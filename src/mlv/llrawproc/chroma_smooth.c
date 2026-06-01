#ifdef CHROMA_SMOOTH_2X2
#define CHROMA_SMOOTH_FUNC chroma_smooth_2x2
#define CHROMA_SMOOTH_MAX_XY_IJ 2
#define CHROMA_SMOOTH_FILTER_SIZE 5
#define CHROMA_SMOOTH_MEDIAN opt_med5
#elif defined(CHROMA_SMOOTH_3X3)
#define CHROMA_SMOOTH_FUNC chroma_smooth_3x3
#define CHROMA_SMOOTH_MAX_XY_IJ 2
#define CHROMA_SMOOTH_FILTER_SIZE 9
#define CHROMA_SMOOTH_MEDIAN opt_med9
#else
#define CHROMA_SMOOTH_FUNC chroma_smooth_5x5
#define CHROMA_SMOOTH_MAX_XY_IJ 4
#define CHROMA_SMOOTH_FILTER_SIZE 25
#define CHROMA_SMOOTH_MEDIAN opt_med25
#endif

#ifndef CHROMA_SMOOTH_TYPE
#define CHROMA_SMOOTH_TYPE uint16_t
#endif

#ifndef LIKELY
#if defined(__GNUC__) || defined(__clang__)
#define LIKELY(x)   __builtin_expect(!!(x), 1)
#define UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define LIKELY(x)   (x)
#define UNLIKELY(x) (x)
#endif
#endif

#ifdef CHROMA_SMOOTH_2X2
static inline int chroma_smooth_med5(int a0, int a1, int a2, int a3, int a4)
{
    #define CS2_SWAP(lo, hi) do { \
        const int _cs2_lo = MIN((lo), (hi)); \
        const int _cs2_hi = MAX((lo), (hi)); \
        (lo) = _cs2_lo; \
        (hi) = _cs2_hi; \
    } while (0)

    CS2_SWAP(a0, a1);
    CS2_SWAP(a3, a4);
    CS2_SWAP(a0, a3);
    CS2_SWAP(a1, a4);
    CS2_SWAP(a1, a2);
    CS2_SWAP(a2, a3);
    CS2_SWAP(a1, a2);

    #undef CS2_SWAP
    return a2;
}

static inline uint16_t chroma_smooth_ev2raw_lookup(const int * ev2raw, int ev)
{
    const int lo = -10 * EV_RESOLUTION;
    const int hi = 14 * EV_RESOLUTION - 1;
    if (LIKELY((unsigned int)(ev - lo) <= (unsigned int)(hi - lo)))
    {
        return (uint16_t)ev2raw[ev];
    }
    ev = ev < lo ? lo : hi;
    return (uint16_t)ev2raw[ev];
}

