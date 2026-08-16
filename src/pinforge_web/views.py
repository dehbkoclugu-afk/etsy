from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.conf import settings
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
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from pinforge.integrations.etsy.oauth import EtsyOAuth
from pinforge.integrations.http import ApiError
from pinforge.integrations.oauth import parse_callback
from pinforge.integrations.pinterest.client import PinterestClient
from pinforge.integrations.pinterest.oauth import PinterestOAuth
from pinforge_web.catalog import CatalogValidationError, create_manual_listing
from pinforge_web.creatives import (
    CreativeValidationError,
    create_or_enqueue_creative,
)
from pinforge_web.etsy_connections import save_etsy_connection
from pinforge_web.etsy_sync import enqueue_etsy_sync
from pinforge_web.forms import (
    BrandKitForm,
    CreativeForm,
    ListingForm,
    PinPublicationForm,
    SignUpForm,
)
from pinforge_web.models import (
    BrandKit,
    Creative,
    CreativeAsset,
    Listing,
    Organization,
    PinPublication,
    ProviderConnection,
)
from pinforge_web.oauth_sessions import deserialize_attempt, serialize_attempt
from pinforge_web.pinterest_connections import (
    save_pinterest_connection,
    sync_pinterest_boards,
)
from pinforge_web.publishing import enqueue_pinterest_publication
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
@require_GET
def etsy_connection(request: HttpRequest) -> HttpResponse:
    organization = _active_organization(request)
    connections = ProviderConnection.objects.filter(
        organization=organization,
        provider=ProviderConnection.Provider.ETSY,
        active=True,
    )
    return render(
        request,
        "pinforge_web/etsy_connection.html",
        {"connections": connections, "configured": _etsy_is_configured()},
    )


@login_required
@require_POST
def etsy_connection_start(request: HttpRequest) -> HttpResponse:
    _active_organization(request)
    if not _etsy_is_configured():
        messages.error(request, "Etsy OAuth is not configured on this server.")
        return redirect("etsy-connection")
    oauth = EtsyOAuth(settings.ETSY_KEYSTRING)
    try:
        attempt = oauth.begin(settings.ETSY_REDIRECT_URI)
    finally:
        oauth.close()
    request.session["etsy_oauth_attempt"] = serialize_attempt(attempt)
    return redirect(attempt.authorization_url)


