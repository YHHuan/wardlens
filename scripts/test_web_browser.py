"""Real Edge acceptance test, using only synthetic EMR and a fake AI provider.

Run on Windows with an explicitly supplied, personally obtained matching driver.
No driver is bundled. Screenshots contain fabricated demo records only.
"""
from __future__ import annotations

import argparse
import base64
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from wardlens.browser_probe import _find_edge, _WebDriver
from wardlens.config import AppSettings
from wardlens.llm.openrouter import StreamEvent
from wardlens.security.secrets import MemorySecretStore
from wardlens.services.ai import AIWorkflowService
from wardlens.services.session import ClinicalSession
from wardlens.web.server import LocalWebServer


class FabricatedProvider:
    def __init__(self):
        self.calls = 0

    def cancel(self):
        pass

    def stream(self, *_args, **_kwargs):
        self.calls += 1
        for delta in (
            "## 合成測試回答\n\n**CRP 下降**，跨 2 天；需配合床邊判斷。[SRC-001]\n\n",
            "| 項目 | 趨勢 |\n| --- | --- |\n| CRP | 15.2 → 11.4 → 7.8 mg/dL |\n\n",
            '<img src="https://example.invalid/leak" onerror="window.injected=true">\n',
            '<script>window.injected=true</script>\n',
        ):
            time.sleep(0.03)
            yield StreamEvent(delta=delta)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--screenshots", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Use a Windows host with Edge installed.")
    browser, version = _find_edge()
    assert browser and args.driver.is_file(), "Edge and a matching driver are required."
    args.screenshots.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    web = _WebDriver(port)
    process = None
    session = ""
    server = None
    with tempfile.TemporaryDirectory(prefix="WardLensUI-test-", ignore_cleanup_errors=True) as scratch:
        try:
            store = MemorySecretStore()
            store.set("openrouter_api_key", "fake-test-key")
            provider = FabricatedProvider()
            controller = ClinicalSession(
                settings=AppSettings(external_ai_enabled=True, privacy_acknowledged=True),
                settings_path=Path(scratch) / "settings.json", secret_store=store,
                ai=AIWorkflowService(client=provider),
            )
            server = LocalWebServer(controller).start()
            process = subprocess.Popen(
                [str(args.driver), f"--port={port}", "--allowed-ips=127.0.0.1", "--log-level=OFF"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 20
            while True:
                try:
                    if web.call("GET", "/status")["ready"]:
                        break
                except Exception:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise
                time.sleep(0.1)
            result = web.call("POST", "/session", {"capabilities": {"alwaysMatch": {
                "browserName": "MicrosoftEdge",
                "ms:edgeOptions": {"binary": str(browser), "args": [
                    f"--user-data-dir={Path(scratch) / 'profile'}", "--no-first-run",
                    "--no-default-browser-check", "--disable-sync", "--disable-background-networking",
                ]},
            }}})
            session = "/session/" + quote(result["sessionId"], safe="")
            web.call("POST", session + "/window/rect", {"width": 1366, "height": 768})

            def js(script, *arguments):
                return web.call("POST", session + "/execute/sync", {"script": script, "args": list(arguments)})

            def until(script, label):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if js("return " + script):
                        return
                    time.sleep(0.1)
                raise AssertionError(label)

            def click(selector):
                js("document.querySelector(arguments[0]).scrollIntoView({block:'center'})", selector)
                found = web.call("POST", session + "/element", {"using": "css selector", "value": selector})
                target = quote(found["element-6066-11e4-a52e-4f735466cecf"], safe="")
                web.call("POST", session + f"/element/{target}/click", {})

            def fill(selector, value):
                js("const n=document.querySelector(arguments[0]); n.value=arguments[1]; n.dispatchEvent(new Event('input',{bubbles:true}));", selector, value)

            def capture(name):
                encoded = web.call("GET", session + "/screenshot")
                (args.screenshots / name).write_bytes(base64.b64decode(encoded))

            web.call("POST", session + "/url", {"url": server.url})
            until("document.querySelector('#version').textContent.includes('0.7')", "App did not connect")
            assert js("return location.hash") == ""
            assert js("return localStorage.length + sessionStorage.length") == 0
            if not js("return document.querySelector('#demo').checked"):
                click("#demo")
            click("#login-button")
            until("document.querySelector('#login-state').textContent.includes('Demo 已登入')", "Demo login failed")
            click("#search-button")
            until("document.querySelectorAll('#patient-list button[data-patient-id]').length===18", "18 patients missing")
            click("#patient-list button[data-patient-id]")
            until("!document.querySelector('#load-patient').disabled", "No selected patient")
            click("#load-patient")
            until("document.querySelector('#overview table') && !document.querySelector('#load-patient').disabled", "Load failed")
            until("document.querySelector('#rounding-data').value.includes('CRP')", "Loaded drafts not adopted")
            labels = js("return [...document.querySelectorAll('#overview .trend-section:first-of-type .trend-label')].map(n=>n.firstChild.textContent)")
            # Sections preceding the table can affect :first-of-type; use the first trend table explicitly.
            if not labels:
                labels = js("return [...document.querySelector('#overview .trend-section').querySelectorAll('.trend-label')].map(n=>n.firstChild.textContent)")
            assert labels[:3] == ["WBC", "Hb", "PLT"], labels
            assert "ceftriaxone" in js("return document.querySelector('#overview').textContent")
            assert js("return document.querySelectorAll('.vital').length") >= 5
            assert js("return document.documentElement.scrollWidth <= innerWidth")
            capture("01-rounding-synthetic.png")
            print(f"PASS Edge {version}: demo login, 18 patients, load, CBC order, vital times, 1366px layout")

            click("#tab-rounding .context-editor summary")
            fill("#rounding-data", "Synthetic CRP 15.2 → 11.4 → 7.8 mg/dL over 2 days.")
            fill("#rounding-question", "請簡短整理合成趨勢")
            time.sleep(0.6)
            click("#rounding-prepare")
            until("document.querySelector('#preview-dialog').open", "Preview did not open")
            assert js("return document.querySelector('#preview-system-details').open") is False
            click("#confirm-review")
            click("#preview-send")
            until("document.querySelector('#rounding-answer table')", "Markdown table missing")
            assert js("return document.querySelector('#rounding-answer h2').textContent") == "合成測試回答"
            assert js("return document.querySelector('#rounding-answer strong').textContent") == "CRP 下降"
            assert "SRC-001" not in js("return document.querySelector('#rounding-answer').textContent")
            assert js("return document.querySelectorAll('#rounding-answer script,#rounding-answer img').length") == 0
            assert js("return Boolean(window.injected)") is False
            assert js("return performance.getEntriesByType('resource').every(r=>new URL(r.name).origin===location.origin)")
            assert provider.calls == 1
            js("document.querySelector('#rounding-answer').scrollIntoView()")
            capture("02-markdown-synthetic.png")
            print("PASS preview/send, Chinese Markdown headings/bold/table, hidden citations, no executable HTML or external requests")

            click('[data-tab="emergency"]')
            fill("#emergency-question", "合成情境：突然喘，需要哪些資料？")
            click("#emergency-prepare")
            until("document.querySelector('#preview-dialog').open", "Emergency preview did not open")
            click("#confirm-review")
            click("#preview-send")
            until("document.querySelector('#emergency-answer table')", "Emergency answer missing")
            assert provider.calls == 2
            assert "請簡短整理" in js("return document.querySelector('#rounding-question').value")
            js("window.scrollTo(0,0)")
            capture("03-emergency-synthetic.png")
            click("#patient-list button[data-patient-id]:nth-child(2)")
            web.call("POST", session + "/alert/accept", {})
            until("document.querySelector('#emergency-question').value === ''", "Patient switch did not clear question")
            assert js("return document.querySelector('#rounding-data').value") == ""
            assert js("return document.querySelector('#rounding-answer').textContent.includes('CRP 下降')") is False
            assert js("return document.querySelector('#emergency-answer').textContent.includes('CRP 下降')") is False
            print("PASS separate emergency workspace and clearing both workspaces on patient switch")

            web.call("POST", session + "/window/rect", {"width": 720, "height": 900})
            assert js("return document.documentElement.scrollWidth <= innerWidth")
            web.call("POST", session + "/refresh", {})
            until("!document.querySelector('#connection-screen').hidden", "Reload should require launcher reopen")
            assert js("return document.querySelector('#app-shell').hidden") is True
            assert js("return document.querySelector('#patient-list').childElementCount") == 0
            print("PASS narrow layout and reload clears clinical DOM, requires launcher reopen")
        finally:
            if session:
                try:
                    web.call("DELETE", session)
                except Exception:
                    pass
            if server:
                server.stop()
            web.http.close()
            if process is not None and process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
