param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [string]$DngOutputDir = "",
    [Alias("Output")]
    [string]$ProfileOutput = "",
    [string]$Receipt = "",
    [string]$Log = "",
    [string]$BuildId = "",
    [switch]$SkipErrors,
    [switch]$Resume,
    [switch]$Verbose,
    [switch]$EnableGpuExport,
    [switch]$UsePayloadHandoff,
    [switch]$UseAsyncWriter,
    [int]$AsyncWriterQueueDepth = 0,
    [string]$GpuExportDll = "",
    [string[]]$AdditionalArgs = @(),
    [string[]]$ExtraEnvironment = @(),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "platform\qt\build-release\release\MLVApp.exe"
}

$exe = (Resolve-Path -LiteralPath $ExePath).Path
$exeDir = Split-Path -Parent $exe
$platformDir = Join-Path $exeDir "platforms"
$qwindows = Join-Path $platformDir "qwindows.dll"
if (-not (Test-Path -LiteralPath $qwindows)) {
    throw "Release CDNG export profiling requires $qwindows. Rebuild/deploy the release tree before profiling."
}

$previousPlatform = $env:QT_QPA_PLATFORM
$previousPlatformPluginPath = $env:QT_QPA_PLATFORM_PLUGIN_PATH
$previousPluginPath = $env:QT_PLUGIN_PATH

function Add-EnvironmentPairs {
    param(
        [object]$Target,
        [string[]]$Pairs
    )

    $expandedPairs = @()
    foreach ($rawPair in $Pairs) {
        if ([string]::IsNullOrWhiteSpace($rawPair)) {
            continue
        }

        $parts = @($rawPair -split ',')
        if ($parts.Count -gt 1 -and ($parts | Where-Object { $_.IndexOf("=") -lt 1 }).Count -eq 0) {
            $expandedPairs += $parts
        }
        else {
            $expandedPairs += $rawPair
        }
    }

    foreach ($pair in $expandedPairs) {
        if ([string]::IsNullOrWhiteSpace($pair)) {
            continue
        }

        $separatorIndex = $pair.IndexOf("=")
        if ($separatorIndex -lt 1) {
            throw "Invalid -ExtraEnvironment entry '$pair'. Use KEY=VALUE."
        }

        $key = $pair.Substring(0, $separatorIndex).Trim()
        $value = $pair.Substring($separatorIndex + 1)
        if ([string]::IsNullOrWhiteSpace($key)) {
            throw "Invalid -ExtraEnvironment entry '$pair'. The key cannot be empty."
        }

        $Target[$key] = $value
    }
}

