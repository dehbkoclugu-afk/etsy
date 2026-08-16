from __future__ import annotations

import html
import hashlib
import ipaddress
import os
import socket
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlparse

from PIL import Image, ImageOps

from pinforge.domain.models import SourceKind, SourceProduct
from pinforge.integrations.http import ApiError, JsonHttpClient


class EtsyClient:
    API_ROOT = "https://api.etsy.com/v3/application"

    def __init__(
        self,
        keystring: str,
        shared_secret: str,
        access_token: str,
        *,
        http: JsonHttpClient | None = None,
        refresh_access_token: Callable[[], str] | None = None,
    ) -> None:
        if not keystring or not shared_secret or not access_token:
            raise ValueError("Etsy keystring, shared secret ve access token gerekli")
        self.http = http or JsonHttpClient()
        self.headers = {
            "x-api-key": f"{keystring}:{shared_secret}",
            "authorization": f"Bearer {access_token}",
        }
        self.last_issues: tuple[str, ...] = ()
        self.refresh_access_token = refresh_access_token

    def close(self) -> None:
        self.http.close()

    def list_active_listings(self, shop_id: str) -> tuple[dict[str, Any], ...]:
        if not shop_id.isdigit():
            raise ValueError("Etsy shop_id sayısal olmalı")
        listings: list[dict[str, Any]] = []
        offset = 0
        for _page in range(100):
            payload = self._get(
                f"{self.API_ROOT}/shops/{shop_id}/listings/active",
                params={
                    "limit": 100,
                    "offset": offset,
                    "sort_on": "created",
                    "sort_order": "desc",
                },
            )
            results = payload.get("results", [])
            if not isinstance(results, list):
                raise ApiError("Etsy listing yanıtında results listesi yok")
            listings.extend(item for item in results if isinstance(item, dict))
            try:
                count = int(payload.get("count", len(listings)))
            except (TypeError, ValueError) as exc:
                raise ApiError("Etsy listing count değeri geçersiz") from exc
            offset += len(results)
            if not results or offset >= count:
                break
        else:
            raise ApiError("Etsy listing sayfalama sınırı aşıldı")
        return tuple(listings)

    def listing_images(self, listing_id: str) -> tuple[dict[str, Any], ...]:
        payload = self._get(
            f"{self.API_ROOT}/listings/{listing_id}/images",
        )
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise ApiError("Etsy görsel yanıtında results listesi yok")
        return tuple(item for item in results if isinstance(item, dict))

    def import_shop(
        self, shop_id: str, cache_directory: str | Path
    ) -> tuple[SourceProduct, ...]:
        cache = Path(cache_directory).expanduser().resolve()
        cache.mkdir(parents=True, exist_ok=True)
        products: list[SourceProduct] = []
        issues: list[str] = []
        for listing in self.list_active_listings(shop_id):
            listing_id = str(listing.get("listing_id", ""))
            if not listing_id:
                continue
            try:
                image_records = self.listing_images(listing_id)
                paths: list[Path] = []
                for index, image in enumerate(image_records[:4], start=1):
                    url = _image_url(image)
                    if not url:
                        continue
                    _validate_remote_image_url(url)
                    path = cache / listing_id / f"{index}.jpg"
                    marker = path.with_suffix(".url.sha256")
                    url_hash = hashlib.sha256(url.encode()).hexdigest()
                    if (
                        path.is_file()
                        and marker.is_file()
                        and marker.read_text() == url_hash
                    ):
                        paths.append(path)
                        continue
                    data, content_type = self.http.get_bytes(
                        url, provider="etsy", max_bytes=20 * 1024 * 1024
                    )
                    _save_validated_jpeg(data, path, content_type)
                    _write_marker(marker, url_hash)
                    paths.append(path)
                if not paths:
                    raise ValueError("görsel bulunamadı")
                price, currency = _price(listing.get("price"))
                title = html.unescape(str(listing.get("title", ""))).strip()
                if not title:
                    raise ValueError("başlık boş")
                products.append(
                    SourceProduct(
                        id=listing_id,
                        title=title[:500],
                        listing_url=str(
                            listing.get("url")
                            or f"https://www.etsy.com/listing/{listing_id}"
                        ),
                        price=price,
                        currency=currency,
                        vertical=_vertical(listing),
                        image_paths=tuple(paths),
                        tags=tuple(
                            str(tag).strip()[:100]
                            for tag in listing.get("tags", [])[:20]
                            if str(tag).strip()
                        ),
                        description=html.unescape(
                            str(listing.get("description", ""))
                        ).strip()[:20_000],
                        kind=SourceKind.ETSY_API,
                    )
                )
            except (ApiError, OSError, ValueError) as exc:
                issues.append(f"{listing_id}: {exc}")
                continue
        self.last_issues = tuple(issues)
        if not products:
            raise ApiError("Etsy mağazasında görselli aktif ürün bulunamadı")
        return tuple(products)

    def _get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.http.request(
                "GET", url, headers=self.headers, provider="etsy", **kwargs
            )
        except ApiError as exc:
            if exc.status_code != 401 or self.refresh_access_token is None:
                raise
            access_token = self.refresh_access_token()
            self.headers["authorization"] = f"Bearer {access_token}"
            return self.http.request(
                "GET", url, headers=self.headers, provider="etsy", **kwargs
            )


