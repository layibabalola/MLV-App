# Bounded model processes

`tools/coordination/Invoke-Lane.ps1` starts each Claude CLI beneath an inert
PowerShell host. The launcher assigns that host to a Windows job with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before sending provider configuration.
Claude and its descendants inherit the job. Losing the launcher closes its
job handle and terminates those descendants. Assignment failure starts no
provider and terminates the inert host.

The launcher writes a `running`, incomplete receipt with process identities
before delivering the prompt. Normal completion replaces it atomically. If
the launcher is forcibly terminated, the receipt remains incomplete; it cannot
prove delivery or zero spend. Receipt I/O failures do not retain the job.
Setup, startup and provider execution share one monotonic timeout budget. The
UTC deadline projects that budget from the same initial start time; the stopwatch
enforces it. An exhausted setup budget prevents the host or provider from starting.

Every Claude lane receives
`--disallowedTools Agent,Task,Monitor,ScheduleWakeup,CronCreate,CronDelete,RemoteTrigger,Workflow,TaskCreate`
(LANE-NO-BACKGROUND-END-TURN-1, 2026-09-22): a headless lane has no later turn, so a
tool that promises one (background jobs, a scheduled wakeup, a cron entry, a remote
trigger) leaves the work stranded exactly like the built-in nested-agent tools this
deny list already blocked. The same list (`$DENIED_TOOLS` /
`$DENIED_TOOLS_DISPLAY` in `tools/coordination/Invoke-Lane.ps1`) is checked at
pre-reservation, so an editing allowlist containing ANY of the nine denied tools is
rejected before reservation, not only `Agent`/`Task`. This is a Claude-CLI-only
mechanism (`--disallowedTools` is a claude flag; codex has no equivalent), so it is
checked only for Claude lanes -- codex lanes keep their previous behaviour. Existing
editing grants, hook
checks, read restrictions and provider-refusal classification still apply. This
blocks the CLI's own tool surface; it is not a claim that Bash permissions prevent
arbitrary external process launches, and `Bash`'s `run_in_background` parameter
cannot be reached by `--disallowedTools` -- it denies tools by name, and
`run_in_background` is a parameter of the `Bash` tool, not a separate tool, so
denying it would require denying `Bash` entirely, which editing lanes need. Round 2
(hub ruling, 2026-09-22) removed a `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` child-
environment flag that stood here through round 1: `docs/never-authorized.json` NA-3
prohibits assigning ANY `CLAUDE_CODE_*` variable, and widening that rule is an
authority change the hub will not make. This PR does not ADD any
`ANTHROPIC_`/`OPENAI_`/`CLAUDE_CODE_` assignment; the pre-existing
`CLAUDE_CODE_EFFORT_LEVEL` assignment described below (from 8ad69168, 2026-09-07,
previously reviewed) is out of scope for this PR and is tracked separately under
card NA3-CHILD-ENV-SCOPE-1 -- whether NA-3's prefix rule covers a launcher setting a
child process's environment in code is a governance interpretation that PR does not
decide here.

**Blocking `run_in_background` (round 6, swarm ruling, 2026-09-22; matcher, fail-
closed protocol, launch self-test and tamper detection added round 7; registered
in EXEC FORM since round 13).** Every Claude-engine lane -- read-only and
editing alike -- receives a per-run `--settings` JSON beside its receipt
(`<lane>-NNN.settings.json`) that registers a `PreToolUse` hook on the
`Bash|PowerShell` matcher (round 7: widened from `Bash` alone -- `PowerShell`
carries the same `run_in_background` parameter and is granted to every editing
lane by `docs/Start-EditingLane.ps1`). The hook script is
`tools/coordination/lane-no-background.py`, copied into this run's already-
reserved, board-rooted slot (`<lane>-NNN.lane-no-background.py`, beside the
receipt, under `.claude-state/fleet-runs` -- itself an NA-2 protected tail no
lane can write to) before argv is built, and registered by that copy's path --
never a path inside the lane's own writable worktree (round 7, sol major 3;
`docs/Start-EditingLane.ps1` runs `Invoke-Lane.ps1` FROM `$WorkDir` for every
editing lane, which sits inside that same worktree). The copy is hashed
immediately (`sha256`).

