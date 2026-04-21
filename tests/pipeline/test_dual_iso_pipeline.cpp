#include "../common/minitest.h"
#include "../common/frame_compare.h"
#include "../common/hash_helpers.h"
#include "../common/test_artifacts.h"

#include "mlv_pipeline_fixture.h"

#include "../../src/mlv/llrawproc/llrawproc.h"

#include <cmath>
#include <QString>

static void assert_fixture_ready(MlvPipelineFixture & fixture)
{
    QString error_message;
    ASSERT_TRUE(fixture.openTinyDualIso(&error_message));
    ASSERT_TRUE(fixture.loadReceipt(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"), &error_message));
    ASSERT_TRUE(fixture.applyReceipt(&error_message));
}

static bool has_processed_8bit_cache_slot(const mlvObject_t * video, uint64_t frameIndex, int threads)
{
    for (int slot = 0; slot < MLV_PROCESSED_8BIT_CACHE_SLOTS; ++slot) {
        if (video->processed_8bit_cache_active[slot]
            && video->processed_8bit_cache_frame[slot] == frameIndex
            && video->processed_8bit_cache_threads[slot] == threads) {
            return true;
        }
    }

    return false;
}

TEST(DualIsoPipeline, TinyDualIsoFullFramesMatchGolden)
{
    MlvPipelineFixture fixture;
    assert_fixture_ready(fixture);

    ASSERT_EQ(1, llrpGetDualIsoMode(fixture.video()));

    const std::vector<uint16_t> frame0 = fixture.renderFrame16(0, 1);
    ASSERT_EQ(1, fixture.video()->current_processed_frame_active);
    ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(fixture.video()->current_processed_frame));
    ASSERT_EQ(1, fixture.video()->current_processed_frame_threads);
    const std::vector<uint16_t> frame1 = fixture.renderFrame16(1, 1);
    ASSERT_EQ(1, fixture.video()->current_processed_frame_active);
    ASSERT_EQ(static_cast<unsigned long long>(1), static_cast<unsigned long long>(fixture.video()->current_processed_frame));
    ASSERT_EQ(1, fixture.video()->current_processed_frame_threads);

    test_artifacts::record("tiny_dual_iso.full16.frame0",
                           sha256_bytes(frame0.data(), frame0.size() * sizeof(uint16_t)));
    test_artifacts::record("tiny_dual_iso.full16.frame1",
                           sha256_bytes(frame1.data(), frame1.size() * sizeof(uint16_t)));
}

TEST(DualIsoPipeline, TinyDualIsoPreviewFramesMatchGoldenAndStayCloseToFull)
{
    MlvPipelineFixture full_fixture;
    assert_fixture_ready(full_fixture);
    const std::vector<uint16_t> full_frame0 = full_fixture.renderFrame16(0, 1);
    const std::vector<uint16_t> full_frame1 = full_fixture.renderFrame16(1, 1);

    MlvPipelineFixture preview_fixture;
    assert_fixture_ready(preview_fixture);
    preview_fixture.receipt().setDualIso(2);
    preview_fixture.receipt().setDualIsoInterpolation(1);
    preview_fixture.receipt().setDualIsoAliasMap(0);
    preview_fixture.receipt().setDualIsoFrBlending(0);

    QString error_message;
    ASSERT_TRUE(preview_fixture.applyReceipt(&error_message));
    ASSERT_EQ(2, llrpGetDualIsoMode(preview_fixture.video()));

    const std::vector<uint16_t> preview_frame0 = preview_fixture.renderFrame16(0, 1);
    const std::vector<uint16_t> preview_frame1 = preview_fixture.renderFrame16(1, 1);

    test_artifacts::record("tiny_dual_iso.preview16.frame0",
                           sha256_bytes(preview_frame0.data(), preview_frame0.size() * sizeof(uint16_t)));
    test_artifacts::record("tiny_dual_iso.preview16.frame1",
                           sha256_bytes(preview_frame1.data(), preview_frame1.size() * sizeof(uint16_t)));

    const frame_compare_result_t frame0_compare = compare_frames_u16(full_frame0.data(),
                                                                     preview_frame0.data(),
                                                                     preview_fixture.width(),
                                                                     preview_fixture.height(),
                                                                     3,
                                                                     2);
    const frame_compare_result_t frame1_compare = compare_frames_u16(full_frame1.data(),
                                                                     preview_frame1.data(),
                                                                     preview_fixture.width(),
                                                                     preview_fixture.height(),
                                                                     3,
                                                                     2);

    test_artifacts::record("tiny_dual_iso.preview16_vs_full_psnr.frame0",
                           QString::number(frame0_compare.psnr_db, 'f', 4).toStdString());
    test_artifacts::record("tiny_dual_iso.preview16_vs_full_psnr.frame1",
                           QString::number(frame1_compare.psnr_db, 'f', 4).toStdString());

    ASSERT_TRUE(frame0_compare.psnr_db >= 8.5);
    ASSERT_TRUE(frame1_compare.psnr_db >= 8.5);
}

TEST(DualIsoPipeline, HeadlessDualIsoPreviewAutoDetectsPatternAndKeepsItAcrossFrames)
{
    MlvPipelineFixture fixture;
    assert_fixture_ready(fixture);

    fixture.receipt().setDualIso(2);
    fixture.receipt().setDualIsoInterpolation(1);
    fixture.receipt().setDualIsoAliasMap(0);
    fixture.receipt().setDualIsoFrBlending(0);
    fixture.receipt().setDualIsoPattern(0);

    QString error_message;
    ASSERT_TRUE(fixture.applyReceipt(&error_message));
    fixture.video()->llrawproc->diso_pattern = 0;

    const std::vector<uint16_t> frame0 = fixture.renderFrame16(0, 1);
    ASSERT_TRUE(!frame0.empty());
    const int detected_pattern = fixture.video()->llrawproc->diso_pattern;
    ASSERT_TRUE(std::abs(detected_pattern) >= 1 && std::abs(detected_pattern) <= 4);

    const std::vector<uint16_t> frame1 = fixture.renderFrame16(1, 1);
    ASSERT_TRUE(!frame1.empty());
    ASSERT_EQ(std::abs(detected_pattern), std::abs(fixture.video()->llrawproc->diso_pattern));
}

TEST(DualIsoPipeline, HeadlessDualIsoPreviewReusesLeastSquaresScratchAcrossFrames)
{
    MlvPipelineFixture fixture;
    assert_fixture_ready(fixture);

    fixture.receipt().setDualIso(2);
    fixture.receipt().setDualIsoInterpolation(1);
    fixture.receipt().setDualIsoAliasMap(0);
    fixture.receipt().setDualIsoFrBlending(0);
    fixture.receipt().setDualIsoPattern(0);

    QString error_message;
    ASSERT_TRUE(fixture.applyReceipt(&error_message));

    ASSERT_EQ(static_cast<size_t>(0), fixture.video()->llrawproc->diso_preview_scratch.data_capacity);
    ASSERT_TRUE(fixture.video()->llrawproc->diso_preview_scratch.data_x == nullptr);
    ASSERT_TRUE(fixture.video()->llrawproc->diso_preview_scratch.data_y == nullptr);
    ASSERT_TRUE(fixture.video()->llrawproc->diso_preview_scratch.data_w == nullptr);

    const std::vector<uint16_t> frame0 = fixture.renderFrame16(0, 1);
    ASSERT_TRUE(!frame0.empty());
    const size_t first_capacity = fixture.video()->llrawproc->diso_preview_scratch.data_capacity;
    int * const first_data_x = fixture.video()->llrawproc->diso_preview_scratch.data_x;
    int * const first_data_y = fixture.video()->llrawproc->diso_preview_scratch.data_y;
    double * const first_data_w = fixture.video()->llrawproc->diso_preview_scratch.data_w;
    ASSERT_TRUE(first_capacity > 0);
    ASSERT_TRUE(first_data_x != nullptr);
    ASSERT_TRUE(first_data_y != nullptr);
    ASSERT_TRUE(first_data_w != nullptr);

    const std::vector<uint16_t> frame1 = fixture.renderFrame16(1, 1);
    ASSERT_TRUE(!frame1.empty());
    ASSERT_EQ(first_capacity, fixture.video()->llrawproc->diso_preview_scratch.data_capacity);
    ASSERT_TRUE(first_data_x == fixture.video()->llrawproc->diso_preview_scratch.data_x);
    ASSERT_TRUE(first_data_y == fixture.video()->llrawproc->diso_preview_scratch.data_y);
    ASSERT_TRUE(first_data_w == fixture.video()->llrawproc->diso_preview_scratch.data_w);
}

TEST(DualIsoPipeline, HeadlessDualIsoFull20BitReusesOuterScratchAcrossFrames)
{
    MlvPipelineFixture fixture;
    assert_fixture_ready(fixture);

    fixture.receipt().setDualIso(1);
    fixture.receipt().setDualIsoInterpolation(1);
    fixture.receipt().setDualIsoAliasMap(1);
    fixture.receipt().setDualIsoFrBlending(1);
    fixture.receipt().setChromaSmooth(2);

    QString error_message;
    ASSERT_TRUE(fixture.applyReceipt(&error_message));

    dualiso_full20bit_scratch_t * const scratch = &fixture.video()->llrawproc->diso_full20bit_scratch;
    ASSERT_EQ(static_cast<size_t>(0), scratch->pixel_capacity);
    ASSERT_TRUE(scratch->raw_buffer_32 == nullptr);
    ASSERT_TRUE(scratch->dark == nullptr);
    ASSERT_TRUE(scratch->bright == nullptr);

    const std::vector<uint16_t> frame0 = fixture.renderFrame16(0, 1);
    ASSERT_TRUE(!frame0.empty());
    const size_t first_capacity = scratch->pixel_capacity;
    uint32_t * const first_raw_buffer = scratch->raw_buffer_32;
    uint32_t * const first_dark = scratch->dark;
    uint32_t * const first_bright = scratch->bright;
    uint32_t * const first_fullres = scratch->fullres;
    uint32_t * const first_halfres = scratch->halfres;
    uint32_t * const first_fullres_smooth = scratch->fullres_smooth;
    uint32_t * const first_halfres_smooth = scratch->halfres_smooth;
    uint16_t * const first_overexposed = scratch->overexposed;
    uint16_t * const first_alias_map = scratch->alias_map;
    uint16_t * const first_over_aux = scratch->over_aux;

    ASSERT_TRUE(first_capacity >= static_cast<size_t>(fixture.width()) * static_cast<size_t>(fixture.height()));
    ASSERT_TRUE(first_raw_buffer != nullptr);
    ASSERT_TRUE(first_dark != nullptr);
    ASSERT_TRUE(first_bright != nullptr);
    ASSERT_TRUE(first_fullres != nullptr);
    ASSERT_TRUE(first_halfres != nullptr);
    ASSERT_TRUE(first_fullres_smooth != nullptr);
    ASSERT_TRUE(first_halfres_smooth != nullptr);
    ASSERT_TRUE(first_overexposed != nullptr);
    ASSERT_TRUE(first_alias_map != nullptr);
    ASSERT_TRUE(first_over_aux != nullptr);

    const std::vector<uint16_t> frame1 = fixture.renderFrame16(1, 1);
    ASSERT_TRUE(!frame1.empty());
    ASSERT_EQ(first_capacity, scratch->pixel_capacity);
    ASSERT_TRUE(first_raw_buffer == scratch->raw_buffer_32);
    ASSERT_TRUE(first_dark == scratch->dark);
    ASSERT_TRUE(first_bright == scratch->bright);
    ASSERT_TRUE(first_fullres == scratch->fullres);
    ASSERT_TRUE(first_halfres == scratch->halfres);
    ASSERT_TRUE(first_fullres_smooth == scratch->fullres_smooth);
    ASSERT_TRUE(first_halfres_smooth == scratch->halfres_smooth);
    ASSERT_TRUE(first_overexposed == scratch->overexposed);
    ASSERT_TRUE(first_alias_map == scratch->alias_map);
    ASSERT_TRUE(first_over_aux == scratch->over_aux);
}

TEST(DualIsoPipeline, HeadlessDualIsoSolvedAutoMatchStateStaysStableAcrossFrames)
{
    MlvPipelineFixture fixture;
    assert_fixture_ready(fixture);

    fixture.video()->llrawproc->diso_pattern = 0;
    fixture.video()->llrawproc->diso_auto_correction = -2;
    fixture.video()->llrawproc->diso_ev_correction = 1;
    fixture.video()->llrawproc->diso_black_delta = -1;

    const std::vector<uint16_t> frame0 = fixture.renderFrame16(0, 1);
    ASSERT_TRUE(!frame0.empty());
    const double solved_ev = fixture.video()->llrawproc->diso_ev_correction;
    const int solved_black_delta = fixture.video()->llrawproc->diso_black_delta;
    ASSERT_TRUE(solved_ev != 1);
    ASSERT_TRUE(solved_black_delta != -1);

    const std::vector<uint16_t> frame1 = fixture.renderFrame16(1, 1);
    ASSERT_TRUE(!frame1.empty());
    ASSERT_TRUE(std::fabs(fixture.video()->llrawproc->diso_ev_correction - solved_ev) < 1e-9);
    ASSERT_EQ(solved_black_delta, fixture.video()->llrawproc->diso_black_delta);
}

TEST(DualIsoPipeline, ProcessedFrameCacheInvalidatesWhenProcessingChangesWithoutManualReset)
{
    MlvPipelineFixture fixture;
    assert_fixture_ready(fixture);

    const std::vector<uint16_t> baseline_frame = fixture.renderFrame16(0, 1);
    ASSERT_EQ(1, fixture.video()->current_processed_frame_active);
    const uint64_t baseline_signature = fixture.video()->current_processed_frame_signature;

    processingSetExposureStops(fixture.processing(), 1.0);

    const std::vector<uint16_t> adjusted_frame = fixture.renderFrame16(0, 1);
    ASSERT_EQ(1, fixture.video()->current_processed_frame_active);
    ASSERT_TRUE(fixture.video()->current_processed_frame_signature != baseline_signature);
    ASSERT_TRUE(baseline_frame != adjusted_frame);

    const std::vector<uint16_t> adjusted_frame_repeat = fixture.renderFrame16(0, 1);
    ASSERT_TRUE(adjusted_frame == adjusted_frame_repeat);
}

TEST(DualIsoPipeline, ProcessedFrame16CacheReusesSolvedDualIsoFrameWithoutManualReset)
{
    MlvPipelineFixture fixture;
    assert_fixture_ready(fixture);

    fixture.video()->llrawproc->diso_pattern = 0;
    fixture.video()->llrawproc->diso_auto_correction = -2;
    fixture.video()->llrawproc->diso_ev_correction = 1;
    fixture.video()->llrawproc->diso_black_delta = -1;

    const std::vector<uint16_t> first_frame = fixture.renderFrame16(0, 1);
    ASSERT_TRUE(!first_frame.empty());
    ASSERT_EQ(1, fixture.video()->current_processed_frame_active);
    ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(fixture.video()->current_processed_frame));
    ASSERT_EQ(1, fixture.video()->current_processed_frame_threads);

    const uint64_t solved_signature = fixture.video()->current_processed_frame_signature;
    const double solved_ev = fixture.video()->llrawproc->diso_ev_correction;
    const int solved_black_delta = fixture.video()->llrawproc->diso_black_delta;
    const int solved_pattern = fixture.video()->llrawproc->diso_pattern;

    ASSERT_TRUE(std::abs(solved_pattern) >= 1 && std::abs(solved_pattern) <= 4);
    ASSERT_TRUE(solved_ev != 1);
    ASSERT_TRUE(solved_black_delta != -1);

    const std::vector<uint16_t> repeated_frame = fixture.renderFrame16(0, 1);
    ASSERT_TRUE(first_frame == repeated_frame);
    ASSERT_EQ(static_cast<unsigned long long>(solved_signature),
              static_cast<unsigned long long>(fixture.video()->current_processed_frame_signature));
    ASSERT_TRUE(std::fabs(fixture.video()->llrawproc->diso_ev_correction - solved_ev) < 1e-9);
    ASSERT_EQ(solved_black_delta, fixture.video()->llrawproc->diso_black_delta);
    ASSERT_EQ(solved_pattern, fixture.video()->llrawproc->diso_pattern);
}

