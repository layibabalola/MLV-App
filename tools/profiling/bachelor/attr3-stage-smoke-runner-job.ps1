# attr3-stage-smoke-runner-job.ps1 -- GENERATOR (runs locally; nothing here runs on Bachelor).
# Emits a job that publishes ONE tracked repository file -- run-release-gui-smoke.ps1, the smoke
# runner tools/profiling/bachelor/playback-attr-3-cuda-job.ps1 launches -- into the Bachelor
# agent cache under its fixed cache name, so that job's hash pin (ATTRCUDA_SMOKE_RUNNER_STALE)
# has verified bytes to match against (ATTR3-SMOKE-RUNNER-PIN-1).
#
# THE DEFECT THIS CLOSES. No tracked stage or assemble job ever copied the runner into the
# Bachelor cache; the attribution job only ever checked that SOME file sat at that cache path.
# Bachelor ran a copy of run-release-gui-smoke.ps1 from before commit f401bf9a, which used the
# pre-f401bf9a log layout and wrote no evidence.runLogSnapshot -- correctly refused downstream by
# Resolve-AttrCudaSmokeRunLog, but only after a scarce quiet venue window had already been spent.
#
# WHY A SEPARATE GENERATOR, NOT A FOURTH ARTIFACT IN playback-attr-3-cuda-stage-job.ps1. That job
# derives EXACTLY three artifact names from -SourceCommit and binds them to one build.json
# published LAST, transactionally, as a set -- "manifest present" is read downstream as "all
# three are complete and verified together". The smoke runner is not part of that build (it is
# not compiled; it is a tracked repository script) and does not belong in that atomic set. This
# generator follows attr3-stage-fixture-job.ps1's pattern instead: one file, addressed by its
# exact baked name, published through the same slot-checked helpers.
#
# WHAT IS BAKED AND WHAT IS CHECKED. The generator resolves the git blob for
# tools/profiling/run-release-gui-smoke.ps1 AT -SourceCommit -- never the working tree, which may
# be dirty or carry a different line-ending checkout -- streams its exact committed bytes to
# -OutDir byte-for-byte (Save-AttrCudaCommittedBlobBytes), and bakes their sha256. The emitted job
# re-hashes the arriving inbox copy against that value before publishing, exactly like
# attr3-stage-fixture-job.ps1: copy to <name>.partial, verify, non-overwriting rename, then remove
# the inbox copy.
#
# CACHE NAME IS FIXED, NOT PER-COMMIT. playback-attr-3-cuda-job.ps1 looks for the runner at the
# constant cache path 'run-release-gui-smoke.ps1' (unversioned, the same convention PresentMon's
# cache name already uses) -- every stage publishes to that one name, and the baked sha256 pin,
# not the name, is what proves which commit's bytes are actually there.
#
# `<jobId>.job.ps1 -VerifyOnly` runs the read-only prefix (containment, name safety, side-file
# presence, hash) and exits without creating, copying, publishing or deleting anything.
#
# NO FOOTAGE. This job moves one tracked repository script. It never opens, names, globs or
# resolves a media file, and it never enumerates the cache.
#
# Usage:
#   pwsh -NoProfile -File tools\profiling\bachelor\attr3-stage-smoke-runner-job.ps1 `
#       -SourceCommit <40-hex> -OutDir <staging-dir>
# then submit with tools\profiling\um-run.ps1 -SideFile <OutDir>\run-release-gui-smoke.ps1
#   -JobId <jobId> -AgentShare \\bachelor\mlv-agent

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$SourceCommit,

    [Parameter(Mandatory = $true)]
    [string]$OutDir,

    # `~` is admitted because Windows temp roots carry 8.3 short names (RUNNER~1, OBABAL~1) and
    # the behavioural tests point -AgentRoot at one; it is inert everywhere this value is used.
    [ValidatePattern('^[A-Za-z]:\\[A-Za-z0-9 _.~\\-]+$')]
    [string]$AgentRoot = 'C:\mlvtmp\mlv-agent',

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,

    [string]$RunnerRelativePath = 'tools/profiling/run-release-gui-smoke.ps1'
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1') -Force

$RunnerName = 'run-release-gui-smoke.ps1'
[void](Assert-AttrCudaSafeArtifactName -Name $RunnerName)
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

