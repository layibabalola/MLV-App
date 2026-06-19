param(
    [Parameter(Mandatory=$true)]
    [string]$Before,
    [Parameter(Mandatory=$true)]
    [string]$After,
    [string]$Output = "",
    [double]$MaxMeanAbsRgbDelta = 2.0,
    [double]$MaxChangedSampleRatio = 0.01,
    [int]$SampleStep = 4,
    [switch]$FailOnScreenshotDelta
)

$ErrorActionPreference = "Stop"

function Read-SmokeJson {
    param([string]$Path)

    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    if (-not (Test-Path -LiteralPath $resolved)) {
        throw "Smoke JSON not found: $resolved"
    }
    Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json -Depth 100
}

function Get-NestedValue {
    param(
        [object]$Object,
        [string]$Path
    )

    $current = $Object
    foreach ($part in $Path.Split('.')) {
        if ($null -eq $current) {
            return $null
        }
        $property = $current.PSObject.Properties[$part]
        if (-not $property) {
            return $null
        }
        $current = $property.Value
    }
    $current
}

function Get-FirstNestedValue {
    param(
        [object]$Object,
        [string[]]$Paths
    )

    foreach ($path in $Paths) {
        $value = Get-NestedValue -Object $Object -Path $path
        if ($null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)) {
            return $value
        }
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

function New-DeltaObject {
    param(
        [object]$BeforeValue,
        [object]$AfterValue
    )

    $beforeDouble = Convert-ToNullableDouble $BeforeValue
    $afterDouble = Convert-ToNullableDouble $AfterValue
    $delta = $null
    $deltaPercent = $null
    if ($null -ne $beforeDouble -and $null -ne $afterDouble) {
        $delta = [Math]::Round($afterDouble - $beforeDouble, 6)
        if ([Math]::Abs($beforeDouble) -gt 0.000001) {
            $deltaPercent = [Math]::Round((($afterDouble - $beforeDouble) / $beforeDouble) * 100.0, 3)
        }
    }

    [pscustomobject]@{
        before = $beforeDouble
        after = $afterDouble
        delta = $delta
        deltaPercent = $deltaPercent
    }
}

function Resolve-SmokeScreenshotPath {
    param([object]$Smoke)

    $path = Get-FirstNestedValue -Object $Smoke -Paths @(
        "screenshot.capture.outputPath",
        "screenshot.path",
        "visualQuality.glOutputProof.screenshotPath"
    )
    if ($null -eq $path) {
        return $null
    }
    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath([string]$path)
    if (Test-Path -LiteralPath $resolved) {
        return $resolved
    }
    [string]$path
}

function Get-ImageMetadata {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    Add-Type -AssemblyName System.Drawing
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $image = [System.Drawing.Image]::FromFile($resolved)
    try {
        [pscustomobject]@{
            path = $resolved
            width = $image.Width
            height = $image.Height
            sha256 = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash
        }
    }
    finally {
        $image.Dispose()
    }
}

function Compare-SmokeScreenshots {
    param(
        [string]$BeforePath,
        [string]$AfterPath,
        [int]$RequestedSampleStep
    )

    $beforeMeta = Get-ImageMetadata -Path $BeforePath
    $afterMeta = Get-ImageMetadata -Path $AfterPath
    if ($null -eq $beforeMeta -or $null -eq $afterMeta) {
        return [pscustomobject]@{
            status = "not-compared"
            reason = "one-or-both-screenshots-missing"
            before = $beforeMeta
            after = $afterMeta
            sameSha256 = $false
            dimensionsMatch = $false
            meanAbsRgbDelta = $null
            maxAbsRgbDelta = $null
            changedSampleRatio = $null
            sampledPixels = 0
        }
    }

    if ($beforeMeta.width -ne $afterMeta.width -or $beforeMeta.height -ne $afterMeta.height) {
        return [pscustomobject]@{
            status = "dimension-mismatch"
            reason = "screenshot-dimensions-differ"
            before = $beforeMeta
            after = $afterMeta
            sameSha256 = $beforeMeta.sha256 -eq $afterMeta.sha256
            dimensionsMatch = $false
            meanAbsRgbDelta = $null
            maxAbsRgbDelta = $null
            changedSampleRatio = $null
            sampledPixels = 0
        }
    }

    Add-Type -AssemblyName System.Drawing
    $beforeBitmap = [System.Drawing.Bitmap]::FromFile($beforeMeta.path)
    $afterBitmap = [System.Drawing.Bitmap]::FromFile($afterMeta.path)
    try {
        $width = $beforeBitmap.Width
        $height = $beforeBitmap.Height
        $autoStep = [Math]::Max(1, [int]([Math]::Min($width, $height) / 512))
        $step = [Math]::Max(1, [Math]::Max($RequestedSampleStep, $autoStep))
        $sampled = 0
        $changed = 0
        $sumAbs = 0.0
        $maxAbs = 0
        for ($y = 0; $y -lt $height; $y += $step) {
            for ($x = 0; $x -lt $width; $x += $step) {
                $beforePixel = $beforeBitmap.GetPixel($x, $y)
                $afterPixel = $afterBitmap.GetPixel($x, $y)
                $dr = [Math]::Abs([int]$afterPixel.R - [int]$beforePixel.R)
                $dg = [Math]::Abs([int]$afterPixel.G - [int]$beforePixel.G)
                $db = [Math]::Abs([int]$afterPixel.B - [int]$beforePixel.B)
                $pixelMax = [Math]::Max($dr, [Math]::Max($dg, $db))
                $sumAbs += $dr + $dg + $db
                $maxAbs = [Math]::Max($maxAbs, $pixelMax)
                if ($pixelMax -gt 3) {
                    ++$changed
                }
                ++$sampled
            }
        }

        $meanAbs = if ($sampled -gt 0) { $sumAbs / ($sampled * 3.0) } else { 0.0 }
        $changedRatio = if ($sampled -gt 0) { $changed / [double]$sampled } else { 0.0 }
        [pscustomobject]@{
            status = "compared"
            reason = $null
            before = $beforeMeta
            after = $afterMeta
            sameSha256 = $beforeMeta.sha256 -eq $afterMeta.sha256
            dimensionsMatch = $true
            sampleStep = $step
            sampledPixels = $sampled
            meanAbsRgbDelta = [Math]::Round($meanAbs, 6)
            maxAbsRgbDelta = $maxAbs
            changedSampleRatio = [Math]::Round($changedRatio, 6)
        }
    }
    finally {
        $beforeBitmap.Dispose()
        $afterBitmap.Dispose()
    }
}

$beforeSmoke = Read-SmokeJson -Path $Before
$afterSmoke = Read-SmokeJson -Path $After
$beforeScreenshot = Resolve-SmokeScreenshotPath -Smoke $beforeSmoke
$afterScreenshot = Resolve-SmokeScreenshotPath -Smoke $afterSmoke
$screenshotCompare = Compare-SmokeScreenshots `
    -BeforePath $beforeScreenshot `
    -AfterPath $afterScreenshot `
    -RequestedSampleStep $SampleStep

$failures = @()
if ($FailOnScreenshotDelta) {
    if ($screenshotCompare.status -ne "compared") {
        $failures += "Screenshot comparison status was $($screenshotCompare.status)."
    }
    elseif (-not $screenshotCompare.sameSha256 -and
            ($screenshotCompare.meanAbsRgbDelta -gt $MaxMeanAbsRgbDelta -or
             $screenshotCompare.changedSampleRatio -gt $MaxChangedSampleRatio)) {
        $failures += (
            "Screenshot delta exceeded threshold: meanAbsRgbDelta=$($screenshotCompare.meanAbsRgbDelta) " +
            "(max $MaxMeanAbsRgbDelta), changedSampleRatio=$($screenshotCompare.changedSampleRatio) " +
            "(max $MaxChangedSampleRatio)."
        )
    }
}

$result = [pscustomobject]@{
    schema = "gui-smoke-ab-compare.v1"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    before = [pscustomobject]@{
        jsonPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Before)
        screenshotPath = $beforeScreenshot
    }
    after = [pscustomobject]@{
        jsonPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($After)
        screenshotPath = $afterScreenshot
    }
    thresholds = [pscustomobject]@{
        failOnScreenshotDelta = [bool]$FailOnScreenshotDelta
        maxMeanAbsRgbDelta = $MaxMeanAbsRgbDelta
        maxChangedSampleRatio = $MaxChangedSampleRatio
    }
    screenshot = $screenshotCompare
    fps = [pscustomobject]@{
        visibleBottomLeftGuiFps = New-DeltaObject `
            -BeforeValue (Get-NestedValue $beforeSmoke "playbackFps.visibleBottomLeftGuiFps") `
            -AfterValue (Get-NestedValue $afterSmoke "playbackFps.visibleBottomLeftGuiFps")
        smokePresentedFps = New-DeltaObject `
            -BeforeValue (Get-NestedValue $beforeSmoke "playbackFps.smokePresentedFps") `
            -AfterValue (Get-NestedValue $afterSmoke "playbackFps.smokePresentedFps")
        smokeTimelineFps = New-DeltaObject `
            -BeforeValue (Get-NestedValue $beforeSmoke "playbackFps.smokeTimelineFps") `
            -AfterValue (Get-NestedValue $afterSmoke "playbackFps.smokeTimelineFps")
    }
    quality = [pscustomobject]@{
        beforeMode = Get-NestedValue $beforeSmoke "visualQuality.qualityModeLast"
        afterMode = Get-NestedValue $afterSmoke "visualQuality.qualityModeLast"
        beforeScaleRequest = Get-NestedValue $beforeSmoke "visualQuality.scaleRequestLast"
        afterScaleRequest = Get-NestedValue $afterSmoke "visualQuality.scaleRequestLast"
        beforeScaleActive = Get-NestedValue $beforeSmoke "visualQuality.scaleActiveLast"
        afterScaleActive = Get-NestedValue $afterSmoke "visualQuality.scaleActiveLast"
        beforePipeline = Get-NestedValue $beforeSmoke "visualQuality.glOutputProof.pipelineStatus"
        afterPipeline = Get-NestedValue $afterSmoke "visualQuality.glOutputProof.pipelineStatus"
        beforeColorArtifactVerdict = Get-NestedValue $beforeSmoke "visualQuality.colorArtifactScan.verdict"
        afterColorArtifactVerdict = Get-NestedValue $afterSmoke "visualQuality.colorArtifactScan.verdict"
    }
    failures = $failures
    verdict = if ($failures.Count -eq 0) { "PASS" } else { "FAIL" }
}

$json = $result | ConvertTo-Json -Depth 24
if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $resolvedOutput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
    $outputDir = Split-Path -Parent $resolvedOutput
    if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }
    Set-Content -LiteralPath $resolvedOutput -Value $json -Encoding UTF8
}

Write-Host ((
    "GUI-SMOKE-AB verdict={0} screenshot_status={1} mean_abs_rgb_delta={2} " +
    "changed_sample_ratio={3} gui_fps_delta={4} presented_fps_delta={5} output={6}") -f
    $result.verdict,
    $result.screenshot.status,
    $result.screenshot.meanAbsRgbDelta,
    $result.screenshot.changedSampleRatio,
    $result.fps.visibleBottomLeftGuiFps.delta,
    $result.fps.smokePresentedFps.delta,
    $(if ([string]::IsNullOrWhiteSpace($Output)) { "<stdout-json>" } else { $resolvedOutput })
)

$json

if ($failures.Count -gt 0) {
    exit 1
}
