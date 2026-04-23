# Stage-Timing Breakdown — Dual ISO HQ Playback (2026-04-22, Claude)

## Summary

Measured per-stage timing on `large_dual_iso.mlv` with the HQ full-20-bit
Dual ISO receipt, 16 frames, single thread, no raw cache, on the llvmpipe
VM. **llrawproc (Dual ISO full-20-bit processing) is ~79% of per-frame
cost; the AMaZE debayer itself is only ~2%.** This is a significant
reframing: on Dual ISO clips, the debayer stage is not the bottleneck —
the Dual ISO blending in llrawproc is.

## Methodology

- Binary: `platform/qt/build-playback-compare-current/release/MLVApp.exe`
  (built against current HEAD, mtime 2026-04-22 10:47)
- Env: `MLVAPP_STAGE_TIMING=1`, `MLVAPP_STAGE_TIMING_FILE=<abs path>.stagelog`
- Invocation:
  ```
  --profile-playback --frames 16 --threads 1 --raw-cache-mb 0
  --input tests/fixtures/clips/large_dual_iso.mlv
  --receipt tests/fixtures/receipts/large_dual_iso_hq.marxml
  ```
- Artifacts:
  - `large_dual_iso_hq_16frames.json` — per-frame latency JSON
  - `large_dual_iso_hq_16frames.stagelog` — per-frame per-stage ms
- Headline from the JSON: avg_latency_ms=803.471, avg_cadence_ms=773.861

## Warm-frame stage breakdown (frames 2-15, single thread)

| Stage                 | Avg ms | % of total |
| --------------------- | ------ | ---------- |
| `raw_uint16`          |  40.6  |  6.0%      |
| `llrawproc`           | 538.9  | 79.1%      |
| debayer (AMaZE + float) | 12.3 |  1.8%      |
| `processing` (grading)|  84.8  | 12.4%      |
| **`processed16_total`** | **681.1** | **100%** |

Math check: 40.6 + 538.9 + 12.3 + 84.8 = 676.6 vs measured 681.1 —
delta is small slab/memcpy overhead at the 16-bit cache store.

The `debayered_frame` stage in the log nests raw_uint16 + llrawproc +
float conversion + AMaZE. Debayer-only is `debayered_frame - raw_uint16
- llrawproc`, which averages 12.3 ms across warm frames.

## Cold frames

- Frame 0: processed16_total = 1211 ms (2× the warm avg)
- Frame 1: processed16_total = 1451 ms (cold AMaZE LUT setup, plausibly)

This matches earlier cold-frame behavior documented in the
pre-opt/post-opt before/after comparison in
`C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/analysis/mlv-playback-investigation.md`.

## Per-frame llrawproc variance

llrawproc ranges from 347 ms (frame 6) to 882 ms (frame 9) across warm
frames — a 2.5× variance. Likely causes: frame-to-frame alias map size,
exposure matching convergence, and possibly the chroma smooth kernel
path. Worth investigating whether this variance is content-driven or
there is non-determinism in the algorithm.

## Implications for the ranked optimization list

This data reframes the CPU-only optimization ranked list from
`mlv-playback-investigation.md`:

### Item 1 — Dual ISO preview UI wire-up (Codex just shipped)

**Directly attacks the 539 ms llrawproc cost.** Codex's measurement
(`.claude/profiling/20260422-dualiso-preview-ui/`) shows:
- Full (dual_iso_mode=1): 647 ms avg
- Preview (dual_iso_mode=2): 244 ms avg
- Speedup: **2.66× avg, 2.27× warm**

That's consistent with the preview path cutting llrawproc from ~540 ms
to ~130-150 ms. The original "3-5× estimate" was slightly optimistic
(real: ~2.5× on average). Still the single biggest CPU-only lever on
Dual ISO clips.

### Item 2 — Scrub-mode cheap debayer (REVISED DOWN for Dual ISO)

**AMaZE is only 12 ms of a 681 ms warm Dual ISO frame (1.8%).**
Replacing AMaZE with bilinear/RCD during scrub would save at most ~10
ms per frame on Dual ISO workloads — not meaningful. The 1.5-3× speedup
estimate for item 2 **does not apply to Dual ISO clips**. It still
plausibly applies to non-Dual-ISO clips where AMaZE is the dominant
cost; that needs a separate non-Dual-ISO measurement to confirm.

**Action**: rerun this breakdown against a non-Dual-ISO fixture before
claiming item 2's impact.

### Item 3 — 16-bit processed-frame ring (unchanged)

A ring that avoids the entire pipeline on cache hit still saves ~681 ms
per repeated frame on this clip. Impact is unchanged; still valuable for
repeat-scrub / short-loop playback.

### Item 5 — AVX2 build (shifted toward llrawproc)

Rather than expecting AVX2 gains primarily on debayer (12 ms stage),
the big-leverage target is:
- `llrawproc` at 539 ms — if Dual ISO blending + alias map + exposure
  match are vectorizable, 1.3-1.8× there saves 160-200 ms per frame
- `processing` at 85 ms — grading is highly vectorizable, 1.3-1.8×
  saves 25-35 ms

Debayer being small means AVX2 gains on the AMaZE inner loop would be
almost invisible on Dual ISO clips; the value is elsewhere.

### New item worth considering — llrawproc variance

Worst-case frame llrawproc = 882 ms; best warm-frame llrawproc = 347 ms.
A 2.5× spread. If a future investigation can eliminate the tail
(investigate alias map caching across frames, exposure-match memo, or
spike causes), that closes the variance gap and improves sustained
throughput even without algorithmic changes.

## Correction to earlier agent report

An earlier Explore agent incorrectly claimed "llrawproc is not broken
out separately from debayered_frame" in the stage emission. Verified
locally: the engine emits `llrawproc` as its own stage, and this run
captured it. Source: `StageTiming.h:113-122` +
`src/mlv/video_mlv.c` stage-note calls, plus the raw data in
`large_dual_iso_hq_16frames.stagelog`.

## Confidence

- All file:line anchors: **Verified locally** this session
- Measurement: **Verified locally** — real run on HEAD binary, raw data
  committed to `.claude/profiling/20260422-stage-timing/`
- Ranked-list implications: **Verified locally** for Dual ISO path only.
  Non-Dual-ISO behavior is **Needs runtime profiling** — run this
  harness against a non-Dual-ISO clip before citing item 2's impact.