if (-not (Test-Path -LiteralPath $OutDir)) { [void](New-Item -ItemType Directory -Path $OutDir -Force) }
$OutDir = (Resolve-Path -LiteralPath $OutDir).Path
$sideFile = Join-Path $OutDir $RunnerName

# Committed bytes, never the working tree (ATTR3-SMOKE-RUNNER-PIN-1): the same blob id
# playback-attr-3-cuda-job.ps1 resolves independently from the same -SourceCommit, so the two
# generators agree without either one taking the other's word for it.
$blobId = Resolve-AttrCudaCommittedBlobId -RepoRoot $RepoRoot -Commit $SourceCommit -RepoRelativePath $RunnerRelativePath
$runnerSha256 = Save-AttrCudaCommittedBlobBytes -RepoRoot $RepoRoot -BlobId $blobId -Destination $sideFile

$jobId = "attr3-stage-smoke-runner-$($SourceCommit.Substring(0,12))-$($runnerSha256.Substring(0,12))"

# --- job body template (placeholders are substituted below) ------------------------------------
$template = @'
# -VerifyOnly runs the read-only prefix (containment, name safety, side-file presence and hash)
# and exits WITHOUT copying, publishing or deleting anything. The agent never passes it; the
# behavioural tests do.
param([switch]$VerifyOnly)

$ErrorActionPreference = 'Stop'
$JobId = '__JOB_ID__'
$AgentRoot = '__AGENT_ROOT__'
$RunnerName = '__RUNNER_NAME__'
$RunnerSha256 = '__RUNNER_SHA256__'
$Cache = Join-Path $AgentRoot 'cache'
$Inbox = Join-Path $AgentRoot 'inbox'
$Work = Join-Path $AgentRoot "work\$JobId"
$Pub = Join-Path $AgentRoot "outbox\$JobId.artifacts"

