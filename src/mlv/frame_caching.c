/* Yeas, we have another background thread */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <time.h>
#if defined(_OPENMP)
#include <omp.h>
#endif
#if defined(__WIN32)
#include <windows.h>
#endif

#include "video_mlv.h"
#include "../debayer/debayer.h"
#include "../ca_correct/CA_correct_RT.h"
#include "../debayer/wb_conversion.h"
#include "pipeline_stage_capture.h"

#include "librtprocesswrapper.h"

#define MIN(X, Y) (((X) < (Y)) ? (X) : (Y))
#define MAX(X, Y) (((X) > (Y)) ? (X) : (Y))
#define LIMIT16(X) MAX(MIN(X, 65535), 0)

#if defined(_MSC_VER)
#define MLV_DEBAYER_THREAD_LOCAL __declspec(thread)
#else
#define MLV_DEBAYER_THREAD_LOCAL __thread
#endif

static MLV_DEBAYER_THREAD_LOCAL double g_mlv_last_debayer_wb_prepare_ms = 0.0;
static MLV_DEBAYER_THREAD_LOCAL double g_mlv_last_debayer_ca_ms = 0.0;
static MLV_DEBAYER_THREAD_LOCAL double g_mlv_last_debayer_kernel_ms = 0.0;
static MLV_DEBAYER_THREAD_LOCAL double g_mlv_last_debayer_wb_undo_ms = 0.0;

static double mlv_debayer_timing_now_seconds(void)
{
#if defined(_OPENMP)
    return omp_get_wtime();
#elif defined(__WIN32)
    LARGE_INTEGER frequency;
    LARGE_INTEGER counter;
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&counter);
    return (double)counter.QuadPart / (double)frequency.QuadPart;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + ((double)ts.tv_nsec / 1000000000.0);
#endif
}

#ifndef STDOUT_SILENT
#define DEBUG(CODE) CODE
#else
#define DEBUG(CODE)
#endif

void resetMlvLastDebayerStageMilliseconds(void)
{
    g_mlv_last_debayer_wb_prepare_ms = 0.0;
    g_mlv_last_debayer_ca_ms = 0.0;
    g_mlv_last_debayer_kernel_ms = 0.0;
    g_mlv_last_debayer_wb_undo_ms = 0.0;
}

double getMlvLastDebayerWbPrepareMilliseconds(void)
{
    return g_mlv_last_debayer_wb_prepare_ms;
}

double getMlvLastDebayerCaMilliseconds(void)
{
    return g_mlv_last_debayer_ca_ms;
}

double getMlvLastDebayerKernelMilliseconds(void)
{
    return g_mlv_last_debayer_kernel_ms;
}

double getMlvLastDebayerWbUndoMilliseconds(void)
{
    return g_mlv_last_debayer_wb_undo_ms;
}

void invalidateMlvProcessedPreviewCache(mlvObject_t * video)
{
    if (!video)
    {
        return;
    }

    video->current_processed_frame_active = 0;
    video->current_processed_frame = 0;
    video->current_processed_frame_threads = 0;
    video->current_processed_frame_signature = 0;
    video->processed_16bit_cache_next_slot = 0;
    for (uint32_t slot = 0; slot < MLV_PROCESSED_16BIT_CACHE_SLOTS; ++slot)
    {
        video->processed_16bit_cache_active[slot] = 0;
        video->processed_16bit_cache_frame[slot] = 0;
        video->processed_16bit_cache_threads[slot] = 0;
        video->processed_16bit_cache_signature[slot] = 0;
    }
    mlvInvalidateProcessed8PrefetchCache(video);
}

static uint64_t mlv_cache_max_start(mlvObject_t * video)
{
    if (getMlvFrames(video) <= getMlvRawCacheLimitFrames(video)) return 0;
    return getMlvFrames(video) - getMlvRawCacheLimitFrames(video);
}

static uint64_t mlv_cache_clamp_start(mlvObject_t * video, uint64_t startFrame)
{
    return MIN(startFrame, mlv_cache_max_start(video));
}

static uint64_t mlv_cache_window_end(mlvObject_t * video)
{
    return MIN(getMlvFrames(video), mlv_cache_clamp_start(video, video->cache_start_frame) + getMlvRawCacheLimitFrames(video));
}

static uint64_t mlv_cache_window_end_for_start(mlvObject_t * video, uint64_t startFrame)
{
    return MIN(getMlvFrames(video), mlv_cache_clamp_start(video, startFrame) + getMlvRawCacheLimitFrames(video));
}

static uint64_t mlv_cache_desired_start_for_frame(mlvObject_t * video, uint64_t frameIndex)
{
    uint64_t frame_limit = getMlvRawCacheLimitFrames(video);
    uint64_t start = mlv_cache_clamp_start(video, video->cache_start_frame);
    uint64_t end = mlv_cache_window_end(video);

    if (frame_limit == 0 || getMlvFrames(video) <= frame_limit)
    {
        return 0;
    }

    if (frameIndex < start)
    {
        return mlv_cache_clamp_start(video, frameIndex);
    }

    if (frameIndex >= end)
    {
        if (frameIndex + 1 <= frame_limit)
        {
            return 0;
        }
        return mlv_cache_clamp_start(video, frameIndex + 1 - frame_limit);
    }

    return start;
}

