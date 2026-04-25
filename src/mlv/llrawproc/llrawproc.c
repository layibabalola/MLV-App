/*
 * Copyright (C) 2017 Bouncyball
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

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "llrawproc.h"
#include "pixelproc.h"
#include "stripes.h"
#include "patternnoise.h"
#include "dualiso.h"
#include "hist.h"
#include "darkframe.h"
#include "../../debug/StageTiming.h"
#include "../../processing/raw_processing.h"
#include "../pipeline_stage_capture.h"

#define MIN(a,b) (((a)<(b))?(a):(b))
#define MAX(a,b) (((a)>(b))?(a):(b))
#define COERCE(x,lo,hi) MAX(MIN((x),(hi)),(lo))
#define ABS(a) ((a) > 0 ? (a) : -(a))

#if defined(_MSC_VER)
#define MLV_THREAD_LOCAL __declspec(thread)
#else
#define MLV_THREAD_LOCAL __thread
#endif

static MLV_THREAD_LOCAL double g_llrawproc_last_shared_lock_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_dualiso_refine_lock_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_publish_lock_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_total_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_dark_frame_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_vertical_stripes_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_focus_pixels_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_bad_pixels_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_pattern_noise_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_dual_iso_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_chroma_smooth_ms = 0.0;
static double g_llrawproc_last_preview_histogram_ms = 0.0;
static double g_llrawproc_last_preview_regression_ms = 0.0;
static double g_llrawproc_last_preview_rowscale_ms = 0.0;
static MLV_THREAD_LOCAL uint64_t g_llrawproc_debug_pixel_map_copy_count = 0;
static MLV_THREAD_LOCAL uint64_t g_llrawproc_debug_dark_frame_copy_count = 0;
static MLV_THREAD_LOCAL uint64_t g_llrawproc_debug_runtime_publish_count = 0;

/* Diagnostic-only escape hatch: set MLVAPP_DISABLE_DUALISO_PLAYBACK_MEAN23_OVERRIDE=1
 * to make applyLLRawProcObject ignore the diso_playback_force_mean23 field
 * and let the receipt's diso_averaging flow through untouched. This is the
 * peer of MLVAPP_PROFILE_DISABLE_DUALISO_OVERRIDE (which exists on the GUI
 * side at platform/qt/DualIsoPlaybackPolicy.h:24-43); both are intended for
 * A/B harness runs where we want to measure the cost of the override
 * itself. NOT for production playback.
 *
 * Cached after first read so the per-frame fast-path stays branchless;
 * tests reset it via llrpReinitMean23OverrideDispatchForTesting() (mirrors
 * dualisoHqReinitDispatchForTesting at dualiso.c). */
static int g_dualiso_playback_mean23_override_env_cache = -1;

static int dualiso_playback_mean23_override_disabled_via_env(void)
{
    if (g_dualiso_playback_mean23_override_env_cache < 0)
    {
        const char * v = getenv("MLVAPP_DISABLE_DUALISO_PLAYBACK_MEAN23_OVERRIDE");
        if (v && *v && strcmp(v, "0") != 0
                  && strcmp(v, "false") != 0
                  && strcmp(v, "FALSE") != 0
                  && strcmp(v, "False") != 0)
        {
            g_dualiso_playback_mean23_override_env_cache = 1;
        }
        else
        {
            g_dualiso_playback_mean23_override_env_cache = 0;
        }
    }
    return g_dualiso_playback_mean23_override_env_cache;
}

/* Test-only hook: force re-evaluation of the env-disable cache from the
 * current process env. Mirrors dualisoHqReinitDispatchForTesting. Not in
 * the public header; tests forward-declare it. */
int llrpReinitMean23OverrideDispatchForTesting(void);
int llrpReinitMean23OverrideDispatchForTesting(void)
{
    g_dualiso_playback_mean23_override_env_cache = -1;
    return dualiso_playback_mean23_override_disabled_via_env();
}

static int llrawproc_worker_copy_pixel_map(pixel_map * destination,
                                           const pixel_map * source)
{
    if (!destination || !source)
    {
        return 0;
    }

    destination->type = source->type;
    destination->count = source->count;

    if (!source->pixels || source->count == 0)
    {
        return 1;
    }

    if (destination->capacity < source->count)
    {
        pixel_xy * resized = realloc(destination->pixels, source->count * sizeof(pixel_xy));
        if (!resized)
        {
            destination->count = 0;
            return 0;
        }
        destination->pixels = resized;
        destination->capacity = source->count;
    }

    memcpy(destination->pixels, source->pixels, source->count * sizeof(pixel_xy));
    g_llrawproc_debug_pixel_map_copy_count++;
    return 1;
}

static int llrawproc_worker_ensure_u16_copy(uint16_t ** destination,
                                            uint32_t * capacity_bytes,
                                            const uint16_t * source,
                                            uint32_t source_bytes)
{
    if (!destination || !capacity_bytes)
    {
        return 0;
    }

    if (!source || source_bytes == 0)
    {
        return 1;
    }

    if (*capacity_bytes < source_bytes)
    {
        uint16_t * resized = realloc(*destination, source_bytes);
        if (!resized)
        {
            return 0;
        }
        *destination = resized;
        *capacity_bytes = source_bytes;
    }

    memcpy(*destination, source, source_bytes);
    return 1;
}

static void llrawproc_free_worker_state(llrawprocWorkerState_t * worker)
{
    if (!worker) return;

    free_luts(worker->raw2ev, worker->ev2raw);
    worker->raw2ev = NULL;
    worker->ev2raw = NULL;
    worker->prev_black_level = -1;
    free(worker->focus_pixel_map_copy.pixels);
    memset(&worker->focus_pixel_map_copy, 0, sizeof(worker->focus_pixel_map_copy));
    free(worker->bad_pixel_map_copy.pixels);
    memset(&worker->bad_pixel_map_copy, 0, sizeof(worker->bad_pixel_map_copy));
    worker->focus_pixel_map_version = 0;
    worker->bad_pixel_map_version = 0;
    free(worker->dark_frame_data_copy);
    worker->dark_frame_data_copy = NULL;
    worker->dark_frame_size = 0;
    worker->dark_frame_capacity = 0;
    worker->dark_frame_version = 0;
    memset(&worker->dark_frame_hdr_copy, 0, sizeof(worker->dark_frame_hdr_copy));

    free(worker->chroma_smooth_scratch.buffer);
    worker->chroma_smooth_scratch.buffer = NULL;
    worker->chroma_smooth_scratch.capacity = 0;

    free_pattern_noise_scratch(&worker->pattern_noise_scratch);
    free_vertical_stripes_scratch(&worker->vertical_stripes_scratch);

    free(worker->diso_preview_scratch.data_x);
    free(worker->diso_preview_scratch.data_y);
    free(worker->diso_preview_scratch.data_w);
    free(worker->diso_preview_scratch.output_image);
    memset(&worker->diso_preview_scratch, 0, sizeof(worker->diso_preview_scratch));

    free_dualiso_full20bit_scratch(&worker->diso_full20bit_scratch);
    memset(&worker->diso_full20bit_scratch, 0, sizeof(worker->diso_full20bit_scratch));

    worker->dng_bit_depth = 0;
    worker->dng_black_level = 0;
    worker->dng_white_level = 0;
}

static uint32_t llrawproc_next_version(uint32_t current_version)
{
    current_version++;
    return current_version ? current_version : 1u;
}

static void llrawproc_bump_focus_map_version(llrawprocObject_t * shared)
{
    if (!shared) return;
    shared->focus_pixel_map_version = llrawproc_next_version(shared->focus_pixel_map_version);
}

static void llrawproc_bump_bad_map_version(llrawprocObject_t * shared)
{
    if (!shared) return;
    shared->bad_pixel_map_version = llrawproc_next_version(shared->bad_pixel_map_version);
}

static const pixel_map * llrawproc_worker_get_focus_map_copy(llrawprocWorkerState_t * worker,
                                                             const llrawprocObject_t * shared)
{
    if (!worker || !shared || shared->fpm_status != 2)
    {
        return NULL;
    }

    if (worker->focus_pixel_map_version != shared->focus_pixel_map_version)
    {
        if (!llrawproc_worker_copy_pixel_map(&worker->focus_pixel_map_copy, &shared->focus_pixel_map))
        {
            return NULL;
        }
        worker->focus_pixel_map_version = shared->focus_pixel_map_version;
    }

    return &worker->focus_pixel_map_copy;
}

static const pixel_map * llrawproc_worker_get_bad_map_copy(llrawprocWorkerState_t * worker,
                                                           const llrawprocObject_t * shared)
{
    if (!worker || !shared || shared->bpm_status != 2)
    {
        return NULL;
    }

    if (worker->bad_pixel_map_version != shared->bad_pixel_map_version)
    {
        if (!llrawproc_worker_copy_pixel_map(&worker->bad_pixel_map_copy, &shared->bad_pixel_map))
        {
            return NULL;
        }
        worker->bad_pixel_map_version = shared->bad_pixel_map_version;
    }

    return &worker->bad_pixel_map_copy;
}

static int llrawproc_worker_sync_dark_frame_copy(llrawprocWorkerState_t * worker,
                                                 const llrawprocObject_t * shared)
{
    if (!worker || !shared)
    {
        return 0;
    }

    if (worker->dark_frame_version == shared->dark_frame_version)
    {
        return 1;
    }

    if (!shared->dark_frame_data || shared->dark_frame_size == 0)
    {
        worker->dark_frame_size = 0;
        memset(&worker->dark_frame_hdr_copy, 0, sizeof(worker->dark_frame_hdr_copy));
        worker->dark_frame_version = shared->dark_frame_version;
        return 1;
    }

    if (!llrawproc_worker_ensure_u16_copy(&worker->dark_frame_data_copy,
                                          &worker->dark_frame_capacity,
                                          shared->dark_frame_data,
                                          shared->dark_frame_size))
    {
        return 0;
    }

    worker->dark_frame_size = shared->dark_frame_size;
    worker->dark_frame_hdr_copy = shared->dark_frame_hdr;
    worker->dark_frame_version = shared->dark_frame_version;
    g_llrawproc_debug_dark_frame_copy_count++;
    return 1;
}

