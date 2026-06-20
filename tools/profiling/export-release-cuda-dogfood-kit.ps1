param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [string]$OutputRoot = "",
    [string]$KitName = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [switch]$IncludeClip,
    [switch]$CreateZip,
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

function Copy-DirectoryChildren {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force |
        Copy-Item -Destination $Destination -Recurse -Force
}

function Copy-RepoFile {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$KitRoot
    )

    $source = Join-Path $Root $RelativePath
    if (!(Test-Path -LiteralPath $source -PathType Leaf)) {
        return [pscustomobject]@{
            source = $source
            destination = Join-Path $KitRoot $RelativePath
            copied = $false
            missing = $true
        }
    }

    $destination = Join-Path $KitRoot $RelativePath
    $dir = Split-Path -Parent $destination
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $destination -Force
    [pscustomobject]@{
        source = (Resolve-Path -LiteralPath $source).Path
        destination = $destination
        copied = $true
        missing = $false
    }
}

function ConvertTo-PowerShellSingleQuotedLiteral {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value) {
        return "''"
    }
    "'" + ([string]$Value).Replace("'", "''") + "'"
}

function Write-DogfoodLauncher {
    param([Parameter(Mandatory = $true)][string]$Path)

    $scriptText = @'
param(
    [Parameter(Mandatory = $true)]
    [Alias("Input")]
    [string]$ClipPath,
    [string]$Receipt = "receipts\FastProxy.marxml",
    [string]$OutputRoot = "",
    [string]$SummaryPath = "",
    [switch]$LaunchOnly,
    [switch]$DngOnly,
    [switch]$ProofOnly,
    [switch]$SummaryOnly,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputRoot = Join-Path $repoRoot ".claude-state\profiling\$stamp-cuda-dogfood"
}
$dryArgs = @()
if ($DryRun) {
    $dryArgs += "-DryRun"
}

if ($SummaryOnly) {
    if ([string]::IsNullOrWhiteSpace($SummaryPath)) {
        $SummaryPath = Join-Path $repoRoot ".claude-state\profiling\local-cuda-playback-dng-smoke-latest.json"
    }
    & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File (Join-Path $repoRoot "tools\profiling\summarize-local-cuda-proof.ps1") `
        -RepoRoot $repoRoot `
        -SummaryPath $SummaryPath
    exit $LASTEXITCODE
}

if ($LaunchOnly) {
    & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File (Join-Path $repoRoot "tools\profiling\start-release-cuda-playback.ps1") `
        -RepoRoot $repoRoot `
        -Input $ClipPath `
        -OutputRoot (Join-Path $OutputRoot "launch") `
        @dryArgs
    exit $LASTEXITCODE
}

if ($DngOnly) {
    & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File (Join-Path $repoRoot "tools\profiling\run-release-cuda-dng-export.ps1") `
        -RepoRoot $repoRoot `
        -Input $ClipPath `
        -Output (Join-Path $OutputRoot "dng-out") `
        -Receipt $Receipt `
        -CdngCodec lossless `
        -OutputRoot (Join-Path $OutputRoot "dng-export") `
        @dryArgs
    exit $LASTEXITCODE
}

& pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File (Join-Path $repoRoot "tools\profiling\run-local-cuda-playback-dng-smoke.ps1") `
    -RepoRoot $repoRoot `
    -Input $ClipPath `
    -Receipt $Receipt `
    -OutputRoot (Join-Path $OutputRoot "proof") `
    @dryArgs
$proofExit = $LASTEXITCODE
$proofSummary = Join-Path $OutputRoot "proof\summary.json"
if (Test-Path -LiteralPath $proofSummary -PathType Leaf) {
    & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File (Join-Path $repoRoot "tools\profiling\summarize-local-cuda-proof.ps1") `
        -RepoRoot $repoRoot `
        -SummaryPath $proofSummary `
        -Output (Join-Path $OutputRoot "proof\proof-report.md")
}
exit $proofExit
'@

    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $scriptText | Set-Content -LiteralPath $Path -Encoding UTF8
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "platform\qt\build-release\release\MLVApp.exe"
}
else {
    $ExePath = Resolve-RepoPath -Root $root -Path $ExePath
}
$exe = (Resolve-Path -LiteralPath $ExePath).Path
$releaseDir = Split-Path -Parent $exe

if ([string]::IsNullOrWhiteSpace($KitName)) {
    $headShort = (& git -C $root rev-parse --short=8 HEAD 2>$null)
    if ([string]::IsNullOrWhiteSpace($headShort)) {
        $headShort = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
    }
    $KitName = "mlvapp-cuda-dogfood-$headShort"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputRoot = Join-Path $root ".claude-state\profiling\$stamp-cuda-dogfood-kit"
}
$outputRootFull = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
    (Resolve-RepoPath -Root $root -Path $OutputRoot))
$kitRoot = Join-Path $outputRootFull $KitName
$manifestPath = Join-Path $kitRoot "cuda-dogfood-manifest.json"
$zipPath = Join-Path $outputRootFull "$KitName.zip"

$artifacts = [pscustomobject]@{
    exe = Get-FileArtifact -Path $exe
    backend = Get-FileArtifact -Path (Join-Path $releaseDir "igpu_recon_cuda.dll")
    cudart = Get-FileArtifact -Path (Join-Path $releaseDir "cudart64_12.dll")
    qwindows = Get-FileArtifact -Path (Join-Path $releaseDir "platforms\qwindows.dll")
}
$missing = @($artifacts.PSObject.Properties |
    Where-Object { -not [bool]$_.Value.exists } |
    ForEach-Object { $_.Name })

$head = (& git -C $root rev-parse --verify HEAD 2>$null)
$status = @(& git -C $root status --short --branch 2>$null | ForEach-Object { [string]$_ })
$clipFullPath = ""
if (-not [string]::IsNullOrWhiteSpace($ClipPath)) {
    $clipFullPath = Resolve-RepoPath -Root $root -Path $ClipPath
    if (-not $DryRun) {
        $clipFullPath = (Resolve-Path -LiteralPath $clipFullPath).Path
    }
}

$copyPlan = @(
    [pscustomobject]@{ source = $releaseDir; destination = "platform\qt\build-release\release"; kind = "release-tree" },
    [pscustomobject]@{ source = (Join-Path $root "tools\profiling"); destination = "tools\profiling"; kind = "profiling-tools" },
    [pscustomobject]@{ source = (Join-Path $root "receipts"); destination = "receipts"; kind = "receipts" }
)
if ($IncludeClip -and -not [string]::IsNullOrWhiteSpace($clipFullPath)) {
    $copyPlan += [pscustomobject]@{ source = $clipFullPath; destination = ("clips\" + [System.IO.Path]::GetFileName($clipFullPath)); kind = "optional-clip" }
}

$manifest = [ordered]@{
    schema = "mlvapp-cuda-dogfood-kit.v1"
    status = if ($missing.Count -eq 0) { if ($DryRun) { "planned" } else { "starting" } } else { "failed" }
    createdAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    dryRun = [bool]$DryRun
    repoRoot = $root
    sourceHead = $head
    sourceStatus = $status
    kitRoot = $kitRoot
    zipPath = if ($CreateZip) { $zipPath } else { $null }
    artifacts = $artifacts
    missingRequiredArtifacts = @($missing)
    copyPlan = $copyPlan
    includedClip = if ($IncludeClip -and -not [string]::IsNullOrWhiteSpace($clipFullPath)) {
        Get-FileArtifact -Path $clipFullPath
    } else { $null }
    commands = [pscustomobject]@{
        proof = ".\RUN-CUDA-DOGFOOD.ps1 -Input <clip.mlv>"
        launchOnly = ".\RUN-CUDA-DOGFOOD.ps1 -Input <clip.mlv> -LaunchOnly"
        dngOnly = ".\RUN-CUDA-DOGFOOD.ps1 -Input <clip.mlv> -DngOnly"
        summaryOnly = ".\RUN-CUDA-DOGFOOD.ps1 -Input <clip.mlv> -SummaryOnly -SummaryPath <summary.json>"
        directProof = ".\tools\profiling\run-local-cuda-playback-dng-smoke.ps1 -RepoRoot . -Input <clip.mlv>"
        directSummary = ".\tools\profiling\summarize-local-cuda-proof.ps1 -RepoRoot . -SummaryPath <summary.json>"
    }
    proofBoundary = [pscustomobject]@{
        packageOnly = $true
        provesDellSupport = $false
        provesRealtimePlayback = $false
        provesDngHashParity = $false
        notes = @(
            "This kit preserves the release tree and profiling scripts needed to run CUDA playback and DNG/CDNG proof locally on a NVIDIA host.",
            "A copied kit is not hardware proof. Quote Dell or UltraMagnus support only from a passing local proof summary produced on that host.",
            "Rendered-video export remains out of scope for this kit."
        )
    }
}

New-Item -ItemType Directory -Force -Path $kitRoot | Out-Null
if ($missing.Count -gt 0) {
    Write-JsonFile -Value ([pscustomobject]$manifest) -Path $manifestPath
    $manifest | ConvertTo-Json -Depth 16
    exit 1
}

if (-not $DryRun) {
    Copy-DirectoryChildren -Source $releaseDir -Destination (Join-Path $kitRoot "platform\qt\build-release\release")
    Copy-DirectoryChildren -Source (Join-Path $root "tools\profiling") -Destination (Join-Path $kitRoot "tools\profiling")
    Copy-DirectoryChildren -Source (Join-Path $root "receipts") -Destination (Join-Path $kitRoot "receipts")
    [void](Copy-RepoFile -Root $root -RelativePath "docs\gpu-lane-playback-export-roadmap.md" -KitRoot $kitRoot)

    if ($IncludeClip -and -not [string]::IsNullOrWhiteSpace($clipFullPath)) {
        $clipDestinationDir = Join-Path $kitRoot "clips"
        New-Item -ItemType Directory -Force -Path $clipDestinationDir | Out-Null
        Copy-Item -LiteralPath $clipFullPath -Destination (Join-Path $clipDestinationDir ([System.IO.Path]::GetFileName($clipFullPath))) -Force
    }

    Write-DogfoodLauncher -Path (Join-Path $kitRoot "RUN-CUDA-DOGFOOD.ps1")
}

$manifest["status"] = if ($DryRun) { "planned" } else { "created" }
Write-JsonFile -Value ([pscustomobject]$manifest) -Path $manifestPath

if ($CreateZip -and -not $DryRun) {
    Compress-Archive -Path (Join-Path $kitRoot "*") -DestinationPath $zipPath -Force
    $zipArtifact = Get-FileArtifact -Path $zipPath
    $manifest["zip"] = $zipArtifact
    Write-JsonFile -Value ([pscustomobject]$manifest) -Path $manifestPath
}

$manifest | ConvertTo-Json -Depth 16