static void mlv_cache_restore_linear_slots(mlvObject_t * video)
{
    uint64_t frame_pix = (uint64_t)getMlvWidth(video) * getMlvHeight(video) * 3;
    uint64_t frame_limit = getMlvRawCacheLimitFrames(video);

    if (!video->rgb_raw_frames || !video->cache_memory_block)
    {
        return;
    }

    for (uint64_t i = 0; i < frame_limit; ++i)
    {
        video->rgb_raw_frames[i] = video->cache_memory_block + (frame_pix * i);
    }
}

static int mlv_cache_preserve_overlap_locked(mlvObject_t * video, uint64_t old_start, uint64_t new_start)
{
    uint64_t frame_limit = getMlvRawCacheLimitFrames(video);
    uint64_t old_end = mlv_cache_window_end_for_start(video, old_start);
    uint64_t new_end = mlv_cache_window_end_for_start(video, new_start);
    uint64_t overlap_start = MAX(old_start, new_start);
    uint64_t overlap_end = MIN(old_end, new_end);
    uint16_t ** remapped_slots = NULL;
    uint8_t * consumed_slots = NULL;
    uint64_t next_free_slot = 0;

    if (!video->rgb_raw_frames || !video->cache_memory_block || frame_limit == 0)
    {
        return 0;
    }

    remapped_slots = (uint16_t **)calloc((size_t)frame_limit, sizeof(uint16_t *));
    consumed_slots = (uint8_t *)calloc((size_t)frame_limit, sizeof(uint8_t));
    if (!remapped_slots || !consumed_slots)
    {
        free(remapped_slots);
        free(consumed_slots);
        return 0;
    }

    for (uint64_t frame = overlap_start; frame < overlap_end; ++frame)
    {
        uint64_t old_slot = frame - old_start;
        uint64_t new_slot = frame - new_start;
        remapped_slots[new_slot] = video->rgb_raw_frames[old_slot];
        consumed_slots[old_slot] = 1;
    }

    for (uint64_t slot = 0; slot < frame_limit; ++slot)
    {
        if (remapped_slots[slot]) continue;

        while (next_free_slot < frame_limit && consumed_slots[next_free_slot])
        {
            ++next_free_slot;
        }

        if (next_free_slot >= frame_limit)
        {
            free(remapped_slots);
            free(consumed_slots);
            return 0;
        }

        remapped_slots[slot] = video->rgb_raw_frames[next_free_slot];
        consumed_slots[next_free_slot] = 1;
    }

    memcpy(video->rgb_raw_frames, remapped_slots, (size_t)frame_limit * sizeof(uint16_t *));

    free(remapped_slots);
    free(consumed_slots);
    return 1;
}

int mlv_frame_in_cache_window(mlvObject_t * video, uint64_t frameIndex)
{
    if (!isMlvActive(video) || getMlvRawCacheLimitFrames(video) == 0) return 0;
    uint64_t start = mlv_cache_clamp_start(video, video->cache_start_frame);
    uint64_t end = MIN(getMlvFrames(video), start + getMlvRawCacheLimitFrames(video));
    return frameIndex >= start && frameIndex < end;
}

uint64_t mlv_cache_slot_for_frame(mlvObject_t * video, uint64_t frameIndex)
{
    return frameIndex - mlv_cache_clamp_start(video, video->cache_start_frame);
}

void mlv_cache_ensure_window(mlvObject_t * video, uint64_t frameIndex)
{
    if (!isMlvActive(video) || video->stop_caching || getMlvRawCacheLimitFrames(video) == 0) return;

    pthread_mutex_lock( &video->g_mutexFind );
    int in_window = mlv_frame_in_cache_window(video, frameIndex);
    uint64_t desired_start = mlv_cache_desired_start_for_frame(video, frameIndex);
    pthread_mutex_unlock( &video->g_mutexFind );
    if (in_window) return;

    video->stop_caching = 1;
    while (video->cache_thread_count) usleep(100);

    pthread_mutex_lock( &video->g_mutexFind );
    uint64_t old_start = mlv_cache_clamp_start(video, video->cache_start_frame);
    uint64_t old_end = mlv_cache_window_end_for_start(video, old_start);
    int overlap_preserved = mlv_cache_preserve_overlap_locked(video, old_start, desired_start);
    video->cache_start_frame = desired_start;
    video->cache_next = 0;
    video->cache_generation++;

    if (!overlap_preserved)
    {
        mlv_cache_restore_linear_slots(video);
    }

    for (uint64_t i = 0; i < getMlvFrames(video); ++i)
    {
        if (!overlap_preserved
            || !mlv_frame_in_cache_window(video, i)
            || i < old_start
            || i >= old_end)
        {
            video->cached_frames[i] = MLV_FRAME_NOT_CACHED;
        }
    }
    video->current_cached_frame_active = 0;
    pthread_mutex_unlock( &video->g_mutexFind );

    video->stop_caching = 0;
    for (int i = 0; i < video->cpu_cores; ++i)
    {
        add_mlv_cache_thread(video);
    }
}

