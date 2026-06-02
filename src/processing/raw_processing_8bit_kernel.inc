/*
 * Shared body of apply_processing_object_8bit_fast_rows.
 *
 * Included twice from raw_processing.c, once with the scalar ISA baseline and
 * once with `__attribute__((target("avx2,fma")))` so the compiler can emit an
 * AVX2+FMA variant. Parity between the two variants is enforced by the
 * DirectProcessed8FastPath* tests in tests/pipeline/test_dual_iso_pipeline.cpp.
 */

#ifndef APPLY_8BIT_FAST_ROWS_NAME
#error "include raw_processing_8bit_kernel.inc only via APPLY_8BIT_FAST_ROWS_NAME"
#endif

#ifndef APPLY_8BIT_FAST_ROWS_ATTRS
#define APPLY_8BIT_FAST_ROWS_ATTRS
#endif

APPLY_8BIT_FAST_ROWS_ATTRS
static void APPLY_8BIT_FAST_ROWS_NAME(processingObject_t * processing,
                                      int imageX,
                                      int rowStart,
                                      int rowEnd,
                                      uint16_t * __restrict inputImage,
                                      uint16_t * __restrict blurImage,
                                      uint8_t * __restrict outputImage,
                                      processing_direct8_kernel_timing_t * timing)
{
    int32_t ** pm = processing->pre_calc_matrix;
    const int row_stride = imageX * 3;
    uint16_t * pix = inputImage + ( rowStart * row_stride );
    uint16_t * bpix = blurImage ? blurImage + ( rowStart * row_stride ) : pix;
    uint8_t * out = outputImage + ( rowStart * row_stride );
    uint16_t * pix_end = inputImage + ( rowEnd * row_stride );
    uint16_t * pre_calc_levels = processing->pre_calc_levels;
    uint16_t * pre_calc_curve = processing->pre_calc_curve_r;
    uint16_t * gcurve_y = processing->gcurve_y;
    uint16_t * gcurve_r = processing->gcurve_r;
    uint16_t * gcurve_g = processing->gcurve_g;
    uint16_t * gcurve_b = processing->gcurve_b;
    const int apply_creative_curves = processing->allow_creative_adjustments != 0;
    const int apply_contrast =
        processing->allow_creative_adjustments != 0
        && ( processing->contrast <= -0.01 || processing->contrast >= 0.01 );
    const int apply_shadows_highlights =
        processing->allow_creative_adjustments != 0
        && ( ( processing->shadows_highlights.shadows <= -0.01
            || processing->shadows_highlights.shadows >= 0.01 )
          || ( processing->shadows_highlights.highlights <= -0.01
            || processing->shadows_highlights.highlights >= 0.01 ) );
    const int apply_local_tone = apply_contrast || apply_shadows_highlights;
    const int blur_image_is_curve_index =
        blurImage && processing_shadows_highlights_curve_index_mask_enabled();
    const int apply_vibrance =
        processing->allow_creative_adjustments != 0
        && ( processing->vibrance <= 0.99 || processing->vibrance >= 1.01 );
    const int apply_saturation =
        processing->allow_creative_adjustments != 0
        && ( processing->saturation <= 0.99 || processing->saturation >= 1.01 );

    int32_t * pm0 = pm[0];
    int32_t * pm4 = pm[4];
    int32_t * pm8 = pm[8];
    double * contrast_curve = processing->contrast_curve;
    double * shadow_highlight_curve = processing->shadows_highlights.shadow_highlight_curve;
    int32_t * pre_calc_vibrance = processing->pre_calc_vibrance;
    int32_t * pre_calc_sat = processing->pre_calc_sat;
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

    /* Phase E7: AgX support on the direct8 fast path.
     *
     * Indirect path (raw_processing.c, applied for the basic-matrix
     * neutral-receipt branch):
     *   1. WB matrix multiply (float).
     *   2. Reinhard tonemap + desaturation (Y-preserving).
     *   3. If AgX: clip negatives to 0, apply agx_compressed_matrix (3x3
     *      forward), LIMIT16 into uint16 pix[].
     *      Else: LIMIT16 of the float result into pix[].
     *   4. Gamma LUT: pre_calc_gamma[pix[i]].
     *   5. (creative curves: pre_calc_curve_r, gcurve_y, gcurve_{r,g,b}).
     *   6. If AgX: convert uint16 pix[] back to float, apply
     *      agx_inverse_matrix (3x3 inverse), LIMIT16 into uint16 pix[].
     *
     * The kernel applies the same forward/inverse pair around the gamma
     * LUT and creative curves.  Branches on `apply_agx` are loop-invariant
     * and the compiler hoists the test out of the per-pixel hot loop.
     * agx_compressed_matrix is declared in raw_processing.c (and visible
     * here because this .inc is included from that translation unit). */
    extern double agx_compressed_matrix[9];
    const int apply_agx = processing->AgX != 0;
    double agx_inverse_matrix[9];
    if( apply_agx )
    {
        invertMatrix(agx_compressed_matrix, agx_inverse_matrix);
    }
    /* Keep AgX matrix coefficients in double to match indirect-SLOW path's
     * `result[i] * agx_compressed_matrix[i]` arithmetic, where the float
     * `result[i]` is implicitly promoted to double for the multiply. Float
     * coefficients here would introduce 1-LSB rounding drift on a handful
     * of edge-case pixels and break the byte-identity guarantee. */
    const double agx_fwd_0 = agx_compressed_matrix[0];
    const double agx_fwd_1 = agx_compressed_matrix[1];
    const double agx_fwd_2 = agx_compressed_matrix[2];
    const double agx_fwd_3 = agx_compressed_matrix[3];
    const double agx_fwd_4 = agx_compressed_matrix[4];
    const double agx_fwd_5 = agx_compressed_matrix[5];
    const double agx_fwd_6 = agx_compressed_matrix[6];
    const double agx_fwd_7 = agx_compressed_matrix[7];
    const double agx_fwd_8 = agx_compressed_matrix[8];
    const double agx_inv_0 = apply_agx ? agx_inverse_matrix[0] : 0.0;
    const double agx_inv_1 = apply_agx ? agx_inverse_matrix[1] : 0.0;
    const double agx_inv_2 = apply_agx ? agx_inverse_matrix[2] : 0.0;
    const double agx_inv_3 = apply_agx ? agx_inverse_matrix[3] : 0.0;
    const double agx_inv_4 = apply_agx ? agx_inverse_matrix[4] : 0.0;
    const double agx_inv_5 = apply_agx ? agx_inverse_matrix[5] : 0.0;
    const double agx_inv_6 = apply_agx ? agx_inverse_matrix[6] : 0.0;
    const double agx_inv_7 = apply_agx ? agx_inverse_matrix[7] : 0.0;
    const double agx_inv_8 = apply_agx ? agx_inverse_matrix[8] : 0.0;

    /* AgX and local-tone contrast both use the generic 16-bit reference path,
     * whose WB matrix multiply promotes the matrix coefficients to double
     * before narrowing back to float. Keep a double copy for those parity
     * cases while preserving the older float fast branch for neutral receipts. */
    const double proper_wb_d0 = processing->proper_wb_matrix[0];
    const double proper_wb_d1 = processing->proper_wb_matrix[1];
    const double proper_wb_d2 = processing->proper_wb_matrix[2];
    const double proper_wb_d3 = processing->proper_wb_matrix[3];
    const double proper_wb_d4 = processing->proper_wb_matrix[4];
    const double proper_wb_d5 = processing->proper_wb_matrix[5];
    const double proper_wb_d6 = processing->proper_wb_matrix[6];
    const double proper_wb_d7 = processing->proper_wb_matrix[7];
    const double proper_wb_d8 = processing->proper_wb_matrix[8];

    if( timing == NULL )
    {
        for( ; pix < pix_end; pix += 3, bpix += 3, out += 3 )
        {
            const uint16_t pix0_level = pre_calc_levels[pix[0]];
            const uint16_t pix1_level = pre_calc_levels[pix[1]];
            const uint16_t pix2_level = pre_calc_levels[pix[2]];
            const int32_t pix0_lut = pm0[pix0_level];
            const int32_t pix1_lut = pm4[pix1_level];
            const int32_t pix2_lut = pm8[pix2_level];
            double expo_correction = 1.0;
            if( apply_shadows_highlights )
            {
                int32_t bval;
                if( blur_image_is_curve_index )
                {
                    bval = bpix[0];
                }
                else
                {
                    const uint16_t bpix0_level = blurImage ? bpix[0] : pix0_level;
                    const uint16_t bpix1_level = blurImage ? bpix[1] : pix1_level;
                    const uint16_t bpix2_level = blurImage ? bpix[2] : pix2_level;
                    bval = ( ( pm0[bpix0_level] << 2 )
                           + ( pm4[bpix1_level] * 11 )
                           +   pm8[bpix2_level] ) >> 4;
                }
                expo_correction *= shadow_highlight_curve[LIMIT16(bval)];
            }
            if( apply_contrast )
            {
                const int32_t cval = ( ( pix0_lut << 2 )
                                     + ( pix1_lut * 11 )
                                     +   pix2_lut ) >> 4;
                expo_correction *= contrast_curve[LIMIT16(cval)];
            }

            float pix0 = (float)( pix0_lut * expo_correction );
            float pix1 = (float)( pix1_lut * expo_correction );
            float pix2 = (float)( pix2_lut * expo_correction );
            /* The generic 16-bit path quantizes local-tone-adjusted values
             * before WB. AgX also routes through that generic path. Mirror
             * that store-to-uint16 behaviour for byte identity. */
            if( apply_agx || apply_local_tone )
            {
                pix0 = (float)(uint16_t)LIMIT16(pix0);
                pix1 = (float)(uint16_t)LIMIT16(pix1);
                pix2 = (float)(uint16_t)LIMIT16(pix2);
            }
            float result0;
            float result1;
            float result2;
            if( apply_agx || apply_local_tone )
            {
                result0 = (float)((double)pix0 * proper_wb_d0 + (double)pix1 * proper_wb_d1 + (double)pix2 * proper_wb_d2);
                result1 = (float)((double)pix0 * proper_wb_d3 + (double)pix1 * proper_wb_d4 + (double)pix2 * proper_wb_d5);
                result2 = (float)((double)pix0 * proper_wb_d6 + (double)pix1 * proper_wb_d7 + (double)pix2 * proper_wb_d8);
            }
            else
            {
                result0 = pix0 * proper_wb_0 + pix1 * proper_wb_1 + pix2 * proper_wb_2;
                result1 = pix0 * proper_wb_3 + pix1 * proper_wb_4 + pix2 * proper_wb_5;
                result2 = pix0 * proper_wb_6 + pix1 * proper_wb_7 + pix2 * proper_wb_8;
            }

            if( !processing->exr_mode && apply_local_tone )
            {
                const float Y = rgb_to_y0 * result0
                              + rgb_to_y1 * result1
                              + rgb_to_y2 * result2;
                const float min_channel = MIN(MIN(result0, result1), result2);
                const float result2_0 = -(Reinhard_for_colour((Y - result0) / Y) * Y) + Y;
                const float result2_1 = -(ReinhardTonemap_f((Y - result1) / Y) * Y) + Y;
                const float result2_2 = -(Reinhard_for_blue((Y - result2) / Y) * Y) + Y;
                float desaturate_factor =
                    (Y - MIN(MIN(result2_0, result2_1), result2_2)) / (Y - min_channel);

                if( Y <= 0.0f ) desaturate_factor = 1.0f;

                result0 = (result0 - Y) * desaturate_factor + Y;
                result1 = (result1 - Y) * desaturate_factor + Y;
                result2 = (result2 - Y) * desaturate_factor + Y;
            }
            else
            {
                const float Y = rgb_to_y0 * result0
                              + rgb_to_y1 * result1
                              + rgb_to_y2 * result2;
                if( !processing->exr_mode && Y > 0.0f )
                {
                    const float min_channel = MIN(MIN(result0, result1), result2);
                    const float y_minus_min_channel = Y - min_channel;
                    if( fabsf(y_minus_min_channel) > FLT_MIN )
                    {
                        const float inv_y = 1.0f / Y;
                        const float result2_0 = Y - (Reinhard_for_colour((Y - result0) * inv_y) * Y);
                        const float result2_1 = Y - (ReinhardTonemap_f((Y - result1) * inv_y) * Y);
                        const float result2_2 = Y - (Reinhard_for_blue((Y - result2) * inv_y) * Y);
                        const float desaturate_factor =
                            (Y - MIN(MIN(result2_0, result2_1), result2_2)) / y_minus_min_channel;

                        result0 = (result0 - Y) * desaturate_factor + Y;
                        result1 = (result1 - Y) * desaturate_factor + Y;
                        result2 = (result2 - Y) * desaturate_factor + Y;
                    }
                }
            }

            /* AgX forward matrix (clip negatives, then 3x3 in double to
             * match the indirect-SLOW reference). After the multiply the
             * result is stored back to float result0/1/2 because the gamma
             * LUT step and the prior Reinhard kept everything in float; the
             * reference path stores via LIMIT16-to-uint16 here, and we
             * replicate that quantisation in the gamma index below. */
            if( apply_agx )
            {
                if( result0 < 0.0f ) result0 = 0.0f;
                if( result1 < 0.0f ) result1 = 0.0f;
                if( result2 < 0.0f ) result2 = 0.0f;
                const double dr = (double)result0;
                const double dg = (double)result1;
                const double db = (double)result2;
                const double ar = dr * agx_fwd_0 + dg * agx_fwd_1 + db * agx_fwd_2;
                const double ag = dr * agx_fwd_3 + dg * agx_fwd_4 + db * agx_fwd_5;
                const double ab = dr * agx_fwd_6 + dg * agx_fwd_7 + db * agx_fwd_8;
                /* Reference path applies LIMIT16 here when storing into pix[]
                 * as uint16; replicate by quantising through uint16 so the
                 * pre_calc_gamma LUT is indexed identically. */
                result0 = (float)(uint16_t)LIMIT16(ar);
                result1 = (float)(uint16_t)LIMIT16(ag);
                result2 = (float)(uint16_t)LIMIT16(ab);
            }

            uint16_t mapped0;
            uint16_t mapped1;
            uint16_t mapped2;
            if( apply_local_tone && !apply_agx )
            {
                mapped0 = pre_calc_gamma[(uint16_t)LIMIT16(result0)];
                mapped1 = pre_calc_gamma[(uint16_t)LIMIT16(result1)];
                mapped2 = pre_calc_gamma[(uint16_t)LIMIT16(result2)];
            }
            else
            {
                mapped0 = pre_calc_gamma[LIMIT16((uint32_t)result0)];
                mapped1 = pre_calc_gamma[LIMIT16((uint32_t)result1)];
                mapped2 = pre_calc_gamma[LIMIT16((uint32_t)result2)];
            }
            if( apply_vibrance )
            {
                const int32_t y1 = ( ( mapped0 << 2 ) + ( mapped1 * 11 ) + mapped2 ) >> 4;
                const int32_t y2 = y1 - 65536;
                const int32_t vib0 = pre_calc_vibrance[mapped0 - y2] + y1;
                const int32_t vib1 = pre_calc_vibrance[mapped1 - y2] + y1;
                const int32_t vib2 = pre_calc_vibrance[mapped2 - y2] + y1;
                if( processing->vibrance > 1.0 )
                {
                    double sat = 0.0;
                    if( !( mapped0 == 0 && mapped1 == 0 && mapped2 == 0 ) )
                    {
                        uint16_t biggest = mapped0;
                        uint16_t smallest = mapped0;
                        if( mapped1 > biggest ) biggest = mapped1;
                        if( mapped2 > biggest ) biggest = mapped2;
                        if( mapped1 < smallest ) smallest = mapped1;
                        if( mapped2 < smallest ) smallest = mapped2;
                        sat = ((double)biggest - (double)smallest) / (double)biggest;
                    }
                    sat = 2.0 * sat / ( sat * sat + 1.0 );
                    if( sat > 1.0 ) sat = 1.0;
                    mapped0 = (uint16_t)LIMIT16( mapped0 * sat + vib0 * ( 1.0 - sat ) );
                    mapped1 = (uint16_t)LIMIT16( mapped1 * sat + vib1 * ( 1.0 - sat ) );
                    mapped2 = (uint16_t)LIMIT16( mapped2 * sat + vib2 * ( 1.0 - sat ) );
                }
                else
                {
                    mapped0 = (uint16_t)LIMIT16(vib0);
                    mapped1 = (uint16_t)LIMIT16(vib1);
                    mapped2 = (uint16_t)LIMIT16(vib2);
                }
            }
            if( apply_saturation )
            {
                const int32_t y1 = ( ( mapped0 << 2 ) + ( mapped1 * 11 ) + mapped2 ) >> 4;
                const int32_t y2 = y1 - 65536;
                mapped0 = (uint16_t)LIMIT16(pre_calc_sat[mapped0 - y2] + y1);
                mapped1 = (uint16_t)LIMIT16(pre_calc_sat[mapped1 - y2] + y1);
                mapped2 = (uint16_t)LIMIT16(pre_calc_sat[mapped2 - y2] + y1);
            }
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

            /* AgX inverse matrix on the post-curve mapped values. Mirrors
             * raw_processing.c lines 1750-1760: as_float[i] = pix[i] (uint16
             * to float), then result = LIMIT16(matrix product), then implicit
             * truncate-to-uint16 on the LIMIT16 float result. */
            if( apply_agx )
            {
                const float fr = (float)mapped0;
                const float fg = (float)mapped1;
                const float fb = (float)mapped2;
                const double ir = (double)fr * agx_inv_0 + (double)fg * agx_inv_1 + (double)fb * agx_inv_2;
                const double ig = (double)fr * agx_inv_3 + (double)fg * agx_inv_4 + (double)fb * agx_inv_5;
                const double ib = (double)fr * agx_inv_6 + (double)fg * agx_inv_7 + (double)fb * agx_inv_8;
                mapped0 = (uint16_t)LIMIT16(ir);
                mapped1 = (uint16_t)LIMIT16(ig);
                mapped2 = (uint16_t)LIMIT16(ib);
            }

            out[0] = (uint8_t)(mapped0 >> 8);
            out[1] = (uint8_t)(mapped1 >> 8);
            out[2] = (uint8_t)(mapped2 >> 8);
        }
        return;
    }

    const int profile_loop_stride = 1024;
    int matrix_profile_counter = 0;
    int gamma_profile_counter = 0;
    int curves_profile_counter = 0;
    double matrix_profile_start = omp_get_wtime();
    double gamma_profile_start = 0.0;
    double curves_profile_start = 0.0;
    for( ; pix < pix_end; pix += 3, bpix += 3, out += 3 )
    {
        const uint16_t pix0_level = pre_calc_levels[pix[0]];
        const uint16_t pix1_level = pre_calc_levels[pix[1]];
        const uint16_t pix2_level = pre_calc_levels[pix[2]];
        const int32_t pix0_lut = pm0[pix0_level];
        const int32_t pix1_lut = pm4[pix1_level];
        const int32_t pix2_lut = pm8[pix2_level];
        double expo_correction = 1.0;
        if( apply_shadows_highlights )
        {
            int32_t bval;
            if( blur_image_is_curve_index )
            {
                bval = bpix[0];
            }
            else
            {
                const uint16_t bpix0_level = blurImage ? bpix[0] : pix0_level;
                const uint16_t bpix1_level = blurImage ? bpix[1] : pix1_level;
                const uint16_t bpix2_level = blurImage ? bpix[2] : pix2_level;
                bval = ( ( pm0[bpix0_level] << 2 )
                       + ( pm4[bpix1_level] * 11 )
                       +   pm8[bpix2_level] ) >> 4;
            }
            expo_correction *= shadow_highlight_curve[LIMIT16(bval)];
        }
        if( apply_contrast )
        {
            const int32_t cval = ( ( pix0_lut << 2 )
                                 + ( pix1_lut * 11 )
                                 +   pix2_lut ) >> 4;
            expo_correction *= contrast_curve[LIMIT16(cval)];
        }

        float pix0 = (float)( pix0_lut * expo_correction );
        float pix1 = (float)( pix1_lut * expo_correction );
        float pix2 = (float)( pix2_lut * expo_correction );
        /* See note in the non-timing branch above. */
        if( apply_agx || apply_local_tone )
        {
            pix0 = (float)(uint16_t)LIMIT16(pix0);
            pix1 = (float)(uint16_t)LIMIT16(pix1);
            pix2 = (float)(uint16_t)LIMIT16(pix2);
        }
        float result0;
        float result1;
        float result2;
        if( apply_agx || apply_local_tone )
        {
            result0 = (float)((double)pix0 * proper_wb_d0 + (double)pix1 * proper_wb_d1 + (double)pix2 * proper_wb_d2);
            result1 = (float)((double)pix0 * proper_wb_d3 + (double)pix1 * proper_wb_d4 + (double)pix2 * proper_wb_d5);
            result2 = (float)((double)pix0 * proper_wb_d6 + (double)pix1 * proper_wb_d7 + (double)pix2 * proper_wb_d8);
        }
        else
        {
            result0 = pix0 * proper_wb_0 + pix1 * proper_wb_1 + pix2 * proper_wb_2;
            result1 = pix0 * proper_wb_3 + pix1 * proper_wb_4 + pix2 * proper_wb_5;
            result2 = pix0 * proper_wb_6 + pix1 * proper_wb_7 + pix2 * proper_wb_8;
        }

        if( !processing->exr_mode && apply_local_tone )
        {
            const float Y = rgb_to_y0 * result0
                          + rgb_to_y1 * result1
                          + rgb_to_y2 * result2;
            const float min_channel = MIN(MIN(result0, result1), result2);
            const float result2_0 = -(Reinhard_for_colour((Y - result0) / Y) * Y) + Y;
            const float result2_1 = -(ReinhardTonemap_f((Y - result1) / Y) * Y) + Y;
            const float result2_2 = -(Reinhard_for_blue((Y - result2) / Y) * Y) + Y;
            float desaturate_factor =
                (Y - MIN(MIN(result2_0, result2_1), result2_2)) / (Y - min_channel);

            if( Y <= 0.0f ) desaturate_factor = 1.0f;

            result0 = (result0 - Y) * desaturate_factor + Y;
            result1 = (result1 - Y) * desaturate_factor + Y;
            result2 = (result2 - Y) * desaturate_factor + Y;
        }
        else
        {
            const float Y = rgb_to_y0 * result0
                          + rgb_to_y1 * result1
                          + rgb_to_y2 * result2;
            if( !processing->exr_mode && Y > 0.0f )
            {
                const float min_channel = MIN(MIN(result0, result1), result2);
                const float y_minus_min_channel = Y - min_channel;
                if( fabsf(y_minus_min_channel) > FLT_MIN )
                {
                    const float inv_y = 1.0f / Y;
                    const float result2_0 = Y - (Reinhard_for_colour((Y - result0) * inv_y) * Y);
                    const float result2_1 = Y - (ReinhardTonemap_f((Y - result1) * inv_y) * Y);
                    const float result2_2 = Y - (Reinhard_for_blue((Y - result2) * inv_y) * Y);
                    const float desaturate_factor =
                        (Y - MIN(MIN(result2_0, result2_1), result2_2)) / y_minus_min_channel;

                    result0 = (result0 - Y) * desaturate_factor + Y;
                    result1 = (result1 - Y) * desaturate_factor + Y;
                    result2 = (result2 - Y) * desaturate_factor + Y;
                }
            }
        }

        /* AgX forward matrix (see comment in non-timing branch above). */
        if( apply_agx )
        {
            if( result0 < 0.0f ) result0 = 0.0f;
            if( result1 < 0.0f ) result1 = 0.0f;
            if( result2 < 0.0f ) result2 = 0.0f;
            const double dr = (double)result0;
            const double dg = (double)result1;
            const double db = (double)result2;
            const double ar = dr * agx_fwd_0 + dg * agx_fwd_1 + db * agx_fwd_2;
            const double ag = dr * agx_fwd_3 + dg * agx_fwd_4 + db * agx_fwd_5;
            const double ab = dr * agx_fwd_6 + dg * agx_fwd_7 + db * agx_fwd_8;
            result0 = (float)(uint16_t)LIMIT16(ar);
            result1 = (float)(uint16_t)LIMIT16(ag);
            result2 = (float)(uint16_t)LIMIT16(ab);
        }
        matrix_profile_counter += 1;
        if( matrix_profile_counter >= profile_loop_stride )
        {
            const double now = omp_get_wtime();
            timing->matrix_ms += (now - matrix_profile_start) * 1000.0;
            matrix_profile_start = now;
            matrix_profile_counter = 0;
        }

        uint16_t mapped0;
        uint16_t mapped1;
        uint16_t mapped2;
        if( apply_local_tone && !apply_agx )
        {
            mapped0 = pre_calc_gamma[(uint16_t)LIMIT16(result0)];
            mapped1 = pre_calc_gamma[(uint16_t)LIMIT16(result1)];
            mapped2 = pre_calc_gamma[(uint16_t)LIMIT16(result2)];
        }
        else
        {
            mapped0 = pre_calc_gamma[LIMIT16((uint32_t)result0)];
            mapped1 = pre_calc_gamma[LIMIT16((uint32_t)result1)];
            mapped2 = pre_calc_gamma[LIMIT16((uint32_t)result2)];
        }
        if( apply_vibrance )
        {
            const int32_t y1 = ( ( mapped0 << 2 ) + ( mapped1 * 11 ) + mapped2 ) >> 4;
            const int32_t y2 = y1 - 65536;
            const int32_t vib0 = pre_calc_vibrance[mapped0 - y2] + y1;
            const int32_t vib1 = pre_calc_vibrance[mapped1 - y2] + y1;
            const int32_t vib2 = pre_calc_vibrance[mapped2 - y2] + y1;
            if( processing->vibrance > 1.0 )
            {
                double sat = 0.0;
                if( !( mapped0 == 0 && mapped1 == 0 && mapped2 == 0 ) )
                {
                    uint16_t biggest = mapped0;
                    uint16_t smallest = mapped0;
                    if( mapped1 > biggest ) biggest = mapped1;
                    if( mapped2 > biggest ) biggest = mapped2;
                    if( mapped1 < smallest ) smallest = mapped1;
                    if( mapped2 < smallest ) smallest = mapped2;
                    sat = ((double)biggest - (double)smallest) / (double)biggest;
                }
                sat = 2.0 * sat / ( sat * sat + 1.0 );
                if( sat > 1.0 ) sat = 1.0;
                mapped0 = (uint16_t)LIMIT16( mapped0 * sat + vib0 * ( 1.0 - sat ) );
                mapped1 = (uint16_t)LIMIT16( mapped1 * sat + vib1 * ( 1.0 - sat ) );
                mapped2 = (uint16_t)LIMIT16( mapped2 * sat + vib2 * ( 1.0 - sat ) );
            }
            else
            {
                mapped0 = (uint16_t)LIMIT16(vib0);
                mapped1 = (uint16_t)LIMIT16(vib1);
                mapped2 = (uint16_t)LIMIT16(vib2);
            }
        }
        if( apply_saturation )
        {
            const int32_t y1 = ( ( mapped0 << 2 ) + ( mapped1 * 11 ) + mapped2 ) >> 4;
            const int32_t y2 = y1 - 65536;
            mapped0 = (uint16_t)LIMIT16(pre_calc_sat[mapped0 - y2] + y1);
            mapped1 = (uint16_t)LIMIT16(pre_calc_sat[mapped1 - y2] + y1);
            mapped2 = (uint16_t)LIMIT16(pre_calc_sat[mapped2 - y2] + y1);
        }

        if( gamma_profile_counter == 0 )
        {
            gamma_profile_start = omp_get_wtime();
        }
        gamma_profile_counter += 1;
        if( gamma_profile_counter >= profile_loop_stride )
        {
            const double now = omp_get_wtime();
            timing->gamma_ms += (now - gamma_profile_start) * 1000.0;
            gamma_profile_start = now;
            gamma_profile_counter = 0;
        }

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

            if( curves_profile_counter == 0 )
            {
                curves_profile_start = omp_get_wtime();
            }
            curves_profile_counter += 1;
            if( curves_profile_counter >= profile_loop_stride )
            {
                const double now = omp_get_wtime();
                timing->curves_ms += (now - curves_profile_start) * 1000.0;
                curves_profile_start = now;
                curves_profile_counter = 0;
            }
        }

        /* AgX inverse matrix (see comment in non-timing branch above). */
        if( apply_agx )
        {
            const float fr = (float)mapped0;
            const float fg = (float)mapped1;
            const float fb = (float)mapped2;
            const double ir = (double)fr * agx_inv_0 + (double)fg * agx_inv_1 + (double)fb * agx_inv_2;
            const double ig = (double)fr * agx_inv_3 + (double)fg * agx_inv_4 + (double)fb * agx_inv_5;
            const double ib = (double)fr * agx_inv_6 + (double)fg * agx_inv_7 + (double)fb * agx_inv_8;
            mapped0 = (uint16_t)LIMIT16(ir);
            mapped1 = (uint16_t)LIMIT16(ig);
            mapped2 = (uint16_t)LIMIT16(ib);
        }

        out[0] = (uint8_t)(mapped0 >> 8);
        out[1] = (uint8_t)(mapped1 >> 8);
        out[2] = (uint8_t)(mapped2 >> 8);
    }

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

#undef APPLY_8BIT_FAST_ROWS_NAME
#undef APPLY_8BIT_FAST_ROWS_ATTRS
