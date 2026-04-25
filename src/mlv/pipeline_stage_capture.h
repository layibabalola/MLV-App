/*!
 * \file pipeline_stage_capture.h
 * \brief Read-only diagnostic harness for capturing pipeline-stage buffers.
 *
 * Purpose: enable paused-vs-playing diff analysis by writing the buffer at
 * each pipeline boundary to disk (with a sidecar JSON describing layout and
 * code-path branches) when MLVAPP_PIPELINE_CAPTURE_DIR is set. When the env
 * var is unset (the default), every call short-circuits to a no-op with no
 * allocation, no I/O, and no metadata-string evaluation.
 *
 * Configuration via environment variables:
 *
 *   MLVAPP_PIPELINE_CAPTURE_DIR
 *       Output directory. Must exist and be writable. If unset, the harness
 *       is completely inert (single load + branch in mlv_pipeline_capture).
 *
 *   MLVAPP_PIPELINE_CAPTURE_LABEL
 *       Short label prefix for filenames (e.g. "paused", "playing"). The
 *       label segregates artifacts from multiple runs that share a single
 *       output directory. Defaults to "run". Only [A-Za-z0-9_-] characters
 *       are kept; the rest are replaced with '_' so the label is filename-
 *       safe.
 *
 *   MLVAPP_PIPELINE_CAPTURE_FRAMES
 *       Comma-separated frame indices to capture, e.g. "0,255,511,1023".
 *       The literal value "all" captures every frame. If unset, the harness
 *       captures only frame 0 by default. Whitespace around commas is
 *       tolerated.
 *
 * Output format (per capture call):
 *
 *   <dir>/<label>_<stage>_f<frame>.bin
 *       Raw bytes, exactly bytes_per_line * height long, no header.
 *
 *   <dir>/<label>_<stage>_f<frame>.json
 *       Sidecar with stage, frame, width, height, bytes_per_line,
 *       bytes_per_pixel, channels, bit_depth, format_label, plus all the
 *       code-path branch labels supplied in the metadata struct.
 *
 * Hook insertion points (all buffers contiguous in memory unless noted):
 *
 *   S0_raw_uint16     — uint16 mono/bayer, post LJ92 decode + unpack, pre llrawproc
 *   S1_pre_dualiso    — uint16 mono/bayer, post non-Dual-ISO llrawproc steps,
 *                       pre Dual ISO recon
 *   S2_post_dualiso   — uint16 mono/bayer, post Dual ISO recon (full HQ or
 *                       preview rowscale)
 *   S3_debayer        — uint16 RGB16 packed (3 ch, 16-bit), post debayer
 *   S4_processing     — uint16 RGB16 packed, post processing core (matrix +
 *                       levels + gamma + curves + output)
 *   S5_processed8     — uint8 RGB8 packed, post 16-to-8 conversion
 *   S6_displayImage   — uint8 RGB8 (potentially aligned-padded stride), final
 *                       QImage just before QPixmap::fromImage
 *
 * Threading: the capture function is threadsafe. A single mutex guards the
 * lazy-init of env-var-derived configuration and serialises disk writes so
 * concurrent worker threads don't interleave output. The fast (disabled)
 * path takes no locks.
 */

#ifndef MLV_PIPELINE_STAGE_CAPTURE_H
#define MLV_PIPELINE_STAGE_CAPTURE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ----------------------------------------------------------------------
 * Stage name constants. Use these in mlv_pipeline_capture() calls so a
 * misspelling shows up at compile time, not as mysteriously-missing
 * artifacts.
 * --------------------------------------------------------------------*/
#define MLV_PIPELINE_STAGE_S0_RAW_UINT16   "S0_raw_uint16"
#define MLV_PIPELINE_STAGE_S1_PRE_DUALISO  "S1_pre_dualiso"
#define MLV_PIPELINE_STAGE_S2_POST_DUALISO "S2_post_dualiso"
#define MLV_PIPELINE_STAGE_S3_DEBAYER      "S3_debayer"
#define MLV_PIPELINE_STAGE_S4_PROCESSING   "S4_processing"
#define MLV_PIPELINE_STAGE_S5_PROCESSED8   "S5_processed8"
#define MLV_PIPELINE_STAGE_S6_DISPLAYIMAGE "S6_displayImage"

/* Coarse format classifier. The free-form `format_label` field on the meta
 * struct is the source of truth for human inspection; this enum exists so
 * downstream tooling can dispatch on the broad shape without parsing
 * strings. */
