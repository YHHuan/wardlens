"""Parse returned EMR navigation without executing scripts or guessing endpoints.

Only the selected patient's result indexes may supply report links. These helpers
do not establish a session, probe category codes, or fetch arbitrary same-host URLs.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from wardlens.emr.parsers import clean_text

_QEMR = "/emr/qemr/qemr.cfm"
_PATIENT_KEYS = {
    "histno",
    "hist_no",
    "chartno",
    "chart_no",
    "mrn",
    "medicalrecordno",
    "patientid",
    "patient_id",
    "pid",
    "pidno",
}
_CURSORS = {"page", "pageno", "pagenum", "pagenumber", "pageindex", "offset", "startrow"}
_DETAIL_KEYS = {
    "report",
    "reportid",
    "reportno",
    "report_no",
    "rptid",
    "repno",
    "resid",
    "resno",
    "resultid",
    "rsno",
    "accession",
    "accessionno",
    "orderno",
    "orderseq",
    "ordno",
    "seq",
    "labno",
    "id",
}
_INDEX_FILTERS = (
    _PATIENT_KEYS
    | _CURSORS
    | {
        "action",
        "tmonth",
        "tdept",
        "resdtype",
        "resdtmonth",
        "year",
        "month",
        "dept",
        "department",
        "from",
        "to",
        "start",
        "end",
        "sort",
        "sortby",
        "orderby",
        "order",
        "limit",
        "pagesize",
        "page_size",
        "format",
        "view",
        "template",
        "lang",
        "language",
        "print",
    }
)
_OPERATION_CONTROLS = {
    "mode",
    "method",
    "op",
    "operation",
    "cmd",
    "command",
    "task",
    "function",
    "func",
    "event",
    "eventtarget",
    "do",
    "handler",
}
_WRITE = re.compile(
    r"delete|update|insert|save|submit|logout|remove|cancel|upload|execute|approve|publish|edit",
    re.I,
)
_NEXT = re.compile(
    r"下一頁|下頁|上一頁|上頁|末頁|首頁|^(?:next|previous|prev|last|first|[<>»«›‹]+|\d+)$", re.I
)


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(
        parsed._replace(
            query=urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True))), fragment=""
        )
    )


def _query(url: str) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True):
        key = key.lower()
        if key in result or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            return None
        if _WRITE.search(key) or _WRITE.search(value):
            return None
        if any(ord(char) < 32 for char in value) or key in {
            "url",
            "redirect",
            "redirecturi",
            "callback",
            "returnurl",
        }:
            return None
        result[key] = value
    return result


def _literal_href(href: str) -> str | None:
    href = href.strip()
    if not href or href.startswith("#"):
        return None
    if href.lower().startswith("javascript:"):
        # Some observed upstream readers unwrap a report-display function. We
        # extract only one literal URL from recognized display wrappers, never JS.
        match = re.fullmatch(
            r"javascript:\s*(?:window\.open|open|showReport|viewReport|openReport|showRes)"
            r"\(\s*(['\"])([^'\"\r\n\\]+)\1\s*(?:,\s*['\"]_blank['\"]\s*)?\)\s*;?",
            href,
            re.I,
        )
        return match.group(2) if match else None
    return href


def _validated_url(href: str, base_url: str, histno: str) -> str | None:
    literal = _literal_href(href)
    if literal is None or re.search(r"[\\\x00-\x20]", literal):
        return None
    # Do not allow path normalization to conceal traversal / encoded scripts.
    if re.search(r"%2e|%2f|%5c|%00|(?:^|/)\.\.(?:/|$)", literal, re.I):
        return None
    url = urljoin(base_url, literal)
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "web9.vghtpe.gov.tw"
        or port not in {None, 443}
        or parsed.username
        or parsed.password
        or not parsed.path.startswith("/emr/")
        or parsed.params
        or _WRITE.search(parsed.path)
    ):
        return None
    query = _query(url)
    if query is None or any(query[key] != histno for key in _PATIENT_KEYS & query.keys()):
        return None
    return urlunparse(parsed._replace(fragment=""))


def report_detail_url(href: str, base_url: str, histno: str) -> str | None:
    """Validate a link originating in a selected patient's report-index row."""
    url = _validated_url(href, base_url, histno)
    if url is None:
        return None
    parsed, query = urlparse(url), _query(url)
    assert query is not None
    if _OPERATION_CONTROLS & query.keys():
        return None
    if parsed.path.lower() == _QEMR:
        if query.get("action", "").lower() != "findres":
            return None
        # A returned row may use a hospital-specific opaque identifier whose
        # name is absent from our fixtures. The already-known read-only findRes
        # endpoint is sufficient; do not guess its ID parameter name. Pure
        # index/filter links still cannot masquerade as report details.
        if not any(value for key, value in query.items() if key not in _INDEX_FILTERS):
            return None
    else:
        if not any(query.get(key) for key in _DETAIL_KEYS):
            return None
        # A returned result display page is allowed; an unknown endpoint is not.
        # This is deliberately narrower than permitting every /emr/ GET.
        filename = parsed.path.rsplit("/", 1)[-1]
        if not re.fullmatch(
            r"(?:view|show|read|get|print)?(?:res|result|report)(?:detail|content)?\.(?:cfm|asp|aspx|jsp|php)",
            filename,
            re.I,
        ):
            return None
        if query.get("action", "").lower() not in {
            "",
            "view",
            "show",
            "read",
            "get",
            "print",
            "findres",
        }:
            return None
    return canonical_url(url)


