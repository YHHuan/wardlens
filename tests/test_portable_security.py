from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from wardlens.models import PatientSummary
from wardlens.security.secrets import CredentialSecretStore, SecretStoreError
from wardlens.services.export import export_patient_list_docx
from wardlens.startup import operation_error_detail, report_unhandled_exception

ROOT = Path(__file__).resolve().parents[1]


class FakeCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, target: str) -> str | None:
        return self.values.get(target)

    def set(self, target: str, value: str) -> None:
        self.values[target] = value

    def delete(self, target: str) -> None:
        self.values.pop(target, None)


def test_credential_store_uses_scoped_target_and_strips_value(monkeypatch) -> None:
    monkeypatch.delenv("WARDLENS_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    backend = FakeCredentialBackend()
    store = CredentialSecretStore(backend)

    store.set("openrouter_api_key", "  sk-or-test  ")
    assert backend.values == {"WardLens/openrouter_api_key": "sk-or-test"}
    assert store.get("openrouter_api_key") == "sk-or-test"
    store.delete("openrouter_api_key")
    assert store.get("openrouter_api_key") is None


def test_credential_store_environment_takes_precedence(monkeypatch) -> None:
    backend = FakeCredentialBackend()
    backend.values["WardLens/openrouter_api_key"] = "stored"
    monkeypatch.setenv("WARDLENS_OPENROUTER_API_KEY", "environment")
    assert CredentialSecretStore(backend).get("openrouter_api_key") == "environment"


def test_credential_store_rejects_blank_value() -> None:
    with pytest.raises(SecretStoreError, match="empty"):
        CredentialSecretStore(FakeCredentialBackend()).set("openrouter_api_key", "   ")


def test_docx_export_is_valid_pure_openxml(tmp_path) -> None:
    patient = PatientSummary(
        histno="12345678",
        name="測試 & <病人>\x01",
        ward="W1",
        bed="01",
        age="55",
        sex="F",
    )
    path = export_patient_list_docx(
        [patient],
        {},
        tmp_path / "patient-list.docx",
        doctor_id="D&1",
    )

    with zipfile.ZipFile(path) as archive:
        required = {
            "[Content_Types].xml",
            "_rels/.rels",
            "docProps/core.xml",
            "word/document.xml",
            "word/styles.xml",
            "word/_rels/document.xml.rels",
        }
        assert required.issubset(archive.namelist())
        for name in required:
            ET.fromstring(archive.read(name))
        document = ET.fromstring(archive.read("word/document.xml"))
        text = "".join(document.itertext())
        assert "測試 & <病人>" in text
        assert "\x01" not in text
        assert "尚未載入；不可解讀為無待辦。" in text


def test_startup_diagnostic_omits_exception_message_and_absolute_paths(tmp_path) -> None:
    secret = "PATIENT-SECRET-998877"
    try:
        raise ValueError(secret)
    except ValueError as exc:
        target = tmp_path / "diagnostic.txt"
        written = report_unhandled_exception(
            type(exc),
            exc,
            exc.__traceback__,
            phase="unit-test",
            target=target,
            show_dialog=False,
        )

    assert written == target
    content = target.read_text(encoding="utf-8")
    assert secret not in content
    assert str(Path.cwd()) not in content
    assert "builtins.ValueError" in content
    assert "message omitted" in content


def test_portable_zip_uses_standard_forward_slash_members(tmp_path) -> None:
    package = tmp_path / "WardLens"
    (package / "app").mkdir(parents=True)
    (package / "app" / "main.py").write_text("pass\n", encoding="utf-8")
    output = tmp_path / "WardLens-Portable.zip"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/create_portable_zip.py"), str(package), str(output)],
        check=True,
    )
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["WardLens/app/main.py"]


def test_caught_operation_error_creates_private_technical_diagnostic(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wardlens.startup.diagnostic_path", lambda: tmp_path / "startup-error.log")
    secret = "SYNTHETIC-PHI-and-key-998877"
    try:
        raise OSError(22, secret)
    except OSError as exc:
        detail = operation_error_detail(exc, phase="load-patient")
    content = (tmp_path / "operation-error.log").read_text(encoding="utf-8")
    assert "load-patient" in detail
    assert "OSError" in detail
    assert "operation-error.log" in detail
    assert "phase: load-patient" in content
    assert "errno=22" in content
    assert "wardlens_version:" in content
    assert "test_caught_operation_error" in content
    assert secret not in content + detail
    assert str(tmp_path) not in content
    assert str(ROOT) not in content
    assert "raise OSError" not in content


def test_operation_error_phase_cannot_log_arbitrary_content(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wardlens.startup.diagnostic_path", lambda: tmp_path / "startup-error.log")
    detail = operation_error_detail(RuntimeError("secret-value"), phase="secret-phase")
    content = (tmp_path / "operation-error.log").read_text(encoding="utf-8")
    assert "phase: operation" in content
    assert "secret" not in content + detail


def test_operation_error_still_shows_code_when_log_cannot_be_written(monkeypatch) -> None:
    monkeypatch.setattr("wardlens.startup._write_diagnostic", lambda *args, **kwargs: None)
    detail = operation_error_detail(RuntimeError("hidden-value"), phase="load-patient")
    assert "load-patient／RuntimeError" in detail
    assert "無法寫入" in detail
    assert "hidden-value" not in detail
