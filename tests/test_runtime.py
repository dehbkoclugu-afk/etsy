from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

from keyring.errors import PasswordDeleteError

from pinforge.integrations.oauth import TokenSet, save_token
from pinforge.observability import _redact
from pinforge.runtime import (
    ANTHROPIC_API_KEY,
    ETSY_SHARED_SECRET,
    ETSY_TOKEN,
    PINTEREST_TOKEN,
    PinForgeRuntime,
)
from pinforge.security import FileLock, MemorySecretStore, SecretStore


def test_runtime_builds_cached_clients_and_audits_settings(tmp_path: Path) -> None:
    token = TokenSet("access", "refresh", time.time() + 3600)
    secrets = MemorySecretStore(
        {
            ANTHROPIC_API_KEY: "anthropic",
            ETSY_SHARED_SECRET: "etsy-secret",
        }
    )
    save_token(secrets, ETSY_TOKEN, token)
    save_token(secrets, PINTEREST_TOKEN, token)
    runtime = PinForgeRuntime(tmp_path, secrets)
    runtime.save_settings(
        replace(
            runtime.settings,
            etsy_keystring="key",
            pinterest_board_mappings={"airbnb": "board"},
        )
    )
    assert runtime.brand_kit().shop_name == "ECOVIA"
    assert runtime.board_for_vertical("airbnb") == "board"
    assert runtime.copy_generator() is runtime.copy_generator()
    assert runtime.etsy_client() is runtime.etsy_client()
    assert runtime.pinterest_client() is runtime.pinterest_client()
    assert (
        runtime.repository.connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type='settings_updated'"
        ).fetchone()[0]
        == 1
    )
    runtime.disconnect("pinterest")
    assert secrets.get(PINTEREST_TOKEN) is None
    runtime.close()


def test_file_lock_and_secret_redaction(tmp_path: Path) -> None:
    path = tmp_path / "worker.lock"
    with FileLock(path):
        assert path.is_file()
    with FileLock(path):
        pass
    message = "authorization: Bearer abc123 refresh_token=topsecret"
    redacted = _redact(message)
    assert "abc123" not in redacted
    assert "topsecret" not in redacted


def test_secret_delete_removes_environment_override(monkeypatch) -> None:  # noqa: ANN001
    os.environ["PINFORGE_ETSY_OAUTH_TOKEN"] = "token"

    def missing(service: str, name: str) -> None:
        raise PasswordDeleteError("missing")

    monkeypatch.setattr("keyring.delete_password", missing)
    SecretStore().delete("etsy_oauth_token")
    assert "PINFORGE_ETSY_OAUTH_TOKEN" not in os.environ
