param(
    [string]$RepoRoot = ".",
    [string]$RemoteHostName = "UltraMagnus",
    [string]$RemoteRepoRoot = "C:\!Layi Wkspc\MLV-App",
    [string]$RemotePacketPath = ".claude-state\profiling\ultramagnus-p3-texture-present\mlvapp-p3-evidence-latest.zip",
    [string]$LocalPacketRoot = ".claude-state\profiling\ultramagnus-p3-texture-present\remote-packets",
    [string]$ImportOutputRoot = ".claude-state\profiling\ultramagnus-p3-texture-present\imported",
    [int]$Seconds = 30,
    [int]$SettleMs = 1000,
    [switch]$SkipRemoteBuild,
    [string]$ImportOnlyPacket = ""
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
    $Value | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-ImportVerifier {
    param(
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][string]$ValidatorScript,
        [Parameter(Mandatory = $true)][string]$PacketPath,
        [Parameter(Mandatory = $true)][string]$ImportRoot
    )

    $output = & pwsh.exe `
        -NoLogo `
        -NoProfile `
        -NonInteractive `
        -ExecutionPolicy Bypass `
        -File $ValidatorScript `
        -RepoRoot $Repo `
        -ImportEvidencePacket $PacketPath `
        -ImportOutputRoot $ImportRoot 2>&1

    return [pscustomobject]@{
        exitCode = $LASTEXITCODE
        output = @($output | ForEach-Object { [string]$_ })
    }
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$validatorScript = Join-Path $repo "tools\profiling\run-ultramagnus-p3-validation.ps1"
$localPacketRootResolved = Resolve-RepoPath -Root $repo -Path $LocalPacketRoot
$importOutputRootResolved = Resolve-RepoPath -Root $repo -Path $ImportOutputRoot
$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$summaryPath = Join-Path $localPacketRootResolved "latest-remote-invoke.json"
$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$session = $null
$remoteResult = $null
$copiedPacket = $null
$importResult = $null

if (!(Test-Path -LiteralPath $validatorScript)) {
    Add-Failure $failures "Missing validator script: $validatorScript"
}

New-Item -ItemType Directory -Force -Path $localPacketRootResolved | Out-Null

if ($failures.Count -eq 0 -and ![string]::IsNullOrWhiteSpace($ImportOnlyPacket)) {
    $packetPath = Resolve-RepoPath -Root $repo -Path $ImportOnlyPacket
    if (!(Test-Path -LiteralPath $packetPath)) {
        Add-Failure $failures "Import-only packet does not exist: $packetPath"
    }
    else {
        $importResult = Invoke-ImportVerifier `
            -Repo $repo `
            -ValidatorScript $validatorScript `
            -PacketPath $packetPath `
            -ImportRoot $importOutputRootResolved
        if ($importResult.exitCode -ne 0) {
            Add-Failure $failures "Import verifier rejected packet with exit code $($importResult.exitCode)."
        }
        $copiedPacket = [pscustomobject]@{
            path = $packetPath
            source = "import-only"
        }
    }
}
elseif ($failures.Count -eq 0) {
    try {
        [System.Net.Dns]::GetHostEntry($RemoteHostName) | Out-Null
    }
    catch {
        Add-Failure $failures "Remote host '$RemoteHostName' did not resolve: $($_.Exception.Message)"
    }

    if ($failures.Count -eq 0 -and !(Test-Connection -ComputerName $RemoteHostName -Count 1 -Quiet)) {
        Add-Failure $failures "Remote host '$RemoteHostName' did not respond to ping."
    }

    if ($failures.Count -eq 0) {
        try {
            Test-WSMan -ComputerName $RemoteHostName -ErrorAction Stop | Out-Null
        }
        catch {
            Add-Failure $failures "PowerShell remoting is unavailable on '$RemoteHostName': $($_.Exception.Message)"
        }
    }

    if ($failures.Count -eq 0) {
        $session = New-PSSession -ComputerName $RemoteHostName
        $remoteResult = Invoke-Command -Session $session -ScriptBlock {
            param(
                [string]$RepoRoot,
                [string]$PacketPath,
                [int]$Seconds,
                [int]$SettleMs,
                [bool]$SkipBuild
            )

            $validator = Join-Path $RepoRoot "tools\profiling\run-ultramagnus-p3-validation.ps1"
            if (!(Test-Path -LiteralPath $validator)) {
                return [pscustomobject]@{
                    status = "failed"
                    exitCode = 127
                    output = @("Missing remote validator script: $validator")
                    packet = $null
                }
            }

            $args = @(
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                $validator,
                "-RepoRoot",
                $RepoRoot,
                "-EvidencePacketPath",
                $PacketPath,
                "-Seconds",
                [string]$Seconds,
                "-SettleMs",
                [string]$SettleMs
            )
            if ($SkipBuild) {
                $args += "-SkipBuild"
            }

            $output = & pwsh.exe @args 2>&1
            $exitCode = $LASTEXITCODE
            $packetFullPath =
                if ([System.IO.Path]::IsPathRooted($PacketPath)) {
                    $PacketPath
                }
                else {
                    Join-Path $RepoRoot $PacketPath
                }

            $packetInfo = $null
            if (Test-Path -LiteralPath $packetFullPath) {
                $item = Get-Item -LiteralPath $packetFullPath
                $packetInfo = [pscustomobject]@{
                    path = $item.FullName
                    length = $item.Length
                    sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
                }
            }

            return [pscustomobject]@{
                status = if ($exitCode -eq 0) { "completed" } else { "failed" }
                exitCode = $exitCode
                output = @($output | ForEach-Object { [string]$_ })
                packet = $packetInfo
            }
        } -ArgumentList $RemoteRepoRoot, $RemotePacketPath, $Seconds, $SettleMs, ([bool]$SkipRemoteBuild)

        if ($remoteResult.exitCode -ne 0) {
            Add-Failure $failures "Remote UltraMagnus validator exited with code $($remoteResult.exitCode)."
        }
        if (!$remoteResult.packet -or [string]::IsNullOrWhiteSpace([string]$remoteResult.packet.path)) {
            Add-Failure $failures "Remote validator did not produce an evidence packet."
        }
    }

    if ($failures.Count -eq 0) {
        $localPacketPath = Join-Path $localPacketRootResolved ("$RemoteHostName-$stamp-" + (Split-Path -Leaf ([string]$remoteResult.packet.path)))
        Copy-Item -FromSession $session -LiteralPath ([string]$remoteResult.packet.path) -Destination $localPacketPath -Force
        $localPacket = Get-Item -LiteralPath $localPacketPath
        $copiedPacket = [pscustomobject]@{
            path = $localPacket.FullName
            length = $localPacket.Length
            sha256 = (Get-FileHash -LiteralPath $localPacket.FullName -Algorithm SHA256).Hash
            remote = $remoteResult.packet
        }
        if ($copiedPacket.sha256 -ne $remoteResult.packet.sha256) {
            Add-Failure $failures "Copied packet hash mismatch: remote $($remoteResult.packet.sha256), local $($copiedPacket.sha256)."
        }
    }

    if ($failures.Count -eq 0) {
        $importResult = Invoke-ImportVerifier `
            -Repo $repo `
            -ValidatorScript $validatorScript `
            -PacketPath ([string]$copiedPacket.path) `
            -ImportRoot $importOutputRootResolved
        if ($importResult.exitCode -ne 0) {
            Add-Failure $failures "Import verifier rejected copied packet with exit code $($importResult.exitCode)."
        }
    }
}

if ($session) {
    Remove-PSSession -Session $session
}

$status = if ($failures.Count -eq 0) { "success" } else { "failed" }
$summary = [pscustomobject]@{
    schema = "mlvapp-ultramagnus-p3-remote-evidence.v1"
    capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    status = $status
    localHost = [Environment]::MachineName
    remoteHost = $RemoteHostName
    remoteRepoRoot = $RemoteRepoRoot
    remotePacketPath = $RemotePacketPath
    localPacketRoot = $localPacketRootResolved
    copiedPacket = $copiedPacket
    remoteResult = $remoteResult
    importResult = $importResult
    warnings = @($warnings)
    failures = @($failures)
}

Write-JsonFile -Value $summary -Path $summaryPath
$summary | ConvertTo-Json -Depth 12

if ($status -eq "success") {
    exit 0
}
exit 2
