param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Label
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

function Assert-FileHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected
    )
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA-256 mismatch for $Path. Expected $Expected; received $Actual."
    }
}

Invoke-Checked -FilePath "python" -ArgumentList @("scripts\check_version.py") -Label "Version check"
Invoke-Checked -FilePath "python" -ArgumentList @("scripts\check_portable.py") -Label "Portable source check"
if (-not $SkipTests) {
    Invoke-Checked -FilePath "python" -ArgumentList @("-m", "ruff", "check", ".") -Label "Ruff"
    Invoke-Checked -FilePath "python" -ArgumentList @("-m", "pytest") -Label "Pytest"
}

$VersionMatch = Select-String -Path "pyproject.toml" -Pattern '^version = "([^"]+)"$'
if (-not $VersionMatch) { throw "Could not read the WardLens version." }
$Version = $VersionMatch.Matches[0].Groups[1].Value
$RuntimeManifest = Get-Content "packaging\portable-runtime.json" -Raw | ConvertFrom-Json
$CommitOutput = & git -c "safe.directory=$ProjectRoot" rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or -not $CommitOutput) { throw "Could not read the source commit." }
$Commit = $CommitOutput.Trim()

$DistDir = Join-Path $ProjectRoot "dist"
$ReleaseDir = Join-Path $DistDir "release"
$StageDir = Join-Path $DistDir "portable-stage"
$CacheDir = Join-Path $DistDir "cache"
$PackageRoot = Join-Path $StageDir "WardLens"
$RuntimeDir = Join-Path $PackageRoot "runtime"
$AppDir = Join-Path $PackageRoot "app"
$PackagesDir = Join-Path $PackageRoot "packages"
$ThirdPartyDir = Join-Path $PackageRoot "THIRD-PARTY"

