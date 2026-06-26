# heartbeat-check.ps1  (shared by BOTH lanes)
# Prints the other lane's NEW SEQ blocks only when its max SEQ exceeds the caller's marker,
# then advances the marker. Emitting output is what wakes a watching agent, so silence ==
# "nothing new" and the agent stays cheap. -LoopSeconds > 0 makes it a persistent heartbeat
# (e.g. run under a Monitor); 0 = one-shot.
param(
    [Parameter(Mandatory = $true)][string]$OtherLaneFile,
    [Parameter(Mandatory = $true)][string]$MarkerFile,
    [int]$LoopSeconds = 0
)
$ErrorActionPreference = 'Continue'

function Invoke-Check {
    param([string]$Other, [string]$Marker)
    if (-not (Test-Path -LiteralPath $Other)) { return }
    $content = Get-Content -LiteralPath $Other -Raw
    if ([string]::IsNullOrWhiteSpace($content)) { return }
    $seqMatches = [regex]::Matches($content, '(?m)^##\s+SEQ\s+(\d+)\b')
    if ($seqMatches.Count -eq 0) { return }
    $maxSeq = ($seqMatches | ForEach-Object { [int]$_.Groups[1].Value } | Measure-Object -Maximum).Maximum
    $last = -1
    if (Test-Path -LiteralPath $Marker) {
        $raw = (Get-Content -LiteralPath $Marker -Raw).Trim()
        if ($raw -match '^\d+$') { $last = [int]$raw }
    }
    if ($maxSeq -le $last) { return }

    $stamp = (Get-Date).ToUniversalTime().ToString('o')
    Write-Output "DUAL-LANE WAKE @ $stamp : $([IO.Path]::GetFileName($Other)) advanced SEQ $last -> $maxSeq"
    # Emit the new blocks (SEQ > last) so the agent sees them immediately.
    $blocks = [regex]::Split($content, '(?m)(?=^##\s+SEQ\s+\d+\b)')
    foreach ($b in $blocks) {
        $m = [regex]::Match($b, '(?m)^##\s+SEQ\s+(\d+)\b')
        if ($m.Success -and [int]$m.Groups[1].Value -gt $last) { Write-Output ($b.TrimEnd()) }
    }
    $dir = Split-Path -Parent $Marker
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Set-Content -LiteralPath $Marker -Value ([string]$maxSeq) -Encoding ASCII
}

if ($LoopSeconds -le 0) {
    # One-shot diagnostic check: no lock (never blocks a manual check).
    Invoke-Check -Other $OtherLaneFile -Marker $MarkerFile
    exit 0
}

# Persistent (Monitor) mode: SINGLE-INSTANCE guard so duplicate Monitors cannot stack and race the
# shared marker (one consumes the new SEQ + advances the marker, the rest go silent). Lock is keyed
# to this lane's marker, so the two lanes' Monitors never conflict. Stale locks (PID gone) self-heal.
$lock = "$MarkerFile.lock"
if (Test-Path -LiteralPath $lock) {
    $oldPid = ((Get-Content -LiteralPath $lock -Raw -ErrorAction SilentlyContinue) | Out-String).Trim()
    if ($oldPid -match '^\d+$' -and (Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue)) {
        Write-Output "DUAL-LANE HEARTBEAT: another instance (PID $oldPid) already watches $([IO.Path]::GetFileName($MarkerFile)); this one exits (single-instance)."
        exit 0
    }
    # else: stale lock (the recorded PID is gone) -> take it over.
}
Set-Content -LiteralPath $lock -Value ([string]$PID) -Encoding ASCII
try {
    while ($true) {
        Invoke-Check -Other $OtherLaneFile -Marker $MarkerFile
        Start-Sleep -Seconds $LoopSeconds
    }
} finally {
    # Release the lock only if it is still ours (best-effort; a hard kill self-heals via the PID check above).
    if (Test-Path -LiteralPath $lock) {
        $cur = ((Get-Content -LiteralPath $lock -Raw -ErrorAction SilentlyContinue) | Out-String).Trim()
        if ($cur -eq [string]$PID) { Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue }
    }
}
