param(
    [ValidateSet("on", "off", "status")]
    [string]$Action = "status",
    [string]$FlagPath = "C:\Users\obabalola\.agent-bridge\bridge_watch_mode.flag"
)

$ErrorActionPreference = "Stop"

$flag = [System.IO.Path]::GetFullPath($FlagPath)
$flagDir = Split-Path -Parent $flag
if ($flagDir -and -not (Test-Path $flagDir)) {
    New-Item -ItemType Directory -Force -Path $flagDir | Out-Null
}

switch ($Action) {
    "on" {
        $payload = @{
            enabled_at = (Get-Date).ToUniversalTime().ToString("o")
            enabled_by = "codex"
        } | ConvertTo-Json
        [System.IO.File]::WriteAllText($flag, $payload + [Environment]::NewLine)
        Write-Output "bridge-watch mode: ON ($flag)"
    }
    "off" {
        if (Test-Path $flag) {
            Remove-Item -LiteralPath $flag -Force
        }
        Write-Output "bridge-watch mode: OFF ($flag)"
    }
    "status" {
        if (Test-Path $flag) {
            Write-Output "bridge-watch mode: ON ($flag)"
            Get-Content -Raw $flag
        } else {
            Write-Output "bridge-watch mode: OFF ($flag)"
        }
    }
}
