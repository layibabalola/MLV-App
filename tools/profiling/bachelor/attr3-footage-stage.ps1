# attr3-footage-stage.ps1 -- the ONE tracked, id-only entry point that stages an owner-consented
# clip onto Bachelor, fully automated: resolve -> verify the source on THIS host -> transfer to
# the agent share -> emit and submit a Bachelor job that places the bytes at the spec path.
# ATTR3-FOOTAGE-STAGE-1.
#
# WHAT RUNS WHERE. Steps 1-3 below run ON THIS HOST (Virtual-Ten): resolving the clip, verifying
# every source part's bytes, and copying them to the agent share under neutral, index-derived
# names (tools/profiling/bachelor/AttrCudaOwnerFootage.psm1's Send-AttrCudaOwnerFootagePartTo-
# Staging). Step 4 builds a job (tools/profiling/bachelor/Attr3FootageStageJob.psm1) and submits
# it through tools/profiling/um-run.ps1, which is the ONLY tracked writer of the agent's inbox --
# step 4's own job then runs on Bachelor, with no checkout and no module path of its own, and does
# the actual placement at the spec path. This script never places a file at that path itself.
#
# THE RESOLVER CHILD PROCESS IS THE ONLY WAY THIS SCRIPT OBTAINS PARTS. There is no parameter
# that accepts a caller-typed path for any part, mirroring attr3-footage-presence-job.ps1's own
# contract: tools/gates/resolve_consented_clip.py alone proves the frozen spec agrees with the
# hook's frozen consent table for -ClipId, and only its --emit-json output -- read by this
# process, never printed -- ever names a part's real path.
#
# NO PATH EVER REACHES THIS SCRIPT'S OWN OUTPUT. Every Write-Output line below names the clip id,
# a part index, a part's staging/verification STATUS TOKEN, or a job id -- never a footage path,
# source or target. The resolver's own stdout (captured only to fold into a refusal message) is
# already path-free by contract (its CLI never prints a part path); Send-AttrCudaOwnerFootagePart-
# ToStaging's and um-run.ps1's own thrown/returned text carry the same guarantee.
#
# Usage:
#   pwsh -NoProfile -File tools\profiling\bachelor\attr3-footage-stage.ps1 -ClipId M16-1243
# Exits 0 with RESULT=FOOTAGE_STAGED when Bachelor now holds every part at its spec path; exits
# non-zero with RESULT=FOOTAGE_STAGE_REFUSED (or an ATTR3_FOOTAGE_STAGE_* refusal token) otherwise.
# Idempotent: a clip already fully present on Bachelor at its spec path transfers nothing new and
# still reports FOOTAGE_STAGED (every part lands ALREADY_PRESENT).

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
    [string]$ClipId,

    [string]$AgentShare = '\\bachelor\mlv-agent',

    # The LOCAL path the agent share above resolves to ON BACHELOR ITSELF -- baked into the
    # emitted job (which runs there, with no UNC path back to its own share), never used by this
    # script to touch the filesystem directly. Independent of -AgentShare on purpose: a test
    # points -AgentShare at a fake local directory standing in for the share while leaving this
    # at a value the job template never actually dereferences in that test (the job is run
    # directly with -File, not through the real Bachelor agent).
    # `~` is admitted because Windows temp roots carry 8.3 short names (RUNNER~1, OBABAL~1) and
    # the behavioural tests point -AgentRootOnHost at one; it is inert everywhere this value is used.
    [ValidatePattern('^[A-Za-z]:\\[A-Za-z0-9 _.~\\-]+$')]
    [string]$AgentRootOnHost = 'C:\mlvtmp\mlv-agent',

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,

    [int]$TimeoutSec = 1800
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'AttrCudaOwnerFootage.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'Attr3FootageStageJob.psm1') -Force

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

function Resolve-Attr3StagePython {
    <#
    .SYNOPSIS
    Prefer `python.exe` only after proving it is Python 3; else fall back to the `py` launcher
    pinned to -3. Mirrors attr3-footage-presence-job.ps1's own copy of this probe (and
    tools/coordination/board-health-sweep.ps1's) -- this machine still carries a Python 2
    `python.exe` on some hosts, so simply assuming `py -3` (or that `python.exe` is 3.x) is not a
    valid availability check.
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
    throw 'ATTR3_FOOTAGE_STAGE_NO_PYTHON no Python 3 interpreter is available to run the resolver'
}

$ResolverPath = Join-Path $RepoRoot 'tools\gates\resolve_consented_clip.py'
if (-not (Test-Path -LiteralPath $ResolverPath -PathType Leaf)) {
    throw "ATTR3_FOOTAGE_STAGE_RESOLVER_MISSING resolver not found at $ResolverPath"
}
$py = Resolve-Attr3StagePython

