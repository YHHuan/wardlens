param(
    [Parameter(Mandatory = $true)][string]$PfxPath,
    [Parameter(Mandatory = $true)][string]$TargetPath,
    [Parameter(Mandatory = $true)][Security.SecureString]$PfxPassword
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $PfxPath -PathType Leaf)) { throw "PFX file not found." }
if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) { throw "Target file not found." }
$SignTool = Get-Command signtool.exe -ErrorAction Stop
$PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PfxPassword)
try {
    $PlainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($PasswordPointer)
    & $SignTool.Source sign /fd SHA256 /td SHA256 /tr "http://timestamp.digicert.com" /f $PfxPath /p $PlainPassword $TargetPath
    if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE" }
    & $SignTool.Source verify /pa /v $TargetPath
    if ($LASTEXITCODE -ne 0) { throw "signature verification failed" }
}
finally {
    $PlainPassword = $null
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer)
}