void mlv_cache_request_playback_preroll(mlvObject_t * video,
                                        uint64_t currentFrame,
                                        uint64_t lastFrameInclusive,
                                        uint64_t lookaheadFrames)
{
    if (!isMlvActive(video)
        || video->stop_caching
        || getMlvRawCacheLimitFrames(video) == 0
        || getMlvFrames(video) == 0)
    {
        return;
    }

    const uint64_t maxFrame = getMlvFrames(video) - 1;
    if (currentFrame > maxFrame) currentFrame = maxFrame;
    if (lastFrameInclusive > maxFrame) lastFrameInclusive = maxFrame;
    if (lastFrameInclusive < currentFrame) lastFrameInclusive = currentFrame;

    uint64_t targetFrame = currentFrame;
    if (lookaheadFrames > 0 && currentFrame < lastFrameInclusive)
    {
        const uint64_t remaining = lastFrameInclusive - currentFrame;
        targetFrame = currentFrame + MIN(lookaheadFrames, remaining);
    }

    mlv_cache_ensure_window(video, targetFrame);

    uint64_t requestFrame = 0;
    int haveRequest = 0;

    pthread_mutex_lock(&video->g_mutexFind);
    uint64_t requestStart = currentFrame;
    if (requestStart < lastFrameInclusive)
    {
        requestStart++;
    }

    for (uint64_t frame = requestStart; frame <= targetFrame; ++frame)
    {
        if (mlv_frame_in_cache_window(video, frame)
            && video->cached_frames[frame] == MLV_FRAME_NOT_CACHED)
        {
            requestFrame = frame;
            haveRequest = 1;
            break;
        }
    }

    if (!haveRequest
        && currentFrame > 0
        && currentFrame <= lastFrameInclusive
        && mlv_frame_in_cache_window(video, currentFrame)
        && video->cached_frames[currentFrame] == MLV_FRAME_NOT_CACHED)
    {
        requestFrame = currentFrame;
        haveRequest = 1;
    }

    if (haveRequest)
    {
        video->cache_next = requestFrame;
    }
    pthread_mutex_unlock(&video->g_mutexFind);

    int shouldWakeWorkers = 0;
    if (haveRequest && !video->stop_caching && video->cpu_cores > 0)
    {
        pthread_mutex_lock(&video->g_mutexCount);
        shouldWakeWorkers = (video->cache_thread_count == 0);
        pthread_mutex_unlock(&video->g_mutexCount);
    }

    if (shouldWakeWorkers)
    {
        for (int i = 0; i < video->cpu_cores; ++i)
        {
            add_mlv_cache_thread(video);
        }
    }
}

void resetMlvCache(mlvObject_t * video)
{
    resetMlvCachedFrame(video);
    invalidateMlvProcessedPreviewCache(video);
    mark_mlv_uncached(video);
}

void disableMlvCaching(mlvObject_t * video)
{
    /* Stop caching and make sure by waiting */
    video->stop_caching = 1;
    while (isMlvObjectCaching(video)) usleep(100);
    /* Remove the memory (it's a tradition in MLV App libraries to leave a couple of bytes) */
    mark_mlv_uncached(video);
    uint16_t * replacement = (uint16_t *)malloc(2);
    if (replacement)
    {
        free(video->cache_memory_block);
        video->cache_memory_block = replacement;
    }
}

void enableMlvCaching(mlvObject_t * video)
{
    /* This will reset the memory and start cache thread */
    video->stop_caching = 0;
    setMlvRawCacheLimitMegaBytes(video, video->cache_limit_mb);
}

/* Hmmmm, did anyone need 2 ways of doing this? */

static int g_mlv_cache_resize_allocation_failure_for_testing = -1;

void mlvCacheSetResizeAllocationFailureForTesting(int allocation_index)
{
    g_mlv_cache_resize_allocation_failure_for_testing = allocation_index;
}

static int mlv_cache_checked_frame_layout(mlvObject_t * video,
                                          uint64_t * frame_pixels,
                                          uint64_t * frame_size)
{
    if (!video || !frame_pixels || !frame_size)
        return 0;
    const int width = getMlvWidth(video);
    const int height = getMlvHeight(video);
    if (width <= 0 || height <= 0)
        return 0;
    const uint64_t checked_width = (uint64_t)width;
    const uint64_t checked_height = (uint64_t)height;
    if (checked_width > UINT64_MAX / checked_height)
        return 0;
    const uint64_t pixels = checked_width * checked_height;
    if (pixels > UINT64_MAX / 3u)
        return 0;
    const uint64_t rgb_pixels = pixels * 3u;
    if (rgb_pixels > UINT64_MAX / sizeof(uint16_t))
        return 0;
    *frame_pixels = rgb_pixels;
    *frame_size = rgb_pixels * sizeof(uint16_t);
    return 1;
}

