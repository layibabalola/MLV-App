Set-StrictMode -Version Latest

function Get-GuiSmokeTaskText {
    param(
        [Parameter(Mandatory)]
        [object]$Task,
        [ValidateRange(1, 60000)]
        [int]$TimeoutMs,
        [Parameter(Mandatory)]
        [string]$Label
    )

    try {
        if (-not $Task.Wait($TimeoutMs)) {
            return [pscustomobject]@{
                completed = $false
                text = ""
                failure = "$Label did not drain within $TimeoutMs ms."
            }
        }
        return [pscustomobject]@{
            completed = $true
            text = [string]$Task.GetAwaiter().GetResult()
            failure = $null
        }
    }
    catch {
        return [pscustomobject]@{
            completed = $false
            text = ""
            failure = "$Label drain failed: $($_.Exception.Message)"
        }
    }
}

function Wait-GuiSmokeProcessBounded {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory)]
        [object]$StandardOutputTask,
        [Parameter(Mandatory)]
        [object]$StandardErrorTask,
        [ValidateRange(1, 3600000)]
        [int]$TimeoutMs,
        [ValidateRange(1, 60000)]
        [int]$TerminationGraceMs = 15000,
        [ValidateRange(1, 60000)]
        [int]$StreamDrainMs = 15000,
        # PLAYBACK-MEASURE-HOST-LOAD-GATE-1 round 3: 0 (default) preserves the original single
        # blocking WaitForExit exactly -- every caller that doesn't opt in keeps its prior
        # behaviour untouched. >0 chops the wait into chunks so $OnSample can run between them,
        # i.e. DURING the leg, not just at its edges.
        [ValidateRange(0, 3600000)]
        [int]$SampleIntervalMs = 0,
        [scriptblock]$OnSample = $null
    )

    $failures = [System.Collections.Generic.List[string]]::new()
    $timedOut = $false
    $treeKillAttempted = $false
    $treeKillSucceeded = $false
    $terminationConfirmed = $false

    if ($SampleIntervalMs -gt 0 -and $null -ne $OnSample) {
        $elapsedMs = 0
        while ($true) {
            $chunkMs = [Math]::Min($SampleIntervalMs, $TimeoutMs - $elapsedMs)
            if ($chunkMs -le 0) {
                $terminationConfirmed = $false
                break
            }
            try {
                $terminationConfirmed = $Process.WaitForExit($chunkMs)
            }
            catch {
                $failures.Add("Process wait failed: $($_.Exception.Message)")
                $terminationConfirmed = $false
                break
            }
            $elapsedMs += $chunkMs
            if ($terminationConfirmed -or $elapsedMs -ge $TimeoutMs) {
                break
            }
            try {
                & $OnSample
            }
            catch {
                $failures.Add("Host-load sample during leg failed: $($_.Exception.Message)")
            }
        }
    }
    else {
        try {
            $terminationConfirmed = $Process.WaitForExit($TimeoutMs)
        }
        catch {
            $failures.Add("Process wait failed: $($_.Exception.Message)")
        }
    }

    if (-not $terminationConfirmed) {
        $timedOut = $true
        $failures.Add("MLVApp exceeded the fail-closed process timeout of $TimeoutMs ms.")
        $treeKillAttempted = $true
        try {
            # Kill(entireProcessTree: true) is required: a child retaining an
            # inherited stdout/stderr handle can otherwise keep evidence
            # collection blocked after the direct process is terminated.
            $Process.Kill($true)
            $treeKillSucceeded = $true
        }
        catch {
            $failures.Add("MLVApp process-tree termination failed: $($_.Exception.Message)")
        }

        try {
            $terminationConfirmed = $Process.WaitForExit($TerminationGraceMs)
        }
        catch {
            $failures.Add("MLVApp post-kill wait failed: $($_.Exception.Message)")
        }
        if (-not $terminationConfirmed) {
            $failures.Add("MLVApp process tree was not confirmed terminated within $TerminationGraceMs ms.")
        }
    }

    $stdoutResult = Get-GuiSmokeTaskText `
        -Task $StandardOutputTask -TimeoutMs $StreamDrainMs -Label "stdout"
    $stderrResult = Get-GuiSmokeTaskText `
        -Task $StandardErrorTask -TimeoutMs $StreamDrainMs -Label "stderr"
    foreach ($failure in @($stdoutResult.failure, $stderrResult.failure)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$failure)) {
            $failures.Add([string]$failure)
        }
    }

    $nativeExitCode = $null
    if ($terminationConfirmed) {
        try {
            $nativeExitCode = $Process.ExitCode
        }
        catch {
            $failures.Add("MLVApp exit code was unavailable: $($_.Exception.Message)")
        }
    }
    $effectiveExitCode = if ($timedOut -or -not $terminationConfirmed) {
        124
    }
    elseif ($null -ne $nativeExitCode) {
        [int]$nativeExitCode
    }
    else {
        125
    }

    [pscustomobject]@{
        timedOut = $timedOut
        timeoutMs = $TimeoutMs
        treeKillAttempted = $treeKillAttempted
        treeKillSucceeded = $treeKillSucceeded
        terminationConfirmed = $terminationConfirmed
        stdoutDrained = [bool]$stdoutResult.completed
        stderrDrained = [bool]$stderrResult.completed
        stdout = [string]$stdoutResult.text
        stderr = [string]$stderrResult.text
        nativeExitCode = $nativeExitCode
        exitCode = $effectiveExitCode
        failures = @($failures)
    }
}

Export-ModuleMember -Function Wait-GuiSmokeProcessBounded
