param(
    [string]$WorkspaceRoot = "",
    [string]$ProjectBucket = "mlv-app",
    [string]$PrivateBucket = "",
    [string]$SessionRegistryPath = "",
    [string]$WatcherConfigPath = "",
    [string]$WatcherPidPath = "",
    [string]$BridgeWatchFlagPath = "",
    [string]$SettingsPath = "",
    [string]$LogPath = "",
    [ValidateSet("response", "final")]
    [string]$HookPhase = "response",
    [switch]$Force,
    [switch]$NoToast
)

$ErrorActionPreference = "Stop"

$userProfile = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath("UserProfile") }
$bridgeRoot = Join-Path $userProfile ".agent-bridge"
if (-not $WorkspaceRoot) {
    $WorkspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
}
if (-not $SessionRegistryPath) {
    $SessionRegistryPath = Join-Path $bridgeRoot "session.json"
}
if (-not $WatcherConfigPath) {
    $WatcherConfigPath = Join-Path $bridgeRoot "watcher-config.json"
}
if (-not $WatcherPidPath) {
    $WatcherPidPath = Join-Path $bridgeRoot "watcher.pid"
}
if (-not $BridgeWatchFlagPath) {
    $BridgeWatchFlagPath = Join-Path $bridgeRoot "bridge_watch_mode.flag"
}
if (-not $SettingsPath) {
    $SettingsPath = Join-Path $bridgeRoot "settings.json"
}
if (-not $LogPath) {
    $LogPath = Join-Path $bridgeRoot "state\codex-bridge-reminder.log"
}

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

function Get-ReminderToastsEnabled {
    param(
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $false
    }

    try {
        $settings = Get-Content -Raw $Path | ConvertFrom-Json
        if ($null -ne $settings.codex_bridge_reminder_toasts_enabled) {
            return [bool]$settings.codex_bridge_reminder_toasts_enabled
        }
    } catch {
        return $false
    }

    return $false
}

