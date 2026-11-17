from __future__ import annotations

import html
from dataclasses import dataclass, replace
from importlib.resources import files

from wardlens.models import EMR_TIMEZONE, PatientBundle
from wardlens.security.deidentify import clean_clinical_headers
from wardlens.services.history import (
    TRUNCATION_MARKER,
    bounded_source,
    coverage_summary,
    window_description,
)


class PromptFormatError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class PromptEnvelope:
    system: str
    user: str

    version = "WARDLENS-PROMPT-V1"

    def canonical(self) -> str:
        return (
            f"{self.version}\n"
            "<SYSTEM>\n"
            f"{self.system.strip()}\n"
            "</SYSTEM>\n"
            "<USER>\n"
            f"{self.user.strip()}\n"
            "</USER>"
        )

    @classmethod
    def parse(cls, text: str) -> PromptEnvelope:
        normalized = text.strip()
        prefix = f"{cls.version}\n<SYSTEM>\n"
        divider = "\n</SYSTEM>\n<USER>\n"
        suffix = "\n</USER>"
        if (
            not normalized.startswith(prefix)
            or divider not in normalized
            or not normalized.endswith(suffix)
        ):
            raise PromptFormatError("Prompt preview markers were changed; rebuild the preview.")
        system_and_user = normalized[len(prefix) : -len(suffix)]
        system, user = system_and_user.split(divider, 1)
        if not system.strip() or not user.strip():
            raise PromptFormatError("System and user content must both be present.")
        return cls(system=system.strip(), user=user.strip())

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


