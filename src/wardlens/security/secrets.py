from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Protocol


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class CredentialBackend(Protocol):
    def get(self, target: str) -> str | None: ...

    def set(self, target: str, value: str) -> None: ...

    def delete(self, target: str) -> None: ...


class MemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialBackend:
    """Small WinCred wrapper with no third-party native dependencies."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        if os.name != "nt":
            raise SecretStoreError("Windows Credential Manager is available only on Windows.")
        try:
            advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise SecretStoreError("Windows Credential Manager is unavailable.") from exc

        self._cred_read = advapi32.CredReadW
        self._cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CredentialW)),
        ]
        self._cred_read.restype = wintypes.BOOL

        self._cred_write = advapi32.CredWriteW
        self._cred_write.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
        self._cred_write.restype = wintypes.BOOL

        self._cred_delete = advapi32.CredDeleteW
        self._cred_delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._cred_delete.restype = wintypes.BOOL

        self._cred_free = advapi32.CredFree
        self._cred_free.argtypes = [wintypes.LPVOID]
        self._cred_free.restype = None

    @staticmethod
    def _raise_last_error(action: str) -> None:
        error = ctypes.get_last_error()
        raise SecretStoreError(f"Windows Credential Manager could not {action} (error {error}).")

    def get(self, target: str) -> str | None:
        pointer = ctypes.POINTER(_CredentialW)()
        if not self._cred_read(target, self._CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
                return None
            self._raise_last_error("read the credential")
        try:
            credential = pointer.contents
            if credential.CredentialBlobSize == 0:
                return ""
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-16-le")
        except (UnicodeDecodeError, ValueError) as exc:
            raise SecretStoreError("Windows Credential Manager returned invalid data.") from exc
        finally:
            if pointer:
                self._cred_free(pointer)

    def set(self, target: str, value: str) -> None:
        raw = value.encode("utf-16-le")
        if len(raw) > 5120:
            raise SecretStoreError("The credential is too long for Windows Credential Manager.")
        blob = ctypes.create_string_buffer(raw)
        credential = _CredentialW()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(wintypes.BYTE))
        credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "WardLens"
        if not self._cred_write(ctypes.byref(credential), 0):
            self._raise_last_error("save the credential")

    def delete(self, target: str) -> None:
        if self._cred_delete(target, self._CRED_TYPE_GENERIC, 0):
            return
        if ctypes.get_last_error() != self._ERROR_NOT_FOUND:
            self._raise_last_error("delete the credential")


class CredentialSecretStore:
    """Store API keys in Windows Credential Manager.

    EMR credentials are intentionally excluded and must remain session-only.
    """

    service_name = "WardLens"

    def __init__(self, backend: CredentialBackend | None = None) -> None:
        self._backend = backend

    def _credential_backend(self) -> CredentialBackend:
        if self._backend is None:
            self._backend = WindowsCredentialBackend()
        return self._backend

    def _target(self, name: str) -> str:
        return f"{self.service_name}/{name}"

    def get(self, name: str) -> str | None:
        environment_name = f"WARDLENS_{name.upper()}"
        if os.getenv(environment_name):
            return os.environ[environment_name]
        if name == "openrouter_api_key" and os.getenv("OPENROUTER_API_KEY"):
            return os.environ["OPENROUTER_API_KEY"]
        try:
            return self._credential_backend().get(self._target(name))
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Unable to read Windows Credential Manager.") from exc

    def set(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            raise SecretStoreError("An empty API key cannot be stored.")
        try:
            self._credential_backend().set(self._target(name), value)
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Unable to save to Windows Credential Manager.") from exc

    def delete(self, name: str) -> None:
        try:
            self._credential_backend().delete(self._target(name))
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Unable to delete from Windows Credential Manager.") from exc


def credential_backend_status() -> str:
    WindowsCredentialBackend()
    return "Windows Credential Manager API loaded"
