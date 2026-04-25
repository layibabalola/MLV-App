/* dualiso_avx2.inc — AVX2 + FMA kernels for HQ Dual ISO recon.
 *
 * This file is included from dualiso.c. It expects the following symbols
 * to be in scope at include time:
 *   EV_RESOLUTION (define), ALIAS_MAP_MAX (extern int), MAX/MIN/COERCE
 *   raw2ev, ev2raw (int*), randn05_cache (float[1024])
 *
 * Each kernel is compiled with __attribute__((target("avx2,fma"))) so the
 * AVX2 path lights up on capable hosts even though dualiso.c itself is
 * built with the project's baseline flags. Layout matches the Phase B0
 * prototype at .claude-state/profiling/20260424-phase-b0-final-blend/
 * final_blend_bench.c.
 *
 * Drift bound: float32 FMA reordering may differ from scalar double-precision
 * by ±1 LSB on a small fraction of pixels. The downstream pipeline already
 * absorbs ±8 LSB of dither noise from raw_set_pixel_20to16_rand, so this
 * level of drift is structurally invisible.
 */

#ifndef DUALISO_AVX2_KERNELS_INCLUDED
#define DUALISO_AVX2_KERNELS_INCLUDED

/* ALIAS_MAP_MAX is defined later in dualiso.c (file-scope `static const`).
 * Mirror its value here as a compile-time constant for the SIMD kernels. */
#ifndef DUALISO_ALIAS_MAP_MAX
#define DUALISO_ALIAS_MAP_MAX 15000
#endif

#ifdef DUALISO_AVX2_AVAILABLE

/* immintrin.h is already included by dualiso.c above. */

/* ====================================================================== *
 * B1.1 — convert_to_20bit: image_data[i] (uint16) << 6, mask 0xFFFFF.
 * 16 px per iter via two _mm256_cvtepu16_epi32 + _mm256_slli_epi32.
 * ====================================================================== */
__attribute__((target("avx2,fma")))
static void convert_to_20bit_avx2(uint32_t * out, const uint16_t * src, size_t total)
{
    const __m256i mask20 = _mm256_set1_epi32(0xFFFFF);
    size_t x = 0;
    for (; x + 16 <= total; x += 16) {
        __m256i lo16 = _mm256_loadu_si256((const __m256i*)(src + x));
        __m128i lo_low  = _mm256_castsi256_si128(lo16);
        __m128i lo_high = _mm256_extracti128_si256(lo16, 1);
        __m256i a = _mm256_cvtepu16_epi32(lo_low);
        __m256i b = _mm256_cvtepu16_epi32(lo_high);
        a = _mm256_and_si256(_mm256_slli_epi32(a, 6), mask20);
        b = _mm256_and_si256(_mm256_slli_epi32(b, 6), mask20);
        _mm256_storeu_si256((__m256i*)(out + x), a);
        _mm256_storeu_si256((__m256i*)(out + x + 8), b);
    }
    for (; x < total; x++) {
        out[x] = (((uint32_t)src[x]) << 6) & 0xFFFFF;
    }
}

/* ====================================================================== *
 * B1.2 — fullres_reconstruction inner row.
 * For BRIGHT rows: fullres[i] = (bright[i] < white_darkened) ? bright[i]
 *                                                            : MAX(bright[i], dark[i]);
 * For DARK   rows: fullres[i] = dark[i].
 * Branch is row-uniform (BRIGHT_ROW depends on y only), so we hoist it
 * outside the inner loop. SIMD only useful on bright rows; dark rows are
 * a memcpy.
 * ====================================================================== */
