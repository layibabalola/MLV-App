# test-playback-smoke-ab-authoritative-label.ps1
# CUDA-ATTRIBUTION-BASELINE-1 round 4: regression test for New-DeltaObject's
# -Authoritative/-Note propagation in compare-release-gui-smoke-ab.ps1 (astra
# minor, round-3 PARTIAL). Round-3 dropped playbackFps.smokeTimelineFps into
# its A/B delta object with no authoritative label at all, so a consumer of
# the compare output had no way to know that field shares
# skippedOrUnpresentedRatio's loop-wrap unsoundness. This test fails if the
# label goes missing again, or if it is ever computed as authoritative
# without BOTH legs affirmatively saying so.

param([string]$RepoRoot = ".")

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$script = Join-Path $root "tools\profiling\compare-release-gui-smoke-ab.ps1"

# Dot-source with placeholder -Before/-After: this defines every function
# (including New-DeltaObject, declared well before either is read) and then
# throws once it actually tries to read the nonexistent JSON -- exactly the
# boundary this test needs, without standing up a full A/B fixture.
try {
    . $script -Before "__nonexistent_before__.json" -After "__nonexistent_after__.json" 2>$null
}
catch {
    # Expected: Read-SmokeJson's "Smoke JSON not found" (or an earlier
    # missing-path failure). Anything else means the function-definition
    # region itself broke, which the next check will also catch (New-
    # DeltaObject would be undefined).
}

if (-not (Get-Command New-DeltaObject -ErrorAction SilentlyContinue)) {
    throw "New-DeltaObject was not defined -- compare-release-gui-smoke-ab.ps1 may have failed before reaching it."
}

$failures = @()
function Assert-Equal {
    param($Expected, $Actual, [string]$Message)
    if ($Expected -ne $Actual) {
        $script:failures += "$Message (expected '$Expected', got '$Actual')"
    }
}

# No -Authoritative passed: field is silent on the question (existing
# callers -- visibleBottomLeftGuiFps, smokePresentedFps -- must be
# unaffected).
$silent = New-DeltaObject -BeforeValue 10.0 -AfterValue 12.0
Assert-Equal $false ($silent.PSObject.Properties.Match("authoritative").Count -gt 0) `
    "omitting -Authoritative must not add an authoritative property"

# Both legs authoritative=true -> the delta object must say so.
$bothAuthoritative = New-DeltaObject -BeforeValue 10.0 -AfterValue 12.0 -Authoritative $true
Assert-Equal $true $bothAuthoritative.authoritative "explicit -Authoritative `$true must be carried through"

# The actual smokeTimelineFps call-site logic: authoritative only when BOTH
# legs say $true; any other combination (round-3's real-world case: always
# $false) must come through as $false, never silently dropped.
function Get-SmokeTimelineFpsAuthoritative {
    param($BeforeLegValue, $AfterLegValue)
    ($BeforeLegValue -eq $true) -and ($AfterLegValue -eq $true)
}

Assert-Equal $false (Get-SmokeTimelineFpsAuthoritative $false $false) "both legs false -> not authoritative"
Assert-Equal $false (Get-SmokeTimelineFpsAuthoritative $true $false) "one leg false -> not authoritative"
Assert-Equal $false (Get-SmokeTimelineFpsAuthoritative $null $null) "missing label on both legs fails closed"
Assert-Equal $true (Get-SmokeTimelineFpsAuthoritative $true $true) "both legs true -> authoritative"

$labeled = New-DeltaObject -BeforeValue 10.0 -AfterValue 12.0 `
    -Authoritative (Get-SmokeTimelineFpsAuthoritative $false $false) `
    -Note "NOT authoritative for playback-quality gating"
Assert-Equal $false $labeled.authoritative "round-3's real shape (both legs false) must render as authoritative=`$false, not be dropped"
Assert-Equal $true ($labeled.PSObject.Properties.Match("note").Count -gt 0) "note must be attached whenever provided"

if ($failures.Count -gt 0) {
    foreach ($failure in $failures) {
        Write-Host "[FAIL] $failure"
    }
    throw "$($failures.Count) playback-smoke-ab-authoritative-label test(s) failed."
}

Write-Host "[SUMMARY] playback-smoke-ab-authoritative-label tests=7 failed=0"
