# Agent Bridge Workflow Guardrails Spec

**Status:** Active policy with enforcement gaps tracked in `REFACTOR_PLAN.md`.

This spec defines which agent workflow and reminder behaviors are mandatory
bridge invariants and which are legitimate user/project preferences. It covers
the behaviors learned during Agent Bridge development across Codex, Claude, the
watcher, the Monitor, and the local documentation workflow.

## Design Rule

A behavior is **mandatory** when skipping it can silently lose work, corrupt
state, send work to the wrong thread, create a false "done" state, or leak
context across an unauthorized boundary.

A behavior is **configurable** only when both are true:

1. A real user or project would reasonably choose a different value.
2. A wrong value fails visibly instead of silently corrupting state or hiding
   work.

Mandatory behavior must not have a normal user-facing off switch. A temporary
debug override is allowed only when it is local-only, explicit, audited,
fail-visible, and documented as unsafe for normal operation.

## Enforcement Tiers

| Tier | Meaning | Examples |
|---|---|---|
| Tier 1 - structural | Code, schema, or process boundary makes skipping hard. | settings validation, sender liveness checks, targeted wake fail-closed gates |
| Tier 2 - tool-backed | Tools and hooks exist, but an agent must call or heed them. | `mark_handled`, pending ledger, Claude Monitor checks |
| Tier 3 - norm-backed | Documented agent discipline with no reliable enforcement yet. | log-first investigation, breakthrough relay, 10/10 review routine |

Tier 2 and Tier 3 mandatory items are not optional merely because enforcement is
weaker. Their enforcement gaps are roadmap work.

## Mandatory Guardrails

