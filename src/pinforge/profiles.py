from __future__ import annotations

import json
import os
import re
from typing import Any
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from pinforge.clock import to_storage, utc_now
from pinforge.settings import AppSettings, SettingsStore


@dataclass(frozen=True, slots=True)
class Profile:
    id: str
    name: str
    created_at: str


class ProfileStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "profiles.json"

    def list(self) -> tuple[Profile, ...]:
        payload = self._load()
        return tuple(Profile(**record) for record in payload["profiles"])

    @property
    def active_id(self) -> str:
        return str(self._load()["active_profile_id"])

    def directory(self, profile_id: str) -> Path:
        self.get(profile_id)
        return (
            self.root
            if profile_id == "default"
            else self.root / "profiles" / profile_id
        )

    def get(self, profile_id: str) -> Profile:
        profile = next((item for item in self.list() if item.id == profile_id), None)
        if profile is None:
            raise ValueError(f"Profil bulunamadı: {profile_id}")
        return profile

    def create(self, name: str, settings: AppSettings) -> Profile:
        clean_name = " ".join(name.split())
        if not 2 <= len(clean_name) <= 60:
            raise ValueError("Profil adı 2-60 karakter olmalı")
        payload = self._load()
        if any(
            str(item["name"]).casefold() == clean_name.casefold()
            for item in payload["profiles"]
        ):
            raise ValueError("Bu profil adı zaten kullanılıyor")
        stem = (
            re.sub(r"[^a-z0-9]+", "-", clean_name.casefold()).strip("-")[:32]
            or "profile"
        )
        existing = {str(item["id"]) for item in payload["profiles"]}
        suffix = 1
        profile_id = stem
        while profile_id in existing:
            suffix += 1
            profile_id = f"{stem[:27]}-{suffix}"
        profile = Profile(profile_id, clean_name, to_storage(utc_now()) or "")
        profile_dir = self.root / "profiles" / profile.id
        profile_dir.mkdir(parents=True, exist_ok=False)
        SettingsStore(profile_dir / "settings.json").save(settings)
        payload["profiles"].append(asdict(profile))
        self._save(payload)
        return profile

    def activate(self, profile_id: str) -> None:
        self.get(profile_id)
        payload = self._load()
        payload["active_profile_id"] = profile_id
        self._save(payload)

    def _load(self) -> dict[str, Any]:
        default = {
            "active_profile_id": "default",
            "profiles": [
                {
                    "id": "default",
                    "name": "Varsayılan",
                    "created_at": datetime(1970, 1, 1).isoformat(),
                }
            ],
        }
        if not self.path.exists():
            return default
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            profiles = payload["profiles"]
            active = str(payload["active_profile_id"])
            if not isinstance(profiles, list) or not profiles:
                raise ValueError("Profil listesi geçersiz")
            parsed = [Profile(**record) for record in profiles]
            if active not in {profile.id for profile in parsed}:
                raise ValueError("Aktif profil bulunamadı")
            return {
                "active_profile_id": active,
                "profiles": [asdict(item) for item in parsed],
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Profil kayıt dosyası bozuk: {exc}") from exc

    def _save(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", dir=self.root, encoding="utf-8", delete=False
        ) as temp:
            json.dump(payload, temp, ensure_ascii=False, indent=2)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        os.replace(temp_path, self.path)
        if os.name != "nt":
            self.path.chmod(0o600)
