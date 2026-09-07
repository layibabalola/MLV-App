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
# This card (0.4c-ii) implements the happy-path demotion: extracting the
# `factory-bridge-regressions` job body verbatim out of `.github/workflows/tests.yml` and
# into a standalone `.github/workflows/factory-bridge.yml`, deterministically and only after
# both gate receipts above validate. It never touches branch protection itself -- that is a
# separate, already-completed step (0.4b-i/0.4b-ii) -- and it never mutates any receipt.
#
# ASCII-only by project convention.

[CmdletBinding()]
param(
    [string]$BoardRoot = "C:\!Layi Wkspc\MLV-App",
    # Overrides the receipts directory outright (used by the unit tests, which point this at
    # a synthetic fixture directory rather than the real board's .claude-state).
    [string]$ReceiptsDir,
    # Root containing .github/workflows/tests.yml (source) and .github/workflows/
    # factory-bridge.yml (destination). Defaults to -BoardRoot so preview works
    # out-of-the-box against the real tree. -Apply refuses unless this is passed
    # EXPLICITLY, and refuses again if the explicit value is BoardRoot itself -- writing
    # into the shared board tree from this gate is never allowed; callers that mean to
    # apply for real must say so with a distinct, explicit path.
    [string]$RepoRoot,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

if (-not $PSBoundParameters.ContainsKey("ReceiptsDir") -or [string]::IsNullOrWhiteSpace($ReceiptsDir)) {
    $ReceiptsDir = Join-Path $BoardRoot ".claude-state\coordination\dual-lane\receipts"
}

$RepoRootWasExplicit = $PSBoundParameters.ContainsKey("RepoRoot") -and -not [string]::IsNullOrWhiteSpace($RepoRoot)
if (-not $RepoRootWasExplicit) {
    $RepoRoot = $BoardRoot
}

if ($Apply) {
    if (-not $RepoRootWasExplicit) {
        throw "demote-factory-bridge refused: -Apply requires an explicit -RepoRoot (distinct from the -BoardRoot default)"
    }
}

$GuardrailMoveReceiptName = "0.4c-guardrail-move.json"
$RequiredChecksReceiptName = "0.4b-required-checks.json"
$BlockedRequiredContext = "Factory Bridge Regressions"
$CanonicalPostContexts = @(
    "Repo Hygiene Python (windows-latest)",
    "Repo Hygiene Python (ubuntu-latest)",
    "Batch Compile",
    "Windows GUI Pilot",
    "Windows Product Oracles"
)
$JobName = "factory-bridge-regressions"

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
    must be a genuine JSON array of strings that does NOT contain "Factory Bridge
    Regressions" -- if it still does, GitHub still requires that job and demoting it here
    would strand a check that can never go green.

    NOTE: this receipt's `postContexts` is the branch-protection required-contexts list, and
    is NEVER compared for equality against the guardrail-move receipt's own reviewed-head
    hash, or against any "requiredchecksmergehead" value -- those are legitimately different
    heads (one is the head the branch-protection change was reviewed against, the other is
    the head the guardrail move's own CI run proved green against) and conflating them is a
    different bug, not a check this function performs.

    O172-adjacent lesson (sol round 1, PR #83): PowerShell's ConvertFrom-Json can collapse a
    single-element JSON array to a bare scalar, and the previous revision's `@(...)`
    coercion made an object-valued, scalar-valued, or empty postContexts pass silently
    instead of being refused as malformed. This version inspects the RAW JSON TEXT with
    System.Text.Json, never the ConvertFrom-Json result's .NET type, so the shape check is
    exact regardless of PowerShell version quirks.
    #>
    param(
        [Parameter(Mandatory = $true)]$Receipt,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$RawJson
    )
    $hasPostContexts = $Receipt.PSObject.Properties.Name -contains "postContexts"
    if (-not $hasPostContexts -or $null -eq $Receipt.postContexts) {
        throw "demote-factory-bridge refused: $RequiredChecksReceiptName is missing postContexts ($Path)"
    }

    try {
        $doc = [System.Text.Json.JsonDocument]::Parse($RawJson)
    } catch {
        throw "demote-factory-bridge refused: $RequiredChecksReceiptName does not parse as JSON for shape inspection ($Path)"
    }
    try {
        $root = $doc.RootElement
        $postContextsElement = $root.GetProperty("postContexts")
        if ($postContextsElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Array) {
            throw "demote-factory-bridge refused: $RequiredChecksReceiptName postContexts is not a JSON array ($Path)"
        }
        $elements = @($postContextsElement.EnumerateArray())
        if ($elements.Count -eq 0) {
            throw "demote-factory-bridge refused: $RequiredChecksReceiptName postContexts is an empty array ($Path)"
        }
        $values = New-Object System.Collections.Generic.List[string]
        foreach ($el in $elements) {
            if ($el.ValueKind -ne [System.Text.Json.JsonValueKind]::String) {
                throw "demote-factory-bridge refused: $RequiredChecksReceiptName postContexts contains a non-string element ($Path)"
            }
            $values.Add($el.GetString())
        }
    } finally {
        $doc.Dispose()
    }

    # Exact match (case-insensitive) is the ONLY accepted match. A value that only matches
    # after trimming whitespace is itself a malformed receipt and is refused, not
    # auto-corrected -- a data-quality problem in a receipt this gate trusts is exactly what
    # "fail closed" means here.
    foreach ($value in $values) {
        if ($value -ieq $BlockedRequiredContext) {
            throw "demote-factory-bridge refused: $RequiredChecksReceiptName still lists '$BlockedRequiredContext' in postContexts ($Path)"
        }
    }
    $trimmedNearMatches = @($values | Where-Object { $_.Trim() -ieq $BlockedRequiredContext -and $_ -cne $BlockedRequiredContext })
    if ($trimmedNearMatches.Count -gt 0) {
        throw "demote-factory-bridge refused: $RequiredChecksReceiptName postContexts contains a whitespace near-match for '$BlockedRequiredContext' ($Path)"
    }
    if ($values.Count -ne $CanonicalPostContexts.Count) {
        throw "demote-factory-bridge refused: $RequiredChecksReceiptName postContexts is not the exact canonical five-context list ($Path)"
    }
    for ($i = 0; $i -lt $CanonicalPostContexts.Count; $i++) {
        if ($values[$i] -cne $CanonicalPostContexts[$i]) {
            throw "demote-factory-bridge refused: $RequiredChecksReceiptName postContexts is not the exact ordered canonical five-context list ($Path)"
        }
    }
}

# --- Deterministic local job extraction (replaces the former happy-path stub) -------------

function Get-ColumnTwoKeyBoundaries {
    <#
    Returns, in file order, the 0-based line index of every line that is EXACTLY a
    2-space-indented YAML mapping key with no inline value (a job name under `jobs:`).
    Used both to find the requested job's own start line and to find where the NEXT
    sibling job begins (the exclusive end of the requested job's block).
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines)
    $boundaries = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match '^  ([A-Za-z0-9_.-]+):\s*$') {
            $boundaries.Add([pscustomobject]@{ Name = $Matches[1]; Line = $i })
        }
    }
    return $boundaries
}

