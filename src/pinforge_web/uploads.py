from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_DIMENSION = 12_000


class UploadValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    mime_type: str
    extension: str
    byte_size: int
    width: int
    height: int
    checksum: str


class StoredInstance(Protocol):
    id: UUID
    organization_id: UUID


def listing_image_upload_to(instance: StoredInstance, filename: str) -> str:
    extension = _safe_image_extension(filename)
    return (
        f"organizations/{instance.organization_id}/listings/"
        f"{instance.id}/original{extension}"
    )


def creative_asset_upload_to(instance: StoredInstance, filename: str) -> str:
    return (
        f"organizations/{instance.organization_id}/creatives/"
        f"{instance.id}/render.png"
    )


def _safe_image_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    return extension if extension in {".jpg", ".jpeg", ".png"} else ".image"


def validate_listing_image(upload: BinaryIO) -> ValidatedImage:
    original_position = upload.tell()
    try:
        declared_size = getattr(upload, "size", None)
        if declared_size is not None and declared_size > MAX_IMAGE_BYTES:
            raise UploadValidationError(
                f"Image must be at most {MAX_IMAGE_BYTES} bytes."
            )

        upload.seek(0)
        checksum = hashlib.sha256()
        byte_size = 0
        while chunk := upload.read(1024 * 1024):
            byte_size += len(chunk)
            if byte_size > MAX_IMAGE_BYTES:
                raise UploadValidationError(
                    f"Image must be at most {MAX_IMAGE_BYTES} bytes."
                )
            checksum.update(chunk)

        upload.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in {"PNG", "JPEG"}:
                    raise UploadValidationError(
                        "Upload must be a PNG or JPEG image."
                    )
                if getattr(image, "n_frames", 1) != 1:
                    raise UploadValidationError("Animated images are not supported.")
                if width <= 0 or height <= 0:
                    raise UploadValidationError("Image dimensions must be positive.")
                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    raise UploadValidationError("Image dimensions are too large.")
                if width * height > MAX_IMAGE_PIXELS:
                    raise UploadValidationError(
                        f"Image exceeds the {MAX_IMAGE_PIXELS} pixel limit."
                    )
                image.verify()

            upload.seek(0)
            with Image.open(upload) as decoded:
                decoded.load()
    except UploadValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise UploadValidationError("Image exceeds the safe pixel limit.") from error
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as error:
        raise UploadValidationError("Upload is not a valid image.") from error
    finally:
        upload.seek(original_position)

    mime_type, extension = (
        ("image/png", ".png")
        if image_format == "PNG"
        else ("image/jpeg", ".jpg")
    )
    return ValidatedImage(
        mime_type=mime_type,
        extension=extension,
        byte_size=byte_size,
        width=width,
        height=height,
        checksum=checksum.hexdigest(),
    )
