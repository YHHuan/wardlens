from datetime import datetime
from threading import Event
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from bs4 import BeautifulSoup

from wardlens.config import AppSettings
from wardlens.emr.base import EMRCancelled, InterfaceChangedError, RequestBudgetExceeded
from wardlens.emr.report_links import (
    lab_categories,
    pagination_urls,
    report_detail_url,
    same_report_identity,
)
from wardlens.emr.vgh import VGHReadOnlyAdapter, _FetchedPage, _RequestBudget
from wardlens.models import EMR_TIMEZONE, PatientBundle, PatientSummary, SourceRecord

PATIENT = PatientSummary(histno="90000001", case_no="SYNTH")
INDEX_URL = "https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm?action=findRes&histno=90000001&tmonth=2026&tdept=ALL"


def report_index(rows):
    return (
        "<table id='reslist'><tr><th>項目</th><th>報告時間</th></tr>"
        + "".join(
            f"<tr><td><a href='{href}'>{title}</a></td><td>{day}</td></tr>"
            for title, day, href in rows
        )
        + "</table>"
    )


def bundle():
    return PatientBundle(
        PATIENT, fetched_at=datetime(2026, 10, 16, tzinfo=EMR_TIMEZONE), lookback_months=3
    )


def test_rare_result_table_and_narrative_without_rscontent_are_preserved(monkeypatch):
    adapter = VGHReadOnlyAdapter()
    current = bundle()
    rare = """<body><h2>Porphyria panel</h2>
    <table><tr><th>Test Name</th><th>Result</th><th>Unit</th><th>Specimen</th><th>Reference</th></tr>
    <tr><td>Porphobilinogen</td><td>18.2</td><td>mg/24 h</td><td>24-hour urine</td><td>&lt;2.0</td></tr>
    <tr><td>Porphyrin screen</td><td>Positive</td><td></td><td>Plasma</td><td>Negative</td></tr></table>
    <p>Method: LC-MS/MS. Interpret in clinical context.</p>
    <table><tr><th>Test</th><th>Result</th></tr><tr><td>ALA</td><td>Pending</td></tr></table>
    </body>"""

    def fetch(url):
        if "tmonth=" in url:
            return _FetchedPage(
                report_index(
                    [("Porphyria panel", "2026/10/15 11:43", "?action=findRes&report=rare")]
                ),
                url,
            )
        return _FetchedPage(rare, url)

    monkeypatch.setattr(adapter, "_get", fetch)
    adapter._fetch_report_history(current, _RequestBudget(10))
    report = current.records_of("report")[0]
    assert "Porphobilinogen\t18.2\tmg/24 h\t24-hour urine\t<2.0" in report.content
    assert "Plasma\tNegative" in report.content and "ALA\tPending" in report.content
    assert "LC-MS/MS" in report.content
    assert report.observed_at.hour == 11 and report.observed_at.minute == 43
    assert current.coverage["report_details_complete"]


def test_selection_table_is_not_treated_as_completed_report(monkeypatch):
    adapter, current = VGHReadOnlyAdapter(), bundle()

    def fetch(url):
        if "tmonth=" in url:
            return _FetchedPage(
                report_index([("Panel", "2026/10/15", "?action=findRes&report=1")]), url
            )
        return _FetchedPage(
            "<table><tr><th>Date</th><th>Department</th></tr><tr><td>10/15</td><td>Lab</td></tr></table>",
            url,
        )

    monkeypatch.setattr(adapter, "_get", fetch)
    adapter._fetch_report_history(current, _RequestBudget(10))
    assert not current.records_of("report")
    assert current.coverage["report_failed"] == 1
    assert not current.coverage["report_details_complete"]


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/emr/report.cfm?report=1",
        "https://web9.vghtpe.gov.tw:444/emr/report.cfm?report=1",
        "https://user@web9.vghtpe.gov.tw/emr/report.cfm?report=1",
        "?action=findRes&report=1&histno=99999999",
        "?action=findRes&report=1&patient_id=99999999",
        "?action=findRes&report=1&histno=90000001&HistNo=99999999",
        "?action=deleteRecord&report=1",
        "?action=findRes&report=1&mode=update",
        "?action=findRes&report=1&redirect=https://evil.example",
        "/emr/unknown.cfm?histno=90000001&report=1",
        "/emr/%2e%2e/delete.cfm?report=1",
        "javascript:deleteRecord('https://web9.vghtpe.gov.tw/emr/report.cfm?report=1')",
        "javascript:showReport('?action=findRes&report=1');alert(1)",
        "?action=findRes&histno=90000001&tmonth=2025&tdept=ALL",  # another index, not a detail
    ],
)
def test_untrusted_report_links_are_never_accepted(href):
    assert report_detail_url(href, INDEX_URL, PATIENT.histno) is None


