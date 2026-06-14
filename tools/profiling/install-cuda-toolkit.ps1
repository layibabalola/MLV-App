# install-cuda-toolkit.ps1
# RUN ON ULTRA-MAGNUS (admin, over SSH). Installs CUDA Toolkit 12.6 TOOLKIT-ONLY
# (no display driver -> avoids the conflict with the existing 596.36 driver that
# made the winget install fail). Downloads the network installer, runs it silent
# with explicit toolkit components, verifies nvcc, logs to the SMB bundle.
# CUDA 12.6 is chosen for compatibility with the installed MSVC 14.37 (VS 17.7);
# CUDA 13.x would reject that host compiler.

$ErrorActionPreference = 'Continue'
$log = 'G:\Temp\mlv-gpu-profile\cuda-install.log'
"=== CUDA toolkit install $(Get-Date -Format o) host=$env:COMPUTERNAME ===" | Set-Content -Encoding ASCII $log
function L($m){ $s = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; $s | Add-Content -Encoding ASCII $log; Write-Host $s }

$ver = '12.6.3'
$url = "https://developer.download.nvidia.com/compute/cuda/$ver/network_installers/cuda_${ver}_windows_network.exe"
$exe = Join-Path $env:TEMP "cuda_${ver}_network.exe"

L "downloading $url"
try { Invoke-WebRequest -Uri $url -OutFile $exe -TimeoutSec 900 } catch { L "download failed: $($_.Exception.Message)"; "STATUS=download_failed" | Add-Content -Encoding ASCII $log; exit 2 }
$mb = [math]::Round((Get-Item $exe).Length/1MB,1)
L "downloaded ${mb} MB"
if ($mb -lt 1) { L "file too small - likely 404/HTML, not the installer"; "STATUS=bad_download" | Add-Content -Encoding ASCII $log; exit 2 }

# Toolkit components only (note: NO display_driver). Names use major.minor.
$comp = 'nvcc_12.6 cudart_12.6 cuobjdump_12.6 nvprune_12.6 nvrtc_12.6 nvrtc_dev_12.6 cuda_profiler_api_12.6 thrust_12.6 npp_12.6 npp_dev_12.6 cublas_12.6 cublas_dev_12.6 visual_studio_integration_12.6'
L "running silent toolkit install (no driver): -s $comp"
$p = Start-Process $exe -ArgumentList ("-s " + $comp) -Wait -PassThru
L "installer exit=$($p.ExitCode)"

# Verify nvcc.
$nvcc = $null
$root = Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
if ($root) { $c = Join-Path $root.FullName 'bin\nvcc.exe'; if (Test-Path $c) { $nvcc = $c } }
L ("nvcc: " + $(if ($nvcc) { $nvcc } else { 'NOT FOUND' }))
if ($nvcc) {
    (& $nvcc --version 2>&1 | Select-Object -Last 2) | ForEach-Object { L "  $_" }
    "STATUS=success" | Add-Content -Encoding ASCII $log
    L '=== DONE (success) ==='
    exit 0
}
# Surface the NVIDIA installer log location for diagnosis if it failed.
$nvLog = Get-ChildItem "$env:TEMP\cuda_*" -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($nvLog) { L "NVIDIA installer logs: $($nvLog.FullName)" }
"STATUS=incomplete" | Add-Content -Encoding ASCII $log
L '=== DONE (incomplete) ==='
exit 1
