from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from wardlens.config import AppSettings
from wardlens.emr.parsers import table_to_tsv
from wardlens.emr.vgh import VGHReadOnlyAdapter, _FetchedPage, _RequestBudget
from wardlens.llm.prompts import PromptBuilder
from wardlens.models import EMR_TIMEZONE, PatientBundle, PatientSummary, SourceRecord
from wardlens.services.history import TRUNCATION_MARKER, coverage_summary, months_before
from wardlens.services.summary import render_bundle_overview


def test_calendar_window_and_single_day_coverage():
    assert months_before(date(2026, 1, 31), 3) == date(2025, 10, 31)
    assert months_before(date(2024, 3, 31), 1) == date(2024, 2, 29)
    record = SourceRecord("lab_cbc", "CBC", "2026/10/16 WBC 10")
    assert "只有單日" in coverage_summary(record)


def test_report_narrative_and_all_tables_are_preserved():
    soup = BeautifulSoup(
        """<div id='RSCONTENT'>
    <table><tr><td>Header</td></tr></table>
    Findings: synthetic earlier opacity.
    <table><tr><td>Comparison</td><td>2026/07/17</td></tr></table>
    Impression: synthetic interval improvement.
    </div>""",
        "html.parser",
    )
    text = VGHReadOnlyAdapter._extract_clinical_text(soup)
    assert "Findings:" in text and "Impression:" in text
    assert "Comparison\t2026/07/17" in text


def test_table_dates_and_units_stay_aligned_with_merged_headers():
    table = BeautifulSoup(
        """
    <table><tr><th rowspan='2'>Test</th><th rowspan='2'>Unit</th>
    <th colspan='2'>2026/07/17</th><th>2026/10/16</th></tr>
    <tr><th>08:00</th><th>18:00</th><th>09:00</th></tr>
    <tr><td>Creatinine</td><td>mg/dL</td><td>1.0</td><td>1.1</td><td>1.8</td></tr></table>
    """,
        "html.parser",
    ).table
    lines = table_to_tsv(table).splitlines()
    assert lines[0].split("\t") == ["Test", "Unit", "2026/07/17", "2026/07/17", "2026/10/16"]
    assert lines[1].split("\t") == ["Test", "Unit", "08:00", "18:00", "09:00"]
    assert lines[2].split("\t") == ["Creatinine", "mg/dL", "1.0", "1.1", "1.8"]
    text = VGHReadOnlyAdapter._extract_clinical_text(BeautifulSoup(str(table), "html.parser"))
    assert "mg/dL\t1.0\t1.1\t1.8" in text


def _index(rows):
    return (
        "<table id='reslist'><tr><th>項目</th><th>檢體</th><th>狀態</th><th>其他</th><th>報告日期</th></tr>"
        + "".join(
            f"<tr><td><a href='{url}'>Synthetic test</a></td><td>blood</td><td>pending</td><td></td><td>{day}</td></tr>"
            for day, url in rows
        )
        + "</table>"
    )


def test_report_history_crosses_year_and_filters_before_fetching_detail(monkeypatch):
    adapter = VGHReadOnlyAdapter()
    bundle = PatientBundle(
        PatientSummary(histno="90000001"),
        fetched_at=datetime(2026, 1, 16, tzinfo=EMR_TIMEZONE),
        lookback_months=3,
    )
    calls = []

    def fetch(url):
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        if "tmonth" in query:
            year = query["tmonth"][0]
            rows = (
                [("2026/01/10", "?action=findRes&report=new")]
                if year == "2026"
                else [
                    ("114/11/02", "?action=findRes&report=old"),
                    ("2025/09/01", "?action=findRes&report=out-of-window"),
                    ("2025/11/02", "https://evil.example/capture"),
                    ("2025/11/02", "?action=findRes&histno=99999999"),
                    ("2025/11/02", "?action=deleteRecord"),
                ]
            )
            return _FetchedPage(_index(rows), url)
        return _FetchedPage("<div id='RSCONTENT'>Synthetic report, result 1.8 mg/dL</div>", url)

    monkeypatch.setattr(adapter, "_get", fetch)
    adapter._fetch_report_history(bundle, _RequestBudget(40))
    assert {parse_qs(urlparse(url).query)["tmonth"][0] for url in calls if "tmonth=" in url} == {
        "2025",
        "2026",
    }
    assert len(calls) == 4
    assert len(bundle.records_of("report")) == 2
    assert bundle.records_of("report")[0].observed_at.year == 2026
    assert bundle.records_of("report")[1].observed_at.year == 2025
    assert all(r.status != "final" for r in bundle.records_of("report"))
    assert any("已略過" in w for w in bundle.warnings)
    assert "2025-10-16" in render_bundle_overview(bundle)


