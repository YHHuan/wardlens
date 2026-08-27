from __future__ import annotations

import sys

from wardlens import __version__


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
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
    try:
        from wardlens.app import main as app_main
    except ModuleNotFoundError as exc:
        if exc.name == "tkinter":
            raise SystemExit(
                "WardLens requires Tkinter. Use the packaged Windows release, or install your Python Tk package."
            ) from exc
        raise
    app_main(arguments)


if __name__ == "__main__":
    main()
