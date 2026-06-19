/*!
 * \file RenderFrameThread.cpp
 * \author masc4ii
 * \copyright 2017
 * \brief The render thread
 */

#include "RenderFrameThread.h"

#include "DecodeWorker.h"
#include "GpuDebayer.h"
#include "Phase3Breadcrumbs.h"
#include "PlaybackQualityPolicy.h"
#include "ReconWorker.h"

#include "../../src/batch/WorkerThreadCount.h"
#include "../../src/debayer/debayer.h"
#include "../../src/mlv/llrawproc/llrawproc.h"
#include "../../src/processing/raw_processing.h"
#include "debug/FrameChecksum.h"
#include "debug/StageTimingCsvSink.h"
#include "debug/StageTiming.h"
#include "../../src/mlv/pipeline_stage_capture.h"
#include <QByteArray>
#include <QDebug>
#include <QDateTime>
#include <QMutexLocker>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <new>

extern "C" {
#include "../../src/debayer/wb_conversion.h"
}

namespace {

bool phase3StageCsvSinkEnsureOpen()
{
    if( stage_timing_csv_sink_enabled() != 0 ) return true;

    static std::atomic<bool> attempted{ false };
    bool expected = false;
    if( !attempted.compare_exchange_strong( expected, true, std::memory_order_acq_rel ) )
    {
        return stage_timing_csv_sink_enabled() != 0;
    }

    const QByteArray path = qgetenv( "MLVAPP_PHASE3_TEL_PATH" );
    if( path.isEmpty() ) return false;
    if( stage_timing_csv_sink_open( path.constData() ) == 0 )
    {
        qWarning() << "Could not open Phase 3 TEL CSV sink from MLVAPP_PHASE3_TEL_PATH";
        return false;
    }
    return true;
}

QString phase4bPathLabel( int path )
{
    switch( path )
    {
    case 8: return QStringLiteral("x8-full-xy-pre-recon");
    case 4: return QStringLiteral("x2-full-xy-pre-recon");
    case 3: return QStringLiteral("full-xy-pre-recon");
    case 2: return QStringLiteral("x-only-pre-recon");
    default: return QStringLiteral("none-or-full-recon-fallback");
    }
}

qint64 stagePixelCount( int width, int height )
{
    if( width <= 0 || height <= 0 ) return 0;
    return static_cast<qint64>( width ) * static_cast<qint64>( height );
}

std::array<double, 3> normalizedWbMultipliers6500()
{
    std::array<double, 3> wb{{1.0, 1.0, 1.0}};
    get_kelvin_multipliers_rgb(6500, wb.data());
    const double maxWb = std::max(wb[0], std::max(wb[1], wb[2]));
    if( maxWb > 0.0
     && std::isfinite(wb[0])
     && std::isfinite(wb[1])
     && std::isfinite(wb[2]) )
    {
        wb[0] /= maxWb;
        wb[1] /= maxWb;
        wb[2] /= maxWb;
    }
    else
    {
        wb = {{1.0, 1.0, 1.0}};
    }
    return wb;
}

void insertStageResolutionTelemetry( QJsonObject &telemetry,
                                     const QString &prefix,
                                     const QString &domain,
                                     int width,
                                     int height,
                                     qint64 sourcePixels )
{
    const qint64 pixels = stagePixelCount( width, height );
    telemetry.insert( prefix + QStringLiteral("_domain"), domain );
    telemetry.insert( prefix + QStringLiteral("_width"), width );
    telemetry.insert( prefix + QStringLiteral("_height"), height );
    telemetry.insert( prefix + QStringLiteral("_pixels"), pixels );
    telemetry.insert( prefix + QStringLiteral("_pixel_retention_ratio"),
                      (sourcePixels > 0)
                          ? static_cast<double>( pixels ) / static_cast<double>( sourcePixels )
                          : 0.0 );
    telemetry.insert( prefix + QStringLiteral("_preview_resolution"),
                      sourcePixels > 0 && pixels > 0 && pixels < sourcePixels );
}

class GpuPlaybackReconScope
{
public:
    explicit GpuPlaybackReconScope( bool enabled )
    {
        llrpSetGpuPlaybackReconAllowedForCurrentThread( enabled ? 1 : 0 );
    }

    ~GpuPlaybackReconScope()
    {
        llrpSetGpuPlaybackReconAllowedForCurrentThread( 0 );
    }
};

class GpuPlaybackReconTexturePresentScope
{
public:
    explicit GpuPlaybackReconTexturePresentScope( bool enabled )
    {
        llrpSetGpuPlaybackReconTexturePresentPreferredForCurrentThread(
            enabled ? 1 : 0 );
    }

    ~GpuPlaybackReconTexturePresentScope()
    {
        llrpSetGpuPlaybackReconTexturePresentPreferredForCurrentThread( 0 );
    }
};

bool gpuPlaybackReconEnvRequested()
{
    const QByteArray value = qgetenv("MLVAPP_GPU_PLAYBACK_RECON");
    if( value.isEmpty() || value == QByteArrayLiteral("0") )
    {
        return false;
    }
    return value.toLower() != QByteArrayLiteral("false");
}

bool assignGpuPlaybackReconTextureState(
    RenderFrameThread::GpuPlaybackReconTextureState &destination,
    const llrpGpuPlaybackReconState_t &source)
{
    destination = RenderFrameThread::GpuPlaybackReconTextureState();
    if( !source.valid
     || source.width <= 0
     || source.height <= 0
     || !source.raw2ev
     || !source.ev2raw
     || !source.mix_curve
     || !source.fullres_curve
     || ( source.apply_dither && !source.randn05 ) )
    {
        return false;
    }

    try
    {
        std::shared_ptr<RenderFrameThread::GpuPlaybackReconTextureState::LutSnapshot> luts =
            std::make_shared<RenderFrameThread::GpuPlaybackReconTextureState::LutSnapshot>();
        luts->raw2ev.assign(
            source.raw2ev,
            source.raw2ev + LLRP_GPU_PLAYBACK_RECON_RAW2EV_COUNT );
        luts->ev2raw.assign(
            source.ev2raw,
            source.ev2raw + LLRP_GPU_PLAYBACK_RECON_EV2RAW_COUNT );
        luts->mixCurve.assign(
            source.mix_curve,
            source.mix_curve + LLRP_GPU_PLAYBACK_RECON_RAW2EV_COUNT );
        luts->fullresCurve.assign(
            source.fullres_curve,
            source.fullres_curve + LLRP_GPU_PLAYBACK_RECON_RAW2EV_COUNT );
        if( source.apply_dither )
        {
            luts->randn05.assign(
                source.randn05,
                source.randn05 + LLRP_GPU_PLAYBACK_RECON_RANDN05_COUNT );
        }
        destination.luts = luts;
    }
    catch( const std::bad_alloc & )
    {
        destination = RenderFrameThread::GpuPlaybackReconTextureState();
        return false;
    }

    destination.valid = true;
    destination.width = source.width;
    destination.height = source.height;
    destination.blackLevel = source.black_level;
    destination.whiteLevel = source.white_level;
    destination.whiteDarkened = source.white_darkened;
    destination.blackDelta = source.black_delta;
    destination.evCorrection = source.ev_correction;
    destination.darkNoise = source.dark_noise;
    destination.interpMethod = source.interp_method;
    destination.useAliasMap = source.use_alias_map != 0;
    destination.useFullres = source.use_fullres != 0;
    destination.chromaSmoothMethod = source.chroma_smooth_method;
    for( size_t i = 0; i < destination.isBright.size(); ++i )
    {
        destination.isBright[i] = source.is_bright[i];
    }
    destination.applyDither = source.apply_dither != 0;
    return true;
}

} // namespace

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
    m_activeUseGpuAmazeDebayer = false;
    m_activeFrameNumber = 0;
    m_activeFrameRequestSerial = 0;
    m_activePresentationContext = ReadyFrame::PresentationContext();
    m_activePresentationPreparationOptions = PresentationPreparationOptions();
    m_activeQueuedPlaybackDropCount = 0;
    m_loggedGpuBilinearSuccess = false;
    m_loggedGpuAmazeSuccess = false;
    m_lastFrameUsedGpuBilinearDebayer = false;
    m_lastFrameUsedGpuAmazeDebayer = false;
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
    m_phase3Mode.store( Phase3Mode::Disabled, std::memory_order_relaxed );
    m_decodeWorker = nullptr;
    m_reconWorker = nullptr;
    m_decodeWorkerStop = false;
    m_reconWorkerStop = false;
}

//Destructor
RenderFrameThread::~RenderFrameThread()
{
    stop();
    if( m_decodeWorker )
    {
        m_decodeWorker->wait();
        delete m_decodeWorker;
        m_decodeWorker = nullptr;
    }
    if( m_reconWorker )
    {
        m_reconWorker->wait();
        delete m_reconWorker;
        m_reconWorker = nullptr;
    }
}

//Init all objects
void RenderFrameThread::init(mlvObject_t *pMlvObject, int imageWidth, int imageHeight)
{
    QMutexLocker locker(&m_mutex);
    m_frameReady = false;
    m_renderRequests.clear();
    m_decodeRequests.clear();
    m_reconRequests.clear();
    m_decodeReadySlots.clear();
    m_processReadySlots.clear();
    m_decodeWorkerStop = false;
    m_reconWorkerStop = false;
    m_pMlvObject = pMlvObject;
    m_imageWidth = imageWidth;
    m_imageHeight = imageHeight;
    m_activeUseGpuBilinearDebayer = false;
    m_activeUseGpuAmazeDebayer = false;
    m_activeFrameNumber = 0;
    m_activeFrameRequestSerial = 0;
    m_activeOutputMode = OutputProcessed8;
    m_activePresentationContext = ReadyFrame::PresentationContext();
    m_activePresentationPreparationOptions = PresentationPreparationOptions();
    m_activeQueuedPlaybackDropCount = 0;
    m_renderingSlotIndex = -1;
    m_presentingSlotIndex = -1;
    m_lastFrameUsedGpuBilinearDebayer = false;
    m_lastGpuBilinearFallbackReason.clear();
    m_lastGpuBilinearRendererDescription.clear();
    m_lastFrameUsedGpuAmazeDebayer = false;
    m_lastGpuAmazeFallbackReason.clear();
    m_lastGpuAmazeRendererDescription.clear();
    m_lastDualIsoPreviewHistogramMs = 0.0;
    m_lastDualIsoPreviewRegressionMs = 0.0;
    m_lastDualIsoPreviewRowscaleMs = 0.0;
    m_activeFrameRequestStageTime = 0.0;
    m_lastRenderThreadQueueWaitMs = 0.0;
    m_lastRenderThreadWorkMs = 0.0;
    m_lastRenderThreadTotalMs = 0.0;
    m_lastFrameReadyEmitStageTime = 0.0;
    m_gpuBilinearDebayerRawFrame.clear();
    m_gpuAmazeDebayerRawFrame.clear();
    const size_t pixelCount =
        static_cast<size_t>(qMax(0, imageWidth)) * static_cast<size_t>(qMax(0, imageHeight));
    const size_t rgbPixelCount = pixelCount * 3u;
    for( FrameSlot &slot : m_frameSlots )
    {
        slot.rawImage8.assign( rgbPixelCount, 0u );
        slot.rawImage16.assign( rgbPixelCount, 0u );
        slot.resetMetadata();
        slot.state.store( SlotState::Idle, std::memory_order_release );
    }
}