static void CHROMA_SMOOTH_FUNC(int w,
                               int h,
                               CHROMA_SMOOTH_TYPE * __restrict inp,
                               CHROMA_SMOOTH_TYPE * __restrict out,
                               int* __restrict raw2ev,
                               int* __restrict ev2raw,
                               int black,
                               int white)
{
    int x,y;

#pragma omp parallel for
    for (y = 4; y < h-5; y += 2)
    {
        const int black_thr = black + 64;
        const unsigned int white_u = (unsigned int)white;
        const int probe_mode = dualiso_mix_chroma_probe_mode();
        const int probe_detail = probe_mode >= 0;
        const int probe_horiz = probe_detail && (probe_mode == 0 || probe_mode == 1);
        const int probe_vert = probe_detail && (probe_mode == 0 || probe_mode == 2);
        const int probe_stage = g_dualiso_full20bit_timing.mix_chroma_probe_stage;
        const int probe_center_fullres =
            probe_detail && (probe_mode == 0 || probe_mode == 3 || probe_mode == 5 || probe_mode == 6) && probe_stage == 1;
        const int probe_center_halfres =
            probe_detail && (probe_mode == 0 || probe_mode == 3 || probe_mode == 5 || probe_mode == 6 || probe_mode == 9 || probe_mode == 10 || probe_mode == 13) && probe_stage == 2;
        const int probe_center = probe_center_fullres || probe_center_halfres;
        const int probe_average_branch = probe_detail && probe_mode == 5;
        const int probe_non_average_branch = probe_detail && probe_mode == 6;
        const int probe_non_average_choose_true_branch = probe_detail && probe_mode == 7;
        const int probe_non_average_choose_false_branch = probe_detail && probe_mode == 8;
        const int probe_non_average_write_r_branch = probe_detail && probe_mode == 9 && probe_center_halfres;
        const int probe_non_average_write_b_branch = probe_detail && probe_mode == 10 && probe_center_halfres;
        const int probe_non_average_store_r_clamp_branch = probe_detail && probe_mode == 11 && probe_center_halfres;
        const int probe_non_average_store_b_clamp_branch = probe_detail && probe_mode == 12 && probe_center_halfres;
        const int probe_non_average_write_both_branch = probe_detail && probe_mode == 13 && probe_center_halfres;
        const int ev2raw_lo = -10 * EV_RESOLUTION;
        const int ev2raw_hi = 14 * EV_RESOLUTION - 1;
        const CHROMA_SMOOTH_TYPE *row_y_m3 = inp + (size_t)(y - 3) * (size_t)w;
        const CHROMA_SMOOTH_TYPE *row_y_m2 = inp + (size_t)(y - 2) * (size_t)w;
        const CHROMA_SMOOTH_TYPE *row_y_m1 = inp + (size_t)(y - 1) * (size_t)w;
        const CHROMA_SMOOTH_TYPE *row_y = inp + (size_t)y * (size_t)w;
        const CHROMA_SMOOTH_TYPE *row_y_p1 = row_y + w;
        const CHROMA_SMOOTH_TYPE *row_y_p2 = inp + (size_t)(y + 2) * (size_t)w;
        const CHROMA_SMOOTH_TYPE *row_y_p3 = inp + (size_t)(y + 3) * (size_t)w;
        const CHROMA_SMOOTH_TYPE *row_y_p4 = inp + (size_t)(y + 4) * (size_t)w;
        CHROMA_SMOOTH_TYPE *out_y = out + (size_t)y * (size_t)w;
        CHROMA_SMOOTH_TYPE *out_y_p1 = out_y + w;

        for (x = 4; x < w-4; x += 2)
        {
            int r0 = row_y[x];
            int b0 = row_y_p1[x + 1];
            const int write_r = (unsigned int)r0 < white_u;
            const int write_b = (unsigned int)b0 < white_u;
            if (UNLIKELY(!write_r && !write_b))
            {
                if (probe_center)
                {
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_write_none_count += 1.0;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_write_none_count += 1.0;
                    }
                }
                continue;
            }

            int med_r[5];
            int med_b[5];
            int eh = 0;
            int ev = 0;
            double chroma_horiz_start = 0.0;
            double chroma_vert_start = 0.0;
            double chroma_center_start = 0.0;

            #define CS2_SAMPLE_H(k, row0, row1, x0) do { \
                const int r = row0[x0]; \
                const int b = row1[x0 + 1]; \
                const int rr = raw2ev[r]; \
                const int bb = raw2ev[b]; \
                const int g1 = raw2ev[row0[x0 + 1]]; \
                const int g2 = raw2ev[row1[x0]]; \
                const int g3 = raw2ev[row0[x0 - 1]]; \
                const int g5 = raw2ev[row1[x0 + 2]]; \
                const int gr = (g1 + g3) / 2; \
                const int gb = (g2 + g5) / 2; \
                eh += ABS(g1 - g3) + ABS(g2 - g5); \
                med_r[(k)] = rr - gr; \
                med_b[(k)] = bb - gb; \
            } while (0)

            if (probe_horiz) chroma_horiz_start = mlv_stage_timing_now();
            CS2_SAMPLE_H(0, row_y, row_y_p1, x - 2);
            CS2_SAMPLE_H(1, row_y_m2, row_y_m1, x);
            CS2_SAMPLE_H(2, row_y, row_y_p1, x);
            CS2_SAMPLE_H(3, row_y_p2, row_y_p3, x);
            CS2_SAMPLE_H(4, row_y, row_y_p1, x + 2);
            #undef CS2_SAMPLE_H

            const int drh = chroma_smooth_med5(med_r[0], med_r[1], med_r[2], med_r[3], med_r[4]);
            const int dbh = chroma_smooth_med5(med_b[0], med_b[1], med_b[2], med_b[3], med_b[4]);
            if (probe_horiz)
            {
                g_dualiso_full20bit_timing.mix_chroma_horiz_probe_ms +=
                    (mlv_stage_timing_now() - chroma_horiz_start) * 1000.0;
            }

            #define CS2_SAMPLE_V(k, row0, row1, rowm1, rowp2, x0) do { \
                const int r = row0[x0]; \
                const int b = row1[x0 + 1]; \
                const int rr = raw2ev[r]; \
                const int bb = raw2ev[b]; \
                const int g1 = raw2ev[row0[x0 + 1]]; \
                const int g2 = raw2ev[row1[x0]]; \
                const int g4 = raw2ev[rowm1[x0]]; \
                const int g6 = raw2ev[rowp2[x0 + 1]]; \
                const int gr = (g2 + g4) / 2; \
                const int gb = (g1 + g6) / 2; \
                ev += ABS(g2 - g4) + ABS(g1 - g6); \
                med_r[(k)] = rr - gr; \
                med_b[(k)] = bb - gb; \
            } while (0)

            if (probe_vert) chroma_vert_start = mlv_stage_timing_now();
            CS2_SAMPLE_V(0, row_y, row_y_p1, row_y_m1, row_y_p2, x - 2);
            CS2_SAMPLE_V(1, row_y_m2, row_y_m1, row_y_m3, row_y, x);
            CS2_SAMPLE_V(2, row_y, row_y_p1, row_y_m1, row_y_p2, x);
            CS2_SAMPLE_V(3, row_y_p2, row_y_p3, row_y_p1, row_y_p4, x);
            CS2_SAMPLE_V(4, row_y, row_y_p1, row_y_m1, row_y_p2, x + 2);
            #undef CS2_SAMPLE_V

            const int drv = chroma_smooth_med5(med_r[0], med_r[1], med_r[2], med_r[3], med_r[4]);
            const int dbv = chroma_smooth_med5(med_b[0], med_b[1], med_b[2], med_b[3], med_b[4]);
            if (probe_vert)
            {
                g_dualiso_full20bit_timing.mix_chroma_vert_probe_ms +=
                    (mlv_stage_timing_now() - chroma_vert_start) * 1000.0;
            }

            /* Reuse the row pointers already prepared for this pass so the
             * center-pixel blend does not keep rebuilding the same y*w
             * address arithmetic inside the hot inner loop. */
            if (probe_center) chroma_center_start = mlv_stage_timing_now();
            double chroma_center_gather_start = 0.0;
            double chroma_center_arithmetic_start = 0.0;
            double chroma_center_store_start = 0.0;
            double chroma_center_store_r_start = 0.0;
            double chroma_center_store_b_start = 0.0;
            double chroma_center_store_r_lookup_start = 0.0;
            double chroma_center_store_b_lookup_start = 0.0;
            double chroma_center_store_lookup_elapsed_ms = 0.0;
            double chroma_center_average_start = 0.0;
            double chroma_center_non_average_start = 0.0;
            double chroma_center_non_average_choose_start = 0.0;
            double chroma_center_non_average_write_r_start = 0.0;
            double chroma_center_non_average_write_b_start = 0.0;
            double chroma_center_non_average_write_both_start = 0.0;
            if (probe_center) chroma_center_gather_start = mlv_stage_timing_now();
            if (probe_center)
            {
                if (write_r && write_b)
                {
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_write_both_count += 1.0;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_write_both_count += 1.0;
                    }
                }
                else if (write_r)
                {
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_write_r_only_count += 1.0;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_write_r_only_count += 1.0;
                    }
                }
                else if (write_b)
                {
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_write_b_only_count += 1.0;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_write_b_only_count += 1.0;
                    }
                }
                else
                {
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_write_none_count += 1.0;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_write_none_count += 1.0;
                    }
                }
            }
            int g1 = raw2ev[row_y[x+1]];
            int g2 = raw2ev[row_y_p1[x]];
            int g3 = raw2ev[row_y[x-1]];
            int g4 = raw2ev[row_y_m1[x]];
            int g5 = raw2ev[row_y_p1[x+2]];
            int g6 = raw2ev[row_y_p2[x+1]];
            if (probe_center)
            {
                const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_gather_start) * 1000.0;
                if (probe_center_fullres)
                {
                    g_dualiso_full20bit_timing.mix_chroma_center_gather_probe_ms += elapsed_ms;
                }
                else
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_gather_probe_ms += elapsed_ms;
                }
                chroma_center_arithmetic_start = mlv_stage_timing_now();
            }

            int grv = (g2+g4)/2;
            int grh = (g1+g3)/2;
            int gbv = (g1+g6)/2;
            int gbh = (g2+g5)/2;
            const int choose_ev_lt_eh = ev < eh;
            if (probe_center)
            {
                if (choose_ev_lt_eh)
                {
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_choose_ev_lt_eh_count += 1.0;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_choose_ev_lt_eh_count += 1.0;
                    }
                }
            }
            int dr = choose_ev_lt_eh ? drv : drh;
            int db = choose_ev_lt_eh ? dbv : dbh;
            int thr = 64;
            const int use_average = r0 < black_thr || b0 < black_thr || ABS(drv - drh) < thr || ABS(grv-grh) < thr || ABS(gbv-gbh) < thr;
            if (probe_center && use_average)
            {
                if (probe_center_fullres)
                {
                    g_dualiso_full20bit_timing.mix_chroma_center_use_average_count += 1.0;
                }
                else
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_use_average_count += 1.0;
                }
            }
            const int probe_non_average_choose_branch =
                !use_average
                && ((probe_non_average_choose_true_branch && choose_ev_lt_eh)
                 || (probe_non_average_choose_false_branch && !choose_ev_lt_eh));
            if (probe_non_average_choose_branch)
            {
                chroma_center_non_average_choose_start = mlv_stage_timing_now();
            }

            int gr = 0;
            int gb = 0;
            if (write_r && write_b)
            {
                if (use_average)
                {
                    dr = (drv+drh)/2;
                    gr = (g1+g2+g3+g4)/4;
                    db = (dbv+dbh)/2;
                    gb = (g1+g2+g5+g6)/4;
                }
                else
                {
                    if (choose_ev_lt_eh)
                    {
                        gr = grv;
                        gb = gbv;
                    }
                    else
                    {
                        gr = grh;
                        gb = gbh;
                    }
                }
            }
            else
            {
                if (write_r)
                {
                    if (use_average)
                    {
                        dr = (drv+drh)/2;
                        gr = (g1+g2+g3+g4)/4;
                    }
                    else
                    {
                        if (choose_ev_lt_eh)
                        {
                            gr = grv;
                        }
                        else
                        {
                            gr = grh;
                        }
                    }
                }
                if (write_b)
                {
                    if (use_average)
                    {
                        db = (dbv+dbh)/2;
                        gb = (g1+g2+g5+g6)/4;
                    }
                    else
                    {
                        if (choose_ev_lt_eh)
                        {
                            gb = gbv;
                        }
                        else
                        {
                            gb = gbh;
                        }
                    }
                }
            }
            if (probe_center)
            {
                const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_arithmetic_start) * 1000.0;
                if (probe_center_fullres)
                {
                    g_dualiso_full20bit_timing.mix_chroma_center_arithmetic_probe_ms += elapsed_ms;
                }
                else
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_arithmetic_probe_ms += elapsed_ms;
                }
                chroma_center_store_start = mlv_stage_timing_now();
            }
            if (probe_non_average_write_both_branch && write_r && write_b)
            {
                chroma_center_non_average_write_both_start = mlv_stage_timing_now();
            }

            if (LIKELY(write_r && write_b) && !use_average)
            {
                uint16_t out_r = 0;
                uint16_t out_b = 0;
                const int out_r_ev = gr + dr;
                const int out_b_ev = gr + db;
                const unsigned int ev2raw_range = (unsigned int)(ev2raw_hi - ev2raw_lo);
                const unsigned int out_r_ev_offset = (unsigned int)(out_r_ev - ev2raw_lo);
                const unsigned int out_b_ev_offset = (unsigned int)(out_b_ev - ev2raw_lo);

                if (probe_non_average_write_both_branch)
                {
                    chroma_center_non_average_write_both_start = mlv_stage_timing_now();
                }

                if (!probe_center)
                {
                    if (LIKELY(out_r_ev_offset <= ev2raw_range && out_b_ev_offset <= ev2raw_range))
                    {
                        out_r = (uint16_t)ev2raw[out_r_ev];
                        out_b = (uint16_t)ev2raw[out_b_ev];
                    }
                    else
                    {
                        const int out_r_ev_clamped =
                            out_r_ev < ev2raw_lo ? ev2raw_lo : (out_r_ev > ev2raw_hi ? ev2raw_hi : out_r_ev);
                        const int out_b_ev_clamped =
                            out_b_ev < ev2raw_lo ? ev2raw_lo : (out_b_ev > ev2raw_hi ? ev2raw_hi : out_b_ev);
                        out_r = (uint16_t)ev2raw[out_r_ev_clamped];
                        out_b = (uint16_t)ev2raw[out_b_ev_clamped];
                    }
                }
                else
                {
                    if (probe_center) chroma_center_store_r_lookup_start = mlv_stage_timing_now();
                    out_r = chroma_smooth_ev2raw_lookup(ev2raw, out_r_ev);
                    if (probe_center)
                    {
                        const double lookup_elapsed_ms =
                            (mlv_stage_timing_now() - chroma_center_store_r_lookup_start) * 1000.0;
                        const double write_start = mlv_stage_timing_now();
                        if (probe_center_fullres)
                        {
                            g_dualiso_full20bit_timing.mix_chroma_center_store_r_lookup_probe_ms += lookup_elapsed_ms;
                        }
                        else
                        {
                            g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_r_lookup_probe_ms += lookup_elapsed_ms;
                        }
                        chroma_center_store_lookup_elapsed_ms += lookup_elapsed_ms;
                        chroma_center_store_r_start = write_start;
                    }
                    if (probe_non_average_branch)
                    {
                        chroma_center_non_average_start = mlv_stage_timing_now();
                    }
                    out_y[x] = out_r;
                    if (probe_center)
                    {
                        const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_store_r_start) * 1000.0;
                        if (probe_center_fullres)
                        {
                            g_dualiso_full20bit_timing.mix_chroma_center_store_r_probe_ms += elapsed_ms;
                        }
                        else
                        {
                            g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_r_probe_ms += elapsed_ms;
                        }
                    }
                    if (probe_center) chroma_center_store_b_lookup_start = mlv_stage_timing_now();
                    out_b = chroma_smooth_ev2raw_lookup(ev2raw, out_b_ev);
                    if (probe_center)
                    {
                        const double lookup_elapsed_ms =
                            (mlv_stage_timing_now() - chroma_center_store_b_lookup_start) * 1000.0;
                        const double write_start = mlv_stage_timing_now();
                        if (probe_center_fullres)
                        {
                            g_dualiso_full20bit_timing.mix_chroma_center_store_b_lookup_probe_ms += lookup_elapsed_ms;
                        }
                        else
                        {
                            g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_b_lookup_probe_ms += lookup_elapsed_ms;
                        }
                        chroma_center_store_lookup_elapsed_ms += lookup_elapsed_ms;
                        chroma_center_store_b_start = write_start;
                    }
                    if (probe_non_average_branch)
                    {
                        chroma_center_non_average_start = mlv_stage_timing_now();
                    }
                    out_y_p1[x + 1] = out_b;
                    if (probe_center)
                    {
                        const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_store_b_start) * 1000.0;
                        if (probe_center_fullres)
                        {
                            g_dualiso_full20bit_timing.mix_chroma_center_store_b_probe_ms += elapsed_ms;
                        }
                        else
                        {
                            g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_b_probe_ms += elapsed_ms;
                        }
                    }
                }
                goto chroma_mix_store_done;
            }

            if (write_r && write_b)
            {
                uint16_t out_r = 0;
                uint16_t out_b = 0;
                const int out_r_ev = gr + dr;
                const int out_b_ev = gr + db;
                const unsigned int ev2raw_range = (unsigned int)(ev2raw_hi - ev2raw_lo);

                if (probe_average_branch)
                {
                    chroma_center_average_start = mlv_stage_timing_now();
                }

                if (!probe_center &&
                    LIKELY((unsigned int)(out_r_ev - ev2raw_lo) <= ev2raw_range &&
                           (unsigned int)(out_b_ev - ev2raw_lo) <= ev2raw_range))
                {
                    out_r = (uint16_t)ev2raw[out_r_ev];
                    out_b = (uint16_t)ev2raw[out_b_ev];
                }
                else
                {
                    if (probe_center) chroma_center_store_r_lookup_start = mlv_stage_timing_now();
                    if (probe_center)
                    {
                        out_r = chroma_smooth_ev2raw_lookup(ev2raw, out_r_ev);
                        const double lookup_elapsed_ms =
                            (mlv_stage_timing_now() - chroma_center_store_r_lookup_start) * 1000.0;
                        const double write_start = mlv_stage_timing_now();
                        if (probe_center_fullres)
                        {
                            g_dualiso_full20bit_timing.mix_chroma_center_store_r_lookup_probe_ms += lookup_elapsed_ms;
                        }
                        else
                        {
                            g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_r_lookup_probe_ms += lookup_elapsed_ms;
                        }
                        chroma_center_store_lookup_elapsed_ms += lookup_elapsed_ms;
                        chroma_center_store_r_start = write_start;
                    }
                    else
                    {
                        const int out_r_ev_clamped =
                            out_r_ev < ev2raw_lo ? ev2raw_lo : (out_r_ev > ev2raw_hi ? ev2raw_hi : out_r_ev);
                        out_r = (uint16_t)ev2raw[out_r_ev_clamped];
                    }
                    if (probe_average_branch)
                    {
                        chroma_center_average_start = mlv_stage_timing_now();
                    }
                    out_y[x] = out_r;
                    if (probe_center)
                    {
                        const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_store_r_start) * 1000.0;
                        if (probe_center_fullres)
                        {
                            g_dualiso_full20bit_timing.mix_chroma_center_store_r_probe_ms += elapsed_ms;
                        }
                        else
                        {
                            g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_r_probe_ms += elapsed_ms;
                        }
                    }
                    if (probe_center) chroma_center_store_b_lookup_start = mlv_stage_timing_now();
                    if (probe_center)
                    {
                        out_b = chroma_smooth_ev2raw_lookup(ev2raw, out_b_ev);
                        const double lookup_elapsed_ms =
                            (mlv_stage_timing_now() - chroma_center_store_b_lookup_start) * 1000.0;
                        const double write_start = mlv_stage_timing_now();
                        if (probe_center_fullres)
                        {
                            g_dualiso_full20bit_timing.mix_chroma_center_store_b_lookup_probe_ms += lookup_elapsed_ms;
                        }
                        else
                        {
                            g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_b_lookup_probe_ms += lookup_elapsed_ms;
                        }
                        chroma_center_store_lookup_elapsed_ms += lookup_elapsed_ms;
                        chroma_center_store_b_start = write_start;
                    }
                    else
                    {
                        const int out_b_ev_clamped =
                            out_b_ev < ev2raw_lo ? ev2raw_lo : (out_b_ev > ev2raw_hi ? ev2raw_hi : out_b_ev);
                        out_b = (uint16_t)ev2raw[out_b_ev_clamped];
                    }
                    if (probe_average_branch)
                    {
                        chroma_center_average_start = mlv_stage_timing_now();
                    }
                    out_y_p1[x + 1] = out_b;
                    if (probe_center)
                    {
                        const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_store_b_start) * 1000.0;
                        if (probe_center_fullres)
                        {
                            g_dualiso_full20bit_timing.mix_chroma_center_store_b_probe_ms += elapsed_ms;
                        }
                        else
                        {
                            g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_b_probe_ms += elapsed_ms;
                        }
                    }
                }
                goto chroma_mix_store_done;
            }

            if (write_r)
            {
chroma_mix_store_r_path:
                if (probe_non_average_write_r_branch && !use_average)
                {
                    chroma_center_non_average_write_r_start = mlv_stage_timing_now();
                }
                if (probe_non_average_store_r_clamp_branch && !use_average && (gr + dr) < ev2raw_lo)
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_r_low_clamp_count += 1.0;
                }
                if (probe_non_average_store_r_clamp_branch && !use_average && (gr + dr) > ev2raw_hi)
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_r_high_clamp_count += 1.0;
                }
                uint16_t out_r = 0;
                if (probe_center) chroma_center_store_r_lookup_start = mlv_stage_timing_now();
                if (probe_center)
                {
                    out_r = chroma_smooth_ev2raw_lookup(ev2raw, gr + dr);
                    const double lookup_elapsed_ms =
                        (mlv_stage_timing_now() - chroma_center_store_r_lookup_start) * 1000.0;
                    const double write_start = mlv_stage_timing_now();
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_store_r_lookup_probe_ms += lookup_elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_r_lookup_probe_ms += lookup_elapsed_ms;
                    }
                    chroma_center_store_lookup_elapsed_ms += lookup_elapsed_ms;
                    chroma_center_store_r_start = write_start;
                }
                else
                {
                    const int out_r_ev = gr + dr;
                    const int out_r_ev_clamped =
                        out_r_ev < ev2raw_lo ? ev2raw_lo : (out_r_ev > ev2raw_hi ? ev2raw_hi : out_r_ev);
                    out_r = (uint16_t)ev2raw[out_r_ev_clamped];
                }
                if (probe_average_branch && use_average)
                {
                    chroma_center_average_start = mlv_stage_timing_now();
                }
                else if (probe_non_average_branch && !use_average)
                {
                    chroma_center_non_average_start = mlv_stage_timing_now();
                }
                out_y[x] = out_r;
                if (probe_center)
                {
                    const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_store_r_start) * 1000.0;
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_store_r_probe_ms += elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_r_probe_ms += elapsed_ms;
                    }
                }
                if (probe_average_branch && use_average)
                {
                    const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_average_start) * 1000.0;
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_average_probe_ms += elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_average_probe_ms += elapsed_ms;
                    }
                }
                else if (probe_non_average_branch && !use_average)
                {
                    const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_non_average_start) * 1000.0;
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_non_average_probe_ms += elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_non_average_probe_ms += elapsed_ms;
                    }
                }
                if (probe_non_average_write_r_branch && !use_average)
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_non_average_write_r_probe_ms +=
                        (mlv_stage_timing_now() - chroma_center_non_average_write_r_start) * 1000.0;
                }
                if (write_b)
                {
                    goto chroma_mix_store_b_path;
                }
            }

            if (write_b)
            {
chroma_mix_store_b_path:
                if (probe_non_average_write_b_branch && !use_average)
                {
                    chroma_center_non_average_write_b_start = mlv_stage_timing_now();
                }
                if (probe_non_average_store_b_clamp_branch && !use_average && (gb + db) < ev2raw_lo)
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_b_low_clamp_count += 1.0;
                }
                if (probe_non_average_store_b_clamp_branch && !use_average && (gb + db) > ev2raw_hi)
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_b_high_clamp_count += 1.0;
                }
                uint16_t out_b = 0;
                if (probe_center) chroma_center_store_b_lookup_start = mlv_stage_timing_now();
                if (probe_center)
                {
                    out_b = chroma_smooth_ev2raw_lookup(ev2raw, gb + db);
                    const double lookup_elapsed_ms =
                        (mlv_stage_timing_now() - chroma_center_store_b_lookup_start) * 1000.0;
                    const double write_start = mlv_stage_timing_now();
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_store_b_lookup_probe_ms += lookup_elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_b_lookup_probe_ms += lookup_elapsed_ms;
                    }
                    chroma_center_store_lookup_elapsed_ms += lookup_elapsed_ms;
                    chroma_center_store_b_start = write_start;
                }
                else
                {
                    const int out_b_ev = gb + db;
                    const int out_b_ev_clamped =
                        out_b_ev < ev2raw_lo ? ev2raw_lo : (out_b_ev > ev2raw_hi ? ev2raw_hi : out_b_ev);
                    out_b = (uint16_t)ev2raw[out_b_ev_clamped];
                }
                if (probe_average_branch && use_average)
                {
                    chroma_center_average_start = mlv_stage_timing_now();
                }
                else if (probe_non_average_branch && !use_average)
                {
                    chroma_center_non_average_start = mlv_stage_timing_now();
                }
                out_y_p1[x + 1] = out_b;
                if (probe_center)
                {
                    const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_store_b_start) * 1000.0;
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_store_b_probe_ms += elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_b_probe_ms += elapsed_ms;
                    }
                }
                if (probe_average_branch && use_average)
                {
                    const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_average_start) * 1000.0;
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_average_probe_ms += elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_average_probe_ms += elapsed_ms;
                    }
                }
                else if (probe_non_average_branch && !use_average)
                {
                    const double elapsed_ms = (mlv_stage_timing_now() - chroma_center_non_average_start) * 1000.0;
                    if (probe_center_fullres)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_non_average_probe_ms += elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_non_average_probe_ms += elapsed_ms;
                    }
                }
                if (probe_non_average_write_b_branch && !use_average)
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_non_average_write_b_probe_ms +=
                        (mlv_stage_timing_now() - chroma_center_non_average_write_b_start) * 1000.0;
                }
                goto chroma_mix_store_done;
            }
