from __future__ import annotations

import json
from pathlib import Path

import httpx

from pinforge.importers.folder import FolderImporter
from pinforge.integrations.anthropic import AnthropicCopyGenerator
from pinforge.integrations.http import JsonHttpClient


def test_structured_copy_generation_uses_one_schema_request(
    product_folder: Path,
) -> None:
    product = FolderImporter.load(product_folder).products[0]
    description = "A" * 260
    variants = [
        {
            "template_id": "mockup_hero",
            "title": "Modern welcome book",
            "description": description,
            "alt_text": "A modern welcome book preview",
            "bullets": [],
        },
        {
            "template_id": "list_stack",
            "title": "Five useful pages",
            "description": description,
            "alt_text": "Five pages listed beside a product mockup",
            "bullets": ["Guide", "Wi-Fi", "Rules", "Places", "Checkout"],
        },
    ]
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert request.headers["x-api-key"] == "secret"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": json.dumps({"variants": variants})}
                ]
            },
        )

    http = JsonHttpClient(httpx.Client(transport=httpx.MockTransport(handler)))
    result = AnthropicCopyGenerator("secret", http=http).generate(
        product, ["mockup_hero", "list_stack"]
    )
    assert calls == 1
    assert result["list_stack"].bullets[-1] == "Checkout"
