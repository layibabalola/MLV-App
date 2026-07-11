# MLV App implementation roadmap

Status snapshot: 2026-07-11. This is the only document that selects the next
mutating work item. Older plans remain useful as design history and evidence,
but they do not set priority or authorize implementation.

## Product outcome

Ship a stable MLV editor whose GUI playback, batch/headless export, DNG output,
and optional GPU acceleration agree on color, exposure, geometry, and receipt
semantics. Prefer a small end-to-end user capability over another layer of
scaffolding. Fail closed to a correct CPU path when an accelerated path is not
proven.

## Read this first

1. `AGENTS.md` and `CLAUDE.md` for safety, proof, and closeout rules.
2. This file, starting at **Current control state**.
3. Only the detail/evidence documents named by the selected item.
4. The live diff and tests around the named code. Do not infer current behavior
   from an older narrative.

Machine-local `.claude-state/` material may contain newer measurements and
handoffs, but it cannot silently change queue order. Reconcile a useful new
finding into this file in the same work block that acts on it.

## Current control state

- Mutating implementer: **Codex**.
- Formal reviewer: **Claude**.
- Specialist reviewer: **Opus only when an item explicitly names it**, notably
  Q7/ABI and GPU-kernel proof.
- Fable: no active implementation or review assignment.
- Maximum mutating WIP: **one work block**.
- Current item: **P0-A — ACTIVE**.
- R1 is complete: `master`/`fork/master` are at
  `dcf830c459e55cb98d31aa3a5c75bca8cddf5ded`, and
  `.claude-state\closeout\repo-closed\repo.json` reports `repoClosed: true`.
- Do not start another queued item while P0-A is active.

No prompt is needed to bootstrap every historical lane. Bootstrap the Codex
implementation loop once; send Claude one exact review request per committed
range; involve a specialist or hardware host only at the item that needs it.

## Status vocabulary

| State | Meaning |
|---|---|
| `QUEUED` | Ordered work, not authorized while an earlier item is active. |
| `ACTIVE` | The sole mutating work block. There must be at most one. |
| `REVIEW` | Committed candidate is frozen pending the named reviewer. |
| `EXTERNAL_GATE` | No useful local mutation remains; the item names the missing access, hardware, footage, or human decision and a recovery action. |
| `DONE` | Required implementation, proof, review, integration, and push are complete, or the row explicitly says the completed proof lives on a line awaiting R1 integration. |
| `DROPPED` | Evidence showed the item is wrong, redundant, or not worth its cost; record why and preserve the evidence pointer. |

Changing priority requires a user directive or a newly proven dependency. When
that happens, update this file explicitly; do not leave contradictory active
queues in detail documents.

## Queue

