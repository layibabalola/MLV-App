# Agent Bridge - Schema Evolution Spec

**Status:** Proposed (forward-compat)
**Authors:** Claude (proposal); Codex implementation pending
**Tier:** Tier 2 — architecture-now-for-future-LAN/WAN
**Motivation:** formalize the `schema_version` policy bridge-wide. Already established for `bridge-root.json` (Phase 13) and runtime breadcrumbs; promote to canonical pattern so cloud rollouts can ship schema changes incrementally without coordinated client updates.

---

## Goal

Every persisted JSON / JSONL entity in the bridge carries a `schema_version` integer. Readers handle older, current, and newer schemas with documented forward-compat semantics. Writers always emit the current version. Migration tools exist for breaking changes.

---

## Existing precedents (already in tree)

These set the pattern; this spec generalizes.

| File | Current schema_version | Established by |
|---|---|---|
| `bridge-root.json` | 1 | Phase 13 (`412228af`) |
| `MOVED_TO.json` | implicit 1 (no field; treat as 1) | Phase 13 |
| `peer-<agent>.runtime.json` | 1 | Phase B (`PHASE_B_BREADCRUMB_DESIGN.md`) |
| `presence-<agent>.runtime.json` | 1 | Multi-layer presence (`BRIDGE_PRESENCE_SPEC.md`) |
| `pause.json` | 1 | Wake hardening (`WAKE_HARDENING_SPEC.md` D1) |
| Server runtime breadcrumbs `server-<pid>.json` | 1 | Runtime breadcrumbs (`8c69ea20`) |
| Watcher runtime breadcrumb `watcher.runtime.json` | 1 | Runtime breadcrumbs (`8c69ea20`) |

This spec applies the same pattern to entities that lack it today.

---

## Files needing schema_version

| File | Today | Add field | Initial value |
|---|---|---|---|
| `state/inbox-<agent>.jsonl` | per-row, no schema_version | per-row `schema_version` | 1 (legacy), 2 after tenant scoping spec lands |
| `state/orphaned-<agent>.jsonl` | same | same | same |
| `state/messages.jsonl` (audit) | per-row, no schema_version | per-row `schema_version` | 1 → 2 with tenant_id |
| `state/state.json` | no field | top-level `schema_version` | 1 |
| `session.json` | no field | top-level `schema_version` and per-entry | 1 |
| `watcher-config.json` | no field | top-level `schema_version` | 1 |
| `routing-rules.json` | no field | top-level `schema_version` | 1 |
| `settings.json` | no field | top-level `schema_version` | 1 |
| `state/wake-failure-windows.json` | (new) | top-level `schema_version` | 1 |
| `state/cross-project-pairs/<link_id>.json` | (new) | top-level `schema_version` | 1 |
| `state/pending_*.jsonl` | (new) | per-row | 1 |
| `machine.json` | (new) | top-level | 1 |

---

## Reading rules (forward-compat tolerance)

**Required behavior of every reader:**

1. **Missing `schema_version` field:** treat as `schema_version: 1` (compat for files written before this spec)
2. **Equal version (`reader_max == writer_version`):** read all fields normally
3. **Older version (`reader_max > writer_version`):** read using older-version field set; missing newer fields default per their documented defaults; emit `schema_version_old_read` audit at INFO level (informational, not warning)
4. **Newer version (`reader_max < writer_version`):**
   - If only optional fields are added: read known fields, ignore unknown, emit `schema_version_unknown_fields_ignored` warning audit
   - If required fields are added: refuse to read; emit `schema_version_required_field_unsupported` error; surface to operator
5. **Malformed `schema_version` (not an int, missing in a v2+ context where required):** quarantine the row/file (move to `*.quarantine.jsonl`); emit `schema_version_malformed` error audit

**"Required vs optional" is documented per schema bump.** A required field added in v2 means: a v1 reader fails closed when seeing v2 data without that field. An optional field added in v2 means: v1 reader simply ignores it.

---

## Writing rules

**Every writer MUST:**

1. Always emit the current `schema_version` for the file/entity it owns
2. Include all required fields for that version
3. Include known optional fields when applicable; do not emit unknown extension fields unless the entity defines an `extras` map (see `BRIDGE_TRANSPORT_ABSTRACTION_SPEC.md`)
4. Use atomic write: `.tmp` → `os.replace` for whole-file writes; line-append for JSONL with shared lock; never partial overwrite

**No backward-compat writes.** Writers do NOT emit older schema versions for backward compatibility with hypothetical older readers in the wild. The reader-tolerance rules above handle the asymmetry.

---

## Per-bump documentation requirements

When a schema_version is bumped (e.g. v1 → v2), the spec doc owning that file MUST include:

1. **Bump rationale** (one paragraph): why the change is needed
2. **Field-by-field diff:** which fields are added (optional or required), removed, type-changed
3. **Reader compat behavior:** what happens when a v1 reader sees v2 data, and vice versa
4. **Migration tool reference:** if a migration is needed (e.g. row rewrites), name the tool. Optional inline migration patterns (compact-on-touch) are documented here.
5. **Test surface:** which tests assert v1↔v2 round-trip and unsupported-field behavior

Example: `BRIDGE_TENANT_SCOPING_SPEC.md` already documents v1 → v2 for inbox rows and audit events with `tenant_id` + `originator_machine_id` as required fields.

---

## Migration tooling

For breaking schema changes (required-field additions, type changes, structural changes), provide migration tools:

### Compact-on-touch (inline)

