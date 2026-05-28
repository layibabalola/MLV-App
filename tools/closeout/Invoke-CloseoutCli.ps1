param(
    [string]$RepoRoot = ".",
    [string[]]$Arguments = @()
)

$ErrorActionPreference = "Stop"
$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
Push-Location -LiteralPath $resolvedRepo
try {
    $heartbeatSeconds = 30
    try {
        $configPath = Join-Path $resolvedRepo "closeout.config.json"
        if (Test-Path -LiteralPath $configPath) {
            $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
            if ($null -ne $config.locking -and $null -ne $config.locking.adapterHeartbeatSeconds) {
                $heartbeatSeconds = [int]$config.locking.adapterHeartbeatSeconds
            }
        }
    } catch {
        $heartbeatSeconds = 30
    }
    if ($env:CLOSEOUT_ADAPTER_HEARTBEAT_SECONDS) {
        $heartbeatSeconds = [int]$env:CLOSEOUT_ADAPTER_HEARTBEAT_SECONDS
    }
    if ($env:CLOSEOUT_DISABLE_ADAPTER_HEARTBEAT -eq "1") {
        $heartbeatSeconds = 0
    }
$runner = @'
import os
import sys
sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
from repo_hygiene.brokered_closeout import bounded_closeout_cli_main
raise SystemExit(bounded_closeout_cli_main(sys.argv[1:]))
'@
    $runnerArgs = @("--repo-root", ".", "--") + $Arguments
    $py = Get-Command py -ErrorAction SilentlyContinue
    $runnerPath = Join-Path ([System.IO.Path]::GetTempPath()) ("mlv-closeout-runner-{0}.py" -f ([guid]::NewGuid().ToString("N")))
    $stdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) ("mlv-closeout-stdout-{0}.log" -f ([guid]::NewGuid().ToString("N")))
    $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) ("mlv-closeout-stderr-{0}.log" -f ([guid]::NewGuid().ToString("N")))
    Set-Content -LiteralPath $runnerPath -Value $runner -Encoding UTF8
    if ($py) {
        $exe = $py.Source
        $processArgs = @("-3", $runnerPath) + $runnerArgs
    } else {
        $exe = (Get-Command python -ErrorAction Stop).Source
        $processArgs = @($runnerPath) + $runnerArgs
    }
    try {
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $exe
        $startInfo.WorkingDirectory = $resolvedRepo
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        foreach ($item in $processArgs) {
            [void]$startInfo.ArgumentList.Add([string]$item)
        }
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        [void]$process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if ($heartbeatSeconds -gt 0) {
            $heartbeatMs = [Math]::Max(1000, $heartbeatSeconds * 1000)
            while (-not $process.WaitForExit($heartbeatMs)) {
                $elapsed = [int]((Get-Date) - $process.StartTime).TotalSeconds
                [Console]::Error.WriteLine("[closeout-heartbeat] pid=$($process.Id) elapsed=${elapsed}s command=$($Arguments -join ' ')")
            }
        } else {
            $process.WaitForExit()
        }
        $process.WaitForExit()
        $stdoutText = $stdoutTask.GetAwaiter().GetResult()
        $stderrText = $stderrTask.GetAwaiter().GetResult()
        Set-Content -LiteralPath $stdoutPath -Value $stdoutText -Encoding UTF8
        Set-Content -LiteralPath $stderrPath -Value $stderrText -Encoding UTF8
        if ($stdoutText) {
            [Console]::Out.Write($stdoutText)
        }
        if ($stderrText) {
            [Console]::Error.Write($stderrText)
        }
        $process.Refresh()
        if ([int]$process.ExitCode -ne 0) {
            throw "closeout CLI failed with exit code $($process.ExitCode)"
        }
    } finally {
        Remove-Item -LiteralPath $runnerPath, $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
    }
} finally {
    Pop-Location
}
