# Agent Bridge - Bootstrap Provenance Spec

**Status:** Codex-side implementation shipped for BP2-BP10; BP11 symmetric
Claude implementation remains parked.
**Authors:** Codex (original framing 2026-04-29 in response to user "configuration by convention" direction); Claude (codification + scope refinements)
**Convention:** *Bootstrap is parent-only by default; sub-agent bootstrap attempts are rejected, and any accidental success is automatically rolled back to the last trusted parent session.*
**Motivation:** the Confucius incident (2026-04-29) revealed that bootstrap can capture a sub-agent's CODEX_THREAD_ID and bind the bridge to a sub-agent chat instead of the user's intended bridge chat. Treating sub-agent bootstrap as a first-class fault — with prevention, detection, and self-heal rules — closes this class of failure without requiring user designation of the bridge chat.

---

## Implemented Status

Shipped Codex-side behavior includes bootstrap-origin detection, conservative
unknown-origin handling, subagent refusal/retargeting, trusted-parent tracking,
unknown-to-parent reclassification coverage, watcher refusal for bad Codex
provenance, trusted-parent drift refusal, and rollback/freeze paths. The
remaining unsatisfied acceptance criterion is BP11: symmetric Claude-side
provenance handling, which is tracked separately because Claude desktop exposes
a different runtime surface.

---

## Problem

Today's `bootstrap_session.py`:
1. Reads `CODEX_THREAD_ID` env var (or `PARENT_THREAD_ID_KEY` config)
2. Captures whatever value is present at bootstrap time
3. Writes it into `peer-codex.runtime.json` and indirectly into `watcher-config.json`
4. Trusts the captured value for the lifetime of the session

If bootstrap is run from a sub-agent context (a Codex chat that is itself running an Agent-tool task or sub-task), the captured ThreadId belongs to the sub-agent chat, not the parent. Subsequent wake nudges land in the sub-agent chat. The bridge is silently bound to a transient context.

The 2026-04-29 incident: user's "Confucius" Codex chat (a sub-agent task implementing wrapper Phase 2) received `check bridge inbox` SendKeys and processed it correctly — but from the wrong context. The bridge ack'd messages from a chat that was supposed to be doing implementation work, not bridge ownership.

## Convention

**Bootstrap is parent-only.** A bridge session is owned by the user's primary Codex/Claude chat (the "parent thread"), not by any sub-agent or transient task chat spawned from it.

This convention is enforced via the three-state provenance model below.

---

## Three-State Provenance Model

Every bridge session has a `bootstrap_origin` value resolved at bootstrap time:

| State | Meaning | Bridge behavior |
|---|---|---|
| `parent` | Confident: bootstrap ran from a primary chat with no sub-agent signals | Trusted; can supersede; eligible for `trusted_parent` promotion after 5-min stability |
| `subagent` | Confident: bootstrap signals indicate sub-agent context (env var, MCP tag, etc.) | **Hard reject** at bootstrap; OR if already mutated, auto-rollback to `last_trusted_parent_session`; freeze if no rollback target |
| `unknown` | Cannot tell: no positive sub-agent signal AND no first-class parent confirmation | **Warning + conservative restrictions**: allowed to bootstrap, but cannot supersede a `parent`-origin session, cannot be promoted to `trusted_parent`, flagged in audit and `bridge_process_status` |

**The `unknown` distinction is critical.** Until Codex Desktop / Claude Code expose first-class subagent markers, "unknown" must not auto-poison. Many legitimate bootstraps will be `unknown` early on; rejecting them all would break normal use.

### `unknown` state semantics in detail

- **Allowed to bootstrap:** writes session.json, watcher-config, peer breadcrumb. Does NOT freeze the bridge.
- **Cannot supersede `parent`:** if there is a current `parent`-origin session, the `unknown` bootstrap registers as additional, not replacement. The `parent` stays the active peer. The `unknown` session is "secondary" — receives messages but doesn't become the canonical wake target.
- **Cannot be promoted to `trusted_parent`:** the 5-minute stability rule that promotes a session to `last_trusted_parent_session` requires `bootstrap_origin == "parent"`. Unknown sessions stay `unknown` until reclassified.
- **Reclassification path:** a session originally tagged `unknown` can be promoted to `parent` if subsequent signals corroborate (e.g. a known-parent env var is observed in the same process, manual user confirmation via a future MCP tool). Demotion `unknown` → `subagent` also supported if subagent signals show up post-bootstrap.
- **Audit and surfacing:** bridge_process_status reports `unknown_origin_session_count` per agent. After three `unknown` bootstraps without any `parent` confirmation, surface to user as a configuration concern.

