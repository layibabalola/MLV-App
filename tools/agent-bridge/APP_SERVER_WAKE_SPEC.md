# App Server Wake Spec - SendKeys-Free Codex Wake Prototype

**Status:** Prototype proven; bridge helper and watcher provider wiring added.
Desktop live repaint classification still requires user-visible observation
before making App Server wake the default.
**Authors:** Codex
**Tier:** Tier 1 - replacement candidate for Codex Desktop wake delivery.
**Depends on:** `WAKE_HARDENING_SPEC.md`, `BRIDGE_WAKE_RECOVERY_LOOPS_SPEC.md`,
`AUTO_PAIR_SPEC.md`, and Codex App Server availability.
**Motivation:** Replace the current Codex Desktop wake path, which uses
deeplink plus SendKeys/UIA composer injection, with a protocol-level App Server
injection path that can deliver bridge messages without typing into the
composer.

---

## Summary

Codex Desktop is not a separate agent stack. It runs an embedded
`codex app-server` and uses the same App Server JSON-RPC protocol family as
other Codex clients. The current bridge still has a legacy option that wakes
Codex Desktop by opening a thread deeplink and injecting the text `check bridge
inbox` into the desktop composer via SendKeys/UIA. That path is fragile: it can
steal focus, clobber draft text, target the wrong visible window if targeting
fails, and requires receipt-based retry logic to prove delivery.

Incident note: on 2026-05-01 UTC, a Claude `PLAN_ACK` routed to the Codex
`mlv-app` project bucket triggered the legacy `wake_codex.ps1` watcher path
while the user was working in an unrelated Codex Desktop project. Preflight
proved only composer availability and draft preservation; it did not prove
project/thread identity. The result was a stray `check bridge inbox` steering
message in the wrong Codex chat. This spec treats that as a design failure of
focus-inferred wake delivery, not as a one-off UI race.

This spec defines a replacement research track:

1. Run a sibling `codex app-server --listen ws://127.0.0.1:<port>` on Windows.
2. Connect a small bridge-owned client to that server.
3. Resume or target the same thread that Codex Desktop has open.
4. Start agent work through `turn/start` or steer an already-active turn with
   `turn/steer`.
5. Empirically determine whether Codex Desktop updates live when it has that
   thread loaded, or whether it only observes the injected content after a
   refresh/reopen.

If live update works, this path can replace `wake_codex.ps1` for Codex
Desktop. If live update does not work, App Server injection still replaces the
composer write, and a much smaller non-text UI nudge may remain only to refresh
Desktop.

Important correction: `turn/start` from the bridge sidecar runs on the App
Server process the sidecar connected to. It only produces a visible Desktop
chat response if that process is sharing the same target thread with Desktop in
a way Desktop observes live. This spec therefore treats sibling App Server
injection as a hypothesis to test, not as proven Desktop wake.

---

## Verified Locally

- Codex Desktop is installed and running on Windows.
- The running Desktop process tree includes:
  `resources\codex.exe app-server --analytics-default-enabled`.
- The installed CLI reports `codex-cli 0.111.0`.
- `codex app-server --help` supports:
  `--listen stdio://` and `--listen ws://IP:PORT`.
- The public App Server docs list `turn/start`, `turn/steer`,
  `thread/inject_items`, `thread/loaded/list`, `thread/unsubscribe`, and MCP
  status/tool endpoints.

## Cross-Checked From Prior Analysis

- OpenAI's App Server engineering post says Codex Web, CLI, IDE extension, and
  Desktop App are powered by the same Codex harness and App Server protocol.
- Codex app settings documentation says the app, CLI, and IDE share MCP
  configuration through `config.toml`.
- Open issues `openai/codex#15299`, `#17543`, and `#18056` show that MCP
  notification-to-session injection is not yet a supported native channel path.
- The `raysonmeng/agent-bridge` design already proves the sibling App Server
  pattern for Claude Code plus Codex TUI/CLI by owning a WebSocket app-server
  proxy and sending `turn/start`.

## Needs Runtime Profiling

- Whether a sibling App Server can resume or inject into the exact Desktop-open
  thread without creating a disconnected duplicate runtime.
- Whether Desktop live-updates when it is already subscribed to the target
  thread and the sibling server injects content.
- Whether Desktop refresh/reopen is enough to show externally injected content
  if live update does not work.
- Whether two App Server processes can safely touch the same persisted thread
  on Windows without file-lock, ordering, or rollout corruption issues.

---

## Goals

- Eliminate text injection into the Codex Desktop composer for bridge wake.
- Require wake delivery to be project/thread-targeted at the protocol layer,
  never inferred solely from the foreground window, focused composer, or
  successful draft-preservation preflight.
