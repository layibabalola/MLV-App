# wake_codex.ps1
# Doc: tools/agent-bridge/WAKE_CODEX_TUNING.md — read before editing params or defaults.
# Inject a synthetic user message into the running Codex Desktop window.
# Invoked from watcher's on_message_command when a Codex bridge message arrives.
#
# Behavior:
#   - If a Codex Desktop thread id is provided, opens codex://threads/<id> before
#     injecting so the reserved trigger lands in the bridge chat, not whichever
#     chat happened to be visible.
#   - Waits for system-wide input idle (>= IdleThresholdSeconds) before injecting,
#     so we don't clobber active typing - anywhere, not just in Codex.
#   - Uses a lock file so concurrent wake invocations batch into one (the next
#     "check bridge inbox" will surface every unread message anyway).
#   - Activates Codex regardless of current foreground (user reading Codex
#     counts as idle from the OS's POV; the message still needs to be delivered).
#   - If Codex itself is the foreground app on a different/unprovable thread,
#     the strict guard fails closed unless an exact RestoreThreadId is available.
#     The watcher may explicitly opt into delivery priority with
#     -AllowForegroundCodexThreadDisplacement, accepting that the user may be
#     left on the bridge thread so unread inbox work is not stranded.
#   - Clears the composer (Ctrl+A + Delete) before injection - any draft text
#     the user left in the box will be wiped. This is a deliberate trade-off.
#
# Usage:
#   .\wake_codex.ps1 [-Message "check bridge inbox"]
#                    [-ThreadId "<codex-desktop-conversation-guid>"]
#                    [-IdleThresholdSeconds 10]
#                    [-MaxWaitSeconds 600]
#                    [-DryRun] [-FindOnly]
#
# Exit codes:
#   3  = unsafe target proof / wrong-chat risk detected before typing.
#   13 = UIA SetFocus primary path + all Win32 fallbacks failed to acquire foreground
#   14 = no Codex window after deeplink navigation
#   15 = total runtime timeout exceeded
#   16 = deferred (system idle never reached within MaxWaitSeconds, user typing,
#        or strict foreground-Codex non-displacement guard refused navigation).
#        Watcher should retry; do NOT trip the wake breaker.

param(
    [string]$Message              = "check bridge inbox",
    [string]$ThreadId             = "",
    [string]$ExpectedProjectToken = "",
    [int]   $IdleThresholdSeconds = 5,
    [int]   $MaxWaitSeconds       = 60,
    [int]   $TotalRuntimeTimeoutSeconds = 90,
    [string]$LockFile             = "",
    [string]$StateDir             = "",
    [string]$MessageId            = "",
    [ValidateSet("urgent", "normal", "low")]
    [string]$Priority             = "normal",
    [int]   $PreflightPollSeconds = 5,
    [int]   $PreflightIdleStabilitySeconds = 0,
    [int]   $PreflightCapSeconds  = 0,
    [int]   $DeeplinkSleepMilliseconds = 500,
    # Smart debounce: fire immediately if composer has been idle-empty for this long.
    # Bypasses the full priority-based stability wait when Codex is clearly idle.
    [int]   $FastPathIdleSeconds  = 1,
    # Draft protection: if composer has content, require this many seconds of
    # stability before firing (prevents clobbering an in-progress Codex response).
    [int]   $DraftStabilitySeconds = 5,
    # Active-typing patience: if user has keyboard focus in the composer (actively typing),
    # extend the preflight cap to this many seconds before hijacking. Within this window
    # the 5s inactivity check (DraftStabilitySeconds) still applies — we fire as soon as
    # they stop typing for 5s, or after this max wait elapses.
    [int]   $ActiveTypingMaxWaitSeconds = 90,
    [switch]$RequireThreadId,
    [switch]$RequireConstantMessage,
    [switch]$VerifyTargetTwice,
    [int]   $VerifyTargetGapMilliseconds = 50,
    [int]   $MaxPreSendRaceMilliseconds = 0,
    [switch]$PostTypingVerify,
    [switch]$WarnOnTitleMismatch,
    [switch]$RequireTitleMatch,
    [string]$ExpectedThreadTitle = "",
    [string]$RestoreThreadId = "",
    [switch]$ProtectForegroundCodexThread,
    [switch]$AllowForegroundCodexThreadDisplacement,
    [switch]$AllowLegacyNoPreflight,
    [switch]$SkipPreflight,
    [switch]$DryRun,
    [switch]$FindOnly,
    [switch]$PrintInnerCommand,
    [switch]$TestInputSize,
    [string]$ProcessName          = "Codex",
    [switch]$RunInnerWake,
    # Developer flag: include raw composer text in preflight audit records.
    # Off by default — enables diagnosis of false-positive draft-preserve classifications.
    [switch]$AuditDraftText
)

$ErrorActionPreference = "Stop"

$defaultUserProfile = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath("UserProfile") }
$defaultBridgeRoot = if ($env:AGENT_BRIDGE_ROOT) {
    [System.Environment]::ExpandEnvironmentVariables($env:AGENT_BRIDGE_ROOT)
} else {
    Join-Path $defaultUserProfile ".agent-bridge"
}
if (-not $StateDir) {
    $StateDir = Join-Path $defaultBridgeRoot "state"
}
if (-not $LockFile) {
    $LockFile = Join-Path ([System.IO.Path]::GetFullPath((Split-Path -Parent $StateDir))) "wake_codex.lock"
}

$script:WakeStart = [System.Diagnostics.Stopwatch]::StartNew()

function Write-StageEvent {
    param([string]$Stage, [string]$Detail = "")
    $elapsed = [Math]::Round($script:WakeStart.Elapsed.TotalSeconds, 3)
    $ts = (Get-Date).ToUniversalTime().ToString("HH:mm:ss.fff") + "Z"
    $msg = "[wake_codex][$ts][+${elapsed}s][$Stage]"
    if ($Detail) { $msg += " " + $Detail }
    Write-Host $msg
}

Add-Type -AssemblyName System.Windows.Forms

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Win32Wake {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void SwitchToThisWindow(IntPtr hWnd, bool fAltTab);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    [DllImport("kernel32.dll")] public static extern uint GetTickCount();
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool SystemParametersInfo(uint uiAction, uint uiParam, ref uint pvParam, uint fWinIni);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool SystemParametersInfoSet(uint uiAction, uint uiParam, uint pvParam, uint fWinIni);
    public const uint SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000;
    public const uint SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001;
    public const uint SWP_NOMOVE    = 0x0002;
    public const uint SWP_NOSIZE    = 0x0001;
    public const uint SWP_SHOWWINDOW = 0x0040;
    public const uint INPUT_KEYBOARD = 1;
    public const ushort VK_MENU = 0x12;
    public const uint KEYEVENTF_KEYUP = 0x0002;
    public const uint WM_PASTE     = 0x0302;
    public const uint WM_KEYDOWN   = 0x0100;
    public const uint WM_KEYUP_MSG = 0x0101;
    public const ushort VK_CONTROL = 0x11;
    public const ushort VK_RETURN  = 0x0D;
    public const ushort VK_DELETE  = 0x2E;
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT {
        public uint type;
        public KEYBDINPUT ki;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }
}
"@

function Get-CodexWindow {
    Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
}

function Get-ForegroundCodexWindow {
    $hWnd = [Win32Wake]::GetForegroundWindow()
    if ($hWnd -eq [IntPtr]::Zero) {
        return $null
    }
    $processId = 0
    [Win32Wake]::GetWindowThreadProcessId($hWnd, [ref]$processId) | Out-Null
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq $ProcessName -and $process.MainWindowHandle -ne 0) {
        return $process
    }
    return $null
}

function Get-WindowTitle {
    param([IntPtr]$hWnd)
    $sb = New-Object System.Text.StringBuilder 256
    [Win32Wake]::GetWindowText($hWnd, $sb, $sb.Capacity) | Out-Null
    return $sb.ToString()
}

function ConvertTo-PowerShellSingleQuotedLiteral {
    param([string]$Value)
    if ($null -eq $Value) {
        return "''"
    }
    return "'" + $Value.Replace("'", "''") + "'"
}

function New-InnerWakeCommand {
    $innerCommandParts = @(
        "& " + (ConvertTo-PowerShellSingleQuotedLiteral $PSCommandPath),
        "-RunInnerWake",
        "-Message " + (ConvertTo-PowerShellSingleQuotedLiteral $Message),
        "-ThreadId " + (ConvertTo-PowerShellSingleQuotedLiteral $ThreadId),
        "-ExpectedProjectToken " + (ConvertTo-PowerShellSingleQuotedLiteral $ExpectedProjectToken),
        "-IdleThresholdSeconds " + [string]$IdleThresholdSeconds,
        "-MaxWaitSeconds " + [string]$MaxWaitSeconds,
        "-TotalRuntimeTimeoutSeconds " + [string]$TotalRuntimeTimeoutSeconds,
        "-LockFile " + (ConvertTo-PowerShellSingleQuotedLiteral $LockFile),
        "-StateDir " + (ConvertTo-PowerShellSingleQuotedLiteral $StateDir),
        "-MessageId " + (ConvertTo-PowerShellSingleQuotedLiteral $MessageId),
        "-Priority " + (ConvertTo-PowerShellSingleQuotedLiteral $Priority),
        "-PreflightPollSeconds " + [string]$PreflightPollSeconds,
        "-PreflightIdleStabilitySeconds " + [string]$PreflightIdleStabilitySeconds,
        "-PreflightCapSeconds " + [string]$PreflightCapSeconds,
        "-DeeplinkSleepMilliseconds " + [string]$DeeplinkSleepMilliseconds,
        "-FastPathIdleSeconds " + [string]$FastPathIdleSeconds,
        "-DraftStabilitySeconds " + [string]$DraftStabilitySeconds,
        "-ActiveTypingMaxWaitSeconds " + [string]$ActiveTypingMaxWaitSeconds,
        "-VerifyTargetGapMilliseconds " + [string]$VerifyTargetGapMilliseconds,
        "-MaxPreSendRaceMilliseconds " + [string]$MaxPreSendRaceMilliseconds,
        "-ProcessName " + (ConvertTo-PowerShellSingleQuotedLiteral $ProcessName)
    )
    if ($RequireThreadId) {
        $innerCommandParts += "-RequireThreadId"
    }
    if ($ExpectedThreadTitle) {
        $innerCommandParts += "-ExpectedThreadTitle " + (ConvertTo-PowerShellSingleQuotedLiteral $ExpectedThreadTitle)
    }
    if ($RestoreThreadId) {
        $innerCommandParts += "-RestoreThreadId " + (ConvertTo-PowerShellSingleQuotedLiteral $RestoreThreadId)
    }
    if ($ProtectForegroundCodexThread) {
        $innerCommandParts += "-ProtectForegroundCodexThread"
    }
    if ($AllowForegroundCodexThreadDisplacement) {
        $innerCommandParts += "-AllowForegroundCodexThreadDisplacement"
    }
    if ($RequireConstantMessage) {
        $innerCommandParts += "-RequireConstantMessage"
    }
    if ($SkipPreflight) {
        $innerCommandParts += "-SkipPreflight"
    }
    if ($VerifyTargetTwice) {
        $innerCommandParts += "-VerifyTargetTwice"
    }
    if ($PostTypingVerify) {
        $innerCommandParts += "-PostTypingVerify"
    }
    if ($WarnOnTitleMismatch) {
        $innerCommandParts += "-WarnOnTitleMismatch"
    }
    if ($RequireTitleMatch) {
        $innerCommandParts += "-RequireTitleMatch"
    }
    if ($DryRun) {
        $innerCommandParts += "-DryRun"
    }
    if ($AuditDraftText) {
        $innerCommandParts += "-AuditDraftText"
    }
    if ($AllowLegacyNoPreflight) {
        $innerCommandParts += "-AllowLegacyNoPreflight"
    }
    return ($innerCommandParts -join " ")
}

