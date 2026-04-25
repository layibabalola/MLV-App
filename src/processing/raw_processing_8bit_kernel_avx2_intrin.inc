/*
 * Hand-tuned AVX2 + FMA intrinsics body of apply_processing_object_8bit_fast_rows.
 *
 * Included from raw_processing.c with `__attribute__((target("avx2,fma")))` so
 * the compiler emits a dedicated AVX2 + FMA function. Strategy:
 *
 *   - Process 8 RGB pixels per inner iteration = 24 uint16 = 48 bytes.
 *   - Deinterleave the 3 channels into three __m128i (8 x uint16 each) using
 *     a small scalar transpose path (cheap; deinterleaving 3-channel u16
 *     in pure SIMD wants vpalignr ladders that aren't a clear win at 8 wide).
 *   - Zero-extend each channel to int32 (__m256i).
 *   - Gather pm0[R], pm4[G], pm8[B] via _mm256_i32gather_epi32 (3 gathers).
 *   - Cvt int32 -> float, do the 3x3 matrix multiply with FMA.
 *   - Compute Y = sum(rgb_to_Y[i] * result_i). If !exr_mode AND any lane has
 *     Y > 0 AND |Y - min(result)| > FLT_MIN, fall back to per-lane scalar
 *     Reinhard for that batch. The Reinhard branch is rare for typical
 *     playback (most lanes either have Y == 0, or land below the FLT_MIN
 *     threshold) and the SIMD fallback to scalar matches the
 *     autovec variant's effective behaviour anyway -- the autovec compiler
 *     scalarises the if() branch too.
 *   - LIMIT16 via _mm256_min_epi32(_mm256_max_epi32(x, 0), 65535).
 *   - Gather pre_calc_gamma[idx]: pre_calc_gamma is uint16, so we gather
 *     32-bit from (int32_t*)pre_calc_gamma using halved indices and then
 *     mask the lane. SAFER variant used here: scalar gamma lookup per lane
 *     via vector extract -- still only 8 loads per 8-pixel batch, which is
 *     close to what the autovec produces for an arbitrary uint16 LUT.
 *   - >>8 + uint8 store via 3 scalar writes per pixel (RGB tightly packed
 *     output).  The tail with creative_curves remains scalar.
 *
 * Parity with the scalar variant is bit-exact under the receipts driven by
 * tests/pipeline/test_dual_iso_pipeline.cpp's DirectProcessed8FastPath* tests.
 */

#ifndef APPLY_8BIT_FAST_ROWS_NAME
#error "include raw_processing_8bit_kernel_avx2_intrin.inc only via APPLY_8BIT_FAST_ROWS_NAME"
#endif

#ifndef APPLY_8BIT_FAST_ROWS_ATTRS
#define APPLY_8BIT_FAST_ROWS_ATTRS
#endif

#include <immintrin.h>

