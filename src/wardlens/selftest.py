from __future__ import annotations

import importlib
import io
import json
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

import requests

from wardlens.emr.demo import DemoEMRAdapter
from wardlens.emr.vgh import VGHReadOnlyAdapter, _FetchedPage
from wardlens.llm.openrouter import OpenRouterClient
from wardlens.llm.prompts import PromptBuilder, PromptEnvelope
from wardlens.models import PatientSummary
from wardlens.security.outbound import PreviewBlocked
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
    run_stream_self_test()


_STREAM_TEXT = "先確認來源，這是合成串流測試。中文🙂 café\u0085保留\u2028文字"


def _synthetic_stream_response() -> requests.Response:
    """Real Requests decoding path; raw UTF-8 split inside Chinese characters."""
    class FragmentedBytes(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2) if size >= 0 else 2)

        def release_conn(self):
            self.close()

    payload = json.dumps({"choices": [{"delta": {"content": _STREAM_TEXT}}]}, ensure_ascii=False)
    body = ("\ufeff: heartbeat\r\n\r\ndata: " + payload
            + '\r\n\r\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\r\n\r\n'
            + 'data: {"choices":[],"usage":{"completion_tokens":20}}\r\n\r\n'
            + "data: [DONE]\r\n\r\n")
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/event-stream"
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    response.raw = FragmentedBytes(body.encode("utf-8"))
    return response


def run_stream_self_test() -> None:
    from wardlens.config import DEFAULT_PROFILES

    client = OpenRouterClient()
    with (
        patch("requests.sessions.Session.request", side_effect=RuntimeError("Offline only")),
        patch.object(client, "validate_profile"),
        patch.object(client.session, "post", return_value=_synthetic_stream_response()),
    ):
        events = list(client.stream("synthetic-key", PromptEnvelope("sys", "合成測試"), DEFAULT_PROFILES["fast"]))
    if "".join(event.delta for event in events) != _STREAM_TEXT or not events[-1].done:
        raise RuntimeError("Packaged UTF-8 stream self-test failed.")
    client.session.close()


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
        "findTpr": "<table id='tprlist'><tr><th>Date</th><th>BP</th><th>HR</th></tr><tr><td>115/10/16 08:00</td><td>120/80 mmHg</td><td>80 bpm</td></tr></table>",
        "findRes": "<table id='reslist'><tr><td>Synthetic report</td><td>blood</td><td></td><td></td><td>2026/10/16</td></tr></table>",
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
            "vital_signs",
            "lab_chemistry",
            "lab_cbc",
            "report_index",
        }
        if {record.source_type for record in bundle.records} != expected:
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
            try:
                _exercise_preview_review(app, root)
            finally:
                app._on_close()


