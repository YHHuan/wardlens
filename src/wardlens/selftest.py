from __future__ import annotations

import importlib
import os
import platform
import sys
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from wardlens.emr.demo import DemoEMRAdapter
from wardlens.security.secrets import WindowsCredentialBackend
from wardlens.services.ai import AIWorkflowService
from wardlens.services.export import export_patient_list_docx


def run_self_test() -> None:
    """Exercise packaged resources without connecting to EMR or external AI."""

    adapter = DemoEMRAdapter()
    adapter.login()
    patients = adapter.search_patients().patients
    if len(patients) != 18:
        raise RuntimeError("Synthetic patient resource is incomplete.")
    bundle = adapter.fetch_patient_bundle(patients[0])
    prepared = AIWorkflowService().prepare("rounding", bundle=bundle)
    if "90000001" in prepared.preview.text or "測試甲" in prepared.preview.text:
        raise RuntimeError("De-identification package smoke test failed.")

    with tempfile.TemporaryDirectory(prefix="wardlens-selftest-") as directory:
        target = Path(directory) / "patient-list.docx"
        export_patient_list_docx(
            patients[:2],
            {patients[0].local_key: bundle},
            target,
            doctor_id="SELFTEST",
        )
        with zipfile.ZipFile(target) as document:
            expected = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            if not expected.issubset(document.namelist()):
                raise RuntimeError("DOCX package smoke test failed.")
            ET.fromstring(document.read("word/document.xml"))


def run_windows_credential_self_test() -> None:
    """Round-trip a synthetic temporary secret and remove it in all cases."""

    backend = WindowsCredentialBackend()
    target = f"WardLens/SelfTest/{uuid.uuid4().hex}"
    sentinel = f"wardlens-selftest-{uuid.uuid4().hex}"
    try:
        backend.set(target, sentinel)
        if backend.get(target) != sentinel:
            raise RuntimeError("Windows Credential Manager round-trip mismatch.")
    finally:
        backend.delete(target)
    if backend.get(target) is not None:
        raise RuntimeError("Windows Credential Manager self-test cleanup failed.")


def run_ui_self_test() -> None:
    """Display every Tk widget once; intended for the Windows CI runner."""

    import tkinter as tk

    from wardlens.app import WardLensApp

    with tempfile.TemporaryDirectory(prefix="wardlens-ui-selftest-") as directory:
        state_dir = Path(directory)
        with (
            patch("wardlens.config.app_data_dir", return_value=state_dir),
            patch("wardlens.app.app_data_dir", return_value=state_dir),
        ):
            root = tk.Tk()
            app = WardLensApp(root, force_demo=True)
            root.update_idletasks()
            root.update()
            if not root.winfo_viewable():
                app._on_close()
                raise RuntimeError("The WardLens window was constructed but was not visible.")
            app._on_close()


def _import_dependencies() -> None:
    for name in ("bs4", "requests"):
        importlib.import_module(name)


def _runtime_check() -> None:
    if not Path(sys.executable).is_file():
        raise RuntimeError("Python executable is missing.")


def run_diagnostics() -> None:
    """Print a bounded, privacy-safe launch report and fail if a check fails."""

    print("WardLens privacy-safe diagnostic")
    print(f"python_version: {platform.python_version()}")
    print(f"platform: {platform.system()} {platform.release()}")
    checks: list[tuple[str, Callable[[], object]]] = [
        ("runtime", _runtime_check),
        ("pure-Python dependencies", _import_dependencies),
        ("offline core and DOCX", run_self_test),
        ("Tk window visibility", run_ui_self_test),
    ]
    if os.name == "nt":
        checks.insert(3, ("Windows Credential Manager", run_windows_credential_self_test))
    else:
        print("[SKIP] Windows Credential Manager: non-Windows host")

    failures: list[str] = []
    for label, operation in checks:
        try:
            operation()
        except Exception as exc:
            failures.append(label)
            print(f"[FAIL] {label}: {type(exc).__module__}.{type(exc).__name__}")
        else:
            print(f"[PASS] {label}")
    print("Clinical content, credentials, exception messages, and absolute paths are omitted.")
    if failures:
        raise RuntimeError("One or more privacy-safe diagnostic checks failed.")
