from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from pinforge_web.models import Membership, Organization, User


@pytest.mark.django_db
def test_user_uses_normalized_email_as_identity() -> None:
    user = User.objects.create_user(
        email="OWNER@Example.COM", password="strong-pass-123"
    )
    assert user.email == "owner@example.com"
    assert user.username is None
    assert user.check_password("strong-pass-123")


@pytest.mark.django_db
def test_user_requires_email() -> None:
    with pytest.raises(ValueError, match="email"):
        User.objects.create_user(email="", password="strong-pass-123")


@pytest.mark.django_db
def test_membership_is_unique_per_user_and_organization() -> None:
    user = User.objects.create_user(
        email="owner@example.com", password="strong-pass-123"
    )
    organization = Organization.objects.create(
        name="Example Shop", timezone="Europe/Istanbul"
    )
    Membership.objects.create(
        user=user,
        organization=organization,
        role=Membership.Role.OWNER,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.create(
            user=user,
            organization=organization,
            role=Membership.Role.MEMBER,
        )
