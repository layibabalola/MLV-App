#ifndef GPUPREVIEWPROCESSING_H
#define GPUPREVIEWPROCESSING_H

#include "../../src/processing/processing_object.h"
#include <QByteArray>
#include <QString>

#include <cstdint>

struct GpuPreviewProcessingConfig
{
    bool enabled = false;
    bool useCameraMatrix = false;
    bool applyGamutCompression = false;
    float sourceExposureStops = 0.0f;
    float properWbMatrix[9] = { 0.0f };
    float rgbToY[3] = { 0.0f };
    QByteArray levelsLut;
    QByteArray matrixLutR;
    QByteArray matrixLutG;
    QByteArray matrixLutB;
    QByteArray gammaLut;
    bool applyCreativeCurves = false;
    bool applyToning = false;
    float toningGain[3] = { 1.0f, 1.0f, 1.0f };
    bool applyVibrance = false;
    float vibrance = 1.0f;
    bool applySaturation = false;
    float saturation = 1.0f;
    bool applyHueVs = false;
    bool applyInLoopContrast = false;
    bool applyAgx = false;
    float agxForward[9] = { 0.0f };
    float agxInverse[9] = { 0.0f };
    float sourceContrast = 0.0f;
    QByteArray inLoopContrastCurve;
    QByteArray contrastCurveLut;
    QByteArray gradationLutY;
    QByteArray gradationLutR;
    QByteArray gradationLutG;
    QByteArray gradationLutB;
    QByteArray hueVsHueCurve;
    QByteArray hueVsSaturationCurve;
    QByteArray hueVsLumaCurve;
    QByteArray lumaVsSaturationCurve;
    uint64_t signature = 0;
};

const char * gpuPreviewProcessingEnvironmentVariableName(void);
bool gpuPreviewProcessingRequestedByEnvironment(void);
QByteArray gpuPreviewProcessingVertexShaderSource(void);
QByteArray gpuPreviewProcessingDisplayFragmentShaderSource(void);
QByteArray gpuPreviewProcessingSubsetFragmentShaderSource(void);
QByteArray gpuPreviewProcessingPackLookupTextureRgba16(const QByteArray & sourceLut);
bool gpuPreviewProcessingRendererIsSoftware(const QString & rendererDescription);
bool gpuPreviewProcessingIsSupported(const processingObject_t * processing,
                                     QString * reason = nullptr);
GpuPreviewProcessingConfig gpuPreviewProcessingBuildConfig(
    const processingObject_t * processing,
    QString * reason = nullptr);
struct GpuPreviewProcessingBackendAvailability
{
    bool available = false;
    QString reason;
    QString rendererDescription;
};
GpuPreviewProcessingBackendAvailability gpuPreviewProcessingProbeGpuBackend(void);
void gpuPreviewProcessingApplyCpuReference(const GpuPreviewProcessingConfig & config,
                                           const uint16_t * inputRgb16,
                                           uint16_t * outputRgb16,
                                           int pixelCount);
bool gpuPreviewProcessingApplyGpuOffscreen(const GpuPreviewProcessingConfig & config,
                                           const uint16_t * inputRgb16,
                                           uint16_t * outputRgb16,
                                           int width,
                                           int height,
                                           QString * reason = nullptr,
                                           QString * rendererDescription = nullptr);

#endif // GPUPREVIEWPROCESSING_H
