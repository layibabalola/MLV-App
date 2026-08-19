# MLV-App provider-control disposition

Disposition: **DISTINGUISH - pending installed choke point/complete action graph/CLOSED + SHADOW + CONTAINMENT proof**.

- Canonical universal token-saving subject: `e70a044f31dd2f43ab7c716d63a4eb89318c61b6`
- Canonical doctrine merge: `909f769d02e8412e51e28e242cfa8d00dadc9a3d`
- Exact R14 mechanics dependency: `874605e43531c9aa230ee16851f8107a8e0d9cec`
- R14 mechanics ratification merge: `488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d`
- MLV-App base: `30889f77e2000190b94d59f80f6a03b12ce3e0d3`

The canonical subject and merge are intentionally separate fields. Neither is claimed as an MLV-App
ancestor. R26 is the canonical zero-authority token-saving doctrine; the older R14 subject remains
bound only where the vendored engine, schemas, and lane bindings depend on its exact mechanics.
Neither reference grants MLV-App provider authority. R16 features remain `SHADOW_INPUT_ONLY`.

## Verified locally

- Production `tick` is non-mutating and returns `ACTIVATION_EVIDENCE_BLOCKED`, `CLOSED`, zero provider
  calls/processes, and zero tokens for work and no-work demand.
- 1,000 unchanged production no-work ticks make zero provider calls/processes/tokens and create no
  state root, broker database, or quota lock.
- Test-only SHADOW and CONTAINMENT modes accept only the exact in-repository fake executable, retain
  the quota lock for its full lifetime, bind model/effort/role/subject/executable/argv, and leave the
  automatic gate CLOSED. Their receipts count fake calls separately and always report zero provider
  calls/processes/tokens.
- The test-only reference slot serializes a fixed `fable`, `claude-review`, `opus`, `sonnet-impl`
  rotation. A no-work demand returns before fake identity, reservation, quota lock, or process
  creation. Each attempt charges fresh input, full cache read, and full cache create while preserving
  the larger of the 20 percent completion reserve and the named priority floor. A failed attempt is
  charged and reconciled before a retry can be admitted. The broker constructs the exact process
  image, script, subject, and argv, compares the full argv and digest on every reload, and permits one
  retry at most. It launches a private content-addressed fake artifact rather than reopening the
  observed source path. Rotation publishes the next cursor before removing active/reservation replay
  fences, so cursor-publication failure leaves restart blocked instead of replaying a generation.
- Those fake-only checks are reference mechanics, not an installed choke point and not a claim that
  CLOSED + SHADOW + CONTAINMENT proof has passed. Production commands cannot reach the reference
  slot, and the caller-supplied fake path is used only as an exact witness.
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
  concurrency is event-safe and non-cancelling; `queue: max` prevents the default single-pending-run
  replacement behavior; jobs have a bounded timeout. Lock evidence covers the intended
  Windows/Ubuntu Python 3.13/3.14 wheel matrix; local resolution verifies Windows only. The R10 tree
  passed all four hosted legs in run `32208720831`. The R11 technical head `9e3ee4d8` was not run;
  the distinct R11 evidence/closeout head `152f1e5f` passed all four hosted legs in run
  `32228841343`. R12 and R13 exact-tree hosted execution is unproven.
- The project profile is version 2 and raises the owner-foreground reserve floor to 20 percent. The
  canonical attended rotation and numeric token policy are pinned by exact external Git blob and
  SHA-256 identities. They are `MOTIVATION_ONLY`: author-attested, not provider-authenticated, not
  independently observed, and incapable of satisfying installation, SHADOW, CONTAINMENT, canary,
  adoption, or activation evidence.

## Cross-checked from prior analysis

`mlv-observed-inventory.candidate.json` v2 preserves the read-only Phase-0 observation: the
`MLV-LaneIgnitionWatchdog` task was Disabled (`Enabled=false`, `PT5M`, interactive/limited), its
physical task file had an exact full SHA-256, and it still targeted the ignored-state
`ignite-dead-lanes.ps1` launcher. Full path/length/SHA-256 observations are recorded for that
launcher, the four live prompts, `claude.cmd`, Claude Code 2.1.220, and its package manifest. These
are observations, never install or launch authority.

The MLV exported-task XML is now an exact observed identity under one explicit convention:
`Export-ScheduledTask` string bytes encoded as UTF-8 without BOM while preserving CRLF. Two
consecutive exports were byte-identical at 1,894 bytes and SHA-256
`d4f5e9dbcfe86555a244aa3e4a0a6bbf220dc132b6887790062963d19c250af3`. This repairs only the XML
canonicalization observation; the physical task-file bytes remain the rollback anchor and the
inventory/action graph remain incomplete.

The task action has a separate canonical receipt: an insertion-ordered compact JSON object with
fields `Execute`, `Arguments`, and `WorkingDirectory`, encoded as UTF-8 without BOM or trailing
newline. The observed null working directory receipt is 257 bytes with SHA-256
`5fb03548103724f4827acdb0ef3e9e9ae6d022471354078b0badaf4a252ab087`. This binds the observed action
only; it is not installation or launch authority.

Inventory v2 also binds the active MLV Codex automation and prompt, four paused definitions,
related disabled tasks, the enabled diagnostic-only Claude shadow task, and both Agent Bridge MCP
source chains. Codex loads the external `agent-bridge` repository while Claude loads the MLV-App
copy; both point at the same mutable `.agent-bridge` state root. That is divergent source identity,
not a unified or certified choke point. The watcher configuration retains two retired MLV Codex
direct-wake entries as dormant evidence, `wake_claude.ps1` remains diagnostic/fail-closed, and no
watcher process was observed in the dynamic snapshot.

The services and startup-folder scans found no matching provider launcher. The startup census
inspected all five regular files across the user and common Startup folders, of which three were
`.lnk` launchable shortcuts; exact file identities and resolved shortcut targets are recorded, and
none matched the explicit Claude/Codex/Agent Bridge/MLV filter. The HKCU `Claude` Run
entry is explicitly classified as attended Claude Desktop startup, not a headless Claude CLI
provider route. Those absences, paused definitions, task state, process counts, and shared state are
snapshot evidence only. The inventory explicitly forbids inferring historical/future absence,
permanent inertness, source-chain equivalence, production authority, or graph completeness from
them; mutable, dynamic, unreadable, and unbound surfaces remain `BLOCKED`.

## Needs runtime profiling or independent evidence

- A complete, independently reproduced surface census and complete broker-owned action graph.
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
