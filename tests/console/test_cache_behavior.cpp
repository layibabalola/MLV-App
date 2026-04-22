#include "../common/minitest.h"

extern "C" {
#include "../../src/mlv/video_mlv.h"
}

#include <cstdint>
#include <memory>
#include <vector>

static std::unique_ptr<mlvObject_t> make_cache_test_video(std::vector<uint8_t> * cache_states)
{
    auto video = std::make_unique<mlvObject_t>();
    *video = {};

    video->is_active = 1;
    video->frames = static_cast<uint64_t>(cache_states->size());
    video->cache_limit_frames = static_cast<uint64_t>(cache_states->size());
    video->RAWI.xRes = 2;
    video->RAWI.yRes = 1;
    video->cached_frames = cache_states->data();
    video->current_cached_frame_active = 1;
    video->current_processed_frame_active = 1;
    video->current_processed_frame = 9;
    video->current_processed_frame_threads = 3;
    video->current_processed_frame_signature = 33;
    video->processed_16bit_cache_active[0] = 1;
    video->processed_16bit_cache_active[1] = 1;
    video->processed_16bit_cache_frame[0] = 2;
    video->processed_16bit_cache_frame[1] = 3;
    video->processed_16bit_cache_threads[0] = 1;
    video->processed_16bit_cache_threads[1] = 2;
    video->processed_16bit_cache_signature[0] = 111;
    video->processed_16bit_cache_signature[1] = 222;
    video->processed_16bit_cache_next_slot = 1;
    video->current_processed_frame_8bit_active = 1;
    video->processed_8bit_cache_active[0] = 1;
    video->processed_8bit_cache_active[1] = 1;
    video->processed_8bit_cache_frame[0] = 2;
    video->processed_8bit_cache_frame[1] = 3;
    video->processed_8bit_cache_threads[0] = 1;
    video->processed_8bit_cache_threads[1] = 2;
    video->processed_8bit_cache_signature[0] = 11;
    video->processed_8bit_cache_signature[1] = 22;
    video->processed_8bit_cache_next_slot = 2;
    video->cpu_cores = 0;

    const uint64_t cache_words = video->cache_limit_frames * static_cast<uint64_t>(video->RAWI.xRes) * video->RAWI.yRes * 3;
    video->cache_memory_block = static_cast<uint16_t *>(calloc(cache_words ? cache_words : 1, sizeof(uint16_t)));
    video->rgb_raw_frames = static_cast<uint16_t **>(calloc(video->cache_limit_frames ? video->cache_limit_frames : 1, sizeof(uint16_t *)));
    for (uint64_t slot = 0; slot < video->cache_limit_frames; ++slot) {
        video->rgb_raw_frames[slot] = video->cache_memory_block + (slot * video->RAWI.xRes * video->RAWI.yRes * 3);
    }

    pthread_mutex_init(&video->g_mutexFind, nullptr);
    pthread_mutex_init(&video->g_mutexCount, nullptr);
    pthread_mutex_init(&video->llrawproc_mutex, nullptr);
    pthread_mutex_init(&video->llrawproc_worker_mutex, nullptr);

    return video;
}

static void destroy_cache_test_video(mlvObject_t * video)
{
    pthread_mutex_destroy(&video->g_mutexFind);
    pthread_mutex_destroy(&video->g_mutexCount);
    pthread_mutex_destroy(&video->llrawproc_mutex);
    pthread_mutex_destroy(&video->llrawproc_worker_mutex);
    free(video->rgb_raw_frames);
    free(video->cache_memory_block);
}

