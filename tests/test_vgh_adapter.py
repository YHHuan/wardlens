from __future__ import annotations

from dataclasses import dataclass, field

import pytest
import requests

from wardlens.config import AppSettings
from wardlens.emr.base import LoginError
from wardlens.emr.vgh import VGHReadOnlyAdapter


@dataclass
class _Cookies:
    def clear(self) -> None:
        return None


@dataclass
class _Response:
    text_value: str
    url: str
    status_code: int = 200
    encoding: str = "utf-8"

    @property
    def content(self) -> bytes:
        return self.text_value.encode("utf-8")

    @property
    def text(self) -> str:
        return self.text_value

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@dataclass
class _Session:
    pages: dict[str, _Response] = field(default_factory=dict)
    error: Exception | None = None
    headers: dict[str, str] = field(default_factory=dict)
    cookies: _Cookies = field(default_factory=_Cookies)
    calls: list[str] = field(default_factory=list)

    def get(self, url: str, **_kwargs) -> _Response:
        self.calls.append(url)
        if self.error:
            raise self.error
        if url in self.pages:
            return self.pages[url]
        raise AssertionError(f"Unexpected URL: {url}")

    def close(self) -> None:
        return None


def _page(rows: list[tuple[str, str, str]], total: int, next_href: str = "") -> str:
    body = "".join(
        f'<tr><td>{bed}</td><td>{name}</td><td><a href="?histno={histno}">{histno}</a></td></tr>'
        for bed, name, histno in rows
    )
    next_link = f'<a rel="next" href="{next_href}">下一頁</a>' if next_href else ""
    return (
        f'<div>共 {total} 人</div><table id="patlist"><thead><tr>'
        "<th>床號</th><th>姓名</th><th>病歷號</th></tr></thead>"
        f"<tbody>{body}</tbody></table>{next_link}"
    )


def test_search_follows_pagination_and_reconciles_total() -> None:
    settings = AppSettings(max_patient_pages=5, max_patients=50)
    first_url = (
        "https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm?"
        "action=findPatient&wd=0&histno=&pidno=&namec=&drid=DOC&er=0&bilqrta=0&bilqrtdt=&bildurdt=0&other=0&nametype="
    )
    # Empty parameters are omitted by urlencode input filtering only in _url, not
    # in the patient search query, so this exact URL is intentional.
    rows1 = [(str(i), f"P{i}", str(90000000 + i)) for i in range(1, 16)]
    rows2 = [(str(i), f"P{i}", str(90000000 + i)) for i in range(16, 19)]
    page2_url = "https://web9.vghtpe.gov.tw/emr/page2"
    session = _Session(
        pages={
            first_url: _Response(_page(rows1, 18, "/emr/page2"), first_url),
            page2_url: _Response(_page(rows2, 18), page2_url),
        }
    )
    adapter = VGHReadOnlyAdapter(settings=settings, session=session)
    adapter._logged_in = True
    adapter._limiter.wait = lambda: None
    result = adapter.search_patients(doctor_id="DOC")
    assert result.complete
    assert result.declared_total == 18
    assert len(result.patients) == 18
    assert result.pages_fetched == 2


def test_network_error_discards_authenticated_session() -> None:
    adapter = VGHReadOnlyAdapter(session=_Session(error=requests.ConnectionError("drop")))
    adapter._logged_in = True
    adapter._limiter.wait = lambda: None
    with pytest.raises(LoginError, match="session 已丟棄"):
        adapter._get("https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm?action=findPatient")
    assert not adapter.logged_in


def test_cross_origin_final_redirect_discards_session() -> None:
    requested = "https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm?action=findPatient"
    session = _Session(
        pages={requested: _Response("<html>unexpected</html>", "https://evil.example/capture")}
    )
    adapter = VGHReadOnlyAdapter(session=session)
    adapter._logged_in = True
    adapter._limiter.wait = lambda: None
    with pytest.raises(LoginError, match="非允許網域"):
        adapter._get(requested)
    assert not adapter.logged_in


def test_histno_batch_is_incomplete_when_exact_patient_is_missing() -> None:
    settings = AppSettings(max_patient_pages=2, max_patients=10)
    query_url = (
        "https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm?"
        "action=findPatient&wd=0&histno=140169&pidno=&namec=&drid=&er=0&bilqrta=0&bilqrtdt=&bildurdt=0&other=0&nametype="
    )
    session = _Session(
        pages={
            query_url: _Response(
                _page([("01", "Different", "99999999")], 1),
                query_url,
            )
        }
    )
    adapter = VGHReadOnlyAdapter(settings=settings, session=session)
    adapter._logged_in = True
    adapter._limiter.wait = lambda: None
    result = adapter.search_patients(histnos=["140169"])
    assert not result.complete
    assert not result.patients
    assert result.declared_total == 1


def test_formal_report_word_blocked_is_not_false_block() -> None:
    assert not VGHReadOnlyAdapter._looks_like_block_or_error(
        "<div id='RSCONTENT'>Abdominal ultrasound: GAS BLOCKED.</div>",
        "https://web9.vghtpe.gov.tw/emr/report",
    )


def test_strong_block_marker_is_detected() -> None:
    assert VGHReadOnlyAdapter._looks_like_block_or_error(
        "<html>系統偵測大量異常存取，請稍後再試</html>",
        "https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm",
    )
