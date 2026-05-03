# Agent Bridge Security Review

**Status:** Phase 17 review snapshot updated 2026-05-01. No P0 issues are
open under the same-user local-machine trust model. The bridge is locally
signable for the reviewed v1 surfaces with the bounded residuals below; the
broader "10/10" product call still requires the non-security roadmap items and
any explicitly requested live Desktop wake dogfood.

## Trust Boundaries

- `Claude` and `Codex` are trusted peers at the same local-user privilege
  level, but either may still send malformed or stale bridge traffic.
- MCP clients are trusted to call documented tools, but tool arguments are
  treated as untrusted input.
- The watcher and wake helpers execute local processes and touch shared state;
  they are high-risk boundaries even though they are local-only.
- Shared state files under the bridge root are integrity-sensitive but not
  treated as secret storage.
- Desktop UI wake surfaces are inherently lossy and must never be treated as
  delivery truth.
- The local admin dashboard backend is a local authority surface. Remote peer
  messages may be displayed there only as untrusted data; they cannot trigger
  confirmations or supply confirmation tokens.
- Tenant/schema/auth metadata are v2 local boundaries today. The bridge still
  assumes one local user, but transport helpers must not leak or rewrite rows
  tagged for another tenant.
- `settings.json`, watcher config, and bridge-root manifests are local operator
  configuration boundaries. Remote peers may suggest changes, but runtime code
  must not treat remote prose as configuration authority.
- Knowledge-sharing contracts are authority boundaries for catch-up and
  cross-project body sharing. Expired, revoked, or missing contracts must block
  historical body disclosure.
- Protected policy docs and the runtime policy registry are local authority
  boundaries. Markdown may describe policy, but effective permissions come from
  runtime policy state and protected-doc confirmation paths.
- MCP tool inputs that trigger filesystem writes, state rewrites, or process
  spawning are untrusted local inputs even when the MCP client itself is
  trusted.
- Audit logs, implementation journals, and health-dashboard output are
  diagnostic boundaries: they may contain sensitive metadata and must escape or
  hash remote-origin text where appropriate.

## Threat Model

Reviewed attack classes and current mitigation state:

- **Command injection:** managed watcher commands execute as `shell=False` argv
  arrays; toast/balloon PowerShell snippets use `-EncodedCommand`; legacy inline
  watcher commands are parsed to argv or rejected on a narrow forbidden-character
  set. Residual risk remains until legacy inline compatibility is removed.
- **Path traversal / split-brain roots:** bridge-root resolution is centralized,
  moved-root manifests are rejected, migration defaults to dry-run/backup, and
  helper scripts honor explicit root-derived paths.
- **State tampering / corrupt JSON:** shared storage helpers use atomic writes,
  cross-process locks, UUID temp files, and corrupt-JSON-safe reads on watcher
  hot paths.
- **Message spoofing / stale-session takeover:** session routing requires active
  sender context for normal work, superseded sessions are rejected/escalated, and
  guided pairing prevents silent active-primary replacement.
- **Replay / dedupe bypass:** message ids and receipt fields are durable JSONL
  rows; current mitigation is receipt/backpressure state, not cryptographic
  signatures. Stronger replay-proof identity remains out of local-v1 scope.
- **Denial of service / backpressure wedging:** per-bucket backpressure, health
  panel debt reporting, stale-unread watchdog, receipt cleanup, and wake breaker
  telemetry make wedged messages visible and recoverable.
- **Stale-contract knowledge leakage:** catch-up and cross-project sharing are
  gated by explicit knowledge contracts with expiry/revocation semantics.
- **Prompt/log exfiltration:** state files are treated as local-user readable;
  dashboard output escapes remote markdown, classifier audits hash forbidden
  request text, and no at-rest secrecy is claimed.
- **Markdown-policy drift escalation:** runtime policy is authoritative over
  markdown; protected doc edits from remote peers become proposals or require
  local confirmation.
