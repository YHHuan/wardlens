from __future__ import annotations

import json

from wardlens.config import AppSettings
from wardlens.security.audit import AuditEvent, HashOnlyAuditLog


def test_settings_round_trip(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(demo_mode=False, max_patients=42)
    settings.save(path)
    loaded = AppSettings.load(path)
    assert not loaded.demo_mode
    assert loaded.max_patients == 42


def test_audit_contains_only_bounded_metadata(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    log = HashOnlyAuditLog(path)
    log.append(
        AuditEvent(event="llm_request", payload_sha256="abc", model="model", outcome="success")
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"event", "model", "outcome", "payload_sha256", "timestamp"}
    assert "patient" not in payload
