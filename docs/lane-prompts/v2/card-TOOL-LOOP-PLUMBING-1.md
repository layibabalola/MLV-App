# TOOLING CARD: TOOL-LOOP-PLUMBING-1  (Phase 0.35 — make an unattended product lane callable and refuse it when unsafe)

You are a `sonnet` lane with a shell and write access to ONE worktree. Tracked scripts only; derive at `{{BASE_SHA}}`.
You were started through `Start-EditingLane.ps1`, which verified the 0.05 receipt against the deployed hook.

## Where you are
- Worktree: `{{WORKDIR}}` (from `fork/master` at `{{BASE_SHA}}`). Branch `git switch -c tool/TOOL-LOOP-PLUMBING-1`.
- Board root (absolute, read-only reference): `C:\!Layi Wkspc\MLV-App`; `.claude-state\` lives ONLY there.
- CLIP_OR_NONE: none

## Measured facts you are fixing (verify at {{BASE_SHA}}; if one is already fixed, say so and skip it)
- `Invoke-WorkstreamLoop.ps1` has no `-Lane` and no `-AllowEdits`.
- `Invoke-Workstream.ps1` has no `-AllowEdits`; its `Invoke-Lane.ps1` call passes `-Lane -PromptFile -Card -RunDir -TimeoutSec`
  only; it never loads a card's `procedure` and composes a generic analysis brief; `$RepoRoot` is hardcoded and `$runDir`
  derives from it; lane choice is `if ($needsShell) { 'luna' } else { 'fable' }`.
- `Invoke-Lane.ps1` defaults `-WorkDir` to the repo root; for claude engines it passes only `--add-dir $WorkDir`; with
  `-AllowEdits` a claude lane gets `permissionMode acceptEdits` and `allowedTools ALL`; the codex branch runs
  `exec -s workspace-write`, which no Claude hook can see. (0.1's PR may already have added `-AllowedTools` and
  `-ExtraReadDir`; if so, use them and do not redefine them.)
- Kill switch checked once per cycle; budget row appended AFTER the lane returns.

## Deliverable (all under tools/coordination/; ASCII-only .ps1)
1. Loop: `-Lane <opus|sonnet|fable|sol|luna>` and `[switch]$AllowEdits`. **`-Install` today persists only `-DailyBudget`,
   `-MaxDispatchesPerCycle`, `-TimeoutSec` and `-StaleHours` into the scheduled-task action and SILENTLY DROPS `-Tracks` (verify at
   {{BASE_SHA}}: `git -C . show {{BASE_SHA}}:tools/coordination/Invoke-WorkstreamLoop.ps1 | sed -n '155,161p'`; the live task's `Arguments`
   carry none). Extend that one `$argLine` so it persists EVERY schedulable parameter it was invoked with — `-Tracks` serialised as a comma
   list, `-Lane`, `-AllowEdits` — and make the script re-parse a comma-joined `-Tracks` string into an array, because a `pwsh -File` action
   passes its arguments as literal strings. Expose the argument-line builder as a function (`Get-InstallArgLine`) and test it
   deterministically: for `-Tracks product,playback -Lane sonnet -AllowEdits` the line contains all three verbatim, and a `-DryRun` cycle
   started from exactly that line's arguments reports `tracks` equal to `product, playback`. No test registers a scheduled task; the hub's
   0.2 acceptance re-reads the real one (O95).**
2. Dispatcher: `[switch]$AllowEdits`; lane resolution by the card's derived `kind` (`product|playback` + `owner sonnet`
   → `sonnet`; `RECON:`/`REVIEW:` scopes → `luna`/`fable`); explicit `-Lane` wins. **Track SELECTION is unchanged and stays by `track`
   (`Get-Track`: absent → `UNSET`, and `-Track` filters through it); 0.18 seeds `track = kind`, and your deterministic test proves a
   `kind`-only card is NOT selected by `-Track product` (S82).**
3. **Every editing dispatch goes through `$D\Start-EditingLane.ps1`, never `Invoke-Lane.ps1 -AllowEdits` directly.**
   In addition to the wrapper's own refusals, the dispatcher refuses (fail closed, reason in the cycle receipt):
   - `-AllowEdits` with `-Lane sol|luna` → `codex-lane-never-edits`.
   - `-AllowEdits` when the card has no `procedure`, or the file's sha256 differs from the card's `procedureSha256` →
     `procedure-missing-or-drifted`. The generic brief is used ONLY for read-only lanes.
   - a fields file containing a top-level label outside the composer contract (see the TEMPLATE's trailing comment) →
     `unknown-field`. Nothing is ever silently dropped.
4. `Invoke-Lane.ps1`: add `-AllowedTools <comma list>` (claude engines; when `-AllowEdits` is set and `-AllowedTools` is
   absent, REFUSE with `allowlist-required` — `ALL` is never granted to an editing lane) and `-ExtraReadDir <path>`
   (mapped to a second `--add-dir`). Skip if 0.1 already landed both.
5. Per editing dispatch: `git -C <board root> worktree add C:\mlvtmp\lane-<card>-<ts> <baseSha>` where `baseSha` is
   `fork/master` resolved at dispatch time and recorded in the receipt as `baseSha` (the plan's diagnosis base is a
   different, fixed SHA); branch checkout, not `--detach`; pass `-WorkDir`, `-RunDir`, `-ExtraReadDir $RunDir`; remove
   the worktree after exit unless dirty (then record the path).
6. Prompt composition: fields file + `product-card-TEMPLATE.md` → composed prompt written into the run dir; substitute
   the contract's labels (`CARD_ID, PRIORITY, CLIP_OR_NONE, ALLOWED_PATHS, BRANCH, STATE, DEPENDS_ON, NOTE, DELIVERABLE,
   ACCEPTANCE, VERIFY_FIRST`; defaults for the optional four) plus runtime `WORKDIR, BASE_SHA, RUNDIR, TS, BRANCH, PR_STEP`
   (`PR_STEP` is EXACTLY one of the two literals plan 0.35 pins, selected on `GH-CAPABILITY` from `lane-gh-capability.json`, with the
   BRANCH placeholder inside it materialised before insertion (named in prose here so this sentence is not a substitution site — O166); both literals are in the template's COMPOSER CONTRACT — S122). Deterministic: same inputs
   → identical bytes. A full card prompt gets only the runtime placeholders, BRANCH among them. The composed prompt path is what `Start-EditingLane.ps1` receives (`MLV_LANE_PROMPT`).
7. Race safety: `[System.Threading.Mutex]` named `Global\MLV-WorkstreamLoop` held for the cycle; kill switch re-checked
   immediately before every lane start; reservation events APPENDED (never updated in place) to `$D\receipts\dispatch-reservations.jsonl`: a `{reservationId,
   state:"reserved", card, kind, lane, recordedUtc}` row BEFORE start and a second row with the same `reservationId` and
   `state: charged|refunded` after; `Invoke-WorkstreamLoop.ps1` reads this exact file for `spentToday` (S76).
8. Tests in `tools/coordination/test_coordination_guardrails.py` — a **pytest** suite (module-level `def test_*`; NOT
   `unittest.TestCase`; the unittest runner collects nothing from it and exits green — O79). Until 0.4c-i's step move lands, the
   suite keeps its existing home in the Windows-only `Factory Bridge Regressions` job; do NOT add a `tools/repo_hygiene` bridge,
   which would run it on the ubuntu leg where `pwsh.exe` does not exist:
   parameter forwarding; kind-based lane resolution; **track-based selection — a queued `{track:"product", kind:"product",
   owner:"sonnet"}` card is selected by `-Track product` and a `kind`-only card is NOT (S82)**; the three dispatcher refusals plus `allowlist-required`;
   worktree path uniqueness for two dispatches; composition determinism, an exact-byte assertion of BOTH PR_STEP literals (one fields card and one full card, each under both
   `GH-CAPABILITY` outcomes — S122) AND "every parsed field appears byte-for-byte in
   the composed prompt"; unknown-field refusal; reservation-before-start; kill-switch re-check. `-DryRun` + a fake
   wrapper/lane shim; no real lane is started by a test.
9. The pre-dispatch EXPORTER (S126 — the hub is the exporter only for the three PRs that land before this card; from this card on the
   dispatcher is): before every review-lane dispatch, `git fetch fork`, assert the full head and base objects are present, read
   `gh pr view -R layibabalola/MLV-App <n> --json number,headRefOid,body,state` and the master-protection required-context set
   (`gh api repos/layibabalola/MLV-App/branches/master/protection`) BEFORE and AFTER
   `gh pr checks -R layibabalola/MLV-App <n> --json name,state,link`, refuse head or context drift, and write `pr-<n>-checks.json` plus
   `pr-<n>-review.json` (`headRefOidBefore`, `headRefOidAfter`, `requiredContextsBefore`, `requiredContextsAfter`, `body`, `checks`, `retrievedUtc`)
   into the run dir — the files `sol-review-PR-TEMPLATE.md` consumes. Tests with a deterministic fake `gh` shim: pinned repository selection
   (`-R layibabalola/MLV-App` on every call), both exports byte-exact, drift refusal, and an absent required context reported as a failure.

## Acceptance
`python -m pytest tools/coordination/test_coordination_guardrails.py -q` green (the runner CI already uses); a `-DryRun` cycle with two eligible
product cards prints two distinct `workDir=`, `lane=sonnet`, and `baseSha=<40 hex>`; a `-DryRun` with `-Lane luna
-AllowEdits` prints `REFUSED codex-lane-never-edits`; a fields file with a stray `FOO:` line prints `REFUSED unknown-field`.
The end-to-end (one real PR from a lane worktree) is run by the hub after merge and recorded as the Phase 0.35 receipt.

## Procedure
Smallest diffs; show the full diff of each existing script; prove one test can FAIL (revert a refusal, red; restore,
green); commit "coordination: the loop can dispatch an editing implementer in its own worktree, and refuses when unsafe";
push; then {{PR_STEP}}

## STOP conditions
Any change outside `tools/coordination/`; the guardrail suite red for an unrelated
reason; a real lane would be needed.

## NEVER-AUTHORIZED (NA-1..NA-10)
All ten — NA-10: never write `.claude/settings.json`, `.claude/settings.local.json` or `tools/hooks/mlv-never-authorized.py` under any root — a lane never edits its own gate (O129). Additionally: do not register or modify the scheduled task from the lane (`-Install` is the hub's act after
merge); do not delete any existing worktree; do not touch `queue.json`; do not edit `Start-EditingLane.ps1` (hub-owned).
