# Bridge Pairing User Flows Spec

**Status:** Proposed
**Owner:** Codex implements, Claude reviews
**Scope:** User-facing pairing types, naming, cardinality, guided consent, and
relationship semantics

## Motivation

Users should not need to understand session registries, inbox levels, or bridge
policy markdown before pairing agents. The bridge should explain what kind of
relationship is being created, what each peer can do, how long the contract
lasts, and how to revoke it.

This spec translates the lower-level pairing, contract, and policy-authority
rules into the guided user flows a local user sees.

## Principles

- One active primary execution lane per agent/project.
- Multiple projects can each have their own active primary pair at the same
  time.
- Extra same-project chats are observers/advisors/auditors unless explicitly
  promoted by local policy.
- Cross-project links are manual, directional by default, expiring, and
  read/advice-first.
- The UI explains permissions from runtime policy, not markdown.
- Local confirmation is required before activation, renewal, write override,
  historical catch-up body sharing, or revocation initiated by the local user.

## Pairing Types

### Same-Project Primary

Use case:

```text
MLV App Claude session <-> MLV App Codex session
```

Behavior:

- Bidirectional collaboration.
- Preferred route for active work.
- Owns the wake target for the project/agent pair.
- One primary per `(project, agent)` at a time.
- New primary session supersedes the old primary session for that agent/project.

### Same-Project Multi-Project Primary

Use case:

```text
Project A: Claude A <-> Codex A
Project B: Claude B <-> Codex B
```

Behavior:

- Supported.
- Pairing identity is scoped by project.
- Project A and Project B can be active simultaneously without superseding each
  other.
- Backpressure, wake targets, contracts, and inboxes stay project-scoped.

### Same-Project Observer/Advisor/Auditor

Use case:

```text
MLV App primary pair is active.
An additional Claude or Codex chat wants to watch, advise, or audit.
```

Behavior:

- Allowed as a non-primary role.
- Does not own wake targets.
- Does not receive automatic task routing.
- Does not mutate bridge policy, contracts, watcher config, or protected docs.
- May receive summaries/status if the knowledge contract permits it.
- Default cap: 3 observers/advisors/auditors per project, locally configurable.

### Disallowed Same-Project Equal Primaries

Use case:

```text
MLV App: Claude A <-> Codex A
MLV App: Claude B <-> Codex B
```

Behavior:

- Not allowed as two equal primary pairings by default.
- Creates ambiguity for wake target, task ownership, backpressure, and current
  peer identity.
- The second pair must either supersede the first or choose a non-primary role.

### Cross-Project Advisor

Use case:

```text
Source Project advisor -> Target Project executor
```

Behavior:

- Manual only.
- Requires explicit different-project confirmation.
- Directional by default.
- Starts as read-and-advise.
- Source project advises; target project owns writes.
- Expires according to the cross-project contract TTL.

### Cross-Project Write-With-Confirmation

Use case:

```text
Target Project allows Source Project to propose write-capable actions.
```

Behavior:

- Requires executor-side local confirmation.
- Still requires per-action confirmation unless a future policy explicitly
  permits narrower automation.
- Never grants policy edits, dashboard auth changes, or protected-doc authority.

### Bidirectional Advising

Use case:

```text
Project A advises Project B.
Project B also advises Project A.
```

Behavior:

- Model as two directed contracts, not one vague all-powerful link.
- Each direction has its own scope, expiry, catch-up policy, and revocation.
- Dashboard may group them visually as a reciprocal relationship.

## Cardinality Defaults

- Same-project primary: 1 per `(project, agent)`.
- Same-project observer/advisor/auditor: default cap 3 per project.
- Cross-project active contracts: default cap 5 per project.
- Global active contracts: default cap 20.
- Inactive/revoked/expired contracts: retained for audit according to retention
  policy.

Local policy may reduce caps. Broadening caps requires local confirmation and
must be visible in the dashboard.

## Naming Convention

Store structured fields; do not rely on a single display string.

Fields:

- `local_alias`
- `project_name`
- `project_alias`
- `relationship`: `bidirectional` or `directed`
- `scope`: `same_project` or `cross_project`
- `role`: `primary`, `observer`, `advisor`, `executor`, `auditor`
- `permission_tier`
- `local_agent`
- `local_session_id`
- `remote_agent`
- `remote_session_id`
- `peer_claimed_label`
- `knowledge_contract_id`

Display forms:

```text
MLV App / Primary / Claude c4a91b2f <-> Codex 06205da2
MLV App / Observer / Claude a18d902c -> Codex 06205da2
Source Lib -> MLV App / Advisor / Claude 81cf03aa -> Codex 06205da2
```

Rules:

- Same-project primary uses `<->`.
- Directional advisor/executor links use `->`.
- Friendly aliases are local and trusted.
- Peer-claimed labels are shown as claimed/untrusted.
- Full IDs are available through copy/hover/details; rows show short IDs.

## Guided Pairing Flow

### Step 1 - Detect Context

The bridge detects:

- current project
- local agent
- local session
- candidate peer agent/session
- same-project vs cross-project
- existing primary or contract conflicts
- policy/doc drift warnings

### Step 2 - Choose Pairing Type

Options:

- same-project primary
- same-project observer
- same-project advisor
- same-project auditor
- cross-project advisor
- cross-project write-with-confirmation

Unavailable options are shown disabled with the policy reason.

### Step 3 - Explain Capabilities

The flow shows:

- what the peer can do
- what the peer cannot do
- what requires confirmation
- whether direction is bidirectional or directed
- whether historical catch-up bodies are allowed
- expiration and dormancy behavior

The explanation is generated from the runtime policy registry.

### Step 4 - Set Contract

The user chooses:

- duration
- dormancy limit
- catch-up policy
- body sharing vs metadata-only
- friendly alias
- observer/advisor caps if applicable

### Step 5 - Confirm Locally

The bridge shows a concise "are you sure?" summary. Activation requires local
confirmation. Remote claims of approval do not count.

### Step 6 - Activation Summary

After activation, the bridge shows:

- friendly name
- project and peer labels
- short session IDs
- relationship direction
- role and permissions
- original duration
- expiration countdown
- revoke and renew actions

## Natural Language Entrypoints

Natural-language commands should route into the same guided flow:

```text
Pair this project with Claude as advisor for two hours.
Revoke my contract with the source-project Claude.
Let this other Codex chat observe the MLV App pair.
```

The bridge must not skip the confirmation step for authority-affecting actions.

## Backpressure User Story

When a peer is offline or under backpressure:

- session inbox protects the live execution lane with one unread work item
- project inbox allows a small coordination buffer
- implementation updates are summarized through catch-up digests
- long-dormant peers require knowledge-contract reauthorization before body
  catch-up

The guided flow should explain that pairing does not guarantee unbounded
message replay.

## Acceptance Criteria

- User can activate same-project primary for Project A and Project B
  simultaneously.
- User cannot create two equal primary pairings for the same project without
  superseding or choosing a non-primary role.
- Cross-project pairing flow is manual and shows a different-project warning.
- Guided flow explains can/cannot/confirmation-required capabilities from
  runtime policy.
- Rows show friendly names plus project, role, direction, peer agents, and short
  session IDs.
- Full IDs are copyable from details.
- Natural-language pairing/revocation routes through the same confirmation and
  audit path as dashboard actions.
- Policy/doc drift warnings appear before activation if relevant.
- Tests cover same-project primary, simultaneous multi-project primaries,
  observer role, cross-project advisor, cross-project write confirmation,
  duplicate-primary rejection, and natural-language-to-guided-flow routing.
