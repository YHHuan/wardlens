from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(slots=True, frozen=True)
class AuditEvent:
    event: str
    payload_sha256: str = ""
    model: str = ""
    outcome: str = ""
    timestamp: str = ""

    def normalized(self) -> AuditEvent:
        if self.timestamp:
            return self
        return AuditEvent(
            event=self.event,
            payload_sha256=self.payload_sha256,
            model=self.model,
            outcome=self.outcome,
            timestamp=datetime.now(UTC).isoformat(),
        )


class HashOnlyAuditLog:
    """Append operational metadata only; patient text and identifiers are forbidden."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, event: AuditEvent) -> None:
        normalized = event.normalized()
        allowed_outcomes = {"", "success", "blocked", "failed", "cancelled"}
        if normalized.outcome not in allowed_outcomes:
            raise ValueError("Audit outcome is not from the bounded vocabulary.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(normalized), ensure_ascii=False, sort_keys=True)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
