/*
 * Copyright (C) 2017 bouncyball
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

#ifndef _llrawproc_h
#define _llrawproc_h

#include "llrawproc_object.h"
#include "../mlv_object.h"

#ifdef __cplusplus
extern "C" {
#endif

llrawprocObject_t * initLLRawProcObject();
void freeLLRawProcObject(mlvObject_t * video);

/* all low level raw processing takes place here */
void applyLLRawProcObject(mlvObject_t * video, uint16_t * raw_image_buff, size_t raw_image_size);
void llrpInitWorkerState(llrawprocWorkerState_t * worker);
void llrpFreeWorkerState(llrawprocWorkerState_t * worker);
void applyLLRawProcObjectWorker(mlvObject_t * video,
                                uint16_t * raw_image_buff,
                                size_t raw_image_size,
                                llrawprocWorkerState_t * worker);

/* Phase 4B-v2: scaled variant for the downsample-BEFORE-llrawproc path.
 * Runs the same pipeline as applyLLRawProcObject but on a buffer whose
 * dimensions differ from video->RAWI.xRes / yRes. The override_w and
 * override_h MUST match the actual buffer dimensions (rows*cols*sizeof
 * uint16_t == raw_image_size). The 4-row dual-ISO pattern must be
 * preserved by the caller — the recon code reads is_bright[y%4] and the
 * caller's downsample kernel must guarantee row 0,1,2,3 of the input
 * carry the same brightness pattern as the corresponding source rows.
 *
 * Subset of operations applied:
 *  - dark frame subtraction (size-agnostic)
 *  - HQ Dual ISO recon (operates on raw_info.width/height)
 *  - chroma smooth (size-agnostic)
 *  - 14-bit lift / undo
 * NOT applied (pre-downsample at full res only when enabled):
 *  - focus pixel interpolation (uses absolute sensor coords)
 *  - bad pixel interpolation (same)
 *  - vertical stripes (uses absolute column index)
 *  - pattern noise fix
 *
 * Returns 1 if the scaled application is safe, 0 if not (in which case
 * the caller must fall back to the v1 full-res llrawproc + post-
 * downsample path). */
int applyLLRawProcObject_with_dims(mlvObject_t * video,
                                   uint16_t * raw_image_buff,
                                   size_t raw_image_size,
                                   int override_w,
                                   int override_h);
double llrpGetLastSharedLockMilliseconds(void);
double llrpGetLastDualIsoRefineLockMilliseconds(void);
double llrpGetLastPublishLockMilliseconds(void);
double llrpGetLastTotalMilliseconds(void);
double llrpGetLastDarkFrameMilliseconds(void);
double llrpGetLastVerticalStripesMilliseconds(void);
double llrpGetLastFocusPixelsMilliseconds(void);
double llrpGetLastBadPixelsMilliseconds(void);
double llrpGetLastPatternNoiseMilliseconds(void);
double llrpGetLastDualIsoMilliseconds(void);
double llrpGetLastChromaSmoothMilliseconds(void);
double llrpGetLastDualIsoPreviewHistogramMilliseconds(void);
double llrpGetLastDualIsoPreviewRegressionMilliseconds(void);
double llrpGetLastDualIsoPreviewRowscaleMilliseconds(void);
void llrpResetDebugPixelMapCopyCount(void);
uint64_t llrpGetDebugPixelMapCopyCount(void);
void llrpResetDebugDarkFrameCopyCount(void);
uint64_t llrpGetDebugDarkFrameCopyCount(void);
void llrpResetDebugRuntimePublishCount(void);
uint64_t llrpGetDebugRuntimePublishCount(void);

/* Detect focus dot fix mode according to RAWC block info (binning + skipping) and camera ID
   Return value 0 = off, 1 = On, 2 = CropRec */
int llrpDetectFocusDotFixMode(mlvObject_t * video);

/* LLRawProcObject all member variable handling functions */
enum { FR_OFF, FR_ON };
int llrpGetFixRawMode(mlvObject_t * video);
void llrpSetFixRawMode(mlvObject_t * video, int value);

enum { VS_OFF, VS_ON, VS_FORCE };
int llrpGetVerticalStripeMode(mlvObject_t * video);
void llrpSetVerticalStripeMode(mlvObject_t * video, int value);
void llrpComputeStripesOn(mlvObject_t * video);

enum { FP_OFF, FP_ON, FP_CROPREC };
int llrpGetFocusPixelMode(mlvObject_t * video);
void llrpSetFocusPixelMode(mlvObject_t * video, int value);

enum { FPI_MLVFS, FPI_RAW2DNG };
int llrpGetFocusPixelInterpolationMethod(mlvObject_t * video);
void llrpSetFocusPixelInterpolationMethod(mlvObject_t * video, int value);

