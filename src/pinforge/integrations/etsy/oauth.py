from __future__ import annotations

from pinforge.integrations.http import JsonHttpClient
from pinforge.integrations.oauth import (
    OAuthAttempt,
    TokenSet,
    create_pkce_attempt,
    token_from_payload,
)


class EtsyOAuth:
    AUTHORIZATION_ENDPOINT = "https://www.etsy.com/oauth/connect"
    TOKEN_ENDPOINT = "https://api.etsy.com/v3/public/oauth/token"
    SCOPES = ("listings_r", "shops_r")

    def __init__(self, keystring: str, *, http: JsonHttpClient | None = None) -> None:
        if not keystring:
            raise ValueError("Etsy keystring gerekli")
        self.keystring = keystring
        self.http = http or JsonHttpClient()

    def close(self) -> None:
        self.http.close()

    def begin(self, redirect_uri: str) -> OAuthAttempt:
        if not redirect_uri.startswith("https://"):
            raise ValueError("Etsy redirect URI kayıtlı bir HTTPS adresi olmalı")
        return create_pkce_attempt(
            self.AUTHORIZATION_ENDPOINT,
            client_id=self.keystring,
            redirect_uri=redirect_uri,
            scopes=self.SCOPES,
            provider="etsy",
        )

    def exchange(self, code: str, attempt: OAuthAttempt) -> TokenSet:
        attempt.validate(provider="etsy", client_id=self.keystring)
        payload = self.http.request(
            "POST",
            self.TOKEN_ENDPOINT,
            data={
                "grant_type": "authorization_code",
                "client_id": self.keystring,
                "redirect_uri": attempt.redirect_uri,
                "code": code,
                "code_verifier": attempt.code_verifier,
            },
            headers={"content-type": "application/x-www-form-urlencoded"},
            provider="etsy",
        )
        token = token_from_payload(payload)
        _validate_scopes(token, self.SCOPES)
        return token

    def refresh(self, refresh_token: str) -> TokenSet:
        payload = self.http.request(
            "POST",
            self.TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "client_id": self.keystring,
                "refresh_token": refresh_token,
            },
            headers={"content-type": "application/x-www-form-urlencoded"},
            provider="etsy",
        )
        token = token_from_payload(payload, existing_refresh_token=refresh_token)
        _validate_scopes(token, self.SCOPES)
        return token


def _validate_scopes(token: TokenSet, required: tuple[str, ...]) -> None:
    if token.scopes and not set(required).issubset(token.scopes):
        raise ValueError("Etsy tokenı gerekli izinleri içermiyor")
