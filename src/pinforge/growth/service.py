from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from pinforge.data.repository import PinRepository
from pinforge.domain.models import PinDraft, SourceProduct
from pinforge.growth.models import (
    ExperimentResult,
    Insight,
    MetricSnapshot,
    SeoSuggestion,
    TrendTerm,
)
from pinforge.integrations.pinterest.client import PinterestClient
from pinforge.integrations.http import ApiError

_WORDS = re.compile(r"[^\W\d_][\w-]{1,49}", re.UNICODE)


class GrowthService:
    MAX_CSV_BYTES = 5 * 1024 * 1024
    MAX_CSV_ROWS = 10_000

    def __init__(self, repository: PinRepository, *, time_zone: str = "UTC") -> None:
        self.repository = repository
        self.zone = ZoneInfo(time_zone)

    def import_metrics_csv(self, path: str | Path) -> int:
        source = self._csv_path(path)
        snapshots: list[MetricSnapshot] = []
        with source.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {
                "pin_id",
                "date",
                "impressions",
                "saves",
                "pin_clicks",
                "outbound_clicks",
            }
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError(f"Metrik CSV sütunları gerekli: {sorted(required)}")
            for index, row in enumerate(reader, start=1):
                if index > self.MAX_CSV_ROWS:
                    raise ValueError("Metrik CSV satır sınırını aşıyor")
                try:
                    snapshots.append(
                        MetricSnapshot(
                            pin_id=row["pin_id"].strip(),
                            metric_date=date.fromisoformat(row["date"].strip()),
                            impressions=int(row["impressions"]),
                            saves=int(row["saves"]),
                            pin_clicks=int(row["pin_clicks"]),
                            outbound_clicks=int(row["outbound_clicks"]),
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Metrik CSV satır {index} geçersiz: {exc}"
                    ) from exc
        self.repository.save_metrics(snapshots)
        return len(snapshots)

    def sync_pinterest(
        self, client: PinterestClient, *, end_date: date | None = None, days: int = 30
    ) -> tuple[int, tuple[str, ...]]:
        if not 1 <= days <= 90:
            raise ValueError("Analytics gün sayısı 1-90 arasında olmalı")
        finish = end_date or date.today()
        start = finish - timedelta(days=days - 1)
        snapshots: list[MetricSnapshot] = []
        issues: list[str] = []
        for draft in self.repository.list_drafts():
            if not draft.pinterest_pin_id:
                continue
            try:
                metrics = client.pin_analytics(draft.pinterest_pin_id, start, finish)
                snapshots.append(
                    MetricSnapshot(
                        pin_id=draft.pinterest_pin_id,
                        metric_date=finish,
                        impressions=metrics["IMPRESSION"],
                        saves=metrics["SAVE"],
                        pin_clicks=metrics["PIN_CLICK"],
                        outbound_clicks=metrics["OUTBOUND_CLICK"],
                    )
                )
            except (ApiError, KeyError, ValueError) as exc:
                issues.append(f"{draft.pinterest_pin_id}: {exc}")
        self.repository.save_metrics(snapshots)
        return len(snapshots), tuple(issues)

    def import_trends_csv(self, path: str | Path) -> int:
        source = self._csv_path(path)
        terms: list[TrendTerm] = []
        with source.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"term", "score"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError("Trend CSV en az term ve score sütunlarını içermeli")
            for index, row in enumerate(reader, start=1):
                if index > self.MAX_CSV_ROWS:
                    raise ValueError("Trend CSV satır sınırını aşıyor")
                try:
                    terms.append(
                        TrendTerm(
                            term=row["term"],
                            score=float(row["score"]),
                            vertical=(row.get("vertical") or "").strip().lower(),
                            observed_on=date.fromisoformat(
                                (row.get("date") or date.today().isoformat()).strip()
                            ),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Trend CSV satır {index} geçersiz: {exc}"
                    ) from exc
        self.repository.save_trends(terms)
        return len(terms)

    def insights(self, dimension: str = "template") -> tuple[Insight, ...]:
        if dimension not in {"template", "hour", "weekday"}:
            raise ValueError(f"Bilinmeyen analiz boyutu: {dimension}")
        rows = self.repository.connection.execute(
            """
            WITH latest AS (
                SELECT m.* FROM pin_metrics m JOIN (
                    SELECT remote_pin_id, MAX(metric_date) metric_date
                    FROM pin_metrics GROUP BY remote_pin_id
                ) x USING(remote_pin_id, metric_date)
            )
            SELECT d.template_id, d.published_at, m.impressions, m.saves,
                   m.pin_clicks, m.outbound_clicks
            FROM latest m JOIN pin_drafts d ON d.pinterest_pin_id = m.remote_pin_id
            WHERE m.impressions > 0
            """
        ).fetchall()
        aggregates: dict[str, list[int]] = {}
        for row in rows:
            if dimension == "template":
                value = str(row["template_id"])
            else:
                published = datetime.fromisoformat(str(row["published_at"]))
                local = published.astimezone(self.zone)
                value = (
                    f"{local.hour:02d}" if dimension == "hour" else str(local.weekday())
                )
            totals = aggregates.setdefault(value, [0, 0, 0])
            totals[0] += int(row["impressions"])
            totals[1] += int(row["saves"] + row["pin_clicks"] + row["outbound_clicks"])
            totals[2] += int(
                row["saves"] * 4 + row["pin_clicks"] * 2 + row["outbound_clicks"] * 5
            )
        return tuple(
            sorted(
                (
                    Insight(
                        dimension,
                        value,
                        totals[0],
                        totals[1],
                        _score(totals[2], totals[0]),
                    )
                    for value, totals in aggregates.items()
                ),
                key=lambda item: (item.score, item.impressions),
                reverse=True,
            )
        )

    def create_experiment(
        self, product_id: str, name: str, drafts: tuple[PinDraft, ...]
    ) -> str:
        if len(drafts) < 2 or len(drafts) > 5:
            raise ValueError("A/B testi 2-5 varyant içermeli")
        if any(draft.product_id != product_id for draft in drafts):
            raise ValueError("Deney varyantları aynı ürüne ait olmalı")
        experiment_id = str(uuid4())
        self.repository.create_experiment(
            experiment_id,
            product_id,
            name.strip()[:100] or "A/B testi",
            tuple((draft.id, chr(65 + index)) for index, draft in enumerate(drafts)),
        )
        return experiment_id

    def experiment_result(
        self, experiment_id: str, *, finalize: bool = False
    ) -> ExperimentResult:
        record, rows = self.repository.experiment_metrics(experiment_id)
        variants = tuple(
            sorted(
                (
                    Insight(
                        "variant",
                        str(row["label"]),
                        int(row["impressions"] or 0),
                        int(row["engagements"] or 0),
                        _score(int(row["weighted"] or 0), int(row["impressions"] or 0)),
                    )
                    for row in rows
                ),
                key=lambda item: (item.score, item.impressions),
                reverse=True,
            )
        )
        eligible = [item for item in variants if item.impressions > 0]
        current_leader = eligible[0].value if len(eligible) >= 2 else None
        stored_winner = (
            str(record["winner_variant"]) if record["winner_variant"] else None
        )
        winner = stored_winner or current_leader
        if finalize and current_leader and not stored_winner:
            self.repository.finish_experiment(experiment_id, current_leader)
            winner = current_leader
        return ExperimentResult(
            experiment_id,
            str(record["name"]),
            "completed"
            if stored_winner or (finalize and current_leader)
            else str(record["status"]),
            winner,
            variants,
        )

    def seo_suggestions(
        self, product: SourceProduct, *, limit: int = 12
    ) -> tuple[SeoSuggestion, ...]:
        relevance = Counter(_tokens(" ".join((product.title, *product.tags))))
        performance: Counter[str] = Counter()
        rows = self.repository.connection.execute(
            """
            WITH latest AS (
                SELECT m.* FROM pin_metrics m JOIN (
                    SELECT remote_pin_id, MAX(metric_date) metric_date
                    FROM pin_metrics GROUP BY remote_pin_id
                ) x USING(remote_pin_id, metric_date)
            )
            SELECT d.title, d.description, SUM(m.impressions) impressions,
                   SUM(m.saves * 4 + m.pin_clicks * 2 + m.outbound_clicks * 5) weighted
            FROM latest m JOIN pin_drafts d ON d.pinterest_pin_id = m.remote_pin_id
            GROUP BY d.id
            """
        ).fetchall()
        for row in rows:
            value = _score(int(row["weighted"] or 0), int(row["impressions"] or 0))
            for token in set(_tokens(f"{row['title']} {row['description']}")):
                performance[token] += round(value * 100)
        trends = self.repository.list_trends(product.vertical)
        trend_scores = {term.term: term.score for term in trends}
        candidates = set(relevance) | set(performance) | set(trend_scores)
        suggestions: list[SeoSuggestion] = []
        for term in candidates:
            product_points = min(30.0, relevance[term] * 15.0)
            trend_points = trend_scores.get(term, 0.0) * 0.45
            history_points = min(25.0, float(performance[term]))
            total = product_points + trend_points + history_points
            reasons = []
            if product_points:
                reasons.append("ürünle ilgili")
            if trend_points:
                reasons.append("trend güçlü")
            if history_points:
                reasons.append("geçmiş performans iyi")
            suggestions.append(
                SeoSuggestion(term, round(total, 2), ", ".join(reasons) or "aday")
            )
        return tuple(
            sorted(suggestions, key=lambda item: (item.score, item.term), reverse=True)[
                : max(1, min(limit, 50))
            ]
        )

    def _csv_path(self, path: str | Path) -> Path:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"CSV bulunamadı: {source}")
        if source.stat().st_size > self.MAX_CSV_BYTES:
            raise ValueError("CSV 5 MB sınırını aşıyor")
        return source


def _score(weighted: int, impressions: int) -> float:
    if impressions <= 0:
        return 0.0
    return round(weighted / impressions * min(1.0, impressions / 1000), 6)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _WORDS.findall(value) if len(token) >= 3)
