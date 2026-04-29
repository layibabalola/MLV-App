# wake_codex.ps1
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
#   13 = all foreground paths failed and UIA composer fallback failed

param(
    [string]$Message              = "check bridge inbox",
    [string]$ThreadId             = "",
    [int]   $IdleThresholdSeconds = 5,
    [int]   $MaxWaitSeconds       = 60,
    [int]   $TotalRuntimeTimeoutSeconds = 90,
    [string]$LockFile             = "$env:USERPROFILE\.agent-bridge\wake_codex.lock",
    [switch]$DryRun,
    [switch]$FindOnly,
    [switch]$PrintInnerCommand,
    [string]$ProcessName          = "Codex",
    [switch]$RunInnerWake
)

$ErrorActionPreference = "Stop"

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
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    [DllImport("kernel32.dll")] public static extern uint GetTickCount();
    public const uint INPUT_KEYBOARD = 1;
    public const ushort VK_MENU = 0x12;
    public const uint KEYEVENTF_KEYUP = 0x0002;
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
        "-IdleThresholdSeconds " + [string]$IdleThresholdSeconds,
        "-MaxWaitSeconds " + [string]$MaxWaitSeconds,
        "-TotalRuntimeTimeoutSeconds " + [string]$TotalRuntimeTimeoutSeconds,
        "-LockFile " + (ConvertTo-PowerShellSingleQuotedLiteral $LockFile),
        "-ProcessName " + (ConvertTo-PowerShellSingleQuotedLiteral $ProcessName)
    )
    if ($DryRun) {
        $innerCommandParts += "-DryRun"
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
    Start-Sleep -Milliseconds 1200
}

function Invoke-CodexForegroundAttempt {
    param(
        [IntPtr]$Hwnd,
        [uint32]$TargetThreadId
    )

    $myThread = [Win32Wake]::GetCurrentThreadId()
    [Win32Wake]::AttachThreadInput($myThread, $TargetThreadId, $true) | Out-Null
    try {
        if ([Win32Wake]::IsIconic($Hwnd)) {
            [Win32Wake]::ShowWindow($Hwnd, 9) | Out-Null
        }
        [Win32Wake]::BringWindowToTop($Hwnd) | Out-Null
        [Win32Wake]::SetForegroundWindow($Hwnd) | Out-Null
    } finally {
        [Win32Wake]::AttachThreadInput($myThread, $TargetThreadId, $false) | Out-Null
    }
}

