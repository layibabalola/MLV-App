#include "../common/minitest.h"
#include "../common/hash_helpers.h"
#include "../common/test_artifacts.h"

#include "mlv_pipeline_fixture.h"

#include "../../platform/qt/GpuPreviewProcessing.h"
#include "../../src/processing/raw_processing.h"

#include <cmath>
#include <cstring>
#include <functional>
#include <string>
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

static void assert_gpu_preview_rejects_processing_feature(
    const char * label,
    const QString & expected_reason,
    const std::function<void(processingObject_t *)> & enable_unsupported_feature)
{
    MlvPipelineFixture fixture;
    assert_gpu_preview_fixture_ready(fixture);
    (void)assert_gpu_preview_subset_supported(fixture);

    processingObject_t * processing = fixture.processing();
    ASSERT_TRUE(processing != nullptr);
    enable_unsupported_feature(processing);

    QString reason;
    if (gpuPreviewProcessingIsSupported(processing, &reason))
    {
        ::minitest::fail(__FILE__, __LINE__,
                         std::string("gpuPreviewProcessingIsSupported rejects ") + label);
    }
    ASSERT_EQ(expected_reason.toStdString(), reason.toStdString());

    const GpuPreviewProcessingConfig rejected_config =
        gpuPreviewProcessingBuildConfig(processing, &reason);
    if (rejected_config.enabled)
    {
        ::minitest::fail(__FILE__, __LINE__,
                         std::string("gpuPreviewProcessingBuildConfig disables ") + label);
    }
    ASSERT_EQ(expected_reason.toStdString(), reason.toStdString());

    test_artifacts::record(std::string("gpu_preview_subset.unsupported.") + label,
                           reason.toStdString());
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

TEST(GpuPreviewProcessing, UnsupportedProcessingFeaturesBlockGpuPreviewSubset)
{
    assert_gpu_preview_rejects_processing_feature(
        "highlight_reconstruction",
        QStringLiteral("highlight reconstruction enabled"),
        [](processingObject_t * processing) { processing->highlight_reconstruction = 1; });
    /* allow_creative_adjustments is no longer rejected on its own: the GPU subset
     * shader now ports every creative-family stage -- the in-loop simple-contrast
     * factor, hue-vs/luma-vs curves, vibrance, saturation, toning, the contrast
     * curve and the gradation curves. Clips are still failed closed on the
     * non-creative features below, which are gated independently of the creative
     * flag. */
    assert_gpu_preview_rejects_processing_feature(
        "gradient",
        QStringLiteral("gradient enabled"),
        [](processingObject_t * processing) { processing->gradient_enable = 1; });
    assert_gpu_preview_rejects_processing_feature(
        "lut",
        QStringLiteral("LUT enabled"),
        [](processingObject_t * processing) { processing->lut_on = 1; });
    assert_gpu_preview_rejects_processing_feature(
        "filter",
        QStringLiteral("filter enabled"),
        [](processingObject_t * processing) { processing->filter_on = 1; });
    assert_gpu_preview_rejects_processing_feature(
        "agx",
        QStringLiteral("AgX enabled"),
        [](processingObject_t * processing) { processing->AgX = 1; });
    assert_gpu_preview_rejects_processing_feature(
        "median_denoiser",
        QStringLiteral("median denoiser enabled"),
        [](processingObject_t * processing) { processing->denoiserStrength = 1; });
    assert_gpu_preview_rejects_processing_feature(
        "rbf_denoiser",
        QStringLiteral("RBF denoiser enabled"),
        [](processingObject_t * processing) { processing->rbfDenoiserLuma = 25; });
    assert_gpu_preview_rejects_processing_feature(
        "grain",
        QStringLiteral("grain enabled"),
        [](processingObject_t * processing) { processing->grainStrength = 25; });
    assert_gpu_preview_rejects_processing_feature(
        "ca_correction",
        QStringLiteral("CA correction enabled"),
        [](processingObject_t * processing) { processing->ca_desaturate = 1; });
    assert_gpu_preview_rejects_processing_feature(
        "sharpening",
        QStringLiteral("sharpening enabled"),
        [](processingObject_t * processing) { processing->sharpen = 0.25; });
    assert_gpu_preview_rejects_processing_feature(
        "chroma_separation",
        QStringLiteral("chroma separation enabled"),
        [](processingObject_t * processing) { processing->cs_zone.use_cs = 1; });
    assert_gpu_preview_rejects_processing_feature(
        "chroma_blur",
        QStringLiteral("chroma blur enabled"),
        [](processingObject_t * processing) { processing->cs_zone.chroma_blur_radius = 3; });
    assert_gpu_preview_rejects_processing_feature(
        "clarity",
        QStringLiteral("clarity enabled"),
        [](processingObject_t * processing) { processing->clarity = 0.25; });
    assert_gpu_preview_rejects_processing_feature(
        "shadows_highlights",
        QStringLiteral("shadows/highlights enabled"),
        [](processingObject_t * processing) { processing->shadows_highlights.shadows = 0.25; });
    assert_gpu_preview_rejects_processing_feature(
        "vignette",
        QStringLiteral("vignette enabled"),
        [](processingObject_t * processing) { processing->vignette_strength = 1; });
    assert_gpu_preview_rejects_processing_feature(
        "unsupported_gamut",
        QStringLiteral("unsupported gamut"),
        [](processingObject_t * processing) { processingSetGamut(processing, GAMUT_Rec2020); });
}

TEST(GpuPreviewProcessing, NeutralCreativeAdjustmentsAreSupportedAndApplyCurves)
{
    MlvPipelineFixture fixture;
    assert_gpu_preview_fixture_ready(fixture);

    /* Baseline: the supported subset with creative adjustments OFF. */
    const GpuPreviewProcessingConfig base_config = assert_gpu_preview_subset_supported(fixture);
    ASSERT_TRUE(!base_config.applyCreativeCurves);
    const std::string base_hash = render_subset_hash(fixture, base_config, 0);

    /* Turn the creative flag ON with every UNPORTED creative stage left neutral
     * (vibrance/saturation/toning/hue-vs at their defaults). The gate must now
     * accept it, because the contrast + gradation curves are ported to the GPU
     * subset shader. */
    processingAllowCreativeAdjustments(fixture.processing());

    QString reason;
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig creative_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(creative_config.enabled);
    ASSERT_TRUE(creative_config.applyCreativeCurves);
    ASSERT_EQ(static_cast<int>(65536u * sizeof(uint16_t)), creative_config.contrastCurveLut.size());
    ASSERT_EQ(static_cast<int>(65536u * sizeof(uint16_t)), creative_config.gradationLutY.size());
    ASSERT_NE(base_config.signature, creative_config.signature);

    /* The default creative contrast curve (pre_calc_curve_r) is non-identity, so
     * the ported CPU-reference output must differ from the creative-off baseline,
     * proving the curve chain is actually applied. */
    const std::string creative_hash = render_subset_hash(fixture, creative_config, 0);
    ASSERT_TRUE(base_hash != creative_hash);

    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.creative.frame0", creative_hash);
    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.creative.signature.frame0",
                           std::to_string(creative_config.signature));
}

