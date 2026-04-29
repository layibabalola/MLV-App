# Audit Profile - Cross-Project Pairing

**Trigger:** Codex's draft `CROSS_PROJECT_PAIRING_SPEC.md` (in flight per
SPEC_REVIEW_REQUEST `09a45981` and my SPEC_REVIEW_RESULT `45e556fd`).

**Audit timing:** at every commit during cross-project pairing
implementation, NOT just at end. Per 2026-04-28 roadmap-shift caveat C2,
cross-project pairing is higher-stakes; baseline checks are NOT deferred
to Phase 14.

---

## Hard gates (any commit failing these gets PUSHBACK, not PASS)

### G1 - Authentication (per SPEC_REVIEW_RESULT 45e556fd addition A)

- [ ] `cross_pair_init` MCP tool requires nonce on BOTH sides within
  60s window
- [ ] Bridge enforces hash-dedup on used nonces (no replay)
- [ ] `cross_pair_init` audit event lands in BOTH projects' messages.jsonl

Test references: A1-A5 in `CROSS_PROJECT_PAIR_TEST_MATRIX.md`.

### G2 - Bidirectional audit (per addition B)

- [ ] Every cross-project read writes audit events to BOTH projects
- [ ] Every cross-project write writes audit events to BOTH projects
- [ ] Audit event includes: timestamp, project rendezvous (both), session
  ids (both), tool call summary, permission level at time of op
- [ ] Compaction in one project preserves cross-project events
  (anti-cleanup measure)

Test references: B1-B4.

### G3 - Source self-promotion rejected (per addition D)

- [ ] `CROSS_PROJECT_PAIR_PROMOTE` only valid when originating session_id
  matches target side
- [ ] Source-side promote attempts return `not_authorized_to_promote`
- [ ] Promotion audit captures originating session_id, not just link id

Test references: D1-D3.

### G4 - Revocation propagation (per addition E)

- [ ] Revoke takes effect within one poll cycle (≤2s)
- [ ] In-flight tool calls return `cross_project_revoked` error
- [ ] Audit captures count of in-flight calls aborted
- [ ] Source agent surfaces revocation in chat

Test references: E1-E4.

### G5 - Multi-link disambiguation (per addition G)

- [ ] Multi-link allowed (project A advisor for Z + project B executor with A)
- [ ] Executor surfaces ALL active links (not just one)
- [ ] Per-link audit tagging (no event leakage between links)
- [ ] `list_cross_project_links` MCP tool exposed

Test references: G1-G4.

---

## Soft gates (PUSHBACK if missing, but PASS-with-followup acceptable)

### S1 - Scope of "read" enumerated (per addition C)

- [ ] Spec must enumerate the read surface for `read_and_advise` tier
- [ ] Default scope: target's worktree only (no parent dir traversal)
- [ ] Bridge state, process state, env vars: NOT readable by default
- [ ] User can request expanded scope explicitly via promotion

### S2 - Naming clarity (per addition F)

- [ ] Spec uses `advisor` / `executor` role-based naming OR documents why
  source/target was kept
- [ ] Error messages avoid ambiguity with bridge's `from`/`to` semantics

### S3 - Test surface (per addition H)

- [ ] Race-condition tests R1-R6 from CROSS_PROJECT_PAIR_TEST_MATRIX
  present
- [ ] If any deferred, documented as known gap with tracking

### S4 - Default values

- [ ] Default permission tier: `read_and_advise`
- [ ] Default expiration: smaller of (idle 30min, source session end,
  target session end, explicit revoke)
- [ ] Default link direction: one-way
- [ ] Receiver acceptance: mandatory, no silent timeout into accept

---

## Failure mode coverage

Cross-reference Codex's draft with the abuse-path catalog in
CROSS_PROJECT_PAIR_TEST_MATRIX.md. Every row in that catalog should map
to a test or be explicitly out of scope.

If a row has no mitigation in Codex's spec, push back.

---

## Per-phase audit cadence

Cross-project pairing will likely ship in phases:

**Phase X.1 - Protocol scaffolding** (control message types, no enforcement)
- G1 + G2 must be DESIGNED in the spec but tests can be `skip` until
  enforcement lands
- Audit looks at: spec doc completeness, control message schema, MCP
  tool signatures

**Phase X.2 - Authentication enforcement** (G1 ENFORCED)
- All A* tests must pass
- AUDIT_RESULT must say `pass` only with all A* green

**Phase X.3 - Audit + permission tier enforcement** (G2 + G3 + S1 ENFORCED)
- All B* + D* tests pass
- AUDIT_RESULT must say `pass` only with these green

**Phase X.4 - Revocation + multi-link** (G4 + G5 ENFORCED)
- E* + G* tests pass

**Phase X.5 - UX polish** (S2 + S3 + S4)
- May ship as PASS-with-followup if minor issues remain

---

## AUDIT_RESULT template (use one per commit)

```
TYPE: AUDIT_RESULT
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Cross-project pairing Phase X.Y - <subscope>
ACTION_REQUESTED: none | fix-followups
NONCE: audit-crossproj-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage:

Hard gates:
- G1 authentication (A1-A5): CHECK | DEFERRED-TO-PHASE-X.Y | MISSING
- G2 audit symmetry (B1-B4): CHECK | DEFERRED | MISSING
- G3 self-promotion rejection (D1-D3): CHECK | DEFERRED | MISSING
- G4 revocation (E1-E4): CHECK | DEFERRED | MISSING
- G5 multi-link (G1-G4): CHECK | DEFERRED | MISSING

Soft gates:
- S1 read scope: CHECK | DEFERRED | MISSING
- S2 naming: CHECK | DEFERRED | MISSING
- S3 race-condition tests: CHECK | DEFERRED | MISSING
- S4 defaults: CHECK | DEFERRED | MISSING

Tests at HEAD: <N> pass.

Failure mode coverage gaps: [list any]

[Push back any hard gate failures; PASS-with-followup if only soft gates
have gaps]

[[handoff:codex]]
```

---

## Coordination model

This is the highest-stakes feature on the bridge roadmap because it
introduces cross-project authority. Per 2026-04-28 roadmap shift, the
hard gates above are NOT deferred to Phase 14 — they're required at
implementation time.

If Codex disagrees with any hard gate, push back via SPEC_REVIEW_RESULT
on the spec, not on the implementation commit.
