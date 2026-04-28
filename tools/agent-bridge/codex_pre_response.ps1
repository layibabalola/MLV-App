param(
    [string]$RepoRoot = ".",
    [switch]$NoToast
)

$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $RepoRoot))
$script = Join-Path $root "tools\agent-bridge\codex_bridge_reminder.ps1"
if (-not (Test-Path $script)) {
    throw "Bridge reminder script not found: $script"
}

$args = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $script,
    "-WorkspaceRoot",
    $root,
    "-HookPhase",
    "response",
    "-ProjectBucket",
    "mlv-app"
)

if ($NoToast) {
    $args += "-NoToast"
}

& powershell.exe @args
