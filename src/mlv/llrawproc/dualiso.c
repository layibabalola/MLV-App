/*
 * Copyright (C) 2014 David Milligan
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the
 * Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor,
 * Boston, MA  02110-1301, USA.
 */

#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <limits.h>
#include "hist.h"
#include "dualiso.h"
#include "opt_med.h"
#include "wirth.h"
#include <pthread.h>
#if defined(__GNUC__) && !defined(__clang__) && (defined(__x86_64__) || defined(__i386__))
#include <immintrin.h>
#define DUALISO_AVX2_AVAILABLE 1
#endif
#include "../pipeline_stage_capture.h"
#include "../../debayer/debayer.h"
#include "../../debug/StageTiming.h"

#define EV_RESOLUTION 65536
#ifndef M_PI
#define M_PI 3.14159265358979323846 /* pi */
#endif

#define MIN(a,b) (((a)<(b))?(a):(b))
#define MAX(a,b) (((a)>(b))?(a):(b))
#define COERCE(x,lo,hi) MAX(MIN((x),(hi)),(lo))
#define ABS(a) ((a) > 0 ? (a) : -(a))

#define LOCK(x) static pthread_mutex_t x = PTHREAD_MUTEX_INITIALIZER; pthread_mutex_lock(&x);
#define UNLOCK(x) pthread_mutex_unlock(&(x));

/* Thread-local HQ recon path counters. Declared __thread so OMP-parallel
 * fixtures and concurrent test runners don't cross-contaminate each other.
 * Only the worker thread that drove diso_get_full20bit increments these;
 * tests reset and read them on the same thread that ran the render. */
#if defined(_MSC_VER)
#define DUALISO_THREAD_LOCAL __declspec(thread)
#else
#define DUALISO_THREAD_LOCAL __thread
#endif

static DUALISO_THREAD_LOCAL unsigned long long g_dualiso_hq_amaze_count = 0;
static DUALISO_THREAD_LOCAL unsigned long long g_dualiso_hq_mean23_count = 0;

void dualiso_debug_note_hq_path(int which)
{
    if (which == 0) g_dualiso_hq_amaze_count++;
    else            g_dualiso_hq_mean23_count++;
}

void dualiso_debug_reset_hq_path_counters(void)
{
    g_dualiso_hq_amaze_count = 0;
    g_dualiso_hq_mean23_count = 0;
}

unsigned long long dualiso_debug_hq_amaze_count(void)
{
    return g_dualiso_hq_amaze_count;
}

unsigned long long dualiso_debug_hq_mean23_count(void)
{
    return g_dualiso_hq_mean23_count;
}

/* ------------------------------------------------------------------------
 * Dual ISO preview rowscale: hot inner loop. Two implementations selected
 * once via pthread_once based on host CPU support and the
 * MLVAPP_DISABLE_AVX2 env-var kill switch. Mirrors the dispatch pattern at
 * src/processing/raw_processing.c:980-1066.
 *
 * The y-2 cross-row dependency (output[i - 2*width] is read after row y-2
 * has been written) means the OUTER y loop is sequential. Column-SIMD
 * within each row is safe.
 * ------------------------------------------------------------------------ */

typedef void (*dualiso_rowscale_fn_t)(int width, int height, int dark_row_start,
                                      const uint16_t * source_image,
                                      uint16_t * output_image,
                                      double a, double b,
                                      int32_t black, int32_t white,
                                      uint16_t shadow);

static void dualiso_rowscale_scalar(int width, int height, int dark_row_start,
                                    const uint16_t * source_image,
                                    uint16_t * output_image,
                                    double a, double b,
                                    int32_t black, int32_t white,
                                    uint16_t shadow)
{
    for(int y = 0; y < height; y++)
    {
        int row_start = y * width;
        if (((y - dark_row_start + 4) % 4) >= 2)
        {
            //bright row
            for(int i = row_start; i < row_start + width; i++)
            {
                if(source_image[i] >= white)
                {
                    output_image[i] = y > 2
                        ? (y < height - 2
                            ? (output_image[i-width*2] + source_image[i+width*2]) / 2
                            : output_image[i-width*2])
                        : source_image[i+width*2];
                }
                else
                {
                    output_image[i] = (uint16_t)(MIN(white,(source_image[i] - black) * a + black + b));
                }
            }
        }
        else
        {
            //dark row
            for(int i = row_start; i < row_start + width; i++)
            {
                if(source_image[i] < shadow)
                {
                    output_image[i] = (uint16_t)(y > 2
                        ? (y < height - 2
                            ? (output_image[i-width*2] + MIN(white,(source_image[i+width*2]  - black) * a + black + b)) / 2
                            : output_image[i-width*2])
                        : MIN(white,(source_image[i+width*2]  - black) * a + black + b));
                }
            }
        }
    }
}

#ifdef DUALISO_AVX2_AVAILABLE
__attribute__((target("avx2,fma")))
static void dualiso_rowscale_avx2(int width, int height, int dark_row_start,
                                  const uint16_t * source_image,
                                  uint16_t * output_image,
                                  double a, double b,
                                  int32_t black, int32_t white,
                                  uint16_t shadow)
{
    /* Hot kernel: 16 uint16/iter via two 8-float halves through FMA.
     * Edge rows use scalar fallbacks. The "edge" range is intentionally
     * wider than (y < 2 || y >= height - 2): the scalar reference treats
     * y == 2 (and y == height - 3) as a *boundary* case for saturated /
     * shadow lanes, where it skips the cross-row averaging that the
     * steady-state body performs. The SIMD body's saturated/shadow patch
     * unconditionally averages output[idx - 2w] with source[idx + 2w],
     * which is the y > 2 formula and disagrees with scalar at exactly the
     * y == 2 boundary. Treating y == 2 as an edge row pushes that boundary
     * back into the scalar fallback and yields byte-identity vs scalar.
     * Cost: 1 additional scalar row out of ~2200 (negligible). The
     * RowscaleAvx2ByteIdentityVsScalar parity test guards against a
     * regression on this boundary. */
    /* Constants in pd (double) so the FMA chain matches scalar bit-for-bit
     * on the values that previously drifted by 1 ULP in float32. The float
     * lo/hi vectors are kept for the integer<->float round trip and the
     * non-FMA tests; only the multiply/add chain runs in pd. */
    const __m256d ad     = _mm256_set1_pd(a);
    const __m256d blackd = _mm256_set1_pd((double)black);
    const __m256d zeroed = _mm256_setzero_pd();
    const __m256d whited = _mm256_set1_pd((double)white);
    /* Match scalar associativity exactly: `(((src - black) * a) + black) + b`,
     * NOT a 3-arg fmadd. Two separate add steps preserve scalar rounding. */
    const __m256d bd     = _mm256_set1_pd(b);
    const __m256i white_u16  = _mm256_set1_epi16((int16_t)white);
    const __m256i bias_xor   = _mm256_set1_epi16((int16_t)0x8000);
    const __m256i shadow_biased = _mm256_set1_epi16((int16_t)((shadow ^ 0x8000) & 0xFFFF));
    const __m256i zero_i = _mm256_setzero_si256();

    for(int y = 0; y < height; y++)
    {
        const int row_start = y * width;
        const int row_end   = row_start + width;
        const int is_bright = (((y - dark_row_start + 4) % 4) >= 2);
        /* Treat y in [0, 2] and [height-3, height-1] as edge so the SIMD
         * body only runs on rows where the y > 2 / y < height - 2 fast
         * paths in the scalar reference both apply. */
        const int edge_row  = (y < 3) || (y >= height - 3) || (width < 16);

        /* Edge rows or narrow widths: scalar full-row. Cheap because rare
         * (only 4 rows out of ~2200 typically). */
        if (edge_row)
        {
            if (is_bright)
            {
                for(int i = row_start; i < row_end; i++)
                {
                    if(source_image[i] >= white)
                    {
                        output_image[i] = y > 2
                            ? (y < height - 2
                                ? (output_image[i-width*2] + source_image[i+width*2]) / 2
                                : output_image[i-width*2])
                            : source_image[i+width*2];
                    }
                    else
                    {
                        output_image[i] = (uint16_t)(MIN(white,(source_image[i] - black) * a + black + b));
                    }
                }
            }
            else
            {
                for(int i = row_start; i < row_end; i++)
                {
                    if(source_image[i] < shadow)
                    {
                        output_image[i] = (uint16_t)(y > 2
                            ? (y < height - 2
                                ? (output_image[i-width*2] + MIN(white,(source_image[i+width*2]  - black) * a + black + b)) / 2
                                : output_image[i-width*2])
                            : MIN(white,(source_image[i+width*2]  - black) * a + black + b));
                    }
                }
            }
            continue;
        }

        const int simd_end = row_start + (width / 16) * 16;
        int i = row_start;

        if (is_bright)
        {
            for( ; i < simd_end; i += 16)
            {
                __m256i src_v = _mm256_loadu_si256((const __m256i*)&source_image[i]);

                /* Saturated mask: src >= white. max(src,white)==src iff src>=white. */
                __m256i sat_mask = _mm256_cmpeq_epi16(_mm256_max_epu16(src_v, white_u16), src_v);

                /* Convert to four pd-quartets (4 doubles per reg, 16 lanes
                 * total). The float intermediate path was 1 ULP off scalar
                 * for borderline values; full-double matches scalar
                 * byte-for-byte. */
                __m256i lo_i32 = _mm256_unpacklo_epi16(src_v, zero_i);
                __m256i hi_i32 = _mm256_unpackhi_epi16(src_v, zero_i);
                __m256d q0 = _mm256_cvtepi32_pd(_mm256_castsi256_si128(lo_i32));
                __m256d q1 = _mm256_cvtepi32_pd(_mm256_extracti128_si256(lo_i32, 1));
                __m256d q2 = _mm256_cvtepi32_pd(_mm256_castsi256_si128(hi_i32));
                __m256d q3 = _mm256_cvtepi32_pd(_mm256_extracti128_si256(hi_i32, 1));

                /* Match scalar: ((src - black) * a + black) + b. The two
                 * trailing adds are *not* fused so the rounding boundary
                 * matches the scalar reference's three rounding steps. */
                q0 = _mm256_add_pd(_mm256_add_pd(_mm256_mul_pd(_mm256_sub_pd(q0, blackd), ad), blackd), bd);
                q1 = _mm256_add_pd(_mm256_add_pd(_mm256_mul_pd(_mm256_sub_pd(q1, blackd), ad), blackd), bd);
                q2 = _mm256_add_pd(_mm256_add_pd(_mm256_mul_pd(_mm256_sub_pd(q2, blackd), ad), blackd), bd);
                q3 = _mm256_add_pd(_mm256_add_pd(_mm256_mul_pd(_mm256_sub_pd(q3, blackd), ad), blackd), bd);

                /* Clamp to [0, white]. The scalar uses MIN(white, .) but no
                 * lower bound — that's UB on negative values cast to uint16.
                 * The AVX2 path explicitly clamps to [0, white] which is the
                 * documented behaviour we want. */
                q0 = _mm256_min_pd(_mm256_max_pd(q0, zeroed), whited);
                q1 = _mm256_min_pd(_mm256_max_pd(q1, zeroed), whited);
                q2 = _mm256_min_pd(_mm256_max_pd(q2, zeroed), whited);
                q3 = _mm256_min_pd(_mm256_max_pd(q3, zeroed), whited);

                /* Truncate-to-int matches scalar's `(uint16_t)double_value`
                 * cast (truncation toward zero). */
                __m128i p0 = _mm256_cvttpd_epi32(q0);
                __m128i p1 = _mm256_cvttpd_epi32(q1);
                __m128i p2 = _mm256_cvttpd_epi32(q2);
                __m128i p3 = _mm256_cvttpd_epi32(q3);
                __m256i lo_p = _mm256_inserti128_si256(_mm256_castsi128_si256(p0), p1, 1);
                __m256i hi_p = _mm256_inserti128_si256(_mm256_castsi128_si256(p2), p3, 1);

                /* repack to uint16. With lo_p coming from unpacklo (src lanes
                 * [0..3, 8..11]) and hi_p from unpackhi (src lanes [4..7,
                 * 12..15]), _mm256_packus_epi32 lays out:
                 *   result[0..3]   = lo_p low 128  (= src[0..3])
                 *   result[4..7]   = hi_p low 128  (= src[4..7])
                 *   result[8..11]  = lo_p high 128 (= src[8..11])
                 *   result[12..15] = hi_p high 128 (= src[12..15])
                 * which is exactly src[0..15] in order, so NO permute is
                 * needed. A previous version of this kernel applied
                 * _mm256_permute4x64_epi64(scaled, 0xD8) here, which
                 * scrambled the already-correct layout and silently
                 * miscoloured the rowscaled bright/dark rows. The byte-
                 * identity test RowscaleAvx2ByteIdentityVsScalar guards
                 * against regression. See debayer.c:167-176 for the same
                 * pattern. */
                __m256i scaled = _mm256_packus_epi32(lo_p, hi_p);

                _mm256_storeu_si256((__m256i*)&output_image[i], scaled);

                /* Patch saturated lanes scalarly (rare path, <5% typical).
                 * The y == 2 boundary is in the edge_row fallback so the
                 * `y > 2` formula here is unconditionally correct for the
                 * SIMD-eligible rows. */
                int sat_mm = _mm256_movemask_epi8(sat_mask);
                if (sat_mm)
                {
                    for(int j = 0; j < 16; j++)
                    {
                        if ((sat_mm >> (j * 2)) & 0x3)
                        {
                            int idx = i + j;
                            output_image[idx] = (output_image[idx-width*2] + source_image[idx+width*2]) / 2;
                        }
                    }
                }
            }
            /* tail */
            for( ; i < row_end; i++)
            {
                if(source_image[i] >= white)
                {
                    output_image[i] = (output_image[i-width*2] + source_image[i+width*2]) / 2;
                }
                else
                {
                    output_image[i] = (uint16_t)(MIN(white,(source_image[i] - black) * a + black + b));
                }
            }
        }
        else /* dark row */
        {
            for( ; i < simd_end; i += 16)
            {
                __m256i src_v = _mm256_loadu_si256((const __m256i*)&source_image[i]);

                /* Shadow mask: src < shadow. _mm256_cmpgt_epi16 is signed; bias both ops by 0x8000. */
                __m256i src_biased = _mm256_xor_si256(src_v, bias_xor);
                __m256i shadow_mask = _mm256_cmpgt_epi16(shadow_biased, src_biased);

                int sm = _mm256_movemask_epi8(shadow_mask);
                if (sm)
                {
                    /* Scalar update only on shadow lanes. Most lanes leave
                     * output untouched (already initialised by the memcpy
                     * before this loop runs). */
                    for(int j = 0; j < 16; j++)
                    {
                        if ((sm >> (j * 2)) & 0x3)
                        {
                            int idx = i + j;
                            int scaled = (int)((source_image[idx+width*2] - black) * a + black + b);
                            if (scaled < 0)     scaled = 0;
                            if (scaled > white) scaled = white;
                            output_image[idx] = (uint16_t)((output_image[idx-width*2] + scaled) / 2);
                        }
                    }
                }
            }
            /* tail */
            for( ; i < row_end; i++)
            {
                if(source_image[i] < shadow)
                {
                    int scaled = (int)((source_image[i+width*2] - black) * a + black + b);
                    if (scaled < 0)     scaled = 0;
                    if (scaled > white) scaled = white;
                    output_image[i] = (uint16_t)((output_image[i-width*2] + scaled) / 2);
                }
            }
        }
    }
}
#endif /* DUALISO_AVX2_AVAILABLE */

static int dualiso_env_truthy(const char * v)
{
    if (!v || !*v) return 0;
    if (!strcmp(v, "1") || !strcmp(v, "true") || !strcmp(v, "TRUE")
     || !strcmp(v, "True") || !strcmp(v, "yes") || !strcmp(v, "on")) return 1;
    return 0;
}

static pthread_once_t g_dualiso_rowscale_dispatch_once = PTHREAD_ONCE_INIT;
static dualiso_rowscale_fn_t g_dualiso_rowscale_fn = NULL;
static int g_dualiso_rowscale_use_avx2 = 0;

static void dualiso_rowscale_dispatch_init(void)
{
    int use_avx2 = 0;
#ifdef DUALISO_AVX2_AVAILABLE
    __builtin_cpu_init();
    use_avx2 = __builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma");
#endif
    if (dualiso_env_truthy(getenv("MLVAPP_DISABLE_AVX2"))) use_avx2 = 0;
    if (dualiso_env_truthy(getenv("MLVAPP_DISABLE_AVX2_DUALISO"))) use_avx2 = 0;
    g_dualiso_rowscale_use_avx2 = use_avx2;
#ifdef DUALISO_AVX2_AVAILABLE
    g_dualiso_rowscale_fn = use_avx2 ? dualiso_rowscale_avx2 : dualiso_rowscale_scalar;
#else
    g_dualiso_rowscale_fn = dualiso_rowscale_scalar;
#endif
}

static void dualiso_rowscale(int width, int height, int dark_row_start,
                             const uint16_t * source_image,
                             uint16_t * output_image,
                             double a, double b,
                             int32_t black, int32_t white,
                             uint16_t shadow)
{
    pthread_once(&g_dualiso_rowscale_dispatch_once, dualiso_rowscale_dispatch_init);
    g_dualiso_rowscale_fn(width, height, dark_row_start,
                          source_image, output_image,
                          a, b, black, white, shadow);
}

