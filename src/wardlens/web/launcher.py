"""Launch the bundled browser UI while retaining a small local lifetime control."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from wardlens import __version__
from wardlens.config import AppSettings, app_data_dir
from wardlens.llm.prompts import PromptBuilder
from wardlens.security.audit import HashOnlyAuditLog
from wardlens.services.ai import AIWorkflowService
from wardlens.services.session import ClinicalSession
from wardlens.startup import install_tk_exception_handler
from wardlens.web.server import LocalWebServer


def open_browser(url: str) -> bool:
    """Use installed Edge; no WebDriver, profile extraction or installation."""
    if sys.platform == "win32":
        from wardlens.browser_probe import _find_edge

        path, _ = _find_edge()
        if path:
            try:
                subprocess.Popen([str(path), "--new-window", "--inprivate", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return True
            except OSError:
                pass
    return bool(webbrowser.open(url, new=1))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="WardLens local browser workspace")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--web", action="store_true")
    args = parser.parse_args(argv)
    settings = AppSettings.load()
    if args.demo:
        settings.demo_mode = True
    session = ClinicalSession(settings=settings, ai=AIWorkflowService(
        builder=PromptBuilder(settings.custom_prompts),
        audit=HashOnlyAuditLog(app_data_dir() / "audit.jsonl")))
    root = tk.Tk()
    install_tk_exception_handler(root)
    root.title(f"WardLens {__version__}｜本機瀏覽器工作台")
    root.geometry("490x280")
    root.minsize(440, 260)
    root.configure(background="#f4f6f7")
    server = None
    status = tk.StringVar(value="正在啟動本機工作台…")

    def stop() -> None:
        if server:
            server.stop()
        else:
            session.close()
        root.destroy()

    def desktop() -> None:
        if session.busy:
            messagebox.showinfo("工作進行中", "請先完成或停止目前工作，再切換桌面備援。", parent=root)
            return
        try:
            subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", "-m", "wardlens", "--desktop"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            messagebox.showerror("無法開啟", "請改用同資料夾 Start-WardLens.bat --desktop。", parent=root)
            return
        stop()

    def reopen() -> None:
        if not server or server.closed:
            status.set("工作台已結束，請關閉後重新啟動 WardLens。")
            return
        try:
            opened = open_browser(server.url)
        except Exception:
            opened = False
        status.set("已開啟瀏覽器；可將此視窗最小化。" if opened else "未能開啟瀏覽器，可重試或使用桌面備援。")

    frame = ttk.Frame(root, padding=22)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(frame, text="WardLens", font=("Segoe UI", 20, "bold")).pack(anchor="w")
    ttk.Label(frame, text="查房前整理 · 急症處置", font=("Segoe UI", 12)).pack(anchor="w", pady=(2, 12))
    ttk.Label(frame, textvariable=status, wraplength=430).pack(anchor="w", pady=4)
    ttk.Label(frame, text="保留此視窗即可繼續使用；關閉會結束本機服務。\n若網頁重新整理後需要連線，按下方重新開啟。", wraplength=430).pack(anchor="w", pady=5)
    actions = ttk.Frame(frame)
    actions.pack(fill=tk.X, pady=12)
    ttk.Button(actions, text="開啟工作台", command=reopen).pack(side=tk.LEFT)
    ttk.Button(actions, text="切換桌面備援", command=desktop).pack(side=tk.LEFT, padx=8)
    ttk.Button(actions, text="結束", command=stop).pack(side=tk.RIGHT)
    root.protocol("WM_DELETE_WINDOW", stop)
    try:
        server = LocalWebServer(session).start()
    except Exception:
        status.set("本機服務無法啟動；請使用桌面備援。")
    else:
        if os.environ.get("WARDLENS_TEST_NO_BROWSER") != "1":
            root.after(100, reopen)

    def watch() -> None:
        if server and server.closed:
            root.destroy()
        else:
            root.after(500, watch)

    root.after(500, watch)
    try:
        root.mainloop()
    finally:
        if server:
            server.stop()
        else:
            session.close()