chroma_mix_store_done:
            if (probe_non_average_write_both_branch && write_r && write_b)
            {
                g_dualiso_full20bit_timing.mix_chroma_halfres_center_non_average_write_both_probe_ms +=
                    (mlv_stage_timing_now() - chroma_center_non_average_write_both_start) * 1000.0;
            }
            if (probe_non_average_choose_branch)
            {
                const double elapsed_ms =
                    (mlv_stage_timing_now() - chroma_center_non_average_choose_start) * 1000.0;
                if (probe_center_fullres)
                {
                    if (choose_ev_lt_eh)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_non_average_choose_true_probe_ms += elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_center_non_average_choose_false_probe_ms += elapsed_ms;
                    }
                }
                else
                {
                    if (choose_ev_lt_eh)
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_non_average_choose_true_probe_ms += elapsed_ms;
                    }
                    else
                    {
                        g_dualiso_full20bit_timing.mix_chroma_halfres_center_non_average_choose_false_probe_ms += elapsed_ms;
                    }
                }
            }
            if (probe_center)
            {
                double store_elapsed_ms = (mlv_stage_timing_now() - chroma_center_store_start) * 1000.0;
                if (store_elapsed_ms > chroma_center_store_lookup_elapsed_ms)
                {
                    store_elapsed_ms -= chroma_center_store_lookup_elapsed_ms;
                }
                else
                {
                    store_elapsed_ms = 0.0;
                }
                const double total_elapsed_ms = (mlv_stage_timing_now() - chroma_center_start) * 1000.0;
                if (probe_center_fullres)
                {
                    g_dualiso_full20bit_timing.mix_chroma_center_store_probe_ms += store_elapsed_ms;
                    g_dualiso_full20bit_timing.mix_chroma_center_probe_ms += total_elapsed_ms;
                }
                else
                {
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_store_probe_ms += store_elapsed_ms;
                    g_dualiso_full20bit_timing.mix_chroma_halfres_center_probe_ms += total_elapsed_ms;
                }
            }
        }
    }
}

