from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
    custom_models: dict[str, str] = field(default_factory=dict)
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
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in payload.items() if key in allowed})

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
        custom_model = self.custom_models.get(key, "").strip()
        if not custom_model:
            return profile
        return ModelProfile(
            key=profile.key,
            label=profile.label,
            model=custom_model,
            reasoning_effort=profile.reasoning_effort,
            max_tokens=profile.max_tokens,
            intended_use=profile.intended_use,
        )
