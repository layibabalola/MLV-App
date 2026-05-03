# wake_claude.ps1
# Fail-closed diagnostic placeholder for Claude Desktop wake parity.
#
# Codex wake can be targeted with a recorded Desktop thread id plus
# codex://threads/<id>. Claude Desktop has claude:// routes, but Agent Bridge
# does not yet have a verified bridge-session -> Claude conversation id mapping
# plus post-deeplink DOM certification. Empirically, claude://claude.ai/new
# navigates the user's current Claude Desktop window rather than opening an
# isolated target. Without a non-disruptive target proof, typing
# "check bridge inbox" would reintroduce the same wrong-chat class we removed
# from the Codex side. This script therefore refuses UI injection by default and
# exists so watcher/config/docs have one explicit, testable boundary.
#
# Exit codes:
#   0  = diagnostic/find-only completed.
#   20 = unsupported: no verified thread-addressable Claude wake target.

param(
    [string]$Message = "check bridge inbox",
    [string]$SessionId = "",
    [string]$Project = "",
    [string]$StateDir = "",
    [switch]$FindOnly,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$UnsupportedExitCode = 20

$defaultUserProfile = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath("UserProfile") }
$defaultBridgeRoot = if ($env:AGENT_BRIDGE_ROOT) {
    [System.Environment]::ExpandEnvironmentVariables($env:AGENT_BRIDGE_ROOT)
} else {
    Join-Path $defaultUserProfile ".agent-bridge"
}
if (-not $StateDir) {
    $StateDir = Join-Path $defaultBridgeRoot "state"
}

function Get-ClaudeDesktopWindows {
    Get-Process -Name "claude" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.MainWindowHandle -ne 0 -and
            $_.Path -and
            $_.Path -like "*AnthropicClaude*"
        } |
        Select-Object ProcessName, Id, MainWindowTitle, MainWindowHandle, Path
}

$windows = @(Get-ClaudeDesktopWindows)
$payload = [ordered]@{
    ok = $false
    status = "unsupported_thread_addressable_wake"
    reason = "Claude Desktop routes are current-window navigation until Agent Bridge maps and verifies a specific conversation target; refusing SendKeys."
    message = $Message
    session_id = $SessionId
    project = $Project
    state_dir = $StateDir
    windows = $windows
    safe_default = "Use the in-process Claude Monitor; restart/confirm it after compaction or session rollover."
}

if ($FindOnly -or $DryRun) {
    $payload.ok = $true
    $payload.status = "diagnostic_only"
    $payload.reason = "FindOnly/DryRun requested; no wake attempted."
    $payload | ConvertTo-Json -Depth 5
    exit 0
}

$payload | ConvertTo-Json -Depth 5
exit $UnsupportedExitCode