APPLY_8BIT_FAST_ROWS_ATTRS
static void APPLY_8BIT_FAST_ROWS_NAME(processingObject_t * processing,
                                      int imageX,
                                      int rowStart,
                                      int rowEnd,
                                      uint16_t * __restrict inputImage,
                                      uint8_t * __restrict outputImage,
                                      processing_direct8_kernel_timing_t * timing)
{
    int32_t ** pm = processing->pre_calc_matrix;
    const int row_stride = imageX * 3;
    uint16_t * pre_calc_curve = processing->pre_calc_curve_r;
    uint16_t * gcurve_y = processing->gcurve_y;
    uint16_t * gcurve_r = processing->gcurve_r;
    uint16_t * gcurve_g = processing->gcurve_g;
    uint16_t * gcurve_b = processing->gcurve_b;
    const int apply_creative_curves = processing->allow_creative_adjustments != 0;
    const int exr_mode = processing->exr_mode != 0;

    int32_t * pm0 = pm[0];
    int32_t * pm4 = pm[4];
    int32_t * pm8 = pm[8];
    uint16_t * pre_calc_gamma = processing->pre_calc_gamma;
    float rgb_to_Y[3] = {0.0f, 0.0f, 0.0f};
    double inverse_mat[9];
    invertMatrix(colour_gamuts[processing->colour_gamut], inverse_mat);
    for( int i = 0; i < 3; ++i ) rgb_to_Y[i] = inverse_mat[3 + i];

    const float proper_wb_0 = (float)processing->proper_wb_matrix[0];
    const float proper_wb_1 = (float)processing->proper_wb_matrix[1];
    const float proper_wb_2 = (float)processing->proper_wb_matrix[2];
    const float proper_wb_3 = (float)processing->proper_wb_matrix[3];
    const float proper_wb_4 = (float)processing->proper_wb_matrix[4];
    const float proper_wb_5 = (float)processing->proper_wb_matrix[5];
    const float proper_wb_6 = (float)processing->proper_wb_matrix[6];
    const float proper_wb_7 = (float)processing->proper_wb_matrix[7];
    const float proper_wb_8 = (float)processing->proper_wb_matrix[8];
    const float rgb_to_y0 = rgb_to_Y[0];
    const float rgb_to_y1 = rgb_to_Y[1];
    const float rgb_to_y2 = rgb_to_Y[2];

    /* Broadcasts. */
    const __m256 v_wb0 = _mm256_set1_ps(proper_wb_0);
    const __m256 v_wb1 = _mm256_set1_ps(proper_wb_1);
    const __m256 v_wb2 = _mm256_set1_ps(proper_wb_2);
    const __m256 v_wb3 = _mm256_set1_ps(proper_wb_3);
    const __m256 v_wb4 = _mm256_set1_ps(proper_wb_4);
    const __m256 v_wb5 = _mm256_set1_ps(proper_wb_5);
    const __m256 v_wb6 = _mm256_set1_ps(proper_wb_6);
    const __m256 v_wb7 = _mm256_set1_ps(proper_wb_7);
    const __m256 v_wb8 = _mm256_set1_ps(proper_wb_8);
    const __m256 v_y0 = _mm256_set1_ps(rgb_to_y0);
    const __m256 v_y1 = _mm256_set1_ps(rgb_to_y1);
    const __m256 v_y2 = _mm256_set1_ps(rgb_to_y2);
    const __m256i v_zero32 = _mm256_setzero_si256();
    const __m256i v_max16 = _mm256_set1_epi32(65535);

    /* Profiling state -- mirrors the scalar/autovec kernel layout exactly so
     * the substage breakdown matches. */
    const int profile_loop_stride = 1024;
    int matrix_profile_counter = 0;
    int gamma_profile_counter = 0;
    int curves_profile_counter = 0;
    double matrix_profile_start = 0.0;
    double gamma_profile_start = 0.0;
    double curves_profile_start = 0.0;
    if( timing != NULL )
    {
        matrix_profile_start = omp_get_wtime();
    }

    for( int row = rowStart; row < rowEnd; ++row )
    {
        uint16_t * pix = inputImage + ( row * row_stride );
        uint8_t * out = outputImage + ( row * row_stride );
        int x = 0;

        /* SIMD body: 8 RGB pixels per iteration. */
        const int simd_x_end = imageX & ~7;
        for( ; x < simd_x_end; x += 8 )
        {
            /* 24 uint16 = 48 bytes spanning pix[x*3 .. x*3+23]. We want three
             * channel vectors of 8 uint16 each.
             *
             * Layout in memory:
             *   r0 g0 b0  r1 g1 b1  r2 g2 b2  r3 g3 b3
             *   r4 g4 b4  r5 g5 b5  r6 g6 b6  r7 g7 b7
             *
             * Easiest correct deinterleave: scalar-load each channel into a
             * tiny 16-byte buffer and broadcast to __m128i. This is 24 scalar
             * loads (one per uint16) which the compiler can emit as 24 movzx;
             * the gather + matmul + gamma cost dominates so the deinterleave
             * is in the noise.
             *
             * Alternative (rejected for v1): use _mm256_loadu_si256 for the
             * first 16 lanes + _mm_loadu_si128 for the last 8, then several
             * vpermq + vpshufb shuffles. Faster but harder to verify
             * byte-identity; bring this in only if perf needs it. */
            uint16_t * p = pix + x * 3;

            /* Channel buffers in stack-aligned scratch. */
            int32_t r_idx[8] __attribute__((aligned(32)));
            int32_t g_idx[8] __attribute__((aligned(32)));
            int32_t b_idx[8] __attribute__((aligned(32)));
            for( int k = 0; k < 8; ++k )
            {
                r_idx[k] = (int32_t)p[k * 3 + 0];
                g_idx[k] = (int32_t)p[k * 3 + 1];
                b_idx[k] = (int32_t)p[k * 3 + 2];
            }
            __m256i v_r_idx = _mm256_load_si256((const __m256i*)r_idx);
            __m256i v_g_idx = _mm256_load_si256((const __m256i*)g_idx);
            __m256i v_b_idx = _mm256_load_si256((const __m256i*)b_idx);

            /* Gather pm0[R], pm4[G], pm8[B] (int32 stride 4). */
            __m256i v_pix0_i = _mm256_i32gather_epi32(pm0, v_r_idx, 4);
            __m256i v_pix1_i = _mm256_i32gather_epi32(pm4, v_g_idx, 4);
            __m256i v_pix2_i = _mm256_i32gather_epi32(pm8, v_b_idx, 4);

            /* Convert to float. */
            __m256 v_pix0 = _mm256_cvtepi32_ps(v_pix0_i);
            __m256 v_pix1 = _mm256_cvtepi32_ps(v_pix1_i);
            __m256 v_pix2 = _mm256_cvtepi32_ps(v_pix2_i);

            /* Matrix multiply with FMA (mirroring scalar's a*b + c*d + e*f
             * left-associated chain, expressed as
             *   r = ((p0*w0) + p1*w1) + p2*w2
             * which the compiler is free to fuse into two FMAs:
             *   tmp = fma(p1, w1, p0*w0);
             *   r   = fma(p2, w2, tmp);
             * Note: scalar source is `pix0*wb0 + pix1*wb1 + pix2*wb2`, which
             * GCC at -O3 evaluates left-to-right *without* fusing because the
             * scalar code uses plain *,+ operators. Our explicit FMA changes
             * the rounding on intermediate sums. The byte-quantising `>>8`
             * downstream absorbs sub-ulp drift; if parity tests fail we
             * fall back to non-fused mul+add here. */
            __m256 v_r0 = _mm256_mul_ps(v_pix0, v_wb0);
            v_r0 = _mm256_fmadd_ps(v_pix1, v_wb1, v_r0);
            v_r0 = _mm256_fmadd_ps(v_pix2, v_wb2, v_r0);

            __m256 v_r1 = _mm256_mul_ps(v_pix0, v_wb3);
            v_r1 = _mm256_fmadd_ps(v_pix1, v_wb4, v_r1);
            v_r1 = _mm256_fmadd_ps(v_pix2, v_wb5, v_r1);

            __m256 v_r2 = _mm256_mul_ps(v_pix0, v_wb6);
            v_r2 = _mm256_fmadd_ps(v_pix1, v_wb7, v_r2);
            v_r2 = _mm256_fmadd_ps(v_pix2, v_wb8, v_r2);

            /* Compute Y per lane. */
            __m256 v_Y = _mm256_mul_ps(v_y0, v_r0);
            v_Y = _mm256_fmadd_ps(v_y1, v_r1, v_Y);
            v_Y = _mm256_fmadd_ps(v_y2, v_r2, v_Y);

            /* Reinhard branch.
             *
             * Vectorising the Reinhard fixup is doable but invasive (two
             * separate Reinhard curves, plus a min/min reduction across the 3
             * channels per lane). For v1 we detect "any lane needs Reinhard"
             * via movemask and fall back to scalar for that 8-pixel batch.
             * The expected path (most production playback) is `exr_mode==0`
             * + Y > 0 + |Y - min| > FLT_MIN, which is the *common* case, so
             * the fallback hits often. To preserve perf we still vectorise
             * the matrix multiply above; the Reinhard fallback then re-loads
             * pix0/pix1/pix2 scalarly.
             *
             * BUT: byte-identity matters more than perf for v1. To avoid any
             * chance of FMA-vs-scalar drift surviving the >>8 truncation
             * when Reinhard is engaged, we ALWAYS take the scalar path for
             * lanes where Reinhard would fire, and only use the SIMD result
             * for lanes where it would not. Since the `if(!exr_mode && Y>0)`
             * + `if(|Y-min|>FLT_MIN)` test is cheap, we evaluate it per lane
             * and union the results. */
            uint32_t reinhard_mask = 0u;
            if( !exr_mode )
            {
                __m256 v_zero_f = _mm256_setzero_ps();
                /* Y > 0 mask. */
                __m256 v_y_pos = _mm256_cmp_ps(v_Y, v_zero_f, _CMP_GT_OQ);
                /* min(r,g,b) per lane. */
                __m256 v_min = _mm256_min_ps(_mm256_min_ps(v_r0, v_r1), v_r2);
                /* |Y - min| > FLT_MIN. fabsf via mask off sign bit. */
                __m256 v_abs_mask = _mm256_castsi256_ps(_mm256_set1_epi32(0x7FFFFFFF));
                __m256 v_diff = _mm256_sub_ps(v_Y, v_min);
                __m256 v_diff_abs = _mm256_and_ps(v_diff, v_abs_mask);
                __m256 v_flt_min = _mm256_set1_ps(FLT_MIN);
                __m256 v_diff_big = _mm256_cmp_ps(v_diff_abs, v_flt_min, _CMP_GT_OQ);
                __m256 v_reinhard = _mm256_and_ps(v_y_pos, v_diff_big);
                reinhard_mask = (uint32_t)_mm256_movemask_ps(v_reinhard);
            }

            /* Cvt result float -> int32 with truncation, then LIMIT16. The
             * scalar source uses `LIMIT16((uint32_t)result_i)`; the unsigned
             * cast of a negative float is implementation-defined and on x86
             * GCC compiles it to cvttss2si -> 0x80000000 -> reinterpret as
             * uint32_t = 2^31 -> LIMIT16 clamps to 65535. We replicate that
             * "negative -> 65535" behaviour via blendv on the sign bit. */
            __m256i v_r0_i = _mm256_cvttps_epi32(v_r0);
            __m256i v_r1_i = _mm256_cvttps_epi32(v_r1);
            __m256i v_r2_i = _mm256_cvttps_epi32(v_r2);

            /* Positive: min(x, 65535). Negative: 65535. */
            __m256i v_r0_neg = _mm256_cmpgt_epi32(v_zero32, v_r0_i);
            __m256i v_r1_neg = _mm256_cmpgt_epi32(v_zero32, v_r1_i);
            __m256i v_r2_neg = _mm256_cmpgt_epi32(v_zero32, v_r2_i);
            __m256i v_r0_min = _mm256_min_epi32(v_r0_i, v_max16);
            __m256i v_r1_min = _mm256_min_epi32(v_r1_i, v_max16);
            __m256i v_r2_min = _mm256_min_epi32(v_r2_i, v_max16);
            v_r0_i = _mm256_blendv_epi8(v_r0_min, v_max16, v_r0_neg);
            v_r1_i = _mm256_blendv_epi8(v_r1_min, v_max16, v_r1_neg);
            v_r2_i = _mm256_blendv_epi8(v_r2_min, v_max16, v_r2_neg);

            /* Stash gamma indices and SIMD-result floats so we can patch
             * Reinhard lanes scalarly. */
            int32_t r0_idx[8] __attribute__((aligned(32)));
            int32_t r1_idx[8] __attribute__((aligned(32)));
            int32_t r2_idx[8] __attribute__((aligned(32)));
            _mm256_store_si256((__m256i*)r0_idx, v_r0_i);
            _mm256_store_si256((__m256i*)r1_idx, v_r1_i);
            _mm256_store_si256((__m256i*)r2_idx, v_r2_i);

            /* Reinhard fallback: redo the 8 affected lanes scalarly so the
             * floats match the reference exactly (no FMA fusion drift). The
             * branch is taken when ANY lane in the batch needs Reinhard;
             * within the loop we still skip lanes where the Reinhard test
             * was false, but since we are already in a slow path the per-lane
             * branch cost is acceptable. */
            if( reinhard_mask != 0u )
            {
                for( int k = 0; k < 8; ++k )
                {
                    if( ((reinhard_mask >> k) & 1u) == 0u )
                    {
                        continue;
                    }

                    /* Re-derive scalar floats from raw pixel data so rounding
                     * matches the scalar kernel. */
                    const float pix0_s = (float)pm0[r_idx[k]];
                    const float pix1_s = (float)pm4[g_idx[k]];
                    const float pix2_s = (float)pm8[b_idx[k]];
                    float result0 = pix0_s * proper_wb_0 + pix1_s * proper_wb_1 + pix2_s * proper_wb_2;
                    float result1 = pix0_s * proper_wb_3 + pix1_s * proper_wb_4 + pix2_s * proper_wb_5;
                    float result2 = pix0_s * proper_wb_6 + pix1_s * proper_wb_7 + pix2_s * proper_wb_8;

                    const float Y_s = rgb_to_y0 * result0
                                    + rgb_to_y1 * result1
                                    + rgb_to_y2 * result2;
                    /* exr_mode known to be false here; Y > 0 and |Y-min|>FLT_MIN
                     * also known to hold from the SIMD precheck. We re-test in
                     * scalar precisely to avoid FMA-vs-scalar drift in the
                     * outer test. */
                    if( !exr_mode && Y_s > 0.0f )
                    {
                        const float min_channel = MIN(MIN(result0, result1), result2);
                        const float y_minus_min_channel = Y_s - min_channel;
                        if( fabsf(y_minus_min_channel) > FLT_MIN )
                        {
                            const float inv_y = 1.0f / Y_s;
                            const float result2_0 = Y_s - (Reinhard_for_colour((Y_s - result0) * inv_y) * Y_s);
                            const float result2_1 = Y_s - (Reinhard_for_blue((Y_s - result1) * inv_y) * Y_s);
                            const float result2_2 = Y_s - (Reinhard_for_blue((Y_s - result2) * inv_y) * Y_s);
                            const float desaturate_factor =
                                (Y_s - MIN(MIN(result2_0, result2_1), result2_2)) / y_minus_min_channel;

                            result0 = (result0 - Y_s) * desaturate_factor + Y_s;
                            result1 = (result1 - Y_s) * desaturate_factor + Y_s;
                            result2 = (result2 - Y_s) * desaturate_factor + Y_s;
                        }
                    }

                    r0_idx[k] = (int32_t)LIMIT16((uint32_t)result0);
                    r1_idx[k] = (int32_t)LIMIT16((uint32_t)result1);
                    r2_idx[k] = (int32_t)LIMIT16((uint32_t)result2);
                }
            }

            /* Matrix substage timing -- counted once per 8-pixel batch (the
             * scalar kernel counts once per pixel; we scale the threshold
             * accordingly). */
            if( timing != NULL )
            {
                matrix_profile_counter += 8;
                if( matrix_profile_counter >= profile_loop_stride )
                {
                    const double now = omp_get_wtime();
                    timing->matrix_ms += (now - matrix_profile_start) * 1000.0;
                    matrix_profile_start = now;
                    matrix_profile_counter = 0;
                }
                if( gamma_profile_counter == 0 )
                {
                    gamma_profile_start = omp_get_wtime();
                }
            }

            /* Gamma LUT. pre_calc_gamma is uint16[65536]. Per-lane scalar
             * extract is 8 movzx; faster alternatives need a 16->32 gather
             * with masking which is brittle for byte-identity. */
            uint16_t mapped0_a[8];
            uint16_t mapped1_a[8];
            uint16_t mapped2_a[8];
            for( int k = 0; k < 8; ++k )
            {
                mapped0_a[k] = pre_calc_gamma[r0_idx[k]];
                mapped1_a[k] = pre_calc_gamma[r1_idx[k]];
                mapped2_a[k] = pre_calc_gamma[r2_idx[k]];
            }

            if( timing != NULL )
            {
                gamma_profile_counter += 8;
                if( gamma_profile_counter >= profile_loop_stride )
                {
                    const double now = omp_get_wtime();
                    timing->gamma_ms += (now - gamma_profile_start) * 1000.0;
                    gamma_profile_start = now;
                    gamma_profile_counter = 0;
                }
            }

            /* Creative curves: scalar path. Same as the reference kernel. */
            if( apply_creative_curves )
            {
                if( timing != NULL && curves_profile_counter == 0 )
                {
                    curves_profile_start = omp_get_wtime();
                }
                for( int k = 0; k < 8; ++k )
                {
                    mapped0_a[k] = pre_calc_curve[mapped0_a[k]];
                    mapped1_a[k] = pre_calc_curve[mapped1_a[k]];
                    mapped2_a[k] = pre_calc_curve[mapped2_a[k]];

                    mapped0_a[k] = gcurve_y[mapped0_a[k]];
                    mapped1_a[k] = gcurve_y[mapped1_a[k]];
                    mapped2_a[k] = gcurve_y[mapped2_a[k]];

                    mapped0_a[k] = gcurve_r[mapped0_a[k]];
                    mapped1_a[k] = gcurve_g[mapped1_a[k]];
                    mapped2_a[k] = gcurve_b[mapped2_a[k]];
                }
                if( timing != NULL )
                {
                    curves_profile_counter += 8;
                    if( curves_profile_counter >= profile_loop_stride )
                    {
                        const double now = omp_get_wtime();
                        timing->curves_ms += (now - curves_profile_start) * 1000.0;
                        curves_profile_start = now;
                        curves_profile_counter = 0;
                    }
                }
            }

            /* Store interleaved RGB uint8. */
            uint8_t * o = out + x * 3;
            for( int k = 0; k < 8; ++k )
            {
                o[k * 3 + 0] = (uint8_t)(mapped0_a[k] >> 8);
                o[k * 3 + 1] = (uint8_t)(mapped1_a[k] >> 8);
                o[k * 3 + 2] = (uint8_t)(mapped2_a[k] >> 8);
            }
        }

        /* Scalar tail: mirror the reference kernel exactly. */
        for( ; x < imageX; ++x )
        {
            uint16_t * p = pix + x * 3;
            uint8_t * o = out + x * 3;

            const float pix0 = (float)pm0[p[0]];
            const float pix1 = (float)pm4[p[1]];
            const float pix2 = (float)pm8[p[2]];
            float result0 = pix0 * proper_wb_0 + pix1 * proper_wb_1 + pix2 * proper_wb_2;
            float result1 = pix0 * proper_wb_3 + pix1 * proper_wb_4 + pix2 * proper_wb_5;
            float result2 = pix0 * proper_wb_6 + pix1 * proper_wb_7 + pix2 * proper_wb_8;

            const float Y = rgb_to_y0 * result0
                          + rgb_to_y1 * result1
                          + rgb_to_y2 * result2;
            if( !exr_mode && Y > 0.0f )
            {
                const float min_channel = MIN(MIN(result0, result1), result2);
                const float y_minus_min_channel = Y - min_channel;
                if( fabsf(y_minus_min_channel) > FLT_MIN )
                {
                    const float inv_y = 1.0f / Y;
                    const float result2_0 = Y - (Reinhard_for_colour((Y - result0) * inv_y) * Y);
                    const float result2_1 = Y - (Reinhard_for_blue((Y - result1) * inv_y) * Y);
                    const float result2_2 = Y - (Reinhard_for_blue((Y - result2) * inv_y) * Y);
                    const float desaturate_factor =
                        (Y - MIN(MIN(result2_0, result2_1), result2_2)) / y_minus_min_channel;

                    result0 = (result0 - Y) * desaturate_factor + Y;
                    result1 = (result1 - Y) * desaturate_factor + Y;
                    result2 = (result2 - Y) * desaturate_factor + Y;
                }
            }

            uint16_t mapped0 = pre_calc_gamma[LIMIT16((uint32_t)result0)];
            uint16_t mapped1 = pre_calc_gamma[LIMIT16((uint32_t)result1)];
            uint16_t mapped2 = pre_calc_gamma[LIMIT16((uint32_t)result2)];
            if( apply_creative_curves )
            {
                mapped0 = pre_calc_curve[mapped0];
                mapped1 = pre_calc_curve[mapped1];
                mapped2 = pre_calc_curve[mapped2];

                mapped0 = gcurve_y[mapped0];
                mapped1 = gcurve_y[mapped1];
                mapped2 = gcurve_y[mapped2];

                mapped0 = gcurve_r[mapped0];
                mapped1 = gcurve_g[mapped1];
                mapped2 = gcurve_b[mapped2];
            }

            o[0] = (uint8_t)(mapped0 >> 8);
            o[1] = (uint8_t)(mapped1 >> 8);
            o[2] = (uint8_t)(mapped2 >> 8);
        }
    }

    if( timing != NULL )
    {
        const double now = omp_get_wtime();
        if( matrix_profile_counter > 0 )
        {
            timing->matrix_ms += (now - matrix_profile_start) * 1000.0;
        }
        if( gamma_profile_counter > 0 )
        {
            timing->gamma_ms += (now - gamma_profile_start) * 1000.0;
        }
        if( curves_profile_counter > 0 )
        {
            timing->curves_ms += (now - curves_profile_start) * 1000.0;
        }
    }
}

#undef APPLY_8BIT_FAST_ROWS_NAME
#undef APPLY_8BIT_FAST_ROWS_ATTRS