TEST(GpuPreviewProcessing, NonNeutralToningIsSupportedAndChangesOutput)
{
    MlvPipelineFixture fixture;
    assert_gpu_preview_fixture_ready(fixture);
    (void)assert_gpu_preview_subset_supported(fixture);

    /* Creative on, every stage neutral: toning must be inert. */
    processingAllowCreativeAdjustments(fixture.processing());
    QString reason;
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig neutral_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(neutral_config.enabled);
    ASSERT_TRUE(!neutral_config.applyToning);
    const std::string neutral_hash = render_subset_hash(fixture, neutral_config, 0);

    /* Non-neutral toning is now ported to the GPU subset, so the gate must accept
     * it (not force CPU) and the per-channel gain must change the output. */
    processingSetToning(fixture.processing(), 255, 192, 0, 40);
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig toned_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(toned_config.enabled);
    ASSERT_TRUE(toned_config.applyToning);
    ASSERT_NE(neutral_config.signature, toned_config.signature);

    const std::string toned_hash = render_subset_hash(fixture, toned_config, 0);
    ASSERT_TRUE(neutral_hash != toned_hash);

    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.toning.frame0", toned_hash);
}

TEST(GpuPreviewProcessing, NonNeutralSaturationIsSupportedAndChangesOutput)
{
    MlvPipelineFixture fixture;
    assert_gpu_preview_fixture_ready(fixture);
    (void)assert_gpu_preview_subset_supported(fixture);

    processingAllowCreativeAdjustments(fixture.processing());
    QString reason;
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig neutral_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(neutral_config.enabled);
    ASSERT_TRUE(!neutral_config.applySaturation);
    const std::string neutral_hash = render_subset_hash(fixture, neutral_config, 0);

    /* Saturation is now ported (direct Y1 + (pix-Y1)*sat), so the gate accepts it
     * and the chroma scale must change the output. */
    processingSetSaturation(fixture.processing(), 1.5);
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig sat_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(sat_config.enabled);
    ASSERT_TRUE(sat_config.applySaturation);
    ASSERT_NEAR(1.5, sat_config.saturation, 0.0001);
    ASSERT_NE(neutral_config.signature, sat_config.signature);

    const std::string sat_hash = render_subset_hash(fixture, sat_config, 0);
    ASSERT_TRUE(neutral_hash != sat_hash);

    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.saturation.frame0", sat_hash);
}

