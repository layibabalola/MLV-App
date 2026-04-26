# Phase 3 Playback Pipeline Data Model

Status: Phase A design artifact, verified against live code on 2026-04-25.
Scope: design only. This document does not authorize production code changes.

This is the canonical contract for Phase 3 playback pipeline work. It is
created under `docs/` and must be staged/committed as the Phase A durable
artifact before Gate A->B opens. Any scratch checklist or agent notes under
`.claude-state/` are convenience copies only; this document is the durable
source of truth once tracked by git.

Older `.claude/analysis` notes are useful history, but they are not binding.
Before implementing any Phase 3 change, reconcile this document against live
code and update the document if the code has moved.

## 1. Live-Code Baseline

This document was written after a read-only pass over these live files:

| Area | Live source |
|---|---|
| Render thread and slot ring | `platform/qt/RenderFrameThread.h`, `platform/qt/RenderFrameThread.cpp` |
| GUI display handoff | `platform/qt/MainWindow.h`, `platform/qt/MainWindow.cpp` |
| Playback scaling | `platform/qt/PlaybackScaling.h` |
| Stage timing | `src/debug/StageTiming.h`, render/profile JSON plumbing |
| MLV object caches | `src/mlv/mlv_object.h`, `src/mlv/video_mlv.c`, `src/mlv/frame_caching.c` |
| llrawproc / Dual ISO recon | `src/mlv/llrawproc/llrawproc.c`, `src/mlv/llrawproc/llrawproc.h`, `src/mlv/llrawproc/llrawproc_object.h` |
| CLI and tests | `platform/qt/main.cpp`, `src/batch/MlvTrim.cpp`, `tests/` |

Important live anchors:

- `RenderFrameThread::ReadyFrame` exposes borrowed pointers into a
  slot-owned `FrameSlot` at `platform/qt/RenderFrameThread.h:36-88`.
- `FrameSlot` owns `rawImage8`, `rawImage16`, and `playbackScaledImage8`, plus
  metadata and telemetry, at `platform/qt/RenderFrameThread.h:140-169`.
- The current render slot ring has `kFrameSlotCount = 4` and
  `kRenderRequestQueueDepth = 4` at `platform/qt/RenderFrameThread.h:201-235`.
- `renderFrame()` drops the oldest queued request when the queue is full at
  `platform/qt/RenderFrameThread.cpp:95-118`.
- `run()` renders serially, unlocks while `drawFrame()` runs, marks a slot
  ready, then emits `frameReady()` outside the lock at
  `platform/qt/RenderFrameThread.cpp:276-319`.
- `acquireLatestReadyFrame()` picks the ready slot with the highest
  `requestSerial`, marks it `presenting`, and returns borrowed pointers at
  `platform/qt/RenderFrameThread.cpp:135-183`.
- `releasePresentedFrameForRequestSerial()` is the safe release primitive for
  borrowed slots at `platform/qt/RenderFrameThread.cpp:197-210`.
- `drawFrameReady()` acquires a ready frame and later releases it after final
  presentation at `platform/qt/MainWindow.cpp:12395-12430` and
  `platform/qt/MainWindow.cpp:12383-12391`.

## 2. Goals And Non-Goals

Phase 3 is a staged playback pipeline. It aims to overlap expensive work so
cadence trends toward the longest single stage instead of the sum of all
stages.

The top-line playback metric is presented-frame cadence, not render latency:

- Primary: warm `cadence_ms` / inter-present time between completed presented
  frames, reported as median, p95, p99, and max.
- Secondary: `latency_ms`, `engine_latency_ms`, `draw_frame_ready_*`, and
  per-stage compute fields.

Non-goals:

- Do not revive a post-llrawproc/recon-output cache for linear playback.
- Do not mutate process environment variables at runtime for fallback.
- Do not expose experimental Phase 3 modes by label alone.
- Do not trust old analysis or invented wrapper flags without checking live
  code.
- Do not change the serial playback path as a side effect of Phase A.

## 3. Threads

### Current Live Threads

The current implementation already has several moving pieces:

| Thread / actor | Current responsibility |
|---|---|
| Qt main thread | Owns UI state, `drawFrameReady()`, final `QImage`/`QPixmap` presentation, scopes, overlays, and `frameReady()` completion. |
| `RenderFrameThread` | Owns the current render slot ring and serial `drawFrame()` execution. |
| Playback-prep worker | Builds display/prep results asynchronously, conflated by request serial. |
| Existing cache/prefetch workers | Own raw cache, processed8 prefetch, and raw uint16 prefetch internals. These are separate from Phase 3 render slots. |