static void llrawproc_worker_reset_dng_bw_levels(llrawprocWorkerState_t * worker,
                                                 const struct raw_info * raw_info)
{
    if (!worker || !raw_info) return;

    worker->dng_bit_depth = raw_info->bits_per_pixel;
    worker->dng_black_level = raw_info->black_level;
    worker->dng_white_level = raw_info->white_level;
}

static llrawproc_runtime_state_t llrawproc_capture_worker_runtime_state(const llrawprocWorkerState_t * worker)
{
    llrawproc_runtime_state_t state = { 0 };
    if (!worker) return state;

    state.diso_pattern = worker->diso_pattern;
    state.diso_auto_correction = worker->diso_auto_correction;
    state.diso_ev_correction = worker->diso_ev_correction;
    state.diso_black_delta = worker->diso_black_delta;
    state.dng_bit_depth = worker->dng_bit_depth;
    state.dng_black_level = worker->dng_black_level;
    state.dng_white_level = worker->dng_white_level;
    return state;
}

static llrawproc_runtime_state_t llrawproc_capture_shared_runtime_state(const llrawprocObject_t * shared)
{
    llrawproc_runtime_state_t state = { 0 };
    if (!shared) return state;

    state.diso_pattern = shared->diso_pattern;
    state.diso_auto_correction = shared->diso_auto_correction;
    state.diso_ev_correction = shared->diso_ev_correction;
    state.diso_black_delta = shared->diso_black_delta;
    state.dng_bit_depth = shared->dng_bit_depth;
    state.dng_black_level = shared->dng_black_level;
    state.dng_white_level = shared->dng_white_level;
    return state;
}

static int llrawproc_runtime_state_equal(const llrawproc_runtime_state_t * lhs,
                                         const llrawproc_runtime_state_t * rhs,
                                         int compare_auto_correction)
{
    if (!lhs || !rhs) return 0;

    if (lhs->diso_pattern != rhs->diso_pattern) return 0;
    if (compare_auto_correction && lhs->diso_auto_correction != rhs->diso_auto_correction) return 0;
    if (lhs->diso_ev_correction != rhs->diso_ev_correction) return 0;
    if (lhs->diso_black_delta != rhs->diso_black_delta) return 0;
    if (lhs->dng_bit_depth != rhs->dng_bit_depth) return 0;
    if (lhs->dng_black_level != rhs->dng_black_level) return 0;
    if (lhs->dng_white_level != rhs->dng_white_level) return 0;
    return 1;
}

static void llrawproc_worker_ensure_luts(llrawprocWorkerState_t * worker, int32_t black_level)
{
    if (!worker) return;

    if (worker->prev_black_level == black_level && worker->raw2ev && worker->ev2raw)
    {
        return;
    }

    free_luts(worker->raw2ev, worker->ev2raw);
    worker->raw2ev = get_raw2ev(black_level);
    worker->ev2raw = get_ev2raw(black_level);
    worker->prev_black_level = black_level;
}

static llrawprocWorkerState_t * llrawproc_acquire_worker_state(mlvObject_t * video)
{
    if (!video) return NULL;

    pthread_t thread_id = pthread_self();
    llrawprocWorkerState_t * slot = NULL;

    pthread_mutex_lock(&video->llrawproc_worker_mutex);

    if (!video->llrawproc_workers)
    {
        const uint32_t initial_capacity = (uint32_t)MAX(video->cpu_cores + 4, 16);
        video->llrawproc_workers = calloc(initial_capacity, sizeof(llrawprocWorkerState_t));
        if (video->llrawproc_workers)
        {
            video->llrawproc_worker_capacity = initial_capacity;
        }
    }

    for (uint32_t i = 0; i < video->llrawproc_worker_capacity; ++i)
    {
        if (video->llrawproc_workers[i].in_use
         && pthread_equal(video->llrawproc_workers[i].thread_id, thread_id))
        {
            slot = &video->llrawproc_workers[i];
            break;
        }
    }

    if (!slot)
    {
        for (uint32_t i = 0; i < video->llrawproc_worker_capacity; ++i)
        {
            if (!video->llrawproc_workers[i].in_use)
            {
                slot = &video->llrawproc_workers[i];
                memset(slot, 0, sizeof(*slot));
                slot->in_use = 1;
                slot->thread_id = thread_id;
                slot->prev_black_level = -1;
                break;
            }
        }
    }

    if (!slot)
    {
#ifndef STDOUT_SILENT
        fprintf(stderr, "llrawproc: worker pool exhausted, using stack scratch for this call.\n");
#endif
    }

    pthread_mutex_unlock(&video->llrawproc_worker_mutex);
    return slot;
}

static void llrawproc_publish_worker_results(mlvObject_t * video,
                                             const llrawproc_runtime_state_t * runtime_state,
                                             int publish_auto_correction)
{
    if (!video || !video->llrawproc || !runtime_state) return;

    video->llrawproc->diso_pattern = runtime_state->diso_pattern;
    if (publish_auto_correction)
    {
        video->llrawproc->diso_auto_correction = runtime_state->diso_auto_correction;
    }
    video->llrawproc->diso_ev_correction = runtime_state->diso_ev_correction;
    video->llrawproc->diso_black_delta = runtime_state->diso_black_delta;
    video->llrawproc->dng_bit_depth = runtime_state->dng_bit_depth;
    video->llrawproc->dng_black_level = runtime_state->dng_black_level;
    video->llrawproc->dng_white_level = runtime_state->dng_white_level;
    g_llrawproc_debug_runtime_publish_count++;
}

static void llrawproc_reset_force_bad_pixel_search(mlvObject_t * video, int bad_pixels)
{
    if (!video || !video->llrawproc || bad_pixels != 2) return;

    const double reset_lock_start = mlv_stage_timing_now();
    pthread_mutex_lock(&video->llrawproc_mutex);
    if (video->llrawproc->bpm_status == 2)
    {
        video->llrawproc->bpm_status = 1;
        video->llrawproc->bad_pixel_map.count = 0;
    }
    pthread_mutex_unlock(&video->llrawproc_mutex);
    g_llrawproc_last_shared_lock_ms += (mlv_stage_timing_now() - reset_lock_start) * 1000.0;
#ifndef STDOUT_SILENT
    printf("Searching bad pixels for every frame\n");
#endif
}

/* this is DNG feature only */
static void deflicker(mlvObject_t * video, uint16_t * raw_image_buff, size_t raw_image_size)
{
    uint16_t black = video->RAWI.raw_info.black_level;
    uint16_t white = (1 << video->RAWI.raw_info.bits_per_pixel) - 1;

    struct histogram * hist = hist_create(white);
    hist_add(hist, raw_image_buff + 1, (uint32_t)((raw_image_size - 1) / 2), 1);
    uint16_t median = hist_median(hist);
    double correction = log2((double) (video->llrawproc->deflicker_target - black) / (median - black));
    video->RAWI.raw_info.exposure_bias[0] = correction * 10000;
    video->RAWI.raw_info.exposure_bias[1] = 10000;
}

/* convert uncompressed 10/12bit raw data to 14bit for subsequent processing */
static void make_14bit(uint16_t * raw_image_buff, size_t raw_image_size, struct raw_info * raw_info)
{
    uint32_t pixel_count = raw_image_size / 2;
    int bits_shift = 14 - raw_info->bits_per_pixel;
    raw_info->black_level <<= bits_shift;
    raw_info->white_level <<= bits_shift;
    raw_info->bits_per_pixel = 14;
    raw_info->frame_size = raw_info->width * raw_info->height * 14 / 8;

    #pragma omp parallel for
    for(uint32_t i = 0; i < pixel_count; ++i)
    {
        raw_image_buff[i] <<= bits_shift;
    }
}

/* undo 14bit conversion to initial bit depth with rounding error minimizing */
static void undo_14bit(uint16_t * raw_image_buff, size_t raw_image_size, uint32_t bpp)
{
    uint32_t pixel_count = raw_image_size / 2;
    int bits_shift = 14 - bpp;
    /* calculate rounding number to be added to the raw value before shifting right to minimize rounding error */
    uint32_t rounding_number = (uint32_t)pow(2, bits_shift - 1);

    #pragma omp parallel for
    for(uint32_t i = 0; i < pixel_count; ++i)
    {
        raw_image_buff[i] = (raw_image_buff[i] + rounding_number) >> bits_shift;
    }
}

/* rescale restricted to imaginary 10-12bit levels of lossless raw data to about real 14bit range */
static void _scale_restricted_range(struct raw_info * raw_info, uint16_t * image_data)
{
    uint32_t pixel_count = raw_info->width * raw_info->height;
    /* find min and max level values in the currecnt raw frame */
    int32_t min_level = image_data[0];
    int32_t max_level = image_data[0];
    for(uint32_t i = 1; i < pixel_count; ++i)
    {
        if(image_data[i] < min_level) min_level = image_data[i];
        if(image_data[i] > max_level) max_level = image_data[i];
    }
#ifndef STDOUT_SILENT
    printf("min_level = %d, max_level = %d\n", min_level, max_level);
#endif
    raw_info->black_level = MAX(min_level, raw_info->black_level);
    raw_info->white_level = MAX(max_level, raw_info->white_level);

    int32_t scaled_white_level = 16200;
    double scale_ratio = (double)(scaled_white_level - raw_info->black_level) / (double)(raw_info->white_level - raw_info->black_level);
    raw_info->white_level = scaled_white_level;

#pragma omp parallel for
    for(uint32_t i = 0; i < pixel_count; ++i)
    {
        image_data[i] = MIN( (uint16_t)((double)((image_data[i] - raw_info->black_level) * scale_ratio + raw_info->black_level) + 0.5), 16383);
    }
}

