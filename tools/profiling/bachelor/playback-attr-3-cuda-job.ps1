# playback-attr-3-cuda-job.ps1 -- GENERATOR (runs locally / in a lane; nothing here runs
# on Bachelor). Emits a self-contained <jobId>.job.ps1 for the Bachelor agent inbox
# (tools/profiling/ultra-magnus-agent.ps1 protocol: inbox\<jobId>.job.ps1 in,
# outbox\<jobId>.result.json out).
#
# Modelled on:
#   - the PLAYBACK-ATTR-2 job body (quiescence check, cache hash checks,
#     run-release-gui-smoke.ps1 invocation, prep_region_* frame parsing, PresentMon
#     sidecar handshake, evidence-manifest.json) -- see
#     .claude-state/fleet-runs/lane-PLAYBACK-ATTR-3-CUDA-S1-20260916T165824Z/inputs/playback-attr-2.job.ps1.txt
#   - the provenance sidecar contract in tools/repo_hygiene/gpu_job_result_provenance.py
#     (rangeHeadSha, llrawprocBlobId, dllSha256 lowercase, pendingSymbolPresence)
#
# FOOTAGE (NA-4, docs/never-authorized.json): the job opens exactly ONE clip, the exact
# canonical path passed as -ClipPath. There is NO id-to-file resolution anywhere; an
# earlier resolver was ruled an NA-4 evasion (sol, PR #131 r2) and removed. NA-4 admits
# a real clip only as the single path on the CLIP_OR_NONE line of the running lane's own
# MLV_LANE_PROMPT, which the owner types by hand (agents cannot write it). Run this
# generator INSIDE that owner-granted lane with -ClipPath equal to that line, so the hook
# sees the path in the lane's tool input. A two-part clip's continuation part is opened
# by the application itself, never named by this code. The owner's consent record
# (receipts/owner-footage-consent-20260916.json and its -correction.json) is evidence of
# consent, never an authorization. Adjudication:
# .claude-state/fleet-runs/swarm-footage-route-20260916T2020Z/SYNTHESIS.md.
#
# Differences from PLAYBACK-ATTR-2:
#   - shipping-default scale (4), NOT forced to 1: no MLVAPP_PLAYBACK_SCALE_FACTOR
#     override is emitted, and -ScaleFactor 4 is passed explicitly.
#   - asserts GPU recon frames > 0 and cpu_frames == 0 from the LAST
#     playback_smoke.gpu_summary line, so a silent CPU fallback fails the job instead
#     of quietly reporting a CPU-path attribution as if it were CUDA.
#   - writes a provenance.json sidecar (top-level rangeHeadSha/llrawprocBlobId/
#     dllSha256/pendingSymbolPresence) that validates under
#     tools/repo_hygiene/gpu_job_result_provenance.py.
#   - the exe/DLL under test are NOT pinned SHA256 constants (PLAYBACK-ATTR-2 already
#     had a built artifact to pin); they are named deterministically from -SourceCommit
#     and expected to already be staged in the Bachelor cache by the split-build route's
#     staging job (tools/profiling/bachelor/playback-attr-3-cuda-stage-job.ps1), which uses
#     the IDENTICAL naming convention. Their hashes are computed fresh at run time and
#     verified against the staged build manifest, not asserted against a value this
#     generator could not have known.
#
# SPLIT BUILD (swarm ruling 2026-09-16,
# .claude-state/fleet-runs/swarm-attr3-buildhost-20260916T2150Z/SYNTHESIS.md). Bachelor has
# no VC tools and no CUDA toolkit, so NOTHING is compiled or inspected with MSVC tooling
# here. The CUDA DLL pair is built on Ultra-Magnus, the exe on the board host, and the
# package is staged into the Bachelor cache by a staging job. Two consequences in this file:
#   - pendingSymbolPresence is READ from the staged build manifest (the old on-Bachelor
#     MSVC export-inspection probe is gone; it could only ever throw here);
#   - the run's own gpu_playback_recon.eligibility diagnostics are parsed BEFORE any
#     verdict, and the job exits 15 (BACKEND_NOT_AVAILABLE) unless
#     cuda_backend_available=1 and r16_available=1, recording both plus r16_reason.
#
# WHICH LOG (sol, PR #133 r2, BLOCKER). The eligibility line is read from the log
# run-release-gui-smoke.ps1 itself designates for the run just executed --
# result.json's `log.path`, the "$outputPath.run.log" per-run snapshot it calls the
# comparison authority -- bound to `evidence.runLogSnapshot.sha256`. NEVER from a glob
# over out\diagnostic\logs, which cannot match anything: that runner writes into a
# GUID-nonced logs-<stem>-<nonce> directory. The exact lines relied on are quoted beside
# the call. A log that is absent, unbound or outside this job's work tree exits 16
# (SMOKE_LOG_UNAVAILABLE) -- a missing gate is never a passed gate.
#
# Usage:
#   pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-job.ps1 `
#       -SourceCommit <40-hex> -BuildManifestSha256 <64-lowercase-hex> `
#       -ClipId M16-1243 -ClipPath <the lane's CLIP_OR_NONE path> -OutFile <path>\<jobId>.job.ps1
#
# -BuildManifestSha256 is the sha the assembler printed (MANIFEST_SHA256= on its RESULT line)
# and the staging generator echoed as buildManifestSha256; see docs/playback-attr-3-cuda.md.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$SourceCommit,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z]\d{2}-\d{3,4}$')]
    [string]$ClipId,

    # The ONE authorized clip (NA-4): must equal the CLIP_OR_NONE line of the lane running
    # this generator. An absolute path directly inside the Bachelor agent cache whose
    # BaseName is -ClipId; the emitted job re-checks both on Bachelor and fails closed.
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z]:\\[A-Za-z0-9 _.\\-]+$')]
    [string]$ClipPath,

    [Parameter(Mandatory = $true)]
    [string]$OutFile,

    # The lowercase sha256 of the build.json that the staging job published into the Bachelor
    # cache -- printed by tools/profiling/bachelor/playback-attr-3-cuda-assemble.ps1
    # (MANIFEST_SHA256= on its RESULT line) and echoed as buildManifestSha256 by
    # tools/profiling/bachelor/playback-attr-3-cuda-stage-job.ps1. MANDATORY (sol, PR #133 r2):
    # without it the job trusts whichever same-named manifest sits in the mutable cache, and
    # replacing build.json plus matching artifacts forges pendingSymbolPresence and the DLL
    # association in one move.
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$BuildManifestSha256,

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,

    # Derived from -SourceCommit, not pinned to an old package: matches the package
    # tools/profiling/bachelor/playback-attr-3-cuda-assemble.ps1 builds on the board host and
    # tools/profiling/bachelor/playback-attr-3-cuda-stage-job.ps1's emitted job stages into the
    # Bachelor cache as MLVApp-playback-attr-3-cuda-<sha12>-pkg.zip -- a raw zip of that build's
    # deployed release dir, so the exe inside keeps its unrenamed build name, MLVApp.exe. This
    # replaces the old default (the July MLVApp-CUDA-W4W5-4d1955f8.zip base, wrong for a build
    # of current master since its Qt runtime may not match). The hub must still verify the
    # derived package is actually staged in the Bachelor cache (built from -SourceCommit, not a
    # stale one) before submitting.
    [string]$BasePackageZip = "MLVApp-playback-attr-3-cuda-$($SourceCommit.Substring(0,12))-pkg.zip",
    [string]$BasePackageExeName = 'MLVApp.exe',

    [string]$PresentMonName = 'PresentMon-2.5.1-x64.exe',
    [string]$PresentMonSha256 = '9BEC3083069F58F911E6A512F4806DB51A27BD096103087BC1D05EF54C80A191',

    # Owner consent for clip M16-1243 on this card; cited (never resolved to a path
    # here) in the evidence manifest for audit trail.
    [string]$ConsentReceiptFileName = 'owner-footage-consent-20260916.json',

    [string]$LlrawprocRelativePath = 'src/mlv/llrawproc/llrawproc.c'
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1') -Force