### Phase 3 Target Threads

The Phase 3D target pipeline has four stage owners:

| Stage owner | Responsibility |
|---|---|
| Decode thread | Read compressed/packed source and produce pre-llrawproc Bayer16 in `slot.raw16`. |
| Recon thread | Run `applyLLRawProcObject()` / Dual ISO preview or HQ recon in-place or into `slot.recon16`. |
| Process thread | Debayer/process/pack into `slot.processed16` and/or `slot.processed8`. |
| Display thread | Qt main thread acquires latest display-ready slot, builds or receives display image, presents, then releases by request serial. |

The playback-prep worker remains a GUI-side helper. It is not allowed to own a
render slot indefinitely; it borrows slot pointers only while the slot is in
`Presenting`.

## 4. Rings And Caches

The Phase 3 render slot ring must not be confused with existing caches.

| Structure | Location | Meaning | Phase 3 rule |
|---|---|---|---|
| Render slot ring | `RenderFrameThread::m_frameSlots`, 4 slots | Presentation lifecycle storage | Phase 3 expands this state machine. |
| Legacy raw cache | `cache_memory_block` / `rgb_raw_frames` | Debayered RGB16 frame cache, not Bayer raw16 | Do not reuse as pipeline storage. |
| `rgb_raw_current_frame` | `mlvObject_t` | Single repeated-frame RGB16 fallback | Not a render slot. |
| Processed16 cache | `rgb_processed_current_frame*` | Exact processed RGB16 cache with signatures and scale | Leave its key/generation model separate. |
| Processed8 cache | `rgb_processed_current_frame_8bit` plus 8 slot metadata lanes | Exact processed RGB8 cache | Do not conflate with render slot ring. |
| Raw uint16 prefetch | 4-slot pre-llrawproc Bayer16 lookahead | Decode-ahead for compressed sources | Allowed and separate; keep LJ92 profiling gate. |
| Display preview cache | `MainWindow` preview cache | GUI display image/pixmap cache | GUI-side only. |

The llrawproc/recon-output cache experiment is explicitly out of scope. It was
not useful for unique-frame linear playback and it risks skipping
`applyLLRawProcObject()` side effects. Any future Phase 3 patch that adds
`llrawproc_cache_*`, `recon_cache_*`, or another post-llrawproc per-frame cache
on the linear playback hot path is a regression unless a new design review
proves it stores/restores all runtime state and improves the target workload.

## 5. Slot Identity And Freshness

Each Phase 3 render slot has both a stable slot index and per-use identity:

| Field | Meaning |
|---|---|
| `slotIndex` | Stable array index in the render slot ring. |
| `slotGeneration` | Incremented every time the slot is reused after `Free`. |
| `clipGeneration` | Incremented on clip switch, destructive receipt reload, or any action that invalidates outstanding render/prep work. |
| `requestSerial` | Monotonic request freshness key. Higher serial wins. |
| `frameNumber` | Source frame index requested by the user/playback loop. |
| `outputMode` | `RenderFrameThread::OutputMode` equivalent for the slot. |

Freshness contract:

1. `requestSerial` is monotonic for all render requests.
2. A slot may be presented only when its `clipGeneration` equals the current
   clip generation.
3. A slot may be presented only when its `requestSerial >= m_lastPresentedSerial`.
4. When serial N is acquired for presentation, ready slots with serial < N are
   stale and must be released or marked stale before they can emit another
   "latest" presentation.
5. Any async prep result whose serial is below the latest requested/presented
   high-water mark must release its borrowed slot and drop without touching UI.
6. `frameNumber` alone is never a freshness key. Loops and scrub patterns can
   revisit frame numbers with newer settings and newer serials.

## 6. State Machine

### Canonical Six-State Lifecycle

The durable conceptual lifecycle is:

```text
Free -> Decode -> Recon -> Process -> DisplayReady -> Presenting -> Free
```

`DisplayReady` means the slot is ready to be borrowed by the GUI. `Presenting`
means the GUI or playback-prep path may still hold pointers into slot-owned
vectors. A slot is not reusable until the matching
`releasePresentedFrameForRequestSerial()` path releases it.

