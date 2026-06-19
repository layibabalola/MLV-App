param(
    [string]$RepoRoot = ".",
    [string]$ExpectedHostName = "ULTRA-MAGNUS",
    [string]$RequiredGpuNamePattern = "4090",
    [string]$ClipRoot = "G:\Temp\mlv-gpu-profile\clips",
    [string[]]$ClipNames = @("M16-1327.MLV"),
    [string[]]$ClipPaths = @(),
    [string]$Receipt = "receipts\FastProxy.marxml",
    [string]$OutputRoot = ".claude-state\profiling\ultramagnus-p3-texture-present",
    [int]$Seconds = 30,
    [int]$SettleMs = 1000,
    [string]$QualityMode = "4",
    [string]$ScaleFactor = "1",
    [int]$ValidationSampleEvery = 10,
    [switch]$SkipBuild,
    [switch]$AllowNonUltraMagnus,
    [switch]$AllowGpuNameMismatch,
    [switch]$DryRun,
    [string]$EvidencePacketPath = "",
    [string]$ImportEvidencePacket = "",
    [string]$ImportOutputRoot = ".claude-state\profiling\ultramagnus-p3-texture-present\imported",
    [switch]$AllowRepoHeadMismatch,
    [string]$EvidenceRepoHead = "",
    [string]$EvidenceBranch = "",
    [string[]]$EvidenceGitStatus = @()
)

$ErrorActionPreference = "Stop"

# The no-readback P3 validation always runs with the heavy per-frame GL-readback + CPU-replay +
# SHA256 parity instrumentation, which perturbs PRESENT cadence (it is absent from real,
# uninstrumented playback whose purpose is to AVOID readback). Opt the downstream playback-artifact
# detector into CADENCE-ADVISORY mode so the JITTER/cadence component is advisory (not fatal) while
# the CORRECTNESS components -- real stall, flicker, FROZEN CONTENT -- stay fatal. Inherited by the
# smoke runner and the detector subprocess. See detect-playback-artifacts.ps1.
$env:MLVAPP_PLAYBACK_ARTIFACT_CADENCE_ADVISORY = "1"

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

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-GitScalar {
    param(
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][string[]]$Args
    )
    $git = Resolve-GitExecutable
    if (-not $git) {
        return $null
    }
    $output = & $git -C $Repo @Args 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ([string]($output | Select-Object -First 1)).Trim()
}

function Get-GitStatusLines {
    param([Parameter(Mandatory = $true)][string]$Repo)
    $git = Resolve-GitExecutable
    if (-not $git) {
        return @()
    }
    $output = & $git -C $Repo status --short --branch 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }
    return @($output | ForEach-Object { [string]$_ })
}

function Resolve-GitExecutable {
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        return $git.Source
    }

    $candidateRoots = @(
        ${env:ProgramFiles},
        ${env:ProgramFiles(x86)},
        "C:\Program Files",
        "C:\Program Files (x86)"
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    foreach ($root in $candidateRoots) {
        foreach ($relative in @("Git\cmd\git.exe", "Git\bin\git.exe")) {
            $candidate = Join-Path $root $relative
            if (Test-Path -LiteralPath $candidate) {
                return $candidate
            }
        }
    }

    return $null
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

function Get-RelativeArtifactPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    return ([System.IO.Path]::GetRelativePath($Root, $Path) -replace '\\', '/')
}

function Get-ArtifactFileEntries {
    param([Parameter(Mandatory = $true)][string]$Root)
    $rootPath = (Resolve-Path -LiteralPath $Root).Path
    return @(Get-ChildItem -LiteralPath $rootPath -Recurse -File | Sort-Object FullName | ForEach-Object {
        [pscustomobject]@{
            relativePath = Get-RelativeArtifactPath -Root $rootPath -Path $_.FullName
            length = $_.Length
            sha256 = Get-FileSha256 -Path $_.FullName
        }
    })
}

