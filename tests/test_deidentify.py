from __future__ import annotations

from datetime import datetime

import pytest

from wardlens.models import PatientSummary
from wardlens.security.deidentify import DataLeakRisk, Deidentifier, clean_clinical_headers
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


def test_horizontal_report_profile_preserves_clinical_columns_and_real_times() -> None:
    raw = (
        "姓名\t病歷號\t年齡\t性別\t檢體\t檢驗項目\t結果\t單位\t參考值\t採檢時間\n"
        "王小明\t87654321\t38\tM\tUrine\tPorphobilinogen\t18.2\tmg/g Cr\t< 2\t2026-11-16 09:10\n"
        "王小明\t87654321\t38\tM\tSerum\tALA\t6.2\tumol/L\t< 5\t2026-11-17 09:10\n"
        "\nMethod: LC-MS/MS\nInterpretation: Correlate with symptoms.\n未知標題：保留原文"
    )
    cleaned, findings = clean_clinical_headers(raw)
    assert "王小明" not in cleaned and "87654321" not in cleaned
    assert cleaned.splitlines()[:3] == [
        "年齡\t性別\t檢體\t檢驗項目\t結果\t單位\t參考值\t採檢時間",
        "38\tM\tUrine\tPorphobilinogen\t18.2\tmg/g Cr\t< 2\t2026-11-16 09:10",
        "38\tM\tSerum\tALA\t6.2\tumol/L\t< 5\t2026-11-17 09:10",
    ]
    assert "Method: LC-MS/MS" in cleaned
    assert "未知標題：保留原文" in cleaned
    assert findings and all("王小明" not in finding.redacted_preview for finding in findings)
    assert clean_clinical_headers(cleaned) == (cleaned, ())


def test_headers_never_swallow_adjacent_clinical_fields_or_test_name() -> None:
    raw = (
        "姓名：王小明\t病歷號：87654321\tWBC 8.4\tNa 135\n"
        "Patient Name: Jane  Example; Test Name: Urine PBG; Result: 18.2; Unit: mg/g Cr\n"
        "Ordering physician: Dr Example | Age: 38 | Sex: M | Dose: 1/2 tablet\n"
        "檢體編號: SPEC-123\t檢體種類: urine\t檢驗方法: LC-MS/MS\n"
        "報告號: RPT-123  PBG 18.2  CRP 5.8\n"
        "病歷號：87654321 WBC: 8.4 Na: 135\n"
        "Specimen ID\tSP-123\tSpecimen\tUrine\tResult\t18.2"
    )
    result = Deidentifier().deidentify(raw)
    assert result.safe_to_send
    for identifier in ("王小明", "87654321", "Jane", "Example", "SPEC-123", "RPT-123", "SP-123"):
        assert identifier not in result.text
    for clinical in ("WBC 8.4", "Na 135", "Test Name: Urine PBG", "Result: 18.2", "Unit: mg/g Cr", "Age: 38", "Sex: M", "Dose: 1/2 tablet", "檢體種類: urine", "檢驗方法: LC-MS/MS", "PBG 18.2", "CRP 5.8", "WBC: 8.4", "Na: 135", "Specimen\tUrine\tResult\t18.2"):
        assert clinical in result.text


def test_vertical_headers_private_links_and_unknown_adjacent_fields() -> None:
    raw = (
        "姓名\n王小明\nDOB:\n1988-01-02\n主治醫師：黃醫師\n申請單號：REQ-987\n"
        "報告名稱：Urine porphyrin profile\n報告日期：2026-11-17 10:30\n"
        "來源 https://emr.example.invalid/record?histno=87654321&reportid=RP123\n"
        "Guideline https://example.org/guidance\n"
        "Name: Jane Unknown Ultra Rare Test: positive"
    )
    cleaned, _ = clean_clinical_headers(raw)
    for value in ("王小明", "1988-01-02", "黃醫師", "REQ-987", "emr.example.invalid"):
        assert value not in cleaned
    assert "報告名稱：Urine porphyrin profile" in cleaned
    assert "報告日期：2026-11-17 10:30" in cleaned
    assert "https://example.org/guidance" in cleaned
    # Ambiguous inline boundaries must block for review, not erase a rare assay.
    result = Deidentifier().deidentify(cleaned)
    assert "Ultra Rare Test: positive" in result.text
    assert not result.safe_to_send
    assert any(f.kind == "labeled_identifier" for f in result.residual_risks)
    reviewed = Deidentifier.mask_findings(result.text, result.residual_risks)
    assert "Jane Unknown" not in reviewed