`Done` is not a borrowing state. If an implementation uses an internal `Done`
marker, it means all borrows are already released and the slot can transition
to `Free` under the parent coordinator mutex. Do not introduce a per-slot
mutex unless this data model is revised with a lock-order table.

### Nine-State Implementation Expansion

Phase B/D may use a more explicit implementation enum:

```text
Free
DecodeQueued -> Decoding
ReconQueued  -> Recon
ProcessQueued -> Processing
DisplayReady
Presenting
```

Staleness is a flag or terminal release path, not a presentable state. A stale
slot can be released only by the coordinator while no stage owns it and while
no GUI/prep borrow remains.

### Current Live Mapping

Current live code has a smaller boolean model:

| Current field | Data-model equivalent |
|---|---|
| `ready=false`, `presenting=false`, not `m_renderingSlotIndex` | `Free` |
| `i == m_renderingSlotIndex` | Serial `Decode+Recon+Process` combined |
| `ready=true` | `DisplayReady` |
| `presenting=true` | `Presenting` |

Because `acquireLatestReadyFrame()` can mark multiple slots as `presenting`
over time while `m_presentingSlotIndex` stores only one index, Phase 3 must
make serial-specific release the authoritative release model. The old
single-index `releasePresentedFrame()` is not sufficient for deeper queues.

## 7. Ownership Table

| Slot field | Decode | Recon | Process | Display / prep | Release |
|---|---|---|---|---|---|
| `slot.raw16` | Write pre-llrawproc Bayer16 | Read or mutate input | Read only if Recon intentionally mutates `raw16` in-place into post-llrawproc data; otherwise forbidden | Never read directly | Clear/free only after no owner/borrow |
| `slot.recon16` | No access | Write post-llrawproc Bayer16 when not using in-place `raw16` | Read for debayer/process | Never read directly | Clear/free after release |
| `slot.processed16` | No access | No access | Write RGB16 | Borrow as RGB16 preview/source | Clear/free after release |
| `slot.processed8` | No access | No access | Write RGB8 | Borrow as RGB8 preview/source | Clear/free after release |
| `slot.playbackScaledImage8` | No access | No access | Optional write during render-scale prep | Borrow when prescaled display path is active | Clear/free after release |
| `frameNumber` | Set from request | Read only | Read only | Read only | Reset on reuse |
| `requestSerial` | Set from request | Read only | Read only | Read for freshness/release | Reset on reuse |
| `clipGeneration` | Set from current clip | Read/check | Read/check | Read/check | Reset on reuse |
| `outputMode` | Set from request | Read only | Read only | Read only | Reset on reuse |
| `presentationContext` | Set from request | Read only | Read for scaling policy | Read for presentation | Reset on reuse |
| `stageTimingTelemetry` | Append decode fields | Append recon fields | Append processing fields | Append presentation fields or copy to sample | Reset on reuse |
| Dual ISO runtime snapshot | No write | Capture after llrawproc | Read/copy only | UI can display/persist after presentation | Reset on reuse |
| Lifecycle state | `DecodeQueued/Decoding` | `ReconQueued/Recon` | `ProcessQueued/Processing` | `DisplayReady/Presenting` | `Free` |

All buffer pointer handoffs are borrowed, not transferred. A worker may not
store a raw pointer to another stage's buffer after it transitions the slot to
the next stage.

## 8. Synchronization Contract

### Parent Render Mutex

`RenderFrameThread::m_mutex` currently protects request queue state, slot
ready/presenting flags, active request fields, telemetry copies, and
wait/wake flow. Phase 3 should keep one parent coordinator mutex for:

- Slot lifecycle state.
- Request queue and stale-drop decisions.
- Queue condition-variable predicates.
- High-water `m_lastPresentedSerial`.
- `clipGeneration` observed by the render thread.

Workers must not run expensive decode/recon/process work while holding the
parent mutex. They reserve a slot/stage under the mutex, copy the small fields
they need, release the mutex, compute, then reacquire the mutex to publish.

### Worker Stop/Wake Mutexes

Per-worker mutexes may protect worker-local stop flags and condition-variable
wakeups. They must not become a second owner of slot lifecycle state. If a
worker needs a slot transition, it uses the parent coordinator mutex.

Lock order:

```text
worker-local mutex -> release -> parent render mutex
```

Never hold a worker-local mutex while waiting on the parent render mutex. Never
hold the parent mutex while calling into llrawproc, processing, Qt image code,
disk IO, LJ92 decode, or cache code.

