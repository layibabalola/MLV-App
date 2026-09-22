# Attr3FootageStageJob.psm1 -- job-TEXT construction for attr3-footage-stage.ps1, split out of
# the CLI generator so a test can build and run a job from SYNTHETIC parts and a SYNTHETIC
# staging directory directly, without the generator's CLI surface ever offering a way to skip the
# resolver (ATTR3-FOOTAGE-STAGE-1; mirrors Attr3FootagePresenceJob.psm1's own split, round 2,
# defect 3, of that generator).
#
# WHAT THIS FUNCTION DOES NOT DO. It never calls the resolver, never reads git, never reads
# tools/gates/output-budget.json or the hook's consent table, and never touches a network share
# itself -- it only builds job TEXT from already-resolved parts. Whether those parts actually
# correspond to a real owner-consented clip, and whether the bytes named by their staging slot
# actually arrived there, are entirely the CALLER's problem: for the real CLI path that caller is
# attr3-footage-stage.ps1, which obtains parts from tools/gates/resolve_consented_clip.py alone
# and stages them itself through AttrCudaOwnerFootage.psm1's Send-AttrCudaOwnerFootagePartToStaging
# before ever calling this function.
#
# WHAT THE EMITTED JOB DOES, ON BACHELOR. First it proves the whole staging chain under
# -AgentRoot, down to and including the per-job staging directory itself, carries no reparse
# point -- before touching anything under it (ATTR3-FOOTAGE-STAGE-1 round 3). For each part: it
# re-verifies the staged copy against the length/sha256 this generator baked in (never trusting
# that a byte-identical local copy stayed byte-identical once it crossed the share), creates the
# resolver's spec directory if it is missing, then PLACES the file at the resolver's spec path
# NON-OVERWRITING via a same-volume verified copy: the staged bytes are copied into an owned,
# per-attempt partial slot ON THE TARGET'S OWN VOLUME, verified there, renamed into place with a
# same-volume atomic rename, and the bytes actually AT the spec path are re-hashed before the
# part is ever reported PLACED -- so a cross-volume interruption can never expose a partial file
# at the spec path, and PLACED always means "these exact bytes are confirmed there now" (round 3;
# a direct cross-volume File.Move used to become copy-then-delete with no such guarantee). A file
# already there with matching bytes is a no-op PASS, a file there with different bytes is refused
# and the target is never touched. Either way the job removes only the staging-share neutral file
# and the local partial slot IT created; nothing else in either directory is ever enumerated or
# touched. Reports by part index and status only -- never a path, in any branch, on any exit.
#
# WHY BASE64, NOT A CHARACTER ALLOWLIST. Same reasoning as Attr3FootagePresenceJob.psm1's own
# header: the job template wraps $PartsJson in a SINGLE-QUOTED PowerShell string literal, so a raw
# path containing a `'` would break out of that literal once JSON-encoded (JSON escapes `"`, not
# `'`). Encoding each path as base64 of its UTF-8 bytes removes the character class that can do
# that; the binding that actually matters -- the resolver's own cross-check of this path against
# the frozen consent table, upstream of this module entirely -- is what is trusted, not a guess at
# which characters a real path can safely contain.

Set-StrictMode -Version Latest

# ATTR3-FOOTAGE-STAGE-1 round 3: see AttrCudaOwnerFootage.psm1's own header for why an
# unconditional `-Force` reimport here is wrong whenever a caller already imported
# AttrCudaArtifacts.psm1 globally first (attr3-footage-stage.ps1 does exactly that before
# importing this module) -- it strips the caller's existing global copy instead of reusing it.
if (-not (Get-Command -Name 'Assert-AttrCudaSafeArtifactName' -ErrorAction SilentlyContinue)) {
    Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1') -Global -ErrorAction Stop
}

