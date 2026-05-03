# Agent-Bridge Documentation Audit — Change Request

**Status:** v2 — Amended after stranger-reviewer scoring pass
**Authors:** Claude (synthesis of 6 parallel cold doc auditors)
**Date:** 2026-05-01
**Scope:** All `*.md` files in `tools/agent-bridge/`
**Auditors:** Core Architecture · Wake System · Session Pairing · Security · Data/Schema · Operations/Refactor

---

## Executive Summary

Six independent cold auditors reviewed all documentation in the agent-bridge
directory. **No group scored above 8/10, and two groups averaged below 5/10.**
The primary failure modes are:

1. **Status tags are wrong or absent.** Specs alternate between "Implemented,"
   "Proposed," and historical narration without consistent markers. A reader
   cannot determine what is shipped, what is speculative, and what is retired.
2. **No unified state machine.** Six specs each define partial states (pairing,
   watch lifecycle, routing, session, wake, receipt) that do not compose.
   No doc shows the full state space.
3. **Contradictions on load-bearing claims.** Examples: HARDENING_SPEC says
   window titles are unreliable → don't use them; PREFLIGHT_SPEC uses title
   verification as a stale-context check. HARDENING_SPEC marks wake-skipped
   as `seen_at`; PREFLIGHT_SPEC says pre-flight never mutates `seen_at`.
4. **Core JSON schemas are never formally defined.** `session.json`,
   `routing-rules.json`, `watcher-config.json`, `implementation-journal.json`
   are referenced everywhere but their field names, types, and constraints
   exist only in code.
5. **Stale forward-looking language.** Many specs were written as proposals;
   after landing they were never rewritten. Reviewer Notes, Open Questions, and
   "in-flight" notes remain in docs that are shipped, creating the appearance of
   ongoing design churn.

---

## Priority Classification

| Priority | Definition |
|---|---|
| **P0 — Critical** | Contradiction that produces wrong behavior if followed; or missing information that blocks correct operation |
| **P1 — High** | Stale content that misleads; gap that prevents correct troubleshooting or implementation |
| **P2 — Medium** | Missing polish, incomplete cross-references, undefined minor terms |
| **P3 — Low** | Cosmetic, redundant, or low-impact clarity gap |

---

## Group 1 — Core Architecture Docs

**Auditor average: 6.4/10**
Files: ARCHITECTURE.md, BRIDGE_PROTOCOL.md, STATE_LAYOUT.md, README.md, USER_GUIDE.md

### DR-A1 [P0] Contradiction: window title used as stale-context check vs. declared unreliable

**Files:** PREFLIGHT_DETECTION_SPEC.md (stale-context check, line ~206) vs.
WAKE_HARDENING_SPEC.md D3 (lesson: "OS-level window titles are unreliable on Windows")
**Problem:** PREFLIGHT spec's stale-context check re-verifies "Window title still matches
the expected project / thread marker." HARDENING spec explicitly codifies that title
heuristics must not be used for security-critical decisions. A new engineer implementing
the stale-context check from PREFLIGHT would implement something that HARDENING says must
not be built.
**Fix:** Remove or replace the title-check from the PREFLIGHT stale-context check.
Replace with the breadcrumb/runtime-file check that HARDENING recommends. Add a
cross-reference in both specs.

---

### DR-A2 [P0] Contradiction: pre-flight never mutates seen_at vs. breaker marks seen