TEST(GpuPreviewProcessing, NonNeutralVibranceIsSupportedAndChangesOutput)
{
    MlvPipelineFixture fixture;
    assert_gpu_preview_fixture_ready(fixture);
    (void)assert_gpu_preview_subset_supported(fixture);

    processingAllowCreativeAdjustments(fixture.processing());
    QString reason;
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig neutral_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(neutral_config.enabled);
    ASSERT_TRUE(!neutral_config.applyVibrance);
    const std::string neutral_hash = render_subset_hash(fixture, neutral_config, 0);

    /* Positive vibrance is now ported (saturation-weighted blend), so the gate
     * accepts it and the output must change. */
    processingSetVibrance(fixture.processing(), 1.5);
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig vib_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(vib_config.enabled);
    ASSERT_TRUE(vib_config.applyVibrance);
    ASSERT_NEAR(1.5, vib_config.vibrance, 0.0001);
    ASSERT_NE(neutral_config.signature, vib_config.signature);

    const std::string vib_hash = render_subset_hash(fixture, vib_config, 0);
    ASSERT_TRUE(neutral_hash != vib_hash);

    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.vibrance.frame0", vib_hash);
}

TEST(GpuPreviewProcessing, NonNeutralHueVsIsSupportedAndChangesOutput)
{
    MlvPipelineFixture fixture;
    assert_gpu_preview_fixture_ready(fixture);
    (void)assert_gpu_preview_subset_supported(fixture);

    processingAllowCreativeAdjustments(fixture.processing());
    QString reason;
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig neutral_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(neutral_config.enabled);
    ASSERT_TRUE(!neutral_config.applyHueVs);
    const std::string neutral_hash = render_subset_hash(fixture, neutral_config, 0);

    /* hue-vs / luma-vs curves are now ported (RGB->HSV, four curve adjustments,
     * HSV->RGB). A constant +0.5 hue_vs_hue curve rotates every chroma pixel's
     * hue by 60*0.5 = 30 degrees, so the gate must accept it (not force the CPU
     * path) and the output must change. */
    for (int i = 0; i < 36000; ++i) fixture.processing()->hue_vs_hue[i] = 0.5f;
    fixture.processing()->hue_vs_hue_used = 1;
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig huevs_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(huevs_config.enabled);
    ASSERT_TRUE(huevs_config.applyHueVs);
    ASSERT_NE(neutral_config.signature, huevs_config.signature);

    const std::string huevs_hash = render_subset_hash(fixture, huevs_config, 0);
    ASSERT_TRUE(neutral_hash != huevs_hash);

    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.hue_vs.frame0", huevs_hash);
}

TEST(GpuPreviewProcessing, NonNeutralInLoopContrastIsSupportedAndChangesOutput)
{
    MlvPipelineFixture fixture;
    assert_gpu_preview_fixture_ready(fixture);
    (void)assert_gpu_preview_subset_supported(fixture);

    processingAllowCreativeAdjustments(fixture.processing());
    QString reason;
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig neutral_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(neutral_config.enabled);
    ASSERT_TRUE(!neutral_config.applyInLoopContrast);
    const std::string neutral_hash = render_subset_hash(fixture, neutral_config, 0);

    /* The in-loop simple-contrast factor is now ported (per-pixel luma-dependent
     * exposure multiply by contrast_curve[cval]), so the gate must accept a
     * non-zero contrast (previously the last creative reject) and the output must
     * change. processingSetSimpleContrast sets contrast = value*0.65 and rebuilds
     * the curve. */
    processingSetSimpleContrast(fixture.processing(), 1.0);
    ASSERT_TRUE(gpuPreviewProcessingIsSupported(fixture.processing(), &reason));
    const GpuPreviewProcessingConfig contrast_config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(contrast_config.enabled);
    ASSERT_TRUE(contrast_config.applyInLoopContrast);
    ASSERT_NE(neutral_config.signature, contrast_config.signature);

    const std::string contrast_hash = render_subset_hash(fixture, contrast_config, 0);
    ASSERT_TRUE(neutral_hash != contrast_hash);

    test_artifacts::record("tiny_dual_iso.gpu_preview_subset.in_loop_contrast.frame0", contrast_hash);
}
