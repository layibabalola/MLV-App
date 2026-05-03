# Codex Bridge Trigger Heuristics

This file is Codex-specific bridge policy. It complements `AGENTS.md`.

`AGENTS.md` keeps the always-on startup contract short.
This file holds the evolving heuristics, wake-loop behavior, and message-handling discipline for Codex.
For the operational lifecycle spec, see `tools/agent-bridge/BRIDGE_WATCH_LIFECYCLE.md`.

## Bridge Non-Negotiables

- Start every bridge-focused turn with non-destructive inbox hygiene on the active Codex private bucket and the project bucket.
- End every bridge-focused turn with the same inbox hygiene before the final response.
- If Codex says `I’m doing X now`, that is a real commitment: the next substantive action must be the edit/action itself unless Codex explicitly says it is `blocked` or `displaced`.
- After interrupts such as `check bridge inbox`, automatically resume the interrupted committed task unless the interrupt clearly changes priority.
- If priority changes, say so explicitly in the chat instead of silently drifting.
- Do not describe something as a wait state unless Codex can name the exact message, decision, or condition it is waiting on.
- Do not present closed-on-send messages (`ACTION_REQUESTED: none` or explicitly optional wording) as `still in flight` or `waiting on Claude`.
- `PROTOCOL_SYNC` / `PROTOCOL_SYNC_ACK` with `confirm` plus `before` is a synchronous gate and must not be left as background traffic.
- If a wait is real enough to block work, it is real enough to bridge as `WAIT_DECLARED`.

## Session Startup

After bridge bootstrap succeeds, perform Codex-side inbox hygiene for the active session.
Do not start a persistent blocking `wait_inbox` loop in the main working chat by default.

Use a blocking loop only for a short, explicit smoke test or a deliberately parked bridge-watch session.
The loop is not considered active until Codex explicitly calls `wait_inbox(...)` in a live turn.

Bridge-watch mode flag:

- Toggle it explicitly with:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-bridge\codex_bridge_watch_mode.ps1 -Action on`
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-bridge\codex_bridge_watch_mode.ps1 -Action off`
- The flag enables louder pre-response/pre-final reminders only.
- It is not hard enforcement and does not override the main-chat default away from start/end inbox hygiene.

- Preferred loop:
  - call `wait_inbox(agent="codex", session_ids=["mlv-app", "<active-guid>"], timeout_seconds=55, mark_read=false)`
- Use `55` seconds so the call returns before the MCP host's default `60` second timeout.
- During an explicit wait-loop test, re-invoke immediately on timeout. Do not pause and do not wait for user input.
- The goal of that test loop is near-continuous blocking:
  - `wait_inbox` returns
  - handle or discard
  - `wait_inbox` again
- Keep the gap between waits as small as possible.
- If a message arrives:
  - surface it in chat,
  - handle it,
  - mark it read explicitly by id,
  - then re-enter `wait_inbox`.
- If the tool returns a transient error or unhandled exception:
  - surface the error,
  - then re-invoke `wait_inbox` unless a stop condition below applies.
- Stop conditions:
  - `SESSION_UPDATE: superseded` arrives
  - the user explicitly ends or clears the session
  - the chat is compacted and the prior live turn is no longer running
  - a newer Codex session takes over and this session is no longer the active owner
  - the user steers or interrupts the conversation
- Any normal user message interrupts the active `wait_inbox` chain.
- In the main working chat, do not auto-reenter `wait_inbox` after answering an interrupted user message; the UI/harness can leave the user unable to interact normally.

## Consumption Safety

Observe first, consume second.

For bridge hygiene, distinguish `read` from `actioned`:

- `read` means Codex has already surfaced the message body in this chat turn by `check_inbox`, `wait_inbox`, `peek_inbox`, or equivalent tooling.
- `actioned` means Codex has completed the requested follow-up work.
- `handled` means Codex has sent the bridge reply, completed the work, or explicitly parked/blocked/displaced/rejected the item and recorded that disposition with `mark_handled`.
- These are not the same state. Do not leave a surfaced message unread in the bridge just because the follow-up work is still pending.

- Use `wait_inbox(..., mark_read=false)` or `peek_inbox` for discovery, demos, and wake loops.
- Do not call `check_inbox(..., mark_read=true)` unless you are ready to surface and act on every returned message immediately.
- If a test explicitly asks for `wait_inbox`, do not substitute `check_inbox`.
- After handling a message seen with `mark_read=false`, mark it read explicitly by id.
- After replying to, acting on, parking, blocking, displacing, or rejecting a substantive surfaced message, call `mark_handled` for that message id with the matching disposition.
- If a non-destructive read already surfaced the message body to Codex, mark it read in the bridge immediately, even if the requested action will be deferred to a later turn.
- When deferring the actual work, say so explicitly as `read but not actioned yet` rather than implying the message remains unread.
- A surfaced message with `read_at` but no `handled_at` remains response debt if it contains an action request, user request, smoke/test prompt, or explicit reply/confirmation ask.
- If a surfaced bridge message contains substantive review, a proposal, a requested answer, or a priority-changing signal, do not treat inbox hygiene as the completed task.
- After surfacing such a message, Codex must do one of these in the same work stretch:
  - send the bridge reply,
  - say explicitly that the item is `read but parked` and why,
  - or say explicitly that another higher-priority item displaced it.
