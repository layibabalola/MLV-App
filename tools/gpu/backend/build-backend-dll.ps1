# build-backend-dll.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds the igpu_recon CUDA backend DLL (+ import lib) with nvcc, then builds
# the host LoadLibrary test harness with MSVC cl, and runs it against the oracle
# vectors to prove 0-LSB parity THROUGH the ABI.
#
# Layout expected in $Dir (the backend deploy dir):
#   igpu_recon_cuda.cu, dll_test.cpp, igpu_recon.h
# Outputs (in $Dir):
#   igpu_recon_cuda.dll, igpu_recon_cuda.lib, igpu_recon_cuda.exp, dll_test.exe
param(
    [string]$Dir      = $PSScriptRoot,
    [string]$Vectors  = 'G:\Temp\mlv-gpu-profile\oracle\vectors',
    [string]$Arch     = 'sm_89'
)
$ErrorActionPreference = 'Stop'
if (-not $Dir) { $Dir = 'G:\Temp\mlv-gpu-profile\backend' }

$src     = Join-Path $Dir 'igpu_recon_cuda.cu'
$dll     = Join-Path $Dir 'igpu_recon_cuda.dll'
$def     = Join-Path $Dir 'igpu_recon_cuda.def'
$testsrc = Join-Path $Dir 'dll_test.cpp'
$testexe = Join-Path $Dir 'dll_test.exe'

# locate MSVC vcvars64 + nvcc (same discovery as build-cuda.ps1)
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vsRoot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null | Select-Object -First 1
if (-not $vsRoot) { throw "MSVC (VC tools) not found via vswhere" }
$vcvars = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "vcvars64 not found: $vcvars" }

$cudaRoot = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName
$nvcc = Join-Path $cudaRoot 'bin\nvcc.exe'
if (-not (Test-Path $nvcc)) { throw "nvcc not found under $cudaRoot" }
$cudaBin = Join-Path $cudaRoot 'bin'

# Single .cmd: set MSVC env once, then build DLL (nvcc -shared) + test (cl).
# nvcc -shared emits the .dll AND the import .lib/.exp automatically.
# --fmad=false for IEEE float parity with the CPU oracle (same as parity probe).
$cmdFile = Join-Path $env:TEMP 'build_backend_dll.cmd'
@(
    '@echo off',
    "call `"$vcvars`" >nul",
    'echo === building igpu_recon_cuda.dll (nvcc -shared --fmad=false, exports via .def) ===',
    "`"$nvcc`" -arch=$Arch -O3 --fmad=false -shared -allow-unsupported-compiler -Xcompiler `"/MD`" -Xlinker `"/DEF:$def`" -I `"$Dir`" `"$src`" -o `"$dll`"",
    'if errorlevel 1 exit /b 11',
    'echo === building dll_test.exe (cl, LoadLibrary harness, no CUDA link) ===',
    "cl /nologo /EHsc /O2 /I `"$Dir`" `"$testsrc`" /Fe:`"$testexe`" /Fo:`"$Dir\dll_test.obj`"",
    'if errorlevel 1 exit /b 12',
    'exit /b 0'
) | Set-Content -Encoding ASCII $cmdFile

Write-Host "build: $src -> $dll  ;  $testsrc -> $testexe  (arch=$Arch)"
& cmd /c "`"$cmdFile`"" 2>&1 | ForEach-Object { Write-Host $_ }
$rc = $LASTEXITCODE
Write-Host "build exit=$rc"
if ($rc -ne 0) { Write-Host 'BUILD FAILED'; exit $rc }

# show emitted artifacts
Write-Host '--- artifacts ---'
Get-ChildItem $Dir -Include igpu_recon_cuda.dll,igpu_recon_cuda.lib,igpu_recon_cuda.exp,dll_test.exe -File -ErrorAction SilentlyContinue |
    Select-Object Name,Length | Format-Table -AutoSize

# run the LoadLibrary parity harness (needs cudart on PATH)
$env:PATH = "$cudaBin;$Dir;$env:PATH"
Write-Host '--- running dll_test (LoadLibrary + GetProcAddress + run + compare) ---'
Push-Location $Dir
& $testexe $Vectors $dll
$prc = $LASTEXITCODE
Pop-Location
Write-Host "dll_test exit=$prc"
exit $prc