//Start rendering
void RenderFrameThread::renderFrame(uint32_t frameNumber,
                                    OutputMode outputMode,
                                    bool useGpuBilinearDebayer,
                                    bool useGpuAmazeDebayer,
                                    uint64_t requestSerial,
                                    const ReadyFrame::PresentationContext &presentationContext,
                                    const PresentationPreparationOptions &presentationPreparation)
{
    QMutexLocker locker(&m_mutex);
    RenderRequest request;
    request.frameNumber = frameNumber;
    request.outputMode = outputMode;
    request.useGpuBilinearDebayer = useGpuBilinearDebayer;
    request.useGpuAmazeDebayer = useGpuAmazeDebayer;
    request.requestSerial = requestSerial;
    request.requestStageTime = mlv_stage_timing_now();
    request.phase3Mode = phase3Mode();
    request.presentationContext = presentationContext;
    request.presentationPreparationOptions = presentationPreparation;

    if( request.presentationContext.dropFramePlaybackActive )
    {
        for( auto it = m_renderRequests.begin(); it != m_renderRequests.end(); )
        {
            if( it->presentationContext.dropFramePlaybackActive
             && it->presentationContext.presentationGeneration
                == request.presentationContext.presentationGeneration )
            {
                it = m_renderRequests.erase( it );
                ++request.queuedPlaybackDropCount;
            }
            else
            {
                ++it;
            }
        }
    }

    if( static_cast<int>(m_renderRequests.size()) >= kRenderRequestQueueDepth )
    {
        if( request.presentationContext.dropFramePlaybackActive )
            ++request.queuedPlaybackDropCount;
        m_renderRequests.pop_front();
    }
    m_renderRequests.push_back( request );
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
    return !(m_renderFrame || m_renderingFrame || phase3WorkInFlightLocked());
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

    FrameSlot &slot = m_frameSlots[readySlotIndex];
    slot.ready = false;
    slot.presenting = true;
    if( slot.state.load( std::memory_order_acquire ) == SlotState::Ready )
    {
        transitionSlotState( readySlotIndex,
                             SlotState::Ready,
                             SlotState::Presenting,
                             slot.phase3Mode,
                             slot.frameNumber,
                             slot.requestSerial,
                             "phase3-presenting" );
    }
    m_presentingSlotIndex = readySlotIndex;
    for( int i = 0; i < static_cast<int>( m_frameSlots.size() ); ++i )
    {
        if( i == readySlotIndex ) continue;
        FrameSlot &older = m_frameSlots[i];
        if( !older.ready ) continue;
        if( older.requestSerial >= slot.requestSerial ) continue;
        if( older.state.load( std::memory_order_acquire ) != SlotState::Ready ) continue;
        transitionSlotState( i,
                             SlotState::Ready,
                             SlotState::Idle,
                             older.phase3Mode,
                             older.frameNumber,
                             older.requestSerial,
                             "phase3-stale-ready" );
        older.ready = false;
        older.presenting = false;
    }
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
        frame->renderedImageWidth = slot.renderedImageWidth;
        frame->renderedImageHeight = slot.renderedImageHeight;
        frame->playbackScaleFactorActive = slot.playbackScaleFactorActive;
        frame->playbackFastScaleActive = slot.playbackFastScaleActive;
        frame->playbackScaledWidth = slot.playbackScaledWidth;
        frame->playbackScaledHeight = slot.playbackScaledHeight;
        frame->playbackScaledBytesPerLine = slot.playbackScaledBytesPerLine;
        frame->usedGpuBilinearDebayer = slot.usedGpuBilinearDebayer;
        frame->gpuBilinearFallbackReason = slot.gpuBilinearFallbackReason;
        frame->gpuBilinearRendererDescription = slot.gpuBilinearRendererDescription;
        frame->usedGpuAmazeDebayer = slot.usedGpuAmazeDebayer;
        frame->gpuAmazeFallbackReason = slot.gpuAmazeFallbackReason;
        frame->gpuAmazeRendererDescription = slot.gpuAmazeRendererDescription;
        frame->gpuAmazeTexturePresentCandidate = slot.gpuAmazeTexturePresentCandidate;
        frame->gpuAmazeTextureRawFrame =
            slot.gpuAmazeTextureRawFrame.empty() ? nullptr : slot.gpuAmazeTextureRawFrame.data();
        frame->gpuAmazeTextureRawFrameSize = slot.gpuAmazeTextureRawFrame.size();
        frame->gpuAmazeTextureWidth = slot.gpuAmazeTextureWidth;
        frame->gpuAmazeTextureHeight = slot.gpuAmazeTextureHeight;
        frame->gpuAmazeTextureBlackLevel = slot.gpuAmazeTextureBlackLevel;
        frame->gpuAmazeTextureWbMultipliers = slot.gpuAmazeTextureWbMultipliers;
        frame->gpuPlaybackReconTexturePresentCandidate = slot.gpuPlaybackReconTexturePresentCandidate;
        frame->gpuPlaybackReconTextureNoReadbackCandidate =
            slot.gpuPlaybackReconTextureNoReadbackCandidate;
        /* The recon-output bayer oracle must come from the dedicated snapshot
         * taken right after the recon stage (slot.rawImage16 has since been
         * overwritten by the process stage with the processed display frame).
         * Falls back to nullptr when no snapshot was captured. */
        frame->gpuPlaybackReconTextureBayerFrame =
            slot.gpuPlaybackReconTexturePresentCandidate
            && !slot.gpuPlaybackReconTextureBayerFrame.empty()
                ? slot.gpuPlaybackReconTextureBayerFrame.data()
                : nullptr;
        frame->gpuPlaybackReconTextureBayerFrameSize =
            frame->gpuPlaybackReconTextureBayerFrame
                ? slot.gpuPlaybackReconTextureBayerFrame.size()
                : 0;
        frame->gpuPlaybackReconTextureInputBayerFrame =
            slot.gpuPlaybackReconTextureNoReadbackCandidate
            && !slot.gpuPlaybackReconTextureInputBayerFrame.empty()
                ? slot.gpuPlaybackReconTextureInputBayerFrame.data()
                : nullptr;
        frame->gpuPlaybackReconTextureInputBayerFrameSize =
            frame->gpuPlaybackReconTextureInputBayerFrame
                ? slot.gpuPlaybackReconTextureInputBayerFrame.size()
                : 0;
        frame->gpuPlaybackReconTextureWidth = slot.gpuPlaybackReconTextureWidth;
        frame->gpuPlaybackReconTextureHeight = slot.gpuPlaybackReconTextureHeight;
        frame->gpuPlaybackReconTextureState = slot.gpuPlaybackReconTextureState;
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

void RenderFrameThread::releasePresentedFrameForRequestSerial( uint64_t requestSerial )
{
    QMutexLocker locker(&m_mutex);
    for( int i = 0; i < static_cast<int>( m_frameSlots.size() ); ++i )
    {
        FrameSlot &slot = m_frameSlots[i];
        if( !slot.presenting ) continue;
        if( slot.requestSerial != requestSerial ) continue;
        releaseSlotLocked( i );
        if( m_presentingSlotIndex == i ) m_presentingSlotIndex = -1;
        m_waitCondition.wakeAll();
        break;
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

bool RenderFrameThread::lastFrameUsedGpuAmazeDebayer() const
{
    return m_lastFrameUsedGpuAmazeDebayer;
}

QString RenderFrameThread::lastGpuAmazeFallbackReason() const
{
    return m_lastGpuAmazeFallbackReason;
}

QString RenderFrameThread::lastGpuAmazeRendererDescription() const
{
    return m_lastGpuAmazeRendererDescription;
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

void RenderFrameThread::setPhase3Mode( Phase3Mode mode ) noexcept
{
    m_phase3Mode.store( mode, std::memory_order_release );
}

Phase3Mode RenderFrameThread::phase3Mode( void ) const noexcept
{
    return m_phase3Mode.load( std::memory_order_acquire );
}

//Stop the thread
void RenderFrameThread::stop()
{
    QMutexLocker locker(&m_mutex);
    m_stop = true;
    stopDecodeWorkerLocked();
    stopReconWorkerLocked();
    m_waitCondition.wakeAll();
}

void RenderFrameThread::lock()
{
    m_mutex.lock();
    while( m_renderFrame || m_renderingFrame || phase3WorkInFlightLocked() )
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
    /* Round-2 item 4: the stage CSV sink used to open only inside the
     * Phase-3-mode telemetry emitter, so MLVAPP_PHASE3_TEL_PATH was inert
     * during normal Sharp/Aggressive playback even though the per-stage
     * enter/leave writes are already wired for it. Opening here is
     * trace-only and default-off: the helper no-ops unless the env is set. */
    (void)phase3StageCsvSinkEnsureOpen();
    runPhase3();
}

bool RenderFrameThread::phase3DecodeAheadActive( Phase3Mode mode ) const
{
    return ( mode == Phase3Mode::DecodeAheadOnly
          || mode == Phase3Mode::DecodeReconProcess )
        && !phase3LiveFallbackActive()
        && !phase3KillSwitchActive( mode );
}

void RenderFrameThread::ensureDecodeWorkerStartedLocked( void )
{
    if( m_decodeWorker ) return;
    m_decodeWorkerStop = false;
    m_decodeWorker = new DecodeWorker( this );
    m_decodeWorker->start();
}

void RenderFrameThread::stopDecodeWorkerLocked( void )
{
    m_decodeWorkerStop = true;
    m_decodeWaitCondition.wakeAll();
}

void RenderFrameThread::ensureReconWorkerStartedLocked( void )
{
    if( m_reconWorker ) return;
    m_reconWorkerStop = false;
    m_reconWorker = new ReconWorker( this );
    m_reconWorker->start();
}

void RenderFrameThread::stopReconWorkerLocked( void )
{
    m_reconWorkerStop = true;
    m_reconWaitCondition.wakeAll();
}

bool RenderFrameThread::phase3WorkInFlightLocked( void ) const
{
    if( !m_decodeRequests.empty()
     || !m_reconRequests.empty()
     || !m_decodeReadySlots.empty()
     || !m_processReadySlots.empty() ) return true;
    for( const FrameSlot &slot : m_frameSlots )
    {
        const SlotState state = slot.state.load( std::memory_order_acquire );
        if( state == SlotState::Requested
         || state == SlotState::Decoding
         || state == SlotState::Decoded
         || state == SlotState::ReconReady
         || state == SlotState::Recon
         || state == SlotState::ProcessReady
         || state == SlotState::Processing )
        {
            return true;
        }
    }
    return false;
}

void RenderFrameThread::queueDecodeRequestLocked( int slotIndex, const RenderRequest &request )
{
    if( slotIndex < 0 || slotIndex >= static_cast<int>( m_frameSlots.size() ) ) return;
    FrameSlot &slot = m_frameSlots[slotIndex];
    slot.resetMetadata();
    slot.queuedRequest = request;
    slot.frameNumber = request.frameNumber;
    slot.requestSerial = request.requestSerial;
    slot.outputMode = request.outputMode;
    slot.phase3Mode = request.phase3Mode;
    slot.presentationContext = request.presentationContext;
    transitionSlotState( slotIndex,
                         SlotState::Idle,
                         SlotState::Requested,
                         request.phase3Mode,
                         request.frameNumber,
                         request.requestSerial,
                         "phase3-3b-decode-requested" );
    m_decodeRequests.push_back( { slotIndex, request } );
    m_decodeWaitCondition.wakeOne();
}

bool RenderFrameThread::takeDecodeRequestForWorker( DecodeQueueEntry *entry )
{
    QMutexLocker locker( &m_mutex );
    while( !m_decodeWorkerStop && !m_stop && m_decodeRequests.empty() )
    {
        m_decodeWaitCondition.wait( &m_mutex );
    }
    if( m_decodeWorkerStop || m_stop ) return false;
    if( m_decodeRequests.empty() ) return false;
    if( entry ) *entry = m_decodeRequests.front();
    m_decodeRequests.pop_front();
    return true;
}

void RenderFrameThread::decodeFrameForWorker( const DecodeQueueEntry &entry )
{
    if( entry.slotIndex < 0 || entry.slotIndex >= static_cast<int>( m_frameSlots.size() ) )
    {
        return;
    }

    const bool stageCsvEnabled = stage_timing_csv_sink_enabled() != 0;
    const uint8_t telemetryMode = static_cast<uint8_t>( entry.request.phase3Mode );
    if( stageCsvEnabled )
    {
        stage_timing_csv_sink_write_event(
            entry.request.frameNumber,
            entry.request.requestSerial,
            static_cast<uint8_t>( entry.slotIndex ),
            MLV_STAGE_DECODE,
            "enter",
            mlv_stage_timing_now_ns(),
            telemetryMode,
            0 );
    }

    transitionSlotState( entry.slotIndex,
                         SlotState::Requested,
                         SlotState::Decoding,
                         entry.request.phase3Mode,
                         entry.request.frameNumber,
                         entry.request.requestSerial,
                         "phase3-3b-decoding" );

    FrameSlot &slot = m_frameSlots[entry.slotIndex];
    const size_t rawPixelCount =
        static_cast<size_t>( qMax( 0, m_imageWidth ) )
        * static_cast<size_t>( qMax( 0, m_imageHeight ) );
    if( slot.rawImage16.size() < rawPixelCount )
    {
        slot.rawImage16.resize( rawPixelCount );
    }
    if( rawPixelCount > 0 && m_pMlvObject )
    {
        (void)getMlvRawFrameUint16( m_pMlvObject,
                                    entry.request.frameNumber,
                                    slot.rawImage16.data() );
    }

    if( stageCsvEnabled )
    {
        stage_timing_csv_sink_write_event(
            entry.request.frameNumber,
            entry.request.requestSerial,
            static_cast<uint8_t>( entry.slotIndex ),
            MLV_STAGE_DECODE,
            "leave",
            mlv_stage_timing_now_ns(),
            telemetryMode,
            0 );
    }

    transitionSlotState( entry.slotIndex,
                         SlotState::Decoding,
                         SlotState::Decoded,
                         entry.request.phase3Mode,
                         entry.request.frameNumber,
                         entry.request.requestSerial,
                         "phase3-3b-decoded" );
}

void RenderFrameThread::signalDecodeDoneFromWorker( int slotIndex )
{
    QMutexLocker locker( &m_mutex );
    if( !m_stop && slotIndex >= 0 )
    {
        const RenderRequest request = m_frameSlots[slotIndex].queuedRequest;
        if( request.phase3Mode == Phase3Mode::DecodeReconProcess )
        {
            m_reconRequests.push_back( { slotIndex, request } );
            m_reconWaitCondition.wakeOne();
        }
        else
        {
            m_decodeReadySlots.push_back( slotIndex );
        }
    }
    m_waitCondition.wakeAll();
}

bool RenderFrameThread::takeReconRequestForWorker( ReconQueueEntry *entry )
{
    QMutexLocker locker( &m_mutex );
    while( !m_reconWorkerStop && !m_stop && m_reconRequests.empty() )
    {
        m_reconWaitCondition.wait( &m_mutex );
    }
    if( m_reconWorkerStop || m_stop ) return false;
    if( m_reconRequests.empty() ) return false;
    if( entry ) *entry = m_reconRequests.front();
    m_reconRequests.pop_front();
    return true;
}

void RenderFrameThread::reconFrameForWorker( const ReconQueueEntry &entry,
                                             llrawprocWorkerState_t *workerState )
{
    if( entry.slotIndex < 0 || entry.slotIndex >= static_cast<int>( m_frameSlots.size() ) )
    {
        return;
    }

    const bool stageCsvEnabled = stage_timing_csv_sink_enabled() != 0;
    const uint8_t telemetryMode = static_cast<uint8_t>( entry.request.phase3Mode );
    if( stageCsvEnabled )
    {
        stage_timing_csv_sink_write_event(
            entry.request.frameNumber,
            entry.request.requestSerial,
            static_cast<uint8_t>( entry.slotIndex ),
            MLV_STAGE_RECON,
            "enter",
            mlv_stage_timing_now_ns(),
            telemetryMode,
            0 );
    }

    transitionSlotState( entry.slotIndex,
                         SlotState::Decoded,
                         SlotState::ReconReady,
                         entry.request.phase3Mode,
                         entry.request.frameNumber,
                         entry.request.requestSerial,
                         "phase3-3c-recon-ready" );
    transitionSlotState( entry.slotIndex,
                         SlotState::ReconReady,
                         SlotState::Recon,
                         entry.request.phase3Mode,
                         entry.request.frameNumber,
                         entry.request.requestSerial,
                         "phase3-3c-recon" );

    FrameSlot &slot = m_frameSlots[entry.slotIndex];
    const size_t rawPixelCount =
        static_cast<size_t>( qMax( 0, m_imageWidth ) )
        * static_cast<size_t>( qMax( 0, m_imageHeight ) );
    if( rawPixelCount > 0 && m_pMlvObject && slot.rawImage16.size() >= rawPixelCount )
    {
        const bool wantsGpuPlaybackReconTextureNoReadback =
            entry.request.presentationContext.gpuPlaybackReconTexturePresentRequested
            && entry.request.presentationContext.playbackActive
            && entry.request.presentationContext.playbackScaleFactor == 1
            && m_imageWidth > 0
            && m_imageHeight > 0;
        bool gpuPlaybackReconTextureInputSnapshotCopied = false;
        bool gpuPlaybackReconTexturePreparedStateValid = false;
        bool gpuPlaybackReconTextureStateSnapshotOk = false;
        QString gpuPlaybackReconTextureNoReadbackFallbackReason;
        if( !wantsGpuPlaybackReconTextureNoReadback )
        {
            slot.gpuPlaybackReconTextureInputBayerFrame.clear();
            slot.gpuPlaybackReconTextureState = GpuPlaybackReconTextureState();
            slot.gpuPlaybackReconTextureNoReadbackCandidate = false;
            gpuPlaybackReconTextureNoReadbackFallbackReason =
                QStringLiteral("GPU playback recon no-readback request was not armed");
        }
        const GpuPlaybackReconScope gpuPlaybackReconScope(
            entry.request.presentationContext.playbackActive );
        const GpuPlaybackReconTexturePresentScope gpuPlaybackReconTexturePresentScope(
            wantsGpuPlaybackReconTextureNoReadback );
        mlv_pipeline_capture_set_current_frame( entry.request.frameNumber );
        applyLLRawProcObjectWorker( m_pMlvObject,
                                    slot.rawImage16.data(),
                                    rawPixelCount * sizeof(uint16_t),
                                    workerState,
                                    0 );
        bool gpuPlaybackReconTextureBayerSnapshotCopied = false;
        if( wantsGpuPlaybackReconTextureNoReadback )
        {
            /* Snapshot the recon-output Dual ISO bayer NOW, before the process
             * stage (getMlvProcessedFrame16Scaled) overwrites slot.rawImage16
             * with the fully-processed display frame. This recon bayer is exactly
             * what the no-readback CUDA->GL R16 texture represents, so it is the
             * correct GL-vs-CPU parity oracle. */
            try
            {
                slot.gpuPlaybackReconTextureBayerFrame.assign(
                    slot.rawImage16.begin(),
                    slot.rawImage16.begin() + static_cast<std::ptrdiff_t>( rawPixelCount ) );
                gpuPlaybackReconTextureBayerSnapshotCopied =
                    slot.gpuPlaybackReconTextureBayerFrame.size() == rawPixelCount;
            }
            catch( const std::bad_alloc & )
            {
                slot.gpuPlaybackReconTextureBayerFrame.clear();
                gpuPlaybackReconTextureBayerSnapshotCopied = false;
            }
            const size_t preparedInputWords =
                llrpGpuPlaybackReconGetLastInputBayer16( nullptr, 0 );
            if( preparedInputWords == rawPixelCount )
            {
                try
                {
                    slot.gpuPlaybackReconTextureInputBayerFrame.assign(
                        rawPixelCount,
                        0u );
                    const size_t copiedWords =
                        llrpGpuPlaybackReconGetLastInputBayer16(
                            slot.gpuPlaybackReconTextureInputBayerFrame.data(),
                            slot.gpuPlaybackReconTextureInputBayerFrame.size() );
                    gpuPlaybackReconTextureInputSnapshotCopied =
                        copiedWords == rawPixelCount;
                }
                catch( const std::bad_alloc & )
                {
                    slot.gpuPlaybackReconTextureInputBayerFrame.clear();
                    slot.gpuPlaybackReconTextureState = GpuPlaybackReconTextureState();
                    slot.gpuPlaybackReconTextureNoReadbackCandidate = false;
                    gpuPlaybackReconTextureNoReadbackFallbackReason =
                        QStringLiteral("GPU playback recon prepared input snapshot allocation failed");
                }
            }
            else
            {
                slot.gpuPlaybackReconTextureInputBayerFrame.clear();
                gpuPlaybackReconTextureNoReadbackFallbackReason =
                    QStringLiteral("GPU playback recon prepared input snapshot was unavailable");
            }

            llrpGpuPlaybackReconState_t preparedState;
            memset( &preparedState, 0, sizeof( preparedState ) );
            const bool preparedStateAvailable =
                llrpGpuPlaybackReconGetLastPreparedState( &preparedState ) != 0;
            gpuPlaybackReconTexturePreparedStateValid = preparedState.valid != 0;
            gpuPlaybackReconTextureStateSnapshotOk =
                preparedStateAvailable
                && assignGpuPlaybackReconTextureState( slot.gpuPlaybackReconTextureState,
                                                       preparedState );
            if( gpuPlaybackReconTextureInputSnapshotCopied
             && gpuPlaybackReconTextureStateSnapshotOk
             && gpuPlaybackReconTextureBayerSnapshotCopied )
            {
                slot.gpuPlaybackReconTextureNoReadbackCandidate = true;
            }
            else
            {
                slot.gpuPlaybackReconTextureNoReadbackCandidate = false;
                slot.gpuPlaybackReconTextureInputBayerFrame.clear();
                slot.gpuPlaybackReconTextureBayerFrame.clear();
                slot.gpuPlaybackReconTextureState = GpuPlaybackReconTextureState();
                if( gpuPlaybackReconTextureNoReadbackFallbackReason.isEmpty() )
                {
                    gpuPlaybackReconTextureNoReadbackFallbackReason =
                        !preparedStateAvailable
                            ? QStringLiteral("GPU playback recon prepared state was unavailable")
                            : ( !gpuPlaybackReconTextureBayerSnapshotCopied
                                ? QStringLiteral("GPU playback recon output bayer snapshot was unavailable")
                                : QStringLiteral("GPU playback recon state snapshot was rejected") );
                }
            }
        }
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_playback_recon_texture_present_no_readback_recon_requested"),
            wantsGpuPlaybackReconTextureNoReadback );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_playback_recon_texture_present_no_readback_input_words"),
            static_cast<qint64>( slot.gpuPlaybackReconTextureInputBayerFrame.size() ) );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_playback_recon_texture_present_no_readback_prepared_state_valid"),
            gpuPlaybackReconTexturePreparedStateValid );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_playback_recon_texture_present_no_readback_state_snapshot_ok"),
            gpuPlaybackReconTextureStateSnapshotOk );
        if( !gpuPlaybackReconTextureNoReadbackFallbackReason.isEmpty() )
        {
            slot.stageTimingTelemetry.insert(
                QStringLiteral("gpu_playback_recon_texture_present_no_readback_fallback_reason"),
                gpuPlaybackReconTextureNoReadbackFallbackReason );
        }
    }

    if( stageCsvEnabled )
    {
        stage_timing_csv_sink_write_event(
            entry.request.frameNumber,
            entry.request.requestSerial,
            static_cast<uint8_t>( entry.slotIndex ),
            MLV_STAGE_RECON,
            "leave",
            mlv_stage_timing_now_ns(),
            telemetryMode,
            0 );
    }

    transitionSlotState( entry.slotIndex,
                         SlotState::Recon,
                         SlotState::ProcessReady,
                         entry.request.phase3Mode,
                         entry.request.frameNumber,
                         entry.request.requestSerial,
                         "phase3-3c-process-ready" );
}