- `ACTION_REQUEST` is a stronger subclass:
  - after surfacing an `ACTION_REQUEST`, Codex must not end the turn with only an inbox summary,
  - Codex must immediately choose and state exactly one disposition:
    - `acting now` and then make the next substantive action the implementation/reply,
    - `recorded and parked` with a durable `record_pending_bridge_action(...)` entry, priority, and reason,
    - `blocked` with the concrete blocker and any question needed to unblock,
    - `displaced` with the higher-priority task that is taking precedence,
    - or `rejected` with a clear reason if the request is unsafe or out of scope,
  - for bridge defects or user-visible workflow failures marked `urgent`, `high`, or `medium`, default to `acting now` unless there is a real blocker or a higher-priority active execution lock,
  - if only a narrow safe slice can be done immediately, implement that slice now and record the remaining slice as a pending action before stopping,
  - marking the message read only satisfies the receipt contract; it does not satisfy the `ACTION_REQUEST` contract.
- If the message did not actually change priority, resume the interrupted implementation or investigation immediately after the inbox report instead of stopping at status.
- A bridge inbox check is complete only when both states are true:
  - surfaced messages were marked read,
  - any substantive surfaced message was either actioned, explicitly parked, blocked, rejected, or displaced and then marked handled.
- The pre-final reminder treats current-turn `read_at` + missing `handled_at` on request/test messages as a final-response guard. Do not bypass it by summarizing only to the user; send the peer reply or record the disposition first.

For bridge coordination, distinguish `follow-on work exists` from `I am blocked waiting`:

- `follow-on work exists` means the other side sent something that may influence future work, but does not currently block progress.
- `I am blocked waiting` means I cannot safely continue my own next action until the peer answers a concrete question, confirms a gate, or resolves a specific condition.
- Do not collapse those states together in user-facing summaries or internal planning.
- If I cannot name the exact thing I am waiting for, I am not actually waiting; I should continue without claiming a wait state.

Inbox hygiene for bridge-related work:

- If the active conversation is itself about bridge behavior, routing, wake paths, hooks, or inbox hygiene, treat every user turn in that conversation as bridge-related.
- At the start of any bridge-related coding, design, audit, or protocol turn, do a lightweight non-destructive inbox awareness pass only as needed for safety/backpressure, but do not let routine inbox status consume the answer slot for a normal user prompt.
- Default flow for normal user prompts in bridge-focused conversations:
  - answer or execute the user-requested work first,
  - then do the end-of-turn inbox check before stopping.
- Exceptions that justify early inbox checking:
  - the user explicitly asked `check bridge inbox`,
  - Codex is about to send bridge traffic and needs to avoid known backpressure,
  - Claude or the user reported a routing/backpressure/session issue that may block the next step,
  - Codex is resuming a bridge task whose correctness depends on fresh inbox state.
- At the end of that turn, check the same buckets again before the final response.
- In bridge-focused conversations, do that end-of-turn inbox check before every final response, even if the user message was only a question about process or UX.
- Surface and handle any relevant messages, then mark each handled message read by id.
- Treat `check bridge inbox` during active bridge work as an interrupt, not as a stopping point:
  - check both buckets non-destructively,
  - surface and mark read any messages that were surfaced,
  - answer the inbox status,
  - then resume the previously active implementation, investigation, or unanswered user question unless the user explicitly says to pause, stop, wait, or only report status.
- If Codex was already answering a user question when an explicit `check bridge inbox` request arrived:
  - treat the earlier unanswered question as active return debt,
  - answer the inbox check,
  - then come back and answer the earlier question before closing the turn unless the user clearly superseded or withdrew it.
- Do not let an explicit inbox-check interrupt erase an already-open answer obligation.
- After any interrupt that surfaces a message but does not truly displace the current task:
  - explicitly classify the result as `resume`, `displaced`, or `parked`,
  - if the surfaced message creates follow-up work that will not be actioned immediately, record it in the pending-action ledger with `record_pending_bridge_action(...)`,
  - then return to the previously committed task as the next substantive action.
- If resuming would be unsafe because the inbox message changes priority or requires a restart, say that explicitly and switch to the newly higher-priority work.
- Do not enter a persistent `wait_inbox` loop in the main working chat unless the user explicitly requests a short smoke test.
- Continuous monitoring is only active while a live turn is blocked inside `wait_inbox`. If Codex sends a final answer and ends the turn, Codex is not continuously monitoring.
- Workflow hooks can remind Codex to check or enter `wait_inbox`, but they cannot resume an already-ended turn or create continuous monitoring by themselves.
- If bridge-watch mode is on, treat the hook output as a high-salience reminder for explicit watch tests only, not as proof that a persistent loop belongs in the main chat.
- In this Codex Desktop thread, trivial nudges can bypass the workflow reminder path entirely. Do not claim the pre-response hook is a reliable wake trigger unless the log proves it fired for that turn.
- If the user asks for continuous bridge monitoring, explain that a blocking loop captures the main chat and recommend an external wake/notification path instead.
- Hook v1 is reminder-only: it may remind Codex to run inbox hygiene, but it must not inspect message bodies, mark messages read, or call `consume_inbox.py`.
- Current Codex Desktop `notify` hook status: tested and not firing in this Desktop thread. Do not rely on it unless `codex-bridge-reminder.log` shows an automatic entry with `force=False noToast=False`.
- Active hook strategy is workflow-rule based: `AGENTS.md` requires `codex_pre_response.ps1` and `codex_pre_final.ps1` around bridge-related responses.
- A hook may evolve to show non-destructive receipt/status summaries only after the receipt tools exist; it must never silently consume bridge messages.
- If Claude or the user reports that Codex's bucket is blocking sends, immediately check Codex's private GUID bucket and project bucket non-destructively.
- If unread messages are present, surface them, handle them, and mark them read by id before doing more bridge work.
- If unread messages are present and the work cannot be completed in the same turn, mark them read anyway once surfaced, then track the remaining task separately in the conversation.
- If both buckets are already empty, send a `BACKPRESSURE_STATUS` or `ROUTE_REPAIR` update to Claude with the checked buckets and ask them to retry from fresh state.
- Inbox hygiene commands must produce an explicit machine-readable result. A
  silent zero-output command is a hygiene failure, not an empty inbox.