/* rescale restricted to imaginary 10-12bit levels of lossless raw data to about real 14bit range */
static void scale_restricted_range(struct raw_info * raw_info, uint16_t * image_data, int low_iso, int high_iso)
{
    int32_t bd = ceil(log2(raw_info->white_level - raw_info->black_level));

    // Digital gain? Add 1 bit…
    int32_t add_bit = 0;

    if (low_iso != high_iso && high_iso >= 6400)
    {
        add_bit = 1;
    }

    int32_t actual_white_level = raw_info->black_level + ((1 << (bd + add_bit)) - 1);
    int32_t scaled_white_level = (raw_info->white_level - raw_info->black_level) * (1 << (14 - bd));

    double scale_ratio = (double)(scaled_white_level - raw_info->black_level) / (double)(actual_white_level - raw_info->black_level);

    raw_info->white_level = scaled_white_level;

    uint32_t pixel_count = raw_info->width * raw_info->height;

    #pragma omp parallel for
    for (uint32_t i = 0; i < pixel_count; ++i)
    {
        image_data[i] = MIN((uint16_t)((double)((image_data[i] - raw_info->black_level) * scale_ratio + raw_info->black_level) + 0.5), 16383);
    }
}

/* initialise low level raw processing struct */
llrawprocObject_t * initLLRawProcObject()
{
    llrawprocObject_t * llrawproc = calloc(1, sizeof(llrawprocObject_t));

    /* set defaults */
    llrawproc->vertical_stripes = 1;
    llrawproc->focus_pixels = 0;
    llrawproc->fpi_method = 0;
    llrawproc->bad_pixels = 1;
    llrawproc->bps_method = 0;
    llrawproc->bpi_method = 0;
    llrawproc->chroma_smooth = 0;
    llrawproc->pattern_noise = 0;
    llrawproc->deflicker_target = 0;
    llrawproc->fpm_status = 0;
    llrawproc->bpm_status = 0;
    llrawproc->compute_stripes = 0;
    llrawproc->dual_iso = 0;
    llrawproc->diso_pattern = 0;
    llrawproc->diso_auto_correction = -1;
    llrawproc->diso_ev_correction = 0;
    llrawproc->diso_black_delta = 0;
    llrawproc->diso_averaging = 0;
    llrawproc->diso_playback_force_mean23 = 0;
    llrawproc->diso_alias_map = 0;
    llrawproc->diso_frblending = 1;
    llrawproc->dark_frame = 0;

    llrawproc->dark_frame_filename = NULL;
    llrawproc->dark_frame_loaded_filename = NULL;
    llrawproc->dark_frame_loaded_mode = DF_OFF;
    llrawproc->dark_frame_data = NULL;
    llrawproc->dark_frame_size = 0;
    llrawproc->dark_frame_version = 1;

    llrawproc->raw2ev = NULL;
    llrawproc->ev2raw = NULL;

    llrawproc->prev_black_level = -1;

    llrawproc->focus_pixel_map.type = PIX_FOCUS;
    llrawproc->focus_pixel_map.pixels = NULL;
    llrawproc->focus_pixel_map_version = 1;
    llrawproc->bad_pixel_map.type = PIX_BAD;
    llrawproc->bad_pixel_map.pixels = NULL;
    llrawproc->bad_pixel_map_version = 1;

    return llrawproc;
}

void freeLLRawProcObject(mlvObject_t * video)
{
    df_free_filename(video);
    df_free(video);

    if (video->llrawproc_workers)
    {
        for (uint32_t i = 0; i < video->llrawproc_worker_capacity; ++i)
        {
            if (video->llrawproc_workers[i].in_use)
            {
                llrawproc_free_worker_state(&video->llrawproc_workers[i]);
            }
        }
        free(video->llrawproc_workers);
        video->llrawproc_workers = NULL;
        video->llrawproc_worker_capacity = 0;
    }

    free_luts(video->llrawproc->raw2ev, video->llrawproc->ev2raw);
    free(video->llrawproc->chroma_smooth_scratch.buffer);
    free_pattern_noise_scratch(&video->llrawproc->pattern_noise_scratch);
    free(video->llrawproc->diso_preview_scratch.data_x);
    free(video->llrawproc->diso_preview_scratch.data_y);
    free(video->llrawproc->diso_preview_scratch.data_w);
    free(video->llrawproc->diso_preview_scratch.output_image);
    free_dualiso_full20bit_scratch(&video->llrawproc->diso_full20bit_scratch);
    free_pixel_maps(&(video->llrawproc->focus_pixel_map), &(video->llrawproc->bad_pixel_map));
    free(video->llrawproc);
}

