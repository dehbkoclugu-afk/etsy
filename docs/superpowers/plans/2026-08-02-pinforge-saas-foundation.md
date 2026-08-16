# PinForge SaaS Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-shaped Django/PostgreSQL web foundation with email accounts, organizations, memberships, tenant isolation, signup, health checks, and CI while preserving the existing PinForge desktop core.

**Architecture:** Introduce `pinforge_saas` as the Django project and `pinforge_web` as the first product app beside the framework-independent `pinforge` package. PostgreSQL is mandatory in production and GitHub CI; an explicit in-memory SQLite switch exists only so this sandbox can run fast local checks without a database service. Server-rendered Django views provide the first vertical slice and all tenant-owned access flows through an active `Organization` membership.

**Tech Stack:** Python 3.12, Django 5.2 LTS, PostgreSQL 17, psycopg 3, pytest, pytest-django, Ruff 0.11.13, mypy, GitHub Actions

## Global Constraints

- Keep the existing `src/pinforge` desktop package and all 40 tests passing.
- Use one Django monolith; do not add Redis, Celery, a SPA framework, an API framework, or a second service.
- Use PostgreSQL in production and CI integration tests; permit SQLite only when `PINFORGE_TEST_SQLITE=1` is explicitly set.
- Use Django secure cookie sessions, CSRF middleware, email identity, and a custom user model created before the first SaaS migration.
- Every tenant-owned record must have a non-null `organization_id`; cross-tenant object access must return 404.
- Do not store provider credentials, OAuth tokens, or payment data in this foundation slice.
- Keep dependency ranges in `pyproject.toml`; generate exact transitive pins in `requirements.lock`.
- Use inline execution in this session; do not dispatch subagents.
- Base the feature on `agent/pinforge-desktop-v0-2-0`; implement on `agent/pinforge-saas-foundation`.

---

## File Map

### Project entry and configuration

- `manage.py`: Django command entry point.
- `src/pinforge_saas/settings.py`: environment parsing, installed apps, middleware,
  templates, database selection, security defaults, and production validation.
- `src/pinforge_saas/urls.py`: health, authentication, signup, and dashboard routes.
- `src/pinforge_saas/asgi.py`: ASGI entry point.
- `src/pinforge_saas/wsgi.py`: WSGI entry point.

### Product app

- `src/pinforge_web/apps.py`: Django app configuration.
- `src/pinforge_web/models.py`: custom email user, organization, and membership models.
- `src/pinforge_web/services.py`: atomic user/organization registration.
- `src/pinforge_web/tenancy.py`: active-organization middleware and tenant-scoped lookup.
- `src/pinforge_web/forms.py`: signup form and validation.
- `src/pinforge_web/views.py`: liveness, readiness, signup, and dashboard views.
- `src/pinforge_web/urls.py`: app route names.
- `src/pinforge_web/migrations/0001_initial.py`: first SaaS schema.

### UI and operations

- `templates/base.html`: accessible shared shell.
- `templates/registration/login.html`: login form.
- `templates/registration/signup.html`: signup form.
- `templates/pinforge_web/dashboard.html`: first authenticated tenant page.
- `.env.example`: non-secret local environment contract.
- `docker-compose.saas.yml`: PostgreSQL 17 local service.

### Tests and quality

- `tests/saas/conftest.py`: Django test setup and helpers.
- `tests/saas/test_health.py`: liveness/readiness behavior.
- `tests/saas/test_models.py`: email user and organization constraints.
- `tests/saas/test_services.py`: atomic account and tenant creation.
- `tests/saas/test_tenancy.py`: active membership and cross-tenant denial.
- `tests/saas/test_auth.py`: signup, login, and dashboard vertical slice.
- `tests/saas/test_settings.py`: production configuration guards.
- `.github/workflows/quality.yml`: PostgreSQL CI service and Django checks.
- `pyproject.toml`: Django dependencies and pytest configuration.
- `requirements.lock`: fully resolved production and development dependency pins.
- `README.md`: SaaS development commands and architecture links.

---

### Task 1: Bootstrap Django and Health Endpoints

**Files:**
- Modify: `pyproject.toml:9-32`
- Modify: `requirements.lock`
- Create: `manage.py`
- Create: `src/pinforge_saas/__init__.py`
- Create: `src/pinforge_saas/settings.py`
- Create: `src/pinforge_saas/urls.py`
- Create: `src/pinforge_saas/asgi.py`
- Create: `src/pinforge_saas/wsgi.py`
- Create: `src/pinforge_web/__init__.py`
- Create: `src/pinforge_web/apps.py`
- Create: `src/pinforge_web/views.py`
- Create: `tests/saas/__init__.py`
- Create: `tests/saas/conftest.py`
- Create: `tests/saas/test_health.py`

