# amaze-debayer-stage-build-run.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds and runs the first CUDA stage probe for generic AMaZE debayering.
# This is a development parity probe only; it does not enable GPU AMaZE in MLVApp.
param(
    [switch]$NoBuild
)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }

$build = Join-Path $dir 'build-cuda.ps1'
$src = Join-Path $dir 'cuda_amaze_debayer_stage_probe.cu'
$out = Join-Path $dir 'cuda_amaze_debayer_stage_probe.exe'

if (-not $NoBuild) {
    Write-Host '=== building cuda_amaze_debayer_stage_probe (--fmad=false) ==='
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $build -Src $src -Out $out -Extra '--fmad=false'
    if ($LASTEXITCODE -ne 0) { Write-Host 'AMAZE DEBAYER STAGE BUILD FAILED'; exit 1 }
}

$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"

Write-Host "`n========== GENERIC AMaZE DEBAYER CUDA STAGE PROBE =========="
& $out
Write-Host "amaze debayer stage probe exit=$LASTEXITCODE"
exit $LASTEXITCODE
