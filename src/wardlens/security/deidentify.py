from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from wardlens.models import PatientSummary


class DataLeakRisk(ValueError):
    """Raised when outbound content still contains a likely identifier."""


@dataclass(slots=True, frozen=True)
class Finding:
    kind: str
    redacted_preview: str


@dataclass(slots=True, frozen=True)
class DeidentificationResult:
    text: str
    input_sha256: str
    output_sha256: str
    replacements: tuple[Finding, ...] = ()
    residual_risks: tuple[Finding, ...] = ()

    @property
    def safe_to_send(self) -> bool:
        return not self.residual_risks


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _masked(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 2:
        return "*" * len(value)
    return f"{value[0]}{'*' * min(6, len(value) - 2)}{value[-1]}"


class Deidentifier:
    """Conservative deterministic redaction with a fail-closed residual scan.

    This is deliberately biased toward blocking ambiguous outbound payloads. It is
    not represented as a complete anonymization system.
    """

    _taiwan_id = re.compile(r"(?<![A-Za-z0-9])[A-Z][1289]\d{8}(?!\d)", re.IGNORECASE)
    _legacy_arc = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-D]\d{8}(?!\d)", re.IGNORECASE)
    _email = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
    _phone = re.compile(
        r"(?<!\d)(?:\+?886[-\s]?)?(?:0?9\d{2}[-\s]?\d{3}[-\s]?\d{3}|0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4})(?!\d)"
    )
    _labeled_identifier_zh = re.compile(
        r"(?im)(姓名|病歷號|病歷編號|身分證(?:字號)?|聯絡電話|手機|地址|床號|病床)"
        r"[ \t]*[:：#]?[ \t]*([^\n|,;]{1,80})"
    )
    _labeled_identifier_en = re.compile(
        r"(?im)\b(patient\s*name|name|address|date\s*of\s*birth|dob|histno|mrn|mr\s*no\.?|chart\s*no\.?|medical\s*record\s*no\.?|patient\s*id)"
        r"[ \t]*[:#][ \t]*([^\n|,;]{1,80})"
    )
    _url_identifier = re.compile(r"(?i)(histno|hisid|phistnum|adistno|caseno)=([^&\s]+)")
    _iso_date = re.compile(
        r"(?<!\d)(20\d{2}|19\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])(?!\d)"
    )
    _roc_date = re.compile(
        r"(?<!\d)(0?\d{2,3})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])(?!\d)"
    )
    _gregorian_zh_date = re.compile(
        r"(?<!\d)((?:19|20)\d{2})[ \t]*年[ \t]*(0?[1-9]|1[0-2])[ \t]*月[ \t]*(0?[1-9]|[12]\d|3[01])[ \t]*日"
    )
    _roc_zh_date = re.compile(
        r"(?<!\d)(?:民國[ \t]*)?(0?\d{2,3})[ \t]*年[ \t]*(0?[1-9]|1[0-2])[ \t]*月[ \t]*(0?[1-9]|[12]\d|3[01])[ \t]*日"
    )
    _short_date = re.compile(r"(?<![\d/.-])(0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])(?![\d/.-])")
    _short_zh_date = re.compile(
        r"(?<!\d)(0?[1-9]|1[0-2])[ \t]*月[ \t]*(0?[1-9]|[12]\d|3[01])[ \t]*日"
    )
    _possible_long_id = re.compile(r"(?<![A-Za-z0-9.])\d{7,16}(?![A-Za-z0-9.])")
    _possible_alphanumeric_id = re.compile(
        r"(?<![A-Za-z0-9])(?=[A-Z0-9]{9,13}(?![A-Za-z0-9]))(?=[A-Z0-9]*\d)[A-Z]{1,2}[A-Z0-9]{8,11}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    _redaction_marker = re.compile(r"\[[A-Z0-9_]+_REMOVED\]")

    def deidentify(
        self,
        text: str,
        patient: PatientSummary | None = None,
        anchor_time: datetime | None = None,
    ) -> DeidentificationResult:
        output = text.replace("\x00", "")
        replacements: list[Finding] = []
        anchor = anchor_time or datetime.now().astimezone()

        if patient is not None:
            term_kinds = (
                (patient.histno, "known_histno"),
                (patient.case_no, "known_case_no"),
                (patient.name, "known_name"),
                (patient.location, "known_location"),
            )
            for value, kind in sorted(term_kinds, key=lambda item: len(item[0]), reverse=True):
                value = value.strip()
                if len(value) < 2 or value not in output:
                    continue
                output = output.replace(value, f"[{kind.upper()}_REMOVED]")
                replacements.append(Finding(kind, _masked(value)))

        output, found = self._replace_pattern(output, self._taiwan_id, "taiwan_id", "[ID_REMOVED]")
        replacements.extend(found)
        output, found = self._replace_pattern(
            output, self._legacy_arc, "legacy_arc", "[ID_REMOVED]"
        )
        replacements.extend(found)
        output, found = self._replace_pattern(output, self._email, "email", "[EMAIL_REMOVED]")
        replacements.extend(found)
        output, found = self._replace_pattern(output, self._phone, "phone", "[PHONE_REMOVED]")
        replacements.extend(found)

        def replace_labeled(match: re.Match[str]) -> str:
            label, value = match.group(1), match.group(2).strip()
            replacements.append(Finding(f"labeled_{label}", _masked(value)))
            return "[IDENTIFIER_REMOVED]"

        output = self._labeled_identifier_zh.sub(replace_labeled, output)
        output = self._labeled_identifier_en.sub(replace_labeled, output)

        def replace_url_identifier(match: re.Match[str]) -> str:
            replacements.append(Finding("identifier_in_url", _masked(match.group(2))))
            return "[IDENTIFIER_REMOVED]"

        output = self._url_identifier.sub(replace_url_identifier, output)
        output = self._iso_date.sub(
            lambda match: self._relative_date(match, anchor, replacements, roc=False), output
        )
        output = self._roc_date.sub(
            lambda match: self._relative_date(match, anchor, replacements, roc=True), output
        )
        output = self._gregorian_zh_date.sub(
            lambda match: self._relative_date(match, anchor, replacements, roc=False), output
        )
        output = self._roc_zh_date.sub(
            lambda match: self._relative_date(match, anchor, replacements, roc=True), output
        )
        output = self._short_date.sub(
            lambda match: self._relative_short_date(match, anchor, replacements), output
        )
        output = self._short_zh_date.sub(
            lambda match: self._relative_short_date(match, anchor, replacements), output
        )

        residuals = self.scan(output, patient=patient)
        return DeidentificationResult(
            text=output,
            input_sha256=_digest(text),
            output_sha256=_digest(output),
            replacements=tuple(replacements),
            residual_risks=tuple(residuals),
        )

    def scan(self, text: str, patient: PatientSummary | None = None) -> list[Finding]:
        findings: list[Finding] = []
        # Redaction labels intentionally retain field names such as ``histno=`` so
        # clinicians can understand the preview. Remove only our bounded marker
        # tokens before scanning, otherwise the scanner flags its own output.
        scannable = self._redaction_marker.sub("", text)
        patterns = (
            (self._taiwan_id, "taiwan_id"),
            (self._legacy_arc, "legacy_arc"),
            (self._email, "email"),
            (self._phone, "phone"),
            (self._labeled_identifier_zh, "labeled_identifier"),
            (self._labeled_identifier_en, "labeled_identifier"),
            (self._url_identifier, "identifier_in_url"),
            (self._possible_long_id, "possible_long_identifier"),
            (self._possible_alphanumeric_id, "possible_alphanumeric_identifier"),
        )
        for pattern, kind in patterns:
            for match in pattern.finditer(scannable):
                findings.append(Finding(kind, _masked(match.group(0))))

        if patient is not None:
            for value in patient.redaction_terms():
                if value in scannable:
                    findings.append(Finding("known_patient_identifier", _masked(value)))
        return findings

    def assert_safe(self, text: str, patient: PatientSummary | None = None) -> None:
        risks = self.scan(text, patient=patient)
        if risks:
            kinds = ", ".join(sorted({risk.kind for risk in risks}))
            raise DataLeakRisk(f"Outbound text is blocked; residual identifier risk: {kinds}")

    @staticmethod
    def _replace_pattern(
        text: str,
        pattern: re.Pattern[str],
        kind: str,
        replacement: str,
    ) -> tuple[str, list[Finding]]:
        findings: list[Finding] = []

        def replace(match: re.Match[str]) -> str:
            findings.append(Finding(kind, _masked(match.group(0))))
            return replacement

        return pattern.sub(replace, text), findings

    @staticmethod
    def _relative_date(
        match: re.Match[str],
        anchor: datetime,
        replacements: list[Finding],
        *,
        roc: bool,
    ) -> str:
        year, month, day = (int(part) for part in match.groups())
        if roc:
            year += 1911
        try:
            parsed = datetime(year, month, day, tzinfo=anchor.tzinfo)
        except ValueError:
            replacements.append(Finding("invalid_exact_date", _masked(match.group(0))))
            return "[EXACT_DATE_REMOVED]"
        delta = (parsed.date() - anchor.date()).days
        replacements.append(Finding("exact_date", _masked(match.group(0))))
        return "D0" if delta == 0 else f"D{delta:+d}"

    @staticmethod
    def _relative_short_date(
        match: re.Match[str],
        anchor: datetime,
        replacements: list[Finding],
    ) -> str:
        month, day = (int(part) for part in match.groups())
        try:
            parsed = datetime(anchor.year, month, day, tzinfo=anchor.tzinfo)
        except ValueError:
            replacements.append(Finding("invalid_exact_date", _masked(match.group(0))))
            return "[EXACT_DATE_REMOVED]"
        delta = (parsed.date() - anchor.date()).days
        replacements.append(Finding("exact_date_without_year", _masked(match.group(0))))
        return "D0" if delta == 0 else f"D{delta:+d}"