**Interfaces:**
- Consumes: existing setuptools `src/` layout and Python 3.11+ package metadata.
- Produces: `pinforge_saas.settings`, route names `health-live` and `health-ready`, and a Django test environment used by every later task.

- [ ] **Step 1: Add bounded Django test/runtime dependencies**

Add these entries to the existing dependency tables in `pyproject.toml`:

```toml
[project]
dependencies = [
  "Django>=5.2,<5.3",
  "httpx>=0.28,<1",
  "keyring>=25,<26",
  "Pillow>=12.3,<13",
  "psycopg[binary]>=3.2,<4",
  "PySide6>=6.8,<7",
  "tzdata>=2026.3,<2027",
]

[project.optional-dependencies]
dev = [
  "mypy>=1.17,<2",
  "pip-audit>=2.9,<3",
  "pytest>=8.3,<10",
  "pytest-cov>=6,<7",
  "pytest-django>=4.11,<5",
  "ruff>=0.11,<1",
]
```

Add this to `[tool.pytest.ini_options]`:

```toml
DJANGO_SETTINGS_MODULE = "pinforge_saas.settings"
python_files = ["test_*.py"]
```

- [ ] **Step 2: Resolve and install exact dependencies**

Run:

```bash
UV_CACHE_DIR=/tmp/pinforge-uv-cache uv pip compile pyproject.toml \
  --extra dev --python-version 3.12 --output-file requirements.lock
UV_CACHE_DIR=/tmp/pinforge-uv-cache uv pip sync \
  --python /tmp/pinforge-venv/bin/python requirements.lock
UV_CACHE_DIR=/tmp/pinforge-uv-cache uv pip install \
  --python /tmp/pinforge-venv/bin/python -e .
```

Expected: `requirements.lock` contains exact transitive pins and Django imports from `/tmp/pinforge-venv`.

- [ ] **Step 3: Write failing liveness and readiness tests**

Create `tests/saas/conftest.py`:

```python
from __future__ import annotations

import os

os.environ.setdefault("PINFORGE_TEST_SQLITE", "1")
```

Create `tests/saas/test_health.py`:

```python
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.urls import reverse


def test_liveness_does_not_touch_database(client) -> None:
    response = client.get(reverse("health-live"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_checks_database(client) -> None:
    response = client.get(reverse("health-ready"))
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.django_db
def test_readiness_hides_database_error_details(client) -> None:
    with patch("pinforge_web.views.connections") as mocked_connections:
        mocked_connections.__getitem__.side_effect = DatabaseError(
            "password leaked in driver message"
        )
        response = client.get(reverse("health-ready"))
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"password" not in response.content
```

- [ ] **Step 4: Run the health tests and confirm the missing project failure**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_health.py -v
```

Expected: collection fails because `pinforge_saas.settings` does not exist.

- [ ] **Step 5: Add the minimal Django project and app configuration**

Create `manage.py`:

```python
#!/usr/bin/env python
from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pinforge_saas.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

Create `src/pinforge_web/apps.py`:

```python
from django.apps import AppConfig


class PinForgeWebConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pinforge_web"
```

Create `src/pinforge_saas/settings.py` with this database boundary and standard Django applications/middleware:

```python
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
TEST_SQLITE = os.environ.get("PINFORGE_TEST_SQLITE") == "1"
DEBUG = os.environ.get("PINFORGE_DEBUG", "1" if TEST_SQLITE else "0") == "1"
SECRET_KEY = os.environ.get("PINFORGE_SECRET_KEY", "pinforge-dev-only-secret")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("PINFORGE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    if host.strip()
]

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
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "pinforge_saas.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
WSGI_APPLICATION = "pinforge_saas.wsgi.application"
ASGI_APPLICATION = "pinforge_saas.asgi.application"

if TEST_SQLITE:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PINFORGE_DB_NAME", "pinforge"),
        "USER": os.environ.get("PINFORGE_DB_USER", "pinforge"),
        "PASSWORD": os.environ.get("PINFORGE_DB_PASSWORD", ""),
        "HOST": os.environ.get("PINFORGE_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("PINFORGE_DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
```

