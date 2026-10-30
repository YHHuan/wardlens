from __future__ import annotations

import json
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
    assert payload["provider"] == {
        "zdr": True,
        "allow_fallbacks": False,
        "data_collection": "deny",
        "require_parameters": True,
    }
    assert payload["reasoning"] == {"effort": "low", "exclude": True}


def test_stream_payload_requests_usage() -> None:
    payload = OpenRouterClient._payload(
        PromptEnvelope("system", "user"),
        _profile(),
        require_zdr=True,
        stream=True,
    )
    assert payload["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize("stream", [True, False])
def test_default_profiles_do_not_send_an_application_output_cap(stream):
    from wardlens.config import DEFAULT_PROFILES

    for profile in DEFAULT_PROFILES.values():
        payload = OpenRouterClient._payload(PromptEnvelope("system", "user"), profile, require_zdr=True, stream=stream)
        assert "max_tokens" not in payload
        assert "max_completion_tokens" not in payload
    explicit = OpenRouterClient._payload(PromptEnvelope("system", "user"), _profile(), require_zdr=True, stream=stream)
    assert explicit["max_tokens"] == 1200


def test_cancel_during_metadata_validation_prevents_paid_post(monkeypatch):
    import threading
    from unittest.mock import Mock

    stopped = threading.Event()
    client = OpenRouterClient()
    monkeypatch.setattr(client, "validate_profile", lambda *args, **kwargs: stopped.set())
    post = Mock(side_effect=AssertionError("must not send inference"))
    monkeypatch.setattr(client, "_post_completion", post)
    with pytest.raises(OpenRouterError, match="沒有送出"):
        list(client.stream("synthetic-key", PromptEnvelope("system", "user"), _profile(), cancel_event=stopped))
    assert not post.called


class _Response:
    ok = True
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_content(self, *, chunk_size, decode_unicode):
        assert not decode_unicode
        for line in self.payload.get("lines", []):
            yield (line + "\n\n").encode("utf-8")


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


class _CatalogSession:
    def __init__(self, response=None):
        self.gets = []
        self.posts = []
        self.response = response

    def get(self, url, **kwargs):
        from wardlens.config import DEFAULT_PROFILES

        self.gets.append((url, kwargs))
        if url.endswith("/auth/key"):
            return _Response({"data": {}})
        if url.endswith("/endpoints/zdr"):
            return _Response({"data": [{"model_id": p.model} for p in DEFAULT_PROFILES.values()]})
        assert url.endswith("/models")
        return _Response(
            {
                "data": [
                    {
                        "id": p.model,
                        "supported_parameters": ["reasoning", "max_tokens"],
                        "reasoning": {"mandatory": True, "supported_efforts": ["low", "high"]},
                    }
                    for p in DEFAULT_PROFILES.values()
                ]
            }
        )

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        assert self.response is not None
        return self.response


def test_connection_check_is_get_only_and_validates_all_routes() -> None:
    from wardlens.config import DEFAULT_PROFILES

    session = _CatalogSession()
    report = OpenRouterClient(session).check_connection(
        "synthetic-key",
        list(DEFAULT_PROFILES.values()),
        require_zdr=True,
    )
    assert not session.posts
    assert len(session.gets) == 3
    assert "待修正" not in report
    assert "不代表帳戶餘額" in report
    assert "synthetic-key" not in report
    for url, kwargs in session.gets:
        if not url.endswith("/auth/key"):
            assert "headers" not in kwargs  # no key on public catalog calls
        assert "json" not in kwargs


@pytest.mark.parametrize("effort", ["none", "max"])
def test_incompatible_effort_blocks_before_paid_request(effort) -> None:
    from dataclasses import replace

    from wardlens.config import DEFAULT_PROFILES

    session = _CatalogSession()
    profile = replace(DEFAULT_PROFILES["fast"], reasoning_effort=effort)
    with pytest.raises(OpenRouterError, match="不支援"):
        OpenRouterClient(session).complete("test", PromptEnvelope("sys", "user"), profile)
    assert not session.posts


def _stream_lines(reason="stop", done=True, text="synthetic answer"):
    if text:
        yield "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})
    if reason:
        yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": reason}]})
    yield 'data: {"choices": [], "usage": {"completion_tokens": 10}}'
    if done:
        yield "data: [DONE]"


@pytest.mark.parametrize(
    "reason,done,text",
    [
        ("length", True, ""),
        ("length", True, "partial"),
        (None, True, "partial"),
        ("stop", False, "partial"),
        ("content_filter", True, "partial"),
    ],
)
def test_incomplete_stream_never_emits_done(reason, done, text) -> None:
    from wardlens.config import DEFAULT_PROFILES

    session = _CatalogSession(_Response({"lines": list(_stream_lines(reason, done, text))}))
    events = []
    with pytest.raises(OpenRouterError):
        for event in OpenRouterClient(session).stream(
            "test",
            PromptEnvelope("sys", "user"),
            DEFAULT_PROFILES["fast"],
        ):
            events.append(event)
    assert not any(e.done for e in events)


def test_complete_stream_preserves_usage_only_chunk() -> None:
    from wardlens.config import DEFAULT_PROFILES

    session = _CatalogSession(_Response({"lines": list(_stream_lines())}))
    events = list(
        OpenRouterClient(session).stream(
            "test",
            PromptEnvelope("sys", "user"),
            DEFAULT_PROFILES["fast"],
        )
    )
    assert events[-1].done
    assert any(e.usage == {"completion_tokens": 10} for e in events)


def test_nonstream_truncation_is_not_a_completed_answer() -> None:
    from wardlens.config import DEFAULT_PROFILES

    session = _CatalogSession(
        _Response(
            {
                "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}],
            }
        )
    )
    with pytest.raises(OpenRouterError, match="tokens 上限"):
        OpenRouterClient(session).complete(
            "test", PromptEnvelope("sys", "user"), DEFAULT_PROFILES["fast"]
        )
