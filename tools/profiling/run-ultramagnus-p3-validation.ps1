param(
    [string]$RepoRoot = ".",
    [string]$ExpectedHostName = "UltraMagnus",
    [string]$RequiredGpuNamePattern = "4090",
    [string]$ClipRoot = "C:\temp\MLV",
    [string[]]$ClipNames = @("M16-1327.MLV", "M16-1347.MLV", "M16-1446.MLV"),
    [string[]]$ClipPaths = @(),
    [string]$Receipt = "receipts\FastProxy.marxml",
    [string]$OutputRoot = ".claude-state\profiling\ultramagnus-p3-texture-present",
    [int]$Seconds = 30,
    [int]$SettleMs = 1000,
    [string]$QualityMode = "4",
    [string]$ScaleFactor = "1",
    [switch]$SkipBuild,
    [switch]$AllowNonUltraMagnus,
    [switch]$AllowGpuNameMismatch,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

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

function Convert-PlaybackLogLineToObject {
    param([string]$Line)

    $result = [ordered]@{}
    $matches = [regex]::Matches($Line, '(?<key>[A-Za-z0-9_]+)=(?<value>"[^"]*"|\S+)')
    foreach ($match in $matches) {
        $key = $match.Groups["key"].Value
        $rawValue = $match.Groups["value"].Value.Trim('"')

        $longValue = 0L
        $doubleValue = 0.0
        if ([long]::TryParse($rawValue, [ref]$longValue)) {
            $result[$key] = $longValue
        }
        elseif ([double]::TryParse(
            $rawValue,
            [System.Globalization.NumberStyles]::Float,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$doubleValue)) {
            $result[$key] = $doubleValue
        }
        else {
            $result[$key] = $rawValue
        }
    }
    return [pscustomobject]$result
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
    $Value | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath $Path -Encoding UTF8
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$smokeScript = Join-Path $repo "tools\profiling\run-release-gui-smoke.ps1"
$releaseExe = Join-Path $repo "platform\qt\build-release\release\MLVApp.exe"
$receiptPath = Resolve-RepoPath -Root $repo -Path $Receipt
$outputRootResolved = Resolve-RepoPath -Root $repo -Path $OutputRoot
$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$runRoot = Join-Path $outputRootResolved $stamp
$summaryPath = Join-Path $runRoot "summary.json"
$latestPath = Join-Path $outputRootResolved "latest.json"
$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

if (!(Test-Path -LiteralPath $smokeScript)) {
    Add-Failure $failures "Missing GUI smoke script: $smokeScript"
}
if (!(Test-Path -LiteralPath $receiptPath)) {
    Add-Failure $failures "Missing receipt: $receiptPath"
}

$hostName = [Environment]::MachineName
if (!$AllowNonUltraMagnus -and $hostName -ine $ExpectedHostName) {
    $hostMessage = "Host is '$hostName'; expected '$ExpectedHostName'. Use -AllowNonUltraMagnus only for dry plumbing checks."
    if ($DryRun) {
        [void]$warnings.Add($hostMessage)
    }
    else {
        Add-Failure $failures $hostMessage
    }
}

$nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
$gpuRows = @()
if ($nvidiaSmi) {
    $queryOutput = & $nvidiaSmi.Source --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1
    if ($LASTEXITCODE -eq 0) {
        foreach ($line in $queryOutput) {
            $parts = ([string]$line).Split(",") | ForEach-Object { $_.Trim() }
            if ($parts.Count -ge 3) {
                $gpuRows += [pscustomobject]@{
                    name = $parts[0]
                    driverVersion = $parts[1]
                    memoryTotal = $parts[2]
                }
            }
        }
    }
    else {
        Add-Failure $failures "nvidia-smi failed: $($queryOutput -join ' ')"
    }
}
else {
    $gpuMessage = "nvidia-smi was not found; P3 CUDA/GL validation needs the UltraMagnus NVIDIA stack."
    if ($DryRun) {
        [void]$warnings.Add($gpuMessage)
    }
    else {
        Add-Failure $failures $gpuMessage
    }
}

if ($gpuRows.Count -gt 0 -and !$AllowGpuNameMismatch) {
    $matchedGpu = $false
    foreach ($gpu in $gpuRows) {
        if ($gpu.name -match $RequiredGpuNamePattern) {
            $matchedGpu = $true
            break
        }
    }
    if (!$matchedGpu) {
        $gpuMatchMessage = "No GPU name matched '$RequiredGpuNamePattern': $($gpuRows.name -join '; ')"
        if ($DryRun) {
            [void]$warnings.Add($gpuMatchMessage)
        }
        else {
            Add-Failure $failures $gpuMatchMessage
        }
    }
}

$resolvedClips = @()
if ($ClipPaths.Count -gt 0) {
    foreach ($clipPath in $ClipPaths) {
        $resolvedClips += Resolve-RepoPath -Root $repo -Path $clipPath
    }
}
else {
    foreach ($clipName in $ClipNames) {
        $resolvedClips += Join-Path $ClipRoot $clipName
    }
}

foreach ($clip in $resolvedClips) {
    if (!(Test-Path -LiteralPath $clip)) {
        $clipMessage = "Missing clip: $clip"
        if ($DryRun) {
            [void]$warnings.Add($clipMessage)
        }
        else {
            Add-Failure $failures $clipMessage
        }
    }
}

$releaseInfo = $null
$releaseHash = $null

if ($failures.Count -eq 0 -and !$DryRun -and !$SkipBuild) {
    $env:PATH = "C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;" + $env:PATH
    & "C:\Qt\Tools\mingw1310_64\bin\mingw32-make.exe" -C (Join-Path $repo "platform\qt\build-release") -B release -j4
    if ($LASTEXITCODE -ne 0) {
        Add-Failure $failures "Release build failed with exit code $LASTEXITCODE."
    }
}

if (Test-Path -LiteralPath $releaseExe) {
    $item = Get-Item -LiteralPath $releaseExe
    $releaseInfo = [pscustomobject]@{
        fullName = $item.FullName
        lastWriteTime = $item.LastWriteTime
        length = $item.Length
    }
    $releaseHash = (Get-FileHash -LiteralPath $releaseExe -Algorithm SHA256).Hash
}
else {
    $exeMessage = "Missing release executable: $releaseExe"
    if ($DryRun) {
        [void]$warnings.Add($exeMessage)
    }
    else {
        Add-Failure $failures $exeMessage
    }
}

$clipResults = @()
$plannedCommands = @()

if ($failures.Count -eq 0) {
    New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

    foreach ($clip in $resolvedClips) {
        $clipItem = Get-Item -LiteralPath $clip
        $clipStem = [System.IO.Path]::GetFileNameWithoutExtension($clipItem.Name)
        $clipOutput = Join-Path $runRoot ($clipStem + "-p3-texture-present.json")
        $screenshots = Join-Path $runRoot ($clipStem + "-screenshots")
        $invokeScript = Join-Path $runRoot ("invoke-" + $clipStem + ".ps1")
        $envListLiteral = "@('MLVAPP_GPU_PLAYBACK_RECON=1','MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1')"
        $invokeText = @"
`$ErrorActionPreference = 'Stop'
`$envList = $envListLiteral
& '$smokeScript' `
    -RepoRoot '$repo' `
    -Input '$($clipItem.FullName)' `
    -Output '$clipOutput' `
    -Receipt '$receiptPath' `
    -Seconds $Seconds `
    -SettleMs $SettleMs `
    -FrameTelemetry `
    -CaptureScreenshot `
    -FailOnColorArtifact `
    -ScreenshotOutputDir '$screenshots' `
    -Scope none `
    -PlaybackDebayer amaze `
    -PlaybackProcessing subset `
    -GpuPreviewProcessing gpu `
    -ScaleFactor '$ScaleFactor' `
    -QualityMode '$QualityMode' `
    -ExpectedQualityMode ([int]'$QualityMode') `
    -RequireLookAssist:`$false `
    -PreserveExperimentalEnvironment `
    -DisableLookAssist `
    -EnablePhase3QualityModes `
    -ExtraEnvironment `$envList
exit `$LASTEXITCODE
"@
        $plannedCommands += [pscustomobject]@{
            clip = $clipItem.FullName
            invokeScript = $invokeScript
            output = $clipOutput
            screenshots = $screenshots
        }

        if ($DryRun) {
            $clipResults += [pscustomobject]@{
                clip = $clipItem.FullName
                status = "planned"
                output = $clipOutput
                screenshots = $screenshots
            }
            continue
        }

        $invokeText | Set-Content -LiteralPath $invokeScript -Encoding UTF8
        & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $invokeScript
        $smokeExit = $LASTEXITCODE

        $clipFailures = [System.Collections.Generic.List[string]]::new()
        if ($smokeExit -ne 0) {
            Add-Failure $clipFailures "GUI smoke exited with code $smokeExit."
        }
        if (!(Test-Path -LiteralPath $clipOutput)) {
            Add-Failure $clipFailures "Smoke result JSON was not written."
        }

        $result = $null
        $gpuSummary = $null
        $noReadbackFrames = 0
        $textureReadbackFrames = 0
        $noReadbackCandidateFrameCount = 0
        $readbackCandidateFrameCount = 0
        $activeNoReadbackFrameCount = 0
        $cudaTextureSourceFrameCount = 0
        $fallbackFrameCount = 0
        $noReadbackFallbackReasons = [ordered]@{}
        $logPath = $null

        if (Test-Path -LiteralPath $clipOutput) {
            $result = Get-Content -LiteralPath $clipOutput -Raw | ConvertFrom-Json
            if (-not $result.validation.ok) {
                Add-Failure $clipFailures ("Wrapper validation failed: " + (($result.validation.failures | ForEach-Object { [string]$_ }) -join "; "))
            }
            if ($result.process.exitCode -ne 0) {
                Add-Failure $clipFailures "MLVApp process exit code was $($result.process.exitCode)."
            }
            $logPath = $result.log.path
        }

        if ($logPath -and (Test-Path -LiteralPath $logPath)) {
            $gpuFrameLines = Select-String -LiteralPath $logPath -Pattern "playback_smoke\.gpu_frame"
            foreach ($line in $gpuFrameLines) {
                $frame = Convert-PlaybackLogLineToObject -Line $line.Line
                if ([int]$frame.texture_no_readback_candidate -eq 1) {
                    $noReadbackCandidateFrameCount++
                }
                if ([int]$frame.texture_readback_candidate -eq 1) {
                    $readbackCandidateFrameCount++
                }
                if ([int]$frame.texture_no_readback_active -eq 1) {
                    $activeNoReadbackFrameCount++
                }
                if ([string]$frame.texture_source -eq "cuda_gl_r16_texture") {
                    $cudaTextureSourceFrameCount++
                }
                if ([string]$frame.texture_source -match "fallback") {
                    $fallbackFrameCount++
                }
                $fallbackReason = [string]$frame.texture_no_readback_fallback_reason
                if (![string]::IsNullOrWhiteSpace($fallbackReason) -and $fallbackReason -ne "none") {
                    if (!$noReadbackFallbackReasons.Contains($fallbackReason)) {
                        $noReadbackFallbackReasons[$fallbackReason] = 0
                    }
                    $noReadbackFallbackReasons[$fallbackReason] = [int]$noReadbackFallbackReasons[$fallbackReason] + 1
                }
            }
            $summaryLine = Select-String -LiteralPath $logPath -Pattern "playback_smoke\.gpu_summary" | Select-Object -Last 1
            if ($summaryLine) {
                $gpuSummary = Convert-PlaybackLogLineToObject -Line $summaryLine.Line
                $noReadbackFrames = [int]$gpuSummary.gpu_texture_no_readback_frames
                $textureReadbackFrames = [int]$gpuSummary.gpu_texture_readback_frames
            }
            else {
                Add-Failure $clipFailures "Missing playback_smoke.gpu_summary line."
            }
        }
        elseif ($logPath) {
            Add-Failure $clipFailures "Smoke log path does not exist: $logPath"
        }
        else {
            Add-Failure $clipFailures "Smoke result did not report a log path."
        }

        if ($noReadbackFrames -le 0) {
            Add-Failure $clipFailures "gpu_texture_no_readback_frames was $noReadbackFrames; expected > 0."
        }
        if ($noReadbackCandidateFrameCount -le 0) {
            Add-Failure $clipFailures "No per-frame telemetry reported texture_no_readback_candidate=1."
        }
        if ($activeNoReadbackFrameCount -le 0) {
            Add-Failure $clipFailures "No per-frame telemetry reported texture_no_readback_active=1."
        }
        if ($cudaTextureSourceFrameCount -le 0) {
            Add-Failure $clipFailures "No per-frame telemetry reported texture_source=cuda_gl_r16_texture."
        }
        if ($fallbackFrameCount -gt 0) {
            Add-Failure $clipFailures "Observed $fallbackFrameCount fallback frame(s); P3 no-readback validation requires no fallback frames."
        }

        $clipStatus = if ($clipFailures.Count -eq 0) { "success" } else { "failed" }
        $clipResults += [pscustomobject]@{
            clip = $clipItem.FullName
            status = $clipStatus
            failures = @($clipFailures)
            output = $clipOutput
            log = $logPath
            screenshots = $screenshots
            processExitCode = if ($result) { $result.process.exitCode } else { $null }
            validationOk = if ($result) { [bool]$result.validation.ok } else { $false }
            presentedFrames = if ($result) { $result.log.summary.presented_frames } else { $null }
            presentedFps = if ($result) { $result.log.summary.presented_fps } else { $null }
            timelineFps = if ($result) { $result.log.summary.timeline_fps } else { $null }
            gpuTextureNoReadbackFrames = $noReadbackFrames
            gpuTextureReadbackFrames = $textureReadbackFrames
            noReadbackCandidateFrameCount = $noReadbackCandidateFrameCount
            readbackCandidateFrameCount = $readbackCandidateFrameCount
            activeNoReadbackFrameCount = $activeNoReadbackFrameCount
            cudaTextureSourceFrameCount = $cudaTextureSourceFrameCount
            fallbackFrameCount = $fallbackFrameCount
            noReadbackFallbackReasons = [pscustomobject]$noReadbackFallbackReasons
            gpuSummary = $gpuSummary
        }

        if ($clipFailures.Count -gt 0) {
            foreach ($failure in $clipFailures) {
                Add-Failure $failures "$($clipItem.Name): $failure"
            }
        }
    }
}

$status =
    if ($failures.Count -eq 0) {
        if ($DryRun) { "planned" } else { "success" }
    }
    else {
        "failed"
    }

$summary = [pscustomobject]@{
    schema = "mlvapp-ultramagnus-p3-validation.v1"
    capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    status = $status
    dryRun = [bool]$DryRun
    repoRoot = $repo
    host = [pscustomobject]@{
        actual = $hostName
        expected = $ExpectedHostName
        allowNonUltraMagnus = [bool]$AllowNonUltraMagnus
    }
    gpu = [pscustomobject]@{
        requiredNamePattern = $RequiredGpuNamePattern
        allowGpuNameMismatch = [bool]$AllowGpuNameMismatch
        devices = $gpuRows
    }
    release = [pscustomobject]@{
        exe = $releaseInfo
        sha256 = $releaseHash
        buildSkipped = [bool]$SkipBuild
    }
    inputs = [pscustomobject]@{
        clipRoot = $ClipRoot
        clipNames = $ClipNames
        clipPaths = $resolvedClips
        receipt = $receiptPath
        seconds = $Seconds
        settleMs = $SettleMs
        qualityMode = $QualityMode
        scaleFactor = $ScaleFactor
    }
    outputs = [pscustomobject]@{
        runRoot = $runRoot
        summary = $summaryPath
        latest = $latestPath
        plannedCommands = $plannedCommands
    }
    clipResults = $clipResults
    warnings = @($warnings)
    failures = @($failures)
}

Write-JsonFile -Value $summary -Path $summaryPath
Write-JsonFile -Value $summary -Path $latestPath
$summary | ConvertTo-Json -Depth 8

if ($status -eq "success" -or $status -eq "planned") {
    exit 0
}
exit 2
