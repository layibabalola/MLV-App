param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "C:\temp\MLV\M16-1327.MLV",
    [string]$Receipt = "receipts\FastProxy.marxml",
    [string]$OutputRoot = "",
    [int]$Seconds = 10,
    [int]$SettleMs = 1000,
    [int]$ValidationSampleEvery = 10,
    [string]$QualityMode = "4",
    [string]$ScaleFactor = "1",
    [int]$MaxFrames = 4,
    [int]$Repeats = 1,
    [string[]]$CdngCodecs = @("uncompressed", "lossless"),
    [string]$RequiredGpuNamePattern = "NVIDIA|GeForce|RTX|GTX|Quadro|Laptop",
    [string]$GpuPlaybackReconBackend = "",
    [string]$GpuExportBackend = "",
    [bool]$TrustedGpuExport = $true,
    [switch]$SkipPlayback,
    [switch]$SkipCdng,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ($Seconds -lt 1) {
    throw "-Seconds must be >= 1."
}
if ($SettleMs -lt 0) {
    throw "-SettleMs must be >= 0."
}
if ($ValidationSampleEvery -lt 1) {
    throw "-ValidationSampleEvery must be >= 1."
}
if ($MaxFrames -lt 1) {
    throw "-MaxFrames must be >= 1."
}
if ($Repeats -lt 1) {
    throw "-Repeats must be >= 1."
}
if ($SkipPlayback -and $SkipCdng) {
    throw "At least one of playback or CDNG validation must run."
}

function Resolve-RepoPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return (Join-Path $Root $Path)
}