function Say([string]$Message) { Write-Output "[$JobId] $Message" }
function Get-ShaLower([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

# --- verifiers, embedded VERBATIM from tools/profiling/bachelor/AttrCudaArtifacts.psm1 --------
__EMBEDDED_FUNCTIONS__
# --- end embedded verifiers -------------------------------------------------------------------

$StepLog = [System.Collections.Specialized.OrderedDictionary]::new()
# Set only once this run owns a freshly created outbox. Until then a failure must not write into
# a previous run's artifacts directory -- and -VerifyOnly must not write at all.
$PubReady = $false

function Complete-Failed([int]$Code, [string]$Step, [string]$Message) {
    $StepLog[$Step] = $Code
    Say "FAIL step=$Step exit=$Code $Message"
    if ($PubReady -and (Test-Path -LiteralPath $Pub)) {
        $partial = [ordered]@{
            schema = 'mlvapp.attr3-stage-smoke-runner.v1'
            jobId = $JobId
            exitCode = $Code
            failedStep = $Step
            message = $Message
            stagedOnHost = $env:COMPUTERNAME
            steps = $StepLog
        }
        try {
            [void](Publish-AttrCudaText -Path (Join-Path $Pub 'result.json') -Value ($partial | ConvertTo-Json -Depth 10))
        } catch {
            Say "RESULT_JSON_NOT_WRITTEN $($_.Exception.Message)"
        }
    }
    Write-Output "RESULT=SMOKE_RUNNER_STAGE_FAILED STEP=$Step EXIT=$Code"
    exit $Code
}

# ATTR3-SMOKE-RUNNER-PIN-1: one exit for "the cache already holds these exact bytes", whether
# that was found by the pre-flight check or by losing the publish race to a concurrent
# identical-bytes publisher -- mirrors attr3-stage-fixture-job.ps1's Complete-AlreadyStaged.
function Complete-AlreadyStaged([string]$CachePathValue) {
    $inboxRemoved = Remove-AttrCudaPartialFile -TrustedRoot $AgentRoot -Path $side
    if (-not $inboxRemoved) {
        $StepLog['inboxCleanup'] = 1
        Complete-Failed 22 'inboxCleanup' "could not remove staged inbox copy: $RunnerName"
    }
    $StepLog['inboxCleanup'] = 0
    $StepLog['publish'] = 0
    [void](Publish-AttrCudaText -Path (Join-Path $Pub 'result.json') -Value (([ordered]@{
        schema = 'mlvapp.attr3-stage-smoke-runner.v1'; jobId = $JobId
        exitCode = 0; alreadyStaged = $true
        runner = [ordered]@{ name = $RunnerName; sha256 = $RunnerSha256; cachePath = $CachePathValue }
        stagedOnHost = $env:COMPUTERNAME; steps = $StepLog
    }) | ConvertTo-Json -Depth 10))
    Write-Output "RESULT=SMOKE_RUNNER_STAGE_OK ALREADY=1 RUNNER=$RunnerName SHA256=$RunnerSha256 CACHE=$CachePathValue"
    exit 0
}

function Assert-UnderAgentRoot([string]$Path, [string]$Label) {
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($AgentRoot)
    if ($full -ne $root -and -not $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "job-owned path '$Label' resolves outside the agent root: $full"
    }
}
foreach ($check in @(
    @{ path = $Cache; label = 'Cache' },
    @{ path = $Inbox; label = 'Inbox' },
    @{ path = $Work; label = 'Work' },
    @{ path = $Pub; label = 'Pub' }
)) { Assert-UnderAgentRoot $check.path $check.label }

Say "START runner name=$RunnerName sha256=$RunnerSha256"

# --- name and path safety, before anything is read, copied, renamed or removed ----------------
try {
    [void](Assert-AttrCudaSafeArtifactName -Name $RunnerName)
    $side = Assert-AttrCudaDirectChild -Root $Inbox -Path (Join-Path $Inbox $RunnerName) -Label "inbox/$RunnerName"
    $cachePath = Assert-AttrCudaDirectChild -Root $Cache -Path (Join-Path $Cache $RunnerName) -Label "cache/$RunnerName"
    $partialPath = Assert-AttrCudaDirectChild -Root $Cache -Path (Join-Path $Cache "$RunnerName.partial") -Label "cache/$RunnerName.partial"
} catch {
    Complete-Failed 6 'artifactNameSafety' $_.Exception.Message
}
$StepLog['artifactNameSafety'] = 0

if (-not (Test-Path -LiteralPath $side -PathType Leaf)) {
    Complete-Failed 3 'inboxSideFile' "expected side-file missing from the inbox: $RunnerName"
}
$StepLog['inboxSideFile'] = 0

$arrivedSha = Get-ShaLower $side
if ($arrivedSha -ne $RunnerSha256) {
    Complete-Failed 4 'runnerHash' "sha256 mismatch for ${RunnerName}: baked $RunnerSha256, arrived $arrivedSha"
}
$StepLog['runnerHash'] = 0

# Everything above is read-only. -VerifyOnly stops here, having touched nothing.
if ($VerifyOnly) {
    Write-Output "RESULT=VERIFY_ONLY_OK RUNNER=$RunnerName SHA256=$RunnerSha256"
    exit 0
}

Remove-AttrCudaTree -TrustedRoot $AgentRoot -Path $Work
# Each container is created explicitly, parent first (never as a side effect of -Force on a
# deeper path), so every one of them is link-checked like the directories this job publishes into.
[void](New-AttrCudaDirectory -Path (Join-Path $AgentRoot 'work'))
[void](New-AttrCudaDirectory -Path $Work)
[void](New-AttrCudaDirectory -Path (Join-Path $AgentRoot 'outbox'))
[void](New-AttrCudaDirectory -Path $Pub)
[void](New-AttrCudaDirectory -Path $Cache)
$PubReady = $true

$Scratch = Join-Path $Work '.job-tmp'
[void](New-AttrCudaDirectory -Path $Scratch)
$env:TEMP = $Scratch
$env:TMP = $Scratch

# Already staged with identical bytes: nothing to do, and re-staging must not churn the cache.
if (Test-Path -LiteralPath $cachePath -PathType Leaf) {
    if ((Get-ShaLower $cachePath) -eq $RunnerSha256) {
        Complete-AlreadyStaged -CachePathValue $cachePath
    }
    Complete-Failed 21 'publishRename' "cache already holds $RunnerName with DIFFERENT bytes"
}

try {
    [void](Publish-AttrCudaFileCopy -Source $side -Destination $partialPath)
    if ((Get-ShaLower $partialPath) -ne $RunnerSha256) { throw "sha256 did not round-trip into the cache for $RunnerName" }
} catch {
    [void](Remove-AttrCudaPartialFile -TrustedRoot $AgentRoot -Path $partialPath)
    Complete-Failed 20 'publishPartial' $_.Exception.Message
}
$StepLog['publishPartial'] = 0

try {
    [void](Publish-AttrCudaFileMoveNonOverwriting -Source $partialPath -Destination $cachePath)
} catch {
    # A plain Move-Item -Force here would delete and replace whatever a concurrent publisher had
    # just placed at $cachePath. The non-overwriting move above never touches an occupied
    # destination -- it either renamed cleanly or the destination is exactly as some other writer
    # left it. Re-hash it to tell "a concurrent publisher already finished this exact runner"
    # (this run is simply done) from "something else is there" (fail closed at the same code the
    # pre-flight check above uses for that).
    [void](Remove-AttrCudaPartialFile -TrustedRoot $AgentRoot -Path $partialPath)
    if ((Test-Path -LiteralPath $cachePath -PathType Leaf) -and (Get-ShaLower $cachePath) -eq $RunnerSha256) {
        Complete-AlreadyStaged -CachePathValue $cachePath
    }
    Complete-Failed 21 'publishRename' $_.Exception.Message
}
$StepLog['publishRename'] = 0

# The inbox copy is removed only after the cache publish is complete, so an interrupted run
# leaves the inbox intact and is simply re-runnable.
$inboxRemoved = Remove-AttrCudaPartialFile -TrustedRoot $AgentRoot -Path $side
if (-not $inboxRemoved) {
    $StepLog['inboxCleanup'] = 1
    Complete-Failed 22 'inboxCleanup' "could not remove staged inbox copy: $RunnerName"
}
$StepLog['inboxCleanup'] = 0

try { [void](Publish-AttrCudaText -Path (Join-Path $Pub 'result.json') -Value (([ordered]@{
    schema = 'mlvapp.attr3-stage-smoke-runner.v1'; jobId = $JobId
    exitCode = 0; alreadyStaged = $false
    runner = [ordered]@{ name = $RunnerName; sha256 = $RunnerSha256; cachePath = $cachePath }
    stagedOnHost = $env:COMPUTERNAME; steps = $StepLog
}) | ConvertTo-Json -Depth 10)) } catch { Complete-Failed 23 'publishResult' $_.Exception.Message }
Write-Output "RESULT=SMOKE_RUNNER_STAGE_OK RUNNER=$RunnerName SHA256=$RunnerSha256 CACHE=$cachePath"
exit 0
'@

$embeddedFunctions = Get-AttrCudaEmbeddedFunctionSource -Name @(
    'Assert-AttrCudaSafeArtifactName',
    'Assert-AttrCudaDirectChild',
    'Assert-AttrCudaNoLinkBelowRoot',
    'Assert-AttrCudaWritableFileSlot',
    'Assert-AttrCudaNonOverwritingFileSlot',
    'Publish-AttrCudaText',
    'Publish-AttrCudaFileCopy',
    'Publish-AttrCudaFileMoveNonOverwriting',
    'New-AttrCudaDirectory',
    'Remove-AttrCudaPartialFile',
    'Remove-AttrCudaTree'
)

$jobPath = Join-Path $OutDir "$jobId.job.ps1"
$text = $template.
    Replace('__EMBEDDED_FUNCTIONS__', $embeddedFunctions).
    Replace('__JOB_ID__', $jobId).
    Replace('__AGENT_ROOT__', $AgentRoot).
    Replace('__RUNNER_NAME__', $RunnerName).
    Replace('__RUNNER_SHA256__', $runnerSha256)
[IO.File]::WriteAllText($jobPath, $text, [Text.UTF8Encoding]::new($false))

# So the hub can pass the baked hash straight to playback-attr-3-cuda-job.ps1's verification
# without re-deriving it, and so a reader can confirm the two generators agree.
Write-Output "RESULT=SMOKE_RUNNER_STAGE_JOB_EMITTED SOURCE=$SourceCommit RUNNER_SHA256=$runnerSha256 JOB=$jobPath"

[pscustomobject]@{
    jobFile = $jobPath
    jobId = $jobId
    sourceCommit = $SourceCommit
    runnerName = $RunnerName
    runnerSha256 = $runnerSha256
    sideFile = $sideFile
    agentRoot = $AgentRoot
    cachePath = (Join-Path (Join-Path $AgentRoot 'cache') $RunnerName)
}
