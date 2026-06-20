param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [Alias("Output")]
    [string]$DngOutputDir = "",
    [string]$Receipt = "",
    [ValidateSet("", "uncompressed", "lossless", "fast-pass")]
    [string]$CdngCodec = "lossless",
    [ValidateSet("trusted", "shadow")]
    [string]$GpuExportMode = "trusted",
    [string]$GpuExportBackend = "cuda",
    [string]$GpuExportDll = "",
    [int]$MaxFrames = 0,
    [string]$OutputRoot = "",
    [string[]]$AdditionalArgs = @(),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ($MaxFrames -lt 0) {
    throw "-MaxFrames must be >= 0."
}

function Resolve-RepoPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return (Join-Path $Root $Path)
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Value | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-FileArtifact {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{
            path = $Path
            exists = $false
            length = $null
            lastWriteTime = $null
            sha256 = $null
        }
    }

    $item = Get-Item -LiteralPath $Path
    [pscustomobject]@{
        path = $item.FullName
        exists = $true
        length = $item.Length
        lastWriteTime = $item.LastWriteTime
        sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
    }
}

function Get-NvidiaSmiSnapshot {
    $cmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $cmd) {
        return [pscustomobject]@{
            found = $false
            path = $null
            output = @()
            error = "nvidia-smi not found"
        }
    }

    try {
        $output = & $cmd.Source --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1
        return [pscustomobject]@{
            found = $true
            path = $cmd.Source
            output = @($output | ForEach-Object { [string]$_ })
            error = $null
        }
    }
    catch {
        return [pscustomobject]@{
            found = $true
            path = $cmd.Source
            output = @()
            error = $_.Exception.Message
        }
    }
}

function Get-DngOutputSummary {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or !(Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{
            path = $Path
            exists = $false
            count = 0
            totalBytes = 0
            firstFiles = @()
        }
    }

    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Filter "*.dng" -ErrorAction SilentlyContinue |
        Sort-Object FullName)
    $totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $totalBytes) {
        $totalBytes = 0
    }
    [pscustomobject]@{
        path = (Resolve-Path -LiteralPath $Path).Path
        exists = $true
        count = $files.Count
        totalBytes = [int64]$totalBytes
        firstFiles = @($files | Select-Object -First 12 | ForEach-Object {
            [pscustomobject]@{
                path = $_.FullName
                length = $_.Length
                lastWriteTime = $_.LastWriteTime
            }
        })
    }
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "platform\qt\build-release\release\MLVApp.exe"
}
else {
    $ExePath = Resolve-RepoPath -Root $root -Path $ExePath
}
$exe = (Resolve-Path -LiteralPath $ExePath).Path
$exeDir = Split-Path -Parent $exe

if ([string]::IsNullOrWhiteSpace($GpuExportDll)) {
    $GpuExportDll = Join-Path $exeDir "igpu_recon_cuda.dll"
}
else {
    $GpuExportDll = Resolve-RepoPath -Root $root -Path $GpuExportDll
}
$gpuDll = (Resolve-Path -LiteralPath $GpuExportDll).Path

$platformDir = Join-Path $exeDir "platforms"
$qwindows = Join-Path $platformDir "qwindows.dll"
if (-not (Test-Path -LiteralPath $qwindows -PathType Leaf)) {
    throw "Release CUDA DNG export requires $qwindows. Rebuild/deploy the release tree before exporting."
}

$clipFullPath = ""
if (-not [string]::IsNullOrWhiteSpace($ClipPath)) {
    $clipFullPath = (Resolve-Path -LiteralPath (Resolve-RepoPath -Root $root -Path $ClipPath)).Path
}

$dngDir = ""
if (-not [string]::IsNullOrWhiteSpace($DngOutputDir)) {
    $dngDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
        (Resolve-RepoPath -Root $root -Path $DngOutputDir))
}

$receiptPath = ""
if (-not [string]::IsNullOrWhiteSpace($Receipt)) {
    $receiptPath = (Resolve-Path -LiteralPath (Resolve-RepoPath -Root $root -Path $Receipt)).Path
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputRoot = Join-Path $root ".claude-state\profiling\$stamp-cuda-dng-export"
}
$runRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
    (Resolve-RepoPath -Root $root -Path $OutputRoot))
$summaryPath = Join-Path $runRoot "summary.json"
$latestPath = Join-Path $root ".claude-state\profiling\cuda-dng-export-latest.json"
$profilePath = Join-Path $runRoot "export-profile.json"
$stdoutPath = Join-Path $runRoot "stdout.txt"
$stderrPath = Join-Path $runRoot "stderr.txt"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$arguments = @("--batch")
if (-not [string]::IsNullOrWhiteSpace($clipFullPath)) {
    $arguments += @("--input", $clipFullPath)
}
if (-not [string]::IsNullOrWhiteSpace($dngDir)) {
    $arguments += @("--output", $dngDir)
}
if (-not [string]::IsNullOrWhiteSpace($receiptPath)) {
    $arguments += @("--receipt", $receiptPath)
}
if (-not [string]::IsNullOrWhiteSpace($CdngCodec)) {
    $arguments += @("--cdng-codec", $CdngCodec)
}
if ($MaxFrames -gt 0) {
    $arguments += @("--max-frames", [string]$MaxFrames)
}
if ($AdditionalArgs.Count -gt 0) {
    $arguments += $AdditionalArgs
}

