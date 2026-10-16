# Windows portable release process

## Distribution design

The supported artifact is `WardLens-Portable.zip`. After extraction it contains:

```text
WardLens/
├── Start-WardLens.bat
├── Diagnose-WardLens.bat
├── runtime/       Python Software Foundation Windows distribution
├── app/           WardLens Python source and resources
├── packages/      hash-locked pure-Python dependencies
└── THIRD-PARTY/   dependency lock, Python license, and official SPDX SBOM
```

The target computer does not run an installer, PowerShell, pip, a downloader, or an execution-policy bypass. The visible BAT launcher calls the bundled, Authenticode-signed PSF `python.exe` in isolated mode. Keep the complete extracted directory together.

This design removes WardLens-generated PyInstaller binaries, but it cannot override AppLocker, WDAC, Defender, or hospital policy. If the package is denied, give hospital IT the release hashes and provenance; never instruct users to bypass endpoint protection.

## Build locally

Run from a clean Windows checkout with Python 3.14 and internet access:

```powershell
py -3.14 -m pip install -e ".[dev]"
.\scripts\build_windows.ps1
```

The script:

1. runs source validation, Ruff, and Pytest;
2. downloads the exact PSF runtime pinned in `packaging/portable-runtime.json`;
3. validates its SHA-256 against both the repository lock and python.org SPDX;
4. installs only the exact pure-Python wheels and hashes in `packaging/portable-requirements.txt`;
5. rejects native binaries outside the PSF runtime;
6. verifies the PSF Authenticode publisher on the main runtime executables/DLL;
7. starts core (including the real VGH parser on fabricated historical-date HTML, with all network requests disabled), visible Tk UI, WinCred, diagnostics, and the exact BAT launcher tests;
8. extracts the finished ZIP to a fresh Windows path containing spaces and reruns the exact BAT/core/UI/diagnostic tests;
9. emits `WardLens-Portable.zip`, `WardLens-Source.zip`, provenance, and SHA-256 sums under `dist\release`.

## GitHub release

1. Update the version in `pyproject.toml` and `src/wardlens/__init__.py`.
2. Run tests, `python scripts/check_portable.py`, and `git diff --check`.
3. Push the reviewed commit and tag `vX.Y.Z`.
4. GitHub Actions rebuilds on a Windows runner and creates a prerelease pilot.
5. Download the public asset without authentication, verify `SHA256SUMS.txt`, inspect the ZIP topology, and re-run the BAT on a separate Windows machine.

Never publish `.env`, API keys, EMR credentials, cookies, raw HTML, responses, patient lists, screenshots, diagnostics from a real workstation, or audit files. Built-in prompts are source controlled; exported developer JSON can include manually entered prompt content and must be reviewed before sharing.