static int mlv_cache_resize_transactional(mlvObject_t * video,
                                          uint64_t frame_limit,
                                          uint64_t cache_limit_bytes,
                                          uint64_t cache_limit_mb,
                                          uint64_t frame_pixels,
                                          uint64_t frame_size)
{
    if (!video
        || frame_limit > SIZE_MAX / sizeof(uint16_t *)
        || (frame_limit != 0 && frame_size > UINT64_MAX / frame_limit))
    {
        return 0;
    }
    const uint64_t block_bytes_u64 = frame_size * frame_limit;
    if (block_bytes_u64 > SIZE_MAX)
        return 0;
    const size_t block_bytes = block_bytes_u64 ? (size_t)block_bytes_u64 : 2u;
    const size_t pointer_bytes = frame_limit
        ? (size_t)frame_limit * sizeof(uint16_t *)
        : sizeof(uint16_t *);

    uint16_t * replacement_block =
        g_mlv_cache_resize_allocation_failure_for_testing == 0
        ? NULL
        : (uint16_t *)malloc(block_bytes);
    uint16_t ** replacement_frames =
        g_mlv_cache_resize_allocation_failure_for_testing == 1
        ? NULL
        : (uint16_t **)malloc(pointer_bytes);
    if (!replacement_block || !replacement_frames)
    {
        free(replacement_block);
        free(replacement_frames);
        return 0;
    }
    for (uint64_t i = 0; i < frame_limit; ++i)
        replacement_frames[i] = replacement_block + (size_t)(frame_pixels * i);

    free(video->cache_memory_block);
    free(video->rgb_raw_frames);
    video->cache_memory_block = replacement_block;
    video->rgb_raw_frames = replacement_frames;
    video->cache_limit_frames = frame_limit;
    video->cache_limit_bytes = cache_limit_bytes;
    video->cache_limit_mb = cache_limit_mb;
    mark_mlv_uncached(video);
    return 1;
}

static void mlv_cache_restart_workers_if_needed(mlvObject_t * video, int restart)
{
    if (!restart) return;
    video->stop_caching = 0;
    for (int i = 0; i < video->cpu_cores; ++i)
        add_mlv_cache_thread(video);
}

/* What I call MegaBytes is actually MebiBytes! I'm so upset to find that out :( */
void setMlvRawCacheLimitMegaBytes(mlvObject_t * video, uint64_t megaByteLimit)
{
    uint64_t frame_pix = 0;
    uint64_t frame_size = 0;
    if (!isMlvActive(video)
        || !mlv_cache_checked_frame_layout(video, &frame_pix, &frame_size)
        || megaByteLimit > UINT64_MAX / (1u << 20))
    {
        return;
    }
    const uint64_t bytes_limit = megaByteLimit * (1u << 20);
    const uint64_t frame_limit = MIN(bytes_limit / frame_size, getMlvFrames(video));

    int has_caching = 0;
    if (!video->stop_caching || isMlvObjectCaching(video))
    {
        has_caching = 1;
        video->stop_caching = 1;
        while (video->cache_thread_count) usleep(100);
    }
    (void)mlv_cache_resize_transactional(video,
                                         frame_limit,
                                         bytes_limit,
                                         megaByteLimit,
                                         frame_pix,
                                         frame_size);
    mlv_cache_restart_workers_if_needed(video, has_caching);
}

/* Not recommended */
void setMlvRawCacheLimitFrames(mlvObject_t * video, uint64_t frameLimit)
{
    uint64_t frame_pix = 0;
    uint64_t frame_size = 0;
    if (!isMlvActive(video)
        || !mlv_cache_checked_frame_layout(video, &frame_pix, &frame_size))
    {
        return;
    }
    const uint64_t frame_limit = MIN(frameLimit, getMlvFrames(video));
    if (frame_limit != 0 && frame_size > UINT64_MAX / frame_limit)
        return;
    const uint64_t bytes_limit = frame_size * frame_limit;
    const uint64_t mbyte_limit = bytes_limit / (1u << 20);

    int has_caching = 0;
    if (!video->stop_caching || isMlvObjectCaching(video))
    {
        has_caching = 1;
        video->stop_caching = 1;
        while (video->cache_thread_count) usleep(100);
    }
    (void)mlv_cache_resize_transactional(video,
                                         frame_limit,
                                         bytes_limit,
                                         mbyte_limit,
                                         frame_pix,
                                         frame_size);
    mlv_cache_restart_workers_if_needed(video, has_caching);
}

/* Marks all frames as not cached */
void mark_mlv_uncached(mlvObject_t * video)
{
    pthread_mutex_lock( &video->g_mutexFind );
    video->cache_generation++;
    video->cache_next = 0;
    invalidateMlvProcessedPreviewCache(video);
    for (uint64_t i = 0; i < getMlvFrames(video); ++i)
    {
        video->cached_frames[i] = MLV_FRAME_NOT_CACHED;
    }
    pthread_mutex_unlock( &video->g_mutexFind );
}