typedef enum {
    MLV_PIPELINE_FORMAT_UNKNOWN = 0,
    MLV_PIPELINE_FORMAT_UINT16_MONO,   /* 1 channel, 16-bit, e.g. raw bayer */
    MLV_PIPELINE_FORMAT_UINT16_RGB,    /* 3 channels, 16-bit, packed RGB */
    MLV_PIPELINE_FORMAT_UINT8_RGB      /* 3 channels, 8-bit, packed RGB */
} mlv_pipeline_format_t;

/* Metadata describing the captured buffer. The capture function copies what
 * it needs into the sidecar JSON; caller-provided strings only need to live
 * for the duration of the call. Any string field may be NULL (written as
 * empty in the sidecar). */
typedef struct {
    const char * stage;            /* one of the MLV_PIPELINE_STAGE_* constants */
    const char * format_label;     /* free-form, e.g. "uint16_bayer_post_focus" */
    mlv_pipeline_format_t format;
    int width;
    int height;
    int bytes_per_line;            /* full row stride in bytes */
    int bytes_per_pixel;
    int channels;                  /* 1, 3, etc. */
    int bit_depth;                 /* 8, 16, etc. */
    /* Code-path branch labels; harness includes verbatim in the sidecar. */
    const char * dual_iso_mode;       /* "off", "preview", "full", "unknown" */
    const char * debayer_mode;        /* "amaze", "bilinear", "ahd", ... */
    int playback_policy_active;       /* 0 or 1 */
    int processing_subset_active;     /* 0 or 1 */
    const char * scaler;              /* "smooth", "fast", "none" */
    const char * path_label;          /* "paused", "playing", "scrub", ... */
    /* Compact 64-bit signature of the receipt + processing settings; same
     * value across paused/playing means the intended pixel output is the
     * same and any visible difference is purely a code-path artifact. */
    uint64_t settings_hash;
} mlv_pipeline_capture_meta_t;

/*!
 * Capture a buffer at a pipeline stage boundary.
 *
 * No-op (early return, zero overhead) unless MLVAPP_PIPELINE_CAPTURE_DIR is
 * set in the environment. Also no-op if frame_index is not in the configured
 * frame set (see MLVAPP_PIPELINE_CAPTURE_FRAMES).
 *
 * On success the function writes <label>_<stage>_f<frame>.bin and
 * <label>_<stage>_f<frame>.json to the configured directory. On any I/O
 * error the function logs to stderr but does not propagate; callers must
 * not depend on capture success for correctness.
 *
 * \param frame_index  zero-based frame index this buffer represents
 * \param buffer       pointer to bytes_per_line * height contiguous bytes;
 *                     ignored when the harness is disabled. Must be NULL-
 *                     checked by the caller only if the caller can't
 *                     guarantee non-NULL.
 * \param meta         describes the buffer; copied as needed before return
 */
void mlv_pipeline_capture(uint64_t frame_index,
                          const void * buffer,
                          const mlv_pipeline_capture_meta_t * meta);

/*!
 * Returns 1 if MLVAPP_PIPELINE_CAPTURE_DIR is set and the directory is
 * usable, 0 otherwise. Callers that need to pre-flatten a buffer (e.g.
 * float -> uint16) before capture can use this as a fast gate to skip the
 * prep entirely when the harness is off.
 */
int mlv_pipeline_capture_enabled(void);

/*!
 * Returns 1 if frame_index is in the configured capture set. Cheap (single
 * mutex acquire on first call to lazy-init, atomic-read after). Use in
 * tight loops where the caller would otherwise issue a capture() that
 * itself does the same check.
 */
int mlv_pipeline_capture_should_capture_frame(uint64_t frame_index);

/*!
 * Set/get a thread-local "current frame" index, for callsites that don't
 * otherwise have one in scope (e.g. inside applyLLRawProcObject which
 * doesn't take a frame index). Callers that own a frame index call
 * set_current_frame() before invoking the transformation; hooks inside
 * the transformation call get_current_frame() to label their capture.
 *
 * Default value (when set has not been called on the current thread) is
 * 0. Setting via NULL/zero is fine.
 */
void     mlv_pipeline_capture_set_current_frame(uint64_t frame_index);
uint64_t mlv_pipeline_capture_get_current_frame(void);

#ifdef __cplusplus
}
#endif

#endif /* MLV_PIPELINE_STAGE_CAPTURE_H */
