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
#include "../../../tools/gpu/igpu_recon.h"
#include "../../debug/StageTiming.h"
#include "../../processing/raw_processing.h"
#include "../pipeline_stage_capture.h"
#include "../video_mlv.h"

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

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
static MLV_THREAD_LOCAL double g_llrawproc_last_pre_dualiso_fix_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_dual_iso_ms = 0.0;
static MLV_THREAD_LOCAL double g_llrawproc_last_chroma_smooth_ms = 0.0;
static MLV_THREAD_LOCAL dualiso_full20bit_timing_t g_llrawproc_last_dual_iso_full20bit_timing = {0};
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

static void llrawproc_reset_dual_iso_full20bit_timing(void)
{
    memset(&g_llrawproc_last_dual_iso_full20bit_timing,
           0,
           sizeof(g_llrawproc_last_dual_iso_full20bit_timing));
    g_llrawproc_last_dual_iso_full20bit_timing.interp_method = -1;
    g_llrawproc_last_dual_iso_full20bit_timing.final_blend_probe_mode = -1;
}

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

/* Phase E5: peer of MLVAPP_DISABLE_DUALISO_PLAYBACK_MEAN23_OVERRIDE for the
 * scale-aware alias_map / FR blending downgrade. The downgrade is opt-in
 * (default OFF) at the GUI policy layer, so this env-disable is mostly a
 * symmetry tool — set MLVAPP_DISABLE_ALIAS_MAP_DOWNGRADE_OVERRIDE=1 to
 * make the per-frame fast-path treat diso_playback_force_disable_alias_map
 * and diso_playback_force_disable_fr_blending as if they were 0, even
 * when the GUI policy has flipped them on (e.g. via the
 * MLVAPP_PLAYBACK_DOWNGRADE_ALIAS_MAP_AT_SCALE=1 opt-in). Useful for
 * headless A/B harnesses that want to force the field on for cache-key
 * tests but still measure the full receipt-authored pipeline cost.
 *
 * Cache reset hook for tests: llrpReinitKeepHeavyStagesAtScaleDispatchForTesting. */
static int g_dualiso_playback_disable_alias_map_downgrade_env_cache = -1;

static int dualiso_playback_alias_map_downgrade_disabled_via_env(void)
{
    if (g_dualiso_playback_disable_alias_map_downgrade_env_cache < 0)
    {
        const char * v = getenv("MLVAPP_DISABLE_ALIAS_MAP_DOWNGRADE_OVERRIDE");
        if (v && *v && strcmp(v, "0") != 0
                  && strcmp(v, "false") != 0
                  && strcmp(v, "FALSE") != 0
                  && strcmp(v, "False") != 0)
        {
            g_dualiso_playback_disable_alias_map_downgrade_env_cache = 1;
        }
        else
        {
            g_dualiso_playback_disable_alias_map_downgrade_env_cache = 0;
        }
    }
    return g_dualiso_playback_disable_alias_map_downgrade_env_cache;
}

int llrpReinitKeepHeavyStagesAtScaleDispatchForTesting(void);
int llrpReinitKeepHeavyStagesAtScaleDispatchForTesting(void)
{
    g_dualiso_playback_disable_alias_map_downgrade_env_cache = -1;
    return dualiso_playback_alias_map_downgrade_disabled_via_env();
}

static int llrawproc_env_truthy_value(const char * v)
{
    return v && *v
        && strcmp(v, "0") != 0
        && strcmp(v, "false") != 0
        && strcmp(v, "FALSE") != 0
        && strcmp(v, "False") != 0;
}

static int llrawproc_gpu_export_enabled(void)
{
    return llrawproc_env_truthy_value(getenv("MLVAPP_GPU_EXPORT"));
}

static MLV_THREAD_LOCAL int g_llrawproc_gpu_playback_recon_allowed = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_playback_texture_present_preferred = 0;

void llrpSetGpuPlaybackReconAllowedForCurrentThread(int enabled);
void llrpSetGpuPlaybackReconAllowedForCurrentThread(int enabled)
{
    g_llrawproc_gpu_playback_recon_allowed = enabled != 0;
}

void llrpSetGpuPlaybackReconTexturePresentPreferredForCurrentThread(int enabled);
void llrpSetGpuPlaybackReconTexturePresentPreferredForCurrentThread(int enabled)
{
    g_llrawproc_gpu_playback_texture_present_preferred = enabled != 0;
}

static int llrawproc_gpu_playback_recon_enabled(void)
{
    return g_llrawproc_gpu_playback_recon_allowed
        && llrawproc_env_truthy_value(getenv("MLVAPP_GPU_PLAYBACK_RECON"));
}

static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_run_attempted = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_run_rc = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_replaced = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_mismatch = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_apply_dither = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_allocated_bytes_valid = 0;
static MLV_THREAD_LOCAL uint64_t g_llrawproc_gpu_export_last_allocated_bytes = 0;
static MLV_THREAD_LOCAL unsigned long long g_llrawproc_gpu_export_last_mismatch_count = 0;
static MLV_THREAD_LOCAL unsigned long long g_llrawproc_gpu_export_last_mismatch_first_index = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_mismatch_first_cpu = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_mismatch_first_gpu = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_export_last_mismatch_max_abs = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_playback_last_run_attempted = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_playback_last_run_rc = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_playback_last_used = 0;
static MLV_THREAD_LOCAL int g_llrawproc_gpu_playback_last_state_valid = 0;
static MLV_THREAD_LOCAL dualiso_gpu_recon_state_t g_llrawproc_gpu_playback_last_prepared_state = {0};

static void llrawproc_gpu_export_reset_last_run_state(void)
{
    g_llrawproc_gpu_export_last_run_attempted = 0;
    g_llrawproc_gpu_export_last_run_rc = 0;
    g_llrawproc_gpu_export_last_replaced = 0;
    g_llrawproc_gpu_export_last_mismatch = 0;
    g_llrawproc_gpu_export_last_apply_dither = 0;
    g_llrawproc_gpu_export_last_allocated_bytes_valid = 0;
    g_llrawproc_gpu_export_last_allocated_bytes = 0;
    g_llrawproc_gpu_export_last_mismatch_count = 0;
    g_llrawproc_gpu_export_last_mismatch_first_index = 0;
    g_llrawproc_gpu_export_last_mismatch_first_cpu = 0;
    g_llrawproc_gpu_export_last_mismatch_first_gpu = 0;
    g_llrawproc_gpu_export_last_mismatch_max_abs = 0;
}

static void llrawproc_gpu_playback_reset_last_run_state(void)
{
    g_llrawproc_gpu_playback_last_run_attempted = 0;
    g_llrawproc_gpu_playback_last_run_rc = 0;
    g_llrawproc_gpu_playback_last_used = 0;
    g_llrawproc_gpu_playback_last_state_valid = 0;
    memset(&g_llrawproc_gpu_playback_last_prepared_state, 0,
           sizeof(g_llrawproc_gpu_playback_last_prepared_state));
}

