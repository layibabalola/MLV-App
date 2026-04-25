#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <pthread.h>
#include <math.h>
#include <time.h>
#include <inttypes.h>
#include "camid/camera_id.h"

#include <unistd.h>
#if defined(__linux)
#include <alloca.h>
#endif

#include "video_mlv.h"
#include "../debug/StageTiming.h"
#include "pipeline_stage_capture.h"
#include "audio_mlv.h"

#include "raw.h"
#include "mlv.h"
#include "llrawproc/llrawproc.h"
#include "mcraw/mcraw.h"

/* Debayering module */
#include "../debayer/debayer.h"
/* Processing module */
#include "../processing/raw_processing.h"
/* Phase 4B: fused downsample-and-debayer for adaptive playback resolution. */
#include "../processing/playback_downsample.h"

/* Lossless decompression */
#include "liblj92/lj92.h"

/* Bitunpack and lossless compression */
#include "../dng/dng.h"

#define MIN(a,b) (((a)<(b))?(a):(b))
#define MAX(a,b) (((a)>(b))?(a):(b))
#define ROR32(v,a) ((v) >> (a) | (v) << (32-(a)))

static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_disk_read_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_decompress_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_decompress_prepare_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_decompress_execute_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_pred6_split_active = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_pred6_split_requested = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_generic_split_active = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_generic_split_requested = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_pred1_fast_path_active = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_requested = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_active = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_pred1_fast_path_eligible = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_scan_component_count = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_write_length = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_expected_write_length = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_skip_length = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_linearize_active = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_component_count = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_lj92_predictor = -1;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_pred6_total_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_pred6_bitstream_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_pred6_predictor_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_generic_total_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_generic_bitstream_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_generic_predictor_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_pred1_fast_path_total_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_pred1_fast_path_bitstream_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_lj92_pred1_fast_path_predictor_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_unpack_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_uint16_copy_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_raw_uint16_prefetch_hit = 0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_llrawproc_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_raw_float_convert_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_debayered_frame_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_processing_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_processed16_total_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_processed16_for_8bit_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_processed16_to_8bit_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL double g_mlv_last_processed8_total_ms = 0.0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_processed8_direct_path_active = 0;
static MLV_STAGE_THREAD_LOCAL int g_mlv_last_processed8_prefetch_hit = 0;

static uint64_t file_set_pos(FILE *stream, uint64_t offset, int whence)
{
#if defined(__WIN32)
    return fseeko64(stream, offset, whence);
#else
    return fseek(stream, offset, whence);
#endif
}

static uint64_t file_get_pos(FILE *stream)
{
#if defined(__WIN32)
    return ftello64(stream);
#else
    return ftell(stream);
#endif
}

#ifndef STDOUT_SILENT
#define DEBUG(CODE) CODE
#else
#define DEBUG(CODE)
#endif

#ifdef __WIN32
#define FMT_SIZE "%u"
#else
#define FMT_SIZE "%zu"
#endif

#define MLV_FNV1A_OFFSET_BASIS UINT64_C(14695981039346656037)
#define MLV_FNV1A_PRIME UINT64_C(1099511628211)
#define MLV_RAW_UINT16_PREFETCH_EMPTY 0
#define MLV_RAW_UINT16_PREFETCH_READY 1
#define MLV_RAW_UINT16_PREFETCH_DECODING 2
#define MLV_RAW_UINT16_PREFETCH_LOOKAHEAD 2
#define MLV_PROCESSED_8BIT_PREFETCH_EMPTY 0
#define MLV_PROCESSED_8BIT_PREFETCH_READY 1
#define MLV_PROCESSED_8BIT_PREFETCH_RENDERING 2
#define MLV_PROCESSED_8BIT_PREFETCH_LOOKAHEAD 2

#if defined(_MSC_VER)
#define MLV_THREAD_LOCAL __declspec(thread)
#else
#define MLV_THREAD_LOCAL __thread
#endif

static void mlv_reset_last_raw_stage_telemetry(void)
{
    g_mlv_last_raw_uint16_ms = 0.0;
    g_mlv_last_raw_uint16_disk_read_ms = 0.0;
    g_mlv_last_raw_uint16_decompress_ms = 0.0;
    g_mlv_last_raw_uint16_decompress_prepare_ms = 0.0;
    g_mlv_last_raw_uint16_decompress_execute_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred6_split_active = 0;
    g_mlv_last_raw_uint16_lj92_pred6_split_requested = 0;
    g_mlv_last_raw_uint16_lj92_generic_split_active = 0;
    g_mlv_last_raw_uint16_lj92_generic_split_requested = 0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_active = 0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_requested = 0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_active = 0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_eligible = 0;
    g_mlv_last_raw_uint16_lj92_scan_component_count = 0;
    g_mlv_last_raw_uint16_lj92_write_length = 0;
    g_mlv_last_raw_uint16_lj92_expected_write_length = 0;
    g_mlv_last_raw_uint16_lj92_skip_length = 0;
    g_mlv_last_raw_uint16_lj92_linearize_active = 0;
    g_mlv_last_raw_uint16_lj92_component_count = 0;
    g_mlv_last_raw_uint16_lj92_predictor = -1;
    g_mlv_last_raw_uint16_lj92_pred6_total_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred6_bitstream_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred6_predictor_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_generic_total_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_generic_bitstream_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_generic_predictor_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_total_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_bitstream_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_predictor_ms = 0.0;
    g_mlv_last_raw_uint16_unpack_ms = 0.0;
    g_mlv_last_raw_uint16_copy_ms = 0.0;
    g_mlv_last_raw_uint16_prefetch_hit = 0;
}

static int mlv_env_value_is_truthy(const char * value)
{
    if (!value || value[0] == '\0') return 0;
    if (strcmp(value, "1") == 0) return 1;
    if (strcasecmp(value, "true") == 0) return 1;
    if (strcasecmp(value, "yes") == 0) return 1;
    if (strcasecmp(value, "on") == 0) return 1;
    return 0;
}

/* Matches lj92.c's lj92_pred6_split_enabled-style truthy check: any non-empty
 * value other than literal "0" enables the feature. Broader than
 * mlv_env_value_is_truthy so the prefetch disable here tracks whatever lj92
 * considers "profiling requested". */
static int mlv_env_value_is_lj92_truthy(const char * value)
{
    if (!value || value[0] == '\0') return 0;
    if (value[0] == '0' && value[1] == '\0') return 0;
    return 1;
}

static int mlv_raw_uint16_prefetch_enabled(void)
{
    static int enabled = -1;
    if (enabled >= 0)
    {
        return enabled;
    }

    if (mlv_env_value_is_truthy(getenv("MLVAPP_DISABLE_RAW_UINT16_PREFETCH")))
    {
        enabled = 0;
    }
    else if (mlv_env_value_is_lj92_truthy(getenv("MLVAPP_PROFILE_LJ92_PRED6_SPLIT"))
             || mlv_env_value_is_lj92_truthy(getenv("MLVAPP_PROFILE_LJ92_GENERIC_SPLIT"))
             || mlv_env_value_is_lj92_truthy(getenv("MLVAPP_PRED1_FASTPATH_MEASUREMENT")))
    {
        /* Invariant (future maintainers, preserve this):
         *   These profiling modes populate THREAD-LOCAL telemetry on the
         *   decoding thread. The consumer then reads those TLs back on its
         *   own thread after the decode returns. For the signals to reach
         *   the consumer at all, DECODE MUST REMAIN ON THE CALLER / READ
         *   THREAD — running it in the prefetch worker puts the TLs on the
         *   worker thread, where the consumer never sees them.
         *
         * Therefore: while any of these profiling env vars are truthy, the
         * prefetch is implicitly disabled so decode stays in-thread. This
         * gate is the single place that enforces the invariant; do not try
         * to propagate thread-locality awareness through the worker path. */
        enabled = 0;
    }
    else
    {
        enabled = 1;
    }
    return enabled;
}

static int mlv_processed8_prefetch_enabled(void)
{
    static int enabled = -1;
    if (enabled >= 0)
    {
        return enabled;
    }

    const char * value = getenv("MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH");
    enabled =
        (value
         && value[0] != '\0'
         && strcmp(value, "0") != 0
         && strcasecmp(value, "false") != 0
         && strcasecmp(value, "off") != 0)
            ? 1
            : 0;
    return enabled;
}

static uint64_t mlv_hash_bytes(uint64_t hash, const void * data, size_t size)
{
    const uint8_t * bytes = (const uint8_t *)data;
    if (!bytes || !size)
    {
        return hash;
    }

    for (size_t i = 0; i < size; ++i)
    {
        hash ^= bytes[i];
        hash *= MLV_FNV1A_PRIME;
    }
    return hash;
}

static uint64_t mlv_hash_c_string(uint64_t hash, const char * value)
{
    if (!value)
    {
        return mlv_hash_bytes(hash, "", 1);
    }

    return mlv_hash_bytes(hash, value, strlen(value) + 1);
}

static uint64_t mlv_hash_sampled_bytes(uint64_t hash, const void * data, size_t size)
{
    const uint8_t * bytes = (const uint8_t *)data;
    const size_t sample_count = 64;
    if (!bytes || !size)
    {
        return hash;
    }

    hash = mlv_hash_bytes(hash, &size, sizeof(size));
    if (size <= sample_count)
    {
        return mlv_hash_bytes(hash, data, size);
    }

    for (size_t sample = 0; sample < sample_count; ++sample)
    {
        size_t offset = (sample * (size - 1)) / (sample_count - 1);
        hash = mlv_hash_bytes(hash, bytes + offset, 1);
    }

    return hash;
}

static uint64_t mlv_hash_pixel_map(uint64_t hash, const pixel_map * map)
{
    if (!map)
    {
        return mlv_hash_bytes(hash, "", 1);
    }

    hash = mlv_hash_bytes(hash, &map->type, sizeof(map->type));
    hash = mlv_hash_bytes(hash, &map->count, sizeof(map->count));
    hash = mlv_hash_bytes(hash, &map->capacity, sizeof(map->capacity));
    if (map->pixels && map->count)
    {
        hash = mlv_hash_bytes(hash, map->pixels, map->count * sizeof(pixel_xy));
    }
    return hash;
}

static uint64_t mlv_hash_filter_object(uint64_t hash, const filterObject_t * filter)
{
    if (!filter)
    {
        return mlv_hash_bytes(hash, "", 1);
    }

    hash = mlv_hash_bytes(hash, &filter->strength, sizeof(filter->strength));
    hash = mlv_hash_bytes(hash, &filter->filter_option, sizeof(filter->filter_option));
    hash = mlv_hash_bytes(hash, filter->processed, sizeof(filter->processed));
    hash = mlv_hash_bytes(hash, filter->original, sizeof(filter->original));
    return hash;
}

static uint64_t mlv_hash_lut(uint64_t hash, const lut_t * lut)
{
    if (!lut)
    {
        return mlv_hash_bytes(hash, "", 1);
    }

    hash = mlv_hash_bytes(hash, lut->title, sizeof(lut->title));
    hash = mlv_hash_bytes(hash, &lut->dimension, sizeof(lut->dimension));
    hash = mlv_hash_bytes(hash, lut->domain_min, sizeof(lut->domain_min));
    hash = mlv_hash_bytes(hash, lut->domain_max, sizeof(lut->domain_max));
    hash = mlv_hash_bytes(hash, &lut->is3d, sizeof(lut->is3d));
    hash = mlv_hash_bytes(hash, &lut->intensity, sizeof(lut->intensity));

    if (lut->cube && lut->dimension)
    {
        uint64_t cube_entries = lut->is3d
            ? (uint64_t)lut->dimension * lut->dimension * lut->dimension * 3
            : (uint64_t)lut->dimension * 3;
        if (cube_entries <= (uint64_t)SIZE_MAX / sizeof(float))
        {
            hash = mlv_hash_sampled_bytes(hash, lut->cube, (size_t)cube_entries * sizeof(float));
        }
    }

    return hash;
}

static uint64_t mlv_hash_llrawproc_state(uint64_t hash, const llrawprocObject_t * llrawproc)
{
    if (!llrawproc)
    {
        return mlv_hash_bytes(hash, "", 1);
    }

    hash = mlv_hash_bytes(hash, &llrawproc->fix_raw, sizeof(llrawproc->fix_raw));
    hash = mlv_hash_bytes(hash, &llrawproc->vertical_stripes, sizeof(llrawproc->vertical_stripes));
    hash = mlv_hash_bytes(hash, &llrawproc->compute_stripes, sizeof(llrawproc->compute_stripes));
    hash = mlv_hash_bytes(hash, &llrawproc->focus_pixels, sizeof(llrawproc->focus_pixels));
    hash = mlv_hash_bytes(hash, &llrawproc->fpi_method, sizeof(llrawproc->fpi_method));
    hash = mlv_hash_bytes(hash, &llrawproc->fpm_status, sizeof(llrawproc->fpm_status));
    hash = mlv_hash_bytes(hash, &llrawproc->bad_pixels, sizeof(llrawproc->bad_pixels));
    hash = mlv_hash_bytes(hash, &llrawproc->bps_method, sizeof(llrawproc->bps_method));
    hash = mlv_hash_bytes(hash, &llrawproc->bpi_method, sizeof(llrawproc->bpi_method));
    hash = mlv_hash_bytes(hash, &llrawproc->bpm_status, sizeof(llrawproc->bpm_status));
    hash = mlv_hash_bytes(hash, &llrawproc->chroma_smooth, sizeof(llrawproc->chroma_smooth));
    hash = mlv_hash_bytes(hash, &llrawproc->pattern_noise, sizeof(llrawproc->pattern_noise));
    hash = mlv_hash_bytes(hash, &llrawproc->deflicker_target, sizeof(llrawproc->deflicker_target));
    hash = mlv_hash_bytes(hash, &llrawproc->diso_validity, sizeof(llrawproc->diso_validity));
    hash = mlv_hash_bytes(hash, &llrawproc->dual_iso, sizeof(llrawproc->dual_iso));
    hash = mlv_hash_bytes(hash, &llrawproc->diso1, sizeof(llrawproc->diso1));
    hash = mlv_hash_bytes(hash, &llrawproc->diso2, sizeof(llrawproc->diso2));
    hash = mlv_hash_bytes(hash, &llrawproc->diso_auto_correction, sizeof(llrawproc->diso_auto_correction));
    hash = mlv_hash_bytes(hash, &llrawproc->diso_averaging, sizeof(llrawproc->diso_averaging));
    /* Mean23 playback override is part of the steady-state cache key: the
     * same frame index produces different pixels with override on (mean23)
     * vs off (AMaZE), so the processed-frame cache must keep both alive
     * across a paused -> playing transition. See note at dualiso.c on the
     * playback policy that flips this field on/off. */
    hash = mlv_hash_bytes(hash, &llrawproc->diso_playback_force_mean23, sizeof(llrawproc->diso_playback_force_mean23));
    hash = mlv_hash_bytes(hash, &llrawproc->diso_alias_map, sizeof(llrawproc->diso_alias_map));
    hash = mlv_hash_bytes(hash, &llrawproc->diso_frblending, sizeof(llrawproc->diso_frblending));
    /* Phase E5 scale-aware downgrade overrides: same cache-key principle
     * as the mean23 override above. With override on (alias_map / FR
     * blending suppressed) the recon produces different pixels than with
     * override off, so the processed-frame cache must keep both alive
     * across a paused -> playing transition. */
    hash = mlv_hash_bytes(hash, &llrawproc->diso_playback_force_disable_alias_map, sizeof(llrawproc->diso_playback_force_disable_alias_map));
    hash = mlv_hash_bytes(hash, &llrawproc->diso_playback_force_disable_fr_blending, sizeof(llrawproc->diso_playback_force_disable_fr_blending));
    hash = mlv_hash_bytes(hash, &llrawproc->dark_frame, sizeof(llrawproc->dark_frame));
    /* Phase 2C: diso_pattern, diso_ev_correction, diso_black_delta and the
     * dng_* fields are auto-published per frame by Dual ISO recon (see
     * src/mlv/llrawproc/llrawproc.c:llrawproc_publish_worker_results) and
     * therefore drift across consecutive frames in steady-state playback
     * (e.g. patt 0 -> -4 and dng_white_level 2840 -> 12688 between the
     * first and second frame on the large_dual_iso fixture).
     *
     * Including them in the cache state hash forces every frame's
     * note_request to bump the prefetch generation counter, which
     * silently invalidates the processed8 8-slot cache. The render thread
     * then re-runs the full pipeline every frame.
     *
     * User-driven changes to any of these fields (manual ev/black-delta
     * sliders, pattern combobox, etc.) flow through resetMlvCache() in
     * the GUI handlers (platform/qt/MainWindow.cpp:8482-8531, 11082) which
     * calls invalidateMlvProcessedPreviewCache() and zeros all 8 cache
     * slots. Cache invalidation on user intent is preserved without
     * relying on the hash. Mode (auto vs manual) stays in the hash via
     * diso_auto_correction (above).
     *
     * See .claude-state/profiling/20260424-phase2c-prefetch-fix/ for
     * diagnostic logs identifying the drift fields. */
    hash = mlv_hash_c_string(hash, llrawproc->dark_frame_filename);
    hash = mlv_hash_bytes(hash, &llrawproc->dark_frame_hdr, sizeof(llrawproc->dark_frame_hdr));
    hash = mlv_hash_bytes(hash, &llrawproc->dark_frame_size, sizeof(llrawproc->dark_frame_size));
    if (llrawproc->dark_frame_data && llrawproc->dark_frame_size)
    {
        hash = mlv_hash_bytes(hash, llrawproc->dark_frame_data, llrawproc->dark_frame_size);
    }
    hash = mlv_hash_pixel_map(hash, &llrawproc->focus_pixel_map);
    hash = mlv_hash_pixel_map(hash, &llrawproc->bad_pixel_map);
    return hash;
}

/* Phase 4A: validate the requested playback scale factor. Accepts 1, 2, or
 * 4; everything else is clamped to 1. Centralised so cache-key, slot
 * match, and the dimensions helper all agree on what counts as valid. */
static int mlv_normalize_playback_scale_factor(int scaleFactor)
{
    if (scaleFactor == 2 || scaleFactor == 4)
    {
        return scaleFactor;
    }
    return 1;
}

/* Phase 4B: resolve the effective scale factor for a given video object.
 * Forces scale=4 when dual ISO is active (the dual-ISO recon relies on
 * the 4-row iso_patterns cycle, so half-rate sampling at scale=2 would
 * silently corrupt the recon). Also rejects scales that don't divide the
 * sensor dimensions evenly (we need clean 2x2 / 4x4 block alignment). */
static int mlv_effective_playback_scale_factor(mlvObject_t * video, int requestedScale)
{
    int s = mlv_normalize_playback_scale_factor(requestedScale);
    if (s == 1) return 1;

    if (!video) return 1;

    const int width = (int)getMlvWidth(video);
    const int height = (int)getMlvHeight(video);
    if (width <= 0 || height <= 0) return 1;

    /* Dual ISO HQ recon: scale must be 4 (or 1). The 4-row iso_patterns
     * cycle isn't preserved at scale=2. */
    if (llrpHQDualIso(video) && s == 2)
    {
        s = 4;
    }

    /* Block alignment: width and height must be a multiple of (s*1) for
     * the 2x kernel and (s) for the 4x kernel. To be safe we require both
     * to be a multiple of `s`. */
    if ((width % s) != 0 || (height % s) != 0)
    {
        return 1;
    }

    return s;
}

static uint64_t mlv_processed_frame_state_signature_with_scale(mlvObject_t * video,
                                                               int scaleFactor);

static uint64_t mlv_processed_frame_state_signature(mlvObject_t * video)
{
    return mlv_processed_frame_state_signature_with_scale(video, 1);
}

static uint64_t mlv_processed_frame_state_signature_with_scale(mlvObject_t * video,
                                                               int scaleFactor)
{
    uint64_t hash = MLV_FNV1A_OFFSET_BASIS;
    processingObject_t * processing = video ? video->processing : NULL;

    /* Phase 4A: hash the normalised scale factor so a scale=1 entry never
     * collides with a scale=2 lookup once Phase 4B starts producing scaled
     * output. Today the pipeline ignores it, but the cache key already
     * distinguishes them. */
    const int normalizedScale = mlv_normalize_playback_scale_factor(scaleFactor);
    hash = mlv_hash_bytes(hash, &normalizedScale, sizeof(normalizedScale));

    if (!video)
    {
        return hash;
    }

    hash = mlv_hash_bytes(hash, &video->use_amaze, sizeof(video->use_amaze));
    hash = mlv_hash_bytes(hash, &video->ca_red, sizeof(video->ca_red));
    hash = mlv_hash_bytes(hash, &video->ca_blue, sizeof(video->ca_blue));
    hash = mlv_hash_bytes(hash, &video->RAWI.raw_info.black_level, sizeof(video->RAWI.raw_info.black_level));
    hash = mlv_hash_bytes(hash, &video->RAWI.raw_info.white_level, sizeof(video->RAWI.raw_info.white_level));
    hash = mlv_hash_llrawproc_state(hash, video->llrawproc);

    if (!processing)
    {
        return hash;
    }

    hash = mlv_hash_bytes(hash, &processing->exr_mode, sizeof(processing->exr_mode));
    hash = mlv_hash_bytes(hash, &processing->AgX, sizeof(processing->AgX));
    hash = mlv_hash_bytes(hash, &processing->filter_on, sizeof(processing->filter_on));
    hash = mlv_hash_filter_object(hash, processing->filter);
    hash = mlv_hash_bytes(hash, &processing->lut_on, sizeof(processing->lut_on));
    hash = mlv_hash_lut(hash, processing->lut);
    hash = mlv_hash_bytes(hash, &processing->wbFindActive, sizeof(processing->wbFindActive));
    hash = mlv_hash_bytes(hash, &processing->wbR, sizeof(processing->wbR));
    hash = mlv_hash_bytes(hash, &processing->wbG, sizeof(processing->wbG));
    hash = mlv_hash_bytes(hash, &processing->wbB, sizeof(processing->wbB));
    if (processing->image_profile)
    {
        hash = mlv_hash_bytes(hash, &processing->image_profile->gamma_power, sizeof(processing->image_profile->gamma_power));
        hash = mlv_hash_bytes(hash, &processing->image_profile->tonemap_function, sizeof(processing->image_profile->tonemap_function));
        hash = mlv_hash_bytes(hash, &processing->image_profile->allow_creative_adjustments, sizeof(processing->image_profile->allow_creative_adjustments));
        hash = mlv_hash_bytes(hash, &processing->image_profile->colour_gamut, sizeof(processing->image_profile->colour_gamut));
        hash = mlv_hash_c_string(hash, processing->image_profile->transfer_function);
    }
    else
    {
        hash = mlv_hash_bytes(hash, "", 1);
    }
    hash = mlv_hash_bytes(hash, &processing->black_level, sizeof(processing->black_level));
    hash = mlv_hash_bytes(hash, &processing->white_level, sizeof(processing->white_level));
    hash = mlv_hash_bytes(hash, &processing->highlight_reconstruction, sizeof(processing->highlight_reconstruction));
    hash = mlv_hash_bytes(hash, &processing->highest_green, sizeof(processing->highest_green));
    hash = mlv_hash_bytes(hash, &processing->highest_green_gradient, sizeof(processing->highest_green_gradient));
    hash = mlv_hash_bytes(hash, &processing->highest_green_diso, sizeof(processing->highest_green_diso));
    hash = mlv_hash_bytes(hash, &processing->highest_green_gradient_diso, sizeof(processing->highest_green_gradient_diso));
    hash = mlv_hash_bytes(hash, processing->gcurve_y, sizeof(processing->gcurve_y));
    hash = mlv_hash_bytes(hash, processing->gcurve_r, sizeof(processing->gcurve_r));
    hash = mlv_hash_bytes(hash, processing->gcurve_g, sizeof(processing->gcurve_g));
    hash = mlv_hash_bytes(hash, processing->gcurve_b, sizeof(processing->gcurve_b));
    hash = mlv_hash_bytes(hash, processing->hue_vs_hue, sizeof(processing->hue_vs_hue));
    hash = mlv_hash_bytes(hash, processing->hue_vs_saturation, sizeof(processing->hue_vs_saturation));
    hash = mlv_hash_bytes(hash, processing->hue_vs_luma, sizeof(processing->hue_vs_luma));
    hash = mlv_hash_bytes(hash, processing->luma_vs_saturation, sizeof(processing->luma_vs_saturation));
    hash = mlv_hash_bytes(hash, &processing->hue_vs_hue_used, sizeof(processing->hue_vs_hue_used));
    hash = mlv_hash_bytes(hash, &processing->hue_vs_saturation_used, sizeof(processing->hue_vs_saturation_used));
    hash = mlv_hash_bytes(hash, &processing->hue_vs_luma_used, sizeof(processing->hue_vs_luma_used));
    hash = mlv_hash_bytes(hash, &processing->luma_vs_saturation_used, sizeof(processing->luma_vs_saturation_used));
    hash = mlv_hash_bytes(hash, &processing->toning_dry, sizeof(processing->toning_dry));
    hash = mlv_hash_bytes(hash, processing->toning_wet, sizeof(processing->toning_wet));
    hash = mlv_hash_bytes(hash, processing->cam_matrix, sizeof(processing->cam_matrix));
    hash = mlv_hash_bytes(hash, processing->cam_matrix_A, sizeof(processing->cam_matrix_A));
    hash = mlv_hash_bytes(hash, processing->proper_wb_matrix, sizeof(processing->proper_wb_matrix));
    hash = mlv_hash_bytes(hash, processing->final_matrix, sizeof(processing->final_matrix));
    hash = mlv_hash_bytes(hash, &processing->cs_zone.use_cs, sizeof(processing->cs_zone.use_cs));
    hash = mlv_hash_bytes(hash, &processing->cs_zone.chroma_blur_radius, sizeof(processing->cs_zone.chroma_blur_radius));
    hash = mlv_hash_bytes(hash, &processing->shadows_highlights.highlights, sizeof(processing->shadows_highlights.highlights));
    hash = mlv_hash_bytes(hash, &processing->shadows_highlights.shadows, sizeof(processing->shadows_highlights.shadows));
    hash = mlv_hash_bytes(hash, &processing->kelvin, sizeof(processing->kelvin));
    hash = mlv_hash_bytes(hash, &processing->wb_tint, sizeof(processing->wb_tint));
    hash = mlv_hash_bytes(hash, &processing->exposure_stops, sizeof(processing->exposure_stops));
    hash = mlv_hash_bytes(hash, &processing->saturation, sizeof(processing->saturation));
    hash = mlv_hash_bytes(hash, &processing->vibrance, sizeof(processing->vibrance));
    hash = mlv_hash_bytes(hash, &processing->contrast, sizeof(processing->contrast));
    hash = mlv_hash_bytes(hash, &processing->pivot, sizeof(processing->pivot));
    hash = mlv_hash_bytes(hash, &processing->clarity, sizeof(processing->clarity));
    hash = mlv_hash_bytes(hash, &processing->light_contrast_factor, sizeof(processing->light_contrast_factor));
    hash = mlv_hash_bytes(hash, &processing->light_contrast_range, sizeof(processing->light_contrast_range));
    hash = mlv_hash_bytes(hash, &processing->dark_contrast_factor, sizeof(processing->dark_contrast_factor));
    hash = mlv_hash_bytes(hash, &processing->dark_contrast_range, sizeof(processing->dark_contrast_range));
    hash = mlv_hash_bytes(hash, &processing->highlight_hue, sizeof(processing->highlight_hue));
    hash = mlv_hash_bytes(hash, &processing->midtone_hue, sizeof(processing->midtone_hue));
    hash = mlv_hash_bytes(hash, &processing->shadow_hue, sizeof(processing->shadow_hue));
    hash = mlv_hash_bytes(hash, &processing->highlight_sat, sizeof(processing->highlight_sat));
    hash = mlv_hash_bytes(hash, &processing->midtone_sat, sizeof(processing->midtone_sat));
    hash = mlv_hash_bytes(hash, &processing->shadow_sat, sizeof(processing->shadow_sat));
    hash = mlv_hash_bytes(hash, &processing->gamma_power, sizeof(processing->gamma_power));
    hash = mlv_hash_bytes(hash, &processing->lighten, sizeof(processing->lighten));
    hash = mlv_hash_bytes(hash, &processing->sharpen, sizeof(processing->sharpen));
    hash = mlv_hash_bytes(hash, &processing->sharpen_bias, sizeof(processing->sharpen_bias));
    hash = mlv_hash_bytes(hash, &processing->sh_masking, sizeof(processing->sh_masking));
    hash = mlv_hash_bytes(hash, processing->wb_multipliers, sizeof(processing->wb_multipliers));
    hash = mlv_hash_bytes(hash, &processing->transformation, sizeof(processing->transformation));
    if (processing->dual_iso)
    {
        hash = mlv_hash_bytes(hash, processing->dual_iso, sizeof(*processing->dual_iso));
    }
    else
    {
        hash = mlv_hash_bytes(hash, "", 1);
    }
    hash = mlv_hash_bytes(hash, &processing->denoiserWindow, sizeof(processing->denoiserWindow));
    hash = mlv_hash_bytes(hash, &processing->denoiserStrength, sizeof(processing->denoiserStrength));
    hash = mlv_hash_bytes(hash, &processing->rbfDenoiserLuma, sizeof(processing->rbfDenoiserLuma));
    hash = mlv_hash_bytes(hash, &processing->rbfDenoiserChroma, sizeof(processing->rbfDenoiserChroma));
    hash = mlv_hash_bytes(hash, &processing->rbfDenoiserRange, sizeof(processing->rbfDenoiserRange));
    hash = mlv_hash_bytes(hash, &processing->grainStrength, sizeof(processing->grainStrength));
    hash = mlv_hash_bytes(hash, &processing->grainLumaWeight, sizeof(processing->grainLumaWeight));
    hash = mlv_hash_bytes(hash, &processing->gradient_exposure_stops, sizeof(processing->gradient_exposure_stops));
    hash = mlv_hash_bytes(hash, &processing->gradient_contrast, sizeof(processing->gradient_contrast));
    hash = mlv_hash_bytes(hash, &processing->gradient_enable, sizeof(processing->gradient_enable));
    if (processing->gradient_mask)
    {
        size_t mask_pixels = (size_t)getMlvWidth(video) * (size_t)getMlvHeight(video);
        hash = mlv_hash_sampled_bytes(hash, processing->gradient_mask, mask_pixels * sizeof(uint16_t));
    }
    else
    {
        hash = mlv_hash_bytes(hash, "", 1);
    }
    hash = mlv_hash_bytes(hash, &processing->vignette_strength, sizeof(processing->vignette_strength));
    if (processing->vignette_mask)
    {
        size_t mask_pixels = (size_t)getMlvWidth(video) * (size_t)getMlvHeight(video);
        hash = mlv_hash_sampled_bytes(hash, processing->vignette_mask, mask_pixels * sizeof(float));
    }
    else
    {
        hash = mlv_hash_bytes(hash, "", 1);
    }
    hash = mlv_hash_bytes(hash, &processing->use_cam_matrix, sizeof(processing->use_cam_matrix));
    hash = mlv_hash_bytes(hash, &processing->colour_gamut, sizeof(processing->colour_gamut));
    hash = mlv_hash_bytes(hash, &processing->tonemap_function, sizeof(processing->tonemap_function));
    hash = mlv_hash_bytes(hash, &processing->colour_space_tag, sizeof(processing->colour_space_tag));
    hash = mlv_hash_bytes(hash, &processing->allow_creative_adjustments, sizeof(processing->allow_creative_adjustments));
    hash = mlv_hash_bytes(hash, &processing->ca_desaturate, sizeof(processing->ca_desaturate));
    hash = mlv_hash_bytes(hash, &processing->ca_radius, sizeof(processing->ca_radius));
    hash = mlv_hash_bytes(hash, &processing->transfer_split, sizeof(processing->transfer_split));
    hash = mlv_hash_bytes(hash, &processing->transfer_split_value, sizeof(processing->transfer_split_value));
    hash = mlv_hash_c_string(hash, processing->transfer_function_string);
    hash = mlv_hash_c_string(hash, processing->transfer_function_string_formatted);

    return hash;
}

