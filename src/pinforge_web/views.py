from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connections
from django.db.utils import IntegrityError
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from pinforge_web.catalog import CatalogValidationError, create_manual_listing
from pinforge_web.creatives import (
    CreativeValidationError,
    create_or_enqueue_creative,
)
from pinforge_web.forms import BrandKitForm, CreativeForm, ListingForm, SignUpForm
from pinforge_web.models import BrandKit, Creative, CreativeAsset, Listing, Organization
from pinforge_web.services import register_account
from pinforge_web.tenancy import get_tenant_object_or_404


@require_GET
def health_live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request: HttpRequest) -> JsonResponse:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


@require_http_methods(["GET", "POST"])
def signup(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            registration = register_account(
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password1"],
                organization_name=form.cleaned_data["organization_name"],
                timezone=form.cleaned_data["timezone"],
            )
        except IntegrityError:
            form.add_error(
                "email",
                "An account with this email already exists.",
            )
        else:
            login(request, registration.user)
            request.session["active_organization_id"] = str(
                registration.organization.id
            )
            return redirect("dashboard")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    if request.organization is None:
        return HttpResponse("No organization membership.", status=403)
    return render(
        request,
        "pinforge_web/dashboard.html",
        {"organization": request.organization},
    )


@login_required
def listing_list(request: HttpRequest) -> HttpResponse:
    organization = _active_organization(request)
    listings = (
        Listing.objects.filter(organization=organization, active=True)
        .select_related("shop")
        .prefetch_related("images")
    )
    return render(
        request,
        "pinforge_web/listing_list.html",
        {"listings": listings},
    )


@login_required
@require_http_methods(["GET", "POST"])
def listing_create(request: HttpRequest) -> HttpResponse:
    organization = _active_organization(request)
    form = ListingForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = create_manual_listing(
                organization=organization,
                title=form.cleaned_data["title"],
                listing_url=form.cleaned_data["listing_url"],
                price=form.cleaned_data["price"],
                currency=form.cleaned_data["currency"],
                tags=form.cleaned_data["tags"],
                description=form.cleaned_data["description"],
                vertical=form.cleaned_data["vertical"],
                images=form.cleaned_data["images"],
            )
        except CatalogValidationError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Listing added to your private catalog.")
            return redirect("listing-detail", listing_id=result.listing.id)
    return render(
        request,
        "pinforge_web/listing_form.html",
        {"form": form},
    )


@login_required
def listing_detail(request: HttpRequest, listing_id: object) -> HttpResponse:
    organization = _active_organization(request)
    listing = get_tenant_object_or_404(
        Listing,
        organization,
        pk=listing_id,
        active=True,
    )
    return _render_listing_detail(request, listing)


@login_required
@require_http_methods(["GET", "POST"])
def brand_kit(request: HttpRequest) -> HttpResponse:
    organization = _active_organization(request)
    instance = BrandKit.objects.filter(organization=organization).first()
    form = BrandKitForm(
        request.POST or None,
        instance=instance,
        initial={"shop_name": organization.name},
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.organization = organization
        saved.save()
        messages.success(request, "Brand kit saved.")
        return redirect("brand-kit")
    return render(
        request,
        "pinforge_web/brand_kit.html",
        {"form": form},
    )


@login_required
@require_POST
def creative_create(request: HttpRequest, listing_id: object) -> HttpResponse:
    organization = _active_organization(request)
    listing = get_tenant_object_or_404(
        Listing,
        organization,
        pk=listing_id,
        active=True,
    )
    form = CreativeForm(request.POST)
    if form.is_valid():
        try:
            result = create_or_enqueue_creative(
                organization=organization,
                listing=listing,
                template_id=form.cleaned_data["template_id"],
                title=form.cleaned_data["title"],
                description=form.cleaned_data["description"],
                alt_text=form.cleaned_data["alt_text"],
            )
        except CreativeValidationError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Creative queued for rendering.")
            return redirect("creative-detail", creative_id=result.creative.id)
    return _render_listing_detail(request, listing, creative_form=form)


@login_required
def creative_detail(request: HttpRequest, creative_id: object) -> HttpResponse:
    organization = _active_organization(request)
    creative = get_tenant_object_or_404(
        Creative,
        organization,
        pk=creative_id,
    )
    asset = CreativeAsset.objects.filter(
        organization=organization,
        creative=creative,
    ).first()
    return render(
        request,
        "pinforge_web/creative_detail.html",
        {"creative": creative, "asset": asset},
    )


@login_required
def creative_download(request: HttpRequest, creative_id: object) -> FileResponse:
    organization = _active_organization(request)
    creative = get_tenant_object_or_404(
        Creative,
        organization,
        pk=creative_id,
        status=Creative.Status.READY,
    )
    asset = CreativeAsset.objects.filter(
        organization=organization,
        creative=creative,
    ).first()
    if asset is None or not asset.file.storage.exists(asset.file.name):
        raise Http404
    try:
        handle = asset.file.open("rb")
    except OSError as error:
        raise Http404 from error
    response = FileResponse(
        handle,
        as_attachment=True,
        filename=f"pinforge-{creative.id}.png",
        content_type=asset.mime_type,
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Length"] = str(asset.byte_size)
    return response


def _render_listing_detail(
    request: HttpRequest,
    listing: Listing,
    *,
    creative_form: CreativeForm | None = None,
) -> HttpResponse:
    form = creative_form or CreativeForm(
        initial={
            "template_id": Creative.Template.TEXT_OVERLAY,
            "title": listing.title[:100],
            "description": listing.description[:500],
            "alt_text": f"Pinterest preview of {listing.title}"[:500],
        }
    )
    creatives = Creative.objects.filter(
        organization=listing.organization,
        listing=listing,
    )
    return render(
        request,
        "pinforge_web/listing_detail.html",
        {
            "listing": listing,
            "creative_form": form,
            "creatives": creatives,
        },
    )


def _active_organization(request: HttpRequest) -> Organization:
    if request.organization is None:
        raise PermissionDenied("No organization membership.")
    return request.organization
