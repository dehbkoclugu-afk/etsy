from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class AppSettings:
    brand_shop_name: str = "ECOVIA"
    brand_primary: str = "#173C35"
    brand_accent: str = "#C9855B"
    brand_surface: str = "#F4F0E6"
    brand_ink: str = "#18201D"
    anthropic_model: str = "claude-haiku-4-5-20251001"
    etsy_keystring: str = ""
    etsy_shop_id: str = ""
    etsy_redirect_uri: str = ""
    pinterest_app_id: str = ""
    pinterest_redirect_uri: str = "http://localhost:53682/callback"
    pinterest_default_board_id: str = ""
    pinterest_board_mappings: dict[str, str] = field(default_factory=dict)
    browser_pinterest_board: str = ""
    browser_channel: str = "chrome"
    export_directory: str = ""
    import_cache_directory: str = ""
    schedule_slots: tuple[str, ...] = ("09:00", "12:30", "15:00", "18:30", "21:00")
    max_daily_pins: int = 10
    max_publish_attempts: int = 5
    headless_batch_size: int = 3
    time_zone: str = "Europe/Istanbul"
    ai_language: str = "tr"
    allowed_destination_hosts: tuple[str, ...] = ("etsy.com",)
    cache_max_megabytes: int = 500
    cache_max_age_days: int = 30

    def __post_init__(self) -> None:
        for color in (
            self.brand_primary,
            self.brand_accent,
            self.brand_surface,
            self.brand_ink,
        ):
            if (
                len(color) != 7
                or not color.startswith("#")
                or any(
                    character not in "0123456789abcdefABCDEF" for character in color[1:]
                )
            ):
                raise ValueError("Marka renkleri #RRGGBB biçiminde olmalı")
        if not 1 <= self.max_daily_pins <= 50:
            raise ValueError("Günlük pin sınırı 1-50 arasında olmalı")
        if not 1 <= self.max_publish_attempts <= 20:
            raise ValueError("Yayın deneme sınırı 1-20 arasında olmalı")
        if not 1 <= self.headless_batch_size <= 20:
            raise ValueError("Headless batch sınırı 1-20 arasında olmalı")
        if not 10 <= self.cache_max_megabytes <= 10_000:
            raise ValueError("Cache boyutu 10-10000 MB arasında olmalı")
        if not 1 <= self.cache_max_age_days <= 3650:
            raise ValueError("Cache yaş sınırı 1-3650 gün arasında olmalı")
        try:
            ZoneInfo(self.time_zone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Bilinmeyen zaman dilimi: {self.time_zone}") from exc
        if not self.schedule_slots:
            raise ValueError("En az bir zamanlama slotu gerekli")
        for slot in self.schedule_slots:
            try:
                hour, minute = (int(value) for value in slot.split(":", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Geçersiz zamanlama slotu: {slot}") from exc
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError(f"Geçersiz zamanlama slotu: {slot}")
        for uri in (self.etsy_redirect_uri, self.pinterest_redirect_uri):
            if uri:
                parsed = urlparse(uri)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError(f"Geçersiz redirect URI: {uri}")
        if self.browser_channel not in {"chrome", "msedge"}:
            raise ValueError("Tarayıcı kanalı chrome veya msedge olmalı")
        hosts = tuple(
            dict.fromkeys(
                host.strip().lower()
                for host in self.allowed_destination_hosts
                if host.strip()
            )
        )
        if not hosts:
            raise ValueError("En az bir izinli Pinterest hedef hostu gerekli")
        if any("/" in host or ":" in host for host in hosts):
            raise ValueError("İzinli hedef hostları yalnızca domain adı içermeli")
        object.__setattr__(self, "allowed_destination_hosts", hosts)
        object.__setattr__(
            self, "pinterest_board_mappings", dict(self.pinterest_board_mappings)
        )


class SettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            corrupt = self.path.with_name(
                f"{self.path.name}.corrupt-{int(time.time())}"
            )
            try:
                os.replace(self.path, corrupt)
            except OSError:
                raise ValueError(f"Ayarlar okunamadı: {exc}") from exc
            return AppSettings()
        if not isinstance(payload, dict):
            raise ValueError("Ayar dosyasının kökü JSON nesnesi olmalı")
        try:
            allowed = {field.name for field in fields(AppSettings)}
            values = {key: value for key, value in payload.items() if key in allowed}
            if "schedule_slots" in values:
                values["schedule_slots"] = tuple(values["schedule_slots"])
            if "allowed_destination_hosts" in values:
                values["allowed_destination_hosts"] = tuple(
                    values["allowed_destination_hosts"]
                )
            if "pinterest_board_mappings" in values:
                values["pinterest_board_mappings"] = {
                    str(key): str(value)
                    for key, value in dict(values["pinterest_board_mappings"]).items()
                }
            return AppSettings(**values)
        except (TypeError, ValueError) as exc:
            invalid = self.path.with_name(
                f"{self.path.name}.invalid-{int(time.time())}"
            )
            try:
                os.replace(self.path, invalid)
            except OSError:
                raise ValueError(f"Ayarlar doğrulanamadı: {exc}") from exc
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2)
        with NamedTemporaryFile(
            "w", dir=self.path.parent, encoding="utf-8", delete=False
        ) as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        try:
            os.replace(temp_path, self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        finally:
            temp_path.unlink(missing_ok=True)
