from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from pinforge.integrations.oauth import (
    create_pkce_attempt,
    load_token,
    parse_callback,
    save_token,
    token_from_payload,
)
from pinforge.security import MemorySecretStore


def test_pkce_callback_and_secret_token_round_trip() -> None:
    attempt = create_pkce_attempt(
        "https://example.test/authorize",
        client_id="client",
        redirect_uri="https://app.test/callback",
        scopes=("read", "write"),
    )
    query = parse_qs(urlparse(attempt.authorization_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [attempt.state]
    assert 43 <= len(attempt.code_verifier) <= 128
    assert (
        parse_callback(
            f"https://app.test/callback?code=ok&state={attempt.state}", attempt.state
        )
        == "ok"
    )

    with pytest.raises(ValueError, match="state"):
        parse_callback("https://app.test/callback?code=ok&state=wrong", attempt.state)

    store = MemorySecretStore()
    token = token_from_payload(
        {"access_token": "access", "refresh_token": "refresh", "expires_in": 60},
        now=100,
    )
    save_token(store, "token", token)
    assert load_token(store, "token") == token
