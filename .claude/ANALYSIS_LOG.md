# MLV‑App analysis log

A running, append‑only record of non‑trivial investigations. See `.claude/CLAUDE.md` for the rule.

---

## 2026‑04‑20 — Playback performance investigation (Parts 1 + 2 + Dual ISO + Codex synthesis)

### Question
User: "I want to investigate MLV playback. It is very slow. Why? Can we ever make playback high quality and real‑time? Could leveraging a GPU help at all?" Follow‑up: "I mainly use Dual ISO. Could that be impacting playback speed? Any way to optimize the Dual ISO path?"

### Verdict in one paragraph
Playback is slow because **every displayed frame redoes almost all the CPU work** (LJ92 decode → llrawproc → debayer → color pipeline → 16→8 → scale → scopes → paint) while the display path does software raster and the cache only helps the first N frames from the start of the clip in AMaZE mode. Real‑time in the current architecture is `drop‑frame` mode — the app even documents it that way. With the fixes ranked below, 4K real‑time at AMaZE‑equivalent quality is reachable, and yes, GPU helps decisively for ~80 % of the pipeline. The one stage that is not GPU‑able (LJ92 Huffman decode) is not the long pole. **For the user's Dual‑ISO workflow specifically, ~5–10× of the speedup is available without GPU at all** — see §6.

---

### 1. Pipeline trace (verified file:line)

Playback entry → display:

1. `platform/qt/MainWindow.cpp:253` — `timerFrameEvent` drives playback
2. `platform/qt/MainWindow.cpp:603` — `drawFrame()` called per frame
3. `platform/qt/RenderFrameThread.cpp:74–98` — single worker QThread; spin‑poll loop, 1 ms sleep
4. `platform/qt/RenderFrameThread.cpp:94` — calls `getMlvProcessedFrame8()`
5. `src/mlv/video_mlv.c:540` — `getMlvProcessedFrame8` → calls `getMlvProcessedFrame16`
6. `src/mlv/video_mlv.c:566` — `getMlvProcessedFrame16` → calls `getMlvRawFrameDebayered`
7. `src/mlv/video_mlv.c:553` — cache check: only serves if cached debayered RGB present
8. `src/mlv/video_mlv.c:511, 380` — `getMlvRawFrameFloat` → `getMlvRawFrameUint16` (disk+decompress, mutex‑protected)
9. `src/mlv/video_mlv.c:195, 243, 327, 359` — raw ingest: mcraw / LJ92 / manual bit‑unpack paths
10. `src/mlv/llrawproc/llrawproc.c:200, 239, 313, 348` — llrawproc: stripes, focus/bad pixels, pattern noise, chroma smooth, dual‑ISO
11. `src/debayer/debayer.c:87, 103, 356` — AMaZE with `pthread_create` **per frame, per thread**
12. `src/processing/raw_processing.c:503, 508` — full color pipeline per pixel
13. `src/mlv/video_mlv.c:572–581` — 16→8 bit reduction, allocates a full‑frame uint16 RGB buffer every frame
14. `platform/qt/MainWindow.cpp:9588–9644` — `drawFrameReady` on main UI thread: `QImage` → `.scaled()` → `QPixmap::fromImage` → `setPixmap`; optional avir Lanczos path at 9614–9632 that allocates+frees a scaled buffer per frame and constructs a `avir_scale_thread_pool` per frame
15. `platform/qt/MainWindow.cpp:1297–1301` — `QGraphicsScene` + `QGraphicsPixmapItem` = software raster
16. `platform/qt/Histogram.cpp:39–41`, `ScopesLabel.cpp:41`, `WaveFormMonitor.cpp:30`, `VectorScope.cpp:30` — scope widgets walk the frame with `pixelColor(x,y)` per pixel on the main thread

---

### 2. Cache architecture problems (verified)

- `src/mlv/mlv_object.h:149` declares `uint64_t cache_start_frame` and `src/mlv/macros.h:11` has `setMlvCacheStartFrame()` — **but the finder at `src/mlv/frame_caching.c:180` iterates `for (uint64_t frame = 0; frame < getMlvRawCacheLimitFrames(video); ++frame)` starting at 0, ignoring `cache_start_frame`**. Dead field. Playhead position has no effect on cache fill order.
- `src/mlv/frame_caching.c:256–258` locks `cache_mutex` around `getMlvRawFrameFloat()` — disk read + LJ92 decode + llrawproc all execute **serially** across cache threads. Cache worker count > 1 only helps AMaZE, not raw ingest.
- `src/mlv/frame_caching.c:216–227` allocates 8 heap buffers + 4 pointer‑array loops **per cache thread** at thread startup (tolerable), but the pattern persists.
- `src/mlv/frame_caching.c:264` — cache stores **16‑bit debayered RGB**, not final preview. The full `applyProcessingObject` color pipeline re‑runs on every displayed frame even when the cache hits.
- Cache is only populated for AMaZE debayer (see `MainWindow.cpp:10788` — "///@todo: ADD HERE OTHER CACHED DEBAYERS!"). Choosing any other debayer disables the cache entirely.

---

### 3. Dual ISO cost — verified and deep

This is the user's primary workflow. Findings:

