from __future__ import annotations

import re

from wardlens.models import PatientBundle, SourceRecord

_todo_pattern = re.compile(
    r"(?i)(?:^|\b)(?:todo|to[- ]?do|pending|follow(?:\s|-)?up|recheck|待辦|追蹤|確認|補|安排)\b"
)


def extract_top_todos(bundle: PatientBundle, maximum: int = 8) -> list[str]:
    admission_records = bundle.records_of("admission_note")
    if not admission_records:
        return []
    latest = _latest(admission_records)
    top_lines = [line.strip(" -•\t") for line in latest.content.splitlines()[:24] if line.strip()]
    matched = [line for line in top_lines if _todo_pattern.search(line)]
    if matched:
        return matched[:maximum]
    return top_lines[: min(5, maximum)]


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
        "待辦／入院病歷頂端：",
    ]
    todos = extract_top_todos(bundle)
    lines.extend([f"- {todo}" for todo in todos] or ["- 未辨識；請直接核對原始 admission note。"])
    lines.extend(["", "近期／Pending 報告："])
    reports = recent_reports(bundle)
    lines.extend([f"- {report}" for report in reports] or ["- 未取得報告索引；不代表沒有報告。"])
    lines.extend(["", "來源稽核："])
    for source_id, record in bundle.source_map():
        observed = (
            record.observed_at.astimezone().strftime("%Y-%m-%d %H:%M")
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
