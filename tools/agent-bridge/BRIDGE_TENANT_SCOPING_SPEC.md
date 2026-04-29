# Agent Bridge - Tenant Scoping Spec

**Status:** Proposed (forward-compat)
**Authors:** Claude (proposal); Codex implementation pending
**Tier:** Tier 2 — architecture-now-for-future-LAN/WAN
**Motivation:** prepare every persisted entity to carry `tenant_id` so multi-tenant cloud (`BRIDGE_AWS_MULTITENANT_DESIGN.md`, Tier 3) can plug in without touching every read/write call site. Single-machine v1 uses `tenant_id = "local-default"`; the field is present in schema from day 1.

---

## Goal

Make tenant scoping a first-class invariant of every bridge entity. Every JSONL row, JSON file, MCP tool argument, and audit event carries `tenant_id`. Local-only deployments use a constant `local-default`; multi-tenant cloud deployments derive `tenant_id` from the authenticated JWT claim.

---

## Scope of changes

The following persisted entities gain a required `tenant_id` field (schema_version bump on each):

| Entity | Path | Today | After spec |
|---|---|---|---|
| Inbox rows | `state/inbox-<agent>.jsonl` | no field | `tenant_id` required |
| Orphaned rows | `state/orphaned-<agent>.jsonl` | no field | `tenant_id` required |
| Audit events | `state/messages.jsonl` | no field | `tenant_id` required (or `tenant_id: "system"` for cross-tenant ops like compact) |
| Pending queues | `state/pending_*.jsonl` | (new file) | `tenant_id` required |
| Cross-project pair state | `state/cross-project-pairs/<link_id>.json` | (new file) | `tenant_id` required (if cross-tenant pairing is ever supported, both source and target tenant_ids stored) |
| Wake failure windows | `state/wake-failure-windows.json` | (new file) | nested by `tenant_id` |
| Session registry | `session.json` | no field | each session entry includes `tenant_id` |
| Peer breadcrumb | `peer-<agent>.runtime.json` | no field | `tenant_id` required |
| Presence record | `presence-<agent>.runtime.json` | (new) | `tenant_id` required (already in spec) |
| Pause flag | `pause.json` | (new) | `tenant_id` scope (future: pause per tenant) |
| Bridge-root manifest | `bridge-root.json` | already versioned | gains `default_tenant_id` field with default `"local-default"` |
| Watcher-config session entries | `watcher-config.json` | no field | each session entry gains `tenant_id` |
| Machine identity | `machine.json` | (new in STATE_LAYOUT) | bound to one tenant in single-tenant cloud; multi-tenant cloud allows multiple |

The following MCP tool arguments gain optional `tenant_id`:

- `send_to_peer`, `check_inbox`, `peek_inbox`, `wait_inbox`, `mark_read`, `mark_seen`, `mark_handled`, `message_status`, `clear_inbox`, `clear_bucket`, `bridge_status`, `pause_bridge`, `resume_bridge`, `bridge_process_status`, `evaluate_routing`, `list_pending_receipts`, `send_control_message`, `activate_session`, `end_session`, `reset_session`, `project_identity`, `session_status`

Default: server resolves from current authenticated identity (`local-default` for single-machine `LocalUserAuth`).

---

## `tenant_id` semantics

**Format:** opaque string, max 64 chars, charset `[a-zA-Z0-9_-]`. Examples:
- `local-default` (single-machine v1)
- `acme-corp` (paid product user/org)
- `cog-us-east-1_abc123` (Cognito user pool ID + sub fragment)
- `system` (reserved for cross-tenant operations like compaction)

**Reserved values:**
- `local-default` — single-machine OS-user-bound; never appears in cloud
- `system` — bridge-internal operations (compact, recovery, cross-tenant audit); never assignable to a user
- `unassigned` — transient state during signup/migration; bridge refuses inbound work writes for `unassigned`

**Comparison:** case-sensitive exact match. NOT case-insensitive (avoid security pitfalls of Unicode normalization mismatches).

**Defaulting (v1 single-machine):** if a write arrives without `tenant_id`, the bridge sets `tenant_id = "local-default"` automatically. Logs a `tenant_id_defaulted` audit event for diagnostic purposes.

**Defaulting (cloud):** if a write arrives without `tenant_id`, bridge REJECTS with `tenant_id_required` error. Cloud is strict — every operation must be authenticated and scoped.

---

## Isolation invariants

The bridge MUST enforce these properties:

1. **No cross-tenant inbox reads.** `check_inbox(agent="codex", tenant_id="A")` returns ONLY rows where `row.tenant_id == "A"`. Even if the file contains rows from other tenants (multi-tenant cloud where one bridge instance serves many users), the filter is applied at read time.

2. **No cross-tenant inbox writes.** `send_to_peer(from_agent="claude", to_agent="codex")` writes a row with `tenant_id` matching the sender's authenticated tenant. Senders cannot forge another tenant's `tenant_id`.

3. **No cross-tenant audit visibility.** `messages.jsonl` writes carry `tenant_id` from the operation's identity. Reads filter on tenant. The `system` tenant's events are visible to all (e.g. compaction events) but never contain message bodies.

4. **No cross-tenant session impersonation.** A session bootstrapped under tenant A cannot see tenant B's sessions in `session.json` even if both share a bridge root.

5. **Cross-project pairing is cross-tenant-disallowed by default.** `CROSS_PROJECT_PAIR_REQUEST` between two projects of DIFFERENT tenants requires explicit operator-level enable (not a normal user action). Single-tenant cross-project (project A and project B both within tenant X) is the default supported case.

---

## File-level scoping strategies

Two valid implementations:

### Strategy A: per-tenant subdirectories (cloud preferred)

```
<bridge-root>/
  state/
    tenants/
      <tenant_id>/
        inbox-claude.jsonl
        inbox-codex.jsonl
        messages.jsonl
        pending_*.jsonl
        cross-project-pairs/
        ...
```

- Pro: physical isolation; per-tenant backup/restore; per-tenant compaction; tenant-specific retention policies
- Pro: filesystem permissions can enforce tenant isolation (Unix mode 700 owned by tenant principal)
- Con: cross-tenant operations (e.g. system audit) need explicit traversal

### Strategy B: shared files with per-row `tenant_id` (single-machine v1 preferred)

```
<bridge-root>/
  state/
    inbox-claude.jsonl    ← rows from all tenants, filtered by tenant_id
    inbox-codex.jsonl     ← same
    messages.jsonl        ← same
```

- Pro: no migration when adding tenants; v1 backward-compat
- Pro: simpler file-handle management
- Con: single-file scaling limits in multi-tenant cloud; needs row-level filter on every read

**v1 uses Strategy B with `local-default` as the single tenant.** The on-disk layout is unchanged from today; rows just gain a `tenant_id` field.

**v3 cloud uses Strategy A** with per-tenant directories under `state/tenants/<tenant_id>/`. Migration from B to A is a per-row split done at first-cloud-bootstrap.

---

## Schema additions

### Inbox row (v2 schema with tenant_id)

```json
{
  "schema_version": 2,
  "id": "...",
  "from": "claude",
  "to": "codex",
  "session_id": "...",
  "parent_project": "mlv-app",
  "tenant_id": "local-default",
  "originator_machine_id": "...",
  "queued_at": "...",
  "body": "...",
  "control_type": null,
  "hop_count": 0,
  "seen_at": null,
  "read_at": null,
  "handled_at": null,
  "failure_reason": null
}
```

`tenant_id` and `originator_machine_id` are new in v2. Schema v1 readers tolerate v2 rows by ignoring unknown fields. Schema v2 readers tolerate v1 rows by defaulting `tenant_id = "local-default"`.

### Audit event (v2 schema with tenant_id)

```json
{
  "schema_version": 2,
  "action": "send_to_peer",
  "tenant_id": "local-default",
  "agent": "claude",
  "session_id": "...",
  "timestamp": "...",
  "originator_machine_id": "..."
}
```

### Session registry entry

```json
{
  "schema_version": 2,
  "agent": "codex",
  "session_id": "7ec4a663-...",
  "tenant_id": "local-default",
  "project": "mlv-app",
  "status": "active",
  "started_at": "...",
  "last_heartbeat_at": "..."
}
```

---

## Per-tenant resource scoping

When implementing Strategy A (cloud), several bridge-internal resources scope per-tenant:

| Resource | Per-tenant scope? | Notes |
|---|---|---|
| Wake retry windows | Yes | One tenant's broken Codex doesn't trip another tenant's circuit breaker |
| Compaction schedule | Yes | Per-tenant retention policies |
| Pause flag | Yes | One tenant pausing doesn't pause everyone |
| Backpressure (one-unread limit) | Yes | Per-tenant per-session limit |
| MCP server subprocess | No | One subprocess per MCP client; tenant identity carried in each request |
| Watcher | No | One watcher per machine, polls all tenants' inboxes |
| Bridge-d daemon | No | Same |

