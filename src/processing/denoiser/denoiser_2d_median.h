/*!
 * \file denoise_2D_median.h
 * \author masc4ii
 * \copyright 2018
 * \brief an very easy 2D median denoiser
 */

#ifndef DENOISER_2D_MEDIAN_H
#define DENOISER_2D_MEDIAN_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct denoise_2d_median_context_s denoise_2d_median_context_t;

void denoise_2D_median(uint16_t *data, int width, int height, uint8_t window, uint8_t strength);
void denoise_2D_median_with_context(uint16_t * data,
                                    int width,
                                    int height,
                                    uint8_t window,
                                    uint8_t strength,
                                    denoise_2d_median_context_t ** context);
void denoise_2D_median_release(denoise_2d_median_context_t ** context);

#ifdef __cplusplus
}
#endif

#endif // DENOISER_2D_MEDIAN_H
