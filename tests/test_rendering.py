from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from pinforge.application.generator import build_copy
from pinforge.importers.folder import FolderImporter
from pinforge.rendering.engine import RenderEngine, RenderError


def png_hash(image: object) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False)  # type: ignore[attr-defined]
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def test_all_templates_render_exact_pin_size(product_folder: Path) -> None:
    product = FolderImporter.load(product_folder).products[0]
    engine = RenderEngine()
    for template_id in engine.templates:
        image = engine.render(template_id, product, build_copy(product, template_id))
        assert image.size == (1000, 1500)
        image.close()


def test_render_is_deterministic(product_folder: Path) -> None:
    product = FolderImporter.load(product_folder).products[0]
    engine = RenderEngine()
    first = engine.render("mockup_hero", product, build_copy(product, "mockup_hero"))
    second = engine.render("mockup_hero", product, build_copy(product, "mockup_hero"))
    assert png_hash(first) == png_hash(second)
    first.close()
    second.close()


def test_split_requires_two_images(product_folder: Path) -> None:
    product = FolderImporter.load(product_folder).products[0]
    one_image_product = product.__class__(
        id=product.id,
        title=product.title,
        listing_url=product.listing_url,
        price=product.price,
        currency=product.currency,
        vertical=product.vertical,
        image_paths=product.image_paths[:1],
    )
    with pytest.raises(RenderError, match="en az 2 görsel"):
        RenderEngine().render(
            "split_compare", one_image_product, build_copy(product, "split_compare")
        )
