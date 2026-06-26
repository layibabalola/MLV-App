#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "llrawproc/llrawproc.h"
#include "../debayer/debayer.h"
#include "mlv_object.h"
#include "video_mlv.h"

static int look_assist_thumb_trace_enabled(void)
{
    const char * env = getenv("MLVAPP_LOOK_ASSIST_THUMB_TRACE");
    return env && *env && strcmp(env, "0") != 0;
}

static uint64_t fnv1a64_bytes(const void * data, size_t bytes)
{
    const unsigned char * p = (const unsigned char *)data;
    uint64_t hash = 1469598103934665603ULL;

    for (size_t i = 0; i < bytes; ++i)
    {
        hash ^= (uint64_t)p[i];
        hash *= 1099511628211ULL;
    }

    return hash;
}

static void trace_look_assist_thumbnail(const char * kind,
                                        int frame_index,
                                        int raw_w,
                                        int raw_h,
                                        int thumb_w,
                                        int thumb_h,
                                        int downscale_factor,
                                        uint64_t hash)
{
    if (!look_assist_thumb_trace_enabled()) return;

    fprintf(stderr,
            "THUMB_TRACE kind=%s frame=%d raw=%dx%d thumb=%dx%d factor=%d hash=%016llx\n",
            kind ? kind : "unknown",
            frame_index,
            raw_w,
            raw_h,
            thumb_w,
            thumb_h,
            downscale_factor,
            (unsigned long long)hash);
}

static uint16_t * get_isolated_thumbnail_source_rgb16(mlvObject_t * video,
                                                      int frame_index,
                                                      int raw_w,
                                                      int raw_h)
{
    size_t pixels = (size_t)raw_w * (size_t)raw_h;
    uint16_t * raw_frame = (uint16_t *)malloc(pixels * sizeof(uint16_t));
    uint16_t * rgb_frame = (uint16_t *)malloc(pixels * 3u * sizeof(uint16_t));

    if (!raw_frame || !rgb_frame)
    {
        free(raw_frame);
        free(rgb_frame);
        return NULL;
    }

    int bit_shift = 0;
    if (getMlvRawFrameProcessedUint16Direct(video, (uint64_t)frame_index, raw_frame, &bit_shift))
    {
        free(raw_frame);
        free(rgb_frame);
        return NULL;
    }

    debayerBasicU16(rgb_frame,
                    raw_frame,
                    raw_w,
                    raw_h,
                    1,
                    bit_shift);
    free(raw_frame);
    return rgb_frame;
}

static void downscale_rgb16_average_to_rgb16(const uint16_t * input,
                                             int input_w,
                                             int downscale_factor,
                                             int thumb_w,
                                             int thumb_h,
                                             uint16_t * output)
{
    uint64_t denominator = (uint64_t)downscale_factor * (uint64_t)downscale_factor;

    for (int outY = 0; outY < thumb_h; ++outY) {
        for (int outX = 0; outX < thumb_w; ++outX) {
            uint64_t sum_r = 0;
            uint64_t sum_g = 0;
            uint64_t sum_b = 0;

            int start_y = outY * downscale_factor;
            int start_x = outX * downscale_factor;

            for (int j = 0; j < downscale_factor; j++) {
                for (int i = 0; i < downscale_factor; i++) {
                    size_t pixel_index = ((size_t)(start_y + j) * input_w + (start_x + i)) * 3u;
                    sum_r += input[pixel_index + 0];
                    sum_g += input[pixel_index + 1];
                    sum_b += input[pixel_index + 2];
                }
            }

            size_t out_pixel_index = ((size_t)outY * thumb_w + outX) * 3u;
            output[out_pixel_index + 0] = (uint16_t)(sum_r / denominator);
            output[out_pixel_index + 1] = (uint16_t)(sum_g / denominator);
            output[out_pixel_index + 2] = (uint16_t)(sum_b / denominator);
        }
    }
}