function Get-JobBlock {
    <#
    Extracts the exact source lines for job $Name out of $Lines: its own preceding
    contiguous "  #" comment lines (if any, with no blank-line break), through the line
    immediately before the next column-2 job key (or EOF). Trailing blank lines are
    trimmed. Throws if the job appears more than once (a duplicate-job source file).
    Returns $null (not an error) if the job is simply absent.
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines, [Parameter(Mandatory = $true)][string]$Name)
    $boundaries = Get-ColumnTwoKeyBoundaries -Lines $Lines
    $matches = @($boundaries | Where-Object { $_.Name -eq $Name })
    if ($matches.Count -eq 0) {
        return $null
    }
    if ($matches.Count -gt 1) {
        throw "demote-factory-bridge refused: source contains $($matches.Count) occurrences of job '$Name' (duplicate job)"
    }
    $jobLine = $matches[0].Line
    $start = $jobLine
    while ($start -gt 0 -and $Lines[$start - 1] -match '^  #') {
        $start--
    }
    $laterBoundaries = @($boundaries | Where-Object { $_.Line -gt $jobLine } | Sort-Object Line)
    if ($laterBoundaries.Count -gt 0) {
        $end = $laterBoundaries[0].Line
    } else {
        $end = $Lines.Count
    }
    while ($end -gt $start -and $Lines[$end - 1].Trim() -eq "") {
        $end--
    }
    return $Lines[$start..($end - 1)]
}