- `py -3 tools\agent-bridge\agent_bridge.py check-inbox ... --format json` is
  now the supported local CLI shape. If it emits no JSON, stop and inspect the
  CLI/tool path before claiming the inbox is empty.
- The pre-response and pre-final hooks are canaries for that contract: they must
  probe `agent_bridge.py check-inbox --help` and fail loudly if the command does
  not expose the expected `--agent` and `--format` JSON-hygiene interface.
- When a peer names a specific unread/blocking message id, search the whole
  `inbox-<agent>.jsonl` for that id and all unread rows before trusting the
  active-bucket summary. Backpressure can be caused by stale/private buckets
  that ordinary active-session hygiene missed.

No-silent-success process rule:

- For bridge-owned CLIs, hooks, watchers, wake helpers, probes, migrations, and
  dashboard/admin helpers, exit code 0 is not enough. A successful process must
  also emit or write a verifiable success artifact: JSON result, audit row,
  receipt transition, health-panel field, test assertion, or explicit
  diagnostic line.
- Early-detection canaries should validate the interface shape before relying
  on a process:
  - `--help` exposes required subcommands/options,
  - JSON output parses and contains `ok` plus `status`,
  - state-mutating commands produce audit/receipt rows,
  - background helpers update health/heartbeat/fire-history fields,
  - watcher wake success is tied to `seen_at` / `read_at`, not process spawn.
- Any command that returns 0 with empty stdout/stderr and no expected state
  artifact must be treated as `silent_failure_suspected` until proven otherwise.
- If a silent failure is found or remediated in bridge communication, send the
  paired peer a `ROOT_CAUSE`, `RISK_DELTA`, or `IMPLEMENTATION_UPDATE` with the
  detection gap and the new canary.

Committed-task rule:

- If Codex says `I’m doing X now`, treat that as a real commitment, not conversational filler.
- After making that commitment, Codex must do one of these before drifting into adjacent work:
  - make the edit or perform the action,
  - state explicitly that it is blocked and why,
  - state explicitly that a newer higher-priority interrupt displaced it.
- Do not announce an edit as in progress unless the very next substantive action is the edit itself.
- Keep a tiny active-work stack mentally:
  - current committed task,
  - whether it is `done`, `blocked`, or `displaced`.
- After interrupts such as `check bridge inbox`, automatically resume the interrupted committed task unless the new message explicitly changes priority.
- If priority changes, say that explicitly in the chat, e.g.:
  - `Inbox check surfaced a higher-priority action request, so I’m switching from heuristic edit to that.`
- If the interrupt was only status or hygiene and did not change priority, resume the committed task immediately rather than treating the interruption as a stopping point.
- Handling an interrupt is not completion of the interrupted task.
- After answering an interrupt, the very next substantive action must be one of:
  - resume the interrupted edit or implementation step,
  - explicitly mark it `displaced`,
  - explicitly mark it `parked`.
- If the interrupt created a deferred obligation, write it to the pending-action ledger before resuming so the obligation survives long work stretches and compaction.

Declared wait-state rule:

- If Codex is truly holding for a reply from Claude before progressing its own work, Codex must send a brief `WAIT_DECLARED` bridge message stating:
  - that Codex is waiting,
  - what specific message id, decision, or condition it is waiting on,
  - why Codex cannot proceed without that reply.
- A real wait state must be concrete enough to bridge.
- If the wait cannot be articulated that concretely, it is not a blocking wait state and must not be described as one.

`WORKING_ON_IT` contract:

- Treat `WORKING_ON_IT` as a work-state contract, not a courtesy ping.
- After sending `WORKING_ON_IT`, Codex enters a protected execution window for the named task.
- During that window, Codex should only do one of these:
  - execute the promised work,
  - answer a true higher-priority interrupt,
  - renew the `WORKING_ON_IT`,
  - close it with `IMPLEMENTATION_UPDATE`, `SPEC_REVIEW_RESULT`, `PARKED`, `DISPLACED`, or `TIMED_OUT`.
- Do not let general status discussion, repeated planning, or routine inbox hygiene become the new main task while a `WORKING_ON_IT` is open.
- Every `WORKING_ON_IT` must name the next concrete checkpoint when possible, e.g.:
  - first patch landed,
  - tests running,
  - commit created,
  - review drafted.
- If Codex gave an ETA, Codex must either:
  - hit that checkpoint and close the loop before the ETA, or
  - send a renewed `WORKING_ON_IT` before the ETA expires.
- If no ETA was given, Codex must still send a renewal once it becomes clear the work will not close in the same work stretch.
- `WORKING_ON_IT` does not authorize silence. It only buys time until the next explicit state transition.
- If Codex is interrupted by `check bridge inbox` or a status question while a `WORKING_ON_IT` is open:
  - answer briefly,
  - classify the task as `resume`, `displaced`, or `parked`,
  - then follow that classification immediately.
