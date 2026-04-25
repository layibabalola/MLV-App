/* Phase 4B: fused Bayer-to-RGB downsample-and-debayer.
 *
 * Overview: at the end of llrawproc the bayer image is full-resolution
 * uint16 RGGB. This module collapses that into a half- or quarter-res
 * RGB16 image via per-channel block averaging. It is the keystone of the
 * adaptive-playback-resolution feature — every downstream stage
 * (debayer, processing, the 8-bit reduce) then runs on a 1/4 or 1/16
 * pixel count, which converts the 5K HQ Dual ISO recon cadence from
 * ~120 ms/frame to a target of ~35 ms/frame.
 *
 * The per-2x2-block channel-averaging strategy is described in
 * .claude-state/profiling/20260425-phase4-scoping/PLAN.md Section 1
 * option (e). The G channel is averaged across the two green positions in
 * the RGGB tile; this is intrinsically anti-aliased and keeps the chroma
 * relationship between R/G/B locally consistent.
 *
 * Dispatch: pthread_once + AVX2 fast path on x86_64. Kill-switches:
 *   MLVAPP_DISABLE_AVX2              (global)
 *   MLVAPP_DISABLE_AVX2_DOWNSAMPLE   (per-kernel)
 *
 * Threading: outer rows are OMP-parallel for both scalar and AVX2
 * variants when threads > 1. Each row is independent, no false sharing.
 */

#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>

#include <omp.h>

#include "playback_downsample.h"

#if defined(__GNUC__) && !defined(__clang__) && (defined(__x86_64__) || defined(__i386__))
#include <immintrin.h>
#define PL_DOWNSAMPLE_AVX2_AVAILABLE 1
#endif

/* ---------------------------------------------------------------------- */
/* Scalar reference kernels — correctness baseline. */

static void pl_downsample_2x_row_scalar(const uint16_t * __restrict bayer_in,
                                        int in_w,
                                        uint16_t * __restrict rgb_out,
                                        int y_out,
                                        int bit_shift)
{
    const int out_w = in_w >> 1;
    const uint16_t * __restrict row0 = bayer_in + ((size_t)(y_out * 2) * (size_t)in_w);
    const uint16_t * __restrict row1 = row0 + in_w;

    /* RGGB tile:
     *   row0: R G R G ...
     *   row1: G B G B ...
     */
    for (int x_out = 0; x_out < out_w; ++x_out)
    {
        const int x_src = x_out * 2;
        const uint32_t r  = row0[x_src];
        const uint32_t g0 = row0[x_src + 1];
        const uint32_t g1 = row1[x_src];
        const uint32_t b  = row1[x_src + 1];
        const uint32_t g  = (g0 + g1) >> 1;

        rgb_out[3 * x_out + 0] = (uint16_t)(r << bit_shift);
        rgb_out[3 * x_out + 1] = (uint16_t)(g << bit_shift);
        rgb_out[3 * x_out + 2] = (uint16_t)(b << bit_shift);
    }
}

