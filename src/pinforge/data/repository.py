from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pinforge.clock import from_storage, to_storage, utc_now
from pinforge.domain.models import PinDraft, PinStatus, SourceKind, SourceProduct
from pinforge.growth.models import MetricSnapshot, TrendTerm


class PinRepository:
    SCHEMA_VERSION = 4
    TERMINAL_STATUSES = (
        PinStatus.PUBLISHED.value,
        PinStatus.PUBLISH_UNKNOWN.value,
    )

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 15000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self._migrate()
        if os.name != "nt":
            self.path.chmod(0o600)
        result = self.connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Veritabanı bütünlük kontrolü başarısız: {result}")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PinRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > self.SCHEMA_VERSION:
            raise RuntimeError(f"Veritabanı sürümü desteklenmiyor: {version}")
        if version == 0:
            tables = {
                row[0]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables:
                raise RuntimeError(
                    "Sürümü olmayan kısmi veritabanı bulundu; dosyayı yedekleyip yeniden oluşturun"
                )
            self.connection.executescript(_SCHEMA_V4)
            return
        self._backup_before_migration(version)
        if version == 1:
            self.connection.executescript(
                """
                ALTER TABLE pin_drafts ADD COLUMN board_id TEXT;
                ALTER TABLE pin_drafts ADD COLUMN pinterest_pin_id TEXT;
                ALTER TABLE pin_drafts ADD COLUMN published_at TEXT;
                ALTER TABLE pin_drafts ADD COLUMN claimed_at TEXT;
                PRAGMA user_version = 2;
                """
            )
            version = 2
        if version == 2:
            self._migrate_v2_to_v3()
            version = 3
        if version == 3:
            self.connection.executescript(_MIGRATE_V3_TO_V4)

    def _backup_before_migration(self, version: int) -> None:
        backup_path = self.path.with_name(f"{self.path.name}.v{version}.bak")
        if backup_path.exists():
            return
        destination = sqlite3.connect(backup_path)
        try:
            self.connection.backup(destination)
        finally:
            destination.close()
        if os.name != "nt":
            backup_path.chmod(0o600)

    def _migrate_v2_to_v3(self) -> None:
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.executescript(_MIGRATE_V2_TO_V3)
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    def save_product(self, product: SourceProduct) -> None:
        self.save_products((product,))

    def save_products(self, products: Iterable[SourceProduct]) -> None:
        values = [_product_values(product) for product in products]
        if not values:
            return
        with self.connection:
            self.connection.executemany(_UPSERT_PRODUCT, values)

    def mark_missing_products_inactive(self, active_ids: Iterable[str]) -> int:
        ids = tuple(dict.fromkeys(active_ids))
        with self.connection:
            if not ids:
                cursor = self.connection.execute(
                    "UPDATE products SET active = 0 WHERE source_kind = ?",
                    (SourceKind.ETSY_API.value,),
                )
            else:
                placeholders = ",".join("?" for _ in ids)
                cursor = self.connection.execute(
                    f"UPDATE products SET active = 0 WHERE source_kind = ? AND id NOT IN ({placeholders})",
                    (SourceKind.ETSY_API.value, *ids),
                )
            return cursor.rowcount

    def list_products(
        self, *, include_inactive: bool = False, limit: int = 1000, offset: int = 0
    ) -> tuple[SourceProduct, ...]:
        query = "SELECT * FROM products"
        parameters: list[object] = []
        if not include_inactive:
            query += " WHERE active = 1"
        query += " ORDER BY imported_at DESC, rowid DESC LIMIT ? OFFSET ?"
        parameters.extend((max(1, min(limit, 5000)), max(0, offset)))
        rows = self.connection.execute(query, parameters).fetchall()
        products: list[SourceProduct] = []
        for row in rows:
            try:
                products.append(_product_from_row(row))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.audit(
                    "product_corrupt",
                    entity_type="product",
                    entity_id=str(row["id"]),
                    details={"error": str(exc)},
                )
        return tuple(products)

    def save_drafts(self, drafts: Iterable[PinDraft]) -> None:
        values = [_draft_values(draft) for draft in drafts]
        if not values:
            return
        with self.connection:
            self.connection.executemany(_UPSERT_DRAFT, values)

    def get_draft(self, draft_id: str) -> PinDraft:
        row = self.connection.execute(
            "SELECT * FROM pin_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Taslak bulunamadı: {draft_id}")
        return _draft_from_row(row)

    def set_status(
        self, draft_id: str, status: PinStatus, *, scheduled_at: datetime | None = None
    ) -> None:
        current = self.get_draft(draft_id)
        allowed = {
            PinStatus.DRAFT: {PinStatus.READY},
            PinStatus.FAILED: {PinStatus.DEAD_LETTER},
        }
        if status not in allowed.get(current.status, set()):
            raise ValueError(f"Geçersiz durum geçişi: {current.status} -> {status}")
        with self.connection:
            self.connection.execute(
                "UPDATE pin_drafts SET status = ?, scheduled_at = COALESCE(?, scheduled_at) WHERE id = ?",
                (status.value, to_storage(scheduled_at), draft_id),
            )

    def schedule(self, draft_id: str, scheduled_at: datetime, board_id: str) -> None:
        self.schedule_many(((draft_id, scheduled_at, board_id),))

    def schedule_many(self, values: Iterable[tuple[str, datetime, str]]) -> None:
        items = tuple(values)
        if any(not board_id.strip() for _, _, board_id in items):
            raise ValueError("Pinterest board seçilmeli")
        with self.connection:
            for draft_id, scheduled_at, board_id in items:
                cursor = self.connection.execute(
                    """
                    UPDATE pin_drafts
                    SET status = ?, scheduled_at = ?, board_id = ?, last_error = NULL,
                        error_code = NULL, error_provider = NULL,
                        attempt_count = CASE WHEN status IN (?, ?) THEN 0 ELSE attempt_count END,
                        quota_day = NULL, lease_owner = NULL, lease_expires_at = NULL
                    WHERE id = ? AND status IN (?, ?, ?, ?)
                    """,
                    (
                        PinStatus.QUEUED.value,
                        to_storage(scheduled_at),
                        board_id,
                        PinStatus.FAILED.value,
                        PinStatus.DEAD_LETTER.value,
                        draft_id,
                        PinStatus.DRAFT.value,
                        PinStatus.READY.value,
                        PinStatus.FAILED.value,
                        PinStatus.DEAD_LETTER.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"Zamanlanabilir taslak bulunamadı: {draft_id}")
                self._audit_in_transaction(
                    "draft_scheduled", "draft", draft_id, {"board_id": board_id}
                )

    def reschedule_queued(self, draft_id: str, scheduled_at: datetime) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE pin_drafts SET scheduled_at = ?, quota_day = NULL
                WHERE id = ? AND status = ?
                """,
                (to_storage(scheduled_at), draft_id, PinStatus.QUEUED.value),
            )
            if cursor.rowcount != 1:
                raise ValueError("Yalnız kuyruğa alınmış pinler takvimde taşınabilir")
            self._audit_in_transaction(
                "draft_rescheduled", "draft", draft_id, {"at": to_storage(scheduled_at)}
            )

    def claim_next(
        self,
        now: datetime,
        *,
        worker_id: str,
        quota_day: str,
        max_daily: int,
        lease_seconds: int = 180,
    ) -> PinDraft | None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            reserved = int(
                self.connection.execute(
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
            )
            if reserved >= max(1, max_daily):
                self.connection.commit()
                return None
            row = self.connection.execute(
                """
                SELECT * FROM pin_drafts
                WHERE status = ? AND scheduled_at <= ?
                ORDER BY scheduled_at, rowid LIMIT 1
                """,
                (PinStatus.QUEUED.value, to_storage(now)),
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            lease_expires = now + timedelta(seconds=max(30, lease_seconds))
            cursor = self.connection.execute(
                """
                UPDATE pin_drafts
                SET status = ?, claimed_at = ?, lease_owner = ?, lease_expires_at = ?,
                    quota_day = ?
                WHERE id = ? AND status = ?
                """,
                (
                    PinStatus.PUBLISHING.value,
                    to_storage(now),
                    worker_id,
                    to_storage(lease_expires),
                    quota_day,
                    row["id"],
                    PinStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                return None
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return _draft_from_row(dict(row) | {"status": PinStatus.PUBLISHING.value})

    def claim_due(self, now: datetime, *, limit: int) -> tuple[PinDraft, ...]:
        result: list[PinDraft] = []
        worker = f"legacy-{uuid4()}"
        for _ in range(max(0, limit)):
            draft = self.claim_next(
                now,
                worker_id=worker,
                quota_day=now.date().isoformat(),
                max_daily=max(1, limit),
            )
            if draft is None:
                break
            result.append(draft)
        return tuple(result)

    def renew_lease(
        self, draft_id: str, worker_id: str, now: datetime, *, lease_seconds: int = 180
    ) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE pin_drafts SET lease_expires_at = ?
                WHERE id = ? AND status = ? AND lease_owner = ?
                """,
                (
                    to_storage(now + timedelta(seconds=max(30, lease_seconds))),
                    draft_id,
                    PinStatus.PUBLISHING.value,
                    worker_id,
                ),
            )
            return cursor.rowcount == 1

    def mark_published(
        self,
        draft_id: str,
        remote_id: str,
        published_at: datetime,
        *,
        remote_url: str | None = None,
        account_id: str | None = None,
    ) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE pin_drafts
                SET status = ?, pinterest_pin_id = ?, published_at = ?, remote_url = ?,
                    account_id = COALESCE(?, account_id), claimed_at = NULL,
                    lease_owner = NULL, lease_expires_at = NULL, last_error = NULL,
                    error_code = NULL, error_provider = NULL
                WHERE id = ? AND status = ?
                """,
                (
                    PinStatus.PUBLISHED.value,
                    remote_id,
                    to_storage(published_at),
                    remote_url,
                    account_id,
                    draft_id,
                    PinStatus.PUBLISHING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Yayınlanan taslak claim durumunda değil: {draft_id}")
            self._audit_in_transaction(
                "pin_published",
                "draft",
                draft_id,
                {"remote_id": remote_id},
                "pinterest",
            )

    def mark_publish_unknown(
        self,
        draft_id: str,
        message: str,
        *,
        remote_id: str | None = None,
        remote_url: str | None = None,
    ) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE pin_drafts
                SET status = ?, last_error = ?, error_code = 'ambiguous_result',
                    error_provider = 'pinterest', claimed_at = NULL,
                    lease_owner = NULL, lease_expires_at = NULL,
                    attempt_count = attempt_count + 1,
                    pinterest_pin_id = COALESCE(?, pinterest_pin_id),
                    remote_url = COALESCE(?, remote_url)
                WHERE id = ? AND status = ?
                """,
                (
                    PinStatus.PUBLISH_UNKNOWN.value,
                    message[:1000],
                    remote_id,
                    remote_url,
                    draft_id,
                    PinStatus.PUBLISHING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(
                    f"Belirsiz taslak publishing durumunda değil: {draft_id}"
                )
            self._audit_in_transaction(
                "publish_unknown",
                "draft",
                draft_id,
                {"error": message, "remote_id": remote_id},
                "pinterest",
            )

    def apply_publish_receipt(
        self,
        draft_id: str,
        remote_id: str,
        published_at: datetime,
        *,
        remote_url: str | None = None,
    ) -> bool:
        """Apply durable proof of remote success regardless of local queue recovery."""
        if not remote_id:
            raise ValueError("Yayın makbuzunda remote_id gerekli")
        with self.connection:
            existing = self.connection.execute(
                "SELECT status, pinterest_pin_id FROM pin_drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"Makbuz taslağı bulunamadı: {draft_id}")
            if existing["status"] == PinStatus.PUBLISHED.value:
                if existing["pinterest_pin_id"] != remote_id:
                    raise ValueError("Yayın makbuzu mevcut uzak kimlikle çelişiyor")
                return False
            cursor = self.connection.execute(
                """
                UPDATE pin_drafts
                SET status = ?, pinterest_pin_id = ?, published_at = ?, remote_url = ?,
                    claimed_at = NULL, lease_owner = NULL, lease_expires_at = NULL,
                    last_error = NULL, error_code = NULL, error_provider = NULL
                WHERE id = ? AND status != ?
                """,
                (
                    PinStatus.PUBLISHED.value,
                    remote_id,
                    to_storage(published_at),
                    remote_url,
                    draft_id,
                    PinStatus.PUBLISHED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Yayın makbuzu uygulanamadı: {draft_id}")
            self._audit_in_transaction(
                "publish_receipt_recovered",
                "draft",
                draft_id,
                {"remote_id": remote_id},
                "pinterest",
            )
            return True

    def reconcile_unknown(
        self, draft_id: str, remote_id: str, *, remote_url: str | None = None
    ) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE pin_drafts SET status = ?, pinterest_pin_id = ?, remote_url = ?,
                    published_at = ?, last_error = NULL, error_code = NULL
                WHERE id = ? AND status = ?
                """,
                (
                    PinStatus.PUBLISHED.value,
                    remote_id,
                    remote_url,
                    to_storage(utc_now()),
                    draft_id,
                    PinStatus.PUBLISH_UNKNOWN.value,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Uzlaştırılabilir taslak bulunamadı: {draft_id}")
            self._audit_in_transaction(
                "publish_reconciled", "draft", draft_id, {"remote_id": remote_id}
            )

    def requeue_unknown(self, draft_id: str, scheduled_at: datetime) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE pin_drafts SET status = ?, scheduled_at = ?, quota_day = NULL,
                    last_error = NULL, error_code = NULL, error_provider = NULL
                WHERE id = ? AND status = ?
                """,
                (
                    PinStatus.QUEUED.value,
                    to_storage(scheduled_at),
                    draft_id,
                    PinStatus.PUBLISH_UNKNOWN.value,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Belirsiz taslak bulunamadı: {draft_id}")
            self._audit_in_transaction("publish_requeued", "draft", draft_id, {})

    def mark_failed_attempt(
        self,
        draft_id: str,
        message: str,
        *,
        retry_at: datetime | None,
        retryable: bool,
        max_attempts: int = 5,
        error_code: str | None = None,
        provider: str | None = None,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT attempt_count FROM pin_drafts WHERE id = ? AND status = ?",
                (draft_id, PinStatus.PUBLISHING.value),
            ).fetchone()
            if row is None:
                raise KeyError(f"Publishing taslağı bulunamadı: {draft_id}")
            attempts = int(row["attempt_count"]) + 1
            if retryable and attempts < max_attempts and retry_at is not None:
                status = PinStatus.QUEUED
            elif attempts >= max_attempts:
                status = PinStatus.DEAD_LETTER
            else:
                status = PinStatus.FAILED
            self.connection.execute(
                """
                UPDATE pin_drafts
                SET status = ?, attempt_count = ?, last_error = ?, error_code = ?,
                    error_provider = ?, scheduled_at = ?, claimed_at = NULL,
                    lease_owner = NULL, lease_expires_at = NULL, quota_day = NULL
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    attempts,
                    message[:1000],
                    error_code,
                    provider,
                    to_storage(retry_at) if status is PinStatus.QUEUED else None,
                    draft_id,
                    PinStatus.PUBLISHING.value,
                ),
            )
            self._audit_in_transaction(
                "publish_retry" if status is PinStatus.QUEUED else "publish_failed",
                "draft",
                draft_id,
                {"attempt": attempts, "error": message, "status": status.value},
                provider,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def recover_stale_claims(self, now: datetime, *, max_attempts: int = 5) -> int:
        with self.connection:
            rows = self.connection.execute(
                """
                SELECT id, attempt_count FROM pin_drafts
                WHERE status = ? AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                """,
                (PinStatus.PUBLISHING.value, to_storage(now)),
            ).fetchall()
            for row in rows:
                attempts = int(row["attempt_count"]) + 1
                status = (
                    PinStatus.DEAD_LETTER
                    if attempts >= max_attempts
                    else PinStatus.QUEUED
                )
                self.connection.execute(
                    """
                    UPDATE pin_drafts SET status = ?, claimed_at = NULL,
                        lease_owner = NULL, lease_expires_at = NULL, quota_day = NULL,
                        attempt_count = ?, error_code = 'stale_lease',
                        last_error = 'Önceki yayın lease süresi doldu'
                    WHERE id = ? AND status = ?
                    """,
                    (status.value, attempts, row["id"], PinStatus.PUBLISHING.value),
                )
            return len(rows)

    def count_published_since(self, since: datetime) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM pin_drafts WHERE status = ? AND published_at >= ?",
                (PinStatus.PUBLISHED.value, to_storage(since)),
            ).fetchone()[0]
        )

    def list_drafts(
        self,
        status: PinStatus | None = None,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[PinDraft, ...]:
        query = "SELECT * FROM pin_drafts"
        parameters: list[object] = []
        if status:
            query += " WHERE status = ?"
            parameters.append(status.value)
        query += (
            " ORDER BY COALESCE(scheduled_at, '9999-12-31'), rowid LIMIT ? OFFSET ?"
        )
        parameters.extend((max(1, min(limit, 5000)), max(0, offset)))
        return tuple(
            _draft_from_row(row)
            for row in self.connection.execute(query, parameters).fetchall()
        )

    def save_metrics(self, snapshots: Iterable[MetricSnapshot]) -> None:
        values = tuple(
            (
                snapshot.pin_id,
                snapshot.metric_date.isoformat(),
                snapshot.impressions,
                snapshot.saves,
                snapshot.pin_clicks,
                snapshot.outbound_clicks,
                to_storage(utc_now()),
            )
            for snapshot in snapshots
        )
        if not values:
            return
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO pin_metrics (
                    remote_pin_id, metric_date, impressions, saves,
                    pin_clicks, outbound_clicks, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(remote_pin_id, metric_date) DO UPDATE SET
                    impressions=excluded.impressions, saves=excluded.saves,
                    pin_clicks=excluded.pin_clicks,
                    outbound_clicks=excluded.outbound_clicks,
                    captured_at=excluded.captured_at
                """,
                values,
            )
            self._audit_in_transaction(
                "metrics_saved", "analytics", None, {"count": len(values)}, "pinterest"
            )

    def list_metrics(
        self, remote_pin_id: str | None = None
    ) -> tuple[MetricSnapshot, ...]:
        query = "SELECT * FROM pin_metrics"
        parameters: tuple[object, ...] = ()
        if remote_pin_id:
            query += " WHERE remote_pin_id = ?"
            parameters = (remote_pin_id,)
        query += " ORDER BY metric_date DESC, remote_pin_id"
        return tuple(
            MetricSnapshot(
                pin_id=str(row["remote_pin_id"]),
                metric_date=datetime.fromisoformat(str(row["metric_date"])).date(),
                impressions=int(row["impressions"]),
                saves=int(row["saves"]),
                pin_clicks=int(row["pin_clicks"]),
                outbound_clicks=int(row["outbound_clicks"]),
            )
            for row in self.connection.execute(query, parameters).fetchall()
        )

    def save_trends(self, terms: Iterable[TrendTerm]) -> None:
        values = tuple(
            (
                term.term,
                term.vertical,
                term.score,
                term.source,
                term.observed_on.isoformat(),
            )
            for term in terms
        )
        if not values:
            return
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO trend_terms (term, vertical, score, source, observed_on)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(term, vertical, source, observed_on) DO UPDATE SET
                    score=excluded.score
                """,
                values,
            )
            self._audit_in_transaction(
                "trends_saved", "analytics", None, {"count": len(values)}
            )

    def list_trends(self, vertical: str = "") -> tuple[TrendTerm, ...]:
        rows = self.connection.execute(
            """
            SELECT term, vertical, MAX(score) score, source, MAX(observed_on) observed_on
            FROM trend_terms WHERE vertical IN ('', ?)
            GROUP BY term, vertical, source ORDER BY score DESC, term LIMIT 1000
            """,
            (vertical.lower(),),
        ).fetchall()
        return tuple(
            TrendTerm(
                term=str(row["term"]),
                score=float(row["score"]),
                vertical=str(row["vertical"]),
                source=str(row["source"]),
                observed_on=datetime.fromisoformat(str(row["observed_on"])).date(),
            )
            for row in rows
        )

    def create_experiment(
        self,
        experiment_id: str,
        product_id: str,
        name: str,
        variants: Iterable[tuple[str, str]],
    ) -> None:
        items = tuple(variants)
        if len(items) < 2:
            raise ValueError("Deney için en az iki varyant gerekli")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO experiments (id, product_id, name, status, created_at)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (experiment_id, product_id, name, to_storage(utc_now())),
            )
            self.connection.executemany(
                """
                INSERT INTO experiment_variants (experiment_id, draft_id, label)
                VALUES (?, ?, ?)
                """,
                ((experiment_id, draft_id, label) for draft_id, label in items),
            )
            self._audit_in_transaction(
                "experiment_created",
                "experiment",
                experiment_id,
                {"variants": len(items)},
            )

    def experiment_metrics(
        self, experiment_id: str
    ) -> tuple[sqlite3.Row, tuple[sqlite3.Row, ...]]:
        record = self.connection.execute(
            "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
        if record is None:
            raise ValueError(f"Deney bulunamadı: {experiment_id}")
        rows = self.connection.execute(
            """
            WITH latest AS (
                SELECT m.* FROM pin_metrics m JOIN (
                    SELECT remote_pin_id, MAX(metric_date) metric_date
                    FROM pin_metrics GROUP BY remote_pin_id
                ) x USING(remote_pin_id, metric_date)
            )
            SELECT v.label, COALESCE(SUM(m.impressions), 0) impressions,
                   COALESCE(SUM(m.saves + m.pin_clicks + m.outbound_clicks), 0) engagements,
                   COALESCE(SUM(m.saves * 4 + m.pin_clicks * 2 + m.outbound_clicks * 5), 0) weighted
            FROM experiment_variants v
            JOIN pin_drafts d ON d.id = v.draft_id
            LEFT JOIN latest m ON m.remote_pin_id = d.pinterest_pin_id
            WHERE v.experiment_id = ? GROUP BY v.label ORDER BY v.label
            """,
            (experiment_id,),
        ).fetchall()
        return record, tuple(rows)

    def finish_experiment(self, experiment_id: str, winner_label: str) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE experiments SET status = 'completed', winner_variant = ?
                WHERE id = ? AND EXISTS (
                    SELECT 1 FROM experiment_variants
                    WHERE experiment_id = ? AND label = ?
                )
                """,
                (winner_label, experiment_id, experiment_id, winner_label),
            )
            if cursor.rowcount != 1:
                raise ValueError("Deney veya kazanan varyant bulunamadı")
            self._audit_in_transaction(
                "experiment_completed",
                "experiment",
                experiment_id,
                {"winner": winner_label},
            )

    def list_experiments(self) -> tuple[sqlite3.Row, ...]:
        return tuple(
            self.connection.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC"
            ).fetchall()
        )

    def audit(
        self,
        event_type: str,
        *,
        entity_type: str,
        entity_id: str | None = None,
        details: dict[str, object] | None = None,
        provider: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        with self.connection:
            self._audit_in_transaction(
                event_type,
                entity_type,
                entity_id,
                details or {},
                provider,
                correlation_id,
            )

    def _audit_in_transaction(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str | None,
        details: dict[str, object],
        provider: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_events (
                occurred_at, event_type, entity_type, entity_id,
                provider, correlation_id, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                to_storage(utc_now()),
                event_type,
                entity_type,
                entity_id,
                provider,
                correlation_id,
                json.dumps(details, ensure_ascii=False, default=str),
            ),
        )


def _product_values(product: SourceProduct) -> tuple[object, ...]:
    return (
        product.id,
        product.title,
        product.listing_url,
        round(product.price * 100),
        product.currency,
        product.vertical,
        json.dumps(product.tags, ensure_ascii=False),
        json.dumps([str(path) for path in product.image_paths], ensure_ascii=False),
        product.description,
        product.kind.value,
        int(product.active),
        to_storage(product.imported_at),
    )


def _product_from_row(row: sqlite3.Row) -> SourceProduct:
    tags = json.loads(row["tags_json"])
    paths = json.loads(row["image_paths_json"])
    if not isinstance(tags, list) or not isinstance(paths, list):
        raise ValueError("Ürün JSON alanları liste değil")
    return SourceProduct(
        id=row["id"],
        title=row["title"],
        listing_url=row["listing_url"],
        price=int(row["price_minor"]) / 100,
        currency=row["currency"],
        vertical=row["vertical"],
        image_paths=tuple(Path(value) for value in paths),
        tags=tuple(str(value) for value in tags),
        description=row["description"],
        kind=SourceKind(row["source_kind"]),
        imported_at=from_storage(row["imported_at"]) or utc_now(),
        active=bool(row["active"]),
    )


def _draft_values(draft: PinDraft) -> tuple[object, ...]:
    return (
        draft.id,
        draft.product_id,
        draft.template_id,
        draft.title,
        draft.description,
        draft.alt_text,
        draft.destination_url,
        draft.board_id,
        str(draft.image_path) if draft.image_path else None,
        to_storage(draft.scheduled_at),
        draft.status.value,
        draft.pinterest_pin_id,
        to_storage(draft.published_at),
        draft.remote_url,
        to_storage(draft.approved_at),
        draft.generation_model,
        draft.prompt_version,
        draft.account_id,
        draft.last_error,
        draft.error_code,
        draft.error_provider,
        draft.attempt_count,
    )


def _draft_from_row(row: sqlite3.Row | dict[str, object]) -> PinDraft:
    return PinDraft(
        id=str(row["id"]),
        product_id=str(row["product_id"]),
        template_id=str(row["template_id"]),
        title=str(row["title"]),
        description=str(row["description"]),
        alt_text=str(row["alt_text"]),
        destination_url=str(row["destination_url"]),
        board_id=str(row["board_id"]) if row["board_id"] else None,
        image_path=Path(str(row["image_path"])) if row["image_path"] else None,
        scheduled_at=from_storage(str(row["scheduled_at"]))
        if row["scheduled_at"]
        else None,
        status=PinStatus(str(row["status"])),
        pinterest_pin_id=str(row["pinterest_pin_id"])
        if row["pinterest_pin_id"]
        else None,
        published_at=from_storage(str(row["published_at"]))
        if row["published_at"]
        else None,
        remote_url=str(row["remote_url"]) if row["remote_url"] else None,
        approved_at=from_storage(str(row["approved_at"]))
        if row["approved_at"]
        else None,
        generation_model=str(row["generation_model"])
        if row["generation_model"]
        else None,
        prompt_version=str(row["prompt_version"]) if row["prompt_version"] else None,
        account_id=str(row["account_id"]) if row["account_id"] else None,
        last_error=str(row["last_error"]) if row["last_error"] else None,
        error_code=str(row["error_code"]) if row["error_code"] else None,
        error_provider=str(row["error_provider"]) if row["error_provider"] else None,
        attempt_count=int(str(row["attempt_count"])),
    )


_UPSERT_PRODUCT = """
INSERT INTO products (
    id, title, listing_url, price_minor, currency, vertical, tags_json,
    image_paths_json, description, source_kind, active, imported_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    title=excluded.title, listing_url=excluded.listing_url,
    price_minor=excluded.price_minor, currency=excluded.currency,
    vertical=excluded.vertical, tags_json=excluded.tags_json,
    image_paths_json=excluded.image_paths_json, description=excluded.description,
    source_kind=excluded.source_kind, active=excluded.active,
    imported_at=excluded.imported_at
"""


_UPSERT_DRAFT = """
INSERT INTO pin_drafts (
    id, product_id, template_id, title, description, alt_text, destination_url,
    board_id, image_path, scheduled_at, status, pinterest_pin_id, published_at,
    remote_url, approved_at, generation_model, prompt_version, account_id,
    last_error, error_code, error_provider, attempt_count
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    title=excluded.title, description=excluded.description,
    alt_text=excluded.alt_text, destination_url=excluded.destination_url,
    board_id=excluded.board_id, image_path=excluded.image_path,
    scheduled_at=excluded.scheduled_at, status=excluded.status,
    approved_at=excluded.approved_at, generation_model=excluded.generation_model,
    prompt_version=excluded.prompt_version, last_error=excluded.last_error,
    error_code=excluded.error_code, error_provider=excluded.error_provider,
    attempt_count=excluded.attempt_count
WHERE pin_drafts.status NOT IN ('published', 'publish_unknown')
"""


_SCHEMA_V4 = """
BEGIN IMMEDIATE;
CREATE TABLE products (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 500),
    listing_url TEXT NOT NULL,
    price_minor INTEGER NOT NULL CHECK(price_minor >= 0),
    currency TEXT NOT NULL CHECK(length(currency) BETWEEN 3 AND 8),
    vertical TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    image_paths_json TEXT NOT NULL,
    description TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('folder', 'manual', 'etsy_api')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    imported_at TEXT NOT NULL
);
CREATE TABLE pin_drafts (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    template_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 100),
    description TEXT NOT NULL CHECK(length(description) <= 500),
    alt_text TEXT NOT NULL CHECK(length(alt_text) BETWEEN 1 AND 500),
    destination_url TEXT NOT NULL,
    board_id TEXT,
    image_path TEXT,
    scheduled_at TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'draft', 'ready', 'queued', 'publishing', 'published',
        'publish_unknown', 'failed', 'dead_letter'
    )),
    pinterest_pin_id TEXT,
    published_at TEXT,
    remote_url TEXT,
    approved_at TEXT,
    generation_model TEXT,
    prompt_version TEXT,
    account_id TEXT,
    claimed_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    quota_day TEXT,
    last_error TEXT,
    error_code TEXT,
    error_provider TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0)
);
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    provider TEXT,
    correlation_id TEXT,
    details_json TEXT NOT NULL
);
CREATE TABLE pin_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_pin_id TEXT NOT NULL,
    metric_date TEXT NOT NULL,
    impressions INTEGER NOT NULL CHECK(impressions >= 0),
    saves INTEGER NOT NULL CHECK(saves >= 0),
    pin_clicks INTEGER NOT NULL CHECK(pin_clicks >= 0),
    outbound_clicks INTEGER NOT NULL CHECK(outbound_clicks >= 0),
    captured_at TEXT NOT NULL,
    UNIQUE(remote_pin_id, metric_date)
);
CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    status TEXT NOT NULL CHECK(status IN ('running', 'completed')),
    created_at TEXT NOT NULL,
    winner_variant TEXT
);
CREATE TABLE experiment_variants (
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    draft_id TEXT NOT NULL UNIQUE REFERENCES pin_drafts(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    PRIMARY KEY(experiment_id, label)
);
CREATE TABLE trend_terms (
    term TEXT NOT NULL,
    vertical TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL CHECK(score BETWEEN 0 AND 100),
    source TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    PRIMARY KEY(term, vertical, source, observed_on)
);
CREATE INDEX pin_drafts_due_idx ON pin_drafts(status, scheduled_at);
CREATE INDEX pin_drafts_product_idx ON pin_drafts(product_id);
CREATE INDEX pin_drafts_lease_idx ON pin_drafts(status, lease_expires_at);
CREATE INDEX pin_drafts_published_idx ON pin_drafts(status, published_at);
CREATE UNIQUE INDEX pin_drafts_remote_idx ON pin_drafts(pinterest_pin_id)
    WHERE pinterest_pin_id IS NOT NULL;
CREATE INDEX audit_events_entity_idx ON audit_events(entity_type, entity_id, occurred_at);
CREATE INDEX pin_metrics_remote_idx ON pin_metrics(remote_pin_id, metric_date);
CREATE INDEX experiments_product_idx ON experiments(product_id, created_at);
CREATE INDEX trend_terms_vertical_idx ON trend_terms(vertical, score DESC);
PRAGMA user_version = 4;
COMMIT;
"""


_MIGRATE_V3_TO_V4 = """
BEGIN IMMEDIATE;
CREATE TABLE pin_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_pin_id TEXT NOT NULL,
    metric_date TEXT NOT NULL,
    impressions INTEGER NOT NULL CHECK(impressions >= 0),
    saves INTEGER NOT NULL CHECK(saves >= 0),
    pin_clicks INTEGER NOT NULL CHECK(pin_clicks >= 0),
    outbound_clicks INTEGER NOT NULL CHECK(outbound_clicks >= 0),
    captured_at TEXT NOT NULL,
    UNIQUE(remote_pin_id, metric_date)
);
CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    status TEXT NOT NULL CHECK(status IN ('running', 'completed')),
    created_at TEXT NOT NULL,
    winner_variant TEXT
);
CREATE TABLE experiment_variants (
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    draft_id TEXT NOT NULL UNIQUE REFERENCES pin_drafts(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    PRIMARY KEY(experiment_id, label)
);
CREATE TABLE trend_terms (
    term TEXT NOT NULL,
    vertical TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL CHECK(score BETWEEN 0 AND 100),
    source TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    PRIMARY KEY(term, vertical, source, observed_on)
);
CREATE INDEX pin_metrics_remote_idx ON pin_metrics(remote_pin_id, metric_date);
CREATE INDEX experiments_product_idx ON experiments(product_id, created_at);
CREATE INDEX trend_terms_vertical_idx ON trend_terms(vertical, score DESC);
PRAGMA user_version = 4;
COMMIT;
"""


_MIGRATE_V2_TO_V3 = """
BEGIN IMMEDIATE;
ALTER TABLE products RENAME TO products_v2;
ALTER TABLE pin_drafts RENAME TO pin_drafts_v2;
DROP INDEX IF EXISTS pin_drafts_due_idx;
CREATE TABLE products (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 500),
    listing_url TEXT NOT NULL,
    price_minor INTEGER NOT NULL CHECK(price_minor >= 0),
    currency TEXT NOT NULL CHECK(length(currency) BETWEEN 3 AND 8),
    vertical TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    image_paths_json TEXT NOT NULL,
    description TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('folder', 'manual', 'etsy_api')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    imported_at TEXT NOT NULL
);
INSERT INTO products SELECT
    id, title, listing_url, CAST(ROUND(MAX(price, 0) * 100) AS INTEGER),
    currency, vertical, tags_json, image_paths_json, description,
    'folder', 1, imported_at
FROM products_v2;
CREATE TABLE pin_drafts (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    template_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 100),
    description TEXT NOT NULL CHECK(length(description) <= 500),
    alt_text TEXT NOT NULL CHECK(length(alt_text) BETWEEN 1 AND 500),
    destination_url TEXT NOT NULL,
    board_id TEXT,
    image_path TEXT,
    scheduled_at TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'draft', 'ready', 'queued', 'publishing', 'published',
        'publish_unknown', 'failed', 'dead_letter'
    )),
    pinterest_pin_id TEXT,
    published_at TEXT,
    remote_url TEXT,
    approved_at TEXT,
    generation_model TEXT,
    prompt_version TEXT,
    account_id TEXT,
    claimed_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    quota_day TEXT,
    last_error TEXT,
    error_code TEXT,
    error_provider TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0)
);
INSERT INTO pin_drafts (
    id, product_id, template_id, title, description, alt_text, destination_url,
    board_id, image_path, scheduled_at, status, pinterest_pin_id, published_at,
    claimed_at, last_error, attempt_count
) SELECT
    id, product_id, template_id, substr(title, 1, 100), substr(description, 1, 500),
    CASE WHEN length(alt_text) = 0 THEN 'Pinterest pin' ELSE substr(alt_text, 1, 500) END,
    destination_url, board_id, image_path, scheduled_at,
    status, pinterest_pin_id, published_at, claimed_at, last_error,
    MAX(attempt_count, 0)
FROM pin_drafts_v2;
DROP TABLE pin_drafts_v2;
DROP TABLE products_v2;
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    provider TEXT,
    correlation_id TEXT,
    details_json TEXT NOT NULL
);
CREATE INDEX pin_drafts_due_idx ON pin_drafts(status, scheduled_at);
CREATE INDEX pin_drafts_product_idx ON pin_drafts(product_id);
CREATE INDEX pin_drafts_lease_idx ON pin_drafts(status, lease_expires_at);
CREATE INDEX pin_drafts_published_idx ON pin_drafts(status, published_at);
CREATE UNIQUE INDEX pin_drafts_remote_idx ON pin_drafts(pinterest_pin_id)
    WHERE pinterest_pin_id IS NOT NULL;
CREATE INDEX audit_events_entity_idx ON audit_events(entity_type, entity_id, occurred_at);
PRAGMA user_version = 3;
COMMIT;
"""
