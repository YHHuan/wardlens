from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from wardlens.emr.base import InterfaceChangedError
from wardlens.models import PatientSummary


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def table_to_tsv(table: Tag, max_rows: int = 500) -> str:
    lines: list[str] = []
    for row in table.find_all("tr")[:max_rows]:
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
        if any(cells):
            lines.append("\t".join(cells))
    return "\n".join(lines)


@dataclass(slots=True)
class ParsedPatientPage:
    patients: list[PatientSummary]
    next_url: str | None
    declared_total: int | None
    fingerprint: str
    warnings: list[str] = field(default_factory=list)


class PatientListParser:
    _histno_keys = ("histno", "hisid", "phistnum", "adistno")

    def parse(self, html: str, page_url: str) -> ParsedPatientPage:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", id="patlist") or self._find_patient_table(soup)
        if table is None:
            raise InterfaceChangedError("找不到病人清單表格；院內頁面可能已改版。")

        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all("th")]
        header_map = {self._normalize_header(value): index for index, value in enumerate(headers)}
        body = table.find("tbody") or table
        patients: list[PatientSummary] = []
        warnings: list[str] = []

        for row in body.find_all("tr"):
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            if not cells or not any(cells):
                continue
            histno, case_no = self._ids_from_row(row, cells, header_map)
            if not histno:
                warnings.append("有一列無法辨識病歷號，已略過。")
                continue
            name = self._value(cells, header_map, ("姓名", "病人姓名", "name"))
            ward = self._value(cells, header_map, ("病房", "護理站", "ward"))
            bed = self._value(cells, header_map, ("床號", "病床", "bed"))
            age = self._value(cells, header_map, ("年齡", "age"))
            sex = self._value(cells, header_map, ("性別", "sex"))
            if not bed and cells:
                bed = self._legacy_bed(cells)
            patients.append(
                PatientSummary(
                    histno=histno,
                    name=name,
                    ward=ward,
                    bed=bed,
                    age=age,
                    sex=sex,
                    case_no=case_no,
                    raw_columns=tuple(cells),
                )
            )

        next_url = self._next_url(soup, page_url)
        declared_total = self._declared_total(soup)
        fingerprint_seed = (
            "|".join(patient.local_key for patient in patients) + f"|{next_url or ''}"
        )
        fingerprint = sha256(fingerprint_seed.encode("utf-8")).hexdigest()
        return ParsedPatientPage(patients, next_url, declared_total, fingerprint, warnings)

    @staticmethod
    def _normalize_header(value: str) -> str:
        return re.sub(r"[\s　:：._-]", "", value).lower()

    def _find_patient_table(self, soup: BeautifulSoup) -> Tag | None:
        for table in soup.find_all("table"):
            header_text = clean_text(
                " ".join(cell.get_text(" ", strip=True) for cell in table.find_all("th"))
            )
            normalized = self._normalize_header(header_text)
            if any(token in normalized for token in ("病歷", "histno", "姓名", "床號")):
                return table
        return None

    def _ids_from_row(
        self,
        row: Tag,
        cells: list[str],
        header_map: dict[str, int],
    ) -> tuple[str, str]:
        histno = ""
        case_no = ""
        for anchor in row.find_all("a", href=True):
            query = parse_qs(urlparse(anchor["href"]).query)
            for key in self._histno_keys:
                value = query.get(key)
                if value and re.fullmatch(r"\d{5,12}", value[0]):
                    histno = value[0]
                    break
            for key in ("caseno", "adicase"):
                value = query.get(key)
                if value and re.fullmatch(r"[A-Za-z0-9-]{4,24}", value[0]):
                    case_no = value[0]
            if histno:
                break

        if not histno:
            histno = self._value(cells, header_map, ("病歷號", "病歷編號", "histno", "chartno"))
            histno = re.sub(r"\D", "", histno)
        if not histno:
            candidates = [re.sub(r"\D", "", cell) for cell in cells]
            candidates = [value for value in candidates if 6 <= len(value) <= 10]
            histno = candidates[0] if candidates else ""
        return histno, case_no

    def _value(
        self,
        cells: list[str],
        header_map: dict[str, int],
        candidates: tuple[str, ...],
    ) -> str:
        normalized_candidates = {self._normalize_header(candidate) for candidate in candidates}
        for header, index in header_map.items():
            if header in normalized_candidates or any(
                candidate in header for candidate in normalized_candidates
            ):
                if index < len(cells):
                    return cells[index]
        return ""

    @staticmethod
    def _legacy_bed(cells: list[str]) -> str:
        for cell in cells[:3]:
            if re.fullmatch(r"[A-Za-z]?\d{1,4}[- ]?\d{0,3}", cell):
                return cell
        return ""

    @staticmethod
    def _next_url(soup: BeautifulSoup, page_url: str) -> str | None:
        candidates = list(soup.find_all("a", rel="next"))
        if not candidates:
            for anchor in soup.find_all("a", href=True):
                label = clean_text(anchor.get_text(" ", strip=True)).lower()
                if label in {"下一頁", "下頁", "next", ">", "›", "»"}:
                    candidates.append(anchor)
                    break
        if not candidates:
            return None
        href = candidates[0].get("href", "").strip()
        if not href or href.lower().startswith("javascript:"):
            return None
        return urljoin(page_url, href)

    @staticmethod
    def _declared_total(soup: BeautifulSoup) -> int | None:
        text = clean_text(soup.get_text(" ", strip=True))
        patterns = (
            r"(?:總筆數|共計|共)\s*[:：]?\s*(\d+)\s*(?:筆|人)",
            r"(?:total)\s*[:：]?\s*(\d+)",
            r"(?:第\s*\d+\s*[-~至]\s*\d+\s*筆?\s*/\s*)?(\d+)\s*筆",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None


def same_origin_https(url: str, allowed_hosts: set[str]) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in allowed_hosts
