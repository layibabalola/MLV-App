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
| 4 | x2 Sharp | M16-1327 | 11.0 | 68.7 | 170 | content OK; span 40.8 SMOOTH (PASS) | 11.0 | baseline |
| 5 | x2 Sharp | M16-1347 | 11.0 | 65.4 | 154 | content OK (PASS) | 11.0 | baseline |
| 6 | x2 Sharp | M16-1446 | 10.9 | 70.3 | 155 | content OK (PASS) | 10.9 | baseline |
| 7 | x4 Sharp | M16-1327 | 13.0 | 47.1 | 146 | content OK; span 49.4 SMOOTH (PASS) | 13.0 | baseline |
| 8 | x4 Sharp | M16-1347 | 13.7 | 42.1 | 121 | content OK (PASS) | 13.7 | baseline |
| 9 | x4 Sharp | M16-1446 | 11.0 | 65.5 | 199 | content OK (PASS, hitch 0.63%) | 11.0 | baseline |
| 10 | x8 Sharp | M16-1327 | 13.0 | 49.4 | 158 | content OK; span 48.7 SMOOTH; x8 canary clean (PASS) | 13.0 | baseline |
| 11 | x8 Sharp | M16-1347 | 15.9 | 34.7 | 94 | content OK; span 63.3 SMOOTH; x8 canary clean (PASS) | 15.9 | baseline |
| 12 | x8 Sharp | M16-1446 | 13.0 | 47.5 | 153 | content OK; span 39.7 SMOOTH; x8 canary clean (PASS) | 13.0 | baseline |
| 13 | x1 Aggr | M16-1327 | 7.2 | 114.5 | 197 | content OK; span 41.3 SMOOTH (PASS) | 7.2 | baseline |
| 14 | x1 Aggr | M16-1347 | 7.2 | 114.3 | 215 | content OK (PASS) | 7.2 | baseline |
| 15 | x1 Aggr | M16-1446 | 7.2 | 123.7 | 245 | content OK (PASS) | 7.2 | baseline |
| 16 | x2 Aggr | M16-1327 | 12.3 | 58.4 | 135 | content OK; span 40.1 SMOOTH (PASS) | 12.3 | baseline |
| 17 | x2 Aggr | M16-1347 | 12.8 | 57.5 | 145 | content OK (PASS) | 12.8 | baseline |
| 18 | x2 Aggr | M16-1446 | 12.8 | 55.1 | 140 | content OK (PASS) | 12.8 | baseline |
| 19 | x4 Aggr | M16-1327 | 17.5 | 30.8 | 105 | content OK (249/257 hashes, benign dup presents, visual clean); span 40.7 SMOOTH (PASS) | 17.5 | baseline |
| 20 | x4 Aggr | M16-1347 | 13.2 | 43.8 | 149 | content OK (PASS) | 13.2 | baseline |
| 21 | x4 Aggr | M16-1446 | 13.9 | 38.0 | 109 | content OK (PASS) | 13.9 | baseline |
| 22 | x8 Aggr | M16-1327 | 13.0 | 49.2 | 138 | content OK; span 40.2 SMOOTH; x8 canary clean (PASS) | 13.0 | baseline |
| 23 | x8 Aggr | M16-1347 | 13.0 | 52.1 | 139 | content OK; span 64.7 SMOOTH; x8 canary clean (PASS) | 13.0 | baseline |
| 24 | x8 Aggr | M16-1446 | 13.3 | 43.1 | 108 | content OK; span 42.7 SMOOTH; x8 canary clean (PASS) | 13.3 | baseline |

**Lanes already at native rate: NONE.** Honest summary: every lane is below the ~23.976 native
target. x1 ~5-8 fps (compute-bound, render 102-168 ms); x2 ~11-13; x4 ~11-17.5; x8 ~13-16.
Worst-vs-native ranking (Sharp): x1 (~30% of native), x2 (~46%), x4/x8 (~50-66%).

Suite baseline for the TESTS gate: 16 pre-existing Qt-linked pipeline failures as of 2026-06-10
(pinned during the stuck-frame fix validation at this code state). Keepers must stay a strict
subset; re-confirm at the then-current SHA inside each code-change iteration.

Human spot-check: NOT yet performed for any lane (loop has made no change yet; baseline itself
needs no approval). The GUI bottom-left "Playback: N fps" was not captured by the harness this
sweep — collect it at the first human spot-check.

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

## ATTEMPTS log

One row per candidate, appended BEFORE measuring (cols 1-9), completed with the result after
(cols 10-12). Kill-categories: theory / measurement / baseline-comparison / attribution.

| Date | Lane | Change | Expected effect | Metric | Baseline row | Build SHA | Holds only if... | Gates run | Measured result | Verdict | Kill-category / commit |
|------|------|--------|-----------------|--------|--------------|-----------|------------------|-----------|-----------------|---------|------------------------|
| _(empty — Phase 0 made no candidate change)_ | | | | | | | | | | | |
