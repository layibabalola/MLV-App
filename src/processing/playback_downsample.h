#ifndef _playback_downsample_
#define _playback_downsample_

#include <stdint.h>

/* Phase 4B: fused Bayer-to-RGB downsample-and-debayer.
 *
 * The pipeline still runs the full LJ92 decode + full llrawproc at native
 * resolution (these need the complete bitstream and Bayer pattern). After
 * llrawproc completes, the unpacked uint16 RGGB bayer image is fed into
 * pl_downsample_bayer_to_rgb_<2x|4x>, which produces a half-/quarter-res
 * RGB image directly via per-2x2-block channel averaging. The debayer
 * step is skipped entirely on the scaled path.
 *
 * Strategy: per 2x2 block of the source RGGB pattern, output one RGB pixel
 *   R := bayer[(0,0)]
 *   G := (bayer[(0,1)] + bayer[(1,0)]) / 2
 *   B := bayer[(1,1)]
 *
 * For scale=4, the 4x4 source region collapses to one RGB output pixel,
 * averaging 4 R taps, 8 G taps, and 4 B taps. The 4-row block stride
 * preserves the dual-ISO `iso_patterns[4][4]` cycle exactly — this is the
 * scale at which dual-ISO playback is correct.
 *
 * Output layout: AoS-3 (RGB interleaved per pixel), uint16 per channel.
 * The output bit depth is normalized to 16-bit; if the bayer input is in
 * 14-bit packed form the caller passes bit_shift=2 (or equivalent <<N).
 * Today video_mlv passes 0 because llrawproc has already widened to
 * 16-bit by the time we run.
 *
 * All kernels are AVX2-vectorised on x86_64 hosts; scalar fallback is
 * used otherwise. Dispatch is pthread_once-latched. Kill switches:
 *   MLVAPP_DISABLE_AVX2                — disables every AVX2 kernel
 *   MLVAPP_DISABLE_AVX2_DOWNSAMPLE     — disables only this kernel
 */

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    PL_DOWNSAMPLE_NONE         = 1, /* scaleFactor=1, no-op (caller bypasses) */
    PL_DOWNSAMPLE_2x_BLOCK_AVG = 2, /* per-channel 2x2 block average */
    PL_DOWNSAMPLE_4x_BLOCK_AVG = 4  /* per-channel 4x4 block average (preserves 4-row dual ISO pattern) */
} pl_downsample_strategy_t;

/* RGGB Bayer -> RGB16 via 2x2 block averaging.
 *
 *   bayer_in : full-resolution bayer at (in_w, in_h), one uint16 per pixel,
 *              row-major. RGGB layout assumed (R at (0,0), G at (0,1) and (1,0),
 *              B at (1,1)).
 *   in_w     : input width in pixels. Must be even. (in_w/2) is output width.
 *   in_h     : input height in pixels. Must be even. (in_h/2) is output height.
 *   rgb_out  : output RGB16, AoS-3 interleaved, sized 3 * (in_w/2) * (in_h/2).
 *   bit_shift: left-shift applied to each output sample after averaging,
 *              for callers that want to widen narrow-bitdepth bayer to
 *              16-bit (e.g. 0 for already-16-bit input, 2 for 14-bit, etc.).
 *   threads  : number of OMP threads. <=1 means serial. */
void pl_downsample_bayer_to_rgb_2x(const uint16_t * bayer_in,
                                   int in_w,
                                   int in_h,
                                   uint16_t * rgb_out,
                                   int bit_shift,
                                   int threads);

/* RGGB Bayer -> RGB16 via 4x4 block averaging.
 *
 * Same contract as the 2x kernel, but operates over 4x4 source regions
 * collapsing to one output pixel.
 *   in_w must be a multiple of 4. (in_w/4) is output width.
 *   in_h must be a multiple of 4. (in_h/4) is output height.
 *
 * Per output pixel:
 *   R := mean of {bayer[(0,0)], bayer[(0,2)], bayer[(2,0)], bayer[(2,2)]}
 *   G := mean of the 8 green positions in the 4x4 RGGB tile
 *   B := mean of {bayer[(1,1)], bayer[(1,3)], bayer[(3,1)], bayer[(3,3)]}
 */
void pl_downsample_bayer_to_rgb_4x(const uint16_t * bayer_in,
                                   int in_w,
                                   int in_h,
                                   uint16_t * rgb_out,
                                   int bit_shift,
                                   int threads);

/* Returns 1 if the AVX2 fast path is in use for the downsample kernels,
 * 0 otherwise. Reads kill-switch env vars on first call. */
int plDownsampleAvx2Active(void);

/* Test-only hook: re-evaluate dispatch from current env state. Used by
 * the parity tests that flip MLVAPP_DISABLE_AVX2_DOWNSAMPLE. */
int plDownsampleReinitDispatchForTesting(void);

#ifdef __cplusplus
}
#endif

#endif /* _playback_downsample_ */