__attribute__((target("avx2,fma")))
static void fullres_reconstruction_bright_row_avx2(uint32_t * fullres,
                                                    const uint32_t * bright,
                                                    const uint32_t * dark,
                                                    uint32_t white_darkened,
                                                    int w)
{
    const __m256i v_white = _mm256_set1_epi32((int32_t)white_darkened);
    int x = 0;
    for (; x + 8 <= w; x += 8) {
        __m256i b = _mm256_loadu_si256((const __m256i*)(bright + x));
        __m256i d = _mm256_loadu_si256((const __m256i*)(dark + x));
        /* mask = b < white_darkened (signed compare safe: values < 1<<20) */
        __m256i ge = _mm256_cmpgt_epi32(v_white, b);  /* white > b -> b < white */
        /* if (b < white): out = b, else out = max(b, d) */
        __m256i mx = _mm256_max_epu32(b, d);
        __m256i out = _mm256_blendv_epi8(mx, b, ge);
        _mm256_storeu_si256((__m256i*)(fullres + x), out);
    }
    for (; x < w; x++) {
        uint32_t f = bright[x];
        uint32_t dd = dark[x];
        fullres[x] = (f < white_darkened) ? f : (f > dd ? f : dd);
    }
}

/* ====================================================================== *
 * B1.3 — convert_20_to_16bit: out[i] = clamp((raw32[i]/16) + randn05 + 0.5, 0, 65535)
 * 8 px / iter; gather 8 dither floats from the precomputed cache via a
 * caller-supplied counter k.
 * ====================================================================== */
__attribute__((target("avx2,fma")))
static void convert_20_to_16bit_row_avx2(uint16_t * out,
                                          const uint32_t * src,
                                          const float * randn_cache,
                                          int * k_inout,
                                          int w)
{
    const __m256 v_inv16 = _mm256_set1_ps(1.0f/16.0f);
    const __m256 v_half  = _mm256_set1_ps(0.5f);
    const __m256i v_zero = _mm256_setzero_si256();
    const __m256i v_max16 = _mm256_set1_epi32(0xFFFF);
    int k = *k_inout;
    int x = 0;
    for (; x + 8 <= w; x += 8) {
        __m256i v = _mm256_loadu_si256((const __m256i*)(src + x));
        __m256 vf = _mm256_cvtepi32_ps(v);
        int idxs[8] __attribute__((aligned(32)));
        for (int i = 0; i < 8; i++) idxs[i] = (k + i) & 1023;
        __m256i vidx = _mm256_load_si256((const __m256i*)idxs);
        __m256 dither = _mm256_i32gather_ps(randn_cache, vidx, 4);
        __m256 r = _mm256_fmadd_ps(vf, v_inv16, dither);
        r = _mm256_add_ps(r, v_half);
        __m256i ri = _mm256_cvttps_epi32(r);
        ri = _mm256_max_epi32(ri, v_zero);
        ri = _mm256_min_epi32(ri, v_max16);
        __m128i lo = _mm256_castsi256_si128(ri);
        __m128i hi = _mm256_extracti128_si256(ri, 1);
        __m128i p  = _mm_packus_epi32(lo, hi);
        _mm_storeu_si128((__m128i*)(out + x), p);
        k = (k + 8) & 1023;
    }
    for (; x < w; x++) {
        float dither = randn_cache[k & 1023];
        k = (k + 1) & 1023;
        int v = (int)((src[x] / 16.0f) + dither + 0.5f);
        if (v < 0) v = 0;
        if (v > 0xFFFF) v = 0xFFFF;
        out[x] = (uint16_t)v;
    }
    *k_inout = k;
}

/* ====================================================================== *
 * B2.1 — final_blend kernel (the big win).
 * Direct transcription of .claude-state/profiling/20260424-phase-b0-final-blend/
 * final_blend_bench.c::final_blend_avx2, adapted to the production
 * signature with optional alias_map (NULL means c=0 path).
 *
 * Processes 8 pixels per iter; tail pixels handled by caller.
 * ====================================================================== */
