<#
.SYNOPSIS
    Pure helpers for Invoke-WorkstreamLoop.ps1's -Install argument line, dot-sourced so a test can
    call them directly without executing the loop (which syncs a real git worktree and would mutate
    live board state - the same reason the loop's cycle body is tested by source assertion, not by
    running it).

.DESCRIPTION
    UNTIL NOW -Install PERSISTED ONLY FOUR OF THE LOOP'S OWN PARAMETERS. -Tracks, -Lane and
    -AllowEdits were silently dropped from the registered scheduled task's argument line, so a
    reinstall silently reset every one of them to its default - the exact class of defect that made
    the 2026-09-03 budget-flag omission (see Invoke-WorkstreamLoop.ps1's own -Install comment) an
    emergency rather than a one-line fix.

    A `pwsh -File` scheduled-task action passes its Arguments as a literal string, so an array
    parameter like -Tracks cannot round-trip through it directly - Get-InstallArgLine serialises it
    as ONE comma-joined -Tracks value, and Resolve-Tracks is what the loop itself calls, immediately
    after binding $Tracks, to turn a single comma-joined string back into an array. Both directions
    live here so they cannot drift apart.

    NO Set-StrictMode HERE - dot-sourced, so it would change the caller's semantics (landing-probe.ps1
    already paid for this lesson).
#>

function Get-InstallArgLine {
    param(
        [Parameter(Mandatory)][string]$ScriptPath,
        [Parameter(Mandatory)][int]$DailyBudget,
        [Parameter(Mandatory)][int]$MaxDispatchesPerCycle,
        [Parameter(Mandatory)][int]$TimeoutSec,
        [Parameter(Mandatory)][int]$StaleHours,
        [Parameter(Mandatory)][string[]]$Tracks,
        [string]$Lane = '',
        [switch]$AllowEdits
    )
    $tracksArg = ($Tracks -join ',')
    $line = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" ' +
             '-DailyBudget {1} -MaxDispatchesPerCycle {2} -TimeoutSec {3} -StaleHours {4} ' +
             '-Tracks "{5}"') -f $ScriptPath, $DailyBudget, $MaxDispatchesPerCycle, $TimeoutSec, $StaleHours, $tracksArg
    if ($Lane) { $line += " -Lane $Lane" }
    if ($AllowEdits) { $line += ' -AllowEdits' }
    return $line
}

function Resolve-Tracks {
    # A pwsh -File action hands back -Tracks as ONE literal string, so a comma-joined value
    # arrives as a single-element array. Only split when it looks like exactly that shape - a
    # genuinely single track name must never be split on some other character it happens to
    # contain.
    param([Parameter(Mandatory)][string[]]$Tracks)
    if ($Tracks.Count -eq 1 -and $Tracks[0] -match ',') {
        return @($Tracks[0] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }
    return $Tracks
}
