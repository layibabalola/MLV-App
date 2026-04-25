/* dualiso_amaze_avx2.inc — AVX2 + FMA kernel for AMaZE edge-direction
 *                          estimator (Phase E1).
 *
 * This file is included from dualiso.c after the file-scope
 * `edge_directions[]` table is declared. It expects the following symbols
 * to be in scope at include time:
 *   EV_RESOLUTION (define), MAX/MIN/COERCE/ABS/COUNT
 *   edge_directions[] (file-scope const struct table; 11 entries)
 *   pack_pd_cmp_to_epi32_mask (helper from dualiso_avx2.inc)
 *
 * The kernel vectorises the inner per-pixel sweep of the edge-direction
 * estimator at dualiso.c::amaze_interpolate (Phase 1 walkthrough scoping
 * `.claude-state/profiling/20260425-amaze-simd-scoping/PLAN.md` Section 1).
 *
 * Scalar reference:
 *
 *     for (int x = 5; x < w-5; x ++) {
 *         int e_best = INT_MAX, d_best = d0;
 *         int dmin = 0, dmax = COUNT(edge_directions)-1;  // 0..10
 *         int search_area = 5;
 *         // ... predicate (sets dmin/dmax to d0,d0 for cheap lanes) ...
 *         if (dmin == dmax) {
 *             d_best = dmin;
 *         } else {
 *             for (int d = dmin; d <= dmax; d++) {
 *                 int e = 0;
 *                 for (int j = -5; j <= 5; j++) {
 *                     int p1 = raw2ev[gray[x+ack.x+j + (y+ack.y*s)*w]];
 *                     int p2 = raw2ev[gray[x+ a.x +j + (y+ a.y *s)*w]];
 *                     int p3 = raw2ev[gray[x+ b.x +j + (y+ b.y *s)*w]];
 *                     int p4 = raw2ev[gray[x+bck.x+j + (y+bck.y*s)*w]];
 *                     e += ABS(p1-p2) + ABS(p2-p3) + ABS(p3-p4);
 *                 }
 *                 e += ABS(d - d0) * EV_RESOLUTION/8;  // diagonal penalty
 *                 if (e < e_best) { e_best = e; d_best = d; }
 *             }
 *         }
 *         edge_direction[x + y*w] = d_best;
 *     }
 *
 * Strategy: outer loop over directions (11 iterations, fully unrolled by
 * the compiler), middle over j (11 iterations, fully unrolled), inner
 * over batches of 8 contiguous output pixels x. For each batch we
 * maintain a running 8-wide int32 (best_e, best_d) and update via a
 * vectorised argmin step after each direction. After all 11 directions,
 * we blend the result against the constant `d0` for the masked-out
 * (cheap) lanes.
 *
 * Predication: the scalar code sets dmin == dmax = d0 for `cheap` lanes
 * (full-res-curve > thr OR bright-row-not-overexposed). To preserve
 * byte-identity, we compute a per-lane `full_search_mask` and blend
 * the final argmin against `d0` for masked-out lanes.
 *
 * Borders (x in [0,5)∪[w-5,w) and y in [0,5)∪[h-5,h)) are skipped by the
 * caller's `for (y = 5; y < h-5; y++)` outer loop — this kernel only
 * processes interior x in [5, w-5) for a single row at a time. The
 * scalar tail handles x past `w - 13` (where 8 SIMD lanes would overrun).
 *
 * Per-pixel cost: 11 directions × 11 j × 4 gathers = 484 i32-gathers per
 * 8-pixel batch = ~60 gathers per pixel. Skylake-class hosts dispatch
 * vpgatherdd at ~5 cycles + 1/lane latency; the gather throughput is
 * the headline cost.
 *
 * Byte-identity vs scalar: this kernel is byte-identical because all
 * arithmetic is integer, no FMA, no float reorder. The argmin
 * tie-breaking matches scalar (`<` not `<=`, so first matching d wins).
 */

