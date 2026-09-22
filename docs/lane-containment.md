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
`--disallowedTools Agent,Task,Monitor,ScheduleWakeup,CronCreate,CronDelete,RemoteTrigger`
(LANE-NO-BACKGROUND-END-TURN-1, 2026-09-22): a headless lane has no later turn, so a
tool that promises one (background jobs, a scheduled wakeup, a cron entry, a remote
trigger) leaves the work stranded exactly like the built-in nested-agent tools this
deny list already blocked. The same list (`$DENIED_TOOLS` /
`$DENIED_TOOLS_DISPLAY` in `tools/coordination/Invoke-Lane.ps1`) is checked at
pre-reservation, so an editing allowlist containing ANY of the seven denied tools is
rejected before reservation, not only `Agent`/`Task`. Existing editing grants, hook
checks, read restrictions and provider-refusal classification still apply. This
blocks the CLI's own tool surface; it is not a claim that Bash permissions prevent
arbitrary external process launches, and **`Bash`'s `run_in_background` parameter is
not technically blocked** -- `--disallowedTools` denies tools by name, and
`run_in_background` is a parameter of the `Bash` tool, not a separate tool, so
denying it would require denying `Bash` entirely, which editing lanes need. Round 2
(hub ruling, 2026-09-22) removed a `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` child-
environment flag that stood here through round 1: `docs/never-authorized.json` NA-3
prohibits assigning ANY `CLAUDE_CODE_*` variable, and widening that rule is an
authority change the hub will not make. Containment of background work now rests on
three things, and none of them technically prevents a lane from starting a
background shell job: the `--disallowedTools` deny list above (nested-agent and
callback-promising CLI tools only), the dirty-no-commit receipt below (the
after-the-fact detector -- a lane that leaves work stranded in a background job
receipts `ended-incomplete`, not `complete`), and the brief-level prohibition on
background work carried in each lane's dispatch prompt. Arbitrary shell descendants
remain confined to the owning Windows job unless an independently authorized
external service starts them (see the job-object section above); the job closes
them, it does not prevent them from being started.

A receipt is also never marked `complete` (or state `ended-incomplete` is forced)
when an EDITING Claude lane's own worktree is still at the lane's starting sha and
carries tracked-file dirt the lane itself introduced -- positive evidence in the
provider's answer does not outrank a tree that never moved; see
`workEvidence.reason: dirty-worktree-no-commit` in
`tools/coordination/Invoke-Lane.ps1`. The check snapshots the tracked (non-`??`)
`git status --porcelain` lines before the child starts and compares them against the
same snapshot after exit: only NEW or CHANGED tracked lines trigger it, so
pre-existing dirt in an already-dirty worktree never flips a receipt. It applies
only to `-AllowEdits` Claude lanes -- a read-only lane can never move HEAD by
construction, so applying the check there would degenerate to "was the surrounding
checkout dirty," a fact outside a review lane's control; Codex lanes are excluded
the same way. When the check fires it appends to any existing
`workEvidence.reason` rather than overwriting it, so a prior failure classification
(e.g. `subtype-error_max_turns`) is never masked.

`-ReasoningEffort low` explicitly requests low effort for one invocation. Codex
receives its normal reasoning configuration argument. Claude receives
`CLAUDE_CODE_EFFORT_LEVEL=low` in the child environment. The receipt records the
override; user settings and the parent environment are unchanged. Omitting it
preserves the existing lane defaults. Codex continues through its existing
direct process path; the new Windows job containment applies to Claude.

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
