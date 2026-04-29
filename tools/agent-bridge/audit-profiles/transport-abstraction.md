# Audit Profile - Transport Abstraction

**Trigger:** commit modifying `core/transport.py` or migrating `agent_bridge.py` direct file I/O through transport
**Reference spec:** `BRIDGE_TRANSPORT_ABSTRACTION_SPEC.md` TR1-TR7
**Phasing:** TR.1 (interface) → TR.2 (send/check migration) → TR.3 (receipt/audit migration) → TR.4 (heartbeat/lease) → TR.5 (facade size verification)

---

## Files this profile covers

- `tools/agent-bridge/core/transport.py` (new — interface + dataclasses)
- `tools/agent-bridge/core/transport_local.py` (new — LocalFilesystemTransport)
- `tools/agent-bridge/agent_bridge.py` (refactor — direct I/O removed)
- `tools/agent-bridge/server_wrapper.py` (--transport flag)
- `tools/agent-bridge/watcher.py` (transport delegation for any inbox/audit calls)
- Future: `tools/agent-bridge/core/transport_lan.py`, `transport_aws.py`

---

## TR-by-TR audit checklist

### TR1 - BridgeTransport interface defined

- [ ] `core/transport.py` exists with `BridgeTransport(ABC)` class
- [ ] All abstract methods present: `append_inbox`, `read_inbox`, `update_receipt`, `append_audit`, `write_presence`, `read_presence`, `heartbeat`, `subscribe`, `acquire_lease`, `list_pending_receipts`
- [ ] Frozen dataclasses: `Identity`, `InboxRow`, `ReceiptUpdate`, `PresenceSnapshot`
- [ ] Type hints complete; no missing return annotations
- [ ] Subclass conformance test: stub subclass that overrides each method passes

### TR2 - LocalFilesystemTransport behavior preservation

- [ ] `core/transport_local.py` implements all 10 abstract methods
- [ ] Existing test suite passes with `agent_bridge.py` rewired through `LocalFilesystemTransport` (no behavioral change)
- [ ] Tenant filtering applied in `read_inbox()` (tenant_id from Identity matches row.tenant_id)
- [ ] File-locking semantics preserved (atomic JSONL append with shared lock)
- [ ] Receipt updates use atomic line-rewrite (not partial in-place)

### TR3 - agent_bridge.py direct I/O removed

- [ ] `send_to_peer` calls `transport.append_inbox(identity, row)` — NOT direct `append_jsonl`
- [ ] `check_inbox` calls `transport.read_inbox(...)` — NOT direct file open + filter
- [ ] `mark_read` / `mark_seen` / `mark_handled` call `transport.update_receipt(...)`
- [ ] Audit log writes go through `transport.append_audit(...)`
- [ ] `bridge_process_status` reads via `transport.heartbeat()` + `transport.read_presence()`
- [ ] grep verifies no remaining `inbox-*.jsonl` direct writes outside transport layer

### TR4 - server_wrapper.py --transport flag

- [ ] New flag `--transport <local|lan|cloud>`, default `local`
- [ ] `core/transport.py:make_transport(args, paths, auth)` factory routes by flag
- [ ] Existing test fixtures default to `local` and behave unchanged
- [ ] Help text documents flag

### TR5 - Tenant filtering at transport layer

- [ ] No consumer-side filter on tenant_id needed (transport handles)
- [ ] Tests assert: cross-tenant rows in same file are filtered out
- [ ] Strategy A (per-tenant subdirs) supported by `LocalFilesystemTransport.append_inbox` when transport=cloud (forward-compat path)

### TR6 - Forward-compat stub implementations

- [ ] Test fixtures: `StubLANSyncTransport`, `StubAWSCloudTransport` with mocked clients
- [ ] Contract test suite (`TransportContractTests`) runs against all three transports — local, stub-lan, stub-cloud
- [ ] Stubs pass the same scenarios

### TR7 - agent_bridge.py facade size

- [ ] `agent_bridge.py` line count meaningfully smaller after migration; criterion 19 progress
- [ ] Target: facade portion <200 lines (transport bulk lives in `core/transport*.py`)
- [ ] If full <200 not yet hit, audit acknowledges progress + names remaining surface

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_phase0_contract test_transport
```

Expected after TR.5 phase: existing tests + `test_transport.py` ContractTests for all transports green.

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Transport abstraction Phase TR.<N>
ACTION_REQUESTED: none | next-phase
NONCE: audit-transport-<phase>-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage vs TR1-TR7:

| TR# | Status |
|---|---|
| TR1 | DONE / PARTIAL / DEFERRED / MISSING |
| ...up through TR7 |

Code observations: [...]

Facade size progression: <before> -> <after>

Tests at HEAD: <N>

[[handoff:codex]]
```

---

## Coordination

This is the largest Tier-2 refactor by line count. Each phase TR.1-TR.5 audited independently. Final TR.7 audit closes the criterion 19 facade-size target.
