# TOOLING CARD: TOOL-GH-PROBE-1  (Phase 0.15 — can a lane push, open a PR, and read the board root?)

You are a `sonnet` lane with a shell and write access to ONE worktree. This card has no product deliverable; it measures
TWO capabilities and prints evidence. The dormant CLAUDE pen (SEQ 565, 2026-09-05) recorded that `gh` inside a lane sandbox
fails with the keyring denial `Access is denied`; whether an editing lane can push, open a PR, or even READ the board root
outside its worktree has never been measured. Every later card's contract depends on both answers.

This card carries NO placeholders on purpose: it runs before the dispatcher's composition exists (Phase 0.35).
- CLIP_OR_NONE: none
- Your worktree is your CURRENT DIRECTORY (the hub created it from `fork/master` and started you in it; confirm with
  `git rev-parse --show-toplevel` and `git rev-parse HEAD`, and print both).
- Remote `fork` = `https://github.com/layibabalola/MLV-App.git`.
- Derive your own timestamp: `$ts = Get-Date -Format yyyyMMddTHHmmss` (PowerShell) or `ts=$(date +%Y%m%dT%H%M%S)` (bash).

## Do exactly this, printing every command and its full output verbatim
0a. As a SINGLE PowerShell tool call, attempt exactly one inert known-DENY payload: `if ($false) { setx ANTHROPIC_PROBE_TOKEN x }`. If the project hook denies it, the tool result carries the hook's stderr line — keep it verbatim for the HOOK-FIRED line. If the call simply runs, the answer is `none`. The payload is INERT: it names no real credential, and an unwired hook merely executes the false branch and touches nothing.
0. `git -C "C:\!Layi Wkspc\MLV-App" rev-parse HEAD` — the board root, outside your worktree. Note success or the exact error.
1. `gh auth status`
2. `git ls-remote --heads fork | head -3`
3. `git switch -c probe/gh-$ts`; `git commit --allow-empty -m "probe: lane gh capability $ts"`; `git push fork probe/gh-$ts`
4. `gh pr create -R layibabalola/MLV-App --head probe/gh-$ts --title "probe: lane gh capability (auto-closed)" --body "TOOL-GH-PROBE-1: verifies an editing lane can open a PR. Closed immediately by the same lane."`
5. `gh pr close <number> -R layibabalola/MLV-App --delete-branch`
6. `git push fork --delete probe/gh-$ts` (no-op if step 5 already deleted it; print the result either way)

## Output — the LAST THREE lines must be exactly
`HOOK-FIRED: <the hook's stderr line>` or `HOOK-FIRED: none`     (from step 0a; proves the PreToolUse registration in the venue that actually runs — O98)
`BOARD-ROOT-READ: ok` or `BOARD-ROOT-READ: denied error=<first line>`     (from step 0)
then one of:
`GH-CAPABILITY: lane-can-open-pr number=<n>`
`GH-CAPABILITY: gh-unavailable step=<1..5> error=<first line of the error>`
`GH-CAPABILITY: push-unavailable error=<first line>`   (if step 3's push fails; stop there, but still print both lines)
The hub writes all three into `lane-gh-capability.json`; the dispatcher's `PR_STEP` and every card's verify-first convention
follow from them, and `HOOK-FIRED: none` blocks 0.35.

## NEVER-AUTHORIZED (docs/never-authorized.json NA-1..NA-10)
NA-10: never write `.claude/settings.json`, `.claude/settings.local.json` or `tools/hooks/mlv-never-authorized.py` under any root — a lane never edits its own gate (O129).
No force-push. No branch other than `probe/gh-<ts>`. No credential or token env var, no `gh auth login`, no `codex login`.
No writes outside this worktree. No other PR touched. No branch-protection call of any kind.
