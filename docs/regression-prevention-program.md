# MLV-App Regression-Prevention Program

Written 2026-06-27 after three independent output regressions (color casts, dark/flat
exposure, jerky playback) shipped over weeks and were only caught by manually building the
known-good June-9 build and eyeballing a before/after on real footage. This program automates
that manual A/B so the class of failure cannot recur silently.

## Root process failure (why all three slipped)

Every existing gate validated an INTERNAL or AGGREGATE proxy — "math preserved", `scale==4`,
telemetry-fields-present, sustained-FPS, color-heuristic-clear, run-to-run determinism stable,
self-hash of tiny fixtures — while **NO gate diffed the actual rendered OUTPUT of the candidate
build against a frozen known-good build on the user's real named footage.** All three landed
under self-asserted **"behavior-preserving / byte-identical / perf / refactor / scheduling"**
commit subjects — precisely the change class that needs output-equivalence *proof* and was the
only class that got none. The same-build ratio recon gate is structurally blind to a fix that
darkens both legs equally; the determinism gate passes a consistently-wrong WB; the FPS gate
reads 24 fps over jerky delivery.

**The principle:** "behavior-preserving" must be a claim that is **discharged against a per-clip
frozen golden** (or explicitly re-blessed with a human before/after), never asserted. The fix is
a thin freeze-and-diff layer over telemetry the app ALREADY emits — the gap was never measurement
capability, it was the absence of a pinned per-clip reference plus a parity assertion.

**Corollary — anchor on the known-good BUILD, never a same-codebase proxy.** The ground truth is
the last build that actually looked right, measured directly on the user's real footage. A
same-codebase *behavioral* proxy — an alternate mode or path you assume is equivalent-and-correct
(a "sync" fallback, a "reference" render, a "should-match" oracle, a scalar like FrameGreenAxis) — is
NOT ground truth: it can carry the *same* root defect, so "make the candidate match the proxy"
converges on the bug, not the fix. (Cost, 2026-06-27: a full night spent converging the async
auto-WB onto `MLVAPP_LOOK_ASSIST_SYNC=1` — which shared the same isolated-render dual-ISO defect and
was *itself* magenta over-correcting; the real cause only fell out when we finally A/B'd against the
Jun-9 build directly.) So at the **START** of any regression hunt — color, exposure, smoothness, or
unknown — build/locate the last-known-good baseline and A/B the real OUTPUT against it FIRST, before
forming a root-cause theory; make "matches the known-good build" the acceptance gate; and treat any
oracle you have not independently grounded against that build as a suspect, not a reference.

## The golden-master, at three altitudes (so non-determinism is contained)

1. **VALUES (deterministic, single-thread, BLOCKING on WB-locked legs).** Applied WB
   (`final_temp`/`final_tint`), presented-frame color stats (`presented_visible_green_axis`,
   `presented_green_artifact_ratio`, MainWindow.cpp:24259-24269), presented luma percentiles
   (p05/p50/p95 — one additive emit), levels. Frozen per-clip JSON stamped with the good-build
   SHA; tolerance from `lookassist-wb-determinism.ps1` constants. **Look-Assist-ON legs are
   ADVISORY** (auto-WB non-determinism), tolerance ~3× the measured run-to-run spread (capture
   5× on the good build); the **`--no-look-assist` + committed-receipt legs are the only ones
   safe to BLOCK on.**
2. **PIXELS (perceptual diff on a settled frame, generous tolerance, BACKSTOP).** The
   human-visible net: a 30% luma drop blows past any sane mean-abs-RGB / luma-median tolerance.
3. **CADENCE (percentile bands p90/p99/hitch_frac, median-of-N, ADVISORY only).** `dP99>15ms`,
   `dHitchFrac>0.02` vs the frozen distribution on a quiesced box. **Honest scope:** cadence is
   the flakiest, machine/load-sensitive signal; it is a warn-and-eyeball trigger paired with the
   `validate-visible-playback.ps1` live filmstrip, **never a hard block, and must never be
   allowed to mute the blocking color/exposure legs — a muted cadence gate is exactly how the
   cadence regression shipped.**

Plus the one **corpus-independent absolute** (BLOCKING, deterministic, no clips needed): a
fixed-point property test — auto-WB drives a near-neutral target to neutral AND a second measure
of the corrected frame is already neutral. Single-pass code cannot satisfy it, so it fails the
dropped refinement loop (8ddddce2) directly.

## Per-regression gate (each grounded in existing tooling)

- **COLOR** — (a) Tier-0 deterministic VALUES golden in `tests/console/test_clip_golden.cpp`:
  WB-locked `--profile-playback` run asserts `final_temp/final_tint` + `presented_visible_green_axis`
  vs frozen per-clip JSON — neutral-driven vs loop-dropped WB are numerically different per clip,
  so it fails the instant the loop is gone (where determinism-spread and the magenta/green-bar
  scanner both passed a uniform sepia). (b) `TEST(AutoWhiteBalance, DrivesNeutralTargetToNeutral)`
  in `tests/pipeline` — the corpus-independent fixed point.
