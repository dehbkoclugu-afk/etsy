from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.storage import Storage
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone

from pinforge_web.models import Listing, ListingImage, Organization, Shop
from pinforge_web.uploads import (
    UploadValidationError,
    ValidatedImage,
    validate_listing_image,
)

logger = logging.getLogger(__name__)
validate_http_url = URLValidator(schemes=("http", "https"))
ZERO_DECIMAL_CURRENCIES = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "JPY",
    "KMF",
    "KRW",
    "PYG",
    "RWF",
    "UGX",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}


class CatalogValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogImportResult:
    listing: Listing
    images: tuple[ListingImage, ...]


def get_or_create_manual_shop(organization: Organization) -> Shop:
    shop, _ = Shop.objects.get_or_create(
        organization=organization,
        source=Shop.Source.MANUAL,
        source_shop_id="manual",
        defaults={"name": f"{organization.name} catalog"[:120]},
    )
    return shop


def create_manual_listing(
    *,
    organization: Organization,
    title: str,
    listing_url: str,
    price: Decimal | str | int,
    currency: str,
    tags: Iterable[str],
    description: str,
    vertical: str,
    images: Sequence[UploadedFile],
) -> CatalogImportResult:
    normalized_title = _required_text(title, "title", 500)
    normalized_url = _listing_url(listing_url)
    normalized_currency = _currency(currency)
    price_minor = _minor_units(price, normalized_currency)
    normalized_tags = _tags(tags)
    normalized_description = description.strip()
    if len(normalized_description) > 20_000:
        raise CatalogValidationError("description must be at most 20000 characters")
    normalized_vertical = _vertical(vertical)
    if not 1 <= len(images) <= 5:
        raise CatalogValidationError("A listing requires 1 to 5 images.")

    validated_images: list[tuple[UploadedFile, ValidatedImage]] = []
    for index, image in enumerate(images, start=1):
        try:
            metadata = validate_listing_image(image)
        except UploadValidationError as error:
            raise CatalogValidationError(f"Image {index}: {error}") from error
        validated_images.append((image, metadata))

    stored_files: list[tuple[Storage, str]] = []
    listing_images: list[ListingImage] = []
    try:
        with transaction.atomic():
            shop = get_or_create_manual_shop(organization)
            listing = Listing.objects.create(
                organization=organization,
                shop=shop,
                source_listing_id=f"manual:{uuid4().hex}",
                title=normalized_title,
                listing_url=normalized_url,
                price_minor=price_minor,
                currency=normalized_currency,
                tags=normalized_tags,
                description=normalized_description,
                vertical=normalized_vertical,
                synchronized_at=timezone.now(),
            )
            for position, (image_upload, metadata) in enumerate(validated_images):
                listing_image = ListingImage(
                    organization=organization,
                    listing=listing,
                    position=position,
                    mime_type=metadata.mime_type,
                    byte_size=metadata.byte_size,
                    width=metadata.width,
                    height=metadata.height,
                    checksum=metadata.checksum,
                )
                original_position = image_upload.tell()
                try:
                    image_upload.seek(0)
                    listing_image.file.save(
                        f"original{metadata.extension}",
                        image_upload,
                        save=False,
                    )
                finally:
                    image_upload.seek(original_position)
                stored_files.append(
                    (listing_image.file.storage, listing_image.file.name)
                )
                listing_image.save()
                listing_images.append(listing_image)
    except Exception:
        _delete_stored_files(stored_files, organization)
        raise

    return CatalogImportResult(listing=listing, images=tuple(listing_images))


def _required_text(value: str, field: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise CatalogValidationError(f"{field} is required")
    if len(normalized) > max_length:
        raise CatalogValidationError(
            f"{field} must be at most {max_length} characters"
        )
    return normalized


def _listing_url(value: str) -> str:
    normalized = value.strip()
    try:
        validate_http_url(normalized)
    except ValidationError as error:
        raise CatalogValidationError(
            "listing_url must be a valid HTTP(S) URL"
        ) from error
    return normalized


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if re.fullmatch(r"[A-Z]{3}", normalized) is None:
        raise CatalogValidationError("currency must be a three-letter code")
    return normalized


def _minor_units(value: Decimal | str | int, currency: str) -> int:
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise CatalogValidationError("price must be a finite decimal") from error
    if not decimal_value.is_finite():
        raise CatalogValidationError("price must be a finite decimal")
    if decimal_value < 0:
        raise CatalogValidationError("price must be non-negative")
    exponent = 0 if currency in ZERO_DECIMAL_CURRENCIES else 2
    quantum = Decimal(1).scaleb(-exponent)
    if decimal_value != decimal_value.quantize(quantum):
        raise CatalogValidationError(
            f"price has too many decimal places for {currency}"
        )
    minor = int(decimal_value * (10**exponent))
    if minor > 9_223_372_036_854_775_807:
        raise CatalogValidationError("price is too large")
    return minor


def _tags(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        if len(value) > 20:
            raise CatalogValidationError("each tag must be at most 20 characters")
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(value)
    if len(normalized) > 13:
        raise CatalogValidationError("a listing may have at most 13 tags")
    return normalized


def _vertical(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-")
    return (normalized or "general")[:64]


def _delete_stored_files(
    stored_files: list[tuple[Storage, str]],
    organization: Organization,
) -> None:
    for storage, name in reversed(stored_files):
        try:
            storage.delete(name)
        except Exception:
            logger.exception(
                "catalog_storage_cleanup_failed",
                extra={"organization_id": str(organization.id)},
            )
