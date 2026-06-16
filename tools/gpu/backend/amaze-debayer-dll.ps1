# amaze-debayer-dll.ps1  (RUN ON ULTRA-MAGNUS, over SSH)
# Builds the CUDA AMaZE debayer backend DLL and verifies its exported C ABI.
param(
    [string]$Dir = $PSScriptRoot,
    [string]$Arch = 'sm_89',
    [switch]$RunGlTexture
)
$ErrorActionPreference = 'Stop'

if (-not $Dir) { $Dir = $PSScriptRoot }

$src = Join-Path $Dir 'igpu_amaze_debayer_cuda.cu'
$dll = Join-Path $Dir 'igpu_amaze_debayer_cuda.dll'
$def = Join-Path $Dir 'igpu_amaze_debayer_cuda.def'
$testsrc = Join-Path $Dir 'amaze_dll_test.cpp'
$testexe = Join-Path $Dir 'amaze_dll_test.exe'

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vsRoot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null | Select-Object -First 1
if (-not $vsRoot) { throw "MSVC (VC tools) not found via vswhere" }
$vcvars = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "vcvars64 not found: $vcvars" }

$cudaRoot = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName
$nvcc = Join-Path $cudaRoot 'bin\nvcc.exe'
if (-not (Test-Path $nvcc)) { throw "nvcc not found under $cudaRoot" }

$gpuRoot = (Resolve-Path (Join-Path $Dir '..')).Path
$cmdFile = Join-Path $env:TEMP 'amaze_debayer_dll.cmd'
@(
    '@echo off',
    "call `"$vcvars`" >nul",
    'echo === building igpu_amaze_debayer_cuda.dll (nvcc -shared --fmad=false, exports via .def) ===',
    "`"$nvcc`" -arch=$Arch -O3 --fmad=false -shared -allow-unsupported-compiler -Xcompiler `"/MD`" -Xlinker `"/DEF:$def`" -I `"$Dir`" -I `"$gpuRoot`" `"$src`" -o `"$dll`"",
    'if errorlevel 1 exit /b 11',
    'echo === building amaze_dll_test.exe (cl, LoadLibrary harness, optional WGL texture check) ===',
    "cl /nologo /EHsc /O2 /I `"$Dir`" /I `"$gpuRoot`" `"$testsrc`" /Fe:`"$testexe`" /Fo:`"$Dir\amaze_dll_test.obj`" opengl32.lib user32.lib gdi32.lib",
    'if errorlevel 1 exit /b 12',
    'exit /b 0'
) | Set-Content -Encoding ASCII $cmdFile

Write-Host "build: $src -> $dll (arch=$Arch)"
& cmd /c "`"$cmdFile`"" 2>&1 | ForEach-Object { Write-Host $_ }
$rc = $LASTEXITCODE
Write-Host "build exit=$rc"
if ($rc -ne 0) { Write-Host 'BUILD FAILED'; exit $rc }

Write-Host '--- artifacts ---'
Get-ChildItem -LiteralPath $Dir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('igpu_amaze_debayer_cuda.dll', 'igpu_amaze_debayer_cuda.lib', 'igpu_amaze_debayer_cuda.exp', 'amaze_dll_test.exe') } |
    Select-Object Name,Length | Format-Table -AutoSize

Write-Host '--- export verification ---'
$dumpbinCommand = Get-Command dumpbin.exe -ErrorAction SilentlyContinue
$dumpbinPath = $null
if ($dumpbinCommand) {
    $dumpbinPath = $dumpbinCommand.Source
    if (-not $dumpbinPath) { $dumpbinPath = $dumpbinCommand.Path }
}
if (-not $dumpbinPath) {
    $dumpbinFile = Get-ChildItem "$vsRoot\VC\Tools\MSVC" -Recurse -Filter dumpbin.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($dumpbinFile) { $dumpbinPath = $dumpbinFile.FullName }
}
if (-not $dumpbinPath) { throw "dumpbin.exe not found after build" }

$exports = & $dumpbinPath /EXPORTS $dll
$required = @(
    'igpu_amaze_debayer_create',
    'igpu_amaze_debayer_destroy',
    'igpu_amaze_debayer_abi_version',
    'igpu_amaze_debayer_describe',
    'igpu_amaze_debayer_run',
    'igpu_amaze_debayer_run_gl_texture',
    'igpu_amaze_debayer_run_post_wb_gl_texture',
    'igpu_amaze_debayer_last_timing'
)
$missing = @()
foreach ($name in $required) {
    if (-not ($exports | Select-String -SimpleMatch $name -Quiet)) {
        $missing += $name
    }
}
if ($missing.Count -gt 0) {
    Write-Host "missing exports: $($missing -join ', ')"
    exit 12
}
Write-Host "export verification: PASS"

if ($RunGlTexture) {
    $cudaBin = Join-Path $cudaRoot 'bin'
    $env:PATH = "$cudaBin;$Dir;$env:PATH"
    Write-Host '--- running amaze_dll_test --gl-texture ---'
    Push-Location $Dir
    & $testexe --gl-texture $dll
    $trc = $LASTEXITCODE
    Pop-Location
    Write-Host "amaze_dll_test exit=$trc"
    exit $trc
}
exit 0
