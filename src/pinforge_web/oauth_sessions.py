from __future__ import annotations

from typing import Any

from pinforge.integrations.oauth import OAuthAttempt


def serialize_attempt(attempt: OAuthAttempt) -> dict[str, object]:
    return {
        "authorization_url": attempt.authorization_url,
        "state": attempt.state,
        "code_verifier": attempt.code_verifier,
        "redirect_uri": attempt.redirect_uri,
        "provider": attempt.provider,
        "client_id": attempt.client_id,
        "created_at": attempt.created_at,
        "expires_at": attempt.expires_at,
    }


def deserialize_attempt(payload: Any, *, provider: str) -> OAuthAttempt:
    if not isinstance(payload, dict):
        raise ValueError(f"{provider.title()} connection attempt is missing")
    try:
        attempt = OAuthAttempt(
            authorization_url=str(payload["authorization_url"]),
            state=str(payload["state"]),
            code_verifier=str(payload["code_verifier"]),
            redirect_uri=str(payload["redirect_uri"]),
            provider=str(payload["provider"]),
            client_id=str(payload["client_id"]),
            created_at=float(payload["created_at"]),
            expires_at=float(payload["expires_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{provider.title()} connection attempt is invalid") from error
    if attempt.provider != provider:
        raise ValueError(f"{provider.title()} connection attempt is invalid")
    return attempt
