# Playback Improvement Plan — approved items, executor handoff

**How to use this doc (cost-saving execution model, user-directed 2026-06-11):** each item below is a
self-contained implementation spec meant to be executed by an inexpensive session (Claude Code with
`/model sonnet`, or Codex) with NO other context. Executor prompt per item:
"Implement item N of docs/playback-improvement-plan.md in C:\!Layi Wkspc\MLV-App, following its
Common Rules and Validation sections exactly. Return the diff and the validation tables."
One item per session/work block. A reviewer (the planning model or the user) checks the diff and the
gate tables before the work block is finalized.

**Status / provenance:** all four items were user-approved 2026-06-11 from the tradeoff menu in
[playback-max-optimization-loop.md](playback-max-optimization-loop.md) ("LOOP STOPPED" section —
read it for the standings, gates, and history). Item 1a's diagnosis is complete (iteration A1).

## Common Rules (every item)

- Work blocks: `pwsh -File tools\closeout\start-work-block.ps1 -RepoRoot . -Summary "<subject>"`
  before starting; `pwsh -File tools\closeout\work-block-complete.ps1 -RepoRoot . -Finalize` when
  validation passes. Commit on the work-block branch; never commit directly to master.
- Build: prepend `C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin` to PATH; build in
  `platform/qt/build-release` with `mingw32-make -j16`. Report the exe SHA256 after each build.
- Test clips: `C:\temp\MLV\M16-1327.MLV` (heavy dual-ISO), `M16-1347.MLV`, `M16-1446.MLV`.
- One MLVApp instance at a time; always `Stop-Process -Force` between runs; NEVER set
  `MLVAPP_AUTOPLAY_EXIT`; keep the app windowed.
- Measurement: fps only from no-screenshot traced runs via
  `tools\profiling\run-release-gui-smoke.ps1` with `-DetectPlaybackArtifacts` (fresh output dir per
  run — the per-day log appends); interleave A/B arms same-session; warm each clip once before
  citable rows (cold first runs read 30-40% low); single-clip deltas under ~20% are noise; a
  suspiciously fast result requires a content check (distinct hashes ~= presents).
- STANDING GATES for any keeper: `validate-visible-playback.ps1` span > 3 on changed lanes;
  detector verdict clean (frozen_content_runs=0, flicker 0, hitch_frac <= 5%); x8 canary
  filmstrips on ALL THREE clips viewed by the reviewer (pink/magenta wash, green dropout,
  half-frame discoloration, RGB-separated blocks = fail); pipeline suite
  (`tools\testing\run-windows-test.ps1 -Suite pipeline -ExePath
  tests\build-pipeline\release\pipeline_tests.exe`, build first via
  `.claude-state\scripts\build-pipeline-tests.ps1`) failure NAMES a subset of the 16-strong
  2026-06-10 baseline plus the known order-flake
  `ProcessingFilters.AggressiveX2PlaybackPreviewUsesQuarterresShadowsHighlights` (a new name must
  be adjudicated: isolation run + TU-order run + full-suite rerun before blaming the change).
- Ledger bookkeeping: append an ATTEMPTS row to playback-max-optimization-loop.md BEFORE measuring
  (date, lane, change, expected effect, metric, baseline row, build SHA, one "holds only if...");
  after the verdict fill in result + KEEP/DEAD-END (kill-category); keepers update the CURRENT
  column (never baseline) citing the work block.
- All new env switches read fresh (no static caching) unless noted; ASCII-only in any .ps1.
- Items 2-3 are the ONLY approved pixel-affecting changes and are strictly PLAYBACK-ONLY:
  pause/scrub/export must remain full quality. They end "pending user softness sign-off".

## Item 1a — Async Look Assist analysis (first-play stall fix; no pixel cost)

Diagnosis (complete, iteration A1): the residual Auto Look Assist auto_wb analysis blocks the UI
thread 683-901 ms at first Play (trace `look_assist.apply.unsettled` -> auto_wb completion); the
playback timer freezes, then catch-up drops 17-22 frames in one jump
(`timer_frame.playback_handled time_diff_ms=719/897`).

1. MAP first (read-only): in `platform/qt/MainWindow.cpp` find the `m_lookAssistAppliedReceipt`
   guard and the code that logs `look_assist.apply.unsettled` and runs the auto_wb analysis (grep
   `look_assist`, `auto_wb`, `lookAssist`). Determine: the expensive function, its INPUTS (does it
   render frames itself or analyze an existing buffer?), its OUTPUTS (which receipt/processing
   setters). Quote file:line in the report.
