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
        machine = if ($Record.machineFingerprint.gpu_name) { $Record.machineFingerprint.gpu_name } else { $Record.machineFingerprint.cpu_model }
        build_sha = $Record.machineFingerprint.build_sha
        presented_fps = if ($null -ne $presentedFps) { [math]::Round($presentedFps, 3) } else { $null }
        no_readback_pct = if ($total -gt 0) { [math]::Round(($noReadback * 100.0) / $total, 3) } else { $null }
        fallback_pct = if ($total -gt 0) { [math]::Round(($fallback * 100.0) / $total, 3) } else { $null }
        fallback_count = $fallback
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
    if ([string]$Record.kind -ne "playback") {
        throw "Unsupported perf field log kind in ${Source}: $($Record.kind)"
    }
    Assert-MachineFingerprint -Fingerprint $Record.machineFingerprint -Source $Source

    $total = Get-TotalPipelineFrames -Counts $Record.pipeline_counts
    $fallback = [int]$Record.fallback_count
    [pscustomobject]@{
        machine = if ($Record.machineFingerprint.gpu_name) { $Record.machineFingerprint.gpu_name } else { $Record.machineFingerprint.cpu_model }
        build_sha = $Record.machineFingerprint.build_sha
        presented_fps = if ($null -ne $Record.presented_fps) { [math]::Round([double]$Record.presented_fps, 3) } else { $null }
        no_readback_pct = if ($null -ne $Record.no_readback_percent) { [math]::Round([double]$Record.no_readback_percent, 3) } else { $null }
        fallback_pct = if ($total -gt 0) { [math]::Round(($fallback * 100.0) / $total, 3) } else { $null }
        fallback_count = $fallback
        dominant_bottleneck = $Record.bottleneck.limiting_stage
        suggested_optimization = $Record.suggested_optimization
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
        else {
            throw "Unsupported perf input schema in $($record.source): $schema"
        }
    }
}

if ($rows.Count -eq 0) {
    throw "No comparable playback perf rows were produced."
}

$rows | Sort-Object machine, source |
    Format-Table -AutoSize -Wrap machine, presented_fps, no_readback_pct, fallback_pct, fallback_count, dominant_bottleneck, suggested_optimization, build_sha, source |
    Out-String -Width 4096 |
    Write-Output
