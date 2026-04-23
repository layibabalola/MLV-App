#ifndef GPUDEBAYER_H
#define GPUDEBAYER_H

#include <QString>

#include <cstdint>

struct GpuBilinearDebayerBackendAvailability
{
    bool available = false;
    QString reason;
    QString rendererDescription;
};

const char * gpuBilinearDebayerEnvironmentVariableName(void);
bool gpuBilinearDebayerRequestedByEnvironment(void);
GpuBilinearDebayerBackendAvailability gpuBilinearDebayerProbeBackend(void);
bool gpuBilinearDebayerApplyGpuOffscreen(const float * inputRawFrame,
                                         uint16_t * outputRgb16,
                                         int width,
                                         int height,
                                         QString * reason = nullptr,
                                         QString * rendererDescription = nullptr);

#endif // GPUDEBAYER_H
