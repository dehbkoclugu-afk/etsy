from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from math import isfinite
from urllib.parse import urlparse

from pinforge.clock import ensure_utc, utc_now


class SourceKind(StrEnum):
    FOLDER = "folder"
    MANUAL = "manual"
    ETSY_API = "etsy_api"


class PinStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PUBLISH_UNKNOWN = "publish_unknown"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class SourceProduct:
    id: str
    title: str
    listing_url: str
    price: float
    currency: str
    vertical: str
    image_paths: tuple[Path, ...]
    tags: tuple[str, ...] = ()
    description: str = ""
    kind: SourceKind = SourceKind.FOLDER
    imported_at: datetime = field(default_factory=utc_now)
    active: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.title.strip():
            raise ValueError("Ürün kimliği ve başlığı zorunlu")
        if not isfinite(self.price) or self.price < 0:
            raise ValueError("Ürün fiyatı negatif veya geçersiz olamaz")
        parsed = urlparse(self.listing_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ürün bağlantısı geçerli bir HTTP(S) adresi olmalı")
        if not self.image_paths:
            raise ValueError("Ürünün en az bir görseli olmalı")
        object.__setattr__(self, "imported_at", ensure_utc(self.imported_at))


@dataclass(frozen=True, slots=True)
class BrandKit:
    shop_name: str = "ECOVIA"
    primary: str = "#173C35"
    accent: str = "#C9855B"
    surface: str = "#F4F0E6"
    ink: str = "#18201D"
    headline_font: str = "DejaVuSerif-Bold.ttf"
    body_font: str = "DejaVuSans.ttf"
    body_bold_font: str = "DejaVuSans-Bold.ttf"


@dataclass(frozen=True, slots=True)
class PinCopy:
    title: str
    description: str
    alt_text: str
    bullets: tuple[str, ...] = ()
    badge_text: str | None = "INSTANT DOWNLOAD"
    generation_model: str | None = None
    prompt_version: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title) > 100:
            raise ValueError("Pin başlığı 1-100 karakter olmalı")
        if len(self.description) > 500:
            raise ValueError("Pin açıklaması en fazla 500 karakter olabilir")
        if not self.alt_text.strip() or len(self.alt_text) > 500:
            raise ValueError("Pin alt metni 1-500 karakter olmalı")
        if len(self.bullets) > 5 or any(len(value) > 80 for value in self.bullets):
            raise ValueError("Pin maddeleri sınırı aşıyor")


@dataclass(frozen=True, slots=True)
class PinDraft:
    id: str
    product_id: str
    template_id: str
    title: str
    description: str
    alt_text: str
    destination_url: str
    board_id: str | None = None
    image_path: Path | None = None
    scheduled_at: datetime | None = None
    status: PinStatus = PinStatus.DRAFT
    pinterest_pin_id: str | None = None
    published_at: datetime | None = None
    remote_url: str | None = None
    approved_at: datetime | None = None
    generation_model: str | None = None
    prompt_version: str | None = None
    account_id: str | None = None
    last_error: str | None = None
    error_code: str | None = None
    error_provider: str | None = None
    attempt_count: int = 0

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.product_id.strip():
            raise ValueError("Taslak ve ürün kimliği zorunlu")
        if not self.template_id.strip():
            raise ValueError("Şablon kimliği zorunlu")
        PinCopy(self.title, self.description, self.alt_text)
        parsed = urlparse(self.destination_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Pin hedef bağlantısı geçerli bir HTTP(S) adresi olmalı")
        if self.attempt_count < 0:
            raise ValueError("Deneme sayısı negatif olamaz")
        for name in ("scheduled_at", "published_at", "approved_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value))


@dataclass(frozen=True, slots=True)
class Board:
    id: str
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class PublishResult:
    remote_id: str
    remote_url: str | None = None
