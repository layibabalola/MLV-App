#include "../common/minitest.h"
#include "../common/hash_helpers.h"
#include "../common/repo_paths.h"
#include "../common/test_artifacts.h"

#include "../../platform/qt/ReceiptSettings.h"
#include "../../src/batch/ReceiptApplier.h"
#include "../../src/batch/ReceiptLoader.h"
#include "../../src/batch/BatchContext.h"
#include "../../src/batch/BatchLogger.h"
#include "../../src/batch/BatchRunner.h"
#include "../../platform/qt/DualIsoPatternMapping.h"

#include <QDir>
#include <QFile>
#include <QTemporaryDir>

#include <cstdlib>
#include <memory>
#include <string>

static QString repo_receipt_path_for_apply(const QString & name)
{
    return repo_file_path(QStringLiteral("receipts/%1").arg(name));
}

static void seed_runtime_objects(mlvObject_t * video,
                                 llrawprocObject_t * llrawproc,
                                 processingObject_t * processing)
{
    *video = {};
    *llrawproc = {};
    *processing = {};

    video->llrawproc = llrawproc;
    video->processing = processing;
    video->RAWI.raw_info.bits_per_pixel = 14;
    video->RAWI.raw_info.black_level = 2048;
    video->RAWI.raw_info.white_level = 15000;
    video->original_black_level = video->RAWI.raw_info.black_level;
    video->original_white_level = video->RAWI.raw_info.white_level;
    video->IDNT.cameraModel = 0x80000285;
    video->frames = 4;
    video->cached_frames = static_cast<uint8_t *>(std::calloc(static_cast<std::size_t>(video->frames),
                                                              sizeof(*video->cached_frames)));

    pthread_mutex_init(&video->g_mutexFind, nullptr);
}

static void destroy_runtime_objects(mlvObject_t * video)
{
    pthread_mutex_destroy(&video->g_mutexFind);
    std::free(video->cached_frames);
    video->cached_frames = nullptr;
}

TEST(ReceiptApplier, FastProxyReceiptMapsToRuntimeState)
{
    ReceiptSettings receipt;
    QString error_message;
    ASSERT_TRUE(ReceiptLoader::loadFromFile(repo_receipt_path_for_apply(QStringLiteral("FastProxy.marxml")),
                                            &receipt,
                                            &error_message));

    auto video = std::make_unique<mlvObject_t>();
    auto llrawproc = std::make_unique<llrawprocObject_t>();
    auto processing = std::make_unique<processingObject_t>();
    seed_runtime_objects(video.get(), llrawproc.get(), processing.get());

    video->current_cached_frame_active = 1;
    video->current_processed_frame_active = 1;

    ReceiptApplier::applyToMlv(&receipt, video.get(), processing.get());

    ASSERT_EQ(1, llrawproc->fix_raw);
    ASSERT_EQ(1, llrawproc->focus_pixels);
    ASSERT_EQ(0, llrawproc->dual_iso);
    ASSERT_EQ(0, llrawproc->diso_averaging);
    ASSERT_EQ(1, llrawproc->diso_alias_map);
    ASSERT_EQ(1, llrawproc->diso_frblending);
    ASSERT_EQ(2047, video->RAWI.raw_info.black_level);
    ASSERT_EQ(2840, video->RAWI.raw_info.white_level);
    ASSERT_EQ(static_cast<unsigned int>(TR_NONE), static_cast<unsigned int>(processing->transformation));
    ASSERT_EQ(0, video->current_cached_frame_active);
    ASSERT_EQ(0, video->current_processed_frame_active);

    QTemporaryDir temporary_dir;
    const QString log_path = temporary_dir.filePath(QStringLiteral("fingerprint.log"));
    BatchLogger::init(log_path);
    ReceiptApplier::printFingerprint(video.get(), processing.get());
    BatchLogger::shutdown();

    QFile fingerprint_file(log_path);
    ASSERT_TRUE(fingerprint_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QByteArray fingerprint = fingerprint_file.readAll();
    ASSERT_TRUE(fingerprint.contains("dualIso=0"));
    ASSERT_TRUE(fingerprint.contains("rawBlack=2047"));

    test_artifacts::record("receipt_applier.fast_proxy_fingerprint",
                           sha256_bytes(fingerprint.constData(),
                                        static_cast<std::size_t>(fingerprint.size())));

    destroy_runtime_objects(video.get());
}

TEST(ReceiptApplier, PreviewDualIsoModePropagatesToRuntime)
{
    ReceiptSettings receipt;
    receipt.setRawFixesEnabled(true);
    receipt.setFocusPixels(1);
    receipt.setBadPixels(0);
    receipt.setDualIsoForced(0);
    receipt.setDualIso(2);
    receipt.setDualIsoAutoCorrected(1);
    receipt.setDualIsoInterpolation(1);
    receipt.setDualIsoAliasMap(0);
    receipt.setDualIsoFrBlending(0);
    receipt.setDarkFrameEnabled(0);
    receipt.setRawBlack(-1);
    receipt.setRawWhite(-1);

    auto video = std::make_unique<mlvObject_t>();
    auto llrawproc = std::make_unique<llrawprocObject_t>();
    auto processing = std::make_unique<processingObject_t>();
    seed_runtime_objects(video.get(), llrawproc.get(), processing.get());

    ReceiptApplier::applyToMlv(&receipt, video.get(), processing.get());

    ASSERT_EQ(2, llrawproc->dual_iso);
    ASSERT_EQ(1, llrawproc->diso_averaging);
    ASSERT_EQ(0, llrawproc->diso_alias_map);
    ASSERT_EQ(0, llrawproc->diso_frblending);

    const QString runtime_summary = QStringLiteral("dualIso=%1;interp=%2;alias=%3;fullres=%4;")
        .arg(llrawproc->dual_iso)
        .arg(llrawproc->diso_averaging)
        .arg(llrawproc->diso_alias_map)
        .arg(llrawproc->diso_frblending);
    test_artifacts::record("receipt_applier.preview_dual_iso",
                           sha256_qstring(runtime_summary));

    destroy_runtime_objects(video.get());
}

TEST(ReceiptApplier, PreviewDualIsoModeSurvivesWhenAutoCorrectionIsDisabled)
{
    ReceiptSettings receipt;
    receipt.setRawFixesEnabled(true);
    receipt.setFocusPixels(1);
    receipt.setBadPixels(0);
    receipt.setDualIsoForced(DISO_FORCED);
    receipt.setDualIso(2);
    receipt.setDualIsoAutoCorrected(0);
    receipt.setDualIsoInterpolation(1);
    receipt.setDualIsoAliasMap(0);
    receipt.setDualIsoFrBlending(0);
    receipt.setDarkFrameEnabled(0);
    receipt.setRawBlack(-1);
    receipt.setRawWhite(-1);

    auto video = std::make_unique<mlvObject_t>();
    auto llrawproc = std::make_unique<llrawprocObject_t>();
    auto processing = std::make_unique<processingObject_t>();
    seed_runtime_objects(video.get(), llrawproc.get(), processing.get());

    ReceiptApplier::applyToMlv(&receipt, video.get(), processing.get());

    ASSERT_EQ(2, llrawproc->dual_iso);
    ASSERT_EQ(0, llrawproc->diso_pattern);

    destroy_runtime_objects(video.get());
}

TEST(ReceiptApplier, AutoCorrectedDualIsoPatternMapsUiIndexToCorePattern)
{
    for( int uiIndex = 0; uiIndex <= 3; ++uiIndex )
    {
        ReceiptSettings receipt;
        receipt.setRawFixesEnabled(true);
        receipt.setFocusPixels(1);
        receipt.setBadPixels(0);
        receipt.setDualIsoForced(DISO_FORCED);
        receipt.setDualIso(2);
        receipt.setDualIsoAutoCorrected(1);
        receipt.setDualIsoPattern(uiIndex);
        receipt.setDualIsoEvCorrection(0);
        receipt.setDualIsoBlackDelta(0);
        receipt.setDarkFrameEnabled(0);
        receipt.setRawBlack(-1);
        receipt.setRawWhite(-1);

        auto video = std::make_unique<mlvObject_t>();
        auto llrawproc = std::make_unique<llrawprocObject_t>();
        auto processing = std::make_unique<processingObject_t>();
        seed_runtime_objects(video.get(), llrawproc.get(), processing.get());
        llrawproc->diso_validity = DISO_INVALID;
        llrawproc->diso1 = 100;
        llrawproc->diso2 = 1600;

        ReceiptApplier::applyToMlv(&receipt, video.get(), processing.get());

        ASSERT_EQ(dualIsoCorePatternFromUiIndex(uiIndex), llrawproc->diso_pattern);

        destroy_runtime_objects(video.get());
    }
}

TEST(ReceiptApplier, DisabledLookAssistClearsStaleBaselineValid)
{
    ReceiptSettings receipt;
    receipt.setLookAssistEnabled(false);
    receipt.setLookAssistBaselineValid(true);

    auto video = std::make_unique<mlvObject_t>();
    auto llrawproc = std::make_unique<llrawprocObject_t>();
    auto processing = std::make_unique<processingObject_t>();
    seed_runtime_objects(video.get(), llrawproc.get(), processing.get());

    QTemporaryDir temporary_dir;
    const QString log_path = temporary_dir.filePath(QStringLiteral("look-assist.log"));
    BatchLogger::init(log_path);
    const bool applied =
        ReceiptApplier::applyHeadlessLookAssist(&receipt, video.get(), processing.get(), 0);
    BatchLogger::shutdown();

    ASSERT_FALSE(applied);
    ASSERT_FALSE(receipt.lookAssistBaselineValid());

    destroy_runtime_objects(video.get());
}

TEST(ReceiptApplier, ValidDualIsoClipIgnoresCopiedCorrectionState)
{
    ReceiptSettings receipt;
    receipt.setRawFixesEnabled(true);
    receipt.setFocusPixels(1);
    receipt.setBadPixels(0);
    receipt.setDualIsoForced(DISO_VALID);
    receipt.setDualIso(1);
    receipt.setDualIsoAutoCorrected(1);
    receipt.setDualIsoPattern(0);
    receipt.setDualIsoEvCorrection(1);
    receipt.setDualIsoBlackDelta(-1);
    receipt.setDualIsoInterpolation(0);
    receipt.setDualIsoAliasMap(1);
    receipt.setDualIsoFrBlending(1);
    receipt.setDualIsoWhite(64346);
    receipt.setDualIsoBlack(8188);
    receipt.setDarkFrameEnabled(0);
    receipt.setRawBlack(12340);
    receipt.setRawWhite(16200);

    auto video = std::make_unique<mlvObject_t>();
    auto llrawproc = std::make_unique<llrawprocObject_t>();
    auto processing = std::make_unique<processingObject_t>();
    seed_runtime_objects(video.get(), llrawproc.get(), processing.get());

    llrawproc->diso_validity = DISO_VALID;
    llrawproc->diso1 = 100;
    llrawproc->diso2 = 6400;
    llrawproc->diso_auto_correction = 2;
    video->MLVI.videoClass = MLV_VIDEO_CLASS_FLAG_LJ92;
    video->RAWI.raw_info.black_level = 2047;
    video->RAWI.raw_info.white_level = 6000;
    video->original_black_level = 2047;
    video->original_white_level = 6000;

    ReceiptApplier::applyToMlv(&receipt, video.get(), processing.get());

    ASSERT_EQ(DISO_VALID, receipt.dualIsoForced());
    ASSERT_EQ(1, receipt.dualIso());
    ASSERT_EQ(0, receipt.dualIsoAutoCorrected());
    ASSERT_EQ(0, receipt.dualIsoPattern());
    ASSERT_EQ(1, receipt.dualIsoEvCorrection());
    ASSERT_EQ(-1, receipt.dualIsoBlackDelta());
    ASSERT_EQ(0u, receipt.dualIsoWhite());
    ASSERT_EQ(0u, receipt.dualIsoBlack());
    ASSERT_EQ(20470, receipt.rawBlack());
    ASSERT_EQ(6000, receipt.rawWhite());

    ASSERT_EQ(1, llrawproc->dual_iso);
    ASSERT_EQ(0, llrawproc->diso_pattern);
    ASSERT_EQ(-1, llrawproc->diso_auto_correction);
    ASSERT_EQ(1.0, llrawproc->diso_ev_correction);
    ASSERT_EQ(-1, llrawproc->diso_black_delta);
    ASSERT_EQ(2047, video->RAWI.raw_info.black_level);
    ASSERT_EQ(6000, video->RAWI.raw_info.white_level);

    destroy_runtime_objects(video.get());
}

TEST(ReceiptApplier, NonDualIsoClipDoesNotInheritValidDualIsoReceipt)
{
    ReceiptSettings receipt;
    receipt.setRawFixesEnabled(true);
    receipt.setFocusPixels(1);
    receipt.setBadPixels(0);
    receipt.setDualIsoForced(DISO_VALID);
    receipt.setDualIso(1);
    receipt.setDualIsoAutoCorrected(1);
    receipt.setDualIsoPattern(3);
    receipt.setDualIsoEvCorrection(220);
    receipt.setDualIsoBlackDelta(15);
    receipt.setDualIsoInterpolation(0);
    receipt.setDualIsoAliasMap(1);
    receipt.setDualIsoFrBlending(1);
    receipt.setDarkFrameEnabled(0);
    receipt.setRawBlack(-1);
    receipt.setRawWhite(-1);

    auto video = std::make_unique<mlvObject_t>();
    auto llrawproc = std::make_unique<llrawprocObject_t>();
    auto processing = std::make_unique<processingObject_t>();
    seed_runtime_objects(video.get(), llrawproc.get(), processing.get());
    llrawproc->diso_validity = DISO_INVALID;

    ReceiptApplier::applyToMlv(&receipt, video.get(), processing.get());

    ASSERT_EQ(DISO_INVALID, receipt.dualIsoForced());
    ASSERT_EQ(0, receipt.dualIso());
    ASSERT_EQ(0, llrawproc->dual_iso);
    ASSERT_EQ(DISO_INVALID, llrawproc->diso_validity);

    destroy_runtime_objects(video.get());
}

TEST(ReceiptApplier, ApplyingReceiptInvalidatesCachedFrames)
{
    ReceiptSettings receipt;
    receipt.setRawFixesEnabled(true);
    receipt.setDualIso(1);
    receipt.setDualIsoInterpolation(0);

    auto video = std::make_unique<mlvObject_t>();
    auto llrawproc = std::make_unique<llrawprocObject_t>();
    auto processing = std::make_unique<processingObject_t>();
    seed_runtime_objects(video.get(), llrawproc.get(), processing.get());

    video->cached_frames[0] = MLV_FRAME_IS_CACHED;
    video->cached_frames[1] = MLV_FRAME_BEING_CACHED;
    video->cached_frames[2] = MLV_FRAME_IS_CACHED;
    video->current_cached_frame_active = 1;
    video->current_processed_frame_active = 1;

    ReceiptApplier::applyToMlv(&receipt, video.get(), processing.get());

    ASSERT_EQ(0, video->current_cached_frame_active);
    ASSERT_EQ(0, video->current_processed_frame_active);
    for( uint64_t index = 0; index < video->frames; ++index )
    {
        ASSERT_EQ(static_cast<unsigned int>(MLV_FRAME_NOT_CACHED),
                  static_cast<unsigned int>(video->cached_frames[index]));
    }

    destroy_runtime_objects(video.get());
}

/* Regression: headless Look Assist must analyze the ORIGINAL receipt cut-in
 * frame, not effectiveCutIn. Under --resume effectiveCutIn is advanced past the
 * frames a previous run already exported; anchoring the analysis on it would
 * give the remaining DNGs different clip-wide look defaults than the first run.
 * lookAssistAnalysisFrameIndex must therefore ignore effectiveCutIn. */
TEST(BatchRunner, LookAssistAnalysisFrameAnchorsToOriginalCutInUnderResume)
{
    /* First run: nothing exported yet, effectiveCutIn == cutIn. */
    ASSERT_EQ(0u, BatchRunner::lookAssistAnalysisFrameIndex(1, 1));
    ASSERT_EQ(9u, BatchRunner::lookAssistAnalysisFrameIndex(10, 10));

    /* Resumed run: effectiveCutIn advanced far past cutIn. The analysis anchor
     * MUST stay at cutIn - 1 (this is exactly the bug the fix prevents: pre-fix
     * these returned effectiveCutIn - 1 = 49 and 199). */
    ASSERT_EQ(0u, BatchRunner::lookAssistAnalysisFrameIndex(1, 50));
    ASSERT_EQ(9u, BatchRunner::lookAssistAnalysisFrameIndex(10, 200));

    /* Degenerate cut-in (receipt cutIn == 0, normalized to frame 0). */
    ASSERT_EQ(0u, BatchRunner::lookAssistAnalysisFrameIndex(0, 0));
    ASSERT_EQ(0u, BatchRunner::lookAssistAnalysisFrameIndex(0, 25));
}

TEST(BatchRunner, MaxFramesClampPreservesCutInAndReceiptBounds)
{
    ASSERT_EQ(10u, BatchRunner::cutOutClampedForMaxFrames(1, 10, 0));
    ASSERT_EQ(5u, BatchRunner::cutOutClampedForMaxFrames(1, 10, 5));
    ASSERT_EQ(14u, BatchRunner::cutOutClampedForMaxFrames(10, 100, 5));
    ASSERT_EQ(12u, BatchRunner::cutOutClampedForMaxFrames(10, 12, 5));
    ASSERT_EQ(8u, BatchRunner::cutOutClampedForMaxFrames(10, 8, 5));
}

TEST(BatchExportFormat, ParsesCdngAliases)
{
    ASSERT_EQ( static_cast<int>(BatchExportFormat::Cdng),
               static_cast<int>(batchExportFormatFromString(QString())) );
    ASSERT_EQ( static_cast<int>(BatchExportFormat::Cdng),
               static_cast<int>(batchExportFormatFromString(QStringLiteral("cdng"))) );
    ASSERT_EQ( static_cast<int>(BatchExportFormat::Cdng),
               static_cast<int>(batchExportFormatFromString(QStringLiteral("Cinema-DNG"))) );
    ASSERT_EQ( std::string("cdng"),
               std::string(batchExportFormatName(BatchExportFormat::Cdng)) );
}

TEST(BatchExportFormat, RecognizesRenderedVideoAsBlockedE4)
{
    ASSERT_EQ( static_cast<int>(BatchExportFormat::RenderedVideo),
               static_cast<int>(batchExportFormatFromString(QStringLiteral("rendered-video"))) );
    ASSERT_EQ( static_cast<int>(BatchExportFormat::RenderedVideo),
               static_cast<int>(batchExportFormatFromString(QStringLiteral("video"))) );
    ASSERT_EQ( static_cast<int>(BatchExportFormat::RenderedVideo),
               static_cast<int>(batchExportFormatFromString(QStringLiteral("mp4"))) );
    ASSERT_EQ( static_cast<int>(BatchExportFormat::RenderedVideo),
               static_cast<int>(batchExportFormatFromString(QStringLiteral("prores"))) );
    ASSERT_EQ( std::string("rendered-video"),
               std::string(batchExportFormatName(BatchExportFormat::RenderedVideo)) );
}

TEST(BatchExportFormat, PreservesRenderedVideoRequestIntent)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264-mp4"));
    ASSERT_EQ( static_cast<int>(BatchExportFormat::RenderedVideo),
               static_cast<int>(request.format) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H264),
               static_cast<int>(request.renderedCodec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mp4),
               static_cast<int>(request.renderedContainer) );
    ASSERT_EQ( std::string("h264"),
               std::string(batchRenderedVideoCodecName(request.renderedCodec)) );
    ASSERT_EQ( std::string("mp4"),
               std::string(batchRenderedVideoContainerName(request.renderedContainer)) );

    request = batchExportFormatRequestFromString(QStringLiteral("hevc-mp4"));
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H265),
               static_cast<int>(request.renderedCodec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mp4),
               static_cast<int>(request.renderedContainer) );

    request = batchExportFormatRequestFromString(QStringLiteral("prores"));
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::ProRes),
               static_cast<int>(request.renderedCodec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mov),
               static_cast<int>(request.renderedContainer) );

    request = batchExportFormatRequestFromString(QStringLiteral("rendered-video"));
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::Unspecified),
               static_cast<int>(request.renderedCodec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Unspecified),
               static_cast<int>(request.renderedContainer) );
}

