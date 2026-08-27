param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
python scripts\check_version.py

if (-not $SkipTests) {
    python -m ruff check .
    python -m pytest
}
python -m wardlens --ui-self-test

$ReleaseDir = Join-Path $ProjectRoot "dist\release"
$OneDirDist = Join-Path $ProjectRoot "dist\onedir"
if (Test-Path $ReleaseDir) { Remove-Item -Recurse -Force $ReleaseDir }
if (Test-Path $OneDirDist) { Remove-Item -Recurse -Force $OneDirDist }
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$CommonArgs = @(
    "--noconfirm", "--clean", "--windowed",
    "--paths", "src",
    "--collect-data", "wardlens",
    "--collect-data", "docx",
    "--collect-submodules", "keyring.backends",
    "--version-file", "scripts\version_info.txt"
)

python -m PyInstaller @CommonArgs --onefile --name "WardLens-OneFile" --distpath $ReleaseDir "src\wardlens\__main__.py"
python -m PyInstaller @CommonArgs --name "WardLens" --distpath $OneDirDist "src\wardlens\__main__.py"

$Signed = $false
if ($env:WARDLENS_PFX_BASE64 -and $env:WARDLENS_PFX_PASSWORD) {
    $TemporaryPfx = Join-Path ([IO.Path]::GetTempPath()) ("wardlens-signing-" + [Guid]::NewGuid().ToString("N") + ".pfx")
    try {
        [IO.File]::WriteAllBytes($TemporaryPfx, [Convert]::FromBase64String($env:WARDLENS_PFX_BASE64))
        $SecurePassword = ConvertTo-SecureString $env:WARDLENS_PFX_PASSWORD -AsPlainText -Force
        $SigningTargets = @((Join-Path $ReleaseDir "WardLens-OneFile.exe"))
        $SigningTargets += Get-ChildItem (Join-Path $OneDirDist "WardLens") -Recurse -File |
            Where-Object { $_.Extension -in ".exe", ".dll", ".pyd" } |
            ForEach-Object { $_.FullName }
        foreach ($Target in $SigningTargets) {
            & (Join-Path $PSScriptRoot "sign_windows.ps1") -PfxPath $TemporaryPfx -TargetPath $Target -PfxPassword $SecurePassword
        }
        $Signed = $true
    }
    finally {
        if (Test-Path -LiteralPath $TemporaryPfx) { Remove-Item -LiteralPath $TemporaryPfx -Force }
        $SecurePassword = $null
    }
}

function Invoke-PackagedSelfTest {
    param(
        [string]$Executable,
        [string]$Argument,
        [string]$Label
    )
    $Process = Start-Process -FilePath $Executable -ArgumentList $Argument -PassThru
    if (-not $Process.WaitForExit(30000)) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        throw "$Label timed out."
    }
    if ($Process.ExitCode -ne 0) { throw "$Label failed with exit code $($Process.ExitCode)." }
}

$OneFileExe = Join-Path $ReleaseDir "WardLens-OneFile.exe"
$OneDirExe = Join-Path $OneDirDist "WardLens\WardLens.exe"
Invoke-PackagedSelfTest -Executable $OneFileExe -Argument "--self-test" -Label "One-file packaged self-test"
Invoke-PackagedSelfTest -Executable $OneFileExe -Argument "--ui-self-test" -Label "One-file packaged UI self-test"
Invoke-PackagedSelfTest -Executable $OneDirExe -Argument "--self-test" -Label "Onedir packaged self-test"
Invoke-PackagedSelfTest -Executable $OneDirExe -Argument "--ui-self-test" -Label "Onedir packaged UI self-test"

$PackageDir = Join-Path $ProjectRoot "dist\package"
if (Test-Path $PackageDir) { Remove-Item -Recurse -Force $PackageDir }
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null
Copy-Item -Recurse (Join-Path $OneDirDist "WardLens") (Join-Path $PackageDir "WardLens")
Copy-Item "README.md", "LICENSE", "SECURITY.md" $PackageDir
python -m pip freeze | Set-Content -Encoding UTF8 (Join-Path $PackageDir "DEPENDENCIES.txt")
Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath (Join-Path $ReleaseDir "WardLens-Onedir.zip") -Force

$DocsDir = Join-Path $ProjectRoot "dist\docs-package"
if (Test-Path $DocsDir) { Remove-Item -Recurse -Force $DocsDir }
New-Item -ItemType Directory -Force -Path $DocsDir | Out-Null
Copy-Item "README.md", "LICENSE", "SECURITY.md", "pyproject.toml" $DocsDir
Copy-Item -Recurse "docs", "src", "tests", "scripts" $DocsDir
Compress-Archive -Path (Join-Path $DocsDir "*") -DestinationPath (Join-Path $ReleaseDir "WardLens-Source.zip") -Force

$Provenance = if ($Signed) {
    "SIGNED BUILD: Authenticode signing and packaged self-tests completed in CI. Verify the publisher and SHA256SUMS.txt before use."
}
else {
    "UNSIGNED BUILD: packaged self-tests completed, but no trusted Authenticode certificate was configured. Hospital IT allowlisting or trusted signing is required; do not bypass endpoint security."
}
$Provenance | Set-Content -Encoding UTF8 (Join-Path $ReleaseDir "BUILD_PROVENANCE.txt")

$HashLines = Get-ChildItem $ReleaseDir -File |
    Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object Name |
    ForEach-Object {
        $Hash = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant()
        "$Hash  $($_.Name)"
    }
$HashLines | Set-Content -Encoding ASCII (Join-Path $ReleaseDir "SHA256SUMS.txt")
Write-Host "Release artifacts: $ReleaseDir"