- Preserve existing bridge hardening above the wake layer: provenance,
  active-session routing, receipt verification, breaker/rate-limit behavior,
  pause gating, and recovery loops.
- Keep `wake_codex.ps1` available as a fallback until App Server delivery is
  proven and regression-tested.
- Prefer a bridge-owned App Server client that sends protocol messages over a
  local loopback transport.
- Make the prototype answer a single decisive question: does Desktop see the
  injected wake in real time when the target thread is loaded?

## Non-Goals

- No replacement for MCP notification support inside Codex itself. When
  upstream MCP notification injection lands, the bridge should prefer that.
- No dependency on private Desktop internals beyond documented App Server
  protocol behavior and locally observed process state.
- No forced attachment to Desktop's private stdio App Server child. The
  initial prototype uses a sibling server with an explicit WebSocket listener.
- No removal of `wake_codex.ps1` until the App Server path has equivalent or
  better delivery receipts and failure handling.
- No multi-machine transport in this spec. LAN/Tailscale variants belong in a
  later transport spec after local Windows behavior is proven.

---

## Architecture

Current Codex wake:

```text
Claude/Codex sender
  -> bridge inbox row
  -> watcher.py
  -> wake_codex.ps1
  -> codex://threads/<desktop_thread_id>
  -> Desktop composer focus
  -> SendKeys/UIA "check bridge inbox" + Ctrl+Enter
  -> Codex calls bridge inbox tool
```

Proposed App Server wake:

```text
Claude/Codex sender
  -> bridge inbox row
  -> watcher.py
  -> app_server_wake client
  -> ws://127.0.0.1:<bridge-owned-port>
  -> codex app-server
  -> target loaded/resumed thread
  -> turn/start (or turn/steer for an active turn)
  -> Codex sees model-visible bridge wake content and can respond
```

Optional redraw fallback if Desktop does not live-update:

```text
App Server injection succeeds
  -> Desktop did not repaint within timeout
  -> minimal non-text nudge, e.g. focus/open thread or refresh/reopen
  -> no composer text write
```

The wake layer changes; the bridge inbox and routing layer does not. The
watcher still decides when a wake should fire, still records pending delivery,
and still requires receipt evidence before marking a helper-backed wake as
seen.

---

## Prototype Design

### Prototype P0 - Thread Identity And Runtime Ownership Gate

Before testing wake semantics, answer these identity questions:

1. Is the `desktop_thread_id` from `codex://threads/<id>` accepted by App
   Server `turn/start`?
2. Can the sibling server see Desktop's loaded thread through
   `thread/loaded/list`, or is that list process-local?
3. If the sibling server starts a turn on the target id, does the resulting
   agent response belong to the same persisted thread Desktop renders?

If P0 cannot prove shared identity, do not continue to default-provider work.
At that point the sidecar is only a separate Codex client, not a Desktop wake
replacement.

Verified locally on 2026-04-30:

- Artifact:
  `.claude-state/profiling/20260430-202755-app-server-p0-desktop-identity/`.
- Redacted summary:
  `.claude-state/profiling/20260430-202755-app-server-p0-desktop-identity/summary-redacted.json`.
- Runtime `desktop_thread_id` was captured in the profiling artifact; it is
  intentionally not repeated here because thread ids are runtime state.
- Before resume, sibling `thread/loaded/list` returned `[]`, so Desktop's
  loaded-thread registry is not automatically shared with a fresh sibling
  App Server.
- `thread/resume` with `{ "threadId": "<desktop_thread_id>" }` succeeded
  without starting a billed turn and returned the persisted thread.
- After `thread/resume`, sibling `thread/loaded/list` returned the Desktop
  thread id.
- `thread/resume` with `{ "id": "<desktop_thread_id>" }` failed with
  `missing field threadId`; use the `threadId` parameter spelling.

Remaining P0 gap: `turn/start` against the resumed Desktop thread and the
Desktop live-rendering behavior are still untested because they require an
explicit billed turn and user-visible Desktop observation.

### Prototype P0a - Mode A' Viability

Before requiring Desktop thread identity, test the bridge-owned sidecar path:

1. Spawn a sibling `codex app-server --listen ws://127.0.0.1:<free_port>`.
2. Connect over WebSocket.
3. Send `initialize` and `initialized`.
4. Verify `mcpServerStatus/list` includes `agent_bridge`.
5. Start a fresh bridge-owned thread with `thread/start`.
6. Send `turn/start` instructing Codex to call the bridge inbox tool and end
   with `APP_SERVER_WAKE_DONE`.