- Default classification is `resume`. If Codex does not explicitly say otherwise, it must return to the protected task immediately after the interrupt.
- A stale `WORKING_ON_IT` is a coordination miss even if the user did not complain.

`WORKING_ON_IT` watchdog strategy:

- Do not use a blind periodic heartbeat as the primary fix.
- Preferred backstop is a conditional watchdog that arms only when a `WORKING_ON_IT` is sent.
- The watchdog should fire at the declared ETA, or at a conservative default threshold if no ETA was given.
- The watchdog reminder should ask for a valid next state, not a vague status:
  - `IMPLEMENTATION_UPDATE`
  - renewed `WORKING_ON_IT`
  - `PARKED`
  - `DISPLACED`
  - `TIMED_OUT`
- Treat the watchdog as detection/escalation only. It does not replace the protected execution-window rule above.

Interrupt-discipline rule:

- Bridge coordination must not become the reason execution stalls.
- During active implementation windows (`WORKING_ON_IT`, `IMPLEMENTATION_START`, or an explicit committed edit), treat bridge traffic by class:
  - `urgent`:
    - true blockers
    - synchronous `confirm ... before ...` gates
    - watchdog reminders at threshold
    - explicit user override
    - these may interrupt execution immediately
  - `important but non-urgent`:
    - audits
    - spec reviews
    - status digests
    - design proposals
    - these should usually be `read and parked` until the next checkpoint
  - `informational`:
    - ACKs
    - closed-loop summaries
    - passive status with `ACTION_REQUESTED: none`
    - these must not steal the active work slot
- Default rule: execution wins over non-urgent coordination traffic.
- If a non-urgent message is surfaced during protected execution:
  - mark it read,
  - if needed, send `WORKING_ON_IT` or explicitly park it,
  - resume the protected task immediately.
- Process non-urgent coordination at explicit checkpoints when possible, e.g.:
  - first patch landed,
  - tests started or finished,
  - commit created,
  - renewal point for an open `WORKING_ON_IT`.
- Sender-side quiet mode is part of the same discipline:
  - if Codex knows Claude is in a protected execution window, avoid sending non-urgent traffic unless it changes priority, is the one allowed watchdog reminder, or the user explicitly asked for live relay.

Status-digest reciprocity rule:

- The user must not be the manual relay for cross-peer status.
- If Codex writes user-facing text that names Claude-owned work, Claude-awaited replies, or a joint open-items summary, Codex must bridge the same status content to Claude in the same work stretch.
- Trigger examples:
  - `active waits remaining`
  - `still in flight`
  - `waiting on Claude`
  - `Claude is working on X`
  - `I will review/audit when Claude ships Y`
  - end-of-turn lists that include open items Claude owns or is expected to answer
- Preferred bridge format is a neutral `STATUS_DIGEST`:
  - frame it as `open joint items between Claude and Codex`
  - list one item per open thread
  - include `no items awaiting Claude action` when that is true
  - avoid `you owe me` phrasing
- Use `WAIT_SUMMARY` only when the digest is specifically about blocking waits rather than general joint status.
- This rule is about peer visibility, not urgency:
  - the digest should still respect the traffic classes above
  - if Claude is in a protected execution window, send the digest only when it materially prevents user-relay or confusion, otherwise park it until the next checkpoint
- Reciprocal expectation applies to Claude as well: if Claude tells the user about Codex-owned work, Codex should receive the same digest without waiting for the user to paste it across.

Outbound peer-review verification rule:

- When Codex closes a shared Agent Bridge roadmap/security/review task and sends Claude a `REVIEW_REQUEST`, `PHASE_DONE`, `READINESS_ASSESSMENT`, `STATUS_DIGEST`, or other reply-expected peer packet, Codex must verify the outbound row before telling the user the handoff is complete.
- Minimum verification before final response:
  - capture and report the bridge message id,
  - confirm `send_to_peer` returned `accepted: true` / `status: queued`,
  - confirm the row exists in the peer inbox or audit log with the intended target session/pair,
  - distinguish `sent`, `seen`, `read`, `handled`, and `replied` explicitly.
- If the message is sent but not yet replied to, say `sent; awaiting peer reply` rather than implying Claude reviewed it.
- If a reply is expected but not yet received, create or preserve response debt through the ledger/response-debt guard instead of relying on memory.
- If outbound verification fails, do not claim the peer was notified; retry once through the normal bridge path, then surface the failure and record a pending action.
- This rule exists because a correct `send_to_peer` is only delivery into durable bridge state. It is not proof that Claude has reviewed, answered, or marked the item handled.

Peer-cognition verification rule:

- Do not say or imply `Claude knows`, `Claude was told`, `Claude is reviewing`,
  or `peer is aware` from `send_to_peer` success alone.
- Treat bridge lifecycle states as distinct:
  - `queued`: durable row exists, but the peer may have no live awareness,
  - `toasted` / watcher `seen_ids`: the watcher noticed or notified, but the
    peer agent may still not have read the body,
  - `seen_at`: a tool or wake path observed the row,
  - `read_at`: the peer surfaced or consumed the body,
  - `handled_at`: the peer acted, parked, blocked, displaced, rejected, or
    replied.
- If a relayed Agent Bridge decision remains `queued` with no `read_at` after a
  short sanity window, report `sent but not read yet`, inspect the peer wake path
  before assuming cognition, and preserve the relay as response debt.
- If the peer-side wake path is Monitor-owned, verify the Monitor is bound to
  the current private session bucket before trusting it. A watcher toast or
  stale Monitor process is not proof of peer cognition.
