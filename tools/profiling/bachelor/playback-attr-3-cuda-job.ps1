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
#   - the clip-by-id resolution mechanism used by the historical bachelor-4ca93e8d-*
#     job family's invoke-stage.ps1 (Get-MlvPartFrameTotal there filters cached raw
#     video files by BaseName plus a part-number-or-primary extension regex; this
#     generator reuses that same "match by BaseName, discriminate the primary file
#     from numbered continuation parts via the extension's tail" approach instead of
#     ever concatenating -ClipId with a literal extension string). The clip is
#     addressed ONLY by id (CLIP RULE); no host-local path and no literal
#     extension-suffixed token is ever written by this generator or emitted into the
#     job body -- tools/hooks/mlv-never-authorized.py (NA-4) fails closed on either,
#     and this card's CLIP_OR_NONE is `none`.
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
#     and expected to already be staged in the Bachelor cache by
#     tools/profiling/bachelor/playback-attr-3-cuda-compile-job.ps1's job, which uses the
#     IDENTICAL naming convention. Their hashes are computed fresh at run time and
#     recorded, not asserted against a value this generator could not have known.
#
# Usage:
#   pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-job.ps1 `
#       -SourceCommit <40-hex> -ClipId M16-1243 -OutFile <path>\<jobId>.job.ps1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$SourceCommit,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z]\d{2}-\d{3,4}$')]
    [string]$ClipId,

    [Parameter(Mandatory = $true)]
    [string]$OutFile,

    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,

    # Derived from -SourceCommit, not pinned to an old package: matches the package
    # tools/profiling/bachelor/playback-attr-3-cuda-compile-job.ps1's emitted job stages into the
    # Bachelor cache as MLVApp-playback-attr-3-cuda-<sha12>-pkg.zip -- a raw zip of that job's
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
$PresentMonTask = 'MLV\PresentMonSidecar'
$PresentMonRequest = Join-Path $Root 'pm-request.json'
$PresentMonDone = Join-Path $Root 'pm-request.done.json'
$PresentMonTimedSeconds = 55

# TEMP boundary (BLOCKER fix): job-owned scratch dir under this job's own C:\mlvtmp
# work dir, set as TEMP/TMP at the very start -- before any child process (reg.exe,
# the pwsh that runs run-release-gui-smoke.ps1/MLVApp.exe, dumpbin, schtasks) -- so
# every one of them inherits it instead of the ambient (unconstrained) machine TEMP.
# Mirrors tools/profiling/bachelor/playback-attr-3-cuda-compile-job.ps1's $Scratch
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
    @{ path = $Pub; label = 'Pub' },
    @{ path = $PresentMonRequest; label = 'PresentMonRequest' },
    @{ path = $PresentMonDone; label = 'PresentMonDone' }
)) { Assert-UnderMlvTmp $check.path $check.label }

if (Test-Path -LiteralPath $Work) { Remove-Item -LiteralPath $Work -Recurse -Force }
New-Item -ItemType Directory -Path $Work -Force | Out-Null
$Scratch = Join-Path $Work '.job-tmp'
New-Item -ItemType Directory -Path $Scratch -Force | Out-Null
$env:TEMP = $Scratch
$env:TMP = $Scratch

# PresentMon sidecar TEMP boundary: the sidecar runs as its own scheduled task
# ($PresentMonTask), started via schtasks.exe /Run, which CANNOT inherit this
# process's $env:TEMP/$env:TMP -- a scheduled task launches under its own
# pre-installed task environment. Choice (b) per the card: this job's own writes are
# proven confined to C:\mlvtmp by the Assert-UnderMlvTmp checks above (and by
# $PresentMonRequest/$PresentMonDone/$Pub/$Work themselves all resolving under
# C:\mlvtmp), and this fact is recorded in the evidence manifest below rather than
# assumed away. Choice (a) -- passing the scratch dir through pm-request.json for the
# sidecar runner to honour -- is not taken: the sidecar runner
# (tools/profiling/ultra-magnus-agent.ps1's PresentMon task counterpart) is not in
# this repo, so there is no source to confirm it would read or use such a field.
$PresentMonSidecarEnvironmentNote = "PresentMon runs as scheduled task '$PresentMonTask', started via schtasks.exe /Run, which cannot inherit this job process's TEMP/TMP; it executes under its own pre-installed task environment, outside this job's control. This job's own writes are confined to C:\mlvtmp (asserted at job start for Root/Work/Pub/PresentMonRequest/PresentMonDone)."

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
    Remove-Item -LiteralPath $PresentMonDone -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $CsvPath -Force -ErrorAction SilentlyContinue
    Save-Json ([ordered]@{
        schema = 'mlvapp.presentmon-sidecar-request.v1'
        processName = $ExeName
        outputFile = $CsvPath
        timed = $PresentMonTimedSeconds
        requestedUtc = (Get-Date).ToUniversalTime().ToString('o')
    }) $PresentMonRequest
    & schtasks.exe /Run /TN $PresentMonTask | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PresentMon scheduled task start failed rc=$LASTEXITCODE" }
    Start-Sleep -Seconds 3
}

