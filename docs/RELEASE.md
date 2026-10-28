# Windows portable release process

## Distribution design

The supported artifact is `WardLens-Portable.zip`. After extraction it contains:

```text
WardLens/
├── WardLens.exe   native first-party launcher
├── Start-WardLens.bat
├── Diagnose-WardLens.bat
├── runtime/       Python Software Foundation Windows distribution
├── app/           WardLens Python source and resources
├── packages/      hash-locked pure-Python dependencies
├── drivers/       README only; optional personal WebDriver preparation
└── THIRD-PARTY/   dependency lock, Python license, and official SPDX SBOM
```

Double-click `WardLens.exe`; it starts the bundled PSF `pythonw.exe` in isolated mode using an absolute path, without a shell or temporary extraction. The readable BAT remains available for diagnostics. Startup runs no installer, PowerShell, pip, downloader, or execution-policy bypass. Keep the complete extracted directory together; the EXE alone is not the application.

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
7. tests no-argument EXE GUI startup/close, missing-runtime errors, core (including fabricated VGH HTML and UTF-8 streams), both visible AI UI workflows, WinCred, diagnostics and BAT launchers;
8. extracts the finished ZIP to a fresh Windows path containing spaces and Chinese characters, then reruns EXE/BAT/core/UI/diagnostic tests;
9. emits `WardLens-Portable.zip`, `WardLens-Source.zip`, provenance, and SHA-256 sums under `dist\release`.

## GitHub release

1. Update the version in `pyproject.toml` and `src/wardlens/__init__.py`.
2. Run tests, `python scripts/check_portable.py`, and `git diff --check`.
3. Push the reviewed commit and tag `vX.Y.Z`.
4. GitHub Actions rebuilds on a Windows runner and creates a prerelease pilot.
5. Download the public asset without authentication, verify `SHA256SUMS.txt`, inspect the ZIP topology, and re-run the EXE and BAT on a separate Windows machine.

Never publish `.env`, API keys, EMR credentials, cookies, raw HTML, responses, patient lists, screenshots, diagnostics from a real workstation, or audit files. Built-in prompts are source controlled; exported developer JSON can include manually entered prompt content and must be reviewed before sharing.
