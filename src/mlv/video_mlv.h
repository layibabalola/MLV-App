#ifndef _video_mlv_
#define _video_mlv_

#include "raw.h"
#include "mlv.h"
#include "../processing/raw_processing.h"

/* mlvObject_t */
#include "mlv_object.h"

/* Usefull macros */
#include "macros.h"

/* All functions in one */
mlvObject_t * initMlvObjectWithClip(char * mlvPath, int preview, int * err, char * error_message);
mlvObject_t * initMlvObjectWithMcrawClip(char * mlvPath, int preview, int * err, char * error_message);

/* Initialises an MLV object. That's all you need to know */
mlvObject_t * initMlvObject();

/* Prints everything you'll ever need to know */
void printMlvInfo(mlvObject_t * video);

/* Reads an MLV file in to a video object(mlvObject_t struct)
 * only puts frame indexes and metadata in to the mlvObject_t, 
 * no debayering or processing */
int openMlvClip(mlvObject_t * video, char * mlvPath, int open_mode, char * error_message);
int openMcrawClip(mlvObject_t * video, char * mcrawPath, int open_mode, char * error_message);

/* return error codes of and open modes of openMlvClip() */
enum mlv_err { MLV_ERR_NONE, MLV_ERR_OPEN, MLV_ERR_IO, MLV_ERR_CORRUPTED, MLV_ERR_INVALID };
enum open_mode { MLV_OPEN_FULL, MLV_OPEN_MAPP, MLV_OPEN_PREVIEW };

/* Functions for saving cut or averaged MLV */
int saveMlvHeaders(mlvObject_t * video, FILE * output_mlv, int export_audio, int export_mode, uint32_t frame_start, uint32_t frame_end, const char * version, char * error_message);
int saveMlvAVFrame(mlvObject_t * video, FILE * output_mlv, int export_audio, int export_mode, uint32_t frame_start, uint32_t frame_end, uint32_t frame_index, uint64_t * avg_buf, char * error_message);
enum export_mode { MLV_FAST_PASS, MLV_COMPRESS, MLV_DECOMPRESS, MLV_AVERAGED_FRAME, MLV_DF_INT };
/* from darkframe.c */
extern int df_init(mlvObject_t * video);

/* Frees all memory and closes file */
void freeMlvObject(mlvObject_t * video);

/* To enable and disable caching */
void disableMlvCaching(mlvObject_t * video);
void enableMlvCaching(mlvObject_t * video);
/* Reset cache, to recache all frames, clears simgle frame cache too */
void resetMlvCache(mlvObject_t * video);
void invalidateMlvProcessedPreviewCache(mlvObject_t * video);
/* For setting how much can be cached - "MegaBytes" == MebiBytes (thanks dmilligan) */
void setMlvRawCacheLimitMegaBytes(mlvObject_t * video, uint64_t megaByteLimit);
void setMlvRawCacheLimitFrames(mlvObject_t * video, uint64_t frameLimit);

/* Links processing settings() with an MLV object */
void setMlvProcessing(mlvObject_t * video, processingObject_t * processing);
/* Function for WB Picker */
void findMlvWhiteBalance(mlvObject_t * video, uint64_t frameIndex, int posX, int posY, int *wbTemp, int *wbTint, int mode);

/* Functions for getting processed MLV frames - uses the 'processing' module,
 * Avalible in 8 and 16 bit! Neither is faster as processing for both is done in 16 bit,
 * only use more than one thread in threads argument for speedier preview, not for export
 * as it may have minor artifacts (though I haven't found them yet) */
void getMlvProcessedFrame8(mlvObject_t * video, uint64_t frameIndex, uint8_t * outputFrame, int threads);
void getMlvProcessedFrame16(mlvObject_t * video, uint64_t frameIndex, uint16_t * outputFrame, int threads);

/* Phase 4A: scale-aware processed-frame getters.
 *
 * The scaleFactor contract is:
 *   - must be 1, 2, or 4
 *   - must be a power of two (Phase 4B will reject non-power-of-two values
 *     to preserve the 2x2 Bayer block alignment)
 *   - if scaleFactor is invalid, the call is treated as scaleFactor == 1
 *
 * Phase 4A note: the rendering pipeline still produces full-resolution
 * output regardless of scaleFactor. The parameter is observed only by:
 *   1. the processed8/16 cache key (so a scale=1 result cannot satisfy a
 *      scale=2 lookup, and vice versa), and
 *   2. mlvFrameOutputDimensions() reporting (always returns full res in
 *      Phase 4A).
 *
 * Phase 4B will add the fused-downsample-and-debayer that actually
 * produces a smaller buffer for scaleFactor > 1. Callers wiring through
 * the scaled API today are forward-compatible: their output buffer must be
 * sized to mlvFrameOutputDimensions(), and once Phase 4B lands the same
 * call site automatically gets the smaller render. */
