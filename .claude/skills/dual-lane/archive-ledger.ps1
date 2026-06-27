# archive-ledger.ps1  (shared by BOTH lanes)
# Keeps a dual-lane coordination ledger SMALL by moving old, fully-settled SEQ blocks into
# archive/<lane>-<UTCdate>.md. CONSERVATIVE + SAFE:
#   - archives a block ONLY if it is RESOLVED, AND its SEQ <= the OTHER lane's cursor (they have
#     read it), AND it is older than the -KeepRecent most-recent SEQs.
#   - NEVER archives an OPEN/ACKED block, the most-recent window, or anything the other lane has not
#     yet read. Never touches the other lane's file.
#   - -DryRun (default) only reports. -Apply backs up the original, appends to the archive, and
#     rewrites the live file (header + a pointer line + kept blocks).
# You only ever run this against YOUR OWN lane file.
param(
    [Parameter(Mandatory = $true)][string]$LaneFile,        # your file, e.g. claude.md
    [Parameter(Mandatory = $true)][string]$OtherLaneFile,   # the other lane file, e.g. codex.md (read its cursor)
    [int]$KeepRecent = 15,                                   # always keep this many most-recent SEQ blocks
    [switch]$Apply                                           # default is dry-run
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $LaneFile))      { Write-Host "no lane file: $LaneFile" -ForegroundColor Red; exit 2 }
if (-not (Test-Path -LiteralPath $OtherLaneFile)) { Write-Host "no other lane file: $OtherLaneFile" -ForegroundColor Red; exit 2 }

$laneName = [IO.Path]::GetFileNameWithoutExtension($LaneFile)            # 'claude' or 'codex'
$cursorKey = if ($laneName -eq 'claude') { 'LAST_READ_CLAUDE_SEQ' } else { 'LAST_READ_CODEX_SEQ' }

$content = Get-Content -LiteralPath $LaneFile -Raw
$other   = Get-Content -LiteralPath $OtherLaneFile -Raw
$otherCursor = 0
$m = [regex]::Match($other, "(?m)^\s*$cursorKey\s*:\s*(\d+)")
if ($m.Success) { $otherCursor = [int]$m.Groups[1].Value }

# Split off the header (everything before the first "## SEQ") and the SEQ blocks.
$firstSeq = [regex]::Match($content, '(?m)^##\s+SEQ\s+\d+\b')
if (-not $firstSeq.Success) { Write-Host "no SEQ blocks in $LaneFile"; exit 0 }
$header = $content.Substring(0, $firstSeq.Index).TrimEnd()
$rest   = $content.Substring($firstSeq.Index)
$blocks = [regex]::Split($rest, '(?m)(?=^##\s+SEQ\s+\d+\b)') | Where-Object { $_.Trim().Length -gt 0 }

# Informational types (STATUS/HEARTBEAT/ACK) are logs: archivable once the other lane has read them,
# even if left OPEN. Actionable types (HANDOFF/REVIEW/QUESTION/BLOCKER) require RESOLVED first.
$informationalTypes = @('STATUS','HEARTBEAT','ACK','OBSERVATION')
$parsed = foreach ($b in $blocks) {
    $sm = [regex]::Match($b, '(?m)^##\s+SEQ\s+(\d+)\s*\|\s*([A-Z_]+)')
    if (-not $sm.Success) { continue }
    $seq = [int]$sm.Groups[1].Value
    $type = $sm.Groups[2].Value
    $resolved = [regex]::IsMatch($b, '(?m)^status:\s*RESOLVED\s*$')
    $informational = $informationalTypes -contains $type
    [pscustomobject]@{ seq = $seq; type = $type; resolved = $resolved; informational = $informational; text = $b.TrimEnd() }
}
$maxSeq = ($parsed | Measure-Object -Property seq -Maximum).Maximum
$recentFloor = $maxSeq - $KeepRecent     # keep blocks with seq > recentFloor

# Archive: read by the other lane AND older than the recent window AND (informational OR resolved).
$toArchive = $parsed | Where-Object { ($_.seq -le $otherCursor) -and ($_.seq -le $recentFloor) -and ($_.informational -or $_.resolved) } | Sort-Object seq
$toKeep    = $parsed | Where-Object { $toArchive -notcontains $_ } | Sort-Object seq

Write-Host ("Lane=$laneName  blocks=$($parsed.Count)  maxSeq=$maxSeq  otherCursor=$otherCursor  keepRecent=$KeepRecent")
if (-not $toArchive -or $toArchive.Count -eq 0) {
    Write-Host "Nothing to archive (no RESOLVED+acked blocks older than the keep-recent window)." -ForegroundColor Green
    exit 0
}
$aMin = ($toArchive | Select-Object -First 1).seq
$aMax = ($toArchive | Select-Object -Last 1).seq
Write-Host ("WOULD ARCHIVE $($toArchive.Count) blocks: SEQ $aMin..$aMax  ->  keep $($toKeep.Count) live (SEQ $((($toKeep|Select-Object -First 1).seq))..$maxSeq)") -ForegroundColor Yellow
if (-not $Apply) { Write-Host "DRY-RUN. Re-run with -Apply to perform it." -ForegroundColor Cyan; exit 0 }

$dir = Split-Path -Parent $LaneFile
$archDir = Join-Path $dir 'archive'
New-Item -ItemType Directory -Force -Path $archDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
# 1) back up the original (safety net)
Copy-Item -LiteralPath $LaneFile -Destination (Join-Path $archDir "$laneName-backup-$stamp.md") -Force
# 2) append archived blocks
$archFile = Join-Path $archDir ("$laneName-" + (Get-Date).ToUniversalTime().ToString('yyyyMMdd') + ".md")
$archBody = "`n# archived $stamp : SEQ $aMin..$aMax (RESOLVED + read by other lane)`n`n" + (($toArchive | ForEach-Object { $_.text }) -join "`n`n") + "`n"
Add-Content -LiteralPath $archFile -Value $archBody -Encoding UTF8
# 3) rewrite the live file: header + pointer + kept blocks
$pointer = "`n# (archived SEQ $aMin..$aMax -> archive/$([IO.Path]::GetFileName($archFile)) on $stamp)`n"
$live = $header + "`n" + $pointer + "`n" + (($toKeep | ForEach-Object { $_.text }) -join "`n`n") + "`n"
Set-Content -LiteralPath $LaneFile -Value $live -Encoding UTF8
Write-Host ("APPLIED: archived SEQ $aMin..$aMax to $archFile ; live file now $($toKeep.Count) blocks. Backup in archive/.") -ForegroundColor Green
