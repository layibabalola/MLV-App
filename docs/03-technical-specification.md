# MLV App — Technical Specification

> **Version pinned**: 1.15.0.0 — `platform/qt/MLVApp.pro:450-460`.
> **Audience**: engineers, LLMs, and code reviewers who need enough detail to
> reconstruct the engine's public interface, data structures, threading model,
> and pipeline ordering from this document plus the source headers.
>
> This document is the deepest of the four headline docs. For narrative
> introductions use `docs/01-user-guide.md` and `docs/02-developer-guide.md`.
> For auditor-level verification pointers use `docs/04-external-auditor-guide.md`.

---

## 1. Abstract

MLV App is a cross-platform Qt application for decoding, grading, and
exporting Magic Lantern `.MLV` (and MCRAW) raw-video clips from Canon DSLRs.
The codebase is split into a platform-independent **engine** under `src/` and
a **Qt platform layer** under `platform/qt/`.

### 1.1 Core abstractions

| Abstraction | Type | Declared in |
|---|---|---|
| MLV clip state | `mlvObject_t` | `src/mlv/mlv_object.h:51-263` |
| Per-frame metadata | `frame_index_t` | `src/mlv/mlv_object.h:25-34` |
| Post-demosaic processing receipt + LUT cache | `processingObject_t` | `src/processing/processing_object.h:19+` |
| Raw-domain correction config | `llrawprocObject_t` | `src/mlv/llrawproc/llrawproc_object.h:80-155` |
| DNG writer | `dngObject_t` | `src/dng/dng.h:34-51` |
| Qt render worker contract | `RenderFrameThread::ReadyFrame` + `::PresentationContext` | `platform/qt/RenderFrameThread.h:36-85` |
| Qt headless-profile options | `MainWindow::PlaybackProfileOptions` | `platform/qt/MainWindow.h:98-122` |

### 1.2 Key invariants

1. **`processingObject_t` is the single source of truth for per-clip
   processing parameters.** Receipts serialise to/from `.marxml` via
   `platform/qt/batch/ReceiptLoader.cpp` and `ReceiptApplier.cpp`.
2. **`mlvObject_t` owns the decode/cache state**; the processing object is
   linked to it via `setMlvProcessing()` but ownership of memory remains
   separate. Both are torn down with matching `init*` / `free*` pairs.
3. **Stage timing is thread-local.** Every `getMlvLastRaw*Milliseconds()` /
   `processingGetLast*Milliseconds()` value lives in thread-local storage
   declared via the `MLV_STAGE_THREAD_LOCAL` macro
   (`src/debug/StageTiming.h:14-18`). A caller on thread A cannot read
   telemetry set on thread B.
4. **Immutable `PresentationContext` per request.** Since commit
   `244c03a1`, `RenderFrameThread::ReadyFrame` carries a frozen
   `PresentationContext` snapshot for the frame it emitted; the Qt paint
   path reads only that context, never live GUI state.
5. **Default-on decode-ahead prefetch.** The 4-slot `raw_uint16` prefetch
   worker is enabled by default; it is disabled by setting
   `MLVAPP_DISABLE_RAW_UINT16_PREFETCH=1` (or implicitly, when any LJ92
   profiling env var is set, so per-thread telemetry is not lost to the
   worker thread). See `src/mlv/video_mlv.c:190-225`.
6. **AVX2 direct-8-bit fast path is bit-identical to the scalar path** for
   the same receipt. Covered by `tests/console/test_avx_golden.cpp`.
7. **Receipts round-trip**: `setMlvProcessing` + `applyProcessingObject` +
   `saveDngFrame` must be reproducible across runs at the DNG byte level for
   a fixed receipt (`tests/console/test_clip_golden.cpp`).

### 1.3 Reading order for this document

Sections 2-4 set up context and data model. Sections 5-7 form the bulk of
the specification: public APIs, the authoritative frame pipeline, and the
threading model. Sections 8-13 cover orthogonal concerns (receipts, GPU
paths, CLI, env vars, telemetry, file formats). Sections 14-18 cover
invariants, extension points, and current WIP seams.

---

## 2. System context

```
  +------------------------------------------------+
  |  On disk                                        |
  |                                                 |
  |  clip.MLV + optional .M00/.M01 (LJ92 or raw)    |
  |  clip.MAPP (index sidecar, optional)            |
  |  receipt.marxml (processingObject snapshot)     |
  |  pixel_maps/*.fpm, *.bpm                        |
  |  dark_frame.MLV (optional, for --dark-frame)    |
  +------------------------------------------------+
            |                         |
            v                         v
  +--------------------+   +-------------------------+
  |  src/ ENGINE       |   |  src/batch/ (orchestrator) |
  |                    |   |  BatchContext + BatchRunner |
  |  video_mlv.c       |<->|  MlvTrim                 |
  |  llrawproc.c       |   +-------------------------+
  |  debayer.c         |           |
  |  raw_processing.c  |           |
  |  dng.c             |           v
  +--------------------+   +-------------------------+
            ^              |  CLI                     |
            |              |  --batch        -> DNG   |
            |              |  --trim-mlv     -> MLV   |
            |              |  --profile-playback -> JSON
            v              +-------------------------+
  +--------------------+
  | platform/qt/ GUI   |
  |                    |
  |  MainWindow        |----> scenes/widgets/scopes
  |  RenderFrameThread |----> QGraphicsPixmapItem
  |  GpuDisplayViewport|      or GpuDisplayViewport
  |  AudioPlayback     |----> QAudioOutput / QAudioSink
  |  ExportSettingsDialog --> ffmpeg (piped) / AVFoundation
  +--------------------+
```

For richer Mermaid versions see `docs/diagrams/`.

The engine has no direct Qt dependency; Qt is a **consumer** of
`mlvObject_t` and `processingObject_t`. Batch mode constructs those objects
without ever instantiating `QApplication`, which is why
`main.cpp:26-62` scans `argv` before `QApplication` is constructed.

---

## 3. Module map

This extends `.claude-state/docs-audit/01-src-architecture.md` §1 with
"who depends on this" notes so call-graph reasoning is possible from this
document alone.

| Path | Responsibility | Who depends on it |
|---|---|---|
| `src/mlv/` | MLV header parsing, frame indexing, cache state, prefetch worker, `.MAPP` sidecar | `platform/qt/MainWindow.cpp`, `src/batch/BatchRunner.cpp`, `src/dng/dng.c` |
| `src/mlv/liblj92/` | Lossless-JPEG decompression (predictors 1/6/generic; scalar + AVX2 fast path) | `src/mlv/video_mlv.c` only |
| `src/mlv/mcraw/` | MCRAW metadata (cJSON) + open path | `src/mlv/video_mlv.c` (via `openMcrawClip`) |
| `src/mlv/camid/` | Per-camera sensor matrix tables, compensation maps | `src/processing/raw_processing.c`, `src/mlv/video_mlv.c` |
| `src/mlv/llrawproc/` | Dark frame, focus/bad pixel remap, vertical stripes, dual ISO, pattern noise, chroma smooth | `src/mlv/video_mlv.c` (via `applyLLRawProcObject`) |
| `src/processing/` | 9-stage post-demosaic pipeline (WB, exposure, curves, denoise, RBF, CA, gamma) | `src/mlv/video_mlv.c`, `platform/qt/*Dialog.cpp`, `tests/pipeline/*` |
| `src/processing/filter/` | Neural-net film emulation (genann) | `src/processing/raw_processing.c` |
| `src/processing/denoiser/` | 2D median denoiser | `src/processing/raw_processing.c` |
| `src/processing/rbfilter/` | Recursive bilateral filter (edge-aware blur, clarity, sharpen) | `src/processing/raw_processing.c` |
| `src/processing/interpolation/` | Spline evaluation for user curves | `src/processing/raw_processing.c`, `platform/qt/Curves.cpp` |
| `src/processing/cafilter/` | Post-demosaic CA desaturation | `src/processing/raw_processing.c` |
| `src/processing/sobel/` | Edge mask for sharpening | `src/processing/raw_processing.c` |
| `src/processing/tinyexpr/` | User-supplied expression evaluator | `src/processing/raw_processing.c` |
| `src/debayer/` | Dispatcher: none / basic / AMaZe / AHD plus bridge to librtprocess | `src/mlv/video_mlv.c` |
| `src/librtprocess/` | Vendored demosaic library (LMMSE, DCB, RCD, IGV, Markesteijn) | `src/debayer/debayer.c` only |
| `src/dng/` | CDNG writer, bit packing, LJPEG codec | `src/batch/BatchRunner.cpp`, `platform/qt/ExportSettingsDialog.cpp`, `platform/qt/MainWindow.cpp` |
| `src/batch/` | CLI batch orchestration: context, runner, receipt loader/applier, trim | `platform/qt/main.cpp` |
| `src/matrix/` | 3×3 linear algebra | `src/processing/raw_processing.c` |
| `src/ca_correct/` | Pre-demosaic chromatic aberration helpers | `src/processing/raw_processing.c` |
| `src/debug/` | Thread-local stage-timing macros | all TUs opting into telemetry |
| `src/icon/` | Build-time icon data | build system only |
| `src/mlv_include.h` | Umbrella header pulling in everything | `platform/qt/*`, tests |
| `platform/qt/` | Qt 5/6 GUI, export orchestration, audio, scopes, CLI entrypoint | end users |
| `platform/qt/batch/` | Qt-side batch wiring (prompts, receipt I/O, types, logger) | `main.cpp`, `BatchRunner` |
| `platform/qt/avir/` | Vendored AVIR image resizer | `RenderFrameThread.cpp` |
| `platform/qt/maddy/` | In-app Markdown viewer backend | docs dialog |
| `platform/cocoa/` | Deprecated Cocoa GUI; AVFoundation export lib still used on macOS | macOS Qt build |

---

## 4. Data model

Each major struct is given with its declaration location, public-ish
fields, lifecycle, invariants, and ownership. Private scratch fields are
called out only when they affect a public contract.

### 4.1 `mlvObject_t`

- **Declared in**: `src/mlv/mlv_object.h:51-263`
- **Init / free**: `initMlvObject()` / `freeMlvObject(mlvObject_t *)`
  (`src/mlv/video_mlv.h:19, :42`). Caller owns.
- **Opening a clip**: `openMlvClip(v, path, mode, err)` or
  `openMcrawClip(v, path, mode, err)` (`src/mlv/video_mlv.h:27-28`). Modes:
  `MLV_OPEN_FULL`, `MLV_OPEN_MAPP`, `MLV_OPEN_PREVIEW`. Errors in
  `enum mlv_err` — `MLV_ERR_NONE/OPEN/IO/CORRUPTED/INVALID`.

