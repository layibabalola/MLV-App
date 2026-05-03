# DOM Telemetry Use Case Catalog

**Status:** Investigation catalog and roadmap input, not shipped runtime.

**Scope:** Read-only UI/DOM/accessibility telemetry for Agent Bridge
orchestration. This includes Windows UI Automation (UIA), accessible DOM trees,
Electron/Chrome DevTools Protocol (CDP) if explicitly available, app-server or
dashboard events, and screenshot/OCR as a fallback.

## Product Thesis

Bridge logs know what Agent Bridge intended to do. The desktop UI knows what the
user and target app can actually see. DOM/UI telemetry is valuable when it acts
as **veto and reconciliation evidence**:

- veto unsafe write-side actions before they touch the wrong chat,
- reconcile bridge receipts with visible user/app state,
- downgrade confidence when UI and durable state disagree,
- explain failures with evidence instead of exit-code folklore.

DOM telemetry must not become sole authority. The bridge should combine durable
JSONL, receipts, app-server/dashboard events, and UI facts into a confidence
model. When sources conflict, the system should fail visible and name the
mismatch.

## Feasibility And Overhead Hypothesis

Making DOM traversal a core part of the application is feasible if it is:

- **scoped:** query known app windows and shallow subtrees first; avoid full
  transcript scans unless explicitly profiling or debugging,
- **adaptive:** scan quickly only around wake/send/read/handle events; use a
  slower heartbeat when idle,
- **fact-oriented:** extract compact facts and hashes, not raw private chat
  bodies,
- **fail-closed:** return `unknown` or `blocked_wrong_thread` rather than
  guessing when selectors drift or confidence is low,
- **measured:** promotion to a core dependency requires latency, CPU, memory,
  privacy, and UI-jank evidence.

Initial cadence hypothesis:

| Mode | Candidate cadence | Purpose |
|---|---:|---|
| Event burst | 250-750ms for 3-5s | Confirm wake rendered, detect busy/blocked state after action. |
| Active pairing | 2-5s | Track active title, busy/idle, handoff cards, and stale commitments. |
| Idle paired session | 10-30s | Keep health/status fresh without constant tree walks. |
| On-demand guard | Single snapshot | Preflight before write-side wake, send, or closeout. |

Promotion gate: a representative UIA scanner should prove p95 snapshot latency
under the chosen cadence, no visible UI jank, bounded CPU/memory, and no raw
message-body retention by default.

## Empirical Measurements (Claude additions, 2026-05-02)

### Read latency

`AutomationElement.FromHandle(hwnd) + FindAll(TreeScope.Descendants, ListItem|TreeItem)` on Codex Desktop main window with 46 sidebar+content items, five consecutive runs:

| Run | Latency |
|-----|---------|
| 1 (cold) | 77 ms |
| 2 | 39 ms |
| 3 | 35 ms |
| 4 | 33 ms |
| 5 | 29 ms |

**Steady-state: 30-40 ms.** Larger trees scale roughly linearly with element count; expect 100-200 ms for trees with hundreds of elements. First-walk cost is amortized after the AutomationPeer cache warms.

### Derived polling overhead

| Cadence | UIA work / minute | % of one core |
|---------|-------------------|---------------|
| 2 s (30 polls/min) | ~1.0 s | ~1.7% |
| 1 s (60 polls/min) | ~2.0 s | ~3.3% |
| 500 ms (120 polls/min) | ~4.0 s | ~6.7% |
| 200 ms (300 polls/min) | ~9-12 s | ~15-20% |

For comparison, idle Electron apps consume 1-3% CPU baseline. Read-only DOM polling at 2 s cadence adds less than the apps themselves use idling. Codex's recommended cadence table (above) is comfortably within budget.

### Event-driven alternative

`Automation.AddAutomationEventHandler` for `Invoke`, `StructureChanged`, and focus-change events removes steady-state polling for change detection — the OS notifies on actual changes. Polling stays useful for streaming-state vital signs (text grows without structure change). Hybrid approach is recommended for the core service.

### Surface coverage (verified 2026-05-02)