void RenderFrameThread::signalReconDoneFromWorker( int slotIndex )
{
    QMutexLocker locker( &m_mutex );
    if( !m_stop && slotIndex >= 0 )
    {
        m_processReadySlots.push_back( slotIndex );
    }
    m_waitCondition.wakeAll();
}

int RenderFrameThread::waitForDecodedSlotLocked( void )
{
    while( !m_stop && m_decodeReadySlots.empty() )
    {
        m_waitCondition.wait( &m_mutex );
    }
    if( m_stop ) return -1;
    const int slotIndex = m_decodeReadySlots.front();
    m_decodeReadySlots.pop_front();
    return slotIndex;
}

int RenderFrameThread::waitForProcessReadySlotLocked( void )
{
    while( !m_stop && m_processReadySlots.empty() )
    {
        m_waitCondition.wait( &m_mutex );
    }
    if( m_stop ) return -1;
    const int slotIndex = m_processReadySlots.front();
    m_processReadySlots.pop_front();
    return slotIndex;
}

void RenderFrameThread::setupActiveRequestLocked( const RenderRequest &request, int slotIndex )
{
    m_activeFrameNumber = request.frameNumber;
    m_activeOutputMode = request.outputMode;
    m_activeUseGpuBilinearDebayer = request.useGpuBilinearDebayer;
    m_activeUseGpuAmazeDebayer = request.useGpuAmazeDebayer;
    m_activeFrameRequestSerial = request.requestSerial;
    m_activeFrameRequestStageTime = request.requestStageTime;
    m_activePresentationContext = request.presentationContext;
    m_activePresentationPreparationOptions = request.presentationPreparationOptions;
    m_activeQueuedPlaybackDropCount = request.queuedPlaybackDropCount;
    m_renderingFrame = true;
    m_renderingSlotIndex = slotIndex;
}

void RenderFrameThread::renderDecodedSlot( int slotIndex,
                                           const RenderRequest &request,
                                           Phase3Mode activePhase3Mode )
{
    const bool phase3Active = activePhase3Mode != Phase3Mode::Disabled;
    const bool stageCsvEnabled =
        !phase3Active && stage_timing_csv_sink_enabled() != 0;
    const uint8_t telemetryMode = static_cast<uint8_t>( activePhase3Mode );
    const uint64_t renderEnterNs =
        stageCsvEnabled ? mlv_stage_timing_now_ns() : 0;
    if( stageCsvEnabled )
    {
        stage_timing_csv_sink_write_event(
            request.frameNumber,
            request.requestSerial,
            static_cast<uint8_t>( slotIndex ),
            MLV_STAGE_DECODE,
            "enter",
            renderEnterNs,
            telemetryMode,
            0 );
    }

    const SlotState slotState = m_frameSlots[slotIndex].state.load( std::memory_order_acquire );
    const bool consumeDecodedRaw =
        activePhase3Mode == Phase3Mode::DecodeAheadOnly
     && slotState == SlotState::Decoded
     && !m_frameSlots[slotIndex].rawImage16.empty();
    const bool consumeReconnedRaw =
        activePhase3Mode == Phase3Mode::DecodeReconProcess
     && slotState == SlotState::ProcessReady
     && !m_frameSlots[slotIndex].rawImage16.empty();
    drawFrame( slotIndex,
               ( consumeDecodedRaw || consumeReconnedRaw )
                   ? m_frameSlots[slotIndex].rawImage16.data()
                   : nullptr,
               consumeReconnedRaw );
    if( phase3Active )
    {
        m_frameSlots[slotIndex].phase3Mode = activePhase3Mode;
    }
    if( frame_checksum_enabled()
     && !m_frameSlots[slotIndex].rawImage8.empty() )
    {
        const uint64_t checksum =
            frame_checksum_compute( m_frameSlots[slotIndex].rawImage8.data(),
                                    m_frameSlots[slotIndex].rawImage8.size() );
        frame_checksum_log_record( static_cast<uint32_t>( request.frameNumber ),
                                   checksum );
    }
    if( phase3Active )
    {
        emitPhase3StageTelemetry( request,
                                  m_frameSlots[slotIndex],
                                  slotIndex,
                                  activePhase3Mode );
    }
    else if( stageCsvEnabled )
    {
        const uint64_t renderLeaveNs = mlv_stage_timing_now_ns();
        stage_timing_csv_sink_write_event(
            request.frameNumber,
            request.requestSerial,
            static_cast<uint8_t>( slotIndex ),
            MLV_STAGE_DECODE,
            "leave",
            renderLeaveNs,
            telemetryMode,
            0 );
        stage_timing_csv_sink_write_event(
            request.frameNumber,
            request.requestSerial,
            static_cast<uint8_t>( slotIndex ),
            MLV_STAGE_RECON,
            "enter",
            renderEnterNs,
            telemetryMode,
            0 );
        stage_timing_csv_sink_write_event(
            request.frameNumber,
            request.requestSerial,
            static_cast<uint8_t>( slotIndex ),
            MLV_STAGE_RECON,
            "leave",
            renderLeaveNs,
            telemetryMode,
            0 );
        stage_timing_csv_sink_write_event(
            request.frameNumber,
            request.requestSerial,
            static_cast<uint8_t>( slotIndex ),
            MLV_STAGE_PROCESS,
            "enter",
            renderEnterNs,
            telemetryMode,
            0 );
        stage_timing_csv_sink_write_event(
            request.frameNumber,
            request.requestSerial,
            static_cast<uint8_t>( slotIndex ),
            MLV_STAGE_PROCESS,
            "leave",
            renderLeaveNs,
            telemetryMode,
            0 );
        stage_timing_csv_sink_write_event(
            request.frameNumber,
            request.requestSerial,
            static_cast<uint8_t>( slotIndex ),
            MLV_STAGE_DISPLAY,
            "enter",
            renderEnterNs,
            telemetryMode,
            0 );
        stage_timing_csv_sink_write_event(
            request.frameNumber,
            request.requestSerial,
            static_cast<uint8_t>( slotIndex ),
            MLV_STAGE_DISPLAY,
            "leave",
            renderLeaveNs,
            telemetryMode,
            0 );
    }
}

void RenderFrameThread::publishRenderedSlot( int slotIndex,
                                             const RenderRequest &request,
                                             Phase3Mode activePhase3Mode )
{
    Q_UNUSED( request );
    if( slotIndex < 0 || slotIndex >= static_cast<int>( m_frameSlots.size() ) )
    {
        return;
    }
    if( activePhase3Mode != Phase3Mode::Disabled )
    {
        const SlotState processingFrom =
            activePhase3Mode == Phase3Mode::DecodeReconProcess
                ? SlotState::ProcessReady
                : SlotState::Decoded;
        transitionSlotState( slotIndex,
                             processingFrom,
                             SlotState::Processing,
                             activePhase3Mode,
                             m_frameSlots[slotIndex].frameNumber,
                             m_frameSlots[slotIndex].requestSerial,
                             activePhase3Mode == Phase3Mode::DecodeReconProcess
                                ? "phase3-3c-processing"
                                : "phase3-3b-processing" );
        transitionSlotState( slotIndex,
                             SlotState::Processing,
                             SlotState::Ready,
                             activePhase3Mode,
                             m_frameSlots[slotIndex].frameNumber,
                             m_frameSlots[slotIndex].requestSerial,
                             activePhase3Mode == Phase3Mode::DecodeReconProcess
                                ? "phase3-3c-ready"
                                : "phase3-3b-ready" );
    }
    m_renderingFrame = false;
    m_renderingSlotIndex = -1;
    m_frameSlots[slotIndex].ready = true;
    m_frameReady = true;
    m_waitCondition.wakeAll();
}