def test_markdown_and_unknown_test_columns_survive_identifier_column_removal() -> None:
    raw = (
        "| Patient Name | Specimen ID | Test Name | Novel porphyrin assay | Unit |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| Jane Example | SP123 | PBG | 18.2 | mg/g Cr |\n"
    )
    cleaned, _ = clean_clinical_headers(raw)
    assert "Jane Example" not in cleaned and "SP123" not in cleaned
    assert cleaned.splitlines() == [
        "| Test Name | Novel porphyrin assay | Unit |",
        "| --- | --- | --- |",
        "| PBG | 18.2 | mg/g Cr |",
    ]
    assert not Deidentifier().scan(cleaned)


def test_clinical_fractions_stay_clinical_dates_are_relative_and_repeat_is_stable() -> None:
    text = (
        "Take 1/2 tablet PO; pain 5/10; 肌力：4/5; ECOG 2/4; dose 1/4 mg; q 6-8 h\n"
        "採檢日期: 11/16; report date: 2026-11-17; event 11/15\n"
        "Collected: 2026-11-17 09:10; Reported: 2026-11-17 11:30\n"
    )
    scanner = Deidentifier()
    anchor = datetime.fromisoformat("2026-11-17T12:00:00+08:00")
    first = scanner.deidentify(text, anchor_time=anchor)
    assert first.safe_to_send
    assert "Take 1/2 tablet PO; pain 5/10; 肌力：4/5; ECOG 2/4; dose 1/4 mg; q 6-8 h" in first.text
    assert "採檢日期: D-1; report date: D0; event D-2" in first.text
    assert "Collected: D0 09:10; Reported: D0 11:30" in first.text
    again = scanner.deidentify(first.text, anchor_time=anchor)
    assert again.text == first.text and again.safe_to_send
    assert not again.replacements


@pytest.mark.parametrize(("ward", "bed"), [("", "01"), ("01", ""), ("101", ""), ("101", "01"), ("01", "02"), ("ICU", "")])
def test_short_or_numeric_locations_do_not_corrupt_clinical_values(ward, bed) -> None:
    patient = PatientSummary(histno="", ward=ward, bed=bed)
    scanner = Deidentifier()
    clinical = "Cr 1.01 mg/dL\nDose: 0.01 mg\nPulse 101 bpm\nTransfer to ICU if needed."
    # An already-redacted input must also pass the residual scan unchanged.
    assert not scanner.scan(clinical, patient=patient)
    result = scanner.deidentify(
        f"病房：{ward or '未提供'}\n床號：{bed or '未提供'}\n"
        f"Ward: {ward or 'not supplied'}\nBed: {bed or 'not supplied'}\n"
        "Collection date: 2026-01-10\nEvent: 2026-01-02\nHistorical: 101-01-02\n"
        + clinical,
        patient=patient,
        anchor_time=datetime.fromisoformat("2026-01-10T12:00:00+08:00"),
    )
    assert result.safe_to_send
    assert clinical in result.text
    assert "Collection date: D0" in result.text
    assert "Event: D-8" in result.text
    assert "Historical: D-5122" in result.text
    assert "病房" not in result.text and "床號" not in result.text
    assert "Ward:" not in result.text and "Bed:" not in result.text
    assert "KNOWN_LOCATION" not in result.text
    assert not scanner.scan(result.text, patient=patient)


def test_specific_known_location_is_still_redacted_without_a_field_label() -> None:
    patient = PatientSummary(histno="", ward="D101", bed="01")
    scanner = Deidentifier()
    assert scanner.scan("Location mentioned as D101-01", patient=patient)
    result = scanner.deidentify("Location mentioned as D101-01; Cr 1.01 mg/dL", patient=patient)
    assert result.safe_to_send
    assert "D101-01" not in result.text
    assert "[KNOWN_LOCATION_REMOVED]" in result.text
    assert "Cr 1.01 mg/dL" in result.text