---

## Authentication interaction

Tenant scoping is enforced via the `BridgeAuth` abstraction (see `BRIDGE_AUTH_SPEC.md`). The auth provider supplies `current_identity()` which includes `tenant_id`; every MCP tool gates writes/reads on that identity.

**v1 LocalUserAuth:** `current_identity().tenant_id == "local-default"` always. No multi-tenant.

**v3 CognitoJWTAuth:** `current_identity().tenant_id` derived from JWT claim `custom:tenant_id`. Bridge refuses operations whose explicit `tenant_id` argument doesn't match the claim (no impersonation).

---

## Migration path

### v1 → v2 (single-machine baseline)

1. Schema bump: every persistent JSON file gets `schema_version` field. Readers accept rows without it as `schema_version: 1`.
2. New writes include `tenant_id: "local-default"` and `originator_machine_id`. Old rows continue to work unchanged.
3. `compact.py` adds rewrite-on-touch: when a row is rewritten for receipt update, schema is upgraded to v2.
4. No mandatory mass migration; in-place gradual upgrade.

### v2 → v2-cloud (single tenant in cloud)

1. User signs up via Cognito; receives `tenant_id` (e.g. `cog-us-east-1_abc123`).
2. `bridge-root.json` `default_tenant_id` field updated from `local-default` to user's cloud tenant.
3. Local-default rows are migrated by `migrate_root.py --upgrade-tenant` which rewrites every row's `tenant_id` from `local-default` to the user's cloud `tenant_id`. Audit log records the migration as `tenant_id_migrated`.
4. After migration, all writes carry the cloud tenant.

### v2-cloud → v2-multi-tenant (paid product, multiple users)

1. Cloud bridge instance accepts MCP connections from many users
2. Each user's MCP server subprocess runs with their JWT identity
3. Bridge filters reads/writes per `tenant_id` on every operation
4. No mass migration; tenancy is enforced at request time

---

## Acceptance criteria

- T1. Schema v2 supports `tenant_id` field on all persisted entities; tests assert round-trip with default `local-default`.
- T2. Writes without `tenant_id` are accepted in single-machine mode (defaulted to `local-default`); tests assert.
- T3. Cross-tenant reads return empty (filter applied); tests assert with mocked multi-tenant rows.
- T4. Cross-tenant writes are refused when sender's authenticated identity differs; tests assert.
- T5. `tenant_id_defaulted` audit event fires when a v1 row is read into v2 context; tests assert.
- T6. `system` tenant reserved value cannot be assigned to a user; tests assert.
- T7. Cross-project pairing across different tenants is refused by default; tests assert.
- T8. `compact.py` rewrites old rows to v2 schema on touch; tests assert.
- T9. Per-tenant pause flag isolates correctly; tests assert with two-tenant fixture.
- T10. Strategy A (per-tenant subdirectories) supported by `BridgeTransport.append_inbox`; tests assert when transport=cloud.

---

## Tests required (unit + integration)

1. `test_inbox_row_v2_schema_round_trip`
2. `test_writer_defaults_local_default_tenant_in_v1_mode`
3. `test_writer_rejects_unauthenticated_tenant_in_cloud_mode`
4. `test_check_inbox_filters_by_tenant_id`
5. `test_send_to_peer_carries_authenticated_tenant_id`
6. `test_compact_upgrades_v1_rows_to_v2`
7. `test_session_registry_per_tenant_isolation`
8. `test_audit_log_per_tenant_visibility_with_system_pass_through`
9. `test_cross_project_pair_refuses_cross_tenant_by_default`
10. `test_per_tenant_pause_does_not_affect_other_tenants`
11. `test_per_tenant_circuit_breaker_isolation`
12. `test_strategy_a_per_tenant_subdirs_layout`
13. `test_migrate_root_upgrades_local_default_to_cloud_tenant`

---

## Forward-compat with `BRIDGE_TRANSPORT_ABSTRACTION_SPEC.md`

The transport interface methods take an `Identity` parameter (which includes `tenant_id`). All implementations enforce tenant scoping at the transport boundary, so neither the storage layer nor the MCP server logic above need to repeat the check.

---

## Coordination model

Codex implements; Claude reviews. Audit profile:
`tools/agent-bridge/audit-profiles/tenant-scoping.md` (to be added if needed; can be a section in a broader Tier-2 audit profile).

[[handoff:codex]]
