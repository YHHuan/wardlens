from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class PatientSummary:
    """Locally identifiable patient summary. Never serialize this into logs."""

    histno: str
    name: str = ""
    ward: str = ""
    bed: str = ""
    age: str = ""
    sex: str = ""
    case_no: str = ""
    raw_columns: tuple[str, ...] = ()

    @property
    def local_key(self) -> str:
        seed = f"{self.histno}|{self.case_no}" if self.case_no else self.histno
        return content_hash(seed)[:16]

    @property
    def location(self) -> str:
        return "-".join(part for part in (self.ward, self.bed) if part)

    @property
    def safe_label(self) -> str:
        return f"Patient-{self.local_key[:6]}"

    def redaction_terms(self) -> tuple[str, ...]:
        return tuple(
            value.strip()
            for value in (self.histno, self.name, self.case_no, self.location)
            if value and len(value.strip()) >= 2
        )


@dataclass(slots=True, frozen=True)
class SourceRecord:
    source_type: str
    title: str
    content: str
    source_url: str = ""
    source_record_id: str = ""
    observed_at: datetime | None = None
    fetched_at: datetime = field(default_factory=utc_now)
    status: str = ""
    content_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", content_hash(self.content))

    @property
    def short_hash(self) -> str:
        return self.content_sha256[:12]


@dataclass(slots=True)
class PatientBundle:
    patient: PatientSummary
    records: list[SourceRecord] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=utc_now)
    warnings: list[str] = field(default_factory=list)

    def records_of(self, *source_types: str) -> list[SourceRecord]:
        wanted = set(source_types)
        return [record for record in self.records if record.source_type in wanted]

    def source_map(self) -> list[tuple[str, SourceRecord]]:
        return [(f"SRC-{idx:03d}", record) for idx, record in enumerate(self.records, start=1)]

    def add_record(self, record: SourceRecord) -> None:
        duplicate = next(
            (
                old
                for old in self.records
                if old.source_type == record.source_type
                and old.source_record_id
                and old.source_record_id == record.source_record_id
                and old.content_sha256 == record.content_sha256
            ),
            None,
        )
        if duplicate is None:
            self.records.append(record)


@dataclass(slots=True)
class PatientListResult:
    patients: list[PatientSummary]
    complete: bool
    pages_fetched: int
    declared_total: int | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class ModelProfile:
    key: str
    label: str
    model: str
    reasoning_effort: str
    max_tokens: int
    intended_use: str


@dataclass(slots=True)
class LLMResult:
    text: str
    model: str
    request_id: str = ""
    provider_name: str = ""
    latency_seconds: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