function Get-IdleSeconds {
    $info = New-Object Win32Wake+LASTINPUTINFO
    $info.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($info)
    [Win32Wake]::GetLastInputInfo([ref]$info) | Out-Null
    $now = [Win32Wake]::GetTickCount()
    # Tick wraparound is harmless - uint subtraction stays correct modulo 2^32.
    $idleMs = ($now - $info.dwTime) -band 0xFFFFFFFF
    return [Math]::Round($idleMs / 1000.0, 1)
}

function Test-CodexThreadId {
    param([string]$Value)
    return $Value -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
}

# Approved wake messages. All must contain "check bridge inbox" as the Codex
# trigger phrase. The prefix identifies who initiated the wake for debugging.
$script:ApprovedWakeMessages = @(
    "check bridge inbox",
    "Watcher says check bridge inbox",
    "Codex says check bridge inbox",
    "Claude says check bridge inbox",
    "User says check bridge inbox"
)

function Assert-TargetedWakePolicy {
    if ($RequireConstantMessage -and ($script:ApprovedWakeMessages -notcontains $Message)) {
        Write-Host ("[wake_codex] Unsafe targeted wake: message not in approved list: " + $Message)
        Write-PreflightAudit -Action "targeted_wake_refused" -Fields @{
            abort_reason = "non_constant_message"
            message_hash = (Get-TextHash -Value $Message)
        }
        exit 3
    }
    if ($RequireThreadId -and -not (Test-CodexThreadId -Value $ThreadId)) {
        Write-Host ("[wake_codex] Unsafe targeted wake: valid ThreadId is required before typing. ThreadId=" + $ThreadId)
        Write-PreflightAudit -Action "targeted_wake_refused" -Fields @{
            abort_reason = "missing_or_invalid_thread_id"
        }
        exit 3
    }
}

function Open-CodexThread {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }
    if (-not (Test-CodexThreadId -Value $Value)) {
        Write-Host ("[wake_codex] WARNING: ThreadId is not a UUID, skipping codex://threads navigation: " + $Value)
        return
    }
    $uri = "codex://threads/$Value"
    Write-Host ("[wake_codex] Opening Codex thread deeplink: " + $uri)
    Start-Process $uri
    if ($DeeplinkSleepMilliseconds -gt 0) {
        Start-Sleep -Milliseconds $DeeplinkSleepMilliseconds
    }
}

function Invoke-CodexForegroundAttempt {
    param(
        [IntPtr]$Hwnd,
        [uint32]$TargetThreadId
    )

    # Un-minimize first — SetForegroundWindow on iconic HWND only un-flashes
    if ([Win32Wake]::IsIconic($Hwnd)) {
        [Win32Wake]::ShowWindow($Hwnd, 9) | Out-Null
        Start-Sleep -Milliseconds 30
    }

    # Step 1: phantom Alt-tap via keybd_event BEFORE AttachThreadInput.
    # This satisfies Windows' "calling thread received the last input event" gate
    # in SetForegroundWindow. keybd_event is lower-level than SendInput and fires
    # synchronously into the input stream, ensuring the gate is open before we attach.
    [Win32Wake]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)       # ALT down
    [Win32Wake]::keybd_event(0x12, 0, 0x0002, [UIntPtr]::Zero)  # ALT up

    # Step 2: attach our input queue to the current foreground thread
    $myThread = [Win32Wake]::GetCurrentThreadId()
    $fg = [Win32Wake]::GetForegroundWindow()
    $fgPid = 0
    $fgThread = [Win32Wake]::GetWindowThreadProcessId($fg, [ref]$fgPid)
    $attached = $false
    if ($fgThread -ne 0 -and $fgThread -ne $myThread) {
        $attached = [Win32Wake]::AttachThreadInput($myThread, $fgThread, $true)
    }

    try {
        [Win32Wake]::BringWindowToTop($Hwnd) | Out-Null
        [Win32Wake]::SetForegroundWindow($Hwnd) | Out-Null
        # Topmost-toggle: promotes then demotes to punch through WS_EX_NOACTIVATE and
        # topmost shell windows (Start menu, Action Center) that block Z-order changes
        $swpFlags = [Win32Wake]::SWP_NOMOVE -bor [Win32Wake]::SWP_NOSIZE -bor [Win32Wake]::SWP_SHOWWINDOW
        [Win32Wake]::SetWindowPos($Hwnd, [IntPtr](-1), 0, 0, 0, 0, $swpFlags) | Out-Null
        [Win32Wake]::SetWindowPos($Hwnd, [IntPtr](-2), 0, 0, 0, 0, $swpFlags) | Out-Null
    } finally {
        if ($attached) {
            [Win32Wake]::AttachThreadInput($myThread, $fgThread, $false) | Out-Null
        }
    }
}

function Invoke-CodexForegroundWithSpiNuke {
    # Nuclear fallback: zero ForegroundLockTimeout, attempt focus, restore.
    # Used only after all gentler attempts have failed.
    param([IntPtr]$Hwnd, [uint32]$TargetThreadId)

    $saved = [uint32]0
    [Win32Wake]::SystemParametersInfo([Win32Wake]::SPI_GETFOREGROUNDLOCKTIMEOUT, 0, [ref]$saved, 0) | Out-Null
    [Win32Wake]::SystemParametersInfoSet([Win32Wake]::SPI_SETFOREGROUNDLOCKTIMEOUT, 0, 0, 0x0002) | Out-Null
    try {
        Invoke-CodexForegroundAttempt -Hwnd $Hwnd -TargetThreadId $TargetThreadId
    } finally {
        [Win32Wake]::SystemParametersInfoSet([Win32Wake]::SPI_SETFOREGROUNDLOCKTIMEOUT, 0, $saved, 0x0002) | Out-Null
    }
}

function Send-AltTap {
    $inputs = New-Object 'Win32Wake+INPUT[]' 2
    $inputs[0].type = [Win32Wake]::INPUT_KEYBOARD
    $inputs[0].ki.wVk = [Win32Wake]::VK_MENU
    $inputs[1].type = [Win32Wake]::INPUT_KEYBOARD
    $inputs[1].ki.wVk = [Win32Wake]::VK_MENU
    $inputs[1].ki.dwFlags = [Win32Wake]::KEYEVENTF_KEYUP
    $inputSize = [System.Runtime.InteropServices.Marshal]::SizeOf($inputs[0])
    $sent = [Win32Wake]::SendInput(2, $inputs, $inputSize)
    if ($sent -ne 2) {
        Write-Host ("[wake_codex] WARNING: SendInput ALT-tap sent " + $sent + "/2 input events.")
    }
}

function New-KeyLParam {
    # Build the lParam value for WM_KEYDOWN / WM_KEYUP_MSG PostMessage calls.
    # Bits: [0-15] repeat=1, [16-23] scan code, [30] prevState, [31] transition.
    param([byte]$ScanCode, [switch]$KeyUp)
    $v = [long]1
    $v = $v -bor ([long]$ScanCode -shl 16)
    if ($KeyUp) {
        $v = $v -bor [long]0x40000000  # previous state = down
        $v = $v -bor [long]0x80000000  # transition = release
    }
    return [IntPtr]$v
}

function Invoke-ClipboardOperation {
    param(
        [scriptblock]$Operation,
        [string]$Context = "clipboard",
        [int]$Attempts = 5,
        [int]$DelayMilliseconds = 50
    )
    $lastException = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            return & $Operation
        } catch {
            $lastException = $_.Exception
            if ($attempt -lt $Attempts) {
                Start-Sleep -Milliseconds $DelayMilliseconds
            }
        }
    }
    if ($null -ne $lastException) {
        throw ($Context + " failed after " + $Attempts + " attempts: " + $lastException.Message)
    }
    throw ($Context + " failed after " + $Attempts + " attempts")
}

function Save-ClipboardState {
    param([string]$Context = "clipboard")
    $state = @{
        Saved = $false
        HadData = $false
        Data = $null
        FormatCount = 0
    }
    try {
        $data = Invoke-ClipboardOperation -Context ($Context + " clipboard save") -Operation {
            [System.Windows.Forms.Clipboard]::GetDataObject()
        }
        $state.Saved = $true
        if ($null -ne $data) {
            $formats = @($data.GetFormats())
            $state.Data = $data
            $state.FormatCount = $formats.Count
            $state.HadData = $formats.Count -gt 0
        }
    } catch {
        Write-Host ("[wake_codex] WARNING: " + $Context + " clipboard save failed; restore will be best-effort only: " + $_.Exception.Message)
    }
    return $state
}

function Restore-ClipboardState {
    param(
        [hashtable]$State,
        [string]$Context = "clipboard",
        [switch]$AuditOnFailure
    )
    if ($null -eq $State -or -not [bool]$State.Saved) {
        return
    }
    try {
        if ([bool]$State.HadData) {
            Invoke-ClipboardOperation -Context ($Context + " clipboard restore") -Operation {
                [System.Windows.Forms.Clipboard]::SetDataObject($State.Data, $true)
            } | Out-Null
        } else {
            Invoke-ClipboardOperation -Context ($Context + " clipboard clear") -Operation {
                [System.Windows.Forms.Clipboard]::Clear()
            } | Out-Null
        }
    } catch {
        if ($AuditOnFailure -and (Get-Command Write-PreflightAudit -ErrorAction SilentlyContinue)) {
            Write-PreflightAudit -Action "preflight_clipboard_restore_failed" -Fields @{
                save_format_count = [int]$State.FormatCount
                exception_text = $_.Exception.Message
            }
        }
        Write-Host ("[wake_codex] WARNING: " + $Context + " clipboard restore failed. Use Win+V to recover from clipboard history: " + $_.Exception.Message)
    }
}

function Set-ClipboardTextForWake {
    param(
        [string]$Text,
        [string]$Context = "wake paste"
    )
    Invoke-ClipboardOperation -Context ($Context + " clipboard text set") -Operation {
        [System.Windows.Forms.Clipboard]::SetText($Text)
    } | Out-Null
}

function Send-ClearComposerViaPostMessage {
    param($ComposerElement)

    $nativeHwnd = [IntPtr]::Zero
    try { $nativeHwnd = [IntPtr]([uint32]$ComposerElement.Current.NativeWindowHandle) } catch {}
    if ($nativeHwnd -eq [IntPtr]::Zero) {
        return $false
    }

    try {
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYDOWN,   [IntPtr][Win32Wake]::VK_CONTROL, (New-KeyLParam -ScanCode 0x1D)) | Out-Null
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYDOWN,   [IntPtr]65, (New-KeyLParam -ScanCode 0x1E)) | Out-Null
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYUP_MSG, [IntPtr]65, (New-KeyLParam -ScanCode 0x1E -KeyUp)) | Out-Null
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYUP_MSG, [IntPtr][Win32Wake]::VK_CONTROL, (New-KeyLParam -ScanCode 0x1D -KeyUp)) | Out-Null
        Start-Sleep -Milliseconds 50
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYDOWN,   [IntPtr][Win32Wake]::VK_DELETE, (New-KeyLParam -ScanCode 0x53)) | Out-Null
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYUP_MSG, [IntPtr][Win32Wake]::VK_DELETE, (New-KeyLParam -ScanCode 0x53 -KeyUp)) | Out-Null
        Start-Sleep -Milliseconds 80
        return $true
    } catch {
        Write-Host ("[wake_codex] Composer cleanup PostMessage exception: " + $_.Exception.Message)
        return $false
    }
}

