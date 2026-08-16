from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from pinforge_saas.settings import (
    resolve_private_media_root,
    resolve_token_encryption_key,
    validate_production_settings,
)


def test_production_requires_a_non_default_secret() -> None:
    with pytest.raises(ImproperlyConfigured, match="PINFORGE_SECRET_KEY"):
        validate_production_settings(
            debug=False,
            secret_key="pinforge-dev-only-secret",
            allowed_hosts=["pinforge.example"],
            database_engine="django.db.backends.postgresql",
        )


def test_production_requires_allowed_hosts() -> None:
    with pytest.raises(ImproperlyConfigured, match="PINFORGE_ALLOWED_HOSTS"):
        validate_production_settings(
            debug=False,
            secret_key="production-secret-value",
            allowed_hosts=[],
            database_engine="django.db.backends.postgresql",
        )


def test_production_rejects_sqlite() -> None:
    with pytest.raises(ImproperlyConfigured, match="PostgreSQL"):
        validate_production_settings(
            debug=False,
            secret_key="production-secret-value",
            allowed_hosts=["pinforge.example"],
            database_engine="django.db.backends.sqlite3",
        )


def test_development_allows_explicit_sqlite_test_database() -> None:
    validate_production_settings(
        debug=True,
        secret_key="pinforge-dev-only-secret",
        allowed_hosts=[],
        database_engine="django.db.backends.sqlite3",
    )


def test_production_requires_an_explicit_private_media_root() -> None:
    with pytest.raises(ImproperlyConfigured, match="PINFORGE_PRIVATE_MEDIA_ROOT"):
        resolve_private_media_root(
            configured="",
            debug=False,
            test_sqlite=False,
        )


def test_development_private_media_root_stays_inside_project() -> None:
    root = resolve_private_media_root(
        configured="",
        debug=True,
        test_sqlite=False,
    )

    assert root.name == "private-media"
    assert root.parent.name == "var"


def test_production_requires_token_encryption_key() -> None:
    with pytest.raises(ImproperlyConfigured, match="TOKEN_ENCRYPTION_KEY"):
        validate_production_settings(
            debug=False,
            secret_key="production-secret-value",
            allowed_hosts=["pinforge.example"],
            database_engine="django.db.backends.postgresql",
        )


def test_development_token_key_is_stable_for_the_secret_key() -> None:
    first = resolve_token_encryption_key(configured="", secret_key="dev-secret")
    second = resolve_token_encryption_key(configured="", secret_key="dev-secret")

    assert first == second
    assert len(first) == 44


def test_token_key_rejects_invalid_fernet_value() -> None:
    with pytest.raises(ImproperlyConfigured, match="Fernet"):
        resolve_token_encryption_key(
            configured="not-a-fernet-key",
            secret_key="dev-secret",
        )
