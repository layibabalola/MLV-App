# attr3-footage-presence-job.ps1 -- GENERATOR (runs locally; nothing here runs on Bachelor and
# nothing here is submitted to Bachelor -- the hub does both). Emits a job that answers exactly
# one question, safely, on the measurement host: does Bachelor already hold owner-consented clip
# <id>, byte-exact, at the path the frozen spec names for it (ATTR3-FOOTAGE-BIND-1, PR-A).
#
# WHY A SEPARATE GENERATOR, NOT A FOURTH ARTIFACT IN A STAGE JOB. This is a PROBE, not a publish
# job: it copies nothing, renames nothing and touches no cache. It exists so the hub can learn
# "is the corpus already there" BEFORE any playback attempt, without ever putting a real footage
# path in a command, a log, or this generator's own stdout.
#
# WHAT IS BAKED AND WHAT IS CHECKED. This generator calls tools/gates/resolve_consented_clip.py
# as a CHILD PROCESS with --emit-json into a private temp file: the resolver alone proves that
# the frozen spec's parts for this clip id (length, sha256, and the hook's own norm() of the
# path) agree with the hook's frozen consent table. This generator then reads that temp file
# IN-PROCESS, deletes it, and bakes each part's (path, length, sha256) into the emitted job as a
# single JSON literal. The emitted job, on Bachelor, re-checks each part against the LIVE
# filesystem: existence, length, and a streaming sha256 -- never trusting that "the resolver
# approved it" means the bytes are still there.
#
# NO PATH EVER REACHES THIS GENERATOR'S OWN OUTPUT. The resolver's own stdout (captured here only
# to fold into a refusal message) is already path-free by contract (its CLI never prints a part
# path). This generator's own Write-Output lines name the clip id, the part count and the local
# job file path it just wrote -- never a footage path.
#
# NO PUBLISH, NO PLAYBACK. The emitted job opens nothing for playback, deploys nothing, and writes
# nothing to disk except its own stdout: a per-part PASS/MISSING/UNREADABLE/LENGTH_MISMATCH/
# SHA256_MISMATCH line, one RESULT= line, and one path-free JSON summary line.
#
# Usage:
#   pwsh -NoProfile -File tools\profiling\bachelor\attr3-footage-presence-job.ps1 `
#       -ClipId M16-1243 -OutDir <staging-dir>
# then submit the emitted <jobId>.job.ps1 to Bachelor the same way any other job in this route is
# submitted -- it needs no side file at all; the resolved parts travel inside the job.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
    [string]$ClipId,

    [Parameter(Mandatory = $true)]
    [string]$OutDir,

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,

    # TEST SEAM ONLY -- never set by the real CLI path. The hub's normal invocation passes only
    # -ClipId/-OutDir/-RepoRoot, which never reaches this branch, so the resolver child process
    # (and therefore the git ref read and the hook's real consent table) is always exercised
    # outside of tests. When set, this generator skips the resolver entirely and builds the job
    # straight from a synthetic {"clipId","parts":[...]} JSON file the caller supplies -- letting
    # tests exercise the job-emission and part-verification logic against fabricated parts
    # without ever naming or checking real footage.
    [string]$InjectResolvedPartsJsonPathForTests = ''
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1') -Force

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

function Resolve-Attr3PresencePython {
    <#
    .SYNOPSIS
    Prefer `python.exe` only after proving it is Python 3; else fall back to the `py` launcher
    pinned to -3. Mirrors tools/coordination/board-health-sweep.ps1's interpreter probe -- this
    machine still carries a Python 2 `python.exe` on some hosts, so simply assuming `py -3` (or
    assuming `python.exe` is 3.x) is not a valid availability check.
    #>
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        $major = @(& $python.Source -c 'import sys; print(sys.version_info[0])' 2>$null)
        if ($LASTEXITCODE -eq 0 -and $major.Count -eq 1 -and $major[0] -eq '3') {
            return [pscustomobject]@{ Exe = $python.Source; PrefixArgs = @() }
        }
    }
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        return [pscustomobject]@{ Exe = $pyLauncher.Source; PrefixArgs = @('-3') }
    }
    throw 'ATTR3_PRESENCE_NO_PYTHON no Python 3 interpreter is available to run the resolver'
}

