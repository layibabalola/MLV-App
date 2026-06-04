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
