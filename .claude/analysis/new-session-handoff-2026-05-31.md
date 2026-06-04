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
