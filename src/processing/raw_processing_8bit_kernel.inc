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
                                      uint8_t * __restrict outputImage,
                                      processing_direct8_kernel_timing_t * timing)
{
    int32_t ** pm = processing->pre_calc_matrix;
    const int row_stride = imageX * 3;
    uint16_t * pix = inputImage + ( rowStart * row_stride );
    uint8_t * out = outputImage + ( rowStart * row_stride );
    uint16_t * pix_end = inputImage + ( rowEnd * row_stride );
    uint16_t * pre_calc_curve = processing->pre_calc_curve_r;
    uint16_t * gcurve_y = processing->gcurve_y;
    uint16_t * gcurve_r = processing->gcurve_r;
    uint16_t * gcurve_g = processing->gcurve_g;
    uint16_t * gcurve_b = processing->gcurve_b;
    const int apply_creative_curves = processing->allow_creative_adjustments != 0;

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

    if( timing == NULL )
    {
        for( ; pix < pix_end; pix += 3, out += 3 )
        {
            const float pix0 = (float)pm0[pix[0]];
            const float pix1 = (float)pm4[pix[1]];
            const float pix2 = (float)pm8[pix[2]];
            float result0 = pix0 * proper_wb_0 + pix1 * proper_wb_1 + pix2 * proper_wb_2;
            float result1 = pix0 * proper_wb_3 + pix1 * proper_wb_4 + pix2 * proper_wb_5;
            float result2 = pix0 * proper_wb_6 + pix1 * proper_wb_7 + pix2 * proper_wb_8;

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
    for( ; pix < pix_end; pix += 3, out += 3 )
    {
        const float pix0 = (float)pm0[pix[0]];
        const float pix1 = (float)pm4[pix[1]];
        const float pix2 = (float)pm8[pix[2]];
        float result0 = pix0 * proper_wb_0 + pix1 * proper_wb_1 + pix2 * proper_wb_2;
        float result1 = pix0 * proper_wb_3 + pix1 * proper_wb_4 + pix2 * proper_wb_5;
        float result2 = pix0 * proper_wb_6 + pix1 * proper_wb_7 + pix2 * proper_wb_8;

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
                const float result2_1 = Y - (Reinhard_for_blue((Y - result1) * inv_y) * Y);
                const float result2_2 = Y - (Reinhard_for_blue((Y - result2) * inv_y) * Y);
                const float desaturate_factor =
                    (Y - MIN(MIN(result2_0, result2_1), result2_2)) / y_minus_min_channel;

                result0 = (result0 - Y) * desaturate_factor + Y;
                result1 = (result1 - Y) * desaturate_factor + Y;
                result2 = (result2 - Y) * desaturate_factor + Y;
            }
        }
        matrix_profile_counter += 1;
        if( matrix_profile_counter >= profile_loop_stride )
        {
            const double now = omp_get_wtime();
            timing->matrix_ms += (now - matrix_profile_start) * 1000.0;
            matrix_profile_start = now;
            matrix_profile_counter = 0;
        }

        uint16_t mapped0 = pre_calc_gamma[LIMIT16((uint32_t)result0)];
        uint16_t mapped1 = pre_calc_gamma[LIMIT16((uint32_t)result1)];
        uint16_t mapped2 = pre_calc_gamma[LIMIT16((uint32_t)result2)];

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