Create `src/pinforge_web/views.py`:

```python
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health_live(request):
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request):
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
```

Create `src/pinforge_saas/urls.py`:

```python
from django.contrib import admin
from django.urls import path

from pinforge_web import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/live", views.health_live, name="health-live"),
    path("health/ready", views.health_ready, name="health-ready"),
]
```

Create `src/pinforge_saas/asgi.py`:

```python
from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pinforge_saas.settings")
application = get_asgi_application()
```

Create `src/pinforge_saas/wsgi.py`:

```python
from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pinforge_saas.settings")
application = get_wsgi_application()
```

Create `src/pinforge_saas/__init__.py` and `src/pinforge_web/__init__.py` as empty package markers.

- [ ] **Step 6: Run health and desktop regression tests**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_health.py -v
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest -q
```

Expected: 3 health tests pass and all existing 40 desktop tests remain green.

- [ ] **Step 7: Commit the bootstrapped web process**

```bash
git add pyproject.toml requirements.lock manage.py src/pinforge_saas \
  src/pinforge_web tests/saas
git commit -m "feat: bootstrap PinForge SaaS web process"
```

---

### Task 2: Add Email Users, Organizations, and Memberships

**Files:**
- Create: `src/pinforge_web/models.py`
- Create: `src/pinforge_web/migrations/__init__.py`
- Create: `src/pinforge_web/migrations/0001_initial.py`
- Modify: `src/pinforge_saas/settings.py`
- Create: `tests/saas/test_models.py`

**Interfaces:**
- Consumes: Django app registry and database configuration from Task 1.
- Produces: `User`, `Organization`, `Membership`, `Membership.Role`, reverse relation `user.memberships`, and setting `AUTH_USER_MODEL="pinforge_web.User"`.

- [ ] **Step 1: Write failing model tests**

Create `tests/saas/test_models.py`:

```python
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from pinforge_web.models import Membership, Organization, User


@pytest.mark.django_db
def test_user_uses_normalized_email_as_identity() -> None:
    user = User.objects.create_user(email="OWNER@Example.COM", password="strong-pass-123")
    assert user.email == "owner@example.com"
    assert user.username is None
    assert user.check_password("strong-pass-123")


@pytest.mark.django_db
def test_user_requires_email() -> None:
    with pytest.raises(ValueError, match="email"):
        User.objects.create_user(email="", password="strong-pass-123")


@pytest.mark.django_db
def test_membership_is_unique_per_user_and_organization() -> None:
    user = User.objects.create_user(email="owner@example.com", password="strong-pass-123")
    organization = Organization.objects.create(name="Example Shop", timezone="Europe/Istanbul")
    Membership.objects.create(user=user, organization=organization, role=Membership.Role.OWNER)
    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.create(user=user, organization=organization, role=Membership.Role.MEMBER)
```

- [ ] **Step 2: Run the model tests and verify the missing-model failure**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_models.py -v
```

Expected: import fails because `pinforge_web.models` does not define the requested classes.

- [ ] **Step 3: Implement the custom user and tenant models**

Create `src/pinforge_web/models.py`:

```python
from __future__ import annotations

from uuid import uuid4

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra_fields) -> "User":
        if not email:
            raise ValueError("email is required")
        normalized = self.normalize_email(email).lower()
        user = self.model(email=normalized, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra_fields) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("superuser must have is_staff and is_superuser")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    objects = UserManager()


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=120)
    timezone = models.CharField(max_length=64, default="UTC")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "organization"), name="unique_user_organization_membership"
            )
        ]
```

Add to `src/pinforge_saas/settings.py`:

```python
AUTH_USER_MODEL = "pinforge_web.User"
```

- [ ] **Step 4: Generate and inspect the initial migration**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/python manage.py makemigrations pinforge_web
/tmp/pinforge-venv/bin/python -m compileall -q src/pinforge_web/migrations
```

Expected: `0001_initial.py` creates `User`, `Organization`, `Membership`, and the unique membership constraint. Inspect the file and confirm no provider token or billing table appears.

- [ ] **Step 5: Run model and migration checks**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_models.py -v
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: 3 tests pass and Django reports `No changes detected`.

- [ ] **Step 6: Commit the tenant schema**

```bash
git add src/pinforge_web/models.py src/pinforge_web/migrations \
  src/pinforge_saas/settings.py tests/saas/test_models.py
