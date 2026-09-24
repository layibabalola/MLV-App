# UmRunDrop.psm1 -- how tools/profiling/um-run.ps1 places side-files and a job into a file-drop
# agent's inbox. Kept in a module so tests can drive the exact code with an observing or faulty
# copier (sol, PR #135 r1: final-state assertions could not prove share verification or ordering).
#
# Invariants:
#   - a side-file name is a plain basename with an allowlisted extension, no trailing dot/space,
#     not a Windows device name, and never job-shaped; after Windows path normalisation it must
#     still be exactly that name (sol r1 BLOCKER: 'evil.job.ps1.' resolves to 'evil.job.ps1');
#   - every temporary name is unique per submission (GUID), so concurrent submitters never share
#     a .sidepart or .job.tmp;
#   - bytes are verified by re-reading the temporary copy FROM THE SHARE before it is renamed;
#   - renames never overwrite: a destination that appeared concurrently makes the rename fail, and
#     the result is accepted only if that destination already holds identical bytes (side-file) --
#     a job file is never replaced;
#   - every side-file is in place before the job is dropped;
#
# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 10 (hub scope ruling reversed: sol proved BOTH round-9
# premises false -- see summary.md). OWNERSHIP OF A JobId IS NOW CLAIMED ATOMICALLY, BY
# CONSTRUCTION, BEFORE ANY OTHER WORK -- not just when a caller happens to pass a budget:
#   - inbox\<id>.meta.json is written FIRST, before a single side-file byte or job byte is copied,
#     for EVERY submission -- the same no-overwrite atomic rename this module already uses and
#     tests extensively for side-files and the job file itself (Move-Item, no -Force: NTFS and SMB
#     both make this a single all-or-nothing operation, so of two racing renames to the same name
#     exactly one can ever land). A caller with no budget still claims the JobId; its metadata just
#     omits the `timeoutSec` field, which both the tracked and deployed agents already treat as
#     "fall back to my own default" (never a new parser rule -- confirmed against the DEPLOYED
#     agent, see summary.md).
#   - every claim also carries a fresh per-submission `nonce`, so the metadata on disk always
#     identifies which submission attempt actually owns it.
#   - this REVERSES round 4's own ordering rule ("job bytes copied before metadata is published,
#     so metadata is only ever visible once its job already exists"). That rule solved a narrower
#     problem -- a hard-killed submission leaving orphaned metadata -- by making metadata-without-
#     a-job rare (a single rename's width). Round 10 needs metadata to be the FIRST thing written,
#     because ownership has to be established before the side-file/job work it is meant to guard,
#     not after it -- so metadata-without-a-job-yet becomes the NORMAL shape of an in-flight
#     submission, not just a crash artifact. The tolerance mechanism for that is unchanged (see
#     immediately below): it was already built to treat metadata-without-a-job as "maybe still
#     live" up to a grace period, and this reversal simply means that grace period is doing its job
#     across the WHOLE submission now, not just a single rename.
#
# THE THREE CLAIM OUTCOMES (Invoke-UmRunDrop, top of function):
#   1. no metadata exists for this JobId (or the metadata that does exist has gone stale -- see
#      below) -> this call claims it, writing its own nonce, and becomes the sole owner of every
#      side-file and job placement that follows;
#   2. metadata exists and is fresh (age below -OrphanMetaGraceSec, measured on the SHARE's own
#      clock via the probe below, never this submitter's) -> refused with UMRUN_JOBID_IN_USE
#      before this call ever touches a side-file or the job -- the incumbent's ownership, and its
#      side-files/job in flight, are left completely untouched;
#   3. metadata exists, is stale, but cannot be removed (e.g. a locked or non-empty path occupying
#      it) -> refused with a distinct message; nothing is silently reclaimed.
#
# ASTRA round 8 (still live at round 10): a purely age-based reclaim cannot PROVE a claim's owner
# is dead -- only that it has been quiet for a while -- so a genuinely live submitter that is
# merely SLOW (a big side-file transfer, not a crash) can, in principle, be reclaimed out from
# under it if its own placement legitimately takes longer than -OrphanMetaGraceSec. This module
# does not attempt to disprove that risk with a liveness heartbeat of its own (unlike the AGENT's
# heartbeat.txt, a submitter has no long-running process on the share to refresh one, and two
# different HOSTS' clocks cannot be compared for "still executing" the way um-run.ps1's client-side
# liveness check compares one share's clock to itself). Instead the contract is explicit and
# caller-facing: -OrphanMetaGraceSec is a promise the CALLER makes about its own worst-case
# placement time, not a guessed constant. Both production callers of this module
# (attr3-footage-stage.ps1) never pass a -SideFile at all, so their claim-to-exposure window is a
# small script copy, comfortably inside the unchanged 60s default; a caller that DOES pass a large
# side file (the manual ATTR3-FIXTURE-REHEARSAL-1 operator workflow in
# docs/playback-attr-3-cuda.md) is the one who knows how long that transfer can legitimately take,
# and now has a real parameter (also exposed on um-run.ps1) to state it, rather than an implicit
# assumption nobody checked. This is the "or never reclaim" alternative the round-10 brief itself
# offered, narrowed to "reclaim only past a caller-declared bound" rather than dropped outright,
# since dropping it entirely would re-introduce the original SUBMIT-RETRY-1 bug (a genuinely dead
# submission bricking every future retry of an operator-chosen JobId forever).
#
# Because the claim now happens BEFORE any side-file/job work, the round-8 fix that re-checked
# metaFinal/final immediately before writing metadata (to narrow a window in which a second,
# no-budget submitter could finish its ENTIRE flow while the first was still mid-transfer) is no
# longer needed as a separate check: that race required a submitter to be able to complete
# everything before ANOTHER submitter had even published its own metadata, which claim-first makes
# impossible by construction -- every submitter's first write is its claim, so the atomic rename
# on inbox\<id>.meta.json is itself the only tiebreaker that can ever matter, for every caller,
# with or without a budget, not a narrower recheck bolted on for the budgeted path alone.