- **Unsafe destructive tool use:** destructive bucket/session tools reject
  deprecated `default`, require explicit target names, and keep probe tooling
  non-mutating unless `--mutate` is supplied.

## Explicit Assumptions

- Same-user local machine only; no network listener is part of the bridge.
- State files may contain sensitive prompts and should not be copied outside
  the bridge root silently.
- Local filesystem access by the same user is in scope as a trust assumption,
  not something the bridge can harden cryptographically.
- Recovery and migration tools are operator-invoked maintenance paths, not
  normal message-delivery paths.

## Shell And Process Boundary Audit

### `watcher.py`

- Current status: hardened with a bounded legacy-compatibility residual.
- Strengths:
  - destructive `consume_inbox.py` commands are explicitly refused
  - helper timeouts prevent unbounded hangs
  - wake exit codes are no longer treated as delivery
  - missing peer breadcrumbs are permanent no-retry failures
  - `pause_bridge` now gates watcher-fired wake commands
  - managed watcher templates now execute as argv arrays instead of shell
    strings
  - private watcher entries can resolve the active session from `session.json`
    at poll time, with stale `session_id` values treated as fallback snapshots
- legacy inline `on_message_command` entries are now coerced to argv and
  rejected if they contain the current forbidden characters `&`, `|`, `;`, `>`,
  `<`, or backtick, or cannot be parsed safely. This is a compatibility guard,
  not a complete shell metacharacter filter; remove the legacy path rather than
  expanding reliance on it.
- Windows toast and legacy balloon fallbacks pass PowerShell through
  `-EncodedCommand`; managed wake helpers still execute as `shell=False` argv
  arrays.
- Required follow-up:
  - retire the legacy inline compatibility form after the deprecation window,
    then keep regression tests proving helper invocations stay argv shaped.

### `wake_codex.ps1`

- Current status: hardened but intentionally treated as an intrusive UI wake
  helper.
- Strengths:
  - direct thread navigation is preferred over ambient-window typing
  - title-marker heuristics were removed after fail-closed host behavior
  - wrong-chat no-retry handling remains available for future UUID-based checks
  - targeted wake mode requires a thread id, constant wake text, duplicate
    target verification, a 500 ms pre-send race cap, and post-typing telemetry
  - UIA pre-flight preserves non-empty drafts, defers active typing, and fails
    closed when UIA is unavailable unless a local debug override is supplied
- Open risk:
  - SendKeys remains focus-sensitive and depends on the Desktop app honoring the
    deeplink target.
- Accepted risk:
  - local desktop focus races are acceptable only because receipt metadata, not
    helper exit, is the delivery source of truth.

### `codex_*` hook scripts

- Current status: low-risk reminder tooling.
- Strengths:
  - documented as best-effort reminders only
  - not allowed to consume or mark inbox messages
- Remaining work:
  - keep docs aligned so operators do not over-trust these hooks as reliable
    automation.

### Bootstrap, recovery, compaction, migration, and probe CLIs

- Current status: hardened for reviewed local-v1 paths.
- Strengths:
  - `probe_server.py` is safe by default and requires `--mutate` for live sends
  - migration tooling validates layout and supports dry-run first
  - recovery tooling defaults to non-destructive reporting
  - bridge-root resolution is centralized and helper scripts honor
    `AGENT_BRIDGE_ROOT` or explicit root-derived paths instead of hardcoded
    `%USERPROFILE%\.agent-bridge`
- Remaining work:
  - continue expanding regression coverage around moved-root rejection and
    literal path handling.

## State And Configuration Validation

- `settings.json` rejects unknown keys.
- `settings.json` rejects invalid boolean and integer types.
- destructive bucket/session tools reject deprecated `default`.
- missing peer breadcrumbs are surfaced as explicit permanent wake skips rather
  than silent retries.
- inbox and audit rows now carry schema/tenant/machine metadata through the
  local auth/transport layer.
- scoped inbox rewrites preserve other local sessions and foreign-tenant rows.
- dashboard audit reads are tenant-filtered and markdown table cells are HTML
  escaped before rendering.
