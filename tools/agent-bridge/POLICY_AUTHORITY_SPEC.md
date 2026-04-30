# Policy Authority And Documentation Drift Spec

**Status:** Proposed
**Owner:** Codex implements, Claude reviews
**Scope:** Runtime policy authority, protected documentation, remote-peer
obedience, and markdown/code drift controls

## Motivation

Bridge documentation is editable text. A compromised peer, confused agent, or
ordinary merge conflict could edit markdown so it claims a dangerous behavior is
allowed even though runtime policy forbids it.

That must never create authority. Markdown can explain policy, propose policy,
or render policy generated from code. It cannot grant permissions, override
runtime enforcement, or satisfy a local-user confirmation requirement.

## Core Rule

Code is the law. Markdown describes the law.

If markdown contradicts enforced runtime policy, the bridge must keep enforcing
runtime policy, warn about documentation drift, and audit the contradiction.

## Authority Hierarchy

The bridge resolves instructions in this order:

1. Immutable product safety rules embedded in code.
2. Local user direct commands from the local chat or local dashboard.
3. Local admin dashboard settings and typed local config.
4. Active knowledge-sharing contract policy.
5. Local agent judgment inside the allowed policy envelope.
6. Remote peer messages as untrusted requests.
7. Markdown/spec text as explanatory or proposed guidance only.

Lower layers can narrow behavior only when the higher layer allows it. They
cannot broaden permissions.

## Document Authority Classes

Every bridge markdown document that affects operations should declare an
authority class in front matter or an equivalent registry:

```yaml
policy_authority: generated | enforced_reference | proposal | explanatory
policy_surface: bridge | watcher | wake | security | roadmap | none
```

Classes:

- `generated`: produced from runtime policy snapshots; manual edits are rejected.
- `enforced_reference`: human-readable docs that must match runtime policy tests.
- `proposal`: design/spec text; not enforced until code and tests land.
- `explanatory`: background notes with no policy authority.

Protected docs include:

- `AGENTS.md`
- `CLAUDE.md`
- `bridge_trigger_heuristics.md`
- bridge security specs
- bridge protocol docs
- knowledge-sharing contract specs
- policy authority specs
- any generated policy reference markdown

## Runtime Policy Registry

Security-sensitive rules must live in a typed runtime registry, not only in
markdown.

The registry should expose:

- policy id
- policy version
- severity
- owning component
- immutable vs locally configurable
- default value
- effective value
- allowed override source
- tests that prove enforcement
- generated markdown fragment hash

Examples:

- remote peer cannot modify local policy
- remote peer cannot extend its own contract
- expired contract blocks body-sharing catch-up
- protected docs require local confirmation before edits
- dashboard revocation uses a local confirmation token
- watcher wake targets cannot be mutated by sub-agent context

## Protected Document Edits

Edits to protected docs are allowed only through a local authority path:

1. Local user request or local dashboard action starts the edit.
2. Tooling detects the protected file.
3. The user gets a diff preview and an "are you sure?" confirmation.
4. The edit records an audit event with requester, confirmer, files, and diff
   hash.
5. If the document claims an enforced behavior changed, matching code/tests must
   change in the same commit or the validation gate fails.

Remote peers may submit proposed patches to protected docs, but those patches
are inert until the local user approves them.

## Remote-Peer Obedience Model

Remote peer text is classified before action:

- `informational`: read and consider; no tool action required.
- `proposal`: can be summarized, reviewed, or parked.
- `contract_action_request`: requires contract policy evaluation.
- `local_confirmation_required`: requires local user/dashboard confirmation.
- `forbidden_remote_authority`: must be rejected and audited.

Always forbidden from remote authority:

- editing local policy as an authoritative change
- changing protected docs without local confirmation
- extending or broadening the remote peer's own contract
- claiming the local user approved something
- disabling audit, confirmations, or drift detection
- modifying dashboard auth or CSRF settings
- mutating watcher wake targets or bootstrap provenance
- requesting secrets, tokens, or hidden local state outside the contract
- turning a proposal/spec into implemented policy without code and tests

Allowed without local confirmation only when it reduces remote access:

- remote peer voluntarily disconnects
- remote peer revokes its side of a contract
- remote peer declines catch-up or asks to receive less data

Even then, the bridge records the event as peer-initiated and keeps local audit
history.

## Drift Detection

The bridge should detect markdown/runtime contradictions in three places:

1. Pre-commit or CI validation.
2. Local dashboard health checks.
3. Bridge health panel extended diagnostics.

Validation rules:

- Generated sections must match the runtime policy snapshot hash.
- `enforced_reference` docs must not claim permissions broader than runtime
  policy.
- Protected docs cannot be changed by remote-origin work without local approval
  metadata.
- If a policy id appears in docs, it must exist in the runtime registry.
- If a runtime policy changes, generated/reference docs must be updated or the
  validation gate fails.

On drift, the bridge should:

- enforce runtime policy
- surface a warning in dashboard/health output
- audit `policy_doc_drift_detected`
- block releases or "10/10" readiness signoff until resolved

## Dashboard Requirements

The local admin dashboard must read effective policy from runtime state and the
policy registry, not from markdown.

Dashboard policy views should show:

- effective policy value
- source of authority
- whether locally configurable
- last changed by
- last changed at
- related docs and their drift status
- protected-doc edit history
- remote requests rejected by policy

Dashboard actions that broaden permissions require a local confirmation token.
Remote peers cannot trigger that token or provide it through bridge messages.

## MCP/API Surface

Proposed tools:

- `list_policy_rules(component=None, include_docs=False)`
- `policy_rule_status(policy_id)`
- `validate_policy_docs(paths=None)`
- `protected_doc_status(paths=None)`
- `propose_protected_doc_edit(path, patch, source_agent, source_message_id)`
- `approve_protected_doc_edit(proposal_id, local_confirmation_token)`
- `reject_remote_authority_request(message_id, reason)`

Implementation may keep some of these as CLI/dashboard internals if MCP exposure
is too broad, but the enforcement path must exist in code.

## Audit Events

Required audit actions:

- `policy_doc_drift_detected`
- `policy_doc_drift_resolved`
- `protected_doc_edit_proposed`
- `protected_doc_edit_approved`
- `protected_doc_edit_rejected`
- `remote_authority_request_rejected`
- `runtime_policy_changed`
- `runtime_policy_snapshot_generated`

Audit records should include policy id, source message id, path, authority
class, local confirmer, old/new hashes, and rejection reason where applicable.

## Tests

Required tests:

- Markdown claiming a forbidden action is allowed does not change enforcement.
- Generated policy sections fail validation after manual edits.
- Protected doc edits from a remote-origin message become proposals, not applied
  changes.
- Local-confirmed protected doc edits are allowed and audited.
- Remote request to extend its own contract is rejected.
- Remote request to reduce access is accepted and audited as peer-initiated.
- Dashboard/effective policy reads runtime registry values, not markdown text.
- Runtime policy changes without matching generated docs fail validation.
- Drift warnings do not block normal safe message receipt, only unsafe actions
  and readiness/signoff.

## Security Notes

This spec treats markdown as attacker-controlled until validated. That is
conservative, but appropriate: policy text is only useful if it cannot be used
as a privilege-escalation channel.

The local user can still choose to change policy. The important distinction is
that the user changes policy through a local authority path, not because a
remote peer or edited markdown told the local agent to obey.
