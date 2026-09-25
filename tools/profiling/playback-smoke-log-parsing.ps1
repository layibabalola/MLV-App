# playback-smoke-log-parsing.ps1
# Shared helper: select a single playback_smoke.* qInfo() record out of a run
# log and parse its key=value pairs. Extracted from run-release-gui-smoke.ps1
# (CUDA-ATTRIBUTION-BASELINE-1 round 4) so the selection logic can be
# unit-tested without launching the GUI app -- see
# test-playback-smoke-log-parsing.ps1.

function Select-PlaybackSmokeRecordLine {
    <#
    .SYNOPSIS
    Selects the last log line emitting a given playback_smoke.* field record.

    .DESCRIPTION
    Anchors on " session=" -- the literal key=value token immediately
    following the field name in every actual record emitted by
    MainWindow::finishPlaybackSmokeTelemetry() -- rather than a bare
    "*$FieldName*" wildcard.

    round 4 (astra major, round-3 regression): a bare wildcard also matches
    any OTHER log line whose explanatory population_basis prose happens to
    name this field. playback_smoke.source_frame_population's own prose names
    its companion playback_smoke.frame_population field
    ("...companion playback_smoke.frame_population -- this is the fix
    for..."), and because that line is emitted AFTER frame_population's own
    record, "-Last 1" over a bare wildcard silently selects the PROSE line
    instead of the record -- Convert-PlaybackLogLineToObject then parses the
    wrong key=value pairs and every field the caller asked for (including
    loop_wrap_count) comes back $null.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Lines,
        [Parameter(Mandatory = $true)]
        [string]$FieldName
    )

    $Lines |
        Where-Object { $_ -like "*$FieldName session=*" } |
        Select-Object -Last 1
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

function Get-SourceFrameAttributionValidationFailures {
    <#
    .SYNOPSIS
    Decides whether the source-frame population record proves this run's
    source-frame loss can be trusted -- the three-state contract
    (present-and-sound / present-and-unsound / missing must fail closed
    when telemetry was requested).

    .DESCRIPTION
    CUDA-ATTRIBUTION-BASELINE-1 round 9 (astra MAJOR, "request-based
    accounting fails OPEN when the source-frame population record is
    missing"): run-release-gui-smoke.ps1 used to gate this entire check on
    `$null -ne $SourceFramePopulation`, so a leg whose log never emitted the
    record at all (e.g. a wiring regression that stops calling
    finishPlaybackSmokeTelemetry's source-frame branch) skipped straight
    past both failure branches below and silently fell back to the weaker
    request-based ratio -- astra's repro: 60 offered, 20 presented, 0
    request-based skips, so the fallback ratio reads 0.0 while the missing
    record's true loss was 66.7%. Extracted so both branches AND the new
    missing-record branch are exercised directly by
    test-playback-smoke-log-parsing.ps1 without launching the GUI.

    .PARAMETER SourceFramePopulation
    The parsed playback_smoke.source_frame_population record object, or
    $null if the log never emitted one this session.

    .PARAMETER AttributionMeasured
    Nullable bool: the record's source_frame_attribution_measured field.

    .PARAMETER PartitionSound
    Nullable bool: the record's partition_sound field.

    .PARAMETER FrameTelemetryRequested
    Whether this leg was launched with -FrameTelemetry (the default).
    #>
    param(
        [object]$SourceFramePopulation,
        [Nullable[bool]]$AttributionMeasured,
        [Nullable[bool]]$PartitionSound,
        [bool]$FrameTelemetryRequested
    )

    $failures = @()
    if ($null -ne $SourceFramePopulation) {
        if ($AttributionMeasured -eq $true -and $PartitionSound -ne $true) {
            $failures += "Source-frame population partition was not sound (partition_sound=$PartitionSound); source-frame loss cannot be trusted for this run."
        }
        elseif ($AttributionMeasured -ne $true -and $FrameTelemetryRequested) {
            $failures += "Frame telemetry was requested (-FrameTelemetry) but the source-frame population record reports source_frame_attribution_measured=$AttributionMeasured; source-frame loss cannot be trusted for this run."
        }
    }
    elseif ($FrameTelemetryRequested) {
        $failures += "Frame telemetry was requested (-FrameTelemetry) but the playback summary did not include a source_frame_population record at all; source-frame loss cannot be trusted for this run."
    }
    return $failures
}