static uint64_t mlv_processed_frame_signature_from_state(uint64_t stateSignature,
                                                         uint64_t frameIndex)
{
    return mlv_hash_bytes(stateSignature, &frameIndex, sizeof(frameIndex));
}

static uint64_t mlv_processed_frame_signature(mlvObject_t * video, uint64_t frameIndex)
{
    return mlv_processed_frame_signature_from_state(
        mlv_processed_frame_state_signature(video),
        frameIndex);
}

/* Phase 4A: scale-aware variant. Always reachable from Scaled* entry
 * points; the existing scale-1 callers continue to use the non-scaled
 * helpers above and stay byte-identical with their previous behaviour. */
static uint64_t mlv_processed_frame_signature_with_scale(mlvObject_t * video,
                                                         uint64_t frameIndex,
                                                         int scaleFactor)
{
    return mlv_processed_frame_signature_from_state(
        mlv_processed_frame_state_signature_with_scale(video, scaleFactor),
        frameIndex);
}

static int mlv_ensure_reusable_buffer(void ** buffer,
                                      uint64_t * capacity_elements,
                                      uint64_t required_elements,
                                      size_t element_size)
{
    if (required_elements == 0)
    {
        return 1;
    }

    if (*buffer && (*capacity_elements >= required_elements))
    {
        return 1;
    }

    if (required_elements > ((uint64_t)SIZE_MAX / element_size))
    {
        return 0;
    }

    void * resized = realloc(*buffer, (size_t)required_elements * element_size);
    if (!resized)
    {
        return 0;
    }

    *buffer = resized;
    *capacity_elements = required_elements;
    return 1;
}

static uint16_t * mlv_ensure_u16_buffer(uint16_t ** buffer, uint64_t * capacity_words, uint64_t required_words)
{
    if (!mlv_ensure_reusable_buffer((void **)buffer, capacity_words, required_words, sizeof(uint16_t)))
    {
        return NULL;
    }
    return *buffer;
}

static uint16_t * mlv_ensure_thread_u16_buffer(uint64_t required_words)
{
    static MLV_THREAD_LOCAL uint16_t * tls_buffer = NULL;
    static MLV_THREAD_LOCAL uint64_t tls_capacity_words = 0;
    return mlv_ensure_u16_buffer(&tls_buffer, &tls_capacity_words, required_words);
}

static uint16_t * mlv_ensure_thread_rgb_u16_buffer(uint64_t required_words)
{
    static MLV_THREAD_LOCAL uint16_t * tls_buffer = NULL;
    static MLV_THREAD_LOCAL uint64_t tls_capacity_words = 0;
    return mlv_ensure_u16_buffer(&tls_buffer, &tls_capacity_words, required_words);
}

static uint8_t * mlv_ensure_u8_buffer(uint8_t ** buffer, uint64_t * capacity_bytes, uint64_t required_bytes)
{
    if (!mlv_ensure_reusable_buffer((void **)buffer, capacity_bytes, required_bytes, sizeof(uint8_t)))
    {
        return NULL;
    }
    return *buffer;
}

static uint8_t * mlv_ensure_thread_u8_buffer(uint64_t required_bytes)
{
    static MLV_THREAD_LOCAL uint8_t * tls_buffer = NULL;
    static MLV_THREAD_LOCAL uint64_t tls_capacity_bytes = 0;
    return mlv_ensure_u8_buffer(&tls_buffer, &tls_capacity_bytes, required_bytes);
}

static int getMlvRawFrameUint16Direct(mlvObject_t * video, uint64_t frameIndex, uint16_t * unpackedFrame);

static void mlv_reset_raw_uint16_prefetch_locked(mlvObject_t * video)
{
    video->raw_uint16_prefetch_request_pending = 0;
    video->raw_uint16_prefetch_worker_busy = 0;
    memset(video->raw_uint16_prefetch_slot_state, 0, sizeof(video->raw_uint16_prefetch_slot_state));
    memset(video->raw_uint16_prefetch_slot_frame, 0, sizeof(video->raw_uint16_prefetch_slot_frame));
    memset(video->raw_uint16_prefetch_slot_generation, 0, sizeof(video->raw_uint16_prefetch_slot_generation));
    video->raw_uint16_prefetch_next_slot = 0;
}

static uint16_t * mlv_raw_uint16_prefetch_slot_ptr(mlvObject_t * video, uint32_t slot)
{
    if (!video->raw_uint16_prefetch_cache
        || slot >= MLV_RAW_UINT16_PREFETCH_SLOTS
        || video->raw_uint16_prefetch_slot_words == 0)
    {
        return NULL;
    }

    uint64_t offset = (uint64_t)slot * video->raw_uint16_prefetch_slot_words;
    if (offset + video->raw_uint16_prefetch_slot_words > video->raw_uint16_prefetch_cache_words)
    {
        return NULL;
    }

    return video->raw_uint16_prefetch_cache + offset;
}

static int mlv_ensure_raw_uint16_prefetch_storage(mlvObject_t * video)
{
    uint64_t frame_words = (uint64_t)getMlvWidth(video) * getMlvHeight(video);
    uint64_t total_words = frame_words * MLV_RAW_UINT16_PREFETCH_SLOTS;
    if (frame_words == 0 || (frame_words != 0 && total_words / frame_words != MLV_RAW_UINT16_PREFETCH_SLOTS))
    {
        return 0;
    }

    uint64_t previous_capacity = video->raw_uint16_prefetch_cache_words;
    uint16_t * cache = mlv_ensure_u16_buffer(&video->raw_uint16_prefetch_cache,
                                             &video->raw_uint16_prefetch_cache_words,
                                             total_words);
    if (!cache)
    {
        return 0;
    }

    if (previous_capacity != video->raw_uint16_prefetch_cache_words
        || video->raw_uint16_prefetch_slot_words != frame_words)
    {
        video->raw_uint16_prefetch_slot_words = frame_words;
        mlv_reset_raw_uint16_prefetch_locked(video);
    }

    return 1;
}

static int mlv_raw_uint16_prefetch_find_slot_locked(mlvObject_t * video, uint64_t frameIndex)
{
    for (uint32_t slot = 0; slot < MLV_RAW_UINT16_PREFETCH_SLOTS; ++slot)
    {
        if (video->raw_uint16_prefetch_slot_state[slot] == MLV_RAW_UINT16_PREFETCH_READY
            && video->raw_uint16_prefetch_slot_frame[slot] == frameIndex
            && video->raw_uint16_prefetch_slot_generation[slot] == video->raw_uint16_prefetch_generation)
        {
            return (int)slot;
        }
    }

    return -1;
}

static void mlv_raw_uint16_prefetch_store_frame(mlvObject_t * video,
                                                uint64_t frameIndex,
                                                const uint16_t * frameData)
{
    pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
    if (!mlv_ensure_raw_uint16_prefetch_storage(video))
    {
        pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
        return;
    }

    int slot = mlv_raw_uint16_prefetch_find_slot_locked(video, frameIndex);
    if (slot < 0)
    {
        slot = (int)video->raw_uint16_prefetch_next_slot;
        video->raw_uint16_prefetch_next_slot =
            (video->raw_uint16_prefetch_next_slot + 1) % MLV_RAW_UINT16_PREFETCH_SLOTS;
    }

    uint16_t * slotBuffer = mlv_raw_uint16_prefetch_slot_ptr(video, (uint32_t)slot);
    if (!slotBuffer)
    {
        pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
        return;
    }

    memcpy(slotBuffer,
           frameData,
           (size_t)video->raw_uint16_prefetch_slot_words * sizeof(uint16_t));
    video->raw_uint16_prefetch_slot_state[slot] = MLV_RAW_UINT16_PREFETCH_READY;
    video->raw_uint16_prefetch_slot_frame[slot] = frameIndex;
    video->raw_uint16_prefetch_slot_generation[slot] = video->raw_uint16_prefetch_generation;
    pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
}

static int mlv_raw_uint16_prefetch_try_copy(mlvObject_t * video,
                                            uint64_t frameIndex,
                                            uint16_t * unpackedFrame)
{
    int hit = 0;
    pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
    int slot = mlv_raw_uint16_prefetch_find_slot_locked(video, frameIndex);
    if (slot >= 0)
    {
        uint16_t * slotBuffer = mlv_raw_uint16_prefetch_slot_ptr(video, (uint32_t)slot);
        if (slotBuffer)
        {
            memcpy(unpackedFrame,
                   slotBuffer,
                   (size_t)video->raw_uint16_prefetch_slot_words * sizeof(uint16_t));
            hit = 1;
        }
    }
    pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
    return hit;
}

static int mlv_start_raw_uint16_prefetch_thread(mlvObject_t * video);

static void mlv_raw_uint16_prefetch_note_request(mlvObject_t * video, uint64_t frameIndex)
{
    pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);

    if (!video->raw_uint16_prefetch_thread_started)
    {
        pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
        if (!mlv_start_raw_uint16_prefetch_thread(video))
        {
            return;
        }
        pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
    }

    if (video->raw_uint16_prefetch_last_request_frame + 1 != frameIndex
        && video->raw_uint16_prefetch_last_request_frame != frameIndex)
    {
        ++video->raw_uint16_prefetch_generation;
        mlv_reset_raw_uint16_prefetch_locked(video);
    }

    video->raw_uint16_prefetch_last_request_frame = frameIndex;
    video->raw_uint16_prefetch_request_frame = frameIndex;
    video->raw_uint16_prefetch_request_pending = 1;
    pthread_cond_signal(&video->raw_uint16_prefetch_cond);
    pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
}

static void * mlv_raw_uint16_prefetch_thread_main(void * opaque)
{
    mlvObject_t * video = (mlvObject_t *)opaque;

    while (1)
    {
        pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
        while (!video->raw_uint16_prefetch_stop
               && !video->raw_uint16_prefetch_request_pending)
        {
            pthread_cond_wait(&video->raw_uint16_prefetch_cond, &video->raw_uint16_prefetch_mutex);
        }

        if (video->raw_uint16_prefetch_stop)
        {
            pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
            break;
        }

        if (!mlv_ensure_raw_uint16_prefetch_storage(video))
        {
            video->raw_uint16_prefetch_request_pending = 0;
            pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
            continue;
        }

        uint64_t baseFrame = video->raw_uint16_prefetch_request_frame;
        uint32_t generation = video->raw_uint16_prefetch_generation;
        video->raw_uint16_prefetch_request_pending = 0;
        video->raw_uint16_prefetch_worker_busy = 1;
        pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);

        for (uint32_t offset = 1; offset <= MLV_RAW_UINT16_PREFETCH_LOOKAHEAD; ++offset)
        {
            uint64_t targetFrame = baseFrame + offset;
            if (targetFrame >= getMlvFrames(video))
            {
                break;
            }

            pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
            if (video->raw_uint16_prefetch_stop
                || generation != video->raw_uint16_prefetch_generation)
            {
                pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
                break;
            }

            if (mlv_raw_uint16_prefetch_find_slot_locked(video, targetFrame) >= 0)
            {
                pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
                continue;
            }

            uint32_t slot = video->raw_uint16_prefetch_next_slot;
            video->raw_uint16_prefetch_next_slot =
                (video->raw_uint16_prefetch_next_slot + 1) % MLV_RAW_UINT16_PREFETCH_SLOTS;
            uint16_t * slotBuffer = mlv_raw_uint16_prefetch_slot_ptr(video, slot);
            if (!slotBuffer)
            {
                pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
                continue;
            }

            video->raw_uint16_prefetch_slot_state[slot] = MLV_RAW_UINT16_PREFETCH_DECODING;
            video->raw_uint16_prefetch_slot_frame[slot] = targetFrame;
            video->raw_uint16_prefetch_slot_generation[slot] = generation;
            pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);

            int decodeOk = (getMlvRawFrameUint16Direct(video, targetFrame, slotBuffer) == 0);

            pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
            if (!decodeOk)
            {
                ++video->raw_uint16_prefetch_decode_failures;
            }
            if (slot < MLV_RAW_UINT16_PREFETCH_SLOTS
                && video->raw_uint16_prefetch_slot_frame[slot] == targetFrame
                && video->raw_uint16_prefetch_slot_generation[slot] == generation)
            {
                video->raw_uint16_prefetch_slot_state[slot] =
                    (decodeOk && !video->raw_uint16_prefetch_stop
                     && generation == video->raw_uint16_prefetch_generation)
                    ? MLV_RAW_UINT16_PREFETCH_READY
                    : MLV_RAW_UINT16_PREFETCH_EMPTY;
            }
            pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
        }

        pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
        video->raw_uint16_prefetch_worker_busy = 0;
        pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
    }

    return NULL;
}

static int mlv_start_raw_uint16_prefetch_thread(mlvObject_t * video)
{
    pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
    if (video->raw_uint16_prefetch_thread_started)
    {
        pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
        return 1;
    }

    /* Hold the mutex across pthread_create so the started flag only publishes
     * after the thread actually exists; otherwise a concurrent note_request
     * could signal a cond for a thread that failed to spawn. */
    int create_rc = pthread_create(&video->raw_uint16_prefetch_thread,
                                   NULL,
                                   mlv_raw_uint16_prefetch_thread_main,
                                   video);
    if (create_rc == 0)
    {
        video->raw_uint16_prefetch_thread_started = 1;
    }
    pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
    return create_rc == 0 ? 1 : 0;
}

static void mlv_reset_processed_frame_16bit_cache(mlvObject_t * video)
{
    video->processed_16bit_cache_next_slot = 0;
    memset(video->processed_16bit_cache_active, 0, sizeof(video->processed_16bit_cache_active));
    memset(video->processed_16bit_cache_frame, 0, sizeof(video->processed_16bit_cache_frame));
    memset(video->processed_16bit_cache_threads, 0, sizeof(video->processed_16bit_cache_threads));
    memset(video->processed_16bit_cache_signature, 0, sizeof(video->processed_16bit_cache_signature));
    /* Phase 4A: scale lane resets to 0 (the "unset" sentinel). Real entries
     * always store a normalised 1, 2, or 4 so a zero lane never matches
     * any live request. */
    memset(video->processed_16bit_cache_scale, 0, sizeof(video->processed_16bit_cache_scale));
    /* Phase 4B: clear the layout unit so the next prepare picks up the
     * new layout fresh. */
    video->processed_16bit_cache_unit_words = 0;
}

static uint16_t * mlv_processed_frame_16bit_cache_slot(mlvObject_t * video, uint32_t slot, uint64_t rgb_frame_words)
{
    if (!video->rgb_processed_frame_cache_16bit
        || slot >= MLV_PROCESSED_16BIT_CACHE_SLOTS)
    {
        return NULL;
    }

    uint64_t offset = (uint64_t)slot * rgb_frame_words;
    if (rgb_frame_words != 0 && offset / rgb_frame_words != slot)
    {
        return NULL;
    }
    if (video->rgb_processed_frame_cache_16bit_words < offset + rgb_frame_words)
    {
        return NULL;
    }

    return video->rgb_processed_frame_cache_16bit + offset;
}

static int mlv_find_processed_frame_16bit_cache_slot_with_scale(mlvObject_t * video,
                                                                uint64_t frameIndex,
                                                                int threads,
                                                                uint64_t signature,
                                                                int scaleFactor)
{
    const int normalizedScale = mlv_normalize_playback_scale_factor(scaleFactor);
    for (uint32_t slot = 0; slot < MLV_PROCESSED_16BIT_CACHE_SLOTS; ++slot)
    {
        if (video->processed_16bit_cache_active[slot]
            && video->processed_16bit_cache_frame[slot] == frameIndex
            && video->processed_16bit_cache_threads[slot] == threads
            && video->processed_16bit_cache_signature[slot] == signature
            && video->processed_16bit_cache_scale[slot] == normalizedScale)
        {
            return (int)slot;
        }
    }

    return -1;
}

static int mlv_find_processed_frame_16bit_cache_slot(mlvObject_t * video,
                                                     uint64_t frameIndex,
                                                     int threads,
                                                     uint64_t signature)
{
    return mlv_find_processed_frame_16bit_cache_slot_with_scale(video,
                                                                frameIndex,
                                                                threads,
                                                                signature,
                                                                1);
}

static uint16_t * mlv_prepare_processed_frame_16bit_cache(mlvObject_t * video,
                                                          uint64_t rgb_frame_words)
{
    uint64_t total_words = rgb_frame_words * MLV_PROCESSED_16BIT_CACHE_SLOTS;
    if (rgb_frame_words != 0 && total_words / rgb_frame_words != MLV_PROCESSED_16BIT_CACHE_SLOTS)
    {
        mlv_reset_processed_frame_16bit_cache(video);
        return NULL;
    }

    /* Phase 4B: when rgb_frame_words differs from the layout unit used by
     * existing slots, the slot offsets are stale (one scale's slot 1
     * overlaps another scale's slot 0). Reset so the next store
     * re-establishes the layout. */
    if (video->processed_16bit_cache_unit_words != 0
        && video->processed_16bit_cache_unit_words != rgb_frame_words)
    {
        mlv_reset_processed_frame_16bit_cache(video);
    }

    uint64_t previous_capacity = video->rgb_processed_frame_cache_16bit_words;
    uint16_t * cache = mlv_ensure_u16_buffer(&video->rgb_processed_frame_cache_16bit,
                                             &video->rgb_processed_frame_cache_16bit_words,
                                             total_words);
    if (!cache)
    {
        mlv_reset_processed_frame_16bit_cache(video);
        return NULL;
    }

    if (previous_capacity != video->rgb_processed_frame_cache_16bit_words)
    {
        mlv_reset_processed_frame_16bit_cache(video);
    }

    /* Record the layout unit so subsequent calls can detect a size change. */
    video->processed_16bit_cache_unit_words = rgb_frame_words;
    return cache;
}

static void mlv_store_processed_frame_16bit_cache_with_scale(mlvObject_t * video,
                                                             uint64_t frameIndex,
                                                             int threads,
                                                             uint64_t signature,
                                                             const uint16_t * frame_data,
                                                             uint64_t rgb_frame_words,
                                                             int scaleFactor)
{
    uint16_t * cache = mlv_prepare_processed_frame_16bit_cache(video, rgb_frame_words);
    if (!cache)
    {
        return;
    }

    const int normalizedScale = mlv_normalize_playback_scale_factor(scaleFactor);
    int slot = mlv_find_processed_frame_16bit_cache_slot_with_scale(video,
                                                                    frameIndex,
                                                                    threads,
                                                                    signature,
                                                                    normalizedScale);
    if (slot < 0)
    {
        slot = (int)video->processed_16bit_cache_next_slot;
        video->processed_16bit_cache_next_slot = (video->processed_16bit_cache_next_slot + 1) % MLV_PROCESSED_16BIT_CACHE_SLOTS;
    }

    uint16_t * slot_buffer = mlv_processed_frame_16bit_cache_slot(video, (uint32_t)slot, rgb_frame_words);
    if (!slot_buffer)
    {
        mlv_reset_processed_frame_16bit_cache(video);
        return;
    }

    memcpy(slot_buffer, frame_data, (size_t)rgb_frame_words * sizeof(uint16_t));
    video->processed_16bit_cache_active[slot] = 1;
    video->processed_16bit_cache_frame[slot] = frameIndex;
    video->processed_16bit_cache_threads[slot] = threads;
    video->processed_16bit_cache_signature[slot] = signature;
    video->processed_16bit_cache_scale[slot] = normalizedScale;
}

static void mlv_store_processed_frame_16bit_cache(mlvObject_t * video,
                                                  uint64_t frameIndex,
                                                  int threads,
                                                  uint64_t signature,
                                                  const uint16_t * frame_data,
                                                  uint64_t rgb_frame_words)
{
    mlv_store_processed_frame_16bit_cache_with_scale(video,
                                                     frameIndex,
                                                     threads,
                                                     signature,
                                                     frame_data,
                                                     rgb_frame_words,
                                                     1);
}

static void mlv_reset_processed_frame_8bit_cache_locked(mlvObject_t * video)
{
    video->current_processed_frame_8bit_active = 0;
    video->current_processed_frame_8bit_signature = 0;
    video->current_processed_frame_8bit = 0;
    video->current_processed_frame_8bit_threads = 0;
    video->processed_8bit_cache_next_slot = 0;
    memset(video->processed_8bit_cache_active, 0, sizeof(video->processed_8bit_cache_active));
    memset(video->processed_8bit_cache_frame, 0, sizeof(video->processed_8bit_cache_frame));
    memset(video->processed_8bit_cache_threads, 0, sizeof(video->processed_8bit_cache_threads));
    memset(video->processed_8bit_cache_signature, 0, sizeof(video->processed_8bit_cache_signature));
    /* Phase 4A: scale lane resets to 0 (no live request matches a 0 lane). */
    memset(video->processed_8bit_cache_scale, 0, sizeof(video->processed_8bit_cache_scale));
    memset(video->processed_8bit_cache_state, 0, sizeof(video->processed_8bit_cache_state));
    memset(video->processed_8bit_cache_prefetched, 0, sizeof(video->processed_8bit_cache_prefetched));
    memset(video->processed_8bit_cache_generation, 0, sizeof(video->processed_8bit_cache_generation));
    /* Phase 4B: clear the per-call unit size so the next prepare picks up
     * the new layout fresh. */
    video->processed_8bit_cache_unit_size = 0;
}

static void mlv_reset_processed_frame_8bit_cache(mlvObject_t * video)
{
    mlv_reset_processed_frame_8bit_cache_locked(video);
}

static uint8_t * mlv_processed_frame_8bit_cache_slot(mlvObject_t * video, uint32_t slot, uint64_t rgb_frame_size)
{
    if (!video->rgb_processed_current_frame_8bit
        || slot >= MLV_PROCESSED_8BIT_CACHE_SLOTS)
    {
        return NULL;
    }

    uint64_t offset = (uint64_t)slot * rgb_frame_size;
    if (rgb_frame_size != 0 && offset / rgb_frame_size != slot)
    {
        return NULL;
    }
    if (video->rgb_processed_current_frame_8bit_bytes < offset + rgb_frame_size)
    {
        return NULL;
    }

    return video->rgb_processed_current_frame_8bit + offset;
}

static int mlv_processed_frame_8bit_cache_slot_matches_locked_with_scale(
    mlvObject_t * video,
    uint32_t slot,
    uint64_t frameIndex,
    int threads,
    uint64_t signature,
    uint32_t generation,
    int scaleFactor)
{
    const int normalizedScale = mlv_normalize_playback_scale_factor(scaleFactor);
    return slot < MLV_PROCESSED_8BIT_CACHE_SLOTS
        && video->processed_8bit_cache_frame[slot] == frameIndex
        && video->processed_8bit_cache_threads[slot] == threads
        && video->processed_8bit_cache_signature[slot] == signature
        && video->processed_8bit_cache_generation[slot] == generation
        && video->processed_8bit_cache_scale[slot] == normalizedScale;
}

static int mlv_processed_frame_8bit_cache_slot_matches_locked(mlvObject_t * video,
                                                              uint32_t slot,
                                                              uint64_t frameIndex,
                                                              int threads,
                                                              uint64_t signature,
                                                              uint32_t generation)
{
    return mlv_processed_frame_8bit_cache_slot_matches_locked_with_scale(video,
                                                                         slot,
                                                                         frameIndex,
                                                                         threads,
                                                                         signature,
                                                                         generation,
                                                                         1);
}

static int mlv_find_processed_frame_8bit_cache_slot_locked_with_scale(mlvObject_t * video,
                                                                      uint64_t frameIndex,
                                                                      int threads,
                                                                      uint64_t signature,
                                                                      int scaleFactor)
{
    for (uint32_t slot = 0; slot < MLV_PROCESSED_8BIT_CACHE_SLOTS; ++slot)
    {
        if (video->processed_8bit_cache_active[slot]
            && video->processed_8bit_cache_state[slot] == MLV_PROCESSED_8BIT_PREFETCH_READY
            && mlv_processed_frame_8bit_cache_slot_matches_locked_with_scale(
                   video,
                   slot,
                   frameIndex,
                   threads,
                   signature,
                   video->processed8_prefetch_generation,
                   scaleFactor))
        {
            return (int)slot;
        }
    }

    return -1;
}

static int mlv_find_processed_frame_8bit_cache_slot_locked(mlvObject_t * video,
                                                           uint64_t frameIndex,
                                                           int threads,
                                                           uint64_t signature)
{
    return mlv_find_processed_frame_8bit_cache_slot_locked_with_scale(video,
                                                                      frameIndex,
                                                                      threads,
                                                                      signature,
                                                                      1);
}

static int mlv_processed_frame_8bit_cache_contains_locked_with_scale(mlvObject_t * video,
                                                                     uint64_t frameIndex,
                                                                     int threads,
                                                                     uint64_t signature,
                                                                     uint32_t generation,
                                                                     int scaleFactor)
{
    for (uint32_t slot = 0; slot < MLV_PROCESSED_8BIT_CACHE_SLOTS; ++slot)
    {
        if (mlv_processed_frame_8bit_cache_slot_matches_locked_with_scale(video,
                                                                          slot,
                                                                          frameIndex,
                                                                          threads,
                                                                          signature,
                                                                          generation,
                                                                          scaleFactor)
            && (video->processed_8bit_cache_state[slot] == MLV_PROCESSED_8BIT_PREFETCH_READY
                || video->processed_8bit_cache_state[slot] == MLV_PROCESSED_8BIT_PREFETCH_RENDERING))
        {
            return 1;
        }
    }

    return 0;
}

static int mlv_processed_frame_8bit_cache_contains_locked(mlvObject_t * video,
                                                          uint64_t frameIndex,
                                                          int threads,
                                                          uint64_t signature,
                                                          uint32_t generation)
{
    return mlv_processed_frame_8bit_cache_contains_locked_with_scale(video,
                                                                     frameIndex,
                                                                     threads,
                                                                     signature,
                                                                     generation,
                                                                     1);
}

static int mlv_next_processed_frame_8bit_cache_slot_locked(mlvObject_t * video)
{
    for (uint32_t offset = 0; offset < MLV_PROCESSED_8BIT_CACHE_SLOTS; ++offset)
    {
        uint32_t slot = (video->processed_8bit_cache_next_slot + offset) % MLV_PROCESSED_8BIT_CACHE_SLOTS;
        if (video->processed_8bit_cache_state[slot] == MLV_PROCESSED_8BIT_PREFETCH_RENDERING
            && !video->processed_8bit_cache_active[slot]
            && video->processed_8bit_cache_frame[slot] == 0
            && video->processed_8bit_cache_threads[slot] == 0
            && video->processed_8bit_cache_signature[slot] == 0)
        {
            video->processed_8bit_cache_state[slot] = MLV_PROCESSED_8BIT_PREFETCH_EMPTY;
            video->processed_8bit_cache_prefetched[slot] = 0;
            video->processed_8bit_cache_generation[slot] = 0;
        }

        if (video->processed_8bit_cache_state[slot] != MLV_PROCESSED_8BIT_PREFETCH_RENDERING)
        {
            video->processed_8bit_cache_next_slot = (slot + 1) % MLV_PROCESSED_8BIT_CACHE_SLOTS;
            return (int)slot;
        }
    }

    return -1;
}

static uint8_t * mlv_prepare_processed_frame_8bit_cache_locked(mlvObject_t * video,
                                                               uint64_t rgb_frame_size)
{
    uint64_t total_bytes = rgb_frame_size * MLV_PROCESSED_8BIT_CACHE_SLOTS;
    if (rgb_frame_size != 0 && total_bytes / rgb_frame_size != MLV_PROCESSED_8BIT_CACHE_SLOTS)
    {
        mlv_reset_processed_frame_8bit_cache_locked(video);
        return NULL;
    }

    /* Phase 4B: when rgb_frame_size differs from the size used to lay out
     * existing slots (e.g. a scale=1 store after the buffer was laid out
     * for scale=2), the slot offsets are stale and the data overlaps
     * unrelated entries. Reset so the next store re-establishes the
     * layout. */
    if (video->processed_8bit_cache_unit_size != 0
        && video->processed_8bit_cache_unit_size != rgb_frame_size)
    {
        mlv_reset_processed_frame_8bit_cache_locked(video);
    }

    uint64_t previous_capacity = video->rgb_processed_current_frame_8bit_bytes;
    uint8_t * cache = mlv_ensure_u8_buffer(&video->rgb_processed_current_frame_8bit,
                                           &video->rgb_processed_current_frame_8bit_bytes,
                                           total_bytes);
    if (!cache)
    {
        mlv_reset_processed_frame_8bit_cache_locked(video);
        return NULL;
    }

    if (previous_capacity != video->rgb_processed_current_frame_8bit_bytes)
    {
        mlv_reset_processed_frame_8bit_cache_locked(video);
    }

    /* Record the layout unit so subsequent calls can detect a size change. */
    video->processed_8bit_cache_unit_size = rgb_frame_size;
    return cache;
}

