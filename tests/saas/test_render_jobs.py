from __future__ import annotations

import hashlib
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from PIL import Image

from pinforge_web.catalog import create_manual_listing
from pinforge_web.jobs import claim_due_job, enqueue_render
from pinforge_web.models import (
    BrandKit,
    Creative,
    CreativeAsset,
    Job,
    Organization,
)
from pinforge_web.rendering import execute_claimed_job, render_creative_job


def image_upload(name: str = "product.png") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (800, 1200), "#D8C6A1").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def private_storage_settings(root: Path) -> dict[str, dict[str, object]]:
    return {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": root, "base_url": None},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


def queued_creative(organization: Organization) -> tuple[Creative, Job]:
    catalog = create_manual_listing(
        organization=organization,
        title="Botanical welcome book",
        listing_url="https://www.etsy.com/listing/123/example",
        price="12.99",
        currency="USD",
        tags=["airbnb", "welcome book"],
        description="A polished guest welcome book.",
        vertical="hospitality",
        images=[image_upload()],
    )
    BrandKit.objects.create(
        organization=organization,
        shop_name="ECOVIA",
        primary="#173C35",
        accent="#C9855B",
        surface="#F4F0E6",
        ink="#18201D",
    )
    creative = Creative.objects.create(
        organization=organization,
        listing=catalog.listing,
        template_id=Creative.Template.TEXT_OVERLAY,
        title="A better guest welcome",
        description="Create a polished first impression.",
        alt_text="Botanical guest welcome book preview",
        destination_url=catalog.listing.listing_url,
    )
    return creative, enqueue_render(organization=organization, creative=creative)


@pytest.mark.django_db
def test_claimed_render_job_creates_valid_private_pin_asset(tmp_path: Path) -> None:
    organization = Organization.objects.create(name="Example Shop")
    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        creative, queued = queued_creative(organization)
        claimed = claim_due_job(worker_id="worker-a")
        assert claimed is not None and claimed.id == queued.id

        completed = execute_claimed_job(claimed, worker_id="worker-a")

        creative.refresh_from_db()
        asset = CreativeAsset.objects.get(creative=creative)
        rendered = Path(tmp_path, asset.file.name)
        assert completed.status == Job.Status.SUCCEEDED
        assert creative.status == Creative.Status.READY
        assert rendered.is_file()
        assert asset.organization == organization
        assert asset.mime_type == "image/png"
        assert (asset.width, asset.height) == (1000, 1500)
        assert asset.byte_size == rendered.stat().st_size
        assert asset.checksum == hashlib.sha256(rendered.read_bytes()).hexdigest()
        with Image.open(rendered) as image:
            assert image.format == "PNG"
            assert image.size == (1000, 1500)


@pytest.mark.django_db
def test_render_retry_reuses_asset_created_before_job_completion(
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")
    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        creative, _ = queued_creative(organization)
        claimed = claim_due_job(worker_id="worker-a")
        assert claimed is not None
        first_asset = render_creative_job(claimed)

        second_asset = render_creative_job(claimed)

    assert first_asset.id == second_asset.id
    assert CreativeAsset.objects.filter(creative=creative).count() == 1


@pytest.mark.django_db
def test_invalid_job_payload_fails_without_leaking_details(tmp_path: Path) -> None:
    organization = Organization.objects.create(name="Example Shop")
    Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": "token=super-secret"},
        deduplication_key="render:invalid",
        max_attempts=1,
    )
    claimed = claim_due_job(worker_id="worker-a")
    assert claimed is not None

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        completed = execute_claimed_job(claimed, worker_id="worker-a")

    assert completed.status == Job.Status.FAILED
    assert completed.error_code == "invalid_job"
    assert "super-secret" not in completed.error_message
    assert not CreativeAsset.objects.exists()


@pytest.mark.django_db
def test_render_failure_is_retryable_and_updates_creative_status(
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")
    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        creative, _ = queued_creative(organization)
        creative.template_id = Creative.Template.SPLIT_COMPARE
        creative.save(update_fields=("template_id", "updated_at"))
        claimed = claim_due_job(worker_id="worker-a")
        assert claimed is not None

        completed = execute_claimed_job(claimed, worker_id="worker-a")

    creative.refresh_from_db()
    assert completed.status == Job.Status.RETRY
    assert completed.error_code == "render_error"
    assert creative.status == Creative.Status.QUEUED
    assert not CreativeAsset.objects.filter(creative=creative).exists()


@pytest.mark.django_db
def test_run_jobs_once_management_command_processes_one_job(tmp_path: Path) -> None:
    organization = Organization.objects.create(name="Example Shop")
    output = StringIO()
    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        creative, job = queued_creative(organization)

        call_command("run_jobs", "--once", "--worker-id", "test-worker", stdout=output)

    job.refresh_from_db()
    creative.refresh_from_db()
    assert job.status == Job.Status.SUCCEEDED
    assert creative.status == Creative.Status.READY
    assert "processed 1 job" in output.getvalue()
