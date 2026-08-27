from __future__ import annotations

import pytest

from wardlens.llm.openrouter import OpenRouterClient, OpenRouterError
from wardlens.llm.prompts import PromptEnvelope
from wardlens.models import ModelProfile


def _profile(model: str = "openai/gpt-5.6-terra") -> ModelProfile:
    return ModelProfile("fast", "Fast", model, "low", 1200, "test")


def test_nonstream_payload_omits_null_stream_options() -> None:
    payload = OpenRouterClient._payload(
        PromptEnvelope("system", "user"),
        _profile(),
        require_zdr=True,
        stream=False,
    )
    assert "stream_options" not in payload
    assert payload["provider"] == {"zdr": True, "allow_fallbacks": False}
    assert payload["reasoning"] == {"effort": "low", "exclude": True}


def test_stream_payload_requests_usage() -> None:
    payload = OpenRouterClient._payload(
        PromptEnvelope("system", "user"),
        _profile(),
        require_zdr=True,
        stream=True,
    )
    assert payload["stream_options"] == {"include_usage": True}


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class _Session:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def get(self, *_args, **_kwargs) -> _Response:
        return _Response(self.payload)


def test_zdr_registry_fails_closed_for_missing_model() -> None:
    client = OpenRouterClient(session=_Session({"data": [{"model_id": "other/model"}]}))
    with pytest.raises(OpenRouterError, match="找不到 ZDR"):
        client.ensure_zdr_available("openai/gpt-5.6-terra")


def test_zdr_registry_accepts_available_model() -> None:
    client = OpenRouterClient(session=_Session({"data": [{"model_id": "openai/gpt-5.6-terra"}]}))
    client.ensure_zdr_available("openai/gpt-5.6-terra")


def test_cancel_closes_active_response() -> None:
    class ActiveResponse:
        closed = False

        def close(self) -> None:
            self.closed = True

    client = OpenRouterClient()
    response = ActiveResponse()
    client._active_response = response
    client.cancel()
    assert response.closed