git commit -m "feat: add SaaS identity and tenant schema"
```

---

### Task 3: Add Atomic Registration and Tenant Isolation

**Files:**
- Create: `src/pinforge_web/services.py`
- Create: `src/pinforge_web/tenancy.py`
- Modify: `src/pinforge_saas/settings.py`
- Create: `tests/saas/test_services.py`
- Create: `tests/saas/test_tenancy.py`

**Interfaces:**
- Consumes: `User`, `Organization`, `Membership`, Django sessions, and authentication middleware.
- Produces: `register_account(*, email, password, organization_name, timezone) -> Registration`, `request.organization`, `request.membership`, and `get_tenant_object_or_404(model, organization, **lookup)`.

- [ ] **Step 1: Write failing atomic-registration tests**

Create `tests/saas/test_services.py`:

```python
from __future__ import annotations

from unittest.mock import patch

import pytest

from pinforge_web.models import Membership, Organization, User
from pinforge_web.services import register_account


@pytest.mark.django_db
def test_register_account_creates_owner_membership() -> None:
    result = register_account(
        email="owner@example.com",
        password="strong-pass-123",
        organization_name="Example Shop",
        timezone="Europe/Istanbul",
    )
    assert result.user.email == "owner@example.com"
    assert result.organization.name == "Example Shop"
    assert result.membership.role == Membership.Role.OWNER


@pytest.mark.django_db(transaction=True)
def test_register_account_rolls_back_every_record_on_membership_failure() -> None:
    with patch.object(Membership.objects, "create", side_effect=RuntimeError("failure")):
        with pytest.raises(RuntimeError, match="failure"):
            register_account(
                email="owner@example.com",
                password="strong-pass-123",
                organization_name="Example Shop",
                timezone="UTC",
            )
    assert not User.objects.exists()
    assert not Organization.objects.exists()
```

- [ ] **Step 2: Run the service tests and verify the missing-service failure**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_services.py -v
```

Expected: import fails because `pinforge_web.services` does not exist.

- [ ] **Step 3: Implement the atomic registration service**

Create `src/pinforge_web/services.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from pinforge_web.models import Membership, Organization, User


@dataclass(frozen=True, slots=True)
class Registration:
    user: User
    organization: Organization
    membership: Membership


@transaction.atomic
def register_account(
    *, email: str, password: str, organization_name: str, timezone: str
) -> Registration:
    user = User.objects.create_user(email=email, password=password)
    organization = Organization.objects.create(name=organization_name, timezone=timezone)
    membership = Membership.objects.create(
        user=user, organization=organization, role=Membership.Role.OWNER
    )
    return Registration(user, organization, membership)
```

- [ ] **Step 4: Write failing active-tenant and cross-tenant tests**

Create `tests/saas/test_tenancy.py`:

```python
from __future__ import annotations

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from django.urls import path

from pinforge_web.models import Membership, Organization, User
from pinforge_web.tenancy import ActiveOrganizationMiddleware, get_tenant_object_or_404


@pytest.mark.django_db
def test_middleware_selects_only_an_authenticated_membership(rf: RequestFactory) -> None:
    user = User.objects.create_user(email="owner@example.com", password="strong-pass-123")
    organization = Organization.objects.create(name="Example Shop")
    membership = Membership.objects.create(
        user=user, organization=organization, role=Membership.Role.OWNER
    )
    request = rf.get("/")
    request.user = user
    request.session = {"active_organization_id": str(organization.id)}
    ActiveOrganizationMiddleware(lambda _: HttpResponse())(request)
    assert request.organization == organization
    assert request.membership == membership


@pytest.mark.django_db
def test_tenant_lookup_returns_404_for_another_organization() -> None:
    user = User.objects.create_user(email="owner@example.com", password="strong-pass-123")
    allowed = Organization.objects.create(name="Allowed")
    forbidden = Organization.objects.create(name="Forbidden")
    membership = Membership.objects.create(
        user=user, organization=forbidden, role=Membership.Role.OWNER
    )
    with pytest.raises(Http404):
        get_tenant_object_or_404(Membership, allowed, pk=membership.pk)
```

- [ ] **Step 5: Run the tenancy tests and verify the missing-module failure**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_tenancy.py -v
```

Expected: import fails because `pinforge_web.tenancy` does not exist.

- [ ] **Step 6: Implement active-organization resolution and scoped lookup**

Create `src/pinforge_web/tenancy.py`:

```python
from __future__ import annotations