def test_literal_report_wrapper_is_parsed_without_executing_script():
    href = "javascript:showReport('?action=findRes&report=rare&histno=90000001')"
    url = report_detail_url(href, INDEX_URL, PATIENT.histno)
    assert parse_qs(urlparse(url).query)["report"] == ["rare"]
    assert report_detail_url(
        "/emr/reports/viewreport.cfm?report=rare&histno=90000001", INDEX_URL, PATIENT.histno
    )


def test_returned_findres_opaque_identity_is_loaded_without_guessing_key_names(monkeypatch):
    adapter, current, calls = VGHReadOnlyAdapter(), bundle(), []
    href = "?action=findRes&histno=90000001&rspkey=OPAQUE-7318&specid=URINE"

    def fetch(url):
        calls.append(url)
        if "tmonth=" in url:
            return _FetchedPage(report_index([("Rare report", "2026/10/15", href)]), url)
        return _FetchedPage("<pre>Porphobilinogen synthetic rare result</pre>", url)

    monkeypatch.setattr(adapter, "_get", fetch)
    adapter._fetch_report_history(current, _RequestBudget(10))
    assert len(calls) == 2
    assert current.coverage["report_loaded"] == 1
    assert current.coverage["report_details_complete"]
    assert parse_qs(urlparse(calls[1]).query)["rspkey"] == ["OPAQUE-7318"]


@pytest.mark.parametrize(
    "href",
    [
        "?action=findRes&rspkey=OPAQUE&mode=view",
        "?action=findRes&rspkey=OPAQUE&operation=read",
        "?action=findRes&rspkey=OPAQUE&cmd=show",
        "?action=findRes&rspkey=OPAQUE&histno=99999999",
        "?action=findRes&tmonth=2026&tdept=ALL&page=2&sortby=date",
        "?action=findRes&rspkey=",
        "/emr/unknown.cfm?rspkey=OPAQUE",
    ],
)
def test_opaque_identifiers_do_not_expand_endpoint_or_operation_authority(href):
    assert report_detail_url(href, INDEX_URL, PATIENT.histno) is None


@pytest.mark.parametrize("change", ["replace", "remove", "add"])
def test_every_opaque_identity_field_must_survive_redirect(change):
    original = "https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm?action=findRes&histno=90000001&rspkey=OPAQUE&specid=URINE"
    redirected = {
        "replace": original.replace("specid=URINE", "specid=PLASMA"),
        "remove": original.replace("&specid=URINE", ""),
        "add": original + "&anotherkey=OTHER",
    }[change]
    assert same_report_identity(original, original, PATIENT.histno)
    assert not same_report_identity(original, redirected, PATIENT.histno)


@pytest.mark.parametrize(
    ("source_time", "precision", "clock"),
    [
        ("2026/10/15", "day", (0, 0, 0)),
        ("2026/10/15 00:00", "minute", (0, 0, 0)),
        ("2026/10/15 11:43", "minute", (11, 43, 0)),
        ("2026/10/15 11:43:00", "second", (11, 43, 0)),
        ("2026/10/15 11:43:27", "second", (11, 43, 27)),
        ("2026/10/15 11:43:99", "day", (0, 0, 0)),
        ("unknown", "", None),
    ],
)
def test_report_timestamp_retains_source_precision(source_time, precision, clock, monkeypatch):
    adapter, current = VGHReadOnlyAdapter(), bundle()

    def fetch(url):
        if "tmonth=" in url:
            return _FetchedPage(
                report_index([("Panel", source_time, "?action=findRes&report=1")]), url
            )
        return _FetchedPage("<pre>Synthetic report</pre>", url)

    monkeypatch.setattr(adapter, "_get", fetch)
    adapter._fetch_report_history(current, _RequestBudget(10))
    record = current.records_of("report")[0]
    assert record.observed_precision == precision
    if clock is None:
        assert record.observed_at is None
    else:
        assert (
            record.observed_at.hour,
            record.observed_at.minute,
            record.observed_at.second,
        ) == clock


