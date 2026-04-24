/*!
 * \file RenderFrameThread.cpp
 * \author masc4ii
 * \copyright 2017
 * \brief The render thread
 */

#include "RenderFrameThread.h"

#include "GpuDebayer.h"

#include "../../src/batch/WorkerThreadCount.h"
#include "../../src/processing/raw_processing.h"
#include "debug/StageTiming.h"
#include <QDebug>
#include <QMutexLocker>

//Constructor
RenderFrameThread::RenderFrameThread()
{
    m_stop = false;
    m_initialized = false;
    m_renderFrame = false;
    m_renderingFrame = false;
    m_frameReady = false;
    m_pMlvObject = nullptr;
    m_activeOutputMode = OutputProcessed8;
    m_activeUseGpuBilinearDebayer = false;
    m_activeFrameNumber = 0;
    m_activeFrameRequestSerial = 0;
    m_activePresentationContext = ReadyFrame::PresentationContext();
    m_activePresentationPreparationOptions = PresentationPreparationOptions();
    m_loggedGpuBilinearSuccess = false;
    m_lastFrameUsedGpuBilinearDebayer = false;
    m_lastDualIsoPreviewHistogramMs = 0.0;
    m_lastDualIsoPreviewRegressionMs = 0.0;
    m_lastDualIsoPreviewRowscaleMs = 0.0;
    m_activeFrameRequestStageTime = 0.0;
    m_lastRenderThreadQueueWaitMs = 0.0;
    m_lastRenderThreadWorkMs = 0.0;
    m_lastRenderThreadTotalMs = 0.0;
    m_lastFrameReadyEmitStageTime = 0.0;
    m_imageWidth = 0;
    m_imageHeight = 0;
    m_renderingSlotIndex = -1;
    m_presentingSlotIndex = -1;
}

//Destructor
RenderFrameThread::~RenderFrameThread()
{

}

//Init all objects
void RenderFrameThread::init(mlvObject_t *pMlvObject, int imageWidth, int imageHeight)
{
    QMutexLocker locker(&m_mutex);
    m_frameReady = false;
    m_renderRequests.clear();
    m_pMlvObject = pMlvObject;
    m_imageWidth = imageWidth;
    m_imageHeight = imageHeight;
    m_activeUseGpuBilinearDebayer = false;
    m_activeFrameNumber = 0;
    m_activeFrameRequestSerial = 0;
    m_activeOutputMode = OutputProcessed8;
    m_activePresentationContext = ReadyFrame::PresentationContext();
    m_renderingSlotIndex = -1;
    m_presentingSlotIndex = -1;
    m_lastFrameUsedGpuBilinearDebayer = false;
    m_lastGpuBilinearFallbackReason.clear();
    m_lastGpuBilinearRendererDescription.clear();
    m_lastDualIsoPreviewHistogramMs = 0.0;
    m_lastDualIsoPreviewRegressionMs = 0.0;
    m_lastDualIsoPreviewRowscaleMs = 0.0;
    m_activeFrameRequestStageTime = 0.0;
    m_lastRenderThreadQueueWaitMs = 0.0;
    m_lastRenderThreadWorkMs = 0.0;
    m_lastRenderThreadTotalMs = 0.0;
    m_lastFrameReadyEmitStageTime = 0.0;
    m_gpuBilinearDebayerRawFrame.clear();
    const size_t pixelCount =
        static_cast<size_t>(qMax(0, imageWidth)) * static_cast<size_t>(qMax(0, imageHeight));
    const size_t rgbPixelCount = pixelCount * 3u;
    for( FrameSlot &slot : m_frameSlots )
    {
        slot.rawImage8.assign( rgbPixelCount, 0u );
        slot.rawImage16.assign( rgbPixelCount, 0u );
        slot.resetMetadata();
    }
}

//Start rendering
void RenderFrameThread::renderFrame(uint32_t frameNumber,
                                    OutputMode outputMode,
                                    bool useGpuBilinearDebayer,
                                    uint64_t requestSerial,
                                    const ReadyFrame::PresentationContext &presentationContext,
                                    const PresentationPreparationOptions &presentationPreparation)
{
    QMutexLocker locker(&m_mutex);
    if( static_cast<int>(m_renderRequests.size()) >= kRenderRequestQueueDepth )
    {
        m_renderRequests.pop_front();
    }
    m_renderRequests.push_back(
        {
            frameNumber,
            outputMode,
            useGpuBilinearDebayer,
            requestSerial,
            mlv_stage_timing_now(),
            presentationContext,
            presentationPreparation
        } );
    m_renderFrame = true;
    m_waitCondition.wakeOne();
}