static void pl_downsample_4x_row_scalar(const uint16_t * __restrict bayer_in,
                                        int in_w,
                                        uint16_t * __restrict rgb_out,
                                        int y_out,
                                        int bit_shift)
{
    const int out_w = in_w >> 2;
    const uint16_t * __restrict row0 = bayer_in + ((size_t)(y_out * 4) * (size_t)in_w);
    const uint16_t * __restrict row1 = row0 + in_w;
    const uint16_t * __restrict row2 = row1 + in_w;
    const uint16_t * __restrict row3 = row2 + in_w;

    /* 4x4 RGGB tile:
     *   row0: R G R G   (R at cols 0,2; G at cols 1,3)
     *   row1: G B G B   (G at cols 0,2; B at cols 1,3)
     *   row2: R G R G
     *   row3: G B G B
     */
    for (int x_out = 0; x_out < out_w; ++x_out)
    {
        const int x_src = x_out * 4;

        /* 4 R taps: row0[0,2], row2[0,2] */
        const uint32_t r0 = row0[x_src + 0];
        const uint32_t r1 = row0[x_src + 2];
        const uint32_t r2 = row2[x_src + 0];
        const uint32_t r3 = row2[x_src + 2];
        const uint32_t r  = (r0 + r1 + r2 + r3) >> 2;

        /* 4 B taps: row1[1,3], row3[1,3] */
        const uint32_t b0 = row1[x_src + 1];
        const uint32_t b1 = row1[x_src + 3];
        const uint32_t b2 = row3[x_src + 1];
        const uint32_t b3 = row3[x_src + 3];
        const uint32_t b  = (b0 + b1 + b2 + b3) >> 2;

        /* 8 G taps:
         *   row0 cols 1,3 ; row1 cols 0,2 ; row2 cols 1,3 ; row3 cols 0,2 */
        const uint32_t g0 = row0[x_src + 1];
        const uint32_t g1 = row0[x_src + 3];
        const uint32_t g2 = row1[x_src + 0];
        const uint32_t g3 = row1[x_src + 2];
        const uint32_t g4 = row2[x_src + 1];
        const uint32_t g5 = row2[x_src + 3];
        const uint32_t g6 = row3[x_src + 0];
        const uint32_t g7 = row3[x_src + 2];
        const uint32_t g  = (g0 + g1 + g2 + g3 + g4 + g5 + g6 + g7) >> 3;

        rgb_out[3 * x_out + 0] = (uint16_t)(r << bit_shift);
        rgb_out[3 * x_out + 1] = (uint16_t)(g << bit_shift);
        rgb_out[3 * x_out + 2] = (uint16_t)(b << bit_shift);
    }
}

/* ---------------------------------------------------------------------- */
/* AVX2 fast paths. */

#ifdef PL_DOWNSAMPLE_AVX2_AVAILABLE

/* 2x kernel.
 *
 * Approach: process 16 output pixels per SIMD iter (= 32 source columns
 * per row across two source rows). Use _mm256_load_si256 to grab 16
 * consecutive uint16 lanes from each source row, then split into the
 * even-indexed (R / G1) and odd-indexed (G2 / B) lanes. Average the two
 * G lanes per output pixel, then apply the bit_shift left-shift.
 *
 * AoS-3 store: GCC autovectorises stride-3 stores poorly when fed AVX2
 * registers, so we spill the three channel vectors to 32-byte-aligned
 * scratch and emit the interleaved triplets scalarly. The scalar
 * interleave runs hot in L1 because it reads the data we just stored.
 *
 * Edge handling: the tail (out_w not divisible by 16) falls through to
 * scalar.
 */
