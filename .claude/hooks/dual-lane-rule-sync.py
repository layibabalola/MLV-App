#!/usr/bin/env python3
"""PostToolUse reminder: keep dual-lane workflow-rule surfaces IN SYNC.

Why this exists (2026-07-01, Layi): a dual-lane workflow-rule change is
MULTI-SURFACE — the canonical protocol doc, the skill (what a fresh session
actually runs), AGENTS.md (agent policy), project-memory (recall), and a
ledger SEQ (announcement). Updating one and forgetting the others already
happened: the silent-liveness / no-phantom-progress rules landed in the
protocol doc + memory but the SKILL.md lagged until the human caught it.
Memory alone kept failing, so this hook injects a propagation checklist
whenever a rule surface is edited, so the sync is not silently skipped.

Mechanism (per Claude Code hooks docs, confirmed): a PostToolUse hook reaches
the MODEL by emitting, on stdout with exit 0:
    {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                            "additionalContext": "<reminder>"}}
NOTE: for PostToolUse, stderr + exit 2 only shows in the user transcript and
does NOT reach the model — so this uses additionalContext, not stderr.

Fails OPEN on any unexpected input (parse error, wrong tool, missing path) so
it can never brick an edit.
"""
import sys
import json

# Editing any of these signals a POSSIBLE dual-lane workflow-rule change.
# Compared case-insensitively against the forward-slash-normalized file path
# (endswith), so both absolute and repo-relative paths match.
RULE_SURFACES = (
    "docs/dual-lane-collaboration-protocol.md",
    ".claude/skills/dual-lane/skill.md",
)

REMINDER = (
    "DUAL-LANE RULE-SYNC REMINDER: you edited a dual-lane rule surface ({f}).\n"
    "A workflow-rule change is MULTI-SURFACE -- it is NOT done until it is propagated to "
    "EVERY applicable surface. Canonical source = the protocol doc; keep the rest as thin "
    "pointers to minimize drift:\n"
    "  1. docs/dual-lane-collaboration-protocol.md  (canonical contract; both lanes read it)\n"
    "  2. .claude/skills/dual-lane/SKILL.md  (Hard rules -- what a fresh session actually runs)\n"
    "  3. AGENTS.md  (if the rule is agent policy Codex reads every session)\n"
    "  4. .claude-state/project-memory/<slug>.md + its README pointer  (for recall)\n"
    "  5. a claude.md ledger SEQ announcing it (+ Codex two-key agreement if it is a shared/coordination rule)\n"
    "If you have ALREADY synced all applicable surfaces this session, disregard this."
)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    if data.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    path = tool_input.get("file_path") or ""
    if not isinstance(path, str) or not path:
        return 0
    norm = path.replace("\\", "/").lower()
    if not any(norm.endswith(surface) for surface in RULE_SURFACES):
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": REMINDER.format(f=path),
        }
    }
    try:
        sys.stdout.write(json.dumps(out))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
