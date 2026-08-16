from __future__ import annotations

import httpx

from pinforge.integrations.http import JsonHttpClient
from pinforge.integrations.oauth import (
    OAuthAttempt,
    TokenSet,
    create_state_attempt,
    token_from_payload,
)


class PinterestOAuth:
    AUTHORIZATION_ENDPOINT = "https://www.pinterest.com/oauth/"
    TOKEN_ENDPOINT = "https://api.pinterest.com/v5/oauth/token"
    SCOPES = ("boards:read", "pins:read", "pins:write", "user_accounts:read")

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        http: JsonHttpClient | None = None,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("Pinterest app ID ve app secret gerekli")
        self.app_id = app_id
        self.app_secret = app_secret
        self.http = http or JsonHttpClient()

    def close(self) -> None:
        self.http.close()

    def begin(self, redirect_uri: str) -> OAuthAttempt:
        if not redirect_uri.startswith(("http://", "https://")):
            raise ValueError("Pinterest redirect URI geçerli bir HTTP(S) adresi olmalı")
        return create_state_attempt(
            self.AUTHORIZATION_ENDPOINT,
            client_id=self.app_id,
            redirect_uri=redirect_uri,
            scopes=self.SCOPES,
            provider="pinterest",
        )

    def exchange(self, code: str, attempt: OAuthAttempt) -> TokenSet:
        attempt.validate(provider="pinterest", client_id=self.app_id)
        payload = self.http.request(
            "POST",
            self.TOKEN_ENDPOINT,
            auth=httpx.BasicAuth(self.app_id, self.app_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": attempt.redirect_uri,
            },
            headers={"content-type": "application/x-www-form-urlencoded"},
            provider="pinterest",
        )
        token = token_from_payload(payload)
        _validate_scopes(token, self.SCOPES)
        return token

    def refresh(self, refresh_token: str) -> TokenSet:
        payload = self.http.request(
            "POST",
            self.TOKEN_ENDPOINT,
            auth=httpx.BasicAuth(self.app_id, self.app_secret),
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            provider="pinterest",
        )
        token = token_from_payload(payload, existing_refresh_token=refresh_token)
        _validate_scopes(token, self.SCOPES)
        return token


def _validate_scopes(token: TokenSet, required: tuple[str, ...]) -> None:
    if token.scopes and not set(required).issubset(token.scopes):
        raise ValueError("Pinterest tokenı gerekli izinleri içermiyor")
