from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

from pinforge.application.generator import build_copy, with_utm
from pinforge.clock import ensure_utc, utc_now
from pinforge.domain.models import BrandKit, PinCopy, PinDraft, PinStatus, SourceProduct
from pinforge.rendering.engine import RenderEngine


@dataclass(frozen=True, slots=True)
class ExportResult:
    output_dir: Path
    csv_path: Path
    drafts: tuple[PinDraft, ...]


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    cleaned = cleaned[:80] or "pin"
    if cleaned != value.strip().lower() or len(value.strip()) > 80:
        digest = hashlib.sha256(value.encode()).hexdigest()[:8]
        cleaned = f"{cleaned[:71]}-{digest}"
    return cleaned


class BundleExporter:
    CSV_FIELDS = (
        "title",
        "description",
        "link",
        "board",
        "publish_date",
        "media_file",
        "alt_text",
        "template",
    )

    def __init__(self, engine: RenderEngine | None = None) -> None:
        self.engine = engine or RenderEngine()

    def export_product(
        self,
        product: SourceProduct,
        template_ids: list[str] | tuple[str, ...],
        output_dir: str | Path,
        *,
        brand: BrandKit | None = None,
        copies: dict[str, PinCopy] | None = None,
        overwrite: bool = False,
        scheduled_at: datetime | None = None,
        board_id: str | None = None,
    ) -> ExportResult:
        if not template_ids:
            raise ValueError("En az bir şablon seçilmeli")
        if len(set(template_ids)) != len(template_ids):
            raise ValueError("Aynı şablon bir dışa aktarmada iki kez seçilemez")
        target = Path(output_dir).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        filenames = {
            template_id: f"{safe_name(product.id)}-{template_id}.png"
            for template_id in template_ids
        }
        existing = [
            target / name for name in filenames.values() if (target / name).exists()
        ]
        csv_path = target / "schedule.csv"
        if csv_path.exists():
            existing.append(csv_path)
        if existing and not overwrite:
            raise FileExistsError(f"Dosya zaten var: {existing[0]}")
        stage = Path(mkdtemp(prefix=".pinforge-stage-", dir=target))
        backup = Path(mkdtemp(prefix=".pinforge-backup-", dir=target))
        drafts: list[PinDraft] = []
        rows: list[dict[str, str]] = []
        approved_at = utc_now()
        normalized_schedule = ensure_utc(scheduled_at) if scheduled_at else None
        try:
            for template_id in template_ids:
                copy = (copies or {}).get(template_id) or build_copy(
                    product, template_id
                )
                filename = filenames[template_id]
                staged_image = self.engine.render_to(
                    stage / filename,
                    template_id,
                    product,
                    copy,
                    brand,
                    overwrite=True,
                )
                link = with_utm(product.listing_url, template_id, product.vertical)
                draft = PinDraft(
                    id=str(uuid4()),
                    product_id=product.id,
                    template_id=template_id,
                    title=copy.title,
                    description=copy.description,
                    alt_text=copy.alt_text,
                    destination_url=link,
                    board_id=board_id,
                    image_path=target / filename,
                    scheduled_at=normalized_schedule,
                    status=PinStatus.QUEUED if normalized_schedule else PinStatus.READY,
                    approved_at=approved_at,
                    generation_model=copy.generation_model,
                    prompt_version=copy.prompt_version,
                )
                if not staged_image.is_file() or staged_image.stat().st_size == 0:
                    raise OSError(f"Render çıktısı doğrulanamadı: {filename}")
                drafts.append(draft)
                rows.append(
                    {
                        "title": draft.title,
                        "description": draft.description,
                        "link": draft.destination_url,
                        "board": board_id or "",
                        "publish_date": normalized_schedule.isoformat()
                        if normalized_schedule
                        else "",
                        "media_file": filename,
                        "alt_text": draft.alt_text,
                        "template": template_id,
                    }
                )

            staged_csv = stage / "schedule.csv"
            with staged_csv.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            promoted: list[Path] = []
            try:
                for name in (*filenames.values(), "schedule.csv"):
                    destination = target / name
                    if destination.exists():
                        os.replace(destination, backup / name)
                    os.replace(stage / name, destination)
                    promoted.append(destination)
            except Exception:
                for destination in promoted:
                    destination.unlink(missing_ok=True)
                for old in backup.iterdir():
                    os.replace(old, target / old.name)
                raise
            return ExportResult(target, csv_path, tuple(drafts))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