| Field | Type | Notes |
|---|---|---|
| `file[]` | `FILE **` | One handle per `.MLV`/`.M00`/`.M01`... segment |
| `main_file_mutex[]` | `pthread_mutex_t *` | One per segment; protects sequential `fseek`+`fread` |
| `g_mutexFind`, `g_mutexCount` | `pthread_mutex_t` | "Pink-frame" prevention during concurrent cache churn |
| `MLVI`, `RAWI`, `RAWC`, `IDNT`, `EXPO`, `LENS`, `ELNS`, `RTCI`, `WBAL`, `WAVI`, `DISO`, `INFO`, `STYL`, `VERS`, `DARK`, `VIDF`, `AUDF` | `mlv_*_hdr_t` | Copies of the latest seen header blocks |
| `video_index`, `audio_index`, `vers_index` | `frame_index_t *` | Monotonic index arrays, one entry per block |
| `frames`, `audios`, `vers_blocks` | `uint32_t` | Counts for the three index arrays |
| `audio_data`, `audio_size`, `audio_buffer_size` | `uint8_t *`, `uint64_t` | Preread WAV-ready audio buffer |
| `processing` | `processingObject_t *` | Not owned (see `setMlvProcessing`) |
| `llrawproc` | `llrawprocObject_t *` | Owned; allocated inside `initMlvObject` |
| `llrawproc_workers`, `llrawproc_worker_mutex` | pool | Per-thread working copies of pixel maps + dark frame |
| `cached_frames`, `rgb_raw_frames`, `cache_memory_block` | `uint8_t *`, `uint16_t **`, `uint16_t *` | Windowed frame cache |
| `current_cached_frame*`, `current_processed_frame*` | `uint8_t/uint64_t` | Single-hot-frame caches for repeat requests |
| `processed_8bit_cache_*[MLV_PROCESSED_8BIT_CACHE_SLOTS]` | arrays | 8-slot processed-8-bit cache |
| `processed_16bit_cache_*[MLV_PROCESSED_16BIT_CACHE_SLOTS]` | arrays | 2-slot processed-16-bit cache |
| `raw_uint16_prefetch_*` | mutex/cond/thread + 4-slot ring | Default-on raw decode-ahead |
| `processed8_prefetch_*` | mutex/cond/thread | Gated `MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH=1` |
| `cache_limit_bytes`, `cache_limit_frames`, `cache_limit_mb` | `uint64_t` | Three shapes of cap for raw-RGB cache |
| `use_amaze` | `int` | Sentinel for debayer algorithm (see `macros.h:80-98`) |
| `ca_red`, `ca_blue` | `float` | Pre-demosaic CA compensation |
| `cpu_cores` | `int` | Suggested worker count; default 4 |

**Cache-state constants** (`mlv_object.h:16-22`):
- `MLV_FRAME_NOT_CACHED=0`, `IS_CACHED=1`, `BEING_CACHED=2`
- `MLV_PROCESSED_8BIT_CACHE_SLOTS=8`, `..._16BIT_CACHE_SLOTS=2`
- `MLV_RAW_UINT16_PREFETCH_SLOTS=4`

**Ownership**: `freeMlvObject` closes file handles, frees index arrays,
frees `llrawproc`, frees cache memory. It does **not** free
`processing`. Callers that `setMlvProcessing(video, processing)` must
still call `freeProcessingObject(processing)` themselves.

### 4.2 `frame_index_t`

- **Declared in**: `src/mlv/mlv_object.h:25-34`

| Field | Type | Meaning |
|---|---|---|
| `frame_type` | `uint16_t` | `VIDF=1`, `AUDF=2`, `VERS=3` |
| `chunk_num` | `uint16_t` | Which segment file the block lives in |
| `frame_number` | `uint32_t` | Monotonic frame id |
| `frame_size` | `uint32_t` | Payload size in bytes |
| `frame_offset` | `uint64_t` | Offset to payload (for `fread`) |
| `frame_time` | `uint64_t` | Microseconds from recording start |
| `block_offset` | `uint64_t` | Offset to the block header |

### 4.3 `processingObject_t`

- **Declared in**: `src/processing/processing_object.h:19-` (400+ LOC)
- **Init / free**: `initProcessingObject()` / `freeProcessingObject()` in
  `src/processing/raw_processing.h:11-13`.
- **Link to clip**: `setMlvProcessing(video, processing)`.

| Field group | Representative members | Notes |
|---|---|---|
| Profile | `image_profile`, `exr_mode`, `AgX`, `use_cam_matrix`, `allow_creative_adjustments`, `highlight_reconstruction` | Top-level mode switches |
| Filter/LUT | `filterObject_t *filter`, `filter_on`, `lut_t *lut`, `lut_on`, `lut_strength` | Neural-net filter + 3D LUT |
| WB picker | `wbFindActive`, `wbR/G/B` | Hot path for picker mode |
| Black/white levels | `black_level`, `white_level` | Floats/ints (see `processingSetBlackLevel`/`processingSetWhiteLevel`) |
| Curves | `gcurve_y/r/g/b[65536]` | User gradation curves |
| Matrices | `cam_matrix[9]`, `cam_matrix_A[9]`, `proper_wb_matrix[9]`, `final_matrix[9]`, `pre_calc_matrix[9]`, `pre_calc_matrix_gradient[9]` | Camera→sRGB pipeline |
| Shadows/Highlights | `shadows_highlights.highlights/shadows`, `shadow_highlight_curve[65536]`, `blur_image` | Local-contrast prep buffer |
| HSL | `hue_vs_hue[36000]`, `hue_vs_saturation[36000]`, `hue_vs_luma[36000]`, `luma_vs_saturation[36000]` plus `*_used` flags | 0.01° resolution |
| Toning | `toning_dry`, `toning_wet[3]` | RGB ratio tone |
| WB | `kelvin`, `wb_tint`, `wb_multipliers[3]` | 2500-10000 K, tint -10..+10 |
| Core sliders | `exposure_stops`, `saturation`, `vibrance`, `contrast`, `pivot`, `contrast_curve[65536]`, `clarity`, `clarity_curve[65536]` | Main grading params |
| S-curve | `light_contrast_factor/range`, `dark_contrast_factor/range`, `lighten` | |
| 3-way | `highlight_hue/sat`, `midtone_hue/sat`, `shadow_hue/sat` | |
| Gamma | `gamma_power`, `pre_calc_gamma[65536]`, `pre_calc_gamma_gradient[65536]` | |
| Sharpen | `sharpen`, `sharpen_bias`, `sh_masking`, `pre_calc_sharp_a/x/y[65536]` | |
| Saturation/Vibrance | `pre_calc_sat[131072]`, `pre_calc_vibrance[131072]` | 17-bit signed LUTs |
| Denoise | `denoiserWindow`, `denoiserStrength`, `denoise_2d_median_context_t *denoiser_context` | |
| RBF | `rbfDenoiserLuma/Chroma/Range` | |
| Grain | `grainStrength`, `grainLumaWeight` | |
| Gradient | `gradient_exposure_stops`, `gradient_contrast`, `gradient_contrast_curve[65536]`, `gradient_enable` | |
| CA | `ca_desaturate`, `ca_radius` | Post-demosaic |
| Dual ISO link | `int *dual_iso` | Pointer **into** `llrawprocObject_t` |

**Invariants**:
- All 65 536-entry LUTs are rebuilt lazily when the relevant setter changes
  value; see `processing_update_curves()`,
  `processing_update_contrast_curve()`, `processing_update_matrices()`.
- `final_matrix` is derived from `cam_matrix` + WB + exposure.
  `processing_update_matrices()` must be called after any of those change
  (the setters do this automatically).
- `dual_iso` is **not owned**; it points into the linked
  `llrawprocObject_t::dual_iso` so the processing pipeline can decide
  whether to run `analyse_frame_highest_green`.

### 4.4 `llrawprocObject_t`

- **Declared in**: `src/mlv/llrawproc/llrawproc_object.h:80-155`
- **Init / free**: `initLLRawProcObject()` / `freeLLRawProcObject(mlvObject_t *)`
  (`src/mlv/llrawproc/llrawproc.h:27-28`).
- Owned by `mlvObject_t::llrawproc`; freed by `freeMlvObject`.

| Field | Type | Purpose |
|---|---|---|
| `fix_raw` | int (`FR_OFF/FR_ON`) | Master enable |
| `vertical_stripes` | int (`VS_OFF/VS_ON/VS_FORCE`) | Canon column banding correction |
| `focus_pixels`, `fpi_method`, `fpm_status` | int (`FP_OFF/FP_ON/FP_CROPREC`) | Phase-detect pixel remap |
| `bad_pixels`, `bps_method`, `bpi_method`, `bpm_status` | int (`BP_OFF/BP_ON/FP_AGGRESSIVE`) | Dead/stuck pixel remap |
| `chroma_smooth` | int (`CS_OFF/CS_2x2/CS_3x3/CS_5x5`) | Post-demosaic sensor noise |
| `pattern_noise` | int (`PN_OFF/PN_ON`) | Row/column pattern removal |
| `deflicker_target` | int | Target ISO percentile |
| `diso_validity` | int (`DISO_INVALID/FORCED/VALID`) | Signals whether a DISO block was seen |
| `dual_iso` | int (`DISO_OFF/DISO_20BIT/DISO_FAST`) | 0=off, 1=full 20-bit reconstruction, 2=preview |
| `diso_auto_correction`, `diso_ev_correction`, `diso_black_delta` | | EV / black-level solve controls |
| `diso_averaging` | int (`DISOI_AMAZE/DISOI_MEAN23`) | Interpolation choice |
| `diso_alias_map`, `diso_frblending` | int | Alias map + full-res blending toggles |
| `dark_frame` | int (0 off / 1 ext / 2 int) | Dark frame subtraction mode |
| `dng_bit_depth`, `dng_black_level`, `dng_white_level` | int | CDNG export target |
| `dark_frame_filename`, `dark_frame_data`, `dark_frame_size`, `dark_frame_version` | | External dark-frame clip state |
| `raw2ev`, `ev2raw` | int *LUTs | Log-domain lookups (rebuilt on black-level change) |
| `prev_black_level` | int32_t | Drives LUT refresh |
| `focus_pixel_map`, `bad_pixel_map` | `pixel_map` | Copy-on-write main-thread maps |
| `stripe_corrections` | `stripes_correction` | Cached vertical-stripe coefficients |

**Worker state**: `llrawprocWorkerState_t`
(`llrawproc_object.h:44-77`) holds per-thread `raw2ev/ev2raw`, per-thread
copies of the pixel maps (with version tags), per-thread dark-frame copy,
and per-thread chroma-smooth / dual-iso scratch buffers. This is the
documented mechanism for keeping the hot decode path lock-light.

### 4.5 `dngObject_t`

- **Declared in**: `src/dng/dng.h:34-51`
- **Init / free**: `initDngObject(mlvObject_t *, raw_state, fps, par[4])` /
  `freeDngObject()`.

| Field | Type | Purpose |
|---|---|---|
| `fps_float`, `par[4]` | double, int32_t[4] | User fps override + pixel aspect ratio |
| `raw_input_state` / `raw_output_state` | int | 0=uncompressed, 1=lossless, 2=pass uncompressed, 3=pass lossless |
| `header_size`, `header_buf` | size_t, uint8_t * | TIFF DNG header |
| `image_size`, `image_buf` | size_t, uint16_t * | Output image buffer (packed) |
| `image_buf2` | uint16_t * | Temporary decompression buffer |
| `image_buf_unpacked`, `image_size_unpacked` | uint16_t *, size_t | Bit-unpacked working buffer |

`UNCOMPRESSED_RAW=0`, `COMPRESSED_RAW=1`, `UNCOMPRESSED_ORIG=2`,
`COMPRESSED_ORIG=3`.

### 4.6 Audio (`mlvAudioObject_t`)

MLV App does not define a dedicated `mlvAudioObject_t`. Audio state lives
inline on `mlvObject_t` (`audio_data`, `audio_size`, `audio_index`,
`WAVI`). The audio API in `src/mlv/audio_mlv.h` is:

- `void readMlvAudioData(mlvObject_t *video)` — fills `audio_data` + size.
- `void writeMlvAudioToWave(mlvObject_t *video, char *path)` — full BWAV
  export.
- `void writeMlvAudioToWaveCut(mlvObject_t *, char *path, uint32_t in, uint32_t out)`
  — cut export.

### 4.7 `RenderFrameThread::ReadyFrame`

- **Declared in**: `platform/qt/RenderFrameThread.h:36-85`