__attribute__((target("avx2")))
static void pl_downsample_2x_row_avx2(const uint16_t * __restrict bayer_in,
                                      int in_w,
                                      uint16_t * __restrict rgb_out,
                                      int y_out,
                                      int bit_shift)
{
    const int out_w = in_w >> 1;
    const uint16_t * __restrict row0 = bayer_in + ((size_t)(y_out * 2) * (size_t)in_w);
    const uint16_t * __restrict row1 = row0 + in_w;

    /* mask to extract even-indexed words from each lane half */
    const __m256i mask_evens = _mm256_set1_epi32(0x0000FFFF);
    const __m256i shift_count = _mm256_set_epi64x(0, bit_shift, 0, bit_shift);

    /* alignas(32) scratch */
    uint16_t r_buf[16] __attribute__((aligned(32)));
    uint16_t g_buf[16] __attribute__((aligned(32)));
    uint16_t b_buf[16] __attribute__((aligned(32)));

    int x_out = 0;
    /* Each iter consumes 32 source columns (in two rows) and produces
     * 16 output RGB triplets. */
    for (; x_out + 16 <= out_w; x_out += 16)
    {
        const int x_src = x_out * 2;
        /* Load 32 source columns from each row (32 * 2 bytes = 64 bytes
         * = two 256-bit vectors). */
        const __m256i row0_lo = _mm256_loadu_si256((const __m256i *)(row0 + x_src + 0));
        const __m256i row0_hi = _mm256_loadu_si256((const __m256i *)(row0 + x_src + 16));
        const __m256i row1_lo = _mm256_loadu_si256((const __m256i *)(row1 + x_src + 0));
        const __m256i row1_hi = _mm256_loadu_si256((const __m256i *)(row1 + x_src + 16));

        /* Extract even-indexed (= R from row0 / G2 from row1) lanes:
         *   even lanes = src & 0x0000FFFF, then pack 2 vectors of 8 evens
         *   into 1 vector of 16 evens.
         * Use _mm256_packus_epi32 which packs adjacent 32-bit lanes into
         * 16-bit. Need to permute lanes back because packus interleaves
         * the two halves of each 256-bit register. */
        const __m256i row0_evens_lo = _mm256_and_si256(row0_lo, mask_evens);
        const __m256i row0_evens_hi = _mm256_and_si256(row0_hi, mask_evens);
        const __m256i row1_evens_lo = _mm256_and_si256(row1_lo, mask_evens);
        const __m256i row1_evens_hi = _mm256_and_si256(row1_hi, mask_evens);

        /* odd-indexed: shift right by 16 and mask. */
        const __m256i row0_odds_lo = _mm256_srli_epi32(row0_lo, 16);
        const __m256i row0_odds_hi = _mm256_srli_epi32(row0_hi, 16);
        const __m256i row1_odds_lo = _mm256_srli_epi32(row1_lo, 16);
        const __m256i row1_odds_hi = _mm256_srli_epi32(row1_hi, 16);

        /* Pack pairs into 16-lane uint16 vectors. After packus the lane
         * order is: [lo0..lo7, hi0..hi7, lo8..lo15, hi8..hi15] across the
         * 256-bit register because AVX packus operates per 128-bit lane.
         * We want [evens_lo, evens_hi] → straight uint16 vector of the 16
         * R values. Use packus then permute4x64 with imm 0xD8 = 11 01 10 00
         * which moves lane1<->lane2 to fix interleave. */
        const __m256i r_packed = _mm256_permute4x64_epi64(
            _mm256_packus_epi32(row0_evens_lo, row0_evens_hi),
            0xD8);
        const __m256i g1_packed = _mm256_permute4x64_epi64(
            _mm256_packus_epi32(row0_odds_lo, row0_odds_hi),
            0xD8);
        const __m256i g2_packed = _mm256_permute4x64_epi64(
            _mm256_packus_epi32(row1_evens_lo, row1_evens_hi),
            0xD8);
        const __m256i b_packed = _mm256_permute4x64_epi64(
            _mm256_packus_epi32(row1_odds_lo, row1_odds_hi),
            0xD8);

        /* G = (g1 + g2) >> 1 (truncating), matching scalar (g0+g1)>>1.
         * _mm256_avg_epu16 rounds half-up; we use the avg-of-avg trick:
         *   (a+b)>>1 (truncate) == avg(a,b) - ((a^b)&1) */
        const __m256i g_xor = _mm256_xor_si256(g1_packed, g2_packed);
        const __m256i g_avg = _mm256_avg_epu16(g1_packed, g2_packed);
        const __m256i g_lsb = _mm256_and_si256(g_xor, _mm256_set1_epi16(1));
        const __m256i g_packed = _mm256_sub_epi16(g_avg, g_lsb);

        /* Apply bit_shift left-shift. Use _mm256_sll_epi16 with a
         * 64-bit count vector. */
        const __m256i r_shifted = _mm256_sll_epi16(r_packed, _mm256_castsi256_si128(shift_count));
        const __m256i g_shifted = _mm256_sll_epi16(g_packed, _mm256_castsi256_si128(shift_count));
        const __m256i b_shifted = _mm256_sll_epi16(b_packed, _mm256_castsi256_si128(shift_count));

        _mm256_store_si256((__m256i *)r_buf, r_shifted);
        _mm256_store_si256((__m256i *)g_buf, g_shifted);
        _mm256_store_si256((__m256i *)b_buf, b_shifted);

        /* AoS-3 interleave (16 RGB triplets = 48 uint16s). */
        uint16_t * __restrict dst = rgb_out + (size_t)x_out * 3u;
        for (int k = 0; k < 16; ++k)
        {
            dst[3 * k + 0] = r_buf[k];
            dst[3 * k + 1] = g_buf[k];
            dst[3 * k + 2] = b_buf[k];
        }
    }

    /* Tail: run the scalar kernel on the remaining columns. */
    if (x_out < out_w)
    {
        const uint16_t * __restrict r0 = row0 + (x_out * 2);
        const uint16_t * __restrict r1 = row1 + (x_out * 2);
        uint16_t * __restrict dst = rgb_out + (size_t)x_out * 3u;
        for (int xo = x_out; xo < out_w; ++xo)
        {
            const uint32_t r  = r0[0];
            const uint32_t g0 = r0[1];
            const uint32_t g1 = r1[0];
            const uint32_t b  = r1[1];
            const uint32_t g  = (g0 + g1) >> 1;
            dst[0] = (uint16_t)(r << bit_shift);
            dst[1] = (uint16_t)(g << bit_shift);
            dst[2] = (uint16_t)(b << bit_shift);
            r0 += 2;
            r1 += 2;
            dst += 3;
        }
    }
}

