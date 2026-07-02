/*!
 * \file GpuDisplayViewport.h
 * \author Codex
 * \copyright 2026
 * \brief Optional OpenGL-backed viewport for the existing QGraphicsView preview.
 */

#ifndef GPUDISPLAYVIEWPORT_H
#define GPUDISPLAYVIEWPORT_H

#include "GpuDebayer.h"
#include "ZebraThresholds.h"
#include "GpuPreviewProcessing.h"
#include "../../src/mlv/llrawproc/llrawproc.h"
#include <QImage>
#include <QOpenGLFunctions>
#include <QtOpenGL/qopenglshaderprogram.h>
#include <QtOpenGL/qopengltexture.h>
#include <QtOpenGLWidgets/qopenglwidget.h>

class QGraphicsPixmapItem;
class QGraphicsView;

class GpuDisplayViewport : public QOpenGLWidget, protected QOpenGLFunctions
{
    Q_OBJECT
public:
    enum SamplingMode
    {
        SamplingNearest = 0,
        SamplingLinear = 1,
        SamplingBicubic = 2
    };

    struct PresentationOptions
    {
        SamplingMode samplingMode;
        bool showZebras;
        float zebraUnderThreshold;
        float zebraOverThreshold;
        GpuPreviewProcessingConfig previewProcessing;
        qint64 telemetryRequestSerial;
        qint64 telemetryDisplayFrame;
        double telemetryDrawBeginStageTime;

        PresentationOptions()
            : samplingMode(SamplingLinear)
            , showZebras(false)
            , zebraUnderThreshold(preview_zebra::kUnderThresholdNormalized)
            , zebraOverThreshold(preview_zebra::kOverThresholdNormalized)
            , previewProcessing()
            , telemetryRequestSerial(0)
            , telemetryDisplayFrame(0)
            , telemetryDrawBeginStageTime(0.0)
        {
        }
    };

    explicit GpuDisplayViewport(QWidget *parent = nullptr);
    ~GpuDisplayViewport() override;

    static bool isRequestedByEnvironment(void);
    static const char *environmentVariableName(void);
    static bool installOn(QGraphicsView *view);
    static bool isInstalledOn(const QGraphicsView *view);
    static bool hasPresentedImage(const QGraphicsView *view);
    static bool hasPresentedGpuReconTexture(const QGraphicsView *view);
    static bool isTexturePresentationActive(const QGraphicsView *view);
    static SamplingMode samplingModeFor(const QGraphicsView *view);
    static QString rendererDescriptionFor(const QGraphicsView *view);
    static bool readPresentedBayer16Texture(QGraphicsView *view,
                                            QByteArray *textureBytes,
                                            int *width,
                                            int *height,
                                            QString *reason = nullptr);
    static bool readGpuReconSourceBayer16Texture(QGraphicsView *view,
                                                 QByteArray *textureBytes,
                                                 int *width,
                                                 int *height,
                                                 QString *reason = nullptr);
    static bool presentImage(QGraphicsView *view,
                             QGraphicsPixmapItem *fallbackItem,
                             const QImage &image,
                             const PresentationOptions &options = PresentationOptions());
    static bool presentRgb16(QGraphicsView *view,
                             QGraphicsPixmapItem *fallbackItem,
                             const uint16_t *imageData,
                             int width,
                             int height,
                             const PresentationOptions &options = PresentationOptions());
    static bool presentBayer16(QGraphicsView *view,
                               QGraphicsPixmapItem *fallbackItem,
                               const uint16_t *imageData,
                               int width,
                               int height,
                               const PresentationOptions &options = PresentationOptions());
    static bool presentGpuPlaybackReconTexture(QGraphicsView *view,
                                               QGraphicsPixmapItem *fallbackItem,
                                               const uint16_t *rawInputBayer14,
                                               size_t rawInputBayer14Words,
                                               const llrpGpuPlaybackReconState_t *state,
                                               const PresentationOptions &options,
                                               QString *reason = nullptr,
                                               llrpGpuPlaybackReconTiming_t *timing = nullptr);
    static bool presentGpuPlaybackReconAmazePostWbTexture(QGraphicsView *view,
                                                          QGraphicsPixmapItem *fallbackItem,
                                                          const uint16_t *rawInputBayer14,
                                                          size_t rawInputBayer14Words,
                                                          const llrpGpuPlaybackReconState_t *state,
                                                          int blackLevel,
                                                          const double wbMultipliers[3],
                                                          const PresentationOptions &options,
                                                          QString *reason = nullptr,
                                                          llrpGpuPlaybackReconTiming_t *timing = nullptr,
                                                          QString *handoffMode = nullptr,
                                                          bool validationProbeTexture = false,
                                                          const uint16_t *retainedDeviceBayer16 = nullptr,
                                                          int retainedDeviceWidth = 0,
                                                          int retainedDeviceHeight = 0);
    static bool presentAmazePostWbTexture(QGraphicsView *view,
                                          QGraphicsPixmapItem *fallbackItem,
                                          const float *rawFrame,
                                          int width,
                                          int height,
                                          int blackLevel,
                                          const double wbMultipliers[3],
                                          const PresentationOptions &options,
                                          QString *reason = nullptr,
                                          QString *rendererDescription = nullptr,
                                          GpuAmazeDebayerBackendTiming *timing = nullptr);
    static void clearPresentedImage(QGraphicsView *view,
                                    QGraphicsPixmapItem *fallbackItem = nullptr);

protected:
    void initializeGL() override;
    void paintGL() override;
    void resizeGL(int w, int h) override;

private:
    static GpuDisplayViewport *from(QGraphicsView *view);
    static const GpuDisplayViewport *from(const QGraphicsView *view);

