/*!
 * \file RenderFrameThread.h
 * \author masc4ii
 * \copyright 2017
 * \brief The render thread
 */

#ifndef RENDERFRAMETHREAD_H
#define RENDERFRAMETHREAD_H

#include <QThread>
#include <QMutex>
#include <QWaitCondition>
#include <QString>
#include <QJsonObject>
#include "../../src/mlv_include.h"
#include "PlaybackScaling.h"

#include <array>
#include <vector>

class RenderFrameThread : public QThread
{
    Q_OBJECT

public:
    enum OutputMode
    {
        OutputProcessed8 = 0,
        OutputProcessed16,
        OutputDebayered16
    };

    struct ReadyFrame
    {
        const uint8_t *rawImage8 = nullptr;
        const uint16_t *rawImage16 = nullptr;
        const uint8_t *playbackScaledImage8 = nullptr;
        uint32_t frameNumber = 0;
        uint64_t requestSerial = 0;
        OutputMode outputMode = OutputProcessed8;
        bool playbackFastScaleActive = false;
        int playbackScaledWidth = 0;
        int playbackScaledHeight = 0;
        bool usedGpuBilinearDebayer = false;
        QString gpuBilinearFallbackReason;
        QString gpuBilinearRendererDescription;
        double dualIsoPreviewHistogramMs = 0.0;
        double dualIsoPreviewRegressionMs = 0.0;
        double dualIsoPreviewRowscaleMs = 0.0;
        double frameReadyEmitStageTime = 0.0;
        bool processedFrame8Active = false;
        uint64_t processedFrame8Signature = 0;
        bool processedFrame16Active = false;
        uint64_t processedFrame16Signature = 0;
        int dualIsoPattern = 0;
        int dualIsoAutoCorrection = 0;
        double dualIsoEvCorrection = 0.0;
        int dualIsoBlackDelta = 0;
        QJsonObject stageTimingTelemetry;
    };

    struct PresentationPreparationOptions
    {
        bool fastPlaybackScale = false;
        int targetWidth = 0;
        int targetHeight = 0;
    };

    RenderFrameThread();
    ~RenderFrameThread();
    void init( mlvObject_t *pMlvObject,
               int imageWidth,
               int imageHeight );
    void renderFrame( uint32_t frameNumber,
                      OutputMode outputMode,
                      bool useGpuBilinearDebayer,
                      uint64_t requestSerial,
                      const PresentationPreparationOptions &presentationPreparation );
    bool isFrameReady( void );
    bool isIdle( void );
    bool acquireLatestReadyFrame( ReadyFrame *frame );
    void releasePresentedFrame( void );
    bool lastFrameUsedGpuBilinearDebayer( void ) const;
    QString lastGpuBilinearFallbackReason( void ) const;
    QString lastGpuBilinearRendererDescription( void ) const;
    double lastDualIsoPreviewHistogramMilliseconds( void ) const;
    double lastDualIsoPreviewRegressionMilliseconds( void ) const;
    double lastDualIsoPreviewRowscaleMilliseconds( void ) const;
    QJsonObject lastStageTimingTelemetry( void ) const;
    double lastFrameReadyEmitStageTime( void ) const;
    void stop( void );
    void lock( void );
    void unlock( void );

signals:
    void frameReady( void );

private:
    struct FrameSlot
    {
        std::vector<uint8_t> rawImage8;
        std::vector<uint16_t> rawImage16;
        std::vector<uint8_t> playbackScaledImage8;
        uint32_t frameNumber = 0;
        uint64_t requestSerial = 0;
        OutputMode outputMode = OutputProcessed8;
        bool ready = false;
        bool presenting = false;
        bool playbackFastScaleActive = false;
        int playbackScaledWidth = 0;
        int playbackScaledHeight = 0;
        bool usedGpuBilinearDebayer = false;
        QString gpuBilinearFallbackReason;
        QString gpuBilinearRendererDescription;
        double dualIsoPreviewHistogramMs = 0.0;
        double dualIsoPreviewRegressionMs = 0.0;
        double dualIsoPreviewRowscaleMs = 0.0;
        double frameReadyEmitStageTime = 0.0;
        bool processedFrame8Active = false;
        uint64_t processedFrame8Signature = 0;
        bool processedFrame16Active = false;
        uint64_t processedFrame16Signature = 0;
        int dualIsoPattern = 0;
        int dualIsoAutoCorrection = 0;
        double dualIsoEvCorrection = 0.0;
        int dualIsoBlackDelta = 0;
        QJsonObject stageTimingTelemetry;