from typing import Any, TypeVar

from django.db.models import Model
from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from pinforge_web.models import Membership, Organization

TenantModel = TypeVar("TenantModel", bound=Model)


class ActiveOrganizationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        request.organization = None
        request.membership = None
        if request.user.is_authenticated:
            memberships = Membership.objects.filter(user=request.user).select_related("organization")
            requested_id = request.session.get("active_organization_id")
            membership = memberships.filter(organization_id=requested_id).first() if requested_id else None
            membership = membership or memberships.order_by("created_at", "pk").first()
            if membership is not None:
                request.membership = membership
                request.organization = membership.organization
                request.session["active_organization_id"] = str(membership.organization_id)
        return self.get_response(request)


def get_tenant_object_or_404(
    model: type[TenantModel], organization: Organization, **lookup: Any
) -> TenantModel:
    return get_object_or_404(model, organization=organization, **lookup)
```

Insert after authentication middleware in `src/pinforge_saas/settings.py`:

```python
"pinforge_web.tenancy.ActiveOrganizationMiddleware",
```

- [ ] **Step 7: Run service, tenancy, type, and regression tests**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest \
  tests/saas/test_services.py tests/saas/test_tenancy.py -v
/tmp/pinforge-venv/bin/mypy src/pinforge_web src/pinforge_saas
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest -q
```

Expected: 4 new tests pass, mypy passes, and the complete test suite remains green.

- [ ] **Step 8: Commit registration and tenancy boundaries**

```bash
git add src/pinforge_web/services.py src/pinforge_web/tenancy.py \
  src/pinforge_saas/settings.py tests/saas/test_services.py tests/saas/test_tenancy.py
git commit -m "feat: enforce SaaS tenant boundaries"
```

---

### Task 4: Add Signup, Login, and Tenant Dashboard

**Files:**
- Create: `src/pinforge_web/forms.py`
- Modify: `src/pinforge_web/views.py`
- Create: `src/pinforge_web/urls.py`
- Modify: `src/pinforge_saas/urls.py`
- Modify: `src/pinforge_saas/settings.py`
- Create: `templates/base.html`
- Create: `templates/registration/login.html`
- Create: `templates/registration/signup.html`
- Create: `templates/pinforge_web/dashboard.html`
- Create: `tests/saas/test_auth.py`

**Interfaces:**
- Consumes: `register_account`, Django authentication, and `request.organization` from Task 3.
- Produces: routes `signup`, `login`, `logout`, and `dashboard`; session key `active_organization_id`; an authenticated tenant landing page.

- [ ] **Step 1: Write failing signup and dashboard tests**

Create `tests/saas/test_auth.py`:

```python
from __future__ import annotations

import pytest
from django.urls import reverse

from pinforge_web.models import Membership, Organization, User


@pytest.mark.django_db
def test_signup_creates_owner_logs_in_and_redirects(client) -> None:
    response = client.post(reverse("signup"), {
        "email": "owner@example.com",
        "organization_name": "Example Shop",
        "timezone": "Europe/Istanbul",
        "password1": "strong-pass-123",
        "password2": "strong-pass-123",
    })
    assert response.status_code == 302
    assert response.url == reverse("dashboard")
    user = User.objects.get(email="owner@example.com")
    membership = Membership.objects.get(user=user)
    assert membership.role == Membership.Role.OWNER
    assert client.session["active_organization_id"] == str(membership.organization_id)


@pytest.mark.django_db
def test_signup_rejects_duplicate_email_without_creating_organization(client) -> None:
    User.objects.create_user(email="owner@example.com", password="strong-pass-123")
    response = client.post(reverse("signup"), {
        "email": "OWNER@example.com",
        "organization_name": "Duplicate Shop",
        "timezone": "UTC",
        "password1": "strong-pass-123",
        "password2": "strong-pass-123",
    })
    assert response.status_code == 200
    assert Organization.objects.count() == 0


@pytest.mark.django_db
def test_dashboard_requires_login(client) -> None:
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert reverse("login") in response.url


@pytest.mark.django_db
def test_dashboard_shows_only_active_organization(client) -> None:
    user = User.objects.create_user(email="owner@example.com", password="strong-pass-123")
    allowed = Organization.objects.create(name="Allowed Shop")
    Organization.objects.create(name="Forbidden Shop")
    Membership.objects.create(user=user, organization=allowed, role=Membership.Role.OWNER)
    client.force_login(user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert b"Allowed Shop" in response.content
    assert b"Forbidden Shop" not in response.content
```

