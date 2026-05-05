# Agent Bridge - Component Supervision Spec

**Status:** Implemented for current long-running bridge components; future
components must register a supervision pattern before shipping.
**Authors:** Claude (proposal); Codex implementation/review
**Tier:** Tier 1 - architectural floor; codifies the convention behind today's wake-storm hardening
**Motivation:** On 2026-04-29 the bridge ate four wake storms. Every storm traced to one root cause: a long-running component (server.py or watcher.py) running stale code while fresher fixes sat in the worktree, untouched by the live process. We patched both in commits `d97eaf9c`, `96ae99d0`, `bfe9a3ec`, `47948817`, `3a380df1`, `d09b4c1a`, `ff5dff0d` - but each component got its own ad-hoc supervision mechanism without a written policy that says "every long-running component MUST self-heal on stale code." This spec codifies that policy so future components inherit the convention rather than reinvent (or skip) it.

The convention is the spec. Configuration is the escape hatch, not the design. This composes with the user-direction memory `convention_over_configuration.md` (2026-04-29).

---

## Implemented Status

Current bridge-owned long-running components comply with this spec: `server.py`
is protected by the wrapper/tool-manifest refresh path, and `watcher.py` is
protected by bootstrap-time code-signature restart plus orphan watcher sweep.
The remaining work is prospective only: any future `bridge-d`, presence agent,
or monitor component must add a registry entry and choose Pattern A or Pattern B
before it lands.

---

## Goal

Define a single architectural rule: every long-running bridge component MUST self-heal when its code is stale, using one of two acceptable supervision patterns, with no user opt-in required for the safety behavior.

## Non-Goals

- No new build / install / packaging system. The patterns work with existing Python script + PowerShell wake architecture.
- No in-process hot-reload (Pattern C). Too complex for current needs; ruled out explicitly.
- No tenant or cross-machine supervision concerns. Single-machine v1.
- No new MCP tools beyond what the supervision patterns already require (existing wrapper Phase 2 + `restart_watcher_for_code_change()`).

---

## Architecture

```
                 ┌──────────────────────────────────────────────┐
                 │  Long-running bridge component               │
                 │  (e.g. server.py, watcher.py, future bridge-d) │
                 └──────┬─────────────────────────────────────┬─┘
                        │                                     │
                        │   either                            │
                        ▼                                     ▼
        ┌──────────────────────────┐    ┌──────────────────────────────┐
        │ Pattern A — Wrapper      │    │ Pattern B — Helper-driven     │
        │   supervision            │    │   restart on cycle            │
        │                          │    │                               │
        │ Parent process polls     │    │ Periodic / triggered check    │
        │ child source mtime or    │    │ (e.g. bootstrap_session.py)   │
        │ signature; relaunches    │    │ verifies running component's  │
        │ child on change          │    │ lease signature against       │
        │                          │    │ current code; clears stale    │
        │ Used today by:           │    │ leases                        │
        │   server.py (via         │    │                               │
        │   server_wrapper.py)     │    │ Used today by:                │
        │                          │    │   watcher.py (via             │
        │                          │    │   restart_watcher_for_code_   │
        │                          │    │   change in 3a380df1)         │
        └──────────────────────────┘    └──────────────────────────────┘

  Default-ON. No opt-in flag for the safety property.
  Opt-out is allowed only as an explicit debug-only escape hatch.
```

Pattern C (in-process hot-reload via `importlib.reload` / signal-driven re-import) is explicitly OUT OF SCOPE. Surface area too large; correctness too fragile.

---

## Acceptance Criteria

### CS1 - Every long-running component declares its supervision pattern

- [ ] Each long-running component documents in its source header (or per-component spec) which pattern (A or B) it uses, including:
  - The signature scope (which files affect runtime behavior)
  - The restart trigger (wrapper poll cadence, or helper invocation point)
  - The audit event emitted on restart
- [ ] A central registry table (in this spec, see "Per-Component Registry" below) is updated for each new component

### CS2 - Default-ON safety, opt-out allowed only as debug escape hatch