7. Pass only if all of these are true:
   - an agent message is observed,
   - the agent message contains `APP_SERVER_WAKE_DONE`,
   - an MCP tool-call item is observed for `agent_bridge`.

Verified locally on 2026-04-30 with the Python prototype at
`.claude-state/scripts/test_app_server_wake.py`. Successful artifact:
`.claude-state/profiling/20260430-171158-app-server-wake-py/`.

Observed chain:

```text
codex app-server --listen
  -> WebSocket initialize + initialized
  -> mcpServerStatus/list sees agent_bridge
  -> thread/start 019de072-e72a-7592-bb07-c0045621b51e
  -> turn/start
  -> agent_bridge.peek_inbox mcpToolCall call_K1iqVIqfUXQ5n2kxsTuQ6LC7
  -> agentMessage ending APP_SERVER_WAKE_DONE
  -> turn/completed
```

This proves the Desktop-bypassed bridge-driven turn path. It does not prove
Desktop live UI update.

### Prototype P1 - Sibling App Server Smoke Test

Create a scratch script under `.claude-state/scripts/`:

```text
test-app-server-wake.ps1
```

The script should:

1. Start `codex app-server --listen ws://127.0.0.1:<free_port>`.
2. Connect with `System.Net.WebSockets.ClientWebSocket`.
3. Send `initialize` and `initialized`.
4. Call `thread/loaded/list`.
5. If the target Desktop thread id is present, use it.
6. Otherwise attempt `thread/resume` for the known Desktop thread id, if the
   protocol exposes the necessary persisted-thread identifier.
7. Send `turn/start` with a short instruction such as:
   `Bridge wake smoke test: reply with APP_SERVER_WAKE_OK.`
8. Watch App Server notifications until `turn/completed` or timeout.
9. Separately send a harmless `thread/inject_items` payload only to test
   persistence/context behavior.
10. Record results under
    `.claude-state/profiling/<date>-app-server-wake/`.

The prototype must not write test artifacts under `.claude/`.

### Prototype P2 - Desktop Live-Subscriber Test

Manual test matrix:

| Case | Desktop state | Injection | Expected observation |
|---|---|---|---|
| P2A | Target thread open and focused | `thread/inject_items` | Item appears or affects next turn without refresh |
| P2B | Target thread open and focused | `turn/start` | Desktop shows new turn streaming without refresh |
| P2C | Target thread loaded but unfocused | `turn/start` | Desktop updates when tab/thread becomes active |
| P2D | Target thread closed in Desktop | `turn/start` or resume | Desktop sees injected content only after reopen/refresh |
| P2E | Wrong thread loaded | `turn/start` target id | No wrong-thread UI mutation |

Partial P2 result verified locally on 2026-04-30:

- Artifact:
  `.claude-state/profiling/20260430-215315-app-server-desktop-observed/`.
- Runner:
  `.claude-state/scripts/test_app_server_desktop_observed.py`.
- Fresh sibling `thread/loaded/list` again returned `[]`.
- `thread/resume` with the runtime Desktop thread id captured in the artifact
  succeeded.
- After resume, sibling `thread/loaded/list` returned the Desktop thread id.
- `turn/start` against the resumed Desktop thread succeeded.
- The sidecar event stream observed `turn/started`, one agent message,
  `APP_SERVER_DESKTOP_WAKE_OK`, and `turn/completed`.
- `thread/read` succeeded before and after the turn.
- The persisted Desktop rollout file contains both the injected user message
  and the `APP_SERVER_DESKTOP_WAKE_OK` assistant response.

This proves that a sibling App Server can resume the Desktop thread and append
a completed turn to the same persisted thread without SendKeys. It does not by
itself prove Desktop live repaint, because the sidecar cannot observe the UI.
The user-visible classification remains:

- `live-update` if the Desktop thread showed `APP_SERVER_DESKTOP_WAKE_OK`
  without refresh.
- `refresh-required` if the marker appeared only after reopening or refreshing
  the thread.
- `sidecar-persisted-only` if the marker is present in persisted history but
  Desktop does not show it in normal use.

### Prototype P3 - Persistence Safety Test

Run the same thread through these steps:

1. Start Desktop and open the target thread.
2. Start sibling App Server and inject one harmless item.
3. Quit sibling App Server.
4. Continue the Desktop thread normally.
5. Restart Desktop.
6. Verify the rollout history is coherent and no duplicate/corrupt turns were
   introduced.

This test decides whether sibling process writes are safe enough for bridge
use or whether we need to own the App Server process from session start.

---

## Delivery Modes