---

## Detection Signals

Detection of `subagent` origin requires multiple signals; AND-ed for confidence. Order from most-reliable to heuristic:

### Signal A (most reliable, requires Codex Desktop cooperation)

**Codex Desktop sets a marker env var** when launching a sub-agent process. Examples:
- `CODEX_SUBAGENT=1`
- `CODEX_SUBAGENT_ID=<uuid>`
- `CODEX_PARENT_AGENT_ID=<uuid>`

If the bootstrap process sees any of these set, `bootstrap_origin = "subagent"` immediately. Symmetric on Claude side: `CLAUDE_SUBAGENT=1` or `CLAUDE_AGENT_DEPTH > 0`.

**This requires a feature request to Codex Desktop / Claude Code harness teams** to expose these vars. Track separately. Until shipped, this signal is unavailable on this host.

### Signal B (moderately reliable, no cooperation required)

**Process tree depth heuristic.** Walk the parent-pid chain from `bootstrap_session.py`. If `parent_pid` chain depth from `claude.exe` / `codex.exe` is greater than expected for a normal MCP-server-spawning-bootstrap path, suspect sub-agent.

Concrete rule:
- Normal bootstrap: claude.exe / codex.exe → MCP server subprocess → bootstrap (depth 2)
- Sub-agent bootstrap: claude.exe → MCP server → Agent tool harness → bootstrap (depth 3+)

If depth > `MAX_NORMAL_BOOTSTRAP_DEPTH = 3`, flag as suspect. Combined with no `parent` corroboration, escalate to `subagent`. By itself, this gives `unknown`.

### Signal C (env var pattern)

**`CODEX_PARENT_THREAD_ID` set AND differs from `CODEX_THREAD_ID`.** When Codex Desktop's sub-agent harness sets `CODEX_PARENT_THREAD_ID` to record the parent's thread, this is a structural indicator that the current bootstrap is in a non-parent context.

If both env vars are set AND differ, `bootstrap_origin = "subagent"` (high confidence). This signal is reliable IF Codex Desktop sets these vars consistently.

### Signal D (MCP context tag)

**MCP server connection metadata.** Future: if the bootstrap call originated from an MCP server subprocess that was tagged with sub-agent context, refuse. Requires Codex Desktop to expose a "connection tag" field in MCP setup. Not implementable today.

### Combined detection logic

```python
def detect_bootstrap_origin(env, pid_chain) -> Tuple[str, dict]:
    signals = {}

    # Signal A
    subagent_env_markers = ["CODEX_SUBAGENT", "CODEX_SUBAGENT_ID",
                             "CLAUDE_SUBAGENT", "CLAUDE_AGENT_DEPTH"]
    for marker in subagent_env_markers:
        if env.get(marker) and env[marker] != "0":
            signals["env_marker"] = marker
            return "subagent", signals

    # Signal C
    parent_tid = env.get("CODEX_PARENT_THREAD_ID") or env.get("CLAUDE_PARENT_THREAD_ID")
    self_tid = env.get("CODEX_THREAD_ID") or env.get("CLAUDE_THREAD_ID")
    if parent_tid and self_tid and parent_tid != self_tid:
        signals["parent_thread_id_mismatch"] = True
        return "subagent", signals

    # Signal B
    depth = compute_pid_chain_depth(pid_chain)
    signals["process_depth"] = depth
    if depth > MAX_NORMAL_BOOTSTRAP_DEPTH:
        # Heuristic alone -> unknown, not subagent (could be a legitimate weird config)
        return "unknown", signals

    # No subagent signals but also no first-class "parent" signal
    # -> conservatively "unknown" until reclassified or manually confirmed
    if "CODEX_THREAD_ID" not in env and "CLAUDE_THREAD_ID" not in env:
        return "unknown", signals

    # We have a thread id, no subagent signals, normal depth -> "parent"
    return "parent", signals
```

`MAX_NORMAL_BOOTSTRAP_DEPTH` is a config constant (default 3); tunable per platform.

---

## Schema additions

### `peer-<agent>.runtime.json` (schema_version 1 → 2)

