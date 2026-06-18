param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [string]$Output = "",
    [int]$Seconds = 8,
    [int]$StartFrame = 0,
    [int]$SettleMs = 2500,
    [double]$SettleCpuPercent = 10,
    [int]$SettleCpuStableMs = 1000,
    [int]$SettleCpuMaxMs = 45000,
    [double]$SystemSettleCpuPercent = -1,
    [int]$SystemSettleCpuStableMs = 2000,
    [int]$SystemSettleCpuMaxMs = 60000,
    [string]$Threads = "auto",
    [string]$QualityMode = "",
    [string]$ScaleFactor = "",
    [int]$ExpectedScaleRequest = 1,
    [int]$ExpectedVisualScaleRequest = -2,
    [int]$ExpectedQualityMode = 1,
    [switch]$PreferHqMean23,
    [switch]$FrameTelemetry,
    [switch]$RbfDetailTiming,
    [switch]$CaptureScreenshot,
    [switch]$FailOnColorArtifact,
    [switch]$SkipWindowScreenshot,
    [string]$ScreenshotOutputDir = "",
    [int]$ScreenshotDelayMs = 2000,
    [int]$ScreenshotWindowWaitMs = 10000,
    [int]$ScreenshotCaptureTimeoutMs = 2500,
    [switch]$PreserveExperimentalEnvironment,
    [string[]]$ExtraEnvironment = @(),
    [string]$Receipt = "",
    [string]$Scope = "",
    [ValidateSet("", "auto", "receipt", "none", "simple", "bilinear", "lmmse", "igv", "amaze", "ahd", "rcd", "dcb", "amaze-cached")]
    [string]$PlaybackDebayer = "",
    [ValidateSet("", "auto", "receipt", "subset")]
    [string]$PlaybackProcessing = "",
    [ValidateSet("", "auto", "cpu", "gpu")]
    [string]$GpuPreviewProcessing = "",
    [ValidateSet("", "auto", "cpu", "gpu")]
    [string]$GpuBilinearDebayer = "",
    [ValidateSet("", "auto", "cpu", "gpu")]
    [string]$GpuAmazeDebayer = "",
    [switch]$GpuAmazeTexturePresent,
    [switch]$DisableLookAssist,
    [switch]$EnablePhase3QualityModes,
    [string]$StageLog = "",
    [ValidateSet("", "on", "off")]
    [string]$Zebras = "",
    [bool]$RequireLookAssist = $true,
    [bool]$RequireCpuSettled = $true,
    [string[]]$AdditionalArgs = @(),
    [switch]$DetectPlaybackArtifacts,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$settledValidationRecommendedSeconds = 30
$validationWarnings = @()

# Opt-in temporal-artifact gate: turn on the interactive trace so the post-run detector can measure
# flicker / stalls / cadence jitter from per-present events. Auto-discounts the screenshot grab.
if ($DetectPlaybackArtifacts -and -not ($ExtraEnvironment | Where-Object { $_ -match '^MLVAPP_INTERACTIVE_TRACE=' })) {
    $ExtraEnvironment += 'MLVAPP_INTERACTIVE_TRACE=1'
}

if (($CaptureScreenshot -or $FrameTelemetry) -and $Seconds -lt $settledValidationRecommendedSeconds) {
    $validationWarnings += (
        "Requested playback window of ${Seconds}s is shorter than the recommended " +
        "${settledValidationRecommendedSeconds}s settled-validation window."
    )
    Write-Warning (
        "GUI smoke requested ${Seconds}s playback, which is shorter than the recommended " +
        "${settledValidationRecommendedSeconds}s settled-validation window for screenshot-backed checks."
    )
}

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

