from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from wardlens.models import PatientSummary
from wardlens.security.headers import identifier_spans, unresolved_labels


class DataLeakRisk(ValueError):
    """Raised when outbound content still contains a likely identifier."""


@dataclass(slots=True, frozen=True)
class Finding:
    kind: str
    redacted_preview: str
    start: int | None = None
    end: int | None = None


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


def clean_clinical_headers(text: str) -> tuple[str, tuple[Finding, ...]]:
    """Remove recognized identifying/admin fields; keep real clinical dates.

    Suitable for a local report view as well as prompt preparation. This is not
    complete de-identification: narrative identifiers and uncertain leftovers
    still need Deidentifier and its outbound residual scan. Finding offsets
    refer to the supplied input, never to the shortened output.
    """
    spans = identifier_spans(text)
    output = text
    for span in reversed(spans):
        output = output[:span.start] + output[span.end:]
    findings = tuple(
        Finding(span.kind, "[administrative field removed]", span.start, span.end)
        for span in spans
    )
    # Keep row boundaries and interior spacing intact; only empty administrative
    # lines need collapsing. Table columns have already been removed together.
    output = re.sub(r"(?m)^[ \t|｜;；]+$", "", output)
    output = re.sub(r"\n{3,}", "\n\n", output).strip()
    return output, findings


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
    _url_identifier = re.compile(
        r"(?i)(histno|hisid|phistnum|adistno|caseno|mrn|patient_?id|"
        r"accession(?:_?(?:no|id))?|specimen_?(?:no|id)|report_?(?:no|id))=([^&\s]+)"
    )
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
    _redaction_marker = re.compile(
        r"\[(?:KNOWN_HISTNO|KNOWN_CASE_NO|KNOWN_NAME|KNOWN_LOCATION|ID|EMAIL|PHONE|"
        r"IDENTIFIER|EXACT_DATE|MANUAL_IDENTIFIER)_REMOVED\]"
    )

    def deidentify(
        self,
        text: str,
        patient: PatientSummary | None = None,
        anchor_time: datetime | None = None,
    ) -> DeidentificationResult:
        output, header_findings = clean_clinical_headers(text.replace("\x00", ""))
        replacements = list(header_findings)
        anchor = anchor_time or datetime.now().astimezone()

        if patient is not None:
            term_kinds = self._known_patient_terms(patient)
            for value, kind in sorted(term_kinds, key=lambda item: len(item[0]), reverse=True):
                if value not in output:
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
            lambda match: (
                match.group(0) if self._is_clinical_fraction(match)
                else self._relative_short_date(match, anchor, replacements)
            ), output
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
        # Preserve offsets for the local review UI; never join digits across a marker.
        scannable = self._redaction_marker.sub(lambda m: " " * len(m.group()), text)
        patterns = (
            (self._taiwan_id, "taiwan_id"),
            (self._legacy_arc, "legacy_arc"),
            (self._email, "email"),
            (self._phone, "phone"),
            (self._url_identifier, "identifier_in_url"),
            (self._possible_long_id, "possible_long_identifier"),
            (self._possible_alphanumeric_id, "possible_alphanumeric_identifier"),
        )
        for pattern, kind in patterns:
            for match in pattern.finditer(scannable):
                findings.append(Finding(kind, _masked(match.group(0)), *match.span()))

        for span in (*identifier_spans(scannable), *unresolved_labels(scannable)):
            findings.append(Finding(span.kind, "[labelled identifier]", span.start, span.end))

        if patient is not None:
            for value, _ in self._known_patient_terms(patient):
                for match in re.finditer(re.escape(value), scannable):
                    findings.append(
                        Finding("known_patient_identifier", _masked(value), *match.span())
                    )
        return findings

    @staticmethod
    def _known_patient_terms(patient: PatientSummary) -> tuple[tuple[str, str], ...]:
        """Only specific locations can be safely replaced without a field label.

        A numeric bed/ward such as 01, 101 or 101-01 also occurs inside doses,
        laboratory values and calendar dates. A short text location such as ICU
        may be clinical content. Their labelled fields are still removed by the
        header cleaner; the literal residual scan must use the same restriction.
        """
        terms: list[tuple[str, str]] = []
        for raw, kind in (
            (patient.histno, "known_histno"),
            (patient.case_no, "known_case_no"),
            (patient.name, "known_name"),
            (patient.location, "known_location"),
        ):
            value = raw.strip()
            if len(value) < 2:
                continue
            if kind == "known_location" and (len(value) < 4 or not any(character.isalpha() for character in value)):
                continue
            terms.append((value, kind))
        return tuple(terms)

    @staticmethod
    def mask_findings(text: str, findings: list[Finding] | tuple[Finding, ...]) -> str:
        """Apply only user-selected current findings, merging overlapping spans."""
        spans: list[tuple[int, int]] = []
        for finding in findings:
            start, end = finding.start, finding.end
            if start is None or end is None or not 0 <= start < end <= len(text):
                raise ValueError("疑似識別碼的位置已變更，請重新檢查。")
            spans.append((start, end))
        merged: list[tuple[int, int]] = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        for start, end in reversed(merged):
            text = text[:start] + "[MANUAL_IDENTIFIER_REMOVED]" + text[end:]
        return text

    def assert_safe(self, text: str, patient: PatientSummary | None = None) -> None:
        risks = self.scan(text, patient=patient)
        if risks:
            kinds = ", ".join(sorted({risk.kind for risk in risks}))
            raise DataLeakRisk(f"Outbound text is blocked; residual identifier risk: {kinds}")

    @staticmethod
    def _is_clinical_fraction(match: re.Match[str]) -> bool:
        """Do not turn dose fractions, scales or ranges into calendar dates."""
        before = match.string[max(0, match.start() - 45):match.start()]
        after = match.string[match.end():match.end() + 35]
        # Dates with an explicit date label stay dates even inside clinical notes.
        if re.search(r"(?:date|日期|時間|採檢|報告|collected|reported)\s*[:：]?\s*$", before, re.I):
            return False
        if re.match(
            r"\s*(?:tab(?:let)?s?|caps?(?:ules?)?|vials?|ampoules?|錠|顆|粒|包|管|mg|mcg|µg|g\b|mL|L\b|units?|IU|mmol|mEq|分|points?\b|strength\b)",
            after, re.I,
        ):
            return True
        if re.search(
            r"(?:pain|疼痛|痛分|肌力|muscle(?:\s+power|\s+strength)?|strength|motor|power|score|scale|grade|GCS|NYHA|ECOG|dose|dosage|劑量|take|服用|給予|分數)[^\n;；:：]*[:：]?\s*$",
            before, re.I,
        ):
            return True
        # Explicit fraction notation and ranges such as q 6-8 h are not dates.
        return bool(re.match(r"\s*(?:hours?|hrs?|h\b|days?|weeks?|小時|天|週)\b", after, re.I))

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