/* 4x kernel.
 *
 * Process 8 output pixels per SIMD iter (= 32 source columns spanning
 * 4 source rows). Using 256-bit vectors of 8 uint32 lanes lets us hold
 * the running sum without overflow (each tap is at most 0xFFFF, and we
 * sum at most 8 taps -> 0x7FFF8 fits easily in 32-bit).
 *
 * Strategy: build 8 x uint32 vectors of taps, sum, divide by 4 (R/B) or 8
 * (G), then pack down to uint16 and apply bit_shift.
 */
__attribute__((target("avx2")))
static void pl_downsample_4x_row_avx2(const uint16_t * __restrict bayer_in,
                                      int in_w,
                                      uint16_t * __restrict rgb_out,
                                      int y_out,
                                      int bit_shift)
{
    const int out_w = in_w >> 2;
    const uint16_t * __restrict row0 = bayer_in + ((size_t)(y_out * 4) * (size_t)in_w);
    const uint16_t * __restrict row1 = row0 + in_w;
    const uint16_t * __restrict row2 = row1 + in_w;
    const uint16_t * __restrict row3 = row2 + in_w;

    const __m256i shift_count = _mm256_set_epi64x(0, bit_shift, 0, bit_shift);

    /* alignas(32) scratch */
    uint16_t r_buf[16] __attribute__((aligned(32)));
    uint16_t g_buf[16] __attribute__((aligned(32)));
    uint16_t b_buf[16] __attribute__((aligned(32)));

    int x_out = 0;
    /* Each iter consumes 32 source columns and produces 8 output RGB
     * triplets. */
    for (; x_out + 8 <= out_w; x_out += 8)
    {
        const int x_src = x_out * 4;

        /* Load 16 consecutive uint16 lanes from each source row.
         * That covers source x in [x_src, x_src+16). For 8 output pixels
         * we span source [x_src, x_src+32) — so we need TWO loads per row.
         * Instead, we'll process 8 output pixels but use lanes only over
         * 32 source columns. Let's split into two halves of 4 outputs each
         * for clarity, each processed as a single vector iter. */

        /* For 4 output pixels (16 source columns), one __m256i load of 16
         * uint16 from each row is enough. So unroll: process 4 outputs,
         * then 4 more, both in this SIMD iter. */

        /* Load 16 source cols from each row into uint16x16 vectors. */
        const __m256i s0_lo = _mm256_loadu_si256((const __m256i *)(row0 + x_src + 0));
        const __m256i s1_lo = _mm256_loadu_si256((const __m256i *)(row1 + x_src + 0));
        const __m256i s2_lo = _mm256_loadu_si256((const __m256i *)(row2 + x_src + 0));
        const __m256i s3_lo = _mm256_loadu_si256((const __m256i *)(row3 + x_src + 0));

        const __m256i s0_hi = _mm256_loadu_si256((const __m256i *)(row0 + x_src + 16));
        const __m256i s1_hi = _mm256_loadu_si256((const __m256i *)(row1 + x_src + 16));
        const __m256i s2_hi = _mm256_loadu_si256((const __m256i *)(row2 + x_src + 16));
        const __m256i s3_hi = _mm256_loadu_si256((const __m256i *)(row3 + x_src + 16));

        /* For the *_lo vectors (16 uint16 lanes covering source cols 0..15),
         * the output pixels are k=0..3, with x_src+0..3 covering output 0,
         * x_src+4..7 covering output 1, etc. So output[k] needs taps at
         * source columns (x_src + 4k + {0,1,2,3}).
         *
         * We can fall back to scalar reduction here since the 4x kernel
         * is dominated by the load and the per-output-pixel arithmetic
         * is many fewer ops than the 2x case. The hot work is the loads. */

        /* Spill the 16-col window to scratch and run a tight scalar
         * inner loop. With s*_lo / s*_hi resident in registers and the
         * scalar reads going to the L1, this is fast enough. */
        uint16_t s0_w[32] __attribute__((aligned(32)));
        uint16_t s1_w[32] __attribute__((aligned(32)));
        uint16_t s2_w[32] __attribute__((aligned(32)));
        uint16_t s3_w[32] __attribute__((aligned(32)));
        _mm256_store_si256((__m256i *)(s0_w + 0), s0_lo);
        _mm256_store_si256((__m256i *)(s0_w + 16), s0_hi);
        _mm256_store_si256((__m256i *)(s1_w + 0), s1_lo);
        _mm256_store_si256((__m256i *)(s1_w + 16), s1_hi);
        _mm256_store_si256((__m256i *)(s2_w + 0), s2_lo);
        _mm256_store_si256((__m256i *)(s2_w + 16), s2_hi);
        _mm256_store_si256((__m256i *)(s3_w + 0), s3_lo);
        _mm256_store_si256((__m256i *)(s3_w + 16), s3_hi);

        for (int k = 0; k < 8; ++k)
        {
            const int xs = k * 4;
            const uint32_t r =
                ((uint32_t)s0_w[xs + 0] + (uint32_t)s0_w[xs + 2]
                + (uint32_t)s2_w[xs + 0] + (uint32_t)s2_w[xs + 2]) >> 2;
            const uint32_t b =
                ((uint32_t)s1_w[xs + 1] + (uint32_t)s1_w[xs + 3]
                + (uint32_t)s3_w[xs + 1] + (uint32_t)s3_w[xs + 3]) >> 2;
            const uint32_t g =
                ((uint32_t)s0_w[xs + 1] + (uint32_t)s0_w[xs + 3]
                + (uint32_t)s1_w[xs + 0] + (uint32_t)s1_w[xs + 2]
                + (uint32_t)s2_w[xs + 1] + (uint32_t)s2_w[xs + 3]
                + (uint32_t)s3_w[xs + 0] + (uint32_t)s3_w[xs + 2]) >> 3;

            r_buf[k] = (uint16_t)r;
            g_buf[k] = (uint16_t)g;
            b_buf[k] = (uint16_t)b;
        }

        /* Apply bit_shift left-shift via vector op for SIMD ILP. */
        const __m128i r_v = _mm_load_si128((const __m128i *)r_buf);
        const __m128i g_v = _mm_load_si128((const __m128i *)g_buf);
        const __m128i b_v = _mm_load_si128((const __m128i *)b_buf);
        const __m128i r_s = _mm_sll_epi16(r_v, _mm256_castsi256_si128(shift_count));
        const __m128i g_s = _mm_sll_epi16(g_v, _mm256_castsi256_si128(shift_count));
        const __m128i b_s = _mm_sll_epi16(b_v, _mm256_castsi256_si128(shift_count));
        _mm_store_si128((__m128i *)r_buf, r_s);
        _mm_store_si128((__m128i *)g_buf, g_s);
        _mm_store_si128((__m128i *)b_buf, b_s);

        uint16_t * __restrict dst = rgb_out + (size_t)x_out * 3u;
        for (int k = 0; k < 8; ++k)
        {
            dst[3 * k + 0] = r_buf[k];
            dst[3 * k + 1] = g_buf[k];
            dst[3 * k + 2] = b_buf[k];
        }
    }

    /* Tail. */
    if (x_out < out_w)
    {
        for (int xo = x_out; xo < out_w; ++xo)
        {
            const int x_src = xo * 4;
            const uint32_t r0 = row0[x_src + 0];
            const uint32_t r1 = row0[x_src + 2];
            const uint32_t r2 = row2[x_src + 0];
            const uint32_t r3 = row2[x_src + 2];
            const uint32_t r  = (r0 + r1 + r2 + r3) >> 2;

            const uint32_t b0 = row1[x_src + 1];
            const uint32_t b1 = row1[x_src + 3];
            const uint32_t b2 = row3[x_src + 1];
            const uint32_t b3 = row3[x_src + 3];
            const uint32_t b  = (b0 + b1 + b2 + b3) >> 2;

            const uint32_t g  = ((uint32_t)row0[x_src + 1] + (uint32_t)row0[x_src + 3]
                              + (uint32_t)row1[x_src + 0] + (uint32_t)row1[x_src + 2]
                              + (uint32_t)row2[x_src + 1] + (uint32_t)row2[x_src + 3]
                              + (uint32_t)row3[x_src + 0] + (uint32_t)row3[x_src + 2]) >> 3;

            uint16_t * __restrict dst = rgb_out + (size_t)xo * 3u;
            dst[0] = (uint16_t)(r << bit_shift);
            dst[1] = (uint16_t)(g << bit_shift);
            dst[2] = (uint16_t)(b << bit_shift);
        }
    }
}

