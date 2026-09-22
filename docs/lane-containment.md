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
closed protocol, launch self-test and tamper detection added round 7).** Every
Claude-engine lane -- read-only and editing alike -- now receives a per-run
`--settings` JSON written beside its receipt (`<lane>-NNN.settings.json`) that
registers a hook on the `Bash|PowerShell` matcher (round 7: widened from `Bash`
alone -- `PowerShell` carries the same `run_in_background` parameter and is
granted to every editing lane by `docs/Start-EditingLane.ps1`). **This is not a
new grant of a `--settings` file: before round 6, a non-bulk-reads lane of
EITHER kind -- read-only or `-AllowEdits` -- already received one carrying only
the `Read` deny rules, gated on `-not $AllowBulkReads` alone, never on
`-AllowEdits`.** Round 6 made the file unconditional (every Claude lane gets one,
bulk-reads or not) and added the hook registration to it; round 7 widened the
hook's matcher.

The hook script is `tools/coordination/lane-no-background.py`, but the command
`--settings` registers is never a path inside the lane's own writable worktree
(round 7, sol major 3): before argv is built, the launcher copies the source
script into THIS run's already-reserved, board-rooted slot
(`<lane>-NNN.lane-no-background.py`, beside the receipt, under
`.claude-state/fleet-runs` -- itself an NA-2 protected tail no lane can write to)
and points the registration at that copy, never at the source. The copy is hashed
immediately (`sha256`). **The registered command STRING is built exactly once
per resolved shell kind** (`$hookCommand`) and used unchanged for both the
`--settings` entry and the launch self-test, so the two can never name different
commands. **Round 8 (sol major 1): the self-test runs that exact string through
the real shell it is registered for**, fed the same synthetic
`{"tool_name":"Bash","tool_input":{"run_in_background":true}}` payload on stdin,
not by invoking the interpreter and the copy as two decomposed PowerShell argv
tokens the way round 7 did. Claude Code's own docs state a shell-form `command`
hook (one without an `args` field, which is what this launcher registers) is
executed via `sh -c` on macOS/Linux, **Git Bash on Windows when it is installed,
or PowerShell as its own documented fallback otherwise** -- and a `shell` field
lets a hook entry choose explicitly instead of relying on that auto-detection
(code.claude.com/docs/en/hooks, "Exec form and shell form" / "Command hook
fields"). A self-test invoking a different shell than the one actually
registered (or bypassing the command string entirely) can pass while the real
registration is broken by bad quoting or a wrong path baked into the string
itself.

**Round 10 (sol major 1a): Git Bash absence is a registered, self-tested
PowerShell fallback, never a launch refusal.** Round 9 refused every Claude lane
outright whenever Git Bash could not be resolved -- stricter than the product
itself, since Claude Code would fall back to PowerShell rather than fail, making
round 9's refusal a self-inflicted outage on any Windows host without Git Bash
(every one still has PowerShell). Git Bash stays PREFERRED when it resolves,
unchanged from round 9; only its absence now tries a second candidate --
Windows PowerShell at its fixed System32 location or on `PATH`, resolved via the
same override/known-location/PATH `Resolve-LaneExecutable` tiers -- before
refusing. Whichever shell wins is named explicitly on the hook entry's `shell`
field (`"bash"` or `"powershell"`), so Claude Code's own shell auto-detection
never has to independently agree with what the self-test just proved denies;
the receipt's `authority.backgroundGateShellKind` records the same value.
**The PowerShell command form is not the Git Bash form with a different
shell wrapped around it -- two measured PowerShell-specific hazards make it
structurally different, both load-bearing:** (1) `powershell.exe -Command
'"<path>" "<arg>"'` is a parse error without a leading call operator `&`,
unlike `bash -c`, which invokes a bare quoted string directly; and (2) even
with `&`, a *nested* `powershell.exe -Command "& exe args"` does not propagate
the invoked process's exit code as its own -- measured directly: the hook's
real exit 2 collapsed to exit 1 from the outer `powershell.exe` with no
`; exit $LASTEXITCODE` suffix. Per the vendor hooks reference's own exit-code
table, exit 1 without a blocking JSON decision is a **non-blocking error** for
`PreToolUse` -- the tool call proceeds -- so a PowerShell-form command missing
that suffix would silently fail OPEN in exactly the one case this fallback
exists to cover. The registered PowerShell command is therefore
`& "<python>" "<hook copy>"; exit $LASTEXITCODE`, and the self-test runs that
exact string through `powershell.exe -Command`, the same way the Git Bash
self-test runs its form through `bash -c`. Only "neither Git Bash nor
PowerShell resolves" refuses the launch now (`background-gate-shell-not-found`,
naming both attempted resolutions).