**Round 13 (hub ruling): registered in EXEC FORM, which removes the shell
entirely rather than proving it.** Rounds 8-12 spent five rounds self-testing
shell CANDIDATES, because a shell-form `command` (a bare string with no `args`
field) hands the executable choice to Claude Code's own, undocumented shell
auto-detection -- each closed hole (a WSL stub on PATH, PATH precedence, an
inherited `CLAUDE_CODE_GIT_BASH_PATH`) left the shape of one more behind it.
The vendor docs name the exit directly (code.claude.com/docs/en/hooks, "Exec
form and shell form" / "Command hook fields", fetched 2026-09-22):

> Shell form runs when `args` is absent. The `command` string is passed to a
> shell... Exec form runs when `args` is present. Claude Code resolves
> `command` as an executable... and spawns it directly with `args` as the
> argument vector. There is no shell. ... `shell` ... Ignored when `args` is
> set.

The hook is now registered as `{"type": "command", "command": "<resolved
python.exe>", "args": ["<hook copy path>"]}` -- no command STRING, no `shell`
field. Claude Code spawns exactly that executable with exactly that argv;
there is no auto-detection left to out-guess and no candidate list to
enumerate. Rounds 8-12's shell-selection machinery -- the all-candidates
resolver, the Git Bash and PowerShell resolvers, the PowerShell-fallback and
per-shell-kind self-test loop, the `$hookCommand` string builders, and the
`MLV_GIT_BASH`/`MLV_LANE_POWERSHELL_EXE` overrides and their fixture tests --
is DELETED, not disabled.

**What still needs resolving: the INTERPRETER.** `command` still names an
executable this launcher chooses (`argv[0]`), so `$PYTHON_EXE` is still
resolved at runtime by `Resolve-LaneExecutable` -- an authoritative override
(`MLV_LANE_PYTHON_EXE`, test/operator use only), then curated known install
locations, then `PATH` as a last resort, the same order round 9 established
after a dev-machine-only pin broke hosted CI. No interpreter resolving
refuses the launch before a hook is registered
(`background-gate-interpreter-not-found`) -- an unrunnable command denies
nothing.

**The self-test is now exact, not a candidate proof.** With no shell in the
path, it invokes `& $PYTHON_EXE $hookCopyPath` through PowerShell's call
operator with an ARGUMENT ARRAY -- the identical spawn exec form itself
performs -- fed the synthetic deny payload on stdin. Round 11's finding
survives unchanged: exit 2 alone is not proof the hook denied THIS payload
(Python itself exits 2 for a missing script path), so the self-test also
requires the hook's own deny-reason text (`"headless lane"`) in the captured
output, plus a positive control (the same call against a non-background
payload, required ALLOWED) before trusting it. One executable, one argv, one
proof. Failure refuses the launch (`background-gate-selftest-failed`, naming
the captured exit code and output). The receipt's `authority` block now
records `backgroundGateInterpreterPath`/`-Source` and `backgroundGateForm:
'exec'` -- replacing round 9-12's `backgroundGateShellKind`/`-Path`/`-Source`/
`-ValidatedCandidates`, none of which mean anything once there is no shell.
Tamper detection is unchanged: the copy is re-hashed after the run, and a
mismatch overwrites `authority.backgroundGate` to `'background-gate-tampered'`
with a detail field. **Still detection at exactly two points in time**
(launch, exit), never continuous monitoring -- a lane that restores the
original bytes before its own process exits leaves both hashes matching, with
no tamper recorded. Neither check covers a rewrite of the WORKTREE'S SOURCE
copy of `lane-no-background.py` or of `Invoke-Lane.ps1` itself. **Known gap,
not yet closed:** NA-10 would be the natural place to guard both as gate
tails a lane may write only at the board venue, but `mlv-never-authorized.py`
is itself one of NA-10's guarded tails, so a lane-worktree session cannot make
that edit; filed as card NA10-LANE-NO-BACKGROUND-TAIL-1, not done here.

**Round 12 (fable minor), narrowed round 13: the deny-reason substring is a
hand-synchronized literal, now cross-referenced and tested.** `"headless
lane"` is load-bearing in `lane-no-background.py`'s `DENY_REASON` and
`Invoke-Lane.ps1`'s `$backgroundGateExpectedDenySubstring`. Round 12 also
cross-referenced this test file's own `_bash_candidate_runs_the_hook` helper
(a third site) -- deleted in round 13 along with the shell-candidate machinery
it classified. `DENY_REASON` carries a comment naming the other site, and
`test_deny_reason_substring_matches_launcher_selftest` reads both directly,
then runs the real hook and checks the substring appears in its actual
runtime stderr, not merely equal to a copy-pasted constant.

The hook script reads the tool-call JSON on stdin and denies the call (exit 2,
one line on stderr -- the same fail-closed protocol
`tools/hooks/mlv-never-authorized.py` uses) whenever `tool_input.run_in_background`
is truthy, for ANY `tool_name` -- a tool-name-agnostic second gate beneath the
matcher, so a future tool carrying the same parameter is refused without a
second wiring change. Every other input -- the flag absent or false, no
`tool_input.run_in_background` key -- allows silently (exit 0, no output).
**Fail-closed on the input itself:** missing or empty stdin, non-JSON stdin, a
top-level JSON value that is not an object, or a present `tool_input` that is
not an object, all DENY (exit 2) rather than allow -- a hook that cannot read
its own input has no basis to conclude the call is safe. It remains pure: no
side effects, no network, no filesystem access beyond stdin/stdout/stderr. The
Read deny rules for the manifest-surface paths stay conditional on
`-AllowBulkReads`; the hook registration is unconditional. This is a
Claude-CLI-only mechanism (`--settings` and its hook keys are claude-specific),
so Codex lanes get neither the settings file nor the hook. The receipt's
authority block records `backgroundGate = 'denied-by-settings-hook'` and
`backgroundHookSha256` (the copy's hash at launch, proven-denying by the
self-test) for every Claude lane.

Containment of background work rests on three things, with a fourth filed and
not yet landed: the `--disallowedTools` deny list above (nested-agent and
callback-promising CLI tools by name); this settings-file hook, proven by a
launch-time self-test and checked for tamper after the run (the mechanism that
actually denies `run_in_background` on the `Bash`/`PowerShell` tool call that
would start it); and the dirty-no-commit receipt below as an after-the-fact
backstop for anything that reaches a background shell by some other route --
auto-backgrounding by the harness, if any, would not necessarily carry the
`run_in_background` flag this hook keys on. **Residual routes, stated
honestly:** a shell built-in that backgrounds work WITHOUT setting the
`run_in_background` parameter -- `Start-Job`, a trailing `&` on an otherwise-
foreground PowerShell command, or `nohup ... &` inside an otherwise-foreground
Bash call -- is invisible to this hook, which inspects only the tool-call
parameter, never the command text; such a job still ends when the command that
started it returns control to the CLI turn, so it does not survive past the
lane's last turn the way `run_in_background: true` does, but it is not
covered by denial here. Arbitrary shell descendants remain confined to the
owning Windows job unless an independently authorized external service starts
them (see the job-object section above); the job closes them, it does not
prevent them from being started.

A receipt is also never marked `complete` (or state `ended-incomplete` is forced)
when an EDITING Claude lane's own worktree is still at the lane's starting sha and
carries tracked-file dirt the lane itself introduced -- positive evidence in the
provider's answer does not outrank a tree that never moved; see
`workEvidence.reason: dirty-worktree-no-commit` in
`tools/coordination/Invoke-Lane.ps1`. **What this detects, precisely:** the check
snapshots a per-path content identity for every tracked (non-`??`) dirty path
before the child starts, and compares it against the same snapshot after exit; a
path counts as lane-introduced if it is newly dirty or its content identity
changed, so pre-existing dirt in an already-dirty worktree never flips a receipt on
its own, but a further edit to an already-dirty tracked file does (round 3: a raw
porcelain status line stays textually identical across such a further edit, so
round 2's line-text comparison could not see it; comparing content identity per
path can). Round 5: that per-path identity is now `git hash-object -- <path>` of
the working-tree file itself (a `DELETED` marker when the path is absent), not a
hash of rendered `git diff HEAD -- <path>` text -- diff rendering for a binary file
collapses to the fixed text `Binary files a/<path> and b/<path> differ` regardless
of which bytes are actually on disk, so hashing that text could not tell two
different binary edits of the same path apart; `hash-object` reports the identity
of the bytes git would actually commit. **What this does NOT detect:** it only
ever looks at the tracked-file working tree, so background work that changes no
tracked file -- a build, a test run, a Bash job reading files or writing only
untracked scratch output -- leaves no trace here and is never caught by this
mechanism, regardless of whether that work is still running in the background when
the lane's last turn ends. It applies only to `-AllowEdits` Claude lanes -- a
read-only lane can never move HEAD by construction, so applying the check there
would degenerate to "was the surrounding checkout dirty," a fact outside a review
lane's control; Codex lanes are excluded the same way. When the check fires it
appends to any existing `workEvidence.reason` rather than overwriting it, so a
prior failure classification (e.g. `subtype-error_max_turns`) is never masked.

**Fail-closed git capture (round 5).** Every `git` call this check makes (`status
--porcelain=v1 -z` for the snapshot, `hash-object` per dirty path) goes through
`Invoke-GitCaptureUtf8`, which returns `{ok, stdout, exitCode, error}` instead of a
bare string -- a caller must check `.ok` before touching `.stdout`, so a failure to
start the process, a failure to read its output, or a non-zero git exit can never
be silently read back as `''`, which previously collided with git's own genuine
empty-and-successful output (a clean tree, an unchanged diff) and would have made
a git failure indistinguishable from "nothing is dirty." If either the PRE-launch
or the POST-exit snapshot cannot be taken, the receipt records
`dirtyCheck: 'unavailable'` with `dirtyCheckReason` naming which snapshot failed
and why, and `workEvidence` is left completely untouched -- an unavailable check
never manufactures a false `ended-incomplete` (git being unreachable is not
evidence of a dirty tree) and never falls back to claiming `dirtyCheck: 'clean'`
(an unavailable check has no basis to claim the tree was actually verified).
`dirtyCheck` also records `'not-applicable'` (the gate did not apply to this lane,
or HEAD moved) and `'clean'`/`'dirty'` for a check that actually ran to completion.
Round 7 (sol minor): the pre-launch and post-exit `git rev-parse HEAD` calls the
gate takes its base and comparison shas from are routed through the same
`Invoke-GitCaptureUtf8` and are held to the same standard -- a failed rev-parse is
now distinguishable from a legitimately inapplicable gate (no base sha because the
prior call succeeded and simply returned nothing) instead of both collapsing into
the same falsy-`$BaseSha` reading, which previously misclassified a failed
pre-launch rev-parse as `'not-applicable'` and a failed post-exit rev-parse as the
misleading `'HEAD moved'`. Both now record `dirtyCheck: 'unavailable'` with
`dirtyCheckReason` naming exactly which rev-parse call failed and why.

`-ReasoningEffort low` explicitly requests low effort for one invocation. Codex
receives its normal reasoning configuration argument. Claude receives
`CLAUDE_CODE_EFFORT_LEVEL=low` in the child environment -- the same pre-existing,
out-of-scope-for-this-PR assignment tracked under NA3-CHILD-ENV-SCOPE-1 above. The
receipt records the override; user settings and the parent environment are
unchanged. Omitting it preserves the existing lane defaults. Codex continues
through its existing direct process path; the new Windows job containment applies
to Claude.

Run the real Windows fixture suite with:

```powershell
py -3 -m pytest -q tests/coordination/test_lane_containment.py
```

Disposable fake providers exercise argv, stdin, JSON, effort, normal completion,
startup and execution deadlines, parent loss, assignment failure, and receipt
write failure. Cleanup targets fixture-owned process identities only. These
tests run in the existing Windows coordination check. They invoke no model or
network service. Provider lifecycle completion is not card acceptance: tests,
commit, independent review, merge and target verification remain separate gates.

Read-only Claude lanes receive a model-visible capability notice through the
supported `--append-system-prompt` argument. It states that only Read, Grep and
Glob are permitted, directs reviews to hub-exported evidence, and prohibits
retrying unavailable shells. The receipt records the exact notice. User prompt
bytes remain unchanged. This notice informs the model; the existing permission
allowlist and Agent/Task deny still enforce tool permissions. The hub must
export any required Git diff or hosted evidence before starting such a review.

The production dispatcher must use the reviewed launcher revision and the
applicable updated execution-control receipt before resuming editing dispatch.
This change does not enable the workstream loop or rewrite historical receipts.
