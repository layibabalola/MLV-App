# recon-parity-build-run.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds and runs the CUDA Dual ISO recon parity probe on the 4090.
# Compiles with --fmad=false for IEEE float parity with the CPU oracle.
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }
$src = Join-Path $dir 'cuda_recon_parity.cu'
$out = Join-Path $dir 'cuda_recon_parity.exe'

& pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'build-cuda.ps1') -Src $src -Out $out -Extra '--fmad=false'
if ($LASTEXITCODE -ne 0) { Write-Host 'BUILD FAILED'; exit 1 }

$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"
Write-Host '--- running cuda_recon_parity ---'
& $out 'G:\Temp\mlv-gpu-profile\oracle\vectors'
$rc = $LASTEXITCODE
Write-Host "parity exit=$rc"
exit $rc
