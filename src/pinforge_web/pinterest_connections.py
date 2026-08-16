from __future__ import annotations

from datetime import datetime, timezone

from django.db import transaction
from django.utils import timezone as django_timezone

from pinforge.integrations.oauth import TokenSet
from pinforge.integrations.pinterest.client import PinterestClient
from pinforge_web.models import Organization, PinterestBoard, ProviderConnection
from pinforge_web.provider_connections import valid_connection_token
from pinforge_web.provider_tokens import encrypt_token


@transaction.atomic
def save_pinterest_connection(
    *, organization: Organization, account_id: str, token: TokenSet
) -> ProviderConnection:
    normalized = account_id.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("Pinterest account ID is invalid")
    connection = (
        ProviderConnection.objects.select_for_update()
        .filter(
            provider=ProviderConnection.Provider.PINTEREST,
            external_account_id=normalized,
        )
        .first()
    )
    if connection is not None and connection.organization_id != organization.id:
        raise ValueError(
            "This Pinterest account is already connected to another workspace"
        )
    values = {
        "organization": organization,
        "token_ciphertext": encrypt_token(token),
        "scopes": list(token.scopes),
        "token_expires_at": datetime.fromtimestamp(token.expires_at, tz=timezone.utc),
        "active": True,
    }
    if connection is None:
        return ProviderConnection.objects.create(
            provider=ProviderConnection.Provider.PINTEREST,
            external_account_id=normalized,
            **values,
        )
    for field, value in values.items():
        setattr(connection, field, value)
    connection.save(update_fields=(*values, "updated_at"))
    return connection


def sync_pinterest_boards(connection: ProviderConnection) -> int:
    if connection.provider != ProviderConnection.Provider.PINTEREST:
        raise ValueError("Connection is not a Pinterest connection")
    token = valid_connection_token(connection)
    client = PinterestClient(token.access_token)
    try:
        boards = client.list_boards()
    finally:
        client.close()
    seen: set[str] = set()
    with transaction.atomic():
        for board in boards:
            PinterestBoard.objects.update_or_create(
                connection=connection,
                external_board_id=board.id,
                defaults={
                    "organization": connection.organization,
                    "name": board.name[:200],
                    "description": board.description[:500],
                    "active": True,
                    "synchronized_at": django_timezone.now(),
                },
            )
            seen.add(board.id)
        PinterestBoard.objects.filter(connection=connection).exclude(
            external_board_id__in=seen
        ).update(active=False)
    return len(boards)
