<#
.SYNOPSIS
    Pick the next actionable card on a workstream, build a COMPACT self-contained
    lane brief, and dispatch a bounded lane. No seat, no lease, no queue mutation.

.DESCRIPTION
    THIS SCRIPT EXISTS BECAUSE OF A MEASURED COST, NOT A STYLE PREFERENCE.

    On 2026-08-31 the same card was dispatched twice to the same lane:

      run 1  prompt 5.7 KB, no capability line, no reading budget
             -> 16 turns, 1,956,254 cache-creation tokens, DIED prompt_too_long, $51.61
      run 2  prompt 7.0 KB, facts inlined, byte-sized reading budget, capability stated
             -> 3 turns, 40,759 cache-creation tokens, $1.18

    A 48x reduction in tokens ingested and 44x in cost, from prompt shape alone.
    The lane in run 1 spent its context reading this board's own coordination
    surface, which is ~1.6 MB:
        .claude-state\coordination\gpu-lane-impl-review-sync.md   ~1.34 MB
        .claude-state\coordination\dual-lane\queue.json           ~270 KB

    So the token economy is NOT about the conversation. It is about never letting
    bulk state reach a model. This script reads the bulk itself, in PowerShell,
    and hands the lane a few KB with the facts already in it.

    THREE THINGS THE BRIEF ALWAYS CARRIES, because each was a measured failure:
      1. THE LANE'S REAL TOOLSET. Invoke-Lane grants tools BY ENGINE. A claude
         lane without -AllowEdits gets Read,Grep,Glob and NO SHELL; a codex lane
         gets ALL tools under a read-only sandbox and CAN execute. A prompt that
         says "prove by execution" is unsatisfiable for the former, and nothing
         told it so.
      2. A READING BUDGET WITH BYTE SIZES, so a lane knows which files will eat
         its context before it opens one.
      3. THE FACTS INLINED. The dispatcher already holds them; making the lane
         re-derive them is what costs the 1.9M tokens.

    QUEUE IS NEVER MUTATED. RESUME.md STEP 4/5 bars it under the recovery
    authority. Dispatch records go to their own append-only log.

.NOTES
    ASCII-only by project convention. The lane/model table lives in
    Invoke-Lane.ps1 and is deliberately NOT duplicated here.
#>
[CmdletBinding()]
param(
    [ValidateSet('factory','playback','product','continuity','fleet','gate','UNSET','auto')]
    [string]$Track = 'auto',

    [string]$CardId,

    [switch]$DryRun,

    [ValidateSet('opus','sonnet','fable','sol','luna')]
    [string]$Lane,

    [int]$TimeoutSec = 1800,

    [switch]$Force,
    [int]$StaleHours = 12
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot   = 'C:\!Layi Wkspc\MLV-App'
$DualLane   = Join-Path $RepoRoot '.claude-state\coordination\dual-lane'
$QueuePath  = Join-Path $DualLane 'queue.json'
$LogPath    = Join-Path $DualLane 'workstream-dispatch-log.jsonl'
$PromptDir  = Join-Path $RepoRoot '.claude-state\fleet-runs\prompts'
$LaneRunner = Join-Path $RepoRoot 'tools\coordination\Invoke-Lane.ps1'

foreach ($p in @($QueuePath, $LaneRunner)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Output "WORKSTREAM: CANNOT-DETERMINE - missing $p"
        exit 3
    }
}
if (-not (Test-Path -LiteralPath $PromptDir)) {
    New-Item -ItemType Directory -Path $PromptDir -Force | Out-Null
}

# A card in one of these states is done. Anything else is live work.
$Terminal = @(
    'closed-fixed','closed-not-this-board','closed-root-caused','closed-superseded',
    'closed-transformed','landed','landed-evidence','landed-local-proof','CLEARED',
    'RETIRED','withdrawn','superseded','retracted-and-fixed','fixed','answered-folded'
)