def test_report_cap_missing_dates_and_pagination_are_explicit(monkeypatch):
    adapter = VGHReadOnlyAdapter()
    bundle = PatientBundle(
        PatientSummary(histno="90000001"),
        fetched_at=datetime(2026, 10, 16, tzinfo=EMR_TIMEZONE),
        lookback_months=3,
    )
    calls = []

    def fetch(url):
        calls.append(url)
        if "tmonth=" in url:
            rows = [(f"2026/10/{day:02}", f"?action=findRes&report={day}") for day in range(1, 16)]
            rows.append(("unknown", "?action=findRes&report=unknown"))
            return _FetchedPage(_index(rows) + "<a rel='next' href='?page=2'>Next</a>", url)
        return _FetchedPage("<pre>Synthetic report</pre>", url)

    monkeypatch.setattr(adapter, "_get", fetch)
    adapter._fetch_report_history(bundle, _RequestBudget(40))
    assert len(calls) == 13
    assert len(bundle.records_of("report")) == 12
    assert "15 個明細連結，已讀取 12 份" in "\n".join(bundle.warnings)
    assert any("下一頁" in w for w in bundle.warnings)
    assert any("日期未辨識" in w for w in bundle.warnings)
    assert not any("report=unknown" in url for url in calls)


def test_prompt_keeps_earlier_and_latest_values_and_never_hides_truncation():
    bundle = PatientBundle(PatientSummary(histno="90000001"), lookback_months=3)
    bundle.records = [SourceRecord("progress_note", "note", "X" * 200000)] * 5
    bundle.records += [
        SourceRecord(
            "lab_chemistry",
            "chemistry",
            "2026/07/17\tCreatinine 1.0 mg/dL\n"
            + "other test\n" * 3000
            + "2026/10/16\tCreatinine 1.8 mg/dL",
        )
    ]
    bundle.warnings = ["Only index available for older imaging; full report not loaded."]
    envelope = PromptBuilder().build("rounding", bundle=bundle)
    assert "Creatinine 1.0 mg/dL" in envelope.user
    assert "Creatinine 1.8 mg/dL" in envelope.user
    assert "SRC-006" in envelope.user
    assert TRUNCATION_MARKER in envelope.user
    assert "Only index available" in envelope.user
    assert "近 3 個月" in envelope.user


def test_trend_request_uses_configured_numeric_months(monkeypatch):
    adapter = VGHReadOnlyAdapter(AppSettings(lab_lookback_months=3))
    adapter._logged_in = True
    monkeypatch.setattr(adapter, "_enter_patient_context", lambda *_args: None)
    monkeypatch.setattr(adapter, "_fetch_report_history", lambda *_args: None)
    urls = []

    def record(_budget, **kwargs):
        urls.append(kwargs["url"])
        return SourceRecord(kwargs["source_type"], kwargs["title"], "Synthetic source")

    monkeypatch.setattr(adapter, "_fetch_record", record)
    bundle = adapter.fetch_patient_bundle(PatientSummary(histno="90000001", case_no="SYNTH"))
    assert bundle.lookback_months == 3
    labs = [url for url in urls if "findResd" in url]
    assert len(labs) == 2 and all("resdtmonth=3" in url for url in labs)
