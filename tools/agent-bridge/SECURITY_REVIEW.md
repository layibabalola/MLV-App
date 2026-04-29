# Agent Bridge Security Review

**Status:** In progress as of 2026-04-29. This document records the current
security posture, findings, accepted assumptions, and the remaining gaps that
must be resolved before the bridge can be called fully hardened.

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

- Current status: partially hardened.
- Strengths:
  - destructive `consume_inbox.py` commands are explicitly refused
  - helper timeouts prevent unbounded hangs
  - wake exit codes are no longer treated as delivery
  - missing peer breadcrumbs are permanent no-retry failures
  - `pause_bridge` now gates watcher-fired wake commands
  - managed watcher templates now execute as argv arrays instead of shell
    strings
- legacy inline `on_message_command` entries are now coerced to argv and
  rejected if they contain shell metacharacters or cannot be parsed safely
- Required follow-up:
  - retire the legacy inline compatibility form after the deprecation window,
    then keep regression tests proving helper invocations stay argv shaped.

### `wake_codex.ps1`

- Current status: improved but still sensitive.
- Strengths:
  - direct thread navigation is preferred over ambient-window typing
  - title-marker heuristics were removed after fail-closed host behavior
  - wrong-chat no-retry handling remains available for future UUID-based checks
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

- Current status: mostly hardened.
- Strengths:
  - `probe_server.py` is safe by default and requires `--mutate` for live sends
  - migration tooling validates layout and supports dry-run first
  - recovery tooling defaults to non-destructive reporting
- Remaining work:
  - continue expanding regression coverage around moved-root rejection and
    literal path handling.

## State And Configuration Validation

- `settings.json` rejects unknown keys.
- `settings.json` rejects invalid boolean and integer types.
- destructive bucket/session tools reject deprecated `default`.
- missing peer breadcrumbs are surfaced as explicit permanent wake skips rather
  than silent retries.

## Findings

### P1 - Legacy inline watcher commands remain a compatibility surface

- Surface: `tools/agent-bridge/watcher.py`
- Risk: hand-edited legacy helper strings may still be malformed or surprising,
  even though they are no longer executed through a shell.
- Current mitigation: both managed templates and legacy inline commands are now
  executed as argv arrays; suspicious legacy strings are rejected as config
  errors with no retry loop.
- Required fix: remove the legacy compatibility path once the migration window
  closes.

### P2 - Wake helper remains UI-focus sensitive

- Surface: `tools/agent-bridge/wake_codex.ps1`
- Risk: wrong foreground target or dropped input.
- Current mitigation: direct thread deeplink, receipt-verified delivery, and
  permanent no-retry handling for non-recoverable wake failures.

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

## Not Yet Fully Reviewed

- Retirement of the legacy inline watcher compatibility path itself.
- A line-by-line audit of every historical proposal doc for stale security
  claims.
- Windows filesystem permission posture beyond the same-user trust model.

## Exit Criteria For Final Security Signoff

- Managed watcher commands execute as argv arrays without `shell=True`, and the
  legacy compatibility path is either removed or explicitly accepted as a
  bounded residual risk.
- Security-sensitive contracts have regression tests for:
  - helper invocation shape
  - settings key/type rejection
  - destructive tool ambiguity rejection
  - probe `--mutate` gate
- This document is updated with final P0/P1/P2/P3 findings and accepted risks.