/* all low level raw processing takes place here */
void applyLLRawProcObject(mlvObject_t * video, uint16_t * raw_image_buff, size_t raw_image_size)
{
    const double apply_start = mlv_stage_timing_now();
    llrawprocObject_t * shared = video ? video->llrawproc : NULL;
    llrawprocWorkerState_t stack_worker;
    llrawprocWorkerState_t * worker = NULL;
    int using_stack_worker = 0;

    g_llrawproc_last_shared_lock_ms = 0.0;
    g_llrawproc_last_dualiso_refine_lock_ms = 0.0;
    g_llrawproc_last_publish_lock_ms = 0.0;
    g_llrawproc_last_total_ms = 0.0;
    g_llrawproc_last_dark_frame_ms = 0.0;
    g_llrawproc_last_vertical_stripes_ms = 0.0;
    g_llrawproc_last_focus_pixels_ms = 0.0;
    g_llrawproc_last_bad_pixels_ms = 0.0;
    g_llrawproc_last_pattern_noise_ms = 0.0;
    g_llrawproc_last_dual_iso_ms = 0.0;
    g_llrawproc_last_chroma_smooth_ms = 0.0;
    g_llrawproc_last_preview_histogram_ms = 0.0;
    g_llrawproc_last_preview_regression_ms = 0.0;
    g_llrawproc_last_preview_rowscale_ms = 0.0;
    g_llrawproc_debug_pixel_map_copy_count = 0;
    g_llrawproc_debug_dark_frame_copy_count = 0;
    if (!video || !shared || !shared->fix_raw)
    {
        g_llrawproc_last_total_ms = (mlv_stage_timing_now() - apply_start) * 1000.0;
        return;
    }

    double dark_frame_ms = 0.0;
    double vertical_stripes_ms = 0.0;
    double focus_pixels_ms = 0.0;
    double bad_pixels_ms = 0.0;
    double pattern_noise_ms = 0.0;
    double dual_iso_ms = 0.0;
    double chroma_smooth_ms = 0.0;

    memset(&stack_worker, 0, sizeof(stack_worker));
    stack_worker.prev_black_level = -1;
    worker = llrawproc_acquire_worker_state(video);
    if (!worker)
    {
        worker = &stack_worker;
        using_stack_worker = 1;
    }

    struct raw_info raw_info = video->RAWI.raw_info;
    const int original_bits_per_pixel = video->RAWI.raw_info.bits_per_pixel;
    const int x_res = video->RAWI.xRes;
    const int y_res = video->RAWI.yRes;
    const int camera_id = video->IDNT.cameraModel;
    const int pan_pos_x = video->VIDF.panPosX;
    const int pan_pos_y = video->VIDF.panPosY;
    const int raw_width = video->RAWI.raw_info.width;
    const int raw_height = video->RAWI.raw_info.height;
    const int crop_rec_mode = (llrpDetectFocusDotFixMode(video) == 2) ? 1 : 0;
    const int unified_mode = (video->MLVI.videoClass & MLV_VIDEO_CLASS_FLAG_LJ92) ? 5 : 0;

    int focus_pixels = 0;
    int fpi_method = 0;
    int bad_pixels = 0;
    int bps_method = 0;
    int bpi_method = 0;
    int chroma_smooth_mode = 0;
    int pattern_noise_mode = 0;
    int diso_validity = 0;
    int dual_iso_mode = 0;
    int diso1 = 0;
    int diso2 = 0;
    int diso_averaging = 0;
    int diso_alias_map = 0;
    int diso_frblending = 0;
    int dark_frame_mode = 0;
    int vertical_stripes_mode = 0;
    int worker_diso_pattern = 0;
    int worker_diso_auto_correction = 0;
    double worker_diso_ev_correction = 0.0;
    int worker_diso_black_delta = 0;
    int apply_dark_frame_outside_lock = 0;
    const uint16_t * dark_frame_data_for_subtraction = NULL;
    uint32_t dark_frame_size_for_subtraction = 0;
    uint32_t dark_frame_black_level = 0;
    const pixel_map * focus_map_for_interpolation = NULL;
    const pixel_map * bad_map_for_interpolation = NULL;
    int focus_status_snapshot = 0;
    int bad_status_snapshot = 0;
    int focus_interpolate_outside_lock = 0;
    int bad_interpolate_outside_lock = 0;
    int bad_force_reset_after_interpolation = 0;
    int apply_vertical_stripes_outside_lock = 0;
    stripes_correction stripe_correction_snapshot = { 0 };
    int compute_vertical_stripes_outside_lock = 0;
    int claimed_vertical_stripes_request = 0;
    int vertical_stripes_compute_succeeded = 0;

    if (original_bits_per_pixel < 14)
    {
        make_14bit(raw_image_buff, raw_image_size, &raw_info);
    }

    llrawproc_worker_reset_dng_bw_levels(worker, &raw_info);
    llrawproc_worker_ensure_luts(worker, raw_info.black_level);

    double shared_lock_start = mlv_stage_timing_now();
    pthread_mutex_lock(&video->llrawproc_mutex);

    if (!shared->fix_raw)
    {
        pthread_mutex_unlock(&video->llrawproc_mutex);
        g_llrawproc_last_shared_lock_ms = (mlv_stage_timing_now() - shared_lock_start) * 1000.0;
        g_llrawproc_last_total_ms = (mlv_stage_timing_now() - apply_start) * 1000.0;
        if (using_stack_worker) llrawproc_free_worker_state(worker);
        return;
    }

    if (!df_init(video))
    {
        const double dark_frame_start = mlv_stage_timing_now();
        if (llrawproc_worker_sync_dark_frame_copy(worker, shared)
         && worker->dark_frame_data_copy
         && worker->dark_frame_size == raw_image_size)
        {
            apply_dark_frame_outside_lock = 1;
            dark_frame_data_for_subtraction = worker->dark_frame_data_copy;
            dark_frame_size_for_subtraction = worker->dark_frame_size;
            dark_frame_black_level = worker->dark_frame_hdr_copy.black_level;
        }
        else
        {
#ifndef STDOUT_SILENT
            printf("Subtracting Dark Frame... ");
#endif
            df_subtract(video, raw_image_buff, raw_image_size);
#ifndef STDOUT_SILENT
            printf("Done\n\n");
#endif
        }
        dark_frame_ms += (mlv_stage_timing_now() - dark_frame_start) * 1000.0;
    }

    focus_pixels = shared->focus_pixels;
    fpi_method = shared->fpi_method;
    bad_pixels = shared->bad_pixels;
    bps_method = shared->bps_method;
    bpi_method = shared->bpi_method;
    chroma_smooth_mode = shared->chroma_smooth;
    pattern_noise_mode = shared->pattern_noise;
    diso_validity = shared->diso_validity;
    dual_iso_mode = shared->dual_iso;
    diso1 = shared->diso1;
    diso2 = shared->diso2;
    diso_averaging = shared->diso_averaging;
    /* Playback-only fast-path override: when set by the GUI playback policy
     * (platform/qt/DualIsoPlaybackPolicy.h), force HQ recon (dual_iso == 1)
     * to use mean23 instead of AMaZE. mean23 is also a matched-pair recon,
     * so the cast still closes; AMaZE costs ~150-200 ms p95 on 5K dual ISO,
     * mean23 costs ~30-50 ms. The receipt's authored diso_averaging is
     * preserved (the override only reads the flag here, never writes the
     * shared field) so paused/scrubbing/export keep AMaZE.
     *
     * Cache invalidation: diso_playback_force_mean23 is hashed by
     * mlv_hash_llrawproc_state, so the processed-frame cache slot signature
     * differs between playback-active (override=1) and paused (override=0)
     * — the same frame index produces two cache slots, and switching from
     * playback to paused presents the AMaZE pixels not the mean23 ones. */
    if (shared->diso_playback_force_mean23 != 0
        && !dualiso_playback_mean23_override_disabled_via_env())
    {
        diso_averaging = 1; /* DISOI_MEAN23 */
    }
    diso_alias_map = shared->diso_alias_map;
    diso_frblending = shared->diso_frblending;
    worker_diso_pattern = shared->diso_pattern;
    worker_diso_auto_correction = shared->diso_auto_correction;
    worker_diso_ev_correction = shared->diso_ev_correction;
    worker_diso_black_delta = shared->diso_black_delta;
    worker->seeded_runtime_state = llrawproc_capture_shared_runtime_state(shared);
    dark_frame_mode = shared->dark_frame;
    vertical_stripes_mode = shared->vertical_stripes;

    if (vertical_stripes_mode)
    {
        stripe_correction_snapshot = shared->stripe_corrections;
        compute_vertical_stripes_outside_lock = (shared->compute_stripes || vertical_stripes_mode == 2);
        if (shared->compute_stripes)
        {
            /* Claim the queued recompute before unlock so only one worker
               solves/publishes a one-shot request. Forced mode still
               recomputes every frame via vertical_stripes_mode == 2. */
            claimed_vertical_stripes_request = 1;
            shared->compute_stripes = 0;
        }
        if (!compute_vertical_stripes_outside_lock)
        {
            apply_vertical_stripes_outside_lock = stripe_correction_snapshot.correction_needed;
        }
    }

    if (compute_vertical_stripes_outside_lock)
    {
        pthread_mutex_unlock(&video->llrawproc_mutex);
        g_llrawproc_last_shared_lock_ms = (mlv_stage_timing_now() - shared_lock_start) * 1000.0;

        const double vertical_stripes_start = mlv_stage_timing_now();
        vertical_stripes_compute_succeeded = compute_vertical_stripes_correction_only(&stripe_correction_snapshot,
                                                                                      raw_image_buff,
                                                                                      raw_info.black_level,
                                                                                      raw_info.white_level,
                                                                                      raw_info.frame_size,
                                                                                      x_res,
                                                                                      y_res,
                                                                                      vertical_stripes_mode,
                                                                                      &worker->vertical_stripes_scratch);
        vertical_stripes_ms += (mlv_stage_timing_now() - vertical_stripes_start) * 1000.0;

        const double stripes_publish_lock_start = mlv_stage_timing_now();
        pthread_mutex_lock(&video->llrawproc_mutex);
        if (vertical_stripes_compute_succeeded)
        {
            shared->stripe_corrections = stripe_correction_snapshot;
        }
        else if (claimed_vertical_stripes_request)
        {
            shared->compute_stripes = 1;
        }
        const double stripes_publish_lock_end = mlv_stage_timing_now();
        g_llrawproc_last_shared_lock_ms += (stripes_publish_lock_end - stripes_publish_lock_start) * 1000.0;
        shared_lock_start = stripes_publish_lock_end;
        stripe_correction_snapshot = shared->stripe_corrections;
        apply_vertical_stripes_outside_lock = stripe_correction_snapshot.correction_needed;
    }

    if (focus_pixels)
    {
        const double focus_pixels_start = mlv_stage_timing_now();
        if (shared->fpm_status < 2)
        {
            int crop_rec = crop_rec_mode ? 1 : (focus_pixels == 2);
            prepare_focus_pixel_map(&shared->focus_pixel_map,
                                    &shared->fpm_status,
                                    camera_id,
                                    raw_width,
                                    raw_height,
                                    crop_rec,
                                    unified_mode);
            llrawproc_bump_focus_map_version(shared);
        }
        focus_status_snapshot = shared->fpm_status;
        if (focus_status_snapshot == 2)
        {
            focus_map_for_interpolation = llrawproc_worker_get_focus_map_copy(worker, shared);
            focus_interpolate_outside_lock = (focus_map_for_interpolation != NULL);
        }
        else focus_interpolate_outside_lock = 0;
        focus_pixels_ms += (mlv_stage_timing_now() - focus_pixels_start) * 1000.0;
    }

    if (bad_pixels)
    {
        const double bad_pixels_start = mlv_stage_timing_now();
        if (shared->bpm_status < 2 || (shared->bpm_status == 2 && bad_pixels == 2))
        {
            bad_force_reset_after_interpolation = prepare_bad_pixel_map(&shared->bad_pixel_map,
                                                                        &shared->bpm_status,
                                                                        raw_image_buff,
                                                                        camera_id,
                                                                        x_res,
                                                                        y_res,
                                                                        pan_pos_x,
                                                                        pan_pos_y,
                                                                        raw_width,
                                                                        raw_height,
                                                                        raw_info.black_level,
                                                                        bad_pixels,
                                                                        bps_method,
                                                                        worker->raw2ev);
            llrawproc_bump_bad_map_version(shared);
        }
        bad_status_snapshot = shared->bpm_status;
        if (bad_status_snapshot == 2)
        {
            bad_map_for_interpolation = llrawproc_worker_get_bad_map_copy(worker, shared);
            bad_interpolate_outside_lock = (bad_map_for_interpolation != NULL);
        }
        else bad_interpolate_outside_lock = 0;
        bad_pixels_ms += (mlv_stage_timing_now() - bad_pixels_start) * 1000.0;
    }

    pthread_mutex_unlock(&video->llrawproc_mutex);
    g_llrawproc_last_shared_lock_ms += (mlv_stage_timing_now() - shared_lock_start) * 1000.0;

    worker->diso_pattern = worker_diso_pattern;
    worker->diso_auto_correction = worker_diso_auto_correction;
    worker->diso_ev_correction = worker_diso_ev_correction;
    worker->diso_black_delta = worker_diso_black_delta;

    if (apply_dark_frame_outside_lock)
    {
        const double dark_frame_start = mlv_stage_timing_now();
        df_subtract_snapshot(dark_frame_data_for_subtraction,
                             dark_frame_size_for_subtraction,
                             dark_frame_black_level,
                             raw_info.bits_per_pixel,
                             raw_image_buff,
                             raw_image_size);
        dark_frame_ms += (mlv_stage_timing_now() - dark_frame_start) * 1000.0;
    }

    if (apply_vertical_stripes_outside_lock)
    {
        const double vertical_stripes_start = mlv_stage_timing_now();
        apply_vertical_stripes_correction_only(&stripe_correction_snapshot,
                                               raw_image_buff,
                                               raw_info.black_level,
                                               raw_info.white_level,
                                               x_res,
                                               y_res);
        vertical_stripes_ms += (mlv_stage_timing_now() - vertical_stripes_start) * 1000.0;
    }

    if (focus_pixels && focus_interpolate_outside_lock && focus_status_snapshot == 2 && focus_map_for_interpolation)
    {
        const double focus_pixels_start = mlv_stage_timing_now();
        interpolate_focus_pixel_map(focus_map_for_interpolation,
                                    raw_image_buff,
                                    x_res,
                                    y_res,
                                    pan_pos_x,
                                    pan_pos_y,
                                    fpi_method,
                                    dual_iso_mode,
                                    worker->raw2ev,
                                    worker->ev2raw);
        focus_pixels_ms += (mlv_stage_timing_now() - focus_pixels_start) * 1000.0;
    }

    if (bad_pixels && bad_interpolate_outside_lock && bad_status_snapshot == 2 && bad_map_for_interpolation)
    {
        const double bad_pixels_start = mlv_stage_timing_now();
        interpolate_bad_pixel_map(bad_map_for_interpolation,
                                  raw_image_buff,
                                  x_res,
                                  y_res,
                                  pan_pos_x,
                                  pan_pos_y,
                                  bpi_method,
                                  dual_iso_mode,
                                  worker->raw2ev,
                                  worker->ev2raw);
        if (bad_force_reset_after_interpolation)
        {
            llrawproc_reset_force_bad_pixel_search(video, bad_pixels);
        }
        bad_pixels_ms += (mlv_stage_timing_now() - bad_pixels_start) * 1000.0;
    }

    if (!diso_validity && pattern_noise_mode)
    {
        const double pattern_noise_start = mlv_stage_timing_now();
#ifndef STDOUT_SILENT
        printf("Fixing pattern noise... ");
#endif
        fix_pattern_noise((int16_t *)raw_image_buff,
                          x_res,
                          y_res,
                          raw_info.white_level,
                          0,
                          &worker->pattern_noise_scratch);
#ifndef STDOUT_SILENT
        printf("Done\n\n");
#endif
        pattern_noise_ms += (mlv_stage_timing_now() - pattern_noise_start) * 1000.0;
    }

    int publish_auto_correction = 1;

    /* S1_pre_dualiso capture: post focus pixel / bad pixel / chroma smooth /
     * pattern noise / dark frame / vertical stripes, but pre Dual ISO recon.
     * Inert when MLVAPP_PIPELINE_CAPTURE_DIR is unset. The current frame
     * index is read from the thread-local set by the caller of
     * applyLLRawProcObject. */
    {
        const uint64_t frame_index = mlv_pipeline_capture_get_current_frame();
        if (mlv_pipeline_capture_should_capture_frame(frame_index))
        {
            mlv_pipeline_capture_meta_t meta;
            memset(&meta, 0, sizeof meta);
            meta.stage = MLV_PIPELINE_STAGE_S1_PRE_DUALISO;
            meta.format = MLV_PIPELINE_FORMAT_UINT16_MONO;
            meta.format_label = "uint16_bayer_pre_dualiso";
            meta.width = x_res;
            meta.height = y_res;
            meta.bytes_per_line = x_res * (int)sizeof(uint16_t);
            meta.bytes_per_pixel = (int)sizeof(uint16_t);
            meta.channels = 1;
            meta.bit_depth = 16;
            meta.dual_iso_mode = (dual_iso_mode == 0) ? "off"
                               : (dual_iso_mode == 1) ? "full"
                               : (dual_iso_mode == 2) ? "preview"
                               : "unknown";
            meta.debayer_mode = "n/a";
            meta.scaler = "none";
            meta.path_label = "applyLLRawProcObject_pre_dualiso";
            mlv_pipeline_capture(frame_index, raw_image_buff, &meta);
        }
    }

    if (diso_validity && dual_iso_mode)
    {
        raw_info.width = x_res;
        raw_info.height = y_res;
        raw_info.pitch = x_res;
        raw_info.active_area.x1 = 0;
        raw_info.active_area.y1 = 0;
        raw_info.active_area.x2 = raw_info.width;
        raw_info.active_area.y2 = raw_info.height;

        int restricted_lossless = (video->MLVI.videoClass & MLV_VIDEO_CLASS_FLAG_LJ92) && raw_info.white_level < 15000;

        if (restricted_lossless)
        {
            const double dual_iso_start = mlv_stage_timing_now();
#ifndef STDOUT_SILENT
            printf("\nScaling raw data range...\n");
            printf("Raw_Black = %d, Raw_White = %d <= BEFORE SCALING\n", raw_info.black_level, raw_info.white_level);
#endif
            int low_iso = MIN(diso1, diso2);
            int high_iso = MAX(diso1, diso2);
            scale_restricted_range(&raw_info, raw_image_buff, low_iso, high_iso);
            llrawproc_worker_reset_dng_bw_levels(worker, &raw_info);
#ifndef STDOUT_SILENT
            printf("Raw_Black = %d, Raw_White = %d <= AFTER SCALING\n", raw_info.black_level, raw_info.white_level);
#endif
            dual_iso_ms += (mlv_stage_timing_now() - dual_iso_start) * 1000.0;
        }

        if (dual_iso_mode == 1)
        {
            const double dual_iso_start = mlv_stage_timing_now();
            int explicit_auto_correction = 0;
            double explicit_ev_correction = worker->diso_ev_correction;
            int explicit_black_delta = worker->diso_black_delta;
            const int has_explicit_auto_match =
                (worker->diso_auto_correction < 0) &&
                (worker->diso_ev_correction != 1) &&
                (worker->diso_black_delta != -1);

            int * auto_correction_ptr = has_explicit_auto_match
                ? &explicit_auto_correction
                : &worker->diso_auto_correction;
            double * ev_correction_ptr = has_explicit_auto_match
                ? &explicit_ev_correction
                : &worker->diso_ev_correction;
            int * black_delta_ptr = has_explicit_auto_match
                ? &explicit_black_delta
                : &worker->diso_black_delta;

            publish_auto_correction = !has_explicit_auto_match;

            diso_get_full20bit(raw_info,
                               raw_image_buff,
                               dark_frame_mode,
                               diso1,
                               diso2,
                               &worker->diso_pattern,
                               auto_correction_ptr,
                               ev_correction_ptr,
                               black_delta_ptr,
                               diso_averaging,
                               diso_alias_map,
                               diso_frblending,
                               chroma_smooth_mode,
                               video->cpu_cores,
                               &worker->diso_full20bit_scratch);
            dual_iso_ms += (mlv_stage_timing_now() - dual_iso_start) * 1000.0;

            if (has_explicit_auto_match)
            {
                worker->diso_ev_correction = explicit_ev_correction;
                worker->diso_black_delta = explicit_black_delta;
            }

            {
                int bits_shift = 16 - raw_info.bits_per_pixel;
                worker->dng_black_level = raw_info.black_level << bits_shift;
                worker->dng_white_level = raw_info.white_level << bits_shift;
                worker->dng_bit_depth = 16;
            }

            llrawproc_worker_ensure_luts(worker, worker->dng_black_level);

            const double refine_lock_start = mlv_stage_timing_now();
            focus_status_snapshot = 0;
            bad_status_snapshot = 0;
            focus_interpolate_outside_lock = 0;
            bad_interpolate_outside_lock = 0;
            bad_force_reset_after_interpolation = 0;
            focus_map_for_interpolation = NULL;
            bad_map_for_interpolation = NULL;
            pthread_mutex_lock(&video->llrawproc_mutex);
            if (focus_pixels)
            {
                if (shared->fpm_status < 2)
                {
                    int crop_rec = crop_rec_mode ? 1 : (focus_pixels == 2);
                    prepare_focus_pixel_map(&shared->focus_pixel_map,
                                            &shared->fpm_status,
                                            camera_id,
                                            raw_width,
                                            raw_height,
                                            crop_rec,
                                            unified_mode);
                    llrawproc_bump_focus_map_version(shared);
                }
                focus_status_snapshot = shared->fpm_status;
                if (focus_status_snapshot == 2)
                {
                    focus_map_for_interpolation = llrawproc_worker_get_focus_map_copy(worker, shared);
                    focus_interpolate_outside_lock = (focus_map_for_interpolation != NULL);
                }
                else focus_interpolate_outside_lock = 0;
            }

            if (bad_pixels)
            {
                if (shared->bpm_status < 2 || (shared->bpm_status == 2 && bad_pixels == 2))
                {
                    bad_force_reset_after_interpolation = prepare_bad_pixel_map(&shared->bad_pixel_map,
                                                                                &shared->bpm_status,
                                                                                raw_image_buff,
                                                                                camera_id,
                                                                                x_res,
                                                                                y_res,
                                                                                pan_pos_x,
                                                                                pan_pos_y,
                                                                                raw_width,
                                                                                raw_height,
                                                                                raw_info.black_level,
                                                                                bad_pixels,
                                                                                bps_method,
                                                                                worker->raw2ev);
                    llrawproc_bump_bad_map_version(shared);
                }
                bad_status_snapshot = shared->bpm_status;
                if (bad_status_snapshot == 2)
                {
                    bad_map_for_interpolation = llrawproc_worker_get_bad_map_copy(worker, shared);
                    bad_interpolate_outside_lock = (bad_map_for_interpolation != NULL);
                }
                else bad_interpolate_outside_lock = 0;
            }
            pthread_mutex_unlock(&video->llrawproc_mutex);
            g_llrawproc_last_dualiso_refine_lock_ms += (mlv_stage_timing_now() - refine_lock_start) * 1000.0;
            g_llrawproc_last_shared_lock_ms += g_llrawproc_last_dualiso_refine_lock_ms;

                if (focus_pixels && focus_interpolate_outside_lock && focus_status_snapshot == 2 && focus_map_for_interpolation)
                {
                    const double focus_pixels_start = mlv_stage_timing_now();
                    interpolate_focus_pixel_map(focus_map_for_interpolation,
                                                raw_image_buff,
                                            x_res,
                                            y_res,
                                            pan_pos_x,
                                            pan_pos_y,
                                            2,
                                            0,
                                            worker->raw2ev,
                                            worker->ev2raw);
                    focus_pixels_ms += (mlv_stage_timing_now() - focus_pixels_start) * 1000.0;
            }

                if (bad_pixels && bad_interpolate_outside_lock && bad_status_snapshot == 2 && bad_map_for_interpolation)
                {
                    const double bad_pixels_start = mlv_stage_timing_now();
                    interpolate_bad_pixel_map(bad_map_for_interpolation,
                                              raw_image_buff,
                                          x_res,
                                          y_res,
                                          pan_pos_x,
                                          pan_pos_y,
                                          2,
                                          0,
                                          worker->raw2ev,
                                          worker->ev2raw);
                    if (bad_force_reset_after_interpolation)
                    {
                        llrawproc_reset_force_bad_pixel_search(video, bad_pixels);
                    }
                    bad_pixels_ms += (mlv_stage_timing_now() - bad_pixels_start) * 1000.0;
            }

            llrawproc_worker_ensure_luts(worker, raw_info.black_level);
        }
        else if (dual_iso_mode == 2)
        {
            const double dual_iso_start = mlv_stage_timing_now();
            diso_get_preview(raw_image_buff,
                             raw_info.width,
                             raw_info.height,
                             raw_info.black_level,
                             raw_info.white_level,
                             &worker->diso_pattern,
                             0,
                             &worker->diso_preview_scratch);
            dual_iso_ms += (mlv_stage_timing_now() - dual_iso_start) * 1000.0;
            g_llrawproc_last_preview_histogram_ms = worker->diso_preview_scratch.last_histogram_ms;
            g_llrawproc_last_preview_regression_ms = worker->diso_preview_scratch.last_regression_ms;
            g_llrawproc_last_preview_rowscale_ms = worker->diso_preview_scratch.last_rowscale_ms;
        }
    }

    /* S2_post_dualiso capture: post Dual ISO recon (full HQ or preview
     * rowscale), before chroma smooth. If dual_iso_mode==0 the capture
     * happens with no transformation since the if-block above was
     * skipped — useful as a "this clip has no Dual ISO" baseline. */
    {
        const uint64_t frame_index = mlv_pipeline_capture_get_current_frame();
        if (mlv_pipeline_capture_should_capture_frame(frame_index))
        {
            mlv_pipeline_capture_meta_t meta;
            memset(&meta, 0, sizeof meta);
            meta.stage = MLV_PIPELINE_STAGE_S2_POST_DUALISO;
            meta.format = MLV_PIPELINE_FORMAT_UINT16_MONO;
            meta.format_label = "uint16_bayer_post_dualiso";
            meta.width = x_res;
            meta.height = y_res;
            meta.bytes_per_line = x_res * (int)sizeof(uint16_t);
            meta.bytes_per_pixel = (int)sizeof(uint16_t);
            meta.channels = 1;
            meta.bit_depth = 16;
            meta.dual_iso_mode = (dual_iso_mode == 0) ? "off"
                               : (dual_iso_mode == 1) ? "full"
                               : (dual_iso_mode == 2) ? "preview"
                               : "unknown";
            meta.debayer_mode = "n/a";
            meta.scaler = "none";
            meta.path_label = "applyLLRawProcObject_post_dualiso";
            mlv_pipeline_capture(frame_index, raw_image_buff, &meta);
        }
    }

    if (chroma_smooth_mode && dual_iso_mode != 1)
    {
        const double chroma_smooth_start = mlv_stage_timing_now();
#ifndef STDOUT_SILENT
        printf("\nUsing chroma smooth method: '%dx%d'\n\n", chroma_smooth_mode, chroma_smooth_mode);
#endif
        chroma_smooth(chroma_smooth_mode,
                      raw_image_buff,
                      x_res,
                      y_res,
                      raw_info.black_level,
                      raw_info.white_level,
                      worker->raw2ev,
                      worker->ev2raw,
                      &worker->chroma_smooth_scratch);
        chroma_smooth_ms += (mlv_stage_timing_now() - chroma_smooth_start) * 1000.0;
    }

    if (original_bits_per_pixel < 14 && dual_iso_mode != 1)
    {
        undo_14bit(raw_image_buff, raw_image_size, video->RAWI.raw_info.bits_per_pixel);
    }

    {
        const llrawproc_runtime_state_t runtime_state = llrawproc_capture_worker_runtime_state(worker);
        const int runtime_state_changed =
            !llrawproc_runtime_state_equal(&runtime_state,
                                           &worker->seeded_runtime_state,
                                           publish_auto_correction);
        if (runtime_state_changed)
        {
            const double publish_lock_start = mlv_stage_timing_now();
            pthread_mutex_lock(&video->llrawproc_mutex);
            llrawproc_publish_worker_results(video, &runtime_state, publish_auto_correction);
            pthread_mutex_unlock(&video->llrawproc_mutex);
            g_llrawproc_last_publish_lock_ms += (mlv_stage_timing_now() - publish_lock_start) * 1000.0;
            g_llrawproc_last_shared_lock_ms += g_llrawproc_last_publish_lock_ms;
        }
    }

    /* deflicker RAW data by changing 'tcBaselineExposure' tag in the exported DNG */
    /*
    if (video->llrawproc->deflicker_target)
    {
#ifndef STDOUT_SILENT
        printf("Per-frame exposure compensation: 'ON'\nDeflicker target: '%d'\n\n", video->llrawproc->deflicker_target);
#endif
        deflicker(video, raw_image_buff, raw_image_size);
    }
    */

#ifndef STDOUT_SILENT
    printf("raw_image_buff[1000] = %u, Proc_Black = %d, Proc_White = %d, Raw_Black = %d, Raw_White = %d <= THE END OF LLRAWPROC\n", raw_image_buff[1000], video->processing->black_level, video->processing->white_level, video->RAWI.raw_info.black_level, video->RAWI.raw_info.white_level);
#endif

    g_llrawproc_last_dark_frame_ms = dark_frame_ms;
    g_llrawproc_last_vertical_stripes_ms = vertical_stripes_ms;
    g_llrawproc_last_focus_pixels_ms = focus_pixels_ms;
    g_llrawproc_last_bad_pixels_ms = bad_pixels_ms;
    g_llrawproc_last_pattern_noise_ms = pattern_noise_ms;
    g_llrawproc_last_dual_iso_ms = dual_iso_ms;
    g_llrawproc_last_chroma_smooth_ms = chroma_smooth_ms;
    g_llrawproc_last_total_ms = (mlv_stage_timing_now() - apply_start) * 1000.0;

    if (using_stack_worker)
    {
        llrawproc_free_worker_state(worker);
    }
}