| App | Surface | UIA tree | Notes |
|-----|---------|----------|-------|
| Codex Desktop | Electron (`Codex.exe`) | Full DOM-like tree | **Demonstrated.** 30-40 ms steady-state on 46-item tree. Tailwind class signatures (`group/cwd...`, `text-size-chat ...`) addressable; version-fragile. |
| Claude Desktop | Electron (`claude.exe`, AnthropicClaude install) | Full DOM-like tree | **Demonstrated.** 29 ms steady-state on 469-element tree (37 ListItems / 95 Buttons / 259 Text / 22 Group / 15 DataItem / etc). `ClassName` property is empty — addressing is by `Name` + parent context, not Tailwind. Bullet-list items in rendered markdown surface as `ListItem` — useful for self-postflight (use case 5). |
| Claude Code CLI | Windows Terminal / pwsh | TextPattern only | Text-grid scrollback readable; no discrete elements to address or click. Only relevant if user runs the CLI variant rather than Desktop. |
| Claude.ai web | Browser | Browser AX tree | Workable through the browser process; messier addressing. Untested. |

### URL deeplink schemes (verified 2026-05-02)

- `codex://threads/<id>` — registered in HKCU. Used by `wake_codex.ps1` for thread navigation pre-SendKeys. Handler: `Codex.exe "%1"`.
- `claude://` — **registered** in `HKCU:\SOFTWARE\Classes\claude` with handler `"C:\Users\<user>\AppData\Local\AnthropicClaude\app-<ver>\claude.exe" "%1"`.

#### `claude://` route table (per Claude.ai web research, 2026-05-02 — empirical verification recommended before depending on these)

| Route | Purpose |
|---|---|
| `claude://claude.ai/new` | New chat |
| `claude://claude.ai/new?q=<urlencoded>` | New chat with prefilled prompt |
| `claude://claude.ai/chat/{conversation-id}` | Open specific chat by UUID |
| `claude://claude.ai/project/{project-id}` | Open specific project by UUID |
| `claude://code/new?q=...&folder=...` | Claude Code composer |
| `claude://cowork/new?q=...&folder=...&file=...` | Cowork session |

Symmetric to `codex://threads/<id>` for chat-level deeplinks (`claude://chat/{uuid}`). **Open gap:** no documented route to deep-link Desktop to a specific *session state* by UUID with prefilled context — feature request #50345 upstream. Bridge cannot directly open Desktop "to where the paired bridge thread is" the way `codex://threads/{id}` allows.

#### Launch invocation (Windows footgun)

Use `Start-Process`, not `cmd /c start`. `cmd` interprets `&` in query strings as a command separator and silently breaks any URL with multiple parameters.

```powershell
Start-Process "claude://code/new?q=$([uri]::EscapeDataString('Fix the failing test'))&folder=C:\repos\foo"
```

### Chrome DevTools Protocol (CDP) — strictly richer surface, opt-in only

Both Claude Desktop and Codex Desktop are Electron and speak CDP. Launching the binary with `--remote-debugging-port=9222` exposes a localhost CDP endpoint with full DOM access — `querySelectorAll`, `Runtime.evaluate`, `Page.captureScreenshot`, `Mouse.click`. Strictly richer than UIA: real CSS selectors, arbitrary JS, programmatic click without foreground concerns, cross-platform.

**The single-instance trap.** Electron's single-instance lock redirects new launches to the running PID and **strips the `--remote-debugging-port` flag**. So enabling CDP requires killing the running instance first:

```powershell
Get-Process -Name 'Claude' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
$exe = Get-ChildItem "$env:LOCALAPPDATA\AnthropicClaude\app-*\claude.exe" |
       Sort-Object LastWriteTime -Descending |
       Select-Object -First 1 -ExpandProperty FullName
Start-Process -FilePath $exe -ArgumentList '--remote-debugging-port=9222'
Start-Sleep -Seconds 3
$targets = Invoke-RestMethod 'http://localhost:9222/json'
```

#### Tradeoffs vs UIA

| Property | UIA | CDP |
|---|---|---|
| Available without restart | yes | **no** — kill required |
| Selector quality | Name + parent context | real CSS |
| Arbitrary JS | no | yes |
| Programmatic click | open empirical | yes (no foreground steal) |
| Screenshots | clumsy | first-class |
| Cross-platform | Windows-only | yes |
| Security surface | low | **localhost:9222 unauthenticated** — any local process can drive |
| Survives app updates | yes | needs versioned-path glob |

**Adoption pattern.** Killing Codex Desktop to enable CDP would terminate any live bridge-paired session. Same for the user's active Claude Desktop window. Therefore CDP is **not a viable always-on telemetry surface** — it's an opt-in mode for sessions where the user explicitly wants the richer capability (forensics dump, deep DOM probe, programmatic UI driving). UIA remains the default always-available surface.