__attribute__((target("avx2,fma")))
static void final_blend_row_avx2(uint32_t * raw_buffer_32,
                                  const uint32_t * fullres,
                                  const uint32_t * fullres_smooth,
                                  const uint32_t * halfres_smooth,
                                  const uint32_t * dark,
                                  const uint32_t * bright,
                                  const uint16_t * overexposed,
                                  const uint16_t * alias_map,   /* may be NULL */
                                  const int * raw2ev_lut,
                                  const int * ev2raw_lut,        /* points to ev2raw_0 + 10*EV_RESOLUTION */
                                  const double * fullres_curve,
                                  int black, int dark_noise,
                                  int total)
{
    const __m256i mask20 = _mm256_set1_epi32(0xFFFFF);
    const __m256i v_black     = _mm256_set1_epi32(black);
    const __m256i v_lo        = _mm256_set1_epi32(-10*EV_RESOLUTION);
    const __m256i v_hi        = _mm256_set1_epi32(14*EV_RESOLUTION - 1);
    const __m256  v_one       = _mm256_set1_ps(1.0f);
    const __m256  v_zero      = _mm256_setzero_ps();
    const __m256  v_inv_alias = _mm256_set1_ps(1.0f / (float)DUALISO_ALIAS_MAP_MAX);
    const __m256  v_inv_200   = _mm256_set1_ps(1.0f / 200.0f);
    const __m256  v_inv4dn    = _mm256_set1_ps(1.0f / (4.0f * (float)dark_noise));

    int x = 0;
    for (; x + 8 <= total; x += 8) {
        __m256i b   = _mm256_loadu_si256((const __m256i*)&bright[x]);
        __m256i hr  = _mm256_loadu_si256((const __m256i*)&halfres_smooth[x]);
        __m256i fr  = _mm256_loadu_si256((const __m256i*)&fullres[x]);
        __m256i frs = _mm256_loadu_si256((const __m256i*)&fullres_smooth[x]);
        __m256i d   = _mm256_loadu_si256((const __m256i*)&dark[x]);

        __m256i hrev_i  = _mm256_i32gather_epi32(raw2ev_lut, hr,  4);
        __m256i frev_i  = _mm256_i32gather_epi32(raw2ev_lut, fr,  4);
        __m256i frsev_i = _mm256_i32gather_epi32(raw2ev_lut, frs, 4);

        __m256 hrev  = _mm256_cvtepi32_ps(hrev_i);
        __m256 frev  = _mm256_cvtepi32_ps(frev_i);
        __m256 frsev = _mm256_cvtepi32_ps(frsev_i);

        __m256i b_idx = _mm256_and_si256(b, mask20);
        __m128i b_idx_lo = _mm256_castsi256_si128(b_idx);
        __m128i b_idx_hi = _mm256_extracti128_si256(b_idx, 1);
        __m256d f_lo_d = _mm256_i32gather_pd(fullres_curve, b_idx_lo, 8);
        __m256d f_hi_d = _mm256_i32gather_pd(fullres_curve, b_idx_hi, 8);
        __m128 f_lo = _mm256_cvtpd_ps(f_lo_d);
        __m128 f_hi = _mm256_cvtpd_ps(f_hi_d);
        __m256 f = _mm256_insertf128_ps(_mm256_castps128_ps256(f_lo), f_hi, 1);

        __m256 c_amap;
        if (alias_map) {
            __m128i amap16 = _mm_loadu_si128((const __m128i*)&alias_map[x]);
            __m256i amap_i = _mm256_cvtepu16_epi32(amap16);
            c_amap = _mm256_mul_ps(_mm256_cvtepi32_ps(amap_i), v_inv_alias);
            c_amap = _mm256_min_ps(_mm256_max_ps(c_amap, v_zero), v_one);
        } else {
            c_amap = v_zero;
        }

        __m128i over16 = _mm_loadu_si128((const __m128i*)&overexposed[x]);
        __m256i over_i = _mm256_cvtepu16_epi32(over16);
        __m256 ovf = _mm256_mul_ps(_mm256_cvtepi32_ps(over_i), v_inv_200);
        ovf = _mm256_min_ps(_mm256_max_ps(ovf, v_zero), v_one);

        __m256 c = _mm256_max_ps(c_amap, ovf);
        __m256 noisy_or_over = _mm256_max_ps(ovf, _mm256_sub_ps(v_one, f));
        f = _mm256_max_ps(f, c);

        __m256 fev = _mm256_fmadd_ps(noisy_or_over, _mm256_sub_ps(frsev, frev), frev);

        __m256i sig = _mm256_srai_epi32(_mm256_add_epi32(d, b), 1);
        __m256i sig_minus_black = _mm256_sub_epi32(sig, v_black);
        __m256 cap = _mm256_mul_ps(_mm256_cvtepi32_ps(sig_minus_black), v_inv4dn);
        f = _mm256_max_ps(v_zero, _mm256_min_ps(f, cap));

        __m256 outf = _mm256_fmadd_ps(f, _mm256_sub_ps(fev, hrev), hrev);

        __m256i outi = _mm256_cvttps_epi32(outf);
        outi = _mm256_min_epi32(_mm256_max_epi32(outi, v_lo), v_hi);

        __m256i raw_out = _mm256_i32gather_epi32(ev2raw_lut, outi, 4);
        _mm256_storeu_si256((__m256i*)&raw_buffer_32[x], raw_out);
    }
    (void)x;
}

