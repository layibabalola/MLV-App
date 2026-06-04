# New Session Handoff - 2026-06-04

## Objective

Continue MLV App playback-FPS work using the new pipeline-resolution model before making more source changes.

The next session should not start with another isolated hotspot tweak. First build a clear model of where each playback mode pays full-resolution work versus preview-resolution work, then use that model to rank the next performance plan.

## Current State

- Repo root: `C:\!Layi Wkspc\MLV-App`
- Expected starting branch after closeout: `master`
- User-facing release exe: `platform\qt\build-release\release\MLVApp.exe`
- Durable context:
  - `.claude/analysis/mlv-playback-investigation.md`
  - `.claude/ANALYSIS_LOG.md`
  - `docs/diagrams/frame-pipeline.md`
  - `docs/14-performance-benchmarking.md`

## Key Playback Breakthrough To Preserve

The important recent playback-FPS breakthrough was not merely adding x8 preview. It was moving x8 reduction earlier:

`full raw decode -> x8 Bayer-domain reduction -> LLRawProc/Dual ISO on reduced Bayer -> reduced-frame debayer -> processing/output`

This is the Premiere/NLE-style lesson: preview resolution must become an upstream pipeline contract. If x8 preview still pays full-resolution Dual ISO/debayer, the scale is too late.

Evidence from `.claude-state/profiling/20260603-playback-scale-x8-early/scale8-early-hqmean23-enabled/M16-1327-30s.json`:

- `phase4b_path=8` / `phase4b_path_label=x8-full-xy-pre-recon` on all 422 presented frame telemetry lines.
- `GUI FPS=76.0`
- `smoke presented FPS=13.250`
- `timeline FPS=23.360`
- `avg_llrawproc_dual_iso_ms=11.126` (`89.879 FPS-equivalent`)
- `avg_debayered_frame_ms=1.175` (`851.064 FPS-equivalent`)

The later first-frame pink-block work was presentation hygiene, not the main playback-FPS breakthrough.

## New Conceptual Model

The next performance pass should produce a table with one row per major playback stage:

| Stage | Domain | x1 resolution | x2 resolution | x4 resolution | x8 resolution | Pixel budget | Telemetry fields | Can skip/approx/cache/preview-res? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Raw read/decode | raw Bayer | source | source today | source today | source today | still full-res today | `raw_uint16_*` | next frontier: decode-aware/tile-aware preview |
| Bayer reduction | raw Bayer | none | mode-dependent | mode-dependent | early x8 compatible path | should drop by scale squared | `phase4b_path`, scale fields | yes |
| LLRawProc/Dual ISO | reconstructed Bayer | source | verify | verify | reduced on early x8 | must reveal late-scale mistakes | `llrawproc_*`, `dual_iso_*` | sometimes preview-res |
| Debayer | RGB from Bayer | source | verify | verify | reduced on early x8 | must scale with reduced dimensions | `debayered_frame_ms` | preview-res where gated |
| Processing16/8 | processed RGB | source | reduced when scale active | reduced when scale active | reduced | scales with processed pixels | `processed16_ms`, `processed8_ms`, processing buckets | subset/cache/preview-res |
| Presentation/upload | display RGB | viewport | viewport | viewport | viewport | separate from render cost | draw/presentation telemetry | cache/prescale |

The model should be generated from code/docs plus profile JSON. Do not rely only on intuition.

## Standing Questions For Every Preview Mode

- What data domain is this stage operating in: raw Bayer, reconstructed Bayer, RGB, or processed RGB?
- What width/height and pixel count does this stage actually process?
- Is this stage still paying full-resolution work after the user chose a reduced preview scale?
- Can this stage be skipped, approximated, cached, or run at preview resolution?
- If this were Premiere/Resolve, where would the proxy/resolution ladder enter the pipeline?

## Ranked Next Work

1. High impact / medium effort: add a durable stage/domain/resolution/pixel-budget table to docs and/or profiling output, then make profile artifacts report the active resolution per stage.
2. High impact / high effort: investigate decode-aware or tile-aware reduced preview so x8 does not always pay full raw decode before reduction.
3. High impact / medium effort: apply the early-resolution contract more consistently to x2/x4 performance preview modes, with explicit compatibility gates and fallback telemetry.
4. Medium impact / low effort: show active scale/fallback reason in GUI smoke/profile output so "requested x8 but paid full-res" is obvious.
5. Medium impact / medium effort: define separate user-facing modes for sharp non-blocky smooth playback versus coarse deep preview.

## Validation Rules

- Keep FPS labels explicit:
  - `GUI FPS` is the bottom-left `Playback: ... fps` status value.
  - `smoke presented FPS` is `presented_fps`.
  - `timeline FPS` is `timeline_fps`.
  - per-stage `1000 / ms` values are only `FPS-equivalent`.
- Use the standard M16 visual smoke set for GUI-facing playback changes:
  - `C:\temp\MLV\M16-1327.MLV`
  - `C:\temp\MLV\M16-1347.MLV`
  - `C:\temp\MLV\M16-1446.MLV`
  - optional control: `C:\temp\MLV\M16-1243.MLV`
- Prefer presented-frame screenshots from `tools\profiling\run-release-gui-smoke.ps1` over viewport screenshots.
- Rebuild the user-facing release tree after GUI/playback/source changes:
  - `pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$env:PATH='C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;' + $env:PATH; & 'C:\Qt\Tools\mingw1310_64\bin\mingw32-make.exe' -C platform\qt\build-release -B release -j4"`

## Paste Prompt For New Session

```text
Read AGENTS.md first, then read .claude/analysis/mlv-playback-investigation.md and .claude/analysis/new-session-handoff-2026-05-31.md.

Goal: implement a playback performance improvement plan using the new pipeline-resolution model, then start with the highest-confidence improvement that falls out of that model.

Do not begin with an isolated hotspot tweak. First build a stage-by-stage table for playback modes x1/x2/x4/x8 with:
- data domain at each stage: raw Bayer, reconstructed Bayer, RGB, processed RGB
- active resolution and pixel count at each stage
- pixel budget versus source resolution
- current telemetry fields that prove the stage cost
- whether the stage can be skipped, approximated, cached, or run at preview resolution

Use the recent breakthrough as the anchor: the early x8 path improved FPS because it moved Bayer-domain reduction before LLRawProc/Dual ISO and before debayer. The core heuristic is: if x8 preview still pays full-res Dual ISO/debayer, the scale is too late.

After the model is written, produce a ranked performance plan. Prioritize:
1. decode-aware or tile-aware reduced preview if the model shows x8 still pays full raw decode,
2. applying the early-resolution contract more consistently to x2/x4 modes if those still pay full-res stages,
3. telemetry/profile output that exposes requested scale, active scale, fallback reason, and per-stage resolution,
4. separate user-facing modes for sharp non-blocky smooth playback versus coarse deep preview.

Implementation expectations:
- Keep edits scoped.
- Use .claude-state/ for scratch artifacts.
- Update durable notes under the existing .claude/analysis files.
- For GUI/playback/source changes, rebuild platform/qt/build-release/release/MLVApp.exe and report LastWriteTime, Length, and SHA256.
- Validate with focused tests plus screenshot-backed GUI smoke where applicable.
- Keep FPS labels explicit: GUI FPS, smoke presented FPS, timeline FPS, and per-stage FPS-equivalent.
- Before final response, run brokered closeout.

Start by inspecting src/mlv/video_mlv.c, src/mlv/video_mlv.h, src/processing/playback_downsample.[ch], platform/qt/RenderFrameThread.cpp, platform/qt/MainWindow.cpp, docs/diagrams/frame-pipeline.md, and docs/14-performance-benchmarking.md. Then implement the model and use it to choose the next change.
```