The `DomBackend` abstraction (Architectural Sketch §) should expose both: `WindowsUiaBackend` (always-on, lower capability) and `CdpBackend` (opt-in, full capability), with consumers picking based on operation.

#### `app.asar` inspection

Bundled renderer code at `%LOCALAPPDATA%\AnthropicClaude\app-<ver>\resources\app.asar`. Extract with `@electron/asar`:

```bash
npm install -g @electron/asar
npx @electron/asar extract <path-to-app.asar> <out-dir>
```

Static inspection of minified renderer JS, CSS, and HTML templates lets us derive **durable CSS selectors** based on actual React component structure rather than guessing from observed class signatures. Equivalent extraction for Codex Desktop's `app.asar` would do the same for the Codex selector registry. Promotes Phase-3's "class-name registry" from "observed-and-fragile" to "source-derived."

#### What CDP still can't do

- Open Desktop to a *specific session state* by UUID with prefilled context (no `/session/` route; FR #50345).
- Read the conversation store programmatically through a stable API (leveldb persistence is binary, undocumented).
- Stay enabled across normal launches without the kill-and-restart dance.

For durable programmatic access to chat *content* across sessions, the public API is the right surface. CDP is for UI automation and "what does the rendered tree actually look like" diagnostics.

### Write-side empirical (results 2026-05-02)

- `ValuePattern.SetValue` / `TextPattern` writes on Codex's ProseMirror composer: **not exposed**, SendKeys + activation is the only path. Empirically established (`uia_setfocus_intrusive` memory).
- **Codex sidebar `ListItem` row (wrapper):** supports **only** `ScrollItemPattern`. `Invoke` and `SelectionItem` are NOT exposed at this level — calling `.Select()` or `.Invoke()` on the wrapper throws or silently no-ops. *Empirical 2026-05-02.*
- **Codex sidebar `ListItem` row's inner `Button` child:** **does support `InvokePattern`**. So programmatic chat-switch via UIA is technically reachable. However, the inner button's accessible `Name` concatenates multiple actions (`'Archive chat Pin chat 2d'`) — naively invoking it may fire the first action (archive) rather than chat-switch. Need to walk *into* the button to find the discrete chat-switch sub-element. **Not a turnkey primitive; needs careful structural matching.**
- **Claude Desktop sidebar:** has a collapsible state. When collapsed, the actual chat list is not present in the UIA tree (an `Expand sidebar` button is visible instead). Pattern probe on the real sidebar requires the sidebar to be expanded first — pending a re-test.
- **General Claude Desktop buttons** (Minimize, Maximize, Search, Back, Forward, Menu): all expose `InvokePattern`. UIA write works for normal buttons; the sidebar-row case is an outlier due to its container structure.
- **Conclusion:** UIA write is **available but not addressable cleanly** for sidebar navigation. CDP `Mouse.click` against a real CSS selector is the cleaner path for this specific use case once we accept the opt-in launch model.

## 10/10 Use Cases

### 1. Wrong-Thread / Wrong-Project Certification

**Signal:** active chat title, selected sidebar row, visible project/repo
breadcrumb, window identity, and thread hints if exposed.

**Decision:** allow, suppress, or downgrade wake/send attempts. Classify failure
as `wrong_chat`, `wrong_project`, or `ambiguous_target`.

**Why logs are not enough:** bridge state knows the intended target, not what
Desktop currently has focused or rendered.

**Validation:** intentionally open another project/thread and verify the bridge
refuses UI write paths before any composer mutation.

### 2. Wake Actually Rendered

**Signal:** latest visible user bubble containing the constant wake phrase,
prompt cleared/idle state, and timestamp/order near the wake attempt.

**Decision:** mark wake as visually confirmed, retry another provider, or open
a breaker with a precise reason.

**Why logs are not enough:** helper exit code can mean "process ran", not
"target chat received visible wake".

**Validation:** compare success, blocked focus, minimized app, stale chat, and
wrong-thread cases.

### 3. Agent Busy / Idle / Blocked Classifier

**Signal:** streaming indicator, stop button, disabled composer, thinking
state, approval modal, error banner, rate-limit banner.

**Decision:** defer nudges, avoid wake storms, route work to ledger, or escalate
to user with a concrete blocked reason.

**Validation:** capture known idle, streaming, tool-approval, error, and
rate-limit UI states and replay parser fixtures.

### 4. User Draft / Composer Safety Gate

**Signal:** composer non-empty flag, attachment chips, text selection, active
typing/focus in the input.

**Decision:** suppress write-side wake and use toast/passive notification or
parked ledger entry instead.

**Privacy rule:** prefer empty/non-empty, length, or salted hash. Do not store
composer text by default.

**Validation:** place a draft in the composer and verify `targeted_sendkeys`
refuses to write.

### 5. Read-But-Not-Surfaced Detection

**Signal:** visible transcript after inbox read, looking for message id, sender,
action marker, or summarized bridge header.

**Decision:** distinguish "receiver consumed and surfaced" from "message was
marked read and vanished"; reopen response debt or require `mark_handled`.

**Validation:** fixtures for read-and-surfaced, read-silent, summarized, and
compacted transcript cases.

### 6. Compaction / Session Rollover Detector

**Signal:** compaction banners, new-chat state, changed selected conversation,
missing prior transcript, context-limit notices.

**Decision:** force bootstrap/drain before trusting continuity or waiting-on-peer
claims.

**Validation:** simulate compaction/new-thread switch and verify bridge marks
prior session assumptions stale.

### 7. MCP Tool Access From Client View

**Signal:** visible tool-call errors, disconnected MCP/server banners, failed
bridge tool output in transcript.

**Decision:** classify `tool_access_risk` separately from durable inbox failure
and recommend host reconnect/restart before retrying bridge tools.

**Validation:** kill/restart wrapper and compare client-visible error with
health-panel state.

### 8. Shared Activity Rail / Handoff Cards

**Signal:** assistant status text, tool-call blocks, final-response boundary,
review requests, implementation asks, approval gates, peer mentions.

**Decision:** emit normalized `WORKING_ON_IT`, `IMPLEMENTATION_UPDATE`,
`WAIT_DECLARED`, `HANDOFF_OPEN`, and `CLOSEOUT` events.

**User value:** the user can answer "who has the ball?" without reading JSONL
or scrolling both chats.

**Validation:** users can resume after inactivity and correctly identify owner,
next action, and blockers.

### 9. Hybrid Truth Reconciler

**Signal:** bridge inbox/receipt JSONL, UIA/CDP facts, app-server/dashboard
events, operation results.

**State artifact:** `reconciled-state.json` with confidence fields such as
`active_thread_confidence`, `wake_success_confidence`, `blocked_reason`, and
`evidence[]`.

**Decision:** prevent false claims like "Claude was notified" or "Codex is
watching" when evidence conflicts.

**Validation:** table-driven contradiction cases where each source disagrees.

### 10. No-Silent-Success Telemetry Contract

**Signal:** every bridge-owned operation emits process result plus independent
artifact: receipt transition, UI observation, state mutation, or event row.

**State artifact:** `operation-results.jsonl` keyed by `operation_id` with
`requested`, `process_exit`, `artifact_seen`, `ui_seen`, and `final_status`.

**Decision:** retry or escalate based on the failed stage rather than treating
exit code 0 as success.

**Validation:** fault injection for exit-0/no-artifact, malformed JSON, wrong
session artifact, wrong-thread wake, and UI unknown.

## Promising But Not Yet 10/10

- **CDP/Electron DOM probe:** rich if available, but version/security sensitive.
- **Screenshot/OCR fallback:** valuable when UIA/CDP fail; must degrade to
  `unknown` on low confidence.
- **Semantic progress estimator:** could improve ETA/nudge cadence; NLP over
  partial transcripts is noisy.
- **Spec drift radar:** detect Claude/Codex using conflicting acceptance
  criteria; needs stable vocabulary to avoid false alarms.
- **Live collaboration map:** useful for long projects, likely after the health
  panel is stable.
- **UI hang detector:** repeated identical UI snapshots/spinner freeze; needs
  idle-vs-hung calibration.
- **Privacy boundary monitor:** detect attachments/code/logs/private artifacts
  before relays; must avoid storing raw content.
- **Emotional friction detector:** user correction patterns may signal confusion
  but should never label emotion in user-facing output.
- **Mode A' Live Mirror Panel** *(Claude addition):* poll active Codex thread
  paragraphs every ~2s during in-flight bridge turns and surface the diff into
  Claude's session as a Monitor stream. Closes the long-standing UX gap where
  bridge-driven Codex output isn't visible in user's Claude window without
  thread-switching. Lighter than parked `wait_inbox` and doesn't burn a Codex
  session slot. Privacy: surface paragraphs only while user has explicitly
  opted into mirror-mode for the session.
- **Cross-project Codex knowledge index** *(Claude addition):* nightly read-only
  UIA walk of all Codex sidebar threads → SQLite FTS5 index. Skill `recall_codex`
  answers "what did I tell Codex about X 3 weeks ago?" without manual scrolling.
  Privacy: store only the user's own messages by default; redact attachments and
  code blocks; honor a per-project opt-out. Orthogonal to memory and ledger.
- **Forensic DOM snapshotter** *(Claude addition):* hourly compact-fact JSONL
  snapshot of sidebar + active-thread last-N paragraph hashes/timestamps,
  zstd-compressed, 7-day rolling cap. Operationalizes incident reconstruction
  alongside bridge logs. Stored under `.claude-state/bridge-ui/` per the
  privacy rule on raw artifacts.
- **Watcher-liveness via rendered heartbeat** *(Claude addition):* watcher's
  most-recent toast/notification line must appear in chat within
  `2 × poll_interval`; if absent, watcher is zombie despite stale-pinned config.
  Catches `wake_provider_change_requires_watcher_restart` class. Requires
  watcher to emit a forced-poll heartbeat tick to make absence diagnostic.

## Telemetry Source Preference

1. First-party bridge/app-server/dashboard events when schema-owned.
2. Read-only UIA/accessibility facts from targeted windows/subtrees.
3. CDP when explicitly available and locally authorized.
4. Screenshot/OCR only as a fallback or validation corpus.
5. Agent self-report only as a weak signal reconciled against external evidence.

## Privacy Rules

- Store facts, timestamps, ids, hashes, and confidence, not raw transcript text.
- Treat composer contents and attachments as sensitive by default.
- Use raw screenshots/OCR only in explicit profiling artifacts under
  `.claude-state/` and redact/delete before sharing.
- UI labels are spoofable; use DOM telemetry as veto/reconciliation evidence,
  not unilateral authorization.

## Investigation Plan

1. Build a read-only UIA snapshot probe for Codex Desktop and Claude Desktop.
2. Measure snapshot overhead at 1s, 2s, 5s, 10s, and event-burst cadences.
3. Record p50/p95/p99 latency, CPU, memory, handle count, and visible UI jank.
4. Measure parser stability across idle, streaming, wrong-thread, modal,
   error, draft, and minimized states.
5. Store compact facts in `.claude-state/bridge-ui/` during profiling; do not
   write raw snapshots into tracked docs.
6. Promote only the safe subset into bridge-root state once the privacy and
   overhead gates pass.

## Architectural Sketch (Claude addition)

If DOM telemetry promotes from "tool" to "core," it should land behind a stable
abstract interface so the bridge runtime depends on the abstraction, not on
UIA specifically. A first sketch:

```
DomBackend (abstract):
  list_apps() -> [AppHandle]
  AppHandle.scan_sidebar() -> [SidebarEntry]
  AppHandle.scan_active_chat(last_n: int) -> [Paragraph]   # facts/hashes, not raw text
  AppHandle.subscribe(events: [structure_changed, focus_changed]) -> EventStream
  AppHandle.invoke(element_ref) -> Result    # gated on Phase-1 click empirical
  AppHandle.select_item(element_ref) -> Result   # ditto

WindowsUiaBackend implements DomBackend     # always-on, low capability
CdpBackend implements DomBackend            # opt-in, full capability via CDP port
  (later) MacAxBackend implements DomBackend
  (later) LinuxAtSpiBackend implements DomBackend

DomService (long-lived, in-bridge):
  - holds a DomBackend per registered app
  - maintains snapshot cache + class-name registry keyed on app version
  - emits high-level events: peer_streaming, peer_idle, peer_blocked,
    sidebar_changed, active_thread_changed
  - exposed to bridge runtime; read-only subset exposed to MCP clients
```

This composes with Codex's hybrid-truth reconciler (use case 9) and
no-silent-success contract (use case 10): `DomService` is one of the evidence
sources fed into `reconciled-state.json`, never the sole authority.

## Phased Roadmap (Claude addition)

Promotion path: **investigation → spec → implementation → core**. Each phase
gates on the prior.

- [x] **Phase 0 — Empirical foundation** *(this doc)*
  - Discovery script (`.claude-state/scripts/codex-thread-names.ps1`).
  - Latency measurement, surface coverage table, scheme registration check.

- [ ] **Phase 1 — Empirical probes** *(next)*
  - 1a. `SelectionItem.Select()` on Codex sidebar row — does it switch chats
    without foreground steal? Result decides whether UIA write-side is part
    of core or stays out of scope. (CDP `Mouse.click` makes this question
    less urgent for the opt-in surface, but still relevant for always-on UIA.)
  - 1b. `Invoke()` on `Stop generating` button — does it work without
    activation?
  - 1c. ~~Claude Desktop install + UIA tree exploration~~ **Done 2026-05-02** — full tree confirmed, parity with Codex on speed; addressing model differs (Name + parent context, no Tailwind classes). `claude://` URL scheme handler confirmed; URL path schema documented (Claude.ai web research, 2026-05-02) but not yet end-to-end verified.
  - 1d. Class-name stability test across two Codex Desktop versions.
  - 1e. **`claude://` route empirical verification** — partial: `claude://claude.ai/new` verified 2026-05-02 (opens new chat in same window via single-instance redirect; no new process, no new HWND). UUID-bearing routes (`/chat/{id}`, `/project/{id}`) deferred until safe UUIDs available. **Operational consequence:** any URL-handler invocation navigates the user's current window — no "spawn new window" path exists short of CDP kill-and-relaunch.
  - 1f. **CDP launch on Codex Desktop** — kill, relaunch with `--remote-debugging-port=9222`, enumerate `/json` targets, attempt a `querySelectorAll` against the sidebar to validate the surface. Note: requires terminating any active bridge-paired Codex session, so coordinate with user / pause bridge first.
  - 1g. **`app.asar` extraction** for both apps — derives durable CSS selectors from minified renderer source. Output feeds the class-name / selector registry referenced in Phase 3.
  - **Gate:** record findings in this doc; decide write-side scope and whether CDP graduates from "opt-in mode" to a recommended profile for specific use cases (e.g. forensics, snapshot indexing).

- [ ] **Phase 2 — Targeted use case implementations** *(Tier-1 consensus)*
  - 2a. Use cases 1 + 2 together — pre-wake project+thread fail-closed and
    wake postflight. Highest impact, shared traversal pass. Land as patches
    inside `wake_codex.ps1` first; refactor into shared helper later.
  - 2b. Use case 3 — busy/idle/blocked classifier; layer into watcher
    patient-wait.
  - 2c. Use case 4 — composer safety gate.
  - 2d. **Forensic snapshotter** (Claude addition) — standalone helper
    `tools/agent-bridge/dom_snapshot.py`, cron-driven; cheap, durable.

- [ ] **Phase 3 — Promotion to core** *(architecture refactor)*
  - Implement `DomBackend` abstract interface (above).
  - Migrate Phase-2 features onto the abstraction.
  - Add event-driven subscription path; reduce polling to streaming-state
    vital signs only.
  - Document version-detection + class-name registry for Tailwind signatures.

- [ ] **Phase 4 — Distinctive 10/10s + reconciler**
  - 4a. Use case 9 — Hybrid truth reconciler with confidence model.
  - 4b. Use case 10 — No-silent-success operation results contract.
  - 4c. **Mode A' Live Mirror Panel** (Claude addition).
  - 4d. **Cross-project Codex knowledge index** (Claude addition); symmetric Claude Desktop index now feasible too.
  - 4e. **Watcher-liveness via rendered heartbeat** (Claude addition).
  - 4f. **Self-postflight for Claude Desktop** (use case 5 / G): now realistically implementable since Claude Desktop's tree exposes assistant-turn paragraphs as `ListItem` nodes — claim sentinels can be matched without TextPattern fallback.

- [ ] **Phase 5 — Cross-platform**
  - macOS AX API backend.
  - Linux AT-SPI backend.

## Open Questions

- What is the safe default cadence on Windows for UIA tree reads with no user
  noticeable jank?
- Can Codex/Claude Desktop expose stable AutomationIds or only text/role
  anchors?
- Is CDP available in any supported Desktop mode without weakening security?
- Which facts should become dashboard health fields versus private profiling
  artifacts?
- Should DOM telemetry be per-app optional, per-project optional, or mandatory
  for write-side wake providers?
- Are `InvokePattern` / `SelectionItemPattern` / `TogglePattern` writes
  non-intrusive on Codex Desktop, or does Electron's tree require activation
  for write paths in general? *(Phase-1 probe, Claude addition.)*
- If `SelectionItem.Select()` works backgrounded, should programmatic chat
  navigation become a primitive in the bridge or stay an
  operator-debugging-only escape hatch?
- Where does `DomService` live — inside `agent_bridge.py` server, as a sibling
  daemon, or as a watcher plugin? *(Claude addition.)*
