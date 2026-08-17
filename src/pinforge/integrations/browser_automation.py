from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image, ImageOps

from pinforge.domain.models import PinDraft, PublishResult, SourceKind, SourceProduct
from pinforge.integrations.http import ApiError

ETSY_HOSTS = ("etsy.com", "www.etsy.com")
PINTEREST_CREATE_URL = "https://www.pinterest.com/pin-creation-tool/"


def _playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - installation guard
        raise RuntimeError(
            "Tarayıcı otomasyonu eksik. `python -m pip install -e .` çalıştırın."
        ) from exc
    return sync_playwright


class PersistentBrowser:
    def __init__(
        self,
        profile_directory: str | Path,
        *,
        channel: str = "chrome",
        headless: bool = False,
    ) -> None:
        if channel not in {"chrome", "msedge"}:
            raise ValueError("Tarayıcı kanalı chrome veya msedge olmalı")
        self.profile_directory = Path(profile_directory).expanduser().resolve()
        self.channel = channel
        self.headless = headless
        self._manager: Any = None
        self.context: Any = None

    def __enter__(self) -> "PersistentBrowser":
        self.profile_directory.mkdir(parents=True, exist_ok=True)
        self._manager = _playwright()().start()
        try:
            self.context = self._manager.chromium.launch_persistent_context(
                str(self.profile_directory),
                channel=self.channel,
                headless=self.headless,
                viewport={"width": 1440, "height": 1000},
                locale="tr-TR",
            )
        except Exception:
            self._manager.stop()
            self._manager = None
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self.context is not None:
            self.context.close()
        if self._manager is not None:
            self._manager.stop()

    def page(self) -> Any:
        if self.context is None:
            raise RuntimeError("Tarayıcı oturumu başlatılmadı")
        return self.context.pages[0] if self.context.pages else self.context.new_page()

    def prepare_login(self) -> None:
        page = self.page()
        page.goto("https://www.etsy.com/signin", wait_until="domcontentloaded")
        pinterest = self.context.new_page()
        pinterest.goto(
            "https://www.pinterest.com/login/", wait_until="domcontentloaded"
        )


class EtsyBrowserImporter:
    def __init__(self, browser: PersistentBrowser) -> None:
        self.browser = browser

    def import_shop(
        self,
        shop_url: str,
        cache_directory: str | Path,
        *,
        limit: int = 20,
    ) -> tuple[SourceProduct, ...]:
        _validate_https_host(shop_url, ETSY_HOSTS)
        cache = Path(cache_directory).expanduser().resolve()
        cache.mkdir(parents=True, exist_ok=True)
        page = self.browser.page()
        page.goto(shop_url, wait_until="domcontentloaded")
        listing_urls = self._listing_urls(page, limit=max(1, min(limit, 100)))
        if not listing_urls:
            raise ValueError("Etsy mağaza sayfasında aktif ürün bulunamadı")
        products: list[SourceProduct] = []
        for listing_url in listing_urls:
            page.goto(listing_url, wait_until="domcontentloaded")
            scripts = page.locator(
                "script[type='application/ld+json']"
            ).all_text_contents()
            product = product_from_json_ld(scripts, listing_url)
            image_paths = self._download_images(
                product["images"], cache / product["id"], limit=5
            )
            products.append(
                SourceProduct(
                    id=product["id"],
                    title=product["title"],
                    listing_url=listing_url,
                    price=product["price"],
                    currency=product["currency"],
                    vertical="general",
                    image_paths=image_paths,
                    description=product["description"],
                    kind=SourceKind.ETSY_API,
                )
            )
        return tuple(products)

    def _listing_urls(self, page: Any, *, limit: int) -> tuple[str, ...]:
        found: dict[str, None] = {}
        stable_rounds = 0
        for _ in range(12):
            values = page.locator("a[href*='/listing/']").evaluate_all(
                "els => els.map(el => el.href)"
            )
            before = len(found)
            for value in values:
                normalized = normalize_etsy_listing_url(str(value))
                if normalized:
                    found[normalized] = None
                if len(found) >= limit:
                    return tuple(found)[:limit]
            stable_rounds = stable_rounds + 1 if len(found) == before else 0
            if stable_rounds >= 2:
                break
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(500)
        return tuple(found)[:limit]

    def _download_images(
        self, urls: Iterable[str], directory: Path, *, limit: int
    ) -> tuple[Path, ...]:
        directory.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for index, url in enumerate(tuple(urls)[:limit], start=1):
            _validate_etsy_image_url(url)
            response = self.browser.context.request.get(url, timeout=30_000)
            if not response.ok:
                raise ApiError(
                    f"Etsy görseli indirilemedi: HTTP {response.status}",
                    status_code=response.status,
                    provider="etsy-browser",
                )
            data = response.body()
            if len(data) > 20 * 1024 * 1024:
                raise ValueError("Etsy görseli 20 MB sınırını aşıyor")
            target = directory / f"{index}.jpg"
            _save_image(data, target)
            paths.append(target)
        if not paths:
            raise ValueError("Etsy ürününde indirilebilir görsel bulunamadı")
        return tuple(paths)


