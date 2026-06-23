# 2026-06-23 - Image Pipeline Hardening Analysis

## Objective

Map the current image / playback pipeline, identify where the decision logic and telemetry are still hard to reason about, and propose defensive hardening steps that reduce future confusion without changing code yet.

## Verified locally

- The pipeline is already instrumented end-to-end, but the signal is spread across multiple layers instead of one authoritative per-frame manifest.
- The top-level pipeline diagram in [`docs/diagrams/frame-pipeline.md`](../docs/diagrams/frame-pipeline.md) shows the intended stage order from raw read/decode through llrawproc, debayer, processing, and output routing.
- The playback-profile harness in [`docs/17-pipeline-stage-capture.md`](../docs/17-pipeline-stage-capture.md) already captures stage-by-stage buffers and sidecar metadata, including `path_label`, `dual_iso_mode`, `debayer_mode`, and `scaler`.
- The quality / preview policy is split across distinct concepts in [`platform/qt/PlaybackQualityPolicy.h`](../platform/qt/PlaybackQualityPolicy.h:1) and [`platform/qt/PlaybackScaling.h`](../platform/qt/PlaybackScaling.h:1):
  - playback quality mode
  - preview mode
  - preview resolution
  - playback scale factor
  - Phase 4B path choice
- The lower-level processing layer also has its own resolution vocabulary and timing buckets in [`src/processing/raw_processing.c`](../src/processing/raw_processing.c:49), including half-res and quarter-res shadows/highlights paths plus the direct8 fast path.
- The render-thread telemetry already carries a lot of useful data in [`platform/qt/RenderFrameThread.cpp`](../platform/qt/RenderFrameThread.cpp:3334), [`platform/qt/RenderFrameThread.cpp`](../platform/qt/RenderFrameThread.cpp:3907), and [`platform/qt/RenderFrameThread.cpp`](../platform/qt/RenderFrameThread.cpp:4488):
  - `render_thread_phase4b_path`
  - `render_thread_phase4b_path_label`
  - `render_thread_phase4b_path_source`
  - `render_thread_phase4b_fallback_reason`
  - `raw_uint16_*`
  - `llrawproc_*`
  - `processed8_direct_path_active`
  - `processed8_direct_path_reason`
  - `render_thread_preview_mode`
  - `render_thread_aggressive_preview`
- The GUI smoke summary in [`platform/qt/MainWindow.cpp`](../platform/qt/MainWindow.cpp:20780) repeats the same story in a different format, including `phase4b_path`, `phase4b_path_label`, `phase4b_fallback_reason`, `direct8`, and `processed8_prefetch`.
- The tests do pin some of this behavior. For example, [`tests/console/test_clip_golden.cpp`](../tests/console/test_clip_golden.cpp:2321) asserts `render_thread_phase4b_path` and `render_thread_phase4b_fallback_reason`, while [`tests/console/test_clip_golden.cpp`](../tests/console/test_clip_golden.cpp:1157) asserts `processed8_direct_path_active` and `processed8_direct_path_reason`.

## Cross-checked from prior analysis

- Prior investigation notes already pointed at the same theme: a tangle of render paths, ambiguous telemetry naming, and inconsistent path labeling made it hard to tell which path was active.
- The repo already has the right building blocks for a better story:
  - stage resolution telemetry
  - path labels and fallback reasons
  - direct8 incompatibility reasons
  - capture harness sidecars
  - focused path tests
- The missing piece is not more raw data. It is a single, normalized contract that makes one frame’s choice legible without mentally joining four different logs and two policy layers.

## Needs runtime profiling

- I did not run a fresh profile or smoke pass for this note. The analysis here is based on source reading and the existing repo docs / tests.
- If the next step is to validate the shape of the proposed manifest, a small smoke run should confirm that the chosen path, fallback reason, and stage dimensions agree on real frames.

## Findings

### 1. The pipeline has multiple overlapping “quality” concepts

The code uses several overlapping concepts that are easy to confuse:

