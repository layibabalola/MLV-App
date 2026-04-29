# Agent Bridge - Wake Recovery Loops Spec

**Status:** Implemented for WR1/WR2/WR3 v1; optional WR1a held-send retry
remains deferred.
**Authors:** Claude (proposal); Codex implementation
**Tier:** Tier 1 - direct response to recovery gaps observed 2026-04-29 13:53 UTC after Layers 1-4 shipped (commit d97eaf9c)
**Depends on:** Layers 1-4 from `WAKE_HARDENING_SPEC.md` + d97eaf9c (D2 breaker, rate limit, parent kill, mark_read backfill) - all of which are live.
**Motivation:** Layers 1-4 prevent wake storms by tripping the D2 breaker after consecutive failures. Trade-off: when the breaker opens, there's no auto-recovery path beyond the 15-min idle auto-close, which silently re-arms the closed state but does NOT drain accumulated backlog. Today (2026-04-29) we observed: 5 wake failures opened the breaker at 13:53:15 UTC; subsequent messages emitted `wake_skipped_breaker_open`; user manually pasted "check bridge inbox" two hours later (15:52:59 UTC). The receiver may have recovered well before that - we never tried again.

This spec adds three independent recovery loops so the system self-heals from breaker-open states without requiring user intervention:

- **WR1** (sender-driven): a `send_to_peer` rejected by backpressure ALSO fires a wake nudge to drain the receiver
- **WR2** (user-driven): explicit user actions (`resume_bridge`, new `nudge_peer`, `mark_read` of stale msg) grant a one-shot breaker bypass
- **WR3** (time-driven): when the D2 breaker auto-closes after idle, fire one wake to drain the backlog instead of silently re-arming

All three respect the wake-storm safety properties: rate limit (Layer 3) holds, breaker (Layer 4) is preserved as the storm prevention; only WR2 can bypass the breaker, and only on explicit user intent with a 60-second cooldown.

---

## Implemented Behavior

Implemented in the bridge code path:

- WR1: `send_to_peer` backpressure rejection re-arms the existing unread
  receiver message for the watcher when the breaker is closed and the wake
  pre-fire rate limit is not active. Audit events distinguish attempted,
  breaker-blocked, rate-limited, and no-unread outcomes.
- WR2: `nudge_peer`, `resume_bridge`, and stale `mark_read` grant one-shot
  breaker bypasses through `wake-failure-windows.json`. The watcher consumes a
  bypass on the next eligible wake attempt; success closes the breaker, failure
  keeps the breaker open and records a normal wake failure.
- WR3: the watcher selects one unread backlog message when an idle breaker is
  ready to auto-close, removes that message from watcher seen-state if needed,
  and fires one wake subject to the normal wake pre-fire rate limit.

Implementation note: v1 dispatch is watcher-poll driven rather than direct MCP
tool invocation of the wake script. This keeps a single wake execution path and
preserves the existing watcher timeout, rate-limit, provenance, and audit
guards.

Deferred:

- WR1a held-send retry remains optional and off the shipped path.
- The health panel surfacing for breaker/recovery state remains tracked by
  `BRIDGE_HEALTH_PANEL_SPEC.md`.

---

## Goal

Convert the breaker from "open and stay open until something else happens" into a self-healing state with three recovery triggers (sender intent, user intent, time elapsed) - while preserving the wake-storm safety properties Layers 1-4 provide.

## Non-Goals

- No new breaker tuning. WAKE_BREAKER_THRESHOLD/WINDOW/IDLE_CLOSE constants stay where d97eaf9c set them.
- No replacement of Layers 1-4. WR1/WR2/WR3 layer ON TOP of the existing breaker; they do not modify Layer 4's open/close semantics.
- No multi-tenant cloud-aware recovery. Single-machine v1 only.
- No removal of manual recovery paths. The user-paste-into-Codex path remains; we just stop requiring it as the only path.

---

## Architecture