**Round 11 (sol major / fable minor 1): a RESOLVED shell is not the same as a
USABLE one -- selection now falls back on self-test failure, not only on
resolution failure.** Round 10 committed to Git Bash the instant
`Resolve-LaneExecutable` returned a hit, by file existence alone -- so on a host
with the WSL feature enabled but no Git for Windows, the System32 WSL launcher
stub also "resolves" (it is a real file on `PATH`), the self-test then fails,
and round 10 refused the whole launch without ever trying the PowerShell
fallback sitting right there: the exact "stricter than the product" outage
round 10 set out to remove, surviving in this one sub-case. There is no
separate classification step -- the self-test already proves whether a
candidate can run this hook's exact command form, so it now runs per candidate,
in preference order (bash, then PowerShell), moving to the next on ANY
self-test failure and refusing only once every resolved candidate has failed.
**Round 11 (sol minor / fable minor 2): exit 2 alone is no longer sufficient
proof.** The hook's own fail-closed empty-stdin path also exits 2, and Python
itself exits 2 for a missing or misquoted script path -- so a registration-only
regression of either shape could pass a bare exit-2 self-test while every
matched call was then over-blocked for the wrong reason. The self-test now also
requires the hook's own deny-reason text (`"headless lane"`) in the captured
output, and adds a positive control: the same registered command string run
against a non-background payload must be ALLOWED (exit 0) before the candidate
is trusted -- a subject and a control that differ is what proves the gate
discriminates, not exit 2 in isolation. Both checks apply per candidate in the
fallback loop above.