- `PlaybackQualityMode` in [`platform/qt/PlaybackQualityPolicy.h`](../platform/qt/PlaybackQualityPolicy.h:54)
- `PlaybackPreviewMode` in [`platform/qt/PlaybackQualityPolicy.h`](../platform/qt/PlaybackQualityPolicy.h:59)
- `PlaybackPreviewResolution` in [`platform/qt/PlaybackQualityPolicy.h`](../platform/qt/PlaybackQualityPolicy.h:70)
- phase4b path selection in [`src/mlv/video_mlv.c`](../src/mlv/video_mlv.c:3725)
- render-thread path labels in [`platform/qt/RenderFrameThread.cpp`](../platform/qt/RenderFrameThread.cpp:3334)
- processing-layer halfres / quarterres blur policy in [`src/processing/raw_processing.c`](../src/processing/raw_processing.c:1227)

That means a user can see “quality” mentioned in the GUI, “preview” in the policy layer, “phase4b path” in the render layer, and “quarterres” in processing, without a single canonical frame-level answer telling them which contract actually ran.

### 2. The authoritative path state is fragmented

The Phase 4B renderer does keep internal state:

- `mlv_phase4bv2_last_path_taken()` in [`src/mlv/video_mlv.c`](../src/mlv/video_mlv.c:3725)
- `mlv_phase4bv2_last_fallback_reason()` in [`src/mlv/video_mlv.c`](../src/mlv/video_mlv.c:3729)
- `mlv_phase4bv3_last_y_crop_rows()` in [`src/mlv/video_mlv.c`](../src/mlv/video_mlv.c:3735)

But the render thread then re-exports those values into a larger telemetry object, and MainWindow re-summarizes them again. The result is correct data, but not a single obvious source of truth.

This matters because the same frame can be described through:

- a raw path id
- a fallback string
- a path label
- a path source
- several stage pixel dimensions
- many stage timings

That is enough for diagnosis, but not enough for clarity unless there is a canonical manifest tying all of it together.

### 3. The terminology around half-res and quarter-res is locally precise but globally noisy

`src/processing/raw_processing.c` is internally consistent, but the naming is still dense:

- `g_processing_last_shadows_highlights_filter_halfres_*`
- `g_processing_last_shadows_highlights_filter_quarterres_*`
- `processing_standard_x1_shadows_highlights_quarterres_enabled()`
- `processing_standard_x2_shadows_highlights_quarterres_enabled()`
- `processing_standard_x4_shadows_highlights_quarterres_enabled()`
- `processing_direct8_preview_quarterres_sh_enabled()`

That is workable for maintainers, but it is easy for future investigators to confuse:

- preview scale
- preview resolution proxy level
- the actual Stage 4B path
- the blur kernel resolution used inside processing

The repo should make this distinction more explicit in logs and docs, not just in code comments.

### 4. Logging exists, but it is not normalized

There are already several useful logs:

- the render-thread smoke line in [`platform/qt/MainWindow.cpp`](../platform/qt/MainWindow.cpp:20780)
- the render-thread telemetry block in [`platform/qt/RenderFrameThread.cpp`](../platform/qt/RenderFrameThread.cpp:3334)
- the GPU viewport context / texture-path log in [`platform/qt/GpuDisplayViewport.cpp`](../platform/qt/GpuDisplayViewport.cpp:912)
- the llrawproc backend / missing symbol diagnostics in [`src/mlv/llrawproc/llrawproc.c`](../src/mlv/llrawproc/llrawproc.c:575) and [`src/mlv/llrawproc/llrawproc.c`](../src/mlv/llrawproc/llrawproc.c:795)
- the processed8 prefetch debug line in [`src/mlv/video_mlv.c`](../src/mlv/video_mlv.c:2558)

But these logs are heterogeneous:

- some are `qInfo`
- some are `qWarning`
- some are `printf`
- some are `fprintf(stderr, ...)`
- some are one-shot summaries
- some are debug-only traces

That makes them useful in isolation but harder to correlate in a future incident.

### 5. The current tests prove pieces, not the whole contract

The tests are strong for their individual seams:

- [`tests/console/test_clip_golden.cpp`](../tests/console/test_clip_golden.cpp:2321) checks Phase 4B path and fallback behavior.
- [`tests/console/test_clip_golden.cpp`](../tests/console/test_clip_golden.cpp:1157) checks direct8 activation and reason strings.
- [`tests/pipeline/test_dual_iso_pipeline.cpp`](../tests/pipeline/test_dual_iso_pipeline.cpp:6595) and nearby cases pin path IDs and crop rows.

What is still missing is a higher-level invariant test that says:

- the path label matches the path id
- the path source matches the cache/live origin
- the fallback reason is non-empty only when the path falls back
- the stage dimensions agree with the chosen path
- the processing resolution labels agree with the selected preview policy

Those cross-field checks would make future regressions much easier to diagnose.

## Suggestions

### Highest priority

1. Add a single per-frame render manifest log line.
   - Emit one canonical line per frame from the render thread.
   - Include: frame index, request serial, scale request/effective, quality mode, preview mode, preview resolution, Phase 4B path id, path label, path source, fallback reason, direct8 active, raw prefetch hit, and stage dimensions.
   - Keep it machine-parsable and stable.

2. Normalize the path vocabulary.
   - Define one canonical enum / manifest for render-path decisions.
   - Keep string labels only as a presentation layer.
   - Avoid forcing future readers to infer meaning from `phase4b_path`, `path_label`, `fallback_reason`, and `direct8_reason` separately.

3. Add cross-field validation tests.
   - Assert that the manifest fields agree with one another.
   - Check that path ids, path labels, fallback reasons, and stage dimensions remain self-consistent.
   - Add at least one test that compares the summary log / telemetry against the stage capture harness sidecar.

### Medium priority

4. Split policy translation from path selection.
   - Create one `RenderPlan`-style object or equivalent decision struct.
   - Compute it once per frame.
   - Pass it through the pipeline instead of re-deriving policy in multiple layers.
   - This would make the logic much less spaghetti-like and reduce repeated `getenv` / cached-static decision code.

5. Make processing-vs-quality terminology explicit in logs.
   - Reserve “quality mode” for the user-facing policy choice.
   - Reserve “preview mode” for sharp vs aggressive.
   - Reserve “preview resolution” for the GUI proxy level.
   - Reserve “path” for the concrete algorithmic route that ran.
   - Reserve “quarterres / halfres” for the internal processing kernel resolution only.

6. Standardize diagnostic prefixes.
   - Prefer a small set of prefixes, such as `RENDER_PLAN`, `PIPELINE_STAGE`, `LLRAWPROC`, `GPU_VIEWPORT`.
   - The goal is grepability, not more text.

### Lower priority but still worthwhile

7. Document the path matrix in one place.
   - The current docs are good for orientation, but they still spread the story across diagram, harness, and test files.
   - Add a concise “which path means what” table for the common playback cases.

8. Add a compact failure-mode glossary.
   - Examples: `fallback_reason`, `direct8-incompatible`, `path_source`, `cache_hit`, `prefetch_hit`, `phase4b_path=0`.
   - This would help future reviewers distinguish a fallback from a bug.

## Recommended shape for the eventual fix

- Keep the current telemetry, but add a normalized manifest on top.
- Keep the legacy string fields for backward compatibility.
- Stop using scattered fields as the primary explanation.
- Let the new manifest become the first thing the smoke harness and reviewer read.

## Suggested implementation tickets

These are intentionally phrased as draft tickets rather than a locked plan. Claude can keep them as-is, split them, merge them, or reorder them if it sees a better implementation path.

1. Fix path emission at the source.
   - Remove any logic that zeroes the real path tag on the dominant interactive path.
   - Make the path label injective over all path codes the renderer can emit.
   - Emit the fallback reason every frame, not only on fallback frames.
   - Inline the path source with the path value so stale cache data cannot masquerade as a fresh decision.

2. Teach the structured consumers about the new manifest.
   - Update the GUI smoke summary parser and any telemetry ingest scripts at the same time as the manifest.
   - Add a consumer-side assertion for the canonical bug case where a reduced frame must not report a zero path code.
   - Keep the manifest visible to the automation that already reads the smoke output.

3. Add a canonical per-frame render manifest.
   - Emit one machine-readable manifest per rendered frame.
   - Include frame index, request serial, request/effective scale, quality mode, preview mode, preview resolution, Phase 4B path id, path label, path source, fallback reason, direct8 active, raw prefetch hit, and stage dimensions.
   - Add a dimension-trace so every reduction and upscale step is explicit.
   - Treat this as the primary explanation surface for future smoke runs.

4. Add runtime invariants and cross-field tests.
   - Assert at emit time that a reduced rendered frame cannot advertise a zero path code.
   - Assert that path id, path label, fallback reason, and stage dimensions agree.
   - Add a smoke-level assertion that the GUI summary, render-thread telemetry, and stage-capture sidecar all describe the same path.
   - Keep the tests narrow and deterministic.

