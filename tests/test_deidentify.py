from __future__ import annotations

from datetime import datetime

import pytest

from wardlens.models import PatientSummary
from wardlens.security.deidentify import DataLeakRisk, Deidentifier
from wardlens.security.outbound import OutboundGuard, PreviewMismatch


def test_deidentify_known_and_labeled_identifiers_without_self_flagging() -> None:
    patient = PatientSummary(
        histno="90000001",
        name="測試甲",
        ward="D101",
        bed="01",
        case_no="DEMO-A01",
    )
    raw = (
        "姓名：測試甲\n病歷號: 90000001\n床號：D101-01\n"
        "email test@example.com phone 0912-345-678\n"
        "https://example.invalid/?histno=90000001\n事件日期 2026-08-25\nCr 1.01 mg/dL"
    )
    result = Deidentifier().deidentify(
        raw,
        patient=patient,
        anchor_time=datetime.fromisoformat("2026-08-27T12:00:00+08:00"),
    )

    assert result.safe_to_send
    assert "測試甲" not in result.text
    assert "90000001" not in result.text
    assert "0912-345-678" not in result.text
    assert "test@example.com" not in result.text
    assert "2026-08-25" not in result.text
    assert "D-2" in result.text
    assert "Cr 1.01 mg/dL" in result.text


def test_unknown_long_identifier_fails_closed() -> None:
    result = Deidentifier().deidentify("unlabeled number 87654321")
    assert not result.safe_to_send
    assert {finding.kind for finding in result.residual_risks} == {"possible_long_identifier"}


def test_outbound_hash_must_match_preview() -> None:
    guard = OutboundGuard()
    preview = guard.prepare("safe clinical text")
    assert guard.verify(preview, preview.text) == preview.sha256
    with pytest.raises(PreviewMismatch):
        guard.verify(preview, preview.text + " changed")


def test_assert_safe_blocks_identifier() -> None:
    with pytest.raises(DataLeakRisk):
        Deidentifier().assert_safe("MR-looking number 12345678")


def test_arc_english_labels_and_chinese_dates_are_removed() -> None:
    raw = (
        "Name: Jane Example\nAddress: Taipei\nDOB: 1980-01-02\n"
        "ARC A812345678 legacy AB12345678\n"
        "民國113年08月25日；2026年8月25日；8/26"
    )
    result = Deidentifier().deidentify(
        raw,
        anchor_time=datetime.fromisoformat("2026-08-27T12:00:00+08:00"),
    )
    assert result.safe_to_send
    assert "Jane Example" not in result.text
    assert "Taipei" not in result.text
    assert "A812345678" not in result.text
    assert "AB12345678" not in result.text
    assert "年" not in result.text
    assert "8/26" not in result.text


def test_long_clinical_words_are_not_identifiers() -> None:
    result = Deidentifier().deidentify("hypertension community acquired pneumonia")
    assert result.safe_to_send
