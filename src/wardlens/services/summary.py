from __future__ import annotations

import re

from wardlens.models import EMR_TIMEZONE, PatientBundle, SourceRecord
from wardlens.services.history import coverage_summary, window_description

_todo_pattern = re.compile(
    r"(?i)^(?:(?:todo|to[- ]?do|pending|follow(?:\s|-)?up|recheck)\b|待辦|待追蹤|待確認|追蹤|確認|補抽|安排|複查)"
)
_todo_heading = re.compile(r"(?i)^(?:todo|to[- ]?do|pending(?:\s+items)?|待辦(?:事項)?)\s*[:：]?$")
_note_heading = re.compile(
    r"(?i)^(?:chief complaint|history of present illness|past medical history|assessment(?:\s*&\s*plan)?|plan|allergies|medications|CC|HPI|PMH|A/P|主訴|現病史|過去病史|診斷|治療計畫)(?:\s*\([^)]*\))?\s*(?:[:：]|$)"
)


def admission_top_items(bundle: PatientBundle, maximum: int = 8) -> tuple[str, list[str]]:
    """Separate explicit tasks from an unclassified admission-note excerpt."""
    admission_records = bundle.records_of("admission_note")
    if not admission_records:
        return "none", []
    latest = _latest(admission_records)
    top_lines = latest.content.splitlines()[:24]
    matched: list[str] = []
    in_section = False
    for raw_line in top_lines:
        line = re.sub(r"^\d+[.)、]\s*", "", raw_line.strip().lstrip("-•*#\t "))
        if not line or _note_heading.match(line):
            in_section = False
            continue
        if _todo_heading.fullmatch(line):
            in_section = True
        elif in_section or _todo_pattern.search(line):
            matched.append(line)
    if matched:
        return "todo", matched[:maximum]
    excerpt = [line.strip() for line in top_lines if line.strip()][: min(5, maximum)]
    return ("excerpt", excerpt) if excerpt else ("none", [])


def extract_top_todos(bundle: PatientBundle, maximum: int = 8) -> list[str]:
    kind, items = admission_top_items(bundle, maximum)
    return items if kind == "todo" else []


def recent_reports(bundle: PatientBundle, maximum: int = 8) -> list[str]:
    output: list[str] = []
    for record in bundle.records_of("report_index", "report"):
        status = (
            f" [{record.status}]" if record.status in {"final", "pending", "preliminary"} else ""
        )
        lines = [line.strip() for line in record.content.splitlines() if line.strip()]
        for line in lines[:maximum]:
            output.append(f"{line}{status}")
            if len(output) >= maximum:
                return output
    return output


def render_bundle_overview(bundle: PatientBundle) -> str:
    patient = bundle.patient
    lines = [
        f"{patient.location or '床位未辨識'}｜{patient.name or patient.safe_label}｜{patient.histno}",
        f"資料截止：{bundle.fetched_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"已取得來源：{len(bundle.records)}",
        "",
    ]
    kind, items = admission_top_items(bundle)
    lines.append("入院紀錄頂端摘錄（未辨識明確待辦）：" if kind == "excerpt" else "入院紀錄頂端待辦：")
    lines.extend([f"- {item}" for item in items] or ["- 未辨識；請直接核對原始 admission note。"])
    lines.extend(["", "近期／Pending 報告："])
    reports = recent_reports(bundle)
    lines.extend([f"- {report}" for report in reports] or ["- 未取得報告索引；不代表沒有報告。"])
    lines.extend(["", "檢驗／報告歷史範圍：", window_description(bundle)])
    for source_id, record in bundle.source_map():
        if record.source_type.startswith("lab_") or record.source_type == "report_index":
            lines.append(f"- [{source_id}] {record.title}：{coverage_summary(record)}")
    lines.extend(["", "來源稽核："])
    for source_id, record in bundle.source_map():
        observed = (
            record.observed_at.astimezone(EMR_TIMEZONE).strftime("%Y-%m-%d %H:%M")
            if record.observed_at
            else "時間未建立"
        )
        lines.append(f"- [{source_id}] {record.title}｜{observed}｜hash {record.short_hash}")
    if bundle.warnings:
        lines.extend(["", "擷取警告："])
        lines.extend(f"- {warning}" for warning in bundle.warnings)
    return "\n".join(lines)


def _latest(records: list[SourceRecord]) -> SourceRecord:
    return max(records, key=lambda record: record.observed_at or record.fetched_at)