2. IMPLEMENT the safest matching pattern:
   - PREFERRED (if the compute analyzes capturable pixel data): copy the analysis input on the UI
     thread (one memcpy), run the pure-math analysis on a worker (`std::thread` detached or
     `QThread::create`; check MLVApp.pro for `QT += concurrent` before considering QtConcurrent),
     marshal results back via `QMetaObject::invokeMethod(this, [..]{..}, Qt::QueuedConnection)`.
     The worker touches ONLY its private copy — no mlvObject_t, no ui->, no processing object.
   - FALLBACK (if it must render): whole analysis on the worker with a lifetime guard — an atomic
     generation counter bumped on every clip open/close; the queued apply re-checks generation AND
     clip-still-open before applying. (Worker-side renders are precedented: the processed8 prefetch
     worker renders concurrently through the same internal locks.)
   - De-dupe semantics unchanged (dispatch where the synchronous call ran; the
     `m_lookAssistAppliedReceipt` guard still prevents re-runs). The APPLY (setter calls) runs only
     on the UI thread. The post-analysis "look pop" already exists today (post-decouple) — moving
     it later is not a new UX class.
   - KILL SWITCH: `MLVAPP_LOOK_ASSIST_SYNC=1` => original synchronous path unchanged.
   - Keep trace events: `look_assist.apply.unsettled` at dispatch; add
     `look_assist.apply.auto_wb_async_applied` in the queued apply.
3. VALIDATE:
   - Cold-stall gate: `.claude-state\scripts\cold-firstplay-capture.ps1 -Clip
     C:\temp\MLV\M16-1347.MLV -Scale 8 -Tag <tag>` and `-Clip ...M16-1446.MLV -Scale 4`. From each
     run's `logs\mlvapp-*.log`: max `time_diff_ms` in the first 8 s after the first draw must be
     < 250 (old build: 719/897); max gap between consecutive `present_content` events < 500 ms;
     the async-applied event fires exactly once per clip open.
   - Kill-switch check: one cold run with `MLVAPP_LOOK_ASSIST_SYNC=1` shows the old blocking
     pattern returns (max time_diff_ms > 500), proving the switch.
   - Steady-state: one traced smoke x2 Sharp M16-1327 (15 s) — verdict PASS, hashes ~= presents.
   - Spans x8 on all three clips + canary PNGs for the reviewer. Full suite per Common Rules.

## Item 1b — First-play horizontal tearing (repro first; no pixel cost)

Status: buffer-level race RULED OUT (FrameSlots are pin-protected while presenting,
RenderFrameThread.cpp ~261-309; the zero-copy QImage lifetime is guarded via
`displayImageOwnsData=false` + `releasePresentedFrameForRequestSerial`). Remaining hypothesis:
QGraphicsView minimal-viewport partial update across a DWM frame on maximal content changes.

1. REPRO first (do not fix blind): extend `cold-firstplay-capture.ps1` with `-IntervalMs 50` and
   start captures at Play (autoplay settle 200 ms), covering the FIRST 500-1500 ms. Heuristic: a
   torn capture's top band matches capture N while its bottom band matches N+1 (row-wise diff
   against both neighbors; seam = sharp transition row). Flag candidates for the reviewer's eyes.
   If 3 cold attempts produce no confirmed tear, record DEAD END (kill-category: measurement —
   not reproducible under instrument) and move on; the user sees it interactively, so also try
   scale x4/x8 on a fresh-copied clip with the GUI visible and uncovered.
2. FIX CANDIDATE (only after a confirmed repro): set the playback view to
   `QGraphicsView::FullViewportUpdate` while playing (restore prior mode on stop) in
   MainWindow.cpp where Play starts/stops. Gate: repro disappears across 3 cold attempts; traced
   x2/x4/x8 fps unchanged within noise; standing gates.

## Item 2 — x2 package (user-approved playback-only quality trade)

