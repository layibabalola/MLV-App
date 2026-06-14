# check-ultra-magnus-toolchain.ps1
# Runs ON Ultra-Magnus. Read-only inventory for the Tier 2 CUDA lane.

$ErrorActionPreference = 'Continue'
$out = 'G:\Temp\mlv-gpu-profile\toolchain-inventory.json'

function CommandSource($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$cudaRoots = @()
foreach ($path in @(
    'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA',
    'C:\Program Files\NVIDIA Corporation'
)) {
    if (Test-Path $path) {
        $cudaRoots += Get-ChildItem $path -Directory -ErrorAction SilentlyContinue |
            Select-Object Name,FullName,LastWriteTime
    }
}

$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
$vsInstances = @()
if (Test-Path $vswhere) {
    $raw = & $vswhere -all -prerelease -format json 2>$null
    if ($raw) {
        try { $vsInstances = $raw | ConvertFrom-Json } catch { $vsInstances = @() }
    }
}

$nvidiaSmi = @()
try {
    $nvidiaSmi = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1
} catch {
    $nvidiaSmi = @("nvidia-smi failed: $($_.Exception.Message)")
}

$msvcTools = @()
foreach ($vs in @($vsInstances)) {
    if ($vs.installationPath) {
        $vcvars = Join-Path $vs.installationPath 'VC\Auxiliary\Build\vcvars64.bat'
        $clCandidates = Get-ChildItem (Join-Path $vs.installationPath 'VC\Tools\MSVC') -Recurse -Filter cl.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\bin\\Hostx64\\x64\\cl\.exe$' } |
            Select-Object -First 5 FullName,LastWriteTime
        $msvcTools += [pscustomobject]@{
            installationPath = $vs.installationPath
            vcvars64 = if (Test-Path $vcvars) { $vcvars } else { $null }
            cl_candidates = $clCandidates
        }
    }
}

$result = [pscustomobject]@{
    host = $env:COMPUTERNAME
    captured = (Get-Date).ToString('o')
    os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber
    computer = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory
    nvidia_smi = $nvidiaSmi
    cuda_path = $env:CUDA_PATH
    nvcc = CommandSource 'nvcc.exe'
    cl = CommandSource 'cl.exe'
    cmake = CommandSource 'cmake.exe'
    ninja = CommandSource 'ninja.exe'
    git = CommandSource 'git.exe'
    vswhere = if (Test-Path $vswhere) { $vswhere } else { $null }
    cuda_roots = $cudaRoots
    visual_studio_instances = $vsInstances
    msvc_tools = $msvcTools
}

$result | ConvertTo-Json -Depth 8 | Set-Content -Encoding ASCII $out
Get-Content $out