function New-EvidencePacket {
    param(
        [Parameter(Mandatory = $true)][string]$RunRoot,
        [Parameter(Mandatory = $true)][string]$PacketPath,
        [Parameter(Mandatory = $true)]$Summary,
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][string]$ExpectedHostName,
        [Parameter(Mandatory = $true)][string]$RequiredGpuNamePattern,
        [string]$EvidenceRepoHead = "",
        [string]$EvidenceBranch = "",
        [string[]]$EvidenceGitStatus = @()
    )

    $runRootResolved = (Resolve-Path -LiteralPath $RunRoot).Path
    $packetFullPath = Resolve-RepoPath -Root $Repo -Path $PacketPath
    $packetParent = Split-Path -Parent $packetFullPath
    if ($packetParent) {
        New-Item -ItemType Directory -Force -Path $packetParent | Out-Null
    }

    $manifestPath = Join-Path $runRootResolved "evidence-packet-manifest.json"
    if (Test-Path -LiteralPath $manifestPath) {
        Remove-Item -LiteralPath $manifestPath -Force
    }

    $clipProof = @($Summary.clipResults | ForEach-Object {
        [pscustomobject]@{
            clip = $_.clip
            status = $_.status
            gpuTextureNoReadbackFrames = $_.gpuTextureNoReadbackFrames
            activeNoReadbackFrameCount = $_.activeNoReadbackFrameCount
            cudaTextureSourceFrameCount = $_.cudaTextureSourceFrameCount
            fallbackFrameCount = $_.fallbackFrameCount
            glProbeActiveCount = $_.glProbeActiveCount
            glTextureReadbackOkCount = $_.glTextureReadbackOkCount
            glParityCheckedCount = $_.glParityCheckedCount
            glParityMatchCount = $_.glParityMatchCount
            glMismatchTotal = $_.glMismatchTotal
            glDistinctTextureHashes = $_.glDistinctTextureHashes
            glRendererDescriptions = $_.glRendererDescriptions
            glBackendArtifacts = $_.glBackendArtifacts
            glScreenshotMethod = $_.glScreenshotMethod
            validationOk = $_.validationOk
        }
    })
    $sourceRepoHead =
        if (![string]::IsNullOrWhiteSpace($EvidenceRepoHead)) { $EvidenceRepoHead }
        else { Get-GitScalar -Repo $Repo -Args @("rev-parse", "HEAD") }
    $sourceBranch =
        if (![string]::IsNullOrWhiteSpace($EvidenceBranch)) { $EvidenceBranch }
        else { Get-GitScalar -Repo $Repo -Args @("rev-parse", "--abbrev-ref", "HEAD") }
    $sourceGitStatus =
        if (@($EvidenceGitStatus).Count -gt 0) { @($EvidenceGitStatus | ForEach-Object { [string]$_ }) }
        else { @(Get-GitStatusLines -Repo $Repo) }

    $manifest = [pscustomobject]@{
        schema = "mlvapp-ultramagnus-p3-evidence-packet.v1"
        createdAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        source = [pscustomobject]@{
            host = [Environment]::MachineName
            expectedHost = $ExpectedHostName
            requiredGpuNamePattern = $RequiredGpuNamePattern
            repoRoot = $Repo
            repoHead = $sourceRepoHead
            branch = $sourceBranch
            gitStatus = $sourceGitStatus
        }
        proof = [pscustomobject]@{
            summaryStatus = $Summary.status
            dryRun = [bool]$Summary.dryRun
            releaseSha256 = $Summary.release.sha256
            clipCount = @($Summary.clipResults).Count
            totalGpuTextureNoReadbackFrames = (@($Summary.clipResults | ForEach-Object { [int]$_.gpuTextureNoReadbackFrames }) | Measure-Object -Sum).Sum
            totalFallbackFrames = (@($Summary.clipResults | ForEach-Object { [int]$_.fallbackFrameCount }) | Measure-Object -Sum).Sum
            correctnessValidated = [bool]$Summary.proof.correctnessValidated
            totalGlProbeActiveFrames = (@($Summary.clipResults | ForEach-Object { [int]$_.glProbeActiveCount }) | Measure-Object -Sum).Sum
            totalGlParityMismatches = (@($Summary.clipResults | ForEach-Object { [long]$_.glMismatchTotal }) | Measure-Object -Sum).Sum
            clips = $clipProof
        }
        files = @()
    }

    Write-JsonFile -Value $manifest -Path $manifestPath
    $manifest.files = @(Get-ArtifactFileEntries -Root $runRootResolved | Where-Object {
        $_.relativePath -ne "evidence-packet-manifest.json"
    })
    Write-JsonFile -Value $manifest -Path $manifestPath

    if (Test-Path -LiteralPath $packetFullPath) {
        Remove-Item -LiteralPath $packetFullPath -Force
    }
    Compress-Archive -Path (Join-Path $runRootResolved "*") -DestinationPath $packetFullPath -Force
    $packetItem = Get-Item -LiteralPath $packetFullPath

    return [pscustomobject]@{
        path = $packetItem.FullName
        length = $packetItem.Length
        sha256 = Get-FileSha256 -Path $packetItem.FullName
        manifest = $manifestPath
        manifestSha256 = Get-FileSha256 -Path $manifestPath
    }
}

