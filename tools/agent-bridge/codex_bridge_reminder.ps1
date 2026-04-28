param(
    [string]$WorkspaceRoot = "C:\!Layi Wkspc\MLV-App\.claude\worktrees\festive-boyd-integration",
    [string]$ProjectBucket = "mlv-app",
    [string]$PrivateBucket = "9111dce5-3d33-4d06-b7a7-87dbf259b0c6",
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

$cwd = (Get-Location).Path
if (-not $Force -and -not (Test-IsUnderPath -Path $cwd -Root $WorkspaceRoot)) {
    exit 0
}

$message = "Bridge hygiene: check Codex private bucket $PrivateBucket and project bucket $ProjectBucket, or enter wait_inbox if bridge-watch is the task."
Write-Output $message

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
    $notify.BalloonTipText = $message
    $notify.Visible = $true
    $notify.ShowBalloonTip(7000)
    Start-Sleep -Seconds 8
    $notify.Dispose()
} catch {
    Write-Output "Bridge reminder toast failed: $($_.Exception.Message)"
}
