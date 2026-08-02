from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pinforge.application.exporter import BundleExporter
from pinforge.data.repository import PinRepository
from pinforge.domain.models import PinStatus, PublishResult
from pinforge.importers.folder import FolderImporter
from pinforge.integrations.http import ApiError
from pinforge.scheduling import SchedulePlanner, SchedulerService


class FakePublisher:
    def __init__(self, error: ApiError | None = None) -> None:
        self.error = error

    def publish(self, draft):  # noqa: ANN001
        if self.error:
            raise self.error
        return PublishResult(remote_id=f"remote-{draft.id}")


def _queued_repository(product_folder: Path, tmp_path: Path) -> PinRepository:
    product = FolderImporter.load(product_folder).products[0]
    exported = BundleExporter().export_product(
        product, ["mockup_hero"], tmp_path / "out"
    )
    repository = PinRepository(tmp_path / "pinforge.db")
    repository.save_product(product)
    repository.save_drafts(exported.drafts)
    repository.schedule(exported.drafts[0].id, datetime(2026, 1, 1, 9), "board")
    return repository


def test_scheduler_claims_and_marks_published(
    product_folder: Path, tmp_path: Path
) -> None:
    repository = _queued_repository(product_folder, tmp_path)
    now = datetime(2026, 1, 1, 10)
    result = SchedulerService(
        repository,
        FakePublisher(),
        min_interval_seconds=0,
        clock=lambda: now,
    ).drain_due()
    draft = repository.list_drafts()[0]
    assert result.published == 1
    assert draft.status is PinStatus.PUBLISHED
    assert draft.pinterest_pin_id.startswith("remote-")
    repository.close()


def test_retryable_failure_returns_to_queue(
    product_folder: Path, tmp_path: Path
) -> None:
    repository = _queued_repository(product_folder, tmp_path)
    now = datetime(2026, 1, 1, 10)
    result = SchedulerService(
        repository,
        FakePublisher(ApiError("rate limited", retryable=True, retry_after=30)),
        min_interval_seconds=0,
        clock=lambda: now,
    ).drain_due()
    draft = repository.list_drafts()[0]
    assert result.retried == 1
    assert draft.status is PinStatus.QUEUED
    assert draft.attempt_count == 1
    assert draft.scheduled_at == datetime(2026, 1, 1, 10, 0, 30, tzinfo=UTC)
    repository.close()


def test_schedule_planner_respects_slots_and_daily_cap() -> None:
    slots = SchedulePlanner.next_slots(
        datetime(2026, 1, 1, 10), 3, ("09:00", "12:00"), max_daily=1
    )
    assert slots == (
        datetime(2026, 1, 1, 9, tzinfo=UTC),
        datetime(2026, 1, 2, 6, tzinfo=UTC),
        datetime(2026, 1, 3, 6, tzinfo=UTC),
    )
