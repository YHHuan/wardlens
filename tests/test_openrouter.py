from __future__ import annotations

import threading
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest

from wardlens.llm.oauth import OpenRouterOAuth
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


def test_zdr_registry_rejects_malformed_catalog() -> None:
    client = OpenRouterClient(session=_Session({"data": {"model_id": "not-a-list"}}))
    with pytest.raises(OpenRouterError, match="無法確認 ZDR"):
        client.ensure_zdr_available("openai/gpt-5.6-terra")


def test_live_picker_returns_sorted_zdr_models() -> None:
    client = OpenRouterClient(
        session=_Session(
            {
                "data": [
                    {"model_id": "openai/gpt-5.6-terra"},
                    {"model_id": "anthropic/claude-sonnet-5"},
                ]
            }
        )
    )
    assert client.available_models() == [
        "anthropic/claude-sonnet-5",
        "openai/gpt-5.6-terra",
    ]


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


def test_oauth_pkce_and_authorization_link() -> None:
    verifier, challenge = OpenRouterOAuth.create_pkce()
    assert 43 <= len(verifier) <= 128
    assert "=" not in challenge
    link = OpenRouterOAuth.authorization_link("http://127.0.0.1:54321/callback/nonce", challenge)
    assert link.startswith("https://openrouter.ai/auth?")
    assert "code_challenge_method=S256" in link
    assert "127.0.0.1%3A54321" in link


def test_oauth_exchanges_code_without_exposing_key() -> None:
    class OAuthResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"key": "sk-or-v1-" + "x" * 64}

    class OAuthSession:
        request_data = ""

        def post(self, _url, *, headers, data, timeout):
            assert headers == {"Content-Type": "application/json"}
            assert timeout == (10, 30)
            self.request_data = data
            return OAuthResponse()

    session = OAuthSession()
    key = OpenRouterOAuth(session=session).exchange("one-time-code", "verifier")
    assert key.startswith("sk-or-v1-")
    request = __import__("json").loads(session.request_data)
    assert request["code"] == "one-time-code"
    assert "key" not in request


def test_oauth_loopback_connect_uses_one_time_code(monkeypatch) -> None:
    class OAuthResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"key": "sk-or-v1-" + "y" * 64}

    class OAuthSession:
        def post(self, _url, *, headers, data, timeout):
            assert __import__("json").loads(data)["code"] == "callback-code"
            return OAuthResponse()

    def fake_open(url: str, *, new: int) -> bool:
        assert new == 1
        callback = parse_qs(urlparse(url).query)["callback_url"][0]

        def visit_callback() -> None:
            with urlopen(callback + "?code=callback-code", timeout=2) as response:
                assert response.status == 200

        threading.Thread(target=visit_callback, daemon=True).start()
        return True

    monkeypatch.setattr("wardlens.llm.oauth.webbrowser.open", fake_open)
    key = OpenRouterOAuth(session=OAuthSession()).connect(timeout_seconds=2)
    assert key.startswith("sk-or-v1-")
