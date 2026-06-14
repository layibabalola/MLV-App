# recon-opt-build-run.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds and runs the CUDA recon OPTIMIZATION / max-throughput probe on the 4090.
#
# Unlike the parity probe (--fmad=false for IEEE parity), this is a TIMING study,
# so it builds with --use_fast_math (FMA contraction / fast intrinsics ALLOWED).
# Correctness is already proven by cuda_recon_parity.cu; trading a few ULP for
# speed is intentional here.
#
# Usage:
#   pwsh -File recon-opt-build-run.ps1                 # 4.1 MP, 200 iters
#   pwsh -File recon-opt-build-run.ps1 -W 8192 -H 4320 # 8K
#   pwsh -File recon-opt-build-run.ps1 -Both           # 4.1 MP + 8K back to back
param(
    [int]$W = 1808,
    [int]$H = 2268,
    [int]$Iters = 200,
    [switch]$Both,
    [switch]$NoBuild
)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }
$src = Join-Path $dir 'cuda_recon_opt.cu'
$out = Join-Path $dir 'cuda_recon_opt.exe'

if (-not $NoBuild) {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'build-cuda.ps1') -Src $src -Out $out -Extra '--use_fast_math'
    if ($LASTEXITCODE -ne 0) { Write-Host 'BUILD FAILED'; exit 1 }
}

$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"

if ($Both) {
    Write-Host '==================== 1808x2268 (4.1 MP) ===================='
    & $out 1808 2268 $Iters
    Write-Host ''
    Write-Host '==================== 8192x4320 (8K) ===================='
    & $out 8192 4320 $Iters
} else {
    Write-Host "==================== ${W}x${H} ===================="
    & $out $W $H $Iters
}
Write-Host "opt probe exit=$LASTEXITCODE"
exit $LASTEXITCODE