def same_result_scope(requested_url: str, returned_url: str, histno: str) -> bool:
    """A result-page redirect must preserve the selected patient and filters."""
    safe = _validated_url(returned_url, requested_url, histno)
    requested, returned = _query(requested_url), _query(returned_url)
    if safe is None or requested is None or returned is None:
        return False
    if urlparse(safe).path.lower() != _QEMR:
        return False
    return all(
        requested.get(key) == returned.get(key)
        for key in ("action", "histno", "tmonth", "tdept", "resdtype", "resdtmonth")
    )


def same_report_identity(requested_url: str, returned_url: str, histno: str) -> bool:
    if report_detail_url(returned_url, requested_url, histno) is None:
        return False
    requested, returned = _query(requested_url), _query(returned_url)
    if requested is None or returned is None:
        return False
    if urlparse(requested_url).path.lower() == _QEMR:
        # Preserve every original opaque field, not only familiar report-ID keys.
        # Adding or removing one can change the selected report just as a new
        # value can, even if another recognized ID happens to remain the same.
        requested_identity = {
            key: value for key, value in requested.items() if key not in _INDEX_FILTERS
        }
        returned_identity = {
            key: value for key, value in returned.items() if key not in _INDEX_FILTERS
        }
        if requested_identity != returned_identity:
            return False
    else:
        if any(returned.get(key) != requested[key] for key in _DETAIL_KEYS & requested.keys()):
            return False
    return all(returned.get(key) == requested[key] for key in _PATIENT_KEYS & requested.keys())


def _pagination_url(href: str, base_url: str, histno: str) -> str | None:
    """Keep the year/category/month/department and patient bound to this index."""
    literal = _literal_href(href)
    if literal is None:
        return None
    original = _query(base_url)
    explicit = _query(urljoin(base_url, literal))
    if original is None or explicit is None:
        return None
    if original.get("action", "").lower() not in {"findres", "findresd"}:
        return None
    if not _CURSORS & explicit.keys():
        return None
    # Query-only pagination commonly omits all context. Inherit it from the
    # actual current index, never from another patient or a global default.
    if literal.startswith("?") or set(explicit).issubset(_CURSORS):
        merged = {**original, **explicit}
        url = urlunparse(urlparse(urljoin(base_url, literal))._replace(query=urlencode(merged)))
    else:
        merged, url = explicit, urljoin(base_url, literal)
    if any(value != original.get(key) for key, value in merged.items() if key not in _CURSORS):
        return None
    if any(merged.get(key) != value for key, value in original.items() if key not in _CURSORS):
        return None
    if any(not re.fullmatch(r"\d{1,7}", value) for key, value in merged.items() if key in _CURSORS):
        return None
    result = _validated_url(url, base_url, histno)
    if result is None or urlparse(result).path.lower() != _QEMR:
        return None
    return canonical_url(result)