5. Normalize the path vocabulary across layers.
   - Define a single canonical path enum or manifest schema for render decisions.
   - Keep string labels only as derived presentation data.
   - Reduce the need to interpret `phase4b_path`, `path_label`, `fallback_reason`, and `direct8_reason` independently.

6. Split policy translation from rendering selection.
   - Introduce a `RenderPlan`-style decision object or equivalent.
   - Compute it once per frame.
   - Pass it through the pipeline rather than re-deriving similar policy in multiple places.
   - Keep this shadow-validated first, then let it become the dispatcher only after it matches the legacy ladders.

7. Clean up quality terminology in logs and docs.
   - Reserve “quality mode” for the user-facing policy choice.
   - Reserve “preview mode” for sharp vs aggressive.
   - Reserve “preview resolution” for the GUI proxy level.
   - Reserve “path” for the concrete algorithmic route that ran.
   - Reserve “quarterres / halfres” for the internal processing kernel resolution only.

8. Add a concise operator-facing glossary.
   - Define the common log terms in one place.
   - Include the most confusing fields and their meaning at a glance.
   - Prefer examples that tie the field back to a real frame outcome.

## Bottom line

This repo already has good instrumentation, but the decision-making surface is still too distributed for future humans to reason about quickly. The safest hardening path is not to remove signal; it is to consolidate signal into one canonical per-frame plan and then make the existing telemetry validate that plan instead of trying to explain it piecemeal.

The empirical review also suggests a sharper ordering than the original draft: fix the path emission at the source, update the structured consumers at the same time, then land the manifest and runtime invariants, and only after that let a `RenderPlan` refactor take over dispatch.

---

# Claude review, pushback & enhancements (2026-06-23, empirically verified)

Reviewed against two things this original note explicitly did NOT do (it states "I did not run a fresh profile ... source reading and existing docs"): (a) a fresh **empirical pass** on real gui-smoke logs from the period-4 investigation, and (b) a **7-agent read-only mapping** of the pipeline with file:line citations. Verdict: **the direction is right and endorsed** — one canonical per-frame manifest, keep the existing telemetry, validate it with cross-field tests, eventually a `RenderPlan`. The cited symbols/files were all confirmed real (`mlv_phase4bv2_last_path_taken` at `video_mlv.c:3725`, `PlaybackQualityPolicy.h`, `PlaybackScaling.h`, `docs/17-pipeline-stage-capture.md`, the GUI summary at `MainWindow.cpp`). But the analysis **understates the severity** and **misses the two highest-leverage items**. Corrections below; every claim is code-cited and, where noted, runtime-verified.

## PUSHBACK 1 — the telemetry is not "correct but fragmented", it is FALSIFIED on the dominant path

The note frames the data as "correct data, but not a single obvious source of truth" (§2). Empirically that is too generous. On the exact config that confused everyone (scale=1, phase3_hq, dual-ISO HQ, Auto proxy — the most common interactive path), the emitted path telemetry is **wrong**, not merely scattered:

1. **Tag zeroing — THE root cause both prior analyses missed.** `RenderFrameThread.cpp:3285-3287`: `phase4bPath = (playbackScaleFactorActive>1 ? mlv_phase4bv2_last_path_taken() : 0)`. At scale=1 the x1 half-res proxy genuinely runs and sets tag 7 (`video_mlv.c:4385`), but the ternary **discards it and emits 0** — byte-identical telemetry to a true full-res frame. I confirmed this live: `phase4b_path=0` in BOTH proxy-on and proxy-disabled runs. This one line is why the period-4 workflow ("full pipeline still stripes") and the peer agent both concluded "full recon / no reduction" on a frame that was reconstructed + processed + upscaled at HALF resolution. **A manifest that reads `mlv_phase4bv2_last_path_taken()` through this zeroing is born lying.** Fix = read it unconditionally (1 line).
2. **Non-injective label.** `RenderFrameThread.cpp:63-73`: the label decodes only tags 8/4/3/2; tags 0,5,6,7,9,10,11 ALL collapse to one string `none-or-full-recon-fallback` — which is also the genuine-fallback string. So "keep string labels as presentation" (ticket 2) is necessary but insufficient until the label is made injective over all 11 codes.
3. **Tag 0 is overloaded** across ≥4 states (reset/not-yet-rendered; scale=1 full-res debayer; 16-bit cache-hit early return; scale>1 fallback). `phase4b_path=0` is meaningless without scale + dims context.
4. **Stale cache tags not reconciled.** A processed8 cache hit replays the STORED tag of whatever earlier frame filled the slot (`video_mlv.c:6528`; slot written `1996`, read `2241`). The only discriminator, `path_source`, is emitted in a separate key, never inline with the value — a stale tag is indistinguishable from a fresh decision.
5. **A FALSE documented invariant.** `video_mlv.c:3715-3716` asserts the tag is "process-wide (not thread-local)", but it is declared `MLV_STAGE_THREAD_LOCAL` (`3721`). Prefetch/lookahead workers set it on their own TLS copy (`5832`/`5917`); the render-thread reader sees only its own. A false invariant comment is exactly what makes an investigator trust a wrong reading.

