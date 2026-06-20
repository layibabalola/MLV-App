param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$InputPath
)

$ErrorActionPreference = "Stop"

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
        else {
            throw "Unsupported perf input schema in $($record.source): $schema"
        }
    }
}

if ($rows.Count -eq 0) {
    throw "No comparable playback perf rows were produced."
}

$rows | Sort-Object machine, source |
    Format-Table -AutoSize -Wrap record_kind, machine, presented_fps, no_readback_pct, fallback_pct, fallback_count, export_frames, cdng_verdict, dng_hash, gpu_export_replaced_pct, gpu_export_trusted_pct, dng_elapsed_delta_pct, dng_throughput_status, dominant_bottleneck, suggested_optimization, dng_suggested_optimization, build_sha, source |
    Out-String -Width 4096 |
    Write-Output