function Get-Prop($obj, [string]$name) {
    if ($null -eq $obj) { return $null }
    $p = $obj.PSObject.Properties[$name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function Get-Track($item) {
    $t = Get-Prop $item 'track'
    if (-not $t) { return 'UNSET' }
    return $t
}

$queue = Get-Content -LiteralPath $QueuePath -Raw | ConvertFrom-Json
$items = @($queue.items)
$live  = @($items | Where-Object { $Terminal -notcontains (Get-Prop $_ 'state') })

# ------------------------------------------------------------------ dispatch history
$history = @{}
$malformed = 0
if (Test-Path -LiteralPath $LogPath) {
    foreach ($line in (Get-Content -LiteralPath $LogPath)) {
        if (-not $line.Trim()) { continue }
        try {
            $row = $line | ConvertFrom-Json
            $id = Get-Prop $row 'cardId'
            if ($id) { $history[$id] = $row }
        } catch { $malformed++ }
    }
}
# A malformed row is COUNTED, never silently treated as "no dispatch". Absent and
# unreadable are different facts and this script will not merge them.
if ($malformed -gt 0) { Write-Output "WORKSTREAM: WARNING - $malformed malformed dispatch-log row(s) skipped" }

function Get-LastDispatchAgeHours([string]$id) {
    if (-not $history.ContainsKey($id)) { return $null }
    $t = Get-Prop $history[$id] 'dispatchedUtc'
    if (-not $t) { return $null }
    try {
        return ((Get-Date).ToUniversalTime() - ([datetime]::Parse($t)).ToUniversalTime()).TotalHours
    } catch { return $null }
}

# ------------------------------------------------------------------ selection
function Get-Rank($item) {
    # Unset priority sorts LAST, so an unprioritised card never outranks a p1.
    $pr = Get-Prop $item 'priority'
    if ($null -eq $pr) { return 999 }
    return [int]$pr
}

if ($CardId) {
    $candidates = @($items | Where-Object { (Get-Prop $_ 'id') -eq $CardId })
    if ($candidates.Count -eq 0) {
        Write-Output "WORKSTREAM: CANNOT-DETERMINE - no card with id '$CardId'"
        exit 3
    }
} else {
    $pool = $live
    if ($Track -ne 'auto') {
        $pool = @($live | Where-Object { (Get-Track $_) -eq $Track })
    }
    if (-not $Force) {
        $pool = @($pool | Where-Object {
            $age = Get-LastDispatchAgeHours (Get-Prop $_ 'id')
            ($null -eq $age) -or ($age -ge $StaleHours)
        })
    }
    $candidates = @($pool | Sort-Object -Property `
        @{ Expression = { Get-Rank $_ } }, `
        @{ Expression = { Get-Prop $_ 'id' } })
}

if ($candidates.Count -eq 0) {
    # THREE OUTCOMES, NEVER TWO. "no card exists" and "every card was filtered out"
    # are different facts about the board and this script will not merge them - an
    # empty track is a GAP to be filled, a fully-dispatched track is HEALTHY.
    $onTrackLive = if ($Track -eq 'auto') { $live.Count }
                   else { @($live | Where-Object { (Get-Track $_) -eq $Track }).Count }
    if ($onTrackLive -eq 0) {
        Write-Output "WORKSTREAM: NO-LIVE-CARDS on track '$Track'. This track has ZERO non-terminal work - it is not idle, it is EMPTY. That is a backlog gap, not a healthy queue. (live overall = $($live.Count))"
        exit 4
    }
    Write-Output "WORKSTREAM: ALL-RECENTLY-DISPATCHED on track '$Track' - $onTrackLive live card(s), every one carrying a dispatch newer than $StaleHours h. Use -Force or lower -StaleHours to re-dispatch."
    exit 0
}

$card      = $candidates[0]
$cardId    = Get-Prop $card 'id'
$cardTrack = Get-Track $card

# ------------------------------------------------------------------ engine choice
# Cards asking for derivation or measurement need a SHELL. Only codex lanes have
# one when invoked read-only, so route those to codex and pure analysis to claude.
$cardText   = ($card | ConvertTo-Json -Depth 8)
$needsShell = $cardText -match '(?i)re-derive|derive|measure|reproduce|proving command|prove by|verify by execution|run the'
if (-not $Lane) { $Lane = if ($needsShell) { 'luna' } else { 'fable' } }
$engine = if ($Lane -eq 'sol' -or $Lane -eq 'luna') { 'codex' } else { 'claude' }

# ------------------------------------------------------------------ the brief
if ($engine -eq 'codex') {
    $capability = @'
You are invoked READ-ONLY on the codex engine: ALL tools under a `read-only` sandbox.
YOU CAN EXECUTE (git, python, pwsh) but CANNOT write files. Prove by EXECUTION and PRINT the
ref you bound to. Run a FALSIFIER beside every subject check - a control and a subject that
return the same reason prove nothing.
'@
} else {
    $capability = @'
You are invoked READ-ONLY on a claude engine. YOUR ONLY TOOLS ARE `Read`, `Grep`, `Glob`.
YOU HAVE NO SHELL - `Bash` and `PowerShell` calls are DENIED by the runner, so no git, no
python, no pwsh. The board's standing "prove by EXECUTION" rule is UNSATISFIABLE for you on
this invocation; that is a known runner asymmetry, not your failing. Substitute: cite a FILE
PATH AND LINE via Grep/Read, and label anything you could not check
CANNOT-VERIFY-WITHOUT-SHELL. Never fold CANNOT-VERIFY into a pass.
'@
}

$cardJson = $card | ConvertTo-Json -Depth 8
$fence    = '```'

$brief = @"
# LANE BRIEF - card $cardId (track: $cardTrack)

## YOUR CAPABILITIES ON THIS INVOCATION - read before planning
$capability

## HARD READING BUDGET - a previous lane died of prompt_too_long after 16 turns and 51 dollars
It blew its context reading this board's coordination surface whole. NEVER read these without a
narrow offset/limit, or a grep-then-slice:
    .claude-state\coordination\gpu-lane-impl-review-sync.md   ~1.34 MB
    .claude-state\coordination\dual-lane\queue.json           ~270 KB
    .claude-state\RESUME.md                                   ~27 KB (fine to read once)
THE CARD IS INLINED BELOW IN FULL. You do not need to open the queue. Budget ~12 tool calls.

## STANDING OPERATOR RULING (2026-08-31), binding on your output
Layi, verbatim: "use the wisdom of the hub lanes to adjudicate decisions rather than ask me my
opinion. I am not qualified." DO NOT return "ask Layi". Return a DECISION. If some part is
genuinely operator-only you must be able to write this line in full, or it is not operator-only:
    BLOCKED ON Layi (<action>) -- delegation check: <lane> <why not> ... Operator-only because
    <1 physical access | 2 external account/UI-only surface | 3 policy-reserved>.
NOTE: ``gh`` IS installed and authenticated on this box, so GitHub PR create/merge is NOT
operator-only. Any blocker naming GitHub is wrong unless token scopes are re-checked.

## STANDING ROUTING FACT
This host (VIRTUAL-TEN) is a VMware VM with ZERO NVIDIA hardware. CUDA build and GPU playback
legs are ROUTED to the GPU hosts (\\bachelor\mlv-agent). "CUDA blocked locally" is a ROUTING
decision, never a lane blocker.

## MEASUREMENT DISCIPLINE THAT ALREADY COST THIS BOARD REAL TIME
- EVERY PLAYBACK NUMBER CARRIES ITS CONFIGURATION OR IT CARRIES NOTHING. Legs here have run with
  MLVAPP_PLAYBACK_SCALE_FACTOR=1 while the shipping default is scale 4 (HighQuality), and the
  overrides appear in NO downstream artifact - only in smoke-stdout.txt prose.
- The stage-timing clock was 1 ms until 2026-09-02. Any median-based attribution computed before
  that is biased toward "unattributed"; the mean is the unbiased estimator.
- An empty grep is a statement about the SEARCH, not about the tree.
- A 0% or 100% rate is a STRUCTURAL claim, not an extreme measurement.
- With n=1, report the observation and STOP.

## THE CARD, verbatim from queue.json
$fence json
$cardJson
$fence

## WHAT I NEED
1. State the card's CURRENT truth: is its blocker still real? Run or cite the proving command.
   A blocker repeated without a fresh proving run is not a blocker, it is a memory.
2. If it is actionable, DO THE ANALYSIS and return the decision, with evidence.
3. If it is NOT actionable, say precisely why, and name the party in the format above.
4. Name anything you find that contradicts the card's own text. A card that describes a retired
   mechanism is worse than an empty card.
5. End with a block headed DECISION containing: the card's live status, the single next action,
   and who owns it.
"@

$stamp      = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$promptPath = Join-Path $PromptDir ("ws-$cardId-$stamp.md")
[System.IO.File]::WriteAllText($promptPath, $brief, [System.Text.UTF8Encoding]::new($false))

$briefKb   = [math]::Round(($brief.Length / 1KB), 1)
$onTrack   = @($live | Where-Object { (Get-Track $_) -eq $cardTrack }).Count

Write-Output "WORKSTREAM: track=$cardTrack card=$cardId priority=$(Get-Rank $card) state=$(Get-Prop $card 'state')"
Write-Output "WORKSTREAM: lane=$Lane engine=$engine needsShell=$needsShell briefKB=$briefKb"
Write-Output "WORKSTREAM: live cards on this track = $onTrack (live overall = $($live.Count))"
Write-Output "WORKSTREAM: prompt=$promptPath"

if ($DryRun) {
    Write-Output 'WORKSTREAM: DRY RUN - nothing dispatched.'
    exit 0
}

$runDir = Join-Path $RepoRoot ".claude-state\fleet-runs\ws-$cardId-$stamp"
& pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $LaneRunner `
    -Lane $Lane -PromptFile $promptPath -Card $cardId -RunDir $runDir -TimeoutSec $TimeoutSec
$laneExit = $LASTEXITCODE

$record = [ordered]@{
    schema          = 'mlv-app/workstream-dispatch/v1'
    cardId          = $cardId
    track           = $cardTrack
    priority        = (Get-Rank $card)
    stateAtDispatch = (Get-Prop $card 'state')
    lane            = $Lane
    engine          = $engine
    needsShell      = [bool]$needsShell
    promptPath      = $promptPath
    promptBytes     = $brief.Length
    runDir          = $runDir
    dispatchedUtc   = (Get-Date).ToUniversalTime().ToString('o')
    laneExitCode    = $laneExit
}
Add-Content -LiteralPath $LogPath -Value ($record | ConvertTo-Json -Compress) -Encoding UTF8

Write-Output "WORKSTREAM: dispatched, laneExit=$laneExit runDir=$runDir"
exit 0