#ifndef DUALISO_AMAZE_AVX2_KERNELS_INCLUDED
#define DUALISO_AMAZE_AVX2_KERNELS_INCLUDED



#ifdef DUALISO_AVX2_AVAILABLE

/* immintrin.h is already included by dualiso.c. */

/* Diagonal penalty per direction d, with d0=5 (the vertical, preferred):
 *   ABS(d - 5) * EV_RESOLUTION/8   for d in [0..NUM_DIR-1]
 *   With NUM_DIR=11 (the file-scope edge_directions[] table has 11 entries
 *   — the two almost-horizontal directions are commented out), d0=11/2=5.
 *   So values are {5,4,3,2,1,0,1,2,3,4,5} * (65536/8) = * 8192. */
static const int32_t kAmazeDiagPenalty[11] = {
    5*8192, 4*8192, 3*8192, 2*8192, 1*8192, 0,
    1*8192, 2*8192, 3*8192, 4*8192, 5*8192
};

/* Vectorised argmin: given two 8-wide int32 vectors `cur_e` (running min
 * cost) and `cur_d` (running argmin direction), and a candidate
 * `cand_e`/`cand_d`, return new (cur_e, cur_d) where for each lane:
 *   if (cand_e < cur_e) { cur_e = cand_e; cur_d = cand_d; }
 *
 * Match scalar tie-break: scalar uses `if (e < e_best)` — strictly less.
 * We use _mm256_cmpgt_epi32(cur_e, cand_e) which is true iff cur_e > cand_e
 * iff cand_e < cur_e — same predicate.
 *
 * Caller passes cand_d as a broadcast scalar (constant per outer
 * direction iteration), so we keep it as __m256i for blendv mechanics. */
__attribute__((target("avx2,fma")))
static inline void amaze_argmin_step_avx2(__m256i * cur_e, __m256i * cur_d,
                                           __m256i cand_e, __m256i cand_d)
{
    __m256i better = _mm256_cmpgt_epi32(*cur_e, cand_e);
    *cur_e = _mm256_blendv_epi8(*cur_e, cand_e, better);
    *cur_d = _mm256_blendv_epi8(*cur_d, cand_d, better);
}

/* Pack 8 int32 lanes (each in [0..255]) to 8 uint8 bytes at `out`.
 * The high 24 bits of each lane are zero. */
__attribute__((target("avx2,fma")))
static inline void amaze_store_8_bytes_avx2(uint8_t * out, __m256i v)
{
    /* _mm256_packus_epi32 on (v, v) produces 16 uint16 lanes from two i32x8
     * inputs, then _mm_packus_epi16 narrows each pair of 16 uint16s to 16
     * uint8s. We only need 8 bytes from the low half. */
    __m128i lo = _mm256_castsi256_si128(v);
    __m128i hi = _mm256_extracti128_si256(v, 1);
    __m128i pack16 = _mm_packus_epi32(lo, hi);   /* 8 uint16 */
    __m128i pack8  = _mm_packus_epi16(pack16, pack16); /* low 8 uint8 carry result */
    /* Store low 8 bytes via _mm_storel_epi64. */
    _mm_storel_epi64((__m128i*)out, pack8);
}

