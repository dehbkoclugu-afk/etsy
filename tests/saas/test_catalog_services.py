from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import override_settings
from PIL import Image

from pinforge_web.catalog import CatalogValidationError, create_manual_listing
from pinforge_web.models import Listing, ListingImage, Organization, Shop


def upload(name: str, color: str = "#173C35") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (20, 30), color).save(buffer, format="PNG")
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


@pytest.mark.django_db
def test_create_manual_listing_normalizes_catalog_and_stores_images(
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        result = create_manual_listing(
            organization=organization,
            title="  Botanical Welcome Book  ",
            listing_url="https://www.etsy.com/listing/123/example",
            price="12.99",
            currency=" usd ",
            tags=[" Airbnb ", "Welcome Book", "airbnb"],
            description="  A useful download.  ",
            vertical=" Hospitality ",
            images=[upload("secret-name.png"), upload("second.png", "#C9855B")],
        )

    listing = result.listing
    assert listing.organization == organization
    assert listing.shop.organization == organization
    assert listing.shop.source == Shop.Source.MANUAL
    assert listing.title == "Botanical Welcome Book"
    assert listing.price_minor == 1299
    assert listing.currency == "USD"
    assert listing.tags == ["Airbnb", "Welcome Book"]
    assert listing.vertical == "hospitality"
    assert [image.position for image in result.images] == [0, 1]
    assert len(list(tmp_path.rglob("original.png"))) == 2
    assert all("secret-name" not in image.file.name for image in result.images)


@pytest.mark.django_db
def test_manual_shop_is_reused_for_later_listings(tmp_path: Path) -> None:
    organization = Organization.objects.create(name="Example Shop")
    common = {
        "organization": organization,
        "listing_url": "https://www.etsy.com/listing/123/example",
        "price": "0",
        "currency": "USD",
        "tags": [],
        "description": "",
        "vertical": "general",
    }

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        first = create_manual_listing(
            **common,
            title="First",
            images=[upload("first.png")],
        )
        second = create_manual_listing(
            **common,
            title="Second",
            images=[upload("second.png")],
        )

    assert first.listing.shop_id == second.listing.shop_id
    assert Shop.objects.filter(organization=organization).count() == 1


@pytest.mark.django_db
def test_all_images_are_validated_before_database_or_storage_writes(
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")
    broken = SimpleUploadedFile("broken.png", b"not an image")

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        with pytest.raises(CatalogValidationError, match="2"):
            create_manual_listing(
                organization=organization,
                title="Invalid",
                listing_url="https://www.etsy.com/listing/123/example",
                price="1.00",
                currency="USD",
                tags=[],
                description="",
                vertical="general",
                images=[upload("valid.png"), broken],
            )

    assert not Shop.objects.filter(organization=organization).exists()
    assert not Listing.objects.filter(organization=organization).exists()
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


@pytest.mark.django_db
def test_storage_file_is_removed_when_database_save_fails(tmp_path: Path) -> None:
    organization = Organization.objects.create(name="Example Shop")

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        with patch.object(
            ListingImage,
            "save",
            side_effect=IntegrityError("simulated database failure"),
        ):
            with pytest.raises(IntegrityError, match="simulated"):
                create_manual_listing(
                    organization=organization,
                    title="Rollback",
                    listing_url="https://www.etsy.com/listing/123/example",
                    price="1.00",
                    currency="USD",
                    tags=[],
                    description="",
                    vertical="general",
                    images=[upload("valid.png")],
                )

    assert not Listing.objects.filter(organization=organization).exists()
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


@pytest.mark.django_db
@pytest.mark.parametrize("count", [0, 6])
def test_manual_listing_requires_one_to_five_images(
    count: int,
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        with pytest.raises(CatalogValidationError, match="1 to 5"):
            create_manual_listing(
                organization=organization,
                title="Image count",
                listing_url="https://www.etsy.com/listing/123/example",
                price="1.00",
                currency="USD",
                tags=[],
                description="",
                vertical="general",
                images=[upload(f"{index}.png") for index in range(count)],
            )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("price", "currency", "message"),
    [
        ("-1", "USD", "non-negative"),
        ("1.001", "USD", "decimal places"),
        ("1.00", "US", "currency"),
    ],
)
def test_manual_listing_rejects_invalid_money(
    price: str,
    currency: str,
    message: str,
    tmp_path: Path,
) -> None:
    organization = Organization.objects.create(name="Example Shop")

    with override_settings(STORAGES=private_storage_settings(tmp_path)):
        with pytest.raises(CatalogValidationError, match=message):
            create_manual_listing(
                organization=organization,
                title="Money",
                listing_url="https://www.etsy.com/listing/123/example",
                price=price,
                currency=currency,
                tags=[],
                description="",
                vertical="general",
                images=[upload("valid.png")],
            )
