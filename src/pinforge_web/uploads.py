from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID


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
