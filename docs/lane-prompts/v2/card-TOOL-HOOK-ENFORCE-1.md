# HUB PROCEDURE 0.05: TOOL-HOOK-ENFORCE-1 — author the MLV-App PROJECT hook and its falsifier suite

**This is NOT a lane card and it does NOT touch the global hook.** Rounds 1-4 established that
`~/.claude/hooks/check-continuity-boundaries.py` is shared by every project on this machine, fails open, changed at
06:00:09Z, and now guards its own directory behind the agent-bridge FREEZE marker. Patching it during another project's
freeze window is neither ours to do nor safe. The enforcement this plan needs is a PROJECT hook: tracked in this repo, applied
by Claude Code in the board root and in every lane worktree that contains it, tested in CI, reviewed cross-family in its PR.
The hub (an interactive Claude session) authors it on the 0.1 branch; nothing is installed by hand.

## Files (all on branch `plan/definitive-fix-v7`, landed by 0.1's PR)
1. **`tools/hooks/mlv-never-authorized.py`** — a PreToolUse gate: one JSON object on stdin (`{"tool_name": ..., "tool_input": ...}`),
   exit 0 ALLOW, exit 2 DENY with a one-line reason on stderr. **Fails CLOSED**: any exception, malformed JSON, empty stdin, or
   missing `tool_name` → exit 2 `hook-error:`. `MLV_HOOK_DRYRUN=1` prints the decision without changing the exit code.
   Rules, applied to `Bash`/`PowerShell` command text AND `Write`/`Edit`/`NotebookEdit` paths and content, one block per register
   row (`never-authorized.json` v26):
   - **NA-1**: `git push` with `--force`, `-f`, `--force-with-lease`, or a `+refspec` to remote `fork` or any URL containing
     `layibabalola/MLV-App`; `filter-branch`; `filter-repo`; `git reset --hard` followed by a push in one command.
   - **NA-2**: `rm|del|Remove-Item|rd|rmdir|git rm` (without `--cached`) OR `mv|move|Move-Item|git mv` OR a truncating write
     (`>`, `Clear-Content`, `Set-Content`, `Out-File`, `tee`) OR a `Write`, `Edit` or `NotebookEdit` whose target is under `.claude-state/closeout`,
     `.claude-state/fleet-runs`, `.claude-state/coordination`, `.closeout-evidence`, or is `.claude/ANALYSIS_LOG.md`.
     A `Write` to exactly `$D\receipts\0.2-enable-preflight-input.json` is the PRE-FLIGHT ARTIFACT act — the SECOND dedicated act, evaluated
     before generic content attribution: ALLOWED only at the board venue, only when the target is absent, and only when its complete content parses as
     exactly one hook-stdin object whose `tool_name` is `PowerShell` and whose `command` is the semantically valid canonical enable compound for the
     current six receipts; it writes evidence only and authorizes no delete; any other path, a worktree venue, an existing target, a malformed object or
     a non-canonical command → DENY (S125; hub-measured on the tenth commit: the outer Write was ALLOWED by the receipts carve-out while this sentence
     said DENY — the act pins the decision by rule, and the ELEVENTH hook commit implements it).
     **CARVE-OUT (O47):** under `$D\receipts\**`, `$D\queue.json`, `$D\lane-gh-capability.json`, `$D\digest\**`,
     `$D\*-resume-CURRENT.md` and `.claude-state\fleet-runs\**`, create-or-extend is ALLOWED; DENY only delete/move, or an
     existing target whose new content is shorter (shrink guard). **FOUR exact exceptions (O64/O65/O96/O124):** deleting
     `$D\WORKSTREAM-LOOP-DISABLED` is ALLOWED only while `$D\receipts\0.18-roadmap-parity.json`, the NEWEST execution-control receipt
     (`execution-control-0.7.json` when 0.7 landed a PR touching a hashed file, else `execution-control-0.6.json` — the hook selects the newest
     `$D\receipts\execution-control-*.json` by its `recordedUtc` field — S81/O102 — carrying forward
     `roadmapParityReceiptSha256`, `queueSha256`, `productLiveCount: 15`), `0.4b-required-checks.json`,
     `0.4c-demoted.json`, `0.6-ratio-guard.json` and `0.5-factory-frozen.json` all exist and VALIDATE against the RECEIPT SCHEMAS of plan §1.3 step 5
     — required keys per producing step, `recordedUtc` in the pinned notation, 64-hex lowercase hashes, 40-hex lowercase shas, path fields that exist
     on disk, `roadmapParityReceiptSha256` equal to the on-disk `0.18-roadmap-parity.json`'s sha256, `productLiveCount` exactly 15, and for every control receipt `reviewedHeadSha` (the PR head sol reviewed before the merge) AND `mergeSha` (the post-merge commit the hashes were
     taken at), a `solVerdictPath` whose JSON is APPROVE at `reviewedHeadSha` (S123), a `fixedSetEqualityProof` binding both shas to the fixed set's digest
     recomputed from the receipt's own `hashes` by the canonical command `python -c "import hashlib,sys,json;h=json.load(open(sys.argv[1]))['hashes'];print(hashlib.sha256('\n'.join(sorted(h[k]+'  '+k for k in h)).encode()).hexdigest())" <receipt>` — whole lines sorted as strings, LF-joined, no trailing
     newline, UTF-8 (O175) — the hub having computed it over the fixed set AS HASHED AT `reviewedHeadSha` (O176), and a `hashes` key set
     exactly the step's (S120); an empty object,
     a missing or empty key, a wrong type or a malformed value is INVALID and the refusal names the file and the key; the execution-control
     candidate set is exactly the six chain names, the selected one exactly `execution-control-0.7.json` when a valid 0.7 exists else exactly
     `execution-control-0.6.json`, the parsed-newest valid chain receipt must be that one, and any other `execution-control-*.json` is INVALID and
     fails the exception closed (S118) —, `$D\receipts\0.2-loop-enabled.json` is
     ABSENT on disk, AND the SAME tool input creates it BEFORE the delete — the canonical compound form `$ErrorActionPreference = 'Stop'; $r = '<json>'; Set-Content -LiteralPath '<receipt>' -Value $r -NoNewline -Encoding utf8 -ErrorAction Stop; if ((Get-Content -LiteralPath '<receipt>' -Raw -ErrorAction Stop) -cne $r) { throw 'enable-receipt-write-verification-failed' }; Remove-Item -LiteralPath '<marker>' -ErrorAction Stop`,
     from which the hook extracts the JSON literal and validates it SEMANTICALLY, not for presence: `state` = `enabling`; `enabledUtc` and
     `recordedUtc` carry the ONE pinned notation `YYYY-MM-DDTHH:MM:SSZ` (whole seconds, uppercase `Z`, no fraction, no offset); `executionControlReceipt`
     = the basename of the NEWEST valid execution-control receipt by `recordedUtc`, where every receipt's `recordedUtc` the hook reads carries that
     same notation, the order is that of the PARSED value, and a non-conforming stamp or an exact tie makes the newest undecidable so the exception
     fails closed (O141); `executionControlSha256` = the lowercase SHA-256 of that exact file, derived as `(Get-FileHash -Algorithm SHA256 -LiteralPath <receipt>).Hash.ToLowerInvariant()` — `Get-FileHash` returns uppercase and the
     comparison is case-sensitive, the trap `Start-EditingLane.ps1` already lowercases both sides for (O145) (S112) — and which it matches ONLY in this fail-closed form — the LEADING `$ErrorActionPreference = 'Stop'`, `-ErrorAction Stop` on both acts, and
     the read-back `throw` between them, all three (S101/O128/O134; the leading preference is the arm measured to close the unresolvable-drive case,
     in which the other two go unbound and the delete runs); a delete-only input, or a literal failing validation, is DENY; the hook recognises the canonical compound as ONE dedicated act evaluated
     BEFORE the generic per-command attribution of verbs to paths, which would otherwise deny it, and ALLOWS it only when its own `--project-dir` argument, normalised,
     equals the board root — a worktree value or a missing/empty argument is DENY (O126), because a worktree lane's hook evaluates the same absolute board paths
     (S105) (plan step 0.2; the enable is ONE-SHOT — once
     the receipt exists a re-armed marker cannot be deleted again without a newly ratified authorization — S98/S99/O118); a shrinking overwrite of
     `$D\*-resume-CURRENT.md` or `$D\ignition\seat-*.md` is ALLOWED only when a byte-identical copy already exists under
     `.claude-state\continuity\archive\` and the new content names its sha256 (plan step 0.9); **(iii) CREATING `$D\WORKSTREAM-LOOP-DISABLED` is ALWAYS ALLOWED, by any
     tool or shell verb, with no receipt condition — re-arming the emergency stop is never gated; only its delete is, by (i) (O96).** **(iv) writes — create, extend or amend, any length — to the four
     ratified manifest surfaces `$D\DEFINITIVE-FIX-PLAN-*.md`, `$D\never-authorized.json`, `$D\Start-EditingLane.ps1` and `$D\prompts\v2\**` are ALLOWED
     only when the hook's own `--project-dir` argument (substituted by Claude Code from `${CLAUDE_PROJECT_DIR}` in the registered command; the
     environment variable of that name is measured absent from hook processes — O126), normalised, equals the board root (`MLV_BOARD_ROOT`); a worktree
     value or a missing/empty argument → the exception does
     not apply; delete/move stays DENY (O124). The receipt carve-out is a PATH permission, not a TOOL permission: a shell truncating write is DENY on
     those paths even for a create, the `Write` create is ALLOW (O125).**
   - **NA-3**: assignment (`X=`, `export X=`, `$env:X=`, `set X=`, `setx X`) of any name starting `ANTHROPIC_`, `OPENAI_`,
     `CLAUDE_CODE_`; `codex login`; `claude auth login|logout` (the read-only `claude auth status` is ALLOWED — S106). **PERSISTENT-scope
     assignment (`setx`, `[Environment]::SetEnvironmentVariable(..., 'User'|'Machine')`) of `CLAUDE_PROJECT_DIR`, `MLV_BOARD_ROOT`, `MLV_LANE_PROMPT`,
     `MLV_REQUIRED_CHECKS_SNAPSHOT` or `MLV_CLIP_CACHE_ROOT` is DENY — it reaches every later hook process and moves the board root the hook judges
     paths against (O129).**
   - **NA-10**: any write (`Write`/`Edit`/`NotebookEdit` or shell) to `.claude/settings.json`, `.claude/settings.local.json` or
     `tools/hooks/mlv-never-authorized.py` under ANY root is DENY unless `--project-dir` equals the board root — the hook re-reads itself on every
     call, so a lane's edit of its own gate or registration would take effect on its next tool call; hook evolution stays hub-authored at the board
     venue (O129, hub extension).
   - **NA-4**: any argument matching `*.mlv` (case-insensitive) or a path under the clip-cache UNC prefix (**`MLV_CLIP_CACHE_ROOT`, DEFAULTING to `\\bachelor\mlv-agent\cache` when unset — O88**), EXCEPT tracked
     fixtures under `<worktree>/tests/fixtures/clips/` (always allowed). The hook reads the `CLIP_OR_NONE:` line of the prompt
     file named by `MLV_LANE_PROMPT` (set by the wrapper), matched as `^(- )?CLIP_OR_NONE:` — the wrapper's own pattern; full cards carry
     the label as a bullet, fields files at column 0 (H2): `none`, or ONE canonical absolute path; candidate and authorized paths
     resolved without following links, compared case-insensitively as FULL paths; a different clip with the same basename is
     DENY. With the var unset, any `.mlv` outside the fixtures is DENY.
   - **NA-6**: `continue-on-error` written into `.github/workflows/`; an `Edit`/`Write` under `tests/**` whose new content has
     fewer `QVERIFY|QCOMPARE|assert` than the old, or adds `QSKIP`; removal of a `test_*.cpp` line from a `.pro` or a test step
     from a `.yml` — EXCEPT a MOVE, matched exactly: removing a test step or job from one tracked workflow is ALLOWED only when the same
     tool call, or the same PR's paired edit, re-adds the byte-identical `run:` body under `.github/workflows/`; the predicate is NET removal
     across `.github/workflows/*.yml`, never per-edit (0.4c-i's step move — O108); `rm`/`git rm` of any `tests/**` file.
   - **NA-7**: any `Write`/`Edit` path, or any Bash/PowerShell redirection / `sed -i` / `tee` / `Set-Content` / `Out-File` /
     `cp`/`Copy-Item` destination, under `.factory/` or outside BOTH the current worktree (`git rev-parse --show-toplevel` of
     cwd) and the board root — **read from `MLV_BOARD_ROOT`, DEFAULTING to `C:\!Layi Wkspc\MLV-App` when unset (the default keeps the rule
     fail-closed on the real machine; only the test sets the variable, so the table is host-independent; the wrapper never sets it, and a lane's
     tool call cannot reach the hook process's environment — O88)**. (No exception for `~/.claude/hooks/` — this plan never writes there.)
   - **NA-8**: `Set-ItemProperty`/`reg add` touching `UserGpuPreferences`; `powercfg`.
   - **NA-9**: `gh api` with `-X DELETE|PATCH|PUT` on a path containing `branches/` and `protection` is DENY when the body REMOVES
     or WEAKENS any context relative to the LAST RECORDED required-context set — **read from the LAST non-empty row of the JSONL file named by
     `MLV_REQUIRED_CHECKS_SNAPSHOT`, DEFAULTING to `$D\receipts\required-checks-live.jsonl` (one complete normalised snapshot per line; a
     malformed row anywhere, or a malformed final row, fails closed); while that file is absent or unparseable the
     hook DENIES every `-X DELETE|PATCH|PUT` on a protection path, add-only bodies included (fail-closed). 0.05 APPENDS the first row from a
     live GET before the hook lands (the hub's session has `gh`; a lane's does not, and the hook never calls `gh` itself); 0.4b-ii's
     `set-required-checks.ps1` APPENDS its validated post-PATCH row and never replaces an earlier one — a rewrite would SHRINK the file, which
     NA-2 denies under `$D\receipts\**` (O92/S84)** — EXCEPT the single 0.4b transition (JSON-equivalent to the canonical
     five-context body; `$D\receipts\0.4a-batch-compile-falsifier.json` present with `failingContext == "Batch Compile"`;
     `$D\receipts\0.4c-guardrail-move.json` present with `conclusion == "success"`; `$D\receipts\0.4b-required-checks.json`
     absent). When the snapshot is present and parseable, add-only bodies are allowed; when it is absent or unparseable they are denied with every
     other protection mutation (S85). (LIMIT: 0.4b-ii runs through `set-required-checks.ps1`; the hook sees the
     launcher; NA-9 is bounded by that script's own receipt gate.)
2. **`tools/repo_hygiene/test_mlv_never_authorized.py`** — collected by the repo-hygiene job's `unittest discover` (`tests.yml:101`), **so
   every case MUST live in a `unittest.TestCase` subclass: a module-level `def test_*` table collects ZERO under the unittest runner and exits
   green — O79's failure mode in the one suite that proves enforcement (O86). Acceptance asserts the runner reports `Ran N tests` with N ≥ the
   number of register rows plus controls, and the local run is RED if N is 0.** A table with an explicit EXPECTED column, expanded into ONE `def test_*` METHOD PER ROW inside the class
   (generated at class-definition time by `setattr` over the table, or written out) — NOT `subTest`s: `unittest` counts `Ran N` once per
   METHOD, so an eleven-row subTest table reports `Ran 1 test`, which the N ≥ rows + controls assertion reads as RED (O90). The rows: the 3 controls (DENY), the 16 round-1 falsifiers (DENY), the 12 round-2 falsifiers (DENY), 4
   fail-closed inputs (DENY), the NA-4 same-basename case (DENY), a receipt CREATE under `$D\receipts\` (ALLOW) and a SHRINK of
   an existing receipt as a `Write` payload (DENY) and as an `Edit` payload (DENY) (S86), the compound create-then-delete enable input with all six 0.2 receipts present and the enable receipt absent (ALLOW), the same with one
   receipt absent (DENY), a BARE marker delete with all six present (DENY — S99), a compound input whose JSON literal lacks a field (DENY — S99), a canonical compound whose `executionControlReceipt` is not the NEWEST valid
   execution-control receipt, whose `executionControlSha256` is not the lowercase SHA-256 of that exact file, or whose `enabledUtc`/`recordedUtc` is
   not parseable ISO-8601 UTC (DENY — S112, one row each) beside the matching receipt/hash/timestamps case (ALLOW), a receipt set in which a fractional stamp sits beside a
   whole-second one (DENY — O141: non-conforming stamp, newest undecidable), a receipt stamped with an offset (DENY), a naive receipt stamp
   (DENY), two receipts with an identical stamp (DENY — undecidable), the literal's stamps carrying fractional seconds (DENY — one notation
   everywhere), an empty object in each of the five fixed receipts (DENY — S118, one row each), an execution-control receipt named outside
   the chain set beside otherwise plausible provenance (DENY — S118), a fixed receipt missing a required key, carrying an uppercase or 63-char
   hash, or naming a path that does not exist (DENY — S118, one row each), a `roadmapParityReceiptSha256` that does not equal the on-disk 0.18
   receipt's sha256 (DENY — S118), `execution-control-0.6.json` stamped later than a valid `execution-control-0.7.json` (DENY — chain violation,
   undecidable), and the fully conforming six-receipt set (ALLOW), a control receipt lacking `solVerdictPath`, one whose verdict JSON is not APPROVE, one whose
   `subject_sha` differs from `reviewedHeadSha`, one lacking `mergeSha` or carrying a malformed one (DENY — S123, one row each), one lacking `fixedSetEqualityProof`, one whose proof's shas differ from its own `reviewedHeadSha`/`mergeSha`, one whose
   proof digest differs from the digest recomputed from its `hashes` (DENY — O172, one row each), the O159 cascade with every re-pointed receipt keeping
   its ORIGINAL `recordedUtc` (ALLOW — O171) and the same cascade restamped so two receipts tie (DENY — undecidable, O171), one whose `hashes` key set has an extra key, one missing a key, and `execution-control-0.1.json`
   lacking `composerStatus` (DENY — S120, one row each), the outer `Write` of the pre-flight artifact with valid content at the board venue onto an
   absent target (ALLOW — S125), the same Write to another path, from a worktree venue, onto an existing target, with a malformed object, or carrying a
   non-canonical command (DENY — S125, one row each; an existing artifact is never rewritten or deleted — the hub re-probes the STORED file and types
   its compound verbatim, O178), a worked digest over a fixed three-path fixture equal to the canonical command's output (ALLOW — O175), an O152
   repair of 0.6 stamped between 0.4b-i's and a valid 0.7's parsed stamps (ALLOW — O177) beside the same repair stamped at write time (DENY — O177),
   the compound input with `0.2-loop-enabled.json` already PRESENT on disk (DENY — S98), and a single input naming both the marker under
   `Remove-Item` and the receipt path in ANY shape other than the canonical enable form (DENY — O118: generic attribution must never be what
   admits or refuses the enable), a compound whose `Set-Content` lacks `-ErrorAction Stop` or whose read-back verification is absent, or whose leading `$ErrorActionPreference = 'Stop'` is absent (DENY — S101/O128), the canonical compound with all six receipts and `--project-dir` = a worktree path (DENY — S105) and absent (DENY — S105) — the
   existing compound ALLOW row runs at the board-root venue, a `Write` of the plan with `--project-dir` = the board root (ALLOW — O124), the same with `--project-dir` = a worktree
   path (DENY) and missing (DENY), a delete of the register even from the board root (DENY), a `setx MLV_BOARD_ROOT x` and a
   `[Environment]::SetEnvironmentVariable('CLAUDE_PROJECT_DIR', 'x', 'User')` (DENY — O129; one row per name), a `Write` of `<worktree>\.claude\settings.json`
   and of `<worktree>\tools\hooks\mlv-never-authorized.py` with `--project-dir` = that worktree (DENY — NA-10) beside the same two at the board venue
   (ALLOW), a shell `Set-Content` CREATE of a receipt (DENY — O125)
   beside the `Write` create of the same path (ALLOW), the kill-switch CREATE (`Write` and
   `Set-Content`) with no receipts present (ALLOW — O96), an `execution-control-*.json` LACKING `recordedUtc` present in the receipt set →
   kill-switch delete (DENY — O105), a test step MOVED between workflows with its `run:` body preserved (ALLOW) and a test step deleted
   outright (DENY) (O108),
   a checkpoint stub with a sha-matching archive copy present (ALLOW) and absent (DENY), the canonical 0.4b body with all receipt conditions satisfied (ALLOW) and with the 0.4b receipt
   present (DENY), an ADD-ONLY body against the snapshot (ALLOW), a single-context REMOVAL against it (DENY), any protection mutation with
   the snapshot ABSENT (DENY), and with its final row MALFORMED (DENY) (O92/S84), and 7 benign ALLOW controls (`claude auth status` — S106, `git push fork product/x`, `git rm --cached .claude/profiling/a.dng`,
   `rm build/*.o`, a `Write` of `tests/console/new_test.cpp` with assertions, `gh pr create`, an add-only required-checks
   PATCH WITH THE SNAPSHOT FIXTURE PRESENT — O97). **The suite fails if any register row NA-1,2,3,4,6,7,8,9,10 has zero DENY cases (NA-5 is enforced by `Invoke-WorkstreamLoop.ps1`, not by this hook,
   and is the only row exempt — O133).** Probes invoke the hook as a
   subprocess with payloads via `sys.executable`; the board root (`MLV_BOARD_ROOT`), the `bachelor` UNC path (`MLV_CLIP_CACHE_ROOT`) and the required-checks snapshot
   (`MLV_REQUIRED_CHECKS_SNAPSHOT`, pointed at a tmp-dir JSONL fixture the test writes, or at a deliberately nonexistent path for the
   snapshot-ABSENT row) are PARAMETERS the test supplies (path-normalised), never literals the hook hardcodes, so the table is green on both
   matrix legs (O81/O97); nothing is executed.
3. **`.claude/settings.json`** (tracked) gains the PreToolUse entry
   `"C:/Users/obabalola/AppData/Local/Python/bin/python.exe" "${CLAUDE_PROJECT_DIR}/tools/hooks/mlv-never-authorized.py" --project-dir "${CLAUDE_PROJECT_DIR}"` with
   matcher `Bash|PowerShell|Write|Edit|NotebookEdit`, alongside the existing hooks — the SAME absolute-interpreter pattern those
   hooks use; the file records that `py -3` hooks silently never executed until 2026-08-09 (S54). **One acceptance case extracts
   the registered command string from `.claude/settings.json`, executes it verbatim from a disposable worktree with one benign
   ALLOW and one harmless DENY payload, and asserts exit 0 and exit 2.** **It lives in `tools/hooks/test_registration_path_local.py`,
   outside the repo-hygiene discovery pattern, because the command names a per-user absolute interpreter that exists on no
   hosted runner (O81): it is a LOCAL gate the hub runs on the branch and again after the 0.1 conflict resolution. Hosted CI
   proves the RULES only.** **Rule from this repo's own history:**
   a hook's script must live on the SAME REF as the tree it guards — it does, because both are tracked.
4. **`$D\Start-EditingLane.ps1`** (already written; hashed in the manifest) resolves `Invoke-Lane.ps1` and the hook FROM THE
   LANE'S WORKTREE, refuses Codex lanes with write access, refuses a missing/drifted 0.05 receipt, refuses `invoke-lane-stale`
   when the worktree's `Invoke-Lane.ps1` lacks `-AllowedTools` (no exemption, not even for the probe), refuses a prompt without
   a `CLIP_OR_NONE:` line, refuses `hook-unregistered` unless the worktree's `.claude/settings.json` carries the EXACT pinned hook command
   under `PreToolUse` as a `type: command` entry (matcher, type and command all exact AND case-sensitive; never a substring — S89/S92/S94/O98), sets `MLV_LANE_PROMPT`, and passes the explicit allowlist. **Direct `Invoke-Lane.ps1 -AllowEdits` is
   forbidden for every lane, and `Invoke-Lane.ps1` itself refuses without the receipt (0.1 (e)).**

## Receipt (written by the hub in 0.1, AFTER the merge and AFTER the canonical checkout is detached onto `fork/master`)
`$D\receipts\0.05-hook-enforced.json` = `{hookSha256, hookTestSha256, wrapperSha256, mergeSha, canonicalHeadSha, testRunUrl,
casesDeny, casesAllow, solVerdictPath, hookWiredProof, divergentLocalMaster}` — `hookWiredProof` is the hook's stderr line from one
attempted known-DENY act in the RESTARTED hub session (O62/O66); without it the receipt does not satisfy 0.15. `hookSha256` is the sha of `tools/hooks/mlv-never-authorized.py` at `mergeSha`; the
wrapper and `Invoke-Lane.ps1` compare the LANE WORKTREE's copy against it. 0.05 has already created `$D\receipts\` and appended the first row of `required-checks-live.jsonl` there (NA-9's input); this receipt is
made by the hub's own session AFTER it has (a) run `git branch preserve/local-master-20260906 master`, (b) `git fetch fork
master`, (c) `git switch --detach fork/master` — NEVER a fast-forward, which aborts today (`merge-base --is-ancestor master
fork/master` is false; local master is 2 ahead / 6 behind) — and (d) RESTARTED its session in `$R` so it actually loads the
project hook, recording the stderr of one known-DENY act (the INERT payload `if ($false) { setx ANTHROPIC_PROBE_TOKEN x }` — it exercises the `ANTHROPIC_` prefix rule, names no real credential, and an UNWIRED hook merely executes the false branch without touching the environment; S73) as `hookWiredProof` (O70/O72). NA-2's
carve-out allows the write.

## Acceptance
Locally on the branch: `python -m unittest tools.repo_hygiene.test_mlv_never_authorized -v` green with `Ran N tests`, N ≥ register rows + controls (N = 0 is RED —
O86) AND
`python -m unittest tools.hooks.test_registration_path_local -v` green; prove the rule suite can FAIL by disabling one rule
(red), restore (green). In CI on the PR: the RULE suite green on both matrix legs (the registration case is local-only — O81). **A scratch run of the canonical compound
against a SAME-DRIVE failure — a read-only target AND a missing parent directory — halts before the delete and leaves the scratch marker in place;
a nonexistent-drive path must NOT be the falsifier, because it exercises parameter binding rather than the write (it is closed by the leading
`$ErrorActionPreference = 'Stop'` and is measured as its own case) (S101/O128).** **No case may depend on a path under
`.claude-state\`, which is gitignored and absent from every hosted checkout — a suite green locally and red in CI is the O81 failure this
clause exists to prevent (O97).** After merge: the receipt exists;
`Start-EditingLane.ps1 -Lane luna -PromptFile <existing> -Card y -WorkDir <wt>` prints `REFUSED: codex-lane-never-edits`; with the
receipt's `hookSha256` altered by one character it prints `REFUSED: hook-drifted` (prove once, restore); hook runtime under
200 ms per call (measure).

## NEVER-AUTHORIZED
All ten NA items apply to the hub while it writes this. Falsifier commands exist only as JSON payloads in the test.
