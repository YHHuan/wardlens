param(
    [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $VsWhere)) { throw "MSVC build tools are required on the build machine only." }
$VsRoot = & $VsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or -not $VsRoot) { throw "No MSVC x64 build toolchain found." }
Import-Module (Join-Path $VsRoot.Trim() "Common7\Tools\Microsoft.VisualStudio.DevShell.dll")
Enter-VsDevShell -VsInstallPath $VsRoot.Trim() -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
$BuildDir = Join-Path $ProjectRoot "build\launcher"
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
$ResponseFile = Join-Path $BuildDir "launcher.rsp"
$Source = Join-Path $ProjectRoot "packaging\wardlens_launcher.c"
$Manifest = Join-Path $ProjectRoot "packaging\wardlens_launcher.manifest"
$Object = Join-Path $BuildDir "wardlens_launcher.obj"
@(
    '/nologo', '/O2', '/MT', '/W4', '/WX', '/utf-8', '/DUNICODE', '/D_UNICODE',
    ('/Fo:"' + $Object + '"'), ('/Fe:"' + $OutputPath + '"'), ('"' + $Source + '"'),
    ('/link /SUBSYSTEM:WINDOWS /MANIFEST:EMBED /MANIFESTINPUT:"' + $Manifest + '" user32.lib')
) | Set-Content -LiteralPath $ResponseFile -Encoding Unicode
# Use the compiler environment only while building; the shipped launcher never invokes a shell.
& cl.exe "@$ResponseFile"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputPath)) {
    throw "Native WardLens.exe build failed."
}
