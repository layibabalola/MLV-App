# Knowledge Sharing Contract Spec

**Status:** Proposed
**Owner:** Codex implements, Claude reviews
**Scope:** Pairing consent, catch-up authorization, dormant-peer privacy gates

## Motivation

Bridge pairing is not just a routing decision. It is permission to share project
knowledge between two agent sessions. That permission can become stale: a peer
may disappear without a graceful unpair, return months later, or pair again after
the project has accumulated sensitive new context.

The bridge must not treat "same peer name" or "old unacknowledged journal
entries" as evergreen consent. Reconnection after a long dormancy should prove
that an active knowledge-sharing contract still authorizes what is about to be
shared.

## Goals

- Make pairing consent explicit, scoped, expiring, and auditable.
- Gate `CATCHUP_DIGEST` and future context-sharing tools on an active contract.
- Prevent year-old dormant peers from receiving old or new implementation
  context automatically.
- Preserve short offline catch-up for normal same-project work.
- Keep cross-project pairing stricter than same-project pairing.
- Prefer safe metadata over body sharing after a contract expires.

## Non-Goals

- No cryptographic identity or signatures in this spec.
- No encrypted-at-rest bridge state.
- No multi-machine trust model.
- No silent migration of old pairings into broad, permanent contracts.
- No direct cross-project file reads or writes.

## Contract Model

Every active pairing owns a `knowledge_contract_id`. Sessions can rotate under a
contract, but the contract defines the durable permission boundary.

Contract fields:

- `schema_version`
- `contract_id`
- `status`: `active`, `expired`, `revoked`, `superseded`
- `scope`: `same_project`, `cross_project`
- `projects`: participating project identifiers
- `agents`: participating agent names
- `created_at`
- `expires_at`
- `last_confirmed_at`
- `max_dormancy_seconds`
- `catchup_policy`
- `allowed_message_types`
- `allowed_summary_fields`
- `body_sharing`: `allowed`, `metadata_only`, `blocked`
- `requires_reconsent_after`
- `created_by_session`
- `superseded_by_contract_id`
- `revoked_at`
- `revoked_by`
- `audit_ids`

### Default Contract Policies

Same-project default:

- `scope = same_project`
- catch-up bodies allowed for short absences only
- default `max_dormancy_seconds = 30 days`
- allowed catch-up message types: implementation updates and phase summaries
- after dormancy expiry, share metadata only until the user reconsents

Cross-project default:

- `scope = cross_project`
- contract is created only by the manual nonce pairing flow
- default expiration follows the existing cross-project pair TTL
- automatic catch-up is allowed only while the link is active and unexpired
- after expiry, no bodies are shared; re-pairing is required

Long dormancy default:

- if a peer returns after `max_dormancy_seconds`, the bridge sends only an
  expired-contract notice with counts and date ranges
- it does not send old message bodies, digest bodies, implementation summaries,
  or project context
- the user must create or renew a contract before catch-up resumes

## Catch-Up Authorization

Before generating or delivering a `CATCHUP_DIGEST`, the bridge must evaluate:

1. Is there an active `knowledge_contract_id` between owner and peer?
2. Does the contract cover the current project scope?
3. Is the contract unexpired and not revoked?
4. Is the peer's dormancy below `max_dormancy_seconds`?
5. Does `catchup_policy` allow the requested message types?
6. Does the digest body obey `allowed_summary_fields` and `body_sharing`?

If any check fails, the bridge must not send the catch-up body. It should queue a
metadata-only control message:

```text
TYPE: KNOWLEDGE_CONTRACT_REAUTH_REQUIRED
CONTRACT_ID: <id>
REASON: expired | revoked | dormant_too_long | scope_mismatch | policy_denied
WITHHELD_COUNT: <n>
WITHHELD_FROM: <timestamp>
WITHHELD_TO: <timestamp>
NEXT_STEP: re-pair or renew contract explicitly
```

## Reauthorization Flow

When reauthorization is required:

1. The bridge reports what would be shared: count, date range, projects,
   message types, and contract status.
2. The user chooses one of:
   - renew with no historical catch-up
   - renew and share a bounded historical window
   - renew metadata-only
   - revoke and discard pending catch-up for that peer
3. Renewal creates a new contract or extends the existing one with an audit
   record.
4. Historical catch-up after renewal is limited to the approved window.

## State Layout

Proposed files:

```text
state/knowledge-contracts/
  <contract_id>.json
  _index.json
```

`_index.json` maps active contract lookups by `(scope, project(s), agent pair)`.
Contract files remain durable audit artifacts after expiry or revocation.

Implementation journal entries should store the contract id that authorized the
original send when available. A later digest may only include entries authorized
by the active or explicitly renewed contract.

## MCP/API Surface

Proposed tools:

- `list_knowledge_contracts(project=None, agent=None, include_inactive=False)`
- `knowledge_contract_status(contract_id)`
- `renew_knowledge_contract(contract_id, confirm=True, catchup_window=None,
  body_sharing=None)`
- `revoke_knowledge_contract(contract_id, reason=None)`
- `preview_catchup(contract_id, peer_agent, since=None, until=None)`

Existing tools that must consult contracts:

- `send_to_peer` when auto-journaling implementation updates
- `send_catchup_digest`
- `check_inbox(mark_read=True)` when it triggers catch-up
- `wait_inbox(mark_read=True)` when it triggers catch-up
- cross-project pairing and cross-project message tools

## Audit Events

Required audit actions:

- `knowledge_contract_created`
- `knowledge_contract_renewed`
- `knowledge_contract_revoked`
- `knowledge_contract_expired`
- `knowledge_contract_reauth_required`
- `catchup_digest_policy_allowed`
- `catchup_digest_policy_blocked`

Audit records must include contract id, scope, projects, agents, reason,
withheld counts where applicable, and whether bodies were shared.

## Migration

Existing same-project pairings should not be silently upgraded to indefinite
contracts. On first use after this spec ships:

- if the peer is recently active, create a short same-project contract and audit
  the compatibility creation
- if the peer has been dormant beyond the default window, create no body-sharing
  contract and require reauthorization
- cross-project links map to contracts only while their existing link is active
  and unexpired

## Acceptance Criteria

- A peer returning after the max dormancy window receives
  `KNOWLEDGE_CONTRACT_REAUTH_REQUIRED`, not a `CATCHUP_DIGEST` body.
- Same-project peers offline for a short, normal interval still receive
  coalesced catch-up digests.
- Cross-project peers never receive catch-up after the pair TTL expires.
- Revoking a contract blocks future catch-up and cross-project sends.
- Renewing a contract can approve no history, metadata-only history, or a bounded
  body-sharing history window.
- Implementation journal catch-up filters by contract id and approved window.
- Health/diagnostic output surfaces expired or reauth-required contracts.
- Tests cover active, expired, revoked, dormant-too-long, scope mismatch,
  metadata-only, and bounded-renewal cases.

## Security Notes

The core security posture is consent decay: permission to share knowledge becomes
weaker with time, not stronger. A stale local state file or old session id must
not be enough to resurrect access to sensitive project context.

This spec intentionally treats knowledge as more sensitive than routing. A
message can be technically deliverable but still blocked by contract policy.