int llrpResetGpuExportRunForTesting(void);
int llrpResetGpuExportRunForTesting(void)
{
    llrawproc_gpu_export_reset_last_run_state();
    return 1;
}

int llrpResetGpuPlaybackReconRunForTesting(void);
int llrpResetGpuPlaybackReconRunForTesting(void)
{
    llrawproc_gpu_playback_reset_last_run_state();
    return 1;
}

int llrpGpuExportLastRunAttemptedForTesting(void);
int llrpGpuExportLastRunAttemptedForTesting(void)
{
    return g_llrawproc_gpu_export_last_run_attempted;
}

int llrpGpuExportLastRunRcForTesting(void);
int llrpGpuExportLastRunRcForTesting(void)
{
    return g_llrawproc_gpu_export_last_run_rc;
}

int llrpGpuExportLastReplacedForTesting(void);
int llrpGpuExportLastReplacedForTesting(void)
{
    return g_llrawproc_gpu_export_last_replaced;
}

int llrpGpuExportLastMismatchForTesting(void);
int llrpGpuExportLastMismatchForTesting(void)
{
    return g_llrawproc_gpu_export_last_mismatch;
}

int llrpGpuExportLastApplyDitherForTesting(void);
int llrpGpuExportLastApplyDitherForTesting(void)
{
    return g_llrawproc_gpu_export_last_apply_dither;
}

unsigned long long llrpGpuExportLastMismatchCountForTesting(void);
unsigned long long llrpGpuExportLastMismatchCountForTesting(void)
{
    return g_llrawproc_gpu_export_last_mismatch_count;
}

unsigned long long llrpGpuExportLastMismatchFirstIndexForTesting(void);
unsigned long long llrpGpuExportLastMismatchFirstIndexForTesting(void)
{
    return g_llrawproc_gpu_export_last_mismatch_first_index;
}

int llrpGpuExportLastMismatchFirstCpuForTesting(void);
int llrpGpuExportLastMismatchFirstCpuForTesting(void)
{
    return g_llrawproc_gpu_export_last_mismatch_first_cpu;
}

int llrpGpuExportLastMismatchFirstGpuForTesting(void);
int llrpGpuExportLastMismatchFirstGpuForTesting(void)
{
    return g_llrawproc_gpu_export_last_mismatch_first_gpu;
}

int llrpGpuExportLastMismatchMaxAbsForTesting(void);
int llrpGpuExportLastMismatchMaxAbsForTesting(void)
{
    return g_llrawproc_gpu_export_last_mismatch_max_abs;
}

int llrpGpuPlaybackReconLastRunAttemptedForTesting(void);
int llrpGpuPlaybackReconLastRunAttemptedForTesting(void)
{
    return g_llrawproc_gpu_playback_last_run_attempted;
}

int llrpGpuPlaybackReconLastRunRcForTesting(void);
int llrpGpuPlaybackReconLastRunRcForTesting(void)
{
    return g_llrawproc_gpu_playback_last_run_rc;
}

int llrpGpuPlaybackReconLastUsedForTesting(void);
int llrpGpuPlaybackReconLastUsedForTesting(void)
{
    return g_llrawproc_gpu_playback_last_used;
}

int llrpGpuPlaybackReconLastStateValidForTesting(void);
int llrpGpuPlaybackReconLastStateValidForTesting(void)
{
    return g_llrawproc_gpu_playback_last_state_valid;
}

static void llrawproc_gpu_playback_public_state_from_dualiso(
    const dualiso_gpu_recon_state_t * state,
    llrpGpuPlaybackReconState_t * out)
{
    if(!out) return;
    memset(out, 0, sizeof(*out));
    if(!state || !state->valid) return;

    out->valid = state->valid;
    out->width = state->width;
    out->height = state->height;
    out->black_level = state->black_level;
    out->white_level = state->white_level;
    out->white_darkened = state->white_darkened;
    out->black_delta = state->black_delta;
    out->ev_correction = state->ev_correction;
    out->dark_noise = state->dark_noise;
    out->interp_method = state->interp_method;
    out->use_alias_map = state->use_alias_map;
    out->use_fullres = state->use_fullres;
    out->chroma_smooth_method = state->chroma_smooth_method;
    memcpy(out->is_bright, state->is_bright, sizeof(out->is_bright));
    out->raw2ev = state->raw2ev;
    out->ev2raw = state->ev2raw;
    out->mix_curve = state->mix_curve;
    out->fullres_curve = state->fullres_curve;
    out->randn05 = state->randn05;
    out->apply_dither = state->apply_dither;
}

static int llrawproc_gpu_playback_dualiso_state_from_public(
    const llrpGpuPlaybackReconState_t * state,
    dualiso_gpu_recon_state_t * out)
{
    if(!out) return 0;
    memset(out, 0, sizeof(*out));
    if(!state || !state->valid) return 0;
    if(state->width <= 0 || state->height <= 0) return 0;
    if(!state->raw2ev || !state->ev2raw || !state->mix_curve || !state->fullres_curve) return 0;
    if(state->apply_dither && !state->randn05) return 0;

    out->valid = state->valid;
    out->width = state->width;
    out->height = state->height;
    out->black_level = state->black_level;
    out->white_level = state->white_level;
    out->white_darkened = state->white_darkened;
    out->black_delta = state->black_delta;
    out->ev_correction = state->ev_correction;
    out->dark_noise = state->dark_noise;
    out->interp_method = state->interp_method;
    out->use_alias_map = state->use_alias_map;
    out->use_fullres = state->use_fullres;
    out->chroma_smooth_method = state->chroma_smooth_method;
    memcpy(out->is_bright, state->is_bright, sizeof(out->is_bright));
    out->raw2ev = state->raw2ev;
    out->ev2raw = state->ev2raw;
    out->mix_curve = state->mix_curve;
    out->fullres_curve = state->fullres_curve;
    out->randn05 = state->randn05;
    out->apply_dither = state->apply_dither;
    return 1;
}

int llrpGpuPlaybackReconGetLastPreparedState(llrpGpuPlaybackReconState_t * state);
int llrpGpuPlaybackReconGetLastPreparedState(llrpGpuPlaybackReconState_t * state)
{
    llrawproc_gpu_playback_public_state_from_dualiso(
        &g_llrawproc_gpu_playback_last_prepared_state,
        state);
    return state && state->valid;
}

void llrpGetLastGpuExportTelemetry(llrpGpuExportTelemetry_t * telemetry)
{
    if(!telemetry) return;
    memset(telemetry, 0, sizeof(*telemetry));
    telemetry->attempted = g_llrawproc_gpu_export_last_run_attempted;
    telemetry->rc = g_llrawproc_gpu_export_last_run_rc;
    telemetry->replaced = g_llrawproc_gpu_export_last_replaced;
    telemetry->allocated_bytes_valid =
        g_llrawproc_gpu_export_last_allocated_bytes_valid;
    telemetry->allocated_bytes = g_llrawproc_gpu_export_last_allocated_bytes;
}