# --- 1. RESOLVE: the resolver child process is the only way this script obtains parts ----------
# Same pattern as attr3-footage-presence-job.ps1: paths flow only through a private temp file this
# process reads IN-PROCESS and deletes in a `finally`, never through a tool call or this script's
# own stdout.
$emitPath = Join-Path ([IO.Path]::GetTempPath()) ("attr3-footage-stage-resolve-$([guid]::NewGuid().ToString('N')).json")
try {
    $resolverArgs = @($py.PrefixArgs) + @($ResolverPath, '--clip-id', $ClipId, '--repo-root', $RepoRoot, '--emit-json', $emitPath)
    $summaryLines = @(& $py.Exe @resolverArgs 2>&1)
    $resolverExit = $LASTEXITCODE
    if ($resolverExit -ne 0) {
        # $summaryLines is the resolver's own path-free JSON summary line (its CLI contract never
        # prints a part path), so folding it into this message is safe.
        throw "ATTR3_FOOTAGE_STAGE_RESOLVE_REFUSED clip '$ClipId' was refused by the resolver (exit $resolverExit): $($summaryLines -join ' ')"
    }
    if (-not (Test-Path -LiteralPath $emitPath -PathType Leaf)) {
        throw 'ATTR3_FOOTAGE_STAGE_EMIT_MISSING resolver exited 0 but wrote no --emit-json file'
    }
    $emitBytes = [IO.File]::ReadAllBytes($emitPath)
} finally {
    if (Test-Path -LiteralPath $emitPath) { Remove-Item -LiteralPath $emitPath -Force }
}

$resolved = [Text.Encoding]::UTF8.GetString($emitBytes) | ConvertFrom-Json
if ($resolved.clipId -cne $ClipId) {
    throw "ATTR3_FOOTAGE_STAGE_CLIP_ID_MISMATCH resolver emitted clipId '$($resolved.clipId)' for requested '$ClipId'"
}
$parts = @($resolved.parts | Sort-Object { [int]$_.index })
Write-Output "RESOLVED clip=$ClipId parts=$($parts.Count)"

# --- 2. VERIFY every source part on THIS host, before anything is sent anywhere ----------------
foreach ($part in $parts) {
    $status = Test-AttrCudaFootagePart -Path $part.path -ExpectedLength ([int64]$part.length) -ExpectedSha256 ([string]$part.sha256)
    Write-Output "SOURCE PART=$($part.index) STATUS=$status"
    if ($status -ne 'PASS') {
        throw "ATTR3_FOOTAGE_STAGE_SOURCE_VERIFY_FAILED part $($part.index) failed source verification ($status)"
    }
}

# --- 3. BUILD the job first: its content-derived jobId names the SAME per-job staging directory
#        this script transfers into next, so both sides agree on it without exchanging state. ---
$stageOutDir = Join-Path ([IO.Path]::GetTempPath()) ("attr3-footage-stage-job-$([guid]::NewGuid().ToString('N'))")
$job = New-Attr3FootageStageJob -ClipId $ClipId -Parts $parts -OutDir $stageOutDir -AgentRoot $AgentRootOnHost
$shareStageDir = Join-Path $AgentShare ("footage-stage\" + $job.jobId)

# --- 4. TRANSFER every verified source part to the agent share, under a neutral, index-derived
#        name, into that dedicated staging directory. -------------------------------------------
foreach ($part in $parts) {
    try {
        [void](Send-AttrCudaOwnerFootagePartToStaging `
            -SourcePath $part.path `
            -StagingDirectory $shareStageDir `
            -Index ([int]$part.index) `
            -ExpectedLength ([int64]$part.length) `
            -ExpectedSha256 ([string]$part.sha256))
    } catch {
        # The underlying exception text is already path-free by contract (OWNER_FOOTAGE_STAGE_*
        # tokens name only an index) -- folding it in here carries no path.
        throw "ATTR3_FOOTAGE_STAGE_TRANSFER_FAILED part $($part.index): $($_.Exception.Message)"
    }
    Write-Output "TRANSFER PART=$($part.index) STATUS=STAGED"
}

# --- 5. SUBMIT the pre-built job through um-run.ps1, the only tracked writer of the agent inbox.
$umRun = Join-Path $RepoRoot 'tools\profiling\um-run.ps1'
$result = & $umRun -ScriptPath $job.jobFile -JobId $job.jobId -AgentShare $AgentShare -TimeoutSec $TimeoutSec

if ($result.stdout) { Write-Output $result.stdout }
if ($result.stderr) { Write-Output $result.stderr }

if ($result.exitCode -eq 0) {
    Write-Output "RESULT=FOOTAGE_STAGED CLIP=$ClipId PARTS=$($parts.Count) JOB=$($job.jobId)"
    exit 0
}
Write-Output "RESULT=FOOTAGE_STAGE_REFUSED CLIP=$ClipId PARTS=$($parts.Count) JOB=$($job.jobId) EXIT=$($result.exitCode)"
exit 1
