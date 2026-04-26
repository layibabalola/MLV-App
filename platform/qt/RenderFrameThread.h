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
#include "MainWindowGpuPreviewPolicy.h"
#include "Phase3Mode.h"
#include "PlaybackScaling.h"

#include <array>
#include <atomic>
#include <deque>
#include <vector>

class DecodeWorker;
class ReconWorker;

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

    enum class SlotState : uint8_t
    {
        Idle = 0,
        Requested,
        Decoding,
        Decoded,
        ReconReady,
        Recon,
        ProcessReady,
        Processing,
        Ready,
        Presenting
    };

    struct ReadyFrame
    {
        struct PresentationContext
        {
            uint64_t requestSerial = 0;
            uint32_t frameNumber = 0;
            int sceneWidth = 0;
            int sceneHeight = 0;
            int imageWidth = 0;
            int imageHeight = 0;
            int devicePixelRatioMilli = 0;
            bool zoomFitEnabled = false;
            bool fastPlaybackScaleEligible = false;
            /* Phase 4A: requested playback scale factor (1, 2, or 4). Always
             * 1 in production today; observed only by the cache key. */
            int playbackScaleFactor = 1;
            MainWindowGpuPreviewPolicyState gpuPreviewPolicy;
            GpuDisplayViewport::PresentationOptions gpuPresentationOptions;
            GpuPreviewProcessingConfig gpuPreviewProcessingConfig;
            QString playbackProcessingReason;
            bool renderThreadUsing16BitPreview = false;
            bool renderThreadUsingGpuPreviewProcessing = false;
            bool renderThreadUsingGpuBilinearDebayer = false;
            bool renderThreadUsingCpuPreviewProcessing = false;
        };

        const uint8_t *rawImage8 = nullptr;
        const uint16_t *rawImage16 = nullptr;
        const uint8_t *playbackScaledImage8 = nullptr;
        uint32_t frameNumber = 0;
        uint64_t requestSerial = 0;
        OutputMode outputMode = OutputProcessed8;
        int renderedImageWidth = 0;
        int renderedImageHeight = 0;
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
        PresentationContext presentationContext;
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

    struct RenderRequest
    {
        uint32_t frameNumber = 0;
        OutputMode outputMode = OutputProcessed8;
        bool useGpuBilinearDebayer = false;
        uint64_t requestSerial = 0;
        double requestStageTime = 0.0;
        Phase3Mode phase3Mode = Phase3Mode::Disabled;
        ReadyFrame::PresentationContext presentationContext;
        PresentationPreparationOptions presentationPreparationOptions;
    };

    struct DecodeQueueEntry
    {
        int slotIndex = -1;
        RenderRequest request;
    };

    struct ReconQueueEntry
    {
        int slotIndex = -1;
        RenderRequest request;
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
                      const ReadyFrame::PresentationContext &presentationContext,
                      const PresentationPreparationOptions &presentationPreparation );
    bool isFrameReady( void );
    bool isIdle( void );
    bool acquireLatestReadyFrame( ReadyFrame *frame );
    void releasePresentedFrame( void );
    void releasePresentedFrameForRequestSerial( uint64_t requestSerial );
    bool lastFrameUsedGpuBilinearDebayer( void ) const;
    QString lastGpuBilinearFallbackReason( void ) const;
    QString lastGpuBilinearRendererDescription( void ) const;
    double lastDualIsoPreviewHistogramMilliseconds( void ) const;
    double lastDualIsoPreviewRegressionMilliseconds( void ) const;
    double lastDualIsoPreviewRowscaleMilliseconds( void ) const;
    QJsonObject lastStageTimingTelemetry( void ) const;
    double lastFrameReadyEmitStageTime( void ) const;
    void setPhase3Mode( Phase3Mode mode ) noexcept;
    Phase3Mode phase3Mode( void ) const noexcept;
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
        std::atomic<SlotState> state{ SlotState::Idle };
        Phase3Mode phase3Mode = Phase3Mode::Disabled;
        int renderedImageWidth = 0;
        int renderedImageHeight = 0;
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
        ReadyFrame::PresentationContext presentationContext;
        bool processedFrame8Active = false;
        uint64_t processedFrame8Signature = 0;
        bool processedFrame16Active = false;
        uint64_t processedFrame16Signature = 0;
        int dualIsoPattern = 0;
        int dualIsoAutoCorrection = 0;
        double dualIsoEvCorrection = 0.0;
        int dualIsoBlackDelta = 0;
        QJsonObject stageTimingTelemetry;
        RenderRequest queuedRequest;

        void resetMetadata( void )
        {
            frameNumber = 0;
            requestSerial = 0;
            outputMode = OutputProcessed8;
            ready = false;
            presenting = false;
            phase3Mode = Phase3Mode::Disabled;
            renderedImageWidth = 0;
            renderedImageHeight = 0;
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
            presentationContext = ReadyFrame::PresentationContext();
            stageTimingTelemetry = QJsonObject();
            queuedRequest = RenderRequest();
        }
    };

    mutable QMutex m_mutex;
    QWaitCondition m_waitCondition;
    static constexpr int kFrameSlotCount = 4;
    static constexpr int kRenderRequestQueueDepth = 4;
    mlvObject_t *m_pMlvObject;
    bool m_initialized;
    bool m_stop;
    bool m_renderFrame;
    bool m_renderingFrame;
    bool m_frameReady;
    std::deque<RenderRequest> m_renderRequests;
    OutputMode m_activeOutputMode;
    bool m_activeUseGpuBilinearDebayer;
    uint32_t m_activeFrameNumber;
    uint64_t m_activeFrameRequestSerial;
    ReadyFrame::PresentationContext m_activePresentationContext;
    PresentationPreparationOptions m_activePresentationPreparationOptions;
    bool m_loggedGpuBilinearSuccess;
    bool m_lastFrameUsedGpuBilinearDebayer;
    QString m_lastGpuBilinearFallbackReason;
    QString m_lastGpuBilinearRendererDescription;
    double m_lastDualIsoPreviewHistogramMs;
    double m_lastDualIsoPreviewRegressionMs;
    double m_lastDualIsoPreviewRowscaleMs;
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
    std::atomic<Phase3Mode> m_phase3Mode;
    std::array<FrameSlot, kFrameSlotCount> m_frameSlots;
    FastPlaybackScaleCache m_playbackScaleCache;
    BilinearPlaybackScaleCache m_playbackBilinearScaleCache;
    std::vector<float> m_gpuBilinearDebayerRawFrame;
    DecodeWorker *m_decodeWorker;
    ReconWorker *m_reconWorker;
    bool m_decodeWorkerStop;
    bool m_reconWorkerStop;
    std::deque<DecodeQueueEntry> m_decodeRequests;
    std::deque<ReconQueueEntry> m_reconRequests;
    std::deque<int> m_decodeReadySlots;
    std::deque<int> m_processReadySlots;
    QWaitCondition m_decodeWaitCondition;
    QWaitCondition m_reconWaitCondition;

    void run( void );
    void runSerial( void );
    void runPhase3( void );
    bool phase3DecodeAheadActive( Phase3Mode mode ) const;
    void ensureDecodeWorkerStartedLocked( void );
    void stopDecodeWorkerLocked( void );
    void ensureReconWorkerStartedLocked( void );
    void stopReconWorkerLocked( void );
    bool phase3WorkInFlightLocked( void ) const;
    void queueDecodeRequestLocked( int slotIndex, const RenderRequest &request );
    bool takeDecodeRequestForWorker( DecodeQueueEntry *entry );
    void decodeFrameForWorker( const DecodeQueueEntry &entry );
    void signalDecodeDoneFromWorker( int slotIndex );
    bool takeReconRequestForWorker( ReconQueueEntry *entry );
    void reconFrameForWorker( const ReconQueueEntry &entry, llrawprocWorkerState_t *workerState );
    void signalReconDoneFromWorker( int slotIndex );
    int waitForDecodedSlotLocked( void );
    int waitForProcessReadySlotLocked( void );
    void setupActiveRequestLocked( const RenderRequest &request, int slotIndex );
    void renderDecodedSlot( int slotIndex, const RenderRequest &request, Phase3Mode activePhase3Mode );
    void publishRenderedSlot( int slotIndex, const RenderRequest &request, Phase3Mode activePhase3Mode );
    void transitionSlotState( int slotIndex,
                              SlotState from,
                              SlotState to,
                              Phase3Mode mode,
                              uint32_t frameNumber,
                              uint64_t requestSerial,
                              const char * context );
    void emitPhase3StageTelemetry( const RenderRequest &request,
                                   const FrameSlot &slot,
                                   int slotIndex,
                                   Phase3Mode mode ) const;
    void drawFrame( int slotIndex,
                    const uint16_t *decodedRawFrame = nullptr,
                    bool decodedRawFrameAlreadyReconned = false );
    int findLatestReadySlotLocked( void ) const;
    int findFreeSlotLocked( void ) const;
    void releaseSlotLocked( int slotIndex );
    void copySlotTelemetryLocked( const FrameSlot &slot );

    friend class DecodeWorker;
    friend class ReconWorker;
};

#endif // RENDERFRAMETHREAD_H