$environment = [ordered]@{
    QT_QPA_PLATFORM = "windows"
    QT_QPA_PLATFORM_PLUGIN_PATH = $platformDir
    QT_PLUGIN_PATH = $exeDir
    MLVAPP_EXPORT_STAGE_PROFILER = "1"
    MLVAPP_EXPORT_STAGE_PROFILE_FILE = $profilePath
    MLVAPP_EXPORT_STAGE_PROFILE_BUILD_ID = (& git -C $root rev-parse --verify HEAD 2>$null)
    MLVAPP_GPU_EXPORT = "1"
    MLVAPP_GPU_EXPORT_DLL = $gpuDll
}
if (-not [string]::IsNullOrWhiteSpace($GpuExportBackend)) {
    $environment["MLVAPP_GPU_EXPORT_BACKEND"] = $GpuExportBackend
}
if ($GpuExportMode -eq "trusted") {
    $environment["MLVAPP_GPU_EXPORT_TRUSTED"] = "1"
}

$status = if ($DryRun) { "planned" } else { "starting" }
$summary = [ordered]@{
    schema = "mlvapp-cuda-dng-export.v1"
    status = $status
    createdAt = (Get-Date).ToString("o")
    host = $env:COMPUTERNAME
    repoRoot = $root
    exe = Get-FileArtifact -Path $exe
    backend = Get-FileArtifact -Path $gpuDll
    cudart = Get-FileArtifact -Path (Join-Path $exeDir "cudart64_12.dll")
    qwindows = Get-FileArtifact -Path $qwindows
    clipPath = $clipFullPath
    receiptPath = $receiptPath
    dngOutputDir = $dngDir
    cdngCodec = $CdngCodec
    maxFrames = $MaxFrames
    gpuExportMode = $GpuExportMode
    arguments = $arguments
    environment = $environment
    nvidiaSmi = Get-NvidiaSmiSnapshot
    profilePath = $profilePath
    stdoutPath = $stdoutPath
    stderrPath = $stderrPath
    exitCode = $null
    dngOutput = Get-DngOutputSummary -Path $dngDir
    proofBoundary = [pscustomobject]@{
        exportRunner = $true
        provesDellSupport = $false
        provesDngHashParity = $false
        proofCommand = "tools\profiling\run-local-cuda-playback-dng-smoke.ps1"
        notes = @(
            "This runner performs a real release --batch DNG export with explicit CUDA export environment.",
            "Trusted mode is an opt-in CUDA output path for the scoped Dual ISO shape; unsupported states still need telemetry/profile review.",
            "Use the local smoke wrapper for CPU-baseline-vs-candidate DNG hash proof on a specific host."
        )
    }
}

Write-JsonFile -Value ([pscustomobject]$summary) -Path $summaryPath
Copy-Item -LiteralPath $summaryPath -Destination $latestPath -Force

if ($DryRun) {
    Write-Output $summaryPath
    return
}

if ([string]::IsNullOrWhiteSpace($clipFullPath)) {
    throw "Missing -Input <clip-or-folder>."
}
if ([string]::IsNullOrWhiteSpace($dngDir)) {
    throw "Missing -Output <dng-output-directory>."
}
New-Item -ItemType Directory -Force -Path $dngDir | Out-Null

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $exe
$startInfo.WorkingDirectory = $root
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
foreach ($argument in $arguments) {
    [void]$startInfo.ArgumentList.Add($argument)
}
foreach ($key in $environment.Keys) {
    $startInfo.EnvironmentVariables[$key] = [string]$environment[$key]
}

$process = [System.Diagnostics.Process]::Start($startInfo)
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
$process.WaitForExit()
$stdout = $stdoutTask.GetAwaiter().GetResult()
$stderr = $stderrTask.GetAwaiter().GetResult()
$stdout | Set-Content -LiteralPath $stdoutPath -Encoding UTF8
$stderr | Set-Content -LiteralPath $stderrPath -Encoding UTF8

$summary["exitCode"] = $process.ExitCode
$summary["status"] = if ($process.ExitCode -eq 0) { "success" } else { "failed" }
$summary["dngOutput"] = Get-DngOutputSummary -Path $dngDir
$summary["profileExists"] = Test-Path -LiteralPath $profilePath -PathType Leaf
Write-JsonFile -Value ([pscustomobject]$summary) -Path $summaryPath
Copy-Item -LiteralPath $summaryPath -Destination $latestPath -Force

if ($process.ExitCode -eq 0 -and -not [bool]$summary["profileExists"]) {
    throw "CUDA DNG export exited 0 but did not write export profile: $profilePath"
}
exit $process.ExitCode
