# Cross-Project Pair - Test Matrix and Abuse-Path Catalog

**Status:** MVP coverage implemented for manual nonce pairing, read-and-advise
default, executor-only promotion, revoke, list, and message routing. Broader
tier-1 read/write and race/performance cases remain future gates before
cross-project file operations ship.
**Authors:** Claude
**Parent spec:** `tools/agent-bridge/CROSS_PROJECT_PAIRING_SPEC.md`
**Motivation:** concrete tier-1 contract tests that cross-project pairing
must pass at every commit, per the 2026-04-28 roadmap-shift caveat C2
(cross-project pairing is higher-stakes; baseline permission/scope checks
NOT deferred to Phase 14).

---

## Tier-1 contract tests (REQUIRED before any cross-project commit ships)

These map to the 8 additions in my SPEC_REVIEW_RESULT `45e556fd`. Each is
a non-negotiable test that the implementation must pass.

The MVP intentionally ships no cross-project file read/write tools. Implemented
coverage focuses on the authority boundary that exists now:

- manual confirmation is required before any cross-project link can activate
- nonce match, nonce expiry, and replay blocking are covered
- new links default to `read_and_advise`
- advisor self-promotion is rejected; executor write override requires explicit confirmation
- active links are listable; revoked links block future messages
- cross-project messages carry policy metadata in the target project bucket

The remaining rows below are still binding for future read/write surfaces.

### A - Authentication via shared nonce

| ID | Test name | Asserts |
|---|---|---|
| A1 | `test_cross_pair_init_requires_nonce_match` | Source side runs `cross_pair_init` with nonce X; target side runs with nonce Y; bridge refuses to activate the link. |
| A2 | `test_cross_pair_init_nonce_window_60s` | Source runs `cross_pair_init` at T=0; target runs at T=61s with same nonce; bridge refuses (window expired). |
| A3 | `test_cross_pair_init_one_sided_rejected` | Source runs `cross_pair_init`; target never runs; bridge does not activate (no SHARED_NONCE_OBSERVED state). |
| A4 | `test_cross_pair_init_nonce_replay_blocked` | Same nonce used twice → second use rejected. Hash-dedup. |
| A5 | `test_cross_pair_init_audit_records_both_projects` | Successful init writes audit events to BOTH projects' messages.jsonl. |

### B - Bidirectional audit symmetry

| ID | Test name | Asserts |
|---|---|---|
| B1 | `test_cross_project_read_audited_both_sides` | Source reads target's file; both projects' messages.jsonl record the read with project rendezvous + permission level + tool call summary. |
| B2 | `test_cross_project_write_audited_both_sides` | Same for writes (when `write_with_confirmation` is active). |
| B3 | `test_cross_project_audit_includes_permission_level` | Audit event includes the permission tier in effect at time of operation. |
| B4 | `test_compaction_does_not_lose_cross_project_audit` | Run audit-log compaction in one project; cross-project events from the other side are preserved. |

### D - Source self-promotion attempt rejection

| ID | Test name | Asserts |
|---|---|---|
| D1 | `test_source_promotion_request_must_originate_in_target_chat` | Source sends `CROSS_PROJECT_PAIR_PROMOTE` from source-side MCP; bridge rejects with `not_authorized_to_promote`. |
| D2 | `test_target_promotion_succeeds` | Target sends promotion from target-side MCP; bridge accepts and updates link permission tier. |
| D3 | `test_promote_audit_includes_originator_session` | Promotion audit captures the session_id that originated the promote request, not just the link id. |

### E - Revocation propagation

| ID | Test name | Asserts |
|---|---|---|
| E1 | `test_revoke_propagates_within_one_poll_cycle` | Target revokes; both sides drop the link within the next poll cycle (≤2s). |
| E2 | `test_revoke_during_inflight_write_returns_revoke_error` | Source has a write tool call in flight at the moment target revokes; tool returns `cross_project_revoked` error, not silent abort. |
| E3 | `test_revoke_audits_inflight_count` | Audit event includes count of in-flight calls aborted. |
| E4 | `test_source_chat_surfaces_revocation` | Source agent's chat displays "link revoked, cannot continue this task" message. |

### G - Multi-link disambiguation