Set-StrictMode -Version Latest

$script:AllowedSideFileExtensions = @('.zip', '.json', '.exe', '.dll', '.txt', '.csv')
$script:DeviceNames = @('CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9')
# The clip fixtures, by STEM. sol, PR #137 r2 BLOCKER: "tracked under tests/fixtures/clips" admits
# every tracked file there, including that directory's README -- and a discovery helper that picked
# the smallest tracked file then proved the bypass rather than the feature. The same two stems the
# attribution generator accepts as -ClipId are the admissible set here, and no extension is named.
$script:TrackedFixtureClipStems = @('tiny_dual_iso', 'large_dual_iso')

function Test-UmRunTrackedFixtureSource {
    <#
    .SYNOPSIS
    True when a side-file's SOURCE is a repository fixture clip, i.e. it sits in a
    `tests/fixtures/clips` directory.
    .DESCRIPTION
    ATTR3-FIXTURE-REHEARSAL-1 has to stage a tracked fixture clip onto a measurement host. Its media
    extension is deliberately NOT added to $AllowedSideFileExtensions: the board's NA-4 gate refuses a
    bare media-extension token in a tools file without an authorization, and composing that token from
    pieces to satisfy the gate's text scan would be routing around a guard rather than meeting it.
    The admissible property is not the extension anyway -- it is that the bytes are a TRACKED FIXTURE
    in THIS repository, which is exactly what NA-4 itself admits. So that is what this tests.

    sol, PR #137 r1 BLOCKER: a lexical segment match admitted any lookalike `tests\fixtures\clips`
    tree anywhere on the machine, including a UNC share and a path THROUGH a junction. Admission is
    therefore anchored to the repository this module ships in, and compared on REAL paths: the
    source's directory, with links resolved, must be the resolved `<repoRoot>\tests\fixtures\clips`
    itself, and the file must be tracked there by git.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        # The repository this module ships in: tools\profiling\UmRunDrop.psm1 -> two levels up.
        [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    )

    $fixturesDir = Join-Path (Join-Path (Join-Path $RepoRoot 'tests') 'fixtures') 'clips'
    if (-not (Test-Path -LiteralPath $fixturesDir -PathType Container)) { return $false }
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) { return $false }

    # Real paths, so a junction/symlink cannot present an outside file as a fixture.
    $resolvedDir = Resolve-UmRunRealDirectory -Path $fixturesDir
    $sourceItem = Get-Item -LiteralPath $SourcePath -Force
    $link = $sourceItem.ResolveLinkTarget($true)
    if ($null -ne $link) { $sourceItem = $link }
    $sourceDir = Resolve-UmRunRealDirectory -Path ([IO.Path]::GetDirectoryName($sourceItem.FullName))
    if (-not [string]::Equals($sourceDir, $resolvedDir, [StringComparison]::OrdinalIgnoreCase)) { return $false }

    # A CLIP fixture, not merely a tracked file in that directory (sol r2: README.md is tracked there).
    $name = [IO.Path]::GetFileName($sourceItem.FullName)
    $stem = [IO.Path]::GetFileNameWithoutExtension($name)
    if ($script:TrackedFixtureClipStems -cnotcontains $stem) { return $false }

    # Tracked in this repository: a file merely dropped into the fixtures directory is not a fixture.
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -eq $git) { return $false }
    $tracked = & $git.Source -C $RepoRoot ls-files --error-unmatch -- ("tests/fixtures/clips/" + $name) 2>$null
    return ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($tracked | Out-String)))
}

