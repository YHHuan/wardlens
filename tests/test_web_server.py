from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest

from wardlens.web.server import LocalWebServer


class UserError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__("THIS_RAW_EXCEPTION_MUST_NOT_BE_SENT")
        self.message, self.status = message, status


class SyntheticController:
    def __init__(self) -> None:
        self.actions = []
        self.state_calls = 0
        self.close_calls = 0
        self.failure = None
        self.response = {"text": "合成測試：中文與 CRP 5 → 2"}

    def state(self):
        self.state_calls += 1
        if self.failure:
            raise self.failure
        return {"version": "synthetic", "epoch": 3, "answer": "中文資料"}

    def action(self, name, payload):
        self.actions.append((name, payload))
        if self.failure:
            raise self.failure
        return self.response

    def close(self):
        self.close_calls += 1


@pytest.fixture
def assets(tmp_path):
    directory = tmp_path / "assets"
    directory.mkdir()
    for name, content in {
        "index.html": "<!doctype html><meta charset=utf-8><title>合成 WardLens</title>",
        "app.css": "body { color: black; }",
        "app.js": "document.title = '合成測試';",
        "markdown.js": "function textOnly(value) { return String(value); }",
    }.items():
        (directory / name).write_text(content, encoding="utf-8")
    (directory / "not-public.txt").write_text("NOT_PUBLIC")
    (tmp_path / "outside.txt").write_text("OUTSIDE_SECRET")
    return directory


@pytest.fixture
def running(assets):
    controller = SyntheticController()
    server = LocalWebServer(controller, assets_dir=assets, idle_timeout=None).start()
    try:
        yield server, controller
    finally:
        server.stop()


def request(server, method="GET", path="/api/state", *, payload=None, headers=None, auth=True, origin=True, raw=None):
    headers = dict(headers or {})
    if auth:
        headers.setdefault("X-WardLens-Token", server.token)
    if origin:
        headers.setdefault("Origin", server.origin)
    if payload is not None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    try:
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def raw_request(server, *, method="POST", path="/api/draft", extra_headers=(), body=b"{}"):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    try:
        connection.putrequest(method, path, skip_host=True)
        for name, value in (
            ("Host", f"127.0.0.1:{server.port}"), ("Origin", server.origin),
            ("X-WardLens-Token", server.token), ("Content-Type", "application/json"),
            *extra_headers,
        ):
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_lifecycle_is_loopback_only_and_closes_controller_once(running):
    server, controller = running
    assert server.start() is server
    parsed = urlsplit(server.url)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == server.port > 0
    assert parsed.fragment == server.token and len(server.token) >= 40
    assert not parsed.query and server.token not in server.origin
    assert not server.closed and not server.wait(0)
    server.stop()
    server.stop()
    assert server.wait(1) and server.closed
    assert controller.close_calls == 1
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        server.start()
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", server.port), timeout=1):
        pass


@pytest.mark.parametrize("path,content_type", [
    ("/", "text/html"), ("/index.html", "text/html"),
    ("/assets/app.css", "text/css"), ("/assets/app.js", "text/javascript"),
    ("/assets/markdown.js", "text/javascript"),
])
def test_only_fixed_bundled_assets_are_public_and_never_cache(running, path, content_type):
    server, controller = running
    status, headers, body = request(server, path=path, auth=False, origin=False)
    assert status == 200 and headers["Content-Type"].startswith(content_type)
    assert int(headers["Content-Length"]) == len(body)
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "img-src 'none'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert not any(name.lower().startswith("access-control-") for name in headers)
    assert "Set-Cookie" not in headers
    assert server.token.encode() not in body
    assert not controller.actions and not controller.state_calls


@pytest.mark.parametrize("path", [
    "/assets/", "/assets/not-public.txt", "/outside.txt", "/../outside.txt",
    "/assets/../../outside.txt", "/assets/%2e%2e/outside.txt",
    "/assets/app.js?secret=EXAMPLE", "//example.invalid/assets/app.js", "/favicon.ico",
])
def test_no_directory_or_arbitrary_file_routes(running, path):
    server, _ = running
    status, _, body = request(server, path=path, auth=False)
    assert status == 404
    assert b"NOT_PUBLIC" not in body and b"OUTSIDE_SECRET" not in body and b"EXAMPLE" not in body


def test_authenticated_state_and_utf8_action_are_exact(running):
    server, controller = running
    status, headers, body = request(server, origin=False)
    assert status == 200 and "charset=utf-8" in headers["Content-Type"]
    assert json.loads(body) == {"ok": True, "version": "synthetic", "epoch": 3, "answer": "中文資料"}
    payload = {"data": "合成中文\nCRP 5 → 2", "revision": 1}
    status, _, body = request(server, "POST", "/api/draft", payload=payload)
    assert status == 200 and json.loads(body)["text"].endswith("5 → 2")
    assert controller.actions == [("draft", payload)]


