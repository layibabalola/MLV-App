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
# WHAT THE EMITTED JOB DOES, ON BACHELOR. For each part: re-verifies the staged copy against the
# length/sha256 this generator baked in (never trusting that a byte-identical local copy stayed
# byte-identical once it crossed the share), creates the resolver's spec directory if it is
# missing, and places the file at the resolver's spec path NON-OVERWRITING -- a file already there
# with matching bytes is a no-op PASS, a file there with different bytes is refused and the target
# is never touched. Either way the job removes only the staged neutral file IT created; nothing
# else in the staging directory is ever enumerated or touched. Reports by part index and status
# only -- never a path, in any branch, on any exit.
#
# WHY BASE64, NOT A CHARACTER ALLOWLIST. Same reasoning as Attr3FootagePresenceJob.psm1's own
# header: the job template wraps $PartsJson in a SINGLE-QUOTED PowerShell string literal, so a raw
# path containing a `'` would break out of that literal once JSON-encoded (JSON escapes `"`, not
# `'`). Encoding each path as base64 of its UTF-8 bytes removes the character class that can do
# that; the binding that actually matters -- the resolver's own cross-check of this path against
# the frozen consent table, upstream of this module entirely -- is what is trusted, not a guess at
# which characters a real path can safely contain.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1') -Force

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
    describing the emitted job otherwise. The returned jobId is content-derived (a sha256 of the
    clip id and every part's index/length/sha256) so a caller can name the SAME per-job staging
    directory on the agent share before this job ever runs there.
    On Bachelor, the emitted job's per-part status is one of PLACED, ALREADY_PRESENT,
    TARGET_CONFLICT, TARGET_PATH_UNSAFE, TARGET_DIR_FAILED, STAGE_SLOT_INVALID, or
    STAGED_<Test-AttrCudaFootagePart status> (the staged copy itself failed verification); the
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

        [ValidatePattern('^[A-Za-z]:\\[A-Za-z0-9 _.\\-]+$')]
        [string]$AgentRoot = 'C:\mlvtmp\mlv-agent'
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

    # A stable, content-derived disambiguator for the job id -- not a security control (the
    # resolver's cross-check, upstream of this module, is), just a dedup/audit key that changes
    # when the baked content does. The caller uses this SAME value to name the per-job staging
    # directory on the agent share, so it is derived from clipId and parts alone, never from
    # -AgentRoot (which can differ per host without changing what is being staged).
    $canonicalPayload = ([ordered]@{ clipId = $ClipId; parts = $partsForJob }) | ConvertTo-Json -Compress -Depth 5
    $sha256Alg = [Security.Cryptography.SHA256]::Create()
    try {
        $sourceSha256 = [BitConverter]::ToString(
            $sha256Alg.ComputeHash([Text.Encoding]::UTF8.GetBytes($canonicalPayload))
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256Alg.Dispose()
    }

    $jobId = "attr3-footage-stage-$ClipId-$($sourceSha256.Substring(0, 12))"
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
$StageDir = Join-Path $AgentRoot ("footage-stage\" + $JobId)

function Say([string]$Message) { Write-Output "[$JobId] $Message" }

# --- verifiers, embedded VERBATIM from tools/profiling/bachelor/AttrCudaArtifacts.psm1 --------
__EMBEDDED_FUNCTIONS__
# --- end embedded verifiers -------------------------------------------------------------------

$RawParts = @($PartsJson | ConvertFrom-Json | Sort-Object { [int]$_.index })
$PartCount = $RawParts.Count

Say "START clip=$ClipId parts=$PartCount"

$results = New-Object System.Collections.Generic.List[object]

# A single recorder so every one of the branches below cleans up its OWN staged neutral file (or
# explicitly declines to, when there was never a resolvable slot to clean) the same way, rather
# than each branch repeating the same three lines with room for one of them to forget it.
function Record-PartResult([int]$Index, [string]$Status, [string]$CleanupPath) {
    if ($CleanupPath) { [void](Remove-AttrCudaPartialFile -TrustedRoot $AgentRoot -Path $CleanupPath) }
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
    # never overwritten -- and either way the staged copy is no longer needed.
    if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
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

    try {
        [void](Publish-AttrCudaFileMoveNonOverwriting -Source $stagedPath -Destination $targetPath)
    } catch {
        # A concurrent placer finished this exact part first -- re-check the bytes already there
        # rather than assume either outcome. Either way the move never happened, so the staged
        # copy this job made is still $stagedPath and still needs cleaning up.
        $racedStatus = Test-AttrCudaFootagePart -Path $targetPath -ExpectedLength $expectedLength -ExpectedSha256 $expectedSha256
        if ($racedStatus -eq 'PASS') {
            Record-PartResult -Index $index -Status 'ALREADY_PRESENT' -CleanupPath $stagedPath
        } else {
            Record-PartResult -Index $index -Status 'TARGET_CONFLICT' -CleanupPath $stagedPath
        }
        continue
    }
    # The move already relocated the staged copy -- there is nothing left at $stagedPath to clean.
    Record-PartResult -Index $index -Status 'PLACED' -CleanupPath $null
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
    })

    if (-not (Test-Path -LiteralPath $OutDir)) { [void](New-Item -ItemType Directory -Path $OutDir -Force) }
    $OutDir = (Resolve-Path -LiteralPath $OutDir).Path
    $jobPath = Join-Path $OutDir "$jobId.job.ps1"
    [IO.File]::WriteAllText($jobPath, $text, [Text.UTF8Encoding]::new($false))

    Write-Output "RESULT=FOOTAGE_STAGE_JOB_EMITTED CLIP=$ClipId PARTS=$($partsForJob.Count) SOURCE_SHA256=$sourceSha256 JOB=$jobPath"

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