#endif /* PL_DOWNSAMPLE_AVX2_AVAILABLE */

/* ---------------------------------------------------------------------- */
/* Dispatch. */

typedef void (*pl_downsample_row_fn_t)(const uint16_t *,
                                       int,
                                       uint16_t *,
                                       int,
                                       int);

static int pl_downsample_env_truthy(const char * v)
{
    if (!v || !*v) return 0;
    if (!strcmp(v, "1") || !strcmp(v, "true") || !strcmp(v, "TRUE")
     || !strcmp(v, "True") || !strcmp(v, "yes") || !strcmp(v, "on")) return 1;
    return 0;
}

static pthread_once_t g_pl_downsample_dispatch_once = PTHREAD_ONCE_INIT;
static int g_pl_downsample_use_avx2 = 0;
static pl_downsample_row_fn_t g_pl_downsample_2x_row_fn = NULL;
static pl_downsample_row_fn_t g_pl_downsample_4x_row_fn = NULL;

static void pl_downsample_dispatch_init(void)
{
    int use_avx2 = 0;
#ifdef PL_DOWNSAMPLE_AVX2_AVAILABLE
    __builtin_cpu_init();
    use_avx2 = __builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma");
#endif
    if (pl_downsample_env_truthy(getenv("MLVAPP_DISABLE_AVX2"))) use_avx2 = 0;
    if (pl_downsample_env_truthy(getenv("MLVAPP_DISABLE_AVX2_DOWNSAMPLE"))) use_avx2 = 0;

    g_pl_downsample_use_avx2 = use_avx2;
#ifdef PL_DOWNSAMPLE_AVX2_AVAILABLE
    g_pl_downsample_2x_row_fn = use_avx2 ? pl_downsample_2x_row_avx2
                                         : pl_downsample_2x_row_scalar;
    g_pl_downsample_4x_row_fn = use_avx2 ? pl_downsample_4x_row_avx2
                                         : pl_downsample_4x_row_scalar;
#else
    g_pl_downsample_2x_row_fn = pl_downsample_2x_row_scalar;
    g_pl_downsample_4x_row_fn = pl_downsample_4x_row_scalar;
#endif
}