foreach ($Path in @($ReleaseDir, $StageDir)) {
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $ReleaseDir, $StageDir, $CacheDir, $PackageRoot, $RuntimeDir, $AppDir, $PackagesDir, $ThirdPartyDir | Out-Null

$RuntimeFileName = Split-Path ([Uri]$RuntimeManifest.url).AbsolutePath -Leaf
$RuntimeZip = Join-Path $CacheDir $RuntimeFileName
$SpdxFile = Join-Path $CacheDir "$RuntimeFileName.spdx.json"
if (Test-Path -LiteralPath $RuntimeZip) {
    try { Assert-FileHash -Path $RuntimeZip -Expected $RuntimeManifest.sha256 }
    catch { Remove-Item -LiteralPath $RuntimeZip -Force }
}
if (-not (Test-Path -LiteralPath $RuntimeZip)) {
    Write-Host "Downloading the pinned Python Software Foundation runtime..."
    Invoke-WebRequest -Uri $RuntimeManifest.url -OutFile $RuntimeZip
}
Assert-FileHash -Path $RuntimeZip -Expected $RuntimeManifest.sha256
Invoke-WebRequest -Uri $RuntimeManifest.spdx_url -OutFile $SpdxFile

$Spdx = Get-Content $SpdxFile -Raw | ConvertFrom-Json
$CpythonPackage = $Spdx.packages | Where-Object { $_.name -eq "CPython" } | Select-Object -First 1
if (-not $CpythonPackage) { throw "The Python SPDX document does not contain CPython." }
$SpdxHash = $CpythonPackage.checksums |
    Where-Object { $_.algorithm -eq "SHA256" } |
    Select-Object -ExpandProperty checksumValue -First 1
if ($SpdxHash.ToLowerInvariant() -ne $RuntimeManifest.sha256.ToLowerInvariant()) {
    throw "The Python SPDX checksum does not match portable-runtime.json."
}

Expand-Archive -LiteralPath $RuntimeZip -DestinationPath $RuntimeDir -Force
$RuntimePython = Join-Path $RuntimeDir "python.exe"
if (-not (Test-Path -LiteralPath $RuntimePython)) { throw "The extracted Python runtime is incomplete." }

$PipArguments = @(
    "-m", "pip", "install",
    "--disable-pip-version-check",
    "--no-deps",
    "--no-compile",
    "--require-hashes",
    "--only-binary=:all:",
    "--platform", "any",
    "--implementation", "py",
    "--python-version", "3.14",
    "--abi", "none",
    "--target", $PackagesDir,
    "-r", (Join-Path $ProjectRoot "packaging\portable-requirements.txt")
)
Invoke-Checked -FilePath $RuntimePython -ArgumentList $PipArguments -Label "Pure-Python dependency installation"

foreach ($ScriptDirectory in @("bin", "Scripts")) {
    $Target = Join-Path $PackagesDir $ScriptDirectory
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
}
$UnexpectedNative = Get-ChildItem -LiteralPath $PackagesDir -Recurse -File |
    Where-Object { $_.Extension.ToLowerInvariant() -in @(".exe", ".dll", ".pyd", ".com", ".sys") }
if ($UnexpectedNative) {
    $Names = ($UnexpectedNative | ForEach-Object { $_.FullName.Substring($PackagesDir.Length + 1) }) -join ", "
    throw "Third-party native binaries are forbidden in the portable package: $Names"
}

$RuntimePrune = @(
    "Doc", "include", "libs", "Scripts", "Tools",
    "Lib\ensurepip", "Lib\idlelib", "Lib\site-packages", "Lib\test",
    "Lib\tkinter\test", "Lib\turtledemo", "Lib\venv"
)
foreach ($RelativePath in $RuntimePrune) {
    $Target = Join-Path $RuntimeDir $RelativePath
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir "Lib\site-packages") | Out-Null

Copy-Item -LiteralPath "src\wardlens" -Destination (Join-Path $AppDir "wardlens") -Recurse
Get-ChildItem -LiteralPath $PackageRoot -Recurse -Directory -Filter "__pycache__" |
    Sort-Object FullName -Descending |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $PackageRoot -Recurse -File |
    Where-Object { $_.Extension.ToLowerInvariant() -in @(".pyc", ".pyo") } |
    Remove-Item -Force

@(
    ".",
    "Lib",
    "DLLs",
    "..\app",
    "..\packages",
    "import site"
) | Set-Content -LiteralPath (Join-Path $RuntimeDir "python314._pth") -Encoding ASCII

Copy-Item -LiteralPath "packaging\Start-WardLens.bat" -Destination $PackageRoot
Copy-Item -LiteralPath "packaging\Diagnose-WardLens.bat" -Destination $PackageRoot
Copy-Item -LiteralPath "packaging\README-FIRST.txt" -Destination $PackageRoot
Copy-Item -LiteralPath "README.md", "SECURITY.md", "LICENSE" -Destination $PackageRoot
Copy-Item -LiteralPath "docs" -Destination (Join-Path $PackageRoot "docs") -Recurse
Copy-Item -LiteralPath $SpdxFile -Destination (Join-Path $ThirdPartyDir "PYTHON-RUNTIME-SPDX.json")
Copy-Item -LiteralPath "packaging\portable-requirements.txt" -Destination (Join-Path $ThirdPartyDir "PYTHON-PACKAGES.txt")
if (Test-Path -LiteralPath (Join-Path $RuntimeDir "LICENSE.txt")) {
    Copy-Item -LiteralPath (Join-Path $RuntimeDir "LICENSE.txt") -Destination (Join-Path $ThirdPartyDir "PYTHON-LICENSE.txt")
}

$SignedFiles = @("python.exe", "pythonw.exe", "python314.dll")
foreach ($RelativePath in $SignedFiles) {
    $Target = Join-Path $RuntimeDir $RelativePath
    if (-not (Test-Path -LiteralPath $Target)) { throw "Signed runtime file is missing: $RelativePath" }
    $Signature = Get-AuthenticodeSignature -LiteralPath $Target
    if ($Signature.Status -ne "Valid") { throw "Invalid Authenticode signature on runtime/${RelativePath}: $($Signature.Status)" }
    if (-not $Signature.SignerCertificate -or $Signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        throw "Unexpected Authenticode publisher on runtime/$RelativePath."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $RuntimeDir "DLLs\python.cat"))) {
    throw "The Python runtime security catalog is missing."
}

