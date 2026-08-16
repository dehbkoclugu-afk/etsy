from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
        ambiguous: bool = False,
        code: str | None = None,
        provider: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after
        self.ambiguous = ambiguous
        self.code = code
        self.provider = provider
        self.request_id = request_id


class JsonHttpClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        max_json_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(connect=10, read=30, write=30, pool=10),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self.sleep = sleep
        self.jitter = jitter
        self.max_json_bytes = max(1024, max_json_bytes)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "JsonHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        retry_safe: bool | None = None,
        provider: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        method_name = method.upper()
        safe = method_name in {"GET", "HEAD"} if retry_safe is None else retry_safe
        attempts = 3 if safe else 1
        for attempt in range(attempts):
            try:
                response = self.client.request(method_name, url, **kwargs)
            except httpx.RequestError as exc:
                if safe and attempt + 1 < attempts:
                    self._sleep(attempt, None)
                    continue
                raise ApiError(
                    f"Ağ isteği başarısız: {exc}",
                    retryable=safe,
                    ambiguous=not safe,
                    code="transport_error",
                    provider=provider,
                ) from exc
            if 300 <= response.status_code < 400:
                raise ApiError(
                    "Kimlik doğrulamalı API yönlendirmesi reddedildi",
                    status_code=response.status_code,
                    code="unexpected_redirect",
                    provider=provider,
                    request_id=_request_id(response),
                )
            if response.status_code < 400:
                _enforce_size(response, self.max_json_bytes)
                if not response.content:
                    return {}
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ApiError(
                        "API geçersiz JSON döndürdü",
                        status_code=response.status_code,
                        code="invalid_json",
                        provider=provider,
                        request_id=_request_id(response),
                    ) from exc
                if not isinstance(payload, dict):
                    raise ApiError(
                        "API yanıtı JSON nesnesi değil",
                        status_code=response.status_code,
                        code="invalid_shape",
                        provider=provider,
                        request_id=_request_id(response),
                    )
                return payload

            retryable = response.status_code in TRANSIENT_STATUSES
            retry_after = _retry_after(response)
            if safe and retryable and attempt + 1 < attempts:
                self._sleep(attempt, retry_after)
                continue
            error = _api_error(
                response,
                retryable=safe and retryable,
                retry_after=retry_after,
                provider=provider,
            )
            if not safe and retryable:
                error.ambiguous = True
                error.retryable = False
            raise error
        raise ApiError("API isteği tamamlanamadı", retryable=safe, provider=provider)

    def get_bytes(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int = 25 * 1024 * 1024,
        allowed_content_types: tuple[str, ...] = ("image/",),
        provider: str | None = None,
    ) -> tuple[bytes, str]:
        for attempt in range(3):
            try:
                with self.client.stream("GET", url, headers=headers) as response:
                    if 300 <= response.status_code < 400:
                        raise ApiError(
                            "Dosya yönlendirmesi reddedildi",
                            status_code=response.status_code,
                            code="unexpected_redirect",
                            provider=provider,
                        )
                    if response.status_code >= 400:
                        retryable = response.status_code in TRANSIENT_STATUSES
                        if retryable and attempt < 2:
                            self._sleep(attempt, _retry_after(response))
                            continue
                        raise _api_error(
                            response,
                            retryable=retryable,
                            retry_after=_retry_after(response),
                            provider=provider,
                        )
                    content_type = response.headers.get("content-type", "").split(
                        ";", 1
                    )[0]
                    if allowed_content_types and not any(
                        content_type.startswith(value)
                        for value in allowed_content_types
                    ):
                        raise ApiError(
                            f"Beklenmeyen dosya türü: {content_type or 'bilinmiyor'}",
                            code="invalid_content_type",
                            provider=provider,
                        )
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise ApiError(
                                f"İndirilen dosya {max_bytes} bayt sınırını aşıyor",
                                code="response_too_large",
                                provider=provider,
                            )
                    return bytes(data), content_type
            except httpx.RequestError as exc:
                if attempt < 2:
                    self._sleep(attempt, None)
                    continue
                raise ApiError(
                    f"Dosya indirilemedi: {exc}",
                    retryable=True,
                    code="transport_error",
                    provider=provider,
                ) from exc
        raise ApiError("Dosya indirilemedi", retryable=True, provider=provider)

    def _sleep(self, attempt: int, retry_after: float | None) -> None:
        base = retry_after if retry_after is not None else min(8.0, 2.0**attempt)
        self.sleep(base + self.jitter() * min(1.0, base * 0.25))


def _enforce_size(response: httpx.Response, limit: int) -> None:
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise ApiError(
                    "API yanıtı boyut sınırını aşıyor", code="response_too_large"
                )
        except ValueError:
            pass
    if len(response.content) > limit:
        raise ApiError("API yanıtı boyut sınırını aşıyor", code="response_too_large")


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(0.0, (parsed - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _request_id(response: httpx.Response) -> str | None:
    return response.headers.get("x-request-id") or response.headers.get("request-id")


def _api_error(
    response: httpx.Response,
    *,
    retryable: bool,
    retry_after: float | None,
    provider: str | None,
) -> ApiError:
    message = f"API hatası ({response.status_code})"
    code: str | None = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("message") or payload.get("error_description")
            error = payload.get("error")
            code_value = payload.get("code") or payload.get("error_code")
            code = str(code_value) if code_value is not None else None
            if isinstance(error, dict):
                detail = error.get("message") or error.get("type") or detail
                code = str(error.get("type") or code or "") or None
            elif isinstance(error, str):
                detail = error
            if detail:
                message = f"{message}: {detail}"
    except ValueError:
        pass
    return ApiError(
        message,
        status_code=response.status_code,
        retryable=retryable,
        retry_after=retry_after,
        code=code or f"http_{response.status_code}",
        provider=provider,
        request_id=_request_id(response),
    )