//Is rendering finished?
bool RenderFrameThread::isFrameReady()
{
    QMutexLocker locker(&m_mutex);
    return m_frameReady;
}

//Returns if there is a frame in the pipeline...
bool RenderFrameThread::isIdle()
{
    QMutexLocker locker(&m_mutex);
    return !(m_renderFrame || m_renderingFrame);
}

bool RenderFrameThread::acquireLatestReadyFrame(ReadyFrame *frame)
{
    QMutexLocker locker(&m_mutex);
    const int readySlotIndex = findLatestReadySlotLocked();
    if( readySlotIndex < 0 )
    {
        m_frameReady = false;
        return false;
    }

    if( m_presentingSlotIndex >= 0 )
    {
        releaseSlotLocked( m_presentingSlotIndex );
        m_presentingSlotIndex = -1;
    }

    FrameSlot &slot = m_frameSlots[readySlotIndex];
    slot.ready = false;
    slot.presenting = true;
    m_presentingSlotIndex = readySlotIndex;
    m_frameReady = (findLatestReadySlotLocked() >= 0);
    copySlotTelemetryLocked( slot );
    m_waitCondition.wakeAll();

    if( frame )
    {
        frame->rawImage8 = slot.rawImage8.empty() ? nullptr : slot.rawImage8.data();
        frame->rawImage16 = slot.rawImage16.empty() ? nullptr : slot.rawImage16.data();
        frame->playbackScaledImage8 =
            slot.playbackScaledImage8.empty() ? nullptr : slot.playbackScaledImage8.data();
        frame->frameNumber = slot.frameNumber;
        frame->requestSerial = slot.requestSerial;
        frame->outputMode = slot.outputMode;
        frame->playbackFastScaleActive = slot.playbackFastScaleActive;
        frame->playbackScaledWidth = slot.playbackScaledWidth;
        frame->playbackScaledHeight = slot.playbackScaledHeight;
        frame->usedGpuBilinearDebayer = slot.usedGpuBilinearDebayer;
        frame->gpuBilinearFallbackReason = slot.gpuBilinearFallbackReason;
        frame->gpuBilinearRendererDescription = slot.gpuBilinearRendererDescription;
        frame->dualIsoPreviewHistogramMs = slot.dualIsoPreviewHistogramMs;
        frame->dualIsoPreviewRegressionMs = slot.dualIsoPreviewRegressionMs;
        frame->dualIsoPreviewRowscaleMs = slot.dualIsoPreviewRowscaleMs;
        frame->frameReadyEmitStageTime = slot.frameReadyEmitStageTime;
        frame->processedFrame8Active = slot.processedFrame8Active;
        frame->processedFrame8Signature = slot.processedFrame8Signature;
        frame->processedFrame16Active = slot.processedFrame16Active;
        frame->processedFrame16Signature = slot.processedFrame16Signature;
        frame->dualIsoPattern = slot.dualIsoPattern;
        frame->dualIsoAutoCorrection = slot.dualIsoAutoCorrection;
        frame->dualIsoEvCorrection = slot.dualIsoEvCorrection;
        frame->dualIsoBlackDelta = slot.dualIsoBlackDelta;
        frame->presentationContext = slot.presentationContext;
        frame->stageTimingTelemetry = slot.stageTimingTelemetry;
    }
    return true;
}

void RenderFrameThread::releasePresentedFrame()
{
    QMutexLocker locker(&m_mutex);
    if( m_presentingSlotIndex >= 0 )
    {
        releaseSlotLocked( m_presentingSlotIndex );
        m_presentingSlotIndex = -1;
        m_waitCondition.wakeAll();
    }
}

bool RenderFrameThread::lastFrameUsedGpuBilinearDebayer() const
{
    return m_lastFrameUsedGpuBilinearDebayer;
}

QString RenderFrameThread::lastGpuBilinearFallbackReason() const
{
    return m_lastGpuBilinearFallbackReason;
}

QString RenderFrameThread::lastGpuBilinearRendererDescription() const
{
    return m_lastGpuBilinearRendererDescription;
}

double RenderFrameThread::lastDualIsoPreviewHistogramMilliseconds() const
{
    return m_lastDualIsoPreviewHistogramMs;
}

double RenderFrameThread::lastDualIsoPreviewRegressionMilliseconds() const
{
    return m_lastDualIsoPreviewRegressionMs;
}

double RenderFrameThread::lastDualIsoPreviewRowscaleMilliseconds() const
{
    return m_lastDualIsoPreviewRowscaleMs;
}

QJsonObject RenderFrameThread::lastStageTimingTelemetry() const
{
    return m_lastStageTimingTelemetry;
}