int plDownsampleAvx2Active(void)
{
    pthread_once(&g_pl_downsample_dispatch_once, pl_downsample_dispatch_init);
    return g_pl_downsample_use_avx2;
}

int plDownsampleReinitDispatchForTesting(void)
{
    pthread_once(&g_pl_downsample_dispatch_once, pl_downsample_dispatch_init);
    pl_downsample_dispatch_init();
    return g_pl_downsample_use_avx2;
}

/* ---------------------------------------------------------------------- */
/* Public entry points. */

void pl_downsample_bayer_to_rgb_2x(const uint16_t * bayer_in,
                                   int in_w,
                                   int in_h,
                                   uint16_t * rgb_out,
                                   int bit_shift,
                                   int threads)
{
    if (!bayer_in || !rgb_out || in_w < 2 || in_h < 2) return;
    if (in_w & 1) return; /* must be even */
    if (in_h & 1) return;

    pthread_once(&g_pl_downsample_dispatch_once, pl_downsample_dispatch_init);

    const int out_h = in_h >> 1;

    if (threads > 1)
    {
        #pragma omp parallel for num_threads(threads)
        for (int y_out = 0; y_out < out_h; ++y_out)
        {
            uint16_t * __restrict dst_row = rgb_out + ((size_t)y_out * (size_t)(in_w >> 1) * 3u);
            g_pl_downsample_2x_row_fn(bayer_in, in_w, dst_row, y_out, bit_shift);
        }
    }
    else
    {
        for (int y_out = 0; y_out < out_h; ++y_out)
        {
            uint16_t * __restrict dst_row = rgb_out + ((size_t)y_out * (size_t)(in_w >> 1) * 3u);
            g_pl_downsample_2x_row_fn(bayer_in, in_w, dst_row, y_out, bit_shift);
        }
    }
}

