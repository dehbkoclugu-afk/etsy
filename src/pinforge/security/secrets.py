from __future__ import annotations

import os
from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError


class SecretStoreError(RuntimeError):
    pass


class SecretStoreProtocol(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class SecretStore:
    SERVICE = "PinForge"

    @staticmethod
    def _env_name(name: str) -> str:
        return "PINFORGE_" + name.upper()

    def get(self, name: str) -> str | None:
        environment = os.getenv(self._env_name(name))
        if environment:
            return environment
        try:
            return keyring.get_password(self.SERVICE, name)
        except KeyringError as exc:
            raise SecretStoreError(
                f"İşletim sistemi parola kasası okunamadı: {exc}"
            ) from exc

    def set(self, name: str, value: str) -> None:
        try:
            keyring.set_password(self.SERVICE, name, value)
        except KeyringError as exc:
            raise SecretStoreError(
                f"İşletim sistemi parola kasasına yazılamadı: {exc}"
            ) from exc

    def delete(self, name: str) -> None:
        os.environ.pop(self._env_name(name), None)
        try:
            keyring.delete_password(self.SERVICE, name)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise SecretStoreError(
                f"İşletim sistemi parola kasasından silinemedi: {exc}"
            ) from exc


class MemorySecretStore:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.values = dict(initial or {})

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)