int dualisoRowscaleAvx2Active(void)
{
    pthread_once(&g_dualiso_rowscale_dispatch_once, dualiso_rowscale_dispatch_init);
    return g_dualiso_rowscale_use_avx2;
}

/* Test-only hook: force re-evaluation of the dispatch from current env.
 * Mirrors dualisoHqReinitDispatchForTesting / debayerBasicU16ReinitDispatchForTesting.
 * Not in the public header; tests forward-declare it. */
int dualisoRowscaleReinitDispatchForTesting(void);
int dualisoRowscaleReinitDispatchForTesting(void)
{
    pthread_once(&g_dualiso_rowscale_dispatch_once, dualiso_rowscale_dispatch_init);
    dualiso_rowscale_dispatch_init();
    return g_dualiso_rowscale_use_avx2;
}

/* ====================================================================== *
 * AVX2 + FMA HQ Dual ISO recon kernels (Path B Phase B1 + B2).
 *
 * Five kernels are dispatched at runtime via pthread_once. Default ON when
 * the host advertises AVX2 + FMA. Set MLVAPP_DISABLE_AVX2_DUALISO_HQ=1
 * (or the global MLVAPP_DISABLE_AVX2=1) to force the scalar reference
 * paths for parity / regression debugging.
 *
 * The B0 prototype (.claude-state/profiling/20260424-phase-b0-final-blend/)
 * measured ~2.0× speedup p50 on the final_blend kernel alone, taking it
 * from ~20 ms to ~10 ms single-threaded on a 1808x2268 frame. With
 * Phase B1 wins on convert_to_20bit + fullres_reconstruction +
 * convert_20_to_16bit + Phase B2 mix_images, the full HQ recon should
 * land in the 10-14 ms range — well below the 16.7 ms / 60 fps budget.
 *
 * Drift bound: float32 FMA reordering may differ from scalar double-precision
 * by ±1 LSB on a small fraction (~0.2%) of pixels, far below the existing
 * raw_set_pixel_20to16_rand dither which already injects ~4 LSB random
 * noise per pixel. Pipeline tests assert ±1 LSB drift on a bounded fraction.
 * ====================================================================== */
#include "dualiso_avx2.inc"

static pthread_once_t g_dualiso_hq_dispatch_once = PTHREAD_ONCE_INIT;
static int g_dualiso_hq_use_avx2 = 0;

static void dualiso_hq_dispatch_init(void)
{
    int use_avx2 = 0;
#ifdef DUALISO_AVX2_AVAILABLE
    __builtin_cpu_init();
    use_avx2 = __builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma");
#endif
    if (dualiso_env_truthy(getenv("MLVAPP_DISABLE_AVX2"))) use_avx2 = 0;
    if (dualiso_env_truthy(getenv("MLVAPP_DISABLE_AVX2_DUALISO_HQ"))) use_avx2 = 0;
    g_dualiso_hq_use_avx2 = use_avx2;
}

int dualisoHqAvx2Active(void);
int dualisoHqAvx2Active(void)
{
    pthread_once(&g_dualiso_hq_dispatch_once, dualiso_hq_dispatch_init);
    return g_dualiso_hq_use_avx2;
}

/* Test-only hook: force re-evaluation of the dispatch from current env.
 * Mirrors debayerBasicU16ReinitDispatchForTesting. Not in the public
 * header; tests forward-declare it. */
int dualisoHqReinitDispatchForTesting(void);
int dualisoHqReinitDispatchForTesting(void)
{
    pthread_once(&g_dualiso_hq_dispatch_once, dualiso_hq_dispatch_init);
    dualiso_hq_dispatch_init();
    return g_dualiso_hq_use_avx2;
}

/* ====================================================================== *
 * Phase C4 — alias-map init err diff + 21-tap weighted Gaussian dispatch.
 *
 * Independent dispatch from g_dualiso_hq_use_avx2 so the parity test can
 * isolate the alias-map kernels. Kill switch:
 *   MLVAPP_DISABLE_AVX2_DUALISO_ALIAS_MAP=1   (also gated by the global
 *   MLVAPP_DISABLE_AVX2=1).
 *
 * Both new kernels are byte-identical to scalar:
 *   - init err diff: pure int32 arith (ABS/MIN/MAX/shifts), predicate masked
 *     to zero on writes; matches scalar `continue` against a pre-zeroed
 *     alias_map buffer.
 *   - 21-tap Gaussian: int32 multiply-and-shift mirrors `* W / 1024` per
 *     term-by-term, matching scalar truncation order; predicate preserves
 *     prior alias_map[x] via _mm256_blendv_epi8.
 * ====================================================================== */
static pthread_once_t g_dualiso_alias_dispatch_once = PTHREAD_ONCE_INIT;
static int g_dualiso_alias_use_avx2 = 0;

static void dualiso_alias_dispatch_init(void)
{
    int use_avx2 = 0;
#ifdef DUALISO_AVX2_AVAILABLE
    __builtin_cpu_init();
    use_avx2 = __builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma");
#endif
    if (dualiso_env_truthy(getenv("MLVAPP_DISABLE_AVX2"))) use_avx2 = 0;
    if (dualiso_env_truthy(getenv("MLVAPP_DISABLE_AVX2_DUALISO_ALIAS_MAP"))) use_avx2 = 0;
    g_dualiso_alias_use_avx2 = use_avx2;
}

int dualisoAliasMapAvx2Active(void);
int dualisoAliasMapAvx2Active(void)
{
    pthread_once(&g_dualiso_alias_dispatch_once, dualiso_alias_dispatch_init);
    return g_dualiso_alias_use_avx2;
}

/* Test-only hook: force re-evaluation of the dispatch from current env.
 * Mirrors dualisoHqReinitDispatchForTesting. Not in the public header;
 * tests forward-declare it. */
int dualisoAliasMapReinitDispatchForTesting(void);
int dualisoAliasMapReinitDispatchForTesting(void)
{
    pthread_once(&g_dualiso_alias_dispatch_once, dualiso_alias_dispatch_init);
    dualiso_alias_dispatch_init();
    return g_dualiso_alias_use_avx2;
}

/* ====================================================================== *
 * Phase E1 — AMaZE edge-direction estimator AVX2 dispatch.
 *
 * Independent dispatch from g_dualiso_hq_use_avx2 / g_dualiso_alias_use_avx2
 * so the parity test can isolate the E1 contribution. Kill switch:
 *   MLVAPP_DISABLE_AVX2_DUALISO_AMAZE=1   (also gated by the global
 *   MLVAPP_DISABLE_AVX2=1).
 *
 * The kernel is byte-identical to scalar: pure int32 arith
 * (ABS / sub / add / cmp) on raw2ev gather results. No FMA, no float
 * reordering, no division.
 *
 * The kernel itself lives in dualiso_amaze_avx2.inc (included earlier
 * in this TU; the include ordering keeps the helpers next to their
 * dispatch siblings). It depends on the file-scope `edge_directions[]`
 * table which is defined later in this TU at line ~2263 — to keep the
 * include order valid we wire the dispatch flags here but include the
 * .inc *after* edge_directions[] is declared (forward declaration of
 * the kernel below resolves the ordering).
 * ====================================================================== */
static pthread_once_t g_dualiso_amaze_dispatch_once = PTHREAD_ONCE_INIT;
static int g_dualiso_amaze_use_avx2 = 0;

static void dualiso_amaze_dispatch_init(void)
{
    int use_avx2 = 0;
#ifdef DUALISO_AVX2_AVAILABLE
    __builtin_cpu_init();
    use_avx2 = __builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma");
#endif
    if (dualiso_env_truthy(getenv("MLVAPP_DISABLE_AVX2"))) use_avx2 = 0;
    if (dualiso_env_truthy(getenv("MLVAPP_DISABLE_AVX2_DUALISO_AMAZE"))) use_avx2 = 0;
    g_dualiso_amaze_use_avx2 = use_avx2;
}

int dualisoAmazeAvx2Active(void);
int dualisoAmazeAvx2Active(void)
{
    pthread_once(&g_dualiso_amaze_dispatch_once, dualiso_amaze_dispatch_init);
    return g_dualiso_amaze_use_avx2;
}

/* Test-only hook: force re-evaluation of the dispatch from current env.
 * Mirrors dualisoAliasMapReinitDispatchForTesting. Not in the public
 * header; tests forward-declare it. */
int dualisoAmazeReinitDispatchForTesting(void);
int dualisoAmazeReinitDispatchForTesting(void)
{
    pthread_once(&g_dualiso_amaze_dispatch_once, dualiso_amaze_dispatch_init);
    dualiso_amaze_dispatch_init();
    return g_dualiso_amaze_use_avx2;
}

static int preview_pattern_index(int iso_pattern)
{
    const int pattern = ABS(iso_pattern);
    return (pattern >= 1 && pattern <= 4) ? pattern : 0;
}

static int set_preview_histograms_from_pattern(int iso_pattern,
                                               struct histogram ** hist,
                                               uint16_t * dark_row_start,
                                               struct histogram ** hist_lo,
                                               struct histogram ** hist_hi)
{
    switch(preview_pattern_index(iso_pattern))
    {
    case 1:
        *dark_row_start = 2;
        *hist_lo = hist[2];
        *hist_hi = hist[0];
        return 1;
    case 2:
        *dark_row_start = 1;
        *hist_lo = hist[1];
        *hist_hi = hist[0];
        return 1;
    case 3:
        *dark_row_start = 0;
        *hist_lo = hist[0];
        *hist_hi = hist[2];
        return 1;
    case 4:
        *dark_row_start = 3;
        *hist_lo = hist[0];
        *hist_hi = hist[2];
        return 1;
    default:
        return 0;
    }
}

static int preview_pattern_from_dark_row_start(uint16_t dark_row_start)
{
    switch(dark_row_start)
    {
    case 0: return -3;
    case 1: return -2;
    case 2: return -1;
    case 3: return -4;
    default: return 0;
    }
}

static int ensure_preview_scratch_capacity(dualiso_preview_scratch_t * scratch, size_t data_size)
{
    if (!scratch)
    {
        return 0;
    }

    if (scratch->data_capacity >= data_size &&
        scratch->data_x &&
        scratch->data_y &&
        scratch->data_w)
    {
        return 1;
    }

    int * next_data_x = (int *)malloc(data_size * sizeof(scratch->data_x[0]));
    int * next_data_y = (int *)malloc(data_size * sizeof(scratch->data_y[0]));
    double * next_data_w = (double *)malloc(data_size * sizeof(scratch->data_w[0]));

    if (!next_data_x || !next_data_y || !next_data_w)
    {
        free(next_data_x);
        free(next_data_y);
        free(next_data_w);
        return 0;
    }

    free(scratch->data_x);
    free(scratch->data_y);
    free(scratch->data_w);
    scratch->data_x = next_data_x;
    scratch->data_y = next_data_y;
    scratch->data_w = next_data_w;
    scratch->data_capacity = data_size;
    return 1;
}

static int ensure_reusable_scratch_buffer(void ** buffer,
                                          size_t * capacity,
                                          size_t required_count,
                                          size_t element_size)
{
    if (!buffer || !capacity)
    {
        return 0;
    }

    if (*buffer && *capacity >= required_count)
    {
        return 1;
    }

    if (required_count == 0)
    {
        return 1;
    }

    if (element_size != 0 && required_count > (SIZE_MAX / element_size))
    {
        return 0;
    }

    void * next = malloc(required_count * element_size);
    if (!next)
    {
        return 0;
    }

    free(*buffer);
    *buffer = next;
    *capacity = required_count;
    return 1;
}

static double * ensure_double_scratch_buffer(double ** buffer, size_t * capacity, size_t required_count)
{
    return ensure_reusable_scratch_buffer((void **)buffer, capacity, required_count, sizeof(double))
        ? *buffer
        : NULL;
}

static uint16_t * ensure_preview_output_buffer(dualiso_preview_scratch_t * scratch, size_t required_count)
{
    return ensure_reusable_scratch_buffer((void **)&scratch->output_image,
                                          &scratch->output_capacity,
                                          required_count,
                                          sizeof(uint16_t))
        ? scratch->output_image
        : NULL;
}

static int * ensure_identify_histograms_scratch(dualiso_full20bit_scratch_t * scratch)
{
    const size_t histogram_count = 4;
    const size_t histogram_bins = 16384;
    const size_t required_count = histogram_count * histogram_bins;

    if (!scratch)
    {
        return NULL;
    }

    return ensure_reusable_scratch_buffer((void **)&scratch->identify_histograms,
                                          &scratch->identify_histogram_capacity,
                                          required_count,
                                          sizeof(int))
        ? scratch->identify_histograms
        : NULL;
}

static int ensure_histogram_match_scratch(dualiso_full20bit_scratch_t * scratch,
                                          size_t pixel_count,
                                          size_t sample_count,
                                          size_t highlight_count)
{
    if (!scratch)
    {
        return 0;
    }

    if (scratch->histogram_match_dark
        && scratch->histogram_match_bright
        && scratch->histogram_match_tmp
        && scratch->histogram_match_hi_dark
        && scratch->histogram_match_hi_bright
        && scratch->histogram_match_pixel_capacity >= pixel_count
        && scratch->histogram_match_sample_capacity >= sample_count
        && scratch->histogram_match_highlight_capacity >= highlight_count)
    {
        return 1;
    }

    int * next_dark = (int *)malloc(pixel_count * sizeof(int));
    int * next_bright = (int *)malloc(pixel_count * sizeof(int));
    int * next_tmp = (int *)malloc(sample_count * sizeof(int));
    int * next_hi_dark = (int *)malloc(highlight_count * sizeof(int));
    int * next_hi_bright = (int *)malloc(highlight_count * sizeof(int));

    if (!next_dark || !next_bright || !next_tmp || !next_hi_dark || !next_hi_bright)
    {
        free(next_dark);
        free(next_bright);
        free(next_tmp);
        free(next_hi_dark);
        free(next_hi_bright);
        return 0;
    }

    free(scratch->histogram_match_dark);
    free(scratch->histogram_match_bright);
    free(scratch->histogram_match_tmp);
    free(scratch->histogram_match_hi_dark);
    free(scratch->histogram_match_hi_bright);

    scratch->histogram_match_dark = next_dark;
    scratch->histogram_match_bright = next_bright;
    scratch->histogram_match_tmp = next_tmp;
    scratch->histogram_match_hi_dark = next_hi_dark;
    scratch->histogram_match_hi_bright = next_hi_bright;
    scratch->histogram_match_pixel_capacity = pixel_count;
    scratch->histogram_match_sample_capacity = sample_count;
    scratch->histogram_match_highlight_capacity = highlight_count;
    return 1;
}

static void assign_amaze_plane_rows(float ** rows, float * storage, size_t row_count, size_t row_width)
{
    for (size_t i = 0; i < row_count; ++i)
    {
        rows[i] = storage + i * row_width;
    }
}

static int ensure_amaze_interpolation_scratch(dualiso_full20bit_scratch_t * scratch,
                                              size_t row_count,
                                              size_t row_width,
                                              size_t pixel_count,
                                              size_t thread_count)
{
    if (!scratch)
    {
        return 0;
    }

    if (thread_count == 0)
    {
        thread_count = 1;
    }

    const size_t plane_cell_count = row_count * row_width;

    if (!ensure_reusable_scratch_buffer((void **)&scratch->amaze_squeezed,
                                        &scratch->amaze_row_capacity,
                                        row_count,
                                        sizeof(int))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_rawData_rows,
                                           &scratch->amaze_row_capacity,
                                           row_count,
                                           sizeof(float *))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_red_rows,
                                           &scratch->amaze_row_capacity,
                                           row_count,
                                           sizeof(float *))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_green_rows,
                                           &scratch->amaze_row_capacity,
                                           row_count,
                                           sizeof(float *))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_blue_rows,
                                           &scratch->amaze_row_capacity,
                                           row_count,
                                           sizeof(float *))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_rawData_storage,
                                           &scratch->amaze_plane_cell_capacity,
                                           plane_cell_count,
                                           sizeof(float))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_red_storage,
                                           &scratch->amaze_plane_cell_capacity,
                                           plane_cell_count,
                                           sizeof(float))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_green_storage,
                                           &scratch->amaze_plane_cell_capacity,
                                           plane_cell_count,
                                           sizeof(float))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_blue_storage,
                                           &scratch->amaze_plane_cell_capacity,
                                           plane_cell_count,
                                           sizeof(float)))
    {
        return 0;
    }

    scratch->amaze_row_width = MAX(scratch->amaze_row_width, row_width);
    assign_amaze_plane_rows(scratch->amaze_rawData_rows, scratch->amaze_rawData_storage, row_count, row_width);
    assign_amaze_plane_rows(scratch->amaze_red_rows, scratch->amaze_red_storage, row_count, row_width);
    assign_amaze_plane_rows(scratch->amaze_green_rows, scratch->amaze_green_storage, row_count, row_width);
    assign_amaze_plane_rows(scratch->amaze_blue_rows, scratch->amaze_blue_storage, row_count, row_width);

    if (!ensure_reusable_scratch_buffer((void **)&scratch->amaze_gray,
                                        &scratch->amaze_pixel_capacity,
                                        pixel_count,
                                        sizeof(uint32_t))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_edge_direction,
                                           &scratch->amaze_pixel_capacity,
                                           pixel_count,
                                           sizeof(uint8_t)))
    {
        return 0;
    }

    if (!ensure_reusable_scratch_buffer((void **)&scratch->amaze_startchunk_y,
                                        &scratch->amaze_thread_capacity,
                                        thread_count,
                                        sizeof(int))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_endchunk_y,
                                           &scratch->amaze_thread_capacity,
                                           thread_count,
                                           sizeof(int))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_thread_id,
                                           &scratch->amaze_thread_capacity,
                                           thread_count,
                                           sizeof(pthread_t))
        || !ensure_reusable_scratch_buffer((void **)&scratch->amaze_arguments,
                                           &scratch->amaze_thread_capacity,
                                           thread_count,
                                           sizeof(amazeinfo_t)))
    {
        return 0;
    }

    return 1;
}

