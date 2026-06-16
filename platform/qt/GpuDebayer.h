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

struct GpuAmazeDebayerBackendAvailability
{
    bool available = false;
    QString reason;
    QString rendererDescription;
};

struct GpuAmazeDebayerBackendTiming
{
    bool available = false;
    double uploadMs = 0.0;
    double kernelMs = 0.0;
    double downloadMs = 0.0;
    double totalMs = 0.0;
};

const char * gpuBilinearDebayerEnvironmentVariableName(void);
bool gpuBilinearDebayerRequestedByEnvironment(void);
const char * gpuAmazeDebayerEnvironmentVariableName(void);
bool gpuAmazeDebayerRequestedByEnvironment(void);
GpuBilinearDebayerBackendAvailability gpuBilinearDebayerProbeBackend(void);
bool gpuBilinearDebayerApplyGpuOffscreen(const float * inputRawFrame,
                                         uint16_t * outputRgb16,
                                         int width,
                                         int height,
                                         QString * reason = nullptr,
                                         QString * rendererDescription = nullptr);
GpuAmazeDebayerBackendAvailability gpuAmazeDebayerProbeBackend(void);
bool gpuAmazeDebayerApplyGpuOffscreen(const float * inputRawFrame,
                                      uint16_t * outputRgb16,
                                      int width,
                                      int height,
                                      QString * reason = nullptr,
                                      QString * rendererDescription = nullptr,
                                      GpuAmazeDebayerBackendTiming * timing = nullptr);

#endif // GPUDEBAYER_H
