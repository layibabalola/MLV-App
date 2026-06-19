param(
    [string]$RepoRoot = ".",
    [string]$RemoteHostName = "ultra-magnus",
    [string]$ExpectedEvidenceHostName = "ULTRA-MAGNUS",
    [string]$AgentRoot = "\\ultra-magnus\G\Temp\mlv-gpu-profile\agent",
    [string]$RemoteStagingShare = "\\ultra-magnus\G\Temp\mlvapp-cdng-export-evidence",
    [string]$RemoteStagingLocal = "G:\Temp\mlvapp-cdng-export-evidence",
    [string]$RemoteRepoRoot = "",
    [string]$RemotePacketPath = "",
    [string]$LocalOutputRoot = ".claude-state\profiling\ultramagnus-cdng-export",
    [string]$ClipRoot = "G:\Temp\mlv-gpu-profile\clips",
    [string[]]$ClipNames = @("M16-1327.MLV"),
    [string]$Receipt = "receipts\FastProxy.marxml",
    [ValidateSet("uncompressed", "lossless", "fast-pass")]
    [string[]]$CdngCodecs = @("uncompressed", "lossless"),
    [int]$MaxFrames = 4,
    [int]$Repeats = 1,
    [int]$AgentTimeoutSec = 2700,
    [switch]$SkipRemoteBuild,
    [switch]$SkipBackendBuild,
    [switch]$SkipStageRepo
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
    $Value | ConvertTo-Json -Depth 18 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Convert-ToPowerShellSingleQuotedString {
    param([AllowNull()][string]$Value)
    return "'" + (($Value -replace "'", "''")) + "'"
}

function Convert-ToPowerShellArrayLiteral {
    param([AllowNull()][string[]]$Values)
    $quoted = @($Values | ForEach-Object {
        Convert-ToPowerShellSingleQuotedString ([string]$_)
    })
    return "@(" + ($quoted -join ", ") + ")"
}

function Convert-AgentExitCode {
    param($Value)
    if ($null -eq $Value) {
        return $null
    }
    if ($Value.PSObject.Properties.Name -contains "value") {
        return [int]$Value.value
    }
    return [int]$Value
}

function Join-WindowsPathLiteral {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Child
    )
    return ($Root.TrimEnd("\") + "\" + $Child.TrimStart("\"))
}

function Convert-RemoteLocalPathToSharePath {
    param(
        [Parameter(Mandatory = $true)][string]$LocalPath,
        [Parameter(Mandatory = $true)][string]$RemoteLocalRoot,
        [Parameter(Mandatory = $true)][string]$RemoteShareRoot
    )
    if ($LocalPath.StartsWith("\\", [System.StringComparison]::OrdinalIgnoreCase)) {
        return $LocalPath
    }
    $localRoot = $RemoteLocalRoot.TrimEnd("\")
    if (!$LocalPath.StartsWith($localRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }
    $relative = $LocalPath.Substring($localRoot.Length).TrimStart("\")
    if ([string]::IsNullOrWhiteSpace($relative)) {
        return $RemoteShareRoot
    }
    return (Join-Path $RemoteShareRoot $relative)
}

function Invoke-RobocopyMirror {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $args = @(
        $Source,
        $Destination,
        "/MIR",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NP",
        "/R:1",
        "/W:1",
        "/XD",
        ".claude-state",
        ".scratch",
        ".vs"
    )
    $output = & robocopy.exe @args 2>&1
    $exitCode = $LASTEXITCODE
    return [pscustomobject]@{
        exitCode = $exitCode
        output = @($output | ForEach-Object { [string]$_ })
        ok = ($exitCode -lt 8)
    }
}

function Test-AgentHeartbeat {
    param([Parameter(Mandatory = $true)][string]$Root)
    $heartbeat = Join-Path $Root "heartbeat.txt"
    if (!(Test-Path -LiteralPath $heartbeat)) {
        return [pscustomobject]@{
            ok = $false
            path = $heartbeat
            ageSeconds = $null
            text = $null
            reason = "missing heartbeat"
        }
    }
    $item = Get-Item -LiteralPath $heartbeat
    $age = [int]((Get-Date).ToUniversalTime() - $item.LastWriteTimeUtc).TotalSeconds
    $text = (Get-Content -LiteralPath $heartbeat -Raw).Trim()
    return [pscustomobject]@{
        ok = ($age -le 120)
        path = $heartbeat
        ageSeconds = $age
        text = $text
        reason = if ($age -le 120) { "ok" } else { "stale heartbeat" }
    }
}

function Submit-AgentJob {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$JobId,
        [Parameter(Mandatory = $true)][string]$ScriptText,
        [Parameter(Mandatory = $true)][int]$TimeoutSec
    )

    $inbox = Join-Path $Root "inbox"
    $outbox = Join-Path $Root "outbox"
    New-Item -ItemType Directory -Force -Path $inbox | Out-Null
    New-Item -ItemType Directory -Force -Path $outbox | Out-Null

    $tmp = Join-Path $inbox "$JobId.job.tmp"
    $job = Join-Path $inbox "$JobId.job.ps1"
    $result = Join-Path $outbox "$JobId.result.json"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
    if (Test-Path -LiteralPath $job) { Remove-Item -LiteralPath $job -Force }
    if (Test-Path -LiteralPath $result) { Remove-Item -LiteralPath $result -Force }

    $ScriptText | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $job

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $result) {
            $raw = Get-Content -LiteralPath $result -Raw
            return [pscustomobject]@{
                timedOut = $false
                resultPath = $result
                result = ($raw | ConvertFrom-Json)
            }
        }
        Start-Sleep -Seconds 2
    }

    return [pscustomobject]@{
        timedOut = $true
        resultPath = $result
        result = $null
    }
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$localOutputRootResolved = Resolve-RepoPath -Root $repo -Path $LocalOutputRoot
if ([string]::IsNullOrWhiteSpace($RemoteRepoRoot)) {
    $RemoteRepoRoot = Join-WindowsPathLiteral -Root $RemoteStagingLocal -Child "repo"
}
if ([string]::IsNullOrWhiteSpace($RemotePacketPath)) {
    $RemotePacketPath = Join-WindowsPathLiteral `
        -Root $RemoteStagingLocal `
        -Child "mlvapp-cdng-export-evidence-latest.zip"
}
$remoteRepoShare = Convert-RemoteLocalPathToSharePath `
    -LocalPath $RemoteRepoRoot `
    -RemoteLocalRoot $RemoteStagingLocal `
    -RemoteShareRoot $RemoteStagingShare
$remotePacketShare = Convert-RemoteLocalPathToSharePath `
    -LocalPath $RemotePacketPath `
    -RemoteLocalRoot $RemoteStagingLocal `
    -RemoteShareRoot $RemoteStagingShare
$remoteJobOutputPath = Join-WindowsPathLiteral `
    -Root $RemoteStagingLocal `
    -Child "latest-cdng-export-agent-job-output.json"
$remoteJobOutputShare = Convert-RemoteLocalPathToSharePath `
    -LocalPath $remoteJobOutputPath `
    -RemoteLocalRoot $RemoteStagingLocal `
    -RemoteShareRoot $RemoteStagingShare

$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$summaryPath = Join-Path $localOutputRootResolved "latest-remote-invoke.json"
$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$stageResult = $null
$agentHeartbeat = $null
$agentSubmission = $null
$remoteJobOutput = $null
$copiedPacket = $null
$expandedPacket = $null
$localRepoHead = ""
$localRepoBranch = ""
$localRepoStatus = @()

New-Item -ItemType Directory -Force -Path $localOutputRootResolved | Out-Null

try {
    [System.Net.Dns]::GetHostEntry($RemoteHostName) | Out-Null
}
catch {
    Add-Failure $failures "Remote host '$RemoteHostName' did not resolve: $($_.Exception.Message)"
}

if ($failures.Count -eq 0 -and !(Test-Connection -ComputerName $RemoteHostName -Count 1 -Quiet)) {
    Add-Failure $failures "Remote host '$RemoteHostName' did not respond to ping."
}

if ($failures.Count -eq 0 -and !(Test-Path -LiteralPath $RemoteStagingShare)) {
    try {
        New-Item -ItemType Directory -Force -Path $RemoteStagingShare | Out-Null
    }
    catch {
        Add-Failure $failures "Remote staging share is unavailable: $RemoteStagingShare ($($_.Exception.Message))"
    }
}

if ($failures.Count -eq 0 -and [string]::IsNullOrWhiteSpace($remoteRepoShare)) {
    Add-Failure $failures "RemoteRepoRoot '$RemoteRepoRoot' is not under RemoteStagingLocal '$RemoteStagingLocal'; cannot map it to SMB share '$RemoteStagingShare'."
}
if ($failures.Count -eq 0 -and [string]::IsNullOrWhiteSpace($remotePacketShare)) {
    Add-Failure $failures "RemotePacketPath '$RemotePacketPath' is not under RemoteStagingLocal '$RemoteStagingLocal'; cannot map it to SMB share '$RemoteStagingShare'."
}

if ($failures.Count -eq 0) {
    $localRepoHeadOutput = @(& git -C $repo rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Add-Failure $failures "Unable to inspect local git HEAD before staging."
    }
    else {
        $localRepoHead = ([string]($localRepoHeadOutput | Select-Object -First 1)).Trim()
    }
}

if ($failures.Count -eq 0) {
    $localRepoBranchOutput = @(& git -C $repo rev-parse --abbrev-ref HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Add-Failure $failures "Unable to inspect local git branch before staging."
    }
    else {
        $localRepoBranch = ([string]($localRepoBranchOutput | Select-Object -First 1)).Trim()
    }
}

if ($failures.Count -eq 0) {
    $localRepoStatusOutput = @(& git -C $repo status --short --branch 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Add-Failure $failures "Unable to inspect local git status before staging."
    }
    else {
        $localRepoStatus = @($localRepoStatusOutput | ForEach-Object { [string]$_ })
    }
}

if ($failures.Count -eq 0 -and !$SkipStageRepo) {
    $localDirty = @($localRepoStatus | Where-Object { !([string]$_).StartsWith("## ") })
    if ($localDirty.Count -gt 0) {
        Add-Failure $failures "Local repo is dirty; commit or explicitly rerun after a clean tree before producing authoritative UltraMagnus export evidence: $($localDirty -join '; ')"
    }
}

if ($failures.Count -eq 0 -and !$SkipStageRepo) {
    $stageResult = Invoke-RobocopyMirror -Source $repo -Destination $remoteRepoShare
    if (!$stageResult.ok) {
        Add-Failure $failures "Repo staging to '$remoteRepoShare' failed with robocopy exit code $($stageResult.exitCode)."
    }
}
elseif ($SkipStageRepo) {
    [void]$warnings.Add("Skipped repo staging; remote repo root must already be current and clean: $RemoteRepoRoot")
}

if ($failures.Count -eq 0) {
    $agentHeartbeat = Test-AgentHeartbeat -Root $AgentRoot
    if (!$agentHeartbeat.ok) {
        Add-Failure $failures "UltraMagnus SMB agent is not live at '$AgentRoot': $($agentHeartbeat.reason)."
    }
}

if ($failures.Count -eq 0) {
    $packetParentShare = Split-Path -Parent $remotePacketShare
    if ($packetParentShare) {
        New-Item -ItemType Directory -Force -Path $packetParentShare | Out-Null
    }
    if (Test-Path -LiteralPath $remotePacketShare) {
        Remove-Item -LiteralPath $remotePacketShare -Force
    }
    if (Test-Path -LiteralPath $remoteJobOutputShare) {
        Remove-Item -LiteralPath $remoteJobOutputShare -Force
    }

    $jobId = "mlvapp-cdng-export-$stamp"
    $skipBuildLiteral = if ($SkipRemoteBuild) { '$true' } else { '$false' }
    $skipBackendBuildLiteral = if ($SkipBackendBuild) { '$true' } else { '$false' }
    $clipNamesLiteral = Convert-ToPowerShellArrayLiteral $ClipNames
    $codecsLiteral = Convert-ToPowerShellArrayLiteral $CdngCodecs
    $localRepoHeadLiteral = Convert-ToPowerShellSingleQuotedString $localRepoHead
    $localRepoBranchLiteral = Convert-ToPowerShellSingleQuotedString $localRepoBranch
    $localRepoStatusLiteral = Convert-ToPowerShellArrayLiteral $localRepoStatus
    $jobScript = @"
`$ErrorActionPreference = 'Stop'
`$repo = $(Convert-ToPowerShellSingleQuotedString $RemoteRepoRoot)
`$packet = $(Convert-ToPowerShellSingleQuotedString $RemotePacketPath)
`$jobOutput = $(Convert-ToPowerShellSingleQuotedString $remoteJobOutputPath)
`$expectedHost = $(Convert-ToPowerShellSingleQuotedString $ExpectedEvidenceHostName)
`$clipRoot = $(Convert-ToPowerShellSingleQuotedString $ClipRoot)
`$clipNames = $clipNamesLiteral
`$receipt = $(Convert-ToPowerShellSingleQuotedString $Receipt)
`$codecs = $codecsLiteral
`$maxFrames = $MaxFrames
`$repeats = $Repeats
`$evidenceRepoHead = $localRepoHeadLiteral
`$evidenceBranch = $localRepoBranchLiteral
`$evidenceGitStatus = $localRepoStatusLiteral
`$skipBuild = $skipBuildLiteral
`$skipBackendBuild = $skipBackendBuildLiteral
`$psExe = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not `$psExe) { `$psExe = 'powershell.exe' }

function Write-JsonFile {
    param(`$Value, [string]`$Path)
    `$dir = Split-Path -Parent `$Path
    if (`$dir) { New-Item -ItemType Directory -Force -Path `$dir | Out-Null }
    `$Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath `$Path -Encoding UTF8
}

function Copy-EvidenceFiles {
    param([string]`$SourceRoot, [string]`$DestinationRoot)
    if (!(Test-Path -LiteralPath `$SourceRoot)) { return }
    Get-ChildItem -LiteralPath `$SourceRoot -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { `$_.Extension -in @('.json', '.log', '.txt') } |
        ForEach-Object {
            `$relative = [System.IO.Path]::GetRelativePath(`$SourceRoot, `$_.FullName)
            `$dest = Join-Path `$DestinationRoot `$relative
            `$parent = Split-Path -Parent `$dest
            if (`$parent) { New-Item -ItemType Directory -Force -Path `$parent | Out-Null }
            Copy-Item -LiteralPath `$_.FullName -Destination `$dest -Force
        }
}

`$runRoot = Join-Path (Split-Path -Parent `$packet) ('run-' + '$stamp')
`$matrixDir = Join-Path `$runRoot 'matrix'
`$packetRoot = Join-Path `$runRoot 'packet'
`$summaryPath = Join-Path `$runRoot 'summary.json'
`$casesPath = Join-Path `$runRoot 'cases.json'
`$releaseDir = Join-Path `$repo 'platform\qt\build-release\release'
`$releaseExe = Join-Path `$releaseDir 'MLVApp.exe'
`$backendDir = Join-Path `$repo 'tools\gpu\backend'
`$backendBuildScript = Join-Path `$backendDir 'build-backend-dll.ps1'
`$backendDll = Join-Path `$backendDir 'igpu_recon_cuda.dll'
`$matrixScript = Join-Path `$repo 'tools\profiling\run-release-cdng-export-profile-matrix.ps1'
`$hashSummaryPath = Join-Path `$matrixDir 'dng-hash-comparison.json'
`$failures = [System.Collections.Generic.List[string]]::new()
`$warnings = [System.Collections.Generic.List[string]]::new()
`$matrixExit = `$null
`$matrixOutputTail = @()
`$releaseInfo = `$null
`$releaseHash = `$null
`$backendArtifacts = @()
`$deployedBackendArtifacts = @()
`$nvidiaSmi = @()

try {
    if ([Environment]::MachineName -ine `$expectedHost) {
        throw "Host is '`$([Environment]::MachineName)'; expected '`$expectedHost'."
    }
    try {
        `$nvidiaSmi = @(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1 | ForEach-Object { [string]`$_ })
    }
    catch {
        `$failures.Add("nvidia-smi failed: `$(`$_.Exception.Message)")
    }
    if (!(Test-Path -LiteralPath `$matrixScript)) {
        throw "Missing matrix script: `$matrixScript"
    }
    if (-not `$skipBackendBuild) {
        if (!(Test-Path -LiteralPath `$backendBuildScript)) {
            throw "Missing backend build script: `$backendBuildScript"
        }
        `$backendOutput = & `$psExe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `$backendBuildScript -Dir `$backendDir 2>&1
        `$backendExit = `$LASTEXITCODE
        if (`$backendExit -ne 0) {
            `$failures.Add("Backend DLL build failed with exit code `$backendExit.")
        }
        if (!(Test-Path -LiteralPath `$backendDll)) {
            `$failures.Add("Backend DLL build did not produce `$backendDll")
        }
        `$backendArtifacts = @(Get-ChildItem -LiteralPath `$backendDir -File -ErrorAction SilentlyContinue |
            Where-Object { `$_.Name -match '^(igpu_recon_cuda\.(dll|lib|exp)|dll_test\.exe)$' } |
            Sort-Object Name |
            ForEach-Object {
                [ordered]@{
                    name = `$_.Name
                    path = `$_.FullName
                    length = `$_.Length
                    sha256 = (Get-FileHash -LiteralPath `$_.FullName -Algorithm SHA256).Hash
                }
            })
        New-Item -ItemType Directory -Force -Path `$releaseDir | Out-Null
        `$deployTargets = @()
        if (Test-Path -LiteralPath `$backendDll) {
            `$deployTargets += [ordered]@{
                source = `$backendDll
                destination = (Join-Path `$releaseDir 'igpu_recon_cuda.dll')
            }
        }
        `$cudaRoot = (Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            Select-Object -First 1).FullName
        if (`$cudaRoot) {
            `$cudart = Get-ChildItem -LiteralPath (Join-Path `$cudaRoot 'bin') -Filter 'cudart64_*.dll' -File -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending |
                Select-Object -First 1
            if (`$cudart) {
                `$deployTargets += [ordered]@{
                    source = `$cudart.FullName
                    destination = (Join-Path `$releaseDir `$cudart.Name)
                }
            }
        }
        foreach (`$target in `$deployTargets) {
            Copy-Item -LiteralPath ([string]`$target.source) -Destination ([string]`$target.destination) -Force
        }
        `$deployedBackendArtifacts = @(`$deployTargets | ForEach-Object {
            `$item = Get-Item -LiteralPath ([string]`$_.destination)
            [ordered]@{
                name = `$item.Name
                path = `$item.FullName
                length = `$item.Length
                sha256 = (Get-FileHash -LiteralPath `$item.FullName -Algorithm SHA256).Hash
            }
        })
    }
    if (-not `$skipBuild) {
        `$makeExe = 'C:\Qt\Tools\mingw1310_64\bin\mingw32-make.exe'
        if (!(Test-Path -LiteralPath `$makeExe)) {
            `$failures.Add("Missing Qt/MinGW release build tool on UltraMagnus: `$makeExe. Rebuild the release tree locally, stage it, and rerun with -SkipRemoteBuild.")
        }
        else {
            `$env:PATH = 'C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;' + `$env:PATH
            & `$makeExe -C (Join-Path `$repo 'platform\qt\build-release') -B release -j4
            if (`$LASTEXITCODE -ne 0) {
                `$failures.Add("Release build failed with exit code `$LASTEXITCODE.")
            }
        }
    }
    if (Test-Path -LiteralPath `$releaseExe) {
        `$releaseItem = Get-Item -LiteralPath `$releaseExe
        `$releaseInfo = [ordered]@{
            path = `$releaseItem.FullName
            lastWriteTime = `$releaseItem.LastWriteTime
            length = `$releaseItem.Length
        }
        `$releaseHash = (Get-FileHash -LiteralPath `$releaseExe -Algorithm SHA256).Hash
    }
    else {
        `$failures.Add("Missing release executable: `$releaseExe")
    }
    `$resolvedReceipt = if ([System.IO.Path]::IsPathRooted(`$receipt)) { `$receipt } else { Join-Path `$repo `$receipt }
    if (!(Test-Path -LiteralPath `$resolvedReceipt)) {
        `$failures.Add("Missing receipt: `$resolvedReceipt")
    }
    `$caseRows = @()
    foreach (`$clipName in `$clipNames) {
        `$clipPath = if ([System.IO.Path]::IsPathRooted(`$clipName)) { `$clipName } else { Join-Path `$clipRoot `$clipName }
        if (!(Test-Path -LiteralPath `$clipPath)) {
            `$failures.Add("Missing clip: `$clipPath")
            continue
        }
        `$clipStem = [System.IO.Path]::GetFileNameWithoutExtension(`$clipPath)
        foreach (`$codec in `$codecs) {
            `$caseRows += [ordered]@{
                name = (`$clipStem + '-' + `$codec + '-gpu-export')
                clipPath = `$clipPath
                receipt = `$resolvedReceipt
                cdngCodec = `$codec
                maxFrames = `$maxFrames
                repeats = `$repeats
            }
        }
    }
    New-Item -ItemType Directory -Force -Path `$runRoot | Out-Null
    [ordered]@{ cases = `$caseRows } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath `$casesPath -Encoding UTF8
    if (`$failures.Count -eq 0) {
        `$matrixArgs = @(
            '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', `$matrixScript,
            '-RepoRoot', `$repo,
            '-CasesPath', `$casesPath,
            '-AlternateRunOrder',
            '-CandidateEnableGpuExport',
            '-CandidateGpuExportDll', `$backendDll,
            '-RequireBaselineNoGpuExportAttempt',
            '-RequireCandidateGpuExportAttempt',
            '-RequireCandidateGpuExportReplacement',
            '-RequireDngHashMatch',
            '-BuildId', ('ultramagnus-cdng-export-' + '$stamp'),
            '-OutputDir', `$matrixDir
        )
        `$matrixOutput = & `$psExe @matrixArgs 2>&1
        `$matrixExit = `$LASTEXITCODE
        `$matrixOutputTail = @(`$matrixOutput | Select-Object -Last 200 | ForEach-Object { [string]`$_ })
        if (`$matrixExit -ne 0) {
            `$failures.Add("CDNG export GPU matrix exited with code `$matrixExit.")
        }
    }
    `$matrixSummary = `$null
    `$hashSummary = `$null
    `$proofFailures = [System.Collections.Generic.List[string]]::new()
    `$matrixSummaryPath = Join-Path `$matrixDir 'matrix-summary.json'
    if (Test-Path -LiteralPath `$matrixSummaryPath) {
        `$matrixSummary = Get-Content -LiteralPath `$matrixSummaryPath -Raw | ConvertFrom-Json -Depth 100
        `$runs = @(`$matrixSummary.cases | ForEach-Object { `$_.runs })
        if (`$runs.Count -eq 0) {
            `$proofFailures.Add("Matrix summary contained zero runs.")
        }
        foreach (`$run in `$runs) {
            `$skipCountsText = 'skip_counts=missing'
            if (`$run.PSObject.Properties['candidateGpuExportSkipReasonCounts']) {
                `$skipParts = @()
                foreach (`$property in `$run.candidateGpuExportSkipReasonCounts.PSObject.Properties) {
                    if (`$null -ne `$property.Value -and [int]`$property.Value -ne 0) {
                        `$skipParts += "`$(`$property.Name)=`$(`$property.Value)"
                    }
                }
                `$skipCountsText = if (`$skipParts.Count -gt 0) {
                    "skip_counts=`$(`$skipParts -join ',')"
                } else {
                    'skip_counts=none'
                }
            }
            if ([string]`$run.verdict -ne 'PASS') {
                `$proofFailures.Add("Run `$(`$run.outputDir) verdict was `$(`$run.verdict).")
            }
            if ([int]`$run.baselineGpuExportAttemptedFrames -ne 0) {
                `$proofFailures.Add("Baseline attempted GPU export in `$(`$run.outputDir).")
            }
            if ([int]`$run.candidateGpuExportAttemptedFrames -ne [int]`$run.candidateFrameCount) {
                `$proofFailures.Add("Candidate attempted `$(`$run.candidateGpuExportAttemptedFrames)/`$(`$run.candidateFrameCount) GPU export frames in `$(`$run.outputDir); `$skipCountsText.")
            }
            if ([int]`$run.candidateGpuExportReplacedFrames -ne [int]`$run.candidateFrameCount) {
                `$proofFailures.Add("Candidate replaced `$(`$run.candidateGpuExportReplacedFrames)/`$(`$run.candidateFrameCount) GPU export frames in `$(`$run.outputDir); `$skipCountsText.")
            }
        }
    }
    else {
        `$proofFailures.Add("Missing matrix summary: `$matrixSummaryPath")
    }
    if (Test-Path -LiteralPath `$hashSummaryPath) {
        `$hashSummary = Get-Content -LiteralPath `$hashSummaryPath -Raw | ConvertFrom-Json -Depth 100
        if ([string]`$hashSummary.verdict -ne 'PASS') {
            `$proofFailures.Add("DNG hash comparison verdict was `$(`$hashSummary.verdict).")
        }
    }
    else {
        `$proofFailures.Add("Missing DNG hash comparison: `$hashSummaryPath")
    }
    foreach (`$proofFailure in `$proofFailures) {
        `$failures.Add(`$proofFailure)
    }
    `$status = if (`$failures.Count -eq 0) { 'success' } else { 'failed' }
    `$summary = [ordered]@{
        schema = 'mlvapp-ultramagnus-cdng-export-validation.v1'
        capturedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
        status = `$status
        host = [ordered]@{
            actual = [Environment]::MachineName
            expected = `$expectedHost
            nvidiaSmi = `$nvidiaSmi
        }
        repo = [ordered]@{
            root = `$repo
            evidenceHead = `$evidenceRepoHead
            evidenceBranch = `$evidenceBranch
            evidenceStatus = `$evidenceGitStatus
        }
        release = [ordered]@{
            exe = `$releaseInfo
            sha256 = `$releaseHash
            buildSkipped = [bool]`$skipBuild
        }
        backend = [ordered]@{
            skipped = [bool]`$skipBackendBuild
            sourceDll = `$backendDll
            artifacts = `$backendArtifacts
            deployedArtifacts = `$deployedBackendArtifacts
        }
        inputs = [ordered]@{
            clipRoot = `$clipRoot
            clipNames = `$clipNames
            receipt = `$resolvedReceipt
            cdngCodecs = `$codecs
            maxFrames = `$maxFrames
            repeats = `$repeats
        }
        outputs = [ordered]@{
            runRoot = `$runRoot
            matrixDir = `$matrixDir
            cases = `$casesPath
            summary = `$summaryPath
            packet = `$packet
            matrixSummary = `$matrixSummaryPath
            dngHashComparison = `$hashSummaryPath
        }
        proof = [ordered]@{
            gpuExportValidated = (`$status -eq 'success')
            baselineNoGpuAttemptRequired = `$true
            candidateGpuAttemptAndReplacementRequired = `$true
            dngHashMatchRequired = `$true
            matrixExitCode = `$matrixExit
            matrixSummary = `$matrixSummary
            dngHashComparison = `$hashSummary
        }
        matrixOutputTail = `$matrixOutputTail
        warnings = @(`$warnings)
        failures = @(`$failures)
    }
    Write-JsonFile -Value `$summary -Path `$summaryPath
    Write-JsonFile -Value `$summary -Path `$jobOutput
    if (Test-Path -LiteralPath `$packetRoot) { Remove-Item -LiteralPath `$packetRoot -Recurse -Force }
    New-Item -ItemType Directory -Force -Path `$packetRoot | Out-Null
    Copy-Item -LiteralPath `$summaryPath -Destination (Join-Path `$packetRoot 'summary.json') -Force
    Copy-Item -LiteralPath `$casesPath -Destination (Join-Path `$packetRoot 'cases.json') -Force
    Copy-EvidenceFiles -SourceRoot `$matrixDir -DestinationRoot (Join-Path `$packetRoot 'matrix')
    if (Test-Path -LiteralPath `$packet) { Remove-Item -LiteralPath `$packet -Force }
    Compress-Archive -Path (Join-Path `$packetRoot '*') -DestinationPath `$packet -Force
    `$summary | ConvertTo-Json -Depth 8
    if (`$status -eq 'success') { exit 0 }
    exit 2
}
catch {
    `$summary = [ordered]@{
        schema = 'mlvapp-ultramagnus-cdng-export-validation.v1'
        capturedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
        status = 'failed'
        host = [ordered]@{
            actual = [Environment]::MachineName
            expected = `$expectedHost
        }
        repo = [ordered]@{
            root = `$repo
            evidenceHead = `$evidenceRepoHead
            evidenceBranch = `$evidenceBranch
            evidenceStatus = `$evidenceGitStatus
        }
        outputs = [ordered]@{
            runRoot = `$runRoot
            summary = `$summaryPath
            packet = `$packet
        }
        failures = @([string]`$_.Exception.Message)
        warnings = @(`$warnings)
    }
    Write-JsonFile -Value `$summary -Path `$summaryPath
    Write-JsonFile -Value `$summary -Path `$jobOutput
    `$summary | ConvertTo-Json -Depth 8
    exit 1
}
"@

    $agentSubmission = Submit-AgentJob `
        -Root $AgentRoot `
        -JobId $jobId `
        -ScriptText $jobScript `
        -TimeoutSec $AgentTimeoutSec
    if ($agentSubmission.timedOut) {
        Add-Failure $failures "Timed out waiting for UltraMagnus SMB agent job '$jobId' after $AgentTimeoutSec seconds."
    }
    else {
        $agentExitCode = Convert-AgentExitCode $agentSubmission.result.exitCode
        if ($agentExitCode -ne 0) {
            Add-Failure $failures "UltraMagnus SMB agent job '$jobId' exited with code $agentExitCode."
        }
    }
}

if (Test-Path -LiteralPath $remoteJobOutputShare) {
    $remoteJobOutput = Get-Content -LiteralPath $remoteJobOutputShare -Raw | ConvertFrom-Json
}

if ($failures.Count -eq 0 -and $remoteJobOutput) {
    if ($remoteJobOutput.status -ne "success") {
        Add-Failure $failures "Remote UltraMagnus export validation status was '$($remoteJobOutput.status)'."
    }
}
elseif ($failures.Count -eq 0) {
    Add-Failure $failures "Remote job did not publish job output: $remoteJobOutputShare"
}

if (Test-Path -LiteralPath $remotePacketShare) {
    $packetDir = Join-Path $localOutputRootResolved "remote-packets"
    New-Item -ItemType Directory -Force -Path $packetDir | Out-Null
    $localPacketPath = Join-Path $packetDir ("$RemoteHostName-$stamp-" + (Split-Path -Leaf $remotePacketShare))
    Copy-Item -LiteralPath $remotePacketShare -Destination $localPacketPath -Force
    $remotePacketHash = (Get-FileHash -LiteralPath $remotePacketShare -Algorithm SHA256).Hash
    $copiedPacket = [pscustomobject]@{
        path = $localPacketPath
        length = (Get-Item -LiteralPath $localPacketPath).Length
        sha256 = (Get-FileHash -LiteralPath $localPacketPath -Algorithm SHA256).Hash
        remote = [pscustomobject]@{
            path = $remotePacketShare
            length = (Get-Item -LiteralPath $remotePacketShare).Length
            sha256 = $remotePacketHash
        }
    }
    if ($copiedPacket.sha256 -ne $remotePacketHash) {
        Add-Failure $failures "Copied packet hash mismatch: remote $remotePacketHash, local $($copiedPacket.sha256)."
    }
    else {
        $expandedRoot = Join-Path $localOutputRootResolved ("imported\packet-$stamp")
        if (Test-Path -LiteralPath $expandedRoot) {
            Remove-Item -LiteralPath $expandedRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $expandedRoot | Out-Null
        Expand-Archive -LiteralPath $localPacketPath -DestinationPath $expandedRoot -Force
        $expandedPacket = [pscustomobject]@{
            path = $expandedRoot
            summary = (Join-Path $expandedRoot "summary.json")
        }
    }
}
elseif ($failures.Count -eq 0) {
    Add-Failure $failures "Remote validator did not produce an evidence packet at '$remotePacketShare'."
}

$status = if ($failures.Count -eq 0) { "success" } else { "failed" }
$summary = [pscustomobject]@{
    schema = "mlvapp-ultramagnus-cdng-export-remote-evidence.v1"
    capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    status = $status
    localHost = [Environment]::MachineName
    transport = "smb-agent"
    remoteHost = $RemoteHostName
    expectedEvidenceHost = $ExpectedEvidenceHostName
    agentRoot = $AgentRoot
    agentHeartbeat = $agentHeartbeat
    remoteStagingShare = $RemoteStagingShare
    remoteStagingLocal = $RemoteStagingLocal
    remoteRepoRoot = $RemoteRepoRoot
    remoteRepoShare = $remoteRepoShare
    remotePacketPath = $RemotePacketPath
    remotePacketShare = $remotePacketShare
    localOutputRoot = $localOutputRootResolved
    stageResult = $stageResult
    agentSubmission = $agentSubmission
    remoteJobOutput = $remoteJobOutput
    copiedPacket = $copiedPacket
    expandedPacket = $expandedPacket
    warnings = @($warnings)
    failures = @($failures)
}

Write-JsonFile -Value $summary -Path $summaryPath
$summary | ConvertTo-Json -Depth 14

if ($status -eq "success") {
    exit 0
}
exit 2
