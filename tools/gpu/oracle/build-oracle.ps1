# build-oracle.ps1 - compile + run the Dual ISO full-20-bit recon CPU oracle.
#
# Compiles tools/gpu/oracle/oracle_main.c (which #includes dualiso.c + hist.c
# directly) into a single-TU scalar oracle with mingw gcc, then runs it with
# MLVAPP_DISABLE_AVX2=1 to emit the bit-exact reference test vectors.
#
# Usage:
#   pwsh -NoProfile -File tools\gpu\oracle\build-oracle.ps1 [-Width 1808] [-Height 2268] [-OutDir <dir>] [-NoRun]
#
# Defaults: 1808x2268, output dumps into tools/gpu/oracle/vectors/.

[CmdletBinding()]
param(
    [int]$Width = 1808,
    [int]$Height = 2268,
    [string]$OutDir = "",
    [switch]$NoRun
)

$ErrorActionPreference = "Stop"

$gcc = "C:/Qt/Tools/mingw1310_64/bin/gcc.exe"
if (-not (Test-Path $gcc)) { throw "gcc not found at $gcc" }

$here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $here "..\..\..")).Path
$srcMain  = Join-Path $here "oracle_main.c"
$exe      = Join-Path $here "oracle.exe"

if ([string]::IsNullOrEmpty($OutDir)) { $OutDir = Join-Path $here "vectors" }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

# Include paths the dualiso.c TU needs (raw.h, hist.h, opt_med.h, wirth.h,
# pipeline_stage_capture.h, debayer.h, StageTiming.h).
$inc = @(
    "-I", (Join-Path $repoRoot "src/mlv"),
    "-I", (Join-Path $repoRoot "src/mlv/llrawproc"),
    "-I", (Join-Path $repoRoot "src/debayer"),
    "-I", (Join-Path $repoRoot "src/debug")
)

# -fopenmp: StageTiming.h uses omp.h / omp_get_wtime; dualiso.c has #pragma omp.
# -DSTDOUT_SILENT: drop the recon's printf chatter.
# -static: bundle libgomp/libgcc/winpthread so oracle.exe is self-contained
#          (avoids "libgomp-1.dll not found" at runtime; portable test-vector gen).
$flags = @("-O2", "-fopenmp", "-DSTDOUT_SILENT", "-std=gnu11", "-Wall", "-static")

$cmd = @($srcMain) + $inc + $flags + @("-o", $exe, "-lpthread", "-lm")

Write-Host "[build-oracle] gcc $($cmd -join ' ')" -ForegroundColor Cyan
& $gcc @cmd
if ($LASTEXITCODE -ne 0) { throw "compile failed (exit $LASTEXITCODE)" }
Write-Host "[build-oracle] compiled -> $exe" -ForegroundColor Green

if ($NoRun) { return }

# Run on the scalar parity path.
$env:MLVAPP_DISABLE_AVX2 = "1"
Write-Host "[build-oracle] running: oracle.exe $Width $Height `"$OutDir`"  (MLVAPP_DISABLE_AVX2=1)" -ForegroundColor Cyan
& $exe $Width $Height $OutDir
if ($LASTEXITCODE -ne 0) { throw "oracle run failed (exit $LASTEXITCODE)" }
Write-Host "[build-oracle] vectors written to $OutDir" -ForegroundColor Green
Get-ChildItem $OutDir | Select-Object Name, Length | Format-Table -AutoSize