/* Phase 4B-v2: scaled-buffer entry point. Runs a SUBSET of the llrawproc
 * pipeline on a buffer whose dimensions differ from
 * video->RAWI.xRes/yRes. The subset includes only the size-agnostic
 * stages (HQ Dual ISO recon, dark frame subtraction, chroma smooth, 14-bit
 * conversion). Pre-downsample stages (focus pixel, bad pixel, vertical
 * stripes, pattern noise) are NOT applied here — the caller must apply
 * them at full res before downsampling, OR ensure they are disabled in
 * the receipt.
 *
 * Returns 1 if the scaled application is safe (caller can proceed), 0 if
 * a feature in the receipt is incompatible with the scaled path (caller
 * must fall back to the v1 full-res path).
 *
 * Threading: this function is callable from playback worker threads. It
 * shares the per-clip worker state with applyLLRawProcObject (acquires the
 * worker via llrawproc_acquire_worker_state). The shared->diso_pattern
 * field is read but not written from the scaled path — so the iso pattern
 * detection MUST have been seeded by a prior full-res render. */
int applyLLRawProcObject_with_dims(mlvObject_t * video,
                                   uint16_t * raw_image_buff,
                                   size_t raw_image_size,
                                   int override_w,
                                   int override_h)
{
    const double apply_start = mlv_stage_timing_now();
    llrawprocObject_t * shared = video ? video->llrawproc : NULL;
    llrawprocWorkerState_t stack_worker;
    llrawprocWorkerState_t * worker = NULL;
    int using_stack_worker = 0;

    g_llrawproc_last_shared_lock_ms = 0.0;
    g_llrawproc_last_dualiso_refine_lock_ms = 0.0;
    g_llrawproc_last_publish_lock_ms = 0.0;
    g_llrawproc_last_total_ms = 0.0;
    g_llrawproc_last_dark_frame_ms = 0.0;
    g_llrawproc_last_vertical_stripes_ms = 0.0;
    g_llrawproc_last_focus_pixels_ms = 0.0;
    g_llrawproc_last_bad_pixels_ms = 0.0;
    g_llrawproc_last_pattern_noise_ms = 0.0;
    g_llrawproc_last_dual_iso_ms = 0.0;
    g_llrawproc_last_chroma_smooth_ms = 0.0;
    g_llrawproc_last_preview_histogram_ms = 0.0;
    g_llrawproc_last_preview_regression_ms = 0.0;
    g_llrawproc_last_preview_rowscale_ms = 0.0;

    if (!video || !shared || !shared->fix_raw)
    {
        g_llrawproc_last_total_ms = (mlv_stage_timing_now() - apply_start) * 1000.0;
        return 1; /* nothing to do — no recon, no fix */
    }
    if (override_w <= 0 || override_h <= 0) return 0;
    if ((size_t)override_w * (size_t)override_h * sizeof(uint16_t) != raw_image_size) return 0;

    /* Bail if the receipt enables features that are unsafe at scaled
     * resolution. The caller must fall back to the v1 path. */
    if (shared->focus_pixels) return 0;
    if (shared->bad_pixels) return 0;
    if (shared->vertical_stripes) return 0;
    if (shared->pattern_noise) return 0;

    memset(&stack_worker, 0, sizeof(stack_worker));
    stack_worker.prev_black_level = -1;
    worker = llrawproc_acquire_worker_state(video);
    if (!worker)
    {
        worker = &stack_worker;
        using_stack_worker = 1;
    }

    /* Build a local raw_info with the override dimensions. The dual ISO
     * recon reads raw_info.width/height/pitch + active_area. */
    struct raw_info raw_info = video->RAWI.raw_info;
    const int original_bits_per_pixel = video->RAWI.raw_info.bits_per_pixel;

    raw_info.width = override_w;
    raw_info.height = override_h;
    raw_info.pitch = override_w * (raw_info.bits_per_pixel <= 16 ? 2 : 4);
    raw_info.frame_size = (uint32_t)(override_w * override_h * 14 / 8);
    raw_info.active_area.x1 = 0;
    raw_info.active_area.y1 = 0;
    raw_info.active_area.x2 = override_w;
    raw_info.active_area.y2 = override_h;

    int diso_validity = 0;
    int dual_iso_mode = 0;
    int diso1 = 0;
    int diso2 = 0;
    int diso_averaging = 0;
    int diso_alias_map = 0;
    int diso_frblending = 0;
    int chroma_smooth_mode = 0;
    int dark_frame_mode = 0;
    int worker_diso_pattern = 0;
    int worker_diso_auto_correction = 0;
    double worker_diso_ev_correction = 0.0;
    int worker_diso_black_delta = 0;
    int apply_dark_frame_outside_lock = 0;
    const uint16_t * dark_frame_data_for_subtraction = NULL;
    uint32_t dark_frame_size_for_subtraction = 0;
    uint32_t dark_frame_black_level = 0;

    if (original_bits_per_pixel < 14)
    {
        make_14bit(raw_image_buff, raw_image_size, &raw_info);
    }

    llrawproc_worker_reset_dng_bw_levels(worker, &raw_info);
    llrawproc_worker_ensure_luts(worker, raw_info.black_level);

    double shared_lock_start = mlv_stage_timing_now();
    pthread_mutex_lock(&video->llrawproc_mutex);
    if (!shared->fix_raw)
    {
        pthread_mutex_unlock(&video->llrawproc_mutex);
        g_llrawproc_last_shared_lock_ms = (mlv_stage_timing_now() - shared_lock_start) * 1000.0;
        g_llrawproc_last_total_ms = (mlv_stage_timing_now() - apply_start) * 1000.0;
        if (using_stack_worker) llrawproc_free_worker_state(worker);
        return 1;
    }

    /* Dark frame: only safe if its dimensions match the override. */
    if (!df_init(video))
    {
        if (llrawproc_worker_sync_dark_frame_copy(worker, shared)
         && worker->dark_frame_data_copy
         && worker->dark_frame_size == raw_image_size)
        {
            apply_dark_frame_outside_lock = 1;
            dark_frame_data_for_subtraction = worker->dark_frame_data_copy;
            dark_frame_size_for_subtraction = worker->dark_frame_size;
            dark_frame_black_level = worker->dark_frame_hdr_copy.black_level;
        }
        /* If the dark frame size doesn't match the scaled buffer, we
         * can't apply it at scale — bail and let the caller fall back. */
        else if (worker->dark_frame_size != 0)
        {
            pthread_mutex_unlock(&video->llrawproc_mutex);
            g_llrawproc_last_shared_lock_ms = (mlv_stage_timing_now() - shared_lock_start) * 1000.0;
            g_llrawproc_last_total_ms = (mlv_stage_timing_now() - apply_start) * 1000.0;
            if (using_stack_worker) llrawproc_free_worker_state(worker);
            return 0;
        }
    }

    diso_validity = shared->diso_validity;
    dual_iso_mode = shared->dual_iso;
    diso1 = shared->diso1;
    diso2 = shared->diso2;
    diso_averaging = shared->diso_averaging;
    if (shared->diso_playback_force_mean23 != 0
        && !dualiso_playback_mean23_override_disabled_via_env())
    {
        diso_averaging = 1; /* DISOI_MEAN23 */
    }
    diso_alias_map = shared->diso_alias_map;
    diso_frblending = shared->diso_frblending;
    worker_diso_pattern = shared->diso_pattern;
    worker_diso_auto_correction = shared->diso_auto_correction;
    worker_diso_ev_correction = shared->diso_ev_correction;
    worker_diso_black_delta = shared->diso_black_delta;
    worker->seeded_runtime_state = llrawproc_capture_shared_runtime_state(shared);
    dark_frame_mode = shared->dark_frame;
    chroma_smooth_mode = shared->chroma_smooth;

    pthread_mutex_unlock(&video->llrawproc_mutex);
    g_llrawproc_last_shared_lock_ms += (mlv_stage_timing_now() - shared_lock_start) * 1000.0;

    worker->diso_pattern = worker_diso_pattern;
    worker->diso_auto_correction = worker_diso_auto_correction;
    worker->diso_ev_correction = worker_diso_ev_correction;
    worker->diso_black_delta = worker_diso_black_delta;

    if (apply_dark_frame_outside_lock)
    {
        const double dark_frame_start = mlv_stage_timing_now();
        df_subtract_snapshot(dark_frame_data_for_subtraction,
                             dark_frame_size_for_subtraction,
                             dark_frame_black_level,
                             raw_info.bits_per_pixel,
                             raw_image_buff,
                             raw_image_size);
        g_llrawproc_last_dark_frame_ms = (mlv_stage_timing_now() - dark_frame_start) * 1000.0;
    }

    int publish_auto_correction = 1;
    double dual_iso_ms = 0.0;
    double chroma_smooth_ms = 0.0;

    if (diso_validity && dual_iso_mode == 1)
    {
        int restricted_lossless = (video->MLVI.videoClass & MLV_VIDEO_CLASS_FLAG_LJ92) && raw_info.white_level < 15000;
        if (restricted_lossless)
        {
            const double dual_iso_start = mlv_stage_timing_now();
            int low_iso = MIN(diso1, diso2);
            int high_iso = MAX(diso1, diso2);
            scale_restricted_range(&raw_info, raw_image_buff, low_iso, high_iso);
            llrawproc_worker_reset_dng_bw_levels(worker, &raw_info);
            dual_iso_ms += (mlv_stage_timing_now() - dual_iso_start) * 1000.0;
        }

        const double dual_iso_start = mlv_stage_timing_now();
        int explicit_auto_correction = 0;
        double explicit_ev_correction = worker->diso_ev_correction;
        int explicit_black_delta = worker->diso_black_delta;
        const int has_explicit_auto_match =
            (worker->diso_auto_correction < 0) &&
            (worker->diso_ev_correction != 1) &&
            (worker->diso_black_delta != -1);

        int * auto_correction_ptr = has_explicit_auto_match
            ? &explicit_auto_correction
            : &worker->diso_auto_correction;
        double * ev_correction_ptr = has_explicit_auto_match
            ? &explicit_ev_correction
            : &worker->diso_ev_correction;
        int * black_delta_ptr = has_explicit_auto_match
            ? &explicit_black_delta
            : &worker->diso_black_delta;

        publish_auto_correction = !has_explicit_auto_match;

        diso_get_full20bit(raw_info,
                           raw_image_buff,
                           dark_frame_mode,
                           diso1,
                           diso2,
                           &worker->diso_pattern,
                           auto_correction_ptr,
                           ev_correction_ptr,
                           black_delta_ptr,
                           diso_averaging,
                           diso_alias_map,
                           diso_frblending,
                           chroma_smooth_mode,
                           video->cpu_cores,
                           &worker->diso_full20bit_scratch);
        dual_iso_ms += (mlv_stage_timing_now() - dual_iso_start) * 1000.0;

        if (has_explicit_auto_match)
        {
            worker->diso_ev_correction = explicit_ev_correction;
            worker->diso_black_delta = explicit_black_delta;
        }

        {
            int bits_shift = 16 - raw_info.bits_per_pixel;
            worker->dng_black_level = raw_info.black_level << bits_shift;
            worker->dng_white_level = raw_info.white_level << bits_shift;
            worker->dng_bit_depth = 16;
        }

        llrawproc_worker_ensure_luts(worker, raw_info.black_level);
    }

    if (chroma_smooth_mode && dual_iso_mode != 1)
    {
        const double chroma_smooth_start = mlv_stage_timing_now();
        chroma_smooth(chroma_smooth_mode,
                      raw_image_buff,
                      override_w,
                      override_h,
                      raw_info.black_level,
                      raw_info.white_level,
                      worker->raw2ev,
                      worker->ev2raw,
                      &worker->chroma_smooth_scratch);
        chroma_smooth_ms += (mlv_stage_timing_now() - chroma_smooth_start) * 1000.0;
    }

    if (original_bits_per_pixel < 14 && dual_iso_mode != 1)
    {
        undo_14bit(raw_image_buff, raw_image_size, video->RAWI.raw_info.bits_per_pixel);
    }

    {
        const llrawproc_runtime_state_t runtime_state = llrawproc_capture_worker_runtime_state(worker);
        const int runtime_state_changed =
            !llrawproc_runtime_state_equal(&runtime_state,
                                           &worker->seeded_runtime_state,
                                           publish_auto_correction);
        if (runtime_state_changed)
        {
            const double publish_lock_start = mlv_stage_timing_now();
            pthread_mutex_lock(&video->llrawproc_mutex);
            llrawproc_publish_worker_results(video, &runtime_state, publish_auto_correction);
            pthread_mutex_unlock(&video->llrawproc_mutex);
            g_llrawproc_last_publish_lock_ms += (mlv_stage_timing_now() - publish_lock_start) * 1000.0;
            g_llrawproc_last_shared_lock_ms += g_llrawproc_last_publish_lock_ms;
        }
    }

    g_llrawproc_last_dual_iso_ms = dual_iso_ms;
    g_llrawproc_last_chroma_smooth_ms = chroma_smooth_ms;
    g_llrawproc_last_total_ms = (mlv_stage_timing_now() - apply_start) * 1000.0;

    if (using_stack_worker)
    {
        llrawproc_free_worker_state(worker);
    }
    return 1;
}

