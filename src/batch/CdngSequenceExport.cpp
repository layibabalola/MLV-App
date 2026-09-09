#include "CdngSequenceExport.h"
#include "BatchContext.h"
#include "BatchLogger.h"
#include "BatchPrompts.h"
#include "WorkerThreadCount.h"
#include "../../platform/qt/ExportProcess.h"
#include <QByteArray>
#include <QCoreApplication>
#include <QChar>
#include <QDir>
#include <QElapsedTimer>
#include <QFileInfo>
#include <QStandardPaths>
#include <QStorageInfo>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <math.h>

namespace
{
static bool environmentFlagEnabled(const char *name)
{
    const QByteArray value = qgetenv(name).trimmed().toLower();
    return !value.isEmpty() && value != QByteArrayLiteral("0")
        && value != QByteArrayLiteral("false") && value != QByteArrayLiteral("off")
        && value != QByteArrayLiteral("no");
}
static bool cdngPayloadHandoffEnabled()
{ return environmentFlagEnabled("MLVAPP_CDNG_EXPORT_PAYLOAD_HANDOFF"); }
static bool cdngAsyncWriterEnabled()
{ return environmentFlagEnabled("MLVAPP_CDNG_EXPORT_ASYNC_WRITER"); }
static int32_t clampDngInt32( double value )
{
    if( value > 2147483647.0 ) return 2147483647;
    if( value < -2147483648.0 ) return (int32_t)-2147483647 - 1;
    return (int32_t)llround( value );
}

static void setDngAsShotNeutralFromProcessing( dngExportOverrides_t *overrides,
                                               const processingObject_t *processing )
{
    if( !overrides || !processing ) return;

    const double scale = 1000000.0;
    const double multipliers[3] =
    {
        processing->wb_multipliers[0],
        processing->wb_multipliers[1],
        processing->wb_multipliers[2]
    };
    for( int c = 0; c < 3; ++c )
    {
        if( !isfinite( multipliers[c] ) || multipliers[c] <= 0.0 )
        {
            return;
        }
    }

    for( int c = 0; c < 3; ++c )
    {
        overrides->as_shot_neutral[c * 2] = 1000000;
        overrides->as_shot_neutral[c * 2 + 1] =
            qMax( 1, clampDngInt32( multipliers[c] * scale ) );
    }
    overrides->as_shot_neutral_enabled = 1;
}

static dngExportOverrides_t makeLookAssistDngOverrides( mlvObject_t *mlvObject,
                                                        int exposureSlider )
{
    dngExportOverrides_t overrides = {};
    if( !mlvObject ) return overrides;

    overrides.enabled = 1;
    if( mlvObject->llrawproc )
    {
        overrides.black_level_enabled = 1;
        overrides.black_level = mlvObject->llrawproc->dng_black_level;
        overrides.white_level_enabled = 1;
        overrides.white_level = mlvObject->llrawproc->dng_white_level;
    }
    if( exposureSlider != 0 )
    {
        overrides.baseline_exposure_enabled = 1;
        overrides.baseline_exposure[0] = exposureSlider;
        overrides.baseline_exposure[1] = 100;
    }
    setDngAsShotNeutralFromProcessing( &overrides, mlvObject->processing );
    return overrides;
}
}