def test_numbered_hidden_form_and_next_links_are_bound_to_patient_year_and_department():
    html = """<a href='?page=2'>2</a><a rel='next' href='?page=3'>more</a>
    <a href='?page=4&histno=99999999'>Next</a>
    <a href='?page=4&tmonth=2025'>Next</a><a href='?page=4&tdept=OTHER'>Next</a>
    <a href='javascript:page(5)'>Next</a>
    <form method='get'><input type='hidden' name='action' value='findRes'>
    <input type='hidden' name='histno' value='90000001'><input type='hidden' name='tmonth' value='2026'>
    <input type='hidden' name='tdept' value='ALL'><select name='page'><option value='6'>6</option></select></form>
    <form method='get'><input type='hidden' name='page' value='7'></form>
    <form method='post'><input type='hidden' name='page' value='8'></form>"""
    urls, rejected = pagination_urls(BeautifulSoup(html, "html.parser"), INDEX_URL, PATIENT.histno)
    queries = [parse_qs(urlparse(url).query) for url in urls]
    assert {query["page"][0] for query in queries} == {"2", "3", "6", "7"}
    assert all(
        query["histno"] == [PATIENT.histno]
        and query["tmonth"] == ["2026"]
        and query["tdept"] == ["ALL"]
        for query in queries
    )
    assert rejected == 5


def test_unsafe_pagination_and_repeated_index_are_explicit(monkeypatch):
    adapter, current = VGHReadOnlyAdapter(), bundle()
    calls = []
    page = report_index([("Panel", "2026/10/15", "?action=findRes&report=1")])

    def fetch(url):
        calls.append(url)
        if "tmonth=" in url:
            return _FetchedPage(
                page + "<a href='?page=2'>Next</a><a href='?page=3&histno=99999999'>Next</a>", url
            )
        return _FetchedPage("<pre>Available report</pre>", url)

    monkeypatch.setattr(adapter, "_get", fetch)
    adapter._fetch_report_history(current, _RequestBudget(10))
    assert len(calls) == 3 and not any("99999999" in url for url in calls)
    assert current.coverage["report_loaded"] == 1
    assert not current.coverage["report_discovery_complete"]
    assert any("重複" in text for text in current.warnings)


def test_discover_all_returned_categories_and_pages_without_enumerating_codes(monkeypatch):
    messages, calls = [], []
    adapter = VGHReadOnlyAdapter(AppSettings(lab_lookback_months=12), progress=messages.append)
    adapter._logged_in = True
    monkeypatch.setattr(adapter, "_enter_patient_context", lambda *_: None)
    monkeypatch.setattr(adapter, "_fetch_report_history", lambda *_: None)

    def fetch(url):
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        action = query["action"][0]
        if action == "findPba":
            return _FetchedPage(
                "<table><tr><td>Profile</td></tr></table><a href='?action=findResd&resdtype=DRARE&histno=90000001'>Rare tests</a>",
                url,
            )
        if action != "findResd":
            return _FetchedPage(
                "<table id='tprlist'><tr><td>2026/10/15 BP 120/80</td></tr></table>", url
            )
        code = query["resdtype"][0]
        controls = "<select name='resdtype'><option value='DCHEM'>Chemistry</option><option value='DCBC'>CBC</option><option value='DURIN'>Urine</option></select>"
        controls += "<a href='?action=findResd&resdtype=DWRONG&histno=99999999'>Wrong patient</a>"
        if code == "DRARE" and "page" not in query:
            controls += "<a rel='next' href='?page=2'>Next</a>"
        date = "2026/07/20" if "page" in query else "2026/10/15"
        return _FetchedPage(
            controls
            + f"<table id='resdtable'><tr><th>Date</th><th>Porphobilinogen</th></tr><tr><td>{date}</td><td>18.2 mg/24 h</td></tr></table>",
            url,
        )

    monkeypatch.setattr(adapter, "_get", fetch)
    current = adapter.fetch_patient_bundle(PATIENT)
    labs = [parse_qs(urlparse(url).query) for url in calls if "findResd" in url]
    assert {query["resdtype"][0] for query in labs} == {"DCHEM", "DCBC", "DURIN", "DRARE"}
    assert len(labs) == 5 and all(query["resdtmonth"] == ["12"] for query in labs)
    assert len(current.records_of("lab_drare")) == 2
    assert (
        current.coverage["lab_categories_discovered"]
        == current.coverage["lab_categories_loaded"]
        == 4
    )
    assert not current.coverage["lab_categories_complete"]  # unsafe category is visible as a gap
    assert all(
        record.observed_at is None
        for record in current.records
        if record.source_type.startswith("lab_")
    )
    assert all("90000001" not in text and "Porphobilinogen" not in text for text in messages)


