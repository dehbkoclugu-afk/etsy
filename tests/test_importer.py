from __future__ import annotations

import json
from pathlib import Path

import pytest

from pinforge.importers.folder import FolderImporter, ManifestError


def test_imports_valid_product(product_folder: Path) -> None:
    result = FolderImporter.load(product_folder)
    product = result.products[0]
    assert product.id == "sample-product"
    assert product.currency == "USD"
    assert len(product.image_paths) == 2
    assert not result.issues


def test_rejects_path_outside_product_folder(product_folder: Path) -> None:
    payload = json.loads((product_folder / "products.json").read_text(encoding="utf-8"))
    payload["products"][0]["images"] = ["../outside.jpg"]
    (product_folder / "products.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestError, match="klasör dışındaki"):
        FolderImporter.load(product_folder)
