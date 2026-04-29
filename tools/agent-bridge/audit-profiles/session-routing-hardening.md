# Audit Profile - Session Routing Hardening

**Trigger:** commit implementing any of SR1-SR7 in `BRIDGE_SESSION_ROUTING_HARDENING_SPEC.md`
**Reference spec:** `BRIDGE_SESSION_ROUTING_HARDENING_SPEC.md` SR1-SR7

---

## Files this profile covers

- `tools/agent-bridge/agent_bridge.py` (MCP tool args, send_to_peer validation, truedup_session_routing)
- `tools/agent-bridge/server.py` (write-path validation hooks)
- `tools/agent-bridge/bootstrap_session.py` (SR7 rotation safety)
- `tools/agent-bridge/core/inbox.py` (or wherever row write/read happens; schema migration)
- `tools/agent-bridge/test_session_routing_hardening.py` (new test file)
- `tools/agent-bridge/BRIDGE_PROTOCOL.md` (SR6 docs)

---

## SR-by-SR audit checklist

### SR1 - Row-level session fields

- [ ] inbox-claude.jsonl and inbox-codex.jsonl row schema bumped from v1 to v2
- [ ] New required fields: `from_session_id`, `to_session_id`, `from_session_id_kind`
- [ ] All current writers populate the new fields
- [ ] v1 reader test: ignores v2-only fields, reads cleanly
- [ ] v2 reader test: tolerates missing fields on v1 rows (defaults null)
- [ ] compact-on-touch test: v1 row rewritten via mark_read promotes to v2
- [ ] Per-bump documentation requirement: this spec section is the rationale doc (SE10)

### SR2 - Server validates session ownership

- [ ] `send_to_peer` extracts `to_agent` arg
- [ ] Validation: explicit `session_id` not `null`/`mlv-app`/owned-by-`to_agent` is refused
- [ ] Error structure: `error_kind="session_routing_mismatch"`, includes received vs expected agent
- [ ] Error message guides caller to fix (omit `session_id` for default-resolve)
- [ ] Validation runs on ALL internal write paths, not just `send_to_peer`
- [ ] Tests:
  - `test_sr_send_to_peer_refuses_wrong_agent_session_id`
  - `test_sr_send_to_peer_accepts_correct_session_id`
  - `test_sr_send_to_peer_accepts_recent_superseded_session_id`

### SR3 - Default-resolve when null

- [ ] `session_id=None` resolves to receiver's active session per `session.json`
- [ ] No active session: falls back to `mlv-app` rendezvous
- [ ] `routing_fallback_to_rendezvous` audit event emitted on fallback
- [ ] Both fail: refuses with `error_kind="no_active_receiver"`
- [ ] Tests:
  - `test_sr_send_to_peer_accepts_null_resolves_to_active`
  - `test_sr_send_to_peer_falls_back_to_rendezvous_when_no_active`
  - `test_sr_send_to_peer_refuses_when_no_active_and_no_fallback`

### SR4 - truedup_session_routing tool

- [ ] MCP tool registered with signature: `agent`, `dry_run=True`, `mode="rekey"|"quarantine"`
- [ ] Scans inbox-`<agent>`.jsonl for orphan rows (session_id matches no active or recent-superseded session)
- [ ] `dry_run=True`: returns list, does NOT modify
- [ ] `mode="rekey"`: rewrites session_id to active session GUID
- [ ] `mode="quarantine"`: moves rows to `<inbox>.orphan.jsonl`
- [ ] Audit events: `session_truedup_rekeyed` per row (rekey mode), `session_truedup_quarantined` per row (quarantine mode)
- [ ] Idempotent after apply (second run returns empty)
- [ ] Tests:
  - `test_truedup_dry_run_lists_orphans`
  - `test_truedup_rekey_mode_rewrites_session_id`
  - `test_truedup_quarantine_mode_moves_to_orphan_file`
  - `test_truedup_idempotent_after_apply`

### SR5 - Param rename (optional, deprecation cycle)

- [ ] New `target_session_id` arg accepted on `send_to_peer`
- [ ] Old `session_id` accepted as deprecated alias
- [ ] `deprecated_param_used` audit event emitted on old name
- [ ] Conflicting both-set raises `error_kind="conflicting_session_id_args"`
- [ ] Documentation updated to point at new name
- [ ] Tests:
  - `test_target_session_id_alias_works`
  - `test_session_id_param_warns_deprecated`
  - `test_conflicting_session_id_and_target_session_id_errors`

### SR6 - Body conventions advisory

- [ ] `BRIDGE_PROTOCOL.md` updated: `FROM_SESSION:`/`TO_SESSION:` body lines marked optional/informational
- [ ] Existing readers tolerate missing body lines (no parse error)
- [ ] grep verifies no writer requires the body lines

### SR7 - Bootstrap rotation safety

- [ ] `bootstrap_session.py` scans inbox-`<agent>`.jsonl for unread in old session bucket on rotation
- [ ] Default behavior: re-keys to new active session
- [ ] Configurable: leave-with-marker option also supported
- [ ] `bootstrap_rotation_routed_messages` audit event emitted with count
- [ ] Tests:
  - `test_bootstrap_rotation_rekeys_old_bucket_unread`

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_session_routing_hardening test_phase0_contract
```

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
NONCE: audit-session-routing-<sha-short>
TIMESTAMP: <iso-utc>
SCOPE: project-only
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Session routing hardening <subscope>
ACTION_REQUESTED: none | implement-deferred-followups

Reviewed <sha>. Coverage vs SR1-SR7:

| SR# | Status |
|---|---|
| SR1 | DONE / PARTIAL / DEFERRED / MISSING |
| SR2 | ... |
| SR3 | ... |
| SR4 | ... |
| SR5 | ... |
| SR6 | ... |
| SR7 | ... |

Phase status:
- Phase 1 (SR2 + SR3): <status>
- Phase 2 (SR1): <status>
- Phase 3 (SR4): <status>
- Phase 4 (SR5): <status>
- Phase 5 (SR6 + SR7): <status>

Misroute regression test (test_int_sr_misroute_caught_before_disk_write):
<pass/fail/missing>

Tests at HEAD: <N>

[[handoff:codex]]
```

---

## Coordination

SR-Phase 1 (SR2 + SR3) is the priority - smallest high-leverage fix that closes the 2026-04-29 misroute footgun outright. Audit Phase 1 before Phase 2 ships; Phase 2's schema bump is reversible-on-rollback if it lands clean.

SR4 truedup tool is the **regression-recovery** path. After SR4 ships, run dry-run on current state to find orphans (e.g. b3fccc3d from 2026-04-29 13:41 incident) and clean them up.

**Critical pushback if seen:** any commit that ships SR1 without bumping `schema_version` is a fail. Per BRIDGE_SCHEMA_EVOLUTION_SPEC SE1, every persistent schema change requires a version field bump and corresponding spec doc section.
