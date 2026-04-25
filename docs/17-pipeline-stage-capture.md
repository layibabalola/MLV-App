# Pipeline-stage capture harness

Read-only diagnostic harness for capturing intermediate pixel buffers at each
boundary of the render pipeline. Designed for **paused-vs-playing** diff
analysis: run the same clip and same `(frame, settings)` tuple through both
chains, capture the buffer at each stage, and identify the first stage where
the two diverge.

The harness is **inert by default**: every callsite short-circuits to a single
load-and-branch (no allocation, no I/O, no metadata-string evaluation) when the
controlling environment variable is unset. Enable per-run via env vars; no code
change required.

## Quick start

```powershell
$env:PATH = "C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;" + $env:PATH
$env:MLVAPP_PIPELINE_CAPTURE_DIR    = "C:\temp\diff_paused"
$env:MLVAPP_PIPELINE_CAPTURE_LABEL  = "paused"
$env:MLVAPP_PIPELINE_CAPTURE_FRAMES = "0,127,255"
& "C:\path\to\MLVApp.exe"
# scrub to frame 0, 127, 255 (paused, no playback) — captures land in $MLVAPP_PIPELINE_CAPTURE_DIR

$env:MLVAPP_PIPELINE_CAPTURE_DIR    = "C:\temp\diff_playing"
$env:MLVAPP_PIPELINE_CAPTURE_LABEL  = "playing"
& "C:\path\to\MLVApp.exe"
# play the clip; same frame indices captured during active playback
```

Then diff stage-by-stage between `C:\temp\diff_paused\paused_S*_f*.bin` and
`C:\temp\diff_playing\playing_S*_f*.bin`. The first stage where the two
diverge is where the cast/streaks/quality-gap originates.

## Configuration

All settings via environment variables, read once at first call (lazy init):

| Env var | Default | Purpose |
|---|---|---|
| `MLVAPP_PIPELINE_CAPTURE_DIR` | (unset) | Output directory. **If unset, the harness is fully inert.** Must exist and be writable. |
| `MLVAPP_PIPELINE_CAPTURE_LABEL` | `run` | Filename prefix. Use distinct values across runs (e.g. `paused`, `playing`) to compare in a single output dir. Non-`[A-Za-z0-9_-]` chars are replaced with `_`. |
| `MLVAPP_PIPELINE_CAPTURE_FRAMES` | `0` | Comma-separated frame indices to capture. The literal `all` captures every frame (large output — use sparingly). Whitespace around commas is tolerated. |

## Output layout

For each captured frame at each stage, two files land in `$DIR`:

- `<label>_<stage>_f<frame>.bin` — raw pixel bytes, exactly `bytes_per_line × height` long, no header.
- `<label>_<stage>_f<frame>.json` — sidecar with stage, frame, dims, stride, format, and code-path branch labels.

Sidecar example:

```json
{
  "stage": "S2_post_dualiso",
  "frame_index": 0,
  "label": "playing",
  "width": 1808,
  "height": 2268,
  "bytes_per_line": 3616,
  "bytes_per_pixel": 2,
  "channels": 1,
  "bit_depth": 16,
  "buffer_bytes": 8201088,
  "format": "uint16_mono",
  "format_label": "uint16_bayer_post_dualiso",
  "dual_iso_mode": "preview",
  "debayer_mode": "n/a",
  "playback_policy_active": true,
  "processing_subset_active": false,
  "scaler": "fast",
  "path_label": "applyLLRawProcObject_post_dualiso",
  "settings_hash": "0x0000000000000000"
}
```

The `dual_iso_mode`, `debayer_mode`, `scaler`, `path_label` fields encode the
code-path branches taken at this capture. Compare these between
paused/playing sidecars to confirm the harness saw the chains you expected.

## Stages

| Stage | Buffer | Format | Hook location |
|---|---|---|---|
| `S0_raw_uint16` | uint16 mono/bayer, post LJ92 + bit-unpack, pre llrawproc | `uint16_mono` | `src/mlv/video_mlv.c:getMlvRawFrameUint16Direct` |
| `S1_pre_dualiso` | uint16 mono, post focus/bad pixel/chroma smooth/pattern noise/dark frame/vertical stripes, pre Dual ISO | `uint16_mono` | `src/mlv/llrawproc/llrawproc.c:applyLLRawProcObject` |
| `S2_post_dualiso` | uint16 mono, post Dual ISO recon (full HQ or preview rowscale) | `uint16_mono` | `src/mlv/llrawproc/llrawproc.c:applyLLRawProcObject` |
| `S3_debayer` | uint16 RGB packed, post debayer (AMaZE/Bilinear/AHD/...) | `uint16_rgb` | `src/mlv/frame_caching.c:get_mlv_raw_frame_debayered` |
| `S4_processing` | uint16 RGB packed, post processing core (matrix + levels + gamma + curves + grain) | `uint16_rgb` | `src/processing/raw_processing.c:applyProcessingObject` |
| `S5_processed8` | uint8 RGB packed, post 16-to-8 conversion. Two distinct path labels: `direct8` (fast path) and `processed16_to_8` (indirect). | `uint8_rgb` | `src/mlv/video_mlv.c:getMlvProcessedFrame8` |
| `S6_displayImage` | uint8 RGB at display dims, just before `QPixmap::fromImage`. Stride may be 4-byte-aligned. | `uint8_rgb` | `platform/qt/MainWindow.cpp:presentPlaybackPreparedFrame` |