### llrawproc State

`applyLLRawProcObject()` is stateful. It mutates the caller buffer and may
publish Dual ISO and DNG runtime fields back to `video->llrawproc`. Phase 3
must treat recon as the only stage allowed to call llrawproc for a slot.

Any code that reads or publishes shared llrawproc runtime state must respect
the existing llrawproc locking model. Do not bypass that model with a cache hit
that skips `applyLLRawProcObject()` side effects.

### Fallback Switch

Auto-fallback must use a live atomic override, not environment mutation. The
Phase B API should be:

```cpp
bool phase3KillSwitchActive(Phase3Mode mode) noexcept;
void phase3SetLiveFallbackActive(bool active) noexcept;
```

Environment variables are read and cached at startup or explicit test reset.
AFB calls `phase3SetLiveFallbackActive(true)`. Dispatchers observe the live
override through `phase3KillSwitchActive(mode)` on every frame request.

## 9. Dispatcher Model

`phase3ModeFor()` is the only place that maps UI/settings/env state to the
runtime pipeline mode. It must be easy to grep and easy to test.

Proposed modes:

| Mode | Meaning |
|---|---|
| `Disabled` | Legacy serial path only. Default. |
| `Scaffold` | Phase 3 slot metadata/telemetry active, serial execution. |
| `DecodeOverlap` | Decode can run ahead; recon/process/display stay serial. |
| `DecodeReconOverlap` | Decode and recon are staged; process/display stay serial. |
| `FullPipeline` | Decode, recon, process, and display are staged. |

Rules:

- A lower phase kill switch disables all higher phases.
- A live fallback disables all Phase 3 modes immediately.
- UI exposure is separate from availability. Experimental modes stay hidden
  unless `Playback/ShowExperimentalPhase3Modes` (or successor) is enabled.
- The Q-cycle remains legacy-only by default until Phase C explicitly changes
  it.

## 10. Telemetry Contract

Current JSON telemetry is `QJsonObject` based:

- Render-side stage data lives in `FrameSlot::stageTimingTelemetry` and
  `ReadyFrame::stageTimingTelemetry`.
- `MainWindow` stores the presented frame's telemetry in
  `m_lastPresentedStageTimingTelemetry`.
- `runHeadlessPlaybackProfile()` merges those fields into each sample.
- Existing sample fields include `cadence_ms`, `latency_ms`,
  `engine_latency_ms`, `draw_frame_ready_*`, `render_thread_*`,
  `processed8_total_ms`, `llrawproc_total_ms`, and many explicit getter-based
  decode/recon/process fields.

`src/debug/StageTiming.h` is not a global telemetry database. Its snapshot is a
`static` thread-local object in a header, so every translation unit gets its
own snapshot. Phase 3 must not try to harvest `video_mlv.c` or `llrawproc.c`
stage labels from `RenderFrameThread.cpp`. Use explicit getters or explicit
per-stage event emission.

Phase B may add event CSV telemetry, but the schema must be explicit:

```text
frame_idx,request_serial,slot,stage,event,ns,phase3_mode,clip_generation
```

Derived `summary.json` can compute:

- `inter_present_ms` from consecutive display/present completion events.
- `drop_count` from stale/drop decisions.
- Per-stage p50/p95/p99/max.
- Queue wait p50/p95/p99/max.

Top-line gates consume presented-frame cadence after warmup, not raw compute
latency.

## 11. CLI And Test Reality

Do not invent command-line flags. Live surfaces are:

| Binary / helper | Supported surface |
|---|---|
| `MLVApp.exe --profile-playback` | Real. Requires `--input` and `--output`; supports `--frames`, `--start-frame`, `--threads`, `--scope`, `--playback-debayer`, `--playback-processing`, `--raw-cache-mb`, `--cache-cpu-cores`, `--fast-open`, GPU flags, `--show-window`, `--wait-for-paint`, and `--stage-log`. |
| `MLVApp.exe --trim-mlv` | Real. Trim mode supports `--input`, `--output`, `--cut-in`, `--cut-out`, `--frame-count`, `--describe-input`, and `--with-audio`. |
| `console_tests.exe` | Supports `--hash-output` and `--check-golden [path]`. No real filter mode. |
| `pipeline_tests.exe` | Supports `--hash-output` and `--check-golden [path]`. No real filter mode. |
| `perf_tests.exe` | Supports `--iterations`, `--threads`, `--json-output`, `--baseline`, `--baseline-profile`, `--stage-log`, `--cold-8bit`, `--raw-cache-mb`, `--cache-cpu-cores`, `--extra-*`, `--regression-pct`, `--update-baseline`, `--require-baseline`, `--stage-timing`, and `--help`. |
| `.claude-state/scripts/run-mlvapp.ps1` | Deterministic runtime wrapper for launching `MLVApp.exe` with the correct Qt/MinGW path. |
| `tests/perf/run_runtime_profile.ps1` | Wraps `perf_tests`, not `MLVApp --profile-playback`. Phase B changes its default output root to `.claude-state\profiling\...` and rejects `.claude\...` scratch paths. |

