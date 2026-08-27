from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from wardlens.models import ModelProfile

DEFAULT_PROFILES = {
    "fast": ModelProfile(
        key="fast",
        label="Fast｜日常整理",
        model="openai/gpt-5.6-terra",
        reasoning_effort="low",
        max_tokens=1400,
        intended_use="病人清單整理、待辦與報告摘要",
    ),
    "deep": ModelProfile(
        key="deep",
        label="Slow｜複雜推理",
        model="openai/gpt-5.6-sol",
        reasoning_effort="high",
        max_tokens=6000,
        intended_use="複雜臨床問題與第二次複核",
    ),
    "emergency_fast": ModelProfile(
        key="emergency_fast",
        label="急症立即版",
        model="openai/gpt-5.6-sol",
        reasoning_effort="low",
        max_tokens=1800,
        intended_use="先產生短版床邊 cognitive aid",
    ),
    "claude_second": ModelProfile(
        key="claude_second",
        label="Claude 第二意見",
        model="anthropic/claude-sonnet-5",
        reasoning_effort="high",
        max_tokens=6000,
        intended_use="選擇性跨模型複核",
    ),
    "gemini_fast": ModelProfile(
        key="gemini_fast",
        label="Gemini Flash",
        model="google/gemini-3.7-flash",
        reasoning_effort="low",
        max_tokens=1800,
        intended_use="低延遲替代模型",
    ),
}

DEVELOPER_CONFIG_SCHEMA = 1
ALLOWED_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
MODEL_ID_PATTERN = re.compile(r"^~?[A-Za-z0-9][A-Za-z0-9._:+~-]*/[A-Za-z0-9][A-Za-z0-9._:+~-]*$")
PROMPT_WORKFLOWS = ("rounding", "admission", "emergency", "qa")
RESERVED_PROMPT_MARKERS = (
    "WARDLENS-PROMPT-V1",
    "<SYSTEM>",
    "</SYSTEM>",
    "<USER>",
    "</USER>",
)


class DeveloperConfigError(ValueError):
    pass


def validate_model_id(value: str) -> str:
    model = value.strip()
    if len(model) > 180 or not MODEL_ID_PATTERN.fullmatch(model):
        raise DeveloperConfigError(
            "模型 ID 格式不正確；請使用 provider/model（例如 openai/gpt-5.6-terra）。"
        )
    return model


def validate_reasoning_effort(value: str) -> str:
    effort = value.strip().lower()
    if effort not in ALLOWED_REASONING_EFFORTS:
        raise DeveloperConfigError(
            "reasoning effort 必須是 " + ", ".join(ALLOWED_REASONING_EFFORTS) + "。"
        )
    return effort


def validate_max_tokens(value: object) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise DeveloperConfigError("max tokens 必須是整數。")
    try:
        tokens = int(value)
    except (TypeError, ValueError) as exc:
        raise DeveloperConfigError("max tokens 必須是整數。") from exc
    if not 128 <= tokens <= 32000:
        raise DeveloperConfigError("max tokens 必須介於 128–32000。")
    return tokens


def validate_prompt(workflow: str, value: str) -> str:
    if workflow not in PROMPT_WORKFLOWS:
        raise DeveloperConfigError(f"未知的 prompt 工作：{workflow}")
    prompt = value.strip()
    if len(prompt) < 80:
        raise DeveloperConfigError(f"{workflow} prompt 過短，至少需要 80 個字元。")
    if len(prompt) > 60000:
        raise DeveloperConfigError(f"{workflow} prompt 超過 60,000 個字元。")
    if "\x00" in prompt or any(marker in prompt for marker in RESERVED_PROMPT_MARKERS):
        raise DeveloperConfigError(f"{workflow} prompt 含保留的 envelope marker。")
    return prompt


def _clean_profile_overrides(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, dict[str, object]] = {}
    for key, raw in value.items():
        if (
            key not in DEFAULT_PROFILES
            or not isinstance(raw, dict)
            or set(raw) != {"model", "reasoning_effort", "max_tokens"}
        ):
            continue
        try:
            cleaned[key] = {
                "model": validate_model_id(str(raw.get("model", ""))),
                "reasoning_effort": validate_reasoning_effort(str(raw.get("reasoning_effort", ""))),
                "max_tokens": validate_max_tokens(raw.get("max_tokens")),
            }
        except DeveloperConfigError:
            continue
    return cleaned


def _clean_prompt_overrides(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, str] = {}
    for workflow, raw in value.items():
        if not isinstance(raw, str):
            continue
        try:
            cleaned[workflow] = validate_prompt(workflow, raw)
        except DeveloperConfigError:
            continue
    return cleaned


def app_data_dir() -> Path:
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "WardLens"
    if os.getenv("XDG_STATE_HOME"):
        return Path(os.environ["XDG_STATE_HOME"]) / "wardlens"
    return Path.home() / ".local" / "state" / "wardlens"


