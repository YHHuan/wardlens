"""Exercise the shipped HTTP/controller path using fabricated data, never inference."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import requests

from wardlens.config import AppSettings
from wardlens.llm.openrouter import StreamEvent
from wardlens.security.secrets import MemorySecretStore
from wardlens.services.ai import AIWorkflowService
from wardlens.services.session import ClinicalSession
from wardlens.web.server import LocalWebServer


class SyntheticClient:
    """No HTTP transport exists on this fake provider."""

    def __init__(self) -> None:
        self.calls = 0

    def cancel(self) -> None:
        pass

    def stream(self, key, envelope, profile, **kwargs):
        assert key == "synthetic-key-for-offline-selftest"
        self.calls += 1
        for delta in ("## 合成查房\n", "| 項目 | 趨勢 |\n| --- | --- |\n", "| CRP | 4 → 2 mg/dL |\n"):
            yield StreamEvent(delta=delta)


def run_web_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="WardLens-web-selftest-") as directory:
        store = MemorySecretStore()
        store.set("openrouter_api_key", "synthetic-key-for-offline-selftest")
        client = SyntheticClient()
        controller = ClinicalSession(
            settings=AppSettings(external_ai_enabled=True, privacy_acknowledged=True),
            settings_path=Path(directory) / "settings.json", secret_store=store,
            ai=AIWorkflowService(client=client),
        )
        server = LocalWebServer(controller).start()
        http = requests.Session()
        http.trust_env = False
        http.headers.update({"X-WardLens-Token": server.token, "Origin": server.origin})

        def action(name, payload):
            response = http.post(server.origin + "/api/" + name, json=payload, timeout=10)
            assert response.ok, (name, response.status_code)
            result = response.json()
            assert result["ok"], name
            return result

        def state():
            response = http.get(server.origin + "/api/state", timeout=10)
            assert response.ok
            return response.json()

        def wait():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                value = state()
                if not value["busy"]:
                    assert value["job"]["status"] == "complete", value["job"]["kind"]
                    return value
                time.sleep(0.02)
            raise AssertionError("Synthetic job timeout")

        try:
            for path in ("/", "/assets/app.css", "/assets/app.js", "/assets/markdown.js"):
                response = http.get(server.origin + path, timeout=10)
                assert response.ok and response.content
                assert response.headers["Cache-Control"] == "no-store"
            outsider = requests.Session()
            outsider.trust_env = False
            try:
                assert outsider.get(server.origin + "/api/state", timeout=10).status_code in {401, 403}
            finally:
                outsider.close()
            action("login", {"demo": True, "username": "", "password": ""})
            wait()
            action("search", {"doctor_id": "", "ward": "", "histnos": ""})
            value = wait()
            assert len(value["patients"]) == 18
            action("select", {"epoch": value["epoch"], "patient_id": value["patients"][0]["id"]})
            value = state()
            action("load", {"epoch": value["epoch"], "months": 3})
            value = wait()
            assert value["overview"] and value["drafts"]["rounding"]["data"]
            draft = value["drafts"]["rounding"]
            common = {"epoch": value["epoch"], "workspace": "rounding", "revision": draft["revision"] + 1}
            action("draft", dict(common, data="Synthetic CRP 4 → 2 mg/dL", question="合成趨勢?", workflow="rounding", profile="fast"))
            preview = action("prepare", common)
            assert not preview["blocked"]
            request = dict(common, preview_id=preview["preview_id"], sha256=preview["sha256"], confirm=True)
            action("generate", request)
            value = wait()
            assert value["answers"]["rounding"]["status"] == "complete"
            assert "4 → 2" in value["answers"]["rounding"]["text"] and client.calls == 1
            repeated = http.post(server.origin + "/api/generate", json=request, timeout=10)
            assert repeated.status_code == 409 and client.calls == 1
            action("logout", {})
            value = state()
            assert not value["patients"] and not value["drafts"]["rounding"]["data"]
            assert "synthetic-key-for-offline-selftest" not in str(value)
        finally:
            http.close()
            server.stop()
