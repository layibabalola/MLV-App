# Max-Playback Optimization Loop — Ledger

**Purpose.** Home document of the self-paced max-playback optimization loop (active goal set
2026-06-10): maximize every (scale x quality-mode) playback lane on the standard M16 trio without
regression. One loop iteration = one brokered work block = at most one candidate change. This file
is the loop's durable ledger: the IMMUTABLE baseline below is never edited; the CURRENT column is
updated only by validated keepers (with the commit cited); every attempt — kept or reverted — gets
an ATTEMPTS row. Protocol, gates, and stop conditions live in the canonical objective (mirrored in
agent memory `playback-max-loop-goal`); freeze history and the tainted/survives audit live in
[playback-look-assist-cold-open-fix.md](playback-look-assist-cold-open-fix.md).

**Why a 2026-06-10 re-baseline:** every earlier fps number was measured while a poisoned processed8
prefetch could serve frozen frames (see the audit doc). Nothing before this table is citable.

## Method (Phase 0, 2026-06-10)

- Build: `master` @ `896e6bea36512154bc973b561d2f764753c8f2e5`;
  `platform/qt/build-release/release/MLVApp.exe` SHA256
  `1019B497EA08E9E2E5B525FF4B915E62D3DA71D2F964DD94E278B3DD70508D2D`, 9,124,352 bytes,
  built 2026-06-10 21:38 (incremental make reported nothing-to-do at measurement time).
- Instrument: `tools/profiling/run-release-gui-smoke.ps1 -DetectPlaybackArtifacts` (traced,
  NO screenshots, 15 s in-app autoplay, settle 1.5 s, CPU-settle not required), one run per
  (lane, clip). Effective fps = detector `median_fps` over present-to-present intervals of the
  `draw_frame_ready.present_content` trace. Auto Look Assist in its DEFAULT state everywhere.
- Lanes: scale 1/2/4/8 via `MLVAPP_PLAYBACK_SCALE_FACTOR`; quality mode Sharp/Smooth =
  `MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW` unset (GUI "Sharp"/"Smooth"), Aggressive = `=1`
  (GUI "Aggressive Performance"). Native target = clip timeline rate ~23.976 fps (measured
  `timeline_fps` 23.85-23.99 on every run).
- Machine state: single sequential sweep 22:25-22:36 local, idle ambient load, cool start.
  Thermal sentinel = run 25 repeated run 1's lane: 5.4 -> 8.1 fps (run 1 paid a cold-cache,
  first-ever-launch penalty; NO thermal degradation across the sweep). Runs 9 and 17 are each
  clip's first run and may carry a smaller cold penalty.
- Spans: `validate-visible-playback.ps1` 30 s PrintWindow runs — all 8 lanes on M16-1327 plus
  x8 both modes on M16-1347/M16-1446. Raw artifacts:
  `.claude-state/profiling/20260610-phase0-baseline/` (scratch, same checkout only).

## IMMUTABLE BASELINE (2026-06-10) + CURRENT

Gates legend: `content` = frozen_content_runs=0, flicker=0, distinct hashes ~= presents (traced
run); `span` = viewport first-to-last span (gate >3) where measured; detector verdict in
parentheses where it needs context. Native = 23.976.

