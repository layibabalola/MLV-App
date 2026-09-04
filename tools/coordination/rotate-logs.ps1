# rotate-logs.ps1 -- implements the log clause of COORDINATION-PRUNE-POLICY.md.
#
# TRACKED ON PURPOSE. The first draft of this lived under .claude-state/, which is gitignored:
# a mechanism that enforces a binding policy cannot be unversioned, unreviewed, and absent
# from a fresh clone -- and a TRACKED writer cannot call an untracked helper at all.
#
# The policy says, verbatim:
#   "Logs: rotate at 1 MB, keep 2 rotations, delete older. Applies to
#    gpu-lane-heartbeat.log, codex-delivery-watcher.log, health.log,
#    context-pressure-beacon.log, *.out/.err."
#
# WHY THIS EXISTS: on 2026-09-04 nothing implemented that clause. No rotation code existed in any
# writer and no .log.N file existed on disk, so codex-delivery-watcher.log had reached 30.2 MB and
# health.log 8.3 MB -- 30x and 8x the stated cap. A binding policy with no implementation is a wish,
# and the correct repair is a mechanism, not one operator deleting bytes once.
#
# Rotation is: file -> file.1 -> file.2, and .3 and beyond are DELETED (the policy says delete, not
# archive: these are regenerable volatile artifacts, class P4).
#
# Idempotent and safe to call on every writer invocation: it does nothing at all below the cap.
[CmdletBinding()]
param(
    # Default to the repo's .claude-state, resolved from this script's TRACKED location.
    [string]$Root = (Join-Path (Join-Path $PSScriptRoot '..\..') '.claude-state'),
    [int]$MaxBytes = 1MB,
    [int]$KeepRotations = 2,
    [switch]$WhatIfOnly
)
$ErrorActionPreference = 'Stop'
$names = @('gpu-lane-heartbeat.log','codex-delivery-watcher.log','health.log','context-pressure-beacon.log')
$targets = @()
foreach ($n in $names) {
    $targets += Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $n -ErrorAction SilentlyContinue
}
foreach ($ext in @('*.out','*.err')) {
    $targets += Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $ext -ErrorAction SilentlyContinue
}
$rotated = 0; $freed = 0
foreach ($f in ($targets | Sort-Object FullName -Unique)) {
    if ($f.Length -le $MaxBytes) { continue }
    $base = $f.FullName
    # drop anything beyond the keep window first, oldest-numbered last
    for ($i = 9; $i -ge $KeepRotations; $i--) {
        $old = "$base.$i"
        if (Test-Path -LiteralPath $old) {
            $freed += (Get-Item -LiteralPath $old).Length
            if (-not $WhatIfOnly) { Remove-Item -LiteralPath $old -Force }
        }
    }
    for ($i = $KeepRotations - 1; $i -ge 1; $i--) {
        $src = "$base.$i"; $dst = "$base.$($i + 1)"
        if (Test-Path -LiteralPath $src -PathType Leaf) {
            if (-not $WhatIfOnly) { Move-Item -LiteralPath $src -Destination $dst -Force }
        }
    }
    if (-not $WhatIfOnly) { Move-Item -LiteralPath $base -Destination "$base.1" -Force }
    $rotated++
    Write-Output ("rotated {0} ({1:N1} MB)" -f $f.Name, ($f.Length / 1MB))
}
Write-Output ("rotate-logs: {0} rotated, {1:N1} MB freed from beyond the keep window{2}" -f `
    $rotated, ($freed / 1MB), $(if ($WhatIfOnly) { ' (WhatIfOnly)' } else { '' }))
