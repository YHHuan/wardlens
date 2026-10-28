import hashlib
import io
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from wardlens import browser_probe as probe


@pytest.mark.parametrize("browser,driver,match", [
    ("156.0.4314.50", "156.0.4314.75", True),
    ("156.0.4313.50", "156.0.4314.50", False),
    ("155.0.4314.50", "156.0.4314.50", False),
    ("156.0.4314.50 SECRET", "156.0.4314.50", False),
])
def test_driver_version_requires_first_three_parts(browser, driver, match):
    assert probe.compatible_versions(browser, driver) == match


def test_driver_lock_is_pinned_to_official_microsoft_download():
    manifest = probe.driver_manifest()
    assert manifest["url"] == f"https://msedgedriver.microsoft.com/{manifest['version']}/edgedriver_win64.zip"
    assert len(manifest["zip_sha256"]) == len(manifest["exe_sha256"]) == 64


def windows_environment(monkeypatch, version="156.0.4314.50"):
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(probe.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(probe, "_find_edge", lambda: (Path("synthetic-edge.exe"), version))


def test_version_mismatch_generates_actionable_safe_report(monkeypatch, tmp_path):
    windows_environment(monkeypatch, "120.0.1000.1")
    fixture = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(probe, "_run_fixture", fixture)
    report = probe.run_browser_probe(package_root=tmp_path)
    assert report["browser_version"] == "120.0.1000.1"
    assert report["checks"][-1]["code"] == "DRIVER_VERSION_MISMATCH"
    assert not fixture.called
    assert str(tmp_path) not in probe.report_text(report)


def test_missing_and_tampered_driver_never_executes(monkeypatch, tmp_path):
    windows_environment(monkeypatch)
    fixture = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(probe, "_run_fixture", fixture)
    assert probe.run_browser_probe(package_root=tmp_path)["checks"][-1]["code"] == "DRIVER_NOT_PREPARED"
    (tmp_path / "drivers").mkdir()
    (tmp_path / "drivers/msedgedriver.exe").write_bytes(b"tampered")
    assert probe.run_browser_probe(package_root=tmp_path)["checks"][-1]["code"] == "DRIVER_HASH_MISMATCH"
    assert not fixture.called


def test_arbitrary_exception_cannot_leak_into_shareable_log(monkeypatch, tmp_path):
    windows_environment(monkeypatch)
    manifest = probe.driver_manifest()
    manifest["exe_sha256"] = hashlib.sha256(b"synthetic").hexdigest()
    monkeypatch.setattr(probe, "driver_manifest", lambda: manifest)
    (tmp_path / "drivers").mkdir()
    (tmp_path / "drivers/msedgedriver.exe").write_bytes(b"synthetic")
    secret = "synthetic-patient-and-key-and-local-path"
    monkeypatch.setattr(probe, "_run_fixture", Mock(side_effect=RuntimeError(secret)))
    report = probe.run_browser_probe(package_root=tmp_path)
    assert report["checks"][-1]["code"] == "UNEXPECTED_PROBE_ERROR"
    assert secret not in probe.report_text(report)
    assert not report["clinical_data_accessed"]


def test_driver_errors_are_not_copied_from_response(monkeypatch):
    client = probe._WebDriver(55555)
    assert client.base == "http://127.0.0.1:55555"
    assert not client.http.trust_env
    response = Mock(ok=False)
    response.json.return_value = {"value": {"error": "session not created", "message": "SECRET-profile-path"}}
    monkeypatch.setattr(client.http, "request", Mock(return_value=response))
    with pytest.raises(probe.ProbeFailure, match="WEBDRIVER_COMMAND_REJECTED") as error:
        client.call("GET", "/status")
    assert "SECRET" not in str(error.value)
    client.http.close()


def test_non_windows_probe_does_not_attempt_browser_launch(monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "linux")
    report = probe.run_browser_probe()
    assert report["checks"][-1]["code"] == "WINDOWS_64BIT_REQUIRED"


def synthetic_download(monkeypatch, *, tamper_zip=False, tamper_exe=False):
    binary = b"synthetic-driver-only-not-executable"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("msedgedriver.exe", binary)
        archive.writestr("Driver_Notes/EULA", "synthetic-license")
        archive.writestr("Driver_Notes/credits.html", "synthetic-credits")
    body = stream.getvalue()
    manifest = probe.driver_manifest()
    manifest["zip_sha256"] = hashlib.sha256(body if not tamper_zip else b"wrong").hexdigest()
    manifest["exe_sha256"] = hashlib.sha256(binary if not tamper_exe else b"wrong").hexdigest()
    monkeypatch.setattr(probe, "driver_manifest", lambda: manifest)
    response = Mock(status_code=200)
    response.iter_content.return_value = [body[:9], body[9:]]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    download = Mock(return_value=response)
    monkeypatch.setattr(probe.requests, "get", download)
    return binary, download


def test_explicit_preparation_verifies_bytes_and_keeps_license(monkeypatch, tmp_path):
    binary, download = synthetic_download(monkeypatch)
    assert probe.prepare_driver(package_root=tmp_path) == probe.driver_manifest()["version"]
    assert (tmp_path / "drivers/msedgedriver.exe").read_bytes() == binary
    assert (tmp_path / "drivers/Driver_Notes/EULA").read_text() == "synthetic-license"
    assert (tmp_path / "drivers/Driver_Notes/credits.html").is_file()
    assert not list((tmp_path / "drivers").glob("prepare-*"))
    download.assert_called_once_with(probe.driver_manifest()["url"], stream=True, timeout=(10, 60), allow_redirects=False)


@pytest.mark.parametrize("tamper_zip,tamper_exe,code", [
    (True, False, "DRIVER_DOWNLOAD_HASH_MISMATCH"),
    (False, True, "DRIVER_HASH_MISMATCH"),
])
def test_bad_download_preserves_existing_driver(monkeypatch, tmp_path, tamper_zip, tamper_exe, code):
    synthetic_download(monkeypatch, tamper_zip=tamper_zip, tamper_exe=tamper_exe)
    (tmp_path / "drivers").mkdir()
    target = tmp_path / "drivers/msedgedriver.exe"
    target.write_bytes(b"existing")
    with pytest.raises(probe.ProbeFailure, match=code):
        probe.prepare_driver(package_root=tmp_path)
    assert target.read_bytes() == b"existing"
    assert not list((tmp_path / "drivers").glob("prepare-*"))


def test_probe_never_downloads_missing_driver(monkeypatch, tmp_path):
    windows_environment(monkeypatch)
    download = Mock(side_effect=AssertionError("probe must not download"))
    monkeypatch.setattr(probe.requests, "get", download)
    report = probe.run_browser_probe(package_root=tmp_path)
    assert report["checks"][-1]["code"] == "DRIVER_NOT_PREPARED"
    assert not download.called
