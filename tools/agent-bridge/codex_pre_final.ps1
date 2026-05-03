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

function Assert-BridgeCliContract {
    param([Parameter(Mandatory=$true)][string]$Root)

    $cli = Join-Path $Root "tools\agent-bridge\agent_bridge.py"
    if (-not (Test-Path $cli)) {
        throw "Bridge CLI not found: $cli"
    }

    $probe = & py -3 $cli check-inbox --help 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($probe | Out-String)
    if ($exitCode -ne 0 -or $text -notmatch "--agent" -or $text -notmatch "--format") {
        throw ("Bridge CLI contract failed: check-inbox help did not expose the expected JSON hygiene command. exit=" + $exitCode + " output=" + $text.Trim())
    }
}

Assert-BridgeCliContract -Root $root

$args = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $script,
    "-WorkspaceRoot",
    $root,
    "-HookPhase",
    "final",
    "-ProjectBucket",
    "mlv-app"
)

if ($NoToast) {
    $args += "-NoToast"
}

& powershell.exe @args