function Resolve-UmRunRealDirectory {
    <# Full path of a directory with every link in the chain resolved; '' when it does not exist. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return '' }
    $link = $item.ResolveLinkTarget($true)
    if ($null -ne $link) { $item = $link }
    return $item.FullName.TrimEnd('\')
}

function Get-UmRunShareNowUtc {
    <#
    .SYNOPSIS
    "Now", stamped by the SAME clock domain as a file already on the given share directory --
    never the caller's own clock.
    .DESCRIPTION
    ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 6 (fable minor, orphan-age check): comparing two
    timestamps that both come from a filesystem's own LastWriteTimeUtc needs no assumption that
    the caller's own clock agrees with that filesystem's -- comparing one filesystem timestamp
    against the caller's own Get-Date does. Round 10 factors this out of Invoke-UmRunDrop's own
    orphan-age check so um-run.ps1's liveness wait (comparing heartbeat.txt's own LastWriteTimeUtc
    against elapsed time) can reuse the exact same probe, on the exact same share, instead of a
    second untested copy of it.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        # Test-only: invoked with the probe file's path immediately after it is written, BEFORE its
        # LastWriteTimeUtc is read -- lets a test stamp the probe file with a timestamp that cannot
        # arise from Get-Date, proving the returned value really was read back off the share and
        # not silently substituted with the caller's own clock (round 8 narrowing: two clocks that
        # happen to coincide in a test environment cannot otherwise be told apart by final state).
        [scriptblock]$TestHookAfterProbeWritten = $null,
        # Test-only: invoked with the computed value immediately after it is read, still inside the
        # probe's own try block -- proves this function actually executed and returned a genuine
        # reading, not e.g. a caller that silently reverted to Get-Date at the call site instead.
        [scriptblock]$TestHookAfterProbeRead = $null
    )

    $probe = Join-Path $Directory ".umrun-clock-probe.$([guid]::NewGuid().ToString('N'))"
    try {
        Set-Content -LiteralPath $probe -Value '' -Encoding ascii -NoNewline
        if ($TestHookAfterProbeWritten) { & $TestHookAfterProbeWritten $probe }
        $now = (Get-Item -LiteralPath $probe -Force).LastWriteTimeUtc
        if ($TestHookAfterProbeRead) { & $TestHookAfterProbeRead $now }
        return $now
    } finally {
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    }
}