def _image_url(image: dict[str, Any]) -> str:
    for key in ("url_fullxfull", "url_570xN", "url_170x135"):
        value = image.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            return value
    return ""


def _save_validated_jpeg(data: bytes, path: Path, content_type: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as source:
            source.verify()
        with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("görsel piksel sınırını aşıyor")
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError(f"desteklenmeyen görsel biçimi: {source.format}")
            if not content_type.startswith("image/"):
                raise ValueError("görsel MIME türü geçersiz")
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError) as exc:
        raise ApiError("Etsy geçersiz bir görsel döndürdü") from exc
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".jpg", delete=False) as temp:
            temp_path = Path(temp.name)
        image.save(temp_path, format="JPEG", quality=92, optimize=True)
        os.replace(temp_path, path)
    finally:
        image.close()
        if "temp_path" in locals() and temp_path.exists():
            temp_path.unlink()


def _write_marker(path: Path, value: str) -> None:
    with NamedTemporaryFile(
        "w", dir=path.parent, encoding="ascii", delete=False
    ) as temp:
        temp.write(value)
        temp.flush()
        os.fsync(temp.fileno())
        temp_path = Path(temp.name)
    os.replace(temp_path, path)


def _price(value: object) -> tuple[float, str]:
    if not isinstance(value, dict):
        raise ValueError("Etsy fiyat alanı geçersiz")
    amount = value.get("amount", 0)
    divisor = value.get("divisor", 100)
    try:
        divisor_value = float(divisor)
        if divisor_value <= 0:
            raise ValueError("Etsy fiyat divisor değeri geçersiz")
        price = float(amount) / divisor_value
        if price < 0:
            raise ValueError("Etsy fiyatı negatif olamaz")
    except (TypeError, ValueError) as exc:
        raise ValueError("Etsy fiyat alanı geçersiz") from exc
    return price, str(value.get("currency_code", "USD")).upper()


def _vertical(listing: dict[str, Any]) -> str:
    tags = [str(tag).lower() for tag in listing.get("tags", [])]
    taxonomy = {
        "airbnb": ("airbnb", "host", "welcome book", "vacation rental"),
        "wedding": ("wedding", "bridal", "bride"),
        "botanical": ("botanical", "plant", "floral"),
        "halloween": ("halloween", "spooky"),
    }
    joined = " ".join(tags)
    return next(
        (
            vertical
            for vertical, words in taxonomy.items()
            if any(word in joined for word in words)
        ),
        "etsy",
    )


def _validate_remote_image_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("Etsy görsel URL'si güvenli bir HTTPS adresi değil")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
        ".local"
    ):
        raise ValueError("Yerel görsel hostuna izin verilmez")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal and not literal.is_global:
        raise ValueError("Özel IP adresinden görsel indirilemez")
    if hostname.endswith(".test"):
        return
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Görsel hostu çözümlenemedi") from exc
    if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Görsel hostu özel IP adresine çözülüyor")