| Order | ID | State | Outcome | Depends on | Completion gate |
|---:|---|---|---|---|---|
| 0 | R0 | DONE | Canonical roadmap plus Codex-handoff/Claude-review closeout contract | none | Claude approves exact range; target contains it; remote is verified |
| 1 | R1 | DONE | Target branch contains every approved, still-required line without importing unreviewed WIP | R0 | ancestry matrix, conflict review, focused tests, Claude approval, push proof |
| 2 | P0-A | ACTIVE | Clip switch/close cannot free an MLV object while render work can still touch it | R1 | deterministic lifecycle test, stop/switch stress, no hang/UAF, GUI A/B |
| 3 | P0-B | QUEUED | CineForm export dimensions are always valid multiples of 16 | P0-A | table-driven helper tests plus odd/anamorphic export smoke |
| 4 | P0-C | QUEUED | Rendered export launches ffmpeg without shell parsing or fragile quoting | P0-B | zero production `popen`, argument/space/quote tests, cancel/error/two-pass smoke |
| 5 | P0-D | QUEUED | Dual-ISO dither is deterministic for a frame regardless of scheduling | P0-C | repeat/thread/path determinism tests plus real-output baseline A/B |
| 6 | P0-E | QUEUED | Histogram accumulation cannot wrap above 65,535 samples per bin | P0-D | synthetic overflow test, parity corpus, bounded performance result |
| 7 | CI-1 | QUEUED | Required CI gives trustworthy red/green signals, including GUI smoke | P0-E | restore/correct missing promotion record, independent GUI job, two hosted greens, then blocking status |
| 8 | E4-1 | QUEUED | Headless batch takes one MLV clip and emits one playable H.264 MP4 | CI-1, P0-C | end-to-end CLI test, receipt/aspect parity, cancellation/error proof, real playable output |
| 9 | P1-A | QUEUED | GPU ABI v2 wording and extension negotiation are unambiguous before new kernels | E4-1 | header/loader/tests agree; unknown extensions fail safely; Claude + Opus design review |
| 10 | P1-B | QUEUED | F1b/processing statistics expose stable, parser-friendly timing and fallback reasons | P1-A | schema/version tests, disabled-path silence, no hot-path regression |
| 11 | Q7 | QUEUED | Highest-value remaining GPU stage is implemented behind a capability gate | P1-B | CPU oracle parity, fallback proof, 4090 proof, cross-path transfer report |
| 12 | LA-1 | QUEUED | Look Assist cold-open/default behavior is predictable and output-regression guarded | Q7 | fixed-point test, receipt legs, known-good output A/B, user-visible state proof |
| 13 | DELL-1 | QUEUED | Dell RTX 3060 hybrid-GPU support is proven locally, not inferred from the 4090 | LA-1 | architecture sidecar, CUDA/GL adapter proof, no-readback/fallback metrics, DNG hash pass |
| 14 | E3-H | QUEUED | Trusted GPU CDNG export is hardened or deliberately remains diagnostic-only | DELL-1 | byte parity, lossless regression decision, explicit promotion/non-promotion record |
| 15 | E4-2 | QUEUED | Rendered batch export grows from the walking skeleton to receipts, queues, progress, and supported codecs | E4-1, E3-H | matrix tests, bounded resources, restart/cancel/resume semantics, CLI compatibility |
| 16 | PLAYER-1 | QUEUED | Player Mode opens complete spanned MLV sets and behaves as a focused player | E4-2 | `.MLV` + `.M00...` ordering tests, receipt/aspect parity, GUI smoke |
| 17 | TRACK-P | QUEUED | Approved queue-wait/H2D/present optimizations are integrated without output or cadence regression | PLAYER-1 | approved-range audit, known-good A/B, queue/cadence metrics, cross-path transfer report |
| 18 | PORT-1 | QUEUED | Portable GPU backend and upstream-ready patch series replace machine-specific assumptions | TRACK-P | backend capability matrix, CPU fallback, Windows/Linux/macOS build proof, clean patch stack |

### Closed proof anchors that R1 must preserve

- F3 invalid-frame-index repair: `8e96d757de8cff32b95df476f72e030978774266`
  on `build/provenance-p0`; hub proof and push are complete. Do not reopen the
  old invalid-index theory. The true clip-lifecycle hazard is P0-A.
- F1 visible-scope policy train through
  `ef5ce27ecf23ca55dd517f1de4d86d81e84a23ef`; closed and pushed on
  `build/provenance-p0`.
- Nyquist scratch correction: `140a740ec9c063088bcef8606d59438826693d57`;
  proof is closed on the Track-P line, but target integration belongs to R1.
- Current target snapshot when this roadmap was written:
  `master`/`fork/master` at `b8fb9b0dc9dcd5960656a318429fcc3ae2658b9f`.
  Treat these hashes as reconciliation inputs, not permanent branch pins.

## Exact implementation cards

### R0 — consolidate control and enforce review roles

Create this file, point older roadmap/optimization documents here, and align
`closeout.config.json`, `DEFAULT_CLOSEOUT_CONFIG`, runtime fallbacks, baseline
symbols, and tests to `CODEX - HANDOFF` followed by `CLAUDE - REVIEW`. Do not
build a YAML schema, roadmap validator, dashboard, or new orchestration layer.

### R1 — reconcile approved lines before new feature work

Start from a freshly fetched, clean target worktree. Build a table for
`master`, `build/provenance-p0`, and `codex/f1-visible-scope-policy` containing
head SHA, merge base, commits unique to each line, ledger review verdict, and
whether each commit is still required. Preserve all three closed anchors above.
Exclude every commit lacking a decisive review or still described as a probe,
diagnostic, experiment, rejected promotion, or active WIP. Present the proposed
merge/cherry-pick order to Claude before mutation when conflict resolution could
change behavior. After integration, run the union of the focused gates for the
accepted ranges and verify the target remote. Never merge the whole Track-P
branch merely because a later approved commit depends on it.

