"""Remove recognized administrative fields without guessing unknown headings.

The offsets always refer to the original string. No clinical values or raw
identifiers are attached to the returned spans; callers can safely count them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HeaderSpan:
    start: int
    end: int
    kind: str = "administrative_header"


_IDENTITY_LABELS = (
    "病人姓名", "患者姓名", "姓名", "病人名稱", "病歷號碼", "病歷編號", "病歷號",
    "病患編號", "病人編號", "身分證字號", "身份證字號", "身分證號", "身分證", "身份證",
    "出生年月日", "出生日期", "出生年月", "生日", "聯絡電話", "聯絡地址", "通訊地址",
    "戶籍地址", "住址", "地址", "手機號碼", "手機", "電話", "電子郵件", "聯絡人",
    "緊急聯絡人", "家屬姓名", "主治醫師", "住院醫師", "開單醫師", "申請醫師", "採檢者",
    "送檢醫師", "檢驗醫師", "報告醫師", "報告人員", "報告者", "簽核醫師", "審核醫師",
    "檢驗師", "醫檢師", "判讀醫師", "醫師姓名", "醫師", "護理師", "護理人員",
    "檢體編號", "檢體號碼", "檢體號", "標本編號", "標本號", "報告編號", "報告號碼",
    "報告號", "申請單號", "申請單編號", "醫令單號", "醫令編號", "申請序號", "流水號",
    "就醫序號", "住院號", "病床號", "病床", "床位", "床號", "病房", "護理站",
    "病歷連結", "報告連結", "病歷網址", "報告網址",
    "patient name", "name", "patient address", "home address", "address",
    "date of birth", "birth date", "birthdate", "dob", "patient id", "patient number",
    "medical record number", "medical record no.", "medical record no", "medical record",
    "chart number", "chart no.", "chart no", "mr number", "mr no.", "mr no", "mrn", "histno",
    "case number", "case no.", "case no", "caseno", "hospital number", "national id",
    "identity number", "id number", "telephone", "phone number", "phone", "mobile", "email",
    "e-mail", "contact person", "emergency contact", "next of kin", "attending physician",
    "ordering physician", "referring physician", "reporting physician", "requesting physician",
    "physician name", "physician", "doctor", "reported by", "verified by", "approved by",
    "collected by", "technologist", "technician", "nurse", "specimen number", "specimen no.",
    "specimen no", "specimen id", "sample number", "sample no.", "sample no", "sample id",
    "accession number", "accession no.", "accession no", "accession id", "accession",
    "report number", "report no.", "report no", "report id", "order number", "order no.",
    "order no", "order id", "request number", "request no.", "request no", "request id",
    "application number", "bed number", "bed no.", "bed no", "bed", "ward", "emr url",
)

# These labels delimit an administrative value; they are NEVER removed. Unknown
# headings also remain untouched. In particular, Name does not match Test Name.
_CLINICAL_LABELS = (
    "age", "sex", "gender", "test name", "analyte name", "drug name", "generic name",
    "specimen name", "specimen type", "specimen", "sample type", "sample", "test", "analyte",
    "result", "value", "unit", "units", "reference range", "reference", "method", "status",
    "collected at", "collection date", "collection time", "collected", "reported at",
    "report date", "report time", "reported", "received at", "received", "date", "time",
    "diagnosis", "impression", "findings", "interpretation", "comment", "comments", "dose",
    "route", "frequency", "WBC", "Hb", "Hgb", "PLT", "Na", "K", "Cl", "CRP", "Cr",
    "creatinine", "PBG", "ALA", "porphobilinogen", "porphyrins", "年齡", "年紀", "性別",
    "檢驗項目", "檢查項目", "檢驗名稱", "檢查名稱", "報告名稱", "檢體種類", "檢體別",
    "檢體", "標本種類", "標本", "檢驗結果", "結果", "數值", "單位", "參考值", "參考區間",
    "方法", "檢驗方法", "採檢日期", "採檢時間", "收件日期", "收件時間", "報告日期", "報告時間",
    "日期", "時間", "診斷", "結論", "備註", "劑量", "給藥途徑", "頻率",
)


def _normalized(label: str) -> str:
    return re.sub(r"[\s:：.#*_]+", "", label).casefold()


_IDENTITY = frozenset(map(_normalized, _IDENTITY_LABELS))
_CLINICAL = frozenset(map(_normalized, _CLINICAL_LABELS))
_ALL_LABELS = _IDENTITY | _CLINICAL


def _alternation(labels: tuple[str, ...]) -> str:
    return "|".join(re.escape(label).replace(r"\ ", r"[ \t]+") for label in sorted(labels, key=len, reverse=True))


_FIELD = re.compile(
    r"(?<![\w])(?P<label>" + _alternation(_IDENTITY_LABELS + _CLINICAL_LABELS)
    + r")[ \t]*[:：#][ \t]*",
    re.IGNORECASE,
)
_CLINICAL_BOUNDARY = re.compile(
    r"(?<![\w])(?P<label>" + _alternation(_CLINICAL_LABELS) + r")(?:[ \t]*[:：#]|[ \t]+(?=[\d<>=]))",
    re.IGNORECASE,
)
_CELL_SEPARATOR = re.compile(r"\t|[|｜;；]")
_PRIVATE_URL = re.compile(
    r"https?://[^\s<>\"']*[?&](?:histno|hisid|phistnum|adistno|caseno|mrn|patient_?id|"
    r"accession(?:_?(?:no|id))?|specimen_?(?:no|id)|report_?(?:no|id))=[^\s<>\"']+",
    re.IGNORECASE,
)


def _reliable_field_boundary(match: re.Match[str], line: str) -> bool:
    # A bare 'Test:' at the tail of an unknown multiword assay heading does not
    # establish where that heading began. Do not erase the preceding words.
    if _normalized(match["label"]) not in {
        "test", "testname", "analyte", "analytename", "specimen", "specimentype", "specimenname",
    }:
        return True
    return not line[:match.start()].strip() or bool(re.search(r"(?:[\t|｜;；]| {2,})[ ]*$", line[:match.start()]))


def _cells(line: str) -> tuple[str, list[tuple[int, int]]]:
    delimiter = "\t" if "\t" in line else ("|" if "|" in line else "｜")
    if delimiter not in line:
        return "", [(0, len(line))]
    positions = [match.start() for match in re.finditer(re.escape(delimiter), line)]
    boundaries = [-1, *positions, len(line)]
    return delimiter, [(a + 1, b) for a, b in zip(boundaries, boundaries[1:], strict=False)]


def _drop_columns(
    line: str, cells: list[tuple[int, int]], indices: set[int], offset: int
) -> list[HeaderSpan]:
    spans: list[HeaderSpan] = []
    # Remove each consecutive run with one adjacent delimiter. Keeping an empty
    # identifier column would make the clinical table misleading after cleanup.
    for index in sorted(indices):
        start, end = cells[index]
        if index + 1 < len(cells):
            end += 1
        elif index > 0 and index - 1 not in indices:
            start -= 1
        spans.append(HeaderSpan(offset + start, offset + min(end, len(line))))
    return spans


def identifier_spans(text: str) -> tuple[HeaderSpan, ...]:
    """Find explicitly labelled fields with reliable value boundaries.

    A free-text mention such as 'Unlabeled accession 12345678' is not a field.
    It remains available to the residual identifier scanner and manual review.
    """
    raw_lines = text.splitlines(keepends=True)
    lines = [line.rstrip("\r\n") for line in raw_lines]
    offsets: list[int] = []
    cursor = 0
    for line in raw_lines:
        offsets.append(cursor)
        cursor += len(line)
    spans: list[HeaderSpan] = []
    table_lines: set[int] = set()
    for i, line in enumerate(lines):
        if i in table_lines:
            continue
        delimiter, cells = _cells(line)
        labels = [_normalized(line[a:b]) for a, b in cells]
        identity_columns = {j for j, label in enumerate(labels) if label in _IDENTITY}
        recognized = sum(label in _ALL_LABELS for label in labels)
        all_known = recognized == sum(bool(label) for label in labels)
        # Allow rare/unknown test headings in a table. Distinguish a header from
        # an inline label/value row using the following row and field positions.
        next_delimiter, next_cells = _cells(lines[i + 1]) if i + 1 < len(lines) else ("", [])
        has_data_row = (
            next_delimiter == delimiter
            and len(next_cells) == len(cells)
            and all(_normalized(lines[i + 1][next_cells[j][0]:next_cells[j][1]]) not in _ALL_LABELS for j in identity_columns)
        )
        no_numeric_headings = all(not re.search(r"\d", line[a:b]) for a, b in cells)
        table_header = (all_known and recognized >= 2) or (recognized >= 2 and has_data_row and no_numeric_headings)
        if delimiter and identity_columns and table_header:
            for j in range(i, len(lines)):
                row_delimiter, row_cells = _cells(lines[j])
                if row_delimiter != delimiter or len(row_cells) != len(cells) or not lines[j].strip():
                    break
                if j > i and any(_normalized(lines[j][row_cells[k][0]:row_cells[k][1]]) in _ALL_LABELS for k in identity_columns):
                    break
                spans.extend(_drop_columns(lines[j], row_cells, identity_columns, offsets[j]))
                table_lines.add(j)

    for i, line in enumerate(lines):
        offset = offsets[i]
        if i not in table_lines:
            delimiter, cells = _cells(line)
            # Label/value TSV rows, including several pairs on one line.
            if delimiter:
                remove: set[int] = set()
                for j, (a, b) in enumerate(cells[:-1]):
                    if j in remove or _normalized(line[a:b]) not in _IDENTITY:
                        continue
                    value_start, value_end = cells[j + 1]
                    value = line[value_start:value_end].strip()
                    if value and _normalized(value) not in _ALL_LABELS:
                        remove.update((j, j + 1))
                spans.extend(_drop_columns(line, cells, remove, offset))

            # Vertical label/value layouts occur after HTML table extraction.
            if _normalized(line) in _IDENTITY:
                if i + 1 < len(lines) and lines[i + 1].strip():
                    value = lines[i + 1]
                    if _normalized(value) not in _ALL_LABELS and not _FIELD.search(value) and not _CLINICAL_BOUNDARY.search(value):
                        spans.append(HeaderSpan(offset, offsets[i + 1] + len(value)))

        fields = [match for match in _FIELD.finditer(line) if _reliable_field_boundary(match, line)]
        for field_index, match in enumerate(fields):
            label = _normalized(match["label"])
            if label not in _IDENTITY:
                continue
            # A protected compound heading may contain a shorter identity label
            # even when punctuation/spaces vary: never erase Test/Drug Name.
            prefix = line[:match.start()].rstrip().casefold()
            if label == "name" and re.search(r"(?:test|analyte|drug|generic|specimen|sample)\s*$", prefix):
                continue
            end = fields[field_index + 1].start() if field_index + 1 < len(fields) else len(line)
            boundary = _CELL_SEPARATOR.search(line, match.end(), end)
            if boundary:
                end = boundary.start()
            clinical_boundary = next((candidate for candidate in _CLINICAL_BOUNDARY.finditer(line, match.end(), end) if _reliable_field_boundary(candidate, line)), None)
            if clinical_boundary:
                end = clinical_boundary.start()
            value = line[match.end():end].strip()
            # An unknown key:value field adjacent without a delimiter cannot be
            # partitioned reliably. Leave it for manual review instead of eating
            # the unknown clinical field. Clock times are not field labels.
            if re.search(r"[^\d\W][\w /-]*[:：]", value):
                continue
            if value:
                spans.append(HeaderSpan(offset + match.start(), offset + end))
            else:
                # Label and value on consecutive lines with a trailing colon.
                if not line[match.end():].strip() and i + 1 < len(lines):
                    value = lines[i + 1]
                    if value.strip() and _normalized(value) not in _ALL_LABELS and not _FIELD.search(value) and not _CLINICAL_BOUNDARY.search(value):
                        spans.append(HeaderSpan(offset + match.start(), offsets[i + 1] + len(value)))

    spans.extend(HeaderSpan(match.start(), match.end(), "identifier_in_url") for match in _PRIVATE_URL.finditer(text))
    return _merged(spans)


def unresolved_labels(text: str) -> tuple[HeaderSpan, ...]:
    """Residual labelled fields that could not be safely partitioned."""
    result: list[HeaderSpan] = []
    for match in _FIELD.finditer(text):
        if _normalized(match["label"]) not in _IDENTITY:
            continue
        prefix = text[:match.start()].rsplit("\n", 1)[-1].rstrip().casefold()
        if _normalized(match["label"]) == "name" and re.search(r"(?:test|analyte|drug|generic|specimen|sample)\s*$", prefix):
            continue
        rest = text[match.end():].split("\n", 1)[0]
        if rest.strip():
            # Review must include the unresolved value. Masking just the label
            # would leave a bare name that a later residual scan could not find.
            end = match.end() + len(rest)
            separator = _CELL_SEPARATOR.search(text, match.end(), end)
            if separator:
                end = separator.start()
            result.append(HeaderSpan(match.start(), end, "labeled_identifier"))
    return tuple(result)


def _merged(spans: list[HeaderSpan]) -> tuple[HeaderSpan, ...]:
    merged: list[HeaderSpan] = []
    for span in sorted(spans, key=lambda item: (item.start, item.end)):
        if merged and span.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = HeaderSpan(previous.start, max(previous.end, span.end), previous.kind)
        else:
            merged.append(span)
    return tuple(merged)