Net: the manifest cannot just *aggregate* these primitives — it must **fix the emission first** (un-zero the tag, make the label injective, carry `path_source` inline, correct the comment). All low-risk, few-line changes, and they must land *before or with* the manifest or the manifest inherits the lie.

## PUSHBACK 2 — the missing piece the note didn't catch: the path telemetry has NO CONSUMER

The note says "the missing piece is not more raw data" (§Cross-checked). True, but it missed *why* the data was invisible: **nothing structured reads it.** `run-release-gui-smoke.ps1` (~1149-1217) ingests only start/summary/aggregate lines (`Select-Object -Last 1`); `analyze-frame-telemetry.py:22` regexes ONLY `playback_smoke.frame` — the pacing line that carries NO path tag. So `phase4b_path` exists in raw stdout but is absent from every structured artifact a workflow/test/reviewer reads. **This is literally why a 13-agent workflow could not find the path.** Therefore: the harness-consumer update is NOT a "lower-priority doc ticket" — it must be **co-delivered with the manifest**, or the manifest is invisible to exactly the automation that needs it.

## ENHANCEMENT — manifest spec (supersedes the field list in tickets 1/Highest-priority-1)

Build ONE line `playback_smoke.render_manifest`, **key=value (QStringList join), NOT positional `%N` args** — `cpu_frame` is already at 38 args and `dual_iso_full20_frame` at 57, approaching the `QString::arg` `%99` ceiling that *already silently corrupted* `cpu_summary` (forcing the `cpu_summary_ext` split at `MainWindow.cpp:22466`). Required fields beyond the note's list:
- **Path (the core fix):** `path_code` = raw 0..11 read UNCONDITIONALLY; `path_label` = NEW injective label over all 11; `path_source(render_thread|processed8_cache)` INLINE; `path_fallback_reason` emitted always; `proxy_active`, `proxy_halvings(0|1|2)`, `direct8`, `processed8_cache_hit`, `raw_prefetch`, `y_crop_rows`.
- **Kill the "halfres" ambiguity** (it means THREE unrelated things that can co-fire on one frame — the preview proxy that halves W&H at `video_mlv.c:3591`, the dual-ISO same-grid mix at unchanged dims in `dualiso.c`, and the Shadows/Highlights RBF filter): emit distinct tokens `preview_proxy_halfres/quarterres`, `diso_mix_halfres`, `sh_rbf_halfres/quarterres`, plus `dual_iso_mode(off|preview|full20)`, `dual_iso_fullres`, `dual_iso_interp`, `sh_filter_res`.
- **Dimension-trace** (the field that makes an artifact period *divisible through the real chain* instead of guessed — the precise thing that misled the period-4 hunt): `src_WxH -> decode -> llrawproc -> rgb -> rendered -> proxy_upscale -> stretch_x -> present_target`. Every value already exists in the timing map (`RenderFrameThread.cpp:2735-2739`, `3577`; `MainWindow.cpp:6637-6640`) and currently dies in the QJsonObject unemitted.

## ENHANCEMENT — runtime invariants, not just offline tests