S3 and S4 only fire when the indirect (processed16 → 8) path runs; the
direct8 fast path bypasses both functions. Use `path_label` in the S5 sidecar
to confirm which path was taken.

## Reading the bin files

Each `.bin` is raw bytes laid out as `height` rows of `bytes_per_line` each.
Effective pixel data per row is `width × bytes_per_pixel`; any trailing bytes
in a row (when `bytes_per_line > width × bytes_per_pixel`) are stride padding
and should be skipped when interpreting pixels.

Example numpy load (uint16 bayer):

```python
import numpy as np, json
with open("paused_S0_raw_uint16_f0.json") as f:
    meta = json.load(f)
buf = np.fromfile("paused_S0_raw_uint16_f0.bin", dtype=np.uint16)
img = buf.reshape(meta["height"], meta["bytes_per_line"] // 2)
img = img[:, :meta["width"]]   # strip stride padding if any
```

For RGB16/RGB8 stages, `channels=3` and pixel data is `[R0, G0, B0, R1, G1, B1, ...]`.

## Diff workflow for paused-vs-playing investigation

1. Pick the same set of frame indices in both runs.
2. Run paused and playing into separate dirs (or same dir with different labels).
3. For each stage `S0..S6`, compare:
   - Bit-exact equality first (`cmp` or numpy `==`).
   - If unequal, diff statistics (mean abs delta per channel, max delta, locations).
4. Identify the **first** stage where paused and playing diverge — that's the cast origin.
5. Cross-reference `dual_iso_mode`, `debayer_mode`, `scaler`, `path_label` in the
   sidecars to confirm which branches actually ran.

A common outcome:
- `S0` and `S1` identical: raw bytes and pre-Dual-ISO transformations match.
- `S2` differs: Dual ISO took different branches (full vs preview).
- `S5` and `S6` differ: the cumulative cast is visible at the user-facing buffer.

If `S2` differs but `S5` does not (visually), the cast is being absorbed by
later processing. If `S2` is identical but `S5` differs, look at debayer
(`S3`) and processing (`S4`) — only fires on the indirect path; for direct8,
the difference must be in the direct8 kernel itself.

## Performance impact

When disabled (env var unset): single load + branch per call site. No
allocation, no syscalls. Verified inert on a `--profile-playback` run with
no env vars set.

When enabled: each capture is `bytes_per_line × height` bytes of disk I/O
plus a small JSON write. Captures of full-frame uint16 buffers at 1920×1080
are ~4 MB each. Limit `MLVAPP_PIPELINE_CAPTURE_FRAMES` to a small list
(default: `0`) unless `=all` is genuinely needed.

The capture function holds a single mutex around each disk write so
concurrent worker threads don't interleave output. Total wall-clock
overhead per captured frame: low milliseconds for the I/O. Not suitable
for performance benchmarking with the harness on; benchmark with it off.

## Limitations

- **S3 / S4 do not fire on the direct8 fast path.** When the rendering pipeline
  takes the direct8 route (`getMlvLastProcessed8DirectPathActive() == 1`), the
  debayer and processing-core hooks are bypassed. The S5 sidecar's
  `path_label` field reports `direct8` in that case. To capture S3/S4 on the
  direct8 path, hooks would need to be added inside
  `src/processing/raw_processing_8bit_kernel.inc`. Out of scope for this
  initial harness.
- **No internal Dual ISO sub-stage capture.** S1/S2 wrap the entire Dual ISO
  call; the regression / histogram / rowscale internals are not separately
  captured. Adding sub-stage hooks inside `src/mlv/llrawproc/dualiso.c` is
  cheap follow-up work if Phase 0 needs the resolution.
- **No GPU-path capture.** When the GPU debayer or GPU preview-processing
  backend runs, the corresponding stage is computed on the GPU and the
  CPU-side buffer is not populated. The CPU-fallback path's hooks still
  fire; the GPU path is uncovered.

## See also

- `src/mlv/pipeline_stage_capture.h` — the public C API and the contract.
- `src/mlv/pipeline_stage_capture.c` — implementation details.
- The 7 hook callsites are tagged in source with `S<N>_<name> capture:` comments.
