# Audit Profile - Wrapper Phase 2 (Persistent Supervisor)

**Trigger:** SPEC_PROPOSAL `b1f8eebf` (2026-04-28) Phase 2 of the
server_wrapper.py SPEC; persistent-supervisor portion of msg `126bbd6a`.

**Audit timing:** when Codex sends `IMPLEMENTATION_UPDATE` with a commit
SHA for Phase 2.

---

## Required behavior

The wrapper must transition from one-shot launcher (Phase 1, current
state via 456153a2) to persistent supervisor:

1. **subprocess.Popen** instead of subprocess.run, so the wrapper can
   monitor while child runs
2. **Byte-transparent stdio piping** between MCP client (parent) and
   server.py (child) — never parse JSON-RPC
3. **2s mtime poll** on watched bridge code files
4. **1s debounce** on multi-file mtime changes (handles burst)
5. **500ms quiescent restart** — only restart when stdio is idle
6. **30s restart-loop protection** — abort after 4 restarts in 30s
7. **mcp_server_self_restarted audit event** to messages.jsonl on each
   restart
8. **Inner crash without recent code change** → surface exit code, do
   NOT restart (preserves "crash bubbles to MCP client" behavior)

---

## Required test coverage

Per the SPEC (b1f8eebf), six unit tests:

- [ ] `test_wrapper_phase2_pumps_stdio_bytes` — byte pass-through
- [ ] `test_wrapper_phase2_restarts_on_mtime_change` — restart on file
  touch
- [ ] `test_wrapper_phase2_debounces_burst` — 3 file touches → 1 restart
- [ ] `test_wrapper_phase2_idle_gates_during_io` — no restart while io
  is live
- [ ] `test_wrapper_phase2_loop_protection_aborts_at_4th` — abort with
  non-zero exit
- [ ] `test_wrapper_phase2_does_not_restart_on_crash_without_code_change`
  — crash bubbles to client

If any of these is missing or skipped, push back.

---

## Audit checklist

### Implementation file (`server_wrapper.py`)

- [ ] subprocess.Popen replaces subprocess.run
- [ ] Two byte-pump threads (parent.stdin → child.stdin,
  child.stdout → parent.stdout) — NOT one async loop, NOT JSON-aware
- [ ] stderr passes through unchanged
- [ ] mtime poll thread runs separately, 2s cadence
- [ ] Watched files computed via `Path(__file__).parent` glob (no
  hardcoded `C:\!Layi Wkspc` literal — would break the 7b3e688f
  portability fix)
- [ ] Debounce: `last_change_time = time.monotonic()`; restart only
  fires when `now - last_change_time >= 1.0`
- [ ] Idle gate: `last_io_time` tracked per byte pumped; restart only
  when `now - last_io_time >= 0.5`
- [ ] Restart procedure: SIGTERM → 5s wait → SIGKILL fallback; drain
  stdout buffer; spawn new Popen; re-attach pump threads; write
  audit event; continue
- [ ] Audit event includes: schema_version, role, parent_pid,
  old_child_pid, new_child_pid, changed_files, elapsed_ms, bridge_root,
  state_dir
- [ ] 30s restart-loop protection: 4th restart within rolling 30s
  window → audit event `mcp_server_self_restart_aborted_loop`, exit
  non-zero
- [ ] Crash without code change → surface exit code, no restart

### Memory / state

- [ ] No new state files outside `state-dir`
- [ ] Audit events go to `<state-dir>/messages.jsonl` (consistent with
  `mcp_server_wrapper_launch` from Phase 1)

### Configurability

Per `settings_design_gate.md`: hardcoded enabled. NO new user setting.
Escape hatch: revert MCP config to direct `server.py` (no wrapper).

- [ ] No new entries in `core/settings.py` for this Phase
- [ ] Behavior is unconditional once wrapper is in MCP config

---

## Test execution

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_phase0_contract
```

Expected: 75 + 6 = 81 tests pass (75 was the count after Phase A; +6 from
Phase 2's six new tests). Adjust if title-revert lands first.

---

## Live verification (post-merge)

After Codex commits Phase 2 and the user restarts Claude Desktop +
Codex Desktop to load the new wrapper:

```python
mcp__agent-bridge__bridge_process_status()
```

Expected:
- 3+ server markers, all `running: true`, no stale
- All commands include `--max-hops 8` (Phase 1 invariant)
- The runtime breadcrumb (`server-<pid>.json`) for each marker now
  reflects fresh PIDs from auto-restart cycles, NOT the pre-Phase-2
  long-lived PIDs

After committing a small change to `tools/agent-bridge/server.py` (e.g.
add a comment), within ~5s:

- New `mcp_server_self_restarted` event in messages.jsonl
- Server marker PID changes (old PID gone, new PID present)
- MCP client (Claude Code, Codex CLI) does NOT see a connection drop

If any of these fails, push back.

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | fail | pass-with-followup
SUMMARY: <SHA> Wrapper Phase 2 persistent supervisor per SPEC_PROPOSAL b1f8eebf
ACTION_REQUESTED: none
NONCE: audit-wrapper-phase2-<sha-short>
SCOPE: project-only

Reviewed <sha>. Implementation file (`server_wrapper.py`):
- subprocess.Popen + byte pumps: CHECK | MISSING
- mtime poll + debounce + idle gate: CHECK | MISSING
- Restart procedure + audit event: CHECK | MISSING
- 30s loop protection: CHECK | MISSING
- Crash-without-code-change preserved: CHECK | MISSING

Tests:
- All 6 SPEC tests present and green: CHECK | MISSING
- Test suite at HEAD: <N> pass

Live verification:
- bridge_process_status shows --max-hops 8 + runtime breadcrumb: CHECK | MISSING
- Commit-to-restart cycle observed: CHECK | NOT TESTED

[Push back deviations; otherwise PASS]

[[handoff:codex]]
```

---

## Coordination

This is a substantial spec with many moving parts. If Codex breaks it into
sub-phases (e.g. 2a = Popen + pumps, 2b = mtime poll + restart), audit
each sub-phase independently and only PASS the full Phase 2 when all six
tests are green.