double llrpGetLastSharedLockMilliseconds(void)
{
    return g_llrawproc_last_shared_lock_ms;
}

double llrpGetLastDualIsoRefineLockMilliseconds(void)
{
    return g_llrawproc_last_dualiso_refine_lock_ms;
}

double llrpGetLastPublishLockMilliseconds(void)
{
    return g_llrawproc_last_publish_lock_ms;
}

double llrpGetLastTotalMilliseconds(void)
{
    return g_llrawproc_last_total_ms;
}

double llrpGetLastDarkFrameMilliseconds(void)
{
    return g_llrawproc_last_dark_frame_ms;
}

double llrpGetLastVerticalStripesMilliseconds(void)
{
    return g_llrawproc_last_vertical_stripes_ms;
}

double llrpGetLastFocusPixelsMilliseconds(void)
{
    return g_llrawproc_last_focus_pixels_ms;
}

double llrpGetLastBadPixelsMilliseconds(void)
{
    return g_llrawproc_last_bad_pixels_ms;
}

double llrpGetLastPatternNoiseMilliseconds(void)
{
    return g_llrawproc_last_pattern_noise_ms;
}

double llrpGetLastDualIsoMilliseconds(void)
{
    return g_llrawproc_last_dual_iso_ms;
}

double llrpGetLastChromaSmoothMilliseconds(void)
{
    return g_llrawproc_last_chroma_smooth_ms;
}

