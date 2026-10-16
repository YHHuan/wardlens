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
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from wardlens.emr.demo import DemoEMRAdapter
from wardlens.emr.vgh import VGHReadOnlyAdapter, _FetchedPage
from wardlens.models import PatientSummary
from wardlens.security.secrets import WindowsCredentialBackend
from wardlens.services.ai import AIWorkflowService
from wardlens.services.export import export_patient_list_docx
from wardlens.services.summary import render_bundle_overview


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

    run_vgh_adapter_self_test()


def run_vgh_adapter_self_test() -> None:
    """Exercise the real parser with fabricated pages and no network requests."""

    pages = {
        "findPatient": "<table id='patlist'><tr><td>90000001</td></tr></table>",
        "findEmr": "<div>Synthetic patient context</div>",
        "findPba": "<table><tr><td>Birth date</td><td>039/01/02</td></tr></table>",
        "findAdm": "<a title='admnote' href='?action=admissionDetail'>Note</a>",
        "admissionDetail": "<pre>115/10/16 Synthetic admission note</pre>",
        "findPrg": "<a href='?action=progressDetail&caseno=SYNTH001'>Progress</a>",
        "progressDetail": "<table><tr><td>058/12/31 Synthetic old source</td></tr></table>",
        "findUd": "<a href='?action=orderDetail&caseno=SYNTH001'>Orders</a>",
        "orderDetail": "<table id='udorder'><tr><td>Synthetic order</td></tr></table>",
        "findResd": "<table id='resdtable'><tr><td>115/10/16 Synthetic lab</td></tr></table>",
        "findRes": "<table id='reslist'><tr><td>2026/10/16 Synthetic report</td></tr></table>",
    }

    def offline_page(url: str) -> _FetchedPage:
        action = parse_qs(urlparse(url).query).get("action", [""])[0]
        if action not in pages:
            raise RuntimeError("The offline adapter self-test requested an unknown fixture.")
        return _FetchedPage(pages[action], url)

    adapter = VGHReadOnlyAdapter()
    adapter._logged_in = True
    try:
        with (
            patch.object(adapter, "_get", side_effect=offline_page),
            patch.object(adapter.session, "request", side_effect=RuntimeError("Offline only")),
        ):
            bundle = adapter.fetch_patient_bundle(
                PatientSummary(histno="90000001", case_no="SYNTH001")
            )
        expected = {
            "profile",
            "admission_note",
            "progress_note",
            "medication",
            "lab_chemistry",
            "lab_cbc",
            "report_index",
        }
        if {record.source_type for record in bundle.records} != expected or bundle.warnings:
            raise RuntimeError("Synthetic VGH bundle was not loaded completely.")
        if bundle.records_of("profile")[0].observed_at is not None:
            raise RuntimeError("A synthetic birth date was incorrectly used as source time.")
        if "1969-12-31" not in render_bundle_overview(bundle):
            raise RuntimeError("Synthetic historical source was not rendered correctly.")
    finally:
        adapter.logout()


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