`run-pipeline-tests.ps1 -Filter` currently maps to `--gtest_filter`, but the
minitest harness does not consume that as a filter. Do not rely on filtered
console/pipeline runs until real filter support exists.

## 12. Display And Buffer Lifetime

`ReadyFrame` contains borrowed pointers into slot-owned vectors. The GUI path
must release by request serial after every success, failure, stale drop, or
replacement.

Known live-code hazards Phase 3 must model:

- `QImage(..., Format_RGB888)` constructors without an explicit stride still
  exist in prep paths. Tight RGB888 buffers whose `width * 3` is not 4-byte
  aligned can be read incorrectly by Qt. Use `playbackWrapRgb8Image()` or
  explicit `bytesPerLine` for borrowed RGB8 buffers.
- `PlaybackPrepResult::preparedImage` is copied into an aligned buffer before
  GUI presentation, then wrapped with explicit stride. That path is safer.
- GPU display handoff copies input into viewport-owned bytes; it does not keep
  slot pointers after the call returns.
- Scopes consume synchronously.
- Scaled render dimensions are not currently carried through `ReadyFrame`.
  `ReadyFrame` has pointer fields and pre-scaled display dimensions, but it
  does not have source `rawImage8Width/rawImage8Height/stride` fields. Any
  Phase 3 mode that renders less than full source dimensions must add explicit
  dimensions/stride before enabling GPU, zebras, scopes, or non-prescaled paths.

`Presenting` lifetime rule:

1. Mark a slot `Presenting` before returning borrowed pointers to GUI/prep.
2. Keep it non-free while any prep or GUI path can read those pointers.
3. Release by `requestSerial`, not by a single global presenting index.
4. On replacement/stale drop, release the superseded serial exactly once.
5. `m_presentingSlotIndex` is legacy compatibility, not the Phase 3 truth.

## 13. llrawproc And Dual ISO Boundary

The recon boundary is pre-debayer, single-channel Bayer16:

```text
decode/unpack -> raw Bayer16 -> applyLLRawProcObject -> post-llrawproc Bayer16 -> debayer/process
```

`getMlvRawFrameProcessedUint16()` decodes raw then calls
`applyLLRawProcObject()` on the caller-provided `uint16_t` Bayer buffer.
`applyLLRawProcObject()` mutates the buffer and may publish Dual ISO runtime
state back to shared `video->llrawproc`.

Dual ISO modes:

| Mode | Meaning |
|---|---|
| `0` | off |
| `1` | full 20-bit / HQ path |
| `2` | preview rowscale path |

Playback policy may force valid Dual ISO playback into preview mode. HQ
playback keeps mode 1 and applies its own policy flags. The Phase 3 recon
stage must record which effective mode it used.

Scaled llrawproc is a narrow HQ-oriented subset. It skips some raw-fix features
and has no preview rowscale branch. Do not route Dual ISO preview mode into a
scaled path unless Phase B/D explicitly verifies the mode and dimensions.

## 14. Memory Budget

Let:

- `P = width * height` source pixels.
- `S = playback scale factor` (`1`, `2`, or `4`).
- `Ps = max(1, width / S) * max(1, height / S)` for scaled RGB stages.

Worst-case full-source per-slot buffers:

| Buffer | Bytes |
|---|---|
| `raw16` Bayer | `2 * P` |
| `recon16` Bayer | `2 * P` |
| `processed16` RGB | `6 * P` |
| `processed8` RGB | `3 * P` |
| `playbackScaledImage8` / display RGB | up to `3 * P` or target display size |

Conservative scale-1 slot budget is about `16 * P` bytes before vector
capacity overhead and Qt-side display copies. At 4096x2160, that is about
142 MB per slot, or about 568 MB for four fully populated slots.

