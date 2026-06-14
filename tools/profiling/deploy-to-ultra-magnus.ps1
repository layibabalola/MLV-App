# deploy-to-ultra-magnus.ps1
# RUN THIS ON THE VM (Virtual-Ten). Stages the slim Tier 1 GPU profiling bundle
# and (optionally) pushes it to Ultra-Magnus over SMB, then pulls results back.
#
# SAFETY: dry-run by default. Nothing is written to the host unless you pass
# -Execute. Without it, robocopy runs in /L (list-only) mode so you can review
# exactly what would transfer.
#
# Transport: SMB share. The default target is Ultra-Magnus' SSD temp share.
# Deployment only needs file copy; LAUNCHING the runner on the host still needs
# a session (console/RDP) or a remote-exec channel (OpenSSH/WinRM).
#
# Typical flow:
#   1) review:   .\deploy-to-ultra-magnus.ps1
#   2) push:     .\deploy-to-ultra-magnus.ps1 -Execute
#   3) (on host, console session) run: G:\Temp\mlv-gpu-profile\run-ultra-magnus-profile.ps1
#   4) pull:     .\deploy-to-ultra-magnus.ps1 -PullResults -Execute

[CmdletBinding()]
param(
    # Ultra-Magnus exposes G:\Temp as a writable SSD staging share.
    [string]$Target = "\\Ultra-Magnus\g\Temp\mlv-gpu-profile",
    [string]$Clip   = "tests\fixtures\clips\large_dual_iso.mlv",
    [switch]$Execute,
    [switch]$PullResults
)

$ErrorActionPreference = "Stop"
$repo = Split-Path (Split-Path $PSScriptRoot)   # tools\profiling -> repo root
$releaseSrc  = Join-Path $repo "platform\qt\build-release\release"
$receiptsSrc = Join-Path $repo "tests\fixtures\receipts"
$runnerSrc   = $PSScriptRoot
$clipSrc     = if ([System.IO.Path]::IsPathRooted($Clip)) { $Clip } else { Join-Path $repo $Clip }
$listOnly    = if ($Execute) { @() } else { @("/L") }

if (-not (Test-Path $releaseSrc)) { throw "Release runtime not found: $releaseSrc (build it first)" }
if (-not (Test-Path $clipSrc))    { throw "Clip not found: $clipSrc" }

function Rc($src, $dst, $extra) {
    $rcArgs = @($src, $dst) + $extra + $listOnly + @("/NFL","/NDL","/NJH","/NP","/R:1","/W:1")
    Write-Host ("robocopy {0} -> {1} {2}" -f $src, $dst, ($extra -join " "))
    & robocopy.exe @rcArgs | Out-Null
    # robocopy exit codes < 8 are success
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $src -> $dst" }
}

if (-not $Execute) {
    Write-Host "*** DRY RUN (list-only). Re-run with -Execute to actually transfer. ***" -ForegroundColor Yellow
}

if ($PullResults) {
    $resultsRemote = Join-Path $Target "results"
    $resultsLocal  = Join-Path $repo ".claude-state\profiling\20260613-gpu-lane-x1\ultra-magnus-results"
    if ($Execute) { New-Item -ItemType Directory -Force -Path $resultsLocal | Out-Null }
    Rc $resultsRemote $resultsLocal @("/E")
    Write-Host "Pulled host results -> $resultsLocal"
    exit 0
}

# Push the bundle.
Rc $releaseSrc  (Join-Path $Target "release")  @("/MIR")
Rc $receiptsSrc (Join-Path $Target "receipts") @("/E")
Rc (Split-Path $clipSrc) (Join-Path $Target "clips") @((Split-Path $clipSrc -Leaf))
Rc $runnerSrc   $Target @("run-ultra-magnus-profile.ps1")

Write-Host ""
if ($Execute) {
    $hostLocal = "G:\Temp\mlv-gpu-profile\run-ultra-magnus-profile.ps1"
    Write-Host "Bundle deployed to $Target" -ForegroundColor Green
    Write-Host "Next: on Ultra-Magnus (console session, NOT RDP) run:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File $hostLocal"
} else {
    Write-Host "Dry run complete. Add -Execute to transfer."
}
exit 0