```json
{
  "schema_version": 2,
  "agent": "codex",
  "session_id": "...",
  "desktop_thread_id": "...",
  "tenant_id": "local-default",
  "originator_machine_id": "...",
  "bootstrap_origin": "parent | subagent | unknown",
  "bootstrap_pid": 14328,
  "bootstrap_parent_pid": 9876,
  "bootstrap_thread_id": "<this session's id>",
  "bootstrap_parent_thread_id": "<parent thread id if known>",
  "trusted_parent_session_id": "<set only if this IS a trusted parent>",
  "subagent_signals": {
    "env_marker": null,
    "process_depth": 2,
    "parent_thread_id_mismatch": false,
    "mcp_tag": null
  },
  "written_by_pid": 14328,
  "written_at": "2026-04-29T...",
  "bootstrap_command": ["py", "-3", "bootstrap_session.py", "..."],
  "bridge_root": "...",
  "manifest_root_id": "..."
}
```

`bootstrap_origin` is REQUIRED in v2. Readers tolerate v1 rows by defaulting to `unknown` (conservative).

### `session.json` (schema_version 1 → 2)

Per-agent additions:

```json
{
  "schema_version": 2,
  "agents": {
    "codex": {
      "active_session": "...",
      "last_trusted_parent_session": "...",
      "trusted_parent_promoted_at": "2026-04-29T...",
      "sessions": {
        "<session_id>": {
          "bootstrap_origin": "parent | subagent | unknown",
          "bootstrap_promoted_to_trusted": false,
          "started_at": "...",
          "last_heartbeat_at": "..."
        }
      }
    }
  }
}
```

A session is promoted to `last_trusted_parent_session` when:
- `bootstrap_origin == "parent"` (not `unknown`, not `subagent`)
- Has been `active` for >= `TRUSTED_PARENT_STABILITY_SECONDS` (default 300s = 5 min)
- Has not been superseded by a non-`parent`-origin session

### `BRIDGE_PROTOCOL.md` v1.6 → v1.7

#### New exit code

| Code | Meaning | Watcher behavior | Mark seen | Retry |
|---|---|---|---|---|
| **11** | Peer breadcrumb has bad provenance (`bootstrap_origin = subagent`, OR `unknown` without rollback target available) | Mark seen; audit `wake_skipped_bad_provenance` with sub-reason | Yes | No (require re-bootstrap or rollback) |

#### New audit events

| Action | Fired by | When | Payload |
|---|---|---|---|
| `bootstrap_origin_resolved` | bootstrap_session | Always, at end of detection | `origin`, `signals`, `pid` |
| `bootstrap_subagent_refused` | bootstrap_session | When subagent detected before mutation | `signals`, `pid` |
| `bootstrap_subagent_auto_rollback_succeeded` | bootstrap_session | When rollback restored a parent | `rolled_back_to_session`, `original_subagent_session` |
| `bootstrap_subagent_auto_rollback_failed` | bootstrap_session | When no rollback target was available | `original_subagent_session`, `frozen` |
| `session_promoted_to_trusted_parent` | bridge-d | Session passed 5-min stability | `session_id`, `promoted_at` |
| `wake_skipped_bad_provenance` | watcher | At fire time when peer breadcrumb has subagent/unknown origin | `breadcrumb_origin`, `message_id` |
| `unknown_origin_warning` | bridge-d | First time an `unknown` session is encountered | `session_id`, `signals` |

#### New message types

- `ROUTE_REPAIR` (existing in agent-level allowlist; formalize use here): emitted on auto-rollback to inform peer that the canonical session id changed. Body includes `previous_session_id`, `restored_session_id`, `rollback_reason`.
- `BRIDGE_FROZEN` (NEW): emitted to peer when rollback is not possible and bridge sends are frozen until manual rebootstrap. Body: `frozen_agent`, `frozen_at`, `freeze_reason`, `manual_recovery_action`.

---

## Self-Heal Rules

### Rule SH1 — Bootstrap subagent detection

Before mutating any state (session.json, watcher-config.json, peer breadcrumb), `bootstrap_session.py`:

1. Calls `detect_bootstrap_origin(env, pid_chain)` → returns `(origin, signals)`
2. If `origin == "subagent"`:
   - Audit `bootstrap_subagent_refused`
   - Refuse to mutate
   - Print clear stderr message to user/agent: "subagent bootstrap refused; only the parent thread should run bootstrap_session.py"
   - Exit code 3 (config_error class)
3. If `origin == "parent"`:
   - Proceed normally