TEST(BatchExportFormat, ParsesRenderedVideoOptionAliases)
{
    bool ok = false;
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H264),
               static_cast<int>(batchRenderedVideoCodecFromString(QStringLiteral("avc"), &ok)) );
    ASSERT_TRUE( ok );

    ok = false;
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H265),
               static_cast<int>(batchRenderedVideoCodecFromString(QStringLiteral("h.265"), &ok)) );
    ASSERT_TRUE( ok );

    ok = false;
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::ProRes),
               static_cast<int>(batchRenderedVideoCodecFromString(QStringLiteral("pro-res"), &ok)) );
    ASSERT_TRUE( ok );

    ok = true;
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::Unspecified),
               static_cast<int>(batchRenderedVideoCodecFromString(QStringLiteral("dnxhr"), &ok)) );
    ASSERT_FALSE( ok );

    ok = false;
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mov),
               static_cast<int>(batchRenderedVideoContainerFromString(QStringLiteral("quicktime"), &ok)) );
    ASSERT_TRUE( ok );

    ok = false;
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mkv),
               static_cast<int>(batchRenderedVideoContainerFromString(QStringLiteral("matroska"), &ok)) );
    ASSERT_TRUE( ok );

    ok = true;
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Unspecified),
               static_cast<int>(batchRenderedVideoContainerFromString(QStringLiteral("avi"), &ok)) );
    ASSERT_FALSE( ok );
}

TEST(BatchExportFormat, BatchContextPreservesExportRequestIntent)
{
    BatchExportFormatRequest request;
    request.format = BatchExportFormat::RenderedVideo;
    request.renderedCodec = BatchRenderedVideoCodec::H265;
    request.renderedContainer = BatchRenderedVideoContainer::Mkv;

    BatchContext::setExportFormatRequest(request);

    const BatchExportFormatRequest stored = BatchContext::exportFormatRequest();
    ASSERT_EQ( static_cast<int>(BatchExportFormat::RenderedVideo),
               static_cast<int>(stored.format) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H265),
               static_cast<int>(stored.renderedCodec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mkv),
               static_cast<int>(stored.renderedContainer) );
    ASSERT_EQ( std::string("request=rendered-video codec=h265 container=mkv"),
               std::string(batchExportFormatRequestSummary(stored).toUtf8().constData()) );

    BatchContext::setExportFormatRequest(BatchExportFormatRequest());
}

TEST(BatchExportFormat, BatchContextPreservesRenderedRenderSettings)
{
    BatchRenderedVideoRenderSettings settings =
        batchRenderedVideoRenderSettingsFromExplicitResize(
            true,
            2048,
            0,
            true);

    BatchContext::setRenderedVideoRenderSettings(settings);

    const BatchRenderedVideoRenderSettings stored =
        BatchContext::renderedVideoRenderSettings();
    ASSERT_TRUE( stored.ready );
    ASSERT_TRUE( stored.explicitHeadlessSettings );
    ASSERT_FALSE( stored.guiSettingsOwned );
    ASSERT_TRUE( stored.resizeEnabled );
    ASSERT_EQ( 2048, stored.resizeWidth );
    ASSERT_EQ( 0, stored.resizeHeight );
    ASSERT_TRUE( stored.resizeHeightLocked );
    ASSERT_EQ( std::string("render-settings-source=explicit-headless render-settings-explicit-headless=true render-settings-gui-owned=false render-settings-ready=true render-settings-reason=none render-settings-resize=true render-settings-resize-width=2048 render-settings-resize-height=0 render-settings-resize-height-locked=true"),
               std::string(batchRenderedVideoRenderSettingsSummary(
                   stored).toUtf8().constData()) );

    BatchContext::setRenderedVideoRenderSettings(
        batchRenderedVideoDefaultRenderSettings());
}

TEST(BatchExportFormat, BatchContextPreservesRenderedFfmpegExecutable)
{
    BatchContext::setRenderedVideoFfmpegExecutable(
        QStringLiteral("  C:/tools/ffmpeg-custom.exe  "));

    ASSERT_EQ( std::string("C:/tools/ffmpeg-custom.exe"),
               std::string(BatchContext::renderedVideoFfmpegExecutable()
                   .toUtf8().constData()) );

    BatchContext::setRenderedVideoFfmpegExecutable(QString());
    ASSERT_TRUE( BatchContext::renderedVideoFfmpegExecutable().isEmpty() );
}

TEST(BatchExportFormat, ValidatesRenderedVideoRequestShape)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264-mp4"));
    ASSERT_TRUE( batchRenderedVideoRequestShapeValid(request) );

    request = batchExportFormatRequestFromString(QStringLiteral("prores-mov"));
    ASSERT_TRUE( batchRenderedVideoRequestShapeValid(request) );

    request = batchExportFormatRequestFromString(QStringLiteral("prores"));
    request.renderedContainer = BatchRenderedVideoContainer::Mp4;
    ASSERT_FALSE( batchRenderedVideoRequestShapeValid(request) );
    ASSERT_EQ( std::string("codec=prores requires container=mov or unspecified"),
               std::string(batchRenderedVideoRequestShapeError(request).toUtf8().constData()) );
}

TEST(BatchExportFormat, ResolvesRenderedVideoTargets)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoTarget target =
        batchRenderedVideoTargetFromRequest(request);
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H264),
               static_cast<int>(target.codec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mp4),
               static_cast<int>(target.container) );
    ASSERT_TRUE( target.complete );
    ASSERT_EQ( std::string(".mp4"),
               std::string(target.extension.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("mp4"));
    target = batchRenderedVideoTargetFromRequest(request);
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H264),
               static_cast<int>(target.codec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mp4),
               static_cast<int>(target.container) );
    ASSERT_TRUE( target.complete );

    request = batchExportFormatRequestFromString(QStringLiteral("mkv"));
    target = batchRenderedVideoTargetFromRequest(request);
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H264),
               static_cast<int>(target.codec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mkv),
               static_cast<int>(target.container) );
    ASSERT_EQ( std::string(".mkv"),
               std::string(target.extension.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("h265"));
    target = batchRenderedVideoTargetFromRequest(request);
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::H265),
               static_cast<int>(target.codec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mp4),
               static_cast<int>(target.container) );
    ASSERT_TRUE( target.complete );

    request = batchExportFormatRequestFromString(QStringLiteral("mov"));
    target = batchRenderedVideoTargetFromRequest(request);
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::ProRes),
               static_cast<int>(target.codec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Mov),
               static_cast<int>(target.container) );
    ASSERT_EQ( std::string(".mov"),
               std::string(target.extension.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("rendered-video"));
    target = batchRenderedVideoTargetFromRequest(request);
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoCodec::Unspecified),
               static_cast<int>(target.codec) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoContainer::Unspecified),
               static_cast<int>(target.container) );
    ASSERT_FALSE( target.complete );
    ASSERT_TRUE( target.extension.isEmpty() );
    ASSERT_EQ( std::string("target-codec=unspecified target-container=unspecified target-extension=unspecified target-complete=false"),
               std::string(batchRenderedVideoTargetSummary(request).toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("prores"));
    request.renderedContainer = BatchRenderedVideoContainer::Mp4;
    target = batchRenderedVideoTargetFromRequest(request);
    ASSERT_FALSE( target.complete );
    ASSERT_TRUE( target.extension.isEmpty() );
}

TEST(BatchExportFormat, ResolvesRenderedVideoEncoderPresets)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoEncoderPreset preset =
        batchRenderedVideoEncoderPresetFromRequest(request);
    ASSERT_TRUE( preset.ready );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderProfile::H264),
               static_cast<int>(preset.profile) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderOption::H264HighMp4),
               static_cast<int>(preset.option) );
    ASSERT_EQ( CODEC_H264, preset.guiCodecProfile );
    ASSERT_EQ( CODEC_H264_H_MP4, preset.guiCodecOption );
    ASSERT_EQ( std::string(".mp4"),
               std::string(preset.extension.toUtf8().constData()) );
    ASSERT_EQ( std::string("encoder-profile=h264 encoder-option=ffmpeg-mp4-high gui-codec-profile=9 gui-codec-option=1 encoder-extension=.mp4 encoder-ready=true"),
               std::string(batchRenderedVideoEncoderPresetSummary(request).toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("mkv"));
    preset = batchRenderedVideoEncoderPresetFromRequest(request);
    ASSERT_TRUE( preset.ready );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderProfile::H264),
               static_cast<int>(preset.profile) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderOption::H264HighMkv),
               static_cast<int>(preset.option) );
    ASSERT_EQ( CODEC_H264, preset.guiCodecProfile );
    ASSERT_EQ( CODEC_H264_H_MKV, preset.guiCodecOption );
    ASSERT_EQ( std::string(".mkv"),
               std::string(preset.extension.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("h265"));
    preset = batchRenderedVideoEncoderPresetFromRequest(request);
    ASSERT_TRUE( preset.ready );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderProfile::H265_8),
               static_cast<int>(preset.profile) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderOption::H265HighMp4),
               static_cast<int>(preset.option) );
    ASSERT_EQ( CODEC_H265_8, preset.guiCodecProfile );
    ASSERT_EQ( CODEC_H265_H_MP4, preset.guiCodecOption );
    ASSERT_EQ( std::string(".mp4"),
               std::string(preset.extension.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("mov"));
    preset = batchRenderedVideoEncoderPresetFromRequest(request);
    ASSERT_TRUE( preset.ready );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderProfile::ProRes422HQ),
               static_cast<int>(preset.profile) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderOption::ProResFfmpegKostya),
               static_cast<int>(preset.option) );
    ASSERT_EQ( CODEC_PRORES422HQ, preset.guiCodecProfile );
    ASSERT_EQ( CODEC_PRORES_OPTION_KS, preset.guiCodecOption );
    ASSERT_EQ( std::string(".mov"),
               std::string(preset.extension.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("rendered-video"));
    preset = batchRenderedVideoEncoderPresetFromRequest(request);
    ASSERT_FALSE( preset.ready );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderProfile::Unspecified),
               static_cast<int>(preset.profile) );
    ASSERT_EQ( static_cast<int>(BatchRenderedVideoEncoderOption::Unspecified),
               static_cast<int>(preset.option) );
    ASSERT_EQ( -1, preset.guiCodecProfile );
    ASSERT_EQ( -1, preset.guiCodecOption );
    ASSERT_TRUE( preset.extension.isEmpty() );
}