- [ ] **Step 2: Run the auth tests and verify missing routes**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_auth.py -v
```

Expected: URL reversal fails because `signup` and `dashboard` do not exist.

- [ ] **Step 3: Implement the signup form**

Create `src/pinforge_web/forms.py`:

```python
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth.password_validation import validate_password

from pinforge_web.models import User


class SignUpForm(forms.Form):
    email = forms.EmailField(max_length=254)
    organization_name = forms.CharField(max_length=120, label="Shop name")
    timezone = forms.CharField(max_length=64, initial="UTC")
    password1 = forms.CharField(widget=forms.PasswordInput, validators=(validate_password,))
    password2 = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self) -> str:
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_timezone(self) -> str:
        timezone = self.cleaned_data["timezone"].strip()
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise forms.ValidationError("Enter a valid IANA timezone.") from error
        return timezone

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") != cleaned.get("password2"):
            self.add_error("password2", "Passwords do not match.")
        return cleaned
```

- [ ] **Step 4: Implement signup and dashboard views and URLs**

Add to `src/pinforge_web/views.py`:

```python
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render

from pinforge_web.forms import SignUpForm
from pinforge_web.services import register_account


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        registration = register_account(
            email=form.cleaned_data["email"],
            password=form.cleaned_data["password1"],
            organization_name=form.cleaned_data["organization_name"],
            timezone=form.cleaned_data["timezone"],
        )
        login(request, registration.user)
        request.session["active_organization_id"] = str(registration.organization.id)
        return redirect("dashboard")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def dashboard(request):
    if request.organization is None:
        return HttpResponse("No organization membership.", status=403)
    return render(request, "pinforge_web/dashboard.html", {"organization": request.organization})
```

Create `src/pinforge_web/urls.py`:

```python
from django.urls import path

from pinforge_web import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("", views.dashboard, name="dashboard"),
]
```

Extend `src/pinforge_saas/urls.py`:

```python
from django.urls import include

urlpatterns += [
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("pinforge_web.urls")),
]
```

Add settings:

```python
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
```

- [ ] **Step 5: Add minimal accessible templates**

Create `templates/base.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}PinForge{% endblock %}</title>
  </head>
  <body>
    <a href="#main">Skip to content</a>
    {% if messages %}
      <ul aria-label="Messages">
        {% for message in messages %}<li>{{ message }}</li>{% endfor %}
      </ul>
    {% endif %}
    <main id="main">{% block content %}{% endblock %}</main>
  </body>
</html>
```

Create `templates/registration/login.html`:

```html
{% extends "base.html" %}
{% block title %}Log in · PinForge{% endblock %}
{% block content %}
  <h1>Log in</h1>
  <form method="post">
    {% csrf_token %}
    {{ form.as_p }}
    <button type="submit">Log in</button>
  </form>
  <p><a href="{% url 'signup' %}">Create an account</a></p>
{% endblock %}
```

Create `templates/registration/signup.html`:

```html
{% extends "base.html" %}
{% block title %}Create account · PinForge{% endblock %}
{% block content %}
  <h1>Create your PinForge workspace</h1>
  <form method="post">
    {% csrf_token %}
    {{ form.as_p }}
    <button type="submit">Create workspace</button>
  </form>
  <p><a href="{% url 'login' %}">Already have an account?</a></p>
{% endblock %}
```

Create `templates/pinforge_web/dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}{{ organization.name }} · PinForge{% endblock %}
{% block content %}
  <h1>{{ organization.name }}</h1>
  <p>Your Etsy-to-Pinterest workspace is ready.</p>
{% endblock %}
```

- [ ] **Step 6: Run auth, CSRF, tenancy, and regression tests**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest \
  tests/saas/test_auth.py tests/saas/test_tenancy.py -v
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest -q
```

Expected: 6 focused tests pass and the complete suite remains green.

- [ ] **Step 7: Commit the first customer-facing vertical slice**

```bash
git add src/pinforge_web/forms.py src/pinforge_web/views.py src/pinforge_web/urls.py \
  src/pinforge_saas/urls.py src/pinforge_saas/settings.py templates tests/saas/test_auth.py
git commit -m "feat: add SaaS signup and tenant dashboard"
```

---

### Task 5: Enforce Production Configuration and Add PostgreSQL Development Service