function Add-ColorArtifactScannerType {
    Add-Type -AssemblyName System.Drawing
    if ("MlvGuiSmokeColorArtifactScanner" -as [type]) {
        return
    }

    Add-Type -ReferencedAssemblies @(
        "System.Drawing",
        "System.Drawing.Common",
        "System.Drawing.Primitives",
        "System.Private.Windows.GdiPlus",
        "System.Private.Windows.Core"
    ) -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public sealed class MlvGuiSmokeColorArtifactScanResult
{
    public string Path;
    public int Width;
    public int Height;
    public int Step;
    public int VisibleSamples;
    public double MagentaRatio;
    public double GreenRatio;
    public double TopBandMagentaRatio;
    public double TopBandGreenRatio;
    public double BottomBandMagentaRatio;
    public double BottomBandGreenRatio;
    public double MaxTileMagentaRatio;
    public double MaxTileGreenRatio;
    public double MeanMagentaAxis;
    public double MeanGreenAxis;
    public string Verdict;
}

public static class MlvGuiSmokeColorArtifactScanner
{
    public static MlvGuiSmokeColorArtifactScanResult Scan(string path)
    {
        using (var source = new Bitmap(path))
        using (var bitmap = new Bitmap(source.Width, source.Height, PixelFormat.Format24bppRgb))
        using (var graphics = Graphics.FromImage(bitmap))
        {
            graphics.DrawImage(source, 0, 0, source.Width, source.Height);
            int width = bitmap.Width;
            int height = bitmap.Height;
            int step = Math.Max(1, Math.Min(width, height) / 512);
            if (step < 2) step = 2;

            const int tileCols = 16;
            const int tileRows = 12;
            int[,] tileTotal = new int[tileRows, tileCols];
            int[,] tileMagenta = new int[tileRows, tileCols];
            int[,] tileGreen = new int[tileRows, tileCols];

            int visible = 0;
            int magenta = 0;
            int green = 0;
            int topVisible = 0;
            int topMagenta = 0;
            int topGreen = 0;
            int bottomVisible = 0;
            int bottomMagenta = 0;
            int bottomGreen = 0;
            double magentaAxisSum = 0.0;
            double greenAxisSum = 0.0;

            Rectangle rect = new Rectangle(0, 0, width, height);
            BitmapData data = bitmap.LockBits(rect, ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
            try
            {
                int stride = data.Stride;
                int bytes = Math.Abs(stride) * height;
                byte[] buffer = new byte[bytes];
                Marshal.Copy(data.Scan0, buffer, 0, bytes);

                for (int y = 0; y < height; y += step)
                {
                    int row = y * stride;
                    bool topBand = y < height / 10;
                    bool bottomBand = y >= (height * 9) / 10;
                    int tileY = Math.Min(tileRows - 1, (int)((long)y * tileRows / Math.Max(1, height)));
                    for (int x = 0; x < width; x += step)
                    {
                        int offset = row + x * 3;
                        if (offset < 0 || offset + 2 >= buffer.Length) continue;
                        int b = buffer[offset + 0];
                        int g = buffer[offset + 1];
                        int r = buffer[offset + 2];
                        double luminance = (r + g + b) / 3.0;
                        if (luminance < 24.0) continue;

                        double magentaAxis = ((r + b) * 0.5) - g;
                        double greenAxis = g - ((r + b) * 0.5);
                        bool magentaHit = magentaAxis > 45.0 && r > 80 && b > 80 && (r - g) > 35 && (b - g) > 20;
                        bool greenHit = greenAxis > 45.0 && g > 80 && (g - r) > 35 && (g - b) > 20;

                        visible++;
                        magentaAxisSum += magentaAxis;
                        greenAxisSum += greenAxis;
                        if (magentaHit) magenta++;
                        if (greenHit) green++;

                        if (topBand)
                        {
                            topVisible++;
                            if (magentaHit) topMagenta++;
                            if (greenHit) topGreen++;
                        }
                        if (bottomBand)
                        {
                            bottomVisible++;
                            if (magentaHit) bottomMagenta++;
                            if (greenHit) bottomGreen++;
                        }

                        int tileX = Math.Min(tileCols - 1, (int)((long)x * tileCols / Math.Max(1, width)));
                        tileTotal[tileY, tileX]++;
                        if (magentaHit) tileMagenta[tileY, tileX]++;
                        if (greenHit) tileGreen[tileY, tileX]++;
                    }
                }
            }
            finally
            {
                bitmap.UnlockBits(data);
            }

            double maxTileMagenta = 0.0;
            double maxTileGreen = 0.0;
            for (int ty = 0; ty < tileRows; ++ty)
            {
                for (int tx = 0; tx < tileCols; ++tx)
                {
                    int total = tileTotal[ty, tx];
                    if (total <= 0) continue;
                    maxTileMagenta = Math.Max(maxTileMagenta, (double)tileMagenta[ty, tx] / total);
                    maxTileGreen = Math.Max(maxTileGreen, (double)tileGreen[ty, tx] / total);
                }
            }

            double magentaRatio = visible > 0 ? (double)magenta / visible : 0.0;
            double greenRatio = visible > 0 ? (double)green / visible : 0.0;
            double topMagentaRatio = topVisible > 0 ? (double)topMagenta / topVisible : 0.0;
            double topGreenRatio = topVisible > 0 ? (double)topGreen / topVisible : 0.0;
            double bottomMagentaRatio = bottomVisible > 0 ? (double)bottomMagenta / bottomVisible : 0.0;
            double bottomGreenRatio = bottomVisible > 0 ? (double)bottomGreen / bottomVisible : 0.0;

            bool barSuspect = topMagentaRatio > 0.08 || topGreenRatio > 0.08 ||
                              bottomMagentaRatio > 0.08 || bottomGreenRatio > 0.08;
            bool blockSuspect = maxTileMagenta > 0.22 || maxTileGreen > 0.22;
            bool globalSuspect = magentaRatio > 0.06 || greenRatio > 0.06;
            string verdict = (barSuspect || blockSuspect) ? "suspect-block-or-bar" :
                             globalSuspect ? "global-color-axis-present" :
                             "clear-heuristic";

            return new MlvGuiSmokeColorArtifactScanResult {
                Path = path,
                Width = width,
                Height = height,
                Step = step,
                VisibleSamples = visible,
                MagentaRatio = Math.Round(magentaRatio, 6),
                GreenRatio = Math.Round(greenRatio, 6),
                TopBandMagentaRatio = Math.Round(topMagentaRatio, 6),
                TopBandGreenRatio = Math.Round(topGreenRatio, 6),
                BottomBandMagentaRatio = Math.Round(bottomMagentaRatio, 6),
                BottomBandGreenRatio = Math.Round(bottomGreenRatio, 6),
                MaxTileMagentaRatio = Math.Round(maxTileMagenta, 6),
                MaxTileGreenRatio = Math.Round(maxTileGreen, 6),
                MeanMagentaAxis = visible > 0 ? Math.Round(magentaAxisSum / visible, 3) : 0.0,
                MeanGreenAxis = visible > 0 ? Math.Round(greenAxisSum / visible, 3) : 0.0,
                Verdict = verdict
            };
        }
    }
}
"@
}

function Get-ScreenshotColorArtifactScan {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{
            requested = $false
            path = $Path
            verdict = "not-captured"
            error = $null
        }
    }

    try {
        Add-ColorArtifactScannerType
        $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
        $scan = [MlvGuiSmokeColorArtifactScanner]::Scan($resolvedPath)
        [pscustomobject]@{
            requested = $true
            path = $scan.Path
            width = $scan.Width
            height = $scan.Height
            step = $scan.Step
            visibleSamples = $scan.VisibleSamples
            verdict = $scan.Verdict
            magentaRatio = $scan.MagentaRatio
            greenRatio = $scan.GreenRatio
            topBandMagentaRatio = $scan.TopBandMagentaRatio
            topBandGreenRatio = $scan.TopBandGreenRatio
            bottomBandMagentaRatio = $scan.BottomBandMagentaRatio
            bottomBandGreenRatio = $scan.BottomBandGreenRatio
            maxTileMagentaRatio = $scan.MaxTileMagentaRatio
            maxTileGreenRatio = $scan.MaxTileGreenRatio
            meanMagentaAxis = $scan.MeanMagentaAxis
            meanGreenAxis = $scan.MeanGreenAxis
            thresholds = [pscustomobject]@{
                bandRatio = 0.08
                tileRatio = 0.22
                globalRatio = 0.06
                verdictsThatFailWhenRequested = @("suspect-block-or-bar", "scan-error")
            }
            note = "Sampled presented-frame screenshot scan for magenta/pink/green bars and tinted blocks; use with -FailOnColorArtifact to make suspect block/bar verdicts validation failures."
            error = $null
        }
    }
    catch {
        [pscustomobject]@{
            requested = $true
            path = $Path
            verdict = "scan-error"
            error = $_.Exception.Message
        }
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
$logRoot = Join-Path $outputDir "logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

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
    "--start-frame", [string]$StartFrame,
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
if (-not [string]::IsNullOrWhiteSpace($PlaybackDebayer)) {
    $arguments += "--playback-debayer=$PlaybackDebayer"
}
if (-not [string]::IsNullOrWhiteSpace($PlaybackProcessing)) {
    $arguments += "--playback-processing=$PlaybackProcessing"
}
if (-not [string]::IsNullOrWhiteSpace($GpuPreviewProcessing)) {
    $arguments += "--gpu-preview-processing=$GpuPreviewProcessing"
}
if (-not [string]::IsNullOrWhiteSpace($GpuBilinearDebayer)) {
    $arguments += "--gpu-bilinear-debayer=$GpuBilinearDebayer"
}
if (-not [string]::IsNullOrWhiteSpace($GpuAmazeDebayer)) {
    $arguments += "--gpu-amaze-debayer=$GpuAmazeDebayer"
}
if ($GpuAmazeTexturePresent) {
    $arguments += "--gpu-amaze-texture-present"
}
if ($DisableLookAssist) {
    $arguments += "--no-look-assist"
}
if ($EnablePhase3QualityModes) {
    $arguments += "--enable-phase3-quality-modes"
}
if (-not [string]::IsNullOrWhiteSpace($StageLog)) {
    $stageLogPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StageLog)
    $stageLogDir = [IO.Path]::GetDirectoryName($stageLogPath)
    if (-not [string]::IsNullOrWhiteSpace($stageLogDir)) {
        New-Item -ItemType Directory -Force -Path $stageLogDir | Out-Null
    }
    $arguments += "--stage-log=$stageLogPath"
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
$launchEnv["MLVAPP_CRASH_FORENSICS_LOG_DIR"] = $logRoot
if (-not [string]::IsNullOrWhiteSpace($QualityMode)) {
    $launchEnv["MLVAPP_PLAYBACK_QUALITY_MODE"] = $QualityMode
}
if ($EnablePhase3QualityModes) {
    $launchEnv["MLVAPP_PLAYBACK_PHASE3_UNATTENDED"] = "1"
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
    "MLVAPP_SHADOWS_HIGHLIGHTS_PROBE",
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
if ([string]::IsNullOrWhiteSpace($QualityMode) -and -not $launchEnv.Contains("MLVAPP_PLAYBACK_QUALITY_MODE")) {
    $clearedEnvironment += "MLVAPP_PLAYBACK_QUALITY_MODE"
}
if (-not $EnablePhase3QualityModes -and -not $launchEnv.Contains("MLVAPP_PLAYBACK_PHASE3_UNATTENDED")) {
    $clearedEnvironment += "MLVAPP_PLAYBACK_PHASE3_UNATTENDED"
}
if (-not $RbfDetailTiming -and -not $launchEnv.Contains("MLVAPP_PLAYBACK_RBF_DETAIL_TIMING")) {
    $clearedEnvironment += "MLVAPP_PLAYBACK_RBF_DETAIL_TIMING"
}
if ([string]::IsNullOrWhiteSpace($ScaleFactor) -and -not $launchEnv.Contains("MLVAPP_PLAYBACK_SCALE_FACTOR")) {
    $clearedEnvironment += "MLVAPP_PLAYBACK_SCALE_FACTOR"
}
if (-not $PreferHqMean23 -and -not $launchEnv.Contains("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23")) {
    $clearedEnvironment += "MLVAPP_PLAYBACK_PREFER_HQ_MEAN23"
}
if (-not $PreserveExperimentalEnvironment) {
    $clearedEnvironment += $experimentalEnvironmentToClear
}
$clearedEnvironment = @($clearedEnvironment | Select-Object -Unique)
$effectiveExpectedVisualScaleRequest = if ($ExpectedVisualScaleRequest -eq -2) {
    $ExpectedScaleRequest
} else {
    $ExpectedVisualScaleRequest
}

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
            expectedVisualScaleRequest = $effectiveExpectedVisualScaleRequest
            expectedQualityMode = $ExpectedQualityMode
            qualityModeOverride = $QualityMode
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
$envBlock["MLVAPP_CRASH_FORENSICS_LOG_DIR"] = $logRoot
if (-not [string]::IsNullOrWhiteSpace($QualityMode)) {
    $envBlock["MLVAPP_PLAYBACK_QUALITY_MODE"] = $QualityMode
}
else {
    [void]$envBlock.Remove("MLVAPP_PLAYBACK_QUALITY_MODE")
}
if ($EnablePhase3QualityModes) {
    $envBlock["MLVAPP_PLAYBACK_PHASE3_UNATTENDED"] = "1"
}
else {
    [void]$envBlock.Remove("MLVAPP_PLAYBACK_PHASE3_UNATTENDED")
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
$colorArtifactScan = $null
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
    $colorArtifactScan = Get-ScreenshotColorArtifactScan -Path $screenshotPath
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
    Where-Object { $_ -like "*playback_smoke.cpu_summary *" } |
    Select-Object -Last 1
# Round-4: fields that used to sit beyond QString::arg's %99 limit (garbage)
# now arrive on a continuation line; merged into the same cpuSummary object.
$cpuSummaryExtLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.cpu_summary_ext*" } |
    Select-Object -Last 1
$stageSplitSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.stage_split_summary*" } |
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
# Since the async Look Assist apply (master 8ddddce2), the default path finishes with
# look_assist.apply.auto_wb_async_applied and never emits look_assist.apply.result
# (that event is sync-fallback-only). Accept the async terminal event as apply
# evidence; the scene then comes from the settle line, which fires only after the
# diagnostics turned valid.
$lookAssistAsyncApplyLine = $recentLines |
    Where-Object { $_ -like "*look_assist.apply.auto_wb_async_applied*" } |
    Select-Object -Last 1
$visualStateLine = $recentLines |
    Where-Object { $_ -like "*gui_smoke.visual_state*" } |
    Select-Object -Last 1
$playbackPolicyLine = $recentLines |
    Where-Object { $_ -like "*gui_smoke.playback_policy*" } |
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
if ($cpuSummary -and $cpuSummaryExtLine) {
    $cpuSummaryExt = Convert-PlaybackLogLineToObject $cpuSummaryExtLine
    if ($cpuSummaryExt) {
        foreach ($p in $cpuSummaryExt.PSObject.Properties) {
            if ($p.Name -ne 'session') {
                $cpuSummary | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force
            }
        }
    }
}
$stageSplitSummary = if ($stageSplitSummaryLine) { Convert-PlaybackLogLineToObject $stageSplitSummaryLine } else { $null }
$processingDetailSummary = if ($processingDetailSummaryLine) { Convert-PlaybackLogLineToObject $processingDetailSummaryLine } else { $null }
$debayerDetailSummary = if ($debayerDetailSummaryLine) { Convert-PlaybackLogLineToObject $debayerDetailSummaryLine } else { $null }
$rbfDetailSummary = if ($rbfDetailSummaryLine) { Convert-PlaybackLogLineToObject $rbfDetailSummaryLine } else { $null }
$dualIsoFull20Summary = if ($dualIsoSummaryLine) { Convert-PlaybackLogLineToObject $dualIsoSummaryLine } else { $null }
$dualIsoMixChromaSummary = if ($dualIsoMixChromaSummaryLine) { Convert-PlaybackLogLineToObject $dualIsoMixChromaSummaryLine } else { $null }
$lookAssistSettle = if ($lookAssistSettleLine) { Convert-PlaybackLogLineToObject $lookAssistSettleLine } else { $null }
$lookAssistApply = if ($lookAssistApplyLine) { Convert-PlaybackLogLineToObject $lookAssistApplyLine } else { $null }
$lookAssistAsyncApply = if ($lookAssistAsyncApplyLine) { Convert-PlaybackLogLineToObject $lookAssistAsyncApplyLine } else { $null }
$visualState = if ($visualStateLine) { Convert-PlaybackLogLineToObject $visualStateLine } else { $null }
$playbackPolicy = if ($playbackPolicyLine) { Convert-PlaybackLogLineToObject $playbackPolicyLine } else { $null }
$cpuSettle = if ($cpuSettleLine) { Convert-PlaybackLogLineToObject $cpuSettleLine } else { $null }
$windowScreenshotLog = if ($windowScreenshotLine) { Convert-PlaybackLogLineToObject $windowScreenshotLine } else { $null }
$windowScreenshotFpsStatusText = Get-ObjectPropertyValue $windowScreenshotLog "fps_status"
$windowScreenshotFpsStatusValue = Convert-FpsStatusTextToValue $windowScreenshotFpsStatusText
$requestedPlaybackDurationMs = [int]([Math]::Max(100, $Seconds * 1000))
$sustainedGuiFpsSample =
    "end-of-requested-duration window screenshot after ${Seconds}s playback"

$lookAssistApplied =
    ($null -ne $lookAssistSettle) -and
    ($lookAssistSettle.enabled -eq 1) -and
    ($lookAssistSettle.diagnostics_valid -eq 1) -and
    ( ( ($null -ne $lookAssistApply) -and
        (-not [string]::IsNullOrWhiteSpace([string]$lookAssistApply.scene)) ) -or
      ( ($null -ne $lookAssistAsyncApply) -and
        (-not [string]::IsNullOrWhiteSpace([string]$lookAssistSettle.scene)) ) )

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
$colorArtifactFailureVerdicts = @("suspect-block-or-bar", "scan-error")
$colorArtifactVerdict =
    if ($null -ne $colorArtifactScan) { [string]$colorArtifactScan.verdict } else { "not-captured" }
$colorArtifactScanPassed =
    -not ($colorArtifactFailureVerdicts -contains $colorArtifactVerdict)

$validationFailures = @()
if ($null -ne $process -and $process.ExitCode -ne 0) {
    $validationFailures += "MLVApp exited with code $($process.ExitCode)."
}
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
if ($FailOnColorArtifact -and
    $null -ne $colorArtifactScan -and
    -not $colorArtifactScanPassed) {
    $validationFailures += "Color artifact scan verdict was $colorArtifactVerdict."
}
if ($ExpectedScaleRequest -ge 0 -and
    ($null -eq $validatedScaleRequest -or [int]$validatedScaleRequest -ne $ExpectedScaleRequest)) {
    $validationFailures += "Playback scale request was $validatedScaleRequest; expected $ExpectedScaleRequest."
}
if ($effectiveExpectedVisualScaleRequest -ge 0 -and
    $null -ne $visualState -and
    [int](Get-ObjectPropertyValue $visualState "scale_request") -ne $effectiveExpectedVisualScaleRequest) {
    $validationFailures += "GUI visual state scale request was $(Get-ObjectPropertyValue $visualState "scale_request"); expected $effectiveExpectedVisualScaleRequest."
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

# Temporal playback-artifact gate (flicker / stall / cadence jitter) parsed from the interactive trace.
$playbackArtifacts = $null
if ($DetectPlaybackArtifacts) {
    $detectorScript = Join-Path $root "tools/profiling/detect-playback-artifacts.ps1"
    $traceLogPath = if ($logFile) { $logFile.FullName } else { $null }
    if ((Test-Path -LiteralPath $detectorScript) -and $traceLogPath -and (Test-Path -LiteralPath $traceLogPath)) {
        $detectorPwsh = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
        if (-not $detectorPwsh) { $detectorPwsh = (Get-Command powershell.exe).Source }
        $detectorOut = & $detectorPwsh -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $detectorScript -TraceLog $traceLogPath 2>&1
        $detectorExit = $LASTEXITCODE
        $detectorText = ($detectorOut | Out-String).Trim()
        $checkLine = ($detectorOut | Where-Object { $_ -match '^ARTIFACT-CHECK ' } | Select-Object -First 1)
        $getField = { param($text, $key) $m = [regex]::Match([string]$text, $key + '=([^\s]+)'); if ($m.Success) { $m.Groups[1].Value } else { $null } }
        $playbackArtifacts = [pscustomobject]@{
            enabled = $true
            verdict = (& $getField $checkLine 'verdict')
            presents = [int](& $getField $checkLine 'presents')
            medianFps = [double](& $getField $checkLine 'median_fps')
            p90Ms = [int](& $getField $checkLine 'p90_ms')
            p99Ms = [int](& $getField $checkLine 'p99_ms')
            maxIntervalMs = [int](& $getField $checkLine 'max_interval_ms')
            hitchFraction = [double](& $getField $checkLine 'hitch_frac')
            flickerBackJumps = [int](& $getField $checkLine 'flicker_back_jumps')
            maxBackJump = [int](& $getField $checkLine 'max_back_jump')
            maxLag = [int](& $getField $checkLine 'max_lag')
            contentEvents = [int](& $getField $checkLine 'content_events')
            distinctHashes = [int](& $getField $checkLine 'distinct_hashes')
            frozenContentRuns = [int](& $getField $checkLine 'frozen_content_runs')
            longestFrozenRun = [int](& $getField $checkLine 'longest_frozen_run')
            detectorExitCode = $detectorExit
            traceLogPath = $traceLogPath
            detectorScript = $detectorScript
            detail = $detectorText
        }
    } else {
        $playbackArtifacts = [pscustomobject]@{
            enabled = $true
            verdict = "no-data"
            detail = "detector or trace log not found (detector=$detectorScript, trace=$traceLogPath)"
        }
    }
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
            failOnColorArtifact = [bool]$FailOnColorArtifact
            expectedScaleRequest = $ExpectedScaleRequest
            expectedVisualScaleRequest = $effectiveExpectedVisualScaleRequest
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
        playbackPolicy = $playbackPolicy
        aspectEvidence = $screenshotAspectEvidence
        colorArtifactScan = $colorArtifactScan
        lookAssist = [pscustomobject]@{
            applied = [bool]$lookAssistApplied
            asyncApplied = [bool]($null -ne $lookAssistAsyncApply)
            asyncDecision = Get-ObjectPropertyValue $lookAssistAsyncApply "decision"
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
        requestedPlaybackSeconds = $Seconds
        requestedPlaybackDurationMs = $requestedPlaybackDurationMs
        guiStatusText = Get-ObjectPropertyValue $playbackSummary "gui_fps_status_text"
        guiStatusValue = Get-ObjectPropertyValue $playbackSummary "gui_fps_status_value"
        guiStatusSample = "end-of-run playback summary; this can differ from the visible screenshot-time label"
        visibleBottomLeftGuiStatusText = $windowScreenshotFpsStatusText
        visibleBottomLeftGuiFps = $windowScreenshotFpsStatusValue
        visibleBottomLeftGuiFpsSample = $sustainedGuiFpsSample
        visibleBottomLeftGuiFpsSource = "gui_smoke.window_screenshot fps_status from screenshot.windowCapture"
        visibleBottomLeftGuiFpsTakenAtUtc = if ($windowScreenshotCapture) { $windowScreenshotCapture.takenAtUtc } else { $null }
        visibleBottomLeftGuiProofPath = $fpsStatusCropPath
        visibleBottomLeftGuiProof = $fpsStatusCropCapture
        sustainedBottomLeftGuiStatusText = $windowScreenshotFpsStatusText
        sustainedBottomLeftGuiFps = $windowScreenshotFpsStatusValue
        sustainedBottomLeftGuiFpsSample = $sustainedGuiFpsSample
        sustainedBottomLeftGuiFpsTakenAtUtc = if ($windowScreenshotCapture) { $windowScreenshotCapture.takenAtUtc } else { $null }
        sustainedBottomLeftGuiProofPath = $fpsStatusCropPath
        sustainedBottomLeftGuiProof = $fpsStatusCropCapture
        screenshotGuiStatusText = $windowScreenshotFpsStatusText
        screenshotGuiStatusValue = $windowScreenshotFpsStatusValue
        smokePresentedFps = Get-ObjectPropertyValue $playbackSummary "presented_fps"
        smokeTimelineFps = Get-ObjectPropertyValue $playbackSummary "timeline_fps"
        reportGuidance = "When citing bottom-left GUI FPS, cite sustainedBottomLeftGuiFps/visibleBottomLeftGuiFps and include the sustainedBottomLeftGuiProofPath/*-fps-status.png crop. For stable FPS, run long enough for playback to settle, for example -Seconds 30. Do not use the presented-frame screenshot as FPS proof because it intentionally omits GUI chrome."
        note = "sustainedBottomLeftGuiFps/visibleBottomLeftGuiFps/screenshotGuiStatusValue is the bottom-left Playback FPS label visible after the requested playback duration in screenshot.windowCapture and enlarged in playbackFps.sustainedBottomLeftGuiProof; guiStatusValue is the later end-of-run summary sample and can differ; smokePresentedFps and smokeTimelineFps are smoke-run telemetry over the full requested duration, and per-stage FPS-equivalent values are 1000 / stage_ms."
    }
    playbackArtifacts = $playbackArtifacts
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
    stageSplitSummary = $stageSplitSummary
    processingDetailSummary = $processingDetailSummary
        debayerDetailSummary = $debayerDetailSummary
        rbfDetailSummary = $rbfDetailSummary
        dualIsoFull20Summary = $dualIsoFull20Summary
        dualIsoMixChromaSummary = $dualIsoMixChromaSummary
        lookAssistSettle = $lookAssistSettle
        lookAssistApply = $lookAssistApply
        visualState = $visualState
        playbackPolicy = $playbackPolicy
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
            playbackPolicy = $playbackPolicyLine
            cpuSettle = $cpuSettleLine
            screenshot = if ($screenshotCapture) { $screenshotCapture.outputPath } else { $null }
            windowScreenshot = if ($windowScreenshotCapture) { $windowScreenshotCapture.outputPath } else { $null }
            fpsStatusCrop = if ($fpsStatusCropCapture) { $fpsStatusCropCapture.outputPath } else { $null }
            windowScreenshotEvent = $windowScreenshotLine
        }
    }
}

if ($DetectPlaybackArtifacts -and $playbackArtifacts -and $playbackArtifacts.verdict -eq "FAIL") {
    $validationFailures += ("Playback artifact detector verdict FAIL: " + (($playbackArtifacts.detail) -replace '\s+', ' '))
}

$result | Add-Member -NotePropertyName validation -NotePropertyValue ([pscustomobject]@{
    ok = ($validationFailures.Count -eq 0)
    lookAssistApplied = [bool]$lookAssistApplied
    cpuSettled = [bool]$cpuSettled
    systemCpuSettled = [bool]$preLaunchSystemCpuSettle.settled
    colorArtifactScanPassed = [bool]$colorArtifactScanPassed
    colorArtifactScanVerdict = $colorArtifactVerdict
    scaleRequestMatched = ($ExpectedScaleRequest -lt 0 -or
        ($null -ne $validatedScaleRequest -and [int]$validatedScaleRequest -eq $ExpectedScaleRequest))
    qualityModeMatched = ($ExpectedQualityMode -lt 0 -or
        ($null -ne $validatedQualityMode -and [int]$validatedQualityMode -eq $ExpectedQualityMode))
    failures = $validationFailures
    warnings = $validationWarnings
    settledValidationRecommendedSeconds = $settledValidationRecommendedSeconds
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
