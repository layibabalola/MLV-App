# demote-factory-bridge.ps1 -- the fail-closed gate for plan step 0.4c-ii (demoting
# `factory-bridge-regressions` from a REQUIRED check to a non-blocking weekly-digest job).
#
# WHY THIS REFUSES BEFORE TOUCHING ANYTHING: the coordination guardrail suite that used to
# be `factory-bridge-regressions`'s only genuinely-required signal moved to
# `repo-hygiene-python` in plan step 0.4c-i (CI-GUARDRAIL-MOVE-1). Demoting
# `factory-bridge-regressions` out of the required-checks set is only safe once that move
# has been PROVEN on hosted CI (the 0.4c-guardrail-move.json receipt) AND the branch
# protection required-contexts list has ALREADY dropped "Factory Bridge Regressions" as an
# independent step (the 0.4b-required-checks.json receipt, plan steps 0.4b-i/0.4b-ii).
# Without both, this script would either demote a job that is still the sole source of the
# guardrail signal, or demote a job GitHub still requires -- either one reopens the gap the
# whole plan sequence exists to close.
#
# This card (0.4c-i, CI-GUARDRAIL-MOVE-1) creates this script and proves ONLY its refusal
# paths. `0.4b-required-checks.json` genuinely does not exist yet at this point in the
# plan's fixed execution order (0.4a-i -> 0.4a-ii -> 0.4c-i -> 0.4b-i -> 0.4b-ii -> 0.4c-ii),
# so the happy path below -- reached only once both receipts validate -- is written but
# deliberately left a stub and is NOT exercised by this card's tests.
#
# ASCII-only by project convention.

[CmdletBinding()]
param(
    [string]$BoardRoot = "C:\!Layi Wkspc\MLV-App",
    # Overrides the receipts directory outright (used by the unit tests, which point this at
    # a synthetic fixture directory rather than the real board's .claude-state).
    [string]$ReceiptsDir
)

$ErrorActionPreference = "Stop"

if (-not $PSBoundParameters.ContainsKey("ReceiptsDir") -or [string]::IsNullOrWhiteSpace($ReceiptsDir)) {
    $ReceiptsDir = Join-Path $BoardRoot ".claude-state\coordination\dual-lane\receipts"
}

$GuardrailMoveReceiptName = "0.4c-guardrail-move.json"
$RequiredChecksReceiptName = "0.4b-required-checks.json"
$BlockedRequiredContext = "Factory Bridge Regressions"

function Read-JsonObjectReceipt {
    <#
    Reads $Path as JSON and requires it to parse to a non-null, non-array object. Any
    failure -- missing file, unreadable file, invalid JSON, or a JSON value that is not an
    object (an array, a scalar, null) -- throws, naming the receipt and the path, and never
    returns a partial result for the caller to reason about.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "demote-factory-bridge refused: $Name is absent (expected at $Path)"
    }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    } catch {
        throw "demote-factory-bridge refused: $Name could not be read ($Path): $($_.Exception.Message)"
    }
    try {
        $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "demote-factory-bridge refused: $Name does not parse as JSON ($Path)"
    }
    if ($null -eq $parsed -or $parsed -isnot [System.Management.Automation.PSCustomObject]) {
        throw "demote-factory-bridge refused: $Name is not a JSON object ($Path)"
    }
    return $parsed
}

function Assert-GuardrailMoveReceiptIsSuccess {
    <#
    0.4c-guardrail-move.json is THIS card's own acceptance receipt -- the hub writes it
    after this PR merges and hosted CI proves the moved guardrail step green inside
    `Repo Hygiene Python (windows-latest)`. It must carry a `conclusion` key equal to the
    exact string "success"; anything else (the key absent, a different value, a non-string
    value) is refused.
    #>
    param([Parameter(Mandatory = $true)]$Receipt, [Parameter(Mandatory = $true)][string]$Path)
    $hasConclusion = $Receipt.PSObject.Properties.Name -contains "conclusion"
    if (-not $hasConclusion -or $Receipt.conclusion -isnot [string] -or $Receipt.conclusion -cne "success") {
        throw "demote-factory-bridge refused: $GuardrailMoveReceiptName does not show conclusion=success ($Path)"
    }
}

function Assert-RequiredChecksReceiptDropsBridgeContext {
    <#
    0.4b-required-checks.json is a LATER plan step's receipt (0.4b-i/0.4b-ii). Its
    `postContexts` field is the required-status-checks list AFTER that step landed, and it
    must NOT contain "Factory Bridge Regressions" -- if it still does, GitHub still requires
    that job and demoting it here would strand a check that can never go green (the job
    keeps running, but nothing in it is genuinely required signal after 0.4c-i moved the one
    suite that was).
    #>
    param([Parameter(Mandatory = $true)]$Receipt, [Parameter(Mandatory = $true)][string]$Path)
    $hasPostContexts = $Receipt.PSObject.Properties.Name -contains "postContexts"
    if (-not $hasPostContexts -or $null -eq $Receipt.postContexts) {
        throw "demote-factory-bridge refused: $RequiredChecksReceiptName is missing postContexts ($Path)"
    }
    # ConvertFrom-Json returns a scalar (not an array) for a single-element JSON array in
    # some PowerShell versions; @(...) normalises every shape -- a scalar, an array, an
    # empty array -- to a PowerShell array so -contains below is always well-defined.
    $postContexts = @($Receipt.postContexts)
    if ($postContexts -contains $BlockedRequiredContext) {
        throw "demote-factory-bridge refused: $RequiredChecksReceiptName still lists '$BlockedRequiredContext' in postContexts ($Path)"
    }
}

$guardrailMovePath = Join-Path $ReceiptsDir $GuardrailMoveReceiptName
$requiredChecksPath = Join-Path $ReceiptsDir $RequiredChecksReceiptName

$guardrailMoveReceipt = Read-JsonObjectReceipt -Path $guardrailMovePath -Name $GuardrailMoveReceiptName
Assert-GuardrailMoveReceiptIsSuccess -Receipt $guardrailMoveReceipt -Path $guardrailMovePath

$requiredChecksReceipt = Read-JsonObjectReceipt -Path $requiredChecksPath -Name $RequiredChecksReceiptName
Assert-RequiredChecksReceiptDropsBridgeContext -Receipt $requiredChecksReceipt -Path $requiredChecksPath

Write-Output "demote-factory-bridge: both receipts validate ($GuardrailMoveReceiptName conclusion=success; $RequiredChecksReceiptName postContexts drops '$BlockedRequiredContext')."

# --- Happy-path demotion (STUB -- written but NOT exercised by this card's tests) ---------
# Reached only once both gate receipts above have validated. The actual demotion (editing
# .github/workflows/tests.yml so `factory-bridge-regressions` is no longer in the branch
# protection required-contexts list, and recording the result) is plan step 0.4c-ii's job
# and is deliberately left unimplemented here: CI-GUARDRAIL-MOVE-1 (0.4c-i) cannot exercise
# it, because 0.4b-required-checks.json does not exist yet at this point in the plan's fixed
# execution order. This script touches NO workflow file past this point.
Write-Output "demote-factory-bridge: demotion logic is not yet implemented (pending plan step 0.4c-ii); no workflow file was touched."
