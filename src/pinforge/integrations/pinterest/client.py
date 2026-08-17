from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import date
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from PIL import Image

from pinforge.domain.models import Board, PinDraft, PublishResult
from pinforge.integrations.http import ApiError, JsonHttpClient


class PinterestClient:
    API_ROOT = "https://api.pinterest.com/v5"
    MAX_IMAGE_BYTES = 20 * 1024 * 1024

    def __init__(
        self,
        access_token: str,
        *,
        http: JsonHttpClient | None = None,
        allowed_destination_hosts: tuple[str, ...] = (),
        refresh_access_token: Callable[[], str] | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("Pinterest access token gerekli")
        self.http = http or JsonHttpClient()
        self.allowed_destination_hosts = tuple(
            host.lower() for host in allowed_destination_hosts
        )
        self.refresh_access_token = refresh_access_token
        self.headers = {
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
        }

    def close(self) -> None:
        self.http.close()

    def list_boards(self) -> tuple[Board, ...]:
        boards: list[Board] = []
        bookmark: str | None = None
        seen_bookmarks: set[str] = set()
        for _page in range(100):
            params: dict[str, str | int] = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            payload = self._safe_get(f"{self.API_ROOT}/boards", params=params)
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise ApiError("Pinterest board yanıtında items listesi yok")
            for item in items:
                if isinstance(item, dict) and item.get("id") and item.get("name"):
                    boards.append(
                        Board(
                            id=str(item["id"]),
                            name=str(item["name"]),
                            description=str(item.get("description", "")),
                        )
                    )
            bookmark_value = payload.get("bookmark")
            bookmark = bookmark_value if isinstance(bookmark_value, str) else None
            if not bookmark:
                break
            if bookmark in seen_bookmarks:
                raise ApiError(
                    "Pinterest tekrarlanan bookmark döndürdü", provider="pinterest"
                )
            seen_bookmarks.add(bookmark)
        else:
            raise ApiError(
                "Pinterest board sayfalama sınırı aşıldı", provider="pinterest"
            )
        return tuple(boards)

    def get_user_account(self) -> dict[str, Any]:
        payload = self._safe_get(f"{self.API_ROOT}/user_account")
        account_id = payload.get("id") or payload.get("username")
        if not account_id:
            raise ApiError("Pinterest hesap yanıtında kimlik yok")
        return payload

    def _safe_get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.http.request(
                "GET", url, headers=self.headers, provider="pinterest", **kwargs
            )
        except ApiError as exc:
            if exc.status_code != 401 or self.refresh_access_token is None:
                raise
            access_token = self.refresh_access_token()
            self.headers["authorization"] = f"Bearer {access_token}"
            return self.http.request(
                "GET", url, headers=self.headers, provider="pinterest", **kwargs
            )

    def publish(self, draft: PinDraft) -> PublishResult:
        if not draft.board_id:
            raise ValueError("Pinterest board seçilmedi")
        if not draft.image_path or not draft.image_path.is_file():
            raise ValueError("Yayınlanacak pin görseli bulunamadı")
        image_bytes = draft.image_path.read_bytes()
        if len(image_bytes) > self.MAX_IMAGE_BYTES:
            raise ValueError("Pinterest görseli 20 MB sınırını aşıyor")
        try:
            with Image.open(BytesIO(image_bytes), formats=("JPEG", "PNG")) as image:
                image.verify()
                image_format = image.format
        except OSError as exc:
            raise ValueError("Pinterest görsel dosyası geçersiz") from exc
        content_types = {"JPEG": "image/jpeg", "PNG": "image/png"}
        content_type = content_types.get(str(image_format))
        if not content_type:
            raise ValueError(
                f"Pinterest için desteklenmeyen görsel biçimi: {image_format}"
            )
        parsed = urlparse(draft.destination_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            raise ValueError("Pinterest hedef bağlantısı HTTPS olmalı")
        if self.allowed_destination_hosts and not any(
            host == allowed or host.endswith("." + allowed)
            for allowed in self.allowed_destination_hosts
        ):
            raise ValueError(f"Pinterest hedef hostuna izin verilmiyor: {host}")
        payload = self.http.request(
            "POST",
            f"{self.API_ROOT}/pins",
            headers=self.headers,
            json={
                "board_id": draft.board_id,
                "title": draft.title[:100],
                "description": draft.description[:500],
                "alt_text": draft.alt_text[:500],
                "link": draft.destination_url,
                "media_source": {
                    "source_type": "image_base64",
                    "content_type": content_type,
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                },
            },
            retry_safe=False,
            provider="pinterest",
        )
        remote_id = payload.get("id")
        if not isinstance(remote_id, str) or not remote_id:
            raise ApiError("Pinterest pin yanıtında id yok")
        return PublishResult(remote_id=remote_id, remote_url=_pin_url(payload))

    def pin_analytics(
        self, pin_id: str, start_date: date, end_date: date
    ) -> dict[str, int]:
        if not pin_id or any(character in pin_id for character in "/?#"):
            raise ValueError("Pinterest pin kimliği geçersiz")
        if end_date < start_date or (end_date - start_date).days > 90:
            raise ValueError("Analytics tarih aralığı 0-90 gün olmalı")
        payload = self._safe_get(
            f"{self.API_ROOT}/pins/{pin_id}/analytics",
            params={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "metric_types": "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK",
                "app_types": "ALL",
                "split_field": "NO_SPLIT",
            },
        )
        return _analytics_metrics(payload)


def _pin_url(payload: dict[str, Any]) -> str | None:
    for key in ("link", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    pin_id = payload.get("id")
    return f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None


def _analytics_metrics(payload: dict[str, Any]) -> dict[str, int]:
    wanted = {"IMPRESSION", "SAVE", "PIN_CLICK", "OUTBOUND_CLICK"}
    found = {key: 0 for key in wanted}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = str(key).upper()
                candidate = nested.get("value") if isinstance(nested, dict) else nested
                if normalized in wanted and isinstance(candidate, (int, float)):
                    found[normalized] = max(found[normalized], max(0, int(candidate)))
                visit(nested)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return found