static uint16_t * ensure_alias_aux_scratch(dualiso_full20bit_scratch_t * scratch, size_t pixel_count)
{
    if (!scratch)
    {
        return NULL;
    }

    return ensure_reusable_scratch_buffer((void **)&scratch->alias_aux,
                                          &scratch->alias_aux_capacity,
                                          pixel_count,
                                          sizeof(uint16_t))
        ? scratch->alias_aux
        : NULL;
}

void free_dualiso_full20bit_scratch(dualiso_full20bit_scratch_t * scratch)
{
    if (!scratch)
    {
        return;
    }

    free(scratch->raw_buffer_32);
    free(scratch->dark);
    free(scratch->bright);
    free(scratch->fullres);
    free(scratch->halfres);
    free(scratch->fullres_smooth);
    free(scratch->halfres_smooth);
    free(scratch->overexposed);
    free(scratch->alias_map);
    free(scratch->over_aux);
    free(scratch->mix_curve);
    free(scratch->histogram_match_dark);
    free(scratch->histogram_match_bright);
    free(scratch->histogram_match_tmp);
    free(scratch->histogram_match_hi_dark);
    free(scratch->histogram_match_hi_bright);
    free(scratch->identify_histograms);
    free(scratch->amaze_squeezed);
    free(scratch->amaze_rawData_rows);
    free(scratch->amaze_red_rows);
    free(scratch->amaze_green_rows);
    free(scratch->amaze_blue_rows);
    free(scratch->amaze_rawData_storage);
    free(scratch->amaze_red_storage);
    free(scratch->amaze_green_storage);
    free(scratch->amaze_blue_storage);
    free(scratch->amaze_gray);
    free(scratch->amaze_edge_direction);
    free(scratch->amaze_startchunk_y);
    free(scratch->amaze_endchunk_y);
    free(scratch->amaze_thread_id);
    free(scratch->amaze_arguments);
    free(scratch->alias_aux);
    memset(scratch, 0, sizeof(*scratch));
}

static int ensure_full20bit_pixel_capacity(dualiso_full20bit_scratch_t * scratch, size_t pixel_count)
{
    if (!scratch)
    {
        return 0;
    }

    if (scratch->pixel_capacity >= pixel_count
        && scratch->raw_buffer_32
        && scratch->dark
        && scratch->bright
        && scratch->fullres
        && scratch->halfres
        && scratch->fullres_smooth
        && scratch->halfres_smooth
        && scratch->overexposed
        && scratch->alias_map
        && scratch->over_aux)
    {
        return 1;
    }

    uint32_t * next_raw = (uint32_t *)malloc(pixel_count * sizeof(uint32_t));
    uint32_t * next_dark = (uint32_t *)malloc(pixel_count * sizeof(uint32_t));
    uint32_t * next_bright = (uint32_t *)malloc(pixel_count * sizeof(uint32_t));
    uint32_t * next_fullres = (uint32_t *)malloc(pixel_count * sizeof(uint32_t));
    uint32_t * next_halfres = (uint32_t *)malloc(pixel_count * sizeof(uint32_t));
    uint32_t * next_fullres_smooth = (uint32_t *)malloc(pixel_count * sizeof(uint32_t));
    uint32_t * next_halfres_smooth = (uint32_t *)malloc(pixel_count * sizeof(uint32_t));
    uint16_t * next_overexposed = (uint16_t *)malloc(pixel_count * sizeof(uint16_t));
    uint16_t * next_alias_map = (uint16_t *)malloc(pixel_count * sizeof(uint16_t));
    uint16_t * next_over_aux = (uint16_t *)malloc(pixel_count * sizeof(uint16_t));

    if (!next_raw
        || !next_dark
        || !next_bright
        || !next_fullres
        || !next_halfres
        || !next_fullres_smooth
        || !next_halfres_smooth
        || !next_overexposed
        || !next_alias_map
        || !next_over_aux)
    {
        free(next_raw);
        free(next_dark);
        free(next_bright);
        free(next_fullres);
        free(next_halfres);
        free(next_fullres_smooth);
        free(next_halfres_smooth);
        free(next_overexposed);
        free(next_alias_map);
        free(next_over_aux);
        return 0;
    }

    free(scratch->raw_buffer_32);
    free(scratch->dark);
    free(scratch->bright);
    free(scratch->fullres);
    free(scratch->halfres);
    free(scratch->fullres_smooth);
    free(scratch->halfres_smooth);
    free(scratch->overexposed);
    free(scratch->alias_map);
    free(scratch->over_aux);

    scratch->raw_buffer_32 = next_raw;
    scratch->dark = next_dark;
    scratch->bright = next_bright;
    scratch->fullres = next_fullres;
    scratch->halfres = next_halfres;
    scratch->fullres_smooth = next_fullres_smooth;
    scratch->halfres_smooth = next_halfres_smooth;
    scratch->overexposed = next_overexposed;
    scratch->alias_map = next_alias_map;
    scratch->over_aux = next_over_aux;
    scratch->pixel_capacity = pixel_count;
    return 1;
}

//this is just meant to be fast
int diso_get_preview(uint16_t * image_data, uint16_t width, uint16_t height, int32_t black, int32_t white, int * iso_pattern, int diso_check, dualiso_preview_scratch_t * scratch)
{
    struct histogram * hist[4];
    struct histogram * hist_hi = NULL;
    struct histogram * hist_lo = NULL;
    const double histogram_start = mlv_stage_timing_now();

    if( scratch )
    {
        scratch->last_histogram_ms = 0.0;
        scratch->last_regression_ms = 0.0;
        scratch->last_rowscale_ms = 0.0;
    }
    
    for(int i = 0; i < 4; i++)
        hist[i] = hist_create(white);
    
    for(uint16_t y = 4; y < height - 4; y += 5)
    {
        hist_add(hist[y % 4], &(image_data[y * width + (y + 1) % 2]), width - (y + 1) % 2, 3);
    }
    
    uint16_t dark_row_start = UINT16_MAX;
    const int cached_pattern = iso_pattern ? *iso_pattern : 0;
    if( !set_preview_histograms_from_pattern(cached_pattern, hist, &dark_row_start, &hist_lo, &hist_hi) )
    {
        uint16_t median[4];
        for(int i = 0; i < 4; i++)
        {
            median[i] = hist_median(hist[i]);
        }

        if((median[2] - black) > ((median[0] - black) * 2) &&
           (median[2] - black) > ((median[1] - black) * 2) &&
           (median[3] - black) > ((median[0] - black) * 2) &&
           (median[3] - black) > ((median[1] - black) * 2))
        {
            dark_row_start = 0;
            hist_lo = hist[0];
            hist_hi = hist[2];
        }
        else if((median[0] - black) > ((median[1] - black) * 2) &&
                (median[0] - black) > ((median[2] - black) * 2) &&
                (median[3] - black) > ((median[1] - black) * 2) &&
                (median[3] - black) > ((median[2] - black) * 2))
        {
            dark_row_start = 1;
            hist_lo = hist[1];
            hist_hi = hist[0];
        }
        else if((median[0] - black) > ((median[2] - black) * 2) &&
                (median[0] - black) > ((median[3] - black) * 2) &&
                (median[1] - black) > ((median[2] - black) * 2) &&
                (median[1] - black) > ((median[3] - black) * 2))
        {
            dark_row_start = 2;
            hist_lo = hist[2];
            hist_hi = hist[0];
        }
        else if((median[1] - black) > ((median[0] - black) * 2) &&
                (median[1] - black) > ((median[3] - black) * 2) &&
                (median[2] - black) > ((median[0] - black) * 2) &&
                (median[2] - black) > ((median[3] - black) * 2))
        {
            dark_row_start = 3;
            hist_lo = hist[0];
            hist_hi = hist[2];
        }
        else
        {
#ifndef STDOUT_SILENT
            err_printf("\nCould not detect dual ISO interlaced lines\n");
#endif

            for(int i = 0; i < 4; i++)
            {
                hist_destroy(hist[i]);
            }
            if( scratch )
            {
                scratch->last_histogram_ms = (mlv_stage_timing_now() - histogram_start) * 1000.0;
            }
            return 0;
        }

        if( iso_pattern )
        {
            *iso_pattern = preview_pattern_from_dark_row_start(dark_row_start);
        }
    }

    if(diso_check)
    {
#ifndef STDOUT_SILENT
        err_printf("\nDetected dual ISO interlaced lines\n");
#endif

        for(int i = 0; i < 4; i++)
        {
            hist_destroy(hist[i]);
        }
        if( scratch )
        {
            scratch->last_histogram_ms = (mlv_stage_timing_now() - histogram_start) * 1000.0;
        }
        return 1;
    }

    if( scratch )
    {
        scratch->last_histogram_ms = (mlv_stage_timing_now() - histogram_start) * 1000.0;
    }

    /* compare the two histograms and plot the curve between the two exposures (dark as a function of bright) */
    const int min_pix = 100;                                /* extract a data point every N image pixels */
    int data_size = (width * height / min_pix + 1);                  /* max number of data points */
    int* data_x = NULL;
    int* data_y = NULL;
    double* data_w = NULL;
    const int using_scratch = scratch && ensure_preview_scratch_capacity(scratch, data_size);
    const double regression_start = mlv_stage_timing_now();

    if( using_scratch )
    {
        data_x = scratch->data_x;
        data_y = scratch->data_y;
        data_w = scratch->data_w;
    }
    else
    {
        data_x = (int *)malloc(data_size * sizeof(data_x[0]));
        data_y = (int *)malloc(data_size * sizeof(data_y[0]));
        data_w = (double *)malloc(data_size * sizeof(data_w[0]));
    }

    if (!data_x || !data_y || !data_w)
    {
        for(int i = 0; i < 4; i++)
        {
            hist_destroy(hist[i]);
        }

        if (!using_scratch)
        {
            free(data_x);
            free(data_y);
            free(data_w);
        }
        if( scratch )
        {
            scratch->last_regression_ms = (mlv_stage_timing_now() - regression_start) * 1000.0;
        }
        return 0;
    }

    int data_num = 0;
    
    int acc_lo = 0;
    int acc_hi = 0;
    int raw_lo = 0;
    int raw_hi = 0;
    int prev_acc_hi = 0;
    
    int hist_total = hist[0]->count;

    /* Iterate over histogram BINS (sized white+1 in hist_create), not the
     * pixel count. The previous loop bound `< hist_total` caused
     * out-of-bounds reads at hist_hi->data[raw_hi] whenever the source
     * frame contained more pixels than the white level (i.e., almost
     * always at 1080p+). The percentile thresholds at the data_w cutoff
     * below still use hist_total correctly. */
    for (raw_hi = 0; raw_hi <= hist_hi->white; raw_hi++)
    {
        acc_hi += hist_hi->data[raw_hi];

        while (acc_lo < acc_hi)
        {
            acc_lo += hist_lo->data[raw_lo];
            raw_lo++;
        }

        if (raw_lo >= white)
            break;
        
        if (acc_hi - prev_acc_hi > min_pix)
        {
            if (acc_hi > hist_total * 1 / 100 && acc_hi < hist_total * 99.99 / 100)    /* throw away outliers */
            {
                data_x[data_num] = raw_hi - black;
                data_y[data_num] = raw_lo - black;
                data_w[data_num] = (MAX(0, raw_hi - black + 100));    /* points from higher brightness are cleaner */
                data_num++;
                prev_acc_hi = acc_hi;
            }
        }
    }
    
    /**
     * plain least squares
     * y = ax + b
     * a = (mean(xy) - mean(x)mean(y)) / (mean(x^2) - mean(x)^2)
     * b = mean(y) - a mean(x)
     */
    
    double mx = 0, my = 0, mxy = 0, mx2 = 0;
    double weight = 0;
    for (int i = 0; i < data_num; i++)
    {
        mx += data_x[i] * data_w[i];
        my += data_y[i] * data_w[i];
        mxy += (double)data_x[i] * data_y[i] * data_w[i];
        mx2 += (double)data_x[i] * data_x[i] * data_w[i];
        weight += data_w[i];
    }
    mx /= weight;
    my /= weight;
    mxy /= weight;
    mx2 /= weight;

    /* Guard against zero-variance bright-row sample. When all bright
     * samples are at the same x-value (e.g. clipped highlights or a flat
     * patch), the regression denominator (mx2 - mx*mx) collapses to zero
     * and `a` becomes NaN/inf. NaN then propagates into 1/(a*a) at the
     * shadow computation and the per-pixel scale at lines below, casting
     * to undefined uint16 values that render as the magenta/cyan cast
     * users observed during playback (2026-04-24). Fall back to the
     * median-ratio scale (my/mx) and zero offset when variance is below
     * a small epsilon — that's the right "no slope detected" default
     * for the dual-ISO pair-up. */
    const double DUALISO_VARIANCE_EPSILON = 1e-9;
    const double denom = mx2 - mx * mx;
    double a, b;
    if (fabs(denom) < DUALISO_VARIANCE_EPSILON)
    {
        a = (fabs(mx) > DUALISO_VARIANCE_EPSILON) ? (my / mx) : 1.0;
        b = 0.0;
    }
    else
    {
        a = (mxy - mx * my) / denom;
        b = my - a * mx;
    }
    
    if (!using_scratch)
    {
        free(data_w);
        free(data_y);
        free(data_x);
    }

    for(int i = 0; i < 4; i++)
    {
        hist_destroy(hist[i]);
    }
    if( scratch )
    {
        scratch->last_regression_ms = (mlv_stage_timing_now() - regression_start) * 1000.0;
    }
    
    //TODO: what's a better way to pick a value for this?
    uint16_t shadow = (uint16_t)(black + 1 / (a * a) + b);
    const double rowscale_start = mlv_stage_timing_now();
    const size_t pixel_count = (size_t)width * (size_t)height;
    uint16_t * output_image = scratch ? ensure_preview_output_buffer(scratch, pixel_count) : NULL;
    const uint16_t * source_image = image_data;

    if( output_image )
    {
        memcpy(output_image, source_image, pixel_count * sizeof(uint16_t));
    }
    else
    {
        output_image = image_data;
    }

    /* Hot inner loop. Dispatched once via pthread_once to either an AVX2
     * vectorised implementation or a portable scalar fallback. The y-2
     * cross-row dependency forces sequential outer iteration; SIMD
     * happens within each row. See the dispatch helpers near the top of
     * this file. */
    dualiso_rowscale(width, height, dark_row_start,
                     source_image, output_image,
                     a, b,
                     black, white, shadow);

    if( output_image != image_data )
    {
        memcpy(image_data, output_image, pixel_count * sizeof(uint16_t));
    }

    if( scratch )
    {
        scratch->last_rowscale_ms = (mlv_stage_timing_now() - rowscale_start) * 1000.0;
    }

    return 1;
}


//from cr2hdr 20bit version
//this is not thread safe (yet)

#define BRIGHT_ROW (is_bright[y % 4])
#define COUNT(x) ((int)(sizeof(x)/sizeof((x)[0])))

#define raw_get_pixel(x,y) (image_data[(x) + (y) * raw_info.width])
#define raw_get_pixel16(x,y) (image_data[(x) + (y) * raw_info.width])
#define raw_get_pixel_14to20(x,y) ((((uint32_t)image_data[(x) + (y) * raw_info.width]) << 6) & 0xFFFFF)
#define raw_get_pixel32(x,y) (raw_buffer_32[(x) + (y) * raw_info.width])
#define raw_set_pixel32(x,y,value) raw_buffer_32[(x) + (y)*raw_info.width] = value
#define raw_get_pixel_20to16(x,y) ((raw_get_pixel32(x,y) >> 4) & 0xFFFF)
#define raw_set_pixel_20to16_rand(x,y,value) image_data[(x) + (y) * raw_info.width] = COERCE((int)((value) / 16.0 + fast_randn05() + 0.5), 0, 0xFFFF)
#define raw_set_pixel20(x,y,value) raw_buffer_32[(x) + (y) * raw_info.width] = COERCE((value), 0, 0xFFFFF)

static const double fullres_thr = 0.8;

/* trial and error - too high = aliasing, too low = noisy */
static const int ALIAS_MAP_MAX = 15000;

