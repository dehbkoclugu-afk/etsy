from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.files import File
from django.core.files.storage import Storage
from django.db import transaction
from django.utils import timezone

from pinforge.domain.models import SourceProduct
from pinforge.integrations.etsy.client import EtsyClient
from pinforge_web.jobs import fail_job, finish_job
from pinforge_web.models import (
    Job,
    Listing,
    ListingImage,
    Organization,
    ProviderConnection,
    Shop,
)
from pinforge_web.provider_connections import valid_connection_token
from pinforge_web.uploads import ValidatedImage, validate_listing_image

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EtsySyncResult:
    shops: int
    listings: int
    issues: tuple[str, ...]


def enqueue_etsy_sync(
    *, organization: Organization, connection: ProviderConnection
) -> Job:
    if (
        connection.organization_id != organization.id
        or connection.provider != ProviderConnection.Provider.ETSY
    ):
        raise ValueError("Etsy connection does not belong to this organization")
    job, _created = Job.objects.get_or_create(
        organization=organization,
        kind=Job.Kind.SYNC_ETSY,
        deduplication_key=f"etsy-sync:{connection.id}",
        defaults={"payload": {"connection_id": str(connection.id)}},
    )
    if job.status in {Job.Status.SUCCEEDED, Job.Status.FAILED}:
        job.status = Job.Status.PENDING
        job.due_at = timezone.now()
        job.attempts = 0
        job.finished_at = None
        job.error_code = ""
        job.error_message = ""
        job.save(
            update_fields=(
                "status",
                "due_at",
                "attempts",
                "finished_at",
                "error_code",
                "error_message",
                "updated_at",
            )
        )
    return job


def execute_claimed_etsy_sync(job: Job, *, worker_id: str) -> Job:
    try:
        result = sync_etsy_job(job)
    except Exception as error:
        return fail_job(
            job_id=job.id,
            worker_id=worker_id,
            error_code="etsy_sync_failed",
            error_message=str(error),
        )
    return finish_job(
        job_id=job.id,
        worker_id=worker_id,
        result={
            "shops": result.shops,
            "listings": result.listings,
            "issues": list(result.issues),
        },
    )


def sync_etsy_job(job: Job) -> EtsySyncResult:
    if job.kind != Job.Kind.SYNC_ETSY:
        raise ValueError("Unsupported Etsy sync job")
    connection_id = job.payload.get("connection_id")
    connection = ProviderConnection.objects.get(
        pk=connection_id,
        organization=job.organization,
        provider=ProviderConnection.Provider.ETSY,
        active=True,
    )
    return sync_etsy_connection(connection)


def sync_etsy_connection(connection: ProviderConnection) -> EtsySyncResult:
    if not settings.ETSY_KEYSTRING or not settings.ETSY_SHARED_SECRET:
        raise ValueError("Etsy API credentials are not configured")
    token = valid_connection_token(connection)
    client = EtsyClient(
        settings.ETSY_KEYSTRING,
        settings.ETSY_SHARED_SECRET,
        token.access_token,
    )
    imported = 0
    issues: list[str] = []
    try:
        shops = client.list_owned_shops(connection.external_account_id)
        with TemporaryDirectory(prefix="pinforge-etsy-sync-") as temporary:
            for raw_shop in shops:
                shop_id = str(raw_shop.get("shop_id", ""))
                if not shop_id.isdigit():
                    issues.append("Etsy returned a shop without a numeric ID")
                    continue
                shop_name = str(raw_shop.get("shop_name") or f"Etsy {shop_id}")[:120]
                shop = _upsert_shop(connection, shop_id=shop_id, name=shop_name)
                try:
                    products = client.import_shop(
                        shop_id,
                        Path(temporary) / shop_id,
                    )
                except Exception as error:
                    issues.append(f"{shop_id}: {error}")
                    continue
                seen: set[str] = set()
                for product in products:
                    _upsert_product(shop, product)
                    seen.add(product.id)
                    imported += 1
                issues.extend(f"{shop_id}/{issue}" for issue in client.last_issues)
                if not client.last_issues:
                    Listing.objects.filter(
                        organization=connection.organization,
                        shop=shop,
                    ).exclude(source_listing_id__in=seen).update(active=False)
    finally:
        client.close()
    if not shops:
        raise ValueError("No Etsy shop was found for this account")
    return EtsySyncResult(shops=len(shops), listings=imported, issues=tuple(issues))


def _upsert_shop(connection: ProviderConnection, *, shop_id: str, name: str) -> Shop:
    shop, _created = Shop.objects.update_or_create(
        organization=connection.organization,
        source=Shop.Source.ETSY,
        source_shop_id=shop_id,
        defaults={"connection": connection, "name": name, "active": True},
    )
    return shop


def _upsert_product(shop: Shop, product: SourceProduct) -> Listing:
    validated = _validate_product_images(product)
    stored: list[tuple[Storage, str]] = []
    old_files: list[tuple[Storage, str]] = []
    try:
        with transaction.atomic():
            listing, _created = Listing.objects.update_or_create(
                organization=shop.organization,
                shop=shop,
                source_listing_id=product.id,
                defaults={
                    "title": product.title[:500],
                    "listing_url": product.listing_url,
                    "price_minor": max(0, round(product.price * 100)),
                    "currency": product.currency[:3].upper(),
                    "tags": [value[:20] for value in product.tags[:13]],
                    "description": product.description[:20_000],
                    "vertical": product.vertical[:64],
                    "active": True,
                    "synchronized_at": timezone.now(),
                },
            )
            previous = list(ListingImage.objects.filter(listing=listing))
            old_files.extend(
                (image.file.storage, image.file.name) for image in previous
            )
            ListingImage.objects.filter(listing=listing).delete()
            for position, (path, metadata) in enumerate(validated):
                image = ListingImage(
                    organization=shop.organization,
                    listing=listing,
                    position=position,
                    mime_type=metadata.mime_type,
                    byte_size=metadata.byte_size,
                    width=metadata.width,
                    height=metadata.height,
                    checksum=metadata.checksum,
                )
                with path.open("rb") as source:
                    image.file.save(
                        f"original{metadata.extension}",
                        File(source),
                        save=False,
                    )
                stored.append((image.file.storage, image.file.name))
                image.save()
    except Exception:
        _delete_files(stored)
        raise
    transaction.on_commit(lambda: _delete_files(old_files))
    return listing


def _validate_product_images(
    product: SourceProduct,
) -> tuple[tuple[Path, ValidatedImage], ...]:
    validated: list[tuple[Path, ValidatedImage]] = []
    for path in product.image_paths[:5]:
        with path.open("rb") as source:
            validated.append((path, validate_listing_image(source)))
    if not validated:
        raise ValueError("Etsy listing has no valid images")
    return tuple(validated)


def _delete_files(files: list[tuple[Storage, str]]) -> None:
    for storage, name in reversed(files):
        try:
            storage.delete(name)
        except Exception:
            logger.exception("etsy_sync_storage_cleanup_failed")
