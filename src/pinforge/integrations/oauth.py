from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from pinforge.security.secrets import SecretStoreProtocol


@dataclass(frozen=True, slots=True)
class OAuthAttempt:
    authorization_url: str
    state: str
    code_verifier: str
    redirect_uri: str
    provider: str = ""
    client_id: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0

    def validate(
        self, *, provider: str, client_id: str, now: float | None = None
    ) -> None:
        current = time.time() if now is None else now
        if self.provider and self.provider != provider:
            raise ValueError("OAuth sağlayıcısı eşleşmedi")
        if self.client_id and not secrets.compare_digest(self.client_id, client_id):
            raise ValueError("OAuth client kimliği eşleşmedi")
        if self.expires_at and current > self.expires_at:
            raise ValueError("OAuth denemesi sona erdi; bağlantıyı yeniden başlatın")


@dataclass(frozen=True, slots=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: float
    token_type: str = "Bearer"
    scopes: tuple[str, ...] = ()
    account_id: str | None = None

    def is_expiring(self, *, now: float | None = None, leeway: int = 120) -> bool:
        return self.expires_at <= (now if now is not None else time.time()) + leeway


def create_pkce_attempt(
    authorization_endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: tuple[str, ...],
    scope_separator: str = " ",
    extra: dict[str, str] | None = None,
    provider: str = "",
    lifetime_seconds: int = 600,
) -> OAuthAttempt:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    created_at = time.time()
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope_separator.join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **(extra or {}),
    }
    separator = "&" if "?" in authorization_endpoint else "?"
    return OAuthAttempt(
        authorization_url=authorization_endpoint + separator + urlencode(query),
        state=state,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        provider=provider,
        client_id=client_id,
        created_at=created_at,
        expires_at=created_at + lifetime_seconds,
    )


def create_state_attempt(
    authorization_endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: tuple[str, ...],
    scope_separator: str = ",",
    extra: dict[str, str] | None = None,
    provider: str = "",
    lifetime_seconds: int = 600,
) -> OAuthAttempt:
    state = secrets.token_urlsafe(32)
    created_at = time.time()
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope_separator.join(scopes),
        "state": state,
        **(extra or {}),
    }
    separator = "&" if "?" in authorization_endpoint else "?"
    return OAuthAttempt(
        authorization_url=authorization_endpoint + separator + urlencode(query),
        state=state,
        code_verifier="",
        redirect_uri=redirect_uri,
        provider=provider,
        client_id=client_id,
        created_at=created_at,
        expires_at=created_at + lifetime_seconds,
    )


def parse_callback(value: str, expected_state: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme and parsed.query:
        query = parse_qs(parsed.query)
    else:
        query = parse_qs(value.removeprefix("?").strip())
    state = query.get("state", [""])[0]
    if not secrets.compare_digest(state, expected_state):
        raise ValueError("OAuth state eşleşmedi; işlem güvenlik nedeniyle durduruldu")
    error = query.get("error", [""])[0]
    if error:
        description = query.get("error_description", [error])[0]
        raise ValueError(f"Yetkilendirme reddedildi: {description}")
    code = query.get("code", [""])[0]
    if not code:
        raise ValueError("Callback içinde authorization code bulunamadı")
    return code


def token_from_payload(
    payload: dict[str, object],
    *,
    now: float | None = None,
    existing_refresh_token: str | None = None,
) -> TokenSet:
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Token yanıtında access_token yok")
    expires_in = payload.get("expires_in", 3600)
    try:
        seconds = int(str(expires_in))
    except (TypeError, ValueError) as exc:
        raise ValueError("Token yanıtındaki expires_in geçersiz") from exc
    if not 0 < seconds <= 366 * 24 * 60 * 60:
        raise ValueError("Token yanıtındaki expires_in güvenli sınır dışında")
    token_type = str(payload.get("token_type", "Bearer"))
    if token_type.lower() != "bearer":
        raise ValueError(f"Desteklenmeyen token türü: {token_type}")
    refresh = payload.get("refresh_token") or existing_refresh_token
    raw_scope = payload.get("scope") or payload.get("scopes") or ""
    if isinstance(raw_scope, str):
        scopes = tuple(value for value in raw_scope.replace(",", " ").split() if value)
    elif isinstance(raw_scope, list):
        scopes = tuple(str(value) for value in raw_scope)
    else:
        scopes = ()
    account_value = payload.get("account_id") or payload.get("user_id")
    return TokenSet(
        access_token=access_token,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_at=(now if now is not None else time.time()) + seconds,
        token_type=token_type,
        scopes=scopes,
        account_id=str(account_value) if account_value else None,
    )


def save_token(store: SecretStoreProtocol, name: str, token: TokenSet) -> None:
    store.set(
        name,
        json.dumps(
            {
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at,
                "token_type": token.token_type,
                "scopes": token.scopes,
                "account_id": token.account_id,
            }
        ),
    )


def load_token(store: SecretStoreProtocol, name: str) -> TokenSet | None:
    raw = store.get(name)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        access_token = payload["access_token"]
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("access_token geçersiz")
        return TokenSet(
            access_token=access_token,
            refresh_token=(
                str(payload["refresh_token"]) if payload.get("refresh_token") else None
            ),
            expires_at=float(payload["expires_at"]),
            token_type=str(payload.get("token_type", "Bearer")),
            scopes=tuple(str(value) for value in payload.get("scopes", [])),
            account_id=str(payload["account_id"])
            if payload.get("account_id")
            else None,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Saklanan {name} tokenı bozuk") from exc