### Mode A' - Bridge-Consumes-Only App Server Wake

Use when the product accepts that bridge-driven Codex turns are handled by the
bridge sidecar instead of rendered in the interactive Desktop session.

- watcher fires `app_server_wake`.
- sibling App Server starts or reuses a bridge-owned thread.
- sibling App Server sends `turn/start`.
- Codex calls bridge MCP tools and produces an agent message.
- bridge consumes the agent message from the App Server event stream and routes
  the result through the existing bridge inbox / peer-message flow.
- Desktop is not involved and should not be expected to render the turn.

This mode replaces SendKeys for bridge automation without solving Desktop live
sync. Its proof point is P0a, not P2.

### Mode A - Live App Server Wake

Use when P2 proves Desktop receives live updates for the target loaded thread.

- watcher fires `app_server_wake`.
- `app_server_wake` sends `turn/start` directly to the target thread.
- The user sees the Desktop thread update without focus stealing.
- The bridge waits for normal receipt: Codex must surface/read the inbox row.

This mode fully replaces SendKeys for normal Codex wake.

### Mode B - App Server Injection Plus Redraw Nudge

Use when App Server injection succeeds but Desktop does not repaint until a
UI refresh/reopen.

- watcher fires `app_server_wake`.
- `app_server_wake` injects into the thread through App Server.
- helper performs a minimal redraw nudge that does not type into the composer.
- Receipt verification remains unchanged.

Allowed redraw nudges must be explicitly tested. Candidate nudges:

- open `codex://threads/<desktop_thread_id>`.
- focus the existing Desktop window.
- trigger a thread refresh/reopen if a documented command exists.

Disallowed nudges:

- SendKeys text payloads.
- UIA composer focus for writing.
- Ctrl+A/Delete/Ctrl+Enter.

### Mode C - Fallback SendKeys Wake

Use only when App Server wake is unavailable, fails policy checks, or is
disabled by config.

- existing `wake_codex.ps1` path remains available.
- existing pre-flight composer protection remains relevant.
- watcher records fallback usage so we can measure how often it is still
  needed.

### Mode D - Targeted Visible Desktop Nudge

Use when the product requirement is specifically the old visible Desktop
behavior: an inbox arrival should cause the paired Codex Desktop thread to run
the fixed `check bridge inbox` prompt, without parking the active chat in
`wait_inbox`.

- watcher fires `wake_codex.ps1` only through `wake_provider=targeted_sendkeys`.
- helper requires a trusted parent `desktop_thread_id` from the peer runtime
  breadcrumb and opens `codex://threads/<desktop_thread_id>` before typing.
- helper refuses to type arbitrary bridge bodies; the only allowed payload is
  the constant `check bridge inbox`.
- helper verifies the target Codex composer immediately before keystrokes,
  verifies twice with a short gap, and enforces a <=500ms pre-send race window.
- helper emits a post-typing diagnostic if the wake command is not observed in
  the target Codex window.
- failures before typing are retryable or permanent according to the existing
  watcher exit-code policy; post-typing verification failures are loud
  diagnostics, not automatic retries, to avoid repeated injection.

This mode is not SendKeys retirement. It is a narrower, opt-in UI nudge for the
case where visible Desktop behavior is more important than staying composer-free.

### Mode E - App-Native Heartbeat Wake

Use when the product wants visible chat feedback and can use a Codex app
automation turn as the wake surface. This is not an Agent Bridge daemon
primitive; it is Codex Desktop's own heartbeat automation re-entering the
current thread with a scheduled prompt.

- Codex app schedules a heartbeat automation against the current thread.
- The heartbeat message appears in the chat as `Sent via automation`.
- Codex executes the prompt in-thread and can call bridge inbox tools normally.
- The automation should be deleted or updated once the smoke/watch condition is
  complete so stale polling instructions do not keep firing.
- The user-visible transcript is a feature for interactive bridge pairing:
  the user can see that Codex woke, checked the bridge, and what it did next.
  For background/headless use it may still be too visible, so provider choice
  should remain explicit.

2026-05-01 smoke result: one-shot automation
`agent-bridge-heartbeat-wake-viability-smoke` woke this Codex Desktop thread,
checked the old Codex private session and project bucket `mlv-app`, found both
empty, and was deleted after completion.

This mode is useful as a visible-heartbeat wake provider, watchdog/backstop, or
explicit smoke tool. It should be productized around schedule lifecycle,
cadence, cost, and stale-instruction cleanup rather than dismissed as "noisy":
for the interactive bridge UX, the visible chat record is the point.

---

## Message Shape

