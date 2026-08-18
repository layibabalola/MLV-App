function Convert-GuiSmokeProvenanceLogLineToObject {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Line)

    $result = [ordered]@{}
    foreach ($match in [regex]::Matches($Line, '(?<key>[A-Za-z0-9_]+)=(?<value>"[^"]*"|\S+)')) {
        $key = $match.Groups['key'].Value
        $rawValue = $match.Groups['value'].Value.Trim('"')
        $intValue = 0L
        if ([long]::TryParse($rawValue, [ref]$intValue)) {
            $result[$key] = $intValue
        }
        else {
            $result[$key] = $rawValue
        }
    }
    [pscustomobject]$result
}

function Assert-GuiSmokeChildEvidenceReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][bool]$ResultExists,
        [Parameter(Mandatory = $true)][bool]$ScreenshotExists
    )

    $failures = [System.Collections.Generic.List[string]]::new()
    if ($ExitCode -ne 0) {
        $failures.Add("smoke-child-exit-$ExitCode")
    }
    if (-not $ResultExists) {
        $failures.Add('missing-result-json')
    }
    if (-not $ScreenshotExists) {
        $failures.Add('missing-screenshot')
    }
    if ($failures.Count -gt 0) {
        throw "GUI smoke child evidence is unusable: $($failures -join ', ')."
    }

    [pscustomobject]@{
        ok = $true
        exitCode = $ExitCode
        resultExists = $ResultExists
        screenshotExists = $ScreenshotExists
    }
}

function Get-GuiSmokeScreenshotProvenance {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$OrderedLogLines)

    $screenshotIndex = -1
    for ($index = 0; $index -lt $OrderedLogLines.Count; ++$index) {
        if ($OrderedLogLines[$index] -like '*gui_smoke.screenshot*') {
            $screenshotIndex = $index
        }
    }

    $presentIndex = -1
    $manifestIndex = -1
    if ($screenshotIndex -ge 0) {
        for ($index = 0; $index -lt $screenshotIndex; ++$index) {
            if ($OrderedLogLines[$index] -like '*draw_frame_ready.present_content*') {
                $presentIndex = $index
            }
            if ($OrderedLogLines[$index] -like '*playback_smoke.render_manifest*') {
                $manifestIndex = $index
            }
        }
    }

    $presentLine = if ($presentIndex -ge 0) { $OrderedLogLines[$presentIndex] } else { $null }
    $manifestLine = if ($manifestIndex -ge 0) { $OrderedLogLines[$manifestIndex] } else { $null }
    $screenshotLine = if ($screenshotIndex -ge 0) { $OrderedLogLines[$screenshotIndex] } else { $null }
    $present = if ($presentLine) { Convert-GuiSmokeProvenanceLogLineToObject $presentLine } else { $null }
    $manifest = if ($manifestLine) { Convert-GuiSmokeProvenanceLogLineToObject $manifestLine } else { $null }

    $failures = [System.Collections.Generic.List[string]]::new()
    if ($screenshotIndex -lt 0) {
        $failures.Add('missing-screenshot-event')
    }
    if ($presentIndex -lt 0) {
        $failures.Add('missing-present-content-before-screenshot')
    }
    if ($manifestIndex -lt 0) {
        $failures.Add('missing-render-manifest-before-screenshot')
    }
    if ($presentIndex -ge 0 -and $manifestIndex -ge 0 -and $presentIndex -ge $manifestIndex) {
        $failures.Add('present-manifest-order-mismatch')
    }

    $displayFrame = if ($present) { $present.PSObject.Properties['display_frame'] } else { $null }
    $contentHash = if ($present) { $present.PSObject.Properties['hash'] } else { $null }
    $pathSource = if ($manifest) { $manifest.PSObject.Properties['path_source'] } else { $null }
    $cacheHit = if ($manifest) { $manifest.PSObject.Properties['processed8_cache_hit'] } else { $null }
    $rawPrefetch = if ($manifest) { $manifest.PSObject.Properties['raw_prefetch'] } else { $null }

    if ($presentIndex -ge 0 -and $null -eq $displayFrame) {
        $failures.Add('missing-display-frame')
    }
    if ($presentIndex -ge 0 -and $null -eq $contentHash) {
        $failures.Add('missing-present-content-hash')
    }
    if ($manifestIndex -ge 0 -and $null -eq $pathSource) {
        $failures.Add('missing-render-path-source')
    }
    elseif ($pathSource -and [string]$pathSource.Value -ne 'render_thread') {
        $failures.Add('render-path-not-fresh')
    }
    if ($manifestIndex -ge 0 -and $null -eq $cacheHit) {
        $failures.Add('missing-processed8-cache-hit-state')
    }
    elseif ($cacheHit -and [int64]$cacheHit.Value -ne 0) {
        $failures.Add('processed8-cache-hit')
    }

    [pscustomobject]@{
        schema = 'mlvapp-gui-smoke-screenshot-provenance.v1'
        validFresh = ($failures.Count -eq 0)
        failures = @($failures)
        displayFrame = if ($displayFrame) { [int64]$displayFrame.Value } else { $null }
        contentHash = if ($contentHash) { [string]$contentHash.Value } else { $null }
        pathSource = if ($pathSource) { [string]$pathSource.Value } else { $null }
        processed8CacheHit = if ($cacheHit) { [int64]$cacheHit.Value } else { $null }
        rawPrefetch = if ($rawPrefetch) { [int64]$rawPrefetch.Value } else { $null }
        eventIndices = [pscustomobject]@{
            presentContent = $presentIndex
            renderManifest = $manifestIndex
            screenshot = $screenshotIndex
        }
        raw = [pscustomobject]@{
            presentContent = $presentLine
            renderManifest = $manifestLine
            screenshot = $screenshotLine
        }
    }
}