| # | Lane | Clip | Baseline eff fps | render ms | p99 ms | Gates @ baseline | CURRENT eff fps | CURRENT updated by |
|---|------|------|-----------------:|----------:|-------:|------------------|----------------:|--------------------|
| 1 | x1 Sharp | M16-1327 | 5.4 cold / 8.1 warm | 168.5 / 102.5 | 354 / 172 | content OK; span 51.4 SMOOTH; (detector FAIL = absolute >=250 ms rule, see notes) | 8.1 | baseline |
| 2 | x1 Sharp | M16-1347 | 6.5 | 131.7 | 246 | content OK (PASS) | 6.5 | baseline |
| 3 | x1 Sharp | M16-1446 | 7.1 | 117.4 | 201 | content OK (PASS) | 7.1 | baseline |
| 4 | x2 Sharp | M16-1327 | 11.0 | 68.7 | 170 | content OK; span 40.8 SMOOTH (PASS) | 13.5 | wb-412cff70908e4a7e, wb-5e98908e8af245c2 revalidation |
| 5 | x2 Sharp | M16-1347 | 11.0 | 65.4 | 154 | content OK (PASS) | 14.9 | wb-412cff70908e4a7e, wb-5e98908e8af245c2 revalidation |
| 6 | x2 Sharp | M16-1446 | 10.9 | 70.3 | 155 | content OK (PASS) | 15.0 | wb-412cff70908e4a7e, wb-5e98908e8af245c2 revalidation |
| 7 | x4 Sharp | M16-1327 | 13.0 | 47.1 | 146 | content OK; span 49.4 SMOOTH (PASS) | 20.9 | wb-a5315ef858a645b2 + wb-412cff70908e4a7e, human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 8 | x4 Sharp | M16-1347 | 13.7 | 42.1 | 121 | content OK (PASS) | 21.7 | wb-a5315ef858a645b2 + wb-412cff70908e4a7e, human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 9 | x4 Sharp | M16-1446 | 11.0 | 65.5 | 199 | content OK (PASS, hitch 0.63%) | 18.3 | wb-a5315ef858a645b2 + wb-412cff70908e4a7e, human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 10 | x8 Sharp | M16-1327 | 13.0 | 49.4 | 158 | content OK; span 48.7 SMOOTH; x8 canary clean (PASS) | 21.1 | wb-a5315ef858a645b2 + wb-412cff70908e4a7e, human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 11 | x8 Sharp | M16-1347 | 15.9 | 34.7 | 94 | content OK; span 63.3 SMOOTH; x8 canary clean (PASS) | 20.9 | wb-a5315ef858a645b2 + wb-412cff70908e4a7e, human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 12 | x8 Sharp | M16-1446 | 13.0 | 47.5 | 153 | content OK; span 39.7 SMOOTH; x8 canary clean (PASS) | 19.4 | wb-a5315ef858a645b2 + wb-412cff70908e4a7e, human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 13 | x1 Aggr | M16-1327 | 7.2 | 114.5 | 197 | content OK; span 41.3 SMOOTH (PASS) | 7.2 | baseline |
| 14 | x1 Aggr | M16-1347 | 7.2 | 114.3 | 215 | content OK (PASS) | 7.2 | baseline |
| 15 | x1 Aggr | M16-1446 | 7.2 | 123.7 | 245 | content OK (PASS) | 7.2 | baseline |
| 16 | x2 Aggr | M16-1327 | 12.3 | 58.4 | 135 | content OK; span 40.1 SMOOTH (PASS) | 12.3 | baseline |
| 17 | x2 Aggr | M16-1347 | 12.8 | 57.5 | 145 | content OK (PASS) | 12.8 | baseline |
| 18 | x2 Aggr | M16-1446 | 12.8 | 55.1 | 140 | content OK (PASS) | 12.8 | baseline |
| 19 | x4 Aggr | M16-1327 | 17.5 | 30.8 | 105 | content OK (249/257 hashes, benign dup presents, visual clean); span 40.7 SMOOTH (PASS) | 16.4 | wb-daff43758c844f74, hot-machine A/B values (cool re-anchor pending), human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 20 | x4 Aggr | M16-1347 | 13.2 | 43.8 | 149 | content OK (PASS) | 16.7 | wb-daff43758c844f74, hot-machine A/B values (cool re-anchor pending), human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 21 | x4 Aggr | M16-1446 | 13.9 | 38.0 | 109 | content OK (PASS) | 20.0 | wb-daff43758c844f74, hot-machine A/B values (cool re-anchor pending), human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 22 | x8 Aggr | M16-1327 | 13.0 | 49.2 | 138 | content OK; span 40.2 SMOOTH; x8 canary clean (PASS) | 14.0 | wb-daff43758c844f74, hot-machine A/B values (cool re-anchor pending), human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 23 | x8 Aggr | M16-1347 | 13.0 | 52.1 | 139 | content OK; span 64.7 SMOOTH; x8 canary clean (PASS) | 18.6 | wb-daff43758c844f74, hot-machine A/B values (cool re-anchor pending), human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |
| 24 | x8 Aggr | M16-1446 | 13.3 | 43.1 | 108 | content OK; span 42.7 SMOOTH; x8 canary clean (PASS) | 18.4 | wb-daff43758c844f74, hot-machine A/B values (cool re-anchor pending), human PASS 2026-06-11 (x8 Sharp: cold-first-play finding open) |

**Lanes already at native rate: NONE.** Honest summary: every lane is below the ~23.976 native
target. x1 ~5-8 fps (compute-bound, render 102-168 ms); x2 ~11-13; x4 ~11-17.5; x8 ~13-16.
Worst-vs-native ranking (Sharp): x1 (~30% of native), x2 (~46%), x4/x8 (~50-66%).

Suite baseline for the TESTS gate: 16 pre-existing Qt-linked pipeline failures as of 2026-06-10
(pinned during the stuck-frame fix validation at this code state). Keepers must stay a strict
subset; re-confirm at the then-current SHA inside each code-change iteration.

Human spot-check: PENDING for x4/x8 Sharp (first keeper, wb-a5315ef858a645b2) — requested in the
loop report. Correction to an earlier note: the GUI bottom-left fps IS captured by the harness as
log.summary.gui_fps_status_value in every kept smoke JSON (Phase 0 x2 Sharp 1327 = 17, x8 = 14).

## Instrument calibration notes (2026-06-10)

1. `detect-playback-artifacts.ps1` FAILs any non-screenshot present interval >= 250 ms as a
   "freeze". At honest slow lanes (x1 ~5 fps => ~185 ms median interval) a 354 ms worst interval
   trips this structurally even with perfectly advancing content (row 1: 77/77 distinct hashes,
   0 frozen runs, 0 flicker). The rule was calibrated for near-real-time lanes. Treat x1 detector
   FAILs with content-clean sub-metrics as "slow but honest"; a lane-aware threshold is a
   legitimate measurement-infra candidate for a future iteration (own work block + gates).
