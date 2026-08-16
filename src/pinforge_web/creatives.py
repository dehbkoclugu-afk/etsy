from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from pinforge.application.generator import with_utm
from pinforge.domain.models import PinCopy
from pinforge.rendering.templates import TEMPLATES
from pinforge_web.jobs import enqueue_render
from pinforge_web.models import (
    BrandKit,
    Creative,
    CreativeAsset,
    Job,
    Listing,
    Organization,
)

TEMPLATE_MAP = {template.id: template for template in TEMPLATES}


class CreativeValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreativeQueueResult:
    creative: Creative
    job: Job
    created: bool


def create_or_enqueue_creative(
    *,
    organization: Organization,
    listing: Listing,
    template_id: str,
    title: str,
    description: str,
    alt_text: str,
) -> CreativeQueueResult:
    if listing.organization_id != organization.id:
        raise CreativeValidationError(
            "listing organization does not match active organization"
        )
    try:
        template = TEMPLATE_MAP[template_id]
    except KeyError as error:
        raise CreativeValidationError("Select a supported template.") from error
    image_count = listing.images.filter(organization=organization).count()
    if image_count < template.required_images:
        raise CreativeValidationError(
            f"{template.display_name} requires at least "
            f"{template.required_images} images."
        )
    try:
        copy = PinCopy(
            title=title.strip(),
            description=description.strip(),
            alt_text=alt_text.strip(),
        )
    except ValueError as error:
        raise CreativeValidationError(str(error)) from error
    destination_url = with_utm(listing.listing_url, template_id, listing.vertical)
    revision = _input_revision(
        organization=organization,
        listing=listing,
        template_id=template_id,
        copy=copy,
    )

    with transaction.atomic():
        creative, created = Creative.objects.get_or_create(
            organization=organization,
            listing=listing,
            input_revision=revision,
            defaults={
                "template_id": template_id,
                "title": copy.title,
                "description": copy.description,
                "alt_text": copy.alt_text,
                "destination_url": destination_url,
                "status": Creative.Status.QUEUED,
                "approved_at": timezone.now(),
            },
        )
        job = enqueue_render(organization=organization, creative=creative)
        if not created and _needs_requeue(creative, job):
            _reset_for_retry(creative, job)
    return CreativeQueueResult(creative=creative, job=job, created=created)


def _input_revision(
    *,
    organization: Organization,
    listing: Listing,
    template_id: str,
    copy: PinCopy,
) -> str:
    brand = BrandKit.objects.filter(organization=organization).first()
    image_checksums = list(
        listing.images.filter(organization=organization)
        .order_by("position", "id")
        .values_list("checksum", flat=True)
    )
    payload = {
        "listing_id": str(listing.id),
        "listing_updated_at": listing.updated_at.isoformat(),
        "image_checksums": image_checksums,
        "brand_updated_at": brand.updated_at.isoformat() if brand else None,
        "template_id": template_id,
        "title": copy.title,
        "description": copy.description,
        "alt_text": copy.alt_text,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _needs_requeue(creative: Creative, job: Job) -> bool:
    if job.status == Job.Status.FAILED or creative.status == Creative.Status.FAILED:
        return True
    if job.status != Job.Status.SUCCEEDED:
        return False
    asset = CreativeAsset.objects.filter(
        organization=creative.organization,
        creative=creative,
    ).first()
    return asset is None or not asset.file.storage.exists(asset.file.name)


def _reset_for_retry(creative: Creative, job: Job) -> None:
    now = timezone.now()
    job.status = Job.Status.PENDING
    job.due_at = now
    job.lease_owner = ""
    job.lease_expires_at = None
    job.attempts = 0
    job.result = {}
    job.error_code = ""
    job.error_message = ""
    job.started_at = None
    job.finished_at = None
    job.save(
        update_fields=(
            "status",
            "due_at",
            "lease_owner",
            "lease_expires_at",
            "attempts",
            "result",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "updated_at",
        )
    )
    creative.status = Creative.Status.QUEUED
    creative.error_code = ""
    creative.save(update_fields=("status", "error_code", "updated_at"))