$script:CanonicalHeader = @(
    "name: Factory Bridge",
    "",
    "on:",
    "  workflow_dispatch:",
    "  pull_request:",
    "  push:",
    "    branches:",
    "      - master",
    "",
    "permissions:",
    "  contents: read",
    "",
    "concurrency:",
    "  group: factory-bridge-`${{ github.workflow }}-`${{ github.event_name }}-`${{ github.event_name == 'workflow_dispatch' && github.run_id || github.ref }}",
    "  cancel-in-progress: `${{ github.event_name != 'workflow_dispatch' }}",
    "",
    "jobs:"
) -join "`n"

$script:RequiredStepNames = @(
    "Set up Python",
    "Install factory bridge dependencies",
    "Verify compatible MCP runtime",
    "Run agent bridge PowerShell launcher regressions",
    "Run full agent-bridge test suite",
    "Run profiling test suite"
)

$script:PinnedJobBodySha256 = "fbbed158292b0e8928e1d77df6e2559d273ed66888657f49a97fc9a8a7e9a9da"

$script:SafePins = @(
    "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
    "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
)

function New-DestinationContent {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$JobLines)
    $jobText = ($JobLines -join "`n")
    return "$script:CanonicalHeader`n$jobText`n"
}

function Get-LfSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    $normalized = ($Text -replace "`r`n", "`n") -replace "`r", "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($normalized)
    return [System.BitConverter]::ToString(
        [System.Security.Cryptography.SHA256]::HashData($bytes)
    ).Replace("-", "").ToLowerInvariant()
}