## 2026-06-04 Addendum - Model Implemented As Telemetry Contract

- Claude Desktop and Codex independently converged on the same playback-resolution model:
  - x2 is late scale: full raw decode and full LLRawProc/Dual ISO, then reduced RGB/processing.
  - x4 has early paths, but default HQ/Auto mean23 can still use full-recon fallback.
  - x8 path 8 moves Bayer reduction before LLRawProc/debayer, but still pays full raw decode.
- The first implemented step is telemetry/profile output, not a decoder rewrite:
  - `render_thread_phase4b_fallback_reason`
  - `render_thread_stage_*_{domain,width,height,pixels,pixel_retention_ratio,preview_resolution}`
- Use these fields before the next optimization so profiles can prove whether a requested scale paid full raw decode, full LLRawProc/Dual ISO, or preview-resolution work.
- Validation completed in the same work block:
  - Phase4B focused pipeline tests passed: `tests=139`, `assertions=510`, `failed=0`.
  - App-backed profile JSON test passed: `ClipGolden.LargeDualIsoHqScaleFourSuppressesRawUint16Prefetch`.
  - Release x8 profile for `C:\temp\MLV\M16-1327.MLV` showed `raw_decode_pixels=4100544`, `llrawproc_pixels=63280`, `processing_pixels=63958`, proving x8 still pays full raw decode while later stages run near preview resolution.
  - Screenshot-backed x8 GUI smoke passed: `GUI FPS=11.0`, `smoke presented FPS=13.393`, `timeline FPS=22.487`, screenshot SHA256 `8BB082054A3604DFBBC4C6304B18618B57362715B3A58B94CF3698D70072C5A8`.
  - Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 01:17:02`, `Length=9031680`, SHA256 `C2262AB2C49AC7A09C36BD6ADCA2702449B48DA385D9D4C7633EF3EA20192978`.

## 2026-06-04 Addendum - Preview Modes Ship The First Model-Driven Improvement

- The next implementation introduced two explicit user-facing playback preview policies:
  - `Sharp / Smooth Preview` is the default. It keeps the conservative x4 HQ mean23 full-recon fallback for smoother, less blocky playback quality.
  - `Aggressive Performance Preview` is opt-in. It lets x4 HQ mean23 use the existing early Phase 4B path where gates allow, and re-enables raw uint16 decode-ahead for Dual ISO x8 because x8 now reconstructs/debayers at preview resolution.
- New controls and proof fields:
  - UI menu: Playback Quality -> Preview Mode.
  - Settings key: `Playback/PreviewMode`.
  - Env overrides: `MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW`, `MLVAPP_PLAYBACK_PREVIEW_MODE`.
  - Profile fields: `playback_preview_mode`, `playback_aggressive_preview`, `render_thread_preview_mode`, `render_thread_aggressive_preview`.
- Validation artifacts:
  - x4 sharp profile: `.claude-state\profiling\20260604-preview-modes\m16-1327-x4-sharp.json` shows `phase4b_path=0`, fallback `HQ mean23 playback uses full-recon x4 fallback`, and `llrawproc_pixels=4100544`.
  - x8 aggressive profile: `.claude-state\profiling\20260604-preview-modes\m16-1327-x8-aggressive.json` shows `phase4b_path=8`, `fallback_reason=none`, `llrawproc_pixels=63280`, `processing_pixels=63958`, and raw prefetch hits on 9 of 10 frames.
  - x8 aggressive GUI smoke: `.claude-state\profiling\20260604-preview-modes\m16-1327-x8-aggressive-smoke.json`, `validation.ok=true`, bottom-left `GUI FPS=17.0`, `smoke presented FPS=16.039`, `timeline FPS=22.624`.
  - Stage timings from the smoke: `avg_raw_uint16_ms=4.632` (`215.89 FPS-equivalent`), `avg_llrawproc_total_ms=3.484` (`287.03 FPS-equivalent`), `avg_debayered_frame_ms=0.726` (`1377.41 FPS-equivalent`), `avg_processing_ms=3.747` (`266.88 FPS-equivalent`), `avg_playback_scale_ms=4.295` (`232.83 FPS-equivalent`), `avg_render_total_ms=26.263` (`38.08 FPS-equivalent`), `avg_draw_total_ms=37.768` (`26.48 FPS-equivalent`).
  - Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 02:26:40 -05:00`, `Length=9042944`, SHA256 `8A2F64C4814275B025E95BB808086D245344AF63C22879632CD7C5248005D11B`.
- Current hardness summary:
  - x8 is the most complete early-resolution mode: full raw decode still happens, but reduction now precedes LLRawProc/Dual ISO and debayer.
  - x4 is policy-split: sharp/smooth intentionally keeps full-recon fallback for default HQ mean23; aggressive exposes the faster early path behind an explicit user choice.
  - x2 is still late/full-recon and should be treated as less hardened for fastest playback until a real x2 Bayer-to-Bayer early path exists.
- Next session should compare x4 sharp/aggressive and x8 sharp/aggressive across the standard M16 clips before making aggressive behavior automatic or starting the heavier decode-aware/tile-aware x8 work.

## 2026-06-04 Addendum - Aggressive x4 Decode Overlap

- The next measured bottleneck was compatible x4 aggressive foreground raw decode:
  - Before patch: `.claude-state\profiling\20260604-next-preview-bottleneck-env\m16-1327-x4-aggressive.json`, `phase4b_path=3`, `raw_prefetch_hits=0`, `avg_raw_uint16_ms=16.455` (`60.77 FPS-equivalent`), `avg_render_total_ms=38.545` (`25.94 FPS-equivalent`).
  - After patch: `.claude-state\profiling\20260604-x4-prefetch-patch\m16-1327-x4-aggressive-after2.json`, `phase4b_path=3`, `raw_prefetch_hits=19/20`, `avg_raw_uint16_ms=1.316` (`759.88 FPS-equivalent`), `avg_render_total_ms=23.263` (`42.99 FPS-equivalent`).
