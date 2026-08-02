from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
from zoneinfo import ZoneInfo

from pinforge.application.publisher import Publisher
from pinforge.clock import ensure_utc, utc_now
from pinforge.data.repository import PinRepository
from pinforge.domain.models import PinStatus
from pinforge.integrations.http import ApiError


@dataclass(frozen=True, slots=True)
class DrainResult:
    published: int = 0
    retried: int = 0
    failed: int = 0
    unknown: int = 0
    recovered_receipts: int = 0
    daily_limit_reached: bool = False


class SchedulerService:
    def __init__(
        self,
        repository: PinRepository,
        publisher: Publisher,
        *,
        max_daily_pins: int = 10,
        max_attempts: int = 5,
        min_interval_seconds: int = 0,
        batch_limit: int = 3,
        time_zone: str = "Europe/Istanbul",
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], None] = time.sleep,
        receipt_directory: str | Path | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.max_daily_pins = max(1, min(max_daily_pins, 50))
        self.max_attempts = max(1, min(max_attempts, 20))
        self.min_interval_seconds = max(0, min_interval_seconds)
        self.batch_limit = max(1, min(batch_limit, 20))
        self.zone = ZoneInfo(time_zone)
        self.clock = clock
        self.sleep = sleep
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.receipt_directory = Path(
            receipt_directory or repository.path.parent / "publish-receipts"
        )
        self.receipt_directory.mkdir(parents=True, exist_ok=True)

    def drain_due(self) -> DrainResult:
        now = ensure_utc(self.clock())
        recovered = self._recover_receipts()
        self.repository.recover_stale_claims(now, max_attempts=self.max_attempts)
        published = retried = failed = unknown = 0
        quota_day = now.astimezone(self.zone).date().isoformat()
        claimed_any = False
        for index in range(self.batch_limit):
            draft = self.repository.claim_next(
                now,
                worker_id=self.worker_id,
                quota_day=quota_day,
                max_daily=self.max_daily_pins,
            )
            if draft is None:
                break
            claimed_any = True
            try:
                result = self.publisher.publish(draft)
            except ApiError as exc:
                if exc.ambiguous:
                    self.repository.mark_publish_unknown(draft.id, str(exc))
                    unknown += 1
                else:
                    retry_at = None
                    if exc.retryable:
                        delay = exc.retry_after or min(
                            3600, 60 * (2**draft.attempt_count)
                        )
                        retry_at = ensure_utc(self.clock()) + timedelta(seconds=delay)
                    self.repository.mark_failed_attempt(
                        draft.id,
                        str(exc),
                        retry_at=retry_at,
                        retryable=exc.retryable,
                        max_attempts=self.max_attempts,
                        error_code=exc.code
                        or (
                            f"http_{exc.status_code}"
                            if exc.status_code
                            else "api_error"
                        ),
                        provider=exc.provider or "pinterest",
                    )
                    retried += int(exc.retryable and retry_at is not None)
                    failed += int(not exc.retryable)
            except Exception as exc:  # keep one broken draft from stranding the batch
                self.repository.mark_failed_attempt(
                    draft.id,
                    str(exc),
                    retry_at=None,
                    retryable=False,
                    max_attempts=self.max_attempts,
                    error_code=type(exc).__name__,
                    provider="local",
                )
                failed += 1
            else:
                try:
                    receipt = self._write_receipt(
                        draft.id, result.remote_id, result.remote_url
                    )
                except OSError as exc:
                    self.repository.mark_publish_unknown(
                        draft.id,
                        f"Uzak yayın başarılı, yerel makbuz yazılamadı: {exc}",
                        remote_id=result.remote_id,
                        remote_url=result.remote_url,
                    )
                    unknown += 1
                else:
                    self.repository.mark_published(
                        draft.id,
                        result.remote_id,
                        ensure_utc(self.clock()),
                        remote_url=result.remote_url,
                    )
                    receipt.unlink(missing_ok=True)
                    published += 1
            if index + 1 < self.batch_limit and self.min_interval_seconds:
                self.sleep(self.min_interval_seconds)
            now = ensure_utc(self.clock())
        limit_reached = False
        if not claimed_any:
            reserved = self.repository.connection.execute(
                """
                SELECT COUNT(*) FROM pin_drafts
                WHERE quota_day = ? AND status IN (?, ?, ?)
                """,
                (
                    quota_day,
                    PinStatus.PUBLISHING.value,
                    PinStatus.PUBLISHED.value,
                    PinStatus.PUBLISH_UNKNOWN.value,
                ),
            ).fetchone()[0]
            limit_reached = int(reserved) >= self.max_daily_pins
        return DrainResult(
            published=published,
            retried=retried,
            failed=failed,
            unknown=unknown,
            recovered_receipts=recovered,
            daily_limit_reached=limit_reached,
        )

    def _write_receipt(
        self, draft_id: str, remote_id: str, remote_url: str | None
    ) -> Path:
        target = self.receipt_directory / f"{draft_id}.json"
        payload = {
            "draft_id": draft_id,
            "remote_id": remote_id,
            "remote_url": remote_url,
            "received_at": ensure_utc(self.clock()).isoformat(),
        }
        with NamedTemporaryFile(
            "w", dir=self.receipt_directory, encoding="utf-8", delete=False
        ) as temp:
            json.dump(payload, temp)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        os.replace(temp_path, target)
        return target

    def _recover_receipts(self) -> int:
        recovered = 0
        for path in self.receipt_directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                received_at = datetime.fromisoformat(str(payload["received_at"]))
                changed = self.repository.apply_publish_receipt(
                    str(payload["draft_id"]),
                    str(payload["remote_id"]),
                    ensure_utc(received_at),
                    remote_url=payload.get("remote_url"),
                )
                path.unlink(missing_ok=True)
                recovered += int(changed)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return recovered


class SchedulePlanner:
    @staticmethod
    def next_slots(
        start: datetime,
        count: int,
        slot_values: tuple[str, ...],
        *,
        max_daily: int = 10,
        time_zone: str = "Europe/Istanbul",
    ) -> tuple[datetime, ...]:
        if count <= 0:
            return ()
        parsed = sorted({_parse_slot(value) for value in slot_values})
        if not parsed:
            raise ValueError("En az bir geçerli zamanlama slotu gerekli")
        zone = ZoneInfo(time_zone)
        local_start = (
            start.replace(tzinfo=zone)
            if start.tzinfo is None
            else start.astimezone(zone)
        )
        start_utc = local_start.astimezone(UTC)
        daily_limit = min(max(1, max_daily), len(parsed))
        result: list[datetime] = []
        day = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
        while len(result) < count:
            used_today = 0
            for hour, minute in parsed:
                candidate_local = day.replace(hour=hour, minute=minute, fold=0)
                candidate_utc = candidate_local.astimezone(UTC)
                round_trip = candidate_utc.astimezone(zone)
                if (round_trip.hour, round_trip.minute) != (hour, minute):
                    continue
                if candidate_utc >= start_utc and used_today < daily_limit:
                    result.append(candidate_utc)
                    used_today += 1
                    if len(result) == count:
                        break
            day += timedelta(days=1)
        return tuple(result)


def _parse_slot(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Geçersiz zamanlama slotu: {value}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Geçersiz zamanlama slotu: {value}")
    return hour, minute