function Assert-UmRunSideFileName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Inbox,
        [string]$SourcePath = ''
    )

    if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+$') {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' is not a plain basename (letters, digits, '_', '-', single dots between parts)"
    }
    if ($Name -match '\.job\.' -or $Name -match '\.(sidepart|tmp|ps1|psm1|psd1|bat|cmd|vbs|js)$') {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' is job-shaped or executable-script-shaped"
    }
    $extension = [IO.Path]::GetExtension($Name).ToLowerInvariant()
    $trackedFixture = $SourcePath -and (Test-UmRunTrackedFixtureSource -SourcePath $SourcePath)
    if (-not $trackedFixture -and $script:AllowedSideFileExtensions -notcontains $extension) {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' extension '$extension' is not in the allowlist and its source is not a tracked fixture clip"
    }
    $stem = $Name.Split('.')[0].ToUpperInvariant()
    if ($script:DeviceNames -contains $stem) {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' is a Windows device name"
    }
    $full = [IO.Path]::GetFullPath((Join-Path $Inbox $Name))
    if ([IO.Path]::GetFileName($full) -cne $Name -or
        -not [string]::Equals([IO.Path]::GetDirectoryName($full).TrimEnd('\'), [IO.Path]::GetFullPath($Inbox).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' does not normalise to itself directly inside the inbox"
    }
    return $full
}

function Invoke-UmRunDrop {
    <#
    .SYNOPSIS
    Claim a JobId, then place verified side-files, then the job, into an agent inbox. Returns the
    job id.
    .PARAMETER Copier
    Performs one copy: & $Copier <source> <destination>. Defaults to Copy-Item. Tests pass an
    observing or faulty copier; production never does.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Inbox,
        [Parameter(Mandatory = $true)][string]$Outbox,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string]$JobId = '',
        [string[]]$SideFile = @(),
        # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1: the AGENT's per-job budget, written to
        # inbox\<id>.meta.json as part of the claim (round 10: every submission claims metadata,
        # with or without a budget -- 0 means the claim's own metadata omits `timeoutSec`, leaving
        # the agent on its own -JobTimeoutSec default (1800 s), the behaviour every caller had
        # before this whole feature existed. um-run's -TimeoutSec now reaches the agent as the
        # job's own budget for a caller that does state one.
        [int]$JobTimeoutSec = 0,
        [scriptblock]$Copier = { param($Source, $Destination) Copy-Item -LiteralPath $Source -Destination $Destination },
        # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 4 (sol major -- escalates fable's round-3 minor),
        # round 10 (widened scope -- see this module's own header): how long metadata with no paired
        # job and no result is tolerated as "maybe still an in-flight submission" before a later call
        # for the SAME JobId is allowed to reclaim it. Before round 10 the only window a live
        # submission could show this shape was a single rename (sub-second); round 10's claim-first
        # ordering makes it the WHOLE submission (side-files, then the job's own copy), so this is
        # now a caller-stated promise about its own worst-case placement time, not a guessed
        # constant -- see the module header for which callers need to raise it.
        [int]$OrphanMetaGraceSec = 60,
        # Test-only: invoked with no arguments immediately before the job's own rename into view,
        # i.e. the last instant at which "is metadata already published?" is the real contract this
        # module owes the agent (metadata-before-VISIBILITY, not metadata-before-the-job's-own-
        # temporary-copy, which sol's round-4 review named as the wrong thing to have proved).
        [scriptblock]$TestHookBeforeJobVisible = $null,
        # Test-only: invoked with the metadata temp file's path immediately after it is written,
        # before the write-back verification -- lets a test corrupt it to prove that verification
        # actually rejects a torn write rather than merely being present and untested, OR (round 10)
        # plant a competing winner's metadata directly at $metaFinal to prove a losing concurrent
        # claim never overwrites it.
        [scriptblock]$TestHookAfterMetaTmpWritten = $null,
        # Test-only: invoked with the round-6 share-clock probe's computed value immediately after
        # it is read, still inside the probe's own try block -- proves this branch actually executes
        # and yields a genuine clock reading (round 6/7 fable minor: "no test fails if this reverts
        # to Get-Date", true of every other test here, since submitter and share sit on one
        # filesystem and clock and are therefore indistinguishable by final state alone).
        [scriptblock]$TestHookAfterShareClockProbe = $null,
        # Test-only: invoked with the probe file's path immediately after it is written, BEFORE its
        # LastWriteTimeUtc is read. Round 8 (sol minor, narrower than round 6/7's): on one machine,
        # the share's clock and the submitter's own clock read as the same value, so no assertion on
        # the FINAL probed timestamp alone can tell "read from the probe file" apart from "the
        # submitter's own Get-Date" -- replacing the read below with a client Get-Date, while still
        # calling -TestHookAfterShareClockProbe with it, satisfies every prior assertion. This hook
        # lets a test stamp the probe file with a timestamp that CANNOT arise from Get-Date (e.g. a
        # fixed date years away), so the probed value can only match if it truly came from reading
        # the file back off the share.
        [scriptblock]$TestHookAfterShareProbeWritten = $null
    )

    if ($JobId -and $JobId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "UMRUN_JOBID_INVALID '$JobId'" }
    if ($JobId -and $JobId.EndsWith('.')) { throw "UMRUN_JOBID_INVALID '$JobId' ends with a dot" }
    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (fable/sol minor 1): checked BEFORE anything that
    # touches the share -- an invalid budget must never be discovered only after paying for a
    # possibly multi-GB side-file transfer.
    if ($JobTimeoutSec -ne 0 -and ($JobTimeoutSec -lt 1 -or $JobTimeoutSec -gt 86400)) {
        throw "UMRUN_JOB_TIMEOUT_INVALID $JobTimeoutSec is outside the agent's accepted 1..86400 range"
    }
    $id = if ($JobId) { $JobId } else { "job_{0}_{1}" -f (Get-Date -Format 'yyyyMMdd_HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8)) }
    # Fast pre-checks, not the actual protection (the claim's own atomic rename below is): cheap,
    # so still worth failing fast on, but racy on their own (TOCTOU) -- a caller relies on the claim
    # for correctness, on these only for a quick, honest-looking refusal in the common case.
    if (Test-Path -LiteralPath (Join-Path $Outbox "$id.result.json")) {
        throw "UMRUN_JOBID_IN_USE outbox already holds $id.result.json"
    }
    $final = Join-Path $Inbox "$id.job.ps1"
    if (Test-Path -LiteralPath $final) { throw "UMRUN_JOBID_IN_USE inbox already holds $id.job.ps1" }
    if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) { throw "UMRUN_SCRIPT_MISSING $ScriptPath" }

    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 4 (sol major -- escalates fable's round-3 minor,
    # "the interrupt window is still open one rename wide"), round 10 (widened -- see module
    # header): metadata with no job and no result can ONLY be left by a submission that was
    # hard-interrupted somewhere between claiming and exposing the job below -- neither agent ever
    # consumes metadata without its paired job (both move together), so this state is never a live
    # job the agent is running. Once it is older than $OrphanMetaGraceSec, a retry for this JobId
    # self-heals by reclaiming it, rather than being bricked forever (the exact bug this round
    # exists to close). Within the grace period it is still treated as possibly live, matching the
    # pre-round-4 refusal.
    $metaFinal = Join-Path $Inbox "$id.meta.json"
    if (Test-Path -LiteralPath $metaFinal) {
        # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 6 (fable minor): the age check used to subtract
        # $metaFinal's LastWriteTimeUtc from THIS SUBMITTER's own Get-Date -- the "can only be a
        # dead submission" proof silently assumed the two clocks agree to within
        # $OrphanMetaGraceSec, so cross-machine skew at or above the grace could let a concurrent
        # same-JobId submitter reclaim a LIVE submission's metadata. NTFS-over-SMB stamps
        # LastWriteTimeUtc using the FILE SERVER's clock, not the writer's -- so a nonce probe
        # written to (and immediately removed from) this same share, right now, is stamped by that
        # SAME clock. Comparing two timestamps from one clock domain needs no assumption about this
        # submitter's own clock at all. Round 10: this now calls the shared Get-UmRunShareNowUtc
        # helper (below) instead of an inline copy, so um-run.ps1's own liveness wait can reuse it.
        $shareNowUtc = Get-UmRunShareNowUtc -Directory $Inbox `
            -TestHookAfterProbeWritten $TestHookAfterShareProbeWritten `
            -TestHookAfterProbeRead $TestHookAfterShareClockProbe
        $metaAgeSec = ($shareNowUtc - (Get-Item -LiteralPath $metaFinal -Force).LastWriteTimeUtc).TotalSeconds
        if ($metaAgeSec -lt $OrphanMetaGraceSec) {
            throw "UMRUN_JOBID_IN_USE inbox already holds $id.meta.json"
        }
        try {
            Remove-Item -LiteralPath $metaFinal -Force -ErrorAction Stop
        } catch {
            throw "UMRUN_JOBID_IN_USE inbox\$id.meta.json is orphaned from an interrupted earlier submission (age $([int]$metaAgeSec)s) and could not be removed for retry"
        }
        Write-Output ("removed orphaned metadata from an interrupted earlier submission: {0}.meta.json (age {1:N0}s)" -f $id, $metaAgeSec)
    }

    $nonce = [guid]::NewGuid().ToString('N')

    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 10 (the claim itself -- see module header for the
    # full rationale). Written through a nonce temp and renamed, never overwriting: of two racing
    # submitters for the SAME JobId, whichever's Move-Item lands first becomes the sole owner of
    # every side-file and job placement that follows; the other is refused here, before it has
    # touched a single side-file or job byte. `timeoutSec` is included only when a real budget was
    # requested -- omitted otherwise, which both the tracked and deployed agents already treat as
    # "fall back to my own default" (confirmed against the deployed agent; see summary.md).
    $metaTmp = Join-Path $Inbox "$id.$nonce.meta.tmp"
    $metaObj = [ordered]@{ jobId = $id; nonce = $nonce }
    if ($JobTimeoutSec -ne 0) { $metaObj['timeoutSec'] = $JobTimeoutSec }
    $metaJson = $metaObj | ConvertTo-Json -Compress
    try {
        Set-Content -LiteralPath $metaTmp -Value $metaJson -Encoding ascii -NoNewline
        if ($TestHookAfterMetaTmpWritten) { & $TestHookAfterMetaTmpWritten $metaTmp }
        # fable/sol minor 2: side-files are re-read from the share and hash-verified before their
        # rename; the metadata previously was not, so a torn write silently reverted the agent to
        # its own default -- the original bug, undetected. Re-read and compare bytes.
        $metaWrittenBack = Get-Content -LiteralPath $metaTmp -Raw -Encoding ascii
        if ($metaWrittenBack -ne $metaJson) {
            throw "UMRUN_JOB_METADATA_VERIFY_FAILED $id.meta.json did not round-trip to the share"
        }
        try {
            Move-Item -LiteralPath $metaTmp -Destination $metaFinal -ErrorAction Stop   # no -Force
        } catch {
            throw "UMRUN_JOBID_IN_USE inbox\$id.meta.json appeared concurrently; refusing to replace it"
        }
    } finally {
        if (Test-Path -LiteralPath $metaTmp) { Remove-Item -LiteralPath $metaTmp -Force -ErrorAction SilentlyContinue }
    }
    if ($JobTimeoutSec -ne 0) {
        Write-Output ("job metadata placed: {0}.meta.json timeoutSec={1}" -f $id, $JobTimeoutSec)
    } else {
        Write-Output ("job claimed: {0}.meta.json (no budget requested)" -f $id)
    }

    # From here on, this call OWNS the claim above -- any failure in the side-file loop or the
    # job's own placement must roll that claim back (round 2/4's original guarantee, now covering
    # the whole post-claim flow instead of only the metadata-then-job-rename tail): otherwise it
    # outlives the refused submission and bricks every later retry that reuses the same JobId
    # (UMRUN_JOBID_IN_USE at the top of this function) even though no job or result exists.
    try {
        foreach ($path in @($SideFile | ForEach-Object { $_ -split ';' } | Where-Object { $_ })) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "UMRUN_SIDEFILE_MISSING $path" }
            $sourceItem = Get-Item -LiteralPath $path -Force
            $name = $sourceItem.Name
            # The name must be the file's own name AND survive validation; a trailing-dot alias
            # passed as a path reports its canonical Name here, and the literal argument is
            # compared too.
            if ([IO.Path]::GetFileName($path) -cne $name) {
                throw "UMRUN_SIDEFILE_NAME_INVALID '$([IO.Path]::GetFileName($path))' is an alias of '$name'"
            }
            $destination = Assert-UmRunSideFileName -Name $name -Inbox $Inbox -SourcePath $sourceItem.FullName
            $localSha = (Get-FileHash -LiteralPath $sourceItem.FullName -Algorithm SHA256).Hash

            if (Test-Path -LiteralPath $destination) {
                $existing = Get-Item -LiteralPath $destination -Force
                if (-not $existing.PSIsContainer -and (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) -and
                    (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq $localSha) {
                    Write-Output "side-file already present with matching sha256: $name"
                    continue
                }
                throw "UMRUN_SIDEFILE_CONFLICT inbox\$name already exists with DIFFERENT content or is not a plain file"
            }

            $part = Join-Path $Inbox "$name.$nonce.sidepart"
            try {
                & $Copier $sourceItem.FullName $part
                $remoteSha = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash
                if ($remoteSha -ne $localSha) {
                    throw "UMRUN_SIDEFILE_VERIFY_FAILED $name did not round-trip to the share (local $localSha, share $remoteSha)"
                }
                try {
                    Move-Item -LiteralPath $part -Destination $destination -ErrorAction Stop   # no -Force: never overwrite
                } catch {
                    if ((Test-Path -LiteralPath $destination) -and (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq $localSha) {
                        Write-Output "side-file placed concurrently with matching sha256: $name"
                    } else {
                        throw "UMRUN_SIDEFILE_CONFLICT inbox\$name appeared concurrently with different content"
                    }
                }
            } finally {
                if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
            }
            Write-Output ("side-file placed: {0} sha256={1}" -f $name, $localSha.ToLowerInvariant())
        }

        # The job's own bytes are copied to their nonce temp, verified never to have been
        # requested with a budget this call already rejected, then renamed into view -- the agent
        # may claim it the instant it appears, and by now this call's own metadata already
        # governs it (claimed before a single side-file or job byte was copied).
        $tmp = Join-Path $Inbox "$id.$nonce.job.tmp"
        try {
            try {
                & $Copier $ScriptPath $tmp
            } catch {
                if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
                throw
            }
            if ($TestHookBeforeJobVisible) { & $TestHookBeforeJobVisible }
            try {
                Move-Item -LiteralPath $tmp -Destination $final -ErrorAction Stop   # no -Force: a job is never replaced
            } catch {
                throw "UMRUN_JOBID_IN_USE inbox\$id.job.ps1 appeared concurrently; refusing to replace it"
            }
        } finally {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
        }
    } catch {
        # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 6 (sol minor): a rollback deletion failure here
        # used to be silently swallowed (SilentlyContinue), so metadata from a refused submission
        # could outlive it and block a same-JobId retry for the full -OrphanMetaGraceSec against
        # this module's own "a failed submission leaves no metadata" invariant, with no indication
        # why. The ORIGINAL failure is still why this submission failed, so it is captured before
        # attempting cleanup and folded into whatever is thrown, rather than replaced by a
        # cleanup-only error.
        $originalError = $_
        try {
            Remove-Item -LiteralPath $metaFinal -Force -ErrorAction Stop
        } catch {
            throw "$($originalError.Exception.Message) -- additionally, inbox\$id.meta.json could not be removed during rollback and may outlive this refused submission"
        }
        throw
    }
    Write-Output "submitted $id -> $final"
    Write-Output "UMRUN_JOBID=$id"
}

Export-ModuleMember -Function Assert-UmRunSideFileName, Test-UmRunTrackedFixtureSource, Resolve-UmRunRealDirectory, Get-UmRunShareNowUtc, Invoke-UmRunDrop
