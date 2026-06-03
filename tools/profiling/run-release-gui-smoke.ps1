param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [string]$Output = "",
    [int]$Seconds = 8,
    [int]$SettleMs = 2500,
    [double]$SettleCpuPercent = 10,
    [int]$SettleCpuStableMs = 1000,
    [int]$SettleCpuMaxMs = 45000,
    [double]$SystemSettleCpuPercent = -1,
    [int]$SystemSettleCpuStableMs = 2000,
    [int]$SystemSettleCpuMaxMs = 60000,
    [string]$Threads = "auto",
    [string]$ScaleFactor = "",
    [int]$ExpectedScaleRequest = 1,
    [int]$ExpectedQualityMode = 1,
    [switch]$PreferHqMean23,
    [switch]$FrameTelemetry,
    [switch]$RbfDetailTiming,
    [switch]$CaptureScreenshot,
    [switch]$SkipWindowScreenshot,
    [string]$ScreenshotOutputDir = "",
    [int]$ScreenshotDelayMs = 2000,
    [int]$ScreenshotWindowWaitMs = 10000,
    [int]$ScreenshotCaptureTimeoutMs = 2500,
    [switch]$PreserveExperimentalEnvironment,
    [string[]]$ExtraEnvironment = @(),
    [string]$Receipt = "",
    [string]$Scope = "",
    [ValidateSet("", "on", "off")]
    [string]$Zebras = "",
    [bool]$RequireLookAssist = $true,
    [bool]$RequireCpuSettled = $true,
    [string[]]$AdditionalArgs = @(),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Convert-PlaybackLogLineToObject {
    param([string]$Line)

    $result = [ordered]@{}
    $matches = [regex]::Matches($Line, '(?<key>[A-Za-z0-9_]+)=(?<value>"[^"]*"|\S+)')
    foreach ($match in $matches) {
        $key = $match.Groups["key"].Value
        $rawValue = $match.Groups["value"].Value.Trim('"')

        $intValue = 0L
        $doubleValue = 0.0
        if ([long]::TryParse($rawValue, [ref]$intValue)) {
            $result[$key] = $intValue
        }
        elseif ([double]::TryParse(
            $rawValue,
            [System.Globalization.NumberStyles]::Float,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$doubleValue)) {
            $result[$key] = $doubleValue
        }
        else {
            $result[$key] = $rawValue
        }
    }
    [pscustomobject]$result
}

function Get-LogTimestampUtc {
    param([string]$Line)

    if ($Line -notmatch '^\[(?<ts>[^\]]+)\]') {
        return $null
    }

    [datetime]::Parse(
        $Matches["ts"],
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
            [System.Globalization.DateTimeStyles]::AdjustToUniversal)
}

