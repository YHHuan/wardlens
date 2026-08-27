from __future__ import annotations

import pytest

from wardlens.emr.base import InterfaceChangedError
from wardlens.emr.parsers import PatientListParser, same_origin_https


def _table(rows: list[tuple[str, str, str]], *, total: int, next_href: str = "") -> str:
    body = "".join(
        f'<tr><td>{bed}</td><td>{name}</td><td><a href="qemr.cfm?action=findEmr&histno={histno}">{histno}</a></td></tr>'
        for bed, name, histno in rows
    )
    next_link = f'<a rel="next" href="{next_href}">下一頁</a>' if next_href else ""
    return (
        "<html><body>"
        f'<div>共 {total} 人</div><table id="patlist"><thead><tr>'
        "<th>床號</th><th>姓名</th><th>病歷號</th></tr></thead>"
        f"<tbody>{body}</tbody></table>{next_link}</body></html>"
    )


def test_parser_handles_eighteen_rows() -> None:
    rows = [(f"{idx:02d}", f"測試{idx}", str(90000000 + idx)) for idx in range(1, 19)]
    parsed = PatientListParser().parse(_table(rows, total=18), "https://web9.vghtpe.gov.tw/list")
    assert len(parsed.patients) == 18
    assert parsed.declared_total == 18
    assert parsed.patients[-1].histno == "90000018"


def test_parser_preserves_short_id_and_safe_next_url() -> None:
    parsed = PatientListParser().parse(
        _table([("01", "短號", "140169")], total=1, next_href="/emr/page2"),
        "https://web9.vghtpe.gov.tw/emr/list",
    )
    assert parsed.patients[0].histno == "140169"
    assert parsed.next_url == "https://web9.vghtpe.gov.tw/emr/page2"
    assert same_origin_https(parsed.next_url, {"web9.vghtpe.gov.tw"})


def test_missing_patient_table_is_interface_change_not_empty_list() -> None:
    with pytest.raises(InterfaceChangedError):
        PatientListParser().parse(
            "<html><body>no table</body></html>", "https://web9.vghtpe.gov.tw/"
        )


def test_cross_origin_or_http_is_rejected() -> None:
    assert not same_origin_https("http://web9.vghtpe.gov.tw/x", {"web9.vghtpe.gov.tw"})
    assert not same_origin_https("https://evil.example/x", {"web9.vghtpe.gov.tw"})
