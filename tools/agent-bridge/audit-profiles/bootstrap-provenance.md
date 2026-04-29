# Audit Profile - Bootstrap Provenance

**Trigger:** any commit modifying `BRIDGE_BOOTSTRAP_PROVENANCE_SPEC.md` implementation surface
**Reference spec:** `BRIDGE_BOOTSTRAP_PROVENANCE_SPEC.md` BP1-BP11
**First commit audited:** `6c2a1648` (PASS-with-followup `1eeb49c0`)

---

## Files this profile covers

- `tools/agent-bridge/bootstrap_session.py` (detect_bootstrap_origin + refusal path)
- `tools/agent-bridge/agent_bridge.py` (activate_session signature)
- `tools/agent-bridge/core/runtime.py` (peer breadcrumb v2 schema + normalize)
- `tools/agent-bridge/watcher.py` (provenance enforcement at fire time)
- `tools/agent-bridge/session.json` schema (trusted_parent tracking)
- `tools/agent-bridge/BRIDGE_PROTOCOL.md` (exit code 11 + audit events)
- Tests in `test_phase0_contract.py`, `test_agent_bridge.py`

---

## BP-by-BP audit checklist

### BP1 - peer breadcrumb schema v1 → v2

- [ ] `core/runtime.py` writes new fields: `bootstrap_origin`, `bootstrap_pid`, `bootstrap_parent_pid`, `bootstrap_thread_id`, `bootstrap_parent_thread_id`, `trusted_parent_session_id`, `subagent_signals`
- [ ] `normalize_peer_runtime_breadcrumb()` defaults `bootstrap_origin = "unknown"` for v1 reads
- [ ] `schema_version: 2` emitted on writes
- [ ] Round-trip tests assert v1→v2 read with default + v2 write+read

### BP2 - detect_bootstrap_origin()

- [ ] Function signature: `detect_bootstrap_origin(*, agent, env=None, process_depth=None) -> Tuple[str, dict]`
- [ ] Detection order: env marker → parent_thread_id_mismatch → process_depth heuristic → unknown fallback
- [ ] `SUBAGENT_ENV_MARKERS` includes both codex (`CODEX_SUBAGENT`, `CODEX_SUBAGENT_ID`) and claude (`CLAUDE_SUBAGENT`, `CLAUDE_AGENT_DEPTH`) variants — symmetric
- [ ] `MAX_NORMAL_BOOTSTRAP_DEPTH = 3` constant
- [ ] Returns tuple `(origin, signals_dict)` with all 4 signal keys (env_marker, process_depth, parent_thread_id_mismatch, mcp_tag) populated
- [ ] Tests cover each signal path independently

### BP3 - subagent refusal in bootstrap

- [ ] `bootstrap()` checks origin BEFORE any state mutation
- [ ] Subagent origin returns refused payload with `exit_code: 3` and `refused: True`
- [ ] `bootstrap_subagent_refused` audit event emitted with signals payload
- [ ] CLI wrapper translates refused payload to OS exit 3
- [ ] Test asserts no session.json mutation when subagent detected

### BP4 - unknown origin allowed with conservative restrictions

- [ ] `activate_session(allow_supersede=False)` for unknown
- [ ] Unknown does NOT supersede an active parent-origin session (registers as secondary)
- [ ] Unknown is not eligible for `trusted_parent` promotion
- [ ] Tests: unknown-bootstrap with active parent → both sessions present, parent stays active

### BP5 - last_trusted_parent_session tracking

- [ ] `session.json` schema has `agents.<agent>.last_trusted_parent_session` and `trusted_parent_promoted_at`
- [ ] Per-session record has `bootstrap_origin` and `bootstrap_promoted_to_trusted`
- [ ] Parent-origin sessions promoted on activation (or at 5-min stability gate per spec)
- [ ] `session_status` MCP tool surfaces trusted_parent data

### BP6 - SH2 auto-rollback (DEFERRED in 6c2a1648)

To be audited when shipped:

- [ ] Detection: bridge-d periodic check reads peer breadcrumb; if origin=subagent AND no rollback in flight: trigger
- [ ] Restoration: rewrite session.json `active_session = last_trusted_parent_session`; rewrite watcher-config; rewrite peer breadcrumb from parent's last-known
- [ ] Audit: `bootstrap_subagent_auto_rollback_succeeded` event with both old and new session IDs
- [ ] Test: orchestrate subagent slip-through → assert rollback restores parent

### BP7 - SH2 freeze when no rollback target (DEFERRED in 6c2a1648)

- [ ] Freeze: `pause.json` written with `scope: "frozen_after_subagent_bootstrap"`
- [ ] `BRIDGE_FROZEN` message sent to peer
- [ ] State marked `needs_rebootstrap`
- [ ] User notification via terminal toast
- [ ] `bootstrap_subagent_auto_rollback_failed` audit
- [ ] Test: subagent slip-through with no valid parent → freeze + alert

### BP8 - watcher exit 11 on subagent breadcrumb

- [ ] `_resolve_command_template` returns `returncode: 11, retryable: False` when peer breadcrumb origin is subagent
- [ ] `WAKE_PERMANENT_EXIT_CODES` includes 11
- [ ] `wake_skipped_bad_provenance` audit emitted
- [ ] Test: msg-subagent-peer scenario → rc=11 + no retry

### BP9 - watcher unknown-origin handling

- [ ] Wake proceeds for unknown origin (does NOT exit 11)
- [ ] `unknown_origin_warning` emitted ONCE per session_key
- [ ] Subsequent fires from same session don't re-warn
- [ ] Tracked in `watcher_state.unknown_origin_warnings` list

### BP10 - SH4 reclassification (DEFERRED)

- [ ] `unknown → parent`: subsequent env var observation triggers in-place breadcrumb update
- [ ] `unknown → subagent`: subagent signals appear post-bootstrap → SH2 auto-rollback path
- [ ] Future MCP tool `confirm_parent_session(session_id)` for manual reclassification

### BP11 - symmetric Claude implementation (PARTIAL in 6c2a1648)

- [ ] Claude detection markers: `CLAUDE_SUBAGENT`, `CLAUDE_AGENT_DEPTH` env var pattern
- [ ] Claude bootstrap path same logic as codex
- [ ] Claude watcher (when `wake_claude.ps1` ships) honors same exit code 11

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_phase0_contract test_server_wrapper_phase2
```

Expected at full BP1-BP11: substantially more than 93 tests (current count). Each new BP adds 2-5 tests.

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Bootstrap provenance <subscope>
ACTION_REQUESTED: none | implement-deferred-followups
NONCE: audit-bootstrap-provenance-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage vs BP1-BP11:

| BP# | Status |
|---|---|
| BP1 | DONE / PARTIAL / DEFERRED / MISSING |
| ...up through BP11 |

Code observations: [...]

Test verification:
- py -3 -m unittest ... = <N> tests

Verdict: PASS-with-followup if some BPs deferred per scope notes; PASS only when all BP1-BP11 satisfied.

[[handoff:codex]]
```

---

## Coordination

Each commit on this work gets its own AUDIT_RESULT keyed to the SHA. Deferred items roll forward; the FINAL audit (all BPs DONE) closes the spec.

Per `BRIDGE_BOOTSTRAP_PROVENANCE_SPEC` migration plan, this work spans 6+ commits (BP.1 spec → BP.2 schema → BP.3 detection → BP.4 enforcement → BP.5 protocol bump → BP.6 symmetric). Audit one slice at a time.