@pytest.mark.parametrize("method,path", [("GET", "/api/state"), ("POST", "/api/login"), ("GET", "/api/unknown")])
def test_every_api_requires_header_token(running, method, path):
    server, controller = running
    for supplied in (None, "wrong", "é"):
        status, _, body = request(server, method, path, payload={} if method == "POST" else None,
                                  auth=False, headers={} if supplied is None else {"X-WardLens-Token": supplied})
        assert status == 401 and json.loads(body)["code"] == "AuthenticationRequired"
    status, _, _ = request(server, method, path + "?token=" + server.token, auth=False)
    assert status == 401
    assert not controller.state_calls and not controller.actions


@pytest.mark.parametrize("headers,expected", [
    ({"Host": "evil.example"}, "InvalidHost"),
    ({"Host": "localhost:123"}, "InvalidHost"),
    ({"Origin": "https://evil.example"}, "InvalidOrigin"),
    ({"Origin": "null"}, "InvalidOrigin"),
    ({"Sec-Fetch-Site": "cross-site"}, "CrossSiteRequest"),
    ({"Sec-Fetch-Site": "same-site"}, "CrossSiteRequest"),
])
def test_rebinding_cross_origin_and_cross_site_are_rejected(running, headers, expected):
    server, controller = running
    status, response_headers, body = request(server, headers=headers)
    assert status == 403 and json.loads(body)["code"] == expected
    assert not any(name.lower().startswith("access-control-") for name in response_headers)
    assert controller.state_calls == 0


def test_post_requires_origin_and_json_and_rejects_preflight(running):
    server, controller = running
    assert request(server, "POST", "/api/draft", payload={}, origin=False)[0] == 403
    for media in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data", "application/json;charset=iso-8859-1"):
        assert request(server, "POST", "/api/draft", payload={}, headers={"Content-Type": media})[0] == 415
    status, headers, _ = request(server, "OPTIONS", "/api/draft", auth=False, headers={"Origin": "https://evil.example"})
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers
    assert not controller.actions


