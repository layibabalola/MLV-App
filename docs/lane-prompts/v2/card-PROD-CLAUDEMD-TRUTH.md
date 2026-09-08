# PRODUCT CARD: PROD-CLAUDEMD-TRUTH  (priority 2)

You are the `sonnet` implementer lane of the MLV-App board. Shell + write access to ONE worktree. Everything you need
is here; do not read the queue, the pens, or the doctrine bus.

## Where you are
- Worktree: `{{WORKDIR}}` (from `fork/master` at `{{BASE_SHA}}`). Work ONLY here. Run dir `{{RUNDIR}}` is readable.
- Branch: `git switch -c product/PROD-CLAUDEMD-TRUTH`. Remote `fork` = `https://github.com/layibabalola/MLV-App.git`.
- CLIP_OR_NONE: none
ALLOWED_PATHS: CLAUDE.md, claude/batch-cli-spec.md, .github/workflows/tests.yml
- NOTE: this card edits ONE workflow file (`.github/workflows/tests.yml`) to add a single step; the template's default
  prohibition is lifted for exactly that edit.

## Why this card exists
`CLAUDE.md` opens as "MLV-App Batch CLI Implementation Spec" with Phases 0-6 described as future work and
"Architecture (Locked - Do Not Deviate)". Every phase SHIPPED: batch export merged 2026-08-08 (`66549181`, E4-1);
`--receipt` is wired as `receiptOpt` in `platform/qt/main.cpp`; per-frame `skipped=` logging lives in
`src/batch/BatchRunner.cpp`. Agents reading CLAUDE.md follow a locked spec for finished work, and the product has had
no written next goal since. Separately, `tools/docs/check_pinned_tokens.py` — the checker CLAUDE.md itself tells every
reader to run — is invoked by no hosted workflow, so its guarantee is local-only.

## Deliverable (docs + one CI step; no product code)
1. `CLAUDE.md` "Purpose": the batch CDNG CLI (Phases 0-6) is SHIPPED, cite the three locations above by symbol/commit,
   and say the next product work is selected from `docs/roadmap.md` (the queue mirror) and nowhere else.
2. `claude/batch-cli-spec.md`: a "Status" table at the top — one row per phase, `SHIPPED`, with the commit or symbol that
   proves it. Spec body verbatim below it as design history.
3. Keep every line under "### Pinned contract tokens" byte-identical (asserted by `tools/repo_hygiene/brokered_closeout.py`
   and its tests). Keep "Behavioral Rules" verbatim.
4. `.github/workflows/tests.yml`, job `repo-hygiene-python`: add one step after the unittest discovery step:
   `python tools/docs/check_pinned_tokens.py` on BOTH matrix legs — `py -3` appears in no workflow in this repo and would bypass the
   `python-version-file: ".python-version"` interpreter into which this job's hashed dependencies are installed; this repo's
   `.claude/settings.json` records `py -3` hooks failing SILENT for two days in Aug 2026, byte-identical to a check that ran and passed
   (O101) — so the pin check is hosted and required.

## Acceptance (hosted CI)
The new step green on both matrix legs; `test_candidate_acceptance.py` green; `CLAUDE.md` ≤ 9,695 bytes — its size at
DIAGNOSIS_BASE; it may shrink but may not grow (`docs/22-doc-fragmentation-policy.md` sets a 12,288 hard cap, but this
card is bound by the tighter ratified budget). Check with `wc -c CLAUDE.md`.

## Verify FIRST
```
git -C . show {{BASE_SHA}}:CLAUDE.md | grep -n -i "shipped"                              # empty today
git -C . grep -n "check_pinned_tokens" {{BASE_SHA}} -- .github/workflows                  # empty today
```
If both are non-empty, print `ALREADY-SHIPPED: <evidence>` and stop.

## Procedure
1. `git switch -c product/PROD-CLAUDEMD-TRUTH`; edit the three files; show full diffs.
2. Prove the pinned-token check can FAIL: remove one token line, run `python tools/docs/check_pinned_tokens.py` (red) — the same interpreter deliverable 4 pins; `py -3` resolves
   here only through a WindowsApps App Execution Alias that this repo's own `.claude/settings.json` records as absent, and a falsifier whose RED
   could be an interpreter error is not a falsifier (O104) —
   restore (green). Paste both.
3. `git add CLAUDE.md claude/batch-cli-spec.md .github/workflows/tests.yml`; commit "docs: CLAUDE.md says the batch CLI
   shipped and points at the backlog; host the pin check"; body ends `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.
4. `git push fork product/PROD-CLAUDEMD-TRUTH`.
5. {{PR_STEP}}

## STOP conditions
Any test red after the change; any change outside the three files; CLAUDE.md over 9,695 bytes; the workflow edit
touching anything but the one added step.

## NEVER-AUTHORIZED (docs/never-authorized.json NA-1..NA-10)
NA-10: never write `.claude/settings.json`, `.claude/settings.local.json` or `tools/hooks/mlv-never-authorized.py` under any root — a lane never edits its own gate (O129).
force-push/history rewrite; deleting/moving/truncating ledger or evidence; credentials or token env vars; opening any
clip (none); spend beyond this run; weakening/skipping/deleting a test (adding a required step is the opposite and is
allowed); writing outside the worktree; owner machine state; branch protection.