/* ====================================================================== *
 * E1 — AMaZE edge-direction estimator inner row, AVX2 + FMA path.
 *
 * For each row y, processes x in [5, w-5) in batches of 8 contiguous
 * output pixels. The caller provides:
 *   gray         — full image (uint32_t per pixel; layout green/2+red/4+blue/4)
 *   raw2ev       — 4-MiB int32 LUT [0..1<<20)
 *   edge_dir_row — output row pointer (uint8_t * sized w)
 *   y            — current row index (5 <= y < h-5)
 *   w            — image width
 *   s            — bright-near sign per row (±1)
 *   bright_row   — 1 iff this row is in the BRIGHT exposure
 *   raw_buffer_32 — full 20-bit raw buffer (used by predicate scan to
 *                   read raw_get_pixel32 → it's just raw_buffer_32[x+y*w])
 *   white_darkened, fullres_curve, fullres_thr — for the predicate
 *
 * The predicate logic mirrors scalar:
 *   if (!BRIGHT_ROW) {
 *     if (fullres_curve[raw[x+y*w]] > fullres_thr)  full_search = 0;
 *     else                                          full_search = 1;
 *   } else {
 *     if (raw[x+y*w] < white_darkened)              full_search = 0;
 *     else                                          full_search = 1;
 *   }
 * full_search == 0 → d_best = d0 = 5; full_search == 1 → 11-direction sweep.
 *
 * For lanes where full_search == 0, we still run the sweep (cheaper than
 * branching) and blend against d0 at the end.
 * ====================================================================== */