- The implementation keeps the conservative prefetch block for full-recon fallback:
  - x4 sharp after patch: `.claude-state\profiling\20260604-x4-prefetch-patch\m16-1327-x4-sharp-after.json`, `phase4b_path=0`, fallback `HQ mean23 playback uses full-recon x4 fallback`, `raw_prefetch_hits=0`, `avg_llrawproc_total_ms=29.545` (`33.85 FPS-equivalent`).
  - App-backed fixture tests also prove aggressive x4/x8 receipts with `focus_pixels` fall back to full-recon and keep raw prefetch off.
- Screenshot-backed x4 aggressive GUI smoke passed:
  - Artifact: `.claude-state\profiling\20260604-x4-prefetch-patch\m16-1327-x4-aggressive-smoke.json`.
  - Bottom-left `GUI FPS=8.6`, `smoke presented FPS=13.144`, `timeline FPS=21.855`.
  - CPU summary: `avg_raw_uint16_ms=5.256` (`190.26 FPS-equivalent`), `avg_llrawproc_total_ms=5.581` (`179.18 FPS-equivalent`), `avg_debayered_frame_ms=0.860` (`1162.79 FPS-equivalent`), `avg_processing_ms=10.151` (`98.51 FPS-equivalent`), `avg_playback_scale_ms=4.070` (`245.70 FPS-equivalent`), `avg_render_total_ms=35.128` (`28.47 FPS-equivalent`), `avg_draw_total_ms=42.279` (`23.65 FPS-equivalent`), `raw_prefetch_hits=63`.
  - Presented screenshot SHA256 `C0DD7F113B935A9E97F987D678E806DE008A340DDFB9067C9BB698842F6919B0`; aspect evidence remains presented-playback stretch with `stretch_x=3.0`, `stretch_y=1.0`, `h_stretch_index=0`, `v_stretch_index=3`.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 02:47:00 -05:00`, `Length=9043456`, SHA256 `2227C07718CA3CB4DEAC43565531C000FEBB4C6E0E4C5C01FF967043B764B8AB`.

## 2026-06-04 Addendum - Auto Aggressive Uses x8 On Cadence Miss

- Multi-clip aggressive profiling across `M16-1327`, `M16-1347`, and `M16-1446` made the next policy choice clear:
  - x4 aggressive average: `GUI FPS=19.000`, `smoke presented FPS=13.067`, `timeline FPS=21.686`, `avg_render_total_ms=37.892` (`26.39 FPS-equivalent`), `avg_draw_total_ms=38.816` (`25.76 FPS-equivalent`).
  - x8 aggressive average: `GUI FPS=24.333`, `smoke presented FPS=15.615`, `timeline FPS=22.091`, `avg_render_total_ms=25.893` (`38.62 FPS-equivalent`), `avg_draw_total_ms=35.166` (`28.44 FPS-equivalent`).
- Implemented the policy change in `PlaybackQualityAutoSampler`:
  - Sharp/Smooth Auto cadence misses now fall back to fixed Fast's x4/no-HQ contract.
  - Aggressive Auto cadence misses now choose HQ x8, preserving the early Bayer-domain x8 path before LLRawProc/Dual ISO and debayer.
- Focused console validation passed: `console_tests.exe --check-golden`, `tests=90`, `assertions=320`, `skipped=29`, `failed=0`; new coverage includes `PlaybackQualityAutoSampler.AggressiveCadenceMissUsesHqx8`.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 03:14:36 -05:00`, `Length=9043456`, SHA256 `23C4F48E0718C5E4AEF13380EE105C83BBA20D1A724957F4E5732345D4007C6E`.
- Remaining validation gap: the release GUI smoke harness can force scale but not playback quality mode. A settings-based Auto/aggressive smoke attempt hit a pre-playback `0xC0000005` crash; user settings were restored and HQ/x2 smoke ran normally afterward. Add a first-class quality-mode smoke override before claiming screenshot-backed Auto switching coverage.

## 2026-06-04 Addendum - Auto Validation Gap Closed

- Added harness/dev overrides that do not mutate user settings:
  - `MLVAPP_PLAYBACK_QUALITY_MODE=auto` (or `fast`, `hq`, `phase3_fast`, `phase3_hq`) selects playback quality mode.
  - `MLVAPP_PLAYBACK_SCALE_FACTOR=auto` bypasses a persisted GUI scale override and lets the active quality policy drive the request scale.
  - `tools\profiling\run-release-gui-smoke.ps1` now has `-QualityMode` and `-ExpectedVisualScaleRequest`, so Auto can validate pre-playback visual state at x4 while expecting final scale x8.
- Moved Auto cadence sampling from `timerFrameEvent()` to actual presented-frame intervals in `finishPresentedFrame()`. This matters because the timer loop can report optimistic GUI-loop values like `500-1000 fps`; the policy now reacts to the same presented cadence used by smoke/profile telemetry.
- Screenshot-backed Auto aggressive smoke passed:
  - Artifact: `.claude-state\profiling\20260604-auto-quality-mode-override\M16-1327-auto-aggressive-presented-cadence.json`.
  - Starts x4, ends x8: `scaleRequestStart=4`, `scaleRequestLast=8`, `scaleActiveLast=8`.
  - Bottom-left `GUI FPS=11.0`, `smoke presented FPS=17.176`, `timeline FPS=23.050`.
  - Timing: `avg_render_total_ms=26.955` (`37.10 FPS-equivalent`), `avg_draw_total_ms=31.858` (`31.39 FPS-equivalent`), `avg_raw_uint16_ms=4.574` (`218.63 FPS-equivalent`), `avg_llrawproc_total_ms=4.071` (`245.64 FPS-equivalent`), `avg_debayered_frame_ms=0.761` (`1314.06 FPS-equivalent`), `avg_processing_ms=4.968` (`201.29 FPS-equivalent`), `avg_playback_scale_ms=4.155` (`240.67 FPS-equivalent`).
  - Presented screenshot SHA256 `9EB3C491568F8032E3EE66657005CF15B0CFC2D2BB7691702501199D22500A32`; FPS crop SHA256 `BBE62961D32DDBDA018B96B19EF68DDA3D947E03E7E4A4576892DD81AC69E55F`.