$ProvenanceLines = @(
    "WardLens portable build provenance",
    "wardlens_version: $Version",
    "source_commit: $Commit",
    "built_utc: $([DateTime]::UtcNow.ToString('o'))",
    "runtime_publisher: $($RuntimeManifest.publisher)",
    "runtime_version: $($RuntimeManifest.version)",
    "runtime_url: $($RuntimeManifest.url)",
    "runtime_sha256: $($RuntimeManifest.sha256)",
    "runtime_authenticode: Valid (python.exe, pythonw.exe, python314.dll)",
    "third_party_native_binaries_outside_runtime: none",
    "launcher: visible BAT; no PowerShell, installer, pip, or policy bypass on target"
)
$ProvenanceLines | Set-Content -LiteralPath (Join-Path $PackageRoot "BUILD_PROVENANCE.txt") -Encoding UTF8
$ProvenanceLines | Set-Content -LiteralPath (Join-Path $ReleaseDir "BUILD_PROVENANCE.txt") -Encoding UTF8

Invoke-Checked -FilePath $RuntimePython -ArgumentList @("-I", "-B", "-X", "utf8", "-m", "wardlens", "--version") -Label "Portable version test"
Invoke-Checked -FilePath $RuntimePython -ArgumentList @("-I", "-B", "-X", "utf8", "-m", "wardlens", "--self-test") -Label "Portable core self-test"
Invoke-Checked -FilePath $RuntimePython -ArgumentList @("-I", "-B", "-X", "utf8", "-m", "wardlens", "--ui-self-test") -Label "Portable visible UI self-test"
Invoke-Checked -FilePath $RuntimePython -ArgumentList @("-I", "-B", "-X", "utf8", "-m", "wardlens", "--diagnose") -Label "Portable diagnostic test"

$env:WARDLENS_NO_PAUSE = "1"
try {
    Invoke-Checked -FilePath "cmd.exe" -ArgumentList @("/d", "/c", "call", (Join-Path $PackageRoot "Start-WardLens.bat"), "--self-test") -Label "Exact BAT launcher test"
    Invoke-Checked -FilePath "cmd.exe" -ArgumentList @("/d", "/c", "call", (Join-Path $PackageRoot "Diagnose-WardLens.bat")) -Label "Exact diagnostic BAT test"
}
finally {
    Remove-Item Env:WARDLENS_NO_PAUSE -ErrorAction SilentlyContinue
    $DiagnosticOutput = Join-Path $PackageRoot "WardLens-Diagnostics.txt"
    if (Test-Path -LiteralPath $DiagnosticOutput) { Remove-Item -LiteralPath $DiagnosticOutput -Force }
}

Get-ChildItem -LiteralPath $PackageRoot -Recurse -Directory -Filter "__pycache__" |
    Sort-Object FullName -Descending |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $PackageRoot -Recurse -File |
    Where-Object { $_.Extension.ToLowerInvariant() -in @(".pyc", ".pyo") } |
    Remove-Item -Force

Invoke-Checked -FilePath "python" -ArgumentList @("scripts\check_portable.py", "--package", $PackageRoot) -Label "Portable package policy check"

$PortableZip = Join-Path $ReleaseDir "WardLens-Portable.zip"
Invoke-Checked -FilePath "python" -ArgumentList @("scripts\create_portable_zip.py", $PackageRoot, $PortableZip) -Label "Portable ZIP creation"
& (Join-Path $PSScriptRoot "test_release_zip.ps1") -ZipPath $PortableZip
$SourceZip = Join-Path $ReleaseDir "WardLens-Source.zip"
Invoke-Checked -FilePath "git" -ArgumentList @("-c", "safe.directory=$ProjectRoot", "archive", "--format=zip", "--output=$SourceZip", "HEAD") -Label "Source archive"

$HashLines = Get-ChildItem -LiteralPath $ReleaseDir -File |
    Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object Name |
    ForEach-Object {
        $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        "$Hash  $($_.Name)"
    }
$HashLines | Set-Content -LiteralPath (Join-Path $ReleaseDir "SHA256SUMS.txt") -Encoding ASCII
Write-Host "Release artifacts: $ReleaseDir"