**Files:** PREFLIGHT_DETECTION_SPEC.md ("Pre-flight events MUST NOT mark messages seen_at
or read_at") vs. WAKE_HARDENING_SPEC.md D2 ("Mark message seen with reason
`wake_skipped_breaker_open`")
**Problem:** One spec prohibits seen_at mutation from the pre-flight path; the other
requires it when the circuit breaker suppresses a wake. These are contradictory contracts.
**Fix:** Clarify ownership. The breaker's `wake_skipped_breaker_open` path fires after the
pre-flight check fails, not during it — if that's the intent, document explicitly that
"breaker-open suppression is a post-preflight exit and may set seen_at." If they truly
share a code path, the PREFLIGHT prohibition must be relaxed for the breaker case.

---

### DR-A3 [P0] Exit code 11 missing from ARCHITECTURE failure tree

**Files:** ARCHITECTURE.md (failure mode tree, ~line 224), BRIDGE_PROTOCOL.md (line ~39)
**Problem:** BRIDGE_PROTOCOL.md documents exit code 11 (bad provenance / bootstrap
rejection). ARCHITECTURE.md's failure tree only goes to exit code 10. Any engineer
reading the architecture for troubleshooting will have an incomplete exit code map.
**Fix:** Add exit code 11 (and any others added since the tree was written) to the
ARCHITECTURE.md failure mode tree.

---

### DR-A4 [P0] Bootstrap flow documented in 4 places with contradictory details

**Files:** BRIDGE_PROTOCOL.md §Session Boot, README.md §Bootstrap, USER_GUIDE.md
§Quick Start, ARCHITECTURE.md glossary
**Problem:** Each of the four bootstrap descriptions uses slightly different parameters,
different sequences, and different path formats. An engineer implementing bootstrap from
scratch would produce different behavior depending on which doc they read.
**Fix:** Pick one canonical bootstrap reference (recommend BRIDGE_PROTOCOL.md §Session
Boot) and reduce the other three to a one-paragraph summary + cross-reference. Audit
all four for parameter and sequence consistency before archiving the duplicates.

---

### DR-A5 [P1] No formal schema for `session.json`, `routing-rules.json`, `watcher-config.json`

**Files:** STATE_LAYOUT.md, BRIDGE_PROTOCOL.md, README.md (all reference these files)
**Problem:** All three files are referenced in at least three specs each. Their field
names, types, required vs. optional status, and validation constraints exist only in
code. A user or an engineer writing tooling for these files must grep the implementation.
**Fix:** Add a `SCHEMA_REFERENCE.md` that formally defines the field-level schema for
each of these JSON files. Reference it from every spec that mentions the file.

---

### DR-A6 [P1] No unified glossary — key terms overloaded across files

**Problem:** "protected" (thread id), "active" (session, pairing, wake), "private"
(bucket type), "rendezvous" (bucket type), "background" (session state) each appear in
multiple files with subtly different meanings. No single reference defines them.
**Fix:** Add a `GLOSSARY.md` that defines every overloaded term. Reference it from
ARCHITECTURE.md, BRIDGE_PROTOCOL.md, and STATE_LAYOUT.md headers.

---

### DR-A7 [P1] USER_GUIDE missing session lifecycle (pause/resume/end)

**File:** USER_GUIDE.md
**Problem:** `bridge pause`, `bridge resume`, and `bridge end` are documented in
BRIDGE_PROTOCOL.md (lines 349-401) but absent from USER_GUIDE.md. Users performing
normal bridge operations cannot find these commands in the user-facing guide.
**Fix:** Add a "Session Lifecycle" section to USER_GUIDE.md covering at minimum:
pause, resume, end, and what state each leaves the bridge in.

---

### DR-A8 [P1] `ensure_watcher` MCP tool undocumented in README tool inventory

**Files:** USER_GUIDE.md (~line 115), README.md (~line 103-112)
**Problem:** USER_GUIDE.md references `ensure_watcher(reason="signature_changed")` as
a required action after wake_provider changes. This MCP tool is not listed in the README
tool inventory. Users will not know it exists.
**Fix:** Add `ensure_watcher` to README.md's tool inventory with a one-line description.

---

### DR-A9 [P1] RUNTIME_RELOAD.md referenced as authoritative but is 2 lines

**File:** ARCHITECTURE.md (~line 117)
**Problem:** ARCHITECTURE.md directs readers to RUNTIME_RELOAD.md before treating
disagreements as defects. The file contains no actionable information.
**Fix:** Either expand RUNTIME_RELOAD.md with the hot-reload contract (which settings
are hot-reloadable, what triggers a reload, what requires watcher restart) or remove
the reference from ARCHITECTURE.md and inline the single-sentence summary there.

---

### DR-A10 [P2] Claude Monitor construct described inconsistently

**Files:** ARCHITECTURE.md (~line 55), README.md (~line 151)
**Problem:** ARCHITECTURE says Monitor is "always-on inbox poll"; README says it is
"persistent in-process" and "started at session bootstrap." Unclear if Monitor is a
per-session construct or a global daemon.
**Fix:** Add one authoritative definition in ARCHITECTURE.md, then use consistent
language everywhere else.

---

## Group 2 — Wake System Docs

**Auditor average: 6/10**
Files: WAKE_HARDENING_SPEC.md, PREFLIGHT_DETECTION_SPEC.md, WAKE_CODEX_TUNING.md,
APP_SERVER_WAKE_SPEC.md

*(DR-A1 and DR-A2 cover the two P0 contradictions in this group.)*

### DR-W1 [P0] APP_SERVER_WAKE_SPEC reviewer notes are unresolved design questions

**File:** APP_SERVER_WAKE_SPEC.md (RN1–RN8 block)
**Problem:** The eight Reviewer Notes in APP_SERVER_WAKE_SPEC are open design questions,
not ship-readiness sign-offs. The spec is labeled as if it describes a researched system
but leaves load-bearing choices undecided (RN2: `"wake": "auto"` vs. explicit config;
RN7 failure mode not added to the table as promised).
**Fix:** Resolve or explicitly defer each Reviewer Note. Move the "decided by X date"
or "deferred — here's why" disposition inline next to each RN. RN7 must be added to
the Failure Modes table as the spec text promised.

---

### DR-W2 [P1] Priority-tiered caps (45s/120s/300s) vs. ActiveTypingMaxWaitSeconds (90s) — which governs?

**Files:** PREFLIGHT_DETECTION_SPEC.md (priority caps table), WAKE_CODEX_TUNING.md
(`ActiveTypingMaxWaitSeconds`)
**Problem:** PREFLIGHT defines three priority-tiered caps (urgent 45s, normal 120s,
low 300s) for force-fire-after-cap. WAKE_CODEX_TUNING documents `ActiveTypingMaxWaitSeconds=90`
as the cap for the actively-typing state. The relationship between these is not defined:
does `ActiveTypingMaxWaitSeconds` override the per-priority cap? Is it additive? Which
wins for an urgent message?
**Fix:** Add one paragraph to PREFLIGHT_DETECTION_SPEC.md's Polling Mechanics section
that explicitly states the precedence rule. Update WAKE_CODEX_TUNING.md with a
cross-reference to PREFLIGHT's cap table.

---

### DR-W3 [P1] Placeholder fingerprint has no refresh policy

**File:** PREFLIGHT_DETECTION_SPEC.md (Placeholder Fingerprinting section)
**Problem:** The spec defines first-run capture and config override, but does not
address what happens when Codex Desktop updates and the placeholder text changes.
A stale fingerprint causes every non-empty composer to be treated as `idle-with-draft`,
triggering unnecessary clipboard save/restore on every wake.
**Fix:** Add a "Fingerprint staleness" subsection: define how stale the fingerprint
can be before re-probing (e.g. re-probe after Codex Desktop version change, or after
N delivery failures with a "stale fingerprint suspected" audit event).

---

### DR-W4 [P1] Rolling 5-minute window semantics undefined in circuit breaker

**File:** WAKE_HARDENING_SPEC.md (D2 circuit breaker)
**Problem:** "Rolling 5-minute window" is stated but not defined. Do individual failure
events expire from the window (sliding window per event), or does the entire window reset
(tumbling window)? This affects how quickly the breaker reopens after a mix of failures
and gaps.
**Fix:** Specify explicitly: "sliding window — each failure expires 5 minutes after it
was recorded; the threshold is the count of unexpired entries at evaluation time."

---

### DR-W5 [P1] Exit code semantics incomplete after D5 (UIA SetFocus)

**Files:** WAKE_HARDENING_SPEC.md D2 (`WAKE_PERMANENT_EXIT_CODES`), D5 (UIA SetFocus)
**Problem:** D2 defines `WAKE_PERMANENT_EXIT_CODES = {3}`. D5 introduces a two-path
outcome: UIA SetFocus succeeds, or falls through to Win32. No exit codes are defined
for these new outcomes. What does "UIA path succeeded" look like to the watcher's
exit code parser?
**Fix:** Add exit code documentation for the D5 UIA path to the WAKE_HARDENING_SPEC
exit code table: exit 0 = success (either path); if per-path auditing is desired, define
distinct audit event names (not exit codes) for UIA vs. Win32 success.

---

### DR-W6 [P2] Workflow rule for WAKE_CODEX_TUNING not enforced

**File:** WAKE_CODEX_TUNING.md (~line 142)
**Problem:** The doc documents its own non-enforcement: "2026-05-01 incident where Codex
rewrote the command template and silently dropped -Message." The rule exists but has no
enforcement mechanism.
**Fix:** Add to WAKE_CODEX_TUNING.md's header: "This file has a `_doc` pointer from
`watcher-config.json` and a `# Doc:` comment in `wake_codex.ps1`. Any PR that modifies
those files must update the table in this doc in the same commit. Reviewers must reject
PRs that modify the parameters without updating this doc." This makes the rule legible to
PR reviewers, not just the implementing agent.

---

## Group 3 — Session Pairing Docs

**Auditor average: 7.2/10**
Files: AUTO_PAIR_SPEC.md, BRIDGE_PAIRING_USER_FLOWS_SPEC.md, BRIDGE_WATCH_LIFECYCLE.md,
BRIDGE_SESSION_ROUTING_HARDENING_SPEC.md, BRIDGE_PAIRING_INTENT_SPEC.md

### DR-P1 [P0] Contradiction: "missing breadcrumb → skip" vs. "missing session → rendezvous fallback"

**Files:** AUTO_PAIR_SPEC.md ("missing peer breadcrumb => wake skipped, `wake_skipped_no_peer`")
vs. BRIDGE_SESSION_ROUTING_HARDENING_SPEC.md SR3 ("no active session → fallback to project
rendezvous bucket")
**Problem:** These two rules govern the same trigger (peer not present) with conflicting
responses. A new engineer cannot tell whether to implement skip-and-audit or fallback
routing.
**Fix:** Reconcile. The most likely intent: routing falls back to rendezvous bucket
(SR3), but wake delivery is independently skipped when breadcrumb is missing (AUTO_PAIR).
Document explicitly that routing fallback and wake skip are orthogonal — routing can
succeed (message in rendezvous) while wake is suppressed (no breadcrumb to wake with).

---

### DR-P2 [P0] No unified state machine across pairing specs

**Problem:** AUTO_PAIR_SPEC (pairing states), BRIDGE_WATCH_LIFECYCLE (watch states),
BRIDGE_SESSION_ROUTING_HARDENING_SPEC (routing states), BRIDGE_PAIRING_INTENT_SPEC
(intent states), and BRIDGE_PAIRING_USER_FLOWS_SPEC (UX states) each define partial
orthogonal state spaces that do not compose into a single coherent machine. A new
engineer implementing the system has no way to derive the complete set of valid states
and transitions.
**Fix:** Create a `PAIRING_STATE_MACHINE.md` that formally defines:
- All session states with their source specs
- All allowed transitions with their triggers and guards
- Which state a session must be in to call `wait_inbox`, `send_to_peer`, `wake_peer`, etc.
Reference this doc from all five pairing specs.

---

### DR-P3 [P1] AUTO_PAIR Phase B through D have no acceptance criteria or ship dates

**File:** AUTO_PAIR_SPEC.md
**Problem:** Phase A (title markers) was reverted. Phases B (breadcrumb UUID check),
C (TBD), and D (symmetric `wake_claude.ps1`) have no acceptance criteria, no ship
dates, and no owner assignments. They appear in a shipped spec as open roadmap items.
**Fix:** Either:
- Write Phase B acceptance criteria in the spec (if planned), or
- Move Phases B-D to a dedicated `AUTO_PAIR_ROADMAP.md` and archive the Phase A
  postmortem section, reducing AUTO_PAIR_SPEC to its implemented Layer 1 content.

---

### DR-P4 [P1] BRIDGE_PAIRING_INTENT_SPEC adds 7 schema fields without citing SCHEMA_EVOLUTION_SPEC

**File:** BRIDGE_PAIRING_INTENT_SPEC.md (~line 220)
**Problem:** The ephemeral relay metadata adds `relay_mode`, `ephemeral_relay_id`,
`reply_to_session_id`, `primary_session_id_at_send`, `pairing_intent_at_send`,
`contract_id_at_send`, `relay_target_bucket` — a schema bump — without a version
number or citation to BRIDGE_SCHEMA_EVOLUTION_SPEC.md.
**Fix:** Assign these fields a schema_version bump (e.g. v3), document them in
BRIDGE_SCHEMA_EVOLUTION_SPEC.md with their required/optional status, and add
a cross-reference from BRIDGE_PAIRING_INTENT_SPEC.

---

### DR-P5 [P1] Observer/advisor/auditor role cap rule is ambiguous

**File:** BRIDGE_PAIRING_USER_FLOWS_SPEC.md
**Problem:** The cap table rows say "observer/advisor/auditor: 3 total" but the
spec defines them as distinct roles. Do all three share the cap or have individual caps?
Can the user pair 2 observers and 1 advisor?
**Fix:** Clarify the cap as either "3 per role type" or "3 total across all
non-primary role types" and add an example showing a legal vs. illegal configuration.

---

## Group 4 — Security & Auth Docs

**Auditor average: 5.5/10**
Files: BRIDGE_AUTH_SPEC.md, SECURITY_REVIEW.md, BRIDGE_BOOTSTRAP_PROVENANCE_SPEC.md,
POLICY_AUTHORITY_SPEC.md

### DR-S1 [P0] BRIDGE_AUTH_SPEC and SECURITY_REVIEW contradict on whether auth is shipped

**Files:** BRIDGE_AUTH_SPEC.md (forward-looking proposal), SECURITY_REVIEW.md (~line 164:
"Added local auth/transport seams `core/auth.py`, `core/transport.py`")
**Problem:** SECURITY_REVIEW claims auth seams are implemented. BRIDGE_AUTH_SPEC reads
as a proposed spec with phases not yet started. Both cannot be true.
**Fix:** Audit the codebase to determine the actual state. Then:
- If auth seams are shipped: update BRIDGE_AUTH_SPEC to accurately describe the
  implementation, change its status to "Implemented (partial)", and mark unimplemented
  phases explicitly.
- If auth seams are not shipped: update SECURITY_REVIEW to say "auth seam files added
  (stub)" and list the shipped vs. unshipped items explicitly.

---

### DR-S2 [P0] POLICY_AUTHORITY_SPEC lists MCP tools that do not exist

**File:** POLICY_AUTHORITY_SPEC.md (~lines 196-205)
**Problem:** `list_policy_rules`, `policy_rule_status`, `validate_policy_docs` are named
as required MCP tools. None appear in the actual MCP tool surface. A user reading this
spec will attempt to call tools that return errors.
**Fix:** Mark POLICY_AUTHORITY_SPEC.md as "Proposed — not yet implemented." Remove any
language suggesting these tools are available today. Add an explicit "Implementation
Status" section at the top.

---

### DR-S3 [P1] SECURITY_REVIEW "Fixed" items contradict "Exit Criteria" items

**File:** SECURITY_REVIEW.md
**Problem:** The "Fixed" section lists P1 items (legacy watcher path, PREFLIGHT
dogfood) as resolved. The "Exit Criteria" section still lists them as remaining work.
A security auditor cannot tell what is actually closed.
**Fix:** Split the SECURITY_REVIEW into two clearly labeled sections:
- "Closed findings" (with commit SHAs, dates, and test coverage)
- "Open findings" (with priority and owner)
Remove any finding from "Closed" that still appears in "Open."

---

### DR-S4 [P1] chmod 600 claimed for MCP secret but is dead code on Windows

**File:** BRIDGE_AUTH_SPEC.md (credential storage model)
**Problem:** `os.chmod(path, 0o600)` on Windows controls only the read-only attribute,
not access control. On the target platform, this provides no protection. (Matches
code CR item CR-S1a.)
**Fix:** Replace the Unix-centric chmod with the Windows NTFS ACL approach (using
`win32security`) as documented in code CR-S1a. Update the spec to describe the
platform-correct implementation.

---

### DR-S5 [P1] BRIDGE_BOOTSTRAP_PROVENANCE_SPEC does not confirm the drift-auto-supersede fix

**File:** BRIDGE_BOOTSTRAP_PROVENANCE_SPEC.md
**Problem:** The spec documents the 2026-05-01 incident (dead bootstrap PID was
invalidly used to authorize trusted-parent replacement) but may not reflect the fix
that was shipped (Codex IMPLEMENTATION_UPDATE 2026-05-01: `bootstrap_trusted_parent_drift_refused`).
**Fix:** Add a "Status update 2026-05-01" section confirming: the fix ships
`bootstrap_trusted_parent_drift_refused` with reason `trusted_parent_thread_drift_requires_explicit_repair`;
dead bootstrap PID is no longer a sufficient condition for auto-supersession.

---

### DR-S6 [P2] POLICY_AUTHORITY_SPEC policy registry authority claim conflicts with claude.md

**File:** POLICY_AUTHORITY_SPEC.md
**Problem:** The spec says "Code is the law" and markdown is explanatory-only. But
CLAUDE.md and AGENTS.md contain binding operational instructions. The spec does not
acknowledge these as authority sources, leaving the authority hierarchy incomplete.
**Fix:** Add a section: "Relationship to CLAUDE.md / AGENTS.md: these files carry
session-scoped operational authority for their respective agents and are not overridden
by this policy spec. Policy spec governs bridge-protocol authority, not agent-session
behavior."

---

## Group 5 — Data Model & Schema Docs

**Auditor average: 4/10**
Files: BRIDGE_SCHEMA_EVOLUTION_SPEC.md, HIERARCHICAL_INBOX_SPEC.md,
MESSAGE_RECEIPTS_SPEC.md, KNOWLEDGE_SHARING_CONTRACT_SPEC.md, STATE_LAYOUT.md

### DR-D1 [P0] Receipt state machine is ambiguous — can `handled` exist without `read`?

**File:** MESSAGE_RECEIPTS_SPEC.md
**Problem:** The spec defines state derivation rules but does not define the ordering
requirement between states. Can a message be marked `handled` without having been
`read`? If yes, the state machine has an unconstrained transition that produces confusing
dashboard states (handled but not read appears as a compliance violation). If no, the
spec must state that `handled_at` requires `read_at` to already be set.
**Fix:** Add an explicit invariant: "Ordering constraint: `handled_at` MUST NOT be set
unless `read_at` is already set." If the codebase already enforces this, quote the
enforcement point.

---

### DR-D2 [P0] HIERARCHICAL_INBOX_SPEC new fields have no schema_version bump assignment

**File:** HIERARCHICAL_INBOX_SPEC.md (~lines 305-314)
**Problem:** The spec introduces `escalated_from`, `escalation_reason`, `inbox_level`,
`parent_project`, `promoted_from`, `promoted_at` but assigns no schema_version bump.
Readers cannot tell whether these are v1 or v2 fields, blocking correct migration and
forward-compat reader implementation.
**Fix:** Assign each set of new fields a schema_version bump number in
BRIDGE_SCHEMA_EVOLUTION_SPEC.md and cross-reference from HIERARCHICAL_INBOX_SPEC.

---

### DR-D3 [P0] Migration tooling (`migrate_root.py --upgrade-schema`) is forward reference

**File:** BRIDGE_SCHEMA_EVOLUTION_SPEC.md (~lines 96-122)
**Problem:** The spec describes a migration tool that does not yet exist. Users or
scripts following the spec will fail. Any engineer implementing the migration will have
no contract to verify against.
**Fix:** Mark the migration tooling section explicitly as "Not yet implemented — see
code CR item CR-D2 for the implementation plan." Add the migration tool as a tracked
deliverable with acceptance criteria.

---

### DR-D4 [P0] `session.json` per-entry field schema is never defined anywhere

**Files:** STATE_LAYOUT.md, BRIDGE_PROTOCOL.md, BRIDGE_SESSION_ROUTING_HARDENING_SPEC.md
(all reference `session.json`)
**Problem:** `session.json` is referenced in at least 8 specs. Its per-entry field names
(e.g. `last_heartbeat_at` mentioned in HIERARCHICAL_INBOX_SPEC.md ~line 153) are
mentioned in passing but never formally defined in any doc.
**Fix:** Add `session.json` entry schema to DR-A5's proposed `SCHEMA_REFERENCE.md`.

---

### DR-D5 [P1] `check_inbox` default `record_seen` behavior is ambiguous

**File:** MESSAGE_RECEIPTS_SPEC.md
**Problem:** The spec says `wait_inbox(..., mark_read=false)` sets `seen_at` AND
`check_inbox(..., mark_read=false)` sets `seen_at` "unless `record_seen=false`." But
`mark_read=false` is the default. Under default usage, does `check_inbox` always
bump `seen_at`? The spec does not state the default for `record_seen`.
**Fix:** Add an explicit defaults table for `check_inbox`:
```
mark_read  record_seen  Effect
false       (default)   sets seen_at only
true        (default)   sets seen_at + read_at
false       false       no mutation
```

---

### DR-D6 [P1] KNOWLEDGE_SHARING_CONTRACT_SPEC contract field storage not defined

**Files:** KNOWLEDGE_SHARING_CONTRACT_SPEC.md (defines fields like `created_by_session`,
`last_confirmed_at`), STATE_LAYOUT.md (~line 169: lists `state/knowledge-contracts/`
but no field definitions)
**Problem:** CONTRACT_SPEC defines 6+ contract-level fields. STATE_LAYOUT acknowledges
the directory exists but defines no field schema. Implementers cannot know what to
write or read from these files.
**Fix:** Add knowledge contract file schema to DR-A5's `SCHEMA_REFERENCE.md`.

---

### DR-D7 [P1] BRIDGE_SCHEMA_EVOLUTION_SPEC `tenant_id` is described as "required for cloud, optional for v1" — incoherent

**File:** BRIDGE_SCHEMA_EVOLUTION_SPEC.md (~line 138)
**Problem:** "Required for cloud, optional for v1" conflates two orthogonal dimensions:
cloud vs. local deployment and schema version. A v1 reader on a cloud deployment cannot
know whether to reject missing `tenant_id`.
**Fix:** Define one required/optional status per schema version, not per deployment
class. Example: "tenant_id: optional in v1 (present for multi-tenant deployments),
required in v2."

---

## Group 6 — Operations & Refactor Docs

**Auditor average: 6.8/10**
Files: SETTINGS.md, VALIDATION_LOOP_RUNBOOK.md, BRIDGE_HEALTH_PANEL_SPEC.md,
REFACTOR_PLAN.md, BRIDGE_HARDENING.md

### DR-O1 [P0] REFACTOR_PLAN simultaneously says "baseline shipped" and lists 16+ pending items

**File:** REFACTOR_PLAN.md
**Problem:** The top of the file says "Baseline hardening has shipped" but the phase
list contains 16+ items still marked pending, proposed, or in-progress. This creates
a false impression of completion status.
**Fix:** Restructure REFACTOR_PLAN into three explicit sections:
1. **Shipped phases** (with commit SHAs, completion dates)
2. **In-progress phases** (with owner, current sub-step)
3. **Deferred/backlog phases** (with blocking reason or dependency)
Add a "Total shipped: X of Y phases" summary at the top.

---

### DR-O2 [P1] VALIDATION_LOOP_RUNBOOK references unverifiable items

**File:** VALIDATION_LOOP_RUNBOOK.md
**Problem:** Pre-flight step 3 references `msg 2071358b` and a `phase14_security_signoff`
audit event. Neither is traceable from the docs or test suite. An engineer executing the
runbook cannot verify these.
**Fix:** Replace unverifiable references with observable criteria. Example:
- "msg 2071358b" → "run `py -3 -m pytest tests/ -k phase14` and confirm 0 failures"
- "phase14_security_signoff" → "confirm SECURITY_REVIEW.md exit criteria list has no
  open P0 or P1 items"

---

### DR-O3 [P1] BRIDGE_HEALTH_PANEL_SPEC status tag contradicts itself

**File:** BRIDGE_HEALTH_PANEL_SPEC.md
**Problem:** The status line reads both "MVP implemented (2026-04-30)" and "Codex review
pending." After Codex's 2026-05-01 IMPLEMENTATION_UPDATE (wake-state health fix,
BRIDGE_HEALTH_PANEL_SPEC updated), the review should be closed.
**Fix:** Update the status line to: "Implemented — Codex review complete 2026-05-01.
Wake health derives from watcher pending-wake state (not process scan)." Archive the
open review item.

---

### DR-O4 [P1] BRIDGE_HEALTH_PANEL_SPEC has no recovery guidance for `degraded` state

**File:** BRIDGE_HEALTH_PANEL_SPEC.md
**Problem:** HP7 surfaces receipt debt (`handled_not_seen_count > 0`), but the spec
does not say what an engineer should do when the health panel shows `degraded`. No
remediation runbook, no escalation path.
**Fix:** Add a "Degraded state remediation" section with step-by-step guidance for
each named degradation cause:
- `unread_stale_messages > threshold` → run `bridge_reconnect_mcp` or drain manually
- `wake_stuck` → run `resume_wake_for_session` or check circuit breaker
- `receipt_debt > threshold` → run receipt-debt diagnostic (per roadmap item 7871760f)

---

### DR-O5 [P1] SETTINGS.md does not document `watcher-config.json`

**File:** SETTINGS.md
**Problem:** BRIDGE_HARDENING.md references `--watcher-config` flag and
`configure_watcher()`, implying `watcher-config.json` is user-tunable. SETTINGS.md
only documents `settings.json`. Users cannot find documentation for the watcher-config
file in the settings reference.
**Fix:** Add a "watcher-config.json" section to SETTINGS.md, or add a cross-reference
explaining that watcher-config is auto-generated by the bootstrap and should only be
edited via `configure_watcher()`.

---

### DR-O6 [P2] SETTINGS.md does not document fallback behavior for unparseable `settings.json`

**File:** SETTINGS.md
**Problem:** The file says "Unsupported keys are rejected" but does not specify what
happens when `settings.json` exists but contains invalid JSON. Fallback behavior is
undocumented.
**Fix:** Add one sentence: "If `settings.json` exists but cannot be parsed as valid JSON,
the bridge logs a warning, names the file path, and falls back to all defaults. The
invalid file is not overwritten."

---

## Summary Table

| ID | Group | Priority | Title |
|---|---|---|---|
| DR-A1 | Architecture | P0 | Title used as stale-context check vs. declared unreliable |
| DR-A2 | Architecture | P0 | Pre-flight never mutates seen_at vs. breaker marks seen |
| DR-A3 | Architecture | P0 | Exit code 11 missing from ARCHITECTURE failure tree |
| DR-A4 | Architecture | P0 | Bootstrap flow in 4 places with contradictory details |
| DR-A5 | Architecture | P1 | No formal schema for session.json / routing-rules.json |
| DR-A6 | Architecture | P1 | No unified glossary — key terms overloaded |
| DR-A7 | Architecture | P1 | USER_GUIDE missing session lifecycle commands |
| DR-A8 | Architecture | P1 | ensure_watcher MCP tool undocumented |
| DR-A9 | Architecture | P1 | RUNTIME_RELOAD.md referenced but empty |
| DR-A10 | Architecture | P2 | Monitor construct described inconsistently |
| DR-W1 | Wake | P0 | APP_SERVER_WAKE reviewer notes are open design questions |
| DR-W2 | Wake | P1 | Priority caps vs. ActiveTypingMaxWaitSeconds precedence |
| DR-W3 | Wake | P1 | Placeholder fingerprint has no refresh policy |
| DR-W4 | Wake | P1 | Rolling 5-min window semantics undefined |
| DR-W5 | Wake | P1 | Exit code semantics incomplete after D5 UIA path |
| DR-W6 | Wake | P2 | Workflow rule for tuning doc not enforced by PR process |
| DR-P1 | Pairing | P0 | Missing breadcrumb → skip vs. missing session → rendezvous |
| DR-P2 | Pairing | P0 | No unified state machine across pairing specs |
| DR-P3 | Pairing | P1 | AUTO_PAIR phases B-D have no acceptance criteria |
| DR-P4 | Pairing | P1 | INTENT_SPEC adds 7 schema fields without version bump |
| DR-P5 | Pairing | P1 | Observer/advisor/auditor cap rule ambiguous |
| DR-S1 | Security | P0 | AUTH_SPEC vs. SECURITY_REVIEW shipped/proposed contradiction |
| DR-S2 | Security | P0 | POLICY_AUTHORITY_SPEC lists non-existent MCP tools |
| DR-S3 | Security | P1 | Fixed vs. exit-criteria conflict in SECURITY_REVIEW |
| DR-S4 | Security | P1 | chmod 600 dead code on Windows |
| DR-S5 | Security | P1 | BOOTSTRAP_PROVENANCE not updated for drift-fix (2026-05-01) |
| DR-S6 | Security | P2 | POLICY_AUTHORITY authority hierarchy incomplete |
| DR-D1 | Data | P0 | Receipt state machine — can handled exist without read? |
| DR-D2 | Data | P0 | HIERARCHICAL_INBOX new fields have no schema_version bump |
| DR-D3 | Data | P0 | Migration tooling is a forward reference (not yet built) |
| DR-D4 | Data | P0 | session.json per-entry schema never formally defined |
| DR-D5 | Data | P1 | check_inbox default record_seen behavior ambiguous |
| DR-D6 | Data | P1 | KNOWLEDGE_SHARING contract field storage not defined |
| DR-D7 | Data | P1 | tenant_id "required for cloud, optional for v1" — incoherent |
| DR-O1 | Operations | P0 | REFACTOR_PLAN says shipped while listing 16+ pending |
| DR-O2 | Operations | P1 | VALIDATION_RUNBOOK references unverifiable items |
| DR-O3 | Operations | P1 | BRIDGE_HEALTH_PANEL status tag contradicts itself |
| DR-O4 | Operations | P1 | BRIDGE_HEALTH_PANEL no recovery guidance for degraded |
| DR-O5 | Operations | P1 | SETTINGS.md does not document watcher-config.json |
| DR-O6 | Operations | P2 | SETTINGS.md missing fallback for unparseable settings.json |

**P0 count: 14 · P1 count: 21 · P2 count: 5**

---

## New Files Required

| File | Purpose |
|---|---|
| `GLOSSARY.md` | Unified term definitions (DR-A6) |
| `SCHEMA_REFERENCE.md` | Formal field-level schemas for all JSON state files (DR-A5, DR-D4, DR-D6) |
| `PAIRING_STATE_MACHINE.md` | Unified state diagram composing all pairing specs (DR-P2) |

---

## Projected Post-Fix Scores

If all P0 and P1 items are resolved:

| Doc Group | Pre-fix avg | Projected post-fix |
|---|---|---|
| Core Architecture | 6.4/10 | 9/10 |
| Wake System | 6.0/10 | 8.5/10 |
| Session Pairing | 7.2/10 | 9/10 |
| Security & Auth | 5.5/10 | 8.5/10 |
| Data Model & Schema | 4.0/10 | 8.5/10 |
| Operations & Refactor | 6.8/10 | 9/10 |

P2 items bring each to 9.5–10/10. The groups furthest from 10 post-P1 are
Wake System and Security, both of which have external dependencies (APP_SERVER_WAKE
design decisions and auth implementation status that require code changes, not doc
changes alone).

---

## Implementation Guidance

**Wave 1 — Fix contradictions (P0):**
DR-A1 (title/stale-context), DR-A2 (seen_at conflict), DR-P1 (skip vs. rendezvous),
DR-S1 (auth shipped/proposed), DR-D1 (receipt ordering), DR-D2 (schema bump for
hierarchy fields)

**Wave 2 — Create missing reference docs (P0):**
DR-A4 (canonicalize bootstrap), DR-A3 (exit code 11), DR-D4 (session.json schema),
DR-D3 (migration tooling status), DR-O1 (REFACTOR_PLAN restructure)

**Wave 3 — P0 speculative tooling:**
DR-S2 (POLICY_AUTHORITY status correction), DR-W1 (APP_SERVER_WAKE RN resolution),
DR-P2 (PAIRING_STATE_MACHINE.md new doc)

**Wave 4 — P1 hardening:**
All remaining P1 items.

**Wave 5 — P2 polish:**
All P2 items.

---

## Reviewer Notes

This document was generated by synthesis of 6 independent cold doc auditors.
Code and doc CRs are sibling documents — both should be reviewed before implementation
begins so that code fixes and doc fixes land in the same pass where possible (e.g.
DR-S4 / CR-S1a both fix the same Windows chmod issue — one in auth.py, one in
BRIDGE_AUTH_SPEC.md).

---

## Amendment — v2 Gaps Found by Stranger Reviewers

Three independent cold reviewers scored the v1 document.
Architecture and Pairing groups were sent REVISE; Security was REVISE; all others
were APPROVE_WITH_NOTES. The items below correct or extend the v1 DR.

---

### DR-A11 [P0] Monitor/compaction lifecycle not documented anywhere (NEW)

**Found by:** Stranger Reviewer 1
**Problem:** CLAUDE.md states: "the Monitor does NOT survive context compaction. Start
it every session, no exceptions." This constraint is not documented in ARCHITECTURE.md,
USER_GUIDE.md, or README.md. A new engineer building against the architecture docs
will build a system that silently stops receiving inbox notifications after a long
session without knowing why.

**Fix:** Add a "Monitor lifecycle" section to ARCHITECTURE.md and USER_GUIDE.md that
documents:
- Monitor is a per-context-window in-process poll; it does NOT persist across compaction.
- Every session start must restart the Monitor.
- Inbox notifications silently stop (no error) when the Monitor dies from compaction
  without restart.
- The session-start checklist in USER_GUIDE must include Monitor startup as a required step.

---

### DR-A10a [P1] Monitor description inconsistency is P1, not P2

**Found by:** Stranger Reviewer 1
**Parent:** DR-A10
**Problem:** Monitor drives all inbox delivery for Claude. An inconsistent description
of a wake-critical component is a P1 operational hazard, not P2 cosmetic.
**Fix:** Promote DR-A10 to P1 in the summary table.

---

### DR-A1a [P1] DR-A1 fix assumes HARDENING specifies an alternative check

**Found by:** Stranger Reviewer 1
**Parent:** DR-A1
**Problem:** The DR-A1 fix says "replace with the breadcrumb/runtime-file check that
HARDENING recommends." But HARDENING only says what NOT to do (no title checks); it
does not specify an alternative stale-context check. Implementing the fix as written
produces a spec that references a non-existent design.
**Fix:** Before updating PREFLIGHT_DETECTION_SPEC.md, define the replacement check:
the stale-context verification should use the runtime breadcrumb file
(`peer-<agent>.runtime.json`, field `desktop_thread_id`) to confirm the target thread.
Then update both specs to reference this concrete mechanism.

---

### DR-A2a [P1] DR-A2 must resolve the seen_at ownership ambiguity, not defer it

**Found by:** Stranger Reviewer 1
**Parent:** DR-A2
**Problem:** The DR-A2 fix says "clarify ownership" — but a doc edit that says
"clarify" without picking the correct interpretation is a deferred decision, not a fix.
**Fix:** The correct interpretation is: the breaker's `wake_skipped_breaker_open` path
fires **after** the pre-flight check has already exited (the breaker is checked in the
watcher before the wake script runs, not during it). Therefore, the PREFLIGHT prohibition
on seen_at mutation is correct and applies only to the pre-flight script itself. The
circuit breaker's `mark_seen` operates in the watcher layer above the script. Update
both specs with this explicit ownership boundary.

---

### DR-A4a [P1] DR-A4 must specify which of the 4 bootstrap descriptions is authoritative

**Found by:** Stranger Reviewer 1
**Parent:** DR-A4
**Problem:** "Pick one canonical reference and reduce others to summaries" without saying
which one is authoritative canonizes whichever the implementer happens to prefer.
**Fix:** The canonical bootstrap reference is **BRIDGE_PROTOCOL.md §Session Boot**.
It is the most detailed and most recently maintained. README.md, USER_GUIDE.md, and
ARCHITECTURE.md must be reduced to summaries that cross-reference BRIDGE_PROTOCOL.md.
Any parameter or sequence contradiction must be resolved by aligning to BRIDGE_PROTOCOL.md.

---

### DR-A9a [P1] DR-A9 must pick between expand and remove

**Found by:** Stranger Reviewer 1
**Parent:** DR-A9
**Problem:** A CR that offers two options without choosing one produces an inconsistent
result depending on who implements it.
**Fix:** The correct action is **expand RUNTIME_RELOAD.md** with the hot-reload contract:
which settings are hot-reloadable (poll_interval_seconds, toasts_enabled, routing_rules_enabled),
which require watcher restart (wake_provider, session binding), and what triggers a
reload cycle. This is high-value operational content that belongs in its own file.

---

### DR-P6 [P0] Supersession during active pairing is undocumented (NEW)

**Found by:** Stranger Reviewer 1
**Problem:** When a Claude session is superseded mid-conversation, the behavior of
in-flight paired messages, active watcher config, and peer notifications is undefined
in any pairing doc. A new engineer implementing session rotation will not know
whether to: (a) drain the paired inbox before acknowledging supersession, (b) notify
the peer, or (c) hold watcher config for the old session.

**Fix:** Add a "Supersession during active pairing" section to BRIDGE_WATCH_LIFECYCLE.md:
1. Superseded session must drain its private bucket before exit.
2. New session must send a HANDSHAKE to the peer to renegotiate the pair.
3. Watcher config is updated by `bootstrap_session.py` at new session start — no
   manual intervention required.
4. Peer messages delivered to the old GUID during the supersession window are
   re-routed by the server to the rendezvous bucket if the GUID is no longer active.

---

### DR-P7 [P0] HANDSHAKE message protocol is not formally specified (NEW)

**Found by:** Stranger Reviewer 1
**Problem:** The HANDSHAKE message type is referenced throughout the pairing docs but
never formally defined: required fields, expected peer response (HANDSHAKE_ACK),
timeout behavior, and retry semantics are absent from all pairing specs.

**Fix:** Add a `HANDSHAKE Protocol` section to BRIDGE_PROTOCOL.md that specifies:
- Required fields: `from_agent`, `from_session_id`, `pair_id`, `project_id`, `schema_version`
- Expected response: `HANDSHAKE_ACK` within `HANDSHAKE_TIMEOUT_S` (default 30s)
- On no ACK: retry once; after second timeout, log `handshake_timeout` audit event
  and continue without pairing confirmation (bootstrap still succeeds)
- Response fields: same as HANDSHAKE plus `ack_session_id` from the peer

---

### DR-P1a [P1] DR-P1 must assign ownership, not just "reconcile"

**Found by:** Stranger Reviewer 1
**Parent:** DR-P1
**Problem:** The fix says "document explicitly that routing fallback and wake skip are
orthogonal." This is correct but it doesn't say which spec owns which rule.
**Fix:** Assign ownership:
- **BRIDGE_SESSION_ROUTING_HARDENING_SPEC.md** owns the routing fallback rule (SR3 stands).
- **AUTO_PAIR_SPEC.md** owns the wake-suppression rule (wake skipped when no breadcrumb).
- Add a one-line cross-reference in each spec to the other's complementary rule.

---

### DR-P2a [P1] PAIRING_STATE_MACHINE.md must use a structured table, not prose

**Found by:** Stranger Reviewer 1
**Parent:** DR-P2
**Problem:** For a system with 5+ orthogonal partial state spaces, prose state
descriptions are insufficient. The new doc must use a structured format.
**Fix:** Mandate a transition table format:

```
| Current State | Trigger | Guard | Next State | Action |
|---|---|---|---|---|
```

Include one table per state space (session, watch, routing, intent) and a
cross-reference diagram showing which state spaces compose.

---

### DR-P3a [P1] DR-P3 must pick between writing criteria and archiving phases B-D

**Found by:** Stranger Reviewer 1
**Parent:** DR-P3
**Problem:** A CR offering two options without choosing produces inconsistency.
**Fix:** Move Phases B-D to a separate `AUTO_PAIR_ROADMAP.md`. Reduce AUTO_PAIR_SPEC
to its implemented Layer 1 content. Archive the Phase A postmortem inline with a
"CLOSED — reverted 2026-04-28" header. This is the correct choice because Phases B-D
have no acceptance criteria, no owner, and no schedule — they are not spec content.

---

### DR-W7 [P1] Circuit breaker + force-fire cap interaction undefined (NEW)

**Found by:** Stranger Reviewer 2
**Problem:** If the circuit breaker is open when the pre-flight force-fire cap expires,
does the force-fire still fire? No spec defines this. If force-fire overrides the
breaker, the breaker provides no protection for cap-triggered wakes. If force-fire
respects the breaker, messages that hit the cap during a breaker-open period are lost.

**Fix:** Add to WAKE_HARDENING_SPEC.md D2: "If the circuit breaker is open at the
time of a pre-flight cap expiry, the force-fire is suppressed (breaker wins). The
message remains unread for the next wake cycle after the breaker auto-closes. This
is preferable to forcing a wake against a known-broken target."

---

### DR-W8 [P1] Placeholder fingerprint absent on first boot (NEW)

**Found by:** Stranger Reviewer 2
**Parent:** DR-W3 (covers staleness, not absence)
**Problem:** On a fresh install with no prior session, `placeholder_fingerprint` is
unset AND no prior "known-empty" state exists from which to capture it. The spec's
"first-run probe" assumes a prior Ctrl+Enter delivery occurred, but on first boot
there is no such event.

**Fix:** Add to PREFLIGHT_DETECTION_SPEC.md's Placeholder Fingerprinting section:
"On first boot (no prior session), if no placeholder fingerprint has been captured,
default to treating any non-whitespace composer content as `idle-with-draft` (conservative
path). The fingerprint is captured after the first successful Ctrl+Enter delivery.
Until captured, the `idle-empty` fast path is unavailable; all non-empty composers
trigger clipboard save/restore."

---

### DR-W1a [P1] APP_SERVER_WAKE_SPEC must be marked DRAFT until RNs resolved

**Found by:** Stranger Reviewer 2
**Parent:** DR-W1
**Problem:** "Resolve or annotate each RN" still allows developers to implement against
a half-decided spec.
**Fix:** Add to APP_SERVER_WAKE_SPEC.md header: "**DRAFT — Not yet implementable.
Open design decisions in Reviewer Notes RN1–RN8 must be resolved before implementation.**"
This single change prevents accidental implementation of undecided behavior without
requiring all RNs to be resolved immediately.

---

### DR-W2a [P1] DR-W2 must state the actual precedence rule

**Found by:** Stranger Reviewer 2
**Parent:** DR-W2
**Problem:** "Add one paragraph + cross-reference" without stating the rule leaves the
precedence ambiguous.
**Fix:** The precedence rule is: `ActiveTypingMaxWaitSeconds` (90s) is the cap for
the `actively-typing` state specifically. The priority-tiered caps (urgent 45s, normal
120s, low 300s) apply when the state is `idle-with-draft`. These are not in competition;
they apply to different states. Codify this explicitly: "If `actively-typing`: cap is
`max(ActiveTypingMaxWaitSeconds, priority_cap[urgency])`. If `idle-with-draft`: cap is
`priority_cap[urgency]` only."

---

### DR-S7 [P0] SECURITY_REVIEW has no threat model (NEW)

**Found by:** Stranger Reviewer 2
**Problem:** SECURITY_REVIEW.md contains findings and a partial fix list but no
formal threat model: no attacker goals, no trust boundaries, no asset inventory, no
attack surface enumeration. A security auditor cannot evaluate whether the findings
cover the right attack surface.

**Fix:** Add a "Threat Model" section to SECURITY_REVIEW.md:
- **Trust boundary:** All bridge communication is local-machine only (localhost MCP server).
- **Assets:** inbox messages (confidentiality + integrity), session GUIDs (authenticity),
  watcher-config (integrity — drives wake behavior).
- **Attacker model:** Malicious local process (same user); malicious content injected
  via bridge message (prompt injection via agent turn).
- **Out of scope:** Network attackers, cross-user attacks (single-user local deployment).

---

### DR-S8 [P1] `core/auth.py` and `core/transport.py` contents undocumented (NEW)

**Found by:** Stranger Reviewer 2
**Problem:** These two files are new (untracked in git status as `??`) and are referenced
by SECURITY_REVIEW.md as the auth seam implementation. Their interfaces, contracts, and
current implementation state are not documented anywhere.

**Fix:** Add a "Auth seam interface" section to BRIDGE_AUTH_SPEC.md that documents:
- What `core/auth.py` provides (stub? real? what interface?)
- What `core/transport.py` provides
- Which MCP tools currently call them vs. bypass them
- The gap between the stub and the full spec

---

### DR-S3a [P1] Commit SHAs must be required, not suggested, in "Closed" findings

**Found by:** Stranger Reviewer 2
**Parent:** DR-S3
**Problem:** For a security doc, "closed findings" without verifiable commit SHAs can
be fabricated or misremembered. SHAs must be required.
**Fix:** Mandate in DR-S3: "Every entry in the Closed findings section must include a
git SHA and a one-line test name that verifies the fix."

---

### DR-S4a [P1] DR-S4 has an unacknowledged blocking dependency on code CR-S1a

**Found by:** Stranger Reviewer 2
**Parent:** DR-S4
**Problem:** The doc fix (update BRIDGE_AUTH_SPEC) describes win32security-based
file protection. If code CR-S1a is not implemented first, the doc describes code that
does not exist.
**Fix:** Mark DR-S4 as "Blocked by code CR-S1a." Do not update the spec text until
`core/auth.py` implements the win32security approach.

---

### DR-S5a [P1] DR-S5 must require code verification before doc update

**Found by:** Stranger Reviewer 2
**Parent:** DR-S5
**Problem:** DR-S5 hedges with "may not reflect the fix." A security spec that
speculatively says "fix shipped" when it may not have shipped is worse than one
that says "fix pending."
**Fix:** Add acceptance criteria to DR-S5: "Verify via `git log --oneline tools/agent-bridge/bootstrap_session.py`
that the commit implementing `bootstrap_trusted_parent_drift_refused` is present and
all trusted-parent drift tests pass. Only then update BRIDGE_BOOTSTRAP_PROVENANCE_SPEC
to say 'Fixed.'"

---

### DR-D8 [P1] `implementation-journal.json` schema never defined (NEW)

**Found by:** Stranger Reviewer 3
**Problem:** `implementation-journal.json` is referenced in STATE_LAYOUT.md and
KNOWLEDGE_SHARING_CONTRACT_SPEC.md but falls through the cracks of DR-A5's scope.
No spec defines its per-entry fields, TTL, or schema version.

**Fix:** Include `implementation-journal.json` entry schema in DR-A5's
`SCHEMA_REFERENCE.md`. Minimum required fields per entry: `sequence`, `type`,
`timestamp`, `from_agent`, `session_id`, `subject`, `body`, `contract_id` (optional).

---

### DR-D9 [P1] Migration algorithm must be specified in prose before tooling exists

**Found by:** Stranger Reviewer 3
**Parent:** DR-D3
**Problem:** DR-D3 says "mark migration tooling as not yet implemented." That closes
the documentation gap but leaves implementers with no contract: even after reading the
fixed doc, they still do not know what a correct v1-to-v2 migration looks like.

**Fix:** Add a "Migration algorithm" subsection to BRIDGE_SCHEMA_EVOLUTION_SPEC.md
that specifies the migration in prose before tooling exists:
1. Read each JSONL row.
2. If `schema_version` is missing: add `"schema_version": "v1"`.
3. If v1 and upgrading to v2: add `tenant_id: "local-default"` and
   `originator_machine_id: "local-machine"` as defaults.
4. Write migrated rows to a temp file; replace original atomically on success.
This allows any implementer to write the migration tool from the spec.

---

### DR-D7a [P1] DR-D7 fix needs a tri-state deployment table

**Found by:** Stranger Reviewer 3
**Parent:** DR-D7
**Problem:** Changing "required for cloud, optional for v1" to "optional in v1, required in v2"
implies every v2 deployment is multi-tenant, which is false for local single-user setups.

**Fix:** Replace the incoherent wording with a deployment-class table:

| Field | local | single-tenant cloud | multi-tenant cloud |
|---|---|---|---|
| `tenant_id` | absent / `local-default` | present, fixed | present, variable |
| `schema_version` | v1 | v2 | v2 |

A v2 reader encountering a missing `tenant_id` on a local deployment must default to
`"local-default"`, not reject the row.

---

### DR-D4a [P1] DR-D4 must flag DR-A5 as a hard prerequisite

**Found by:** Stranger Reviewer 3
**Parent:** DR-D4
**Problem:** DR-D4 says "add session.json schema to SCHEMA_REFERENCE.md" — but
SCHEMA_REFERENCE.md doesn't exist yet (proposed in DR-A5). The fix is a forward
reference to a forward reference.

**Fix:** Explicitly mark: "DR-D4 is blocked by DR-A5. SCHEMA_REFERENCE.md must be
created (DR-A5) before session.json schema can be added (DR-D4)." Add DR-A5 to
Wave 1 and move DR-D4 to Wave 2.

---

### DR-O7 [P1] BRIDGE_HARDENING.md has no DR items despite being in audit scope (NEW)

**Found by:** Stranger Reviewer 3
**Problem:** BRIDGE_HARDENING.md was in the Operations group's audit scope but returned
no DR items. This is suspicious — either the doc is genuinely near-perfect or it was
not reviewed.

**Fix (via follow-up review):** The doc is near-9/10 by auditor estimate (H-01 through
H-21 pass audit notes, explicit deferred items). One gap: the "Known Limits" section
says monorepo override uses `.agent-bridge.json` but no example file or schema is
documented. Add a `.agent-bridge.json` format section to BRIDGE_HARDENING.md.

---

### DR-O1a [P1] REFACTOR_PLAN restructure must include an ownership/maintenance clause

**Found by:** Stranger Reviewer 3
**Parent:** DR-O1
**Problem:** Restructuring the doc prevents the current false-completion state but does
not prevent the "shipped" section from going stale again.
**Fix:** Add to REFACTOR_PLAN.md header: "Maintenance rule: The 'Shipped phases' section
is updated in the same commit that closes a phase. The commit message must reference this
file. If a phase is shipped and this file is not updated in the same commit, the omission
is a documentation defect."

---

### DR-O4a [P1] DR-O4 remediation steps must include failure paths

**Found by:** Stranger Reviewer 3
**Parent:** DR-O4
**Problem:** The proposed degraded-state remediation steps end at the happy path for
each command. If `resume_wake_for_session` itself fails, the runbook is a dead end.

**Fix:** Add a failure path for each remediation step:
- `bridge_reconnect_mcp` fails → check if MCP server process is running; restart via
  `python -m tools.agent_bridge.server_wrapper`
- `resume_wake_for_session` fails → check circuit breaker state; if state file is
  corrupt, delete `wake-failure-windows.json` and restart watcher

---

### DR-O5a [P1] DR-O5 must pick between section and cross-reference

**Found by:** Stranger Reviewer 3
**Parent:** DR-O5
**Problem:** "Two alternatives" without choosing is an unresolved decision.
**Fix:** `watcher-config.json` is auto-generated by bootstrap — users should not
edit it directly. Therefore the correct action is a **cross-reference in SETTINGS.md**
pointing to `configure_watcher()` documentation, not a new settings section. Add:
"watcher-config.json is auto-generated by `bootstrap_session.py` and managed by
`configure_watcher()`. Do not edit manually. See `WAKE_CODEX_TUNING.md` for the
canonical template."

---

## Amended Summary Table (v2 additions)

| ID | Group | Priority | Title |
|---|---|---|---|
| DR-A11 | Architecture | P0 | Monitor/compaction lifecycle not documented anywhere |
| DR-A10a | Architecture | P1 | Monitor description inconsistency promoted to P1 |
| DR-A1a | Architecture | P1 | DR-A1 fix assumes HARDENING specifies alternative |
| DR-A2a | Architecture | P1 | DR-A2 must resolve seen_at ownership, not defer |
| DR-A4a | Architecture | P1 | DR-A4 must specify BRIDGE_PROTOCOL.md as authoritative |
| DR-A9a | Architecture | P1 | DR-A9 must expand RUNTIME_RELOAD.md |
| DR-P6 | Pairing | P0 | Supersession during active pairing undocumented |
| DR-P7 | Pairing | P0 | HANDSHAKE protocol fields/response not specified |
| DR-P1a | Pairing | P1 | DR-P1 must assign ownership per spec |
| DR-P2a | Pairing | P1 | PAIRING_STATE_MACHINE.md must use structured table |
| DR-P3a | Pairing | P1 | DR-P3 must archive phases B-D to AUTO_PAIR_ROADMAP.md |
| DR-W7 | Wake | P1 | Circuit breaker + force-fire cap interaction undefined |
| DR-W8 | Wake | P1 | Placeholder fingerprint absent on first boot |
| DR-W1a | Wake | P1 | APP_SERVER_WAKE_SPEC must be marked DRAFT |
| DR-W2a | Wake | P1 | DR-W2 must state the actual precedence rule |
| DR-S7 | Security | P0 | SECURITY_REVIEW has no threat model |
| DR-S8 | Security | P1 | core/auth.py and core/transport.py contents undocumented |
| DR-S3a | Security | P1 | Closed findings must require commit SHAs |
| DR-S4a | Security | P1 | DR-S4 blocked by code CR-S1a |
| DR-S5a | Security | P1 | DR-S5 must require code verification first |
| DR-D8 | Data | P1 | implementation-journal.json schema never defined |
| DR-D9 | Data | P1 | Migration algorithm must be specified in prose |
| DR-D7a | Data | P1 | DR-D7 fix needs tri-state deployment table |
| DR-D4a | Data | P1 | DR-D4 blocked by DR-A5 (hard prerequisite) |
| DR-O7 | Operations | P1 | BRIDGE_HARDENING.md .agent-bridge.json format missing |
| DR-O1a | Operations | P1 | REFACTOR_PLAN restructure needs maintenance clause |
| DR-O4a | Operations | P1 | DR-O4 remediation steps need failure paths |
| DR-O5a | Operations | P1 | DR-O5 must choose cross-reference not new section |

**v2 additions: 3 new P0 · 25 new P1**
**Combined total: 17 P0 · 46 P1 · 5 P2**

---

## Revised Projected Post-Fix Scores (v2)

| Doc Group | v1 projected | v2 projected (post-P0+P1) |
|---|---|---|
| Core Architecture | 9/10 | 9.5/10 |
| Wake System | 8.5/10 | 9/10 |
| Session Pairing | 9/10 | 9.5/10 |
| Security & Auth | 8.5/10 | 9/10 |
| Data Model & Schema | 8.5/10 | 9.5/10 |
| Operations & Refactor | 9/10 | 9.5/10 |

Security and Wake are the two groups most likely to stay below 9.5 because
they have external implementation dependencies (auth code must exist before
auth specs can be accurate; APP_SERVER_WAKE RNs must be resolved before that
spec reaches implementable state).

P2 items bring each group to 10/10. No structural blocker prevents 10/10
with full v2 implementation.

[[handoff:codex]]