- Screenshot-backed Auto sharp/smooth smoke passed:
  - Artifact: `.claude-state\profiling\20260604-auto-quality-mode-override\M16-1327-auto-sharp-presented-cadence.json`.
  - Stays x4: `scaleRequestStart=4`, `scaleRequestLast=4`, `scaleActiveLast=4`.
  - Bottom-left `GUI FPS=12.0`, `smoke presented FPS=9.030`, `timeline FPS=22.119`.
  - Timing: `avg_render_total_ms=143.079` (`6.99 FPS-equivalent`), `avg_draw_total_ms=27.562` (`36.28 FPS-equivalent`), `avg_raw_uint16_ms=13.438` (`74.42 FPS-equivalent`), `avg_llrawproc_total_ms=37.595` (`26.60 FPS-equivalent`), `avg_debayered_frame_ms=51.854` (`19.29 FPS-equivalent`), `avg_processing_ms=10.933` (`91.47 FPS-equivalent`), `avg_playback_scale_ms=16.787` (`59.57 FPS-equivalent`).
  - Presented screenshot SHA256 `8F74741F9CC6A034471638B4AC64FFFF224753978B62A9C37FCDB3E65A99ABFF`; FPS crop SHA256 `D16D4461A3CF10E5A9185FC2C10C46EB83594047B890D7AD54A181AC37CAEDF1`.
- Focused console validation passed after the parser additions: `console_tests.exe --check-golden`, `tests=92`, `assertions=336`, `skipped=29`, `failed=0`.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 03:53:28 -05:00`, `Length=9047040`, SHA256 `8D282B595AF7733FFA9B07E950F4EE6A2741C6EAED8836BDD9BD89270253D549`.
- Next session should extend this Auto sharp/aggressive comparison to `M16-1347` and `M16-1446`, then choose the next source change from the stage model. On `M16-1327`, aggressive x8 cut render work from `143.079 ms` (`6.99 FPS-equivalent`) to `26.955 ms` (`37.10 FPS-equivalent`), but the remaining gap to user-visible presented FPS means presentation cadence / queued presentation replacement should be measured before starting a heavier decode-aware raw decoder rewrite.

## 2026-06-04 Addendum - Multi-Clip Auto Aggregate Points To Prep/Presentation Cadence

- Auto sharp/aggressive comparison is now extended across `M16-1327`, `M16-1347`, and `M16-1446`.
- Multi-clip Auto aggressive average:
  - `smoke presented FPS=15.670`, `timeline FPS=22.686`.
  - `avg_present_interval_ms=60.481` (`16.53 FPS-equivalent`), `avg_render_total_ms=29.875` (`33.47 FPS-equivalent`), `avg_draw_total_ms=33.380` (`29.96 FPS-equivalent`).
  - Stage averages: `avg_raw_uint16_ms=6.247` (`160.09 FPS-equivalent`), `avg_llrawproc_total_ms=4.557` (`219.43 FPS-equivalent`), `avg_debayered_frame_ms=0.863` (`1158.30 FPS-equivalent`), `avg_processing_ms=5.270` (`189.74 FPS-equivalent`), `avg_processed16_ms=19.858` (`50.36 FPS-equivalent`), `avg_processed8_ms=23.648` (`42.29 FPS-equivalent`), `avg_playback_scale_ms=4.189` (`238.72 FPS-equivalent`).
- Multi-clip Auto sharp/smooth average:
  - `smoke presented FPS=9.108`, `timeline FPS=22.085`.
  - `avg_present_interval_ms=100.892` (`9.91 FPS-equivalent`), `avg_render_total_ms=124.196` (`8.05 FPS-equivalent`), `avg_draw_total_ms=33.294` (`30.04 FPS-equivalent`).
  - Stage averages: `avg_raw_uint16_ms=12.919` (`77.41 FPS-equivalent`), `avg_llrawproc_total_ms=31.537` (`31.71 FPS-equivalent`), `avg_debayered_frame_ms=45.801` (`21.83 FPS-equivalent`), `avg_processing_ms=11.909` (`83.97 FPS-equivalent`), `avg_processed16_ms=61.614` (`16.23 FPS-equivalent`), `avg_processed8_ms=65.865` (`15.18 FPS-equivalent`), `avg_playback_scale_ms=17.198` (`58.15 FPS-equivalent`).
- Implemented the next telemetry split in `MainWindow`:
  - Per-frame/profile fields: `playback_prep_pre_enqueue_ms`, `playback_prep_worker_queue_ms`, `playback_prep_worker_build_ms`, `playback_prep_worker_total_ms`, `playback_prep_result_queue_ms`, `playback_prep_elapsed_before_present_ms`, `playback_prep_total_before_finish_ms`.
  - Smoke-summary averages: `avg_playback_prep_*`.
- Validation on rebuilt release exe:
  - `.claude-state\profiling\20260604-playback-prep-telemetry\M16-1327-auto-aggressive-prep-telemetry-rerun.json`, validation `ok=true`.
  - Bottom-left `GUI FPS=58.0` was noisy; stable run metrics were `smoke presented FPS=16.113`, `timeline FPS=23.003`.
  - `avg_render_total_ms=34.317` (`29.14 FPS-equivalent`), `avg_draw_total_ms=32.290` (`30.97 FPS-equivalent`).
  - Prep split: `avg_playback_prep_worker_build_ms=0.021` (`47619.05 FPS-equivalent`), `avg_playback_prep_result_queue_ms=22.000` (`45.45 FPS-equivalent`), `avg_playback_prep_total_before_finish_ms=30.676` (`32.60 FPS-equivalent`).
  - Presented screenshot SHA256 `9EB3C491568F8032E3EE66657005CF15B0CFC2D2BB7691702501199D22500A32`; FPS crop SHA256 `EAB33F97E7E0F9DD7F9A0B37F1960F0777B95A6D55DA75F3588661F9FB826A65`.
- Ranked next target:
  - First investigate and reduce result-queue/presentation cadence: `playback_prep_result_queue_ms`, `prep_replaced_after`, UI-thread queued signal latency, and render-slot release timing.
  - Keep decode-aware/tile-aware x8 as the larger frontier, but do not start the decoder rewrite until the prep/presentation queue is explained.

## 2026-06-04 Addendum - Inline Prep Presentation Removes Queue Delay

- Implemented the next telemetry-proven scheduler/presentation fix:
  - Pre-scaled fast playback frames (`playbackScaledImage8`) now build and present their lightweight `PlaybackPrepResult` inline in `MainWindow::drawFrameReady()`.
  - The gate is narrow: playback active, fast-scale active, zoom-fit, no transform, no zebras, no GPU presentation, no display-preview cache, and valid playback-scaled dimensions.
  - Added `playback_prep_inline_present` per frame and `playback_prep_inline_present_frames` in smoke summaries.
- Why this was the right first edit after the model:
  - Prior telemetry showed prep worker build at only `0.021 ms` (`47619.05 FPS-equivalent`) but prep result queue at `22.000 ms` (`45.45 FPS-equivalent`) and prep total at `30.676 ms` (`32.60 FPS-equivalent`).
  - This removes measured scheduling latency after the render thread already produced a scaled frame; it does not change raw decode, LLRawProc/Dual ISO, debayer, or Bayer-domain reduction.
- Screenshot-backed validation on the rebuilt release exe:
  - `M16-1327` Auto aggressive: `.claude-state\profiling\20260604-inline-prep-presentation\M16-1327-auto-aggressive-inline-prep-expected4.json`, validation `ok=true`.
    - Auto now stayed sharper at x4 (`scaleRequestStart=4`, `scaleRequestLast=4`, `scaleActiveLast=4`) instead of dropping to active x8 under prep queue pressure.
    - Bottom-left `GUI FPS=15.0` in summary and `100.0` in screenshot-time crop, `smoke presented FPS=26.352`, `timeline FPS=22.663`.
    - `avg_present_interval_ms=35.526` (`28.15 FPS-equivalent`), `avg_render_total_ms=35.476` (`28.19 FPS-equivalent`), `avg_draw_total_ms=10.688` (`93.56 FPS-equivalent`).
    - Prep result queue is `0.000 ms` (removed; FPS-equivalent not meaningful), prep total `9.320 ms` (`107.30 FPS-equivalent`), inline frames `250`, stale/replaced prep drops `0/0`.
  - `M16-1327` explicit x8 aggressive: `.claude-state\profiling\20260604-inline-prep-presentation\M16-1327-scale8-aggressive-inline-prep.json`, validation `ok=true`.
    - Bottom-left `GUI FPS=35.0` in summary and `27.0` in screenshot-time crop, `smoke presented FPS=25.112`, `timeline FPS=22.919`.
    - `avg_present_interval_ms=37.811` (`26.45 FPS-equivalent`), `avg_render_total_ms=35.795` (`27.94 FPS-equivalent`), `avg_draw_total_ms=10.900` (`91.74 FPS-equivalent`), prep total `9.258 ms` (`108.01 FPS-equivalent`), inline frames `229`.
  - `M16-1347` Auto aggressive: `.claude-state\profiling\20260604-inline-prep-presentation\M16-1347-auto-aggressive-inline-prep.json`, validation `ok=true`.
    - Bottom-left `GUI FPS=30.0` in summary and `14.0` in screenshot-time crop, `smoke presented FPS=22.832`, `timeline FPS=22.215`.
    - `avg_present_interval_ms=40.186` (`24.88 FPS-equivalent`), `avg_render_total_ms=40.495` (`24.69 FPS-equivalent`), `avg_draw_total_ms=10.716` (`93.32 FPS-equivalent`), prep total `9.059 ms` (`110.39 FPS-equivalent`), inline frames `222`.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 04:26:19 -05:00`, `Length=9053696`, SHA256 `A826C66A608788A259264558BE72CA0530E3447103566B923777851D8F8AB752`.
