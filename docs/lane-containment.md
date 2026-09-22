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
and `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in the child environment
(LANE-NO-BACKGROUND-END-TURN-1, 2026-09-22): a headless lane has no later turn, so a
tool that promises one (background jobs, a scheduled wakeup, a cron entry, a remote
trigger) leaves the work stranded exactly like the built-in nested-agent tools this
deny list already blocked. An editing allowlist containing any denied tool is
rejected before reservation. Existing editing grants, hook checks, read restrictions
and provider-refusal classification still apply. This blocks the CLI's own tool
surface; it is not a claim that Bash permissions prevent arbitrary external process
launches. Those launches remain confined to the owning job unless an independently
authorized external service starts them.

A receipt is also never marked `complete` (or state `ended-incomplete` is forced)
when the child's own worktree is still at the lane's starting sha with tracked files
left dirty -- positive evidence in the provider's answer does not outrank a tree that
never moved; see `workEvidence.reason: dirty-worktree-no-commit` in
`tools/coordination/Invoke-Lane.ps1`.

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
