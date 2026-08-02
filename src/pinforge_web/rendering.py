from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from django.core.files import File
from django.db import transaction

from pinforge.application.generator import DEFAULT_BULLETS
from pinforge.domain.models import (
    BrandKit as DomainBrandKit,
    PinCopy,
    SourceKind,
    SourceProduct,
)
from pinforge.rendering.engine import RenderEngine, RenderError
from pinforge.rendering.fonts import FontError
from pinforge_web.jobs import fail_job, finish_job
from pinforge_web.models import (
    BrandKit,
    Creative,
    CreativeAsset,
    Job,
    ListingImage,
)
from pinforge_web.uploads import (
    MAX_IMAGE_BYTES,
    UploadValidationError,
    ValidatedImage,
    validate_listing_image,
)

logger = logging.getLogger(__name__)
ALLOWED_FONTS = {
    "DejaVuSerif-Bold.ttf",
    "DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf",
}


class InvalidRenderJob(ValueError):
    pass


def execute_claimed_job(
    job: Job,
    *,
    worker_id: str,
    engine: RenderEngine | None = None,
) -> Job:
    try:
        asset = render_creative_job(job, engine=engine)
    except Exception as error:
        error_code = _error_code_for(error)
        failed = fail_job(
            job_id=job.id,
            worker_id=worker_id,
            error_code=error_code,
            error_message=str(error),
        )
        _update_creative_after_failure(job, failed, error_code)
        return failed
    return finish_job(
        job_id=job.id,
        worker_id=worker_id,
        result={
            "creative_id": str(asset.creative_id),
            "asset_id": str(asset.id),
        },
    )


def render_creative_job(
    job: Job,
    *,
    engine: RenderEngine | None = None,
) -> CreativeAsset:
    creative = _creative_for_job(job)
    existing = CreativeAsset.objects.filter(
        organization=job.organization,
        creative=creative,
    ).first()
    if existing is not None and existing.file.storage.exists(existing.file.name):
        _mark_creative_ready(creative)
        return existing
    if existing is not None:
        existing.delete()

    Creative.objects.filter(
        pk=creative.pk,
        organization=job.organization,
    ).update(status=Creative.Status.RENDERING, error_code="")
    creative.status = Creative.Status.RENDERING
    creative.error_code = ""

    with TemporaryDirectory(prefix="pinforge-render-") as temporary:
        root = Path(temporary)
        source_paths = _copy_listing_images(creative, root)
        product = _source_product(creative, source_paths)
        brand = _domain_brand(job, creative)
        copy = PinCopy(
            title=creative.title,
            description=creative.description,
            alt_text=creative.alt_text,
            bullets=(
                DEFAULT_BULLETS
                if creative.template_id == Creative.Template.LIST_STACK
                else ()
            ),
        )
        output = root / "render.png"
        (engine or RenderEngine()).render_to(
            output,
            creative.template_id,
            product,
            copy,
            brand,
            overwrite=True,
        )
        with output.open("rb") as rendered:
            metadata = validate_listing_image(rendered)
        if (metadata.width, metadata.height) != (1000, 1500):
            raise RenderError("Rendered asset must be exactly 1000x1500 pixels")
        return _store_asset(job, creative, output, metadata)


def _creative_for_job(job: Job) -> Creative:
    if job.kind != Job.Kind.RENDER_CREATIVE:
        raise InvalidRenderJob("Unsupported job kind")
    raw_id = job.payload.get("creative_id")
    try:
        creative_id = UUID(str(raw_id))
    except (TypeError, ValueError, AttributeError) as error:
        raise InvalidRenderJob("Job contains an invalid creative ID") from error
    try:
        creative = Creative.objects.select_related("listing").get(
            pk=creative_id,
            organization=job.organization,
        )
    except Creative.DoesNotExist as error:
        raise InvalidRenderJob("Creative does not belong to this organization") from error
    if creative.listing.organization_id != job.organization_id:
        raise InvalidRenderJob("Listing does not belong to this organization")
    return creative


