# queue-derive.ps1 - read-only audit: recomputes each queue.json item's delivery state from git
# ancestry at read time and REFUSES to silently repeat a hand-typed state that disagrees with raw.
# (QUEUE-DERIVE-1, fable SEQ 1297; generalizes the QUEUE-HEAD-1 fix that stopped at one field.)
#
# For every item: scans ALL string field values (recursively) for 40-hex commit SHAs, verifies
# each is a real reachable commit object in this repo, and checks whether it is an ancestor of
# the given -MasterRef (default: master). Compares that DERIVED signal against the item's
# hand-typed `state` and flags two failure directions:
#   STALE-ALREADY-LANDED   state says "still open" (dispatched/booked/in-review/...) but a sha
#                          named in the item IS an ancestor of master -- the item has landed and
#                          the queue does not know it yet (the audit's own named class: E4-1,
#                          CI-1, CI1-DOC-TRUTH-1 all held this shape -- two of them recurred
#                          across the very hub turn documenting the first instance).
#   SUSPECT-NOT-LANDED     state says "landed" (landed/landed-evidence/landed-local-proof/
#                          CLEARED/fixed) but NO sha named in the item is an ancestor of master --
#                          the claim is unverifiable against raw and should be re-checked.
#   UNKNOWN-STATE          the item's `state` string is not in ANY of the three declared buckets
#                          (open / landed / neutral) below. This is a THIRD outcome, not folded
#                          into either side -- a new state string must be classified explicitly,
#                          never silently guessed at (RESUME STEP 4: "when you add a check, name
#                          its three outcomes").
#
# Prose fields (blocker/note/scope/etc.) are NEVER read as authority here -- only 40-hex commit
# shas that resolve to real, reachable commit objects in THIS repo are evidence. This tool NEVER
# writes queue.json; mutating the queue authority is the hub's act. Use write-verified-json.ps1
# (same directory) if you are the hub applying a correction this tool surfaced.
#
# Usage: pwsh -File queue-derive.ps1 [-QueueFile <path>] [-RepoRoot <path>] [-MasterRef master] [-Json]
#
# NOTE ON DEFAULTS: -RepoRoot and -QueueFile default to the CANONICAL ABSOLUTE repo path, not a
# path relative to this script's own location. `.claude-state/` is gitignored and a linked
# worktree is a checkout of TRACKED FILES ONLY, so `.claude-state/coordination/dual-lane/queue.json`
# never exists inside a linked worktree -- REGARDLESS of where this script itself is running from
# (canonical checkout or a work-block worktree). A relative default would silently resolve to a
# non-existent path the moment this script is run from a worktree, which is most of the time it is
# run. Do not "fix" this to a relative path; that reintroduces the exact trap RESUME.md STEP 0
# names for `.claude-state`.
# Exit codes: 0 no mismatches (or -Json emitted regardless); 1 mismatch(es) found; 12 queue.json
#             does not parse; 13 bad args / repo-root does not resolve / master ref does not resolve.
param(
    [string]$QueueFile = 'C:\!Layi Wkspc\MLV-App\.claude-state\coordination\dual-lane\queue.json',
    [string]$RepoRoot  = 'C:\!Layi Wkspc\MLV-App',
    [string]$MasterRef = 'master',
    [switch]$Json
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $QueueFile)) {
    [Console]::Error.WriteLine("queue-derive: queue file not found: $QueueFile")
    exit 13
}

# ---- git helper, always pinned to -RepoRoot via -C, never the caller's cwd ----
function Invoke-Git {
    param([string[]]$GitArgs)
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = 'git'
    $psi.ArgumentList.Add('-C')
    $psi.ArgumentList.Add($RepoRoot)
    foreach ($a in $GitArgs) { $psi.ArgumentList.Add($a) }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $p = [System.Diagnostics.Process]::Start($psi)
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    return [pscustomobject]@{ ExitCode = $p.ExitCode; StdOut = $stdout; StdErr = $stderr }
}

function Test-CommitExists([string]$sha) {
    $r = Invoke-Git @('cat-file', '-e', "$sha^{commit}")
    return ($r.ExitCode -eq 0)
}

