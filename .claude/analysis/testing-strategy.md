# MLV‑App Testing Strategy

Updated: 2026‑04‑20

Counterpart docs: `.claude/analysis/mlv-playback-investigation.md`, `.claude/ANALYSIS_LOG.md`, `/AGENTS.md`, `.claude/CLAUDE.md`.

This note defines the regression‑safety plan for the optimization work in progress (per‑frame allocator elimination, cache refactor, Dual ISO preview path, GPU pipeline, Qt OpenGL migration). It exists because the planned changes are all high‑risk: they touch pipeline stages whose output is not currently validated anywhere.

---

## 1. Current test coverage — measured

### Verified locally
- `.github/workflows/Windows.yml`, `Linux.yml`, `macOS-Intel.yml`, `macOS-Arm64.yml` — **build‑only.** No tests are invoked. CI success means "it compiled," nothing more.
- `platform/binning_test/` — standalone `main.c` for one algorithm. Not a harness.
- `platform/qt/avir/other/frtest.cpp` — avir library's internal test, not MLV‑App's.
- No gtest, no QTest, no pytest, no Catch2, no `CTest` registration anywhere.
- `receipts/*.marxml` — XML files that fully serialize processing settings. **This is the pre‑built anchor we will use for reproducible test inputs.**

### Implication
Every optimization proposed in the playback investigation ships blind today. Allocator changes in `denoiser_2d_median.c` could alter one pixel by 1 LSB and no one would notice until a user comparing an export against a reference saw it. The Dual ISO preview‑mode revival could produce subtly wrong interpolation at row boundaries. A GPU port could shift colors by 0.5 % and pass human eyeball review.

We have to bootstrap the harness before the risky work lands, not after.

---

## 2. Framework choice

### Decision: QTest as the unified harness, plus a thin header‑only C matcher.

Rationale:

- **Qt is already a hard dependency** — no new third‑party framework is added.
- **qmake `subdirs` TEMPLATE** cleanly supports adding a `tests/` subtree beside `platform/qt/`, with its own `.pro` files that reuse the source tree.
- **QTest supports data‑driven tests** (`QTEST_MAIN` + `_data()` functions) — ideal for matrix‑style (clip × receipt × frame) coverage.
- **QTest emits XML** consumable by GitHub Actions and any test reporter.
- **Pure C code** (`src/mlv/*.c`, `src/debayer/*.c`, `src/processing/*.c`) can be exercised from C++ test wrappers. The `extern "C"` boundaries in the existing code already support this.
- **Cross‑platform out of the box** — we target the same three platforms as the CI already builds on.

A ~50‑line header `tests/common/frame_compare.h` provides the tolerance‑based frame matcher used across categories:

```c
typedef struct {
    double psnr_db;          // higher is better; 60 dB = near bit‑exact 8‑bit
    uint16_t max_abs_diff;   // worst single‑channel delta
    double mean_abs_diff;    // average delta
    uint64_t pixels_exceeding_tolerance;
} frame_compare_result_t;

frame_compare_result_t compare_frames_u16(
    const uint16_t *ref, const uint16_t *got,
    int width, int height, int channels,
    uint16_t per_pixel_tolerance);

frame_compare_result_t compare_frames_u8(
    const uint8_t *ref, const uint8_t *got,
    int width, int height, int channels,
    uint8_t per_pixel_tolerance);
```

Thresholds are per‑test. Bit‑exact paths (LJ92 decode) use `per_pixel_tolerance = 0`. Floating‑point pipeline stages (color processing) use `per_pixel_tolerance = 1` (1 LSB). GPU/CPU parity uses `per_pixel_tolerance = 2` with `psnr_db >= 60`.

---

## 3. Test fixture layout