- Next session:
  - Run the same Auto aggressive smoke on `M16-1446`, then aggregate the new post-queue multi-clip Auto aggressive average against the previous `15.670` smoke-presented FPS baseline.
  - Choose the next change from the new post-queue profile. Likely candidates are remaining render work around `35-40 ms` (`24.69-28.19 FPS-equivalent`), processing/shadows-highlights prep, and the still-full raw decode floor for explicit x8.

## 2026-06-04 Addendum - Aggressive Odd-Height Shadows/Highlights Preview

- Implemented the next model-selected processing change:
  - Aggressive playback preview now permits the half-res shadows/highlights RGB blur on even-width, odd-height preview frames; it processes the even region and fills the trailing output row from the previous generated row.
  - Sharp/smooth preview keeps the conservative full-res RGB blur path for odd heights.
  - The aggressive TLS state is restored across render-thread, processed8 direct, and processed8 prefetch paths.
- Focused validation passed:
  - Rebuilt `tests\build-ci-pipeline`.
  - `pipeline_tests.exe --gtest_filter=ProcessingFilters.*`, `tests=142`, `assertions=30`, `failed=0`.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 04:53:48 -05:00`, `Length=9054208`, SHA256 `45B8DF86B60759B89D44B5FF20DCE176AD298BEE38725B07A766948B57EA76DA`.
- Screenshot-backed explicit x8 aggressive smoke passed on `M16-1446`: `.claude-state\profiling\20260604-post-queue-render-cost\M16-1446-scale8-aggressive-sh-halfres-patch.json`, validation `ok=true`.
  - Bottom-left `GUI FPS=142.0`, `smoke presented FPS=29.879`, `timeline FPS=22.765`.
  - `avg_render_total_ms=29.234` (`34.21 FPS-equivalent`), `avg_render_work_ms=21.377` (`46.78 FPS-equivalent`), `avg_draw_total_ms=10.033` (`99.67 FPS-equivalent`), `avg_processing_ms=3.359` (`297.71 FPS-equivalent`), `avg_processing_shadows_highlights_prep_ms=2.286` (`437.45 FPS-equivalent`), `avg_sh_filter_halfres_rbf_ms=1.949` (`513.08 FPS-equivalent`).
  - Previous active-x8 baseline on the same clip was `.claude-state\profiling\20260604-post-queue-render-cost\M16-1446-auto-aggressive-inline-baseline-expected8.json`: `smoke presented FPS=19.924`, `avg_render_total_ms=56.261 ms` (`17.77 FPS-equivalent`), `avg_processing_shadows_highlights_prep_ms=6.435 ms` (`155.40 FPS-equivalent`). Caveat: baseline was Auto after settling to active x8; validation run forced explicit x8.
- Screenshot-backed explicit x4 aggressive smoke also passed on `M16-1327`: `.claude-state\profiling\20260604-post-queue-render-cost\M16-1327-scale4-aggressive-sh-halfres-patch.json`, validation `ok=true`.
  - Bottom-left `GUI FPS=111.0` in summary and `100.0` in the screenshot-time crop, `smoke presented FPS=19.985`, `timeline FPS=22.299`.
  - `avg_render_total_ms=54.800` (`18.25 FPS-equivalent`), `avg_render_work_ms=37.168` (`26.91 FPS-equivalent`), `avg_processing_ms=9.332` (`107.16 FPS-equivalent`), `avg_processing_shadows_highlights_prep_ms=4.079` (`245.16 FPS-equivalent`), `avg_sh_filter_halfres_rbf_ms=3.305` (`302.57 FPS-equivalent`).
- Next session:
  - Treat x8 as the clean win from this change.
  - Treat x4 as branch proof only; its next iteration should target the current dominant stage from fresh telemetry.
  - Auto aggressive on `M16-1446` bounced between final x4 and x8 in short exploratory smokes, so use a longer or more controlled Auto profile before making a policy conclusion.

## 2026-06-04 Addendum - Aggressive Presentation Resampler

- Fresh explicit-scale telemetry identified the next common retained bucket after the queue and shadows/highlights fixes: final processed-RGB presentation scale was still `4-5 ms/frame` (`~208-244 FPS-equivalent`) on aggressive x4/x8.
- Implemented a preview-mode policy split:
  - Sharp/Smooth keeps anti-aliased bilinear/cubic presentation scaling.
  - Aggressive Performance Preview uses nearest-neighbor for the final processed-RGB presentation scale after upstream preview reduction has already happened.
  - The full profile telemetry key `render_thread_playback_scale_resampler` still exposes the selected resampler.
- Focused validation passed after rebuilding `tests\build-ci-pipeline`: `pipeline_tests.exe --gtest_filter=PlaybackScaling.*`, `tests=143`, `assertions=67719`, `skipped=1`, `failed=0`; the skipped test was the pre-existing OpenMP-thread-count-sensitive performance check.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 05:17:26 -05:00`, `Length=9054720`, SHA256 `F81D5BD82E5C36A8881DBE9D28647B0B85024C51D3B786C5B11CEEF2A92A7BB9`.
- Screenshot-backed explicit x4 aggressive smoke on `M16-1327`:
  - Before: `.claude-state\profiling\20260604-next-measured-preview\M16-1327-scale4-aggressive-current.json`, `GUI FPS=111.0` summary / `71.0` crop, `smoke presented FPS=26.975`, `timeline FPS=22.659`, `avg_render_total_ms=33.240` (`30.08 FPS-equivalent`), `avg_playback_scale_ms=4.108` (`243.43 FPS-equivalent`).
  - After: `.claude-state\profiling\20260604-next-measured-preview\M16-1327-scale4-aggressive-nearest.json`, `GUI FPS=34.0` summary / `15.0` crop, `smoke presented FPS=38.059`, `timeline FPS=23.510`, `avg_render_total_ms=22.168` (`45.11 FPS-equivalent`), `avg_playback_scale_ms=1.147` (`871.84 FPS-equivalent`).
  - Presented screenshot SHA256 `F8C0A8F146E75F502291C8C0DC5942CC297E19973D089130CA30E42F7714F5FF`; FPS crop SHA256 `BD6520A579D5A80805B6BD76900081A450161C076E6D2B23F4CBC32868151495`.
