from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import isfinite


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    pin_id: str
    metric_date: date
    impressions: int = 0
    saves: int = 0
    pin_clicks: int = 0
    outbound_clicks: int = 0

    def __post_init__(self) -> None:
        if not self.pin_id.strip():
            raise ValueError("Pinterest pin kimliği gerekli")
        values = (
            self.impressions,
            self.saves,
            self.pin_clicks,
            self.outbound_clicks,
        )
        if any(not 0 <= value <= 1_000_000_000_000 for value in values):
            raise ValueError("Performans metrikleri 0-1 trilyon arasında olmalı")

    @property
    def engagement_score(self) -> float:
        if self.impressions <= 0:
            return 0.0
        weighted = self.saves * 4 + self.pin_clicks * 2 + self.outbound_clicks * 5
        confidence = min(1.0, self.impressions / 1000)
        return weighted / self.impressions * confidence


@dataclass(frozen=True, slots=True)
class TrendTerm:
    term: str
    score: float
    vertical: str = ""
    observed_on: date = field(default_factory=date.today)
    source: str = "csv"

    def __post_init__(self) -> None:
        normalized = " ".join(self.term.lower().split())
        if not 2 <= len(normalized) <= 100:
            raise ValueError("Trend terimi 2-100 karakter olmalı")
        if not isfinite(self.score) or not 0 <= self.score <= 100:
            raise ValueError("Trend puanı 0-100 arasında olmalı")
        object.__setattr__(self, "term", normalized)


@dataclass(frozen=True, slots=True)
class Insight:
    dimension: str
    value: str
    impressions: int
    engagements: int
    score: float


@dataclass(frozen=True, slots=True)
class SeoSuggestion:
    term: str
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    experiment_id: str
    name: str
    status: str
    winner_label: str | None
    variants: tuple[Insight, ...]