class PromptBuilder:
    # Includes source metadata and escaped text, not just raw record bodies.
    # Leave room for system/question text in the 2 MB local HTTP request limit.
    MAX_CONTEXT_CHARACTERS = 400000
    _HEADER_RESERVE = 10000
    _BODY_BUDGET = 240000
    _prompt_files = {
        "rounding": "rounding_review_zh_tw.txt",
        "admission": "admission_note_en.txt",
        "emergency": "emergency_zh_tw.txt",
        "qa": "rounding_review_zh_tw.txt",
    }

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._overrides = dict(overrides or {})

    def system_prompt(self, workflow: str) -> str:
        try:
            filename = self._prompt_files[workflow]
        except KeyError as exc:
            raise ValueError(f"Unknown workflow: {workflow}") from exc
        custom = self._overrides.get(workflow, "").strip()
        if custom:
            return custom
        path = files("wardlens").joinpath(f"resources/prompts/{filename}")
        return path.read_text(encoding="utf-8").strip()

    def build(
        self,
        workflow: str,
        *,
        bundle: PatientBundle | None = None,
        question: str = "",
        emergency_text: str = "",
        context_text: str | None = None,
    ) -> PromptEnvelope:
        system = self.system_prompt(workflow)
        blocks: list[str] = []
        if context_text is not None:
            # An explicit editable draft REPLACES the automatic bundle, including
            # when the user cleared it. Never silently append the original chart.
            blocks.append(
                '<clinical_source id="EDITED-CONTEXT" status="clinician-edited; verify against EMR">\n'
                + self._escape_source(context_text.strip() or "No patient context supplied.")
                + "\n</clinical_source>"
            )
        elif bundle is not None:
            blocks.append(self._bundle_text(bundle))
        if workflow == "emergency":
            case_text = emergency_text.strip() or question.strip()
            if not case_text:
                raise ValueError("請輸入目前病情。")
            blocks.append(
                "<current_event>\n" + self._escape_source(case_text) + "\n</current_event>"
            )
        elif question.strip():
            blocks.append(
                "<clinician_question>\n"
                + self._escape_source(question.strip())
                + "\n</clinician_question>"
            )
        elif workflow == "rounding":
            blocks.append("請依固定格式整理今天查房重點。")
        elif workflow == "admission":
            blocks.append("Generate the admission note using the required six-section format.")
        else:
            raise ValueError("請輸入問題。")
        return PromptEnvelope(system=system, user="\n\n".join(blocks))

    def _bundle_text(self, bundle: PatientBundle) -> str:
        patient = bundle.patient
        header = [
            "<patient_context>",
            "Patient label: P1 (request-local, not derived from an identifier)",
            f"Age: {self._escape_source(patient.age[:64] or 'not provided')}",
            f"Sex: {self._escape_source(patient.sex[:64] or 'not provided')}",
            f"Source count: {len(bundle.records)}",
            window_description(bundle, relative=True),
            "</patient_context>",
        ]
        if bundle.warnings:
            header.append(
                "<source_warnings>\n"
                + bounded_source(self._escape_source("\n".join(bundle.warnings)), 6000)
                + "\n</source_warnings>"
            )
        source_blocks = self._context_sources(bundle, editor=False)
        if not source_blocks:
            source_blocks.append(
                '<clinical_source id="SRC-000">No readable clinical source was provided.</clinical_source>'
            )
        return "\n".join(header + source_blocks)

    @staticmethod
    def _allocate_sizes(contents: list[str], budget: int) -> list[int]:
        # Fair-share allocation prevents earlier notes from crowding out labs.
        allocations = [0] * len(contents)
        remaining = max(0, budget)
        pending = set(range(len(contents)))
        while pending and remaining:
            share = max(1, remaining // len(pending))
            for i in sorted(pending):
                need = min(len(contents[i]), 120000) - allocations[i]
                take = min(need, share, remaining)
                allocations[i] += take
                remaining -= take
                if take == need:
                    pending.remove(i)
        return allocations

    @classmethod
    def _source_allocations(cls, bundle: PatientBundle) -> list[int]:
        contents = [clean_clinical_headers(record.content)[0] for record in bundle.records]
        return cls._allocate_sizes(contents, cls._BODY_BUDGET)

    def _context_sources(self, bundle: PatientBundle, *, editor: bool) -> list[str]:
        """Bound the entire rendered context and disclose every omitted excerpt.

        The usual case retains every source with a fair share of body space.
        Only if metadata alone exceeds the total budget, distribute excerpts
        across the source list and clearly report the missing source count. Raw
        fetched reports remain in the local bundle; nothing here changes them.
        """
        sources: list[tuple[str, str, str]] = []
        anchor = bundle.fetched_at.astimezone(EMR_TIMEZONE).date()
        metadata_shortened = False
        for source_id, raw_record in bundle.source_map():
            content, _ = clean_clinical_headers(raw_record.content)
            title, _ = clean_clinical_headers(raw_record.title)
            status, _ = clean_clinical_headers(raw_record.status)
            metadata_shortened |= len(title) > 200 or len(status) > 80 or len(raw_record.source_type) > 64
            title = title[:200] + ("…" if len(title) > 200 else "")
            status = status[:80] + ("…" if len(status) > 80 else "")
            record = replace(raw_record, content=content, title=title, status=status)
            coverage = ""
            if record.source_type.startswith("lab_") or record.source_type in {"report", "report_index", "vital_signs"}:
                coverage = coverage_summary(record, relative_to=None if editor else anchor) + "\n"
            if editor:
                when = record.observed_at.astimezone(EMR_TIMEZONE).strftime("%Y-%m-%d %H:%M") if record.observed_at else "來源時間未建立"
                prefix = f"\n[{source_id}] {title}｜{record.source_type[:64]}｜{when}｜{status or '狀態未建立'}\n{coverage}"
                sources.append((prefix, content, ""))
            else:
                when = "time not established"
                if record.observed_at:
                    day_delta = (record.observed_at.astimezone(EMR_TIMEZONE).date() - anchor).days
                    when = "D0" if day_delta == 0 else f"D{day_delta:+d}"
                metadata = f"id={source_id}; type={record.source_type[:64]}; title={title}; observed={when}; status={status or 'not stated'}"
                prefix = f"<clinical_source {html.escape(metadata, quote=True)}>\n{self._escape_source(coverage)}"
                sources.append((prefix, self._escape_source(content), "\n</clinical_source>"))

        maximum = self.MAX_CONTEXT_CHARACTERS - self._HEADER_RESERVE
        total_sources = len(sources)
        overhead = sum(len(prefix) + len(suffix) + 1 for prefix, _, suffix in sources)
        # Reserve half the source budget for clinical content before discarding
        # any metadata. This branch is for thousands of unusually large titles.
        if overhead > maximum:
            count = max(1, int(total_sources * (maximum // 2) / overhead))
            indices = [round(i * (total_sources - 1) / max(1, count - 1)) for i in range(count)]
            sources = [sources[index] for index in indices]
            overhead = sum(len(prefix) + len(suffix) + 1 for prefix, _, suffix in sources)
        allocations = self._allocate_sizes(
            [content for _, content, _ in sources], min(self._BODY_BUDGET, maximum - overhead)
        )
        blocks: list[str] = []
        truncated_count = 0
        omitted_content = 0
        for (prefix, content, suffix), limit in zip(sources, allocations, strict=True):
            if len(content) <= limit:
                excerpt = content
            elif limit >= len(TRUNCATION_MARKER) + 2:
                excerpt = bounded_source(content, limit)
                truncated_count += 1
            else:
                excerpt = ""
                omitted_content += 1
            blocks.append(prefix + excerpt + suffix)
        if truncated_count or omitted_content or len(sources) < total_sources or metadata_shortened:
            blocks.append(
                f"{TRUNCATION_MARKER}\n"
                f"AI 輸入額度：{truncated_count} 份來源節錄、{omitted_content} 份內容未納入、"
                f"{total_sources - len(sources)} 份來源未納入；標題過長時亦節錄。"
                "完整擷取原文仍在本機；不得據此宣稱檢驗／報告已全部涵蓋。"
            )
        return blocks

    def editor_context(self, bundle: PatientBundle) -> str:
        """Human-editable chart snapshot without system prompts or XML scaffolding."""
        lines = [
            "病人資料草稿（本機可編輯；編輯不會寫回 EMR，引用仍須核對原始來源）",
            f"資料截止：{bundle.fetched_at.astimezone(EMR_TIMEZONE):%Y-%m-%d %H:%M %Z}",
            f"Age: {bundle.patient.age[:64] or 'not provided'}; Sex: {bundle.patient.sex[:64] or 'not provided'}",
            window_description(bundle),
        ]
        if bundle.warnings:
            lines.extend(["", "擷取警告：", bounded_source("\n".join(bundle.warnings), 6000)])
        lines.extend(self._context_sources(bundle, editor=True))
        if not bundle.records:
            lines.append("未取得可讀資料，不代表沒有異常。")
        return "\n".join(lines)

    @staticmethod
    def clinical_preview(envelope: PromptEnvelope) -> str:
        """Readable projection of the exact user message; full bytes stay available.

        Remove ONLY app-generated wrapper lines before unescaping clinical text,
        so source text resembling tags cannot be accidentally hidden.
        """
        output: list[str] = []
        for line in envelope.user.split("\n"):
            if line.startswith('<clinical_source id="EDITED-CONTEXT" '):
                output.append("【將送出的病人資料】")
            elif line == "<current_event>":
                output.append("【目前病況／問題】")
            elif line == "<clinician_question>":
                output.append("【問題】")
            elif line in {"</clinical_source>", "</current_event>", "</clinician_question>"}:
                continue
            else:
                output.append(line)
        return html.unescape("\n".join(output))

    @staticmethod
    def _escape_source(text: str) -> str:
        return html.escape(text.replace("\x00", ""), quote=False)