```
tests/
├── tests.pro                       # TEMPLATE = subdirs
├── common/
│   ├── frame_compare.h / .c
│   ├── mlv_loader.h / .c            # boilerplate for openMlv + applyReceipt
│   └── hash_helpers.h / .c          # SHA‑256 of frame bytes
├── fixtures/
│   ├── clips/
│   │   ├── tiny_non_diso.MLV        # ≤8 frames, 640×360, checked in
│   │   ├── tiny_diso.MLV            # ≤8 frames, 1920×1080 dual ISO, checked in
│   │   ├── tiny_diso_compressed.MLV # ≤8 frames, LJ92 compressed dual ISO
│   │   └── README.md                # provenance, camera, ISO settings
│   ├── receipts/
│   │   ├── identity.marxml           # all settings at neutral
│   │   ├── diso_hq.marxml            # dual_iso=1, AMaZE interp, alias on, fullres on
│   │   ├── diso_fast.marxml          # dual_iso=1, Mean interp, alias off, fullres off
│   │   ├── diso_preview.marxml       # dual_iso=2 (after preview path re‑enable)
│   │   ├── heavy_grade.marxml        # max sharpen / clarity / RBF / denoise
│   │   └── with_lut.marxml           # + checked‑in .cube LUT
│   ├── luts/
│   │   └── fixture_709_to_sRGB.cube
│   └── golden/
│       ├── hashes.json              # {clip, receipt, frame_idx} → sha256
│       └── reference_frames/        # PNG dumps for visual diff on failure (git LFS)
├── unit/
│   ├── test_lj92.cpp                 # §4.1
│   ├── test_llrawproc_stages.cpp     # §4.2
│   ├── test_debayer_bilinear.cpp     # §4.3
│   ├── test_debayer_amaze.cpp
│   ├── test_debayer_rcd.cpp
│   ├── test_processing_color.cpp     # §4.4
│   ├── test_processing_identity.cpp  # settings at zero → output ≈ input
│   ├── test_dualiso_preview.cpp      # §4.5
│   ├── test_dualiso_full20bit.cpp
│   ├── test_cache_invariants.cpp     # §4.6
│   └── test_frame_compare.cpp        # the matcher itself
├── integration/
│   ├── test_render_thread.cpp        # §5.1
│   ├── test_batch_cli.cpp            # §5.2 — leverages existing --batch
│   ├── test_receipt_roundtrip.cpp    # §5.3
│   └── test_resume_feature.cpp       # existing --resume covered
├── perf/
│   ├── bench_playback_non_diso.cpp   # §6
│   ├── bench_playback_diso.cpp
│   ├── bench_debayer.cpp
│   └── baselines.json                # ms/frame per clip+receipt, hardware‑keyed
├── gui/
│   ├── test_zoom_pan.cpp             # §7 QTest GUI
│   ├── test_drop_files.cpp
│   └── test_scopes_update.cpp
├── gpu_parity/                        # §8 (added when GPU work starts)
│   └── test_color_pipeline_gpu_vs_cpu.cpp
└── fuzz/                              # §10
    ├── fuzz_mlv_header.cpp
    └── fuzz_lj92_stream.cpp
```

Clips are **tiny** to stay in‑git. The `MLV_TEST_CORPUS` env var points at a local directory of larger real‑world clips for nightly perf runs; tests skip gracefully when unset.

---

## 4. Unit test categories

### 4.1 LJ92 decode — bit‑exact

**What:** Given a canonical LJ92 byte stream (extracted from `tiny_diso_compressed.MLV`), decode and hash the output.
**Guards against:** Regressions in `src/mlv/liblj92/lj92.c`, `SLOW_HUFF` re‑enablement accidents, Huffman‑table corruption.
**Tolerance:** `per_pixel_tolerance = 0`. Exact.
**Data:** 5 different LJ92 streams covering 10/12/14‑bit and different slice configurations.
**Implementation:** Extract the compressed bytes to a `.bin` fixture at harness‑bootstrap time; tests feed them to `lj92_open` / `lj92_decode` directly.

### 4.2 llrawproc stages — per‑stage isolation

**What:** `applyLLRawProcObject` is a chain. Each stage (`stripes`, `focus_pixels`, `bad_pixels`, `pattern_noise`, `chroma_smooth`, `dual_iso`, `dark_frame`) gets a test that:
1. Loads a known bayer frame.
2. Enables *only* that stage (all others off).
3. Compares output against a checked‑in hash.