/* Clears cache by freeing then reallocating (RAM usage down until frames written) */
void clear_mlv_cache(mlvObject_t * video)
{
    mark_mlv_uncached(video);
    uint64_t frame_pixels = 0;
    uint64_t frame_size = 0;
    const uint64_t frame_limit = MIN(video->cache_limit_frames, getMlvFrames(video));
    if (!mlv_cache_checked_frame_layout(video, &frame_pixels, &frame_size)
        || (frame_limit != 0 && frame_size > UINT64_MAX / frame_limit))
    {
        return;
    }
    const uint64_t block_bytes_u64 = frame_size * frame_limit;
    if (block_bytes_u64 > SIZE_MAX)
        return;
    const size_t bytes = block_bytes_u64 ? (size_t)block_bytes_u64 : 2u;
    uint16_t * replacement =
        g_mlv_cache_resize_allocation_failure_for_testing == 0
        ? NULL
        : (uint16_t *)malloc(bytes);
    if (replacement)
    {
        free(video->cache_memory_block);
        video->cache_memory_block = replacement;
        for (uint64_t i = 0; i < frame_limit; ++i)
            video->rgb_raw_frames[i] = replacement + (size_t)(frame_pixels * i);
    }
}

/* Returns 1 on success, or 0 if all are cached */
int find_mlv_frame_to_cache(mlvObject_t * video, uint64_t * index) /* Outputs to *index */
{
    pthread_mutex_lock( &video->g_mutexFind );
    /* If a specific frame was requested */
    if (video->cache_next)
    {
        uint64_t requested = video->cache_next;
        video->cache_next = 0;
        if (mlv_frame_in_cache_window(video, requested))
        {
            *index = requested;
            pthread_mutex_unlock( &video->g_mutexFind );
            return 1;
        }
    }

    uint64_t start = mlv_cache_clamp_start(video, video->cache_start_frame);
    uint64_t end = mlv_cache_window_end(video);
    for (uint64_t frame = start; frame < end; ++frame)
    {
        /* Return index if it is not cached */
        if (video->cached_frames[frame] == MLV_FRAME_NOT_CACHED)
        {
            *index = frame;
            pthread_mutex_unlock( &video->g_mutexFind );
            return 1;
        }
    }
    pthread_mutex_unlock( &video->g_mutexFind );
    return 0;
}

static volatile int g_mlv_cache_worker_pause_for_testing = 0;

void mlvCacheSetWorkerStartPauseForTesting(int enabled)
{
    g_mlv_cache_worker_pause_for_testing = enabled != 0;
}

static void run_mlv_cache_thread(mlvObject_t * video, int counter_already_claimed);

static void mlv_cache_worker_count_release(mlvObject_t * video)
{
    pthread_mutex_lock(&video->g_mutexCount);
    video->cache_thread_count--;
    pthread_mutex_unlock(&video->g_mutexCount);
}

static void * mlv_cache_thread_entry(void * opaque)
{
    mlvObject_t * video = (mlvObject_t *)opaque;
    while (g_mlv_cache_worker_pause_for_testing)
        usleep(100);
    run_mlv_cache_thread(video, 1);
    return NULL;
}

/* Adds one detached thread.  Claim the active count before pthread_create so
 * object destruction cannot observe zero while a worker is pending start. */
void add_mlv_cache_thread(mlvObject_t * video)
{
    if (!video) return;
    pthread_mutex_lock(&video->g_mutexCount);
    video->cache_thread_count++;
    pthread_mutex_unlock(&video->g_mutexCount);

    pthread_t thread;
    if (pthread_create(&thread, NULL, mlv_cache_thread_entry, video) != 0)
    {
        mlv_cache_worker_count_release(video);
        return;
    }
    if (pthread_detach(thread) != 0)
    {
        /* A valid newly created thread should detach.  If the platform
         * refuses, join it here so its handle and object lifetime stay owned. */
        (void)pthread_join(thread, NULL);
    }
}

/* Synchronous entry retained for callers/tests; it owns its count claim. */
void an_mlv_cache_thread(mlvObject_t * video)
{
    run_mlv_cache_thread(video, 0);
}

/* Add as many of these as you want :) */
static int g_mlv_cache_allocation_failure_for_testing = 0;

void mlvCacheSetAllocationFailureForTesting(int enabled)
{
    g_mlv_cache_allocation_failure_for_testing = enabled != 0;
}