        void resetMetadata( void )
        {
            frameNumber = 0;
            requestSerial = 0;
            outputMode = OutputProcessed8;
            ready = false;
            presenting = false;
            playbackFastScaleActive = false;
            playbackScaledWidth = 0;
            playbackScaledHeight = 0;
            usedGpuBilinearDebayer = false;
            gpuBilinearFallbackReason.clear();
            gpuBilinearRendererDescription.clear();
            dualIsoPreviewHistogramMs = 0.0;
            dualIsoPreviewRegressionMs = 0.0;
            dualIsoPreviewRowscaleMs = 0.0;
            frameReadyEmitStageTime = 0.0;
            processedFrame8Active = false;
            processedFrame8Signature = 0;
            processedFrame16Active = false;
            processedFrame16Signature = 0;
            dualIsoPattern = 0;
            dualIsoAutoCorrection = 0;
            dualIsoEvCorrection = 0.0;
            dualIsoBlackDelta = 0;
            stageTimingTelemetry = QJsonObject();
        }
    };

    mutable QMutex m_mutex;
    QWaitCondition m_waitCondition;
    mlvObject_t *m_pMlvObject;
    bool m_initialized;
    bool m_stop;
    bool m_renderFrame;
    bool m_renderingFrame;
    bool m_frameReady;
    OutputMode m_outputMode;
    bool m_useGpuBilinearDebayer;
    uint32_t m_frameNumber;
    uint64_t m_frameRequestSerial;
    OutputMode m_activeOutputMode;
    bool m_activeUseGpuBilinearDebayer;
    uint32_t m_activeFrameNumber;
    uint64_t m_activeFrameRequestSerial;
    PresentationPreparationOptions m_presentationPreparationOptions;
    PresentationPreparationOptions m_activePresentationPreparationOptions;
    bool m_loggedGpuBilinearSuccess;
    bool m_lastFrameUsedGpuBilinearDebayer;
    QString m_lastGpuBilinearFallbackReason;
    QString m_lastGpuBilinearRendererDescription;
    double m_lastDualIsoPreviewHistogramMs;
    double m_lastDualIsoPreviewRegressionMs;
    double m_lastDualIsoPreviewRowscaleMs;
    double m_frameRequestStageTime;
    double m_activeFrameRequestStageTime;
    double m_lastRenderThreadQueueWaitMs;
    double m_lastRenderThreadWorkMs;
    double m_lastRenderThreadTotalMs;
    double m_lastFrameReadyEmitStageTime;
    QJsonObject m_lastStageTimingTelemetry;
    int m_imageWidth;
    int m_imageHeight;
    int m_renderingSlotIndex;
    int m_presentingSlotIndex;
    std::array<FrameSlot, 2> m_frameSlots;
    FastPlaybackScaleCache m_playbackScaleCache;
    std::vector<float> m_gpuBilinearDebayerRawFrame;

    void run( void );
    void drawFrame( int slotIndex );
    int findLatestReadySlotLocked( void ) const;
    int findFreeSlotLocked( void ) const;
    void releaseSlotLocked( int slotIndex );
    void copySlotTelemetryLocked( const FrameSlot &slot );
};

#endif // RENDERFRAMETHREAD_H