For rows in JSONL files: when a writer rewrites a row for any reason (receipt update, marked seen, etc.), it upgrades the schema in place. No mass migration required; the upgrade is amortized over normal use.

### `migrate_root.py --upgrade-schema <ver>` (mass)

For whole-file changes that affect on-disk layout (e.g. moving from Strategy B to Strategy A in tenant scoping). Adds a `--upgrade-schema` mode that:

1. Acquires a singleton lease (`state/locks/migration.lock`)
2. Iterates all eligible files
3. Rewrites each per the documented upgrade procedure
4. Emits `schema_version_migrated` audit per file
5. Atomic completion: a failure mid-migration leaves a `.migration_in_progress.tmp` marker so retry resumes

### Recovery scan integration

`recover_state.py --scan-historical` already detects partial-migration states for `bridge-root.json`. Extend to check for any file with `schema_version < current_known` and report:

- Files with old versions (informational; rewrite-on-touch will eventually upgrade)
- Files with missing `schema_version` field (potential v0 legacy; flag for review)
- Files with malformed `schema_version` (corruption; require operator action)

---

## Cross-implementation considerations (LAN/cloud)

For LAN sync (`BRIDGE_LAN_TRANSPORT_SPEC.md`, Tier 3): replication includes `schema_version`; receiver applies its own forward-compat rules independently. Two peers can run different schema versions during a rolling upgrade window; older peer ignores new optional fields, never sees required new fields it cannot read because writer side hasn't started writing them.

For cloud (`BRIDGE_AWS_MULTITENANT_DESIGN.md`, Tier 3): schema migrations roll out via Lambda, applied per-tenant on a controlled schedule. DynamoDB items carry `schema_version` attribute; reader Lambda routes to version-specific handlers.

---

## Schema bumps planned in current roadmap

In dependency order:

| Bump | File | New fields | Required? | Owning spec |
|---|---|---|---|---|
| v1 → v2 | `inbox-<agent>.jsonl` rows | `tenant_id`, `originator_machine_id` | Required for cloud, optional for v1 | `BRIDGE_TENANT_SCOPING_SPEC.md` |
| v1 → v2 | `messages.jsonl` rows | `tenant_id`, `originator_machine_id` | Same | Same |
| 0 → 1 | `state.json` | top-level `schema_version` | Required | This spec |
| 0 → 1 | `session.json` | top-level + per-entry | Required | This spec |
| 0 → 1 | `watcher-config.json` | top-level | Required | This spec |
| v1 → v2 | `session.json` entry | `tenant_id`, `last_heartbeat_at` | Optional | Tenant scoping + presence |
| v1 → v2 | `peer-<agent>.runtime.json` | (none yet; reserved for breadcrumb extensions) | n/a | Phase B follow-ups |
| v1 → v2 | `bridge-root.json` | `default_tenant_id`, `transport_default` | Optional | Tenant scoping + transport |

Each bump documented in its owning spec per the per-bump requirements above.

---

## Acceptance criteria

- SE1. Every persisted JSON / JSONL entity has `schema_version` field; tests assert presence on write.
- SE2. Readers default to `schema_version: 1` when field is absent; tests assert with legacy-format fixtures.
- SE3. Readers tolerate newer optional fields; tests assert with synthetic v2 fixtures.
- SE4. Readers refuse newer required fields with `schema_version_required_field_unsupported`; tests assert.
- SE5. Malformed `schema_version` triggers quarantine; tests assert.
- SE6. `migrate_root.py --upgrade-schema` mass-migrates correctly; tests assert with mocked file tree.
- SE7. `recover_state.py --scan-historical` reports schema-version mismatches; tests assert.
- SE8. Compact-on-touch upgrades rows when rewriting; tests assert with v1 fixture rewritten through `mark_read` ending up as v2.
- SE9. No backward-compat writes occur; tests assert via grep that no writer emits `schema_version: 1` after the canonical version is 2.
- SE10. Per-bump documentation requirement enforced via spec-doc lint (CI check that any file with `schema_version > 1` is referenced in at least one spec doc with the required fields-diff section).

---

## Tests required

1. `test_writer_emits_current_schema_version`
2. `test_reader_defaults_v1_when_field_missing`
3. `test_reader_tolerates_newer_optional_fields`
4. `test_reader_refuses_newer_required_fields`
5. `test_reader_quarantines_malformed_version`
6. `test_compact_on_touch_upgrades_v1_to_v2`
7. `test_migrate_root_upgrade_schema_mass_migration`
8. `test_recover_state_scan_historical_flags_old_versions`
9. `test_no_writer_emits_legacy_versions`
10. `test_lan_replication_handles_mixed_versions` (Tier 3, when LAN ships)

---

## Coordination model

Codex implements; Claude reviews. This spec is policy + tooling; minimal new code, mostly conventions and adding `schema_version` fields to existing files.

Suggested phasing:

- **Phase SE.1** Add `schema_version` to files that lack it (state.json, session.json, watcher-config.json, settings.json) at version 1; readers tolerate absence
- **Phase SE.2** Implement `migrate_root.py --upgrade-schema` and extend `recover_state.py --scan-historical`
- **Phase SE.3** Tenant-scoping bumps land (per `BRIDGE_TENANT_SCOPING_SPEC.md`); use the policy machinery to handle the v1→v2 transitions
- **Phase SE.4** Spec-doc lint added to CI

Audit profile: `tools/agent-bridge/audit-profiles/schema-evolution.md` (to be added).

[[handoff:codex]]