2a. QUARTER-RES X2 PLAYBACK PREVIEW. Do not design from scratch — resurrect the reverted
implementation: `git show af40fe88` and `git show 0123fef9` (later reverted by restoring files to
75acb18c; the revert's basis was a tainted comparison — see the ledger audit). Those commits
contain: `mlv_quarterres_x2_preview_enabled()` (env-gated), `mlv_rgb_u16_upscale_to_size()`
(edge-clamped bilinear), `mlv_render_scaled_rgb16_x2_quarter_preview_core()` (decode ->
`pl_downsample_bayer_to_bayer_4x` -> recon at quarter -> debayer -> upscale, reusing the v3
16-aligned Y-crop so the dual-ISO 4-row phase is preserved), path tag
`g_mlv_phase4bv2_path_taken = 5`, and tests `Phase4B_DualIsoScaleTwoQuarterRes*` in
`tests/pipeline/test_dual_iso_pipeline.cpp`. Changes vs the old version: default ON for x2
PLAYBACK preview in BOTH quality modes (user approval covers it); keep
`MLVAPP_DISABLE_QUARTERRES_X2_PREVIEW` as the kill switch; confirm the path engages only under
playback preview mode (pause/scrub/export untouched — verify by reading the gate call sites).
Expected: x2 render 45-70 -> ~15-25 ms; x2 fps -> ~native 24. A/B: quarter-res ON vs kill-switch
OFF, interleaved, 2 reps, all three clips, x2 both modes + x4/x8 no-regress single runs.

2b. AGGRESSIVE-X2 INVERSION. Aggressive x2 currently renders SLOWER than Sharp (59-69 vs 45-50 ms)
because `mlvPlaybackAggressivePreviewMode()` routes x2 to the expensive early full-XY/HQ mean23
path (`video_mlv.c` x2 handler, ~3701-3709 region). With 2a default-on for playback in both modes,
aggressive x2 should take the quarter-res path too — verify path tag 5 engages under aggressive,
then re-measure: aggressive x2 must be >= Sharp x2 fps (inversion dead). If the old aggressive
mapping still overrides, remap aggressive x2 to the quarter-res path explicitly. Update the
aggressive-x2 path-tag tests (the reverted commits show their shapes).

## Item 3 — x1 half-res playback proxy (user-approved quality trade)

New x1 playback-preview path mirroring the quarter-res pattern one octave up: at scale==1 +
playback preview mode + not disabled: downsample bayer 2x -> dual-ISO recon at half-res -> debayer
-> bilinear upscale 2x to full (`mlv_rgb_u16_upscale_to_size` from item 2a) -> new path tag (6).
Check for an existing 2x bayer downsample helper next to `pl_downsample_bayer_to_bayer_4x`; write
the 2x variant if absent (same averaging scheme, preserve the dual-ISO row phase using the same
16-aligned crop approach the quarter-res core used — that pattern was validated canary-safe).
Kill switch `MLVAPP_DISABLE_HALFRES_X1_PREVIEW`. x1 prefetch stays OFF (unchanged gates).
Pause/scrub/export untouched (playback-preview-mode gate only). Expected: x1 ~8 -> ~13-15 fps.
Tests: clone the quarter-res test shapes for x1 (path 6 engages under playback preview, defaults
off outside playback, kill switch wins). A/B interleaved on/off, 2 reps, all three clips, x1 both
quality modes; x2/x4/x8 no-regress singles; full standing gates.

## Item 4 — fps readout smoothing (cosmetic)

In `MainWindow.cpp` `timerFrameEvent`, the bottom-left text uses near-instantaneous
`measuredFrameMs` (the block computing `measuredFps` ~line 1571-1584). Replace the displayed value
with a ~1 s rolling average (small ring buffer of recent present intervals or an EMA), updating the
text at most a few times per second. ONLY the displayed text changes — do not touch trace/telemetry
fields. Note: the smoke harness's `gui_fps_status_value` parses this text, so before/after
comparisons of that one field shift semantics (acceptable, cosmetic). Validate: one traced smoke
run per scale (2/4/8) PASS + eyeball that the number reads steady; standing gates are satisfied by
the no-pixel nature (suite + one span run suffice).

## Reviewer checklist (per item, before finalize)

1. Read the full diff — reject scope creep beyond the item's files/shape.
2. Check the validation tables against the gate thresholds above.
3. View the x8 canary PNGs (all three clips) yourself for items touching render paths (2, 3).
4. Confirm the ledger ATTEMPTS row + CURRENT updates are written and honest (machine state noted).
5. Finalize the work block; for items 2-3 flag the lane "pending user softness sign-off".