| Guardrail | Why mandatory | Current enforcement | Gap / next promotion |
|---|---|---|---|
| Session bootstrap and previous-session drain | Otherwise prior-session bridge mail can vanish during rollover. | Tier 1 for bootstrap; Tier 2 for surfacing drained rows in chat. | Stop/pre-response guard should detect unbootstrapped paired sessions before bridge work. |
| Claude Monitor presence before waiting on Claude | Durable mail can queue while Claude never wakes after compaction. | Tier 2: bootstrap reminder and docs. | Add Monitor presence/recent-heartbeat guard before "waiting for Claude" closeouts. |
| Explicit receipt lifecycle | `read` is not the same as handled; collapsing them loses work. | Tier 1 receipt fields/tools; Tier 2 agent follow-through. | Add stop-time guard for surfaced substantive rows lacking `handled_at`. |
| `ACTION_REQUEST` disposition gate | Action requests must become acting, parked, blocked, displaced, or rejected. | Tier 2/Tier 3 via heuristics and final reminders. | Add stop-time disposition guard keyed by message id. |
| Outbound reply-debt persistence | A blocked send can be forgotten across compaction if only remembered in chat. | Tier 2 pending ledger exists. | Add guard requiring queued id or full pending body ledger entry before closeout. |
| No silent inbox failure | A quiet no-op looks like an empty inbox and hides broken communication. | Tier 1 local CLI/MCP result contracts and hook canaries. | Keep probes in pre-response/pre-final hooks; failures must remain loud. |
| No silent consumption | Watcher, bootstrap, compaction, and hooks must not mark unread work read. | Tier 1 protocol invariant and tests in key paths. | Extend tests as new consumers are added. |
| Wrong-thread wake prevention | A wake into the wrong chat corrupts the user's work context. | Tier 1 targeted SendKeys thread id, breadcrumb, stale-context, and preflight gates. Read-only UIA sidebar/title enumeration is the preferred non-intrusive Desktop identity primitive where available. | Keep live Desktop stale-context certification in release checklist, including active chat title/project evidence before UI write paths. |
| Constant wake payload | UI wake must not paste arbitrary bridge bodies or secrets. | Tier 1 `wake_codex.ps1` fixed `check bridge inbox` payload. | No normal override; any debug variant must be explicit and audited. |
| Log-first investigation | Strange bridge failures have repeatedly been misdiagnosed without durable logs. | Tier 3 heuristic. | Add health-panel/review templates that require relevant audit/log references for root-cause claims. |
| Pending ledger drain | Known work should not disappear because the immediate user prompt ended. | Tier 2 final guard and ledger tools. | Add stop hook that blocks idle closeout with actionable top ledger item unless parked/blocked/displaced. |
| Active-task interrupt resumption | Inbox checks, status questions, and process/debugging interrupts must not erase an already-open implementation or review task. | Tier 2: Codex pre-final reminder warns when `execution-state.json` still has an active task. | Promote to shared guardrail debt/dashboard visibility and require `resume`, `complete`, `blocked`, `parked`, or `displaced` before final closeout. |
| Peer relay for shared bridge decisions | Claude and Codex drift when one side learns a rule, fix, or roadmap change alone. | Tier 3 heuristic; bridge tools provide delivery. | Add relay-debt guard for known design/status/completion messages not sent. |
| Both-scoped settings parity | A shared setting is unsafe if only one side honors it. | Tier 2 docs plus partial tests; behavioral-only keys are not complete until both agents prove they honor them. | Add settings-parity checklist/test for every new `both` key and dashboard debt for incomplete keys. |
| Settings write recipe | Broken JSON or BOM/parse failures can make Desktop overwrite or ignore config. | Tier 1 loader rejects invalid keys/types; safe writer not universal. | Add one reusable UTF-8-no-BOM atomic settings writer and require all settings mutations to use it. |
| Worktree and scratch-path discipline | Wrong-branch commits and tracked scratch leaks are painful to recover. | Tier 3 repo instructions and `.gitignore`. | Add doc/review checks for new scratch artifacts and personal paths. |
| 10/10 implementation routine | One agent's confidence is not enough for shared bridge readiness. | Tier 3 completion standard. | Add checklist support in ledger/dashboard so review debt is visible. |
| Review closeout handoff | Peer review loops drift if findings are patched locally but the amended status is never sent back. | Tier 2: `review-loop-state.jsonl` and Codex final reminder detect handled review results without closeout. | Promote to shared guardrail debt/dashboard visibility and require closeout before 10/10 completion. |
| Background reviewer wait discipline | Waiting on stranger agents can silently become stopping if ETA/checkback state lives only in chat. | Tier 2: `reviewer-wait-state.jsonl`, AgentBridge reviewer-wait tools, and Codex final reminder detect missing/due ETA/checkback debt. | Promote to dashboard guardrail debt; require `verdict_received`, future ETA/checkback, or parked/blocked/cancelled status before final closeout. |
| Privacy and policy gates | Cross-project/body catch-up can leak sensitive context without consent. | Tier 1/Tier 2 knowledge contracts and policy authority surfaces. | Keep policy drift and contract reauth visible in dashboard. |

## Configurable Behaviors

These may be settings, modes, or documented project preferences because a wrong
choice should be visible and reversible:

- Wake provider and UX mode: visible `targeted_sendkeys`, headless
  `app_server`, toast-only `disabled`, or diagnostic modes.
  This setting changes how the receiver is nudged and whether the Desktop chat
  visibly renders the wake. It does not relax receipt/handling obligations,
  target-validation gates, constant-payload rules, or wrong-thread prevention.
- Wake/reminder cadence: poll interval, idle threshold, max wait, compaction,
  stale-unread rearm timing, and retention windows.
- Notification style: toast enablement, tray cap, expiry, and retention mode.
- Pairing defaults: ask-first, active-primary, background, and pending-pair
  timeout.
- Heuristic mirror mechanism: automatic mirror workflow versus manual review
  before mirroring. The relay content may be mandatory; the mirror mechanism is
  configurable.
- Bridge trigger sensitivity: terse versus chatty peer communication, as long
  as mandatory relay categories still fire.