static void white_detect(struct raw_info raw_info, uint16_t * image_data, int* white_dark, int* white_bright, int * is_bright)
{
    /* sometimes the white level is much lower than 15000; this would cause pink highlights */
    /* workaround: consider the white level as a little under the maximum pixel value from the raw file */
    /* caveat: bright and dark exposure may have different white levels, so we'll take the minimum value */
    /* side effect: if the image is not overexposed, it may get brightened a little; shouldn't hurt */
    
    int whites[2]         = {  0,    0};
    int discard_pixels[2] = { 10,   50}; /* discard the brightest N pixels */
    int safety_margins[2] = {100, 1500}; /* use a higher safety margin for the higher ISO */
    /* note: with the high-ISO WL underestimated by 1500, you would lose around 0.15 EV of non-aliased detail */
    
    int* pixels[2];
    int max_pix = raw_info.width * raw_info.height / 2 / 9;
    pixels[0] = malloc(max_pix * sizeof(pixels[0][0]));
    pixels[1] = malloc(max_pix * sizeof(pixels[0][0]));
    memset(pixels[0], 0, sizeof(max_pix * sizeof(pixels[0][0])));
    memset(pixels[1], 0, sizeof(max_pix * sizeof(pixels[0][0])));
    int counts[2] = {0, 0};
    
    /* collect all the pixels and find the k-th max, thus ignoring hot pixels */
    /* change the sign in order to use kth_smallest_int */
    //#pragma omp parallel for collapse(2)
    for (int y = raw_info.active_area.y1; y < raw_info.active_area.y2; y += 3)
    {
        for (int x = raw_info.active_area.x1; x < raw_info.active_area.x2; x += 3)
        {
            int pix = raw_get_pixel16(x, y);
            
#define BIN_IDX is_bright[y%4]
            counts[BIN_IDX] = MIN(counts[BIN_IDX], max_pix-1);
            pixels[BIN_IDX][counts[BIN_IDX]] = -pix;
            counts[BIN_IDX]++;
#undef BIN_IDX
        }
    }
    
    whites[0] = -kth_smallest_int(pixels[0], counts[0], discard_pixels[0]) - safety_margins[0];
    whites[1] = -kth_smallest_int(pixels[1], counts[1], discard_pixels[1]) - safety_margins[1];
    
    //~ printf("%8d %8d\n", whites[0], whites[1]);
    //~ printf("%8d %8d\n", counts[0], counts[1]);
    
    /* we assume 14-bit input data; out-of-range white levels may cause crash */
    *white_dark = COERCE(whites[0], 10000, 16383);
    *white_bright = COERCE(whites[1], 5000, 16383);
#ifndef STDOUT_SILENT
    printf("White levels    : %d %d\n", *white_dark, *white_bright);
#endif
    free(pixels[0]);
    free(pixels[1]);
}

static void compute_black_noise(struct raw_info raw_info, uint16_t * image_data, int x1, int x2, int y1, int y2, int dx, int dy, double* out_mean, double* out_stdev)
{
    long long black = 0;
    int num = 0;
    /* compute average level */
    #pragma omp parallel for collapse(2)
    for (int y = y1; y < y2; y += dy)
    {
        for (int x = x1; x < x2; x += dx)
        {
            black += raw_get_pixel(x, y);
            num++;
        }
    }
    
    double mean = (double) black / num;
    
    /* compute standard deviation */
    double stdev = 0;
    #pragma omp parallel for collapse(2)
    for (int y = y1; y < y2; y += dy)
    {
        for (int x = x1; x < x2; x += dx)
        {
            double dif = raw_get_pixel(x, y) - mean;
            stdev += dif * dif;
        }
    }
    stdev /= (num-1);
    stdev = sqrt(stdev);
    
    if (num == 0)
    {
        mean = raw_info.black_level;
        stdev = 8; /* default to 11 stops of DR */
    }
    
    *out_mean = mean;
    *out_stdev = stdev;
}

static int mean2(int a, int b, int white, int* err)
{
    if (a >= white || b >= white)
    {
        if (err) *err = 10000000;
        return white;
    }
    
    int m = (a + b) / 2;
    
    if (err)
        *err = ABS(a - b);
    
    return m;
}

static int mean3(int a, int b, int c, int white, int* err)
{
    int m = (a + b + c) / 3;
    
    if (err)
        *err = MAX(MAX(ABS(a - m), ABS(b - m)), ABS(c - m));
    
    if (a >= white || b >= white || c >= white)
        return MAX(m, white);
    
    return m;
}

/* http://www.developpez.net/forums/d544518/c-cpp/c/equivalent-randn-matlab-c/#post3241904 */

#define TWOPI (6.2831853071795864769252867665590057683943387987502) /* 2 * pi */

/*
 RAND is a macro which returns a pseudo-random numbers from a uniform
 distribution on the interval [0 1]
 */
#define RAND (rand())/((double) RAND_MAX)

/*
 RANDN is a macro which returns a pseudo-random numbers from a normal
 distribution with mean zero and standard deviation one. This macro uses Box
 Muller's algorithm
 */
#define RANDN (sqrt(-2.0*log(RAND))*cos(TWOPI*RAND))

/* anti-posterization noise */
/* before rounding, it's a good idea to add a Gaussian noise of stdev=0.5 */
static float randn05_cache[1024];

void fast_randn_init()
{
    int i;
    for (i = 0; i < 1024; i++)
    {
        randn05_cache[i] = RANDN / 2;
    }
}

float fast_randn05()
{
    static int k = 0;
    return randn05_cache[(k++) & 1023];
}

static int identify_rggb_or_gbrg(struct raw_info raw_info,
                                 uint16_t * image_data,
                                 dualiso_full20bit_scratch_t * scratch)
{
    int w = raw_info.width;
    int h = raw_info.height;
    int * histogram_storage = ensure_identify_histograms_scratch(scratch);

    if (!histogram_storage)
    {
        return 0;
    }

    /* build 4 little histograms: one for red, one for blue and two for green */
    /* we don't know yet which channels are which, but that's what we are trying to find out */
    /* the ones with the smallest difference are likely the green channels */
    int* hist[4];
    for (int i = 0; i < 4; i++)
    {
        hist[i] = histogram_storage + (i * 16384);
    }
    memset(histogram_storage, 0, 4 * 16384 * sizeof(int));
    
    int y0 = (raw_info.active_area.y1 + 3) & ~3;
    
    /* to simplify things, analyze an identical number of bright and dark lines */
    #pragma omp parallel for collapse(2)
    for (int y = y0; y < h/4*4; y++)
    {
        for (int x = 0; x < w; x++)
            hist[(y%2)*2 + (x%2)][raw_get_pixel16(x,y) & 16383]++;
    }
    
    /* compute cdf */
    for (int k = 0; k < 4; k++)
    {
        int acc = 0;
        for (int i = 0; i < 16384; i++)
        {
            acc += hist[k][i];
            hist[k][i] = acc;
        }
    }
    
    /* compare cdf's */
    /* for rggb, greens are at y%2 != x%2, that is, 1 and 2 */
    /* for gbrg, greens are at y%2 == x%2, that is, 0 and 3 */
    double diffs_rggb = 0;
    double diffs_gbrg = 0;
    //#pragma omp parallel for
    for (int i = 0; i < 16384; i++)
    {
        diffs_rggb += ABS(hist[1][i] - hist[2][i]);
        diffs_gbrg += ABS(hist[0][i] - hist[3][i]);
    }
    
    /* which one is most likely? */
    return diffs_rggb < diffs_gbrg;
}

static int identify_bright_and_dark_fields(struct raw_info raw_info,
                                           uint16_t * image_data,
                                           int rggb,
                                           int * is_bright,
                                           dualiso_full20bit_scratch_t * scratch)
{
    (void)rggb;
    /* first we need to know which lines are dark and which are bright */
    /* the pattern is not always the same, so we need to autodetect it */
    
    /* it may look like this */                       /* or like this */
    /*
     ab cd ef gh  ab cd ef gh               ab cd ef gh  ab cd ef gh
     
     0  RG RG RG RG  RG RG RG RG            0  rg rg rg rg  rg rg rg rg
     1  gb gb gb gb  gb gb gb gb            1  gb gb gb gb  gb gb gb gb
     2  rg rg rg rg  rg rg rg rg            2  RG RG RG RG  RG RG RG RG
     3  GB GB GB GB  GB GB GB GB            3  GB GB GB GB  GB GB GB GB
     4  RG RG RG RG  RG RG RG RG            4  rg rg rg rg  rg rg rg rg
     5  gb gb gb gb  gb gb gb gb            5  gb gb gb gb  gb gb gb gb
     6  rg rg rg rg  rg rg rg rg            6  RG RG RG RG  RG RG RG RG
     7  GB GB GB GB  GB GB GB GB            7  GB GB GB GB  GB GB GB GB
     8  RG RG RG RG  RG RG RG RG            8  rg rg rg rg  rg rg rg rg
     */
    
    /* white level is not yet known, just use a rough guess */
    int white = 10000;
    int black = raw_info.black_level;
    
    int w = raw_info.width;
    int h = raw_info.height;
    
    int * histogram_storage = ensure_identify_histograms_scratch(scratch);
    if (!histogram_storage)
    {
        return 0;
    }

    /* build 4 little histograms */
    int* hist[4];
    for (int i = 0; i < 4; i++)
    {
        hist[i] = histogram_storage + (i * 16384);
    }
    memset(histogram_storage, 0, 4 * 16384 * sizeof(int));
    
    int y0 = (raw_info.active_area.y1 + 3) & ~3;
    
    /* to simplify things, analyze an identical number of bright and dark lines */
    for (int y = y0; y < h/4*4; y++)
    {
        for (int x = 0; x < w; x++)
        {
            if ((x%2) != (y%2))
            {
                /* only check the green pixels */
                hist[y%4][raw_get_pixel16(x,y) & 16383]++;
            }
        }
    }
    
    int hist_total = 0;
    for (int i = 0; i < 16384; i++)
        hist_total += hist[0][i];
    
    /* choose the highest percentile that is not overexposed */
    /* but not higher than 99.8, to keep a tiny bit of robustness (specular highlights may play dirty tricks) */
    int acc[4] = {0};
    int raw[4] = {0};
    int off[4] = {0};
    int ref;
    int ref_max = hist_total * 0.998;
    int ref_off = hist_total * 0.05;
    for (ref = 0; ref < ref_max; ref++)
    {
        for (int i = 0; i < 4; i++)
        {
            while (acc[i] < ref)
            {
                acc[i] += hist[i][raw[i]];
                raw[i]++;
            }
        }
        
        if (ref < ref_off)
        {
            if (MAX(MAX(raw[0], raw[1]), MAX(raw[2], raw[3])) < black + (white-black) / 4)
            {
                /* try to remove the black offset by estimating it from relatively dark pixels */
                off[0] = raw[0];
                off[1] = raw[1];
                off[2] = raw[2];
                off[3] = raw[3];
            }
        }
        
        if (raw[0] >= white) break;
        if (raw[1] >= white) break;
        if (raw[2] >= white) break;
        if (raw[3] >= white) break;
    }
    
    /* remove black offsets */
    raw[0] -= off[0];
    raw[1] -= off[1];
    raw[2] -= off[2];
    raw[3] -= off[3];
    
    /* very crude way to compute median */
    int sorted_bright[4];
    memcpy(sorted_bright, raw, sizeof(sorted_bright));
    {
        for (int i = 0; i < 4; i++)
        {
            for (int j = i+1; j < 4; j++)
            {
                if (sorted_bright[i] > sorted_bright[j])
                {
                    double aux = sorted_bright[i];
                    sorted_bright[i] = sorted_bright[j];
                    sorted_bright[j] = aux;
                }
            }
        }
    }
    double median_bright = (sorted_bright[1] + sorted_bright[2]) / 2;
    
    for (int i = 0; i < 4; i++)
        is_bright[i] = raw[i] > median_bright;
#ifndef STDOUT_SILENT
    printf("ISO pattern     : %c%c%c%c %s\n", is_bright[0] ? 'B' : 'd', is_bright[1] ? 'B' : 'd', is_bright[2] ? 'B' : 'd', is_bright[3] ? 'B' : 'd', rggb ? "RGGB" : "GBRG");
#endif
    if (is_bright[0] + is_bright[1] + is_bright[2] + is_bright[3] != 2)
    {
#ifndef STDOUT_SILENT
        printf("Bright/dark detection error\n");
#endif
        return 0;
    }
    
    if (is_bright[0] == is_bright[2] || is_bright[1] == is_bright[3])
    {
#ifndef STDOUT_SILENT
        printf("Interlacing method not supported\n");
#endif
        return 0;
    }
    return 1;
}

static int _match_exposures(struct raw_info raw_info, uint32_t * raw_buffer_32, double * corr_ev, int * white_darkened, int * is_bright)
{
    /* guess ISO - find the factor and the offset for matching the bright and dark images */
    int black20 = raw_info.black_level;
    int white20 = MIN(raw_info.white_level, *white_darkened);
    int black = black20/16;
    int white = white20/16;
    int clip0 = white - black;
    int clip  = clip0 * 0.95;    /* there may be nonlinear response in very bright areas */
    
    int w = raw_info.width;
    int h = raw_info.height;
    int y0 = raw_info.active_area.y1 + 2;
    
    /* quick interpolation for matching */
    int* dark   = malloc(w * h * sizeof(dark[0]));
    int* bright = malloc(w * h * sizeof(bright[0]));
    memset(dark, 0, w * h * sizeof(dark[0]));
    memset(bright, 0, w * h * sizeof(bright[0]));
    
    //#pragma omp parallel for
    for (int y = y0; y < h-2; y += 3)
    {
        int* native = BRIGHT_ROW ? bright : dark;
        int* interp = BRIGHT_ROW ? dark : bright;

        for (int x = 0; x < w; x += 3)
        {
            int pa = raw_get_pixel_20to16(x, y-2) - black;
            int pb = raw_get_pixel_20to16(x, y+2) - black;
            int pn = raw_get_pixel_20to16(x, y) - black;
            int pi = (pa + pb + 1) / 2;
            if (pa >= clip || pb >= clip) pi = clip0;               /* pixel too bright? discard */
            if (pi >= clip) pn = clip0;                             /* interpolated pixel not good? discard the other one too */
            interp[x + y * w] = pi;
            native[x + y * w] = pn;
        }
    }
    
    /*
     * Robust line fit (match unclipped data):
     * - use (median_bright, median_dark) as origin
     * - select highlights between 98 and 99.9th percentile to find the slope (ISO)
     * - choose the slope that explains the largest number of highlight points (inspired from RANSAC)
     *
     * Rationale:
     * - exposure matching is important to be correct in bright_highlights (which are combined with dark_midtones)
     * - low percentiles are likely affected by noise (this process is essentially a histogram matching)
     * - as ad-hoc as it looks, it's the only method that passed all the test samples so far.
     */
    int nmax = (w+2) * (h+2) / 9;   /* downsample by 3x3 for speed */
    int * tmp = malloc(nmax * sizeof(tmp[0]));
    
    /* median_bright */
    int n = 0;
    for (int y = y0; y < h-2; y += 3)
    {
        for (int x = 0; x < w; x += 3)
        {
            int b = bright[x + y*w];
            if (b >= clip) continue;
            tmp[n++] = b;
        }
    }
    int bmed = median_int_wirth(tmp, n);
    
    int * bps = 0;
    
    /* also compute the range for bright pixels (used to find the slope) */
    int b_lo = kth_smallest_int(tmp, n, n*98/100);
    int b_hi = kth_smallest_int(tmp, n, n*99.9/100);
    
    /* median_dark */
    n = 0;
    for (int y = y0; y < h-2; y += 3)
    {
        for (int x = 0; x < w; x += 3)
        {
            int d = dark[x + y*w];
            int b = bright[x + y*w];
            if (b >= clip) continue;
            tmp[n++] = d;
        }
    }
    int dmed = median_int_wirth(tmp, n);
    
    int * dps = 0;
    
    /* select highlights used to find the slope (ISO) */
    /* (98th percentile => up to 2% highlights) */
    int hi_nmax = nmax/50;
    int hi_n = 0;
    int* hi_dark = malloc(hi_nmax * sizeof(hi_dark[0]));
    int* hi_bright = malloc(hi_nmax * sizeof(hi_bright[0]));
    
    for (int y = y0; y < h-2; y += 3)
    {
        for (int x = 0; x < w; x += 3)
        {
            int d = dark[x + y*w];
            int b = bright[x + y*w];
            if (b >= b_hi) continue;
            if (b <= b_lo) continue;
            hi_dark[hi_n] = d;
            hi_bright[hi_n] = b;
            hi_n++;
            if (hi_n >= hi_nmax) break;
        }
    }
    
    //~ printf("Selected %d highlight points (max %d)\n", hi_n, hi_nmax);
    
    double a = 0;
    double b = 0;
    
    int best_score = 0;
    for (double ev = 0; ev < 6; ev += 0.002)
    {
        double test_a = pow(2, -ev);
        double test_b = dmed - bmed * test_a;
        
        int score = 0;
        for (int i = 0; i < hi_n; i++)
        {
            int d = hi_dark[i];
            int b = hi_bright[i];
            int e = d - (b*test_a + test_b);
            if (ABS(e) < 50) score++;
        }
        if (score > best_score)
        {
            best_score = score;
            a = test_a;
            b = test_b;
            //~ printf("%f: %d\n", a, score);
        }
    }
    free(hi_dark); hi_dark = 0;
    free(hi_bright); hi_bright = 0;
    free(tmp); tmp = 0;
    
    free(dark);
    free(bright);
    if (dps) free(dps);
    if (bps) free(bps);
    
    /* apply the correction */
    double b20 = b * 16;
    //#pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y ++)
    {
        for (int x = 0; x < w; x ++)
        {
            int p = raw_get_pixel32(x, y);
            if (p == 0) continue;
            
            if (BRIGHT_ROW)
            {
                /* bright exposure: darken and apply the black offset (fixme: why not half?) */
                p = (p - black20) * a + black20 + b20*a;
            }
            else
            {
                p = p - b20 + b20*a;
            }
            
            /* out of range? */
            /* note: this breaks M24-1127 */
            //p = COERCE(p, 0, 0xFFFFF);
            
            raw_set_pixel20(x, y, p);
        }
    }
    *white_darkened = (white20 - black20 + b20) * a + black20;
    
    double factor = 1/a;
    if (factor < 1.2 || !isfinite(factor))
    {
#ifndef STDOUT_SILENT
        printf("Doesn't look like interlaced ISO\n");
#endif
        return 0;
    }
    
    *corr_ev = log2(factor);
#ifndef STDOUT_SILENT
    printf("ISO difference  : %.2f EV (%d)\n", log2(factor), (int)round(factor*100));
    printf("Black delta     : %.2f\n", b/4); /* we want to display black delta for the 14-bit original data, but we have computed it from 16-bit data */
#endif
    return 1;
}