function Import-EvidencePacket {
    param(
        [Parameter(Mandatory = $true)][string]$PacketPath,
        [Parameter(Mandatory = $true)][string]$DestinationRoot,
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][string]$ExpectedHostName,
        [Parameter(Mandatory = $true)][string]$RequiredGpuNamePattern,
        [Parameter(Mandatory = $true)][bool]$AllowRepoHeadMismatch
    )

    $importFailures = [System.Collections.Generic.List[string]]::new()
    $importWarnings = [System.Collections.Generic.List[string]]::new()
    $packetResolved = Resolve-RepoPath -Root $Repo -Path $PacketPath
    $destinationRootResolved = Resolve-RepoPath -Root $Repo -Path $DestinationRoot
    New-Item -ItemType Directory -Force -Path $destinationRootResolved | Out-Null

    if (!(Test-Path -LiteralPath $packetResolved)) {
        Add-Failure $importFailures "Missing evidence packet: $packetResolved"
    }

    $packetItem = $null
    $extractRoot = Join-Path $destinationRootResolved ("packet-" + (Get-Date -Format "yyyyMMddTHHmmss"))
    if ($importFailures.Count -eq 0) {
        $packetItem = Get-Item -LiteralPath $packetResolved
        New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
        Expand-Archive -LiteralPath $packetItem.FullName -DestinationPath $extractRoot -Force
    }

    $manifestPath = Join-Path $extractRoot "evidence-packet-manifest.json"
    $summaryPath = Join-Path $extractRoot "summary.json"
    $manifest = $null
    $summary = $null

    if ($importFailures.Count -eq 0) {
        if (!(Test-Path -LiteralPath $manifestPath)) {
            Add-Failure $importFailures "Packet is missing evidence-packet-manifest.json."
        }
        if (!(Test-Path -LiteralPath $summaryPath)) {
            Add-Failure $importFailures "Packet is missing summary.json."
        }
    }

    if ($importFailures.Count -eq 0) {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json

        if ($manifest.schema -ne "mlvapp-ultramagnus-p3-evidence-packet.v1") {
            Add-Failure $importFailures "Unexpected evidence manifest schema: $($manifest.schema)"
        }
        if ([string]$manifest.source.host -ine $ExpectedHostName) {
            Add-Failure $importFailures "Evidence source host was '$($manifest.source.host)'; expected '$ExpectedHostName'."
        }
        $gpuNames = @($summary.gpu.devices | ForEach-Object { [string]$_.name })
        $matchedGpu = $false
        foreach ($gpuName in $gpuNames) {
            if ($gpuName -match $RequiredGpuNamePattern) {
                $matchedGpu = $true
                break
            }
        }
        if (!$matchedGpu) {
            Add-Failure $importFailures "Evidence packet has no GPU matching '$RequiredGpuNamePattern': $($gpuNames -join '; ')"
        }

        $currentHead = Get-GitScalar -Repo $Repo -Args @("rev-parse", "HEAD")
        if (!$AllowRepoHeadMismatch -and $currentHead -and $manifest.source.repoHead -and $currentHead -ne $manifest.source.repoHead) {
            Add-Failure $importFailures "Current repo HEAD $currentHead does not match evidence repo HEAD $($manifest.source.repoHead)."
        }
        $sourceGitStatus = @()
        if ($null -ne $manifest.source.gitStatus) {
            $sourceGitStatus = @($manifest.source.gitStatus | Where-Object { ![string]::IsNullOrWhiteSpace([string]$_) })
        }
        $sourceDirtyEntries = @($sourceGitStatus | Where-Object { !([string]$_).StartsWith("## ") })
        if ($sourceDirtyEntries.Count -gt 0) {
            Add-Failure $importFailures "Evidence source worktree was dirty: $($sourceDirtyEntries -join '; ')"
        }

        foreach ($file in @($manifest.files)) {
            $artifactPath = Join-Path $extractRoot ([string]$file.relativePath)
            if (!(Test-Path -LiteralPath $artifactPath)) {
                Add-Failure $importFailures "Packet missing artifact listed in manifest: $($file.relativePath)"
                continue
            }
            $artifact = Get-Item -LiteralPath $artifactPath
            if ($artifact.Length -ne [int64]$file.length) {
                Add-Failure $importFailures "Artifact length mismatch for $($file.relativePath): expected $($file.length), got $($artifact.Length)."
            }
            $actualHash = Get-FileSha256 -Path $artifact.FullName
            if ($actualHash -ne [string]$file.sha256) {
                Add-Failure $importFailures "Artifact hash mismatch for $($file.relativePath): expected $($file.sha256), got $actualHash."
            }
        }

        if ($summary.status -ne "success") {
            Add-Failure $importFailures "Evidence summary status was '$($summary.status)'; expected success."
        }
        if ([bool]$summary.dryRun) {
            Add-Failure $importFailures "Evidence summary is a dry run; dry-run packets are not proof."
        }
        if (@($summary.failures).Count -gt 0) {
            Add-Failure $importFailures "Evidence summary contains failures: $((@($summary.failures) | ForEach-Object { [string]$_ }) -join '; ')"
        }
        if (-not [bool]$summary.proof.correctnessValidated) {
            Add-Failure $importFailures "Evidence summary proof.correctnessValidated was not true."
        }
        foreach ($clip in @($summary.clipResults)) {
            $clipName = [string]$clip.clip
            if ($clip.status -ne "success") {
                Add-Failure $importFailures "$clipName status was '$($clip.status)'; expected success."
            }
            if (-not [bool]$clip.validationOk) {
                Add-Failure $importFailures "$clipName validationOk was not true."
            }
            if ([int]$clip.gpuTextureNoReadbackFrames -le 0) {
                Add-Failure $importFailures "$clipName had gpuTextureNoReadbackFrames=$($clip.gpuTextureNoReadbackFrames); expected > 0."
            }
            if ([int]$clip.activeNoReadbackFrameCount -le 0) {
                Add-Failure $importFailures "$clipName had activeNoReadbackFrameCount=$($clip.activeNoReadbackFrameCount); expected > 0."
            }
            if ([int]$clip.cudaTextureSourceFrameCount -le 0) {
                Add-Failure $importFailures "$clipName had cudaTextureSourceFrameCount=$($clip.cudaTextureSourceFrameCount); expected > 0."
            }
            if ([int]$clip.fallbackFrameCount -ne 0) {
                Add-Failure $importFailures "$clipName had fallbackFrameCount=$($clip.fallbackFrameCount); expected 0."
            }
            if ([int]$clip.glProbeActiveCount -le 0) {
                Add-Failure $importFailures "$clipName had glProbeActiveCount=$($clip.glProbeActiveCount); expected > 0."
            }
            if ([int]$clip.glTextureReadbackOkCount -ne [int]$clip.glProbeActiveCount) {
                Add-Failure $importFailures "$clipName had glTextureReadbackOkCount=$($clip.glTextureReadbackOkCount) for glProbeActiveCount=$($clip.glProbeActiveCount)."
            }
            if ([int]$clip.glParityCheckedCount -ne [int]$clip.glProbeActiveCount) {
                Add-Failure $importFailures "$clipName had glParityCheckedCount=$($clip.glParityCheckedCount) for glProbeActiveCount=$($clip.glProbeActiveCount)."
            }
            if ([int]$clip.glParityMatchCount -ne [int]$clip.glProbeActiveCount) {
                Add-Failure $importFailures "$clipName had glParityMatchCount=$($clip.glParityMatchCount) for glProbeActiveCount=$($clip.glProbeActiveCount)."
            }
            if ([long]$clip.glMismatchTotal -ne 0) {
                Add-Failure $importFailures "$clipName had glMismatchTotal=$($clip.glMismatchTotal); expected 0."
            }
            if ([int]$clip.glDistinctTextureHashes -le 1) {
                Add-Failure $importFailures "$clipName had glDistinctTextureHashes=$($clip.glDistinctTextureHashes); expected > 1."
            }
            if ([string]$clip.glScreenshotMethod -ne "app_internal_gl_viewport_grab") {
                Add-Failure $importFailures "$clipName had glScreenshotMethod='$($clip.glScreenshotMethod)'; expected app_internal_gl_viewport_grab."
            }
            $clipRendererMatched = $false
            foreach ($renderer in @($clip.glRendererDescriptions | ForEach-Object { [string]$_ })) {
                if ($renderer -match $RequiredGpuNamePattern) {
                    $clipRendererMatched = $true
                    break
                }
            }
            if (!$clipRendererMatched) {
                Add-Failure $importFailures "$clipName had no GL renderer matching '$RequiredGpuNamePattern': $((@($clip.glRendererDescriptions) | ForEach-Object { [string]$_ }) -join '; ')"
            }
            if (@($clip.glBackendArtifacts).Count -le 0) {
                Add-Failure $importFailures "$clipName did not include a backend DLL hash artifact."
            }
        }
    }

    $packetHash = if ($packetItem) { Get-FileSha256 -Path $packetItem.FullName } else { $null }
    $status = if ($importFailures.Count -eq 0) { "success" } else { "failed" }
    $result = [pscustomobject]@{
        schema = "mlvapp-ultramagnus-p3-evidence-import.v1"
        importedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        status = $status
        packet = [pscustomobject]@{
            path = $packetResolved
            length = if ($packetItem) { $packetItem.Length } else { $null }
            sha256 = $packetHash
        }
        extractedTo = $extractRoot
        manifest = if ($manifest) { $manifestPath } else { $null }
        summary = if ($summary) { $summaryPath } else { $null }
        source = if ($manifest) { $manifest.source } else { $null }
        proof = if ($manifest) { $manifest.proof } else { $null }
        warnings = @($importWarnings)
        failures = @($importFailures)
    }

    Write-JsonFile -Value $result -Path (Join-Path $extractRoot "import-result.json")
    Write-JsonFile -Value $result -Path (Join-Path $destinationRootResolved "latest-import.json")
    $result | ConvertTo-Json -Depth 12
    if ($status -eq "success") {
        exit 0
    }
    exit 2
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$smokeScript = Join-Path $repo "tools\profiling\run-release-gui-smoke.ps1"
$releaseExe = Join-Path $repo "platform\qt\build-release\release\MLVApp.exe"
$receiptPath = Resolve-RepoPath -Root $repo -Path $Receipt
$outputRootResolved = Resolve-RepoPath -Root $repo -Path $OutputRoot
$importOutputRootResolved = Resolve-RepoPath -Root $repo -Path $ImportOutputRoot

