param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [string]$Output = "",
    [int]$Frames = 3,
    [int]$StartFrame = 0,
    [int]$FrameStep = 1,
    # Default to auto-thread playback so the profile wrapper reflects real
    # release behavior. Pass 1 explicitly only when you need a controlled
    # single-thread diagnostic run.
    [string]$Threads = "auto",
    [string]$Receipt = "",
    [string]$QualityMode = "",
    [string]$ScaleFactor = "",
    [string]$PreviewMode = "",
    [switch]$ShowWindow,
    [switch]$WaitForPaint,
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
    throw "Release playback profiling requires $qwindows. Rebuild/deploy the release tree before profiling."
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
    # The user-facing Windows release deploys qwindows.dll, not qoffscreen.dll.
    # Forcing offscreen against this tree causes the Qt platform-plugin popup.
    $env:QT_QPA_PLATFORM = "windows"
    $env:QT_QPA_PLATFORM_PLUGIN_PATH = $platformDir
    $env:QT_PLUGIN_PATH = $exeDir

    if ($DryRun) {
        [pscustomobject]@{
            exePath = $exe
            qtQpaPlatform = $env:QT_QPA_PLATFORM
            qtQpaPlatformPluginPath = $env:QT_QPA_PLATFORM_PLUGIN_PATH
            qtPluginPath = $env:QT_PLUGIN_PATH
            qwindowsExists = $true
            previousQtQpaPlatform = $previousPlatform
        } | ConvertTo-Json -Depth 3
        return
    }

    if ([string]::IsNullOrWhiteSpace($ClipPath)) {
        throw "Missing -Input <clip.mlv>."
    }
    if ([string]::IsNullOrWhiteSpace($Output)) {
        throw "Missing -Output <profile.json>."
    }

    $inputPath = (Resolve-Path -LiteralPath $ClipPath).Path
    $outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
    $outputDir = Split-Path -Parent $outputPath
    if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    $arguments = @(
        "--profile-playback",
        "--input", $inputPath,
        "--frames", [string]$Frames,
        "--start-frame", [string]$StartFrame,
        "--frame-step", [string]$FrameStep,
        "--output", $outputPath,
        "--threads", $Threads
    )
    if (-not [string]::IsNullOrWhiteSpace($Receipt)) {
        $arguments += @("--receipt", (Resolve-Path -LiteralPath $Receipt).Path)
    }
    if ($ShowWindow) {
        $arguments += "--show-window"
    }
    if ($WaitForPaint) {
        $arguments += "--wait-for-paint"
    }
    if ($AdditionalArgs.Count -gt 0) {
        $arguments += $AdditionalArgs
    }

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $exe
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = -not ($ShowWindow -or $WaitForPaint)
    foreach ($argument in $arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $envBlock = $startInfo.EnvironmentVariables
    $envBlock["MLVAPP_PLAYBACK_MAX_THREADS"] = $Threads
    if (-not [string]::IsNullOrWhiteSpace($QualityMode)) {
        $envBlock["MLVAPP_PLAYBACK_QUALITY_MODE"] = $QualityMode
    }
    elseif ($envBlock.ContainsKey("MLVAPP_PLAYBACK_QUALITY_MODE")) {
        [void]$envBlock.Remove("MLVAPP_PLAYBACK_QUALITY_MODE")
    }
    if (-not [string]::IsNullOrWhiteSpace($ScaleFactor)) {
        $envBlock["MLVAPP_PLAYBACK_SCALE_FACTOR"] = $ScaleFactor
    }
    elseif ($envBlock.ContainsKey("MLVAPP_PLAYBACK_SCALE_FACTOR")) {
        [void]$envBlock.Remove("MLVAPP_PLAYBACK_SCALE_FACTOR")
    }
    if (-not [string]::IsNullOrWhiteSpace($PreviewMode)) {
        $envBlock["MLVAPP_PLAYBACK_PREVIEW_MODE"] = $PreviewMode
        [void]$envBlock.Remove("MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW")
    }
    elseif ($envBlock.ContainsKey("MLVAPP_PLAYBACK_PREVIEW_MODE")) {
        [void]$envBlock.Remove("MLVAPP_PLAYBACK_PREVIEW_MODE")
        [void]$envBlock.Remove("MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW")
    }
    elseif ($envBlock.ContainsKey("MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW")) {
        [void]$envBlock.Remove("MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW")
    }
    Add-EnvironmentPairs -Target $envBlock -Pairs $ExtraEnvironment

    $process = [System.Diagnostics.Process]::Start($startInfo)
    $process.WaitForExit()
    exit $process.ExitCode
}
finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
    $env:QT_QPA_PLATFORM_PLUGIN_PATH = $previousPlatformPluginPath
    $env:QT_PLUGIN_PATH = $previousPluginPath
}
