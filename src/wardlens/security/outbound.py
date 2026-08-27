from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from wardlens.models import PatientSummary
from wardlens.security.deidentify import DataLeakRisk, Deidentifier


class PreviewMismatch(ValueError):
    """Raised when the approved preview and actual payload differ."""


def payload_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class ApprovedPreview:
    text: str
    sha256: str
    replacement_count: int


class OutboundGuard:
    def __init__(self, deidentifier: Deidentifier | None = None) -> None:
        self.deidentifier = deidentifier or Deidentifier()

    def prepare(self, raw_text: str, patient: PatientSummary | None = None) -> ApprovedPreview:
        result = self.deidentifier.deidentify(raw_text, patient=patient)
        if result.residual_risks:
            kinds = ", ".join(sorted({risk.kind for risk in result.residual_risks}))
            raise DataLeakRisk(f"Preview blocked; please remove: {kinds}")
        return ApprovedPreview(
            text=result.text,
            sha256=result.output_sha256,
            replacement_count=len(result.replacements),
        )

    def verify(
        self, preview: ApprovedPreview, actual_text: str, patient: PatientSummary | None = None
    ) -> str:
        self.deidentifier.assert_safe(actual_text, patient=patient)
        actual_hash = payload_hash(actual_text)
        if actual_hash != preview.sha256:
            raise PreviewMismatch(
                "Outbound payload changed after preview; preview it again before sending."
            )
        return actual_hash
