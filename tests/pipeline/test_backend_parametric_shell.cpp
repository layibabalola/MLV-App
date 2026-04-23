/*
 * test_backend_parametric_shell.cpp
 *
 * Backend-parametric shell for the GPU phase.
 *
 * Slice 1 proved the CPU oracle and the harness shape. Slice 2a flips the GPU
 * backend from a fixed "not implemented" skip into a real offscreen OpenGL
 * execution path. These tests now accept two valid outcomes for Backend::Gpu:
 *   1. real execution with CPU-vs-GPU tolerance parity and a GPU golden hash
 *   2. a concrete runtime skip reason on unsupported / software-only stacks
 *
 * The golden schema stays flat and backend-qualified:
 *   tiny_dual_iso.preview_processing.{cpu|gpu}.frame0
 */

#include "../common/minitest.h"
#include "../common/frame_compare.h"
#include "../common/hash_helpers.h"
#include "../common/test_artifacts.h"

#include "backend_parametric_fixture.h"

#include "../../platform/qt/GpuPreviewProcessing.h"

#include <QString>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace {

void configure_gpu_preview_supported_subset(BackendParametricFixture & fixture)
{
    processingObject_t * processing = fixture.processing();
    ASSERT_TRUE(processing != nullptr);

    /*
     * Mirrors the configuration block used by test_gpu_preview_processing.cpp
     * so both tests anchor on the same "known-supported" receipt subset.
     * Keeping these in sync is a maintenance burden - once the backend
     * fixture fully subsumes the older test, the block will migrate here.
     */
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

GpuPreviewProcessingConfig build_supported_config(BackendParametricFixture & fixture)
{
    QString error_message;
    ASSERT_TRUE(fixture.openTinyDualIso(&error_message));
    ASSERT_TRUE(fixture.loadReceipt(
        QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"),
        &error_message));
    ASSERT_TRUE(fixture.applyReceipt(&error_message));

    configure_gpu_preview_supported_subset(fixture);

    QString reason;
    if (!gpuPreviewProcessingIsSupported(fixture.processing(), &reason))
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "gpuPreviewProcessingIsSupported",
                         reason.toStdString());
    }

    const GpuPreviewProcessingConfig config =
        gpuPreviewProcessingBuildConfig(fixture.processing(), &reason);
    ASSERT_TRUE(config.enabled);
    ASSERT_TRUE(config.signature != 0);
    return config;
}

std::string format_artifact_key(const char * backend_name, uint64_t frame_index)
{
    std::string key = "tiny_dual_iso.preview_processing.";
    key += backend_name;
    key += ".frame";
    key += std::to_string(frame_index);
    return key;
}

void assert_known_gpu_skip_reason(const QString & reason)
{
    ASSERT_TRUE(!reason.isEmpty());
    ASSERT_TRUE(reason.contains(QStringLiteral("QOffscreenSurface"))
             || reason.contains(QStringLiteral("QOpenGLContext"))
             || reason.contains(QStringLiteral("software rasterizer"))
             || reason.contains(QStringLiteral("QOpenGLShaderProgram")));
}

} // namespace

TEST(BackendParametricShell, CpuBackendIsAlwaysAvailable)
{
    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeBackend(BackendParametricFixture::Backend::Cpu);
    ASSERT_TRUE(availability.available);
    ASSERT_TRUE(availability.reason.isEmpty());
}

TEST(BackendParametricShell, GpuBackendProbeReportsAvailabilityOrKnownSkip)
{
    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeBackend(BackendParametricFixture::Backend::Gpu);
    if (!availability.available)
    {
        assert_known_gpu_skip_reason(availability.reason);
        SKIP_TEST(availability.reason.toStdString());
    }

    ASSERT_TRUE(availability.reason.isEmpty());
    ASSERT_TRUE(!availability.rendererDescription.isEmpty());
}

