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
#   - a budget (-JobTimeoutSec) is range-checked before any side-file is copied, and its metadata
#     is removed if the job placement that follows it fails or is refused, so a failed submission
#     never bricks a later retry of the same JobId (ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2);
#   - the job's own bytes are copied to their temporary FIRST, metadata is published only once
#     those bytes already sit on the share, and the job is exposed (renamed into view) immediately
#     after -- so the window in which metadata is visible with no job to consume it is a single
#     rename, not the whole job copy (round 4, escalating fable's round-3 minor to sol's major: a
#     hard interruption -- a kill, not a thrown exception -- during that window cannot be caught,
#     so metadata surviving it is a certainty, not a bug to eliminate; it is instead SELF-HEALED on
#     the next submission attempt for the same JobId once -OrphanMetaGraceSec has passed, since
#     neither agent ever consumes metadata without its paired job (both move together), so metadata
#     with no job and no result, older than the grace period, is provably from a dead submission.

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
    Place verified side-files, then the job, into an agent inbox. Returns the job id.
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
        # inbox\<id>.meta.json BEFORE the job becomes visible (the agent's own contract: "VM submit
        # metadata written before the job is visible"). 0 means "write no metadata", which leaves the
        # agent on its own -JobTimeoutSec default (1800 s) -- the behaviour every caller had before,
        # and the reason a multi-GB placement was killed at 30 min while its caller had asked for an
        # hour: um-run's -TimeoutSec reached only the CLIENT poll and never crossed to the host.
        [int]$JobTimeoutSec = 0,
        [scriptblock]$Copier = { param($Source, $Destination) Copy-Item -LiteralPath $Source -Destination $Destination },
        # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 4 (sol major -- escalates fable's round-3 minor):
        # how long metadata with no paired job and no result is tolerated as "maybe still an
        # in-flight submission" before a later call for the SAME JobId is allowed to reclaim it. The
        # only window in which a live submission legitimately shows this exact shape is the single
        # rename below (metadata already published, job not yet exposed) -- sub-second in practice
        # -- so the default is generous headroom for that race, not a real wait a caller ever sees.
        [int]$OrphanMetaGraceSec = 60,
        # Test-only: invoked with no arguments immediately before the job's own rename into view,
        # i.e. the last instant at which "is metadata already published?" is the real contract this
        # module owes the agent (metadata-before-VISIBILITY, not metadata-before-the-job's-own-
        # temporary-copy, which sol's round-4 review named as the wrong thing to have proved).
        [scriptblock]$TestHookBeforeJobVisible = $null,
        # Test-only: invoked with the metadata temp file's path immediately after it is written,
        # before the write-back verification -- lets a test corrupt it to prove that verification
        # actually rejects a torn write rather than merely being present and untested.
        [scriptblock]$TestHookAfterMetaTmpWritten = $null
    )

    if ($JobId -and $JobId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "UMRUN_JOBID_INVALID '$JobId'" }
    if ($JobId -and $JobId.EndsWith('.')) { throw "UMRUN_JOBID_INVALID '$JobId' ends with a dot" }
    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (fable/sol minor 1): checked BEFORE the side-file
    # loop below, which can be a multi-GB transfer -- an invalid budget must never be discovered
    # only after paying for that transfer.
    if ($JobTimeoutSec -ne 0 -and ($JobTimeoutSec -lt 1 -or $JobTimeoutSec -gt 86400)) {
        throw "UMRUN_JOB_TIMEOUT_INVALID $JobTimeoutSec is outside the agent's accepted 1..86400 range"
    }
    $id = if ($JobId) { $JobId } else { "job_{0}_{1}" -f (Get-Date -Format 'yyyyMMdd_HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8)) }
    if (Test-Path -LiteralPath (Join-Path $Outbox "$id.result.json")) {
        throw "UMRUN_JOBID_IN_USE outbox already holds $id.result.json"
    }
    $final = Join-Path $Inbox "$id.job.ps1"
    if (Test-Path -LiteralPath $final) { throw "UMRUN_JOBID_IN_USE inbox already holds $id.job.ps1" }
    if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) { throw "UMRUN_SCRIPT_MISSING $ScriptPath" }

    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 4 (sol major -- escalates fable's round-3 minor,
    # "the interrupt window is still open one rename wide"). Metadata with no job and no result can
    # ONLY be left by a submission that was hard-interrupted between publishing metadata and
    # exposing the job below -- neither agent ever consumes metadata without its paired job (both
    # move together), so this state is never a live job the agent is running. Once it is older than
    # $OrphanMetaGraceSec (comfortably past the single-rename window a live submission could still
    # be inside), a retry for this JobId self-heals by reclaiming it, rather than being bricked
    # forever (the exact bug this round exists to close). Within the grace period it is still
    # treated as possibly live, matching the pre-round-4 refusal.
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
        # submitter's own clock at all.
        $shareNowProbe = Join-Path $Inbox ".umrun-clock-probe.$([guid]::NewGuid().ToString('N'))"
        try {
            Set-Content -LiteralPath $shareNowProbe -Value '' -Encoding ascii -NoNewline
            $shareNowUtc = (Get-Item -LiteralPath $shareNowProbe -Force).LastWriteTimeUtc
        } finally {
            Remove-Item -LiteralPath $shareNowProbe -Force -ErrorAction SilentlyContinue
        }
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
    foreach ($path in @($SideFile | ForEach-Object { $_ -split ';' } | Where-Object { $_ })) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "UMRUN_SIDEFILE_MISSING $path" }
        $sourceItem = Get-Item -LiteralPath $path -Force
        $name = $sourceItem.Name
        # The name must be the file's own name AND survive validation; a trailing-dot alias passed
        # as a path reports its canonical Name here, and the literal argument is compared too.
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

    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 4 (sol major, reordering fable/sol's round-2/3
    # metadata-before-job-visibility rule): the job's own bytes are copied to their nonce temp
    # FIRST, before metadata is written at all. Previously metadata was published, THEN the job was
    # copied and renamed -- so a hard interruption during that copy (which can be the whole transfer
    # for a large job body) left metadata visible with nothing to ever consume it. Copying first
    # means metadata is only ever published once the job's bytes already sit on the share, so the
    # window where metadata is visible with no job is just the rename below, not the copy above it.
    $tmp = Join-Path $Inbox "$id.$nonce.job.tmp"
    try {
        & $Copier $ScriptPath $tmp
    } catch {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
        throw
    }

    # The agent reads inbox\<id>.meta.json when it picks the job up, and accepts timeoutSec in
    # 1..86400, falling back to its own default when the value is missing or unparseable. It must
    # therefore be in place BEFORE the job file is renamed into view -- same ordering rule the
    # side-files above obey, and for the same reason: the agent may claim the job the instant it
    # appears. Written through a nonce temp and renamed, never overwriting.
    #
    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (fable/sol major 1): if metadata is placed here but
    # the job placement below then fails or is refused, this submission's own metadata is removed in
    # that failure's catch block -- otherwise it outlives the refused submission and bricks every
    # later retry that reuses the same JobId (UMRUN_JOBID_IN_USE at the check above) even though no
    # job or result exists. $metaPlacedHere tracks ONLY metadata this call itself placed, never a
    # pre-existing file (which already throws UMRUN_JOBID_IN_USE, or is self-healed, before reaching
    # here).
    $metaPlacedHere = $false
    try {
        if ($JobTimeoutSec -ne 0) {
            if (Test-Path -LiteralPath $metaFinal) { throw "UMRUN_JOBID_IN_USE inbox already holds $id.meta.json" }
            $metaTmp = Join-Path $Inbox "$id.$nonce.meta.tmp"
            $metaJson = [ordered]@{ jobId = $id; timeoutSec = $JobTimeoutSec } | ConvertTo-Json -Compress
            try {
                Set-Content -LiteralPath $metaTmp -Value $metaJson -Encoding ascii -NoNewline
                if ($TestHookAfterMetaTmpWritten) { & $TestHookAfterMetaTmpWritten $metaTmp }
                # fable/sol minor 2: side-files are re-read from the share and hash-verified before
                # their rename; the metadata previously was not, so a torn write silently reverted the
                # agent to its own default -- the original bug, undetected. Re-read and compare bytes.
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
            $metaPlacedHere = $true
            Write-Output ("job metadata placed: {0}.meta.json timeoutSec={1}" -f $id, $JobTimeoutSec)
        }

        if ($TestHookBeforeJobVisible) { & $TestHookBeforeJobVisible }
        try {
            Move-Item -LiteralPath $tmp -Destination $final -ErrorAction Stop   # no -Force: a job is never replaced
        } catch {
            throw "UMRUN_JOBID_IN_USE inbox\$id.job.ps1 appeared concurrently; refusing to replace it"
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
        if ($metaPlacedHere) {
            try {
                Remove-Item -LiteralPath $metaFinal -Force -ErrorAction Stop
            } catch {
                throw "$($originalError.Exception.Message) -- additionally, inbox\$id.meta.json could not be removed during rollback and may outlive this refused submission"
            }
        }
        throw
    } finally {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
    Write-Output "submitted $id -> $final"
    Write-Output "UMRUN_JOBID=$id"
}

Export-ModuleMember -Function Assert-UmRunSideFileName, Test-UmRunTrackedFixtureSource, Resolve-UmRunRealDirectory, Invoke-UmRunDrop
