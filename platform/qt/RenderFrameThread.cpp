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
    m_frameReady = false;
    m_pRawImage16 = nullptr;
    m_outputMode = OutputProcessed8;
    m_useGpuBilinearDebayer = false;
    m_loggedGpuBilinearSuccess = false;
    m_lastFrameUsedGpuBilinearDebayer = false;
    m_lastDualIsoPreviewHistogramMs = 0.0;
    m_lastDualIsoPreviewRegressionMs = 0.0;
    m_lastDualIsoPreviewRowscaleMs = 0.0;
    m_frameRequestStageTime = 0.0;
    m_lastRenderThreadQueueWaitMs = 0.0;
    m_lastRenderThreadWorkMs = 0.0;
    m_lastRenderThreadTotalMs = 0.0;
    m_lastFrameReadyEmitStageTime = 0.0;
}

//Destructor
RenderFrameThread::~RenderFrameThread()
{

}

//Init all objects
void RenderFrameThread::init(mlvObject_t *pMlvObject, uint8_t *pRawImage, uint16_t *pRawImage16)
{
    m_mutex.lock();
    m_frameReady = false;
    m_pMlvObject = pMlvObject;
    m_pRawImage = pRawImage;
    m_pRawImage16 = pRawImage16;
    m_useGpuBilinearDebayer = false;
    m_lastFrameUsedGpuBilinearDebayer = false;
    m_lastGpuBilinearFallbackReason.clear();
    m_lastGpuBilinearRendererDescription.clear();
    m_lastDualIsoPreviewHistogramMs = 0.0;
    m_lastDualIsoPreviewRegressionMs = 0.0;
    m_lastDualIsoPreviewRowscaleMs = 0.0;
    m_frameRequestStageTime = 0.0;
    m_lastRenderThreadQueueWaitMs = 0.0;
    m_lastRenderThreadWorkMs = 0.0;
    m_lastRenderThreadTotalMs = 0.0;
    m_lastFrameReadyEmitStageTime = 0.0;
    m_gpuBilinearDebayerRawFrame.clear();
    m_mutex.unlock();
}

//Start rendering
void RenderFrameThread::renderFrame(uint32_t frameNumber,
                                    OutputMode outputMode,
                                    bool useGpuBilinearDebayer)
{
    m_mutex.lock();
    m_frameNumber = frameNumber;
    m_outputMode = outputMode;
    m_useGpuBilinearDebayer = useGpuBilinearDebayer;
    m_renderFrame = true;
    m_frameReady = false;
    m_frameRequestStageTime = mlv_stage_timing_now();
    m_mutex.unlock();
}

//Is rendering finished?
bool RenderFrameThread::isFrameReady()
{
    m_mutex.lock();
    bool retVal = m_frameReady;
    m_mutex.unlock();
    return retVal;
}

//Returns if there is a frame in the pipeline...
bool RenderFrameThread::isIdle()
{
    m_mutex.lock();
    bool retVal = m_renderFrame;
    m_mutex.unlock();
    return !retVal;
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
    m_mutex.lock();
    m_stop = true;
    m_mutex.unlock();
    this->thread()->quit();
}

//Main loop of the thread
void RenderFrameThread::run(void)
{
    m_mutex.lock();
    while( !m_stop )
    {
        if( m_renderFrame )
        {
            drawFrame();
            m_renderFrame = false;
            m_frameReady = true;
        }
        m_mutex.unlock();
        msleep(1);
        m_mutex.lock();
    }
    m_stop = false;
    m_mutex.unlock();
}