**Files:**
- Modify: `src/pinforge_saas/settings.py`
- Create: `tests/saas/test_settings.py`
- Create: `.env.example`
- Create: `docker-compose.saas.yml`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: environment-driven settings from Task 1.
- Produces: `validate_production_settings(...)`, secure production defaults, and a reproducible PostgreSQL 17 service contract.

- [ ] **Step 1: Write failing production-setting tests**

Create `tests/saas/test_settings.py`:

```python
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
```

- [ ] **Step 2: Run the setting tests and verify the missing-function failure**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_settings.py -v
```

Expected: import fails because `validate_production_settings` is undefined.

- [ ] **Step 3: Implement production validation and security defaults**

Add to `src/pinforge_saas/settings.py` before constructing `DATABASES`:

```python
from django.core.exceptions import ImproperlyConfigured

DEV_SECRET_KEY = "pinforge-dev-only-secret"


def validate_production_settings(
    *, debug: bool, secret_key: str, allowed_hosts: list[str], database_engine: str
) -> None:
    if debug:
        return
    if secret_key == DEV_SECRET_KEY:
        raise ImproperlyConfigured("PINFORGE_SECRET_KEY must be set in production")
    if not allowed_hosts:
        raise ImproperlyConfigured("PINFORGE_ALLOWED_HOSTS must be set in production")
    if database_engine != "django.db.backends.postgresql":
        raise ImproperlyConfigured("PostgreSQL is required in production")
```

Use `DEV_SECRET_KEY` as the development fallback and call the function after `DATABASES` is built. Add:

```python
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
```

- [ ] **Step 4: Add the local PostgreSQL contract**

Create `.env.example`:

```dotenv
PINFORGE_DEBUG=1
PINFORGE_SECRET_KEY=replace-for-non-local-use
PINFORGE_ALLOWED_HOSTS=localhost,127.0.0.1
PINFORGE_DB_NAME=pinforge
PINFORGE_DB_USER=pinforge
PINFORGE_DB_PASSWORD=pinforge-local-only
PINFORGE_DB_HOST=127.0.0.1
PINFORGE_DB_PORT=5432
```

Create `docker-compose.saas.yml`:

```yaml
services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: pinforge
      POSTGRES_USER: pinforge
      POSTGRES_PASSWORD: pinforge-local-only
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pinforge -d pinforge"]
      interval: 5s
      timeout: 3s
      retries: 10
    volumes:
      - pinforge-postgres:/var/lib/postgresql/data

volumes:
  pinforge-postgres:
```

Add `.env` to `.gitignore`; do not ignore `.env.example`.

- [ ] **Step 5: Run security-setting and deployment checks**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest tests/saas/test_settings.py -v
PINFORGE_DEBUG=1 PINFORGE_TEST_SQLITE=1 \
  /tmp/pinforge-venv/bin/python manage.py check
PINFORGE_DEBUG=0 PINFORGE_SECRET_KEY=production-secret-value \
PINFORGE_ALLOWED_HOSTS=pinforge.example PINFORGE_DB_PASSWORD=test-only \
  /tmp/pinforge-venv/bin/python manage.py check --deploy
```

Expected: 3 tests pass; normal check has no errors; deploy check has no high-severity security error. A database connection is not opened by `check --deploy`.

- [ ] **Step 6: Commit the production configuration boundary**

```bash
git add src/pinforge_saas/settings.py tests/saas/test_settings.py \
  .env.example docker-compose.saas.yml .gitignore
git commit -m "feat: secure SaaS production configuration"
```

---

### Task 6: Run SaaS Tests on PostgreSQL in CI

**Files:**
- Modify: `.github/workflows/quality.yml:10-47`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-02-pinforge-saas-design.md`

**Interfaces:**
- Consumes: PostgreSQL settings, migrations, and complete test suite from Tasks 1-5.
- Produces: a pull-request gate that runs Django migrations/tests against PostgreSQL and documented local commands.

- [ ] **Step 1: Add a PostgreSQL service to the backend CI job**

Under `jobs.backend` in `.github/workflows/quality.yml`, add:

```yaml
    services:
      postgres:
        image: postgres:17-alpine
        env:
          POSTGRES_DB: pinforge_test
          POSTGRES_USER: pinforge
          POSTGRES_PASSWORD: pinforge-ci-only
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U pinforge -d pinforge_test"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      PINFORGE_DEBUG: "1"
      PINFORGE_SECRET_KEY: pinforge-ci-only-secret
      PINFORGE_ALLOWED_HOSTS: localhost,testserver
      PINFORGE_DB_NAME: pinforge_test
      PINFORGE_DB_USER: pinforge
      PINFORGE_DB_PASSWORD: pinforge-ci-only
      PINFORGE_DB_HOST: 127.0.0.1
      PINFORGE_DB_PORT: "5432"
