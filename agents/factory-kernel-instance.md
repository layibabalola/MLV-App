# Fleet factory kernel: MLV-App instance map

MLV-App runs `specs/fleet-factory-kernel.md` on the fleet doctrine bus as **DOGFOOD, not ADOPT** (bus
`bootstrap/PROMPT-K-dogfood-kernel.md`). **Kernel `CANDIDATE r5`; profile `code` revision `r9`** (re-filed
2026-09-20 against the bus; this map had read `Kernel r1 / code@r1` since 2026-09-14, four kernel and eight
profile revisions behind). Profile `code` primary, with a proposed `measured-objective` sub-instance for
render/export parity and playback measurement. Since 2026-09-14.

**Re-file debt is itself an observable, and it went unpaid for six days.** The rule at the foot of this file
says re-file weekly while the kernel is a CANDIDATE and whenever a revision changes; between 2026-09-14 and
2026-09-20 the kernel moved r1 -> r5 and the profile r1 -> r9 and this file was not touched once. A map that
silently describes an older kernel is worse than an absent one, because a sibling reads it as current.

Doctrine is data: the binding copy of any rule is this repository's own mechanism below, never the bus text.

A `NONE` is a finding filed on the bus, not a gap to paper over here. Paths under `.claude-state/` are board-local
(gitignored): they exist on the board host only, and no sibling project can resolve them.

| Clause | MLV-App mechanism | Observable | Gap |
|---|---|---|---|
| K1 roles separate | `agents/orchestration-tiering.md` (procedure); lane table in `tools/coordination/Invoke-Lane.ps1`; `tools/coordination/record_workstream_completion.py` requires a separate verdict receipt bound to the subject sha and output digest | producer lane receipt and verifier verdict receipt | NOT ENFORCED: the completion recorder binds verdict, sha and output digest but no producer or reviewer identity, so one actor can supply both receipts; receipts are board-local |
| K2 authority register | NONE. `docs/never-authorized.json`, enforced fail-closed by `tools/hooks/mlv-never-authorized.py`, is a list of acts no actor may perform, not a register of what needs the owner | the prohibition list's path | NONE: no register of owner-reserved decisions; acts outside the list stay governed by the task, the owner's standing orders and repository gates, not by the list |
| K3 identity + claimant | `record_workstream_completion.py` binds commit sha plus per-file digests | subject sha and digest registry | no claims/leases; no pre-commit identity; no eval-set or scorer digest for the measured-objective lane |
| K4 positive evidence | `Invoke-Lane.ps1` receipt from `finally`; `tools/coordination/lane-provider-refusal.ps1` | receipt `exitCode`, `providerRefusal`, prompt/output digests | **receipt `complete:true` is written for a run with exit 1 and `max_turns`** (2026-09-14, card PLAY-COUNTERS-CPU); dispatches that never launch leave no receipt |
| K5 declared profile, typed terminals | `.claude-state/kernel/subject-ledger.md` (subject declared BEFORE first byte); `tools/gates/output_budget.py`, GPU parity build/run scripts, `docs/14-performance-benchmarking.md` A/A matrices | the ledger entry's tool-written stamp vs the lane receipt `startedUtc` and the commit author date | **PARTIAL, and measured 2026-09-20: of three code-producing subjects, ONE passes cleanly (S4/#140), ONE holds with a disclosed qualification (S3/#139, ~9 h of imported content predates its declaration), and ONE FAILS UNCORRECTED (S2/#137: lane started 18:09:21Z and the commit was authored 18:19:01Z against a declaration composed at 18:25Z -- the first byte precedes the declaration by 6-16 min).** The ledger self-diagnosed the cause as KF-11: a declaration field must be COPIED from a tool-written stamp, never composed by hand. S4 obeys it; S2 predates it and was never re-checked. Thermal degradation is still not a typed terminal. |
| K6 independent key | Procedure: Claude produces and Sol (Codex) reviews (`agents/orchestration-tiering.md`, which also permits same-family review outside protected changes); GitHub required checks are a CI key the producer does not control | **the `engine` field of each key's RUN RECEIPT, never the `reviewer` name in the verdict body (KF-13)** | **NOT ENFORCED, and violated in the measured record: PR #134 rounds r4 and r5 turned `astra` + `sol`, which are BOTH `engine: codex` -- two keys, one family.** PR #133 merged after EIGHT consecutive single-key rounds; #118, #119 and #114 also merged on a single key. Continuous cross-family pairing only begins at #134 r6 and #140 r1, so the standard this row describes is about two days old. No tool binds a reviewer's provider family to a completion. |
| K7 delivery | `tools/repo_hygiene/brokered_closeout.py` (repo-closed postcondition), target `fork/master` | closeout blocks the final response until the repo is closed | NONE: no typed `CLOSURE_INCOMPLETE` state for accepted-but-undelivered work |
| K8 capacity | `lane-provider-refusal.ps1`; `docs/ROTATION.md` (rotation is an owner act) | last quota event and what in-flight work did | no automatic park/rotate |
| K9 resume | `.claude-state/RESUME.md`, OS task `MLV-BoardStateHeartbeat`, `tools/session-checkpoint.py` | heartbeat `-Status` and a fresh snapshot | resume state is board-local; survives account rotation, not machine loss |
| K10 inventory + parity | `~/.claude/machine-inventory.yaml`; `~/.claude/hooks/check-account-drift.ps1` at SessionStart | `probed_under`, parity verdict | machine-local; a probe can be derived and still wrong (PATH fault read as unavailability) |
| K11 honest reports | `CLAUDE.md` behavioural rule 1; `agents/error-remediation.md` | quoted rule lines | NONE: no tracked tool emits an R9 `posture:` line in this repo |
| K12 feedback | **LANDED, and still scoring zero by the bus's own criterion.** ~18 MLV-authored sections across bus `TRAPS.md`, `RECEIPTS.md` and `RULINGS.md` since 2026-08-09; newest 2026-09-18 (KF-13 reviewer-name-is-not-key-identity, KF-15 untyped provider refusal, KF-16 same-class stop rule) | the bus harvest row, not the filing count: `adjudications/factory-kernel/HARVESTS.md` | **The only MLV harvest row is dated 2026-09-15 and reads `3 FIT / 0 FRICTION / 0 BREAK / 13 UNEXERCISED` with ZERO subjects closed end-to-end. S3/#139 reached DELIVERED on 2026-09-18 and HAS NOT BEEN FILED. `KERNEL-BUS-FILING-MLV-2` has sat `queued` since 2026-09-18.** The bus criterion is explicit that a filing with `subjects: 0` is commentary, not progress -- so 18 sections of findings have bought no kernel credit. The bus heartbeat for this project currently reads `FOLD_PENDING`. |

Re-file weekly while the kernel is a CANDIDATE, and whenever the kernel or profile revision changes (PROMPT-K §6).