def pagination_urls(soup: BeautifulSoup, base_url: str, histno: str) -> tuple[list[str], int]:
    urls: list[str] = []
    rejected = 0
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        parsed_query = _query(urljoin(base_url, href)) or {}
        label = clean_text(anchor.get_text(" ", strip=True))
        # A numeric report name is not a page number. Actual paging controls can
        # nevertheless be inside a footer row of the results table.
        inside_results = anchor.find_parent(id=re.compile(r"^(?:reslist|resdtable)$", re.I))
        if inside_results and not (_CURSORS & parsed_query.keys()) and re.fullmatch(r"\d+", label):
            continue
        is_pager = (
            bool(_CURSORS & parsed_query.keys())
            or bool(_NEXT.search(label))
            or "next" in anchor.get("rel", [])
        )
        if not is_pager:
            continue
        if not href or href.startswith("#") and "disabled" in anchor.get("class", []):
            continue
        candidate = _pagination_url(href, base_url, histno)
        if candidate:
            urls.append(candidate)
        else:
            rejected += 1
    # Numeric page selectors in an explicitly GET form are also read-only.
    for form in soup.find_all("form"):
        hidden = {
            str(field["name"]): str(field.get("value", ""))
            for field in form.find_all("input", attrs={"type": "hidden", "name": True})
        }
        if _CURSORS & {key.lower() for key in hidden}:
            action = urljoin(base_url, str(form.get("action", "")))
            candidate = _pagination_url(
                urlunparse(urlparse(action)._replace(query=urlencode(hidden))), base_url, histno
            )
            if str(form.get("method", "get")).lower() == "get" and candidate:
                urls.append(candidate)
            else:
                rejected += 1
        for select in form.find_all("select", attrs={"name": True}):
            name = str(select.get("name", "")).lower()
            if name not in _CURSORS:
                continue
            if str(form.get("method", "get")).lower() != "get":
                rejected += 1
                continue
            action = urljoin(base_url, str(form.get("action", "")))
            for option in select.find_all("option", value=True):
                values = {**hidden, name: str(option["value"])}
                candidate = _pagination_url(
                    urlunparse(urlparse(action)._replace(query=urlencode(values))), base_url, histno
                )
                if candidate:
                    urls.append(candidate)
                else:
                    rejected += 1
    for select in soup.find_all("select", attrs={"name": True}):
        if str(select["name"]).lower() in _CURSORS and select.find_parent("form") is None:
            rejected += 1
    return list(dict.fromkeys(urls)), rejected


def lab_categories(soup: BeautifulSoup, base_url: str, histno: str) -> tuple[dict[str, str], int]:
    """Only codes actually returned in category controls or findResd links."""
    found: dict[str, str] = {}
    rejected = 0

    def add(code: str, label: str) -> None:
        nonlocal rejected
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", code):
            found.setdefault(code.upper(), label[:80] or code.upper())
        elif code:
            rejected += 1

    for select in soup.find_all("select"):
        if str(select.get("name", select.get("id", ""))).lower() != "resdtype":
            continue
        for option in select.find_all("option", value=True):
            add(str(option["value"]).strip(), clean_text(option.get_text(" ", strip=True)))
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if "findresd" not in href.lower() or "resdtype" not in href.lower():
            continue
        url = _validated_url(href, base_url, histno)
        query = _query(url) if url else None
        if (
            not url
            or urlparse(url).path.lower() != _QEMR
            or query is None
            or query.get("action", "").lower() != "findresd"
        ):
            rejected += 1
            continue
        add(query.get("resdtype", ""), clean_text(anchor.get_text(" ", strip=True)))
    return found, rejected


def report_content(soup: BeautifulSoup) -> Tag | BeautifulSoup | None:
    """Recognize a report body, including a plain test/result table.

    The fallback does not treat an arbitrary table / a selection page as a
    report. All narrative and tables in a recognized report stay available.
    """
    known = soup.find(id=re.compile(r"^RSCONTENT$", re.I))
    if known is not None:
        return known
    if soup.find(id=re.compile(r"^(?:reslist|patlist)$", re.I)):
        return None
    if soup.find("pre"):
        return soup.find("pre")
    for table in soup.find_all("table"):
        for row in table.find_all("tr", limit=3):
            labels = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            has_test = any(
                re.fullmatch(
                    r"(?:test(?: name| item)?|item|analyte|檢驗(?:項目|名稱)?|項目(?:名稱)?|檢查(?:項目|名稱))",
                    label,
                    re.I,
                )
                for label in labels
            )
            has_result = any(
                re.fullmatch(
                    r"(?:result|value|結果|檢驗(?:值|結果|報告)|檢查結果|報告值)", label, re.I
                )
                for label in labels
            )
            if has_test and has_result:
                for unwanted in soup.find_all(["form", "nav", "script", "style"]):
                    unwanted.decompose()
                return soup
    return None