ProcessResult CdngSequenceExport::exportCdngSequence(
    mlvObject_t *mlvObject,
    const QString &outDir,
    const QString &clipBaseName,
    int codecProfile,
    int codecOption,
    uint32_t cutIn,
    uint32_t cutOut,
    double stretchX,
    double stretchY,
    bool audioExport,
    bool rawFixEnabled,
    ProgressCallback progressCallback,
    bool applyLookAssistDngDefaults,
    int lookAssistExposure)
{
    ProcessResult result;
    QElapsedTimer timer;
    timer.start();
    bool verbose = BatchContext::isVerbose();

    /* --- Prepare mlvObject for raw export --- */
    setMlvAlwaysUseAmaze( mlvObject );
    llrpResetFpmStatus(mlvObject);
    llrpResetBpmStatus(mlvObject);
    llrpComputeStripesOn(mlvObject);
    mlvObject->current_cached_frame_active = 0;
    if( rawFixEnabled || applyLookAssistDngDefaults )
    {
        mlvObject->llrawproc->fix_raw = 1;
    }

    /* Chroma smoothing is a PREVIEW convenience, not raw: it denoises the Bayer in place and bakes
     * into the DNG, which Lightroom can't undo. Never bake it on export (Layi 2026-06-30) -- keep the
     * exported DNGs raw and let Lightroom's non-destructive Color Noise Reduction do the cleanup.
     * Force it off for the export render regardless of the playback/receipt setting (this also skips
     * the dual-ISO recon's chroma cleanup, so dual-ISO DNGs come out as raw recon -> clean in LR).
     * The GUI caller restores the live playback chroma mode afterwards so previews keep smoothing.
     * Escape hatch: set MLVAPP_EXPORT_BAKE_CHROMA_SMOOTH=1 to retain the playback chroma in exports. */
    if( !qEnvironmentVariableIsSet( "MLVAPP_EXPORT_BAKE_CHROMA_SMOOTH" ) )
    {
        llrpSetChromaSmoothMode( mlvObject, 0 );
    }

    /* --- Build subfolder path and naming prefix --- */
    QString pathName = outDir;
    if( codecOption == CODEC_CNDG_DEFAULT )
    {
        pathName = pathName + QStringLiteral("/%1").arg( clipBaseName );
    }
    else
    {
        pathName = pathName + QStringLiteral("/%1_1_%2-%3-%4_0001_C0000")
            .arg( clipBaseName )
            .arg( getMlvTmYear( mlvObject ), 2, 10, QChar('0') )
            .arg( getMlvTmMonth( mlvObject ), 2, 10, QChar('0') )
            .arg( getMlvTmDay( mlvObject ), 2, 10, QChar('0') );
    }

    /* Create output subfolder */
    QDir dir;
    if( !dir.mkpath( pathName ) )
    {
        result.success = false;
        result.errorMessage = QStringLiteral("Failed to create output folder: %1").arg( pathName );
        result.elapsedSeconds = timer.elapsed() / 1000.0;
        return result;
    }

    /* --- Export WAV audio if requested --- */
    if( doesMlvHaveAudio( mlvObject ) && audioExport )
    {
        QString wavFileName = pathName;
        if( codecOption == CODEC_CNDG_DEFAULT )
            wavFileName = wavFileName + QStringLiteral("/%1.wav").arg( clipBaseName );
        else
            wavFileName = wavFileName + QStringLiteral("/%1_1_%2-%3-%4_0001_C0000.wav")
                .arg( clipBaseName )
                .arg( getMlvTmYear( mlvObject ), 2, 10, QChar('0') )
                .arg( getMlvTmMonth( mlvObject ), 2, 10, QChar('0') )
                .arg( getMlvTmDay( mlvObject ), 2, 10, QChar('0') );
#ifdef Q_OS_UNIX
        writeMlvAudioToWaveCut( mlvObject, wavFileName.toUtf8().data(), cutIn, cutOut );
#else
        writeMlvAudioToWaveCut( mlvObject, wavFileName.toLatin1().data(), cutIn, cutOut );
#endif
    }

    /* --- Compute pixel aspect ratio from stretch factors --- */
    int32_t picAR[4] = { 0 };
    if( stretchX == STRETCH_H_125 )      { picAR[0] = 5; picAR[1] = 4; }
    else if( stretchX == STRETCH_H_133 ) { picAR[0] = 4; picAR[1] = 3; }
    else if( stretchX == STRETCH_H_150 ) { picAR[0] = 3; picAR[1] = 2; }
    else if( stretchX == STRETCH_H_167 ) { picAR[0] = 5; picAR[1] = 3; }
    else if( stretchX == STRETCH_H_175 ) { picAR[0] = 7; picAR[1] = 4; }
    else if( stretchX == STRETCH_H_180 ) { picAR[0] = 9; picAR[1] = 5; }
    else if( stretchX == STRETCH_H_200 ) { picAR[0] = 2; picAR[1] = 1; }
    else                                 { picAR[0] = 1; picAR[1] = 1; }

    if( stretchY == STRETCH_V_167 )      { picAR[2] = 5; picAR[3] = 3; }
    else if( stretchY == STRETCH_V_300 ) { picAR[2] = 3; picAR[3] = 1; }
    else if( stretchY == STRETCH_V_033 ) { picAR[2] = 1; picAR[3] = 1; picAR[0] *= 3; }
    else                                 { picAR[2] = 1; picAR[3] = 1; }

    /* --- Init DNG struct --- */
    double fps = getMlvFramerate( mlvObject );
    dngObject_t *cinemaDng = initDngObject( mlvObject, codecProfile - 6, fps, picAR );
    if( !cinemaDng )
    {
        result.success = false;
        result.errorMessage = QStringLiteral("Could not allocate DNG export buffers");
        result.elapsedSeconds = timer.elapsed() / 1000.0;
        return result;
    }

    /* Render one frame for raw correction init */
    const size_t rawWidth = static_cast<size_t>( getMlvWidth( mlvObject ) );
    const size_t rawHeight = static_cast<size_t>( getMlvHeight( mlvObject ) );
    if( rawWidth == 0 || rawHeight == 0
        || rawWidth > SIZE_MAX / rawHeight
        || rawWidth * rawHeight > SIZE_MAX / 3u
        || rawWidth * rawHeight * 3u > SIZE_MAX / sizeof( uint16_t ) )
    {
        freeDngObject( cinemaDng );
        result.success = false;
        result.errorMessage = QStringLiteral("Invalid DNG export dimensions");
        result.elapsedSeconds = timer.elapsed() / 1000.0;
        return result;
    }
    const size_t frameSize = rawWidth * rawHeight * 3u;
    uint16_t *imgBuffer = static_cast<uint16_t *>( malloc( frameSize * sizeof( uint16_t ) ) );
    if( !imgBuffer )
    {
        freeDngObject( cinemaDng );
        result.success = false;
        result.errorMessage = QStringLiteral("Could not allocate DNG processing buffer");
        result.elapsedSeconds = timer.elapsed() / 1000.0;
        return result;
    }
    getMlvProcessedFrame16( mlvObject, 0, imgBuffer, mlvappEffectiveWorkerThreadCount() );
    free( imgBuffer );

    if( applyLookAssistDngDefaults )
    {
        const dngExportOverrides_t overrides =
            makeLookAssistDngOverrides( mlvObject, lookAssistExposure );
        setDngExportOverrides( cinemaDng, &overrides );
    }

    /* --- Frame export loop (cutIn/cutOut are 1-based) --- */
    int totalFrames = cutOut - cutIn + 1;
    bool aborted = false;
    uint64_t lastReportedGpuVramBytes = 0;
    bool hasReportedGpuVramBytes = false;
    const bool usePayloadHandoff = cdngPayloadHandoffEnabled();
    const bool useAsyncWriter = cdngAsyncWriterEnabled();
    dngPayloadWriter_t *payloadWriter = nullptr;
    if( useAsyncWriter )
    {
        payloadWriter = createDngPayloadWriter();
        if( !payloadWriter )
        {
            result.success = false;
            result.errorMessage =
                QStringLiteral("Could not start CDNG payload writer");
            aborted = true;
        }
    }

    for( uint32_t frame = cutIn - 1; frame < cutOut && !aborted; frame++ )
    {
        /* Build frame filename */
        QString dngName;
        if( codecOption == CODEC_CNDG_DEFAULT )
        {
            dngName = QStringLiteral("%1_%2.dng")
                .arg( clipBaseName )
                .arg( getMlvFrameNumber( mlvObject, frame ), 6, 10, QChar('0') );
        }
        else
        {
            dngName = QStringLiteral("%1_1_%2-%3-%4_0001_C0000_%5.dng")
                .arg( clipBaseName )
                .arg( getMlvTmYear( mlvObject ), 2, 10, QChar('0') )
                .arg( getMlvTmMonth( mlvObject ), 2, 10, QChar('0') )
                .arg( getMlvTmDay( mlvObject ), 2, 10, QChar('0') )
                .arg( getMlvFrameNumber( mlvObject, frame ), 6, 10, QChar('0') );
        }

        QString filePathNr = pathName + QStringLiteral("/") + dngName;

        /* Save cDNG frame */
        QString properties_fn = QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation);
#ifdef Q_OS_UNIX
        properties_fn.append("/mlv-dng-params.txt");
        QByteArray filePathBytes = filePathNr.toUtf8();
        QByteArray propertiesBytes = properties_fn.toUtf8();
#else
        properties_fn.append("\\mlv-dng-params.txt");
        QByteArray filePathBytes = filePathNr.toLatin1();
        QByteArray propertiesBytes = properties_fn.toLatin1();
#endif
        int saveErr = 0;
        if( useAsyncWriter )
        {
            saveErr = saveDngFrameViaAsyncPayloadWriter( payloadWriter,
                                                        mlvObject,
                                                        cinemaDng,
                                                        frame,
                                                        filePathBytes.data(),
                                                        propertiesBytes.constData() );
        }
        else if( usePayloadHandoff )
        {
            saveErr = saveDngFrameViaPayload( mlvObject, cinemaDng, frame,
                                              filePathBytes.data(),
                                              propertiesBytes.constData() );
        }
        else
        {
            saveErr = saveDngFrame( mlvObject, cinemaDng, frame,
                                    filePathBytes.data(),
                                    propertiesBytes.constData() );
        }
        if( saveErr )
        {
            /* Frame save failed — BatchPrompts decides skip-or-abort */
            if( BatchPrompts::shouldSkipFrame( clipBaseName, frame, filePathNr ) )
            {
                result.framesSkipped++;
                continue;
            }
            else
            {
                result.success = false;
                result.errorMessage = QStringLiteral("saveDngFrame failed for frame %1 (%2)")
                    .arg( frame ).arg( filePathNr );
                aborted = true;
                break;
            }
        }
        else
        {
            result.framesExported++;
        }

        if( BatchContext::isBatchMode() )
        {
            llrpGpuExportTelemetry_t gpuTelemetry;
            llrpGetLastGpuExportTelemetry( &gpuTelemetry );
            if( gpuTelemetry.attempted
             && gpuTelemetry.allocated_bytes_valid
             && gpuTelemetry.allocated_bytes > 0
             && ( !hasReportedGpuVramBytes
               || lastReportedGpuVramBytes != gpuTelemetry.allocated_bytes ) )
            {
                const double allocatedMb =
                    (double)gpuTelemetry.allocated_bytes / ( 1024.0 * 1024.0 );
                BatchLogger::out(QStringLiteral(
                    "[BATCH] GPU %1 frame=%2 rc=%3 replaced=%4 vramAllocatedMB=%5\n")
                    .arg( clipBaseName )
                    .arg( frame )
                    .arg( gpuTelemetry.rc )
                    .arg( gpuTelemetry.replaced )
                    .arg( allocatedMb, 0, 'f', 1 ));
                lastReportedGpuVramBytes = gpuTelemetry.allocated_bytes;
                hasReportedGpuVramBytes = true;
            }
        }

        /* Check disk space */
        QStorageInfo disk( QFileInfo( filePathNr ).path() );
        if( 20 > disk.bytesAvailable() / 1024 / 1024 )
        {
            if( !BatchPrompts::shouldContinue( clipBaseName,
                    QStringLiteral("Disk full — less than 20 MB remaining") ) )
            {
                result.success = false;
                result.errorMessage = QStringLiteral("Disk full during export");
                aborted = true;
                break;
            }
        }

        /* Progress callback — called once per frame after write/skip */
        if( progressCallback )
        {
            if( !progressCallback( result.framesExported + result.framesSkipped, totalFrames ) )
            {
                aborted = true;
                break;
            }
        }
        else if( BatchContext::isBatchMode() && verbose )
        {
            BatchLogger::out(QStringLiteral("[BATCH] FRAME %1 %2/%3\n")
                       .arg( clipBaseName )
                       .arg( result.framesExported + result.framesSkipped )
                       .arg( totalFrames ));
        }

        /* Let event loop breathe */
        qApp->processEvents();
    }

    if( payloadWriter )
    {
        const int writerErr = finishDngPayloadWriter( payloadWriter );
        payloadWriter = nullptr;
        if( writerErr )
        {
            result.success = false;
            result.errorMessage =
                QStringLiteral("CDNG payload writer failed during async flush");
            aborted = true;
        }
    }

    /* Free DNG struct */
    freeDngObject( cinemaDng );

    if( !aborted )
    {
        result.success = ( result.framesSkipped == 0 )
                         || BatchContext::skipErrors();
    }
    result.elapsedSeconds = timer.elapsed() / 1000.0;
    return result;
}
