# Audit Profile - Tenant Scoping

**Trigger:** commit adding `tenant_id` field to persistent entity, OR commit refactoring read/write to filter by tenant
**Reference spec:** `BRIDGE_TENANT_SCOPING_SPEC.md` T1-T10

---

## Files this profile covers

- All persistent files (inbox, audit, breadcrumb, presence, session.json, watcher-config, pause.json, machine.json, etc.)
- `tools/agent-bridge/agent_bridge.py` (MCP tool args)
- `tools/agent-bridge/core/transport.py` and implementations (transport-layer enforcement)
- `tools/agent-bridge/migrate_root.py` (--upgrade-tenant mode)

---

## T-by-T audit checklist

### T1 - Schema v2 supports tenant_id

For each persistent entity touched by this commit:

- [ ] `tenant_id` field present (required in v2)
- [ ] Default `local-default` for single-machine v1 readers
- [ ] Tests assert round-trip with default

### T2 - Writes default tenant_id in v1 mode

- [ ] Writes without explicit `tenant_id` accepted (defaulted to `local-default`)
- [ ] `tenant_id_defaulted` audit event emitted on default
- [ ] Tests assert with v1 single-machine fixture

### T3 - Cross-tenant read filtering

- [ ] `read_inbox(identity, ...)` filters: only rows where `row.tenant_id == identity.tenant_id`
- [ ] Strategy B (shared file) test: 3 rows from 3 different tenants in one file → each tenant reads only its own
- [ ] Strategy A (per-tenant subdir) test: each tenant subdir contains only its own data

### T4 - Cross-tenant writes refused

- [ ] Sender cannot forge another tenant's tenant_id
- [ ] If write specifies `tenant_id != identity.tenant_id`, transport refuses (cloud mode)
- [ ] Single-machine mode (LocalUserAuth): always passes since tenant_id is local-default
- [ ] Tests assert refusal in cloud-mode fixture

### T5 - Reserved values

- [ ] `local-default` reserved (single-machine; never appears in cloud)
- [ ] `system` reserved for bridge-internal ops; not assignable to user
- [ ] `unassigned` reserved for transient state; refuses inbound work writes
- [ ] Tests assert each reserved value behavior

### T6 - tenant_id_defaulted audit

- [ ] Reading a v1 row in v2 context: `tenant_id_defaulted` event with row id + defaulted_to value
- [ ] Tests assert with mixed v1/v2 fixture

### T7 - Cross-project pairing refuses cross-tenant by default

- [ ] If `CROSS_PROJECT_PAIR_REQUEST` source.tenant != target.tenant: refused
- [ ] Operator-level enable required for cross-tenant pairing (if ever supported)
- [ ] Tests assert refusal with two-tenant fixture

### T8 - compact-on-touch upgrades v1 → v2

- [ ] When a writer rewrites a row, schema upgraded in place (per BRIDGE_SCHEMA_EVOLUTION_SPEC SE8)
- [ ] `tenant_id` field added with default value during upgrade
- [ ] Tests assert with v1 fixture rewritten via mark_read → result is v2 with tenant_id

### T9 - Per-tenant pause isolation

- [ ] `pause.json` schema includes `tenant_id` scope field
- [ ] Pause for tenant A does not affect tenant B
- [ ] Tests assert with two-tenant fixture

### T10 - Per-tenant circuit breaker isolation

- [ ] `wake-failure-windows.json` keyed by `(tenant_id, session_id)`
- [ ] Tenant A's broken Codex doesn't trip tenant B's breaker
- [ ] Tests assert

### T11 - Strategy A per-tenant subdirs (cloud)

- [ ] `<bridge-root>/state/tenants/<tenant_id>/` layout supported
- [ ] `BridgeTransport.append_inbox` writes to per-tenant subdir when transport=cloud
- [ ] Tests assert with cloud-stub transport

### T12 - migrate_root --upgrade-tenant

- [ ] New mode in `migrate_root.py` for v1 → v2-cloud transition
- [ ] Rewrites every row's tenant_id from `local-default` to user's cloud tenant_id
- [ ] `tenant_id_migrated` audit per row
- [ ] Tests assert with mocked file tree

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_tenant_scoping
```

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Tenant scoping <subscope>
ACTION_REQUESTED: none | implement-deferred-followups
NONCE: audit-tenant-scoping-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage vs T1-T12:

| T# | Status |
|---|---|
| T1 | DONE / PARTIAL / DEFERRED / MISSING |
| ...up through T12 |

Schema bumps in this commit:
- <file>: tenant_id field added; defaults to <value>

Tests at HEAD: <N>

[[handoff:codex]]
```

---

## Coordination

Multi-tenant cloud is Tier-3 future work; this profile audits the forward-compat single-machine work that prepares the codebase. T1-T10 are achievable in single-machine v1; T11-T12 require cloud transport implementation.
