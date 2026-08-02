from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from pinforge_saas.settings import validate_production_settings


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
