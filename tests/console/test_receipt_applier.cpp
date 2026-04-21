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
