<#
.SYNOPSIS
    The ONLY sanctioned way to start a lane with write access. Refuses before Invoke-Lane.ps1 runs
    unless the enforcement receipt matches the hook IN THE LANE'S WORKTREE. Written by the hub in 0.05.
.DESCRIPTION
    Rev 6: everything is resolved from -WorkDir, never from the canonical checkout (round-4 O48: the
    canonical checkout sits on a peer branch and never receives a merge to fork/master). The project hook
    tools\hooks\mlv-never-authorized.py and tools\coordination\Invoke-Lane.ps1 are read from the worktree
    the lane will run in; that is the copy Claude Code will apply and execute.
    Refusals (exit 3, one line on stdout starting REFUSED:):
      codex-lane-never-edits     -Lane sol|luna with write access (no Claude hook is visible to codex exec)
      workdir-missing            -WorkDir absent or not a git worktree
      workdir-is-board-root      -WorkDir resolves to the board root; a lane is never rooted there (O124)
      hook-receipt-missing       receipts\0.05-hook-enforced.json absent
      hook-missing               <WorkDir>\tools\hooks\mlv-never-authorized.py absent (worktree predates 0.1)
      hook-drifted               sha256 of the worktree's hook != receipt.hookSha256
      hook-wire-unproven         receipt has no hookWiredProof (the registration path was never proven in a live session)
      hook-unregistered          the worktree's .claude/settings.json has no PreToolUse entry of type 'command' whose command is EXACTLY the pinned hook command (a hook is interpreter x script x REGISTRATION; O98/S89/S92)
      invoke-lane-stale          the worktree's Invoke-Lane.ps1 has no -AllowedTools or no -ExtraReadDir (predates 0.1); NO exemption
      prompt-missing             -PromptFile does not exist
      clip-line-missing          the prompt has no CLIP_OR_NONE: line (NA-4 needs it)
    Invoke-Lane.ps1 (post-0.1) repeats the receipt check itself and adds hook-not-enforced / allowlist-required.
    ASCII-only by project convention. Never modifies queue.json or any receipt.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('opus','sonnet','fable','sol','luna')][string]$Lane,
    [Parameter(Mandatory)][string]$PromptFile,
    [Parameter(Mandatory)][string]$WorkDir,
    [string]$Card = '',
    [string]$RunDir = '',
    [string]$ExtraReadDir = '',
    [int]$TimeoutSec = 1500
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# MLV_BOARD_ROOT: only a test sets it (a tmp-dir board fixture); the default is the real board (O107).
$RepoRoot  = if ($env:MLV_BOARD_ROOT) { $env:MLV_BOARD_ROOT } else { 'C:\!Layi Wkspc\MLV-App' }
$DualLane  = Join-Path $RepoRoot '.claude-state\coordination\dual-lane'
$Receipt   = Join-Path $DualLane 'receipts\0.05-hook-enforced.json'
# The audited allowlist: exactly the hook's matcher set plus read-only tools. ALL is never granted.
$AllowedTools = 'Bash,PowerShell,Read,Write,Edit,Glob,Grep,NotebookEdit'

