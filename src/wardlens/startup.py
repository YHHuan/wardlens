from __future__ import annotations

import ctypes
import os
import platform
import sys
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any


def diagnostic_path() -> Path:
    """Return a per-user path that never lives beside clinical exports."""

    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / "WardLens"
    elif os.getenv("XDG_STATE_HOME"):
        base = Path(os.environ["XDG_STATE_HOME"]) / "wardlens"
    else:
        base = Path.home() / ".local" / "state" / "wardlens"
    return base / "startup-error.log"


def _safe_exception_summary(exc: BaseException) -> str:
    if isinstance(exc, ModuleNotFoundError):
        return f"missing module: {exc.name or 'unknown'}"
    if isinstance(exc, OSError):
        return f"os error: winerror={getattr(exc, 'winerror', None)}, errno={exc.errno}"
    return "message omitted to prevent clinical or credential data from entering diagnostics"


def _render_diagnostic(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    phase: str,
) -> str:
    try:
        from wardlens import __version__
    except Exception:
        __version__ = "unknown"
    frames = traceback.extract_tb(tb) if tb is not None else []
    stack = []
    for frame in frames[-30:]:
        # Only code filenames/functions/line numbers: no source line, locals,
        # exception text, response URLs, or user-specific directory names.
        location = frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
        stack.append(f"  {location}:{frame.lineno} in {frame.name}")
    if not stack:
        stack.append("  no Python traceback was available")
    return "\n".join(
        [
            "WardLens privacy-safe diagnostic",
            f"timestamp_utc: {datetime.now(UTC).isoformat()}",
            f"phase: {phase}",
            f"wardlens_version: {__version__}",
            f"python_version: {platform.python_version()}",
            f"platform: {platform.system()} {platform.release()}",
            f"exception_type: {exc_type.__module__}.{exc_type.__name__}",
            f"exception_summary: {_safe_exception_summary(exc)}",
            "traceback_frames:",
            *stack,
            "",
            "Clinical values, credentials, and exception messages are intentionally omitted.",
        ]
    )


def _write_diagnostic(content: str, target: Path | None = None) -> Path | None:
    destinations = [target or diagnostic_path()]
    fallback = Path(tempfile.gettempdir()) / f"WardLens-{destinations[0].name}"
    if fallback not in destinations:
        destinations.append(fallback)
    for destination in destinations:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        except OSError:
            continue
        return destination
    return None


def _show_native_error(path: Path | None) -> None:
    location = str(path) if path is not None else "(the diagnostic file could not be written)"
    message = (
        "WardLens could not continue.\n\n"
        "No clinical data or credentials were written to the diagnostic log.\n"
        f"Diagnostic file:\n{location}\n\n"
        "Please run Diagnose-WardLens.bat and send the generated report."
    )
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "WardLens error", 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def report_unhandled_exception(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    phase: str,
    target: Path | None = None,
    show_dialog: bool = True,
) -> Path | None:
    content = _render_diagnostic(exc_type, exc, tb, phase=phase)
    path = _write_diagnostic(content, target)
    if show_dialog:
        _show_native_error(path)
    return path


def install_tk_exception_handler(root: Any) -> None:
    def callback(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        report_unhandled_exception(exc_type, exc, tb, phase="tk-callback")

    root.report_callback_exception = callback


def operation_error_detail(exc: Exception, *, phase: str = "operation") -> str:
    """Record bounded technical context for errors caught by the UI worker."""

    phase = phase if phase in {"operation", "load-patient"} else "operation"
    path = report_unhandled_exception(
        type(exc),
        exc,
        exc.__traceback__,
        phase=phase,
        target=diagnostic_path().with_name("operation-error.log"),
        show_dialog=False,
    )
    location = f"\n診斷檔：{path}" if path is not None else "\n診斷檔無法寫入；請回報錯誤碼。"
    return (
        f"發生未預期錯誤（{phase}／{type(exc).__name__}）。"
        "\n診斷只記錄程式版本與錯誤位置，不記錄病歷、帳密或 API key。"
        f"{location}"
    )