void RenderFrameThread::runPhase3( void )
{
    m_mutex.lock();
    ensureDecodeWorkerStartedLocked();
    ensureReconWorkerStartedLocked();
    while( !m_stop )
    {
        while( !m_stop
            && m_decodeReadySlots.empty()
            && m_processReadySlots.empty()
            && ( m_renderRequests.empty() || findFreeSlotLocked() < 0 ) )
        {
            m_waitCondition.wait( &m_mutex );
        }
        if( m_stop ) break;

        bool queuedPhase3Request = false;
        if( !m_renderRequests.empty()
         && findFreeSlotLocked() >= 0
         && phase3DecodeAheadActive( m_renderRequests.front().phase3Mode ) )
        {
            const int slotIndex = findFreeSlotLocked();
            const RenderRequest request = m_renderRequests.front();
            m_renderRequests.pop_front();
            m_renderFrame = !m_renderRequests.empty()
                         || !m_decodeRequests.empty()
                         || !m_reconRequests.empty()
                         || !m_decodeReadySlots.empty()
                         || !m_processReadySlots.empty();
            queueDecodeRequestLocked( slotIndex, request );
            queuedPhase3Request = true;
        }

        int slotIndex = -1;
        RenderRequest request;
        Phase3Mode activePhase3Mode = Phase3Mode::Disabled;
        if( !m_processReadySlots.empty() )
        {
            slotIndex = waitForProcessReadySlotLocked();
            if( slotIndex < 0 ) break;
            request = m_frameSlots[slotIndex].queuedRequest;
            activePhase3Mode = request.phase3Mode;
        }
        else if( !m_decodeReadySlots.empty() )
        {
            slotIndex = waitForDecodedSlotLocked();
            if( slotIndex < 0 ) break;
            request = m_frameSlots[slotIndex].queuedRequest;
            activePhase3Mode = request.phase3Mode;
        }
        else if( queuedPhase3Request )
        {
            continue;
        }
        else if( !m_renderRequests.empty() && findFreeSlotLocked() >= 0 )
        {
            slotIndex = findFreeSlotLocked();
            request = m_renderRequests.front();
            m_renderRequests.pop_front();
            m_renderFrame = !m_renderRequests.empty();

            const Phase3Mode requestedPhase3Mode = request.phase3Mode;
            if( requestedPhase3Mode != Phase3Mode::Disabled )
            {
                if( phase3LiveFallbackActive() )
                {
                    const qint64 nowMs = QDateTime::currentMSecsSinceEpoch();
                    playbackQualityAutoFallbackEpochWriteToSettings(
                        PlaybackQualityMode::Phase3Fast, nowMs );
                    playbackQualityAutoFallbackEpochWriteToSettings(
                        PlaybackQualityMode::Phase3HQ, nowMs );
                    Phase3Breadcrumbs::push( static_cast<uint8_t>( slotIndex ),
                                             0,
                                             0,
                                             mlv_stage_timing_now_ns(),
                                             request.frameNumber,
                                             request.requestSerial,
                                             static_cast<uint8_t>( requestedPhase3Mode ),
                                             "phase3-live-fallback" );
                }
                else if( !phase3KillSwitchActive( requestedPhase3Mode ) )
                {
                    activePhase3Mode = requestedPhase3Mode;
                    transitionSlotState( slotIndex,
                                         SlotState::Idle,
                                         SlotState::Requested,
                                         activePhase3Mode,
                                         request.frameNumber,
                                         request.requestSerial,
                                         "phase3-requested" );
                    transitionSlotState( slotIndex,
                                         SlotState::Requested,
                                         SlotState::Decoding,
                                         activePhase3Mode,
                                         request.frameNumber,
                                         request.requestSerial,
                                         "phase3-decoding" );
                }
            }
        }
        else
        {
            continue;
        }

        setupActiveRequestLocked( request, slotIndex );

        if( !m_renderRequests.empty()
         && findFreeSlotLocked() >= 0
         && phase3DecodeAheadActive( m_renderRequests.front().phase3Mode ) )
        {
            const int nextSlotIndex = findFreeSlotLocked();
            const RenderRequest nextRequest = m_renderRequests.front();
            m_renderRequests.pop_front();
            queueDecodeRequestLocked( nextSlotIndex, nextRequest );
        }

        m_mutex.unlock();
        renderDecodedSlot( slotIndex, request, activePhase3Mode );
        m_mutex.lock();

        if( activePhase3Mode != Phase3Mode::Disabled
         && m_frameSlots[slotIndex].state.load( std::memory_order_acquire ) == SlotState::Decoding )
        {
            transitionSlotState( slotIndex,
                                 SlotState::Decoding,
                                 SlotState::Decoded,
                                 activePhase3Mode,
                                 request.frameNumber,
                                 request.requestSerial,
                                 "phase3-decoded" );
        }
        publishRenderedSlot( slotIndex, request, activePhase3Mode );
        m_mutex.unlock();
        emit frameReady();
        m_mutex.lock();
    }
    stopDecodeWorkerLocked();
    stopReconWorkerLocked();
    m_stop = false;
    m_mutex.unlock();
    if( m_decodeWorker )
    {
        m_decodeWorker->wait();
        delete m_decodeWorker;
        m_decodeWorker = nullptr;
    }
    if( m_reconWorker )
    {
        m_reconWorker->wait();
        delete m_reconWorker;
        m_reconWorker = nullptr;
    }
}

void RenderFrameThread::transitionSlotState( int slotIndex,
                                             SlotState from,
                                             SlotState to,
                                             Phase3Mode mode,
                                             uint32_t frameNumber,
                                             uint64_t requestSerial,
                                             const char * context )
{
    if( slotIndex < 0 || slotIndex >= static_cast<int>( m_frameSlots.size() ) ) return;

    SlotState expected = from;
    const bool changed =
        m_frameSlots[slotIndex].state.compare_exchange_strong(
            expected,
            to,
            std::memory_order_acq_rel,
            std::memory_order_acquire );
    Q_ASSERT_X( changed,
                "RenderFrameThread::transitionSlotState",
                "unexpected Phase 3 slot state transition" );
    if( !changed )
    {
        return;
    }

    Phase3Breadcrumbs::push(
        static_cast<uint8_t>( slotIndex ),
        static_cast<uint8_t>( from ),
        static_cast<uint8_t>( to ),
        mlv_stage_timing_now_ns(),
        frameNumber,
        requestSerial,
        static_cast<uint8_t>( mode ),
        context );
}

void RenderFrameThread::emitPhase3StageTelemetry( const RenderRequest &request,
                                                  const FrameSlot &slot,
                                                  int slotIndex,
                                                  Phase3Mode mode ) const
{
    if( !phase3StageCsvSinkEnsureOpen() ) return;

    const QJsonObject &telemetry = slot.stageTimingTelemetry;
    const auto fieldMs = [&telemetry]( const char * key ) -> double {
        return telemetry.value( QLatin1String( key ) ).toDouble();
    };
    const auto durationNs = []( double ms ) -> uint64_t {
        if( !std::isfinite( ms ) || ms <= 0.0 ) return 0;
        return static_cast<uint64_t>( ms * 1000000.0 );
    };
    double reconMs = fieldMs( "llrawproc_total_ms" );
    if( reconMs <= 0.0 ) reconMs = fieldMs( "llrawproc_ms" );

    double processMs = fieldMs( "processed8_total_ms" );
    if( processMs <= 0.0 ) processMs = fieldMs( "processed16_total_ms" );
    if( processMs <= 0.0 ) processMs = fieldMs( "processing_ms" );

    uint64_t ns = mlv_stage_timing_now_ns();
    const uint8_t slotId = static_cast<uint8_t>( slotIndex );
    const uint8_t phase3 = static_cast<uint8_t>( mode );
    const auto writeEvent = [&]( const char * stage, const char * event, uint64_t eventNs ) {
        stage_timing_csv_sink_write_event( request.frameNumber,
                                           request.requestSerial,
                                           slotId,
                                           stage,
                                           event,
                                           eventNs,
                                           phase3,
                                           0 );
    };
    const auto emitStage = [&]( const char * stage, uint64_t duration ) {
        writeEvent( stage, "enter", ns );
        ns += duration;
        writeEvent( stage, "leave", ns );
    };

    const double decodeMs = fieldMs( "raw_uint16_ms" );
    if( !( ( mode == Phase3Mode::DecodeAheadOnly
          || mode == Phase3Mode::DecodeReconProcess )
        && decodeMs <= 0.0 ) )
    {
        emitStage( MLV_STAGE_DECODE, durationNs( decodeMs ) );
    }
    if( !( mode == Phase3Mode::DecodeReconProcess && reconMs <= 0.0 ) )
    {
        emitStage( MLV_STAGE_RECON, durationNs( reconMs ) );
    }
    emitStage( MLV_STAGE_PROCESS, durationNs( processMs ) );
    emitStage( MLV_STAGE_DISPLAY,
               durationNs( fieldMs( "render_thread_playback_scale_ms" ) ) );
}

void RenderFrameThread::runSerial(void)
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
        m_activeUseGpuAmazeDebayer = request.useGpuAmazeDebayer;
        m_activeFrameRequestSerial = request.requestSerial;
        m_activeFrameRequestStageTime = request.requestStageTime;
        m_activePresentationContext = request.presentationContext;
        m_activePresentationPreparationOptions = request.presentationPreparationOptions;
        m_activeQueuedPlaybackDropCount = request.queuedPlaybackDropCount;
        m_renderingFrame = true;
        m_renderingSlotIndex = slotIndex;

        const Phase3Mode requestedPhase3Mode = request.phase3Mode;
        Phase3Mode activePhase3Mode = Phase3Mode::Disabled;
        if( requestedPhase3Mode != Phase3Mode::Disabled )
        {
            if( phase3LiveFallbackActive() )
            {
                const qint64 nowMs = QDateTime::currentMSecsSinceEpoch();
                playbackQualityAutoFallbackEpochWriteToSettings(
                    PlaybackQualityMode::Phase3Fast, nowMs );
                playbackQualityAutoFallbackEpochWriteToSettings(
                    PlaybackQualityMode::Phase3HQ, nowMs );
                Phase3Breadcrumbs::push( static_cast<uint8_t>( slotIndex ),
                                         0,
                                         0,
                                         mlv_stage_timing_now_ns(),
                                         request.frameNumber,
                                         request.requestSerial,
                                         static_cast<uint8_t>( requestedPhase3Mode ),
                                         "phase3-live-fallback" );
            }
            else if( !phase3KillSwitchActive( requestedPhase3Mode ) )
            {
                activePhase3Mode = requestedPhase3Mode;
                transitionSlotState( slotIndex,
                                     SlotState::Idle,
                                     SlotState::Requested,
                                     activePhase3Mode,
                                     request.frameNumber,
                                     request.requestSerial,
                                     "phase3-requested" );
                transitionSlotState( slotIndex,
                                     SlotState::Requested,
                                     SlotState::Decoding,
                                     activePhase3Mode,
                                     request.frameNumber,
                                     request.requestSerial,
                                     "phase3-decoding" );
            }
        }
        m_mutex.unlock();
        const bool phase3Active = activePhase3Mode != Phase3Mode::Disabled;
        const bool stageCsvEnabled =
            !phase3Active && stage_timing_csv_sink_enabled() != 0;
        const uint8_t telemetryMode = static_cast<uint8_t>( activePhase3Mode );
        const uint64_t renderEnterNs =
            stageCsvEnabled ? mlv_stage_timing_now_ns() : 0;
        if( stageCsvEnabled )
        {
            stage_timing_csv_sink_write_event(
                request.frameNumber,
                request.requestSerial,
                static_cast<uint8_t>( slotIndex ),
                MLV_STAGE_DECODE,
                "enter",
                renderEnterNs,
                telemetryMode,
                0 );
        }
        drawFrame( slotIndex );
        if( phase3Active )
        {
            m_frameSlots[slotIndex].phase3Mode = activePhase3Mode;
        }
        if( frame_checksum_enabled()
         && !m_frameSlots[slotIndex].rawImage8.empty() )
        {
            const uint64_t checksum =
                frame_checksum_compute( m_frameSlots[slotIndex].rawImage8.data(),
                                        m_frameSlots[slotIndex].rawImage8.size() );
            frame_checksum_log_record( static_cast<uint32_t>( request.frameNumber ),
                                       checksum );
        }
        if( phase3Active )
        {
            emitPhase3StageTelemetry( request,
                                      m_frameSlots[slotIndex],
                                      slotIndex,
                                      activePhase3Mode );
        }
        else if( stageCsvEnabled )
        {
            const uint64_t renderLeaveNs = mlv_stage_timing_now_ns();
            stage_timing_csv_sink_write_event(
                request.frameNumber,
                request.requestSerial,
                static_cast<uint8_t>( slotIndex ),
                MLV_STAGE_DECODE,
                "leave",
                renderLeaveNs,
                telemetryMode,
                0 );
            stage_timing_csv_sink_write_event(
                request.frameNumber,
                request.requestSerial,
                static_cast<uint8_t>( slotIndex ),
                MLV_STAGE_RECON,
                "enter",
                renderEnterNs,
                telemetryMode,
                0 );
            stage_timing_csv_sink_write_event(
                request.frameNumber,
                request.requestSerial,
                static_cast<uint8_t>( slotIndex ),
                MLV_STAGE_RECON,
                "leave",
                renderLeaveNs,
                telemetryMode,
                0 );
            stage_timing_csv_sink_write_event(
                request.frameNumber,
                request.requestSerial,
                static_cast<uint8_t>( slotIndex ),
                MLV_STAGE_PROCESS,
                "enter",
                renderEnterNs,
                telemetryMode,
                0 );
            stage_timing_csv_sink_write_event(
                request.frameNumber,
                request.requestSerial,
                static_cast<uint8_t>( slotIndex ),
                MLV_STAGE_PROCESS,
                "leave",
                renderLeaveNs,
                telemetryMode,
                0 );
            stage_timing_csv_sink_write_event(
                request.frameNumber,
                request.requestSerial,
                static_cast<uint8_t>( slotIndex ),
                MLV_STAGE_DISPLAY,
                "enter",
                renderEnterNs,
                telemetryMode,
                0 );
            stage_timing_csv_sink_write_event(
                request.frameNumber,
                request.requestSerial,
                static_cast<uint8_t>( slotIndex ),
                MLV_STAGE_DISPLAY,
                "leave",
                renderLeaveNs,
                telemetryMode,
                0 );
        }
        m_mutex.lock();
        if( phase3Active )
        {
            /* Phase 3A keeps execution serial: drawFrame() is still the
             * monolithic decode/recon/process call. These lifecycle markers
             * validate slot ownership and breadcrumb wiring; later sub-phases
             * move the stage transitions to the real worker boundaries. */
            transitionSlotState( slotIndex,
                                 SlotState::Decoding,
                                 SlotState::Decoded,
                                 activePhase3Mode,
                                 request.frameNumber,
                                 request.requestSerial,
                                 "phase3-decoded" );
            transitionSlotState( slotIndex,
                                 SlotState::Decoded,
                                 SlotState::Processing,
                                 activePhase3Mode,
                                 request.frameNumber,
                                 request.requestSerial,
                                 "phase3-processing" );
            transitionSlotState( slotIndex,
                                 SlotState::Processing,
                                 SlotState::Ready,
                                 activePhase3Mode,
                                 request.frameNumber,
                                 request.requestSerial,
                                 "phase3-ready" );
        }
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
        if( slot.state.load( std::memory_order_acquire ) != SlotState::Idle ) continue;
        return i;
    }
    return -1;
}

void RenderFrameThread::releaseSlotLocked( int slotIndex )
{
    if( slotIndex < 0 || slotIndex >= static_cast<int>(m_frameSlots.size()) ) return;
    FrameSlot &slot = m_frameSlots[slotIndex];
    const SlotState state = slot.state.load( std::memory_order_acquire );
    if( state == SlotState::Presenting )
    {
        transitionSlotState( slotIndex,
                             SlotState::Presenting,
                             SlotState::Idle,
                             slot.phase3Mode,
                             slot.frameNumber,
                             slot.requestSerial,
                             "phase3-released" );
    }
    else if( state != SlotState::Idle )
    {
        Q_ASSERT_X( false,
                    "RenderFrameThread::releaseSlotLocked",
                    "releasing a slot from an unexpected Phase 3 state" );
        slot.state.store( SlotState::Idle, std::memory_order_release );
    }
    slot.ready = false;
    slot.presenting = false;
    slot.phase3Mode = Phase3Mode::Disabled;
}

