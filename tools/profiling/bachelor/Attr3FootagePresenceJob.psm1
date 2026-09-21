# Attr3FootagePresenceJob.psm1 -- job-TEXT construction for attr3-footage-presence-job.ps1, split
# out of the CLI generator so a test can build a job from SYNTHETIC parts directly, without the
# generator's CLI surface ever offering a way to skip the resolver (ATTR3-FOOTAGE-BIND-1, PR-A,
# round 2, defect 3).
#
# THE DEFECT THIS CLOSES. -InjectResolvedPartsJsonPathForTests used to be a public parameter on
# the CLI generator (attr3-footage-presence-job.ps1): passing it from ANY caller, not just a test,
# skipped the resolver child process entirely -- and with it, the resolver's cross-check of the
# frozen spec against the hook's frozen consent table. New-Attr3FootagePresenceJob below takes
# already-resolved parts as in-memory objects; nothing on the generator's real CLI surface
# (-ClipId/-OutDir/-RepoRoot only -- see that script) can reach this function except by calling
# the resolver first and handing its output here. A test imports THIS MODULE directly and calls
# the function with fabricated parts, which is an ordinary PowerShell function call, not a flag on
# the production entry point.
#
# WHAT THIS FUNCTION DOES NOT DO. It never calls the resolver, never reads git, and never reads
# tools/gates/output-budget.json or the hook's consent table. Whether the parts it is handed
# actually correspond to a real owner-consented clip is entirely the caller's problem; for the
# real CLI path that caller is attr3-footage-presence-job.ps1, which obtains parts from
# tools/gates/resolve_consented_clip.py alone.
#
# WHY BASE64, NOT A CHARACTER ALLOWLIST (round 2, defect 1). Every real consented part path in
# tools/gates/output-budget.json uses FORWARD slashes (drive letter, colon, then `/`-separated
# segments), and a measurement host's own TEMP root can carry an `~` from an 8.3 short name --
# both real, both previously rejected by a backslash-only allowlist regex that this module does
# not carry forward. What actually has to be safe is the embedding: the job template below wraps
# $PartsJson in a SINGLE-QUOTED PowerShell string literal, so a raw path containing a `'` would
# break out of that literal into arbitrary template code once JSON-encoded (JSON escapes `"`, not
# `'`). Encoding each path as base64 of its UTF-8 bytes removes the character class that can do
# that -- base64 output is `[A-Za-z0-9+/=]` only -- so the binding that actually matters (the
# resolver's cross-check against the frozen table, including path_norm_sha256) is what is trusted,
# not a guess at which characters a real path can safely contain.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1') -Force