### P0-A — close the real clip-lifecycle race

Use `.claude-state/crash-analysis/f3-cache-drain/f3-invalid-frame-fix-handoff.md`
and `.claude-state/project-memory/f3-invalid-index-round-20260706.md` when they
exist. They prove F3 was an invalid index and explicitly leave clip-switch/
render-free quiescence open. Trace ownership across `MainWindow`,
`RenderFrameThread`, frame slots, worker/prefetch tasks, and `freeMlvObject`.
Define one lifecycle barrier: stop accepting requests, cancel/wake pending work,
wait until every borrower has released the generation, then free it. Do not
paper over the race with sleeps or a low-level null check. Add a deterministic
test seam that blocks a render borrower while switch/close begins, proves free
waits, then releases it and proves completion. Stress repeated open/play/switch/
close and preserve playback cadence. Because this changes GUI/playback
lifecycle, rebuild the user-facing release and run the named real-clip A/B gate.

### P0-B — correct dimension alignment once

Extract a small overflow-safe `roundUpToMultiple(value, multiple)` helper and
use it for CineForm width/height alignment. The current `width += width % 16`
pattern is wrong for most remainders; do not copy it. Test exact multiples,
every remainder 1–15, zero/invalid inputs, and representative stretched widths
such as 1921, 1922, 1928, and 1935. Keep the H.264 even-dimension behavior
explicit. Export an odd/anamorphic case and verify encoded dimensions, playable
output, and `getMlvAspectRatio`-derived display geometry.

### P0-C — replace shell-built ffmpeg execution

Inventory every production `popen`/`_popen` site and classify single-process,
two-pass, and actual pipe behavior. Introduce a narrow process runner using
`QProcess` program + argument lists, separate stdout/stderr capture, timeout,
cancellation, exit-status normalization, and bounded diagnostic tails. Model a
pipeline as two explicit processes or remove it if an equivalent single ffmpeg
invocation exists; never pass a user path through a command shell. Unit-test
spaces, apostrophes, quotes, ampersands, Unicode, missing executables, nonzero
exit, cancellation, and cleanup of partial files. Preserve progress reporting
and prove a real single-pass and stabilization/two-pass export.

### P0-D — make Dual-ISO dither reproducible

Replace the process-wide `fast_randn05()` cursor with a deterministic generator
whose inputs include stable frame identity and pixel/sample coordinates. Specify
the sequence and seed layout in code so scalar, AVX2, CPU export, and GPU oracle
paths cannot accidentally choose different identities. Add tests for repeated
runs, thread counts, task order, and supported path equivalence; tighten the old
50%-of-pixels/delta-64 tolerance only as far as the intended arithmetic permits.
This changes pixels: require known-good-build A/B on named clips and do not
re-bless a golden merely to make the candidate green. Record whether GPU, CPU,
and CDNG paths transfer or intentionally differ.

### P0-E — eliminate histogram counter wrap without losing throughput

Add a synthetic uniform frame that drives one bin above 65,535 and fails the
old counter. Use 32-bit totals. If 16-bit thread-local partials are retained for
speed, reduce/clear them in chunks before any partial can overflow; merely
summing already-wrapped 16-bit partials is not a fix. Test exact totals around
65,535/65,536 and large-frame parity. Benchmark the existing representative
Dual-ISO workload and report milliseconds and FPS-equivalent. Prefer the
simplest safe design unless the measured regression justifies chunked partials.

### CI-1 — make GUI CI evidence trustworthy

The workflow references `.claude/analysis/gui-tests-ci-remediation.md`, which
was absent in the 2026-07-09 audit. First determine whether it was deleted,
renamed, or never committed; restore the intended record by editing an existing
tracked analysis document or correct the workflow reference. Put the pilot in
an independent job so unrelated required suites remain blocking. Inspect hosted
runs and collect two consecutive green executions before removing
`continue-on-error`. If GitHub Actions access is unavailable, set
`EXTERNAL_GATE` with the exact access/install command and continue to E4-1 only
if local CI-equivalent proof is green and the user accepts the temporary gate.

Repository-closeout debt parked for CI-1 (full sweep at
`c6ea6a1e43451273f41cedf59428f3d193d6bb3d`, 2026-07-10): the completed sweep
enumerated 33 retained reports and 40 actionable candidates. Safe classes remain
broker-owned and are not parked: 17 `preserve_detached_dirty_now`, 3
`prune_now`, 1 `clean_integrate_now`, and 19 clean-detached removals. The exact
unsafe classes below are parked by explicit branch/worktree protection; parking
does not approve, merge, reset, clean, or delete their bytes.