static void match_by_histogram(struct raw_info raw_info,
                               uint32_t * raw_buffer_32,
                               double * ev_correction,
                               int * black_delta,
                               int * white_darkened,
                               int * is_bright,
                               dualiso_full20bit_scratch_t * scratch)
{
    /* guess ISO - find the factor and the offset for matching the bright and dark images */
    int black20 = raw_info.black_level;
    int white20 = MIN(raw_info.white_level, *white_darkened);
    int black = black20/16;
    int white = white20/16;
    int clip0 = white - black;
    int clip  = clip0 * 0.95;    /* there may be nonlinear response in very bright areas */

    int w = raw_info.width;
    int h = raw_info.height;
    int y0 = raw_info.active_area.y1 + 2;

    /* quick interpolation for matching */
    const size_t pixel_count = (size_t)w * (size_t)h;
    int nmax = (w+2) * (h+2) / 9;   /* downsample by 3x3 for speed */
    int hi_nmax = MAX(nmax/50, 1);

    if (!ensure_histogram_match_scratch(scratch, pixel_count, (size_t)nmax, (size_t)hi_nmax))
    {
        return;
    }

    int * dark = scratch->histogram_match_dark;
    int * bright = scratch->histogram_match_bright;
    memset(dark, 0, w * h * sizeof(dark[0]));
    memset(bright, 0, w * h * sizeof(bright[0]));

    //#pragma omp parallel for
    for (int y = y0; y < h-2; y += 3)
    {
        int* native = BRIGHT_ROW ? bright : dark;
        int* interp = BRIGHT_ROW ? dark : bright;

        for (int x = 0; x < w; x += 3)
        {
            int pa = raw_get_pixel_20to16(x, y-2) - black;
            int pb = raw_get_pixel_20to16(x, y+2) - black;
            int pn = raw_get_pixel_20to16(x, y) - black;
            int pi = (pa + pb + 1) / 2;
            if (pa >= clip || pb >= clip) pi = clip0;               /* pixel too bright? discard */
            if (pi >= clip) pn = clip0;                             /* interpolated pixel not good? discard the other one too */
            interp[x + y * w] = pi;
            native[x + y * w] = pn;
        }
    }

    /*
     * Robust line fit (match unclipped data):
     * - use (median_bright, median_dark) as origin
     * - select highlights between 98 and 99.9th percentile to find the slope (ISO)
     * - choose the slope that explains the largest number of highlight points (inspired from RANSAC)
     *
     * Rationale:
     * - exposure matching is important to be correct in bright_highlights (which are combined with dark_midtones)
     * - low percentiles are likely affected by noise (this process is essentially a histogram matching)
     * - as ad-hoc as it looks, it's the only method that passed all the test samples so far.
     */
    int * tmp = scratch->histogram_match_tmp;

    /* median_bright */
    int n = 0;
    for (int y = y0; y < h-2; y += 3)
    {
        for (int x = 0; x < w; x += 3)
        {
            int b = bright[x + y*w];
            if (b >= clip) continue;
            tmp[n++] = b;
        }
    }
    int bmed = median_int_wirth(tmp, n);

    /* also compute the range for bright pixels (used to find the slope) */
    int b_lo = kth_smallest_int(tmp, n, n*98/100);
    int b_hi = kth_smallest_int(tmp, n, n*99.9/100);

    /* median_dark */
    n = 0;
    for (int y = y0; y < h-2; y += 3)
    {
        for (int x = 0; x < w; x += 3)
        {
            int d = dark[x + y*w];
            int b = bright[x + y*w];
            if (b >= clip) continue;
            tmp[n++] = d;
        }
    }
    int dmed = median_int_wirth(tmp, n);

    /* select highlights used to find the slope (ISO) */
    /* (98th percentile => up to 2% highlights) */
    int hi_n = 0;
    int* hi_dark = scratch->histogram_match_hi_dark;
    int* hi_bright = scratch->histogram_match_hi_bright;

    for (int y = y0; y < h-2; y += 3)
    {
        for (int x = 0; x < w; x += 3)
        {
            int d = dark[x + y*w];
            int b = bright[x + y*w];
            if (b >= b_hi) continue;
            if (b <= b_lo) continue;
            hi_dark[hi_n] = d;
            hi_bright[hi_n] = b;
            hi_n++;
            if (hi_n >= hi_nmax) break;
        }
    }

    //~ printf("Selected %d highlight points (max %d)\n", hi_n, hi_nmax);

    double a = 0;
    double b = 0;

    int best_score = 0;
    for (double ev = 0; ev < 6; ev += 0.01)
    {
        double test_a = pow(2, -ev);
        double test_b = dmed - bmed * test_a;

        int score = 0;
        for (int i = 0; i < hi_n; i++)
        {
            int d = hi_dark[i];
            int b = hi_bright[i];
            int e = d - (b*test_a + test_b);
            if (ABS(e) < 50) score++;
        }
        if (score > best_score)
        {
            best_score = score;
            a = test_a;
            b = test_b;
            //~ printf("%f: %d\n", a, score);
        }
    }

    *ev_correction = log2(1/a);
    *black_delta = b * 16;
}

static int match_exposures(struct raw_info raw_info,
                           uint32_t * raw_buffer_32,
                           int dark_frame,
                           int iso1,
                           int iso2,
                           int * auto_correction,
                           double * ev_correction,
                           int * black_delta,
                           int * white_darkened,
                           int * is_bright,
                           dualiso_full20bit_scratch_t * scratch)
{
    int black = raw_info.black_level;
    int white = MIN(raw_info.white_level, *white_darkened);

    int w = raw_info.width;
    int h = raw_info.height;

    double _ev_correction = 0.0;
    int _black_delta = 0;

    if (*auto_correction == -1)
    {
        int low_iso = MIN(iso1, iso2);
        int high_iso = MAX(iso1, iso2);

        _ev_correction = log2(high_iso / low_iso);

        // ISO 6400 having the same brightness as ISO 3200 on 650D. Not sure about other cameras.
        // Anyway, ISO 6400 and higher is considerred useless.
        if (high_iso >= 6400 && _ev_correction > 0)
        {
            high_iso /= 2;
            _ev_correction -= 1;
        }

        if (!dark_frame)
        {
            _black_delta = ((high_iso / 100) * 64) - ((low_iso / 100) * 64);
        }
    }
    else if (*auto_correction == -2)
    {
        match_by_histogram(raw_info, raw_buffer_32, &_ev_correction, &_black_delta, white_darkened, is_bright, scratch);
    }

    if (*ev_correction != 1)
    {
        _ev_correction = -*ev_correction;
    }

    if (*black_delta != -1)
    {
        _black_delta = *black_delta * 64;
    }

    _ev_correction = COERCE(_ev_correction, 0, 6.0);
    _black_delta = COERCE(_black_delta, 0, 100 * 64);

    *ev_correction = -_ev_correction;
    *black_delta = _black_delta / 64;

    //printf("DISO: %d, %.2f, %d\n", *auto_correction, *ev_correction, *black_delta);

    if (_ev_correction < 0.5)
    {
#ifndef STDOUT_SILENT
        printf("Doesn't look like interlaced ISO.\n");
#endif
        return 0;
    }

    double factor = pow(2, -_ev_correction);

    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y ++)
    {
        for (int x = 0; x < w; x ++)
        {
            int p = raw_get_pixel32(x, y);

            if (p == 0) continue;

            if (BRIGHT_ROW)
            {
                p = ((p - black + _black_delta) * factor) + black;
            }
            else
            {
                p = (p - _black_delta) + (_black_delta * factor);
            }

            raw_set_pixel20(x, y, p);
        }
    }

    *white_darkened = ((white - black + _black_delta) * factor) + black;

    return 1;
}

static inline uint32_t * convert_to_20bit(struct raw_info raw_info, uint16_t * image_data, uint32_t * raw_buffer_32)
{
    int w = raw_info.width;
    int h = raw_info.height;
    if (!raw_buffer_32)
    {
        return NULL;
    }

#ifdef DUALISO_AVX2_AVAILABLE
    pthread_once(&g_dualiso_hq_dispatch_once, dualiso_hq_dispatch_init);
    if (g_dualiso_hq_use_avx2)
    {
        /* Per-row dispatch so OMP parallelism still applies. */
        #pragma omp parallel for
        for (int y = 0; y < h; y ++) {
            convert_to_20bit_avx2(&raw_buffer_32[(size_t)y*w],
                                  &image_data[(size_t)y*w],
                                  (size_t)w);
        }
        return raw_buffer_32;
    }
#endif

    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y ++)
        for (int x = 0; x < w; x ++)
            raw_buffer_32[x + y*w] = raw_get_pixel_14to20(x, y);

    return raw_buffer_32;
}

static inline void build_ev2raw_lut(int * raw2ev, int * ev2raw_0, int black, int white)
{
    int* ev2raw = ev2raw_0 + 10*EV_RESOLUTION;
    
    #pragma omp parallel for
    for (int i = 0; i < 1<<20; i++)
    {
        double signal = MAX(i/64.0 - black/64.0, -1023);
        if (signal > 0)
            raw2ev[i] = (int)round(log2(1+signal) * EV_RESOLUTION);
        else
            raw2ev[i] = -(int)round(log2(1-signal) * EV_RESOLUTION);
    }
    
    #pragma omp parallel for
    for (int i = -10*EV_RESOLUTION; i < 0; i++)
    {
        ev2raw[i] = COERCE(black+64 - round(64*pow(2, ((double)-i/EV_RESOLUTION))), 0, black);
    }
    
    #pragma omp parallel for
    for (int i = 0; i < 14*EV_RESOLUTION; i++)
    {
        ev2raw[i] = COERCE(black-64 + round(64*pow(2, ((double)i/EV_RESOLUTION))), black, (1<<20)-1);
        
        if (i >= raw2ev[white])
        {
            ev2raw[i] = MAX(ev2raw[i], white);
        }
    }
    
    /* keep "bad" pixels, if any */
    ev2raw[raw2ev[0]] = 0;
    ev2raw[raw2ev[0]] = 0;
    
    /* check raw <--> ev conversion */
    //~ printf("%d %d %d %d %d %d %d *%d* %d %d %d %d %d\n", raw2ev[0],         raw2ev[16000],         raw2ev[32000],         raw2ev[131068],         raw2ev[131069],         raw2ev[131070],         raw2ev[131071],         raw2ev[131072],         raw2ev[131073],         raw2ev[131074],         raw2ev[131075],         raw2ev[131076],         raw2ev[132000]);
    //~ printf("%d %d %d %d %d %d %d *%d* %d %d %d %d %d\n", ev2raw[raw2ev[0]], ev2raw[raw2ev[16000]], ev2raw[raw2ev[32000]], ev2raw[raw2ev[131068]], ev2raw[raw2ev[131069]], ev2raw[raw2ev[131070]], ev2raw[raw2ev[131071]], ev2raw[raw2ev[131072]], ev2raw[raw2ev[131073]], ev2raw[raw2ev[131074]], ev2raw[raw2ev[131075]], ev2raw[raw2ev[131076]], ev2raw[raw2ev[132000]]);
}

static inline double compute_noise(struct raw_info raw_info, uint16_t * image_data, double * noise_std, double * dark_noise, double * bright_noise, double * dark_noise_ev, double * bright_noise_ev)
{
    double noise_avg = 0.0;
    for (int y = 0; y < 4; y++)
        compute_black_noise(raw_info, image_data, 8, raw_info.active_area.x1 - 8, raw_info.active_area.y1/4*4 + 20 + y, raw_info.active_area.y2 - 20, 1, 4, &noise_avg, &noise_std[y]);
#ifndef STDOUT_SILENT
    printf("Noise levels    : %.02f %.02f %.02f %.02f (14-bit)\n", noise_std[0], noise_std[1], noise_std[2], noise_std[3]);
#endif
    *dark_noise = MIN(MIN(noise_std[0], noise_std[1]), MIN(noise_std[2], noise_std[3]));
    *bright_noise = MAX(MAX(noise_std[0], noise_std[1]), MAX(noise_std[2], noise_std[3]));
    *dark_noise_ev = log2(*dark_noise);
    *bright_noise_ev = log2(*bright_noise);
    return noise_avg;
}

static inline double * build_fullres_curve(int black)
{
    /* fullres mixing curve */
    static double fullres_curve[1<<20];
    static int previous_black = -1;
    
    if(previous_black == black) return fullres_curve;
    
    previous_black = black;
    
    const double fullres_start = 4;
    const double fullres_transition = 4;
    //const double fullres_thr = 0.8;
    
    #pragma omp parallel for
    for (int i = 0; i < (1<<20); i++)
    {
        double ev2 = log2(MAX(i/64.0 - black/64.0, 1));
        double c2 = -cos(COERCE(ev2 - fullres_start, 0, fullres_transition)*M_PI/fullres_transition);
        double f = (c2+1) / 2;
        fullres_curve[i] = f;
    }
    
    return fullres_curve;
}

/* define edge directions for interpolation */
struct xy { int x; int y; };
const struct
{
    struct xy ack;      /* verification pixel near a */
    struct xy a;        /* interpolation pixel from the nearby line: normally (0,s) but also (1,s) or (-1,s) */
    struct xy b;        /* interpolation pixel from the other line: normally (0,-2s) but also (1,-2s), (-1,-2s), (2,-2s) or (-2,-2s) */
    struct xy bck;      /* verification pixel near b */
}
edge_directions[] = {       /* note: all y coords should be multiplied by s */
    //~ { {-6,2}, {-3,1}, { 6,-2}, { 9,-3} },     /* almost horizontal (little or no improvement) */
    { {-4,2}, {-2,1}, { 4,-2}, { 6,-3} },
    { {-3,2}, {-1,1}, { 3,-2}, { 4,-3} },
    { {-2,2}, {-1,1}, { 2,-2}, { 3,-3} },     /* 45-degree diagonal */
    { {-1,2}, {-1,1}, { 1,-2}, { 2,-3} },
    { {-1,2}, { 0,1}, { 1,-2}, { 1,-3} },
    { { 0,2}, { 0,1}, { 0,-2}, { 0,-3} },     /* vertical, preferred; no extra confirmations needed */
    { { 1,2}, { 0,1}, {-1,-2}, {-1,-3} },
    { { 1,2}, { 1,1}, {-1,-2}, {-2,-3} },
    { { 2,2}, { 1,1}, {-2,-2}, {-3,-3} },     /* 45-degree diagonal */
    { { 3,2}, { 1,1}, {-3,-2}, {-4,-3} },
    { { 4,2}, { 2,1}, {-4,-2}, {-6,-3} },
    //~ { { 6,2}, { 3,1}, {-6,-2}, {-9,-3} },     /* almost horizontal */
};

/* Phase E1 — AMaZE edge-direction estimator AVX2 kernel.
 *
 * Included here (rather than at the top with dualiso_avx2.inc) because the
 * kernel needs the file-scope `edge_directions[]` table just declared above.
 * Wired via the g_dualiso_amaze_use_avx2 dispatch flag declared earlier in
 * this TU. */
#include "dualiso_amaze_avx2.inc"

static inline int edge_interp(float ** plane, int * squeezed, int * raw2ev, int dir, int x, int y, int s)
{
    
    int dxa = edge_directions[dir].a.x;
    int dya = edge_directions[dir].a.y * s;
    int pa = COERCE((int)plane[squeezed[y+dya]][x+dxa], 0, 0xFFFFF);
    int dxb = edge_directions[dir].b.x;
    int dyb = edge_directions[dir].b.y * s;
    int pb = COERCE((int)plane[squeezed[y+dyb]][x+dxb], 0, 0xFFFFF);
    int pi = (raw2ev[pa] * 2 + raw2ev[pb]) / 3;
    
    return pi;
}

static void* demosaic_wrapper(void* arg) {
    amazeinfo_t* info = (amazeinfo_t*)arg;
    demosaic(info);
    return NULL;
}

