from __future__ import annotations

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connections
from django.db.utils import IntegrityError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from pinforge_web.forms import SignUpForm
from pinforge_web.services import register_account


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
