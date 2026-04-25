#include <cstdint>
#include <cstdlib>

#include "../../src/mlv_include.h"
extern "C" {
#include "../../src/debayer/wb_conversion.h"
#include "../../src/debayer/debayer.h"
}
#ifndef restrict
#define restrict __restrict
#endif
#include "../../src/librtprocess/src/include/librtprocesswrapper.h"

#include <cstring>

extern "C" {

int llrpDetectFocusDotFixMode(mlvObject_t * video)
{
    switch(video->IDNT.cameraModel)
    {
        case 0x80000331:
        case 0x80000355:
        case 0x80000346:
        case 0x80000301:
        case 0x80000326:
            return 1;
        default:
            return 0;
    }
}

int llrpGetFixRawMode(mlvObject_t * video) { return video->llrawproc->fix_raw; }
void llrpSetFixRawMode(mlvObject_t * video, int value) { video->llrawproc->fix_raw = value; }
int llrpGetVerticalStripeMode(mlvObject_t * video) { return video->llrawproc->vertical_stripes; }
void llrpSetVerticalStripeMode(mlvObject_t * video, int value) { video->llrawproc->vertical_stripes = value; }
void llrpComputeStripesOn(mlvObject_t * video) { video->llrawproc->compute_stripes = 1; }
int llrpGetFocusPixelMode(mlvObject_t * video) { return video->llrawproc->focus_pixels; }
void llrpSetFocusPixelMode(mlvObject_t * video, int value) { video->llrawproc->focus_pixels = value; }
int llrpGetFocusPixelInterpolationMethod(mlvObject_t * video) { return video->llrawproc->fpi_method; }
void llrpSetFocusPixelInterpolationMethod(mlvObject_t * video, int value) { video->llrawproc->fpi_method = value; }
int llrpGetBadPixelMode(mlvObject_t * video) { return video->llrawproc->bad_pixels; }
void llrpSetBadPixelMode(mlvObject_t * video, int value) { video->llrawproc->bad_pixels = value; }
int llrpGetBadPixelSearchMethod(mlvObject_t * video) { return video->llrawproc->bps_method; }
void llrpSetBadPixelSearchMethod(mlvObject_t * video, int value) { video->llrawproc->bps_method = value; }
int llrpGetBadPixelInterpolationMethod(mlvObject_t * video) { return video->llrawproc->bpi_method; }
void llrpSetBadPixelInterpolationMethod(mlvObject_t * video, int value) { video->llrawproc->bpi_method = value; }
int llrpGetChromaSmoothMode(mlvObject_t * video) { return video->llrawproc->chroma_smooth; }
void llrpSetChromaSmoothMode(mlvObject_t * video, int value) { video->llrawproc->chroma_smooth = value; }
int llrpGetPatternNoiseMode(mlvObject_t * video) { return video->llrawproc->pattern_noise; }
void llrpSetPatternNoiseMode(mlvObject_t * video, int value) { video->llrawproc->pattern_noise = value; }
int llrpGetDeflickerTarget(mlvObject_t * video) { return video->llrawproc->deflicker_target; }
void llrpSetDeflickerTarget(mlvObject_t * video, int value) { video->llrawproc->deflicker_target = value; }
int llrpGetDualIsoMode(mlvObject_t * video) { return video->llrawproc->dual_iso; }
void llrpSetDualIsoMode(mlvObject_t * video, int value) { video->llrawproc->dual_iso = value; }
int llrpGetDualIsoInterpolationMethod(mlvObject_t * video) { return video->llrawproc->diso_averaging; }
void llrpSetDualIsoInterpolationMethod(mlvObject_t * video, int value) { video->llrawproc->diso_averaging = value; }
int llrpGetDualIsoPlaybackForceMean23(mlvObject_t * video) { return video->llrawproc->diso_playback_force_mean23; }
void llrpSetDualIsoPlaybackForceMean23(mlvObject_t * video, int value) { video->llrawproc->diso_playback_force_mean23 = value ? 1 : 0; }
int llrpGetDualIsoAliasMapMode(mlvObject_t * video) { return video->llrawproc->diso_alias_map; }
void llrpSetDualIsoAliasMapMode(mlvObject_t * video, int value) { video->llrawproc->diso_alias_map = value; }
int llrpGetDualIsoFullResBlendingMode(mlvObject_t * video) { return video->llrawproc->diso_frblending; }
void llrpSetDualIsoFullResBlendingMode(mlvObject_t * video, int value) { video->llrawproc->diso_frblending = value; }
int llrpGetDualIsoValidity(mlvObject_t * video) { return video->llrawproc->diso_validity; }
void llrpSetDualIsoValidity(mlvObject_t * video, int diso_force)
{
    video->llrawproc->diso_validity = diso_force ? DISO_FORCED : DISO_INVALID;
}
int llrpHQDualIso(mlvObject_t * video)
{
    return (video->llrawproc->dual_iso == 1) && video->llrawproc->diso_validity && video->llrawproc->fix_raw;
}
void llrpResetDngBWLevels(mlvObject_t * video)
{
    video->llrawproc->dng_black_level = getMlvBlackLevel(video);
    video->llrawproc->dng_white_level = getMlvWhiteLevel(video);
    video->llrawproc->dng_bit_depth = getMlvBitdepth(video);
}
void llrpResetFpmStatus(mlvObject_t * video) { video->llrawproc->fpm_status = 0; }
void llrpResetBpmStatus(mlvObject_t * video) { video->llrawproc->bpm_status = 0; }
void llrpInitDarkFrameExtFileName(mlvObject_t * video, char * df_filename)
{
    if (video->llrawproc->dark_frame_filename) {
        std::free(video->llrawproc->dark_frame_filename);
    }
    const std::size_t length = std::strlen(df_filename) + 1;
    video->llrawproc->dark_frame_filename = static_cast<char *>(std::malloc(length));
    std::memcpy(video->llrawproc->dark_frame_filename, df_filename, length);
}
void llrpFreeDarkFrameExtFileName(mlvObject_t * video)
{
    if (video->llrawproc->dark_frame_filename) {
        std::free(video->llrawproc->dark_frame_filename);
        video->llrawproc->dark_frame_filename = nullptr;
    }
}
int llrpGetDarkFrameMode(mlvObject_t * video) { return video->llrawproc->dark_frame; }
void llrpSetDarkFrameMode(mlvObject_t * video, int value) { video->llrawproc->dark_frame = value; }
int llrpGetDarkFrameExtStatus(mlvObject_t * video) { return video->llrawproc->dark_frame_filename ? 1 : 0; }
int llrpGetDarkFrameIntStatus(mlvObject_t *) { return 0; }
int llrpValidateExtDarkFrame(mlvObject_t *, char *, char * error_message)
{
    if (error_message) {
        error_message[0] = '\0';
    }
    return 0;
}

void processingSetTransformation(processingObject_t * processing, int transformation)
{
    processing->transformation = static_cast<uint8_t>(transformation);
}

void processingSetBlackAndWhiteLevel(processingObject_t * processing,
                                     float mlvBlackLevel,
                                     int mlvWhiteLevel,
                                     int mlvBitDepth)
{
    const int bits_shift = 16 - mlvBitDepth;
    if (mlvBlackLevel >= 0) {
        processing->black_level = mlvBlackLevel * (1 << bits_shift);
    }
    if (mlvWhiteLevel >= 0) {
        processing->white_level = static_cast<int>((mlvWhiteLevel << bits_shift) * 0.993);
    }
}

void processingSetBlackLevel(processingObject_t * processing, float mlvBlackLevel, int mlvBitDepth)
{
    processingSetBlackAndWhiteLevel(processing, mlvBlackLevel, -1, mlvBitDepth);
}

void processingSetWhiteLevel(processingObject_t * processing, int mlvWhiteLevel, int mlvBitDepth)
{
    processingSetBlackAndWhiteLevel(processing, -1, mlvWhiteLevel, mlvBitDepth);
}

void getMlvRawFrameFloat(mlvObject_t *, uint64_t, float *) {}
int getMlvRawFrameProcessedUint16(mlvObject_t *, uint64_t, uint16_t *, int * bit_shift)
{
    if (bit_shift) {
        *bit_shift = 0;
    }
    return 0;
}

void wb_convert(wb_convert_info_t * wb_info, float *, int, int, int)
{
    if (wb_info) {
        wb_info->r = 1.0f;
        wb_info->g = 1.0f;
        wb_info->b = 1.0f;
    }
}

void wb_undo(const wb_convert_info_t *, uint16_t *, int, int, int) {}

void debayerLibRtProcess(uint16_t *, float *, int, int, int, double[9]) {}
void debayerAmaze(uint16_t *, float *, int, int, int, int) {}
void debayerEasy(uint16_t *, float *, int, int, int, int) {}
void debayerNoneU16(uint16_t *, const uint16_t *, int, int, int, int) {}
void debayerBasicU16(uint16_t *, uint16_t *, int, int, int, int) {}
int debayerBasicU16Avx2Active(void) { return 0; }
void debayerAhd(uint16_t *, float *, int, int) {}
void debayerBasic(uint16_t *, float *, int, int, int) {}

int getMlvLastProcessed8DirectPathActive(void) { return 0; }

void demosaic(amazeinfo_t *) {}

void lrtpCaCorrect(float **, int, int, int, int, const uint8_t, size_t, const double, const double, uint8_t) {}

} // extern "C"