if (![string]::IsNullOrWhiteSpace($ImportEvidencePacket)) {
    Import-EvidencePacket `
        -PacketPath $ImportEvidencePacket `
        -DestinationRoot $importOutputRootResolved `
        -Repo $repo `
        -ExpectedHostName $ExpectedHostName `
        -RequiredGpuNamePattern $RequiredGpuNamePattern `
        -AllowRepoHeadMismatch ([bool]$AllowRepoHeadMismatch)
}

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

# The no-readback path REQUIRES raw-fix processing (and hence Dual ISO recon) to be enabled:
# rawFixesEnabled=0 sets fix_raw=0, which early-returns the entire LLRawProc apply BEFORE the Dual
# ISO recon block (src/mlv/llrawproc/llrawproc.c:1776), so there is no recon-only bayer to present
# and no-readback CORRECTLY does not arm. Parse the receipt so the per-clip assertions expect arming
# ONLY when raw fixes are on; with raw fixes off they assert the correct "not-armed" outcome instead
# of false-failing. (Default receipt FastProxy.marxml has rawFixesEnabled=1.)
$receiptRawFixesEnabled = $true
if (Test-Path -LiteralPath $receiptPath) {
    try {
        [xml]$receiptXml = Get-Content -LiteralPath $receiptPath -Raw
        $rfeNode = $receiptXml.SelectSingleNode('//rawFixesEnabled')
        if ($rfeNode -and ([string]$rfeNode.InnerText).Trim() -eq '0') { $receiptRawFixesEnabled = $false }
    } catch {
        [void]$warnings.Add("Could not parse rawFixesEnabled from receipt '$receiptPath'; assuming enabled.")
    }
}

