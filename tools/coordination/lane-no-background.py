#!/usr/bin/env python3
"""LANE-NO-BACKGROUND-END-TURN-1 round 6: PreToolUse hook that denies backgrounded Bash.

Wired per-lane by tools/coordination/Invoke-Lane.ps1, which writes a `--settings` file for
every Claude-engine lane registering this script against the `Bash` matcher. A headless
`claude -p` lane gets no later turn, so a call with `run_in_background: true` promises a
callback the process cannot receive -- the same shape `--disallowedTools` already denies for
Monitor/ScheduleWakeup/CronCreate/CronDelete/RemoteTrigger/Workflow/TaskCreate. That mechanism
cannot reach this case: `run_in_background` is a parameter of the `Bash` tool call, not a
separate tool name, so denying it by name is not possible. This hook closes exactly that gap,
without touching NA-3 or adding any CLAUDE_CODE_*/ANTHROPIC_*/OPENAI_* environment assignment
(swarm ruling, 2026-09-22) -- see docs/lane-containment.md.

Contract
--------
stdin  : one JSON object, ``{"tool_name": ..., "tool_input": {...}}`` (Claude Code's
         PreToolUse hook payload).
deny   : ``tool_name == "Bash"`` and ``tool_input.run_in_background is True`` -- emits the
         JSON-decision protocol (``hookSpecificOutput.permissionDecision: "deny"``) on
         stdout and exits 0. This is deliberately NOT the exit-2/stderr protocol
         ``tools/hooks/mlv-never-authorized.py`` uses; Claude Code reads the decision from
         stdout JSON when present.
allow  : every other input -- the flag absent or false, any non-Bash tool_name, or stdin
         that is missing/malformed/not the expected shape -- allows silently: exit 0,
         nothing on stdout. Registration already scopes this hook to the `Bash` matcher; the
         `tool_name` check here is a second, explicit gate so the script's own behaviour does
         not depend on that registration being correct.

Pure. No side effects, no network, no filesystem access beyond stdin/stdout.
"""

import json
import sys

DENY_REASON = (
    "headless lane: run this command in the foreground with an explicit timeout; "
    "a headless lane has no later turn"
)


def should_deny(payload):
    if not isinstance(payload, dict):
        return False
    if payload.get("tool_name") != "Bash":
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    return tool_input.get("run_in_background") is True


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if should_deny(payload):
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": DENY_REASON,
                }
            },
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