- When this distinction causes a miss or near-miss, bridge a `HEURISTIC_SYNC` or
  `ROOT_CAUSE` once the peer can receive it, and include the stuck message id,
  lifecycle state, and wake-path evidence.

What's-next / status-report rule:

- Treat user prompts like `what now?`, `what's next?`, `show roadmap`, `where are we?`, `status?`, `status report`, `what remains?`, `next steps?`, or `what should we do now?` as bridge-coordination prompts when the active work is Agent Bridge, shared wake/routing behavior, or a Claude/Codex paired task.
- Before answering one of those prompts, reconcile fresh state:
  - run normal bridge inbox hygiene for the active Codex private bucket and project bucket,
  - fold any newly surfaced peer status into the answer,
  - check the active execution task or pending-action ledger if the prompt is asking what Codex should do next.
- For Agent Bridge work, next-step/status answers are always relayed. After
  answering the user, send Claude the same decision as `STATUS_DIGEST`,
  `READINESS_ASSESSMENT`, or `ACTION_REQUEST` whenever the prompt asks what to
  do next, what remains, what the roadmap is, or what the recommended next step
  should be.
- This relay is mandatory even when the recommendation looks local-only,
  obvious, or already implied by the ledger. The goal is to prevent Claude and
  Codex from diverging on roadmap priority after the user receives guidance.
- Concrete Agent Bridge design advice is also mandatory relay traffic. If Codex
  recommends or revises a setting name, schema shape, enum value, migration
  rule, default behavior, implementation order, fallback path, or defect
  remediation plan for Agent Bridge, Codex must send Claude the same
  recommendation in the same work stretch unless the user explicitly says not
  to relay it. Do not wait for the user to paste the idea across.
- This includes brief advisory answers that do not edit files. If the answer
  would help Claude keep implementation, docs, tests, or roadmap decisions in
  sync, bridge it as `STATUS_DIGEST`, `SPEC_PROPOSAL`, `READINESS_ASSESSMENT`,
  or `HEURISTIC_SYNC` as appropriate.
- The relay must include:
  - the user-facing recommendation,
  - the inbox/ledger state used to derive it,
  - any blocked send/backpressure state,
  - and whether Claude is expected to act, review, or only stay informed.
- If backpressure blocks the relay, record the full outbound body in the
  pending-action ledger before ending the turn and retry when the peer drains.
- Outside Agent Bridge / paired-thread coordination, only relay next-step
  answers when they mention:
  - work Claude completed or is expected to do,
  - work Codex completed that Claude should account for,
  - joint roadmap priority,
  - a next-step recommendation for shared bridge behavior,
  - a concrete bridge setting/schema/migration/default recommendation,
  - or any change to which path is considered primary versus fallback.
- Do not auto-relay purely local status answers that do not mention Agent Bridge, Claude-owned work, shared bridge behavior, or paired-thread coordination.
- Receipt disposition:
  - inbound `IMPLEMENTATION_SUMMARY`, `IMPLEMENTATION_UPDATE`, `PHASE_DONE`, `STATUS_DIGEST`, `READINESS_ASSESSMENT`, `ROADMAP_ALIGNMENT`, and `TEST_RESULT` messages are substantive status traffic,
  - after surfacing them, mark them `handled` once their contents are folded into the current answer, roadmap, or active-task context,
  - never mark them `ignored` merely because `ACTION_REQUESTED: none` or because they contain words like `status`, `ack`, or `summary`.
- Only pure wake/timing pings such as `WAKE_TEST`, `Watcher says check bridge inbox`, or empty-inbox keepalive probes may be marked `ignored`.
- Classify by the top-level message type, subject, and sender intent. Do not
  mark a substantive message `ignored` merely because its body quotes
  `WAKE_TEST`, `Watcher says check bridge inbox`, or another ignored-only token
  as an example.
- If a status-report answer changes what Codex thinks is next, the next substantive action after the answer must follow that classification: `continue`, `park`, `block`, or `displace`.

Workflow-strategy sync rule:

- If Codex creates or changes a bridge workflow strategy, operating rule, or durable coordination mechanism that Claude should know about, Codex must:
  - update the local heuristics/spec/doc first,
  - send Claude the matching `HEURISTIC_SYNC`, `SPEC_PROPOSAL`, or `IMPLEMENTATION_UPDATE` in the same work stretch,
  - explicitly ask Claude to mirror the strategy or propose the minimal symmetric contract if a direct mirror is not appropriate,
  - include a short justification for why symmetry matters, e.g. preventing drift, preserving compaction safety, or keeping interrupt handling consistent across both agents.
- Do not treat “shared implementation sent” as equivalent to “mirror requested”.
- If the strategy is bridge-core rather than Codex-local UX, the default assumption is that Claude should mirror it unless there is a concrete reason not to.
- The reciprocal expectation applies to Codex as well when Claude is the one executing.

Waypoint rule:

- A successful checkpoint is not automatically a stopping point.
- If the user asked Codex to `keep going`, `iterate until done`, or gave another open-ended execution instruction, treat a commit, green test run, or completed slice as a waypoint, not an endpoint.
- After every substantive checkpoint, Codex must explicitly decide one of:
  - `continue next slice`
  - `blocked`
  - `done because the user-requested scope is actually complete`
- If known in-scope work still remains and there is no real blocker, default to `continue next slice`.
- Do not let `slice complete` silently become `request complete`.
- Before ending a long-running implementation turn after a commit or verification checkpoint, Codex should sanity-check:
  - are there still known remaining items in scope?
  - did the user explicitly ask for continued iteration?
  - is there a natural next slice already identified?