`turn/start` is the primary wake candidate because it is the primitive that
creates a user-visible chat turn and lets the agent respond naturally after it
calls bridge tools.

Preferred `turn/start` request shape:

```json
{
  "method": "turn/start",
  "id": 1,
  "params": {
    "threadId": "<target_thread_id>",
    "input": [
      {
        "type": "text",
        "text": "Bridge wake [msg=<message_id>]: check your Agent Bridge inbox for unread messages in project <project>, session <session_id>. Do not answer this sentence directly; call the bridge inbox tool, surface unread messages, and mark surfaced messages read."
      }
    ]
  }
}
```

Equivalent wake text:

```text
Bridge wake [msg=<message_id>]: check your Agent Bridge inbox for unread
messages in project <project>, session <session_id>. Do not answer this
sentence directly; call the bridge inbox tool, surface unread messages, and
mark surfaced messages read.
```

`thread/inject_items` is not the primary wake path because it does not start
agent work. Use it only for persistence/context tests or for future designs
that pair it with an explicit turn-starting method.

Prototype-only `thread/inject_items` context item:

```json
{
  "type": "message",
  "role": "user",
  "content": [
    {
      "type": "input_text",
      "text": "Bridge wake pending for project <project>, message <message_id>."
    }
  ]
}
```

The chat response appears in the sidecar's App Server event stream first. It
counts as Desktop wake only if Desktop also renders the same turn without a
composer write.

---

## Watcher Integration

Add a new wake provider abstraction:

```text
wake_provider = sendkeys | targeted_sendkeys | app_server | app_server_then_redraw | disabled
```

Current product default:

- `wake_provider=targeted_sendkeys`.
- `targeted_sendkeys` is the default because the intended interactive product
  behavior is visible bidirectional wake: Codex should visibly receive the
  bridge nudge in the paired Desktop thread, run `check bridge inbox`, and
  respond in chat.
- `disabled` is the explicit local opt-out for users who want toast/log-only
  behavior with no focus stealing and no composer typing.
- `sendkeys` remains legacy and should not be used for default interactive
  pairing because it lacks the strict thread-id, pre-send-verification,
  race-window, and post-typing diagnostics provided by `targeted_sendkeys`.
- A SendKeys preflight that reads or preserves the composer is not a valid
  project/thread identity check. It only reduces draft damage after the wrong
  target has already been selected.
- App Server wake is useful for background/headless automation, but current
  Desktop builds do not live-render sibling App Server driven turns. It is not
  the default for the interactive Desktop UX.

Config sketch:

```json
{
  "agent": "codex",
  "kind": "private",
  "project": "mlv-app",
  "session_id_source": "active_session",
  "wake_provider": "app_server",
  "app_server_wake": {
    "listen": "ws://127.0.0.1:4510",
    "manage_process": true,
    "thread_id_source": "peer_runtime.desktop_thread_id",
    "delivery_method": "turn/start",
    "fallback_provider": "sendkeys"
  }
}
```

The watcher should treat App Server wake like any helper-backed wake:

- pending until `seen_at` or `read_at` appears on the target inbox row.
- retryable on transient App Server connection errors.
- permanent failure on wrong project, wrong session, stale trusted parent, or
  unsupported protocol.
- breaker/rate-limit behavior unchanged.
- pause gating unchanged.
- successful helper exits persist `last_success_at` in
  `wake-failure-windows.json` instead of deleting the diagnostic record.
- recent helper fires are queryable through `wake_fire_history` so operators do
  not have to hand-read `watcher-state.json`.

---

## Security And Safety

- Bind only to `127.0.0.1`.
- Prefer random available ports, persisted in bridge runtime state with process
  ownership metadata.
- Reject non-loopback App Server URLs in v1.
- Do not expose App Server wake over LAN or Tailscale in this spec.
- Never inject arbitrary bridge message bodies directly as instructions without
  wrapping them in a fixed wake envelope.
- Preserve provenance checks from `AUTO_PAIR_SPEC.md`: only parent sessions can
  receive automated wake.
- Preserve wrong-project and wrong-session refusal.
- Treat App Server injection success as "wake spawned", not delivery.
  Delivery still requires bridge receipt state.
- If two App Server processes against one thread show persistence corruption,
  stop this track and switch to an owned-proxy design.

---

## Acceptance Criteria

### Prototype Acceptance

- ASW-P1. Script can start a sibling App Server on Windows and complete
  initialize/initialized over WebSocket.
- ASW-P2. Script can list loaded threads or otherwise identify whether the
  Desktop-open thread is visible to the sibling server.