TEST(BackendParametricShell, CpuRenderPreviewProcessingSubsetProducesStableHash)
{
    BackendParametricFixture fixture;
    const GpuPreviewProcessingConfig config = build_supported_config(fixture);

    const std::vector<uint16_t> cpu_frame = fixture.renderPreviewProcessingSubset(
        BackendParametricFixture::Backend::Cpu, config, 0);
    ASSERT_TRUE(!cpu_frame.empty());

    const std::size_t expected_size =
        static_cast<std::size_t>(fixture.width()) *
        static_cast<std::size_t>(fixture.height()) *
        3u;
    ASSERT_EQ(expected_size, cpu_frame.size());

    const std::string hash = sha256_bytes(cpu_frame.data(),
                                          cpu_frame.size() * sizeof(uint16_t));
    test_artifacts::record(
        format_artifact_key(
            BackendParametricFixture::backendName(
                BackendParametricFixture::Backend::Cpu),
            0),
        hash);
}

TEST(BackendParametricShell, GpuRenderPreviewProcessingSubsetMatchesCpuWithinTolerance)
{
    BackendParametricFixture fixture;
    const GpuPreviewProcessingConfig config = build_supported_config(fixture);
    const std::vector<uint16_t> cpu_frame = fixture.renderPreviewProcessingSubset(
        BackendParametricFixture::Backend::Cpu, config, 0);
    ASSERT_TRUE(!cpu_frame.empty());

    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeBackend(BackendParametricFixture::Backend::Gpu);
    if (!availability.available)
    {
        assert_known_gpu_skip_reason(availability.reason);
        SKIP_TEST(availability.reason.toStdString());
    }

    QString error_message;
    const std::vector<uint16_t> gpu_frame = fixture.renderPreviewProcessingSubset(
        BackendParametricFixture::Backend::Gpu, config, 0, &error_message);
    if (gpu_frame.empty())
    {
        assert_known_gpu_skip_reason(error_message);
        SKIP_TEST(error_message.toStdString());
    }

    ASSERT_TRUE(error_message.isEmpty());
    ASSERT_EQ(cpu_frame.size(), gpu_frame.size());

    const std::string gpu_hash = sha256_bytes(gpu_frame.data(),
                                              gpu_frame.size() * sizeof(uint16_t));
    test_artifacts::record(
        format_artifact_key(
            BackendParametricFixture::backendName(
                BackendParametricFixture::Backend::Gpu),
            0),
        gpu_hash);

    const frame_compare_result_t result = compare_frames_u16(
        cpu_frame.data(), gpu_frame.data(),
        fixture.width(), fixture.height(), 3,
        /*per_pixel_tolerance=*/1);
    const frame_tolerance_verdict_t verdict = evaluate_frame_tolerance(
        result, cpu_frame.size(),
        /*max_abs_diff_threshold=*/3,
        /*max_mismatch_fraction=*/0.001);

    if (!verdict.passed)
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "GpuRenderPreviewProcessingSubsetMatchesCpuWithinTolerance",
                         verdict.detail);
    }
}

TEST(BackendParametricShell, ToleranceEvaluatorAgreesWithItselfOnPerfectMatch)
{
    BackendParametricFixture fixture;
    const GpuPreviewProcessingConfig config = build_supported_config(fixture);

    const std::vector<uint16_t> cpu_a = fixture.renderPreviewProcessingSubset(
        BackendParametricFixture::Backend::Cpu, config, 0);
    const std::vector<uint16_t> cpu_b = fixture.renderPreviewProcessingSubset(
        BackendParametricFixture::Backend::Cpu, config, 0);
    ASSERT_TRUE(!cpu_a.empty());
    ASSERT_EQ(cpu_a.size(), cpu_b.size());

    const frame_compare_result_t result = compare_frames_u16(
        cpu_a.data(), cpu_b.data(),
        fixture.width(), fixture.height(), 3,
        /*per_pixel_tolerance=*/0);

    const std::size_t total_samples = cpu_a.size();
    const frame_tolerance_verdict_t verdict = evaluate_frame_tolerance(
        result, total_samples,
        /*max_abs_diff_threshold=*/3,
        /*max_mismatch_fraction=*/0.001);

    ASSERT_TRUE(verdict.passed);
    ASSERT_EQ(static_cast<uint16_t>(0), verdict.observed_max_abs_diff);
    ASSERT_NEAR(0.0, verdict.observed_mismatch_fraction, 1e-12);
    ASSERT_TRUE(!verdict.detail.empty());
}
