#ifndef MLVAPP_STAGE_TIMING_H
#define MLVAPP_STAGE_TIMING_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <omp.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_MSC_VER)
#define MLV_STAGE_THREAD_LOCAL __declspec(thread)
#else
#define MLV_STAGE_THREAD_LOCAL __thread
#endif

enum { MLV_STAGE_TIMING_SNAPSHOT_CAPACITY = 32 };

typedef struct
{
    char stage[48];
    double elapsed_ms;
    int used;
} mlv_stage_timing_snapshot_entry_t;

typedef struct
{
    uint64_t frame_index;
    int has_frame_index;
    mlv_stage_timing_snapshot_entry_t entries[MLV_STAGE_TIMING_SNAPSHOT_CAPACITY];
} mlv_stage_timing_snapshot_t;

static MLV_STAGE_THREAD_LOCAL mlv_stage_timing_snapshot_t g_mlv_stage_timing_snapshot = { 0 };

static inline int mlv_stage_timing_enabled(void)
{
    static int initialized = 0;
    static int enabled = 0;
    if (!initialized)
    {
        const char * env = getenv("MLVAPP_STAGE_TIMING");
        enabled = (env != NULL && env[0] != '\0' && strcmp(env, "0") != 0);
        initialized = 1;
    }
    return enabled;
}

static inline FILE * mlv_stage_timing_stream(void)
{
    static int initialized = 0;
    static FILE * stream = NULL;

    if (!initialized)
    {
        const char * path = getenv("MLVAPP_STAGE_TIMING_FILE");
        if (path && path[0] != '\0')
        {
            stream = fopen(path, "a");
        }
        initialized = 1;
    }

    return stream ? stream : stderr;
}

static inline double mlv_stage_timing_now(void)
{
    return omp_get_wtime();
}

static inline void mlv_stage_timing_reset_snapshot(void)
{
    memset(&g_mlv_stage_timing_snapshot, 0, sizeof(g_mlv_stage_timing_snapshot));
}

static inline void mlv_stage_timing_note_snapshot(const char * stage, uint64_t frameIndex, double elapsed_ms)
{
    if (!stage || !stage[0])
    {
        return;
    }

    g_mlv_stage_timing_snapshot.frame_index = frameIndex;
    g_mlv_stage_timing_snapshot.has_frame_index = 1;

    for (int index = 0; index < MLV_STAGE_TIMING_SNAPSHOT_CAPACITY; ++index)
    {
        mlv_stage_timing_snapshot_entry_t * entry = &g_mlv_stage_timing_snapshot.entries[index];
        if (entry->used && strcmp(entry->stage, stage) == 0)
        {
            entry->elapsed_ms = elapsed_ms;
            return;
        }
        if (!entry->used)
        {
            strncpy(entry->stage, stage, sizeof(entry->stage) - 1);
            entry->stage[sizeof(entry->stage) - 1] = '\0';
            entry->elapsed_ms = elapsed_ms;
            entry->used = 1;
            return;
        }
    }
}

static inline const mlv_stage_timing_snapshot_t * mlv_stage_timing_get_snapshot(void)
{
    return &g_mlv_stage_timing_snapshot;
}

static inline void mlv_stage_timing_note(const char * stage, uint64_t frameIndex, double startTime)
{
    const double elapsed_ms = (mlv_stage_timing_now() - startTime) * 1000.0;
    mlv_stage_timing_note_snapshot(stage, frameIndex, elapsed_ms);
    if (!mlv_stage_timing_enabled()) return;
    FILE * stream = mlv_stage_timing_stream();
    fprintf(stream, "[mlv-stage-timing] frame=%llu stage=%s ms=%.3f\n",
            (unsigned long long)frameIndex, stage, elapsed_ms);
    fflush(stream);
}

static inline void mlv_stage_timing_note_elapsed(const char * stage, uint64_t frameIndex, double elapsed_ms)
{
    mlv_stage_timing_note_snapshot(stage, frameIndex, elapsed_ms);
    if (!mlv_stage_timing_enabled()) return;
    FILE * stream = mlv_stage_timing_stream();
    fprintf(stream, "[mlv-stage-timing] frame=%llu stage=%s ms=%.3f\n",
            (unsigned long long)frameIndex, stage, elapsed_ms);
    fflush(stream);
}

#ifdef __cplusplus
}
#endif

#endif
