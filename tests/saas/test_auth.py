from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from pinforge_web.models import Membership, Organization, User


@pytest.mark.django_db
def test_signup_creates_owner_logs_in_and_redirects(client) -> None:
    response = client.post(
        reverse("signup"),
        {
            "email": "owner@example.com",
            "organization_name": "Example Shop",
            "timezone": "Europe/Istanbul",
            "password1": "strong-pass-123",
            "password2": "strong-pass-123",
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("dashboard")
    user = User.objects.get(email="owner@example.com")
    membership = Membership.objects.get(user=user)
    assert membership.role == Membership.Role.OWNER
    assert client.session["active_organization_id"] == str(
        membership.organization_id
    )


@pytest.mark.django_db
def test_signup_rejects_duplicate_email_without_creating_organization(client) -> None:
    User.objects.create_user(
        email="owner@example.com", password="strong-pass-123"
    )
    response = client.post(
        reverse("signup"),
        {
            "email": "OWNER@example.com",
            "organization_name": "Duplicate Shop",
            "timezone": "UTC",
            "password1": "strong-pass-123",
            "password2": "strong-pass-123",
        },
    )
    assert response.status_code == 200
    assert b"already exists" in response.content
    assert Organization.objects.count() == 0


@pytest.mark.django_db
def test_signup_requires_csrf() -> None:
    client = Client(enforce_csrf_checks=True)
    response = client.post(
        reverse("signup"),
        {
            "email": "owner@example.com",
            "organization_name": "Example Shop",
            "timezone": "UTC",
            "password1": "strong-pass-123",
            "password2": "strong-pass-123",
        },
    )
    assert response.status_code == 403
    assert not User.objects.exists()


@pytest.mark.django_db
def test_dashboard_requires_login(client) -> None:
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert reverse("login") in response.url


@pytest.mark.django_db
def test_dashboard_rejects_authenticated_user_without_membership(client) -> None:
    user = User.objects.create_user(
        email="owner@example.com", password="strong-pass-123"
    )
    client.force_login(user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_dashboard_shows_only_active_organization(client) -> None:
    user = User.objects.create_user(
        email="owner@example.com", password="strong-pass-123"
    )
    allowed = Organization.objects.create(name="Allowed Shop")
    Organization.objects.create(name="Forbidden Shop")
    Membership.objects.create(
        user=user,
        organization=allowed,
        role=Membership.Role.OWNER,
    )
    client.force_login(user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert b"Allowed Shop" in response.content
    assert b"Forbidden Shop" not in response.content
