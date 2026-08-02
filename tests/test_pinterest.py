from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from pinforge.domain.models import PinDraft
from pinforge.integrations.http import JsonHttpClient
from pinforge.integrations.http import ApiError
from pinforge.integrations.pinterest import PinterestClient


def test_pinterest_board_pagination_and_publish(tmp_path: Path) -> None:
    image_path = tmp_path / "pin.png"
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    png_data = buffer.getvalue()
    image_path.write_bytes(png_data)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/boards"):
            if request.url.params.get("bookmark") == "next":
                return httpx.Response(200, json={"items": [{"id": "2", "name": "B"}]})
            return httpx.Response(
                200,
                json={"items": [{"id": "1", "name": "A"}], "bookmark": "next"},
            )
        if request.url.path.endswith("/pins"):
            body = json.loads(request.content)
            assert base64.b64decode(body["media_source"]["data"]) == png_data
            assert body["board_id"] == "1"
            return httpx.Response(201, json={"id": "pin-9"})
        raise AssertionError(f"Unexpected request: {request.url}")

    pinterest = PinterestClient(
        "token",
        http=JsonHttpClient(httpx.Client(transport=httpx.MockTransport(handler))),
    )
    assert [board.id for board in pinterest.list_boards()] == ["1", "2"]
    result = pinterest.publish(
        PinDraft(
            id="draft",
            product_id="product",
            template_id="mockup_hero",
            title="Title",
            description="Description",
            alt_text="Alt",
            destination_url="https://example.test/product",
            board_id="1",
            image_path=image_path,
        )
    )
    assert result.remote_id == "pin-9"
    assert len([request for request in requests if request.method == "POST"]) == 1


def test_publish_is_not_retried_after_ambiguous_server_error(tmp_path: Path) -> None:
    image_path = tmp_path / "pin.png"
    Image.new("RGB", (8, 8), "red").save(image_path, format="PNG")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"message": "try later"})

    pinterest = PinterestClient(
        "token",
        http=JsonHttpClient(
            httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
        ),
    )
    with pytest.raises(ApiError, match="try later"):
        pinterest.publish(
            PinDraft(
                id="draft",
                product_id="product",
                template_id="mockup_hero",
                title="Title",
                description="Description",
                alt_text="Alt",
                destination_url="https://example.test/product",
                board_id="1",
                image_path=image_path,
            )
        )
    assert calls == 1