- `foreign_dirty_retained` (3): `codex/w10c-agent-reliability` at
  `C:/mlvtmp/codex-w10c-agent-reliability`, `codex/ui-signal-gap-instrument` at
  `C:/mlvtmp/mlvapp-5aa7cebe-codex-ui-signal`, and
  `codex/w10-agent-heartbeat-hardening` at
  `C:/mlvtmp/codex-w10-agent-heartbeat-hardening`.
- `foreign_dirty_target_overlap` (3): `codex/cuda-chroma-x1` at
  `C:/mlvtmp/mlvapp-f28dbca7-cuda-chroma-codex`,
  `codex/optimus-m2-x1` at `C:/mlvtmp/mlvapp-optimus-m2-codex`, and
  `codex/laon-sh-no-readback` at
  `C:/mlvtmp/mlvapp-f28dbca7-laon-nr-codex`.
- `unowned_dirty` (1): `build/provenance-p0` in the main
  `C:/!Layi Wkspc/MLV-App` checkout.
- `too_many_dirty_paths` (5 detached build-heavy worktrees):
  `C:/mlvtmp/opus-review-bugA`, `C:/mlvtmp/wt-bisect`,
  `C:/mlvtmp/wt-precuda`, `C:/mlvtmp/wt-baseline`, and
  `C:/mlvtmp/wt-good-765ed4a3`.

Exit from parking requires the owning lane to preserve or commit its exact dirty
paths, prove target-overlap resolution where present, and then remove the
corresponding exact protection in a reviewed change. The recovery route is to
run `repo-sweep-closeout.ps1`, inspect that candidate's latest report, and use
`remediate-retained-closeout.ps1 -Apply` only after the owner has made its bytes
eligible. Do not bulk-clean these surfaces or infer ownership from this card.

Content-review parking extension (2026-07-10): `claude/export-hardening` at
`84ed31860808d2bbf8f23e7b75507be405298c67` and its worktree
`C:/mlvtmp/mlvapp-export-hardening-claude` are also explicitly protected. A
clean merge probe is not content approval: this branch contains four real
product commits across batch, DNG, and CLI export surfaces. Do not integrate it
as repository hygiene. After P0-A, queue it as a normal reviewed work block
alongside or immediately before P0-B/P0-C, with focused batch/DNG tests, a
release rebuild and fingerprint, a CODEX handoff, and a Claude review. Its
export work also informs E4-1; protection is removed only by that reviewed
integration or an explicit reviewed rejection.

Live-session protection extension (2026-07-10): Fable SEQ 479/480 identified
`claude/sleepy-brahmagupta-3996c3` and
`C:/!Layi Wkspc/MLV-App/.claude/worktrees/sleepy-brahmagupta-3996c3` as the
live Fable hub #13 Claude session seat, not stale cleanup debris. Candidate
`e9a40b0802eb2dff` is retained as live-session-protected: do not delete its
branch, remove its worktree, or treat a restore as new dirt. The broad
`.claude/worktrees/**` root remains inspect-only; the exact branch/path
protection prevents branch-oriented cleanup from bypassing that policy.

### E4-1 — ship the rendered-export walking skeleton

Delete no contract types merely for aesthetic cleanup, but add no new framework
until one clip reaches one MP4. Reuse the working GUI decode/process/render
semantics behind a narrow headless adapter; do not fork receipt, aspect, or
color logic. Accept one input clip, one output path, and H.264 first. Emit clear
progress and machine-readable failure, support cancellation, write atomically,
and refuse unsupported modes. The blocking proof opens a real MLV, applies its
receipt, exports a playable MP4, checks frame count/duration/dimensions/aspect,
and compares representative frames with the GUI/reference path. As touched,
split `src/batch/BatchTypes.h` and
`tests/console/test_receipt_applier.cpp` by domain rather than extending their
existing monoliths.

### Later cards — activation rule

Before activating P1-A or later, turn its queue row into a card at the same
specificity as P0-A–E4-1: exact source seam, baseline failure, smallest slice,
tests, real-output proof, performance budget, rollback, and external-gate
recovery. That refinement is part of the preceding item's closeout, not a
separate planning project.

