#include "../../src/mlv/llrawproc/llrawproc.h"

#include <cstring>

extern "C" void processingGamutRgbToY(int, double out_rgb_to_Y[3])
{
    out_rgb_to_Y[0] = 0.2126729;
    out_rgb_to_Y[1] = 0.7151522;
    out_rgb_to_Y[2] = 0.0721750;
}

extern "C" void processingAgxMatrices(double out_forward[9], double out_inverse[9])
{
    for (int i = 0; i < 9; ++i) {
        out_forward[i] = (i % 4) == 0 ? 1.0 : 0.0;
        out_inverse[i] = out_forward[i];
    }
}

extern "C" int llrpGpuPlaybackReconRunGlTexture(
    const llrpGpuPlaybackReconState_t *,
    const uint16_t *,
    size_t,
    unsigned int,
    int *rc_out,
    llrpGpuPlaybackReconTiming_t *timing_out)
{
    if (rc_out) {
        *rc_out = -1;
    }
    if (timing_out) {
        std::memset(timing_out, 0, sizeof(*timing_out));
    }
    return 0;
}
