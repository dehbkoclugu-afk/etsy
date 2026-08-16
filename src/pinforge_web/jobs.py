from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from pinforge_web.models import Creative, Job, Organization

DEFAULT_LEASE = timedelta(minutes=5)
DEFAULT_RETRY_DELAY = timedelta(minutes=1)
SECRET_PATTERN = re.compile(
    r"(?i)\b(password|token|secret|authorization|api[_-]?key)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)


class JobLeaseError(RuntimeError):
    pass


def enqueue_render(
    *,
    organization: Organization,
    creative: Creative,
    priority: int = 100,
    due_at: datetime | None = None,
) -> Job:
    if creative.organization_id != organization.id:
        raise ValueError("creative organization does not match job organization")
    job, _ = Job.objects.get_or_create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        deduplication_key=f"render:{creative.id}",
        defaults={
            "payload": {"creative_id": str(creative.id)},
            "priority": priority,
            "due_at": due_at or timezone.now(),
        },
    )
    return job


def claim_due_job(
    *,
    worker_id: str,
    now: datetime | None = None,
    lease_for: timedelta = DEFAULT_LEASE,
) -> Job | None:
    normalized_worker = _worker_id(worker_id)
    claimed_at = now or timezone.now()
    eligible = Q(
        status__in=(Job.Status.PENDING, Job.Status.RETRY),
        due_at__lte=claimed_at,
    ) | Q(
        status=Job.Status.RUNNING,
        lease_expires_at__lte=claimed_at,
    )
    with transaction.atomic():
        jobs = Job.objects.select_for_update(
            skip_locked=connection.features.has_select_for_update_skip_locked
        )
        job = jobs.filter(eligible).order_by(
            "priority",
            "due_at",
            "created_at",
            "id",
        ).first()
        if job is None:
            return None
        job.status = Job.Status.RUNNING
        job.attempts += 1
        job.lease_owner = normalized_worker
        job.lease_expires_at = claimed_at + lease_for
        job.started_at = job.started_at or claimed_at
        job.finished_at = None
        job.save(
            update_fields=(
                "status",
                "attempts",
                "lease_owner",
                "lease_expires_at",
                "started_at",
                "finished_at",
                "updated_at",
            )
        )
        return job


def finish_job(
    *,
    job_id: UUID,
    worker_id: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> Job:
    finished_at = now or timezone.now()
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        _require_lease(job, worker_id, finished_at)
        job.status = Job.Status.SUCCEEDED
        job.result = result
        job.error_code = ""
        job.error_message = ""
        job.lease_owner = ""
        job.lease_expires_at = None
        job.finished_at = finished_at
        job.save(
            update_fields=(
                "status",
                "result",
                "error_code",
                "error_message",
                "lease_owner",
                "lease_expires_at",
                "finished_at",
                "updated_at",
            )
        )
        return job


def fail_job(
    *,
    job_id: UUID,
    worker_id: str,
    error_code: str,
    error_message: str,
    now: datetime | None = None,
    retry_delay: timedelta = DEFAULT_RETRY_DELAY,
) -> Job:
    failed_at = now or timezone.now()
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        _require_lease(job, worker_id, failed_at)
        terminal = job.attempts >= job.max_attempts
        job.status = Job.Status.FAILED if terminal else Job.Status.RETRY
        job.due_at = failed_at if terminal else failed_at + retry_delay
        job.error_code = _error_code(error_code)
        job.error_message = _sanitize_error(error_message)
        job.lease_owner = ""
        job.lease_expires_at = None
        job.finished_at = failed_at if terminal else None
        job.save(
            update_fields=(
                "status",
                "due_at",
                "error_code",
                "error_message",
                "lease_owner",
                "lease_expires_at",
                "finished_at",
                "updated_at",
            )
        )
        return job


def _worker_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("worker_id must contain 1 to 128 characters")
    return normalized


def _require_lease(job: Job, worker_id: str, now: datetime) -> None:
    normalized_worker = _worker_id(worker_id)
    if (
        job.status != Job.Status.RUNNING
        or job.lease_owner != normalized_worker
        or job.lease_expires_at is None
        or job.lease_expires_at < now
    ):
        raise JobLeaseError("job lease is not owned by this worker")


def _error_code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return (normalized or "job_failed")[:64]


def _sanitize_error(value: str) -> str:
    redacted = SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return " ".join(redacted.split())[:500]