## Continuous-drain protocol

For every item:

1. Refresh remotes and inspect branch, tracking, dirty state, worktrees, and
   stashes. Work only in a clean side worktree from a target-contained approved
   head. Never absorb foreign dirty files.
2. Read only this roadmap, the selected card, named evidence, and touched code.
   Confirm the baseline still fails or the gap still exists.
3. Start one brokered work block with a human subject. Mark the item `ACTIVE`
   in the same change when practical.
4. Add the narrowest failing automated test or deterministic evidence seam.
5. Implement the smallest end-to-end fix. Centralize shared behavior instead of
   duplicating another GUI/batch/GPU variant.
6. Run focused tests, then the proportional regression suite. For output,
   playback, receipt, scaling, color, perf, or “behavior-preserving” changes,
   obey `docs/regression-prevention-program.md` and the cross-path/aspect rules.
7. Build the actual user-facing release for GUI-affecting work and report its
   path, timestamp, size, and SHA-256.
8. Commit only owned paths. Append `CODEX - HANDOFF` with the canonical full-40
   `Range:` and evidence. Freeze mutation.
9. Claude adversarially reviews that exact range and appends a later
   `CLAUDE - REVIEW` with the same `Range:` and a bare `Verdict:`. Address
   `CHANGES_REQUESTED` in the same item; do not skip forward.
10. Run brokered finalize, push/verify as policy requires, run repo sweep, then
    update this file: completed item to `DONE`, next item to `ACTIVE` or
    `EXTERNAL_GATE`. Continue without waiting for a new planning prompt.

Stop only when the queue is entirely `DONE`, `DROPPED`, or `EXTERNAL_GATE`, or
when the user changes product priority. An external gate parks only that item;
continue with the earliest dependency-safe local item and record the deviation.

## Paste-ready Codex bootstrap prompt

```text
Work in C:\!Layi Wkspc\MLV-App as the sole mutating implementer. Continuously
drain docs/roadmap.md until every item is DONE, DROPPED with evidence, or
EXTERNAL_GATE with an exact recovery action.

Read AGENTS.md, CLAUDE.md, then docs/roadmap.md. Treat docs/roadmap.md as the
only priority authority; older roadmap documents are detail/evidence only.
Maintain at most one mutating work block. Use a clean side worktree from a
target-contained approved head, preserve foreign dirty state, and use the
brokered start/finalize workflow. Do not start a later item while an earlier
item is ACTIVE or REVIEW unless the earlier item is explicitly EXTERNAL_GATE
and the later item is dependency-safe.

For each item: reproduce/confirm the baseline; add a failing deterministic test
or evidence seam; implement the smallest end-to-end slice; run focused and
proportional regression gates; apply known-good output A/B, aspect, cross-path,
and real GUI release rules when relevant; commit only owned paths; append a
CODEX - HANDOFF with the exact full-40 Range; then stop mutation until Claude
appends the matching later CLAUDE - REVIEW with a bare Verdict. After approval,
finalize, push/verify, repo-sweep, update roadmap state, and immediately select
the next item.

Codex implements. Claude reviews. Opus participates only when the selected card
requires specialist Q7/ABI/GPU proof. Do not bootstrap Fable or parallel
mutating lanes. Do not create roadmap YAML, validators, dashboards, or new
scaffolding unless a later measured failure justifies them. Corrected receipt
test path: tests/console/test_receipt_applier.cpp.

Begin by reporting the live target/provenance/Track-P ancestry, the selected
item, worktree, work-block id, baseline failure, and planned proof. Then execute;
do not merely propose another plan.
```

## Review prompt template

```text
Review the frozen Codex candidate for <ITEM-ID> in C:\!Layi Wkspc\MLV-App.
Range: `<FULL-40-START>..<FULL-40-FEATURE>`

Read AGENTS.md, CLAUDE.md, docs/roadmap.md, the selected item card, the CODEX -
HANDOFF entry, and the diff. Try to falsify correctness, output equivalence,
failure handling, tests, performance claims, and scope. Do not mutate product
code. Append a later `CLAUDE - REVIEW` entry to the configured coordination
file with the exact same Range and a bare `Verdict: APPROVE`,
`Verdict: CHANGES_REQUESTED`, or `Verdict: BLOCKER`, plus concrete evidence.
```
