from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar
from uuid import UUID

from django.db.models import Model
from django.http import HttpRequest, HttpResponseBase
from django.shortcuts import get_object_or_404

from pinforge_web.models import Membership, Organization

TenantModel = TypeVar("TenantModel", bound=Model)


class ActiveOrganizationMiddleware:
    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponseBase],
    ) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        request.organization = None
        request.membership = None
        if request.user.is_authenticated:
            memberships = Membership.objects.filter(user=request.user).select_related(
                "organization"
            )
            requested_id = _valid_organization_id(
                request.session.get("active_organization_id")
            )
            membership = (
                memberships.filter(organization_id=requested_id).first()
                if requested_id is not None
                else None
            )
            membership = membership or memberships.order_by(
                "created_at", "pk"
            ).first()
            if membership is not None:
                request.membership = membership
                request.organization = membership.organization
                request.session["active_organization_id"] = str(
                    membership.organization_id
                )
        return self.get_response(request)


def get_tenant_object_or_404(
    model: type[TenantModel],
    organization: Organization,
    **lookup: Any,
) -> TenantModel:
    return get_object_or_404(model, organization=organization, **lookup)


def _valid_organization_id(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None
