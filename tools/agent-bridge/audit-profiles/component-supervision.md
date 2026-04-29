# Audit Profile - Component Supervision

**Trigger:** any commit adding or modifying a long-running bridge component, OR any commit changing supervision wrappers / restart helpers / signature scope
**Reference spec:** `BRIDGE_COMPONENT_SUPERVISION_SPEC.md` CS1-CS7 + Per-Component Registry

---

## Files this profile covers

- All long-running component sources (`tools/agent-bridge/server.py`, `watcher.py`, future daemons)
- Supervision plumbing (`server_wrapper.py`, `bootstrap_session.py:restart_watcher_for_code_change`, future analogues)
- Registry: `BRIDGE_COMPONENT_SUPERVISION_SPEC.md` "Per-Component Registry" table
- Tests: `test_agent_bridge.py`, `test_phase0_contract.py`, `test_server_wrapper_phase2.py`

---

## CS-by-CS audit checklist

### CS1 - Component declares supervision pattern

- [ ] Component's source header / per-component spec names Pattern A or Pattern B
- [ ] Signature scope (files) explicitly listed
- [ ] Restart trigger (wrapper poll OR helper invocation point) explicitly listed
- [ ] Audit event name explicitly listed

### CS2 - Default-ON, opt-out allowed only as debug escape hatch

- [ ] No flag required for the safety behavior
- [ ] If opt-out exists, its name contains "debug" / "skip" / explicit-intent indicator (e.g. `--no-restart-watcher-if-code-changed`)
- [ ] Production / standard configs (SessionStart hook, AGENTS.md hook, etc.) do NOT pass the opt-out flag
- [ ] Tests:
  - `test_*_default_path_restarts_without_opt_in`
  - `test_*_opt_out_flag_preserves_stale_for_debug`

**Critical pushback:** if a commit ships a new long-running component with default-OFF supervision, STATUS=fail. Re-shipping `3a380df1`'s opt-in default would be the canonical example - exactly what `d09b4c1a` corrected.

### CS3 - Signature scope completeness

- [ ] Component's signature includes every file affecting runtime behavior:
  - Component's own source
  - All bridge modules it imports
  - All scripts it spawns (e.g., `wake_codex.ps1` for watcher)
  - Any config schema it parses (in some cases)
- [ ] Adding a new dependency to a component triggers a signature scope update in the same commit
- [ ] Tests assert signature changes when any in-scope file changes

### CS4 - Audit event emitted on restart

- [ ] Restart event includes: component name, reason (`signature_changed` / `missing_signature` / `manual_restart` / `wrapper_relaunch`), old PID, new PID
- [ ] Event name follows naming convention: `<component>_restart_<reason>` or equivalent
- [ ] Event is queryable from `messages.jsonl` audit log
- [ ] Tests assert event emission on each restart trigger path

### CS5 - Per-component registry maintained

- [ ] Adding a long-running component to the codebase REQUIRES updating the spec's registry table in the same commit
- [ ] Registry row includes: name, pattern, signature scope, restart trigger, default-ON status, audit event, spec ref
- [ ] Removing a component requires explicit rationale (commit message + spec amendment)
- [ ] (Future) CI lint check verifies registry completeness

### CS6 - New components without supervision = STATUS=fail

- [ ] Any commit introducing a long-running process (subprocess.Popen with watchdog, daemon thread, etc.) MUST select Pattern A or B
- [ ] Lacking pattern selection → audit STATUS=fail regardless of other coverage
- [ ] No "we'll add supervision later" exceptions

### CS7 - Symmetric coverage

- [ ] Both Claude-side and Codex-side long-running components covered
- [ ] Cross-agent components (watcher) covered once but symmetric in effect
- [ ] Asymmetries documented with rationale in registry

### CS8 - Orphan detection (the 2026-04-29 incident class)

- [ ] Sweep enumerates all processes matching component command-line pattern (not just lease-holder)
- [ ] Lease-holder identified via lease file PID
- [ ] Non-lease orphans killed with audit event `orphan_watcher_killed` per kill (or `orphan_<component>_killed`)
- [ ] Sweep runs default-on as part of supervision; NO opt-in flag for the safety behavior
- [ ] After bootstrap: exactly one process per long-running component running
- [ ] Tests: `test_sweep_orphan_watchers_kills_non_lease_processes`, `test_sweep_orphan_watchers_preserves_lease_holder`, `test_sweep_runs_on_every_bootstrap`, `test_sweep_audits_each_kill`

**Critical pushback if seen during audit**: any restart helper that ships without orphan-sweep, OR that gates orphan-sweep behind an opt-in flag, is STATUS=fail. The 2026-04-29 incident proved the gap is real — sweep is required, not optional.

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_phase0_contract test_server_wrapper_phase2
```

Plus the registry-completeness integration test (CS5):

```bash
py -3 -m unittest test_agent_bridge.AgentBridgeTests.test_int_supervision_registry_completeness
```

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
NONCE: audit-component-supervision-<sha-short>
TIMESTAMP: <iso-utc>
SCOPE: project-only
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Component supervision <subscope>
ACTION_REQUESTED: none | implement-deferred-followups | fix-default-off-violation

Reviewed <sha>. Coverage:

| CS# | Status |
|---|---|
| CS1 | DONE / PARTIAL / DEFERRED / MISSING |
| CS2 | ... |  ← critical: default-ON must be verified
| CS3 | ... |
| CS4 | ... |
| CS5 | ... |
| CS6 | ... |  ← critical: new components must pick pattern
| CS7 | ... |

Registry update verified: yes / no
Default-ON across all entries: yes / no (entries violating, if any: ...)
Signature scope changes: <files added/removed since prior commit>
Tests at HEAD: <N>

[[handoff:codex]]
```

---

## Coordination

This spec retroactively codifies what `d97eaf9c` + `3a380df1` + `d09b4c1a` already shipped, plus future-proofs by mandating the rule for new long-running components.

**Critical fail-fast rules** (non-negotiable):

1. **Default-OFF for safety** is auto-fail (CS2 violation). The shipped `3a380df1` had this issue; `d09b4c1a` corrected it. Any future commit must default-ON the supervision behavior.
2. **No supervision pattern selected** is auto-fail (CS6 violation). Adding a long-running process without Pattern A or B = STATUS=fail.
3. **Registry not updated** when adding a component is auto-fail (CS5 violation).

These rules are sticky regardless of test count or other coverage. The whole point of this spec is "the convention is the spec."
