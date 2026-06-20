param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RawArgs
)

$ErrorActionPreference = "Stop"

$Json = $false
$OutputJson = ""
$InputPath = @()
for ($i = 0; $i -lt @($RawArgs).Count; $i++) {
    $arg = [string]$RawArgs[$i]
    $normalizedArg = $arg.ToLowerInvariant()
    if ($normalizedArg -eq "-json") {
        $Json = $true
        continue
    }
    if ($normalizedArg -eq "-outputjson") {
        if ($i + 1 -ge @($RawArgs).Count) {
            throw "-OutputJson requires a path argument."
        }
        $i++
        $OutputJson = [string]$RawArgs[$i]
        continue
    }
    $InputPath += $arg
}

if (@($InputPath).Count -eq 0) {
    throw "At least one input JSON/profile/field-log path is required."
}

function Read-JsonRecords {
    param([string]$Path)

    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Input JSON not found: $resolved"
    }

    $raw = Get-Content -LiteralPath $resolved -Raw
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "Input JSON is empty: $resolved"
    }

    try {
        return @([pscustomobject]@{
            source = $resolved
            json = ($raw | ConvertFrom-Json -Depth 100)
        })
    }
    catch {
        $records = @()
        $lineNumber = 0
        foreach ($line in ($raw -split "`r?`n")) {
            $lineNumber++
            if ([string]::IsNullOrWhiteSpace($line)) {
                continue
            }
            try {
                $records += [pscustomobject]@{
                    source = "${resolved}:$lineNumber"
                    json = ($line | ConvertFrom-Json -Depth 100)
                }
            }
            catch {
                throw "Failed to parse JSON or JSONL in ${resolved} at line ${lineNumber}: $($_.Exception.Message)"
            }
        }
        if ($records.Count -eq 0) {
            throw "Input JSONL has no records: $resolved"
        }
        return $records
    }
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    $dir = Split-Path -Parent $resolved
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Value | ConvertTo-Json -Depth 64 | Set-Content -LiteralPath $resolved -Encoding UTF8
}

function Assert-MachineFingerprint {
    param(
        [object]$Fingerprint,
        [string]$Source
    )

    if ($null -eq $Fingerprint -or [string]$Fingerprint.schema -ne "machine-fingerprint.v1") {
        throw "Missing or unsupported machineFingerprint schema in ${Source}: $($Fingerprint.schema)"
    }
}

function Get-TotalPipelineFrames {
    param([object]$Counts)

    if ($null -eq $Counts) {
        return 0
    }
    $total = 0
    foreach ($property in @($Counts.PSObject.Properties)) {
        $value = 0
        if ([int]::TryParse([string]$property.Value, [ref]$value)) {
            $total += $value
        }
    }
    $total
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
    return $null
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
    return $null
}

function Get-MachineLabel {
    param([object]$Fingerprint)

    if ($Fingerprint.gpu_name) {
        return [string]$Fingerprint.gpu_name
    }
    [string]$Fingerprint.cpu_model
}

function Get-FirstPropertyValue {
    param(
        [object]$Object,
        [string[]]$Names
    )

    if ($null -eq $Object) {
        return $null
    }
    foreach ($name in @($Names)) {
        $property = $Object.PSObject.Properties[$name]
        if ($null -ne $property -and $null -ne $property.Value) {
            return $property.Value
        }
    }
    return $null
}

function Get-AverageNullableDouble {
    param([object[]]$Values)

    $sum = 0.0
    $count = 0
    foreach ($value in @($Values)) {
        $parsed = Convert-ToNullableDouble $value
        if ($null -ne $parsed) {
            $sum += $parsed
            $count++
        }
    }
    if ($count -eq 0) {
        return $null
    }
    return ($sum / [double]$count)
}