//render the picture
void RenderFrameThread::drawFrame()
{
    const double render_start = mlv_stage_timing_now();
    m_lastRenderThreadQueueWaitMs =
        (m_frameRequestStageTime > 0.0 && render_start >= m_frameRequestStageTime)
            ? (render_start - m_frameRequestStageTime) * 1000.0
            : 0.0;
    mlv_stage_timing_reset_snapshot();
    m_lastFrameUsedGpuBilinearDebayer = false;
    m_lastGpuBilinearRendererDescription.clear();
    m_lastDualIsoPreviewHistogramMs = 0.0;
    m_lastDualIsoPreviewRegressionMs = 0.0;
    m_lastDualIsoPreviewRowscaleMs = 0.0;
    m_lastRenderThreadWorkMs = 0.0;
    m_lastRenderThreadTotalMs = 0.0;
    m_lastFrameReadyEmitStageTime = 0.0;
    m_lastStageTimingTelemetry = QJsonObject();
    if ( !m_useGpuBilinearDebayer )
    {
        m_lastGpuBilinearFallbackReason.clear();
    }
    if ( m_outputMode == OutputProcessed16 && m_pRawImage16 )
    {
        getMlvProcessedFrame16( m_pMlvObject, m_frameNumber, m_pRawImage16, mlvappEffectiveWorkerThreadCount() );
        m_lastDualIsoPreviewHistogramMs = llrpGetLastDualIsoPreviewHistogramMilliseconds();
        m_lastDualIsoPreviewRegressionMs = llrpGetLastDualIsoPreviewRegressionMilliseconds();
        m_lastDualIsoPreviewRowscaleMs = llrpGetLastDualIsoPreviewRowscaleMilliseconds();
        mlv_stage_timing_note("render_thread_draw16", m_frameNumber, render_start);
    }
    else if ( m_outputMode == OutputDebayered16 && m_pRawImage16 )
    {
        bool usedGpuBilinearDebayer = false;
        bool renderedDebayeredFrame = false;
        if ( m_useGpuBilinearDebayer && m_pMlvObject )
        {
            const int width = getMlvWidth( m_pMlvObject );
            const int height = getMlvHeight( m_pMlvObject );
            const size_t pixelCount = static_cast<size_t>(width) * static_cast<size_t>(height);
            m_gpuBilinearDebayerRawFrame.resize( pixelCount );
            getMlvRawFrameFloat( m_pMlvObject,
                                 m_frameNumber,
                                 m_gpuBilinearDebayerRawFrame.data() );

            QString gpuReason;
            QString rendererDescription;
            usedGpuBilinearDebayer =
                gpuBilinearDebayerApplyGpuOffscreen( m_gpuBilinearDebayerRawFrame.data(),
                                                     m_pRawImage16,
                                                     width,
                                                     height,
                                                     &gpuReason,
                                                     &rendererDescription );
            if ( usedGpuBilinearDebayer )
            {
                renderedDebayeredFrame = true;
                m_lastFrameUsedGpuBilinearDebayer = true;
                m_lastGpuBilinearFallbackReason.clear();
                m_lastGpuBilinearRendererDescription = rendererDescription;
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
                debayerBasic( m_pRawImage16,
                              m_gpuBilinearDebayerRawFrame.data(),
                              width,
                              height,
                              1 );
                renderedDebayeredFrame = true;
                m_lastFrameUsedGpuBilinearDebayer = false;
                m_lastGpuBilinearRendererDescription = rendererDescription;
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
                m_lastGpuBilinearFallbackReason = gpuReason;
            }
        }

        if ( !renderedDebayeredFrame )
        {
            getMlvRawFrameDebayered( m_pMlvObject, m_frameNumber, m_pRawImage16 );
        }
        mlv_stage_timing_note("render_thread_draw16_debayered", m_frameNumber, render_start);
    }
    else
    {
        getMlvProcessedFrame8( m_pMlvObject, m_frameNumber, m_pRawImage, mlvappEffectiveWorkerThreadCount() );
        m_lastDualIsoPreviewHistogramMs = llrpGetLastDualIsoPreviewHistogramMilliseconds();
        m_lastDualIsoPreviewRegressionMs = llrpGetLastDualIsoPreviewRegressionMilliseconds();
        m_lastDualIsoPreviewRowscaleMs = llrpGetLastDualIsoPreviewRowscaleMilliseconds();
        mlv_stage_timing_note("render_thread_draw", m_frameNumber, render_start);
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
        m_lastDualIsoPreviewHistogramMs +
        m_lastDualIsoPreviewRegressionMs +
        m_lastDualIsoPreviewRowscaleMs;
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

    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_ms"),
                                       rawUint16Ms );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_disk_read_ms"),
                                       rawUint16DiskReadMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_decompress_ms"),
                                       rawUint16DecompressMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_decompress_prepare_ms"),
                                       rawUint16DecompressPrepareMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_decompress_execute_ms"),
                                       rawUint16DecompressExecuteMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_split_active"),
                                       rawUint16Lj92Pred6SplitActive != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_split_requested"),
                                       rawUint16Lj92Pred6SplitRequested != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_split_active"),
                                       rawUint16Lj92GenericSplitActive != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_split_requested"),
                                       rawUint16Lj92GenericSplitRequested != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_active"),
                                       rawUint16Lj92Pred1FastPathActive != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_requested"),
                                       rawUint16Lj92Pred1FastPathMeasurementRequested != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_active"),
                                       rawUint16Lj92Pred1FastPathMeasurementActive != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_eligible"),
                                       rawUint16Lj92Pred1FastPathEligible != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_scan_component_count"),
                                       rawUint16Lj92ScanComponentCount );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_component_count"),
                                       rawUint16Lj92ComponentCount );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_write_length"),
                                       rawUint16Lj92WriteLength );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_expected_write_length"),
                                       rawUint16Lj92ExpectedWriteLength );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_skip_length"),
                                       rawUint16Lj92SkipLength );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_linearize_active"),
                                       rawUint16Lj92LinearizeActive != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_predictor"),
                                       rawUint16Lj92Predictor );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_total_ms"),
                                       rawUint16Lj92Pred6TotalMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_bitstream_ms"),
                                       rawUint16Lj92Pred6BitstreamMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_predictor_ms"),
                                       rawUint16Lj92Pred6PredictorMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred6_other_ms"),
                                       rawUint16Lj92Pred6OtherMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_total_ms"),
                                       rawUint16Lj92GenericTotalMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_bitstream_ms"),
                                       rawUint16Lj92GenericBitstreamMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_predictor_ms"),
                                       rawUint16Lj92GenericPredictorMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_generic_other_ms"),
                                       rawUint16Lj92GenericOtherMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_total_ms"),
                                       rawUint16Lj92Pred1FastPathTotalMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_bitstream_ms"),
                                       rawUint16Lj92Pred1FastPathBitstreamMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_predictor_ms"),
                                       rawUint16Lj92Pred1FastPathPredictorMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_lj92_pred1_fast_path_other_ms"),
                                       rawUint16Lj92Pred1FastPathOtherMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_unpack_ms"),
                                       rawUint16UnpackMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_copy_ms"),
                                       rawUint16CopyMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_prefetch_hit"),
                                       rawUint16PrefetchHit != 0 );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_uint16_other_ms"),
                                       rawUint16OtherMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_ms"),
                                       llrawprocMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_total_ms"),
                                       llrpGetLastTotalMilliseconds() );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_dark_frame_ms"),
                                       llrawprocDarkFrameMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_vertical_stripes_ms"),
                                       llrawprocVerticalStripesMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_focus_pixels_ms"),
                                       llrawprocFocusPixelsMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_bad_pixels_ms"),
                                       llrawprocBadPixelsMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_pattern_noise_ms"),
                                       llrawprocPatternNoiseMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_dual_iso_ms"),
                                       llrawprocDualIsoMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_chroma_smooth_ms"),
                                       llrawprocChromaSmoothMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("llrawproc_other_ms"),
                                       llrawprocOtherMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("dual_iso_preview_total_ms"),
                                        dualIsoPreviewTotalMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("dual_iso_preview_histogram_ms"),
                                        m_lastDualIsoPreviewHistogramMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("dual_iso_preview_regression_ms"),
                                        m_lastDualIsoPreviewRegressionMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("dual_iso_preview_rowscale_ms"),
                                        m_lastDualIsoPreviewRowscaleMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("debayered_frame_ms"),
                                        getMlvLastDebayeredFrameMilliseconds() );
    m_lastStageTimingTelemetry.insert( QStringLiteral("raw_float_convert_ms"),
                                       rawFloatConvertMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("debayer_exclusive_ms"),
                                       debayerExclusiveMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("debayer_wb_prepare_ms"),
                                       debayerWbPrepareMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("debayer_ca_ms"),
                                       debayerCaMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("debayer_kernel_ms"),
                                       debayerKernelMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("debayer_wb_undo_ms"),
                                       debayerWbUndoMs );
    m_lastStageTimingTelemetry.insert(
        QStringLiteral("debayer_pipeline_other_ms"),
        debayerPipelineOtherMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processing_ms"),
                                       processingMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processing_setup_ms"),
                                       processingSetupMs );
    m_lastStageTimingTelemetry.insert(
        QStringLiteral("processing_shadows_highlights_prep_ms"),
        processingShadowsHighlightsPrepMs );
    m_lastStageTimingTelemetry.insert(
        QStringLiteral("processing_highest_green_ms"),
        processingHighestGreenMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processing_core_ms"),
                                       processingCoreMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processing_denoise_ms"),
                                       processingDenoiseMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processing_rbf_ms"),
                                       processingRbfMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processing_ca_ms"),
                                       processingCaMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processing_other_ms"),
                                       processingOtherMs );
    m_lastStageTimingTelemetry.insert(
        QStringLiteral("processing_core_levels_ms"),
        processingCoreLevelsMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processing_core_color_ms"),
                                       processingCoreColorMs );
    m_lastStageTimingTelemetry.insert(
        QStringLiteral("processing_core_creative_ms"),
        processingCoreCreativeMs );
    m_lastStageTimingTelemetry.insert(
        QStringLiteral("processing_core_output_ms"),
        processingCoreOutputMs );
    m_lastStageTimingTelemetry.insert(
        QStringLiteral("processing_core_other_ms"),
        processingCoreOtherMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processed16_total_ms"),
                                       getMlvLastProcessed16TotalMilliseconds() );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processed16_for_8bit_ms"),
                                       getMlvLastProcessed16For8BitMilliseconds() );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processed16_to_8bit_ms"),
                                       getMlvLastProcessed16To8BitMilliseconds() );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processed8_total_ms"),
                                       getMlvLastProcessed8TotalMilliseconds() );
    m_lastStageTimingTelemetry.insert( QStringLiteral("processed8_direct_path_active"),
                                       getMlvLastProcessed8DirectPathActive() != 0 );
    m_lastRenderThreadWorkMs = (mlv_stage_timing_now() - render_start) * 1000.0;
    m_lastRenderThreadTotalMs =
        (m_frameRequestStageTime > 0.0 && mlv_stage_timing_now() >= m_frameRequestStageTime)
            ? (mlv_stage_timing_now() - m_frameRequestStageTime) * 1000.0
            : m_lastRenderThreadWorkMs;
    m_lastFrameReadyEmitStageTime = mlv_stage_timing_now();
    m_lastStageTimingTelemetry.insert( QStringLiteral("render_thread_queue_wait_ms"),
                                       m_lastRenderThreadQueueWaitMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("render_thread_work_ms"),
                                       m_lastRenderThreadWorkMs );
    m_lastStageTimingTelemetry.insert( QStringLiteral("render_thread_total_ms"),
                                       m_lastRenderThreadTotalMs );
    emit frameReady();
}