Individual stage tests catch the common mode "I rewrote the denoiser allocator and now bad_pixels is off by 1."

**Guards against:** Tier 1 per‑frame allocator refactor regressions, `double → float` conversion fidelity loss, OpenMP reordering bugs, Dual ISO cache‑of‑pattern detection introducing stale cache bugs.
**Tolerance:** `per_pixel_tolerance = 0` for deterministic integer ops; `per_pixel_tolerance = 1` for ops with float internals (pattern noise, dual ISO).

### 4.3 Debayer — algorithm isolation

**What:** For each debayer algorithm (`bilinear`, `AMaZE`, `RCD`, `LMMSE`, `IGV`, `AHD`, `DCB`):
1. Feed a fixed bayer test pattern — the canonical `data/GMB_CC24.bayer` color checker is a common industry reference.
2. Compare the debayered 16‑bit RGB output.
**Guards against:** The planned tile‑parallel debayer refactor, `pthread_create` pooling refactor, and future GPU RCD port.
**Tolerance:** `per_pixel_tolerance = 0` for bilinear (deterministic); `per_pixel_tolerance = 1` for AMaZE/RCD (float convergence).

### 4.4 Color pipeline — `applyProcessingObject` matrix test

**What:** Data‑driven: `(receipt, bayer_input, expected_rgb_hash)`. Each row runs the full processing chain and compares against a golden hash.
**Guards against:** Everything in `src/processing/` — `double → float` exposure correction (Tier 1 item), early‑out for identity receipts, 3D LUT tetrahedral replacement, GPU color pipeline port (Tier 3).
**Tolerance:** `per_pixel_tolerance = 1`.
**Key cases:**
- Identity receipt → output pixel mean should equal input pixel mean within 1 LSB.
- Heavy grade receipt → golden hash.
- 3D LUT receipt → golden hash.
- Each individual control (exposure, contrast, pivot, temperature, tint, sharpen, clarity, RBF, shadows, highlights, gradation curves, HvH, HvS, HvL, LvS, saturation, vibrance) at a known non‑zero value.

### 4.5 Dual ISO — dedicated suite

Given this is the user's primary workflow and the largest optimization target, it gets its own test file with particular thoroughness:

| Test | Guards against |
|---|---|
| `diso_get_preview` output hash (after path re‑enable) | Preview path re‑enabling regressing quality |
| `diso_get_full20bit` with `interp_method=0` (AMaZE‑edge) hash | Method‑0 allocator refactor |
| `diso_get_full20bit` with `interp_method=1` (`mean23_interpolate`) hash | Method‑1 changes |
| Preview vs Full20bit output difference (PSNR ≥ 25 dB) | "Preview not meaningfully similar to full" regression |
| Pattern cache invariant: detect on frame 0, assert same pattern returned on frame 1..N without re‑detection | Pattern‑cache refactor (Tier 1 item) |
| Auto exposure match: run on frame 0, assert same values reused on frame 1..N when "freeze after solve" is enabled | Auto‑match freeze refactor |
| Alias map on vs off: different hashes, but no crashes/OOB | Alias map path |
| Full‑res blend on vs off: different hashes | Blend path |
| Cache reset on DualISO setting change: assert `cached_frames[]` zeroed after `llrpSetDualIsoInterpolationMethod` | Cache reset semantics |

### 4.6 Cache invariants

**What:** Non‑output tests about cache behavior:

- Cached frame bytes == uncached frame bytes (cache fidelity).
- Receipt change invalidates cached final‑preview frames.
- `cache_start_frame` → cache fills centered on that frame (after Tier 1 fix).
- Concurrent `find_mlv_frame_to_cache` calls never return the same index (mutex correctness).
- Cache fill under ThreadSanitizer shows no data race.
- Cache does not serialize `getMlvRawFrameFloat` after the `cache_mutex` removal (assert two cache threads progress in parallel via atomic counter).

**Guards against:** Cache refactor breaking correctness or concurrency.

### 4.7 Receipt round‑trip

**What:** Load `.marxml` → apply → serialize → load → apply → compare outputs. Must be identical.
**Guards against:** Receipt schema drift, especially around the in‑flight Dual ISO mode‑2 addition.

