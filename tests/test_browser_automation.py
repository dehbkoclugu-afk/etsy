from __future__ import annotations

import json

import pytest

from pinforge.integrations.browser_automation import (
    normalize_etsy_listing_url,
    product_from_json_ld,
)
from pinforge.cli import parser


def test_normalize_etsy_listing_url_strips_tracking() -> None:
    assert (
        normalize_etsy_listing_url(
            "https://www.etsy.com/listing/123456/ceramic-cup?ref=shop_home_active_1"
        )
        == "https://www.etsy.com/listing/123456/ceramic-cup"
    )
    assert normalize_etsy_listing_url("https://evil.example/listing/123") is None
    assert normalize_etsy_listing_url("javascript:alert(1)") is None


def test_product_from_nested_json_ld() -> None:
    payload = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "BreadcrumbList"},
            {
                "@type": "Product",
                "name": "Handmade Ceramic Cup",
                "description": "Made by hand.",
                "image": [
                    "https://i.etsystatic.com/123/il/a.jpg",
                    {"url": "https://i.etsystatic.com/123/il/b.jpg"},
                ],
                "offers": {"price": "24.50", "priceCurrency": "USD"},
            },
        ],
    }

    product = product_from_json_ld(
        (json.dumps(payload),),
        "https://www.etsy.com/listing/987654/handmade-cup",
    )

    assert product == {
        "id": "987654",
        "title": "Handmade Ceramic Cup",
        "description": "Made by hand.",
        "price": 24.5,
        "currency": "USD",
        "images": (
            "https://i.etsystatic.com/123/il/a.jpg",
            "https://i.etsystatic.com/123/il/b.jpg",
        ),
    }


def test_product_json_ld_requires_product_and_images() -> None:
    with pytest.raises(ValueError, match="okunamadı"):
        product_from_json_ld(
            (json.dumps({"@type": "WebPage"}),), "https://www.etsy.com/listing/1/x"
        )

    with pytest.raises(ValueError, match="görselleri"):
        product_from_json_ld(
            (json.dumps({"@type": "Product", "name": "No image"}),),
            "https://www.etsy.com/listing/1/x",
        )


def test_browser_run_cli_is_one_command_and_can_be_headless() -> None:
    args = parser().parse_args(
        [
            "browser-run",
            "https://www.etsy.com/shop/example",
            "--board",
            "New products",
            "--headless",
        ]
    )

    assert args.command == "browser-run"
    assert args.board == "New products"
    assert args.limit == 1
    assert args.headless is True
