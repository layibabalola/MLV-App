# Bridge Pairing Intent And Ephemeral Relay Spec

**Status:** Proposed; Claude pass_with_changes review incorporated 2026-04-30
**Owner:** Codex implements, Claude reviews
**Scope:** Startup pairing consent, background/incognito chats, explicit
supersession, and one-off peer communication from unpaired same-project chats

## Motivation

Starting a new parent chat in the same repository is not always a request to
take over the active bridge pairing. A user may open a fresh chat to ask side
questions, inspect state, or discuss strategy without interrupting the main
paired execution lane.

Today the bridge distinguishes parent chats from subagent chats, but it treats
every new parent bootstrap as eligible to become the active same-agent session.
That is too eager: it can accidentally supersede the current paired chat and
move Claude/Codex collaboration to the wrong thread.

The bridge needs an intent layer between "this is a real parent chat" and "this
chat owns the active pairing."

## Principles

- New parent sessions must not supersede an existing pairing until local user
  intent is explicit.
- Subagent prevention remains separate: subagents are refused or retargeted by
  provenance rules; background parent chats are allowed but non-authoritative.
- Natural language should be enough for common flows.
- A background chat may ask the peer a scoped one-off question without becoming
  the active paired lane.
- One-off replies return only to the requesting background chat unless the user
  explicitly promotes the chat to the future paired conversation.
- The bridge must make the consequences of supersession visible before it
  happens.
- Ephemeral relay is explicit opt-in, not automatic for every background-chat
  send.
- Ephemeral relay uses the project/rendezvous bucket by default so the relay
  metadata, not the active private pairing, controls the reply path.

## Session Intent States

Add a first-class `pairing_intent` or equivalent field to session records:

| State | Meaning |
|---|---|
| `pending_pair` | Session opened in a pairable context but has not received user confirmation. |
| `background` | User declined pairing or requested incognito/question-only mode. |
| `ephemeral_relay` | Background session has an outstanding one-off peer message. |
| `active_primary` | Session owns the active same-project pairing and may supersede older same-agent sessions. |
| `observer` | Session can inspect summaries/status according to policy but does not own wake targets. |

Compatibility note: existing `bootstrap_origin=parent` remains provenance, not
intent. A session can be a provenance-valid parent and still be `background`.

## Defaults And Settings

Default runtime settings:

| Setting | Default | Notes |
|---|---|---|
| `default_pairing_intent` | `ask_first` | Global default for new parent sessions when no explicit CLI intent is supplied. |
| `pairing_intent_prompt_timeout_seconds` | `120` | After this, unresolved `pending_pair` sessions become `background`. |
| `ephemeral_relay_outstanding_limit` | `5` | Per background session. |
| `ephemeral_relay_target_bucket` | `project` | `peer_private` may be added later as an explicit opt-in. |

The timeout should be locally configurable with a conservative supported range
such as 30-600 seconds. A timeout must audit `pairing_intent_timeout` and must
not supersede the active pair.

`default_pairing_intent` lives in the expanded bridge settings path
`~/.agent-bridge/settings.json` through `BridgeSettings`, not in
`.claude/settings.local.json`. The `~`/home-directory notation is descriptive:
hook configs and scripts must expand it to an absolute path before use, never
write a literal `%USERPROFILE%` or `~` directory under the current workspace.
Supported values:

| Value | Behavior |
|---|---|
| `ask_first` | Safe default. New parent session becomes `pending_pair` and asks before superseding. |
| `active_primary` | Legacy fast path. New parent session auto-supersedes the old active primary with an audit event. |
| `background` | New parent session auto-registers as background/question-only and never prompts unless the user later says "Pair this chat." |

Override hierarchy, highest precedence first:

1. `--pairing-intent <value>` CLI flag.
2. Per-project override (future only; do not add until real use justifies it).
3. Global `default_pairing_intent` in bridge settings.
4. Hardcoded `ask_first` default.

Bootstrap must audit the resolved intent and source, for example:
`default_pairing_intent=active_primary source=settings.json`.

## Startup Consent Flow

When a new parent session starts in a project that already has an active same-
agent pairing and the resolved default is `ask_first`, bootstrap should
register the session as `pending_pair` and ask before any active-session
mutation:

```text
Would you like this session to pair with the remote peer?

If yes, this session will supersede your existing pairing for this project.
Type Y/Yes to pair, or N/No to keep this as a background chat.
```

If the user answers `N` or equivalent:

