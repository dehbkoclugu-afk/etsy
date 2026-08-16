from __future__ import annotations

import json
from dataclasses import asdict

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from pinforge.integrations.oauth import TokenSet


def encrypt_token(token: TokenSet) -> str:
    payload = json.dumps(asdict(token), separators=(",", ":")).encode()
    return _fernet().encrypt(payload).decode("ascii")


def decrypt_token(ciphertext: str) -> TokenSet:
    try:
        payload = json.loads(_fernet().decrypt(ciphertext.encode("ascii")))
        return TokenSet(
            access_token=str(payload["access_token"]),
            refresh_token=(
                str(payload["refresh_token"])
                if payload.get("refresh_token")
                else None
            ),
            expires_at=float(payload["expires_at"]),
            token_type=str(payload.get("token_type", "Bearer")),
            scopes=tuple(str(value) for value in payload.get("scopes", [])),
            account_id=(
                str(payload["account_id"]) if payload.get("account_id") else None
            ),
        )
    except (InvalidToken, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Stored provider token cannot be decrypted") from error


def _fernet() -> Fernet:
    try:
        return Fernet(settings.TOKEN_ENCRYPTION_KEY.encode("ascii"))
    except (AttributeError, ValueError) as error:
        raise ValueError("Invalid provider token encryption key") from error