static void downscale_rgb16_average_to_rgb8(const uint16_t * input,
                                            int input_w,
                                            int downscale_factor,
                                            int thumb_w,
                                            int thumb_h,
                                            unsigned char * output)
{
    uint64_t denominator = (uint64_t)downscale_factor * (uint64_t)downscale_factor;

    for (int outY = 0; outY < thumb_h; ++outY) {
        for (int outX = 0; outX < thumb_w; ++outX) {
            uint64_t sum_r = 0;
            uint64_t sum_g = 0;
            uint64_t sum_b = 0;

            int start_y = outY * downscale_factor;
            int start_x = outX * downscale_factor;

            for (int j = 0; j < downscale_factor; j++) {
                for (int i = 0; i < downscale_factor; i++) {
                    size_t pixel_index = ((size_t)(start_y + j) * input_w + (start_x + i)) * 3u;
                    sum_r += input[pixel_index + 0];
                    sum_g += input[pixel_index + 1];
                    sum_b += input[pixel_index + 2];
                }
            }

            size_t out_pixel_index = ((size_t)outY * thumb_w + outX) * 3u;
            output[out_pixel_index + 0] = (unsigned char)((sum_r / denominator) >> 8);
            output[out_pixel_index + 1] = (unsigned char)((sum_g / denominator) >> 8);
            output[out_pixel_index + 2] = (unsigned char)((sum_b / denominator) >> 8);
        }
    }
}

int create_thumbnail(mlvObject_t * video, uint8_t * thumbnail_img, int downscaled_factor, int width, int height, int threads)
{
    int raw_w = video->RAWI.xRes;
    int raw_h = video->RAWI.yRes;
    int i, j;

    uint16_t *raw_frame = (uint16_t *)(malloc(raw_w * raw_h * sizeof(uint16_t)));
    if (getMlvRawFrameUint16(video, 0, raw_frame))
    {
        free(raw_frame);
        return 1;
    }

    int pixel_count = (width) * (height);

    uint16_t *downscaled_frame = (uint16_t *)(malloc(pixel_count * sizeof(uint16_t)));

    if (!downscaled_frame)
    {
        free(raw_frame);
        return 1;
    }

    for (i = 0; i < height; i++)
        for (j = 0; j < width; j++)
            downscaled_frame[i * width + j] = raw_frame[(i * downscaled_factor) * raw_w + (j * downscaled_factor)];

    int shift_val = (llrpHQDualIso(video)) ? 0 : (16 - video->RAWI.raw_info.bits_per_pixel);

    float *float_thumb = (float *)(malloc(pixel_count * sizeof(float)));

    if (!float_thumb)
    {
        free(raw_frame);
        free(downscaled_frame);
        return 1;
    }

    for (i = 0; i < pixel_count; i++)
        float_thumb[i] = (float)(downscaled_frame[i] << shift_val);

    uint16_t *debayered_frame = (uint16_t *)(malloc(pixel_count * 3 * sizeof(uint16_t)));

    if (!debayered_frame)
    {
        free(raw_frame);
        free(downscaled_frame);
        free(float_thumb);
        return 1;
    }

    debayerBasic(debayered_frame, float_thumb, width, height, 1);

    uint16_t *processed_frame = (uint16_t *)(malloc(pixel_count * 3 * sizeof(uint16_t)));

    if (!processed_frame)
    {
        free(raw_frame);
        free(downscaled_frame);
        free(debayered_frame);
        free(float_thumb);
        return 1;
    }

    applyProcessingObject(video->processing,
                          width, height,
                          debayered_frame,
                          processed_frame,
                          threads, 1, 0);

    for (i = 0; i < pixel_count * 3; i++)
        thumbnail_img[i] = (uint8_t)(processed_frame[i] >> 8);

    free(raw_frame);
    free(downscaled_frame);
    free(debayered_frame);
    free(float_thumb);
    free(processed_frame);

    return 0;
}