function Refuse([string]$Code, [string]$Detail) {
    Write-Output ("REFUSED: {0} {1}" -f $Code, $Detail)
    exit 3
}
if ($Lane -eq 'sol' -or $Lane -eq 'luna') { Refuse 'codex-lane-never-edits' "lane=$Lane" }
if (-not (Test-Path -LiteralPath $WorkDir)) { Refuse 'workdir-missing' $WorkDir }
$WorkDir = (Resolve-Path -LiteralPath $WorkDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $WorkDir '.git'))) { Refuse 'workdir-missing' "not a git worktree: $WorkDir" }
if ((Resolve-Path -LiteralPath $WorkDir).Path.TrimEnd('\') -ieq (Resolve-Path -LiteralPath $RepoRoot).Path.TrimEnd('\')) { Refuse 'workdir-is-board-root' "a lane is never rooted at the board: $WorkDir (O124)" }
if (-not (Test-Path -LiteralPath $PromptFile)) { Refuse 'prompt-missing' $PromptFile }
if (-not (Test-Path -LiteralPath $Receipt))    { Refuse 'hook-receipt-missing' $Receipt }
$HookPath   = Join-Path $WorkDir 'tools\hooks\mlv-never-authorized.py'
$InvokeLane = Join-Path $WorkDir 'tools\coordination\Invoke-Lane.ps1'
if (-not (Test-Path -LiteralPath $HookPath))   { Refuse 'hook-missing' $HookPath }
if (-not (Test-Path -LiteralPath $InvokeLane)) { Refuse 'invoke-lane-stale' "absent: $InvokeLane" }
$rec = Get-Content -LiteralPath $Receipt -Raw | ConvertFrom-Json
$hookSha = (Get-FileHash -LiteralPath $HookPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hookSha -ne ([string]$rec.hookSha256).ToLowerInvariant()) { Refuse 'hook-drifted' ("worktree={0} receipt={1}" -f $hookSha, $rec.hookSha256) }
if (-not ($rec.PSObject.Properties.Name -contains 'hookWiredProof') -or [string]::IsNullOrWhiteSpace([string]$rec.hookWiredProof)) { Refuse 'hook-wire-unproven' $Receipt }
# A hook is (interpreter x script x REGISTRATION); the hash above proves only the middle term (O98).
$SettingsPath = Join-Path $WorkDir '.claude\settings.json'
if (-not (Test-Path -LiteralPath $SettingsPath)) { Refuse 'hook-unregistered' "no .claude/settings.json in $WorkDir" }
# The ratified registration is ONE exact string: pinned absolute interpreter x tracked script under CLAUDE_PROJECT_DIR (S89),
# carrying the project dir on argv because the environment variable is measured absent from hook processes (O126).
$ExpectedHookCommand = '"C:/Users/obabalola/AppData/Local/Python/bin/python.exe" "${CLAUDE_PROJECT_DIR}/tools/hooks/mlv-never-authorized.py" --project-dir "${CLAUDE_PROJECT_DIR}"'
$registered = $false
try {
    $settingsObj = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
    foreach ($entry in @($settingsObj.hooks.PreToolUse)) {
        if ([string]$entry.matcher -cne 'Bash|PowerShell|Write|Edit|NotebookEdit') { continue }   # case-sensitive (S94)
        foreach ($h in @($entry.hooks)) {
            $cmd = [string]$h.command
            if (([string]$h.type -ceq 'command') -and ($cmd -ceq $ExpectedHookCommand)) { $registered = $true }
        }
    }
} catch { $registered = $false }
if (-not $registered) { Refuse 'hook-unregistered' "no PreToolUse entry (matcher Bash|PowerShell|Write|Edit|NotebookEdit) of type 'command' whose command is exactly $ExpectedHookCommand in $SettingsPath" }
$laneParams = (Get-Command $InvokeLane).Parameters.Keys
if (-not ($laneParams -contains 'AllowedTools')) { Refuse 'invoke-lane-stale' "no -AllowedTools in $InvokeLane (worktree predates 0.1)" }
if (-not ($laneParams -contains 'ExtraReadDir')) { Refuse 'invoke-lane-stale' "no -ExtraReadDir in $InvokeLane (worktree predates 0.1)" }
if (-not (Select-String -LiteralPath $PromptFile -Pattern '^(- )?CLIP_OR_NONE:' -Quiet)) { Refuse 'clip-line-missing' $PromptFile }

# The project hook reads CLIP_OR_NONE from this file to enforce NA-4.
$env:MLV_LANE_PROMPT = (Resolve-Path -LiteralPath $PromptFile).Path

$argsList = @('-Lane', $Lane, '-PromptFile', $PromptFile, '-WorkDir', $WorkDir, '-TimeoutSec', $TimeoutSec, '-AllowEdits', '-AllowedTools', $AllowedTools)
if ($Card)   { $argsList += @('-Card', $Card) }
if ($RunDir) { $argsList += @('-RunDir', $RunDir) }
if ($ExtraReadDir) { $argsList += @('-ExtraReadDir', $ExtraReadDir) }

Write-Output ("WRAPPER: hook={0} receipt-ok lane={1} card={2} workDir={3}" -f $hookSha.Substring(0,12), $Lane, $Card, $WorkDir)
& pwsh -NoProfile -File $InvokeLane @argsList
exit $LASTEXITCODE