```
                                            +-------------------+
                                            |   D2 breaker      |
                                            |   (Layer 4)       |
                                            +--+----------+-----+
                                               |          |
                       open|                   |          |closed
                            |                   |          |
                            v                   v          v
                  ┌──────────────────────┬─────────────────────┐
                  │  Recovery triggers   │  Normal wake firing │
                  │  (this spec)         │  (Layers 1-4)       │
                  ├──────────────────────┴─────────────────────┤
                  │                                            │
                  │  WR1: send_to_peer backpressure rejection  │
                  │       └─> nudge_peer (respects breaker)    │
                  │                                            │
                  │  WR2: resume_bridge / nudge_peer /         │
                  │       mark_read (stale)                    │
                  │       └─> bypass_grant++                   │
                  │       └─> next wake fires despite open     │
                  │       └─> success: breaker fully closes    │
                  │       └─> failure: breaker stays open      │
                  │                                            │
                  │  WR3: breaker auto-close transition        │
                  │       └─> fire one wake if backlog exists  │
                  │       └─> failure restarts Layer 4 window  │
                  │                                            │
                  └────────────────────────────────────────────┘
```

The breaker is the single source of truth for storm prevention. Recovery triggers either (a) respect the breaker (WR1, WR3) or (b) provide a bounded one-shot bypass (WR2). The bypass is rate-limited and tied to explicit user intent so it cannot be used to defeat the breaker.

---

## Acceptance Criteria

### WR1 - Backpressure rejection auto-nudges receiver

**Behavior:** when `send_to_peer` is refused due to receiver backpressure (`error_kind="backpressure_unread_work"`), the server attempts a wake nudge to the receiver before returning the error. The nudge respects all existing wake protections - rate limit and breaker hold.

- [ ] Server fires a wake_<peer> attempt on backpressure rejection
- [ ] Wake honors Layer 3 rate limit (skipped if recent fires for that session)
- [ ] Wake honors Layer 4 breaker (skipped if breaker open for that session)
- [ ] Audit events distinguish:
  - `backpressure_rejected_with_nudge` (nudge fired)
  - `backpressure_rejected_no_nudge_breaker_open` (breaker prevented)
  - `backpressure_rejected_no_nudge_rate_limited` (Layer 3 prevented)
- [ ] The `send_to_peer` error response reports which path was taken (in `data.nudge_status` or similar)
- [ ] Tests:
  - `test_wr1_backpressure_rejection_fires_nudge_when_breaker_closed`
  - `test_wr1_backpressure_rejection_skips_nudge_when_breaker_open`
  - `test_wr1_backpressure_rejection_skips_nudge_when_rate_limited`
  - `test_wr1_backpressure_rejection_emits_correct_audit_event_per_path`
  - `test_wr1_nudge_honors_layer3_rate_limit_under_rapid_backpressure`

**Optional WR1a (held-send retry, behind feature flag in v1):**

- [ ] Server holds the rejected send for up to `WR1_HOLD_SECONDS` (default 30) after receiver marks_read; sender's MCP call awaits up to that ceiling and writes the message if the wait clears
- [ ] Behind a flag (`WR1A_HELD_SEND_ENABLED`) in v1, off by default
- [ ] Tests:
  - `test_wr1a_held_send_writes_after_receiver_drains`
  - `test_wr1a_held_send_times_out_returns_error`
  - `test_wr1a_disabled_by_default_returns_immediate_error`

### WR2 - User-action breaker bypass (one-shot recovery)

**Behavior:** explicit user actions grant a one-shot bypass that fires the next wake despite the breaker being open. Success closes the breaker; failure keeps it open. Rate-limited per `(agent, session_id)` per 60s.

**Definition of "user action":** an MCP tool call from the user's surface that signals delivery intent. The fixed list:

- `resume_bridge` - any open breakers at resume time get one bypass each on next wake
- `nudge_peer(agent, session_id=None)` - new MCP tool, explicit one-shot wake
- `mark_read` of a message older than `BREAKER_BYPASS_STALE_MIN` (default 30 min)