function Test-DestinationIsCompletedMigration {
    param([Parameter(Mandatory = $true)][string]$Text, [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines)
    $normalized = (($Text -replace "`r`n", "`n") -replace "`r", "`n")
    $prefix = "$script:CanonicalHeader`n"
    if (-not $normalized.StartsWith($prefix, [StringComparison]::Ordinal)) { return $false }
    $jobSection = $normalized.Substring($prefix.Length)
    $jobSectionLines = $jobSection -split "`n"
    $jobLines = Get-JobBlock -Lines $jobSectionLines -Name $JobName
    if ($null -eq $jobLines) { return $false }
    $boundaries = Get-ColumnTwoKeyBoundaries -Lines $jobSectionLines
    if (@($boundaries).Count -ne 1 -or $boundaries[0].Name -cne $JobName) { return $false }
    $jobText = (($jobLines -join "`n").TrimEnd("`r", "`n")) + "`n"
    if ((Get-LfSha256 -Text $jobText) -cne $script:PinnedJobBodySha256) { return $false }
    return $normalized -ceq "$prefix$jobText"
}

function Get-InputSnapshot {
    param([Parameter(Mandatory = $true)][string]$Path, [switch]$AllowAbsent)
    if (-not (Test-Path -LiteralPath $Path)) {
        if ($AllowAbsent) { return [pscustomobject]@{ Exists=$false; Sha256=$null } }
        throw "demote-factory-bridge refused: required input absent ($Path)"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "demote-factory-bridge refused: input is not a file ($Path)"
    }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    return [pscustomobject]@{ Exists=$true; Sha256=([BitConverter]::ToString([Security.Cryptography.SHA256]::HashData($bytes)).Replace('-', '').ToLowerInvariant()) }
}

function Assert-InputUnchanged {
    param([string]$Path, $Snapshot, [string]$Label)
    $now = Get-InputSnapshot -Path $Path -AllowAbsent
    if ($now.Exists -ne $Snapshot.Exists -or $now.Sha256 -cne $Snapshot.Sha256) {
        throw "demote-factory-bridge refused: $Label changed after validation ($Path); no further writes performed"
    }
}

function Assert-SafeApplyRoot {
    param([string]$RepoRoot, [string]$BoardRoot)
    $repo = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path.TrimEnd('\','/')
    $board = (Resolve-Path -LiteralPath $BoardRoot -ErrorAction Stop).Path.TrimEnd('\','/')
    if ($repo -ieq $board) { throw "demote-factory-bridge refuses -RepoRoot equal to -BoardRoot after path resolution" }
    $toInspect = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($leaf in @($repo, (Join-Path $repo '.github'), (Join-Path $repo '.github\workflows'))) {
        $cursor = $leaf
        while ($cursor) {
            if (Test-Path -LiteralPath $cursor) { [void]$toInspect.Add($cursor) }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { break }
            $cursor = $parent
        }
    }
    foreach ($candidate in $toInspect) {
        $item = Get-Item -LiteralPath $candidate -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "demote-factory-bridge refused: reparse-point path or ancestor is not allowed for apply ($candidate)"
        }
    }
    return $repo
}

function Write-AtomicFile {
    <#
    Writes $Content to $Path via a sibling temp file plus atomic rename, so a crash or
    partial-write mid-flight never leaves $Path holding truncated or corrupt content --
    $Path either still holds its ORIGINAL bytes, or the COMPLETE new bytes, never a partial
    write. The sibling temp file is always cleaned up, on both success and failure.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Content)
    if ((Test-Path -LiteralPath $Path) -and -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "demote-factory-bridge failed: cannot write '$Path' -- an existing non-file (e.g. a directory) occupies that path"
    }
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $tempPath = Join-Path $directory ((Split-Path -Leaf $Path) + ".demote-tmp-" + [System.Guid]::NewGuid().ToString("N"))
    try {
        [System.IO.File]::WriteAllText($tempPath, $Content, (New-Object System.Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $tempPath -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-JobDemotion {
    <#
    Computes both the source-with-job-removed text and the destination text BEFORE any
    write happens, rejecting duplicate/ambiguous/conflicting states first. Only once both
    outputs are known-good does it write -- destination FIRST, then source removal -- so a
    failure partway through never loses the only copy of the job. Returns a result object
    describing what happened (or would happen, in preview) and which files actually
    changed, for a caller that must report the truth on partial failure.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][bool]$Apply,
        [Parameter(Mandatory = $true)][string]$GuardrailReceiptPath,
        [Parameter(Mandatory = $true)]$GuardrailReceiptSnapshot,
        [Parameter(Mandatory = $true)][string]$RequiredReceiptPath,
        [Parameter(Mandatory = $true)]$RequiredReceiptSnapshot
    )
    $sourcePath = Join-Path $RepoRoot ".github\workflows\tests.yml"
    $destPath = Join-Path $RepoRoot ".github\workflows\factory-bridge.yml"
    $changedFiles = New-Object System.Collections.Generic.List[string]
    $sourceSnapshot = Get-InputSnapshot -Path $sourcePath
    $destSnapshot = Get-InputSnapshot -Path $destPath -AllowAbsent

    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "demote-factory-bridge refused: source workflow is absent ($sourcePath)"
    }
    $sourceText = Get-Content -LiteralPath $sourcePath -Raw -ErrorAction Stop
    $sourceLines = $sourceText -split "`r?`n"
    $jobLines = Get-JobBlock -Lines $sourceLines -Name $JobName

    $destExists = Test-Path -LiteralPath $destPath -PathType Leaf
    $destText = $null
    $destLines = $null
    $destIsCompleted = $false
    if ($destExists) {
        $destText = Get-Content -LiteralPath $destPath -Raw -ErrorAction Stop
        $destLines = $destText -split "`r?`n"
        $destIsCompleted = Test-DestinationIsCompletedMigration -Text $destText -Lines $destLines
    }

    if ($null -eq $jobLines) {
        if ($destIsCompleted) {
            Assert-InputUnchanged -Path $GuardrailReceiptPath -Snapshot $GuardrailReceiptSnapshot -Label "guardrail receipt"
            Assert-InputUnchanged -Path $RequiredReceiptPath -Snapshot $RequiredReceiptSnapshot -Label "required-checks receipt"
            Assert-InputUnchanged -Path $sourcePath -Snapshot $sourceSnapshot -Label "source workflow"
            Assert-InputUnchanged -Path $destPath -Snapshot $destSnapshot -Label "destination workflow"
            return [pscustomobject]@{
                Status        = "already-migrated"
                Message       = "demote-factory-bridge: already-migrated; source no longer has '$JobName'; destination is the exact pinned migration; no writes needed."
                ChangedFiles  = @()
                DestPath      = $destPath
            }
        }
        throw "demote-factory-bridge refused: source has no '$JobName' job and destination is not a valid completed migration ($destPath)"
    }

    if ($destIsCompleted) {
        # The source still carries the job even though the destination already looks like
        # the finished migration -- an ambiguous, stale state that must be reconciled by a
        # human/recheck rather than guessed at here.
        throw "demote-factory-bridge refused: stale state -- source still contains '$JobName' but destination already looks migrated; rerun after reconciling ($destPath)"
    }

    $normalizedJobText = (($jobLines -join "`n").TrimEnd("`r", "`n")) + "`n"
    $sourceJobSha = Get-LfSha256 -Text $normalizedJobText
    if ($sourceJobSha -cne $script:PinnedJobBodySha256) {
        throw "demote-factory-bridge refused: source bridge job differs from pinned fd8 body (actual=$sourceJobSha expected=$script:PinnedJobBodySha256)"
    }
    $computedDestText = New-DestinationContent -JobLines $jobLines

    if ($destExists -and $destText -ne $computedDestText) {
        throw "demote-factory-bridge refused: destination already exists with content that is neither the computed migration nor a recognized completed migration ($destPath)"
    }

    $destUnchanged = $destExists -and ($destText -eq $computedDestText)

    $sourceBoundaries = Get-ColumnTwoKeyBoundaries -Lines $sourceLines
    $jobBoundary = @($sourceBoundaries | Where-Object { $_.Name -eq $JobName })[0]
    $laterBoundaries = @($sourceBoundaries | Where-Object { $_.Line -gt $jobBoundary.Line } | Sort-Object Line)
    $start = $jobBoundary.Line
    while ($start -gt 0 -and $sourceLines[$start - 1] -match '^  #') { $start-- }
    if ($laterBoundaries.Count -gt 0) { $end = $laterBoundaries[0].Line } else { $end = $sourceLines.Count }
    $remainingLines = @()
    if ($start -gt 0) { $remainingLines += $sourceLines[0..($start - 1)] }
    if ($end -lt $sourceLines.Count) { $remainingLines += $sourceLines[$end..($sourceLines.Count - 1)] }
    # Preserve unrelated source text; do not reformat blank lines in other jobs.
    $computedSourceText = $remainingLines -join "`n"
    if (-not $computedSourceText.EndsWith("`n")) { $computedSourceText += "`n" }

    if (-not $Apply) {
        $destHash = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($computedDestText))).Replace("-", "").ToLowerInvariant()
        $sourceHash = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($computedSourceText))).Replace("-", "").ToLowerInvariant()
        return [pscustomobject]@{
            Status       = "preview"
            Message      = "demote-factory-bridge preview: would write $destPath (sha256=$destHash) and rewrite $sourcePath (sha256=$sourceHash); no files were changed."
            ChangedFiles = @()
            DestPath     = $destPath
            SourcePath   = $sourcePath
        }
    }

    Assert-InputUnchanged -Path $GuardrailReceiptPath -Snapshot $GuardrailReceiptSnapshot -Label "guardrail receipt"
    Assert-InputUnchanged -Path $RequiredReceiptPath -Snapshot $RequiredReceiptSnapshot -Label "required-checks receipt"
    Assert-InputUnchanged -Path $sourcePath -Snapshot $sourceSnapshot -Label "source workflow"
    Assert-InputUnchanged -Path $destPath -Snapshot $destSnapshot -Label "destination workflow"
    try {
        if (-not $destUnchanged) {
            Write-AtomicFile -Path $destPath -Content $computedDestText
            $changedFiles.Add($destPath)
        }
        Write-AtomicFile -Path $sourcePath -Content $computedSourceText
        $changedFiles.Add($sourcePath)
    } catch {
        $changed = if ($changedFiles.Count) { $changedFiles -join ', ' } else { '(none)' }
        throw "demote-factory-bridge partial failure: changed files=$changed; destination-first ordering preserves a complete job copy. $($_.Exception.Message)"
    }
    $finalDest = [IO.File]::ReadAllText($destPath)
    $finalSource = [IO.File]::ReadAllText($sourcePath)
    if (-not (Test-DestinationIsCompletedMigration -Text $finalDest -Lines ($finalDest -split "`r?`n")) -or $null -ne (Get-JobBlock -Lines ($finalSource -split "`r?`n") -Name $JobName)) {
        throw "demote-factory-bridge partial failure: changed files=$($changedFiles -join ', '); post-write verification failed"
    }

    return [pscustomobject]@{
        Status       = "applied"
        Message      = "demote-factory-bridge: extracted '$JobName' into $destPath and removed it from $sourcePath."
        ChangedFiles = @($changedFiles)
        DestPath     = $destPath
        SourcePath   = $sourcePath
    }
}

