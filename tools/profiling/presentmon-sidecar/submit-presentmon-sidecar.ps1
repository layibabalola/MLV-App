[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ProcessName,
    [Parameter(Mandatory)] [string] $OutputFile,
    [Parameter(Mandatory)] [string] $ResultPath,
    [string] $MailboxRoot = 'C:\mlvtmp\mlv-agent\presentmon-sidecar',
    [string] $AllowedOutputRoot = 'C:\mlvtmp\mlv-agent\outbox',
    [string] $ScheduledTaskName = 'MLV\PresentMonSidecar',
    [string] $TriggerScript,
    [ValidateRange(1, 3600)] [int] $TimedSeconds = 55,
    [ValidateRange(5, 3600)] [int] $RequestLifetimeSeconds = 180,
    [ValidateRange(1, 7200)] [int] $WaitTimeoutSeconds = 240,
    [ValidateRange(10, 7200)] [int] $StaleLockSeconds = 600
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'PresentMonSidecar.psm1') -Force

$requestId = [guid]::NewGuid().ToString('N')
$started = [DateTimeOffset]::UtcNow
$outcome = [ordered]@{
    schema = 'mlvapp.presentmon-sidecar-result.v2'
    requestId = $requestId
    status = 'client_failure'
    reason = 'client_failure'
    rateClaimsAdmissible = $false
    outputFile = $null
    requestHash = $null
    presentMonExitCode = $null
    startedUtc = $started.ToString('o')
    finishedUtc = $null
}
$lockStream = $null
$lockPath = $null
$exitCode = 20

try {
    $normalizedOutput = Resolve-PresentMonSidecarOutputPath -Path $OutputFile -AllowedRoot $AllowedOutputRoot
    $outcome.outputFile = $normalizedOutput
    foreach ($name in @('requests', 'claims', 'receipts', 'rejected')) {
        [IO.Directory]::CreateDirectory((Join-Path $MailboxRoot $name)) | Out-Null
    }

    $lockPath = Join-Path $MailboxRoot 'client.lock'
    if (Test-Path -LiteralPath $lockPath) {
        $age = [DateTimeOffset]::UtcNow - [DateTimeOffset](Get-Item -LiteralPath $lockPath).LastWriteTimeUtc
        if ($age.TotalSeconds -gt $StaleLockSeconds) {
            Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
        }
    }
    try {
        $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $lockBytes = [Text.UTF8Encoding]::new($false).GetBytes($requestId)
        $lockStream.Write($lockBytes, 0, $lockBytes.Length)
        $lockStream.Flush($true)
    } catch [IO.IOException] {
        throw 'mailbox_busy'
    }

    foreach ($staleCandidate in @(Get-ChildItem -LiteralPath (Join-Path $MailboxRoot 'requests') -Filter '*.request.json' -File -ErrorAction SilentlyContinue)) {
        try {
            $staleRequest = Read-PresentMonSidecarJson -Path $staleCandidate.FullName
            $staleExpiry = [DateTimeOffset]::MinValue
            if ([DateTimeOffset]::TryParse([string]$staleRequest.expiresUtc, [ref]$staleExpiry) -and $staleExpiry -le [DateTimeOffset]::UtcNow) {
                $rejectedName = $staleCandidate.BaseName + '.client-expired.json'
                [IO.File]::Move($staleCandidate.FullName, (Join-Path (Join-Path $MailboxRoot 'rejected') $rejectedName))
            }
        } catch {
            # Malformed or concurrently claimed requests remain visible below and
            # fail closed as mailbox_busy; the client never guesses ownership.
        }
    }
    $active = @(Get-ChildItem -LiteralPath (Join-Path $MailboxRoot 'requests') -Filter '*.request.json' -File -ErrorAction SilentlyContinue) +
        @(Get-ChildItem -LiteralPath (Join-Path $MailboxRoot 'claims') -Filter '*.request.json' -File -ErrorAction SilentlyContinue)
    if ($active.Count -gt 0) { throw 'mailbox_busy' }

    $requested = [DateTimeOffset]::UtcNow
    $request = [ordered]@{
        schema = 'mlvapp.presentmon-sidecar-request.v2'
        requestId = $requestId
        requestedUtc = $requested.ToString('o')
        expiresUtc = $requested.AddSeconds($RequestLifetimeSeconds).ToString('o')
        processName = $ProcessName
        outputFile = $normalizedOutput
        timedSeconds = $TimedSeconds
    }
    $requestHash = Get-PresentMonSidecarRequestHash -Request $request
    $request['requestHash'] = $requestHash
    $outcome.requestHash = $requestHash
    $requestPath = Join-Path (Join-Path $MailboxRoot 'requests') "$requestId.request.json"
    Write-PresentMonSidecarJsonAtomic -Value $request -Path $requestPath

    if ($TriggerScript) {
        & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $TriggerScript -RequestId $requestId
        if ($LASTEXITCODE -ne 0) { throw "sidecar_trigger_failed:$LASTEXITCODE" }
    } else {
        & schtasks.exe /Run /TN $ScheduledTaskName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "sidecar_trigger_failed:$LASTEXITCODE" }
    }

    $receiptPath = Join-Path (Join-Path $MailboxRoot 'receipts') "$requestId.done.json"
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($WaitTimeoutSeconds)
    while (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf) -and [DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'receipt_timeout' }
    $receipt = Read-PresentMonSidecarJson -Path $receiptPath
    if ([string]$receipt.requestId -cne $requestId -or [string]$receipt.requestHash -cne $requestHash) {
        throw 'receipt_identity_mismatch'
    }
    if ([string]$receipt.outputFile -ine $normalizedOutput -or [string]$receipt.processName -cne $ProcessName) {
        throw 'receipt_payload_mismatch'
    }
    $outcome.status = [string]$receipt.status
    $outcome.reason = [string]$receipt.reason
    $outcome.presentMonExitCode = $receipt.presentMonExitCode
    if ($receipt.status -eq 'done' -and [int]$receipt.presentMonExitCode -eq 0 -and (Test-Path -LiteralPath $normalizedOutput -PathType Leaf)) {
        $outcome.rateClaimsAdmissible = $true
        $exitCode = 0
    } else {
        $exitCode = 21
    }
} catch {
    $message = $_.Exception.Message
    $outcome.status = 'client_failure'
    $outcome.reason = $message
    if ($message -eq 'mailbox_busy') { $exitCode = 22 }
    elseif ($message -eq 'receipt_timeout') { $exitCode = 23 }
    elseif ($message -match '^receipt_') { $exitCode = 24 }
} finally {
    $outcome.finishedUtc = [DateTimeOffset]::UtcNow.ToString('o')
    $resultParent = Split-Path -Parent ([IO.Path]::GetFullPath($ResultPath))
    [IO.Directory]::CreateDirectory($resultParent) | Out-Null
    $temporaryResult = Join-Path $resultParent ('.' + [IO.Path]::GetFileName($ResultPath) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($temporaryResult, (($outcome | ConvertTo-Json -Depth 10) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryResult -Destination $ResultPath -Force
    if ($lockStream) { $lockStream.Dispose() }
    if ($lockPath -and (Test-Path -LiteralPath $lockPath)) { Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue }
}
exit $exitCode
