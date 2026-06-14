# pipeline-probe-build-run.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds + runs the full end-to-end GPU playback pipeline probe and the bilinear
# debayer parity probe on the 4090.
#
#   cuda_debayer_parity.cu  -> built --fmad=false (bit-exact vs CPU reference)
#   cuda_pipeline_probe.cu  -> built --use_fast_math (max-speed timing study)
#
# The pipeline probe runs at 4.1 MP (1808x2268) and 8K (8192x4320) with a
# half-res (1/2) viewport downscale. The debayer parity runs at 4.1 MP.
#
# Usage:
#   pwsh -File pipeline-probe-build-run.ps1            # build + run both, both res
#   pwsh -File pipeline-probe-build-run.ps1 -Iters 500 # tighter warm avg
#   pwsh -File pipeline-probe-build-run.ps1 -NoBuild   # rerun existing exes
param(
    [int]$Iters = 300,
    [int]$VpDiv = 2,
    [switch]$NoBuild,
    [switch]$ParityOnly,
    [switch]$TimingOnly
)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }

$build = Join-Path $dir 'build-cuda.ps1'
$parSrc = Join-Path $dir 'cuda_debayer_parity.cu'
$parOut = Join-Path $dir 'cuda_debayer_parity.exe'
$pipSrc = Join-Path $dir 'cuda_pipeline_probe.cu'
$pipOut = Join-Path $dir 'cuda_pipeline_probe.exe'

if (-not $NoBuild) {
    if (-not $TimingOnly) {
        Write-Host '=== building cuda_debayer_parity (--fmad=false) ==='
        & pwsh -NoProfile -ExecutionPolicy Bypass -File $build -Src $parSrc -Out $parOut -Extra '--fmad=false'
        if ($LASTEXITCODE -ne 0) { Write-Host 'DEBAYER PARITY BUILD FAILED'; exit 1 }
    }
    if (-not $ParityOnly) {
        Write-Host '=== building cuda_pipeline_probe (--use_fast_math) ==='
        & pwsh -NoProfile -ExecutionPolicy Bypass -File $build -Src $pipSrc -Out $pipOut -Extra '--use_fast_math'
        if ($LASTEXITCODE -ne 0) { Write-Host 'PIPELINE PROBE BUILD FAILED'; exit 1 }
    }
}

$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"

if (-not $TimingOnly) {
    Write-Host "`n========== DEBAYER PARITY (4.1 MP) =========="
    & $parOut 1808 2268
    Write-Host "debayer parity exit=$LASTEXITCODE"
}

if (-not $ParityOnly) {
    Write-Host "`n========== PIPELINE PROBE @ 4.1 MP (1808x2268) =========="
    & $pipOut 1808 2268 $Iters $VpDiv
    Write-Host "`n========== PIPELINE PROBE @ 8K (8192x4320) =========="
    & $pipOut 8192 4320 $Iters $VpDiv
}