#if defined(_WIN32)
typedef igpu_recon_backend* (*llrawproc_gpu_create_fn)(const char*);
typedef void (*llrawproc_gpu_destroy_fn)(igpu_recon_backend*);
typedef int (*llrawproc_gpu_abi_version_fn)(igpu_recon_backend*);
typedef const char* (*llrawproc_gpu_describe_fn)(igpu_recon_backend*);
typedef int (*llrawproc_gpu_set_clip_fn)(igpu_recon_backend*, const igpu_recon_clip_t*);
typedef int (*llrawproc_gpu_set_luts_fn)(igpu_recon_backend*, const igpu_recon_luts_t*);
typedef int (*llrawproc_gpu_run_fn)(igpu_recon_backend*,
                                    const igpu_recon_frame_t*,
                                    const uint16_t*,
                                    igpu_recon_out_kind,
                                    uint16_t*,
                                    unsigned int);
typedef int (*llrawproc_gpu_last_timing_fn)(igpu_recon_backend*, igpu_recon_timing_t*);
typedef int (*llrawproc_gpu_allocated_bytes_fn)(igpu_recon_backend*, uint64_t*);

typedef struct
{
    HMODULE dll;
    int attempted;
    int unavailable;
    char dll_path[1024];
    char dll_resolved_path[1024];
    igpu_recon_backend * backend;
    llrawproc_gpu_create_fn create;
    llrawproc_gpu_destroy_fn destroy;
    llrawproc_gpu_abi_version_fn abi_version;
    llrawproc_gpu_describe_fn describe;
    llrawproc_gpu_set_clip_fn set_clip;
    llrawproc_gpu_set_luts_fn set_luts;
    llrawproc_gpu_run_fn run;
    llrawproc_gpu_last_timing_fn last_timing;
    llrawproc_gpu_allocated_bytes_fn allocated_bytes;
} llrawprocGpuExportBackend_t;

static llrawprocGpuExportBackend_t g_llrawproc_gpu_export_backend = {0};
static pthread_mutex_t g_llrawproc_gpu_recon_backend_mutex = PTHREAD_MUTEX_INITIALIZER;

int llrpGpuPlaybackReconGetBackendInfo(llrpGpuPlaybackReconBackendInfo_t * info);
int llrpGpuPlaybackReconGetBackendInfo(llrpGpuPlaybackReconBackendInfo_t * info)
{
    llrawprocGpuExportBackend_t * g = &g_llrawproc_gpu_export_backend;
    const char * description = NULL;
    if(!info) return 0;
    memset(info, 0, sizeof(*info));

    pthread_mutex_lock(&g_llrawproc_gpu_recon_backend_mutex);
    info->available = (g->backend && !g->unavailable) ? 1 : 0;
    info->attempted = g->attempted;
    info->unavailable = g->unavailable;
    snprintf(info->requested_path,
             sizeof(info->requested_path),
             "%s",
             g->dll_path);
    snprintf(info->resolved_path,
             sizeof(info->resolved_path),
             "%s",
             g->dll_resolved_path);
    if(g->backend && g->describe)
    {
        description = g->describe(g->backend);
    }
    snprintf(info->description,
             sizeof(info->description),
             "%s",
             description ? description : "");
    pthread_mutex_unlock(&g_llrawproc_gpu_recon_backend_mutex);
    return 1;
}

static const char * llrawproc_gpu_recon_backend_dll_path(int prefer_playback_dll)
{
    const char * dll_path = NULL;

    if(prefer_playback_dll)
    {
        dll_path = getenv("MLVAPP_GPU_PLAYBACK_RECON_DLL");
        if(!dll_path || !*dll_path) dll_path = getenv("MLVAPP_GPU_RECON_DLL");
        if(!dll_path || !*dll_path) dll_path = getenv("MLVAPP_GPU_EXPORT_DLL");
    }
    else
    {
        dll_path = getenv("MLVAPP_GPU_EXPORT_DLL");
        if(!dll_path || !*dll_path) dll_path = getenv("MLVAPP_GPU_RECON_DLL");
    }

    return (dll_path && *dll_path) ? dll_path : "igpu_recon_cuda.dll";
}

static void llrawproc_gpu_export_backend_release(llrawprocGpuExportBackend_t * g)
{
    if(g->backend && g->destroy)
    {
        g->destroy(g->backend);
    }
    if(g->dll)
    {
        FreeLibrary(g->dll);
    }
    memset(g, 0, sizeof(*g));
}

static void llrawproc_gpu_export_backend_mark_unavailable(llrawprocGpuExportBackend_t * g,
                                                          const char * dll_path)
{
    llrawproc_gpu_export_backend_release(g);
    g->attempted = 1;
    g->unavailable = 1;
    snprintf(g->dll_path, sizeof(g->dll_path), "%s", dll_path ? dll_path : "");
}

/* Test-only hook: clear the sticky LoadLibrary result so a focused test can
 * exercise missing-DLL fallback without poisoning later in-process cases. */
int llrpResetGpuExportBackendForTesting(void);
int llrpResetGpuExportBackendForTesting(void)
{
    llrawproc_gpu_export_backend_release(&g_llrawproc_gpu_export_backend);
    llrawproc_gpu_export_reset_last_run_state();
    return 1;
}

int llrpGpuExportBackendAttemptedForTesting(void);
int llrpGpuExportBackendAttemptedForTesting(void)
{
    return g_llrawproc_gpu_export_backend.attempted;
}

int llrpGpuExportBackendUnavailableForTesting(void);
int llrpGpuExportBackendUnavailableForTesting(void)
{
    return g_llrawproc_gpu_export_backend.unavailable;
}

static FARPROC llrawproc_gpu_export_resolve(HMODULE dll, const char * name)
{
    FARPROC proc = GetProcAddress(dll, name);
    if(!proc)
    {
#ifndef STDOUT_SILENT
        printf("MLVAPP_GPU_EXPORT: missing backend symbol %s\n", name);
#endif
    }
    return proc;
}