enum { BP_OFF, BP_ON, FP_AGGRESSIVE };
int llrpGetBadPixelMode(mlvObject_t * video);
void llrpSetBadPixelMode(mlvObject_t * video, int value);

enum { BPS_NORMAL, BPS_FORCE };
int llrpGetBadPixelSearchMethod(mlvObject_t * video);
void llrpSetBadPixelSearchMethod(mlvObject_t * video, int value);

enum { BPI_MLVFS, BPI_RAW2DNG };
int llrpGetBadPixelInterpolationMethod(mlvObject_t * video);
void llrpSetBadPixelInterpolationMethod(mlvObject_t * video, int value);

enum { CS_OFF, CS_2x2, CS_3x3, CS_5x5 };
int llrpGetChromaSmoothMode(mlvObject_t * video);
void llrpSetChromaSmoothMode(mlvObject_t * video, int value);

enum { PN_OFF, PN_ON };
int llrpGetPatternNoiseMode(mlvObject_t * video);
void llrpSetPatternNoiseMode(mlvObject_t * video, int value);

int llrpGetDeflickerTarget(mlvObject_t * video);
void llrpSetDeflickerTarget(mlvObject_t * video, int value);

/* dual iso stuff */
enum { DISO_OFF, DISO_20BIT, DISO_FAST };
int llrpGetDualIsoMode(mlvObject_t * video);
void llrpSetDualIsoMode(mlvObject_t * video, int value);

enum { DISOI_AMAZE, DISOI_MEAN23 };
int llrpGetDualIsoInterpolationMethod(mlvObject_t * video);
void llrpSetDualIsoInterpolationMethod(mlvObject_t * video, int value);

/* Playback-only override: when non-zero, applyLLRawProcObject forces the
 * HQ dual ISO recon (dual_iso == DISO_20BIT) onto the mean23 interpolation
 * regardless of what the receipt selected. The receipt's stored value is
 * not modified, so paused/scrubbing/export keep the authored choice (e.g.
 * AMaZE). Set/cleared by the GUI in MainWindow::applyEffectiveDualIsoPlaybackSettings
 * based on effectiveDualIsoPlaybackRuntimeSettings(). */
int llrpGetDualIsoPlaybackForceMean23(mlvObject_t * video);
void llrpSetDualIsoPlaybackForceMean23(mlvObject_t * video, int value);

int llrpGetDualIsoAliasMapMode(mlvObject_t * video);
void llrpSetDualIsoAliasMapMode(mlvObject_t * video, int value);

int llrpGetDualIsoFullResBlendingMode(mlvObject_t * video);
void llrpSetDualIsoFullResBlendingMode(mlvObject_t * video, int value);

/* Phase E5: playback-only overrides that force the HQ dual ISO recon to
 * skip alias_map suppression and full-res blending. The GUI sets these
 * non-zero when playback is active AND HQ recon would run AND the active
 * playback scale factor is >= 4 (see MainWindow::applyEffectiveDualIsoPlaybackSettings).
 * Receipt-authored values for diso_alias_map / diso_frblending are not
 * modified, so paused/scrubbing/export still apply the receipt's intended
 * quality. Diagnostic env var MLVAPP_PLAYBACK_KEEP_ALIAS_MAP_AT_SCALE=1
 * forces the override off (matches the precedent set by the rowscale and
 * mean23 escape hatches). */
int llrpGetDualIsoPlaybackForceDisableAliasMap(mlvObject_t * video);
void llrpSetDualIsoPlaybackForceDisableAliasMap(mlvObject_t * video, int value);
int llrpGetDualIsoPlaybackForceDisableFrBlending(mlvObject_t * video);
void llrpSetDualIsoPlaybackForceDisableFrBlending(mlvObject_t * video, int value);

enum { DISO_INVALID, DISO_FORCED, DISO_VALID }; // Return values
int llrpGetDualIsoValidity(mlvObject_t * video);
void llrpSetDualIsoValidity(mlvObject_t * video, int diso_force);

int llrpHQDualIso(mlvObject_t * video);

void llrpResetDngBWLevels(mlvObject_t * video);

/* reset focus/bad pixel map status */
void llrpResetFpmStatus(mlvObject_t * video);
void llrpResetBpmStatus(mlvObject_t * video);

/* dark frame stuff */
void llrpInitDarkFrameExtFileName(mlvObject_t * video, char * df_filename);
void llrpFreeDarkFrameExtFileName(mlvObject_t * video);

int llrpGetDarkFrameMode(mlvObject_t * video);
void llrpSetDarkFrameMode(mlvObject_t * video, int value);

int llrpGetDarkFrameExtStatus(mlvObject_t * video);
int llrpGetDarkFrameIntStatus(mlvObject_t * video);

int llrpValidateExtDarkFrame(mlvObject_t * video, char * df_filename, char * error_message);

#ifdef __cplusplus
}
#endif

#endif