If Phase 3 keeps raw/recon full size but scales RGB stages by `S=4`, the slot
budget is roughly:

```text
4 * P + 12 * Ps bytes
```

At 4096x2160 and `S=4`, that is about 42 MB per slot, or 168 MB for four
slots, before Qt-side copies. This is a budget estimate, not permission to
drop dimensions from metadata. Every slot must carry actual dimensions,
stride, format, and scale.

## 15. Invariants

These invariants are written so Phase B/D can turn them into `Q_ASSERT` or
test checks.

| ID | Invariant |
|---|---|
| INV-1 | A slot has exactly one lifecycle owner at a time. |
| INV-2 | No expensive compute runs while holding the parent render mutex. |
| INV-3 | A worker never publishes a slot if `clipGeneration` changed during compute. |
| INV-4 | A worker never publishes a slot if its `slotGeneration` no longer matches. |
| INV-5 | A presented slot has `requestSerial >= m_lastPresentedSerial` for the same clip generation. |
| INV-6 | Ready slots older than the latest acquired serial are released or marked stale before another `frameReady` can present them. |
| INV-7 | `Presenting` slots are never selected by free-slot allocation. |
| INV-8 | `Presenting` slots are released by request serial after every success, failure, stale drop, and replacement path. |
| INV-9 | `ReadyFrame` borrowed pointers are not stored past release. |
| INV-10 | Slot buffers include dimensions, stride, format, scale, request serial, slot generation, and clip generation. |
| INV-11 | llrawproc is called only in the recon stage for a slot. |
| INV-12 | No post-llrawproc/recon-output cache hit may skip llrawproc runtime side effects. |
| INV-13 | Raw uint16 prefetch remains pre-llrawproc and disabled during LJ92 profiling modes. |
| INV-14 | Processed8/processed16 cache generations remain separate from render slot generations. |
| INV-15 | Auto-fallback uses the live fallback atomic, not runtime env mutation. |
| INV-16 | Experimental Phase 3 modes are unavailable unless the explicit dogfood visibility setting is enabled. |
| INV-17 | `cadence_ms` / inter-present time is the primary performance gate. |
| INV-18 | New CLI/wrapper assumptions must be checked against the current parser or wrapper source before being written into a plan. |
| INV-19 | `StageTiming.h` snapshots are not treated as cross-TU telemetry. |
| INV-20 | A clip switch or destructive receipt reload invalidates all outstanding slots and prep work by `clipGeneration`. |
| INV-21 | Teardown signals workers, wakes them, joins them, then frees buffers and destroys mutexes/conditions. |
| INV-22 | Phase 3 never aliases render slot storage to existing mlvObject caches. |

## 16. Failure-Mode Catalog

| Failure | Symptom | Prevention |
|---|---|---|
| Stale frame presents after scrub | Old frame flashes after newer request | `requestSerial` high-water, stale ready-slot drain |
| Slot use-after-free | Crash or corrupted QImage/QPixmap | `Presenting` state and serial release |
| Deadlock | Playback stalls | Parent mutex only for state; no compute under lock |
| Env fallback race | Workers disagree about Phase 3 state | live atomic fallback, cached env only |
| llrawproc side effects skipped | wrong Dual ISO/DNG state after loop/scrub | no post-llrawproc hot-path cache |
| StageTiming false attribution | zeros or wrong stage fields | explicit getters / explicit event CSV |
| Tight RGB stride bug | skewed image or read past end | explicit bytesPerLine / aligned prepared image |
| Scaled source misinterpreted as full size | GPU/zebra/scope reads wrong dimensions | add source dimensions/stride to ReadyFrame before scaled modes |
| Test wrapper lies | day-1 unknown option or silent no-op | verify parser and wrapper source |
| Memory blowup | paging, OOM, stutter | slot budget and bounded queue depth |

## 17. Phase A Live-Code Verification Notes

The Phase A pass corrected these assumptions against live code:

- `MLVApp.exe --profile-playback --frames` is real in `platform/qt/main.cpp`;
  `perf_tests.exe --frames` is not. Use `--iterations` for `perf_tests`.
- `MLVApp.exe --trim-mlv` and trim-mode `--describe-input` are real in the
  trim parser, not profile mode.
- `console_tests.exe` and `pipeline_tests.exe` do not have real filter support.
- `PlaybackScaling.cpp` does not exist; playback scaling implementation is
  inline/header-based.
