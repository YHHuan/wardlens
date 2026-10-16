from __future__ import annotations

import calendar
import re
from datetime import date

from wardlens.models import EMR_TIMEZONE, PatientBundle, SourceRecord

TRUNCATION_MARKER = "[SOURCE TRUNCATED: omitted content; trend/coverage is incomplete]"


def months_before(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def source_dates(text: str) -> list[date]:
    dates: set[date] = set()
    for match in re.finditer(r"(?<!\d)(\d{2,4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", text):
        year, month, day = map(int, match.groups())
        if year == 0:
            continue
        if len(match[1]) < 4:
            year += 1911
        try:
            dates.add(date(year, month, day))
        except ValueError:
            continue
    return sorted(dates)


def bounded_source(text: str, limit: int) -> str:
    """Keep both ends, never silently imply an excerpt is the full source."""
    if len(text) <= limit:
        return text
    room = max(0, limit - len(TRUNCATION_MARKER) - 2)
    head = room * 2 // 3
    tail = room - head
    return text[:head] + "\n" + TRUNCATION_MARKER + "\n" + (text[-tail:] if tail else "")


def coverage_summary(record: SourceRecord, *, relative_to: date | None = None) -> str:
    dates = source_dates(record.content)
    if not dates and record.source_type == "report" and record.observed_at is not None:
        dates = [record.observed_at.astimezone(EMR_TIMEZONE).date()]
    if not dates:
        return "未辨識日期，無法確認歷史涵蓋範圍"

    def display(day: date) -> str:
        if relative_to is None:
            return day.isoformat()
        delta = (day - relative_to).days
        return "D0" if delta == 0 else f"D{delta:+d}"

    result = f"辨識 {len(dates)} 個日期：{display(dates[0])} → {display(dates[-1])}"
    if len(dates) == 1:
        result += "；只有單日，不能判斷 longitudinal trend"
    if TRUNCATION_MARKER in record.content:
        result += "；資料已截短"
    return result + "（不是完整性保證）"


def window_description(bundle: PatientBundle, *, relative: bool = False) -> str:
    if bundle.lookback_months is None:
        return "歷史查詢範圍未建立"
    end = bundle.fetched_at.astimezone(EMR_TIMEZONE).date()
    start = months_before(end, bundle.lookback_months)
    span = f"D{(start - end).days:+d} → D0" if relative else f"{start} → {end}"
    return f"要求回溯近 {bundle.lookback_months} 個月：{span}；實際涵蓋以下列來源為準"