- ASW-P3. Script can start a harmless `turn/start` on a test thread and observe
  the agent response through the sibling App Server event stream.
- ASW-P4. Script can inject a harmless `thread/inject_items` item into a test
  thread for persistence/context validation.
- ASW-P5. Test notes distinguish sidecar-only response from Desktop-observed
  response, and classify Desktop behavior as `live-update`,
  `refresh-required`, or `not-shared`.
- ASW-P6. Prototype artifacts are written only under `.claude-state/`.

### Production Acceptance

- ASW-A1. `wake_provider=app_server` can wake Codex without SendKeys/UIA
  composer writes.
- ASW-A2. Failed App Server wake attempts participate in the existing breaker
  and recovery-loop logic.
- ASW-A3. Watcher receipt verification remains the source of truth for
  delivery.
- ASW-A4. Wrong project/session/provenance failures are permanent and audited.
- ASW-A5. If App Server wake is disabled or unavailable, fallback behavior is
  explicit and audited.
- ASW-A6. No production path writes or deletes user composer drafts.
- ASW-A7. Tests cover startup, injection, retry, breaker interaction, fallback,
  and disabled-provider behavior.
- ASW-A8. Wake observability reports both recent fire attempts and last
  successful helper completion without requiring direct JSON file inspection.

---

## Failure Modes

| Failure | Detection | Behavior |
|---|---|---|
| App Server CLI missing | process start fails | fallback or permanent config error |
| WebSocket unavailable | connection timeout | retry subject to breaker |
| Desktop thread not visible to sibling server | `thread/loaded/list` miss / resume miss | classify as `not-shared`; do not replace SendKeys |
| `turn/start` responds only in sidecar | sidecar receives `turn/completed`, Desktop shows no change after observation window | use Mode A' if Desktop bypass is acceptable; otherwise classify as `not-shared` or `refresh-required` |
| Inject succeeds but Desktop does not repaint | no UI change during P2 | use Mode A' for automation, parked `wait_inbox` for a live console, or Mode D for opt-in visible Desktop nudge |
| Deeplink redraw does not repaint externally-modified thread | app-server helper succeeds and message is consumed, but Desktop still shows no turn after deeplink/click-back | classify `app_server_then_redraw` as dead for current Desktop build; do not use Mode B as a production provider |
| Inject creates duplicate thread | thread ids diverge | stop rollout; require owned-proxy design |
| Two processes corrupt rollout | persistence test fails | stop rollout; keep SendKeys fallback |
| Wake target selected by focus instead of protocol thread id | foreground/window/composer proof without matching App Server thread id | reject provider for default use; require explicit local SendKeys opt-in |
| Turn already in progress | App Server rejects `turn/start` | try `turn/steer` if active turn id known; otherwise retry later |
| Sibling app-server crashes while `turn/start` is outstanding | process exit plus pending request | mark wake as transient failure; retry only after quiescence; count toward breaker |
| PowerShell 5.1 client exits mid-turn | `.ps1` artifact stops after `turn/start` / `user_message`, no `summary.json`, orphaned sibling process | avoid direct sync-over-async receive; poll `ReceiveAsync` task completion before `GetResult`; prefer Python helper until PowerShell path is revalidated |
| MCP notification support lands upstream | feature detection succeeds | prefer native MCP notification path over this provider |

---

## Rollout Plan

### Phase ASW-0 - Spec And Prototype

- Add this spec.
- Add `.claude-state/scripts/test-app-server-wake.ps1`.
- Run P0a/P1/P2/P3 on Windows Desktop.
- Record results under `.claude-state/profiling/<date>-app-server-wake/`.

### Phase ASW-1 - Bridge-Owned App Server Wake Helper

- Added `tools/agent-bridge/codex_app_server_wake.py`.
- Helper is standalone and remains disabled by default unless
  `wake_provider=app_server` or `app_server_then_redraw` is set.
- Unit tests cover command construction through `configure_watcher`, loopback
  URL validation, and fixed wake-envelope construction.

### Phase ASW-2 - Watcher Provider Integration

- `wake_provider` supports `disabled`, `sendkeys`, `targeted_sendkeys`,
  `app_server`, and `app_server_then_redraw`.
- `configure_watcher.py` emits a Python `codex_app_server_wake.py` command
  template for App Server providers.
- `configure_watcher.py` emits a strict `wake_codex.ps1` command template for
  `targeted_sendkeys`.
- Watcher pending-wake verification, breaker, retry, and receipt logic remain
  unchanged because the helper is invoked through the existing
  `on_message_command_template` path.