| Field | Type | Purpose |
|---|---|---|
| `rawImage8`, `rawImage16` | `const uint8_t/uint16_t *` | Output buffer (slot-owned) |
| `playbackScaledImage8` | `const uint8_t *` | Optional fast-downscaled 8-bit preview |
| `frameNumber`, `requestSerial` | uint32_t, uint64_t | Correlates with render request |
| `outputMode` | `OutputMode` | `OutputProcessed8/16/Debayered16` |
| `playbackFastScaleActive`, `playbackScaledWidth/Height` | | Fast-scale metadata |
| `usedGpuBilinearDebayer`, `gpuBilinearFallbackReason`, `gpuBilinearRendererDescription` | bool/QString | GPU path observability |
| `dualIsoPreviewHistogramMs/RegressionMs/RowscaleMs` | double | Dual ISO preview sub-stages |
| `frameReadyEmitStageTime` | double | `omp_get_wtime()` at emit |
| `presentationContext` | `PresentationContext` | Immutable snapshot (see 4.8) |
| `processedFrame8Active/Signature`, `processedFrame16Active/Signature` | | Processed-frame cache receipts |
| `dualIsoPattern/AutoCorrection/EvCorrection/BlackDelta` | | Replayed dual ISO state |
| `stageTimingTelemetry` | `QJsonObject` | Full stage-timing field set (see §12) |

### 4.8 `RenderFrameThread::ReadyFrame::PresentationContext`

- **Declared in**: `platform/qt/RenderFrameThread.h:38-57`

All fields are captured at the time the render request is enqueued and
never modified thereafter (commit `244c03a1`).

| Field | Type | Purpose |
|---|---|---|
| `requestSerial` | uint64_t | Correlation ID |
| `frameNumber` | uint32_t | |
| `sceneWidth/Height`, `imageWidth/Height` | int | Qt geometry snapshot |
| `devicePixelRatioMilli` | int | DPR × 1000 |
| `zoomFitEnabled` | bool | |
| `fastPlaybackScaleEligible` | bool | |
| `gpuPreviewPolicy` | `MainWindowGpuPreviewPolicyState` | |
| `gpuPresentationOptions` | `GpuDisplayViewport::PresentationOptions` | |
| `gpuPreviewProcessingConfig` | `GpuPreviewProcessingConfig` | |
| `playbackProcessingReason` | QString | Human-readable policy trace |
| `renderThreadUsing16BitPreview`, `...UsingGpuPreviewProcessing`, `...UsingGpuBilinearDebayer`, `...UsingCpuPreviewProcessing` | bool | Policy evidence for telemetry |

### 4.9 `MainWindow::PlaybackProfileOptions`

- **Declared in**: `platform/qt/MainWindow.h:98-122`

| Field | Type | Default | Purpose |
|---|---|---|---|
| `inputPath`, `receiptPath`, `outputPath` | QString | "" | CLI args |
| `startFrame`, `frameCount` | int | 0 | Zero-based window |
| `workerThreads`, `forceWorkerThreads` | int, bool | 1, true | Thread gate |
| `rawCacheMB` | uint64_t | 0 | Raw cache cap |
| `cacheCpuCores` | int | 1 | Cache workers |
| `zebras`, `fastOpen`, `showWindow`, `waitForPaint` | bool | false | Toggles |
| `scope` | `PlaybackProfileScope` | None | `None/Histogram/Waveform/Parade/Vectorscope` |
| `playbackDebayer` | `PlaybackProfileDebayerRequest` | Auto | `Auto/Receipt/None/Simple/Bilinear/LMMSE/IGV/AMaZE/AHD/RCD/DCB/AmazeCached` |
| `playbackProcessing` | `PlaybackProfileProcessingRequest` | Auto | `Auto/Receipt/Subset` |
| `gpuPreviewProcessingBackend` | `GpuPreviewProcessingBackendRequest` | Auto | `Auto/Cpu/Gpu` |
| `gpuBilinearDebayerBackend` | `GpuBilinearDebayerBackendRequest` | Auto | `Auto/Cpu/Gpu` |

---

## 5. Public engine API surface

Grouped by module. One-line behavior descriptions; full contracts live in
the headers cited. Cross-reference `src/mlv_include.h` (umbrella).

### 5.1 MLV I/O — `src/mlv/video_mlv.h`

```c
mlvObject_t * initMlvObject(void);
mlvObject_t * initMlvObjectWithClip(char *mlvPath, int preview,
                                    int *err, char *error_message);
mlvObject_t * initMlvObjectWithMcrawClip(char *mcrawPath, int preview,
                                         int *err, char *error_message);

int openMlvClip (mlvObject_t *video, char *mlvPath,   int open_mode, char *error_message);
int openMcrawClip(mlvObject_t *video, char *mcrawPath, int open_mode, char *error_message);

void freeMlvObject(mlvObject_t *video);
void printMlvInfo (mlvObject_t *video);

void setMlvProcessing(mlvObject_t *video, processingObject_t *processing);
void findMlvWhiteBalance(mlvObject_t *video, uint64_t frameIndex,
                         int posX, int posY,
                         int *wbTemp, int *wbTint, int mode);

int saveMlvHeaders (mlvObject_t *video, FILE *output_mlv,
                    int export_audio, int export_mode,
                    uint32_t frame_start, uint32_t frame_end,
                    const char *version, char *error_message);
int saveMlvAVFrame (mlvObject_t *video, FILE *output_mlv,
                    int export_audio, int export_mode,
                    uint32_t frame_start, uint32_t frame_end,
                    uint32_t frame_index, uint64_t *avg_buf,
                    char *error_message);
/* export modes: MLV_FAST_PASS, MLV_COMPRESS, MLV_DECOMPRESS,
 *               MLV_AVERAGED_FRAME, MLV_DF_INT                */
```

**Cache controls**:

```c
void disableMlvCaching(mlvObject_t *video);
void enableMlvCaching (mlvObject_t *video);
void resetMlvCache    (mlvObject_t *video);
void invalidateMlvProcessedPreviewCache(mlvObject_t *video);
void setMlvRawCacheLimitMegaBytes(mlvObject_t *video, uint64_t mb);
void setMlvRawCacheLimitFrames   (mlvObject_t *video, uint64_t frames);
void mlv_cache_request_playback_preroll(mlvObject_t *video,
                                        uint64_t currentFrame,
                                        uint64_t lastFrameInclusive,
                                        uint64_t lookaheadFrames);
```

### 5.2 Frame fetch — `src/mlv/video_mlv.h`

```c
/* Entry points */
void getMlvProcessedFrame8 (mlvObject_t *video, uint64_t frameIndex,
                            uint8_t  *outputFrame, int threads);
void getMlvProcessedFrame16(mlvObject_t *video, uint64_t frameIndex,
                            uint16_t *outputFrame, int threads);

/* Lower-level getters */
int  getMlvRawFrameUint16   (mlvObject_t *video, uint64_t frameIndex,
                             uint16_t *unpackedFrame);
int  getMlvRawFrameProcessedUint16(mlvObject_t *video, uint64_t frameIndex,
                                   uint16_t *outputFrame, int *bit_shift);
void getMlvRawFrameFloat    (mlvObject_t *video, uint64_t frameIndex,
                             float *outputFrame);
void getMlvRawFrameDebayered(mlvObject_t *video, uint64_t frameIndex,
                             uint16_t *outputFrame);
int  create_thumbnail       (mlvObject_t *video, uint8_t *thumbnail_img,
                             int downscaled_factor, int width, int height,
                             int threads);
```

**Telemetry getters** (all thread-local — see §7):

```c
/* Total raw-uint16 pipeline timing */
double getMlvLastRawUint16Milliseconds(void);
double getMlvLastRawUint16DiskReadMilliseconds(void);
double getMlvLastRawUint16DecompressMilliseconds(void);
double getMlvLastRawUint16DecompressPrepareMilliseconds(void);
double getMlvLastRawUint16DecompressExecuteMilliseconds(void);
double getMlvLastRawUint16UnpackMilliseconds(void);
double getMlvLastRawUint16CopyMilliseconds(void);

/* LJ92 predictor split */
int    getMlvLastRawUint16Lj92Pred1FastPathActive(void);
int    getMlvLastRawUint16Lj92Pred1FastPathEligible(void);
int    getMlvLastRawUint16Lj92Pred1FastPathMeasurementRequested(void);
int    getMlvLastRawUint16Lj92Pred1FastPathMeasurementActive(void);
double getMlvLastRawUint16Lj92Pred1FastPathTotalMilliseconds(void);
double getMlvLastRawUint16Lj92Pred1FastPathBitstreamMilliseconds(void);
double getMlvLastRawUint16Lj92Pred1FastPathPredictorMilliseconds(void);
int    getMlvLastRawUint16Lj92Pred6SplitActive(void);
int    getMlvLastRawUint16Lj92Pred6SplitRequested(void);
double getMlvLastRawUint16Lj92Pred6TotalMilliseconds(void);
double getMlvLastRawUint16Lj92Pred6BitstreamMilliseconds(void);
double getMlvLastRawUint16Lj92Pred6PredictorMilliseconds(void);
int    getMlvLastRawUint16Lj92GenericSplitActive(void);
int    getMlvLastRawUint16Lj92GenericSplitRequested(void);
double getMlvLastRawUint16Lj92GenericTotalMilliseconds(void);
double getMlvLastRawUint16Lj92GenericBitstreamMilliseconds(void);
double getMlvLastRawUint16Lj92GenericPredictorMilliseconds(void);
int    getMlvLastRawUint16Lj92Predictor(void); /* 1/6/other */
int    getMlvLastRawUint16Lj92ScanComponentCount(void);
int    getMlvLastRawUint16Lj92ComponentCount(void);
int    getMlvLastRawUint16Lj92WriteLength(void);
int    getMlvLastRawUint16Lj92ExpectedWriteLength(void);
int    getMlvLastRawUint16Lj92SkipLength(void);
int    getMlvLastRawUint16Lj92LinearizeActive(void);

/* Prefetch and cache hit */
int      getMlvLastRawUint16PrefetchHit(void);
uint64_t getMlvRawUint16PrefetchDecodeFailures(mlvObject_t *video);

/* llrawproc + debayer + processing */
double getMlvLastLlrawprocMilliseconds(void);
double getMlvLastRawFloatConvertMilliseconds(void);
double getMlvLastDebayeredFrameMilliseconds(void);
void   resetMlvLastDebayerStageMilliseconds(void);
double getMlvLastDebayerWbPrepareMilliseconds(void);
double getMlvLastDebayerCaMilliseconds(void);
double getMlvLastDebayerKernelMilliseconds(void);
double getMlvLastDebayerWbUndoMilliseconds(void);
double getMlvLastProcessingMilliseconds(void);
double getMlvLastProcessed16TotalMilliseconds(void);
double getMlvLastProcessed16For8BitMilliseconds(void);
double getMlvLastProcessed16To8BitMilliseconds(void);
double getMlvLastProcessed8TotalMilliseconds(void);
int    getMlvLastProcessed8DirectPathActive(void);
int    getMlvLastProcessed8PrefetchHit(void);
```

> All `getMlvLastRawUint16*`, `getMlvLastLlrawproc*Milliseconds`,
> `getMlvLastDebayer*`, and `getMlvLastProcessed*` getters return
> thread-local state set on the thread that performed the decode/process.
> If the call chain crosses threads (including the prefetch worker when
> enabled) the snapshot may be stale on the caller's thread. This is why
> the profiling env vars disable the prefetch worker (see §7).

### 5.3 Processing — `src/processing/raw_processing.h`

```c
processingObject_t * initProcessingObject(void);
void                 freeProcessingObject(processingObject_t *p);

void applyProcessingObject (processingObject_t *p, int w, int h,
                            uint16_t *in, uint16_t *out,
                            int threads, int imageChanged, uint64_t frameIndex);
void applyProcessingObject8(processingObject_t *p, int w, int h,
                            uint16_t *in, uint8_t  *out,
                            int threads, int imageChanged, uint64_t frameIndex);
void processingGetFloatOutputForEXR(processingObject_t *p, int w, int h,
                                    uint16_t *in, float *out,
                                    int threads, int imageChanged, uint64_t frameIndex);

int  processingCanUseDirect8BitOutput(const processingObject_t *p);
int  processingFastPathAvx2Active    (void);
void processingResetLastTimingTelemetry(void);
```