function Send-BridgeMessageViaPostMessage {
    # Delivers $Value + Ctrl+Enter directly into the Chromium render widget via
    # PostMessage, bypassing the Windows foreground-window requirement entirely.
    # Called when UIA SetFocus gives element-level keyboard focus but Claude Desktop
    # holds the foreground lock and all Win32 SetForegroundWindow paths are blocked.
    param($ComposerElement, [string]$Value)

    $nativeHwnd = [IntPtr]::Zero
    try { $nativeHwnd = [IntPtr]([uint32]$ComposerElement.Current.NativeWindowHandle) } catch {}
    if ($nativeHwnd -eq [IntPtr]::Zero) {
        Write-Host "[wake_codex] PostMessage path: NativeWindowHandle is zero; cannot deliver."
        return $false
    }
    Write-Host ("[wake_codex] PostMessage path: targeting hwnd=" + $nativeHwnd)

    $pmClipboardState = Save-ClipboardState -Context "PostMessage path"

    try {
        if (-not (Send-ClearComposerViaPostMessage -ComposerElement $ComposerElement)) {
            return $false
        }

        # WM_PASTE: high-level semantic message that Chromium processes regardless of
        # foreground window status; equivalent to Edit > Paste in the app menu.
        Set-ClipboardTextForWake -Text $Value -Context "PostMessage path"
        Start-Sleep -Milliseconds 30
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_PASTE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
        Start-Sleep -Milliseconds 100

        # Ctrl+Enter to steer/submit
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYDOWN,   [IntPtr][Win32Wake]::VK_CONTROL, (New-KeyLParam -ScanCode 0x1D)) | Out-Null
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYDOWN,   [IntPtr][Win32Wake]::VK_RETURN,  (New-KeyLParam -ScanCode 0x1C)) | Out-Null
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYUP_MSG, [IntPtr][Win32Wake]::VK_RETURN,  (New-KeyLParam -ScanCode 0x1C -KeyUp)) | Out-Null
        [Win32Wake]::PostMessage($nativeHwnd, [Win32Wake]::WM_KEYUP_MSG, [IntPtr][Win32Wake]::VK_CONTROL, (New-KeyLParam -ScanCode 0x1D -KeyUp)) | Out-Null

        Write-Host "[wake_codex] PostMessage path: sent clear+paste+Ctrl+Enter."
        return $true
    } catch {
        Write-Host ("[wake_codex] PostMessage path exception: " + $_.Exception.Message)
        return $false
    } finally {
        Restore-ClipboardState -State $pmClipboardState -Context "PostMessage path" -AuditOnFailure
    }
}

# Codex Desktop placeholder strings that ProseMirror renders via CSS ::before
# but UIA TextPattern.GetText() returns as real text. Treat these as empty.
$script:CodexComposerPlaceholders = @(
    "Ask for follow-up changes",
    "Ask for follow up changes or @ to tag an agent",
    "Message Codex...",
    "Ask Codex..."
)

function Test-IsCodexPlaceholderText {
    param([string]$Text)
    $trimmed = $Text.Trim()
    foreach ($p in $script:CodexComposerPlaceholders) {
        if ($trimmed -eq $p) { return $true }
    }
    return $false
}

# Primary placeholder detector: checks UIA child element class name rather than
# matching text content. ProseMirror marks the empty-composer paragraph with
# class="placeholder" (ClassName exposed by Chromium UIA). This is stable across
# Codex Desktop releases regardless of what text the placeholder displays.
# Falls back to $false on any UIA error so the string-list check still runs.
function Test-IsPlaceholderByStructure {
    param($Composer)
    try {
        $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
        $child = $walker.GetFirstChild($Composer)
        if ($null -eq $child) { return $false }
        $cn = try { [string]$child.Current.ClassName } catch { return $false }
        if ($cn -eq 'placeholder') { return $true }
    } catch {}
    return $false
}

function Get-CodexComposerElement {
    param([IntPtr]$RootHwnd)

    try {
        Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
        Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
        $root = [System.Windows.Automation.AutomationElement]::FromHandle($RootHwnd)
        if ($null -eq $root) {
            return $null
        }

        # Fast path: FindFirst with exact class name — avoids full-tree scan.
        # Falls back to FindAll only if exact match misses (e.g. class is a compound
        # like "ProseMirror-editor" or changes between Codex Desktop versions).
        foreach ($exactName in @("ProseMirror", "ProseMirror-editor")) {
            try {
                $cond = New-Object System.Windows.Automation.PropertyCondition(
                    [System.Windows.Automation.AutomationElement]::ClassNameProperty, $exactName
                )
                $found = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
                if ($null -ne $found -and $found.Current.IsEnabled) {
                    return $found
                }
            } catch {}
        }

        # Fallback: full-tree scan filtered in PowerShell.
        $all = $root.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        )
        $fallback = $null
        for ($i = 0; $i -lt $all.Count; $i++) {
            $element = $all.Item($i)
            $className = [string]$element.Current.ClassName
            if ($className -notlike "*ProseMirror*") {
                continue
            }
            if ($null -eq $fallback -and $element.Current.IsEnabled) {
                $fallback = $element
            }
            if ($element.Current.IsEnabled -and $element.Current.IsKeyboardFocusable) {
                return $element
            }
        }
        return $fallback
    } catch {
        Write-Host ("[wake_codex] UIA composer search failed: " + $_.Exception.Message)
        return $null
    }
}

function ConvertTo-ComposerLengthBucket {
    param([int]$Length)
    if ($Length -le 0) { return "0" }
    if ($Length -le 32) { return "1-32" }
    if ($Length -le 256) { return "33-256" }
    if ($Length -le 2048) { return "257-2048" }
    return "over-2048"
}

function Get-TextHash {
    param([string]$Value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$Value)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Write-WakeTelemetry {
    param([hashtable]$Fields = @{})
    try {
        $event = [ordered]@{
            schema_version = 1
            source = "wake_codex.ps1"
            timestamp = (Get-Date).ToUniversalTime().ToString("s") + "+00:00"
            desktop_thread_id = $ThreadId
        }
        foreach ($key in $Fields.Keys) {
            $event[$key] = $Fields[$key]
        }
        $json = $event | ConvertTo-Json -Compress -Depth 8
        Write-Host ("AGENT_BRIDGE_WAKE_TELEMETRY " + $json)
    } catch {
        Write-Host ("[wake_codex] WARNING: telemetry write failed: " + $_.Exception.Message)
    }
}

function ConvertTo-CleanCodexThreadTitle {
    param([string]$Title)
    $text = ([string]$Title).Replace("`r", " ").Replace("`n", " ").Trim()
    $text = [regex]::Replace($text, "\s+", " ")
    if ([string]::IsNullOrWhiteSpace($text)) {
        return ""
    }
    # Codex sidebar accessibility names can append relative ages without a
    # separator, for example "Agent Bridge18h".
    $text = [regex]::Replace($text, "(?<=\S)(?:\d+\s*(?:s|m|h|d|w)|\d+\s*(?:mo|y))$", "").Trim()
    return $text
}

function Test-CodexThreadTitleCandidate {
    param([string]$Title)
    $text = ConvertTo-CleanCodexThreadTitle -Title $Title
    if ([string]::IsNullOrWhiteSpace($text) -or $text.Length -gt 160) {
        return $false
    }
    $blocked = @(
        "Archive chat",
        "Archive chat Pin chat",
        "Automations",
        "Back",
        "Close",
        "Collapse all",
        "Codex",
        "Edit",
        "File",
        "Filter sidebar chats",
        "Forward",
        "Help",
        "Hide sidebar",
        "Maximize",
        "Minimize",
        "New chat",
        "Plugins",
        "Projects",
        "Search",
        "Update",
        "View",
        "Window"
    )
    return $blocked -notcontains $text
}

function Get-CodexSelectedSidebarThreadTitle {
    param([System.Windows.Automation.AutomationElement]$Root)
    $rootRect = $Root.Current.BoundingRectangle
    $all = $Root.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    for ($i = 0; $i -lt $all.Count; $i++) {
        $element = $all.Item($i)
        $controlType = $element.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
        if ($controlType -ne "ListItem") {
            continue
        }
        $name = ConvertTo-CleanCodexThreadTitle -Title ([string]$element.Current.Name)
        if (-not (Test-CodexThreadTitleCandidate -Title $name)) {
            continue
        }
        $className = [string]$element.Current.ClassName
        if ($className -notlike "*after:block*") {
            continue
        }
        $rect = $element.Current.BoundingRectangle
        if ($rect.Width -le 0 -or $rect.Height -le 0) {
            continue
        }
        if ($rect.X -lt ($rootRect.X - 4) -or $rect.X -gt ($rootRect.X + $rootRect.Width)) {
            continue
        }
        if ($rect.Y -lt ($rootRect.Y - 4) -or $rect.Y -gt ($rootRect.Y + $rootRect.Height)) {
            continue
        }
        $children = $element.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        )
        for ($j = 0; $j -lt $children.Count; $j++) {
            $child = $children.Item($j)
            $childType = $child.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
            if ($childType -ne "Button") {
                continue
            }
            $childClass = [string]$child.Current.ClassName
            if ($childClass -match "(^|\s)bg-token-list-hover-background(\s|$)") {
                return $name
            }
        }
    }
    return ""
}

function Get-CodexTopHeaderThreadTitle {
    param([System.Windows.Automation.AutomationElement]$Root)
    $rootRect = $Root.Current.BoundingRectangle
    $all = $Root.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    for ($i = 0; $i -lt $all.Count; $i++) {
        $element = $all.Item($i)
        $controlType = $element.Current.ControlType.ProgrammaticName -replace "^ControlType\.", ""
        if ($controlType -ne "Text") {
            continue
        }
        $rect = $element.Current.BoundingRectangle
        if ($rect.Width -le 0 -or $rect.Height -le 0) {
            continue
        }
        if ($rect.Y -lt $rootRect.Y -or $rect.Y -gt ($rootRect.Y + 260)) {
            continue
        }
        $name = ConvertTo-CleanCodexThreadTitle -Title ([string]$element.Current.Name)
        if (Test-CodexThreadTitleCandidate -Title $name) {
            return $name
        }
    }
    return ""
}

