"""Local, deterministic presentation of clinical tables; no model-derived values."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from datetime import datetime

from wardlens.models import EMR_TIMEZONE, PatientBundle, SourceRecord
from wardlens.security.deidentify import clean_clinical_headers
from wardlens.services.history import TRUNCATION_MARKER, normalize_result_date
from wardlens.services.summary import admission_top_items, recent_reports

_MEASURES = (
    ("WBC", "cbc", ("white blood cell", "white blood cells", "白血球")),
    ("Hb", "cbc", ("HGB", "hemoglobin", "血紅素")),
    ("PLT", "cbc", ("platelet", "platelets", "血小板")),
    ("Na", "chemistry", ("sodium",)),
    ("K", "chemistry", ("potassium",)),
    ("Cr", "chemistry", ("CREA", "creatinine",)),
    ("BUN", "chemistry", ("blood urea nitrogen",)),
    ("CRP", "chemistry", ("C-reactive protein",)),
    ("ALT", "chemistry", ()), ("AST", "chemistry", ()),
    ("Bilirubin", "chemistry", ("BILIT", "total bilirubin")),
    ("SEG", "differential", ("NEUT", "neutrophil", "neutrophils")),
    ("BAND", "differential", ("bands",)),
    ("LYM", "differential", ("lymphocyte", "lymphocytes")),
    ("MONO", "differential", ("monocyte", "monocytes")),
    ("EOS", "differential", ("eosinophil", "eosinophils")),
    ("BASO", "differential", ("basophil", "basophils")),
    ("T", "vitals", ("temperature", "體溫")),
    ("BP", "vitals", ("blood pressure", "血壓")),
    ("SBP", "vitals", ("收縮壓",)), ("DBP", "vitals", ("舒張壓",)),
    ("HR", "vitals", ("P", "pulse", "heart rate", "心跳", "心率", "脈搏")),
    ("RR", "vitals", ("R", "respiratory rate", "呼吸")),
    ("SpO₂", "vitals", ("SpO2", "oxygen saturation", "血氧", "血氧飽和度")),
)


def _key(value: str) -> str:
    return re.sub(r"[\s_：:]", "", value).casefold()


_ALIASES = {
    _key(alias): (label, group, order)
    for order, (label, group, aliases) in enumerate(_MEASURES)
    for alias in (label, *aliases)
}
_DATE_LABELS = {_key(item) for item in ("Date", "Datetime", "日期", "日期／時間", "採檢日期", "採檢時間", "量測時間", "檢驗日期", "檢驗時間", "報告日期", "報告時間", "Collection date", "Collection time", "Report date", "Report time")}
_TIME_LABELS = {_key(item) for item in ("Time", "時間", "時分")}
_NAME_LABELS = {_key(item) for item in ("Test", "Test name", "Item", "Analyte", "項目", "項目名稱", "檢驗項目", "檢驗名稱")}
_VALUE_LABELS = {_key(item) for item in ("Value", "Result", "結果", "數值", "檢驗值", "檢驗結果")}
_UNIT_LABELS = {_key(item) for item in ("Unit", "Units", "單位")}
_SPECIMEN_LABELS = {_key(item) for item in ("Specimen", "Specimen type", "Sample type", "檢體", "檢體種類", "檢體類別")}
_METHOD_LABELS = {_key(item) for item in ("Method", "Assay method", "方法", "檢驗方法")}
_REFERENCE_LABELS = {_key(item) for item in ("Reference", "Reference range", "參考值", "參考範圍")}
_UNKNOWN_DATE_LABELS = {_key(item) for item in (
    "unknown", "unknown date", "unknown time", "undated", "date unknown", "time unknown",
    "日期不明", "時間不明", "日期未明", "時間未明", "日期未知", "時間未知", "未記載日期", "未記載時間",
)}
_IGNORE_LABELS = _DATE_LABELS | _TIME_LABELS | _NAME_LABELS | _UNIT_LABELS | _SPECIMEN_LABELS | _METHOD_LABELS | _REFERENCE_LABELS | {
    _key(item) for item in ("Reference", "Reference range", "參考值", "參考範圍", "Code", "代碼", "檢體", "Specimen", "狀態", "Status", "備註")
}
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?"
_VALUE = re.compile(rf"^\s*(?P<comparison>[<>≤≥]=?)?\s*(?P<number>{_NUMBER})(?:\s+(?P<suffix>.*))?\s*$")
_UNIT = re.compile(r"^(?:%|°?[CF]|℃|℉|mmHg|bpm|kg|cm|/min|(?:10(?:\^|\*\*)?[369³⁶⁹]|[A-Za-zµμ]+)[/·][A-Za-zµμ\d³]+)$")
_CLOCK = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")


def _parse_time(value: str) -> datetime | None:
    # No year inference for MM/DD, and no restoration of deidentified D± dates.
    normalized = normalize_result_date(value.strip())
    if not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[ T])", normalized):
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.replace(tzinfo=EMR_TIMEZONE) if parsed.tzinfo is None else parsed


def _label_and_unit(raw: str) -> tuple[str, str, str, int]:
    label, unit = raw.strip(), ""
    bracket = re.fullmatch(r"(.+?)\s*[\[(]([^\])]+)[\])]", label)
    if bracket and _UNIT.fullmatch(bracket[2].strip()):
        label, unit = bracket[1].strip(), bracket[2].strip()
    else:
        parts = label.rsplit(None, 1)
        if len(parts) == 2 and _UNIT.fullmatch(parts[1]):
            label, unit = parts
    canonical, group, order = _ALIASES.get(_key(label), (label, "other", len(_MEASURES)))
    return canonical, group, unit, order


def _point(label: str, unit: str, value: str, time: str, source_id: str, **metadata) -> dict:
    canonical, group, header_unit, order = _label_and_unit(label)
    unit = unit.strip() or header_unit
    match = _VALUE.fullmatch(value)
    number = None
    if match:
        suffix = (match["suffix"] or "").strip()
        explicit_unit = re.sub(r"\s+[HL*]$", "", suffix)
        if _UNIT.fullmatch(explicit_unit):
            unit = explicit_unit
        elif suffix not in {"", "H", "L", "*"}:
            match = None
        if match and not match["comparison"]:
            number = float(match["number"].replace(",", ""))
            if not float("-inf") < number < float("inf"):
                number = None
    parsed = _parse_time(time)
    precision = "unknown"
    if parsed:
        precision = "second" if re.search(r"\d:\d{2}:\d{2}", time) else "minute" if re.search(r"\d:\d{2}", time) else "day"
    return {
        "label": canonical, "group": group, "unit": unit, "order": order,
        "value": value, "time": time, "source_id": source_id,
        "timestamp": parsed.isoformat() if parsed else "", "time_precision": precision, "numeric": number,
        "specimen": metadata.get("specimen", ""), "method": metadata.get("method", ""),
        "reference": metadata.get("reference", ""), "time_basis": metadata.get("time_basis", "observation"),
    }


def _metadata(header: list[str], row: list[str], defaults: dict | None = None) -> dict:
    result = dict(defaults or {})
    for field, labels in (("specimen", _SPECIMEN_LABELS), ("method", _METHOD_LABELS), ("reference", _REFERENCE_LABELS)):
        column = next((i for i, cell in enumerate(header) if _key(cell) in labels), None)
        if column is not None:
            result[field] = row[column].strip()
    return result


def _time_basis(label: str) -> str:
    key = _key(label)
    if "report" in key or "報告" in key:
        return "report"
    if "collection" in key or "採檢" in key:
        return "collection"
    return "observation"


def _date_rows(rows: list[list[str]], source_id: str, defaults: dict | None = None) -> tuple[list[dict], list[str]]:
    for header_index, header in enumerate(rows[:12]):
        keys = [_key(cell) for cell in header]
        dates = [index for index, key in enumerate(keys) if key in _DATE_LABELS]
        if not dates:
            continue
        # Prefer collection over reporting dates. A separate collection clock is
        # paired only with a date-only value, never with an already precise time.
        collection = [i for i in dates if _time_basis(header[i]) == "collection"]
        choices = collection or [i for i in dates if _time_basis(header[i]) == "observation"] or dates
        date_column = next((i for i in choices if "date" in keys[i] or "日期" in keys[i]), choices[0])
        time_column = next((i for i, key in enumerate(keys) if key in _TIME_LABELS), None)
        paired_clocks = [i for i in choices if i != date_column and ("time" in keys[i] or "時間" in keys[i])]
        if paired_clocks:
            time_column = paired_clocks[0]
        name_column = next((i for i, key in enumerate(keys) if key in _NAME_LABELS), None)
        value_column = next((i for i, key in enumerate(keys) if key in _VALUE_LABELS), None)
        unit_column = next((i for i, key in enumerate(keys) if key in _UNIT_LABELS), None)
        units: dict[int, str] = {}
        points: list[dict] = []
        warnings: list[str] = []
        for row in rows[header_index + 1:]:
            if len(row) != len(header):
                warnings.append("有檢驗列欄數不符，未推測欄位對應；請展開原文。")
                continue
            if _key(row[date_column]) in _UNIT_LABELS:
                units = dict(enumerate(row))
                continue
            if [_key(cell) for cell in row] == keys:
                continue
            time = row[date_column]
            if (time_column is not None and _CLOCK.fullmatch(row[time_column])
                    and _parse_time(time) and not re.search(r"\d:\d{2}", time)):
                time += " " + row[time_column]
            metadata = _metadata(header, row, defaults)
            metadata["time_basis"] = _time_basis(header[date_column])
            if name_column is not None and value_column is not None:
                unit = row[unit_column] if unit_column is not None else ""
                points.append(_point(row[name_column], unit, row[value_column], time, source_id, **metadata))
                continue
            for index, label in enumerate(header):
                if not label or keys[index] in _IGNORE_LABELS:
                    continue
                points.append(_point(label, units.get(index, ""), row[index], time, source_id, **metadata))
        return points, warnings
    return [], []


def _date_columns(rows: list[list[str]], source_id: str, defaults: dict | None = None) -> tuple[list[dict], list[str]]:
    for header_index, header in enumerate(rows[:12]):
        known_dates = {index: cell for index, cell in enumerate(header) if _parse_time(cell)}
        unknown_dates = {index: cell for index, cell in enumerate(header) if _key(cell) in _UNKNOWN_DATE_LABELS}
        named_column = next((i for i, cell in enumerate(header) if _key(cell) in _NAME_LABELS), None)
        if not known_dates and not (unknown_dates and named_column is not None):
            continue
        dates = {**known_dates, **unknown_dates}
        name_column = named_column if named_column is not None else 0
        if name_column in dates:
            continue
        unit_column = next((i for i, cell in enumerate(header) if _key(cell) in _UNIT_LABELS), None)
        warnings: list[str] = []
        if unknown_dates:
            warnings.append("有日期／時間未明的檢驗欄，數值另列為時間未明；請展開原文核對。")
        unparsed = [cell or "（空白）" for index, cell in enumerate(header)
                    if index not in dates and index != name_column and _key(cell) not in _IGNORE_LABELS]
        if unparsed:
            labels = "、".join(label[:60] for label in unparsed[:4])
            warnings.append(f"有欄位標頭無法判定為日期或已知附註欄（{labels}），未推測其數值與日期；請展開原文核對未呈現欄位。")
        data_index = header_index + 1
        if data_index < len(rows) and len(rows[data_index]) == len(header):
            clock_row = rows[data_index]
            if known_dates and all(_CLOCK.fullmatch(clock_row[index]) for index in known_dates):
                dates = {index: time + (" " + clock_row[index] if _CLOCK.fullmatch(clock_row[index]) else "")
                         for index, time in dates.items()}
                data_index += 1
        points: list[dict] = []
        for row in rows[data_index:]:
            if len(row) != len(header):
                warnings.append("有檢驗列欄數不符，未推測欄位對應；請展開原文。")
                continue
            label = row[name_column]
            if not label or _key(label) in _IGNORE_LABELS:
                continue
            unit = row[unit_column] if unit_column is not None else ""
            metadata = _metadata(header, row, defaults)
            metadata["time_basis"] = "observation"
            points.extend(_point(label, unit, row[index], time, source_id, **metadata) for index, time in dates.items())
        return points, warnings
    return [], []


def _span(points: list[dict]) -> str:
    times = [datetime.fromisoformat(point["timestamp"]) for point in points if point["timestamp"]]
    if not times:
        return "時間未建立"
    seconds = (max(times) - min(times)).total_seconds()
    if seconds == 0:
        return "單一時間點"
    amount, unit = (seconds / 86400, "天") if seconds >= 86400 else (seconds / 3600, "小時")
    if seconds < 3600:
        amount, unit = seconds / 60, "分鐘"
    return f"跨 {amount:.1f}".removesuffix(".0") + f" {unit}"


def _series(points: list[dict]) -> list[dict]:
    groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for point in points:
        if point["label"] and _key(point["label"]) not in _IGNORE_LABELS:
            groups[tuple(point[key] for key in ("label", "unit", "specimen", "method", "time_basis"))].append(point)
    output = []
    for (label, unit, specimen, method, time_basis), items in groups.items():
        # Unknown observations remain available, but never become a latest value
        # or displace known observations from the compact chronological preview.
        known = sorted((item for item in items if item["timestamp"]),
                       key=lambda item: datetime.fromisoformat(item["timestamp"]))
        unknown = [item for item in items if not item["timestamp"]]
        items = known + unknown
        numeric = all(item["numeric"] is not None for item in items)
        collisions: dict[str, set[str]] = defaultdict(set)
        for item in items:
            if item["timestamp"]:
                collisions[item["timestamp"]].add(item["value"])
        conflict = any(len(values) > 1 for values in collisions.values())
        by_day: dict = defaultdict(list)
        for item in known:
            day = datetime.fromisoformat(item["timestamp"]).astimezone(EMR_TIMEZONE).date()
            by_day[day].append(item)
        mixed_precision_days = {
            day for day, observations in by_day.items()
            if any(item["time_precision"] == "day" for item in observations)
            and any(item["time_precision"] != "day" for item in observations)
        }
        ambiguous_latest_days = {
            day for day in mixed_precision_days if len({item["value"] for item in by_day[day]}) > 1
        }
        has_time_conflict = conflict or bool(mixed_precision_days)
        notes = [f"已知時間 {len(known)} 筆／未知時間 {len(unknown)} 筆"]
        if time_basis == "report":
            notes.append("按報告時間排列；不代表採檢時間")
        if not unit:
            notes.append("單位未提供")
        if unknown:
            notes.append("含時間未辨識資料，無法建立完整時序")
        if not numeric:
            notes.append("含缺值、比較符或非純數值，保留原文")
        if conflict:
            notes.append("相同日期／時間標記有不同數值，請核對量測時間與來源")
        if mixed_precision_days:
            notes.append("同日部分紀錄僅有日期，無法確定先後；僅依日期分組，不以午夜推定量測時間")
        if len(items) == 1:
            notes.append("僅一筆，不能判斷趨勢")
        if len(known) > 5:
            notes.append(f"先顯示最近 5／{len(known)} 筆已知時間資料，完整資料可展開")
        values = [{key: item[key] for key in ("value", "time", "source_id", "timestamp", "time_precision", "numeric", "reference")} for item in items]
        known_values, unknown_values = values[:len(known)], values[len(known):]
        latest = known_values[-1] if known_values else None
        latest_day = datetime.fromisoformat(latest["timestamp"]).astimezone(EMR_TIMEZONE).date() if latest else None
        if latest and (len(collisions[latest["timestamp"]]) > 1 or latest_day in ambiguous_latest_days):
            # A tied timestamp with conflicting results cannot identify one
            # current value. Keep both original results in the source details.
            latest = None
        span = _span(known)
        if mixed_precision_days:
            days = (max(by_day) - min(by_day)).days
            span = f"跨 {days} 天（依日期，實際間隔未明）" if days else "同日，先後未明"
        if known and unknown:
            span += "（依已知時間計）"
        output.append({
            "label": label, "unit": unit, "group": items[0]["group"],
            "specimen": specimen, "method": method, "time_basis": time_basis,
            "reference": " / ".join(dict.fromkeys(item["reference"] for item in items if item["reference"])),
            "values": known_values[-5:], "all_values": values, "point_count": len(values),
            "latest_known": latest, "unknown_values": unknown_values,
            "chronology_complete": not unknown and not has_time_conflict,
            "has_time_conflict": has_time_conflict,
            "span": span, "note": "；".join(notes),
            "chartable": bool(unit and not unknown and numeric and len(items) > 1 and not has_time_conflict),
            "_order": items[0]["order"],
        })
    output.sort(key=lambda item: (item["_order"], item["label"], item["unit"]))
    for item in output:
        del item["_order"]
    return output


def _is_header(cells: list[str]) -> bool:
    keys = {_key(cell) for cell in cells}
    return bool(keys & _DATE_LABELS or (keys & _NAME_LABELS and (keys & _VALUE_LABELS or any(_parse_time(cell) for cell in cells))))


def _blocks(text: str) -> list[tuple[str, list[str]]]:
    """Separate narrative and independent tables without merging their columns."""
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if not line.strip():
            if blocks and blocks[-1][1]:
                blocks.append(("text", []))
            continue
        kind = "table" if "\t" in line else "text"
        cells = [cell.strip() for cell in line.split("\t")]
        # An explicit next header starts another table, even without a blank line.
        if (not blocks or blocks[-1][0] != kind
                or (kind == "table" and blocks[-1][1] and _is_header(cells))):
            blocks.append((kind, []))
        blocks[-1][1].append(line)
    return [(kind, lines) for kind, lines in blocks if lines]


def clinical_sections(text: str) -> list[dict]:
    sections = []
    for kind, lines in _blocks(text):
        rows = [[cell.strip() for cell in line.split("\t")] for line in lines]
        if kind == "table" and len(rows) > 1 and len({len(row) for row in rows}) == 1:
            has_header = _is_header(rows[0]) or any(_key(cell) in _IGNORE_LABELS for cell in rows[0])
            sections.append({"kind": "table", "headers": rows[0] if has_header else [f"欄 {i + 1}" for i in range(len(rows[0]))],
                             "rows": rows[1:] if has_header else rows, "generic_headers": not has_header})
        else:
            sections.append({"kind": "text", "text": "\n".join(lines)})
    return sections


def _record_time(record: SourceRecord) -> str:
    if not record.observed_at:
        return ""
    if record.observed_precision == "day":
        return record.observed_at.astimezone(EMR_TIMEZONE).date().isoformat()
    return record.observed_at.isoformat(timespec="minutes" if record.observed_precision == "minute" else "seconds")


def source_view(source_id: str, record: SourceRecord, *, include_content: bool = True) -> dict:
    cleaned, _ = clean_clinical_headers(record.content)
    title, _ = clean_clinical_headers(record.title)
    return {"id": source_id, "title": title or "臨床來源", "type": record.source_type,
            "time": _record_time(record),
            "status": record.status, "truncated": TRUNCATION_MARKER in record.content,
            "deferred": not include_content, "preview": cleaned[:240],
            "content": record.content if include_content else "",
            "sections": clinical_sections(cleaned) if include_content else []}


def _report_defaults(text: str, record: SourceRecord) -> tuple[dict, str]:
    fields: dict[str, set[str]] = defaultdict(set)
    times: set[str] = set()
    clocks: set[str] = set()
    pairs: list[tuple[str, str]] = []
    lines = text.splitlines()
    meta_labels = _SPECIMEN_LABELS | _METHOD_LABELS | _DATE_LABELS
    consumed: set[int] = set()
    for i, line in enumerate(lines):
        if i in consumed:
            continue
        columns = [cell.strip() for cell in line.split("\t")]
        if len(columns) > 1 and all(_key(cell) in meta_labels for cell in columns):
            if i + 1 < len(lines):
                values = [cell.strip() for cell in lines[i + 1].split("\t")]
                if len(values) == len(columns) and not any(_key(value) in meta_labels for value in values):
                    pairs.extend(zip(columns, values, strict=True))
                    consumed.add(i + 1)
            continue
        cells = re.split(r"\t+|\s*[:：]\s*", line.strip(), maxsplit=1)
        if len(cells) == 2 and "\t" not in cells[1] and _key(cells[1]) not in meta_labels:
            pairs.append((cells[0], cells[1].strip()))
    for label, value in pairs:
        key = _key(label)
        for field, labels in (("specimen", _SPECIMEN_LABELS), ("method", _METHOD_LABELS)):
            if key in labels:
                fields[field].add(value)
        if key in _DATE_LABELS and _time_basis(label) == "collection":
            if _parse_time(value):
                times.add(value)
            elif _CLOCK.fullmatch(value):
                clocks.add(value)
    # Do not arbitrarily choose between multiple conflicting specimen/time headers.
    metadata = {field: next(iter(values)) if len(values) == 1 else " | ".join(sorted(values))
                for field, values in fields.items()}
    if len(times) == 1:
        time = next(iter(times))
        if len(clocks) == 1 and not re.search(r"\d:\d{2}", time):
            time += " " + next(iter(clocks))
        return dict(metadata, time_basis="collection"), time
    if record.observed_at:
        return dict(metadata, time_basis="report"), _record_time(record)
    return dict(metadata, time_basis="unknown"), ""


def _report_results(rows: list[list[str]], source_id: str, defaults: dict, time: str) -> tuple[list[dict], list[str]]:
    if not rows:
        return [], []
    keys = [_key(cell) for cell in rows[0]]
    name = next((i for i, key in enumerate(keys) if key in _NAME_LABELS), None)
    value = next((i for i, key in enumerate(keys) if key in _VALUE_LABELS), None)
    unit = next((i for i, key in enumerate(keys) if key in _UNIT_LABELS), None)
    if name is None or value is None or set(keys) & _DATE_LABELS:
        return [], []
    points, warnings = [], []
    for row in rows[1:]:
        if len(row) != len(keys):
            warnings.append("有檢驗列欄數不符，未推測欄位對應；請展開原文。")
            continue
        points.append(_point(row[name], row[unit] if unit is not None else "", row[value], time, source_id,
                             **_metadata(rows[0], row, defaults)))
    return points, warnings


def build_rounding_view(bundle: PatientBundle, *, include_content: bool = True) -> dict:
    """Return JSON-safe local view data; exact source text remains available.

    `values` holds up to five recent points with known times; `unknown_values`
    keeps observations that cannot be ordered and `all_values` retains both.
    `latest_known` is null if no time is known or the latest value is ambiguous.
    `has_time_conflict` covers tied values and same-day mixed date/time precision;
    do not imply an order with arrows in these rows. `chronology_complete` also
    excludes unknown times.
    Plot only when `chartable` is true, using `timestamp`, not equal gaps.
    Show original `time` in labels; day-only timestamps are coordinates, not an
    assertion that sampling occurred at midnight (`time_precision` records this).
    `numeric` is null for missing/comparison/non-numeric results. No reference
    ranges, units, abnormal flags or clinical interpretations are fabricated.
    """
    cleaned_records = [replace(record, content=clean_clinical_headers(record.content)[0]) for record in bundle.records]
    local_bundle = replace(bundle, records=cleaned_records)
    kind, todos = admission_top_items(local_bundle)
    warnings = list(bundle.warnings)
    sources, laboratory, vital_signs, medications = [], [], [], []
    for source_id, record in bundle.source_map():
        sources.append(source_view(source_id, record, include_content=include_content))
        cleaned = cleaned_records[len(sources) - 1].content
        if record.source_type in {"medication", "medications"}:
            medications.extend(line.strip() for line in cleaned.splitlines() if line.strip())
        if not (record.source_type.startswith("lab_") or record.source_type in {"vital_signs", "report"}):
            continue
        defaults, fallback_time = _report_defaults("", record)
        points, table_warnings = [], []
        for block_kind, lines in _blocks(cleaned):
            block_defaults, block_time = _report_defaults("\n".join(lines), record)
            clinical_fields = {key: value for key, value in block_defaults.items() if key != "time_basis"}
            if clinical_fields:
                if "specimen" in clinical_fields:
                    defaults, fallback_time = _report_defaults("", record)
                defaults.update(clinical_fields)
            if block_defaults["time_basis"] == "collection":
                defaults["time_basis"], fallback_time = "collection", block_time
            if block_kind != "table":
                continue
            rows = [[cell.strip() for cell in line.split("\t")] for line in lines]
            parsed, issues = _date_rows(rows, source_id, defaults)
            if not parsed:
                parsed, more = _date_columns(rows, source_id, defaults)
                issues.extend(more)
            if not parsed and record.source_type == "report":
                parsed, more = _report_results(rows, source_id, defaults, fallback_time)
                issues.extend(more)
            points.extend(parsed)
            table_warnings.extend(issues)
        warnings.extend(f"{record.title}：{warning}" for warning in table_warnings)
        if not points and record.source_type != "report":
            warnings.append(f"{record.title}：未辨識可靠的日期／數值表格，完整內容保留於來源。")
        if TRUNCATION_MARKER in record.content:
            warnings.append(f"{record.title}：原始資料已截短，不能視為完整趨勢。")
        (vital_signs if record.source_type == "vital_signs" else laboratory).extend(points)
    if len(medications) > 12:
        warnings.append("用藥先顯示前 12 行，請展開來源核對完整處方與狀態。")
    return {
        "todos": todos, "todo_kind": kind, "reports": recent_reports(local_bundle),
        "report_details": [source for source in sources if source["type"] == "report"],
        "coverage": dict(bundle.coverage),
        "medications": medications[:12], "vitals": _series(vital_signs),
        "trends": _series(laboratory), "warnings": list(dict.fromkeys(warnings)),
        "sources": sources,
    }
