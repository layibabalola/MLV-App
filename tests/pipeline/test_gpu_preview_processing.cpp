#include "../common/minitest.h"
#include "../common/hash_helpers.h"
#include "../common/test_artifacts.h"

#include "mlv_pipeline_fixture.h"

#include "../../platform/qt/GpuPreviewProcessing.h"
#include "../../src/processing/raw_processing.h"

#include <cmath>
#include <cstring>
#include <vector>

static void assert_gpu_preview_fixture_ready(MlvPipelineFixture & fixture)
{
    QString error_message;
    ASSERT_TRUE(fixture.openTinyDualIso(&error_message));
    ASSERT_TRUE(fixture.loadReceipt(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"), &error_message));
    ASSERT_TRUE(fixture.applyReceipt(&error_message));
}

static void configure_gpu_preview_supported_subset(MlvPipelineFixture & fixture)
{
    processingObject_t * processing = fixture.processing();
    ASSERT_TRUE(processing != nullptr);

    processing->AgX = 0;
    processingDontAllowCreativeAdjustments(processing);
    processing->highlight_reconstruction = 0;
    processing->gradient_enable = 0;
    processing->lut_on = 0;
    processing->filter_on = 0;
    processing->exr_mode = 0;
    processing->denoiserStrength = 0;
    processing->rbfDenoiserLuma = 0;
    processing->rbfDenoiserChroma = 0;
    processing->grainStrength = 0;
    processing->ca_desaturate = 0;
    processing->sharpen = 0.0;
    processing->cs_zone.use_cs = 0;
    processing->cs_zone.chroma_blur_radius = 0;
    processing->clarity = 0.0;
    processing->shadows_highlights.shadows = 0.0;
    processing->shadows_highlights.highlights = 0.0;
    processing->vignette_strength = 0;
    processingSetGamut(processing, GAMUT_Rec709);
}

static GpuPreviewProcessingConfig assert_gpu_preview_subset_supported(MlvPipelineFixture & fixture)
{
    configure_gpu_preview_supported_subset(fixture);

    QString reason;
    if( !gpuPreviewProcessingIsSupported(fixture.processing(), &reason) )
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "gpuPreviewProcessingIsSupported(fixture.processing(), &reason)",
                         reason.toStdString());
    }

    const GpuPreviewProcessingConfig config = gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(config.enabled);
    ASSERT_TRUE(config.signature != 0);
    ASSERT_EQ(static_cast<int>(65536u * sizeof(uint16_t)), config.levelsLut.size());
    ASSERT_EQ(static_cast<int>(65536u * sizeof(uint16_t)), config.gammaLut.size());
    return config;
}

static std::vector<uint16_t> render_gpu_preview_subset_cpu_reference(MlvPipelineFixture & fixture,
                                                                     const GpuPreviewProcessingConfig & config,
                                                                     uint64_t frame_index)
{
    const std::vector<uint16_t> debayered = fixture.renderDebayeredFrame16(frame_index);
    ASSERT_TRUE(!debayered.empty());
    std::vector<uint16_t> output(debayered.size(), 0);
    gpuPreviewProcessingApplyCpuReference(config,
                                          debayered.data(),
                                          output.data(),
                                          fixture.width() * fixture.height());
    return output;
}

static std::string render_subset_hash(MlvPipelineFixture & fixture,
                                      const GpuPreviewProcessingConfig & config,
                                      uint64_t frame_index)
{
    const std::vector<uint16_t> subset_output =
        render_gpu_preview_subset_cpu_reference(fixture, config, frame_index);
    return sha256_bytes(subset_output.data(), subset_output.size() * sizeof(uint16_t));
}

TEST(GpuPreviewProcessing, TinyDualIsoReceiptSubsetGoldenOutputIsStable)
{
    MlvPipelineFixture frame0_fixture;
    assert_gpu_preview_fixture_ready(frame0_fixture);
    const GpuPreviewProcessingConfig frame0_config = assert_gpu_preview_subset_supported(frame0_fixture);
    ASSERT_NEAR(0.0, frame0_config.sourceExposureStops, 0.0001);
    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.frame0",
                           render_subset_hash(frame0_fixture, frame0_config, 0));
    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.signature.frame0",
                           std::to_string(frame0_config.signature));

    MlvPipelineFixture frame1_fixture;
    assert_gpu_preview_fixture_ready(frame1_fixture);
    const GpuPreviewProcessingConfig frame1_config = assert_gpu_preview_subset_supported(frame1_fixture);
    ASSERT_NEAR(0.0, frame1_config.sourceExposureStops, 0.0001);
    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.frame1",
                           render_subset_hash(frame1_fixture, frame1_config, 1));
    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.signature.frame1",
                           std::to_string(frame1_config.signature));
}

TEST(GpuPreviewProcessing, ExposureStopsChangesSubsetConfigAndStableOutput)
{
    MlvPipelineFixture fixture;
    assert_gpu_preview_fixture_ready(fixture);
    const GpuPreviewProcessingConfig base_config = assert_gpu_preview_subset_supported(fixture);
    const std::string base_hash = render_subset_hash(fixture, base_config, 0);

    processingSetExposureStops(fixture.processing(), 0.75);

    QString reason;
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig exposed_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(exposed_config.enabled);
    ASSERT_NEAR(0.75, exposed_config.sourceExposureStops, 0.0001);
    ASSERT_NE(base_config.signature, exposed_config.signature);
    ASSERT_TRUE(base_config.gammaLut != exposed_config.gammaLut);

    /* Directional LUT check: positive exposure must brighten mid-gray.
     * The supported subset preserves positive exposure through the copied
     * pre_calc_gamma LUT (see GpuPreviewProcessing.cpp mechanism comment
     * and src/processing/raw_processing.c::processingSetGamma). A byte-level
     * inequality alone does not prove direction - this asserts the sign. */
    ASSERT_EQ(static_cast<int>(65536u * sizeof(uint16_t)), base_config.gammaLut.size());
    ASSERT_EQ(static_cast<int>(65536u * sizeof(uint16_t)), exposed_config.gammaLut.size());
    const uint16_t * base_gamma =
        reinterpret_cast<const uint16_t *>(base_config.gammaLut.constData());
    const uint16_t * exposed_gamma =
        reinterpret_cast<const uint16_t *>(exposed_config.gammaLut.constData());
    ASSERT_TRUE(exposed_gamma[32768] > base_gamma[32768]);

    const std::string exposed_hash = render_subset_hash(fixture, exposed_config, 0);
    ASSERT_TRUE(base_hash != exposed_hash);

    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.exposure_0_75.frame0",
                           exposed_hash);
    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.exposure_0_75.signature.frame0",
                           std::to_string(exposed_config.signature));
}