function Add-Failure {
    param(
        [System.Collections.Generic.List[string]]$Failures,
        [string]$Message
    )
    [void]$Failures.Add($Message)
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Value | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-FileArtifact {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (!(Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{
            path = $Path
            exists = $false
            length = $null
            lastWriteTime = $null
            sha256 = $null
        }
    }

    $item = Get-Item -LiteralPath $Path
    [pscustomobject]@{
        path = $item.FullName
        exists = $true
        length = $item.Length
        lastWriteTime = $item.LastWriteTime
        sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
    }
}

function Normalize-CdngCodecs {
    param([AllowNull()][string[]]$Values)

    $normalized = [System.Collections.Generic.List[string]]::new()
    foreach ($value in @($Values)) {
        if ($null -eq $value) {
            continue
        }
        foreach ($part in ([string]$value).Split(",")) {
            $codec = $part.Trim().ToLowerInvariant()
            if ([string]::IsNullOrWhiteSpace($codec)) {
                continue
            }
            switch ($codec) {
                "default" { $codec = "uncompressed" }
                "compressed" { $codec = "lossless" }
                "fast" { $codec = "fast-pass" }
                "fastpass" { $codec = "fast-pass" }
            }
            if ($codec -notin @("uncompressed", "lossless", "fast-pass")) {
                throw "Invalid CDNG codec '$part'. Use uncompressed, lossless, or fast-pass."
            }
            [void]$normalized.Add($codec)
        }
    }

    @($normalized | Select-Object -Unique)
}

function Invoke-ChildPowerShell {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = & pwsh.exe @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    [pscustomobject]@{
        exitCode = $exitCode
        output = @($output | ForEach-Object { [string]$_ })
    }
}

function Get-NvidiaSmiRows {
    try {
        $rows = @(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1 |
            ForEach-Object { [string]$_ })
        if ($LASTEXITCODE -ne 0) {
            return [pscustomobject]@{
                ok = $false
                rows = $rows
                error = "nvidia-smi exited with code $LASTEXITCODE."
            }
        }
        return [pscustomobject]@{
            ok = ($rows.Count -gt 0)
            rows = $rows
            error = if ($rows.Count -gt 0) { $null } else { "nvidia-smi returned no GPU rows." }
        }
    }
    catch {
        [pscustomobject]@{
            ok = $false
            rows = @()
            error = $_.Exception.Message
        }
    }
}

function Convert-ToInt64 {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return 0L
    }
    [long]$Value
}

function Get-PlaybackProofSummary {
    param([AllowNull()]$Summary)

    if ($null -eq $Summary) {
        return $null
    }

    $clips = @($Summary.clipResults)
    $totalNoReadback = 0L
    $totalFallback = 0L
    $totalProbeActive = 0L
    $totalParityMismatch = 0L
    $renderers = @()
    $backendArtifacts = @()
    foreach ($clip in $clips) {
        $totalNoReadback += Convert-ToInt64 $clip.gpuTextureNoReadbackFrames
        $totalFallback += Convert-ToInt64 $clip.fallbackFrameCount
        $totalProbeActive += Convert-ToInt64 $clip.glProbeActiveCount
        $totalParityMismatch += Convert-ToInt64 $clip.glMismatchTotal
        foreach ($renderer in @($clip.glRendererDescriptions)) {
            if (-not [string]::IsNullOrWhiteSpace([string]$renderer)) {
                $renderers += [string]$renderer
            }
        }
        foreach ($artifact in @($clip.glBackendArtifacts)) {
            if ($null -ne $artifact) {
                $backendArtifacts += $artifact
            }
        }
    }

    [pscustomobject]@{
        status = $Summary.status
        correctnessValidated = [bool]$Summary.proof.correctnessValidated
        releaseSha256 = $Summary.release.sha256
        totalGpuTextureNoReadbackFrames = $totalNoReadback
        totalFallbackFrames = $totalFallback
        totalGlProbeActiveFrames = $totalProbeActive
        totalGlParityMismatches = $totalParityMismatch
        rendererDescriptions = @($renderers | Select-Object -Unique)
        backendArtifacts = @($backendArtifacts)
        clips = @($clips | ForEach-Object {
            [pscustomobject]@{
                clip = $_.clip
                status = $_.status
                presentedFrames = $_.presentedFrames
                presentedFps = $_.presentedFps
                timelineFps = $_.timelineFps
                gpuTextureNoReadbackFrames = $_.gpuTextureNoReadbackFrames
                fallbackFrameCount = $_.fallbackFrameCount
                glProbeActiveCount = $_.glProbeActiveCount
                glParityMatchCount = $_.glParityMatchCount
                glMismatchTotal = $_.glMismatchTotal
                glScreenshotMethod = $_.glScreenshotMethod
            }
        })
    }
}

function Get-CdngProofSummary {
    param([AllowNull()]$Summary)

    if ($null -eq $Summary) {
        return $null
    }

    $runs = @()
    foreach ($case in @($Summary.cases)) {
        foreach ($run in @($case.runs)) {
            $runs += $run
        }
    }

    $candidateAttempted = 0L
    $candidateReplaced = 0L
    $candidateTrusted = 0L
    $candidateFrames = 0L
    $baselineAttempted = 0L
    foreach ($run in $runs) {
        $candidateAttempted += Convert-ToInt64 $run.candidateGpuExportAttemptedFrames
        $candidateReplaced += Convert-ToInt64 $run.candidateGpuExportReplacedFrames
        $candidateTrusted += Convert-ToInt64 $run.candidateGpuExportTrustedFrames
        $candidateFrames += Convert-ToInt64 $run.candidateFrameCount
        $baselineAttempted += Convert-ToInt64 $run.baselineGpuExportAttemptedFrames
    }

    [pscustomobject]@{
        verdict = $Summary.verdict
        totals = $Summary.totals
        dngHash = $Summary.dngHash
        baselineGpuExportAttemptedFrames = $baselineAttempted
        candidateFrameCount = $candidateFrames
        candidateGpuExportAttemptedFrames = $candidateAttempted
        candidateGpuExportReplacedFrames = $candidateReplaced
        candidateGpuExportTrustedFrames = $candidateTrusted
        cases = @($Summary.cases | ForEach-Object {
            [pscustomobject]@{
                name = $_.name
                cdngCodec = $_.cdngCodec
                verdict = $_.verdict
                maxFrames = $_.maxFrames
                repeats = $_.repeats
            }
        })
    }
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$smokeScript = Join-Path $root "tools\profiling\run-ultramagnus-p3-validation.ps1"
$matrixScript = Join-Path $root "tools\profiling\run-release-cdng-export-profile-matrix.ps1"
if (!(Test-Path -LiteralPath $smokeScript)) {
    throw "P3 validation script not found: $smokeScript"
}
if (!(Test-Path -LiteralPath $matrixScript)) {
    throw "CDNG matrix script not found: $matrixScript"
}

$clipFullPath = Resolve-RepoPath -Root $root -Path $ClipPath
$receiptFullPath = Resolve-RepoPath -Root $root -Path $Receipt
if (!$DryRun -and !(Test-Path -LiteralPath $clipFullPath)) {
    throw "Clip not found: $clipFullPath"
}
if (!$DryRun -and !(Test-Path -LiteralPath $receiptFullPath)) {
    throw "Receipt not found: $receiptFullPath"
}

if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "platform\qt\build-release\release\MLVApp.exe"
}
else {
    $ExePath = Resolve-RepoPath -Root $root -Path $ExePath
}
$releaseDir = Split-Path -Parent $ExePath
$backendDll = Join-Path $releaseDir "igpu_recon_cuda.dll"
$cudartDll = Join-Path $releaseDir "cudart64_12.dll"

$codecs = Normalize-CdngCodecs $CdngCodecs
if ($codecs.Count -eq 0) {
    throw "At least one CDNG codec is required."
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputRoot = Join-Path $root ".claude-state\profiling\$stamp-local-cuda-playback-dng-smoke"
}
$runRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
    (Resolve-RepoPath -Root $root -Path $OutputRoot))