| ID | Test name | Asserts |
|---|---|---|
| G1 | `test_multi_link_allowed` | Project A is advisor for project Z, AND project B wants A as executor; bridge accepts both links. |
| G2 | `test_executor_chat_lists_all_active_links` | Executor's chat (or `bridge_active_peer` query) returns ALL active cross-project links, not just one. |
| G3 | `test_link_isolation_distinct_audits` | Operations under link A→Z don't leak into link B→A's audit log; events tagged with link id. |
| G4 | `test_list_cross_project_links_mcp_tool_present` | MCP tool exposed for querying active cross-project links from either side. |

### Race conditions and edge cases

| ID | Test name | Asserts |
|---|---|---|
| R1 | `test_simultaneous_accept_reject_user_inputs` | User clicks accept and reject within 100ms of each other; bridge serializes; first decision wins; second is discarded with audit event. |
| R2 | `test_expiration_mid_write` | Write-capable link with 30min idle timeout; idle hits 30min during a write; tool returns `cross_project_expired` error. |
| R3 | `test_target_thread_change_requires_fresh_confirmation` | Target's desktop_thread_id changes (user switched chats); existing link auto-suspends; user re-confirms before resume. |
| R4 | `test_rebootstrap_does_not_silently_restore_write_link` | Source side re-bootstraps; write-capable link is NOT auto-restored; `read_and_advise` MAY auto-restore if both sides still valid. |
| R5 | `test_source_does_not_gain_write_via_init_arg_injection` | Source runs `cross_pair_init` with `permission=full_temporary_delegate`; bridge ignores; default `read_and_advise` applies. Permission elevation only via target promotion. |
| R6 | `test_bridge_root_mismatch_blocks_cross_project_link` | Source and target are running with different bridge_roots; bridge refuses link. |

---

## Failure mode coverage (abuse-path catalog)

Map known abuse patterns to test IDs. If a row has no test ID, it's a gap.

| Abuse pattern | Mitigated by | Test |
|---|---|---|
| Compromised source escalates itself to write | D - source self-promotion rejection | D1 |
| Replay an old `CROSS_PROJECT_PAIR_REQUEST` to re-establish dead link | A - nonce one-shot + window | A4 |
| Hide cross-project activity by compacting source's audit log | B - bidirectional audit | B4 |
| Race between accept and reject | R - serialization | R1 |
| Mid-write revoke leaves stale state | E - inflight abort | E2, E3 |
| Multiple links accumulate silently | G - executor lists all active | G2 |
| Source uses init arg to grant itself permission | R - server-side enforcement | R5 |
| Two bridges accidentally cross-pollinate via shared filesystem | R - root match check | R6 |
| Phase 13 root migration during active link | Bridge migration safety lease (existing) | (covered by `migrate_root.py` tests) |
| Wrong-chat injection of synthetic register message | AUTO_PAIR_SPEC Phase B title-or-uuid verification | (covered by Phase B tests) |

---

## Performance / scaling tests

| ID | Test name | Asserts |
|---|---|---|
| P1 | `test_audit_log_grows_bounded_per_link` | Long-running link with 1000 ops; audit log stays within reasonable size; compaction-safe. |
| P2 | `test_link_setup_latency` | Time from `cross_pair_init` to link active < 5s under normal load. |
| P3 | `test_revoke_latency_p99` | 99th-percentile revoke-to-effect latency < 2s under poll cycle load. |

---

## Implementation gating

Before any cross-project pairing code merges to main:

1. All tier-1 tests (A1-A5, B1-B4, D1-D3, E1-E4, G1-G4) MUST pass.
2. Race-condition tests (R1-R6) SHOULD pass; if any are deferred,
   document the deferral as a known gap with a tracking issue.
3. Performance tests (P1-P3) MAY be deferred to Phase 14, but a smoke
   benchmark MUST exist showing p50 latencies are reasonable.

This list is the contract for tier-1. Phase 14 tail-end review will cover
broader threat modeling not captured here.

---

## Naming conventions

Per my SPEC_REVIEW_RESULT addition F: prefer `advisor` and `executor`
role-based names. Tests above already use these names where applicable
(`test_source_*` should be renamed to `test_advisor_*` once the spec
adopts; `test_target_*` to `test_executor_*`). Source/target preserved in
this matrix for now to match Codex's draft language; fold in advisor/executor
when the spec lands.

[[handoff:codex]]
