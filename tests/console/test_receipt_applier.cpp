#include "../common/minitest.h"
#include "../common/hash_helpers.h"
#include "../common/repo_paths.h"
#include "../common/test_artifacts.h"

#include "../../platform/qt/ReceiptSettings.h"
#include "../../src/batch/ReceiptApplier.h"
#include "../../src/batch/ReceiptLoader.h"
#include "../../src/batch/BatchLogger.h"

#include <QDir>
#include <QFile>
#include <QTemporaryDir>

#include <cstdlib>
#include <memory>

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