    void cleanupGLResources(void);
    void setFallbackItem(QGraphicsPixmapItem *item);
    void setPresentedImage(const QImage &image, const PresentationOptions &options);
    void setPresentedRgb16(const uint16_t *imageData, int width, int height, const PresentationOptions &options);
    void setPresentedBayer16(const uint16_t *imageData, int width, int height, const PresentationOptions &options);
    bool setPresentedGpuPlaybackReconTexture(const uint16_t *rawInputBayer14,
                                             size_t rawInputBayer14Words,
                                             const llrpGpuPlaybackReconState_t *state,
                                             const PresentationOptions &options,
                                             QString *reason,
                                             llrpGpuPlaybackReconTiming_t *timing);
    bool setPresentedGpuPlaybackReconAmazePostWbTexture(const uint16_t *rawInputBayer14,
                                                        size_t rawInputBayer14Words,
                                                        const llrpGpuPlaybackReconState_t *state,
                                                        int blackLevel,
                                                        const double wbMultipliers[3],
                                                        const PresentationOptions &options,
                                                        QString *reason,
                                                        llrpGpuPlaybackReconTiming_t *timing,
                                                        QString *handoffMode,
                                                        bool validationProbeTexture,
                                                        const uint16_t *retainedDeviceBayer16,
                                                        int retainedDeviceWidth,
                                                        int retainedDeviceHeight);
    bool setPresentedAmazePostWbTexture(const float *rawFrame,
                                        int width,
                                        int height,
                                        int blackLevel,
                                        const double wbMultipliers[3],
                                        const PresentationOptions &options,
                                        QString *reason,
                                        QString *rendererDescription,
                                        GpuAmazeDebayerBackendTiming *timing);
    void clearPresentedImage(void);
    void requestInstrumentedUpdate(const char *reason);
    void updateTextureIfNeeded(void);
    void updateProcessingTexturesIfNeeded(void);
    void ensureProgram(void);
    void destroyTexture(void);
    void destroyProcessingTextures(void);
    void applySamplingMode(void);
    void setPresentationOptions(const PresentationOptions &options);
    QRectF targetRectInViewport(void) const;
    int pendingWidth(void) const;
    int pendingHeight(void) const;
    bool hasPendingFrame(void) const;

    bool m_loggedContext;
    bool m_loggedTexturePath;
    bool m_textureDirty;
    bool m_texturePresentationActive;
    bool m_samplingModeDirty;
    bool m_processingTexturesDirty;
    bool m_pendingTextureIs16Bit;
    bool m_pendingTextureIsBayer16;
    bool m_pendingTextureFromGpuAmaze;
    bool m_pendingTextureFromGpuRecon;
    bool m_textureIs16Bit;
    bool m_textureIsBayer16;
    bool m_gpuReconSourceTextureCurrent;
    bool m_abPrimed;
    QGraphicsView *m_view;
    QGraphicsPixmapItem *m_fallbackItem;
    QImage m_pendingImage;
    QByteArray m_pendingTextureBytes;
    int m_pendingTextureWidth;
    int m_pendingTextureHeight;
    PresentationOptions m_presentationOptions;
    uint64_t m_processingTextureSignature;
    bool m_processingTextureSignatureValid;
    QString m_rendererDescription;
    double m_lastUpdateRequestStageTime;
    qulonglong m_updateRequestSequence;
    qulonglong m_lastPaintLoggedUpdateSequence;
    QString m_lastUpdateReason;
    qint64 m_lastUpdateRequestSerial;
    qint64 m_lastUpdateDisplayFrame;
    double m_lastUpdateDrawBeginStageTime;
    QOpenGLShaderProgram *m_program;
    QOpenGLTexture *m_texture;
    QOpenGLTexture *m_gpuReconSourceTexture;
    QOpenGLTexture *m_levelsLutTexture;
    QOpenGLTexture *m_matrixLutRTexture;
    QOpenGLTexture *m_matrixLutGTexture;
    QOpenGLTexture *m_matrixLutBTexture;
    QOpenGLTexture *m_gammaLutTexture;
};

#endif // GPUDISPLAYVIEWPORT_H
