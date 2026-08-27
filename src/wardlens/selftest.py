from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import keyring
from docx import Document

from wardlens.emr.demo import DemoEMRAdapter
from wardlens.services.ai import AIWorkflowService


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
    Document()
    # Read-only lookup validates that the packaged Windows keyring backend and
    # its entry-point metadata can load. It does not create a credential.
    if os.name == "nt":
        keyring.get_password("WardLens-Packaged-SelfTest", "nonexistent")


def run_ui_self_test() -> None:
    """Construct every Tk widget once; intended for the Windows CI runner."""

    import tkinter as tk

    from wardlens.app import WardLensApp

    with tempfile.TemporaryDirectory(prefix="wardlens-ui-selftest-") as directory:
        state_dir = Path(directory)
        with (
            patch("wardlens.config.app_data_dir", return_value=state_dir),
            patch("wardlens.app.app_data_dir", return_value=state_dir),
        ):
            root = tk.Tk()
            root.withdraw()
            app = WardLensApp(root, force_demo=True)
            root.update_idletasks()
            app._on_close()