$guardrailMovePath = Join-Path $ReceiptsDir $GuardrailMoveReceiptName
$requiredChecksPath = Join-Path $ReceiptsDir $RequiredChecksReceiptName

if ($Apply) { $RepoRoot = Assert-SafeApplyRoot -RepoRoot $RepoRoot -BoardRoot $BoardRoot }
$guardrailSnapshot = if (Test-Path -LiteralPath $guardrailMovePath -PathType Leaf) { Get-InputSnapshot -Path $guardrailMovePath } else { $null }
$guardrailMoveReceipt = Read-JsonObjectReceipt -Path $guardrailMovePath -Name $GuardrailMoveReceiptName
Assert-GuardrailMoveReceiptIsSuccess -Receipt $guardrailMoveReceipt -Path $guardrailMovePath

$requiredSnapshot = if (Test-Path -LiteralPath $requiredChecksPath -PathType Leaf) { Get-InputSnapshot -Path $requiredChecksPath } else { $null }
$requiredChecksReceipt = Read-JsonObjectReceipt -Path $requiredChecksPath -Name $RequiredChecksReceiptName
$requiredChecksRawJson = Get-Content -LiteralPath $requiredChecksPath -Raw -ErrorAction Stop
Assert-RequiredChecksReceiptDropsBridgeContext -Receipt $requiredChecksReceipt -Path $requiredChecksPath -RawJson $requiredChecksRawJson