void RenderFrameThread::copySlotTelemetryLocked( const FrameSlot &slot )
{
    m_lastFrameUsedGpuBilinearDebayer = slot.usedGpuBilinearDebayer;
    m_lastGpuBilinearFallbackReason = slot.gpuBilinearFallbackReason;
    m_lastGpuBilinearRendererDescription = slot.gpuBilinearRendererDescription;
    m_lastFrameUsedGpuAmazeDebayer = slot.usedGpuAmazeDebayer;
    m_lastGpuAmazeFallbackReason = slot.gpuAmazeFallbackReason;
    m_lastGpuAmazeRendererDescription = slot.gpuAmazeRendererDescription;
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
void RenderFrameThread::drawFrame( int slotIndex,
                                   const uint16_t *decodedRawFrame,
                                   bool decodedRawFrameAlreadyReconned )
{
    FrameSlot &slot = m_frameSlots[slotIndex];
    const bool preserveGpuPlaybackReconTextureSnapshot =
        decodedRawFrameAlreadyReconned
        && slot.gpuPlaybackReconTextureNoReadbackCandidate;
    std::vector<uint16_t> preservedGpuPlaybackReconTextureInputBayerFrame;
    std::vector<uint16_t> preservedGpuPlaybackReconTextureBayerFrame;
    GpuPlaybackReconTextureState preservedGpuPlaybackReconTextureState;
    QJsonObject preservedGpuPlaybackReconTextureTelemetry;
    const auto preserveGpuPlaybackReconTextureTelemetry =
        [&slot, &preservedGpuPlaybackReconTextureTelemetry]( const char * key )
    {
        const QString field = QString::fromLatin1( key );
        if( slot.stageTimingTelemetry.contains( field ) )
        {
            preservedGpuPlaybackReconTextureTelemetry.insert(
                field,
                slot.stageTimingTelemetry.value( field ) );
        }
    };
    if( decodedRawFrameAlreadyReconned )
    {
        preserveGpuPlaybackReconTextureTelemetry(
            "gpu_playback_recon_texture_present_no_readback_recon_requested" );
        preserveGpuPlaybackReconTextureTelemetry(
            "gpu_playback_recon_texture_present_no_readback_input_words" );
        preserveGpuPlaybackReconTextureTelemetry(
            "gpu_playback_recon_texture_present_no_readback_prepared_state_valid" );
        preserveGpuPlaybackReconTextureTelemetry(
            "gpu_playback_recon_texture_present_no_readback_state_snapshot_ok" );
        preserveGpuPlaybackReconTextureTelemetry(
            "gpu_playback_recon_texture_present_no_readback_fallback_reason" );
    }
    if( preserveGpuPlaybackReconTextureSnapshot )
    {
        preservedGpuPlaybackReconTextureInputBayerFrame =
            std::move( slot.gpuPlaybackReconTextureInputBayerFrame );
        preservedGpuPlaybackReconTextureBayerFrame =
            std::move( slot.gpuPlaybackReconTextureBayerFrame );
        preservedGpuPlaybackReconTextureState =
            slot.gpuPlaybackReconTextureState;
    }
    slot.resetMetadata();
    slot.frameNumber = m_activeFrameNumber;
    slot.requestSerial = m_activeFrameRequestSerial;
    slot.outputMode = m_activeOutputMode;
    slot.presentationContext = m_activePresentationContext;
    if( preserveGpuPlaybackReconTextureSnapshot )
    {
        slot.gpuPlaybackReconTextureNoReadbackCandidate = true;
        slot.gpuPlaybackReconTextureInputBayerFrame =
            std::move( preservedGpuPlaybackReconTextureInputBayerFrame );
        slot.gpuPlaybackReconTextureBayerFrame =
            std::move( preservedGpuPlaybackReconTextureBayerFrame );
        slot.gpuPlaybackReconTextureState =
            preservedGpuPlaybackReconTextureState;
    }
    for( auto it = preservedGpuPlaybackReconTextureTelemetry.begin();
         it != preservedGpuPlaybackReconTextureTelemetry.end();
         ++it )
    {
        slot.stageTimingTelemetry.insert( it.key(), it.value() );
    }

    const double render_start = mlv_stage_timing_now();
    const uint32_t frameNumber = slot.frameNumber;
    const OutputMode outputMode = slot.outputMode;
    const bool useGpuBilinearDebayer = m_activeUseGpuBilinearDebayer;
    const bool useGpuAmazeDebayer = m_activeUseGpuAmazeDebayer;
    const int playbackScaleFactor = m_activePresentationContext.playbackScaleFactor;
    const bool playbackPreviewFastPathActive =
        outputMode == OutputProcessed8
        && m_activePresentationContext.playbackActive
        && m_activePresentationContext.renderThreadUsingPlaybackPreviewProcessing;
    struct PlaybackPreviewModeGuard
    {
        PlaybackPreviewModeGuard( bool enabled, int scaleFactor )
            : previousPreviewMode( processingPlaybackPreviewModeEnabled() )
            , previousAggressivePreviewMode( processingPlaybackAggressivePreviewModeEnabled() )
            , previousScaleFactor( processingPlaybackPreviewScaleFactor() )
        {
            processingSetPlaybackPreviewMode( enabled ? 1 : 0 );
            processingSetPlaybackAggressivePreviewMode(
                ( enabled && mlvPlaybackAggressivePreviewMode() != 0 ) ? 1 : 0 );
            processingSetPlaybackPreviewScaleFactor( enabled ? scaleFactor : 1 );
        }

        ~PlaybackPreviewModeGuard()
        {
            processingSetPlaybackPreviewScaleFactor( previousScaleFactor );
            processingSetPlaybackAggressivePreviewMode( previousAggressivePreviewMode );
            processingSetPlaybackPreviewMode( previousPreviewMode );
        }

        int previousPreviewMode;
        int previousAggressivePreviewMode;
        int previousScaleFactor;
    } playbackPreviewModeGuard( playbackPreviewFastPathActive, playbackScaleFactor );
    const GpuPlaybackReconScope gpuPlaybackReconScope(
        m_activePresentationContext.playbackActive );
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
    if ( !useGpuAmazeDebayer )
    {
        slot.gpuAmazeFallbackReason.clear();
    }

    /* Read the requested scale factor. The MLV core can still reject invalid
     * alignment, so request and active values are logged separately. */
    int renderedImageWidth = m_imageWidth;
    int renderedImageHeight = m_imageHeight;
    if( m_pMlvObject
     && ( outputMode == OutputProcessed8 || outputMode == OutputProcessed16 ) )
    {
        mlvFrameOutputDimensions( m_pMlvObject,
                                  playbackScaleFactor,
                                  &renderedImageWidth,
                                  &renderedImageHeight );
    }
    if( renderedImageWidth <= 0 ) renderedImageWidth = m_imageWidth;
    if( renderedImageHeight <= 0 ) renderedImageHeight = m_imageHeight;
    slot.renderedImageWidth = renderedImageWidth;
    slot.renderedImageHeight = renderedImageHeight;
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_playback_scale_factor_request"),
        playbackScaleFactor );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_playback_active"),
        m_activePresentationContext.playbackActive );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_drop_frame_playback_active"),
        m_activePresentationContext.dropFramePlaybackActive );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_queued_playback_drops_before_start"),
        m_activeQueuedPlaybackDropCount );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_rendered_width"),
        renderedImageWidth );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_rendered_height"),
        renderedImageHeight );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_playback_preview_fast_path"),
        playbackPreviewFastPathActive );

    const int generalWorkerThreads = mlvappEffectiveWorkerThreadCount();
    const int workerThreads = mlvappEffectivePlaybackWorkerThreadCount();
    const int openMpThreads = mlvappApplyPlaybackOpenMpThreadCount(workerThreads);
    if( m_pMlvObject )
    {
        setMlvCpuCores( m_pMlvObject, workerThreads );
    }
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_worker_threads"),
        workerThreads );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_worker_thread_cap_active"),
        workerThreads < generalWorkerThreads );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_openmp_threads"),
        openMpThreads );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_openmp_thread_cap_active"),
        openMpThreads < generalWorkerThreads );

    if ( outputMode == OutputProcessed16 && !slot.rawImage16.empty() )
    {
        getMlvProcessedFrame16Scaled( m_pMlvObject,
                                      frameNumber,
                                      slot.rawImage16.data(),
                                      workerThreads,
                                      playbackScaleFactor );
        slot.dualIsoPreviewHistogramMs = llrpGetLastDualIsoPreviewHistogramMilliseconds();
        slot.dualIsoPreviewRegressionMs = llrpGetLastDualIsoPreviewRegressionMilliseconds();
        slot.dualIsoPreviewRowscaleMs = llrpGetLastDualIsoPreviewRowscaleMilliseconds();
        mlv_stage_timing_note("render_thread_draw16", frameNumber, render_start);
    }
    else if ( outputMode == OutputDebayered16 && !slot.rawImage16.empty() )
    {
        bool usedGpuBilinearDebayer = false;
        bool usedGpuAmazeDebayer = false;
        bool renderedDebayeredFrame = false;
        GpuAmazeDebayerBackendTiming gpuAmazeTiming;
        const bool useGpuAmazeTexturePresent =
            m_activePresentationContext.gpuAmazeTexturePresentRequested;
        if ( useGpuAmazeDebayer && m_pMlvObject )
        {
            const int width = getMlvWidth( m_pMlvObject );
            const int height = getMlvHeight( m_pMlvObject );
            const size_t pixelCount = static_cast<size_t>(width) * static_cast<size_t>(height);
            m_gpuAmazeDebayerRawFrame.resize( pixelCount );
            getMlvRawFrameFloat( m_pMlvObject,
                                 frameNumber,
                                 m_gpuAmazeDebayerRawFrame.data() );

            QString gpuReason;
            QString rendererDescription;
            if ( m_pMlvObject->ca_red <= -0.1 || m_pMlvObject->ca_red >= 0.1
              || m_pMlvObject->ca_blue <= -0.1 || m_pMlvObject->ca_blue >= 0.1 )
            {
                gpuReason = QStringLiteral(
                    "GPU AMaZE debayer does not support CA correction yet; falling back to CPU AMaZE");
            }
            else
            {
                wb_convert_info_t wbInfo;
                wb_convert( &wbInfo,
                            m_gpuAmazeDebayerRawFrame.data(),
                            width,
                            height,
                            getMlvBlackLevel( m_pMlvObject ) );
                if ( useGpuAmazeTexturePresent )
                {
                    const GpuAmazeDebayerBackendAvailability availability =
                        gpuAmazeDebayerProbeBackend();
                    slot.stageTimingTelemetry.insert(
                        QStringLiteral("gpu_amaze_texture_present_preflight_available"),
                        availability.available );
                    if ( !availability.rendererDescription.isEmpty() )
                    {
                        slot.stageTimingTelemetry.insert(
                            QStringLiteral("gpu_amaze_texture_present_preflight_renderer"),
                            availability.rendererDescription );
                    }
                    if ( availability.available )
                    {
                        slot.gpuAmazeTextureRawFrame = m_gpuAmazeDebayerRawFrame;
                        slot.gpuAmazeTexturePresentCandidate = true;
                        slot.gpuAmazeTextureWidth = width;
                        slot.gpuAmazeTextureHeight = height;
                        slot.gpuAmazeTextureBlackLevel = getMlvBlackLevel( m_pMlvObject );
                        slot.gpuAmazeTextureWbMultipliers = normalizedWbMultipliers6500();
                        rendererDescription =
                            QStringLiteral("pending CUDA AMaZE post-WB GL texture present");
                        usedGpuAmazeDebayer = true;
                        slot.stageTimingTelemetry.insert(
                            QStringLiteral("gpu_amaze_texture_present_candidate"),
                            true );
                    }
                    else
                    {
                        gpuReason =
                            availability.reason.isEmpty()
                                ? QStringLiteral("GPU AMaZE texture-present backend preflight failed")
                                : QStringLiteral("GPU AMaZE texture-present backend unavailable before GL handoff: %1")
                                      .arg(availability.reason);
                        rendererDescription = availability.rendererDescription;
                        slot.stageTimingTelemetry.insert(
                            QStringLiteral("gpu_amaze_texture_present_candidate"),
                            false );
                    }
                }
                else
                {
                    usedGpuAmazeDebayer =
                        gpuAmazeDebayerApplyGpuOffscreen( m_gpuAmazeDebayerRawFrame.data(),
                                                          slot.rawImage16.data(),
                                                          width,
                                                          height,
                                                          &gpuReason,
                                                          &rendererDescription,
                                                          &gpuAmazeTiming );
                    if ( usedGpuAmazeDebayer )
                    {
                        wb_undo( &wbInfo,
                                 slot.rawImage16.data(),
                                 width,
                                 height,
                                 getMlvBlackLevel( m_pMlvObject ) );
                    }
                }
            }

            if ( usedGpuAmazeDebayer )
            {
                renderedDebayeredFrame = true;
                slot.usedGpuAmazeDebayer = true;
                slot.gpuAmazeFallbackReason.clear();
                slot.gpuAmazeRendererDescription = rendererDescription;
                if ( !m_loggedGpuAmazeSuccess )
                {
                    qInfo() << "GPU AMaZE debayer enabled for the debayered-16 preview path"
                            << "(renderer:"
                            << (rendererDescription.isEmpty() ? QStringLiteral("unknown") : rendererDescription)
                            << ").";
                    m_loggedGpuAmazeSuccess = true;
                }
            }
            else
            {
                slot.usedGpuAmazeDebayer = false;
                slot.gpuAmazeRendererDescription = rendererDescription;
                const QString previousFallbackReason = m_lastGpuAmazeFallbackReason;
                if ( !gpuReason.isEmpty()
                  && gpuReason != previousFallbackReason )
                {
                    qWarning().nospace()
                        << "GPU AMaZE debayer fell back to CPU: "
                        << gpuReason
                        << " (renderer="
                        << (rendererDescription.isEmpty() ? QStringLiteral("unknown") : rendererDescription)
                        << ").";
                }
                slot.gpuAmazeFallbackReason = gpuReason;
            }
        }
        else if ( useGpuBilinearDebayer && m_pMlvObject )
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
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_bilinear_debayer_active"),
            usedGpuBilinearDebayer );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_amaze_debayer_active"),
            usedGpuAmazeDebayer );
        if ( !slot.gpuAmazeFallbackReason.isEmpty() )
        {
            slot.stageTimingTelemetry.insert(
                QStringLiteral("gpu_amaze_debayer_fallback_reason"),
                slot.gpuAmazeFallbackReason );
        }
        if ( !slot.gpuAmazeRendererDescription.isEmpty() )
        {
            slot.stageTimingTelemetry.insert(
                QStringLiteral("gpu_amaze_debayer_renderer"),
                slot.gpuAmazeRendererDescription );
        }
        if ( usedGpuAmazeDebayer && gpuAmazeTiming.available )
        {
            slot.stageTimingTelemetry.insert(
                QStringLiteral("gpu_amaze_debayer_upload_ms"),
                gpuAmazeTiming.uploadMs );
            slot.stageTimingTelemetry.insert(
                QStringLiteral("gpu_amaze_debayer_kernel_ms"),
                gpuAmazeTiming.kernelMs );
            slot.stageTimingTelemetry.insert(
                QStringLiteral("gpu_amaze_debayer_download_ms"),
                gpuAmazeTiming.downloadMs );
            slot.stageTimingTelemetry.insert(
                QStringLiteral("gpu_amaze_debayer_total_ms"),
                gpuAmazeTiming.totalMs );
        }
        mlv_stage_timing_note("render_thread_draw16_debayered", frameNumber, render_start);
    }
    else if( !slot.rawImage8.empty() )
    {
        bool renderedFromPhase3Raw = false;
        if( decodedRawFrame )
        {
            if( decodedRawFrameAlreadyReconned )
            {
                renderedFromPhase3Raw =
                    getMlvProcessedFrame8ScaledFromReconnedRaw16(
                        m_pMlvObject,
                        frameNumber,
                        decodedRawFrame,
                        slot.rawImage8.data(),
                        workerThreads,
                        playbackScaleFactor ) != 0;
            }
            else
            {
                renderedFromPhase3Raw =
                    getMlvProcessedFrame8ScaledFromRaw16( m_pMlvObject,
                                                          frameNumber,
                                                          decodedRawFrame,
                                                          slot.rawImage8.data(),
                                                          workerThreads,
                                                          playbackScaleFactor ) != 0;
            }
        }
        if( !renderedFromPhase3Raw )
        {
            getMlvProcessedFrame8Scaled( m_pMlvObject,
                                         frameNumber,
                                         slot.rawImage8.data(),
                                         workerThreads,
                                         playbackScaleFactor );
        }
        slot.stageTimingTelemetry.insert(
            QStringLiteral("phase3_decoded_raw_consumed"),
            decodedRawFrame && !decodedRawFrameAlreadyReconned && renderedFromPhase3Raw );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("phase3_reconned_raw_consumed"),
            decodedRawFrame && decodedRawFrameAlreadyReconned && renderedFromPhase3Raw );
        slot.dualIsoPreviewHistogramMs = llrpGetLastDualIsoPreviewHistogramMilliseconds();
        slot.dualIsoPreviewRegressionMs = llrpGetLastDualIsoPreviewRegressionMilliseconds();
        slot.dualIsoPreviewRowscaleMs = llrpGetLastDualIsoPreviewRowscaleMilliseconds();
        mlv_stage_timing_note("render_thread_draw", frameNumber, render_start);
    }

    slot.stageTimingTelemetry.insert(
        QStringLiteral("gpu_playback_recon_env_enabled"),
        gpuPlaybackReconEnvRequested() );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("gpu_playback_recon_attempted"),
        llrpGpuPlaybackReconLastRunAttemptedForTesting() != 0 );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("gpu_playback_recon_used"),
        llrpGpuPlaybackReconLastUsedForTesting() != 0 );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("gpu_playback_recon_state_valid"),
        llrpGpuPlaybackReconLastStateValidForTesting() != 0 );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("gpu_playback_recon_rc"),
        llrpGpuPlaybackReconLastRunRcForTesting() );
    {
        const size_t fullResPixelCount =
            static_cast<size_t>( qMax( 0, m_imageWidth ) )
            * static_cast<size_t>( qMax( 0, m_imageHeight ) );
        const bool readbackBayerCandidate =
            m_activePresentationContext.gpuPlaybackReconTexturePresentRequested
            && decodedRawFrameAlreadyReconned
            && playbackScaleFactor == 1
            && m_imageWidth > 0
            && m_imageHeight > 0
            && slot.rawImage16.size() >= fullResPixelCount;
        /* The no-readback GL R16 texture is the RECON-ONLY Dual ISO bayer; the
         * CUDA backend has no focus/bad-pixel code. Eligibility is decided
         * AUTHORITATIVELY in the C worker on an EFFECTIVENESS basis: it stores the
         * no-readback input bayer, then RETRACTS it if the post-recon focus/bad-pixel
         * interpolation actually mutated the recon output (llrawproc.c ~2553). When
         * retracted, slot.gpuPlaybackReconTextureInputBayerFrame is empty below, so
         * noReadbackCandidate becomes false and we fall back to the readback bayer
         * (slot.rawImage16, which DOES include the fixes -> display stays correct).
         * postReconRawFixActive here is a DIAGNOSTIC of the mode flags only
         * (bad_pixels defaults to 1); it is NOT the gate, because a mode that is On
         * but fixes no pixels is still no-readback-eligible. */
        const bool postReconRawFixActive =
            m_pMlvObject
            && m_pMlvObject->llrawproc
            && ( m_pMlvObject->llrawproc->focus_pixels != 0
              || m_pMlvObject->llrawproc->bad_pixels != 0 );
        const bool noReadbackCandidate =
            m_activePresentationContext.gpuPlaybackReconTexturePresentRequested
            && playbackScaleFactor == 1
            && m_imageWidth > 0
            && m_imageHeight > 0
            && slot.gpuPlaybackReconTextureNoReadbackCandidate
            && slot.gpuPlaybackReconTextureInputBayerFrame.size() >= fullResPixelCount
            && slot.gpuPlaybackReconTextureBayerFrame.size() >= fullResPixelCount
            && slot.gpuPlaybackReconTextureState.valid
            && slot.gpuPlaybackReconTextureState.width == m_imageWidth
            && slot.gpuPlaybackReconTextureState.height == m_imageHeight;
        slot.gpuPlaybackReconTextureNoReadbackCandidate = noReadbackCandidate;
        slot.gpuPlaybackReconTexturePresentCandidate =
            readbackBayerCandidate || noReadbackCandidate;
        slot.gpuPlaybackReconTextureWidth =
            slot.gpuPlaybackReconTexturePresentCandidate ? m_imageWidth : 0;
        slot.gpuPlaybackReconTextureHeight =
            slot.gpuPlaybackReconTexturePresentCandidate ? m_imageHeight : 0;
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_playback_recon_texture_present_readback_bayer_candidate"),
            readbackBayerCandidate );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_playback_recon_texture_present_no_readback_candidate"),
            noReadbackCandidate );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("gpu_playback_recon_texture_present_post_recon_raw_fix_active"),
            postReconRawFixActive );
        if( slot.gpuPlaybackReconTexturePresentCandidate )
        {
            slot.stageTimingTelemetry.insert(
                QStringLiteral("gpu_playback_recon_texture_present_source"),
                noReadbackCandidate
                    ? QStringLiteral("cuda_gl_r16_pending")
                    : QStringLiteral("cpu16_reconstructed_bayer") );
        }
    }

    int playbackScaleFactorActive = playbackScaleFactor;
    if( m_pMlvObject
     && ( outputMode == OutputProcessed8 || outputMode == OutputProcessed16 ) )
    {
        const int coreActiveScale = m_pMlvObject->playback_scale_factor_active;
        if( coreActiveScale == 1 || coreActiveScale == 2 || coreActiveScale == 4 || coreActiveScale == 8 )
        {
            playbackScaleFactorActive = coreActiveScale;
        }
    }
    slot.playbackScaleFactorActive = playbackScaleFactorActive;
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_playback_scale_factor_effective"),
        playbackScaleFactorActive );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_playback_scale_factor_clamped"),
        playbackScaleFactorActive != playbackScaleFactor );
    const bool processed8CacheHit = getMlvLastProcessed8CacheHit() != 0;
    const bool processed8PrefetchHit = getMlvLastProcessed8PrefetchHit() != 0;
    const int processed8CacheHitScale = getMlvLastProcessed8CacheHitScaleFactor();
    const int phase4bPath = playbackScaleFactorActive > 1
                           ? mlv_phase4bv2_last_path_taken()
                           : 0;
    const int phase4bCropRows = playbackScaleFactorActive > 1
                               ? mlv_phase4bv3_last_y_crop_rows()
                               : 0;
    const int sourceWidth = qMax( 0, m_imageWidth );
    const int sourceHeight = qMax( 0, m_imageHeight );
    const int renderedWidth = qMax( 0, renderedImageWidth );
    const int renderedHeight = qMax( 0, renderedImageHeight );
    const qint64 sourcePixels = stagePixelCount( sourceWidth, sourceHeight );
    const qint64 renderedPixels = stagePixelCount( renderedWidth, renderedHeight );
    const double llrawprocPreDualIsoFixMs =
        ( m_pMlvObject && m_pMlvObject->llrawproc )
            ? m_pMlvObject->llrawproc->playback_pre_dualiso_fix_ms
            : 0.0;
    const bool preDualIsoFixActive = llrawprocPreDualIsoFixMs > 0.0;
    const int preDualIsoFixWidth = preDualIsoFixActive ? sourceWidth : 0;
    const int preDualIsoFixHeight = preDualIsoFixActive ? sourceHeight : 0;
    QString phase4bFallbackReason = QStringLiteral("none");
    const bool aggressivePreview = ( mlvPlaybackAggressivePreviewMode() != 0 );
    const bool phase4bReducedPath =
        phase4bPath == 8 || phase4bPath == 4 || phase4bPath == 3 || phase4bPath == 2;
    bool skipFocusPixels = false;
    bool skipBadPixels = false;
    bool skipVerticalStripes = false;
    bool skipPatternNoise = false;
    if( m_pMlvObject && m_pMlvObject->llrawproc )
    {
        skipFocusPixels = m_pMlvObject->llrawproc->focus_pixels != 0;
        skipBadPixels = m_pMlvObject->llrawproc->bad_pixels != 0;
        skipVerticalStripes = m_pMlvObject->llrawproc->vertical_stripes != 0;
        skipPatternNoise = m_pMlvObject->llrawproc->pattern_noise != 0;
    }
    const bool skippedScaledRawCoordinateFixes =
        aggressivePreview
        && phase4bReducedPath
        && ( skipFocusPixels
          || skipBadPixels
          || skipVerticalStripes
          || skipPatternNoise );
    if( playbackScaleFactorActive > 1 && phase4bPath == 0 )
    {
        const char *reason = mlv_phase4bv2_last_fallback_reason();
        phase4bFallbackReason =
            reason && *reason
                ? QString::fromUtf8( reason )
                : QStringLiteral("unknown");
    }
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_phase4b_path"),
        phase4bPath );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_phase4b_path_label"),
        phase4bPathLabel( phase4bPath ) );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_phase4b_path_source"),
        processed8CacheHit
            ? QStringLiteral("processed8_cache")
            : QStringLiteral("render_thread") );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_phase4b_y_crop_rows"),
        phase4bCropRows );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_phase4b_fallback_reason"),
        phase4bFallbackReason );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_scaled_raw_coordinate_fixes_skipped"),
        skippedScaledRawCoordinateFixes );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_scaled_skip_focus_pixels"),
        skippedScaledRawCoordinateFixes && skipFocusPixels );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_scaled_skip_bad_pixels"),
        skippedScaledRawCoordinateFixes && skipBadPixels );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_scaled_skip_vertical_stripes"),
        skippedScaledRawCoordinateFixes && skipVerticalStripes );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_scaled_skip_pattern_noise"),
        skippedScaledRawCoordinateFixes && skipPatternNoise );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_preview_mode"),
        aggressivePreview
            ? QStringLiteral("aggressive_performance")
            : QStringLiteral("sharp_smooth") );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_aggressive_preview"),
        aggressivePreview );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_playback_scale_sensor_pixels"),
        sourcePixels );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_playback_scale_output_pixels"),
        renderedPixels );
    slot.stageTimingTelemetry.insert(
        QStringLiteral("render_thread_playback_scale_pixel_retention_ratio"),
        (sourcePixels > 0)
            ? static_cast<double>( renderedPixels ) / static_cast<double>( sourcePixels )
            : 0.0 );

    const bool processedOutputMode =
        outputMode == OutputProcessed8 || outputMode == OutputProcessed16;
    int bayerReductionInputWidth = 0;
    int bayerReductionInputHeight = 0;
    int bayerReductionOutputWidth = 0;
    int bayerReductionOutputHeight = 0;
    int llrawprocWidth = processedOutputMode ? sourceWidth : 0;
    int llrawprocHeight = processedOutputMode ? sourceHeight : 0;
    int rgbStageInputWidth = processedOutputMode ? sourceWidth : 0;
    int rgbStageInputHeight = processedOutputMode ? sourceHeight : 0;
    const int rgbStageOutputWidth = processedOutputMode ? renderedWidth : 0;
    const int rgbStageOutputHeight = processedOutputMode ? renderedHeight : 0;
    const int processingWidth = processedOutputMode ? renderedWidth : 0;
    const int processingHeight = processedOutputMode ? renderedHeight : 0;

    if( processedOutputMode )
    {
        if( phase4bPath == 8 )
        {
            const int effectiveHeight = qMax( 0, sourceHeight - phase4bCropRows );
            bayerReductionInputWidth = sourceWidth;
            bayerReductionInputHeight = effectiveHeight;
            bayerReductionOutputWidth = sourceWidth / 8;
            bayerReductionOutputHeight = effectiveHeight / 8;
        }
        else if( phase4bPath == 4 )
        {
            const int effectiveHeight = qMax( 0, sourceHeight - phase4bCropRows );
            bayerReductionInputWidth = sourceWidth;
            bayerReductionInputHeight = effectiveHeight;
            bayerReductionOutputWidth = sourceWidth / 2;
            bayerReductionOutputHeight = effectiveHeight / 2;
        }
        else if( phase4bPath == 3 )
        {
            const int effectiveHeight = qMax( 0, sourceHeight - phase4bCropRows );
            bayerReductionInputWidth = sourceWidth;
            bayerReductionInputHeight = effectiveHeight;
            bayerReductionOutputWidth = sourceWidth / 4;
            bayerReductionOutputHeight = effectiveHeight / 4;
        }
        else if( phase4bPath == 2 )
        {
            bayerReductionInputWidth = sourceWidth;
            bayerReductionInputHeight = sourceHeight;
            bayerReductionOutputWidth = sourceWidth / 4;
            bayerReductionOutputHeight = sourceHeight;
        }

        if( phase4bPath == 8 || phase4bPath == 4 || phase4bPath == 3 || phase4bPath == 2 )
        {
            llrawprocWidth = bayerReductionOutputWidth;
            llrawprocHeight = bayerReductionOutputHeight;
            rgbStageInputWidth = bayerReductionOutputWidth;
            rgbStageInputHeight = bayerReductionOutputHeight;
        }
    }

    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_raw_decode"),
        QStringLiteral("raw_bayer"),
        processedOutputMode ? sourceWidth : 0,
        processedOutputMode ? sourceHeight : 0,
        sourcePixels );
    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_pre_dualiso_fix"),
        QStringLiteral("raw_bayer"),
        preDualIsoFixWidth,
        preDualIsoFixHeight,
        sourcePixels );
    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_bayer_reduction_input"),
        QStringLiteral("raw_bayer"),
        bayerReductionInputWidth,
        bayerReductionInputHeight,
        sourcePixels );
    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_bayer_reduction_output"),
        QStringLiteral("raw_bayer"),
        bayerReductionOutputWidth,
        bayerReductionOutputHeight,
        sourcePixels );
    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_llrawproc"),
        QStringLiteral("reconstructed_bayer"),
        llrawprocWidth,
        llrawprocHeight,
        sourcePixels );
    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_rgb_input"),
        QStringLiteral("reconstructed_bayer"),
        rgbStageInputWidth,
        rgbStageInputHeight,
        sourcePixels );
    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_rgb_output"),
        QStringLiteral("rgb"),
        rgbStageOutputWidth,
        rgbStageOutputHeight,
        sourcePixels );
    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_processing"),
        QStringLiteral("processed_rgb"),
        processingWidth,
        processingHeight,
        sourcePixels );
    insertStageResolutionTelemetry(
        slot.stageTimingTelemetry,
        QStringLiteral("render_thread_stage_presentation_input"),
        QStringLiteral("processed_rgb"),
        renderedWidth,
        renderedHeight,
        sourcePixels );
    if( m_lastLoggedPlaybackScaleFactorRequest != playbackScaleFactor
     || m_lastLoggedPlaybackScaleFactorActive != playbackScaleFactorActive )
    {
        qInfo().nospace()
            << "Playback scale effective: requested=x"
            << playbackScaleFactor
            << " active=x"
            << playbackScaleFactorActive
            << " rendered="
            << renderedImageWidth
            << "x"
            << renderedImageHeight
            << " target="
            << m_activePresentationPreparationOptions.targetWidth
            << "x"
            << m_activePresentationPreparationOptions.targetHeight
            << " clamped="
            << (playbackScaleFactorActive != playbackScaleFactor)
            << " phase4b_path="
            << phase4bPath
            << " outputMode="
            << static_cast<int>( outputMode );
        m_lastLoggedPlaybackScaleFactorRequest = playbackScaleFactor;
        m_lastLoggedPlaybackScaleFactorActive = playbackScaleFactorActive;
    }

    slot.playbackFastScaleActive = false;
    slot.playbackScaledWidth = 0;
    slot.playbackScaledHeight = 0;
    slot.playbackScaledBytesPerLine = 0;
    if( outputMode == OutputProcessed8
     && m_activePresentationPreparationOptions.fastPlaybackScale
     && !slot.rawImage8.empty()
     && m_activePresentationPreparationOptions.targetWidth > 0
     && m_activePresentationPreparationOptions.targetHeight > 0 )
    {
        const double playbackScaleStart = mlv_stage_timing_now();

        /* Phase 4D: when the render path returns a downsampled buffer
         * (Phase 4B's scale=2/4/8 path), nearest-neighbour presentation makes
         * the lower-res preview look blocky and aliased. Use bilinear for any
         * playback-scaled buffer that needs a final presentation resize; keep
         * nearest only for the scaleFactor==1 path so full-res playback stays
         * byte-identical. */
        const int sourceWidthForScaler = renderedImageWidth;
        const int sourceHeightForScaler = renderedImageHeight;
        const bool upscaling =
            playbackScaleFactorActive > 1
         && sourceWidthForScaler < m_activePresentationPreparationOptions.targetWidth
         && sourceHeightForScaler < m_activePresentationPreparationOptions.targetHeight;
        const bool presentationResize =
            sourceWidthForScaler != m_activePresentationPreparationOptions.targetWidth
         || sourceHeightForScaler != m_activePresentationPreparationOptions.targetHeight;
        const PlaybackPresentationScaleResampler presentationScaleResampler =
            playbackChoosePresentationScaleResampler( playbackScaleFactorActive,
                                                      phase4bPath,
                                                      upscaling,
                                                      presentationResize,
                                                      aggressivePreview );
        const double stretchX =
            (sourceWidthForScaler > 0)
                ? static_cast<double>( m_activePresentationPreparationOptions.targetWidth )
                  / static_cast<double>( sourceWidthForScaler )
                : 0.0;
        const double stretchY =
            (sourceHeightForScaler > 0)
                ? static_cast<double>( m_activePresentationPreparationOptions.targetHeight )
                  / static_cast<double>( sourceHeightForScaler )
                : 0.0;
        slot.stageTimingTelemetry.insert(
            QStringLiteral("render_thread_playback_scale_target_width"),
            m_activePresentationPreparationOptions.targetWidth );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("render_thread_playback_scale_target_height"),
            m_activePresentationPreparationOptions.targetHeight );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("render_thread_playback_scale_upscaling"),
            upscaling );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("render_thread_playback_scale_source_to_target_x"),
            stretchX );
        slot.stageTimingTelemetry.insert(
            QStringLiteral("render_thread_playback_scale_source_to_target_y"),
            stretchY );
        bool bilinearUsed = false;
        bool cubicUsed = false;
        const int targetBytesPerLine =
            ((m_activePresentationPreparationOptions.targetWidth * 3) + 3) & ~3;

        if( presentationScaleResampler == PlaybackPresentationScaleResampler::Cubic )
        {
            slot.playbackFastScaleActive =
                playbackBuildCubicScaledRgb8( slot.rawImage8.data(),
                                              sourceWidthForScaler,
                                              sourceHeightForScaler,
                                              m_activePresentationPreparationOptions.targetWidth,
                                              m_activePresentationPreparationOptions.targetHeight,
                                              slot.playbackScaledImage8,
                                              m_playbackCubicScaleCache,
                                              targetBytesPerLine );
            cubicUsed = slot.playbackFastScaleActive;
        }
        if( !slot.playbackFastScaleActive
         && presentationScaleResampler == PlaybackPresentationScaleResampler::Bilinear )
        {
            slot.playbackFastScaleActive =
                playbackBuildBilinearScaledRgb8( slot.rawImage8.data(),
                                                 sourceWidthForScaler,
                                                 sourceHeightForScaler,
                                                 m_activePresentationPreparationOptions.targetWidth,
                                                 m_activePresentationPreparationOptions.targetHeight,
                                                 slot.playbackScaledImage8,
                                                 m_playbackBilinearScaleCache,
                                                 targetBytesPerLine );
            bilinearUsed = slot.playbackFastScaleActive;
        }
        if( !slot.playbackFastScaleActive )
        {
            slot.playbackFastScaleActive =
                playbackBuildFastScaledRgb8( slot.rawImage8.data(),
                                             sourceWidthForScaler,
                                             sourceHeightForScaler,
                                             m_activePresentationPreparationOptions.targetWidth,
                                             m_activePresentationPreparationOptions.targetHeight,
                                             slot.playbackScaledImage8,
                                             m_playbackScaleCache,
                                             targetBytesPerLine );
        }
        if( slot.playbackFastScaleActive )
        {
            slot.playbackScaledWidth = m_activePresentationPreparationOptions.targetWidth;
            slot.playbackScaledHeight = m_activePresentationPreparationOptions.targetHeight;
            slot.playbackScaledBytesPerLine = targetBytesPerLine;
        }
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_active"),
                                          slot.playbackFastScaleActive );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_bilinear"),
                                          bilinearUsed );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_cubic"),
                                          cubicUsed );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_resampler"),
                                          cubicUsed
                                              ? QStringLiteral("cubic")
                                              : (bilinearUsed
                                                     ? QStringLiteral("bilinear")
                                                     : (slot.playbackFastScaleActive
                                                            ? QStringLiteral("nearest")
                                                            : QStringLiteral("none"))) );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_ms"),
                                          (mlv_stage_timing_now() - playbackScaleStart) * 1000.0 );
    }
    else
    {
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_target_width"),
                                          0 );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_target_height"),
                                          0 );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_upscaling"),
                                          false );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_source_to_target_x"),
                                          0.0 );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_source_to_target_y"),
                                          0.0 );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_active"),
                                          false );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_bilinear"),
                                          false );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_cubic"),
                                          false );
        slot.stageTimingTelemetry.insert( QStringLiteral("render_thread_playback_scale_resampler"),
                                          QStringLiteral("none") );
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
    const quint64 rawUint16PrefetchDecodeFailures =
        static_cast<quint64>( getMlvRawUint16PrefetchDecodeFailures( m_pMlvObject ) );
    const double llrawprocMs = getMlvLastLlrawprocMilliseconds();
    const double llrawprocDarkFrameMs = llrpGetLastDarkFrameMilliseconds();
    const double llrawprocVerticalStripesMs = llrpGetLastVerticalStripesMilliseconds();
    const double llrawprocFocusPixelsMs = llrpGetLastFocusPixelsMilliseconds();
    const double llrawprocBadPixelsMs = llrpGetLastBadPixelsMilliseconds();
    const double llrawprocPatternNoiseMs = llrpGetLastPatternNoiseMilliseconds();
    const double llrawprocDualIsoMs = llrpGetLastDualIsoMilliseconds();
    dualiso_full20bit_timing_t dualIsoFull20 = {};
    llrpGetLastDualIsoFull20bitTiming( &dualIsoFull20 );
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
    const double processingShadowsHighlightsResizeMs =
        processingGetLastShadowsHighlightsResizeMilliseconds();
    const double processingShadowsHighlightsCopyMs =
        processingGetLastShadowsHighlightsCopyMilliseconds();
    const double processingShadowsHighlightsFilterMs =
        processingGetLastShadowsHighlightsFilterMilliseconds();
    const double processingShadowsHighlightsFilterFullresMs =
        processingGetLastShadowsHighlightsFilterFullresMilliseconds();
    const double processingShadowsHighlightsFilterHalfresDownsampleMs =
        processingGetLastShadowsHighlightsFilterHalfresDownsampleMilliseconds();
    const double processingShadowsHighlightsFilterHalfresRbfMs =
        processingGetLastShadowsHighlightsFilterHalfresRbfMilliseconds();
    const double processingShadowsHighlightsFilterHalfresUpsampleMs =
        processingGetLastShadowsHighlightsFilterHalfresUpsampleMilliseconds();
    const double processingShadowsHighlightsFilterQuarterresDownsampleMs =
        processingGetLastShadowsHighlightsFilterQuarterresDownsampleMilliseconds();
    const double processingShadowsHighlightsFilterQuarterresRbfMs =
        processingGetLastShadowsHighlightsFilterQuarterresRbfMilliseconds();
    const double processingShadowsHighlightsFilterQuarterresUpsampleMs =
        processingGetLastShadowsHighlightsFilterQuarterresUpsampleMilliseconds();
    const double processingShadowsHighlightsRbfTotalMs =
        processingGetLastShadowsHighlightsRbfTotalMilliseconds();
    const double processingShadowsHighlightsRbfBoundaryMs =
        processingGetLastShadowsHighlightsRbfBoundaryMilliseconds();
    const double processingShadowsHighlightsRbfRangeTableMs =
        processingGetLastShadowsHighlightsRbfRangeTableMilliseconds();
    const double processingShadowsHighlightsRbfLeftMs =
        processingGetLastShadowsHighlightsRbfLeftMilliseconds();
    const double processingShadowsHighlightsRbfRightMs =
        processingGetLastShadowsHighlightsRbfRightMilliseconds();
    const double processingShadowsHighlightsRbfHorizontalAverageMs =
        processingGetLastShadowsHighlightsRbfHorizontalAverageMilliseconds();
    const double processingShadowsHighlightsRbfVerticalDownMs =
        processingGetLastShadowsHighlightsRbfVerticalDownMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpFirstLineMs =
        processingGetLastShadowsHighlightsRbfVerticalUpFirstLineMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpBodyMs =
        processingGetLastShadowsHighlightsRbfVerticalUpBodyMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpBodyDiffMs =
        processingGetLastShadowsHighlightsRbfVerticalUpBodyDiffMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpBodyStoreMs =
        processingGetLastShadowsHighlightsRbfVerticalUpBodyStoreMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpBodyStoreFactorMs =
        processingGetLastShadowsHighlightsRbfVerticalUpBodyStoreFactorMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpBodyStoreColorMs =
        processingGetLastShadowsHighlightsRbfVerticalUpBodyStoreColorMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpBodyStoreColorSrcMs =
        processingGetLastShadowsHighlightsRbfVerticalUpBodyStoreColorSrcMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpBodyStoreColorPrevMs =
        processingGetLastShadowsHighlightsRbfVerticalUpBodyStoreColorPrevMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpBodyStoreColorAssignMs =
        processingGetLastShadowsHighlightsRbfVerticalUpBodyStoreColorAssignMilliseconds();
    const double processingShadowsHighlightsRbfVerticalUpMs =
        processingGetLastShadowsHighlightsRbfVerticalUpMilliseconds();
    const double processingShadowsHighlightsRbfOutputMs =
        processingGetLastShadowsHighlightsRbfOutputMilliseconds();
    const double processingHighestGreenMs =
        processingGetLastHighestGreenMilliseconds();
    const double processingCoreMs = processingGetLastCoreMilliseconds();
    const double processingDenoiseMs = processingGetLastDenoiseMilliseconds();
    const double processingRbfMs = processingGetLastRbfMilliseconds();
    const double processingCaMs = processingGetLastCaMilliseconds();
    const double processingChromaMs = processingGetLastChromaMilliseconds();
    const double processingSharpenMs = processingGetLastSharpenMilliseconds();
    const double processingGrainMs = processingGetLastGrainMilliseconds();
    const double processingKnownMs =
        processingSetupMs +
        processingShadowsHighlightsPrepMs +
        processingHighestGreenMs +
        processingCoreMs +
        processingDenoiseMs +
        processingRbfMs +
        processingCaMs +
        processingChromaMs +
        processingSharpenMs +
        processingGrainMs;
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
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_prefetch_decode_failures"),
                                      static_cast<qint64>( rawUint16PrefetchDecodeFailures ) );
    slot.stageTimingTelemetry.insert( QStringLiteral("raw_uint16_other_ms"),
                                      rawUint16OtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_ms"),
                                      llrawprocMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("llrawproc_pre_dualiso_fix_ms"),
                                      llrawprocPreDualIsoFixMs );
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
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_total_ms"),
                                      dualIsoFull20.total_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_pattern_ms"),
                                      dualIsoFull20.pattern_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_noise_ms"),
                                      dualIsoFull20.noise_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_scratch_ms"),
                                      dualIsoFull20.scratch_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_convert20_ms"),
                                      dualIsoFull20.convert20_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_match_ms"),
                                      dualIsoFull20.match_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_ms"),
                                      dualIsoFull20.interp_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_mean23_ms"),
                                      dualIsoFull20.interp_mean23_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_ms"),
                                      dualIsoFull20.interp_amaze_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_scratch_ms"),
                                      dualIsoFull20.interp_amaze_scratch_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_clear_ms"),
                                      dualIsoFull20.interp_amaze_clear_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_squeeze_ms"),
                                      dualIsoFull20.interp_amaze_squeeze_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_rawdata_ms"),
                                      dualIsoFull20.interp_amaze_rawdata_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_demosaic_ms"),
                                      dualIsoFull20.interp_amaze_demosaic_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_demosaic_setup_ms"),
                                      dualIsoFull20.interp_amaze_demosaic_setup_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_demosaic_create_ms"),
                                      dualIsoFull20.interp_amaze_demosaic_create_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_demosaic_join_ms"),
                                      dualIsoFull20.interp_amaze_demosaic_join_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_demosaic_worker_total_ms"),
                                      dualIsoFull20.interp_amaze_demosaic_worker_total_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_demosaic_worker_max_ms"),
                                      dualIsoFull20.interp_amaze_demosaic_worker_max_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_demosaic_worker_count"),
                                      dualIsoFull20.interp_amaze_demosaic_worker_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_postprocess_ms"),
                                      dualIsoFull20.interp_amaze_postprocess_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_grayscale_ms"),
                                      dualIsoFull20.interp_amaze_grayscale_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_init_ms"),
                                      dualIsoFull20.interp_amaze_edge_init_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_lut_ms"),
                                      dualIsoFull20.interp_amaze_lut_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_direction_ms"),
                                      dualIsoFull20.interp_amaze_edge_direction_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_simd_batches"),
                                      dualIsoFull20.interp_amaze_edge_simd_batches );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_allskip_batches"),
                                      dualIsoFull20.interp_amaze_edge_allskip_batches );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_mixed_batches"),
                                      dualIsoFull20.interp_amaze_edge_mixed_batches );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_fullsearch_batches"),
                                      dualIsoFull20.interp_amaze_edge_fullsearch_batches );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_fullsearch_pixels"),
                                      dualIsoFull20.interp_amaze_edge_fullsearch_pixels );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_skip_pixels"),
                                      dualIsoFull20.interp_amaze_edge_skip_pixels );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_edge_scalar_pixels"),
                                      dualIsoFull20.interp_amaze_edge_scalar_pixels );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_amaze_actual_interp_ms"),
                                      dualIsoFull20.interp_amaze_actual_interp_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_border_ms"),
                                      dualIsoFull20.interp_border_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_fullres_ms"),
                                      dualIsoFull20.fullres_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_ms"),
                                      dualIsoFull20.mix_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_curve_select_ms"),
                                      dualIsoFull20.mix_curve_select_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_curve_build_ms"),
                                      dualIsoFull20.mix_curve_build_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_curve_float_ms"),
                                      dualIsoFull20.mix_curve_float_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_ev_lut_ms"),
                                      dualIsoFull20.mix_ev_lut_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_halfres_ms"),
                                      dualIsoFull20.mix_halfres_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_halfres_avx2_bulk_ms"),
                                      dualIsoFull20.mix_halfres_avx2_bulk_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_halfres_scalar_tail_ms"),
                                      dualIsoFull20.mix_halfres_scalar_tail_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_ms"),
                                      dualIsoFull20.mix_chroma_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_copy_ms"),
                                      dualIsoFull20.mix_chroma_copy_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_fullres_ms"),
                                      dualIsoFull20.mix_chroma_fullres_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_ms"),
                                      dualIsoFull20.mix_chroma_halfres_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_horiz_probe_ms"),
                                      dualIsoFull20.mix_chroma_horiz_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_vert_probe_ms"),
                                      dualIsoFull20.mix_chroma_vert_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_gather_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_gather_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_arithmetic_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_arithmetic_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_store_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_store_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_store_r_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_store_r_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_store_b_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_store_b_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_store_r_lookup_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_store_r_lookup_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_store_b_lookup_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_store_b_lookup_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_average_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_average_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_non_average_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_non_average_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_non_average_choose_true_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_non_average_choose_true_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_non_average_choose_false_probe_ms"),
                                      dualIsoFull20.mix_chroma_center_non_average_choose_false_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_write_both_count"),
                                      dualIsoFull20.mix_chroma_center_write_both_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_write_r_only_count"),
                                      dualIsoFull20.mix_chroma_center_write_r_only_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_write_b_only_count"),
                                      dualIsoFull20.mix_chroma_center_write_b_only_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_write_none_count"),
                                      dualIsoFull20.mix_chroma_center_write_none_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_use_average_count"),
                                      dualIsoFull20.mix_chroma_center_use_average_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_center_choose_ev_lt_eh_count"),
                                      dualIsoFull20.mix_chroma_center_choose_ev_lt_eh_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_gather_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_gather_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_arithmetic_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_arithmetic_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_r_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_r_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_b_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_b_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_r_lookup_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_r_lookup_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_b_lookup_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_b_lookup_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_r_low_clamp_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_r_low_clamp_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_b_low_clamp_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_b_low_clamp_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_r_high_clamp_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_r_high_clamp_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_store_b_high_clamp_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_store_b_high_clamp_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_average_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_average_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_non_average_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_non_average_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_non_average_choose_true_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_non_average_choose_true_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_non_average_choose_false_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_non_average_choose_false_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_non_average_write_r_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_non_average_write_r_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_non_average_write_b_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_non_average_write_b_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_non_average_write_both_probe_ms"),
                                      dualIsoFull20.mix_chroma_halfres_center_non_average_write_both_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_write_both_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_write_both_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_write_r_only_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_write_r_only_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_write_b_only_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_write_b_only_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_write_none_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_write_none_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_use_average_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_use_average_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_halfres_center_choose_ev_lt_eh_count"),
                                      dualIsoFull20.mix_chroma_halfres_center_choose_ev_lt_eh_count );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_probe_mode"),
                                      dualIsoFull20.mix_chroma_probe_mode );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_chroma_probe_stage"),
                                      dualIsoFull20.mix_chroma_probe_stage );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_halfres_probe_mode"),
                                      dualIsoFull20.mix_halfres_probe_mode );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_alias_map_ms"),
                                      dualIsoFull20.mix_alias_map_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_alias_map_setup_ms"),
                                      dualIsoFull20.mix_alias_map_setup_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_alias_map_init_ms"),
                                      dualIsoFull20.mix_alias_map_init_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_alias_map_copy_ms"),
                                      dualIsoFull20.mix_alias_map_copy_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_alias_map_filter_ms"),
                                      dualIsoFull20.mix_alias_map_filter_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_alias_map_gaussian_ms"),
                                      dualIsoFull20.mix_alias_map_gaussian_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_alias_map_grayscale_ms"),
                                      dualIsoFull20.mix_alias_map_grayscale_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_overexposed_ms"),
                                      dualIsoFull20.mix_overexposed_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_setup_ms"),
                                      dualIsoFull20.final_blend_setup_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_row_kernel_ms"),
                                      dualIsoFull20.final_blend_row_kernel_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_raw2ev_gather_probe_ms"),
                                      dualIsoFull20.final_blend_raw2ev_gather_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_fullres_curve_gather_probe_ms"),
                                      dualIsoFull20.final_blend_fullres_curve_gather_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_ev2raw_store_probe_ms"),
                                      dualIsoFull20.final_blend_ev2raw_store_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_arithmetic_probe_ms"),
                                      dualIsoFull20.final_blend_arithmetic_probe_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_overexposed_density"),
                                      dualIsoFull20.final_blend_overexposed_density );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_cap_clamp_pct"),
                                      dualIsoFull20.final_blend_cap_clamp_pct );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_f_near_0_pct"),
                                      dualIsoFull20.final_blend_f_near_0_pct );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_f_near_1_pct"),
                                      dualIsoFull20.final_blend_f_near_1_pct );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_ms"),
                                      dualIsoFull20.final_blend_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_convert16_ms"),
                                      dualIsoFull20.convert16_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_other_ms"),
                                      dualIsoFull20.other_ms );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_curve_corr_ev"),
                                      dualIsoFull20.mix_curve_corr_ev );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_curve_overlap"),
                                      dualIsoFull20.mix_curve_overlap );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_curve_rebuilt"),
                                      dualIsoFull20.mix_curve_rebuilt != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_mix_curve_global_hit"),
                                      dualIsoFull20.mix_curve_global_hit != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_interp_method"),
                                      dualIsoFull20.interp_method );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_final_blend_probe_mode"),
                                      dualIsoFull20.final_blend_probe_mode );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_use_alias_map"),
                                      dualIsoFull20.use_alias_map != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_use_fullres"),
                                      dualIsoFull20.use_fullres != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_threads"),
                                      dualIsoFull20.threads );
    slot.stageTimingTelemetry.insert( QStringLiteral("dual_iso_full20_valid"),
                                      dualIsoFull20.valid != 0 );
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
    const int debayerEngineMode =
        m_pMlvObject ? doesMlvAlwaysUseAmaze( m_pMlvObject ) : -1;
    const bool debayerBasicU16Avx2Available =
        debayerBasicU16Avx2Active() != 0;
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_engine_mode"),
                                      debayerEngineMode );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_basic_u16_avx2_available"),
                                      debayerBasicU16Avx2Available );
    slot.stageTimingTelemetry.insert( QStringLiteral("debayer_basic_u16_avx2_used"),
                                      debayerEngineMode == 0
                                      && debayerBasicU16Avx2Available );
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
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_resize_ms"),
                                      processingShadowsHighlightsResizeMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_copy_ms"),
                                      processingShadowsHighlightsCopyMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_filter_ms"),
                                      processingShadowsHighlightsFilterMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_filter_fullres_ms"),
                                      processingShadowsHighlightsFilterFullresMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_filter_halfres_downsample_ms"),
                                      processingShadowsHighlightsFilterHalfresDownsampleMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_filter_halfres_rbf_ms"),
                                      processingShadowsHighlightsFilterHalfresRbfMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_filter_halfres_upsample_ms"),
                                      processingShadowsHighlightsFilterHalfresUpsampleMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_filter_quarterres_downsample_ms"),
                                      processingShadowsHighlightsFilterQuarterresDownsampleMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_filter_quarterres_rbf_ms"),
                                      processingShadowsHighlightsFilterQuarterresRbfMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_filter_quarterres_upsample_ms"),
                                      processingShadowsHighlightsFilterQuarterresUpsampleMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_total_ms"),
                                      processingShadowsHighlightsRbfTotalMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_boundary_ms"),
                                      processingShadowsHighlightsRbfBoundaryMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_range_table_ms"),
                                      processingShadowsHighlightsRbfRangeTableMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_left_ms"),
                                      processingShadowsHighlightsRbfLeftMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_right_ms"),
                                      processingShadowsHighlightsRbfRightMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_horizontal_average_ms"),
                                      processingShadowsHighlightsRbfHorizontalAverageMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_down_ms"),
                                      processingShadowsHighlightsRbfVerticalDownMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_first_line_ms"),
                                      processingShadowsHighlightsRbfVerticalUpFirstLineMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_body_ms"),
                                      processingShadowsHighlightsRbfVerticalUpBodyMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_body_diff_ms"),
                                      processingShadowsHighlightsRbfVerticalUpBodyDiffMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_body_store_ms"),
                                      processingShadowsHighlightsRbfVerticalUpBodyStoreMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_body_store_factor_ms"),
                                      processingShadowsHighlightsRbfVerticalUpBodyStoreFactorMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_body_store_color_ms"),
                                      processingShadowsHighlightsRbfVerticalUpBodyStoreColorMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_body_store_color_src_ms"),
                                      processingShadowsHighlightsRbfVerticalUpBodyStoreColorSrcMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_body_store_color_prev_ms"),
                                      processingShadowsHighlightsRbfVerticalUpBodyStoreColorPrevMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_body_store_color_assign_ms"),
                                      processingShadowsHighlightsRbfVerticalUpBodyStoreColorAssignMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_vertical_up_ms"),
                                      processingShadowsHighlightsRbfVerticalUpMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_shadows_highlights_rbf_output_ms"),
                                      processingShadowsHighlightsRbfOutputMs );
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
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_chroma_ms"),
                                      processingChromaMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_sharpen_ms"),
                                      processingSharpenMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_grain_ms"),
                                      processingGrainMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_other_ms"),
                                      processingOtherMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_levels_ms"),
                                      processingCoreLevelsMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_ms"),
                                      processingCoreColorMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_ms"),
                                      processingGetLastCoreColorMainMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_gradient_ms"),
                                      processingGetLastCoreColorGradientMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_ms"),
                                      processingGetLastCoreColorMainPreludeMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_vignette_ms"),
                                      processingGetLastCoreColorMainPreludeVignetteMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_creative_ms"),
                                      processingGetLastCoreColorMainPreludeCreativeMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_creative_shadows_ms"),
                                      processingGetLastCoreColorMainPreludeCreativeShadowsMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_creative_contrast_ms"),
                                      processingGetLastCoreColorMainPreludeCreativeContrastMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_ms"),
                                      processingGetLastCoreColorMainPreludeWbMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_matrix_ms"),
                                      processingGetLastCoreColorMainPreludeWbMatrixMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_matrix_r_ms"),
                                      processingGetLastCoreColorMainPreludeWbMatrixRMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_matrix_g_ms"),
                                      processingGetLastCoreColorMainPreludeWbMatrixGMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_matrix_b_ms"),
                                      processingGetLastCoreColorMainPreludeWbMatrixBMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_gradient_matrix_ms"),
                                      processingGetLastCoreColorMainPreludeWbGradientMatrixMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_exposure_ms"),
                                      processingGetLastCoreColorMainPreludeWbExposureMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_gamut_ms"),
                                      processingGetLastCoreColorMainPreludeWbGamutMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_main_prelude_wb_recon_ms"),
                                      processingGetLastCoreColorMainPreludeWbReconMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_ms"),
                                      processingGetLastCoreColorCamMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_main_ms"),
                                      processingGetLastCoreColorCamMainMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_gradient_ms"),
                                      processingGetLastCoreColorCamGradientMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_wb_ms"),
                                      processingGetLastCoreColorCamWbMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_wb_matrix_ms"),
                                      processingGetLastCoreColorCamWbMatrixMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_wb_gamut_ms"),
                                      processingGetLastCoreColorCamWbGamutMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_wb_desat_ms"),
                                      processingGetLastCoreColorCamWbDesatMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_ms"),
                                      processingGetLastCoreColorCamAgxMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_clip_ms"),
                                      processingGetLastCoreColorCamAgxClipMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_clip_neg_r_count"),
                                      processingGetLastCoreColorCamAgxClipNegRCount() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_clip_neg_g_count"),
                                      processingGetLastCoreColorCamAgxClipNegGCount() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_clip_neg_b_count"),
                                      processingGetLastCoreColorCamAgxClipNegBCount() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_matrix_ms"),
                                      processingGetLastCoreColorCamAgxMatrixMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_matrix_r_ms"),
                                      processingGetLastCoreColorCamAgxMatrixRMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_matrix_g_ms"),
                                      processingGetLastCoreColorCamAgxMatrixGMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_matrix_b_ms"),
                                      processingGetLastCoreColorCamAgxMatrixBMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_matrix_r_hi_count"),
                                      processingGetLastCoreColorCamAgxMatrixRHiCount() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_matrix_g_hi_count"),
                                      processingGetLastCoreColorCamAgxMatrixGHiCount() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_cam_agx_matrix_b_hi_count"),
                                      processingGetLastCoreColorCamAgxMatrixBHiCount() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_gamma_ms"),
                                      processingGetLastCoreColorGammaMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_gamma_main_ms"),
                                      processingGetLastCoreColorGammaMainMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_color_gamma_gradient_ms"),
                                      processingGetLastCoreColorGammaGradientMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_direct8_matrix_ms"),
                                      processingDirect8MatrixMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_direct8_gamma_ms"),
                                      processingDirect8GammaMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_direct8_curves_ms"),
                                      processingDirect8CurvesMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_ms"),
                                      processingCoreCreativeMs );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_hue_vs_ms"),
                                      processingGetLastCoreCreativeHueVsMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_vibrance_ms"),
                                      processingGetLastCoreCreativeVibranceMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_saturation_ms"),
                                      processingGetLastCoreCreativeSaturationMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_toning_ms"),
                                      processingGetLastCoreCreativeToningMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_curve_ms"),
                                      processingGetLastCoreCreativeCurveMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_gradation_ms"),
                                      processingGetLastCoreCreativeGradationMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processing_core_creative_agx_inverse_ms"),
                                      processingGetLastCoreCreativeAgxInverseMilliseconds() );
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
    slot.stageTimingTelemetry.insert( QStringLiteral("processed16_cache_store_ms"),
                                      getMlvLastProcessed16CacheStoreMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_total_ms"),
                                      getMlvLastProcessed8TotalMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_cache_store_ms"),
                                      getMlvLastProcessed8CacheStoreMilliseconds() );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_direct_path_active"),
                                      getMlvLastProcessed8DirectPathActive() != 0 );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_direct_path_reason"),
                                      QString::fromLatin1(
                                          processingGetDirect8IncompatibilityReason(
                                              m_pMlvObject ? m_pMlvObject->processing : nullptr ) ) );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_cache_hit"),
                                      processed8CacheHit );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_cache_hit_scale_factor"),
                                      processed8CacheHitScale );
    slot.stageTimingTelemetry.insert( QStringLiteral("processed8_prefetch_hit"),
                                      processed8PrefetchHit );

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