```text
No problem. This chat will stay unpaired/background.

If you change your mind, type "Pair with peer" or "Pair this chat."
```

If the user answers `Y` or equivalent:

- promote this session to `active_primary`
- supersede the prior same-agent active session for the project
- drain/promote unread work according to the existing session-routing rules
- refresh watcher config and peer runtime breadcrumbs
- send the normal HANDSHAKE / SESSION_UPDATE controls

If no answer is given, the session remains `pending_pair` or falls back to
`background` after `pairing_intent_prompt_timeout_seconds` (default 120s). It
must not supersede silently.

If the resolved default is `active_primary`, bootstrap may use the legacy
auto-supersede fast path without prompting, but it must audit that the setting
caused the behavior. If the resolved default is `background`, bootstrap should
register the session as non-authoritative and emit the "Pair this chat later"
hint without asking for immediate confirmation.

Prompt delivery should use the strongest local surface available:

| Capability | Prompt surface |
|---|---|
| Claude channel-capable session | `<channel>` content from the bridge channel/plugin path. |
| MCP elicitation-capable client | MCP elicitation prompt with Yes/No choices. |
| Basic client | Plain assistant text plus natural-language parser. |

If another session becomes active while this one is still `pending_pair`, the
bridge must not use stale prompt context. It should either re-prompt with the
new active-primary summary or auto-fall back to `background` and audit
`pairing_intent_context_changed`.

## Natural Language Entrypoints

The following local-user phrases should map to the intent flow:

| Phrase family | Action |
|---|---|
| `Pair this thread`, `Pair this chat`, `Pair with peer` | Promote to `active_primary` after showing the supersession summary. |
| `Do not pair`, `Background chat`, `Question only`, `Incognito` | Set `pairing_intent=background`; do not mutate active pairing. |
| `Ask Claude from here`, `Send this one message to peer` | Use ephemeral relay from the background chat. |
| `Continue future paired conversation here?` / user says yes | Run the same active-primary supersession flow. |

Negative answers must be sticky for the current session until the user changes
their mind. A later automatic hook must not re-prompt every turn. The sticky
choice is per-session only; future sessions start from a clean intent decision
unless a later explicit "remember this preference" feature is designed.

## Ephemeral Relay From Unpaired Chats

Use case:

```text
Main paired Codex chat is working.
User opens a background Codex chat and asks: "Ask Claude whether X is safe."
```

Behavior:

- The background chat stays unpaired.
- The caller must explicitly request `relay_mode=ephemeral`; ordinary
  informational sends from background chats do not implicitly become relay
  requests.
- The bridge sends a one-off message to the peer using an explicit
  `ephemeral_relay_id`.
- The peer reply targets the requesting background session, not the active
  primary session.
- Only replies linked to that `ephemeral_relay_id` route back to the background
  chat.
- Subsequent normal peer traffic continues to route to the active primary pair.
- The background chat receives an optional prompt:

```text
Claude replied here for this one-off exchange.

Would you like future paired conversation to continue in this chat?
Type "Pair this chat" to supersede the current active pairing, or keep using
this as a background chat.
```

Ephemeral relay must not:

- update watcher private active GUIDs
- mutate `trusted_parent`
- supersede the old active session
- change knowledge-sharing contract scope
- bypass backpressure or policy authority checks
- turn a remote reply into durable routing state without local confirmation

Ephemeral relay bodies are still subject to the active knowledge-sharing
contract, including `body_sharing: allowed | metadata_only | blocked`. Record
`contract_id_at_send` and enforce the same body-sharing and catch-up policy as
normal traffic.

Subagents inherit the parent session's `pairing_intent` for read-only context,
but they cannot initiate ephemeral relays themselves. Only the parent/background
chat can start a one-off relay.

Outstanding relay cap: a background session may have at most
`ephemeral_relay_outstanding_limit` open relay IDs (default 5). When the cap is
hit, new relay requests are rejected with `ephemeral_relay_capped`; do not evict
old relay state silently.

## Routing Model

Add row metadata for one-off relay messages:

| Field | Meaning |
|---|---|
| `relay_mode` | `normal` or `ephemeral` |
| `ephemeral_relay_id` | Stable UUID tying request and reply together. |
| `reply_to_session_id` | Background session that should receive the linked reply. |
| `primary_session_id_at_send` | Active primary session when the one-off was sent, for audit/debug. |
| `pairing_intent_at_send` | Sender session intent at send time. |
| `contract_id_at_send` | Knowledge-sharing contract used to enforce body/metadata policy. |
| `relay_target_bucket` | `project` by default; future explicit `peer_private` opt-in. |