double llrpGetLastDualIsoPreviewHistogramMilliseconds(void)
{
    return g_llrawproc_last_preview_histogram_ms;
}

double llrpGetLastDualIsoPreviewRegressionMilliseconds(void)
{
    return g_llrawproc_last_preview_regression_ms;
}

double llrpGetLastDualIsoPreviewRowscaleMilliseconds(void)
{
    return g_llrawproc_last_preview_rowscale_ms;
}

void llrpResetDebugPixelMapCopyCount(void)
{
    g_llrawproc_debug_pixel_map_copy_count = 0;
}

uint64_t llrpGetDebugPixelMapCopyCount(void)
{
    return g_llrawproc_debug_pixel_map_copy_count;
}

void llrpResetDebugDarkFrameCopyCount(void)
{
    g_llrawproc_debug_dark_frame_copy_count = 0;
}

uint64_t llrpGetDebugDarkFrameCopyCount(void)
{
    return g_llrawproc_debug_dark_frame_copy_count;
}

void llrpResetDebugRuntimePublishCount(void)
{
    g_llrawproc_debug_runtime_publish_count = 0;
}

uint64_t llrpGetDebugRuntimePublishCount(void)
{
    return g_llrawproc_debug_runtime_publish_count;
}

/* Detect focus dot fix mode according to RAWC block info (binning + skipping) and camera ID
   Return value 0 = off, 1 = On, 2 = CropRec */