TEST(DualIsoPipeline, ProcessedFrame8CacheReusesExactFrameAndInvalidatesWithSignatureChanges)
{
    MlvPipelineFixture fixture;
    assert_fixture_ready(fixture);

    const std::vector<uint8_t> baseline_frame = fixture.renderFrame8(0, 1);
    ASSERT_EQ(1, fixture.video()->current_processed_frame_8bit_active);
    ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(fixture.video()->current_processed_frame_8bit));
    ASSERT_EQ(1, fixture.video()->current_processed_frame_8bit_threads);
    const uint64_t baseline_signature = fixture.video()->current_processed_frame_8bit_signature;

    const std::vector<uint8_t> cached_repeat = fixture.renderFrame8(0, 1);
    ASSERT_TRUE(baseline_frame == cached_repeat);
    ASSERT_EQ(static_cast<unsigned long long>(baseline_signature),
              static_cast<unsigned long long>(fixture.video()->current_processed_frame_8bit_signature));

    processingSetExposureStops(fixture.processing(), 0.5);

    const std::vector<uint8_t> adjusted_frame = fixture.renderFrame8(0, 1);
    ASSERT_EQ(1, fixture.video()->current_processed_frame_8bit_active);
    ASSERT_TRUE(fixture.video()->current_processed_frame_8bit_signature != baseline_signature);
    ASSERT_TRUE(baseline_frame != adjusted_frame);
}