static inline void amaze_interpolate(struct raw_info raw_info,
                                     uint32_t * raw_buffer_32,
                                     uint32_t* dark,
                                     uint32_t* bright,
                                     int black,
                                     int white,
                                     int white_darkened,
                                     int * is_bright,
                                     int threads,
                                     dualiso_full20bit_scratch_t * scratch)
{
    int w = raw_info.width;
    int h = raw_info.height;
    int wx = w + 16;

    if (!ensure_amaze_interpolation_scratch(scratch, (size_t)h, (size_t)wx, (size_t)w * (size_t)h, (size_t)threads))
    {
        return;
    }

    int* squeezed = scratch->amaze_squeezed;
    float** rawData = scratch->amaze_rawData_rows;
    float** red = scratch->amaze_red_rows;
    float** green = scratch->amaze_green_rows;
    float** blue = scratch->amaze_blue_rows;
    memset(squeezed, 0, h * sizeof(int));
    memset(scratch->amaze_rawData_storage, 0, (size_t)h * (size_t)wx * sizeof(float));
    
    /* squeeze the dark image by deleting fields from the bright exposure */
    int yh = -1;
    for (int y = 0; y < h; y ++)
    {
        if (BRIGHT_ROW)
            continue;
        
        if (yh < 0) /* make sure we start at the same parity (RGGB cell) */
            yh = y;
        
        for (int x = 0; x < w; x++)
        {
            int p = raw_get_pixel32(x, y);
            
            if (x%2 != y%2) /* divide green channel by 2 to approximate the final WB better */
                p = (p - black) / 2 + black;
            
            rawData[yh][x] = p;
        }
        
        squeezed[y] = yh;
        
        yh++;
    }
    
    /* now the same for the bright exposure */
    yh = -1;
    for (int y = 0; y < h; y ++)
    {
        if (!BRIGHT_ROW)
            continue;
        
        if (yh < 0) /* make sure we start with the same parity (RGGB cell) */
            yh = h/4*2 + y;
        
        for (int x = 0; x < w; x++)
        {
            int p = raw_get_pixel32(x, y);
            
            if (x%2 != y%2) /* divide green channel by 2 to approximate the final WB better */
                p = (p - black) / 2 + black;
            
            rawData[yh][x] = p;
        }
        
        squeezed[y] = yh;
        
        yh++;
        if (yh >= h) break; /* just in case */
    }

    // Multithreaded debayer
    int* startchunk_y = scratch->amaze_startchunk_y;
    int* endchunk_y = scratch->amaze_endchunk_y;

    int chunk_height = h / threads;
    chunk_height -= chunk_height % 2;

    while(chunk_height <= 32 && threads > 1) {
        threads--;
        chunk_height = h / threads;
        chunk_height -= chunk_height % 2;
    }

    for (int thread = 0; thread < threads; ++thread) {
        startchunk_y[thread] = chunk_height * thread;
        endchunk_y[thread] = chunk_height * (thread + 1);
    }
    endchunk_y[threads-1] = h;

    pthread_t* thread_id = (pthread_t*)scratch->amaze_thread_id;
    amazeinfo_t* amaze_arguments = (amazeinfo_t*)scratch->amaze_arguments;

    for (int thread = 0; thread < threads; ++thread) {
        amaze_arguments[thread] = (amazeinfo_t) {
            rawData,
            red,
            green,
            blue,
            0, startchunk_y[thread],
            w, (endchunk_y[thread] - startchunk_y[thread]),
            0,
            0
        };
        
        pthread_create(&thread_id[thread], NULL, demosaic_wrapper, &amaze_arguments[thread]);
    }

    for (int thread = 0; thread < threads; ++thread) {
        pthread_join(thread_id[thread], NULL);
    }

    /* undo green channel scaling and clamp the other channels */
    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y ++)
    {
        for (int x = 0; x < w; x ++)
        {
            green[y][x] = COERCE((green[y][x] - black) * 2 + black, 0, 0xFFFFF);
            red[y][x] = COERCE(red[y][x], 0, 0xFFFFF);
            blue[y][x] = COERCE(blue[y][x], 0, 0xFFFFF);
        }
    }
#ifndef STDOUT_SILENT
    printf("Edge-directed interpolation...\n");
#endif
    //~ printf("Grayscale...\n");
    /* convert to grayscale and de-squeeze for easier processing */
    uint32_t * gray = scratch->amaze_gray;

    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y ++)
        for (int x = 0; x < w; x ++)
            gray[x + y*w] = green[squeezed[y]][x]/2 + red[squeezed[y]][x]/4 + blue[squeezed[y]][x]/4;
    
    
    uint8_t* edge_direction = scratch->amaze_edge_direction;
    int d0 = COUNT(edge_directions)/2;

    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y ++)
        for (int x = 0; x < w; x ++)
            edge_direction[x + y*w] = d0;
    
    double * fullres_curve = build_fullres_curve(black);
    
    //~ printf("Cross-correlation...\n");
    
    /* for fast EV - raw conversion */
    static int raw2ev[1<<20];   /* EV x EV_RESOLUTION */
    static int ev2raw_0[24*EV_RESOLUTION];
    static int previous_black = -1;
    
    /* handle sub-black values (negative EV) */
    int* ev2raw = ev2raw_0 + 10*EV_RESOLUTION;
    
    LOCK(ev2raw_mutex)
    {
        int semi_overexposed = 0;
        int not_overexposed = 0;
        int deep_shadow = 0;
        int not_shadow = 0;

        if(black != previous_black)
        {
            build_ev2raw_lut(raw2ev, ev2raw_0, black, white);
            previous_black = black;
        }
#ifdef DUALISO_AVX2_AVAILABLE
        pthread_once(&g_dualiso_amaze_dispatch_once, dualiso_amaze_dispatch_init);
        if (g_dualiso_amaze_use_avx2)
        {
            /* Phase E1 — AVX2 fast path. Skips the diagnostic stat counters
             * (semi_overexposed/not_overexposed/deep_shadow/not_shadow) which
             * only feed the STDOUT_SILENT printfs below. The kernel is
             * byte-identical for `edge_direction[x + y*w]` writes. */
            #pragma omp parallel for
            for (int y = 5; y < h-5; y ++)
            {
                int s = (is_bright[y%4] == is_bright[(y+1)%4]) ? -1 : 1;
                int br = BRIGHT_ROW;
                /* Pass a row pointer into the per-row kernel so its writes
                 * land in edge_direction[x + y*w]. */
                amaze_edge_direction_estimator_row_avx2(
                    y, w, s, br,
                    gray, raw_buffer_32, raw2ev,
                    fullres_curve, fullres_thr,
                    (uint32_t)white_darkened,
                    &edge_direction[(size_t)y * (size_t)w]);
            }
        }
        else
#endif
        {
        #pragma omp parallel for
        for (int y = 5; y < h-5; y ++)
        {
            int s = (is_bright[y%4] == is_bright[(y+1)%4]) ? -1 : 1;    /* points to the closest row having different exposure */
            for (int x = 5; x < w-5; x ++)
            {
                int e_best = INT_MAX;
                int d_best = d0;
                int dmin = 0;
                int dmax = COUNT(edge_directions)-1;
                int search_area = 5;

                /* only use high accuracy on the dark exposure where the bright ISO is overexposed */
                if (!BRIGHT_ROW)
                {
                    /* interpolating bright exposure */
                    if (fullres_curve[raw_get_pixel32(x, y)] > fullres_thr)
                    {
#pragma omp atomic
                        /* no high accuracy needed, just interpolate vertically */
                        not_shadow++;
                        dmin = d0;
                        dmax = d0;
                    }
                    else
                    {
#pragma omp atomic
                        /* deep shadows, unlikely to use fullres, so we need a good interpolation */
                        deep_shadow++;
                    }
                }
                else if (raw_get_pixel32(x, y) < (unsigned int)white_darkened)
                {
#pragma omp atomic
                    /* interpolating dark exposure, but we also have good data from the bright one */
                    not_overexposed++;
                    dmin = d0;
                    dmax = d0;
                }
                else
                {
#pragma omp atomic
                    /* interpolating dark exposure, but the bright one is clipped */
                    semi_overexposed++;
                }

                if (dmin == dmax)
                {
                    d_best = dmin;
                }
                else
                {
                    for (int d = dmin; d <= dmax; d++)
                    {
                        int e = 0;
                        for (int j = -search_area; j <= search_area; j++)
                        {
                            int dx1 = edge_directions[d].ack.x + j;
                            int dy1 = edge_directions[d].ack.y * s;
                            int p1 = raw2ev[gray[x+dx1 + (y+dy1)*w]];
                            int dx2 = edge_directions[d].a.x + j;
                            int dy2 = edge_directions[d].a.y * s;
                            int p2 = raw2ev[gray[x+dx2 + (y+dy2)*w]];
                            int dx3 = edge_directions[d].b.x + j;
                            int dy3 = edge_directions[d].b.y * s;
                            int p3 = raw2ev[gray[x+dx3 + (y+dy3)*w]];
                            int dx4 = edge_directions[d].bck.x + j;
                            int dy4 = edge_directions[d].bck.y * s;
                            int p4 = raw2ev[gray[x+dx4 + (y+dy4)*w]];
                            e += ABS(p1-p2) + ABS(p2-p3) + ABS(p3-p4);
                        }

                        /* add a small penalty for diagonal directions */
                        /* (the improvement should be significant in order to choose one of these) */
                        e += ABS(d - d0) * EV_RESOLUTION/8;

                        if (e < e_best)
                        {
                            e_best = e;
                            d_best = d;
                        }
                    }
                }

                edge_direction[x + y*w] = d_best;
            }
        }
        }
#ifndef STDOUT_SILENT
        printf("Semi-overexposed: %.02f%%\n", semi_overexposed * 100.0 / (semi_overexposed + not_overexposed));
        printf("Deep shadows    : %.02f%%\n", deep_shadow * 100.0 / (deep_shadow + not_shadow));
#endif
        //~ printf("Actual interpolation...\n");
        
        #pragma omp parallel for
        for (int y = 2; y < h-2; y ++)
        {
            uint32_t* native = BRIGHT_ROW ? bright : dark;
            uint32_t* interp = BRIGHT_ROW ? dark : bright;
            int is_rg = (y % 2 == 0); /* RG or GB? */
            int s = (is_bright[y%4] == is_bright[(y+1)%4]) ? -1 : 1;    /* points to the closest row having different exposure */
            
            //~ printf("Interpolating %s line %d from [near] %d (squeezed %d) and [far] %d (squeezed %d)\n", BRIGHT_ROW ? "BRIGHT" : "DARK", y, y+s, yh_near, y-2*s, yh_far);
            
            for (int x = 2; x < w-2; x += 2)
            {
                for (int k = 0; k < 2; k++, x++)
                {
                    float** plane = is_rg ? (x%2 == 0 ? red   : green)
                    : (x%2 == 0 ? green : blue );
                    
                    int dir = edge_direction[x + y*w];
                    
                    /* vary the interpolation direction and average the result (reduces aliasing) */
                    int pi0 = edge_interp(plane, squeezed, raw2ev, dir, x, y, s);
                    int pip = edge_interp(plane, squeezed, raw2ev, MIN(dir+1, COUNT(edge_directions)-1), x, y, s);
                    int pim = edge_interp(plane, squeezed, raw2ev, MAX(dir-1,0), x, y, s);
                    
                    interp[x   + y * w] = ev2raw[(2*pi0+pip+pim)/4];
                    native[x   + y * w] = raw_get_pixel32(x, y);
                }
                x -= 2;
            }
        }
    }
    UNLOCK(ev2raw_mutex)
    
}

static inline void mean23_interpolate(struct raw_info raw_info, uint32_t * raw_buffer_32, uint32_t* dark, uint32_t* bright, int black, int white, int white_darkened, int * is_bright)
{
    int w = raw_info.width;
    int h = raw_info.height;
#ifndef STDOUT_SILENT
    printf("Interpolation   : mean23\n");
#endif
    /* for fast EV - raw conversion */
    static int raw2ev[1<<20];   /* EV x EV_RESOLUTION */
    static int ev2raw_0[24*EV_RESOLUTION];
    static int previous_black = -1;
    
    /* handle sub-black values (negative EV) */
    int* ev2raw = ev2raw_0 + 10*EV_RESOLUTION;
    
    LOCK(ev2raw_mutex)
    {
        if(black != previous_black)
        {
            build_ev2raw_lut(raw2ev, ev2raw_0, black, white);
            previous_black = black;
        }
        #pragma omp parallel for
        for (int y = 2; y < h-2; y ++)
        {
            uint32_t* native = BRIGHT_ROW ? bright : dark;
            uint32_t* interp = BRIGHT_ROW ? dark : bright;
            int is_rg = (y % 2 == 0); /* RG or GB? */
            int white = !BRIGHT_ROW ? white_darkened : raw_info.white_level;
            
            for (int x = 2; x < w-3; x += 2)
            {
                
                /* red/blue: interpolate from (x,y+2) and (x,y-2) */
                /* green: interpolate from (x+1,y+1),(x-1,y+1),(x,y-2) or (x+1,y-1),(x-1,y-1),(x,y+2), whichever has the correct brightness */
                
                int s = (is_bright[y%4] == is_bright[(y+1)%4]) ? -1 : 1;
                
                if (is_rg)
                {
                    int ra = raw_get_pixel32(x, y-2);
                    int rb = raw_get_pixel32(x, y+2);
                    int ri = mean2(raw2ev[ra], raw2ev[rb], raw2ev[white], 0);
                    
                    int ga = raw_get_pixel32(x+1+1, y+s);
                    int gb = raw_get_pixel32(x+1-1, y+s);
                    int gc = raw_get_pixel32(x+1, y-2*s);
                    int gi = mean3(raw2ev[ga], raw2ev[gb], raw2ev[gc], raw2ev[white], 0);
                    
                    interp[x   + y * w] = ev2raw[ri];
                    interp[x+1 + y * w] = ev2raw[gi];
                }
                else
                {
                    int ba = raw_get_pixel32(x+1  , y-2);
                    int bb = raw_get_pixel32(x+1  , y+2);
                    int bi = mean2(raw2ev[ba], raw2ev[bb], raw2ev[white], 0);
                    
                    int ga = raw_get_pixel32(x+1, y+s);
                    int gb = raw_get_pixel32(x-1, y+s);
                    int gc = raw_get_pixel32(x, y-2*s);
                    int gi = mean3(raw2ev[ga], raw2ev[gb], raw2ev[gc], raw2ev[white], 0);
                    
                    interp[x   + y * w] = ev2raw[gi];
                    interp[x+1 + y * w] = ev2raw[bi];
                }
                
                native[x   + y * w] = raw_get_pixel32(x, y);
                native[x+1 + y * w] = raw_get_pixel32(x+1, y);
            }
        }
    }
    UNLOCK(ev2raw_mutex)
}

static inline void border_interpolate(struct raw_info raw_info, uint32_t * raw_buffer_32, uint32_t* dark, uint32_t* bright, int * is_bright)
{
    int w = raw_info.width;
    int h = raw_info.height;
    
    /* border interpolation */
    for (int y = 0; y < 3; y ++)
    {
        uint32_t* native = BRIGHT_ROW ? bright : dark;
        uint32_t* interp = BRIGHT_ROW ? dark : bright;
        
        for (int x = 0; x < w; x ++)
        {
            interp[x + y * w] = raw_get_pixel32(x, y+2);
            native[x + y * w] = raw_get_pixel32(x, y);
        }
    }
    
    for (int y = h-4; y < h; y ++)
    {
        uint32_t* native = BRIGHT_ROW ? bright : dark;
        uint32_t* interp = BRIGHT_ROW ? dark : bright;
        
        for (int x = 0; x < w; x ++)
        {
            interp[x + y * w] = raw_get_pixel32(x, y-2);
            native[x + y * w] = raw_get_pixel32(x, y);
        }
    }
    
    for (int y = 2; y < h; y ++)
    {
        uint32_t* native = BRIGHT_ROW ? bright : dark;
        uint32_t* interp = BRIGHT_ROW ? dark : bright;
        
        for (int x = 0; x < 2; x ++)
        {
            interp[x + y * w] = raw_get_pixel32(x, y-2);
            native[x + y * w] = raw_get_pixel32(x, y);
        }
        
        for (int x = w-3; x < w; x ++)
        {
            interp[x + y * w] = raw_get_pixel32(x-2, y-2);
            native[x + y * w] = raw_get_pixel32(x-2, y);
        }
    }
}

static inline void fullres_reconstruction(struct raw_info raw_info, uint32_t * fullres, uint32_t* dark, uint32_t* bright, uint32_t white_darkened, int * is_bright)
{
    int w = raw_info.width;
    int h = raw_info.height;

    /* reconstruct a full-resolution image (discard interpolated fields whenever possible) */
    /* this has full detail and lowest possible aliasing, but it has high shadow noise and color artifacts when high-iso starts clipping */
#ifndef STDOUT_SILENT
    printf("Full-res reconstruction...\n");
#endif

#ifdef DUALISO_AVX2_AVAILABLE
    pthread_once(&g_dualiso_hq_dispatch_once, dualiso_hq_dispatch_init);
    if (g_dualiso_hq_use_avx2)
    {
        /* Hoist BRIGHT_ROW out of the inner loop: row-uniform branch. */
        #pragma omp parallel for
        for (int y = 0; y < h; y ++) {
            if (BRIGHT_ROW) {
                fullres_reconstruction_bright_row_avx2(&fullres[(size_t)y*w],
                                                       &bright[(size_t)y*w],
                                                       &dark[(size_t)y*w],
                                                       white_darkened, w);
            } else {
                memcpy(&fullres[(size_t)y*w], &dark[(size_t)y*w], (size_t)w * sizeof(uint32_t));
            }
        }
        return;
    }
#endif

    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y ++)
    {
        for (int x = 0; x < w; x ++)
        {
            if (BRIGHT_ROW)
            {
                uint32_t f = bright[x + y*w];
                /* if the brighter copy is overexposed, the guessed pixel for sure has higher brightness */
                fullres[x + y*w] = f < white_darkened ? f : MAX(f, dark[x + y*w]);
            }
            else
            {
                fullres[x + y*w] = dark[x + y*w];
            }
        }
    }
}

