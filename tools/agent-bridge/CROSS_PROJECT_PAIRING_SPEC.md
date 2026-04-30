# Cross-Project Pairing Spec

**Status:** MVP implemented (2026-04-30)
**Owner:** Codex implements, Claude reviews
**Scope:** Manual, communication-only/read-and-advise by default

## Motivation

Users sometimes want one project thread to advise another: source-project
context and understanding should be available to the target project without
silently granting write authority. This must be deliberate, visible, and
manual. Same-project auto-pairing remains separate; cross-project pairing is
never inferred automatically.

## MVP Contract

### Manual Pairing

Both project chats must call `cross_pair_init` with:

- different `project` and `peer_project` values
- opposite roles: `advisor` and `executor`
- the same shared nonce
- `confirm_different_projects=true`

If confirmation is missing, the tool refuses activation and returns the warning:

```text
You are about to pair threads from different projects. Are you sure?
```

The nonce window is 60 seconds. One-sided or expired observations remain
pending and never activate a link. Used nonces are hashed and replay-blocked.

### Default Permission

Every new link starts as:

```text
permission_tier = read_and_advise
```

The advisor can communicate guidance, but the executor owns writes in the
target project. Advisor attempts to request write permission during init are
ignored; permission elevation is executor-only.

### Write Override

`cross_pair_promote` can change the tier to `write_with_confirmation`, but only
from the executor project and only with `confirm_write_override=true`.

The MVP does not provide cross-project write tools. The tier is stored so later
write-capable tools can gate on it, but all shipped communication remains
message-only.

### Communication Path

`send_cross_project_message` sends through an active link and wraps every body
with policy metadata:

- `TYPE: CROSS_PROJECT_MESSAGE`
- `LINK_ID`
- `FROM_PROJECT`
- `TO_PROJECT`
- `FROM_ROLE`
- `PERMISSION_TIER`
- `ROLE_POLICY`

The target receives the message in the peer project bucket. If the link is
expired, revoked, or unrelated to `from_project`, the send is rejected.

### Lifecycle

`list_cross_project_links` returns active links, optionally scoped to a project.
`cross_pair_revoke` lets either participating project revoke the link. Revoked
links cannot send more cross-project messages.

## State

Link state lives under:

```text
state/cross-project-pairs/<link_id>.json
state/cross-project-pairs/_pending.json
```

Each active link stores:

- schema version
- link id
- status
- advisor and executor project/session metadata
- permission tier
- expiration timestamp
- policy flags

## Audit

The MVP writes audit events for:

- `cross_project_pair_pending`
- `cross_project_pair_activated`
- `cross_project_pair_permission_updated`
- `cross_project_pair_revoked`
- `cross_project_message_sent`

Audit records include project names, link id, accepted/rejected state where
applicable, and permission tier for message sends.

## Security Notes

This MVP intentionally does not implement direct cross-project file reads or
writes. It ships only the pairing, policy, and communication layer so the
default behavior is safe: source advises, target writes. Future read/write tools
must gate on link status, permission tier, project role, expiration, and
revocation before touching target project state.