static void run_mlv_cache_thread(mlvObject_t * video, int counter_already_claimed)
{
    if (!video) return;
    if (!counter_already_claimed)
    {
        pthread_mutex_lock(&video->g_mutexCount);
        video->cache_thread_count++;
        pthread_mutex_unlock(&video->g_mutexCount);
    }
    if (!isMlvActive(video))
    {
        mlv_cache_worker_count_release(video);
        return;
    }

    uint32_t height = getMlvHeight(video);
    uint32_t width = getMlvWidth(video);
    const size_t checked_width = (size_t)width;
    const size_t checked_height = (size_t)height;
    if (width < 33 || height < 33
        || checked_width > SIZE_MAX / checked_height)
    {
        mlv_cache_worker_count_release(video);
        return;
    }
    const size_t pixelsize = checked_width * checked_height;
    if (pixelsize < 10u
        || pixelsize > SIZE_MAX / 3u
        || pixelsize > SIZE_MAX / sizeof(float)
        || checked_height > SIZE_MAX / sizeof(float *))
    {
        mlv_cache_worker_count_release(video);
        return;
    }

    /* 2d array uglyness */
    const int force_allocation_failure = g_mlv_cache_allocation_failure_for_testing;
    float  * __restrict imagefloat1d = force_allocation_failure ? NULL : (float *)malloc(pixelsize * sizeof(float));
    float ** __restrict imagefloat2d = force_allocation_failure ? NULL : (float **)malloc(checked_height * sizeof(float *));
    float  * __restrict red1d = force_allocation_failure ? NULL : (float *)malloc(pixelsize * sizeof(float));
    float ** __restrict red2d = force_allocation_failure ? NULL : (float **)malloc(checked_height * sizeof(float *));
    float  * __restrict green1d = force_allocation_failure ? NULL : (float *)malloc(pixelsize * sizeof(float));
    float ** __restrict green2d = force_allocation_failure ? NULL : (float **)malloc(checked_height * sizeof(float *));
    float  * __restrict blue1d = force_allocation_failure ? NULL : (float *)malloc(pixelsize * sizeof(float));
    float ** __restrict blue2d = force_allocation_failure ? NULL : (float **)malloc(checked_height * sizeof(float *));
    if (!imagefloat1d || !imagefloat2d
        || !red1d || !red2d
        || !green1d || !green2d
        || !blue1d || !blue2d)
    {
        free(red1d);
        free(red2d);
        free(green1d);
        free(green2d);
        free(blue1d);
        free(blue2d);
        free(imagefloat2d);
        free(imagefloat1d);
        mlv_cache_worker_count_release(video);
        return;
    }
    for (size_t y = 0; y < checked_height; ++y)
    {
        const size_t row_offset = y * checked_width;
        imagefloat2d[y] = imagefloat1d + row_offset;
        red2d[y] = red1d + row_offset;
        green2d[y] = green1d + row_offset;
        blue2d[y] = blue1d + row_offset;
    }

    pthread_mutex_lock( &video->g_mutexCount );
    amazeinfo_t amaze_params = {
        .rawData =  imagefloat2d,
        .red     =  red2d,
        .green   =  green2d,
        .blue    =  blue2d,
        .winx    =  0,
        .winy    =  0,
        .winw    =  getMlvWidth(video),
        .winh    =  getMlvHeight(video),
        .cfa     =  0
    };
    pthread_mutex_unlock( &video->g_mutexCount );

    while (1 < 2)
    {
        if (video->stop_caching) break;

        uint64_t cache_frame;
        uint32_t cache_generation;

        /* If cache finder reurns false, it's time t stop caching */
        if (!find_mlv_frame_to_cache(video, &cache_frame)) break;

        pthread_mutex_lock( &video->g_mutexFind );
        if (!mlv_frame_in_cache_window(video, cache_frame)
            || video->cached_frames[cache_frame] != MLV_FRAME_NOT_CACHED)
        {
            pthread_mutex_unlock( &video->g_mutexFind );
            continue;
        }
        cache_generation = video->cache_generation;
        video->cached_frames[cache_frame] = MLV_FRAME_BEING_CACHED;
        pthread_mutex_unlock( &video->g_mutexFind );

        getMlvRawFrameFloat(video, cache_frame, imagefloat1d);

        /* Single thread AMaZE */
        const int demosaic_succeeded = demosaic(&amaze_params);

        if (!demosaic_succeeded)
        {
            pthread_mutex_lock(&video->g_mutexFind);
            if (cache_generation == video->cache_generation
                && video->cached_frames[cache_frame] == MLV_FRAME_BEING_CACHED)
            {
                video->cached_frames[cache_frame] = MLV_FRAME_NOT_CACHED;
            }
            pthread_mutex_unlock(&video->g_mutexFind);
            /* A persistent worker/allocation failure would otherwise select
             * the same NOT_CACHED frame immediately and hot-spin forever.
             * Leave the cache thread; a later explicitly started worker may
             * retry after resources recover. */
            break;
        }

        pthread_mutex_lock( &video->g_mutexFind );
        int cache_still_valid = (cache_generation == video->cache_generation);
        uint64_t cache_slot = mlv_cache_slot_for_frame(video, cache_frame);
        pthread_mutex_unlock( &video->g_mutexFind );
        if (!cache_still_valid) continue;

        /* To 16-bit */
        uint16_t * out = video->rgb_raw_frames[cache_slot];
        for (size_t i = 0; i < pixelsize - 10u; i++)
        {
            uint16_t * pix = out + (i * 3u);
            pix[0] = (uint16_t)MIN(red1d[i], 65535);
            pix[1] = (uint16_t)MIN(green1d[i], 65535);
            pix[2] = (uint16_t)MIN(blue1d[i], 65535);
        }

        pthread_mutex_lock( &video->g_mutexFind );
        if (cache_generation == video->cache_generation)
        {
            video->cached_frames[cache_frame] = MLV_FRAME_IS_CACHED;
        }
        else
        {
            video->cached_frames[cache_frame] = MLV_FRAME_NOT_CACHED;
        }
        pthread_mutex_unlock( &video->g_mutexFind );

        DEBUG( printf("Debayered frame %llu/%llu has been cached.\n", cache_frame+1, video->cache_limit_frames); )
    }

    free(red1d);
    free(red2d);
    free(green1d);
    free(green2d);
    free(blue1d);
    free(blue2d);
    free(imagefloat2d);
    free(imagefloat1d);

    mlv_cache_worker_count_release(video);
}

