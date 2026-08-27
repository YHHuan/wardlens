from __future__ import annotations

import os

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
