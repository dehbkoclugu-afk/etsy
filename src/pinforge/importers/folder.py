from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pinforge.domain.models import SourceProduct


class ManifestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImportIssue:
    index: int
    message: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    products: tuple[SourceProduct, ...]
    issues: tuple[ImportIssue, ...]


class FolderImporter:
    @staticmethod
    def load(folder: str | Path) -> ImportResult:
        root = Path(folder).expanduser().resolve()
        manifest = root / "products.json"
        if not manifest.is_file():
            raise ManifestError(f"products.json bulunamadı: {root}")

        if manifest.stat().st_size > 5 * 1024 * 1024:
            raise ManifestError("products.json 5 MB sınırını aşıyor")
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"products.json okunamadı: {exc}") from exc

        records = payload.get("products") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ManifestError(
                "products.json bir liste veya products listesi içermeli"
            )

        if len(records) > 10_000:
            raise ManifestError("products.json en fazla 10000 ürün içerebilir")
        products: list[SourceProduct] = []
        issues: list[ImportIssue] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(records):
            try:
                product = FolderImporter._parse_product(root, raw, index)
                if product.id in seen_ids:
                    raise ValueError(f"Yinelenen ürün kimliği: {product.id}")
                seen_ids.add(product.id)
                products.append(product)
            except (KeyError, TypeError, ValueError) as exc:
                issues.append(ImportIssue(index=index, message=str(exc)))

        if not products and issues:
            raise ManifestError(
                "Geçerli ürün bulunamadı: " + "; ".join(i.message for i in issues)
            )
        return ImportResult(tuple(products), tuple(issues))

    @staticmethod
    def _parse_product(root: Path, raw: Any, index: int) -> SourceProduct:
        if not isinstance(raw, dict):
            raise TypeError(f"{index + 1}. kayıt nesne olmalı")

        product_id = FolderImporter._required_text(raw, "id", index)
        title = FolderImporter._required_text(raw, "title", index)
        listing_url = FolderImporter._required_text(raw, "listing_url", index)
        parsed_url = urlparse(listing_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(
                f"{product_id}: listing_url geçerli bir http(s) adresi değil"
            )

        image_values = raw.get("images", raw.get("local_image_paths", []))
        if not isinstance(image_values, list) or not image_values:
            raise ValueError(f"{product_id}: en az bir görsel gerekli")

        image_paths: list[Path] = []
        for value in image_values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{product_id}: görsel yolu metin olmalı")
            resolved = (root / value).resolve()
            if not resolved.is_relative_to(root):
                raise ValueError(
                    f"{product_id}: klasör dışındaki görsellere izin verilmez"
                )
            if not resolved.is_file():
                raise ValueError(f"{product_id}: görsel bulunamadı: {value}")
            image_paths.append(resolved)

        tags = raw.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"{product_id}: tags bir metin listesi olmalı")
        if len(tags) > 100 or any(len(tag) > 200 for tag in tags):
            raise ValueError(f"{product_id}: tags sınırı aşıyor")

        try:
            price = float(raw.get("price", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{product_id}: price sayı olmalı") from exc

        return SourceProduct(
            id=product_id,
            title=title[:500],
            listing_url=listing_url,
            price=price,
            currency=str(raw.get("currency", "USD")).upper(),
            vertical=str(raw.get("vertical", "general")).strip() or "general",
            image_paths=tuple(image_paths),
            tags=tuple(tag.strip() for tag in tags if tag.strip()),
            description=str(raw.get("description", "")).strip()[:20_000],
        )

    @staticmethod
    def _required_text(raw: dict[str, Any], key: str, index: int) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{index + 1}. kayıt: {key} zorunlu bir metin alanıdır")
        return value.strip()