$playbackRoot = Join-Path $runRoot "playback"
$cdngRoot = Join-Path $runRoot "cdng"
$summaryPath = Join-Path $runRoot "summary.json"
$latestPath = Join-Path $root ".claude-state\profiling\local-cuda-playback-dng-smoke-latest.json"
$casesPath = Join-Path $runRoot "cdng-cases.json"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$nvidia = if ($DryRun) {
    [pscustomobject]@{ ok = $null; rows = @(); error = "dry run: nvidia-smi not executed" }
}
else {
    Get-NvidiaSmiRows
}
if (!$DryRun -and -not [bool]$nvidia.ok) {
    Add-Failure $failures "nvidia-smi did not report a usable NVIDIA GPU: $($nvidia.error)"
}

$artifacts = [pscustomobject]@{
    exe = Get-FileArtifact -Path $ExePath
    backend = Get-FileArtifact -Path $backendDll
    cudart = Get-FileArtifact -Path $cudartDll
}
foreach ($artifactName in @("exe", "backend", "cudart")) {
    if (-not [bool]$artifacts.$artifactName.exists) {
        Add-Failure $failures "Missing release artifact '$artifactName': $($artifacts.$artifactName.path)"
    }
}

$playback = $null
$playbackChild = $null
if (-not $SkipPlayback -and $failures.Count -eq 0) {
    $playbackPacket = Join-Path $playbackRoot "p3-local-evidence.zip"
    $playbackArgs = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $smokeScript,
        "-RepoRoot", $root,
        "-ExpectedHostName", $env:COMPUTERNAME,
        "-RequiredGpuNamePattern", $RequiredGpuNamePattern,
        "-ClipPaths", $clipFullPath,
        "-Receipt", $receiptFullPath,
        "-OutputRoot", $playbackRoot,
        "-Seconds", ([string]$Seconds),
        "-SettleMs", ([string]$SettleMs),
        "-QualityMode", $QualityMode,
        "-ScaleFactor", $ScaleFactor,
        "-ValidationSampleEvery", ([string]$ValidationSampleEvery),
        "-SkipBuild",
        "-EvidencePacketPath", $playbackPacket
    )
    if (-not [string]::IsNullOrWhiteSpace($GpuPlaybackReconBackend)) {
        $playbackArgs += @("-GpuPlaybackReconBackend", $GpuPlaybackReconBackend)
    }
    if ($DryRun) {
        $playbackArgs += "-DryRun"
    }

    $playbackChild = Invoke-ChildPowerShell -Arguments $playbackArgs
    $playbackSummaryPath = Join-Path $playbackRoot "latest.json"
    if (Test-Path -LiteralPath $playbackSummaryPath) {
        $playback = Get-Content -LiteralPath $playbackSummaryPath -Raw | ConvertFrom-Json -Depth 100
    }
    else {
        Add-Failure $failures "Playback validation did not write $playbackSummaryPath."
    }
    if ($playbackChild.exitCode -ne 0) {
        Add-Failure $failures "Playback validation exited with code $($playbackChild.exitCode)."
    }
    elseif ($playback -and $playback.status -notin @("success", "planned")) {
        Add-Failure $failures "Playback validation status was '$($playback.status)'."
    }
    elseif (!$DryRun -and $playback -and -not [bool]$playback.proof.correctnessValidated) {
        Add-Failure $failures "Playback validation did not set proof.correctnessValidated=true."
    }
}

