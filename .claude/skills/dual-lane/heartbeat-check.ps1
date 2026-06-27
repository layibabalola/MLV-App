# heartbeat-check.ps1  (shared by BOTH lanes)  -- SELF-HEALING, cursor-based.
#
# Wakes a watching agent when the OTHER lane's ledger has SEQ blocks the THIS lane has not PROCESSED.
# "Processed" = this lane's ledger header `LAST_READ_<OTHER>_SEQ:` (e.g. claude.md `LAST_READ_CODEX_SEQ:`),
# which the AGENT advances only after it actually handles the entries. Emitting output is what wakes the
# agent; silence == nothing unprocessed.
#
# WHY cursor-based (2026-06-27 fix): the old design advanced a script-owned .claude-marker the instant it
# EMITTED, decoupled from whether the agent received the wake. When wake delivery broke (a dead Monitor / a
# path-with-space invocation failure), a polling copy still advanced the marker, so wakes for SEQ 181..190
# were consumed with the agent never woken (it sat idle ~4h until a human nudge). Keying the wake condition
# off the agent's PROCESSED cursor makes a dropped wake SELF-HEAL: it re-fires every poll (full once, then a
# throttled terse re-nudge) until the agent advances its cursor. The script never advances the cursor.
param(
    [Parameter(Mandatory = $true)][string]$OtherLaneFile,            # the other lane's ledger (e.g. codex.md)
    [Parameter(Mandatory = $true)][string]$MarkerFile,              # used ONLY for the single-instance lock path
    [string]$SelfLedgerFile = "",                                  # THIS lane's ledger (e.g. claude.md)
    [string]$CursorKey = "LAST_READ_CODEX_SEQ",                    # header key holding the PROCESSED cursor
    [int]$LoopSeconds = 0,
    [int]$ReNudgeSeconds = 600                                     # re-remind cadence while the cursor stays behind
)
$ErrorActionPreference = 'Continue'

function Get-MaxSeq([string]$file) {
    if (-not (Test-Path -LiteralPath $file)) { return -1 }
    $c = Get-Content -LiteralPath $file -Raw
    if ([string]::IsNullOrWhiteSpace($c)) { return -1 }
    $m = [regex]::Matches($c, '(?m)^##\s+SEQ\s+(\d+)\b')
    if ($m.Count -eq 0) { return -1 }
    return ($m | ForEach-Object { [int]$_.Groups[1].Value } | Measure-Object -Maximum).Maximum
}
function Get-Cursor([string]$ledger, [string]$key, [string]$fallbackMarker) {
    # Authoritative: the agent's processed cursor in its own ledger header.
    if ($ledger -and (Test-Path -LiteralPath $ledger)) {
        foreach ($ln in (Get-Content -LiteralPath $ledger -TotalCount 15 -ErrorAction SilentlyContinue)) {
            if ($ln -match ('^\s*' + [regex]::Escape($key) + '\s*:\s*(\d+)')) { return [int]$Matches[1] }
        }
    }
    # Legacy fallback only (a bare numeric marker file); not advanced by this script anymore.
    if ($fallbackMarker -and (Test-Path -LiteralPath $fallbackMarker)) {
        $raw = (Get-Content -LiteralPath $fallbackMarker -Raw -ErrorAction SilentlyContinue)
        if ($raw) { $raw = $raw.Trim(); if ($raw -match '^\d+$') { return [int]$raw } }
    }
    return -1
}
function Emit-NewBlocks([string]$file, [int]$cursor) {
    $c = Get-Content -LiteralPath $file -Raw
    foreach ($b in [regex]::Split($c, '(?m)(?=^##\s+SEQ\s+\d+\b)')) {
        $m = [regex]::Match($b, '(?m)^##\s+SEQ\s+(\d+)\b')
        if ($m.Success -and [int]$m.Groups[1].Value -gt $cursor) { Write-Output ($b.TrimEnd()) }
    }
}

if ($LoopSeconds -le 0) {
    # One-shot diagnostic check (no lock).
    $maxSeq = Get-MaxSeq $OtherLaneFile
    $cursor = Get-Cursor $SelfLedgerFile $CursorKey $MarkerFile
    if ($maxSeq -gt $cursor) {
        Write-Output ("DUAL-LANE WAKE @ " + (Get-Date).ToUniversalTime().ToString('o') + " : " + [IO.Path]::GetFileName($OtherLaneFile) + " has SEQ $maxSeq > processed cursor $cursor")
        Emit-NewBlocks $OtherLaneFile $cursor
    }
    exit 0
}

# Persistent (Monitor) mode: single-instance lock keyed to this lane's marker (stale PID self-heals).
$lock = "$MarkerFile.lock"
if (Test-Path -LiteralPath $lock) {
    $oldPid = ((Get-Content -LiteralPath $lock -Raw -ErrorAction SilentlyContinue) | Out-String).Trim()
    if ($oldPid -match '^\d+$' -and (Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue)) {
        Write-Output "DUAL-LANE HEARTBEAT: another instance (PID $oldPid) already watches $([IO.Path]::GetFileName($MarkerFile)); this one exits (single-instance)."
        exit 0
    }
}
Set-Content -LiteralPath $lock -Value ([string]$PID) -Encoding ASCII
$lastEmittedMax = -1
$lastEmitEpoch = 0
try {
    while ($true) {
        $maxSeq = Get-MaxSeq $OtherLaneFile
        $cursor = Get-Cursor $SelfLedgerFile $CursorKey $MarkerFile
        if ($maxSeq -gt $cursor) {
            $now = [int][double](Get-Date -UFormat %s)
            if ($maxSeq -ne $lastEmittedMax) {
                Write-Output ("DUAL-LANE WAKE @ " + (Get-Date).ToUniversalTime().ToString('o') + " : " + [IO.Path]::GetFileName($OtherLaneFile) + " advanced to SEQ $maxSeq (processed cursor $cursor)")
                Emit-NewBlocks $OtherLaneFile $cursor
                $lastEmittedMax = $maxSeq; $lastEmitEpoch = $now
            }
            elseif (($now - $lastEmitEpoch) -ge $ReNudgeSeconds) {
                # cursor STILL behind after a full wake -> a dropped wake; re-nudge (terse, self-healing).
                Write-Output ("DUAL-LANE RE-NUDGE @ " + (Get-Date).ToUniversalTime().ToString('o') + " : " + ($maxSeq - $cursor) + " unprocessed Codex SEQ (cursor $cursor < max $maxSeq) -- direct-read codex.md and advance LAST_READ_CODEX_SEQ.")
                $lastEmitEpoch = $now
            }
        }
        else { $lastEmittedMax = -1 }   # caught up; next new SEQ emits full again
        Start-Sleep -Seconds $LoopSeconds
    }
}
finally {
    if (Test-Path -LiteralPath $lock) {
        $cur = ((Get-Content -LiteralPath $lock -Raw -ErrorAction SilentlyContinue) | Out-String).Trim()
        if ($cur -eq [string]$PID) { Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue }
    }
}
