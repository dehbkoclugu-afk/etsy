from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pinforge.domain.models import PinDraft, PublishResult
from pinforge.integrations.http import ApiError

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

    def prepare_pinterest_login(self) -> None:
        page = self.page()
        page.goto("https://www.pinterest.com/login/", wait_until="domcontentloaded")


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