TEST(BatchExportFormat, PlansRenderedVideoFfmpegVideoArguments)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoFfmpegVideoPlan ffmpegPlan =
        batchRenderedVideoFfmpegVideoPlanFromRequest(request);
    ASSERT_TRUE( ffmpegPlan.ready );
    ASSERT_EQ( std::string("libx264"),
               std::string(ffmpegPlan.encoder.toUtf8().constData()) );
    ASSERT_EQ( std::string("medium"),
               std::string(ffmpegPlan.preset.toUtf8().constData()) );
    ASSERT_EQ( std::string("-crf"),
               std::string(ffmpegPlan.qualityFlag.toUtf8().constData()) );
    ASSERT_EQ( 14, ffmpegPlan.qualityValue );
    ASSERT_EQ( std::string("yuv420p"),
               std::string(ffmpegPlan.pixelFormat.toUtf8().constData()) );
    ASSERT_EQ( std::string("-c:v libx264 -preset medium -crf 14 -pix_fmt yuv420p"),
               std::string(ffmpegPlan.videoArguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-video-encoder=libx264 ffmpeg-video-preset=medium ffmpeg-video-quality=-crf:14 ffmpeg-video-pix-fmt=yuv420p ffmpeg-video-tag=none ffmpeg-video-args=-c:v libx264 -preset medium -crf 14 -pix_fmt yuv420p ffmpeg-video-ready=true ffmpeg-video-reason=none"),
               std::string(batchRenderedVideoFfmpegVideoPlanSummary(request).toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("h265"));
    ffmpegPlan = batchRenderedVideoFfmpegVideoPlanFromRequest(request);
    ASSERT_TRUE( ffmpegPlan.ready );
    ASSERT_EQ( std::string("libx265"),
               std::string(ffmpegPlan.encoder.toUtf8().constData()) );
    ASSERT_EQ( std::string("-crf"),
               std::string(ffmpegPlan.qualityFlag.toUtf8().constData()) );
    ASSERT_EQ( 18, ffmpegPlan.qualityValue );
    ASSERT_EQ( std::string("hvc1"),
               std::string(ffmpegPlan.videoTag.toUtf8().constData()) );
    ASSERT_EQ( std::string("-c:v libx265 -preset medium -crf 18 -tag:v hvc1 -pix_fmt yuv420p"),
               std::string(ffmpegPlan.videoArguments.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("mov"));
    ffmpegPlan = batchRenderedVideoFfmpegVideoPlanFromRequest(request);
    ASSERT_TRUE( ffmpegPlan.ready );
    ASSERT_EQ( std::string("prores_ks"),
               std::string(ffmpegPlan.encoder.toUtf8().constData()) );
    ASSERT_EQ( std::string("-profile:v"),
               std::string(ffmpegPlan.qualityFlag.toUtf8().constData()) );
    ASSERT_EQ( CODEC_PRORES422HQ, ffmpegPlan.qualityValue );
    ASSERT_EQ( std::string("yuv422p10"),
               std::string(ffmpegPlan.pixelFormat.toUtf8().constData()) );
    ASSERT_EQ( std::string("-c:v prores_ks -profile:v 3 -pix_fmt yuv422p10"),
               std::string(ffmpegPlan.videoArguments.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("rendered-video"));
    ffmpegPlan = batchRenderedVideoFfmpegVideoPlanFromRequest(request);
    ASSERT_FALSE( ffmpegPlan.ready );
    ASSERT_EQ( std::string("rendered encoder preset incomplete"),
               std::string(ffmpegPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoFfmpegFilterArguments)
{
    BatchRenderedVideoFfmpegFilterPlan filterPlan =
        batchRenderedVideoFfmpegFilterPlanForCurrentBuild();

    ASSERT_TRUE( filterPlan.ready );
    ASSERT_TRUE( filterPlan.baseColorScaleReady );
    ASSERT_FALSE( filterPlan.optionalFiltersOwned );
    ASSERT_FALSE( filterPlan.moireeFilterOwned );
    ASSERT_FALSE( filterPlan.hdrBlendOwned );
    ASSERT_FALSE( filterPlan.stabilizationOwned );
    ASSERT_EQ( std::string("gui-base-color-scale"),
               std::string(filterPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("scale=in_color_matrix=bt601:out_color_matrix=bt709"),
               std::string(filterPlan.colorScaleFilter.toUtf8().constData()) );
    ASSERT_EQ( std::string("-vf scale=in_color_matrix=bt601:out_color_matrix=bt709"),
               std::string(filterPlan.filterArguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-filter-source=gui-base-color-scale ffmpeg-filter-base-color-scale-ready=true ffmpeg-filter-optional-owned=false ffmpeg-filter-moiree-owned=false ffmpeg-filter-hdr-owned=false ffmpeg-filter-stabilization-owned=false ffmpeg-filter-color-scale=scale=in_color_matrix=bt601:out_color_matrix=bt709 ffmpeg-filter-args=-vf scale=in_color_matrix=bt601:out_color_matrix=bt709 ffmpeg-filter-ready=true ffmpeg-filter-reason=none"),
               std::string(batchRenderedVideoFfmpegFilterPlanSummary(
                   filterPlan).toUtf8().constData()) );

    BatchRenderedVideoOptionalFilterPlan optionalFilterPlan =
        batchRenderedVideoOptionalFilterPlanFromFilterPlan(filterPlan);
    ASSERT_TRUE( optionalFilterPlan.contractReady );
    ASSERT_TRUE( optionalFilterPlan.baseFilterContractReady );
    ASSERT_FALSE( optionalFilterPlan.optionalFiltersRequested );
    ASSERT_FALSE( optionalFilterPlan.optionalFilterGraphOwned );
    ASSERT_FALSE( optionalFilterPlan.moireeFilterOwned );
    ASSERT_FALSE( optionalFilterPlan.hdrBlendOwned );
    ASSERT_FALSE( optionalFilterPlan.stabilizationOwned );
    ASSERT_FALSE( optionalFilterPlan.optionalFilterOrderOwned );
    ASSERT_FALSE( optionalFilterPlan.optionalFilterParityOwned );
    ASSERT_FALSE( optionalFilterPlan.optionalFilterExecutionReady );
    ASSERT_EQ( std::string("optional-filter-ownership-contract"),
               std::string(optionalFilterPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("-vf scale=in_color_matrix=bt601:out_color_matrix=bt709"),
               std::string(optionalFilterPlan.baseFilterArguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("optional-filter-source=optional-filter-ownership-contract optional-filter-base-contract-ready=true optional-filter-base-args=-vf scale=in_color_matrix=bt601:out_color_matrix=bt709 optional-filter-requested=false optional-filter-graph-owned=false optional-filter-moiree-owned=false optional-filter-hdr-owned=false optional-filter-stabilization-owned=false optional-filter-order-owned=false optional-filter-parity-owned=false optional-filter-exec-ready=false optional-filter-contract-ready=true optional-filter-reason=none"),
               std::string(batchRenderedVideoOptionalFilterPlanSummary(
                   optionalFilterPlan).toUtf8().constData()) );

    BatchRenderedVideoFfmpegFilterPlan invalidFilterPlan;
    optionalFilterPlan =
        batchRenderedVideoOptionalFilterPlanFromFilterPlan(invalidFilterPlan);
    ASSERT_FALSE( optionalFilterPlan.contractReady );
    ASSERT_FALSE( optionalFilterPlan.baseFilterContractReady );
    ASSERT_EQ( std::string("rendered ffmpeg filter plan unavailable"),
               std::string(optionalFilterPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoSourceAudioContract)
{
    BatchRenderedVideoSourceAudioPlan sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanForCurrentBuild(
            QStringLiteral("C:\\clips\\M16-1327.MLV"));

    ASSERT_TRUE( sourceAudioPlan.contractReady );
    ASSERT_TRUE( sourceAudioPlan.videoOnlyFallbackReady );
    ASSERT_FALSE( sourceAudioPlan.discoveryOwned );
    ASSERT_FALSE( sourceAudioPlan.discoveryAttempted );
    ASSERT_FALSE( sourceAudioPlan.sourceAudioKnown );
    ASSERT_FALSE( sourceAudioPlan.sourceAudioPresent );
    ASSERT_FALSE( sourceAudioPlan.extractionOwned );
    ASSERT_FALSE( sourceAudioPlan.muxInputOwned );
    ASSERT_FALSE( sourceAudioPlan.syncValidationOwned );
    ASSERT_EQ( 0, sourceAudioPlan.sampleRate );
    ASSERT_EQ( 0, sourceAudioPlan.channels );
    ASSERT_EQ( 0, sourceAudioPlan.bitsPerSample );
    ASSERT_EQ( static_cast<qulonglong>(0), sourceAudioPlan.audioBytes );
    ASSERT_EQ( std::string("video-only-undiscovered"),
               std::string(sourceAudioPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("C:/clips/M16-1327.MLV"),
               std::string(sourceAudioPlan.clipPath.toUtf8().constData()) );
    ASSERT_EQ( std::string("unknown"),
               std::string(sourceAudioPlan.audioState.toUtf8().constData()) );
    ASSERT_EQ( std::string("source-audio-source=video-only-undiscovered source-audio-clip=C:/clips/M16-1327.MLV source-audio-state=unknown source-audio-sample-rate=0 source-audio-channels=0 source-audio-bits=0 source-audio-bytes=0 source-audio-discovery-owned=false source-audio-discovery-attempted=false source-audio-known=false source-audio-present=false source-audio-extraction-owned=false source-audio-mux-input-owned=false source-audio-sync-validation-owned=false source-audio-video-only-ready=true source-audio-contract-ready=true source-audio-reason=none"),
               std::string(batchRenderedVideoSourceAudioPlanSummary(
                   sourceAudioPlan).toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoDiscoveredSourceAudioMetadata)
{
    BatchRenderedVideoSourceAudioPlan sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
            QStringLiteral("C:\\clips\\M16-1327.MLV"),
            true,
            2,
            48000,
            16,
            123456);

    ASSERT_TRUE( sourceAudioPlan.contractReady );
    ASSERT_TRUE( sourceAudioPlan.videoOnlyFallbackReady );
    ASSERT_TRUE( sourceAudioPlan.discoveryOwned );
    ASSERT_TRUE( sourceAudioPlan.discoveryAttempted );
    ASSERT_TRUE( sourceAudioPlan.sourceAudioKnown );
    ASSERT_TRUE( sourceAudioPlan.sourceAudioPresent );
    ASSERT_FALSE( sourceAudioPlan.extractionOwned );
    ASSERT_FALSE( sourceAudioPlan.muxInputOwned );
    ASSERT_FALSE( sourceAudioPlan.syncValidationOwned );
    ASSERT_EQ( 48000, sourceAudioPlan.sampleRate );
    ASSERT_EQ( 2, sourceAudioPlan.channels );
    ASSERT_EQ( 16, sourceAudioPlan.bitsPerSample );
    ASSERT_EQ( static_cast<qulonglong>(123456), sourceAudioPlan.audioBytes );
    ASSERT_EQ( std::string("open-mlv-audio-metadata"),
               std::string(sourceAudioPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("present"),
               std::string(sourceAudioPlan.audioState.toUtf8().constData()) );
    ASSERT_EQ( std::string("source-audio-source=open-mlv-audio-metadata source-audio-clip=C:/clips/M16-1327.MLV source-audio-state=present source-audio-sample-rate=48000 source-audio-channels=2 source-audio-bits=16 source-audio-bytes=123456 source-audio-discovery-owned=true source-audio-discovery-attempted=true source-audio-known=true source-audio-present=true source-audio-extraction-owned=false source-audio-mux-input-owned=false source-audio-sync-validation-owned=false source-audio-video-only-ready=true source-audio-contract-ready=true source-audio-reason=none"),
               std::string(batchRenderedVideoSourceAudioPlanSummary(
                   sourceAudioPlan).toUtf8().constData()) );

    sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            false,
            0,
            0,
            0,
            0);
    ASSERT_TRUE( sourceAudioPlan.contractReady );
    ASSERT_TRUE( sourceAudioPlan.discoveryOwned );
    ASSERT_TRUE( sourceAudioPlan.discoveryAttempted );
    ASSERT_TRUE( sourceAudioPlan.sourceAudioKnown );
    ASSERT_FALSE( sourceAudioPlan.sourceAudioPresent );
    ASSERT_EQ( std::string("absent"),
               std::string(sourceAudioPlan.audioState.toUtf8().constData()) );

    sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            true,
            0,
            48000,
            16,
            123456);
    ASSERT_FALSE( sourceAudioPlan.contractReady );
    ASSERT_TRUE( sourceAudioPlan.videoOnlyFallbackReady );
    ASSERT_TRUE( sourceAudioPlan.discoveryOwned );
    ASSERT_TRUE( sourceAudioPlan.sourceAudioKnown );
    ASSERT_TRUE( sourceAudioPlan.sourceAudioPresent );
    ASSERT_EQ( std::string("rendered source audio metadata invalid"),
               std::string(sourceAudioPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoSourceAudioExtractionPrerequisites)
{
    BatchRenderedVideoSourceAudioPlan sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanForCurrentBuild(
            QStringLiteral("C:\\clips\\M16-1327.MLV"));
    BatchRenderedVideoSourceAudioExtractionPlan extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan,
            QStringLiteral("C:\\renders\\M16-1327.source-audio.wav"));

    ASSERT_TRUE( extractionPlan.contractReady );
    ASSERT_TRUE( extractionPlan.sourceAudioContractReady );
    ASSERT_FALSE( extractionPlan.sourceAudioKnown );
    ASSERT_FALSE( extractionPlan.sourceAudioPresent );
    ASSERT_FALSE( extractionPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( extractionPlan.extractionPathPlanned );
    ASSERT_TRUE( extractionPlan.extractionPathReady );
    ASSERT_TRUE( extractionPlan.sampleFormatReady );
    ASSERT_TRUE( extractionPlan.sampleRateReady );
    ASSERT_TRUE( extractionPlan.channelLayoutReady );
    ASSERT_FALSE( extractionPlan.extractionProcessOwned );
    ASSERT_FALSE( extractionPlan.tempFileOwned );
    ASSERT_FALSE( extractionPlan.cleanupOwned );
    ASSERT_FALSE( extractionPlan.extractionReady );
    ASSERT_TRUE( extractionPlan.videoOnlyFallbackReady );
    ASSERT_EQ( std::string("C:/renders/M16-1327.source-audio.wav"),
               std::string(extractionPlan.plannedAudioPath
                   .toUtf8().constData()) );
    ASSERT_EQ( std::string("source-audio-extraction-source=source-audio-extraction-prerequisite-contract source-audio-extraction-clip=C:/clips/M16-1327.MLV source-audio-extraction-format=wav-pcm-s16le source-audio-extraction-path=C:/renders/M16-1327.source-audio.wav source-audio-extraction-source-contract-ready=true source-audio-extraction-known=false source-audio-extraction-present=false source-audio-extraction-discovery-owned=false source-audio-extraction-path-planned=false source-audio-extraction-path-ready=true source-audio-extraction-format-ready=true source-audio-extraction-sample-rate-ready=true source-audio-extraction-channel-layout-ready=true source-audio-extraction-process-owned=false source-audio-extraction-temp-file-owned=false source-audio-extraction-cleanup-owned=false source-audio-extraction-ready=false source-audio-extraction-video-only-ready=true source-audio-extraction-contract-ready=true source-audio-extraction-reason=none"),
               std::string(batchRenderedVideoSourceAudioExtractionPlanSummary(
                   extractionPlan).toUtf8().constData()) );

    sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
            QStringLiteral("C:\\clips\\M16-1327.MLV"),
            true,
            2,
            48000,
            16,
            123456);
    extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan,
            QStringLiteral("C:/renders/M16-1327.source-audio.wav"));
    ASSERT_TRUE( extractionPlan.contractReady );
    ASSERT_TRUE( extractionPlan.sourceAudioContractReady );
    ASSERT_TRUE( extractionPlan.sourceAudioKnown );
    ASSERT_TRUE( extractionPlan.sourceAudioPresent );
    ASSERT_TRUE( extractionPlan.sourceAudioDiscoveryOwned );
    ASSERT_TRUE( extractionPlan.extractionPathPlanned );
    ASSERT_TRUE( extractionPlan.extractionPathReady );
    ASSERT_TRUE( extractionPlan.sampleFormatReady );
    ASSERT_TRUE( extractionPlan.sampleRateReady );
    ASSERT_TRUE( extractionPlan.channelLayoutReady );
    ASSERT_FALSE( extractionPlan.extractionProcessOwned );
    ASSERT_FALSE( extractionPlan.tempFileOwned );
    ASSERT_FALSE( extractionPlan.cleanupOwned );
    ASSERT_FALSE( extractionPlan.extractionReady );
    ASSERT_TRUE( extractionPlan.videoOnlyFallbackReady );

    extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan,
            QString());
    ASSERT_FALSE( extractionPlan.contractReady );
    ASSERT_TRUE( extractionPlan.extractionPathPlanned );
    ASSERT_FALSE( extractionPlan.extractionPathReady );
    ASSERT_EQ( std::string("rendered source audio extraction prerequisite contract unavailable"),
               std::string(extractionPlan.reason.toUtf8().constData()) );

    BatchRenderedVideoSourceAudioPlan invalidSourceAudioPlan;
    invalidSourceAudioPlan.reason =
        QStringLiteral("source audio contract unavailable");
    extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            invalidSourceAudioPlan,
            QStringLiteral("C:/renders/M16-1327.source-audio.wav"));
    ASSERT_FALSE( extractionPlan.contractReady );
    ASSERT_FALSE( extractionPlan.sourceAudioContractReady );
    ASSERT_EQ( std::string("source audio contract unavailable"),
               std::string(extractionPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoFfmpegAudioInputContract)
{
    BatchRenderedVideoSourceAudioPlan sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanForCurrentBuild(
            QStringLiteral("C:\\clips\\M16-1327.MLV"));
    BatchRenderedVideoSourceAudioExtractionPlan extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan,
            QStringLiteral("C:\\renders\\M16-1327.source-audio.wav"));
    BatchRenderedVideoFfmpegAudioInputPlan inputPlan =
        batchRenderedVideoFfmpegAudioInputPlanFromExtraction(
            extractionPlan);

    ASSERT_TRUE( inputPlan.contractReady );
    ASSERT_TRUE( inputPlan.sourceAudioExtractionContractReady );
    ASSERT_FALSE( inputPlan.sourceAudioKnown );
    ASSERT_FALSE( inputPlan.sourceAudioPresent );
    ASSERT_FALSE( inputPlan.extractionPathPlanned );
    ASSERT_TRUE( inputPlan.extractionPathReady );
    ASSERT_FALSE( inputPlan.extractionReady );
    ASSERT_FALSE( inputPlan.tempFileOwned );
    ASSERT_FALSE( inputPlan.cleanupOwned );
    ASSERT_FALSE( inputPlan.audioInputPlanned );
    ASSERT_FALSE( inputPlan.audioInputOwned );
    ASSERT_FALSE( inputPlan.audioInputReady );
    ASSERT_TRUE( inputPlan.videoOnlyFallbackReady );
    ASSERT_EQ( std::string("C:/renders/M16-1327.source-audio.wav"),
               std::string(inputPlan.plannedAudioPath
                   .toUtf8().constData()) );
    ASSERT_EQ( std::string("-an"),
               std::string(inputPlan.activeAudioArguments
                   .toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-audio-input-source=ffmpeg-audio-input-contract ffmpeg-audio-input-path=C:/renders/M16-1327.source-audio.wav ffmpeg-audio-input-planned-args=unspecified ffmpeg-audio-input-active-args=-an ffmpeg-audio-input-extraction-contract-ready=true ffmpeg-audio-input-known=false ffmpeg-audio-input-present=false ffmpeg-audio-input-path-planned=false ffmpeg-audio-input-path-ready=true ffmpeg-audio-input-extraction-ready=false ffmpeg-audio-input-temp-file-owned=false ffmpeg-audio-input-cleanup-owned=false ffmpeg-audio-input-planned=false ffmpeg-audio-input-owned=false ffmpeg-audio-input-ready=false ffmpeg-audio-input-video-only-ready=true ffmpeg-audio-input-contract-ready=true ffmpeg-audio-input-reason=none"),
               std::string(batchRenderedVideoFfmpegAudioInputPlanSummary(
                   inputPlan).toUtf8().constData()) );

    sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
            QStringLiteral("C:\\clips\\M16-1327.MLV"),
            true,
            2,
            48000,
            16,
            123456);
    extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan,
            QStringLiteral("C:/renders/M16-1327.source-audio.wav"));
    inputPlan =
        batchRenderedVideoFfmpegAudioInputPlanFromExtraction(
            extractionPlan);
    ASSERT_TRUE( inputPlan.contractReady );
    ASSERT_TRUE( inputPlan.audioInputPlanned );
    ASSERT_FALSE( inputPlan.audioInputOwned );
    ASSERT_FALSE( inputPlan.audioInputReady );
    ASSERT_EQ( std::string("-i \"C:/renders/M16-1327.source-audio.wav\""),
               std::string(inputPlan.plannedInputArguments
                   .toUtf8().constData()) );

    extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan,
            QString());
    inputPlan =
        batchRenderedVideoFfmpegAudioInputPlanFromExtraction(
            extractionPlan);
    ASSERT_FALSE( inputPlan.contractReady );
    ASSERT_TRUE( inputPlan.audioInputPlanned );
    ASSERT_FALSE( inputPlan.extractionPathReady );
    ASSERT_EQ( std::string("rendered source audio extraction prerequisite contract unavailable"),
               std::string(inputPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoAudioMuxPrerequisitesContract)
{
    BatchRenderedVideoSourceAudioPlan sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanForCurrentBuild(
            QStringLiteral("C:\\clips\\M16-1327.MLV"));
    BatchRenderedVideoAudioMuxPrerequisitesPlan audioMuxPlan =
        batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
            sourceAudioPlan);

    ASSERT_TRUE( audioMuxPlan.contractReady );
    ASSERT_TRUE( audioMuxPlan.sourceAudioContractReady );
    ASSERT_TRUE( audioMuxPlan.sourceAudioInputContractReady );
    ASSERT_TRUE( audioMuxPlan.videoOnlyFallbackReady );
    ASSERT_FALSE( audioMuxPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( audioMuxPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( audioMuxPlan.audioInputOwned );
    ASSERT_FALSE( audioMuxPlan.audioMuxOwned );
    ASSERT_FALSE( audioMuxPlan.audioSyncValidationOwned );
    ASSERT_FALSE( audioMuxPlan.muxReady );
    ASSERT_EQ( std::string("audio-mux-prerequisite-contract"),
               std::string(audioMuxPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("rendered-source-audio-discovery"),
               std::string(audioMuxPlan.inputState.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-audio-input-or-video-only-fallback"),
               std::string(audioMuxPlan.outputState.toUtf8().constData()) );
    ASSERT_TRUE( audioMuxPlan.sourceAudioExtractionContractReady );
    ASSERT_EQ( std::string("audio-mux-source=audio-mux-prerequisite-contract audio-mux-input=rendered-source-audio-discovery audio-mux-output=ffmpeg-audio-input-or-video-only-fallback audio-mux-source-contract-ready=true audio-mux-extraction-contract-ready=true audio-mux-audio-input-contract-ready=true audio-mux-video-only-ready=true audio-mux-source-discovery-owned=false audio-mux-extraction-owned=false audio-mux-input-owned=false audio-mux-mux-owned=false audio-mux-sync-owned=false audio-mux-ready=false audio-mux-contract-ready=true audio-mux-reason=none"),
               std::string(batchRenderedVideoAudioMuxPrerequisitesPlanSummary(
                   audioMuxPlan).toUtf8().constData()) );

    sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
            QStringLiteral("C:\\clips\\M16-1327.MLV"),
            true,
            2,
            48000,
            16,
            123456);
    BatchRenderedVideoSourceAudioExtractionPlan extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan,
            QStringLiteral("C:/renders/M16-1327.source-audio.wav"));
    audioMuxPlan =
        batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
            sourceAudioPlan,
            extractionPlan);
    ASSERT_TRUE( audioMuxPlan.contractReady );
    ASSERT_TRUE( audioMuxPlan.sourceAudioContractReady );
    ASSERT_TRUE( audioMuxPlan.sourceAudioExtractionContractReady );
    ASSERT_TRUE( audioMuxPlan.sourceAudioInputContractReady );
    ASSERT_TRUE( audioMuxPlan.videoOnlyFallbackReady );
    ASSERT_TRUE( audioMuxPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( audioMuxPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( audioMuxPlan.audioInputOwned );
    ASSERT_FALSE( audioMuxPlan.audioMuxOwned );
    ASSERT_FALSE( audioMuxPlan.audioSyncValidationOwned );
    ASSERT_FALSE( audioMuxPlan.muxReady );
    ASSERT_EQ( std::string("audio-mux-source=audio-mux-prerequisite-contract audio-mux-input=rendered-source-audio-discovery audio-mux-output=ffmpeg-audio-input-or-video-only-fallback audio-mux-source-contract-ready=true audio-mux-extraction-contract-ready=true audio-mux-audio-input-contract-ready=true audio-mux-video-only-ready=true audio-mux-source-discovery-owned=true audio-mux-extraction-owned=false audio-mux-input-owned=false audio-mux-mux-owned=false audio-mux-sync-owned=false audio-mux-ready=false audio-mux-contract-ready=true audio-mux-reason=none"),
               std::string(batchRenderedVideoAudioMuxPrerequisitesPlanSummary(
                   audioMuxPlan).toUtf8().constData()) );

    BatchRenderedVideoSourceAudioPlan invalidSourceAudioPlan;
    invalidSourceAudioPlan.reason =
        QStringLiteral("source audio contract unavailable");
    audioMuxPlan =
        batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
            invalidSourceAudioPlan);
    ASSERT_FALSE( audioMuxPlan.contractReady );
    ASSERT_FALSE( audioMuxPlan.sourceAudioContractReady );
    ASSERT_FALSE( audioMuxPlan.sourceAudioExtractionContractReady );
    ASSERT_FALSE( audioMuxPlan.sourceAudioInputContractReady );
    ASSERT_FALSE( audioMuxPlan.videoOnlyFallbackReady );
    ASSERT_FALSE( audioMuxPlan.muxReady );
    ASSERT_EQ( std::string("source audio contract unavailable"),
               std::string(audioMuxPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoFfmpegAudioContract)
{
    BatchRenderedVideoSourceAudioPlan sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanForCurrentBuild(
            QStringLiteral("C:/clips/M16-1327.MLV"));
    BatchRenderedVideoAudioMuxPrerequisitesPlan audioMuxPlan =
        batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
            sourceAudioPlan);
    BatchRenderedVideoFfmpegAudioPlan audioPlan =
        batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
            sourceAudioPlan,
            audioMuxPlan);

    ASSERT_TRUE( audioMuxPlan.contractReady );
    ASSERT_TRUE( audioPlan.contractReady );
    ASSERT_TRUE( audioPlan.videoOnlyCommandReady );
    ASSERT_TRUE( audioPlan.audioInputContractReady );
    ASSERT_FALSE( audioPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( audioPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( audioPlan.audioInputOwned );
    ASSERT_FALSE( audioPlan.audioMuxOwned );
    ASSERT_FALSE( audioPlan.audioSyncOwned );
    ASSERT_EQ( std::string("video-only-contract"),
               std::string(audioPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("-an"),
               std::string(audioPlan.audioArguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-audio-source=video-only-contract ffmpeg-audio-args=-an ffmpeg-audio-video-only-ready=true ffmpeg-audio-source-discovery-owned=false ffmpeg-audio-extraction-owned=false ffmpeg-audio-input-contract-ready=true ffmpeg-audio-input-owned=false ffmpeg-audio-mux-owned=false ffmpeg-audio-sync-owned=false ffmpeg-audio-contract-ready=true ffmpeg-audio-reason=none"),
               std::string(batchRenderedVideoFfmpegAudioPlanSummary(
                   audioPlan).toUtf8().constData()) );

    sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            true,
            2,
            48000,
            16,
            123456);
    BatchRenderedVideoSourceAudioExtractionPlan extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan,
            QStringLiteral("C:/renders/M16-1327.source-audio.wav"));
    audioMuxPlan =
        batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
            sourceAudioPlan,
            extractionPlan);
    audioPlan =
        batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
            sourceAudioPlan,
            audioMuxPlan);
    ASSERT_TRUE( audioMuxPlan.contractReady );
    ASSERT_TRUE( audioPlan.contractReady );
    ASSERT_TRUE( audioPlan.videoOnlyCommandReady );
    ASSERT_TRUE( audioPlan.audioInputContractReady );
    ASSERT_TRUE( audioPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( audioPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( audioPlan.audioInputOwned );
    ASSERT_FALSE( audioPlan.audioMuxOwned );
    ASSERT_FALSE( audioPlan.audioSyncOwned );
    ASSERT_EQ( std::string("-an"),
               std::string(audioPlan.audioArguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-audio-source=video-only-contract ffmpeg-audio-args=-an ffmpeg-audio-video-only-ready=true ffmpeg-audio-source-discovery-owned=true ffmpeg-audio-extraction-owned=false ffmpeg-audio-input-contract-ready=true ffmpeg-audio-input-owned=false ffmpeg-audio-mux-owned=false ffmpeg-audio-sync-owned=false ffmpeg-audio-contract-ready=true ffmpeg-audio-reason=none"),
               std::string(batchRenderedVideoFfmpegAudioPlanSummary(
                   audioPlan).toUtf8().constData()) );

    BatchRenderedVideoAudioMuxPrerequisitesPlan invalidAudioMuxPlan;
    invalidAudioMuxPlan.reason =
        QStringLiteral("audio mux prerequisites unavailable");
    audioPlan =
        batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
            sourceAudioPlan,
            invalidAudioMuxPlan);
    ASSERT_FALSE( audioPlan.contractReady );
    ASSERT_FALSE( audioPlan.videoOnlyCommandReady );
    ASSERT_EQ( std::string("audio mux prerequisites unavailable"),
               std::string(audioPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoFfmpegFrameGeometryFromGuiState)
{
    BatchRenderedVideoFfmpegFramePlan plan =
        batchRenderedVideoFfmpegFramePlanFromGuiState(
            5792,
            3872,
            24000.0 / 1001.0,
            STRETCH_H_100,
            STRETCH_V_100,
            false,
            0,
            0,
            false,
            BatchRenderedVideoEncoderProfile::H264);
    ASSERT_TRUE( plan.ready );
    ASSERT_EQ( 5792, plan.outputWidth );
    ASSERT_EQ( 3872, plan.outputHeight );
    ASSERT_EQ( std::string("23.976"),
               std::string(plan.frameRateArgument.toUtf8().constData()) );
    ASSERT_EQ( std::string("5792x3872"),
               std::string(plan.frameSizeArgument.toUtf8().constData()) );
    ASSERT_FALSE( plan.scaled );
    ASSERT_FALSE( plan.codecDimensionAdjusted );
    ASSERT_EQ( std::string("ffmpeg-frame-source=5792x3872 ffmpeg-frame-size=5792x3872 ffmpeg-frame-rate=23.976 ffmpeg-frame-resize=false ffmpeg-frame-resize-height-locked=false ffmpeg-frame-stretch=false ffmpeg-frame-codec-dimension-adjusted=false ffmpeg-frame-scaled=false ffmpeg-frame-ready=true ffmpeg-frame-reason=none"),
               std::string(batchRenderedVideoFfmpegFramePlanSummary(plan).toUtf8().constData()) );

    plan = batchRenderedVideoFfmpegFramePlanFromGuiState(
        1000,
        500,
        25.0,
        STRETCH_H_200,
        STRETCH_V_100,
        true,
        1920,
        0,
        true,
        BatchRenderedVideoEncoderProfile::H264);
    ASSERT_TRUE( plan.ready );
    ASSERT_EQ( 1920, plan.outputWidth );
    ASSERT_EQ( 480, plan.outputHeight );
    ASSERT_TRUE( plan.resizeEnabled );
    ASSERT_TRUE( plan.resizeHeightLocked );
    ASSERT_TRUE( plan.scaled );

    plan = batchRenderedVideoFfmpegFramePlanFromGuiState(
        1000,
        501,
        25.0,
        STRETCH_H_100,
        STRETCH_V_033,
        false,
        0,
        0,
        false,
        BatchRenderedVideoEncoderProfile::H264);
    ASSERT_TRUE( plan.ready );
    ASSERT_EQ( 3000, plan.outputWidth );
    ASSERT_EQ( 502, plan.outputHeight );
    ASSERT_TRUE( plan.stretchApplied );
    ASSERT_TRUE( plan.codecDimensionAdjusted );

    plan = batchRenderedVideoFfmpegFramePlanFromGuiState(
        1001,
        501,
        25.0,
        STRETCH_H_100,
        STRETCH_V_100,
        false,
        0,
        0,
        false,
        BatchRenderedVideoEncoderProfile::ProRes422HQ);
    ASSERT_TRUE( plan.ready );
    ASSERT_EQ( 1001, plan.outputWidth );
    ASSERT_EQ( 501, plan.outputHeight );
    ASSERT_FALSE( plan.codecDimensionAdjusted );

    plan = batchRenderedVideoFfmpegFramePlanFromGuiState(
        0,
        500,
        25.0,
        STRETCH_H_100,
        STRETCH_V_100,
        false,
        0,
        0,
        false,
        BatchRenderedVideoEncoderProfile::H264);
    ASSERT_FALSE( plan.ready );
    ASSERT_EQ( std::string("rendered source dimensions invalid"),
               std::string(plan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoFrameProcessingContract)
{
    BatchRenderedVideoSourceMetadata metadata =
        batchRenderedVideoSourceMetadata(
            5792,
            3872,
            24000.0 / 1001.0,
            STRETCH_H_100,
            STRETCH_V_100);
    BatchRenderedVideoFfmpegFramePlan framePlan =
        batchRenderedVideoFfmpegFramePlanFromMetadata(
            metadata,
            batchRenderedVideoDefaultRenderSettings(),
            BatchRenderedVideoEncoderProfile::H264);
    BatchRenderedVideoReceiptApplicationPlan receiptPlan =
        batchRenderedVideoReceiptApplicationPlanFromContracts(
            metadata,
            framePlan);
    ASSERT_TRUE( receiptPlan.contractReady );
    ASSERT_TRUE( receiptPlan.sourceMetadataReady );
    ASSERT_TRUE( receiptPlan.frameGeometryReady );
    ASSERT_TRUE( receiptPlan.inputContractReady );
    ASSERT_TRUE( receiptPlan.outputContractReady );
    ASSERT_FALSE( receiptPlan.applyToMlvOwned );
    ASSERT_FALSE( receiptPlan.processingObjectMutationOwned );
    ASSERT_FALSE( receiptPlan.cacheInvalidationOwned );
    ASSERT_FALSE( receiptPlan.cutStretchStateOwned );
    ASSERT_FALSE( receiptPlan.lookAssistApplicationOwned );
    ASSERT_FALSE( receiptPlan.receiptValidationOwned );
    ASSERT_FALSE( receiptPlan.applicationReady );
    ASSERT_EQ( std::string("receipt-application-input-output-contract"),
               std::string(receiptPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("open-mlv-runtime-plus-batch-receipt"),
               std::string(receiptPlan.inputState.toUtf8().constData()) );
    ASSERT_EQ( std::string("receipt-applied-mlv-processing-state"),
               std::string(receiptPlan.outputState.toUtf8().constData()) );
    ASSERT_EQ( std::string("receipt-application-source=receipt-application-input-output-contract receipt-application-input=open-mlv-runtime-plus-batch-receipt receipt-application-output=receipt-applied-mlv-processing-state receipt-application-metadata-ready=true receipt-application-frame-geometry-ready=true receipt-application-input-contract-ready=true receipt-application-output-contract-ready=true receipt-application-apply-owned=false receipt-application-processing-object-owned=false receipt-application-cache-invalidation-owned=false receipt-application-cut-stretch-owned=false receipt-application-look-assist-owned=false receipt-application-validation-owned=false receipt-application-ready=false receipt-application-contract-ready=true receipt-application-reason=none"),
               std::string(batchRenderedVideoReceiptApplicationPlanSummary(
                   receiptPlan).toUtf8().constData()) );
    BatchRenderedVideoFrameProcessingPlan processingPlan =
        batchRenderedVideoFrameProcessingPlanFromFramePlan(
            metadata,
            framePlan,
            receiptPlan);

    ASSERT_TRUE( processingPlan.contractReady );
    ASSERT_TRUE( processingPlan.sourceMetadataReady );
    ASSERT_TRUE( processingPlan.frameGeometryReady );
    ASSERT_TRUE( processingPlan.receiptApplicationContractReady );
    ASSERT_TRUE( processingPlan.debayerContractReady );
    ASSERT_TRUE( processingPlan.previewProcessingContractReady );
    ASSERT_TRUE( processingPlan.resizeProcessingContractReady );
    ASSERT_TRUE( processingPlan.rgb48FrameBufferContractReady );
    ASSERT_TRUE( processingPlan.frameIterationContractReady );
    ASSERT_FALSE( processingPlan.receiptApplicationOwned );
    ASSERT_FALSE( processingPlan.debayerOwned );
    ASSERT_FALSE( processingPlan.previewProcessingOwned );
    ASSERT_FALSE( processingPlan.resizeProcessingOwned );
    ASSERT_FALSE( processingPlan.rgb48FrameBufferOwned );
    ASSERT_FALSE( processingPlan.frameIterationOwned );
    ASSERT_FALSE( processingPlan.processingParityValidationOwned );
    ASSERT_FALSE( processingPlan.processingParityReady );
    ASSERT_FALSE( processingPlan.frameProcessingReady );
    ASSERT_EQ( std::string("rgb48"),
               std::string(processingPlan.rawFramePixelFormat.toUtf8().constData()) );
    ASSERT_EQ( std::string("5792x3872"),
               std::string(processingPlan.outputSize.toUtf8().constData()) );
    ASSERT_EQ( std::string("render-processing-source=headless-rendered-frame-contract render-processing-pix-fmt=rgb48 render-processing-output-size=5792x3872 render-processing-metadata-ready=true render-processing-frame-geometry-ready=true render-processing-receipt-contract-ready=true render-processing-debayer-contract-ready=true render-processing-preview-contract-ready=true render-processing-resize-contract-ready=true render-processing-rgb48-buffer-contract-ready=true render-processing-frame-iteration-contract-ready=true render-processing-receipt-owned=false render-processing-debayer-owned=false render-processing-preview-owned=false render-processing-resize-owned=false render-processing-rgb48-buffer-owned=false render-processing-frame-iteration-owned=false render-processing-parity-validation-owned=false render-processing-parity-ready=false render-processing-frame-ready=false render-processing-contract-ready=true render-processing-reason=none"),
               std::string(batchRenderedVideoFrameProcessingPlanSummary(
                   processingPlan).toUtf8().constData()) );

    metadata = batchRenderedVideoSourceMetadata(
        0,
        3872,
        24000.0 / 1001.0,
        STRETCH_H_100,
        STRETCH_V_100);
    receiptPlan = batchRenderedVideoReceiptApplicationPlanFromContracts(
        metadata,
        framePlan);
    processingPlan = batchRenderedVideoFrameProcessingPlanFromFramePlan(
        metadata,
        framePlan,
        receiptPlan);
    ASSERT_FALSE( receiptPlan.contractReady );
    ASSERT_FALSE( receiptPlan.sourceMetadataReady );
    ASSERT_EQ( std::string("rendered source dimensions invalid"),
               std::string(receiptPlan.reason.toUtf8().constData()) );
    ASSERT_FALSE( processingPlan.contractReady );
    ASSERT_FALSE( processingPlan.sourceMetadataReady );
    ASSERT_FALSE( processingPlan.receiptApplicationContractReady );
    ASSERT_FALSE( processingPlan.debayerContractReady );
    ASSERT_FALSE( processingPlan.previewProcessingContractReady );
    ASSERT_FALSE( processingPlan.resizeProcessingContractReady );
    ASSERT_FALSE( processingPlan.rgb48FrameBufferContractReady );
    ASSERT_FALSE( processingPlan.frameIterationContractReady );
    ASSERT_EQ( std::string("rendered source dimensions invalid"),
               std::string(processingPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoFfmpegBinaryResolution)
{
    BatchRenderedVideoFfmpegBinaryPlan defaultPlan =
        batchRenderedVideoFfmpegBinaryPlanFromRequestedName();
    ASSERT_FALSE( defaultPlan.pathSearchOwned );
    ASSERT_FALSE( defaultPlan.pathSearchAttempted );
    ASSERT_FALSE( defaultPlan.foundOnPath );
    ASSERT_TRUE( defaultPlan.commandExecutableReady );
    ASSERT_EQ( std::string("ffmpeg"),
               std::string(defaultPlan.resolvedExecutable.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-binary-source=default-executable-name ffmpeg-binary-request=ffmpeg ffmpeg-binary-resolved=ffmpeg ffmpeg-binary-path-search-owned=false ffmpeg-binary-path-search-attempted=false ffmpeg-binary-found=false ffmpeg-binary-command-ready=true ffmpeg-binary-reason=none"),
               std::string(batchRenderedVideoFfmpegBinaryPlanSummary(
                   defaultPlan).toUtf8().constData()) );

    BatchRenderedVideoFfmpegBinaryPlan requestedPlan =
        batchRenderedVideoFfmpegBinaryPlanFromRequestedName(
            QStringLiteral("C:/tools/ffmpeg-custom.exe"));
    ASSERT_FALSE( requestedPlan.pathSearchOwned );
    ASSERT_FALSE( requestedPlan.pathSearchAttempted );
    ASSERT_FALSE( requestedPlan.foundOnPath );
    ASSERT_TRUE( requestedPlan.commandExecutableReady );
    ASSERT_EQ( std::string("requested-executable"),
               std::string(requestedPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("C:/tools/ffmpeg-custom.exe"),
               std::string(requestedPlan.requestedExecutable.toUtf8().constData()) );
    ASSERT_EQ( std::string("C:/tools/ffmpeg-custom.exe"),
               std::string(requestedPlan.resolvedExecutable.toUtf8().constData()) );

    BatchRenderedVideoFfmpegBinaryPlan foundPlan =
        batchRenderedVideoFfmpegBinaryPlanFromResolvedPath(
            QStringLiteral("ffmpeg"),
            QStringLiteral("C:/tools/ffmpeg.exe"));
    ASSERT_TRUE( foundPlan.pathSearchOwned );
    ASSERT_TRUE( foundPlan.pathSearchAttempted );
    ASSERT_TRUE( foundPlan.foundOnPath );
    ASSERT_TRUE( foundPlan.commandExecutableReady );
    ASSERT_EQ( std::string("C:/tools/ffmpeg.exe"),
               std::string(foundPlan.resolvedExecutable.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-binary-source=path-search ffmpeg-binary-request=ffmpeg ffmpeg-binary-resolved=C:/tools/ffmpeg.exe ffmpeg-binary-path-search-owned=true ffmpeg-binary-path-search-attempted=true ffmpeg-binary-found=true ffmpeg-binary-command-ready=true ffmpeg-binary-reason=none"),
               std::string(batchRenderedVideoFfmpegBinaryPlanSummary(
                   foundPlan).toUtf8().constData()) );

    BatchRenderedVideoFfmpegBinaryPlan missingPlan =
        batchRenderedVideoFfmpegBinaryPlanFromResolvedPath(
            QStringLiteral("ffmpeg"),
            QString());
    ASSERT_TRUE( missingPlan.pathSearchOwned );
    ASSERT_TRUE( missingPlan.pathSearchAttempted );
    ASSERT_FALSE( missingPlan.foundOnPath );
    ASSERT_TRUE( missingPlan.commandExecutableReady );
    ASSERT_EQ( std::string("ffmpeg"),
               std::string(missingPlan.resolvedExecutable.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg executable not found on PATH"),
               std::string(missingPlan.reason.toUtf8().constData()) );

    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoJobPlan basePlan =
        batchRenderedVideoJobPlanFromRequest(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            request,
            batchRenderedVideoDefaultRenderSettings(),
            foundPlan);
    BatchRenderedVideoSourceMetadata metadata =
        batchRenderedVideoSourceMetadata(
            5792,
            3872,
            24000.0 / 1001.0,
            STRETCH_H_100,
            STRETCH_V_100);
    BatchRenderedVideoJobPlan plan =
        batchRenderedVideoJobPlanWithMetadata(basePlan, metadata);
    ASSERT_TRUE( plan.preflightReady );
    ASSERT_FALSE( plan.runnable );
    ASSERT_TRUE( plan.ffmpegCommandReady );
    ASSERT_EQ( std::string("C:/tools/ffmpeg.exe"),
               std::string(plan.ffmpegCommandPlan.executable.toUtf8().constData()) );
    ASSERT_TRUE( std::string(batchRenderedVideoJobPlanSummary(plan).toUtf8().constData())
        .find("ffmpeg-binary-source=path-search ffmpeg-binary-request=ffmpeg ffmpeg-binary-resolved=C:/tools/ffmpeg.exe ffmpeg-binary-path-search-owned=true ffmpeg-binary-path-search-attempted=true ffmpeg-binary-found=true ffmpeg-binary-command-ready=true") != std::string::npos );

    basePlan = batchRenderedVideoJobPlanFromRequest(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders"),
        request,
        batchRenderedVideoDefaultRenderSettings(),
        missingPlan);
    plan = batchRenderedVideoJobPlanWithMetadata(basePlan, metadata);
    ASSERT_TRUE( plan.preflightReady );
    ASSERT_FALSE( plan.runnable );
    ASSERT_TRUE( plan.ffmpegCommandReady );
    ASSERT_FALSE( plan.ffmpegBinaryPlan.foundOnPath );
    ASSERT_TRUE( std::string(batchRenderedVideoJobPlanSummary(plan).toUtf8().constData())
        .find("ffmpeg-binary-found=false ffmpeg-binary-command-ready=true ffmpeg-binary-reason=ffmpeg executable not found on PATH") != std::string::npos );

    basePlan = batchRenderedVideoJobPlanFromRequest(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders"),
        request,
        batchRenderedVideoDefaultRenderSettings(),
        requestedPlan);
    plan = batchRenderedVideoJobPlanWithMetadata(basePlan, metadata);
    ASSERT_TRUE( plan.preflightReady );
    ASSERT_FALSE( plan.runnable );
    ASSERT_TRUE( plan.ffmpegCommandReady );
    ASSERT_EQ( std::string("C:/tools/ffmpeg-custom.exe"),
               std::string(plan.ffmpegBinaryPlan.requestedExecutable
                   .toUtf8().constData()) );
    ASSERT_EQ( std::string("C:/tools/ffmpeg-custom.exe"),
               std::string(plan.ffmpegCommandPlan.executable
                   .toUtf8().constData()) );
    ASSERT_TRUE( std::string(batchRenderedVideoJobPlanSummary(plan)
        .toUtf8().constData())
        .find("ffmpeg-binary-source=requested-executable ffmpeg-binary-request=C:/tools/ffmpeg-custom.exe ffmpeg-binary-resolved=C:/tools/ffmpeg-custom.exe") != std::string::npos );
}

TEST(BatchExportFormat, PlansRenderedVideoFfmpegCommandShape)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoJobPlan basePlan =
        batchRenderedVideoJobPlanFromRequest(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            request);
    BatchRenderedVideoSourceMetadata metadata =
        batchRenderedVideoSourceMetadata(
            5792,
            3872,
            24000.0 / 1001.0,
            STRETCH_H_100,
            STRETCH_V_100);

    BatchRenderedVideoJobPlan plan =
        batchRenderedVideoJobPlanWithMetadata(basePlan, metadata);
    ASSERT_TRUE( plan.ffmpegCommandReady );
    ASSERT_TRUE( plan.ffmpegCommandPlan.ready );
    ASSERT_TRUE( plan.ffmpegCommandPlan.rawVideoPipeInputReady );
    ASSERT_TRUE( plan.ffmpegExecutionContractReady );
    ASSERT_TRUE( plan.ffmpegExecutionPlan.contractReady );
    ASSERT_TRUE( plan.ffmpegExecutionPlan.commandReady );
    ASSERT_FALSE( plan.ffmpegExecutionPlan.executionReady );
    ASSERT_FALSE( plan.ffmpegExecutionPlan.processLaunchOwned );
    ASSERT_FALSE( plan.ffmpegExecutionPlan.stdinPipeOwned );
    ASSERT_FALSE( plan.ffmpegExecutionPlan.rawFrameFeedOwned );
    ASSERT_FALSE( plan.ffmpegExecutionPlan.stderrCaptureOwned );
    ASSERT_FALSE( plan.ffmpegExecutionPlan.exitCodeValidationOwned );
    ASSERT_FALSE( plan.ffmpegExecutionPlan.timeoutOwned );
    ASSERT_FALSE( plan.ffmpegExecutionPlan.cleanupOwned );
    ASSERT_EQ( std::string("-an"),
               std::string(plan.ffmpegCommandPlan.audioArguments.toUtf8().constData()) );
    ASSERT_FALSE( plan.ffmpegCommandPlan.audioInputOwned );
    ASSERT_FALSE( plan.ffmpegCommandPlan.executionOwned );
    ASSERT_FALSE( plan.ffmpegCommandPlan.outputVerificationOwned );
    ASSERT_EQ( std::string("ffmpeg"),
               std::string(plan.ffmpegCommandPlan.executable.toUtf8().constData()) );
    ASSERT_EQ( std::string("gui-rawvideo-pipe"),
               std::string(plan.ffmpegCommandPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("rgb48"),
               std::string(plan.ffmpegCommandPlan.rawInputPixelFormat.toUtf8().constData()) );
    ASSERT_EQ( std::string("-r 23.976 -y -f rawvideo -s 5792x3872 -pix_fmt rgb48 -i -"),
               std::string(plan.ffmpegCommandPlan.rawInputArguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("rec709-default"),
               std::string(plan.ffmpegCommandPlan.colorTagSource.toUtf8().constData()) );
    ASSERT_EQ( 1, plan.ffmpegCommandPlan.colorTag );
    ASSERT_EQ( std::string("-color_primaries 1 -color_trc 1 -colorspace bt709"),
               std::string(plan.ffmpegCommandPlan.colorArguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("-r 23.976 -y -f rawvideo -s 5792x3872 -pix_fmt rgb48 -i - -c:v libx264 -preset medium -crf 14 -pix_fmt yuv420p -color_primaries 1 -color_trc 1 -colorspace bt709 -vf scale=in_color_matrix=bt601:out_color_matrix=bt709 -an \"C:/renders/M16-1327.mp4\""),
               std::string(plan.ffmpegCommandPlan.arguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg -r 23.976 -y -f rawvideo -s 5792x3872 -pix_fmt rgb48 -i - -c:v libx264 -preset medium -crf 14 -pix_fmt yuv420p -color_primaries 1 -color_trc 1 -colorspace bt709 -vf scale=in_color_matrix=bt601:out_color_matrix=bt709 -an \"C:/renders/M16-1327.mp4\""),
               std::string(plan.ffmpegCommandPlan.commandLine.toUtf8().constData()) );

    ASSERT_EQ( std::string("ffmpeg-command-source=gui-rawvideo-pipe ffmpeg-command-exe=ffmpeg ffmpeg-command-raw-pix-fmt=rgb48 ffmpeg-command-raw-input=-r 23.976 -y -f rawvideo -s 5792x3872 -pix_fmt rgb48 -i - ffmpeg-command-color-source=rec709-default ffmpeg-command-color-tag=1 ffmpeg-command-color-args=-color_primaries 1 -color_trc 1 -colorspace bt709 ffmpeg-command-audio-args=-an ffmpeg-command-audio-owned=false ffmpeg-command-execution-owned=false ffmpeg-command-output-verification-owned=false ffmpeg-command-args=-r 23.976 -y -f rawvideo -s 5792x3872 -pix_fmt rgb48 -i - -c:v libx264 -preset medium -crf 14 -pix_fmt yuv420p -color_primaries 1 -color_trc 1 -colorspace bt709 -vf scale=in_color_matrix=bt601:out_color_matrix=bt709 -an \"C:/renders/M16-1327.mp4\" ffmpeg-command-ready=true ffmpeg-command-reason=none"),
                std::string(batchRenderedVideoFfmpegCommandPlanSummary(
                    plan.ffmpegCommandPlan).toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-execution-source=command-contract ffmpeg-execution-exe=ffmpeg ffmpeg-execution-command-ready=true ffmpeg-execution-process-launch-owned=false ffmpeg-execution-stdin-pipe-owned=false ffmpeg-execution-raw-frame-feed-owned=false ffmpeg-execution-stderr-capture-owned=false ffmpeg-execution-exit-code-owned=false ffmpeg-execution-timeout-owned=false ffmpeg-execution-cleanup-owned=false ffmpeg-execution-ready=false ffmpeg-execution-contract-ready=true ffmpeg-execution-reason=none"),
               std::string(batchRenderedVideoFfmpegExecutionPlanSummary(
                   plan.ffmpegExecutionPlan).toUtf8().constData()) );

    BatchRenderedVideoFfmpegCommandPlan invalidPlan =
        batchRenderedVideoFfmpegCommandPlanFromParts(
            BatchRenderedVideoFfmpegFramePlan(),
            plan.ffmpegFilterPlan,
            plan.ffmpegVideoPlan,
            plan.outputPlan);
    ASSERT_FALSE( invalidPlan.ready );
    ASSERT_EQ( std::string("rendered ffmpeg frame plan unavailable"),
               std::string(invalidPlan.reason.toUtf8().constData()) );
    BatchRenderedVideoFfmpegExecutionPlan invalidExecutionPlan =
        batchRenderedVideoFfmpegExecutionPlanFromCommand(invalidPlan);
    ASSERT_FALSE( invalidExecutionPlan.contractReady );
    ASSERT_FALSE( invalidExecutionPlan.executionReady );
    ASSERT_EQ( std::string("rendered ffmpeg frame plan unavailable"),
               std::string(invalidExecutionPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, OverlaysRenderedVideoMetadataOntoJobPlan)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoJobPlan basePlan =
        batchRenderedVideoJobPlanFromRequest(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            request);

    ASSERT_TRUE( basePlan.preflightReady );
    ASSERT_FALSE( basePlan.metadataAttempted );
    ASSERT_FALSE( basePlan.metadataReady );
    ASSERT_FALSE( basePlan.ffmpegFrameReady );
    ASSERT_FALSE( basePlan.receiptApplicationContractReady );
    ASSERT_FALSE( basePlan.receiptApplicationPlan.contractReady );
    ASSERT_TRUE( basePlan.ffmpegFilterReady );
    ASSERT_TRUE( basePlan.optionalFilterContractReady );
    ASSERT_TRUE( basePlan.optionalFilterPlan.contractReady );
    ASSERT_TRUE( basePlan.optionalFilterPlan.baseFilterContractReady );
    ASSERT_FALSE( basePlan.optionalFilterPlan.optionalFiltersRequested );
    ASSERT_FALSE( basePlan.optionalFilterPlan.optionalFilterGraphOwned );
    ASSERT_FALSE( basePlan.optionalFilterPlan.moireeFilterOwned );
    ASSERT_FALSE( basePlan.optionalFilterPlan.hdrBlendOwned );
    ASSERT_FALSE( basePlan.optionalFilterPlan.stabilizationOwned );
    ASSERT_FALSE( basePlan.optionalFilterPlan.optionalFilterOrderOwned );
    ASSERT_FALSE( basePlan.optionalFilterPlan.optionalFilterParityOwned );
    ASSERT_FALSE( basePlan.optionalFilterPlan.optionalFilterExecutionReady );
    ASSERT_TRUE( basePlan.sourceAudioContractReady );
    ASSERT_TRUE( basePlan.sourceAudioPlan.contractReady );
    ASSERT_FALSE( basePlan.sourceAudioPlan.discoveryAttempted );
    ASSERT_TRUE( basePlan.sourceAudioExtractionContractReady );
    ASSERT_TRUE( basePlan.sourceAudioExtractionPlan.contractReady );
    ASSERT_EQ( std::string("C:/renders/M16-1327.source-audio.wav"),
               std::string(basePlan.sourceAudioExtractionPlan.plannedAudioPath
                   .toUtf8().constData()) );
    ASSERT_FALSE( basePlan.sourceAudioExtractionPlan.sourceAudioKnown );
    ASSERT_FALSE( basePlan.sourceAudioExtractionPlan.sourceAudioPresent );
    ASSERT_FALSE( basePlan.sourceAudioExtractionPlan.extractionPathPlanned );
    ASSERT_FALSE( basePlan.sourceAudioExtractionPlan.extractionProcessOwned );
    ASSERT_FALSE( basePlan.sourceAudioExtractionPlan.tempFileOwned );
    ASSERT_FALSE( basePlan.sourceAudioExtractionPlan.cleanupOwned );
    ASSERT_FALSE( basePlan.sourceAudioExtractionPlan.extractionReady );
    ASSERT_TRUE( basePlan.ffmpegAudioInputContractReady );
    ASSERT_TRUE( basePlan.ffmpegAudioInputPlan.contractReady );
    ASSERT_FALSE( basePlan.ffmpegAudioInputPlan.sourceAudioKnown );
    ASSERT_FALSE( basePlan.ffmpegAudioInputPlan.sourceAudioPresent );
    ASSERT_FALSE( basePlan.ffmpegAudioInputPlan.audioInputPlanned );
    ASSERT_FALSE( basePlan.ffmpegAudioInputPlan.audioInputOwned );
    ASSERT_FALSE( basePlan.ffmpegAudioInputPlan.audioInputReady );
    ASSERT_EQ( std::string("C:/renders/M16-1327.source-audio.wav"),
               std::string(basePlan.ffmpegAudioInputPlan.plannedAudioPath
                   .toUtf8().constData()) );
    ASSERT_EQ( std::string("-an"),
               std::string(basePlan.ffmpegAudioInputPlan.activeAudioArguments
                   .toUtf8().constData()) );
    ASSERT_TRUE( basePlan.audioMuxPrerequisitesContractReady );
    ASSERT_TRUE( basePlan.audioMuxPrerequisitesPlan.contractReady );
    ASSERT_TRUE( basePlan.audioMuxPrerequisitesPlan.sourceAudioContractReady );
    ASSERT_TRUE( basePlan.audioMuxPrerequisitesPlan.sourceAudioExtractionContractReady );
    ASSERT_TRUE( basePlan.audioMuxPrerequisitesPlan.sourceAudioInputContractReady );
    ASSERT_TRUE( basePlan.audioMuxPrerequisitesPlan.videoOnlyFallbackReady );
    ASSERT_FALSE( basePlan.audioMuxPrerequisitesPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( basePlan.audioMuxPrerequisitesPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( basePlan.audioMuxPrerequisitesPlan.audioInputOwned );
    ASSERT_FALSE( basePlan.audioMuxPrerequisitesPlan.audioMuxOwned );
    ASSERT_FALSE( basePlan.audioMuxPrerequisitesPlan.audioSyncValidationOwned );
    ASSERT_FALSE( basePlan.audioMuxPrerequisitesPlan.muxReady );
    ASSERT_TRUE( basePlan.ffmpegAudioContractReady );
    ASSERT_TRUE( basePlan.ffmpegAudioPlan.contractReady );
    ASSERT_TRUE( basePlan.ffmpegAudioPlan.audioInputContractReady );
    ASSERT_FALSE( basePlan.ffmpegAudioPlan.audioMuxOwned );
    ASSERT_FALSE( basePlan.frameProcessingContractReady );
    ASSERT_FALSE( basePlan.ffmpegExecutionContractReady );
    ASSERT_FALSE( basePlan.outputVerificationExecutionContractReady );
    ASSERT_EQ( std::string("render-settings-source=batch-defaults render-settings-explicit-headless=false render-settings-gui-owned=false render-settings-ready=true render-settings-reason=none render-settings-resize=false render-settings-resize-width=0 render-settings-resize-height=0 render-settings-resize-height-locked=false"),
               std::string(batchRenderedVideoRenderSettingsSummary(
                   basePlan.renderSettings).toUtf8().constData()) );
    ASSERT_EQ( std::string("rendered processing parity, frame processing, audio muxing, ffmpeg execution, output verification, and headless rendered-export runner are not implemented"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(basePlan).toUtf8().constData()) );

    BatchRenderedVideoSourceMetadata metadata =
        batchRenderedVideoSourceMetadata(
            5792,
            3872,
            24000.0 / 1001.0,
            STRETCH_H_100,
            STRETCH_V_100);
    ASSERT_TRUE( metadata.ready );
    ASSERT_EQ( std::string("source-metadata=5792x3872"),
               std::string(batchRenderedVideoSourceMetadataSummary(metadata)
                   .left(QStringLiteral("source-metadata=5792x3872").length())
                   .toUtf8().constData()) );

    BatchRenderedVideoRenderSettings settings =
        batchRenderedVideoRenderSettingsFromExplicitResize(
            true,
            1920,
            0,
            true);
    ASSERT_TRUE( settings.ready );
    ASSERT_TRUE( settings.explicitHeadlessSettings );
    ASSERT_FALSE( settings.guiSettingsOwned );

    BatchRenderedVideoJobPlan plan =
        batchRenderedVideoJobPlanWithMetadata(basePlan, metadata, settings);
    ASSERT_TRUE( plan.metadataAttempted );
    ASSERT_TRUE( plan.metadataReady );
    ASSERT_TRUE( plan.ffmpegFrameReady );
    ASSERT_TRUE( plan.receiptApplicationContractReady );
    ASSERT_TRUE( plan.receiptApplicationPlan.contractReady );
    ASSERT_TRUE( plan.receiptApplicationPlan.inputContractReady );
    ASSERT_TRUE( plan.receiptApplicationPlan.outputContractReady );
    ASSERT_FALSE( plan.receiptApplicationPlan.applyToMlvOwned );
    ASSERT_FALSE( plan.receiptApplicationPlan.processingObjectMutationOwned );
    ASSERT_FALSE( plan.receiptApplicationPlan.cacheInvalidationOwned );
    ASSERT_FALSE( plan.receiptApplicationPlan.cutStretchStateOwned );
    ASSERT_FALSE( plan.receiptApplicationPlan.lookAssistApplicationOwned );
    ASSERT_FALSE( plan.receiptApplicationPlan.receiptValidationOwned );
    ASSERT_FALSE( plan.receiptApplicationPlan.applicationReady );
    ASSERT_TRUE( plan.frameProcessingContractReady );
    ASSERT_TRUE( plan.frameProcessingPlan.contractReady );
    ASSERT_TRUE( plan.frameProcessingPlan.sourceMetadataReady );
    ASSERT_TRUE( plan.frameProcessingPlan.frameGeometryReady );
    ASSERT_TRUE( plan.frameProcessingPlan.receiptApplicationContractReady );
    ASSERT_TRUE( plan.frameProcessingPlan.debayerContractReady );
    ASSERT_TRUE( plan.frameProcessingPlan.previewProcessingContractReady );
    ASSERT_TRUE( plan.frameProcessingPlan.resizeProcessingContractReady );
    ASSERT_TRUE( plan.frameProcessingPlan.rgb48FrameBufferContractReady );
    ASSERT_TRUE( plan.frameProcessingPlan.frameIterationContractReady );
    ASSERT_FALSE( plan.frameProcessingPlan.processingParityReady );
    ASSERT_FALSE( plan.frameProcessingPlan.frameProcessingReady );
    ASSERT_FALSE( plan.frameProcessingPlan.receiptApplicationOwned );
    ASSERT_FALSE( plan.frameProcessingPlan.debayerOwned );
    ASSERT_FALSE( plan.frameProcessingPlan.previewProcessingOwned );
    ASSERT_FALSE( plan.frameProcessingPlan.resizeProcessingOwned );
    ASSERT_FALSE( plan.frameProcessingPlan.rgb48FrameBufferOwned );
    ASSERT_FALSE( plan.frameProcessingPlan.frameIterationOwned );
    ASSERT_FALSE( plan.frameProcessingPlan.processingParityValidationOwned );
    ASSERT_TRUE( plan.ffmpegCommandReady );
    ASSERT_TRUE( plan.ffmpegExecutionContractReady );
    ASSERT_TRUE( plan.outputVerificationExecutionContractReady );
    ASSERT_TRUE( plan.outputVerificationExecutionPlan.contractReady );
    ASSERT_TRUE( plan.outputVerificationExecutionPlan.outputVerificationContractReady );
    ASSERT_TRUE( plan.outputVerificationExecutionPlan.ffmpegExecutionContractReady );
    ASSERT_FALSE( plan.outputVerificationExecutionPlan.verificationExecutionReady );
    ASSERT_FALSE( plan.outputVerificationExecutionPlan.fileExistenceCheckOwned );
    ASSERT_FALSE( plan.outputVerificationExecutionPlan.nonEmptyCheckOwned );
    ASSERT_FALSE( plan.outputVerificationExecutionPlan.mediaProbeExecutionOwned );
    ASSERT_FALSE( plan.outputVerificationExecutionPlan.codecContainerValidationOwned );
    ASSERT_FALSE( plan.outputVerificationExecutionPlan.frameCountValidationOwned );
    ASSERT_FALSE( plan.outputVerificationExecutionPlan.receiptHashValidationOwned );
    ASSERT_TRUE( plan.preflightReady );
    ASSERT_FALSE( plan.runnable );
    ASSERT_EQ( 1920, plan.ffmpegFramePlan.outputWidth );
    ASSERT_EQ( 1284, plan.ffmpegFramePlan.outputHeight );
    ASSERT_EQ( std::string("23.976"),
               std::string(plan.ffmpegFramePlan.frameRateArgument.toUtf8().constData()) );
    ASSERT_EQ( std::string("rendered processing parity, frame processing, audio muxing, ffmpeg execution, output verification, and headless rendered-export runner are not implemented"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );

    const std::string summary =
        std::string(batchRenderedVideoJobPlanSummary(plan).toUtf8().constData());
    ASSERT_TRUE( summary.find("source-metadata=5792x3872") != std::string::npos );
    ASSERT_TRUE( summary.find("source-metadata-attempted=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-filter-source=gui-base-color-scale") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-filter-args=-vf scale=in_color_matrix=bt601:out_color_matrix=bt709") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-filter-optional-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-source=optional-filter-ownership-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-base-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-requested=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-graph-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-moiree-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-hdr-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-stabilization-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-order-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-parity-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-exec-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-source=video-only-undiscovered") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-state=unknown") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-sample-rate=0") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-channels=0") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-bits=0") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-bytes=0") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-discovery-attempted=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-known=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-video-only-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-source=source-audio-extraction-prerequisite-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-format=wav-pcm-s16le") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-path=C:/renders/M16-1327.source-audio.wav") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-known=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-present=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-path-planned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-process-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-temp-file-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-cleanup-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-source=ffmpeg-audio-input-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-path=C:/renders/M16-1327.source-audio.wav") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-planned-args=unspecified") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-active-args=-an") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-known=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-present=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-planned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-source=audio-mux-prerequisite-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-source-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-extraction-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-video-only-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-source-discovery-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-extraction-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-input-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-mux-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-sync-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-source=video-only-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-args=-an") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-mux-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-settings-source=explicit-headless render-settings-explicit-headless=true render-settings-gui-owned=false render-settings-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-settings-resize=true render-settings-resize-width=1920") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-frame-size=1920x1284") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-frame-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-source=receipt-application-input-output-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-output-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-apply-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-processing-object-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-cache-invalidation-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-cut-stretch-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-look-assist-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-validation-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-output-size=1920x1284") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-receipt-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-debayer-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-preview-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-resize-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-rgb48-buffer-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-frame-iteration-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-receipt-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-debayer-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-preview-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-rgb48-buffer-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-parity-validation-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-parity-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-frame-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-command-raw-input=-r 23.976 -y -f rawvideo -s 1920x1284 -pix_fmt rgb48 -i -") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-command-audio-owned=false ffmpeg-command-execution-owned=false ffmpeg-command-output-verification-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-command-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-execution-command-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-execution-process-launch-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-execution-stdin-pipe-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-execution-raw-frame-feed-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-execution-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-execution-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-source=post-ffmpeg-output-verification-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-probe=ffprobe") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-ffmpeg-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-file-exists-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-probe-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("preflight-ready=true runnable=false") != std::string::npos );

    BatchRenderedVideoSourceAudioPlan discoveredAudioPlan =
        batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            true,
            2,
            48000,
            16,
            123456);
    BatchRenderedVideoJobPlan audioBasePlan =
        batchRenderedVideoJobPlanWithSourceAudio(
            basePlan,
            discoveredAudioPlan);
    ASSERT_TRUE( audioBasePlan.preflightReady );
    ASSERT_TRUE( audioBasePlan.sourceAudioPlan.discoveryOwned );
    ASSERT_TRUE( audioBasePlan.sourceAudioPlan.sourceAudioKnown );
    ASSERT_TRUE( audioBasePlan.sourceAudioPlan.sourceAudioPresent );
    ASSERT_EQ( 48000, audioBasePlan.sourceAudioPlan.sampleRate );
    ASSERT_EQ( 2, audioBasePlan.sourceAudioPlan.channels );
    ASSERT_EQ( 16, audioBasePlan.sourceAudioPlan.bitsPerSample );
    ASSERT_EQ( static_cast<qulonglong>(123456),
               audioBasePlan.sourceAudioPlan.audioBytes );
    ASSERT_TRUE( audioBasePlan.sourceAudioExtractionContractReady );
    ASSERT_TRUE( audioBasePlan.sourceAudioExtractionPlan.contractReady );
    ASSERT_TRUE( audioBasePlan.sourceAudioExtractionPlan.sourceAudioKnown );
    ASSERT_TRUE( audioBasePlan.sourceAudioExtractionPlan.sourceAudioPresent );
    ASSERT_TRUE( audioBasePlan.sourceAudioExtractionPlan.sourceAudioDiscoveryOwned );
    ASSERT_TRUE( audioBasePlan.sourceAudioExtractionPlan.extractionPathPlanned );
    ASSERT_TRUE( audioBasePlan.sourceAudioExtractionPlan.extractionPathReady );
    ASSERT_FALSE( audioBasePlan.sourceAudioExtractionPlan.extractionProcessOwned );
    ASSERT_FALSE( audioBasePlan.sourceAudioExtractionPlan.tempFileOwned );
    ASSERT_FALSE( audioBasePlan.sourceAudioExtractionPlan.cleanupOwned );
    ASSERT_FALSE( audioBasePlan.sourceAudioExtractionPlan.extractionReady );
    ASSERT_TRUE( audioBasePlan.ffmpegAudioInputContractReady );
    ASSERT_TRUE( audioBasePlan.ffmpegAudioInputPlan.contractReady );
    ASSERT_TRUE( audioBasePlan.ffmpegAudioInputPlan.audioInputPlanned );
    ASSERT_FALSE( audioBasePlan.ffmpegAudioInputPlan.audioInputOwned );
    ASSERT_FALSE( audioBasePlan.ffmpegAudioInputPlan.audioInputReady );
    ASSERT_EQ( std::string("-i \"C:/renders/M16-1327.source-audio.wav\""),
               std::string(audioBasePlan.ffmpegAudioInputPlan.plannedInputArguments
                   .toUtf8().constData()) );
    ASSERT_EQ( std::string("-an"),
               std::string(audioBasePlan.ffmpegAudioInputPlan.activeAudioArguments
                   .toUtf8().constData()) );
    ASSERT_TRUE( audioBasePlan.audioMuxPrerequisitesPlan.sourceAudioDiscoveryOwned );
    ASSERT_TRUE( audioBasePlan.audioMuxPrerequisitesPlan.sourceAudioExtractionContractReady );
    ASSERT_TRUE( audioBasePlan.audioMuxPrerequisitesPlan.sourceAudioInputContractReady );
    ASSERT_FALSE( audioBasePlan.audioMuxPrerequisitesPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( audioBasePlan.audioMuxPrerequisitesPlan.audioInputOwned );
    ASSERT_FALSE( audioBasePlan.audioMuxPrerequisitesPlan.audioMuxOwned );
    ASSERT_FALSE( audioBasePlan.audioMuxPrerequisitesPlan.audioSyncValidationOwned );
    ASSERT_FALSE( audioBasePlan.audioMuxPrerequisitesPlan.muxReady );
    ASSERT_TRUE( audioBasePlan.ffmpegAudioPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( audioBasePlan.ffmpegAudioPlan.sourceAudioExtractionOwned );
    ASSERT_TRUE( audioBasePlan.ffmpegAudioPlan.audioInputContractReady );
    ASSERT_FALSE( audioBasePlan.ffmpegAudioPlan.audioInputOwned );
    ASSERT_FALSE( audioBasePlan.ffmpegAudioPlan.audioMuxOwned );
    ASSERT_FALSE( audioBasePlan.ffmpegAudioPlan.audioSyncOwned );

    BatchRenderedVideoJobPlan discoveredPlan =
        batchRenderedVideoJobPlanWithMetadata(
            audioBasePlan,
            metadata,
            settings);
    ASSERT_TRUE( discoveredPlan.preflightReady );
    ASSERT_FALSE( discoveredPlan.runnable );
    ASSERT_TRUE( discoveredPlan.ffmpegCommandReady );
    ASSERT_TRUE( discoveredPlan.ffmpegExecutionContractReady );
    ASSERT_TRUE( discoveredPlan.outputVerificationExecutionContractReady );
    ASSERT_TRUE( discoveredPlan.sourceAudioPlan.discoveryOwned );
    ASSERT_TRUE( discoveredPlan.sourceAudioExtractionPlan.contractReady );
    ASSERT_TRUE( discoveredPlan.sourceAudioExtractionPlan.sourceAudioDiscoveryOwned );
    ASSERT_TRUE( discoveredPlan.sourceAudioExtractionPlan.extractionPathPlanned );
    ASSERT_FALSE( discoveredPlan.sourceAudioExtractionPlan.extractionProcessOwned );
    ASSERT_FALSE( discoveredPlan.sourceAudioExtractionPlan.tempFileOwned );
    ASSERT_FALSE( discoveredPlan.sourceAudioExtractionPlan.cleanupOwned );
    ASSERT_FALSE( discoveredPlan.sourceAudioExtractionPlan.extractionReady );
    ASSERT_TRUE( discoveredPlan.ffmpegAudioInputContractReady );
    ASSERT_TRUE( discoveredPlan.ffmpegAudioInputPlan.contractReady );
    ASSERT_TRUE( discoveredPlan.ffmpegAudioInputPlan.audioInputPlanned );
    ASSERT_FALSE( discoveredPlan.ffmpegAudioInputPlan.audioInputOwned );
    ASSERT_FALSE( discoveredPlan.ffmpegAudioInputPlan.audioInputReady );
    ASSERT_EQ( std::string("-i \"C:/renders/M16-1327.source-audio.wav\""),
               std::string(discoveredPlan.ffmpegAudioInputPlan.plannedInputArguments
                   .toUtf8().constData()) );
    ASSERT_TRUE( discoveredPlan.audioMuxPrerequisitesPlan.sourceAudioDiscoveryOwned );
    ASSERT_TRUE( discoveredPlan.audioMuxPrerequisitesPlan.sourceAudioExtractionContractReady );
    ASSERT_TRUE( discoveredPlan.audioMuxPrerequisitesPlan.sourceAudioInputContractReady );
    ASSERT_FALSE( discoveredPlan.audioMuxPrerequisitesPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( discoveredPlan.audioMuxPrerequisitesPlan.audioInputOwned );
    ASSERT_FALSE( discoveredPlan.audioMuxPrerequisitesPlan.audioMuxOwned );
    ASSERT_FALSE( discoveredPlan.audioMuxPrerequisitesPlan.audioSyncValidationOwned );
    ASSERT_FALSE( discoveredPlan.audioMuxPrerequisitesPlan.muxReady );
    ASSERT_TRUE( discoveredPlan.ffmpegAudioPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( discoveredPlan.ffmpegAudioPlan.sourceAudioExtractionOwned );
    ASSERT_TRUE( discoveredPlan.ffmpegAudioPlan.audioInputContractReady );
    ASSERT_FALSE( discoveredPlan.ffmpegAudioPlan.audioInputOwned );
    ASSERT_FALSE( discoveredPlan.ffmpegAudioPlan.audioMuxOwned );
    ASSERT_FALSE( discoveredPlan.ffmpegAudioPlan.audioSyncOwned );
    ASSERT_EQ( std::string("-an"),
               std::string(discoveredPlan.ffmpegCommandPlan.audioArguments
                   .toUtf8().constData()) );
    const std::string discoveredSummary =
        std::string(batchRenderedVideoJobPlanSummary(
            discoveredPlan).toUtf8().constData());
    ASSERT_TRUE( discoveredSummary.find("source-audio-source=open-mlv-audio-metadata") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-state=present") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-sample-rate=48000") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-channels=2") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-bits=16") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-bytes=123456") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-discovery-owned=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-mux-input-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-sync-validation-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-known=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-present=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-discovery-owned=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-path-planned=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-process-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-temp-file-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-cleanup-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-ready=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("source-audio-extraction-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-source=ffmpeg-audio-input-contract") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-path=C:/renders/M16-1327.source-audio.wav") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-planned-args=-i \"C:/renders/M16-1327.source-audio.wav\"") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-active-args=-an") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-known=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-present=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-planned=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-ready=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("audio-mux-source-discovery-owned=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("audio-mux-extraction-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("audio-mux-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("audio-mux-extraction-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("audio-mux-input-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("audio-mux-mux-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("audio-mux-sync-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("audio-mux-ready=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-source-discovery-owned=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-extraction-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-input-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-mux-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-audio-sync-owned=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("ffmpeg-command-audio-args=-an") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("runner-audio-mux-ready=false") != std::string::npos );
    ASSERT_TRUE( discoveredSummary.find("preflight-ready=true runnable=false") != std::string::npos );

    BatchRenderedVideoJobPlan settingsBasePlan =
        batchRenderedVideoJobPlanFromRequest(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            request,
            settings);
    ASSERT_TRUE( settingsBasePlan.preflightReady );
    ASSERT_TRUE( settingsBasePlan.renderSettings.explicitHeadlessSettings );

    BatchRenderedVideoJobPlan implicitSettingsPlan =
        batchRenderedVideoJobPlanWithMetadata(settingsBasePlan, metadata);
    ASSERT_TRUE( implicitSettingsPlan.metadataAttempted );
    ASSERT_TRUE( implicitSettingsPlan.metadataReady );
    ASSERT_TRUE( implicitSettingsPlan.ffmpegFrameReady );
    ASSERT_TRUE( implicitSettingsPlan.receiptApplicationContractReady );
    ASSERT_TRUE( implicitSettingsPlan.frameProcessingContractReady );
    ASSERT_TRUE( implicitSettingsPlan.ffmpegCommandReady );
    ASSERT_TRUE( implicitSettingsPlan.ffmpegExecutionContractReady );
    ASSERT_TRUE( implicitSettingsPlan.outputVerificationExecutionContractReady );
    ASSERT_TRUE( implicitSettingsPlan.preflightReady );
    ASSERT_FALSE( implicitSettingsPlan.runnable );
    ASSERT_EQ( 1920, implicitSettingsPlan.ffmpegFramePlan.outputWidth );
    ASSERT_EQ( 1284, implicitSettingsPlan.ffmpegFramePlan.outputHeight );
    ASSERT_TRUE( implicitSettingsPlan.renderSettings.explicitHeadlessSettings );
    const std::string implicitSettingsSummary =
        std::string(batchRenderedVideoJobPlanSummary(
            implicitSettingsPlan).toUtf8().constData());
    ASSERT_TRUE( implicitSettingsSummary.find("render-settings-source=explicit-headless render-settings-explicit-headless=true") != std::string::npos );
    ASSERT_TRUE( implicitSettingsSummary.find("ffmpeg-frame-size=1920x1284") != std::string::npos );

    BatchRenderedVideoRenderSettings invalidSettings =
        batchRenderedVideoRenderSettingsFromExplicitResize(
            true,
            0,
            1080,
            false);
    ASSERT_FALSE( invalidSettings.ready );
    plan = batchRenderedVideoJobPlanWithMetadata(
        basePlan,
        metadata,
        invalidSettings);
    ASSERT_TRUE( plan.metadataAttempted );
    ASSERT_TRUE( plan.metadataReady );
    ASSERT_FALSE( plan.ffmpegFrameReady );
    ASSERT_FALSE( plan.frameProcessingContractReady );
    ASSERT_FALSE( plan.ffmpegCommandReady );
    ASSERT_FALSE( plan.ffmpegExecutionContractReady );
    ASSERT_FALSE( plan.outputVerificationExecutionContractReady );
    ASSERT_FALSE( plan.preflightReady );
    ASSERT_EQ( std::string("rendered resize dimensions invalid"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );

    metadata = batchRenderedVideoSourceMetadata(
        0,
        3872,
        24000.0 / 1001.0,
        STRETCH_H_100,
        STRETCH_V_100);
    plan = batchRenderedVideoJobPlanWithMetadata(basePlan, metadata, settings);
    ASSERT_TRUE( plan.metadataAttempted );
    ASSERT_FALSE( plan.metadataReady );
    ASSERT_FALSE( plan.ffmpegFrameReady );
    ASSERT_FALSE( plan.frameProcessingContractReady );
    ASSERT_FALSE( plan.ffmpegCommandReady );
    ASSERT_FALSE( plan.ffmpegExecutionContractReady );
    ASSERT_FALSE( plan.outputVerificationExecutionContractReady );
    ASSERT_FALSE( plan.preflightReady );
    ASSERT_EQ( std::string("rendered source dimensions invalid"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );

    BatchRenderedVideoJobPlan invalidOutputPlan =
        batchRenderedVideoJobPlanFromRequest(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders/custom.mov"),
            request);
    plan = batchRenderedVideoJobPlanWithMetadata(
        invalidOutputPlan,
        metadata,
        settings);
    ASSERT_EQ( std::string("output path extension does not match target extension"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );
}

TEST(BatchRunner, BuildsRenderedVideoMetadataFromClipState)
{
    BatchRenderedVideoSourceMetadata metadata =
        BatchRunner::renderedVideoSourceMetadataFromClipState(
            5792,
            3872,
            24000.0 / 1001.0,
            -1.0,
            0.0);
    ASSERT_TRUE( metadata.ready );
    ASSERT_EQ( 5792, metadata.width );
    ASSERT_EQ( 3872, metadata.height );
    ASSERT_EQ( STRETCH_H_100, metadata.stretchFactorX );
    ASSERT_EQ( STRETCH_V_100, metadata.stretchFactorY );

    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoJobPlan plan =
        batchRenderedVideoJobPlanWithMetadata(
            batchRenderedVideoJobPlanFromRequest(
                QStringLiteral("C:/clips/M16-1327.MLV"),
                QStringLiteral("C:/renders"),
                request),
            metadata);
    ASSERT_TRUE( plan.metadataAttempted );
    ASSERT_TRUE( plan.metadataReady );
    ASSERT_TRUE( plan.ffmpegFrameReady );
    ASSERT_TRUE( plan.frameProcessingContractReady );
    ASSERT_TRUE( plan.optionalFilterContractReady );
    ASSERT_TRUE( plan.audioMuxPrerequisitesContractReady );
    ASSERT_TRUE( plan.audioMuxPrerequisitesPlan.contractReady );
    ASSERT_FALSE( plan.audioMuxPrerequisitesPlan.muxReady );
    ASSERT_TRUE( plan.ffmpegCommandReady );
    ASSERT_TRUE( plan.ffmpegExecutionContractReady );
    ASSERT_TRUE( plan.outputVerificationExecutionContractReady );
    ASSERT_TRUE( plan.preflightReady );
    ASSERT_FALSE( plan.runnable );
    ASSERT_EQ( std::string("rendered processing parity, frame processing, audio muxing, ffmpeg execution, output verification, and headless rendered-export runner are not implemented"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );

    metadata = BatchRunner::renderedVideoSourceMetadataFromClipState(
        5792,
        3872,
        0.0,
        STRETCH_H_100,
        STRETCH_V_100);
    ASSERT_FALSE( metadata.ready );
    ASSERT_EQ( std::string("rendered frame rate invalid"),
               std::string(metadata.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoOutputPaths)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoTarget target =
        batchRenderedVideoTargetFromRequest(request);
    BatchRenderedVideoOutputPlan plan =
        batchRenderedVideoOutputPlanFromPaths(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            target);
    ASSERT_TRUE( plan.ready );
    ASSERT_EQ( std::string("C:/renders/M16-1327.mp4"),
               std::string(plan.outputPath.toUtf8().constData()) );
    ASSERT_FALSE( plan.explicitFileOutput );
    ASSERT_EQ( 1, plan.inputClipCount );
    ASSERT_TRUE( plan.reason.isEmpty() );
    ASSERT_EQ( std::string("rendered-output=C:/renders/M16-1327.mp4 rendered-output-explicit-file=false rendered-output-input-clips=1 rendered-output-ready=true rendered-output-reason=none"),
               std::string(batchRenderedVideoOutputPlanSummary(
                   QStringLiteral("C:/clips/M16-1327.MLV"),
                   QStringLiteral("C:/renders"),
                   request).toUtf8().constData()) );

    plan = batchRenderedVideoOutputPlanFromPaths(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders/custom.mp4"),
        target);
    ASSERT_TRUE( plan.ready );
    ASSERT_EQ( std::string("C:/renders/custom.mp4"),
               std::string(plan.outputPath.toUtf8().constData()) );
    ASSERT_TRUE( plan.explicitFileOutput );
    ASSERT_EQ( 1, plan.inputClipCount );

    plan = batchRenderedVideoOutputPlanFromPaths(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders"),
        target,
        2);
    ASSERT_TRUE( plan.ready );
    ASSERT_EQ( std::string("C:/renders/M16-1327.mp4"),
               std::string(plan.outputPath.toUtf8().constData()) );
    ASSERT_FALSE( plan.explicitFileOutput );
    ASSERT_EQ( 2, plan.inputClipCount );

    plan = batchRenderedVideoOutputPlanFromPaths(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders/custom.mp4"),
        target,
        2);
    ASSERT_FALSE( plan.ready );
    ASSERT_TRUE( plan.outputPath.isEmpty() );
    ASSERT_TRUE( plan.explicitFileOutput );
    ASSERT_EQ( 2, plan.inputClipCount );
    ASSERT_EQ( std::string("explicit rendered output file requires a single input clip"),
               std::string(plan.reason.toUtf8().constData()) );
    ASSERT_EQ( std::string("rendered-output=unspecified rendered-output-explicit-file=true rendered-output-input-clips=2 rendered-output-ready=false rendered-output-reason=explicit rendered output file requires a single input clip"),
               std::string(batchRenderedVideoOutputPlanSummary(plan).toUtf8().constData()) );

    plan = batchRenderedVideoOutputPlanFromPaths(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders/custom.mov"),
        target);
    ASSERT_FALSE( plan.ready );
    ASSERT_TRUE( plan.outputPath.isEmpty() );
    ASSERT_EQ( std::string("output path extension does not match target extension"),
               std::string(plan.reason.toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("rendered-video"));
    target = batchRenderedVideoTargetFromRequest(request);
    plan = batchRenderedVideoOutputPlanFromPaths(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders"),
        target);
    ASSERT_FALSE( plan.ready );
    ASSERT_TRUE( plan.outputPath.isEmpty() );
    ASSERT_EQ( std::string("rendered target incomplete"),
               std::string(plan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoOutputVerificationContract)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoTarget target =
        batchRenderedVideoTargetFromRequest(request);
    BatchRenderedVideoOutputPlan outputPlan =
        batchRenderedVideoOutputPlanFromPaths(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            target);

    BatchRenderedVideoOutputVerificationPlan verificationPlan =
        batchRenderedVideoOutputVerificationPlanFromOutput(
            outputPlan,
            target);
    ASSERT_TRUE( verificationPlan.contractReady );
    ASSERT_TRUE( verificationPlan.outputPathReady );
    ASSERT_TRUE( verificationPlan.extensionMatchesTarget );
    ASSERT_FALSE( verificationPlan.fileExistenceCheckOwned );
    ASSERT_FALSE( verificationPlan.nonEmptyCheckOwned );
    ASSERT_FALSE( verificationPlan.mediaProbeOwned );
    ASSERT_FALSE( verificationPlan.codecContainerCheckOwned );
    ASSERT_FALSE( verificationPlan.frameCountCheckOwned );
    ASSERT_FALSE( verificationPlan.receiptOrHashOwned );
    ASSERT_FALSE( verificationPlan.verificationExecutionOwned );
    ASSERT_EQ( std::string("C:/renders/M16-1327.mp4"),
               std::string(verificationPlan.expectedOutputPath.toUtf8().constData()) );
    ASSERT_EQ( std::string(".mp4"),
               std::string(verificationPlan.expectedExtension.toUtf8().constData()) );
    ASSERT_EQ( std::string("output-verification-source=planned-output-contract output-verification-path=C:/renders/M16-1327.mp4 output-verification-extension=.mp4 output-verification-path-ready=true output-verification-extension-match=true output-verification-file-exists-owned=false output-verification-nonempty-owned=false output-verification-probe-owned=false output-verification-codec-container-owned=false output-verification-frame-count-owned=false output-verification-receipt-hash-owned=false output-verification-execution-owned=false output-verification-contract-ready=true output-verification-reason=none"),
               std::string(batchRenderedVideoOutputVerificationPlanSummary(
                   verificationPlan).toUtf8().constData()) );

    BatchRenderedVideoJobPlan jobPlan =
        batchRenderedVideoJobPlanFromRequest(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            request);
    ASSERT_TRUE( jobPlan.outputVerificationContractReady );
    ASSERT_TRUE( jobPlan.outputVerificationPlan.contractReady );
    ASSERT_TRUE( jobPlan.preflightReady );
    ASSERT_FALSE( jobPlan.runnable );
    const std::string summary =
        std::string(batchRenderedVideoJobPlanSummary(jobPlan).toUtf8().constData());
    ASSERT_TRUE( summary.find("output-verification-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-file-exists-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-execution-owned=false") != std::string::npos );

    outputPlan = batchRenderedVideoOutputPlanFromPaths(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders/custom.mov"),
        target);
    verificationPlan =
        batchRenderedVideoOutputVerificationPlanFromOutput(
            outputPlan,
            target);
    ASSERT_FALSE( verificationPlan.contractReady );
    ASSERT_FALSE( verificationPlan.outputPathReady );
    ASSERT_EQ( std::string("output path extension does not match target extension"),
               std::string(verificationPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoOutputVerificationExecutionContract)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoTarget target =
        batchRenderedVideoTargetFromRequest(request);
    BatchRenderedVideoOutputPlan outputPlan =
        batchRenderedVideoOutputPlanFromPaths(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            target);
    BatchRenderedVideoOutputVerificationPlan verificationPlan =
        batchRenderedVideoOutputVerificationPlanFromOutput(
            outputPlan,
            target);
    BatchRenderedVideoSourceMetadata metadata =
        batchRenderedVideoSourceMetadata(
            5792,
            3872,
            24000.0 / 1001.0,
            STRETCH_H_100,
            STRETCH_V_100);
    BatchRenderedVideoFfmpegFramePlan framePlan =
        batchRenderedVideoFfmpegFramePlanFromMetadata(
            metadata,
            batchRenderedVideoDefaultRenderSettings(),
            BatchRenderedVideoEncoderProfile::H264);
    BatchRenderedVideoFfmpegCommandPlan commandPlan =
        batchRenderedVideoFfmpegCommandPlanFromParts(
            framePlan,
            batchRenderedVideoFfmpegFilterPlanForCurrentBuild(),
            batchRenderedVideoFfmpegVideoPlanFromEncoderPreset(
                batchRenderedVideoEncoderPresetFromTarget(target)),
            outputPlan);
    BatchRenderedVideoFfmpegExecutionPlan ffmpegExecutionPlan =
        batchRenderedVideoFfmpegExecutionPlanFromCommand(commandPlan);

    BatchRenderedVideoOutputVerificationExecutionPlan executionPlan =
        batchRenderedVideoOutputVerificationExecutionPlanFromContracts(
            verificationPlan,
            ffmpegExecutionPlan);
    ASSERT_TRUE( executionPlan.contractReady );
    ASSERT_TRUE( executionPlan.outputVerificationContractReady );
    ASSERT_TRUE( executionPlan.ffmpegExecutionContractReady );
    ASSERT_FALSE( executionPlan.fileExistenceCheckOwned );
    ASSERT_FALSE( executionPlan.nonEmptyCheckOwned );
    ASSERT_FALSE( executionPlan.mediaProbeExecutionOwned );
    ASSERT_FALSE( executionPlan.codecContainerValidationOwned );
    ASSERT_FALSE( executionPlan.frameCountValidationOwned );
    ASSERT_FALSE( executionPlan.receiptHashValidationOwned );
    ASSERT_FALSE( executionPlan.verificationExecutionReady );
    ASSERT_EQ( std::string("ffprobe"),
               std::string(executionPlan.mediaProbeExecutable.toUtf8().constData()) );
    ASSERT_EQ( std::string("output-verification-exec-source=post-ffmpeg-output-verification-contract output-verification-exec-path=C:/renders/M16-1327.mp4 output-verification-exec-extension=.mp4 output-verification-exec-probe=ffprobe output-verification-exec-output-contract-ready=true output-verification-exec-ffmpeg-contract-ready=true output-verification-exec-file-exists-owned=false output-verification-exec-nonempty-owned=false output-verification-exec-probe-owned=false output-verification-exec-codec-container-owned=false output-verification-exec-frame-count-owned=false output-verification-exec-receipt-hash-owned=false output-verification-exec-ready=false output-verification-exec-contract-ready=true output-verification-exec-reason=none"),
               std::string(batchRenderedVideoOutputVerificationExecutionPlanSummary(
                   executionPlan).toUtf8().constData()) );

    BatchRenderedVideoFfmpegExecutionPlan invalidFfmpegExecutionPlan;
    executionPlan =
        batchRenderedVideoOutputVerificationExecutionPlanFromContracts(
            verificationPlan,
            invalidFfmpegExecutionPlan);
    ASSERT_FALSE( executionPlan.contractReady );
    ASSERT_TRUE( executionPlan.outputVerificationContractReady );
    ASSERT_FALSE( executionPlan.ffmpegExecutionContractReady );
    ASSERT_EQ( std::string("rendered ffmpeg execution contract unavailable"),
               std::string(executionPlan.reason.toUtf8().constData()) );
}

TEST(BatchExportFormat, PlansRenderedVideoJobPreflightButKeepsRunnerBlocked)
{
    BatchExportFormatRequest request =
        batchExportFormatRequestFromString(QStringLiteral("h264"));
    BatchRenderedVideoJobPlan plan =
        batchRenderedVideoJobPlanFromRequest(
            QStringLiteral("C:/clips/M16-1327.MLV"),
            QStringLiteral("C:/renders"),
            request);

    ASSERT_TRUE( plan.requestValid );
    ASSERT_TRUE( plan.targetReady );
    ASSERT_TRUE( plan.encoderReady );
    ASSERT_TRUE( plan.ffmpegVideoReady );
    ASSERT_TRUE( plan.ffmpegFilterReady );
    ASSERT_TRUE( plan.optionalFilterContractReady );
    ASSERT_TRUE( plan.optionalFilterPlan.contractReady );
    ASSERT_FALSE( plan.optionalFilterPlan.optionalFiltersRequested );
    ASSERT_FALSE( plan.optionalFilterPlan.optionalFilterExecutionReady );
    ASSERT_TRUE( plan.sourceAudioContractReady );
    ASSERT_TRUE( plan.sourceAudioPlan.contractReady );
    ASSERT_TRUE( plan.sourceAudioExtractionContractReady );
    ASSERT_TRUE( plan.sourceAudioExtractionPlan.contractReady );
    ASSERT_FALSE( plan.sourceAudioExtractionPlan.sourceAudioKnown );
    ASSERT_FALSE( plan.sourceAudioExtractionPlan.sourceAudioPresent );
    ASSERT_FALSE( plan.sourceAudioExtractionPlan.extractionPathPlanned );
    ASSERT_FALSE( plan.sourceAudioExtractionPlan.extractionProcessOwned );
    ASSERT_FALSE( plan.sourceAudioExtractionPlan.tempFileOwned );
    ASSERT_FALSE( plan.sourceAudioExtractionPlan.cleanupOwned );
    ASSERT_FALSE( plan.sourceAudioExtractionPlan.extractionReady );
    ASSERT_TRUE( plan.ffmpegAudioInputContractReady );
    ASSERT_TRUE( plan.ffmpegAudioInputPlan.contractReady );
    ASSERT_FALSE( plan.ffmpegAudioInputPlan.sourceAudioKnown );
    ASSERT_FALSE( plan.ffmpegAudioInputPlan.sourceAudioPresent );
    ASSERT_FALSE( plan.ffmpegAudioInputPlan.audioInputPlanned );
    ASSERT_FALSE( plan.ffmpegAudioInputPlan.audioInputOwned );
    ASSERT_FALSE( plan.ffmpegAudioInputPlan.audioInputReady );
    ASSERT_EQ( std::string("C:/renders/M16-1327.source-audio.wav"),
               std::string(plan.ffmpegAudioInputPlan.plannedAudioPath
                   .toUtf8().constData()) );
    ASSERT_EQ( std::string("-an"),
               std::string(plan.ffmpegAudioInputPlan.activeAudioArguments
                   .toUtf8().constData()) );
    ASSERT_TRUE( plan.audioMuxPrerequisitesContractReady );
    ASSERT_TRUE( plan.audioMuxPrerequisitesPlan.contractReady );
    ASSERT_TRUE( plan.audioMuxPrerequisitesPlan.sourceAudioContractReady );
    ASSERT_TRUE( plan.audioMuxPrerequisitesPlan.sourceAudioExtractionContractReady );
    ASSERT_TRUE( plan.audioMuxPrerequisitesPlan.sourceAudioInputContractReady );
    ASSERT_TRUE( plan.audioMuxPrerequisitesPlan.videoOnlyFallbackReady );
    ASSERT_FALSE( plan.audioMuxPrerequisitesPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( plan.audioMuxPrerequisitesPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( plan.audioMuxPrerequisitesPlan.audioInputOwned );
    ASSERT_FALSE( plan.audioMuxPrerequisitesPlan.audioMuxOwned );
    ASSERT_FALSE( plan.audioMuxPrerequisitesPlan.audioSyncValidationOwned );
    ASSERT_FALSE( plan.audioMuxPrerequisitesPlan.muxReady );
    ASSERT_TRUE( plan.ffmpegAudioContractReady );
    ASSERT_TRUE( plan.ffmpegAudioPlan.audioInputContractReady );
    ASSERT_FALSE( plan.receiptApplicationContractReady );
    ASSERT_FALSE( plan.receiptApplicationPlan.contractReady );
    ASSERT_FALSE( plan.frameProcessingContractReady );
    ASSERT_FALSE( plan.ffmpegCommandReady );
    ASSERT_FALSE( plan.ffmpegExecutionContractReady );
    ASSERT_FALSE( plan.outputVerificationExecutionContractReady );
    ASSERT_TRUE( plan.outputReady );
    ASSERT_TRUE( plan.preflightReady );
    ASSERT_FALSE( plan.runnerPrerequisites.processingParityReady );
    ASSERT_FALSE( plan.runnerPrerequisites.frameProcessingReady );
    ASSERT_FALSE( plan.runnerPrerequisites.audioMuxReady );
    ASSERT_FALSE( plan.runnerPrerequisites.ffmpegExecutionReady );
    ASSERT_FALSE( plan.runnerPrerequisites.outputVerificationReady );
    ASSERT_FALSE( plan.runnerPrerequisites.headlessRunnerReady );
    ASSERT_FALSE( plan.runnerPrerequisites.ready );
    ASSERT_FALSE( plan.runnable );
    ASSERT_EQ( std::string("C:/renders/M16-1327.mp4"),
               std::string(plan.outputPlan.outputPath.toUtf8().constData()) );
    ASSERT_EQ( std::string("rendered processing parity, frame processing, audio muxing, ffmpeg execution, output verification, and headless rendered-export runner are not implemented"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );
    ASSERT_EQ( std::string("runner-processing-parity-ready=false runner-frame-processing-ready=false runner-audio-mux-ready=false runner-ffmpeg-execution-ready=false runner-output-verification-ready=false runner-headless-export-ready=false runner-ready=false runner-reason=rendered processing parity, frame processing, audio muxing, ffmpeg execution, output verification, and headless rendered-export runner are not implemented"),
               std::string(batchRenderedVideoRunnerPrerequisitesSummary(
                   plan.runnerPrerequisites).toUtf8().constData()) );

    const std::string summary =
        std::string(batchRenderedVideoJobPlanSummary(plan).toUtf8().constData());
    ASSERT_TRUE( summary.find("request=rendered-video codec=h264 container=unspecified") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-video-args=-c:v libx264 -preset medium -crf 14 -pix_fmt yuv420p") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-filter-args=-vf scale=in_color_matrix=bt601:out_color_matrix=bt709") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-filter-optional-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-source=optional-filter-ownership-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-graph-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-exec-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("optional-filter-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-source=video-only-undiscovered") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-sample-rate=0") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-channels=0") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-bits=0") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-bytes=0") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-discovery-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-known=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-video-only-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-source=source-audio-extraction-prerequisite-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-path=C:/renders/M16-1327.source-audio.wav") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-path-planned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-process-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-temp-file-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-cleanup-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("source-audio-extraction-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-source=ffmpeg-audio-input-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-path=C:/renders/M16-1327.source-audio.wav") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-planned-args=unspecified") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-active-args=-an") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-known=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-present=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-planned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-source=audio-mux-prerequisite-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-source-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-extraction-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-video-only-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-source-discovery-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-extraction-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-input-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-mux-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-sync-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("audio-mux-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-source=video-only-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-source-discovery-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-extraction-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-input-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-mux-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-contract-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("receipt-application-reason=rendered source metadata unavailable") != std::string::npos );
    ASSERT_TRUE( summary.find("render-processing-contract-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-command-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-execution-command-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-execution-contract-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("rendered-output=C:/renders/M16-1327.mp4 rendered-output-explicit-file=false rendered-output-input-clips=1 rendered-output-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-output-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-ffmpeg-contract-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-probe=ffprobe") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("output-verification-exec-contract-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("preflight-ready=true runnable=false") != std::string::npos );

    BatchRenderedVideoRenderSettings invalidSettings =
        batchRenderedVideoRenderSettingsFromExplicitResize(
            true,
            0,
            1080,
            false);
    ASSERT_FALSE( invalidSettings.ready );
    plan = batchRenderedVideoJobPlanFromRequest(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders"),
        request,
        invalidSettings);

    ASSERT_TRUE( plan.requestValid );
    ASSERT_TRUE( plan.targetReady );
    ASSERT_TRUE( plan.encoderReady );
    ASSERT_TRUE( plan.ffmpegVideoReady );
    ASSERT_TRUE( plan.ffmpegFilterReady );
    ASSERT_TRUE( plan.optionalFilterContractReady );
    ASSERT_FALSE( plan.frameProcessingContractReady );
    ASSERT_FALSE( plan.ffmpegCommandReady );
    ASSERT_FALSE( plan.ffmpegExecutionContractReady );
    ASSERT_FALSE( plan.outputVerificationExecutionContractReady );
    ASSERT_FALSE( plan.renderSettings.ready );
    ASSERT_TRUE( plan.outputReady );
    ASSERT_FALSE( plan.preflightReady );
    ASSERT_FALSE( plan.runnable );
    ASSERT_EQ( std::string("rendered resize dimensions invalid"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );
    ASSERT_TRUE( std::string(batchRenderedVideoJobPlanSummary(plan).toUtf8().constData())
        .find("render-settings-ready=false render-settings-reason=rendered resize dimensions invalid") != std::string::npos );

    plan = batchRenderedVideoJobPlanFromRequest(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders/custom.mp4"),
        request,
        2);

    ASSERT_TRUE( plan.requestValid );
    ASSERT_TRUE( plan.targetReady );
    ASSERT_TRUE( plan.encoderReady );
    ASSERT_TRUE( plan.ffmpegVideoReady );
    ASSERT_TRUE( plan.ffmpegFilterReady );
    ASSERT_TRUE( plan.optionalFilterContractReady );
    ASSERT_FALSE( plan.frameProcessingContractReady );
    ASSERT_FALSE( plan.ffmpegCommandReady );
    ASSERT_FALSE( plan.ffmpegExecutionContractReady );
    ASSERT_FALSE( plan.outputVerificationExecutionContractReady );
    ASSERT_FALSE( plan.outputReady );
    ASSERT_FALSE( plan.preflightReady );
    ASSERT_FALSE( plan.runnable );
    ASSERT_TRUE( plan.outputPlan.explicitFileOutput );
    ASSERT_EQ( 2, plan.outputPlan.inputClipCount );
    ASSERT_EQ( std::string("explicit rendered output file requires a single input clip"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );

    request = batchExportFormatRequestFromString(QStringLiteral("rendered-video"));
    plan = batchRenderedVideoJobPlanFromRequest(
        QStringLiteral("C:/clips/M16-1327.MLV"),
        QStringLiteral("C:/renders"),
        request);

    ASSERT_TRUE( plan.requestValid );
    ASSERT_FALSE( plan.targetReady );
    ASSERT_FALSE( plan.preflightReady );
    ASSERT_FALSE( plan.runnable );
    ASSERT_EQ( std::string("rendered target incomplete"),
               std::string(batchRenderedVideoJobPlanFirstBlocker(plan).toUtf8().constData()) );
}

TEST(BatchExportFormat, RejectsUnknownFormats)
{
    ASSERT_EQ( static_cast<int>(BatchExportFormat::Unknown),
               static_cast<int>(batchExportFormatFromString(QStringLiteral("gif"))) );
    ASSERT_EQ( std::string("unknown"),
               std::string(batchExportFormatName(BatchExportFormat::Unknown)) );
}

TEST(BatchRunner, PerClipReceiptCopyDoesNotMutateSharedBaseReceipt)
{
    ReceiptSettings base;
    base.setExposure(0);
    base.setTemperature(6000);
    base.setRawBlack(-1);
    base.setRawWhite(-1);
    base.setLookAssistBaselineValid(false);

    ReceiptSettings clipReceipt = base;
    clipReceipt.setExposure(178);
    clipReceipt.setTemperature(5840);
    clipReceipt.setRawBlack(20470);
    clipReceipt.setRawWhite(6000);
    clipReceipt.setLookAssistBaselineValid(true);

    ASSERT_EQ(0, base.exposure());
    ASSERT_EQ(6000, base.temperature());
    ASSERT_EQ(-1, base.rawBlack());
    ASSERT_EQ(-1, base.rawWhite());
    ASSERT_FALSE(base.lookAssistBaselineValid());
}
