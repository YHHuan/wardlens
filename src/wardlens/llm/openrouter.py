from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import requests

from wardlens.llm.prompts import PromptEnvelope
from wardlens.llm.sse import SSEProtocolError, iter_sse_data
from wardlens.models import LLMResult, ModelProfile


class OpenRouterError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class StreamEvent:
    delta: str = ""
    done: bool = False
    request_id: str = ""
    provider_name: str = ""
    usage: dict[str, object] | None = None


class OpenRouterClient:
    base_url = "https://openrouter.ai/api/v1"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._zdr_models: set[str] | None = None
        self._zdr_checked_at = 0.0
        self._models: dict[str, dict] | None = None
        self._models_checked_at = 0.0
        self._active_lock = threading.Lock()
        self._active_response: requests.Response | None = None

    def cancel(self) -> None:
        """Best-effort hard abort of the currently streaming HTTP response."""

        with self._active_lock:
            response = self._active_response
        if response is not None:
            response.close()

    def verify_key(self, api_key: str) -> None:
        try:
            response = self.session.get(
                f"{self.base_url}/auth/key",
                headers=self._headers(api_key),
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise OpenRouterError("無法連線 OpenRouter；請檢查網路、防火牆或稍後重試。") from exc
        if response.status_code in {401, 403}:
            raise OpenRouterError("OpenRouter API key 無效或權限不足。")
        if not response.ok:
            raise OpenRouterError(f"OpenRouter key 檢查失敗（HTTP {response.status_code}）。")
        try:
            if not isinstance(response.json().get("data"), dict):
                raise ValueError("Invalid key metadata")
        except (ValueError, AttributeError) as exc:
            raise OpenRouterError(
                "OpenRouter key 回應格式不符，請檢查是否遭網路登入頁攔截。"
            ) from exc

    def ensure_zdr_available(self, model: str) -> None:
        now = time.monotonic()
        if self._zdr_models is None or now - self._zdr_checked_at > 900:
            self._refresh_zdr_models(now)
        if model not in self._zdr_models:
            raise OpenRouterError(f"模型 {model} 目前找不到 ZDR endpoint，已封鎖送出。")

    def available_models(self, *, zdr_only: bool = True) -> list[str]:
        """Return live model IDs for the developer picker; no clinical data is sent."""

        if zdr_only:
            self._refresh_zdr_models(time.monotonic())
            return sorted(model for model in self._zdr_models or set() if model)
        return sorted(self.model_catalog())

    def model_catalog(self, *, refresh: bool = False) -> dict[str, dict]:
        now = time.monotonic()
        if not refresh and self._models is not None and now - self._models_checked_at < 900:
            return self._models
        try:
            response = self.session.get(f"{self.base_url}/models", timeout=(10, 30))
            response.raise_for_status()
            entries = response.json().get("data", [])
            if not isinstance(entries, list):
                raise ValueError("models.data is not a list")
            self._models = {
                str(entry["id"]): entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("id")
            }
            self._models_checked_at = now
            return self._models
        except (requests.RequestException, ValueError, AttributeError) as exc:
            raise OpenRouterError("無法取得 OpenRouter 最新模型清單。") from exc

    def validate_profile(self, profile: ModelProfile, *, require_zdr: bool = True) -> None:
        entry = self.model_catalog().get(profile.model)
        if entry is None:
            raise OpenRouterError(f"模型 {profile.model} 不在目前目錄；請在開發者模式更新 ID。")
        reasoning = entry.get("reasoning") or {}
        efforts = reasoning.get("supported_efforts")
        if reasoning.get("mandatory") and profile.reasoning_effort == "none":
            raise OpenRouterError(f"{profile.model} 不支援關閉推理；請選 low 或其他支援等級。")
        if isinstance(efforts, list) and profile.reasoning_effort not in efforts:
            raise OpenRouterError(f"{profile.model} 不支援選取的 reasoning；請調整開發者設定。")
        parameters = entry.get("supported_parameters", [])
        if "reasoning" not in parameters and "reasoning_effort" not in parameters:
            raise OpenRouterError("此模型未宣告支援 reasoning 參數，請選擇相容模型。")
        if require_zdr:
            self.ensure_zdr_available(profile.model)

    def check_connection(
        self, api_key: str, profiles: list[ModelProfile], *, require_zdr: bool
    ) -> str:
        """GET-only diagnostics: no prompt, clinical text or paid generation."""
        self.verify_key(api_key)
        self.model_catalog(refresh=True)
        if require_zdr:
            self._refresh_zdr_models(time.monotonic())
        lines = ["API key 驗證成功；以下僅檢查公開模型／ZDR 目錄，未送出病歷、未生成。"]
        for profile in profiles:
            try:
                self.validate_profile(profile, require_zdr=require_zdr)
            except OpenRouterError as exc:
                lines.append(f"待修正 {profile.key}：{exc}")
            else:
                lines.append(f"目錄符合 {profile.key}：{profile.model}")
        lines.append("不代表帳戶餘額、模型權限或實際生成已通過；請先用合成 Demo 測試。")
        return "\n".join(lines)

    def _refresh_zdr_models(self, now: float) -> None:
        try:
            response = self.session.get(f"{self.base_url}/endpoints/zdr", timeout=(10, 30))
            response.raise_for_status()
            entries = response.json().get("data", [])
            if not isinstance(entries, list):
                raise ValueError("endpoints.data is not a list")
            self._zdr_models = {
                str(entry.get("model_id", "")) for entry in entries if isinstance(entry, dict)
            }
            self._zdr_checked_at = now
        except (requests.RequestException, ValueError, AttributeError) as exc:
            raise OpenRouterError("無法確認 ZDR endpoint，依 fail-closed 原則不送出。") from exc

    def complete(
        self,
        api_key: str,
        envelope: PromptEnvelope,
        profile: ModelProfile,
        *,
        require_zdr: bool = True,
    ) -> LLMResult:
        start = time.monotonic()
        self.validate_profile(profile, require_zdr=require_zdr)
        response = self._post_completion(
            api_key, envelope, profile, require_zdr=require_zdr, stream=False
        )
        self._raise_for_response(response)
        try:
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
            self._check_finish_reason(payload["choices"][0].get("finish_reason"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError("模型回應格式不完整。") from exc
        if not isinstance(text, str) or not text.strip():
            raise OpenRouterError("模型沒有回傳可讀文字。")
        return LLMResult(
            text=text.strip(),
            model=str(payload.get("model", profile.model)),
            request_id=str(payload.get("id", "")),
            provider_name=str(payload.get("provider", "")),
            latency_seconds=time.monotonic() - start,
            usage=payload.get("usage", {}) if isinstance(payload.get("usage"), dict) else {},
        )

    def stream(
        self,
        api_key: str,
        envelope: PromptEnvelope,
        profile: ModelProfile,
        *,
        require_zdr: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[StreamEvent]:
        if cancel_event is not None and cancel_event.is_set():
            raise OpenRouterError("生成已停止；沒有送出新的生成請求。")
        self.validate_profile(profile, require_zdr=require_zdr)
        if cancel_event is not None and cancel_event.is_set():
            raise OpenRouterError("生成已停止；沒有送出新的生成請求。")
        request_id = ""
        provider_name = ""
        emitted = False
        finish_reason = None
        saw_done = False
        response = self._post_completion(
            api_key, envelope, profile, require_zdr=require_zdr, stream=True
        )
        with self._active_lock:
            self._active_response = response
        try:
            with response:
                if cancel_event is not None and cancel_event.is_set():
                    raise OpenRouterError("生成已停止；內容不完整。")
                self._raise_for_response(response)
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type and content_type != "text/event-stream":
                    raise OpenRouterError(
                        "回應不是預期的串流格式（WL-SSE-TYPE）；請檢查網路／代理伺服器或稍後重試。"
                    )
                for data in iter_sse_data(response.iter_content(chunk_size=512, decode_unicode=False)):
                    data = data.strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        saw_done = True
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise OpenRouterError(
                            "收到無法解析的串流事件（WL-SSE-JSON）；本次內容不完整。"
                            "請勿依片段執行處置，可回報此代碼；不需提供病歷。"
                        ) from exc
                    if not isinstance(payload, dict):
                        raise OpenRouterError("串流事件結構不符（WL-SSE-SHAPE）；內容不完整。")
                    if payload.get("error"):
                        raise OpenRouterError("OpenRouter 在串流期間回報錯誤。")
                    request_id = str(payload.get("id", request_id))
                    provider_name = str(payload.get("provider", provider_name))
                    choices = payload.get("choices", [])
                    if not isinstance(choices, list):
                        raise OpenRouterError("串流選項結構不符（WL-SSE-SHAPE）；內容不完整。")
                    usage = payload.get("usage")
                    if isinstance(usage, dict):
                        yield StreamEvent(
                            request_id=request_id, provider_name=provider_name, usage=usage
                        )
                    if not choices:
                        continue
                    if not isinstance(choices[0], dict):
                        raise OpenRouterError("串流選項結構不符（WL-SSE-SHAPE）；內容不完整。")
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
                        # A later usage event must never erase a truncation error.
                        self._check_finish_reason(finish_reason)
                    delta_fields = choices[0].get("delta")
                    if delta_fields is None:
                        delta_fields = {}
                    if not isinstance(delta_fields, dict):
                        raise OpenRouterError("串流文字結構不符（WL-SSE-SHAPE）；內容不完整。")
                    delta = delta_fields.get("content", "")
                    if delta is not None and not isinstance(delta, str):
                        raise OpenRouterError("串流文字結構不符（WL-SSE-SHAPE）；內容不完整。")
                    if isinstance(delta, str) and delta:
                        emitted = True
                        yield StreamEvent(
                            delta=delta, request_id=request_id, provider_name=provider_name
                        )
        except UnicodeDecodeError as exc:
            raise OpenRouterError(
                "串流不是有效的 UTF-8 文字（WL-SSE-UTF8）；內容不完整，請稍後重試。"
            ) from exc
        except SSEProtocolError as exc:
            raise OpenRouterError("串流分段超過安全限制（WL-SSE-FRAME）；內容不完整。") from exc
        except requests.RequestException as exc:
            raise OpenRouterError("OpenRouter 串流逾時或連線中斷；以上內容不完整。") from exc
        finally:
            with self._active_lock:
                if self._active_response is response:
                    self._active_response = None
        self._check_finish_reason(finish_reason)
        if not saw_done:
            raise OpenRouterError("串流連線提前結束；以上內容不完整。")
        if not emitted:
            raise OpenRouterError("模型沒有回傳可讀文字。")
        yield StreamEvent(done=True, request_id=request_id, provider_name=provider_name)

    def _post_completion(
        self,
        api_key: str,
        envelope: PromptEnvelope,
        profile: ModelProfile,
        *,
        require_zdr: bool,
        stream: bool,
    ):
        try:
            return self.session.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(api_key),
                json=self._payload(envelope, profile, require_zdr=require_zdr, stream=stream),
                timeout=(10, 90 if stream else 180),
                stream=stream,
            )
        except requests.RequestException as exc:
            raise OpenRouterError("無法連線 OpenRouter 或等待逾時；請檢查網路後重新嘗試。") from exc

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        key = api_key.strip()
        if not key:
            raise OpenRouterError("尚未設定 OpenRouter API key。")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/YHHuan/wardlens",
            "X-OpenRouter-Title": "WardLens",
        }

    @staticmethod
    def _payload(
        envelope: PromptEnvelope,
        profile: ModelProfile,
        *,
        require_zdr: bool,
        stream: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": profile.model,
            "messages": envelope.messages(),
            "reasoning": {"effort": profile.reasoning_effort, "exclude": True},
            "provider": {
                "zdr": require_zdr,
                "allow_fallbacks": False,
                "data_collection": "deny",
                "require_parameters": True,
            },
            "stream": stream,
        }
        if profile.max_tokens is not None:
            payload["max_tokens"] = profile.max_tokens
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    @staticmethod
    def _check_finish_reason(reason: object) -> None:
        if reason == "stop":
            return
        if reason == "length":
            raise OpenRouterError(
                "已達本次模型的 tokens 上限，回答可能被截斷或推理用完額度。"
                "供應商預設仍有上限；可在開發者模式指定較高額度，或縮小問題／降低 reasoning 再重試。"
                "不可把本次片段當成完整處置。"
            )
        raise OpenRouterError("模型沒有正常完成文字回答；內容不完整，請重新生成或更換模型。")

    @staticmethod
    def _raise_for_response(response: requests.Response) -> None:
        if response.ok:
            return
        if response.status_code in {401, 403}:
            raise OpenRouterError("OpenRouter API key 無效或沒有此模型權限。")
        if response.status_code == 402:
            raise OpenRouterError(
                "OpenRouter 餘額或 key 額度不足；請至帳戶確認 credits／用量上限。"
            )
        if response.status_code in {400, 404}:
            raise OpenRouterError("模型或參數不相容；請檢查模型 ID、reasoning 與 tokens 設定。")
        if response.status_code == 429:
            raise OpenRouterError("OpenRouter 暫時達到 rate limit；請稍後重試。")
        if 500 <= response.status_code:
            raise OpenRouterError("OpenRouter 或上游模型暫時無法使用。")
        raise OpenRouterError(f"OpenRouter 請求失敗（HTTP {response.status_code}）。")