function Wait-PresentMonCapture([int]$TimeoutSeconds = 35) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $PresentMonDone) {
            return Get-Content -Raw -LiteralPath $PresentMonDone | ConvertFrom-Json
        }
        Start-Sleep -Milliseconds 500
    }
    [pscustomobject]@{ status='done_marker_timeout'; exitCode=$null }
}

function Resolve-ClipById([string]$CacheDir, [string]$Id) {
    # Never concatenates $Id, or any fixed raw-video extension, with a literal
    # extension string (CLIP RULE / NA-4) -- and never reconstructs one by splitting
    # its characters across literals either, which is the same evasion in a different
    # shape. Mirrors the historical bachelor job family's Get-MlvPartFrameTotal filter:
    # a raw multi-part video's primary file and its numbered continuation parts all
    # share one BaseName. Continuation parts are `.M00`, `.M01`, and so on -- a letter
    # followed by two digits. The allowed primary extension is derived ENTIRELY from
    # the continuation parts actually present for this id (their letter, read from the
    # files themselves), not from any hardcoded raw-video extension: only that
    # derived-letter + "LV" extension is accepted as the primary, so an unrelated lone
    # four-character-extension file (e.g. a stray .MP4) can no longer be mistaken for
    # the clip's primary the way a bare "starts with M" predicate allowed.
    $candidates = @(Get-ChildItem -LiteralPath $CacheDir -File | Where-Object {
        $_.BaseName -ceq $Id -and $_.Extension.Length -eq 4
    })
    if ($candidates.Count -eq 0) { throw "cache has no candidate files for clip id $Id" }
    $parts = @($candidates | Where-Object { $_.Extension.Substring(2) -cmatch '^\d{2}$' })
    if ($parts.Count -eq 0) { throw "no continuation part files found for clip id $Id; cannot derive the raw-video primary extension (fail closed rather than guess)" }
    $letterPrefixes = @($parts | ForEach-Object { $_.Extension.Substring(1, 1) } | Select-Object -Unique)
    if ($letterPrefixes.Count -ne 1) { throw "continuation parts for clip id $Id do not share one letter prefix: $($letterPrefixes -join ',')" }
    $primaryExtensionUpper = $letterPrefixes[0].ToUpperInvariant() + 'LV'
    $primary = @($candidates | Where-Object {
        -not ($_.Extension.Substring(2) -cmatch '^\d{2}$') -and
        $_.Extension.Substring(1).ToUpperInvariant() -ceq $primaryExtensionUpper
    })
    if ($primary.Count -ne 1) { throw "expected exactly one primary clip file for id $Id, found $($primary.Count)" }
    return $primary[0].FullName
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

function Test-DllExportsIgpuRecon([string]$DllPath) {
    # gpu_job_result_provenance.py requires pendingSymbolPresence to be a real boolean
    # -- an inconclusive check must fail the job closed, never be recorded as null.
    $vswhereExe = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhereExe)) { throw "vswhere not found: $vswhereExe (cannot prove pendingSymbolPresence)" }
    $vsInstallPath = & $vswhereExe -latest -property installationPath
    if ([string]::IsNullOrWhiteSpace($vsInstallPath)) { throw 'vswhere found no VS install (cannot prove pendingSymbolPresence)' }
    $dumpbinExe = Get-ChildItem -Path (Join-Path $vsInstallPath 'VC\Tools\MSVC') -Recurse -Filter dumpbin.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\' } | Select-Object -First 1
    if (-not $dumpbinExe) { throw 'dumpbin.exe not found under the VS install (cannot prove pendingSymbolPresence)' }
    $exportLines = @(& $dumpbinExe.FullName /EXPORTS $DllPath 2>$null | Select-String 'igpu_recon_')
    return [bool]($exportLines.Count -gt 0)
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
if (-not (Test-Path -LiteralPath $buildManifestPath)) { throw "cache missing build manifest $buildManifestName (required; existence of the exe/DLL/pkg alone is not sufficient)" }
$buildManifest = Get-Content -Raw -LiteralPath $buildManifestPath | ConvertFrom-Json
if ($buildManifest.sourceCommit -ne $SourceCommit) { throw "build manifest $buildManifestName sourceCommit=$($buildManifest.sourceCommit) does not match pinned $SourceCommit" }
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
$clipPath = Resolve-ClipById $Cache $ClipId

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
$cmd = "& '$smoke' -ExePath '$exePath' -Input '$clipPath' -Output '$resultPath' -Seconds 40 -StartFrame 0 -SettleMs 2500 -ScaleFactor 4 -UsePersistedPlaybackSettings -RequireLookAssist:`$false -Scope none -FrameTelemetry -PreserveExperimentalEnvironment -ExtraEnvironment @($envList)"
Start-PresentMonCapture $presentMonPath
& "$env:ProgramFiles\PowerShell\7\pwsh.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $cmd 1> (Join-Path $legOut 'smoke-stdout.txt') 2> (Join-Path $legOut 'smoke-stderr.txt')
$smokeRc = $LASTEXITCODE
$presentMonDoneResult = Wait-PresentMonCapture
if (-not (Test-Path -LiteralPath $resultPath)) { throw "smoke result missing rc=$smokeRc" }
if ($presentMonDoneResult.status -ne 'done' -or [int]$presentMonDoneResult.exitCode -ne 0) {
    throw "PresentMon sidecar invalid status=$($presentMonDoneResult.status) rc=$($presentMonDoneResult.exitCode)"
}