def _exercise_preview_review(app, root) -> None:
    """Exercise actual modal widgets for both workflows, using fabricated codes."""
    from wardlens.preview import PreviewConfirmationDialog, PreviewReviewDialog

    failures = []

    def callback_error(_kind, value, _traceback):
        failures.append(value)

    root.report_callback_exception = callback_error

    def auto_confirmation(*args, **kwargs):
        dialog = PreviewConfirmationDialog(*args, **kwargs)

        def confirm() -> None:
            try:
                if dialog.show_full.get() or dialog.advanced_frame.winfo_manager():
                    raise RuntimeError("Background prompt was not collapsed by default.")
                if "EDITED-CONTEXT" in dialog.clinical_text.get("1.0", "end-1c"):
                    raise RuntimeError("Internal envelope leaked into the readable preview.")
                dialog.show_full.set(True)
                dialog._toggle_full()
                if not dialog.advanced_frame.winfo_manager():
                    raise RuntimeError("Full prompt disclosure failed.")
                if dialog.full_text.get("1.0", "end-1c") != kwargs["prepared"].preview.text:
                    raise RuntimeError("Full prompt differs from the approved hash input.")
                dialog._approve()
            finally:
                if dialog.window.winfo_exists():
                    dialog.window.destroy()

        root.after(1, confirm)
        return dialog

    def auto_review(*args, **kwargs):
        dialog = PreviewReviewDialog(*args, **kwargs)

        def repair() -> None:
            try:
                dialog._mask_all()
                dialog._apply()
            finally:
                if dialog.window.winfo_exists():
                    dialog.window.destroy()

        root.after(1, repair)
        return dialog

    adapter = DemoEMRAdapter()
    adapter.login()
    app.current_patient = adapter.search_patients().patients[0]
    app.current_bundle = adapter.fetch_patient_bundle(app.current_patient)
    app._fill_workspaces(app.current_bundle)
    root.update()
    # Edits replace the auto-loaded snapshot; the two drafts are independent.
    app.ai_workspace.data_text.delete("1.0", "end")
    app.ai_workspace.data_text.insert("1.0", "Synthetic edited chart: creatinine 1.8 mg/dL")
    root.update()
    if not app.ai_workspace.dirty or app.emergency_workspace.dirty:
        raise RuntimeError("Workspace draft isolation failed.")
    with patch("tkinter.messagebox.askyesno", return_value=False):
        app._bring_in_context(app.ai_workspace)
        app._load_selected_patient()
    if "Synthetic edited chart" not in app.ai_workspace.context() or app._busy:
        raise RuntimeError("Cancelled refresh replaced an edited draft.")
    with (
        patch("requests.sessions.Session.request", side_effect=RuntimeError("Offline only")),
        patch("wardlens.app.PreviewReviewDialog", side_effect=auto_review),
        patch("wardlens.app.PreviewConfirmationDialog", side_effect=auto_confirmation),
        patch("tkinter.messagebox.askyesno", return_value=True),
        patch("tkinter.messagebox.showerror", side_effect=lambda *args, **kwargs: failures.append(args)),
    ):
        app.question_text.insert("1.0", "Synthetic accession 87654321; creatinine 1.8 mg/dL")
        app.emergency_text.insert("1.0", "Synthetic event; accession 87654321; BP unavailable")
        root.update()
        app._prepare_ai()
        app._prepare_emergency()
        root.update()
        for prepared in (app.ai_prepared, app.emergency_prepared):
            if prepared is None or "87654321" in prepared.preview.text:
                raise RuntimeError("Local preview recovery failed.")
            app.ai.record_copy(prepared)
        if "Synthetic edited chart" not in app.ai_prepared.envelope().user:
            raise RuntimeError("Edited context was not included.")
        _exercise_generation(app, root, app.ai_prepared, app.ai_result_text)
        _exercise_generation(app, root, app.emergency_prepared, app.emergency_result_text)
        # Merely moving the caret must not invalidate, but paste/edit must.
        app.question_text.event_generate("<KeyRelease-Left>")
        root.update()
        if app.ai_prepared is None:
            raise RuntimeError("Non-edit key invalidated preview.")
        app.question_text.insert("end", " changed")
        app.emergency_text.insert("end", " changed")
        root.update()
        if app.ai_prepared is not None or app.emergency_prepared is not None:
            raise RuntimeError("Edited input retained a stale preview.")

        service = AIWorkflowService(builder=PromptBuilder(overrides={"qa": "Synthetic background code 87654321"}))
        try:
            service.prepare("qa", question="Synthetic question")
        except PreviewBlocked as blocked:
            dialog = PreviewReviewDialog(
                root, blocked=blocked, patient=None,
                recheck=lambda text: service.prepare_reviewed(text, workflow="qa"),
            )
            try:
                if not dialog.show_system.get() or not dialog.system_text.tag_ranges("risk"):
                    raise RuntimeError("Background identifiers were hidden from review.")
                dialog._mask_all()
                dialog._apply()
                if dialog.result is None or "87654321" in dialog.result.preview.text:
                    raise RuntimeError("Background prompt repair failed.")
            finally:
                if dialog.window.winfo_exists():
                    dialog.window.destroy()
        else:
            raise RuntimeError("Synthetic background identifier was not blocked.")

        def cancel_confirmation(*args, **kwargs):
            dialog = PreviewConfirmationDialog(*args, **kwargs)
            root.after(1, dialog.window.destroy)
            return dialog

        # Send-first opens review; cancelling either workflow never reaches send.
        with (
            patch("wardlens.app.PreviewConfirmationDialog", side_effect=cancel_confirmation),
            patch.object(app, "_send_prepared") as send,
        ):
            app._send_ai()
            app._send_emergency()
            if send.called or app.ai_prepared is not None or app.emergency_prepared is not None:
                raise RuntimeError("Cancelled review authorized a send.")
        # Read-only during work; programmatic refresh/clear must still function.
        app.ai_workspace.set_busy(True)
        before = app.ai_workspace.context()
        app.ai_workspace.data_text.insert("end", "must not be inserted")
        if app.ai_workspace.context() != before:
            raise RuntimeError("Busy workspace remained editable.")
        app.ai_workspace.load_bundle(app.current_bundle)
        if app.ai_workspace.context() == before:
            raise RuntimeError("Disabled draft could not be refreshed programmatically.")
        app.ai_workspace.set_busy(False)
        # A new patient must not inherit either page's questions or answers.
        second = adapter.search_patients().patients[1]
        item = app.patient_tree.insert("", "end")
        app.patients[item] = second
        app.patient_tree.selection_set(item)
        app._patient_selected()
        root.update()
        for workspace in (app.ai_workspace, app.emergency_workspace):
            if workspace.context() or workspace.question_text.get("1.0", "end-1c") or workspace.result_text.get("1.0", "end-1c"):
                raise RuntimeError("Previous patient data survived a patient switch.")
    if failures:
        cause = failures[0] if isinstance(failures[0], Exception) else None
        raise RuntimeError("UI callback failed during self-test.") from cause


def _exercise_generation(app, root, prepared, output) -> None:
    """Drive the actual worker/UI stream path without a key, network or billing."""
    with (
        patch.object(app, "_cloud_allowed", return_value=True),
        patch.object(app, "_get_api_key", return_value="synthetic-key"),
        patch.object(app.ai.client, "validate_profile"),
        patch.object(app.ai.client.session, "post", return_value=_synthetic_stream_response()) as post,
    ):
        app._send_prepared(prepared, "fast", output)
        if not app._busy:
            raise RuntimeError("Synthetic UI generation did not start.")
        timed_out = []

        def timeout():
            timed_out.append(True)
            root.quit()

        def check():
            if app._busy:
                root.after(10, check)
            else:
                root.quit()

        deadline = root.after(10000, timeout)
        root.after(10, check)
        root.mainloop()
        root.after_cancel(deadline)
        if timed_out or _STREAM_TEXT not in output.get("1.0", "end-1c") or "生成完成" not in app.status_var.get():
            raise RuntimeError("Synthetic UI UTF-8 generation failed.")
        if post.call_count != 1:
            raise RuntimeError("Unexpected duplicate generation request.")


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