TEST(DualIsoPipeline, ProcessedFrame8CacheKeepsNearbyFramesWarm)
{
    MlvPipelineFixture fixture;
    QString error_message;
    ASSERT_TRUE(fixture.openTinyDualIso(&error_message));
    ASSERT_TRUE(fixture.loadReceipt(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"), &error_message));
    fixture.receipt().setDualIso(0);
    ASSERT_TRUE(fixture.applyReceipt(&error_message));
    ASSERT_EQ(0, llrpGetDualIsoMode(fixture.video()));

    const std::vector<uint8_t> frame0 = fixture.renderFrame8(0, 1);
    const uint64_t frame0_signature = fixture.video()->current_processed_frame_8bit_signature;
    ASSERT_TRUE(has_processed_8bit_cache_slot(fixture.video(), 0, 1));

    const std::vector<uint8_t> frame1 = fixture.renderFrame8(1, 1);
    const uint64_t frame1_signature = fixture.video()->current_processed_frame_8bit_signature;
    ASSERT_TRUE(has_processed_8bit_cache_slot(fixture.video(), 0, 1));
    ASSERT_TRUE(has_processed_8bit_cache_slot(fixture.video(), 1, 1));
    ASSERT_TRUE(frame0_signature != frame1_signature);

    const std::vector<uint8_t> frame0_repeat = fixture.renderFrame8(0, 1);
    ASSERT_TRUE(frame0 == frame0_repeat);
    ASSERT_TRUE(frame1 != frame0_repeat);
    ASSERT_EQ(static_cast<unsigned long long>(0), static_cast<unsigned long long>(fixture.video()->current_processed_frame_8bit));
    ASSERT_EQ(static_cast<unsigned long long>(frame0_signature),
              static_cast<unsigned long long>(fixture.video()->current_processed_frame_8bit_signature));
}

TEST(DualIsoPipeline, ChromaSmoothScratchReusesFrameBufferAcrossFrames)
{
    MlvPipelineFixture fixture;
    QString error_message;
    ASSERT_TRUE(fixture.openTinyDualIso(&error_message));
    ASSERT_TRUE(fixture.loadReceipt(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"), &error_message));
    fixture.receipt().setDualIso(0);
    fixture.receipt().setChromaSmooth(2);
    ASSERT_TRUE(fixture.applyReceipt(&error_message));

    ASSERT_TRUE(fixture.video()->llrawproc->chroma_smooth_scratch.buffer == nullptr);
    ASSERT_EQ(static_cast<size_t>(0), fixture.video()->llrawproc->chroma_smooth_scratch.capacity);

    const std::vector<uint16_t> frame0 = fixture.renderFrame16(0, 1);
    ASSERT_TRUE(!frame0.empty());

    uint16_t * const first_buffer = fixture.video()->llrawproc->chroma_smooth_scratch.buffer;
    const size_t first_capacity = fixture.video()->llrawproc->chroma_smooth_scratch.capacity;
    ASSERT_TRUE(first_buffer != nullptr);
    ASSERT_TRUE(first_capacity >= static_cast<size_t>(fixture.width()) * static_cast<size_t>(fixture.height()));

    const std::vector<uint16_t> frame1 = fixture.renderFrame16(1, 1);
    ASSERT_TRUE(!frame1.empty());
    ASSERT_TRUE(first_buffer == fixture.video()->llrawproc->chroma_smooth_scratch.buffer);
    ASSERT_EQ(first_capacity, fixture.video()->llrawproc->chroma_smooth_scratch.capacity);
}