class PinterestBrowserClient:
    def __init__(self, browser: PersistentBrowser, *, board_name: str) -> None:
        if not board_name.strip():
            raise ValueError("Pinterest pano adı gerekli")
        self.browser = browser
        self.board_name = board_name.strip()

    def publish(self, draft: PinDraft) -> PublishResult:
        if draft.image_path is None or not draft.image_path.is_file():
            raise ValueError("Yayınlanacak Pin görseli bulunamadı")
        page = self.browser.page()
        page.goto(PINTEREST_CREATE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        if (
            "/login" in page.url
            or page.get_by_text(re.compile("Oturum Aç|Log in", re.I)).count()
        ):
            raise ValueError(
                "Pinterest oturumu açık değil; önce `pinforge browser-login` çalıştırın"
            )
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(str(draft.image_path.resolve()))
        _fill_first(
            page,
            (
                "input[data-test-id='pin-draft-title']",
                "textarea[placeholder*='title' i]",
                "textarea[placeholder*='başlık' i]",
            ),
            draft.title,
        )
        _fill_first(
            page,
            (
                "div[data-test-id='pin-draft-description'] div[contenteditable='true']",
                "textarea[placeholder*='description' i]",
                "textarea[placeholder*='açıklama' i]",
            ),
            draft.description,
        )
        _fill_first(
            page,
            (
                "input[data-test-id='pin-draft-link']",
                "input[placeholder*='link' i]",
                "input[placeholder*='bağlantı' i]",
            ),
            draft.destination_url,
        )
        board_button = page.locator(
            "button[data-test-id='board-dropdown-select-button']"
        )
        if not board_button.count():
            board_button = page.get_by_role(
                "button", name=re.compile("Pano seç|Select board|Choose board", re.I)
            )
        board_button.first.click()
        page.get_by_text(self.board_name, exact=True).first.click()
        publish = page.locator("button[data-test-id='pin-builder-publish-button']")
        if not publish.count():
            publish = page.get_by_role(
                "button", name=re.compile("Yayınla|Publish", re.I)
            )
        publish.first.click()
        try:
            page.wait_for_function(
                """
                () => /\\/pin\\/\\d+/.test(location.pathname) ||
                  Array.from(document.querySelectorAll('a[href*="/pin/"]'))
                    .some(link => /\\/pin\\/\\d+/.test(link.getAttribute('href') || ''))
                """,
                timeout=30_000,
            )
        except Exception as exc:
            raise ApiError(
                "Pinterest yayın tıklandı ancak sonuç doğrulanamadı; tekrar göndermeyin",
                ambiguous=True,
                code="browser_publish_unknown",
                provider="pinterest-browser",
            ) from exc
        candidates = [page.url]
        candidates.extend(
            page.locator("a[href*='/pin/']").evaluate_all(
                "els => els.map(el => el.href)"
            )
        )
        match = next(
            (
                found
                for value in candidates
                if (found := re.search(r"/pin/(\d+)", value))
            ),
            None,
        )
        if not match:
            raise ApiError(
                "Pinterest Pin kimliği doğrulanamadı",
                ambiguous=True,
                code="browser_publish_unknown",
                provider="pinterest-browser",
            )
        remote_url = next(
            value for value in candidates if f"/pin/{match.group(1)}" in value
        )
        return PublishResult(remote_id=match.group(1), remote_url=remote_url)


def normalize_etsy_listing_url(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host != "etsy.com" and not host.endswith(".etsy.com"):
        return None
    match = re.search(r"/listing/(\d+)(?:/([^/?#]+))?", parsed.path)
    if not match:
        return None
    slug = f"/{match.group(2)}" if match.group(2) else ""
    return f"https://www.etsy.com/listing/{match.group(1)}{slug}"


def product_from_json_ld(scripts: Iterable[str], listing_url: str) -> dict[str, Any]:
    product: dict[str, Any] | None = None
    for script in scripts:
        try:
            payload = json.loads(script)
        except json.JSONDecodeError:
            continue
        product = _find_product(payload)
        if product is not None:
            break
    if product is None:
        raise ValueError("Etsy ürün bilgisi sayfadan okunamadı")
    match = re.search(r"/listing/(\d+)", listing_url)
    if not match:
        raise ValueError("Etsy ürün kimliği bağlantıda bulunamadı")
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = next((item for item in offers if isinstance(item, dict)), {})
    offers = offers if isinstance(offers, dict) else {}
    raw_images = product.get("image", ())
    if isinstance(raw_images, str):
        raw_images = (raw_images,)
    elif isinstance(raw_images, dict):
        raw_images = (raw_images.get("url"),)
    images = tuple(
        str(value.get("url") if isinstance(value, dict) else value)
        for value in raw_images
        if value
    )
    title = str(product.get("name") or "").strip()
    if not title or not images:
        raise ValueError("Etsy ürün başlığı veya görselleri eksik")
    try:
        price = float(offers.get("price", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Etsy ürün fiyatı okunamadı") from exc
    return {
        "id": match.group(1),
        "title": title[:500],
        "description": str(product.get("description") or "").strip()[:20_000],
        "price": price,
        "currency": str(offers.get("priceCurrency") or "USD")[:3].upper(),
        "images": images,
    }


def _find_product(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        kind = value.get("@type")
        if kind == "Product" or (isinstance(kind, list) and "Product" in kind):
            return value
        for nested in value.values():
            found = _find_product(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_product(nested)
            if found is not None:
                return found
    return None


def _fill_first(page: Any, selectors: tuple[str, ...], value: str) -> None:
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count():
            target = locator.first
            if target.get_attribute("contenteditable") == "true":
                target.click()
                target.fill(value)
            else:
                target.fill(value)
            return
    raise ValueError("Pinterest yayın alanı bulunamadı; arayüz değişmiş olabilir")


def _validate_https_host(url: str, allowed: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        host == value or host.endswith("." + value) for value in allowed
    ):
        raise ValueError("Beklenmeyen veya güvenli olmayan adres")


def _validate_etsy_image_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host == "etsystatic.com" or host.endswith(".etsystatic.com")
    ):
        raise ValueError("Etsy dışındaki görsel adresi reddedildi")


def _save_image(data: bytes, target: Path) -> None:
    from io import BytesIO

    try:
        with Image.open(BytesIO(data)) as source:
            source.verify()
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("Görsel piksel sınırını aşıyor")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.save(target, format="JPEG", quality=92, optimize=True)
            image.close()
    except OSError as exc:
        raise ValueError("Etsy geçersiz bir görsel döndürdü") from exc