if ($InjectResolvedPartsJsonPathForTests) {
    # TEST SEAM -- see the parameter's own comment. Not reachable unless a caller explicitly
    # passes this parameter, which the real CLI path (hub invocation) never does.
    if (-not (Test-Path -LiteralPath $InjectResolvedPartsJsonPathForTests -PathType Leaf)) {
        throw "ATTR3_PRESENCE_TEST_SEAM_MISSING $InjectResolvedPartsJsonPathForTests"
    }
    $emitBytes = [IO.File]::ReadAllBytes($InjectResolvedPartsJsonPathForTests)
} else {
    $ResolverPath = Join-Path $RepoRoot 'tools\gates\resolve_consented_clip.py'
    if (-not (Test-Path -LiteralPath $ResolverPath -PathType Leaf)) {
        throw "ATTR3_PRESENCE_RESOLVER_MISSING resolver not found at $ResolverPath"
    }
    $py = Resolve-Attr3PresencePython

    # The resolver is invoked as a CHILD PROCESS that writes paths only into this private temp
    # file; this generator process reads it IN-PROCESS (never Write-Output-ing its content) and
    # deletes it in a `finally` before returning. A real footage path may flow only through
    # process memory or through a file a child process writes -- never into a tool call or onto
    # this generator's own stdout (hard rules 1-3 of the card this generator implements).
    $emitPath = Join-Path ([IO.Path]::GetTempPath()) ("attr3-footage-presence-resolve-$([guid]::NewGuid().ToString('N')).json")
    try {
        $resolverArgs = @($py.PrefixArgs) + @($ResolverPath, '--clip-id', $ClipId, '--repo-root', $RepoRoot, '--emit-json', $emitPath)
        $summaryLines = @(& $py.Exe @resolverArgs 2>&1)
        $resolverExit = $LASTEXITCODE
        if ($resolverExit -ne 0) {
            # $summaryLines is the resolver's own path-free JSON summary line (its CLI contract
            # never prints a part path), so folding it into this message is safe.
            throw "ATTR3_PRESENCE_RESOLVE_REFUSED clip '$ClipId' was refused by the resolver (exit $resolverExit): $($summaryLines -join ' ')"
        }
        if (-not (Test-Path -LiteralPath $emitPath -PathType Leaf)) {
            throw 'ATTR3_PRESENCE_EMIT_MISSING resolver exited 0 but wrote no --emit-json file'
        }
        $emitBytes = [IO.File]::ReadAllBytes($emitPath)
    } finally {
        if (Test-Path -LiteralPath $emitPath) { Remove-Item -LiteralPath $emitPath -Force }
    }
}

$sha256Alg = [Security.Cryptography.SHA256]::Create()
try {
    $sourceSha256 = [BitConverter]::ToString($sha256Alg.ComputeHash($emitBytes)).Replace('-', '').ToLowerInvariant()
} finally {
    $sha256Alg.Dispose()
}

$resolved = [Text.Encoding]::UTF8.GetString($emitBytes) | ConvertFrom-Json
if ($resolved.clipId -cne $ClipId) {
    throw "ATTR3_PRESENCE_CLIP_ID_MISMATCH resolver emitted clipId '$($resolved.clipId)' for requested '$ClipId'"
}
$parts = @($resolved.parts)
if ($parts.Count -eq 0) {
    throw "ATTR3_PRESENCE_NO_PARTS resolver emitted zero parts for '$ClipId'"
}
# Defense in depth: the resolver already proved these against the frozen table, but a job body
# is about to embed them as a literal, so their shape is re-checked here too. The allowlist
# mirrors playback-attr-3-cuda-job.ps1's -ClipPath check.
foreach ($part in $parts) {
    if ($part.path -notmatch '^[A-Za-z]:\\[A-Za-z0-9 _.\\-]+$') {
        throw "ATTR3_PRESENCE_PART_PATH_INVALID part $($part.index) carries characters outside the allowlist"
    }
    if ($part.sha256 -notmatch '^[0-9a-f]{64}$') {
        throw "ATTR3_PRESENCE_PART_SHA_INVALID part $($part.index) sha256 is not 64 lowercase hex"
    }
    if ($part.length -isnot [long] -and $part.length -isnot [int]) {
        throw "ATTR3_PRESENCE_PART_LENGTH_INVALID part $($part.index) length is not an integer"
    }
}