function Get-CodexThreadTitleSnapshot {
    param(
        [IntPtr]$RootHwnd,
        [string]$WindowTitle = ""
    )
    $uiaName = ""
    $sidebarTitle = ""
    $headerTitle = ""
    try {
        Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
        Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
        $root = [System.Windows.Automation.AutomationElement]::FromHandle($RootHwnd)
        if ($null -ne $root) {
            $sidebarTitle = Get-CodexSelectedSidebarThreadTitle -Root $root
            if ([string]::IsNullOrWhiteSpace($sidebarTitle)) {
                $headerTitle = Get-CodexTopHeaderThreadTitle -Root $root
            }
            $uiaName = ConvertTo-CleanCodexThreadTitle -Title ([string]$root.Current.Name)
        }
    } catch {
        Write-Host ("[wake_codex] WARNING: UIA thread title read failed: " + $_.Exception.Message)
    }
    $windowTitleClean = ConvertTo-CleanCodexThreadTitle -Title $WindowTitle
    $title = if (-not [string]::IsNullOrWhiteSpace($sidebarTitle)) {
        $sidebarTitle
    } elseif (-not [string]::IsNullOrWhiteSpace($headerTitle)) {
        $headerTitle
    } elseif (-not [string]::IsNullOrWhiteSpace($uiaName)) {
        $uiaName
    } else {
        $windowTitleClean
    }
    $source = if (-not [string]::IsNullOrWhiteSpace($sidebarTitle)) {
        "codex_app_dom_sidebar_selected_thread"
    } elseif (-not [string]::IsNullOrWhiteSpace($headerTitle)) {
        "codex_app_dom_top_header"
    } elseif (-not [string]::IsNullOrWhiteSpace($uiaName)) {
        "uia_root_name"
    } else {
        "win32_window_text"
    }
    return @{
        Title = $title
        Source = $source
        UiaName = $uiaName
        WindowTitle = $windowTitleClean
    }
}

function Get-ProcessNameForHwnd {
    param([IntPtr]$Hwnd)
    if ($Hwnd -eq [IntPtr]::Zero) {
        return ""
    }
    $processId = 0
    [Win32Wake]::GetWindowThreadProcessId($Hwnd, [ref]$processId) | Out-Null
    if ($processId -le 0) {
        return ""
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) {
        return ""
    }
    return [string]$process.ProcessName
}

function Test-ThreadTitleEquals {
    param(
        [string]$Actual,
        [string]$Expected
    )
    if ([string]::IsNullOrWhiteSpace($Actual) -or [string]::IsNullOrWhiteSpace($Expected)) {
        return $false
    }
    return $Actual.Trim().Equals($Expected.Trim(), [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-GenericCodexThreadTitle {
    param([string]$Title)
    $value = ($Title -as [string])
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $true
    }
    return $value.Trim().Equals("Codex", [System.StringComparison]::OrdinalIgnoreCase)
}

function Write-ThreadTitleUnknown {
    param(
        [hashtable]$Snapshot,
        [string]$Reason
    )
    $title = [string]$Snapshot.Title
    Write-WakeTelemetry -Fields @{
        action = "thread_title_unknown"
        desktop_thread_title = $title
        desktop_thread_title_source = [string]$Snapshot.Source
        desktop_window_title = [string]$Snapshot.WindowTitle
        expected_project_token = $ExpectedProjectToken
        title_project_match = $null
        title_project_match_state = $Reason
    }
    Write-PreflightAudit -Action "targeted_wake_title_unknown" -Fields @{
        desktop_thread_title = $title
        expected_project_token = $ExpectedProjectToken
        reason = $Reason
        hard_fail = [bool]$RequireTitleMatch
    }
    $detail = "project/thread title proof unavailable (" + $Reason + "); title='" + $title + "'"
    if ($RequireTitleMatch) {
        Write-Host ("[wake_codex] Targeted wake refused: " + $detail)
        exit 3
    }
    if ($WarnOnTitleMismatch) {
        Write-Host ("[wake_codex] WARNING: " + $detail + "; continuing because title match is warn-only.")
    }
}

function Get-ExpectedThreadTitleFromRuntime {
    if (-not [string]::IsNullOrWhiteSpace($ExpectedThreadTitle)) {
        if (Test-GenericCodexThreadTitle -Title $ExpectedThreadTitle) {
            return ""
        }
        return $ExpectedThreadTitle
    }
    if ([string]::IsNullOrWhiteSpace($StateDir) -or [string]::IsNullOrWhiteSpace($ThreadId)) {
        return ""
    }
    try {
        $bridgeRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $StateDir))
        $runtimePath = Join-Path $bridgeRoot "peer-codex.runtime.json"
        if (-not (Test-Path -LiteralPath $runtimePath)) {
            return ""
        }
        $runtime = Get-Content -LiteralPath $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$runtime.desktop_thread_id -ne $ThreadId) {
            return ""
        }
        if ($runtime.PSObject.Properties.Name -contains "desktop_thread_title_project_match" -and $runtime.desktop_thread_title_project_match -eq $false) {
            return ""
        }
        if (Test-GenericCodexThreadTitle -Title ([string]$runtime.desktop_thread_title)) {
            return ""
        }
        return [string]$runtime.desktop_thread_title
    } catch {
        Write-Host ("[wake_codex] WARNING: expected thread title runtime read failed: " + $_.Exception.Message)
        return ""
    }
}

function Test-ForegroundCodexNavigationSafety {
    param(
        [IntPtr]$ForegroundHwnd,
        [string]$ForegroundTitle
    )
    $result = @{
        Ok = $true
        SkipNavigation = $false
        Reason = "not_foreground_codex"
        PreviousThreadTitle = ""
        ExpectedThreadTitle = ""
    }
    if (-not $ProtectForegroundCodexThread) {
        return $result
    }
    $foregroundProcessName = Get-ProcessNameForHwnd -Hwnd $ForegroundHwnd
    if ($foregroundProcessName -ne $ProcessName) {
        return $result
    }
    if (-not (Test-CodexThreadId -Value $ThreadId)) {
        $result.Ok = $false
        $result.SkipNavigation = $false
        $result.Reason = "foreground_codex_target_thread_unavailable"
        return $result
    }

    $snapshot = Get-CodexThreadTitleSnapshot -RootHwnd $ForegroundHwnd -WindowTitle $ForegroundTitle
    $expectedTitle = Get-ExpectedThreadTitleFromRuntime
    $result.PreviousThreadTitle = [string]$snapshot.Title
    $result.ExpectedThreadTitle = [string]$expectedTitle

    if ((-not (Test-GenericCodexThreadTitle -Title $expectedTitle)) -and (Test-ThreadTitleEquals -Actual ([string]$snapshot.Title) -Expected $expectedTitle)) {
        $result.Ok = $true
        $result.SkipNavigation = $true
        $result.Reason = "foreground_codex_already_target"
        return $result
    }

    if (Test-CodexThreadId -Value $RestoreThreadId) {
        $result.Ok = $true
        $result.SkipNavigation = $false
        $result.Reason = "restore_thread_id_available"
        return $result
    }

    if ($AllowForegroundCodexThreadDisplacement) {
        $result.Ok = $true
        $result.SkipNavigation = $false
        $result.Reason = "foreground_codex_delivery_priority_no_restore"
        return $result
    }

    $result.Ok = $false
    $result.SkipNavigation = $false
    $result.Reason = "foreground_codex_restore_thread_unavailable"
    return $result
}

function Write-ForegroundCodexDeliveryPriorityAudit {
    param([hashtable]$NavigationSafety)

    Write-StageEvent "STAGE4_DELIVERY_PRIORITY_DISPLACEMENT" "restore_thread_id=missing"
    Write-PreflightAudit -Action "targeted_wake_delivery_priority_no_restore" -Fields @{
        previous_desktop_thread_title = [string]$NavigationSafety.PreviousThreadTitle
        expected_desktop_thread_title = [string]$NavigationSafety.ExpectedThreadTitle
        target_thread_id = [string]$ThreadId
        restore_thread_id_present = $false
    }
    Write-WakeTelemetry -Fields @{
        action = "foreground_codex_delivery_priority_no_restore"
        previous_desktop_thread_title = [string]$NavigationSafety.PreviousThreadTitle
        expected_desktop_thread_title = [string]$NavigationSafety.ExpectedThreadTitle
    }
    Write-Host "[wake_codex] Delivery priority: foreground Codex may be displaced because no exact RestoreThreadId is available."
}

function Test-TitleContainsProjectToken {
    param(
        [string]$Title,
        [string]$Token
    )
    if ([string]::IsNullOrWhiteSpace($Token)) {
        return $null
    }
    if ([string]::IsNullOrWhiteSpace($Title)) {
        return $false
    }
    return $Title.IndexOf($Token, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Invoke-ThreadTitleProjectCertification {
    param([hashtable]$Snapshot)
    if ([string]::IsNullOrWhiteSpace($ExpectedProjectToken)) {
        return
    }
    $title = [string]$Snapshot.Title
    if (Test-GenericCodexThreadTitle -Title $title) {
        Write-ThreadTitleUnknown -Snapshot $Snapshot -Reason "generic_codex_title"
        return
    }
    $matches = Test-TitleContainsProjectToken -Title $title -Token $ExpectedProjectToken
    if ($null -eq $matches) {
        Write-ThreadTitleUnknown -Snapshot $Snapshot -Reason "empty_or_unreadable_title"
        return
    }
    Write-WakeTelemetry -Fields @{
        action = "thread_title_certified"
        desktop_thread_title = $title
        desktop_thread_title_source = [string]$Snapshot.Source
        desktop_window_title = [string]$Snapshot.WindowTitle
        expected_project_token = $ExpectedProjectToken
        title_project_match = $matches
    }
    if ($matches) {
        Write-PreflightAudit -Action "targeted_wake_title_verified" -Fields @{
            desktop_thread_title = $title
            expected_project_token = $ExpectedProjectToken
        }
        return
    }
    $fields = @{
        desktop_thread_title = $title
        expected_project_token = $ExpectedProjectToken
        hard_fail = [bool]$RequireTitleMatch
    }
    Write-PreflightAudit -Action "targeted_wake_title_mismatch" -Fields $fields
    $detail = "expected project token '" + $ExpectedProjectToken + "' not found in title '" + $title + "'"
    if ($RequireTitleMatch) {
        Write-Host ("[wake_codex] Targeted wake refused: " + $detail)
        exit 3
    }
    if ($WarnOnTitleMismatch) {
        Write-Host ("[wake_codex] WARNING: title/project mismatch; continuing because title match is warn-only: " + $detail)
    }
}

function Write-PreflightAudit {
    param(
        [string]$Action,
        [hashtable]$Fields = @{}
    )
    try {
        if ([string]::IsNullOrWhiteSpace($StateDir)) {
            return
        }
        $dir = [System.Environment]::ExpandEnvironmentVariables($StateDir)
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        $event = @{
            schema_version = 2
            id = [guid]::NewGuid().ToString()
            timestamp = (Get-Date).ToUniversalTime().ToString("s") + "+00:00"
            action = $Action
            agent = "codex"
            message_id = $MessageId
            accepted = $true
            tenant_id = "local-default"
            originator_machine_id = "local-machine"
        }
        foreach ($key in $Fields.Keys) {
            $event[$key] = $Fields[$key]
        }
        $line = $event | ConvertTo-Json -Compress -Depth 8
        Add-Content -Path (Join-Path $dir "messages.jsonl") -Value $line -Encoding UTF8
    } catch {
        Write-Host ("[wake_codex] WARNING: preflight audit failed: " + $_.Exception.Message)
    }
}

function Get-CodexComposerTextReadOnly {
    param($Composer)
    if ($null -eq $Composer) {
        throw "composer element is null"
    }
    $textPattern = $null
    if ($Composer.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$textPattern)) {
        return [string]$textPattern.DocumentRange.GetText(-1)
    }
    $valuePattern = $null
    if ($Composer.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern)) {
        return [string]$valuePattern.Current.Value
    }
    return [string]$Composer.Current.Name
}