void getMlvProcessedFrame8Scaled(mlvObject_t * video,
                                 uint64_t frameIndex,
                                 uint8_t * outputFrame,
                                 int threads,
                                 int scaleFactor);
int getMlvProcessedFrame8ScaledFromRaw16(mlvObject_t * video,
                                         uint64_t frameIndex,
                                         const uint16_t * decodedRawFrame,
                                         uint8_t * outputFrame,
                                         int threads,
                                         int scaleFactor);
int getMlvProcessedFrame8ScaledFromReconnedRaw16(mlvObject_t * video,
                                                 uint64_t frameIndex,
                                                 const uint16_t * reconnedRawFrame,
                                                 uint8_t * outputFrame,
                                                 int threads,
                                                 int scaleFactor);
void getMlvProcessedFrame16Scaled(mlvObject_t * video,
                                  uint64_t frameIndex,
                                  uint16_t * outputFrame,
                                  int threads,
                                  int scaleFactor);

/* Reports the (width,height) a Phase-4-aware caller should size the output
 * buffer for. In Phase 4A this always returns the full sensor dimensions
 * (the pipeline still renders at scale=1 internally). In Phase 4B it will
 * return W/scaleFactor and H/scaleFactor when scaleFactor > 1.
 * scaleFactor must be 1, 2, or 4; invalid values are treated as 1. */
void mlvFrameOutputDimensions(mlvObject_t * video,
                              int scaleFactor,
                              int * outWidth,
                              int * outHeight);
double getMlvLastRawUint16Milliseconds(void);
double getMlvLastRawUint16DiskReadMilliseconds(void);
double getMlvLastRawUint16DecompressMilliseconds(void);
double getMlvLastRawUint16DecompressPrepareMilliseconds(void);
double getMlvLastRawUint16DecompressExecuteMilliseconds(void);
int getMlvLastRawUint16Lj92Pred6SplitActive(void);
int getMlvLastRawUint16Lj92Pred6SplitRequested(void);
int getMlvLastRawUint16Lj92GenericSplitActive(void);
int getMlvLastRawUint16Lj92GenericSplitRequested(void);
int getMlvLastRawUint16Lj92Pred1FastPathActive(void);
int getMlvLastRawUint16Lj92Pred1FastPathMeasurementRequested(void);
int getMlvLastRawUint16Lj92Pred1FastPathMeasurementActive(void);
int getMlvLastRawUint16Lj92Pred1FastPathEligible(void);
int getMlvLastRawUint16Lj92ScanComponentCount(void);
int getMlvLastRawUint16Lj92WriteLength(void);
int getMlvLastRawUint16Lj92ExpectedWriteLength(void);
int getMlvLastRawUint16Lj92SkipLength(void);
int getMlvLastRawUint16Lj92LinearizeActive(void);
int getMlvLastRawUint16Lj92ComponentCount(void);
int getMlvLastRawUint16Lj92Predictor(void);
double getMlvLastRawUint16Lj92Pred6TotalMilliseconds(void);
double getMlvLastRawUint16Lj92Pred6BitstreamMilliseconds(void);
double getMlvLastRawUint16Lj92Pred6PredictorMilliseconds(void);
double getMlvLastRawUint16Lj92GenericTotalMilliseconds(void);
double getMlvLastRawUint16Lj92GenericBitstreamMilliseconds(void);
double getMlvLastRawUint16Lj92GenericPredictorMilliseconds(void);
double getMlvLastRawUint16Lj92Pred1FastPathTotalMilliseconds(void);
double getMlvLastRawUint16Lj92Pred1FastPathBitstreamMilliseconds(void);
double getMlvLastRawUint16Lj92Pred1FastPathPredictorMilliseconds(void);
double getMlvLastRawUint16UnpackMilliseconds(void);
double getMlvLastRawUint16CopyMilliseconds(void);
int getMlvLastRawUint16PrefetchHit(void);
uint64_t getMlvRawUint16PrefetchDecodeFailures(mlvObject_t * video);
double getMlvLastLlrawprocMilliseconds(void);
double getMlvLastRawFloatConvertMilliseconds(void);
double getMlvLastDebayeredFrameMilliseconds(void);
void resetMlvLastDebayerStageMilliseconds(void);
double getMlvLastDebayerWbPrepareMilliseconds(void);
double getMlvLastDebayerCaMilliseconds(void);
double getMlvLastDebayerKernelMilliseconds(void);
double getMlvLastDebayerWbUndoMilliseconds(void);
double getMlvLastProcessingMilliseconds(void);
double getMlvLastProcessed16TotalMilliseconds(void);
double getMlvLastProcessed16For8BitMilliseconds(void);
double getMlvLastProcessed16To8BitMilliseconds(void);
double getMlvLastProcessed8TotalMilliseconds(void);
int getMlvLastProcessed8DirectPathActive(void);
int getMlvLastProcessed8PrefetchHit(void);