static void mlv_store_processed_frame_8bit_cache_locked_with_scale(mlvObject_t * video,
                                                                   uint64_t frameIndex,
                                                                   int threads,
                                                                   uint64_t signature,
                                                                   const uint8_t * frame_data,
                                                                   uint64_t rgb_frame_size,
                                                                   int update_current_entry,
                                                                   int prefetched,
                                                                   int scaleFactor)
{
    uint8_t * cache = mlv_prepare_processed_frame_8bit_cache_locked(video, rgb_frame_size);
    if (!cache)
    {
        return;
    }

    const int normalizedScale = mlv_normalize_playback_scale_factor(scaleFactor);
    int slot = mlv_find_processed_frame_8bit_cache_slot_locked_with_scale(video,
                                                                          frameIndex,
                                                                          threads,
                                                                          signature,
                                                                          normalizedScale);
    if (slot < 0)
    {
        slot = mlv_next_processed_frame_8bit_cache_slot_locked(video);
    }

    if (slot < 0)
    {
        return;
    }

    uint8_t * slot_buffer = mlv_processed_frame_8bit_cache_slot(video, (uint32_t)slot, rgb_frame_size);
    if (!slot_buffer)
    {
        mlv_reset_processed_frame_8bit_cache_locked(video);
        return;
    }

    memcpy(slot_buffer, frame_data, (size_t)rgb_frame_size);
    video->processed_8bit_cache_active[slot] = 1;
    video->processed_8bit_cache_frame[slot] = frameIndex;
    video->processed_8bit_cache_threads[slot] = threads;
    video->processed_8bit_cache_signature[slot] = signature;
    video->processed_8bit_cache_scale[slot] = normalizedScale;
    video->processed_8bit_cache_state[slot] = MLV_PROCESSED_8BIT_PREFETCH_READY;
    video->processed_8bit_cache_prefetched[slot] = prefetched != 0;
    video->processed_8bit_cache_generation[slot] = video->processed8_prefetch_generation;

    if (update_current_entry)
    {
        video->current_processed_frame_8bit_active = 1;
        video->current_processed_frame_8bit = frameIndex;
        video->current_processed_frame_8bit_threads = threads;
        video->current_processed_frame_8bit_signature = signature;
    }
}

static void mlv_store_processed_frame_8bit_cache_locked(mlvObject_t * video,
                                                        uint64_t frameIndex,
                                                        int threads,
                                                        uint64_t signature,
                                                        const uint8_t * frame_data,
                                                        uint64_t rgb_frame_size,
                                                        int update_current_entry,
                                                        int prefetched)
{
    mlv_store_processed_frame_8bit_cache_locked_with_scale(video,
                                                           frameIndex,
                                                           threads,
                                                           signature,
                                                           frame_data,
                                                           rgb_frame_size,
                                                           update_current_entry,
                                                           prefetched,
                                                           1);
}

static void mlv_store_processed_frame_8bit_cache(mlvObject_t * video,
                                                 uint64_t frameIndex,
                                                 int threads,
                                                 uint64_t signature,
                                                 const uint8_t * frame_data,
                                                 uint64_t rgb_frame_size,
                                                 int update_current_entry,
                                                 int prefetched)
{
    pthread_mutex_lock(&video->processed8_prefetch_mutex);
    mlv_store_processed_frame_8bit_cache_locked(video,
                                                frameIndex,
                                                threads,
                                                signature,
                                                frame_data,
                                                rgb_frame_size,
                                                update_current_entry,
                                                prefetched);
    pthread_mutex_unlock(&video->processed8_prefetch_mutex);
}

static void mlv_store_processed_frame_8bit_cache_with_scale(mlvObject_t * video,
                                                            uint64_t frameIndex,
                                                            int threads,
                                                            uint64_t signature,
                                                            const uint8_t * frame_data,
                                                            uint64_t rgb_frame_size,
                                                            int update_current_entry,
                                                            int prefetched,
                                                            int scaleFactor)
{
    pthread_mutex_lock(&video->processed8_prefetch_mutex);
    mlv_store_processed_frame_8bit_cache_locked_with_scale(video,
                                                           frameIndex,
                                                           threads,
                                                           signature,
                                                           frame_data,
                                                           rgb_frame_size,
                                                           update_current_entry,
                                                           prefetched,
                                                           scaleFactor);
    pthread_mutex_unlock(&video->processed8_prefetch_mutex);
}

static void mlv_copy_processed8_prefetch_processing_state(processingObject_t * dst,
                                                          const processingObject_t * src)
{
    if (!dst || !src)
    {
        return;
    }

    dst->exr_mode = src->exr_mode;
    dst->AgX = src->AgX;
    dst->highlight_reconstruction = src->highlight_reconstruction;
    dst->shadows_highlights.highlights = src->shadows_highlights.highlights;
    dst->shadows_highlights.shadows = src->shadows_highlights.shadows;
    dst->contrast = src->contrast;
    dst->clarity = src->clarity;
    dst->transformation = src->transformation;
    dst->denoiserStrength = src->denoiserStrength;
    dst->rbfDenoiserLuma = src->rbfDenoiserLuma;
    dst->rbfDenoiserChroma = src->rbfDenoiserChroma;
    dst->grainStrength = src->grainStrength;
    dst->gradient_enable = src->gradient_enable;
    dst->vignette_strength = src->vignette_strength;
    dst->use_cam_matrix = src->use_cam_matrix;
    dst->colour_gamut = src->colour_gamut;
    dst->allow_creative_adjustments = src->allow_creative_adjustments;
    dst->ca_desaturate = src->ca_desaturate;
    dst->ca_radius = src->ca_radius;
    dst->filter_on = src->filter_on;
    dst->lut_on = src->lut_on;
    dst->cs_zone.use_cs = src->cs_zone.use_cs;
    dst->sharpen = src->sharpen;
    memcpy(dst->proper_wb_matrix,
           src->proper_wb_matrix,
           sizeof(dst->proper_wb_matrix));
    memcpy(dst->pre_calc_levels,
           src->pre_calc_levels,
           sizeof(dst->pre_calc_levels));
    memcpy(dst->pre_calc_gamma,
           src->pre_calc_gamma,
           sizeof(dst->pre_calc_gamma));
    memcpy(dst->pre_calc_curve_r,
           src->pre_calc_curve_r,
           sizeof(dst->pre_calc_curve_r));
    memcpy(dst->gcurve_y, src->gcurve_y, sizeof(dst->gcurve_y));
    memcpy(dst->gcurve_r, src->gcurve_r, sizeof(dst->gcurve_r));
    memcpy(dst->gcurve_g, src->gcurve_g, sizeof(dst->gcurve_g));
    memcpy(dst->gcurve_b, src->gcurve_b, sizeof(dst->gcurve_b));

    for (int i = 0; i < 9; ++i)
    {
        if (dst->pre_calc_matrix[i] && src->pre_calc_matrix[i])
        {
            memcpy(dst->pre_calc_matrix[i],
                   src->pre_calc_matrix[i],
                   65536u * sizeof(*dst->pre_calc_matrix[i]));
        }
    }
}

static int mlv_processed_frame_8bit_cache_try_copy(mlvObject_t * video,
                                                   uint64_t frameIndex,
                                                   int threads,
                                                   uint64_t signature,
                                                   int allow_prefetched_hit,
                                                   uint8_t * outputFrame,
                                                   uint64_t rgb_frame_size,
                                                   int scaleFactor,
                                                   int * prefetched)
{
    int hit = 0;
    if (prefetched)
    {
        *prefetched = 0;
    }

    pthread_mutex_lock(&video->processed8_prefetch_mutex);
    int slot = mlv_find_processed_frame_8bit_cache_slot_locked_with_scale(video,
                                                                          frameIndex,
                                                                          threads,
                                                                          signature,
                                                                          scaleFactor);
    if (slot >= 0)
    {
        uint8_t * cached_frame = mlv_processed_frame_8bit_cache_slot(video, (uint32_t)slot, rgb_frame_size);
        if (cached_frame)
        {
            const int is_prefetched = video->processed_8bit_cache_prefetched[slot] != 0;
            if (is_prefetched && !allow_prefetched_hit)
            {
                pthread_mutex_unlock(&video->processed8_prefetch_mutex);
                return 0;
            }

            if (outputFrame != cached_frame)
            {
                memcpy(outputFrame, cached_frame, (size_t)rgb_frame_size);
            }
            video->current_processed_frame_8bit_active = 1;
            video->current_processed_frame_8bit = frameIndex;
            video->current_processed_frame_8bit_threads = threads;
            video->current_processed_frame_8bit_signature = signature;
            if (prefetched)
            {
                *prefetched = is_prefetched;
            }
            hit = 1;
        }
        else
        {
            mlv_reset_processed_frame_8bit_cache_locked(video);
        }
    }
    pthread_mutex_unlock(&video->processed8_prefetch_mutex);

    return hit;
}

static void mlv_reset_processed8_prefetch_locked(mlvObject_t * video)
{
    video->processed8_prefetch_request_pending = 0;
    video->processed8_prefetch_worker_busy = 0;
    video->processed8_prefetch_request_frame = 0;
    video->processed8_prefetch_request_threads = 0;
    video->processed8_prefetch_request_scale = 0;
    mlv_reset_processed_frame_8bit_cache_locked(video);
}

static int mlv_render_processed_frame8_direct_with_processing(mlvObject_t * video,
                                                              processingObject_t * processing,
                                                              int syncProcessingLevels,
                                                              uint64_t frameIndex,
                                                              uint8_t * outputFrame,
                                                              int threads,
                                                              int recordTelemetry);
static int mlv_start_processed8_prefetch_thread(mlvObject_t * video);

static void mlv_processed8_prefetch_note_request(mlvObject_t * video,
                                                 uint64_t frameIndex,
                                                 int threads,
                                                 uint64_t stateSignature,
                                                 int scaleFactor)
{
    pthread_mutex_lock(&video->processed8_prefetch_mutex);

    if (!video->processed8_prefetch_thread_started)
    {
        pthread_mutex_unlock(&video->processed8_prefetch_mutex);
        if (!mlv_start_processed8_prefetch_thread(video))
        {
            return;
        }
        pthread_mutex_lock(&video->processed8_prefetch_mutex);
    }

    /* Phase E6: scaleFactor change must invalidate the prefetch generation
     * the same way the state signature already does. (The state signature
     * encodes scale via Phase 4A, so this is in practice subsumed by the
     * stateSignature comparison below; we guard explicitly anyway in case
     * future hash refactors decouple them.) */
    if ((video->processed8_prefetch_last_request_frame + 1 != frameIndex
         && video->processed8_prefetch_last_request_frame != frameIndex)
        || video->processed8_prefetch_last_request_threads != threads
        || video->processed8_prefetch_last_state_signature != stateSignature
        || video->processed8_prefetch_last_request_scale != scaleFactor)
    {
        ++video->processed8_prefetch_generation;
        mlv_reset_processed8_prefetch_locked(video);
        if (!video->processed8_prefetch_processing || !video->processing)
        {
            pthread_mutex_unlock(&video->processed8_prefetch_mutex);
            return;
        }

        mlv_copy_processed8_prefetch_processing_state(video->processed8_prefetch_processing,
                                                      video->processing);
    }
    video->processed8_prefetch_last_request_frame = frameIndex;
    video->processed8_prefetch_last_request_threads = threads;
    video->processed8_prefetch_last_state_signature = stateSignature;
    video->processed8_prefetch_last_request_scale = scaleFactor;
    video->processed8_prefetch_request_frame = frameIndex;
    video->processed8_prefetch_request_threads = threads;
    video->processed8_prefetch_request_scale = scaleFactor;
    video->processed8_prefetch_request_pending = 1;
    pthread_cond_signal(&video->processed8_prefetch_cond);
    pthread_mutex_unlock(&video->processed8_prefetch_mutex);
}

typedef struct
{
    mlvObject_t * video;
    processingObject_t * processing;
    uint64_t baseFrame;
    int threads;
    uint64_t stateSignature;
    uint32_t generation;
    uint64_t rgb_frame_size;
    uint32_t offsetStart;
    uint32_t offsetStep;
    int scaleFactor; /* Phase E6: must match the render thread's normalizedScale
                      * so the cache slot's `scale` lane lines up with the
                      * lookup. Without this, store-with-scale=4 vs
                      * lookup-with-scale=1 misses every time. */
} mlv_processed8_prefetch_task_t;

static void mlv_processed8_prefetch_execute_task(const mlv_processed8_prefetch_task_t * task)
{
    if (!task || !task->video || !task->processing)
    {
        return;
    }

    for (uint32_t offset = task->offsetStart;
         offset <= MLV_PROCESSED_8BIT_PREFETCH_LOOKAHEAD;
         offset += task->offsetStep)
    {
        uint64_t targetFrame = task->baseFrame + offset;
        if (targetFrame >= getMlvFrames(task->video))
        {
            break;
        }

        uint64_t targetSignature =
            mlv_processed_frame_signature_from_state(task->stateSignature,
                                                    targetFrame);

        pthread_mutex_lock(&task->video->processed8_prefetch_mutex);
        if (task->video->processed8_prefetch_stop
            || task->generation != task->video->processed8_prefetch_generation)
        {
            pthread_mutex_unlock(&task->video->processed8_prefetch_mutex);
            break;
        }

        if (!mlv_prepare_processed_frame_8bit_cache_locked(task->video,
                                                           task->rgb_frame_size))
        {
            pthread_mutex_unlock(&task->video->processed8_prefetch_mutex);
            continue;
        }

        if (mlv_processed_frame_8bit_cache_contains_locked_with_scale(task->video,
                                                                      targetFrame,
                                                                      task->threads,
                                                                      targetSignature,
                                                                      task->generation,
                                                                      task->scaleFactor))
        {
            pthread_mutex_unlock(&task->video->processed8_prefetch_mutex);
            continue;
        }
        pthread_mutex_unlock(&task->video->processed8_prefetch_mutex);

        uint8_t * prefetchBuffer = mlv_ensure_thread_u8_buffer(task->rgb_frame_size);
        if (!prefetchBuffer)
        {
            continue;
        }

        /* Phase E6: the direct render reads playback_scale_factor_active
         * from the video object to decide whether to invoke the fused
         * downsample. The render thread sets this before calling the
         * direct path (line ~3499). When the worker pre-renders we must
         * mirror that contract — without it the worker would render at
         * full resolution but store under the scale-N cache lane (or vice
         * versa), invalidating any byte-identity assumption. */
        const int saved_scale = task->video->playback_scale_factor_active;
        task->video->playback_scale_factor_active = task->scaleFactor;
        int renderOk = mlv_render_processed_frame8_direct_with_processing(
            task->video,
            task->processing,
            0,
            targetFrame,
            prefetchBuffer,
            task->threads,
            0);
        task->video->playback_scale_factor_active = saved_scale;

        pthread_mutex_lock(&task->video->processed8_prefetch_mutex);
        if (renderOk
            && !task->video->processed8_prefetch_stop
            && task->generation == task->video->processed8_prefetch_generation
            && !mlv_processed_frame_8bit_cache_contains_locked_with_scale(task->video,
                                                                          targetFrame,
                                                                          task->threads,
                                                                          targetSignature,
                                                                          task->generation,
                                                                          task->scaleFactor))
        {
            mlv_store_processed_frame_8bit_cache_locked_with_scale(task->video,
                                                                   targetFrame,
                                                                   task->threads,
                                                                   targetSignature,
                                                                   prefetchBuffer,
                                                                   task->rgb_frame_size,
                                                                   0,
                                                                   1,
                                                                   task->scaleFactor);
        }
        pthread_mutex_unlock(&task->video->processed8_prefetch_mutex);
    }
}

static void * mlv_processed8_prefetch_thread_main(void * opaque)
{
    mlvObject_t * video = (mlvObject_t *)opaque;
    processingObject_t * prefetchProcessing = initProcessingObject();

    while (1)
    {
        pthread_mutex_lock(&video->processed8_prefetch_mutex);
        while (!video->processed8_prefetch_stop
               && !video->processed8_prefetch_request_pending)
        {
            pthread_cond_wait(&video->processed8_prefetch_cond, &video->processed8_prefetch_mutex);
        }

        if (video->processed8_prefetch_stop)
        {
            pthread_mutex_unlock(&video->processed8_prefetch_mutex);
            break;
        }

        /* Phase E6: rgb_frame_size depends on the requested scale; size the
         * cache buffer to match what the slot store will write. Computing
         * this from the active request keeps store and lookup byte-sized
         * the same way. */
        const int requestScale = mlv_normalize_playback_scale_factor(
            video->processed8_prefetch_request_scale);
        const int full_w = (int)getMlvWidth(video);
        const int full_h = (int)getMlvHeight(video);
        const int out_w = (requestScale > 1) ? (full_w / requestScale) : full_w;
        const int out_h = (requestScale > 1) ? (full_h / requestScale) : full_h;
        const uint64_t rgb_frame_size = (uint64_t)out_w * (uint64_t)out_h * 3u;

        if (!mlv_prepare_processed_frame_8bit_cache_locked(video, rgb_frame_size))
        {
            video->processed8_prefetch_request_pending = 0;
            pthread_mutex_unlock(&video->processed8_prefetch_mutex);
            continue;
        }

        uint64_t baseFrame = video->processed8_prefetch_request_frame;
        int threads = video->processed8_prefetch_request_threads;
        int taskScale = requestScale;
        uint64_t stateSignature = video->processed8_prefetch_last_state_signature;
        uint32_t generation = video->processed8_prefetch_generation;
        if (prefetchProcessing && video->processed8_prefetch_processing)
        {
            mlv_copy_processed8_prefetch_processing_state(prefetchProcessing,
                                                          video->processed8_prefetch_processing);
        }
        video->processed8_prefetch_request_pending = 0;
        video->processed8_prefetch_worker_busy = 1;
        pthread_mutex_unlock(&video->processed8_prefetch_mutex);

        mlv_processed8_prefetch_task_t primaryTask = {
            .video = video,
            .processing = prefetchProcessing,
            .baseFrame = baseFrame,
            .threads = threads,
            .stateSignature = stateSignature,
            .generation = generation,
            .rgb_frame_size = rgb_frame_size,
            .offsetStart = 1,
            .offsetStep = 1,
            .scaleFactor = taskScale
        };
        mlv_processed8_prefetch_execute_task(&primaryTask);

        pthread_mutex_lock(&video->processed8_prefetch_mutex);
        video->processed8_prefetch_worker_busy = 0;
        pthread_mutex_unlock(&video->processed8_prefetch_mutex);
    }

    if (prefetchProcessing)
    {
        freeProcessingObject(prefetchProcessing);
    }

    return NULL;
}

static int mlv_start_processed8_prefetch_thread(mlvObject_t * video)
{
    pthread_mutex_lock(&video->processed8_prefetch_mutex);
    if (video->processed8_prefetch_thread_started)
    {
        pthread_mutex_unlock(&video->processed8_prefetch_mutex);
        return 1;
    }
    video->processed8_prefetch_thread_started = 1;
    pthread_mutex_unlock(&video->processed8_prefetch_mutex);

    if (pthread_create(&video->processed8_prefetch_thread,
                       NULL,
                       mlv_processed8_prefetch_thread_main,
                       video) != 0)
    {
        pthread_mutex_lock(&video->processed8_prefetch_mutex);
        video->processed8_prefetch_thread_started = 0;
        pthread_mutex_unlock(&video->processed8_prefetch_mutex);
        return 0;
    }

    return 1;
}

static float * mlv_ensure_float_buffer(float ** buffer, uint64_t * capacity_pixels, uint64_t required_pixels)
{
    if (!mlv_ensure_reusable_buffer((void **)buffer, capacity_pixels, required_pixels, sizeof(float)))
    {
        return NULL;
    }
    return *buffer;
}

static float * mlv_ensure_thread_float_buffer(uint64_t required_pixels)
{
    static MLV_THREAD_LOCAL float * tls_buffer = NULL;
    static MLV_THREAD_LOCAL uint64_t tls_capacity_pixels = 0;
    return mlv_ensure_float_buffer(&tls_buffer, &tls_capacity_pixels, required_pixels);
}

static int seek_to_next_known_block(FILE * in_file)
{
    uint64_t read_ahead_size = 128 * 1024 * 1024;
    uint8_t * ahead = malloc(read_ahead_size);

    uint64_t read = fread(ahead, 1, read_ahead_size, in_file);
    file_set_pos(in_file, -read, SEEK_CUR);
    for (uint64_t i = 0; i < read; i++)
    {
        if (memcmp(ahead + i, "VIDF", 4) == 0 ||
            memcmp(ahead + i, "AUDF", 4) == 0 ||
            memcmp(ahead + i, "NULL", 4) == 0 ||
            memcmp(ahead + i, "RTCI", 4) == 0)
        {
            DEBUG( printf("Next known block: %c%c%c%c at 0x%"PRIx64"+0x%"PRIx64" = ", ahead[i], ahead[i+1], ahead[i+2], ahead[i+3], file_get_pos(in_file), i); )
            file_set_pos(in_file, i, SEEK_CUR);
            DEBUG( printf("0x%"PRIx64"\n", file_get_pos(in_file)); )
            free(ahead);
            return 1;
        }
    }

    DEBUG( printf("Could not find any known block from 0x%"PRIx64".\n", file_get_pos(in_file)); )
    free(ahead);
    return 0;
}

/* Spanned multichunk MLV file handling */
static FILE **load_all_chunks(char *base_filename, int *entries)
{
    int seq_number = 0;
    int max_name_len = strlen(base_filename) + 16;
    char *filename = alloca(max_name_len);

    strncpy(filename, base_filename, max_name_len - 1);
    FILE **files = malloc(sizeof(FILE*));

    files[0] = fopen(filename, "rb");
    if(!files[0])
    {
        free(files);
        return NULL;
    }

    DEBUG( printf("\nFile %s opened\n", filename); )

    /* get extension and check if it is a .MLV */
    char *dot = strrchr(filename, '.');
    if(dot)
    {
        dot++;
        if(strcasecmp(dot, "mlv"))
        {
            seq_number = 100;
        }
    }

    (*entries)++;
    while(seq_number < 99)
    {
        FILE **realloc_files = realloc(files, (*entries + 1) * sizeof(FILE*));

        if(!realloc_files)
        {
            free(files);
            return NULL;
        }

        files = realloc_files;

        /* check for the next file M00, M01 etc */
        char seq_name[8];

        sprintf(seq_name, "%02d", seq_number);
        seq_number++;

        strcpy(&filename[strlen(filename) - 2], seq_name);

        /* try to open */
        files[*entries] = fopen(filename, "rb");
        if(files[*entries])
        {
            DEBUG( printf("File %s opened\n", filename); )
            (*entries)++;
        }
        else
        {
            DEBUG( printf("File %s not existing\n\n", filename); )
            break;
        }
    }

    return files;
}

static void close_all_chunks(FILE ** files, int entries)
{
    for(int i = 0; i < entries; i++)
        if(files[i]) fclose(files[i]);
    if(files) free(files);
}

static void frame_index_sort(frame_index_t *frame_index, uint32_t entries)
{
    if (!entries) return;

    uint32_t n = entries;
    do
    {
        uint32_t new_n = 1;
        for (uint32_t i = 0; i < n-1; ++i)
        {
            if (frame_index[i].frame_time > frame_index[i+1].frame_time)
            {
                frame_index_t tmp = frame_index[i+1];
                frame_index[i+1] = frame_index[i];
                frame_index[i] = tmp;
                new_n = i + 1;
            }
        }
        n = new_n;
    } while (n > 1);
}

/* Unpack or decompress original raw data */
static int getMlvRawFrameUint16Direct(mlvObject_t * video, uint64_t frameIndex, uint16_t * unpackedFrame)
{
    int bitdepth = video->RAWI.raw_info.bits_per_pixel;
    int width = video->RAWI.xRes;
    int height = video->RAWI.yRes;
    int pixels_count = width * height;

    int chunk = video->video_index[frameIndex].chunk_num;
    uint32_t frame_size = video->video_index[frameIndex].frame_size;
    uint64_t frame_offset = video->video_index[frameIndex].frame_offset;
    uint64_t frame_header_offset = video->video_index[frameIndex].block_offset;

    /* How many bytes is RAW frame */
    int raw_frame_size = (width * height * bitdepth) / 8;
    /* Memory buffer for original RAW data */
    uint8_t * raw_frame = (uint8_t *)malloc(raw_frame_size + 4); // additional 4 bytes for safety

    g_mlv_last_raw_uint16_disk_read_ms = 0.0;
    g_mlv_last_raw_uint16_decompress_ms = 0.0;
    g_mlv_last_raw_uint16_decompress_prepare_ms = 0.0;
    g_mlv_last_raw_uint16_decompress_execute_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred6_split_active = 0;
    g_mlv_last_raw_uint16_lj92_pred6_split_requested = 0;
    g_mlv_last_raw_uint16_lj92_generic_split_active = 0;
    g_mlv_last_raw_uint16_lj92_generic_split_requested = 0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_active = 0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_requested = 0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_active = 0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_eligible = 0;
    g_mlv_last_raw_uint16_lj92_scan_component_count = 0;
    g_mlv_last_raw_uint16_lj92_write_length = 0;
    g_mlv_last_raw_uint16_lj92_expected_write_length = 0;
    g_mlv_last_raw_uint16_lj92_skip_length = 0;
    g_mlv_last_raw_uint16_lj92_linearize_active = 0;
    g_mlv_last_raw_uint16_lj92_component_count = 0;
    g_mlv_last_raw_uint16_lj92_predictor = -1;
    g_mlv_last_raw_uint16_lj92_pred6_total_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred6_bitstream_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred6_predictor_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_generic_total_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_generic_bitstream_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_generic_predictor_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_total_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_bitstream_ms = 0.0;
    g_mlv_last_raw_uint16_lj92_pred1_fast_path_predictor_ms = 0.0;
    g_mlv_last_raw_uint16_unpack_ms = 0.0;
    g_mlv_last_raw_uint16_copy_ms = 0.0;

    FILE * file = video->file[chunk];

    /* Move to start of frame in file and read the RAW data */
    pthread_mutex_lock(video->main_file_mutex + chunk);

    file_set_pos(file, frame_header_offset, SEEK_SET);

    if (isMcrawLoaded(video))
    {
        mr_item_t item = {};
        const double disk_read_start = mlv_stage_timing_now();

        if (fread(&item, sizeof(mr_item_t), 1, file) != 1)
        {
            DEBUG( printf("Frame header read error\n"); )
            free(raw_frame);
            pthread_mutex_unlock(video->main_file_mutex + chunk);
            return 1;
        }

        frame_size = item.size;

        if (fread(raw_frame, frame_size, 1, file) != 1)
        {
            DEBUG( printf("Frame data read error\n"); )
            free(raw_frame);
            pthread_mutex_unlock(video->main_file_mutex + chunk);
            return 1;
        }

        pthread_mutex_unlock(video->main_file_mutex + chunk);
        g_mlv_last_raw_uint16_disk_read_ms = (mlv_stage_timing_now() - disk_read_start) * 1000.0;

        const double decompress_start = mlv_stage_timing_now();
        int64_t ret = mr_decode_video_frame((uint8_t*)unpackedFrame, raw_frame, frame_size, width, height, video->compression_type);
        g_mlv_last_raw_uint16_decompress_execute_ms = (mlv_stage_timing_now() - decompress_start) * 1000.0;
        g_mlv_last_raw_uint16_decompress_ms = g_mlv_last_raw_uint16_decompress_execute_ms;

        if (ret <= 0)
        {
            DEBUG( printf("mcraw decoder: Failed with error code (%d)\n", ret); )
            free(raw_frame);
            return 1;
        }

        if (video->RAWI.raw_info.cfa_pattern == 0x01000201)   // gbrg
        {
            const double copy_start = mlv_stage_timing_now();
            // gb  ->  rg
            // rg      gb

            // discard first row
            memmove(unpackedFrame, &unpackedFrame[width], width * (height - 1) * 2);

            // copy row n-2 to row n
            memcpy(&unpackedFrame[width * (height - 1)], &unpackedFrame[width * (height - 3)], width * 2);
            g_mlv_last_raw_uint16_copy_ms = (mlv_stage_timing_now() - copy_start) * 1000.0;
        }
        else if (video->RAWI.raw_info.cfa_pattern == 0x00010102)   // bggr
        {
            const double copy_start = mlv_stage_timing_now();
            // bg  ->  rg
            // gr      gb

            // !!untested!!

            // discard first row, discard first col
            memmove(unpackedFrame, &unpackedFrame[width + 1], (width * (height - 1) * 2) - 2);

            // copy row n-2 to row n
            memcpy(&unpackedFrame[width * (height - 1)], &unpackedFrame[width * (height - 3)], width * 2);

            // copy col n-2 to col n
            for (int i = 0; i < height; i++)
            {
                int pos = ((i + 1) * width) - 1;
                unpackedFrame[pos] = unpackedFrame[pos - 2];
            }
            g_mlv_last_raw_uint16_copy_ms = (mlv_stage_timing_now() - copy_start) * 1000.0;
        }
        else if (video->RAWI.raw_info.cfa_pattern == 0x01020001)   // grbg
        {
            const double copy_start = mlv_stage_timing_now();
            // gr  ->  rg
            // bg      gb

            // !!untested!!

            // discard first col
            memmove(unpackedFrame, &unpackedFrame[1], (width * height * 2) - 2);

            // copy col n-2 to col n
            for (int i = 0; i < height; i++)
            {
                int pos = ((i + 1) * width) - 1;
                unpackedFrame[pos] = unpackedFrame[pos - 2];
            }
            g_mlv_last_raw_uint16_copy_ms = (mlv_stage_timing_now() - copy_start) * 1000.0;
        }
    }
    else
    {
        const double disk_read_start = mlv_stage_timing_now();
        if (fread(&video->VIDF, sizeof(mlv_vidf_hdr_t), 1, file) != 1)
        {
            DEBUG( printf("Frame header read error\n"); )
            free(raw_frame);
            pthread_mutex_unlock(video->main_file_mutex + chunk);
            return 1;
        }

        file_set_pos(file, frame_offset, SEEK_SET);

        if (video->MLVI.videoClass & MLV_VIDEO_CLASS_FLAG_LJ92)
        {
            if(fread(raw_frame, frame_size, 1, file) != 1)
            {
                DEBUG( printf("Frame data read error\n"); )
                free(raw_frame);
                pthread_mutex_unlock(video->main_file_mutex + chunk);
                return 1;
            }

            pthread_mutex_unlock(video->main_file_mutex + chunk);
            g_mlv_last_raw_uint16_disk_read_ms = (mlv_stage_timing_now() - disk_read_start) * 1000.0;

            int components = 1;
            lj92 decoder_object;
            const double decompress_start = mlv_stage_timing_now();
            const double decompress_prepare_start = mlv_stage_timing_now();
            int ret = lj92_open(&decoder_object, raw_frame, frame_size, &width, &height, &bitdepth, &components);
            g_mlv_last_raw_uint16_decompress_prepare_ms =
                (mlv_stage_timing_now() - decompress_prepare_start) * 1000.0;
            if(ret != LJ92_ERROR_NONE)
            {
                DEBUG( printf("LJ92 decoder: Failed with error code (%d)\n", ret); )
                free(raw_frame);
                return 1;
            }
            else
            {
                const double decompress_execute_start = mlv_stage_timing_now();
                ret = lj92_decode(decoder_object, unpackedFrame, width * height * components, 0, NULL, 0);
                g_mlv_last_raw_uint16_decompress_execute_ms =
                    (mlv_stage_timing_now() - decompress_execute_start) * 1000.0;
                g_mlv_last_raw_uint16_lj92_pred6_split_active =
                    lj92_get_last_pred6_split_active();
                g_mlv_last_raw_uint16_lj92_pred6_split_requested =
                    lj92_get_last_pred6_split_requested();
                g_mlv_last_raw_uint16_lj92_generic_split_active =
                    lj92_get_last_generic_split_active();
                g_mlv_last_raw_uint16_lj92_generic_split_requested =
                    lj92_get_last_generic_split_requested();
                g_mlv_last_raw_uint16_lj92_pred1_fast_path_active =
                    lj92_get_last_pred1_fast_path_active();
                g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_requested =
                    lj92_get_last_pred1_fast_path_measurement_requested();
                g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_active =
                    lj92_get_last_pred1_fast_path_measurement_active();
                g_mlv_last_raw_uint16_lj92_pred1_fast_path_eligible =
                    lj92_get_last_pred1_fast_path_eligible();
                g_mlv_last_raw_uint16_lj92_scan_component_count =
                    lj92_get_last_scan_component_count();
                g_mlv_last_raw_uint16_lj92_write_length =
                    lj92_get_last_write_length();
                g_mlv_last_raw_uint16_lj92_expected_write_length =
                    lj92_get_last_expected_write_length();
                g_mlv_last_raw_uint16_lj92_skip_length =
                    lj92_get_last_skip_length();
                g_mlv_last_raw_uint16_lj92_linearize_active =
                    lj92_get_last_linearize_active();
                g_mlv_last_raw_uint16_lj92_component_count =
                    lj92_get_last_component_count();
                g_mlv_last_raw_uint16_lj92_predictor =
                    lj92_get_last_predictor();
                g_mlv_last_raw_uint16_lj92_pred6_total_ms =
                    lj92_get_last_pred6_total_ms();
                g_mlv_last_raw_uint16_lj92_pred6_bitstream_ms =
                    lj92_get_last_pred6_bitstream_ms();
                g_mlv_last_raw_uint16_lj92_pred6_predictor_ms =
                    lj92_get_last_pred6_predictor_ms();
                g_mlv_last_raw_uint16_lj92_generic_total_ms =
                    lj92_get_last_generic_total_ms();
                g_mlv_last_raw_uint16_lj92_generic_bitstream_ms =
                    lj92_get_last_generic_bitstream_ms();
                g_mlv_last_raw_uint16_lj92_generic_predictor_ms =
                    lj92_get_last_generic_predictor_ms();
                g_mlv_last_raw_uint16_lj92_pred1_fast_path_total_ms =
                    lj92_get_last_pred1_fast_path_total_ms();
                g_mlv_last_raw_uint16_lj92_pred1_fast_path_bitstream_ms =
                    lj92_get_last_pred1_fast_path_bitstream_ms();
                g_mlv_last_raw_uint16_lj92_pred1_fast_path_predictor_ms =
                    lj92_get_last_pred1_fast_path_predictor_ms();
                if(ret != LJ92_ERROR_NONE)
                {
                    DEBUG( printf("LJ92 decoder: Failed with error code (%d)\n", ret); )
                    free(raw_frame);
                    return 1;
                }
            }
            lj92_close(decoder_object);
            g_mlv_last_raw_uint16_decompress_ms = (mlv_stage_timing_now() - decompress_start) * 1000.0;
        }
        else /* If not compressed just unpack to 16bit */
        {
            if(fread(raw_frame, raw_frame_size, 1, file) != 1)
            {
                DEBUG( printf("Frame data read error\n"); )
                free(raw_frame);
                pthread_mutex_unlock(video->main_file_mutex + chunk);
                return 1;
            }

            pthread_mutex_unlock(video->main_file_mutex + chunk);
            g_mlv_last_raw_uint16_disk_read_ms = (mlv_stage_timing_now() - disk_read_start) * 1000.0;

            uint32_t mask = (1 << bitdepth) - 1;
            const double unpack_start = mlv_stage_timing_now();
            #pragma omp parallel for
            for (int i = 0; i < pixels_count; ++i)
            {
                uint32_t bits_offset = i * bitdepth;
                uint32_t bits_address = bits_offset / 16;
                uint32_t bits_shift = bits_offset % 16;
                uint32_t rotate_value = 16 + ((32 - bitdepth) - bits_shift);
                uint32_t uncorrected_data = *((uint32_t *)&((uint16_t *)raw_frame)[bits_address]);
                uint32_t data = ROR32(uncorrected_data, rotate_value);
                unpackedFrame[i] = ((uint16_t)(data & mask));
            }
            g_mlv_last_raw_uint16_unpack_ms = (mlv_stage_timing_now() - unpack_start) * 1000.0;
        }
    }

    free(raw_frame);

    /* S0_raw_uint16 capture: post LJ92 decode + bit-unpack, pre llrawproc.
     * Inert when MLVAPP_PIPELINE_CAPTURE_DIR is unset. */
    if (mlv_pipeline_capture_should_capture_frame(frameIndex))
    {
        const int width = (int)getMlvWidth(video);
        const int height = (int)getMlvHeight(video);
        mlv_pipeline_capture_meta_t meta;
        memset(&meta, 0, sizeof meta);
        meta.stage = MLV_PIPELINE_STAGE_S0_RAW_UINT16;
        meta.format = MLV_PIPELINE_FORMAT_UINT16_MONO;
        meta.format_label = "uint16_bayer_post_unpack";
        meta.width = width;
        meta.height = height;
        meta.bytes_per_line = width * (int)sizeof(uint16_t);
        meta.bytes_per_pixel = (int)sizeof(uint16_t);
        meta.channels = 1;
        meta.bit_depth = 16;
        meta.dual_iso_mode = "n/a";
        meta.debayer_mode = "n/a";
        meta.scaler = "none";
        meta.path_label = NULL;
        mlv_pipeline_capture(frameIndex, unpackedFrame, &meta);
    }

    return 0;
}