---

## 5. Integration tests

### 5.1 Render thread

**What:** Drive `RenderFrameThread` with a fixed sequence of playback requests. Assert:
- Exactly one `frameReady` signal per request.
- Output bytes identical to direct call of `getMlvProcessedFrame8`.
- Double‑buffer invariant (after Tier 2 refactor): `m_pRawImage` swap does not tear.

### 5.2 Batch CLI — byte‑for‑byte DNG diff

**What:** The `--batch` mode is already working (your recent feature). It produces CDNG sequences. Tests:
1. Run `--batch --input tiny_diso.MLV --output /tmp/out1 --receipt diso_fast.marxml`.
2. Compute SHA‑256 of every output `.dng`.
3. Compare against checked‑in golden hashes in `tests/fixtures/golden/batch_dng_hashes.json`.

This is the **single highest‑leverage regression test** because:
- It exercises the full pipeline end‑to‑end.
- It's cheap to run (headless, no Qt GUI required for the export path — hence the `--batch` mode).
- CDNG output is deterministic and byte‑reproducible.
- It's already parameterizable via CLI args.

Add a separate test for `--resume` to protect the existing feature from regression.

### 5.3 Cross‑platform parity

**What:** Build on Linux / macOS / Windows CI runners. Run the unit matrix on each. Output hashes must match across platforms (with the tolerance expected — integer paths bit‑exact, float paths ±1 LSB).
**Guards against:** MinGW vs GCC vs Clang vs Apple‑Clang floating‑point drift, library version differences (librtprocess, ffmpeg).

---

## 6. Performance regression tests

