from __future__ import annotations

import html
from dataclasses import dataclass
from importlib.resources import files

from wardlens.models import PatientBundle


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
    ) -> PromptEnvelope:
        system = self.system_prompt(workflow)
        blocks: list[str] = []
        if bundle is not None:
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
            f"Age: {patient.age or 'not provided'}",
            f"Sex: {patient.sex or 'not provided'}",
            f"Source count: {len(bundle.records)}",
            "</patient_context>",
        ]
        source_blocks: list[str] = []
        total_chars = 0
        for source_id, record in bundle.source_map():
            content = record.content[:24000]
            if total_chars + len(content) > 140000:
                content = content[: max(0, 140000 - total_chars)]
            if not content:
                break
            total_chars += len(content)
            when = "time not established"
            if record.observed_at is not None:
                day_delta = (record.observed_at.date() - bundle.fetched_at.date()).days
                when = "D0" if day_delta == 0 else f"D{day_delta:+d}"
            metadata = (
                f"id={source_id}; type={record.source_type}; title={record.title}; "
                f"observed={when}; status={record.status or 'not stated'}"
            )
            source_blocks.append(
                f"<clinical_source {html.escape(metadata, quote=True)}>\n"
                f"{self._escape_source(content)}\n"
                "</clinical_source>"
            )
        if not source_blocks:
            source_blocks.append(
                '<clinical_source id="SRC-000">No readable clinical source was provided.</clinical_source>'
            )
        return "\n".join(header + source_blocks)

    @staticmethod
    def _escape_source(text: str) -> str:
        return html.escape(text.replace("\x00", ""), quote=False)
