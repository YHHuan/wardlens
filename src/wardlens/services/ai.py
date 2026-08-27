from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from wardlens.llm.openrouter import OpenRouterClient, StreamEvent
from wardlens.llm.prompts import PromptBuilder, PromptEnvelope
from wardlens.models import LLMResult, ModelProfile, PatientBundle, PatientSummary
from wardlens.security.audit import AuditEvent, HashOnlyAuditLog
from wardlens.security.outbound import ApprovedPreview, OutboundGuard


@dataclass(slots=True, frozen=True)
class PreparedRequest:
    """The exact de-identified bytes a clinician has been shown for approval."""

    preview: ApprovedPreview
    workflow: str
    source_fetched_at: datetime | None = None

    def envelope(self) -> PromptEnvelope:
        return PromptEnvelope.parse(self.preview.text)


class AIWorkflowService:
    def __init__(
        self,
        *,
        builder: PromptBuilder | None = None,
        guard: OutboundGuard | None = None,
        client: OpenRouterClient | None = None,
        audit: HashOnlyAuditLog | None = None,
    ) -> None:
        self.builder = builder or PromptBuilder()
        self.guard = guard or OutboundGuard()
        self.client = client or OpenRouterClient()
        self.audit = audit

    def prepare(
        self,
        workflow: str,
        *,
        bundle: PatientBundle | None = None,
        question: str = "",
        emergency_text: str = "",
    ) -> PreparedRequest:
        raw = self.builder.build(
            workflow,
            bundle=bundle,
            question=question,
            emergency_text=emergency_text,
        ).canonical()
        patient: PatientSummary | None = bundle.patient if bundle is not None else None
        preview = self.guard.prepare(raw, patient=patient)
        # Parse now as a structural integrity check, before the preview reaches UI.
        PromptEnvelope.parse(preview.text)
        self._audit("preview_created", preview.sha256, outcome="success")
        return PreparedRequest(
            preview=preview,
            workflow=workflow,
            source_fetched_at=bundle.fetched_at if bundle is not None else None,
        )

    def complete(
        self,
        api_key: str,
        prepared: PreparedRequest,
        profile: ModelProfile,
        *,
        require_zdr: bool = True,
    ) -> LLMResult:
        envelope = self._verified_envelope(prepared)
        try:
            result = self.client.complete(
                api_key,
                envelope,
                profile,
                require_zdr=require_zdr,
            )
        except Exception:
            self._audit(
                "llm_request", prepared.preview.sha256, model=profile.model, outcome="failed"
            )
            raise
        self._audit("llm_request", prepared.preview.sha256, model=profile.model, outcome="success")
        return result

    def stream(
        self,
        api_key: str,
        prepared: PreparedRequest,
        profile: ModelProfile,
        *,
        require_zdr: bool = True,
    ) -> Iterator[StreamEvent]:
        envelope = self._verified_envelope(prepared)
        try:
            yield from self.client.stream(
                api_key,
                envelope,
                profile,
                require_zdr=require_zdr,
            )
        except Exception:
            self._audit(
                "llm_request", prepared.preview.sha256, model=profile.model, outcome="failed"
            )
            raise
        self._audit("llm_request", prepared.preview.sha256, model=profile.model, outcome="success")

    def record_copy(self, prepared: PreparedRequest) -> None:
        self._verified_envelope(prepared)
        self._audit("prompt_copied", prepared.preview.sha256, outcome="success")

    def _verified_envelope(self, prepared: PreparedRequest) -> PromptEnvelope:
        envelope = prepared.envelope()
        self.guard.verify(prepared.preview, envelope.canonical())
        return envelope

    def _audit(
        self, event: str, payload_sha256: str, *, model: str = "", outcome: str = ""
    ) -> None:
        if self.audit is not None:
            self.audit.append(
                AuditEvent(
                    event=event,
                    payload_sha256=payload_sha256,
                    model=model,
                    outcome=outcome,
                )
            )
