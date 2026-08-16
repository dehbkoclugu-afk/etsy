from __future__ import annotations

import csv
from pathlib import Path

import pytest
from PIL import Image

from pinforge.application.exporter import BundleExporter
from pinforge.importers.folder import FolderImporter


def test_exports_images_and_utf8_csv(product_folder: Path, tmp_path: Path) -> None:
    product = FolderImporter.load(product_folder).products[0]
    result = BundleExporter().export_product(
        product,
        ["mockup_hero", "list_stack"],
        tmp_path / "out",
    )
    assert len(result.drafts) == 2
    assert result.csv_path.is_file()
    with result.csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert "utm_source=pinterest" in rows[0]["link"]
    assert {row["template"] for row in rows} == {"mockup_hero", "list_stack"}
    for row in rows:
        with Image.open(result.output_dir / row["media_file"]) as image:
            assert image.size == (1000, 1500)


def test_existing_csv_blocks_export_before_images_are_written(
    product_folder: Path, tmp_path: Path
) -> None:
    product = FolderImporter.load(product_folder).products[0]
    output = tmp_path / "out"
    output.mkdir()
    (output / "schedule.csv").write_text("existing", encoding="utf-8")
    expected_image = output / "sample-product-mockup_hero.png"
    with pytest.raises(FileExistsError):
        BundleExporter().export_product(product, ["mockup_hero"], output)
    assert not expected_image.exists()
