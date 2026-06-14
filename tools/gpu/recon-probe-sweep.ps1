# recon-probe-sweep.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Runs the recon timing probe across resolutions to show GPU scaling headroom.
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot; if (-not $dir) { $dir = 'G:\Temp\mlv-gpu-profile\gpu' }
$exe = Join-Path $dir 'cuda_recon_probe.exe'
$cudaBin = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName + '\bin'
$env:PATH = "$cudaBin;$env:PATH"
foreach ($r in @('1808 2268','2880 1620','3840 2160','5568 3132','8192 4320')) {
    $p = $r -split ' '
    Write-Host "==================== $($p[0])x$($p[1]) ===================="
    & $exe $p[0] $p[1] 50
}
