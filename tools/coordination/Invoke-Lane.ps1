<#
.SYNOPSIS
    Invoke one fleet lane as a bounded CLI process. No seat, no lease, no registry.

.DESCRIPTION
    Claude-driven fleet topology (adopted 2026-08-29, Layi's ruling). Replaces the
    Codex-Desktop scheduled-automation lane model, under which the board spent
    seven heartbeat rotations in three days and landed zero product changes.

    THE CENTRAL DESIGN CHANGE: a lane is a PROCESS, not a SEAT.

    The prior topology simulated liveness in documents - seat-registry.json,
    health/leases/*.json, durableTaskId, automation.toml target_thread_id - and
    every one of those could disagree with reality. A lane was "alive" because a
    file said so, and the board deadlocked when the file was stale (LIVENESS-KEY-1,
    BOARD-DARK-1, and the v13-r15 transition-receipt block that left the hub dark
    for 53 hours).

    A process needs none of it. It is alive because it is running and dead when it
    exits, and the exit code is the truth. So this script:
      - takes NO lease and renews nothing
      - writes NO shared coordination state (no registry, no queue, no pen)
      - therefore requires NO seat and has no succession gate to pass

    The audit record is a per-invocation RECEIPT. Receipts accumulate; nothing is
    ever renewed or reconciled.

    WHAT THE RECEIPT ACTUALLY GUARANTEES - stated narrowly on purpose, because an
    earlier version of this header implied a durability the code did not provide:
      - its slot is reserved ATOMICALLY before any work starts, so concurrent
        lanes cannot overwrite each other's evidence
      - it is written from a finally block, so it exists on every exit path FROM
        THE RESERVATION ONWARD, including a throw; `complete` and `failure` say
        which path was taken. Argument-validation failures BEFORE the reservation
        (bad -PromptFile, unresolvable -WorkDir) produce NO receipt - correctly,
        because no slot was taken and no work was attempted. Verified by
        injecting a post-reservation throw: receipt written, complete=false,
        failure carried, exitCode -999.
      - prompt and output are hashed, so the record cannot drift from what ran
    It does NOT guarantee anything about a host that dies mid-write, and it makes
    no claim of immutability - a later run with the same explicit -RunDir and a
    freed slot could reuse the name.

.NOTES
    ASCII-only by project convention (non-ASCII in .ps1 has broken parsing here).
    Receipts are UTF-8 without BOM for the same reason.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('opus', 'sonnet', 'fable', 'sol', 'luna')]
    [string]$Lane,

    [string]$Prompt,
    [string]$PromptFile,

    # Working root the lane reasons about. Defaults to the repo this script lives in.
    [string]$WorkDir,

    # Where receipts and outputs land. Defaults to a timestamped run dir.
    [string]$RunDir,

    [int]$TimeoutSec = 900,

    # Grant the lane write access. OFF by default: an analysis or review lane that
    # cannot mutate the tree cannot corrupt the thing it is judging.
    [switch]$AllowEdits,

    # Free-text tag recorded in the receipt (e.g. the card id).
    [string]$Card = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------- lane table
# Engine, model and effort per lane. This table IS the topology; there is no
# other place a lane's model is decided, so a lane cannot silently drift onto
# the wrong tier the way a registry field could.
$LANES = @{
    opus   = @{ engine = 'claude'; model = 'opus';           effort = 'high';   role = 'orchestrator' }
    sonnet = @{ engine = 'claude'; model = 'sonnet';         effort = '';       role = 'implementer' }
    fable  = @{ engine = 'claude'; model = 'claude-fable-5'; effort = '';       role = 'review-guidance-planning' }
    sol    = @{ engine = 'codex';  model = 'gpt-5.6-sol';    effort = 'high';   role = 'adversarial-verifier' }
    luna   = @{ engine = 'codex';  model = 'gpt-5.6-luna';   effort = 'high';   role = 'breadth-recon' }
}

# Absolute launcher paths. NEITHER is on the Git Bash PATH on this host, and a
# bare name resolves differently per shell - measured 2026-08-29.
$CLAUDE_EXE = Join-Path $env:APPDATA 'npm\claude.cmd'
$CODEX_EXE  = Join-Path $env:APPDATA 'npm\codex.cmd'

function Get-Sha256([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '')
    } finally { $sha.Dispose() }
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    # UTF-8 WITHOUT BOM: a BOM has broken JSON consumers on this box before.
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

# ---------------------------------------------------------------- resolve inputs
if (-not $Prompt -and -not $PromptFile) { throw 'Supply -Prompt or -PromptFile.' }
if ($PromptFile) {
    if (-not (Test-Path -LiteralPath $PromptFile)) { throw "PromptFile not found: $PromptFile" }
    $Prompt = Get-Content -LiteralPath $PromptFile -Raw
}

if (-not $WorkDir) { $WorkDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path }
$WorkDir = (Resolve-Path -LiteralPath $WorkDir).Path

if (-not $RunDir) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $RunDir = Join-Path $WorkDir ".claude-state\fleet-runs\$stamp"
}
if (-not (Test-Path -LiteralPath $RunDir)) { New-Item -ItemType Directory -Path $RunDir -Force | Out-Null }
$RunDir = (Resolve-Path -LiteralPath $RunDir).Path

$cfg = $LANES[$Lane]

# ATOMIC SLOT RESERVATION. The previous form was
#     while (Test-Path <candidate>) { $n++ }
# which is CHECK-THEN-ACT: two lanes launched concurrently into the same run dir
# both see the slot free, both pick it, and the second silently overwrites the
# first one's evidence. This session ran concurrent lanes, so the race was live.
# FileMode::CreateNew is a SINGLE filesystem operation that FAILS if the path
# already exists, so the winner is decided by the OS, not by our timing.
$base = $null
for ($n = 1; $n -le 999; $n++) {
    $candidate = Join-Path $RunDir ("{0}-{1:d3}" -f $Lane, $n)
    try {
        $fs = [System.IO.File]::Open(
            "$candidate.receipt.json",
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None)
        $fs.Dispose()
        $base = $candidate
        break
    } catch [System.IO.IOException] {
        continue   # someone else owns this slot; try the next
    }
}
if ($null -eq $base) { throw "Could not reserve a receipt slot in $RunDir after 999 attempts." }
$promptPath = "$base.prompt.txt"
$outPath    = "$base.out.txt"
$errPath    = "$base.err.txt"
$lastPath   = "$base.last.txt"
$rcptPath   = "$base.receipt.json"

# RESERVED MARKER, written the instant the slot is taken and before ANY work.
# The atomic reservation above creates an EMPTY file, which is a valid claim but
# INVALID JSON - anything reading receipts while a lane is in flight crashes on
# it, and a crash between reservation and completion would leave that empty file
# as the only record. This is PEN-GAP-1's concern in receipt form: durable
# mutation must not precede durable explanation. The slot now always parses and
# always says what it is.
Write-Utf8NoBom $rcptPath (([ordered]@{
    schema   = 'mlv-app/fleet-lane-receipt/v1'
    state    = 'reserved'
    complete = $false
    lane     = $Lane
    card     = $Card
    reservedUtc = (Get-Date).ToUniversalTime().ToString('o')
} | ConvertTo-Json -Depth 4))

# CRASH-TOTAL RECEIPT. Every field the receipt needs is initialised BEFORE the
# try, so a failure anywhere still produces a well-formed receipt instead of
# nothing. The old code wrote the receipt only on the normal tail, outside any
# try/finally, with $ErrorActionPreference='Stop' - so any throw yielded ZERO
# receipt and a failed run was indistinguishable from one that never ran. That
# is the exact defect this runner's own card list called SWEEP-RECEIPT-1.
$startedUtc = (Get-Date).ToUniversalTime()
$sw         = [System.Diagnostics.Stopwatch]::StartNew()
$exitCode   = -999
$timedOut   = $false
$final      = ''
$failure    = $null
$authority  = [ordered]@{ permissionMode = 'unset'; allowedTools = 'unset'; sandbox = 'unset'; writableRoot = $null }

try {

Write-Utf8NoBom $promptPath $Prompt

# ---------------------------------------------------------------- build argv
# Argument ARRAYS, never a concatenated string: this repo has paths with spaces
# and a '!' in them, and string-built command lines have mis-split here before.
if ($cfg.engine -eq 'claude') {
    $exe  = $CLAUDE_EXE
    $argv = @('-p', '--model', $cfg.model, '--output-format', 'json', '--add-dir', $WorkDir)
    if ($AllowEdits) {
        $argv += @('--permission-mode', 'acceptEdits')
    } else {
        # No read-only permission mode exists, so restrict the TOOLS instead.
        # COMMA-SEPARATED, ONE TOKEN: --allowedTools is VARIADIC, so passing the
        # tools as separate arguments makes it swallow the positional prompt that
        # follows and the CLI dies with "Input must be provided...".
        $argv += @('--permission-mode', 'dontAsk',
                   '--allowedTools', 'Read,Grep,Glob')
    }
    # PROMPT GOES VIA STDIN, NOT AS A POSITIONAL ARGUMENT. Several claude flags
    # (--allowedTools, --add-dir) are VARIADIC and keep consuming every following
    # token that does not start with '-', so a trailing positional prompt is
    # silently absorbed into the flag's value list and the CLI then dies with
    # "Input must be provided either through stdin or as a prompt argument".
    # stdin has no such ambiguity and no command-line length limit.
    $stdinContent = $Prompt
    # CAUSAL-REACH-1: the receipt used to record only allowEdits, which says what a
    # lane may WRITE and nothing about what it may CAUSE. Record the actual granted
    # authority so a receipt can be audited against the policy that produced it.
    $authority = [ordered]@{
        permissionMode = if ($AllowEdits) { 'acceptEdits' } else { 'dontAsk' }
        allowedTools   = if ($AllowEdits) { 'ALL' } else { 'Read,Grep,Glob' }
        sandbox        = 'n/a (claude)'
        writableRoot   = if ($AllowEdits) { $WorkDir } else { $null }
    }
} else {
    $exe  = $CODEX_EXE
    $sandbox = if ($AllowEdits) { 'workspace-write' } else { 'read-only' }
    # -s and -c are set EXPLICITLY per call. ~/.codex/config.toml carries
    # approval_policy=never + sandbox_mode=danger-full-access globally, which is
    # fine for a watched interactive session and NOT fine for automated fan-out.
    $argv = @('exec',
              '-m', $cfg.model,
              '-c', ("model_reasoning_effort=`"{0}`"" -f $cfg.effort),
              '-s', $sandbox,
              '-C', $WorkDir,
              '-o', $lastPath,
              '--skip-git-repo-check',
              '-')
    # '-' MEANS "READ THE PROMPT FROM STDIN", and it is not optional here.
    # Passing a multi-line prompt POSITIONALLY is silently TRUNCATED AT THE FIRST
    # NEWLINE, because the launcher is a .cmd batch wrapper and cmd.exe breaks the
    # argument there. Measured 2026-08-29: a 3,243-byte review prompt arrived as
    # its first line only, and the lane answered "what would you like me to do?"
    # in 10.9s with exit 0 - a SUCCESSFUL-LOOKING run that reviewed nothing.
    $stdinContent = $Prompt
    $authority = [ordered]@{
        permissionMode = 'n/a (codex)'
        allowedTools   = 'ALL'
        sandbox        = $sandbox
        writableRoot   = if ($AllowEdits) { $WorkDir } else { $null }
    }
}

# ---------------------------------------------------------------- run, bounded
$startedUtc = (Get-Date).ToUniversalTime()
$sw = [System.Diagnostics.Stopwatch]::StartNew()

# LAUNCH VIA ProcessStartInfo.ArgumentList, NOT Start-Process -ArgumentList.
# Start-Process joins an array into ONE command-line string without quoting the
# elements, so this repo's own path - "C:\!Layi Wkspc\MLV-App", which contains a
# space - arrives SPLIT and codex rejects the fragment as an unexpected argument.
# ArgumentList is a real collection and .NET applies correct per-argument
# escaping (including the special .cmd rules), so a path with spaces survives.
$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName               = $exe
foreach ($a in $argv) { [void]$psi.ArgumentList.Add($a) }
$psi.WorkingDirectory       = $WorkDir
$psi.UseShellExecute        = $false
$psi.RedirectStandardInput  = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true
$psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
$psi.StandardErrorEncoding  = [System.Text.UTF8Encoding]::new($false)

$proc = [System.Diagnostics.Process]::Start($psi)

# Start the async reads BEFORE waiting: a child that fills a redirected pipe
# buffer blocks forever if nobody is draining it, and the timeout below would
# then measure a deadlock we caused rather than a slow lane.
$outTask = $proc.StandardOutput.ReadToEndAsync()
$errTask = $proc.StandardError.ReadToEndAsync()

# The prompt reaches claude this way; codex gets an empty stdin that is CLOSED,
# which is what stops it waiting on "Reading additional input from stdin...".
$proc.StandardInput.Write($stdinContent)
$proc.StandardInput.Close()

$timedOut = $false
if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
    $timedOut = $true
    try { $proc.Kill($true) } catch { }
    try { [void]$proc.WaitForExit(15000) } catch { }
}
$sw.Stop()
$exitCode = if ($timedOut) { -1 } else { $proc.ExitCode }

# ---------------------------------------------------------------- harvest
$stdout = try { $outTask.Result } catch { '' }
if ($null -eq $stdout) { $stdout = '' }
$stderrText = try { $errTask.Result } catch { '' }
if ($null -eq $stderrText) { $stderrText = '' }
Write-Utf8NoBom $outPath $stdout
Write-Utf8NoBom $errPath $stderrText

$final = ''
if ($cfg.engine -eq 'claude') {
    # --output-format json wraps the answer; fall back to raw stdout if the
    # process died before emitting well-formed JSON.
    try {
        $j = $stdout | ConvertFrom-Json
        if ($j.PSObject.Properties.Name -contains 'result') { $final = [string]$j.result }
    } catch { $final = '' }
    if (-not $final) { $final = $stdout }
    Write-Utf8NoBom $lastPath $final
} else {
    $final = if (Test-Path -LiteralPath $lastPath) { Get-Content -LiteralPath $lastPath -Raw } else { '' }
    if ([string]::IsNullOrWhiteSpace($final)) { $final = $stdout }
    # LAST-RESORT FALLBACK TO STDERR. codex writes its -o last-message file only on
    # a CLEAN exit and streams its working narration to STDERR, not stdout - so a
    # lane killed at the timeout leaves the last-message file absent and stdout
    # EMPTY, i.e. a 15-minute run yielding literally zero bytes. Measured
    # 2026-08-29 on a timed-out luna recon. Partial narration is worth far more
    # than nothing when deciding whether to retry, widen the timeout, or change
    # approach entirely.
    if ([string]::IsNullOrWhiteSpace($final)) { $final = $stderrText }
    if ($null -eq $final) { $final = '' }
    if ($timedOut) { Write-Utf8NoBom $lastPath $final }
}

}
catch {
    $failure = $_.Exception.Message
    throw
}
finally {

$receipt = [ordered]@{
    schema       = 'mlv-app/fleet-lane-receipt/v1'
    # SAME KEY AT EVERY STAGE. A reader checks `state` once - reserved, complete or
    # failed - instead of inferring liveness from which fields happen to be present.
    state        = if ($null -ne $failure) { 'failed' } elseif ($exitCode -ne -999) { 'complete' } else { 'incomplete' }
    lane         = $Lane
    role         = $cfg.role
    engine       = $cfg.engine
    model        = $cfg.model
    effort       = $cfg.effort
    card         = $Card
    workDir      = $WorkDir
    allowEdits   = [bool]$AllowEdits
    authority    = $authority
    startedUtc   = $startedUtc.ToString('o')
    endedUtc     = (Get-Date).ToUniversalTime().ToString('o')
    durationSec  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    timedOut     = $timedOut
    timeoutSec   = $TimeoutSec
    exitCode     = $exitCode
    promptSha256 = Get-Sha256 $Prompt
    promptBytes  = [System.Text.Encoding]::UTF8.GetByteCount($Prompt)
    outputSha256 = Get-Sha256 $final
    outputBytes  = [System.Text.Encoding]::UTF8.GetByteCount($final)
    promptPath   = $promptPath
    outputPath   = $lastPath
    stdoutPath   = $outPath
    stderrPath   = $errPath
    failure      = $failure
    complete     = ($null -eq $failure -and $exitCode -ne -999)
}
Write-Utf8NoBom $rcptPath (($receipt | ConvertTo-Json -Depth 6))

}   # end finally - the receipt is now written on EVERY exit path

Write-Host ("[{0}] {1}/{2} effort={3} exit={4} {5}s -> {6}" -f `
    $Lane, $cfg.engine, $cfg.model, $cfg.effort, $exitCode, $receipt.durationSec, $rcptPath)

if ($timedOut) { Write-Host "  TIMED OUT after ${TimeoutSec}s - output is partial." }

# Emit the receipt so a caller can pipeline on it.
[pscustomobject]$receipt
