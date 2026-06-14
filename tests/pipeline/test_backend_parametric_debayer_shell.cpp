#include "../common/minitest.h"
#include "../common/frame_compare.h"
#include "../common/hash_helpers.h"
#include "../common/test_artifacts.h"

#include "backend_parametric_fixture.h"

#include "../../src/processing/raw_processing.h"

#include <QString>
#include <cstddef>
#include <string>
#include <vector>

namespace {

void assert_debayer_fixture_ready(BackendParametricFixture & fixture)
{
    QString error_message;
    ASSERT_TRUE(fixture.openTinyDualIso(&error_message));
    ASSERT_TRUE(fixture.loadReceipt(
        QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"),
        &error_message));
    ASSERT_TRUE(fixture.applyReceipt(&error_message));
}

std::string format_debayer_artifact_key(const char * mode_name,
                                        const char * backend_name,
                                        uint64_t frame_index)
{
    std::string key = "tiny_dual_iso.debayer.";
    key += mode_name;
    key += ".";
    key += backend_name;
    key += ".frame";
    key += std::to_string(frame_index);
    return key;
}

void assert_known_gpu_debayer_skip_reason(const QString & reason)
{
    ASSERT_TRUE(!reason.isEmpty());
    ASSERT_TRUE(reason.contains(QStringLiteral("QOffscreenSurface"))
             || reason.contains(QStringLiteral("QOpenGLContext"))
             || reason.contains(QStringLiteral("QOpenGLFramebufferObject"))
             || reason.contains(QStringLiteral("software rasterizer"))
             || reason.contains(QStringLiteral("QOpenGLShaderProgram")));
}

void assert_known_gpu_x1_pipeline_skip_reason(const QString & reason)
{
    ASSERT_TRUE(!reason.isEmpty());
    ASSERT_TRUE(reason.contains(QStringLiteral("QOffscreenSurface"))
             || reason.contains(QStringLiteral("QOpenGLContext"))
             || reason.contains(QStringLiteral("QOpenGLFramebufferObject"))
             || reason.contains(QStringLiteral("software rasterizer"))
             || reason.contains(QStringLiteral("QOpenGLShaderProgram")));
}

void assert_amaze_gpu_tripwire_reason(const QString & reason)
{
    ASSERT_TRUE(!reason.isEmpty());
    ASSERT_TRUE(reason.contains(QStringLiteral("not yet implemented")));
    ASSERT_TRUE(reason.contains(QStringLiteral("bilinear only")));
}

std::vector<uint16_t> render_debayer_frame(BackendParametricFixture::Backend backend,
                                           BackendParametricFixture::DebayerMode mode,
                                           uint64_t frame_index,
                                           QString * error_message = nullptr)
{
    BackendParametricFixture fixture;
    assert_debayer_fixture_ready(fixture);
    return fixture.renderDebayeredFrame(
        backend,
        mode,
        frame_index,
        error_message);
}

std::string render_debayer_hash(BackendParametricFixture::DebayerMode mode,
                                uint64_t frame_index)
{
    const std::vector<uint16_t> frame = render_debayer_frame(
        BackendParametricFixture::Backend::Cpu,
        mode,
        frame_index);
    ASSERT_TRUE(!frame.empty());
    return sha256_bytes(frame.data(), frame.size() * sizeof(uint16_t));
}

void configure_p_pre_supported_processing_subset(BackendParametricFixture & fixture)
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

GpuPreviewProcessingConfig build_p_pre_supported_processing_config(
    BackendParametricFixture & fixture)
{
    configure_p_pre_supported_processing_subset(fixture);

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

std::vector<uint16_t> render_x1_debayer_processing_frame(
    BackendParametricFixture & fixture,
    BackendParametricFixture::Backend backend,
    BackendParametricFixture::DebayerMode mode,
    const GpuPreviewProcessingConfig & config,
    uint64_t frame_index,
    QString * error_message = nullptr)
{
    QString local_error;
    std::vector<uint16_t> debayered = fixture.renderDebayeredFrame(
        backend, mode, frame_index, &local_error);
    if (debayered.empty())
    {
        if (error_message) *error_message = local_error;
        return std::vector<uint16_t>();
    }

    std::vector<uint16_t> output(debayered.size(), 0);
    switch (backend)
    {
    case BackendParametricFixture::Backend::Cpu:
        gpuPreviewProcessingApplyCpuReference(config,
                                              debayered.data(),
                                              output.data(),
                                              fixture.width() * fixture.height());
        return output;

    case BackendParametricFixture::Backend::Gpu:
        if (!gpuPreviewProcessingApplyGpuOffscreen(config,
                                                   debayered.data(),
                                                   output.data(),
                                                   fixture.width(),
                                                   fixture.height(),
                                                   &local_error))
        {
            if (error_message) *error_message = local_error;
            return std::vector<uint16_t>();
        }
        return output;
    }

    if (error_message)
    {
        *error_message = QStringLiteral("unknown backend enumerator");
    }
    return std::vector<uint16_t>();
}

std::string format_x1_pipeline_artifact_key(const char * mode_name,
                                            const char * backend_name,
                                            uint64_t frame_index)
{
    std::string key = "tiny_dual_iso.x1_debayer_processing.";
    key += mode_name;
    key += ".";
    key += backend_name;
    key += ".frame";
    key += std::to_string(frame_index);
    return key;
}

} // namespace

TEST(BackendParametricDebayerShell, CpuRenderDebayerBilinearProducesStableHash)
{
    const std::string hash = render_debayer_hash(
        BackendParametricFixture::DebayerMode::Bilinear, 0);
    test_artifacts::record(
        format_debayer_artifact_key(
            BackendParametricFixture::debayerModeName(
                BackendParametricFixture::DebayerMode::Bilinear),
            BackendParametricFixture::backendName(
                BackendParametricFixture::Backend::Cpu),
            0),
        hash);
}

TEST(BackendParametricDebayerShell, CpuRenderDebayerAmazeProducesStableHash)
{
    const std::string hash = render_debayer_hash(
        BackendParametricFixture::DebayerMode::Amaze, 0);
    test_artifacts::record(
        format_debayer_artifact_key(
            BackendParametricFixture::debayerModeName(
                BackendParametricFixture::DebayerMode::Amaze),
            BackendParametricFixture::backendName(
                BackendParametricFixture::Backend::Cpu),
            0),
        hash);
}

TEST(BackendParametricDebayerShell, CpuRenderDebayerModesProduceDistinctOutput)
{
    const std::string bilinear_hash = render_debayer_hash(
        BackendParametricFixture::DebayerMode::Bilinear, 0);
    const std::string amaze_hash = render_debayer_hash(
        BackendParametricFixture::DebayerMode::Amaze, 0);
    ASSERT_TRUE(bilinear_hash != amaze_hash);
}

TEST(BackendParametricDebayerShell, GpuRenderDebayerBilinearMatchesCpuWithinToleranceOrSkips)
{
    BackendParametricFixture fixture;
    assert_debayer_fixture_ready(fixture);
    const std::vector<uint16_t> cpu_frame = fixture.renderDebayeredFrame(
        BackendParametricFixture::Backend::Cpu,
        BackendParametricFixture::DebayerMode::Bilinear,
        0);
    ASSERT_TRUE(!cpu_frame.empty());

    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeDebayerBackend(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Bilinear);
    if (!availability.available)
    {
        assert_known_gpu_debayer_skip_reason(availability.reason);
        SKIP_TEST(availability.reason.toStdString());
    }

    QString error_message;
    const std::vector<uint16_t> gpu_frame = fixture.renderDebayeredFrame(
        BackendParametricFixture::Backend::Gpu,
        BackendParametricFixture::DebayerMode::Bilinear,
        0,
        &error_message);
    if (gpu_frame.empty())
    {
        assert_known_gpu_debayer_skip_reason(error_message);
        SKIP_TEST(error_message.toStdString());
    }

    ASSERT_TRUE(error_message.isEmpty());
    ASSERT_EQ(cpu_frame.size(), gpu_frame.size());

    const std::string gpu_hash = sha256_bytes(gpu_frame.data(),
                                              gpu_frame.size() * sizeof(uint16_t));
    test_artifacts::record(
        format_debayer_artifact_key(
            BackendParametricFixture::debayerModeName(
                BackendParametricFixture::DebayerMode::Bilinear),
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
                         "GpuRenderDebayerBilinearMatchesCpuWithinToleranceOrSkips",
                         verdict.detail);
    }
}

TEST(BackendParametricDebayerShell, GpuRenderDebayerAmazeSkipsUntilBackendLands)
{
    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeDebayerBackend(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Amaze);
    if (!availability.available)
    {
        assert_amaze_gpu_tripwire_reason(availability.reason);
        SKIP_TEST(availability.reason.toStdString());
    }

    ::minitest::fail(__FILE__, __LINE__,
                     "BackendParametricFixture::probeDebayerBackend(Backend::Gpu, DebayerMode::Amaze)",
                     "GPU AMaZE debayer backend is now available; update the slice-3a tripwire test.");
}

TEST(BackendParametricDebayerShell, PPreX1BilinearDebayerProcessingFrameDiffWithinExistingToleranceOrSkips)
{
    BackendParametricFixture fixture;
    assert_debayer_fixture_ready(fixture);
    const GpuPreviewProcessingConfig config =
        build_p_pre_supported_processing_config(fixture);

    const std::vector<uint16_t> cpu_frame =
        render_x1_debayer_processing_frame(
            fixture,
            BackendParametricFixture::Backend::Cpu,
            BackendParametricFixture::DebayerMode::Bilinear,
            config,
            0);
    ASSERT_TRUE(!cpu_frame.empty());

    const BackendParametricFixture::BackendAvailability debayer_availability =
        BackendParametricFixture::probeDebayerBackend(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Bilinear);
    if (!debayer_availability.available)
    {
        assert_known_gpu_x1_pipeline_skip_reason(debayer_availability.reason);
        SKIP_TEST(debayer_availability.reason.toStdString());
    }

    const BackendParametricFixture::BackendAvailability processing_availability =
        BackendParametricFixture::probeBackend(BackendParametricFixture::Backend::Gpu);
    if (!processing_availability.available)
    {
        assert_known_gpu_x1_pipeline_skip_reason(processing_availability.reason);
        SKIP_TEST(processing_availability.reason.toStdString());
    }

    QString error_message;
    const std::vector<uint16_t> gpu_frame =
        render_x1_debayer_processing_frame(
            fixture,
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Bilinear,
            config,
            0,
            &error_message);
    if (gpu_frame.empty())
    {
        assert_known_gpu_x1_pipeline_skip_reason(error_message);
        SKIP_TEST(error_message.toStdString());
    }

    ASSERT_TRUE(error_message.isEmpty());
    ASSERT_EQ(cpu_frame.size(), gpu_frame.size());

    const std::string cpu_hash = sha256_bytes(cpu_frame.data(),
                                              cpu_frame.size() * sizeof(uint16_t));
    const std::string gpu_hash = sha256_bytes(gpu_frame.data(),
                                              gpu_frame.size() * sizeof(uint16_t));
    test_artifacts::record(
        format_x1_pipeline_artifact_key(
            BackendParametricFixture::debayerModeName(
                BackendParametricFixture::DebayerMode::Bilinear),
            BackendParametricFixture::backendName(
                BackendParametricFixture::Backend::Cpu),
            0),
        cpu_hash);
    test_artifacts::record(
        format_x1_pipeline_artifact_key(
            BackendParametricFixture::debayerModeName(
                BackendParametricFixture::DebayerMode::Bilinear),
            BackendParametricFixture::backendName(
                BackendParametricFixture::Backend::Gpu),
            0),
        gpu_hash);

    const uint16_t per_pixel_tolerance = 1;
    const uint16_t max_abs_diff_threshold = 3;
    const double max_mismatch_fraction = 0.001;
    const frame_compare_result_t result = compare_frames_u16(
        cpu_frame.data(), gpu_frame.data(),
        fixture.width(), fixture.height(), 3,
        per_pixel_tolerance);
    const frame_tolerance_verdict_t verdict = evaluate_frame_tolerance(
        result, cpu_frame.size(),
        max_abs_diff_threshold,
        max_mismatch_fraction);

    if (!verdict.passed)
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "PPreX1BilinearDebayerProcessingFrameDiffWithinExistingToleranceOrSkips",
                         verdict.detail);
    }
}

TEST(BackendParametricDebayerShell, PPreX1AmazeDebayerProcessingFrameDiffSkipsUntilBackendLands)
{
    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeDebayerBackend(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Amaze);
    if (!availability.available)
    {
        assert_amaze_gpu_tripwire_reason(availability.reason);
        SKIP_TEST(availability.reason.toStdString());
    }

    ::minitest::fail(__FILE__, __LINE__,
                     "BackendParametricFixture::probeDebayerBackend(Backend::Gpu, DebayerMode::Amaze)",
                     "GPU AMaZE debayer backend is now available; update this P-pre x1 frame-diff test to compare CPU-vs-GPU pixels before any Full Quality AMaZE claim.");
}