4. If `origin == "unknown"`:
   - Proceed with conservative restrictions (no supersede of `parent`, no trusted_parent eligibility)
   - Audit `unknown_origin_warning`
   - Surface in bridge_process_status

### Rule SH2 — Auto-rollback if subagent already mutated

If somehow a `subagent`-origin bootstrap succeeded (signal added post-mutation, or refusal logic was bypassed), bridge-d on next poll:

1. Reads peer breadcrumb; sees `bootstrap_origin == "subagent"`
2. Reads session.json; finds `last_trusted_parent_session`
3. If `last_trusted_parent_session` is still valid (process alive per `os_process` presence layer; not older than `ROLLBACK_MAX_AGE_HOURS`, default 24):
   - Reactivate parent session as `active`
   - Rewrite watcher-config to point at parent
   - Rewrite peer breadcrumb from parent's last-known-good values
   - Send `ROUTE_REPAIR` to peer + `SESSION_UPDATE` to local audit
   - Audit `bootstrap_subagent_auto_rollback_succeeded`
4. Otherwise (no valid rollback target):
   - Freeze codex-side bridge sends (`pause.json` with scope `frozen_after_subagent_bootstrap`)
   - Mark state `needs_rebootstrap`
   - Send `BRIDGE_FROZEN` to peer
   - Audit `bootstrap_subagent_auto_rollback_failed`
   - Surface to user via terminal notification

### Rule SH3 — Watcher-side refusal