__attribute__((target("avx2,fma")))
static void amaze_edge_direction_estimator_row_avx2(
    int y, int w, int s, int bright_row,
    const uint32_t * gray,
    const uint32_t * raw_buffer_32,
    const int32_t * raw2ev,
    const double * fullres_curve,
    double fullres_thr_v,
    uint32_t white_darkened,
    uint8_t * edge_dir_row /* row pointer; this kernel writes [5, w-5) */)
{
    /* NUM_DIR matches the file-scope edge_directions[] table (11 entries,
     * 2 commented-out near-horizontal directions). D0 = NUM_DIR/2 = 5. */
    enum { D0 = 5, NUM_DIR = 11, SEARCH = 5, NUM_J = 11 };
    const __m256i v_d0     = _mm256_set1_epi32(D0);
    const __m256i v_intmax = _mm256_set1_epi32(INT_MAX);
    const __m256d v_thr    = _mm256_set1_pd(fullres_thr_v);
    const __m256i v_white_dark = _mm256_set1_epi32((int32_t)white_darkened);

    /* Cache the NUM_DIR edge_directions[] table values as plain ints with
     * the per-row sign `s` already folded into the y components. */
    int32_t ack_x[NUM_DIR], ack_y[NUM_DIR];
    int32_t a_x  [NUM_DIR], a_y  [NUM_DIR];
    int32_t b_x  [NUM_DIR], b_y  [NUM_DIR];
    int32_t bck_x[NUM_DIR], bck_y[NUM_DIR];
    for (int d = 0; d < NUM_DIR; d++) {
        ack_x[d] = edge_directions[d].ack.x;
        ack_y[d] = edge_directions[d].ack.y * s;
        a_x  [d] = edge_directions[d].a.x;
        a_y  [d] = edge_directions[d].a.y * s;
        b_x  [d] = edge_directions[d].b.x;
        b_y  [d] = edge_directions[d].b.y * s;
        bck_x[d] = edge_directions[d].bck.x;
        bck_y[d] = edge_directions[d].bck.y * s;
    }

    int x = 5;
    /* Process 8 lanes at a time. Tail is x > w - 13 (need 8 lanes + 5 right margin). */
    for (; x + 8 <= w - 5; x += 8) {

        /* --- Build per-lane full_search mask. ---
         * raw[x..x+7 + y*w] (uint32 → int32 for compares). */
        __m256i raw_v = _mm256_loadu_si256(
            (const __m256i*)&raw_buffer_32[(size_t)y * (size_t)w + x]);

        __m256i full_search_mask;
        if (bright_row) {
            /* full_search iff raw >= white_darkened.
             * raw and white_darkened are <2^20 so signed compare is safe. */
            __m256i ge = _mm256_cmpgt_epi32(raw_v, v_white_dark); /* raw > wd */
            __m256i eq = _mm256_cmpeq_epi32(raw_v, v_white_dark);
            full_search_mask = _mm256_or_si256(ge, eq);
        } else {
            /* full_search iff fullres_curve[raw] <= fullres_thr.
             * Gather 8 doubles (4 + 4) and compare. */
            __m128i raw_lo = _mm256_castsi256_si128(raw_v);
            __m128i raw_hi = _mm256_extracti128_si256(raw_v, 1);
            __m256d fc_lo = _mm256_i32gather_pd(fullres_curve, raw_lo, 8);
            __m256d fc_hi = _mm256_i32gather_pd(fullres_curve, raw_hi, 8);
            /* skip-search iff fc > thr; full_search = !skip. */
            __m256d skip_lo = _mm256_cmp_pd(fc_lo, v_thr, _CMP_GT_OQ);
            __m256d skip_hi = _mm256_cmp_pd(fc_hi, v_thr, _CMP_GT_OQ);
            __m256i skip_mask = pack_pd_cmp_to_epi32_mask(skip_lo, skip_hi);
            /* full_search = NOT skip. */
            full_search_mask = _mm256_xor_si256(skip_mask, _mm256_set1_epi32(-1));
        }

        /* --- Per-direction sweep. ---
         * Maintain best_e (running min) and best_d (running argmin).
         * Initialize best_e = INT_MAX so the first valid direction always wins. */
        __m256i best_e = v_intmax;
        __m256i best_d = v_d0;

        for (int d = 0; d < NUM_DIR; d++) {
            /* 11-tap reduction: e_d = sum_{j=-5..5} (|p1-p2|+|p2-p3|+|p3-p4|).
             *
             * Hoist the row-base offsets per direction (they don't depend on j or x):
             *   row_ack = (y + ack_y[d]) * w
             *   row_a   = (y + a_y  [d]) * w
             *   row_b   = (y + b_y  [d]) * w
             *   row_bck = (y + bck_y[d]) * w. */
            const ptrdiff_t row_ack = (ptrdiff_t)(y + ack_y[d]) * (ptrdiff_t)w;
            const ptrdiff_t row_a   = (ptrdiff_t)(y + a_y  [d]) * (ptrdiff_t)w;
            const ptrdiff_t row_b   = (ptrdiff_t)(y + b_y  [d]) * (ptrdiff_t)w;
            const ptrdiff_t row_bck = (ptrdiff_t)(y + bck_y[d]) * (ptrdiff_t)w;

            __m256i sum_e = _mm256_setzero_si256();

            for (int j = -SEARCH; j <= SEARCH; j++) {
                /* Linear addresses for the four taps for this (d, j):
                 *   addr1 = x + ack_x[d] + j + row_ack
                 *   addr2 = x + a_x  [d] + j + row_a
                 *   addr3 = x + b_x  [d] + j + row_b
                 *   addr4 = x + bck_x[d] + j + row_bck.
                 *
                 * Each lane k reads gray[base + k] for k in 0..7 — i.e.
                 * 8 consecutive uint32s. We can do this as a single
                 * 256-bit load (no gather needed for x). The actual gather
                 * is on raw2ev[gray[...]]. */
                const ptrdiff_t off1 = (ptrdiff_t)x + ack_x[d] + j + row_ack;
                const ptrdiff_t off2 = (ptrdiff_t)x + a_x  [d] + j + row_a;
                const ptrdiff_t off3 = (ptrdiff_t)x + b_x  [d] + j + row_b;
                const ptrdiff_t off4 = (ptrdiff_t)x + bck_x[d] + j + row_bck;

                __m256i g1 = _mm256_loadu_si256((const __m256i*)&gray[off1]);
                __m256i g2 = _mm256_loadu_si256((const __m256i*)&gray[off2]);
                __m256i g3 = _mm256_loadu_si256((const __m256i*)&gray[off3]);
                __m256i g4 = _mm256_loadu_si256((const __m256i*)&gray[off4]);

                /* gray values are uint32 with values <= max gray value
                 * (< 2^20 from the (g/2+r/4+b/4) build). Use them directly
                 * as 32-bit indices into raw2ev (which is sized [0, 1<<20)). */
                __m256i p1 = _mm256_i32gather_epi32(raw2ev, g1, 4);
                __m256i p2 = _mm256_i32gather_epi32(raw2ev, g2, 4);
                __m256i p3 = _mm256_i32gather_epi32(raw2ev, g3, 4);
                __m256i p4 = _mm256_i32gather_epi32(raw2ev, g4, 4);

                /* Three abs-diffs. raw2ev values fit in int32 (range ~[-655360, 917504]). */
                __m256i d12 = _mm256_abs_epi32(_mm256_sub_epi32(p1, p2));
                __m256i d23 = _mm256_abs_epi32(_mm256_sub_epi32(p2, p3));
                __m256i d34 = _mm256_abs_epi32(_mm256_sub_epi32(p3, p4));

                sum_e = _mm256_add_epi32(sum_e, d12);
                sum_e = _mm256_add_epi32(sum_e, d23);
                sum_e = _mm256_add_epi32(sum_e, d34);
            }

            /* Add the diagonal penalty (constant per direction). */
            __m256i pen = _mm256_set1_epi32(kAmazeDiagPenalty[d]);
            sum_e = _mm256_add_epi32(sum_e, pen);

            /* argmin step: if sum_e < best_e, update. Tie-break favors
             * the earlier-evaluated d (matches scalar `<` not `<=`). */
            __m256i d_vec = _mm256_set1_epi32(d);
            amaze_argmin_step_avx2(&best_e, &best_d, sum_e, d_vec);
        }

        /* Blend: lanes where full_search_mask == 0 get d0. */
        __m256i out = _mm256_blendv_epi8(v_d0, best_d, full_search_mask);

        /* Store 8 uint8s into edge_dir_row[x..x+7]. */
        amaze_store_8_bytes_avx2(&edge_dir_row[x], out);
    }

    /* Tail: scalar reference for x in [x, w-5). Mirrors the scalar code
     * exactly. Use plain int arithmetic (matching the original) so negative
     * tap offsets (x + ack_x + j can be < 0) don't wrap-around through
     * size_t promotion. */
    for (; x < w - 5; x++) {
        int e_best = INT_MAX;
        int d_best = D0;
        int dmin = 0, dmax = NUM_DIR - 1;

        if (!bright_row) {
            if (fullres_curve[raw_buffer_32[x + y * w]] > fullres_thr_v) {
                dmin = D0; dmax = D0;
            }
        } else if (raw_buffer_32[x + y * w] < white_darkened) {
            dmin = D0; dmax = D0;
        }

        if (dmin == dmax) {
            d_best = dmin;
        } else {
            for (int d = dmin; d <= dmax; d++) {
                int e = 0;
                for (int j = -SEARCH; j <= SEARCH; j++) {
                    int p1 = raw2ev[gray[x+ack_x[d]+j + (y+ack_y[d]) * w]];
                    int p2 = raw2ev[gray[x+a_x  [d]+j + (y+a_y  [d]) * w]];
                    int p3 = raw2ev[gray[x+b_x  [d]+j + (y+b_y  [d]) * w]];
                    int p4 = raw2ev[gray[x+bck_x[d]+j + (y+bck_y[d]) * w]];
                    int ad12 = p1 > p2 ? p1 - p2 : p2 - p1;
                    int ad23 = p2 > p3 ? p2 - p3 : p3 - p2;
                    int ad34 = p3 > p4 ? p3 - p4 : p4 - p3;
                    e += ad12 + ad23 + ad34;
                }
                e += kAmazeDiagPenalty[d];
                if (e < e_best) { e_best = e; d_best = d; }
            }
        }
        edge_dir_row[x] = (uint8_t)d_best;
    }
}

#endif /* DUALISO_AVX2_AVAILABLE */

#endif /* DUALISO_AMAZE_AVX2_KERNELS_INCLUDED */