int getMlvRawFrameUint16(mlvObject_t * video, uint64_t frameIndex, uint16_t * unpackedFrame)
{
    const int compressedRaw = isMcrawLoaded(video)
        || (video->MLVI.videoClass & MLV_VIDEO_CLASS_FLAG_LJ92);
    const int prefetchEnabled = compressedRaw && mlv_raw_uint16_prefetch_enabled();

    g_mlv_last_raw_uint16_prefetch_hit = 0;

    if (prefetchEnabled && mlv_raw_uint16_prefetch_try_copy(video, frameIndex, unpackedFrame))
    {
        g_mlv_last_raw_uint16_disk_read_ms = 0.0;
        g_mlv_last_raw_uint16_decompress_ms = 0.0;
        g_mlv_last_raw_uint16_decompress_prepare_ms = 0.0;
        g_mlv_last_raw_uint16_decompress_execute_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_pred6_split_active = 0;
        g_mlv_last_raw_uint16_lj92_pred6_split_requested = 0;
        g_mlv_last_raw_uint16_lj92_generic_split_active = 0;
        g_mlv_last_raw_uint16_lj92_generic_split_requested = 0;
        g_mlv_last_raw_uint16_lj92_pred1_fast_path_active = 0;
        g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_requested = 0;
        g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_active = 0;
        g_mlv_last_raw_uint16_lj92_pred1_fast_path_eligible = 0;
        g_mlv_last_raw_uint16_lj92_scan_component_count = 0;
        g_mlv_last_raw_uint16_lj92_write_length = 0;
        g_mlv_last_raw_uint16_lj92_expected_write_length = 0;
        g_mlv_last_raw_uint16_lj92_skip_length = 0;
        g_mlv_last_raw_uint16_lj92_linearize_active = 0;
        g_mlv_last_raw_uint16_lj92_component_count = 0;
        g_mlv_last_raw_uint16_lj92_predictor = -1;
        g_mlv_last_raw_uint16_lj92_pred6_total_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_pred6_bitstream_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_pred6_predictor_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_generic_total_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_generic_bitstream_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_generic_predictor_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_pred1_fast_path_total_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_pred1_fast_path_bitstream_ms = 0.0;
        g_mlv_last_raw_uint16_lj92_pred1_fast_path_predictor_ms = 0.0;
        g_mlv_last_raw_uint16_unpack_ms = 0.0;
        g_mlv_last_raw_uint16_copy_ms = 0.0;
        g_mlv_last_raw_uint16_prefetch_hit = 1;
        mlv_raw_uint16_prefetch_note_request(video, frameIndex);
        return 0;
    }

    int result = getMlvRawFrameUint16Direct(video, frameIndex, unpackedFrame);
    if (result == 0 && prefetchEnabled)
    {
        mlv_raw_uint16_prefetch_store_frame(video, frameIndex, unpackedFrame);
        mlv_raw_uint16_prefetch_note_request(video, frameIndex);
    }

    return result;
}

int getMlvRawFrameProcessedUint16(mlvObject_t * video,
                                  uint64_t frameIndex,
                                  uint16_t * outputFrame,
                                  int * bit_shift)
{
    int pixels_count = video->RAWI.xRes * video->RAWI.yRes;
    size_t output_frame_size = (size_t)pixels_count * sizeof(uint16_t);

    mlv_reset_last_raw_stage_telemetry();
    g_mlv_last_llrawproc_ms = 0.0;
    g_mlv_last_raw_float_convert_ms = 0.0;

    const double unpack_start = mlv_stage_timing_now();
    if(getMlvRawFrameUint16(video, frameIndex, outputFrame))
    {
        memset(outputFrame, 0, output_frame_size);
        mlv_stage_timing_note("raw_uint16", frameIndex, unpack_start);
        return 1;
    }
    const double raw_uint16_ms = (mlv_stage_timing_now() - unpack_start) * 1000.0;
    mlv_stage_timing_note_elapsed("raw_uint16", frameIndex, raw_uint16_ms);
    g_mlv_last_raw_uint16_ms = raw_uint16_ms;
    mlv_stage_timing_note_elapsed("raw_uint16_disk_read", frameIndex, g_mlv_last_raw_uint16_disk_read_ms);
    mlv_stage_timing_note_elapsed("raw_uint16_decompress", frameIndex, g_mlv_last_raw_uint16_decompress_ms);
    mlv_stage_timing_note_elapsed("raw_uint16_decompress_prepare", frameIndex, g_mlv_last_raw_uint16_decompress_prepare_ms);
    mlv_stage_timing_note_elapsed("raw_uint16_decompress_execute", frameIndex, g_mlv_last_raw_uint16_decompress_execute_ms);
    mlv_stage_timing_note_elapsed("raw_uint16_unpack", frameIndex, g_mlv_last_raw_uint16_unpack_ms);
    mlv_stage_timing_note_elapsed("raw_uint16_copy", frameIndex, g_mlv_last_raw_uint16_copy_ms);

    const double llraw_start = mlv_stage_timing_now();
    /* Make the frame index available to S1/S2 capture hooks inside
     * applyLLRawProcObject (which doesn't otherwise carry one). */
    mlv_pipeline_capture_set_current_frame(frameIndex);
    applyLLRawProcObject(video, outputFrame, output_frame_size);
    const double llrawproc_ms = (mlv_stage_timing_now() - llraw_start) * 1000.0;
    mlv_stage_timing_note_elapsed("llrawproc", frameIndex, llrawproc_ms);
    g_mlv_last_llrawproc_ms = llrawproc_ms;

    if (bit_shift)
    {
        *bit_shift = llrpHQDualIso(video) ? 0 : (16 - video->RAWI.raw_info.bits_per_pixel);
    }

    return 0;
}

/* Unpacks the bits of a frame to get a bayer B&W image (without black level correction)
 * Needs memory to return to, sized: sizeof(float) * getMlvHeight(urvid) * getMlvWidth(urvid)
 * Output image's pixels will be in range 0-65535 as if it is 16 bit integers */
void getMlvRawFrameFloat(mlvObject_t * video, uint64_t frameIndex, float * outputFrame)
{
    const double total_start = mlv_stage_timing_now();
    mlv_reset_last_raw_stage_telemetry();
    g_mlv_last_llrawproc_ms = 0.0;
    g_mlv_last_raw_float_convert_ms = 0.0;
    int pixels_count = video->RAWI.xRes * video->RAWI.yRes;
    uint16_t * unpacked_frame = mlv_ensure_thread_u16_buffer((uint64_t)pixels_count);
    if (!unpacked_frame)
    {
        memset(outputFrame, 0, pixels_count * sizeof(float));
        mlv_stage_timing_note("raw_float_total", frameIndex, total_start);
        return;
    }

    int shift_val = 0;
    if(getMlvRawFrameProcessedUint16(video, frameIndex, unpacked_frame, &shift_val))
    {
        memset(outputFrame, 0, pixels_count * sizeof(float));
        mlv_stage_timing_note("raw_float_total", frameIndex, total_start);
        return;
    }
    mlv_stage_timing_note_elapsed("raw_float_locked",
                                  frameIndex,
                                  g_mlv_last_raw_uint16_ms + llrpGetLastSharedLockMilliseconds());

    /* convert uint16_t raw data -> float raw_data for processing with amaze or bilinear debayer, both need data input as float */
    const double raw_float_convert_start = mlv_stage_timing_now();
    #pragma omp parallel for
    for (volatile int i = 0; i < pixels_count; ++i)
    {
        outputFrame[i] = (float)(unpacked_frame[i] << shift_val);
    }
    g_mlv_last_raw_float_convert_ms = (mlv_stage_timing_now() - raw_float_convert_start) * 1000.0;
    mlv_stage_timing_note_elapsed("raw_float_convert", frameIndex, g_mlv_last_raw_float_convert_ms);

    mlv_stage_timing_note("raw_float_total", frameIndex, total_start);
}

void setMlvProcessing(mlvObject_t * video, processingObject_t * processing)
{
    //double camera_matrix[9]; commented for now, not used

    /* Easy bit */
    video->processing = processing;
    resetMlvCachedFrame(video);

    /* Link dual_iso value, because it is needed */
    video->processing->dual_iso = &video->llrawproc->dual_iso;

    /* explicitely switch whitebalance find flag off to get right matrix values */
    video->processing->wbFindActive = 0;

    /* Vignette alloc */
    video->processing->vignette_mask = realloc( video->processing->vignette_mask, getMlvWidth(video) * getMlvHeight(video) * sizeof( float ) );

    /* Gradient alloc */
    video->processing->gradient_mask = realloc( video->processing->gradient_mask, getMlvWidth(video) * getMlvHeight(video) * sizeof( uint16_t ) );

    /* MATRIX stuff (not working, so commented out - 
     * processing object defaults to 1,0,0,0,1,0,0,0,1) */

    /* Get camera matrix for MLV clip and set it in the processing object */
    //getMlvCameraTosRGBMatrix(video, camera_matrix);
    /* Set Camera to RGB */
    //processingCamTosRGBMatrix(processing, camera_matrix); /* Still not used in processing cos not working right */

    /* Make copy of original black and white levels, because it can be changed from the gui */
    video->original_black_level = getMlvBlackLevel(video);
    video->original_white_level = getMlvWhiteLevel(video);

    /* BLACK / WHITE level */
    processingSetBlackAndWhiteLevel( processing, getMlvBlackLevel(video), getMlvWhiteLevel(video), getMlvBitdepth(video) );

    /* If 5D3 or cropmode */
    if (strlen((char *)getMlvCamera(video)) > 20 || getMlvMaxWidth(video) > 1920)
    {
        processingSetSharpeningBias(processing, 0.0);
    }
    else /* Sharpening more sideways to hide vertical line skip artifacts a bit */
    {
        processingSetSharpeningBias(processing, -0.33);
    }

    /* Get camera matrices, daylight and tungsten */
    if (camidCheckIfCameraKnown(getMlvCameraModel(video)))
    {
        double cam_matrix_D[9], cam_matrix_A[9];
        int32_t * cam_matrix_D_int = camidGetColorMatrix2(getMlvCameraModel(video));
        int32_t * cam_matrix_A_int = camidGetColorMatrix1(getMlvCameraModel(video));
        for (int i = 0; i < 9; ++i)
        {
            cam_matrix_D[i] = ((double)cam_matrix_D_int[i*2])/((double)cam_matrix_D_int[i*2+1]);
            cam_matrix_A[i] = ((double)cam_matrix_A_int[i*2])/((double)cam_matrix_A_int[i*2+1]);
        }

        processingSetCamMatrix(processing, cam_matrix_D, cam_matrix_A);
    }
    else
    {
        /* If the camera is unknown, get matrix from the MLV matrix field.
         * Currently, MLV only stores one matrix unfortunately, so same the same
         * matrix will be used for tungsten and daylight. TODO: update this
         * code once MLV has new colour matrix blocks */
        double cam_matrix[9];
        int32_t * mlv_mat = video->RAWI.raw_info.color_matrix1;
        for (int i = 0; i < 9; ++i)
        {
            cam_matrix[i] = ((double)mlv_mat[i*2])/((double)mlv_mat[i*2+1]);
        }
        processingSetCamMatrix(processing, cam_matrix, cam_matrix);
    }
}

/* Phase 4B-v2: TLS scratch buffer for the post-downsample bayer (used
 * before llrawproc runs on the smaller buffer). */
static uint16_t * mlv_ensure_thread_scaled_bayer_buffer(uint64_t required_words)
{
    static MLV_THREAD_LOCAL uint16_t * tls_buffer = NULL;
    static MLV_THREAD_LOCAL uint64_t tls_capacity_words = 0;
    return mlv_ensure_u16_buffer(&tls_buffer, &tls_capacity_words, required_words);
}

/* Phase 4B-v2: env kill switch — set to 1 to disable downsample-before-
 * llrawproc and fall back to the v1 (downsample-after) path. Cached after
 * first read for branchless per-frame fast path. */
static int g_mlv_phase4bv2_disabled_env_cache = -1;

static int mlv_phase4bv2_disabled_via_env(void)
{
    if (g_mlv_phase4bv2_disabled_env_cache < 0)
    {
        const char * v = getenv("MLVAPP_DISABLE_PHASE4BV2");
        g_mlv_phase4bv2_disabled_env_cache =
            (v && *v && strcmp(v, "0") != 0 && strcmp(v, "false") != 0) ? 1 : 0;
    }
    return g_mlv_phase4bv2_disabled_env_cache;
}

/* Phase 4B-v3: env kill switch — set to 1 to disable the Y-crop-to-16-aligned
 * full XY pre-recon wrapper and force the v2 X-only-pre-recon fallback. v3
 * adds <= 0.66% of source rows lost from the bottom edge (zero on clips
 * already 16-aligned). */
static int g_mlv_phase4bv3_disabled_env_cache = -1;

static int mlv_phase4bv3_disabled_via_env(void)
{
    if (g_mlv_phase4bv3_disabled_env_cache < 0)
    {
        const char * v = getenv("MLVAPP_DISABLE_PHASE4BV3");
        g_mlv_phase4bv3_disabled_env_cache =
            (v && *v && strcmp(v, "0") != 0 && strcmp(v, "false") != 0) ? 1 : 0;
    }
    return g_mlv_phase4bv3_disabled_env_cache;
}

/* Phase 4B-v3: telemetry — counts which path the most recent v2 entry took.
 * 3 = full XY (v3, Y-cropped or natively aligned); 2 = X-only fallback;
 * 0 = v2 entry not invoked / rejected before path selection.
 *
 * Process-wide (not thread-local) so test code reading the counter on the
 * test thread sees writes from any worker thread that ran the v2 entry.
 * The counter is updated atomically via store-only semantics; tests don't
 * require any cross-thread synchronisation beyond a memory fence at the
 * end of the rendering call (which is provided by the OMP parallel-for
 * end barrier in the surrounding pipeline). */
static int g_mlv_phase4bv2_path_taken = 0;
static int g_mlv_phase4bv3_y_crop_rows = 0;

int mlv_phase4bv2_last_path_taken(void)
{
    return g_mlv_phase4bv2_path_taken;
}

int mlv_phase4bv3_last_y_crop_rows(void)
{
    return g_mlv_phase4bv3_y_crop_rows;
}

void mlv_phase4bv_reset_env_cache_for_testing(void)
{
    g_mlv_phase4bv2_disabled_env_cache = -1;
    g_mlv_phase4bv3_disabled_env_cache = -1;
}

/* Phase 4B-v2: returns 1 if the receipt's llrawproc options are compatible
 * with the downsample-before-llrawproc path. The path skips focus pixel,
 * bad pixel, vertical stripes, and pattern noise — those need full-res
 * absolute coordinates or column indices. If any of them are enabled, the
 * caller falls back to the v1 path. Dual ISO HQ recon is the headline
 * case; if it's enabled and the other features are off, v2 is safe. */
static int mlv_phase4bv2_receipt_compatible(mlvObject_t * video)
{
    if (!video || !video->llrawproc) return 0;
    llrawprocObject_t * shared = video->llrawproc;
    if (shared->focus_pixels) return 0;
    if (shared->bad_pixels) return 0;
    if (shared->vertical_stripes) return 0;
    if (shared->pattern_noise) return 0;
    return 1;
}

/* Phase 4B-v2: one-shot diagnostic logger. Set MLVAPP_LOG_PHASE4BV2=1 to
 * see why the v2 path is being rejected on the first frame of a session. */
static int g_mlv_phase4bv2_log_env_cache = -1;
static MLV_THREAD_LOCAL int g_mlv_phase4bv2_log_emitted = 0;

static void mlv_phase4bv2_log_rejection(const char * reason)
{
    if (g_mlv_phase4bv2_log_env_cache < 0)
    {
        const char * v = getenv("MLVAPP_LOG_PHASE4BV2");
        g_mlv_phase4bv2_log_env_cache =
            (v && *v && strcmp(v, "0") != 0) ? 1 : 0;
    }
    if (!g_mlv_phase4bv2_log_env_cache) return;
    if (g_mlv_phase4bv2_log_emitted) return;
    g_mlv_phase4bv2_log_emitted = 1;
    fprintf(stderr, "[PHASE4BV2] reject: %s\n", reason);
    fflush(stderr);
}

/* Phase 4B-v3 (full XY pre-recon, optionally Y-cropped to 16-aligned):
 *
 * Runs pl_downsample_bayer_to_bayer_4x on (full_w, eff_h) where
 * eff_h = (full_h / 16) * 16 — the largest 16-aligned height <= full_h.
 * For clips where full_h is already 16-aligned this is identity (no rows
 * lost). For clips where it isn't (e.g. user's 5K M16-1210 at 1808x2268,
 * gcd=4), eff_h = 2256, losing 12 source rows from the bottom edge
 * (~0.5% Y loss, invisible at playback display resolution after the
 * existing bilinear stretch).
 *
 * After the kernel produces (full_w/4, eff_h/4) bayer, llrawproc and
 * debayer run on that smaller buffer. The output rows 0..eff_h/4-1 are
 * direct recon results; the trailing (full_h - eff_h)/4 rows of the
 * caller-sized output buffer are filled by replicating the last valid
 * row, so the downstream upscale doesn't see a black band.
 *
 * Returns 1 on success, 0 to fall back to the v2 X-only path. */
static int mlv_render_scaled_rgb16_v3_full_xy(mlvObject_t * video,
                                              uint64_t frameIndex,
                                              uint16_t * outputFrame,
                                              int threads,
                                              int * out_dn_ms_x100)
{
    const int full_w = (int)getMlvWidth(video);
    const int full_h = (int)getMlvHeight(video);

    /* in_w must be multiple of 4 (kernel constraint, same as v2).
     * eff_h must be multiple of 16 (kernel constraint), so we crop down to
     * the nearest 16-aligned height. eff_h must also be > 0; in practice
     * any sensible Dual ISO clip has full_h >= 16. */
    const int eff_h = (full_h / 16) * 16;
    if (full_w % 4 != 0 || eff_h < 16) return 0;

    g_mlv_phase4bv3_y_crop_rows = full_h - eff_h;

    /* Bayer→bayer 4x kernel output dim. */
    const int mid_w = full_w / 4;
    const int mid_h = eff_h / 4;
    /* Final output dim that the caller's buffer is sized for. */
    const int out_w = full_w / 4;
    const int out_h = full_h / 4;
    const uint64_t full_pixels = (uint64_t)full_w * (uint64_t)full_h;
    const uint64_t mid_pixels = (uint64_t)mid_w * (uint64_t)mid_h;
    const uint64_t mid_rgb_words = mid_pixels * 3u;
    const uint64_t out_rgb_words = (uint64_t)out_w * (uint64_t)out_h * 3u;

    uint16_t * full_bayer = mlv_ensure_thread_u16_buffer(full_pixels);
    uint16_t * mid_bayer = mlv_ensure_thread_scaled_bayer_buffer(mid_pixels);
    uint16_t * mid_rgb = mlv_ensure_thread_rgb_u16_buffer(mid_rgb_words);
    if (!full_bayer || !mid_bayer || !mid_rgb) return 0;

    /* Step 1: decode raw at full res (no llrawproc). */
    const double raw_start = mlv_stage_timing_now();
    if (getMlvRawFrameUint16(video, frameIndex, full_bayer))
    {
        memset(outputFrame, 0, (size_t)out_rgb_words * sizeof(uint16_t));
        return 0;
    }
    g_mlv_last_raw_uint16_ms = (mlv_stage_timing_now() - raw_start) * 1000.0;
    mlv_stage_timing_note_elapsed("raw_uint16", frameIndex, g_mlv_last_raw_uint16_ms);

    /* Step 2: bayer→bayer 4x (full XY) downsample, viewing the full bayer
     * as (full_w, eff_h). The kernel reads only rows 0..eff_h-1 — rows
     * eff_h..full_h-1 are simply ignored. (Reading less data than the
     * buffer holds is safe.) */
    const double downsample_start = mlv_stage_timing_now();
    int actual_mid_w = 0, actual_mid_h = 0;
    int rc = pl_downsample_bayer_to_bayer_4x(full_bayer, full_w, eff_h,
                                              mid_bayer, &actual_mid_w, &actual_mid_h, threads);
    if (rc != 0 || actual_mid_w != mid_w || actual_mid_h != mid_h)
    {
        mlv_phase4bv2_log_rejection("v3 bayer-to-bayer 4x kernel rejected");
        return 0;
    }
    const double downsample_ms = (mlv_stage_timing_now() - downsample_start) * 1000.0;

    /* Step 3: apply llrawproc subset on the narrowed buffer. Dimensions
     * (mid_w, mid_h) — the dual-ISO 4-row pattern is preserved by the
     * kernel because it samples complete 4-row blocks at stride 16 in
     * source space. */
    const double llraw_start = mlv_stage_timing_now();
    mlv_pipeline_capture_set_current_frame(frameIndex);
    const size_t mid_bytes = (size_t)mid_pixels * sizeof(uint16_t);
    int llraw_ok = applyLLRawProcObject_with_dims(video, mid_bayer, mid_bytes,
                                                  mid_w, mid_h);
    if (!llraw_ok)
    {
        mlv_phase4bv2_log_rejection("v3 applyLLRawProcObject_with_dims rejected");
        return 0;
    }
    const double llrawproc_ms = (mlv_stage_timing_now() - llraw_start) * 1000.0;
    mlv_stage_timing_note_elapsed("llrawproc", frameIndex, llrawproc_ms);
    g_mlv_last_llrawproc_ms = llrawproc_ms;

    /* Step 4: debayer narrowed bayer → RGB16 at (mid_w, mid_h) directly
     * into the head of the output buffer. (out_w == mid_w by design,
     * and mid_h <= out_h, so this fills rows 0..mid_h-1 of the output.) */
    const int bit_shift = llrpHQDualIso(video) ? 0 : (16 - video->RAWI.raw_info.bits_per_pixel);
    const double debayer_start = mlv_stage_timing_now();
    debayerBasicU16(outputFrame, mid_bayer, mid_w, mid_h, threads, bit_shift);
    const double debayer_ms = (mlv_stage_timing_now() - debayer_start) * 1000.0;

    /* Step 5: replicate the last valid row into rows mid_h..out_h-1 of the
     * output buffer. For 16-aligned clips mid_h == out_h and this loop
     * runs zero iterations. For the user's 1808x2268 clip mid_h=564,
     * out_h=567 — we replicate row 563 into rows 564, 565, 566. The
     * downstream bilinear upscale to display target then sees a 567-row
     * image; the last 3 rows are duplicates of row 563, contributing a
     * negligible band at the very bottom edge after stretch. */
    const size_t row_words = (size_t)out_w * 3u;
    if (mid_h > 0)
    {
        const uint16_t * last_row = outputFrame + (size_t)(mid_h - 1) * row_words;
        for (int y = mid_h; y < out_h; ++y)
        {
            uint16_t * dst = outputFrame + (size_t)y * row_words;
            memcpy(dst, last_row, row_words * sizeof(uint16_t));
        }
    }
    else
    {
        memset(outputFrame, 0, (size_t)out_rgb_words * sizeof(uint16_t));
    }

    g_mlv_last_debayered_frame_ms = downsample_ms + debayer_ms;
    mlv_stage_timing_note_elapsed("debayered_frame", frameIndex, g_mlv_last_debayered_frame_ms);
    if (out_dn_ms_x100)
    {
        *out_dn_ms_x100 = (int)(downsample_ms * 100.0);
    }
    return 1;
}

/* Phase 4B-v2: downsample-BEFORE-llrawproc path.
 *
 * Architecture (the actual cast-closed-fast path):
 *   1. Decode raw at full res (LJ92 / unpacked uint16 bayer).
 *   2. Bayer→bayer 4x downsample. Phase 4B-v3 runs the FULL XY downsample
 *      (Y reduced 4x in pre-recon) when in_h is a multiple of 16 OR can
 *      be cropped down to the nearest 16-aligned height with negligible
 *      visual loss. Otherwise falls back to the v2 X-only kernel
 *      (Y identity).
 *   3. Apply HQ Dual ISO recon + dark frame + chroma smooth on the
 *      narrowed buffer.
 *   4. Debayer the narrowed bayer → RGB16.
 *   5. (X-only fallback only) RGB Y-only 4x downsample to final dim.
 *
 * Why the v3 wrapper: the dual-ISO 4-row bright/dark pattern uses
 * is_bright[y%4] inside diso_get_full20bit. Y downsampling pre-recon
 * requires keeping COMPLETE 4-row blocks at stride 16 in source space
 * (so the kept row indices modulo 4 match the iso_patterns cycle). For
 * arbitrary in_h the largest stride that divides in_h is gcd(in_h, 16).
 * For the user's M16-1210 (1808x2268), gcd(2268, 16) = 4 — meaning
 * Y stride 4 = no Y reduction in the v2 X-only fallback. v3 instead
 * crops to eff_h = 2256 (the largest 16-aligned <= 2268), losing 12
 * source rows from the bottom edge, and runs the full XY downsample.
 *
 * Speedup at v3 (full XY) on 5K: HQ recon scales linearly with W*H.
 * Going from W*H to (W/4)*(H/4) saves ~94% of recon time vs the v1
 * post-recon path; ~75% vs the v2 X-only path. The Y-crop is the
 * architectural change needed to push cast-closed playback past
 * ~15 fps GUI on the user's clip.
 *
 * Returns 1 on success, 0 if the receipt is incompatible (caller falls
 * back to v1). */