- [ ] No opt-in flag is required for restart-on-stale-code behavior
- [ ] If an opt-out exists, its name must explicitly indicate "debug" or equivalent semantics (`--no-X-for-debugging`, etc.)
- [ ] Opt-out flags MUST NOT be set in production / standard SessionStart hook configs
- [ ] Tests assert that the default code path (no flag passed) DOES restart on stale code

### CS3 - Signature scope must include all wake-relevant files

- [ ] Pattern A wrapper signature OR Pattern B helper signature includes EVERY file that affects component runtime behavior
- [ ] At minimum: the component's own source, plus any modules it imports from the bridge codebase, plus any wake / hook PowerShell scripts the component spawns
- [ ] Adding a new file that the component imports/spawns requires updating its signature scope

### CS4 - Restart events must be auditable

- [ ] Every restart event emits a structured audit record with: component name, reason (`signature_changed` / `missing_signature` / `manual_restart`), old PID, new PID
- [ ] Audit records are queryable for incident retrospectives (per existing `messages.jsonl` audit log)

### CS5 - Per-component registry maintained in this spec

- [ ] Adding a new long-running component requires adding a row to the registry table (see below) with its pattern, signature scope, restart trigger, and default-on status
- [ ] Removing a component from the registry requires explicit rationale (it stops being long-running, or migrates to a different supervision strategy)
- [ ] CI lint check (future): grep for `long_running = True` (or equivalent marker) in bridge components; verify each appears in this registry

### CS6 - New components without supervision = spec violation

- [ ] Any commit that introduces a new long-running bridge process without picking Pattern A or Pattern B is a STATUS=fail audit verdict
- [ ] The audit profile (see `audit-profiles/component-supervision.md`) enforces this fail-fast

### CS7 - Symmetric coverage policy