The note frames cross-field checks as CI tests (good, keep). Add **runtime assertions at the emit site** (debug build): if `rendered_dims < src_dims` (a reduction provably occurred) then `path_code` MUST be non-zero, else emit a `render_manifest_inconsistency` marker. *This would have fired immediately on the scale=1 proxy frame* and ended the confusion at the source. Plus a cache-provenance invariant (cache-served `path_code` must match the current request's scale/quality).

## ENHANCEMENT — test harness: a known-period grating clip

Stronger than generic cross-field tests: add a synthetic clip with an exactly-known horizontal spatial frequency and assert the emitted dimension-trace *divides that period correctly at each resample stage* for every (scale × proxy × quality × dual-ISO) combination. A wrong path is then caught by a **numeric period mismatch**, not by trusting a label — making the period-4 mislabeling class impossible to reintroduce silently.

## Consolidated, risk-ordered sequencing (supersedes the ticket numbering)

Endorsed shape, but re-sequenced by **risk AND peer-edit conflict** (Codex is actively editing `video_mlv.c`/`dualiso.c` for the dual-ISO striping fix right now). **Phase A is GUI-side only — no `video_mlv.c` conflict — and Step A1 directly helps the striping hunt** by finally revealing which path each frame takes:

- **Phase A (low risk, conflict-free, do first):**
  - A1 — remove the scale≤1 tag zeroing; read the tag unconditionally; emit `fallback_reason` every frame. `RenderFrameThread.cpp:3285-3290,3326-3333`. *Highest-value single change.*
  - A2 — make `phase4bPathLabel` injective over all 11 codes. `RenderFrameThread.cpp:63-73`.
  - A3 — add the `render_manifest` line (key=value, with dimension-trace + disambiguated halfres tokens). `MainWindow.cpp:~21786`.
  - A4 — disambiguate the three emitted "halfres" tokens (GUI emission strings only). `MainWindow.cpp`.
  - A5 — teach the consumers: `analyze-frame-telemetry.py:22` + `run-release-gui-smoke.ps1:1149` ingest/aggregate the manifest; add the canonical-bug-config smoke assertion (path 7, not 0/none). `tests/console/test_clip_golden.cpp:2326-2506`.
  - A6 — debug-build invariant assertions (rendered<source ⟹ code≠0; cache-source ⟹ scale/quality match).
  - A7 — known-period grating clip + per-stage period-division test.
- **Phase B (one-line, but touches `video_mlv.c` — sequence after Codex's striping fix lands):**
  - B1 — correct the false "process-wide (not thread-local)" comment. `video_mlv.c:3715-3721`.
- **Phase C (structural, medium/high risk, defer behind the Phase-A safety net + Codex coordination):**
  - C1 — named `mlv_render_path_t` enum shared core↔GUI with a compile-time label-completeness check.
  - C2 — `RenderPlan` in **shadow/compute-only** mode: compute once, ASSERT it matches the legacy ladders, emit it; do NOT let it drive dispatch yet (de-risks before it controls anything).
  - C3 — dispatcher consumes `RenderPlan`; leaves stop re-deriving dims; replace the `processingSetPlaybackPreviewScaleFactor` save/restore dance with `plan.effectiveScale`; de-dup the `_direct8_*`/scaled-core triplication; unify the env-cache statics (some freeze on first read, siblings re-read — an A/B that sets an env after first render silently no-ops half the switches).

## Scope pushback

- **Do NOT rename internal C symbols** (the note's ticket 5 risks this). Normalize only the **emitted log tokens**. Renaming `g_processing_last_shadows_highlights_filter_halfres_*` etc. is churn + merge-conflict surface for ~zero observability gain.
- **`RenderPlan` must be shadow-mode first** (compute + assert-matches-legacy before it drives dispatch). Treating it as a near-term ticket (note ticket 4) without the shadow step is the riskiest move in the whole plan.
- **Process note:** this analysis, like the two before it, was source-reading without runtime verification — and consequently mis-rated severity. That is itself the case for the hardening: observability that is TRUE and machine-consumable, plus the discipline to empirically verify before concluding. The gap left by the one map agent that failed (full `MLVAPP_*` env-toggle inventory) should be filled before Phase C, since the env-cache unification (C3) depends on it.

**Bottom line (Claude):** endorse the direction, but the first deliverable is not "add a manifest" — it is **fix the falsified path emission (A1/A2), make it machine-consumable (A5), and assert it can't lie again (A6/A7)**, all GUI-side and conflict-free, with the `RenderPlan` consolidation as a later, shadow-validated phase.
