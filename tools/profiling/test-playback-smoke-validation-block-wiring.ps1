# test-playback-smoke-validation-block-wiring.ps1
# CUDA-ATTRIBUTION-BASELINE-1 round 10 (sol + fable MAJOR, round 9): the
# round-9 fail-closed helper (Get-SourceFrameAttributionValidationFailures,
# playback-smoke-log-parsing.ps1) is unit-tested in isolation by
# test-playback-smoke-log-parsing.ps1, which calls the helper with its own
# hand-picked arguments -- it stays green even if
# run-release-gui-smoke.ps1's production call site is detached. Three
# one-line mutations at that call site (run-release-gui-smoke.ps1:1646-1650)
# silently restore round-9's fail-open bug while every existing test stays
# green: hardcoding -FrameTelemetryRequested $false, deleting the
# `$validationFailures +=` statement, and discarding the result into
# $ignored instead of appending it.
#
# This test EXTRACTS the actual `if (-not $LaunchOnlyProbe) { ... }`
# validation block's text straight out of the live run-release-gui-smoke.ps1
# file -- not a hand-copied reimplementation -- and EXECUTES it in a
# controlled scope standing in for a FrameTelemetry-requested leg whose
# source_frame_population record is missing. A real edit to that production
# block is therefore what this test runs on every invocation, and each of
# the three mutations above is reproduced in-memory to prove the checker
# actually catches that shape of regression (not merely that it currently
# passes).

param([string]$RepoRoot = ".")

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$runnerPath = Join-Path $root "tools\profiling\run-release-gui-smoke.ps1"
. (Join-Path $root "tools\profiling\playback-smoke-log-parsing.ps1")

$failures = @()

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        $script:failures += $Message
    }
}

function Get-BalancedBraceBlock {
    <#
    .SYNOPSIS
    Returns the substring of $Source starting at the "{" that follows the
    unique match of $MarkerRegex, through its matching closing "}"
    (inclusive of both braces).

    .DESCRIPTION
    Brace characters inside a double-quoted string literal (e.g. a -f format
    token like "{0:P2}") do not affect the depth count -- this walks the
    text tracking whether it is inside a double-quoted string (honoring
    PowerShell's backtick escape) so such tokens can never desynchronize the
    match, whether or not they happen to be brace-balanced themselves.
    #>
    param([string]$Source, [string]$MarkerRegex)

    $markerMatches = [regex]::Matches($Source, $MarkerRegex)
    if ($markerMatches.Count -ne 1) {
        throw "expected exactly one match for marker '$MarkerRegex', found $($markerMatches.Count)"
    }
    $openBrace = $Source.IndexOf("{", $markerMatches[0].Index)
    if ($openBrace -lt 0) {
        throw "no opening brace found after marker '$MarkerRegex'"
    }

    $depth = 0
    $inString = $false
    $i = $openBrace
    while ($i -lt $Source.Length) {
        $ch = $Source[$i]
        if ($inString) {
            if ($ch -eq '`') { $i += 2; continue }
            if ($ch -eq '"') {
                if ($i + 1 -lt $Source.Length -and $Source[$i + 1] -eq '"') { $i += 2; continue }
                $inString = $false
            }
        }
        else {
            if ($ch -eq '"') { $inString = $true }
            elseif ($ch -eq '{') { $depth++ }
            elseif ($ch -eq '}') {
                $depth--
                if ($depth -eq 0) {
                    return $Source.Substring($openBrace, $i - $openBrace + 1)
                }
            }
        }
        $i++
    }
    throw "unbalanced braces starting at offset ${openBrace}: never returned to depth 0"
}

function Invoke-SourceFrameValidationBlock {
    <#
    Executes $BlockText (real or mutated production text) in this function's
    scope after seeding it with the exact script-scope variable names the
    live `if (-not $LaunchOnlyProbe) { ... }` block
    (run-release-gui-smoke.ps1:1605-1658) reads, then returns the resulting
    $validationFailures array. These are not renamed stand-ins -- they are
    the identifiers the extracted text itself references.
    #>
    param(
        [string]$BlockText,
        [bool]$LaunchOnlyProbe,
        [object]$PresentedFrames,
        [object]$FirstPresentedFrame,
        [object]$LastPresentedFrame,
        [object]$LoopWrapCount,
        [object]$SourceFramePopulation,
        [object]$AttributionMeasuredValue,
        [object]$PartitionSoundValue,
        [bool]$FrameTelemetryValue,
        [object]$SkippedOrUnpresentedRatioForGate,
        [double]$MaxSkippedOrUnpresentedRatioValue
    )

    $validationFailures = @()
    $presentedFrames = $PresentedFrames
    $firstPresentedFrame = $FirstPresentedFrame
    $lastPresentedFrame = $LastPresentedFrame
    $loopWrapCount = $LoopWrapCount
    $sourceFramePopulation = $SourceFramePopulation
    $sourceFrameAttributionMeasured = $AttributionMeasuredValue
    $sourceFramePopulationSound = $PartitionSoundValue
    $FrameTelemetry = $FrameTelemetryValue
    $skippedOrUnpresentedRatioForGate = $SkippedOrUnpresentedRatioForGate
    $MaxSkippedOrUnpresentedRatio = $MaxSkippedOrUnpresentedRatioValue

    # $BlockText is "{ ... }" (the extracted braces are inclusive); a bare
    # Invoke-Expression on that text would just construct and return a
    # scriptblock LITERAL without running its body. Prefixing ". " makes it
    # a dot-source of an inline scriptblock, which runs the body in THIS
    # function's scope so `$validationFailures += ...` mutates the local
    # variable seeded above.
    Invoke-Expression (". " + $BlockText)
    return @($validationFailures)
}

$runnerSource = Get-Content -LiteralPath $runnerPath -Raw
$blockMarkerRegex = [regex]::Escape('if (-not $LaunchOnlyProbe) {')
$block = Get-BalancedBraceBlock -Source $runnerSource -MarkerRegex $blockMarkerRegex

Assert-True ($block -like "*Get-SourceFrameAttributionValidationFailures*") `
    "extracted block sanity: expected the source-frame attribution helper call inside the extracted 'if (-not `$LaunchOnlyProbe)' text"