void pl_downsample_bayer_to_rgb_4x(const uint16_t * bayer_in,
                                   int in_w,
                                   int in_h,
                                   uint16_t * rgb_out,
                                   int bit_shift,
                                   int threads)
{
    if (!bayer_in || !rgb_out || in_w < 4 || in_h < 4) return;
    if (in_w & 3) return; /* must be multiple of 4 */
    if (in_h & 3) return;

    pthread_once(&g_pl_downsample_dispatch_once, pl_downsample_dispatch_init);

    const int out_h = in_h >> 2;

    if (threads > 1)
    {
        #pragma omp parallel for num_threads(threads)
        for (int y_out = 0; y_out < out_h; ++y_out)
        {
            uint16_t * __restrict dst_row = rgb_out + ((size_t)y_out * (size_t)(in_w >> 2) * 3u);
            g_pl_downsample_4x_row_fn(bayer_in, in_w, dst_row, y_out, bit_shift);
        }
    }
    else
    {
        for (int y_out = 0; y_out < out_h; ++y_out)
        {
            uint16_t * __restrict dst_row = rgb_out + ((size_t)y_out * (size_t)(in_w >> 2) * 3u);
            g_pl_downsample_4x_row_fn(bayer_in, in_w, dst_row, y_out, bit_shift);
        }
    }
}
