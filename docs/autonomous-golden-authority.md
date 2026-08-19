# Autonomous Golden Authority

Status: candidate governance contract. It grants no runtime, provider, canary,
or automatic-launch authority. The machine-readable policy is
[`tools/repo_hygiene/autonomous-golden-authority.json`](../tools/repo_hygiene/autonomous-golden-authority.json).

## Outcome

Routine golden maintenance does not require the owner to approve each update.
The safe default is preservation: when evidence is incomplete, reviewers
disagree, or a change is aesthetically ambiguous without a standing policy, the
proposal is rejected and the known-good golden remains authoritative. The owner
receives a decision receipt and may appeal, but silence never means approval.

This delegation does not ask models to replace objective evidence. Hosted
baseline/candidate output comparison is mandatory and has an unconditional
veto. The hub and review lanes interpret the evidence and policy; they cannot
vote a failing output gate green.

## Separation of authority

- The implementation lane may propose a golden update but is recused from its
  approval and cannot write the accepted golden.
- A broker owns promotion and rollback. It accepts only content-addressed
  evidence and cannot build or modify product code.
- The known-good executable, toolchain, corpus, and manifest come from an
  immutable artifact store. The candidate lane cannot supply them.
- Signer identity, key, model family, and operator class must be distinct. One
  process or key cannot satisfy two signer classes.
- Reviews use commit-then-reveal, so one reviewer cannot copy or converge on an
  earlier verdict before committing its own digest.

## Decision classes

Class A covers representation-only changes with zero semantic output delta. It
requires unanimous hosted-oracle, stranger-review, and hub/doctrine receipts.

Class B covers output changes predicted completely by a pre-existing ratified
specification. It additionally requires two independently scheduled hosted
runs, real-output A/B at VALUES, PIXELS, and CADENCE altitudes, no unpredicted
changed key, and one shadow cycle with the old golden still authoritative.

Class C covers aesthetic, default, or ambiguous changes. It may proceed only
under a standing bounded policy and requires five unanimous seats: hosted
oracle, two stranger reviewers from different model families, hub/doctrine, and
an output specialist. Without that policy - or on abstention, timeout, or
disagreement - the broker rejects the proposal without waking the owner.

## Evidence capsule

Every proposal binds the candidate commit/tree; frozen known-good build and
manifest; exact corpus digests; old/proposed golden and actual-output digests;
per-key before/after values; VALUES/PIXELS/CADENCE results; test inventory,
failures, and skips; toolchain/environment; ratified prediction; signer
receipts; and the promotion-policy digest.

Deterministic values must reproduce in at least two independently scheduled
hosted runs. Any missing clip, new skip, dirty tree, baseline mismatch, unknown
delta, signer overlap, signer disagreement, or unexplained variance is a veto.

## Anti-laundering transaction

A proposal cannot change product code, golden, verifier, or promotion policy in
one authority transaction. Verifier or policy changes land first and complete a
separate review cycle before judging a golden proposal.

Promotion is two phase:

1. The broker writes a PREPARED receipt after exact-head evidence and quorum.
2. A shadow cycle runs while the old golden remains authoritative.
3. The broker alone writes an atomic commit containing only the golden and its
   append-only promotion receipt.
4. All checks rerun on that exact promotion head.
5. Any post-promotion mismatch triggers automatic rollback and quarantines the
   proposal class/policy tuple.

Approval carries across a later commit only when a verifier proves every
authority-sensitive blob is byte-identical and the intervening diff is an
allowlisted evidence/test-only change.

## Owner experience

Routine Class A/B decisions target completion within 30 minutes after hosted
evidence is available. Class C under a standing policy has a two-hour quorum
window; timeout means rejection. Notifications contain the candidate/head,
class, changed keys, artifact digests, signer verdicts, and promotion or rollback
commit plus an appeal link. They are receipts, not approval requests.

Until a separately reviewed broker, signer-key separation, immutable artifact
store, and shadow/promotion machinery are installed, this policy remains a
zero-authority design contract. It does not enable Claude or any provider lane.