static int llrawproc_gpu_export_backend_available(int prefer_playback_dll)
{
    llrawprocGpuExportBackend_t * g = &g_llrawproc_gpu_export_backend;
    const char * dll_path = llrawproc_gpu_recon_backend_dll_path(prefer_playback_dll);

    if(g->backend)
    {
        if(strcmp(g->dll_path, dll_path) == 0) return 1;
        llrawproc_gpu_export_backend_release(g);
    }
    if(g->unavailable)
    {
        if(strcmp(g->dll_path, dll_path) == 0) return 0;
        llrawproc_gpu_export_backend_release(g);
    }

    g->attempted = 1;
    snprintf(g->dll_path, sizeof(g->dll_path), "%s", dll_path);

    g->dll = LoadLibraryA(dll_path);
    if(!g->dll)
    {
        llrawproc_gpu_export_backend_mark_unavailable(g, dll_path);
        return 0;
    }
    if(!GetModuleFileNameA(g->dll, g->dll_resolved_path, sizeof(g->dll_resolved_path)))
    {
        snprintf(g->dll_resolved_path,
                 sizeof(g->dll_resolved_path),
                 "%s",
                 dll_path);
    }

#define LLRAWPROC_GPU_RESOLVE_TYPED(member, type, symbol) \
    do { \
        union { FARPROC raw; type typed; } resolved; \
        resolved.raw = llrawproc_gpu_export_resolve(g->dll, symbol); \
        g->member = resolved.typed; \
    } while(0)

    LLRAWPROC_GPU_RESOLVE_TYPED(create, llrawproc_gpu_create_fn, "igpu_recon_create");
    LLRAWPROC_GPU_RESOLVE_TYPED(destroy, llrawproc_gpu_destroy_fn, "igpu_recon_destroy");
    LLRAWPROC_GPU_RESOLVE_TYPED(abi_version, llrawproc_gpu_abi_version_fn, "igpu_recon_abi_version");
    LLRAWPROC_GPU_RESOLVE_TYPED(describe, llrawproc_gpu_describe_fn, "igpu_recon_describe");
    LLRAWPROC_GPU_RESOLVE_TYPED(set_clip, llrawproc_gpu_set_clip_fn, "igpu_recon_set_clip");
    LLRAWPROC_GPU_RESOLVE_TYPED(set_luts, llrawproc_gpu_set_luts_fn, "igpu_recon_set_luts");
    LLRAWPROC_GPU_RESOLVE_TYPED(run, llrawproc_gpu_run_fn, "igpu_recon_run");
    LLRAWPROC_GPU_RESOLVE_TYPED(last_timing, llrawproc_gpu_last_timing_fn, "igpu_recon_last_timing");
    {
        union { FARPROC raw; llrawproc_gpu_allocated_bytes_fn typed; } resolved;
        resolved.raw = GetProcAddress(g->dll, "igpu_recon_allocated_bytes");
        g->allocated_bytes = resolved.typed;
    }

#undef LLRAWPROC_GPU_RESOLVE_TYPED

    if(!g->create || !g->destroy || !g->abi_version || !g->describe ||
       !g->set_clip || !g->set_luts || !g->run || !g->last_timing)
    {
        llrawproc_gpu_export_backend_mark_unavailable(g, dll_path);
        return 0;
    }

    g->backend = g->create("cuda");
    if(!g->backend || g->abi_version(g->backend) != IGPU_RECON_ABI_VERSION)
    {
        if(g->backend)
        {
            g->destroy(g->backend);
        }
        g->backend = NULL;
        llrawproc_gpu_export_backend_mark_unavailable(g, dll_path);
        return 0;
    }

    return 1;
}

static int llrawproc_gpu_recon_backend_available_guarded(int prefer_playback_dll)
{
    int available = 0;
    pthread_mutex_lock(&g_llrawproc_gpu_recon_backend_mutex);
    available = llrawproc_gpu_export_backend_available(prefer_playback_dll);
    pthread_mutex_unlock(&g_llrawproc_gpu_recon_backend_mutex);
    return available;
}

static int llrawproc_gpu_recon_run_backend(const dualiso_gpu_recon_state_t * state,
                                           const uint16_t * gpu_input,
                                           uint16_t * gpu_output,
                                           unsigned int gl_texture_id,
                                           igpu_recon_out_kind out_kind,
                                           size_t raw_image_size,
                                           int prefer_playback_dll,
                                           int * rc_out,
                                           uint64_t * allocated_bytes_out,
                                           int * allocated_bytes_valid_out,
                                           llrpGpuPlaybackReconTiming_t * timing_out)
{
    llrawprocGpuExportBackend_t * g = &g_llrawproc_gpu_export_backend;
    igpu_recon_clip_t clip;
    igpu_recon_luts_t luts;
    igpu_recon_frame_t frame;
    int rc = 0;
    const size_t pixel_count = raw_image_size / sizeof(uint16_t);

    if(rc_out) *rc_out = -1;
    if(allocated_bytes_out) *allocated_bytes_out = 0;
    if(allocated_bytes_valid_out) *allocated_bytes_valid_out = 0;
    if(timing_out) memset(timing_out, 0, sizeof(*timing_out));
    if(!state || !state->valid || !gpu_input || raw_image_size == 0) return 0;
    if(out_kind == IGPU_OUT_CPU16 && !gpu_output) return 0;
    if(out_kind == IGPU_OUT_GL_TEXTURE && gl_texture_id == 0) return 0;
    if(pixel_count != (size_t)state->width * (size_t)state->height) return 0;

    memset(&clip, 0, sizeof(clip));
    clip.width = state->width;
    clip.height = state->height;
    clip.black_level = state->black_level;
    clip.white_level = state->white_level;
    memcpy(clip.is_bright, state->is_bright, sizeof(clip.is_bright));

    memset(&luts, 0, sizeof(luts));
    luts.raw2ev = state->raw2ev;
    luts.ev2raw = state->ev2raw;
    luts.mix_curve = state->mix_curve;
    luts.fullres_curve = state->fullres_curve;
    luts.randn05 = state->randn05;

    memset(&frame, 0, sizeof(frame));
    frame.ev_correction = state->ev_correction;
    frame.black_delta = state->black_delta;
    frame.white_darkened = state->white_darkened;
    frame.dark_noise = state->dark_noise;
    frame.interp_method = state->interp_method;
    frame.use_alias_map = state->use_alias_map;
    frame.use_fullres = state->use_fullres;
    frame.chroma_smooth_method = state->chroma_smooth_method;
    frame.apply_dither = state->apply_dither;

    pthread_mutex_lock(&g_llrawproc_gpu_recon_backend_mutex);
    if(!llrawproc_gpu_export_backend_available(prefer_playback_dll))
    {
        pthread_mutex_unlock(&g_llrawproc_gpu_recon_backend_mutex);
        return 0;
    }
    rc = g->set_clip(g->backend, &clip);
    if(rc == 0) rc = g->set_luts(g->backend, &luts);
    if(rc == 0) rc = g->run(g->backend, &frame, gpu_input, out_kind, gpu_output, gl_texture_id);
    if(g->allocated_bytes && allocated_bytes_out && allocated_bytes_valid_out)
    {
        uint64_t allocated_bytes = 0;
        if(g->allocated_bytes(g->backend, &allocated_bytes) == 0)
        {
            *allocated_bytes_out = allocated_bytes;
            *allocated_bytes_valid_out = 1;
        }
    }
    if(g->last_timing && timing_out)
    {
        igpu_recon_timing_t timing;
        memset(&timing, 0, sizeof(timing));
        if(g->last_timing(g->backend, &timing) == 0)
        {
            timing_out->available = 1;
            timing_out->upload_ms = timing.upload_ms;
            timing_out->kernel_ms = timing.kernel_ms;
            timing_out->interop_ms = timing.download_ms;
            timing_out->total_ms = timing.total_ms;
        }
    }
    pthread_mutex_unlock(&g_llrawproc_gpu_recon_backend_mutex);

    if(rc_out) *rc_out = rc;
    return rc == 0;
}

