from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from pinforge.integrations.etsy import EtsyClient
from pinforge.integrations.http import JsonHttpClient


def test_etsy_import_paginates_and_caches_validated_image(tmp_path: Path) -> None:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "#173C35").save(buffer, format="JPEG")
    image_bytes = buffer.getvalue()
    listing_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal listing_calls
        if request.url.path.endswith("/listings/active"):
            listing_calls += 1
            assert request.headers["x-api-key"] == "key:secret"
            assert request.url.params["limit"] == "1"
            if request.url.params["offset"] == "0":
                return httpx.Response(
                    200,
                    json={
                        "count": 1,
                        "results": [
                            {
                                "listing_id": 123,
                                "title": "Welcome &amp; Guide",
                                "url": "https://www.etsy.com/listing/123",
                                "price": {
                                    "amount": 1290,
                                    "divisor": 100,
                                    "currency_code": "USD",
                                },
                                "tags": ["Airbnb", "guide"],
                                "description": "Useful guide",
                            }
                        ],
                    },
                )
        if request.url.path.endswith("/listings/123/images"):
            return httpx.Response(
                200, json={"results": [{"url_fullxfull": "https://img.test/a.jpg"}]}
            )
        if request.url.host == "img.test":
            return httpx.Response(
                200, content=image_bytes, headers={"content-type": "image/jpeg"}
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    etsy = EtsyClient("key", "secret", "token", http=JsonHttpClient(client))
    products = etsy.import_shop("42", tmp_path / "cache", limit=1)
    assert listing_calls == 1
    assert products[0].title == "Welcome & Guide"
    assert products[0].price == 12.9
    assert products[0].image_paths[0].is_file()
    with Image.open(products[0].image_paths[0]) as cached:
        assert cached.format == "JPEG"


def test_etsy_listing_limit_is_validated() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, request=request)
        )
    )
    etsy = EtsyClient("key", "secret", "token", http=JsonHttpClient(client))

    for invalid in (0, 101):
        with pytest.raises(ValueError, match="1-100"):
            etsy.list_active_listings("42", limit=invalid)
