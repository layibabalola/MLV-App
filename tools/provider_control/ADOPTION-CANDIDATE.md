# MLV-App universal provider-control adoption candidate

Disposition: **ADOPT_CANDIDATE R2 only - zero runtime/adoption authority**.

Exact doctrine subject: 488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d.
Exact MLV-App base: 30889f77e2000190b94d59f80f6a03b12ce3e0d3.

This candidate contributes MLV-App's project profile and reconciles its legacy watchdog with the
fleet's ratified R14 contract. It does not claim ADOPT, does not install, and does not enable a
lane. The current task remains disabled and still targets the legacy ignored-state launcher; the
current census therefore blocks adoption.

## Proposed sole boundary

mlv_lane_supervisor.py is the only proposed repository launch boundary. It vendors the exact
canonical doctrine engine and all twelve runtime schemas by Git blob identity. Production
work-bearing ticks may return only CLOSED/refused: this author intentionally did not implement or
exercise the separately reviewed suspended-child resume seam. The only child-launch code is
guarded by MLV_PROVIDER_CONTROL_TEST_ONLY=1 and accepts only the byte-identified in-repo fake
provider.

The proposed profile sets one host-local shared quota domain, concurrency one, max 12 turns,
120,000 context tokens, mandatory capsule/checkpoint/cache bindings, milestone compaction,
15-55% priority reserves, 15-minute post-reset quiet, and the exact universal capacity dimensions.
Efficiency controls cannot weaken model, effort, role, frozen subject, review, product, release,
or hardware gates.

Four frozen seat prompts are exact byte copies of the live ignored-state subjects at authoring
time. lane-bindings.candidate.json pins model, effort, role, subject, adapter, quota domain,
doctrine commit, and doctrine engine blob. Any missing/unknown field, malformed JSON, oversized
turn/context request, capacity reserve deficit, or changed binding refuses before child creation.

## R2 signed-review repairs

- One canonical production state root is bound into lane-bindings and checked with the exact
  profile digest and duplicated state-root identity before any quota-lock path. The fake seam has
  one separately named test root; an alternate root is refused before a lock file exists.
- Idle state now persists a typed CANONICAL_DEMAND_V1 / NO_WORK fingerprint. A first observation is
  IDLE_RECORDED, a changed no-work observation is IDLE_CHANGED, and only an exact unchanged prior is
  IDLE_SKIPPED. The following 1,000 unchanged ticks still make zero provider calls/processes/tokens.
- Profile and lane-binding bytes have compile-time SHA-256 pins. Every subject has an exact digest.
  Bindings and subject bytes are reloaded and revalidated while holding the quota lock immediately
  before the fake child boundary; redirected model, role, path, or subject bytes refuse.
- Exact R1 97f64b161f4015eb579ad731e9cdf41dc7c951e7 is preserved as the red control:
  it acquires two same-domain locks through two roots and classifies first/changed no-work as
  IDLE_SKIPPED. R2 rejects the alternate root and distinguishes first/changed/unchanged.

## Proved locally

- 1,000 deterministic no-work ticks: IDLE_SKIPPED, zero calls/processes/tokens, no broker DB,
  no quota-lock acquisition.
- Missing state and work-bearing ticks: CLOSED/refused before provider resolution.
- Auth success, reset observation, capacity return, and quota refusal: signal-only; gate stays
  CLOSED.
- Fake-only capacity refusal occurs before Popen.
- Two simultaneous fake requests in one quota domain: the first holds the OS lock for the full
  child lifetime; the second receives QUOTA_DOMAIN_BUSY.
- Fake receipt binds model, effort, role, subject digest, executable digest, and canonical argv
  digest.
- The audit-only installer reports the current disabled/direct task. Apply mode deterministically
  refuses INSTALL_NOT_IN_AUTHOR_SCOPE.

## Three-part DISTINGUISH and proof

The honest project disposition remains:

1. PENDING_PRODUCTION_SUSPENDED_RESUME_BOUNDARY. Proof: production work returns CLOSED/refused;
   any non-CLOSED gate returns PRODUCTION_RESUME_BOUNDARY_NOT_ADJUDICATED. Only the byte-identified
   fake provider can cross a child boundary, under an explicit test-only environment.
2. PENDING_SIGNED_INSTALL_AND_COMPLETE_CENSUS. Proof: the candidate profile's state-root HMAC and
   intended inventory's deployment hashes remain zero placeholders; the current exact census still
   shows the Disabled machine task targeting the ignored-state direct/fail-toward launcher.
3. PENDING_EXPLICIT_ONE_USE_CANARY_AUTHORITY. Proof: there is no installation receipt, final-profile
   review receipt, manual canary authorization, provider/auth invocation, or canary execution.

Therefore the exact current token is:

DISTINGUISH(488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d,
PENDING_PRODUCTION_SUSPENDED_RESUME_BOUNDARY;
PENDING_SIGNED_INSTALL_AND_COMPLETE_CENSUS;
PENDING_EXPLICIT_ONE_USE_CANARY_AUTHORITY)

## Required before exact ADOPT

1. Independent exact-commit mechanics and safety reviews must both pass.
2. Hosted Windows/Ubuntu x Python 3.13/3.14 jobs must pass on the exact candidate.
3. An installation owner must replace all zero placeholders with fresh HMAC-bound state-root,
   census, executable, and discovery identities; regenerate and schema-check the final profile and
   inventory; then obtain fresh review because the profile hash changes.
4. The machine task must be rewritten to the sole tracked wrapper but remain Disabled. The legacy
   ignored-state direct launcher must be retired with byte-preserving rollback evidence.
5. A current four-surface census must show no unknown, extra, unhashed, or direct provider path.
6. The separately reviewed production suspended-child observer/resume boundary must be present and
   pass the canonical fake-provider hostile suite. This author's CLOSED-only seam is not a canary.
7. Publish ADOPT(canonical commit, final profile hash, review receipt) only after those exact
   receipts exist. A canary remains a distinct, one-use authorization and is out of scope.

Rollback remains: disable MLV-LaneIgnitionWatchdog, restore the pre-install task XML whose
UTF-16 SHA-256 is recorded in CURRENT-SAFETY-EVIDENCE.json, prove Disabled on both state fields,
and leave the universal gate CLOSED.