- **EXPOSURE/SHARPNESS** — the pre-merge baseline A/B (`compare-output-budget.ps1`) on the **REAL
  8-bit present path**, NOT the pipeline fixture. CRITICAL: the half-res proxy lives only on
  `getMlvProcessedFrame8Scaled` (playbackPreview=1, RenderFrameThread.cpp:3157);
  `MlvPipelineFixture.renderFrame16Scaled` renders full-res regardless of scale (its own header
  admits it), so a fixture luminance test passes green while playback darkens. The A/B must read
  the 8-bit present buffer (`rgb8DisplaySource`) and diff presented mean-luma + contrast vs the
  frozen good build at **shipping defaults** (ExpectedScaleRequest=4) — absolute, not the
  same-build ratio the recon gate is blind to. Backstop: the Tier-0 seam's
  `playback_preview_mode`/`play_scale_active` + presented-luma percentiles catch proxy STATE
  per-commit; the `shipping-defaults.json` snapshot test fails CI on a silent `PreviewResolution=Auto`
  flip.
- **SMOOTHNESS** — cadence DELTA budget from the already-emitted `detect-playback-artifacts.ps1`
  ARTIFACT-CHECK line (p90_ms/p99_ms/hitch_frac, parsed at run-release-gui-smoke.ps1:1538-1546):
  FAIL-band on the DELTA vs the frozen June-9 distribution, median-of-3, ADVISORY, paired with the
  live filmstrip human oracle. (Re-entrant-present d2301d5c is only weakly observable as bimodal
  intervals; truly catching it needs new paired present-start/present-complete trace events.)

## Minimal high-leverage plan (priority order)

1. **Tier-0 console VALUES golden (build first, blocking per-commit).** `PresentedValuesMatchesGolden`
   in `tests/console/test_clip_golden.cpp` on the two committed fixtures via the existing
   `--profile-playback` seam, single-thread, WB-locked. Free, deterministic, near-zero flake;
   independently catches COLOR and the proxy-state/luma half of EXPOSURE.
2. **Neutral-convergence property test (blocking per-commit).** The corpus-independent absolute
   that fails the dropped loop.
3. **ONE pre-merge baseline A/B on the real 8-bit present path.** `compare-output-budget.ps1` over
   exactly 2 clips at shipping defaults; color+exposure WB-locked BLOCKING, cadence median-of-3
   ADVISORY. The only thing that catches the full EXPOSURE proxy + SMOOTHNESS. Wire into
   `run-shipping-guard-smoke.ps1`.
4. **Process defenses (near-zero cost, highest unknown-shape leverage).** See the binding rule in
   `AGENTS.md`/`CLAUDE.md`: re-classify behavior-preserving/byte-identical/proxy/scheduling diffs as
   highest-risk requiring an attached baseline-A/B PASS; tracked `tools/gates/shipping-defaults.json`
   + console equality test so a default flip fails CI; bless tooling refuses dirty/unstamped exes
   and requires `-Reason`.

## Residual risks (named, not hand-waved)

- **Corpus blindness is the largest unclosed risk:** golden coverage == corpus coverage. The
  current 6 homogeneous 5D3 dual-ISO travel clips miss a gray-card, low-key crushed-blacks,
  high-key, and non-dual-ISO 8-bit-non-neutral-receipt clip — a content-dependent cast (the probe
  documented tint -35/-26/-26/+18, different sign per clip) ships green. Mitigated only by the
  "later" corpus-widening item (ongoing maintenance, not a one-time fix).
- **Re-bless laundering** is the worst failure mode: under cost pressure with a noisy gate, the
  path of least resistance is to re-bless a regressed build green, and the same engineer who writes
  "byte-identical" re-blesses the contradicting golden. Only structural guard: buildstamp-pinned
  bless refusing dirty/unstamped exes + `-Reason` + a reviewed diff.
- **Auto-WB non-determinism** forces the perceptual legs to wide tolerances (a few mireds / 5-8%
  luma can hide) — which is why the BLOCKING legs are WB-locked and the auto-WB-on legs advisory.
- **Cadence flake** stays advisory; a real micro-stutter can pass it and must be caught by the
  human filmstrip.
- **Point-sample coverage:** a single settled frame + one 30s window catches nothing in motion
  content beyond the first frame, audio sync, seek/scrub, the CDNG export path (playback-only
  gates), memory growth, or regressions after frame N > settle.

## The one-line rule

A change whose subject contains *behavior-preserving / byte-identical / refactor / perf /
scheduling / proxy / no-op* is the **highest-risk** class and may not merge without an attached
baseline-A/B PASS on the real named clips (color+exposure WB-locked blocking; cadence + pixels
advisory) versus the pinned known-good build — proven, not asserted.
