from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]
TEST_SQLITE = os.environ.get("PINFORGE_TEST_SQLITE") == "1"
DEBUG = os.environ.get("PINFORGE_DEBUG", "1" if TEST_SQLITE else "0") == "1"
DEV_SECRET_KEY = "pinforge-dev-only-secret"
SECRET_KEY = os.environ.get("PINFORGE_SECRET_KEY", DEV_SECRET_KEY)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "PINFORGE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
    ).split(",")
    if host.strip()
]


def validate_production_settings(
    *,
    debug: bool,
    secret_key: str,
    allowed_hosts: list[str],
    database_engine: str,
) -> None:
    if debug:
        return
    if secret_key == DEV_SECRET_KEY:
        raise ImproperlyConfigured(
            "PINFORGE_SECRET_KEY must be set in production"
        )
    if not allowed_hosts:
        raise ImproperlyConfigured(
            "PINFORGE_ALLOWED_HOSTS must be set in production"
        )
    if database_engine != "django.db.backends.postgresql":
        raise ImproperlyConfigured("PostgreSQL is required in production")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "pinforge_web.apps.PinForgeWebConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "pinforge_web.tenancy.ActiveOrganizationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pinforge_saas.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        )
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTH_USER_MODEL = "pinforge_web.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
WSGI_APPLICATION = "pinforge_saas.wsgi.application"
ASGI_APPLICATION = "pinforge_saas.asgi.application"

DATABASES: dict[str, dict[str, object]]
if TEST_SQLITE:
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("PINFORGE_DB_NAME", "pinforge"),
            "USER": os.environ.get("PINFORGE_DB_USER", "pinforge"),
            "PASSWORD": os.environ.get("PINFORGE_DB_PASSWORD", ""),
            "HOST": os.environ.get("PINFORGE_DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("PINFORGE_DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }

validate_production_settings(
    debug=DEBUG,
    secret_key=SECRET_KEY,
    allowed_hosts=ALLOWED_HOSTS,
    database_engine=str(DATABASES["default"]["ENGINE"]),
)

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31_536_000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
