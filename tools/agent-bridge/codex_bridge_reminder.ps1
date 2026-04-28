param(
    [string]$WorkspaceRoot = "C:\!Layi Wkspc\MLV-App\.claude\worktrees\festive-boyd-integration",
    [string]$ProjectBucket = "mlv-app",
    [string]$PrivateBucket = "",
    [string]$SessionRegistryPath = "C:\Users\obabalola\.agent-bridge\session.json",
    [string]$BridgeWatchFlagPath = "C:\Users\obabalola\.agent-bridge\bridge_watch_mode.flag",
    [string]$LogPath = "C:\Users\obabalola\.agent-bridge\state\codex-bridge-reminder.log",
    [ValidateSet("response", "final")]
    [string]$HookPhase = "response",
    [switch]$Force,
    [switch]$NoToast
)

$ErrorActionPreference = "Stop"

function Test-IsUnderPath {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Root
    )

    try {
        $resolvedPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
        $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
        return $resolvedPath.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Resolve-ActivePrivateBucket {
    param(
        [string]$RegistryPath,
        [string]$ProjectName,
        [string]$Fallback
    )

    if (-not (Test-Path $RegistryPath)) {
        return $Fallback
    }

    try {
        $registry = Get-Content -Raw $RegistryPath | ConvertFrom-Json
        $project = $registry.projects.$ProjectName
        if ($null -ne $project -and $null -ne $project.active -and $project.active.codex) {
            return [string]$project.active.codex
        }
    } catch {
        # Fall back to the provided default if the registry is unavailable.
    }

    return $Fallback
}

$cwd = (Get-Location).Path
$timestamp = (Get-Date).ToUniversalTime().ToString("o")
$logDir = Split-Path -Parent $LogPath
if ($logDir -and -not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}
"$timestamp invoked cwd=$cwd force=$($Force.IsPresent) noToast=$($NoToast.IsPresent)" | Add-Content -Path $LogPath -Encoding UTF8

if (-not $Force -and -not (Test-IsUnderPath -Path $cwd -Root $WorkspaceRoot)) {
    "$timestamp suppressed reason=outside-workspace workspace=$WorkspaceRoot" | Add-Content -Path $LogPath -Encoding UTF8
    exit 0
}

$resolvedPrivateBucket = Resolve-ActivePrivateBucket -RegistryPath $SessionRegistryPath -ProjectName $ProjectBucket -Fallback $PrivateBucket
$watchModeActive = Test-Path $BridgeWatchFlagPath

$message = "Bridge hygiene: check Codex private bucket $resolvedPrivateBucket and project bucket $ProjectBucket. Continuous monitoring is NOT active unless this thread is currently blocked inside wait_inbox."
Write-Output $message

if ($watchModeActive) {
    Write-Output "BRIDGE-WATCH MODE ACTIVE ($HookPhase reminder only; not hard enforcement)."
    Write-Output "If this turn is an explicit bridge-watch smoke test or a deliberately parked watch session, your last action before yielding should be:"
    Write-Output "  mcp__agent_bridge__wait_inbox(agent=`"codex`", session_ids=[`"$ProjectBucket`",`"$resolvedPrivateBucket`"], timeout_seconds=55, mark_read=false)"
    Write-Output "Do not use a persistent wait_inbox loop in the main working chat unless the user explicitly asked for that short test."
}

"$timestamp reminded phase=$HookPhase project=$ProjectBucket private=$resolvedPrivateBucket watch_mode=$watchModeActive" | Add-Content -Path $LogPath -Encoding UTF8

if ($NoToast) {
    exit 0
}

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $notify.BalloonTipTitle = "Codex bridge reminder"
    if ($watchModeActive) {
        $notify.BalloonTipText = "Bridge-watch mode active. Check private bucket $resolvedPrivateBucket, then re-enter wait_inbox only for explicit bridge-watch tests."
    } else {
        $notify.BalloonTipText = $message
    }
    $notify.Visible = $true
    $notify.ShowBalloonTip(7000)
    Start-Sleep -Seconds 8
    $notify.Dispose()
} catch {
    Write-Output "Bridge reminder toast failed: $($_.Exception.Message)"
}
