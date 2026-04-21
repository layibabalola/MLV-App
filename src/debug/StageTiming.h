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

static inline double mlv_stage_timing_now(void)
{
    return omp_get_wtime();
}

static inline void mlv_stage_timing_note(const char * stage, uint64_t frameIndex, double startTime)
{
    if (!mlv_stage_timing_enabled()) return;
    const double elapsed_ms = (mlv_stage_timing_now() - startTime) * 1000.0;
    fprintf(stderr, "[mlv-stage-timing] frame=%llu stage=%s ms=%.3f\n",
            (unsigned long long)frameIndex, stage, elapsed_ms);
}

#ifdef __cplusplus
}
#endif

#endif