function Test-IsAncestor([string]$sha, [string]$ref) {
    $r = Invoke-Git @('merge-base', '--is-ancestor', $sha, $ref)
    return ($r.ExitCode -eq 0)
}

# confirm repo root and master ref resolve before trusting anything downstream
$top = Invoke-Git @('rev-parse', '--show-toplevel')
if ($top.ExitCode -ne 0) {
    [Console]::Error.WriteLine("queue-derive: -RepoRoot does not resolve to a git repo: $RepoRoot`n$($top.StdErr)")
    exit 13
}
$masterShaResult = Invoke-Git @('rev-parse', $MasterRef)
$masterSha = $masterShaResult.StdOut.Trim()
if ($masterShaResult.ExitCode -ne 0 -or [string]::IsNullOrEmpty($masterSha)) {
    [Console]::Error.WriteLine("queue-derive: -MasterRef '$MasterRef' does not resolve in $RepoRoot")
    exit 13
}

$queueRaw = [System.IO.File]::ReadAllText($QueueFile)
try {
    $queue = $queueRaw | ConvertFrom-Json -ErrorAction Stop
} catch {
    [Console]::Error.WriteLine("queue-derive: queue.json does not parse: $($_.Exception.Message)")
    exit 12
}

$shaPattern = [regex]'\b[0-9a-fA-F]{40}\b'
# BLOCKING [2] fix (QUEUE-DERIVE-1 review, LANE-4 b6e24c75, session 396a0cfa): every review
# range on this board is written <base>..<head>, and base is master far more often than not --
# so a bare 40-hex scan picked up the BASE of an item's own OPEN review range as "a landed
# sha", reporting STALE-ALREADY-LANDED on the item that merely records it is under review. A
# <40hex>..<40hex> token is matched first and only its HEAD is kept; both matched shas are
# then stripped from the value before the bare-sha pass, so the base is never re-picked-up as
# a standalone commit reference.
$rangePattern = [regex]'\b([0-9a-fA-F]{40})\.\.([0-9a-fA-F]{40})\b'

# State buckets -- explicit and named, not inferred. A state string absent from all three is
# UNKNOWN-STATE, never silently folded into OPEN or LANDED.
$openStates = @(
    'dispatched', 'dispatched-untracked-target', 'booked', 'booked-rule', 'in-review',
    'blocked-operator', 'in-progress-operator-started', 'consult-open', 'blocked-redesign',
    'changes-requested-awaiting-acceptance-evidence', 'changes-requested-awaiting-implementer',
    'approved-awaiting-finalize', 'surfaced-awaiting-ordering', 'open-risk',
    'adjudicated-provisional'
)
$landedStates = @('landed', 'landed-evidence', 'landed-local-proof', 'CLEARED', 'fixed')
# Deliberately excluded from either bucket: these describe a TERMINAL disposition that is not a
# claim about git ancestry either way. Flagging a mismatch on them would manufacture a finding,
# not report one.
$neutralStates = @(
    'superseded', 'withdrawn', 'retracted-and-fixed', 'RETIRED', 'deferred-nonblocking',
    'scoped-untracked-target', 'answered-folded', 'optional-validation'
)

function Get-CommitShasFromValue($value, [System.Collections.Generic.HashSet[string]]$acc) {
    if ($null -eq $value) { return }
    if ($value -is [string]) {
        $remaining = $value
        foreach ($rm in $rangePattern.Matches($value)) {
            [void]$acc.Add($rm.Groups[2].Value.ToLowerInvariant())
            $remaining = $remaining.Replace($rm.Value, '')
        }
        foreach ($m in $shaPattern.Matches($remaining)) { [void]$acc.Add($m.Value.ToLowerInvariant()) }
    } elseif ($value -is [System.Management.Automation.PSCustomObject]) {
        foreach ($prop in $value.PSObject.Properties) { Get-CommitShasFromValue $prop.Value $acc }
    } elseif (($value -is [System.Collections.IEnumerable]) -and -not ($value -is [string])) {
        foreach ($el in $value) { Get-CommitShasFromValue $el $acc }
    }
}

