# recon-amaze-parity-build-run.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds and runs the CUDA Dual ISO AMaZE recon parity probe on the 4090.
# Compiles with --fmad=false for IEEE float parity with the CPU oracle.
#
# AMaZE (interp_method=0) is the PRODUCTION DEFAULT (diso_averaging=0). This
# probe ports the dualiso-owned edge-directed reconstruction (gray, edge_dir,
# edge_interp -> dark/bright) + the validated downstream chain, taking the
# demosaiced red/green/blue float planes from the oracle's real demosaic dump.
#
# The probe reads W/H + all scalars (incl. amaze_row_width, interp_method) from
# each case's scalars.txt, so the SAME exe validates every AMaZE case by pointing
# it at a different vectors dir.
#
# Usage:
#   pwsh -File recon-amaze-parity-build-run.ps1 -Case amaze
#   pwsh -File recon-amaze-parity-build-run.ps1 -VectorsDir 'G:\Temp\mlv-gpu-profile\oracle\vectors\amaze_clip'
#   pwsh -File recon-amaze-parity-build-run.ps1 -AllCases
param(
    [string]$Case = "amaze",
    [string]$VectorsDir = "",
    [switch]$AllCases,
    [switch]$NoBuild
)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }
$src = Join-Path $dir 'cuda_recon_amaze_parity.cu'
$out = Join-Path $dir 'cuda_recon_amaze_parity.exe'
$vecRoot = 'G:\Temp\mlv-gpu-profile\oracle\vectors'

if (-not $NoBuild) {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'build-cuda.ps1') -Src $src -Out $out -Extra '--fmad=false'
    if ($LASTEXITCODE -ne 0) { Write-Host 'BUILD FAILED'; exit 1 }
}

$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"

$cases = @()
if ($AllCases) { $cases = @('amaze', 'amaze_clip', 'amaze_iso1600') }
elseif (-not [string]::IsNullOrEmpty($VectorsDir)) { $cases = @($null) }
else { $cases = @($Case) }

$rc = 0
foreach ($c in $cases) {
    $vd = if ($null -eq $c) { $VectorsDir } else { Join-Path $vecRoot $c }
    Write-Host "--- running cuda_recon_amaze_parity ($vd) ---"
    & $out $vd
    if ($LASTEXITCODE -ne 0) { $rc = $LASTEXITCODE }
}
Write-Host "amaze-parity exit=$rc"
exit $rc
