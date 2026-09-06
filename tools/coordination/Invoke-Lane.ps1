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
    [string]$Card = '',

    # Backstop against a runaway lane. Claude only (codex exec has no equivalent).
    # 0 disables the cap. Measured 2026-09-03: real lanes used 13-21 turns, so 40 is a
    # runaway guard, NOT the spend control - that is -DenyBulkReads below.
    [int]$MaxTurns = 40,

    # Let the lane read the bulk coordination files. OFF by default.
    # MEASURED 2026-09-03: three unattended fable lanes cost USD 22-25 EACH, every one
    # burning ~970,000 cache-creation tokens - almost exactly the 1.6 MB coordination
    # surface. Invoke-Workstream.ps1 already reads that bulk in PowerShell and inlines
    # the facts into a 4-7 KB brief, so a lane re-reading it pays full price for context
    # it was already given. A compact brief bounds the PROMPT; it does not bound the
    # READING, and nothing here did until now.
    [switch]$AllowBulkReads,

    # 0.1: the explicit tool allowlist an editing lane is granted. REQUIRED with
    # -AllowEdits; 'ALL' is never accepted (allowlist-required). Comma-separated,
    # passed straight through to claude's --allowedTools.
    [string]$AllowedTools = '',

    # 0.1: an extra directory the lane may read beyond -WorkDir (e.g. board
    # coordination paths an editing lane needs without a full -AllowBulkReads
    # grant). Optional; claude engine only (--add-dir).
    [string]$ExtraReadDir = ''
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

# ---------------------------------------------------------------- 0.1 pre-flight (before any process, before any run dir)
# (a) A codex lane (sol, luna) can never be granted write access: no Claude hook is
# visible to codex exec, so nothing here could enforce NA-1..NA-10 against it.
if ($AllowEdits -and ($Lane -eq 'sol' -or $Lane -eq 'luna')) {
    throw "codex-lane-never-edits: -Lane $Lane with -AllowEdits (no Claude hook is visible to codex exec)"
}
# (b) An editing lane's tool grant must be an explicit, auditable list. 'ALL' is
# never accepted - that is the exact grant this whole patch exists to narrow.
if ($AllowEdits -and ([string]::IsNullOrWhiteSpace($AllowedTools) -or $AllowedTools -eq 'ALL')) {
    throw "allowlist-required: -AllowEdits requires -AllowedTools <comma-separated list>; 'ALL' is never granted"
}
# MLV_BOARD_ROOT: only a test sets it (a tmp-dir board fixture); the default is the
# real board (mirrors Start-EditingLane.ps1's own resolution, O107).
$RepoRoot = if ($env:MLV_BOARD_ROOT) { $env:MLV_BOARD_ROOT } else { 'C:\!Layi Wkspc\MLV-App' }
$HookEnforcedReceipt = Join-Path $RepoRoot '.claude-state\coordination\dual-lane\receipts\0.05-hook-enforced.json'
$WorkDirHookPath     = Join-Path $WorkDir 'tools\hooks\mlv-never-authorized.py'
# hookSha256 is computed unconditionally (when the file exists) so the receipt can
# always carry it, per 0.1's receipt-fields requirement - not only on editing lanes.
$WorkDirHookSha256 = if (Test-Path -LiteralPath $WorkDirHookPath) {
    (Get-FileHash -LiteralPath $WorkDirHookPath -Algorithm SHA256).Hash.ToLowerInvariant()
} else { $null }
# (e) The worktree's OWN hook copy is what actually governs an editing lane's
# session (Claude Code loads it from -WorkDir), so this checks THAT copy against
# the board's receipt of what the ratified hook hashes to - never the board root's
# own copy, which the lane never runs against.
if ($AllowEdits) {
    if (-not (Test-Path -LiteralPath $HookEnforcedReceipt)) {
        throw "hook-not-enforced: receipt missing at $HookEnforcedReceipt"
    }
    if ($null -eq $WorkDirHookSha256) {
        throw "hook-not-enforced: hook script missing in worktree: $WorkDirHookPath"
    }
    $hookEnforcedRec = Get-Content -LiteralPath $HookEnforcedReceipt -Raw | ConvertFrom-Json
    $receiptHookSha256 = ([string]$hookEnforcedRec.hookSha256).ToLowerInvariant()
    if ($WorkDirHookSha256 -ne $receiptHookSha256) {
        throw ("hook-not-enforced: worktree hook sha256={0} != receipt hookSha256={1}" -f $WorkDirHookSha256, $receiptHookSha256)
    }
}
$BaseSha = try {
    (& git -C $WorkDir rev-parse HEAD 2>$null | Select-Object -First 1)
} catch { $null }
if ([string]::IsNullOrWhiteSpace($BaseSha)) { $BaseSha = $null }

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
$settingsPath = "$base.settings.json"

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
$denyRules  = @()
# $null, never 0. An engine that does not REPORT cost and a run that cost nothing are
# different facts and this receipt will not merge them - the same rule the dispatcher's
# malformed-row counter follows.
$costUsd = $null; $numTurns = $null
$cacheCreateTokens = $null; $cacheReadTokens = $null; $outputTokens = $null