```

Do not set `PINFORGE_TEST_SQLITE` in CI.

- [ ] **Step 2: Add migration and deployment checks before tests**

After dependency installation in the backend job, add:

```yaml
      - name: Check migrations
        run: python manage.py makemigrations --check --dry-run
      - name: Apply migrations
        run: python manage.py migrate --noinput
      - name: Django configuration
        env:
          PINFORGE_DEBUG: "0"
        run: python manage.py check --deploy
```

Keep the existing lint, mypy, coverage, and dependency-audit steps. The coverage command must execute all desktop and SaaS tests against the PostgreSQL service.

- [ ] **Step 3: Add local SaaS instructions to the README**

Document these exact commands under a new `SaaS development` section:

```bash
docker compose -f docker-compose.saas.yml up -d postgres
set -a && . ./.env.example && set +a
python manage.py migrate
python manage.py runserver
```

Also document the sandbox-only fallback:

```bash
PINFORGE_TEST_SQLITE=1 pytest tests/saas
```

State that production and CI use PostgreSQL and link the SaaS design and foundation plan.

- [ ] **Step 4: Run the full local verification matrix**

Run:

```bash
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/pytest --cov=pinforge --cov=pinforge_web \
  --cov=pinforge_saas --cov-report=term
/tmp/pinforge-venv/bin/ruff --version | rg '^ruff 0\.11\.13$'
/tmp/pinforge-venv/bin/ruff check src tests
/tmp/pinforge-venv/bin/mypy src/pinforge src/pinforge_web src/pinforge_saas
XDG_CACHE_HOME=/tmp/pinforge-audit-cache \
  /tmp/pinforge-venv/bin/pip-audit -r requirements.lock
PINFORGE_TEST_SQLITE=1 /tmp/pinforge-venv/bin/python manage.py \
  makemigrations --check --dry-run
```

Expected: all tests pass, coverage remains at least 70%, Ruff passes at 0.11.13, mypy passes, no known dependency vulnerability is reported, and migrations are current.

- [ ] **Step 5: Validate the workflow syntax and inspect the final diff**

Run:

```bash
python -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('.github/workflows/quality.yml').read_text())"
git diff --check
git status --short
```

If PyYAML is unavailable, replace only the first command with:

```bash
ruby -e "require 'yaml'; YAML.load_file('.github/workflows/quality.yml')"
```

Expected: YAML parses, `git diff --check` is silent, and only the planned files are modified.

- [ ] **Step 6: Commit the CI-complete foundation**

```bash
git add .github/workflows/quality.yml README.md \
  docs/superpowers/specs/2026-08-02-pinforge-saas-design.md
git commit -m "ci: verify SaaS foundation on PostgreSQL"
```

- [ ] **Step 7: Push the branch and open a dependent draft PR**

Push `agent/pinforge-saas-foundation` and create a draft PR with:

- Base: `agent/pinforge-desktop-v0-2-0` until PR #1 merges.
- Title: `feat: bootstrap PinForge SaaS foundation`.
- Body: summarize Django/PostgreSQL, tenant isolation, signup, security settings,
  local verification, and the dependency on PR #1.

Monitor the PostgreSQL CI job. If it fails, inspect the exact job log, fix the
branch, rerun the full affected local checks, and update the same PR.

---

## Self-Review Record

- Spec coverage for this sub-project: Django monolith, PostgreSQL, accounts,
  organizations, membership roles, tenant-scoped lookups, sessions, CSRF, health,
  secure production defaults, local development, migrations, and CI are assigned.
- Deferred to separate approved sub-project plans: object storage/render adapter,
  catalog/manual import, Etsy OAuth/synchronization, durable jobs, Pinterest,
  Paddle/usage ledger, analytics, browser smoke, backup/restore, and alpha release.
- Type contract check: `register_account` returns `Registration`; middleware sets
  `request.organization` and `request.membership`; all later snippets use those exact
  names. Route names remain `health-live`, `health-ready`, `signup`, `login`,
  `logout`, and `dashboard` throughout.
- Placeholder scan: no unresolved implementation placeholders remain in this plan.