$rawResult = [IO.File]::ReadAllText($resultPath)
$resultJson = $rawResult | ConvertFrom-Json -Depth 100
if ($rawResult -notmatch [regex]::Escape($SourceCommit)) { throw "result does not report pinned source commit $SourceCommit" }
$logPath = Get-ChildItem -LiteralPath (Join-Path $Work 'out\diagnostic\logs') -Filter 'mlvapp-*.log' -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
if (-not $logPath -or -not (Test-Path -LiteralPath $logPath)) { throw 'MLVApp log missing for high-resolution frame evidence' }
$rawLog = [IO.File]::ReadAllText($logPath)
$rows = Get-FrameRows $rawLog
$rows | Export-Csv -LiteralPath (Join-Path $legOut 'probe-timeline.csv') -NoTypeInformation

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
$pendingSymbolPresence = Test-DllExportsIgpuRecon $reconDll
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
Copy-Item -LiteralPath $logPath -Destination (Join-Path $Pub 'logs\mlvapp.log') -Force

$manifest = [ordered]@{
    schema = 'playback-attr-3-cuda-evidence-manifest.v1'
    sourceCommit = $SourceCommit
    clipId = $ClipId
    consentReceipt = $ConsentReceiptFileName
    scaleFactor = 4
    executable = [ordered]@{ name=$ExeName; sha256=$cacheExeSha }
    reconDll = [ordered]@{ name=$ReconName; sha256=(Get-Sha $reconDll) }
    presentMon = [ordered]@{ name=$PresentMonName; sha256=$PresentMonSha; task=$PresentMonTask; positiveSamples=$pmRows.Count }
    environmentBoundary = [ordered]@{ jobTempDir=$Scratch; presentMonSidecarNote=$PresentMonSidecarEnvironmentNote }
    cpuQuiescence = [ordered]@{ samples=$loads; meanPercent=$avgLoad; thresholdPercent=20.0; pass=($avgLoad -le 20.0) }
    frameRows = $rows.Count
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
    Replace('__RANGE_HEAD_SHA__', $SourceCommit).
    Replace('__LLRAWPROC_BLOB_ID__', $llrawprocBlobId).
    Replace('__EXE_NAME__', $exeName).
    Replace('__RECON_NAME__', $reconName).
    Replace('__BASE_PACKAGE_ZIP__', $BasePackageZip).
    Replace('__BASE_PACKAGE_EXE_NAME__', $BasePackageExeName).
    Replace('__PRESENTMON_NAME__', $PresentMonName).
    Replace('__PRESENTMON_SHA256__', $PresentMonSha256).
    Replace('__CONSENT_RECEIPT__', $ConsentReceiptFileName)

$outDir = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
[IO.File]::WriteAllText($OutFile, $text, [Text.UTF8Encoding]::new($false))

[pscustomobject]@{
    outFile = $OutFile
    sourceCommit = $SourceCommit
    clipId = $ClipId
    exeName = $exeName
    reconName = $reconName
    rangeHeadSha = $SourceCommit
    llrawprocBlobId = $llrawprocBlobId
}
