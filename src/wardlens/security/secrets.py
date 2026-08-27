from __future__ import annotations

import os
from typing import Protocol


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class MemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


class CredentialSecretStore:
    """Store API keys in the operating system credential vault.

    EMR credentials are intentionally excluded and must remain session-only.
    """

    service_name = "WardLens"

    def _keyring(self):
        try:
            import keyring
        except ImportError as exc:
            raise SecretStoreError("The OS credential backend is unavailable.") from exc
        return keyring

    def get(self, name: str) -> str | None:
        environment_name = f"WARDLENS_{name.upper()}"
        if os.getenv(environment_name):
            return os.environ[environment_name]
        if name == "openrouter_api_key" and os.getenv("OPENROUTER_API_KEY"):
            return os.environ["OPENROUTER_API_KEY"]
        try:
            return self._keyring().get_password(self.service_name, name)
        except Exception as exc:
            raise SecretStoreError("Unable to read Windows Credential Manager.") from exc

    def set(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            raise SecretStoreError("An empty API key cannot be stored.")
        try:
            self._keyring().set_password(self.service_name, name, value)
        except Exception as exc:
            raise SecretStoreError("Unable to save to Windows Credential Manager.") from exc

    def delete(self, name: str) -> None:
        try:
            self._keyring().delete_password(self.service_name, name)
        except Exception:
            return
