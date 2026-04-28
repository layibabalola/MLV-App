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
    "-ProjectBucket",
    "mlv-app",
    "-PrivateBucket",
    "9111dce5-3d33-4d06-b7a7-87dbf259b0c6"
)

if ($NoToast) {
    $args += "-NoToast"
}

& powershell.exe @args