function New-MachineFingerprintObject {
    param(
        [object]$BuildSha = $null,
        [object]$GpuName = $null,
        [object]$GpuDriverVersion = $null,
        [object]$GpuComputeCapability = $null,
        [object]$GpuVramTotalMb = $null,
        [object]$CpuModel = $null,
        [object]$CpuCores = $null,
        [object]$CpuThreads = $null,
        [object]$RamTotalMb = $null,
        [object]$OsVersion = $null
    )

    [pscustomobject]@{
        schema = "machine-fingerprint.v1"
        build_sha = if ($null -ne $BuildSha) { [string]$BuildSha } else { $null }
        gpu_name = if ($null -ne $GpuName) { [string]$GpuName } else { $null }
        gpu_driver_version = if ($null -ne $GpuDriverVersion) { [string]$GpuDriverVersion } else { $null }
        gpu_compute_capability = if ($null -ne $GpuComputeCapability) { [string]$GpuComputeCapability } else { $null }
        gpu_vram_total_mb = Convert-ToNullableInt64 $GpuVramTotalMb
        cpu_model = if ($null -ne $CpuModel) { [string]$CpuModel } else { $null }
        cpu_cores = Convert-ToNullableInt64 $CpuCores
        cpu_threads = Convert-ToNullableInt64 $CpuThreads
        ram_total_mb = Convert-ToNullableInt64 $RamTotalMb
        os_version = if ($null -ne $OsVersion) { [string]$OsVersion } else { $null }
    }
}

function Convert-NvidiaSmiMemoryToMb {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }
    $text = [string]$Value
    $match = [regex]::Match($text, "([0-9]+)")
    if (!$match.Success) {
        return $null
    }
    return [long]$match.Groups[1].Value
}

function Get-ImportedP3RunMetadata {
    param(
        [object]$Record,
        [string]$Source
    )

    $sourcePath = $Source
    $sourceLineIndex = $sourcePath.LastIndexOf(":")
    if ($sourceLineIndex -gt 1 -and $sourcePath.Substring($sourceLineIndex - 1, 1) -ne ":") {
        $candidate = $sourcePath.Substring(0, $sourceLineIndex)
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $sourcePath = $candidate
        }
    }
    $sourceDir = Split-Path -Parent $sourcePath
    if ([string]::IsNullOrWhiteSpace($sourceDir)) {
        return $null
    }

    foreach ($clip in @($Record.clipResults)) {
        if ($null -eq $clip.output) {
            continue
        }
        $leaf = Split-Path -Leaf ([string]$clip.output)
        if ([string]::IsNullOrWhiteSpace($leaf)) {
            continue
        }
        $candidatePath = Join-Path $sourceDir $leaf
        if (!(Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
            continue
        }
        try {
            $clipJson = Get-Content -LiteralPath $candidatePath -Raw | ConvertFrom-Json -Depth 100
            if ($clipJson.log -and $clipJson.log.runMetadata) {
                return $clipJson.log.runMetadata
            }
        }
        catch {
            continue
        }
    }
    return $null
}

