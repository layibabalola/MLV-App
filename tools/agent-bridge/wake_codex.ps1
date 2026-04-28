# wake_codex.ps1
# Inject a synthetic user message into the running Codex Desktop window.
# Invoked from watcher's on_message_command when a Codex bridge message arrives.
#
# Usage:
#   .\wake_codex.ps1 [-Message "check bridge inbox"] [-DryRun] [-FindOnly]

param(
    [string]$Message     = "check bridge inbox",
    [switch]$DryRun,
    [switch]$FindOnly,
    [string]$ProcessName = "Codex"
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
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
}
"@

function Get-CodexWindow {
    Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
}

function Get-WindowTitle {
    param([IntPtr]$hWnd)
    $sb = New-Object System.Text.StringBuilder 256
    [Win32Wake]::GetWindowText($hWnd, $sb, $sb.Capacity) | Out-Null
    return $sb.ToString()
}

# Stage 1: locate
$codex = Get-CodexWindow
if (-not $codex) {
    Write-Host "[wake_codex] No Codex window found. Skipping."
    exit 0
}

$codexHwnd  = $codex.MainWindowHandle
$codexTitle = Get-WindowTitle -hWnd $codexHwnd
$pidStr     = [string]$codex.Id
Write-Host ("[wake_codex] Found Codex: PID=" + $pidStr + " hwnd=" + $codexHwnd + " title=" + $codexTitle)

if ($FindOnly) {
    Write-Host "[wake_codex] FindOnly mode. Not changing focus."
    exit 0
}

# Stage 2: skip if Codex already foreground
$prevFg      = [Win32Wake]::GetForegroundWindow()
$prevFgTitle = Get-WindowTitle -hWnd $prevFg
Write-Host ("[wake_codex] Foreground before: hwnd=" + $prevFg + " title=" + $prevFgTitle)

if ($prevFg -eq $codexHwnd) {
    Write-Host "[wake_codex] Codex already foreground. User is active. Skipping injection."
    exit 0
}

# Stage 3: activate Codex via AttachThreadInput trick
$myThread     = [Win32Wake]::GetCurrentThreadId()
$codexProcId  = 0
$codexThread  = [Win32Wake]::GetWindowThreadProcessId($codexHwnd, [ref]$codexProcId)

[Win32Wake]::AttachThreadInput($myThread, $codexThread, $true) | Out-Null
try {
    if ([Win32Wake]::IsIconic($codexHwnd)) {
        [Win32Wake]::ShowWindow($codexHwnd, 9) | Out-Null
    }
    [Win32Wake]::SetForegroundWindow($codexHwnd) | Out-Null
} finally {
    [Win32Wake]::AttachThreadInput($myThread, $codexThread, $false) | Out-Null
}

Start-Sleep -Milliseconds 250

$nowFg      = [Win32Wake]::GetForegroundWindow()
$nowFgTitle = Get-WindowTitle -hWnd $nowFg
Write-Host ("[wake_codex] Foreground after activate: hwnd=" + $nowFg + " title=" + $nowFgTitle)

if ($nowFg -ne $codexHwnd) {
    Write-Host "[wake_codex] WARNING: failed to bring Codex to foreground. Aborting injection."
    exit 1
}

if ($DryRun) {
    Write-Host ("[wake_codex] DryRun. Would send: " + $Message + " + Enter. Restoring focus.")
    Start-Sleep -Milliseconds 200
    [Win32Wake]::SetForegroundWindow($prevFg) | Out-Null
    exit 0
}

# Stage 4: send keystrokes
[System.Windows.Forms.SendKeys]::SendWait($Message)
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
Write-Host ("[wake_codex] Sent: " + $Message + " + Enter")

# Stage 5: restore previous foreground
Start-Sleep -Milliseconds 200
[Win32Wake]::SetForegroundWindow($prevFg) | Out-Null
Write-Host ("[wake_codex] Restored focus to: " + $prevFgTitle)
exit 0
