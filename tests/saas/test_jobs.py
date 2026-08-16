from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from pinforge_web.jobs import (
    JobLeaseError,
    claim_due_job,
    enqueue_render,
    fail_job,
    finish_job,
)
from pinforge_web.models import Creative, Job, Listing, Organization, Shop


def create_creative(organization: Organization) -> Creative:
    shop = Shop.objects.create(
        organization=organization,
        source=Shop.Source.MANUAL,
        source_shop_id="manual",
        name="Catalog",
    )
    listing = Listing.objects.create(
        organization=organization,
        shop=shop,
        source_listing_id="manual:one",
        title="Welcome book",
        listing_url="https://www.etsy.com/listing/123/example",
        price_minor=1299,
        currency="USD",
    )
    return Creative.objects.create(
        organization=organization,
        listing=listing,
        template_id=Creative.Template.TEXT_OVERLAY,
        title="Welcome book",
        description="Useful download",
        alt_text="Welcome book preview",
        destination_url=listing.listing_url,
    )


@pytest.mark.django_db
def test_enqueue_render_is_deduplicated_and_tenant_checked() -> None:
    organization = Organization.objects.create(name="Example Shop")
    other = Organization.objects.create(name="Other Shop")
    creative = create_creative(organization)

    first = enqueue_render(organization=organization, creative=creative)
    second = enqueue_render(organization=organization, creative=creative)

    assert first == second
    assert first.payload == {"creative_id": str(creative.id)}
    assert first.deduplication_key == f"render:{creative.id}"
    assert Job.objects.count() == 1
    with pytest.raises(ValueError, match="organization"):
        enqueue_render(organization=other, creative=creative)


@pytest.mark.django_db
def test_claim_uses_priority_then_due_time_and_does_not_double_claim() -> None:
    organization = Organization.objects.create(name="Example Shop")
    now = timezone.now()
    low_priority = Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": "low"},
        deduplication_key="render:low",
        priority=100,
        due_at=now - timedelta(minutes=2),
    )
    high_priority = Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": "high"},
        deduplication_key="render:high",
        priority=10,
        due_at=now - timedelta(minutes=1),
    )

    first = claim_due_job(worker_id="worker-a", now=now)
    second = claim_due_job(worker_id="worker-b", now=now)

    assert first is not None and first.id == high_priority.id
    assert first.status == Job.Status.RUNNING
    assert first.attempts == 1
    assert first.lease_owner == "worker-a"
    assert first.lease_expires_at == now + timedelta(minutes=5)
    assert second is not None and second.id == low_priority.id
    assert second.lease_owner == "worker-b"


@pytest.mark.django_db
def test_claim_recovers_an_expired_lease() -> None:
    organization = Organization.objects.create(name="Example Shop")
    now = timezone.now()
    job = Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": "one"},
        deduplication_key="render:one",
        status=Job.Status.RUNNING,
        attempts=1,
        lease_owner="dead-worker",
        lease_expires_at=now - timedelta(seconds=1),
    )

    claimed = claim_due_job(worker_id="recovery-worker", now=now)

    assert claimed is not None and claimed.id == job.id
    assert claimed.attempts == 2
    assert claimed.lease_owner == "recovery-worker"


@pytest.mark.django_db
def test_finish_requires_matching_live_lease() -> None:
    organization = Organization.objects.create(name="Example Shop")
    job = Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": "one"},
        deduplication_key="render:one",
    )
    claimed = claim_due_job(worker_id="worker-a")
    assert claimed is not None

    with pytest.raises(JobLeaseError, match="lease"):
        finish_job(job_id=job.id, worker_id="worker-b", result={"ok": True})

    finished = finish_job(
        job_id=job.id,
        worker_id="worker-a",
        result={"creative_id": "one"},
    )
    assert finished.status == Job.Status.SUCCEEDED
    assert finished.result == {"creative_id": "one"}
    assert finished.lease_owner == ""
    assert finished.lease_expires_at is None
    assert finished.finished_at is not None


@pytest.mark.django_db
def test_failure_retries_then_becomes_terminal_and_redacts_secrets() -> None:
    organization = Organization.objects.create(name="Example Shop")
    job = Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": "one"},
        deduplication_key="render:one",
        max_attempts=2,
    )
    now = timezone.now()
    claimed = claim_due_job(worker_id="worker-a", now=now)
    assert claimed is not None

    retry = fail_job(
        job_id=job.id,
        worker_id="worker-a",
        error_code="render_error",
        error_message="token=super-secret password: hunter2",
        now=now,
        retry_delay=timedelta(seconds=30),
    )
    assert retry.status == Job.Status.RETRY
    assert retry.due_at == now + timedelta(seconds=30)
    assert "super-secret" not in retry.error_message
    assert "hunter2" not in retry.error_message

    claimed_again = claim_due_job(
        worker_id="worker-b",
        now=now + timedelta(seconds=31),
    )
    assert claimed_again is not None
    terminal = fail_job(
        job_id=job.id,
        worker_id="worker-b",
        error_code="render_error",
        error_message="still failed",
        now=now + timedelta(seconds=31),
    )
    assert terminal.status == Job.Status.FAILED
    assert terminal.finished_at == now + timedelta(seconds=31)


@pytest.mark.django_db
def test_future_job_is_not_claimed() -> None:
    organization = Organization.objects.create(name="Example Shop")
    now = timezone.now()
    Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": "future"},
        deduplication_key="render:future",
        due_at=now + timedelta(minutes=1),
    )

    assert claim_due_job(worker_id="worker", now=now) is None
