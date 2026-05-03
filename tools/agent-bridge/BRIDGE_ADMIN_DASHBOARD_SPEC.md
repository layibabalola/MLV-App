# Bridge Admin Dashboard Spec

**Status:** Partially implemented - backend health/pairing/contract surfaces, guided same-project pairing APIs, local launcher UX, auto-refresh, and chat-triggered dashboard opening exist; richer dashboard UI remains follow-up.
**Owner:** Codex implements, Claude reviews
**Scope:** Local web dashboard for pairings, contracts, policy status,
backpressure, revocation, and audit visibility

## Motivation

Bridge administration is currently spread across chat commands, MCP tools,
state files, and specs. Field users need a local dashboard that answers:

- Who am I paired with?
- What is this peer allowed to do?
- How long does the contract last?
- Is anything backpressured, expired, or waiting for confirmation?
- How do I revoke or renew safely?
- Are docs/policy in drift?

The dashboard should make safe operations obvious and dangerous operations
deliberate.

## Security Model

The dashboard is local-admin UI, not a remote peer surface.

Requirements:

- Bind to `127.0.0.1` by default.
- Require a local random auth token or equivalent local-only auth.
- Use CSRF tokens for mutating actions.
- Disable CORS by default.
- Escape all peer-provided text.
- Never render remote markdown as trusted HTML.
- Read effective policy from the runtime policy registry, not markdown.
- Use the same bridge APIs as natural-language local commands.
- Audit all mutating actions.

Remote peers cannot use bridge messages to trigger dashboard confirmations,
provide confirmation tokens, or modify dashboard auth settings.

## Dashboard Areas

### Overview

Shows:

- bridge health
- active projects
- active primary pairings
- active observer/advisor/auditor lanes
- active cross-project contracts
- open backpressure buckets
- contracts requiring reauthorization
- policy/doc drift warnings
- recent rejected remote-authority requests

### Pairings

Each row shows:

- friendly local name
- project alias and project id
- relationship direction
- local agent/session short id
- remote agent/session short id
- peer claimed label, marked as untrusted
- role
- permission tier
- status
- wake ownership, if any
- current inbox/backpressure status

Example:

```text
MLV App primary
Claude c4a91b2f <-> Codex 06205da2
Project: mlv-app
Role: Primary
Status: Active
```

### Knowledge Contracts

Each contract row shows:

- local alias
- contract id short form
- scope
- participating projects
- participating agents
- role and permission tier
- original contract duration
- created at
- expires at
- countdown until expiration
- max dormancy
- catch-up policy
- body-sharing policy
- last peer seen
- pending catch-up count/date range
- status: active, expiring soon, expired, revoked, reauth required

Actions:

- revoke
- renew
- preview catch-up
- approve bounded historical catch-up
- switch to metadata-only
- rename local alias
- copy full diagnostic bundle

### Guided Pairing

Implements `BRIDGE_PAIRING_USER_FLOWS_SPEC.md`.

The dashboard should:

- detect same-project vs cross-project
- show allowed pairing types
- disable unavailable pairing types with policy reasons
- explain capabilities and restrictions from runtime policy
- show contract terms
- require local confirmation before activation
- create the pairing/contract through the same backend path as chat commands

### Revocation

Revocation can start from:

- dashboard button
- local natural-language command
- remote peer voluntarily disconnecting
- contract expiry

Local-user revocation flow:

1. User chooses revoke.
2. Dashboard shows impacted peer, project, contract, pending catch-up, and
   consequence summary.
3. User confirms.
4. Contract becomes revoked.
5. Pairing is severed.
6. Future sends, wakes, and catch-up bodies are blocked.
7. Pending body-sharing digests are discarded or withheld.
8. Peer receives a minimal `CONTRACT_REVOKED` notice if allowed.
9. Audit event is recorded.

Remote voluntary disconnect skips local confirmation because it reduces remote
access, but it still records a peer-initiated event and keeps audit history.

### Backpressure And Catch-Up

Shows:

- session inbox pressure: one active unread work item
- project inbox pressure: small project coordination buffer
- rejected send count
- sender(s) waiting for resolution
- whether a `BACKPRESSURE_RESOLVED` notification is pending
- whether a `CATCHUP_DIGEST` can be sent
- whether knowledge-contract policy blocks body catch-up

User-facing copy should explain that backpressure is not message loss; it is a
flow-control signal. If body catch-up is blocked by dormancy or expiry, the UI
shows withheld counts and reauthorization options.

### Policy And Protected Docs

Shows:

- effective runtime policies
- local configurability
- source of authority
- protected docs
- doc drift status
- generated policy snapshot hash
- remote protected-doc edit proposals
- rejected remote-authority requests

Mutating actions:

