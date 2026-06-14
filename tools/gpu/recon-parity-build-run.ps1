# recon-parity-build-run.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds and runs the CUDA Dual ISO recon parity probe on the 4090.
# Compiles with --fmad=false for IEEE float parity with the CPU oracle.
#
# The probe is parameterized: it reads W/H + all scalars from each case's
# scalars.txt, so the SAME exe validates every case just by pointing it at a
# different vectors dir.
#
# Usage:
#   # single case (default base):
#   pwsh -File recon-parity-build-run.ps1 -Case base
#   # explicit vectors dir:
#   pwsh -File recon-parity-build-run.ps1 -VectorsDir 'G:\Temp\mlv-gpu-profile\oracle\vectors\clipped'
#   # all breadth cases (base, res8k, clipped, iso1600):
#   pwsh -File recon-parity-build-run.ps1 -AllCases
param(
    [string]$Case = "base",
    [string]$VectorsDir = "",
    [switch]$AllCases,
    [switch]$NoBuild
)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }
$src = Join-Path $dir 'cuda_recon_parity.cu'
$out = Join-Path $dir 'cuda_recon_parity.exe'
$vecRoot = 'G:\Temp\mlv-gpu-profile\oracle\vectors'

if (-not $NoBuild) {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'build-cuda.ps1') -Src $src -Out $out -Extra '--fmad=false'
    if ($LASTEXITCODE -ne 0) { Write-Host 'BUILD FAILED'; exit 1 }
}

$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"

$cases = @()
if ($AllCases) { $cases = @('base', 'res8k', 'clipped', 'iso1600') }
elseif (-not [string]::IsNullOrEmpty($VectorsDir)) { $cases = @($null) }   # explicit dir
else { $cases = @($Case) }

$rc = 0
foreach ($c in $cases) {
    $vd = if ($null -eq $c) { $VectorsDir } else { Join-Path $vecRoot $c }
    Write-Host "--- running cuda_recon_parity ($vd) ---"
    & $out $vd
    if ($LASTEXITCODE -ne 0) { $rc = $LASTEXITCODE }
}
Write-Host "parity exit=$rc"
exit $rc