static int llrawproc_gpu_recon_run_cpu16(const dualiso_gpu_recon_state_t * state,
                                         const uint16_t * gpu_input,
                                         uint16_t * gpu_output,
                                         size_t raw_image_size,
                                         int prefer_playback_dll,
                                         int * rc_out,
                                         uint64_t * allocated_bytes_out,
                                         int * allocated_bytes_valid_out)
{
    return llrawproc_gpu_recon_run_backend(state,
                                          gpu_input,
                                          gpu_output,
                                          0,
                                          IGPU_OUT_CPU16,
                                          raw_image_size,
                                          prefer_playback_dll,
                                          rc_out,
                                          allocated_bytes_out,
                                          allocated_bytes_valid_out,
                                          NULL);
}

static int llrawproc_gpu_export_try_replace(uint16_t * cpu_output,
                                            const uint16_t * gpu_input,
                                            size_t raw_image_size)
{
    dualiso_gpu_recon_state_t state;
    uint16_t * gpu_output = NULL;
    int rc = 0;
    uint64_t allocated_bytes = 0;
    int allocated_bytes_valid = 0;
    const size_t pixel_count = raw_image_size / sizeof(uint16_t);

    llrawproc_gpu_export_reset_last_run_state();
    if(!cpu_output || !gpu_input || !llrawproc_gpu_export_enabled()) return 0;
    if(!dualiso_debug_get_last_gpu_recon_state(&state) || !state.valid) return 0;
    if(pixel_count != (size_t)state.width * (size_t)state.height) return 0;
    g_llrawproc_gpu_export_last_apply_dither = state.apply_dither;

    gpu_output = (uint16_t *)malloc(raw_image_size);
    if(!gpu_output) return 0;

    g_llrawproc_gpu_export_last_run_attempted = 1;
    (void)llrawproc_gpu_recon_run_cpu16(&state,
                                        gpu_input,
                                        gpu_output,
                                        raw_image_size,
                                        0,
                                        &rc,
                                        &allocated_bytes,
                                        &allocated_bytes_valid);
    g_llrawproc_gpu_export_last_run_rc = rc;
    if(allocated_bytes_valid)
    {
        g_llrawproc_gpu_export_last_allocated_bytes_valid = 1;
        g_llrawproc_gpu_export_last_allocated_bytes = allocated_bytes;
    }
    if(rc == 0 && memcmp(cpu_output, gpu_output, raw_image_size) == 0)
    {
        g_llrawproc_gpu_export_last_replaced = 1;
        memcpy(cpu_output, gpu_output, raw_image_size);
        free(gpu_output);
        return 1;
    }
    if(rc == 0)
    {
        unsigned long long mismatch_count = 0;
        for(size_t i = 0; i < pixel_count; ++i)
        {
            int cpu = (int)cpu_output[i];
            int gpu = (int)gpu_output[i];
            int diff = cpu - gpu;
            if(diff)
            {
                int abs_diff = diff < 0 ? -diff : diff;
                if(mismatch_count == 0)
                {
                    g_llrawproc_gpu_export_last_mismatch_first_index = (unsigned long long)i;
                    g_llrawproc_gpu_export_last_mismatch_first_cpu = cpu;
                    g_llrawproc_gpu_export_last_mismatch_first_gpu = gpu;
                }
                if(abs_diff > g_llrawproc_gpu_export_last_mismatch_max_abs)
                {
                    g_llrawproc_gpu_export_last_mismatch_max_abs = abs_diff;
                }
                ++mismatch_count;
            }
        }
        g_llrawproc_gpu_export_last_mismatch_count = mismatch_count;
        g_llrawproc_gpu_export_last_mismatch = 1;
    }

    free(gpu_output);
    return 0;
}

static int llrawproc_gpu_playback_try_reconstruct(const dualiso_gpu_recon_state_t * state,
                                                  const uint16_t * gpu_input,
                                                  uint16_t * gpu_output,
                                                  size_t raw_image_size)
{
    uint16_t * gpu_recon_output = NULL;
    int rc = -1;

    llrawproc_gpu_playback_reset_last_run_state();
    g_llrawproc_gpu_playback_last_state_valid =
        (state && state->valid) ? 1 : 0;
    if(state && state->valid)
    {
        g_llrawproc_gpu_playback_last_prepared_state = *state;
    }
    if(!state || !state->valid || !gpu_input || !gpu_output || raw_image_size == 0)
    {
        return 0;
    }

    gpu_recon_output = (uint16_t *)malloc(raw_image_size);
    if(!gpu_recon_output)
    {
        g_llrawproc_gpu_playback_last_run_rc = -2;
        return 0;
    }

    g_llrawproc_gpu_playback_last_run_attempted = 1;
    if(llrawproc_gpu_recon_run_cpu16(state,
                                     gpu_input,
                                     gpu_recon_output,
                                     raw_image_size,
                                     1,
                                     &rc,
                                     NULL,
                                     NULL))
    {
        memcpy(gpu_output, gpu_recon_output, raw_image_size);
        free(gpu_recon_output);
        g_llrawproc_gpu_playback_last_run_rc = rc;
        g_llrawproc_gpu_playback_last_used = 1;
        return 1;
    }

    free(gpu_recon_output);
    g_llrawproc_gpu_playback_last_run_rc = rc;
    return 0;
}

int llrpGpuPlaybackReconRunGlTexture(const llrpGpuPlaybackReconState_t * state,
                                     const uint16_t * raw_input_bayer14,
                                     size_t raw_image_size,
                                     unsigned int gl_texture_id,
                                     int * rc_out,
                                     llrpGpuPlaybackReconTiming_t * timing_out);
int llrpGpuPlaybackReconRunGlTexture(const llrpGpuPlaybackReconState_t * state,
                                     const uint16_t * raw_input_bayer14,
                                     size_t raw_image_size,
                                     unsigned int gl_texture_id,
                                     int * rc_out,
                                     llrpGpuPlaybackReconTiming_t * timing_out)
{
    dualiso_gpu_recon_state_t private_state;
    if(rc_out) *rc_out = -1;
    if(timing_out) memset(timing_out, 0, sizeof(*timing_out));
    if(!llrawproc_gpu_playback_dualiso_state_from_public(state, &private_state))
    {
        return 0;
    }
    return llrawproc_gpu_recon_run_backend(&private_state,
                                          raw_input_bayer14,
                                          NULL,
                                          gl_texture_id,
                                          IGPU_OUT_GL_TEXTURE,
                                          raw_image_size,
                                          1,
                                          rc_out,
                                          NULL,
                                          NULL,
                                          timing_out);
}

