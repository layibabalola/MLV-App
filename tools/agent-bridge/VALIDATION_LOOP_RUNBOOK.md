# Final 10/10 Validation Loop - Operational Runbook

**Status:** Active tail-end runbook. The canonical 10/10 gate still lives in
`REFACTOR_PLAN.md`; this doc is the operational expansion for when the core
bridge roadmap is actually ready to enter the loop.
**Authors:** Claude
**When:** This loop runs at tail-end of the bridge roadmap, after all
acceptance criteria are met and Phase 14 security review completes.
**Pre-2026-04-28-roadmap-shift:** this loop was blocking earlier feature
work. Per the 2026-04-28 user-directed reshuffle, this loop is now a
tail-end gate; per-feature contract tests stay inline.

---

## Pre-flight checklist (do these in order before entering the loop)

1. **Acceptance criteria 1-33 closed.** Check
   `tools/agent-bridge/REFACTOR_PLAN.md` against current state. Any
   item marked "open" must be either (a) closed via Codex commit + Claude
   audit, or (b) explicitly removed from scope by user with rationale.
2. **All in-scope core-bridge specs marked Implemented, Archived, or
   explicitly Partial with remaining gaps called out.** Per criterion 25, the
   core bridge docs should not carry stale "Proposed" status by the time the
   loop starts. Parallel/out-of-scope design tracks such as cross-project
   pairing are excluded until they are explicitly pulled into scope. Run:
   ```bash
   grep -n "^\*\*Status:\*\* Proposed" tools/agent-bridge/*.md
   ```
   Expected: no remaining Proposed statuses for docs that are part of the
   accepted core bridge roadmap.
3. **Phase 14 security review complete.** All eight threat additions
   from msg `2071358b` either implemented or explicitly accepted as
   residual risk. Audit log must show `phase14_security_signoff` event.
4. **Any explicitly in-scope extension tracks green.** If cross-project pairing
   has been accepted into the main bridge scope by user + Claude + Codex, use
   `CROSS_PROJECT_PAIR_TEST_MATRIX.md` and require the agreed tier-1 set to
   pass. Otherwise, this step is skipped as out of scope for the core bridge
   10/10 call.
5. **Wake hardening shipped for the accepted scope.** D1 (pause_bridge
   gating) must be green. D2 (circuit breaker) must be green if it remains in
   scope for the 10/10 call; otherwise it must be explicitly deferred with user
   agreement and documented residual risk.
6. **Both agents agree on scope.** Send a `READINESS_CHECK` to peer:
   "Are we both ready to enter the Final 10/10 Validation Loop? List any
   blockers." If either side names blockers, return to step 1.

---

## The loop (run independently per agent, then converge)

### Step 1 - Smoke suite (each agent runs independently)

Each agent executes its full smoke suite from a fresh interpreter on a
clean checkout:

**Codex side:**
```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_phase0_contract
```

**Claude side:** identical command. Both agents must report green.

If either side has red tests: STOP. Fix the failure (Codex implements,
Claude reviews). Re-enter step 1.

### Step 2 - Failure-path suite (each agent runs independently)

Each agent runs the explicit failure-injection suite. This includes:

- All tests with `_failure`, `_corrupt`, `_race`, `_revoke`, `_expired`,
  `_stale` in their name
- Concurrency harness (criterion 17): N=4 simultaneous senders + N=2
  watchers + 1 receiver, no message loss or duplication
- Hypothesis property tests (criterion 22) for any function with property
  contracts (e.g. `compact_inbox` is idempotent under repeated calls)
- Graceful-shutdown destructive path tests (criterion 26): SIGTERM
  during active send, watcher restart, watcher kill mid-poll

Each agent reports:
- Number of tests run
- Number of tests passed / failed / skipped
- Any unexpected output, warnings, deprecation notices

### Step 3 - Cross-check: agents compare results

Each agent sends a `READINESS_ASSESSMENT` to the peer with:
- Smoke result (count + status)
- Failure-path result (count + status)
- Any unexpected output observed
- Self-assessed score on two axes:
  - Operational confidence today (smoke-testable)
  - Hardening including future drift (full suite + roadmap completion)

If both sides converge on 10/10 on both axes: proceed to step 4.

If divergence: each side sends `RISK_DELTA` for the items that move their
score. Resolve by code change, doc change, or scope removal (user
authorizes scope removal). Return to step 1.

### Step 4 - Bilateral final agreement

Both sides send a `FINAL_VALIDATION_PASS` with:
- Final scores (must be 10/10 on both axes from each side)
- List of all closed acceptance criteria
- List of explicitly-removed items with user authorization links
- Commit SHA at which the validation was performed

User accepts via response: "10/10 confirmed" or equivalent.

### Step 5 - Lock-in commit

A single commit on the integration branch with:
- A `VALIDATION_LOOP_PASS.md` file recording the validation event:
  - Date / time UTC
  - Bilateral session GUIDs at time of validation
  - Final score from each side
  - Commit SHA validated
  - User acceptance message
- A tag: `bridge-v1.0-validated`

After this commit lands, the bridge refactor is officially complete.
Subsequent bridge work is "post-1.0 enhancement", subject to its own
review process.

---

## Loop exit criteria

You exit the loop only via:

1. **Both agents 10/10 on both axes + user accepts** → step 5 lock-in
2. **User explicitly removes the loop from scope** → document + close
3. **Critical bug discovered** → return to step 1 after fix; this can
   loop indefinitely; that's the point

Do NOT exit via:
- "Good enough"
- "Smoke passed, that's fine"
- "We can fix that in 1.1"

The loop's job is to surface anything before 1.0; if it surfaces something,
it's blocking by definition.

---

## Common pitfalls

1. **Confusing operational confidence with hardening.** Smoke alone
   pushes operational, not hardening. A 10/10 operational + 8/10
   hardening is NOT 10/10. Both axes must be at 10. Use the two-axis
   framing from `bridge_trigger_heuristics.md`.
2. **Reading old test results.** Tests must run from a fresh interpreter
   on the actual commit being validated. Cached pytest output from a
   prior commit doesn't count.
3. **Skipping concurrency harness.** Criterion 17 is concurrency
   no-loss/dup proof. If the harness doesn't exist, the loop hasn't
   even started — go build it before re-entering.
4. **One agent runs all the tests, other agent trusts.** Both agents run
   independently. Trust-but-verify is the entire point of the loop.
5. **Spec status drift.** Specs gain "Proposed" status as new ideas pop
   up. The loop's pre-flight requires zero "Proposed" specs. Audit
   regularly during the velocity period; don't let the queue accumulate.

---

## Memory and resumption

If the loop is interrupted (context compaction, session end, user pause):

1. Persist current step + each agent's last-completed sub-step to
   `tools/agent-bridge/state/validation-loop-state.json`
2. On resumption: read state, continue from last step
3. If state is missing or older than 24h: restart the loop from step 1

---

## Coordination model

Both agents participate equally in the loop. There's no "Codex
implements, Claude reviews" asymmetry here — both run their own tests
independently and compare. User is the final arbiter.

Audit profile not needed for this doc; it IS the audit profile for the
overall refactor.