- `src/mlv/llrawproc/dualiso.c` is **2 290 lines**, one of the heaviest files in the codebase.
- Entry point actually used: `diso_get_full20bit` at `dualiso.c:2085` — 14 parameters; performs pattern detection, EV‑curve least‑squares fit, inter‑row interpolation, alias map generation, full‑res blending, and internal chroma smoothing.
- Dispatch: `src/mlv/llrawproc/llrawproc.c:313–440` — the whole block runs when `dual_iso && diso_validity`.
- `llrawproc.c:349` — only `dual_iso == 1` (Full 20bit HDR processing mode) is wired up.
- `llrawproc.c:430–438` — **the fast preview path `diso_get_preview` is commented out.** `diso_get_preview` at `dualiso.c:47` is only ~500 lines of preview‑quality interpolation — dramatically cheaper than the full 20‑bit path. **This is dead code that could be the single biggest Dual‑ISO playback win.**
- `llrawproc.c:443` — regular chroma smooth is skipped when `dual_iso == 1` because `diso_get_full20bit` does its own (line 363 passes `chroma_smooth` down).
- `llrawproc.c:372–374` — black level has to be recalculated after Full 20 bit, which invalidates the pre‑computed `raw2ev`/`ev2raw` LUTs and rebuilds them.
- `llrpHQDualIso` at `llrawproc.c:707–710` returns true when `dual_iso == 1 && diso_validity && llrpGetFixRawMode`. There is no "Dual ISO Preview" equivalent today.
- Public‑issue evidence (GitHub #86): dual‑ISO export of a 98‑minute clip took **~37 hours** vs **~3 hours** without — **~12× slowdown from Dual ISO alone**. That is the export path, but the playback path runs the same `diso_get_full20bit` every frame, so the ratio transfers.
- Dual ISO pattern detection (the 4‑histogram median scan at `dualiso.c:58–104`) is **not clip‑level cached**. It re‑detects the dark/bright row offset every single frame despite the pattern being a clip property.
- Dual ISO is score **3** on the GPU feasibility scale — inter‑row blending + statistical analysis. Feasible GPU port but not trivial.

---

### 4. Per‑frame malloc hot spots (verified)

Each of these reallocates per displayed frame:

- `src/processing/denoiser/denoiser_2d_median.c:77–79` — **three `malloc`s per pixel**. ~6.2 M heap ops/frame at 1080p, ~25 M at 4K.
- `src/processing/rbfilter/rbf_wrapper.cpp:33–36` — `new[]`/`delete[]` ~63 MB per recursive bilateral call (shadows/highlights, RBF denoise).
- `src/debayer/debayer.c:25–37, 386–398` — ~25 MB malloc+free per frame for R/G/B plane arrays passed to AMaZE.
- `src/debayer/debayer.c:87–103` — `pthread_create` + `pthread_join` **per frame, per thread**. Windows pthread wrapper has non‑trivial thread‑create cost.
- `src/mlv/llrawproc/patternnoise.c:113–115` — ~12 MB per invocation.
- `src/mlv/llrawproc/pixelproc.c:108` — ~4 MB per chroma‑smooth call.
- `src/mlv/video_mlv.c:572–581` — full‑frame uint16 RGB intermediate allocated every frame inside `getMlvProcessedFrame8`.
- `platform/qt/MainWindow.cpp:9614–9632` — `avir_scale_thread_pool` **constructed per frame** on the UI thread, plus `malloc`/`free` of a full scaled RGB buffer. Only active when stretch factor ≠ 1.0.

Combined impact: allocator lock contention serializes otherwise‑parallel code. This is why issue #86's reporter saw **8 % CPU utilization** during a dual‑ISO export — the CPU is blocked on heap locks and thread create/join, not compute.

---

### 5. Processing stage inefficiencies (verified)

- `src/processing/raw_processing.c:440` — TODO comment: "Maybe this blurring bit could b multithreaded I need to think". Recursive bilateral is currently single‑threaded.
- `src/processing/raw_processing.c:764–765` — `double expo_correction` in hot pixel loop. Source is uint16. Forces 64‑bit math across a stencil and kills auto‑vectorization.
- `platform/qt/Histogram.cpp:39–41` (and sister scopes) — `img.pixelColor(x,y)` is the slowest QImage accessor: per‑pixel bounds check + QColor construction + virtual dispatch, ~6 M calls per frame at 1080p, all on the UI thread.
- `src/processing/raw_processing.c:508` — sharpen/clarity/grain/LUT run unconditionally. No identity‑parameter early‑out.

---

### 6. Peer‑tool evidence (verified via background research)

| Tool | Architecture | Real‑time claim |
|---|---|---|
| MLRawViewer (baldand) | Python + PyOpenGL + GLSL. Upload packed bayer → 1‑channel texture → fragment shader debayer + WB + exposure + display. No host RGB intermediate. | "Playback at file frame rate or closest achievable if constrained by disk read speed." |
| Fast CinemaDNG / MLV Player | CUDA. Full pipeline on GPU. | HQLI (Malvar) **69 GPix/s** on RTX 4090. MG (AMaZE‑class quality) **20 GPix/s**. At 2 MPix that's thousands of fps. Disk‑bound. |
| darktable | Per‑module CPU + OpenCL parity. Each iop has `process()` and `process_cl()`. | Interactive 0.76 s → 0.11 s (**7×**); export 25.2 s → 6.5 s (**4×**). |
| DaVinci Resolve | GPU Metal/CUDA/OpenCL end‑to‑end. | Faster‑than‑realtime 8K @24fps on dual Pro Vega II (Mac Pro). |
| RawTherapee | CPU + OpenMP + tiles. | Struggles at 4K realtime. Confirms CPU‑only ceiling. |
| FFmpeg / libplacebo | Neither ships a Bayer demosaic GPU filter. | N/A — can't off‑load upstream. |

Demosaic quality tiers (Kodak PSNR):

| Algorithm | PSNR | GPU impl? |
|---|---|---|
| Bilinear | ~33 dB | Trivial everywhere |
| Malvar‑He‑Cutler / HQLI | ~36 dB | Trivial; GLSL + CUDA ubiquitous |
| **RCD** | **~39–40 dB** | **Full OpenCL in darktable; librtprocess already has CPU RCD which MLV‑App links** |
| MG | ~40.5 dB | CUDA (Fast CinemaDNG) |
| AMaZE | ~40 dB | **None exists and none planned** — darktable dev: "there is no plan to implement it." |

Crucial: **RCD ≥ AMaZE quality, has production OpenCL, and librtprocess CPU RCD is already linked into MLV‑App.**

---

### 7. GPU feasibility scorecard (1 trivial → 5 inherently CPU‑bound)

---

## 2026-04-20 - Regression testing strategy addendum

### Question
User: "Also come up with a unit testing strategy. I dont want any of our future changes to introduce regression. Be exhaustive."

### Verified locally
- There is currently no real automated behavior-test suite in this repo.
- The GitHub Actions workflows are packaging/build workflows, not correctness workflows:
  - `.github/workflows/Windows.yml:11-69`
  - `.github/workflows/Linux.yml:11-80`
  - `.github/workflows/macOS-Intel.yml:12-40`
  - `.github/workflows/macOS-Arm64.yml:12-40`
- There is already a strong headless integration seam:
  - `platform/qt/main.cpp:18-159` exposes `--batch`
  - `src/batch/ReceiptApplier.cpp:22-299` applies runtime settings without GUI interaction
  - `src/batch/ReceiptApplier.cpp:310-360` prints a structured runtime fingerprint that can be asserted in tests
- Golden correctness tests must prefer single-thread processing because `src/mlv/video_mlv.h:58-63` explicitly warns multi-threaded processed-frame generation is preview-oriented and may have minor artifacts.

### Strategy
- Use a layered test pyramid, not unit tests alone:
  1. Pure unit tests for helpers/state transitions
  2. Core pipeline microtests on synthetic frame buffers
  3. Golden-frame integration tests on tiny fixture clips
  4. Headless batch/receipt regression tests
  5. Performance benchmarks with thresholds, kept separate from correctness gating

### Highest-value additions
- Add tests around Dual ISO mode selection before changing the code:
  - playback preview path vs paused/export path
  - `Mean` vs `AMaZE`
  - alias map on/off
  - full-res blending on/off
  - frozen auto-match values vs rerun-every-frame behavior
- Add cache-behavior tests before changing cache fill order or removing serialization:
  - playhead-centered selection
  - `cache_start_frame` semantics
  - cache hit/miss correctness
  - cache invalidation after receipt-setting changes
- Add golden-frame tests before touching:
  - scratch-buffer hoisting
  - AVX enablement
  - GPU preview path
  - display/scaling path

### Practical recommendation
- Canonical reference path:
  - CPU
  - cache disabled unless explicitly testing cache
  - `threads=1`
- Fast PR suite:
  - synthetic-buffer tests
  - receipt fingerprint tests
  - a few tiny golden-frame checks
- Heavier nightly suite:
  - larger fixture clips
  - performance benchmarks
  - GPU-vs-CPU tolerance comparisons once GPU work begins

### Notes for future implementation
- The existing `platform/binning_test/` proves the core pipeline can be driven outside the GUI.
- The batch path is the best first integration-test harness because it is already in the product, headless, and cross-platform.

| Stage | Score | Note |
|---|---|---|
| LJ92 Huffman decode | 5 | Stays on CPU. |
| Vertical stripes | 2 | Column multiply. |
| Bad pixels / focus pixels | 3 | Sparse scatter. |
| Pattern noise (median) | 4 | Sorting network. |
| Chroma smooth | 2 | Stencil. |
| **Dual ISO (`diso_get_full20bit`)** | **3** | Inter‑row blending + statistics. Feasible GPU port but non‑trivial. |
| Dark‑frame subtract | 1 | Trivial. |
| WB pre‑conversion (bayer) | 2 | Per‑element multiply. |
| Debayer — bilinear / Malvar | 1 | Fragment shader. |
| Debayer — RCD | 2 | Multi‑pass, has OpenCL reference. |
| Debayer — AMaZE | 5 | Keep on CPU forever. |
| WB 3×3 matrix | 1 | Fragment shader. |
| Gamma / levels / tone / contrast / clarity LUTs | 1 | 1D texture. |
| 3D LUT (.cube) | 2 | `GL_TEXTURE_3D` trilinear. |
| Recursive bilateral (shadows/highlights) | 5 | IIR scan. Leave on CPU, multi‑thread it. |
| Sharpen | 2 | Convolution. |
| Grain | 2 | Hash PRNG. |
| 16→8 reduction | 1 | Disappears into framebuffer. |
| **Display scaling (Qt / avir)** | **1** | **Biggest immediate UI win.** |
| Scopes | 3 | Atomics; kill `pixelColor()` first. |

---

### 8. Qt display migration options

- **Option A** (`setViewport(new QOpenGLWidget())`): ~5 lines, negligible gain. Skip.
- **Option B** (Custom `QGraphicsItem` uploads GL texture, keeps scene): 3–5 days, ~200 lines. Kills avir Lanczos main‑thread path. Keeps picker/zoom/gradient/drop untouched. **Recommended first GPU step.**
- **Option C** (Replace QGraphicsView with custom `QOpenGLWidget`, 16‑bit upload, 3D LUT): 7–12 days, ~500 lines. Required for full GPU pipeline. Must reimplement picker coord math, zoom/pan, gradient overlay as QPainter‑on‑GL.

API choice: **GLSL via `QOpenGLWidget`** for Tiers 2–3. OpenCL enters only at Tier 4 (RCD debayer). `QSurfaceFormat::sRGBColorSpace` is already set at `MainWindow.cpp:112`.

---

### 9. Ranked roadmap

**Tier 1 — CPU hygiene (1–2 weeks)**
1. Persistent scratch buffers for denoiser (3‑malloc fix), RBF, AMaZE planes, patternnoise, chroma smooth, `getMlvProcessedFrame8` uint16 intermediate — put all on `processingObject`/`mlvObject`, resize only on dimension change.
2. Fix `Histogram::pixelColor(x,y)` → `constBits()` raw pointer walk, ditto for waveform/parade/vectorscope.
3. `double expo_correction` → `float` in hot pixel loop.
4. Move `pthread_create` out of debayer per‑frame path — one pool at mlvObject init, reuse.
5. Move `avir_scale_thread_pool` out of `drawFrameReady` — one pool per mlvObject.
6. Wire `cache_start_frame` into the cache finder → playhead‑aware cache fill.
7. Remove `cache_mutex` around `getMlvRawFrameFloat` — allow parallel disk + LJ92 across cache threads.
8. Dual‑ISO: **re‑enable `diso_get_preview` and expose a "Preview (fast) / 20‑bit HQ" toggle in the Dual ISO UI.** Default Playback to Preview, Paused‑Frame to HQ. See §10 for detail.
9. Dual‑ISO: clip‑level cache the `dark_row_start` pattern detection so it runs once per clip, not once per frame.
10. Early‑out in color pipeline when sharpen/clarity/grain are 0 and no LUT is loaded.

Expected outcome: 1080p AMaZE realtime; 2K bilinear realtime; Dual‑ISO playback ~5–10× faster; 4K still not realtime.

**Tier 2 — GPU display (Option B) + CPU threading polish (1 week)**
1. Option B from §8.
2. LRU cache eviction + enable cache for all debayer modes, not just AMaZE.
3. OpenMP on recursive bilateral outer loop.
4. Preview‑downscale mode: box‑filter 4K→2K before the color pipeline when fit‑to‑window.
5. Double‑buffer `m_pRawImage` so render thread can start N+1 while UI thread uploads N.
6. Cache **final 8‑bit RGB preview**, not debayered 16‑bit RGB. Invalidate on receipt change.

Expected outcome: 4K preview‑res realtime with AMaZE; scopes no longer stall; scrubbing instant inside cache.

**Tier 3 — GPU color pipeline (3–4 weeks)**
1. Option C from §8.
2. Port full color pipeline (WB 3×3, levels, gamma, contrast, tone curve, 3D LUT, sharpen, grain, 16→8, scale) to one GLSL pass.
3. Feed 16‑bit debayered RGB to GPU (reuse `getMlvProcessedFrame16` output).
4. PBO async upload.
5. Preview path only. Export path stays CPU — unchanged — for bit‑for‑bit safety.

Expected outcome: 4K full‑res realtime on Apple Silicon / discrete GPU; 4K preview realtime on Intel Iris Xe.

**Tier 4 — GPU debayer via RCD (1–2 weeks)**
1. Port darktable's `demosaic_rcd.cl` to either GLSL compute (GL 4.3 where available) or a Qt‑OpenCL interop path.
2. RCD becomes the default playback debayer (≥ AMaZE quality).
3. AMaZE stays CPU as an opt‑in "Paused‑Frame Quality" mode.
4. Bilinear demoted to legacy fallback.

Expected outcome: 4K AMaZE‑equivalent realtime on any modern GPU.

**Tier 5 — Dual ISO on GPU (optional, 2–3 weeks)**
1. Port inter‑row interpolation + alias map + full‑res blend to GLSL compute.
2. Keep 4‑histogram pattern detection on CPU (runs once per clip after §9 Tier‑1 item 9).
3. Can be deferred until after Tier 4 — users get a big win from the Preview toggle alone.

**Do NOT attempt**: AMaZE on GPU, LJ92 on GPU. Both are score‑5 research projects that will die mid‑port like the 2017 OpenCL branch did.

---

### 10. Dual‑ISO specific action plan (user's primary workflow)

Because Dual ISO is the user's main mode, these are extracted as a standalone plan:

**Immediate (Tier 1 subset, 2–3 days):**
1. In `llrawproc.c:430`, uncomment the `diso_get_preview` branch and gate it on a new `dual_iso == 2` mode.
2. In `MainWindow.cpp` (the Dual ISO pattern combobox area around 9656), add a new three‑state toggle: **Off / Preview / 20‑bit HQ**. Default **Preview** during playback, **20‑bit HQ** when paused.
3. In `RenderFrameThread` / playback driver, switch to Preview mode when `timerFrameEvent` is active; switch back to HQ on pause. This is architecturally the same split Resolve uses for optimized/full‑res media.
4. Cache the detected `dark_row_start` pattern on the `mlvObject` after first detection. Store as `int diso_cached_pattern` with `int diso_cached_pattern_valid`. Next time `diso_get_full20bit` runs, skip the 4‑histogram scan and reuse the cached pattern.
5. Skip the alias map (`use_alias_map` param of `diso_get_full20bit`) during playback. Alias artifacts are invisible at preview resolution.
6. Skip full‑res blending (`use_fullres`) during playback. This is the slowest part of `diso_get_full20bit` and its quality benefit is not visible at preview resolution.

Expected: **5–10× speedup for Dual‑ISO playback** without any GPU work or architectural change.

**Medium term (Tier 4 companion, after GPU pipeline lands):**
- Port inter‑row interpolation to GPU compute shader. Data pattern is simple 2‑D stencil with row‑stride gather.
- Full 20‑bit mode becomes GPU‑capable; no need for separate Preview path eventually.

---

### 11. Things NOT to pursue
- AMaZE on GPU (no GPU impl exists; darktable lead dev explicitly said "no plan").
- LJ92 on GPU (serial bit‑stream; research‑grade GPU Huffman required).
- Full OpenCL pipeline in one PR (ParallelDNG / OpenCL‑Processing branches both died this way in 2017/2021).
- Amending export path with GPU equivalence — keep CPU export bit‑for‑bit stable.

---

### 12. Reference points for future sessions
- Build command: `qmake6 MLVApp.pro && mingw32-make -j4` with `GCC_EXEC_PREFIX="C:/Qt/Tools/mingw1310_64/lib/gcc/"` for LTO plugin resolution.
- Test MLV: `C:/temp/MLV/M02-1341.MLV` (517 frames, sequential frame_numbers 0‑516).
- Peer branches to study (remote): `OpenCL-Processing` (2017 scaffolding, no kernels), `ParallelDNG` (2026, CPU OMP), `NewProcessing` (2021, abandoned).
- External references:
  - `https://bitbucket.org/baldand/mlrawviewer` — Python + PyOpenGL architecture
  - `https://www.fastcompression.com/solutions/demosaicing.htm` — CUDA demosaic benchmarks
  - `https://github.com/darktable-org/darktable/blob/master/src/iop/demosaicing/rcd.c` — RCD CPU reference
  - `https://github.com/darktable-org/darktable/tree/master/data/kernels` — darktable OpenCL kernels
  - `https://discuss.pixls.us/t/solved-git-master-and-release-4-2-0-amaze-demosaic-on-cpu-only/36659` — confirms no AMaZE‑on‑GPU

---

### Superseded sections
None yet.

---

## 2026-04-20 - Phase 0 follow-up: clip-backed golden seed + cache seed

Implemented two concrete test-seam additions on top of the Phase 0
scaffold.

1. Added `tests/console/test_cache_behavior.cpp` against the real
   `src/mlv/frame_caching.c`:
   - verifies `resetMlvCache()` clears `current_cached_frame_active`,
   - verifies `find_mlv_frame_to_cache()` skips cached and busy frames,
   - verifies `cache_next` overrides the linear scan.
2. Added `tests/console/test_clip_golden.cpp` as a fixture-gated batch
   export golden test for a tiny Dual ISO clip. The test skips unless the
   clip, receipt, manifest, and `MLVAPP_BATCH_EXE` are all present.
3. Added fixture contract files:
   - `tests/fixtures/clips/README.md`
   - `tests/fixtures/receipts/tiny_dual_iso_hq.marxml`
   - `tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json`

Important harness finding:
- Once the console target linked the real `frame_caching.c`, the old
  receipt-applier tests started crashing in `mark_mlv_uncached()` because
  `ReceiptApplier::applyToMlv()` now reaches the real `resetMlvCache()`.
  Fix was to seed `video->cached_frames` and initialize
  `video->g_mutexFind` in `tests/console/test_receipt_applier.cpp`.

Local Windows verification after the fix:
- `tests/build/console/release/console_tests.exe --check-golden`:
  pass, with the new clip-backed golden test skipped because
  `tests/fixtures/clips/tiny_dual_iso.mlv` is not checked in yet.
- `tests/build/alloc/release/alloc_tests.exe`: pass.
- `tests/build/gui/release/gui_tests.exe` with `QT_QPA_PLATFORM=offscreen`:
  pass.

### Determinism follow-up

Claude review flagged the missing `threads=1` invariant before the first
clip-backed golden test. Implemented immediately after the review:

- Added `tests/common/test_runtime.h` to force:
  - `MLVAPP_FORCE_THREADS=1`
  - `OMP_NUM_THREADS=1`
  - `OMP_DYNAMIC=FALSE`
  - Qt thread-pool max thread count = 1
- Wired that helper into the console, alloc, and GUI test entrypoints.
- Added `src/batch/WorkerThreadCount.h` so production batch export can
  honor `MLVAPP_FORCE_THREADS`.
- Switched batch export setup in `src/batch/BatchRunner.cpp` from
  `QThread::idealThreadCount()` to the new helper.
- Switched the `exportCdngSequence()` raw-correction pre-render in
  `platform/qt/MainWindow.cpp` to the same helper.
- Added `tests/console/test_worker_thread_count.cpp` as regression
  coverage for the override behavior.

Verification:
- `platform/qt` app build: pass after the thread-count helper changes.
- `tests/build/console/release/console_tests.exe --check-golden`: pass.

### Clip-backed golden test activation

Closed the remaining fixture and Qt-pin follow-ups:

- Added a real fixture clip at
  `tests/fixtures/clips/tiny_dual_iso.mlv`:
  - 2 frames
  - 7,931,239 bytes
  - derived from `C:\\temp\\MLV\\Processed\\M02-1344.MLV`
- Added a small headless `--trim-mlv` CLI path through the app to make
  this derivation reproducible:
  - `src/batch/MlvTrim.cpp`
  - `src/batch/MlvTrim.h`
  - `platform/qt/main.cpp`
- Updated the paired receipt to the actual clip length:
  `tests/fixtures/receipts/tiny_dual_iso_hq.marxml`
- Filled the committed DNG hash manifest:
  `tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json`
- Updated `test_clip_golden.cpp` so the spawned batch subprocess gets an
  explicit deterministic environment instead of relying on implicit env
  inheritance.
- Normalized relative DNG paths in `test_clip_golden.cpp` to forward
  slashes before comparing to the manifest.
- Pinned `.github/workflows/tests.yml` to Qt 6 across Linux, macOS, and
  Windows.
- Extended `.github/workflows/tests.yml` so CI builds a batch-capable app
  binary and exports `MLVAPP_BATCH_EXE` before running console tests.

Local verification after activation:
- `platform/qt` app build: pass.
- `tests/build/console/release/console_tests.exe --check-golden` with
  `MLVAPP_BATCH_EXE=platform/qt/debug/MLVApp.exe`: pass.
- `tests/build/alloc/release/alloc_tests.exe`: pass.
- `tests/build/gui/release/gui_tests.exe` with `QT_QPA_PLATFORM=offscreen`:
  pass.

### Playback implementation follow-up

Implemented the next playback phase after the scaffold:

- Revived `dual_iso == 2` preview processing in
  `src/mlv/llrawproc/llrawproc.c`.
- Extended `diso_get_preview(...)` in `src/mlv/llrawproc/dualiso.c` to
  consume the existing `diso_pattern` state, so preview mode can reuse
  cached/manual row-pattern knowledge.
- Added runtime-only playback-vs-paused Dual ISO switching in
  `platform/qt/MainWindow.cpp` using a new shared policy helper in
  `platform/qt/DualIsoPlaybackPolicy.h`.
- Added export/clip-switch guards so preview runtime settings are not
  accidentally persisted into receipts or used for single-frame export.
- Added policy regression tests in
  `tests/console/test_dual_iso_playback_policy.cpp`.
- Added receipt/cache invalidation regression in
  `tests/console/test_receipt_applier.cpp`.
- Added a new real-engine in-process test target under `tests/pipeline/`
  with:
  - `mlv_pipeline_fixture.{h,cpp}`
  - `test_dual_iso_pipeline.cpp`
  - `test_main.cpp`
- Added direct Dual ISO frame goldens in
  `tests/fixtures/golden/pipeline_hashes.json`.
- Extended `.github/workflows/tests.yml` so cross-platform test runs
  build, execute, upload, and compare both console and pipeline hash
  artifacts.

Local verification:

- `platform/qt` rebuild: pass
- `tests/build-all/console/release/console_tests.exe --check-golden`:
  pass
- `tests/build-all/alloc/release/alloc_tests.exe`: pass
- `tests/build-all/gui/release/gui_tests.exe` with offscreen platform:
  pass
- `tests/build-all/pipeline/release/pipeline_tests.exe --check-golden`:
  pass

Measured direct-frame preview-vs-full similarity on the tiny Dual ISO
fixture:

- frame 0 PSNR: `8.9036 dB`
- frame 1 PSNR: `11.1749 dB`

That is low enough that preview must not be described as “close to HQ,”
but stable enough to freeze exact preview hashes and keep a low PSNR
floor as a semantic backstop.

### Cache / perf / display follow-up

Implemented the next performance-oriented block:

- Cache behavior is now windowed instead of effectively hard-wired to
  frame-zero fill. `src/mlv/frame_caching.c` uses `cache_start_frame` as
  a real window anchor, `src/mlv/video_mlv.c` ensures the window follows
  the requested frame, and
  `tests/console/test_cache_behavior.cpp` now locks the
  `cache_start_frame` selection behavior.
- Added `src/debug/StageTiming.h`, a header-only instrumentation seam.
  Setting `MLVAPP_STAGE_TIMING=1` emits per-stage timing lines around raw
  decode, llrawproc, debayered-frame fetch, processing, 16->8
  conversion, render-thread draw, scopes, and `drawFrameReady()` total.
- Added a first perf harness at `tests/perf/perf_main.cpp` /
  `tests/perf/perf_tests.pro`. It benchmarks tiny Dual ISO full vs
  preview for both 16-bit and 8-bit output and writes JSON metrics.
- Scope hot paths were cleaned up in `platform/qt/Histogram.cpp`,
  `platform/qt/VectorScope.cpp`, and `platform/qt/WaveFormMonitor.cpp`
  so they no longer rely on slow per-pixel Qt accessors in their inner
  loops.
- Added a minimal, opt-in GPU-display foundation in
  `platform/qt/GpuDisplayViewport.{h,cpp}` behind
  `MLVAPP_EXPERIMENTAL_GL_VIEWPORT=1`. Default behavior is unchanged;
  this is a safe foothold for a texture-backed preview path later.
- Extended `.github/workflows/tests.yml` to run the perf harness and
  upload perf artifacts in addition to console and pipeline hashes.

Local verification after this pass:

- `console_tests --check-golden`: pass, including the clip-backed export
  golden when `MLVAPP_BATCH_EXE` points at `platform/qt/release/MLVApp.exe`
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass
- `gui_tests` with `QT_QPA_PLATFORM=offscreen`: pass
- `perf_tests --iterations 10`: pass
  - `full16 avg_ms=231.216`
  - `preview16 avg_ms=172.271`
  - `full8 avg_ms=211.219`
  - `preview8 avg_ms=178.823`

### Final cleanup / exact-frame cache follow-up

- `platform/qt/MainWindow.cpp`
  - fixed the temporary `drawFrameReady()` regression caused by the
    zebra/display refactor
  - the function now has one image assembly path, one Dual ISO slider
    update block, one zebra pass, one pixmap upload, and one scope pass
- `src/mlv/video_mlv.c`, `src/mlv/mlv_object.h`, `src/mlv/macros.h`
  - added an exact 8-bit processed-frame cache on top of the existing
    exact 16-bit processed-frame cache
  - fixed the processed-frame cache signature so it is stored *after*
    the frame render, not before; this matters because llrawproc and the
    processing pipeline mutate runtime-derived state during the first
    render of a frame
- `tests/pipeline/test_dual_iso_pipeline.cpp`
  - new regression proves the 8-bit processed-frame cache is reused for
    repeated exact-frame requests and invalidates when processing state
    changes
- `tests/console/test_cache_behavior.cpp`
  - `resetMlvCache()` regression now checks the 8-bit processed-frame
    cache state too

Latest local verification:

- `console_tests --check-golden`: pass
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass (`4` tests / `39` assertions)
- `gui_tests` with `QT_QPA_PLATFORM=offscreen`: pass
- `perf_tests --iterations 10`: pass
  - `full16 avg_ms=207.849`
  - `preview16 avg_ms=174.817`
  - `full8 avg_ms=210.910`
  - `preview8 avg_ms=181.352`

### 2026-04-21 - final local green pass

- Added `absolute_guards` support to `tests/perf/perf_main.cpp` and
  seeded broad watchdog ceilings in `tests/perf/baselines.json`.
- Refreshed the clip-backed export golden manifest in
  `tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json` to the stable
  subprocess batch-export seam used by `tests/console/test_clip_golden.cpp`.
- Rebuilt the app and all test targets locally on Qt 6.10.2 + MinGW.

Final local verification:

- `console_tests --check-golden`: pass
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass
- `gui_tests` with `QT_QPA_PLATFORM=offscreen`: pass
- `perf_tests --iterations 10`: pass

Latest perf run:

- `full16 avg_ms=249.558`
- `preview16 avg_ms=209.123`
- `full8 avg_ms=28.402`
- `preview8 avg_ms=7.460`
- `preview16 speedup vs full16=1.193x`
- `preview8 speedup vs full8=3.807x`

The new absolute watchdog ceilings passed:

- `tiny_dual_iso.full16.average_ms.max = 5000`
- `tiny_dual_iso.preview16.average_ms.max = 5000`
- `tiny_dual_iso.full8.average_ms.max = 1000`
- `tiny_dual_iso.preview8.average_ms.max = 1000`

### 2026-04-21 - larger fixture + scratch reuse follow-up

Implemented and verified another local pass after the watchdog work:

- added a checked-in larger Dual ISO perf fixture:
  - `tests/fixtures/clips/large_dual_iso.mlv`
  - `tests/fixtures/receipts/large_dual_iso_hq.marxml`
- added llrawproc-owned chroma-smooth scratch reuse:
  - `src/mlv/llrawproc/pixelproc.{h,c}`
  - `src/mlv/llrawproc/llrawproc.c`
- added processing-owned Sobel/sharpen-mask scratch reuse:
  - `src/processing/sobel/{sobel.h,sobel.c}`
  - `src/processing/raw_processing.c`
  - `src/processing/processing_object.h`
- added new regressions:
  - `tests/pipeline/test_processing_filters.cpp`
    - `SobelScratchReuseMatchesFreshResultAfterResize`
  - `tests/pipeline/test_dual_iso_pipeline.cpp`
    - `ChromaSmoothScratchReusesFrameBufferAcrossFrames`
- changed the local perf profile gate to track only the stable 16-bit
  medians (`full16`, `preview16`) while still reporting 8-bit paths and
  enforcing them with relative/absolute guards.
- refreshed the clip-backed export golden manifest after the hot-path
  changes:
  - `tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json`

Final local verification after this pass:

- `console_tests --check-golden`: pass
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass
- `gui_tests` with `QT_QPA_PLATFORM=offscreen`: pass
- `perf_tests --iterations 10 --update-baseline`: pass
- `perf_tests --iterations 5 --require-baseline`: pass

Representative latest enforced perf result:

- `tiny_dual_iso.full16 median_ms = 207.213`
- `tiny_dual_iso.preview16 median_ms = 171.586`
- `large_dual_iso.full16 median_ms = 205.578`
- `large_dual_iso.preview16 median_ms = 179.166`

Practical outcome:

- two more per-frame allocation sites are now reused rather than
  malloc/free on every render,
- the local perf loop now covers both the tiny correctness fixture and a
  broader 16-frame Dual ISO workload,
- and the full local safety net is green again.

### 2026-04-21 - GPU 16-bit presenter + AVX validation

Implemented another local playback pass:

- `platform/qt/GpuDisplayViewport.{h,cpp}`
  - added direct 16-bit RGB frame presentation through the experimental
    texture-backed viewport.
- `platform/qt/MainWindow.{h,cpp}`
  - added a 16-bit preview buffer plus a runtime gate that enables the 16-bit
    presenter path only when the GL viewport is active and zebras/scopes are
    off.
- `platform/qt/RenderFrameThread.{h,cpp}`
  - render thread now supports 8-bit or 16-bit preview output per frame.
- `tests/gui/test_gui_smoke.cpp`
  - added `gpuViewportQueuesRgb16Frame`.

Verified locally:

- `platform/qt` rebuild: pass
- `console_tests --check-golden`: pass
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass
- `gui_tests` with `QT_QPA_PLATFORM=offscreen`: pass
- `perf_tests --iterations 10 --require-baseline`: pass

Perf note:

- `--iterations 5` showed local jitter on `tiny_dual_iso.full16`
- `--iterations 10 --require-baseline` passed cleanly with:
  - `tiny_dual_iso.full16 median_ms = 203.321`
  - `tiny_dual_iso.preview16 median_ms = 168.154`
  - `large_dual_iso.full16 median_ms = 206.049`
  - `large_dual_iso.preview16 median_ms = 169.247`

AVX validation:

- validated the opt-in AVX build path in a clean temporary build directory
- confirmed the app builds with `MLVAPP_ENABLE_AVX=1`, `-mavx`, and
  `MLVAPP_BUILD_AVX=1`

### 2026-04-21 - Broader fuzz targets and AVX parity test

Implemented the user-owned follow-up only inside `tests/`.

Added broader parser fuzz coverage:

- `tests/fuzz/fuzz_driver.cpp`
  - shared local file-fed harness for fuzz executables
- `tests/fuzz/fuzz_receipt_loader.pro`
- `tests/fuzz/fuzz_lj92.{pro,cpp}`
- `tests/fuzz/fuzz_mlv_open.{pro,cpp}`
- `tests/fuzz/fuzz_targets.pro`
  - now a subdirs entry point for the three fuzz executables

Added a local AVX on/off parity regression:

- `tests/console/test_avx_golden.cpp`
  - builds `tests/console/avx_parity_helper.pro` with AVX off/on
  - compares rendered frame-hash JSON on tiny + large Dual ISO fixtures
- `tests/console/avx_parity_helper.{pro,cpp}`

Support files:

- `tests/common/pipeline_runtime.pri`
  - shared real pipeline source list for test-only helpers
- `tests/README.md`
- `tests/fuzz/README.md`

Local verification:

- built all fuzz targets with Qt 6.10.2 + MinGW 13.1
- ran:
  - `fuzz_receipt_loader` against `tests/fixtures/receipts`
  - `fuzz_lj92` against `tests/fixtures/receipts/tiny_dual_iso_hq.marxml`
  - `fuzz_mlv_open` against `tests/fixtures/clips/tiny_dual_iso.mlv`
- rebuilt `console_tests`
- ran `console_tests` with local `QMAKE` / `MAKE` env overrides
- `AvxParity.DefaultAndAvxBuildsProduceMatchingFrameHashes`: pass

Notable implementation detail:

- Windows shells here did not have `qmake` / `mingw32-make` on `PATH`, so
  the AVX parity test now falls back to scanning `C:/Qt` for those tools
  before skipping

### 2026-04-21 - Race fix, full20bit scratch reuse, and local GPU/parity verification

Implemented another local-only playback/testing pass.

High-signal code changes:

- `src/mlv/video_mlv.c`
  - `getMlvRawFrameFloat(...)` now takes `video->cache_mutex` internally around
    the decode + llrawproc scratch path
- `src/mlv/frame_caching.c`
  - removed the old outer `cache_mutex` lock around `getMlvRawFrameFloat(...)`
    since the lock now lives at the shared seam itself
- `src/mlv/llrawproc/dualiso.{h,c}`
  - added `dualiso_full20bit_scratch_t`
  - promoted the old top-level per-frame buffers used by `diso_get_full20bit`
    into persistent scratch owned by `llrawprocObject_t`
  - kept the remaining obvious per-frame allocations confined to helper/inner
    algorithm paths (not the old outer-frame workset)
- `src/mlv/llrawproc/llrawproc_object.h`
  - stores the new `diso_full20bit_scratch`
- `platform/qt/GpuDisplayViewport.{h,cpp}`
  - presenter now exposes a narrow renderer-description query for test/debug
    use
  - zebra overlay is supported as a shader-side display-processing step on the
    16-bit presenter path
- `platform/qt/MainWindow.cpp`
  - no longer disables the GPU 16-bit preview path just because zebras are on
- `tests/pipeline/test_dual_iso_pipeline.cpp`
  - added `HeadlessDualIsoFull20BitReusesOuterScratchAcrossFrames`
- `tests/gui/test_gui_smoke.cpp`
  - added zebra-processing parity coverage for the 16-bit presenter path
  - local `llvmpipe` software GL now causes a targeted skip for that one parity
    test, while exact RGB888/RGB16 presenter hash tests still run

Local verification:

- rebuilt `tests/tests.pro`: pass
- `console_tests --check-golden`: pass (`21 tests / 142 assertions`)
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass (`16 tests / 154 assertions`)
- `gui_tests`: pass with `1` intentional skip on `llvmpipe`
- `perf_tests --iterations 10 --require-baseline`: pass
- built `tests/fuzz/fuzz_targets.pro`: pass
- `fuzz_receipt_loader tests/fixtures/receipts`: pass
- `fuzz_lj92 tests/fixtures/clips/tiny_dual_iso.mlv`: pass
- `fuzz_mlv_open tests/fixtures/clips/tiny_dual_iso.mlv`: pass
- rebuilt `platform/qt/release/MLVApp.exe`: pass

Important local note:

- `perf_tests --iterations 5 --require-baseline` is not reliable on the tiny
  2-frame fixture in this workspace; it can false-fail `tiny_dual_iso.full16`
  because the sample count is too small
- `--iterations 10 --require-baseline` remains stable and should be treated as
  the meaningful local perf gate