function New-RemoteP3MachineFingerprint {
    param(
        [object]$Record,
        [string]$Source
    )

    $runMetadata = Get-ImportedP3RunMetadata -Record $Record -Source $Source
    $gpuDevice = @($Record.gpu.devices | Select-Object -First 1)[0]
    $osVersion = $null
    if ($runMetadata -and $runMetadata.os) {
        $osVersion = $runMetadata.os.pretty
    }
    New-MachineFingerprintObject `
        -BuildSha (Get-FirstPropertyValue -Object $runMetadata -Names @("build_sha")) `
        -GpuName (Get-FirstPropertyValue -Object $gpuDevice -Names @("name")) `
        -GpuDriverVersion (Get-FirstPropertyValue -Object $gpuDevice -Names @("driverVersion")) `
        -GpuVramTotalMb (Convert-NvidiaSmiMemoryToMb (Get-FirstPropertyValue -Object $gpuDevice -Names @("memoryTotal"))) `
        -OsVersion $osVersion
}

function Get-ClipPresentedFrames {
    param([object[]]$Clips)

    $total = 0L
    foreach ($clip in @($Clips)) {
        $value = Convert-ToNullableInt64 $clip.presentedFrames
        if ($null -ne $value) {
            $total += $value
        }
    }
    $total
}

function Get-Percent {
    param(
        [object]$Numerator,
        [object]$Denominator
    )

    $num = Convert-ToNullableDouble $Numerator
    $den = Convert-ToNullableDouble $Denominator
    if ($null -eq $num -or $null -eq $den -or [math]::Abs($den) -lt 0.000001) {
        return $null
    }
    [math]::Round(($num * 100.0) / $den, 3)
}

function Get-ProofSummarySuggestion {
    param([object]$Record)

    $playback = $Record.proof.playback
    $playbackAb = $Record.proof.playbackAb
    $cdng = $Record.proof.cdng
    if ($null -eq $playback) {
        return "run_playback_no_readback_proof"
    }
    if ((Convert-ToNullableInt64 $playback.totalFallbackFrames) -gt 0) {
        return "fix_playback_fallback_reason"
    }
    if ((Convert-ToNullableInt64 $playback.totalGpuTextureNoReadbackFrames) -le 0) {
        return "enable_gpu_texture_no_readback_or_fix_adapter"
    }
    if ($null -eq $playbackAb -or $null -eq $playbackAb.compare) {
        return "run_playback_ab_speed_probe"
    }
    $fpsDelta = Convert-ToNullableDouble $playbackAb.compare.presentedFps.delta
    if ($null -ne $fpsDelta -and $fpsDelta -lt 0) {
        return "optimize_playback_cuda_candidate"
    }
    if ($null -eq $cdng) {
        return "run_dng_hash_export_proof"
    }
    if ([string]$cdng.dngHash.verdict -ne "PASS") {
        return "fix_dng_hash_parity"
    }
    $candidateFrames = Convert-ToNullableInt64 $cdng.candidateFrameCount
    if ($null -ne $candidateFrames -and $candidateFrames -gt 0) {
        if ((Convert-ToNullableInt64 $cdng.candidateGpuExportReplacedFrames) -lt $candidateFrames) {
            return "fix_gpu_dng_export_replacement"
        }
        if ($Record.inputs.trustedGpuExport -and
            (Convert-ToNullableInt64 $cdng.candidateGpuExportTrustedFrames) -lt $candidateFrames) {
            return "fix_trusted_gpu_dng_export_coverage"
        }
    }
    if ($cdng.throughputClassification -and $cdng.throughputClassification.suggestedOptimization) {
        return [string]$cdng.throughputClassification.suggestedOptimization
    }
    return "quote_with_host_clip_boundaries"
}

function New-RemoteP3SummaryRow {
    param(
        [object]$Record,
        [string]$Source
    )

    if ([string]$Record.schema -ne "mlvapp-ultramagnus-p3-validation.v1") {
        throw "Unexpected UltraMagnus P3 summary schema in ${Source}: $($Record.schema)"
    }
    $fingerprint = New-RemoteP3MachineFingerprint -Record $Record -Source $Source
    Assert-MachineFingerprint -Fingerprint $fingerprint -Source $Source

    $clips = @($Record.clipResults)
    $presentedFrames = Get-ClipPresentedFrames -Clips $clips
    $noReadback = 0L
    $fallback = 0L
    $fpsValues = @()
    foreach ($clip in $clips) {
        $nr = Convert-ToNullableInt64 $clip.gpuTextureNoReadbackFrames
        if ($null -ne $nr) { $noReadback += $nr }
        $fb = Convert-ToNullableInt64 $clip.fallbackFrameCount
        if ($null -ne $fb) { $fallback += $fb }
        $fpsValues += $clip.presentedFps
    }
    $presentedFps = Get-AverageNullableDouble -Values $fpsValues

    $suggestion = if ([string]$Record.status -ne "success") {
        "inspect_p3_remote_validation_failure"
    } elseif (-not [bool]$Record.proof.correctnessValidated) {
        "fix_playback_correctness_validation"
    } elseif ($fallback -gt 0) {
        "fix_playback_fallback_reason"
    } elseif ($noReadback -le 0) {
        "enable_gpu_texture_no_readback_or_fix_adapter"
    } else {
        "run_playback_ab_speed_probe"
    }

    [pscustomobject]@{
        record_kind = "ultramagnus_p3_summary"
        machine = Get-MachineLabel -Fingerprint $fingerprint
        build_sha = $fingerprint.build_sha
        presented_fps = if ($null -ne $presentedFps) { [math]::Round($presentedFps, 3) } else { $null }
        no_readback_pct = if ($presentedFrames -gt 0) { [math]::Round(($noReadback * 100.0) / $presentedFrames, 3) } else { $null }
        fallback_pct = if ($presentedFrames -gt 0) { [math]::Round(($fallback * 100.0) / $presentedFrames, 3) } else { $null }
        fallback_count = $fallback
        export_frames = $null
        cdng_verdict = $null
        dng_hash = $null
        gpu_export_replaced_pct = $null
        gpu_export_trusted_pct = $null
        dng_elapsed_delta_pct = $null
        dng_throughput_status = $null
        dng_suggested_optimization = $null
        dominant_bottleneck = "unknown"
        suggested_optimization = $suggestion
        build_status = $Record.status
        source = $Source
    }
}

function Get-RemoteCdngRuns {
    param([object]$Record)

    $runs = @()
    foreach ($case in @($Record.proof.matrixSummary.cases)) {
        foreach ($run in @($case.runs)) {
            $runs += $run
        }
    }
    return @($runs)
}

function Get-UniformOrMixed {
    param([object[]]$Values)

    $nonEmpty = @($Values | Where-Object { $null -ne $_ -and -not [string]::IsNullOrWhiteSpace([string]$_) } | ForEach-Object { [string]$_ } | Select-Object -Unique)
    if ($nonEmpty.Count -eq 0) {
        return "unknown"
    }
    if ($nonEmpty.Count -eq 1) {
        return $nonEmpty[0]
    }
    return "mixed"
}

function New-RemoteCdngSummaryRow {
    param(
        [object]$Record,
        [string]$Source
    )

    if ([string]$Record.schema -ne "mlvapp-ultramagnus-cdng-export-validation.v1") {
        throw "Unexpected UltraMagnus CDNG summary schema in ${Source}: $($Record.schema)"
    }
    $matrix = $Record.proof.matrixSummary
    if ($null -eq $matrix -or [string]$matrix.schema -ne "release-cdng-export-profile-matrix.v1") {
        throw "Missing or unsupported CDNG matrix summary schema in ${Source}: $($matrix.schema)"
    }
    Assert-MachineFingerprint -Fingerprint $matrix.machineFingerprint -Source $Source

    $runs = Get-RemoteCdngRuns -Record $Record
    $candidateFrames = 0L
    $replacedFrames = 0L
    $trustedFrames = 0L
    $elapsedDeltaPctValues = @()
    $bottlenecks = @()
    foreach ($run in $runs) {
        $frames = Convert-ToNullableInt64 $run.candidateFrameCount
        if ($null -ne $frames) { $candidateFrames += $frames }
        $replaced = Convert-ToNullableInt64 $run.candidateGpuExportReplacedFrames
        if ($null -ne $replaced) { $replacedFrames += $replaced }
        $trusted = Convert-ToNullableInt64 $run.candidateGpuExportTrustedFrames
        if ($null -ne $trusted) { $trustedFrames += $trusted }
        $elapsedDeltaPctValues += $run.elapsedDeltaPercent
        if ($run.candidateBottleneck) {
            $bottlenecks += $run.candidateBottleneck.limiting_stage
        }
    }
    $elapsedDeltaPct = Get-AverageNullableDouble -Values $elapsedDeltaPctValues
    $dngHashVerdict = if ($matrix.dngHash) {
        [string]$matrix.dngHash.verdict
    } elseif ($Record.proof.dngHashComparison) {
        [string]$Record.proof.dngHashComparison.verdict
    } else {
        $null
    }
    $throughput = $matrix.throughputClassification

    [pscustomobject]@{
        record_kind = "ultramagnus_cdng_summary"
        machine = Get-MachineLabel -Fingerprint $matrix.machineFingerprint
        build_sha = $matrix.machineFingerprint.build_sha
        presented_fps = $null
        no_readback_pct = $null
        fallback_pct = $null
        fallback_count = $null
        export_frames = $candidateFrames
        cdng_verdict = [string]$matrix.verdict
        dng_hash = $dngHashVerdict
        gpu_export_replaced_pct = if ($candidateFrames -gt 0) { Get-Percent $replacedFrames $candidateFrames } else { $null }
        gpu_export_trusted_pct = if ($candidateFrames -gt 0) { Get-Percent $trustedFrames $candidateFrames } else { $null }
        dng_elapsed_delta_pct = if ($null -ne $elapsedDeltaPct) { [math]::Round($elapsedDeltaPct, 3) } else { $null }
        dng_throughput_status = if ($throughput) { [string]$throughput.status } else { $null }
        dng_suggested_optimization = if ($throughput) { [string]$throughput.suggestedOptimization } else { $null }
        dominant_bottleneck = Get-UniformOrMixed -Values $bottlenecks
        suggested_optimization = if ($throughput) { [string]$throughput.suggestedOptimization } else { "review_cdng_export_summary" }
        build_status = $Record.status
        source = $Source
    }
}

function New-ProfileRow {
    param(
        [object]$Record,
        [string]$Source
    )

    if ([string]$Record.schema -ne "mlvapp.playback_profile.v1") {
        throw "Unexpected playback profile schema in ${Source}: $($Record.schema)"
    }
    Assert-MachineFingerprint -Fingerprint $Record.machineFingerprint -Source $Source

    $summary = $Record.summary
    if ($null -eq $summary -or [string]$summary.schema -ne "mlvapp.playback-profile-summary.v1") {
        throw "Missing or unsupported playback summary schema in ${Source}: $($summary.schema)"
    }

    $counts = $summary.pipeline_counts
    $total = Get-TotalPipelineFrames -Counts $counts
    $noReadback = if ($counts) { [int]$counts.gpu_texture_no_readback } else { 0 }
    $fallback = [int]$summary.fallback_count
    $avgCadenceMs = [double]$Record.metadata.average_cadence_ms
    $presentedFps = if ($avgCadenceMs -gt 0.000001) { 1000.0 / $avgCadenceMs } else { $null }

    [pscustomobject]@{
        record_kind = "playback_profile"
        machine = Get-MachineLabel -Fingerprint $Record.machineFingerprint
        build_sha = $Record.machineFingerprint.build_sha
        presented_fps = if ($null -ne $presentedFps) { [math]::Round($presentedFps, 3) } else { $null }
        no_readback_pct = if ($total -gt 0) { [math]::Round(($noReadback * 100.0) / $total, 3) } else { $null }
        fallback_pct = if ($total -gt 0) { [math]::Round(($fallback * 100.0) / $total, 3) } else { $null }
        fallback_count = $fallback
        export_frames = $null
        cdng_verdict = $null
        dng_hash = $null
        gpu_export_replaced_pct = $null
        gpu_export_trusted_pct = $null
        dng_elapsed_delta_pct = $null
        dng_throughput_status = $null
        dng_suggested_optimization = $null
        dominant_bottleneck = $summary.bottleneck.limiting_stage
        suggested_optimization = $summary.bottleneck.suggested_optimization
        source = $Source
    }
}

function New-FieldLogRow {
    param(
        [object]$Record,
        [string]$Source
    )

    if ([string]$Record.schema -ne "mlvapp.perf-field-log.v1") {
        throw "Unexpected perf field log schema in ${Source}: $($Record.schema)"
    }
    Assert-MachineFingerprint -Fingerprint $Record.machineFingerprint -Source $Source
    $kind = [string]$Record.kind

    if ($kind -ne "playback" -and $kind -ne "export") {
        throw "Unsupported perf field log kind in ${Source}: $kind"
    }

    $total = if ($kind -eq "playback") {
        Get-TotalPipelineFrames -Counts $Record.pipeline_counts
    } else {
        0
    }
    $fallback = if ($kind -eq "playback") { [int]$Record.fallback_count } else { $null }
    $frameCount = Convert-ToNullableInt64 $Record.frame_count
    return [pscustomobject]@{
        record_kind = if ($kind -eq "playback") { "playback_field_log" } else { "export_field_log" }
        machine = Get-MachineLabel -Fingerprint $Record.machineFingerprint
        build_sha = $Record.machineFingerprint.build_sha
        presented_fps = if ($kind -eq "playback" -and $null -ne $Record.presented_fps) { [math]::Round([double]$Record.presented_fps, 3) } else { $null }
        no_readback_pct = if ($kind -eq "playback" -and $null -ne $Record.no_readback_percent) { [math]::Round([double]$Record.no_readback_percent, 3) } else { $null }
        fallback_pct = if ($kind -eq "playback" -and $total -gt 0) { [math]::Round(($fallback * 100.0) / $total, 3) } else { $null }
        fallback_count = if ($kind -eq "playback") { $fallback } else { $null }
        export_frames = if ($kind -eq "export") { $frameCount } else { $null }
        cdng_verdict = $null
        dng_hash = $null
        gpu_export_replaced_pct = $null
        gpu_export_trusted_pct = $null
        dng_elapsed_delta_pct = $null
        dng_throughput_status = $null
        dng_suggested_optimization = $null
        dominant_bottleneck = $Record.bottleneck.limiting_stage
        suggested_optimization = $Record.suggested_optimization
        source = $Source
    }
}

function New-LocalProofSummaryRow {
    param(
        [object]$Record,
        [string]$Source
    )

    if ([string]$Record.schema -ne "mlvapp-local-cuda-playback-dng-smoke.v1") {
        throw "Unexpected local proof summary schema in ${Source}: $($Record.schema)"
    }
    Assert-MachineFingerprint -Fingerprint $Record.machineFingerprint -Source $Source

    $playback = $Record.proof.playback
    $playbackAb = $Record.proof.playbackAb
    $clips = if ($playback) { @($playback.clips) } else { @() }
    $presentedFrames = Get-ClipPresentedFrames -Clips $clips
    $noReadback = if ($playback) {
        Convert-ToNullableInt64 $playback.totalGpuTextureNoReadbackFrames
    } else { $null }
    $fallback = if ($playback) {
        Convert-ToNullableInt64 $playback.totalFallbackFrames
    } else { $null }
    $candidatePresentedFps = if ($playbackAb -and $playbackAb.compare) {
        Convert-ToNullableDouble $playbackAb.compare.presentedFps.candidate
    } else { $null }
    $cdng = $Record.proof.cdng
    $candidateFrames = if ($cdng) {
        Convert-ToNullableInt64 $cdng.candidateFrameCount
    } else { $null }
    $dngElapsedDeltaPct = if ($cdng -and $cdng.speed) {
        Convert-ToNullableDouble $cdng.speed.avgElapsedDeltaPercent
    } else { $null }
    $dngSuggestion = if ($cdng -and $cdng.throughputClassification) {
        [string]$cdng.throughputClassification.suggestedOptimization
    } else { $null }

    [pscustomobject]@{
        record_kind = "local_proof_summary"
        machine = Get-MachineLabel -Fingerprint $Record.machineFingerprint
        build_sha = $Record.machineFingerprint.build_sha
        presented_fps = if ($null -ne $candidatePresentedFps) { [math]::Round($candidatePresentedFps, 3) } else { $null }
        no_readback_pct = if ($presentedFrames -gt 0 -and $null -ne $noReadback) { [math]::Round(($noReadback * 100.0) / $presentedFrames, 3) } else { $null }
        fallback_pct = if ($presentedFrames -gt 0 -and $null -ne $fallback) { [math]::Round(($fallback * 100.0) / $presentedFrames, 3) } else { $null }
        fallback_count = if ($null -ne $fallback) { $fallback } else { $null }
        export_frames = $candidateFrames
        cdng_verdict = if ($cdng) { [string]$cdng.verdict } else { $null }
        dng_hash = if ($cdng -and $cdng.dngHash) { [string]$cdng.dngHash.verdict } else { $null }
        gpu_export_replaced_pct = if ($cdng -and $null -ne $candidateFrames) { Get-Percent $cdng.candidateGpuExportReplacedFrames $candidateFrames } else { $null }
        gpu_export_trusted_pct = if ($cdng -and $null -ne $candidateFrames) { Get-Percent $cdng.candidateGpuExportTrustedFrames $candidateFrames } else { $null }
        dng_elapsed_delta_pct = if ($null -ne $dngElapsedDeltaPct) { [math]::Round($dngElapsedDeltaPct, 3) } else { $null }
        dng_throughput_status = if ($cdng -and $cdng.throughputClassification) { [string]$cdng.throughputClassification.status } else { $null }
        dng_suggested_optimization = $dngSuggestion
        dominant_bottleneck = "unknown"
        suggested_optimization = Get-ProofSummarySuggestion -Record $Record
        build_status = $Record.status
        source = $Source
    }
}

function New-PlaybackAbSummaryRow {
    param(
        [object]$Record,
        [string]$Source
    )

    if ([string]$Record.schema -ne "mlvapp-cuda-playback-ab.v1") {
        throw "Unexpected playback A/B schema in ${Source}: $($Record.schema)"
    }
    Assert-MachineFingerprint -Fingerprint $Record.machineFingerprint -Source $Source

    $baselinePresented = Convert-ToNullableDouble $Record.compare.presentedFps.baseline
    $candidatePresented = Convert-ToNullableDouble $Record.compare.presentedFps.candidate
    $deltaPct = Convert-ToNullableDouble $Record.compare.presentedFps.deltaPercent
    $suggestion = if ([string]$Record.status -ne "success") {
        "fix_playback_ab_proof_failures"
    } elseif ($null -eq $deltaPct) {
        "rerun_playback_ab_for_presented_fps_delta"
    } elseif ($deltaPct -lt 0.0) {
        "investigate_cuda_playback_regression"
    } elseif ($deltaPct -lt 10.0) {
        "profile_present_decode_and_recon_stages"
    } else {
        "validate_same_clip_on_dell"
    }

    [pscustomobject]@{
        record_kind = "playback_ab_summary"
        machine = Get-MachineLabel -Fingerprint $Record.machineFingerprint
        build_sha = $Record.machineFingerprint.build_sha
        baseline_presented_fps = if ($null -ne $baselinePresented) { [math]::Round($baselinePresented, 3) } else { $null }
        presented_fps = if ($null -ne $candidatePresented) { [math]::Round($candidatePresented, 3) } else { $null }
        playback_fps_delta_pct = if ($null -ne $deltaPct) { [math]::Round($deltaPct, 3) } else { $null }
        no_readback_pct = $null
        fallback_pct = $null
        fallback_count = $null
        export_frames = $null
        cdng_verdict = $null
        dng_hash = $null
        gpu_export_replaced_pct = $null
        gpu_export_trusted_pct = $null
        dng_elapsed_delta_pct = $null
        dng_throughput_status = $null
        dng_suggested_optimization = $null
        dominant_bottleneck = "unknown"
        suggested_optimization = $suggestion
        build_status = $Record.status
        source = $Source
    }
}

function New-ComparisonDocument {
    param(
        [object[]]$Rows,
        [string[]]$Inputs
    )

    [pscustomobject]@{
        schema = "mlvapp.compare-machine-perf.v1"
        generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        input_count = @($Inputs).Count
        row_count = @($Rows).Count
        inputs = @($Inputs)
        rows = @($Rows)
    }
}

$rows = @()
foreach ($path in $InputPath) {
    foreach ($record in (Read-JsonRecords -Path $path)) {
        $schema = [string]$record.json.schema
        if ($schema -eq "mlvapp.playback_profile.v1") {
            $rows += New-ProfileRow -Record $record.json -Source $record.source
        }
        elseif ($schema -eq "mlvapp.perf-field-log.v1") {
            $rows += New-FieldLogRow -Record $record.json -Source $record.source
        }
        elseif ($schema -eq "mlvapp-local-cuda-playback-dng-smoke.v1") {
            $rows += New-LocalProofSummaryRow -Record $record.json -Source $record.source
        }
        elseif ($schema -eq "mlvapp-cuda-playback-ab.v1") {
            $rows += New-PlaybackAbSummaryRow -Record $record.json -Source $record.source
        }
        elseif ($schema -eq "mlvapp-ultramagnus-p3-validation.v1") {
            $rows += New-RemoteP3SummaryRow -Record $record.json -Source $record.source
        }
        elseif ($schema -eq "mlvapp-ultramagnus-cdng-export-validation.v1") {
            $rows += New-RemoteCdngSummaryRow -Record $record.json -Source $record.source
        }
        else {
            throw "Unsupported perf input schema in $($record.source): $schema"
        }
    }
}

if ($rows.Count -eq 0) {
    throw "No comparable playback perf rows were produced."
}

$sortedRows = @($rows | Sort-Object machine, source)
$comparison = New-ComparisonDocument -Rows $sortedRows -Inputs $InputPath

if (-not [string]::IsNullOrWhiteSpace($OutputJson)) {
    Write-JsonFile -Value $comparison -Path $OutputJson
}

if ($Json) {
    $comparison | ConvertTo-Json -Depth 64 | Write-Output
    return
}

$sortedRows |
    Format-Table -AutoSize -Wrap record_kind, machine, baseline_presented_fps, presented_fps, playback_fps_delta_pct, no_readback_pct, fallback_pct, fallback_count, export_frames, cdng_verdict, dng_hash, gpu_export_replaced_pct, gpu_export_trusted_pct, dng_elapsed_delta_pct, dng_throughput_status, dominant_bottleneck, suggested_optimization, dng_suggested_optimization, build_sha, source |
    Out-String -Width 4096 |
    Write-Output
