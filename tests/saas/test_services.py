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
    with patch.object(
        Membership.objects,
        "create",
        side_effect=RuntimeError("failure"),
    ):
        with pytest.raises(RuntimeError, match="failure"):
            register_account(
                email="owner@example.com",
                password="strong-pass-123",
                organization_name="Example Shop",
                timezone="UTC",
            )
    assert not User.objects.exists()
    assert not Organization.objects.exists()