**Why these and not others:** these are the only MCP tools whose semantics imply "I want this delivered now." Other mutating tools (e.g. `mark_handled`, `pause_bridge`) don't carry that intent.

- [ ] New MCP tool `nudge_peer(agent: str, session_id: str | None = None)`:
  - Fires one wake_<peer>.ps1 invocation immediately
  - If breaker is open: bypasses, executes, on success closes the breaker; on failure records the failure as if a normal wake had failed
  - If breaker is closed: same as a normal wake fire
  - Rate-limited: max one call per `(agent, session_id)` per `BREAKER_BYPASS_COOLDOWN_S` (default 60s)
  - Audit event `nudge_peer_user_initiated`
- [ ] `resume_bridge` enhancement:
  - Scans `wake-failure-windows.json` for any open breakers
  - For each open breaker, increments `bypass_grant` field by 1
  - Audit event `resume_bridge_breaker_bypass_grant` per granted bypass
- [ ] `mark_read` enhancement:
  - If the message's `created_at` is older than `BREAKER_BYPASS_STALE_MIN` AND a breaker is open for that session: increment `bypass_grant` by 1
  - Audit event `mark_read_stale_breaker_bypass_grant`
- [ ] `wake-failure-windows.json` schema:
  - Adds `bypass_grant: int` field per session (count of pending bypasses)
  - Watcher checks before firing: if `breaker_state == "open"` AND `bypass_grant > 0`, decrements and fires
- [ ] Bypass success: breaker fully closes (failure window cleared per Layer 4 success-clears semantic)
- [ ] Bypass failure: breaker stays open; failure recorded normally; bypass NOT re-granted
- [ ] Bypass is one-shot: does NOT persist beyond a single wake fire
- [ ] Tests:
  - `test_wr2_nudge_peer_fires_when_breaker_closed`
  - `test_wr2_nudge_peer_bypasses_open_breaker_one_shot`
  - `test_wr2_nudge_peer_bypass_failure_keeps_breaker_open`
  - `test_wr2_nudge_peer_rate_limit_blocks_rapid_calls`
  - `test_wr2_resume_bridge_grants_bypass_per_open_breaker`
  - `test_wr2_resume_bridge_no_grant_when_no_open_breaker`
  - `test_wr2_mark_read_stale_message_grants_bypass`
  - `test_wr2_mark_read_fresh_message_does_not_grant_bypass`
  - `test_wr2_bypass_success_closes_breaker_fully`
  - `test_wr2_bypass_failure_does_not_extend_breaker_window`

### WR3 - Breaker auto-close drains backlog

**Behavior:** when the D2 breaker transitions from open to closed via the auto-close path (15-min idle, Layer 4 default), AND there are unread messages for that session: fire ONE wake immediately. The wake's purpose is to nudge the receiver to drain its backlog - not to deliver each message individually.

- [ ] On breaker open → closed transition via auto-close path:
  - Check if any unread messages exist for the affected session
  - If yes, fire ONE wake_<peer>.ps1 invocation
  - Subject to Layer 3 rate limit (may be deferred 10s if recently fired - rare)
- [ ] If retry wake succeeds (exit 0): breaker stays closed; backlog drains naturally
- [ ] If retry wake fails: counted as a fresh Layer 4 first failure (window restarts at 1)
- [ ] Audit event `wake_breaker_autoclose_retry` per fire
- [ ] Only triggered by auto-close, NOT by WR2 user-bypass-success (which is a different transition path)
- [ ] One wake per session - regardless of how many messages are unread
- [ ] Tests:
  - `test_wr3_autoclose_with_unread_fires_one_wake`
  - `test_wr3_autoclose_with_no_unread_does_nothing`
  - `test_wr3_autoclose_retry_failure_starts_fresh_window`
  - `test_wr3_autoclose_retry_respects_layer3_rate_limit`
  - `test_wr3_user_bypass_close_does_NOT_trigger_autoclose_retry`

