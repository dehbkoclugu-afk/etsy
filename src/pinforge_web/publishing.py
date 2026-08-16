from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from pinforge.domain.models import PinDraft, PinStatus
from pinforge.integrations.http import ApiError
from pinforge.integrations.pinterest.client import PinterestClient
from pinforge_web.jobs import fail_job, finish_job
from pinforge_web.models import (
    Creative,
    CreativeAsset,
    Job,
    Organization,
    PinPublication,
    PinterestBoard,
    ProviderConnection,
)
from pinforge_web.provider_connections import valid_connection_token


def enqueue_pinterest_publication(
    *,
    organization: Organization,
    creative: Creative,
    board: PinterestBoard,
    scheduled_at: datetime | None,
) -> PinPublication:
    if creative.organization_id != organization.id:
        raise ValueError("Creative does not belong to this organization")
    if creative.status != Creative.Status.READY:
        raise ValueError("Creative must be rendered before publishing")
    if board.organization_id != organization.id or not board.active:
        raise ValueError("Pinterest board does not belong to this organization")
    connection = board.connection
    if (
        connection.organization_id != organization.id
        or connection.provider != ProviderConnection.Provider.PINTEREST
        or not connection.active
    ):
        raise ValueError("Pinterest connection is not active")
    due_at = scheduled_at or timezone.now()
    if timezone.is_naive(due_at):
        due_at = timezone.make_aware(due_at)
    with transaction.atomic():
        publication = PinPublication.objects.create(
            organization=organization,
            creative=creative,
            connection=connection,
            board=board,
            scheduled_at=due_at,
        )
        Job.objects.create(
            organization=organization,
            kind=Job.Kind.PUBLISH_PINTEREST,
            payload={"publication_id": str(publication.id)},
            due_at=due_at,
            max_attempts=1,
            deduplication_key=f"pinterest-publish:{publication.id}",
        )
    return publication


def execute_claimed_pinterest_publish(job: Job, *, worker_id: str) -> Job:
    try:
        publication = publish_job(job)
    except ApiError as error:
        _mark_publication_error(
            job,
            status=cast(
                str,
                PinPublication.Status.PUBLISH_UNKNOWN
                if error.ambiguous
                else PinPublication.Status.FAILED,
            ),
            code=error.code or "pinterest_api_error",
            message=str(error),
        )
        return fail_job(
            job_id=job.id,
            worker_id=worker_id,
            error_code=error.code or "pinterest_api_error",
            error_message=str(error),
        )
    except Exception as error:
        _mark_publication_error(
            job,
            status=cast(str, PinPublication.Status.FAILED),
            code="pinterest_publish_failed",
            message=str(error),
        )
        return fail_job(
            job_id=job.id,
            worker_id=worker_id,
            error_code="pinterest_publish_failed",
            error_message=str(error),
        )
    return finish_job(
        job_id=job.id,
        worker_id=worker_id,
        result={
            "publication_id": str(publication.id),
            "remote_pin_id": publication.remote_pin_id,
            "remote_url": publication.remote_url,
        },
    )


def publish_job(job: Job) -> PinPublication:
    if job.kind != Job.Kind.PUBLISH_PINTEREST:
        raise ValueError("Unsupported Pinterest publish job")
    publication = PinPublication.objects.select_related(
        "creative__listing", "connection", "board"
    ).get(
        pk=job.payload.get("publication_id"),
        organization=job.organization,
    )
    if publication.status == PinPublication.Status.PUBLISHED:
        return publication
    asset = CreativeAsset.objects.get(
        organization=job.organization,
        creative=publication.creative,
    )
    PinPublication.objects.filter(pk=publication.pk).update(
        status=PinPublication.Status.PUBLISHING,
        error_code="",
        error_message="",
    )
    token = valid_connection_token(publication.connection)
    with TemporaryDirectory(prefix="pinforge-publish-") as temporary:
        image_path = Path(temporary) / "pin.png"
        with asset.file.open("rb") as source, image_path.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
        draft = PinDraft(
            id=str(publication.id),
            product_id=str(publication.creative.listing_id),
            template_id=publication.creative.template_id,
            title=publication.creative.title,
            description=publication.creative.description,
            alt_text=publication.creative.alt_text,
            destination_url=publication.creative.destination_url,
            board_id=publication.board.external_board_id,
            image_path=image_path,
            scheduled_at=publication.scheduled_at,
            status=PinStatus.PUBLISHING,
            approved_at=publication.creative.approved_at,
            account_id=publication.connection.external_account_id,
        )
        client = PinterestClient(
            token.access_token,
            allowed_destination_hosts=settings.PINTEREST_ALLOWED_DESTINATION_HOSTS,
        )
        try:
            result = client.publish(draft)
        finally:
            client.close()
    publication.status = PinPublication.Status.PUBLISHED
    publication.remote_pin_id = result.remote_id
    publication.remote_url = result.remote_url or ""
    publication.published_at = timezone.now()
    publication.error_code = ""
    publication.error_message = ""
    publication.save(
        update_fields=(
            "status",
            "remote_pin_id",
            "remote_url",
            "published_at",
            "error_code",
            "error_message",
            "updated_at",
        )
    )
    return publication


def _mark_publication_error(job: Job, *, status: str, code: str, message: str) -> None:
    publication_id = job.payload.get("publication_id")
    PinPublication.objects.filter(
        pk=publication_id,
        organization=job.organization,
    ).update(
        status=status,
        error_code=code[:64],
        error_message=" ".join(message.split())[:500],
    )