/* Gets a freshly debayered frame every time ( temp memory should be Width * Height * sizeof(float) ) */
static int get_mlv_raw_frame_float_isolated_analysis(mlvObject_t * video,
                                                     uint64_t frame_index,
                                                     float * output_frame)
{
    int width = getMlvWidth(video);
    int height = getMlvHeight(video);
    const size_t frame_pixels = (size_t)width * (size_t)height;
    uint16_t * raw_frame_u16 = (uint16_t *)output_frame;
    int bit_shift = 0;

    if (getMlvRawFrameProcessedUint16Direct(video, frame_index, raw_frame_u16, &bit_shift))
    {
        memset(output_frame, 0, frame_pixels * sizeof(float));
        return 1;
    }

    for (int64_t i = (int64_t)frame_pixels - 1; i >= 0; --i)
    {
        output_frame[i] = (float)((uint32_t)raw_frame_u16[i] << bit_shift);
    }

    return 0;
}

static void get_mlv_raw_frame_debayered_impl( mlvObject_t * video,
                                              uint64_t frame_index,
                                              float * temp_memory,
                                              uint16_t * output_frame,
                                              int debayer_type,
                                              int isolated_analysis ) /* 0=bilinear 1=amaze ... */
{
    int width = getMlvWidth(video);
    int height = getMlvHeight(video);
    const size_t frame_pixels = (size_t)width * (size_t)height;
    const int debayer_threads = isolated_analysis ? 1 : getMlvCpuCores(video);

    if( debayer_type == 0 )
    {
        uint16_t * raw_frame_u16 = (uint16_t *)temp_memory;
        int bit_shift = 0;
        int raw_failed = isolated_analysis
            ? getMlvRawFrameProcessedUint16Direct(video, frame_index, raw_frame_u16, &bit_shift)
            : getMlvRawFrameProcessedUint16(video, frame_index, raw_frame_u16, &bit_shift);
        if (raw_failed)
        {
            memset(output_frame, 0, frame_pixels * 3u * sizeof(uint16_t));
            return;
        }

        const double debayer_kernel_start = mlv_debayer_timing_now_seconds();
        debayerBasicU16(output_frame,
                        raw_frame_u16,
                        width,
                        height,
                        debayer_threads,
                        bit_shift);
        g_mlv_last_debayer_kernel_ms = (mlv_debayer_timing_now_seconds() - debayer_kernel_start) * 1000.0;
        return;
    }

    if( debayer_type == 2 )
    {
        uint16_t * raw_frame_u16 = (uint16_t *)temp_memory;
        int bit_shift = 0;
        int raw_failed = isolated_analysis
            ? getMlvRawFrameProcessedUint16Direct(video, frame_index, raw_frame_u16, &bit_shift)
            : getMlvRawFrameProcessedUint16(video, frame_index, raw_frame_u16, &bit_shift);
        if (raw_failed)
        {
            memset(output_frame, 0, frame_pixels * 3u * sizeof(uint16_t));
            return;
        }

        const double debayer_kernel_start = mlv_debayer_timing_now_seconds();
        debayerNoneU16(output_frame,
                       raw_frame_u16,
                       width,
                       height,
                       debayer_threads,
                       bit_shift);
        g_mlv_last_debayer_kernel_ms = (mlv_debayer_timing_now_seconds() - debayer_kernel_start) * 1000.0;
        return;
    }

    /* Get the raw data in B&W */
    if (isolated_analysis)
    {
        if (get_mlv_raw_frame_float_isolated_analysis(video, frame_index, temp_memory))
        {
            memset(output_frame, 0, frame_pixels * 3u * sizeof(uint16_t));
            return;
        }
    }
    else
    {
        getMlvRawFrameFloat(video, frame_index, temp_memory);
    }

    wb_convert_info_t wb_info;

    /* WB conversion for ideal debayer result, not for bilinear, easy and non debayer */
    if( !( debayer_type == 0 || debayer_type == 2 || debayer_type == 3 ) )
    {
        const double wb_prepare_start = mlv_debayer_timing_now_seconds();
        wb_convert(&wb_info, temp_memory, width, height, getMlvBlackLevel(video));
        g_mlv_last_debayer_wb_prepare_ms = (mlv_debayer_timing_now_seconds() - wb_prepare_start) * 1000.0;

        /* CA correction, multithreaded, not for bilinear, easy and non debayer because not visible and slow */
        if( video->ca_red <= -0.1 || video->ca_red >= 0.1
         || video->ca_blue <= -0.1 || video->ca_blue >= 0.1 )
        {
            const double ca_start = mlv_debayer_timing_now_seconds();
            /* 2d array for CA correction */
            float ** __restrict imagefloat2d = (float **)malloc(height * sizeof(float *));
            for (int y = 0; y < height; ++y) imagefloat2d[y] = (float *)(temp_memory+(y*width));

            /* the magic CA correction function */
            /*CA_correct_RT(imagefloat2d, 0, 0, width, height,
                          0, 0, width, height,
                          0, video->ca_red, video->ca_blue);*/ /*auto, red, blue*/

            lrtpCaCorrect( imagefloat2d, 0, 0, width, height,
                           0, 0, video->ca_red, video->ca_blue, 0 );
            g_mlv_last_debayer_ca_ms = (mlv_debayer_timing_now_seconds() - ca_start) * 1000.0;
        }
    }

    /* Debayer */
    const double debayer_kernel_start = mlv_debayer_timing_now_seconds();
    if (/*debayer_type == 1 ||*/ debayer_type == 4 || debayer_type == 5 || /*debayer_type == 6 ||*/ debayer_type == 7 || debayer_type == 8)
    {
        //AMaZE and AHD disabled from librtprocess because of bad artifacts
        debayerLibRtProcess(output_frame, temp_memory, width, height, debayer_type, video->processing->cam_matrix);
    }
    else if (debayer_type == 1 )
    {
        if (!debayerAmaze(output_frame,
                          temp_memory,
                          width,
                          height,
                          debayer_threads,
                          getMlvBlackLevel(video)))
        {
            memset(output_frame, 0, frame_pixels * 3u * sizeof(uint16_t));
            return;
        }
    }
    else if(debayer_type == 2 || debayer_type == 3)
    {
        /* threaded easy types */
        debayerEasy(output_frame, temp_memory, width, height, debayer_threads, debayer_type);
    }
    else if (debayer_type == 6 )
    {
        debayerAhd(output_frame, temp_memory, width, height);
    }
    else
    {
        /* Debayer quickly (bilinearly) */
        debayerBasic(output_frame, temp_memory, width, height, 1);
    }
    g_mlv_last_debayer_kernel_ms = (mlv_debayer_timing_now_seconds() - debayer_kernel_start) * 1000.0;

    /* WB conversion undo for ideal debayer result */
    if( !( debayer_type == 0 || debayer_type == 2 || debayer_type == 3 ) )
    {
        const double wb_undo_start = mlv_debayer_timing_now_seconds();
        wb_undo(&wb_info, output_frame, width, height, getMlvBlackLevel(video));
        g_mlv_last_debayer_wb_undo_ms = (mlv_debayer_timing_now_seconds() - wb_undo_start) * 1000.0;
    }

    /* S3_debayer capture: post Bayer->RGB16, all debayer algorithms (AMaZE,
     * Bilinear, AHD, LibRtProcess, Easy) converge here before return. Inert
     * when MLVAPP_PIPELINE_CAPTURE_DIR is unset. */
    if( mlv_pipeline_capture_should_capture_frame(frame_index) )
    {
        const char * debayer_label =
            debayer_type == 0 ? "none"   :
            debayer_type == 1 ? "amaze"  :
            debayer_type == 2 ? "easy"   :
            debayer_type == 3 ? "bilinear" :
            debayer_type == 4 ? "ahd"    :
            debayer_type == 5 ? "rcd"    :
            debayer_type == 6 ? "dcb"    :
            debayer_type == 7 ? "lmmse"  :
            debayer_type == 8 ? "igv"    :
            "unknown";
        mlv_pipeline_capture_meta_t meta;
        memset(&meta, 0, sizeof meta);
        meta.stage = MLV_PIPELINE_STAGE_S3_DEBAYER;
        meta.format = MLV_PIPELINE_FORMAT_UINT16_RGB;
        meta.format_label = "uint16_rgb_post_debayer";
        meta.width = width;
        meta.height = height;
        meta.bytes_per_line = width * 3 * (int)sizeof(uint16_t);
        meta.bytes_per_pixel = 3 * (int)sizeof(uint16_t);
        meta.channels = 3;
        meta.bit_depth = 16;
        meta.dual_iso_mode = NULL;
        meta.debayer_mode = debayer_label;
        meta.scaler = "none";
        meta.path_label = isolated_analysis
            ? "get_mlv_raw_frame_debayered_isolated_analysis"
            : "get_mlv_raw_frame_debayered";
        mlv_pipeline_capture(frame_index, output_frame, &meta);
    }
}

void get_mlv_raw_frame_debayered( mlvObject_t * video,
                                  uint64_t frame_index,
                                  float * temp_memory,
                                  uint16_t * output_frame,
                                  int debayer_type ) /* 0=bilinear 1=amaze ... */
{
    get_mlv_raw_frame_debayered_impl(video,
                                     frame_index,
                                     temp_memory,
                                     output_frame,
                                     debayer_type,
                                     0);
}

void get_mlv_raw_frame_debayered_isolated_analysis( mlvObject_t * video,
                                                    uint64_t frame_index,
                                                    float * temp_memory,
                                                    uint16_t * output_frame,
                                                    int debayer_type ) /* 0=bilinear 1=amaze ... */
{
    get_mlv_raw_frame_debayered_impl(video,
                                     frame_index,
                                     temp_memory,
                                     output_frame,
                                     debayer_type,
                                     1);
}
