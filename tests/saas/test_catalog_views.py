from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from pinforge_web.models import (
    BrandKit,
    Listing,
    Membership,
    Organization,
    Shop,
    User,
)


def image_upload(name: str = "product.png") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (20, 30), "#173C35").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def private_storage_settings(root: Path) -> dict[str, dict[str, object]]:
    return {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": root, "base_url": None},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


def login_owner(client: Client, organization: Organization, email: str) -> User:
    user = User.objects.create_user(email=email, password="strong-pass-123")
    Membership.objects.create(
        user=user,
        organization=organization,
        role=Membership.Role.OWNER,
    )
    client.force_login(user)
    session = client.session
    session["active_organization_id"] = str(organization.id)
    session.save()
    return user


def create_listing(organization: Organization, title: str) -> Listing:
    shop, _ = Shop.objects.get_or_create(
        organization=organization,
        source=Shop.Source.MANUAL,
        source_shop_id="manual",
        defaults={"name": "Manual catalog"},
    )
    return Listing.objects.create(
        organization=organization,
        shop=shop,
        source_listing_id=f"manual:{title}",
        title=title,
        listing_url="https://www.etsy.com/listing/123/example",
        price_minor=1299,
        currency="USD",
    )


@pytest.mark.django_db
def test_catalog_requires_login(client: Client) -> None:
    response = client.get(reverse("listing-list"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


@pytest.mark.django_db
def test_listing_create_requires_csrf(tmp_path: Path) -> None:
    client = Client(enforce_csrf_checks=True)
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        response = client.post(
            reverse("listing-create"),
            {
                "title": "Welcome book",
                "listing_url": "https://www.etsy.com/listing/123/example",
                "price": "12.99",
                "currency": "USD",
                "tags": "airbnb, welcome book",
                "description": "Useful download",
                "vertical": "hospitality",
                "images": image_upload(),
            },
        )

    assert response.status_code == 403
    assert not Listing.objects.filter(organization=organization).exists()


@pytest.mark.django_db
def test_owner_can_create_listing_with_multiple_images(
    client: Client,
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        response = client.post(
            reverse("listing-create"),
            {
                "title": "Welcome book",
                "listing_url": "https://www.etsy.com/listing/123/example",
                "price": "12.99",
                "currency": "usd",
                "tags": "airbnb, welcome book",
                "description": "Useful download",
                "vertical": "hospitality",
                "images": [image_upload("one.png"), image_upload("two.png")],
            },
        )

    listing = Listing.objects.get(organization=organization)
    assert response.status_code == 302
    assert response.url == reverse("listing-detail", args=(listing.id,))
    assert listing.images.count() == 2


@pytest.mark.django_db
def test_listing_form_shows_validation_without_creating_row(
    client: Client,
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        response = client.post(
            reverse("listing-create"),
            {
                "title": "Welcome book",
                "listing_url": "not-a-url",
                "price": "12.99",
                "currency": "USD",
                "tags": "airbnb",
                "vertical": "hospitality",
                "images": image_upload(),
            },
        )

    assert response.status_code == 200
    assert b"Enter a valid URL" in response.content
    assert not Listing.objects.filter(organization=organization).exists()


@pytest.mark.django_db
def test_listing_pages_only_expose_active_tenant(client: Client) -> None:
    allowed = Organization.objects.create(name="Allowed")
    forbidden = Organization.objects.create(name="Forbidden")
    login_owner(client, allowed, "owner@example.com")
    own_listing = create_listing(allowed, "Own listing")
    other_listing = create_listing(forbidden, "Secret listing")

    listing_response = client.get(reverse("listing-list"))
    forbidden_response = client.get(
        reverse("listing-detail", args=(other_listing.id,))
    )

    assert listing_response.status_code == 200
    assert own_listing.title.encode() in listing_response.content
    assert other_listing.title.encode() not in listing_response.content
    assert forbidden_response.status_code == 404


@pytest.mark.django_db
def test_owner_can_save_tenant_brand_kit(client: Client) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")

    response = client.post(
        reverse("brand-kit"),
        {
            "shop_name": "ECOVIA",
            "primary": "#173C35",
            "accent": "#C9855B",
            "surface": "#F4F0E6",
            "ink": "#18201D",
            "headline_font": "DejaVuSerif-Bold.ttf",
            "body_font": "DejaVuSans.ttf",
            "body_bold_font": "DejaVuSans-Bold.ttf",
        },
    )

    brand = BrandKit.objects.get(organization=organization)
    assert response.status_code == 302
    assert response.url == reverse("brand-kit")
    assert brand.shop_name == "ECOVIA"
    assert brand.primary == "#173C35"


@pytest.mark.django_db
def test_brand_kit_rejects_invalid_color(client: Client) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")

    response = client.post(
        reverse("brand-kit"),
        {
            "shop_name": "ECOVIA",
            "primary": "green",
            "accent": "#C9855B",
            "surface": "#F4F0E6",
            "ink": "#18201D",
            "headline_font": "DejaVuSerif-Bold.ttf",
            "body_font": "DejaVuSans.ttf",
            "body_bold_font": "DejaVuSans-Bold.ttf",
        },
    )

    assert response.status_code == 200
    assert b"six-digit hex color" in response.content
    assert not BrandKit.objects.filter(organization=organization).exists()
