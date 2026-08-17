from __future__ import annotations

from datetime import datetime, timezone

from django.conf import settings
from django.db import transaction

from pinforge.integrations.etsy.oauth import EtsyOAuth
from pinforge.integrations.oauth import TokenSet
from pinforge.integrations.pinterest.oauth import PinterestOAuth
from pinforge_web.models import ProviderConnection
from pinforge_web.provider_tokens import decrypt_token, encrypt_token


def valid_connection_token(connection: ProviderConnection) -> TokenSet:
    token = decrypt_token(connection.token_ciphertext)
    if not token.is_expiring():
        return token
    if not token.refresh_token:
        raise ValueError(f"{connection.get_provider_display()} connection expired")
    oauth: EtsyOAuth | PinterestOAuth
    if connection.provider == ProviderConnection.Provider.ETSY:
        oauth = EtsyOAuth(settings.ETSY_KEYSTRING)
    elif connection.provider == ProviderConnection.Provider.PINTEREST:
        oauth = PinterestOAuth(
            settings.PINTEREST_APP_ID,
            settings.PINTEREST_APP_SECRET,
        )
    else:
        raise ValueError("Unsupported provider connection")
    try:
        refreshed = oauth.refresh(token.refresh_token)
    finally:
        oauth.close()
    save_connection_token(connection=connection, token=refreshed)
    return refreshed


@transaction.atomic
def save_connection_token(
    *, connection: ProviderConnection, token: TokenSet
) -> ProviderConnection:
    locked = ProviderConnection.objects.select_for_update().get(pk=connection.pk)
    locked.token_ciphertext = encrypt_token(token)
    locked.scopes = list(token.scopes)
    locked.token_expires_at = datetime.fromtimestamp(token.expires_at, tz=timezone.utc)
    locked.active = True
    locked.save(
        update_fields=(
            "token_ciphertext",
            "scopes",
            "token_expires_at",
            "active",
            "updated_at",
        )
    )
    connection.token_ciphertext = locked.token_ciphertext
    connection.scopes = locked.scopes
    connection.token_expires_at = locked.token_expires_at
    connection.active = True
    return connection
