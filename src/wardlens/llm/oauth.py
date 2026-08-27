from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from wardlens.llm.openrouter import OpenRouterError


class OpenRouterOAuth:
    """OpenRouter OAuth PKCE flow for a local desktop app.

    The temporary localhost listener receives only a short-lived authorization code.
    The resulting API key is returned to the caller for secure OS-vault storage.
    """

    authorize_url = "https://openrouter.ai/auth"
    exchange_url = "https://openrouter.ai/api/v1/auth/keys"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @staticmethod
    def create_pkce() -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return verifier, challenge

    def connect(self, *, timeout_seconds: int = 300) -> str:
        verifier, challenge = self.create_pkce()
        path_nonce = secrets.token_urlsafe(18)
        received = threading.Event()
        result: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if parsed.path != f"/callback/{path_nonce}":
                    self.send_error(404)
                    return
                query = parse_qs(parsed.query)
                if query.get("code"):
                    result["code"] = query["code"][0]
                elif query.get("error"):
                    result["error"] = query["error"][0]
                else:
                    result["error"] = "OpenRouter 未回傳授權碼。"
                received.set()
                body = (
                    "<!doctype html><meta charset='utf-8'><title>WardLens</title>"
                    "<h2>WardLens 授權已接收</h2>"
                    "<p>可以關閉此頁並回到 WardLens。API key 不會顯示在瀏覽器。</p>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                return

        try:
            server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        except OSError as exc:
            raise OpenRouterError("無法啟動本機 OAuth callback；請改用剪貼簿貼上。") from exc

        port = server.server_address[1]
        callback_url = f"http://127.0.0.1:{port}/callback/{path_nonce}"
        url = self.authorization_link(callback_url, challenge)
        server.timeout = 0.5
        try:
            if not webbrowser.open(url, new=1):
                raise OpenRouterError("無法開啟瀏覽器；請改用剪貼簿貼上 API key。")
            deadline = time.monotonic() + timeout_seconds
            while not received.is_set() and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        if not received.is_set():
            raise OpenRouterError("OpenRouter 登入逾時；請重新登入或改用剪貼簿貼上。")
        if result.get("error"):
            raise OpenRouterError(f"OpenRouter 授權失敗：{result['error']}")
        return self.exchange(result.get("code", ""), verifier)

    @classmethod
    def authorization_link(cls, callback_url: str, challenge: str) -> str:
        return (
            cls.authorize_url
            + "?"
            + urlencode(
                {
                    "callback_url": callback_url,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "key_label": "WardLens desktop",
                }
            )
        )

    def exchange(self, code: str, verifier: str) -> str:
        if not code.strip() or not verifier.strip():
            raise OpenRouterError("OpenRouter OAuth 授權碼不完整。")
        try:
            response = self.session.post(
                self.exchange_url,
                headers={"Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "code": code.strip(),
                        "code_verifier": verifier.strip(),
                        "code_challenge_method": "S256",
                    }
                ),
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise OpenRouterError("無法交換 OpenRouter OAuth 授權碼。") from exc
        if not response.ok:
            raise OpenRouterError(f"OpenRouter OAuth 交換失敗（HTTP {response.status_code}）。")
        try:
            key = response.json()["key"]
        except (ValueError, KeyError, TypeError) as exc:
            raise OpenRouterError("OpenRouter OAuth 回應缺少 API key。") from exc
        if not isinstance(key, str) or len(key.strip()) < 20:
            raise OpenRouterError("OpenRouter OAuth 回傳的 API key 格式不完整。")
        return key.strip()
