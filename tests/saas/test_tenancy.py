from __future__ import annotations

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory

from pinforge_web.models import Membership, Organization, User
from pinforge_web.tenancy import (
    ActiveOrganizationMiddleware,
    get_tenant_object_or_404,
)


@pytest.mark.django_db
def test_middleware_selects_only_an_authenticated_membership(
    rf: RequestFactory,
) -> None:
    user = User.objects.create_user(
        email="owner@example.com", password="strong-pass-123"
    )
    organization = Organization.objects.create(name="Example Shop")
    membership = Membership.objects.create(
        user=user,
        organization=organization,
        role=Membership.Role.OWNER,
    )
    request = rf.get("/")
    request.user = user
    request.session = {"active_organization_id": str(organization.id)}

    ActiveOrganizationMiddleware(lambda _: HttpResponse())(request)

    assert request.organization == organization
    assert request.membership == membership


@pytest.mark.django_db
def test_middleware_ignores_an_organization_without_membership(
    rf: RequestFactory,
) -> None:
    user = User.objects.create_user(
        email="owner@example.com", password="strong-pass-123"
    )
    allowed = Organization.objects.create(name="Allowed")
    forbidden = Organization.objects.create(name="Forbidden")
    Membership.objects.create(
        user=user,
        organization=allowed,
        role=Membership.Role.OWNER,
    )
    request = rf.get("/")
    request.user = user
    request.session = {"active_organization_id": str(forbidden.id)}

    ActiveOrganizationMiddleware(lambda _: HttpResponse())(request)

    assert request.organization == allowed


@pytest.mark.django_db
def test_tenant_lookup_returns_404_for_another_organization() -> None:
    user = User.objects.create_user(
        email="owner@example.com", password="strong-pass-123"
    )
    allowed = Organization.objects.create(name="Allowed")
    forbidden = Organization.objects.create(name="Forbidden")
    membership = Membership.objects.create(
        user=user,
        organization=forbidden,
        role=Membership.Role.OWNER,
    )

    with pytest.raises(Http404):
        get_tenant_object_or_404(Membership, allowed, pk=membership.pk)
