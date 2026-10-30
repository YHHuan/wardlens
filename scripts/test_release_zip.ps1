param(
    [Parameter(Mandatory = $true)][string]$ZipPath
)

$ErrorActionPreference = "Stop"
$ResolvedZip = (Resolve-Path -LiteralPath $ZipPath).Path
$SmokeRoot = Join-Path ([IO.Path]::GetTempPath()) ("WardLens portable 測試 " + [Guid]::NewGuid().ToString("N"))
$PreviousNoPause = [Environment]::GetEnvironmentVariable("WARDLENS_NO_PAUSE", "Process")

try {
    New-Item -ItemType Directory -Path $SmokeRoot | Out-Null
    Expand-Archive -LiteralPath $ResolvedZip -DestinationPath $SmokeRoot -Force
    $PackageRoot = Join-Path $SmokeRoot "WardLens"
    $Launcher = Join-Path $PackageRoot "Start-WardLens.bat"
    $DiagnosticLauncher = Join-Path $PackageRoot "Diagnose-WardLens.bat"
    $DesktopLauncher = Join-Path $PackageRoot "Start-WardLens-Desktop.bat"
    if (-not (Test-Path -LiteralPath $Launcher) -or -not (Test-Path -LiteralPath $DiagnosticLauncher)) {
        throw "The extracted ZIP does not contain the expected WardLens launchers."
    }
    & python (Join-Path $PSScriptRoot "test_windows_launcher.py") --exe (Join-Path $PackageRoot "WardLens.exe")
    if ($LASTEXITCODE -ne 0) { throw "Extracted native EXE launch tests failed." }

    $env:WARDLENS_NO_PAUSE = "1"
    foreach ($Test in @(
        @{ Label = "extracted BAT core test"; Path = $Launcher; Argument = "--self-test" },
        @{ Label = "extracted BAT visible UI test"; Path = $Launcher; Argument = "--ui-self-test" },
        @{ Label = "extracted BAT browser API test"; Path = $Launcher; Argument = "--web-self-test" },
        @{ Label = "extracted desktop BAT test"; Path = $DesktopLauncher; Argument = "--ui-self-test" },
        @{ Label = "extracted diagnostic BAT test"; Path = $DiagnosticLauncher; Argument = "" }
    )) {
        $CommandLine = "call `"$($Test.Path)`" $($Test.Argument)"
        & $env:ComSpec /d /s /c $CommandLine
        if ($LASTEXITCODE -ne 0) {
            throw "$($Test.Label) failed with exit code $LASTEXITCODE."
        }
    }
}
finally {
    if ($null -eq $PreviousNoPause) {
        Remove-Item Env:WARDLENS_NO_PAUSE -ErrorAction SilentlyContinue
    }
    else {
        $env:WARDLENS_NO_PAUSE = $PreviousNoPause
    }
    if (Test-Path -LiteralPath $SmokeRoot) {
        Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
    }
}

Write-Host "Extracted release ZIP smoke test passed."
