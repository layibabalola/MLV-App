# recon-probe-build-run.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds and runs the CUDA recon timing probe on the 4090.
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }
$src = Join-Path $dir 'cuda_recon_probe.cu'
$out = Join-Path $dir 'cuda_recon_probe.exe'

& pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'build-cuda.ps1') -Src $src -Out $out -Extra '-lineinfo'
if ($LASTEXITCODE -ne 0) { Write-Host 'BUILD FAILED'; exit 1 }

$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"
Write-Host '--- running cuda_recon_probe (4.1 MP, 60 iters) ---'
& $out 1808 2268 60
Write-Host "probe exit=$LASTEXITCODE"
exit $LASTEXITCODE
