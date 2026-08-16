from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.test import Client, override_settings
from django.urls import reverse

from pinforge_web.models import (
    Creative,
    CreativeAsset,
    Job,
    Listing,
    ListingImage,
    Membership,
    Organization,
    Shop,
    User,
)


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


def listing_with_image(organization: Organization, title: str = "Welcome book") -> Listing:
    shop = Shop.objects.create(
        organization=organization,
        source=Shop.Source.MANUAL,
        source_shop_id="manual",
        name="Manual catalog",
    )
    listing = Listing.objects.create(
        organization=organization,
        shop=shop,
        source_listing_id=f"manual:{title}",
        title=title,
        listing_url="https://www.etsy.com/listing/123/example",
        price_minor=1299,
        currency="USD",
        tags=["airbnb"],
        description="Useful download",
        vertical="hospitality",
    )
    ListingImage.objects.create(
        organization=organization,
        listing=listing,
        file="private/source.png",
        position=0,
        mime_type="image/png",
        byte_size=100,
        width=20,
        height=30,
        checksum="a" * 64,
    )
    return listing


def creative_payload() -> dict[str, str]:
    return {
        "template_id": Creative.Template.TEXT_OVERLAY,
        "title": "A better guest welcome",
        "description": "Create a polished first impression.",
        "alt_text": "Botanical welcome book preview",
    }


@pytest.mark.django_db
def test_creative_creation_is_post_only_and_queues_render(client: Client) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")
    listing = listing_with_image(organization)
    url = reverse("creative-create", args=(listing.id,))

    assert client.get(url).status_code == 405
    response = client.post(url, creative_payload())

    creative = Creative.objects.get(organization=organization)
    job = Job.objects.get(organization=organization)
    assert response.status_code == 302
    assert response.url == reverse("creative-detail", args=(creative.id,))
    assert creative.listing == listing
    assert creative.status == Creative.Status.QUEUED
    assert "utm_source=pinterest" in creative.destination_url
    assert job.payload == {"creative_id": str(creative.id)}


@pytest.mark.django_db
def test_identical_creative_submission_is_idempotent(client: Client) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")
    listing = listing_with_image(organization)
    url = reverse("creative-create", args=(listing.id,))

    first = client.post(url, creative_payload())
    second = client.post(url, creative_payload())

    assert first.url == second.url
    assert Creative.objects.filter(organization=organization).count() == 1
    assert Job.objects.filter(organization=organization).count() == 1


@pytest.mark.django_db
def test_template_requiring_more_images_is_rejected_before_queue(client: Client) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")
    listing = listing_with_image(organization)
    payload = creative_payload()
    payload["template_id"] = Creative.Template.SPLIT_COMPARE

    response = client.post(
        reverse("creative-create", args=(listing.id,)),
        payload,
    )

    assert response.status_code == 200
    assert b"requires at least 2 images" in response.content
    assert not Creative.objects.filter(organization=organization).exists()
    assert not Job.objects.filter(organization=organization).exists()


@pytest.mark.django_db
def test_creative_pages_are_tenant_scoped(client: Client) -> None:
    allowed = Organization.objects.create(name="Allowed")
    forbidden = Organization.objects.create(name="Forbidden")
    login_owner(client, allowed, "owner@example.com")
    other_listing = listing_with_image(forbidden, "Secret listing")
    other_creative = Creative.objects.create(
        organization=forbidden,
        listing=other_listing,
        template_id=Creative.Template.TEXT_OVERLAY,
        title="Secret creative",
        description="Secret",
        alt_text="Secret preview",
        destination_url=other_listing.listing_url,
        input_revision="secret-revision",
    )

    detail = client.get(reverse("creative-detail", args=(other_creative.id,)))
    create = client.post(
        reverse("creative-create", args=(other_listing.id,)),
        creative_payload(),
    )
    download = client.get(
        reverse("creative-download", args=(other_creative.id,))
    )

    assert detail.status_code == 404
    assert create.status_code == 404
    assert download.status_code == 404


@pytest.mark.django_db
def test_ready_asset_download_is_private_and_streamed(
    client: Client,
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")
    listing = listing_with_image(organization)
    creative = Creative.objects.create(
        organization=organization,
        listing=listing,
        template_id=Creative.Template.TEXT_OVERLAY,
        title="Ready creative",
        description="Ready",
        alt_text="Ready preview",
        destination_url=listing.listing_url,
        status=Creative.Status.READY,
        input_revision="ready-revision",
    )
    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        asset = CreativeAsset(
            organization=organization,
            creative=creative,
            mime_type="image/png",
            byte_size=len(b"private-png"),
            width=1000,
            height=1500,
            checksum="b" * 64,
        )
        asset.file.save("render.png", ContentFile(b"private-png"), save=True)

        response = client.get(
            reverse("creative-download", args=(creative.id,))
        )
        content = b"".join(response.streaming_content)

    assert response.status_code == 200
    assert content == b"private-png"
    assert response["Content-Type"] == "image/png"
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Disposition"] == (
        f'attachment; filename="pinforge-{creative.id}.png"'
    )


@pytest.mark.django_db
def test_queued_creative_has_no_download(client: Client) -> None:
    organization = Organization.objects.create(name="Example Shop")
    login_owner(client, organization, "owner@example.com")
    listing = listing_with_image(organization)
    creative = Creative.objects.create(
        organization=organization,
        listing=listing,
        template_id=Creative.Template.TEXT_OVERLAY,
        title="Queued creative",
        description="Queued",
        alt_text="Queued preview",
        destination_url=listing.listing_url,
        input_revision="queued-revision",
    )

    response = client.get(reverse("creative-download", args=(creative.id,)))

    assert response.status_code == 404