# The emitted job runs on a host with no checkout, so it cannot Import-Module: the verification
# functions are spliced into its text VERBATIM at generation time. The test suite executes the
# module copy, so the code under test is the code that runs on Bachelor.
$embeddedFunctions = Get-AttrCudaEmbeddedFunctionSource -Name @(
    'Assert-AttrCudaBuildManifest',
    'Resolve-AttrCudaSmokeRunLog',
    'Get-AttrCudaLastEligibilityLine',
    'Get-AttrCudaEligibilityVerdict',
    'Remove-AttrCudaTree'
)

# --- resolve provenance locally, BEFORE the job ever touches Bachelor -------------
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
& git -C $RepoRoot cat-file -e "$SourceCommit^{commit}" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "SourceCommit is not a commit known to the local repo at $RepoRoot : $SourceCommit"
}
$llrawprocBlobId = (& git -C $RepoRoot rev-parse "${SourceCommit}:${LlrawprocRelativePath}").Trim()
if ($LASTEXITCODE -ne 0 -or $llrawprocBlobId -notmatch '^[0-9a-f]{40}$') {
    throw "Could not resolve a blob id for $LlrawprocRelativePath at $SourceCommit"
}
if ($PresentMonSha256 -notmatch '^[0-9A-Fa-f]{64}$') {
    throw "PresentMonSha256 is not a 64-hex sha256: $PresentMonSha256"
}

$shortSha = $SourceCommit.Substring(0, 12)
$exeName = "MLVApp-playback-attr-3-cuda-$shortSha.exe"
$reconName = "igpu_recon_cuda-playback-attr-3-cuda-$shortSha.dll"

# --- job body template (placeholders are substituted below; the body itself never
#     touches this generator's variables directly, so there is no accidental capture
#     of this machine's environment into the emitted script) ----------------------
$template = @'
$ErrorActionPreference = 'Stop'
$SourceCommit = '__SOURCE_COMMIT__'
$ClipId = '__CLIP_ID__'
$BuildManifestSha256 = '__BUILD_MANIFEST_SHA256__'
$AuthorizedClipPath = '__CLIP_PATH__'
$RangeHeadSha = '__RANGE_HEAD_SHA__'
$LlrawprocBlobId = '__LLRAWPROC_BLOB_ID__'
$ExeName = '__EXE_NAME__'
$ReconName = '__RECON_NAME__'
$BasePackageZip = '__BASE_PACKAGE_ZIP__'
$BasePackageExeName = '__BASE_PACKAGE_EXE_NAME__'
$PresentMonName = '__PRESENTMON_NAME__'
$PresentMonSha = '__PRESENTMON_SHA256__'
$ConsentReceiptFileName = '__CONSENT_RECEIPT__'
$Root = 'C:\mlvtmp\mlv-agent'
$Cache = Join-Path $Root 'cache'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$JobId = "playback-attr-3-cuda-$($SourceCommit.Substring(0,12))-$ClipId-$Stamp"
$Work = Join-Path 'C:\mlvtmp' $JobId
$Pub = Join-Path $Root "outbox\$JobId.artifacts"
$PresentMonTimedSeconds = 55

# --- verifiers, embedded VERBATIM from tools/profiling/bachelor/AttrCudaArtifacts.psm1 --------
# Defined FIRST, before any statement that calls them (sol PR #133 r3: the work-tree cleanup was
# called above its definition, so every emitted job died with command-not-found).
__EMBEDDED_FUNCTIONS__
# --- end embedded verifiers -------------------------------------------------------------------

