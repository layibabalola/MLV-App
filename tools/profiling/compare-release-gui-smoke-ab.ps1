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

function Resolve-ExistingFileBinding {
    param(
        [object]$Path,
        [string]$Label
    )

    if ($null -eq $Path -or [string]::IsNullOrWhiteSpace([string]$Path)) {
        throw "$Label path is missing."
    }
    $unresolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath([string]$Path)
    if (-not (Test-Path -LiteralPath $unresolved -PathType Leaf)) {
        throw "$Label file not found: $unresolved"
    }
    $resolved = (Resolve-Path -LiteralPath $unresolved).Path
    [pscustomobject]@{
        path = $resolved
        sha256 = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash
        length = (Get-Item -LiteralPath $resolved).Length
    }
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

function Get-LaunchArgumentValue {
    param(
        [object]$Smoke,
        [string]$Name
    )

    $arguments = @(Get-NestedValue -Object $Smoke -Path "launch.arguments")
    for ($index = 0; $index -lt $arguments.Count; ++$index) {
        if ([string]$arguments[$index] -eq $Name) {
            if ($index + 1 -ge $arguments.Count) {
                throw "Launch argument $Name has no value."
            }
            return [string]$arguments[$index + 1]
        }
    }
    throw "Launch argument $Name is missing."
}

function Get-GitTreeBinding {
    param(
        [object]$Smoke,
        [string]$BuildSha,
        [string]$Label
    )

    if ($BuildSha -notmatch '^[0-9a-fA-F]{40}$') {
        throw "$Label build SHA is not a full 40-character Git commit: $BuildSha"
    }
    $repoRoot = [string](Get-NestedValue -Object $Smoke -Path "repoRoot")
    if ([string]::IsNullOrWhiteSpace($repoRoot) -or -not (Test-Path -LiteralPath $repoRoot -PathType Container)) {
        throw "$Label repoRoot is missing or unavailable: $repoRoot"
    }
    $treeLines = @(& git -C $repoRoot show -s --format=%T $BuildSha 2>$null)
    $gitExit = $LASTEXITCODE
    $tree = $treeLines | Select-Object -First 1
    if ($gitExit -ne 0 -or [string]$tree -notmatch '^[0-9a-fA-F]{40}$') {
        throw "$Label build commit is unavailable in the recorded repository: $BuildSha"
    }
    [pscustomobject]@{
        commit = $BuildSha.ToLowerInvariant()
        tree = ([string]$tree).Trim().ToLowerInvariant()
    }
}

function Get-LegEvidenceBinding {
    param(
        [string]$Name,
        [object]$Smoke,
        [string]$JsonPath
    )

    $json = Resolve-ExistingFileBinding -Path $JsonPath -Label "$Name smoke JSON"
    $exe = Resolve-ExistingFileBinding `
        -Path (Get-NestedValue -Object $Smoke -Path "exePath") `
        -Label "$Name executable"
    $clip = Resolve-ExistingFileBinding `
        -Path (Get-NestedValue -Object $Smoke -Path "clipPath") `
        -Label "$Name clip"
    $receipt = Resolve-ExistingFileBinding `
        -Path (Get-LaunchArgumentValue -Smoke $Smoke -Name "--receipt") `
        -Label "$Name receipt"
    $log = Resolve-ExistingFileBinding `
        -Path (Get-FirstNestedValue -Object $Smoke -Paths @("log.path", "log.runMetadata.log_file")) `
        -Label "$Name source log"
    $buildSha = [string](Get-NestedValue -Object $Smoke -Path "log.runMetadata.build_sha")
    $git = Get-GitTreeBinding -Smoke $Smoke -BuildSha $buildSha -Label $Name
    $commandLine = @(Get-NestedValue -Object $Smoke -Path "log.runMetadata.command_line")
    if ($commandLine.Count -eq 0 -or [string]::IsNullOrWhiteSpace([string]$commandLine[0])) {
        throw "$Name log metadata does not record the launched executable."
    }
    $resolvedCommandExe = (Resolve-Path -LiteralPath ([string]$commandLine[0])).Path
    if (-not [string]::Equals($resolvedCommandExe, $exe.path, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name executable path does not match log run metadata."
    }
    $launchArguments = @(Get-NestedValue -Object $Smoke -Path "launch.arguments")
    $loggedArguments = @($commandLine | Select-Object -Skip 1)
    if ((Convert-ToStableValueText $launchArguments) -ne (Convert-ToStableValueText $loggedArguments)) {
        throw "$Name launch arguments do not match log run metadata."
    }
    $resolvedInput = (Resolve-Path -LiteralPath (Get-LaunchArgumentValue -Smoke $Smoke -Name "--input")).Path
    if (-not [string]::Equals($resolvedInput, $clip.path, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name clip path does not match its --input argument."
    }

    [pscustomobject]@{
        json = $json
        executable = $exe
        clip = $clip
        receipt = $receipt
        sourceLog = $log
        git = $git
    }
}

function Convert-ToStableValueText {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }
    $Value | ConvertTo-Json -Compress -Depth 16
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

function New-ValueChangeObject {
    param(
        [object]$BeforeValue,
        [object]$AfterValue
    )

    [pscustomobject]@{
        before = $BeforeValue
        after = $AfterValue
        changed = ([string]$BeforeValue) -ne ([string]$AfterValue)
    }
}

function Convert-ToNonNullArray {
    param([object]$Value)

    if ($null -eq $Value) {
        return @()
    }

    @(@($Value) | Where-Object { $null -ne $_ })
}

function New-AutoDecisionComparison {
    param(
        [object]$BeforeSmoke,
        [object]$AfterSmoke
    )

    [pscustomobject]@{
        expectedAuto = New-ValueChangeObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.expectedAuto") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.expectedAuto")
        fieldsPresent = New-ValueChangeObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.fieldsPresent") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.fieldsPresent")
        source = New-ValueChangeObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.source") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.source")
        reason = New-ValueChangeObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.reason") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.reason")
        targetFps = New-DeltaObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.targetFps") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.targetFps")
        averageMs = New-DeltaObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.averageMs") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.averageMs")
        budgetMs = New-DeltaObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.budgetMs") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.budgetMs")
        sampleCount = New-DeltaObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.sampleCount") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.sampleCount")
        averageFpsEquivalent = New-DeltaObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.averageFpsEquivalent") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.averageFpsEquivalent")
        budgetFpsEquivalent = New-DeltaObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.budgetFpsEquivalent") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.budgetFpsEquivalent")
        headroomCapabilityLast = New-ValueChangeObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.headroomCapabilityLastBool") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.headroomCapabilityLastBool")
        validatedNoReadbackCapabilityObserved = New-ValueChangeObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.validatedNoReadbackCapabilityObservedBool") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.validatedNoReadbackCapabilityObservedBool")
        validatedNoReadbackCapabilityDemotedLast = New-ValueChangeObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.validatedNoReadbackCapabilityDemotedLastBool") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.validatedNoReadbackCapabilityDemotedLastBool")
        capabilityConsistent = New-ValueChangeObject `
            -BeforeValue (Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.capabilityConsistent") `
            -AfterValue (Get-NestedValue $AfterSmoke "visualQuality.autoDecision.capabilityConsistent")
        beforeCapabilityFailures = @(Convert-ToNonNullArray (
            Get-NestedValue $BeforeSmoke "visualQuality.autoDecision.capabilityFailures"
        ))
        afterCapabilityFailures = @(Convert-ToNonNullArray (
            Get-NestedValue $AfterSmoke "visualQuality.autoDecision.capabilityFailures"
        ))
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
$beforeJsonPath = (Resolve-Path -LiteralPath $Before).Path
$afterJsonPath = (Resolve-Path -LiteralPath $After).Path
$resolvedOutput = $null
if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $resolvedOutput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
    if ([string]::Equals($resolvedOutput, $beforeJsonPath, [StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($resolvedOutput, $afterJsonPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Output must not overwrite either smoke JSON input."
    }
}
$beforeScreenshot = Resolve-SmokeScreenshotPath -Smoke $beforeSmoke
$afterScreenshot = Resolve-SmokeScreenshotPath -Smoke $afterSmoke
$screenshotCompare = Compare-SmokeScreenshots `
    -BeforePath $beforeScreenshot `
    -AfterPath $afterScreenshot `
    -RequestedSampleStep $SampleStep

$failures = @()
$evidenceBindings = $null
try {
    $beforeBinding = Get-LegEvidenceBinding `
        -Name "before" -Smoke $beforeSmoke -JsonPath $beforeJsonPath
    $afterBinding = Get-LegEvidenceBinding `
        -Name "after" -Smoke $afterSmoke -JsonPath $afterJsonPath
    $evidenceBindings = [pscustomobject]@{
        before = $beforeBinding
        after = $afterBinding
    }
    if ([string]::Equals($beforeBinding.json.path, $afterBinding.json.path, [StringComparison]::OrdinalIgnoreCase)) {
        $failures += "Before and after smoke JSON paths must be distinct."
    }
    if ([string]::Equals($beforeBinding.executable.path, $afterBinding.executable.path, [StringComparison]::OrdinalIgnoreCase)) {
        $failures += "Before and after executables must be distinct files."
    }
    if ($beforeBinding.executable.sha256 -eq $afterBinding.executable.sha256) {
        $failures += "Before and after executables have the same SHA256; same-arm A/B is forbidden."
    }
    if ($beforeBinding.git.commit -eq $afterBinding.git.commit) {
        $failures += "Before and after build commits are identical; same-arm A/B is forbidden."
    }
    if (-not [string]::Equals($beforeBinding.clip.path, $afterBinding.clip.path, [StringComparison]::OrdinalIgnoreCase) -or
        $beforeBinding.clip.sha256 -ne $afterBinding.clip.sha256) {
        $failures += "Before and after legs do not bind the same clip bytes."
    }
    if (-not [string]::Equals($beforeBinding.receipt.path, $afterBinding.receipt.path, [StringComparison]::OrdinalIgnoreCase) -or
        $beforeBinding.receipt.sha256 -ne $afterBinding.receipt.sha256) {
        $failures += "Before and after legs do not bind the same receipt bytes."
    }
}
catch {
    $failures += "Evidence binding failed: $($_.Exception.Message)"
}

$visualStateKeys = @(
    "look_assist_enabled", "temperature", "tint", "raw_black", "raw_white",
    "chroma_smooth", "stretch_x", "stretch_y", "h_stretch_index", "v_stretch_index",
    "dual_iso_mode", "dual_iso_interp", "dual_iso_alias_map", "dual_iso_fullres",
    "drop_frame", "scale_request", "quality_mode", "receipt_supplied"
)
$visualStateEvidence = [ordered]@{}
foreach ($key in $visualStateKeys) {
    $beforeValue = Get-NestedValue $beforeSmoke "visualQuality.visualState.$key"
    $afterValue = Get-NestedValue $afterSmoke "visualQuality.visualState.$key"
    $matches = (
        $null -ne $beforeValue -and
        $null -ne $afterValue -and
        (Convert-ToStableValueText $beforeValue) -eq (Convert-ToStableValueText $afterValue)
    )
    $visualStateEvidence[$key] = [pscustomobject]@{
        before = $beforeValue
        after = $afterValue
        matches = $matches
    }
    if (-not $matches) {
        $failures += "Visual-state mismatch or missing value for $key."
    }
}

foreach ($leg in @(
    [pscustomobject]@{ Name = "before"; Smoke = $beforeSmoke },
    [pscustomobject]@{ Name = "after"; Smoke = $afterSmoke }
)) {
    $colorVerdict = [string](Get-NestedValue $leg.Smoke "visualQuality.colorArtifactScan.verdict")
    if ([string]::IsNullOrWhiteSpace($colorVerdict)) {
        $failures += "$($leg.Name) smoke has no color-artifact verdict."
    }
    elseif ($colorVerdict -in @("suspect-block-or-bar", "scan-error")) {
        $failures += "$($leg.Name) smoke color-artifact verdict is $colorVerdict."
    }
}

$presentedFrameEvidence = [ordered]@{}
foreach ($leg in @(
    [pscustomobject]@{ Name = "before"; Smoke = $beforeSmoke },
    [pscustomobject]@{ Name = "after"; Smoke = $afterSmoke }
)) {
    $validationOk = Get-NestedValue $leg.Smoke "validation.ok"
    $launchOnly = Get-NestedValue $leg.Smoke "validation.launchOnlyProbe"
    $presented = Get-FirstNestedValue $leg.Smoke @(
        "validation.presentedFrames", "log.summary.presented_frames")
    $firstPresented = Get-FirstNestedValue $leg.Smoke @(
        "validation.firstPresentedFrame", "log.summary.first_presented_frame")
    $lastPresented = Get-FirstNestedValue $leg.Smoke @(
        "validation.lastPresentedFrame", "log.summary.last_presented_frame")
    $presentedFrameEvidence[$leg.Name] = [pscustomobject]@{
        presentedFrames = $presented
        firstPresentedFrame = $firstPresented
        lastPresentedFrame = $lastPresented
    }
    if ($validationOk -ne $true) {
        $failures += "$($leg.Name) smoke did not independently pass validation."
    }
    if ($launchOnly -eq $true) {
        $failures += "$($leg.Name) smoke is launch-only and cannot enter a playback A/B."
    }
    if ($null -eq $presented -or [int64]$presented -lt 2) {
        $failures += "$($leg.Name) smoke did not present at least two frames."
    }
    if ($null -eq $firstPresented -or $null -eq $lastPresented -or
        [int64]$firstPresented -eq [int64]$lastPresented) {
        $failures += "$($leg.Name) smoke did not prove displayed-frame advancement."
    }
}
$beforeLastPresented = $presentedFrameEvidence.before.lastPresentedFrame
$afterLastPresented = $presentedFrameEvidence.after.lastPresentedFrame
if ($null -eq $beforeLastPresented -or $null -eq $afterLastPresented -or
    [int64]$beforeLastPresented -ne [int64]$afterLastPresented) {
    $failures += (
        "Screenshot A/B is not frame-locked: before lastPresentedFrame=" +
        "$beforeLastPresented, after lastPresentedFrame=$afterLastPresented."
    )
}
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
    schema = "gui-smoke-ab-compare.v2"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    before = [pscustomobject]@{
        jsonPath = $beforeJsonPath
        screenshotPath = $beforeScreenshot
    }
    after = [pscustomobject]@{
        jsonPath = $afterJsonPath
        screenshotPath = $afterScreenshot
    }
    evidenceBindings = $evidenceBindings
    visualStateEvidence = [pscustomobject]$visualStateEvidence
    thresholds = [pscustomobject]@{
        failOnScreenshotDelta = [bool]$FailOnScreenshotDelta
        maxMeanAbsRgbDelta = $MaxMeanAbsRgbDelta
        maxChangedSampleRatio = $MaxChangedSampleRatio
    }
    screenshot = $screenshotCompare
    presentedFrameEvidence = [pscustomobject]$presentedFrameEvidence
    autoDecision = New-AutoDecisionComparison `
        -BeforeSmoke $beforeSmoke `
        -AfterSmoke $afterSmoke
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
    $outputDir = Split-Path -Parent $resolvedOutput
    if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }
    Set-Content -LiteralPath $resolvedOutput -Value $json -Encoding UTF8
}

Write-Host ((
    "GUI-SMOKE-AB verdict={0} screenshot_status={1} mean_abs_rgb_delta={2} " +
    "changed_sample_ratio={3} gui_fps_delta={4} presented_fps_delta={5} " +
    "auto_reason_before={6} auto_reason_after={7} auto_avg_ms_delta={8} " +
    "auto_avg_fps_eq_delta={9} output={10}") -f
    $result.verdict,
    $result.screenshot.status,
    $result.screenshot.meanAbsRgbDelta,
    $result.screenshot.changedSampleRatio,
    $result.fps.visibleBottomLeftGuiFps.delta,
    $result.fps.smokePresentedFps.delta,
    $result.autoDecision.reason.before,
    $result.autoDecision.reason.after,
    $result.autoDecision.averageMs.delta,
    $result.autoDecision.averageFpsEquivalent.delta,
    $(if ([string]::IsNullOrWhiteSpace($Output)) { "<stdout-json>" } else { $resolvedOutput })
)

$json

if ($failures.Count -gt 0) {
    exit 1
}