@login_required
@require_GET
def etsy_connection_callback(request: HttpRequest) -> HttpResponse:
    organization = _active_organization(request)
    raw_attempt = request.session.pop("etsy_oauth_attempt", None)
    try:
        attempt = deserialize_attempt(raw_attempt, provider="etsy")
        code = parse_callback(request.GET.urlencode(), attempt.state)
        oauth = EtsyOAuth(settings.ETSY_KEYSTRING)
        try:
            token = oauth.exchange(code, attempt)
        finally:
            oauth.close()
        save_etsy_connection(organization=organization, token=token)
    except (ApiError, ValueError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Etsy account connected securely.")
    return redirect("etsy-connection")


@login_required
@require_POST
def etsy_connection_disconnect(
    request: HttpRequest, connection_id: object
) -> HttpResponse:
    organization = _active_organization(request)
    connection = get_tenant_object_or_404(
        ProviderConnection,
        organization,
        pk=connection_id,
        provider=ProviderConnection.Provider.ETSY,
    )
    connection.delete()
    messages.success(request, "Etsy account disconnected.")
    return redirect("etsy-connection")


@login_required
@require_POST
def etsy_connection_sync(request: HttpRequest, connection_id: object) -> HttpResponse:
    organization = _active_organization(request)
    connection = get_tenant_object_or_404(
        ProviderConnection,
        organization,
        pk=connection_id,
        provider=ProviderConnection.Provider.ETSY,
        active=True,
    )
    enqueue_etsy_sync(organization=organization, connection=connection)
    messages.success(request, "Etsy listing sync queued.")
    return redirect("etsy-connection")


def _etsy_is_configured() -> bool:
    return bool(settings.ETSY_KEYSTRING and settings.ETSY_REDIRECT_URI)


@login_required
@require_GET
def pinterest_connection(request: HttpRequest) -> HttpResponse:
    organization = _active_organization(request)
    connections = ProviderConnection.objects.filter(
        organization=organization,
        provider=ProviderConnection.Provider.PINTEREST,
        active=True,
    ).prefetch_related("pinterest_boards")
    return render(
        request,
        "pinforge_web/pinterest_connection.html",
        {"connections": connections, "configured": _pinterest_is_configured()},
    )


@login_required
@require_POST
def pinterest_connection_start(request: HttpRequest) -> HttpResponse:
    _active_organization(request)
    if not _pinterest_is_configured():
        messages.error(request, "Pinterest OAuth is not configured on this server.")
        return redirect("pinterest-connection")
    oauth = PinterestOAuth(
        settings.PINTEREST_APP_ID,
        settings.PINTEREST_APP_SECRET,
    )
    try:
        attempt = oauth.begin(settings.PINTEREST_REDIRECT_URI)
    finally:
        oauth.close()
    request.session["pinterest_oauth_attempt"] = serialize_attempt(attempt)
    return redirect(attempt.authorization_url)


@login_required
@require_GET
def pinterest_connection_callback(request: HttpRequest) -> HttpResponse:
    organization = _active_organization(request)
    raw_attempt = request.session.pop("pinterest_oauth_attempt", None)
    try:
        attempt = deserialize_attempt(raw_attempt, provider="pinterest")
        code = parse_callback(request.GET.urlencode(), attempt.state)
        oauth = PinterestOAuth(
            settings.PINTEREST_APP_ID,
            settings.PINTEREST_APP_SECRET,
        )
        try:
            token = oauth.exchange(code, attempt)
        finally:
            oauth.close()
        client = PinterestClient(token.access_token)
        try:
            account = client.get_user_account()
        finally:
            client.close()
        account_id = str(account.get("id") or account.get("username") or "")
        connection = save_pinterest_connection(
            organization=organization,
            account_id=account_id,
            token=token,
        )
        sync_pinterest_boards(connection)
    except (ApiError, ValueError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Pinterest account and boards connected.")
    return redirect("pinterest-connection")


@login_required
@require_POST
def pinterest_connection_sync(
    request: HttpRequest, connection_id: object
) -> HttpResponse:
    organization = _active_organization(request)
    connection = get_tenant_object_or_404(
        ProviderConnection,
        organization,
        pk=connection_id,
        provider=ProviderConnection.Provider.PINTEREST,
        active=True,
    )
    count = sync_pinterest_boards(connection)
    messages.success(request, f"Synchronized {count} Pinterest boards.")
    return redirect("pinterest-connection")


@login_required
@require_POST
def pinterest_connection_disconnect(
    request: HttpRequest, connection_id: object
) -> HttpResponse:
    organization = _active_organization(request)
    connection = get_tenant_object_or_404(
        ProviderConnection,
        organization,
        pk=connection_id,
        provider=ProviderConnection.Provider.PINTEREST,
    )
    if PinPublication.objects.filter(connection=connection).exists():
        connection.active = False
        connection.save(update_fields=("active", "updated_at"))
    else:
        connection.delete()
    messages.success(request, "Pinterest account disconnected.")
    return redirect("pinterest-connection")


def _pinterest_is_configured() -> bool:
    return bool(
        settings.PINTEREST_APP_ID
        and settings.PINTEREST_APP_SECRET
        and settings.PINTEREST_REDIRECT_URI
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
        {
            "creative": creative,
            "asset": asset,
            "publication_form": PinPublicationForm(organization=organization),
            "publications": PinPublication.objects.filter(
                organization=organization,
                creative=creative,
            ).select_related("board"),
        },
    )


@login_required
@require_POST
def creative_publish(request: HttpRequest, creative_id: object) -> HttpResponse:
    organization = _active_organization(request)
    creative = get_tenant_object_or_404(
        Creative,
        organization,
        pk=creative_id,
    )
    form = PinPublicationForm(request.POST, organization=organization)
    if form.is_valid():
        try:
            enqueue_pinterest_publication(
                organization=organization,
                creative=creative,
                board=form.cleaned_data["board"],
                scheduled_at=form.cleaned_data.get("scheduled_at") or timezone.now(),
            )
        except ValueError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Pinterest publication queued.")
    else:
        messages.error(request, "Choose a valid Pinterest board and time.")
    return redirect("creative-detail", creative_id=creative.id)


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