TEST(CacheBehavior, ResetMlvCacheClearsStatesAndCurrentCachedFrame)
{
    std::vector<uint8_t> cache_states = {
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_BEING_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_IS_CACHED
    };
    auto video = make_cache_test_video(&cache_states);

    resetMlvCache(video.get());

    ASSERT_EQ(0, video->current_cached_frame_active);
    ASSERT_EQ(0, video->current_processed_frame_active);
    ASSERT_EQ(static_cast<uint64_t>(0), video->current_processed_frame);
    ASSERT_EQ(0, video->current_processed_frame_threads);
    ASSERT_EQ(static_cast<uint64_t>(0), video->current_processed_frame_signature);
    ASSERT_EQ(0, video->current_processed_frame_8bit_active);
    ASSERT_EQ(static_cast<unsigned int>(0), static_cast<unsigned int>(video->processed_16bit_cache_next_slot));
    ASSERT_EQ(static_cast<unsigned int>(0), static_cast<unsigned int>(video->processed_8bit_cache_next_slot));
    for (int slot = 0; slot < MLV_PROCESSED_16BIT_CACHE_SLOTS; ++slot) {
        ASSERT_EQ(static_cast<unsigned int>(0), static_cast<unsigned int>(video->processed_16bit_cache_active[slot]));
        ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(video->processed_16bit_cache_frame[slot]));
        ASSERT_EQ(0, video->processed_16bit_cache_threads[slot]);
        ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(video->processed_16bit_cache_signature[slot]));
    }
    for (int slot = 0; slot < MLV_PROCESSED_8BIT_CACHE_SLOTS; ++slot) {
        ASSERT_EQ(static_cast<unsigned int>(0), static_cast<unsigned int>(video->processed_8bit_cache_active[slot]));
        ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(video->processed_8bit_cache_frame[slot]));
        ASSERT_EQ(0, video->processed_8bit_cache_threads[slot]);
        ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(video->processed_8bit_cache_signature[slot]));
    }
    for (uint8_t state : cache_states) {
        ASSERT_EQ(static_cast<unsigned int>(MLV_FRAME_NOT_CACHED), static_cast<unsigned int>(state));
    }

    destroy_cache_test_video(video.get());
}

TEST(CacheBehavior, FindMlvFrameToCacheSkipsBusyAndCachedFrames)
{
    std::vector<uint8_t> cache_states = {
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_BEING_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED
    };
    auto video = make_cache_test_video(&cache_states);

    uint64_t index = 999;
    ASSERT_TRUE(find_mlv_frame_to_cache(video.get(), &index));
    ASSERT_EQ(static_cast<unsigned long long>(2), static_cast<unsigned long long>(index));

    cache_states[2] = MLV_FRAME_IS_CACHED;
    ASSERT_TRUE(find_mlv_frame_to_cache(video.get(), &index));
    ASSERT_EQ(static_cast<unsigned long long>(4), static_cast<unsigned long long>(index));

    destroy_cache_test_video(video.get());
}

TEST(CacheBehavior, FindMlvFrameToCacheHonorsExplicitCacheNext)
{
    std::vector<uint8_t> cache_states = {
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED
    };
    auto video = make_cache_test_video(&cache_states);
    video->cache_next = 4;

    uint64_t index = 0;
    ASSERT_TRUE(find_mlv_frame_to_cache(video.get(), &index));
    ASSERT_EQ(static_cast<unsigned long long>(4), static_cast<unsigned long long>(index));
    ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(video->cache_next));

    destroy_cache_test_video(video.get());
}

TEST(CacheBehavior, FindMlvFrameToCacheUsesCacheStartFrameWindow)
{
    std::vector<uint8_t> cache_states = {
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_BEING_CACHED,
        MLV_FRAME_NOT_CACHED
    };
    auto video = make_cache_test_video(&cache_states);
    video->cache_limit_frames = 3;
    video->cache_start_frame = 5;

    uint64_t index = 999;
    ASSERT_TRUE(find_mlv_frame_to_cache(video.get(), &index));
    ASSERT_EQ(static_cast<unsigned long long>(5), static_cast<unsigned long long>(index));

    cache_states[5] = MLV_FRAME_IS_CACHED;
    ASSERT_TRUE(find_mlv_frame_to_cache(video.get(), &index));
    ASSERT_EQ(static_cast<unsigned long long>(7), static_cast<unsigned long long>(index));

    destroy_cache_test_video(video.get());
}