int llrpGpuPlaybackReconRunCpu16Probe(const llrpGpuPlaybackReconState_t * state,
                                      const uint16_t * raw_input_bayer14,
                                      size_t raw_image_size,
                                      uint16_t * output_bayer16,
                                      int * rc_out,
                                      llrpGpuPlaybackReconTiming_t * timing_out);
int llrpGpuPlaybackReconRunCpu16Probe(const llrpGpuPlaybackReconState_t * state,
                                      const uint16_t * raw_input_bayer14,
                                      size_t raw_image_size,
                                      uint16_t * output_bayer16,
                                      int * rc_out,
                                      llrpGpuPlaybackReconTiming_t * timing_out)
{
    dualiso_gpu_recon_state_t private_state;
    if(rc_out) *rc_out = -1;
    if(timing_out) memset(timing_out, 0, sizeof(*timing_out));
    if(!output_bayer16) return 0;
    if(!llrawproc_gpu_playback_dualiso_state_from_public(state, &private_state))
    {
        return 0;
    }
    return llrawproc_gpu_recon_run_backend(&private_state,
                                          raw_input_bayer14,
                                          output_bayer16,
                                          0,
                                          IGPU_OUT_CPU16,
                                          raw_image_size,
                                          1,
                                          rc_out,
                                          NULL,
                                          NULL,
                                          timing_out);
}
#else
static int llrawproc_gpu_export_backend_available(int prefer_playback_dll)
{
    (void)prefer_playback_dll;
    return 0;
}

static int llrawproc_gpu_recon_backend_available_guarded(int prefer_playback_dll)
{
    return llrawproc_gpu_export_backend_available(prefer_playback_dll);
}

int llrpResetGpuExportBackendForTesting(void);
int llrpResetGpuExportBackendForTesting(void)
{
    return 1;
}

int llrpGpuExportBackendAttemptedForTesting(void);
int llrpGpuExportBackendAttemptedForTesting(void)
{
    return 0;
}

int llrpGpuExportBackendUnavailableForTesting(void);
int llrpGpuExportBackendUnavailableForTesting(void)
{
    return 1;
}

static int llrawproc_gpu_export_try_replace(uint16_t * cpu_output,
                                            const uint16_t * gpu_input,
                                            size_t raw_image_size)
{
    (void)cpu_output;
    (void)gpu_input;
    (void)raw_image_size;
    return 0;
}

static int llrawproc_gpu_playback_try_reconstruct(const dualiso_gpu_recon_state_t * state,
                                                  const uint16_t * gpu_input,
                                                  uint16_t * gpu_output,
                                                  size_t raw_image_size)
{
    (void)state;
    (void)gpu_input;
    (void)gpu_output;
    (void)raw_image_size;
    llrawproc_gpu_playback_reset_last_run_state();
    g_llrawproc_gpu_playback_last_run_attempted = 1;
    g_llrawproc_gpu_playback_last_run_rc = -1;
    return 0;
}

int llrpGpuPlaybackReconRunGlTexture(const llrpGpuPlaybackReconState_t * state,
                                     const uint16_t * raw_input_bayer14,
                                     size_t raw_image_size,
                                     unsigned int gl_texture_id,
                                     int * rc_out,
                                     llrpGpuPlaybackReconTiming_t * timing_out);
int llrpGpuPlaybackReconRunGlTexture(const llrpGpuPlaybackReconState_t * state,
                                     const uint16_t * raw_input_bayer14,
                                     size_t raw_image_size,
                                     unsigned int gl_texture_id,
                                     int * rc_out,
                                     llrpGpuPlaybackReconTiming_t * timing_out)
{
    (void)state;
    (void)raw_input_bayer14;
    (void)raw_image_size;
    (void)gl_texture_id;
    if(rc_out) *rc_out = -1;
    if(timing_out) memset(timing_out, 0, sizeof(*timing_out));
    return 0;
}

int llrpGpuPlaybackReconRunCpu16Probe(const llrpGpuPlaybackReconState_t * state,
                                      const uint16_t * raw_input_bayer14,
                                      size_t raw_image_size,
                                      uint16_t * output_bayer16,
                                      int * rc_out,
                                      llrpGpuPlaybackReconTiming_t * timing_out);
int llrpGpuPlaybackReconRunCpu16Probe(const llrpGpuPlaybackReconState_t * state,
                                      const uint16_t * raw_input_bayer14,
                                      size_t raw_image_size,
                                      uint16_t * output_bayer16,
                                      int * rc_out,
                                      llrpGpuPlaybackReconTiming_t * timing_out)
{
    (void)state;
    (void)raw_input_bayer14;
    (void)raw_image_size;
    (void)output_bayer16;
    if(rc_out) *rc_out = -1;
    if(timing_out) memset(timing_out, 0, sizeof(*timing_out));
    return 0;
}

int llrpGpuPlaybackReconGetBackendInfo(llrpGpuPlaybackReconBackendInfo_t * info);
int llrpGpuPlaybackReconGetBackendInfo(llrpGpuPlaybackReconBackendInfo_t * info)
{
    if(!info) return 0;
    memset(info, 0, sizeof(*info));
    info->unavailable = 1;
    return 1;
}
#endif

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

void llrpInitWorkerState(llrawprocWorkerState_t * worker)
{
    if (!worker) return;

    memset(worker, 0, sizeof(*worker));
    worker->prev_black_level = -1;
}