- Status report format and verbosity.
- Review depth above the required floor.
- Backpressure and rate limits within safe bounded ranges.
- Catch-up digest detail level under an active knowledge contract.
- Project-specific doc-pointer strictness when a repository lacks a docs tree.
- Local shell/background-task discipline, where the repository has stronger or
  weaker conventions than Agent Bridge.

## Minimum Compliance Artifacts

Until every mandatory guardrail is structurally enforced, agents must leave
durable evidence that the rule was followed:

| Guardrail class | Minimum artifact |
|---|---|
| Log-first root-cause claim | Audit/log path, message id, event id, or health-panel row cited in the diagnosis. |
| Peer relay | `send_to_peer` result id, or a pending-ledger entry containing the full unsent body and blockage reason. |
| `ACTION_REQUEST` disposition | `mark_handled` status/reason, or pending-ledger item with acting/parked/blocked/displaced/rejected state. |
| Response debt | Original message id plus either queued reply id or stored pending reply body. |
| 10/10 routine | Codex result, Claude review result, two stranger-review results, and any `RISK_DELTA` resolution. |
| Review closeout | Review request id, peer result id, local handled/disposition event, and closeout `send_to_peer` id or pending action with exact peer result id plus full recoverable closeout body. |
| Settings parity | Changed key, scope, consuming code paths for each scoped agent, docs update, and focused validation. |
| Wake certification | Wake attempt id, target thread/project evidence, receipt state, and no-wrong-thread diagnostic when applicable. |
| UIA Desktop identity evidence | Sidebar/active-title snapshot or explicit unavailable reason; read-only UIA checks are non-intrusive and separate from SetFocus/write-side activation. |
| Active-task interrupt resumption | Active task id, interrupt classification, and one of `resume`, `complete`, `blocked`, `parked`, or `displaced` before final response. |
| Background-agent wait | `reviewer-wait-state.jsonl` row containing wait id, reviewer id, first checkpoint/request id, requested ETA/checkback, renewed ETA if missed, and either `verdict_received` or parked/blocked/cancelled status. |

If the artifact cannot be produced, the guardrail is not complete. Before Phase
18 ships, record the missing artifact in the pending-action ledger, review
notes, or peer reply. After Phase 18 ships, record it as a `guardrail-debt.jsonl`
row rather than treating the work as done.

## Settings Admission Gate

Do not add a workflow/reminder setting unless all answers are "yes":

1. Is the behavior configurable rather than mandatory under this spec?
2. Does a real user/project have a legitimate reason to change it?
3. Is the failure mode of a bad value visible in the health panel, logs, hook
   output, or user-facing UI?
4. Are defaults safe for visible bidirectional pairing?
5. If scoped to `both`, can both Claude and Codex read and respect it before
   the setting is considered complete?

If any answer is "no", keep the behavior mandatory, implement a debug-only
override, or defer the setting.

## Stop-Time Promotion Plan

The next enforcement step is Phase 18, an unshipped stop/pre-final guard suite
that promotes the most dangerous Tier 2 items toward Tier 1:

1. Refuse or loudly warn on final closeout when a surfaced `ACTION_REQUEST`
   lacks a recorded disposition.
2. Refuse or loudly warn when a surfaced substantive bridge message is read but
   not handled.
3. Refuse or loudly warn when an outbound peer reply failed/backpressured and no
   full pending body is recorded in the ledger.
4. Warn before "waiting for Claude" if the Claude Monitor is not known running
   after the current compaction/session rollover.
5. Warn when the top Codex-owned ledger item is actionable and the turn is idle.
6. Warn when a peer review result has been handled locally but the amended
   closeout has not been sent or durably parked.
7. Warn when a turn is about to close while an active task is still open and the
   interrupt has not been classified as resumed, completed, blocked, parked, or
   displaced.

These guards should be fail-visible first. Once false positives are understood,
they may become blocking for Agent Bridge implementation sessions.
