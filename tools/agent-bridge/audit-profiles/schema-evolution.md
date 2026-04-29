# Audit Profile - Schema Evolution Policy

**Trigger:** commit adding `schema_version` field to a persistent file, OR commit bumping schema_version on existing file
**Reference spec:** `BRIDGE_SCHEMA_EVOLUTION_SPEC.md` SE1-SE10

---

## Files this profile covers

- All persistent JSON / JSONL files at the bridge root and under `state/`
- `tools/agent-bridge/migrate_root.py` (--upgrade-schema mode addition)
- `tools/agent-bridge/recover_state.py` (--scan-historical extension)

---

## SE-by-SE audit checklist

### SE1 - schema_version present on every entity

For each persistent file added or modified in this commit:

- [ ] Top-level `schema_version` field (whole-file JSON) OR per-row `schema_version` (JSONL)
- [ ] Initial value = 1 (unless documented promotion to higher)
- [ ] Tests assert presence on writes

### SE2 - Reader defaults v1 when missing

- [ ] Reader for this file accepts rows/files without `schema_version` (treats as v1)
- [ ] Test: legacy fixture without field reads cleanly

### SE3 - Forward-compat for newer optional fields

- [ ] Reader silently ignores unknown fields when `schema_version > current_known_version` AND new fields are optional
- [ ] Logs `schema_version_unknown_fields_ignored` warning
- [ ] Test: synthetic v(N+1) fixture with extra optional field reads via vN reader

### SE4 - Refuses newer required fields

- [ ] Reader fails closed when newer schema adds REQUIRED fields the reader can't interpret
- [ ] Emits `schema_version_required_field_unsupported` error
- [ ] Test: synthetic v(N+1) fixture with new required field → vN reader refuses

### SE5 - Malformed schema_version → quarantine

- [ ] Non-int / missing-when-required `schema_version` → row/file quarantined to `*.quarantine.jsonl`
- [ ] Emits `schema_version_malformed` audit event
- [ ] Test: corrupted fixture → quarantine

### SE6 - migrate_root.py --upgrade-schema

- [ ] New `--upgrade-schema <ver>` mode in `migrate_root.py`
- [ ] Acquires `state/locks/migration.lock` singleton lease
- [ ] Iterates eligible files; rewrites per upgrade procedure
- [ ] Emits `schema_version_migrated` audit per file
- [ ] Atomic completion (mid-failure marker for retry)
- [ ] Test with mocked file tree

### SE7 - recover_state.py --scan-historical extended

- [ ] Detects files with `schema_version < current_known`
- [ ] Detects missing `schema_version` field
- [ ] Detects malformed `schema_version`
- [ ] Reports each as informational/warning/error appropriately
- [ ] Test asserts each detection path

### SE8 - Compact-on-touch upgrades

- [ ] When a writer rewrites a row for any reason (receipt update, etc.), schema is upgraded in-place to current version
- [ ] No mass migration required for routine bumps; amortized over normal use
- [ ] Test: v1 row marked_read → resulting row is v2

### SE9 - No backward-compat writes

- [ ] grep verifies no writer emits `schema_version: 1` after canonical version is 2
- [ ] All write paths emit current canonical version

### SE10 - Per-bump documentation requirement

- [ ] Any file with schema_version > 1 has corresponding section in its owning spec doc:
  - Bump rationale
  - Field-by-field diff
  - Reader compat behavior
  - Migration tool reference
  - Test surface
- [ ] CI lint check: every `schema_version > 1` referenced in at least one spec doc with required diff section

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_schema_evolution
```

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Schema evolution policy <subscope>
ACTION_REQUESTED: none | document-bump-rationale
NONCE: audit-schema-evolution-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage vs SE1-SE10:

| SE# | Status |
|---|---|
| SE1 | DONE / PARTIAL / DEFERRED / MISSING |
| ...up through SE10 |

Schema bumps in this commit:
- <file>: vN -> vN+1; required fields added: [...]; optional fields added: [...]
- Per-bump doc section verified in: <spec_doc>

Tests at HEAD: <N>

[[handoff:codex]]
```

---

## Coordination

Schema bumps land per-feature. This profile is referenced by other profiles (e.g. `bootstrap-provenance.md` BP5 schema bump uses SE policy). Standalone changes that just add `schema_version` to existing file (Phase SE.1) audited via this profile alone.