static int mlv_render_scaled_rgb16_v2(mlvObject_t * video,
                                      uint64_t frameIndex,
                                      uint16_t * outputFrame,
                                      int scaleFactor,
                                      int threads)
{
    g_mlv_phase4bv2_path_taken = 0;
    g_mlv_phase4bv3_y_crop_rows = 0;

    if (!video || !outputFrame || scaleFactor <= 1) return 0;
    if (mlv_phase4bv2_disabled_via_env())
    {
        mlv_phase4bv2_log_rejection("MLVAPP_DISABLE_PHASE4BV2 set");
        return 0;
    }
    if (!mlv_phase4bv2_receipt_compatible(video))
    {
        llrawprocObject_t * shared = video ? video->llrawproc : NULL;
        if (!shared) mlv_phase4bv2_log_rejection("no shared");
        else if (shared->focus_pixels) mlv_phase4bv2_log_rejection("focus_pixels enabled");
        else if (shared->bad_pixels) mlv_phase4bv2_log_rejection("bad_pixels enabled");
        else if (shared->vertical_stripes) mlv_phase4bv2_log_rejection("vertical_stripes enabled");
        else if (shared->pattern_noise) mlv_phase4bv2_log_rejection("pattern_noise enabled");
        else mlv_phase4bv2_log_rejection("receipt incompatible (unknown)");
        return 0;
    }
    if (scaleFactor != 4)
    {
        /* scale=2 falls back to v1; the X-only-pre-recon savings only
         * pay off at scale=4 (X reduction by 4 = 75% recon save). */
        return 0;
    }

    const int full_w = (int)getMlvWidth(video);
    const int full_h = (int)getMlvHeight(video);
    if (full_w <= 0 || full_h <= 0)
    {
        mlv_phase4bv2_log_rejection("invalid dims");
        return 0;
    }
    if (full_w % 4 != 0)
    {
        mlv_phase4bv2_log_rejection("width not multiple of 4");
        return 0;
    }
    if (full_h % 4 != 0)
    {
        mlv_phase4bv2_log_rejection("height not multiple of 4");
        return 0;
    }

    /* Phase 4B-v3: try the full XY pre-recon path first. Eligible whenever
     * full_h >= 16 (so eff_h = floor(full_h/16)*16 >= 16) and the v3 kill
     * switch is not set. The path runs the recon on (W/4, eff_h/4) — for
     * 5K Dual ISO (1808x2268) that's (452, 564) = 254,928 pixels, vs the
     * X-only path's (452, 2268) = 1,025,136 pixels. */
    if (!mlv_phase4bv3_disabled_via_env() && full_h >= 16)
    {
        int dn_x100 = 0;
        if (mlv_render_scaled_rgb16_v3_full_xy(video, frameIndex, outputFrame, threads, &dn_x100))
        {
            g_mlv_phase4bv2_path_taken = 3;
            return 1;
        }
        /* v3 path attempted and rejected — fall through to v2 X-only. */
    }

    /* Phase 4B-v2 X-only fallback. Reached when v3 is disabled, full_h < 16,
     * the v3 kernel rejected (kernel-level dim check fails), or the
     * llrawproc recon at the cropped+downsampled dim failed. */

    /* Intermediate dim: X reduced by 4, Y identity. */
    const int mid_w = full_w / 4;
    const int mid_h = full_h;
    /* Final output dim: scale=4 in both. */
    const int out_w = full_w / 4;
    const int out_h = full_h / 4;
    const uint64_t full_pixels = (uint64_t)full_w * (uint64_t)full_h;
    const uint64_t mid_pixels = (uint64_t)mid_w * (uint64_t)mid_h;
    const uint64_t mid_rgb_words = mid_pixels * 3u;
    const uint64_t out_rgb_words = (uint64_t)out_w * (uint64_t)out_h * 3u;

    uint16_t * full_bayer = mlv_ensure_thread_u16_buffer(full_pixels);
    uint16_t * mid_bayer = mlv_ensure_thread_scaled_bayer_buffer(mid_pixels);
    /* Reuse the rgb_u16 thread-local for the post-debayer mid-RGB buffer.
     * Its capacity grows on demand; oversize for our (mid_w,mid_h) case
     * is harmless. */
    uint16_t * mid_rgb = mlv_ensure_thread_rgb_u16_buffer(mid_rgb_words);
    if (!full_bayer || !mid_bayer || !mid_rgb) return 0;

    /* Step 1: decode raw at full res (no llrawproc). */
    const double raw_start = mlv_stage_timing_now();
    if (getMlvRawFrameUint16(video, frameIndex, full_bayer))
    {
        memset(outputFrame, 0, (size_t)out_rgb_words * sizeof(uint16_t));
        return 0;
    }
    g_mlv_last_raw_uint16_ms = (mlv_stage_timing_now() - raw_start) * 1000.0;
    mlv_stage_timing_note_elapsed("raw_uint16", frameIndex, g_mlv_last_raw_uint16_ms);

    /* Step 2: bayer→bayer X-only 4x downsample. */
    const double downsample_start = mlv_stage_timing_now();
    int actual_mid_w = 0, actual_mid_h = 0;
    int rc = pl_downsample_bayer_to_bayer_4x_x_only(full_bayer, full_w, full_h,
                                                     mid_bayer, &actual_mid_w, &actual_mid_h, threads);
    if (rc != 0 || actual_mid_w != mid_w || actual_mid_h != mid_h)
    {
        mlv_phase4bv2_log_rejection("bayer-to-bayer x-only kernel rejected");
        return 0;
    }
    const double downsample_ms = (mlv_stage_timing_now() - downsample_start) * 1000.0;

    /* Step 3: apply llrawproc subset (HQ recon, dark frame, chroma smooth)
     * on the narrowed buffer. */
    const double llraw_start = mlv_stage_timing_now();
    mlv_pipeline_capture_set_current_frame(frameIndex);
    const size_t mid_bytes = (size_t)mid_pixels * sizeof(uint16_t);
    int llraw_ok = applyLLRawProcObject_with_dims(video, mid_bayer, mid_bytes,
                                                  mid_w, mid_h);
    if (!llraw_ok)
    {
        mlv_phase4bv2_log_rejection("applyLLRawProcObject_with_dims rejected");
        return 0;
    }
    const double llrawproc_ms = (mlv_stage_timing_now() - llraw_start) * 1000.0;
    mlv_stage_timing_note_elapsed("llrawproc", frameIndex, llrawproc_ms);
    g_mlv_last_llrawproc_ms = llrawproc_ms;

    /* Step 4: debayer narrowed bayer → RGB16 at (mid_w, mid_h). */
    const int bit_shift = llrpHQDualIso(video) ? 0 : (16 - video->RAWI.raw_info.bits_per_pixel);
    const double debayer_start = mlv_stage_timing_now();
    debayerBasicU16(mid_rgb, mid_bayer, mid_w, mid_h, threads, bit_shift);

    /* Step 5: RGB Y-only 4x downsample → final (out_w, out_h). */
    int actual_out_w = 0, actual_out_h = 0;
    int rc2 = pl_downsample_rgb_to_rgb_4x_y_only(mid_rgb, mid_w, mid_h,
                                                  outputFrame, &actual_out_w, &actual_out_h, threads);
    if (rc2 != 0 || actual_out_w != out_w || actual_out_h != out_h)
    {
        mlv_phase4bv2_log_rejection("post-recon Y-only RGB downsample rejected");
        return 0;
    }
    const double debayer_ms = (mlv_stage_timing_now() - debayer_start) * 1000.0;
    g_mlv_last_debayered_frame_ms = downsample_ms + debayer_ms;
    mlv_stage_timing_note_elapsed("debayered_frame", frameIndex, g_mlv_last_debayered_frame_ms);
    g_mlv_phase4bv2_path_taken = 2;
    return 1;
}

/* Phase 4B: scaled-resolution debayered-RGB16 producer.
 *
 * Phase 4B-v2 path (preferred when compatible): see
 * mlv_render_scaled_rgb16_v2 above — runs the HQ Dual ISO recon on the
 * downsampled bayer (1/16 the pixels at scale=4).
 *
 * Phase 4B-v1 fallback: full LJ92 + full llrawproc + post-downsample. Used
 * when the receipt enables features (focus pixel, bad pixel, vstripes,
 * pattern noise) that require full-res absolute coordinates. The output is
 * already debayered (per-channel block average) so the debayer step is
 * bypassed entirely.
 *
 * Returns 1 on success, 0 on failure (then outputFrame is zeroed). The
 * scaleFactor must be the *effective* scale (caller already ran it
 * through mlv_effective_playback_scale_factor — typically 2 or 4). */
static int mlv_render_scaled_rgb16(mlvObject_t * video,
                                   uint64_t frameIndex,
                                   uint16_t * outputFrame,
                                   int scaleFactor,
                                   int threads)
{
    if (!video || !outputFrame || scaleFactor <= 1) return 0;

    /* Try Phase 4B-v2 first. */
    if (mlv_render_scaled_rgb16_v2(video, frameIndex, outputFrame, scaleFactor, threads))
    {
        return 1;
    }

    const int width  = (int)getMlvWidth(video);
    const int height = (int)getMlvHeight(video);
    const int out_w  = width  / scaleFactor;
    const int out_h  = height / scaleFactor;
    const uint64_t in_pixels = (uint64_t)width * (uint64_t)height;
    const uint64_t out_words = (uint64_t)out_w * (uint64_t)out_h * 3u;

    /* Borrow a thread-local buffer for the full-res Bayer. The TLS
     * buffer grows on demand; once allocated for the full sensor it's
     * reused across calls. */
    uint16_t * unpacked = mlv_ensure_thread_u16_buffer(in_pixels);
    if (!unpacked)
    {
        memset(outputFrame, 0, (size_t)out_words * sizeof(uint16_t));
        return 0;
    }

    int bit_shift = 0;
    const double debayer_start = mlv_stage_timing_now();
    if (getMlvRawFrameProcessedUint16(video, frameIndex, unpacked, &bit_shift))
    {
        memset(outputFrame, 0, (size_t)out_words * sizeof(uint16_t));
        return 0;
    }

    /* Per scope plan: bit_shift widens narrow-bitdepth bayer to 16-bit.
     * For HQ Dual ISO recon, llrawproc has already widened to 16-bit
     * (bit_shift==0). For non-recon paths bit_shift may be non-zero. */
    if (scaleFactor == 4)
    {
        pl_downsample_bayer_to_rgb_4x(unpacked, width, height, outputFrame, bit_shift, threads);
    }
    else
    {
        pl_downsample_bayer_to_rgb_2x(unpacked, width, height, outputFrame, bit_shift, threads);
    }
    g_mlv_last_debayered_frame_ms = (mlv_stage_timing_now() - debayer_start) * 1000.0;
    mlv_stage_timing_note_elapsed("debayered_frame", frameIndex, g_mlv_last_debayered_frame_ms);
    return 1;
}

void getMlvRawFrameDebayered(mlvObject_t * video, uint64_t frameIndex, uint16_t * outputFrame)
{
    int width = getMlvWidth(video);
    int height = getMlvHeight(video);
    int frame_size = width * height * sizeof(uint16_t) * 3;
    uint64_t pixels_count = (uint64_t)width * height;
    mlv_reset_last_raw_stage_telemetry();
    resetMlvLastDebayerStageMilliseconds();
    mlv_cache_ensure_window(video, frameIndex);
    int cache_window_active = mlv_frame_in_cache_window(video, frameIndex);

    /* If frame was requested last time and is sitting in the "current" frame cache */
    if ( video->cached_frames[frameIndex] == MLV_FRAME_NOT_CACHED
         && video->current_cached_frame_active 
         && video->current_cached_frame == frameIndex )
    {
        memcpy(outputFrame, video->rgb_raw_current_frame, frame_size);
    }
    /* Is this next bit even readable? */
    else switch (video->cached_frames[frameIndex])
    {
        case MLV_FRAME_IS_CACHED:
        {
            if (cache_window_active)
            {
                memcpy(outputFrame, video->rgb_raw_frames[mlv_cache_slot_for_frame(video, frameIndex)], frame_size);
                break;
            }
            video->cached_frames[frameIndex] = MLV_FRAME_NOT_CACHED;
            /* fall through */
        }

        case MLV_FRAME_NOT_CACHED:
        {
            /* If it is within the cache range, request for it to be cached */
            if (cache_window_active)
            {
                video->cache_next = frameIndex;
            }
            /* fall through */
        }

        case MLV_FRAME_BEING_CACHED:
        {
            if (doesMlvAlwaysUseAmaze(video) && isMlvObjectCaching(video))
            {
                while (video->cached_frames[frameIndex] != MLV_FRAME_IS_CACHED) usleep(100);
                if (mlv_frame_in_cache_window(video, frameIndex))
                {
                    memcpy(outputFrame, video->rgb_raw_frames[mlv_cache_slot_for_frame(video, frameIndex)], frame_size);
                    break;
                }
            }

            float * raw_frame = mlv_ensure_float_buffer(&video->raw_debayer_temp_frame,
                                                        &video->raw_debayer_temp_frame_pixels,
                                                        pixels_count);
            uint16_t * current_frame = mlv_ensure_u16_buffer(&video->rgb_raw_current_frame,
                                                             &video->rgb_raw_current_frame_words,
                                                             pixels_count * 3);
            if (!raw_frame || !current_frame)
            {
                memset(outputFrame, 0, frame_size);
                video->current_cached_frame_active = 0;
                break;
            }

            get_mlv_raw_frame_debayered(video, frameIndex, raw_frame, current_frame, doesMlvAlwaysUseAmaze(video));
            memcpy(outputFrame, video->rgb_raw_current_frame, frame_size);
            video->current_cached_frame_active = 1;
            video->current_cached_frame = frameIndex;
            break;
        }
    }
}

static void mlv_sync_processing_black_white_levels(mlvObject_t * video)
{
    const int desired_bit_depth = llrpHQDualIso(video)
        ? video->llrawproc->dng_bit_depth
        : getMlvBitdepth(video);
    const float desired_black_level = llrpHQDualIso(video)
        ? (float)video->llrawproc->dng_black_level
        : (float)getMlvBlackLevel(video);
    const int desired_white_level = llrpHQDualIso(video)
        ? video->llrawproc->dng_white_level
        : getMlvWhiteLevel(video);
    const int bits_shift = 16 - desired_bit_depth;
    const int expected_black_level =
        (desired_black_level > 0.0f)
            ? (int)(desired_black_level * pow(2.0, bits_shift))
            : 0;
    const int expected_white_level =
        (int)((double)(desired_white_level << bits_shift) * 0.993);

    if ((int)video->processing->black_level != expected_black_level
     || video->processing->white_level != expected_white_level)
    {
        processingSetBlackAndWhiteLevel(video->processing,
                                        desired_black_level,
                                        desired_white_level,
                                        desired_bit_depth);
    }
}

static int mlv_can_use_direct_processed_frame8_path(mlvObject_t * video)
{
    return video
        && video->processing
        && processingCanUseDirect8BitOutput(video->processing);
}

static int mlv_render_processed_frame8_direct_with_processing(mlvObject_t * video,
                                                              processingObject_t * processing,
                                                              int syncProcessingLevels,
                                                              uint64_t frameIndex,
                                                              uint8_t * outputFrame,
                                                              int threads,
                                                              int recordTelemetry)
{
    /* Phase 4B: scale resolution. video->playback_scale_factor_active is
     * the *effective* scale set by the *_with_scale entry points. */
    const int scaleFactor = video ? video->playback_scale_factor_active : 1;
    const int eff_scale = (scaleFactor > 1) ? scaleFactor : 1;
    const int full_w = (int)getMlvWidth(video);
    const int full_h = (int)getMlvHeight(video);
    const int out_w = (eff_scale > 1) ? (full_w / eff_scale) : full_w;
    const int out_h = (eff_scale > 1) ? (full_h / eff_scale) : full_h;

    const uint64_t rgb_frame_size = (uint64_t)out_w * (uint64_t)out_h * 3;
    const uint64_t pixels_count = (uint64_t)full_w * (uint64_t)full_h;
    float * raw_frame = mlv_ensure_thread_float_buffer(pixels_count);
    /* The TLS rgb_u16 buffer grows to the largest size requested across
     * calls; oversize for scaled output is harmless (we only fill the
     * scaled-W * scaled-H * 3 prefix). */
    const uint64_t rgb_buf_words = (eff_scale > 1) ? rgb_frame_size : ((uint64_t)full_w * (uint64_t)full_h * 3u);
    uint16_t * unprocessed_frame = mlv_ensure_thread_rgb_u16_buffer(rgb_buf_words);
    if (!raw_frame || !unprocessed_frame || !processing)
    {
        memset(outputFrame, 0, (size_t)rgb_frame_size);
        return 0;
    }

    const double processed16_start = recordTelemetry ? mlv_stage_timing_now() : 0.0;
    const double debayer_start = recordTelemetry ? mlv_stage_timing_now() : 0.0;
    if (eff_scale > 1)
    {
        /* Phase 4B: fused downsample-and-debayer at scale > 1. The
         * unprocessed_frame buffer is filled with scaled RGB16 directly;
         * the debayer step is bypassed entirely. */
        if (!mlv_render_scaled_rgb16(video, frameIndex, unprocessed_frame, eff_scale, threads))
        {
            memset(outputFrame, 0, (size_t)rgb_frame_size);
            return 0;
        }
    }
    else
    {
        get_mlv_raw_frame_debayered(video,
                                    frameIndex,
                                    raw_frame,
                                    unprocessed_frame,
                                    doesMlvAlwaysUseAmaze(video));
    }
    if (recordTelemetry)
    {
        g_mlv_last_debayered_frame_ms = (mlv_stage_timing_now() - debayer_start) * 1000.0;
        mlv_stage_timing_note_elapsed("debayered_frame", frameIndex, g_mlv_last_debayered_frame_ms);
    }

    if (syncProcessingLevels)
    {
        mlv_sync_processing_black_white_levels(video);
    }

    const double processing_start = recordTelemetry ? mlv_stage_timing_now() : 0.0;
    applyProcessingObject8(processing,
                           out_w,
                           out_h,
                           unprocessed_frame,
                           outputFrame,
                           threads,
                           1,
                           frameIndex);
    if (recordTelemetry)
    {
        g_mlv_last_processing_ms = (mlv_stage_timing_now() - processing_start) * 1000.0;
        mlv_stage_timing_note_elapsed("processing", frameIndex, g_mlv_last_processing_ms);
        g_mlv_last_processed16_total_ms = (mlv_stage_timing_now() - processed16_start) * 1000.0;
        g_mlv_last_processed16_for_8bit_ms = g_mlv_last_processed16_total_ms;
        g_mlv_last_processed16_to_8bit_ms = 0.0;
        g_mlv_last_processed8_direct_path_active = 1;
        mlv_stage_timing_note_elapsed("processed16_total", frameIndex, g_mlv_last_processed16_total_ms);
        mlv_stage_timing_note_elapsed("processed16_for_8bit", frameIndex, g_mlv_last_processed16_for_8bit_ms);
        mlv_stage_timing_note_elapsed("processed16_to_8bit", frameIndex, g_mlv_last_processed16_to_8bit_ms);
    }

    return 1;
}

static int mlv_render_processed_frame8_direct(mlvObject_t * video,
                                              uint64_t frameIndex,
                                              uint8_t * outputFrame,
                                              int threads,
                                              int recordTelemetry)
{
    return mlv_render_processed_frame8_direct_with_processing(video,
                                                              video ? video->processing : NULL,
                                                              1,
                                                              frameIndex,
                                                              outputFrame,
                                                              threads,
                                                              recordTelemetry);
}

/* Get a processed frame in 16 bit, only use more than one thread for preview as
 * it may have minor artifacts (though I haven't found them yet) */
static void getMlvProcessedFrame16_with_scale(mlvObject_t * video,
                                              uint64_t frameIndex,
                                              uint16_t * outputFrame,
                                              int threads,
                                              int scaleFactor)
{
    const double total_start = mlv_stage_timing_now();
    mlv_reset_last_raw_stage_telemetry();
    g_mlv_last_llrawproc_ms = 0.0;
    g_mlv_last_debayered_frame_ms = 0.0;
    g_mlv_last_processing_ms = 0.0;
    g_mlv_last_processed16_total_ms = 0.0;
    g_mlv_last_processed8_direct_path_active = 0;

    /* Phase 4B: resolve effective scale (clamps dual ISO scale=2 up to 4,
     * rejects scales that don't divide the sensor evenly). The cache key
     * uses the *effective* scale so a request that gets clamped doesn't
     * collide with a different request that lands at the same scale. */
    const int normalizedScale = mlv_effective_playback_scale_factor(video, scaleFactor);
    if (video)
    {
        video->playback_scale_factor_active = normalizedScale;
    }

    /* Phase 4B: width/height are the OUTPUT (scaled) dimensions for the
     * downstream processing call and the cache. The full sensor
     * dimensions are still required by the downsample kernel; we capture
     * those in full_w / full_h. */
    const int full_w = (int)getMlvWidth(video);
    const int full_h = (int)getMlvHeight(video);
    int width  = (normalizedScale > 1) ? (full_w / normalizedScale) : full_w;
    int height = (normalizedScale > 1) ? (full_h / normalizedScale) : full_h;

    /* Size of RAW frame */
    uint64_t rgb_frame_size = (uint64_t)height * width * 3;
    uint64_t requested_signature = mlv_processed_frame_signature_with_scale(video,
                                                                            frameIndex,
                                                                            normalizedScale);

    if (video->current_processed_frame_active
        && video->current_processed_frame == frameIndex
        && video->current_processed_frame_threads == threads
        && video->current_processed_frame_signature == requested_signature
        && video->rgb_processed_current_frame)
    {
        if (outputFrame != video->rgb_processed_current_frame)
        {
            memcpy(outputFrame, video->rgb_processed_current_frame, (size_t)rgb_frame_size * sizeof(uint16_t));
        }
        g_mlv_last_processed16_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
        mlv_stage_timing_note("processed16_total", frameIndex, total_start);
        return;
    }

    int cached_slot = mlv_find_processed_frame_16bit_cache_slot_with_scale(video,
                                                                           frameIndex,
                                                                           threads,
                                                                           requested_signature,
                                                                           normalizedScale);
    if (cached_slot >= 0)
    {
        uint16_t * cached_frame = mlv_processed_frame_16bit_cache_slot(video, (uint32_t)cached_slot, rgb_frame_size);
        if (cached_frame)
        {
            if (outputFrame != cached_frame)
            {
                memcpy(outputFrame, cached_frame, (size_t)rgb_frame_size * sizeof(uint16_t));
            }

            uint16_t * exact_cache = mlv_ensure_u16_buffer(&video->rgb_processed_current_frame,
                                                           &video->rgb_processed_current_frame_words,
                                                           rgb_frame_size);
            if (exact_cache)
            {
                if (exact_cache != cached_frame)
                {
                    memcpy(exact_cache, cached_frame, (size_t)rgb_frame_size * sizeof(uint16_t));
                }
                video->current_processed_frame_active = 1;
                video->current_processed_frame = frameIndex;
                video->current_processed_frame_threads = threads;
                video->current_processed_frame_signature = requested_signature;
            }
            else
            {
                video->current_processed_frame_active = 0;
                video->current_processed_frame_signature = 0;
            }

            g_mlv_last_processed16_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
            mlv_stage_timing_note("processed16_total", frameIndex, total_start);
            return;
        }

        mlv_reset_processed_frame_16bit_cache(video);
    }

    /* Unprocessed debayered frame (RGB) */
    uint16_t * unprocessed_frame = mlv_ensure_u16_buffer(&video->rgb_processed_temp_frame,
                                                         &video->rgb_processed_temp_frame_words,
                                                         rgb_frame_size);
    if (!unprocessed_frame)
    {
        memset(outputFrame, 0, (size_t)rgb_frame_size * sizeof(uint16_t));
        video->current_processed_frame_active = 0;
        g_mlv_last_processed16_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
        mlv_stage_timing_note("processed16_total", frameIndex, total_start);
        return;
    }

    /* Phase 4B: at scale > 1, route through the fused
     * downsample-and-debayer that produces RGB16 directly at scaled
     * dimensions. The debayer step is skipped entirely on this path. */
    if (normalizedScale > 1)
    {
        if (!mlv_render_scaled_rgb16(video, frameIndex, unprocessed_frame, normalizedScale, threads))
        {
            memset(outputFrame, 0, (size_t)rgb_frame_size * sizeof(uint16_t));
            video->current_processed_frame_active = 0;
            g_mlv_last_processed16_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
            mlv_stage_timing_note("processed16_total", frameIndex, total_start);
            return;
        }
    }
    else
    {
        const double debayer_start = mlv_stage_timing_now();
        getMlvRawFrameDebayered(video, frameIndex, unprocessed_frame);
        g_mlv_last_debayered_frame_ms = (mlv_stage_timing_now() - debayer_start) * 1000.0;
        mlv_stage_timing_note_elapsed("debayered_frame", frameIndex, g_mlv_last_debayered_frame_ms);
    }

    mlv_sync_processing_black_white_levels(video);

    /* Do processing.......... */
    const double processing_start = mlv_stage_timing_now();
    applyProcessingObject( video->processing,
                           width, height,
                           unprocessed_frame,
                           outputFrame,
                           threads, 1, frameIndex );
    g_mlv_last_processing_ms = (mlv_stage_timing_now() - processing_start) * 1000.0;
    mlv_stage_timing_note_elapsed("processing", frameIndex, g_mlv_last_processing_ms);

    const uint64_t final_signature = mlv_processed_frame_signature_with_scale(video,
                                                                               frameIndex,
                                                                               normalizedScale);

    uint16_t * processed_cache = mlv_ensure_u16_buffer(&video->rgb_processed_current_frame,
                                                       &video->rgb_processed_current_frame_words,
                                                       rgb_frame_size);
    if (processed_cache)
    {
        if (outputFrame != processed_cache)
        {
            memcpy(processed_cache, outputFrame, (size_t)rgb_frame_size * sizeof(uint16_t));
        }
        video->current_processed_frame_active = 1;
        video->current_processed_frame = frameIndex;
        video->current_processed_frame_threads = threads;
        video->current_processed_frame_signature = final_signature;
    }
    else
    {
        video->current_processed_frame_active = 0;
        video->current_processed_frame_signature = 0;
    }

    mlv_store_processed_frame_16bit_cache_with_scale(video,
                                                     frameIndex,
                                                     threads,
                                                     final_signature,
                                                     outputFrame,
                                                     rgb_frame_size,
                                                     normalizedScale);

    g_mlv_last_processed16_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
    mlv_stage_timing_note("processed16_total", frameIndex, total_start);
}

/* Phase 4A: scale-1 entrypoint preserves the original public API. */
void getMlvProcessedFrame16(mlvObject_t * video, uint64_t frameIndex, uint16_t * outputFrame, int threads)
{
    getMlvProcessedFrame16_with_scale(video, frameIndex, outputFrame, threads, 1);
}

/* Phase 4A: scale-aware variant. The pipeline still renders at full
 * resolution; scaleFactor only affects cache-key isolation. */
void getMlvProcessedFrame16Scaled(mlvObject_t * video,
                                  uint64_t frameIndex,
                                  uint16_t * outputFrame,
                                  int threads,
                                  int scaleFactor)
{
    getMlvProcessedFrame16_with_scale(video, frameIndex, outputFrame, threads, scaleFactor);
}