# A FrameTelemetry-requested leg whose source_frame_population record never
# showed up (astra round-9's exact repro shape). presented/first/last/wrap
# are all set to values that pass cleanly so the assertions below isolate
# the source-frame-record branch, not some other unrelated failure.
$commonArgs = @{
    LaunchOnlyProbe = $false
    PresentedFrames = 20
    FirstPresentedFrame = 0
    LastPresentedFrame = 19
    LoopWrapCount = 0
    SourceFramePopulation = $null
    AttributionMeasuredValue = $null
    PartitionSoundValue = $null
    FrameTelemetryValue = $true
    SkippedOrUnpresentedRatioForGate = 0.0
    MaxSkippedOrUnpresentedRatioValue = 0.5
}

# --- Baseline: the REAL, unmutated production block -------------------------
$realResult = @(Invoke-SourceFrameValidationBlock -BlockText $block @commonArgs)
Assert-True ($realResult.Count -ge 1) `
    "production validation block must fail closed for a MISSING source_frame_population record when FrameTelemetry was requested (got $($realResult.Count) failures)"
if ($realResult.Count -ge 1) {
    Assert-True (($realResult -join "|") -like "*did not include a source_frame_population record at all*") `
        "production validation block's failure message must name the missing record; got: $($realResult -join '; ')"
}

# --- Mutation 1: hardcode -FrameTelemetryRequested $false -------------------
$mutation1 = $block -replace [regex]::Escape('-FrameTelemetryRequested $FrameTelemetry'), '-FrameTelemetryRequested $false'
Assert-True ($mutation1 -ne $block) `
    "mutation 1 fixture text did not match anything in the extracted block -- update this test's mutation string"
$mutation1Result = @(Invoke-SourceFrameValidationBlock -BlockText $mutation1 @commonArgs)
Assert-True ($mutation1Result.Count -eq 0) `
    "mutation 1 (-FrameTelemetryRequested `$false) must silently restore the round-9 fail-open bug for this test's checker to be meaningful; got $($mutation1Result.Count) failures"

# --- Mutations 2 and 3: detach the helper's result from `$validationFailures`
$callSiteRegex = [regex]::new(
    '\$validationFailures\s*\+=\s*Get-SourceFrameAttributionValidationFailures\s*`?\s*' +
    '-SourceFramePopulation\s+\$sourceFramePopulation\s*`?\s*' +
    '-AttributionMeasured\s+\$sourceFrameAttributionMeasured\s*`?\s*' +
    '-PartitionSound\s+\$sourceFramePopulationSound\s*`?\s*' +
    '-FrameTelemetryRequested\s+\$FrameTelemetry',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
$callSiteMatches = $callSiteRegex.Matches($block)
Assert-True ($callSiteMatches.Count -eq 1) `
    "expected exactly one source-frame attribution call site in the extracted block, found $($callSiteMatches.Count)"

if ($callSiteMatches.Count -eq 1) {
    $callSiteText = $callSiteMatches[0].Value

    # Mutation 2: delete the `$validationFailures +=` statement entirely.
    $mutation2 = $block.Replace($callSiteText, "")
    Assert-True ($mutation2 -ne $block) `
        "mutation 2 fixture text did not match anything in the extracted block -- update this test's mutation string"
    $mutation2Result = @(Invoke-SourceFrameValidationBlock -BlockText $mutation2 @commonArgs)
    Assert-True ($mutation2Result.Count -eq 0) `
        "mutation 2 (deleted '`$validationFailures +=' statement) must silently restore the round-9 fail-open bug for this test's checker to be meaningful; got $($mutation2Result.Count) failures"

    # Mutation 3: discard the helper's result into $ignored instead of
    # appending it to $validationFailures.
    $discardedCallSiteText = $callSiteText -replace '\$validationFailures\s*\+=', '$ignored ='
    $mutation3 = $block.Replace($callSiteText, $discardedCallSiteText)
    Assert-True ($mutation3 -ne $block) `
        "mutation 3 fixture text did not match anything in the extracted block -- update this test's mutation string"
    $mutation3Result = @(Invoke-SourceFrameValidationBlock -BlockText $mutation3 @commonArgs)
    Assert-True ($mutation3Result.Count -eq 0) `
        "mutation 3 (result discarded into `$ignored) must silently restore the round-9 fail-open bug for this test's checker to be meaningful; got $($mutation3Result.Count) failures"
}

if ($failures.Count -gt 0) {
    foreach ($failure in $failures) {
        Write-Host "[FAIL] $failure"
    }
    throw "$($failures.Count) playback-smoke-validation-block-wiring test(s) failed."
}

Write-Host "[SUMMARY] playback-smoke-validation-block-wiring tests=10 failed=0"