# Reads UIA color and child-element structure from the composer for calibration.
# Used only when -AuditDraftText is set. Never throws — all paths are try/catch.
function Get-CodexComposerColorHint {
    param($Composer)
    $hint = @{
        fg_color_raw = 'unsupported'
        fg_color_rgb = $null
        children     = @()
    }
    # ForegroundColorAttribute on the full document range
    try {
        $tp = $null
        if ($Composer.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$tp)) {
            $attr = $tp.DocumentRange.GetAttributeValue(
                [System.Windows.Automation.TextPatternIdentifiers]::ForegroundColorAttribute)
            if ($attr -ne [System.Windows.Automation.AutomationElement]::NotSupported) {
                $c = [int]$attr
                $hint.fg_color_raw = $c
                $hint.fg_color_rgb = @{
                    r = $c -band 0xFF
                    g = ($c -shr 8) -band 0xFF
                    b = ($c -shr 16) -band 0xFF
                }
            }
        }
    } catch {}
    # Immediate children: ControlType, ClassName, IsContentElement, truncated Name
    try {
        $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
        $child = $walker.GetFirstChild($Composer)
        $childList = [System.Collections.Generic.List[object]]::new()
        $limit = 10
        while ($null -ne $child -and $childList.Count -lt $limit) {
            $ct  = try { $child.Current.ControlType.ProgrammaticName } catch { 'unknown' }
            $cn  = try { [string]$child.Current.ClassName } catch { '' }
            $ice = try { [bool]$child.Current.IsContentElement } catch { $null }
            $nm  = try { $raw = [string]$child.Current.Name; ($raw -replace "`n", '\n').Substring(0, [Math]::Min(80, $raw.Length)) } catch { '' }
            $childList.Add(@{ control_type = $ct; class_name = $cn; is_content_element = $ice; name_preview = $nm })
            $child = $walker.GetNextSibling($child)
        }
        $hint.children = $childList.ToArray()
    } catch {}
    return $hint
}

function Invoke-ComposerPreflight {
    param([IntPtr]$RootHwnd)

    $priorityCaps = @{ urgent = 45; normal = 120; low = 300 }
    $capSeconds = if ($PreflightCapSeconds -gt 0) { $PreflightCapSeconds } else { [int]$priorityCaps[$Priority] }

    # Smart debounce thresholds:
    #   idle-empty  → FastPathIdleSeconds (default 1s): composer is clearly idle, fire quickly
    #   idle-with-draft → DraftStabilitySeconds (default 5s): protect in-progress content
    # These replace the flat priority-based stability wait from the old design.
    $fastPathSeconds   = [Math]::Max(0, $FastPathIdleSeconds)
    $draftStableSeconds = [Math]::Max(0, $DraftStabilitySeconds)

    $started    = Get-Date
    $lastText   = $null
    $lastState  = $null
    $stableSince = $null
    $cachedComposer = $null   # reuse UIA element ref across polls — avoids re-scanning

    while ((New-TimeSpan -Start $started -End (Get-Date)).TotalSeconds -lt $capSeconds) {
        # Reuse cached composer element; only re-scan if null (first call or element went stale).
        $composer = $cachedComposer
        if ($null -eq $composer) {
            # Retry up to 3x at 200ms — deeplink navigation briefly makes the UIA tree
            # unavailable. Retrying here avoids the 5s+ watcher retry cycle on exit 16.
            for ($uiaRetry = 0; $uiaRetry -lt 3; $uiaRetry++) {
                $composer = Get-CodexComposerElement -RootHwnd $RootHwnd
                if ($null -ne $composer) { break }
                if ($uiaRetry -lt 2) { Start-Sleep -Milliseconds 200 }
            }
        }
        if ($null -eq $composer) {
            Write-PreflightAudit -Action "preflight_aborted_policy_state" -Fields @{
                abort_reason = "uia_unavailable"; priority = $Priority
            }
            if ($AllowLegacyNoPreflight) {
                Write-Host "[wake_codex] WARNING: UIA unavailable; AllowLegacyNoPreflight enabled, continuing with legacy wake path."
                return @{ State = "legacy-no-preflight"; DraftText = ""; PreserveDraft = $false; Composer = $null }
            }
            Write-Host "[wake_codex] UIA composer unavailable. Deferring wake without intrusive typing."
            exit 16
        }
        $cachedComposer = $composer

        try {
            $text = Get-CodexComposerTextReadOnly -Composer $composer
        } catch {
            # Element may have gone stale — clear cache so next iteration re-scans.
            $cachedComposer = $null
            Write-PreflightAudit -Action "preflight_aborted_policy_state" -Fields @{
                abort_reason = "uia_unavailable"; priority = $Priority; exception_text = $_.Exception.Message
            }
            if ($AllowLegacyNoPreflight) {
                Write-Host "[wake_codex] WARNING: UIA read failed; AllowLegacyNoPreflight enabled, continuing with legacy wake path."
                return @{ State = "legacy-no-preflight"; DraftText = ""; PreserveDraft = $false; Composer = $null }
            }
            Write-Host ("[wake_codex] UIA composer read failed. Deferring wake: " + $_.Exception.Message)
            exit 16
        }

        $trimmed = ([string]$text).Trim()
        $isPlaceholder = (Test-IsPlaceholderByStructure -Composer $composer) -or (Test-IsCodexPlaceholderText -Text $trimmed)
        $composerFocused = $false
        try { $composerFocused = [bool]$composer.Current.HasKeyboardFocus } catch {}
        $state = if ([string]::IsNullOrWhiteSpace($trimmed) -or $isPlaceholder) {
            "idle-empty"
        } elseif ($composerFocused) {
            "actively-typing"  # user has focus in the composer right now
        } else {
            "idle-with-draft"  # draft present but user is not focused here
        }

        # User is actively composing — extend the cap so we wait patiently while they type.
        # The 5s inactivity check (DraftStabilitySeconds) still fires as soon as they pause.
        if ($state -eq "actively-typing" -and $capSeconds -lt $ActiveTypingMaxWaitSeconds) {
            $capSeconds = $ActiveTypingMaxWaitSeconds
            Write-StageEvent "PREFLIGHT_ACTIVE_TYPING_DETECTED" ("cap_extended_to=" + $capSeconds + "s")
        }

        if ($null -ne $lastText -and $text -eq $lastText) {
            if ($null -eq $stableSince) { $stableSince = Get-Date }
        } else {
            $stableSince = Get-Date
            if ($null -ne $lastText) {
                Write-PreflightAudit -Action "preflight_deferred_active_typing" -Fields @{
                    current_state = "actively-typing"; priority = $Priority
                }
            }
        }
        $lastText  = $text
        $lastState = $state
        $stableFor = (New-TimeSpan -Start $stableSince -End (Get-Date)).TotalSeconds

        $threshold = if ($state -eq "idle-empty") { $fastPathSeconds } else { $draftStableSeconds }
        if ($stableFor -ge $threshold) {
            $preflightAuditFields = @{
                state = $state
                composer_text_hash = (Get-TextHash -Value ([string]$text))
                composer_length_bucket = (ConvertTo-ComposerLengthBucket -Length ([string]$text).Length)
                composer_line_count = (([string]$text -split "`n").Count)
                priority = $Priority
                idle_seconds_observed = [Math]::Round($stableFor, 1)
                fast_path = ($state -eq "idle-empty")
            }
            if ($AuditDraftText) {
                $preflightAuditFields["composer_text"] = [string]$text
                $colorHint = Get-CodexComposerColorHint -Composer $composer
                $preflightAuditFields["composer_fg_color_raw"] = $colorHint.fg_color_raw
                if ($null -ne $colorHint.fg_color_rgb) {
                    $preflightAuditFields["composer_fg_rgb"] = $colorHint.fg_color_rgb
                }
                $preflightAuditFields["composer_children"] = $colorHint.children
            }
            Write-PreflightAudit -Action "preflight_state_detected" -Fields $preflightAuditFields
            return @{
                State = $state
                DraftText = if ($state -ne "idle-empty") { [string]$text } else { "" }
                PreserveDraft = ($state -ne "idle-empty")
                Composer = $cachedComposer
            }
        }

        # Poll at 500ms when actively waiting — tighter debounce than the old 1-5s.
        Start-Sleep -Milliseconds 500
    }

    Write-PreflightAudit -Action "preflight_forced_after_cap" -Fields @{
        priority = $Priority; cap_seconds = $capSeconds
        draft_preserved = -not [string]::IsNullOrWhiteSpace($lastText)
    }
    return @{
        State = "forced-after-cap"
        DraftText = [string]$lastText
        PreserveDraft = -not [string]::IsNullOrWhiteSpace($lastText)
        Composer = $cachedComposer
    }
}

function Get-TargetVerificationSnapshot {
    param([IntPtr]$RootHwnd)

    $foreground = [Win32Wake]::GetForegroundWindow()
    if ($foreground -ne $RootHwnd) {
        return @{
            Ok = $false
            Reason = "foreground_mismatch"
            ForegroundHwnd = [string]$foreground
            TargetHwnd = [string]$RootHwnd
        }
    }

    $composer = Get-CodexComposerElement -RootHwnd $RootHwnd
    if ($null -eq $composer) {
        return @{ Ok = $false; Reason = "composer_unavailable" }
    }
    if (-not $composer.Current.IsEnabled) {
        return @{ Ok = $false; Reason = "composer_disabled" }
    }

    try {
        $text = Get-CodexComposerTextReadOnly -Composer $composer
    } catch {
        return @{
            Ok = $false
            Reason = "composer_read_failed"
            ExceptionText = $_.Exception.Message
        }
    }

    return @{
        Ok = $true
        Reason = "verified"
        Composer = $composer
        Text = [string]$text
        TextHash = (Get-TextHash -Value ([string]$text))
        TextLength = ([string]$text).Length
        VerifiedAt = Get-Date
    }
}

function Invoke-TargetPreSendVerification {
    param([IntPtr]$RootHwnd)

    $first = Get-TargetVerificationSnapshot -RootHwnd $RootHwnd
    if (-not $first.Ok) {
        Write-PreflightAudit -Action "targeted_wake_presend_verification_failed" -Fields @{
            abort_reason = $first.Reason
            foreground_hwnd = $first.ForegroundHwnd
            target_hwnd = $first.TargetHwnd
        }
        Write-Host ("[wake_codex] Target verification failed before typing: " + $first.Reason)
        exit 16
    }

    if ($VerifyTargetTwice) {
        Start-Sleep -Milliseconds ([Math]::Max(0, $VerifyTargetGapMilliseconds))
        $second = Get-TargetVerificationSnapshot -RootHwnd $RootHwnd
        if (-not $second.Ok) {
            Write-PreflightAudit -Action "targeted_wake_presend_verification_failed" -Fields @{
                abort_reason = $second.Reason
                foreground_hwnd = $second.ForegroundHwnd
                target_hwnd = $second.TargetHwnd
            }
            Write-Host ("[wake_codex] Target verification failed on second read before typing: " + $second.Reason)
            exit 16
        }
        if ($second.TextHash -ne $first.TextHash) {
            Write-PreflightAudit -Action "targeted_wake_presend_verification_failed" -Fields @{
                abort_reason = "composer_changed_between_verifications"
                first_length_bucket = (ConvertTo-ComposerLengthBucket -Length $first.TextLength)
                second_length_bucket = (ConvertTo-ComposerLengthBucket -Length $second.TextLength)
            }
            Write-Host "[wake_codex] Target verification failed before typing: composer changed during verification window."
            exit 16
        }
        Write-PreflightAudit -Action "targeted_wake_presend_verified" -Fields @{
            verified_twice = $true
            verify_gap_milliseconds = $VerifyTargetGapMilliseconds
            composer_text_hash = $second.TextHash
            composer_length_bucket = (ConvertTo-ComposerLengthBucket -Length $second.TextLength)
        }
        return $second
    }

    Write-PreflightAudit -Action "targeted_wake_presend_verified" -Fields @{
        verified_twice = $false
        composer_text_hash = $first.TextHash
        composer_length_bucket = (ConvertTo-ComposerLengthBucket -Length $first.TextLength)
    }
    return $first
}