def test_category_discovery_rejects_wrong_patient_and_invalid_codes():
    html = """<select name='resdtype'><option value='DURIN'>Urine</option><option value='../write'>Bad</option></select>
    <a href='?action=findResd&resdtype=DRARE&histno=90000001'>Rare</a>
    <a href='?action=findResd&resdtype=DOTHER&histno=99999999'>Other</a>"""
    codes, rejected = lab_categories(BeautifulSoup(html, "html.parser"), INDEX_URL, PATIENT.histno)
    assert set(codes) == {"DURIN", "DRARE"} and rejected == 2


def test_cancellation_preserves_completed_records_and_stops_new_requests(monkeypatch):
    cancelled, calls = Event(), []
    adapter = VGHReadOnlyAdapter(cancel_event=cancelled)
    adapter._logged_in = True
    monkeypatch.setattr(adapter, "_enter_patient_context", lambda *_: None)

    def record(_budget, **kwargs):
        calls.append(kwargs["source_type"])
        cancelled.set()
        return SourceRecord("profile", "Synthetic", "Available synthetic source")

    monkeypatch.setattr(adapter, "_fetch_record", record)
    current = adapter.fetch_patient_bundle(PATIENT)
    assert calls == ["profile"] and len(current.records) == 1
    assert current.coverage["cancelled"]
    assert not current.coverage["report_discovery_complete"]
    assert not current.coverage["report_details_complete"]


def test_cancellation_during_report_details_keeps_progress(monkeypatch):
    cancelled, calls = Event(), []
    adapter, current = VGHReadOnlyAdapter(cancel_event=cancelled), bundle()

    def fetch(url):
        calls.append(url)
        if "tmonth=" in url:
            return _FetchedPage(
                report_index(
                    [(f"Panel {n}", "2026/10/15", f"?action=findRes&report={n}") for n in range(3)]
                ),
                url,
            )
        cancelled.set()
        return _FetchedPage("<pre>Available synthetic result</pre>", url)

    monkeypatch.setattr(adapter, "_get", fetch)
    with pytest.raises(EMRCancelled):
        adapter._fetch_report_history(current, _RequestBudget(10))
    assert len(calls) == 2 and current.coverage["report_loaded"] == 1
    assert (
        len(current.records_of("report")) == 1 and not current.coverage["report_details_complete"]
    )


def test_cancel_after_limiter_never_sends_request(monkeypatch):
    cancelled = Event()
    adapter = VGHReadOnlyAdapter(cancel_event=cancelled)
    monkeypatch.setattr(adapter._limiter, "wait", cancelled.set)
    monkeypatch.setattr(
        adapter.session,
        "get",
        lambda *_args, **_kwargs: pytest.fail("Request sent after cancellation"),
    )
    with pytest.raises(EMRCancelled):
        adapter._get(INDEX_URL)


def test_finite_budget_is_an_explicit_partial_result(monkeypatch):
    adapter, current = VGHReadOnlyAdapter(), bundle()

    def fetch(url):
        if "tmonth=" in url:
            return _FetchedPage(
                report_index([("Panel", "2026/10/15", "?action=findRes&report=1")]), url
            )
        pytest.fail("Budget should stop the detail request")

    monkeypatch.setattr(adapter, "_get", fetch)
    with pytest.raises(RequestBudgetExceeded):
        adapter._fetch_report_history(current, _RequestBudget(1))
    assert current.records_of("report_index") and not current.coverage["report_details_complete"]