/* Get a processed frame in 8 bit */
static void getMlvProcessedFrame8_with_scale(mlvObject_t * video,
                                             uint64_t frameIndex,
                                             uint8_t * outputFrame,
                                             int threads,
                                             int scaleFactor)
{
    const double total_start = mlv_stage_timing_now();
    mlv_reset_last_raw_stage_telemetry();
    g_mlv_last_llrawproc_ms = 0.0;
    g_mlv_last_debayered_frame_ms = 0.0;
    g_mlv_last_processing_ms = 0.0;
    g_mlv_last_processed16_total_ms = 0.0;
    g_mlv_last_processed16_for_8bit_ms = 0.0;
    g_mlv_last_processed16_to_8bit_ms = 0.0;
    g_mlv_last_processed8_total_ms = 0.0;
    g_mlv_last_processed8_direct_path_active = 0;
    g_mlv_last_processed8_prefetch_hit = 0;

    /* Phase 4B: resolve effective scale (clamps dual ISO scale=2 up to 4,
     * rejects scales that don't divide the sensor evenly). */
    const int normalizedScale = mlv_effective_playback_scale_factor(video, scaleFactor);
    if (video)
    {
        video->playback_scale_factor_active = normalizedScale;
    }

    /* Phase 4B: size the output by *effective* scale, not full sensor.
     * The downstream direct8 path reads playback_scale_factor_active to
     * decide whether to invoke the fused downsample. */
    const int full_w = (int)getMlvWidth(video);
    const int full_h = (int)getMlvHeight(video);
    const int out_w  = (normalizedScale > 1) ? (full_w / normalizedScale) : full_w;
    const int out_h  = (normalizedScale > 1) ? (full_h / normalizedScale) : full_h;
    uint64_t rgb_frame_size = (uint64_t)out_w * (uint64_t)out_h * 3u;
    uint16_t * processed_frame = NULL;
    const int direct8PathActive = mlv_can_use_direct_processed_frame8_path(video);
    const int processed8PrefetchActive =
        direct8PathActive && mlv_processed8_prefetch_enabled();
    uint64_t requested_state_signature = 0;

    if (direct8PathActive)
    {
        mlv_sync_processing_black_white_levels(video);
        requested_state_signature = mlv_processed_frame_state_signature_with_scale(video,
                                                                                   normalizedScale);
        if (processed8PrefetchActive)
        {
            mlv_processed8_prefetch_note_request(video,
                                                frameIndex,
                                                threads,
                                                requested_state_signature,
                                                normalizedScale);
        }
    }

    uint64_t requested_signature = direct8PathActive
        ? mlv_processed_frame_signature_from_state(requested_state_signature, frameIndex)
        : mlv_processed_frame_signature_with_scale(video, frameIndex, normalizedScale);

    int prefetched_hit = 0;
    if (mlv_processed_frame_8bit_cache_try_copy(video,
                                                frameIndex,
                                                threads,
                                                requested_signature,
                                                processed8PrefetchActive,
                                                outputFrame,
                                                rgb_frame_size,
                                                normalizedScale,
                                                &prefetched_hit))
    {
        g_mlv_last_processed8_prefetch_hit = prefetched_hit;
        g_mlv_last_processed8_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
        mlv_stage_timing_note("processed8_total", frameIndex, total_start);
        return;
    }

    if (direct8PathActive)
    {
        if (!mlv_render_processed_frame8_direct(video, frameIndex, outputFrame, threads, 1))
        {
            pthread_mutex_lock(&video->processed8_prefetch_mutex);
            video->current_processed_frame_8bit_active = 0;
            mlv_reset_processed_frame_8bit_cache_locked(video);
            pthread_mutex_unlock(&video->processed8_prefetch_mutex);
            g_mlv_last_processed8_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
            mlv_stage_timing_note("processed8_total", frameIndex, total_start);
            return;
        }

        mlv_store_processed_frame_8bit_cache_with_scale(video,
                                                        frameIndex,
                                                        threads,
                                                        requested_signature,
                                                        outputFrame,
                                                        rgb_frame_size,
                                                        1,
                                                        0,
                                                        normalizedScale);

        /* S5_processed8 capture (direct8 fast path) */
        if (mlv_pipeline_capture_should_capture_frame(frameIndex))
        {
            mlv_pipeline_capture_meta_t meta;
            memset(&meta, 0, sizeof meta);
            meta.stage = MLV_PIPELINE_STAGE_S5_PROCESSED8;
            meta.format = MLV_PIPELINE_FORMAT_UINT8_RGB;
            meta.format_label = "uint8_rgb_direct8";
            meta.width = out_w;
            meta.height = out_h;
            meta.bytes_per_line = out_w * 3;
            meta.bytes_per_pixel = 3;
            meta.channels = 3;
            meta.bit_depth = 8;
            meta.scaler = (normalizedScale > 1) ? "playback_downsample" : "none";
            meta.path_label = "direct8";
            meta.settings_hash = (uint64_t)requested_signature;
            mlv_pipeline_capture(frameIndex, outputFrame, &meta);
        }

        g_mlv_last_processed8_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
        mlv_stage_timing_note_elapsed("processed8_total", frameIndex, g_mlv_last_processed8_total_ms);
        return;
    }

    if (video->current_processed_frame_active
        && video->current_processed_frame == frameIndex
        && video->current_processed_frame_threads == threads
        && video->current_processed_frame_signature == requested_signature
        && video->rgb_processed_current_frame)
    {
        processed_frame = video->rgb_processed_current_frame;
    }
    else
    {
        processed_frame = mlv_ensure_u16_buffer(&video->rgb_processed_current_frame,
                                                &video->rgb_processed_current_frame_words,
                                                rgb_frame_size);
        if (!processed_frame)
        {
            memset(outputFrame, 0, (size_t)rgb_frame_size);
            video->current_processed_frame_active = 0;
            pthread_mutex_lock(&video->processed8_prefetch_mutex);
            mlv_reset_processed_frame_8bit_cache_locked(video);
            pthread_mutex_unlock(&video->processed8_prefetch_mutex);
            g_mlv_last_processed8_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
            mlv_stage_timing_note("processed8_total", frameIndex, total_start);
            return;
        }
    }

    const double processed16_start = mlv_stage_timing_now();
    getMlvProcessedFrame16_with_scale(video, frameIndex, processed_frame, threads, normalizedScale);
    g_mlv_last_processed16_for_8bit_ms = (mlv_stage_timing_now() - processed16_start) * 1000.0;
    mlv_stage_timing_note_elapsed("processed16_for_8bit", frameIndex, g_mlv_last_processed16_for_8bit_ms);

    if (video->current_processed_frame_active
        && video->current_processed_frame == frameIndex
        && video->current_processed_frame_threads == threads
        && video->current_processed_frame_signature == mlv_processed_frame_signature_with_scale(video, frameIndex, normalizedScale)
        && video->rgb_processed_current_frame)
    {
        processed_frame = video->rgb_processed_current_frame;
    }

    /* Copy (and 8-bitize) */
    const double convert_start = mlv_stage_timing_now();
    #pragma omp parallel for
    for (uint64_t i = 0; i < rgb_frame_size; ++i)
    {
        outputFrame[i] = processed_frame[i] >> 8;
    }
    g_mlv_last_processed16_to_8bit_ms = (mlv_stage_timing_now() - convert_start) * 1000.0;
    mlv_stage_timing_note_elapsed("processed16_to_8bit", frameIndex, g_mlv_last_processed16_to_8bit_ms);

    /* S5_processed8 capture (indirect path: processed16 -> 8) */
    if (mlv_pipeline_capture_should_capture_frame(frameIndex))
    {
        mlv_pipeline_capture_meta_t meta;
        memset(&meta, 0, sizeof meta);
        meta.stage = MLV_PIPELINE_STAGE_S5_PROCESSED8;
        meta.format = MLV_PIPELINE_FORMAT_UINT8_RGB;
        meta.format_label = "uint8_rgb_processed16_packdown";
        meta.width = out_w;
        meta.height = out_h;
        meta.bytes_per_line = out_w * 3;
        meta.bytes_per_pixel = 3;
        meta.channels = 3;
        meta.bit_depth = 8;
        meta.scaler = (normalizedScale > 1) ? "playback_downsample" : "none";
        meta.path_label = "processed16_to_8";
        meta.settings_hash =
            (uint64_t)mlv_processed_frame_signature_with_scale(video, frameIndex, normalizedScale);
        mlv_pipeline_capture(frameIndex, outputFrame, &meta);
    }

    mlv_store_processed_frame_8bit_cache_with_scale(
        video,
        frameIndex,
        threads,
        video->current_processed_frame_active
            ? video->current_processed_frame_signature
            : mlv_processed_frame_signature_with_scale(video, frameIndex, normalizedScale),
        outputFrame,
        rgb_frame_size,
        1,
        0,
        normalizedScale);

    g_mlv_last_processed8_total_ms = (mlv_stage_timing_now() - total_start) * 1000.0;
    mlv_stage_timing_note_elapsed("processed8_total", frameIndex, g_mlv_last_processed8_total_ms);
}

/* Phase 4A: scale-1 entrypoint preserves the original public API. */
void getMlvProcessedFrame8(mlvObject_t * video, uint64_t frameIndex, uint8_t * outputFrame, int threads)
{
    getMlvProcessedFrame8_with_scale(video, frameIndex, outputFrame, threads, 1);
}

/* Phase 4A: scale-aware variant. The pipeline still renders at full
 * resolution; scaleFactor only affects cache-key isolation. */
void getMlvProcessedFrame8Scaled(mlvObject_t * video,
                                 uint64_t frameIndex,
                                 uint8_t * outputFrame,
                                 int threads,
                                 int scaleFactor)
{
    getMlvProcessedFrame8_with_scale(video, frameIndex, outputFrame, threads, scaleFactor);
}

/* Phase 4B: helper for callers that need to size their output buffer for
 * the scaled pipeline. Returns (W/scale, H/scale) when the requested
 * scale is honoured, else returns the full sensor dimensions.
 *
 * The "effective" scale is computed with the same dual-ISO clamp rules as
 * the rendering path — for HQ Dual ISO clips, scale=2 is forced up to
 * scale=4 because the recon relies on the 4-row iso_patterns cycle. */
void mlvFrameOutputDimensions(mlvObject_t * video,
                              int scaleFactor,
                              int * outWidth,
                              int * outHeight)
{
    const int width  = video ? (int)getMlvWidth(video) : 0;
    const int height = video ? (int)getMlvHeight(video) : 0;
    const int s = mlv_effective_playback_scale_factor(video, scaleFactor);
    if (outWidth)
    {
        *outWidth = (s > 1) ? (width / s) : width;
    }
    if (outHeight)
    {
        *outHeight = (s > 1) ? (height / s) : height;
    }
}

double getMlvLastRawUint16Milliseconds(void)
{
    return g_mlv_last_raw_uint16_ms;
}

double getMlvLastRawUint16DiskReadMilliseconds(void)
{
    return g_mlv_last_raw_uint16_disk_read_ms;
}

double getMlvLastRawUint16DecompressMilliseconds(void)
{
    return g_mlv_last_raw_uint16_decompress_ms;
}

double getMlvLastRawUint16DecompressPrepareMilliseconds(void)
{
    return g_mlv_last_raw_uint16_decompress_prepare_ms;
}

double getMlvLastRawUint16DecompressExecuteMilliseconds(void)
{
    return g_mlv_last_raw_uint16_decompress_execute_ms;
}

int getMlvLastRawUint16Lj92Pred6SplitActive(void)
{
    return g_mlv_last_raw_uint16_lj92_pred6_split_active;
}

int getMlvLastRawUint16Lj92Pred6SplitRequested(void)
{
    return g_mlv_last_raw_uint16_lj92_pred6_split_requested;
}

int getMlvLastRawUint16Lj92GenericSplitActive(void)
{
    return g_mlv_last_raw_uint16_lj92_generic_split_active;
}

int getMlvLastRawUint16Lj92GenericSplitRequested(void)
{
    return g_mlv_last_raw_uint16_lj92_generic_split_requested;
}

int getMlvLastRawUint16Lj92Pred1FastPathActive(void)
{
    return g_mlv_last_raw_uint16_lj92_pred1_fast_path_active;
}

int getMlvLastRawUint16Lj92Pred1FastPathMeasurementRequested(void)
{
    return g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_requested;
}

int getMlvLastRawUint16Lj92Pred1FastPathMeasurementActive(void)
{
    return g_mlv_last_raw_uint16_lj92_pred1_fast_path_measurement_active;
}

int getMlvLastRawUint16Lj92Pred1FastPathEligible(void)
{
    return g_mlv_last_raw_uint16_lj92_pred1_fast_path_eligible;
}

int getMlvLastRawUint16Lj92ScanComponentCount(void)
{
    return g_mlv_last_raw_uint16_lj92_scan_component_count;
}

int getMlvLastRawUint16Lj92WriteLength(void)
{
    return g_mlv_last_raw_uint16_lj92_write_length;
}

int getMlvLastRawUint16Lj92ExpectedWriteLength(void)
{
    return g_mlv_last_raw_uint16_lj92_expected_write_length;
}

int getMlvLastRawUint16Lj92SkipLength(void)
{
    return g_mlv_last_raw_uint16_lj92_skip_length;
}

int getMlvLastRawUint16Lj92LinearizeActive(void)
{
    return g_mlv_last_raw_uint16_lj92_linearize_active;
}

int getMlvLastRawUint16Lj92ComponentCount(void)
{
    return g_mlv_last_raw_uint16_lj92_component_count;
}

int getMlvLastRawUint16Lj92Predictor(void)
{
    return g_mlv_last_raw_uint16_lj92_predictor;
}

double getMlvLastRawUint16Lj92Pred6TotalMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_pred6_total_ms;
}

double getMlvLastRawUint16Lj92Pred6BitstreamMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_pred6_bitstream_ms;
}

double getMlvLastRawUint16Lj92Pred6PredictorMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_pred6_predictor_ms;
}

double getMlvLastRawUint16Lj92GenericTotalMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_generic_total_ms;
}

double getMlvLastRawUint16Lj92GenericBitstreamMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_generic_bitstream_ms;
}

double getMlvLastRawUint16Lj92GenericPredictorMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_generic_predictor_ms;
}

double getMlvLastRawUint16Lj92Pred1FastPathTotalMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_pred1_fast_path_total_ms;
}

double getMlvLastRawUint16Lj92Pred1FastPathBitstreamMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_pred1_fast_path_bitstream_ms;
}

double getMlvLastRawUint16Lj92Pred1FastPathPredictorMilliseconds(void)
{
    return g_mlv_last_raw_uint16_lj92_pred1_fast_path_predictor_ms;
}

double getMlvLastRawUint16UnpackMilliseconds(void)
{
    return g_mlv_last_raw_uint16_unpack_ms;
}

double getMlvLastRawUint16CopyMilliseconds(void)
{
    return g_mlv_last_raw_uint16_copy_ms;
}

int getMlvLastRawUint16PrefetchHit(void)
{
    return g_mlv_last_raw_uint16_prefetch_hit;
}

uint64_t getMlvRawUint16PrefetchDecodeFailures(mlvObject_t * video)
{
    if (!video) return 0;
    pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
    uint64_t value = video->raw_uint16_prefetch_decode_failures;
    pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
    return value;
}

double getMlvLastLlrawprocMilliseconds(void)
{
    return g_mlv_last_llrawproc_ms;
}

double getMlvLastRawFloatConvertMilliseconds(void)
{
    return g_mlv_last_raw_float_convert_ms;
}

double getMlvLastDebayeredFrameMilliseconds(void)
{
    return g_mlv_last_debayered_frame_ms;
}

double getMlvLastProcessingMilliseconds(void)
{
    return g_mlv_last_processing_ms;
}

double getMlvLastProcessed16TotalMilliseconds(void)
{
    return g_mlv_last_processed16_total_ms;
}

double getMlvLastProcessed16For8BitMilliseconds(void)
{
    return g_mlv_last_processed16_for_8bit_ms;
}

double getMlvLastProcessed16To8BitMilliseconds(void)
{
    return g_mlv_last_processed16_to_8bit_ms;
}

double getMlvLastProcessed8TotalMilliseconds(void)
{
    return g_mlv_last_processed8_total_ms;
}

int getMlvLastProcessed8DirectPathActive(void)
{
    return g_mlv_last_processed8_direct_path_active;
}

int getMlvLastProcessed8PrefetchHit(void)
{
    return g_mlv_last_processed8_prefetch_hit;
}

/* To initialise mlv object with a clip
 * Two functions in one */
mlvObject_t * initMlvObjectWithClip(char * mlvPath, int preview, int * err, char * error_message)
{
    mlvObject_t * video = initMlvObject();
    char error_message_tmp[256] = {0};
    int err_tmp =  openMlvClip(video, mlvPath, preview, error_message_tmp);
    if (err != NULL) *err = err_tmp;
    if (error_message != NULL) strcpy(error_message, error_message_tmp);
    return video;
}

/* To initialise mlv object with a clip
 * Two functions in one */
mlvObject_t * initMlvObjectWithMcrawClip(char * mlvPath, int preview, int * err, char * error_message)
{
    mlvObject_t * video = initMlvObject();
    char error_message_tmp[256] = {0};
    int err_tmp =  openMcrawClip(video, mlvPath, preview, error_message_tmp);
    if (err != NULL) *err = err_tmp;
    if (error_message != NULL) strcpy(error_message, error_message_tmp);
    return video;
}

/* Allocates a tiny bit of memory for everything in the structure
 * so we can always be sure there is memory, and when we need to 
 * resize it, simply do free followed by malloc */
mlvObject_t * initMlvObject()
{
    mlvObject_t * video = (mlvObject_t *)calloc( 1, sizeof(mlvObject_t) );

    /* Initialize index buffers with NULL,
     * will be allocated/reallocated later */
    video->video_index = NULL;
    video->audio_index = NULL;

    /* Init audio buffer pointer */
    video->audio_data = NULL;

    /* Cache things, only one element for now as it is empty */
    video->rgb_raw_frames = NULL;
    video->rgb_raw_current_frame = NULL;
    video->cached_frames = NULL;
    video->raw_debayer_temp_frame = NULL;
    video->rgb_processed_temp_frame = NULL;
    video->rgb_processed_current_frame = NULL;
    video->rgb_processed_frame_cache_16bit = NULL;
    video->rgb_processed_current_frame_8bit = NULL;
    video->current_processed_frame_signature = 0;
    mlv_reset_processed_frame_16bit_cache(video);
    mlv_reset_processed_frame_8bit_cache(video);
    /* All frames in one block of memory for least mallocing during usage */
    video->cache_memory_block = NULL;
    /* Path (so separate cache threads can have their own FILE*s) */
    video->path = NULL;

    /* Will avoid main file conflicts with audio and stuff */
    pthread_mutex_init(&video->g_mutexFind, NULL);
    pthread_mutex_init(&video->g_mutexCount, NULL);
    pthread_mutex_init(&video->llrawproc_mutex, NULL);
    pthread_mutex_init(&video->llrawproc_worker_mutex, NULL);
    pthread_mutex_init(&video->processed8_prefetch_mutex, NULL);
    pthread_cond_init(&video->processed8_prefetch_cond, NULL);
    pthread_mutex_init(&video->raw_uint16_prefetch_mutex, NULL);
    pthread_cond_init(&video->raw_uint16_prefetch_cond, NULL);
    video->llrawproc_workers = NULL;
    video->llrawproc_worker_capacity = 0;
    video->processed8_prefetch_processing = initProcessingObject();
    video->processed8_prefetch_thread_started = 0;
    video->processed8_prefetch_stop = 0;
    video->processed8_prefetch_request_pending = 0;
    video->processed8_prefetch_worker_busy = 0;
    video->processed8_prefetch_request_frame = 0;
    video->processed8_prefetch_request_threads = 0;
    video->processed8_prefetch_last_request_frame = 0;
    video->processed8_prefetch_last_request_threads = 0;
    video->processed8_prefetch_last_state_signature = 0;
    video->processed8_prefetch_generation = 1;
    video->raw_uint16_prefetch_thread_started = 0;
    video->raw_uint16_prefetch_stop = 0;
    video->raw_uint16_prefetch_request_pending = 0;
    video->raw_uint16_prefetch_worker_busy = 0;
    video->raw_uint16_prefetch_request_frame = 0;
    video->raw_uint16_prefetch_last_request_frame = 0;
    video->raw_uint16_prefetch_generation = 1;
    video->raw_uint16_prefetch_cache = NULL;
    video->raw_uint16_prefetch_cache_words = 0;
    video->raw_uint16_prefetch_slot_words = 0;
    mlv_reset_raw_uint16_prefetch_locked(video);

    /* Set cache limit to allow ~1 second of 1080p and be safe for low ram PCs */
    setMlvRawCacheLimitMegaBytes(video, 290);
    setMlvCacheStartFrame(video, 0); /* Just in case */

    /* Seems about right */
    setMlvCpuCores(video, 4);

    /* Init low level raw processing object */
    video->llrawproc = initLLRawProcObject();

    /* Init CA correction */
    //video->ca_auto = 0;
    video->ca_red = 0.0;
    video->ca_blue = 0.0;

    /* Use default camid as fallback */
    camera_id_t *camid = camidGet(0);
    memcpy(&video->camid, camid, sizeof(camera_id_t));

    /* Retun pointer */
    return video;
}

/* Free all memory and close file */
void freeMlvObject(mlvObject_t * video)
{
    isMlvActive(video) = 0;

    /* Stop caching and make sure using silly sleep trick */
    video->stop_caching = 1;
    while (video->cache_thread_count) usleep(100);

    pthread_mutex_lock(&video->processed8_prefetch_mutex);
    video->processed8_prefetch_stop = 1;
    pthread_cond_broadcast(&video->processed8_prefetch_cond);
    pthread_mutex_unlock(&video->processed8_prefetch_mutex);
    if (video->processed8_prefetch_thread_started)
    {
        pthread_join(video->processed8_prefetch_thread, NULL);
    }

    pthread_mutex_lock(&video->raw_uint16_prefetch_mutex);
    video->raw_uint16_prefetch_stop = 1;
    pthread_cond_broadcast(&video->raw_uint16_prefetch_cond);
    pthread_mutex_unlock(&video->raw_uint16_prefetch_mutex);
    if (video->raw_uint16_prefetch_thread_started)
    {
        pthread_join(video->raw_uint16_prefetch_thread, NULL);
    }

    /* Close all MLV file chunks */
    if(video->file) close_all_chunks(video->file, video->filenum);
    /* Free all memory */
    if(video->video_index) free(video->video_index);
    if(video->audio_index) free(video->audio_index);
    if(video->vers_index) free(video->vers_index);

    /* Free audio buffer */
    if(video->audio_data)
    {
        free(video->audio_data);
        video->audio_data = NULL;
    }

    /* Now free these */
    if(video->cached_frames)
    {
        free(video->cached_frames);
        video->cached_frames = NULL;
    }
    if(video->rgb_raw_frames) free(video->rgb_raw_frames);
    if(video->rgb_raw_current_frame) free(video->rgb_raw_current_frame);
    if(video->raw_debayer_temp_frame) free(video->raw_debayer_temp_frame);
    if(video->rgb_processed_temp_frame) free(video->rgb_processed_temp_frame);
    if(video->rgb_processed_current_frame) free(video->rgb_processed_current_frame);
    if(video->rgb_processed_frame_cache_16bit) free(video->rgb_processed_frame_cache_16bit);
    if(video->rgb_processed_current_frame_8bit) free(video->rgb_processed_current_frame_8bit);
    if(video->raw_uint16_prefetch_cache) free(video->raw_uint16_prefetch_cache);
    if(video->cache_memory_block) free(video->cache_memory_block);
    if(video->path) free(video->path);
    if(video->processed8_prefetch_processing) freeProcessingObject(video->processed8_prefetch_processing);
    freeLLRawProcObject(video);

    /* Mutex things here... */
    for (int i = 0; i < video->filenum; ++i)
        if(video->main_file_mutex) pthread_mutex_destroy(video->main_file_mutex + i);
    if(video->main_file_mutex) free(video->main_file_mutex);
    pthread_mutex_destroy(&video->g_mutexFind);
    pthread_mutex_destroy(&video->g_mutexCount);
    pthread_mutex_destroy(&video->llrawproc_mutex);
    pthread_mutex_destroy(&video->llrawproc_worker_mutex);
    pthread_mutex_destroy(&video->processed8_prefetch_mutex);
    pthread_cond_destroy(&video->processed8_prefetch_cond);
    pthread_mutex_destroy(&video->raw_uint16_prefetch_mutex);
    pthread_cond_destroy(&video->raw_uint16_prefetch_cond);

    /* Main 1 */
    free(video);
}

/* Save MLV App map file (.MAPP) */
static int save_mapp(mlvObject_t * video)
{
    int mapp_name_len = strlen(video->path);
    char * mapp_filename = alloca(mapp_name_len + 4);
    memset(mapp_filename, 0x00, mapp_name_len + 4);
    memcpy(mapp_filename, video->path, mapp_name_len);
    char * dot = strrchr(mapp_filename, '.');
    memcpy(dot, ".MAPP\0", 6);

    size_t video_index_size = video->frames * sizeof(frame_index_t);
    size_t audio_index_size = video->audios * sizeof(frame_index_t);
    size_t vers_index_size = video->vers_blocks * sizeof(frame_index_t);
    size_t mapp_buf_size = sizeof(mapp_header_t) +
                           sizeof(mlv_file_hdr_t) +
                           sizeof(mlv_rawi_hdr_t) +
                           sizeof(mlv_rawc_hdr_t) +
                           sizeof(mlv_idnt_hdr_t) +
                           sizeof(mlv_expo_hdr_t) +
                           sizeof(mlv_lens_hdr_t) +
                           sizeof(mlv_elns_hdr_t) +
                           sizeof(mlv_rtci_hdr_t) +
                           sizeof(mlv_wbal_hdr_t) +
                           sizeof(mlv_styl_hdr_t) +
                           sizeof(mlv_wavi_hdr_t) +
                           sizeof(mlv_diso_hdr_t) +
                           sizeof(mlv_dark_hdr_t) +
                           sizeof(camera_id_t) +
                           video_index_size +
                           audio_index_size +
                           vers_index_size;

    uint8_t * mapp_buf = malloc(mapp_buf_size);
    if(!mapp_buf)
    {
        return 1;
    }

    /* init mapp header */
    mapp_header_t mapp_header = { "MAPP", mapp_buf_size + video->audio_size, MAPP_VERSION, video->block_num, video->frames, video->audios, video->vers_blocks, video->audio_size, video->dark_frame_offset };
    /* copy pointer to mapp buffer */
    uint8_t * ptr = mapp_buf;
    /* fill mapp buffer */
    memcpy(ptr, (uint8_t*)&mapp_header, sizeof(mapp_header_t));
    memcpy(ptr += sizeof(mapp_header_t), (uint8_t*)&(video->MLVI), sizeof(mlv_file_hdr_t));
    memcpy(ptr += sizeof(mlv_file_hdr_t), (uint8_t*)&(video->RAWI), sizeof(mlv_rawi_hdr_t));
    memcpy(ptr += sizeof(mlv_rawi_hdr_t), (uint8_t*)&(video->RAWC), sizeof(mlv_rawc_hdr_t));
    memcpy(ptr += sizeof(mlv_rawc_hdr_t), (uint8_t*)&(video->IDNT), sizeof(mlv_idnt_hdr_t));
    memcpy(ptr += sizeof(mlv_idnt_hdr_t), (uint8_t*)&(video->EXPO), sizeof(mlv_expo_hdr_t));
    memcpy(ptr += sizeof(mlv_expo_hdr_t), (uint8_t*)&(video->LENS), sizeof(mlv_lens_hdr_t));
    memcpy(ptr += sizeof(mlv_lens_hdr_t), (uint8_t*)&(video->ELNS), sizeof(mlv_elns_hdr_t));
    memcpy(ptr += sizeof(mlv_elns_hdr_t), (uint8_t*)&(video->RTCI), sizeof(mlv_rtci_hdr_t));
    memcpy(ptr += sizeof(mlv_rtci_hdr_t), (uint8_t*)&(video->WBAL), sizeof(mlv_wbal_hdr_t));
    memcpy(ptr += sizeof(mlv_wbal_hdr_t), (uint8_t*)&(video->STYL), sizeof(mlv_styl_hdr_t));
    memcpy(ptr += sizeof(mlv_styl_hdr_t), (uint8_t*)&(video->WAVI), sizeof(mlv_wavi_hdr_t));
    memcpy(ptr += sizeof(mlv_wavi_hdr_t), (uint8_t*)&(video->DISO), sizeof(mlv_diso_hdr_t));
    memcpy(ptr += sizeof(mlv_diso_hdr_t), (uint8_t*)&(video->DARK), sizeof(mlv_dark_hdr_t));
    memcpy(ptr += sizeof(mlv_dark_hdr_t), (uint8_t*)&(video->camid), sizeof(camera_id_t));
    ptr += sizeof(camera_id_t);
    if(video->video_index)
    {
        memcpy(ptr, (uint8_t*)video->video_index, video_index_size);
        ptr += video_index_size;
    }
    if(video->audio_index)
    {
        memcpy(ptr, (uint8_t*)video->audio_index, audio_index_size);
        ptr += audio_index_size;
    }
    if(video->vers_index)
    {
        memcpy(ptr, (uint8_t*)video->vers_index, vers_index_size);
        ptr += vers_index_size;
    }

    /* open .MAPP file for writing */
    FILE* mappf = fopen(mapp_filename, "wb");
    if (!mappf)
    {
        DEBUG( printf("Could not open %s\n\n", mapp_filename); )
        free(mapp_buf);
        return 1;
    }

    /* write mapp buffer */
    if(fwrite(mapp_buf, mapp_buf_size, 1, mappf) != 1)
    {
        DEBUG( printf("\nCould not save header and metadata to %s\n", mapp_filename); )
        fclose(mappf);
        free(mapp_buf);
        return 1;
    }
    DEBUG( printf("\nHeader and metadata saved to %s\n", mapp_filename); )

    /* write mapp buffer */
    if(fwrite(video->audio_data, video->audio_size, 1, mappf) != 1)
    {
        DEBUG( printf("Could not save audio data to %s\n", mapp_filename); )
        fclose(mappf);
        free(mapp_buf);
        return 1;
    }
    DEBUG( printf("Audio data saved to %s\n", mapp_filename); )

    fclose(mappf);
    free(mapp_buf);
    return 0;
}

/* Load MLV App map file (.MAPP) */
static int load_mapp(mlvObject_t * video)
{
    int mapp_name_len = strlen(video->path);
    char * mapp_filename = alloca(mapp_name_len + 4);
    memset(mapp_filename, 0x00, mapp_name_len + 4);
    memcpy(mapp_filename, video->path, mapp_name_len);
    char * dot = strrchr(mapp_filename, '.');
    memcpy(dot, ".MAPP\0", 6);

    /* open .MAPP file for reading */
    FILE* mappf = fopen(mapp_filename, "rb");
    if (!mappf)
    {
        DEBUG( printf("Could not open %s\n\n", mapp_filename); )
        return 1;
    }

    /* Read .MAPP header */
    mapp_header_t mapp_header = { 0 };
    if ( fread(&mapp_header, sizeof(mapp_header_t), 1, mappf) != 1 )
    {
        DEBUG( printf("Could not read header from %s\n", mapp_filename); )
        goto mapp_error;
    }
    DEBUG( printf("Header loaded from %s\n", mapp_filename); )

    DEBUG(
        printf("Magic %s, Size %lu, Version %d, Total Blocks %d, Total VIDF %d, Total AUDF %d, Total VERS %d, Audio Size %lu, DF Offset %lu\n",
        mapp_header.fileMagic, mapp_header.mapp_size, mapp_header.mapp_version, mapp_header.block_num, mapp_header.video_frames,
        mapp_header.audio_frames, mapp_header.vers_blocks, mapp_header.audio_size, mapp_header.df_offset);
    )

    /* Check MAPP validity */
    if( memcmp(mapp_header.fileMagic, "MAPP", 4) != 0 )
    {
        DEBUG( printf("Not a valid MAPP file: %s\n", mapp_filename); )
        goto mapp_error;
    }
    /* Check MAPP version */
    if( mapp_header.mapp_version != MAPP_VERSION )
    {
        DEBUG( printf("Wrong MAPP version: %d. Please rebuild all MAPPs\n", mapp_header.mapp_version); )
        goto mapp_error;
    }

    uint64_t mark_pos = file_get_pos(mappf);
    file_set_pos(mappf, 0, SEEK_END);
    uint64_t mapp_file_size = file_get_pos(mappf);
    file_set_pos(mappf, mark_pos, SEEK_SET);
    if( mapp_header.mapp_size != mapp_file_size )
    {
        DEBUG( printf("MAPP file size is wrong: %s\n", mapp_filename); )
        goto mapp_error;
    }

    /* Read MLV block headers */
    int ret = 0;
    ret += fread(&(video->MLVI), sizeof(mlv_file_hdr_t), 1, mappf);
    ret += fread(&(video->RAWI), sizeof(mlv_rawi_hdr_t), 1, mappf);
    ret += fread(&(video->RAWC), sizeof(mlv_rawc_hdr_t), 1, mappf);
    ret += fread(&(video->IDNT), sizeof(mlv_idnt_hdr_t), 1, mappf);
    ret += fread(&(video->EXPO), sizeof(mlv_expo_hdr_t), 1, mappf);
    ret += fread(&(video->LENS), sizeof(mlv_lens_hdr_t), 1, mappf);
    ret += fread(&(video->ELNS), sizeof(mlv_elns_hdr_t), 1, mappf);
    ret += fread(&(video->RTCI), sizeof(mlv_rtci_hdr_t), 1, mappf);
    ret += fread(&(video->WBAL), sizeof(mlv_wbal_hdr_t), 1, mappf);
    ret += fread(&(video->STYL), sizeof(mlv_styl_hdr_t), 1, mappf);
    ret += fread(&(video->WAVI), sizeof(mlv_wavi_hdr_t), 1, mappf);
    ret += fread(&(video->DISO), sizeof(mlv_diso_hdr_t), 1, mappf);
    ret += fread(&(video->DARK), sizeof(mlv_dark_hdr_t), 1, mappf);
    ret += fread(&(video->camid), sizeof(camera_id_t), 1, mappf);
    if(ret != 14)
    {
        DEBUG( printf("ret = %d, could not read metadata from %s\n", ret, mapp_filename); )
        goto mapp_error;
    }
    DEBUG( printf("Metadata loaded from %s\n", mapp_filename); )

    /* Read video index */
    if(mapp_header.video_frames)
    {
        size_t video_index_size = mapp_header.video_frames * sizeof(frame_index_t);

        video->video_index = malloc(video_index_size);
        if(!video->video_index)
        {
            DEBUG( printf("Malloc error: video index\n"); )
            goto mapp_error;
        }

        if ( fread(video->video_index, video_index_size, 1, mappf) != 1 )
        {
            DEBUG( printf("Could not read video index from %s\n", mapp_filename); )
            goto mapp_error;
        }
        DEBUG( printf("Video index loaded from %s\n", mapp_filename); )
    }

    /* Read audio index */
    if(mapp_header.audio_frames)
    {
        size_t audio_index_size = mapp_header.audio_frames * sizeof(frame_index_t);

        video->audio_index = malloc(audio_index_size);
        if(!video->audio_index)
        {
            DEBUG( printf("Malloc error: audio index\n"); )
            goto mapp_error;
        }

        if ( fread(video->audio_index, audio_index_size, 1, mappf) != 1 )
        {
            DEBUG( printf("Could not read audio index from %s\n", mapp_filename); )
            goto mapp_error;
        }
        DEBUG( printf("Audio index loaded from %s\n", mapp_filename); )
    }

    /* Read vers index */
    if(mapp_header.vers_blocks)
    {
        size_t vers_index_size = mapp_header.vers_blocks * sizeof(frame_index_t);

        video->vers_index = malloc(vers_index_size);
        if(!video->vers_index)
        {
            DEBUG( printf("Malloc error: VERS index\n"); )
            goto mapp_error;
        }

        if ( fread(video->vers_index, vers_index_size, 1, mappf) != 1 )
        {
            DEBUG( printf("Could not read VERS index from %s\n", mapp_filename); )
            goto mapp_error;
        }
        DEBUG( printf("VERS index loaded from %s\n", mapp_filename); )
    }

    /* Read audio data */
    if(mapp_header.audio_size)
    {
        video->audio_buffer_size = mapp_header.audio_size;
        video->audio_size = mapp_header.audio_size;
        video->audio_data = malloc(mapp_header.audio_size);
        if ( fread(video->audio_data, mapp_header.audio_size, 1, mappf) != 1 )
        {
            DEBUG( printf("Could not read audio data from %s\n", mapp_filename); )
            goto mapp_error;
        }
        DEBUG( printf("Audio data loaded from %s\n", mapp_filename); )
    }

    /* Set video and audio frame counts */
    video->frames = mapp_header.video_frames;
    video->audios = mapp_header.audio_frames;

    /* Set some required values */
    video->block_num = mapp_header.block_num;
    video->dark_frame_offset = mapp_header.df_offset;
    video->vers_blocks = mapp_header.vers_blocks;

    DEBUG( printf("MAPP version %u loaded: %s\n", mapp_header.mapp_version, mapp_filename); )

    fclose(mappf);
    return 0;

mapp_error:

    if(video->video_index)
    {
        free(video->video_index);
        video->video_index = NULL;
    }
    if(video->audio_index)
    {
        free(video->audio_index);
        video->audio_index = NULL;
    }
    if(video->vers_index)
    {
        free(video->vers_index);
        video->vers_index = NULL;
    }
    if(video->audio_data)
    {
        free(video->audio_data);
        video->audio_data = NULL;
    }
    if(mappf) fclose(mappf);

    return 1;
}