- Screenshot-backed explicit x8 aggressive smoke on `M16-1446`:
  - Before: `.claude-state\profiling\20260604-next-measured-preview\M16-1446-scale8-aggressive-current.json`, `GUI FPS=125.0` summary / `76.0` crop, `smoke presented FPS=28.968`, `timeline FPS=22.847`, `avg_render_total_ms=27.449` (`36.43 FPS-equivalent`), `avg_playback_scale_ms=4.532` (`220.65 FPS-equivalent`).
  - After: `.claude-state\profiling\20260604-next-measured-preview\M16-1446-scale8-aggressive-nearest.json`, `GUI FPS=27.0` summary / `142.0` crop, `smoke presented FPS=40.436`, `timeline FPS=23.835`, `avg_render_total_ms=20.217` (`49.46 FPS-equivalent`), `avg_playback_scale_ms=1.229` (`813.67 FPS-equivalent`).
  - Presented screenshot SHA256 `59AF0CEB9FFE55E56290FA97EDF2054F3528408960D96B5A96B8FFF97F0F60EA`; FPS crop SHA256 `9F1098A0219CE2751B44D93B4558EC0D969E57E8356B8EE245A84F0E15223526`.
- Interpretation:
  - The stable `smoke presented FPS` metric improved substantially at both x4 and x8; short-run bottom-left `GUI FPS` labels remained noisy and should not be used alone for ranking.
  - This does not change Bayer-domain reduction, raw decode, LLRawProc/Dual ISO, or debayer. It is the next measured processed-RGB presentation step after the early-resolution model has already reduced upstream work.
- Next session:
  - Run longer Auto aggressive smokes to check scale stability after the presentation-scaler reduction.
  - Rank the next change from fresh telemetry: remaining render work outside `avg_playback_scale_ms`, x4 LLRawProc/processing, or the larger decode-aware/tile-aware x8 raw-decode floor.

## 2026-06-04 Addendum - Aggressive Auto Chooses x8 After Warmup

- Fresh post-resampler Auto aggressive smokes showed explicit x8 was now the best proven Dual ISO aggressive preview path, so the sampler policy was tightened:
  - Aggressive Performance Preview + Dual ISO now chooses HQ x8 after the Auto warmup window, even if cadence is merely near target.
  - Sharp/Smooth Auto and non-Dual-ISO aggressive Auto keep the earlier cadence-miss policy.
