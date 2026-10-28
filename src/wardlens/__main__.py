from __future__ import annotations

import sys

from wardlens import __version__


def _dispatch(arguments: list[str]) -> None:
    if "--version" in arguments:
        print(f"WardLens {__version__}")
        return
    if "--self-test" in arguments:
        from wardlens.selftest import run_self_test

        run_self_test()
        print("WardLens packaged self-test passed.")
        return
    if "--ui-self-test" in arguments:
        from wardlens.selftest import run_ui_self_test

        run_ui_self_test()
        print("WardLens packaged UI self-test passed.")
        return
    if "--diagnose" in arguments:
        from wardlens.selftest import run_diagnostics

        run_diagnostics()
        return
    if "--browser-diagnostics" in arguments:
        from wardlens.browser_probe import report_text, run_browser_probe
        from wardlens.config import app_data_dir

        text = report_text(run_browser_probe())
        target = app_data_dir() / "WardLens-Browser-Diagnostics.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(text)
        return
    try:
        from wardlens.app import main as app_main
    except ModuleNotFoundError as exc:
        if exc.name == "tkinter":
            raise SystemExit(
                "WardLens requires Tkinter. Use the packaged Windows release, or install your Python Tk package."
            ) from exc
        raise
    app_main(arguments)


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        _dispatch(arguments)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        from wardlens.startup import report_unhandled_exception

        noninteractive = {"--self-test", "--ui-self-test", "--diagnose", "--version", "--browser-diagnostics"}
        report_unhandled_exception(
            type(exc),
            exc,
            exc.__traceback__,
            phase="startup",
            show_dialog=not noninteractive.intersection(arguments),
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