- If the answers are `yes`, `yes`, and `yes`, continue rather than close.
- Only stop after a successful checkpoint when one of these is true:
  - all requested work is complete,
  - a real blocker exists,
  - the user redirected the task,
  - the next step has hidden consequences that require explicit confirmation.
- Before ending a turn after a checkpoint, Codex must also check the pending-action ledger for its own open bridge obligations:
  - call `next_pending_bridge_action(owner_agent="codex")` when available, or otherwise inspect the durable pending ledger,
  - treat the returned item as the machine-selected active candidate rather than relying on memory or the most recent discussion topic,
  - if actionable in-scope items remain and there is no real blocker, continue by doing that top pending item instead of stopping,
  - if the item should not be worked now, explicitly say why it remains `parked`, `displaced`, or `blocked`.
- Stronger universal rule:
  - this ledger check is not limited to checkpoint-shaped turns,
  - before every final response in a bridge-focused conversation, Codex must run the same `next_pending_bridge_action(owner_agent="codex")` check,
  - if the returned top item is actionable and in scope, Codex must keep going instead of ending the turn,
  - only after that item is worked, or explicitly classified as `blocked`, `parked`, or `displaced`, may Codex actually stop.
- Implementation note:
  - `I finished what the user just asked for` is not by itself permission to stop if the ledger still contains actionable Codex-owned work,
  - the ledger check happens after the requested turn-local work is complete and before the final response is sent.
- During an active implementation stretch, the highest-priority actionable ledger item is the default active task.
- Aggressive drain rule:
  - if the inbox is clear and the top Codex-owned ledger item is actionable, Codex should keep executing it by default without waiting for a user nudge,
  - stopping is allowed only when:
    - the ledger has no actionable Codex-owned items,
    - the top item is explicitly `blocked`, `parked`, or `displaced`,
    - the user explicitly redirected or paused the work,
    - or the next step has hidden consequences that require confirmation.
- Post-commit / post-test continuation rule:
  - after a commit, green test run, or other successful checkpoint, immediately re-run `next_pending_bridge_action(owner_agent="codex")`,
  - if it returns an actionable item, that item becomes the next default work block.
- User process/debugging questions do not automatically replace that active task:
  - answer them briefly,
  - then immediately resume the highest-priority actionable ledger item,
  - unless the user explicitly reprioritized the work or the answer revealed a real blocker or hidden-risk decision.
- Explicit reprioritization rule:
  - a user process/debugging/meta question does **not** by itself reprioritize away from the active ledger item,
  - treat those turns as brief interrupt work unless the user clearly says to pause, switch, stop, or implement the process/meta topic itself,
  - after answering a meta/process question, the default next action is to resume draining the top actionable ledger item in the same turn.
- Supersession note:
  - this supersedes any looser interpretation that `the latest user prompt automatically becomes the whole-turn main task`,
  - the latest prompt controls the next answer, but not the post-answer execution path unless it clearly changes implementation priority.
- Do not let meta-work, status narration, or workflow discussion quietly become the new main task when a higher-priority actionable ledger item already exists.
- If a different item becomes the new active task, say why explicitly:
  - `Reprioritizing from SH2 auto-rollback to <item> because <reason>.`
- Do not rely on memory alone for “what’s next” once a turn is about to end.
- The pending-action ledger is the final anti-drop backstop for work Codex has already committed to actioning.
- If the user or Claude enumerates Codex-side pending work, reconcile the durable pending-action ledger in that same work stretch:
  - add newly surfaced Codex obligations that are missing,
  - resolve ledger entries that are already completed,
  - correct priorities or details if the external list is more accurate than Codex's local ledger.
- Do not let the ledger stay empty or stale when Codex already knows about open Codex-side obligations.
- Workflow implication:
  - the ledger is not just a memory aid; it is the default source of truth for `what Codex should do next`,
  - if the current conversation turns meta while actionable ledger items remain, the turn should snap back to the top item after the meta answer unless the user clearly changed priority.
- Rehydration requirement:
  - the pre-response/pre-final reminder path should surface a compact bridge digest when available:
    - bridge state
    - active private/project buckets
    - heuristics/rule version marker
    - top pending ledger item
  - on pre-final, if execution is idle and the Codex ledger has a top pending item, the reminder must surface an explicit `FINAL-GUARD` warning:
    - do not send the final response yet,
    - either work the top item,
    - or classify it as `blocked`, `parked`, or `displaced`.
  - Treat that warning as a failed stop-condition, not as informational text.
  - after compaction, the carry-forward summary should preserve at least:
    - the top ledger item id + summary,
    - whether inbox was clear,
    - and the currently active bridge-rule digest or equivalent reminder.

Unexpected behavior / log-first rule:

- When a strange bridge issue appears, inspect durable logs before settling on a root cause.
- Triggers include:
  - wrong-thread wake or wrong-project routing,
  - unexpected pairing/supersession,
  - messages marked delivered/read/handled contrary to what the UI shows,
  - watcher wake success/failure that contradicts user observation,
  - any "this should be impossible" behavior.
- Minimum investigation:
  - inspect `~/.agent-bridge/session.json` for active sessions, pairs, trusted parent, thread ids, and supersession history,
  - inspect `~/.agent-bridge/state/messages.jsonl` around the relevant timestamps/session ids/thread ids,
  - inspect the relevant `inbox-*.jsonl`, `watcher-state.json`, and `implementation-journal.json` when delivery, read/handled state, wake, or implementation sync is involved,
  - reconstruct a short timeline with concrete timestamps and event names before claiming root cause.
