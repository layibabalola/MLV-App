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
