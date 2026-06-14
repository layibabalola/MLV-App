# build-cuda.ps1  (RUN ON ULTRA-MAGNUS)
# Generic nvcc build helper. Sets up the MSVC environment (vcvars64) and compiles
# a .cu with nvcc for the 4090 (sm_89). Writes a temp .cmd to avoid nested-quote
# issues with spaced install paths.
param(
    [Parameter(Mandatory)][string]$Src,
    [Parameter(Mandatory)][string]$Out,
    [string]$Arch  = 'sm_89',      # Ada Lovelace = RTX 4090
    [string]$Extra = ''            # extra nvcc flags (e.g. -O3 -lineinfo)
)
$ErrorActionPreference = 'Stop'

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vsRoot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null | Select-Object -First 1
if (-not $vsRoot) { throw "MSVC (VC tools) not found via vswhere" }
$vcvars = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "vcvars64 not found: $vcvars" }

$cudaRoot = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory |
    Sort-Object Name -Descending | Select-Object -First 1).FullName
$nvcc = Join-Path $cudaRoot 'bin\nvcc.exe'
if (-not (Test-Path $nvcc)) { throw "nvcc not found under $cudaRoot" }

$cmdFile = Join-Path $env:TEMP ("build_" + [System.IO.Path]::GetFileNameWithoutExtension($Out) + ".cmd")
@(
    '@echo off',
    "call `"$vcvars`" >nul",
    "`"$nvcc`" -arch=$Arch -O3 $Extra -allow-unsupported-compiler `"$Src`" -o `"$Out`"",
    'exit /b %ERRORLEVEL%'
) | Set-Content -Encoding ASCII $cmdFile

Write-Host "build: $Src -> $Out (arch=$Arch) via $vcvars + $nvcc"
& cmd /c "`"$cmdFile`"" 2>&1 | ForEach-Object { Write-Host $_ }
$rc = $LASTEXITCODE
Write-Host "nvcc exit=$rc"
exit $rc