# TEMP boundary (BLOCKER fix): job-owned scratch dir under this job's own C:\mlvtmp
# work dir, set as TEMP/TMP at the very start -- before any child process (reg.exe,
# the pwsh that runs run-release-gui-smoke.ps1/MLVApp.exe, PresentMon) -- so every one
# of them inherits it instead of the ambient (unconstrained) machine TEMP.
# Mirrors tools/profiling/bachelor/playback-attr-3-cuda-assemble.ps1's $Scratch
# pattern. $Work is created here (not later) precisely so the scratch dir it hosts is
# never wiped out from under a live $env:TEMP by a later "recreate $Work" step.
function Assert-UnderMlvTmp([string]$Path, [string]$Label) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -ne 'C:\mlvtmp' -and $full -notlike 'C:\mlvtmp\*') {
        throw "job-owned path '$Label' resolves outside C:\mlvtmp: $full"
    }
}
foreach ($check in @(
    @{ path = $Root; label = 'Root' },
    @{ path = $Work; label = 'Work' },
    @{ path = $Pub; label = 'Pub' }
)) { Assert-UnderMlvTmp $check.path $check.label }

Remove-AttrCudaTree -Path $Work
New-Item -ItemType Directory -Path $Work -Force | Out-Null
$Scratch = Join-Path $Work '.job-tmp'
New-Item -ItemType Directory -Path $Scratch -Force | Out-Null
$env:TEMP = $Scratch
$env:TMP = $Scratch

# PresentMon runs as a DIRECT child of this job (sol PR #131 r3): it inherits the job-owned
# TEMP/TMP above, so every child tool is confined to C:\mlvtmp. The elevated scheduled task
# MLV\PresentMonSidecar is NOT used: the agent account was granted ETW trace rights through
# 'Performance Log Users' (Bachelor fix-presentmon-privilege.ps1, 2026-07-28). If that grant is
# missing, PresentMon exits 6 (access denied) and this job fails closed; it never falls back to
# the task.

