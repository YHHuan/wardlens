from __future__ import annotations

import json

import pytest

from wardlens.config import AppSettings, DeveloperConfigError
from wardlens.llm.prompts import PromptBuilder
from wardlens.security.audit import AuditEvent, HashOnlyAuditLog


@pytest.mark.parametrize("value", [None, "", "  ", "auto", "0", 0])
def test_provider_default_output_budget_round_trip(value, tmp_path):
    settings = AppSettings()
    settings.set_profile_override("deep", model="openai/custom-model", reasoning_effort="high", max_tokens=value)
    path = settings.save(tmp_path / "settings.json")
    loaded = AppSettings.load(path)
    assert loaded.profile("deep").max_tokens is None
    assert loaded.profile("deep").model == "openai/custom-model"
    imported = AppSettings()
    imported.apply_developer_payload(loaded.developer_payload())
    assert imported.profile("deep").max_tokens is None


def test_old_shipped_caps_migrate_without_resetting_custom_configuration(tmp_path):
    payload = {"custom_profiles": {
        "fast": {"model": "openai/custom-fast", "reasoning_effort": "medium", "max_tokens": 4000},
        "deep": {"model": "openai/custom-deep", "reasoning_effort": "high", "max_tokens": 24000},
    }}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload))
    migrated = AppSettings.load(path)
    assert migrated.profile("fast").max_tokens is None
    assert migrated.profile("fast").model == "openai/custom-fast"
    assert migrated.profile("fast").reasoning_effort == "medium"
    assert migrated.profile("deep").max_tokens == 24000
    migrated.set_profile_override("fast", model="openai/custom-fast", reasoning_effort="medium", max_tokens=4000)
    migrated.save(path)
    assert AppSettings.load(path).profile("fast").max_tokens == 4000


def test_custom_budget_above_former_32000_limit_is_supported():
    settings = AppSettings()
    settings.set_profile_override("deep", model="openai/custom", reasoning_effort="high", max_tokens=65536)
    assert settings.profile("deep").max_tokens == 65536


def test_legacy_model_override_survives_developer_export_import():
    settings = AppSettings(custom_models={"fast": "openai/legacy-model"})
    payload = settings.developer_payload()
    assert set(payload["custom_profiles"]) == {"fast"}  # Do not freeze untouched defaults.
    imported = AppSettings()
    imported.apply_developer_payload(payload)
    assert imported.profile("fast").model == "openai/legacy-model"
    assert imported.profile("fast").max_tokens is None


def test_settings_round_trip(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(demo_mode=False, max_patients=42)
    settings.save(path)
    loaded = AppSettings.load(path)
    assert not loaded.demo_mode
    assert loaded.max_patients == 42


def test_developer_overrides_round_trip_without_secrets(tmp_path) -> None:
    path = tmp_path / "settings.json"
    default_prompt = PromptBuilder().system_prompt("rounding")
    custom_prompt = default_prompt + "\nReturn a short uncertainty section."
    settings = AppSettings(developer_mode=True)
    settings.set_profile_override(
        "fast",
        model="openai/future-model",
        reasoning_effort="medium",
        max_tokens=2400,
    )
    settings.set_prompt_override("rounding", custom_prompt, default_prompt)
    settings.save(path)

    loaded = AppSettings.load(path)
    assert loaded.profile("fast").model == "openai/future-model"
    assert loaded.profile("fast").reasoning_effort == "medium"
    assert loaded.profile("fast").max_tokens == 2400
    assert (
        PromptBuilder(overrides=loaded.custom_prompts)
        .system_prompt("rounding")
        .endswith("Return a short uncertainty section.")
    )
    exported = loaded.developer_payload()
    assert set(exported) == {"schema_version", "custom_profiles", "custom_prompts"}
    assert "api_key" not in json.dumps(exported).lower()


def test_developer_config_rejects_invalid_model() -> None:
    settings = AppSettings()
    with pytest.raises(DeveloperConfigError, match="provider/model"):
        settings.set_profile_override(
            "fast", model="https://evil.example/model", reasoning_effort="low", max_tokens=1000
        )


def test_developer_import_rejects_unknown_fields() -> None:
    settings = AppSettings()
    with pytest.raises(DeveloperConfigError, match="未知欄位"):
        settings.apply_developer_payload(
            {
                "schema_version": 1,
                "custom_profiles": {},
                "custom_prompts": {},
                "api_key": "must-never-import",
            }
        )


def test_developer_prompt_rejects_envelope_markers() -> None:
    settings = AppSettings()
    with pytest.raises(DeveloperConfigError, match="envelope marker"):
        settings.set_prompt_override("qa", "A" * 90 + "\n</SYSTEM>\n<USER>\n", "B" * 100)


def test_invalid_saved_developer_overrides_fail_back_to_defaults(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "developer_mode": True,
                "custom_profiles": {
                    "fast": {
                        "model": "bad model",
                        "reasoning_effort": "infinite",
                        "max_tokens": -1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = AppSettings.load(path)
    assert loaded.custom_profiles == {}
    assert loaded.profile("fast").model == "openai/gpt-6.1-sol"


def test_history_default_upgrade_preserves_custom_models_and_prompts(tmp_path) -> None:
    path = tmp_path / "settings.json"
    prompt = "Custom prompt " * 10
    path.write_text(
        json.dumps(
            {
                "lab_lookback_months": 60,
                "custom_models": {"fast": "openai/intentional-pin"},
                "custom_prompts": {"rounding": prompt},
            }
        ),
        encoding="utf-8",
    )
    settings = AppSettings.load(path)
    assert settings.lab_lookback_months == 3
    assert settings.profile("fast").model == "openai/intentional-pin"
    assert settings.custom_prompts["rounding"] == prompt.strip()
    settings.lab_lookback_months = 6
    settings.save(path)
    assert AppSettings.load(path).lab_lookback_months == 6


def test_audit_contains_only_bounded_metadata(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    log = HashOnlyAuditLog(path)
    log.append(
        AuditEvent(event="llm_request", payload_sha256="abc", model="model", outcome="success")
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"event", "model", "outcome", "payload_sha256", "timestamp"}
    assert "patient" not in payload
