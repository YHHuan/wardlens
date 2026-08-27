from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import requests

from wardlens.llm.prompts import PromptEnvelope
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
        self._active_lock = threading.Lock()
        self._active_response: requests.Response | None = None

    def cancel(self) -> None:
        """Best-effort hard abort of the currently streaming HTTP response."""

        with self._active_lock:
            response = self._active_response
        if response is not None:
            response.close()

    def verify_key(self, api_key: str) -> None:
        response = self.session.get(
            f"{self.base_url}/auth/key",
            headers=self._headers(api_key),
            timeout=(10, 30),
        )
        if response.status_code in {401, 403}:
            raise OpenRouterError("OpenRouter API key 無效或權限不足。")
        if not response.ok:
            raise OpenRouterError(f"OpenRouter key 檢查失敗（HTTP {response.status_code}）。")

    def ensure_zdr_available(self, model: str) -> None:
        now = time.monotonic()
        if self._zdr_models is None or now - self._zdr_checked_at > 900:
            try:
                response = self.session.get(f"{self.base_url}/endpoints/zdr", timeout=(10, 30))
                response.raise_for_status()
                entries = response.json().get("data", [])
                self._zdr_models = {str(entry.get("model_id", "")) for entry in entries}
                self._zdr_checked_at = now
            except (requests.RequestException, ValueError, AttributeError) as exc:
                raise OpenRouterError("無法確認 ZDR endpoint，依 fail-closed 原則不送出。") from exc
        if model not in self._zdr_models:
            raise OpenRouterError(f"模型 {model} 目前找不到 ZDR endpoint，已封鎖送出。")

    def complete(
        self,
        api_key: str,
        envelope: PromptEnvelope,
        profile: ModelProfile,
        *,
        require_zdr: bool = True,
    ) -> LLMResult:
        start = time.monotonic()
        if require_zdr:
            self.ensure_zdr_available(profile.model)
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(api_key),
            json=self._payload(envelope, profile, require_zdr=require_zdr, stream=False),
            timeout=(10, 180),
        )
        self._raise_for_response(response)
        try:
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
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
    ) -> Iterator[StreamEvent]:
        if require_zdr:
            self.ensure_zdr_available(profile.model)
        request_id = ""
        provider_name = ""
        emitted = False
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(api_key),
            json=self._payload(envelope, profile, require_zdr=require_zdr, stream=True),
            timeout=(10, 90),
            stream=True,
        )
        with self._active_lock:
            self._active_response = response
        try:
            with response:
                self._raise_for_response(response)
                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("error"):
                        raise OpenRouterError("OpenRouter 在串流期間回報錯誤。")
                    request_id = str(payload.get("id", request_id))
                    provider_name = str(payload.get("provider", provider_name))
                    choices = payload.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}).get("content", "")
                    if isinstance(delta, str) and delta:
                        emitted = True
                        yield StreamEvent(
                            delta=delta, request_id=request_id, provider_name=provider_name
                        )
                    usage = payload.get("usage")
                    if isinstance(usage, dict):
                        yield StreamEvent(
                            request_id=request_id, provider_name=provider_name, usage=usage
                        )
        finally:
            with self._active_lock:
                if self._active_response is response:
                    self._active_response = None
        if not emitted:
            raise OpenRouterError("模型沒有回傳可讀文字。")
        yield StreamEvent(done=True, request_id=request_id, provider_name=provider_name)

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
            "max_tokens": profile.max_tokens,
            "reasoning": {"effort": profile.reasoning_effort, "exclude": True},
            "provider": {
                "zdr": require_zdr,
                "allow_fallbacks": False,
            },
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    @staticmethod
    def _raise_for_response(response: requests.Response) -> None:
        if response.ok:
            return
        if response.status_code in {401, 403}:
            raise OpenRouterError("OpenRouter API key 無效或沒有此模型權限。")
        if response.status_code == 429:
            raise OpenRouterError("OpenRouter 暫時達到 rate limit；請稍後重試。")
        if 500 <= response.status_code:
            raise OpenRouterError("OpenRouter 或上游模型暫時無法使用。")
        raise OpenRouterError(f"OpenRouter 請求失敗（HTTP {response.status_code}）。")
