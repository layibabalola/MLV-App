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
    [int]$PlaybackAbSeconds = 30,
    [int]$PlaybackAbSettleMs = 2500,
    [int]$MaxFrames = 4,
    [int]$Repeats = 1,
    [string[]]$CdngCodecs = @("uncompressed", "lossless"),
    [string]$RequiredGpuNamePattern = "NVIDIA|GeForce|RTX|GTX|Quadro|Laptop",
    [string]$GpuPlaybackReconBackend = "",
    [string]$GpuExportBackend = "",
    [bool]$TrustedGpuExport = $true,
    [switch]$NoHighPerformancePreference,
    [switch]$SkipPlayback,
    [switch]$SkipPlaybackAb,
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
if ($PlaybackAbSeconds -lt 1) {
    throw "-PlaybackAbSeconds must be >= 1."
}
if ($PlaybackAbSettleMs -lt 0) {
    throw "-PlaybackAbSettleMs must be >= 0."
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
if ($SkipPlayback -and $SkipPlaybackAb -and $SkipCdng) {
    throw "At least one of playback validation, playback A/B, or CDNG validation must run."
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

function Get-GpuPreferenceValue {
    param([Parameter(Mandatory = $true)][string]$Executable)
    $prefKey = "HKCU:\Software\Microsoft\DirectX\UserGpuPreferences"
    if (!(Test-Path -LiteralPath $prefKey)) {
        return $null
    }
    $item = Get-ItemProperty -LiteralPath $prefKey -Name $Executable -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        return $null
    }
    return [string]$item.$Executable
}

function Set-GpuPreferenceHighPerformance {
    param([Parameter(Mandatory = $true)][string]$Executable)
    $prefKey = "HKCU:\Software\Microsoft\DirectX\UserGpuPreferences"
    New-Item -Path $prefKey -Force | Out-Null
    New-ItemProperty `
        -Path $prefKey `
        -Name $Executable `
        -Value "GpuPreference=2;" `
        -PropertyType String `
        -Force | Out-Null
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

function Resolve-NvidiaSmiCommand {
    $cmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        "C:\Windows\System32\nvidia-smi.exe",
        (Join-Path $env:ProgramFiles "NVIDIA Corporation\NVSMI\nvidia-smi.exe"),
        (Join-Path $env:ProgramW6432 "NVIDIA Corporation\NVSMI\nvidia-smi.exe")
    )
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $null
}

function Get-NvidiaSmiRows {
    $cmd = Resolve-NvidiaSmiCommand
    if (-not $cmd) {
        return [pscustomobject]@{
            ok = $false
            found = $false
            path = $null
            rows = @()
            error = "nvidia-smi not found"
        }
    }

    try {
        $rows = @(& $cmd --query-gpu=name,driver_version,compute_cap,memory.total --format=csv,noheader,nounits 2>&1 |
            ForEach-Object { [string]$_ })
        if ($LASTEXITCODE -ne 0) {
            return [pscustomobject]@{
                ok = $false
                found = $true
                path = $cmd
                rows = $rows
                error = "nvidia-smi exited with code $LASTEXITCODE."
            }
        }
        return [pscustomobject]@{
            ok = ($rows.Count -gt 0)
            found = $true
            path = $cmd
            rows = $rows
            error = if ($rows.Count -gt 0) { $null } else { "nvidia-smi returned no GPU rows." }
        }
    }
    catch {
        [pscustomobject]@{
            ok = $false
            found = $true
            path = $cmd
            rows = @()
            error = $_.Exception.Message
        }
    }
}

function Get-RepoBuildSha {
    param([string]$RepoRoot)
    try {
        $sha = (& git -C $RepoRoot rev-parse --verify HEAD 2>$null)
        if (-not [string]::IsNullOrWhiteSpace($sha)) {
            return [string]$sha
        }
    }
    catch {
    }
    $null
}

function Get-FirstNvidiaSmiFingerprintRow {
    param([object]$NvidiaSmi)

    if ($null -eq $NvidiaSmi -or -not [bool]$NvidiaSmi.ok) {
        return $null
    }
    $row = @($NvidiaSmi.rows | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
    if ($row.Count -eq 0) {
        return $null
    }
    $parts = @([string]$row[0] -split "," | ForEach-Object { ([string]$_).Trim() })
    if ($parts.Count -lt 4) {
        return $null
    }
    $vram = $null
    $parsedVram = 0
    if ([int]::TryParse($parts[3], [ref]$parsedVram)) {
        $vram = $parsedVram
    }
    [pscustomobject]@{
        gpu_name = $parts[0]
        gpu_driver_version = $parts[1]
        gpu_compute_capability = $parts[2]
        gpu_vram_total_mb = $vram
    }
}

function Get-LocalMachineFingerprint {
    param(
        [string]$RepoRoot,
        [object]$NvidiaSmi
    )

    $gpu = Get-FirstNvidiaSmiFingerprintRow -NvidiaSmi $NvidiaSmi
    $cpu = $null
    $computer = $null
    $os = $null
    try { $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 } catch {}
    try { $computer = Get-CimInstance Win32_ComputerSystem | Select-Object -First 1 } catch {}
    try { $os = Get-CimInstance Win32_OperatingSystem | Select-Object -First 1 } catch {}

    [pscustomobject]@{
        schema = "machine-fingerprint.v1"
        build_sha = Get-RepoBuildSha -RepoRoot $RepoRoot
        gpu_name = if ($gpu) { $gpu.gpu_name } else { $null }
        gpu_driver_version = if ($gpu) { $gpu.gpu_driver_version } else { $null }
        gpu_compute_capability = if ($gpu) { $gpu.gpu_compute_capability } else { $null }
        gpu_vram_total_mb = if ($gpu) { $gpu.gpu_vram_total_mb } else { $null }
        cpu_model = if ($cpu) { [string]$cpu.Name } else { $null }
        cpu_cores = if ($cpu) { [int]$cpu.NumberOfCores } else { $null }
        cpu_threads = if ($cpu) { [int]$cpu.NumberOfLogicalProcessors } else { $null }
        ram_total_mb = if ($computer) { [int64]([math]::Round([double]$computer.TotalPhysicalMemory / 1MB)) } else { $null }
        os_version = if ($os) { "$($os.Caption) $($os.Version)" } else { $null }
    }
}

function Convert-ToInt64 {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return 0L
    }
    [long]$Value
}

function Convert-ToNullableDouble {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $null
    }

    $parsed = 0.0
    if ([double]::TryParse(
        [string]$Value,
        [System.Globalization.NumberStyles]::Float,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed)) {
        return $parsed
    }
    $null
}

function Get-AverageOrNull {
    param(
        [object[]]$Rows,
        [Parameter(Mandatory = $true)][string]$PropertyName
    )

    $values = @($Rows | ForEach-Object {
        Convert-ToNullableDouble $_.$PropertyName
    } | Where-Object { $null -ne $_ })
    if ($values.Count -eq 0) {
        return $null
    }

    [Math]::Round((($values | Measure-Object -Average).Average), 3)
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
    $speedRuns = @()
    foreach ($run in $runs) {
        $candidateAttempted += Convert-ToInt64 $run.candidateGpuExportAttemptedFrames
        $candidateReplaced += Convert-ToInt64 $run.candidateGpuExportReplacedFrames
        $candidateTrusted += Convert-ToInt64 $run.candidateGpuExportTrustedFrames
        $candidateFrames += Convert-ToInt64 $run.candidateFrameCount
        $baselineAttempted += Convert-ToInt64 $run.baselineGpuExportAttemptedFrames
        $speedRuns += [pscustomobject]@{
            name = $run.name
            cdngCodec = $run.cdngCodec
            repeat = $run.repeat
            runOrder = $run.runOrder
            verdict = $run.verdict
            baselineElapsedMs = Convert-ToNullableDouble $run.baselineElapsedMs
            candidateElapsedMs = Convert-ToNullableDouble $run.candidateElapsedMs
            elapsedDeltaMs = Convert-ToNullableDouble $run.elapsedDeltaMs
            elapsedDeltaPercent = Convert-ToNullableDouble $run.elapsedDeltaPercent
            frameTotalAvgDeltaMs = Convert-ToNullableDouble $run.frameTotalAvgDeltaMs
            frameTotalP95DeltaMs = Convert-ToNullableDouble $run.frameTotalP95DeltaMs
            llrawprocTotalAvgDeltaMs = Convert-ToNullableDouble $run.llrawprocTotalAvgDeltaMs
            llrawprocDualIsoAvgDeltaMs = Convert-ToNullableDouble $run.llrawprocDualIsoAvgDeltaMs
            dngCompressAvgDeltaMs = Convert-ToNullableDouble $run.dngCompressAvgDeltaMs
            dngCompressOutputMiBPerSecondDelta = Convert-ToNullableDouble $run.dngCompressOutputMiBPerSecondDelta
        }
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
        speed = [pscustomobject]@{
            runCount = $speedRuns.Count
            avgBaselineElapsedMs = Get-AverageOrNull -Rows $speedRuns -PropertyName "baselineElapsedMs"
            avgCandidateElapsedMs = Get-AverageOrNull -Rows $speedRuns -PropertyName "candidateElapsedMs"
            avgElapsedDeltaMs = Get-AverageOrNull -Rows $speedRuns -PropertyName "elapsedDeltaMs"
            avgElapsedDeltaPercent = Get-AverageOrNull -Rows $speedRuns -PropertyName "elapsedDeltaPercent"
            avgFrameTotalDeltaMs = Get-AverageOrNull -Rows $speedRuns -PropertyName "frameTotalAvgDeltaMs"
            avgLlrawprocTotalDeltaMs = Get-AverageOrNull -Rows $speedRuns -PropertyName "llrawprocTotalAvgDeltaMs"
            avgLlrawprocDualIsoDeltaMs = Get-AverageOrNull -Rows $speedRuns -PropertyName "llrawprocDualIsoAvgDeltaMs"
            avgDngCompressDeltaMs = Get-AverageOrNull -Rows $speedRuns -PropertyName "dngCompressAvgDeltaMs"
            runs = @($speedRuns)
            proofBoundary = "Elapsed deltas are local to the host, clip, codec, max-frame count, release/backend hashes, and run order captured by the child CDNG matrix."
        }
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

function Get-PlaybackAbProofSummary {
    param([AllowNull()]$Summary)

    if ($null -eq $Summary) {
        return $null
    }

    [pscustomobject]@{
        status = $Summary.status
        seconds = $Summary.seconds
        settleMs = $Summary.settleMs
        gpuPreference = $Summary.gpuPreference
        nvidiaSmi = $Summary.nvidiaSmi
        baseline = $Summary.baseline
        candidate = $Summary.candidate
        compare = $Summary.compare
        proofFailures = @($Summary.proofFailures)
        proofBoundary = $Summary.proofBoundary
        summaryPath = $Summary.outputs.summary
    }
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$smokeScript = Join-Path $root "tools\profiling\run-ultramagnus-p3-validation.ps1"
$playbackAbScript = Join-Path $root "tools\profiling\run-release-cuda-playback-ab.ps1"
$matrixScript = Join-Path $root "tools\profiling\run-release-cdng-export-profile-matrix.ps1"
if (!(Test-Path -LiteralPath $smokeScript)) {
    throw "P3 validation script not found: $smokeScript"
}
if (!(Test-Path -LiteralPath $playbackAbScript)) {
    throw "Playback A/B script not found: $playbackAbScript"
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
$playbackAbRoot = Join-Path $runRoot "playback-ab"
$cdngRoot = Join-Path $runRoot "cdng"
$summaryPath = Join-Path $runRoot "summary.json"
$latestPath = Join-Path $root ".claude-state\profiling\local-cuda-playback-dng-smoke-latest.json"
$casesPath = Join-Path $runRoot "cdng-cases.json"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$nvidia = if ($DryRun) {
    [pscustomobject]@{
        ok = $null
        found = $null
        path = $null
        rows = @()
        error = "dry run: nvidia-smi not executed"
    }
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

$gpuPreferenceBefore = $null
$gpuPreferenceAfter = $null
$expectedGpuPreference = "GpuPreference=2;"
if ([bool]$artifacts.exe.exists) {
    $gpuPreferenceBefore = Get-GpuPreferenceValue -Executable $artifacts.exe.path
    $gpuPreferenceAfter = $gpuPreferenceBefore
    if (!$DryRun -and !$NoHighPerformancePreference) {
        Set-GpuPreferenceHighPerformance -Executable $artifacts.exe.path
        $gpuPreferenceAfter = Get-GpuPreferenceValue -Executable $artifacts.exe.path
        if ($gpuPreferenceAfter -ne $expectedGpuPreference) {
            Add-Failure $failures "Windows per-app GPU preference for '$($artifacts.exe.path)' was '$gpuPreferenceAfter'; expected '$expectedGpuPreference'."
        }
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

$playbackAb = $null
$playbackAbChild = $null
if (-not $SkipPlaybackAb -and $failures.Count -eq 0) {
    $playbackReconBackendForAb =
        if ([string]::IsNullOrWhiteSpace($GpuPlaybackReconBackend)) { "cuda" }
        else { $GpuPlaybackReconBackend }
    $playbackAbArgs = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $playbackAbScript,
        "-RepoRoot", $root,
        "-ExePath", $ExePath,
        "-ClipPath", $clipFullPath,
        "-Receipt", $receiptFullPath,
        "-BackendDll", $backendDll,
        "-OutputRoot", $playbackAbRoot,
        "-Seconds", ([string]$PlaybackAbSeconds),
        "-SettleMs", ([string]$PlaybackAbSettleMs),
        "-QualityMode", $QualityMode,
        "-ScaleFactor", $ScaleFactor,
        "-ValidationSampleEvery", ([string]$ValidationSampleEvery),
        "-GpuPlaybackReconBackend", $playbackReconBackendForAb,
        "-FailOnColorArtifact",
        "-RequireCandidateGlParity"
    )
    if ($NoHighPerformancePreference) {
        $playbackAbArgs += "-NoHighPerformancePreference"
    }
    if ($DryRun) {
        $playbackAbArgs += "-DryRun"
    }

    $playbackAbChild = Invoke-ChildPowerShell -Arguments $playbackAbArgs
    $playbackAbSummaryPath = Join-Path $playbackAbRoot "summary.json"
    if (Test-Path -LiteralPath $playbackAbSummaryPath) {
        $playbackAb = Get-Content -LiteralPath $playbackAbSummaryPath -Raw | ConvertFrom-Json -Depth 100
    }
    else {
        Add-Failure $failures "Playback A/B did not write $playbackAbSummaryPath."
    }
    if ($playbackAbChild.exitCode -ne 0) {
        Add-Failure $failures "Playback A/B exited with code $($playbackAbChild.exitCode)."
    }
    elseif ($playbackAb -and $playbackAb.status -notin @("success", "planned")) {
        Add-Failure $failures "Playback A/B status was '$($playbackAb.status)'."
    }
    elseif (!$DryRun -and $playbackAb -and @($playbackAb.proofFailures).Count -gt 0) {
        Add-Failure $failures "Playback A/B proof failures: $(@($playbackAb.proofFailures) -join '; ')"
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

$machineFingerprint = Get-LocalMachineFingerprint -RepoRoot $root -NvidiaSmi $nvidia

$summary = [pscustomobject]@{
    schema = "mlvapp-local-cuda-playback-dng-smoke.v1"
    capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    status = $status
    dryRun = [bool]$DryRun
    repoRoot = $root
    machineFingerprint = $machineFingerprint
    host = [pscustomobject]@{
        name = $env:COMPUTERNAME
        requiredGpuNamePattern = $RequiredGpuNamePattern
        machineFingerprint = $machineFingerprint
        nvidiaSmi = $nvidia
        gpuPreference = [pscustomobject]@{
            attemptedHighPerformance = [bool](!$NoHighPerformancePreference)
            mutated = [bool](!$DryRun -and !$NoHighPerformancePreference -and [bool]$artifacts.exe.exists)
            before = $gpuPreferenceBefore
            after = $gpuPreferenceAfter
            expected = $expectedGpuPreference
        }
    }
    artifacts = $artifacts
    inputs = [pscustomobject]@{
        clipPath = $clipFullPath
        receipt = $receiptFullPath
        seconds = $Seconds
        settleMs = $SettleMs
        playbackAbSeconds = $PlaybackAbSeconds
        playbackAbSettleMs = $PlaybackAbSettleMs
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
        playbackAbRoot = if ($SkipPlaybackAb) { $null } else { $playbackAbRoot }
        cdngRoot = if ($SkipCdng) { $null } else { $cdngRoot }
        cdngCases = if ($SkipCdng) { $null } else { $casesPath }
    }
    proof = [pscustomobject]@{
        playback = Get-PlaybackProofSummary -Summary $playback
        playbackAb = Get-PlaybackAbProofSummary -Summary $playbackAb
        cdng = Get-CdngProofSummary -Summary $cdng
    }
    childProcesses = [pscustomobject]@{
        playback = if ($playbackChild) {
            [pscustomobject]@{
                exitCode = $playbackChild.exitCode
                outputTail = @($playbackChild.output | Select-Object -Last 120)
            }
        } else { $null }
        playbackAb = if ($playbackAbChild) {
            [pscustomobject]@{
                exitCode = $playbackAbChild.exitCode
                outputTail = @($playbackAbChild.output | Select-Object -Last 120)
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