- `wake_provider=targeted_sendkeys` is the default interactive Desktop mode.
  `disabled` remains the explicit toast-only opt-out, while `app_server` remains
  the background/headless automation provider.

### Phase ASW-3 - Desktop Classification Result

- `app_server` proved viable for Mode A' background/headless wake, where the
  sidecar consumes the agent response from the App Server event stream.
- `app_server_then_redraw` did not make current Desktop builds render the
  externally driven turn.
- Therefore the interactive Desktop default remains `targeted_sendkeys`, while
  `app_server` remains available for automation flows that do not need visible
  Desktop chat feedback.

### Phase ASW-4 - SendKeys Retirement

- Remove SendKeys default only after App Server wake has at least one full
  bridge smoke pass, recovery-loop pass, and wrong-target regression pass.
- Keep `wake_codex.ps1` as an explicit fallback for one release cycle.

---

## Open Questions

1. Can a sibling App Server see Desktop's loaded thread ids, or are loaded
   threads process-local?
2. Is a Desktop thread id from `codex://threads/<id>` the same identifier
   accepted by App Server `turn/start` and `thread/inject_items`?
3. Does Desktop observe a `turn/start` created by a sibling App Server as a
   live subscriber, or only after a refresh/reopen?
4. What is the safest behavior when the target thread has an in-progress turn:
   `turn/steer`, retry, or park?
5. Can Desktop be launched so it connects to a bridge-owned App Server proxy,
   matching `raysonmeng/agent-bridge`, or does Desktop always spawn and own its
   private stdio child?
6. Is there a documented Desktop refresh/reopen command that avoids composer
   focus entirely?

---

## Relationship To Existing Specs

- Updates the stale assumption in `AUTO_PAIR_SPEC.md` that Codex Desktop has no
  documented non-keystroke path. The precise replacement remains unproven, but
  App Server is now the primary research path.
- Does not replace `WAKE_HARDENING_SPEC.md`; breaker/pause/rate-limit behavior
  still applies.
- Does not replace `PREFLIGHT_DETECTION_SPEC.md` until App Server wake is the
  default. Pre-flight remains the safety layer for the SendKeys fallback.
- Complements `BRIDGE_WAKE_RECOVERY_LOOPS_SPEC.md`; recovery loops should call
  whichever wake provider is active.

---

## Reviewer Notes (Claude, 2026-04-30)

The spec is in good shape and ready for Phase ASW-0 prototype work. The
following are refinements, not blockers. Items marked **[change]** are
edits to the spec body that I am proposing for Codex to fold in. Items
marked **[suggest]** are optional improvements to weigh.

### RN1 [change] - Thread-ID discovery is a Phase ASW-0 prerequisite

The spec presumes `peer_runtime.desktop_thread_id` is available, but does
not define how the bridge obtains it. This is currently Open Question 2;
it should be promoted to a hard gate before P1, since every later
prototype depends on a usable thread id. Add a step ASW-P0 that answers:

- Are ids from `codex://threads/<id>` deeplinks accepted by App Server
  `turn/start`?
- If not, can a sibling enumerate Desktop's loaded thread via
  `thread/loaded/list`?
