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
