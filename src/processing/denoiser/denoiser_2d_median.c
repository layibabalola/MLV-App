/*!
 * \file denoise_2D_median.c
 * \author masc4ii
 * \copyright 2018
 * \brief an very easy 2D median denoiser
 */

#include <stdlib.h>
#include <string.h>
#include "denoiser_2d_median.h"

#ifdef _OPENMP
#include <omp.h>
#endif

struct denoise_2d_median_context_s {
    uint16_t * noisy;
    size_t noisy_capacity;
    int * window_storage;
    size_t window_capacity;
    int window_threads;
};

static int denoiser_max_threads(void)
{
#ifdef _OPENMP
    return omp_get_max_threads();
#else
    return 1;
#endif
}

static int denoiser_thread_num(void)
{
#ifdef _OPENMP
    return omp_get_thread_num();
#else
    return 0;
#endif
}

static void denoise_reset_context(denoise_2d_median_context_t * context)
{
    if( !context ) return;

    free(context->noisy);
    context->noisy = NULL;
    context->noisy_capacity = 0;

    free(context->window_storage);
    context->window_storage = NULL;
    context->window_capacity = 0;
    context->window_threads = 0;
}

static int denoise_ensure_noisy_capacity(denoise_2d_median_context_t * context, size_t image_size)
{
    if( !context ) return 0;
    if( context->noisy_capacity >= image_size ) return 1;

    uint16_t * resized = realloc(context->noisy, image_size * sizeof(uint16_t));
    if( !resized ) return 0;

    context->noisy = resized;
    context->noisy_capacity = image_size;
    return 1;
}

static int denoise_ensure_window_capacity(denoise_2d_median_context_t * context,
                                          size_t window_size,
                                          int thread_count)
{
    if( !context ) return 0;
    if( context->window_capacity >= window_size && context->window_threads >= thread_count ) return 1;

    size_t total_values = window_size * (size_t)thread_count * 3;
    int * resized = realloc(context->window_storage, total_values * sizeof(int));
    if( !resized ) return 0;

    context->window_storage = resized;
    context->window_capacity = window_size;
    context->window_threads = thread_count;
    return 1;
}

static void swap(int * a, int * b)
{
    int temp;
    temp=*a;
    *a=*b;
    *b=temp;
}

/* the aim of the partition is to return the subscript of the exact */
/* position of the pivot when it is sorted */
/* the low variable is used to point to the position of the next lowest element */
static int partition(int arr[], int first, int last)
{
    int pivot = arr[last];
    int low = first;
    int i = first;
    while(i <= last-1 ){
        if(arr[i] < pivot){
            swap(&arr[i], &arr[low]);
            low++;
        }
        i++;
    }
    swap(&arr[last], &arr[low]);
    return low;
}

static void quick_sort(int arr[], int first, int last)
{
    int pivot_pos;
    if(first < last){
        pivot_pos = partition(arr, first, last);
        quick_sort(arr, first, pivot_pos-1);
        quick_sort(arr, pivot_pos+1, last);
    }
}

void denoise_2D_median_release(denoise_2d_median_context_t ** context)
{
    if( !context || !*context ) return;

    denoise_reset_context(*context);
    free(*context);
    *context = NULL;
}

void denoise_2D_median_with_context(uint16_t * data,
                                    int width,
                                    int height,
                                    uint8_t window,
                                    uint8_t strength,
                                    denoise_2d_median_context_t ** context)
{
    denoise_2d_median_context_t local_context = {0};
    denoise_2d_median_context_t * scratch = &local_context;

    if( context )
    {
        if( !*context )
        {
            *context = calloc(1, sizeof(denoise_2d_median_context_t));
            if( !*context ) return;
        }
        scratch = *context;
    }

    if( strength > 100 ) strength = 100;
    if( strength == 0 || width <= 0 || height <= 0 || window == 0 )
    {
        if( !context ) denoise_reset_context(&local_context);
        return;
    }

    const float strengthF = strength / 100.0f;
    const float antiStrengthF = 1.0f - strengthF;
    const size_t imageSize = (size_t)width * (size_t)height * 3;

    const uint16_t winSize = window * window;
    const uint16_t edgeX = window / 2;
    const uint16_t edgeY = window / 2;
    const uint16_t middle = winSize / 2;

    if( width <= edgeX * 2 || height <= edgeY * 2 )
    {
        if( !context ) denoise_reset_context(&local_context);
        return;
    }

    const int threadCount = denoiser_max_threads();
    if( !denoise_ensure_noisy_capacity(scratch, imageSize) ||
        !denoise_ensure_window_capacity(scratch, winSize, threadCount) )
    {
        if( !context ) denoise_reset_context(&local_context);
        return;
    }

    memcpy(scratch->noisy, data, imageSize * sizeof(uint16_t));

#pragma omp parallel
    {
        int * threadWindow = scratch->window_storage + (size_t)denoiser_thread_num() * scratch->window_capacity * 3;
        int * windowR = threadWindow;
        int * windowG = threadWindow + scratch->window_capacity;
        int * windowB = threadWindow + scratch->window_capacity * 2;

#pragma omp for
        for( int x = edgeX; x < width-edgeX; x++ )
        {
            for( int y = edgeY; y < height-edgeY; y++ )
            {
                uint32_t i = 0;
                for( uint16_t fx = 0; fx < window; fx++ )
                {
                    for( uint16_t fy = 0; fy < window; fy++ )
                    {
                        const uint16_t w = x + fx - edgeX;
                        const uint16_t h = y + fy - edgeY;
                        windowR[i] = scratch->noisy[(h*width+w)*3+0];
                        windowG[i] = scratch->noisy[(h*width+w)*3+1];
                        windowB[i] = scratch->noisy[(h*width+w)*3+2];
                        i++;
                    }
                }

                quick_sort(windowR, 0, winSize-1);
                quick_sort(windowG, 0, winSize-1);
                quick_sort(windowB, 0, winSize-1);

                data[(y*width+x)*3+0] = strengthF*windowR[middle] + antiStrengthF*data[(y*width+x)*3+0];
                data[(y*width+x)*3+1] = strengthF*windowG[middle] + antiStrengthF*data[(y*width+x)*3+1];
                data[(y*width+x)*3+2] = strengthF*windowB[middle] + antiStrengthF*data[(y*width+x)*3+2];
            }
        }
    }

    if( !context ) denoise_reset_context(&local_context);
}

/* 2D median filter */
void denoise_2D_median(uint16_t *data, int width, int height, uint8_t window, uint8_t strength)
{
    denoise_2D_median_with_context(data, width, height, window, strength, NULL);
}