/* Phase 4B-v2/v3 telemetry — for parity tests and diagnostics. Returns the
 * path taken on the most recent v2 entry on the calling thread:
 *   3 = v3 full-XY pre-recon (Y-cropped if necessary)
 *   2 = v2 X-only pre-recon fallback
 *   0 = v2 entry not invoked / rejected before path selection. */
int mlv_phase4bv2_last_path_taken(void);
/* Number of source rows cropped from the bottom edge by the v3 Y-crop
 * wrapper on the most recent v2 entry on the calling thread. 0 if v3
 * wasn't taken or if the clip was already 16-Y-aligned. */
int mlv_phase4bv3_last_y_crop_rows(void);

/* Test-only hook: clear the cached env-var values for MLVAPP_DISABLE_PHASE4BV2
 * and MLVAPP_DISABLE_PHASE4BV3 so subsequent calls re-read getenv(). Used by
 * parity tests that flip the kill switches mid-process. */
void mlv_phase4bv_reset_env_cache_for_testing(void);

/* Unpacks the bits of a frame to get a bayer B&W image (without black level correction)
 * Needs memory to return to, sized: sizeof(float) * getMlvHeight(urvid) * getMlvWidth(urvid)
 * Output values will be in range 0-65535 (16 bit), float is only because AMAzE uses it */
int getMlvRawFrameUint16(mlvObject_t * video, uint64_t frameIndex, uint16_t * unpackedFrame);
int getMlvRawFrameProcessedUint16(mlvObject_t * video,
                                  uint64_t frameIndex,
                                  uint16_t * outputFrame,
                                  int * bit_shift);
void getMlvRawFrameFloat(mlvObject_t * video, uint64_t frameIndex, float * outputFrame);

/* Gets a debayered 16 bit frame */
void getMlvRawFrameDebayered(mlvObject_t * video, uint64_t frameIndex, uint16_t * outputFrame);

/* For processing only, no use to average library user ;) Camera RGB -> sRGB */
void getMlvCameraTosRGBMatrix(mlvObject_t * video, double * outputMatrix); /* Still havent had any success here */

/* Gets image aspect ratio according to RAWC block info, calculating from binnin + skipping values */
float getMlvAspectRatio(mlvObject_t * video);

/* Set imaginary lossless bit depth value */
void setMlvLosslessBpp(mlvObject_t * video);

/******************************** 
 ********* PRIVATE AREA *********
 ********************************/

/* Add as many of these as you want :) */
void an_mlv_cache_thread(mlvObject_t * video);

/* Marks all frames as not cached */
void mark_mlv_uncached(mlvObject_t * video);

/* Clears cache by freeing then reallocating (RAM usage down until frames written) */
void clear_mlv_cache(mlvObject_t * video);

/* Cache window helpers */
int mlv_frame_in_cache_window(mlvObject_t * video, uint64_t frameIndex);
uint64_t mlv_cache_slot_for_frame(mlvObject_t * video, uint64_t frameIndex);
void mlv_cache_ensure_window(mlvObject_t * video, uint64_t frameIndex);
void mlv_cache_request_playback_preroll(mlvObject_t * video,
                                        uint64_t currentFrame,
                                        uint64_t lastFrameInclusive,
                                        uint64_t lookaheadFrames);

/* Returns 1 on success, or 0 if all are cached */
int find_mlv_frame_to_cache(mlvObject_t * video, uint64_t *index); /* Outputs to *index */

/* Adds one thread, active total can be checked in mlvObject->cache_thread_count */
void add_mlv_cache_thread(mlvObject_t * video);

/* OLD DEPRACTEDFSDJKHJKLAJSKDLJ KLSDJKL AJSD LKSAJDLKSAJDLK DKJS */
void cache_mlv_frames(mlvObject_t * video);

/* Gets a debayered frame; how is it different from getMlvRawFrameDebayered?... it doesn't get it from cache ever
 * also you must allocate it some temporary memory because that's how it works and you shouldnt be looking anyway */
void get_mlv_raw_frame_debayered(mlvObject_t * video,
                                  uint64_t frame_index,
                                  float * temp_memory,
                                  uint16_t * output_frame,
                                  int debayer_type ); /* Debayer type: 0=bilinear 1=amaze */

/* Thumbnail Creation with a downscaled raw image sub-sampling algorithm is used. */
int create_thumbnail(mlvObject_t * video, uint8_t * thumbnail_img, int downscaled_factor, int width, int height, int threads);

/* Thumbnail Creation with full debayer, but downscaled image processing */
void get_area_average_downscale_thumnail(mlvObject_t *video, int frame_index, int downscale_factor, int cpu_cores, unsigned char *out_buffer);

#endif
