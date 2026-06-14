# smoke-build-run.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds and runs cuda_smoke to validate the nvcc/MSVC/4090 build+run path.
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }
$src = Join-Path $dir 'cuda_smoke.cu'
$out = Join-Path $dir 'cuda_smoke.exe'

& pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'build-cuda.ps1') -Src $src -Out $out
if ($LASTEXITCODE -ne 0) { Write-Host 'BUILD FAILED'; exit 1 }

$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"
Write-Host '--- running cuda_smoke ---'
& $out
Write-Host "smoke exit=$LASTEXITCODE"
exit $LASTEXITCODE