function Test-CodexWindowContainsText {
    param(
        [IntPtr]$RootHwnd,
        [string]$Value,
        [int]$TimeoutMilliseconds = 2000
    )

    $deadline = (Get-Date).AddMilliseconds([Math]::Max(100, $TimeoutMilliseconds))
    while ((Get-Date) -lt $deadline) {
        try {
            Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
            Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
            $root = [System.Windows.Automation.AutomationElement]::FromHandle($RootHwnd)
            if ($null -ne $root) {
                $all = $root.FindAll(
                    [System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition
                )
                for ($i = 0; $i -lt $all.Count; $i++) {
                    $element = $all.Item($i)
                    $name = [string]$element.Current.Name
                    if ($name -and $name.Contains($Value)) {
                        return $true
                    }
                    $valuePattern = $null
                    if ($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern)) {
                        $valueText = [string]$valuePattern.Current.Value
                        if ($valueText -and $valueText.Contains($Value)) {
                            return $true
                        }
                    }
                    $textPattern = $null
                    if ($element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$textPattern)) {
                        $text = [string]$textPattern.DocumentRange.GetText(4096)
                        if ($text -and $text.Contains($Value)) {
                            return $true
                        }
                    }
                }
            }
        } catch {
            Write-Host ("[wake_codex] WARNING: post-typing verification read failed: " + $_.Exception.Message)
            return $false
        }
        Start-Sleep -Milliseconds 100
    }
    return $false
}

function Test-CodexWakePostflight {
    param(
        [IntPtr]$RootHwnd,
        [string]$Value,
        [int]$TimeoutMilliseconds = 2500
    )

    $deadline = (Get-Date).AddMilliseconds([Math]::Max(250, $TimeoutMilliseconds))
    $expected = ([string]$Value).Trim()
    while ((Get-Date) -lt $deadline) {
        try {
            $composer = Get-CodexComposerElement -RootHwnd $RootHwnd
            if ($null -ne $composer) {
                $text = Get-CodexComposerTextReadOnly -Composer $composer
                $trimmed = ([string]$text).Trim()
                if (-not [string]::IsNullOrWhiteSpace($expected) -and $trimmed.Equals($expected, [System.StringComparison]::Ordinal)) {
                    return @{ Ok = $false; Reason = "wake_command_still_in_composer" }
                }
                $isPlaceholder = (Test-IsPlaceholderByStructure -Composer $composer) -or (Test-IsCodexPlaceholderText -Text $trimmed)
                if ([string]::IsNullOrWhiteSpace($trimmed) -or $isPlaceholder) {
                    return @{ Ok = $true; Reason = "composer_empty_after_submit" }
                }
            }
        } catch {
            return @{ Ok = $false; Reason = "postflight_read_failed"; ExceptionText = $_.Exception.Message }
        }
        if (Test-CodexWindowContainsText -RootHwnd $RootHwnd -Value $Value -TimeoutMilliseconds 250) {
            return @{ Ok = $true; Reason = "wake_command_rendered" }
        }
        Start-Sleep -Milliseconds 100
    }
    return @{ Ok = $false; Reason = "postflight_timeout" }
}

function Clear-InjectedWakeTextIfPresent {
    param(
        [IntPtr]$RootHwnd,
        $ComposerElement,
        [string]$Value,
        [string]$DeliveryMode = "sendkeys",
        [string]$Reason = "unknown"
    )

    $result = @{
        Attempted = $false
        Cleared = $false
        Reason = $Reason
        CleanupMode = ""
        ComposerLengthBucket = "unknown"
    }
    $composer = $ComposerElement
    if ($null -eq $composer) {
        $composer = Get-CodexComposerElement -RootHwnd $RootHwnd
    }
    if ($null -eq $composer) {
        $result.CleanupMode = "unavailable"
        Write-PreflightAudit -Action "targeted_wake_injected_text_cleanup_skipped" -Fields @{
            cleanup_reason = $Reason
            skip_reason = "composer_unavailable"
        }
        return $result
    }

    $text = ""
    try {
        $text = [string](Get-CodexComposerTextReadOnly -Composer $composer)
    } catch {
        $result.CleanupMode = "read_failed"
        Write-PreflightAudit -Action "targeted_wake_injected_text_cleanup_skipped" -Fields @{
            cleanup_reason = $Reason
            skip_reason = "composer_read_failed"
            exception_text = $_.Exception.Message
        }
        return $result
    }

    $trimmed = $text.Trim()
    $expected = ([string]$Value).Trim()
    $result.ComposerLengthBucket = ConvertTo-ComposerLengthBucket -Length $trimmed.Length
    if ([string]::IsNullOrWhiteSpace($expected) -or -not $trimmed.Equals($expected, [System.StringComparison]::Ordinal)) {
        $result.CleanupMode = "exact_match_not_found"
        Write-PreflightAudit -Action "targeted_wake_injected_text_cleanup_skipped" -Fields @{
            cleanup_reason = $Reason
            skip_reason = "composer_did_not_exactly_match_wake_text"
            composer_length_bucket = $result.ComposerLengthBucket
        }
        return $result
    }

    $result.Attempted = $true
    $cleanupMode = "postmessage"
    $cleared = Send-ClearComposerViaPostMessage -ComposerElement $composer
    if (-not $cleared) {
        $cleanupMode = "sendkeys"
        try {
            $composer.SetFocus()
            Start-Sleep -Milliseconds 80
        } catch {}
        if ([Win32Wake]::GetForegroundWindow() -eq $RootHwnd) {
            try {
                [System.Windows.Forms.SendKeys]::SendWait("^a")
                Start-Sleep -Milliseconds 60
                [System.Windows.Forms.SendKeys]::SendWait("{DELETE}")
                Start-Sleep -Milliseconds 100
                $cleared = $true
            } catch {
                Write-Host ("[wake_codex] Composer cleanup SendKeys exception: " + $_.Exception.Message)
                $cleared = $false
            }
        }
    }

    $result.CleanupMode = $cleanupMode
    if ($cleared) {
        try {
            $afterText = [string](Get-CodexComposerTextReadOnly -Composer $composer)
            $afterTrimmed = $afterText.Trim()
            $isPlaceholder = (Test-IsPlaceholderByStructure -Composer $composer) -or (Test-IsCodexPlaceholderText -Text $afterTrimmed)
            $cleared = [string]::IsNullOrWhiteSpace($afterTrimmed) -or $isPlaceholder
        } catch {
            $cleared = $true
        }
    }
    $result.Cleared = [bool]$cleared
    Write-PreflightAudit -Action $(if ($result.Cleared) { "targeted_wake_injected_text_cleared" } else { "targeted_wake_injected_text_cleanup_failed" }) -Fields @{
        cleanup_reason = $Reason
        cleanup_mode = $cleanupMode
        composer_length_bucket = $result.ComposerLengthBucket
    }
    return $result
}

function Send-BridgeMessageKeys {
    param(
        [string]$Value,
        [string]$DraftText = "",
        [switch]$PreserveDraft
    )

    $clipboardState = Save-ClipboardState -Context "SendKeys path"
    $savedFormatCount = [int]$clipboardState.FormatCount

    try {
        [System.Windows.Forms.SendKeys]::SendWait("^a")
        Start-Sleep -Milliseconds 60
        [System.Windows.Forms.SendKeys]::SendWait("{DELETE}")
        Start-Sleep -Milliseconds 60

        # Always paste via clipboard (single atomic Ctrl+V) rather than
        # character-by-character SendKeys. This collapses the interleave race
        # window from ~18 keystrokes to one compound event, and avoids AV
        # heuristics that flag character-sequence injection into foreign windows.
        Set-ClipboardTextForWake -Text $Value -Context "SendKeys path"
        [System.Windows.Forms.SendKeys]::SendWait("^v")
        Start-Sleep -Milliseconds 100
        [System.Windows.Forms.SendKeys]::SendWait("^{ENTER}")

        if ($PreserveDraft -and -not [string]::IsNullOrWhiteSpace($DraftText)) {
            Start-Sleep -Milliseconds 250
            Set-ClipboardTextForWake -Text $DraftText -Context "SendKeys draft restore"
            [System.Windows.Forms.SendKeys]::SendWait("^v")
            $draftAuditFields = @{ save_format_count = $savedFormatCount; restore_succeeded = $true }
            if ($AuditDraftText) { $draftAuditFields["draft_text"] = $DraftText }
            Write-PreflightAudit -Action "preflight_draft_preserved" -Fields $draftAuditFields
        }
    } finally {
        Restore-ClipboardState -State $clipboardState -Context "SendKeys path" -AuditOnFailure
    }
}

function Invoke-CodexComposerUiaFallback {
    param(
        [IntPtr]$RootHwnd,
        [string]$Value,
        [string]$DraftText = "",
        [bool]$PreserveDraft = $false,
        [switch]$DryRun
    )

    $composer = Get-CodexComposerElement -RootHwnd $RootHwnd
    if ($null -eq $composer) {
        Write-Host "[wake_codex] UIA fallback could not find ProseMirror composer."
        return $false
    }

    try {
        $composer.SetFocus()
        Start-Sleep -Milliseconds 150
        if ($DryRun) {
            Write-Host ("[wake_codex] DryRun. UIA fallback would send: " + $Value + " + Ctrl+Enter (steer).")
            return $true
        }
        Send-BridgeMessageKeys -Value $Value -DraftText $DraftText -PreserveDraft:$PreserveDraft
        Write-Host ("[wake_codex] UIA fallback sent: " + $Value + " + Ctrl+Enter (steer; composer cleared first)")
        return $true
    } catch {
        Write-Host ("[wake_codex] UIA fallback failed: " + $_.Exception.Message)
        return $false
    }
}

function Test-Win32InputSize {
    $sample = New-Object 'Win32Wake+INPUT'
    $size = [System.Runtime.InteropServices.Marshal]::SizeOf($sample)
    Write-Host ("[wake_codex] Win32 INPUT size=" + $size)
    return $size -gt 0
}

if ($TestInputSize) {
    if (Test-Win32InputSize) {
        exit 0
    }
    exit 1
}

if ($PrintInnerCommand -and -not $RunInnerWake) {
    Write-Host (New-InnerWakeCommand)
    exit 0
}

Assert-TargetedWakePolicy

Write-StageEvent "START" ("message_id=" + $MessageId + " priority=" + $Priority + " idle_threshold=" + $IdleThresholdSeconds + "s")