double RenderFrameThread::lastFrameReadyEmitStageTime() const
{
    QMutexLocker locker(&m_mutex);
    return m_lastFrameReadyEmitStageTime;
}

//Stop the thread
void RenderFrameThread::stop()
{
    QMutexLocker locker(&m_mutex);
    m_stop = true;
    m_waitCondition.wakeAll();
}

void RenderFrameThread::lock()
{
    m_mutex.lock();
    while( m_renderFrame || m_renderingFrame )
    {
        m_waitCondition.wait(&m_mutex);
    }
}

void RenderFrameThread::unlock()
{
    m_mutex.unlock();
}

//Main loop of the thread
void RenderFrameThread::run(void)
{
    m_mutex.lock();
    while( !m_stop )
    {
        while( !m_stop
            && ( m_renderRequests.empty() || findFreeSlotLocked() < 0 ) )
        {
            m_waitCondition.wait(&m_mutex);
        }
        if( m_stop ) break;

        const int slotIndex = findFreeSlotLocked();
        if( slotIndex < 0 )
        {
            continue;
        }

        const RenderRequest request = m_renderRequests.front();
        m_renderRequests.pop_front();
        m_renderFrame = !m_renderRequests.empty();
        m_activeFrameNumber = request.frameNumber;
        m_activeOutputMode = request.outputMode;
        m_activeUseGpuBilinearDebayer = request.useGpuBilinearDebayer;
        m_activeFrameRequestSerial = request.requestSerial;
        m_activeFrameRequestStageTime = request.requestStageTime;
        m_activePresentationContext = request.presentationContext;
        m_activePresentationPreparationOptions = request.presentationPreparationOptions;
        m_renderingFrame = true;
        m_renderingSlotIndex = slotIndex;
        m_mutex.unlock();
        drawFrame( slotIndex );
        m_mutex.lock();
        m_renderingFrame = false;
        m_renderingSlotIndex = -1;
        m_frameSlots[slotIndex].ready = true;
        m_frameReady = true;
        m_waitCondition.wakeAll();
        m_mutex.unlock();
        emit frameReady();
        m_mutex.lock();
    }
    m_stop = false;
    m_mutex.unlock();
}

int RenderFrameThread::findLatestReadySlotLocked() const
{
    int readySlotIndex = -1;
    uint64_t latestRequestSerial = 0;
    for( int i = 0; i < static_cast<int>(m_frameSlots.size()); ++i )
    {
        const FrameSlot &slot = m_frameSlots[i];
        if( !slot.ready ) continue;
        if( readySlotIndex < 0 || slot.requestSerial >= latestRequestSerial )
        {
            readySlotIndex = i;
            latestRequestSerial = slot.requestSerial;
        }
    }
    return readySlotIndex;
}

int RenderFrameThread::findFreeSlotLocked() const
{
    for( int i = 0; i < static_cast<int>(m_frameSlots.size()); ++i )
    {
        const FrameSlot &slot = m_frameSlots[i];
        if( i == m_renderingSlotIndex ) continue;
        if( slot.ready || slot.presenting ) continue;
        return i;
    }
    return -1;
}

void RenderFrameThread::releaseSlotLocked( int slotIndex )
{
    if( slotIndex < 0 || slotIndex >= static_cast<int>(m_frameSlots.size()) ) return;
    FrameSlot &slot = m_frameSlots[slotIndex];
    slot.ready = false;
    slot.presenting = false;
}

void RenderFrameThread::copySlotTelemetryLocked( const FrameSlot &slot )
{
    m_lastFrameUsedGpuBilinearDebayer = slot.usedGpuBilinearDebayer;
    m_lastGpuBilinearFallbackReason = slot.gpuBilinearFallbackReason;
    m_lastGpuBilinearRendererDescription = slot.gpuBilinearRendererDescription;
    m_lastDualIsoPreviewHistogramMs = slot.dualIsoPreviewHistogramMs;
    m_lastDualIsoPreviewRegressionMs = slot.dualIsoPreviewRegressionMs;
    m_lastDualIsoPreviewRowscaleMs = slot.dualIsoPreviewRowscaleMs;
    m_lastFrameReadyEmitStageTime = slot.frameReadyEmitStageTime;
    m_lastStageTimingTelemetry = slot.stageTimingTelemetry;
    m_lastRenderThreadQueueWaitMs =
        slot.stageTimingTelemetry.value( QStringLiteral("render_thread_queue_wait_ms") ).toDouble();
    m_lastRenderThreadWorkMs =
        slot.stageTimingTelemetry.value( QStringLiteral("render_thread_work_ms") ).toDouble();
    m_lastRenderThreadTotalMs =
        slot.stageTimingTelemetry.value( QStringLiteral("render_thread_total_ms") ).toDouble();
}

