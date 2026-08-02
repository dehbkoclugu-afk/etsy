from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction

from pinforge_web.models import Membership, Organization, User


@dataclass(frozen=True, slots=True)
class Registration:
    user: User
    organization: Organization
    membership: Membership


@transaction.atomic
def register_account(
    *,
    email: str,
    password: str,
    organization_name: str,
    timezone: str,
) -> Registration:
    normalized_name = organization_name.strip()
    if not normalized_name:
        raise ValueError("organization name is required")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError("timezone must be a valid IANA name") from error

    user = User.objects.create_user(email=email, password=password)
    organization = Organization.objects.create(
        name=normalized_name,
        timezone=timezone,
    )
    membership = Membership.objects.create(
        user=user,
        organization=organization,
        role=Membership.Role.OWNER,
    )
    return Registration(user, organization, membership)
