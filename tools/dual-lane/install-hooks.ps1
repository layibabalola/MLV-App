#!/usr/bin/env pwsh
# install-hooks.ps1 -- point this repo's git hooks at the version-controlled dual-lane hooks so the
# pre-commit lane-guard survives clone and fires for BOTH lanes.
#
# WARNING: activating this makes EVERY commit in this repo run the lane-guard, which fail-closes when
# env GIT_DUAL_LANE is unset. BOTH lanes must set GIT_DUAL_LANE per session (or commit via lane-commit.ps1)
# BEFORE activation, or routine commits get blocked. Coordinate via the dual-lane ledger first, then -Activate.
[CmdletBinding()]
param([switch]$Activate, [switch]$Deactivate)
$ErrorActionPreference = 'Stop'
$root = (& git rev-parse --show-toplevel).Trim()
if ($Deactivate) {
    & git -C $root config --unset core.hooksPath 2>$null
    Write-Host "core.hooksPath unset (default .git/hooks restored)."
    exit 0
}
if ($Activate) {
    & git -C $root config core.hooksPath 'tools/dual-lane/hooks'
    Write-Host "core.hooksPath=tools/dual-lane/hooks -- pre-commit lane-guard ACTIVE for both lanes."
    Write-Host "Each session MUST set `$env:GIT_DUAL_LANE='claude'|'codex' or commit via lane-commit.ps1."
    exit 0
}
Write-Host "Usage: install-hooks.ps1 -Activate | -Deactivate"
Write-Host ("Current core.hooksPath: " + (& git -C $root config core.hooksPath))