- `StageTiming.h` is per-translation-unit thread-local, so it cannot be used
  as a global stage snapshot source.
- The live render slot model can have multiple `presenting` slots even though
  `m_presentingSlotIndex` is single-index; serial-specific release is the
  durable model.
- The current `ReadyFrame` does not carry raw source dimensions/stride, which
  blocks safe scaled-source interpretation in some display paths.

## 18. Canonical Self-Review Checklist

Before every Phase 3 commit, the implementing agent must answer every item
below. If any answer is "no" or "not checked", stop and fix the design/code
before committing.

1. Did I read the live code touched by this commit, not only this document or
   old `.claude/analysis` notes?
2. Did I keep Phase A design-only unless the user explicitly opened Gate A->B?
3. Did I preserve the serial legacy path when Phase 3 is disabled?
4. Did I avoid reviving llrawproc/recon-output caching on the linear playback
   hot path?
5. Did I keep raw uint16 prefetch pre-llrawproc, separate from render slots,
   and disabled when LJ92 profiling env vars request caller-thread telemetry?
6. Did I keep processed8/processed16 cache generations separate from render
   slot generations?
7. Does each slot have exactly one lifecycle owner at a time?
8. Did every slot transition happen under the parent coordinator mutex?
9. Did expensive decode/recon/process/display work happen outside the parent
   mutex?
10. Did every worker publish only if `clipGeneration` and `slotGeneration` still
   match?
11. Did I avoid storing borrowed `ReadyFrame` pointers past
    `releasePresentedFrameForRequestSerial()`?
12. Did every success, failure, stale drop, and replacement path release the
    presenting slot by request serial?
13. Did I prevent stale serials from presenting after a newer serial?
14. Did I exclude every `Presenting` slot from free-slot allocation?
15. Did I add or preserve dimensions, stride, format, scale, request serial,
    slot generation, and clip generation for every buffer handoff?
16. Did I avoid relying on `m_presentingSlotIndex` as the only presentation
    truth?
17. Did I keep llrawproc calls inside the Recon stage only?
18. Did I avoid runtime environment mutation for fallback?
19. Does fallback use `phase3SetLiveFallbackActive(true)` and
    `phase3KillSwitchActive(mode)`?
20. Are experimental modes hidden unless the dogfood visibility setting is on?
21. Is cadence measured from presented frames after warmup, not from compute
    latency alone?
22. Did I verify every new CLI flag or wrapper option against live parser or
    script source?
23. Did I avoid treating `StageTiming.h` snapshots as cross-TU telemetry?
24. Did clip switch and destructive receipt reload invalidate outstanding
    slots/prep work through `clipGeneration`?
25. Does teardown wake workers, join workers, then free buffers and destroy
    mutexes/conditions?
26. Did I avoid aliasing render slot storage to mlvObject caches?
27. Did I keep scratch/profiling artifacts under `.claude-state/` and durable
    docs under `docs/`?
28. Did I add tests or a documented local verification plan appropriate to the
    phase gate?
29. Did I include rollback instructions (`RollbackEnv:` and/or
    `RollbackCommit:`) when the phase adds behavior?

## 19. Gate A Handoff And Phase B Scope

Gate A->B is closed until the user replies with the exact phrase
`Gate A->B open`. Phrases such as "looks good", "continue", "go ahead",
summaries after compaction, or agent self-ratings do not open the gate.

Phase A is 10/10 only when all of these are true:

1. This tracked doc exists under `docs/`.
2. The working checklist under `.claude-state/scripts/` matches Section 18.
3. The live-code verification pass has been completed and mismatches were
   fixed in this document, not in production code.
4. `.claude-state/user-context/phase3-plan/WAITING_FOR_USER.md` records that
   the gate is closed.
5. The user has reviewed this document or chosen to defer review.

If Gate A->B opens, Phase B scope is infrastructure only:

- Allowed: inert dispatcher scaffolding, kill-switch plumbing, telemetry
  sinks, checksum/parity harnesses, crash breadcrumbs, local wrapper scripts,
  and tests for those surfaces.
- Forbidden without a later gate: enabling overlap modes by default, changing
  normal playback behavior, exposing Phase 3 UI modes, adding worker-threaded
  pipeline execution, changing image output, reviving post-llrawproc caches,
  or shipping performance-gated behavior.

After Phase B completes, the agent must stop for the next explicit gate before
Phase C or Phase D behavior changes.
