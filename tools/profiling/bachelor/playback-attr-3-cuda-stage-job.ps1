# playback-attr-3-cuda-stage-job.ps1 -- GENERATOR (runs locally / in a lane; nothing here runs
# on Bachelor). Emits a self-contained <jobId>.job.ps1 that takes the package
# tools/profiling/bachelor/playback-attr-3-cuda-assemble.ps1 built on the board host -- dropped
# into the Bachelor agent inbox as plain side-files -- and publishes it into the agent cache in
# the layout tools/profiling/bachelor/playback-attr-3-cuda-job.ps1 (the attribution job)
# verifies.
#
# WHY A JOB AND NOT A COPY. NA-7: direct hooked writes to \\bachelor\... are refused; the
# tracked submitter tools/profiling/um-run.ps1 is the route, and the agent only ever EXECUTES
# inbox\*.job.ps1 while leaving every other inbox file alone. So the hub drops the artifacts
# beside the job and this job does the publishing, on the host, with the hashes re-checked
# there. Nothing in this file hides a path in a variable to route around that boundary.
#
# WHAT IT PROVES. Every expected file name and lowercase sha256 is BAKED IN at generation time,
# read from the assembler's build.json on this machine. On the host the job re-hashes each
# side-file and refuses on the first mismatch, then cross-checks the arriving build.json against
# the same baked values -- so neither a stale side-file nor an edited manifest can publish a
# package that does not match what was assembled.
#
# ORDER. Artifacts are copied to <name>.partial in the cache, hash-verified there, renamed, and
# only then is build.json published -- LAST, exactly as the attribution job assumes: its mere
# presence means the exe, the DLL and the package zip beside it are complete and verified.
#
# NO FOOTAGE. This job moves build artifacts. It never opens, names, globs or resolves a media
# file, and it never enumerates the cache.
#
# Usage:
#   pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-stage-job.ps1 `
#       -SourceCommit <40-hex> -BuildDir <assembler -OutDir> -OutDir <staging-dir>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$SourceCommit,

    # The assembler's -OutDir: holds playback-attr-3-cuda-<sha12>-build.json and the three
    # artifacts it names.
    [Parameter(Mandatory = $true)]
    [string]$BuildDir,

    [Parameter(Mandatory = $true)]
    [string]$OutDir,

    [ValidatePattern('^[A-Za-z]:\\[A-Za-z0-9 _.\\-]+$')]
    [string]$AgentRoot = 'C:\mlvtmp\mlv-agent'
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1') -Force

$names = Get-AttrCudaArtifactNames -SourceCommit $SourceCommit
$JobId = "playback-attr-3-cuda-stage-$($names.shortSha)"

if (-not (Test-Path -LiteralPath $BuildDir -PathType Container)) { throw "BuildDir is not a directory: $BuildDir" }
$BuildDir = (Resolve-Path -LiteralPath $BuildDir).Path
$buildManifestPath = Join-Path $BuildDir $names.buildManifestName
if (-not (Test-Path -LiteralPath $buildManifestPath)) {
    throw "build manifest not found: $buildManifestPath (the assembler writes it LAST; its absence means that build did not complete)"
}
$buildManifest = Get-Content -Raw -LiteralPath $buildManifestPath | ConvertFrom-Json
if ($buildManifest.sourceCommit -ne $SourceCommit) {
    throw "build manifest sourceCommit=$($buildManifest.sourceCommit) does not match -SourceCommit $SourceCommit"
}
if ($buildManifest.pendingSymbolPresence -isnot [bool]) {
    throw "build manifest has no boolean pendingSymbolPresence; the attribution job would refuse it on the host"
}

