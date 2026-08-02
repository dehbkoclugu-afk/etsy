from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from pinforge_web import uploads
from pinforge_web.uploads import (
    UploadValidationError,
    creative_asset_upload_to,
    listing_image_upload_to,
    validate_listing_image,
)


def image_upload(
    *,
    image_format: str = "PNG",
    size: tuple[int, int] = (20, 30),
    name: str = "seller-provided-name.png",
) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", size, "#173C35").save(buffer, format=image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="text/plain")


def test_valid_image_is_identified_from_bytes_and_preserves_cursor() -> None:
    upload = image_upload()
    upload.seek(3)

    validated = validate_listing_image(upload)

    assert validated.mime_type == "image/png"
    assert validated.extension == ".png"
    assert validated.width == 20
    assert validated.height == 30
    assert validated.byte_size == upload.size
    assert len(validated.checksum) == 64
    assert upload.tell() == 3


def test_jpeg_is_accepted_even_when_declared_mime_is_wrong() -> None:
    upload = image_upload(image_format="JPEG", name="photo.bin")

    validated = validate_listing_image(upload)

    assert validated.mime_type == "image/jpeg"
    assert validated.extension == ".jpg"


def test_oversized_upload_is_rejected_before_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "MAX_IMAGE_BYTES", 10)
    upload = image_upload()

    with pytest.raises(UploadValidationError, match="10 bytes"):
        validate_listing_image(upload)


def test_excessive_pixels_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "MAX_IMAGE_PIXELS", 100)
    upload = image_upload(size=(11, 10))

    with pytest.raises(UploadValidationError, match="pixel"):
        validate_listing_image(upload)


def test_unsupported_image_format_is_rejected() -> None:
    upload = image_upload(image_format="GIF", name="image.gif")

    with pytest.raises(UploadValidationError, match="PNG or JPEG"):
        validate_listing_image(upload)


def test_truncated_image_is_rejected() -> None:
    valid = image_upload()
    upload = SimpleUploadedFile("broken.png", valid.read()[:40])

    with pytest.raises(UploadValidationError, match="valid image"):
        validate_listing_image(upload)


def test_private_storage_keys_do_not_include_seller_filename() -> None:
    instance = type(
        "Instance",
        (),
        {"id": uuid4(), "organization_id": uuid4()},
    )()

    source_key = listing_image_upload_to(instance, "My Secret Product Name.PNG")
    render_key = creative_asset_upload_to(instance, "anything.jpg")

    assert source_key == (
        f"organizations/{instance.organization_id}/listings/"
        f"{instance.id}/original.png"
    )
    assert render_key == (
        f"organizations/{instance.organization_id}/creatives/"
        f"{instance.id}/render.png"
    )
    assert "Secret" not in source_key