- Do not rely only on current memory, current UI appearance, or one live process/env sample when historical activity could explain the failure.
- If logs are unavailable or inconclusive, say so explicitly and label the conclusion as a hypothesis.

Persistent-issue breakthrough rule:

- When Codex, Claude, or the user uncovers a breakthrough on a persistent or recurring bridge issue, share it with the paired peer automatically.
- A breakthrough includes:
  - a root-cause invariant that explains repeated failures,
  - log evidence that overturns the prior theory,
  - discovery that a safety rule or guard predicate is invalid,
  - a durable diagnostic technique that would have shortened the investigation,
  - or a newly proven constraint that changes the roadmap or implementation priority.
- Send it even if there is no immediate implementation request. Use `ROOT_CAUSE`, `RISK_DELTA`, `HEURISTIC_SYNC`, or `BREAKTHROUGH_FINDING` as appropriate.
- Include enough evidence for the peer to update its own model:
  - concrete timestamps or event names when available,
  - the old assumption,
  - the corrected invariant,
  - and the practical consequence for future behavior.
- This rule is symmetric: if Claude finds such a breakthrough, Codex should receive it; if Codex finds one, Claude should receive it.

Closed-on-send exclusion:

- When summarizing `still in flight`, `open threads`, or `waiting on Claude`, do not include messages whose `ACTION_REQUESTED` is `none`.
- Also exclude messages whose `ACTION_REQUESTED` is explicitly optional, such as `if you want X`, `if desired`, or equivalent non-blocking wording.
- Treat `AUDIT_RESULT`, `IMPLEMENTATION_UPDATE`, `CLOSEOUT`, and similar closed-on-send messages as complete unless they include a concrete required action.
- If a message communicates verdict, context, or optional follow-up only, do not present it to the user as an active wait state.

## Routing Heuristics

Bridge messages automatically when the user would otherwise need to paste them manually.

High-value auto-send categories:

- `ROOT_CAUSE`
  - a genuine diagnosis that changes what we think is broken
- `PROTOCOL_SYNC`
  - shared operating rules, consumption rules, or message-shape changes
- `HEURISTIC_SYNC`
  - changes to bridge-trigger behavior or what should auto-send
- `SPEC_REVIEW_REQUEST`
  - new or materially changed bridge specs, protocol docs, lifecycle docs, or design notes that need peer review before implementation
- `IMPLEMENTATION_START`
  - starting code changes that implement a shared bridge design, protocol behavior, or cross-agent workflow
- `IMPLEMENTATION_UPDATE`
  - finishing or committing code changes that implement shared bridge behavior, especially when the peer agent was expected to review the design
- `RESTART_ACK`
  - confirmation that a restart fixed a previously broken bridge path
- `AUDIT_RESULT`
  - verification of another agent's patch, diagnosis, or test result
- `READINESS_ASSESSMENT`
  - a score, acceptance judgement, hardening/readiness rating, or 10/10 feasibility analysis that changes what work is considered blocking versus polish
- `RISK_DELTA`
  - a newly identified live defect, production-risk cap, or test-coverage gap that materially changes the hardening score or next-step priority
- `BREAKTHROUGH_FINDING`
  - evidence or a corrected invariant that resolves a persistent/recurrent issue, invalidates a previous theory, or changes how future debugging should proceed
- `ACTION_REQUEST`
  - a concrete next step the other agent needs to do now
- `PHASE_DONE`
  - a meaningful milestone ready for peer review

Directory classifier:

- Any commit or substantive implementation checkpoint touching
  `tools/agent-bridge/` is shared bridge behavior by definition.
- Do not require a separate semantic classification pass before notifying the
  peer; the path is the trigger.
- Before ending the turn after such a checkpoint, send the matching bridge
  `IMPLEMENTATION_UPDATE`, `SPEC_PROPOSAL`, `READINESS_ASSESSMENT`, or
  `STATUS_DIGEST`, or record a pending bridge action explaining why the sync is
  parked.
- This is a structural backstop against missing cross-agent updates after
  bridge-internal commits.

Bridge spec discipline:

- When drafting or materially changing a bridge design spec, protocol spec, lifecycle doc, or trigger heuristic, send `SPEC_REVIEW_REQUEST` to Claude automatically.
- When beginning implementation of a shared bridge design, send `IMPLEMENTATION_START` before editing.
- After committing shared bridge behavior, send `IMPLEMENTATION_UPDATE` with the commit hash, verification, and known follow-up gaps.
- Treat a substantive bridge commit as incomplete until the matching peer sync message has been sent in the same work stretch.
- When giving or revising a bridge hardening score, smoke-test confidence score, roadmap-readiness judgement, or "can this reach 10/10 yet?" answer, send `READINESS_ASSESSMENT` to Claude automatically.
- If the assessment names a live defect or a test gap that caps the score, also include `RISK_DELTA` details and whether the item is required for resilience or merely roadmap/config polish.
- Distinguish current operational confidence from full roadmap completeness; do not collapse "smoke coverage can improve" into "all roadmap phases must be complete" without stating which missing items actually block hardening.
- When the user asks Codex to evaluate, merge, prioritize, or give "thoughts on"
  Claude's Agent Bridge roadmap/proposal/review, treat Codex's answer as shared
  bridge coordination. After answering the user, send Claude the same decision as
  `READINESS_ASSESSMENT`, `STATUS_DIGEST`, or `SPEC_PROPOSAL` in the same work
  stretch unless the user explicitly says not to relay it.