def _copy_listing_images(creative: Creative, root: Path) -> tuple[Path, ...]:
    records = ListingImage.objects.filter(
        organization=creative.organization,
        listing=creative.listing,
    ).order_by("position", "id")
    copied: list[Path] = []
    for record in records:
        extension = ".png" if record.mime_type == "image/png" else ".jpg"
        target = root / f"source-{record.position}{extension}"
        checksum = hashlib.sha256()
        copied_bytes = 0
        with record.file.open("rb") as source, target.open("wb") as destination:
            while chunk := source.read(1024 * 1024):
                copied_bytes += len(chunk)
                if copied_bytes > MAX_IMAGE_BYTES:
                    raise UploadValidationError("Stored listing image is too large.")
                checksum.update(chunk)
                destination.write(chunk)
        if copied_bytes != record.byte_size:
            raise UploadValidationError("Stored listing image size mismatch.")
        if checksum.hexdigest() != record.checksum:
            raise UploadValidationError("Stored listing image checksum mismatch.")
        with target.open("rb") as copied_image:
            metadata = validate_listing_image(copied_image)
        if (
            metadata.mime_type != record.mime_type
            or metadata.width != record.width
            or metadata.height != record.height
        ):
            raise UploadValidationError("Stored listing image metadata mismatch.")
        copied.append(target)
    if not copied:
        raise RenderError("Listing has no renderable images")
    return tuple(copied)


def _source_product(
    creative: Creative,
    source_paths: tuple[Path, ...],
) -> SourceProduct:
    listing = creative.listing
    tags = (
        tuple(value for value in listing.tags if isinstance(value, str))
        if isinstance(listing.tags, list)
        else ()
    )
    return SourceProduct(
        id=str(listing.id),
        title=listing.title,
        listing_url=listing.listing_url,
        price=listing.price_minor / 100,
        currency=listing.currency,
        vertical=listing.vertical,
        image_paths=source_paths,
        tags=tags,
        description=listing.description,
        kind=SourceKind.MANUAL,
        imported_at=listing.created_at,
        active=listing.active,
    )


def _domain_brand(job: Job, creative: Creative) -> DomainBrandKit:
    brand = BrandKit.objects.filter(organization=job.organization).first()
    if brand is None:
        return DomainBrandKit(shop_name=creative.listing.shop.name)
    fonts = {brand.headline_font, brand.body_font, brand.body_bold_font}
    if not fonts <= ALLOWED_FONTS:
        raise RenderError("Brand kit contains an unsupported font")
    return DomainBrandKit(
        shop_name=brand.shop_name,
        primary=brand.primary,
        accent=brand.accent,
        surface=brand.surface,
        ink=brand.ink,
        headline_font=brand.headline_font,
        body_font=brand.body_font,
        body_bold_font=brand.body_bold_font,
    )


def _store_asset(
    job: Job,
    creative: Creative,
    output: Path,
    metadata: ValidatedImage,
) -> CreativeAsset:
    asset = CreativeAsset(
        organization=job.organization,
        creative=creative,
        mime_type=metadata.mime_type,
        byte_size=metadata.byte_size,
        width=metadata.width,
        height=metadata.height,
        checksum=metadata.checksum,
    )
    stored_name = ""
    try:
        with output.open("rb") as rendered:
            asset.file.save("render.png", File(rendered), save=False)
        stored_name = asset.file.name
        with transaction.atomic():
            asset.save()
            Creative.objects.filter(
                pk=creative.pk,
                organization=job.organization,
            ).update(status=Creative.Status.READY, error_code="")
    except Exception:
        if stored_name:
            _delete_file(asset, stored_name, job)
        raise
    creative.status = Creative.Status.READY
    creative.error_code = ""
    return asset


def _mark_creative_ready(creative: Creative) -> None:
    if creative.status != Creative.Status.READY or creative.error_code:
        Creative.objects.filter(pk=creative.pk).update(
            status=Creative.Status.READY,
            error_code="",
        )
        creative.status = Creative.Status.READY
        creative.error_code = ""


def _delete_file(asset: CreativeAsset, name: str, job: Job) -> None:
    try:
        asset.file.storage.delete(name)
    except Exception:
        logger.exception(
            "creative_storage_cleanup_failed",
            extra={
                "organization_id": str(job.organization_id),
                "job_id": str(job.id),
            },
        )


def _error_code_for(error: Exception) -> str:
    if isinstance(error, InvalidRenderJob):
        return "invalid_job"
    if isinstance(error, (RenderError, FontError, UploadValidationError)):
        return "render_error"
    if isinstance(error, OSError):
        return "storage_error"
    return "internal_error"


def _update_creative_after_failure(
    job: Job,
    failed: Job,
    error_code: str,
) -> None:
    raw_id = job.payload.get("creative_id")
    try:
        creative_id = UUID(str(raw_id))
    except (TypeError, ValueError, AttributeError):
        return
    status = (
        Creative.Status.FAILED
        if failed.status == Job.Status.FAILED
        else Creative.Status.QUEUED
    )
    Creative.objects.filter(
        pk=creative_id,
        organization=job.organization,
    ).update(status=status, error_code=error_code)