Write-Output "demote-factory-bridge: both receipts validate ($GuardrailMoveReceiptName conclusion=success; $RequiredChecksReceiptName postContexts drops '$BlockedRequiredContext')."

Assert-InputUnchanged -Path $guardrailMovePath -Snapshot $guardrailSnapshot -Label "guardrail receipt"
Assert-InputUnchanged -Path $requiredChecksPath -Snapshot $requiredSnapshot -Label "required-checks receipt"
try {
    $result = Invoke-JobDemotion -RepoRoot $RepoRoot -Apply:$Apply.IsPresent `
        -GuardrailReceiptPath $guardrailMovePath -GuardrailReceiptSnapshot $guardrailSnapshot `
        -RequiredReceiptPath $requiredChecksPath -RequiredReceiptSnapshot $requiredSnapshot
} catch {
    # Name whatever was ACTUALLY changed before the failure -- destination-first order means
    # this is either nothing, or the destination alone (source removal, the last step, never
    # ran) -- never a partially-written file, since Write-AtomicFile only ever leaves a
    # complete file at its target path.
    throw
}

Write-Output $result.Message
if ($result.ChangedFiles.Count -gt 0) {
    Write-Output "demote-factory-bridge: changed files: $($result.ChangedFiles -join ', ')"
} else {
    Write-Output "demote-factory-bridge: changed files: (none)"
}
