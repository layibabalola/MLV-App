param(
    [int]$Iterations = 10,
    [string[]]$Threads = @("1", "4"),
    [string]$OutputRoot = "",
    [string]$PerfExe = "",
    [switch]$Cold8bit,
    [uint64]$RawCacheMB = 0,
    [int]$CacheCpuCores = 1,
    [switch]$RequireBaseline
)

$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $PSCommandPath }
    return (Resolve-Path (Join-Path $scriptDir "..\..")).Path
}

function Find-FirstCommand([string[]]$Names) {
    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

function Ensure-RuntimePath {
    $qtBin = Find-FirstCommand @(
        "qmake",
        "C:\Qt\6.10.2\mingw_64\bin\qmake.exe"
    )
    $makeBin = Find-FirstCommand @(
        "mingw32-make",
        "C:\Qt\Tools\mingw1310_64\bin\mingw32-make.exe"
    )

    $paths = New-Object System.Collections.Generic.List[string]
    if ($qtBin) { $paths.Add((Split-Path -Parent $qtBin)) }
    if ($makeBin) { $paths.Add((Split-Path -Parent $makeBin)) }

    foreach ($path in $paths | Select-Object -Unique) {
        if ($env:PATH -notlike "*$path*") {
            $env:PATH = "$path;$env:PATH"
        }
    }
}

function Find-PerfExe([string]$Root) {
    $candidates = @(
        (Join-Path $Root "tests\perf\debug\perf_tests.exe"),
        (Join-Path $Root "tests\perf\release\perf_tests.exe"),
        (Join-Path $Root "tests\build-runtime-profile\perf\release\perf_tests.exe"),
        (Join-Path $Root "tests\build-runtime-profile\perf\debug\perf_tests.exe"),
        (Join-Path $Root "tests\build-runtime-profile\release\perf_tests.exe"),
        (Join-Path $Root "tests\build-all\perf\release\perf_tests.exe"),
        (Join-Path $Root "tests\build-all\perf\debug\perf_tests.exe"),
        (Join-Path $Root "tests\build-all\release\perf_tests.exe"),
        (Join-Path $Root "tests\build-codex-current\perf\release\perf_tests.exe"),
        (Join-Path $Root "tests\build-codex-current\perf\debug\perf_tests.exe"),
        (Join-Path $Root "tests\build-codex-current\release\perf_tests.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    }

    $recursive = Get-ChildItem -Path (Join-Path $Root "tests") -Recurse -Filter "perf_tests.exe" -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($recursive) { return $recursive.FullName }

    return $null
}

function Ensure-PerfExe([string]$Root, [string]$RequestedPerfExe) {
    if ($RequestedPerfExe) {
        if (-not (Test-Path $RequestedPerfExe)) {
            throw "Specified perf executable does not exist: $RequestedPerfExe"
        }
        return (Resolve-Path $RequestedPerfExe).Path
    }

    $found = Find-PerfExe $Root
    if ($found) { return $found }

    $qmake = Find-FirstCommand @(
        "qmake",
        "C:\Qt\6.10.2\mingw_64\bin\qmake.exe"
    )
    $make = Find-FirstCommand @(
        "mingw32-make",
        "C:\Qt\Tools\mingw1310_64\bin\mingw32-make.exe"
    )

    if (-not $qmake -or -not $make) {
        throw "Could not find qmake and mingw32-make to build perf_tests automatically."
    }

    $buildDir = Join-Path $Root "tests\build-runtime-profile"
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

    Push-Location $buildDir
    try {
        & $qmake (Join-Path $Root "tests\tests.pro")
        if ($LASTEXITCODE -ne 0) { throw "qmake failed with exit code $LASTEXITCODE" }
        & $make -j4
        if ($LASTEXITCODE -ne 0) { throw "mingw32-make failed with exit code $LASTEXITCODE" }
    }
    finally {
        Pop-Location
    }

    $found = Find-PerfExe $Root
    if (-not $found) {
        throw "Built tests tree, but perf_tests.exe was still not found."
    }
    return $found
}

$repoRoot = Get-RepoRoot
Ensure-RuntimePath
if (-not $OutputRoot) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputRoot = Join-Path $repoRoot ".claude\profiling\$timestamp"
}
if (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot $OutputRoot
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$perfExePath = Ensure-PerfExe -Root $repoRoot -RequestedPerfExe $PerfExe
$baselinePath = Join-Path $repoRoot "tests\perf\baselines.json"

$results = @()
foreach ($threadEntry in $Threads) {
    foreach ($threadCount in ($threadEntry -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        $threadCount = [int]$threadCount
    $jsonPath = [System.IO.Path]::GetFullPath((Join-Path $OutputRoot ("perf-thread{0}.json" -f $threadCount)))
    $stageLogPath = [System.IO.Path]::GetFullPath((Join-Path $OutputRoot ("stage-thread{0}.log" -f $threadCount)))
    $args = @(
        "--iterations", $Iterations,
        "--threads", $threadCount,
        "--json-output", $jsonPath,
        "--baseline", $baselinePath,
        "--stage-log", $stageLogPath
    )
    if ($RequireBaseline) {
        $args += "--require-baseline"
    }
    if ($Cold8bit) {
        $args += "--cold-8bit"
    }
    if ($RawCacheMB -gt 0) {
        $args += "--raw-cache-mb"
        $args += "$RawCacheMB"
        $args += "--cache-cpu-cores"
        $args += "$CacheCpuCores"
    }

    Write-Host ("Running perf_tests for threads={0}" -f $threadCount)
    & $perfExePath @args
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 2) {
        throw "perf_tests failed for threads=$threadCount with exit code $LASTEXITCODE"
    }
    if ($LASTEXITCODE -eq 2) {
        Write-Warning ("perf_tests reported regression failures for threads={0}; keeping profiling artifacts for analysis." -f $threadCount)
    }

    $results += [pscustomobject]@{
        Threads = $threadCount
        Json = $jsonPath
        StageLog = $stageLogPath
    }
    }
}

$summaryPath = Join-Path $OutputRoot "run-summary.txt"
$summary = @()
$summary += "perf_exe=$perfExePath"
$summary += "iterations=$Iterations"
$summary += "baseline=$baselinePath"
$summary += "cold_8bit=$($Cold8bit.IsPresent)"
$summary += "raw_cache_mb=$RawCacheMB"
$summary += "cache_cpu_cores=$CacheCpuCores"
foreach ($result in $results) {
    $summary += ("threads={0} json={1} stage_log={2}" -f $result.Threads, $result.Json, $result.StageLog)
}
$summary | Set-Content -Path $summaryPath -Encoding UTF8

Write-Host "Runtime profiling artifacts written to:"
Write-Host $OutputRoot
