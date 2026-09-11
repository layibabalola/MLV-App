<#
.SYNOPSIS
    Bridge loop-queued cards to external factory (Adobe Document Cloud Ingester).

.DESCRIPTION
    Translates a queue card marked factory-bridged into a work order for the
    Adobe factory, invokes its dispatch, and consumes the receipt back into
    the local queue. Enables dogfooding: loop coordinates, factory implements.

.NOTES
    ASCII-only by project convention.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$QueueCardJson,

    [Parameter(Mandatory = $true)]
    [string]$LoopRoot,

    [Parameter(Mandatory = $true)]
    [string]$ExternalFactoryRoot = "C:\!Layi Wkspc\Adobe Document Cloud Ingester"
)

# Parse JSON card back to hashtable
$QueueCard = $QueueCardJson | ConvertFrom-Json -AsHashtable

$ErrorActionPreference = 'Stop'

# ============================================================ VALIDATION

if (-not (Test-Path -LiteralPath $LoopRoot)) {
    Write-Error "LoopRoot not found: $LoopRoot" -ErrorAction Stop
}

if (-not (Test-Path -LiteralPath $ExternalFactoryRoot)) {
    Write-Error "ExternalFactoryRoot not found: $ExternalFactoryRoot" -ErrorAction Stop
}

if (-not $QueueCard.cardId) {
    Write-Error "QueueCard missing cardId" -ErrorAction Stop
}

# ============================================================ TRANSLATE CARD TO WORK ORDER

$cardId = $QueueCard.cardId
$kind = $QueueCard.kind ?? 'product'
$scope = $QueueCard.scope -join ','

Write-Host "FACTORY-INTAKE: Translating card $cardId (kind=$kind, scope=$scope)"

# Factory work order schema (minimal bridge contract)
$workOrder = @{
    sourceCardId = $cardId
    sourceTrack = 'loop'
    sourceLoopRoot = $LoopRoot
    kind = $kind
    scope = $QueueCard.scope
    deliverable = $QueueCard.deliverable ?? ''
    procedure = $QueueCard.procedure ?? ''
    timestamp = (Get-Date -AsUTC -Format 'o')
    returnPath = Join-Path $LoopRoot '.claude-state' 'fleet-runs' 'factory-returns' $cardId
}

# Create return directory
$returnDir = $workOrder.returnPath
if (-not (Test-Path -LiteralPath $returnDir)) {
    New-Item -ItemType Directory -LiteralPath $returnDir -Force | Out-Null
}

# Write work order to factory intake directory
$factoryIntakeDir = Join-Path $ExternalFactoryRoot '.claude-state' 'coordination' 'loop-ingress'
if (-not (Test-Path -LiteralPath $factoryIntakeDir)) {
    New-Item -ItemType Directory -LiteralPath $factoryIntakeDir -Force | Out-Null
}

$workOrderFile = Join-Path $factoryIntakeDir "work-order-$cardId-$(Get-Date -AsUTC -Format 'yyyyMMddTHHmmssZ').json"
$workOrder | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $workOrderFile -Encoding UTF8NoBOM

Write-Host "FACTORY-INTAKE: Work order written to $workOrderFile"

# ============================================================ INVOKE FACTORY DISPATCH

Write-Host "FACTORY-INTAKE: Invoking factory dispatcher for $cardId"

$factoryDispatcher = Join-Path $ExternalFactoryRoot 'tools' 'coordination' 'Invoke-Workstream.ps1'
if (-not (Test-Path -LiteralPath $factoryDispatcher)) {
    Write-Error "Factory dispatcher not found at $factoryDispatcher" -ErrorAction Stop
}

# Note: Factory has its own lanes (Sol, Luna, Opus, Sonnet). We invoke its dispatcher
# with the translated card. Factory owns the execution.
& pwsh -NoProfile -File $factoryDispatcher `
    -QueueCardId $cardId `
    -LoopSourceRoot $LoopRoot `
    -ReturnPath $returnDir `
    -ErrorAction Stop

if ($LASTEXITCODE -ne 0) {
    Write-Host "FACTORY-INTAKE: Dispatcher exited $LASTEXITCODE for $cardId"
    exit $LASTEXITCODE
}

# ============================================================ CONSUME FACTORY RECEIPT

Write-Host "FACTORY-INTAKE: Waiting for factory receipt from $cardId"

$receiptFile = Join-Path $returnDir 'factory-receipt.json'
$maxWait = 300  # 5 minutes for factory to write receipt
$waited = 0

while (-not (Test-Path -LiteralPath $receiptFile) -and $waited -lt $maxWait) {
    Start-Sleep -Seconds 5
    $waited += 5
}

if (-not (Test-Path -LiteralPath $receiptFile)) {
    Write-Host "FACTORY-INTAKE: No receipt after ${maxWait}s — factory may still be processing or blocked"
    Write-Host "FACTORY-INTAKE: Card marked for retry; loop will check again on next cycle"
    exit 0  # Non-fatal; let loop re-check later
}

# Read receipt and update local queue state
$receipt = Get-Content -LiteralPath $receiptFile -Raw | ConvertFrom-Json

Write-Host "FACTORY-INTAKE: Received status=$($receipt.status) for $cardId"

if ($receipt.status -eq 'completed') {
    Write-Host "FACTORY-INTAKE: Card $cardId completed by factory; marking for local follow-up"
}
elseif ($receipt.status -eq 'blocked') {
    Write-Host "FACTORY-INTAKE: Card $cardId blocked in factory; diagnostics: $($receipt.blockReason)"
}
else {
    Write-Host "FACTORY-INTAKE: Card $cardId status=$($receipt.status)"
}

# Write return record for loop to consume
$returnRecord = @{
    cardId = $cardId
    factoryStatus = $receipt.status
    factoryReceiptPath = $receiptFile
    returnTimestamp = (Get-Date -AsUTC -Format 'o')
    action = switch ($receipt.status) {
        'completed' { 'move-to-landed' }
        'blocked' { 'mark-blocked-retry' }
        default { 'mark-waiting' }
    }
}

$returnRecordFile = Join-Path $returnDir 'return-record.json'
$returnRecord | ConvertTo-Json | Set-Content -LiteralPath $returnRecordFile -Encoding UTF8NoBOM

Write-Host "FACTORY-INTAKE: Return record written; card $cardId ready for loop follow-up"
Write-Host "FACTORY-INTAKE: $cardId complete, action=$($returnRecord.action)"