try {

Write-Utf8NoBom $promptPath $Prompt

# ---------------------------------------------------------------- build argv
# Argument ARRAYS, never a concatenated string: this repo has paths with spaces
# and a '!' in them, and string-built command lines have mis-split here before.
if ($cfg.engine -eq 'claude') {
    $exe  = $CLAUDE_EXE
    $argv = @('-p', '--model', $cfg.model, '--output-format', 'json', '--add-dir', $WorkDir)
    if ($ExtraReadDir) { $argv += @('--add-dir', $ExtraReadDir) }
    if ($MaxTurns -gt 0) { $argv += @('--max-turns', [string]$MaxTurns) }
    # Deny-list written to the RUN DIR so the grant is auditable beside the receipt that
    # it produced, rather than being an invisible property of the invocation.
    if (-not $AllowBulkReads) {
        $denyRules = @(
            'Read(**/gpu-lane-impl-review-sync.md)',
            'Read(**/queue.json)',
            'Read(**/claude-resume-CURRENT.md)',
            'Read(**/fable-resume-CURRENT.md)',
            'Read(**/orchestrator-resume-CURRENT.md)'
        )
        $settingsObj = @{ permissions = @{ deny = $denyRules } }
        Write-Utf8NoBom $settingsPath ($settingsObj | ConvertTo-Json -Depth 5)
        $argv += @('--settings', $settingsPath)
    }
    if ($AllowEdits) {
        # 0.1: acceptEdits still takes an explicit --allowedTools list - the mode
        # decides HOW an allowed tool behaves (auto-accept vs prompt), the list
        # decides WHICH tools are allowed at all. 'ALL' was refused above.
        $argv += @('--permission-mode', 'acceptEdits', '--allowedTools', $AllowedTools)
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
        allowedTools   = if ($AllowEdits) { $AllowedTools } else { 'Read,Grep,Glob' }
        sandbox        = 'n/a (claude)'
        writableRoot   = if ($AllowEdits) { $WorkDir } else { $null }
        maxTurns       = if ($MaxTurns -gt 0) { $MaxTurns } else { 'unset' }
        bulkReads      = if ($AllowBulkReads) { 'ALLOWED' } else { 'DENIED' }
        denyRules      = if ($AllowBulkReads) { @() } else { $denyRules }
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
        maxTurns       = 'n/a (codex exec has no turn cap)'
        bulkReads      = 'ALLOWED (codex takes no settings deny-list)'
        denyRules      = @()
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
        # SPEND IS A FIRST-CLASS RECEIPT FACT. Until 2026-09-03 the receipt recorded
        # exitCode (the truth about completion) and byte counts, but NOTHING about cost -
        # so "what did this board spend" was answerable only by grepping raw stdout of
        # every run dir by hand. That is how USD 74.62 accumulated unnoticed in 4 h.
        if ($j.PSObject.Properties.Name -contains 'total_cost_usd') {
            $costUsd = [double]$j.total_cost_usd
        }
        if ($j.PSObject.Properties.Name -contains 'num_turns') { $numTurns = [int]$j.num_turns }
        if ($j.PSObject.Properties.Name -contains 'usage' -and $null -ne $j.usage) {
            if ($j.usage.PSObject.Properties.Name -contains 'cache_creation_input_tokens') {
                $cacheCreateTokens = [int64]$j.usage.cache_creation_input_tokens
            }
            if ($j.usage.PSObject.Properties.Name -contains 'cache_read_input_tokens') {
                $cacheReadTokens = [int64]$j.usage.cache_read_input_tokens
            }
            if ($j.usage.PSObject.Properties.Name -contains 'output_tokens') {
                $outputTokens = [int64]$j.usage.output_tokens
            }
        }
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
    allowedTools = if ($AllowEdits) { $AllowedTools } else { $authority.allowedTools }
    baseSha      = $BaseSha
    hookSha256   = $WorkDirHookSha256
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
    spend        = [ordered]@{
        costUsd            = $costUsd
        costReported       = ($null -ne $costUsd)
        # Absence is NOT zero. codex exec emits no cost telemetry, so a codex receipt
        # says so explicitly instead of implying a free run.
        costUnavailableWhy = if ($null -ne $costUsd) { $null }
                             elseif ($cfg.engine -eq 'codex') { 'codex exec reports no cost telemetry' }
                             else { 'claude json carried no total_cost_usd (died before emitting it?)' }
        numTurns           = $numTurns
        cacheCreateTokens  = $cacheCreateTokens
        cacheReadTokens    = $cacheReadTokens
        outputTokens       = $outputTokens
    }
}
Write-Utf8NoBom $rcptPath (($receipt | ConvertTo-Json -Depth 6))

}   # end finally - the receipt is now written on EVERY exit path

Write-Host ("[{0}] {1}/{2} effort={3} exit={4} {5}s cost={6} -> {7}" -f `
    $Lane, $cfg.engine, $cfg.model, $cfg.effort, $exitCode, $receipt.durationSec,
    $(if ($null -ne $costUsd) { 'USD ' + ([math]::Round($costUsd,2)) } else { 'unreported' }),
    $rcptPath)

if ($timedOut) { Write-Host "  TIMED OUT after ${TimeoutSec}s - output is partial." }

# Emit the receipt so a caller can pipeline on it.
[pscustomobject]$receipt

# PROPAGATE THE OUTCOME. This script's own header says "it is alive because it is running and
# dead when it exits, and THE EXIT CODE IS THE TRUTH" -- and until now it did not honour that at
# its own boundary. It computed $exitCode, wrote it into the receipt, printed it, and then fell
# off the end, which in PowerShell exits 0. Invoke-Workstream.ps1 faithfully captured that
# $LASTEXITCODE and logged laneExitCode=0, so EVERY failed lane was recorded as a success in the
# only durable log the loop consults.
#
# Measured 2026-09-05, two dispatches of twelve:
#   CITE-TXN-1          181.5s  USD 2.24  api_error_status 429 "You've hit your session limit"
#   GATE-FAMILY-BOOKED    2.8s  USD 0     api_error_status 429
# Both receipts carried exitCode 1. Both dispatch-log rows carried laneExitCode 0. Two of the
# day's twelve dispatches produced nothing and were indistinguishable from work that succeeded.
#
# Sentinels are mapped rather than passed through: a negative value does not survive a process
# exit code intact, and -1 would surface as 255. 124 for a timeout matches the taxonomy the repo
# already uses in boundedRunnerExitCodes; 127 marks "reserved but never completed", which the
# receipt also records as complete=false with the failure carried.
$propagated = switch ($exitCode) {
    -1      { 124 }   # timed out
    -999    { 127 }   # slot reserved, no completion recorded
    default { if ($exitCode -ge 0 -and $exitCode -le 255) { $exitCode } else { 1 } }
}
exit $propagated
