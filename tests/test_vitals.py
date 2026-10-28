from urllib.parse import parse_qs, urlparse

import pytest

from wardlens.emr.base import InterfaceChangedError
from wardlens.emr.vgh import VGHReadOnlyAdapter, _FetchedPage, _RequestBudget
from wardlens.models import PatientSummary


def test_vital_table_keeps_timestamps_columns_and_values(monkeypatch):
    html = """<table id="tprlist"><tr><th>日期／時間</th><th>T °C</th><th>BP mmHg</th><th>HR bpm</th><th>RR /min</th><th>SpO2 %</th></tr>
    <tr><td>115/10/27 08:00</td><td>36.5</td><td>120/80</td><td>80</td><td>18</td><td>98</td></tr>
    <tr><td>115/10/28 08:00</td><td>37.0</td><td>118/78</td><td>84</td><td>19</td><td>97</td></tr></table>"""
    adapter = VGHReadOnlyAdapter()
    monkeypatch.setattr(adapter, "_get", lambda url: _FetchedPage(html, url))
    record = adapter._fetch_record(
        _RequestBudget(1), source_type="vital_signs", title="TPR",
        url=adapter._url("findTpr", caseno="SYNTH001"), preferred_link_title=None,
        expected_id="tprlist", patient=PatientSummary(histno="90000001", case_no="SYNTH001"),
    )
    assert record.observed_at is None  # A multi-day table has no single observation time.
    assert "08:00" in record.content and "120/80" in record.content and "118/78" in record.content
    assert "SpO2 %" in record.content and "\t" in record.content
    assert not any("未辨識" in warning for warning in adapter._vital_warnings(record.content))


def test_vital_missing_columns_and_dates_are_explicit():
    warnings = VGHReadOnlyAdapter._vital_warnings("BP\tHR\n120/80\t80")
    assert any("SpO₂" in warning and "RR" in warning for warning in warnings)
    assert any("未辨識到日期" in warning for warning in warnings)


def test_vital_route_is_current_case_only_and_partial_failure_is_visible(monkeypatch):
    adapter = VGHReadOnlyAdapter()
    adapter._logged_in = True
    monkeypatch.setattr(adapter, "_enter_patient_context", lambda *_: None)
    monkeypatch.setattr(adapter, "_fetch_report_history", lambda *_: None)
    seen = []

    def record(_budget, **kwargs):
        from wardlens.models import SourceRecord

        seen.append(kwargs)
        if kwargs["source_type"] == "vital_signs":
            raise InterfaceChangedError("Synthetic missing #tprlist")
        return SourceRecord(source_type=kwargs["source_type"], title="Synthetic", content="synthetic source")

    monkeypatch.setattr(adapter, "_fetch_record", record)
    bundle = adapter.fetch_patient_bundle(PatientSummary(histno="90000001", case_no="SYNTH001"))
    vitals = next(spec for spec in seen if spec["source_type"] == "vital_signs")
    assert parse_qs(urlparse(vitals["url"]).query) == {"action": ["findTpr"], "caseno": ["SYNTH001"]}
    assert vitals["expected_id"] == "tprlist"
    assert bundle.records
    assert any("生命徵象 TPR 未載入" in warning for warning in bundle.warnings)


def test_wrong_vital_page_is_rejected(monkeypatch):
    adapter = VGHReadOnlyAdapter()
    monkeypatch.setattr(adapter, "_get", lambda url: _FetchedPage("<html>No data</html>", url))
    with pytest.raises(InterfaceChangedError, match="tprlist"):
        adapter._fetch_record(
            _RequestBudget(1), source_type="vital_signs", title="TPR", url="https://web9.vghtpe.gov.tw/",
            preferred_link_title=None, expected_id="tprlist", patient=PatientSummary(histno="90000001"),
        )