$hostName = [Environment]::MachineName
$sessionInfo = [pscustomobject]@{
    sessionName = $env:SESSIONNAME
    processSessionId = (Get-Process -Id $PID).SessionId
    userInteractive = [Environment]::UserInteractive
}
if (!$AllowNonUltraMagnus -and $hostName -ine $ExpectedHostName) {
    $hostMessage = "Host is '$hostName'; expected '$ExpectedHostName'. Use -AllowNonUltraMagnus only for dry plumbing checks."
    if ($DryRun) {
        [void]$warnings.Add($hostMessage)
    }
    else {
        Add-Failure $failures $hostMessage
    }
}
if (!$DryRun -and [int]$sessionInfo.processSessionId -eq 0) {
    Add-Failure $failures "Validator is running in Session 0; GL no-readback validation requires an interactive console session."
}
if (!$DryRun -and [string]$sessionInfo.sessionName -match '^Services$') {
    Add-Failure $failures "Validator SESSIONNAME is '$($sessionInfo.sessionName)'; GL no-readback validation requires the console user session."
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
$gpuPreferenceBefore = $null
$gpuPreferenceAfter = $null

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
    $gpuPreferenceBefore = Get-GpuPreferenceValue -Executable $item.FullName
    if (!$DryRun) {
        Set-GpuPreferenceHighPerformance -Executable $item.FullName
    }
    $gpuPreferenceAfter = Get-GpuPreferenceValue -Executable $item.FullName
    if (!$DryRun -and $gpuPreferenceAfter -ne "GpuPreference=2;") {
        Add-Failure $failures "Windows per-app GPU preference for '$($item.FullName)' was '$gpuPreferenceAfter'; expected 'GpuPreference=2;'."
    }
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
        # wb-523ca500 P3: the in-app eligibility guard now ADMITS the proven HQ
        # Dual ISO config class (interp==1, alias==1, fullres==1, chroma==0) by
        # DEFAULT for arbitrary is_bright/ev/black_delta, so the real (non-base,
        # auto-corrected) live state enters the no-readback path WITHOUT the legacy
        # MLVAPP_GPU_PLAYBACK_RECON_ALLOW_ANY_HQ_STATE diagnostic env (that env is
        # now a no-op superset and is intentionally NOT set here).
        #
        # MLVAPP_GPU_PLAYBACK_RECON_VALIDATE_OUTPUT_SAMPLE_EVERY=N samples the heavy
        # GL-vs-CPU-oracle parity check on every Nth presented no-readback frame so
        # the temporal artifact/cadence detector sees real (un-instrumented)
        # playback cadence while the sampled frames still prove GL == oracle 0 LSB.
        $envListLiteral = "@('MLVAPP_GPU_PLAYBACK_RECON=1','MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1','MLVAPP_GPU_PLAYBACK_RECON_VALIDATE_OUTPUT=1','MLVAPP_GPU_PLAYBACK_RECON_VALIDATE_OUTPUT_SAMPLE_EVERY=$ValidationSampleEvery','QT_OPENGL=desktop')"
        $expectedScaleRequest = -1
        [void][int]::TryParse($ScaleFactor, [ref]$expectedScaleRequest)
        $invokeText = @"
`$ErrorActionPreference = 'Stop'
`$envList = $envListLiteral
`$smokeParams = @{
    RepoRoot = '$repo'
    ExePath = '$releaseExe'
    ClipPath = '$($clipItem.FullName)'
    Output = '$clipOutput'
    Receipt = '$receiptPath'
    Seconds = $Seconds
    SettleMs = $SettleMs
    FrameTelemetry = `$true
    CaptureScreenshot = `$true
    FailOnColorArtifact = `$true
    DetectPlaybackArtifacts = `$true
    ScreenshotOutputDir = '$screenshots'
    Scope = 'none'
    PlaybackDebayer = 'amaze'
    PlaybackProcessing = 'subset'
    GpuPreviewProcessing = 'gpu'
    ScaleFactor = '$ScaleFactor'
    QualityMode = '$QualityMode'
    ExpectedScaleRequest = $expectedScaleRequest
    ExpectedVisualScaleRequest = $expectedScaleRequest
    ExpectedQualityMode = ([int]'$QualityMode')
    RequireLookAssist = `$false
    PreserveExperimentalEnvironment = `$true
    DisableLookAssist = `$true
    EnablePhase3QualityModes = `$true
    ExtraEnvironment = `$envList
}
& '$smokeScript' @smokeParams
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
        $glOutputProof = $null
        $glProbeActiveCount = 0
        $glTextureReadbackOkCount = 0
        $glParityCheckedCount = 0
        $glParityMatchCount = 0
        $glMismatchTotal = 0
        $glDistinctTextureHashes = 0
        $glRendererDescriptions = @()
        $glBackendArtifacts = @()
        $glScreenshotMethod = $null

        if (Test-Path -LiteralPath $clipOutput) {
            $result = Get-Content -LiteralPath $clipOutput -Raw | ConvertFrom-Json
            if (-not $result.validation.ok) {
                Add-Failure $clipFailures ("Wrapper validation failed: " + (($result.validation.failures | ForEach-Object { [string]$_ }) -join "; "))
            }
            if ($result.process.exitCode -ne 0) {
                Add-Failure $clipFailures "MLVApp process exit code was $($result.process.exitCode)."
            }
            $logPath = $result.log.path
            $glOutputProof = $result.visualQuality.glOutputProof
            if ($glOutputProof) {
                $glProbeActiveCount = [int]$glOutputProof.activeProbeCount
                $glTextureReadbackOkCount = [int]$glOutputProof.textureReadbackOkCount
                $glParityCheckedCount = [int]$glOutputProof.parityCheckedCount
                $glParityMatchCount = [int]$glOutputProof.parityMatchCount
                $glMismatchTotal = [long]$glOutputProof.mismatchCountTotal
                $glDistinctTextureHashes = [int]$glOutputProof.distinctTextureHashes
                $glRendererDescriptions = @($glOutputProof.rendererDescriptions | ForEach-Object { [string]$_ })
                $glScreenshotMethod = [string]$glOutputProof.screenshotMethod
                foreach ($backendPath in @($glOutputProof.backendResolvedPaths | ForEach-Object { [string]$_ })) {
                    if ([string]::IsNullOrWhiteSpace($backendPath) -or $backendPath -eq "none") {
                        continue
                    }
                    if (!(Test-Path -LiteralPath $backendPath)) {
                        Add-Failure $clipFailures "Backend DLL path from GL probe does not exist on evidence host: $backendPath"
                        continue
                    }
                    $backendItem = Get-Item -LiteralPath $backendPath
                    $glBackendArtifacts += [pscustomobject]@{
                        path = $backendItem.FullName
                        length = $backendItem.Length
                        sha256 = Get-FileSha256 -Path $backendItem.FullName
                    }
                }
            }
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

        if ($receiptRawFixesEnabled) {
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
        }
        else {
            # rawFixesEnabled=0 -> fix_raw=0 -> the LLRawProc apply early-returns BEFORE the Dual ISO
            # recon block (src/mlv/llrawproc/llrawproc.c:1776), so there is no recon-only bayer and the
            # no-readback path CORRECTLY does not arm. Assert that correct outcome instead of failing.
            if ($noReadbackFrames -gt 0) {
                Add-Failure $clipFailures "rawFixesEnabled=0 but gpu_texture_no_readback_frames=$noReadbackFrames; no-readback must NOT arm without raw-fix recon."
            }
            if ($noReadbackCandidateFrameCount -gt 0) {
                Add-Failure $clipFailures "rawFixesEnabled=0 but texture_no_readback_candidate appeared in $noReadbackCandidateFrameCount frame(s); no-readback must NOT arm without raw-fix recon."
            }
            if ($activeNoReadbackFrameCount -gt 0) {
                Add-Failure $clipFailures "rawFixesEnabled=0 but texture_no_readback_active appeared in $activeNoReadbackFrameCount frame(s); no-readback must NOT present without raw-fix recon."
            }
            if ($cudaTextureSourceFrameCount -gt 0) {
                Add-Failure $clipFailures "rawFixesEnabled=0 but texture_source=cuda_gl_r16_texture appeared in $cudaTextureSourceFrameCount frame(s); no-readback must NOT present without raw-fix recon."
            }
            if ($glProbeActiveCount -gt 0) {
                Add-Failure $clipFailures "rawFixesEnabled=0 but GL no-readback probe rows were active ($glProbeActiveCount); no-readback must NOT present without raw-fix recon."
            }
            [void]$warnings.Add("rawFixesEnabled=0: no-readback correctly not armed (Dual ISO recon disabled); no-readback arming and GL no-readback proof assertions skipped. Use a raw-fixes-on receipt (e.g. FastProxy.marxml) to validate the no-readback path.")
        }
        if ($receiptRawFixesEnabled) {
            if ($null -eq $glOutputProof) {
                Add-Failure $clipFailures "Smoke result did not include visualQuality.glOutputProof."
            }
            else {
                if (-not [bool]$glOutputProof.requested) {
                    Add-Failure $clipFailures "GL output proof was not requested by the smoke wrapper."
                }
                if ($glProbeActiveCount -le 0) {
                    Add-Failure $clipFailures "No active GL output probe rows were logged."
                }
                if ($glProbeActiveCount -gt 0 -and $glTextureReadbackOkCount -ne $glProbeActiveCount) {
                    Add-Failure $clipFailures "GL texture readback did not succeed for every probe ($glTextureReadbackOkCount/$glProbeActiveCount)."
                }
                if ($glProbeActiveCount -gt 1 -and $glDistinctTextureHashes -le 1) {
                    Add-Failure $clipFailures "GL texture content did not advance: distinctTextureHashes=$glDistinctTextureHashes."
                }
                if ($glProbeActiveCount -gt 0 -and $glParityCheckedCount -ne $glProbeActiveCount) {
                    Add-Failure $clipFailures "GL texture parity was not checked for every probe ($glParityCheckedCount/$glProbeActiveCount)."
                }
                if ($glProbeActiveCount -gt 0 -and $glParityMatchCount -ne $glProbeActiveCount) {
                    Add-Failure $clipFailures "GL texture parity did not match the CPU oracle for every probe ($glParityMatchCount/$glProbeActiveCount; mismatches=$glMismatchTotal)."
                }
                if ($glMismatchTotal -ne 0) {
                    Add-Failure $clipFailures "GL texture parity mismatch count was $glMismatchTotal; expected 0."
                }
                if ($glScreenshotMethod -ne "app_internal_gl_viewport_grab") {
                    Add-Failure $clipFailures "Color scan screenshot method was '$glScreenshotMethod'; expected app_internal_gl_viewport_grab."
                }
                $rendererMatched = $false
                foreach ($renderer in $glRendererDescriptions) {
                    if ($renderer -match $RequiredGpuNamePattern) {
                        $rendererMatched = $true
                        break
                    }
                }
                if (!$rendererMatched) {
                    Add-Failure $clipFailures "No GL_RENDERER from playback_smoke.gl_probe matched '$RequiredGpuNamePattern': $($glRendererDescriptions -join '; ')"
                }
                if (@($glBackendArtifacts).Count -le 0) {
                    Add-Failure $clipFailures "No backend DLL artifact hash was captured from playback_smoke.gl_probe."
                }
            }
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
            glOutputProof = $glOutputProof
            glProbeActiveCount = $glProbeActiveCount
            glTextureReadbackOkCount = $glTextureReadbackOkCount
            glParityCheckedCount = $glParityCheckedCount
            glParityMatchCount = $glParityMatchCount
            glMismatchTotal = $glMismatchTotal
            glDistinctTextureHashes = $glDistinctTextureHashes
            glRendererDescriptions = $glRendererDescriptions
            glBackendArtifacts = $glBackendArtifacts
            glScreenshotMethod = $glScreenshotMethod
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

$correctnessValidated =
    $receiptRawFixesEnabled -and
    ($status -eq "success") -and
    (-not [bool]$DryRun) -and
    (@($clipResults).Count -gt 0) -and
    (@($clipResults | Where-Object {
        $_.status -ne "success" -or
        [int]$_.glProbeActiveCount -le 0 -or
        [int]$_.glTextureReadbackOkCount -ne [int]$_.glProbeActiveCount -or
        [int]$_.glParityCheckedCount -ne [int]$_.glProbeActiveCount -or
        [int]$_.glParityMatchCount -ne [int]$_.glProbeActiveCount -or
        [long]$_.glMismatchTotal -ne 0 -or
        [int]$_.glDistinctTextureHashes -le 1 -or
        [string]$_.glScreenshotMethod -ne "app_internal_gl_viewport_grab"
    }).Count -eq 0)

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
        session = $sessionInfo
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
        gpuPreferenceBefore = $gpuPreferenceBefore
        gpuPreferenceAfter = $gpuPreferenceAfter
    }
    inputs = [pscustomobject]@{
        clipRoot = $ClipRoot
        clipNames = $ClipNames
        clipPaths = $resolvedClips
        receipt = $receiptPath
        receiptRawFixesEnabled = [bool]$receiptRawFixesEnabled
        seconds = $Seconds
        settleMs = $SettleMs
        qualityMode = $QualityMode
        scaleFactor = $ScaleFactor
        validationSampleEvery = $ValidationSampleEvery
    }
    outputs = [pscustomobject]@{
        runRoot = $runRoot
        summary = $summaryPath
        latest = $latestPath
        plannedCommands = $plannedCommands
        evidencePacket = $null
    }
    proof = [pscustomobject]@{
        correctnessValidated = [bool]$correctnessValidated
        noReadbackValidationApplicable = [bool]$receiptRawFixesEnabled
        validationSurface = "gl_texture_r16_readback_vs_cpu_dual_iso_recon_frame"
        frameAdvanceSurface = "playback_smoke.gl_probe texture_hash parsed by detect-playback-artifacts.ps1"
        colorScanSurface = "app_internal_gl_viewport_grab"
    }
    clipResults = $clipResults
    warnings = @($warnings)
    failures = @($failures)
}

Write-JsonFile -Value $summary -Path $summaryPath

if ($status -eq "success" -or $status -eq "planned") {
    $packetPath =
        if (![string]::IsNullOrWhiteSpace($EvidencePacketPath)) {
            Resolve-RepoPath -Root $repo -Path $EvidencePacketPath
        }
        else {
            Join-Path $outputRootResolved ("mlvapp-p3-evidence-$stamp.zip")
        }
    $summary.outputs.evidencePacket = New-EvidencePacket `
        -RunRoot $runRoot `
        -PacketPath $packetPath `
        -Summary $summary `
        -Repo $repo `
        -ExpectedHostName $ExpectedHostName `
        -RequiredGpuNamePattern $RequiredGpuNamePattern `
        -EvidenceRepoHead $EvidenceRepoHead `
        -EvidenceBranch $EvidenceBranch `
        -EvidenceGitStatus $EvidenceGitStatus
    Write-JsonFile -Value $summary -Path $summaryPath
}

Write-JsonFile -Value $summary -Path $latestPath
$summary | ConvertTo-Json -Depth 8

if ($status -eq "success" -or $status -eq "planned") {
    exit 0
}
exit 2
