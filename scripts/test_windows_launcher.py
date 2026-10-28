"""Exercise the shipped native launcher, including exit codes and diagnostics."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def check_double_click(exe: Path, directory: str) -> None:
    """Open the real no-argument GUI; close only the child of our test launcher."""
    import ctypes as c
    from ctypes import wintypes as w

    class ProcessEntry(c.Structure):
        _fields_ = [
            ("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("pid", w.DWORD),
            ("heap", c.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
            ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
            ("exe", w.WCHAR * 260),
        ]

    kernel, user = c.WinDLL("kernel32", use_last_error=True), c.WinDLL("user32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
        function.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.IsWindowVisible.argtypes = [w.HWND]
    user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    environment = dict(os.environ, LOCALAPPDATA=directory)
    process = subprocess.Popen([str(exe)], cwd=directory, env=environment)
    try:
        deadline = time.monotonic() + 20
        windows = []
        while time.monotonic() < deadline and process.poll() is None and not windows:
            children = set()
            snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
            if snapshot == w.HANDLE(-1).value:
                raise RuntimeError("Cannot enumerate the test launcher child.")
            try:
                entry = ProcessEntry()
                entry.dwSize = c.sizeof(entry)
                more = kernel.Process32FirstW(snapshot, c.byref(entry))
                while more:
                    if entry.parent == process.pid:
                        children.add(entry.pid)
                    more = kernel.Process32NextW(snapshot, c.byref(entry))
            finally:
                kernel.CloseHandle(snapshot)

            @callback_type
            def visit(window, _parameter, owned=children):
                pid = w.DWORD()
                user.GetWindowThreadProcessId(window, c.byref(pid))
                if pid.value in owned and user.IsWindowVisible(window):
                    title = c.create_unicode_buffer(256)
                    user.GetWindowTextW(window, title, len(title))
                    if title.value.startswith("WardLens "):
                        windows.append(window)
                return True

            user.EnumWindows(visit, 0)
            if not windows:
                time.sleep(0.1)
        if not windows:
            raise RuntimeError("Double-click EXE did not create a visible WardLens window.")
        user.PostMessageW(windows[0], 0x0010, 0, 0)  # WM_CLOSE, our own child only.
        if process.wait(timeout=10) != 0:
            raise RuntimeError("Double-click EXE did not close cleanly.")
        print("EXE double-click visible GUI passed")
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--browser-probe", action="store_true", help="Require real synthetic browser checks to pass on a matching Edge host.")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This test requires Windows.")
    exe = args.exe.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="WardLens launcher 測試 ") as directory:
        check_double_click(exe, directory)
        # Different working directory: the launcher must resolve its OWN folder.
        for flag, marker in (
            ("--version", "WardLens"),
            ("--self-test", "self-test passed"),
            ("--ui-self-test", "UI self-test passed"),
            ("--diagnose", "PASS"),
        ):
            result = subprocess.run(
                [str(exe), flag], cwd=directory, capture_output=True,
                encoding="utf-8", errors="replace", timeout=90,
            )
            if result.returncode != 0 or marker not in result.stdout:
                raise RuntimeError(f"EXE {flag} failed: exit={result.returncode}; stdout={result.stdout}; stderr={result.stderr}")
            print(f"EXE {flag} passed")
        missing = Path(directory) / "WardLens.exe"
        shutil.copy2(exe, missing)
        result = subprocess.run([str(missing), "--version"], cwd=directory, capture_output=True, timeout=10)
        if result.returncode != 2:
            raise RuntimeError(f"EXE without runtime returned {result.returncode}, expected 2.")
        print("EXE missing-runtime check passed")
        if args.browser_probe:
            result = subprocess.run(
                [str(exe), "--browser-diagnostics"], cwd=directory, capture_output=True,
                encoding="utf-8", errors="replace", timeout=120,
                env=dict(os.environ, LOCALAPPDATA=directory),
            )
            report = json.loads(result.stdout)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if result.returncode or report["result"] != "PASS":
                raise RuntimeError("Real browser probe did not pass; inspect safe report above.")


if __name__ == "__main__":
    main()