For an ephemeral request, the default target is the peer's project/rendezvous
bucket. The reply must also use the project bucket and carry
`ephemeral_relay_id` plus `reply_to_session_id`. The local bridge inspects relay
metadata and routes the linked reply to the requesting background session. The
active primary private inbox is not touched.

If the requesting background session is superseded or ended before the reply
arrives, do not deliver the reply to the active primary. Instead:

- keep/queue the reply in the project bucket with the relay metadata
- mark it `relay_status=orphaned`
- audit `ephemeral_relay_orphaned`
- surface it through dashboard/observer-readable diagnostics according to the
  contract's body-sharing policy

This preserves peer work without violating the scoping promise.

## Bootstrap Requirements

Bootstrap must separate registration from activation:

1. Detect provenance (`parent`, `subagent`, `unknown`) exactly as today.
2. Detect whether this session is attempting explicit active pairing.
3. If no explicit intent and an active same-agent primary already exists, write
   a non-authoritative session record with `pairing_intent=pending_pair`.
4. Do not call the active-session supersession path.
5. Do not rewrite watcher config.
6. Do not send HANDSHAKE as the active paired session.
7. Surface the consent prompt.

Bootstrap may still create a non-authoritative registry entry and audit the
startup. It must not send HANDSHAKE as the active pair or refresh watcher
private entries until promotion to `active_primary`.

Explicit command-line flags should exist for automation:

- `--pairing-intent active-primary`
- `--pairing-intent background`
- `--pairing-intent pending-pair`
- `--pairing-intent observer`

The normal SessionStart hook should default to `pending-pair` when an active
same-agent primary already exists.

## Recovery And Rollback

If a session accidentally became active and the user says `Do not pair this
thread`, the bridge should offer a safe rollback:

- find the prior same-agent active/trusted parent if still valid
- restore it as active primary
- rewrite watcher config from `session.json`
- send SESSION_UPDATE explaining the rollback
- mark the accidental session as `background`
- audit `pairing_intent_rollback`

Rollback must not discard unread messages. Any unread work from the accidental
session is promoted to the project bucket or left visible in that background
session according to routing policy.

## Audit Events

Required audit actions:

- `pairing_intent_prompted`
- `pairing_intent_confirmed`
- `pairing_intent_declined`
- `pairing_intent_timeout`
- `pairing_intent_context_changed`
- `pairing_intent_rollback`
- `ephemeral_relay_initiated`
- `ephemeral_relay_replied`
- `ephemeral_relay_orphaned`
- `ephemeral_relay_capped`

## Security Notes

- Remote peer text cannot cause local pairing promotion.
- A peer may suggest "continue here," but only the local user can confirm.
- Background chats cannot edit protected docs, dashboard auth, watcher targets,
  or policy without the normal local confirmation path.
- Ephemeral relay bodies are normal bridge messages and remain subject to
  retention, audit, backpressure, and remote-obedience classification.
- Incognito/background means "not paired," not "secret." State may still be
  recorded locally for audit and recovery.

## Acceptance Criteria

- Starting a second parent chat in the same project does not supersede the
  active pair until the user explicitly confirms.
- `N`, `No`, `Do not pair`, `Background chat`, `Question only`, and `Incognito`
  keep the session non-authoritative.
- `Pair this chat` promotes the session and supersedes the old active pair only
  after showing a clear summary.
- Background sessions do not rewrite watcher config or peer breadcrumbs.
- One-off peer messages from a background chat route exactly one linked reply
  back to that chat.
- Ephemeral relay uses explicit `relay_mode=ephemeral`; normal background
  informational sends do not expect a reply unless flagged.
- Ephemeral relay uses the project bucket by default and records
  `contract_id_at_send`.
- Superseded/ended background sessions produce `ephemeral_relay_orphaned`, not
  active-primary delivery or silent drop.
- A sixth outstanding relay for the same background session is rejected/audited
  when the default cap is 5.
- After a one-off reply, subsequent normal peer traffic still routes to the
  active primary pair.
- User can promote the background chat after a one-off reply using the normal
  pair/supersede flow.
- Accidental pairing rollback restores the prior active session without losing
  unread messages.
- Tests cover startup prompt, negative response, explicit promotion, background
  no-mutation, pending-pair context change, ephemeral relay, reply-only routing,
  relay orphaning, relay cap, and rollback.