A separate test binary `tests/perf/bench_*`, invoked by a **nightly** CI job (not every PR — they're slow and sensitive to hardware noise).

### Design
- Each bench: load clip → apply receipt → loop N frames with a warmup → record p50/p90/p99 ms/frame.
- Output a JSON summary: `{hardware_id, git_sha, date, clip, receipt, p50_ms, p90_ms, p99_ms}`.
- Baselines live in `tests/perf/baselines.json`, keyed by hardware tag.
- Test fails if `p50 > baseline * 1.15` (15 % regression threshold; tighter thresholds give false positives from CI noise).
- Result artifact uploaded to GitHub Actions → trend graph via a tiny `bench_plot.py`.

### Coverage
- Playback non‑DualISO, AMaZE, 1080p, identity receipt.
- Playback DualISO Full20bit HQ, AMaZE interp, alias+fullres, 1080p.
- Playback DualISO preview mode (post Tier 1), 1080p — should be ≥ 5× faster than HQ baseline.
- Export (batch CLI) 100 frames to DNG, DualISO.
- Cache fill rate: frames/second with 4 cache threads.
- Cold vs warm cache playback ratio.

### CI runners
GitHub's standard runners are noisy. Recommended: use `runs-on: self-hosted` with a dedicated perf box, fallback to public runners for relative comparison within a single job (not cross‑job).

---

## 7. Qt GUI tests (QTest)

**What:** QTest's signal/slot testing + `QTest::mouseClick` / `QTest::keyClick`.

- Zoom (mouse wheel) — assert scene transform changes and anchor‑under‑cursor holds.
- Drop MLV on window — assert `filesDropped` signal carries correct path.
- Pick WB eyedropper — click pixel, assert `wbPicked` signal with correct scene coords.
- Scope widgets update when frame changes — assert paint event fires exactly once per `frameReady`.
- Save receipt → close → reopen → assert settings preserved.

**Guards against:** The Option B/C Qt OpenGL migration breaking picker coord math, zoom logic, or gradient overlay.

**Constraint:** QTest GUI tests require a display / offscreen platform. Use `QT_QPA_PLATFORM=offscreen` in CI; confirmed supported on all three CI platforms.

---

## 8. GPU / CPU parity tests (added with Tier 2 work)

Once any stage runs on GPU, it gets a parity test:

1. Process frame via CPU path → reference bytes.
2. Process same frame via GPU path → test bytes.
3. Assert `psnr_db >= 60` and `max_abs_diff <= 2` (8‑bit) / `4` (16‑bit).

Specific cases:
- Display scaling (Option B): GPU bilinear vs Qt `SmoothTransformation`.
- Color pipeline (Option 3 / Tier 3): full pipeline GPU vs CPU.
- RCD debayer (Tier 4): GPU RCD vs CPU RCD via librtprocess.

Parity tests are **required** before any GPU path can be default‑enabled. Behind a feature flag is fine; default‑on requires passing parity.

---

## 9. Memory and allocation tests

Given the Tier 1 target is removing per‑frame allocations, we need a test that actually measures them.

### Approach
- A macro‑based tracking allocator installed only in test builds: `tests/common/tracking_alloc.h` overrides `malloc` / `free` / `new` / `delete` via LD_PRELOAD (Linux), `DYLD_INSERT_LIBRARIES` (macOS), or import‑table patching (Windows).
- Each test records total allocations during a "measured region" (typically one frame render).
- Per‑test budget: `EXPECT_LE(allocs_per_frame, budget);`

### Post‑Tier‑1 targets
| Stage | Current allocs/frame (est.) | Post‑Tier‑1 budget |
|---|---|---|
| `denoiser_2d_median` active | ~6 M | ≤ 1 |
| `rbf_wrapper` active | 1 large | 0 |
| AMaZE debayer | 8 large | 0 |
| `diso_get_full20bit` | ~12 | ≤ 2 |
| `patternnoise` | 3 | 0 |
| `chroma_smooth` | 1 | 0 |
| `getMlvProcessedFrame8` 16‑bit intermediate | 1 | 0 |
| `drawFrameReady` avir path | 1 + thread pool | 0 |

Tests start as "measure" (record current value) and tighten to "assert" once the refactor lands.

### Valgrind / ASAN / TSAN
- **ASAN** on every unit test run in CI (adds ~2× time but catches use‑after‑free in allocator refactor).
- **TSAN** on cache invariant tests and `test_render_thread` (catches races in the `cache_mutex` removal).
- **Valgrind massif** nightly on one bench run to track heap high‑water.

---

## 10. Fuzz tests

Low priority but important for release confidence:

- `fuzz_mlv_header.cpp` — LibFuzzer‑style harness around `openMlvClipNew` with random byte input. Protects against crashes when opening corrupted clips.
- `fuzz_lj92_stream.cpp` — harness around `lj92_open` / `lj92_decode` with randomly truncated / mutated compressed streams.
- `fuzz_marxml.cpp` — receipt XML parser with random well‑formed and malformed inputs.

Run nightly in CI with a fixed corpus + timer. OSS‑Fuzz submission possible long‑term.

---

## 11. Known test gaps (to be filled)

Things not covered and must be called out in PRs that touch them:

- **HDR output paths** (HDRx, 20‑bit DNG) — no test clips.
- **mcraw ingest** — we need a small mcraw fixture.
- **RAW2MLV convert tool** — separate binary, not tested.
- **FFmpeg H.264 / ProRes export** — non‑deterministic byte output; would need tolerance‑based comparison.
- **Gradient polygon overlay** — QTest GUI test missing.
- **Histogram / waveform / vectorscope pixel correctness** — only paint‑once coverage today.
- **i18n / translation files** — untested.
- **macOS Cocoa legacy path** (`platform/cocoa/`) — mostly abandoned but still compiles; leave as‑is.

---

## 12. Rollout plan

### Phase 0 — Bootstrap (1 week)
- Add `tests/` subtree and `tests.pro`.
- Wire into `.github/workflows/*.yml` as a new "Tests" step after build.
- Implement `frame_compare.h/c` + `mlv_loader.h/c` + `hash_helpers.h/c`.
- Write 3 seed tests: LJ92 bit‑exact, identity‑receipt pipeline, `--batch` CLI byte‑diff.
- Checked‑in tiny fixture clips (curate from user's own M02‑1341 or public domain).

### Phase 1 — Coverage for Tier 1 refactor (1 week)
- Add all §4 unit tests *before* the per‑frame allocator refactor.
- Run them as the baseline "golden" state.
- Land the Tier 1 optimizations one PR at a time. Each PR must pass the existing golden tests (no regression) and ideally add new memory‑budget tests (reduced alloc count).

### Phase 2 — Coverage for Dual ISO preview path (1 week)
- Before re‑enabling `diso_get_preview`, write §4.5 tests at the CURRENT state (dual_iso=1 only).
- Add `diso_get_preview` golden tests as the path comes online.
- Pattern‑cache invariant tests validate the clip‑level cache.

### Phase 3 — GPU parity tests (added alongside Tier 2 work)
- §8 parity tests land in the same PRs as the GPU code.
- Feature‑flag‑gated until parity is green.

### Phase 4 — Performance regression baselines (1 week, after Tier 1)
- Nightly perf job captures baselines on a dedicated self‑hosted runner.
- Tier 1 fixes are measured — if they don't show a win in the bench, they are not merged.

---

## 13. Non‑negotiables

1. **No PR lands in `src/mlv/`, `src/debayer/`, `src/processing/`, `src/mlv/llrawproc/` without a passing golden‑frame regression test that exercises the touched code.** After Phase 1 completes, this is a hard CI gate, not a soft review preference.
2. **No GPU code path ships default‑enabled without parity tests passing.**
3. **No Dual ISO change ships without the §4.5 Dual ISO suite passing.** The user's primary workflow.
4. **Performance claims require a number in the commit message.** "Optimize RBF: 23 % faster on 1080p DualISO (p50 42.3 → 32.6 ms)" — not "Speed up RBF."
5. **Cross‑platform hash drift is treated as a bug**, not accepted as "floating point." If Linux and Windows produce different output, one of them is wrong — file a bug and investigate.

---

## 14. Open questions / decisions needed from user

1. **Test clip corpus:** does the user have permission to check in a few‑frame extract of `M02-1341.MLV`, or do we use only public‑domain sample clips (e.g., from the MLV sample collection in `hudsonmartins/mlv-sample-files`)?
2. **Self‑hosted perf runner:** does the user want to dedicate a machine for nightly perf runs, or accept GitHub‑runner noise for now?
3. **Dependency on Python:** a couple of helpers (bench plotting, fuzz corpus management) are easier in Python. OK to introduce Python 3 as a test‑only dependency?
4. **Tolerance choices:** current defaults (`per_pixel_tolerance = 0/1/2` by stage) are engineering judgment. Does the user want to ratchet tighter after stability is established?

---

## 14.5. Reconciliation with Codex's testing strategy (added 2026‑04‑20 later that day)

Codex independently produced a layered testing strategy and embedded it in `.claude/analysis/mlv-playback-investigation.md` §"Regression Test Strategy". The two strategies converge on all essentials. This section records where they complement each other and what merges into a single plan.

### Where Codex adds value this doc missed
1. **`threads=1` determinism rule for golden tests.** `src/mlv/video_mlv.h:58–63` explicitly warns that multithreaded processed‑frame generation is preview‑oriented and may have minor artifacts. Golden correctness tests must force single‑threaded execution. **Adopt as a test harness invariant.**
2. **`ReceiptApplier::printFingerprint()` as a ready‑made assertion seam** at `src/batch/ReceiptApplier.cpp:22–299, 310–360`. Tests can snapshot the receipt‑to‑runtime mapping without inspecting pipeline internals. **Use for Layer‑1 receipt‑mapping tests instead of hand‑writing field comparators.**
3. **Layering by cost/speed rather than by category:**
   - Layer 1: Pure unit tests (logic + state, no image buffers) — milliseconds
   - Layer 2: Core pipeline microtests on synthetic buffers — seconds
   - Layer 3: Golden‑frame integration tests on tiny fixture clips — tens of seconds
   - Layer 4: Headless batch/receipt regression tests — minutes
   - Layer 5: Performance benchmarks — nightly, not every PR
   This is a cleaner mental model than this doc's category split. **Adopt as the directory structure.**
4. **Receipt‑to‑runtime mapping tests** (not just output tests) — verify that `ReceiptApplier::applyToMlv()` sets the exact llrawproc/processing fields a given `.marxml` specifies. Protects against the Dual‑ISO mode‑2 plumbing going wrong when we wire up the preview path.

### Where this doc adds value Codex missed
1. **Allocation‑tracking tests** (§9) — per‑frame alloc counts as pass/fail assertions. Required to actually measure the Tier‑1 allocator refactor. Codex doesn't have this layer.
2. **Fuzz tests** (§10) — MLV header, LJ92 stream, marxml parser. Release‑confidence floor.
3. **Cross‑platform hash parity** as a CI gate — Linux vs Windows vs macOS output must match within tolerance. Codex implies this but doesn't name it.
4. **Memory / ASAN / TSAN tooling** (§9) — specific callouts for when to use each.
5. **Concrete `frame_compare.h` API sketch** — saves a design round when scaffolding lands.

### Framework choice — reconciliation
This doc (§2) proposed QTest as the unified harness. Codex (§"Framework choice") recommends a lightweight C++ runner with QTest only for Qt‑facing tests.

**Codex's position is better on reflection.** The core pipeline tests (`llrawproc`, `debayer`, `processing`, cache) have no Qt dependency today. Forcing them through QTest adds compile‑time bloat and binds pure‑C unit tests to Qt's event loop. A hybrid is cleaner:

- **Layers 1–2 (pure unit + microtests):** single‑header C++ test runner — [greatest.h](https://github.com/silentbicycle/greatest) or [utest.h](https://github.com/sheredom/utest.h). Zero dependencies beyond a C++17 compiler.
- **Layer 3 (golden‑frame):** same runner; uses `frame_compare.h` from this doc.
- **Layer 4 (batch/receipt):** same runner; drives the existing `--batch` binary as a subprocess.
- **Layer 5 (perf):** same runner; emits JSON baselines.
- **Layer 6 (GUI smoke, small set):** QTest.

Revised directory layout merging both proposals:

```
tests/
├── tests.pro                        # subdirs template
├── common/
│   ├── test_runner.h                # greatest.h or utest.h vendored single-header
│   ├── frame_compare.h / .c
│   ├── mlv_loader.h / .c
│   └── hash_helpers.h / .c
├── fixtures/                         # same as §3
├── core/                             # Codex's naming; pure-C core tests
│   ├── unit/                         # Layer 1 — pure logic
│   ├── micro/                        # Layer 2 — synthetic buffers
│   └── golden/                       # Layer 3 — tiny MLV fixtures
├── batch/                            # Layer 4 — CLI + ReceiptApplier
├── perf/                             # Layer 5 — benchmarks
├── gui/                              # Layer 6 — QTest only
├── alloc/                            # This doc's §9 — alloc budgets
└── fuzz/                             # This doc's §10 — LibFuzzer harnesses
```

### Agreed execution order (from Codex's summary, endorsed)
1. **Before touching playback logic:** tests for Dual ISO mode selection, cache behavior, receipt fingerprints.
2. **Before refactoring scratch buffers, AVX flags, or display path:** tiny golden‑frame fixtures locked in.
3. **Only then** land `diso_get_preview`, playhead‑aware caching, Dual ISO playback/paused quality splits.

This ordering is now the canonical sequencing. Both agents follow it.

---

## 15. Effort estimate

| Phase | Effort | Outcome |
|---|---|---|
| 0. Bootstrap harness + 3 seed tests | 1 wk | CI runs tests; --batch regression net live |
| 1. Unit coverage for all pipeline stages | 1 wk | Tier 1 refactor safe to land |
| 2. Dual ISO suite + preview path | 1 wk | Dual ISO optimizations safe |
| 3. GPU parity harness | 0.5 wk (concurrent with Tier 2 GPU) | GPU PRs have a clear pass/fail criterion |
| 4. Perf regression baselines | 1 wk | Performance claims verifiable |
| 5. Fuzz corpus + nightly | 0.5 wk | Crash bugs caught before release |

**Total: ~5 weeks, can overlap with the optimization work itself.** The first week (Phase 0) must precede any Tier‑1 optimization merge.