@pytest.mark.parametrize("body", [b"", b"[]", b"null", b"{broken", b'{"data":"\xff"}', b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_malformed_or_ambiguous_json_never_reaches_controller(running, body):
    server, controller = running
    status, _, response = request(server, "POST", "/api/draft", raw=body, headers={"Content-Type": "application/json"})
    assert status == 400 and json.loads(response)["code"] == "InvalidJSON"
    assert not controller.actions


@pytest.mark.parametrize("extra_headers,status", [
    ([("Content-Length", "2"), ("Content-Length", "2")], 400),
    ([("Content-Length", "-1")], 400),
    ([("Content-Length", "+2")], 400),
    ([("Content-Length", "9999999999")], 413),
    ([("Content-Length", "2"), ("Transfer-Encoding", "chunked")], 400),
    ([("Content-Length", "2"), ("Host", "evil.example")], 400),
    ([("Content-Length", "2"), ("Origin", "https://evil.example")], 400),
    ([("Content-Length", "2"), ("X-WardLens-Token", "wrong")], 400),
    ([("Content-Length", "2"), ("Content-Type", "text/plain")], 400),
    ([], 411),
])
def test_http_ambiguity_and_size_limits_are_rejected(running, extra_headers, status):
    server, controller = running
    actual, _, _ = raw_request(server, extra_headers=extra_headers)
    assert actual == status
    assert not controller.actions


def test_body_cap_is_enforced_before_reading(assets):
    controller = SyntheticController()
    server = LocalWebServer(controller, assets_dir=assets, idle_timeout=None, max_body_bytes=16).start()
    try:
        status, _, body = raw_request(server, extra_headers=[("Content-Length", "17")], body=b"")
        assert status == 413 and json.loads(body)["code"] == "RequestTooLarge"
        assert not controller.actions
    finally:
        server.stop()


def test_response_cap_and_invalid_controller_output_are_safe(assets):
    controller = SyntheticController()
    server = LocalWebServer(controller, assets_dir=assets, idle_timeout=None, max_response_bytes=32).start()
    try:
        status, _, body = request(server)
        assert status == 503 and json.loads(body)["code"] == "ResponseTooLarge"
        controller.response = ["PRIVATE_INVALID_RESULT"]
        status, _, body = request(server, "POST", "/api/draft", payload={})
        assert status == 500 and b"PRIVATE_INVALID_RESULT" not in body
    finally:
        server.stop()


def test_only_explicit_user_errors_may_show_safe_messages(running, capsys):
    server, controller = running
    controller.failure = UserError("請重新確認預覽。", 409)
    status, _, body = request(server, "POST", "/api/generate", payload={})
    assert status == 409 and json.loads(body) == {"ok": False, "error": "請重新確認預覽。", "code": "UserError"}
    controller.failure = RuntimeError("SECRET_KEY_PATIENT_PATH")
    status, _, body = request(server)
    assert status == 500 and json.loads(body)["code"] == "RuntimeError"
    controller.failure = UserError("PRIVATE_UNAPPROVED_STATUS", 500)
    status, _, body2 = request(server)
    assert status == 500 and b"PRIVATE_UNAPPROVED_STATUS" not in body2
    assert b"SECRET_KEY_PATIENT_PATH" not in body and b"THIS_RAW_EXCEPTION" not in body
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_unknown_actions_methods_and_get_body_are_not_dispatched(running):
    server, controller = running
    assert request(server, "POST", "/api/not_an_action", payload={})[0] == 404
    assert request(server, "GET", "/api/state?x=1")[0] == 404
    assert request(server, "PUT", "/api/draft", payload={})[0] == 405
    assert request(server, "GET", "/api/state", raw=b"X")[0] == 400
    assert not controller.actions and not controller.state_calls


def test_authenticated_close_acknowledges_then_stops_without_deadlock(running):
    server, controller = running
    status, _, body = request(server, "POST", "/api/close", payload={})
    assert status == 200 and json.loads(body) == {"ok": True, "closed": True}
    assert server.wait(2) and controller.close_calls == 1
    assert not controller.actions


def test_idle_timeout_clears_state_and_unauthenticated_requests_do_not_extend_it(assets):
    controller = SyntheticController()
    server = LocalWebServer(controller, assets_dir=assets, idle_timeout=0.3).start()
    try:
        time.sleep(0.15)
        assert request(server, auth=False)[0] == 401
        assert server.wait(0.3)
        assert controller.close_calls == 1
    finally:
        server.stop()


def test_authenticated_activity_extends_idle_lifetime(assets):
    controller = SyntheticController()
    server = LocalWebServer(controller, assets_dir=assets, idle_timeout=0.5).start()
    try:
        for _ in range(3):
            time.sleep(0.2)
            assert request(server)[0] == 200
        assert not server.closed
        assert server.wait(1)
    finally:
        server.stop()


def test_worker_limit_rejects_excess_connections_then_recovers(assets):
    controller = SyntheticController()
    entered, release = threading.Event(), threading.Event()
    original = controller.state

    def held_state():
        entered.set()
        assert release.wait(2)
        return original()

    controller.state = held_state
    server = LocalWebServer(controller, assets_dir=assets, idle_timeout=None, max_workers=1).start()
    first_result = []
    worker = threading.Thread(target=lambda: first_result.append(request(server)))
    try:
        worker.start()
        assert entered.wait(1)
        status, _, _ = request(server)
        assert status == 503
        release.set()
        worker.join(2)
        assert not worker.is_alive() and first_result[0][0] == 200
        # Receiving the final response byte can precede the worker's socket
        # cleanup by a scheduling tick. Capacity must return promptly, not at
        # an assumed ordering between the client and the server threads.
        deadline = time.monotonic() + 1
        recovered = False
        while time.monotonic() < deadline:
            if request(server)[0] == 200:
                recovered = True
                break
            time.sleep(0.01)
        assert recovered
    finally:
        release.set()
        worker.join(2)
        server.stop()


def test_partial_request_timeout_releases_worker(assets):
    controller = SyntheticController()
    server = LocalWebServer(controller, assets_dir=assets, idle_timeout=None, request_timeout=0.15, max_workers=1).start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=1) as client:
            client.sendall(b"POST /api/draft HTTP/1.1\r\n")
            time.sleep(0.3)
            assert client.recv(1024) == b""
        assert request(server)[0] == 200
        assert not controller.actions
    finally:
        server.stop()


def test_expect_continue_and_oversized_headers_fail_without_dispatch(running):
    server, controller = running
    status, _, body = raw_request(server, extra_headers=[("Expect", "100-continue"), ("Content-Length", "2")])
    assert status == 417 and json.loads(body)["code"] == "InvalidHeaders"
    status, _, _ = request(server, headers={"X-Synthetic-Padding": "x" * 17000})
    assert status == 431
    assert not controller.actions and not controller.state_calls


def test_stop_still_finishes_when_controller_cleanup_raises(assets, capsys):
    controller = SyntheticController()

    def fail_close():
        controller.close_calls += 1
        raise RuntimeError("PRIVATE_CLEANUP_DETAIL")

    controller.close = fail_close
    server = LocalWebServer(controller, assets_dir=assets, idle_timeout=None).start()
    server.stop()
    assert server.wait(1) and controller.close_calls == 1
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_head_assets_have_no_body_and_api_head_cannot_leak_state(running):
    server, controller = running
    status, headers, body = request(server, "HEAD", "/assets/app.js", auth=False)
    assert status == 200 and int(headers["Content-Length"]) > 0 and not body
    assert request(server, "HEAD", "/api/state", auth=False)[0] == 401
    assert request(server, "HEAD", "/api/state")[0] == 405
    assert not controller.state_calls


@pytest.mark.parametrize("kwargs", [
    {"idle_timeout": 0}, {"idle_timeout": float("nan")}, {"request_timeout": float("inf")},
    {"request_timeout": None}, {"idle_timeout": "10"},
    {"max_workers": True}, {"max_workers": 65}, {"max_body_bytes": -1},
])
def test_invalid_resource_bounds_are_rejected(kwargs):
    with pytest.raises(ValueError):
        LocalWebServer(SyntheticController(), **kwargs)