- remote-authority classifier input is NFKC-normalized, Unicode format controls
  are stripped, oversized text is rejected at `MAX_MESSAGE_BYTES`, and
  authority-tinged ambiguity fails closed to local confirmation.
- guided pairing activation is confirmation-gated for same-project primary
  changes, and subagent/background sessions cannot silently become active
  primaries.
- health-panel and stale-unread watchdog surfaces are read-only by default and
  present mutating recovery as explicit operator actions.

## Findings

### P0 - None open

No currently known issue permits remote code execution, silent bridge-message
consumption, or remote policy-authority escalation under the local-only trust
model.

### P1 - Legacy inline watcher commands remain a compatibility surface

- Surface: `tools/agent-bridge/watcher.py`
- Risk: hand-edited legacy helper strings may still be malformed or surprising,
  even though they are no longer executed through a shell.
- Current mitigation: both managed templates and legacy inline commands are now
  executed as argv arrays; suspicious legacy strings are rejected as config
  errors with no retry loop.
- Required fix: remove the legacy compatibility path once the migration window
  closes.

### P1 - Targeted Codex wake needs optional live Desktop dogfood before broad distribution

- Surface: `tools/agent-bridge/wake_codex.ps1`
- Risk: the remaining intrusive composer path now has a UIA pre-flight gate,
  draft reinsertion, clipboard restore, fail-closed UIA handling, and safe
  audit metadata, but intrusive live Desktop smoke coverage is still an
  operator-visible dogfood step.
- Required fix before broad default distribution: run live Desktop smoke
  scenarios: idle-empty, idle-with-draft, active typing deferral, and
  UIA-unavailable failure behavior.

### P2 - Wake helper remains UI-focus sensitive

- Surface: `tools/agent-bridge/wake_codex.ps1`
- Risk: wrong foreground target or dropped input.
- Current mitigation: direct thread deeplink, receipt-verified delivery, and
  permanent no-retry handling for non-recoverable wake failures.

### P2 - Real Claude wake remains unavailable

- Surface: `tools/agent-bridge/wake_claude.ps1`
- Risk: Codex-to-Claude responsiveness still depends on the in-context Claude
  Monitor rather than a safe external Desktop wake helper.
- Current mitigation: `wake_claude.ps1` is diagnostic-only and exits 20 instead
  of attempting unverified SendKeys against a generic Claude Desktop window.
- Why P2, not P1: loss of the Claude Monitor after compaction causes delayed or
  missed responsiveness, not message loss or remote authority escalation. The
  queue remains durable and bootstrap reminders make the limitation visible.

### P3 - Shared state files are local-user readable

- Surface: bridge root and state files
- Risk: prompt/history disclosure to the same local user or their other
  processes.
- Accepted because: the bridge is explicitly local-only infrastructure and does
  not claim at-rest secrecy.

## Fixed Or Improved In This Branch

- Wake success is receipt-based rather than spawn-based.
- Missing peer breadcrumbs no longer loop retries forever.
- `pause_bridge` now suppresses watcher-fired wake commands while still letting
  notifications surface.
- Settings validation has explicit coverage for unknown keys and invalid types.
- Managed watcher templates now execute as argv arrays with regression coverage.
- Legacy inline watcher commands are now parsed to argv or rejected as config
  errors instead of running through `shell=True`.
- Probe tooling remains non-mutating by default.
- Added local auth/transport seams (`core/auth.py`, `core/transport.py`) with
  schema v2 tenant/machine stamping for inbox and audit rows.
- Added configurable bridge-root support across remaining helper seams, with
  `AGENT_BRIDGE_ROOT` and explicit root-derived paths replacing hidden
  `%USERPROFILE%\.agent-bridge` assumptions.
- Fixed a scoped rewrite bug where `check_inbox(session_id=...)` and similar
  filtered operations could rewrite a mailbox from a partial local view.
