"""Bounded, authenticated loopback transport for the portable browser UI.

This is a local application endpoint, not a LAN/public web server. It exposes
only fixed bundled assets and controller actions, never a filesystem directory.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
}
_ACTIONS = frozenset({
    "login", "search", "select", "load", "logout", "draft", "prepare", "repair",
    "generate", "cancel", "copy_prompt", "settings", "key_save", "key_delete",
    "oauth", "connection", "developer_get", "developer_save", "models",
    "browser_probe", "browser_prepare", "export", "close",
})
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'none'; font-src 'none'; object-src 'none'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'none'"
)
_SAFE_ERROR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


class Controller(Protocol):
    def state(self) -> dict[str, Any]: ...

    def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


class _RequestRejected(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


class _BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    request_queue_size = 8

    def __init__(self, owner: LocalWebServer) -> None:
        self.owner = owner
        self._slots = threading.BoundedSemaphore(owner.max_workers)
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.owner.request_timeout)
        return connection, address

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            # Do not create another worker or block waiting for a slot.
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                    b"Cache-Control: no-store\r\nContent-Length: 0\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        with self._connections_lock:
            self._connections.add(request)
        try:
            super().process_request(request, client_address)
        except Exception:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()

    def close_connections(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def handle_error(self, request, client_address) -> None:
        # Standard socketserver tracebacks can include request data. Never log it.
        return


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WardLens"
    sys_version = ""

    @property
    def owner(self) -> LocalWebServer:
        return self.server.owner

    def log_message(self, _format: str, *args: object) -> None:
        # No URLs, headers, bodies, credentials or clinical content in access logs.
        return

    def _one_header(self, name: str) -> str | None:
        values = self.headers.get_all(name, [])
        if len(values) > 1:
            raise _RequestRejected(400, "InvalidHeaders", "請求標頭格式無效。")
        return values[0] if values else None

    def _guard(self, *, api: bool, post: bool = False) -> None:
        if sum(len(key) + len(value) for key, value in self.headers.items()) > 16384:
            raise _RequestRejected(431, "HeadersTooLarge", "請求標頭過大。")
        if self._one_header("Host") != f"127.0.0.1:{self.owner.port}":
            raise _RequestRejected(403, "InvalidHost", "只允許這次啟動的本機位址。")
        origin = self._one_header("Origin")
        if (post and origin is None) or (origin is not None and origin != self.owner.origin):
            raise _RequestRejected(403, "InvalidOrigin", "請從 WardLens 本機視窗操作。")
        fetch_site = self._one_header("Sec-Fetch-Site")
        if fetch_site is not None and fetch_site not in {"same-origin", "none"}:
            raise _RequestRejected(403, "CrossSiteRequest", "不接受其他網站的請求。")
        if self._one_header("Transfer-Encoding") is not None:
            raise _RequestRejected(400, "InvalidHeaders", "不支援此請求傳輸方式。")
        if api:
            supplied = self._one_header("X-WardLens-Token") or ""
            if not secrets.compare_digest(supplied.encode("utf-8"), self.owner.token.encode("ascii")):
                raise _RequestRejected(401, "AuthenticationRequired", "此視窗已失效，請從啟動器重新開啟。")
            self.owner._touch()

    def _send(self, status: int, body: bytes, content_type: str, *, head: bool = False) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _json(self, status: int, result: dict[str, Any]) -> None:
        body = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(body) > self.owner.max_response_bytes:
            raise _RequestRejected(503, "ResponseTooLarge", "資料量超過本機介面容量，請縮小資料範圍。")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, code: str, message: str) -> None:
        # Error payloads must not depend on the size of a controller response.
        body = json.dumps({"ok": False, "error": message, "code": code}, ensure_ascii=False).encode("utf-8")
        try:
            self._send(status, body, "application/json; charset=utf-8", head=self.command == "HEAD")
        except (OSError, ValueError):
            pass

    def send_error(self, code, message=None, explain=None) -> None:
        # BaseHTTPRequestHandler otherwise reflects portions of malformed requests.
        self._error(code, "InvalidRequest", "請求格式或方法無效。")

    def handle_expect_100(self) -> bool:
        self._error(417, "InvalidHeaders", "不支援此請求傳輸方式。")
        return False

    def _invoke(self, operation) -> None:
        try:
            operation()
        except _RequestRejected as exc:
            self._error(exc.status, exc.code, exc.message)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as exc:
            name = type(exc).__name__
            message, status = getattr(exc, "message", None), getattr(exc, "status", None)
            if name == "UserError" and isinstance(message, str) and type(status) is int and status in {400, 403, 409}:
                self._error(status, "UserError", message[:1000])
            else:
                code = name if _SAFE_ERROR_NAME.fullmatch(name) else "InternalError"
                self._error(500, code, "發生未預期錯誤，請重新操作或重新開啟 WardLens。")

    def _get(self, *, head: bool = False) -> None:
        api = self.path == "/api" or self.path.startswith("/api/")
        self._guard(api=api)
        content_length = self._one_header("Content-Length")
        if content_length is not None and content_length != "0":
            raise _RequestRejected(400, "InvalidHeaders", "讀取請求不得包含內容。")
        if api:
            if head:
                raise _RequestRejected(405, "MethodNotAllowed", "此 API 不支援這個方法。")
            if self.path != "/api/state":
                raise _RequestRejected(404, "NotFound", "找不到這個操作。")
            result = self.owner.controller.state()
            if not isinstance(result, dict):
                raise TypeError("Controller state must be a dictionary")
            self._json(200, {**result, "ok": True})
            return
        asset = _STATIC.get(self.path)
        if asset is None:
            raise _RequestRejected(404, "NotFound", "找不到這個頁面。")
        name, content_type = asset
        resource = self.owner.assets_dir.joinpath(name)
        if not resource.is_file():
            raise _RequestRejected(404, "NotFound", "介面資源未完整解壓縮。")
        body = resource.read_bytes()
        if len(body) > 2 * 1024 * 1024:
            raise _RequestRejected(503, "AssetTooLarge", "介面資源大小異常。")
        self._send(200, body, content_type, head=head)

    def do_GET(self) -> None:  # noqa: N802
        self._invoke(self._get)

    def do_HEAD(self) -> None:  # noqa: N802
        self._invoke(lambda: self._get(head=True))

    def _post(self) -> None:
        self._guard(api=True, post=True)
        if not self.path.startswith("/api/") or self.path[len("/api/"):] not in _ACTIONS:
            raise _RequestRejected(404, "NotFound", "找不到這個操作。")
        content_type = self._one_header("Content-Type") or ""
        if content_type.lower().replace(" ", "") not in {"application/json", "application/json;charset=utf-8"}:
            raise _RequestRejected(415, "UnsupportedMediaType", "只接受 UTF-8 JSON 請求。")
        length_text = self._one_header("Content-Length")
        if length_text is None:
            raise _RequestRejected(411, "LengthRequired", "請求缺少內容長度。")
        if not re.fullmatch(r"[0-9]{1,10}", length_text):
            raise _RequestRejected(400, "InvalidLength", "請求內容長度無效。")
        length = int(length_text)
        if length > self.owner.max_body_bytes:
            raise _RequestRejected(413, "RequestTooLarge", "輸入資料過大，請縮小資料範圍。")
        body = self.rfile.read(length)
        if len(body) != length:
            raise _RequestRejected(400, "IncompleteRequest", "請求內容不完整。")
        try:
            payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise _RequestRejected(400, "InvalidJSON", "JSON 格式無效。") from exc
        if not isinstance(payload, dict):
            raise _RequestRejected(400, "InvalidJSON", "請求內容必須是 JSON 物件。")
        action = self.path[len("/api/"):]
        if action == "close":
            if payload:
                raise _RequestRejected(400, "InvalidJSON", "結束操作不得包含其他參數。")
            try:
                self._json(200, {"ok": True, "closed": True})
                self.wfile.flush()
            finally:
                # shutdown must not wait inside the serve_forever/request thread.
                threading.Thread(target=self.owner.stop, daemon=True, name="wardlens-web-stop").start()
            return
        result = self.owner.controller.action(action, payload)
        if not isinstance(result, dict):
            raise TypeError("Controller action must return a dictionary")
        self._json(200, {**result, "ok": True})

    def do_POST(self) -> None:  # noqa: N802
        self._invoke(self._post)

    def _unsupported(self) -> None:
        self._guard(api=self.path == "/api" or self.path.startswith("/api/"))
        raise _RequestRejected(405, "MethodNotAllowed", "此 API 不支援這個方法。")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._invoke(self._unsupported)

    do_PUT = do_OPTIONS
    do_PATCH = do_OPTIONS
    do_DELETE = do_OPTIONS


class LocalWebServer:
    """One application session, bound to a random IPv4 loopback port.

    ``start()`` returns self; ``url`` contains a fragment-only bootstrap token.
    The controller must synchronize its own state and reject stale revisions.
    ``stop()`` is idempotent and closes the controller once. ``wait()`` lets a
    launcher wait for either explicit close or expiration after API inactivity.
    """

    def __init__(
        self, controller: Controller, *, assets_dir: Path | None = None,
        idle_timeout: float | None = 900, request_timeout: float = 10,
        max_body_bytes: int = 2 * 1024 * 1024, max_response_bytes: int = 16 * 1024 * 1024,
        max_workers: int = 8,
    ) -> None:
        if request_timeout is None:
            raise ValueError("request_timeout must be a positive finite number")
        for value, label in ((request_timeout, "request_timeout"), (idle_timeout, "idle_timeout")):
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{label} must be a positive finite number")
        for value, label in ((max_body_bytes, "max_body_bytes"), (max_response_bytes, "max_response_bytes"), (max_workers, "max_workers")):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{label} must be a positive integer")
        if request_timeout > 60 or max_workers > 64:
            raise ValueError("request_timeout/max_workers exceed local-server bounds")
        self.controller = controller
        self.assets_dir = assets_dir if assets_dir is not None else files("wardlens.web").joinpath("assets")
        self.idle_timeout = idle_timeout
        self.request_timeout = request_timeout
        self.max_body_bytes = max_body_bytes
        self.max_response_bytes = max_response_bytes
        self.max_workers = max_workers
        self.token = secrets.token_urlsafe(32)
        self.port = 0
        self._http: _BoundedHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._stopping = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._last_activity = time.monotonic()

    @property
    def origin(self) -> str:
        if not self.port:
            raise RuntimeError("Local server has not started")
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/#{self.token}"

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def start(self) -> LocalWebServer:
        with self._lifecycle_lock:
            if self._stopping.is_set():
                raise RuntimeError("A stopped LocalWebServer cannot be restarted")
            if self._http is not None:
                return self
            self._http = _BoundedHTTPServer(self)
            self.port = self._http.server_address[1]
            self._touch()
            self._thread = threading.Thread(target=self._http.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True, name="wardlens-web")
            self._thread.start()
            if self.idle_timeout is not None:
                threading.Thread(target=self._watch_idle, daemon=True, name="wardlens-web-idle").start()
        return self

    def _touch(self) -> None:
        with self._activity_lock:
            self._last_activity = time.monotonic()

    def _watch_idle(self) -> None:
        interval = min(1.0, self.idle_timeout / 4)
        while not self._stopping.wait(interval):
            with self._activity_lock:
                expired = time.monotonic() - self._last_activity >= self.idle_timeout
            if expired:
                self.stop()
                return

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._stopping.is_set():
                return
            self._stopping.set()
            http = self._http
        try:
            if http is not None:
                try:
                    http.shutdown()
                finally:
                    http.close_connections()
                    http.server_close()
        except Exception:
            # Never serialize/log transport or clinical cleanup exceptions.
            pass
        finally:
            try:
                self.controller.close()
            except Exception:
                pass
            finally:
                self._closed.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)
