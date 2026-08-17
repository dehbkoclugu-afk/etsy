from __future__ import annotations

from datetime import datetime, timezone
from django.db import transaction

from pinforge.integrations.oauth import TokenSet
from pinforge_web.models import Organization, ProviderConnection
from pinforge_web.provider_tokens import encrypt_token


@transaction.atomic
def save_etsy_connection(
    *, organization: Organization, token: TokenSet
) -> ProviderConnection:
    account_id = _account_id(token)
    connection = (
        ProviderConnection.objects.select_for_update()
        .filter(
            provider=ProviderConnection.Provider.ETSY,
            external_account_id=account_id,
        )
        .first()
    )
    if connection is not None and connection.organization_id != organization.id:
        raise ValueError("This Etsy account is already connected to another workspace")
    values = {
        "organization": organization,
        "token_ciphertext": encrypt_token(token),
        "scopes": list(token.scopes),
        "token_expires_at": datetime.fromtimestamp(token.expires_at, tz=timezone.utc),
        "active": True,
    }
    if connection is None:
        connection = ProviderConnection.objects.create(
            provider=ProviderConnection.Provider.ETSY,
            external_account_id=account_id,
            **values,
        )
    else:
        for field, value in values.items():
            setattr(connection, field, value)
        connection.save(update_fields=(*values, "updated_at"))
    return connection


def _account_id(token: TokenSet) -> str:
    if token.account_id:
        return token.account_id
    prefix, separator, _remainder = token.access_token.partition(".")
    if separator and prefix.isdigit():
        return prefix
    raise ValueError("Etsy token response did not identify the account")