function Send-AltTap {
    $inputs = New-Object 'Win32Wake+INPUT[]' 2
    $inputs[0].type = [Win32Wake]::INPUT_KEYBOARD
    $inputs[0].ki.wVk = [Win32Wake]::VK_MENU
    $inputs[1].type = [Win32Wake]::INPUT_KEYBOARD
    $inputs[1].ki.wVk = [Win32Wake]::VK_MENU
    $inputs[1].ki.dwFlags = [Win32Wake]::KEYEVENTF_KEYUP
    $inputSize = [System.Runtime.InteropServices.Marshal]::SizeOf([Win32Wake+INPUT])
    $sent = [Win32Wake]::SendInput(2, $inputs, $inputSize)
    if ($sent -ne 2) {
        Write-Host ("[wake_codex] WARNING: SendInput ALT-tap sent " + $sent + "/2 input events.")
    }
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

function Send-BridgeMessageKeys {
    param([string]$Value)

    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 60
    [System.Windows.Forms.SendKeys]::SendWait("{DELETE}")
    Start-Sleep -Milliseconds 60

    [System.Windows.Forms.SendKeys]::SendWait($Value)
    Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait("^{ENTER}")
}

function Invoke-CodexComposerUiaFallback {
    param(
        [IntPtr]$RootHwnd,
        [string]$Value,
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
        Send-BridgeMessageKeys -Value $Value
        Write-Host ("[wake_codex] UIA fallback sent: " + $Value + " + Ctrl+Enter (steer; composer cleared first)")
        return $true
    } catch {
        Write-Host ("[wake_codex] UIA fallback failed: " + $_.Exception.Message)
        return $false
    }
}

if ($PrintInnerCommand -and -not $RunInnerWake) {
    Write-Host (New-InnerWakeCommand)
    exit 0
}

# --- Stage 1: locate ---
$codex = Get-CodexWindow
if (-not $codex) {
    Write-Host "[wake_codex] No Codex window found. Skipping."
    exit 0
}
$codexHwnd  = $codex.MainWindowHandle
$codexTitle = Get-WindowTitle -hWnd $codexHwnd
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
try {
    # --- Stage 3: wait for system-wide idle ---
    Write-Host ("[wake_codex] Waiting for >= " + $IdleThresholdSeconds + "s idle (max " + $MaxWaitSeconds + "s)...")
    $startTime = Get-Date
    $achieved  = $false

    while ((New-TimeSpan -Start $startTime -End (Get-Date)).TotalSeconds -lt $MaxWaitSeconds) {
        $idle = Get-IdleSeconds
        if ($idle -ge $IdleThresholdSeconds) {
            $achieved = $true
            Write-Host ("[wake_codex] Idle threshold reached (idle=" + $idle + "s). Proceeding.")
            break
        }
        Start-Sleep -Seconds 1
    }

    if (-not $achieved) {
        Write-Host ("[wake_codex] Max wait of " + $MaxWaitSeconds + "s expired without idle. Forcibly injecting anyway - bridge delivery wins over user typing.")
        # Fall through: inject regardless. User's in-progress draft (if any) will
        # be wiped by the Ctrl+A+Delete in stage 5, and any keystrokes they're
        # mid-typing may interleave with our SendKeys briefly.
    }

    # --- Stage 4: activate Codex (no foreground-skip - fire regardless) ---
    $prevFg      = [Win32Wake]::GetForegroundWindow()
    $prevFgTitle = Get-WindowTitle -hWnd $prevFg
    Write-Host ("[wake_codex] Foreground before: hwnd=" + $prevFg + " title=" + $prevFgTitle)

    Open-CodexThread -Value $ThreadId

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

    $codexProcId = 0
    $codexThread = [Win32Wake]::GetWindowThreadProcessId($codexHwnd, [ref]$codexProcId)

    Invoke-CodexForegroundAttempt -Hwnd $codexHwnd -TargetThreadId $codexThread

    Start-Sleep -Milliseconds 250

    $nowFg = [Win32Wake]::GetForegroundWindow()
    if ($nowFg -ne $codexHwnd) {
        Write-Host "[wake_codex] First foreground attempt failed. Trying SendInput ALT-tap fallback."
        Send-AltTap
        Start-Sleep -Milliseconds 50

        Invoke-CodexForegroundAttempt -Hwnd $codexHwnd -TargetThreadId $codexThread
        Start-Sleep -Milliseconds 200

        $nowFg = [Win32Wake]::GetForegroundWindow()
        if ($nowFg -ne $codexHwnd) {
            Write-Host "[wake_codex] ALT-tap retry failed. Trying SwitchToThisWindow fallback."
            [Win32Wake]::SwitchToThisWindow($codexHwnd, $true)
            Start-Sleep -Milliseconds 200
            $nowFg = [Win32Wake]::GetForegroundWindow()
            if ($nowFg -ne $codexHwnd) {
                Write-Host "[wake_codex] WARNING: failed to bring Codex to foreground after all focus fallbacks; trying UIA composer fallback."
                $uiaDelivered = Invoke-CodexComposerUiaFallback -RootHwnd $codexHwnd -Value $Message -DryRun:$DryRun
                if (-not $uiaDelivered) {
                    Write-Host "[wake_codex] WARNING: UIA composer fallback failed. Aborting."
                    exit 13
                }
                Start-Sleep -Milliseconds 200
                [Win32Wake]::SetForegroundWindow($prevFg) | Out-Null
                Write-Host ("[wake_codex] Restored focus to: " + $prevFgTitle)
                exit 0
            }
            Write-Host "[wake_codex] SwitchToThisWindow fallback succeeded."
        } else {
            Write-Host "[wake_codex] SendInput ALT-tap fallback succeeded."
        }
    }

    if ($DryRun) {
        Write-Host ("[wake_codex] DryRun. Would clear composer + send: " + $Message + " + Ctrl+Enter (steer). Restoring focus.")
        Start-Sleep -Milliseconds 200
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
    Send-BridgeMessageKeys -Value $Message
    Write-Host ("[wake_codex] Sent: " + $Message + " + Ctrl+Enter (steer; composer cleared first)")

    # --- Stage 6: restore previous foreground ---
    Start-Sleep -Milliseconds 200
    [Win32Wake]::SetForegroundWindow($prevFg) | Out-Null
    Write-Host ("[wake_codex] Restored focus to: " + $prevFgTitle)

} catch {
    Write-Host ("[wake_codex] ERROR: " + $_.Exception.Message)
    exit 1
}

exit 0