Representative setters (full list ~150 entries; see header):

```c
void processingSetTemperature       (processingObject_t *p, int temp);  /* macro */
void processingSetWhiteBalance      (processingObject_t *p, double K, double tint);
void processingSetWhiteBalanceKelvin(processingObject_t *p, double K);
void processingSetWhiteBalanceTint  (processingObject_t *p, double tint);
void processingSetExposureStops     (processingObject_t *p, double stops);
void processingSetGCurve            (processingObject_t *p, int n,
                                     float *Xin, float *Yin, uint8_t channel);
void processingSetHueVsCurves       (processingObject_t *p, int n,
                                     float *Xin, float *Yin, uint8_t channel);
void processingSetTonemappingFunction(processingObject_t *p, int function);
int  processingSetTransferFunction  (processingObject_t *p, char *function);
void processingSetGamma             (processingObject_t *p, double gamma);
void processingSetSharpening        (processingObject_t *p, double amount);
void processingSetSharpeningBias    (processingObject_t *p, double bias);
void processingSetContrast          (processingObject_t *p,
                                     double DCRange, double DCFactor,
                                     double LCRange, double LCFactor,
                                     double lighten);
void processingSetClarity           (processingObject_t *p, double value);
void processingSetSaturation        (processingObject_t *p, double sat);
void processingSetVibrance          (processingObject_t *p, double vib);
void processingSetBlackLevel        (processingObject_t *p, float bl, int bpp);
void processingSetWhiteLevel        (processingObject_t *p, int wl, int bpp);
void processingSetGamut             (processingObject_t *p, int gamut);
void processingSetHighlights        (processingObject_t *p, double v);
void processingSetShadows           (processingObject_t *p, double v);
void processingSetToning            (processingObject_t *p,
                                     uint8_t r, uint8_t g, uint8_t b,
                                     uint8_t strength);
void processingSetLutStrength       (processingObject_t *p, uint8_t strength);
void processingSetVignetteStrength  (processingObject_t *p, int8_t value);
void processingSetVignetteMask      (processingObject_t *p,
                                     uint16_t w, uint16_t h,
                                     float radius, float shape,
                                     float xStretch, float yStretch);
void processingSetGradientMask      (processingObject_t *p,
                                     uint16_t w, uint16_t h,
                                     float x1, float y1, float x2, float y2);
void processingSetTransformation    (processingObject_t *p, int transformation);
void processingSetImageProfile      (processingObject_t *p, int imageProfile);
void processingSetCamMatrix         (processingObject_t *p,
                                     double *camMatrix, double *camMatrixA);
void processingFindWhiteBalance     (processingObject_t *p, int w, int h,
                                     uint16_t *in, int posX, int posY,
                                     int *wbTemp, int *wbTint, int mode);
```

**Constants worth pinning** (header):

- Gamuts: `GAMUT_Rec709(0)`, `Rec2020`, `ACES_AP0`, `AdobeRGB`,
  `ProPhotoRGB`, `XYZ`, `AlexaWideGamutRGB`, `SonySGamut3`,
  `DavinciWideGamut`, `ACES_AP1`, `Canon_Cinema`, `PanasonivV`.
- Tonemap functions: `TONEMAP_None` through `TONEMAP_PanasonicVLog(12)`.
- Image profiles: `PROFILE_STANDARD(0)` through `PROFILE_CANON_LOG(11)`.

### 5.4 Demosaic — `src/debayer/debayer.h`

```c
void debayerEasy     (uint16_t *out, float *bayer, int w, int h, int threads, int type);
void debayerNoneU16  (uint16_t *out, const uint16_t *bayer,
                      int w, int h, int threads, int bit_shift);
void debayerBasicU16 (uint16_t *out, uint16_t *bayer,
                      int w, int h, int threads, int bit_shift);
void debayerBasic    (uint16_t *out, float *bayer, int w, int h, int threads);
void debayerAmaze    (uint16_t *out, float *bayer, int w, int h,
                      int threads, int blacklevel);
void debayerLibRtProcess(uint16_t *out, float *bayer, int w, int h,
                         int algorithm, double camMatrix[9]);
void debayerAhd      (uint16_t *out, float *bayer, int w, int h);
```

`debayerEasy` dispatches on `type`: None/Basic/AMaZE/AHD/LMMSE/IGV/RCD/DCB
(all integer-coded; see `src/mlv/macros.h:80-98` for mapping).
LMMSE/IGV/RCD/DCB all route through `debayerLibRtProcess` (vendored
`librtprocess`). AMaZE and AHD live in-tree.

### 5.5 LLRAWPROC — `src/mlv/llrawproc/llrawproc.h`

```c
llrawprocObject_t * initLLRawProcObject(void);
void                freeLLRawProcObject(mlvObject_t *video);
void                applyLLRawProcObject(mlvObject_t *video,
                                         uint16_t *raw_image_buff,
                                         size_t raw_image_size);

/* Fixed-raw master enable */
int  llrpGetFixRawMode      (mlvObject_t *video);
void llrpSetFixRawMode      (mlvObject_t *video, int value);       /* FR_OFF / FR_ON */

/* Focus pixels */
int  llrpGetFocusPixelMode  (mlvObject_t *video);
void llrpSetFocusPixelMode  (mlvObject_t *video, int value);       /* FP_OFF/ON/CROPREC */
int  llrpGetFocusPixelInterpolationMethod(mlvObject_t *video);
void llrpSetFocusPixelInterpolationMethod(mlvObject_t *video, int value); /* FPI_MLVFS/RAW2DNG */
int  llrpDetectFocusDotFixMode(mlvObject_t *video);

/* Bad pixels */
int  llrpGetBadPixelMode    (mlvObject_t *video);
void llrpSetBadPixelMode    (mlvObject_t *video, int value);       /* BP_OFF/ON/AGGRESSIVE */
int  llrpGetBadPixelSearchMethod(mlvObject_t *video);
void llrpSetBadPixelSearchMethod(mlvObject_t *video, int value);   /* BPS_NORMAL/FORCE */
int  llrpGetBadPixelInterpolationMethod(mlvObject_t *video);
void llrpSetBadPixelInterpolationMethod(mlvObject_t *video, int value); /* BPI_MLVFS/RAW2DNG */

/* Dual ISO */
int  llrpGetDualIsoMode       (mlvObject_t *video);
void llrpSetDualIsoMode       (mlvObject_t *video, int value);     /* DISO_OFF/20BIT/FAST */
int  llrpGetDualIsoInterpolationMethod(mlvObject_t *video);
void llrpSetDualIsoInterpolationMethod(mlvObject_t *video, int value); /* DISOI_AMAZE/MEAN23 */
int  llrpGetDualIsoAliasMapMode(mlvObject_t *video);
void llrpSetDualIsoAliasMapMode(mlvObject_t *video, int value);
int  llrpGetDualIsoFullResBlendingMode(mlvObject_t *video);
void llrpSetDualIsoFullResBlendingMode(mlvObject_t *video, int value);
int  llrpGetDualIsoValidity   (mlvObject_t *video);
void llrpSetDualIsoValidity   (mlvObject_t *video, int diso_force); /* DISO_INVALID/FORCED/VALID */
int  llrpHQDualIso            (mlvObject_t *video);

/* Chroma smooth, stripes, pattern noise */
int  llrpGetChromaSmoothMode  (mlvObject_t *video);
void llrpSetChromaSmoothMode  (mlvObject_t *video, int value);     /* CS_OFF/2x2/3x3/5x5 */
int  llrpGetVerticalStripeMode(mlvObject_t *video);
void llrpSetVerticalStripeMode(mlvObject_t *video, int value);     /* VS_OFF/ON/FORCE */
void llrpComputeStripesOn     (mlvObject_t *video);
int  llrpGetPatternNoiseMode  (mlvObject_t *video);
void llrpSetPatternNoiseMode  (mlvObject_t *video, int value);     /* PN_OFF/ON */

/* Deflicker + dark frame */
int  llrpGetDeflickerTarget   (mlvObject_t *video);
void llrpSetDeflickerTarget   (mlvObject_t *video, int value);
int  llrpGetDarkFrameMode     (mlvObject_t *video);
void llrpSetDarkFrameMode     (mlvObject_t *video, int value);
int  llrpGetDarkFrameExtStatus(mlvObject_t *video);
int  llrpGetDarkFrameIntStatus(mlvObject_t *video);
int  llrpValidateExtDarkFrame (mlvObject_t *video, char *df_filename,
                                char *error_message);
void llrpInitDarkFrameExtFileName(mlvObject_t *video, char *df_filename);
void llrpFreeDarkFrameExtFileName(mlvObject_t *video);

/* Status resets */
void llrpResetFpmStatus(mlvObject_t *video);
void llrpResetBpmStatus(mlvObject_t *video);
void llrpResetDngBWLevels(mlvObject_t *video);

/* Stage timing (thread-local; mirror the telemetry contract) */
double llrpGetLastTotalMilliseconds(void);
double llrpGetLastSharedLockMilliseconds(void);
double llrpGetLastDualIsoRefineLockMilliseconds(void);
double llrpGetLastPublishLockMilliseconds(void);
double llrpGetLastDarkFrameMilliseconds(void);
double llrpGetLastVerticalStripesMilliseconds(void);
double llrpGetLastFocusPixelsMilliseconds(void);
double llrpGetLastBadPixelsMilliseconds(void);
double llrpGetLastPatternNoiseMilliseconds(void);
double llrpGetLastDualIsoMilliseconds(void);
double llrpGetLastChromaSmoothMilliseconds(void);
double llrpGetLastDualIsoPreviewHistogramMilliseconds(void);
double llrpGetLastDualIsoPreviewRegressionMilliseconds(void);
double llrpGetLastDualIsoPreviewRowscaleMilliseconds(void);
```

### 5.6 DNG — `src/dng/dng.h`

```c
dngObject_t * initDngObject(mlvObject_t *mlv_data, int raw_state,
                            double fps, int32_t par[4]);
int           saveDngFrame (mlvObject_t *mlv_data, dngObject_t *dng_data,
                            uint32_t frame_index, char *dng_filename,
                            const char *props_filename);
void          freeDngObject(dngObject_t *dng_data);

void dng_unpack_image_bits(uint16_t *in, uint16_t *out,
                            int w, int h, uint32_t bpp);
void dng_pack_image_bits  (uint16_t *in, uint16_t *out,
                            int w, int h, uint32_t bpp, int big_endian);
int  dng_compress_image   (uint16_t *out, uint16_t *in,
                            size_t *out_size, int w, int h, uint32_t bpp);
int  dng_decompress_image (uint16_t *out, uint16_t *in,
                            size_t in_size, int w, int h, uint32_t bpp);
```

### 5.7 Batch — `src/batch/`

```c++
// src/batch/BatchRunner.h
class BatchRunner {
public:
    static int run(const QString &inputPath, const QString &outputPath);
};

// src/batch/BatchContext.h - static singleton, set once, read everywhere
class BatchContext {
public:
    static void setBatchMode(bool);   static bool isBatchMode();
    static void setSkipErrors(bool);  static bool skipErrors();
    static void setVerbose(bool);     static bool isVerbose();
    static void setLogPath(const QString&);           static QString logPath();
    static void setReceiptPath(const QString&);       static QString receiptPath();
    static void setUseDefaultReceipt(bool);           static bool useDefaultReceipt();
    static void setResumeEnabled(bool);               static bool resumeEnabled();
};

// src/batch/MlvTrim.h
class MlvTrim {
public:
    static int run(QCoreApplication &app);
};
```

Sibling headers:

- `src/batch/ReceiptLoader.h` — parse a `.marxml` into in-memory
  `ReceiptSettings`.
- `src/batch/ReceiptApplier.h` — push `ReceiptSettings` onto
  `mlvObject_t::processing` + `::llrawproc`.
