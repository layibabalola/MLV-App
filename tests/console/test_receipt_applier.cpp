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
}

TEST(BatchExportFormat, PlansRenderedVideoFfmpegAudioContract)
{
    BatchRenderedVideoFfmpegAudioPlan audioPlan =
        batchRenderedVideoFfmpegAudioPlanForCurrentBuild();

    ASSERT_TRUE( audioPlan.contractReady );
    ASSERT_TRUE( audioPlan.videoOnlyCommandReady );
    ASSERT_FALSE( audioPlan.sourceAudioDiscoveryOwned );
    ASSERT_FALSE( audioPlan.sourceAudioExtractionOwned );
    ASSERT_FALSE( audioPlan.audioInputOwned );
    ASSERT_FALSE( audioPlan.audioMuxOwned );
    ASSERT_FALSE( audioPlan.audioSyncOwned );
    ASSERT_EQ( std::string("video-only-contract"),
               std::string(audioPlan.source.toUtf8().constData()) );
    ASSERT_EQ( std::string("-an"),
               std::string(audioPlan.audioArguments.toUtf8().constData()) );
    ASSERT_EQ( std::string("ffmpeg-audio-source=video-only-contract ffmpeg-audio-args=-an ffmpeg-audio-video-only-ready=true ffmpeg-audio-source-discovery-owned=false ffmpeg-audio-extraction-owned=false ffmpeg-audio-input-owned=false ffmpeg-audio-mux-owned=false ffmpeg-audio-sync-owned=false ffmpeg-audio-contract-ready=true ffmpeg-audio-reason=none"),
               std::string(batchRenderedVideoFfmpegAudioPlanSummary(
                   audioPlan).toUtf8().constData()) );
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

    BatchRenderedVideoFfmpegCommandPlan invalidPlan =
        batchRenderedVideoFfmpegCommandPlanFromParts(
            BatchRenderedVideoFfmpegFramePlan(),
            plan.ffmpegFilterPlan,
            plan.ffmpegVideoPlan,
            plan.outputPlan);
    ASSERT_FALSE( invalidPlan.ready );
    ASSERT_EQ( std::string("rendered ffmpeg frame plan unavailable"),
               std::string(invalidPlan.reason.toUtf8().constData()) );
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
    ASSERT_TRUE( basePlan.ffmpegFilterReady );
    ASSERT_TRUE( basePlan.ffmpegAudioContractReady );
    ASSERT_TRUE( basePlan.ffmpegAudioPlan.contractReady );
    ASSERT_FALSE( basePlan.ffmpegAudioPlan.audioMuxOwned );
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
    ASSERT_TRUE( plan.ffmpegCommandReady );
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
    ASSERT_TRUE( summary.find("ffmpeg-audio-source=video-only-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-args=-an") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-mux-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-contract-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-settings-source=explicit-headless render-settings-explicit-headless=true render-settings-gui-owned=false render-settings-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("render-settings-resize=true render-settings-resize-width=1920") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-frame-size=1920x1284") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-frame-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-command-raw-input=-r 23.976 -y -f rawvideo -s 1920x1284 -pix_fmt rgb48 -i -") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-command-audio-owned=false ffmpeg-command-execution-owned=false ffmpeg-command-output-verification-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-command-ready=true") != std::string::npos );
    ASSERT_TRUE( summary.find("preflight-ready=true runnable=false") != std::string::npos );

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
    ASSERT_TRUE( implicitSettingsPlan.ffmpegCommandReady );
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
    ASSERT_FALSE( plan.ffmpegCommandReady );
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
    ASSERT_FALSE( plan.ffmpegCommandReady );
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
    ASSERT_TRUE( plan.ffmpegCommandReady );
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
    ASSERT_TRUE( plan.ffmpegAudioContractReady );
    ASSERT_FALSE( plan.ffmpegCommandReady );
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
    ASSERT_TRUE( summary.find("ffmpeg-audio-source=video-only-contract") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-source-discovery-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-extraction-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-audio-mux-owned=false") != std::string::npos );
    ASSERT_TRUE( summary.find("ffmpeg-command-ready=false") != std::string::npos );
    ASSERT_TRUE( summary.find("rendered-output=C:/renders/M16-1327.mp4 rendered-output-explicit-file=false rendered-output-input-clips=1 rendered-output-ready=true") != std::string::npos );
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
    ASSERT_FALSE( plan.ffmpegCommandReady );
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
    ASSERT_FALSE( plan.ffmpegCommandReady );
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