void get_area_average_downscale_thumnail(mlvObject_t *video, int frame_index, int downscale_factor, int cpu_cores, unsigned char *out_buffer)
{
    if (!video || !out_buffer) {
        return;
    }

    /* Get RAW frame info */
    int raw_w = video->RAWI.xRes;
    int raw_h = video->RAWI.yRes;

    if (raw_w <= 0 || raw_h <= 0 || downscale_factor <= 0) {
        return;
    }

    const int thumbW = raw_w / downscale_factor;
    const int thumbH = raw_h / downscale_factor;
    if (thumbW <= 0 || thumbH <= 0) {
        return;
    }

    processingObject_t *analysis_processing = processingCloneForAnalysis(video->processing);
    if (!analysis_processing) {
        return;
    }

    uint16_t *debayered_raw_frame = get_isolated_thumbnail_source_rgb16(video, frame_index, raw_w, raw_h);
    if (!debayered_raw_frame) {
        processingFreeClone(analysis_processing);
        return;
    }
    trace_look_assist_thumbnail("processed-source16", frame_index, raw_w, raw_h, raw_w, raw_h,
                                1, fnv1a64_bytes(debayered_raw_frame,
                                                 (size_t)raw_w * (size_t)raw_h * 3u * sizeof(uint16_t)));

    uint16_t *downscaled_image = (uint16_t *) malloc(
        (size_t) (thumbW * thumbH * 3) * sizeof(uint16_t));
    if (!downscaled_image) {
        free(debayered_raw_frame);
        processingFreeClone(analysis_processing);
        return;
    }

    downscale_rgb16_average_to_rgb16(debayered_raw_frame, raw_w, downscale_factor,
                                     thumbW, thumbH, downscaled_image);
    trace_look_assist_thumbnail("processed-input16", frame_index, raw_w, raw_h, thumbW, thumbH,
                                downscale_factor, fnv1a64_bytes(downscaled_image,
                                                               (size_t)thumbW * (size_t)thumbH * 3u * sizeof(uint16_t)));

    uint16_t *downscaled_processed_image = (uint16_t *) malloc(
        (size_t) (thumbW * thumbH * 3) * sizeof(uint16_t));
    if (!downscaled_processed_image) {
        free(debayered_raw_frame);
        free(downscaled_image);
        processingFreeClone(analysis_processing);
        return;
    }

    applyProcessingObject(analysis_processing,
                          thumbW, thumbH,
                          downscaled_image,
                          downscaled_processed_image,
                          cpu_cores, 1, frame_index);
    trace_look_assist_thumbnail("processed-output16", frame_index, raw_w, raw_h, thumbW, thumbH,
                                downscale_factor, fnv1a64_bytes(downscaled_processed_image,
                                                               (size_t)thumbW * (size_t)thumbH * 3u * sizeof(uint16_t)));

    size_t size = thumbW * thumbH * 3;
    for (size_t i = 0; i < size; i++) {
        out_buffer[i] = downscaled_processed_image[i] >> 8;
    }
    trace_look_assist_thumbnail("processed", frame_index, raw_w, raw_h, thumbW, thumbH,
                                downscale_factor, fnv1a64_bytes(out_buffer, size));

    /* Cleanup */
    free(downscaled_processed_image);
    free(downscaled_image);
    free(debayered_raw_frame);
    processingFreeClone(analysis_processing);
}

void get_area_average_downscale_raw_thumnail(mlvObject_t *video, int frame_index, int downscale_factor, unsigned char *out_buffer)
{
    if (!video || !out_buffer) {
        return;
    }

    int raw_w = video->RAWI.xRes;
    int raw_h = video->RAWI.yRes;

    if (raw_w <= 0 || raw_h <= 0 || downscale_factor <= 0) {
        return;
    }

    const int thumbW = raw_w / downscale_factor;
    const int thumbH = raw_h / downscale_factor;
    if (thumbW <= 0 || thumbH <= 0) {
        return;
    }

    uint16_t *debayered_raw_frame = get_isolated_thumbnail_source_rgb16(video, frame_index, raw_w, raw_h);
    if (!debayered_raw_frame) {
        return;
    }
    trace_look_assist_thumbnail("raw-source16", frame_index, raw_w, raw_h, raw_w, raw_h,
                                1, fnv1a64_bytes(debayered_raw_frame,
                                                 (size_t)raw_w * (size_t)raw_h * 3u * sizeof(uint16_t)));

    size_t size = (size_t)thumbW * (size_t)thumbH * 3u;
    downscale_rgb16_average_to_rgb8(debayered_raw_frame, raw_w, downscale_factor,
                                    thumbW, thumbH, out_buffer);
    trace_look_assist_thumbnail("raw", frame_index, raw_w, raw_h, thumbW, thumbH,
                                downscale_factor, fnv1a64_bytes(out_buffer, size));

    free(debayered_raw_frame);
}