- `src/batch/BatchLogger.h` — stdout/log-file mirror (used by `--batch`).
- `src/batch/BatchPrompts.h` — error-decision hooks.
- `src/batch/BatchTypes.h` — `ProcessResult`, option enums shared with
  GUI.
- `src/batch/WorkerThreadCount.h` — centralises `--threads` /
  `MLVAPP_FORCE_THREADS` resolution for consistency across batch and
  playback-profile entry points.

### 5.8 Qt orchestration — `platform/qt/MainWindow.h`, `RenderFrameThread.h`

```c++
class MainWindow : public QMainWindow {
public:
    enum class PlaybackProfileScope { None, Histogram, Waveform, Parade, Vectorscope };
    enum class PlaybackProfileDebayerRequest { Auto, Receipt, None, Simple,
                                               Bilinear, LMMSE, IGV, AMaZE,
                                               AHD, RCD, DCB, AmazeCached };
    enum class PlaybackProfileProcessingRequest { Auto, Receipt, Subset };

    struct PlaybackProfileOptions; // see 4.9

    using ProgressCallback = std::function<bool(int framesDone, int totalFrames)>;

    static ProcessResult exportCdngSequence(
        mlvObject_t *mlvObject,
        const QString &outDir,
        const QString &clipBaseName,
        int codecProfile,
        int codecOption,
        uint32_t cutIn,
        uint32_t cutOut,
        double stretchX,
        double stretchY,
        bool audioExport,
        bool rawFixEnabled,
        ProgressCallback progressCallback = nullptr);

    int runHeadlessPlaybackProfile(const PlaybackProfileOptions &options);
};

class RenderFrameThread : public QThread {
public:
    enum OutputMode { OutputProcessed8, OutputProcessed16, OutputDebayered16 };
    struct ReadyFrame;             // see 4.7 + 4.8
    struct PresentationPreparationOptions { bool fastPlaybackScale;
                                            int  targetWidth;
                                            int  targetHeight; };
    struct RenderRequest { uint32_t frameNumber; OutputMode outputMode;
                           bool useGpuBilinearDebayer;
                           uint64_t requestSerial;
                           double requestStageTime;
                           ReadyFrame::PresentationContext presentationContext;
                           PresentationPreparationOptions presentationPreparationOptions; };

    void init(mlvObject_t *pMlvObject, int imageWidth, int imageHeight);
    void renderFrame(uint32_t frameNumber,
                     OutputMode outputMode,
                     bool useGpuBilinearDebayer,
                     uint64_t requestSerial,
                     const ReadyFrame::PresentationContext &ctx,
                     const PresentationPreparationOptions &prep);
    bool isFrameReady();
    bool isIdle();
    bool acquireLatestReadyFrame(ReadyFrame *frame);
    void releasePresentedFrame();
    void releasePresentedFrameForRequestSerial(uint64_t requestSerial);
    // plus telemetry getters: lastDualIsoPreviewHistogramMilliseconds(), etc.
signals:
    void frameReady();
};
```

**Producer/consumer contract** (post-commit `244c03a1`):

1. `MainWindow::renderFrame(frame, ...)` captures GUI geometry and policy
   into `PresentationContext`.
2. `RenderFrameThread::renderFrame` enqueues a `RenderRequest` (max 4
   pending; `kRenderRequestQueueDepth=4`,
   `RenderFrameThread.h:200-201`).
3. Worker thread runs `run()`→`drawFrame(slotIndex)` into one of 4
   `FrameSlot`s, then emits `frameReady()`.
4. `MainWindow::drawFrameReady()` calls
   `acquireLatestReadyFrame(&frame)`, reads `frame.presentationContext`
   (never live GUI state), updates the scene, and calls
   `releasePresentedFrameForRequestSerial(frame.requestSerial)`.

---

## 6. Frame pipeline (authoritative)

Entry points: `getMlvProcessedFrame8/16` (and `getMlvRawFrame*`). All
steps below are performed inside these functions unless noted.

### 6.1 Stage order

| # | Stage | Source | Function |
|---|---|---|---|
| 1 | Frame lookup | `src/mlv/video_mlv.c` | `video_index[frameIndex]` |
| 2 | Raw uint16 prefetch probe | `src/mlv/video_mlv.c` | `raw_uint16_prefetch_*` slot test |
| 3 | Disk read + LJ92 decompress | `src/mlv/video_mlv.c`, `src/mlv/liblj92/lj92.c` | `lj92_open` / `lj92_decode` |
| 4 | `applyLLRawProcObject` | `src/mlv/llrawproc/llrawproc.c` | 7 sub-stages (see 6.2) |
| 5 | Debayer dispatch | `src/debayer/debayer.c` | `debayerEasy` or direct call |
| 6 | `applyProcessingObject[8]` | `src/processing/raw_processing.c` | 9 stages (see 6.3) |
| 7 | Output routed | caller | uint8 QImage / uint16 RGB plane |

#### (1) Index lookup

`frame_index_t *entry = &video->video_index[frameIndex];`. This is O(1);
the array was built at `openMlvClip` time by walking every block.

#### (2) Prefetch cache probe

- 4 decode-ahead slots
  (`MLV_RAW_UINT16_PREFETCH_SLOTS=4`, `mlv_object.h:21`).
- Per-file mutex: `main_file_mutex[]`.
- Slot states: `MLV_RAW_UINT16_PREFETCH_EMPTY=0`,
  `READY=1`, `DECODING=2` (`video_mlv.c:117-120`).
- Enabled by default; disabled via
  `MLVAPP_DISABLE_RAW_UINT16_PREFETCH=1` or implicitly when
  `MLVAPP_PROFILE_LJ92_PRED6_SPLIT` /
  `MLVAPP_PROFILE_LJ92_GENERIC_SPLIT` /
  `MLVAPP_PRED1_FASTPATH_MEASUREMENT` are set (so per-thread LJ92
  telemetry reaches the caller). See `video_mlv.c:190-225`.
- Hit: `raw_uint16_prefetch_hit=1`, `raw_uint16_disk_read_ms≈0`,
  `raw_uint16_decompress_ms≈0`.
- Failure counter: `raw_uint16_prefetch_decode_failures` — per-clip
  atomic count of background decode failures; `0` on healthy runs.

#### (3) VIDF block read and LJ92 decompress

- If `MLVI.videoClass & MLV_VIDEO_CLASS_FLAG_LJ92` is set, the payload is
  lossless JPEG.
- `liblj92` exposes three code paths:
  1. **Pred1 fast path** — scalar and AVX2 specialised for predictor 1
     (the common case for Canon ML dumps including Dual ISO fixtures —
     see memory `lj92_predictor_dualiso.md`). Gated at build by the
     `MLVAPP_ENABLE_AVX2=1` qmake env; split telemetry only computed when
     `MLVAPP_PRED1_FASTPATH_MEASUREMENT` is set (`lj92.c:531`).
  2. **Pred6 split** — profiling-only instrumentation; env
     `MLVAPP_PROFILE_LJ92_PRED6_SPLIT=1`.
  3. **Generic** — fallback for other predictors; profiling split via
     `MLVAPP_PROFILE_LJ92_GENERIC_SPLIT=1`.
- MCRAW clips use a separate compression type
  (`mlvObject::compression_type`).

#### (4) `applyLLRawProcObject` (in-place uint16)

Fixed order, applied only if `llrawproc->fix_raw == FR_ON`:

| # | Sub-stage | Controlling flag |
|---|---|---|
| 4.1 | Dark frame subtraction | `llrawproc->dark_frame` |
| 4.2 | Focus-pixel remap | `llrawproc->focus_pixels`, `fpi_method` |
| 4.3 | Bad-pixel remap | `llrawproc->bad_pixels`, `bps_method`, `bpi_method` |
| 4.4 | Vertical-stripe correction | `llrawproc->vertical_stripes` |
| 4.5 | Dual ISO reconstruction | `llrawproc->dual_iso` (20-bit full or preview) |
| 4.6 | Pattern-noise removal | `llrawproc->pattern_noise` |
| 4.7 | Chroma smoothing | `llrawproc->chroma_smooth` (2×2 / 3×3 / 5×5) |