function New-Attr3FootageStageJob {
    <#
    .SYNOPSIS
    Build and write a <jobId>.job.ps1 that places already-share-verified footage parts at their
    resolver-designated spec path on Bachelor.
    .DESCRIPTION
    Each part's path is embedded in the emitted job as base64 of its UTF-8 bytes, decoded on
    Bachelor via the module's own Read-AttrCudaBase64Payload (embedded verbatim) -- never as an
    interpolated string. The structural sanity check below (drive letter, colon, separator, no
    control characters) is defense in depth over the resolver's own cross-check against the
    frozen consent table; it is NOT what makes the embedding injection-safe -- the base64
    encoding is, regardless of what this check would have allowed through (see
    Attr3FootagePresenceJob.psm1's identical validation for the same reasoning).
    Throws a distinguishable ATTR3_STAGE_* token on any refusal; returns a pscustomobject
    describing the emitted job otherwise. The returned jobId carries a fresh random component
    (round 3) so every call gets a unique id regardless of content -- a caller names the SAME
    per-job staging directory on the agent share by using this RETURNED value, never by
    recomputing it, and a retried invocation for the SAME clip and parts never collides with an
    earlier attempt's own retained result receipt. sourceSha256 (also returned) is the stable,
    content-derived audit/dedup key the id itself used to be.
    On Bachelor, the emitted job's per-part status is one of PLACED, ALREADY_PRESENT,
    TARGET_CONFLICT, TARGET_PATH_UNSAFE, TARGET_STATE_UNKNOWN, TARGET_DIR_FAILED,
    TARGET_VOLUME_PARTIAL_EXISTS (round 4: the target-volume partial slot is already occupied --
    refused, untouched, never this job's to delete), TARGET_VOLUME_COPY_FAILED,
    TARGET_VOLUME_VERIFY_<Test-AttrCudaFootagePart status> (the same-volume partial copy failed
    verification), PLACED_VERIFY_<status> (the post-rename re-hash at the spec path failed -- this
    job removes the target IT just placed in this case, round 4), STAGE_SLOT_INVALID,
    STAGED_PATH_UNSAFE (round 4: the staged file's own leaf is a reparse point) or STAGED_<status>
    (the staged copy itself failed verification); the
    overall result is FOOTAGE_STAGED (exit 0) when every part is PLACED or ALREADY_PRESENT, else
    FOOTAGE_STAGE_REFUSED (exit 1). No exception's own text ever reaches this job's output, since
    it can contain a real path.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
        [string]$ClipId,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Parts,

        [Parameter(Mandatory = $true)]
        [string]$OutDir,

        # `~` is admitted because Windows temp roots carry 8.3 short names (RUNNER~1, OBABAL~1) and
        # the behavioural tests point -AgentRoot at one; it is inert everywhere this value is used.
        [ValidatePattern('^[A-Za-z]:\\[A-Za-z0-9 _.~\\-]+$')]
        [string]$AgentRoot = 'C:\mlvtmp\mlv-agent',

        # ATTR3-FOOTAGE-STAGE-1 round 4: a TEST-ONLY hook, never reachable from the production
        # CLI (attr3-footage-stage.ps1 never passes it -- see that script's own header on the
        # resolver being the only way it obtains parts). -1 (the default) never matches any real
        # part index and is inert. A test that passes a real index gets a job whose emitted body
        # corrupts that part's already-verified local partial AFTER the local verify but BEFORE
        # the same-volume publish rename -- proving the POST-RENAME re-hash, not the pre-rename
        # check, is what gates a PLACED report, and that a target this job's own rename just
        # created is removed by this same job when that re-hash fails.
        [int]$TestHookCorruptAfterVerifyPartIndex = -1
    )

    if ($Parts.Count -eq 0) {
        throw "ATTR3_STAGE_NO_PARTS zero parts supplied for '$ClipId'"
    }

    foreach ($part in $Parts) {
        $path = [string]$part.path
        if ([string]::IsNullOrEmpty($path)) {
            throw "ATTR3_STAGE_PART_PATH_INVALID part $($part.index) has an empty path"
        }
        # Drive letter, colon, then a forward OR back slash -- both separators are real (every
        # consented part in the frozen spec uses forward slashes; a target path baked by this
        # repository's own tooling may use either).
        if ($path -notmatch '^[A-Za-z]:[\\/]') {
            throw "ATTR3_STAGE_PART_PATH_INVALID part $($part.index) does not start with a drive letter, colon and separator"
        }
        if ($path -match '[\x00-\x1f]') {
            throw "ATTR3_STAGE_PART_PATH_INVALID part $($part.index) contains a control character"
        }
        if ($part.sha256 -notmatch '^[0-9a-f]{64}$') {
            throw "ATTR3_STAGE_PART_SHA_INVALID part $($part.index) sha256 is not 64 lowercase hex"
        }
        if ($part.length -isnot [long] -and $part.length -isnot [int]) {
            throw "ATTR3_STAGE_PART_LENGTH_INVALID part $($part.index) length is not an integer"
        }
    }

    # Re-serialised, compact and key-ordered, so the embedded literal is deterministic and never
    # carries the caller's own incidental whitespace or key order. `path` never appears here --
    # only `pathBase64` does.
    $partsForJob = @($Parts | Sort-Object { [int]$_.index } | ForEach-Object {
        [ordered]@{
            index = [int]$_.index
            pathBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$_.path))
            length = [int64]$_.length
            sha256 = [string]$_.sha256
        }
    })
    $partsJson = $partsForJob | ConvertTo-Json -Compress -Depth 5
    # ConvertTo-Json -Compress on a single-element array still yields a bare object, never
    # `[...]`, unless coerced -- the emitted job always expects a JSON array.
    if ($partsForJob.Count -eq 1) { $partsJson = "[$partsJson]" }

    # A content-derived audit key -- not a security control (the resolver's cross-check, upstream
    # of this module, is) -- reported separately as sourceSha256 below. It is NOT the job id: the
    # caller uses the RETURNED jobId (below) to name the per-job staging directory on the agent
    # share, so nothing requires the id itself to be content-derived.
    $canonicalPayload = ([ordered]@{ clipId = $ClipId; parts = $partsForJob }) | ConvertTo-Json -Compress -Depth 5
    $sha256Alg = [Security.Cryptography.SHA256]::Create()
    try {
        $sourceSha256 = [BitConverter]::ToString(
            $sha256Alg.ComputeHash([Text.Encoding]::UTF8.GetBytes($canonicalPayload))
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256Alg.Dispose()
    }

    # ATTR3-FOOTAGE-STAGE-1 round 3 (astra PR #148 MAJOR): a purely content-derived job id meant a
    # rerun with the SAME clip and parts always submitted the SAME id, so once a prior attempt's
    # result receipt existed on the share, um-run.ps1/UmRunDrop.psm1 refused every retry with
    # UMRUN_JOBID_IN_USE -- forcing a full retransfer to look like the only option, when the real
    # fix is a fresh id per attempt. The random component below makes every call's id unique
    # regardless of content; $sourceSha256 (still reported) remains the stable audit/dedup key.
    $attemptNonce = [guid]::NewGuid().ToString('N').Substring(0, 10)
    $jobId = "attr3-footage-stage-$ClipId-$($sourceSha256.Substring(0, 12))-$attemptNonce"
    [void](Assert-AttrCudaSafeArtifactName -Name "$jobId.job.ps1")

    # ATTR3-FOOTAGE-STAGE-1: Test-AttrCudaFootagePart is the ONE shared per-part content
    # verifier (also embedded, byte-identically, in attr3-footage-presence-job.ps1's probe and
    # playback-attr-3-cuda-job.ps1's owner-id content gate) -- see that function's own header in
    # AttrCudaArtifacts.psm1. Publish-AttrCudaFileMoveNonOverwriting/Assert-AttrCudaNonOverwriting-
    # FileSlot/Assert-AttrCudaWritableFileSlot are the same non-overwriting-publish primitives
    # ATTR3-FIXTURE-STAGE-1's fixture job uses; Assert-AttrCudaDirectChild/Assert-AttrCudaNoLink-
    # BelowRoot/New-AttrCudaDirectory/Remove-AttrCudaPartialFile are the same link-safety
    # primitives every job in this route embeds.
    $embeddedFunctions = Get-AttrCudaEmbeddedFunctionSource -Name @(
        'Read-AttrCudaBase64Payload',
        'ConvertTo-AttrCudaUtf8String',
        'Test-AttrCudaFootagePart',
        'Assert-AttrCudaDirectChild',
        'Assert-AttrCudaNoLinkBelowRoot',
        'New-AttrCudaDirectory',
        'Assert-AttrCudaWritableFileSlot',
        'Assert-AttrCudaNonOverwritingFileSlot',
        'Publish-AttrCudaFileMoveNonOverwriting',
        'Remove-AttrCudaPartialFile'
    )

    # --- job body template (placeholders are substituted below; the body itself never touches
    #     this function's variables directly, so there is no accidental capture of this process's
    #     environment into the emitted script) -------------------------------------------------
    $template = @'
$ErrorActionPreference = 'Stop'
$JobId = '__JOB_ID__'
$ClipId = '__CLIP_ID__'
$AgentRoot = '__AGENT_ROOT__'
$PartsJson = '__PARTS_JSON__'
# Test-only hook (round 4): -1 unless a test explicitly built this job with
# -TestHookCorruptAfterVerifyPartIndex set -- see New-Attr3FootageStageJob's own header.
$TestHookCorruptPartIndex = __TEST_HOOK_CORRUPT_PART_INDEX__
$StageDir = Join-Path $AgentRoot ("footage-stage\" + $JobId)

function Say([string]$Message) { Write-Output "[$JobId] $Message" }

# --- verifiers, embedded VERBATIM from tools/profiling/bachelor/AttrCudaArtifacts.psm1 --------
__EMBEDDED_FUNCTIONS__
# --- end embedded verifiers -------------------------------------------------------------------

$RawParts = @($PartsJson | ConvertFrom-Json | Sort-Object { [int]$_.index })
$PartCount = $RawParts.Count

Say "START clip=$ClipId parts=$PartCount"

# ATTR3-FOOTAGE-STAGE-1 round 3 (astra PR #148 MAJOR, containment): prove the ENTIRE staging
# chain under $AgentRoot -- down to and including $StageDir itself -- carries no reparse point
# BEFORE a single byte is read from or deleted under it. A junction planted at $StageDir (or any
# existing ancestor above it) would otherwise redirect every Copy-Item/Remove-Item below outside
# the owned staging directory. Checked once, up front, rather than per part: nothing under
# $StageDir is touched at all if this refuses.
$stagingChainSafe = $true
try {
    [void](Assert-AttrCudaNoLinkBelowRoot -TrustedRoot $AgentRoot -Path $StageDir)
} catch {
    $stagingChainSafe = $false
}

$results = New-Object System.Collections.Generic.List[object]

if (-not $stagingChainSafe) {
    foreach ($rawPart in $RawParts) {
        $results.Add([ordered]@{ index = [int]$rawPart.index; status = 'STAGE_SLOT_INVALID' })
        Write-Output "PART=$([int]$rawPart.index) STATUS=STAGE_SLOT_INVALID"
    }
    Write-Output "RESULT=FOOTAGE_STAGE_REFUSED CLIP=$ClipId PARTS=$PartCount"
    Write-Output (([ordered]@{
        schema = 'mlvapp.attr3-footage-stage.v1'
        jobId = $JobId
        clipId = $ClipId
        result = 'FOOTAGE_STAGE_REFUSED'
        partCount = $PartCount
        parts = $results
    }) | ConvertTo-Json -Compress -Depth 5)
    exit 1
}

# A single recorder so every one of the branches below cleans up its OWN staged neutral file (or
# explicitly declines to, when there was never a resolvable slot to clean) the same way, rather
# than each branch repeating the same three lines with room for one of them to forget it.
function Record-PartResult([int]$Index, [string]$Status, [string]$CleanupPath) {
    # -WarningAction SilentlyContinue (round 3, no path in any branch): Remove-AttrCudaPartialFile
    # writes a Write-Warning diagnostic naming the path on a refused cleanup -- useful for a human
    # operator tailing this job's own log on Bachelor directly, but this job's RESULT is what
    # travels back to the submitter over um-run.ps1, and that channel must never carry a path.
    if ($CleanupPath) { [void](Remove-AttrCudaPartialFile -TrustedRoot $AgentRoot -Path $CleanupPath -WarningAction SilentlyContinue) }
    $results.Add([ordered]@{ index = $Index; status = $Status })
    Write-Output "PART=$Index STATUS=$Status"
}

foreach ($rawPart in $RawParts) {
    $index = [int]$rawPart.index
    $expectedLength = [int64]$rawPart.length
    $expectedSha256 = [string]$rawPart.sha256
    # The path never travels as a literal: decoded from base64 IN THIS PROCESS, on this host, and
    # used only through -LiteralPath calls below -- never re-embedded into a string PowerShell
    # parses as code.
    $decoded = Read-AttrCudaBase64Payload -Base64 $rawPart.pathBase64
    $targetPath = ConvertTo-AttrCudaUtf8String -Bytes $decoded.bytes
    # The staging slot name is derived from the index alone -- the SAME formula
    # Get-AttrCudaOwnerFootageStagingName (AttrCudaOwnerFootage.psm1) uses -- so this job never
    # trusts a caller-supplied name for a path it is about to read from.
    $stagedName = "part-$index"

    $stagedPath = $null
    try {
        $stagedPath = Assert-AttrCudaDirectChild -Root $StageDir -Path (Join-Path $StageDir $stagedName) -Label "stage part $index"
    } catch {
        Record-PartResult -Index $index -Status 'STAGE_SLOT_INVALID' -CleanupPath $null
        continue
    }

    # ATTR3-FOOTAGE-STAGE-1 round 4 (astra 3, link checks on read paths): the whole-chain check up
    # front (above, before this loop) only proves $StageDir ITSELF carries no reparse point -- the
    # individual leaf "part-<n>" has never been checked. Re-running the SAME chain check with
    # $StageDir as the trusted root walks exactly that one remaining component before a single
    # byte of it is ever hashed or read.
    try {
        [void](Assert-AttrCudaNoLinkBelowRoot -TrustedRoot $StageDir -Path $stagedPath)
    } catch {
        Record-PartResult -Index $index -Status 'STAGED_PATH_UNSAFE' -CleanupPath $stagedPath
        continue
    }

    $stageStatus = Test-AttrCudaFootagePart -Path $stagedPath -ExpectedLength $expectedLength -ExpectedSha256 $expectedSha256
    if ($stageStatus -ne 'PASS') {
        Record-PartResult -Index $index -Status "STAGED_$stageStatus" -CleanupPath $stagedPath
        continue
    }

    $driveRoot = [IO.Path]::GetPathRoot($targetPath)
    try {
        [void](Assert-AttrCudaNoLinkBelowRoot -TrustedRoot $driveRoot -Path $targetPath)
    } catch {
        Record-PartResult -Index $index -Status 'TARGET_PATH_UNSAFE' -CleanupPath $stagedPath
        continue
    }

    # Already present: a byte-identical target is a no-op PASS, a different one is refused --
    # never overwritten -- and either way the staged copy is no longer needed. Wrapped (round 3,
    # no path in any branch): under $ErrorActionPreference = 'Stop' an unwrapped Test-Path call
    # can throw a TERMINATING provider error (e.g. an I/O fault) whose own exception text can
    # carry $targetPath, escaping this loop entirely rather than mapping to a fixed status token.
    $targetExists = $false
    try {
        $targetExists = Test-Path -LiteralPath $targetPath -PathType Leaf -ErrorAction Stop
    } catch {
        Record-PartResult -Index $index -Status 'TARGET_STATE_UNKNOWN' -CleanupPath $stagedPath
        continue
    }
    if ($targetExists) {
        $existingStatus = Test-AttrCudaFootagePart -Path $targetPath -ExpectedLength $expectedLength -ExpectedSha256 $expectedSha256
        if ($existingStatus -eq 'PASS') {
            Record-PartResult -Index $index -Status 'ALREADY_PRESENT' -CleanupPath $stagedPath
        } else {
            Record-PartResult -Index $index -Status 'TARGET_CONFLICT' -CleanupPath $stagedPath
        }
        continue
    }

    # Create the target's own directory chain -- one level at a time, so New-AttrCudaDirectory's
    # own parent-not-a-link check runs at every level, never assumed from the drive-root check
    # above (which only proves the EXISTING ancestors are link-free; nothing below the deepest
    # existing component has been created, or checked, yet).
    $targetDir = [IO.Path]::GetDirectoryName($targetPath)
    $relative = $targetDir.Substring($driveRoot.Length)
    $cursor = $driveRoot.TrimEnd('\')
    $dirCreateFailed = $false
    foreach ($component in $relative.Split('\')) {
        if ([string]::IsNullOrEmpty($component)) { continue }
        $cursor = Join-Path $cursor $component
        try {
            [void](New-AttrCudaDirectory -Path $cursor)
        } catch {
            $dirCreateFailed = $true
            break
        }
    }
    if ($dirCreateFailed) {
        Record-PartResult -Index $index -Status 'TARGET_DIR_FAILED' -CleanupPath $stagedPath
        continue
    }

    # ATTR3-FOOTAGE-STAGE-1 round 3 (sol BLOCKER, astra MAJOR x2): $stagedPath (under $AgentRoot,
    # the agent SHARE staging area) and $targetPath (the resolver's spec path) are not guaranteed
    # to be on the same volume -- a direct File.Move across volumes silently becomes copy-then-
    # delete, which can expose a partial file AT THE SPEC PATH on interruption or a full target
    # volume, with no post-move check that anything actually arrived intact. The fix: copy the
    # already share-verified bytes into an OWNED, per-attempt partial slot ON THE TARGET'S OWN
    # VOLUME (named from $JobId, which is unique per attempt -- so it can never collide with, or
    # be mistaken for, a partial another attempt created), verify THAT copy, publish it with a
    # same-volume non-overwriting rename (genuinely atomic, since both sides are now on one
    # volume), then RE-HASH the bytes actually sitting at the spec path before ever reporting
    # PLACED. Any failure along this path removes only the partial slot THIS attempt created --
    # never $targetPath, never another attempt's partial.
    $localPartialName = ".attr3-footage-stage-$JobId-part$index.partial"
    $localPartialPath = $null
    try {
        $localPartialPath = Assert-AttrCudaDirectChild -Root $targetDir -Path (Join-Path $targetDir $localPartialName) -Label "local partial part $index"
    } catch {
        Record-PartResult -Index $index -Status 'TARGET_PATH_UNSAFE' -CleanupPath $stagedPath
        continue
    }

    # ATTR3-FOOTAGE-STAGE-1 round 4 (sol BLOCKER 2a): the target-volume partial is opened with
    # EXCLUSIVE creation ([IO.FileMode]::CreateNew) -- never a Copy-Item -Force onto a pre-cleared
    # slot -- so a partial another concurrent placer for this exact part is actively writing is
    # never silently deleted and overwritten; it is refused, untouched.
    $localPartialExists = $false
    $localCopyFailed = $false
    $localSrcStream = $null
    $localDstStream = $null
    try {
        try {
            $localSrcStream = [IO.File]::Open($stagedPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        } catch {
            $localCopyFailed = $true
        }
        if (-not $localCopyFailed) {
            try {
                $localDstStream = [IO.File]::Open($localPartialPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            } catch [IO.IOException] {
                $localPartialExists = $true
            } catch {
                $localCopyFailed = $true
            }
        }
        if (-not $localCopyFailed -and -not $localPartialExists) {
            try {
                $localSrcStream.CopyTo($localDstStream)
                $localDstStream.Flush()
            } catch {
                $localCopyFailed = $true
            }
        }
    } finally {
        if ($localSrcStream) { $localSrcStream.Dispose() }
        if ($localDstStream) { $localDstStream.Dispose() }
    }
    if ($localPartialExists) {
        # Refused, untouched: this is NOT ours to delete -- either a concurrent placer for this
        # exact part is still writing it, or a prior attempt's own partial is still there.
        Record-PartResult -Index $index -Status 'TARGET_VOLUME_PARTIAL_EXISTS' -CleanupPath $stagedPath
        continue
    }
    if ($localCopyFailed) {
        try { Remove-Item -LiteralPath $localPartialPath -Force -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        Record-PartResult -Index $index -Status 'TARGET_VOLUME_COPY_FAILED' -CleanupPath $stagedPath
        continue
    }

    $localStatus = Test-AttrCudaFootagePart -Path $localPartialPath -ExpectedLength $expectedLength -ExpectedSha256 $expectedSha256
    if ($localStatus -ne 'PASS') {
        try { Remove-Item -LiteralPath $localPartialPath -Force -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        Record-PartResult -Index $index -Status "TARGET_VOLUME_VERIFY_$localStatus" -CleanupPath $stagedPath
        continue
    }

    if ($index -eq $TestHookCorruptPartIndex) {
        # Test-only hook (round 4): corrupts the LOCAL, already-verified partial's bytes AFTER
        # the local verify above but BEFORE the same-volume publish rename below -- see this
        # function's own header for what this proves.
        $corruptStream = [IO.File]::Open($localPartialPath, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $corruptStream.WriteByte(0) } finally { $corruptStream.Dispose() }
    }

    try {
        [void](Publish-AttrCudaFileMoveNonOverwriting -Source $localPartialPath -Destination $targetPath)
    } catch {
        # A concurrent placer finished this exact part first -- re-check the bytes already there
        # rather than assume either outcome. Either way the same-volume rename never happened, so
        # the local partial this attempt made is still there and still needs cleaning up.
        $racedStatus = Test-AttrCudaFootagePart -Path $targetPath -ExpectedLength $expectedLength -ExpectedSha256 $expectedSha256
        try { Remove-Item -LiteralPath $localPartialPath -Force -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        if ($racedStatus -eq 'PASS') {
            Record-PartResult -Index $index -Status 'ALREADY_PRESENT' -CleanupPath $stagedPath
        } else {
            Record-PartResult -Index $index -Status 'TARGET_CONFLICT' -CleanupPath $stagedPath
        }
        continue
    }

    # The rename succeeded -- re-hash the bytes actually AT THE SPEC PATH now. A same-volume
    # rename is atomic against a concurrent READER or another RENAME, but never trust that alone
    # proves the arrived bytes are correct: report PLACED only when a fresh read confirms it.
    $placedStatus = Test-AttrCudaFootagePart -Path $targetPath -ExpectedLength $expectedLength -ExpectedSha256 $expectedSha256
    if ($placedStatus -ne 'PASS') {
        # ATTR3-FOOTAGE-STAGE-1 round 4 (sol BLOCKER 2b): the non-overwriting rename just above
        # created $targetPath as THIS JOB'S OWN OBJECT -- nothing else could already have been
        # there, or the rename itself would have thrown ATTRCUDA_NONOVERWRITE_DESTINATION_EXISTS
        # instead of succeeding. A failed post-rename re-hash therefore means the bytes THIS JOB
        # just placed are wrong, so this job -- and only this job -- removes them, rather than
        # leaving a corrupt file at the spec path under a PLACED-shaped status. A rerun's own
        # ALREADY_PRESENT check then sees a clean absence, never a false TARGET_CONFLICT against
        # bytes this job itself left broken.
        try { Remove-Item -LiteralPath $targetPath -Force -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        Record-PartResult -Index $index -Status "PLACED_VERIFY_$placedStatus" -CleanupPath $stagedPath
        continue
    }
    # The rename already relocated the local partial -- nothing left there to clean -- but
    # $stagedPath (the SHARE-side staged copy) was only ever COPIED from, never moved, so it is
    # still there and still needs cleaning up now that its bytes are safely verified at the target.
    Record-PartResult -Index $index -Status 'PLACED' -CleanupPath $stagedPath
}

# Overall-result mapping: only a part that is actually AT the target (placed just now, or
# already there with matching bytes) counts as staged; anything else -- a bad staged copy, an
# unsafe or conflicting target, a directory that could not be created -- refuses the whole job.
$statuses = @($results | ForEach-Object { $_.status })
$okStatuses = @('PLACED', 'ALREADY_PRESENT')
if (($statuses | Where-Object { $okStatuses -notcontains $_ }).Count -eq 0) {
    $overall = 'FOOTAGE_STAGED'; $exitCode = 0
} else {
    $overall = 'FOOTAGE_STAGE_REFUSED'; $exitCode = 1
}

Write-Output "RESULT=$overall CLIP=$ClipId PARTS=$PartCount"
Write-Output (([ordered]@{
    schema = 'mlvapp.attr3-footage-stage.v1'
    jobId = $JobId
    clipId = $ClipId
    result = $overall
    partCount = $PartCount
    parts = $results
}) | ConvertTo-Json -Compress -Depth 5)
exit $exitCode
'@

    # ATTR3-FOOTAGE-STAGE-1, following ATTR3-FOOTAGE-BIND-1 PR-B round 3 (STRUCTURAL): a
    # single-pass substitution over the WHOLE token map at once (Expand-AttrCudaTemplate's own
    # header explains why a chained .Replace(...).Replace(...) sequence is unsafe here).
    $text = Expand-AttrCudaTemplate -Template $template -Tokens ([ordered]@{
        JOB_ID = $jobId
        CLIP_ID = $ClipId
        AGENT_ROOT = $AgentRoot
        PARTS_JSON = $partsJson
        EMBEDDED_FUNCTIONS = $embeddedFunctions
        TEST_HOOK_CORRUPT_PART_INDEX = $TestHookCorruptAfterVerifyPartIndex
    })

    if (-not (Test-Path -LiteralPath $OutDir)) { [void](New-Item -ItemType Directory -Path $OutDir -Force) }
    $OutDir = (Resolve-Path -LiteralPath $OutDir).Path
    $jobPath = Join-Path $OutDir "$jobId.job.ps1"
    [IO.File]::WriteAllText($jobPath, $text, [Text.UTF8Encoding]::new($false))

    # ATTR3-FOOTAGE-STAGE-1 round 3 (no path in any branch): JOB= now names the opaque job id,
    # never the local job FILE path -- the id alone is enough for a caller to correlate this
    # emission with the submission that follows.
    Write-Output "RESULT=FOOTAGE_STAGE_JOB_EMITTED CLIP=$ClipId PARTS=$($partsForJob.Count) SOURCE_SHA256=$sourceSha256 JOB=$jobId"

    [pscustomobject]@{
        jobFile = $jobPath
        jobId = $jobId
        clipId = $ClipId
        partCount = $partsForJob.Count
        sourceSha256 = $sourceSha256
        agentRoot = $AgentRoot
    }
}

Export-ModuleMember -Function New-Attr3FootageStageJob
