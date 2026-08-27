# Windows release process

## Build locally

Run from a clean Windows checkout:

```powershell
.\scripts\build_windows.ps1
```

The script runs lint/tests, builds one-file and onedir variants, zips documentation, records installed dependency versions, and emits SHA-256 hashes under `dist\release`.

## Signing

Unsigned PyInstaller binaries can be blocked by SmartScreen, Smart App Control, Defender, AppLocker or WDAC. Do not tell users to disable those controls. Obtain an RSA code-signing certificate from a trusted provider or use Microsoft Artifact Signing, then sign every EXE/DLL with the same publisher identity before hashing and publishing.

The optional helper accepts a `SecureString` PFX password:

```powershell
$PfxPassword = Read-Host "PFX password" -AsSecureString
.\scripts\sign_windows.ps1 -PfxPath C:\secure\wardlens.pfx -TargetPath .\dist\release\WardLens-OneFile.exe -PfxPassword $PfxPassword
```

For CI, prefer an identity-backed signing service over storing a reusable PFX. Ask hospital IT whether WDAC/AppLocker publisher rules or a file-hash allowlist is required.

## GitHub release

1. Update version in `pyproject.toml`, `src/wardlens/__init__.py` and `scripts/version_info.txt`.
2. Run tests and inspect `git diff --check`.
3. Tag `vX.Y.Z` and push the tag.
4. GitHub Actions builds on a Windows runner and attaches both packages plus `SHA256SUMS.txt`.
5. Download artifacts, verify hashes on a separate machine, scan, then copy the release ZIP to the approved Drive folder.

Never publish `.env`, API keys, EMR credentials, raw HTML, prompts, responses, patient lists, screenshots or audit files.