function Get-ShaLower([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

# Bake the exact artifact set from the manifest, and verify each one HERE first: a side-file
# that does not match on this machine can never match on the host, and failing now costs a
# generator run instead of an agent round trip.
$expected = @(
    [ordered]@{ name = $buildManifest.packageZip.name; sha256 = ([string]$buildManifest.packageZip.sha256).ToLowerInvariant() },
    [ordered]@{ name = $buildManifest.exe.name; sha256 = ([string]$buildManifest.exe.sha256).ToLowerInvariant() },
    [ordered]@{ name = $buildManifest.dll.name; sha256 = ([string]$buildManifest.dll.sha256).ToLowerInvariant() }
)
foreach ($item in $expected) {
    if ([string]::IsNullOrWhiteSpace([string]$item.name)) { throw 'build manifest names an artifact with no file name' }
    if ([string]$item.sha256 -notmatch '^[0-9a-f]{64}$') { throw "build manifest sha256 for $($item.name) is not a lowercase sha256" }
    $localPath = Join-Path $BuildDir ([string]$item.name)
    if (-not (Test-Path -LiteralPath $localPath)) { throw "artifact named by the build manifest is missing from BuildDir: $localPath" }
    $actual = Get-ShaLower $localPath
    if ($actual -ne [string]$item.sha256) { throw "sha256 mismatch in BuildDir for $($item.name): manifest $($item.sha256), on disk $actual" }
}
$manifestSha256 = Get-ShaLower $buildManifestPath

if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$OutDir = (Resolve-Path -LiteralPath $OutDir).Path
$jobPath = Join-Path $OutDir "$JobId.job.ps1"

# --- job body template (placeholders are substituted below) ------------------------------------
$template = @'
$ErrorActionPreference = 'Stop'
$SourceCommit = '__SOURCE_COMMIT__'
$JobId = '__JOB_ID__'
$AgentRoot = '__AGENT_ROOT__'
$ManifestName = '__MANIFEST_NAME__'
$ManifestSha256 = '__MANIFEST_SHA256__'
$Expected = __EXPECTED_LITERAL__
$Cache = Join-Path $AgentRoot 'cache'
$Inbox = Join-Path $AgentRoot 'inbox'
$Work = Join-Path $AgentRoot "work\$JobId"
$Pub = Join-Path $AgentRoot "outbox\$JobId.artifacts"

function Say([string]$Message) { Write-Output "[$JobId] $Message" }
function Get-ShaLower([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

$StepLog = [System.Collections.Specialized.OrderedDictionary]::new()

function Complete-Failed([int]$Code, [string]$Step, [string]$Message) {
    $StepLog[$Step] = $Code
    Say "FAIL step=$Step exit=$Code $Message"
    if (Test-Path -LiteralPath $Pub) {
        $partial = [ordered]@{
            schema = 'mlvapp.playback-attr-3-cuda-stage.v1'
            sourceCommit = $SourceCommit
            jobId = $JobId
            exitCode = $Code
            failedStep = $Step
            message = $Message
            stagedOnHost = $env:COMPUTERNAME
            steps = $StepLog
        }
        $partial | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Pub 'result.json') -Encoding UTF8
    }
    Write-Output "RESULT=STAGE_FAILED STEP=$Step EXIT=$Code"
    exit $Code
}

# Machine-safety boundary: every path this job touches lives under the agent root.
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

if (Test-Path -LiteralPath $Work) { Remove-Item -LiteralPath $Work -Recurse -Force }
if (Test-Path -LiteralPath $Pub) { Remove-Item -LiteralPath $Pub -Recurse -Force }
New-Item -ItemType Directory -Path $Work -Force | Out-Null
New-Item -ItemType Directory -Path $Pub -Force | Out-Null
New-Item -ItemType Directory -Path $Cache -Force | Out-Null

# Job-owned TEMP before anything else runs.
$Scratch = Join-Path $Work '.job-tmp'
New-Item -ItemType Directory -Path $Scratch -Force | Out-Null
$env:TEMP = $Scratch
$env:TMP = $Scratch

Say "START source=$SourceCommit files=$($Expected.Count)"

# Each side-file is addressed by its EXACT baked name -- no directory enumeration, no pattern.
$sideFiles = @()
foreach ($item in $Expected) {
    $name = [string]$item.name
    $path = Join-Path $Inbox $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Complete-Failed 3 'inboxSideFiles' "expected side-file missing from the inbox: $name"
    }
    $sideFiles += [ordered]@{ name = $name; path = $path; sha256 = [string]$item.sha256 }
}
$manifestSide = Join-Path $Inbox $ManifestName
if (-not (Test-Path -LiteralPath $manifestSide -PathType Leaf)) {
    Complete-Failed 3 'inboxSideFiles' "expected side-file missing from the inbox: $ManifestName"
}
$StepLog['inboxSideFiles'] = 0

$observed = [System.Collections.Specialized.OrderedDictionary]::new()
foreach ($item in $sideFiles) {
    $actual = Get-ShaLower ([string]$item.path)
    $observed[[string]$item.name] = $actual
    if ($actual -ne [string]$item.sha256) {
        Complete-Failed 4 'sideFileHashes' "sha256 mismatch for $($item.name): expected $($item.sha256), arrived $actual"
    }
}
$manifestActualSha = Get-ShaLower $manifestSide
if ($manifestActualSha -ne $ManifestSha256) {
    Complete-Failed 4 'sideFileHashes' "sha256 mismatch for $ManifestName : expected $ManifestSha256, arrived $manifestActualSha"
}
$StepLog['sideFileHashes'] = 0

# The manifest's own claims must still agree with the baked set: identical bytes cannot
# disagree, so this fails only if the baking itself was wrong -- which is worth knowing.
$arrivedManifest = Get-Content -Raw -LiteralPath $manifestSide | ConvertFrom-Json
if ($arrivedManifest.sourceCommit -ne $SourceCommit) {
    Complete-Failed 5 'manifestBinding' "arrived manifest sourceCommit=$($arrivedManifest.sourceCommit) does not match $SourceCommit"
}
if ($arrivedManifest.pendingSymbolPresence -isnot [bool]) {
    Complete-Failed 5 'manifestBinding' 'arrived manifest has no boolean pendingSymbolPresence; the attribution job would refuse it'
}
foreach ($claim in @($arrivedManifest.packageZip, $arrivedManifest.exe, $arrivedManifest.dll)) {
    $claimedName = [string]$claim.name
    if (-not $observed.Contains($claimedName)) {
        Complete-Failed 5 'manifestBinding' "arrived manifest names $claimedName, which is not in the staged set"
    }
    if ($observed[$claimedName] -ne ([string]$claim.sha256).ToLowerInvariant()) {
        Complete-Failed 5 'manifestBinding' "arrived manifest sha256 for $claimedName disagrees with the staged bytes"
    }
}
$StepLog['manifestBinding'] = 0

# --- transactional publish into the cache: .partial, verify, rename, manifest LAST -------------
$partialPaths = @()
foreach ($item in $sideFiles) { $partialPaths += (Join-Path $Cache "$($item.name).partial") }
$partialPaths += (Join-Path $Cache "$ManifestName.partial")
function Remove-JobPartials {
    foreach ($p in $script:partialPaths) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }
}

try {
    foreach ($item in $sideFiles) {
        $partial = Join-Path $Cache "$($item.name).partial"
        Copy-Item -LiteralPath ([string]$item.path) -Destination $partial -Force
        if ((Get-ShaLower $partial) -ne [string]$item.sha256) { throw "sha256 did not round-trip into the cache for $($item.name)" }
    }
} catch {
    Remove-JobPartials
    Complete-Failed 20 'publishPartials' $_.Exception.Message
}
$StepLog['publishPartials'] = 0

try {
    foreach ($item in $sideFiles) {
        Move-Item -LiteralPath (Join-Path $Cache "$($item.name).partial") -Destination (Join-Path $Cache ([string]$item.name)) -Force
    }
} catch {
    Remove-JobPartials
    Complete-Failed 21 'publishRename' $_.Exception.Message
}
$StepLog['publishRename'] = 0

try {
    $partialManifest = Join-Path $Cache "$ManifestName.partial"
    Copy-Item -LiteralPath $manifestSide -Destination $partialManifest -Force
    if ((Get-ShaLower $partialManifest) -ne $ManifestSha256) { throw "sha256 did not round-trip into the cache for $ManifestName" }
    Move-Item -LiteralPath $partialManifest -Destination (Join-Path $Cache $ManifestName) -Force
} catch {
    Remove-JobPartials
    Complete-Failed 22 'publishManifest' $_.Exception.Message
}
$StepLog['publishManifest'] = 0

# The side-files are MOVED, not copied: leaving the package sitting in the inbox invites a
# later job to stage a stale copy of it. Removal happens only after the cache publish is
# complete, so an interrupted run leaves the inbox intact and is simply re-runnable.
foreach ($item in $sideFiles) { Remove-Item -LiteralPath ([string]$item.path) -Force -ErrorAction SilentlyContinue }
Remove-Item -LiteralPath $manifestSide -Force -ErrorAction SilentlyContinue
$StepLog['inboxCleanup'] = 0

$published = [System.Collections.Specialized.OrderedDictionary]::new()
foreach ($item in $sideFiles) { $published[[string]$item.name] = [string]$item.sha256 }
$published[$ManifestName] = $ManifestSha256
$result = [ordered]@{
    schema = 'mlvapp.playback-attr-3-cuda-stage.v1'
    sourceCommit = $SourceCommit
    jobId = $JobId
    exitCode = 0
    cache = $Cache
    published = $published
    manifestPublishedLast = $true
    stagedOnHost = $env:COMPUTERNAME
    stagedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
    steps = $StepLog
}
$result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Pub 'result.json') -Encoding UTF8
Write-Output "RESULT=STAGE_OK SOURCE=$SourceCommit FILES=$($published.Count) CACHE=$Cache ARTIFACTS=$Pub"
exit 0
'@

