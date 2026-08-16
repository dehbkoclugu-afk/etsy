from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import httpx
import pytest

from pinforge.application.exporter import BundleExporter
from pinforge.data.repository import PinRepository
from pinforge.domain.models import PinStatus, PublishResult, SourceKind
from pinforge.importers.folder import FolderImporter
from pinforge.integrations.http import ApiError, JsonHttpClient
from pinforge.integrations.oauth import create_state_attempt, token_from_payload
from pinforge.integrations.pinterest import PinterestClient
from pinforge.scheduling import SchedulePlanner, SchedulerService
from pinforge.settings import AppSettings, SettingsStore


class SuccessPublisher:
    def publish(self, draft):  # noqa: ANN001
        return PublishResult(
            remote_id=f"remote-{draft.id}",
            remote_url=f"https://www.pinterest.com/pin/remote-{draft.id}",
        )


class AmbiguousPublisher:
    def publish(self, draft):  # noqa: ANN001
        raise ApiError(
            "connection lost after send",
            ambiguous=True,
            code="transport_error",
            provider="pinterest",
        )


def _scheduled_database(
    product_folder: Path, tmp_path: Path, *, template_ids=("mockup_hero",)
) -> tuple[Path, datetime]:
    product = FolderImporter.load(product_folder).products[0]
    result = BundleExporter().export_product(
        product, template_ids, tmp_path / "exports"
    )
    database = tmp_path / "pinforge.db"
    now = datetime(2026, 1, 1, 10, tzinfo=UTC)
    with PinRepository(database) as repository:
        repository.save_product(product)
        repository.save_drafts(result.drafts)
        repository.schedule_many(
            (draft.id, now - timedelta(minutes=1), "board") for draft in result.drafts
        )
    return database, now


def test_daily_quota_is_atomic_across_workers(
    product_folder: Path, tmp_path: Path
) -> None:
    database, now = _scheduled_database(
        product_folder, tmp_path, template_ids=("mockup_hero", "list_stack")
    )
    barrier = Barrier(2)

    def claim(worker: str) -> str | None:
        with PinRepository(database) as repository:
            barrier.wait()
            draft = repository.claim_next(
                now,
                worker_id=worker,
                quota_day="2026-01-01",
                max_daily=1,
            )
            return draft.id if draft else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(claim, ("one", "two")))
    assert sum(value is not None for value in results) == 1


def test_expired_lease_reaches_dead_letter(
    product_folder: Path, tmp_path: Path
) -> None:
    database, now = _scheduled_database(product_folder, tmp_path)
    with PinRepository(database) as repository:
        draft = repository.claim_next(
            now,
            worker_id="worker",
            quota_day="2026-01-01",
            max_daily=1,
            lease_seconds=30,
        )
        assert draft is not None
        assert (
            repository.recover_stale_claims(now + timedelta(seconds=31), max_attempts=1)
            == 1
        )
        recovered = repository.get_draft(draft.id)
        assert recovered.status is PinStatus.DEAD_LETTER
        assert recovered.error_code == "stale_lease"


def test_ambiguous_publish_is_never_automatically_requeued(
    product_folder: Path, tmp_path: Path
) -> None:
    database, now = _scheduled_database(product_folder, tmp_path)
    with PinRepository(database) as repository:
        result = SchedulerService(
            repository,
            AmbiguousPublisher(),
            batch_limit=1,
            clock=lambda: now,
        ).drain_due()
        draft = repository.list_drafts()[0]
        assert result.unknown == 1
        assert draft.status is PinStatus.PUBLISH_UNKNOWN


def test_receipt_recovers_remote_success_after_local_commit_failure(
    product_folder: Path, tmp_path: Path
) -> None:
    database, now = _scheduled_database(product_folder, tmp_path)
    with PinRepository(database) as repository:
        original = repository.mark_published

        def fail_once(*args, **kwargs):  # noqa: ANN002, ANN003
            raise sqlite3.OperationalError("disk temporarily unavailable")

        repository.mark_published = fail_once  # type: ignore[method-assign]
        service = SchedulerService(
            repository,
            SuccessPublisher(),
            batch_limit=1,
            clock=lambda: now,
        )
        with pytest.raises(sqlite3.OperationalError):
            service.drain_due()
        repository.mark_published = original  # type: ignore[method-assign]
        recovered = SchedulerService(
            repository,
            SuccessPublisher(),
            batch_limit=1,
            clock=lambda: now,
        ).drain_due()
        assert recovered.recovered_receipts == 1
        assert repository.list_drafts()[0].status is PinStatus.PUBLISHED