- validate policy docs
- generate policy reference docs
- approve/reject protected-doc proposal
- change locally configurable policy

Actions that broaden authority require local confirmation.

### Audit

Shows an append-only timeline filtered by:

- pairing/contract id
- project
- peer
- policy id
- dashboard action
- remote request
- rejected action
- revocation/renewal

The audit UI should make it easy to answer: "Who or what caused this permission
to change?"

## Natural Language Integration

Local chat commands and dashboard actions share the same backend path.

Examples:

```text
Show active contracts.
Rename the Claude source-project contract to "Parser advisor".
Revoke the MLV App source-project advisor contract.
Approve metadata-only catch-up for that peer.
```

The local agent should display the same confirmation summary the dashboard would
display, then call the same action.

## Launcher UX

The local dashboard has two operator-friendly entrypoints:

- `tools/agent-bridge/dashboard_launcher.py` starts the local HTTP dashboard,
  prints the authenticated URL in foreground mode, opens the browser by
  default, and can run as a singleton background supervisor.
- `tools/agent-bridge/dashboard-launcher/Agent Bridge Dashboard Launcher.bat`
  is the double-click Windows launcher. It prefers `pyw` / `pythonw` so no
  long-lived console remains visible, then falls back to minimized console
  Python only if no windowless launcher is available.

In background mode the launcher writes
`state/dashboard-launcher.runtime.json` with the local URL, token, process id,
and health metadata. A second launch first health-checks that runtime file; if
the dashboard is alive it reuses the existing server and opens that URL instead
of spawning another one. The supervisor health-checks the local dashboard at a
bounded cadence and restarts the HTTP server if the server thread dies or
`/api/overview` stops responding.

The dashboard root page auto-refreshes every 5 seconds by fetching
`/api/overview?format=json` with the session token. Operators can pause live
refresh without disabling the manual "Refresh now" button. The page includes a
local Stop button that POSTs to `/api/shutdown` with the CSRF token; this exits
the background supervisor cleanly. Task Manager remains an emergency stop
fallback.

The UI may expose direct buttons only for allowlisted low-risk remediation
actions whose implementation is local and bounded, currently stale MCP server
marker cleanup and read-receipt backfill. All other recommended actions remain
copy-only instructions. Direct buttons POST to `/api/recommended-action` with the
dashboard token and CSRF header.

Local chat can also call the `open_dashboard` MCP tool. The tool starts or
reuses the in-process dashboard server, opens the tokenized URL in the default
browser, and returns only a token-free URL to chat.

## API Surface

Proposed local dashboard backend endpoints or equivalent MCP/CLI handlers:

- `dashboard_overview`
- `open_dashboard`
- `recommended_action`
- `list_pairings`
- `pairing_details`
- `start_guided_pairing`
- `confirm_guided_pairing`
- `list_contracts`
- `contract_details`
- `preview_contract_catchup`
- `renew_contract`
- `revoke_contract`
- `rename_local_alias`
- `list_policy_dashboard`
- `validate_policy_dashboard`
- `list_remote_authority_requests`
- `audit_timeline`

Implementation may expose these as internal HTTP handlers, MCP tools, or CLI
commands, but the local web server must call the same enforcement layer.

## Audit Events

Required audit actions:

- `dashboard_started`
- `dashboard_action_requested`
- `dashboard_action_confirmed`
- `dashboard_action_rejected`
- `guided_pairing_started`
- `guided_pairing_confirmed`
- `guided_pairing_cancelled`
- `contract_revoke_requested`
- `contract_revoked`
- `contract_renew_requested`
- `contract_renewed`
- `local_alias_updated`

Audit records should include local actor, action source (`dashboard` or
`local_chat`), target contract/pairing id, confirmation id, and result.

## Non-Goals

- No remote web access.
- No cloud dashboard.
- No multi-user authorization model in v1.
- No remote peer access to dashboard routes.
- No direct editing of runtime policy through markdown.

## Acceptance Criteria

- Dashboard lists active pairings and contracts with friendly names, short IDs,
  roles, status, original duration, and expiration countdown.
- User can revoke a contract early through the dashboard with confirmation.
- User can revoke through natural language and gets the same confirmation and
  backend behavior.
- Revocation severs pairing and blocks future sends, wakes, and catch-up bodies.
- Dashboard shows when body catch-up is blocked and why.
- Dashboard shows doc/policy drift from runtime policy validation.
- Remote-provided labels are escaped and marked as untrusted.
- Dashboard reads effective policy from runtime registry, not markdown.
- Mutating dashboard actions require CSRF/local confirmation.
- Tests cover dashboard read-only rendering, revoke confirmation, natural
  language revoke, expired contract display, backpressure display, policy drift
  display, and remote-label escaping.
