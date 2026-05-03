param(
    [string]$WorkspaceRoot = "",
    [string]$BridgeRoot = "",
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
    [int]$DedupSeconds = 30,
    [switch]$Force,
    [switch]$NoToast
)

$ErrorActionPreference = "Stop"

if (-not $BridgeRoot) {
    if ($env:AGENT_BRIDGE_ROOT) {
        $BridgeRoot = [System.Environment]::ExpandEnvironmentVariables($env:AGENT_BRIDGE_ROOT)
    } else {
        $userProfile = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath("UserProfile") }
        $BridgeRoot = Join-Path $userProfile ".agent-bridge"
    }
}
$bridgeRoot = [System.IO.Path]::GetFullPath($BridgeRoot)
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

function Get-ToastSettings {
    param([string]$Path)
    $expiryMinutes = 5
    $retentionMode = 'latest_sticky'
    if (Test-Path $Path) {
        try {
            $s = Get-Content -Raw $Path | ConvertFrom-Json
            if ($null -ne $s.toast_expiry_minutes -and [int]$s.toast_expiry_minutes -ge 1 -and [int]$s.toast_expiry_minutes -le 60) {
                $expiryMinutes = [int]$s.toast_expiry_minutes
            }
            $validModes = @('latest_sticky', 'all_sticky', 'all_expiring')
            if ($null -ne $s.toast_retention_mode -and $validModes -contains ([string]$s.toast_retention_mode).Trim().ToLower()) {
                $retentionMode = ([string]$s.toast_retention_mode).Trim().ToLower()
            }
        } catch {}
    }
    return @{ ExpiryMinutes = $expiryMinutes; RetentionMode = $retentionMode }
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

function Write-ReminderLog {
    param(
        [string]$Path,
        [string]$Line,
        [int]$Retries = 5,
        [int]$DelayMilliseconds = 50
    )

    for ($attempt = 0; $attempt -le $Retries; $attempt++) {
        try {
            $Line | Add-Content -Path $Path -Encoding UTF8
            return $true
        } catch [System.IO.IOException] {
            if ($attempt -eq $Retries) {
                Write-Output "Bridge reminder log write failed after $($Retries + 1) attempt(s): $($_.Exception.Message)"
                return $false
            }
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }

    return $false
}

function Get-RecentReminderDuplicate {
    param(
        [string]$Path,
        [string]$Phase,
        [string]$ProjectName,
        [int]$WindowSeconds
    )

    if ($WindowSeconds -le 0 -or -not (Test-Path $Path)) {
        return $null
    }

    try {
        $lines = Get-Content $Path
    } catch {
        return $null
    }

    for ($idx = $lines.Count - 1; $idx -ge 0; $idx--) {
        $line = [string]$lines[$idx]
        if ($line -match "^(?<ts>\S+) reminded phase=$([regex]::Escape($Phase)) project=$([regex]::Escape($ProjectName)) ") {
            try {
                $previous = [datetimeoffset]::Parse($matches.ts)
                $now = [datetimeoffset]::Parse($timestamp)
                $delta = ($now - $previous).TotalSeconds
                if ($delta -lt $WindowSeconds) {
                    return [pscustomobject]@{
                        previous = $previous
                        delta_seconds = [math]::Round($delta, 3)
                    }
                }
                return $null
            } catch {
                return $null
            }
        }
    }

    return $null
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

    return "rules=$version active=inbox_end+ledger_every_turn+response_debt_guard"
}

function Get-ResponseDebtStatePath {
    param([string]$StateDir)
    return (Join-Path $StateDir "response-debt-state.json")
}

function Set-ResponseDebtTurnStart {
    param(
        [string]$StateDir,
        [string]$ProjectName,
        [string]$PrivateSession,
        [string]$Timestamp
    )

    try {
        if (-not (Test-Path $StateDir)) {
            New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
        }
        $payload = [pscustomobject]@{
            schema_version = 1
            owner_agent = "codex"
            project = $ProjectName
            private_session = $PrivateSession
            current_turn_started_at = $Timestamp
            updated_at = $Timestamp
        }
        $payload | ConvertTo-Json -Depth 6 | Set-Content -Path (Get-ResponseDebtStatePath -StateDir $StateDir) -Encoding UTF8
    } catch {
        # Best-effort reminder state only. The hook must never block the user.
    }
}

function Get-ResponseDebtTurnStart {
    param([string]$StateDir)

    $path = Get-ResponseDebtStatePath -StateDir $StateDir
    if (-not (Test-Path $path)) {
        return $null
    }
    try {
        $payload = Get-Content -Raw $path | ConvertFrom-Json
        if (-not [string]::IsNullOrWhiteSpace([string]$payload.current_turn_started_at)) {
            return [datetimeoffset]::Parse([string]$payload.current_turn_started_at)
        }
    } catch {
        return $null
    }
    return $null
}

function Get-BridgeMessageType {
    param([string]$Body)

    foreach ($line in ([string]$Body -split "`n")) {
        if ($line -match '^\s*TYPE:\s*(?<type>[A-Za-z0-9_-]+)\s*$') {
            return $matches.type.ToUpperInvariant()
        }
    }
    return ""
}

function Get-BridgeMessageSubject {
    param([string]$Body)

    foreach ($line in ([string]$Body -split "`n")) {
        if ($line -match '^\s*SUBJECT:\s*(?<subject>.+?)\s*$') {
            return $matches.subject
        }
    }
    return ""
}

function Test-BridgeMessageNeedsDisposition {
    param([string]$Body)

    $text = [string]$Body
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $false
    }
    if ($text -match '(?im)^\s*ACTION_REQUEST(ED)?\s*:') {
        return $true
    }

    $type = Get-BridgeMessageType -Body $text
    if ($type -in @(
        "ACTION_REQUEST",
        "USER_REQUEST",
        "TEST_MESSAGE",
        "PARKED_LOOP_TEST",
        "UI_LIVE_RENDER_TEST",
        "APP_SERVER_THEN_REDRAW_TEST",
        "TARGETED_SENDKEYS_SMOKE"
    )) {
        return $true
    }
    if ($type.EndsWith("_ACK")) {
        return $false
    }
    if ($type.EndsWith("_SMOKE") -or $type.EndsWith("_TEST")) {
        return $true
    }

    return ($text -match '(?i)\bwhen you receive\b|\bplease\s+(surface|reply|confirm|send|note|acknowledge)\b|\buser wants you to\b')
}

function Get-ResponseDebtDigest {
    param(
        [string]$StateDir,
        [string]$ProjectName,
        [string]$PrivateSession
    )

    $turnStart = Get-ResponseDebtTurnStart -StateDir $StateDir
    if ($null -eq $turnStart) {
        return @{
            banner = "response_debt=unknown"
            hasDebt = $false
        }
    }

    $path = Join-Path $StateDir "inbox-codex.jsonl"
    if (-not (Test-Path $path)) {
        return @{
            banner = "response_debt=empty"
            hasDebt = $false
        }
    }

    $sessionBuckets = @($ProjectName, $PrivateSession) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    $debts = @()
    try {
        foreach ($line in Get-Content $path) {
            if ([string]::IsNullOrWhiteSpace($line)) {
                continue
            }
            try {
                $row = $line | ConvertFrom-Json
            } catch {
                continue
            }
            if ([string]$row.to -ne "codex") {
                continue
            }
            if ($sessionBuckets -notcontains [string]$row.session_id) {
                continue
            }
            if ([string]::IsNullOrWhiteSpace([string]$row.read_at)) {
                continue
            }
            if (-not [string]::IsNullOrWhiteSpace([string]$row.handled_at)) {
                continue
            }
            try {
                $readAt = [datetimeoffset]::Parse([string]$row.read_at)
            } catch {
                continue
            }
            if ($readAt -lt $turnStart) {
                continue
            }
            $body = [string]$row.body
            if (-not (Test-BridgeMessageNeedsDisposition -Body $body)) {
                continue
            }
            $subject = Get-BridgeMessageSubject -Body $body
            if ([string]::IsNullOrWhiteSpace($subject)) {
                $subject = Get-BridgeMessageType -Body $body
            }
            if ($subject.Length -gt 72) {
                $subject = $subject.Substring(0, 69) + "..."
            }
            $debts += [pscustomobject]@{
                id = [string]$row.id
                subject = $subject
                read_at = $readAt
            }
        }
    } catch {
        return @{
            banner = "response_debt=unreadable"
            hasDebt = $false
        }
    }

    if ($debts.Count -eq 0) {
        return @{
            banner = "response_debt=empty"
            hasDebt = $false
        }
    }

    $top = $debts | Sort-Object read_at | Select-Object -First 1
    return @{
        banner = "response_debt=$($top.id) $($top.subject)"
        hasDebt = $true
    }
}

function Get-ReviewCloseoutDebtDigest {
    param(
        [string]$StateDir,
        [string]$OwnerAgent = "codex",
        [string]$ProjectName = "",
        [string]$PrivateSession = ""
    )

    $path = Join-Path $StateDir "review-loop-state.jsonl"
    if (-not (Test-Path $path)) {
        return @{
            banner = "review_closeout=empty"
            hasDebt = $false
        }
    }

    $pendingCloseoutKeys = @{}
    $pendingPath = Join-Path $StateDir "pending-actions.json"
    if (Test-Path $pendingPath) {
        try {
            $pendingPayload = Get-Content -Raw $pendingPath | ConvertFrom-Json
            foreach ($action in @($pendingPayload.actions)) {
                if ($null -eq $action) {
                    continue
                }
                if ([string]$action.owner_agent -ne $OwnerAgent) {
                    continue
                }
                $actionGuard = [string]$action.guard_id
                $actionKind = [string]$action.kind
                $actionStatus = ([string]$action.status).Trim().ToLowerInvariant()
                $executionState = ([string]$action.execution_state).Trim().ToLowerInvariant()
                if ($actionGuard -ne "WGI-09" -and $actionKind -ne "review_closeout") {
                    continue
                }
                if ($actionStatus -notin @("pending", "parked", "blocked", "displaced")) {
                    continue
                }
                if ($executionState -and $executionState -notin @("parked", "blocked", "displaced", "pending")) {
                    continue
                }
                $pendingBodyFields = @(
                    [string]$action.closeout_body,
                    [string]$action.pending_body,
                    [string]$action.full_body,
                    [string]$action.body
                )
                $hasRecoverableBody = $false
                foreach ($bodyField in $pendingBodyFields) {
                    if (-not [string]::IsNullOrWhiteSpace($bodyField)) {
                        $hasRecoverableBody = $true
                        break
                    }
                }
                if (-not $hasRecoverableBody) {
                    continue
                }
                $requestId = [string]$action.review_loop_id
                if ([string]::IsNullOrWhiteSpace($requestId)) {
                    $requestId = [string]$action.request_message_id
                }
                $peerResultId = [string]$action.peer_result_message_id
                if (-not [string]::IsNullOrWhiteSpace($requestId) -and -not [string]::IsNullOrWhiteSpace($peerResultId)) {
                    $pendingCloseoutKeys["$requestId|$peerResultId"] = $true
                }
            }
        } catch {
            # Pending actions are advisory for this guard; unreadable ledger should
            # not suppress a real review-closeout warning.
        }
    }

    $sessionBuckets = @($ProjectName, $PrivateSession) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    $loops = @{}
    try {
        foreach ($line in Get-Content $path) {
            if ([string]::IsNullOrWhiteSpace($line)) {
                continue
            }
            try {
                $row = $line | ConvertFrom-Json
            } catch {
                continue
            }
            $rowOwner = [string]$row.owner_agent
            if (-not [string]::IsNullOrWhiteSpace($rowOwner) -and $rowOwner -ne $OwnerAgent) {
                continue
            }
            $rowOwnerSession = [string]$row.owner_session_id
            if (
                -not [string]::IsNullOrWhiteSpace($rowOwnerSession) -and
                $sessionBuckets.Count -gt 0 -and
                $sessionBuckets -notcontains $rowOwnerSession
            ) {
                continue
            }
            $key = [string]$row.request_message_id
            if ([string]::IsNullOrWhiteSpace($key)) {
                $key = [string]$row.review_loop_id
            }
            if ([string]::IsNullOrWhiteSpace($key)) {
                continue
            }
            if (-not $loops.ContainsKey($key)) {
                $loops[$key] = @{
                    request_id = $key
                    subject = ""
                    handled_results = @{}
                    closeouts = @{}
                }
            }
            $loop = $loops[$key]
            $subject = [string]$row.subject
            if (-not [string]::IsNullOrWhiteSpace($subject)) {
                $loop["subject"] = $subject
            }
            $eventType = [string]$row.event_type
            $peerResultId = [string]$row.peer_result_message_id
            if ($eventType -eq "peer_result_handled" -and -not [string]::IsNullOrWhiteSpace($peerResultId)) {
                $handledAt = [datetimeoffset]::MinValue
                try {
                    $handledAt = [datetimeoffset]::Parse([string]$row.created_at)
                } catch {}
                $loop["handled_results"][$peerResultId] = [pscustomobject]@{
                    request_id = $key
                    peer_result_id = $peerResultId
                    handled_at = $handledAt
                    subject = $subject
                }
            } elseif ($eventType -eq "closeout_sent" -and -not [string]::IsNullOrWhiteSpace([string]$row.closeout_message_id) -and -not [string]::IsNullOrWhiteSpace($peerResultId)) {
                $closeoutAt = [datetimeoffset]::MinValue
                try {
                    $closeoutAt = [datetimeoffset]::Parse([string]$row.created_at)
                } catch {}
                $loop["closeouts"][$peerResultId] = [pscustomobject]@{
                    closeout_id = [string]$row.closeout_message_id
                    closeout_at = $closeoutAt
                }
            }
        }
    } catch {
        return @{
            banner = "review_closeout=unreadable"
            hasDebt = $false
        }
    }

    $debts = @()
    foreach ($loop in $loops.Values) {
        foreach ($handled in $loop["handled_results"].Values) {
            $peerResultId = [string]$handled.peer_result_id
            $requestId = [string]$handled.request_id
            $closed = $false
            if ($loop["closeouts"].ContainsKey($peerResultId)) {
                $closeout = $loop["closeouts"][$peerResultId]
                if ($closeout.closeout_at -ge $handled.handled_at) {
                    $closed = $true
                }
            }
            if ($pendingCloseoutKeys.ContainsKey("$requestId|$peerResultId")) {
                $closed = $true
            }
            if (-not $closed) {
                $subject = [string]$handled.subject
                if ([string]::IsNullOrWhiteSpace($subject)) {
                    $subject = [string]$loop["subject"]
                }
                $debts += [pscustomobject]@{
                    request_id = $requestId
                    peer_result_id = $peerResultId
                    subject = $subject
                    peer_result_handled_at = $handled.handled_at
                }
            }
        }
    }

    if ($debts.Count -eq 0) {
        return @{
            banner = "review_closeout=empty"
            hasDebt = $false
        }
    }

    $top = $debts | Sort-Object peer_result_handled_at | Select-Object -First 1
    $subject = [string]$top.subject
    if ([string]::IsNullOrWhiteSpace($subject)) {
        $subject = [string]$top.peer_result_id
    }
    if ($subject.Length -gt 72) {
        $subject = $subject.Substring(0, 69) + "..."
    }
    return @{
        banner = "review_closeout=$($top.request_id) $subject"
        hasDebt = $true
    }
}

function Get-ReviewerWaitDebtDigest {
    param(
        [string]$StateDir,
        [string]$OwnerAgent = "codex"
    )

    $path = Join-Path $StateDir "reviewer-wait-state.jsonl"
    if (-not (Test-Path $path)) {
        return @{
            banner = "reviewer_wait=empty"
            hasDebt = $false
        }
    }

    $waits = @{}
    try {
        foreach ($line in Get-Content $path) {
            if ([string]::IsNullOrWhiteSpace($line)) {
                continue
            }
            try {
                $row = $line | ConvertFrom-Json
            } catch {
                continue
            }
            if ([string]$row.owner_agent -ne $OwnerAgent) {
                continue
            }
            $waitId = [string]$row.wait_id
            if ([string]::IsNullOrWhiteSpace($waitId)) {
                continue
            }
            if (-not $waits.ContainsKey($waitId)) {
                $waits[$waitId] = [ordered]@{
                    wait_id = $waitId
                    reviewer_id = ""
                    subject = ""
                    status = ""
                    eta_at = ""
                    checkback_due_at = ""
                    latest_event_at = ""
                    latest_event_type = ""
                }
            }
            $wait = $waits[$waitId]
            foreach ($field in @("reviewer_id", "subject", "status", "eta_at", "checkback_due_at")) {
                $value = [string]$row.$field
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    $wait[$field] = $value
                }
            }
            $wait["latest_event_at"] = [string]$row.created_at
            $wait["latest_event_type"] = [string]$row.event_type
        }
    } catch {
        return @{
            banner = "reviewer_wait=unreadable"
            hasDebt = $false
        }
    }

    $now = [datetimeoffset]::UtcNow
    $terminalStatuses = @("verdict_received", "parked", "blocked", "cancelled")
    $debts = @()
    $scheduled = @()
    foreach ($wait in $waits.Values) {
        $status = ([string]$wait.status).Trim().ToLowerInvariant()
        if ($terminalStatuses -contains $status) {
            continue
        }
        $dueRaw = [string]$wait.checkback_due_at
        if ([string]::IsNullOrWhiteSpace($dueRaw)) {
            $dueRaw = [string]$wait.eta_at
        }
        if ([string]::IsNullOrWhiteSpace($dueRaw)) {
            $wait["debt_reason"] = "missing_eta_or_checkback"
            $debts += [pscustomobject]$wait
            continue
        }
        try {
            $due = [datetimeoffset]::Parse($dueRaw)
        } catch {
            $wait["debt_reason"] = "invalid_eta_or_checkback"
            $debts += [pscustomobject]$wait
            continue
        }
        if ($due -le $now) {
            $wait["debt_reason"] = "checkback_due"
            $debts += [pscustomobject]$wait
        } else {
            $scheduled += [pscustomobject]$wait
        }
    }

    if ($debts.Count -gt 0) {
        $top = $debts | Select-Object -First 1
        $label = [string]$top.subject
        if ([string]::IsNullOrWhiteSpace($label)) {
            $label = [string]$top.reviewer_id
        }
        if ([string]::IsNullOrWhiteSpace($label)) {
            $label = [string]$top.wait_id
        }
        if ($label.Length -gt 72) {
            $label = $label.Substring(0, 69) + "..."
        }
        return @{
            banner = "reviewer_wait=$($top.wait_id) $($top.debt_reason) $label"
            hasDebt = $true
        }
    }

    if ($scheduled.Count -gt 0) {
        return @{
            banner = "reviewer_wait=scheduled($($scheduled.Count))"
            hasDebt = $false
        }
    }

    return @{
        banner = "reviewer_wait=empty"
        hasDebt = $false
    }
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
        $executionState = ([string]$action.execution_state).Trim().ToLowerInvariant()
        if ($executionState -in @("blocked", "parked", "displaced", "completed")) {
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

function Get-ActiveExecutionDigest {
    param(
        [string]$StateDir,
        [string]$OwnerAgent = "codex"
    )

    $path = Join-Path $StateDir "execution-state.json"
    if (-not (Test-Path $path)) {
        return @{
            banner = "execution=idle"
            resume = $null
        }
    }

    try {
        $payload = Get-Content -Raw $path | ConvertFrom-Json
    } catch {
        return @{
            banner = "execution=unreadable"
            resume = $null
        }
    }

    $ownerRecord = $payload.owners.$OwnerAgent
    if ($null -eq $ownerRecord -or $null -eq $ownerRecord.active_task) {
        return @{
            banner = "execution=idle"
            resume = $null
        }
    }

    $task = $ownerRecord.active_task
    $summary = [string]$task.summary
    if ($summary.Length -gt 72) {
        $summary = $summary.Substring(0, 69) + "..."
    }

    $status = [string]$task.status
    $proofStatus = [string]$task.proof_status
    $source = [string]$task.source
    $priorDisposition = [string]$task.prior_disposition
    $priorActionId = [string]$task.prior_action_id
    $interruptMode = [string]$task.interrupt_mode
    $latestClassification = $task.latest_interrupt_classification
    $classificationDisposition = ""
    if ($null -ne $latestClassification) {
        $classificationDisposition = [string]$latestClassification.disposition
    }
    $taskId = [string]$task.id
    $banner = "active_task=$status/$proofStatus $taskId $summary source=$source interrupt=$interruptMode"
    if (-not [string]::IsNullOrWhiteSpace($priorDisposition) -or -not [string]::IsNullOrWhiteSpace($priorActionId)) {
        $banner += " prior=${priorDisposition}:$priorActionId"
    }
    if ([string]::IsNullOrWhiteSpace($classificationDisposition)) {
        $banner += " classification=missing"
    } else {
        $banner += " classification=$classificationDisposition"
    }
    return @{
        banner = $banner
        resume = "resume active task: $taskId $summary"
        classification = $classificationDisposition
    }
}

function ConvertTo-XmlSafeString {
    param([string]$Value)
    if ($null -eq $Value) { return '' }
    return [System.Security.SecurityElement]::Escape([string]$Value)
}

function Get-ToastStateFilePath {
    param([string]$StateDir)
    return Join-Path $StateDir 'toast-latest.json'
}

# Send a WinRT Toast Notification.
# Strategy: the most recent toast lingers permanently (no ExpirationTime) until
# the user dismisses it. All older toasts expire after 5 minutes and are cleared
# from the Action Center automatically.
#
# Mechanism: each toast gets a unique tag stored in toast-latest.json. On the next
# invocation, the previous "permanent" toast is re-sent with the same tag but with
# ExpirationTime=Now+5min, converting it to an expiring entry in the Action Center.
# The new toast is then sent with no ExpirationTime.
#
# Works in PS 5.1 (powershell.exe) only -- WinRT ContentType=WindowsRuntime is not
# supported in PS 7.x (pwsh.exe). The caller catches the load failure and falls back
# to NotifyIcon balloon tip (no expiry control, but still functional).
function Send-BridgeWinRtToast {
    param(
        [string]$Title,
        [string]$Body,
        [string]$StateDir,
        [int]$ExpiryMinutes = 5,
        # latest_sticky : newest sticks until dismissed; older toasts expire after ExpiryMinutes
        # all_sticky    : nothing auto-expires; user dismisses every toast manually
        # all_expiring  : every toast expires after ExpiryMinutes, including the newest
        [string]$RetentionMode = 'latest_sticky'
    )

    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    $group = 'agent-bridge'
    $stateFile = Get-ToastStateFilePath -StateDir $StateDir

    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)

    # Convert the previous permanent toast to an expiring one, unless the user
    # wants everything to stay permanent (all_permanent mode).
    if ($RetentionMode -ne 'all_sticky' -and (Test-Path $stateFile)) {
        try {
            $prev = Get-Content -Raw $stateFile | ConvertFrom-Json
            if ($null -ne $prev -and -not [string]::IsNullOrWhiteSpace([string]$prev.tag)) {
                $prevXml = '<toast><visual><binding template="ToastGeneric"><text>' +
                    (ConvertTo-XmlSafeString ([string]$prev.title)) +
                    '</text><text>' +
                    (ConvertTo-XmlSafeString ([string]$prev.body)) +
                    '</text></binding></visual></toast>'
                $prevDoc = New-Object Windows.Data.Xml.Dom.XmlDocument
                $prevDoc.LoadXml($prevXml)
                $prevToast = New-Object Windows.UI.Notifications.ToastNotification $prevDoc
                $prevToast.Tag = [string]$prev.tag
                $prevToast.Group = $group
                $prevToast.ExpirationTime = [DateTimeOffset]::Now.AddMinutes($ExpiryMinutes)
                $notifier.Show($prevToast)
            }
        } catch {}
    }

    $newTag = 'bridge-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmssfff')
    $newXml = '<toast><visual><binding template="ToastGeneric"><text>' +
        (ConvertTo-XmlSafeString $Title) +
        '</text><text>' +
        (ConvertTo-XmlSafeString $Body) +
        '</text></binding></visual></toast>'
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($newXml)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
    $toast.Tag = $newTag
    $toast.Group = $group
    if ($RetentionMode -eq 'all_expiring') {
        $toast.ExpirationTime = [DateTimeOffset]::Now.AddMinutes($ExpiryMinutes)
    }

    $notifier.Show($toast)

    try {
        if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Force -Path $StateDir | Out-Null }
        [pscustomobject]@{ tag = $newTag; title = $Title; body = $Body } |
            ConvertTo-Json -Compress |
            Set-Content -Path $stateFile -Encoding UTF8
    } catch {}
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
            if (
                $entry.kind -eq "private" -and (
                    $entry.session_id -eq $PrivateSession -or (
                        $entry.session_id_source -eq "active_session" -and
                        $entry.project -eq $ProjectName
                    )
                )
            ) {
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
Write-ReminderLog -Path $LogPath -Line "$timestamp invoked cwd=$cwd force=$($Force.IsPresent) noToast=$($NoToast.IsPresent)" | Out-Null

if (-not $Force -and -not (Test-IsUnderPath -Path $cwd -Root $WorkspaceRoot)) {
    Write-ReminderLog -Path $LogPath -Line "$timestamp suppressed reason=outside-workspace workspace=$WorkspaceRoot" | Out-Null
    exit 0
}

$resolvedPrivateBucket = Resolve-ActivePrivateBucket -RegistryPath $SessionRegistryPath -ProjectName $ProjectBucket -Fallback $PrivateBucket
$recentDuplicate = $null
$duplicateToastSuppressed = $false
if (-not $Force) {
    $recentDuplicate = Get-RecentReminderDuplicate -Path $LogPath -Phase $HookPhase -ProjectName $ProjectBucket -WindowSeconds $DedupSeconds
}
if ($recentDuplicate) {
    Write-ReminderLog -Path $LogPath -Line "$timestamp suppressed reason=duplicate phase=$HookPhase project=$ProjectBucket delta_seconds=$($recentDuplicate.delta_seconds) previous=$($recentDuplicate.previous.ToString('o'))" | Out-Null
    if ($HookPhase -ne "final") {
        exit 0
    }
    # Final-hook guards must still run even when toast/reminder noise is
    # deduplicated; otherwise newly-created debt can be hidden for DedupSeconds.
    $duplicateToastSuppressed = $true
}
$watchModeActive = Test-Path $BridgeWatchFlagPath
$toastEnabled = Get-ReminderToastsEnabled -Path $SettingsPath
$toastSettings = Get-ToastSettings -Path $SettingsPath
$bridgeState = Get-BridgeRuntimeState -RegistryPath $SessionRegistryPath -WatcherConfigPath $WatcherConfigPath -WatcherPidPath $WatcherPidPath -ProjectName $ProjectBucket -PrivateSession $resolvedPrivateBucket
$resolvedBridgeRoot = Resolve-BridgeRoot -CandidatePaths @($SessionRegistryPath, $WatcherConfigPath, $WatcherPidPath, $SettingsPath, $BridgeWatchFlagPath, $LogPath) -Fallback $bridgeRoot
$resolvedStateDir = Join-Path $resolvedBridgeRoot "state"
if ($HookPhase -eq "response") {
    Set-ResponseDebtTurnStart -StateDir $resolvedStateDir -ProjectName $ProjectBucket -PrivateSession $resolvedPrivateBucket -Timestamp $timestamp
}
$heuristicsDigest = Get-HeuristicsDigest -WorkspaceRoot $WorkspaceRoot
$executionDigest = Get-ActiveExecutionDigest -StateDir $resolvedStateDir -OwnerAgent "codex"
$ledgerDigest = Get-NextPendingBridgeActionDigest -StateDir $resolvedStateDir -OwnerAgent "codex"
$responseDebtDigest = Get-ResponseDebtDigest -StateDir $resolvedStateDir -ProjectName $ProjectBucket -PrivateSession $resolvedPrivateBucket
$reviewCloseoutDebtDigest = Get-ReviewCloseoutDebtDigest -StateDir $resolvedStateDir -OwnerAgent "codex" -ProjectName $ProjectBucket -PrivateSession $resolvedPrivateBucket
$reviewerWaitDebtDigest = Get-ReviewerWaitDebtDigest -StateDir $resolvedStateDir -OwnerAgent "codex"
$ledgerHasPending = $ledgerDigest.StartsWith("ledger_top=")
$responseDebtHasPending = [bool]$responseDebtDigest.hasDebt
$reviewCloseoutHasPending = [bool]$reviewCloseoutDebtDigest.hasDebt
$reviewerWaitHasPending = [bool]$reviewerWaitDebtDigest.hasDebt
$executionIdle = $executionDigest.banner -eq "execution=idle"
$executionHasActiveTask = $executionDigest.banner.StartsWith("active_task=")

$stateLine = "Bridge state: $bridgeState"
$message = "Bridge hygiene: check Codex private bucket $resolvedPrivateBucket and project bucket $ProjectBucket. Continuous monitoring is NOT active unless this thread is currently blocked inside wait_inbox."
$digestLine = "Bridge digest: $heuristicsDigest ; $($executionDigest.banner) ; $ledgerDigest ; $($responseDebtDigest.banner) ; $($reviewCloseoutDebtDigest.banner) ; $($reviewerWaitDebtDigest.banner)"
Write-Output $stateLine
Write-Output $message
Write-Output $digestLine
if ($executionDigest.resume) {
    Write-Output $executionDigest.resume
}
if ($HookPhase -eq "final" -and $executionIdle -and $ledgerHasPending) {
    Write-Output "FINAL-GUARD: execution is idle but the Codex ledger is not empty. Do not send a final response until the top item is worked, blocked, parked, or explicitly displaced."
}
if ($HookPhase -eq "final" -and $executionHasActiveTask) {
    Write-Output "FINAL-GUARD: an active Codex task is still open. Do not treat an interrupt, inbox check, status answer, or checkpoint as completion; resume it now, or explicitly classify it as blocked, parked, displaced, or complete."
    if ([string]::IsNullOrWhiteSpace([string]$executionDigest.classification)) {
        Write-Output "FINAL-GUARD: active-task interrupt classification artifact is missing. Record classify_execution_interrupt(disposition=resume|complete|blocked|parked|displaced), or close the active task, before 10/10 closeout."
    }
}
if ($HookPhase -eq "final" -and $responseDebtHasPending) {
    Write-Output "FINAL-GUARD: bridge message read this turn still needs reply/disposition. Send a bridge reply or mark it handled/parked/blocked/displaced before final."
}
if ($HookPhase -eq "final" -and $reviewCloseoutHasPending) {
    Write-Output "FINAL-GUARD: peer review loop is handled locally but lacks a closeout handoff. Send the amended status/review closeout to the peer or record it as parked/blocked before final."
}
if ($HookPhase -eq "final" -and $reviewerWaitHasPending) {
    Write-Output "FINAL-GUARD: background reviewer wait has no valid ETA/checkback, or its checkback is due. Ask the reviewer for an ETA/verdict, record reviewer_wait_state, or park/block the reviewer debt before final."
}

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

Write-ReminderLog -Path $LogPath -Line "$timestamp reminded phase=$HookPhase project=$ProjectBucket private=$resolvedPrivateBucket bridge_state=$bridgeState watch_mode=$watchModeActive toast_enabled=$toastEnabled heuristics='$heuristicsDigest' execution='$($executionDigest.banner)' ledger='$ledgerDigest' response_debt='$($responseDebtDigest.banner)' review_closeout='$($reviewCloseoutDebtDigest.banner)' reviewer_wait='$($reviewerWaitDebtDigest.banner)' final_guard=$($HookPhase -eq "final" -and (($executionIdle -and $ledgerHasPending) -or $executionHasActiveTask -or $responseDebtHasPending -or $reviewCloseoutHasPending -or $reviewerWaitHasPending))" | Out-Null

if ($NoToast -or -not $toastEnabled -or $duplicateToastSuppressed) {
    exit 0
}

$toastTitle = 'Codex bridge reminder'
$toastBody = if ($watchModeActive) {
    "Bridge state: $bridgeState. Bridge-watch mode active. Check private bucket $resolvedPrivateBucket, then re-enter wait_inbox only for explicit bridge-watch tests."
} else {
    "$stateLine`n$digestLine"
}

$winRtSucceeded = $false
try {
    Send-BridgeWinRtToast -Title $toastTitle -Body $toastBody -StateDir $resolvedStateDir -ExpiryMinutes $toastSettings.ExpiryMinutes -RetentionMode $toastSettings.RetentionMode
    $winRtSucceeded = $true
} catch {}

if (-not $winRtSucceeded) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
        $notify.BalloonTipTitle = $toastTitle
        $notify.BalloonTipText = $toastBody
        $notify.Visible = $true
        $notify.ShowBalloonTip(7000)
        Start-Sleep -Seconds 8
        $notify.Dispose()
    } catch {
        Write-Output "Bridge reminder toast failed: $($_.Exception.Message)"
    }
}
