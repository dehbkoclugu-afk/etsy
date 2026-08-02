from __future__ import annotations

import os
import time
from pathlib import Path

from pinforge.data.repository import PinRepository
from pinforge.domain.models import BrandKit
from pinforge.integrations.anthropic import AnthropicCopyGenerator
from pinforge.integrations.etsy import EtsyClient, EtsyOAuth
from pinforge.integrations.oauth import load_token, save_token
from pinforge.integrations.pinterest import PinterestClient, PinterestOAuth
from pinforge.profiles import Profile, ProfileStore
from pinforge.security.secrets import SecretStoreProtocol
from pinforge.security.file_lock import FileLock
from pinforge.settings import AppSettings, SettingsStore

ANTHROPIC_API_KEY = "anthropic_api_key"
ETSY_SHARED_SECRET = "etsy_shared_secret"
ETSY_TOKEN = "etsy_oauth_token"
PINTEREST_APP_SECRET = "pinterest_app_secret"
PINTEREST_TOKEN = "pinterest_oauth_token"


def default_data_directory() -> Path:
    if os.name == "nt" and os.getenv("APPDATA"):
        return Path(os.environ["APPDATA"]) / "PinForge"
    base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "pinforge"


class PinForgeRuntime:
    def __init__(
        self,
        data_directory: str | Path,
        secrets: SecretStoreProtocol,
    ) -> None:
        self.root_data_directory = Path(data_directory).expanduser().resolve()
        self.root_data_directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.root_data_directory, 0o700)
        self.secrets = secrets
        self._resources: list[object] = []
        self._clients: dict[str, object] = {}
        self.profile_store = ProfileStore(self.root_data_directory)
        self.profile_id = self.profile_store.active_id
        self._open_profile(self.profile_id)
        self._prune_cache()

    def close(self) -> None:
        self._close_clients()
        self.repository.close()

    def _close_clients(self) -> None:
        for resource in reversed(self._resources):
            close = getattr(resource, "close", None)
            if close:
                close()
        self._resources.clear()
        self._clients.clear()

    def profiles(self) -> tuple[Profile, ...]:
        return self.profile_store.list()

    def create_profile(self, name: str) -> Profile:
        profile = self.profile_store.create(name, self.settings)
        self.repository.audit(
            "profile_created", entity_type="profile", entity_id=profile.id
        )
        return profile

    def switch_profile(self, profile_id: str) -> None:
        if profile_id == self.profile_id:
            return
        self.profile_store.get(profile_id)
        previous_id = self.profile_id
        self._close_clients()
        self.repository.close()
        try:
            self.profile_id = profile_id
            self._open_profile(profile_id)
            self._prune_cache()
            self.profile_store.activate(profile_id)
        except Exception:
            self.repository.close()
            self.profile_id = previous_id
            self._open_profile(previous_id)
            raise
        self.repository.audit(
            "profile_activated", entity_type="profile", entity_id=profile_id
        )

    def secret_key(self, base_name: str) -> str:
        return (
            base_name
            if self.profile_id == "default"
            else f"{base_name}__{self.profile_id}"
        )

    def get_secret(self, base_name: str) -> str | None:
        return self.secrets.get(self.secret_key(base_name))

    def set_secret(self, base_name: str, value: str) -> None:
        self.secrets.set(self.secret_key(base_name), value)

    def _open_profile(self, profile_id: str) -> None:
        self.data_directory = self.profile_store.directory(profile_id)
        self.data_directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.data_directory, 0o700)
        self.settings_store = SettingsStore(self.data_directory / "settings.json")
        self.settings = self.settings_store.load()
        self.repository = PinRepository(self.data_directory / "pinforge.db")

    def save_settings(self, settings: AppSettings) -> None:
        self.settings_store.save(settings)
        self.settings = settings
        for name in tuple(self._clients):
            resource = self._clients.pop(name)
            if resource in self._resources:
                self._resources.remove(resource)
            close = getattr(resource, "close", None)
            if close:
                close()
        self.repository.audit(
            "settings_updated", entity_type="settings", details={"version": 1}
        )

    def brand_kit(self, *, shop_name: str | None = None) -> BrandKit:
        return BrandKit(
            shop_name=shop_name or self.settings.brand_shop_name,
            primary=self.settings.brand_primary,
            accent=self.settings.brand_accent,
            surface=self.settings.brand_surface,
            ink=self.settings.brand_ink,
        )

    def board_for_vertical(self, vertical: str) -> str:
        return self.settings.pinterest_board_mappings.get(
            vertical, self.settings.pinterest_default_board_id
        )

    def copy_generator(self) -> AnthropicCopyGenerator:
        key = self.get_secret(ANTHROPIC_API_KEY)
        if not key:
            raise ValueError("Anthropic API anahtarı ayarlanmamış")
        cached = self._clients.get("anthropic")
        if isinstance(cached, AnthropicCopyGenerator):
            return cached
        generator = AnthropicCopyGenerator(
            key,
            model=self.settings.anthropic_model,
            cache_directory=self.data_directory / "ai-cache",
            language=self.settings.ai_language,
        )
        self._remember("anthropic", generator)
        return generator

    def etsy_oauth(self) -> EtsyOAuth:
        oauth = EtsyOAuth(self.settings.etsy_keystring)
        self._resources.append(oauth)
        return oauth

    def etsy_client(self) -> EtsyClient:
        shared_secret = self.get_secret(ETSY_SHARED_SECRET)
        if not shared_secret:
            raise ValueError("Etsy shared secret ayarlanmamış")
        token = self._valid_token("etsy", self.secret_key(ETSY_TOKEN))
        cached = self._clients.get("etsy")
        if (
            isinstance(cached, EtsyClient)
            and cached.headers["authorization"] == f"Bearer {token.access_token}"
        ):
            return cached
        client = EtsyClient(
            self.settings.etsy_keystring,
            shared_secret,
            token.access_token,
            refresh_access_token=lambda: self._force_refresh(
                "etsy", self.secret_key(ETSY_TOKEN)
            ),
        )
        self._remember("etsy", client)
        return client

    def pinterest_oauth(self) -> PinterestOAuth:
        secret = self.get_secret(PINTEREST_APP_SECRET)
        if not secret:
            raise ValueError("Pinterest app secret ayarlanmamış")
        oauth = PinterestOAuth(self.settings.pinterest_app_id, secret)
        self._resources.append(oauth)
        return oauth

    def pinterest_client(self) -> PinterestClient:
        token = self._valid_token("pinterest", self.secret_key(PINTEREST_TOKEN))
        cached = self._clients.get("pinterest")
        if (
            isinstance(cached, PinterestClient)
            and cached.headers["authorization"] == f"Bearer {token.access_token}"
        ):
            return cached
        client = PinterestClient(
            token.access_token,
            allowed_destination_hosts=self.settings.allowed_destination_hosts,
            refresh_access_token=lambda: self._force_refresh(
                "pinterest", self.secret_key(PINTEREST_TOKEN)
            ),
        )
        self._remember("pinterest", client)
        return client

    def disconnect(self, provider: str) -> None:
        names = {
            "etsy": self.secret_key(ETSY_TOKEN),
            "pinterest": self.secret_key(PINTEREST_TOKEN),
        }
        if provider not in names:
            raise ValueError(f"Bilinmeyen sağlayıcı: {provider}")
        self.secrets.delete(names[provider])
        resource = self._clients.pop(provider, None)
        if resource in self._resources:
            self._resources.remove(resource)
        close = getattr(resource, "close", None)
        if close:
            close()
        self.repository.audit(
            "provider_disconnected", entity_type="auth", provider=provider
        )

    def validate_board_id(self, board_id: str) -> None:
        if board_id not in {
            board.id for board in self.pinterest_client().list_boards()
        }:
            raise ValueError(
                f"Pinterest board bulunamadı veya erişilemiyor: {board_id}"
            )

    def _valid_token(self, provider: str, secret_name: str):  # noqa: ANN202
        with FileLock(self.data_directory / f"{provider}-refresh.lock"):
            token = load_token(self.secrets, secret_name)
            if token is None:
                raise ValueError(f"{provider.title()} hesabı bağlı değil")
            if not token.is_expiring():
                return token
            if not token.refresh_token:
                raise ValueError(
                    f"{provider.title()} tokenı sona ermiş ve refresh token yok"
                )
            refresh_token = token.refresh_token
            if provider == "etsy":
                token = self.etsy_oauth().refresh(refresh_token)
            else:
                token = self.pinterest_oauth().refresh(refresh_token)
            save_token(self.secrets, secret_name, token)
            self.repository.audit(
                "token_refreshed",
                entity_type="auth",
                provider=provider,
                details={"scopes": token.scopes, "account_id": token.account_id},
            )
            return token

    def _force_refresh(self, provider: str, secret_name: str) -> str:
        with FileLock(self.data_directory / f"{provider}-refresh.lock"):
            current = load_token(self.secrets, secret_name)
            if current is None or not current.refresh_token:
                raise ValueError(f"{provider.title()} refresh tokenı bulunamadı")
            refresh_token = current.refresh_token
            if provider == "etsy":
                token = self.etsy_oauth().refresh(refresh_token)
            else:
                token = self.pinterest_oauth().refresh(refresh_token)
            save_token(self.secrets, secret_name, token)
            self.repository.audit(
                "token_forced_refresh", entity_type="auth", provider=provider
            )
            return token.access_token

    def _remember(self, name: str, resource: object) -> None:
        old = self._clients.get(name)
        if old in self._resources:
            self._resources.remove(old)
            close = getattr(old, "close", None)
            if close:
                close()
        self._clients[name] = resource
        self._resources.append(resource)

    def _prune_cache(self) -> None:
        cutoff = time.time() - self.settings.cache_max_age_days * 86400
        candidates: list[Path] = []
        for folder_name in ("ai-cache", "etsy-cache"):
            folder = self.data_directory / folder_name
            if folder.is_dir():
                candidates.extend(path for path in folder.rglob("*") if path.is_file())
        for path in candidates:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
        remaining = sorted(
            (path for path in candidates if path.exists()),
            key=lambda path: path.stat().st_mtime,
        )
        total = sum(path.stat().st_size for path in remaining)
        limit = self.settings.cache_max_megabytes * 1024 * 1024
        for path in remaining:
            if total <= limit:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
            except OSError:
                continue