# --- Stage 1: locate ---
Write-StageEvent "STAGE1_FIND" "locating Codex window"
$codex = Get-CodexWindow
if (-not $codex) {
    Write-StageEvent "STAGE1_ABORT" "no Codex window found"
    Write-Host "[wake_codex] No Codex window found. Skipping."
    exit 0
}
$codexHwnd  = $codex.MainWindowHandle
$codexTitle = Get-WindowTitle -hWnd $codexHwnd
Write-StageEvent "STAGE1_OK" ("PID=" + $codex.Id + " hwnd=" + $codexHwnd)
Write-Host ("[wake_codex] Found Codex: PID=" + $codex.Id + " hwnd=" + $codexHwnd + " title=" + $codexTitle)

if ($FindOnly) {
    Write-Host "[wake_codex] FindOnly mode. Exiting."
    exit 0
}

if (-not $RunInnerWake) {
    # --- Stage 2: lock - only one wake instance polls/injects at a time ---
    $lockDir = Split-Path -Parent $LockFile
    if (-not (Test-Path $lockDir)) {
        New-Item -ItemType Directory -Path $lockDir | Out-Null
    }

    if (Test-Path $LockFile) {
        $lockAge = (Get-Date) - (Get-Item $LockFile).LastWriteTime
        if ($lockAge.TotalSeconds -lt ($MaxWaitSeconds + 60)) {
            Write-Host ("[wake_codex] Another wake instance is active (lock age=" + [int]$lockAge.TotalSeconds + "s). Skipping; it will pick up our message via 'check bridge inbox'.")
            exit 0
        } else {
            Write-Host "[wake_codex] Stale lock detected (older than max wait). Taking over."
            Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
        }
    }

    # Take lock - store PID so stale-detection can verify
    $PID | Set-Content -Path $LockFile -NoNewline

    try {
        $innerCommand = New-InnerWakeCommand
        $encodedCommand = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($innerCommand))
        $argumentList = @(
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            $encodedCommand
        )

        $child = Start-Process -FilePath "powershell.exe" -ArgumentList $argumentList -WindowStyle Hidden -PassThru
        if (-not $child.WaitForExit($TotalRuntimeTimeoutSeconds * 1000)) {
            Write-Host ("[wake_codex] Total runtime exceeded " + $TotalRuntimeTimeoutSeconds + "s. Killing stuck wake helper.")
            Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
            exit 15
        }
        exit $child.ExitCode
    } finally {
        Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    }
}

