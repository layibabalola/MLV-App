[CmdletBinding()]
param(
    [string] $MailboxRoot = 'C:\mlvtmp\mlv-agent\presentmon-sidecar',
    [string] $AllowedOutputRoot = 'C:\mlvtmp\mlv-agent\outbox',
    [string] $PresentMonPath = 'C:\mlvtmp\mlv-agent\cache\PresentMon-2.5.1-x64.exe',
    [string] $ExpectedPresentMonSha256 = '9BEC3083069F58F911E6A512F4806DB51A27BD096103087BC1D05EF54C80A191',
    [string] $PresentMonArgumentsPrefixJson = '[]'
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'PresentMonSidecar.psm1') -Force
foreach ($name in @('requests', 'claims', 'receipts', 'rejected')) {
    [IO.Directory]::CreateDirectory((Join-Path $MailboxRoot $name)) | Out-Null
}

function Write-Receipt($Request, [string] $Status, [string] $Reason, $ExitCode, [string] $StartedUtc) {
    $id = [string]$Request.requestId
    $receipt = [ordered]@{
        schema = 'mlvapp.presentmon-sidecar-receipt.v2'
        requestId = $id
        requestHash = [string]$Request.requestHash
        status = $Status
        reason = $Reason
        processName = [string]$Request.processName
        outputFile = [string]$Request.outputFile
        startedUtc = $StartedUtc
        finishedUtc = [DateTimeOffset]::UtcNow.ToString('o')
        presentMonExitCode = $ExitCode
    }
    $path = Join-Path (Join-Path $MailboxRoot 'receipts') "$id.done.json"
    Write-PresentMonSidecarJsonAtomic -Value $receipt -Path $path
}

$selected = $null
$claim = $null
foreach ($candidate in @(Get-ChildItem -LiteralPath (Join-Path $MailboxRoot 'requests') -Filter '*.request.json' -File | Sort-Object Name)) {
    $candidateId = $candidate.BaseName -replace '\.request$', ''
    $claimPath = Join-Path (Join-Path $MailboxRoot 'claims') $candidate.Name
    try { [IO.File]::Move($candidate.FullName, $claimPath) } catch [IO.IOException] { continue }
    $claim = $claimPath
    try { $parsed = Read-PresentMonSidecarJson -Path $claimPath } catch {
        Move-Item -LiteralPath $claimPath -Destination (Join-Path (Join-Path $MailboxRoot 'rejected') $candidate.Name) -Force
        continue
    }
    if ([string]$parsed.requestId -cne $candidateId) {
        $parsed.requestId = $candidateId
        $parsed.requestHash = ''
        Write-Receipt $parsed 'rejected' 'request_filename_identity_mismatch' $null ([DateTimeOffset]::UtcNow.ToString('o'))
        Move-Item -LiteralPath $claimPath -Destination (Join-Path (Join-Path $MailboxRoot 'rejected') $candidate.Name) -Force
        continue
    }
    $expires = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$parsed.expiresUtc, [ref]$expires) -or $expires -le [DateTimeOffset]::UtcNow) {
        Write-Receipt $parsed 'rejected' 'request_expired' $null ([DateTimeOffset]::UtcNow.ToString('o'))
        Move-Item -LiteralPath $claimPath -Destination (Join-Path (Join-Path $MailboxRoot 'rejected') $candidate.Name) -Force
        continue
    }
    $selected = $parsed
    break
}
if ($null -eq $selected) { exit 2 }

$started = [DateTimeOffset]::UtcNow.ToString('o')
try {
    if ([string]$selected.schema -cne 'mlvapp.presentmon-sidecar-request.v2') { throw 'request_schema_invalid' }
    $calculatedHash = Get-PresentMonSidecarRequestHash -Request $selected
    if ([string]$selected.requestHash -cne $calculatedHash) { throw 'request_hash_mismatch' }
    $normalizedOutput = Resolve-PresentMonSidecarOutputPath -Path ([string]$selected.outputFile) -AllowedRoot $AllowedOutputRoot
    $selected.outputFile = $normalizedOutput
    $actualPresentMonHash = (Get-FileHash -LiteralPath $PresentMonPath -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actualPresentMonHash -cne $ExpectedPresentMonSha256.ToUpperInvariant()) { throw 'presentmon_hash_mismatch' }
    [IO.Directory]::CreateDirectory((Split-Path -Parent $normalizedOutput)) | Out-Null
    Remove-Item -LiteralPath $normalizedOutput -Force -ErrorAction SilentlyContinue

    $prefixArguments = @($PresentMonArgumentsPrefixJson | ConvertFrom-Json)
    $arguments = $prefixArguments + @(
        '--process_name', [string]$selected.processName,
        '--output_file', $normalizedOutput,
        '--timed', [string][int]$selected.timedSeconds,
        '--terminate_after_timed', '--stop_existing_session', '--no_console_stats'
    )
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $PresentMonPath
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    foreach ($argument in $arguments) { [void]$psi.ArgumentList.Add([string]$argument) }
    $process = [Diagnostics.Process]::Start($psi)
    $process.WaitForExit()
    $reason = if ($process.ExitCode -eq 0) { 'ok' } else { 'presentmon_failed' }
    $status = if ($process.ExitCode -eq 0) { 'done' } else { 'failed' }
    Write-Receipt $selected $status $reason $process.ExitCode $started
    if ($process.ExitCode -ne 0) { exit 11 }
} catch {
    Write-Receipt $selected 'rejected' $_.Exception.Message $null $started
    exit 10
} finally {
    if ($claim -and (Test-Path -LiteralPath $claim)) { Remove-Item -LiteralPath $claim -Force }
}
exit 0