TEST(CacheBehavior, EnsureWindowSlidesMinimallyAndPreservesOverlapPointers)
{
    std::vector<uint8_t> cache_states = {
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_NOT_CACHED
    };
    auto video = make_cache_test_video(&cache_states);
    video->cache_limit_frames = 4;
    video->cache_start_frame = 2;
    video->stop_caching = 0;

    uint16_t * old_frame3_ptr = video->rgb_raw_frames[1];
    uint16_t * old_frame5_ptr = video->rgb_raw_frames[3];

    mlv_cache_ensure_window(video.get(), 6);

    ASSERT_EQ(static_cast<unsigned long long>(3), static_cast<unsigned long long>(video->cache_start_frame));
    ASSERT_EQ(static_cast<unsigned int>(MLV_FRAME_NOT_CACHED), static_cast<unsigned int>(cache_states[2]));
    ASSERT_EQ(static_cast<unsigned int>(MLV_FRAME_IS_CACHED), static_cast<unsigned int>(cache_states[3]));
    ASSERT_EQ(static_cast<unsigned int>(MLV_FRAME_IS_CACHED), static_cast<unsigned int>(cache_states[5]));
    ASSERT_EQ(static_cast<unsigned int>(MLV_FRAME_NOT_CACHED), static_cast<unsigned int>(cache_states[6]));
    ASSERT_EQ(static_cast<std::uintptr_t>(reinterpret_cast<std::uintptr_t>(old_frame3_ptr)),
              static_cast<std::uintptr_t>(reinterpret_cast<std::uintptr_t>(video->rgb_raw_frames[0])));
    ASSERT_EQ(static_cast<std::uintptr_t>(reinterpret_cast<std::uintptr_t>(old_frame5_ptr)),
              static_cast<std::uintptr_t>(reinterpret_cast<std::uintptr_t>(video->rgb_raw_frames[2])));

    destroy_cache_test_video(video.get());
}

TEST(CacheBehavior, EnsureWindowCanShiftWhenCachingThreadsAreIdle)
{
    std::vector<uint8_t> cache_states = {
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_NOT_CACHED
    };
    auto video = make_cache_test_video(&cache_states);
    video->cache_limit_frames = 2;
    video->cache_start_frame = 0;
    video->stop_caching = 0;
    video->cache_thread_count = 0;

    mlv_cache_ensure_window(video.get(), 2);

    ASSERT_EQ(static_cast<unsigned long long>(1), static_cast<unsigned long long>(video->cache_start_frame));
    ASSERT_TRUE(mlv_frame_in_cache_window(video.get(), 2));
    ASSERT_EQ(static_cast<unsigned int>(MLV_FRAME_NOT_CACHED), static_cast<unsigned int>(cache_states[0]));

    destroy_cache_test_video(video.get());
}

TEST(CacheBehavior, PlaybackPrerollRequestsFirstFutureUncachedFrame)
{
    std::vector<uint8_t> cache_states = {
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED
    };
    auto video = make_cache_test_video(&cache_states);
    video->cache_limit_frames = 4;
    video->cache_start_frame = 2;
    video->stop_caching = 0;
    video->cpu_cores = 0;

    mlv_cache_request_playback_preroll(video.get(), 2, 5, 2);

    ASSERT_EQ(static_cast<unsigned long long>(3), static_cast<unsigned long long>(video->cache_next));
    uint64_t index = 999;
    ASSERT_TRUE(find_mlv_frame_to_cache(video.get(), &index));
    ASSERT_EQ(static_cast<unsigned long long>(3), static_cast<unsigned long long>(index));

    destroy_cache_test_video(video.get());
}

TEST(CacheBehavior, PlaybackPrerollSlidesWindowTowardLookahead)
{
    std::vector<uint8_t> cache_states = {
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_IS_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_NOT_CACHED,
        MLV_FRAME_NOT_CACHED
    };
    auto video = make_cache_test_video(&cache_states);
    video->cache_limit_frames = 2;
    video->cache_start_frame = 0;
    video->stop_caching = 0;
    video->cpu_cores = 0;

    mlv_cache_request_playback_preroll(video.get(), 1, 4, 2);

    ASSERT_EQ(static_cast<unsigned long long>(2), static_cast<unsigned long long>(video->cache_start_frame));
    ASSERT_TRUE(mlv_frame_in_cache_window(video.get(), 3));
    ASSERT_EQ(static_cast<unsigned long long>(2), static_cast<unsigned long long>(video->cache_next));

    destroy_cache_test_video(video.get());
}
