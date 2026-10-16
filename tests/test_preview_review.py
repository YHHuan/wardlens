from dataclasses import replace

import pytest

from wardlens.llm.prompts import PromptFormatError
from wardlens.security.audit import HashOnlyAuditLog
from wardlens.security.deidentify import Deidentifier
from wardlens.security.outbound import PreviewBlocked, PreviewMismatch
from wardlens.services.ai import AIWorkflowService


@pytest.mark.parametrize("workflow", ["rounding", "admission", "qa", "emergency"])
def test_blocked_review_stays_local_and_requires_recheck(workflow, tmp_path):
    path = tmp_path / "audit.jsonl"
    service = AIWorkflowService(audit=HashOnlyAuditLog(path))
    text = "Unlabeled accession 87654321; creatinine 1.8 mg/dL"
    with pytest.raises(PreviewBlocked) as error:
        service.prepare(workflow, question=text, emergency_text=text)
    blocked = error.value
    assert "87654321" not in str(blocked)
    assert "87654321" in blocked.result.text
    assert not path.exists()
    with pytest.raises(PreviewBlocked):
        service.prepare_reviewed(blocked.result.text, workflow=workflow)
    draft = Deidentifier.mask_findings(blocked.result.text, blocked.result.residual_risks)
    prepared = service.prepare_reviewed(draft, workflow=workflow)
    assert "87654321" not in prepared.preview.text
    assert "creatinine 1.8 mg/dL" in prepared.preview.text
    service.record_copy(prepared)
    audit = path.read_text()
    assert "87654321" not in audit and "creatinine" not in audit
    changed = replace(
        prepared,
        preview=replace(prepared.preview, text=prepared.preview.text.replace("1.8", "2.0")),
    )
    with pytest.raises(PreviewMismatch):
        service.record_copy(changed)
    with pytest.raises(PromptFormatError):
        service.prepare_reviewed("broken markers", workflow=workflow)


def test_scan_offsets_survive_markers_and_masking_preserves_neighboring_values():
    text = "[KNOWN_HISTNO_REMOVED] code 87654321; CRP 3.1; code A123456789"
    scanner = Deidentifier()
    findings = scanner.scan(text)
    assert any(text[f.start : f.end] == "87654321" for f in findings)
    redacted = scanner.mask_findings(text, findings)
    assert "CRP 3.1" in redacted
    assert not scanner.scan(redacted)
    assert not scanner.scan("123[KNOWN_HISTNO_REMOVED]4567")


def test_fake_redaction_marker_cannot_hide_an_identifier():
    assert Deidentifier().scan("[87654321_REMOVED]")