int llrpDetectFocusDotFixMode(mlvObject_t * video)
{
    switch(video->IDNT.cameraModel)
    {
        case 0x80000331: // EOSM
        case 0x80000355: // EOSM2
        case 0x80000346: // 100D
        case 0x80000301: // 650D
        case 0x80000326: // 700D
            if(video->RAWC.blockType[0])
            {
                int sampling_x = video->RAWC.binning_x + video->RAWC.skipping_x;
                int sampling_y = video->RAWC.binning_y + video->RAWC.skipping_y;
                if( (video->RAWI.raw_info.height < 900) && !(sampling_y == 5 && sampling_x == 3) )
                {
                    return 2;
                }
            }
            return 1;

        default: // All other cameras
            return 0;
    }
}

/* LLRawProcObject variable handling */
int llrpGetFixRawMode(mlvObject_t * video)
{
    return video->llrawproc->fix_raw;
}

void llrpSetFixRawMode(mlvObject_t * video, int value)
{
    video->llrawproc->fix_raw = value;
}

int llrpGetVerticalStripeMode(mlvObject_t * video)
{
    return video->llrawproc->vertical_stripes;
}

void llrpSetVerticalStripeMode(mlvObject_t * video, int value)
{
    video->llrawproc->vertical_stripes = value;
}

void llrpComputeStripesOn(mlvObject_t * video)
{
    pthread_mutex_lock(&video->llrawproc_mutex);
    video->llrawproc->compute_stripes = 1;
    pthread_mutex_unlock(&video->llrawproc_mutex);
}

int llrpGetFocusPixelMode(mlvObject_t * video)
{
    return video->llrawproc->focus_pixels;
}

void llrpSetFocusPixelMode(mlvObject_t * video, int value)
{
    video->llrawproc->focus_pixels = value;
}

int llrpGetFocusPixelInterpolationMethod(mlvObject_t * video)
{
    return video->llrawproc->fpi_method;
}

void llrpSetFocusPixelInterpolationMethod(mlvObject_t * video, int value)
{
    video->llrawproc->fpi_method = value;
}

int llrpGetBadPixelMode(mlvObject_t * video)
{
    return video->llrawproc->bad_pixels;
}

void llrpSetBadPixelMode(mlvObject_t * video, int value)
{
    video->llrawproc->bad_pixels = value;
}

int llrpGetBadPixelSearchMethod(mlvObject_t *video)
{
    return video->llrawproc->bps_method;
}

void llrpSetBadPixelSearchMethod(mlvObject_t * video, int value)
{
    video->llrawproc->bps_method = value;
}

int llrpGetBadPixelInterpolationMethod(mlvObject_t * video)
{
    return video->llrawproc->bpi_method;
}

void llrpSetBadPixelInterpolationMethod(mlvObject_t * video, int value)
{
    video->llrawproc->bpi_method = value;
}

int llrpGetChromaSmoothMode(mlvObject_t * video)
{
    return video->llrawproc->chroma_smooth;
}

void llrpSetChromaSmoothMode(mlvObject_t * video, int value)
{
    video->llrawproc->chroma_smooth = value;
}

int llrpGetPatternNoiseMode(mlvObject_t * video)
{
    return video->llrawproc->pattern_noise;
}

void llrpSetPatternNoiseMode(mlvObject_t * video, int value)
{
    video->llrawproc->pattern_noise = value;
}

int llrpGetDeflickerTarget(mlvObject_t * video)
{
    return video->llrawproc->deflicker_target;
}

void llrpSetDeflickerTarget(mlvObject_t * video, int value)
{
    video->llrawproc->deflicker_target = value;
}

int llrpGetDualIsoMode(mlvObject_t * video)
{
    return video->llrawproc->dual_iso;
}

void llrpSetDualIsoMode(mlvObject_t * video, int value)
{
    video->llrawproc->dual_iso = value;
}

int llrpGetDualIsoInterpolationMethod(mlvObject_t * video)
{
    return video->llrawproc->diso_averaging;
}

void llrpSetDualIsoInterpolationMethod(mlvObject_t * video, int value)
{
    video->llrawproc->diso_averaging = value;
}

int llrpGetDualIsoPlaybackForceMean23(mlvObject_t * video)
{
    return video->llrawproc->diso_playback_force_mean23;
}

void llrpSetDualIsoPlaybackForceMean23(mlvObject_t * video, int value)
{
    video->llrawproc->diso_playback_force_mean23 = value ? 1 : 0;
}

int llrpGetDualIsoAliasMapMode(mlvObject_t * video)
{
    return video->llrawproc->diso_alias_map;
}

void llrpSetDualIsoAliasMapMode(mlvObject_t * video, int value)
{
    video->llrawproc->diso_alias_map = value;
}

int llrpGetDualIsoFullResBlendingMode(mlvObject_t * video)
{
    return video->llrawproc->diso_frblending;
}

void llrpSetDualIsoFullResBlendingMode(mlvObject_t * video, int value)
{
    video->llrawproc->diso_frblending = value;
}

int llrpGetDualIsoValidity(mlvObject_t * video)
{
    return video->llrawproc->diso_validity;
}

void llrpSetDualIsoValidity(mlvObject_t * video, int diso_force)
{
    int iso1 = (int)video->EXPO.isoValue;

    if (iso1 < 100)
    {
        iso1 = 100;
    }

    if (diso_force)
    {
        video->llrawproc->diso_validity = DISO_FORCED;

        video->llrawproc->diso1 = iso1;
        video->llrawproc->diso2 = iso1;
    }
    else if (video->DISO.blockType[0] && video->DISO.dualMode)
    {
        video->llrawproc->diso_validity = DISO_VALID;

        int iso2 = (int)video->DISO.isoValue;

        if (iso2 < 0)
        {
            if (iso2 < -6)
            {
                iso2 = iso1 / pow(2, ABS(iso2) - 6);
            }
            else
            {
                iso2 = iso1 * pow(2, ABS(7 + iso2));
            }

            iso2 = COERCE(iso2, 100, 3200);
        }
        else if ((iso2 >= 0) && (iso2 < 100))
        {
            iso2 = iso1 * pow(2, iso2) / (iso1 / 100);
        }

        video->llrawproc->diso1 = iso1;
        video->llrawproc->diso2 = iso2;
    }
    else
    {
        video->llrawproc->diso_validity = DISO_INVALID;
    }
}

int llrpHQDualIso(mlvObject_t * video)
{
    return (video->llrawproc->dual_iso == 1) && video->llrawproc->diso_validity && (llrpGetFixRawMode(video));
}

void llrpResetDngBWLevels(mlvObject_t * video)
{
    video->llrawproc->dng_bit_depth = video->RAWI.raw_info.bits_per_pixel;
    video->llrawproc->dng_black_level = video->RAWI.raw_info.black_level;
    video->llrawproc->dng_white_level = video->RAWI.raw_info.white_level;
}

void llrpResetFpmStatus(mlvObject_t * video)
{
    pthread_mutex_lock(&video->llrawproc_mutex);
    reset_fpm_status(&(video->llrawproc->focus_pixel_map), &(video->llrawproc->fpm_status));
    llrawproc_bump_focus_map_version(video->llrawproc);
    pthread_mutex_unlock(&video->llrawproc_mutex);
}

void llrpResetBpmStatus(mlvObject_t * video)
{
    pthread_mutex_lock(&video->llrawproc_mutex);
    reset_bpm_status(&(video->llrawproc->bad_pixel_map), &(video->llrawproc->bpm_status));
    llrawproc_bump_bad_map_version(video->llrawproc);
    pthread_mutex_unlock(&video->llrawproc_mutex);
}

/* dark frame stuff */
void llrpInitDarkFrameExtFileName(mlvObject_t * video, char * df_filename)
{
    pthread_mutex_lock(&video->llrawproc_mutex);
    const int changed = !video->llrawproc->dark_frame_filename
                     || strcmp(video->llrawproc->dark_frame_filename, df_filename) != 0;
    df_free_filename(video);
    df_init_filename(video, df_filename);
    if (changed) df_free(video);
    pthread_mutex_unlock(&video->llrawproc_mutex);
}

void llrpFreeDarkFrameExtFileName(mlvObject_t * video)
{
    pthread_mutex_lock(&video->llrawproc_mutex);
    const int changed = video->llrawproc->dark_frame_filename != NULL;
    df_free_filename(video);
    if (changed) df_free(video);
    pthread_mutex_unlock(&video->llrawproc_mutex);
}

int llrpGetDarkFrameMode(mlvObject_t * video)
{
    return video->llrawproc->dark_frame;
}

void llrpSetDarkFrameMode(mlvObject_t * video, int value)
{
    pthread_mutex_lock(&video->llrawproc_mutex);
    if (video->llrawproc->dark_frame != value)
    {
        video->llrawproc->dark_frame = value;
        df_free(video);
    }
    else
    {
        video->llrawproc->dark_frame = value;
    }
    pthread_mutex_unlock(&video->llrawproc_mutex);
}

int llrpGetDarkFrameExtStatus(mlvObject_t * video)
{
    if(video->llrawproc->dark_frame_filename) return 1;
    return 0;
}

int llrpGetDarkFrameIntStatus(mlvObject_t * video)
{
    if(video->DARK.blockType[0]) return 1;
    return 0;
}

int llrpValidateExtDarkFrame(mlvObject_t * video, char * df_filename, char * error_message)
{
    return df_validate(video, df_filename, error_message);
}