No post-`applyLLRawProcObject` cache layer is shipped; results are
recomputed on every `getMlvProcessedFrame16` call. (Earlier WIP notes
under `.claude/analysis/` discuss adding such a cache, but no code is
landed on this branch — see [04-external-auditor-guide.md §13.5](04-external-auditor-guide.md#135-mutable-state-during-playback-and-the-broader-wip-surface).)

#### (5) Debayer dispatch

`debayerEasy` (or a direct AMaZe/AHD call) runs one of nine algorithms:

| Algorithm | Implementation | Threading | GPU path |
|---|---|---|---|
| None | `debayerNoneU16` (already-debayered) | serial | n/a |
| Basic / Bilinear | `debayerBasic` / `debayerBasicU16` | OMP strips | Experimental (§9) |
| AHD | `debayerAhd` | OMP strips | CPU only |
| AMaZe | `debayerAmaze` | pthread strips | CPU only |
| LMMSE | `debayerLibRtProcess` | librtprocess internal | CPU only |
| IGV | `debayerLibRtProcess` | librtprocess internal | CPU only |
| RCD | `debayerLibRtProcess` | librtprocess internal | CPU only |
| DCB | `debayerLibRtProcess` | librtprocess internal | CPU only |
| Markesteijn | `debayerLibRtProcess` (X-Trans only) | librtprocess | CPU only |

Bilinear is the only algorithm with an experimental GPU path
(`MLVAPP_EXPERIMENTAL_GPU_DEBAYER=1`).

#### (6) `applyProcessingObject[8]` — 9 stages

Source: `src/processing/raw_processing.c`.

| # | Stage | Key function |
|---|---|---|
| 1 | **Setup** — rebuild 65 536-entry LUTs if receipt changed | `processing_update_curves`, `processing_update_contrast_curve`, `processing_update_shadow_highlight_curve`, `processing_update_matrices` |
| 2 | **Shadows/Highlights prep** — box-blur image into `shadows_highlights.blur_image` | `blur_image` |
| 3 | **Highest-green** — analyse for highlight reconstruction | `analyse_frame_highest_green` |
| 4 | **Core** — Levels → Color matrix → Creative (HSL, Hue-vs-\*) → Output | `apply_processing_object` inner loop; `processing_core_timing_t` records `levels_ms/color_ms/creative_ms/output_ms` |
| 5 | **Denoise** — 2D median | `denoiser_context` |
| 6 | **RBF** — recursive bilateral for blur/clarity/sharpen | `src/processing/rbfilter/` |
| 7 | **CA correction** | `src/processing/cafilter/` |
| 8 | **Gamma / transfer / gamut** | `processingSetTransferFunction`, `processingSetGamma`, `processingSetGamut` |
| 9 | **Optional direct 8-bit fast path** | `applyProcessingObject8` (AVX2 dispatch; `processingFastPathAvx2Active`); skips 16-bit store if caller asked for 8-bit only |

Representative line ranges for stage boundaries in
`src/processing/raw_processing.c` can be located by grepping for
`processing_setup_start`, `processing_shadows_highlights_prep_start`,
`processing_highest_green_start`, `processing_core_start`,
`processing_denoise_start`, `processing_rbf_start`,
`processing_ca_start`, `processing_other_start`.

#### (7) Output mode

`RenderFrameThread::OutputMode` (public enum):

| Value | Meaning | Output buffer |
|---|---|---|
| `OutputProcessed8` | Final 8-bit RGB (preview / QImage path) | `rawImage8` |
| `OutputProcessed16` | Full 16-bit RGB (export / HDR scopes) | `rawImage16` |
| `OutputDebayered16` | 16-bit debayered but unprocessed (GPU-processed downstream) | `rawImage16` |

---

## 7. Threading model

### 7.1 Prefetch pthread (raw uint16)

- **Location**: `src/mlv/video_mlv.c` (search `raw_uint16_prefetch_`).
- **Sleeps on** `raw_uint16_prefetch_cond` until
  `raw_uint16_prefetch_request_pending == 1`.
- **State per slot**: EMPTY / DECODING / READY; 4 slots
  (`MLV_RAW_UINT16_PREFETCH_SLOTS`).
- **Telemetry**: `raw_uint16_prefetch_hit` (per-frame TL),
  `raw_uint16_prefetch_decode_failures` (per-clip atomic).
- **Gate**: enabled by default. Disabled when
  `MLVAPP_DISABLE_RAW_UINT16_PREFETCH` is truthy or when any LJ92 split
  profiling env var is set
  (`video_mlv.c:202-218`). The comment at that site explicitly says: the
  prefetch worker would move TL telemetry onto the wrong thread.

### 7.2 LLRAWPROC workers

- **Pool**: `llrawprocWorkerState_t *llrawproc_workers` on
  `mlvObject_t`.
- **Mutexes**: `llrawproc_mutex` (shared config), `llrawproc_worker_mutex`
  (claim a worker slot), `llrawproc_cache_mutex` (post-stage cache).
- **Thread-local copies**: each worker owns `raw2ev/ev2raw`, its own
  `pixel_map focus_pixel_map_copy` + `bad_pixel_map_copy` (with
  versioning), its own dark-frame buffer, its own chroma-smooth /
  pattern-noise / vertical-stripes / dual-iso scratch. This is the
  "data-race protection rule" in
  `.claude/analysis/mlv-playback-investigation.md`.

### 7.3 OpenMP / pthread in demosaic + processing

- Compiled with `-fopenmp` on MinGW (Windows), `-lomp` via Homebrew LLVM
  (macOS), `-lgomp` (Linux). See
  `platform/qt/MLVApp.pro:54-147`.
- Demosaic uses vertical-strip split (`strips = threads`) with one
  worker per strip. AMaZe uses explicit pthreads; librtprocess algorithms
  use OMP internally.
- Curves / denoise use `#pragma omp parallel for` with dynamic
  scheduling.
- Gaussian blur uses a chunked parallel implementation
  (`blur_threaded.c`).
- Thread-count guidance: `--threads 4` is the clean best on the
  profiling VM documented in `tests/README.md:330-334`;
  `--threads 8` regresses compressed decode on that host.

### 7.4 Qt side

- **`RenderFrameThread`**: one `QThread`; 4-slot `FrameSlot` ring; queue
  depth 4. Producer posts an immutable `PresentationContext` + a
  `RenderRequest` with a monotonic `requestSerial`. Consumer reads only
  the emitted `ReadyFrame`. See §4.7, §4.8, §5.8.
- **`PlaybackPrepTask` / async-prep worker** *(WIP, commit
  `970bc389`)*: second background worker thread inside `MainWindow`.
  Moves image-prep work (pre-scaled `QImage` wrap, `scanZebrasRgb8`,
  `build_fast_playback_scaled_image`) out of `drawFrameReady()`. Scene /
  scopes / overlay / audio sync stays on the GUI thread. Uses
  `QMetaObject::invokeMethod(..., Qt::QueuedConnection)` to post back.
  See `.claude/analysis/mlv-playback-investigation.md` for the in-progress
  design, and `platform/qt/MainWindow.cpp:1114` for
  `enqueuePlaybackPrepTask`.
- **Audio playback thread**: `AudioPlayback` (see
  `platform/qt/AudioPlayback.h`) drives `QAudioOutput` (Qt5) or
  `QAudioSink` (Qt6) and synchronises with the timeline frame position.
- **Scripting**: `platform/qt/Scripting.h/.cpp` runs user scripts from a
  non-GUI thread and posts results back via queued signals.

### 7.5 Thread-local telemetry

`MLV_STAGE_THREAD_LOCAL` (`src/debug/StageTiming.h:14-18`) and
`MLV_PROCESSING_THREAD_LOCAL` (`src/processing/raw_processing.c:40+`)
declare all per-stage `g_*_ms` variables as thread-local. No locks are
needed for reads/writes but:

1. The reader must be on the same thread that wrote the value. This is
   why `getMlvLastRaw*` must be called from the same thread that invoked
   `getMlvProcessedFrame*`.
2. If a worker thread does the work, the caller sees zeros. This is why
   the `raw_uint16` prefetch is gated off when LJ92 profiling env vars
   are set.

### 7.6 Known pitfall — StageTiming snapshots

`mlv_stage_timing_get_snapshot()` returns a pointer to a thread-local
snapshot
(`src/debug/StageTiming.h:108-111`). Because the snapshot is
translation-unit local to any TU that includes the header, reading it
from a different TU that linked in a separate copy of the header will
return zeros. **Always use the `getMlvLast*` getters in
`src/mlv/video_mlv.h` (or `llrpGetLast*` / `processingGetLast*` in their
respective headers) instead of dereferencing the snapshot from outside
`video_mlv.c`.** See memory note `stagetiming_pitfall.md`.

---

## 8. Receipt system (`.marxml`)

A **receipt** is the serialised form of `processingObject_t` plus a
subset of `llrawprocObject_t`. It is written to a `.marxml` sidecar.

| Component | Path |
|---|---|
| Loader | `platform/qt/batch/ReceiptLoader.cpp` + `.h` (`src/batch/ReceiptLoader.h` for CLI) |
| Applier | `platform/qt/batch/ReceiptApplier.cpp` + `.h` (`src/batch/ReceiptApplier.h`) |
| In-memory form | `ReceiptSettings` (~150 setters) in `platform/qt/ReceiptSettings.cpp` |
| Sample presets | `receipts/*.marxml` |
| Test fixtures | `tests/fixtures/receipts/*.marxml` |

Applying a receipt (pseudo):

```c++
ReceiptSettings rs = ReceiptLoader::load(path);
ReceiptApplier::apply(rs, mlvObject->processing, mlvObject->llrawproc);
```

Invariants:

- Unknown XML tags are ignored on load (forward compatibility).
- Receipt keys map 1:1 to `processingSet*` / `llrpSet*` functions.
- Two receipts that differ in at least one field produce
  bit-different CDNG output (covered by the golden tests under
  `tests/fixtures/golden/`).

---

## 9. GPU paths (experimental)

All three paths are environment-gated and off by default. When any is
set and the app is on Windows, `main.cpp:64-86` forces
`QT_OPENGL=desktop` unless the caller set it. For architectural detail
see `docs/12-gpu-viewport-architecture.md`.

### 9.1 `MLVAPP_EXPERIMENTAL_GL_VIEWPORT=1`

- Installs `GpuDisplayViewport` (a `QOpenGLWidget` subclass) as the
  viewport for `graphicsView`.
- Accepts an 8-bit QImage **or** a 16-bit RGB frame and uploads to a
  persistent `QOpenGLTexture`.
- Supports nearest / linear / bicubic sampling; shader-side zebra
  overlays without CPU fallback.
- Fallback: legacy `QGraphicsPixmapItem` path.

### 9.2 `MLVAPP_EXPERIMENTAL_GPU_PROCESSING=1`

- Shader-side preview processing (`GpuPreviewProcessing.cpp`).
- Covers a subset of the CPU processing pipeline (exposure, curves, LUT,
  tone) for the 16-bit viewport path.
- CPU reference fallback is always retained.

### 9.3 `MLVAPP_EXPERIMENTAL_GPU_DEBAYER=1`

- Bilinear only (`GpuDebayer.cpp`). AMaZE remains CPU.
- A one-time runtime probe writes probe results into the
  `--profile-playback` JSON:
  - `gpu_bilinear_debayer_probe_available` (bool)
  - `gpu_bilinear_debayer_probe_reason` (string)
  - `gpu_bilinear_debayer_probe_renderer` (string)
- Per-frame telemetry:
  - `gpu_bilinear_debayer_active`
  - `gpu_bilinear_debayer_renderer`
  - `gpu_bilinear_debayer_fallback_reason`
- CPU fallback is automatic on `llvmpipe` / software renderer /
  missing extension.

---

## 10. CLI surface

Before `QApplication` is constructed, `platform/qt/main.cpp:26-86`
scans raw `argv` to decide which entry point to dispatch. This must
happen pre-Qt because `--batch` and `--trim-mlv` must not bring up a
`QMessageBox` on the CI runner.

| Flag | Entry point | Description |
|---|---|---|
| `--batch` | `runBatch(QCoreApplication)` (`main.cpp:235-344`) | Headless CDNG export. Required args: `--input`, `--output`. Optional: `--skip-errors`, `--log`, `--verbose`, `--receipt`, `--default-receipt`, `--resume`. |
| `--trim-mlv` | `MlvTrim::run(QCoreApplication &)` | MLV segment trim / reconstruction. Stand-alone utility. |
| `--profile-playback` | `runPlaybackProfile(QApplication)` (`main.cpp:346+`) | Headless playback profiler. Emits JSON to `--output`. Stacks arbitrary other `--*` options; see §5.8 `PlaybackProfileOptions`. |
| `--gpu-viewport` | Passed through to `MainWindow` ctor | Forces experimental GL viewport for this run. |
| `--gpu-preview-processing {auto\|cpu\|gpu}` | Passed through | Selects preview-processing backend. `gpu` implies `--gpu-viewport`. |
| `--gpu-bilinear-debayer {auto\|cpu\|gpu}` | Passed through | Selects bilinear debayer backend for the GPU preview-processing path. `gpu` implies `--gpu-viewport`. |

Default (no recognised pre-QApplication flag): `MainWindow` is
constructed and the GUI comes up.

Playback-profile-only flags: `--frames <n>` (default 16),
`--start-frame <n>`, `--scope {none|histogram|waveform|parade|vectorscope}`,
`--playback-debayer {auto|receipt|none|simple|bilinear|lmmse|igv|amaze|ahd|rcd|dcb|amaze-cached}`,
`--playback-processing {auto|receipt|subset}`, `--zebras`,
`--raw-cache-mb <mb>`, `--cache-cpu-cores <n>`, `--threads {<n>|auto}`,
`--fast-open`, `--show-window`, `--wait-for-paint`, `--stage-log <file>`.

---

## 11. Environment variables (full list)

Legend: **compile** = read at qmake/compile time;
**runtime** = read at process start;
**per-frame** = queried each render.

| Variable | Default | Scope | Effect |
|---|---|---|---|
| `QT_OPENGL` | unset | runtime (set by launcher) | `desktop` forces desktop GL (not ANGLE); required on Windows for experimental GPU paths. Set by `.claude-state/scripts/run-mlvapp.ps1` and by `main.cpp` when a GPU flag is passed. |
| `QT_QPA_PLATFORM` | unset | runtime | Standard Qt platform plugin override; surfaced in profile JSON as `qt_qpa_platform_environment`. |
| `MLVAPP_ENABLE_AVX` | unset | compile (qmake) | `1` → adds `-mavx` and `MLVAPP_BUILD_AVX=1`. See `platform/qt/avx_optin.pri`. |
| `MLVAPP_ENABLE_AVX2` | unset | compile (qmake) | `1` → adds `-mavx2 -mfma`, `MLVAPP_BUILD_AVX=1 MLVAPP_BUILD_AVX2=1`; enables the LJ92 Pred1 fast path and the direct-8-bit processing fast path. |
| `MLVAPP_DISABLE_AVX2` | unset | runtime | Truthy value disables the AVX2 direct-8-bit processing dispatcher at runtime even in AVX2 builds. See `processingFastPathAvx2Active`. |
| `MLVAPP_DISABLE_RAW_UINT16_PREFETCH` | unset (prefetch ON) | runtime | Truthy (`1/true/yes/on`) → disables raw-uint16 prefetch worker; decode happens on caller thread. See `video_mlv.c:198`. |
| `MLVAPP_EXPERIMENTAL_RAW_UINT16_PREFETCH` | — | historical | **Inert.** Was the enable gate; no longer read. |
| `MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH` | unset | runtime | Truthy enables experimental processed-8-bit prefetch worker; `video_mlv.c:227-245`. |
| `MLVAPP_EXPERIMENTAL_GL_VIEWPORT` | unset | runtime | `1` installs `GpuDisplayViewport`. |
| `MLVAPP_EXPERIMENTAL_GPU_PROCESSING` | unset | runtime | `1` enables shader-side preview processing. |
| `MLVAPP_EXPERIMENTAL_GPU_DEBAYER` | unset | runtime | `1` enables bilinear GPU debayer. |
| `MLVAPP_STAGE_TIMING` | unset | runtime | Non-empty and not `"0"` → emit per-stage lines via `mlv_stage_timing_note` (`StageTiming.h:44`). |
| `MLVAPP_STAGE_TIMING_FILE` | unset | runtime | Path to redirect stage-timing lines to a file. Default is stderr. |
| `MLVAPP_PRED1_FASTPATH_MEASUREMENT` | unset | runtime | Truthy → enables LJ92 Pred1 fast-path telemetry split; also implicitly disables raw uint16 prefetch worker (`video_mlv.c:204`). |
| `MLVAPP_PROFILE_LJ92_PRED6_SPLIT` | unset | runtime | Truthy → enables Pred6 split telemetry; also disables prefetch worker. |
| `MLVAPP_PROFILE_LJ92_GENERIC_SPLIT` | unset | runtime | Truthy → enables generic split telemetry; intrusive (per-sample), use only for shape diagnosis. Also disables prefetch worker. |
| `MLVAPP_FORCE_THREADS` | unset | runtime | Forces MLVApp worker thread count for headless profiling / CI stability. `--threads <n>` sets this. |

---

## 12. Telemetry / stage-timing contract

The primary telemetry surface is the `--profile-playback` JSON. The
`ReadyFrame::stageTimingTelemetry` `QJsonObject` emitted by
`RenderFrameThread` carries a superset for in-process consumers. All
keys are stable — renaming one requires updating the console-tests
contract (`tests/pipeline/test_dual_iso_pipeline.cpp`), the playback
profiler aggregator (`MainWindow.cpp`), and any downstream baselines.

### 12.1 Cadence and render thread

| Key | Unit | Meaning |
|---|---|---|
| `cadence_ms` | ms | Wall-clock delta between consecutive `frameReady()` emissions. |
| `latency_ms` | ms | Delta between request enqueue and `frameReady()`. |
| `render_thread_queue_wait_ms` | ms | Time spent in the 4-slot request queue before work started. |
| `render_thread_work_ms` | ms | Wall-clock work between queue pop and slot ready. |
| `render_thread_total_ms` | ms | `queue_wait + work`. |

### 12.2 `drawFrameReady` split

| Key | Unit | Meaning |
|---|---|---|
| `draw_frame_ready_queue_ms` | ms | Waiting for UI thread to pick up. |
| `draw_frame_ready_total_ms` | ms | Total time inside `drawFrameReady()`. |
| `draw_frame_ready_scene_ms` | ms | Scene geometry + pixmap swap. |
| `draw_frame_ready_image_ms` | ms | `QImage` wrap / fast-scale / raster. |
| `draw_frame_ready_present_ms` | ms | Presenter (CPU pixmap or GPU upload). |
| `draw_frame_ready_scopes_ms` | ms | Histogram / waveform / parade / vectorscope. |
| `draw_frame_ready_overlay_ms` | ms | Gradient / focus pixel / zebra overlays. |

### 12.3 Raw uint16

| Key | Unit | Meaning |
|---|---|---|
| `raw_uint16_ms` | ms | Wall-clock for the raw-uint16 stage total. |
| `raw_uint16_disk_read_ms` | ms | `fread` of VIDF block. |
| `raw_uint16_decompress_ms` | ms | LJ92 decode total. |
| `raw_uint16_decompress_prepare_ms` | ms | Setup before LJ92 `decode`. |
| `raw_uint16_decompress_execute_ms` | ms | Inside `lj92_decode`. |
| `raw_uint16_unpack_ms` | ms | 14/12/10-bit unpacking to 16 bits. |
| `raw_uint16_copy_ms` | ms | Final memcpy / bit-shift to caller buffer. |
| `raw_uint16_other_ms` | ms | `raw_uint16_ms - (sum of above)`. |
| `raw_uint16_prefetch_hit` | bool | Frame came from a prefetch slot. |
| `raw_uint16_prefetch_decode_failures` | int | Per-clip count; should be 0 on healthy runs. |

### 12.4 LJ92 predictor split

| Key | Unit | Meaning |
|---|---|---|
| `raw_uint16_lj92_predictor` | int | Predictor id (1/6/other). |
| `raw_uint16_lj92_pred1_fast_path_active` | bool | Pred1 fast path actually used. |
| `raw_uint16_lj92_pred1_fast_path_eligible` | bool | Frame qualified for Pred1 fast path. |
| `raw_uint16_lj92_pred1_fast_path_measurement_requested` | bool | Env var set. |
| `raw_uint16_lj92_pred1_fast_path_measurement_active` | bool | Measurement actually captured. |
| `raw_uint16_lj92_pred1_fast_path_total_ms` | ms | Total time in the Pred1 fast path. |
| `raw_uint16_lj92_pred1_fast_path_bitstream_ms` | ms | Bitstream decode inside Pred1. |
| `raw_uint16_lj92_pred1_fast_path_predictor_ms` | ms | Predictor step inside Pred1. |
| `raw_uint16_lj92_pred6_split_requested` | bool | `MLVAPP_PROFILE_LJ92_PRED6_SPLIT` set. |
| `raw_uint16_lj92_pred6_split_active` | bool | Pred6 actually hit for this frame. |
| `raw_uint16_lj92_pred6_total_ms/_bitstream_ms/_predictor_ms/_other_ms` | ms | Pred6 internals. |
| `raw_uint16_lj92_generic_split_requested/_active` | bool | Generic split telemetry. |
| `raw_uint16_lj92_generic_total_ms/_bitstream_ms/_predictor_ms/_other_ms` | ms | Generic internals. |
| `raw_uint16_lj92_scan_component_count`, `..._component_count`, `..._write_length`, `..._expected_write_length`, `..._skip_length`, `..._linearize_active` | int/bool | LJ92 header diagnostics. |

### 12.5 LLRAWPROC breakdown

| Key | Unit |
|---|---|
| `llrawproc_ms` | ms (thread-local totals) |
| `llrawproc_total_ms` | ms (wall-clock including locks) |
| `llrawproc_dark_frame_ms` | ms |
| `llrawproc_vertical_stripes_ms` | ms |
| `llrawproc_focus_pixels_ms` | ms |
| `llrawproc_bad_pixels_ms` | ms |
| `llrawproc_pattern_noise_ms` | ms |
| `llrawproc_dual_iso_ms` | ms |
| `llrawproc_chroma_smooth_ms` | ms |
| `llrawproc_other_ms` | ms |

### 12.6 Dual ISO

| Key | Unit / type |
|---|---|
| `dual_iso_preview_histogram_ms` | ms |
| `dual_iso_preview_regression_ms` | ms |
| `dual_iso_preview_rowscale_ms` | ms |
| `dual_iso_preview_total_ms` | ms |
| `dual_iso_mode_selected` | int (receipt request) |
| `dual_iso_mode_effective` | int (after policy resolution) |
| `dual_iso_preview_runtime_active` | bool |
| `dual_iso_preview_override_active` | bool |

### 12.7 Debayer breakdown

| Key | Unit |
|---|---|
| `debayered_frame_ms` | ms |
| `debayer_exclusive_ms` | ms |
| `debayer_wb_prepare_ms` | ms |
| `debayer_ca_ms` | ms |
| `debayer_kernel_ms` | ms |
| `debayer_wb_undo_ms` | ms |
| `debayer_pipeline_other_ms` | ms |
| `raw_float_convert_ms` | ms |

### 12.8 Processing breakdown

| Key | Unit |
|---|---|
| `processing_ms` | ms |
| `processed16_total_ms` | ms |
| `processed16_for_8bit_ms` | ms |
| `processed16_to_8bit_ms` | ms |
| `processed8_total_ms` | ms |
| `processing_setup_ms` | ms |
| `processing_shadows_highlights_prep_ms` | ms |
| `processing_highest_green_ms` | ms |
| `processing_core_ms` | ms |
| `processing_core_levels_ms` | ms |
| `processing_core_color_ms` | ms |
| `processing_core_creative_ms` | ms |
| `processing_core_output_ms` | ms |
| `processing_core_other_ms` | ms |
| `processing_denoise_ms` | ms |
| `processing_rbf_ms` | ms |
| `processing_ca_ms` | ms |
| `processing_other_ms` | ms |

### 12.9 GPU

| Key | Type |
|---|---|
| `gpu16_preview_active` | bool |
| `gpu_preview_processing_active` | bool |
| `gpu_preview_processing_backend_request` | string (auto/cpu/gpu) |
| `gpu_preview_processing_environment_requested` | bool |
| `gpu_bilinear_debayer_active` | bool |
| `gpu_bilinear_debayer_renderer` | string |
| `gpu_bilinear_debayer_fallback_reason` | string |
| `gpu_bilinear_debayer_probe_available` | bool |
| `gpu_bilinear_debayer_probe_reason` | string |
| `gpu_bilinear_debayer_probe_renderer` | string |

### 12.10 Processed-8 direct path

| Key | Type |
|---|---|
| `processed8_direct_path_active` | bool |
| `processed8_prefetch_hit` | bool |

### 12.11 Play-start preroll

| Key | Type |
|---|---|
| `play_start_preroll_active` | bool |
| `play_start_preroll_eligible` | bool |
| `play_start_preroll_disabled_by_environment` | bool |
| `play_to_first_frame_measured` | bool |
| `play_to_first_frame_ms` | ms |

### 12.12 Qt environment

| Key | Type |
|---|---|
| `qt_opengl_environment` | string |
| `qt_qpa_platform_environment` | string |

---

## 13. File formats

### 13.1 MLV block types

Canonical header in `src/mlv/mlv.h`. A raw spec lives in `src/mlv/raw.h`.

| Block | Purpose |
|---|---|
| `MLVI` | File header. Version, FPS num/denom, frame counts, video/audio class flags (`MLV_VIDEO_CLASS_FLAG_LJ92`, `_MCRAW`). |
| `RAWI` | Raw image info: resolution, bit depth, active area, black/white levels. |
| `RAWC` | Raw capture: binning, skipping. Used by `getMlvAspectRatio` and focus-dot fix detection. |
| `IDNT` | Camera id / serial / model. |
| `EXPO` | Exposure: ISO, shutter. |
| `LENS`, `ELNS` | Lens name, serial, focal length min/max, aperture min/max. |
| `RTCI` | Real-time clock (tm_year... tm_sec). |
| `WBAL` | White balance mode, kelvin, gains. |
| `WAVI` | WAV audio metadata: sample rate, channels. |
| `DISO` | Dual-ISO marker block with second ISO. |
| `INFO` | Free-form `INFO_STRING`. |
| `STYL` | Picture style. |
| `VERS` | Version strings (firmware / build info). |
| `DARK` | Internal dark frame offset/size. |
| `VIDF` | Video frame block (payload: raw/LJ92). |
| `AUDF` | Audio frame block. |

### 13.2 `.MAPP` sidecar

Declared: `mapp_header_t` (`mlv_object.h:37-48`), `MAPP_VERSION=3`.

| Field | Type | Meaning |
|---|---|---|
| `fileMagic[4]` | `uint8_t[4]` | "MAPP" |
| `mapp_size` | `uint64_t` | Total bytes of sidecar |
| `mapp_version` | `uint8_t` | Layout version |
| `block_num` | `uint32_t` | Total block count |
| `video_frames`, `audio_frames`, `vers_blocks` | `uint32_t` | Per-index counts |
| `audio_size` | `uint64_t` | Audio buffer bytes |
| `df_offset` | `uint64_t` | Offset to embedded dark frame |

Used to skip the linear scan on re-open.

### 13.3 `.marxml` receipts

XML serialisation of `processingObject_t` + selected `llrawprocObject_t`
fields. See §8.

### 13.4 CDNG export variants

Controlled by `raw_state` on `dngObject_t`:

| `raw_state` | Meaning |
|---|---|
| 0 (`UNCOMPRESSED_RAW`) | Uncompressed DNG. |
| 1 (`COMPRESSED_RAW`) | Lossless-JPEG DNG. |
| 2 (`UNCOMPRESSED_ORIG`) | Pass-through: write the raw sensor payload as-is (uncompressed case). |
| 3 (`COMPRESSED_ORIG`) | Pass-through compressed. |

ExportSettingsDialog surfaces "Default / Resolve / Fast" flavours which
are presets over these plus per-frame sidecar generation (`.dng.xmp`).

---

## 14. Memory ownership rules

| Object | Allocator | Deallocator | Notes |
|---|---|---|---|
| `mlvObject_t` | `initMlvObject` | `freeMlvObject` | Owns file handles, indices, caches, `llrawproc`, audio buffer |
| `processingObject_t` | `initProcessingObject` | `freeProcessingObject` | Caller owns; link via `setMlvProcessing` does **not** transfer ownership. `freeMlvObject` does not free the linked processing. |
| `llrawprocObject_t` | `initLLRawProcObject` | `freeLLRawProcObject(mlvObject_t *)` | Allocated inside `initMlvObject` and freed by `freeMlvObject`. **Never** call `freeLLRawProcObject` independently. |
| `dngObject_t` | `initDngObject` | `freeDngObject` | Per-export; short-lived. |
| Prefetch slots | Heap inside `mlvObject_t::raw_uint16_prefetch_cache` | `freeMlvObject` | Owned by the clip; not caller-visible. |
| `FrameSlot` / `std::vector<uint8_t>` row storage | `RenderFrameThread` | `RenderFrameThread` destructor | Slot ownership tracked by `presenting` flag; consumer must call `releasePresentedFrameForRequestSerial` when done. |
| Worker-thread copies (llrawproc) | `llrawproc_workers[]` on `mlvObject_t` | `freeMlvObject` | Copy-on-claim, version-tagged; protects the main-thread maps. |

**Data-race protection rule** (from
`.claude/analysis/mlv-playback-investigation.md`): the async-prep worker
*never* passes raw slot pointers to a worker thread without ownership
transfer. For the fast-scale `playbackScaledImage8` path, the worker
copies the byte vector before kicking off async work. If the copy is
not possible (e.g. unexpected geometry), the code falls back to the
synchronous drawFrameReady path.

---

## 15. Performance invariants

A code reviewer can apply this rubric:

1. **No GUI state reads on the render thread.** Everything the render
   thread needs must already live on the captured `PresentationContext`.
   Evidence: `RenderFrameThread.h:38-57`; `MainWindow::renderFrame`
   captures once.
2. **Telemetry keys never rename in isolation.** Changing a key name in
   `video_mlv.c` / `raw_processing.c` without updating
   `MainWindow.cpp`'s JSON aggregator and the test fixtures
   (`tests/console/`, `tests/pipeline/`) breaks goldens.
3. **AVX2 fast path must remain bit-identical to the scalar path.**
   Covered by `tests/console/test_avx_golden.cpp`.
4. **Direct-8-bit fast path equals `(processed16 >> 8)`.** Covered by
   `DualIsoPipeline.DirectProcessed8FastPathMatchesShiftedProcessed16*`
   (`tests/pipeline/test_dual_iso_pipeline.cpp`).
5. **Cache window shift invalidates in-flight work.**
   `cache_start_frame` + `cache_generation` on `mlvObject_t`
   (`mlv_object.h:156-157`) bumps every window shift; in-flight workers
   must re-check before publishing.
6. **Processing LUTs are cached across frames.** Any setter that
   changes a LUT input must call
   `processing_update_*` *eagerly* in the setter to keep
   `applyProcessingObject` idempotent and lock-light.
7. **Any receipt change bumps `processed_*_cache_signature`.** The
   processed-frame caches (`MLV_PROCESSED_{8,16}BIT_CACHE_SLOTS`) key
   on frame + thread count + signature; otherwise a stale processed
   frame could be served after a slider move.
8. **The prefetch worker is gated off while LJ92 profiling env vars are
   set.** Any future change must preserve this gate to keep thread-local
   telemetry readable.

---

## 16. Extending the engine

### 16.1 Adding a new processing parameter end-to-end

1. **Receipt field** — add the tag in the XML writer/reader under
   `platform/qt/batch/ReceiptLoader.cpp` and `ReceiptApplier.cpp`; add a
   getter/setter on `ReceiptSettings`.
2. **`processingObject_t` field** — add the field in
   `src/processing/processing_object.h`.
3. **Setter in `raw_processing.h/.c`** — follow the mixedCase
   `processingSetFoo` pattern; update the LUT(s) eagerly if needed.
4. **Pipeline stage** — edit the relevant stage in
   `raw_processing.c`. Bump the processed-frame cache signature.
5. **UI slider / combo** — add to `MainWindow.ui`, wire a slot on
   `MainWindow` that calls the setter and requests a repaint.
6. **Golden test** — run `console_tests --check-golden --hash-output
   new_hashes.json` with the new field toggled on a test receipt, then
   commit the updated `tests/fixtures/golden/hashes.json` /
   `pipeline_hashes.json`.

### 16.2 Adding a new demosaic backend

1. Add algorithm impl under `src/debayer/` or vendor it into
   `src/librtprocess/` (keep GPL-3 compatibility).
2. Add a `debayerFoo` entry point with the standard signature
   `(uint16_t *out, float *bayer, int w, int h, int threads, ...)`.
3. Dispatch it from `debayerEasy` behind a new integer id in
   `macros.h:80-98`.
4. Add `PlaybackProfileDebayerRequest::Foo` to `MainWindow.h:75-89` and
   the parser in `main.cpp:103-161`.
5. Wire a preset entry in `ReceiptSettings` so `.marxml` can name it.
6. Add a golden entry (usually under `tests/pipeline/`).

### 16.3 Adding a new export codec

1. Extend the dispatch in
   `platform/qt/ExportSettingsDialog.cpp::startExport` with a new codec
   id.
2. If the codec ships via bundled ffmpeg, update
   `platform/qt/FFmpeg/` with the binary that has the encoder.
3. If hardware-accelerated on macOS, wire it through `AvfLibWrapper.h`
   and `platform/cocoa/avf_lib/`.
4. Add a row to the codec table in `docs/01-user-guide.md`.

---

## 17. Open issues and WIP seams

The items below are in-tree and partially implemented; see
`.claude/analysis/mlv-playback-investigation.md` for the live design
notes (first ~200 lines for current state).

- **Presentation-split async-prep WIP** (commit `970bc389`). A second
  worker thread (`PlaybackPrepTask`) behind `MainWindow` lifts image
  prep, fast-scale, and zebra scan out of `drawFrameReady()`. Data-race
  protection rule in §14 governs input copies. Scope boundaries are
  fully captured in
  `platform/qt/MainWindow.cpp:1114-1492` and
  `:11918-11959`.
- **Direct-8-bit processed path** is *activated* by default and gated
  by `processingCanUseDirect8BitOutput(p)` + AVX2 dispatch. Caller
  opts in via `getMlvProcessedFrame8` when the receipt allows it.
- **Raw uint16 decode-ahead prefetch** is *default-on*. Disable via
  `MLVAPP_DISABLE_RAW_UINT16_PREFETCH=1`; implicitly disabled when LJ92
  profiling env vars are set.
- **Play-start preroll** (non-blocking 2-frame lookahead): fired on
  clicking Play when cached-AMaZe playback is eligible. Telemetry keys
  `play_start_preroll_*` and `play_to_first_frame_*` record shape;
  interpretation note in `tests/README.md:382-386` — use for A/B trend
  only, not as a literal "button to visible pixel" metric.

---

## 18. Glossary

| Term | Meaning |
|---|---|
| **MLV** | Magic Lantern raw video container; per-block framed file with optional segmented `.M00/.M01` companions. |
| **MAPP** | Sidecar index file for MLV (`.MAPP`). Skips linear block scan on re-open. |
| **marxml** | XML serialisation of `processingObject_t` + `llrawprocObject_t` subset, a.k.a. a **receipt**. |
| **Receipt** | The in-memory + on-disk representation of a clip's grade. Addressable 1:1 via `processingSet*`/`llrpSet*`. |
| **Debayer** | Bayer-pattern demosaic. Nine algorithms are available; AMaZE and AHD are in-tree, the rest via `librtprocess`. |
| **LJ92** | Lossless JPEG, the compression used for compressed MLV VIDF payloads. Implemented in-tree as `src/mlv/liblj92/`. |
| **Dual ISO** | Magic Lantern feature that interleaves two ISO sensitivities across alternating rows. Post-processed either via 20-bit reconstruction (`DISO_20BIT`) or a fast preview path (`DISO_FAST`). |
| **Predictor (LJ92)** | JPEG predictor id (1=no-preceding, 6=median-plane, etc.). The Pred1 fast path is the AVX2-accelerated specialised decoder for predictor 1 (including Dual ISO fixtures — memory note `lj92_predictor_dualiso.md`). |
| **AVIR** | Vendored MIT-licensed high-quality image resizer used from `platform/qt/` for preview downscale. |
| **AMaZE** | Emil Martinec's demosaic algorithm; one of the highest-quality options. In-tree at `src/debayer/amaze_demosaic.c`. |
| **librtprocess** | Vendored GPL-3 demosaic library from the rawtherapee project; provides LMMSE, DCB, RCD, IGV, Markesteijn. |
| **Prefetch slot** | One of the 4 slots in `raw_uint16_prefetch_cache` that stores a decode-ahead frame. States: EMPTY / DECODING / READY. |
| **Cadence (playback)** | Wall-clock delta between successive `frameReady()` emissions. Target is `1000 / fps` ms; the `cadence_ms` telemetry field measures it. |
| **Golden hash** | SHA-256 binary hash of a specific test-fixture DNG frame or QImage. Stored under `tests/fixtures/golden/`. |

---

## Cross-references

- `docs/01-user-guide.md` — end-user installation, clip import,
  grading, export.
- `docs/02-developer-guide.md` — building, testing, contribution
  workflow.
- [`docs/03b-technical-specification-algorithms.md`](03b-technical-specification-algorithms.md)
  — **algorithms and binary layouts companion** to this doc. Where this
  spec names a struct, stage, or contract, 03b gives the actual byte
  layout, formula, or pseudocode (MLV/MAPP block layouts, LJ92 codec,
  LLRAWPROC math, 9-stage pipeline pseudocode, receipt XML schema, CDNG
  IFDs, `--profile-playback` JSON shape). Read 03b for reconstruction.
- `docs/04-external-auditor-guide.md` — how to reproduce results from
  a clean checkout without any prior context.
- `docs/10-build-windows.md`, `docs/11-build-macos-linux.md` — platform
  build instructions.
- `docs/12-gpu-viewport-architecture.md` — deep dive on the experimental
  GPU paths.
- `docs/13-testing-infrastructure.md`, `docs/14-performance-benchmarking.md`,
  `docs/15-test-fixtures.md`, `docs/16-fuzz-testing.md` — testing stack.
- `docs/diagrams/` — ASCII and Mermaid diagrams referenced from this doc.

---

*Document pinned to engine version 1.15.0.0
(`platform/qt/MLVApp.pro:450-460`). Future edits should update the
pinned version if the engine bumps.*