void llrpFreeWorkerState(llrawprocWorkerState_t * worker)
{
    if (!worker) return;

    llrawproc_free_worker_state(worker);
    llrpInitWorkerState(worker);
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
    llrawproc->diso_playback_force_disable_alias_map = 0;
    llrawproc->diso_playback_force_disable_fr_blending = 0;
    llrawproc->playback_pre_dualiso_fix_ms = 0.0;
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
void applyLLRawProcObjectWorker(mlvObject_t * video,
                                uint16_t * raw_image_buff,
                                size_t raw_image_size,
                                llrawprocWorkerState_t * supplied_worker,
                                int stop_before_dual_iso)
{
    const double apply_start = mlv_stage_timing_now();
    llrawprocObject_t * shared = video ? video->llrawproc : NULL;
    llrawprocWorkerState_t stack_worker;
    llrawprocWorkerState_t * worker = supplied_worker;
    int using_stack_worker = 0;

    llrawproc_gpu_export_reset_last_run_state();
    g_llrawproc_last_shared_lock_ms = 0.0;
    g_llrawproc_last_dualiso_refine_lock_ms = 0.0;
    g_llrawproc_last_publish_lock_ms = 0.0;
    g_llrawproc_last_total_ms = 0.0;
    g_llrawproc_last_dark_frame_ms = 0.0;
    g_llrawproc_last_vertical_stripes_ms = 0.0;
    g_llrawproc_last_focus_pixels_ms = 0.0;
    g_llrawproc_last_bad_pixels_ms = 0.0;
    g_llrawproc_last_pattern_noise_ms = 0.0;
    g_llrawproc_last_pre_dualiso_fix_ms = 0.0;
    g_llrawproc_last_dual_iso_ms = 0.0;
    g_llrawproc_last_chroma_smooth_ms = 0.0;
    llrawproc_reset_dual_iso_full20bit_timing();
    g_llrawproc_last_preview_histogram_ms = 0.0;
    g_llrawproc_last_preview_regression_ms = 0.0;
    g_llrawproc_last_preview_rowscale_ms = 0.0;
    llrawproc_gpu_playback_reset_last_run_state();
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

    if (worker && !worker->raw2ev && !worker->ev2raw && worker->prev_black_level == 0)
    {
        worker->prev_black_level = -1;
    }
    if (!worker)
    {
        memset(&stack_worker, 0, sizeof(stack_worker));
        stack_worker.prev_black_level = -1;
        worker = llrawproc_acquire_worker_state(video);
        if (!worker)
        {
            worker = &stack_worker;
            using_stack_worker = 1;
        }
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
    /* Phase E5 playback-only fast-path overrides: at scale >= 4 the GUI
     * policy disables alias_map suppression and full-res blending because
     * the 4x4 downsample is itself an anti-aliasing operation and the
     * FR-blending stage on a 1/16 pixel-count buffer mixes same-resolution
     * data with itself. Both stages cost ~8-15 ms/frame combined on 5K
     * dual-ISO clips. Receipt-authored values are not modified, so
     * paused/scrubbing/export keep diso_alias_map / diso_frblending
     * untouched. Cache invalidation: the override fields are hashed by
     * mlv_hash_llrawproc_state, so playback-active and paused produce
     * different cache slot signatures for the same frame index. */
    if (!dualiso_playback_alias_map_downgrade_disabled_via_env())
    {
        if (shared->diso_playback_force_disable_alias_map != 0)
        {
            diso_alias_map = 0;
        }
        if (shared->diso_playback_force_disable_fr_blending != 0)
        {
            diso_frblending = 0;
        }
    }
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

    if (stop_before_dual_iso)
    {
        g_llrawproc_last_pre_dualiso_fix_ms = (mlv_stage_timing_now() - apply_start) * 1000.0;
        g_llrawproc_last_total_ms = g_llrawproc_last_pre_dualiso_fix_ms;
        if (shared)
        {
            shared->playback_pre_dualiso_fix_ms = g_llrawproc_last_pre_dualiso_fix_ms;
        }
        if (using_stack_worker) llrawproc_free_worker_state(worker);
        return;
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
            uint16_t * gpu_export_input = NULL;
            uint16_t * gpu_playback_input = NULL;
            int gpu_playback_recon_used = 0;
            int dual_iso_recon_ok = 0;
            int capture_gpu_recon_state = 0;
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

            if (llrawproc_gpu_export_enabled()
             && raw_image_size > 0
             && llrawproc_gpu_recon_backend_available_guarded(0))
            {
                gpu_export_input = (uint16_t *)malloc(raw_image_size);
                if (gpu_export_input)
                {
                    memcpy(gpu_export_input, raw_image_buff, raw_image_size);
                }
            }
            if (llrawproc_gpu_playback_recon_enabled()
             && raw_image_size > 0)
            {
                llrawproc_gpu_playback_reset_last_run_state();
                gpu_playback_input = (uint16_t *)malloc(raw_image_size);
                if (gpu_playback_input)
                {
                    memcpy(gpu_playback_input, raw_image_buff, raw_image_size);
                }
            }

            if (gpu_playback_input)
            {
                dualiso_gpu_recon_state_t gpu_playback_state;
                memset(&gpu_playback_state, 0, sizeof(gpu_playback_state));
                /* Prepare may resolve Dual ISO pattern/match fields; CPU fallback
                 * intentionally reuses those idempotent resolved values below. */
                if (diso_prepare_gpu_recon_state(raw_info,
                                                 gpu_playback_input,
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
                                                 &worker->diso_full20bit_scratch,
                                                 &gpu_playback_state))
                {
                    g_llrawproc_gpu_playback_last_state_valid =
                        gpu_playback_state.valid ? 1 : 0;
                    g_llrawproc_gpu_playback_last_prepared_state =
                        gpu_playback_state;
                }
                if (gpu_playback_state.valid
                 && !g_llrawproc_gpu_playback_texture_present_preferred
                 && llrawproc_gpu_playback_try_reconstruct(&gpu_playback_state,
                                                           gpu_playback_input,
                                                           raw_image_buff,
                                                           raw_image_size))
                {
                    dual_iso_recon_ok = 1;
                    gpu_playback_recon_used = 1;
                }
            }

            if (!gpu_playback_recon_used)
            {
                capture_gpu_recon_state =
                    (gpu_export_input != NULL)
                    || (gpu_playback_input != NULL
                     && g_llrawproc_gpu_playback_texture_present_preferred);
                dualiso_debug_set_gpu_recon_state_capture_enabled(capture_gpu_recon_state);
                dual_iso_recon_ok =
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
            }
            if (!gpu_playback_recon_used
             && dual_iso_recon_ok
             && gpu_playback_input
             && g_llrawproc_gpu_playback_texture_present_preferred
             && capture_gpu_recon_state)
            {
                dualiso_gpu_recon_state_t cpu_oracle_state;
                memset(&cpu_oracle_state, 0, sizeof(cpu_oracle_state));
                if (dualiso_debug_get_last_gpu_recon_state(&cpu_oracle_state)
                 && cpu_oracle_state.valid)
                {
                    g_llrawproc_gpu_playback_last_state_valid = 1;
                    g_llrawproc_gpu_playback_last_prepared_state = cpu_oracle_state;
                }
            }
            if (gpu_export_input)
            {
                if (!gpu_playback_recon_used && dual_iso_recon_ok)
                {
                    llrawproc_gpu_export_try_replace(raw_image_buff,
                                                     gpu_export_input,
                                                     raw_image_size);
                }
                free(gpu_export_input);
            }
            if (gpu_playback_input)
            {
                free(gpu_playback_input);
            }
            dualiso_debug_set_gpu_recon_state_capture_enabled(0);
            dualiso_debug_get_full20bit_timing(
                &g_llrawproc_last_dual_iso_full20bit_timing);
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

            const double refine_lock_start = mlv_stage_timing_now();
            int post_recon_luts_active = 0;
            focus_status_snapshot = 0;
            bad_status_snapshot = 0;
            focus_interpolate_outside_lock = 0;
            bad_interpolate_outside_lock = 0;
            bad_force_reset_after_interpolation = 0;
            focus_map_for_interpolation = NULL;
            bad_map_for_interpolation = NULL;
            if (bad_pixels)
            {
                llrawproc_worker_ensure_luts(worker, worker->dng_black_level);
                post_recon_luts_active = 1;
            }
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
                if (!post_recon_luts_active)
                {
                    llrawproc_worker_ensure_luts(worker, worker->dng_black_level);
                    post_recon_luts_active = 1;
                }
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
                if (!post_recon_luts_active)
                {
                    llrawproc_worker_ensure_luts(worker, worker->dng_black_level);
                    post_recon_luts_active = 1;
                }
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

            if (post_recon_luts_active)
            {
                llrawproc_worker_ensure_luts(worker, raw_info.black_level);
            }
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

void applyLLRawProcObject(mlvObject_t * video, uint16_t * raw_image_buff, size_t raw_image_size)
{
    applyLLRawProcObjectWorker(video, raw_image_buff, raw_image_size, NULL, 0);
}

void applyLLRawProcObjectPreDualIsoFixes(mlvObject_t * video,
                                         uint16_t * raw_image_buff,
                                         size_t raw_image_size)
{
    applyLLRawProcObjectWorker(video, raw_image_buff, raw_image_size, NULL, 1);
}

/* Phase 4B-v2: scaled-buffer entry point. Runs a SUBSET of the llrawproc
 * pipeline on a buffer whose dimensions differ from
 * video->RAWI.xRes/yRes. The subset includes only the size-agnostic
 * stages (HQ Dual ISO recon, dark frame subtraction, chroma smooth, 14-bit
 * conversion). Pre-downsample stages (focus pixel, bad pixel, vertical
 * stripes, pattern noise) are NOT applied here — the caller must apply
 * them at full res before downsampling, OR ensure they are disabled in
 * the receipt. Aggressive preview is the one deliberate exception: it may
 * skip those coordinate-sensitive stages to keep reduction ahead of Dual
 * ISO/debayer for coarse deep preview.
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
    llrawproc_reset_dual_iso_full20bit_timing();
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
     * resolution. Aggressive preview explicitly treats those stages as
     * skippable approximations; sharp/smooth playback falls back to the
     * conservative full-res path. */
    if (!mlvPlaybackAggressivePreviewMode())
    {
        if (shared->focus_pixels) return 0;
        if (shared->bad_pixels) return 0;
        if (shared->vertical_stripes) return 0;
        if (shared->pattern_noise) return 0;
    }

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
    /* Phase E5 scale-aware downgrade: see note in applyLLRawProcObject. */
    if (!dualiso_playback_alias_map_downgrade_disabled_via_env())
    {
        if (shared->diso_playback_force_disable_alias_map != 0)
        {
            diso_alias_map = 0;
        }
        if (shared->diso_playback_force_disable_fr_blending != 0)
        {
            diso_frblending = 0;
        }
    }
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
        dualiso_debug_get_full20bit_timing(
            &g_llrawproc_last_dual_iso_full20bit_timing);
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

double llrpGetLastPreDualIsoFixMilliseconds(void)
{
    return g_llrawproc_last_pre_dualiso_fix_ms;
}

double llrpGetLastDualIsoMilliseconds(void)
{
    return g_llrawproc_last_dual_iso_ms;
}

double llrpGetLastChromaSmoothMilliseconds(void)
{
    return g_llrawproc_last_chroma_smooth_ms;
}

void llrpGetLastDualIsoFull20bitTiming(dualiso_full20bit_timing_t * timing)
{
    if (!timing) return;
    *timing = g_llrawproc_last_dual_iso_full20bit_timing;
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

int llrpGetDualIsoPlaybackForceDisableAliasMap(mlvObject_t * video)
{
    return video->llrawproc->diso_playback_force_disable_alias_map;
}

void llrpSetDualIsoPlaybackForceDisableAliasMap(mlvObject_t * video, int value)
{
    video->llrawproc->diso_playback_force_disable_alias_map = value ? 1 : 0;
}

int llrpGetDualIsoPlaybackForceDisableFrBlending(mlvObject_t * video)
{
    return video->llrawproc->diso_playback_force_disable_fr_blending;
}

void llrpSetDualIsoPlaybackForceDisableFrBlending(mlvObject_t * video, int value)
{
    video->llrawproc->diso_playback_force_disable_fr_blending = value ? 1 : 0;
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

/* Seed shared->diso_pattern from a FULL-RESOLUTION raw frame when it is still
 * unseeded (0). The scaled (reduced-resolution) playback recon detects the
 * dual-ISO bright/dark row pattern from the data it is handed; on 8x/4x
 * downsampled data that detection is unreliable (the row distinction is
 * averaged away), so on the cold pass — before any full-res render has seeded
 * the pattern, e.g. the processed8 prefetch worker reconstructing scaled frames
 * at play start — it mis-detects and emits the green-dropped "x8 cold pink".
 * Detecting once from the full-res raw (row pattern intact) and publishing the
 * SAME negative -1..-4 encoding that diso_get_full20bit / diso_get_preview both
 * consume gives the scaled recon a correct seed. diso_check=1 is detect-only and
 * READ-ONLY on raw_full. No-op when not dual-ISO, already seeded, or detection
 * fails — so it can only improve, never regress (and never returns failure to a
 * caller, unlike the reverted defer guard). */
void llrpEnsureDualIsoPatternSeeded(mlvObject_t * video, uint16_t * raw_full, int full_w, int full_h)
{
    if (!video || !video->llrawproc || !raw_full) return;
    llrawprocObject_t * shared = video->llrawproc;
    if (!shared->fix_raw || !shared->diso_validity || shared->dual_iso != 1) return;
    if (shared->diso_pattern != 0) return;
    if (full_w <= 0 || full_h <= 0 || full_w > 65535 || full_h > 65535) return;

    int detected = 0;
    if (diso_get_preview(raw_full,
                         (uint16_t)full_w,
                         (uint16_t)full_h,
                         (int32_t)video->RAWI.raw_info.black_level,
                         (int32_t)video->RAWI.raw_info.white_level,
                         &detected,
                         1 /* diso_check: detect-only, read-only on raw_full */,
                         NULL)
        && detected != 0)
    {
        pthread_mutex_lock(&video->llrawproc_mutex);
        if (shared->diso_pattern == 0)
        {
            shared->diso_pattern = detected;
        }
        pthread_mutex_unlock(&video->llrawproc_mutex);

        static int s_log_diso_seed = -1;
        if (s_log_diso_seed < 0)
        {
            const char * v = getenv("MLVAPP_LOG_DISO_SEED");
            s_log_diso_seed = (v && v[0] && v[0] != '0') ? 1 : 0;
        }
        if (s_log_diso_seed)
        {
            fprintf(stderr, "[DISO_SEED] seeded diso_pattern=%d from full-res %dx%d\n",
                    detected, full_w, full_h);
        }
    }
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