- Added tenant-filtered dashboard audit reads and escaped markdown rendering
  for dashboard table cells.
- Added a remote-obedience classifier that rejects and audits forbidden remote
  authority requests without storing raw request text.
- Added dynamic watcher active-session binding (`session_id_source:
  "active_session"`) so stale private GUIDs in `watcher-config.json` do not
  strand current-session notifications after session rotation.
- Added Claude bootstrap Monitor reminders in `bootstrap_session.py` output and
  stderr so Claude SessionStart hooks no longer depend only on prose memory.
- Added dashboard/local-chat shared backend actions for contract revoke,
  renew, and alias update with explicit confirmation for authority-changing
  operations.
- Added same-project guided pairing backend actions that require local
  confirmation before replacing an active primary and keep subagent/background
  sessions out of active-primary promotion by default.
- Added health panel recommended remediation actions, stale-unread detection,
  and receipt-debt cleanup surfaces so delivered-but-unread wake gaps are
  visible and repairable without JSONL spelunking.
- Added fail-closed `wake_claude.ps1` diagnostics so Claude parity work has an
  audited safe boundary instead of an unsafe best-guess SendKeys helper.
- Added safe MCP payload bisection to `probe_server.py`. Direct stdio probing
  accepted a 6 KB non-mutating classifier payload on 2026-04-30, which points
  the earlier `-32602` long-message failure toward the desktop/client layer
  rather than the bridge server validator.
- Added bounded Hypothesis routing properties and a concurrent project-send
  harness for sender-liveness, session-target escalation, agent-level work
  rejection, and no-lost-row/unique-id concurrency behavior.
- Changed the legacy Windows balloon notification fallback to use
  `-EncodedCommand`, matching the primary toast path and avoiding raw
  PowerShell `-Command` script transport.
- Added hook canaries for the local `agent_bridge.py check-inbox` CLI contract
  and retrying reminder-log writes, so hook success requires a verifiable
  interface/artifact rather than silent exit code 0.
- Regression suite now covers 232 bridge tests, plus compile-time validation
  via `py -3 -m compileall -q tools\agent-bridge`.

## Not Yet Fully Reviewed

- Retirement of the legacy inline watcher compatibility path itself.
- A line-by-line audit of every historical proposal doc for stale security
  claims.
- Windows filesystem permission posture beyond the same-user trust model.
- Richer guided-pairing activation UI beyond the backend confirmation path,
  safe bootstrap prompt, non-primary cap, and dashboard-visible session status.
- Intrusive live Desktop validation of the `wake_codex.ps1` targeted SendKeys
  path.
- Real symmetric Claude wake remains blocked on a verified thread-addressable
  Desktop target. The shipped `wake_claude.ps1` is diagnostic-only and refuses
  SendKeys by default; current Claude Monitor is guarded at bootstrap but
  remains defense-in-depth rather than an external wake helper.

## Exit Criteria For Final Security Signoff

- Managed watcher commands execute as argv arrays without `shell=True`, and the
  legacy compatibility path is either removed or explicitly accepted as a
  bounded residual risk.
- `PREFLIGHT_DETECTION_SPEC.md` is implemented and tested; no normal wake path
  clears a non-empty Codex composer without draft preservation.
- Security-sensitive contracts have regression tests for helper invocation
  shape, settings key/type rejection, destructive tool ambiguity rejection,
  probe `--mutate` gating, tenant-filtered transport rewrites,
  remote-obedience classification, and dashboard backend confirmation paths.
- This document is updated with final P0/P1/P2/P3 findings and accepted risks.

## Phase 17 Disposition

Phase 17 is complete for the reviewed local-v1 security scope as of 2026-05-01
after the Claude stranger-review amendment: trust boundaries and threat model
classes are documented, process/dashboard/wake boundaries have regression
coverage, and open risks are explicitly ranked above. The remaining items are
accepted residuals, product-hardening follow-ups, or explicitly scoped
roadmap/test-depth gaps rather than known P0 security defects.