**Round 12 (sol major, restated): `shell` pins a KIND, not an executable --
Claude Code resolves the actual bash.exe/powershell.exe independently of this
launcher, and the self-test must now prove every candidate that independent
resolution could plausibly land on, not just the one this launcher's own
precedence preferred.** Established from the vendor docs directly
(code.claude.com/docs/en/setup, "Set up on Windows", fetched 2026-09-22; full
fetch transcript in this round's summary.md):

> With Git for Windows, Claude Code uses Git Bash for the Bash tool. If Claude
> Code can't find Git Bash, set the path in your settings.json file:
> `{"env": {"CLAUDE_CODE_GIT_BASH_PATH": "C:\\Program Files\\Git\\bin\\bash.exe"}}`

and (code.claude.com/docs/en/hooks, "Command hook fields"):

> `shell` ... Accepts `"bash"` or `"powershell"`. Defaults to `"bash"`, or to
> `"powershell"` on Windows when Git Bash isn't installed. ... Ignored when
> `args` is set.

Two things follow, stated plainly per the producer brief: (1) the `shell`
field selects which FORM Claude Code runs the command as, never which
executable runs it -- Claude Code's own Git-Bash discovery decides that,
independently of anything this launcher resolved for its self-test; (2) the
only DOCUMENTED way to pin Claude Code's own choice is
`env.CLAUDE_CODE_GIT_BASH_PATH` in `--settings` -- which NA-3 forbids this
launcher from ever setting, no exception, so that gap cannot be closed by
registration alone. Round 11's self-test proved ONE candidate -- whichever
this launcher's own override/known-location/PATH precedence preferred -- and
trusted the `bash` kind on that single proof. Sol's round-11-restated repro:
put a working Git Bash somewhere only this launcher's own resolution would
find (or, equivalently, let a SECOND Git Bash sit on `PATH` ahead of the
curated known location), and the self-test proves the one this launcher
prefers while staying silent about a sibling Claude Code's own,
undocumented, discovery might invoke instead -- the receipt then claims
`denied-by-settings-hook` for a shell that was never actually proven.

Since Claude Code's real selection algorithm is not published beyond
"installed or not", the strongest proof this launcher can construct without
the forbidden environment variable is to self-test EVERY candidate a kind
could plausibly resolve to, and trust that kind only when ALL of them pass --
narrowing the gap, not eliminating it outright. `Resolve-LaneExecutableAllCandidates`
(`tools/coordination/Invoke-Lane.ps1`) replaces the single-winner resolver for
this purpose: when no override env var is set, it returns every KNOWN
LOCATION that exists (not just the first) plus the `PATH` hit, deduplicated
by resolved path; when an override IS set, it returns exactly that one path,
unchanged from round 11 -- the override is this launcher's own test/operator
mechanism (`MLV_GIT_BASH` / `MLV_LANE_POWERSHELL_EXE`), which Claude Code does
not read, so a deliberately-forced candidate is not made safer by also
probing whatever else the host happens to expose. The launch self-test loop
now tests every candidate in a kind's list -- never short-circuiting on the
first pass or the first failure, so every attempt lands in the receipt or the
refusal message -- and disqualifies the WHOLE kind if even one candidate
fails either self-test half, even when the precedence winner itself passed.
SELECTION (which path/source the receipt and the `--settings` file actually
name) still follows the same override-then-known-location-then-PATH
precedence as round 11; only the GATE for trusting a kind at all changed.
The receipt's `authority.backgroundGateShellValidatedCandidates` names every
candidate that was self-tested and passed for the winning kind, answering
"which candidates were validated" rather than merely asserting that one was.
`test_resolve_prefers_known_location_over_path_when_both_resolve` and its new
sibling `test_launch_falls_back_to_powershell_when_a_path_candidate_fails_
selftest_even_though_known_location_passes`
(`tests/coordination/test_lane_containment.py`) cover both directions: two
independently-working candidates still select by precedence, and one
candidate failing disqualifies the kind entirely and falls back to
PowerShell -- the shape sol's round-11-restated repro constructs. **Residual,
stated honestly:** this still cannot PROVE Claude Code will pick one of the
candidates this launcher discovered at all, only that it cannot find a
discoverable-but-broken sibling among them; a bash.exe reachable only through
some means neither known-location nor `PATH` search would surface (a
non-standard install Claude Code's own detection nonetheless finds) remains
outside what this launcher can self-test without `CLAUDE_CODE_GIT_BASH_PATH`,
which NA-3 forbids setting.

**Round 12 (fable minor): the deny-reason substring is a hand-synchronized
literal across three files, now cross-referenced and tested.** `"headless
lane"` is load-bearing in `lane-no-background.py`'s `DENY_REASON`,
`Invoke-Lane.ps1`'s `$backgroundGateExpectedDenySubstring`, and
`test_lane_containment.py`'s `_bash_candidate_runs_the_hook` helper. `DENY_REASON`
now carries a comment naming the other two sites and the consequence of
rewording it alone (every Claude lane launch refuses its self-test --
fail-closed, never fail-open, but an outage nonetheless), and
`test_deny_reason_substring_matches_launcher_and_test_helper` reads all three
sites directly and fails if any one disagrees.

**Round 9: both the interpreter and the shell are RESOLVED at runtime, never
pinned.** The dev-machine-only pins broke hosted CI: the real job log (run
35758428267, job 106850299120, 2026-09-22) shows Git Bash ran fine on the
runner -- the self-test's exit 127 was bash's own "No such file or directory"
for the pinned `$PYTHON_EXE`, not for bash. Both now resolve the same way, in
order: (1) an authoritative per-variable override (`MLV_GIT_BASH`,
`MLV_LANE_PYTHON_EXE`) that never falls through, even to a real hit, so a test
forcing "unresolvable" is deterministic regardless of host layout; (2) known,
verified install locations, including the previous pins; (3) `PATH` as a last
resort -- deliberately last, not first as a shell resolver would naturally do
it: `Get-Command bash.exe` on the dev machine resolves to the Windows-shipped
WSL launcher stub ahead of real Git Bash (System32 precedes Git's `bin` on
`PATH`), which would have refused every default launch on any box with WSL
enabled. **No Python interpreter resolving refuses the launch before a hook
command is ever built** (`background-gate-interpreter-not-found`) -- an
unrunnable command denies nothing. **Round 10: the equivalent shell refusal
(`background-gate-shell-not-found`) now fires only when NEITHER Git Bash NOR
PowerShell resolves** (see above) -- Git Bash alone failing to resolve is no
longer sufficient on its own. The receipt's `authority` block records each
resolved path, which tier found it, and (round 10) which shell kind won
(`backgroundGateShellKind`, `backgroundGateShellPath`/`-Source`,
`backgroundGateInterpreterPath`/`-Source`). The self-test requires the fail-closed
deny (exit 2) -- a launch whose self-test does not pass never starts the
provider; the receipt records `failure: "background-gate-selftest-failed: ..."`
instead. After the run, the copy is re-hashed; a lane with Bash/Edit access is
not confined to `--add-dir`, so if the two hashes differ the receipt's
`authority.backgroundGate` is overwritten to `'background-gate-tampered'` with a
detail field, rather than silently continuing to claim
`'denied-by-settings-hook'`. **This is detection at exactly two points in
time -- the instant the copy is written before launch, and the instant the
provider process exits -- never continuous monitoring while the provider runs:**
a lane that rewrites the copy mid-run and then restores the original bytes
before its own process exits leaves the two hashes matching, and the receipt
still claims `'denied-by-settings-hook'` with no tamper recorded, even though
the gate was provably absent for however long the rewrite was in effect.
Neither the self-test nor the re-hash proves anything about a FUTURE launch
from this same worktree if the lane instead rewrote the WORKTREE'S SOURCE copy
of `tools/coordination/lane-no-background.py` (the file the next launch would
copy from) or of `Invoke-Lane.ps1` itself (the file that performs the
copy-and-wire step). **Known gap, not yet closed (round 7):** NA-10 in
`tools/hooks/mlv-never-authorized.py` is the natural place to guard both paths as
gate tails a lane may write only at the board venue, the same way it already
guards `.claude/settings.json` and its own script -- but `mlv-never-authorized.py`
is itself one of NA-10's guarded tails, so a session running in a lane worktree
(this one included) is refused by NA-10 when it tries to make that edit; it is
attributable ONLY to the board-rooted actor. Filed for a board-venue session to
land as card NA10-LANE-NO-BACKGROUND-TAIL-1, not done here.

The hook script reads the tool-call JSON on stdin and denies the call (exit 2,
one line on stderr -- the same fail-closed protocol
`tools/hooks/mlv-never-authorized.py` uses, not round 6's exit-0-plus-stdout-JSON
protocol) whenever `tool_input.run_in_background` is truthy, for ANY
`tool_name` -- round 7 made the script's own check tool-name-agnostic as a second,
independent gate beneath the matcher, so a future tool carrying the same
parameter is refused on the same basis without a second wiring change. Every
other input -- the flag absent or false, no `tool_input.run_in_background` key --
allows silently (exit 0, no output). **Fail-closed on the input itself (round 7,
sol major 2):** missing or empty stdin, non-JSON stdin, a top-level JSON value
that is not an object, or a present `tool_input` that is not an object, all DENY
(exit 2) rather than allow -- a hook that cannot read its own input has no basis
to conclude the call is safe. It remains pure: no side effects, no network, no
filesystem access beyond stdin/stdout/stderr. The Read deny rules for the
manifest-surface paths stay conditional on `-AllowBulkReads` exactly as before
round 6; the hook registration is unconditional. This is a Claude-CLI-only
mechanism (`--settings` and its hook keys are claude-specific), so Codex lanes
get neither the settings file nor the hook. The receipt's authority block
records `backgroundGate = 'denied-by-settings-hook'` (round 7: renamed from
`backgroundBash`, now that the matcher and the script's own check both cover
PowerShell, and any tool, alongside Bash) and `backgroundHookSha256` (the copy's
hash at launch, proven-denying by the self-test) for every Claude lane.

Containment of background work now rests on three things, with a fourth filed
and not yet landed: the `--disallowedTools` deny list above (nested-agent and
callback-promising CLI tools by name); this settings-file hook, proven by a
launch-time self-test and checked for tamper after the run (the mechanism that
actually denies `run_in_background` on the `Bash`/`PowerShell` tool call that
would start it); and the dirty-no-commit receipt below as an after-the-fact
backstop for anything that reaches a background shell by some other route --
auto-backgrounding by the
harness, if any, would not necessarily carry the `run_in_background` flag this
hook keys on. **Residual routes, stated honestly:** a shell built-in that
backgrounds work WITHOUT setting the `run_in_background` parameter -- `Start-Job`,
or `&` at the end of an otherwise-foreground PowerShell command, or `nohup ... &`
in Bash -- is invisible to this hook, which inspects only the tool-call
parameter, never the command text; such a job still ends when the command that
started it returns control to the CLI turn, so it does not survive past the
lane's last turn the way `run_in_background: true` does, but it is not covered by
denial here. Arbitrary shell descendants remain confined to the owning Windows
job unless an independently authorized external service starts them (see the
job-object section above); the job closes them, it does not prevent them from
being started.

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