function Read-JsonObject {
    param(
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    try {
        return Get-Content -Raw $Path | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-WatcherRunning {
    param(
        [string]$PidPath
    )

    if (-not (Test-Path $PidPath)) {
        return $false
    }

    try {
        $pidValue = [int](Get-Content -Raw $PidPath).Trim()
        $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        return $null -ne $proc
    } catch {
        return $false
    }
}

function Resolve-BridgeRoot {
    param(
        [string[]]$CandidatePaths,
        [string]$Fallback
    )

    foreach ($candidate in $CandidatePaths) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        $parent = Split-Path -Parent $candidate
        if (-not [string]::IsNullOrWhiteSpace($parent)) {
            return $parent
        }
    }

    return $Fallback
}

function Get-HeuristicsDigest {
    param(
        [string]$WorkspaceRoot
    )

    $heuristicsPath = Join-Path $WorkspaceRoot "bridge_trigger_heuristics.md"
    if (-not (Test-Path $heuristicsPath)) {
        return "rules=missing"
    }

    $version = $null
    try {
        $version = (git -C $WorkspaceRoot log -1 --format=%h -- bridge_trigger_heuristics.md 2>$null | Select-Object -First 1).Trim()
    } catch {
        $version = $null
    }
    if ([string]::IsNullOrWhiteSpace($version)) {
        try {
            $version = (Get-Item $heuristicsPath).LastWriteTimeUtc.ToString("yyyyMMddTHHmmssZ")
        } catch {
            $version = "unknown"
        }
    }

    return "rules=$version active=inbox_end+ledger_every_turn"
}

function Get-NextPendingBridgeActionDigest {
    param(
        [string]$StateDir,
        [string]$OwnerAgent = "codex"
    )

    $path = Join-Path $StateDir "pending-actions.json"
    if (-not (Test-Path $path)) {
        return "ledger=empty"
    }

    try {
        $payload = Get-Content -Raw $path | ConvertFrom-Json
    } catch {
        return "ledger=unreadable"
    }

    $priorityOrder = @{
        urgent = 0
        high = 1
        normal = 2
        low = 3
    }

    $pending = @()
    foreach ($action in @($payload.actions)) {
        if ($null -eq $action) {
            continue
        }
        if ([string]$action.owner_agent -ne $OwnerAgent) {
            continue
        }
        if ([string]$action.status -ne "pending") {
            continue
        }

        $priority = [string]$action.priority
        if (-not $priorityOrder.ContainsKey($priority)) {
            $priority = "normal"
        }

        $dueBucket = 1
        $dueValue = [datetimeoffset]::MaxValue
        if (-not [string]::IsNullOrWhiteSpace([string]$action.due_at)) {
            try {
                $dueValue = [datetimeoffset]::Parse([string]$action.due_at)
                $dueBucket = 0
            } catch {
                $dueValue = [datetimeoffset]::MaxValue
                $dueBucket = 1
            }
        }

        $createdValue = [datetimeoffset]::MaxValue
        if (-not [string]::IsNullOrWhiteSpace([string]$action.created_at)) {
            try {
                $createdValue = [datetimeoffset]::Parse([string]$action.created_at)
            } catch {
                $createdValue = [datetimeoffset]::MaxValue
            }
        }

        $pending += [pscustomobject]@{
            action = $action
            priorityRank = $priorityOrder[$priority]
            dueBucket = $dueBucket
            dueValue = $dueValue
            createdValue = $createdValue
        }
    }

    if ($pending.Count -eq 0) {
        return "ledger=empty"
    }

    $top = $pending |
        Sort-Object priorityRank, dueBucket, dueValue, createdValue |
        Select-Object -First 1

    $summary = [string]$top.action.summary
    if ($summary.Length -gt 72) {
        $summary = $summary.Substring(0, 69) + "..."
    }
    $priority = [string]$top.action.priority
    $actionId = [string]$top.action.id
    return "ledger_top=$priority $actionId $summary"
}

function Get-BridgeRuntimeState {
    param(
        [string]$RegistryPath,
        [string]$WatcherConfigPath,
        [string]$WatcherPidPath,
        [string]$ProjectName,
        [string]$PrivateSession
    )

    if ([string]::IsNullOrWhiteSpace($PrivateSession)) {
        return "UNBOOTSTRAPPED"
    }

    $watcherRunning = Test-WatcherRunning -PidPath $WatcherPidPath
    $config = Read-JsonObject -Path $WatcherConfigPath
    $privateEntry = $false
    $projectEntry = $false
    if ($null -ne $config -and $null -ne $config.sessions) {
        foreach ($entry in $config.sessions) {
            if ($null -eq $entry -or $entry.agent -ne "codex") {
                continue
            }
            if ($entry.kind -eq "private" -and $entry.session_id -eq $PrivateSession) {
                $privateEntry = $true
            }
            if ($entry.kind -eq "rendezvous" -and $entry.session_id -eq $ProjectName) {
                $projectEntry = $true
            }
        }
    }

    if ($watcherRunning -and $privateEntry -and $projectEntry) {
        return "WATCHING"
    }
    return "BOOTSTRAPPED_NOT_WATCHING"
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
$toastEnabled = Get-ReminderToastsEnabled -Path $SettingsPath
$bridgeState = Get-BridgeRuntimeState -RegistryPath $SessionRegistryPath -WatcherConfigPath $WatcherConfigPath -WatcherPidPath $WatcherPidPath -ProjectName $ProjectBucket -PrivateSession $resolvedPrivateBucket
$resolvedBridgeRoot = Resolve-BridgeRoot -CandidatePaths @($SessionRegistryPath, $WatcherConfigPath, $WatcherPidPath, $SettingsPath, $BridgeWatchFlagPath, $LogPath) -Fallback $bridgeRoot
$resolvedStateDir = Join-Path $resolvedBridgeRoot "state"
$heuristicsDigest = Get-HeuristicsDigest -WorkspaceRoot $WorkspaceRoot
$ledgerDigest = Get-NextPendingBridgeActionDigest -StateDir $resolvedStateDir -OwnerAgent "codex"

$stateLine = "Bridge state: $bridgeState"
$message = "Bridge hygiene: check Codex private bucket $resolvedPrivateBucket and project bucket $ProjectBucket. Continuous monitoring is NOT active unless this thread is currently blocked inside wait_inbox."
$digestLine = "Bridge digest: $heuristicsDigest ; $ledgerDigest"
Write-Output $stateLine
Write-Output $message
Write-Output $digestLine

if ($bridgeState -eq "UNBOOTSTRAPPED") {
    Write-Output "Recovery: run py -3 tools\agent-bridge\recover_bridge_session.py --state-dir `"$resolvedStateDir`" --agent codex --cwd . --watcher-config `"$WatcherConfigPath`""
} elseif ($bridgeState -eq "BOOTSTRAPPED_NOT_WATCHING") {
    Write-Output "Recovery: run the same recover_bridge_session.py command to re-arm watcher/config state for this project."
}

if ($watchModeActive) {
    Write-Output "BRIDGE-WATCH MODE ACTIVE ($HookPhase reminder only; not hard enforcement)."
    Write-Output "If this turn is an explicit bridge-watch smoke test or a deliberately parked watch session, your last action before yielding should be:"
    Write-Output "  mcp__agent_bridge__wait_inbox(agent=`"codex`", session_ids=[`"$ProjectBucket`",`"$resolvedPrivateBucket`"], timeout_seconds=55, mark_read=false)"
    Write-Output "Do not use a persistent wait_inbox loop in the main working chat unless the user explicitly asked for that short test."
}

"$timestamp reminded phase=$HookPhase project=$ProjectBucket private=$resolvedPrivateBucket bridge_state=$bridgeState watch_mode=$watchModeActive toast_enabled=$toastEnabled heuristics='$heuristicsDigest' ledger='$ledgerDigest'" | Add-Content -Path $LogPath -Encoding UTF8

if ($NoToast -or -not $toastEnabled) {
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
        $notify.BalloonTipText = "Bridge state: $bridgeState. Bridge-watch mode active. Check private bucket $resolvedPrivateBucket, then re-enter wait_inbox only for explicit bridge-watch tests."
    } else {
        $notify.BalloonTipText = "$stateLine`n$digestLine"
    }
    $notify.Visible = $true
    $notify.ShowBalloonTip(7000)
    Start-Sleep -Seconds 8
    $notify.Dispose()
} catch {
    Write-Output "Bridge reminder toast failed: $($_.Exception.Message)"
}