- Implemented in `platform\qt\PlaybackQualityPolicy.h`; focused coverage added in `tests\console\test_playback_quality_auto_mode.cpp` as `PlaybackQualityAutoSampler.AggressiveDualIsoUsesHqx8AfterWarmup`.
- Focused validation passed after rebuilding `tests\build-ci-console`: `console_tests.exe --gtest_filter=PlaybackQualityAutoSampler.*`, `tests=93`, `assertions=36`, `skipped=0`, `failed=0`.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 05:42:36 -05:00`, `Length=9054720`, SHA256 `D77312A8A303428F3783333538280BD033AA888191192A1179DB2EB38C92E5A0`.
- Screenshot-backed 20s Auto aggressive GUI smokes passed with `validation.ok=true`, `scaleRequestStart=4`, `scaleRequestLast=8`, and `scaleActiveLast=8`:
  - `M16-1327`: before `smoke presented FPS=27.706`, `avg_render_total_ms=38.488` (`25.98 FPS-equivalent`); after `smoke presented FPS=36.338`, `avg_render_total_ms=24.261` (`41.22 FPS-equivalent`). Bottom-left `GUI FPS=38.0` summary / `76.0` crop, `timeline FPS=23.915`. Presented screenshot SHA256 `43DDEB320E8FEDEDB83CA537468167EB62980F405C3D432CEC797AF932FFA188`; FPS crop SHA256 `5847FB9CF332A38E0C91768388D6FD1F2ED75E9B75177E903E589DD29686DA81`.
  - `M16-1347`: before `smoke presented FPS=23.131`, `avg_render_total_ms=51.655` (`19.36 FPS-equivalent`); after `smoke presented FPS=40.286`, `avg_render_total_ms=20.093` (`49.77 FPS-equivalent`). Bottom-left `GUI FPS=20.0` summary / `62.0` crop, `timeline FPS=23.956`. Presented screenshot SHA256 `5498BE29F4493EB65CB07C3D1491ECDA84D6B0D37AC890CC69B02A6BCBCA7B4D`; FPS crop SHA256 `DE216489D509479752A9C0F163E8F31D96AD7617DC14E2935E8F332F4A39684E`.
  - `M16-1446`: before `smoke presented FPS=25.476`, `avg_render_total_ms=44.067` (`22.69 FPS-equivalent`); after `smoke presented FPS=37.379`, `avg_render_total_ms=21.277` (`47.00 FPS-equivalent`). Bottom-left `GUI FPS=16.0` summary / `76.0` crop, `timeline FPS=23.911`. Presented screenshot SHA256 `19AB2A415BE87AB3C4FC3C0D1D40281D2F0B68D8499BA1373C7C0D94892FD571`; FPS crop SHA256 `60916B1EA7D8940B25B02D666EA25BC7191E380F59166C9313375699DA7B7DF6`.
- Next session:
  - Keep the user-facing split clear: Sharp/Smooth Preview is sharper and anti-aliased; Aggressive Performance Preview is coarse/deep and now uses the strongest early-resolution path after warmup.
  - Rank the next iteration from post-policy telemetry. The high-impact frontier is still decode-aware/tile-aware x8 because raw decode remains full source resolution, but first compare it against the current retained buckets: `avg_processed16_ms` around `9.976-12.861 ms` (`77.75-100.24 FPS-equivalent`) and residual render/presentation scheduling.

## 2026-06-04 Addendum - Cache-Store Telemetry Says Do Not Prune The Cache Next

- A scaled-playback cache-skip behavior change was tested and rejected. It improved some reruns but regressed `M16-1446`, so no cache-pruning behavior was kept.
- The kept change is telemetry-only:
  - Per-frame/profile keys: `processed16_cache_store_ms`, `processed8_cache_store_ms`.
  - GUI smoke summary averages: `avg_processed16_cache_store_ms`, `avg_processed8_cache_store_ms`.
- Corrected explicit x8 aggressive headless profile:
  - Artifact: `.claude-state\profiling\20260604-next-x8-bottleneck\M16-1327-x8-aggressive-cache-telemetry-profile-final.json`.
  - `request=8`, `effective=8`, `phase4b_path=8`, `fallback=none`, `direct8=false`.
  - Resolution proof: raw decode `1808x2268` (`4,100,544` pixels), LLRawProc `226x280` (`63,280` pixels), processing/presentation `226x283` (`63,958` pixels).
  - Stage timing: `render_thread_total_ms=14.162` (`70.61 FPS-equivalent`), `raw_uint16_ms=1.649` (`606.56 FPS-equivalent`), `llrawproc_total_ms=2.703` (`370.00 FPS-equivalent`), `processing_ms=2.865` (`349.06 FPS-equivalent`), `processed16_total_ms=10.676` (`93.67 FPS-equivalent`), `processed8_total_ms=13.892` (`71.98 FPS-equivalent`).
  - Cache-store timing: `processed16_cache_store_ms=0.027` (`36993.86 FPS-equivalent`), `processed8_cache_store_ms=0.000` (effectively zero; FPS-equivalent not meaningful).
- Screenshot-backed x8 aggressive GUI smoke passed:
  - Artifact: `.claude-state\profiling\20260604-next-x8-bottleneck\M16-1327-auto-aggressive-cache-telemetry-smoke-final.json`, `validation.ok=true`.
  - Bottom-left screenshot-time `GUI FPS=90.0`, end-summary `GUI FPS=100.0`, `smoke presented FPS=33.381`, `timeline FPS=23.935`.
  - `avg_render_total_ms=27.242` (`36.71 FPS-equivalent`), `avg_processed8_ms=18.067` (`55.35 FPS-equivalent`), `avg_draw_total_ms=9.727` (`102.81 FPS-equivalent`), `avg_processed16_ms=14.319` (`69.84 FPS-equivalent`).
  - Cache-store summary: `avg_processed16_cache_store_ms=0.086` (`11627.91 FPS-equivalent`), `avg_processed8_cache_store_ms=0.022` (`45454.55 FPS-equivalent`).
  - Presented screenshot: `.claude-state\profiling\20260604-next-x8-bottleneck\screenshots-M16-1327-cache-telemetry-final\M16-1327.png`; FPS crop: `.claude-state\profiling\20260604-next-x8-bottleneck\screenshots-M16-1327-cache-telemetry-final\M16-1327-fps-status.png`.
  - Aspect evidence remains presented-playback stretch: `aspect=2.392322`, `stretch_x=3.0`, `stretch_y=1.0`, `h_stretch_index=0`, `v_stretch_index=3`.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 06:32:07 -05:00`, `Length=9057792`, SHA256 `3CD86B59561ADF3A4E90FDD104ED2AC9F8FC8B86D7D24F01CC9B9876CFFC3A32`.
- Next session:
  - Do not start with cache pruning; the measured cache-store cost is near zero.
  - Rank `processed16_total_ms` / processed8 conversion first for a possible aggressive direct8/local-tone-compatible path (`10.676-14.319 ms`, `69.84-93.67 FPS-equivalent`).
  - Keep decode-aware/tile-aware x8 on the roadmap because raw decode still touches full source pixels, but only start that heavier work when longer profiles show raw decode is a wall-clock limiter after prefetch.

## 2026-06-04 Addendum - Aggressive Direct8 Local-Tone Probe Rejected

- A probe allowed direct8 for non-neutral local tone only in Aggressive Performance Preview, while keeping Sharp/Smooth conservative. It was reverted.
- Focused probe tests passed before revert, so this was not an immediate unit-level parity failure: simple direct8 contrast stayed byte-identical, Sharp/Smooth still blocked the state, and aggressive local-tone direct8 stayed within max diff `5` / mean diff about `0.147`.
- The probe lost the retained x8 throughput shape because local-tone direct8 had to use the shared/autovec kernel and force serial preview render:
  - Probe profile `.claude-state\profiling\20260604-aggressive-direct8-localtone\M16-1327-x8-aggressive-direct8-profile.json`: `render_thread_total_ms=24.405` (`40.97 FPS-equivalent`), `processing_ms=9.108` (`109.79 FPS-equivalent`), `processed16_total_ms=19.351` (`51.68 FPS-equivalent`), `processed8_total_ms=23.568` (`42.43 FPS-equivalent`), `direct8=true`.
  - Prior retained x8 aggressive profile: `render_thread_total_ms=14.162` (`70.61 FPS-equivalent`), `processing_ms=2.865` (`349.06 FPS-equivalent`), `processed16_total_ms=10.676` (`93.67 FPS-equivalent`), `processed8_total_ms=13.892` (`71.98 FPS-equivalent`), `direct8=false`.