- [ ] Both Claude-side and Codex-side bridge components fall under this spec
- [ ] Cross-agent components (the watcher serves both agents) are covered once
- [ ] Asymmetries (e.g., one agent runs a daemon the other doesn't) must be justified in the registry

### CS8 - Orphan detection (added 2026-04-29 from real incident)

**Motivation:** d09b4c1a's `restart_watcher_for_code_change` kills the lease-holder if its code signature is stale. Orphans WITHOUT a lease are not detected. On 2026-04-29, watcher PID 107712 (started 13:20 UTC, no lease, pre-fix code) ran for 8+ hours alongside the lease-holder PID 113248 (post-fix code). Net effect: doubled poll rate, toast spam, state-file thrash. Orphans accumulate silently because the supervision logic only sees the lease.

The supervision pattern must therefore detect orphans regardless of lease status.

- [ ] Restart helper or sibling sweeper enumerates all processes matching the long-running component's command line pattern (e.g., `python.exe ... watcher.py`)
- [ ] Identifies the legitimate lease-holder via the lease file (e.g., `watcher.lock` PID)
- [ ] For every other matching process: kills (Stop-Process / psutil.kill)
- [ ] Audit event `orphan_watcher_killed` per kill with PID, parent_pid, started_at
- [ ] Sweep runs on every bootstrap invocation (default-on per CS2 convention-over-configuration)
- [ ] After bootstrap completes: exactly one process matching the watcher pattern is running

**Critical invariant**: orphans CANNOT accumulate over time. If a sweep doesn't find any orphans, that's a no-op cheap operation. If it finds orphans, it kills them and audits — silent recovery.

**Tests:**
- `test_sweep_orphan_watchers_kills_non_lease_processes`
- `test_sweep_orphan_watchers_preserves_lease_holder`
- `test_sweep_runs_on_every_bootstrap`
- `test_sweep_audits_each_kill`

**Composes with CS2 (default-ON):** the sweep is part of the supervision floor; it MUST NOT require an opt-in flag. Any restart helper that doesn't include orphan sweep (or that gates it behind a flag) is a CS8 violation.

---

## Per-Component Registry

| # | Component | Pattern | Signature scope | Restart trigger | Default-ON | Audit event | Spec ref |
|---|---|---|---|---|---|---|---|
| 1 | `server.py` / `server_wrapper.py` (Desktop MCP stack) | A - wrapper + trampoline | `server.py`, `agent_bridge.py`, `server_wrapper.py`, `core/*.py` | `server_wrapper.py` poll; exit 77 via `server_wrapper_trampoline.py` for wrapper edits | yes | `mcp_server_self_restarted`, `mcp_server_wrapper_self_restart_requested`, `mcp_server_wrapper_launch` | (Phase 2 + trampoline work) |
| 2 | `watcher.py` (inbox poller / wake fire) | B — helper + CS8 orphan sweep | `watcher.py`, `wake_codex.ps1`, `bootstrap_session.py`, `configure_watcher.py`, `agent_bridge.py`, `core/runtime.py` | `restart_watcher_for_code_change()` + `sweep_orphan_watchers()` invoked by `bootstrap_session.py` (default `True` per `d09b4c1a`) | yes | `watcher_restart_code_changed`, `orphan_watcher_killed` | `3a380df1` + `d09b4c1a` + (pending CS8 commit) |
| 3 | (future) `bridge-d` daemon | TBD | TBD | TBD | yes | TBD | TBD |
| 4 | (future) presence agent | TBD | TBD | TBD | yes | TBD | TBD |

Future entries are placeholders to ensure the spec is consulted when adding a new long-running component.

---

## Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Component runs stale code (the 2026-04-29 incidents) | Pattern A/B signature mismatch | Auto-restart per pattern |
| Wrapper process dies mid-session | Lock heartbeat staleness | Lock owner detects, replacement wrapper takes over (existing Phase 2 logic) |
| Helper invocation point never fires | Missing `bootstrap_session.py` invocation | SessionStart hook ensures bootstrap runs each session start; periodic external trigger ensures cycle |
| Signature scope incomplete | Manual review at audit | CS5 registry maintenance + CS3 audit checklist |
| User explicitly opts out for debug then forgets to flip back | Risk of stale code re-emerging | CS2 opt-out flag naming makes intent explicit; debug sessions are short-lived |

---

## Tests Required

**Unit:**

- `test_component_supervision_pattern_a_wrapper_relaunches_on_signature_change`
- `test_component_supervision_pattern_b_helper_clears_stale_lease`
- `test_component_supervision_default_path_restarts_without_opt_in`  (CS2 — proves no flag needed)
- `test_component_supervision_opt_out_flag_preserves_stale_for_debug`  (CS2 — proves debug escape hatch works)
- `test_component_supervision_signature_scope_includes_all_wake_files`  (CS3 — guards against scope drift)
- `test_component_supervision_audit_event_emitted_on_restart` (CS4)

**Integration:**

- `test_int_supervision_full_cycle_pre_d97eaf9c_regression`: simulate the actual 2026-04-29 13:53 storm scenario - watcher running pre-fix code, fresh fix files present, bootstrap runs, watcher gets restarted with new code, wake fires with new behavior. Verifies the spec's central promise.
- `test_int_supervision_registry_completeness`: scan the bridge codebase for `long_running` markers and verify each appears in the registry table.

---

## Phased Rollout

**CS-Phase 1 (already shipped — this spec is retroactive codification):**
- server.py via Wrapper Phase 2 (Pattern A)
- watcher.py via `restart_watcher_for_code_change()` per `3a380df1` + `d09b4c1a` (Pattern B)

**CS-Phase 2 (this spec):**
- Document the rule formally
- Per-component registry table maintained as the source of truth
- Audit profile fails any new component that doesn't comply

**CS-Phase 3 (future, gated on new component additions):**
- When `bridge-d` or presence agent or any new long-running component lands, registry entry + pattern selection is required pre-merge

---

## Coordination Model

Codex implements / extends; Claude reviews. Audit profile: `tools/agent-bridge/audit-profiles/component-supervision.md` (added with this spec).

**Composes with:**

- `BRIDGE_HARDENING.md` - this spec is a hardening sub-spec
- `convention_over_configuration` (memory note) - codifies the architectural application of that rule
- `BRIDGE_HEALTH_PANEL_SPEC.md` HP3 + HP4 (watcher / server status) - panel surfaces the supervision state per registry entry
- `WAKE_HARDENING_SPEC.md` Layer 4 (D2 breaker) - operates ABOVE this layer; supervision keeps code fresh, breaker handles wake firing failures

**Does NOT compose with:**

- In-process hot-reload (explicitly out of scope)
- Cross-machine supervision (Tier-3 future)

[[handoff:codex]]