function Get-SystemCpuPercent {
    $sample = Get-CimInstance `
        -ClassName Win32_PerfFormattedData_PerfOS_Processor `
        -Filter "Name='_Total'" `
        -ErrorAction Stop
    [double]$sample.PercentProcessorTime
}

function Wait-SystemCpuSettle {
    param(
        [double]$ThresholdPercent,
        [int]$StableMs,
        [int]$MaxMs
    )

    $requested = $ThresholdPercent -ge 0 -and $StableMs -gt 0 -and $MaxMs -gt 0
    $result = [ordered]@{
        requested = $requested
        settled = -not $requested
        thresholdPercent = $ThresholdPercent
        stableMs = 0
        requiredStableMs = $StableMs
        elapsedMs = 0
        maxMs = $MaxMs
        lastPercent = $null
        failure = $null
    }
    if (-not $requested) {
        return [pscustomobject]$result
    }

    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $lastSampleMs = 0
    while ($watch.ElapsedMilliseconds -lt $MaxMs -and $result.stableMs -lt $StableMs) {
        Start-Sleep -Milliseconds 500
        try {
            $cpuPercent = Get-SystemCpuPercent
        }
        catch {
            $result.failure = $_.Exception.Message
            break
        }

        $nowMs = [int]$watch.ElapsedMilliseconds
        $deltaMs = [Math]::Max(1, $nowMs - $lastSampleMs)
        $lastSampleMs = $nowMs
        $result.lastPercent = $cpuPercent

        if ($cpuPercent -le $ThresholdPercent) {
            $result.stableMs += $deltaMs
        }
        else {
            $result.stableMs = 0
        }
    }

    $result.elapsedMs = [int]$watch.ElapsedMilliseconds
    $result.settled = $result.stableMs -ge $StableMs
    [pscustomobject]$result
}

function Get-ObjectPropertyValue {
    param(
        [object]$Object,
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($property) {
        return $property.Value
    }

    $null
}

function Convert-ToNullableDouble {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }

    $parsed = 0.0
    if ([double]::TryParse(
        [string]$Value,
        [System.Globalization.NumberStyles]::Float,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed)) {
        return $parsed
    }

    $null
}

function Convert-FpsStatusTextToValue {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }

    $match = [regex]::Match(
        [string]$Value,
        '([-+]?[0-9]+(?:\.[0-9]+)?)\s*fps',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $match.Success) {
        return $null
    }

    Convert-ToNullableDouble $match.Groups[1].Value
}

function Get-ScreenshotImageMetadata {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    Add-Type -AssemblyName System.Drawing
    $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
    $item = Get-Item -LiteralPath $resolvedPath
    $image = [System.Drawing.Image]::FromFile($resolvedPath)
    try {
        $aspect = $null
        if ($image.Height -gt 0) {
            $aspect = [Math]::Round($image.Width / $image.Height, 6)
        }

        [pscustomobject]@{
            width = $image.Width
            height = $image.Height
            aspect = $aspect
            pixelFormat = $image.PixelFormat.ToString()
            length = $item.Length
            sha256 = (Get-FileHash -LiteralPath $resolvedPath -Algorithm SHA256).Hash
        }
    }
    finally {
        $image.Dispose()
    }
}

function New-FpsStatusProofCrop {
    param(
        [string]$SourcePath,
        [string]$OutputPath,
        [int]$CropWidth = 720,
        [int]$CropHeight = 72,
        [int]$Scale = 3
    )

    if ([string]::IsNullOrWhiteSpace($SourcePath) -or -not (Test-Path -LiteralPath $SourcePath)) {
        return $null
    }

    Add-Type -AssemblyName System.Drawing
    $resolvedSourcePath = (Resolve-Path -LiteralPath $SourcePath).Path
    $resolvedOutputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
    $outputDir = Split-Path -Parent $resolvedOutputPath
    if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    $source = [System.Drawing.Image]::FromFile($resolvedSourcePath)
    try {
        $actualCropWidth = [Math]::Min($CropWidth, $source.Width)
        $actualCropHeight = [Math]::Min($CropHeight, $source.Height)
        $crop = [System.Drawing.Rectangle]::new(
            0,
            [Math]::Max(0, $source.Height - $actualCropHeight),
            $actualCropWidth,
            $actualCropHeight)
        $scaledWidth = [Math]::Max(1, $actualCropWidth * $Scale)
        $scaledHeight = [Math]::Max(1, $actualCropHeight * $Scale)
        $target = [System.Drawing.Bitmap]::new($scaledWidth, $scaledHeight)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($target)
            try {
                $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
                $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
                $graphics.DrawImage(
                    $source,
                    [System.Drawing.Rectangle]::new(0, 0, $scaledWidth, $scaledHeight),
                    $crop,
                    [System.Drawing.GraphicsUnit]::Pixel)
            }
            finally {
                $graphics.Dispose()
            }

            $target.Save($resolvedOutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally {
            $target.Dispose()
        }

        $item = Get-Item -LiteralPath $resolvedOutputPath
        [pscustomobject]@{
            outputPath = $item.FullName
            takenAtUtc = $item.LastWriteTimeUtc.ToString("o")
            method = "bottom-left-status-crop-from-window-grab"
            sourcePath = $resolvedSourcePath
            crop = [pscustomobject]@{
                x = $crop.X
                y = $crop.Y
                width = $crop.Width
                height = $crop.Height
                scale = $Scale
            }
            length = $item.Length
            image = Get-ScreenshotImageMetadata -Path $resolvedOutputPath
        }
    }
    finally {
        $source.Dispose()
    }
}

function New-ScreenshotAspectEvidence {
    param(
        [object]$ImageMetadata,
        [object]$VisualState
    )

    $stretchX = Convert-ToNullableDouble (Get-ObjectPropertyValue $VisualState "stretch_x")
    $stretchY = Convert-ToNullableDouble (Get-ObjectPropertyValue $VisualState "stretch_y")
    $hStretchIndex = Get-ObjectPropertyValue $VisualState "h_stretch_index"
    $vStretchIndex = Get-ObjectPropertyValue $VisualState "v_stretch_index"

    $hasStretchTelemetry = $null -ne $stretchX -and $null -ne $stretchY
    $activeStretch = $false
    if ($hasStretchTelemetry) {
        $activeStretch =
            [Math]::Abs($stretchX - 1.0) -gt 0.0001 -or
            [Math]::Abs($stretchY - 1.0) -gt 0.0001
    }

    $mode = "not-captured"
    $interpretation = "No screenshot was captured."
    if ($null -ne $ImageMetadata) {
        if (-not $hasStretchTelemetry) {
            $mode = "presented-frame-stretch-unknown"
            $interpretation = "Screenshot dimensions are from the presented frame, but stretch telemetry was unavailable."
        }
        elseif ($activeStretch) {
            $mode = "presented-playback-stretch"
            $interpretation = "Screenshot dimensions include the active playback stretch/de-squeeze state."
        }
        else {
            $mode = "neutral-presented-frame"
            $interpretation = "Screenshot dimensions are the presented frame with neutral stretch."
        }
    }

    [pscustomobject]@{
        mode = $mode
        interpretation = $interpretation
        width = Get-ObjectPropertyValue $ImageMetadata "width"
        height = Get-ObjectPropertyValue $ImageMetadata "height"
        aspect = Get-ObjectPropertyValue $ImageMetadata "aspect"
        stretchX = $stretchX
        stretchY = $stretchY
        hStretchIndex = $hStretchIndex
        vStretchIndex = $vStretchIndex
        activeStretch = $activeStretch
        hasStretchTelemetry = $hasStretchTelemetry
    }
}

function Add-EnvironmentPairs {
    param(
        [object]$Target,
        [string[]]$Pairs
    )

    foreach ($pair in $Pairs) {
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

function Wait-ForLogLineMatch {
    param(
        [string]$LogRoot,
        [string]$Pattern,
        [datetime]$NotBeforeUtc = [datetime]::MinValue,
        [int]$TimeoutMs = 15000,
        [int]$PollMs = 250
    )

    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($watch.ElapsedMilliseconds -lt $TimeoutMs) {
        try {
            $latestLog = Get-ChildItem -LiteralPath $LogRoot -Filter "mlvapp-*.log" |
                Sort-Object LastWriteTimeUtc -Descending |
                Select-Object -First 1
            if ($latestLog) {
                $match = Select-String -LiteralPath $latestLog.FullName -Pattern $Pattern -SimpleMatch |
                    Select-Object -Last 1
                if ($match) {
                    $timestampUtc = Get-LogTimestampUtc -Line $match.Line
                    if ($timestampUtc -and $timestampUtc -lt $NotBeforeUtc) {
                        Start-Sleep -Milliseconds $PollMs
                        continue
                    }
                    return [pscustomobject]@{
                        path = $latestLog.FullName
                        line = $match.Line
                        timestampUtc = $timestampUtc
                    }
                }
            }
        }
        catch {
        }

        Start-Sleep -Milliseconds $PollMs
    }

    return $null
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "platform\qt\build-release\release\MLVApp.exe"
}

$exe = (Resolve-Path -LiteralPath $ExePath).Path
if ([string]::IsNullOrWhiteSpace($ClipPath)) {
    throw "Missing -Input <clip.mlv>."
}
$inputPath = (Resolve-Path -LiteralPath $ClipPath).Path
$logRoot = Join-Path $env:APPDATA "magiclantern\MLVApp\logs"

if ([string]::IsNullOrWhiteSpace($Output)) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $clipBase = [IO.Path]::GetFileNameWithoutExtension($inputPath)
    $Output = Join-Path $root ".claude-state\profiling\$stamp-gui-smoke\$clipBase.json"
}
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$outputDir = Split-Path -Parent $outputPath
if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

$screenshotPath = $null
$windowScreenshotPath = $null
$fpsStatusCropPath = $null
if ($CaptureScreenshot) {
    if ([string]::IsNullOrWhiteSpace($ScreenshotOutputDir)) {
        $ScreenshotOutputDir = Join-Path $outputDir "screenshots"
    }
    $screenshotDirPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ScreenshotOutputDir)
    New-Item -ItemType Directory -Force -Path $screenshotDirPath | Out-Null
    $clipBase = [IO.Path]::GetFileNameWithoutExtension($inputPath)
    $screenshotPath = Join-Path $screenshotDirPath ("{0}.png" -f $clipBase)
    if (-not $SkipWindowScreenshot) {
        $windowScreenshotPath = Join-Path $screenshotDirPath ("{0}-window.png" -f $clipBase)
        $fpsStatusCropPath = Join-Path $screenshotDirPath ("{0}-fps-status.png" -f $clipBase)
    }
}

$arguments = @(
    "--gui-smoke-playback",
    "--input", $inputPath,
    "--seconds", [string]$Seconds,
    "--settle-ms", [string]$SettleMs,
    "--settle-cpu-percent", ([string]::Format(
        [System.Globalization.CultureInfo]::InvariantCulture,
        "{0}",
        $SettleCpuPercent)),
    "--settle-cpu-stable-ms", [string]$SettleCpuStableMs,
    "--settle-cpu-max-ms", [string]$SettleCpuMaxMs
)
if (-not [string]::IsNullOrWhiteSpace($Receipt)) {
    $arguments += @("--receipt", (Resolve-Path -LiteralPath $Receipt).Path)
}
if (-not [string]::IsNullOrWhiteSpace($Scope)) {
    $arguments += @("--scope", $Scope)
}
if ($CaptureScreenshot) {
    $arguments += @("--screenshot-output", $screenshotPath)
    if (-not [string]::IsNullOrWhiteSpace($windowScreenshotPath)) {
        $arguments += @("--window-screenshot-output", $windowScreenshotPath)
    }
}
if ($Zebras -eq "on") {
    $arguments += "--zebras"
}
elseif ($Zebras -eq "off") {
    $arguments += "--no-zebras"
}
if ($AdditionalArgs.Count -gt 0) {
    $arguments += $AdditionalArgs
}

$useAutoPlaybackThreads = [string]::IsNullOrWhiteSpace($Threads) -or
    $Threads.Trim().Equals("auto", [System.StringComparison]::OrdinalIgnoreCase) -or
    $Threads.Trim().Equals("0", [System.StringComparison]::OrdinalIgnoreCase)

$launchEnv = [ordered]@{}
if (-not $useAutoPlaybackThreads) {
    $launchEnv["MLVAPP_PLAYBACK_MAX_THREADS"] = $Threads
}
if ($FrameTelemetry) {
    $launchEnv["MLVAPP_PLAYBACK_SMOKE_TELEMETRY"] = "1"
}
if (-not [string]::IsNullOrWhiteSpace($ScaleFactor)) {
    $launchEnv["MLVAPP_PLAYBACK_SCALE_FACTOR"] = $ScaleFactor
}
if ($PreferHqMean23) {
    $launchEnv["MLVAPP_PLAYBACK_PREFER_HQ_MEAN23"] = "1"
}
if ($RbfDetailTiming) {
    $launchEnv["MLVAPP_PLAYBACK_RBF_DETAIL_TIMING"] = "1"
}
Add-EnvironmentPairs -Target $launchEnv -Pairs $ExtraEnvironment
if (-not [string]::IsNullOrWhiteSpace($env:OMP_NUM_THREADS)) {
    $launchEnv["OMP_NUM_THREADS"] = $env:OMP_NUM_THREADS
}

$experimentalEnvironment = @(
    "MLVAPP_ENABLE_SH_CURVE_INDEX_MASK",
    "MLVAPP_DISABLE_SH_CURVE_INDEX_MASK",
    "MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH",
    "MLVAPP_ENABLE_AVX2_INTRIN_DIRECT8",
    "MLVAPP_DISABLE_AVX2_INTRIN_DIRECT8",
    "MLVAPP_ENABLE_DUAL_ISO_FAST_X4_IN_HQ",
    "MLVAPP_PLAYBACK_RBF_DETAIL_TIMING",
    "MLVAPP_PROCESSING_CORE_COLOR_MAIN_PRELUDE_PROBE",
    "MLVAPP_PROCESSING_CORE_COLOR_MAIN_PRELUDE_WB_PROBE",
    "MLVAPP_PROCESSING_CORE_COLOR_MAIN_PRELUDE_CREATIVE_PROBE",
    "MLVAPP_PROCESSING_CORE_COLOR_CAM_WB_PROBE",
    "MLVAPP_PROCESSING_CORE_COLOR_GAMMA_PROBE",
    "MLVAPP_PROCESSING_CORE_CREATIVE_PROBE",
    "MLVAPP_PLAYBACK_SCOPE_INTERVAL_MS",
    "MLVAPP_DISABLE_RAW_UINT16_PREFETCH",
    "MLVAPP_DISABLE_PLAY_START_PREROLL"
)
$experimentalEnvironmentToClear = @(
    $experimentalEnvironment | Where-Object { -not $launchEnv.Contains($_) }
)
$clearedEnvironment = @()
if (-not $FrameTelemetry) {
    $clearedEnvironment += "MLVAPP_PLAYBACK_SMOKE_TELEMETRY"
}
if (-not $RbfDetailTiming) {
    $clearedEnvironment += "MLVAPP_PLAYBACK_RBF_DETAIL_TIMING"
}
$clearedEnvironment += @(
    "MLVAPP_PLAYBACK_SCALE_FACTOR",
    "MLVAPP_PLAYBACK_PREFER_HQ_MEAN23"
)
if (-not $PreserveExperimentalEnvironment) {
    $clearedEnvironment += $experimentalEnvironmentToClear
}
$clearedEnvironment = @($clearedEnvironment | Select-Object -Unique)

if ($DryRun) {
    [pscustomobject]@{
        exePath = $exe
        workingDirectory = $root
        arguments = $arguments
        environment = $launchEnv
        clearsEnvironment = $clearedEnvironment
        preserveExperimentalEnvironment = [bool]$PreserveExperimentalEnvironment
        preLaunchSystemCpuSettle = [pscustomobject]@{
            requested = $SystemSettleCpuPercent -ge 0
            thresholdPercent = $SystemSettleCpuPercent
            stableMs = $SystemSettleCpuStableMs
            maxMs = $SystemSettleCpuMaxMs
        }
        validationPolicy = [pscustomobject]@{
            requireLookAssist = $RequireLookAssist
            requireCpuSettled = $RequireCpuSettled
            expectedScaleRequest = $ExpectedScaleRequest
            expectedQualityMode = $ExpectedQualityMode
        }
        output = $outputPath
    } | ConvertTo-Json -Depth 5
    return
}

$smokeMutex = [System.Threading.Mutex]::new($false, "Global\MLVAppGuiSmokeLogParser")
$smokeMutexAcquired = $smokeMutex.WaitOne([TimeSpan]::FromMinutes(10))
if (-not $smokeMutexAcquired) {
    throw "Timed out waiting for another GUI smoke run to release the shared MLVApp log parser."
}

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $exe
$startInfo.WorkingDirectory = $root
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
foreach ($argument in $arguments) {
    [void]$startInfo.ArgumentList.Add($argument)
}

$envBlock = $startInfo.EnvironmentVariables
if (-not $useAutoPlaybackThreads) {
    $envBlock["MLVAPP_PLAYBACK_MAX_THREADS"] = $Threads
}
if ($FrameTelemetry) {
    $envBlock["MLVAPP_PLAYBACK_SMOKE_TELEMETRY"] = "1"
}
else {
    [void]$envBlock.Remove("MLVAPP_PLAYBACK_SMOKE_TELEMETRY")
}
if (-not [string]::IsNullOrWhiteSpace($ScaleFactor)) {
    $envBlock["MLVAPP_PLAYBACK_SCALE_FACTOR"] = $ScaleFactor
}
else {
    [void]$envBlock.Remove("MLVAPP_PLAYBACK_SCALE_FACTOR")
}
if ($PreferHqMean23) {
    $envBlock["MLVAPP_PLAYBACK_PREFER_HQ_MEAN23"] = "1"
}
else {
    [void]$envBlock.Remove("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23")
}
if ($RbfDetailTiming) {
    $envBlock["MLVAPP_PLAYBACK_RBF_DETAIL_TIMING"] = "1"
}
else {
    [void]$envBlock.Remove("MLVAPP_PLAYBACK_RBF_DETAIL_TIMING")
}
Add-EnvironmentPairs -Target $envBlock -Pairs $ExtraEnvironment
if (-not $PreserveExperimentalEnvironment) {
    foreach ($name in $experimentalEnvironmentToClear) {
        [void]$envBlock.Remove($name)
    }
}

$preLaunchSystemCpuSettle = Wait-SystemCpuSettle `
    -ThresholdPercent $SystemSettleCpuPercent `
    -StableMs $SystemSettleCpuStableMs `
    -MaxMs $SystemSettleCpuMaxMs

$startUtc = [datetime]::UtcNow
$process = [System.Diagnostics.Process]::Start($startInfo)
$screenshotCapture = $null
$windowScreenshotCapture = $null
$fpsStatusCropCapture = $null
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
$process.WaitForExit()
$stdout = $stdoutTask.GetAwaiter().GetResult()
$stderr = $stderrTask.GetAwaiter().GetResult()
$endUtc = [datetime]::UtcNow

if ($CaptureScreenshot) {
    if (-not (Test-Path -LiteralPath $screenshotPath)) {
        throw "GUI smoke completed but did not write screenshot: $screenshotPath"
    }
    $screenshotItem = Get-Item -LiteralPath $screenshotPath
    $screenshotImage = Get-ScreenshotImageMetadata -Path $screenshotPath
    $screenshotCapture = [pscustomobject]@{
        outputPath = $screenshotItem.FullName
        takenAtUtc = $screenshotItem.LastWriteTimeUtc.ToString("o")
        method = "app-internal-presented-frame-preferred"
        length = $screenshotItem.Length
        image = $screenshotImage
        requestedDelayMs = $ScreenshotDelayMs
        windowWaitMs = $ScreenshotWindowWaitMs
        captureTimeoutMs = $ScreenshotCaptureTimeoutMs
    }
}
if (-not [string]::IsNullOrWhiteSpace($windowScreenshotPath)) {
    if (-not (Test-Path -LiteralPath $windowScreenshotPath)) {
        throw "GUI smoke completed but did not write window screenshot: $windowScreenshotPath"
    }
    $windowScreenshotItem = Get-Item -LiteralPath $windowScreenshotPath
    $windowScreenshotImage = Get-ScreenshotImageMetadata -Path $windowScreenshotPath
    $windowScreenshotCapture = [pscustomobject]@{
        outputPath = $windowScreenshotItem.FullName
        takenAtUtc = $windowScreenshotItem.LastWriteTimeUtc.ToString("o")
        method = "app-internal-window-grab"
        length = $windowScreenshotItem.Length
        image = $windowScreenshotImage
    }
    if (-not [string]::IsNullOrWhiteSpace($fpsStatusCropPath)) {
        $fpsStatusCropCapture = New-FpsStatusProofCrop `
            -SourcePath $windowScreenshotPath `
            -OutputPath $fpsStatusCropPath
    }
}

$logFile = Get-ChildItem -LiteralPath $logRoot -Filter "mlvapp-*.log" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

$recentLines = @()
if ($logFile) {
    $since = $startUtc.AddSeconds(-2)
    foreach ($line in Get-Content -LiteralPath $logFile.FullName) {
        $timestamp = Get-LogTimestampUtc -Line $line
        if ($timestamp -and $timestamp -ge $since) {
            $recentLines += $line
        }
    }
}

$runMetadataLine = $recentLines |
    Where-Object { $_ -like "*run_metadata=*" -and $_ -like "*--gui-smoke-playback*" } |
    Select-Object -Last 1
$playbackStartLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.start*" } |
    Select-Object -Last 1
$summaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.summary*" } |
    Select-Object -Last 1
$cpuSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.cpu_summary*" } |
    Select-Object -Last 1
$processingDetailSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.processing_detail_summary*" } |
    Select-Object -Last 1
$debayerDetailSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.debayer_detail_summary*" } |
    Select-Object -Last 1
$rbfDetailSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.rbf_detail_summary*" } |
    Select-Object -Last 1
$dualIsoSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.dual_iso_full20_summary*" } |
    Select-Object -Last 1
$dualIsoMixChromaSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.dual_iso_mix_chroma_summary*" } |
    Select-Object -Last 1
$lookAssistSettleLine = $recentLines |
    Where-Object { $_ -like "*gui_smoke.look_assist_settle*" } |
    Select-Object -Last 1
$lookAssistApplyLine = $recentLines |
    Where-Object { $_ -like "*look_assist.apply.result*" } |
    Select-Object -Last 1
$visualStateLine = $recentLines |
    Where-Object { $_ -like "*gui_smoke.visual_state*" } |
    Select-Object -Last 1
$cpuSettleLine = $recentLines |
    Where-Object { $_ -like "*gui_smoke.cpu_settle*" } |
    Select-Object -Last 1
$windowScreenshotLine = $recentLines |
    Where-Object { $_ -like "*gui_smoke.window_screenshot*" } |
    Select-Object -Last 1

$runMetadata = $null
if ($runMetadataLine -and $runMetadataLine -match 'run_metadata=(?<json>\{.*\})') {
    $runMetadata = $Matches["json"] | ConvertFrom-Json
}

$playbackStart = if ($playbackStartLine) { Convert-PlaybackLogLineToObject $playbackStartLine } else { $null }
$playbackSummary = if ($summaryLine) { Convert-PlaybackLogLineToObject $summaryLine } else { $null }
$cpuSummary = if ($cpuSummaryLine) { Convert-PlaybackLogLineToObject $cpuSummaryLine } else { $null }
$processingDetailSummary = if ($processingDetailSummaryLine) { Convert-PlaybackLogLineToObject $processingDetailSummaryLine } else { $null }
$debayerDetailSummary = if ($debayerDetailSummaryLine) { Convert-PlaybackLogLineToObject $debayerDetailSummaryLine } else { $null }
$rbfDetailSummary = if ($rbfDetailSummaryLine) { Convert-PlaybackLogLineToObject $rbfDetailSummaryLine } else { $null }
$dualIsoFull20Summary = if ($dualIsoSummaryLine) { Convert-PlaybackLogLineToObject $dualIsoSummaryLine } else { $null }
$dualIsoMixChromaSummary = if ($dualIsoMixChromaSummaryLine) { Convert-PlaybackLogLineToObject $dualIsoMixChromaSummaryLine } else { $null }
$lookAssistSettle = if ($lookAssistSettleLine) { Convert-PlaybackLogLineToObject $lookAssistSettleLine } else { $null }
$lookAssistApply = if ($lookAssistApplyLine) { Convert-PlaybackLogLineToObject $lookAssistApplyLine } else { $null }
$visualState = if ($visualStateLine) { Convert-PlaybackLogLineToObject $visualStateLine } else { $null }
$cpuSettle = if ($cpuSettleLine) { Convert-PlaybackLogLineToObject $cpuSettleLine } else { $null }
$windowScreenshotLog = if ($windowScreenshotLine) { Convert-PlaybackLogLineToObject $windowScreenshotLine } else { $null }
$windowScreenshotFpsStatusText = Get-ObjectPropertyValue $windowScreenshotLog "fps_status"
$windowScreenshotFpsStatusValue = Convert-FpsStatusTextToValue $windowScreenshotFpsStatusText

$lookAssistApplied =
    ($null -ne $lookAssistApply) -and
    ($null -ne $lookAssistSettle) -and
    ($lookAssistSettle.enabled -eq 1) -and
    ($lookAssistSettle.diagnostics_valid -eq 1) -and
    (-not [string]::IsNullOrWhiteSpace([string]$lookAssistApply.scene))

$scaleRequestStart = Get-ObjectPropertyValue $playbackStart "scale_request"
$scaleRequestLast = Get-ObjectPropertyValue $playbackSummary "scale_request_last"
$scaleActiveLast = Get-ObjectPropertyValue $playbackSummary "scale_active_last"
$qualityModeStart = Get-ObjectPropertyValue $playbackStart "quality_mode"
$qualityModeLast = Get-ObjectPropertyValue $playbackSummary "quality_mode"
$validatedScaleRequest = if ($null -ne $scaleRequestLast) { $scaleRequestLast } else { $scaleRequestStart }
$validatedQualityMode = if ($null -ne $qualityModeLast) { $qualityModeLast } else { $qualityModeStart }

$cpuSettled = $true
if ($SettleCpuMaxMs -gt 0 -or $SettleCpuStableMs -gt 0) {
    $cpuSettled = $false
    if ($cpuSettle) {
        $cpuSettled = ($cpuSettle.settled -eq 1)
    }
}

$validationFailures = @()
if ($RequireLookAssist -and -not $lookAssistApplied) {
    $validationFailures += "Look Assist did not settle/apply before playback."
}
if ($null -eq $visualState) {
    $validationFailures += "GUI visual state telemetry was missing."
}
if ($RequireLookAssist -and
    $null -ne $visualState -and
    ([int](Get-ObjectPropertyValue $visualState "look_assist_enabled") -ne 1 -or
     [int](Get-ObjectPropertyValue $visualState "look_assist_diagnostics_valid") -ne 1)) {
    $validationFailures += "GUI visual state did not have settled Look Assist enabled."
}
if ($RequireCpuSettled -and -not $cpuSettled) {
    $validationFailures += "CPU did not settle before playback."
}
if ($RequireCpuSettled -and
    $preLaunchSystemCpuSettle.requested -and
    -not $preLaunchSystemCpuSettle.settled) {
    $validationFailures += "System CPU did not settle before launching MLVApp."
}
if ($ExpectedScaleRequest -ge 0 -and
    ($null -eq $validatedScaleRequest -or [int]$validatedScaleRequest -ne $ExpectedScaleRequest)) {
    $validationFailures += "Playback scale request was $validatedScaleRequest; expected $ExpectedScaleRequest."
}
if ($ExpectedScaleRequest -ge 0 -and
    $null -ne $visualState -and
    [int](Get-ObjectPropertyValue $visualState "scale_request") -ne $ExpectedScaleRequest) {
    $validationFailures += "GUI visual state scale request was $(Get-ObjectPropertyValue $visualState "scale_request"); expected $ExpectedScaleRequest."
}
if ($ExpectedQualityMode -ge 0 -and
    ($null -eq $validatedQualityMode -or [int]$validatedQualityMode -ne $ExpectedQualityMode)) {
    $validationFailures += "Quality mode was $validatedQualityMode; expected $ExpectedQualityMode."
}
if ($ExpectedQualityMode -ge 0 -and
    $null -ne $visualState -and
    [int](Get-ObjectPropertyValue $visualState "quality_mode") -ne $ExpectedQualityMode) {
    $validationFailures += "GUI visual state quality mode was $(Get-ObjectPropertyValue $visualState "quality_mode"); expected $ExpectedQualityMode."
}

$screenshotAspectEvidence = if ($CaptureScreenshot) {
    New-ScreenshotAspectEvidence `
        -ImageMetadata (Get-ObjectPropertyValue $screenshotCapture "image") `
        -VisualState $visualState
} else {
    $null
}

$result = [pscustomobject]@{
    schema = "mlvapp-gui-smoke-result.v1"
    capturedAtUtc = $endUtc.ToString("o")
    repoRoot = $root
    exePath = $exe
    clipPath = $inputPath
    outputPath = $outputPath
    launch = [pscustomobject]@{
        workingDirectory = $root
        arguments = $arguments
        environment = $launchEnv
        validationPolicy = [pscustomobject]@{
            requireLookAssist = $RequireLookAssist
            requireCpuSettled = $RequireCpuSettled
            expectedScaleRequest = $ExpectedScaleRequest
            expectedQualityMode = $ExpectedQualityMode
        }
        matchedUserShellDefaults = [pscustomobject]@{
            frameTelemetry = [bool]$FrameTelemetry
            rbfDetailTiming = [bool]$RbfDetailTiming
            playbackMaxThreads = if ($useAutoPlaybackThreads) { "auto/unset" } else { $Threads }
            scaleFactorUnsetUnlessRequested = [string]::IsNullOrWhiteSpace($ScaleFactor)
            preferHqMean23UnsetUnlessRequested = -not $PreferHqMean23
            experimentalEnvironmentCleared = -not $PreserveExperimentalEnvironment
            clearedEnvironment = $clearedEnvironment
            qtPlatformNotForced = $true
        }
        preLaunchSystemCpuSettle = $preLaunchSystemCpuSettle
    }
    visualQuality = [pscustomobject]@{
        scaleRequestStart = $scaleRequestStart
        scaleRequestLast = $scaleRequestLast
        scaleActiveLast = $scaleActiveLast
        qualityModeStart = $qualityModeStart
        qualityModeLast = $qualityModeLast
        visualState = $visualState
        aspectEvidence = $screenshotAspectEvidence
        lookAssist = [pscustomobject]@{
            applied = [bool]$lookAssistApplied
            enabled = Get-ObjectPropertyValue $lookAssistSettle "enabled"
            diagnosticsValid = Get-ObjectPropertyValue $lookAssistSettle "diagnostics_valid"
            waitMs = Get-ObjectPropertyValue $lookAssistSettle "wait_ms"
            analysis = Get-ObjectPropertyValue $lookAssistApply "analysis"
            scene = Get-ObjectPropertyValue $lookAssistApply "scene"
            median = Get-ObjectPropertyValue $lookAssistApply "median"
            p05 = Get-ObjectPropertyValue $lookAssistApply "p05"
            p95 = Get-ObjectPropertyValue $lookAssistApply "p95"
            p99 = Get-ObjectPropertyValue $lookAssistApply "p99"
            presetExposure = Get-ObjectPropertyValue $lookAssistApply "preset_exp"
            presetContrast = Get-ObjectPropertyValue $lookAssistApply "preset_contrast"
            presetPivot = Get-ObjectPropertyValue $lookAssistApply "preset_pivot"
            presetShadows = Get-ObjectPropertyValue $lookAssistApply "preset_shadows"
            presetHighlights = Get-ObjectPropertyValue $lookAssistApply "preset_highlights"
            presetVibrance = Get-ObjectPropertyValue $lookAssistApply "preset_vibrance"
            presetTemperatureDelta = Get-ObjectPropertyValue $lookAssistApply "preset_temp_delta"
            presetTintDelta = Get-ObjectPropertyValue $lookAssistApply "preset_tint_delta"
            finalTemperature = Get-ObjectPropertyValue $lookAssistApply "final_temp"
            finalTint = Get-ObjectPropertyValue $lookAssistApply "final_tint"
        }
    }
    playbackFps = [pscustomobject]@{
        guiStatusText = Get-ObjectPropertyValue $playbackSummary "gui_fps_status_text"
        guiStatusValue = Get-ObjectPropertyValue $playbackSummary "gui_fps_status_value"
        guiStatusSample = "end-of-run playback summary; this can differ from the visible screenshot-time label"
        visibleBottomLeftGuiStatusText = $windowScreenshotFpsStatusText
        visibleBottomLeftGuiFps = $windowScreenshotFpsStatusValue
        visibleBottomLeftGuiFpsSample = "window screenshot time"
        visibleBottomLeftGuiFpsSource = "gui_smoke.window_screenshot fps_status from screenshot.windowCapture"
        visibleBottomLeftGuiFpsTakenAtUtc = if ($windowScreenshotCapture) { $windowScreenshotCapture.takenAtUtc } else { $null }
        visibleBottomLeftGuiProofPath = $fpsStatusCropPath
        visibleBottomLeftGuiProof = $fpsStatusCropCapture
        screenshotGuiStatusText = $windowScreenshotFpsStatusText
        screenshotGuiStatusValue = $windowScreenshotFpsStatusValue
        smokePresentedFps = Get-ObjectPropertyValue $playbackSummary "presented_fps"
        smokeTimelineFps = Get-ObjectPropertyValue $playbackSummary "timeline_fps"
        reportGuidance = "When citing bottom-left GUI FPS, cite visibleBottomLeftGuiFps and include the visibleBottomLeftGuiProofPath/*-fps-status.png crop. Do not use the presented-frame screenshot as FPS proof because it intentionally omits GUI chrome."
        note = "visibleBottomLeftGuiFps/screenshotGuiStatusValue is the bottom-left Playback FPS label visible at window screenshot time in screenshot.windowCapture and enlarged in playbackFps.visibleBottomLeftGuiProof; guiStatusValue is the later end-of-run summary sample and can differ; smokePresentedFps and smokeTimelineFps are smoke-run telemetry, and per-stage FPS-equivalent values are 1000 / stage_ms."
    }
    process = [pscustomobject]@{
        id = $process.Id
        exitCode = $process.ExitCode
        startedAtUtc = $startUtc.ToString("o")
        endedAtUtc = $endUtc.ToString("o")
        stdout = $stdout.Trim()
        stderr = $stderr.Trim()
    }
    screenshot = [pscustomobject]@{
        requested = [bool]$CaptureScreenshot
        path = $screenshotPath
        capture = $screenshotCapture
        windowPath = $windowScreenshotPath
        windowCapture = $windowScreenshotCapture
        fpsStatusCropPath = $fpsStatusCropPath
        fpsStatusCrop = $fpsStatusCropCapture
        visibleBottomLeftGuiProofPath = $fpsStatusCropPath
        visibleBottomLeftGuiProof = $fpsStatusCropCapture
    }
    log = [pscustomobject]@{
        path = if ($logFile) { $logFile.FullName } else { $null }
        runMetadata = $runMetadata
        playbackStart = $playbackStart
        summary = $playbackSummary
        cpuSummary = $cpuSummary
        processingDetailSummary = $processingDetailSummary
        debayerDetailSummary = $debayerDetailSummary
        rbfDetailSummary = $rbfDetailSummary
        dualIsoFull20Summary = $dualIsoFull20Summary
        dualIsoMixChromaSummary = $dualIsoMixChromaSummary
        lookAssistSettle = $lookAssistSettle
        lookAssistApply = $lookAssistApply
        visualState = $visualState
        cpuSettle = $cpuSettle
        screenshot = $screenshotCapture
        windowScreenshot = $windowScreenshotCapture
        windowScreenshotEvent = $windowScreenshotLog
        fpsStatusCrop = $fpsStatusCropCapture
        raw = [pscustomobject]@{
            runMetadata = $runMetadataLine
            playbackStart = $playbackStartLine
            summary = $summaryLine
            cpuSummary = $cpuSummaryLine
            processingDetailSummary = $processingDetailSummaryLine
            debayerDetailSummary = $debayerDetailSummaryLine
            rbfDetailSummary = $rbfDetailSummaryLine
            dualIsoFull20Summary = $dualIsoSummaryLine
            dualIsoMixChromaSummary = $dualIsoMixChromaSummaryLine
            lookAssistSettle = $lookAssistSettleLine
            lookAssistApply = $lookAssistApplyLine
            visualState = $visualStateLine
            cpuSettle = $cpuSettleLine
            screenshot = if ($screenshotCapture) { $screenshotCapture.outputPath } else { $null }
            windowScreenshot = if ($windowScreenshotCapture) { $windowScreenshotCapture.outputPath } else { $null }
            fpsStatusCrop = if ($fpsStatusCropCapture) { $fpsStatusCropCapture.outputPath } else { $null }
            windowScreenshotEvent = $windowScreenshotLine
        }
    }
}

$result | Add-Member -NotePropertyName validation -NotePropertyValue ([pscustomobject]@{
    ok = ($validationFailures.Count -eq 0)
    lookAssistApplied = [bool]$lookAssistApplied
    cpuSettled = [bool]$cpuSettled
    systemCpuSettled = [bool]$preLaunchSystemCpuSettle.settled
    scaleRequestMatched = ($ExpectedScaleRequest -lt 0 -or
        ($null -ne $validatedScaleRequest -and [int]$validatedScaleRequest -eq $ExpectedScaleRequest))
    qualityModeMatched = ($ExpectedQualityMode -lt 0 -or
        ($null -ne $validatedQualityMode -and [int]$validatedQualityMode -eq $ExpectedQualityMode))
    failures = $validationFailures
})

$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $outputPath -Encoding UTF8
$result | ConvertTo-Json -Depth 8
if ($smokeMutexAcquired) {
    $smokeMutex.ReleaseMutex()
    $smokeMutex.Dispose()
}
if ($process.ExitCode -ne 0) {
    exit $process.ExitCode
}
if ($validationFailures.Count -gt 0) {
    foreach ($failure in $validationFailures) {
        Write-Error $failure
    }
    exit 2
}
exit 0