$results = @()
$shaCache = @{}  # sha -> @{ exists = bool; ancestor = bool }

foreach ($item in $queue.items) {
    $id = $item.id
    $state = $item.state
    $shas = [System.Collections.Generic.HashSet[string]]::new()
    Get-CommitShasFromValue $item $shas

    $ancestorShas = @()
    $nonAncestorShas = @()
    foreach ($sha in $shas) {
        if (-not $shaCache.ContainsKey($sha)) {
            $exists = Test-CommitExists $sha
            $anc = $false
            if ($exists) { $anc = Test-IsAncestor $sha $MasterRef }
            $shaCache[$sha] = @{ exists = $exists; ancestor = $anc }
        }
        $info = $shaCache[$sha]
        if ($info.exists) {
            if ($info.ancestor) { $ancestorShas += $sha } else { $nonAncestorShas += $sha }
        }
    }

    $bucket = 'UNKNOWN-STATE'
    if ($openStates -contains $state) { $bucket = 'OPEN' }
    elseif ($landedStates -contains $state) { $bucket = 'LANDED' }
    elseif ($neutralStates -contains $state) { $bucket = 'NEUTRAL' }

    $finding = 'OK'
    if ($bucket -eq 'OPEN' -and $ancestorShas.Count -gt 0) {
        $finding = 'STALE-ALREADY-LANDED'
    } elseif ($bucket -eq 'LANDED' -and $ancestorShas.Count -eq 0 -and $shas.Count -gt 0) {
        # Only flagged when the item names at least one candidate sha but none of them checks
        # out. An item claiming 'landed' with NO sha at all is a documentation gap, a different
        # and lower-confidence class -- not asserted here as a mismatch, since there is nothing
        # in the item to check against raw.
        $finding = 'SUSPECT-NOT-LANDED'
    } elseif ($bucket -eq 'UNKNOWN-STATE') {
        $finding = 'UNKNOWN-STATE'
    }

    $results += [pscustomobject]@{
        id              = $id
        typedState      = $state
        bucket          = $bucket
        owner           = $item.owner
        priority        = $item.priority
        ancestorShas    = ($ancestorShas -join ',')
        nonAncestorShas = ($nonAncestorShas -join ',')
        finding         = $finding
    }
}

$mismatches = $results | Where-Object { $_.finding -eq 'STALE-ALREADY-LANDED' -or $_.finding -eq 'SUSPECT-NOT-LANDED' }
$unknownStateItems = $results | Where-Object { $_.finding -eq 'UNKNOWN-STATE' }

if ($Json) {
    [pscustomobject]@{
        masterRef          = $MasterRef
        masterSha          = $masterSha
        derivedAt          = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        itemCount          = $results.Count
        mismatchCount      = $mismatches.Count
        unknownStateCount  = $unknownStateItems.Count
        results            = $results
    } | ConvertTo-Json -Depth 6
} else {
    Write-Output ("queue-derive: repoRoot={0} master={1} ({2}) items={3}" -f $RepoRoot, $MasterRef, $masterSha.Substring(0, 8), $results.Count)
    if ($unknownStateItems.Count -gt 0) {
        $uniqueUnknown = ($unknownStateItems | ForEach-Object { "$($_.id)=$($_.typedState)" } | Sort-Object -Unique) -join ', '
        Write-Output ""
        Write-Output ("UNKNOWN STATE STRINGS (not classified as OPEN, LANDED or NEUTRAL -- add to a bucket in this script, do not guess): {0}" -f $uniqueUnknown)
    }
    if ($mismatches.Count -eq 0) {
        Write-Output ""
        Write-Output "NO MISMATCHES: every item's typed state agrees with what git ancestry can verify."
    } else {
        Write-Output ""
        Write-Output ("{0} MISMATCH(ES) between typed state and git-derived state:" -f $mismatches.Count)
        foreach ($m in $mismatches) {
            Write-Output ("  [{0}] {1}: typed='{2}' owner={3} ancestorShas=[{4}]" -f $m.id, $m.finding, $m.typedState, $m.owner, $m.ancestorShas)
        }
    }
}

exit ([int]($mismatches.Count -gt 0))