function New-Attr3FootagePresenceJob {
    <#
    .SYNOPSIS
    Build and write a <jobId>.job.ps1 footage-presence probe from already-resolved parts.
    .DESCRIPTION
    Each part's path is embedded in the emitted job as base64 of its UTF-8 bytes, decoded on
    Bachelor via the module's own Read-AttrCudaBase64Payload (embedded verbatim, the same helper
    tools/profiling/bachelor/attr3-stage-smoke-runner-job.ps1 uses for its inline payload) --
    never as an interpolated string. The structural sanity check below (drive letter, colon,
    separator, no control characters) is defense in depth over the resolver's own cross-check
    against the frozen consent table; it is NOT what makes the embedding injection-safe -- the
    base64 encoding is, regardless of what this check would have allowed through.
    Throws a distinguishable ATTR3_PRESENCE_* token on any refusal; returns a pscustomobject
    describing the emitted job otherwise.
    On Bachelor, the emitted job checks each part with every filesystem call wrapped in its own
    try/catch (round 4), reporting an honest per-part status -- PASS, NOT_FOUND, ACCESS_DENIED,
    UNREADABLE, LENGTH_MISMATCH or SHA256_MISMATCH -- and an overall result of FOOTAGE_PRESENT
    (exit 0), FOOTAGE_ABSENT (exit 1), FOOTAGE_MISMATCH (exit 2) or FOOTAGE_INDETERMINATE (exit
    3); see the mapping documented above the overall-result block in the job template below for
    the exact rule. No exception's own text ever reaches this job's output, since it can contain
    the part's real path.
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
        [string]$OutDir
    )

    if ($Parts.Count -eq 0) {
        throw "ATTR3_PRESENCE_NO_PARTS zero parts supplied for '$ClipId'"
    }

    foreach ($part in $Parts) {
        $path = [string]$part.path
        if ([string]::IsNullOrEmpty($path)) {
            throw "ATTR3_PRESENCE_PART_PATH_INVALID part $($part.index) has an empty path"
        }
        # Drive letter, colon, then a forward OR back slash -- both separators are real (see
        # module header); anything after that, including `~`, is unconstrained here.
        if ($path -notmatch '^[A-Za-z]:[\\/]') {
            throw "ATTR3_PRESENCE_PART_PATH_INVALID part $($part.index) does not start with a drive letter, colon and separator"
        }
        if ($path -match '[\x00-\x1f]') {
            throw "ATTR3_PRESENCE_PART_PATH_INVALID part $($part.index) contains a control character"
        }
        if ($part.sha256 -notmatch '^[0-9a-f]{64}$') {
            throw "ATTR3_PRESENCE_PART_SHA_INVALID part $($part.index) sha256 is not 64 lowercase hex"
        }
        if ($part.length -isnot [long] -and $part.length -isnot [int]) {
            throw "ATTR3_PRESENCE_PART_LENGTH_INVALID part $($part.index) length is not an integer"
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
    # resolver's cross-check is), just a dedup/audit key that changes when the baked content does.
    $canonicalPayload = ([ordered]@{ clipId = $ClipId; parts = $partsForJob }) | ConvertTo-Json -Compress -Depth 5
    $sha256Alg = [Security.Cryptography.SHA256]::Create()
    try {
        $sourceSha256 = [BitConverter]::ToString(
            $sha256Alg.ComputeHash([Text.Encoding]::UTF8.GetBytes($canonicalPayload))
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256Alg.Dispose()
    }

    $jobId = "attr3-footage-presence-$ClipId-$($sourceSha256.Substring(0, 12))"
    [void](Assert-AttrCudaSafeArtifactName -Name "$jobId.job.ps1")

    $embeddedFunctions = Get-AttrCudaEmbeddedFunctionSource -Name @('Read-AttrCudaBase64Payload')

    # --- job body template (placeholders are substituted below; the body itself never touches
    #     this function's variables directly, so there is no accidental capture of this process's
    #     environment into the emitted script) -------------------------------------------------
    $template = @'
$ErrorActionPreference = 'Stop'
$JobId = '__JOB_ID__'
$ClipId = '__CLIP_ID__'
$PartsJson = '__PARTS_JSON__'

function Say([string]$Message) { Write-Output "[$JobId] $Message" }

# --- verifiers, embedded VERBATIM from tools/profiling/bachelor/AttrCudaArtifacts.psm1 --------
__EMBEDDED_FUNCTIONS__
# --- end embedded verifiers -------------------------------------------------------------------

$RawParts = @($PartsJson | ConvertFrom-Json)
$PartCount = $RawParts.Count

Say "START clip=$ClipId parts=$PartCount"

$results = New-Object System.Collections.Generic.List[object]
foreach ($rawPart in $RawParts) {
    # The path never travels as a literal: it is decoded from base64 IN THIS PROCESS, on this
    # host, and used only through -LiteralPath calls below -- never re-embedded into a string
    # that PowerShell parses as code.
    $decoded = Read-AttrCudaBase64Payload -Base64 $rawPart.pathBase64
    $partPath = [Text.Encoding]::UTF8.GetString($decoded.bytes)

    # Every filesystem operation on this part is wrapped in its own try/catch (round 4, defect
    # 4). Under $ErrorActionPreference = 'Stop', an UNWRAPPED Test-Path/Get-Item call can throw a
    # TERMINATING error on a provider or access failure -- which would escape this loop entirely
    # (skipping every remaining part and the final RESULT/JSON lines) and print the exception's
    # own text, which can contain $partPath, to this job's stderr via PowerShell's default
    # uncaught-error reporting. Nothing below ever writes $_, $_.Exception or its .Message --
    # only a fixed status TOKEN never derived from exception text.
    $status = $null
    $actualLength = $null
    try {
        $actualLength = (Get-Item -LiteralPath $partPath -Force -ErrorAction Stop).Length
    } catch [System.Management.Automation.ItemNotFoundException] {
        $status = 'NOT_FOUND'
    } catch [System.UnauthorizedAccessException] {
        $status = 'ACCESS_DENIED'
    } catch {
        $status = if ($_.CategoryInfo.Category -eq 'PermissionDenied') { 'ACCESS_DENIED' } else { 'UNREADABLE' }
    }

    if (-not $status) {
        if ($actualLength -ne [int64]$rawPart.length) {
            $status = 'LENGTH_MISMATCH'
        } else {
            try {
                $actualSha256 = (Get-FileHash -LiteralPath $partPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
                $status = if ($actualSha256 -eq $rawPart.sha256) { 'PASS' } else { 'SHA256_MISMATCH' }
            } catch [System.UnauthorizedAccessException] {
                $status = 'ACCESS_DENIED'
            } catch {
                $status = if ($_.CategoryInfo.Category -eq 'PermissionDenied') { 'ACCESS_DENIED' } else { 'UNREADABLE' }
            }
        }
    }
    Write-Output "PART=$($rawPart.index) STATUS=$status"
    $results.Add([ordered]@{
        index = $rawPart.index
        status = $status
        length = $rawPart.length
        sha256_12 = ([string]$rawPart.sha256).Substring(0, 12)
    })
}

# Overall-result mapping (round 4, defect 5). Each per-part status above is honest about WHY a
# part failed, so the overall result can distinguish "definitely not there" from "could not
# tell" instead of folding every non-PASS reason into MISSING/UNREADABLE as before:
#   FOOTAGE_PRESENT       exit 0  every part is PASS.
#   FOOTAGE_ABSENT        exit 1  every part is NOT_FOUND.
#   FOOTAGE_MISMATCH      exit 2  at least one part is LENGTH_MISMATCH or SHA256_MISMATCH (a
#                                 byte difference was actually OBSERVED on a readable part) AND
#                                 no part is ACCESS_DENIED or UNREADABLE.
#   FOOTAGE_INDETERMINATE exit 3  anything else -- e.g. any ACCESS_DENIED/UNREADABLE part, or a
#                                 mix of PASS and NOT_FOUND with no part actually differing --
#                                 cannot honestly be called PRESENT, ABSENT or MISMATCH.
$statuses = @($results | ForEach-Object { $_.status })
$diffStatuses = @('LENGTH_MISMATCH', 'SHA256_MISMATCH')
$uncertainStatuses = @('ACCESS_DENIED', 'UNREADABLE')
if (($statuses | Where-Object { $_ -ne 'PASS' }).Count -eq 0) {
    $overall = 'FOOTAGE_PRESENT'; $exitCode = 0
} elseif (($statuses | Where-Object { $_ -ne 'NOT_FOUND' }).Count -eq 0) {
    $overall = 'FOOTAGE_ABSENT'; $exitCode = 1
} elseif (
    ($statuses | Where-Object { $diffStatuses -contains $_ }).Count -ge 1 -and
    ($statuses | Where-Object { $uncertainStatuses -contains $_ }).Count -eq 0
) {
    $overall = 'FOOTAGE_MISMATCH'; $exitCode = 2
} else {
    $overall = 'FOOTAGE_INDETERMINATE'; $exitCode = 3
}

Write-Output "RESULT=$overall CLIP=$ClipId PARTS=$PartCount"
Write-Output (([ordered]@{
    schema = 'mlvapp.attr3-footage-presence.v1'
    jobId = $JobId
    clipId = $ClipId
    result = $overall
    partCount = $PartCount
    parts = $results
}) | ConvertTo-Json -Compress -Depth 5)
exit $exitCode
'@

    # __EMBEDDED_FUNCTIONS__ is spliced in LAST, after every other placeholder is substituted (the
    # same ordering playback-attr-3-cuda-job.ps1 and attr3-stage-smoke-runner-job.ps1 use), so a
    # placeholder-shaped token that happens to appear inside the verbatim module source can never
    # be rewritten by an earlier .Replace() call.
    $text = $template.
        Replace('__JOB_ID__', $jobId).
        Replace('__CLIP_ID__', $ClipId).
        Replace('__PARTS_JSON__', $partsJson)
    $text = $text.Replace('__EMBEDDED_FUNCTIONS__', $embeddedFunctions)

    if (-not (Test-Path -LiteralPath $OutDir)) { [void](New-Item -ItemType Directory -Path $OutDir -Force) }
    $OutDir = (Resolve-Path -LiteralPath $OutDir).Path
    $jobPath = Join-Path $OutDir "$jobId.job.ps1"
    [IO.File]::WriteAllText($jobPath, $text, [Text.UTF8Encoding]::new($false))

    Write-Output "RESULT=FOOTAGE_PRESENCE_JOB_EMITTED CLIP=$ClipId PARTS=$($partsForJob.Count) SOURCE_SHA256=$sourceSha256 JOB=$jobPath"

    [pscustomobject]@{
        jobFile = $jobPath
        jobId = $jobId
        clipId = $ClipId
        partCount = $partsForJob.Count
        sourceSha256 = $sourceSha256
    }
}

Export-ModuleMember -Function New-Attr3FootagePresenceJob