The watcher reads `peer-<agent>.runtime.json` at fire time. If `bootstrap_origin` is:
- `parent`: proceed normally
- `subagent`: skip wake; emit exit code 11; audit `wake_skipped_bad_provenance` with `sub_reason: subagent`. Mark message seen, no retry.
- `unknown`: proceed with caution. Wake fires (don't auto-poison), but audit `wake_skipped_bad_provenance` is NOT emitted; instead emit `unknown_origin_warning` once per session.

### Rule SH4 — Reclassification

A session originally tagged `unknown` can be reclassified:
- `unknown → parent` if subsequent corroborating signals appear (env var observed, manual user confirmation via future `confirm_parent_session` MCP tool). Updates `peer-<agent>.runtime.json` schema_version-2 in place.
- `unknown → subagent` if subagent signals appear post-bootstrap. Triggers SH2 auto-rollback.

---

## Symmetric Rule for Claude Code Sub-agents

Claude Code's Agent tool launches sub-agents in their own context. Same rule applies:
- `bootstrap_session.py --agent claude` refuses if running inside a sub-agent context
- Detection: Claude Code's harness sets specific env vars when running an Agent task. Likely candidates: `CLAUDE_AGENT_DEPTH > 0`, `CLAUDE_SUBAGENT_ID` set, `CLAUDE_AGENT_TYPE` non-null
- Auto-rollback applies symmetrically to claude-side `last_trusted_parent_session`

Specifying exact env var names requires confirmation from the Claude Code harness team. Track separately. Until specified, Claude side uses pid-chain depth heuristic (Signal B) only, which gives `unknown` rather than `subagent`.

---

## Open Questions and Codex Desktop Coordination

These items require external cooperation; tracked here so they don't block the Claude-side spec adoption.

### OQ1 — Codex Desktop subagent env var

Need Codex Desktop to set `CODEX_SUBAGENT=1` (or equivalent) when launching sub-agent processes. File feature request to Codex Desktop team. Until shipped, only Signals B and C are usable; many bootstraps will land as `unknown` rather than `subagent`.

### OQ2 — Claude Code subagent env var

Same need on Claude side. File feature request to Claude Code harness team for `CLAUDE_AGENT_DEPTH` env var on Agent-tool sub-agent processes.

### OQ3 — `confirm_parent_session` MCP tool

A future MCP tool that lets a user explicitly confirm "this session IS a parent" — promotes `unknown` → `parent` manually. Defer to follow-up spec.

### OQ4 — `bridge_active_work` MCP tool

Per `INBOX_WORKFLOW_RULES` (memory note): tool that returns list of in-flight `WORKING_ON_IT` messages. Useful for "what is everyone doing right now?" queries. In scope for follow-up Tier-2 work.

---

## Acceptance Criteria

- BP1. `peer-<agent>.runtime.json` schema_version 2 with `bootstrap_origin` field; tests assert v1→v2 round-trip and default-to-unknown for v1 reads.
- BP2. `detect_bootstrap_origin()` implemented per the combined detection logic; tests assert each signal path.
- BP3. `bootstrap_session.py` refuses subagent origin (exit 3, audit `bootstrap_subagent_refused`); tests assert.
- BP4. `bootstrap_session.py` allows unknown origin with conservative restrictions (no supersede of parent, no trusted_parent promotion); tests assert.
- BP5. `last_trusted_parent_session` tracked in `session.json` schema v2; promoted after 5-min `parent`-origin stability; tests assert with mocked clock.
- BP6. SH2 auto-rollback path: detected subagent-mutation → rollback to last trusted parent if valid; tests assert.
- BP7. SH2 freeze path: no valid rollback target → `BRIDGE_FROZEN` + pause.json; tests assert.
- BP8. SH3 watcher refusal: subagent breadcrumb → exit 11; tests assert.
- BP9. SH3 watcher unknown handling: unknown breadcrumb → wake fires, `unknown_origin_warning` emitted once; tests assert.
- BP10. SH4 reclassification: unknown → parent on env var observation; tests assert.
- BP11. Symmetric Claude implementation; tests parallel to BP1-BP10.

---

## Tests Required

**Unit (per detection signal):**

1. `test_detect_origin_subagent_via_env_marker`
2. `test_detect_origin_subagent_via_thread_id_mismatch`
3. `test_detect_origin_unknown_via_high_process_depth`
4. `test_detect_origin_unknown_via_missing_thread_id`
5. `test_detect_origin_parent_normal_bootstrap`

**Bootstrap path:**

6. `test_bootstrap_refuses_subagent_with_exit_3`
7. `test_bootstrap_allows_unknown_with_conservative_flag`
8. `test_bootstrap_allows_parent_normally`
9. `test_unknown_session_does_not_supersede_parent`
10. `test_unknown_session_not_promoted_to_trusted_parent`

**Promotion / rollback:**

11. `test_session_promoted_to_trusted_parent_after_5_min_stability`
12. `test_auto_rollback_restores_last_trusted_parent`
13. `test_auto_rollback_freezes_when_no_target`
14. `test_unknown_to_parent_reclassification_on_env_observation`

**Watcher:**

15. `test_watcher_emits_exit_11_on_subagent_breadcrumb`
16. `test_watcher_proceeds_on_unknown_breadcrumb_with_warning`
17. `test_watcher_proceeds_normally_on_parent_breadcrumb`

**Integration:**

18. End-to-end: parent bootstraps → sub-agent attempts bootstrap → refused → parent remains active
19. End-to-end: parent bootstraps → sub-agent slips through (test fixture) → bridge-d auto-rollback → ROUTE_REPAIR sent

---

## Migration Path

### Phase BP.1 — Spec adoption

This document. No code change; codifies the convention.

### Phase BP.2 — Schema bumps

Bump `peer-<agent>.runtime.json` and `session.json` to v2 per `BRIDGE_SCHEMA_EVOLUTION_SPEC.md` policy. Readers handle v1 by defaulting `bootstrap_origin` to `unknown`.

### Phase BP.3 — Bootstrap detection logic

Implement `detect_bootstrap_origin()` and integrate into `bootstrap_session.py`. Refusal path for subagent. Conservative restrictions for unknown. Tests BP1-BP10 except watcher-side.

### Phase BP.4 — Watcher refusal + auto-rollback

Implement SH3 watcher refusal and SH2 auto-rollback. New exit code 11. New audit events. Tests BP6-BP9.

### Phase BP.5 — `BRIDGE_PROTOCOL.md` bump

v1.6 → v1.7 with exit code 11, new audit events, new message types (`ROUTE_REPAIR` formalization, `BRIDGE_FROZEN`).

### Phase BP.6 — Symmetric Claude implementation

Mirror on claude side with appropriate env var detection. Tests BP11.

### Phase BP.7 — Codex Desktop / Claude Code feature requests

File feature requests for first-class subagent markers per OQ1, OQ2.

---

## Coordination Model

Codex implements; Claude reviews. This spec is Claude-authored per `dfe07498` agreement and `077f9138` WORKING_ON_IT. Codex's `ca29220a` PASS-with-scope-notes locked the design including the parent/subagent/unknown distinction.

Audit profile: `tools/agent-bridge/audit-profiles/bootstrap-provenance.md` (to be added). Use the existing audit profile structure (required removals/preservation, test expectations, AUDIT_RESULT template).

[[handoff:codex]]
