from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def make_image(path: Path, color: str, label: str) -> None:
    image = Image.new("RGB", (900, 1100), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((110, 110, 790, 990), fill="#F8F4E9", outline="#173C35", width=8)
    draw.text((170, 500), label, fill="#173C35")
    image.save(path, quality=90)
    image.close()


@pytest.fixture
def product_folder(tmp_path: Path) -> Path:
    make_image(tmp_path / "one.jpg", "#DCE7E2", "WELCOME")
    make_image(tmp_path / "two.jpg", "#E8D4C5", "GUIDE")
    payload = {
        "products": [
            {
                "id": "sample-product",
                "title": "Modern Welcome Book Template",
                "listing_url": "https://www.etsy.com/listing/123/sample?ref=test",
                "price": 12.9,
                "currency": "usd",
                "vertical": "airbnb",
                "tags": ["welcome book", "host guide"],
                "images": ["one.jpg", "two.jpg"],
            }
        ]
    }
    (tmp_path / "products.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path