def test_receipt_write_failure_preserves_remote_identity_as_unknown(
    product_folder: Path, tmp_path: Path
) -> None:
    database, now = _scheduled_database(product_folder, tmp_path)
    with PinRepository(database) as repository:
        service = SchedulerService(
            repository,
            SuccessPublisher(),
            batch_limit=1,
            clock=lambda: now,
        )

        def fail_receipt(*args, **kwargs):  # noqa: ANN002, ANN003
            raise OSError("disk full")

        service._write_receipt = fail_receipt  # type: ignore[method-assign]
        result = service.drain_due()
        draft = repository.list_drafts()[0]
        assert result.unknown == 1
        assert draft.status is PinStatus.PUBLISH_UNKNOWN
        assert draft.pinterest_pin_id == f"remote-{draft.id}"


def test_published_draft_cannot_be_reset_by_upsert(
    product_folder: Path, tmp_path: Path
) -> None:
    database, now = _scheduled_database(product_folder, tmp_path)
    with PinRepository(database) as repository:
        claimed = repository.claim_next(
            now,
            worker_id="worker",
            quota_day="2026-01-01",
            max_daily=1,
        )
        assert claimed is not None
        repository.mark_published(claimed.id, "remote", now)
        repository.save_drafts((replace(claimed, status=PinStatus.READY),))
        assert repository.get_draft(claimed.id).status is PinStatus.PUBLISHED


def test_source_kind_and_local_product_activity_are_preserved(
    product_folder: Path, tmp_path: Path
) -> None:
    product = FolderImporter.load(product_folder).products[0]
    etsy = replace(product, id="etsy-1", kind=SourceKind.ETSY_API)
    with PinRepository(tmp_path / "pinforge.db") as repository:
        repository.save_products((product, etsy))
        repository.mark_missing_products_inactive(())
        records = {
            item.id: item for item in repository.list_products(include_inactive=True)
        }
        assert records[product.id].active
        assert not records[etsy.id].active
        assert records[etsy.id].kind is SourceKind.ETSY_API


def test_invalid_settings_are_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"max_daily_pins": "many"}), encoding="utf-8")
    settings = SettingsStore(path).load()
    assert settings == AppSettings()
    assert tuple(tmp_path.glob("settings.json.invalid-*"))


def test_refresh_payload_preserves_rotating_token_and_validates_expiry() -> None:
    token = token_from_payload(
        {"access_token": "new", "expires_in": 3600},
        existing_refresh_token="keep-me",
        now=100,
    )
    assert token.refresh_token == "keep-me"
    with pytest.raises(ValueError, match="expires_in"):
        token_from_payload({"access_token": "bad", "expires_in": -1})


def test_oauth_attempt_expires_and_is_provider_bound() -> None:
    attempt = create_state_attempt(
        "https://example.test/oauth",
        client_id="client",
        redirect_uri="https://app.test/callback",
        scopes=("read",),
        provider="pinterest",
        lifetime_seconds=10,
    )
    with pytest.raises(ValueError, match="sona erdi"):
        attempt.validate(
            provider="pinterest", client_id="client", now=attempt.expires_at + 1
        )
    with pytest.raises(ValueError, match="sağlayıcısı"):
        attempt.validate(provider="etsy", client_id="client")


def test_json_limit_and_repeating_bookmark_are_rejected() -> None:
    oversized = JsonHttpClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": "x" * 2000})
            )
        ),
        max_json_bytes=1024,
    )
    with pytest.raises(ApiError, match="boyut"):
        oversized.request("GET", "https://example.test")

    pinterest = PinterestClient(
        "token",
        http=JsonHttpClient(
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, json={"items": [], "bookmark": "same"}
                    )
                )
            )
        ),
    )
    with pytest.raises(ApiError, match="bookmark"):
        pinterest.list_boards()


def test_dst_nonexistent_slot_is_skipped() -> None:
    slots = SchedulePlanner.next_slots(
        datetime(2026, 3, 29, 0),
        1,
        ("02:30",),
        time_zone="Europe/Berlin",
    )
    assert slots == (datetime(2026, 3, 30, 0, 30, tzinfo=UTC),)


def test_export_writes_board_and_queued_utc_status(
    product_folder: Path, tmp_path: Path
) -> None:
    import csv

    product = FolderImporter.load(product_folder).products[0]
    result = BundleExporter().export_product(
        product,
        ("mockup_hero",),
        tmp_path / "out",
        board_id="board-1",
        scheduled_at=datetime(2026, 1, 1, 9),
    )
    with result.csv_path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["board"] == "board-1"
    assert result.drafts[0].status is PinStatus.QUEUED
    assert result.drafts[0].scheduled_at.tzinfo is UTC
