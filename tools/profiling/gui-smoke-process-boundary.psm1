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
        # round 5 (sol MAJOR): round 3/4 accumulated $elapsedMs as a sum of NOMINAL chunk lengths,
        # never counting $OnSample's own execution time -- a slow callback (each one is a
        # Get-HostLoadSnapshot: two CIM queries plus a Get-Process enumeration/sort) let the loop
        # drift arbitrarily far past $TimeoutMs with every iteration, since the deadline check only
        # ever compared against the undercounted nominal sum. Schedule against a real wall-clock
        # stopwatch instead: $chunkMs is now computed from ACTUAL elapsed time (wait + callback),
        # so the whole loop is bounded by $TimeoutMs plus at most one in-flight chunk's wait and one
        # in-flight callback's duration, not an unbounded multiple of it.
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            $chunkMs = [Math]::Min($SampleIntervalMs, $TimeoutMs - $stopwatch.ElapsedMilliseconds)
            if ($chunkMs -le 0) {
                $terminationConfirmed = $false
                break
            }
            try {
                $terminationConfirmed = $Process.WaitForExit($chunkMs)
            }
            catch {
                $failures.Add("Process wait failed: $($_.Exception.GetType().Name)")
                $terminationConfirmed = $false
                break
            }
            if ($terminationConfirmed -or $stopwatch.ElapsedMilliseconds -ge $TimeoutMs) {
                break
            }
            # round 6 (sol MAJOR, astra MAJOR -- "collection is not bounded as a whole"): $OnSample
            # itself runs synchronously with no deadline of its own -- PowerShell has no
            # cooperative-cancellation point inside a scriptblock invoked with `&` in the caller's
            # own runspace, so a callback that never returns cannot be preempted from here (true
            # preemption would need a separate runspace with independently-duplicated session
            # state -- Get-HostLoadSnapshot and its helper functions live in the CALLING script's
            # scope, not this module's -- judged out of scope for this round's smallest-diff
            # mandate; disclosed here and in the round 6 summary, same class as round 5's disclosed
            # dilution residual). What IS bounded now: once a call DOES return, its own duration is
            # measured, and a call that took far longer than its declared cadence stops the loop
            # from scheduling further chunks/callbacks rather than silently continuing to
            # compound. The overrun is surfaced explicitly here AND (independently)
            # Get-HostLoadVerdict's round-5 observed-max-sample-gap check already forces the whole
            # leg's coverage to "unknown" once the gap between this sample's capturedAtUtc and the
            # next one's exceeds 1.5x the declared cadence -- this early break is what lets that
            # next, huge gap actually happen instead of the loop trying (and likely again
            # overrunning) another chunk immediately afterward.
            # round 6 (sol MAJOR, astra MAJOR -- "collection is not bounded as a whole"): $OnSample
            # itself runs synchronously with no deadline of its own -- PowerShell has no
            # cooperative-cancellation point inside a scriptblock invoked with `&` in the caller's
            # own runspace, so a callback that never returns cannot be preempted from here (true
            # preemption would need a separate runspace with independently-duplicated session
            # state -- Get-HostLoadSnapshot and its helper functions live in the CALLING script's
            # scope, not this module's -- judged out of scope for this round's smallest-diff
            # mandate; disclosed here and in the round 6 summary, same class as round 5's disclosed
            # dilution residual). What IS bounded now: once a call DOES return, its own duration is
            # measured, and a call that took far longer than its declared cadence stops the loop
            # from scheduling further chunks/callbacks rather than silently continuing to
            # compound. The overrun is surfaced explicitly here AND (independently)
            # Get-HostLoadVerdict's round-5 observed-max-sample-gap check already forces the whole
            # leg's coverage to "unknown" once the gap between this sample's capturedAtUtc and the
            # next one's exceeds 1.5x the declared cadence -- this early break is what lets that
            # next, huge gap actually happen instead of the loop trying (and likely again
            # overrunning) another chunk immediately afterward.
            $onSampleStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            try {
                & $OnSample
            }
            catch {
                # round 5 (sol minor, same class as round 4's hostLoad.*.error sanitization):
                # $_.Exception.Message can embed a path or host identifier and this string reaches
                # the receipt via processBoundary.failures -> the summary JSON. Record only the
                # exception's TYPE.
                $failures.Add("Host-load sample during leg failed: $($_.Exception.GetType().Name)")
            }
            $onSampleStopwatch.Stop()
            if ($onSampleStopwatch.ElapsedMilliseconds -gt ($SampleIntervalMs * 3)) {
                $failures.Add(
                    "Host-load sample during leg took $($onSampleStopwatch.ElapsedMilliseconds) ms, " +
                    "far exceeding its $SampleIntervalMs ms declared sampling cadence; treating " +
                    "remaining coverage for this leg as unknown rather than scheduling further " +
                    "samples.")
                break
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