$expectedLiteral = '@(' + (($expected | ForEach-Object {
    "[ordered]@{ name = '" + ([string]$_.name).Replace("'", "''") + "'; sha256 = '" + [string]$_.sha256 + "' }"
}) -join ', ') + ')'

$text = $template.
    Replace('__SOURCE_COMMIT__', $SourceCommit).
    Replace('__JOB_ID__', $JobId).
    Replace('__AGENT_ROOT__', $AgentRoot.Replace("'", "''")).
    Replace('__MANIFEST_NAME__', $names.buildManifestName).
    Replace('__MANIFEST_SHA256__', $manifestSha256).
    Replace('__EXPECTED_LITERAL__', $expectedLiteral)

[IO.File]::WriteAllText($jobPath, $text, [Text.UTF8Encoding]::new($false))

[pscustomobject]@{
    jobFile = $jobPath
    jobId = $JobId
    sourceCommit = $SourceCommit
    agentRoot = $AgentRoot
    # Exactly these files must be dropped into <agent root>\inbox beside the job, by their
    # own names. The agent executes only *.job.ps1 and leaves the rest untouched.
    inboxSideFiles = @(@($expected | ForEach-Object { [string]$_.name }) + @($names.buildManifestName))
    buildManifestName = $names.buildManifestName
    buildManifestSha256 = $manifestSha256
}
