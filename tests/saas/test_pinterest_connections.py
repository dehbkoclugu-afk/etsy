from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from pinforge.integrations.oauth import OAuthAttempt, TokenSet
from pinforge_web.models import (
    Creative,
    Job,
    Listing,
    Membership,
    Organization,
    PinPublication,
    PinterestBoard,
    ProviderConnection,
    Shop,
    User,
)
from pinforge_web.publishing import enqueue_pinterest_publication


class FakePinterestOAuth:
    def __init__(self, app_id: str, app_secret: str) -> None:
        assert (app_id, app_secret) == ("pinterest-app", "pinterest-secret")

    def begin(self, redirect_uri: str) -> OAuthAttempt:
        return OAuthAttempt(
            authorization_url="https://www.pinterest.com/oauth/?state=safe-state",
            state="safe-state",
            code_verifier="verifier",
            redirect_uri=redirect_uri,
            provider="pinterest",
            client_id="pinterest-app",
            created_at=1.0,
            expires_at=9_999_999_999.0,
        )

    def exchange(self, code: str, attempt: OAuthAttempt) -> TokenSet:
        assert code == "auth-code"
        assert attempt.state == "safe-state"
        return token()

    def close(self) -> None:
        return None


class FakePinterestClient:
    def __init__(self, access_token: str) -> None:
        assert access_token == "pinterest-access-token"

    def get_user_account(self) -> dict[str, str]:
        return {"id": "pinterest-account"}

    def close(self) -> None:
        return None


def token() -> TokenSet:
    return TokenSet(
        access_token="pinterest-access-token",
        refresh_token="pinterest-refresh-token",
        expires_at=(timezone.now() + timedelta(hours=1)).timestamp(),
        scopes=("boards:read", "pins:write"),
    )


def login_owner(client) -> Organization:
    user = User.objects.create_user(
        email="owner@example.com", password="strong-pass-123"
    )
    organization = Organization.objects.create(name="Example Shop")
    Membership.objects.create(
        user=user,
        organization=organization,
        role=Membership.Role.OWNER,
    )
    client.force_login(user)
    return organization


@pytest.mark.django_db
@override_settings(
    PINTEREST_APP_ID="pinterest-app",
    PINTEREST_APP_SECRET="pinterest-secret",
    PINTEREST_REDIRECT_URI=("https://pinforge.example/connections/pinterest/callback/"),
)
@patch("pinforge_web.views.sync_pinterest_boards", return_value=2)
@patch("pinforge_web.views.PinterestClient", FakePinterestClient)
@patch("pinforge_web.views.PinterestOAuth", FakePinterestOAuth)
def test_pinterest_oauth_stores_tenant_connection(_sync_boards, client) -> None:
    organization = login_owner(client)

    start = client.post(reverse("pinterest-connection-start"))
    assert start.status_code == 302
    assert start.url.startswith("https://www.pinterest.com/oauth/")

    callback = client.get(
        reverse("pinterest-connection-callback"),
        {"state": "safe-state", "code": "auth-code"},
    )

    connection = ProviderConnection.objects.get()
    assert callback.status_code == 302
    assert connection.organization == organization
    assert connection.provider == ProviderConnection.Provider.PINTEREST
    assert connection.external_account_id == "pinterest-account"
    assert "pinterest-access-token" not in connection.token_ciphertext


@pytest.mark.django_db
def test_ready_creative_can_be_queued_for_pinterest() -> None:
    organization = Organization.objects.create(name="Example Shop")
    other = Organization.objects.create(name="Other Shop")
    shop = Shop.objects.create(
        organization=organization,
        source=Shop.Source.MANUAL,
        source_shop_id="manual",
        name="Catalog",
    )
    listing = Listing.objects.create(
        organization=organization,
        shop=shop,
        source_listing_id="manual:one",
        title="Ceramic cup",
        listing_url="https://www.etsy.com/listing/123/example",
        price_minor=89000,
        currency="TRY",
    )
    creative = Creative.objects.create(
        organization=organization,
        listing=listing,
        template_id=Creative.Template.TEXT_OVERLAY,
        title="A handmade ceramic cup",
        description="Made slowly by hand.",
        alt_text="A cream ceramic cup on a wooden table",
        destination_url=listing.listing_url,
        status=Creative.Status.READY,
    )
    connection = ProviderConnection.objects.create(
        organization=organization,
        provider=ProviderConnection.Provider.PINTEREST,
        external_account_id="pinterest-account",
        token_ciphertext="encrypted",
        scopes=["pins:write"],
        token_expires_at=timezone.now() + timedelta(hours=1),
    )
    board = PinterestBoard.objects.create(
        organization=organization,
        connection=connection,
        external_board_id="board-one",
        name="Handmade finds",
    )

    publication = enqueue_pinterest_publication(
        organization=organization,
        creative=creative,
        board=board,
        scheduled_at=None,
    )

    job = Job.objects.get(kind=Job.Kind.PUBLISH_PINTEREST)
    assert publication.status == PinPublication.Status.QUEUED
    assert job.payload == {"publication_id": str(publication.id)}
    assert job.due_at == publication.scheduled_at
    with pytest.raises(ValueError, match="organization"):
        enqueue_pinterest_publication(
            organization=other,
            creative=creative,
            board=board,
            scheduled_at=None,
        )