static inline void build_alias_map(struct raw_info raw_info,
                                   uint16_t* alias_map,
                                   uint32_t* fullres_smooth,
                                   uint32_t* halfres_smooth,
                                   uint32_t* bright,
                                   int dark_noise,
                                   int black,
                                   int * raw2ev,
                                   dualiso_full20bit_scratch_t * scratch)
{
    if(!alias_map) return;
    
    int w = raw_info.width;
    int h = raw_info.height;
    
    double * fullres_curve = build_fullres_curve(black);
#ifndef STDOUT_SILENT
    printf("Building alias map...\n");
#endif
    uint16_t* alias_aux = ensure_alias_aux_scratch(scratch, (size_t)w * (size_t)h);
    if (!alias_aux)
    {
        return;
    }
    
    /* build the aliasing maps (where it's likely to get aliasing) */
    /* do this by comparing fullres and halfres images */
    /* if the difference is small, we'll prefer halfres for less noise, otherwise fullres for less aliasing */
#ifdef DUALISO_AVX2_AVAILABLE
    pthread_once(&g_dualiso_alias_dispatch_once, dualiso_alias_dispatch_init);
    if (g_dualiso_alias_use_avx2)
    {
        #pragma omp parallel for
        for (int y = 0; y < h; y ++)
        {
            build_alias_map_init_diff_row_avx2(&alias_map[(size_t)y * (size_t)w],
                                                &fullres_smooth[(size_t)y * (size_t)w],
                                                &halfres_smooth[(size_t)y * (size_t)w],
                                                &bright[(size_t)y * (size_t)w],
                                                raw2ev,
                                                fullres_curve,
                                                fullres_thr,
                                                dark_noise,
                                                w);
        }
    }
    else
#endif
    {
        #pragma omp parallel for collapse(2)
        for (int y = 0; y < h; y ++)
        {
            for (int x = 0; x < w; x ++)
            {
                /* do not compute alias map where we'll use fullres detail anyway */
                if (fullres_curve[bright[x + y*w]] > fullres_thr)
                    continue;

                int f = fullres_smooth[x + y*w];
                int h = halfres_smooth[x + y*w];
                int fe = raw2ev[f];
                int he = raw2ev[h];
                int e_lin = ABS(f - h); /* error in linear space, for shadows (downweights noise) */
                e_lin = MAX(e_lin - dark_noise*3/2, 0);
                int e_log = ABS(fe - he); /* error in EV space, for highlights (highly sensitive to noise) */
                alias_map[x + y*w] = MIN(MIN(e_lin/2, e_log/16), 65530);
            }
        }
    }

    memcpy(alias_aux, alias_map, w * h * sizeof(uint16_t));
#ifndef STDOUT_SILENT
    printf("Filtering alias map...\n");
#endif
    #pragma omp parallel for collapse(2)
    for (int y = 6; y < h-6; y ++)
    {
        for (int x = 6; x < w-6; x ++)
        {
            /* do not compute alias map where we'll use fullres detail anyway */
            if (fullres_curve[bright[x + y*w]] > fullres_thr)
                continue;
            
            /* use 5th max (out of 37) to filter isolated pixels */
            int neighbours[] = {
                                                                              -alias_map[x-2 + (y-6) * w], -alias_map[x+0 + (y-6) * w], -alias_map[x+2 + (y-6) * w],
                                                 -alias_map[x-4 + (y-4) * w], -alias_map[x-2 + (y-4) * w], -alias_map[x+0 + (y-4) * w], -alias_map[x+2 + (y-4) * w], -alias_map[x+4 + (y-4) * w],
                    -alias_map[x-6 + (y-2) * w], -alias_map[x-4 + (y-2) * w], -alias_map[x-2 + (y-2) * w], -alias_map[x+0 + (y-2) * w], -alias_map[x+2 + (y-2) * w], -alias_map[x+4 + (y-2) * w], -alias_map[x+6 + (y-2) * w], 
                    -alias_map[x-6 + (y+0) * w], -alias_map[x-4 + (y+0) * w], -alias_map[x-2 + (y+0) * w], -alias_map[x+0 + (y+0) * w], -alias_map[x+2 + (y+0) * w], -alias_map[x+4 + (y+0) * w], -alias_map[x+6 + (y+0) * w], 
                    -alias_map[x-6 + (y+2) * w], -alias_map[x-4 + (y+2) * w], -alias_map[x-2 + (y+2) * w], -alias_map[x+0 + (y+2) * w], -alias_map[x+2 + (y+2) * w], -alias_map[x+4 + (y+2) * w], -alias_map[x+6 + (y+2) * w], 
                                                 -alias_map[x-4 + (y+4) * w], -alias_map[x-2 + (y+4) * w], -alias_map[x+0 + (y+4) * w], -alias_map[x+2 + (y+4) * w], -alias_map[x+4 + (y+4) * w],
                                                                              -alias_map[x-2 + (y+6) * w], -alias_map[x+0 + (y+6) * w], -alias_map[x+2 + (y+6) * w],
            };
            alias_aux[x + y * w] = -kth_smallest_int(neighbours, COUNT(neighbours), 5);
        }
    }
#ifndef STDOUT_SILENT
    printf("Smoothing alias map...\n");
#endif
    /* gaussian blur */
#ifdef DUALISO_AVX2_AVAILABLE
    pthread_once(&g_dualiso_alias_dispatch_once, dualiso_alias_dispatch_init);
    if (g_dualiso_alias_use_avx2)
    {
        #pragma omp parallel for
        for (int y = 6; y < h-6; y ++)
        {
            build_alias_map_gaussian_row_avx2(&alias_map[(size_t)y * (size_t)w],
                                               alias_aux,
                                               y, w,
                                               bright, fullres_curve, fullres_thr);
        }
    }
    else
#endif
    {
        #pragma omp parallel for collapse(2)
        for (int y = 6; y < h-6; y ++)
        {
            for (int x = 6; x < w-6; x ++)
            {
                /* do not compute alias map where we'll use fullres detail anyway */
                if (fullres_curve[bright[x + y*w]] > fullres_thr)
                    continue;

                int c =
                (alias_aux[x+0 + (y+0) * w])+
                (alias_aux[x+0 + (y-2) * w] + alias_aux[x-2 + (y+0) * w] + alias_aux[x+2 + (y+0) * w] + alias_aux[x+0 + (y+2) * w]) * 820 / 1024 +
                (alias_aux[x-2 + (y-2) * w] + alias_aux[x+2 + (y-2) * w] + alias_aux[x-2 + (y+2) * w] + alias_aux[x+2 + (y+2) * w]) * 657 / 1024 +
                (alias_aux[x+0 + (y-2) * w] + alias_aux[x-2 + (y+0) * w] + alias_aux[x+2 + (y+0) * w] + alias_aux[x+0 + (y+2) * w]) * 421 / 1024 +
                (alias_aux[x-2 + (y-2) * w] + alias_aux[x+2 + (y-2) * w] + alias_aux[x-2 + (y-2) * w] + alias_aux[x+2 + (y-2) * w] + alias_aux[x-2 + (y+2) * w] + alias_aux[x+2 + (y+2) * w] + alias_aux[x-2 + (y+2) * w] + alias_aux[x+2 + (y+2) * w]) * 337 / 1024 +
                (alias_aux[x-2 + (y-2) * w] + alias_aux[x+2 + (y-2) * w] + alias_aux[x-2 + (y+2) * w] + alias_aux[x+2 + (y+2) * w]) * 173 / 1024 +
                (alias_aux[x+0 + (y-6) * w] + alias_aux[x-6 + (y+0) * w] + alias_aux[x+6 + (y+0) * w] + alias_aux[x+0 + (y+6) * w]) * 139 / 1024 +
                (alias_aux[x-2 + (y-6) * w] + alias_aux[x+2 + (y-6) * w] + alias_aux[x-6 + (y-2) * w] + alias_aux[x+6 + (y-2) * w] + alias_aux[x-6 + (y+2) * w] + alias_aux[x+6 + (y+2) * w] + alias_aux[x-2 + (y+6) * w] + alias_aux[x+2 + (y+6) * w]) * 111 / 1024 +
                (alias_aux[x-2 + (y-6) * w] + alias_aux[x+2 + (y-6) * w] + alias_aux[x-6 + (y-2) * w] + alias_aux[x+6 + (y-2) * w] + alias_aux[x-6 + (y+2) * w] + alias_aux[x+6 + (y+2) * w] + alias_aux[x-2 + (y+6) * w] + alias_aux[x+2 + (y+6) * w]) * 57 / 1024;
                alias_map[x + y * w] = c;
            }
        }
    }
    
    /* make it grayscale */
    #pragma omp parallel for collapse(2)
    for (int y = 2; y < h-2; y += 2)
    {
        for (int x = 2; x < w-2; x += 2)
        {
            int a = alias_map[x   +     y * w];
            int b = alias_map[x+1 +     y * w];
            int c = alias_map[x   + (y+1) * w];
            int d = alias_map[x+1 + (y+1) * w];
            int C = MAX(MAX(a,b), MAX(c,d));
            
            C = MIN(C, ALIAS_MAP_MAX);
            
            alias_map[x   +     y * w] =
            alias_map[x+1 +     y * w] =
            alias_map[x   + (y+1) * w] =
            alias_map[x+1 + (y+1) * w] = C;
        }
    }
    
}

#define CHROMA_SMOOTH_TYPE uint32_t

#define CHROMA_SMOOTH_2X2
#include "chroma_smooth.c"
#undef CHROMA_SMOOTH_2X2

#define CHROMA_SMOOTH_3X3
#include "chroma_smooth.c"
#undef CHROMA_SMOOTH_3X3

#define CHROMA_SMOOTH_5X5
#include "chroma_smooth.c"
#undef CHROMA_SMOOTH_5X5

static inline void hdr_chroma_smooth(struct raw_info raw_info, uint32_t * input, uint32_t * output, int method, int * raw2ev, int * ev2raw)
{
    int w = raw_info.width;
    int h = raw_info.height;
    int black = raw_info.black_level;
    int white = raw_info.white_level;
    
    switch (method) {
        case 2:
            chroma_smooth_2x2(w, h, input, output, raw2ev, ev2raw, black, white);
            break;
        case 3:
            chroma_smooth_3x3(w, h, input, output, raw2ev, ev2raw, black, white);
            break;
        case 5:
            chroma_smooth_5x5(w, h, input, output, raw2ev, ev2raw, black, white);
            break;
            
        default:
#ifndef STDOUT_SILENT
            err_printf("Unsupported chroma smooth method\n");
#endif
            break;
    }
}

static inline int mix_images(struct raw_info raw_info, uint32_t* fullres, uint32_t* fullres_smooth, uint32_t* halfres, uint32_t* halfres_smooth, uint16_t* alias_map, uint32_t* dark, uint32_t* bright, uint16_t * overexposed, int dark_noise, uint32_t white_darkened, double corr_ev, double lowiso_dr, uint32_t black, uint32_t white, int chroma_smooth_method, dualiso_full20bit_scratch_t * scratch)
{
    int w = raw_info.width;
    int h = raw_info.height;
    
    /* mix the two images */
    /* highlights:  keep data from dark image only */
    /* shadows:     keep data from bright image only */
    /* midtones:    mix data from both, to bring back the resolution */
    
    /* estimate ISO overlap */
    /*
     ISO 100:       ###...........  (11 stops)
     ISO 1600:  ####..........      (10 stops)
     Combined:  XX##..............  (14 stops)
     */
    double clipped_ev = corr_ev;
    double overlap = lowiso_dr - clipped_ev;
    
    /* you get better colors, less noise, but a little more jagged edges if we underestimate the overlap amount */
    /* maybe expose a tuning factor? (preference towards resolution or colors) */
    overlap -= MIN(3, overlap - 3);
#ifndef STDOUT_SILENT
    printf("ISO overlap     : %.1f EV (approx)\n", overlap);
#endif
    if (overlap < 0.5)
    {
#ifndef STDOUT_SILENT
        printf("Overlap error\n");
#endif
        return 0;
    }
    else if (overlap < 2)
    {
#ifndef STDOUT_SILENT
        printf("Overlap too small, use a smaller ISO difference for better results.\n");
#endif
    }
#ifndef STDOUT_SILENT
    printf("Half-res blending...\n");
#endif
    /* mixing curve */
    double max_ev = log2(white/64 - black/64);
    double * mix_curve = scratch
        ? ensure_double_scratch_buffer(&scratch->mix_curve, &scratch->mix_curve_capacity, (1u << 20))
        : NULL;
    if (!mix_curve)
    {
        return 0;
    }
    
    #pragma omp parallel for
    for (int i = 0; i < 1<<20; i++)
    {
        double ev = log2(MAX(i/64.0 - black/64.0, 1)) + corr_ev;
        double c = -cos(MAX(MIN(ev-(max_ev-overlap),overlap),0)*M_PI/overlap);
        double k = (c+1) / 2;
        mix_curve[i] = k;
    }


    /* for fast EV - raw conversion */
    static int raw2ev[1<<20];   /* EV x EV_RESOLUTION */
    static int ev2raw_0[24*EV_RESOLUTION];
    static uint32_t previous_black = -1;
    
    /* handle sub-black values (negative EV) */
    int* ev2raw = ev2raw_0 + 10*EV_RESOLUTION;
    
    LOCK(ev2raw_mutex)
    {
        if(black != previous_black)
        {
            build_ev2raw_lut(raw2ev, ev2raw_0, black, white);
            previous_black = black;
        }
        
#ifdef DUALISO_AVX2_AVAILABLE
        pthread_once(&g_dualiso_hq_dispatch_once, dualiso_hq_dispatch_init);
        if (g_dualiso_hq_use_avx2)
        {
            #pragma omp parallel for
            for (int y = 0; y < h; y ++) {
                mix_images_row_avx2(&halfres[(size_t)y*w],
                                    &bright[(size_t)y*w],
                                    &dark[(size_t)y*w],
                                    raw2ev, ev2raw, mix_curve, w);
                /* tail: pixels not covered by SIMD bulk */
                int x_start = (w / 8) * 8;
                for (int x = x_start; x < w; x ++) {
                    int b = bright[x + y*w];
                    int d = dark[x + y*w];
                    int bev = raw2ev[b];
                    int dev = raw2ev[d];
                    double k = COERCE(mix_curve[b & 0xFFFFF], 0, 1);
                    int mixed = bev * (1-k) + dev * k;
                    halfres[x + y*w] = ev2raw[mixed];
                }
            }
        }
        else
#endif
        {
            #pragma omp parallel for collapse(2)
            for (int y = 0; y < h; y ++)
            {
                for (int x = 0; x < w; x ++)
                {
                    /* bright and dark source pixels  */
                    /* they may be real or interpolated */
                    /* they both have the same brightness (they were adjusted before this loop), so we are ready to mix them */
                    int b = bright[x + y*w];
                    int d = dark[x + y*w];

                    /* go from linear to EV space */
                    int bev = raw2ev[b];
                    int dev = raw2ev[d];

                    /* blending factor */
                    double k = COERCE(mix_curve[b & 0xFFFFF], 0, 1);

                    /* mix bright and dark exposures */
                    int mixed = bev * (1-k) + dev * k;
                    halfres[x + y*w] = ev2raw[mixed];
                }
            }
        }
        if (chroma_smooth_method)
        {
#ifndef STDOUT_SILENT
            printf("Chroma smoothing...\n");
#endif
            memcpy(fullres_smooth, fullres, w * h * sizeof(uint32_t));
            memcpy(halfres_smooth, halfres, w * h * sizeof(uint32_t));
            hdr_chroma_smooth(raw_info, fullres, fullres_smooth, chroma_smooth_method, raw2ev, ev2raw);
            hdr_chroma_smooth(raw_info, halfres, halfres_smooth, chroma_smooth_method, raw2ev, ev2raw);
        }
        if(alias_map)
        {
            build_alias_map(raw_info, alias_map, fullres_smooth, halfres_smooth, bright, dark_noise, black, raw2ev, scratch);
        }
    }
    UNLOCK(ev2raw_mutex)
    
    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y ++)
    {
        for (int x = 0; x < w; x ++)
        {
            overexposed[x + y * w] = bright[x + y * w] >= white_darkened || dark[x + y * w] >= white ? 100 : 0;
        }
    }
    
    /* "blur" the overexposed map */
    uint16_t* over_aux = scratch ? scratch->over_aux : NULL;
    if (!over_aux)
    {
        return 0;
    }
    memcpy(over_aux, overexposed, w * h * sizeof(uint16_t));
    
    #pragma omp parallel for collapse(2)
    for (int y = 3; y < h-3; y ++)
    {
        for (int x = 3; x < w-3; x ++)
        {
            overexposed[x + y * w] =
            (over_aux[x+0 + (y+0) * w])+
            (over_aux[x+0 + (y-1) * w] + over_aux[x-1 + (y+0) * w] + over_aux[x+1 + (y+0) * w] + over_aux[x+0 + (y+1) * w]) * 820 / 1024 +
            (over_aux[x-1 + (y-1) * w] + over_aux[x+1 + (y-1) * w] + over_aux[x-1 + (y+1) * w] + over_aux[x+1 + (y+1) * w]) * 657 / 1024 +
            //~ (over_aux[x+0 + (y-2) * w] + over_aux[x-2 + (y+0) * w] + over_aux[x+2 + (y+0) * w] + over_aux[x+0 + (y+2) * w]) * 421 / 1024 +
            //~ (over_aux[x-1 + (y-2) * w] + over_aux[x+1 + (y-2) * w] + over_aux[x-2 + (y-1) * w] + over_aux[x+2 + (y-1) * w] + over_aux[x-2 + (y+1) * w] + over_aux[x+2 + (y+1) * w] + over_aux[x-1 + (y+2) * w] + over_aux[x+1 + (y+2) * w]) * 337 / 1024 +
            //~ (over_aux[x-2 + (y-2) * w] + over_aux[x+2 + (y-2) * w] + over_aux[x-2 + (y+2) * w] + over_aux[x+2 + (y+2) * w]) * 173 / 1024 +
            //~ (over_aux[x+0 + (y-3) * w] + over_aux[x-3 + (y+0) * w] + over_aux[x+3 + (y+0) * w] + over_aux[x+0 + (y+3) * w]) * 139 / 1024 +
            //~ (over_aux[x-1 + (y-3) * w] + over_aux[x+1 + (y-3) * w] + over_aux[x-3 + (y-1) * w] + over_aux[x+3 + (y-1) * w] + over_aux[x-3 + (y+1) * w] + over_aux[x+3 + (y+1) * w] + over_aux[x-1 + (y+3) * w] + over_aux[x+1 + (y+3) * w]) * 111 / 1024 +
            //~ (over_aux[x-2 + (y-3) * w] + over_aux[x+2 + (y-3) * w] + over_aux[x-3 + (y-2) * w] + over_aux[x+3 + (y-2) * w] + over_aux[x-3 + (y+2) * w] + over_aux[x+3 + (y+2) * w] + over_aux[x-2 + (y+3) * w] + over_aux[x+2 + (y+3) * w]) * 57 / 1024;
            0;
        }
    }
    
    return 1;
}