try {
    # The user-facing release tree deploys qwindows.dll. Keep the batch run on
    # the same platform-plugin path as normal GUI launches to avoid offscreen
    # plugin popups masking export failures.
    $env:QT_QPA_PLATFORM = "windows"
    $env:QT_QPA_PLATFORM_PLUGIN_PATH = $platformDir
    $env:QT_PLUGIN_PATH = $exeDir

    $profilePath = ""
    $dngDir = ""
    $inputPath = ""
    $receiptPath = ""
    $logPath = ""

    if (-not [string]::IsNullOrWhiteSpace($ProfileOutput)) {
        $profilePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ProfileOutput)
    }
    if (-not [string]::IsNullOrWhiteSpace($DngOutputDir)) {
        $dngDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DngOutputDir)
    }
    if (-not [string]::IsNullOrWhiteSpace($ClipPath)) {
        $inputPath = (Resolve-Path -LiteralPath $ClipPath).Path
    }
    if (-not [string]::IsNullOrWhiteSpace($Receipt)) {
        $receiptPath = (Resolve-Path -LiteralPath $Receipt).Path
    }
    if (-not [string]::IsNullOrWhiteSpace($Log)) {
        $logPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Log)
    }

    $effectiveBuildId = $BuildId
    if ([string]::IsNullOrWhiteSpace($effectiveBuildId)) {
        $effectiveBuildId = (& git -C $root rev-parse --verify HEAD 2>$null)
        if ([string]::IsNullOrWhiteSpace($effectiveBuildId)) {
            $effectiveBuildId = (Get-Item -LiteralPath $exe).LastWriteTimeUtc.ToString("o")
        }
    }

    $arguments = @("--batch")
    if (-not [string]::IsNullOrWhiteSpace($inputPath)) {
        $arguments += @("--input", $inputPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($dngDir)) {
        $arguments += @("--output", $dngDir)
    }
    if (-not [string]::IsNullOrWhiteSpace($receiptPath)) {
        $arguments += @("--receipt", $receiptPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($logPath)) {
        $arguments += @("--log", $logPath)
    }
    if ($SkipErrors) {
        $arguments += "--skip-errors"
    }
    if ($Resume) {
        $arguments += "--resume"
    }
    if ($Verbose) {
        $arguments += "--verbose"
    }
    if ($AdditionalArgs.Count -gt 0) {
        $arguments += $AdditionalArgs
    }

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $exe
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    foreach ($argument in $arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $envBlock = $startInfo.EnvironmentVariables
    $envBlock["QT_QPA_PLATFORM"] = $env:QT_QPA_PLATFORM
    $envBlock["QT_QPA_PLATFORM_PLUGIN_PATH"] = $env:QT_QPA_PLATFORM_PLUGIN_PATH
    $envBlock["QT_PLUGIN_PATH"] = $env:QT_PLUGIN_PATH
    if (-not [string]::IsNullOrWhiteSpace($profilePath)) {
        $envBlock["MLVAPP_EXPORT_STAGE_PROFILER"] = "1"
        $envBlock["MLVAPP_EXPORT_STAGE_PROFILE_FILE"] = $profilePath
        $envBlock["MLVAPP_EXPORT_STAGE_PROFILE_BUILD_ID"] = $effectiveBuildId
    }
    if ($EnableGpuExport) {
        $envBlock["MLVAPP_GPU_EXPORT"] = "1"
        if (-not [string]::IsNullOrWhiteSpace($GpuExportDll)) {
            $envBlock["MLVAPP_GPU_EXPORT_DLL"] = (Resolve-Path -LiteralPath $GpuExportDll).Path
        }
    }
    else {
        [void]$envBlock.Remove("MLVAPP_GPU_EXPORT")
        [void]$envBlock.Remove("MLVAPP_GPU_EXPORT_DLL")
    }
    if ($UsePayloadHandoff) {
        $envBlock["MLVAPP_CDNG_EXPORT_PAYLOAD_HANDOFF"] = "1"
    }
    else {
        [void]$envBlock.Remove("MLVAPP_CDNG_EXPORT_PAYLOAD_HANDOFF")
    }
    if ($UseAsyncWriter) {
        $envBlock["MLVAPP_CDNG_EXPORT_ASYNC_WRITER"] = "1"
        if ($AsyncWriterQueueDepth -gt 0) {
            $envBlock["MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH"] = [string]$AsyncWriterQueueDepth
        }
        else {
            [void]$envBlock.Remove("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH")
        }
    }
    else {
        [void]$envBlock.Remove("MLVAPP_CDNG_EXPORT_ASYNC_WRITER")
        [void]$envBlock.Remove("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH")
    }
    Add-EnvironmentPairs -Target $envBlock -Pairs $ExtraEnvironment

    if ($DryRun) {
        [pscustomobject]@{
            exePath = $exe
            arguments = $arguments
            profileOutput = $profilePath
            dngOutputDir = $dngDir
            buildId = $effectiveBuildId
            qwindowsExists = $true
            environment = [pscustomobject]@{
                QT_QPA_PLATFORM = $envBlock["QT_QPA_PLATFORM"]
                QT_QPA_PLATFORM_PLUGIN_PATH = $envBlock["QT_QPA_PLATFORM_PLUGIN_PATH"]
                QT_PLUGIN_PATH = $envBlock["QT_PLUGIN_PATH"]
                MLVAPP_EXPORT_STAGE_PROFILER = $envBlock["MLVAPP_EXPORT_STAGE_PROFILER"]
                MLVAPP_EXPORT_STAGE_PROFILE_FILE = $envBlock["MLVAPP_EXPORT_STAGE_PROFILE_FILE"]
                MLVAPP_EXPORT_STAGE_PROFILE_BUILD_ID = $envBlock["MLVAPP_EXPORT_STAGE_PROFILE_BUILD_ID"]
                MLVAPP_GPU_EXPORT = $envBlock["MLVAPP_GPU_EXPORT"]
                MLVAPP_GPU_EXPORT_DLL = $envBlock["MLVAPP_GPU_EXPORT_DLL"]
                MLVAPP_CDNG_EXPORT_PAYLOAD_HANDOFF = $envBlock["MLVAPP_CDNG_EXPORT_PAYLOAD_HANDOFF"]
                MLVAPP_CDNG_EXPORT_ASYNC_WRITER = $envBlock["MLVAPP_CDNG_EXPORT_ASYNC_WRITER"]
                MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH = $envBlock["MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH"]
            }
        } | ConvertTo-Json -Depth 5
        return
    }

    if ([string]::IsNullOrWhiteSpace($inputPath)) {
        throw "Missing -Input <clip-or-folder>."
    }
    if ([string]::IsNullOrWhiteSpace($dngDir)) {
        throw "Missing -DngOutputDir <directory>."
    }
    if ([string]::IsNullOrWhiteSpace($profilePath)) {
        throw "Missing -Output <profile.json>."
    }

    New-Item -ItemType Directory -Force -Path $dngDir | Out-Null
    $profileDir = Split-Path -Parent $profilePath
    if (-not [string]::IsNullOrWhiteSpace($profileDir)) {
        New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    }
    if (-not [string]::IsNullOrWhiteSpace($logPath)) {
        $logDir = Split-Path -Parent $logPath
        if (-not [string]::IsNullOrWhiteSpace($logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        }
    }

    $process = [System.Diagnostics.Process]::Start($startInfo)
    $process.WaitForExit()
    if ($process.ExitCode -eq 0 -and -not (Test-Path -LiteralPath $profilePath)) {
        throw "Batch export exited 0 but did not write export stage profile: $profilePath"
    }
    exit $process.ExitCode
}
finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
    $env:QT_QPA_PLATFORM_PLUGIN_PATH = $previousPlatformPluginPath
    $env:QT_PLUGIN_PATH = $previousPluginPath
}
