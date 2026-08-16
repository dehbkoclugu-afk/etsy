from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from pinforge_web.models import (
    BrandKit,
    Creative,
    CreativeAsset,
    Job,
    Listing,
    ListingImage,
    Organization,
    Shop,
)


def create_listing(
    organization: Organization,
    *,
    source_listing_id: str = "manual-1",
) -> Listing:
    shop = Shop.objects.create(
        organization=organization,
        source=Shop.Source.MANUAL,
        source_shop_id="manual",
        name=f"{organization.name} catalog",
    )
    return Listing.objects.create(
        organization=organization,
        shop=shop,
        source_listing_id=source_listing_id,
        title="Botanical welcome book",
        listing_url="https://www.etsy.com/listing/123/example",
        price_minor=1299,
        currency="USD",
        tags=["welcome book", "airbnb"],
        vertical="hospitality",
    )


@pytest.mark.django_db
def test_catalog_records_are_tenant_owned_and_cascade_with_organization() -> None:
    organization = Organization.objects.create(name="Example Shop")
    listing = create_listing(organization)
    image = ListingImage.objects.create(
        organization=organization,
        listing=listing,
        file="private/source.png",
        position=0,
        mime_type="image/png",
        byte_size=100,
        width=10,
        height=10,
        checksum="a" * 64,
    )
    brand = BrandKit.objects.create(organization=organization, shop_name="ECOVIA")
    creative = Creative.objects.create(
        organization=organization,
        listing=listing,
        template_id=Creative.Template.MOCKUP_HERO,
        title="Botanical welcome book",
        description="A polished guest welcome book.",
        alt_text="A botanical welcome book preview",
        destination_url=listing.listing_url,
    )
    asset = CreativeAsset.objects.create(
        organization=organization,
        creative=creative,
        file="private/render.png",
        mime_type="image/png",
        byte_size=100,
        width=1000,
        height=1500,
        checksum="b" * 64,
    )
    job = Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": str(creative.id)},
        deduplication_key=f"render:{creative.id}",
    )

    assert image.organization_id == listing.organization_id
    assert brand.organization_id == organization.id
    assert asset.organization_id == creative.organization_id
    assert job.organization_id == organization.id

    organization.delete()

    assert not Listing.objects.filter(pk=listing.pk).exists()
    assert not Creative.objects.filter(pk=creative.pk).exists()
    assert not Job.objects.filter(pk=job.pk).exists()


@pytest.mark.django_db
def test_shop_and_listing_source_ids_are_unique_inside_tenant() -> None:
    organization = Organization.objects.create(name="Example Shop")
    listing = create_listing(organization)

    with pytest.raises(IntegrityError), transaction.atomic():
        Shop.objects.create(
            organization=organization,
            source=Shop.Source.MANUAL,
            source_shop_id="manual",
            name="Duplicate",
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        Listing.objects.create(
            organization=organization,
            shop=listing.shop,
            source_listing_id=listing.source_listing_id,
            title="Duplicate",
            listing_url="https://www.etsy.com/listing/456/duplicate",
            price_minor=100,
            currency="USD",
        )


@pytest.mark.django_db
def test_listing_rejects_negative_minor_price() -> None:
    organization = Organization.objects.create(name="Example Shop")
    shop = Shop.objects.create(
        organization=organization,
        source=Shop.Source.MANUAL,
        source_shop_id="manual",
        name="Catalog",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Listing.objects.create(
            organization=organization,
            shop=shop,
            source_listing_id="negative",
            title="Invalid",
            listing_url="https://www.etsy.com/listing/789/invalid",
            price_minor=-1,
            currency="USD",
        )


@pytest.mark.django_db
def test_listing_image_position_and_creative_asset_are_unique() -> None:
    organization = Organization.objects.create(name="Example Shop")
    listing = create_listing(organization)
    image_fields = {
        "organization": organization,
        "listing": listing,
        "file": "private/source.png",
        "position": 0,
        "mime_type": "image/png",
        "byte_size": 100,
        "width": 10,
        "height": 10,
        "checksum": "a" * 64,
    }
    ListingImage.objects.create(**image_fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingImage.objects.create(**image_fields)

    creative = Creative.objects.create(
        organization=organization,
        listing=listing,
        template_id=Creative.Template.TEXT_OVERLAY,
        title="A useful pin",
        description="Description",
        alt_text="Alt text",
        destination_url=listing.listing_url,
    )
    asset_fields = {
        "organization": organization,
        "creative": creative,
        "file": "private/render.png",
        "mime_type": "image/png",
        "byte_size": 100,
        "width": 1000,
        "height": 1500,
        "checksum": "b" * 64,
    }
    CreativeAsset.objects.create(**asset_fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        CreativeAsset.objects.create(**asset_fields)


@pytest.mark.django_db
def test_job_deduplication_and_non_negative_attempts_are_database_constraints() -> None:
    organization = Organization.objects.create(name="Example Shop")
    fields = {
        "organization": organization,
        "kind": Job.Kind.RENDER_CREATIVE,
        "payload": {"creative_id": "one"},
        "deduplication_key": "render:one",
    }
    Job.objects.create(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        Job.objects.create(**fields)

    with pytest.raises(IntegrityError), transaction.atomic():
        Job.objects.create(
            organization=organization,
            kind=Job.Kind.RENDER_CREATIVE,
            payload={"creative_id": "two"},
            deduplication_key="render:two",
            attempts=-1,
        )


@pytest.mark.django_db
def test_job_defaults_are_ready_for_lease_claiming() -> None:
    organization = Organization.objects.create(name="Example Shop")
    before = timezone.now() - timedelta(seconds=1)
    job = Job.objects.create(
        organization=organization,
        kind=Job.Kind.RENDER_CREATIVE,
        payload={"creative_id": "one"},
        deduplication_key="render:one",
    )

    assert job.status == Job.Status.PENDING
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.due_at >= before
    assert job.lease_owner == ""
    assert job.lease_expires_at is None