- `PROTOCOL_SYNC` or `PROTOCOL_SYNC_ACK` with `ACTION_REQUESTED` containing both `confirm` and `before` is a synchronous coordination gate.
- Treat that subclass as reply-required within the normal ack window, not optional background traffic, because the sender is holding its next action behind your confirmation.
- Do not leave a `confirm ... before ...` gate unanswered while discussing adjacent bridge work.
- When changing this heuristics file, send `HEURISTIC_SYNC` to Claude and ask whether the same rule is useful on Claude's side.
- If Codex realizes after the fact that a message should have been bridged, send the missed bridge message immediately, then update this heuristics file in the same turn so the miss becomes an explicit future trigger.

Canonical completion / tandem review rule:

- For Agent Bridge roadmap, security, wake/routing, pairing, dashboard, or
  hardening work, "done" means the canonical completion standard in
  `tools/agent-bridge/REFACTOR_PLAN.md`, not just local confidence.
- Codex must echo and use that standard when the user asks to finalize,
  certify, score, iterate to 10/10, or complete the Agent Bridge roadmap.
- Codex must work in tandem with Claude by default:
  - send `IMPLEMENTATION_START` before material edits,
  - send `IMPLEMENTATION_UPDATE`, `REVIEW_REQUEST`, or
    `READINESS_ASSESSMENT` at meaningful checkpoints,
  - include changed files, tests/run results, failure-path coverage, risks,
    and open blockers,
  - verify the outbound row landed before telling the user Claude was notified.
- Background agents should assist where they materially improve speed, coverage,
  decomposition, or independent validation. Use them especially for cold review,
  failure-path review, and stranger scoring of shared bridge changes.
- Two or more background agents acting as strangers must rate the implementation
  10/10, or name concrete blockers, before Codex calls the implementation final.
- Waiting on background agents is an active execution state. If an agent does
  not return by the first checkpoint, ask that agent for an ETA, record the ETA
  or missing response in the turn notes/ledger, check back at that ETA, and ask
  for a renewed ETA if the result is still not ready. Do not yield a final
  response that merely says "waiting on agents" unless the wait is explicitly
  classified as blocked/parked and the recoverable state is durable.
- Claude review is not optional for finalization. If Claude cannot review
  immediately because of backpressure or context loss, record the full outbound
  body in the pending-action ledger and classify the item as blocked/parked
  rather than silently treating the phase as complete.
- This standard applies to all Agent Bridge implementations, not just final
  roadmap certification. Small patches can use proportionate tests/review, but
  still need peer notification, ledger state, and independent stranger review
  before final closeout.
- Always iterate roadmap and todo/pending-ledger state so as much safe work as
  possible is completed without pausing for more user input. After one item is
  completed, parked, or blocked, immediately continue to the next safe
  highest-impact item unless the next step requires a non-obvious
  product/security decision, external credentials, destructive action, or
  explicit user choice.
- When implementation is complete, notify Claude for review and iterate change
  requests until Claude would rate it 10/10. Do the same with two or more
  background stranger reviewers. If any reviewer names blockers, continue
  implementation or record the blocker explicitly in the ledger.
- Final 10/10 status is canonical only when Codex, Claude, two or more
  background stranger agents, and the user agree.
- If Codex, Claude, or background stranger reviewers disagree, send or expect
  `RISK_DELTA`; resolve by code, tests, docs, or explicit user-approved scope
  change before signoff.

Optional thread-close convention:

- When an exchange is complete and no further reply is expected, prefer making that explicit in the bridge message rather than leaving closure implicit.
- Acceptable forms:
  - trailing literal `EXCHANGE_CLOSED` or `THREAD_CLOSED`
  - an explicit status such as `STATUS: confirmed-thread-closes`
- Use this especially after `PROTOCOL_SYNC`, caveat confirmations, and other coordination exchanges that could otherwise be misread as still open.

Do not auto-send:

- routine empty-inbox updates
- low-signal acknowledgements with no state change
- repeated restatements of already-known session ids or config

## Routing Discipline

- Prefer project or active GUID session buckets; do not route new bridge traffic through `default`.
- Treat explicit `default` sends or destructive operations as protocol errors, not fallbacks.
- If a send is blocked by unread mail, inspect which bucket is blocked before assuming transport failure.

## Known Limits

- Codex does not have a fully proven external wake path into an idle active chat yet.
- Practical Codex wake today is:
  - a running `wait_inbox` loop in-session, or
  - best-effort `wake_codex.ps1` through watcher when a protected parent thread
    id is configured; without that target it is active-window scoped and may
    wake the wrong Codex chat, or
  - the user nudging Codex to check/read after Claude sends.
- Until a true harness-level trigger or reliably targeted thread wake exists,
  do not overclaim Codex auto-wake as symmetric with Claude Monitor.
- Distinguish two mechanisms clearly:
  - keepalive loop:
    - the chat is still alive inside `wait_inbox(...)`
    - this is the primary wake mechanism
    - chained `wait_inbox` calls keep the thread near-continuously listening
  - post-turn recovery:
    - the turn has already ended and the chat is detached
    - hooks such as `Stop` may run external recovery logic
    - that is not the same as guaranteed re-entry into this same live thread
- Treat `Stop` hooks or similar post-turn hooks as recovery helpers only:
  - useful for logging, reminders, or external orchestration
  - not a substitute for maintaining the active `wait_inbox` loop