- The same probe run confirmed raw x8 prefetch is already active on the current base: `raw_uint16_prefetch_hit=36/37`, `raw_uint16_ms=3.730 ms` (`268.12 FPS-equivalent`), `decode_failures=0`.
- Next session:
  - Do not reopen aggressive local-tone direct8 unless the first patch proves a parallel-safe route and beats the retained x8 profile with screenshot-backed color smoke.
  - Keep optimizing the retained x8 processed16/processing path first.
  - Keep decode-aware/tile-aware x8 as the structural frontier, but gate it on profiles where raw prefetch is hitting and raw decode still dominates wall-clock time.

## 2026-06-04 Addendum - Aggressive x8 Shadows/Highlights Quarter-Res Deep Preview

- Implemented the next retained-x8 processing win selected from the pipeline-resolution model:
  - Added a scale-aware processing preview contract via `processingSetPlaybackPreviewScaleFactor()` / `processingPlaybackPreviewScaleFactor()`.
  - Aggressive Performance Preview at `scale >= 8` now runs Shadows/Highlights RBF at quarter preview resolution, then bilinear-upsamples through existing scratch buffers.
  - Sharp/Smooth Preview keeps the existing full local-tone path; aggressive x4 keeps the existing half-res path.
  - Kill switch: `MLVAPP_DISABLE_AGGRESSIVE_X8_SH_QUARTERRES=1`.
- Added proof telemetry:
  - Per-frame/profile keys: `processing_shadows_highlights_filter_quarterres_downsample_ms`, `processing_shadows_highlights_filter_quarterres_rbf_ms`, `processing_shadows_highlights_filter_quarterres_upsample_ms`.
  - GUI smoke compact summary averages: `avg_sh_filter_quarterres_downsample_ms`, `avg_sh_filter_quarterres_rbf_ms`, `avg_sh_filter_quarterres_upsample_ms`.
- Focused validation passed: `tests\build-ci-pipeline\release\pipeline_tests.exe --gtest_filter=ProcessingFilters.*`, `tests=144`, `assertions=42`, `skipped=0`, `failed=0`.
- Rebuilt `platform\qt\build-release\release\MLVApp.exe`: `LastWriteTime=2026-06-04 07:54:36 -05:00`, `Length=9062912`, SHA256 `263D47D968B55059042639566DA13BD2EB917086F84089AB5744B72C2B8120DD`.
- Headless explicit x8 aggressive profiles in `.claude-state\profiling\20260604-retained-x8-processing\` used 75 warm frames per clip and proved `phase4b_path=8` (`x8-full-xy-pre-recon`), `fallback=none`, `raw_prefetch_hit=75/75`, `direct8=0`, raw decode `4,100,544` pixels, LLRawProc `63,280` pixels, and processing/presentation `63,958` pixels.
- Profile before/after highlights:
  - `M16-1327`: render `55.173 -> 25.133 ms` (`18.12 -> 39.79 FPS-equivalent`), processing `21.200 -> 6.507 ms` (`47.17 -> 153.69 FPS-equivalent`), Shadows/Highlights filter `8.827 -> 3.227 ms` (`113.29 -> 309.92 FPS-equivalent`), processed8 `53.960 -> 24.360 ms` (`18.53 -> 41.05 FPS-equivalent`).
  - `M16-1347`: render `70.507 -> 52.600 ms` (`14.18 -> 19.01 FPS-equivalent`), processing `28.200 -> 13.333 ms` (`35.46 -> 75.00 FPS-equivalent`), Shadows/Highlights filter `13.173 -> 6.707 ms` (`75.91 -> 149.11 FPS-equivalent`), processed8 `68.453 -> 51.053 ms` (`14.61 -> 19.59 FPS-equivalent`).
  - `M16-1446`: render `46.013 -> 40.187 ms` (`21.73 -> 24.88 FPS-equivalent`), processing `12.653 -> 11.547 ms` (`79.03 -> 86.60 FPS-equivalent`), Shadows/Highlights filter `6.480 -> 6.213 ms` (`154.32 -> 160.94 FPS-equivalent`), processed8 `44.053 -> 39.307 ms` (`22.70 -> 25.44 FPS-equivalent`).
- Final screenshot-backed release GUI smoke on `M16-1327` passed: `.claude-state\profiling\20260604-retained-x8-processing\M16-1327-x8-quarterres-sh-smoke-final.json`, `validation.ok=true`, visible bottom-left `GUI FPS=8.2`, summary `GUI FPS=9.0`, `smoke presented FPS=17.259`, `timeline FPS=23.819`, `avg_render_total_ms=73.362 ms` (`13.63 FPS-equivalent`), `avg_processed8_ms=45.475 ms` (`21.99 FPS-equivalent`). The smoke is scheduling-noisy but confirms the release GUI path and summary sink.
- Final compact summary proved quarter-res selection: half-res downsample/RBF/upsample all `0.000 ms`; quarter-res downsample/RBF/upsample `1.127/4.290/1.154 ms` (`887.31/233.10/866.55 FPS-equivalent`).
- Final screenshot: `.claude-state\profiling\20260604-retained-x8-processing\screenshots-M16-1327-quarterres-sh-final\M16-1327.png`, SHA256 `5C48F9327640BFD0BDE461ED6BE65B2AA1D0D4417F0E832B4403326B510B488D`; FPS crop SHA256 `39842F9C429FA8B8599AAD9B58069F0F1F94A6F2ECE562B2B36D093E29CD02C1`; aspect evidence `2.392322`, `stretch_x=3.0`, `stretch_y=1.0`, `h_stretch_index=0`, `v_stretch_index=3`.
- Next session:
  - Keep the active goal: iterate playback speed from telemetry-ranked remaining buckets with explicit ms and FPS/FPS-equivalent labels.
  - Next likely target is not cache pruning or local-tone direct8. Start from `M16-1347`/`M16-1446` retained x8: processed16 totals `45.373 ms` (`22.04 FPS-equivalent`) and `34.267 ms` (`29.18 FPS-equivalent`), plus LLRawProc totals `19.453 ms` (`51.41 FPS-equivalent`) and `12.587 ms` (`79.45 FPS-equivalent`).
  - Keep decode-aware/tile-aware x8 as the structural frontier, but only begin it when longer profiles show raw decode or queueing dominates despite raw prefetch hits.