static inline void final_blend(struct raw_info raw_info, uint32_t* raw_buffer_32, uint32_t* fullres, uint32_t* fullres_smooth, uint32_t* halfres_smooth, uint32_t* dark, uint32_t* bright, uint16_t* overexposed, uint16_t* alias_map, int black, int white, int dark_noise)
{
    /* fullres mixing curve */
    double * fullres_curve = build_fullres_curve(black);
    
    int w = raw_info.width;
    int h = raw_info.height;
    
    /* for fast EV - raw conversion */
    static int raw2ev[1<<20];   /* EV x EV_RESOLUTION */
    static int ev2raw_0[24*EV_RESOLUTION];
    static int previous_black = -1;
    
    /* handle sub-black values (negative EV) */
    int* ev2raw = ev2raw_0 + 10*EV_RESOLUTION;
    
    LOCK(ev2raw_mutex)
    {
        if(black != previous_black)
        {
            build_ev2raw_lut(raw2ev, ev2raw_0, black, white);
            previous_black = black;
        }
#ifndef STDOUT_SILENT
        printf("Final blending...\n");
#endif

#ifdef DUALISO_AVX2_AVAILABLE
        pthread_once(&g_dualiso_hq_dispatch_once, dualiso_hq_dispatch_init);
        if (g_dualiso_hq_use_avx2)
        {
            #pragma omp parallel for
            for (int y = 0; y < h; y ++) {
                final_blend_row_avx2(&raw_buffer_32[(size_t)y*w],
                                     &fullres[(size_t)y*w],
                                     &fullres_smooth[(size_t)y*w],
                                     &halfres_smooth[(size_t)y*w],
                                     &dark[(size_t)y*w],
                                     &bright[(size_t)y*w],
                                     &overexposed[(size_t)y*w],
                                     alias_map ? &alias_map[(size_t)y*w] : NULL,
                                     raw2ev, ev2raw, fullres_curve,
                                     black, dark_noise, w);
                /* tail: pixels not covered by SIMD bulk */
                int x_start = (w / 8) * 8;
                for (int x = x_start; x < w; x ++) {
                    int b = bright[x + y*w];
                    int hr = halfres_smooth[x + y*w];
                    int fr = fullres[x + y*w];
                    int frs = fullres_smooth[x + y*w];
                    int hrev = raw2ev[hr];
                    int frev = raw2ev[fr];
                    int frsev = raw2ev[frs];
                    double f = fullres_curve[b & 0xFFFFF];
                    double c = 0;
                    if (alias_map) {
                        int co = alias_map[x + y*w];
                        c = COERCE(co / (double) ALIAS_MAP_MAX, 0, 1);
                    }
                    double ovf = COERCE(overexposed[x + y*w] / 200.0, 0, 1);
                    c = MAX(c, ovf);
                    double noisy_or_overexposed = MAX(ovf, 1-f);
                    f = MAX(f, c);
                    double fev = noisy_or_overexposed * frsev + (1-noisy_or_overexposed) * frev;
                    int sig = (dark[x + y*w] + bright[x + y*w]) / 2;
                    f = MAX(0, MIN(f, (double)(sig - black) / (4*dark_noise)));
                    int output = hrev * (1-f) + fev * f;
                    output = COERCE(output, -10*EV_RESOLUTION, 14*EV_RESOLUTION-1);
                    raw_set_pixel32(x, y, ev2raw[output]);
                }
            }
        }
        else
#endif
        {
            #pragma omp parallel for collapse(2)
            for (int y = 0; y < h; y ++)
            {
                for (int x = 0; x < w; x ++)
                {
                    /* high-iso image (for measuring signal level) */
                    int b = bright[x + y*w];

                    /* half-res image (interpolated and chroma filtered, best for low-contrast shadows) */
                    int hr = halfres_smooth[x + y*w];

                    /* full-res image (non-interpolated, except where one ISO is blown out) */
                    int fr = fullres[x + y*w];

                    /* full res with some smoothing applied to hide aliasing artifacts */
                    int frs = fullres_smooth[x + y*w];

                    /* go from linear to EV space */
                    int hrev = raw2ev[hr];
                    int frev = raw2ev[fr];
                    int frsev = raw2ev[frs];

                    int output = 0;

                    /* blending factor */
                    double f = fullres_curve[b & 0xFFFFF];

                    double c = 0;

                    if (alias_map)
                    {
                        int co = alias_map[x + y*w];
                        c = COERCE(co / (double) ALIAS_MAP_MAX, 0, 1);
                    }

                    double ovf = COERCE(overexposed[x + y*w] / 200.0, 0, 1);
                    c = MAX(c, ovf);

                    double noisy_or_overexposed = MAX(ovf, 1-f);

                    /* use data from both ISOs in high-detail areas, even if it's noisier (less aliasing) */
                    f = MAX(f, c);

                    /* use smoothing in noisy near-overexposed areas to hide color artifacts */
                    double fev = noisy_or_overexposed * frsev + (1-noisy_or_overexposed) * frev;

                    /* limit the use of fullres in dark areas (fixes some black spots, but may increase aliasing) */
                    int sig = (dark[x + y*w] + bright[x + y*w]) / 2;
                    f = MAX(0, MIN(f, (double)(sig - black) / (4*dark_noise)));

                    /* blend "half-res" and "full-res" images smoothly to avoid banding*/
                    output = hrev * (1-f) + fev * f;

                    /* show full-res map (for debugging) */
                    //~ output = f * 14*EV_RESOLUTION;

                    /* show alias map (for debugging) */
                    //~ output = c * 14*EV_RESOLUTION;

                    //~ output = hotpixel[x+y*w] ? 14*EV_RESOLUTION : 0;
                    //~ output = raw2ev[dark[x+y*w]];
                    /* safeguard */
                    output = COERCE(output, -10*EV_RESOLUTION, 14*EV_RESOLUTION-1);


                    /* back to linear space and commit */
                    raw_set_pixel32(x, y, ev2raw[output]);
                }
            }
        }
    }
    UNLOCK(ev2raw_mutex)
}

static inline void convert_20_to_16bit(struct raw_info raw_info, uint16_t * image_data, uint32_t * raw_buffer_32)
{
    int w = raw_info.width;
    int h = raw_info.height;
    /* go back from 20-bit to 16-bit output */
    //raw_info.buffer = raw_buffer_16;
    raw_info.black_level /= 16;
    raw_info.white_level /= 16;

#ifdef DUALISO_AVX2_AVAILABLE
    pthread_once(&g_dualiso_hq_dispatch_once, dualiso_hq_dispatch_init);
    if (g_dualiso_hq_use_avx2)
    {
        /* Per-row dispatch with thread-local dither cursor. The scalar
         * fast_randn05() uses a process-wide static counter; here we use
         * a per-row seed so the noise distribution and amplitude are
         * preserved while threading cleanly. The cache itself is fixed,
         * so bit identity vs scalar is not preserved (the scalar's global
         * counter ordering already depends on OMP scheduling) but the
         * statistical contract of the dither is unchanged. */
        #pragma omp parallel for
        for (int y = 0; y < h; y++) {
            int k = (y * 7) & 1023;  /* per-row cursor seed */
            convert_20_to_16bit_row_avx2(&image_data[(size_t)y*w],
                                          &raw_buffer_32[(size_t)y*w],
                                          randn05_cache, &k, w);
        }
        return;
    }
#endif

    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            raw_set_pixel_20to16_rand(x, y, raw_buffer_32[x + y*w]);
}

int diso_get_full20bit(struct raw_info raw_info, uint16_t * image_data, int dark_frame, int iso1, int iso2, int * iso_pattern, int * auto_correction, double * ev_correction, int * black_delta, int interp_method, int use_alias_map, int use_fullres, int chroma_smooth_method, int threads, dualiso_full20bit_scratch_t * scratch)
{
    int w = raw_info.width;
    int h = raw_info.height;
    
    if (w <= 0 || h <= 0) return 0;

    /* RGGB or GBRG? */
    //int rggb = identify_rggb_or_gbrg(raw_info, image_data, scratch);
    int rggb = ((raw_info.cfa_pattern == 0) || (raw_info.cfa_pattern == 0x02010100)) ? 1 : 0;

    if (!rggb) /* this code assumes RGGB, so we need to skip one line */
    {
        image_data += raw_info.pitch;
        raw_info.active_area.y1++;
        raw_info.active_area.y2--;
        raw_info.height--;
        h--;
    }
    
    const int iso_patterns[4][4] = {{1, 1, 0, 0}, {1, 0, 0, 1}, {0, 0, 1, 1}, {0, 1, 1, 0}};

    int is_bright[4];

    if (!*iso_pattern)
    {
        if (!identify_bright_and_dark_fields(raw_info, image_data, rggb, is_bright, scratch)) return 0;

        for (int i = 0; i < 4; i++)
        {
            if (memcmp(is_bright, iso_patterns[i], sizeof(is_bright)) == 0)
            {
                *iso_pattern = -(i + 1);
                break;
            }
        }

        if (!*iso_pattern) return 0;
    }
    else if (*iso_pattern > 0 && *iso_pattern <= 4)
    {
        memcpy(is_bright, iso_patterns[*iso_pattern - 1], sizeof(is_bright));
    }
    else if (*iso_pattern >= -4 && *iso_pattern <= -1)
    {
        /* Negative values in {-1..-4} are the "pattern already auto-discovered"
           encoding written by the !*iso_pattern branch above on a previous
           call. On forced llrawproc re-entry (e.g. resetMlvCachedFrame) the
           published shared->diso_pattern reseeds this function with the
           negative value, so we must reuse it instead of silently returning 0
           (which would leave raw_image_buff unprocessed while downstream still
           promoted dng_bit_depth to 16). This mirrors diso_get_preview's
           preview_pattern_index() convention of ABS(iso_pattern) at
           dualiso.c:46-48. See analysis: Nineteenth pass - 2026-04-21. */
        memcpy(is_bright, iso_patterns[(-*iso_pattern) - 1], sizeof(is_bright));
    }
    else if (*iso_pattern == 5)
    {
        if (!identify_bright_and_dark_fields(raw_info, image_data, rggb, is_bright, scratch))
        {
            memcpy(is_bright, iso_patterns[0], sizeof(is_bright));
        }
    }
    else
    {
        return 0;
    }
    
    int ret = 0;
    
    /* will use 20-bit processing and 16-bit output, instead of 14 */
    raw_info.black_level *= 64;
    raw_info.white_level *= 64;
    
    int black = raw_info.black_level;
    int white = raw_info.white_level / 64;
    
    int white_bright = white / 2;
    //white_detect(raw_info, image_data, &white, &white_bright, is_bright);
    white *= 64;
    white_bright *= 64;
    raw_info.white_level = white;
    
    double noise_std[4];
    double dark_noise, bright_noise, dark_noise_ev, bright_noise_ev;
    double noise_avg = compute_noise(raw_info, image_data, noise_std, &dark_noise, &bright_noise, &dark_noise_ev, &bright_noise_ev);

    if (!ensure_full20bit_pixel_capacity(scratch, (size_t)w * (size_t)h))
    {
        return 0;
    }
    
    /* promote from 14 to 20 bits (original raw buffer holds 14-bit values stored as uint16_t) */
    uint32_t * raw_buffer_32 = convert_to_20bit(raw_info, image_data, scratch->raw_buffer_32);
    if (!raw_buffer_32)
    {
        return 0;
    }
    
    /* we have now switched to 20-bit, update noise numbers */
    dark_noise *= 64;
    bright_noise *= 64;
    dark_noise_ev += 6;
    bright_noise_ev += 6;
    
    /* dark and bright exposures, interpolated */
    uint32_t* dark = scratch->dark;
    uint32_t* bright = scratch->bright;
    memset(dark, 0, w * h * sizeof(uint32_t));
    memset(bright, 0, w * h * sizeof(uint32_t));
    
    /* fullres image (minimizes aliasing) */
    uint32_t* fullres = scratch->fullres;
    memset(fullres, 0, w * h * sizeof(uint32_t));
    uint32_t* fullres_smooth = fullres;
    
    /* halfres image (minimizes noise and banding) */
    uint32_t* halfres = scratch->halfres;
    memset(halfres, 0, w * h * sizeof(uint32_t));
    uint32_t* halfres_smooth = halfres;
    
    if (chroma_smooth_method)
    {
        if (use_fullres)
        {
            fullres_smooth = scratch->fullres_smooth;
        }
        halfres_smooth = scratch->halfres_smooth;
    }
    
    /* overexposure map */
    uint16_t * overexposed = scratch->overexposed;
    memset(overexposed, 0, w * h * sizeof(uint16_t));
    
    uint16_t* alias_map = NULL;
    if (use_alias_map)
    {
        alias_map = scratch->alias_map;
        memset(alias_map, 0, w * h * sizeof(uint16_t));
    }
    
    //~ printf("Exposure matching...\n");
    /* estimate ISO difference between bright and dark exposures */
    int white_darkened = white_bright;
    int expo_matched = match_exposures(raw_info, raw_buffer_32, dark_frame, iso1, iso2, auto_correction, ev_correction, black_delta, &white_darkened, is_bright, scratch);
    double corr_ev = ABS(*ev_correction);

#ifndef STDOUT_SILENT
    if (expo_matched)
    {
        printf("Exposures matched");
    }
    else
    {
        printf("Exposures not matched");
    }
#endif
    /* estimate dynamic range */
    double lowiso_dr = log2(white - black) - dark_noise_ev;
#ifndef STDOUT_SILENT
    double highiso_dr = log2(white_bright - black) - bright_noise_ev;
    printf("Dynamic range   : %.02f (+) %.02f => %.02f EV (in theory)\n", lowiso_dr, highiso_dr, highiso_dr + corr_ev);
#endif
    /* correction factor for the bright exposure, which was just darkened */
    //double factor = pow(2, corr_ev);

    /* update bright noise measurements, so they can be compared after scaling */
    //bright_noise /= factor;
    //bright_noise_ev -= corr_ev;

    if (interp_method == 0)
    {
        dualiso_debug_note_hq_path(0);
        amaze_interpolate(raw_info, raw_buffer_32, dark, bright, black, white, white_darkened, is_bright, threads, scratch);
    }
    else
    {
        dualiso_debug_note_hq_path(1);
        mean23_interpolate(raw_info, raw_buffer_32, dark, bright, black, white, white_darkened, is_bright);
    }

    border_interpolate(raw_info, raw_buffer_32, dark, bright, is_bright);

    if (use_fullres) fullres_reconstruction(raw_info, fullres, dark, bright, white_darkened, is_bright);

    if (mix_images(raw_info, fullres, fullres_smooth, halfres, halfres_smooth, alias_map, dark, bright, overexposed, dark_noise, white_darkened, corr_ev, lowiso_dr, black, white, chroma_smooth_method, scratch))
    {
        /* let's check the ideal noise levels (on the halfres image, which in black areas is identical to the bright one) */
#ifndef STDOUT_SILENT
        //#pragma omp parallel for collapse(2)
        for (int y = 3; y < h-2; y ++)
            for (int x = 2; x < w-2; x ++)
                raw_set_pixel32(x, y, bright[x + y*w]);
        
        compute_black_noise(raw_info, image_data, 8, raw_info.active_area.x1 - 8, raw_info.active_area.y1 + 20, raw_info.active_area.y2 - 20, 1, 1, &noise_avg, &noise_std[0]);
        double ideal_noise_std = noise_std[0];
#endif
        final_blend(raw_info, raw_buffer_32, fullres, fullres_smooth, halfres_smooth, dark, bright, overexposed, alias_map, black, white, dark_noise);

        /* let's see how much dynamic range we actually got */
#ifndef STDOUT_SILENT
        compute_black_noise(raw_info, image_data, 8, raw_info.active_area.x1 - 8, raw_info.active_area.y1 + 20, raw_info.active_area.y2 - 20, 1, 1, &noise_avg, &noise_std[0]);
        printf("Noise level     : %.02f (20-bit), ideally %.02f\n", noise_std[0], ideal_noise_std);
        printf("Dynamic range   : %.02f EV (cooked)\n", log2(white - black) - log2(noise_std[0]));
#endif
        convert_20_to_16bit(raw_info, image_data, raw_buffer_32);
        ret = 1;
    }
    
    if (!rggb) /* back to GBRG */
    {
        raw_info.active_area.y1--;
        raw_info.active_area.y2++;
        raw_info.height++;
        h++;
    }
    
    return ret;
}