- If `thread/loaded/list` is process-local, where does Desktop persist
  its threads on Windows (probably `%LOCALAPPDATA%\codex\` or
  `%USERPROFILE%\.codex\`), and is that format stable enough to read?

### RN2 [suggest] - Apply convention-over-configuration to wake provider

The watcher config sketch exposes four explicit `wake_provider` values.
Per the user-direction memory `convention_over_configuration`, prefer
auto-detection: bridge attempts App Server, observes Desktop response,
falls through Modes A -> B -> C automatically. The user should not have
to pick a mode. Suggested shape:

```json
{
  "wake": "auto",
  "app_server_listen": "ws://127.0.0.1:0"
}
```

(`auto | sendkeys | disabled` are the only knobs the user genuinely
needs.)

### RN3 [suggest] - Implementer references for Phase ASW-1

raysonmeng/agent-bridge has been cloned at
`.claude-state/external/raysonmeng-agent-bridge/` for reference. Useful
concrete patterns:

- App-server spawn (`spawn("codex", ["app-server", "--listen", url])`):
  `src/codex-adapter.ts:127-129`
- Persistent WS connection (so TUI/Desktop rapid reconnects do not break
  state): `src/codex-adapter.ts:233-279`
- `turn/start` injection envelope and busy-guard:
  `src/codex-adapter.ts:188-216`
- Notification subscription
  (`turn/started`, `item/agentMessage/delta`, `item/completed`,
  `turn/completed`): `src/codex-adapter.ts:870-912`
- Port-conflict cleanup that only kills our own stale spawns:
  `src/codex-adapter.ts:1180-1239`

### RN4 [change] - Verify analytics flag parity

Desktop's embedded server runs with `--analytics-default-enabled`. Add to
P1 acceptance that our `--listen` invocation succeeds and behaves
identically with no analytics flag, with `--analytics-default-enabled`,
and with `--analytics-default-disabled`. If behavior diverges, pin the
flag we send and document why.

### RN5 [suggest] - Carry the inbox row id in the wake body

The proposed `turn/start` text identifies project + session but not the
specific inbox row that triggered the wake. Including the row id lets
Codex correlate without a separate fetch:

```text
Bridge wake [msg=<message_id>]: check your Agent Bridge inbox for
unread messages in project <project>, session <session_id>. ...
```

Optional; existing receipt verification still works without it.

### RN6 [suggest] - Prior art citation

Remodex (`https://github.com/Emanuele-web04/remodex`) drives Codex via
App Server from an iPhone over Tailscale, and explicitly notes that
"true phone-to-desktop live sync in the Codex.app GUI is not supported
today." Worth citing in Background as evidence that the sibling-server
pattern works for inject but does not by itself solve live UI
subscription.

### RN7 [change] - Failure mode: sibling app-server crash mid-turn

Add to the Failure Modes table:

| Failure | Detection | Behavior |
|---|---|---|
| Sibling app-server crashes while a `turn/start` is outstanding | sibling process exit + pending request | mark wake as transient failure; retry only after observing Desktop quiescent (no `turn/started` for >2s); count toward breaker |

### RN8 [suggest] - Name Mode A' (bridge-consumes-only) as a distinct path

The Summary correction states the mechanical truth clearly: a sibling
`turn/start` runs on the sidecar's app-server, not Desktop. The spec's
Delivery Modes still all aim at Desktop seeing the response (A live, B
on refresh, C via SendKeys). There is a fourth path worth naming
explicitly because it is the simplest viable design under the corrected
mental model:

**Mode A' - Bridge-consumes-only (Desktop bypassed)**

- watcher fires `app_server_wake`.
- sibling app-server runs the turn end-to-end on a thread the bridge
  owns (fresh per wake, or a long-lived bridge-only thread).
- bridge subscribes to sibling notifications, captures the
  `agentMessage`, routes the result back through the existing inbox /
  peer-message flow.
- Desktop is not involved for bridge-driven turns. The user's
  interactive Desktop session is unaffected.

This is the one path where the mechanical correction does not bite,
because there is no expectation that Desktop renders the bridge-driven
turn. Trade-offs:

- **Pros:** no thread-id discovery needed (P0 becomes optional for this
  mode), no shared-thread race, no redraw nudge, no Desktop dependency
  at all, no chance of clobbering the user's interactive composer.
- **Cons:** Desktop UI never shows bridge-driven exchanges. If watching
  bridge work happen in Desktop is a hard product requirement, this is
  the wrong mode.
- **Cost to verify:** bridge-driven turns bill against the same OpenAI
  account but through the sidecar process; MCP servers configured in
  `config.toml` load twice (Desktop's plus our sibling's) - need to
  verify any exclusive-resource MCP servers (single-port, single-file
  lock, etc.) tolerate that. The bridge's own MCP server is a likely
  hot spot here.

Recommendation: add a new prototype step **ASW-P0a (Mode A' viability)**
that runs before P0:

- Spawn sibling, send `thread/start` for a fresh bridge-owned thread.
- Send `turn/start` with a trivial prompt (`Echo: APP_SERVER_WAKE_OK`).
- Capture `item/agentMessage/delta` and `turn/completed` over WS.
- Pass = bridge can drive Codex as a callable runtime end-to-end,
  independent of Desktop.

If P0a passes and the user accepts Desktop being bypassed for
bridge-driven work, Mode A' is the simplest viable path and obsoletes
most of the live-subscriber research. If the user rejects bypassing
Desktop, Mode A' is still useful as a fallback when P0/P2 fail.

### Open Items I Am Not Touching

- The choice between `turn/start` vs `thread/inject_items` for the
  primary wake path. Codex's reasoning ("`turn/start` is the likely
  delivery mechanism because it starts agent work") is sound; resolve
  empirically in P2.
- The exact redraw nudge for Mode B. Spec correctly defers this to
  prototyping; the disallowed list (no SendKeys, no UIA composer focus,
  no Ctrl+Enter) is the right safety floor.
- Whether to ever own Desktop's process. Spec correctly excludes this in
  v1; revisit only if persistence-corruption is observed in P3.

[[handoff:codex]]