@dataclass(slots=True)
class AppSettings:
    demo_mode: bool = True
    external_ai_enabled: bool = False
    privacy_acknowledged: bool = False
    require_zdr: bool = True
    developer_mode: bool = False
    custom_models: dict[str, str] = field(default_factory=dict)
    custom_profiles: dict[str, dict[str, object]] = field(default_factory=dict)
    custom_prompts: dict[str, str] = field(default_factory=dict)
    clipboard_clear_seconds: int = 60
    request_delay_seconds: float = 1.0
    max_requests_per_minute: int = 60
    max_patient_pages: int = 20
    max_patients: int = 200
    lab_lookback_months: int = 60

    @classmethod
    def load(cls, path: Path | None = None) -> AppSettings:
        target = path or app_data_dir() / "settings.json"
        if not target.exists():
            return cls()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(payload, dict):
            return cls()
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        values = {key: value for key, value in payload.items() if key in allowed}
        values["custom_profiles"] = _clean_profile_overrides(values.get("custom_profiles"))
        values["custom_prompts"] = _clean_prompt_overrides(values.get("custom_prompts"))
        if not isinstance(values.get("custom_models", {}), dict):
            values["custom_models"] = {}
        return cls(**values)

    def save(self, path: Path | None = None) -> Path:
        target = path or app_data_dir() / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)
        return target

    def profile(self, key: str) -> ModelProfile:
        profile = DEFAULT_PROFILES[key]
        override = self.custom_profiles.get(key, {})
        legacy_model = self.custom_models.get(key, "")
        model_value = override.get("model", legacy_model or profile.model)
        effort_value = override.get("reasoning_effort", profile.reasoning_effort)
        tokens_value = override.get("max_tokens", profile.max_tokens)
        try:
            model = validate_model_id(str(model_value))
            reasoning_effort = validate_reasoning_effort(str(effort_value))
            max_tokens = validate_max_tokens(tokens_value)
        except DeveloperConfigError:
            return profile
        return ModelProfile(
            key=profile.key,
            label=profile.label,
            model=model,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            intended_use=profile.intended_use,
        )

    def set_profile_override(
        self, key: str, *, model: str, reasoning_effort: str, max_tokens: object
    ) -> None:
        if key not in DEFAULT_PROFILES:
            raise DeveloperConfigError(f"未知的模型路由：{key}")
        candidate = {
            "model": validate_model_id(model),
            "reasoning_effort": validate_reasoning_effort(reasoning_effort),
            "max_tokens": validate_max_tokens(max_tokens),
        }
        default = DEFAULT_PROFILES[key]
        if candidate == {
            "model": default.model,
            "reasoning_effort": default.reasoning_effort,
            "max_tokens": default.max_tokens,
        }:
            self.custom_profiles.pop(key, None)
        else:
            self.custom_profiles[key] = candidate
        self.custom_models.pop(key, None)

    def set_prompt_override(self, workflow: str, prompt: str, default_prompt: str) -> None:
        cleaned = validate_prompt(workflow, prompt)
        if cleaned == default_prompt.strip():
            self.custom_prompts.pop(workflow, None)
        else:
            self.custom_prompts[workflow] = cleaned

    def developer_payload(self) -> dict[str, Any]:
        return {
            "schema_version": DEVELOPER_CONFIG_SCHEMA,
            "custom_profiles": {
                key: dict(profile) for key, profile in self.custom_profiles.items()
            },
            "custom_prompts": dict(self.custom_prompts),
        }

    def apply_developer_payload(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise DeveloperConfigError("開發者設定檔必須是 JSON object。")
        if payload.get("schema_version") != DEVELOPER_CONFIG_SCHEMA:
            raise DeveloperConfigError("不支援的開發者設定檔版本。")
        if set(payload) != {"schema_version", "custom_profiles", "custom_prompts"}:
            raise DeveloperConfigError("開發者設定檔包含未知欄位。")
        raw_profiles = payload.get("custom_profiles", {})
        raw_prompts = payload.get("custom_prompts", {})
        profiles = _clean_profile_overrides(raw_profiles)
        prompts = _clean_prompt_overrides(raw_prompts)
        if not isinstance(raw_profiles, dict) or len(profiles) != len(raw_profiles):
            raise DeveloperConfigError("模型設定包含未知或無效欄位。")
        if not isinstance(raw_prompts, dict) or len(prompts) != len(raw_prompts):
            raise DeveloperConfigError("Prompt 設定包含未知或無效欄位。")
        self.custom_profiles = profiles
        self.custom_prompts = prompts
        self.custom_models = {}

    def reset_developer_overrides(self) -> None:
        self.custom_models = {}
        self.custom_profiles = {}
        self.custom_prompts = {}