def response(url, *, target=None):
    value = requests.Response()
    value.url = url
    value.status_code = 302 if target else 200
    value._content = b"<pre>Synthetic report</pre>"
    value.encoding = "utf-8"
    if target:
        value.headers["Location"] = target
    return value


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/capture",
        INDEX_URL.replace("90000001", "99999999"),
        INDEX_URL.replace("findRes", "deleteRecord"),
        INDEX_URL.replace("2026", "2025"),
    ],
)
def test_result_redirect_is_rejected_before_following(target, monkeypatch):
    adapter, calls = VGHReadOnlyAdapter(), []
    monkeypatch.setattr(adapter._limiter, "wait", lambda: None)

    def get(url, **kwargs):
        calls.append((url, kwargs["allow_redirects"]))
        return response(url, target=target)

    monkeypatch.setattr(adapter.session, "get", get)
    with pytest.raises(InterfaceChangedError):
        adapter._get(INDEX_URL)
    assert calls == [(INDEX_URL, False)]


def test_safe_result_redirect_counts_budget_and_rate_each_hop(monkeypatch):
    adapter, calls, waits = VGHReadOnlyAdapter(), [], []
    budget = adapter._active_budget = _RequestBudget(2)
    budget.consume()  # The calling fetch routine charges the initial request.
    monkeypatch.setattr(adapter._limiter, "wait", lambda: waits.append(1))

    def get(url, **kwargs):
        assert not kwargs["allow_redirects"]
        calls.append(url)
        return response(url, target=INDEX_URL + "&page=2" if len(calls) == 1 else None)

    monkeypatch.setattr(adapter.session, "get", get)
    page = adapter._get(INDEX_URL)
    assert len(calls) == len(waits) == budget.used == 2
    assert "page=2" in page.url


def test_report_redirect_cannot_change_the_linked_report(monkeypatch):
    adapter, calls = VGHReadOnlyAdapter(), []
    url = "https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm?action=findRes&histno=90000001&report=one"
    monkeypatch.setattr(adapter._limiter, "wait", lambda: None)

    def get(requested, **kwargs):
        calls.append(requested)
        return response(requested, target=url.replace("report=one", "report=two"))

    monkeypatch.setattr(adapter.session, "get", get)
    with pytest.raises(InterfaceChangedError):
        adapter._get(url)
    assert calls == [url]


@pytest.mark.parametrize("stop", ["cancel", "budget"])
def test_redirect_stops_before_next_request_on_cancel_or_budget(stop, monkeypatch):
    cancelled, calls = Event(), []
    adapter = VGHReadOnlyAdapter(cancel_event=cancelled)
    adapter._active_budget = _RequestBudget(1 if stop == "budget" else 2)
    adapter._active_budget.consume()
    monkeypatch.setattr(adapter._limiter, "wait", lambda: None)

    def get(url, **kwargs):
        calls.append(url)
        if stop == "cancel":
            cancelled.set()
        return response(url, target=INDEX_URL + "&page=2")

    monkeypatch.setattr(adapter.session, "get", get)
    with pytest.raises(EMRCancelled if stop == "cancel" else RequestBudgetExceeded):
        adapter._get(INDEX_URL)
    assert calls == [INDEX_URL]


def test_table_footer_pagination_and_td_headers_are_not_clinical_rows(monkeypatch):
    adapter, current = VGHReadOnlyAdapter(), bundle()
    adapter.max_result_pages = 2

    def fetch(url):
        if "tmonth=" in url:
            page = parse_qs(urlparse(url).query).get("page", ["1"])[0]
            html = (
                "<table id='reslist'><tr><td>項目</td><td>報告日期</td></tr>"
                f"<tr><td><a href='?action=findRes&report={page}'>Panel</a></td><td>2026/10/15</td></tr>"
                "<tr><td colspan='2'><a href='?page=2'>Next</a></td></tr></table>"
            )
            return _FetchedPage(html, url)
        return _FetchedPage("<pre>Synthetic report</pre>", url)

    monkeypatch.setattr(adapter, "_get", fetch)
    adapter._fetch_report_history(current, _RequestBudget(10))
    assert current.coverage["report_candidates"] == current.coverage["report_loaded"] == 2
    assert current.coverage["report_skipped"] == 0
    assert current.coverage["report_discovery_complete"]
    assert all(record.observed_at is not None for record in current.records_of("report"))
