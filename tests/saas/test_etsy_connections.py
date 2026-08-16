from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

from pinforge.integrations.oauth import OAuthAttempt, TokenSet
from pinforge_web.models import Membership, Organization, ProviderConnection, User
from pinforge_web.etsy_connections import save_etsy_connection
from pinforge_web.provider_tokens import decrypt_token


class FakeEtsyOAuth:
    def __init__(self, keystring: str) -> None:
        assert keystring == "etsy-key"

    def begin(self, redirect_uri: str) -> OAuthAttempt:
        return OAuthAttempt(
            authorization_url="https://www.etsy.com/oauth/connect?state=safe-state",
            state="safe-state",
            code_verifier="verifier",
            redirect_uri=redirect_uri,
            provider="etsy",
            client_id="etsy-key",
            created_at=1.0,
            expires_at=9_999_999_999.0,
        )

    def exchange(self, code: str, attempt: OAuthAttempt) -> TokenSet:
        assert code == "auth-code"
        assert attempt.state == "safe-state"
        return TokenSet(
            access_token="12345.secret-access-token",
            refresh_token="secret-refresh-token",
            expires_at=2_000_000_000.0,
            scopes=("listings_r", "shops_r"),
        )

    def close(self) -> None:
        return None


def login_owner(client, *, name: str = "Example Shop") -> Organization:
    user = User.objects.create_user(
        email=f"{name.lower().replace(' ', '-')}@example.com",
        password="strong-pass-123",
    )
    organization = Organization.objects.create(name=name)
    Membership.objects.create(
        user=user,
        organization=organization,
        role=Membership.Role.OWNER,
    )
    client.force_login(user)
    return organization


@pytest.mark.django_db
@override_settings(
    ETSY_KEYSTRING="etsy-key",
    ETSY_REDIRECT_URI="https://pinforge.example/connections/etsy/callback/",
)
@patch("pinforge_web.views.EtsyOAuth", FakeEtsyOAuth)
def test_etsy_oauth_stores_encrypted_tenant_connection(client) -> None:
    organization = login_owner(client)

    start = client.post(reverse("etsy-connection-start"))
    assert start.status_code == 302
    assert start.url.startswith("https://www.etsy.com/oauth/connect")
    assert client.session["etsy_oauth_attempt"]["code_verifier"] == "verifier"

    callback = client.get(
        reverse("etsy-connection-callback"),
        {"state": "safe-state", "code": "auth-code"},
    )
    assert callback.status_code == 302
    assert callback.url == reverse("etsy-connection")

    connection = ProviderConnection.objects.get()
    assert connection.organization == organization
    assert connection.external_account_id == "12345"
    assert "secret-access-token" not in connection.token_ciphertext
    assert "secret-refresh-token" not in connection.token_ciphertext
    assert decrypt_token(connection.token_ciphertext).access_token == (
        "12345.secret-access-token"
    )
    assert "etsy_oauth_attempt" not in client.session


@pytest.mark.django_db
@override_settings(
    ETSY_KEYSTRING="etsy-key",
    ETSY_REDIRECT_URI="https://pinforge.example/connections/etsy/callback/",
)
@patch("pinforge_web.views.EtsyOAuth", FakeEtsyOAuth)
def test_etsy_callback_rejects_state_mismatch_and_consumes_attempt(client) -> None:
    login_owner(client)
    client.post(reverse("etsy-connection-start"))

    response = client.get(
        reverse("etsy-connection-callback"),
        {"state": "attacker-state", "code": "auth-code"},
        follow=True,
    )

    assert response.status_code == 200
    assert b"state" in response.content.lower()
    assert not ProviderConnection.objects.exists()
    assert "etsy_oauth_attempt" not in client.session


@pytest.mark.django_db
def test_etsy_disconnect_is_tenant_scoped(client) -> None:
    allowed = login_owner(client)
    forbidden = Organization.objects.create(name="Forbidden Shop")
    connection = ProviderConnection.objects.create(
        organization=forbidden,
        provider=ProviderConnection.Provider.ETSY,
        external_account_id="999",
        token_ciphertext="encrypted",
        scopes=["listings_r"],
        token_expires_at="2030-01-01T00:00:00Z",
    )

    response = client.post(
        reverse("etsy-connection-disconnect", args=(connection.id,))
    )

    assert response.status_code == 404
    assert ProviderConnection.objects.filter(pk=connection.id).exists()
    assert allowed != forbidden


@pytest.mark.django_db
def test_same_etsy_account_cannot_move_between_tenants() -> None:
    first = Organization.objects.create(name="First Shop")
    second = Organization.objects.create(name="Second Shop")
    token = TokenSet(
        access_token="12345.secret-access-token",
        refresh_token="secret-refresh-token",
        expires_at=2_000_000_000.0,
        scopes=("listings_r", "shops_r"),
    )
    connection = save_etsy_connection(organization=first, token=token)

    with pytest.raises(ValueError, match="another workspace"):
        save_etsy_connection(organization=second, token=token)

    connection.refresh_from_db()
    assert connection.organization == first