#else
static void CHROMA_SMOOTH_FUNC(int w,
                               int h,
                               CHROMA_SMOOTH_TYPE * __restrict inp,
                               CHROMA_SMOOTH_TYPE * __restrict out,
                               int* __restrict raw2ev,
                               int* __restrict ev2raw,
                               int black,
                               int white)
{
    int x,y;

    #pragma omp parallel for collapse(2)
    for (y = 2+CHROMA_SMOOTH_MAX_XY_IJ; y < h-3-CHROMA_SMOOTH_MAX_XY_IJ; y += 2)
    {
        for (x = 2+CHROMA_SMOOTH_MAX_XY_IJ; x < w-2-CHROMA_SMOOTH_MAX_XY_IJ; x += 2)
        {
            /**
             * for each red pixel, compute the median value of red minus interpolated green at the same location
             * the median value is then considered the "true" difference between red and green
             * same for blue vs green
             * 
             *
             * each red pixel has 4 green neighbours, so we may interpolate as follows:
             * - mean or median(t,b,l,r)
             * - choose between mean(t,b) and mean(l,r) (idea from AHD)
             * 
             * same for blue; note that a RG/GB cell has 6 green pixels that we need to analyze
             * 2 only for red, 2 only for blue, and 2 shared
             *    g
             *   gRg
             *    gBg
             *     g
             *
             * choosing the interpolation direction seems to give cleaner results
             * the direction is choosen over the entire filtered area (so we do two passes, one for each direction, 
             * and at the end choose the one for which total interpolation error is smaller)
             * 
             * error = sum(abs(t-b)) or sum(abs(l-r))
             * 
             * interpolation in EV space (rather than linear) seems to have less color artifacts in high-contrast areas
             * 
             * we can use this filter for 3x3 RG/GB cells or 5x5
             */
            int i,j;
            int k = 0;
            int med_r[CHROMA_SMOOTH_FILTER_SIZE];
            int med_b[CHROMA_SMOOTH_FILTER_SIZE];
            
            /* first try to interpolate in horizontal direction */
            int eh = 0;
            for (i = -CHROMA_SMOOTH_MAX_XY_IJ; i <= CHROMA_SMOOTH_MAX_XY_IJ; i += 2)
            {
                for (j = -CHROMA_SMOOTH_MAX_XY_IJ; j <= CHROMA_SMOOTH_MAX_XY_IJ; j += 2)
                {
                    #ifdef CHROMA_SMOOTH_2X2
                    if (ABS(i) + ABS(j) == 4)
                        continue;
                    #endif
                    
                    int r  = inp[x+i   +   (y+j) * w];
                    int b  = inp[x+i+1 + (y+j+1) * w];
                                                        /*  for R      for B      */
                    int g1 = inp[x+i+1 +   (y+j) * w];  /*  Right      Top        */
                    int g2 = inp[x+i   + (y+j+1) * w];  /*  Bottom     Left       */
                    int g3 = inp[x+i-1 +   (y+j) * w];  /*  Left                  */ 
                  //int g4 = inp[x+i   + (y+j-1) * w];  /*  Top                   */
                    int g5 = inp[x+i+2 + (y+j+1) * w];  /*             Right      */
                  //int g6 = inp[x+i+1 + (y+j+2) * w];  /*             Bottom     */
                    
                    g1 = raw2ev[g1];
                    g2 = raw2ev[g2];
                    g3 = raw2ev[g3];
                  //g4 = raw2ev[g4];
                    g5 = raw2ev[g5];
                  //g6 = raw2ev[g6];
                    
                    int gr = (g1+g3)/2;
                    int gb = (g2+g5)/2;
                    eh += ABS(g1-g3) + ABS(g2-g5);
                    med_r[k] = raw2ev[r] - gr;
                    med_b[k] = raw2ev[b] - gb;
                    k++;
                }
            }

            /* difference from green, with horizontal interpolation */
            int drh = CHROMA_SMOOTH_MEDIAN(med_r);
            int dbh = CHROMA_SMOOTH_MEDIAN(med_b);
            
            /* next, try to interpolate in vertical direction */
            int ev = 0;
            k = 0;
            for (i = -CHROMA_SMOOTH_MAX_XY_IJ; i <= CHROMA_SMOOTH_MAX_XY_IJ; i += 2)
            {
                for (j = -CHROMA_SMOOTH_MAX_XY_IJ; j <= CHROMA_SMOOTH_MAX_XY_IJ; j += 2)
                {
                    #ifdef CHROMA_SMOOTH_2X2
                    if (ABS(i) + ABS(j) == 4)
                        continue;
                    #endif

                    int r  = inp[x+i   +   (y+j) * w];
                    int b  = inp[x+i+1 + (y+j+1) * w];
                                                        /*  for R      for B      */
                    int g1 = inp[x+i+1 +   (y+j) * w];  /*  Right      Top        */
                    int g2 = inp[x+i   + (y+j+1) * w];  /*  Bottom     Left       */
                  //int g3 = inp[x+i-1 +   (y+j) * w];  /*  Left                  */ 
                    int g4 = inp[x+i   + (y+j-1) * w];  /*  Top                   */
                  //int g5 = inp[x+i+2 + (y+j+1) * w];  /*             Right      */
                    int g6 = inp[x+i+1 + (y+j+2) * w];  /*             Bottom     */
                    
                    g1 = raw2ev[g1];
                    g2 = raw2ev[g2];
                  //g3 = raw2ev[g3];
                    g4 = raw2ev[g4];
                  //g5 = raw2ev[g5];
                    g6 = raw2ev[g6];
                    
                    int gr = (g2+g4)/2;
                    int gb = (g1+g6)/2;
                    ev += ABS(g2-g4) + ABS(g1-g6);
                    med_r[k] = raw2ev[r] - gr;
                    med_b[k] = raw2ev[b] - gb;
                    k++;
                }
            }

            /* difference from green, with vertical interpolation */
            int drv = CHROMA_SMOOTH_MEDIAN(med_r);
            int dbv = CHROMA_SMOOTH_MEDIAN(med_b);

            /* back to our filtered pixels (RG/GB cell) */
            int g1 = inp[x+1 +     y * w];
            int g2 = inp[x   + (y+1) * w];
            int g3 = inp[x-1 +   (y) * w];
            int g4 = inp[x   + (y-1) * w];
            int g5 = inp[x+2 + (y+1) * w];
            int g6 = inp[x+1 + (y+2) * w];
            
            g1 = raw2ev[g1];
            g2 = raw2ev[g2];
            g3 = raw2ev[g3];
            g4 = raw2ev[g4];
            g5 = raw2ev[g5];
            g6 = raw2ev[g6];

            /* which of the two interpolations will we choose? */
            int grv = (g2+g4)/2;
            int grh = (g1+g3)/2;
            int gbv = (g1+g6)/2;
            int gbh = (g2+g5)/2;
            int gr = ev < eh ? grv : grh;
            int gb = ev < eh ? gbv : gbh;
            int dr = ev < eh ? drv : drh;
            int db = ev < eh ? dbv : dbh;
            
            int r0 = inp[x   +     y * w];
            int b0 = inp[x+1 + (y+1) * w];

            /* if we are close to the noise floor, use both directions, beacuse otherwise it will affect the noise structure and introduce false detail */
            /* todo: smooth transition between the two methods? better thresholding condition? */
            int thr = 64;
            if (r0 < black+thr || b0 < black+thr || ABS(drv - drh) < thr || ABS(grv-grh) < thr || ABS(gbv-gbh) < thr)
            {
                dr = (drv+drh)/2;
                db = (dbv+dbh)/2;
                gr = (g1+g2+g3+g4)/4;
                gb = (g1+g2+g5+g6)/4;
            }

            /* replace red and blue pixels with filtered values, keep green pixels unchanged */
            /* don't touch overexposed areas */
            if (out[x   +     y * w] < (unsigned int)white)
                out[x   +     y * w] = chroma_smooth_ev2raw_lookup(ev2raw, gr + dr);
            
            if (out[x+1  + (y+1)* w] < (unsigned int)white)
                out[x+1 + (y+1) * w] = chroma_smooth_ev2raw_lookup(ev2raw, gb + db);
        }
    }
}
#endif

#undef CHROMA_SMOOTH_FUNC
#undef CHROMA_SMOOTH_MAX_XY_IJ
#undef CHROMA_SMOOTH_FILTER_SIZE
#undef CHROMA_SMOOTH_MEDIAN
