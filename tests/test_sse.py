import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
import requests

from wardlens.llm.openrouter import OpenRouterClient, OpenRouterError
from wardlens.llm.prompts import PromptEnvelope
from wardlens.llm.sse import SSEProtocolError, iter_sse_data
from wardlens.models import ModelProfile

TEXT = "先確認呼吸，這是合成測試。中文🙂 café\u0085保留\u2028全部\u2029文字"
PROFILE = ModelProfile("test", "Test", "synthetic/model", "low", 1200, "test")


def stream_bytes(text=TEXT, ending="\n"):
    # Real UTF-8, NOT ASCII-escaped JSON (which concealed the v0.4 bug).
    content = json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
    return ("\ufeff: OPENROUTER PROCESSING" + ending * 2
            + "data: " + content + ending * 2
            + 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}' + ending * 2
            + 'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
              '"usage":{"completion_tokens":25}}' + ending * 2
            + "data: [DONE]" + ending * 2).encode("utf-8")


class FragmentedBytes(io.BytesIO):
    def __init__(self, data, fragment):
        super().__init__(data)
        self.fragment = fragment

    def read(self, size=-1):
        return super().read(min(size, self.fragment) if size >= 0 else self.fragment)

    def release_conn(self):
        self.close()


def response_for(data, fragment=512, content_type="text/event-stream", status=200):
    response = requests.Response()
    response.status_code = status
    response.headers["Content-Type"] = content_type
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    response.raw = FragmentedBytes(data, fragment)
    return response


def consume(response):
    client = OpenRouterClient()
    with patch.object(client, "validate_profile"), patch.object(
        client.session, "post", return_value=response
    ) as post:
        try:
            return list(client.stream("synthetic-key", PromptEnvelope("sys", "user"), PROFILE))
        finally:
            assert post.call_count == 1  # Never silently retry paid inference.
            assert client._active_response is None
            assert response.raw.closed


def test_old_requests_decoder_reproduces_chinese_corruption():
    response = response_for(stream_bytes())
    assert response.encoding == "ISO-8859-1"
    first_data = next(line for line in response.iter_lines(decode_unicode=True)
                      if line.startswith("data: "))
    with pytest.raises(json.JSONDecodeError):
        json.loads(first_data[6:])


@pytest.mark.parametrize("fragment", [1, 2, 3, 7, 512])
@pytest.mark.parametrize("ending", ["\n", "\r", "\r\n"])
def test_real_requests_bytes_preserve_chinese_and_unicode(fragment, ending):
    events = consume(response_for(stream_bytes(ending=ending), fragment))
    assert "".join(event.delta for event in events) == TEXT
    assert sum(event.done for event in events) == 1
    assert events[-1].done
    assert any(event.usage == {"completion_tokens": 25} for event in events)


def test_sse_uses_utf8_even_if_proxy_claims_a_different_charset():
    events = consume(response_for(stream_bytes(), content_type="text/event-stream; charset=latin-1"))
    assert "".join(event.delta for event in events) == TEXT


def test_multiline_json_event_reaches_client_losslessly():
    raw = stream_bytes().replace(b'{"choices": [{"delta":', b'{"choices": [\ndata: {"delta":', 1)
    events = consume(response_for(raw, fragment=1))
    assert "".join(event.delta for event in events) == TEXT
    assert events[-1].done


def test_multiline_data_comments_fields_and_unterminated_event():
    raw = b': heartbeat\r\nevent: message\r\nid: 1\r\ndata: {"a":\r\ndata:  2}\r\n\r\ndata: discarded\n'
    assert list(iter_sse_data(bytes([b]) for b in raw)) == ['{"a":\n 2}']
    assert list(iter_sse_data([b"data\n\ndata: tail"])) == [""]


@pytest.mark.parametrize("data,code", [
    (b'data: {"choices":\xff}\n\n', "WL-SSE-UTF8"),
    (b'data: {"choices":"\xe5\x85', "WL-SSE-UTF8"),
    (b'data: broken-json\n\n', "WL-SSE-JSON"),
    (b'data: []\n\n', "WL-SSE-SHAPE"),
    (b'data: {"choices":[1]}\n\n', "WL-SSE-SHAPE"),
    (b'data: {"choices":{}}\n\n', "WL-SSE-SHAPE"),
    (b'data: {"choices":[{"delta":[]}]}\n\n', "WL-SSE-SHAPE"),
    (b'data: {"choices":[{"delta":{"content":[]}}]}\n\n', "WL-SSE-SHAPE"),
    (b'data: {"error":{"message":"synthetic-private-error"}}\n\n', "回報錯誤"),
    (stream_bytes().removesuffix(b"\n\n"), "提前結束"),
])
def test_invalid_or_incomplete_response_is_never_completed(data, code):
    with pytest.raises(OpenRouterError, match=code) as error:
        consume(response_for(data, fragment=3))
    assert "synthetic-private-error" not in str(error.value)


def test_later_usage_stop_cannot_hide_prior_truncation():
    raw = stream_bytes().replace(b'"finish_reason":"stop"', b'"finish_reason":"length"', 1)
    with pytest.raises(OpenRouterError, match="tokens 上限"):
        consume(response_for(raw))


@pytest.mark.parametrize("status,code", [(200, "WL-SSE-TYPE"), (401, "key"), (429, "rate limit")])
def test_network_error_pages_are_not_treated_as_answers(status, code):
    with pytest.raises(OpenRouterError, match=code) as error:
        consume(response_for(b"<html>private-proxy-body</html>", content_type="text/html", status=status))
    assert "private-proxy-body" not in str(error.value)


@pytest.mark.parametrize("raw", [b"data: xxxxxxxxxxxxxxxxx", b"data: 1234\ndata: 5678\ndata: 90\n\n"])
def test_line_and_event_limits(raw):
    with pytest.raises(SSEProtocolError):
        list(iter_sse_data([raw], maximum=10))


def test_real_loopback_http_stream_round_trip():
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests_seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = stream_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")  # no charset, like OpenRouter
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for index in range(0, len(body), 7):
                self.wfile.write(body[index:index + 7])
                self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = OpenRouterClient()
    client.session.trust_env = False
    client.base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch.object(client, "validate_profile"):
            events = list(client.stream("synthetic-key", PromptEnvelope("sys", "合成問題"), PROFILE))
        assert "".join(event.delta for event in events) == TEXT
        assert events[-1].done
        assert len(requests_seen) == 1
        assert requests_seen[0]["messages"][1]["content"] == "合成問題"
    finally:
        client.session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