/* Save MLV headers */
int saveMlvHeaders(mlvObject_t * video, FILE * output_mlv, int export_audio, int export_mode, uint32_t frame_start, uint32_t frame_end, const char * version, char * error_message)
{
    if(export_mode == MLV_DF_INT && !video->DARK.blockType[0])
    {
        sprintf(error_message, "There is no internal darkframe in:  %s", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        return 1;
    }
    else if((export_mode == MLV_COMPRESS) && isMlvCompressed(video))
    {
        sprintf(error_message, "MLV already compressed:  %s\nUse 'Fast Pass' instead", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        return 1;
    }
    else if((export_mode == MLV_DECOMPRESS) && (!isMlvCompressed(video)))
    {
        sprintf(error_message, "MLV already uncompressed:  %s\nUse 'Fast Pass' instead", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        return 1;
    }

    /* construct version info */
    char version_info[1024] = { 0 };
    char tms[64] = { 0 };
    char export_mode_str[32] = { 0 };
    char export_audio_str[8] = { 0 };
    time_t rawtm = time(NULL);
    struct tm *tm = localtime(&rawtm);
    strftime(tms, sizeof(tms), "%H:%M:%S %b %e %Y", tm);

    switch(export_mode)
    {
        case MLV_FAST_PASS:
        {
            strcat(export_mode_str, "MLV_FAST_PASS");
            break;
        }
        case MLV_COMPRESS:
        {
            strcat(export_mode_str, "MLV_COMPRESS");
            break;
        }
        case MLV_DECOMPRESS:
        {
            strcat(export_mode_str, "MLV_DECOMPRESS");
            break;
        }
        case MLV_AVERAGED_FRAME:
        {
            strcat(export_mode_str, "MLV_AVERAGED_FRAME");
            break;
        }
        case MLV_DF_INT:
        {
            strcat(export_mode_str, "MLV_DF_INT");
            break;
        }
        default:
            strcat(export_mode_str, "MLV_FAST_PASS");
    }

    if(video->WAVI.blockType[0] && export_audio && (export_mode < MLV_AVERAGED_FRAME)) strcat(export_audio_str, "ON");
    else strcat(export_audio_str, "OFF");

    sprintf(version_info, "exported by MLV App version %s on %s; export mode: %s (audio: %s) ", version, tms, export_mode_str, export_audio_str);
    size_t vers_info_size = strlen(version_info) + 1;
    size_t vers_block_size = sizeof(mlv_vers_hdr_t) + vers_info_size;
    mlv_vers_hdr_t VERS_HEADER = { "VERS", vers_block_size, 0xFFFFFFFFFFFFFFFF, vers_info_size };

    /* calculate space needed for original VERS blocks */
    size_t orig_vers_blocks_size = 0;
    for (uint32_t i = 0; i < video->vers_blocks; ++i)
        orig_vers_blocks_size += sizeof(mlv_vers_hdr_t) + video->vers_index[i].frame_size;

    size_t mlv_headers_size = video->MLVI.blockSize + video->RAWI.blockSize + video->IDNT.blockSize +
                              video->EXPO.blockSize + video->LENS.blockSize + video->WBAL.blockSize +
                              video->RTCI.blockSize + vers_block_size + orig_vers_blocks_size;

    if(video->ELNS.blockType[0]) mlv_headers_size += video->ELNS.blockSize;
    if(video->RAWC.blockType[0]) mlv_headers_size += video->RAWC.blockSize;
    if(video->STYL.blockType[0]) mlv_headers_size += video->STYL.blockSize;
    if(video->DISO.blockType[0]) mlv_headers_size += video->DISO.blockSize;
    if(video->WAVI.blockType[0] && export_audio && export_mode < MLV_AVERAGED_FRAME) mlv_headers_size += video->WAVI.blockSize;
    if(video->INFO.blockType[0] && video->INFO_STRING[0]) mlv_headers_size += video->INFO.blockSize;
    if(video->llrawproc->dark_frame && export_mode < MLV_AVERAGED_FRAME) // if normal MLV export specified and dark frame exists
    {
        df_init(video);
        DEBUG( printf("Block Size = %u, DF Size = %u, Export Mode = %u, Filename = %s\n", video->llrawproc->dark_frame_hdr.blockSize, video->llrawproc->dark_frame_size, export_mode, video->llrawproc->dark_frame_filename); )
        DEBUG( printf("Headers Size += %u\n", video->llrawproc->dark_frame_hdr.blockSize); )
        mlv_headers_size += video->llrawproc->dark_frame_hdr.blockSize;
    }
    uint8_t * mlv_headers_buf = malloc(mlv_headers_size);
    if(!mlv_headers_buf)
    {
        sprintf(error_message, "Could not allocate memory for block headers");
        DEBUG( printf("\n%s\n", error_message); )
        return 1;
    }

    /* fill mlv_headers_buf */
    uint8_t * ptr = mlv_headers_buf;
    mlv_file_hdr_t output_mlvi = { 0 };
    memcpy(&output_mlvi, (uint8_t*)&(video->MLVI), sizeof(mlv_file_hdr_t));
    output_mlvi.fileNum = 0;
    output_mlvi.fileCount = 1;
    output_mlvi.videoFrameCount = (export_mode >= MLV_AVERAGED_FRAME) ? 1 : frame_end - frame_start + 1;
    output_mlvi.audioFrameCount = (!export_audio || export_mode >= MLV_AVERAGED_FRAME) ? 0 : 1;
    if(export_mode == MLV_COMPRESS && (!isMlvCompressed(video))) output_mlvi.videoClass |= MLV_VIDEO_CLASS_FLAG_LJ92;
    else if(export_mode >= MLV_DECOMPRESS && isMlvCompressed(video)) output_mlvi.videoClass  = 1;
    output_mlvi.audioClass = (!export_audio || export_mode >= MLV_AVERAGED_FRAME) ? 0 : 1;
    if(export_mode == MLV_DF_INT)
    {
        output_mlvi.sourceFpsNom = video->DARK.sourceFpsNom;
        output_mlvi.sourceFpsDenom = video->DARK.sourceFpsDenom;
    }
    memcpy(ptr, &output_mlvi, sizeof(mlv_file_hdr_t));
    ptr += video->MLVI.blockSize;

    if(export_mode == MLV_DF_INT)
    {
        mlv_rawi_hdr_t output_rawi = { 0 };
        memcpy(&output_rawi, (uint8_t*)&(video->RAWI), sizeof(mlv_rawi_hdr_t));
        output_rawi.xRes = video->DARK.xRes;
        output_rawi.yRes = video->DARK.yRes;
        output_rawi.raw_info.width = video->DARK.rawWidth;
        output_rawi.raw_info.height = video->DARK.rawHeight;
        output_rawi.raw_info.bits_per_pixel = video->DARK.bits_per_pixel;
        output_rawi.raw_info.black_level = video->DARK.black_level;
        output_rawi.raw_info.white_level = video->DARK.white_level;
        memcpy(ptr, &output_rawi, sizeof(mlv_rawi_hdr_t));
    }
    else
    {
        memcpy(ptr, (uint8_t*)&(video->RAWI), sizeof(mlv_rawi_hdr_t));
    }
    ptr += video->RAWI.blockSize;

    if(video->RAWC.blockType[0])
    {
        if(export_mode == MLV_DF_INT)
        {
            mlv_rawc_hdr_t output_rawc = { 0 };
            memcpy(&output_rawc, (uint8_t*)&(video->RAWC), sizeof(mlv_rawc_hdr_t));
            output_rawc.binning_x = video->DARK.binning_x;
            output_rawc.skipping_x = video->DARK.skipping_x;
            output_rawc.binning_y = video->DARK.binning_y;
            output_rawc.skipping_y = video->DARK.skipping_y;
            memcpy(ptr, &output_rawc, sizeof(mlv_rawc_hdr_t));
        }
        else
        {
            memcpy(ptr, (uint8_t*)&(video->RAWC), sizeof(mlv_rawc_hdr_t));
        }
        ptr += video->RAWC.blockSize;
    }

    if(export_mode == MLV_DF_INT)
    {
        mlv_idnt_hdr_t output_idnt = { 0 };
        memcpy(&output_idnt, (uint8_t*)&(video->IDNT), sizeof(mlv_idnt_hdr_t));
        output_idnt.cameraModel = video->DARK.cameraModel;
        memcpy(ptr, &output_idnt, sizeof(mlv_idnt_hdr_t));
    }
    else
    {
        memcpy(ptr, (uint8_t*)&(video->IDNT), sizeof(mlv_idnt_hdr_t));
    }
    ptr += video->IDNT.blockSize;

    if(export_mode == MLV_DF_INT)
    {
        mlv_expo_hdr_t output_expo = { 0 };
        memcpy(&output_expo, (uint8_t*)&(video->EXPO), sizeof(mlv_expo_hdr_t));
        output_expo.isoMode = video->DARK.isoMode;
        output_expo.isoValue = video->DARK.isoValue;
        output_expo.isoAnalog = video->DARK.isoAnalog;
        output_expo.digitalGain = video->DARK.digitalGain;
        output_expo.shutterValue = video->DARK.shutterValue;
        memcpy(ptr, &output_expo, sizeof(mlv_expo_hdr_t));
    }
    else
    {
        memcpy(ptr, (uint8_t*)&(video->EXPO), sizeof(mlv_expo_hdr_t));
    }
    ptr += video->EXPO.blockSize;

    memcpy(ptr, (uint8_t*)&(video->LENS), sizeof(mlv_lens_hdr_t));
    ptr += video->LENS.blockSize;

    if(video->ELNS.blockType[0])
    {
        memcpy(ptr, (uint8_t*)&(video->ELNS), sizeof(mlv_elns_hdr_t));
        ptr += video->ELNS.blockSize;
    }

    memcpy(ptr, (uint8_t*)&(video->WBAL), sizeof(mlv_wbal_hdr_t));
    ptr += video->WBAL.blockSize;

    if(video->STYL.blockType[0])
    {
        memcpy(ptr, (uint8_t*)&(video->STYL), sizeof(mlv_styl_hdr_t));
        ptr += video->STYL.blockSize;
    }

    memcpy(ptr, (uint8_t*)&(video->RTCI), sizeof(mlv_rtci_hdr_t));
    ptr += video->RTCI.blockSize;

    if(video->INFO.blockType[0] && video->INFO_STRING[0])
    {
        memcpy(ptr, (uint8_t*)&(video->INFO), sizeof(mlv_info_hdr_t));
        ptr += sizeof(mlv_info_hdr_t);
        memcpy(ptr, (uint8_t*)&(video->INFO_STRING), strlen(video->INFO_STRING) + 1);
        ptr += (video->INFO.blockSize - sizeof(mlv_info_hdr_t) + strlen(video->INFO_STRING) + 1);
    }

    if(video->DISO.blockType[0])
    {
        memcpy(ptr, (uint8_t*)&(video->DISO), sizeof(mlv_diso_hdr_t));
        ptr += video->DISO.blockSize;
    }

    if(video->WAVI.blockType[0] && export_audio && (export_mode < MLV_AVERAGED_FRAME))
    {
        memcpy(ptr, (uint8_t*)&(video->WAVI), sizeof(mlv_wavi_hdr_t));
        ptr += video->WAVI.blockSize;
    }

    if(video->llrawproc->dark_frame && export_mode < MLV_AVERAGED_FRAME) // if normal MLV export specified and dark frame exists
    {
        memcpy(ptr, (uint8_t*)&(video->llrawproc->dark_frame_hdr), sizeof(mlv_dark_hdr_t));
        ptr += sizeof(mlv_dark_hdr_t);

        size_t df_packed_size = video->llrawproc->dark_frame_hdr.blockSize - sizeof(mlv_dark_hdr_t);
        uint8_t * df_packed = calloc(df_packed_size, 1);
        dng_pack_image_bits((uint16_t *)df_packed, video->llrawproc->dark_frame_data, video->llrawproc->dark_frame_hdr.xRes, video->llrawproc->dark_frame_hdr.yRes, video->llrawproc->dark_frame_hdr.bits_per_pixel, 0);
        memcpy(ptr, df_packed, df_packed_size);
        ptr += df_packed_size;
        DEBUG( printf("\nDARK block inserted\n"); )
    }

    memcpy(ptr, &VERS_HEADER, sizeof(mlv_vers_hdr_t));
    ptr += sizeof(mlv_vers_hdr_t);
    memcpy(ptr, version_info, vers_info_size);
    ptr += vers_info_size;

    /* read all VERS block headers */
    char orig_vers_block[1024] = { 0 };
    for (uint32_t i = 0; i < video->vers_blocks; ++i)
    {
        int chunk = video->vers_index[i].chunk_num;
        file_set_pos(video->file[chunk], video->vers_index[i].block_offset, SEEK_SET);
        uint32_t orig_vers_block_size = sizeof(mlv_vers_hdr_t) + video->vers_index[i].frame_size;
        if(fread(orig_vers_block, orig_vers_block_size, 1, video->file[chunk]) != 1)
        {
            sprintf(error_message, "Could not read VERS block header from:  %s", video->path);
            DEBUG( printf("\n%s\n", error_message); )
                    return 1;
        }
        else
        {
            memcpy(ptr, orig_vers_block, orig_vers_block_size);
            ptr += orig_vers_block_size;
        }
    }

    /* write mlv_headers_buf */
    if(fwrite(mlv_headers_buf, mlv_headers_size, 1, output_mlv) != 1)
    {
        sprintf(error_message, "Could not write MLV headers");
        DEBUG( printf("\n%s\n", error_message); )
        free(mlv_headers_buf);
        return 1;
    }

    DEBUG( printf("\nMLV headers saved\n"); )
    free(mlv_headers_buf);
    return 0;
}

/* Save video frame plus audio if available */
int saveMlvAVFrame(mlvObject_t * video, FILE * output_mlv, int export_audio, int export_mode, uint32_t frame_start, uint32_t frame_end, uint32_t frame_index, uint64_t * avg_buf, char * error_message)
{
    mlv_vidf_hdr_t vidf_hdr = { 0 };

    int write_ok = (export_mode == MLV_AVERAGED_FRAME) ? 0 : 1;
    uint32_t pixel_count = video->RAWI.xRes * video->RAWI.yRes;
    uint32_t frame_size_packed = (uint32_t)(pixel_count * video->RAWI.raw_info.bits_per_pixel / 8);
    uint32_t frame_size_unpacked = pixel_count * 2;
    uint32_t max_frame_number = frame_end - frame_start + 1;

    int chunk = video->video_index[frame_index].chunk_num;
    uint32_t frame_size = video->video_index[frame_index].frame_size;
    uint64_t frame_offset = video->video_index[frame_index].frame_offset;
    uint64_t block_offset = video->video_index[frame_index].block_offset;

    /* read VIDF block header */
    file_set_pos(video->file[chunk], block_offset, SEEK_SET);
    if(fread(&vidf_hdr, sizeof(mlv_vidf_hdr_t), 1, video->file[chunk]) != 1)
    {
        sprintf(error_message, "Could not read VIDF block header from:  %s", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        return 1;
    }

    vidf_hdr.blockSize -= vidf_hdr.frameSpace;
    vidf_hdr.frameSpace = 0;

    /* for safety allocate max possible size buffer for VIDF block, calculated for 16bits per pixel */
    uint8_t * block_buf = calloc(sizeof(mlv_vidf_hdr_t) + frame_size_unpacked, 1);
    if(!block_buf)
    {
        sprintf(error_message, "Could not allocate memory for VIDF block");
        DEBUG( printf("\n%s\n", error_message); )
        return 1;
    }
    /* for safety allocate max possible size buffer for image data, calculated for 16bits per pixel */
    uint8_t * frame_buf = calloc(frame_size_unpacked, 1);
    if(!frame_buf)
    {
        sprintf(error_message, "Could not allocate memory for VIDF frame");
        DEBUG( printf("\n%s\n", error_message); )
        free(block_buf);
        return 1;
    }

    /* read frame buffer */
    file_set_pos(video->file[chunk], frame_offset, SEEK_SET);
    if(fread(frame_buf, frame_size, 1, video->file[chunk]) != 1)
    {
        sprintf(error_message, "Could not read VIDF image data from:  %s", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        free(frame_buf);
        free(block_buf);
        return 1;
    }

    if(export_mode == MLV_DF_INT) // export internal dark frame as separate MLV
    {
        size_t df_packed_size = video->DARK.blockSize - sizeof(mlv_dark_hdr_t);
        /* read dark frame */
        file_set_pos(video->file[0], video->dark_frame_offset, SEEK_SET);
        if(fread(frame_buf, df_packed_size, 1, video->file[0]) != 1)
        {
            sprintf(error_message, "Could not read DARK block image data from:  %s", video->path);
            DEBUG( printf("\n%s\n", error_message); )
            free(frame_buf);
            free(block_buf);
            return 1;
        }
        /* set blocksize and samplesAveraged to frameNumber */
        vidf_hdr.blockSize = video->DARK.blockSize;
        vidf_hdr.frameNumber = video->DARK.samplesAveraged;
        memcpy(block_buf, &vidf_hdr, sizeof(mlv_vidf_hdr_t));
        memcpy((block_buf + sizeof(mlv_vidf_hdr_t)), frame_buf, df_packed_size);
    }
    else if(export_mode == MLV_AVERAGED_FRAME) // average all frames to one dark frame
    {
        uint16_t * frame_buf_unpacked = calloc(frame_size_unpacked, 1);
        if(!frame_buf_unpacked)
        {
            sprintf(error_message, "Averaging: could not allocate memory for unpacked frame");
            DEBUG( printf("\n%s\n", error_message); )
            free(frame_buf);
            free(block_buf);
            return 1;
        }
        if(isMlvCompressed(video))
        {
            int ret = dng_decompress_image(frame_buf_unpacked, (uint16_t*)frame_buf, frame_size, video->RAWI.xRes, video->RAWI.yRes, video->RAWI.raw_info.bits_per_pixel);
            if(ret != LJ92_ERROR_NONE)
            {
                sprintf(error_message, "Averaging: could not decompress frame:  LJ92_ERROR %u", ret);
                DEBUG( printf("\n%s\n", error_message); )
                free(frame_buf_unpacked);
                free(frame_buf);
                free(block_buf);
                return ret;
            }
        }
        else
        {
            dng_unpack_image_bits(frame_buf_unpacked, (uint16_t*)frame_buf, video->RAWI.xRes, video->RAWI.yRes, video->RAWI.raw_info.bits_per_pixel);
        }
        for(uint32_t i = 0; i < pixel_count; i++)
        {
            avg_buf[i] += frame_buf_unpacked[i];
        }

        if(frame_index == frame_end - 1)
        {
            for(uint32_t i = 0; i < pixel_count; i++)
            {
                frame_buf_unpacked[i] = (avg_buf[i] + max_frame_number / 2) / max_frame_number;
            }
            dng_pack_image_bits((uint16_t *)frame_buf, frame_buf_unpacked, video->RAWI.xRes, video->RAWI.yRes, video->RAWI.raw_info.bits_per_pixel, 0);

            vidf_hdr.frameNumber = max_frame_number;
            vidf_hdr.blockSize = sizeof(mlv_vidf_hdr_t) + frame_size_packed;
            memcpy(block_buf, &vidf_hdr, sizeof(mlv_vidf_hdr_t));
            memcpy((block_buf + sizeof(mlv_vidf_hdr_t)), frame_buf, frame_size_packed);
            write_ok = 1;
        }

        free(frame_buf_unpacked);
    }
    else if((export_mode == MLV_COMPRESS) && (!isMlvCompressed(video))) // compress MLV frame with LJ92 if specified
    {
        int ret = 0;
        size_t frame_size_compressed = 0;

        uint16_t * frame_buf_unpacked = calloc(frame_size_unpacked, 1);
        uint16_t * frame_buf_compressed = calloc(frame_size_unpacked, 1);
        if(!frame_buf_unpacked || !frame_buf_compressed)
        {
            DEBUG( printf("\nCould not allocate memory for frame compressing\n"); )
            ret = 1;
        }

        if(!ret)
        {
            dng_unpack_image_bits(frame_buf_unpacked, (uint16_t*)frame_buf, video->RAWI.xRes, video->RAWI.yRes, video->RAWI.raw_info.bits_per_pixel);
            ret = dng_compress_image(frame_buf_compressed, frame_buf_unpacked, &frame_size_compressed, video->RAWI.xRes, video->RAWI.yRes, video->RAWI.raw_info.bits_per_pixel);
            if(ret == LJ92_ERROR_NONE)
            {
                vidf_hdr.blockSize = sizeof(mlv_vidf_hdr_t) + frame_size_compressed;
                memcpy(block_buf, &vidf_hdr, sizeof(mlv_vidf_hdr_t));
                memcpy((block_buf + sizeof(mlv_vidf_hdr_t)), (uint8_t*)frame_buf_compressed, frame_size_compressed);
            }
            else // if compression error then save original uncompressed raw
            {
                memcpy(block_buf, &vidf_hdr, sizeof(mlv_vidf_hdr_t));
                memcpy((block_buf + sizeof(mlv_vidf_hdr_t)), frame_buf, frame_size);

                /* patch MLVI header and set back videoClass to 1 (uncompressed) */
                uint64_t current_pos = file_get_pos(output_mlv);
                file_set_pos(output_mlv, 32, SEEK_SET);
                uint16_t videoClass = 0x1;
                if(fwrite(&videoClass, sizeof(uint16_t), 1, output_mlv) != 1)
                {
                    DEBUG( printf("\nCould not patch videoClass in MLV header\n"); )
                }
                file_set_pos(output_mlv, current_pos, SEEK_SET);
            }
        }

        if(frame_buf_unpacked) free(frame_buf_unpacked);
        if(frame_buf_compressed) free(frame_buf_compressed);
    }
    else if((export_mode == MLV_DECOMPRESS) && isMlvCompressed(video)) // decompress MLV frame with LJ92 if specified
    {
        int ret = 0;

        uint16_t * frame_buf_unpacked = calloc(frame_size_unpacked, 1);
        if(!frame_buf_unpacked)
        {
            DEBUG( printf("\nCould not allocate memory for frame decompressing\n"); )
            ret = 1;
        }

        if(!ret)
        {
            int ret = dng_decompress_image(frame_buf_unpacked, (uint16_t*)frame_buf, frame_size, video->RAWI.xRes, video->RAWI.yRes, video->RAWI.raw_info.bits_per_pixel);
            if(ret == LJ92_ERROR_NONE)
            {
                dng_pack_image_bits((uint16_t*)frame_buf, frame_buf_unpacked, video->RAWI.xRes, video->RAWI.yRes, video->RAWI.raw_info.bits_per_pixel, 0);
                vidf_hdr.blockSize = sizeof(mlv_vidf_hdr_t) + frame_size_packed;
                memcpy(block_buf, &vidf_hdr, sizeof(mlv_vidf_hdr_t));
                memcpy((block_buf + sizeof(mlv_vidf_hdr_t)), frame_buf, frame_size_packed);
            }
            else // if decompression error then save original lossless raw
            {
                memcpy(block_buf, &vidf_hdr, sizeof(mlv_vidf_hdr_t));
                memcpy((block_buf + sizeof(mlv_vidf_hdr_t)), frame_buf, frame_size);

                /* patch MLVI header and set back videoClass to 0x21 (lossless) */
                uint64_t current_pos = file_get_pos(output_mlv);
                file_set_pos(output_mlv, 32, SEEK_SET);
                uint16_t videoClass = 0x1 | MLV_VIDEO_CLASS_FLAG_LJ92;
                if(fwrite(&videoClass, sizeof(uint16_t), 1, output_mlv) != 1)
                {
                    DEBUG( printf("\nCould not patch videoClass in MLV header\n"); )
                }
                file_set_pos(output_mlv, current_pos, SEEK_SET);
            }
        }

        if(frame_buf_unpacked) free(frame_buf_unpacked);
    }
    else // pass through the original raw frame
    {
        memcpy(block_buf, &vidf_hdr, sizeof(mlv_vidf_hdr_t));
        memcpy((block_buf + sizeof(mlv_vidf_hdr_t)), frame_buf, frame_size);
    }

    /* if audio export is enabled */
    if(!(frame_start - frame_index - 1) && export_audio && export_mode < MLV_AVERAGED_FRAME )
    {
        /* initialize AUDF header */
        mlv_audf_hdr_t audf_hdr = { { 'A','U','D','F' }, 0, 0, 0, 0 };

        /* Calculate the sum of audio sample sizes for all audio channels */
        uint64_t audio_sample_size = getMlvAudioChannels(video) * (getMlvAudioBitsPerSample(video) / 8);
        /* Calculate the audio alignement block size in bytes */
        uint16_t block_align = audio_sample_size * 1024;
        /* Calculate audio starting offset */
        uint64_t audio_start_offset = ( (uint64_t)( (double)(getMlvSampleRate(video) * audio_sample_size * (frame_start - 1)) / (double)getMlvFramerate(video) ) );
        /* Make sure start offset value is multiple of sum of all channel sample sizes */
        uint64_t audio_start_offset_aligned = audio_start_offset - (audio_start_offset % audio_sample_size);
        /* Calculate cut audio size */
        uint64_t cut_audio_size = (uint64_t)( (double)(getMlvSampleRate(video) * audio_sample_size * (frame_end - frame_start + 1)) / (double)getMlvFramerate(video) );
        /* check if cut_audio_size is multiple of 'block_align' bytes and not more than original audio data size */
        uint64_t cut_audio_size_aligned = MIN( (cut_audio_size - (cut_audio_size % block_align) + block_align), video->audio_size );
        /* make max audio size (uint32_t max value - 1) multiple of 'block_align' bytes */
        uint32_t max_audio_size = 0xFFFFFFFF - (0xFFFFFFFF % block_align);
        /* Not likely that audio size exeeds the 4.3gb but anyway check if cut_audio_size is more than uint32_t max value to not overflow blockSize variable */
        if(cut_audio_size_aligned > max_audio_size) cut_audio_size_aligned = max_audio_size;

        /* fill AUDF block header */
        audf_hdr.blockSize = sizeof(mlv_audf_hdr_t) + cut_audio_size_aligned;
        audf_hdr.timestamp = vidf_hdr.timestamp;

        /* write AUDF block header */
        if(fwrite(&audf_hdr, sizeof(mlv_audf_hdr_t), 1, output_mlv) != 1)
        {
            sprintf(error_message, "Could not write AUDF block header");
            DEBUG( printf("\n%s\n", error_message); )
            free(frame_buf);
            free(block_buf);
            return 1;
        }

        /* write audio data */
        if(fwrite(video->audio_data + audio_start_offset_aligned, cut_audio_size_aligned, 1, output_mlv) != 1)
        {
            sprintf(error_message, "Could not write AUDF block audio data");
            DEBUG( printf("\n%s\n", error_message); )
            free(frame_buf);
            free(block_buf);
            return 1;
        }
    }

    /* write mlvFrame */
    if(write_ok)
    {
        if(fwrite(block_buf, vidf_hdr.blockSize, 1, output_mlv) != 1)
        {
            sprintf(error_message, "Could not write video frame #%u", frame_index);
            DEBUG( printf("\n%s\n", error_message); )
            free(frame_buf);
            free(block_buf);
            return 1;
        }
    }

    free(frame_buf);
    free(block_buf);
    DEBUG( if( (export_mode == MLV_FAST_PASS) && (!isMlvCompressed(video)) ) printf("Saved video frame #%u\n", frame_index); )
    return 0;
}

/* Reads a mcraw file in to a mlv object(mlvObject_t struct)
 * only puts metadata in to the mlvObject_t, no debayering or bit unpacking
 */
int openMcrawClip(mlvObject_t * video, char * mcrawPath, int open_mode, char * error_message)
{    
    video->path = malloc( strlen(mcrawPath) + 1 );
    memcpy(video->path, mcrawPath, strlen(mcrawPath));
    video->path[strlen(mcrawPath)] = 0x0;

    mr_ctx_t *ctx = mr_decoder_new(0);

    int res = mr_decoder_open(ctx, mcrawPath);

    if (res == 0) {
        res = mr_decoder_parse(ctx);
    }

    if (res != 0)
    {
        sprintf(error_message, "Could not open file:  %s", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        return MLV_ERR_OPEN; // can not open file
    }

    FILE **files = malloc(sizeof(FILE*));
    files[0] = mr_get_file_handle(ctx);
    video->file = files;


    /* Mutexes for every file */
    video->main_file_mutex = calloc(sizeof(pthread_mutex_t), 1);
    pthread_mutex_init(video->main_file_mutex, NULL);

    /* In preview mode we don't need to waste time on audio loading from MAPP */
    if (open_mode != MLV_OPEN_PREVIEW)
    {
        // DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG DEBUG
        //if (!load_mapp(video)) {
        //    goto short_cut;
        //}
    }

    int64_t video_index_max = mr_get_frame_count(ctx);

    if (open_mode == MLV_OPEN_PREVIEW) {
       video_index_max = 1;
    }

    video->compression_type = mr_get_compression_type(ctx);
    video->frames      = video_index_max;
    video->video_index = (frame_index_t *)calloc(video->frames, sizeof(frame_index_t));

    video->audios      = mr_get_audio_packet_count(ctx);
    video->audio_index = (frame_index_t *)calloc(video->audios, sizeof(frame_index_t));

    mr_buffer_offset_t *offsets = mr_get_index(ctx);

    for (int64_t i = 0; i < video->frames; i++)
    {
        video->video_index[i].frame_type   = 1;
        video->video_index[i].chunk_num    = 0;
        video->video_index[i].frame_size   = 0;
        video->video_index[i].frame_offset = offsets[i].offset + sizeof(mr_item_t);
        video->video_index[i].frame_number = i;
        video->video_index[i].frame_time   = offsets[i].timestamp / 1000000;   // ns to ms
        video->video_index[i].block_offset = offsets[i].offset;
    }

    mr_buffer_offset_t *audio_offsets = mr_get_audio_index(ctx);

    for (int64_t i = 0; i < video->audios; i++)
    {
        video->audio_index[i].frame_type   = 1;
        video->audio_index[i].chunk_num    = 0;
        video->audio_index[i].frame_size   = 0;
        video->audio_index[i].frame_offset = audio_offsets[i].offset + sizeof(mr_item_t);
        video->audio_index[i].frame_number = i;
        video->audio_index[i].frame_time   = audio_offsets[i].timestamp / 1000000;
        video->audio_index[i].block_offset = audio_offsets[i].offset;
    }

    memcpy(&video->WAVI.blockType, "WAVI", 4);
    video->WAVI.blockSize                 = sizeof(mlv_wavi_hdr_t);
    video->WAVI.format                    = 1;    // 1=Integer PCM, 6=alaw, 7=mulaw
    video->WAVI.bitsPerSample             = 16;
    video->WAVI.channels                  = mr_get_audio_channels(ctx);
    video->WAVI.samplingRate              = mr_get_audio_sample_rate(ctx);
    video->WAVI.bytesPerSecond            = video->WAVI.samplingRate * video->WAVI.channels * (video->WAVI.bitsPerSample / 8);
    video->WAVI.blockAlign                = video->WAVI.channels * (video->WAVI.bitsPerSample / 8);


    memcpy(&video->RAWI.blockType, "RAWI", 4);
    video->RAWI.blockSize                 = sizeof(mlv_rawi_hdr_t);
    video->RAWI.xRes                      = mr_get_width(ctx);
    video->RAWI.yRes                      = mr_get_height(ctx);
    video->RAWI.raw_info.active_area.x1   = 0;
    video->RAWI.raw_info.active_area.x2   = video->RAWI.xRes;
    video->RAWI.raw_info.active_area.y1   = 0;
    video->RAWI.raw_info.active_area.y2   = video->RAWI.yRes;

    video->RAWI.raw_info.bits_per_pixel   = mr_get_bits_per_pixel(ctx);
    video->RAWI.raw_info.black_level      = mr_get_black_level(ctx);
    video->RAWI.raw_info.white_level      = mr_get_white_level(ctx);
    video->RAWI.raw_info.cfa_pattern      = mr_get_cfa_pattern(ctx);
    video->RAWI.raw_info.exposure_bias[0] = 0;
    video->RAWI.raw_info.exposure_bias[1] = 0;


    double *matrix = mr_get_color_matrix1(ctx);
    for (int i = 0; i < 9; i++)
    {
        video->camid.ColorMatrix1[i * 2]     = (int)(matrix[i] * 10000.);
        video->camid.ColorMatrix1[i * 2 + 1] = 10000;
    }
    memcpy(video->RAWI.raw_info.color_matrix1, video->camid.ColorMatrix1, 18 * sizeof(int32_t));

    matrix = mr_get_color_matrix2(ctx);
    for (int i = 0; i < 9; i++)
    {
        video->camid.ColorMatrix2[i * 2]     = (int)(matrix[i] * 10000.);
        video->camid.ColorMatrix2[i * 2 + 1] = 10000;
    }

    matrix = mr_get_forward_matrix1(ctx);
    for (int i = 0; i < 9; i++)
    {
        video->camid.ForwardMatrix1[i * 2]     = (int)(matrix[i] * 10000.);
        video->camid.ForwardMatrix1[i * 2 + 1] = 10000;
    }

    matrix = mr_get_forward_matrix2(ctx);
    for (int i = 0; i < 9; i++)
    {
        video->camid.ForwardMatrix2[i * 2]     = (int)(matrix[i] * 10000.);
        video->camid.ForwardMatrix2[i * 2 + 1] = 10000;
    }

    video->MLVI.blockSize        = sizeof(mlv_file_hdr_t);
    video->MLVI.videoClass       = MLV_VIDEO_CLASS_FLAG_MCRAW;
    video->MLVI.videoFrameCount  = video_index_max;    // number of video frames in this file. set to 0 on start, updated when finished.

    mr_get_frame_rate(ctx, &video->MLVI.sourceFpsNom, &video->MLVI.sourceFpsDenom);
    video->MLVI.audioClass       = 1;                  // 0=none, 1=WAV
    video->MLVI.audioFrameCount  = 0;                  // number of audio frames in this file. set to 0 on start, updated when finished.

    memcpy(&video->WBAL.blockType, "WBAL", 4);
    video->WBAL.blockSize        = sizeof(mlv_wbal_hdr_t);
    video->WBAL.wb_mode          = 6;     // CUSTOM
    video->WBAL.timestamp        = 0;
    video->WBAL.kelvin           = 0;

    double *wb = mr_get_as_shot_neutral(ctx);
    video->WBAL.wbgain_r         = wb[0] * 1024;
    video->WBAL.wbgain_g         = wb[1] * 1024;
    video->WBAL.wbgain_b         = wb[2] * 1024;

    memcpy(&video->IDNT.blockType, "IDNT", 4);
    video->IDNT.blockSize        = sizeof(mlv_idnt_hdr_t);
    snprintf((char *)video->IDNT.cameraName, 31, "%s", mr_get_model(ctx));

    memcpy(&video->LENS.blockType, "LENS", 4);
    video->LENS.blockSize        = sizeof(mlv_lens_hdr_t);
    video->LENS.focalLength      = mr_get_focal_length(ctx);       // in mm
    video->LENS.aperture         = mr_get_aperture(ctx) * 100;     // f-number * 100

    memcpy(&video->EXPO.blockType, "EXPO", 4);
    video->EXPO.blockSize        = sizeof(mlv_lens_hdr_t);
    video->EXPO.isoValue         = mr_get_iso(ctx);
    video->EXPO.shutterValue     = mr_get_exposure_time(ctx) / 1000;

    time_t t = mr_get_timestamp(ctx) / 1000;
    struct tm tm = *localtime(&t);

    memcpy(&video->RTCI.blockType, "RTCI", 4);
    video->RTCI.blockSize        = sizeof(mlv_rtci_hdr_t);
    video->RTCI.tm_year          = tm.tm_year;
    video->RTCI.tm_mon           = tm.tm_mon;
    video->RTCI.tm_mday          = tm.tm_mday;
    video->RTCI.tm_hour          = tm.tm_hour;
    video->RTCI.tm_sec           = tm.tm_sec;

    /* Sort video and audio frames by time stamp */
    if (video->frames) {
        frame_index_sort(video->video_index, video->frames);
    }

    if (video->audios) {
        frame_index_sort(video->audio_index, video->audios);
    }

    /* Reads MLV audio into buffer (video->audio_data) and sync it,
     * set full audio buffer size (video->audio_buffer_size) and
     * aligned usable audio data size (video->audio_size) */
    readMlvAudioData(video);

    /* Save mapp file if this feature is on */
    if (open_mode == MLV_OPEN_MAPP)  {
       save_mapp(video);
    }

short_cut:

    /* Set imaginary lossless bit depth */
    setMlvLosslessBpp(video);

    video->llrawproc->diso_validity = DISO_INVALID;


    /* NON compressed frame size */
    video->frame_size = (getMlvHeight(video) * getMlvWidth(video) * getMlvBitdepth(video)) / 8;

    /* Calculate framerate */
    video->frame_rate = getMlvFramerateOrig(video);

    /* Make sure frame cache number is up to date by rerunniinitLLRawProcObjectng thiz */
    setMlvRawCacheLimitMegaBytes(video, getMlvRawCacheLimitMegaBytes(video));

    /* For frame cache */
    video->rgb_raw_frames = (uint16_t **)malloc( sizeof(uint16_t *) * video->frames );
    video->rgb_raw_current_frame_words = (uint64_t)getMlvWidth(video) * getMlvHeight(video) * 3;
    video->rgb_raw_current_frame = (uint16_t *)malloc( video->rgb_raw_current_frame_words * sizeof(uint16_t) );
    video->cached_frames = (uint8_t *)calloc( sizeof(uint8_t), video->frames );

    isMlvActive(video) = 5;

    /* Start caching unless it was disabled already */
    if (!video->stop_caching && (open_mode != MLV_OPEN_PREVIEW))
    {
        for (int i = 0; i < video->cpu_cores; ++i)
        {
            add_mlv_cache_thread(video);
        }
    }

    return MLV_ERR_NONE;
}

/* Reads an MLV file in to a mlv object(mlvObject_t struct) 
 * only puts metadata in to the mlvObject_t, 
 * no debayering or bit unpacking */
int openMlvClip(mlvObject_t * video, char * mlvPath, int open_mode, char * error_message)
{
    video->path = malloc( strlen(mlvPath) + 1 );
    memcpy(video->path, mlvPath, strlen(mlvPath));
    video->path[strlen(mlvPath)] = 0x0;
    video->file = load_all_chunks(mlvPath, &video->filenum);
    if(!video->file)
    {
        sprintf(error_message, "Could not open file:  %s", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        return MLV_ERR_OPEN; // can not open file
    }

    /* Mutexes for every file */
    video->main_file_mutex = calloc(sizeof(pthread_mutex_t), video->filenum);
    for (int i = 0; i < video->filenum; ++i)
    {
        pthread_mutex_init(video->main_file_mutex + i, NULL);
    }

    /* In preview mode we don't need to waste time on audio loading from MAPP */
    if(open_mode != MLV_OPEN_PREVIEW)
    {
        if(!load_mapp(video)) goto short_cut;
    }

    uint64_t block_num = 0; /* Number of blocks in file */
    mlv_hdr_t block_header; /* Basic MLV block header */
    uint64_t video_frames = 0; /* Number of frames in video */
    uint64_t audio_frames = 0; /* Number of audio blocks in video */
    uint32_t vers_blocks = 0; /* Number of VERS blocks in MLV */
    uint64_t video_index_max = 0; /* initial size of frame index */
    uint64_t audio_index_max = 0; /* initial size of audio index */
    uint32_t vers_index_max = 0; /* initial size of VERS index */
    int mlvi_read = 0; /* Flips to 1 if 1st chunk MLVI block was read */
    int rtci_read = 0; /* Flips to 1 if 1st RTCI block was read */
    int lens_read = 0; /* Flips to 1 if 1st LENS block was read */
    int elns_read = 0; /* Flips to 1 if 1st ELNS block was read */
    int wbal_read = 0; /* Flips to 1 if 1st WBAL block was read */
    int styl_read = 0; /* Flips to 1 if 1st STYL block was read */
    int fread_err = 1;

    for(int i = 0; i < video->filenum; i++)
    {
        /* Getting size of file in bytes */
        file_set_pos(video->file[i], 0, SEEK_END);
        uint64_t file_size = file_get_pos(video->file[i]);
        if ( !file_size )
        {
            sprintf(error_message, "Zero byte size file:  %s", video->path);
            DEBUG( printf("\n%s\n", error_message); )
            --video->filenum;
            return MLV_ERR_INVALID;
        }
        file_set_pos(video->file[i], 0, SEEK_SET); /* Start of file */

        /* Read file header */
        if ( fread(&block_header, sizeof(mlv_hdr_t), 1, video->file[i]) != 1 )
        {
            sprintf(error_message, "File is too short to be a valid MLV:  %s", video->path);
            DEBUG( printf("\n%s\n", error_message); )
            --video->filenum;
            return MLV_ERR_INVALID;
        }
        file_set_pos(video->file[i], 0, SEEK_SET); /* Start of file */

        if ( memcmp(block_header.blockType, "MLVI", 4) == 0 )
        {
            if( !mlvi_read )
            {
                fread_err &= fread(&video->MLVI, sizeof(mlv_file_hdr_t), 1, video->file[i]);
                mlvi_read = 1; // read MLVI only for first chunk
            }
        }
        else
        {
            sprintf(error_message, "File header is missing, invalid MLV:  %s", video->path);
            DEBUG( printf("\n%s\n", error_message); )
            --video->filenum;
            return MLV_ERR_INVALID;
        }

        while ( file_get_pos(video->file[i]) < file_size ) /* Check if were at end of file yet */
        {
            /* Record position to go back to it later if block is read */
            uint64_t block_start = file_get_pos(video->file[i]);
            /* Read block header */
            fread_err &= fread(&block_header, sizeof(mlv_hdr_t), 1, video->file[i]);
            if(block_header.blockSize < sizeof(mlv_hdr_t))
            {
                sprintf(error_message, "Invalid blockSize '%u', corrupted file:  %s", block_header.blockSize, video->path);
                DEBUG( printf("\n%s\n", error_message); )
                --video->filenum;
                return MLV_ERR_INVALID;
            }

            /* Next block location */
            uint64_t next_block = (uint64_t)block_start + (uint64_t)block_header.blockSize;
            /* Go back to start of block for next bit */
            file_set_pos(video->file[i], block_start, SEEK_SET);

            /* Now check what kind of block it is and read it in to the mlv object */
            if ( memcmp(block_header.blockType, "NULL", 4) == 0 || memcmp(block_header.blockType, "BKUP", 4) == 0)
            {
                /* do nothing, skip this block */
            }
            else if ( memcmp(block_header.blockType, "VIDF", 4) == 0 )
            {
                fread_err &= fread(&video->VIDF, sizeof(mlv_vidf_hdr_t), 1, video->file[i]);

                DEBUG( printf("video frame %i | chunk %i | size %lu | offset %lu | time %lu\n",
                               video->VIDF.frameNumber, i, video->VIDF.blockSize - sizeof(mlv_vidf_hdr_t) - video->VIDF.frameSpace,
                               block_start + video->VIDF.frameSpace, video->VIDF.timestamp); )

                /* Dynamically resize the frame index buffer */
                if(!video_index_max)
                {
                    video_index_max = 128;
                    video->video_index = (frame_index_t *)calloc(video_index_max, sizeof(frame_index_t));
                }
                else if(video_frames >= video_index_max - 1)
                {
                    uint64_t video_index_new_size = video_index_max * 2;
                    frame_index_t * video_index_new = (frame_index_t *)calloc(video_index_new_size, sizeof(frame_index_t));
                    memcpy(video_index_new, video->video_index, video_index_max * sizeof(frame_index_t));
                    free(video->video_index);
                    video->video_index = video_index_new;
                    video_index_max = video_index_new_size;
                }

                /* Fill frame index */
                video->video_index[video_frames].frame_type = 1;
                video->video_index[video_frames].chunk_num = i;
                video->video_index[video_frames].frame_size = video->VIDF.blockSize - sizeof(mlv_vidf_hdr_t) - video->VIDF.frameSpace;
                video->video_index[video_frames].frame_offset = file_get_pos(video->file[i]) + video->VIDF.frameSpace;
                video->video_index[video_frames].frame_number = video->VIDF.frameNumber;
                video->video_index[video_frames].frame_time = video->VIDF.timestamp;
                video->video_index[video_frames].block_offset = block_start;

                /* Count actual video frames */
                video_frames++;

                /* In preview mode exit loop after first videf read */
                if(open_mode == MLV_OPEN_PREVIEW)
                {
                    video->frames = video_frames;
                    video->audios = audio_frames;
                    goto preview_out;
                }
            }
            else if ( memcmp(block_header.blockType, "AUDF", 4) == 0 )
            {
                fread_err &= fread(&video->AUDF, sizeof(mlv_audf_hdr_t), 1, video->file[i]);

                DEBUG( printf("audio frame %i | chunk %i | size %lu | offset %lu | time %lu\n",
                               video->AUDF.frameNumber, i, video->AUDF.blockSize - sizeof(mlv_audf_hdr_t) - video->AUDF.frameSpace,
                               block_start + video->AUDF.frameSpace, video->AUDF.timestamp); )

                /* Dynamically resize the audio index buffer */
                if(!audio_index_max)
                {
                    audio_index_max = 32;
                    video->audio_index = (frame_index_t *)malloc(sizeof(frame_index_t) * audio_index_max);
                }
                else if(audio_frames >= audio_index_max - 1)
                {
                    uint64_t audio_index_new_size = audio_index_max * 2;
                    frame_index_t * audio_index_new = (frame_index_t *)calloc(audio_index_new_size, sizeof(frame_index_t));
                    memcpy(audio_index_new, video->audio_index, audio_index_max * sizeof(frame_index_t));
                    free(video->audio_index);
                    video->audio_index = audio_index_new;
                    audio_index_max = audio_index_new_size;
                }

                /* Fill audio index */
                video->audio_index[audio_frames].frame_type = 2;
                video->audio_index[audio_frames].chunk_num = i;
                video->audio_index[audio_frames].frame_size = video->AUDF.blockSize - sizeof(mlv_audf_hdr_t) - video->AUDF.frameSpace;
                video->audio_index[audio_frames].frame_offset = file_get_pos(video->file[i]) + video->AUDF.frameSpace;
                video->audio_index[audio_frames].frame_number = video->AUDF.frameNumber;
                video->audio_index[audio_frames].frame_time = video->AUDF.timestamp;
                video->audio_index[audio_frames].block_offset = block_start;

                /* Count actual audio frames */
                audio_frames++;
            }
            else if ( memcmp(block_header.blockType, "RAWI", 4) == 0 )
            {
                fread_err &= fread(&video->RAWI, sizeof(mlv_rawi_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "RAWC", 4) == 0 )
            {
                fread_err &= fread(&video->RAWC, sizeof(mlv_rawc_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "WAVI", 4) == 0 )
            {
                fread_err &= fread(&video->WAVI, sizeof(mlv_wavi_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "EXPO", 4) == 0 )
            {
                fread_err &= fread(&video->EXPO, sizeof(mlv_expo_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "LENS", 4) == 0 )
            {
                if( !lens_read )
                {
                    fread_err &= fread(&video->LENS, sizeof(mlv_lens_hdr_t), 1, video->file[i]);
                    lens_read = 1; //read only first one
                    //Terminate string, if it isn't terminated.
                    for( int n = 0; n < 32; n++ )
                    {
                        if( video->LENS.lensName[n] == '\0' ) break;
                        if( n == 31 ) video->LENS.lensName[n] = '\0';
                    }
                }
            }
            else if ( memcmp(block_header.blockType, "ELNS", 4) == 0 )
            {
                if( !elns_read )
                {
                    fread_err &= fread(&video->ELNS, sizeof(mlv_elns_hdr_t), 1, video->file[i]);
                    elns_read = 1; //read only first one
                }
            }
            else if ( memcmp(block_header.blockType, "WBAL", 4) == 0 )
            {
                if( !wbal_read )
                {
                    fread_err &= fread(&video->WBAL, sizeof(mlv_wbal_hdr_t), 1, video->file[i]);
                    wbal_read = 1; //read only first one
                }
            }
            else if ( memcmp(block_header.blockType, "STYL", 4) == 0 )
            {
                if( !styl_read )
                {
                    fread_err &= fread(&video->STYL, sizeof(mlv_styl_hdr_t), 1, video->file[i]);
                    styl_read = 1; //read only first one
                }
            }
            else if ( memcmp(block_header.blockType, "RTCI", 4) == 0 )
            {
                if( !rtci_read )
                {
                    fread_err &= fread(&video->RTCI, sizeof(mlv_rtci_hdr_t), 1, video->file[i]);
                    rtci_read = 1; //read only first one
                }
            }
            else if ( memcmp(block_header.blockType, "IDNT", 4) == 0 )
            {
                fread_err &= fread(&video->IDNT, sizeof(mlv_idnt_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "INFO", 4) == 0 )
            {
                fread_err &= fread(&video->INFO, sizeof(mlv_info_hdr_t), 1, video->file[i]);
                if(video->INFO.blockSize > sizeof(mlv_info_hdr_t))
                {
                    fread_err &= fread(&video->INFO_STRING, video->INFO.blockSize - sizeof(mlv_info_hdr_t), 1, video->file[i]);
                }
            }
            else if ( memcmp(block_header.blockType, "DISO", 4) == 0 )
            {
                fread_err &= fread(&video->DISO, sizeof(mlv_diso_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "MARK", 4) == 0 )
            {
                /* do nothing atm */
                //fread(&video->MARK, sizeof(mlv_mark_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "ELVL", 4) == 0 )
            {
                /* do nothing atm */
                //fread(&video->ELVL, sizeof(mlv_elvl_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "DEBG", 4) == 0 )
            {
                /* do nothing atm */
                //fread(&video->DEBG, sizeof(mlv_debg_hdr_t), 1, video->file[i]);
            }
            else if ( memcmp(block_header.blockType, "VERS", 4) == 0 )
            {
                /* Find all VERS blocks and make index for them */
                fread_err &= fread(&video->VERS, sizeof(mlv_vers_hdr_t), 1, video->file[i]);

                DEBUG( printf("VERS blocknum %i | chunk %i | size %lu | offset %lu | time %lu\n",
                               vers_blocks, i, video->VERS.blockSize - sizeof(mlv_vers_hdr_t),
                               block_start, video->VERS.timestamp); )

                /* Dynamically resize the index buffer */
                if(!vers_index_max)
                {
                    vers_index_max = 128;
                    video->vers_index = (frame_index_t *)calloc(vers_index_max, sizeof(frame_index_t));
                }
                else if(vers_blocks >= vers_index_max - 1)
                {
                    uint64_t vers_index_new_size = vers_index_max * 2;
                    frame_index_t * vers_index_new = (frame_index_t *)calloc(vers_index_new_size, sizeof(frame_index_t));
                    memcpy(vers_index_new, video->vers_index, vers_index_max * sizeof(frame_index_t));
                    free(video->vers_index);
                    video->vers_index = vers_index_new;
                    vers_index_max = vers_index_new_size;
                }

                /* Fill frame index */
                video->vers_index[vers_blocks].frame_type = 3;
                video->vers_index[vers_blocks].chunk_num = i;
                video->vers_index[vers_blocks].frame_size = video->VERS.blockSize - sizeof(mlv_vers_hdr_t);
                video->vers_index[vers_blocks].frame_offset = file_get_pos(video->file[i]);
                video->vers_index[vers_blocks].frame_number = vers_blocks;
                video->vers_index[vers_blocks].frame_time = video->VERS.timestamp;
                video->vers_index[vers_blocks].block_offset = block_start;

                /* Count actual VERS blocks */
                vers_blocks++;
            }
            else if ( memcmp(block_header.blockType, "DARK", 4) == 0 )
            {
                fread_err &= fread(&video->DARK, sizeof(mlv_dark_hdr_t), 1, video->file[i]);
                video->dark_frame_offset = file_get_pos(video->file[i]);
            }
            else
            {
                /* block name is wrong, so try to brute force the position of next valid block */
                if(!seek_to_next_known_block(video->file[i]))
                {
                    char block_type[5] = { 0 };
                    memcpy(block_type, block_header.blockType, 4);
                    sprintf(error_message, "Unknown blockType '%s' or corrupted file:  %s", block_type, video->path);
                    DEBUG( printf("\n%s\n", error_message); )
                            --video->filenum;
                    return MLV_ERR_CORRUPTED;
                }
                continue;
            }

            /* Printing stuff for fun */
            //DEBUG( printf("Block #%4i  |  %.4s  |%9i Bytes\n", block_num, block_header.blockType, block_header.blockSize); )

            /* Move to next block */
            file_set_pos(video->file[i], next_block, SEEK_SET);

            block_num++;
        }
    }

    /* Return with error if no video frames found */
    if(!fread_err)
    {
        sprintf(error_message, "File read error:  %s", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        --video->filenum;
        return MLV_ERR_IO;
    }
    /* Return with error if no video frames found */
    if(!video_frames)
    {
        sprintf(error_message, "No video frames found in:  %s", video->path);
        DEBUG( printf("\n%s\n", error_message); )
        --video->filenum;
        return MLV_ERR_INVALID;
    }

    /* Set total block amount in mlv */
    video->block_num = block_num;

    /* Sort video and audio frames by time stamp */
    if(video_frames) frame_index_sort(video->video_index, video_frames);
    if(audio_frames) frame_index_sort(video->audio_index, audio_frames);

    /* Set frame count in video object */
    video->frames = video_frames;
    /* Set audio count in video object */
    video->audios = audio_frames;
    /* Set VERS block count in video object */
    video->vers_blocks = vers_blocks;

    /* Reads MLV audio into buffer (video->audio_data) and sync it,
     * set full audio buffer size (video->audio_buffer_size) and
     * aligned usable audio data size (video->audio_size) */
    readMlvAudioData(video);

    /* Save mapp file if this feature is on */
    if(open_mode == MLV_OPEN_MAPP) save_mapp(video);

short_cut:

    /* Set imaginary lossless bit depth */
    setMlvLosslessBpp(video);
    /* Check and set dual iso validity */
    llrpSetDualIsoValidity(video, 0);

preview_out:

    /* NON compressed frame size */
    video->frame_size = (getMlvHeight(video) * getMlvWidth(video) * getMlvBitdepth(video)) / 8;
    /* Calculate framerate */
    video->frame_rate = getMlvFramerateOrig(video);

    /* Make sure frame cache number is up to date by rerunniinitLLRawProcObjectng thiz */
    setMlvRawCacheLimitMegaBytes(video, getMlvRawCacheLimitMegaBytes(video));

    /* For frame cache */
    video->rgb_raw_frames = (uint16_t **)malloc( sizeof(uint16_t *) * video->frames );
    video->rgb_raw_current_frame_words = (uint64_t)getMlvWidth(video) * getMlvHeight(video) * 3;
    video->rgb_raw_current_frame = (uint16_t *)malloc( video->rgb_raw_current_frame_words * sizeof(uint16_t) );
    video->cached_frames = (uint8_t *)calloc( sizeof(uint8_t), video->frames );

    isMlvActive(video) = 1;

    /* Start caching unless it was disabled already */
    if (!video->stop_caching && (open_mode != MLV_OPEN_PREVIEW))
    {
        for (int i = 0; i < video->cpu_cores; ++i)
        {
            add_mlv_cache_thread(video);
        }
    }

    return MLV_ERR_NONE;
}

void setMlvLosslessBpp(mlvObject_t * video)
{
    /* Calculate imaginary bit depth for restricted lossledd raw data */
    video->lossless_bpp = ceil( log2( getMlvWhiteLevel(video) - getMlvBlackLevel(video) ) );
}

/* Get image aspect ratio according to RAWC block info, calculating from binnin + skipping values.
   Returns aspect ratio or 0 in case if RAWC block is not present in MLV file */
float getMlvAspectRatio(mlvObject_t * video)
{
    if(video->RAWC.blockType[0])
    {
        int sampling_x = video->RAWC.binning_x + video->RAWC.skipping_x;
        int sampling_y = video->RAWC.binning_y + video->RAWC.skipping_y;

        if( sampling_x == 0 ) return 0;
        return ( (float)sampling_y / (float)sampling_x );
    }
    return 0;
}

void printMlvInfo(mlvObject_t * video)
{
    printf("\nMLV Info\n\n");
    printf("      MLV Version: %s\n", video->MLVI.versionString);
    printf("      File Blocks: %lu\n", video->block_num);
    printf("\nLens Info\n\n");
    printf("       Lens Model: %s\n", video->LENS.lensName);
    printf("    Serial Number: %s\n", video->LENS.lensSerial);
    printf("\nCamera Info\n\n");
    printf("     Camera Model: %s\n", video->IDNT.cameraName);
    printf("    Serial Number: %s\n", video->IDNT.cameraSerial);
    printf("\nVideo Info\n\n");
    printf("     X Resolution: %i\n", video->RAWI.xRes);
    printf("     Y Resolution: %i\n", video->RAWI.yRes);
    printf("     Total Frames: %i\n", video->frames);
    printf("       Frame Rate: %.3f\n", video->frame_rate);
    printf("\nExposure Info\n\n");
    printf("          Shutter: 1/%.1f\n", (float)1000000 / (float)video->EXPO.shutterValue);
    printf("      ISO Setting: %i\n", video->EXPO.isoValue);
    printf("     Digital Gain: %i\n", video->EXPO.digitalGain);
    printf("\nRAW Info\n\n");
    printf("      Black Level: %i\n", video->RAWI.raw_info.black_level);
    printf("      White Level: %i\n", video->RAWI.raw_info.white_level);
    printf("     Bits / Pixel: %i\n\n", video->RAWI.raw_info.bits_per_pixel);
}

/* WB Picker function; selects the pic and sends task to processing module */
void findMlvWhiteBalance(mlvObject_t *video, uint64_t frameIndex, int posX, int posY, int *wbTemp, int *wbTint, int mode)
{
    /* Useful */
    int width = getMlvWidth(video);
    int height = getMlvHeight(video);

    /* Size of RAW frame */
    int rgb_frame_size = height * width * 3;

    /* Unprocessed debayered frame (RGB) */
    uint16_t * unprocessed_frame = malloc( rgb_frame_size * sizeof(uint16_t) );

    /* Get the raw data in B&W */
    getMlvRawFrameDebayered(video, frameIndex, unprocessed_frame);

    /* find WB.......... */
    processingFindWhiteBalance( video->processing,
                                width, height,
                                unprocessed_frame,
                                posX, posY,
                                wbTemp, wbTint, mode);

    free(unprocessed_frame);
}