/* ====================================================================== *
 * B2.2 — mix_images half-res blend.
 * ====================================================================== */
__attribute__((target("avx2,fma")))
static void mix_images_row_avx2(uint32_t * halfres,
                                 const uint32_t * bright,
                                 const uint32_t * dark,
                                 const int * raw2ev_lut,
                                 const int * ev2raw_lut,
                                 const double * mix_curve,
                                 int total)
{
    const __m256i mask20 = _mm256_set1_epi32(0xFFFFF);
    const __m256i v_lo   = _mm256_set1_epi32(-10*EV_RESOLUTION);
    const __m256i v_hi   = _mm256_set1_epi32(14*EV_RESOLUTION - 1);
    const __m256  v_one  = _mm256_set1_ps(1.0f);
    const __m256  v_zero = _mm256_setzero_ps();

    int x = 0;
    for (; x + 8 <= total; x += 8) {
        __m256i b = _mm256_loadu_si256((const __m256i*)&bright[x]);
        __m256i d = _mm256_loadu_si256((const __m256i*)&dark[x]);

        __m256i bev_i = _mm256_i32gather_epi32(raw2ev_lut, b, 4);
        __m256i dev_i = _mm256_i32gather_epi32(raw2ev_lut, d, 4);
        __m256 bev = _mm256_cvtepi32_ps(bev_i);
        __m256 dev = _mm256_cvtepi32_ps(dev_i);

        __m256i b_idx = _mm256_and_si256(b, mask20);
        __m128i b_idx_lo = _mm256_castsi256_si128(b_idx);
        __m128i b_idx_hi = _mm256_extracti128_si256(b_idx, 1);
        __m256d k_lo_d = _mm256_i32gather_pd(mix_curve, b_idx_lo, 8);
        __m256d k_hi_d = _mm256_i32gather_pd(mix_curve, b_idx_hi, 8);
        __m128 k_lo = _mm256_cvtpd_ps(k_lo_d);
        __m128 k_hi = _mm256_cvtpd_ps(k_hi_d);
        __m256 k = _mm256_insertf128_ps(_mm256_castps128_ps256(k_lo), k_hi, 1);
        k = _mm256_min_ps(_mm256_max_ps(k, v_zero), v_one);

        /* mixed = bev*(1-k) + dev*k = bev + k*(dev - bev) */
        __m256 mixed_f = _mm256_fmadd_ps(k, _mm256_sub_ps(dev, bev), bev);
        __m256i mixed_i = _mm256_cvttps_epi32(mixed_f);
        mixed_i = _mm256_min_epi32(_mm256_max_epi32(mixed_i, v_lo), v_hi);

        __m256i raw_out = _mm256_i32gather_epi32(ev2raw_lut, mixed_i, 4);
        _mm256_storeu_si256((__m256i*)&halfres[x], raw_out);
    }
    (void)x;
}

#endif /* DUALISO_AVX2_AVAILABLE */

#endif /* DUALISO_AVX2_KERNELS_INCLUDED */
