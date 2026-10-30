"""Local, deterministic presentation of clinical tables; no model-derived values."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from wardlens.models import EMR_TIMEZONE, PatientBundle
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
_DATE_LABELS = {_key(item) for item in ("Date", "Datetime", "日期", "日期／時間", "採檢日期", "採檢時間", "量測時間", "檢驗日期", "檢驗時間", "報告日期")}
_TIME_LABELS = {_key(item) for item in ("Time", "時間", "時分")}
_NAME_LABELS = {_key(item) for item in ("Test", "Item", "項目", "檢驗項目", "檢驗名稱")}
_VALUE_LABELS = {_key(item) for item in ("Value", "Result", "結果", "數值", "檢驗值", "檢驗結果")}
_UNIT_LABELS = {_key(item) for item in ("Unit", "Units", "單位")}
_IGNORE_LABELS = _DATE_LABELS | _TIME_LABELS | _NAME_LABELS | _UNIT_LABELS | {
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


def _point(label: str, unit: str, value: str, time: str, source_id: str) -> dict:
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
    }


def _date_rows(rows: list[list[str]], source_id: str) -> tuple[list[dict], list[str]]:
    for header_index, header in enumerate(rows[:12]):
        keys = [_key(cell) for cell in header]
        dates = [index for index, key in enumerate(keys) if key in _DATE_LABELS]
        if len(dates) != 1:
            continue
        date_column = dates[0]
        time_column = next((i for i, key in enumerate(keys) if key in _TIME_LABELS), None)
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
            if time_column is not None and row[time_column]:
                time += " " + row[time_column]
            if name_column is not None and value_column is not None:
                unit = row[unit_column] if unit_column is not None else ""
                points.append(_point(row[name_column], unit, row[value_column], time, source_id))
                continue
            for index, label in enumerate(header):
                if not label or keys[index] in _IGNORE_LABELS:
                    continue
                points.append(_point(label, units.get(index, ""), row[index], time, source_id))
        return points, warnings
    return [], []


def _date_columns(rows: list[list[str]], source_id: str) -> tuple[list[dict], list[str]]:
    for header_index, header in enumerate(rows[:12]):
        dates = {index: cell for index, cell in enumerate(header) if _parse_time(cell)}
        if not dates:
            continue
        name_column = next((i for i, cell in enumerate(header) if _key(cell) in _NAME_LABELS), 0)
        if name_column in dates:
            continue
        unit_column = next((i for i, cell in enumerate(header) if _key(cell) in _UNIT_LABELS), None)
        data_index = header_index + 1
        if data_index < len(rows) and len(rows[data_index]) == len(header):
            clock_row = rows[data_index]
            if all(_CLOCK.fullmatch(clock_row[index]) for index in dates):
                dates = {index: time + " " + clock_row[index] for index, time in dates.items()}
                data_index += 1
        points: list[dict] = []
        warnings: list[str] = []
        for row in rows[data_index:]:
            if len(row) != len(header):
                warnings.append("有檢驗列欄數不符，未推測欄位對應；請展開原文。")
                continue
            label = row[name_column]
            if not label or _key(label) in _IGNORE_LABELS:
                continue
            unit = row[unit_column] if unit_column is not None else ""
            points.extend(_point(label, unit, row[index], time, source_id) for index, time in dates.items())
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
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for point in points:
        if point["label"] and _key(point["label"]) not in _IGNORE_LABELS:
            groups[(point["label"], point["unit"])].append(point)
    output = []
    for (label, unit), items in groups.items():
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
        notes = [f"已知時間 {len(known)} 筆／未知時間 {len(unknown)} 筆"]
        if not unit:
            notes.append("單位未提供")
        if unknown:
            notes.append("含時間未辨識資料，無法建立完整時序")
        if not numeric:
            notes.append("含缺值、比較符或非純數值，保留原文")
        if conflict:
            notes.append("相同日期／時間標記有不同數值，請核對量測時間與來源")
        if len(items) == 1:
            notes.append("僅一筆，不能判斷趨勢")
        if len(known) > 5:
            notes.append(f"先顯示最近 5／{len(known)} 筆已知時間資料，完整資料可展開")
        values = [{key: item[key] for key in ("value", "time", "source_id", "timestamp", "time_precision", "numeric")} for item in items]
        known_values, unknown_values = values[:len(known)], values[len(known):]
        latest = known_values[-1] if known_values else None
        if latest and len(collisions[latest["timestamp"]]) > 1:
            # A tied timestamp with conflicting results cannot identify one
            # current value. Keep both original results in the source details.
            latest = None
        span = _span(known)
        if known and unknown:
            span += "（依已知時間計）"
        output.append({
            "label": label, "unit": unit, "group": items[0]["group"],
            "values": known_values[-5:], "all_values": values, "point_count": len(values),
            "latest_known": latest, "unknown_values": unknown_values,
            "chronology_complete": not unknown and not conflict,
            "span": span, "note": "；".join(notes),
            "chartable": bool(unit and not unknown and numeric and len(items) > 1 and not conflict),
            "_order": items[0]["order"],
        })
    output.sort(key=lambda item: (item["_order"], item["label"], item["unit"]))
    for item in output:
        del item["_order"]
    return output


def build_rounding_view(bundle: PatientBundle) -> dict:
    """Return JSON-safe local view data; exact source text remains available.

    `values` holds up to five recent points with known times; `unknown_values`
    keeps observations that cannot be ordered and `all_values` retains both.
    `latest_known` is null if no time is known or the latest timestamp conflicts.
    `chronology_complete` excludes unknown times and conflicting timestamps.
    Plot only when `chartable` is true, using `timestamp`, not equal gaps.
    Show original `time` in labels; day-only timestamps are coordinates, not an
    assertion that sampling occurred at midnight (`time_precision` records this).
    `numeric` is null for missing/comparison/non-numeric results. No reference
    ranges, units, abnormal flags or clinical interpretations are fabricated.
    """
    kind, todos = admission_top_items(bundle)
    warnings = list(bundle.warnings)
    sources, laboratory, vital_signs, medications = [], [], [], []
    for source_id, record in bundle.source_map():
        sources.append({
            "id": source_id, "title": record.title, "type": record.source_type,
            "time": record.observed_at.isoformat() if record.observed_at else "",
            "content": record.content, "status": record.status,
        })
        if record.source_type in {"medication", "medications"}:
            medications.extend(line.strip() for line in record.content.splitlines() if line.strip())
        if not (record.source_type.startswith("lab_") or record.source_type == "vital_signs"):
            continue
        rows = [[cell.strip() for cell in line.split("\t")] for line in record.content.splitlines() if "\t" in line]
        points, table_warnings = _date_rows(rows, source_id)
        if not points:
            points, more_warnings = _date_columns(rows, source_id)
            table_warnings.extend(more_warnings)
        warnings.extend(f"{record.title}：{warning}" for warning in table_warnings)
        if not points:
            warnings.append(f"{record.title}：未辨識可靠的日期／數值表格，完整內容保留於來源。")
        if TRUNCATION_MARKER in record.content:
            warnings.append(f"{record.title}：原始資料已截短，不能視為完整趨勢。")
        (vital_signs if record.source_type == "vital_signs" else laboratory).extend(points)
    if len(medications) > 12:
        warnings.append("用藥先顯示前 12 行，請展開來源核對完整處方與狀態。")
    return {
        "todos": todos, "todo_kind": kind, "reports": recent_reports(bundle),
        "medications": medications[:12], "vitals": _series(vital_signs),
        "trends": _series(laboratory), "warnings": list(dict.fromkeys(warnings)),
        "sources": sources,
    }