2. First-ever app launch (run 1) pays a large cold penalty (5.4 vs 8.1 warm same lane/SHA);
   first-run-of-clip pays a smaller one. Compare like-for-like warmth in A/Bs; the sentinel-rerun
   pattern (repeat run 1's lane at sweep end) quantifies drift per sweep.
3. Span-validator note: its own "longest stall" metric read 0-200 ms on all 12 runs — no real
   stalls anywhere in Phase 0.

## Bottleneck evidence for the next iteration (2026-06-10, Phase 0 telemetry)

- **render_total is nearly scale-insensitive in Sharp mode**: x2 ~65-70 ms, x4 ~42-65 ms,
  x8 ~35-50 ms — a 16x pixel-count drop from x2 to x8 buys almost nothing. The per-frame cost is
  dominated by a scale-insensitive stage, consistent with every foreground frame paying the
  indirect processed16->8 render now that the processed8 prefetch fails closed (correctly) on the
  default-Look-Assist direct8-incompatible state. The ~80-95 ms present medians sit at ~2 timer
  ticks (42 ms tick) — the wait-for-second-tick quantization signature.
- SCOPE-blessed candidate to rank first next firing: teach the prefetch worker the indirect
  processed16->8 render so its hits are honest under default Look Assist — legitimate throughput
  lever IF hit content passes the content gate (the old bug class makes this the #1 validation
  target). Expected effect: render cost moves off the foreground; presents catch the first tick;
  x2/x4/x8 Sharp approach native.
- x1 remains compute-bound with no safe (non-pixel) lever — tradeoff-menu territory, never
  autonomous.
- Aggressive lanes: faster at x1 (7.2 vs 5.4-7.1) and x2 (12.3-12.8 vs ~11), mixed at x4
  (17.5 best lane overall on 1327, but 13.2 on 1347), no benefit at x8 (~13 both modes). The
  x4 Aggr 1327 vs 1347 spread (17.5 vs 13.2) is a single-run cross-clip difference — re-measure
  before reading meaning into it.

## LOOP STOPPED 2026-06-11 (stop condition 2) - final standings + tradeoff menu

**Warm standings on 7dbb7326 (all keepers active):** x4/x8 BOTH MODES at or near native on the trio
(x8 Sharp 22.2-23.8 warm incl. 1327; x4 17.9-21.3; aggressive x4/x8 within ~2 fps of Sharp);
x2 Sharp 13-14.3 / x2 Aggr 12-14.3 (render-bound 45-70 ms); x1 6.2-8.8 (compute-bound by design).
Cold first-runs of a clip can read 30-40% low (sentinel + 1327 re-anchor lesson) - judge lanes warm.

**Remaining levers are all pixel-affecting or contested (the autonomous loop's boundary):**
1. x2 (both modes): quarter-res playback preview (validated mechanism, reverted by user call in the
   prior loop's iter 13 on a tainted basis; honest expectation now: render 45-70 -> ~15-25 ms,
   x2 -> native, visibly softer DURING playback only). User decision per lane.
2. x2 Aggressive inversion: remap aggressive x2 off the expensive HQ mean23 recon (it renders
   SLOWER than Sharp today, 59-69 vs 45-50 ms). Within the mode's quality contract but in the
   thrice-reverted fragile zone - user decision.
3. x1: no non-pixel lever exists (full-res compute, threads maxed, no GPU). Options: a lower-res
   preview/proxy mode for x1 playback (pixel-affecting), or accept ~8 fps.
4. Measurement-infra (non-pixel, anytime): lane-aware detector freeze threshold for honest slow
   lanes; per-clip warm-up runs in sweep scripts.

**Human spot-checks COMPLETED 2026-06-11 (M16-1327, user-driven GUI):**
- x2 Sharp: PASS. x4 Aggr: PASS. x8 Aggr: PASS (no corruption on the historical lane).
- x4 Sharp: PASS with note - the bottom-left fps readout swings high/low. Matches the measured
  p99 spread (miss-bursts on the heavy clip) AND the readout itself: under the 8 ms timer the
  status text shows near-instantaneous frame-to-frame rates, which swing even when the median is
  steady. A rolling-average fps readout is a small UI follow-up (menu item 5).
- x8 Sharp: CONDITIONAL - first cold play did not progress displayed frames until ~25% of the
  clip had played; second play smooth. Cold-start-only; automated gates are blind here by
  construction (the smoke pre-settles, spans/sweeps ran warm). OPEN FINDING, see below.
- NEW USER OBSERVATION (all lanes): brief HORIZONTAL TEARING right after pressing Play on a
  fresh clip. Not a wrong-content prefetch artifact (hit bytes are test-pinned + hash-gated);
  ranked suspects: (1) display-buffer write/paint overlap that the 8 ms present cadence exposes
  while first-play renders are slowest (widest overlap window), (2) pre-existing first-play
  behavior predating the loop. Bisect: reproduce with MLVAPP_PLAYBACK_TIMER_POLL_MS=-1 (legacy
  timer, prefetch on) and with MLVAPP_PROCESSED8_PREFETCH_INDIRECT=0 (worker off, fast timer).
  Tearing only with the fast timer => presentation race; tearing in both => pre-existing.
  BISECT VERDICT (user-run, 2026-06-11): tearing in BOTH arms => PRE-EXISTING first-play
  presentation behavior, not introduced by any keeper. Standalone correctness item (chip
  task_8a246aa5 queued: cold first-play stall + tearing, one cold-path investigation).

**Open follow-ups from the human gate:** cold first-play x8 frame-progression stall + first-play
tearing (likely the same cold-path complex: look-assist settle + worker warm-up + coldest decode
all compete in the first seconds). Deserves its own session; not a regression of a measured gate
(content/span/suite all clean warm), but user-visible on first play.

## APPROVED-MENU PHASE (user granted all four items 2026-06-11; driver plans, Sonnet implements)

Iteration A1 (wb-ae312bc0d9da4605, 76d45430): cold-first-play INSTRUMENT + DIAGNOSIS (no code change).
New tool: .claude-state/scripts/cold-firstplay-capture.ps1 (cold-copy launch, PrintWindow burst from
window-appearance, 100 ms cadence, trace capture). Findings:
- STALL (item 1b) ROOT CAUSE: residual Auto Look Assist auto_wb analysis blocks the UI thread
  683-901 ms at first Play (look_assist.apply.unsettled -> auto_wb complete; x8-cold 683 ms,
  x4-cold 874 ms) - the playback timer freezes, then catch-up drops 17-22 frames in one jump
  (time_diff_ms=719/897). Same residual the cold-open fix deferred ('move off-thread' option).
  Fix planned for next firing: compute the analysis on a worker thread, apply on the UI thread
  via queued signal; kill-switch env.
- TEARING (item 1a): pixel-buffer race RULED OUT by code map - FrameSlots are pin-protected while
  presenting (RenderFrameThread.cpp:261-309), zero-copy QImage lifetime is guarded
  (PlaybackScaling.h:902, displayImageOwnsData=false + releasePresentedFrameForRequestSerial).
  Not reproduced by the 100 ms instrument in 5 s windows (2 near-threshold flags appear in BOTH
  cold and warm arms = likely scene content). Working hypothesis: QGraphicsView minimal-viewport
  partial update across a DWM frame on maximal content changes. Candidate fix (next firings):
  full-viewport update mode on present (cheap, gateable); needs a 50 ms-cadence first-500 ms
  capture to confirm repro first.

### Item 1a results

- What changed: `platform/qt/MainWindow.cpp` temporarily moved the residual Look Assist auto_wb
  analysis onto a worker thread with a `MLVAPP_LOOK_ASSIST_SYNC` kill switch, generation guard,
  and queued apply; the code was then reverted after validation failed.
- Before/after: repeated cold-first-play captures on `C:\temp\MLV\M16-1347.MLV` x8 dropped the
  initial stall materially versus the 683-901 ms baseline, but the detector still failed the gate
  (`max interval 393 ms`, `max stall 3100-3400 ms` across repeated captures). Sync-mode
  confirmation on `20260611-codex-1a-synccheck` reproduced the old blocking pattern
  (`max stall 3200 ms`).
- Gates run: cold capture logs, detector gap scan, steady-state traced smoke attempts, and x8
  canary captures were collected, but the item did not clear the standing <250 ms cold-stall gate
  and therefore is a dead end.
- Artifact paths:
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1a-m1347-x8/`
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1a-m1347-x8-v2/`
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1a-m1347-x8-v3/`
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1a-synccheck/`
- Pending human eyes: none for the dead-end decision; the next item can start from the reverted
  tree without carrying partial code forward.

### Item 1b results

- What changed: no code change. I only tightened the cold-first-play capture cadence to 50 ms
  and inspected the first-play window for the suspected horizontal seam.
- Before/after: three cold attempts on `M16-1347 x8`, `M16-1327 x4`, and `M16-1446 x8` all
  produced the same early black/top-band transition and moving lower content, but no confirmed
  tear seam under visual inspection. The tear heuristic lit up many frames in every attempt,
  but the flagged frames did not show a clear top-half / bottom-half mismatch once viewed.
- Gates run: 3 cold attempts completed; no confirmed tear reproduced under the instrument.
- Artifact paths:
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1b-x8-a1/`
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1b-x4-a2/`
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1b-x8-a3/`
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1b-x8-a1/cap-001.png`
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1b-x8-a1/cap-002.png`
  - `.claude-state/profiling/20260611-iter6-coldplay/20260611-codex-1b-x8-a1/cap-003.png`
- Pending human eyes: none for the dead-end call; the data was not reproducible enough to justify
  a fix candidate.

### Item 2 results

- What changed: `src/mlv/video_mlv.c` keeps the quarter-res x2 preview default-on for playback
  preview, preserves `MLVAPP_DISABLE_QUARTERRES_X2_PREVIEW` as the comparison kill switch, and
  gates aggressive x2 back to the full-XY path after the quarter-res aggressive arm regressed the
  artifact gate. `tests/pipeline/test_dual_iso_pipeline.cpp` now codifies the sharp keeper and
  the aggressive full-XY fallback.
- Keeper lane: x2 Sharp PASS on all three clips in the current smoke set. Latest result files:
  `.claude-state/profiling/20260611-item2a/x2-sharp-on-1327-v5/result.json`,
  `x2-sharp-off-1327-v5/result.json`, `x2-sharp-on-1347-v5/result.json`,
  `x2-sharp-off-1347-v5/result.json`, `x2-sharp-on-1446-v5/result.json`,
  `x2-sharp-off-1446-v5/result.json`. Representative median fps from the current keeper state:
  1327 `13.5` current on, 1347 `14.9` current on, 1446 `15.0` current on.
- Dead-end lane: aggressive x2 quarter-res did not clear the detector gate, so the final code
  keeps aggressive on the full-XY fallback path 4. Current aggressive result files:
  `.claude-state/profiling/20260611-item2a/x2-aggr-current-1327-on/result.json`,
  `x2-aggr-current-1327-off/result.json`, `x2-aggr-current-1347-on/result.json`,
  `x2-aggr-current-1446-on/result.json`. Representative current aggressive median fps from the
  valid PASS runs: 1327 `13.0` on / `14.1` off, 1347 `13.2` on, 1446 `13.5` on.
- Gates: `tests/build-ci-pipeline/release/pipeline_tests.exe` subset
  `DualIsoPipeline.Phase4B_DualIsoScaleTwo*` PASS 4/4 after rebuild; valid traced smoke runs PASS
  with detector verdict PASS on the current on-arms and the valid 1327 off-arm. No screenshot-backed
  before/after stills were captured in this iteration, so the visual compare remains open.
- Pending human eyes: the playback-only softness review still needs a later screenshot pass if the
  user wants a visual compare on the x2 tradeoff. The code-side verdict is honest: sharp keeper,
  aggressive dead-end.

### Item 3 results

- What changed: I attempted the x1 half-res playback-preview proxy in `src/mlv/video_mlv.c`
  with the `MLVAPP_DISABLE_HALFRES_X1_PREVIEW` kill switch and added the corresponding pipeline
  coverage in `tests/pipeline/test_dual_iso_pipeline.cpp`, then reverted the code after the smoke
  gate proved the lane was not keepable.
- Before/after: the current release-tree smoke on `C:\temp\MLV\M16-1327.MLV` failed in both
  arms. Default-on smoke landed at `presented_fps=9.852` / `guiStatusText="Playback: 12 fps"`
  and kill-switch smoke landed at `presented_fps=7.793` / `guiStatusText="Playback: 9.3 fps"`,
  but both runs tripped the playback artifact detector on long gaps (`max_gap_ms=190289`).
- Gates: full Phase4B pipeline suite PASS 186/238 after rebuild, but the required traced smoke
  runs FAILed for both the default-on and kill-switch arms. The detector flagged mid-playback
  discontinuities, so x1 is a dead end, not a keeper.
- Artifact paths:
  - `.claude-state/profiling/20260611-item3-x1-smoke-current/`
  - `.claude-state/profiling/20260611-item3-x1-smoke-kill/`
  - `.claude-state/profiling/logs/mlvapp-20260611.log`
- Pending human eyes: none for the dead-end decision; the code was reverted and the tree is
  clean of the x1 proxy attempt.

### Item 4 results

- What changed: `platform/qt/MainWindow.cpp` now smooths the displayed bottom-left playback FPS
  with a roughly 1-second EMA and only refreshes the label every 250 ms; `platform/qt/MainWindow.h`
  carries the new smoothing state. Trace / telemetry fields were left alone.
- Before/after: the smoke summaries now report a steadier `gui_fps_status_text` while playback
  continues unchanged. Scale 2 on `M16-1327` reported `Playback: 13 fps` in the end-of-run smoke
  summary and `Playback: 14 fps` in the bottom-left crop; scale 4 reported `Playback: 21 fps`;
  scale 8 reported `Playback: 20 fps`.
- Gates: `validate-visible-playback.ps1` passed on all three scales with spans above 3 and no
  glitch candidates. Dedicated smoke sweeps at scales 2/4/8 completed with the requested scale
  request set correctly; the screenshot-backed scale-2 crop showed the expected steady label.
- Artifact paths:
  - `.claude-state/profiling/20260611-item4-scale2-1327-nodet`
  - `.claude-state/profiling/20260611-item4-scale4-1327-nodet`
  - `.claude-state/profiling/20260611-item4-scale8-1327-nodet`
  - `.claude-state/profiling/20260611-item4-scale2-1327-shot.json`
  - `.claude-state/profiling/20260611-item4-scale2-1327-shot/screenshots/M16-1327-fps-status.png`
  - `.claude-state/profiling/20260610-visval/item4-s2/result.txt`
  - `.claude-state/profiling/20260610-visval/item4-s4/result.txt`
  - `.claude-state/profiling/20260610-visval/item4-s8/result.txt`
- Pending human eyes: the screenshot-backed crop was checked directly; no unresolved visual concern
  remains for the cosmetic label smoothing itself.

## ATTEMPTS log

One row per candidate, appended BEFORE measuring (cols 1-9), completed with the result after
(cols 10-12). Kill-categories: theory / measurement / baseline-comparison / attribution.

| Date | Lane | Change | Expected effect | Metric | Baseline row | Build SHA | Holds only if... | Gates run | Measured result | Verdict | Kill-category / commit |
|------|------|--------|-----------------|--------|--------------|-----------|------------------|-----------|-----------------|---------|------------------------|
| 2026-06-11 | x2 Sharp (x4/x8 Sharp expected to co-benefit) | Prefetch worker renders the indirect processed16->8 composition for direct8-incompatible states (default Look Assist case), gated by a faithful-copy state allowlist + `MLVAPP_PROCESSED8_PREFETCH_INDIRECT` (default on, =0 restores skip). video_mlv.c worker task + new indirect helper; 3 new pipeline tests incl. worker-hit byte-identity. | The worker hides the ~65-70 ms foreground indirect render behind the next frame; presents catch the first 42 ms tick instead of the second; x2 Sharp 11 -> toward ~24. | detector median_fps + prefetch hit rate + all content gates, interleaved on/off A/B (env kill switch), 3 clips | rows 4-6 (x2 Sharp), regression watch rows 1-24 | code on top of 6f8121ac (exe SHA in result) | Holds only if worker hit CONTENT stays byte-faithful to the foreground render (test-pinned + content gates) AND worker/foreground CPU contention does not slow the miss path on 16 threads. | In-flight findings: (a) first interleaved A/B was NULL - 0 hits; signature probe (MLVAPP_PREFETCH_DEBUG, kept) showed the worker gate evaluating direct8 compatibility OUTSIDE the thread-local preview-policy envelope while the render fails closed INSIDE it (pre-existing asymmetry: baseline worker wasted ~39 decodes/run the same way); fixed by hoisting the envelope to task level; re-probe: 227 hits / 66 misses, 238 stores, 0 render failures. (b) Suite v1: one new failure Phase4A_TestProcessed8CacheScaleKeyIsolation - its signature-stability assertion assumes a quiescent worker (llrawproc-status drift, documented Phase E7 behavior); test now quiesces the worker via env, all assertions unweakened. (c) Suite v2 AND v3 died 0xC0000005 at the SAME location (right after the known-fail FastHqPathForFastX2) -> not random. Root cause found: `mlv_ensure_reusable_buffer` uses realloc, and the direct/indirect render helpers captured the TLS rgb_u16 pointer at out-size BEFORE the scaled decode whose internals grow the same slot for mid-res intermediates -> realloc moves the block -> render writes through the stale pointer into freed memory (silent UAF; write+read use the same stale pointer so data flows until the heap reuses the block). PRE-EXISTING on the direct worker path; the foreground escaped because its first x1 render pre-grows the slot to full-res; my change exploded worker exposure. Fixed by pre-growing the TLS slot to full-res RGB (the supremum of all intermediates) before capture in all three render helpers. (d) Post-hoist A/B: x4 Sharp on 18.2-19.2 vs off 15.9-16.1, x8 Sharp on 18.2-21.3 vs off 16.4 (render absorbed 10-20 ms vs 29-37 ms) = REAL wins; x2 Sharp wash-to-negative (worker indirect render ~= foreground cost, splits cores; 1347 down both reps); x2 Aggressive single-run -18% (aggressive incompatible lanes skip the main cache so worker output is never consumed = pure contention). Candidate NARROWED: indirect worker render only at scale>=4 AND not aggressive; x2/aggressive revert to baseline skip behavior. Tests moved to x4 accordingly. | PIXELS span 48.2/47.9/62.7/40.0 SMOOTH (x4-1327, x8 trio); CONTENT all runs PASS (hashes~=presents, 0 frozen, 0 flicker, hitch<=0.6%); VISUAL x8 trio + x4 filmstrips inspected clean; TESTS suite completes post-UAF-fix, fail set = baseline 16 + AggressiveX2PlaybackPreviewUsesQuarterresShadowsHighlights which REPRODUCES on the unmodified baseline exe under ProcessingFilters.* order (pre-existing order-flake, not this change; isolation 8/8 PASS both exes), 3 new tests pass incl. worker-hit byte-identity; HUMAN pending | Interleaved A/B (exe 2AD588E4) on/off: x4 Sharp 19.6/21.3/20.8 vs 16.1/16.4/16.4 (+22-30%), x8 Sharp 21.7/22.2/21.7 vs 16.7/16.4/16.7 (+30-35%, ~90-93% of native), render absorbed 8.4-14.4 ms vs 29-37 ms; x2 Sharp arms identical machinery (noise only), x2 Aggr byte-equal arms; final exe A43CEEFF anchors: x4 19.2 fps render 15.8, x8 22.2 fps render 8.5, content clean | KEEP (x4/x8 Sharp; x2 attempt recorded as dead end: worker render ~= foreground cost at x2, kill-category attribution/measurement) | wb-a5315ef858a645b2; also hardened: TLS realloc UAF in render helpers (suite crashes eliminated), SH quarter-res policy caches made thread-local (cross-thread poisoning), MLVAPP_PREFETCH_DEBUG tracing kept env-gated |
| 2026-06-11 | x1 playback preview proxy (user-approved quality trade) | Half-res x1 playback-preview proxy in `src/mlv/video_mlv.c` with `MLVAPP_DISABLE_HALFRES_X1_PREVIEW`; x1 pipeline coverage in `tests/pipeline/test_dual_iso_pipeline.cpp` (then reverted after the smoke gate failed). | x1 ~8 fps -> ~13-15 fps while keeping playback preview only; default-on should beat the full-res x1 lane and the kill switch should preserve the original path for comparison. | detector median_fps + long-gap freeze gate + full Phase4B suite + traced smoke on all three clips | rows 1-3 and 13-15 (x1 Sharp/Aggr) | code on top of 900762b6d9bc712fffd02ca7d2f70f5152c91e24 | Holds only if the default-on arm improves or holds on all three clips and no other lane regresses beyond noise, with the smoke detector staying clean and the x1 kill switch preserving the baseline. | Phase4B pipeline suite PASS 186/238 after rebuild; traced smoke on `M16-1327` default-on and kill-switch arms; detector artifact scan on both arms | default-on `presented_fps=9.852`, kill-switch `presented_fps=7.793`; both smoke runs FAILed the playback artifact detector on long gaps (`max_gap_ms=190289`, `long_gaps=2/3`, mid-playback freeze). | DEAD END | measurement / attribution; reverted in wb-6120f5d5c6074126 |
| 2026-06-11 | fps readout smoothing (cosmetic) | Smooth the displayed bottom-left playback FPS text in `platform/qt/MainWindow.cpp`/`.h` with a ~1 s EMA and 250 ms update cadence; only the display text changes, not trace/telemetry. | The visible `Playback: ... fps` label should stop flickering between instantaneous frame intervals and read steadier during playback while all actual playback telemetry remains unchanged. | traced smoke `gui_fps_status_value` + `visibleBottomLeftGuiStatusText` on scales 2/4/8, plus a visual sanity check that the display reads steady | rows 4-6, 7-12, 13-18, 19-24 (display-only; no perf delta) | exe SHA `555B118A4871027C910BC5FBACFBC73DC0DF902D918F9A66849E23EC3B0A164B` | Holds only if the cosmetic text update leaves playback trace fields unchanged, the smoke detector stays clean, and the visible FPS label stays steady on all three scales. | `validate-visible-playback.ps1` PASS on scales 2/4/8 (`span=48.74/46.17/49.31`, `longest_stall=200/0/0ms`, `glitch_candidates=0`); traced smoke summaries on scales 2/4/8 reported `gui_fps_status_value=13.0/21.0/20.0` and scale-2 screenshot crop showed `Playback: 14 fps` | `Playback: 13 fps` / `Playback: 14 fps` crop at scale 2; scale 4 `Playback: 21 fps`; scale 8 `Playback: 20 fps`; trace telemetry unchanged | KEEP | wb-6120f5d5c6074126; cosmetic-only, no perf delta |
| 2026-06-11 | Look Assist async cold-open | Move the residual auto_wb analysis off the UI thread; keep clip-open behavior identical while avoiding the 683-901 ms first-play stall. Fresh per-call env reads, generation guard, and QObject lifetime guard around the queued apply. | Cold first play should stop freezing the playback timer; first 8 s after draw should stay below the stall threshold while the async-applied event still fires once per clip open. | cold-firstplay-capture logs, detector gaps, steady-state traced smoke, kill-switch cold run, x8 canaries | item 1a / rows 1-3 and 13-15 for playback watch | 9DBB93A241059013450C59CF860239EE9CCBCAFB1D91BE6AD17F5A7676031621 | Holds only if the queued apply stays clip-safe, the kill switch reproduces the old stall, and no extra regressions appear in the traced smoke or canary checks. | cold-firstplay-capture (1347 x8, repeated), detector gap scan, sync-kill cold run, smoke attempts, x8 canaries | direct async worker cut the first-play stall down materially, but the detector still failed the <250 ms gate on the cold trace (max interval 393 ms; max stall 3100-3400 ms across repeated captures). Sync-mode kill check on 20260611-codex-1a-synccheck reproduced the old blocking pattern (max stall 3200 ms). | DEAD END | measurement / no-keep |
| 2026-06-11 | First-play horizontal tearing repro | Re-run the cold-first-play capture at 50 ms cadence on the suspected tear clips, starting at play and keeping the GUI visible/uncovered, to separate a real seam from the first-play black/transition band. | A real tear should show a top/bottom mismatch in a single capture and be reproducible across cold attempts; if not confirmed after 3 cold attempts, dead-end the hypothesis. | row-wise seam inspection, tear-flagged.json, cap PNGs from three cold attempts | item 1b / cold-open tear notes and the current `loop stopped` hypothesis | 9DBB93A241059013450C59CF860239EE9CCBCAFB1D91BE6AD17F5A7676031621 | Holds only if the 50 ms instrument confirms a seam that the reviewer can see in the captured PNGs; otherwise the hypothesis is not reproducible. | cold-firstplay-capture at 50 ms, early cap PNG review, three cold attempts across x4/x8 and multiple clips | Three cold attempts (`20260611-codex-1b-x8-a1`, `-x4-a2`, `-x8-a3`) all showed the same black top band / moving lower content transition, but no confirmed tear seam under visual inspection; the heuristic flagged many frames, yet the flagged frames did not produce a clear top-vs-bottom mismatch when viewed. | DEAD END | measurement / no-keep |
| 2026-06-11 | x2 Sharp (x4/x8 Sharp expected to co-benefit toward the 24 fps tick ceiling) | RE-AUDIT of the twice-reverted playback-timer de-quantization on HONEST baselines (prior kills were baseline-comparison casualties of the poisoned-hit era; the flicker that killed attempt 1 is now mechanically guarded + headlessly gated). `mlvappPlaybackTimerIntervalMs()`: poll the playback timer at 8 ms instead of the clip rate; pacing stays elapsed-time (DropFrameMode); `MLVAPP_PLAYBACK_TIMER_POLL_MS` -1 = legacy poll (A/B + kill switch), >0 explicit. MainWindow.cpp, 2 call sites + helper. | A finished ~45 ms x2 render presents on the next 8 ms tick instead of waiting out a full 42 ms period: x2 Sharp ~11-16 -> toward 1000/render (~20-22); prefetch-fed x4/x8 (render 8-15 ms) -> toward native. | detector median_fps + flicker_back_jumps (must stay 0) + all content gates, interleaved default-vs-legacy-env A/B, 3 clips | rows 4-6 target; rows 1-3 (x1) and 7-24 no-regress watch | code on top of b90a954f | Holds only if 125 Hz idle polling stays negligible on the UI thread (busy ticks early-out; idle ticks are cheap) and the forward-only guard keeps faster servicing temporally clean (flicker 0, no stale presents). | PIXELS span 50.0/48.7/48.9/61.3/41.3 SMOOTH (x2/x4/x8 1327 + x8 trio); CONTENT clean every run incl. hashes==presents after the dup-draw guard; flicker_back_jumps=0 on ALL 40+ runs; VISUAL x8 trio inspected clean; TESTS suite 16 failures set-IDENTICAL to baseline | In-flight: content gate caught the fast poll re-presenting identical frames (~20% dup hashes at x4/x8, presents>native) -> added the same-position skip-draw guard (playback only); post-guard hashes==presents. Interleaved A/B: x2 Sharp on 13.0-15.4 vs off 12.7-13.2 (+11% mean, 6/6 pairs); x4 Sharp distinct-rate 17->18.3-21.7/s; x8 Sharp post-guard interleaved ON 20.8/21.7/20.8 vs OFF 20/15.2/15.4 (ON holds while OFF sags with heat = thermal resilience); x1 8.1 both arms; x2 Aggr 13.9 vs 12.8 (single run, within noise, direction positive). GUI bottom-left fps ON 16-27 vs OFF 6.8-16. Legacy off-arm also FAILed detector once (pre-existing borderline 250ms trips at x2 on warm machine). | KEEP (timer de-quantization default-on at 8 ms; MLVAPP_PLAYBACK_TIMER_POLL_MS=-1 restores legacy, >0 explicit; prior twice-reverted verdicts retired as baseline-comparison casualties) | wb-412cff70908e4a7e |
| 2026-06-11 | x8 Aggr (x4 Aggr co-benefit) | Aggressive x4/x8 consult the processed8 cache again (foreground lookup re-enabled at scale>=4; foreground stores stay skipped) + worker indirect gate drops the !aggressive clause at scale>=4. Re-anchored honest aggressive standings first on 552cc2a7: x2 Aggr 12.7/11.4/13.0 render 59-69 ms (render-bound, perf-mode inversion persists), x4 Aggr 22.2/21.7/20.4, x8 Aggr 18.2/17.2/18.5 render 36-39 ms. New aggressive-ambient byte-identity test. | x8 Aggr ~17-18.5 -> ~21+ (worker hides the 36-39 ms render); x4 Aggr toward native; x2 Aggr unchanged (excluded: contention wall). | detector median_fps + hit content + all gates, interleaved indirect-on/off A/B; x8 Aggr is THE historical corruption canary - visual gate extra-strict | rows 19-24 target (anchors above supersede stale Phase-0 CURRENT); rows 4-12 no-regress | code on top of 552cc2a7 | Holds only if worker aggressive-envelope renders are byte-faithful to foreground aggressive renders (test-pinned) and x8 aggressive stays artifact-free under prefetch fills (canary). | PIXELS spans x8a1 68.0/67.1/41.5 SMOOTH; CONTENT hashes==presents all 22 A/B runs, frozen=0, flicker=0; VISUAL x8 aggressive trio inspected CLEAN (the historical corruption path, prefetch fills active); TESTS suite run1 = baseline16 + known ProcessingFilters flake + ONE new name (StandardPreviewScaleTwo...SH) that passed in isolation, passed in TU order, and passed in suite run2 -> one-off order/timing flake in the documented SH-fragility family (iter-4 code provably inert for its mode); 4 prefetch tests pass incl. new aggressive byte-identity (hits=1, mismatched=0) | Interleaved A/B (hot machine, exe 614A8D02): x8 Aggr on 13.5-19.6 vs off 12-15.2 (+18% mean, 5/6 pairs); x4 Aggr on 16.4-20.0 vs off 14.3-14.9 (+12-34%); x2 Aggr 13.9 vs 13.5 unchanged (excluded); x8 Sharp no-regress (20.8 on). Renders absorbed: x8a1 23-29 ms vs 45-62 off. Pre-change cool anchors were x4a1 20.4-22.2 / x8a1 17.2-18.5 - absolute CURRENT values below carry a hot-machine caveat; next firing re-anchors cool. | KEEP | wb-daff43758c844f74 |
| 2026-06-11 | x4/x8 on M16-1327 (the heavy-clip laggard) | Cool re-anchor first (7dbb7326: 1347/1446 x8 Sharp AT native 23.3/22.2; 1327 lags every prefetch-fed lane: x4a0 12.7, x8a0 14.5, ~1% hitch FAILs). Hit-rate probe 1327 x8: 276 hits / 5 misses (98%) - misses are NOT the residual; ranked bottleneck = worker/UI contention (heavy dual-ISO worker renders saturate 16 OpenMP threads while the UI presents). Candidate: env-tunable worker render-thread cap MLVAPP_PROCESSED8_PREFETCH_THREADS (unset = legacy full threads; cache keys unchanged). | Capping worker parallelism leaves UI headroom: 1327 x4/x8 toward 18+. | interleaved env A/B (unset vs 12 vs 8) on 1327 x8/x4 | rows 7/10/19/22 | 7dbb7326 + cap patch | Holds only if processing output is thread-count-invariant (static scheduling; content gates + visual verify) AND the slower worker still outpaces playback (98% hit margin currently thin: render 35-50 ms vs 42 ms budget). | content gates clean all 6 runs (hashes==presents) | Interleaved 1327 x8: full 23.8/23.8 vs t12 22.7/23.3 vs t8 23.3/22.7 - NO cap benefit, and the LEGACY arm hit native: the re-anchor's 1327 sag (14.5 FAIL) was a COLD-CACHE transient (its rows were the clip's first runs of the session), not clip character. Warm, 1327 x8 = native. | DEAD END (no-keep; cap code reverted, tree clean). Kill-category: measurement (cold-state contamination of the re-anchor) + theory (contention hypothesis dead on warm runs). LESSON: re-anchor sweeps need a warm-up run per clip before citable rows. | reverted in wb-91d04bf1a7f64803 |
| 2026-06-11 | x2 Sharp/Aggr (all three clips) | Quarter-res x2 playback preview default-on for playback preview; `MLVAPP_DISABLE_QUARTERRES_X2_PREVIEW` kill switch. `src/mlv/video_mlv.c` quarter preview core + x2 dispatcher + default-on quality gate; `tests/pipeline/test_dual_iso_pipeline.cpp` sharp keeper and aggressive full-XY fallback coverage. | x2 Sharp should take path 5 by default; aggressive x2 was re-audited and remains on path 4 because the quarter-res aggressive arm regressed the detector gate. | detector median_fps + render ms + path tag + content gates, interleaved on/off A/B on all three clips; x4/x8 no-regress single runs; no-screenshot smoke; pipeline suite subset check | rows 4-6 and 16-18 (x2 Sharp/Aggr) | ce343bb1402b3affb049b7de93ee9bfb314584ed | Holds only if x2 Sharp stays cleaner/faster than the revert path and aggressive x2 does not regress content or hitch gates while staying on the full-XY fallback. | pipeline filter PASS; smoke PASS on x2 Sharp 1327/1347/1446 and aggressive 1327/1347/1446 current builds; aggressive dead-end evidenced by path 4 fallback after the quarter-res route was gated off. Artifact paths: `.claude-state/profiling/20260611-item2a/x2-sharp-on-1327-v5/result.json`, `.claude-state/profiling/20260611-item2a/x2-sharp-off-1327-v5/result.json`, `.claude-state/profiling/20260611-item2a/x2-sharp-on-1347-v5/result.json`, `.claude-state/profiling/20260611-item2a/x2-sharp-off-1347-v5/result.json`, `.claude-state/profiling/20260611-item2a/x2-sharp-on-1446-v5/result.json`, `.claude-state/profiling/20260611-item2a/x2-sharp-off-1446-v5/result.json`, `.claude-state/profiling/20260611-item2a/x2-aggr-current-1327-on/result.json`, `.claude-state/profiling/20260611-item2a/x2-aggr-current-1327-off/result.json`, `.claude-state/profiling/20260611-item2a/x2-aggr-current-1347-on/result.json`, `.claude-state/profiling/20260611-item2a/x2-aggr-current-1446-on/result.json` | current smoke on/off median fps: x2 Sharp 1327 `14.1/13.5`, 1347 `12.5/13.5`, 1446 `14.5/13.9`; aggressive current PASS runs 1327 `13.0/14.1`, 1347 `13.2`, 1446 `13.5`. The malformed 1347 off-arm attempt was discarded. | DEAD-END for the aggressive inversion; KEEP for the x2 Sharp keeper | wb-5e98908e8af245c2 (item 2a revalidation), aggressive arm recorded as measurement dead-end |
