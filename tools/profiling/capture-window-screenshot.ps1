param(
    [Parameter(Mandatory = $true)]
    [System.Diagnostics.Process]$Process,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [int]$WindowWaitMs = 10000,
    [int]$CaptureTimeoutMs = 2500,
    [int]$PollMs = 100
)

$ErrorActionPreference = "Stop"

if (-not ("MLVAppWindowCapture.NativeMethods" -as [type])) {
    Add-Type -AssemblyName System.Drawing
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace MLVAppWindowCapture
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    public static class NativeMethods
    {
        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

        [DllImport("user32.dll")]
        public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, int nFlags);
    }
}
"@
}

function Wait-ForWindowHandle {
    param(
        [System.Diagnostics.Process]$TargetProcess,
        [int]$TimeoutMs,
        [int]$PollIntervalMs
    )

    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $handle = [IntPtr]::Zero

    while ($watch.ElapsedMilliseconds -lt $TimeoutMs) {
        try {
            $TargetProcess.Refresh()
            $handle = $TargetProcess.MainWindowHandle
            if ($handle -ne [IntPtr]::Zero) {
                return $handle
            }
        }
        catch {
        }

        Start-Sleep -Milliseconds $PollIntervalMs
    }

    return [IntPtr]::Zero
}

$directory = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($directory)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$hwnd = Wait-ForWindowHandle -TargetProcess $Process -TimeoutMs $WindowWaitMs -PollIntervalMs $PollMs
if ($hwnd -eq [IntPtr]::Zero) {
    throw "Timed out waiting for a main window handle from process $($Process.Id)."
}

$captureStart = [System.Diagnostics.Stopwatch]::StartNew()
$screenshotTaken = $false
$lastFailure = $null

while ($captureStart.ElapsedMilliseconds -lt $CaptureTimeoutMs -and -not $screenshotTaken) {
    try {
        $rect = New-Object MLVAppWindowCapture.RECT
        if (-not [MLVAppWindowCapture.NativeMethods]::GetWindowRect($hwnd, [ref]$rect)) {
            throw "GetWindowRect failed for hwnd $hwnd."
        }

        $width = $rect.Right - $rect.Left
        $height = $rect.Bottom - $rect.Top
        if ($width -le 0 -or $height -le 0) {
            throw "Window rectangle was empty for hwnd $hwnd."
        }

        $bitmap = New-Object System.Drawing.Bitmap $width, $height
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            try {
                $hdc = $graphics.GetHdc()
                try {
                    $screenshotTaken = [MLVAppWindowCapture.NativeMethods]::PrintWindow($hwnd, $hdc, 2)
                }
                finally {
                    $graphics.ReleaseHdc($hdc)
                }
            }
            finally {
                $graphics.Dispose()
            }

            if (-not $screenshotTaken) {
                $fallback = [System.Drawing.Graphics]::FromImage($bitmap)
                try {
                    $fallback.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
                    $screenshotTaken = $true
                }
                finally {
                    $fallback.Dispose()
                }
            }

            if ($screenshotTaken) {
                $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
            }
        }
        finally {
            $bitmap.Dispose()
        }
    }
    catch {
        $lastFailure = $_.Exception.Message
        if ($captureStart.ElapsedMilliseconds -ge $CaptureTimeoutMs) {
            break
        }
        Start-Sleep -Milliseconds $PollMs
    }
}

if (-not $screenshotTaken) {
    if ($lastFailure) {
        throw "Failed to capture screenshot for process $($Process.Id): $lastFailure"
    }
    throw "Failed to capture screenshot for process $($Process.Id)."
}

[pscustomobject]@{
    processId = $Process.Id
    hwnd = $hwnd.ToInt64()
    outputPath = (Resolve-Path -LiteralPath $OutputPath).Path
    takenAtUtc = [datetime]::UtcNow.ToString("o")
    method = if ($screenshotTaken) { "printwindow-or-copyfromscreen" } else { "unknown" }
}