$cdng = $null
$cdngChild = $null
if (-not $SkipCdng -and $failures.Count -eq 0) {
    $clipStem = [System.IO.Path]::GetFileNameWithoutExtension($clipFullPath)
    $caseIndex = 0
    $cases = @($codecs | ForEach-Object {
        $caseIndex++
        [pscustomobject]@{
            name = "$clipStem-$($_)-local-gpu-export"
            clipPath = $clipFullPath
            receipt = $receiptFullPath
            cdngCodec = $_
            maxFrames = $MaxFrames
            repeats = $Repeats
        }
    })
    Write-JsonFile -Value ([pscustomobject]@{ cases = $cases }) -Path $casesPath

    $cdngArgs = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $matrixScript,
        "-RepoRoot", $root,
        "-ExePath", $ExePath,
        "-CasesPath", $casesPath,
        "-OutputDir", $cdngRoot,
        "-BuildId", ("local-cuda-playback-dng-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")),
        "-AlternateRunOrder",
        "-CandidateEnableGpuExport",
        "-CandidateGpuExportDll", $backendDll,
        "-RequireBaselineNoGpuExportAttempt",
        "-RequireCandidateGpuExportAttempt",
        "-RequireCandidateGpuExportReplacement",
        "-RequireDngHashMatch"
    )
    if ($TrustedGpuExport) {
        $cdngArgs += @("-CandidateGpuExportTrusted", "-RequireCandidateGpuExportTrusted")
    }
    if (-not [string]::IsNullOrWhiteSpace($GpuExportBackend)) {
        $cdngArgs += @("-CandidateGpuExportBackend", $GpuExportBackend)
    }
    if ($DryRun) {
        $cdngArgs += "-DryRun"
    }

    $cdngChild = Invoke-ChildPowerShell -Arguments $cdngArgs
    $matrixSummaryPath = Join-Path $cdngRoot "matrix-summary.json"
    if (Test-Path -LiteralPath $matrixSummaryPath) {
        $cdng = Get-Content -LiteralPath $matrixSummaryPath -Raw | ConvertFrom-Json -Depth 100
    }
    elseif ($DryRun) {
        [void]$warnings.Add("Dry run: CDNG matrix printed a plan and did not write $matrixSummaryPath.")
    }
    else {
        Add-Failure $failures "CDNG matrix did not write $matrixSummaryPath."
    }
    if ($cdngChild.exitCode -ne 0) {
        Add-Failure $failures "CDNG matrix exited with code $($cdngChild.exitCode)."
    }
    elseif ($cdng -and $cdng.verdict -notin @("PASS", "DRY_RUN")) {
        Add-Failure $failures "CDNG matrix verdict was '$($cdng.verdict)'."
    }
    elseif (!$DryRun -and $cdng -and $cdng.dngHash -and $cdng.dngHash.verdict -ne "PASS") {
        Add-Failure $failures "CDNG DNG hash verdict was '$($cdng.dngHash.verdict)'."
    }
}

$status = if ($failures.Count -eq 0) {
    if ($DryRun) { "planned" } else { "success" }
}
else {
    "failed"
}

$summary = [pscustomobject]@{
    schema = "mlvapp-local-cuda-playback-dng-smoke.v1"
    capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    status = $status
    dryRun = [bool]$DryRun
    repoRoot = $root
    host = [pscustomobject]@{
        name = $env:COMPUTERNAME
        requiredGpuNamePattern = $RequiredGpuNamePattern
        nvidiaSmi = $nvidia
    }
    artifacts = $artifacts
    inputs = [pscustomobject]@{
        clipPath = $clipFullPath
        receipt = $receiptFullPath
        seconds = $Seconds
        settleMs = $SettleMs
        validationSampleEvery = $ValidationSampleEvery
        qualityMode = $QualityMode
        scaleFactor = $ScaleFactor
        cdngCodecs = @($codecs)
        maxFrames = $MaxFrames
        repeats = $Repeats
        trustedGpuExport = [bool]$TrustedGpuExport
        gpuPlaybackReconBackend = $GpuPlaybackReconBackend
        gpuExportBackend = $GpuExportBackend
    }
    outputs = [pscustomobject]@{
        runRoot = $runRoot
        summary = $summaryPath
        latest = $latestPath
        playbackRoot = if ($SkipPlayback) { $null } else { $playbackRoot }
        cdngRoot = if ($SkipCdng) { $null } else { $cdngRoot }
        cdngCases = if ($SkipCdng) { $null } else { $casesPath }
    }
    proof = [pscustomobject]@{
        playback = Get-PlaybackProofSummary -Summary $playback
        cdng = Get-CdngProofSummary -Summary $cdng
    }
    childProcesses = [pscustomobject]@{
        playback = if ($playbackChild) {
            [pscustomobject]@{
                exitCode = $playbackChild.exitCode
                outputTail = @($playbackChild.output | Select-Object -Last 120)
            }
        } else { $null }
        cdng = if ($cdngChild) {
            [pscustomobject]@{
                exitCode = $cdngChild.exitCode
                outputTail = @($cdngChild.output | Select-Object -Last 120)
            }
        } else { $null }
    }
    warnings = @($warnings)
    failures = @($failures)
}

Write-JsonFile -Value $summary -Path $summaryPath
Write-JsonFile -Value $summary -Path $latestPath
$summary | ConvertTo-Json -Depth 12

if ($failures.Count -gt 0) {
    exit 1
}
