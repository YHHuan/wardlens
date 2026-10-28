"""Selenium prerequisite probe via the same W3C WebDriver browser interface.

Only fresh-profile, synthetic data pages are used. This is not an EMR adapter;
it reads no cookies, hospital pages or credentials. Driver preparation is a
separate, explicit Microsoft download, never part of running a probe.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote

import requests

from wardlens import __version__

VERSION = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,5}\.\d{1,5}$")


class ProbeFailure(RuntimeError):
    """Contains only one of our fixed, nonclinical error codes."""


def driver_manifest() -> dict:
    return json.loads(files("wardlens").joinpath("resources/browser/edge-driver.json").read_text(encoding="utf-8"))


def compatible_versions(browser: str, driver: str) -> bool:
    return bool(VERSION.fullmatch(browser) and VERSION.fullmatch(driver) and browser.split(".")[:3] == driver.split(".")[:3])


def prepare_driver(*, package_root: Path | None = None) -> str:
    """Explicit personal-use download; never distribute its bytes in our release."""
    manifest = driver_manifest()
    target = (package_root or Path(__file__).resolve().parents[2]) / "drivers"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=target) as directory:
        archive = Path(directory) / "driver.zip"
        digest = hashlib.sha256()
        size = 0
        try:
            with requests.get(manifest["url"], stream=True, timeout=(10, 60), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ProbeFailure("DRIVER_DOWNLOAD_FAILED")
                with archive.open("wb") as stream:
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 64_000_000:
                            raise ProbeFailure("DRIVER_DOWNLOAD_TOO_LARGE")
                        digest.update(chunk)
                        stream.write(chunk)
        except requests.RequestException as exc:
            raise ProbeFailure("DRIVER_DOWNLOAD_FAILED") from exc
        if digest.hexdigest() != manifest["zip_sha256"]:
            raise ProbeFailure("DRIVER_DOWNLOAD_HASH_MISMATCH")
        with zipfile.ZipFile(archive) as bundle:
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in bundle.namelist()):
                raise ProbeFailure("DRIVER_ARCHIVE_INVALID")
            binary = bundle.read("msedgedriver.exe")
            if hashlib.sha256(binary).hexdigest() != manifest["exe_sha256"]:
                raise ProbeFailure("DRIVER_HASH_MISMATCH")
            bundle.extractall(Path(directory) / "extracted")
        extracted = Path(directory) / "extracted"
        shutil.copytree(extracted / "Driver_Notes", target / "Driver_Notes", dirs_exist_ok=True)
        shutil.copy2(extracted / "msedgedriver.exe", target / "msedgedriver.exe")
    return manifest["version"]


def _windows_file_version(path: Path) -> str:
    import ctypes as c
    from ctypes import wintypes as w

    library = c.WinDLL("version", use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, c.POINTER(w.DWORD)]
    library.GetFileVersionInfoSizeW.restype = w.DWORD
    library.GetFileVersionInfoW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p]
    library.VerQueryValueW.argtypes = [c.c_void_p, w.LPCWSTR, c.POINTER(c.c_void_p), c.POINTER(w.UINT)]
    size = library.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return "unknown"
    buffer = c.create_string_buffer(size)
    if not library.GetFileVersionInfoW(str(path), 0, size, buffer):
        return "unknown"
    pointer, length = c.c_void_p(), w.UINT()
    if not library.VerQueryValueW(buffer, "\\", c.byref(pointer), c.byref(length)) or length.value < 52:
        return "unknown"
    info = c.cast(pointer, c.POINTER(w.DWORD * 13)).contents
    return f"{info[2] >> 16}.{info[2] & 65535}.{info[3] >> 16}.{info[3] & 65535}"


def _find_edge() -> tuple[Path | None, str]:
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / "Microsoft/Edge/Application/msedge.exe"
            if candidate.is_file():
                return candidate, _windows_file_version(candidate)
    return None, "not_found"


def _synthetic_page() -> str:
    rows = "".join(f"<tr><td>合成案例 {i}</td><td>{i}</td></tr>" for i in range(1, 19))
    return ("<!doctype html><meta charset='utf-8'><title>WardLens 本機合成測試</title>"
            "<h1 id='title'>中文讀取測試</h1><p>本頁沒有真實病人、帳號或院內資料。</p>"
            f"<table id='cases'>{rows}</table>"
            "<button id='run' onclick=\"setTimeout(()=>document.getElementById('result').textContent='動態更新成功',100)\">測試動態更新</button>"
            "<p id='result'>尚未更新</p>")


class _WebDriver:
    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.http = requests.Session()
        self.http.trust_env = False  # Never send loopback traffic through a hospital proxy.

    def call(self, method: str, path: str, payload=None):
        try:
            response = self.http.request(method, self.base + path, json=payload, timeout=(3, 15))
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProbeFailure("WEBDRIVER_CONNECTION_FAILED") from exc
        if not isinstance(body, dict) or not response.ok or (
            isinstance(body.get("value"), dict) and body["value"].get("error")
        ):
            raise ProbeFailure("WEBDRIVER_COMMAND_REJECTED")
        return body.get("value")


def _run_fixture(driver: Path, browser: Path, report: dict) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    client = _WebDriver(port)
    process = None
    session = ""
    with tempfile.TemporaryDirectory(prefix="WardLensBrowserTest-", ignore_cleanup_errors=True) as profile:
        try:
            try:
                process = subprocess.Popen(
                    [str(driver), f"--port={port}", "--allowed-ips=127.0.0.1", "--log-level=OFF"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                raise ProbeFailure("DRIVER_START_FAILED") from exc
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise ProbeFailure("DRIVER_EXITED")
                try:
                    status = client.call("GET", "/status")
                    if isinstance(status, dict) and status.get("ready"):
                        break
                except ProbeFailure:
                    pass
                if time.monotonic() >= deadline:
                    raise ProbeFailure("DRIVER_NOT_READY")
                time.sleep(0.2)
            report["checks"].append({"test": "driver_start", "result": "PASS"})
            value = client.call("POST", "/session", {"capabilities": {"alwaysMatch": {
                "browserName": "MicrosoftEdge",
                "ms:edgeOptions": {"binary": str(browser), "args": [
                    f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                    "--disable-sync", "--disable-background-networking",
                ]},
            }}})
            if not isinstance(value, dict) or not isinstance(value.get("sessionId"), str):
                raise ProbeFailure("INVALID_SESSION")
            session = "/session/" + quote(value["sessionId"], safe="")
            client.call("POST", session + "/timeouts", {"pageLoad": 15000, "script": 10000, "implicit": 0})
            client.call("POST", session + "/url", {"url": "data:text/html;charset=utf-8," + quote(_synthetic_page())})
            value = client.call("POST", session + "/execute/sync", {"script": "return document.querySelector('#title').textContent", "args": []})
            if value != "中文讀取測試":
                raise ProbeFailure("UNICODE_DOM_MISMATCH")
            report["checks"].append({"test": "chinese_dom", "result": "PASS"})
            value = client.call("POST", session + "/execute/sync", {"script": "return document.querySelectorAll('#cases tr').length", "args": []})
            if value != 18:
                raise ProbeFailure("TABLE_COUNT_MISMATCH")
            report["checks"].append({"test": "table_18_rows", "result": "PASS"})
            element = client.call("POST", session + "/element", {"using": "css selector", "value": "#run"})
            if not isinstance(element, dict) or not isinstance(element.get("element-6066-11e4-a52e-4f735466cecf"), str):
                raise ProbeFailure("ELEMENT_NOT_FOUND")
            element_id = quote(element["element-6066-11e4-a52e-4f735466cecf"], safe="")
            client.call("POST", session + f"/element/{element_id}/click", {})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                value = client.call("POST", session + "/execute/sync", {"script": "return document.querySelector('#result').textContent", "args": []})
                if value == "動態更新成功":
                    report["checks"].append({"test": "click_and_dynamic_update", "result": "PASS"})
                    return
                time.sleep(0.1)
            raise ProbeFailure("DYNAMIC_UPDATE_TIMEOUT")
        finally:
            if session:
                try:
                    client.call("DELETE", session)
                except ProbeFailure:
                    pass
            client.http.close()
            if process is not None and process.poll() is None:
                # Terminate only this test's driver process tree, never all browsers.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                process.wait(timeout=5)


def run_browser_probe(*, package_root: Path | None = None) -> dict:
    manifest = driver_manifest()
    report = {
        "format": "WardLens-Browser-Diagnostics-v1", "wardlens_version": __version__,
        "interface": "W3C WebDriver (Selenium prerequisite; not an EMR integration)",
        "platform": "Windows" if sys.platform == "win32" else "unsupported",
        "architecture": {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(), "other"),
        "browser": "Microsoft Edge", "browser_version": "unknown", "driver_version": manifest["version"],
        "clinical_data_accessed": False, "existing_browser_profile_used": False,
        "checks": [], "result": "FAIL",
    }
    try:
        if sys.platform != "win32" or report["architecture"] == "other" or sys.maxsize <= 2**32:
            raise ProbeFailure("WINDOWS_64BIT_REQUIRED")
        browser, version = _find_edge()
        report["browser_version"] = version if VERSION.fullmatch(version) else "unknown"
        if browser is None:
            raise ProbeFailure("EDGE_NOT_FOUND")
        if not compatible_versions(version, manifest["version"]):
            raise ProbeFailure("DRIVER_VERSION_MISMATCH")
        report["checks"].append({"test": "browser_driver_version", "result": "PASS"})
        root = package_root or Path(__file__).resolve().parents[2]
        driver = root / "drivers/msedgedriver.exe"
        if not driver.is_file():
            raise ProbeFailure("DRIVER_NOT_PREPARED")
        if hashlib.sha256(driver.read_bytes()).hexdigest() != manifest["exe_sha256"]:
            raise ProbeFailure("DRIVER_HASH_MISMATCH")
        report["checks"].append({"test": "driver_hash", "result": "PASS"})
        _run_fixture(driver, browser, report)
        report["result"] = "PASS"
    except ProbeFailure as exc:
        report["checks"].append({"test": "probe", "result": "FAIL", "code": str(exc)})
    except Exception:
        # No raw exception strings/tracebacks/paths can enter the shareable report.
        report["checks"].append({"test": "probe", "result": "FAIL", "code": "UNEXPECTED_PROBE_ERROR"})
    return report


def report_text(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