---

## Failure Modes

| Failure | Detection | Behavior |
|---|---|---|
| WR1 nudge fires but receiver is gone | Layer 4 will trip again at threshold | OK - same protection as without WR1 |
| WR2 user calls `nudge_peer` 100 times | Rate limit (60s cooldown) | Most calls return rate-limited error |
| WR2 agent attempts polled `nudge_peer` | Rate limit holds; cooldown blocks loops | OK; auditable as `nudge_peer_user_initiated_rate_limited` |
| WR2 bypass cascade (multiple grants accrue) | `bypass_grant` is incremented but consumed one-per-fire | Watcher decrements on each fire; cannot accumulate beyond a small bound (3 sources × 1 each) |
| WR3 retry storms by re-tripping breaker repeatedly | Layer 4 catches at threshold | Eventual auto-close → another retry; net effect: one wake per 15-min cycle while receiver remains broken. NOT a storm. |
| Receiver in permanent bad state | All three loops fail | One wake per 15min cycle (WR3) plus one wake per backpressure'd send (WR1) plus user-initiated wakes (WR2). All bounded. |

---

## Tests Required

See per-WR test list above. Total: 15 unit tests across WR1/WR2/WR3 + 3 optional WR1a tests.

**Integration:**

- `test_int_wr_recovery_loop_2026-04-29_regression`: simulate the actual 13:53 UTC scenario - 5 wake failures open breaker, message backpressure'd; verify WR1 fires nudge (gets blocked by breaker, audit event correct); verify WR2 user calls `nudge_peer` and bypass works; verify WR3 auto-close fires retry.
- `test_int_wr_three_loops_compose_correctly`: rapid sequence of all three recovery triggers; verify rate limits + breaker semantics hold; no double-firing.
- `test_int_wr_recovery_under_continuous_failure`: receiver permanently broken; verify bounded wake count over 1 hour (target: <=10 fires/hour).

---

## Phased Rollout

**WR-Phase 1 (highest leverage, smallest surface):** WR3. Modifies Layer 4 auto-close to fire one wake. Single-file change; no new MCP tools. Closes the recovery loop for the time-elapsed case (which is what would have helped 2026-04-29 at 14:08:15 UTC if shipped).

**WR-Phase 2:** WR1. Adds nudge attempt on backpressure rejection. Helps when sender's intent should escalate. Modifies `send_to_peer` error path.

**WR-Phase 3:** WR2 (full user-bypass). New `nudge_peer` MCP tool, schema bump for `bypass_grant`, three trigger points (`resume_bridge`, `nudge_peer`, stale `mark_read`). Largest of the three; biggest user-facing payoff.

**WR-Phase 4 (optional):** WR1a held-send retry. Behind a feature flag.

Each phase ships in its own commit, audited via `audit-profiles/wake-recovery-loops.md`.

---

## Coordination Model

Codex implements; Claude reviews. Audit profile: `tools/agent-bridge/audit-profiles/wake-recovery-loops.md` (added with this spec).

**Composes with:**

- `WAKE_HARDENING_SPEC.md` - WR1/WR2/WR3 layer on top of Layer 4 (D2 breaker) without modifying its open/close semantics
- `BRIDGE_HEALTH_PANEL_SPEC.md` HP11 - panel surfaces breaker state and recent recovery events; user sees "breaker open for codex" → calls `nudge_peer` → bypass fires
- `BRIDGE_SESSION_ROUTING_HARDENING_SPEC.md` SR3 (default-resolve) - WR1's nudge target is the receiver's active session, same resolution path as send_to_peer
- `MESSAGE_RECEIPTS_SPEC.md` - WR2's `mark_read` trigger reads message age from `created_at`; depends on receipt schema being stable

**Does NOT compose with:**

- Cross-project pairing (separate channel)
- Multi-tenant cloud auth (Tier-3 future)

[[handoff:codex]]
