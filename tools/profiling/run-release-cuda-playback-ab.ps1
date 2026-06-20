param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "C:\temp\MLV\M16-1327.MLV",
    [string]$Receipt = "receipts\FastProxy.marxml",
    [string]$BackendDll = "",
    [string]$OutputRoot = "",
    [int]$Seconds = 30,
    [int]$SettleMs = 2500,
    [int]$StartFrame = 0,
    [string]$Threads = "auto",
    [string]$QualityMode = "4",
    [string]$ScaleFactor = "1",
    [int]$ValidationSampleEvery = 10,
    [string]$GpuPlaybackReconBackend = "cuda",
    [switch]$CandidateFirst,
    [switch]$NoScreenshot,
    [switch]$FailOnColorArtifact,
    [switch]$RequireCandidateImprovesPresentedFps,
    [switch]$RequireCandidateGlParity,
    [switch]$NoHighPerformancePreference,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "cuda-backend-architecture.ps1")

if ($Seconds -lt 1) {
    throw "-Seconds must be >= 1."
}
if ($SettleMs -lt 0) {
    throw "-SettleMs must be >= 0."
}
if ($ValidationSampleEvery -lt 1) {
    throw "-ValidationSampleEvery must be >= 1."
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

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Value | ConvertTo-Json -Depth 64 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-FileArtifact {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
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

function Convert-ToNullableDouble {
    param([object]$Value)
    if ($null -eq $Value) {
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

function Convert-ToNullableInt64 {
    param([object]$Value)
    if ($null -eq $Value) {
        return $null
    }

    $parsed = 0L
    if ([long]::TryParse(
        [string]$Value,
        [System.Globalization.NumberStyles]::Integer,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed)) {
        return $parsed
    }
    $null
}

function Get-NestedValue {
    param(
        [object]$Object,
        [string]$Path
    )

    $current = $Object
    foreach ($part in $Path.Split(".")) {
        if ($null -eq $current) {
            return $null
        }
        $property = $current.PSObject.Properties[$part]
        if (-not $property) {
            return $null
        }
        $current = $property.Value
    }
    $current
}

function New-MetricDelta {
    param(
        [object]$BaselineValue,
        [object]$CandidateValue
    )

    $baseline = Convert-ToNullableDouble $BaselineValue
    $candidate = Convert-ToNullableDouble $CandidateValue
    $delta = $null
    $deltaPercent = $null
    if ($null -ne $baseline -and $null -ne $candidate) {
        $delta = [Math]::Round($candidate - $baseline, 6)
        if ([Math]::Abs($baseline) -gt 0.000001) {
            $deltaPercent = [Math]::Round((($candidate - $baseline) / $baseline) * 100.0, 3)
        }
    }

    [pscustomobject]@{
        baseline = $baseline
        candidate = $candidate
        delta = $delta
        deltaPercent = $deltaPercent
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

function Get-NvidiaSmiSnapshot {
    $cmd = Resolve-NvidiaSmiCommand
    if (-not $cmd) {
        return [pscustomobject]@{
            found = $false
            path = $null
            output = @()
            error = "nvidia-smi not found"
        }
    }

    try {
        $output = & $cmd --query-gpu=name,driver_version,compute_cap,memory.total --format=csv,noheader 2>&1
        return [pscustomobject]@{
            found = $true
            path = $cmd
            output = @($output | ForEach-Object { [string]$_ })
            error = $null
        }
    }
    catch {
        return [pscustomobject]@{
            found = $true
            path = $cmd.Source
            output = @()
            error = $_.Exception.Message
        }
    }
}

function Convert-ComputeCapabilityToCudaToken {
    param([AllowNull()][string]$ComputeCapability)

    if ([string]::IsNullOrWhiteSpace($ComputeCapability)) {
        return $null
    }
    $trimmed = ([string]$ComputeCapability).Trim()
    if ($trimmed -match '^([0-9]+)\.([0-9]+)$') {
        return "sm_$($Matches[1])$($Matches[2])"
    }
    if ($trimmed -match '^([0-9]{2,3})$') {
        return "sm_$trimmed"
    }
    $null
}

function Get-NvidiaSmiComputeCapability {
    param([AllowNull()]$NvidiaSmi)

    if ($null -eq $NvidiaSmi -or -not [bool]$NvidiaSmi.found) {
        return $null
    }
    $row = @($NvidiaSmi.output | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
    if ($row.Count -eq 0) {
        return $null
    }
    $parts = @([string]$row[0] -split "," | ForEach-Object { ([string]$_).Trim() })
    if ($parts.Count -lt 3) {
        return $null
    }
    [string]$parts[2]
}

function Get-FirstNvidiaSmiFingerprintRow {
    param([AllowNull()]$NvidiaSmi)

    if ($null -eq $NvidiaSmi -or -not [bool]$NvidiaSmi.found) {
        return $null
    }
    $row = @($NvidiaSmi.output | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
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
    elseif ($parts[3] -match '([0-9]+)') {
        $parsedVram = [int]$Matches[1]
        $vram = $parsedVram
    }
    [pscustomobject]@{
        gpu_name = $parts[0]
        gpu_driver_version = $parts[1]
        gpu_compute_capability = $parts[2]
        gpu_vram_total_mb = $vram
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

function Get-LocalMachineFingerprint {
    param(
        [string]$RepoRoot,
        [AllowNull()]$NvidiaSmi
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

function Test-CudaBackendCompatibility {
    param(
        [AllowNull()]$ArchitectureInfo,
        [AllowNull()]$NvidiaSmi
    )

    $computeCapability = Get-NvidiaSmiComputeCapability -NvidiaSmi $NvidiaSmi
    $smToken = Convert-ComputeCapabilityToCudaToken -ComputeCapability $computeCapability
    $computeToken = if ($smToken) { $smToken -replace '^sm_', 'compute_' } else { $null }
    $tokens = @()
    if ($ArchitectureInfo -and $ArchitectureInfo.tokens) {
        $tokens = @($ArchitectureInfo.tokens | ForEach-Object { [string]$_ })
    }
    $compatible = $null
    $reason = "gpu_compute_capability_unavailable"
    if ($null -eq $ArchitectureInfo -or -not [bool]$ArchitectureInfo.exists) {
        $compatible = $false
        $reason = "backend_missing"
    }
    elseif ($null -eq $smToken) {
        $compatible = $null
        $reason = "gpu_compute_capability_unavailable"
    }
    elseif (-not [bool]$ArchitectureInfo.detectionReliable) {
        $compatible = $false
        $reason = "backend_architecture_detection_unreliable"
    }
    elseif ($tokens.Count -eq 0) {
        $compatible = $false
        $reason = "backend_architecture_metadata_unavailable"
    }
    elseif (($tokens -contains $smToken) -or ($tokens -contains $computeToken)) {
        $compatible = $true
        $reason = "compatible"
    }
    else {
        $compatible = $false
        $reason = "backend_architecture_missing_target"
    }

    [pscustomobject]@{
        schema = "mlvapp.cuda-backend-compatibility.v1"
        compatible = $compatible
        reason = $reason
        gpu_compute_capability = $computeCapability
        required_tokens = @(@($smToken, $computeToken) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
        backend_tokens = @($tokens)
        proofBoundary = "This is a preflight compatibility gate only. Runtime support still requires no-readback playback proof, fallback counts, GL parity, and timing evidence on the host."
    }
}

function Invoke-ChildPowerShell {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = & pwsh.exe @Arguments 2>&1
    [pscustomobject]@{
        exitCode = $LASTEXITCODE
        output = @($output | ForEach-Object { [string]$_ })
    }
}

function Read-SmokeSummary {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$ExitCode
    )

    $exists = Test-Path -LiteralPath $Path -PathType Leaf
    if (-not $exists) {
        return [pscustomobject]@{
            path = $Path
            exists = $false
            exitCode = $ExitCode
            validationOk = $false
        }
    }

    $json = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -Depth 100
    $summary = Get-NestedValue $json "log.summary"
    $glProof = Get-NestedValue $json "visualQuality.glOutputProof"
    if ($null -eq $glProof) {
        $glProof = Get-NestedValue $json "validation.glOutputProof"
    }
    $validationOk = [bool](Get-NestedValue $json "validation.ok")
    $presentedFrames = Convert-ToNullableInt64 (Get-NestedValue $summary "presented_frames")
    if ($null -eq $presentedFrames) {
        $presentedFrames = Convert-ToNullableInt64 (Get-NestedValue $json "validation.presentedFrames")
    }

    [pscustomobject]@{
        path = (Resolve-Path -LiteralPath $Path).Path
        exists = $true
        exitCode = $ExitCode
        validationOk = $validationOk
        runMetadata = Get-NestedValue $json "log.runMetadata"
        validationFailures = @(Get-NestedValue $json "validation.failures")
        validationWarnings = @(Get-NestedValue $json "validation.warnings")
        presentedFrames = $presentedFrames
        presentedFps = Convert-ToNullableDouble (Get-NestedValue $summary "presented_fps")
        timelineFps = Convert-ToNullableDouble (Get-NestedValue $summary "timeline_fps")
        guiStatusText = Get-NestedValue $json "playbackFps.guiStatusText"
        guiStatusFps = Convert-ToNullableDouble (Get-NestedValue $json "playbackFps.guiStatusValue")
        visibleGuiStatusText = Get-NestedValue $json "playbackFps.visibleBottomLeftGuiStatusText"
        visibleGuiFps = Convert-ToNullableDouble (Get-NestedValue $json "playbackFps.visibleBottomLeftGuiFps")
        avgPresentIntervalMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_present_interval_ms")
        avgRenderTotalMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_render_total_ms")
        avgRenderWorkMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_render_work_ms")
        avgQueueWaitMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_queue_wait_ms")
        avgLlrawprocMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_llrawproc_ms")
        avgProcessed8Ms = Convert-ToNullableDouble (Get-NestedValue $summary "avg_processed8_ms")
        avgDrawTotalMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_draw_total_ms")
        avgPrepWorkerBuildMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_playback_prep_worker_build_ms")
        avgPrepWorkerTotalMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_playback_prep_worker_total_ms")
        avgPrepBeforeFinishMs = Convert-ToNullableDouble (Get-NestedValue $summary "avg_playback_prep_total_before_finish_ms")
        scaleRequestLast = Convert-ToNullableInt64 (Get-NestedValue $summary "scale_request_last")
        qualityMode = Convert-ToNullableInt64 (Get-NestedValue $summary "quality_mode")
        glProof = [pscustomobject]@{
            requested = [bool](Get-NestedValue $glProof "requested")
            activeProbeCount = Convert-ToNullableInt64 (Get-NestedValue $glProof "activeProbeCount")
            parityCheckedCount = Convert-ToNullableInt64 (Get-NestedValue $glProof "parityCheckedCount")
            parityMatchCount = Convert-ToNullableInt64 (Get-NestedValue $glProof "parityMatchCount")
            mismatchCountTotal = Convert-ToNullableInt64 (Get-NestedValue $glProof "mismatchCountTotal")
            distinctTextureHashes = Convert-ToNullableInt64 (Get-NestedValue $glProof "distinctTextureHashes")
            renderers = @(Get-NestedValue $glProof "rendererDescriptions")
            backendDescriptions = @(Get-NestedValue $glProof "backendDescriptions")
            screenshotMethod = Get-NestedValue $glProof "screenshotMethod"
        }
        autoCapability = [pscustomobject]@{
            validatedNoReadbackObserved = Get-NestedValue $json "visualQuality.autoDecision.validatedNoReadbackCapabilityObservedBool"
            validatedNoReadbackDemoted = Get-NestedValue $json "visualQuality.autoDecision.validatedNoReadbackCapabilityDemotedLastBool"
            capabilityConsistent = Get-NestedValue $json "visualQuality.autoDecision.capabilityConsistent"
        }
    }
}

function ConvertTo-PowerShellSingleQuotedLiteral {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value) {
        return "''"
    }
    "'" + ([string]$Value).Replace("'", "''") + "'"
}

function Write-SmokeInvokeScript {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$SmokeScript,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string]$Clip,
        [Parameter(Mandatory = $true)][string]$Output,
        [Parameter(Mandatory = $true)][string]$ScreenshotDir,
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$Quality,
        [Parameter(Mandatory = $true)][string]$Scale,
        [Parameter(Mandatory = $true)][string]$ThreadSetting,
        [Parameter(Mandatory = $true)][int]$SecondsValue,
        [Parameter(Mandatory = $true)][int]$SettleMsValue,
        [Parameter(Mandatory = $true)][int]$StartFrameValue,
        [Parameter(Mandatory = $true)][string]$GpuPreviewProcessing,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$GpuBackend,
        [Parameter(Mandatory = $true)][string[]]$ExtraEnvironment,
        [Parameter(Mandatory = $true)][bool]$CaptureScreenshot,
        [Parameter(Mandatory = $true)][bool]$FailOnColorArtifactValue,
        [Parameter(Mandatory = $true)][bool]$DryRunValue
    )

    $expectedScale = -1
    [void][int]::TryParse($Scale, [ref]$expectedScale)
    $expectedQuality = -1
    [void][int]::TryParse($Quality, [ref]$expectedQuality)

    $envLiteral = if ($ExtraEnvironment.Count -gt 0) {
        "@(" + (($ExtraEnvironment | ForEach-Object {
            ConvertTo-PowerShellSingleQuotedLiteral $_
        }) -join ", ") + ")"
    }
    else {
        "@()"
    }
    $gpuBackendLiteral = ConvertTo-PowerShellSingleQuotedLiteral $GpuBackend

    $scriptText = @"
`$ErrorActionPreference = 'Stop'
`$envList = $envLiteral
`$smokeParams = @{
    RepoRoot = $(ConvertTo-PowerShellSingleQuotedLiteral $RepoRoot)
    ExePath = $(ConvertTo-PowerShellSingleQuotedLiteral $Exe)
    ClipPath = $(ConvertTo-PowerShellSingleQuotedLiteral $Clip)
    Output = $(ConvertTo-PowerShellSingleQuotedLiteral $Output)
    Receipt = $(ConvertTo-PowerShellSingleQuotedLiteral $ReceiptPath)
    Seconds = $SecondsValue
    SettleMs = $SettleMsValue
    StartFrame = $StartFrameValue
    Threads = $(ConvertTo-PowerShellSingleQuotedLiteral $ThreadSetting)
    Scope = 'none'
    PlaybackDebayer = 'amaze'
    PlaybackProcessing = 'subset'
    GpuPreviewProcessing = $(ConvertTo-PowerShellSingleQuotedLiteral $GpuPreviewProcessing)
    ScaleFactor = $(ConvertTo-PowerShellSingleQuotedLiteral $Scale)
    QualityMode = $(ConvertTo-PowerShellSingleQuotedLiteral $Quality)
    ExpectedScaleRequest = $expectedScale
    ExpectedVisualScaleRequest = $expectedScale
    ExpectedQualityMode = $expectedQuality
    RequireLookAssist = `$false
    DisableLookAssist = `$true
    EnablePhase3QualityModes = `$true
    PreserveExperimentalEnvironment = `$true
    FrameTelemetry = `$true
    DetectPlaybackArtifacts = `$true
    ExtraEnvironment = `$envList
}
if (-not [string]::IsNullOrWhiteSpace($gpuBackendLiteral)) {
    `$smokeParams['GpuPlaybackReconBackend'] = $gpuBackendLiteral
}
if (`$$CaptureScreenshot) {
    `$smokeParams['CaptureScreenshot'] = `$true
    `$smokeParams['ScreenshotOutputDir'] = $(ConvertTo-PowerShellSingleQuotedLiteral $ScreenshotDir)
}
if (`$$FailOnColorArtifactValue) {
    `$smokeParams['FailOnColorArtifact'] = `$true
}
if (`$$DryRunValue) {
    `$smokeParams['DryRun'] = `$true
}
& $(ConvertTo-PowerShellSingleQuotedLiteral $SmokeScript) @smokeParams
exit `$LASTEXITCODE
"@
    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $scriptText | Set-Content -LiteralPath $Path -Encoding UTF8

    [pscustomobject]@{
        invokeScript = $Path
        output = $Output
        screenshotDir = if ($CaptureScreenshot) { $ScreenshotDir } else { $null }
        gpuPreviewProcessing = $GpuPreviewProcessing
        gpuPlaybackReconBackend = $GpuBackend
        extraEnvironment = @($ExtraEnvironment)
        dryRun = $DryRunValue
    }
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "platform\qt\build-release\release\MLVApp.exe"
}
else {
    $ExePath = Resolve-RepoPath -Root $root -Path $ExePath
}
$exe = (Resolve-Path -LiteralPath $ExePath).Path
$exeDir = Split-Path -Parent $exe
$gpuPreferenceBefore = Get-GpuPreferenceValue -Executable $exe
$gpuPreferenceAfter = $gpuPreferenceBefore
if (!$DryRun -and !$NoHighPerformancePreference) {
    Set-GpuPreferenceHighPerformance -Executable $exe
    $gpuPreferenceAfter = Get-GpuPreferenceValue -Executable $exe
}

$smokeScript = Join-Path $root "tools\profiling\run-release-gui-smoke.ps1"
if (-not (Test-Path -LiteralPath $smokeScript -PathType Leaf)) {
    throw "Missing GUI smoke wrapper: $smokeScript"
}

if ([string]::IsNullOrWhiteSpace($BackendDll)) {
    $BackendDll = Join-Path $exeDir "igpu_recon_cuda.dll"
}
else {
    $BackendDll = Resolve-RepoPath -Root $root -Path $BackendDll
}
$backend = (Resolve-Path -LiteralPath $BackendDll).Path
$nvidiaSmi = Get-NvidiaSmiSnapshot
$machineFingerprint = Get-LocalMachineFingerprint -RepoRoot $root -NvidiaSmi $nvidiaSmi
$backendArchitecture = Get-CudaBackendArchitectureInfo -Path $backend
$backendCompatibility = Test-CudaBackendCompatibility `
    -ArchitectureInfo $backendArchitecture `
    -NvidiaSmi $nvidiaSmi
$preflightFailures = @()
if (!$DryRun -and [bool]$nvidiaSmi.found -and $backendCompatibility.compatible -ne $true) {
    $preflightFailures += ("CUDA backend architecture is not compatible with this GPU: compute_capability={0}; backend_tokens=[{1}]; reason={2}. Rebuild/deploy igpu_recon_cuda.dll with tools\\gpu\\backend\\build-backend-dll.ps1 -Arch portable before using this run for Dell/UltraMagnus claims." -f `
        $backendCompatibility.gpu_compute_capability,
        (($backendCompatibility.backend_tokens | ForEach-Object { [string]$_ }) -join ","),
        $backendCompatibility.reason)
}

$clip = (Resolve-Path -LiteralPath (Resolve-RepoPath -Root $root -Path $ClipPath)).Path
$receiptPath = (Resolve-Path -LiteralPath (Resolve-RepoPath -Root $root -Path $Receipt)).Path

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputRoot = Join-Path $root ".claude-state\profiling\$stamp-cuda-playback-ab"
}
$runRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
    (Resolve-RepoPath -Root $root -Path $OutputRoot))
$summaryPath = Join-Path $runRoot "summary.json"
$latestPath = Join-Path $root ".claude-state\profiling\cuda-playback-ab-latest.json"
$latestParent = Split-Path -Parent $latestPath
if ($latestParent) {
    New-Item -ItemType Directory -Force -Path $latestParent | Out-Null
}
$baselineOutput = Join-Path $runRoot "baseline-cpu.json"
$candidateOutput = Join-Path $runRoot "candidate-cuda.json"
$baselineScreenshots = Join-Path $runRoot "baseline-screenshots"
$candidateScreenshots = Join-Path $runRoot "candidate-screenshots"
$baselineInvokeScript = Join-Path $runRoot "invoke-baseline.ps1"
$candidateInvokeScript = Join-Path $runRoot "invoke-candidate.ps1"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$captureScreenshot = -not [bool]$NoScreenshot
$baselineEnv = @(
    "QT_OPENGL=desktop"
)
$candidateEnv = @(
    "QT_OPENGL=desktop",
    "MLVAPP_EXPERIMENTAL_GL_VIEWPORT=1",
    "MLVAPP_EXPERIMENTAL_GPU_PROCESSING=1",
    "MLVAPP_GPU_PLAYBACK_RECON=1",
    "MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1",
    "MLVAPP_GPU_PLAYBACK_RECON_DLL=$backend",
    "MLVAPP_GPU_PLAYBACK_RECON_VALIDATE_OUTPUT=1",
    "MLVAPP_GPU_PLAYBACK_RECON_VALIDATE_OUTPUT_SAMPLE_EVERY=$ValidationSampleEvery"
)

$baselineCommand = Write-SmokeInvokeScript `
    -Path $baselineInvokeScript `
    -SmokeScript $smokeScript `
    -RepoRoot $root `
    -Exe $exe `
    -Clip $clip `
    -Output $baselineOutput `
    -ScreenshotDir $baselineScreenshots `
    -ReceiptPath $receiptPath `
    -Quality $QualityMode `
    -Scale $ScaleFactor `
    -ThreadSetting $Threads `
    -SecondsValue $Seconds `
    -SettleMsValue $SettleMs `
    -StartFrameValue $StartFrame `
    -GpuPreviewProcessing "cpu" `
    -GpuBackend "" `
    -ExtraEnvironment $baselineEnv `
    -CaptureScreenshot $captureScreenshot `
    -FailOnColorArtifactValue ([bool]$FailOnColorArtifact) `
    -DryRunValue ([bool]$DryRun)

$candidateCommand = Write-SmokeInvokeScript `
    -Path $candidateInvokeScript `
    -SmokeScript $smokeScript `
    -RepoRoot $root `
    -Exe $exe `
    -Clip $clip `
    -Output $candidateOutput `
    -ScreenshotDir $candidateScreenshots `
    -ReceiptPath $receiptPath `
    -Quality $QualityMode `
    -Scale $ScaleFactor `
    -ThreadSetting $Threads `
    -SecondsValue $Seconds `
    -SettleMsValue $SettleMs `
    -StartFrameValue $StartFrame `
    -GpuPreviewProcessing "gpu" `
    -GpuBackend $GpuPlaybackReconBackend `
    -ExtraEnvironment $candidateEnv `
    -CaptureScreenshot $captureScreenshot `
    -FailOnColorArtifactValue ([bool]$FailOnColorArtifact) `
    -DryRunValue ([bool]$DryRun)

$summary = [ordered]@{
    schema = "mlvapp-cuda-playback-ab.v1"
    status = if ($DryRun) { "planned" } else { "starting" }
    createdAt = (Get-Date).ToString("o")
    host = $env:COMPUTERNAME
    repoRoot = $root
    exe = Get-FileArtifact -Path $exe
    backend = Get-FileArtifact -Path $backend
    backendArchitecture = $backendArchitecture
    backendCompatibility = $backendCompatibility
    cudart = Get-FileArtifact -Path (Join-Path $exeDir "cudart64_12.dll")
    clipPath = $clip
    receiptPath = $receiptPath
    seconds = $Seconds
    settleMs = $SettleMs
    qualityMode = $QualityMode
    scaleFactor = $ScaleFactor
    validationSampleEvery = $ValidationSampleEvery
    runOrder = if ($CandidateFirst) { @("candidate", "baseline") } else { @("baseline", "candidate") }
    machineFingerprint = $machineFingerprint
    nvidiaSmi = $nvidiaSmi
    preflightFailures = @($preflightFailures)
    gpuPreference = [pscustomobject]@{
        attemptedHighPerformance = [bool](!$NoHighPerformancePreference)
        mutated = [bool](!$DryRun -and !$NoHighPerformancePreference)
        before = $gpuPreferenceBefore
        after = $gpuPreferenceAfter
        expected = "GpuPreference=2;"
    }
    commands = [pscustomobject]@{
        baseline = $baselineCommand
        candidate = $candidateCommand
    }
    outputs = [pscustomobject]@{
        baseline = $baselineOutput
        candidate = $candidateOutput
        summary = $summaryPath
    }
    proofBoundary = [pscustomobject]@{
        sameExecutableCpuVsCudaPlayback = $true
        hostLocalOnly = $true
        provesRealtimePlayback = $false
        provesDellSupport = $false
        notes = @(
            "This wrapper compares CPU baseline and scoped CUDA no-readback candidate on the same release executable.",
            "On Windows hybrid-GPU laptops, the wrapper sets the per-app high-performance GPU preference before real runs unless -NoHighPerformancePreference is passed.",
            "A speed gain is quotable only for the host, clip, settings, and run duration captured in this summary.",
            "UltraMagnus RTX 4090 results do not prove Dell laptop support; run this wrapper on the Dell laptop for that claim."
        )
    }
}
Write-JsonFile -Value ([pscustomobject]$summary) -Path $summaryPath
Copy-Item -LiteralPath $summaryPath -Destination $latestPath -Force

if ($DryRun) {
    Write-Output $summaryPath
    return
}
if ($preflightFailures.Count -gt 0) {
    $summary["status"] = "failed"
    $summary["proofFailures"] = @($preflightFailures)
    Write-JsonFile -Value ([pscustomobject]$summary) -Path $summaryPath
    Copy-Item -LiteralPath $summaryPath -Destination $latestPath -Force
    throw ($preflightFailures -join "; ")
}

$baselineResult = $null
$candidateResult = $null
$steps = if ($CandidateFirst) {
    @(
        [pscustomobject]@{ label = "candidate"; invokeScript = $candidateCommand.invokeScript },
        [pscustomobject]@{ label = "baseline"; invokeScript = $baselineCommand.invokeScript }
    )
}
else {
    @(
        [pscustomobject]@{ label = "baseline"; invokeScript = $baselineCommand.invokeScript },
        [pscustomobject]@{ label = "candidate"; invokeScript = $candidateCommand.invokeScript }
    )
}

foreach ($step in $steps) {
    $result = Invoke-ChildPowerShell -Arguments @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $step.invokeScript
    )
    if ($step.label -eq "baseline") {
        $baselineResult = $result
    }
    else {
        $candidateResult = $result
    }
}

$baselineSummary = Read-SmokeSummary -Path $baselineOutput -ExitCode $baselineResult.exitCode
$candidateSummary = Read-SmokeSummary -Path $candidateOutput -ExitCode $candidateResult.exitCode

$observedBuildSha = Get-NestedValue $candidateSummary "runMetadata.build_sha"
if ([string]::IsNullOrWhiteSpace([string]$observedBuildSha)) {
    $observedBuildSha = Get-NestedValue $baselineSummary "runMetadata.build_sha"
}
$missingFingerprintBuildSha =
    [string]::IsNullOrWhiteSpace([string]$summary["machineFingerprint"].build_sha)
if (-not [string]::IsNullOrWhiteSpace([string]$observedBuildSha) -and
    $missingFingerprintBuildSha) {
    $summary["machineFingerprint"].build_sha = [string]$observedBuildSha
}

$proofFailures = @()
if ($baselineResult.exitCode -ne 0) {
    $proofFailures += "baseline-smoke-exit-code=$($baselineResult.exitCode)"
}
if ($candidateResult.exitCode -ne 0) {
    $proofFailures += "candidate-smoke-exit-code=$($candidateResult.exitCode)"
}
if (-not $NoHighPerformancePreference -and $gpuPreferenceAfter -ne "GpuPreference=2;") {
    $proofFailures += "windows-high-performance-gpu-preference-not-set actual=$gpuPreferenceAfter"
}
if (-not $baselineSummary.validationOk) {
    $proofFailures += "baseline-validation-not-ok"
}
if (-not $candidateSummary.validationOk) {
    $proofFailures += "candidate-validation-not-ok"
}
if ($RequireCandidateGlParity) {
    if (-not $candidateSummary.glProof.requested) {
        $proofFailures += "candidate-gl-parity-not-requested"
    }
    if ($candidateSummary.glProof.activeProbeCount -lt 1) {
        $proofFailures += "candidate-gl-active-probe-count=$($candidateSummary.glProof.activeProbeCount)"
    }
    if ($candidateSummary.glProof.parityCheckedCount -lt 1) {
        $proofFailures += "candidate-gl-parity-checked-count=$($candidateSummary.glProof.parityCheckedCount)"
    }
    if ($candidateSummary.glProof.mismatchCountTotal -ne 0) {
        $proofFailures += "candidate-gl-parity-mismatches=$($candidateSummary.glProof.mismatchCountTotal)"
    }
}
if ($RequireCandidateImprovesPresentedFps) {
    $baselineFps = Convert-ToNullableDouble $baselineSummary.presentedFps
    $candidateFps = Convert-ToNullableDouble $candidateSummary.presentedFps
    if ($null -eq $baselineFps -or $null -eq $candidateFps -or $candidateFps -le $baselineFps) {
        $proofFailures += "candidate-presented-fps-not-improved baseline=$baselineFps candidate=$candidateFps"
    }
}

$compare = [pscustomobject]@{
    presentedFps = New-MetricDelta -BaselineValue $baselineSummary.presentedFps -CandidateValue $candidateSummary.presentedFps
    timelineFps = New-MetricDelta -BaselineValue $baselineSummary.timelineFps -CandidateValue $candidateSummary.timelineFps
    guiStatusFps = New-MetricDelta -BaselineValue $baselineSummary.guiStatusFps -CandidateValue $candidateSummary.guiStatusFps
    visibleGuiFps = New-MetricDelta -BaselineValue $baselineSummary.visibleGuiFps -CandidateValue $candidateSummary.visibleGuiFps
    avgPresentIntervalMs = New-MetricDelta -BaselineValue $baselineSummary.avgPresentIntervalMs -CandidateValue $candidateSummary.avgPresentIntervalMs
    avgRenderTotalMs = New-MetricDelta -BaselineValue $baselineSummary.avgRenderTotalMs -CandidateValue $candidateSummary.avgRenderTotalMs
    avgRenderWorkMs = New-MetricDelta -BaselineValue $baselineSummary.avgRenderWorkMs -CandidateValue $candidateSummary.avgRenderWorkMs
    avgQueueWaitMs = New-MetricDelta -BaselineValue $baselineSummary.avgQueueWaitMs -CandidateValue $candidateSummary.avgQueueWaitMs
    avgLlrawprocMs = New-MetricDelta -BaselineValue $baselineSummary.avgLlrawprocMs -CandidateValue $candidateSummary.avgLlrawprocMs
    avgProcessed8Ms = New-MetricDelta -BaselineValue $baselineSummary.avgProcessed8Ms -CandidateValue $candidateSummary.avgProcessed8Ms
    avgDrawTotalMs = New-MetricDelta -BaselineValue $baselineSummary.avgDrawTotalMs -CandidateValue $candidateSummary.avgDrawTotalMs
    avgPrepWorkerBuildMs = New-MetricDelta -BaselineValue $baselineSummary.avgPrepWorkerBuildMs -CandidateValue $candidateSummary.avgPrepWorkerBuildMs
    avgPrepWorkerTotalMs = New-MetricDelta -BaselineValue $baselineSummary.avgPrepWorkerTotalMs -CandidateValue $candidateSummary.avgPrepWorkerTotalMs
    avgPrepBeforeFinishMs = New-MetricDelta -BaselineValue $baselineSummary.avgPrepBeforeFinishMs -CandidateValue $candidateSummary.avgPrepBeforeFinishMs
}

$summary["status"] = if ($proofFailures.Count -eq 0) { "success" } else { "failed" }
$summary["baseline"] = $baselineSummary
$summary["candidate"] = $candidateSummary
$summary["compare"] = $compare
$summary["proofFailures"] = @($proofFailures)
$summary["childOutput"] = [pscustomobject]@{
    baseline = $baselineResult.output
    candidate = $candidateResult.output
}
Write-JsonFile -Value ([pscustomobject]$summary) -Path $summaryPath
Copy-Item -LiteralPath $summaryPath -Destination $latestPath -Force

Write-Output $summaryPath
if ($proofFailures.Count -gt 0) {
    exit 1
}
