# install-tier2-toolchain.ps1
# RUN ON ULTRA-MAGNUS (admin, over SSH). Installs the Tier 2 build prereqs:
# MSVC C++ Build Tools (VS 2022 BuildTools + VCTools workload) and the CUDA
# Toolkit, via winget. Verifies cl.exe + nvcc and logs everything to the SMB
# bundle so the VM can read progress/result.

$ErrorActionPreference = 'Continue'
$log = 'G:\Temp\mlv-gpu-profile\tier2-install.log'
"=== Tier 2 toolchain install $(Get-Date -Format o) host=$env:COMPUTERNAME ===" | Set-Content -Encoding ASCII $log
function L($m){ $s = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; $s | Add-Content -Encoding ASCII $log; Write-Host $s }

# Locate winget (it lives on the per-user WindowsApps PATH).
$winget = (Get-Command winget.exe -ErrorAction SilentlyContinue).Source
if (-not $winget) { $c = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'; if (Test-Path $c) { $winget = $c } }
if (-not $winget) { L 'winget NOT found - need direct-installer fallback. Stopping.'; "STATUS=winget_missing" | Add-Content -Encoding ASCII $log; exit 2 }
L "winget: $winget"; (& $winget --version 2>&1) | ForEach-Object { L "  $_" }

# 1) MSVC C++ Build Tools (the nvcc host compiler).
L '--- installing Microsoft.VisualStudio.2022.BuildTools (+ VCTools) ---'
& $winget install --id Microsoft.VisualStudio.2022.BuildTools -e --silent `
    --accept-source-agreements --accept-package-agreements --disable-interactivity `
    --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" 2>&1 |
    ForEach-Object { L "  $_" }
L "buildtools winget exit=$LASTEXITCODE"

# 2) CUDA Toolkit.
L '--- installing Nvidia.CUDA ---'
& $winget install --id Nvidia.CUDA -e --silent `
    --accept-source-agreements --accept-package-agreements --disable-interactivity 2>&1 |
    ForEach-Object { L "  $_" }
L "cuda winget exit=$LASTEXITCODE"

# 3) Verify.
L '--- verification ---'
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$cl = $null
if (Test-Path $vswhere) {
    $cl = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -find 'VC\Tools\MSVC\**\bin\Hostx64\x64\cl.exe' 2>$null | Select-Object -First 1
}
L ("cl.exe : " + $(if ($cl) { $cl } else { 'NOT FOUND' }))

$nvcc = (Get-Command nvcc.exe -ErrorAction SilentlyContinue).Source
if (-not $nvcc) {
    $cudaRoot = Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    if ($cudaRoot) { $c = Join-Path $cudaRoot.FullName 'bin\nvcc.exe'; if (Test-Path $c) { $nvcc = $c } }
}
L ("nvcc   : " + $(if ($nvcc) { $nvcc } else { 'NOT FOUND' }))
if ($nvcc) { (& $nvcc --version 2>&1 | Select-Object -Last 2) | ForEach-Object { L "  $_" } }

$ok = ($cl -and $nvcc)
L ("STATUS=" + $(if ($ok) { 'success' } else { 'incomplete' }))
L '=== DONE ==='
exit $(if ($ok) { 0 } else { 1 })