# Re-serialised, compact and key-ordered, so the embedded literal is deterministic and never
# carries the emit file's own incidental whitespace or key order.
$partsForJob = @($parts | Sort-Object { [int]$_.index } | ForEach-Object {
    [ordered]@{ index = [int]$_.index; path = [string]$_.path; length = [int64]$_.length; sha256 = [string]$_.sha256 }
})
$partsJson = $partsForJob | ConvertTo-Json -Compress -Depth 5
# ConvertTo-Json -Compress on a single-element array still yields a bare object, never `[...]`,
# unless coerced -- the emitted job always expects a JSON array.
if ($partsForJob.Count -eq 1) { $partsJson = "[$partsJson]" }

$jobId = "attr3-footage-presence-$ClipId-$($sourceSha256.Substring(0, 12))"
[void](Assert-AttrCudaSafeArtifactName -Name "$jobId.job.ps1")

# --- job body template (placeholders are substituted below; the body itself never touches this
#     generator's variables directly, so there is no accidental capture of this machine's
#     environment into the emitted script) -------------------------------------------------------
$template = @'
$ErrorActionPreference = 'Stop'
$JobId = '__JOB_ID__'
$ClipId = '__CLIP_ID__'
$PartsJson = '__PARTS_JSON__'

function Say([string]$Message) { Write-Output "[$JobId] $Message" }

$Parts = @($PartsJson | ConvertFrom-Json)
$PartCount = $Parts.Count

Say "START clip=$ClipId parts=$PartCount"

$results = New-Object System.Collections.Generic.List[object]
foreach ($part in $Parts) {
    $status = $null
    $actualLength = $null
    if (-not (Test-Path -LiteralPath $part.path -PathType Leaf)) {
        $status = 'MISSING'
    } else {
        try {
            $actualLength = (Get-Item -LiteralPath $part.path -Force).Length
        } catch {
            $status = 'UNREADABLE'
        }
    }
    if (-not $status) {
        if ($actualLength -ne [int64]$part.length) {
            $status = 'LENGTH_MISMATCH'
        } else {
            try {
                $actualSha256 = (Get-FileHash -LiteralPath $part.path -Algorithm SHA256).Hash.ToLowerInvariant()
            } catch {
                $status = 'UNREADABLE'
            }
            if (-not $status) {
                $status = if ($actualSha256 -eq $part.sha256) { 'PASS' } else { 'SHA256_MISMATCH' }
            }
        }
    }
    Write-Output "PART=$($part.index) STATUS=$status"
    $results.Add([ordered]@{
        index = $part.index
        status = $status
        length = $part.length
        sha256_12 = $part.sha256.Substring(0, 12)
    })
}

$statuses = @($results | ForEach-Object { $_.status })
if (($statuses | Where-Object { $_ -ne 'PASS' }).Count -eq 0) {
    $overall = 'FOOTAGE_PRESENT'; $exitCode = 0
} elseif (($statuses | Where-Object { $_ -ne 'MISSING' }).Count -eq 0) {
    $overall = 'FOOTAGE_ABSENT'; $exitCode = 1
} else {
    $overall = 'FOOTAGE_MISMATCH'; $exitCode = 2
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

$text = $template.
    Replace('__JOB_ID__', $jobId).
    Replace('__CLIP_ID__', $ClipId).
    Replace('__PARTS_JSON__', $partsJson)

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