# --- Inner wake process: stages 3-6 only ---
$cleanupOnUnhandledFailure = $false
$cleanupRootHwnd = [IntPtr]::Zero
$cleanupComposer = $null
$cleanupDeliveryMode = "sendkeys"
try {
    # --- Stage 3: wait for system-wide idle ---
    Write-StageEvent "STAGE3_IDLE_START" ("threshold=" + $IdleThresholdSeconds + "s max=" + $MaxWaitSeconds + "s")
    Write-Host ("[wake_codex] Waiting for >= " + $IdleThresholdSeconds + "s idle (max " + $MaxWaitSeconds + "s)...")
    $startTime = Get-Date
    $achieved  = $false

    while ((New-TimeSpan -Start $startTime -End (Get-Date)).TotalSeconds -lt $MaxWaitSeconds) {
        $idle = Get-IdleSeconds
        if ($idle -ge $IdleThresholdSeconds) {
            $achieved = $true
            Write-StageEvent "STAGE3_IDLE_OK" ("idle=" + $idle + "s")
            Write-Host ("[wake_codex] Idle threshold reached (idle=" + $idle + "s). Proceeding.")
            break
        }
        Start-Sleep -Seconds 1
    }

    if (-not $achieved) {
        Write-StageEvent "STAGE3_IDLE_TIMEOUT" ("max=" + $MaxWaitSeconds + "s")
        Write-Host ("[wake_codex] Max wait of " + $MaxWaitSeconds + "s expired without idle. Deferring delivery to avoid keystroke collision with active user typing.")
        Write-Host "[wake_codex] Bridge message stays unread; watcher will retry, or next bridge event will surface it."
        exit 16
    }

    # --- Stage 4: activate Codex (no foreground-skip - fire regardless) ---
    $prevFg      = [Win32Wake]::GetForegroundWindow()
    $prevFgTitle = Get-WindowTitle -hWnd $prevFg
    Write-Host ("[wake_codex] Foreground before: hwnd=" + $prevFg + " title=" + $prevFgTitle)

    $navigationSafety = Test-ForegroundCodexNavigationSafety -ForegroundHwnd $prevFg -ForegroundTitle $prevFgTitle
    if (-not $navigationSafety.Ok) {
        Write-PreflightAudit -Action "targeted_wake_refused" -Fields @{
            abort_reason = [string]$navigationSafety.Reason
            previous_desktop_thread_title = [string]$navigationSafety.PreviousThreadTitle
            expected_desktop_thread_title = [string]$navigationSafety.ExpectedThreadTitle
            restore_thread_id_present = [bool](Test-CodexThreadId -Value $RestoreThreadId)
        }
        Write-WakeTelemetry -Fields @{
            action = "foreground_codex_navigation_refused"
            reason = [string]$navigationSafety.Reason
            previous_desktop_thread_title = [string]$navigationSafety.PreviousThreadTitle
            expected_desktop_thread_title = [string]$navigationSafety.ExpectedThreadTitle
        }
        Write-Host ("[wake_codex] Targeted wake deferred: valid target thread is unavailable. reason=" + [string]$navigationSafety.Reason)
        exit 16
    }

    if ($navigationSafety.SkipNavigation) {
        Write-StageEvent "STAGE4_DEEPLINK_SKIPPED" ("reason=" + [string]$navigationSafety.Reason)
        Write-Host "[wake_codex] Foreground Codex appears to already be the target thread; skipping deeplink navigation."
    } else {
        if ([string]$navigationSafety.Reason -eq "foreground_codex_delivery_priority_no_restore") {
            Write-ForegroundCodexDeliveryPriorityAudit -NavigationSafety $navigationSafety
        }
        Write-StageEvent "STAGE4_DEEPLINK_START" ("thread=" + $ThreadId)
        Open-CodexThread -Value $ThreadId
        Write-StageEvent "STAGE4_DEEPLINK_DONE"
    }

    # The deeplink can create or retarget a Codex window. Prefer the foreground
    # Codex window after navigation, then fall back to the first visible one.
    $codex = Get-ForegroundCodexWindow
    if (-not $codex) {
        $codex = Get-CodexWindow
    }
    if (-not $codex) {
        Write-Host "[wake_codex] No Codex window found after deeplink navigation. Aborting."
        exit 14
    }
    $codexHwnd  = $codex.MainWindowHandle
    $codexTitle = Get-WindowTitle -hWnd $codexHwnd
    Write-Host ("[wake_codex] Target Codex after deeplink: PID=" + $codex.Id + " hwnd=" + $codexHwnd + " title=" + $codexTitle)
    $threadTitleSnapshot = Get-CodexThreadTitleSnapshot -RootHwnd $codexHwnd -WindowTitle $codexTitle
    Write-Host ("[wake_codex] Visible Codex thread title: " + [string]$threadTitleSnapshot.Title)
    Write-WakeTelemetry -Fields @{
        action = "thread_title_observed"
        desktop_thread_title = [string]$threadTitleSnapshot.Title
        desktop_thread_title_source = [string]$threadTitleSnapshot.Source
        desktop_window_title = [string]$threadTitleSnapshot.WindowTitle
    }
    Invoke-ThreadTitleProjectCertification -Snapshot $threadTitleSnapshot

    # Pre-flight is read-only: inspect the ProseMirror composer before any
    # focus/SendKeys path. UIA-unavailable defaults to exit 16 so the watcher
    # retries without counting this as a wake failure.
    Write-StageEvent "STAGE4_PREFLIGHT_START" ("priority=" + $Priority + " skip=" + [string]$SkipPreflight)
    if ($SkipPreflight) {
        $preflight = @{ State = "skip-preflight"; DraftText = ""; PreserveDraft = $false; Composer = $null }
        Write-Host "[wake_codex] Preflight skipped (-SkipPreflight). Proceeding directly to foreground."
    } else {
        $preflight = Invoke-ComposerPreflight -RootHwnd $codexHwnd
    }
    Write-StageEvent "STAGE4_PREFLIGHT_DONE" ("state=" + $preflight.State)
    Write-Host ("[wake_codex] Preflight composer state: " + $preflight.State)

    $codexProcId = 0
    $codexThread = [Win32Wake]::GetWindowThreadProcessId($codexHwnd, [ref]$codexProcId)

    # --- Stage 4b: focus acquisition ---
    # UIA SetFocus is the primary strategy: it acts directly on the cached
    # composer element (no new tree scan needed) and empirically acquires
    # foreground on Windows 10 even when the calling process doesn't own it.
    # Win32 chain (SetForegroundWindow, ALT-tap, SPI nuke, SwitchToThisWindow)
    # runs only when UIA SetFocus fails to move foreground to the Codex window.
    Write-StageEvent "STAGE4B_FOCUS_START"

    $composerForFocus = $preflight.Composer
    if ($null -eq $composerForFocus) {
        $composerForFocus = Get-CodexComposerElement -RootHwnd $codexHwnd
    }

    $focusAcquired = $false
    $deliveryMode  = "sendkeys"
    if ($null -ne $composerForFocus) {
        try {
            if ([Win32Wake]::IsIconic($codexHwnd)) {
                [Win32Wake]::ShowWindow($codexHwnd, 9) | Out-Null
                Start-Sleep -Milliseconds 30
            }
            $composerForFocus.SetFocus()
            Start-Sleep -Milliseconds 80
            $nowFg = [Win32Wake]::GetForegroundWindow()
            $composerHasKbFocus = $false
            try { $composerHasKbFocus = [bool]$composerForFocus.Current.HasKeyboardFocus } catch {}
            if ($nowFg -eq $codexHwnd) {
                $focusAcquired = $true
                $deliveryMode  = "sendkeys"
                Write-StageEvent "STAGE4B_FOCUS_UIA_OK" ("elapsed=" + [Math]::Round($script:WakeStart.Elapsed.TotalSeconds, 3) + "s focus_mode=foreground")
                Write-Host "[wake_codex] UIA SetFocus acquired foreground directly."
            } elseif ($composerHasKbFocus) {
                # Composer has element-level keyboard focus but Claude Desktop holds the
                # foreground lock, so SetForegroundWindow is blocked at the OS level.
                # Use PostMessage WM_PASTE which targets the message queue directly and
                # bypasses the foreground-window requirement entirely.
                $focusAcquired = $true
                $deliveryMode  = "postmessage"
                Write-StageEvent "STAGE4B_FOCUS_UIA_ELEMENT_ONLY" ("elapsed=" + [Math]::Round($script:WakeStart.Elapsed.TotalSeconds, 3) + "s")
                Write-Host "[wake_codex] UIA SetFocus: element-level keyboard focus acquired (foreground locked by Claude). Switching to PostMessage delivery."
            } else {
                Write-Host "[wake_codex] UIA SetFocus did not acquire foreground or element focus; falling through to Win32 chain."
            }
        } catch {
            Write-Host ("[wake_codex] UIA SetFocus threw: " + $_.Exception.Message + "; falling through to Win32 chain.")
        }
    } else {
        Write-Host "[wake_codex] No cached composer element; falling through to Win32 chain."
    }

    if (-not $focusAcquired) {
        Invoke-CodexForegroundAttempt -Hwnd $codexHwnd -TargetThreadId $codexThread
        Start-Sleep -Milliseconds 80
        $nowFg = [Win32Wake]::GetForegroundWindow()

        if ($nowFg -ne $codexHwnd) {
            Write-Host "[wake_codex] Win32 first attempt failed. Trying SendInput ALT-tap fallback."
            Send-AltTap
            Start-Sleep -Milliseconds 30
            Invoke-CodexForegroundAttempt -Hwnd $codexHwnd -TargetThreadId $codexThread
            Start-Sleep -Milliseconds 80
            $nowFg = [Win32Wake]::GetForegroundWindow()

            if ($nowFg -ne $codexHwnd) {
                Write-Host "[wake_codex] ALT-tap retry failed. Trying SPI ForegroundLockTimeout=0 nuke."
                Invoke-CodexForegroundWithSpiNuke -Hwnd $codexHwnd -TargetThreadId $codexThread
                Start-Sleep -Milliseconds 80
                $nowFg = [Win32Wake]::GetForegroundWindow()
                if ($nowFg -ne $codexHwnd) {
                    Write-Host "[wake_codex] SPI nuke failed. Trying SwitchToThisWindow fallback."
                    [Win32Wake]::SwitchToThisWindow($codexHwnd, $true)
                    Start-Sleep -Milliseconds 80
                    $nowFg = [Win32Wake]::GetForegroundWindow()
                }
                if ($nowFg -ne $codexHwnd) {
                    if ($RequireThreadId) {
                        Write-PreflightAudit -Action "targeted_wake_presend_verification_failed" -Fields @{
                            abort_reason = "foreground_unavailable"
                            foreground_hwnd = [string]$nowFg
                            target_hwnd = [string]$codexHwnd
                        }
                        Write-Host "[wake_codex] Targeted wake refused: Codex target window could not be made foreground before typing."
                        exit 16
                    }
                    Write-Host "[wake_codex] WARNING: all Win32 focus paths failed. Aborting."
                    exit 13
                }
                Write-Host "[wake_codex] SwitchToThisWindow fallback succeeded."
            } else {
                Write-Host "[wake_codex] SendInput ALT-tap fallback succeeded."
            }
        }
        Write-StageEvent "STAGE4B_FOCUS_WIN32_OK" ("elapsed=" + [Math]::Round($script:WakeStart.Elapsed.TotalSeconds, 3) + "s")
    }

    $targetVerification = $null
    if (-not $SkipPreflight -and ($RequireThreadId -or $VerifyTargetTwice -or $MaxPreSendRaceMilliseconds -gt 0)) {
        $targetVerification = Invoke-TargetPreSendVerification -RootHwnd $codexHwnd
        $rawText = [string]$targetVerification.Text
        $isPlaceholder = Test-IsCodexPlaceholderText -Text $rawText
        $preflight["DraftText"] = if ($isPlaceholder) { "" } else { $rawText }
        $preflight["PreserveDraft"] = (-not [string]::IsNullOrWhiteSpace($rawText)) -and (-not $isPlaceholder)
    }

    if ($DryRun) {
        Write-Host ("[wake_codex] DryRun. Would send: " + $Message + " + Ctrl+Enter (steer); preserve draft=" + [string]$preflight.PreserveDraft + ". Restoring focus.")
        Start-Sleep -Milliseconds 80
        [Win32Wake]::SetForegroundWindow($prevFg) | Out-Null
        exit 0
    }

    # --- Stage 5: clear composer + send keystrokes + Ctrl+Enter (steer) ---
    # Ctrl+A + Delete wipes any draft text in the composer. Trade-off the user
    # has explicitly accepted: bridge message delivery > preserving in-progress drafts.
    #
    # Ctrl+Enter (NOT plain Enter): in Codex Desktop, Ctrl+Enter is the "Steer"
    # action - it interrupts whatever Codex is currently doing and forces
    # immediate handling of the submitted message. Plain Enter just queues
    # behind the current turn. We always want bridge wakes to steer, so the
    # "check bridge inbox" trigger is actioned now, not after Codex finishes
    # whatever it was thinking about.
    if ($null -ne $targetVerification -and $MaxPreSendRaceMilliseconds -gt 0) {
        $raceMs = ((Get-Date) - $targetVerification.VerifiedAt).TotalMilliseconds
        if ($raceMs -gt $MaxPreSendRaceMilliseconds) {
            Write-PreflightAudit -Action "targeted_wake_presend_verification_failed" -Fields @{
                abort_reason = "presend_race_window_exceeded"
                race_milliseconds = [Math]::Round($raceMs, 1)
                max_race_milliseconds = $MaxPreSendRaceMilliseconds
            }
            Write-Host ("[wake_codex] Target verification expired before typing (race window " + [Math]::Round($raceMs, 1) + "ms > " + $MaxPreSendRaceMilliseconds + "ms).")
            exit 16
        }
    }
    if ($deliveryMode -eq "postmessage") {
        Write-StageEvent "STAGE5_POSTMESSAGE_START"
        $pmOk = Send-BridgeMessageViaPostMessage -ComposerElement $composerForFocus -Value $Message
        Write-StageEvent "STAGE5_POSTMESSAGE_DONE" ("ok=" + [string]$pmOk)
        if (-not $pmOk) {
            # Do NOT fall back to SendKeys here: foreground is Claude Desktop, so
            # SendKeys would inject "check bridge inbox" into the Claude composer.
            Clear-InjectedWakeTextIfPresent -RootHwnd $codexHwnd -ComposerElement $composerForFocus -Value $Message -DeliveryMode $deliveryMode -Reason "postmessage_delivery_failed" | Out-Null
            Write-Host "[wake_codex] PostMessage delivery failed with no safe fallback while foreground is locked. Deferring."
            exit 16
        }
        $cleanupOnUnhandledFailure = $true
        $cleanupRootHwnd = $codexHwnd
        $cleanupComposer = $composerForFocus
        $cleanupDeliveryMode = $deliveryMode
        Write-Host ("[wake_codex] Sent via PostMessage: " + $Message + " (steer; preflight=" + $preflight.State + ")")
    } else {
        Write-StageEvent "STAGE5_SENDKEYS_START" ("preserve_draft=" + [string]([bool]$preflight.PreserveDraft))
        try {
            Send-BridgeMessageKeys -Value $Message -DraftText $preflight.DraftText -PreserveDraft:([bool]$preflight.PreserveDraft)
        } catch {
            Clear-InjectedWakeTextIfPresent -RootHwnd $codexHwnd -ComposerElement $composerForFocus -Value $Message -DeliveryMode $deliveryMode -Reason "sendkeys_delivery_exception" | Out-Null
            throw
        }
        $cleanupOnUnhandledFailure = $true
        $cleanupRootHwnd = $codexHwnd
        $cleanupComposer = $composerForFocus
        $cleanupDeliveryMode = $deliveryMode
        Write-StageEvent "STAGE5_SENDKEYS_DONE"
        Write-Host ("[wake_codex] Sent: " + $Message + " + Ctrl+Enter (steer; preflight=" + $preflight.State + ")")
    }

    if ($PostTypingVerify) {
        $postflight = Test-CodexWakePostflight -RootHwnd $codexHwnd -Value $Message -TimeoutMilliseconds 2500
        if ($postflight.Ok) {
            Write-PreflightAudit -Action "targeted_wake_postflight_verified" -Fields @{
                message_hash = (Get-TextHash -Value $Message)
                reason = [string]$postflight.Reason
            }
            Write-WakeTelemetry -Fields @{
                action = "wake_postflight_verified"
                reason = [string]$postflight.Reason
                message_hash = (Get-TextHash -Value $Message)
            }
            Write-Host ("[wake_codex] Wake postflight verified: " + [string]$postflight.Reason)
            $cleanupOnUnhandledFailure = $false
        } else {
            $cleanup = Clear-InjectedWakeTextIfPresent -RootHwnd $codexHwnd -ComposerElement $composerForFocus -Value $Message -DeliveryMode $deliveryMode -Reason ([string]$postflight.Reason)
            Write-PreflightAudit -Action "targeted_wake_postflight_verification_failed" -Fields @{
                message_hash = (Get-TextHash -Value $Message)
                reason = [string]$postflight.Reason
                cleanup_attempted = [bool]$cleanup.Attempted
                cleanup_succeeded = [bool]$cleanup.Cleared
            }
            Write-WakeTelemetry -Fields @{
                action = "wake_postflight_verification_failed"
                reason = [string]$postflight.Reason
                message_hash = (Get-TextHash -Value $Message)
                cleanup_attempted = [bool]$cleanup.Attempted
                cleanup_succeeded = [bool]$cleanup.Cleared
            }
            Write-Host ("[wake_codex] WARNING: wake postflight did not confirm visible delivery: " + [string]$postflight.Reason)
            if ([bool]$cleanup.Cleared -or [string]$postflight.Reason -eq "wake_command_still_in_composer") {
                if ([bool]$cleanup.Cleared) {
                    $cleanupOnUnhandledFailure = $false
                }
                Write-Host "[wake_codex] Deferred wake after cleaning unsent injected composer text."
                exit 16
            }
        }
    } else {
        $cleanupOnUnhandledFailure = $false
    }

    # --- Stage 6: restore previous foreground ---
    Write-StageEvent "STAGE6_RESTORE_START"
    Start-Sleep -Milliseconds 200
    if ($ProtectForegroundCodexThread -and (Test-CodexThreadId -Value $RestoreThreadId) -and $RestoreThreadId -ne $ThreadId) {
        Write-StageEvent "STAGE6_RESTORE_THREAD_START" ("thread=" + $RestoreThreadId)
        Write-PreflightAudit -Action "targeted_wake_restore_thread_attempted" -Fields @{
            restore_thread_id = $RestoreThreadId
            target_thread_id = $ThreadId
        }
        Write-WakeTelemetry -Fields @{
            action = "restore_thread_attempted"
            restore_thread_id = $RestoreThreadId
        }
        Open-CodexThread -Value $RestoreThreadId
        Write-StageEvent "STAGE6_RESTORE_THREAD_DONE"
        Write-PreflightAudit -Action "targeted_wake_restore_thread_deeplink_invoked_unverified" -Fields @{
            restore_thread_id = $RestoreThreadId
            target_thread_id = $ThreadId
        }
        Write-WakeTelemetry -Fields @{
            action = "restore_thread_deeplink_invoked_unverified"
            restore_thread_id = $RestoreThreadId
        }
    }
    [Win32Wake]::SetForegroundWindow($prevFg) | Out-Null
    Write-StageEvent "STAGE6_DONE" ("total=" + [Math]::Round($script:WakeStart.Elapsed.TotalSeconds, 3) + "s restored_to=" + $prevFgTitle)
    Write-Host ("[wake_codex] Restored focus to: " + $prevFgTitle)

} catch {
    Write-StageEvent "ERROR" $_.Exception.Message
    Write-Host ("[wake_codex] ERROR: " + $_.Exception.Message)
    exit 1
} finally {
    if ($cleanupOnUnhandledFailure -and $cleanupRootHwnd -ne [IntPtr]::Zero) {
        Clear-InjectedWakeTextIfPresent -RootHwnd $cleanupRootHwnd -ComposerElement $cleanupComposer -Value $Message -DeliveryMode $cleanupDeliveryMode -Reason "unverified_delivery_finally" | Out-Null
    }
}

exit 0
