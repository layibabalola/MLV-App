#!/usr/bin/env pwsh
# owner-of.ps1 -- resolve a repo-relative path to its dual-lane owner: codex | claude | shared | unknown.
# THE single ownership resolver. Every dual-lane guardrail (lane-commit.ps1, the pre-commit lane-guard)
# calls this so there is exactly one source of truth. Fail-closed: an unmapped path returns 'unknown',
# which consumers MUST treat as a hard block (add the path to .dual-lane/ownership.json).
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [string]$MapPath
)
$ErrorActionPreference = 'Stop'
if (-not $MapPath) {
    $root = (& git rev-parse --show-toplevel 2>$null)
    if (-not $root) { $root = (Resolve-Path "$PSScriptRoot\..\..").Path }
    $MapPath = Join-Path $root '.dual-lane/ownership.json'
}
if (-not (Test-Path -LiteralPath $MapPath)) { Write-Output 'unknown'; exit 0 }
$map = Get-Content -LiteralPath $MapPath -Raw | ConvertFrom-Json
$p = ($Path -replace '\\','/') -replace '^\./',''

function Test-LaneGlob([string]$path, [string]$glob) {
    if ($glob.EndsWith('/**')) {
        $pre = $glob.Substring(0, $glob.Length - 3).TrimEnd('/')
        return ($path -eq $pre) -or $path.StartsWith($pre + '/')
    }
    if ($glob.StartsWith('**/')) { return $path -like ('*' + $glob.Substring(2)) }
    if ($glob.Contains('*'))     { return $path -like $glob }
    return $path -eq $glob
}

# 1) exact overrides win
if ($map.overrides.PSObject.Properties.Name -contains $p) { Write-Output ($map.overrides.$p); exit 0 }
# 2) shared
foreach ($g in @($map.shared)) { if (Test-LaneGlob $p $g) { Write-Output 'shared'; exit 0 } }
# 3) lane globs
foreach ($lane in $map.lanes.PSObject.Properties.Name) {
    foreach ($g in @($map.lanes.$lane.globs)) { if (Test-LaneGlob $p $g) { Write-Output $lane; exit 0 } }
}
# 4) fail-closed
Write-Output 'unknown'
exit 0
