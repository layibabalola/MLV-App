# MLV-App provider-control disposition

Disposition: **DISTINGUISH - pending supervisor/action graph/CLOSED + SHADOW + CONTAINMENT proof**.

- Canonical R14 technical subject: `874605e43531c9aa230ee16851f8107a8e0d9cec`
- Ratification merge: `488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d`
- MLV-App base: `30889f77e2000190b94d59f80f6a03b12ce3e0d3`

The technical subject and ratification merge are intentionally separate fields. Neither is claimed
as an MLV-App ancestor. This candidate pins the ratified R14 contract as external doctrine data.
R16 features are unratified and may be considered only as `SHADOW_INPUT_ONLY` with zero authority.

## Verified locally

- Production `tick` is non-mutating and returns `ACTIVATION_EVIDENCE_BLOCKED`, `CLOSED`, zero provider
  calls/processes, and zero tokens for work and no-work demand.
- 1,000 unchanged production no-work ticks make zero provider calls/processes/tokens and create no
  state root, broker database, or quota lock.
- Test-only SHADOW and CONTAINMENT modes accept only the exact in-repository fake executable, retain
  the quota lock for its full lifetime, bind model/effort/role/subject/executable/argv, and leave the
  automatic gate CLOSED. Their receipts count fake calls separately and always report zero provider
  calls/processes/tokens.
- Test roots equal or canonically aliased to the production root are refused, as is any test-root
  path with a symlink/junction/reparse component. The refusal occurs before state or quota-lock work.
- Demand capsule, checkpoint, and cache-affinity bindings must each be a full, nonzero SHA-256.
- Fake receipts bind the actual resolved Python process image path/SHA-256 separately from the fake
  script path/SHA-256; the script is never mislabeled as the executable image.
- Missing fields, injected model/provider fields, contract drift, changed prompt/task/launcher/CLI
  hashes, alternate state roots, all-zero identities, null shared-broker identity, incomplete graph,
  and the observed direct launcher all block before a production child boundary.
- No production suspended-child/resume boundary, direct-provider command, canary command, adoption
  command, installer mutation, provider authentication, or provider invocation exists here.
- CI action inputs are pinned to exact commits. The complete `jsonschema` transitive dependency set
  is version-pinned and SHA-256 locked, and the workflow requires hashes with binary-only,
  noninteractive resolution and without upgrading pip. Checkout credentials are not persisted;
  concurrency is event-safe and non-cancelling; jobs have a bounded timeout. Lock evidence covers
  the intended Windows/Ubuntu Python 3.13/3.14 wheel matrix; local resolution verifies Windows only,
  so hosted execution is still unproven.

## Cross-checked from prior analysis

`mlv-observed-inventory.candidate.json` preserves the read-only Phase-0 observation: the
`MLV-LaneIgnitionWatchdog` task was Disabled (`Enabled=false`, `PT5M`, interactive/limited), its
physical task file had an exact full SHA-256, and it still targeted the ignored-state
`ignite-dead-lanes.ps1` launcher. Full path/length/SHA-256 observations are recorded for that
launcher, the four live prompts, `claude.cmd`, Claude Code 2.1.220, and its package manifest. These
are observations, never install or launch authority.

The prior exported-task XML hash is retained only as `UNKNOWN_BLOCKED`: current in-memory export
encodings did not reproduce its byte stream, so it is not accepted as an exact rollback identity.
The physical task-file bytes are the current rollback anchor.

## Needs runtime profiling or independent evidence

- A complete four-surface census and complete broker-owned action graph.
- Non-placeholder state-root and shared-broker identities.
- The separately reviewed production suspended-child observer/resume boundary.
- Exact CLOSED + SHADOW + CONTAINMENT evidence on the final committed tree and hosted matrix.
- Independent review, signed installation, and explicit one-use canary authority.
- A citable Fable sequence for the canonical c39 MLV specification. None is present in the
  reviewed project evidence, so c39 is not project authority.

Until all blockers are resolved and the fleet's pinned evidence proves the resulting exact tree,
this project must not publish ADOPT, enable/start the task, invoke a provider, or claim a canary.

Rollback invariant: leave `MLV-LaneIgnitionWatchdog` Disabled with `Enabled=false`, restore only the
exact recorded physical task-file bytes through a separately reviewed installer, and leave the
automatic launch gate CLOSED.
