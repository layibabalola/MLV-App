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
#   STALE-LANDED-IN-PR     state says "still open", the item names NO verifiable sha at all, and a
#                          MERGED PR claims it behind a landing verb. This is the ancestry check's
#                          blind spot and the reason it was added: on 2026-09-05 SIX cards had
#                          landed and this tool printed `NO MISMATCHES`, because a card that names
#                          no commit gives the sha scan nothing to check. The tool was right about
#                          what it checked and silent about what it could not -- a green result
#                          scoped narrower than the question being asked of it.
#
# THE PR PROBE IS A SECOND, INDEPENDENT AXIS AND IS DELIBERATELY NOT MERGED WITH THE FIRST.
# Ancestry is local, offline and certain. The PR probe is remote, needs `gh`, and is a claim a
# human wrote. So it runs ONLY for items the sha axis could not judge (no verifiable sha), it is
# FAIL-OPEN (no `gh` -> CANNOT-DETERMINE, nothing flagged, and the tool says so out loud), and the
# match rules live in landing-probe.ps1, shared byte-for-byte with the dispatch loop that already
# uses them. A bare mention without a landing verb is a NEAR-MISS: reported, never acted on.
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
#             does not parse; 13 bad args / repo-root does not resolve / master ref does not resolve;
#             14 -MasterRef is provably behind its own upstream, so every answer would be wrong.
param(
    [string]$QueueFile = 'C:\!Layi Wkspc\MLV-App\.claude-state\coordination\dual-lane\queue.json',
    [string]$RepoRoot  = 'C:\!Layi Wkspc\MLV-App',
    [string]$MasterRef = 'master',
    # Skip the merged-PR landing probe (offline, or a deterministic run that must not touch the
    # network). The sha-ancestry axis is unaffected; only STALE-LANDED-IN-PR stops being derivable.
    [switch]$NoLandingProbe,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
# --- staleness guard -------------------------------------------------------------------
# The board root is the ONLY checkout that carries .claude-state/, and it routinely sits on a
# peer branch -- so an absolute-path invocation of this script can silently run a months-old
# copy and print a WRONG board reading. Refuse that. See assert-script-currency.ps1.
$__guard = Join-Path $PSScriptRoot 'assert-script-currency.ps1'
if (Test-Path -LiteralPath $__guard) { & $__guard -ScriptPath $PSCommandPath }
# ONE definition of "did this card land", shared with Invoke-Workstream.ps1. Never a second copy.
. (Join-Path $PSScriptRoot 'landing-probe.ps1')
# ---------------------------------------------------------------------------------------


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

# ---- the ref this tool measures against must not itself be stale ------------------------
# EVERY answer below is "is this sha an ancestor of -MasterRef". If -MasterRef is behind its own
# upstream, the tool does not fail: it QUIETLY RETURNS WRONG ANSWERS. A card landed in the
# missing commits reads SUSPECT-NOT-LANDED (a false red) and an open card that landed there
# reads OK (a false green, which is the whole class this file exists to refuse).
#
# NOT HYPOTHETICAL, AND NOT A ONE-OFF. Measured 2026-09-05: the board's local `master` was 5
# commits behind `fork/master`, so `c043f6fc` -- the commit that landed B2-TOOLING-BASELINE --
# was NOT an ancestor of `master` while being an ancestor of `fork/master`. It was fast-forwarded
# by hand at 07:05Z and was THREE COMMITS BEHIND AGAIN BY 13:41Z. A hand fix is not a fix; the
# ref drifts on its own schedule, so the check has to be executable.
#
# POLARITY, copied deliberately from assert-script-currency.ps1 rather than reinvented: fail
# CLOSED on PROVEN drift, fail OPEN on missing infrastructure. We stop only when we can
# positively demonstrate the ref is strictly behind a resolvable upstream. No upstream, no
# remote, a detached sha, a fresh clone -- all say nothing and let the caller run. A guard that
# blocked on "I could not check" would be worse than the bug.
if ($env:MLV_ALLOW_STALE_MASTER_REF -ne '1') {
    $upstream = $null
    $u = Invoke-Git @('rev-parse', '--abbrev-ref', "$MasterRef@{upstream}")
    if ($u.ExitCode -eq 0 -and $u.StdOut.Trim()) {
        $upstream = $u.StdOut.Trim()
    }
    if ($upstream) {
        $upstreamSha = (Invoke-Git @('rev-parse', $upstream)).StdOut.Trim()
        # Strictly behind: the ref IS an ancestor of its upstream, and they are not equal.
        # Ahead or diverged is NOT this defect -- a local branch carrying unpushed work is
        # normal, and refusing on it would make the tool unusable during ordinary development.
        if ($upstreamSha -and $upstreamSha -ne $masterSha -and
            (Test-IsAncestor $masterSha $upstream)) {
            $behind = (Invoke-Git @('rev-list', '--count', "$masterSha..$upstreamSha")).StdOut.Trim()
            # WHICH FIX TO PRINT DEPENDS ON WHETHER THE REF IS CHECKED OUT, and getting this wrong
            # makes the refusal useless. MEASURED while using this very guard on 2026-09-05: it
            # printed `git fetch fork master:master`, which git REFUSES with "refusing to fetch
            # into branch 'refs/heads/master' checked out at ..." -- and the board root had moved
            # onto master hours earlier, so the one command the tool suggested was the one command
            # that could not work. A refusal that names an impossible fix is a refusal nobody can
            # act on.
            $remote = $upstream.Split('/')[0]
            $checkedOutAt = $null
            $wt = Invoke-Git @('worktree', 'list', '--porcelain')
            if ($wt.ExitCode -eq 0) {
                $currentPath = $null
                foreach ($line in ($wt.StdOut -split "`r?`n")) {
                    if ($line -like 'worktree *') { $currentPath = $line.Substring(9).Trim() }
                    elseif ($line -eq "branch refs/heads/$MasterRef") { $checkedOutAt = $currentPath; break }
                }
            }
            if ($checkedOutAt) {
                $fixLine = @"
    git -C "$checkedOutAt" fetch $remote
    git -C "$checkedOutAt" merge --ff-only $upstream
  ('$MasterRef' is CHECKED OUT at that path, so 'fetch $remote ${MasterRef}:${MasterRef}' is refused by git.)
"@
            } else {
                $fixLine = "    git -C `"$RepoRoot`" fetch $remote ${MasterRef}:${MasterRef}"
            }
            [Console]::Error.WriteLine(@"
STALE MEASURING REF REFUSED: -MasterRef '$MasterRef' is $behind commit(s) behind '$upstream'.
  $MasterRef  = $($masterSha.Substring(0,12))
  $upstream = $($upstreamSha.Substring(0,12))

  Every finding below is 'is <sha> an ancestor of $MasterRef', so a ref that is behind does not
  make this tool fail -- it makes it QUIETLY WRONG in both directions. Refusing is the point.

  Fix it, then re-run:
$fixLine

  Or measure against the upstream directly:
    -MasterRef $upstream

  If you are DELIBERATELY auditing against an older ref, set MLV_ALLOW_STALE_MASTER_REF=1.
"@)
            exit 14
        }
    }
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
    'adjudicated-provisional',
    # Active work, not a disposition: the item is with a reviewer and can still change.
    # Sibling of 'in-review'.
    'handed-off-awaiting-review'
)
$landedStates = @(
    'landed', 'landed-evidence', 'landed-local-proof', 'CLEARED', 'fixed',
    # Sibling of 'fixed': it asserts a fix exists, which IS a claim about git ancestry, so it
    # belongs here and not in the neutral bucket. If such an item records shas that never
    # reached master, SUSPECT-NOT-LANDED is a real finding and should fire.
    'closed-fixed'
)
# Deliberately excluded from either bucket: these describe a TERMINAL disposition that is not a
# claim about git ancestry either way. Flagging a mismatch on them would manufacture a finding,
# not report one.
$neutralStates = @(
    'superseded', 'withdrawn', 'retracted-and-fixed', 'RETIRED', 'deferred-nonblocking',
    'scoped-untracked-target', 'answered-folded', 'optional-validation',
    # All terminal, and none asserts that any commit reached master, so by the rule above
    # flagging ancestry on them would manufacture a finding rather than report one:
    'closed-superseded',      # sibling of 'superseded' -- another item replaced it
    'closed-transformed',     # became a different item; nothing shipped under THIS id
    'closed-root-caused',     # an investigation concluded; a cause is not a commit
    'closed-not-this-board'   # out of scope here; ancestry in this repo says nothing
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

# ---- PASS 1: sha ancestry, per item. Local, offline, certain. -----------------------------
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
    } elseif ($bucket -eq 'OPEN' -and $ancestorShas.Count -eq 0 -and $nonAncestorShas.Count -eq 0) {
        # THE BLIND SPOT, marked here and judged in pass 2. An OPEN item naming no verifiable sha
        # is exactly the case the ancestry axis cannot speak to, and staying silent on it is what
        # produced `NO MISMATCHES` over six landed cards. Nothing is asserted yet.
        $finding = 'NO-SHA-EVIDENCE'
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
        landedInPr      = ''
    }
}

# ---- PASS 2: merged-PR landing claims, ONLY for the items pass 1 could not judge. ---------
# Remote, needs `gh`, and reads a claim a human wrote -- so it is a separate axis, runs on a
# strictly smaller set, and fails OPEN. `landedInPr` is recorded on every item it judges so a
# reader can see WHY a finding fired without re-running the probe.
$probeStatus = 'skipped: -NoLandingProbe'
$nearMisses = @()
$unjudged = @($results | Where-Object { $_.finding -eq 'NO-SHA-EVIDENCE' })
if (-not $NoLandingProbe -and $unjudged.Count -gt 0) {
    $probe = Get-CardLandingEvidence -CardId @($unjudged | ForEach-Object { $_.id })
    $probeStatus = $probe.status
    $nearMisses = @($probe.nearMiss)
    foreach ($row in $unjudged) {
        if ($probe.landed.ContainsKey($row.id)) {
            $hit = $probe.landed[$row.id]
            $row.finding = 'STALE-LANDED-IN-PR'
            $row.landedInPr = "PR #$($hit.number) [matched by $($hit.how)] $($hit.title)"
        }
    }
}
# An item the probe did not match stays NO-SHA-EVIDENCE, which is NOT a mismatch: it means this
# tool has nothing to check it against, and that is a documentation gap, not a disagreement.
$mismatches = $results | Where-Object {
    $_.finding -eq 'STALE-ALREADY-LANDED' -or $_.finding -eq 'SUSPECT-NOT-LANDED' -or
    $_.finding -eq 'STALE-LANDED-IN-PR'
}
$unknownStateItems = $results | Where-Object { $_.finding -eq 'UNKNOWN-STATE' }

$noShaItems = @($results | Where-Object { $_.finding -eq 'NO-SHA-EVIDENCE' })

if ($Json) {
    [pscustomobject]@{
        masterRef          = $MasterRef
        masterSha          = $masterSha
        derivedAt          = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        itemCount          = $results.Count
        mismatchCount      = $mismatches.Count
        unknownStateCount  = $unknownStateItems.Count
        landingProbe       = $probeStatus
        nearMisses         = $nearMisses
        noShaEvidenceCount = $noShaItems.Count
        results            = $results
    } | ConvertTo-Json -Depth 6
} else {
    Write-Output ("queue-derive: repoRoot={0} master={1} ({2}) items={3}" -f $RepoRoot, $MasterRef, $masterSha.Substring(0, 8), $results.Count)
    # The probe's status is printed on EVERY run, including when it could not run. A check whose
    # failure looks identical to a pass is the defect this whole file exists to refuse.
    Write-Output ("queue-derive: landing-probe {0}" -f $probeStatus)
    if ($nearMisses.Count -gt 0) {
        Write-Output ("queue-derive: landing-probe NEAR-MISS ({0}): {1} - named in a merged PR without a landing verb, NOT flagged" -f $nearMisses.Count, ($nearMisses -join ', '))
    }
    if ($noShaItems.Count -gt 0) {
        Write-Output ("queue-derive: {0} open item(s) name no verifiable sha and no merged PR claims them - NOT a mismatch, nothing to check them against: {1}" -f $noShaItems.Count, (($noShaItems | ForEach-Object { $_.id }) -join ', '))
    }
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
            if ($m.finding -eq 'STALE-LANDED-IN-PR') {
                Write-Output ("  [{0}] {1}: typed='{2}' owner={3} evidence={4}" -f $m.id, $m.finding, $m.typedState, $m.owner, $m.landedInPr)
            } else {
                Write-Output ("  [{0}] {1}: typed='{2}' owner={3} ancestorShas=[{4}]" -f $m.id, $m.finding, $m.typedState, $m.owner, $m.ancestorShas)
            }
        }
    }
}

exit ([int]($mismatches.Count -gt 0))