//render the picture
void RenderFrameThread::drawFrame( int slotIndex )
{
    FrameSlot &slot = m_frameSlots[slotIndex];
    slot.resetMetadata();
    slot.frameNumber = m_activeFrameNumber;
    slot.requestSerial = m_activeFrameRequestSerial;
    slot.outputMode = m_activeOutputMode;
    slot.presentationContext = m_activePresentationContext;

    const double render_start = mlv_stage_timing_now();
    const uint32_t frameNumber = slot.frameNumber;
    const OutputMode outputMode = slot.outputMode;
    const bool useGpuBilinearDebayer = m_activeUseGpuBilinearDebayer;
    const double frameRequestStageTime = m_activeFrameRequestStageTime;
    const double renderThreadQueueWaitMs =
        (frameRequestStageTime > 0.0 && render_start >= frameRequestStageTime)
            ? (render_start - frameRequestStageTime) * 1000.0
            : 0.0;

    mlv_stage_timing_reset_snapshot();
    if ( !useGpuBilinearDebayer )
    {
        slot.gpuBilinearFallbackReason.clear();
    }

    if ( outputMode == OutputProcessed16 && !slot.rawImage16.empty() )
    {
        getMlvProcessedFrame16( m_pMlvObject,
                                frameNumber,
                                slot.rawImage16.data(),
                                mlvappEffectiveWorkerThreadCount() );
        slot.dualIsoPreviewHistogramMs = llrpGetLastDualIsoPreviewHistogramMilliseconds();
        slot.dualIsoPreviewRegressionMs = llrpGetLastDualIsoPreviewRegressionMilliseconds();
        slot.dualIsoPreviewRowscaleMs = llrpGetLastDualIsoPreviewRowscaleMilliseconds();
        mlv_stage_timing_note("render_thread_draw16", frameNumber, render_start);
    }
    else if ( outputMode == OutputDebayered16 && !slot.rawImage16.empty() )
    {
        bool usedGpuBilinearDebayer = false;
        bool renderedDebayeredFrame = false;
        if ( useGpuBilinearDebayer && m_pMlvObject )
        {
            const int width = getMlvWidth( m_pMlvObject );
            const int height = getMlvHeight( m_pMlvObject );
            const size_t pixelCount = static_cast<size_t>(width) * static_cast<size_t>(height);
            m_gpuBilinearDebayerRawFrame.resize( pixelCount );
            getMlvRawFrameFloat( m_pMlvObject,
                                 frameNumber,
                                 m_gpuBilinearDebayerRawFrame.data() );

            QString gpuReason;
            QString rendererDescription;
            usedGpuBilinearDebayer =
                gpuBilinearDebayerApplyGpuOffscreen( m_gpuBilinearDebayerRawFrame.data(),
                                                     slot.rawImage16.data(),
                                                     width,
                                                     height,
                                                     &gpuReason,
                                                     &rendererDescription );
            if ( usedGpuBilinearDebayer )
            {
                renderedDebayeredFrame = true;
                slot.usedGpuBilinearDebayer = true;
                slot.gpuBilinearFallbackReason.clear();
                slot.gpuBilinearRendererDescription = rendererDescription;
                if ( !m_loggedGpuBilinearSuccess )
                {
                    qInfo() << "Experimental GPU bilinear debayer enabled for the debayered-16 preview path"
                            << "(renderer:"
                            << (rendererDescription.isEmpty() ? QStringLiteral("unknown") : rendererDescription)
                            << ").";
                    m_loggedGpuBilinearSuccess = true;
                }
            }
            else
            {
                debayerBasic( slot.rawImage16.data(),
                              m_gpuBilinearDebayerRawFrame.data(),
                              width,
                              height,
                              1 );
                renderedDebayeredFrame = true;
                slot.usedGpuBilinearDebayer = false;
                slot.gpuBilinearRendererDescription = rendererDescription;
                const QString previousFallbackReason = m_lastGpuBilinearFallbackReason;
                if ( !gpuReason.isEmpty()
                  && gpuReason != previousFallbackReason )
                {
                    qWarning().nospace()
                        << "Experimental GPU bilinear debayer fell back to CPU: "
                        << gpuReason
                        << " (renderer="
                        << (rendererDescription.isEmpty() ? QStringLiteral("unknown") : rendererDescription)
                        << ").";
                }
                slot.gpuBilinearFallbackReason = gpuReason;
            }
        }

        if ( !renderedDebayeredFrame )
        {
            getMlvRawFrameDebayered( m_pMlvObject, frameNumber, slot.rawImage16.data() );
        }
        mlv_stage_timing_note("render_thread_draw16_debayered", frameNumber, render_start);
    }
    else if( !slot.rawImage8.empty() )
    {
        getMlvProcessedFrame8( m_pMlvObject,
                               frameNumber,
                               slot.rawImage8.data(),
                               mlvappEffectiveWorkerThreadCount() );
        slot.dualIsoPreviewHistogramMs = llrpGetLastDualIsoPreviewHistogramMilliseconds();
        slot.dualIsoPreviewRegressionMs = llrpGetLastDualIsoPreviewRegressionMilliseconds();
        slot.dualIsoPreviewRowscaleMs = llrpGetLastDualIsoPreviewRowscaleMilliseconds();
        mlv_stage_timing_note("render_thread_draw", frameNumber, render_start);
    }

    slot.playbackFastScaleActive = false;
    slot.playbackScaledWidth = 0;
    slot.playbackScaledHeight = 0;
    if( outputMode == OutputProcessed8
     && m_activePresentationPreparationOptions.fastPlaybackScale
     && !slot.rawImage8.empty()
     && m_activePresentationPreparationOptions.targetWidth > 0
     && m_activePresentationPreparationOptions.targetHeight > 0 )
    {
        const double playbackScaleStart = mlv_stage_timing_now();
        slot.playbackFastScaleActive =
            playbackBuildFastScaledRgb8( slot.rawImage8.data(),
                                         m_imageWidth,
                                         m_imageHeight,
                                         m_activePresentationPreparationOptions.targetWidth,
                                         m_activePresentationPreparationOptions.targetHeight,
                                         slot.playbackScaledImage8,
                                         m_playbackScaleCache );
        if( slot.playbackFastScaleActive )
        {
            slot.playbackScaledWidth = m_activePresentationPreparationOptions.targetWidth;
            slot.playbackScaledHeight = m_activePresentationPreparationOptions.targetHeight;
        }
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_active"),
                                          slot.playbackFastScaleActive );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_ms"),
                                          (mlv_stage_timing_now() - playbackScaleStart) * 1000.0 );
    }
    else
    {
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_active"),
                                          false );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_ms"),
                                          0.0 );
    }

    const double rawUint16Ms = getMlvLastRawUint16Milliseconds();
    const double rawUint16DiskReadMs = getMlvLastRawUint16DiskReadMilliseconds();
    const double rawUint16DecompressMs = getMlvLastRawUint16DecompressMilliseconds();
    const double rawUint16DecompressPrepareMs =
        getMlvLastRawUint16DecompressPrepareMilliseconds();
    const double rawUint16DecompressExecuteMs =
        getMlvLastRawUint16DecompressExecuteMilliseconds();
    const int rawUint16Lj92Pred6SplitActive =
        getMlvLastRawUint16Lj92Pred6SplitActive();
    const int rawUint16Lj92Pred6SplitRequested =
        getMlvLastRawUint16Lj92Pred6SplitRequested();
    const int rawUint16Lj92GenericSplitActive =
        getMlvLastRawUint16Lj92GenericSplitActive();
    const int rawUint16Lj92GenericSplitRequested =
        getMlvLastRawUint16Lj92GenericSplitRequested();
    const int rawUint16Lj92Pred1FastPathActive =
        getMlvLastRawUint16Lj92Pred1FastPathActive();
    const int rawUint16Lj92Pred1FastPathMeasurementRequested =
        getMlvLastRawUint16Lj92Pred1FastPathMeasurementRequested();
    const int rawUint16Lj92Pred1FastPathMeasurementActive =
        getMlvLastRawUint16Lj92Pred1FastPathMeasurementActive();
    const int rawUint16Lj92Pred1FastPathEligible =
        getMlvLastRawUint16Lj92Pred1FastPathEligible();
    const int rawUint16Lj92ScanComponentCount =
        getMlvLastRawUint16Lj92ScanComponentCount();
    const int rawUint16Lj92WriteLength =
        getMlvLastRawUint16Lj92WriteLength();
    const int rawUint16Lj92ExpectedWriteLength =
        getMlvLastRawUint16Lj92ExpectedWriteLength();
    const int rawUint16Lj92SkipLength =
        getMlvLastRawUint16Lj92SkipLength();
    const int rawUint16Lj92LinearizeActive =
        getMlvLastRawUint16Lj92LinearizeActive();
    const int rawUint16Lj92ComponentCount =
        getMlvLastRawUint16Lj92ComponentCount();
    const int rawUint16Lj92Predictor =
        getMlvLastRawUint16Lj92Predictor();
    const double rawUint16Lj92Pred6TotalMs =
        getMlvLastRawUint16Lj92Pred6TotalMilliseconds();
    const double rawUint16Lj92Pred6BitstreamMs =
        getMlvLastRawUint16Lj92Pred6BitstreamMilliseconds();
    const double rawUint16Lj92Pred6PredictorMs =
        getMlvLastRawUint16Lj92Pred6PredictorMilliseconds();
    const double rawUint16Lj92GenericTotalMs =
        getMlvLastRawUint16Lj92GenericTotalMilliseconds();
    const double rawUint16Lj92GenericBitstreamMs =
        getMlvLastRawUint16Lj92GenericBitstreamMilliseconds();
    const double rawUint16Lj92GenericPredictorMs =
        getMlvLastRawUint16Lj92GenericPredictorMilliseconds();
    const double rawUint16Lj92Pred1FastPathTotalMs =
        getMlvLastRawUint16Lj92Pred1FastPathTotalMilliseconds();
    const double rawUint16Lj92Pred1FastPathBitstreamMs =
        getMlvLastRawUint16Lj92Pred1FastPathBitstreamMilliseconds();
    const double rawUint16Lj92Pred1FastPathPredictorMs =
        getMlvLastRawUint16Lj92Pred1FastPathPredictorMilliseconds();
    const double rawUint16UnpackMs = getMlvLastRawUint16UnpackMilliseconds();
    const double rawUint16CopyMs = getMlvLastRawUint16CopyMilliseconds();
    const int rawUint16PrefetchHit = getMlvLastRawUint16PrefetchHit();
    const double llrawprocMs = getMlvLastLlrawprocMilliseconds();
    const double llrawprocDarkFrameMs = llrpGetLastDarkFrameMilliseconds();
    const double llrawprocVerticalStripesMs = llrpGetLastVerticalStripesMilliseconds();
    const double llrawprocFocusPixelsMs = llrpGetLastFocusPixelsMilliseconds();
    const double llrawprocBadPixelsMs = llrpGetLastBadPixelsMilliseconds();
    const double llrawprocPatternNoiseMs = llrpGetLastPatternNoiseMilliseconds();
    const double llrawprocDualIsoMs = llrpGetLastDualIsoMilliseconds();
    const double llrawprocChromaSmoothMs = llrpGetLastChromaSmoothMilliseconds();
    const double llrawprocKnownMs =
        llrawprocDarkFrameMs +
        llrawprocVerticalStripesMs +
        llrawprocFocusPixelsMs +
        llrawprocBadPixelsMs +
        llrawprocPatternNoiseMs +
        llrawprocDualIsoMs +
        llrawprocChromaSmoothMs;
    const double llrawprocOtherMs = qMax( 0.0, llrawprocMs - llrawprocKnownMs );
    const double dualIsoPreviewTotalMs =
        slot.dualIsoPreviewHistogramMs +
        slot.dualIsoPreviewRegressionMs +
        slot.dualIsoPreviewRowscaleMs;
    const double rawFloatConvertMs = getMlvLastRawFloatConvertMilliseconds();
    const double debayerWbPrepareMs = getMlvLastDebayerWbPrepareMilliseconds();
    const double debayerCaMs = getMlvLastDebayerCaMilliseconds();
    const double debayerKernelMs = getMlvLastDebayerKernelMilliseconds();
    const double debayerWbUndoMs = getMlvLastDebayerWbUndoMilliseconds();
    const double debayerExclusiveMs = qMax( 0.0,
                                            getMlvLastDebayeredFrameMilliseconds()
                                                - rawUint16Ms
                                                - llrawprocMs );
    const double debayerKnownMs =
        rawFloatConvertMs +
        debayerWbPrepareMs +
        debayerCaMs +
        debayerKernelMs +
        debayerWbUndoMs;
    const double debayerPipelineOtherMs =
        qMax( 0.0, debayerExclusiveMs - debayerKnownMs );
    const double rawUint16KnownMs =
        rawUint16DiskReadMs +
        rawUint16DecompressMs +
        rawUint16UnpackMs +
        rawUint16CopyMs;
    const double rawUint16OtherMs =
        qMax( 0.0, rawUint16Ms - rawUint16KnownMs );
    const double rawUint16Lj92Pred6OtherMs =
        qMax( 0.0,
              rawUint16Lj92Pred6TotalMs
                - rawUint16Lj92Pred6BitstreamMs
                - rawUint16Lj92Pred6PredictorMs );
    const double rawUint16Lj92GenericOtherMs =
        qMax( 0.0,
              rawUint16Lj92GenericTotalMs
                - rawUint16Lj92GenericBitstreamMs
                - rawUint16Lj92GenericPredictorMs );
    const double rawUint16Lj92Pred1FastPathOtherMs =
        qMax( 0.0,
              rawUint16Lj92Pred1FastPathTotalMs
                - rawUint16Lj92Pred1FastPathBitstreamMs
                - rawUint16Lj92Pred1FastPathPredictorMs );
    const double processingMs = getMlvLastProcessingMilliseconds();
    const double processingSetupMs = processingGetLastSetupMilliseconds();
    const double processingShadowsHighlightsPrepMs =
        processingGetLastShadowsHighlightsPrepMilliseconds();
    const double processingHighestGreenMs =
        processingGetLastHighestGreenMilliseconds();
    const double processingCoreMs = processingGetLastCoreMilliseconds();
    const double processingDenoiseMs = processingGetLastDenoiseMilliseconds();
    const double processingRbfMs = processingGetLastRbfMilliseconds();
    const double processingCaMs = processingGetLastCaMilliseconds();
    const double processingKnownMs =
        processingSetupMs +
        processingShadowsHighlightsPrepMs +
        processingHighestGreenMs +
        processingCoreMs +
        processingDenoiseMs +
        processingRbfMs +
        processingCaMs;
    const double processingOtherMs = qMax( 0.0, processingMs - processingKnownMs );
    const double processingCoreLevelsMs =
        processingGetLastCoreLevelsMilliseconds();
    const double processingCoreColorMs =
        processingGetLastCoreColorMilliseconds();
    const double processingDirect8MatrixMs =
        processingGetLastDirect8MatrixMilliseconds();
    const double processingDirect8GammaMs =
        processingGetLastDirect8GammaMilliseconds();
    const double processingDirect8CurvesMs =
        processingGetLastDirect8CurvesMilliseconds();
    const double processingCoreCreativeMs =
        processingGetLastCoreCreativeMilliseconds();
    const double processingCoreOutputMs =
        processingGetLastCoreOutputMilliseconds();
    const double processingCoreKnownMs =
        processingCoreLevelsMs +
        processingCoreColorMs +
        processingCoreCreativeMs +
        processingCoreOutputMs;
    const double processingCoreOtherMs =
        qMax( 0.0, processingCoreMs - processingCoreKnownMs );

    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_ms"),
                                      rawUint16Ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_disk_read_ms"),
                                      rawUint16DiskReadMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_decompress_ms"),
                                      rawUint16DecompressMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_decompress_prepare_ms"),
                                      rawUint16DecompressPrepareMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_decompress_execute_ms"),
                                      rawUint16DecompressExecuteMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_split_active"),
                                      rawUint16Lj92Pred6SplitActive != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_split_requested"),
                                      rawUint16Lj92Pred6SplitRequested != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_split_active"),
                                      rawUint16Lj92GenericSplitActive != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_split_requested"),
                                      rawUint16Lj92GenericSplitRequested != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_active"),
                                      rawUint16Lj92Pred1FastPathActive != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_requested"),
                                      rawUint16Lj92Pred1FastPathMeasurementRequested != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_active"),
                                      rawUint16Lj92Pred1FastPathMeasurementActive != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_eligible"),
                                      rawUint16Lj92Pred1FastPathEligible != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_scan_component_count"),
                                      rawUint16Lj92ScanComponentCount );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_component_count"),
                                      rawUint16Lj92ComponentCount );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_write_length"),
                                      rawUint16Lj92WriteLength );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_expected_write_length"),
                                      rawUint16Lj92ExpectedWriteLength );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_skip_length"),
                                      rawUint16Lj92SkipLength );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_linearize_active"),
                                      rawUint16Lj92LinearizeActive != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_predictor"),
                                      rawUint16Lj92Predictor );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_total_ms"),
                                      rawUint16Lj92Pred6TotalMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_bitstream_ms"),
                                      rawUint16Lj92Pred6BitstreamMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_predictor_ms"),
                                      rawUint16Lj92Pred6PredictorMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_other_ms"),
                                      rawUint16Lj92Pred6OtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_total_ms"),
                                      rawUint16Lj92GenericTotalMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_bitstream_ms"),
                                      rawUint16Lj92GenericBitstreamMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_predictor_ms"),
                                      rawUint16Lj92GenericPredictorMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_other_ms"),
                                      rawUint16Lj92GenericOtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_total_ms"),
                                      rawUint16Lj92Pred1FastPathTotalMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_bitstream_ms"),
                                      rawUint16Lj92Pred1FastPathBitstreamMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_predictor_ms"),
                                      rawUint16Lj92Pred1FastPathPredictorMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_other_ms"),
                                      rawUint16Lj92Pred1FastPathOtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_unpack_ms"),
                                      rawUint16UnpackMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_copy_ms"),
                                      rawUint16CopyMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_prefetch_hit"),
                                      rawUint16PrefetchHit != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_other_ms"),
                                      rawUint16OtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_ms"),
                                      llrawprocMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_total_ms"),
                                      llrpGetLastTotalMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_dark_frame_ms"),
                                      llrawprocDarkFrameMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_vertical_stripes_ms"),
                                      llrawprocVerticalStripesMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_focus_pixels_ms"),
                                      llrawprocFocusPixelsMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_bad_pixels_ms"),
                                      llrawprocBadPixelsMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_pattern_noise_ms"),
                                      llrawprocPatternNoiseMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_dual_iso_ms"),
                                      llrawprocDualIsoMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_chroma_smooth_ms"),
                                      llrawprocChromaSmoothMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_other_ms"),
                                      llrawprocOtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_preview_total_ms"),
                                      dualIsoPreviewTotalMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_preview_histogram_ms"),
                                      slot.dualIsoPreviewHistogramMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_preview_regression_ms"),
                                      slot.dualIsoPreviewRegressionMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_preview_rowscale_ms"),
                                      slot.dualIsoPreviewRowscaleMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayered_frame_ms"),
                                      getMlvLastDebayeredFrameMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_float_convert_ms"),
                                      rawFloatConvertMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_exclusive_ms"),
                                      debayerExclusiveMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_wb_prepare_ms"),
                                      debayerWbPrepareMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_ca_ms"),
                                      debayerCaMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_kernel_ms"),
                                      debayerKernelMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_wb_undo_ms"),
                                      debayerWbUndoMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_pipeline_other_ms"),
                                      debayerPipelineOtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_ms"),
                                      processingMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_setup_ms"),
                                      processingSetupMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_prep_ms"),
                                      processingShadowsHighlightsPrepMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_highest_green_ms"),
                                      processingHighestGreenMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_ms"),
                                      processingCoreMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_denoise_ms"),
                                      processingDenoiseMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_rbf_ms"),
                                      processingRbfMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_ca_ms"),
                                      processingCaMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_other_ms"),
                                      processingOtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_levels_ms"),
                                      processingCoreLevelsMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_ms"),
                                      processingCoreColorMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_direct8_matrix_ms"),
                                      processingDirect8MatrixMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_direct8_gamma_ms"),
                                      processingDirect8GammaMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_direct8_curves_ms"),
                                      processingDirect8CurvesMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_ms"),
                                      processingCoreCreativeMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_output_ms"),
                                      processingCoreOutputMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_other_ms"),
                                      processingCoreOtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed16_total_ms"),
                                      getMlvLastProcessed16TotalMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed16_for_8bit_ms"),
                                      getMlvLastProcessed16For8BitMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed16_to_8bit_ms"),
                                      getMlvLastProcessed16To8BitMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_total_ms"),
                                      getMlvLastProcessed8TotalMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_direct_path_active"),
                                      getMlvLastProcessed8DirectPathActive() != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_prefetch_hit"),
                                      getMlvLastProcessed8PrefetchHit() != 0 );

    slot.processedFrame8Active =
        m_pMlvObject && m_pMlvObject->current_processed_frame_8bit_active;
    slot.processedFrame8Signature =
        m_pMlvObject ? m_pMlvObject->current_processed_frame_8bit_signature : 0;
    slot.processedFrame16Active =
        m_pMlvObject && m_pMlvObject->current_processed_frame_active;
    slot.processedFrame16Signature =
        m_pMlvObject ? m_pMlvObject->current_processed_frame_signature : 0;
    if( m_pMlvObject && m_pMlvObject->llrawproc )
    {
        slot.dualIsoPattern = m_pMlvObject->llrawproc->diso_pattern;
        slot.dualIsoAutoCorrection = m_pMlvObject->llrawproc->diso_auto_correction;
        slot.dualIsoEvCorrection = m_pMlvObject->llrawproc->diso_ev_correction;
        slot.dualIsoBlackDelta = m_pMlvObject->llrawproc->diso_black_delta;
    }

    const double renderThreadWorkMs = (mlv_stage_timing_now() - render_start) * 1000.0;
    const double renderThreadTotalMs =
        (frameRequestStageTime > 0.0 && mlv_stage_timing_now() >= frameRequestStageTime)
            ? (mlv_stage_timing_now() - frameRequestStageTime) * 1000.0
            : renderThreadWorkMs;
    slot.frameReadyEmitStageTime = mlv_stage_timing_now();
    slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_queue_wait_ms"),
                                      renderThreadQueueWaitMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_work_ms"),
                                      renderThreadWorkMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_total_ms"),
                                      renderThreadTotalMs );
}
