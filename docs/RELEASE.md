# Windows portable release process

## Distribution design

The supported artifact is `WardLens-Portable.zip`. After extraction it contains:

```text
WardLens/
├── WardLens.exe   native first-party launcher
├── Start-WardLens.bat
├── Start-WardLens-Desktop.bat
├── Diagnose-WardLens.bat
├── runtime/       Python Software Foundation Windows distribution
├── app/           WardLens Python source, prompts and web/assets/
├── packages/      hash-locked pure-Python dependencies
├── drivers/       README only; optional personal WebDriver preparation
└── THIRD-PARTY/   dependency lock, Python license, and official SPDX SBOM
```

Double-click `WardLens.exe`; it starts the bundled PSF `pythonw.exe` in isolated mode using an absolute path, without a shell or temporary extraction. v0.7 opens a small Tk controller plus the local browser workbench, preferring installed Edge InPrivate and falling back to the default browser. The main UI needs no Node, Selenium, WebDriver or remote web assets. The readable BAT remains available for diagnostics; `Start-WardLens-Desktop.bat` or `--desktop` opens the Tk backup. Startup runs no installer, PowerShell, pip, downloader, or execution-policy bypass. Keep the complete extracted directory together; the EXE alone is not the application.

Closing the controller or selecting Exit must stop the local server and clinical session. Refreshing the browser requires reopening it from the controller because the connection token is memory-only. Closing a browser tab alone does not immediately terminate the program.

The optional browser test uses installed Edge. Public releases do not redistribute Microsoft WebDriver because its included EULA section 3.e restricts redistribution. An explicit preparation button downloads the pinned official archive for personal testing, validates both archive/executable SHA-256 and retains the complete `Driver_Notes`. Preparation is separate from startup/probing; the public package check rejects native files in `drivers/`. Never use a personally prepared directory as a release or public mirror. See [browser testing](BROWSER_TEST.md).

The first-party launcher is compiled from `packaging/wardlens_launcher.c` with a static MSVC runtime and an `asInvoker` manifest. It is not Authenticode-signed; the PSF signatures cover only the Python runtime. Its SHA-256 and build source are recorded in provenance. Compiler tools are used on the build machine only. The process launch uses an explicit application path and writable command buffer as documented by [Microsoft CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw).

This design removes WardLens-generated PyInstaller binaries, but it cannot override AppLocker, WDAC, Defender, or hospital policy. If the package is denied, give hospital IT the release hashes and provenance; never instruct users to bypass endpoint protection.

## Build locally

Run from a clean Windows checkout with Python 3.14, MSVC x64 build tools and internet access:

```powershell
py -3.14 -m pip install -e ".[dev]"
.\scripts\build_windows.ps1
```

The script:

1. runs source validation, Ruff, and Pytest;
2. downloads the exact PSF runtime pinned in `packaging/portable-runtime.json`;
3. validates its SHA-256 against both the repository lock and python.org SPDX;
4. installs only the exact pure-Python wheels and hashes in `packaging/portable-requirements.txt`;
5. compiles the first-party `WardLens.exe` and rejects native dependencies under `app/` and `packages/`;
6. verifies the PSF Authenticode publisher on the main runtime executables/DLL;
7. tests no-argument EXE startup/close, missing-runtime errors, core (including fabricated VGH HTML and UTF-8 streams), the browser API self-test, desktop AI workflows, WinCred, diagnostics and BAT launchers;
8. extracts the finished ZIP to a fresh Windows path containing spaces and Chinese characters, then reruns portable launch/core/UI/diagnostic checks;
9. emits `WardLens-Portable.zip`, `WardLens-Source.zip`, provenance, and SHA-256 sums under `dist\release`.

## v0.7 acceptance

These are release requirements, not evidence that a particular v0.7 build or hospital workstation has passed. Attach the actual CI run, artifact hash and Windows observations to the release review. Use only synthetic records and a fake LLM transport for automated generation tests.

- Verify the full ZIP contains all four static assets and the desktop BAT, and runs without Node, pip, CDN or a driver preparation step.
- Open the main workbench from the EXE, load the 18-patient Demo, select/load/switch patients, edit each workspace independently, review/repair previews and render synthetic Chinese streaming answers. Verify logout and exit clear the session, refresh requires controller reopen, and desktop fallback starts.
- Check Markdown headings/tables/plain-text copy and hostile HTML/link/image fixtures; no source/model content may execute or fetch external resources. Check the layout on the target Windows/Edge screen size, not only Python API tests.
- Verify CBC ordering, expandable differential/source details, visible last measurement time and span, unit separation, unknown-time values, conflicting timestamps, single points and missing sources. Do not treat a synthetic parser test as validation of live hospital HTML.
- Run unauthorized/wrong Host/Origin/token requests, stale epoch/revision and reused-send tests. Confirm credentials/cookies are absent from API state, clinical content is absent from logs, and no browser persistent storage is used.
- Repeat startup and core flows from the finished ZIP in a fresh Windows directory with spaces and Chinese characters. Check both BAT paths, controller shutdown and missing-runtime diagnostics.

The previous WebDriver probe PASS applies only to its synthetic browser operations. It does not prove this new UI, the release package, live EMR coverage or a Selenium adapter works.

## GitHub release

1. Update the version in `pyproject.toml` and `src/wardlens/__init__.py`.
2. Run tests, `python scripts/check_portable.py`, and `git diff --check`.
3. Push the reviewed commit and tag `vX.Y.Z`.
4. GitHub Actions rebuilds on a Windows runner and creates a prerelease pilot.
5. Download the public asset without authentication, verify `SHA256SUMS.txt`, inspect the ZIP topology, and re-run the EXE and BAT on a separate Windows machine.

Never publish `.env`, API keys, EMR credentials, cookies, raw HTML, responses, patient lists, screenshots, diagnostics from a real workstation, or audit files. Built-in prompts are source controlled; exported developer JSON can include manually entered prompt content and must be reviewed before sharing.