function Get-Sha([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}


function Save-Json($Object, [string]$Path) {
    $Object | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Get-Mean([double[]]$Values) {
    if ($Values.Count -eq 0) { return $null }
    [double](($Values | Measure-Object -Average).Average)
}

function Get-SampleSd([double[]]$Values) {
    if ($Values.Count -lt 2) { return 0.0 }
    $mean = Get-Mean $Values
    $sum = 0.0
    foreach ($value in $Values) { $sum += [math]::Pow($value - $mean, 2) }
    [math]::Sqrt($sum / ($Values.Count - 1))
}

function Get-Percentile([double[]]$Values, [double]$P) {
    if ($Values.Count -eq 0) { return $null }
    $sorted = @($Values | Sort-Object)
    if ($sorted.Count -eq 1) { return [double]$sorted[0] }
    $rank = ($sorted.Count - 1) * $P
    $low = [math]::Floor($rank)
    $high = [math]::Ceiling($rank)
    if ($low -eq $high) { return [double]$sorted[$low] }
    [double]($sorted[$low] + ($sorted[$high] - $sorted[$low]) * ($rank - $low))
}

function Get-Stats([double[]]$Values) {
    $mean = Get-Mean $Values
    $sd = Get-SampleSd $Values
    [ordered]@{
        count = $Values.Count
        meanMs = $mean
        sdMs = $sd
        cvPct = if ($null -ne $mean -and $mean -ne 0) { 100.0 * $sd / $mean } else { $null }
        p50Ms = Get-Percentile $Values 0.50
        p95Ms = Get-Percentile $Values 0.95
        p99Ms = Get-Percentile $Values 0.99
        fpsEquivalentMean = if ($null -ne $mean -and $mean -gt 0) { 1000.0 / $mean } else { $null }
    }
}

function Start-PresentMonCapture([string]$CsvPath) {
    if (Test-Path -LiteralPath $CsvPath) { throw "PresentMon output already exists: $CsvPath" }
    $pmArgs = @('--process_name', $ExeName, '--output_file', $CsvPath, '--timed', [string]$PresentMonTimedSeconds,
                '--terminate_after_timed', '--stop_existing_session', '--no_console_stats')
    # Direct child: inherits this job's TEMP/TMP. -PassThru so the exit code is checked.
    $proc = Start-Process -FilePath (Join-Path $Cache $PresentMonName) -ArgumentList $pmArgs -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 3
    if ($proc.HasExited -and $proc.ExitCode -ne 0) {
        throw "PRESENTMON_FAILED rc=$($proc.ExitCode) (6 = ETW access denied: the agent account needs 'Performance Log Users')"
    }
    return $proc
}

function Wait-PresentMonCapture($Proc, [int]$TimeoutSeconds = 35) {
    if (-not $Proc.WaitForExit($TimeoutSeconds * 1000)) {
        try { $Proc.Kill() } catch { }
        throw "PRESENTMON_TIMEOUT: did not exit within $TimeoutSeconds s after playback"
    }
    [pscustomobject]@{ status = 'done'; exitCode = $Proc.ExitCode }
}

function Get-FrameRows([string]$RawLog) {
    $rows = [System.Collections.Generic.List[object]]::new()
    $keys = @(
        'prep_region_setup_ms', 'prep_region_gpu_ms', 'prep_region_image_ms',
        'prep_region_present_ms', 'prep_region_finish_ms',
        'prep_region_total_ms', 'prep_region_unattributed_ms'
    )
    foreach ($line in ($RawLog -split "`r?`n")) {
        if ($line -notmatch 'playback_smoke\.frame ') { continue }
        $values = @{}
        foreach ($match in [regex]::Matches($line, '(?<key>[A-Za-z0-9_]+)=(?<value>[^\s]+)')) {
            $values[$match.Groups['key'].Value] = $match.Groups['value'].Value
        }
        if (-not $values.ContainsKey('prep_region_total_ms')) { continue }
        $row = [ordered]@{}
        foreach ($key in @('session','index','elapsed_ms','interval_ms','display_frame','serial')) {
            if ($values.ContainsKey($key)) { $row[$key] = $values[$key] }
        }
        foreach ($key in $keys) {
            if (-not $values.ContainsKey($key)) { continue }
            $row[$key] = [double]::Parse($values[$key], [Globalization.CultureInfo]::InvariantCulture)
        }
        if ($row.Contains('prep_region_total_ms') -and $row.Contains('prep_region_unattributed_ms')) {
            [void]$rows.Add([pscustomobject]$row)
        }
    }
    if ($rows.Count -lt 10) { throw "only $($rows.Count) high-resolution frame rows; require >=10" }
    return @($rows)
}

function Get-LastGpuSummary([string]$RawLog) {
    # Cumulative per-session counters: the LAST line carries the run's final totals.
    $last = $null
    foreach ($line in ($RawLog -split "`r?`n")) {
        if ($line -notmatch 'playback_smoke\.gpu_summary ') { continue }
        $values = @{}
        foreach ($match in [regex]::Matches($line, '(?<key>[A-Za-z0-9_]+)=(?<value>[^\s]+)')) {
            $values[$match.Groups['key'].Value] = $match.Groups['value'].Value
        }
        $last = $values
    }
    if ($null -eq $last) { throw 'no playback_smoke.gpu_summary line found in the MLVApp log' }
    $required = @('cpu_frames','gpu_preview_frames','gpu_recon_readback_frames','gpu_texture_readback_frames','gpu_texture_no_readback_frames')
    foreach ($key in $required) {
        if (-not $last.ContainsKey($key)) { throw "playback_smoke.gpu_summary line missing $key" }
    }
    [ordered]@{
        cpuFrames = [int]$last['cpu_frames']
        gpuPreviewFrames = [int]$last['gpu_preview_frames']
        gpuReconReadbackFrames = [int]$last['gpu_recon_readback_frames']
        gpuTextureReadbackFrames = [int]$last['gpu_texture_readback_frames']
        gpuTextureNoReadbackFrames = [int]$last['gpu_texture_no_readback_frames']
    }
}

foreach ($item in @(
    @{ path=(Join-Path $Cache $PresentMonName); sha=$PresentMonSha }
)) {
    if (-not (Test-Path -LiteralPath $item.path)) { throw "cache missing $($item.path)" }
    if ((Get-Sha $item.path) -ne $item.sha) { throw "hash mismatch $($item.path)" }
}
# Non-transactional publish fix (BLOCKER): existence of the exe/DLL/pkg alone does not
# prove they belong together -- a compile job's publish could have been interrupted
# between renames. The compile job's cache manifest (holding all three lowercase
# sha256s, written LAST after the atomic rename) is REQUIRED, and every one of the
# three cached files' sha256 is verified against it before anything is trusted.
$buildManifestName = "playback-attr-3-cuda-$($SourceCommit.Substring(0,12))-build.json"
$buildManifestPath = Join-Path $Cache $buildManifestName
# AUTHENTICATE THE MANIFEST BEFORE READING A SINGLE FIELD OF IT (sol, PR #133 r2). The cache is
# mutable and this job does not own it; a same-named build.json with matching artifacts would
# otherwise forge pendingSymbolPresence and the whole DLL association. $BuildManifestSha256 was
# baked in by the generator from the sha the assembler/staging step printed, and the check runs
# BEFORE ConvertFrom-Json -- a parsed field is already a trusted field. The same call also
# requires sourceCommit to equal the pinned commit, dllPairManifestSha256 to be present (so the
# exe's DLL pair is chained back to the Ultra-Magnus manifest rather than merely asserted), and
# pendingSymbolPresence to be a real boolean.
$buildManifest = Assert-AttrCudaBuildManifest -Path $buildManifestPath -ExpectedSha256 $BuildManifestSha256 -ExpectedSourceCommit $SourceCommit
# pendingSymbolPresence is READ, never re-derived here (swarm ruling
# .claude-state/fleet-runs/swarm-attr3-buildhost-20260916T2150Z/SYNTHESIS.md): this host has no
# VC tools at all, so the old on-Bachelor MSVC export-inspection probe could only ever throw.
# The symbol test runs on the host that built the DLL
# (tools/profiling/ultramagnus/playback-attr-3-cuda-dll-job.ps1) and is carried in the build
# manifest, whose bytes are now authenticated above.
$pendingSymbolPresence = [bool]$buildManifest.pendingSymbolPresence
$dllPairManifestSha256 = ([string]$buildManifest.dllPairManifestSha256).ToLowerInvariant()
$manifestChecks = @(
    @{ label = 'packageZip'; path = (Join-Path $Cache $BasePackageZip); expectedSha = $buildManifest.packageZip.sha256 },
    @{ label = 'exe'; path = (Join-Path $Cache $ExeName); expectedSha = $buildManifest.exe.sha256 },
    @{ label = 'dll'; path = (Join-Path $Cache $ReconName); expectedSha = $buildManifest.dll.sha256 }
)
foreach ($check in $manifestChecks) {
    if (-not (Test-Path -LiteralPath $check.path)) { throw "cache missing $($check.path)" }
    if ([string]::IsNullOrWhiteSpace($check.expectedSha)) { throw "build manifest $buildManifestName is missing a sha256 for $($check.label)" }
    if ((Get-Sha $check.path) -ne $check.expectedSha.ToUpperInvariant()) { throw "hash mismatch (vs build manifest $buildManifestName) for $($check.path)" }
}
foreach ($name in @($PresentMonName, 'run-release-gui-smoke.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Cache $name))) { throw "cache missing $name" }
}
# NA-4: open exactly the one authorized path baked in by the generator -- no lookup.
$clipPath = $AuthorizedClipPath
# Injection guard (sol PR #131 r4): the path is later embedded in a nested pwsh -Command string.
if ($clipPath -notmatch '^[A-Za-z]:\\[A-Za-z0-9 _.\\-]+$') { throw "authorized clip path contains characters outside the allowlist" }
if ((Split-Path -Parent $clipPath) -ine $Cache) { throw "authorized clip path is not directly inside the agent cache" }
if ([IO.Path]::GetFileNameWithoutExtension($clipPath) -cne $ClipId) { throw "authorized clip path does not name clip id $ClipId" }
if (-not (Test-Path -LiteralPath $clipPath -PathType Leaf)) { throw "authorized clip path is missing on this host" }

# $Work and $Pub already exist (created at job start, alongside the TEMP/TMP scratch
# dir under $Work) -- do not remove/recreate $Work here, which would delete the live
# $env:TEMP/$env:TMP scratch dir out from under this process.
New-Item -ItemType Directory -Path (Join-Path $Work 'out') -Force | Out-Null
New-Item -ItemType Directory -Path $Pub -Force | Out-Null
Expand-Archive -LiteralPath (Join-Path $Cache $BasePackageZip) -DestinationPath (Join-Path $Work 'pkg') -Force
$baseExe = Get-ChildItem -LiteralPath (Join-Path $Work 'pkg') -Recurse -Filter $BasePackageExeName | Select-Object -First 1
if (-not $baseExe) { throw "base package executable not found: $BasePackageExeName" }
$pkgDir = $baseExe.Directory.FullName
$exePath = Join-Path $pkgDir $ExeName
$reconDll = Join-Path $pkgDir 'igpu_recon_cuda.dll'
$cacheExeSha = Get-Sha (Join-Path $Cache $ExeName)
$cacheReconSha = Get-Sha (Join-Path $Cache $ReconName)
Copy-Item -LiteralPath (Join-Path $Cache $ExeName) -Destination $exePath -Force
Copy-Item -LiteralPath (Join-Path $Cache $ReconName) -Destination $reconDll -Force
if ((Get-Sha $exePath) -ne $cacheExeSha -or (Get-Sha $reconDll) -ne $cacheReconSha) {
    throw 'deployed artifact hash verification failed (copy from cache did not round-trip)'
}

reg add "HKCU\Software\Microsoft\DirectX\UserGpuPreferences" /v "$exePath" /t REG_SZ /d "GpuPreference=2;" /f | Out-Null
reg add "HKCU\Software\magiclantern.MLVApp\MLVApp" /v playbackProcessingSubset /t REG_DWORD /d 1 /f | Out-Null
reg add "HKCU\Software\magiclantern.MLVApp\MLVApp" /v zebras /t REG_DWORD /d 0 /f | Out-Null
reg add "HKCU\Software\magiclantern.MLVApp\MLVApp" /v caching /t REG_DWORD /d 0 /f | Out-Null
reg add "HKCU\Software\magiclantern.MLVApp\MLVApp\Playback" /v QualityMode /t REG_DWORD /d 1 /f | Out-Null
reg add "HKCU\Software\magiclantern.MLVApp\MLVApp\Playback" /v PreviewMode /t REG_DWORD /d 0 /f | Out-Null
reg add "HKCU\Software\magiclantern.MLVApp\MLVApp\Playback" /v ScaleFactorOverride /t REG_DWORD /d 0 /f | Out-Null
reg add "HKCU\Software\magiclantern.MLVApp\MLVApp\Playback" /v PreviewResolution /t REG_DWORD /d 0 /f | Out-Null

$loads = @()
for ($i = 0; $i -lt 3; $i++) {
    $loads += [double](Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    if ($i -lt 2) { Start-Sleep -Seconds 12 }
}
$avgLoad = Get-Mean $loads
if ($avgLoad -gt 20.0) {
    $venue = [ordered]@{
        schema='playback-attr-3-cuda-venue.v1'
        result='VENUE_NOT_QUIESCENT'
        cpuSamples=$loads
        cpuMean=$avgLoad
        cpuThresholdPercent=20.0
        sourceCommit=$SourceCommit
        clipId=$ClipId
        executableSha256=$cacheExeSha
        artifactRoot=$Pub
    }
    Save-Json $venue (Join-Path $Pub 'summary.json')
    Write-Output "RESULT=VENUE_NOT_QUIESCENT CPU_MEAN=$avgLoad ARTIFACTS=$Pub"
    exit 12
}

$legOut = Join-Path $Work 'out\diagnostic'
New-Item -ItemType Directory -Path $legOut -Force | Out-Null
$resultPath = Join-Path $legOut 'result.json'
$presentMonPath = Join-Path $legOut 'presentmon.csv'
$smoke = Join-Path $Cache 'run-release-gui-smoke.ps1'
$envs = @(
    'MLVAPP_PLAYBACK_QUALITY_MODE=phase3_hq',
    'MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW=0',
    'MLVAPP_PLAYBACK_PREVIEW_MODE=sharp_smooth',
    'MLVAPP_PLAYBACK_SMOKE_TELEMETRY=1',
    'MLVAPP_PLAYBACK_SMOKE_TIMELINE_TELEMETRY=1',
    'MLVAPP_PLAYBACK_DETAILED_TIMELINE_TELEMETRY=1',
    'MLVAPP_GPU_PLAYBACK_RECON_ELIGIBILITY_DIAG=1',
    'MLVAPP_STAGE_TIMING=1',
    'MLVAPP_PERF_FIELD_LOG=1',
    'MLVAPP_PLAYBACK_PHASE3_UNATTENDED=1',
    'MLVAPP_GPU_PLAYBACK_RECON=1',
    'MLVAPP_GPU_PLAYBACK_RECON_BACKEND=cuda',
    ('MLVAPP_GPU_PLAYBACK_RECON_DLL=' + $reconDll),
    'MLVAPP_GPU_PLAYBACK_RECON_ASYNC_H2D=0',
    'MLVAPP_EXPERIMENTAL_GPU_PROCESSING=1',
    'MLVAPP_EXPERIMENTAL_GPU_AMAZE_DEBAYER=1',
    'MLVAPP_EXPERIMENTAL_GL_WINDOW_VIEWPORT=1',
    'MLVAPP_VIEWPORT_PRESENT_DIAG=1',
    'MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1',
    'MLVAPP_EXPERIMENTAL_GPU_AMAZE_TEXTURE_PRESENT=1',
    'MLVAPP_GPU_PLAYBACK_RECON_RETAIN_DEVICE_OUTPUT=1',
    ('QT_PLUGIN_PATH=' + $pkgDir),
    ('QT_QPA_PLATFORM_PLUGIN_PATH=' + (Join-Path $pkgDir 'platforms')),
    'QT_OPENGL=desktop',
    'QT_FORCE_STDERR_LOGGING=1'
)
# Shipping default: scale factor 4. Unlike PLAYBACK-ATTR-2, no
# MLVAPP_PLAYBACK_SCALE_FACTOR override is emitted; -ScaleFactor 4 is explicit
# below for self-documentation even though it is run-release-gui-smoke.ps1's own
# default.
$envList = "'" + ($envs -join "','") + "'"
function ConvertTo-PsSingleQuoted([string]$Value) { "'" + $Value.Replace("'", "''") + "'" }
$cmd = "& $(ConvertTo-PsSingleQuoted $smoke) -ExePath $(ConvertTo-PsSingleQuoted $exePath) -Input $(ConvertTo-PsSingleQuoted $clipPath) -Output $(ConvertTo-PsSingleQuoted $resultPath) -Seconds 40 -StartFrame 0 -SettleMs 2500 -ScaleFactor 4 -UsePersistedPlaybackSettings -RequireLookAssist:`$false -Scope none -FrameTelemetry -PreserveExperimentalEnvironment -ExtraEnvironment @($envList)"
$presentMonProc = Start-PresentMonCapture $presentMonPath
& "$env:ProgramFiles\PowerShell\7\pwsh.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $cmd 1> (Join-Path $legOut 'smoke-stdout.txt') 2> (Join-Path $legOut 'smoke-stderr.txt')
$smokeRc = $LASTEXITCODE
$presentMonDoneResult = Wait-PresentMonCapture $presentMonProc
if (-not (Test-Path -LiteralPath $resultPath)) { throw "smoke result missing rc=$smokeRc" }
if ($presentMonDoneResult.status -ne 'done' -or [int]$presentMonDoneResult.exitCode -ne 0) {
    throw "PresentMon capture invalid status=$($presentMonDoneResult.status) rc=$($presentMonDoneResult.exitCode)"
}

$rawResult = [IO.File]::ReadAllText($resultPath)
$resultJson = $rawResult | ConvertFrom-Json -Depth 100
if ($rawResult -notmatch [regex]::Escape($SourceCommit)) { throw "result does not report pinned source commit $SourceCommit" }

# THE LOG COMES FROM THE RESULT, NOT FROM A GLOB (sol, PR #133 r2). The previous
# `out\diagnostic\logs\mlvapp-*.log` search could never match: run-release-gui-smoke.ps1 writes
# into a GUID-nonced `logs-<stem>-<nonce>` directory and publishes the authoritative per-run
# snapshot separately --
#
#     $runNonce = [Guid]::NewGuid().ToString("N")
#     $logRoot = Join-Path $outputDir ("logs-{0}-{1}" -f $outputStem, $runNonce)
#     # Preserve the exact lines consumed by this result in a per-run immutable
#     # snapshot.  The aggregate rotating app log is allowed to grow later and is
#     # therefore diagnostic only; comparison authority comes from this snapshot.
#     $runLogSnapshotPath = "$outputPath.run.log"
#     log = [pscustomobject]@{ path = $runLogSnapshotPath; aggregateSourcePath = ... }
#     evidence = [pscustomobject]@{ runNonce = $runNonce; runLogSnapshot = $runLogSnapshotBinding }
#
# so the job threw "MLVApp log missing" before it could ever reach the eligibility gate below.
# Resolve-AttrCudaSmokeRunLog reads log.path, requires it to sit inside this job's own work tree
# and to hash to evidence.runLogSnapshot.sha256, and fails closed at exit 16 otherwise -- absent
# evidence is never treated as passing evidence.
try {
    $runLog = Resolve-AttrCudaSmokeRunLog -ResultJsonPath $resultPath -ContainingRoot $Work
} catch {
    $unavailable = [ordered]@{
        schema='playback-attr-3-cuda-venue.v1'; result='SMOKE_LOG_UNAVAILABLE'
        message=$_.Exception.Message; smokeExitCode=$smokeRc; resultJson=$resultPath
        sourceCommit=$SourceCommit; clipId=$ClipId; artifactRoot=$Pub
    }
    Save-Json $unavailable (Join-Path $Pub 'summary.json')
    Write-Output "RESULT=SMOKE_LOG_UNAVAILABLE MESSAGE=`"$($_.Exception.Message)`" ARTIFACTS=$Pub"
    exit 16
}
$logPath = $runLog.path
$rawLog = [IO.File]::ReadAllText($logPath)
$rows = Get-FrameRows $rawLog
$rows | Export-Csv -LiteralPath (Join-Path $legOut 'probe-timeline.csv') -NoTypeInformation

# Backend-availability gate (swarm ruling, 2026-09-16): parse the run's own diagnostic
# fields BEFORE any verdict. A run where the CUDA backend never loaded, or where the R16
# texture path was not admitted, cannot produce a CUDA attribution -- the frame counters
# alone would happily describe some other path. Both fields and r16_reason are recorded
# either way, so a refusal says WHY.
$verdict = Get-AttrCudaEligibilityVerdict -LogText $rawLog
$diagnostics = [ordered]@{
    source = $verdict.source
    linePresent = $verdict.linePresent
    cudaBackendAvailable = $verdict.cudaBackendAvailable
    r16Available = $verdict.r16Available
    r16Reason = $verdict.r16Reason
    cudaBackendAttempted = $verdict.cudaBackendAttempted
    cudaBackendResolved = $verdict.cudaBackendResolved
    r16ProbeRan = $verdict.r16ProbeRan
    admitted = $verdict.admitted
    # Which bytes the verdict was read from, and the binding that proves they are this run's.
    log = [ordered]@{ path = $runLog.path; sha256 = $runLog.sha256; bytes = $runLog.bytes; runNonce = $runLog.runNonce; source = $runLog.source; aggregateSourcePath = $runLog.aggregateSourcePath }
}
if (-not $verdict.admitted) {
    $refusal = [ordered]@{
        schema='playback-attr-3-cuda-venue.v1'; result='BACKEND_NOT_AVAILABLE'
        diagnostics=$diagnostics; sourceCommit=$SourceCommit; clipId=$ClipId; artifactRoot=$Pub
    }
    Save-Json $refusal (Join-Path $Pub 'summary.json')
    Write-Output "RESULT=BACKEND_NOT_AVAILABLE CUDA_BACKEND_AVAILABLE=$($verdict.cudaBackendAvailable) R16_AVAILABLE=$($verdict.r16Available) R16_REASON=`"$($verdict.r16Reason)`" ARTIFACTS=$Pub"
    exit $verdict.exitCode
}

$gpuSummary = Get-LastGpuSummary $rawLog
# CUDA gate fix (MAJOR): gpu_preview_frames is not CUDA reconstruction -- a run with
# only preview frames and zero recon/readback/texture frames must not pass as CUDA-
# exercised. Only recon/texture readback and no-readback frames count toward the gate.
$gpuFramesTotal = $gpuSummary.gpuReconReadbackFrames + $gpuSummary.gpuTextureReadbackFrames + $gpuSummary.gpuTextureNoReadbackFrames
if ($gpuFramesTotal -le 0) {
    $fallback = [ordered]@{
        schema='playback-attr-3-cuda-venue.v1'; result='GPU_RECON_FRAMES_ZERO'
        gpuSummary=$gpuSummary; sourceCommit=$SourceCommit; clipId=$ClipId; artifactRoot=$Pub
    }
    Save-Json $fallback (Join-Path $Pub 'summary.json')
    Write-Output "RESULT=GPU_RECON_FRAMES_ZERO ARTIFACTS=$Pub"
    exit 13
}
if ($gpuSummary.cpuFrames -gt 0) {
    $fallback = [ordered]@{
        schema='playback-attr-3-cuda-venue.v1'; result='CPU_FALLBACK_DETECTED'
        gpuSummary=$gpuSummary; sourceCommit=$SourceCommit; clipId=$ClipId; artifactRoot=$Pub
    }
    Save-Json $fallback (Join-Path $Pub 'summary.json')
    Write-Output "RESULT=CPU_FALLBACK_DETECTED CPU_FRAMES=$($gpuSummary.cpuFrames) ARTIFACTS=$Pub"
    exit 14
}

$stats = [ordered]@{}
foreach ($name in @('prep_region_setup','prep_region_gpu','prep_region_image','prep_region_present','prep_region_finish','prep_region_total','prep_region_unattributed')) {
    $values = @($rows | ForEach-Object { [double]$_.$($name + '_ms') })
    $stats[$name] = Get-Stats $values
}

$pmRows = @()
foreach ($row in @(Import-Csv -LiteralPath $presentMonPath)) {
    $value = 0.0
    if ([double]::TryParse([string]$row.MsBetweenDisplayChange, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$value) -and $value -gt 0) {
        $pmRows += [pscustomobject]@{
            ordinal = $pmRows.Count
            timeInMs = [double]$row.TimeInMs
            msBetweenDisplayChange = $value
            displayFpsEquivalent = 1000.0 / $value
            presentMode = [string]$row.PresentMode
        }
    }
}
if ($pmRows.Count -eq 0) { throw 'PresentMon sidecar had no positive MsBetweenDisplayChange samples' }
$pmRows | Export-Csv -LiteralPath (Join-Path $legOut 'presentmon-series.csv') -NoTypeInformation
$pmStats = Get-Stats @($pmRows | ForEach-Object { [double]$_.msBetweenDisplayChange })

$dllSha256Lower = (Get-Sha $reconDll).ToLowerInvariant()
# $pendingSymbolPresence came from the build manifest above, whose dll.sha256 was verified
# against this exact DLL before it was deployed -- so the export claim is bound to these bytes.
$provenance = [ordered]@{
    rangeHeadSha = $RangeHeadSha
    llrawprocBlobId = $LlrawprocBlobId
    dllSha256 = $dllSha256Lower
    pendingSymbolPresence = $pendingSymbolPresence
}
Save-Json $provenance (Join-Path $Pub 'provenance.json')

Copy-Item -LiteralPath $resultPath -Destination (Join-Path $Pub 'result.json') -Force
Copy-Item -LiteralPath (Join-Path $legOut 'smoke-stdout.txt') -Destination (Join-Path $Pub 'smoke-stdout.txt') -Force
Copy-Item -LiteralPath (Join-Path $legOut 'smoke-stderr.txt') -Destination (Join-Path $Pub 'smoke-stderr.txt') -Force
Copy-Item -LiteralPath (Join-Path $legOut 'probe-timeline.csv') -Destination (Join-Path $Pub 'probe-timeline.csv') -Force
Copy-Item -LiteralPath $presentMonPath -Destination (Join-Path $Pub 'presentmon.csv') -Force
Copy-Item -LiteralPath (Join-Path $legOut 'presentmon-series.csv') -Destination (Join-Path $Pub 'presentmon-series.csv') -Force
New-Item -ItemType Directory -Path (Join-Path $Pub 'logs') -Force | Out-Null
# The per-run snapshot, under the name that says what it is. The aggregate rotating app log is
# NOT published: the smoke runner is explicit that it may grow after the run and carries no
# comparison authority.
Copy-Item -LiteralPath $logPath -Destination (Join-Path $Pub 'logs\smoke-run.log') -Force

$manifest = [ordered]@{
    schema = 'playback-attr-3-cuda-evidence-manifest.v1'
    sourceCommit = $SourceCommit
    clipId = $ClipId
    consentReceipt = $ConsentReceiptFileName
    scaleFactor = 4
    # The authenticated chain, end to end: this manifest's own bytes, and the DLL-pair manifest
    # it names. Neither is a claim the measurement host had to take on trust.
    buildManifest = [ordered]@{ name=$buildManifestName; sha256=$BuildManifestSha256; dllPairManifestSha256=$dllPairManifestSha256 }
    executable = [ordered]@{ name=$ExeName; sha256=$cacheExeSha }
    reconDll = [ordered]@{ name=$ReconName; sha256=(Get-Sha $reconDll) }
    presentMon = [ordered]@{ name=$PresentMonName; sha256=$PresentMonSha; launch='direct-child-inherits-job-temp'; positiveSamples=$pmRows.Count }
    environmentBoundary = [ordered]@{ jobTempDir=$Scratch; allChildrenInheritJobTemp=$true }
    cpuQuiescence = [ordered]@{ samples=$loads; meanPercent=$avgLoad; thresholdPercent=20.0; pass=($avgLoad -le 20.0) }
    frameRows = $rows.Count
    smokeRunLog = [ordered]@{ path=$runLog.path; sha256=$runLog.sha256; bytes=$runLog.bytes; runNonce=$runLog.runNonce; source=$runLog.source }
    diagnostics = $diagnostics
    gpuSummary = $gpuSummary
    gpuFramesTotal = $gpuFramesTotal
    regions = $stats
    presentMonStats = $pmStats
    provenance = $provenance
    artifactRoot = $Pub
    capturedUtc = (Get-Date).ToUniversalTime().ToString('o')
}
Save-Json $manifest (Join-Path $Pub 'evidence-manifest.json')
$files = Get-ChildItem -LiteralPath $Pub -Recurse -File | ForEach-Object { [ordered]@{ path=$_.FullName.Substring($Pub.Length + 1); sha256=(Get-Sha $_.FullName); bytes=$_.Length } }
Save-Json ([ordered]@{ schema='playback-attr-3-cuda-artifact-index.v1'; artifactRoot=$Pub; files=$files }) (Join-Path $Pub 'artifact-index.json')
Write-Output "RESULT=MEASUREMENT_CAPTURED SOURCE=$SourceCommit CLIP=$ClipId ROWS=$($rows.Count) GPU_FRAMES=$gpuFramesTotal CPU_FRAMES=$($gpuSummary.cpuFrames) PRESENTMON_SAMPLES=$($pmRows.Count) ARTIFACTS=$Pub"
exit 0
'@

$text = $template.
    Replace('__SOURCE_COMMIT__', $SourceCommit).
    Replace('__CLIP_ID__', $ClipId).
    Replace('__BUILD_MANIFEST_SHA256__', $BuildManifestSha256.ToLowerInvariant()).
    Replace('__CLIP_PATH__', $ClipPath.Replace("'", "''")).
    Replace('__RANGE_HEAD_SHA__', $SourceCommit).
    Replace('__LLRAWPROC_BLOB_ID__', $llrawprocBlobId).
    Replace('__EXE_NAME__', $exeName).
    Replace('__RECON_NAME__', $reconName).
    Replace('__BASE_PACKAGE_ZIP__', $BasePackageZip).
    Replace('__BASE_PACKAGE_EXE_NAME__', $BasePackageExeName).
    Replace('__PRESENTMON_NAME__', $PresentMonName).
    Replace('__PRESENTMON_SHA256__', $PresentMonSha256).
    Replace('__CONSENT_RECEIPT__', $ConsentReceiptFileName)
# LAST: the module text is spliced in after every other substitution, so no placeholder rule can
# rewrite a character inside the verbatim verifier source.
$text = $text.Replace('__EMBEDDED_FUNCTIONS__', $embeddedFunctions)

$outDir = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
[IO.File]::WriteAllText($OutFile, $text, [Text.UTF8Encoding]::new($false))

[pscustomobject]@{
    outFile = $OutFile
    sourceCommit = $SourceCommit
    buildManifestSha256 = $BuildManifestSha256.ToLowerInvariant()
    clipId = $ClipId
    exeName = $exeName
    reconName = $reconName
    rangeHeadSha = $SourceCommit
    llrawprocBlobId = $llrawprocBlobId
}
