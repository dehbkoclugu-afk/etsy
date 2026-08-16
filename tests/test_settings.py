from __future__ import annotations

import json
from pathlib import Path

from pinforge.settings import AppSettings, SettingsStore


def test_settings_are_atomic_and_ignore_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    settings = AppSettings(schedule_slots=("08:00", "18:00"), max_daily_pins=7)
    store.save(settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["future_field"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.load() == settings
