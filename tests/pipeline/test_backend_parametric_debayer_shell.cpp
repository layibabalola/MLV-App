#include "../common/minitest.h"
#include "../common/frame_compare.h"
#include "../common/hash_helpers.h"
#include "../common/test_artifacts.h"

#include "backend_parametric_fixture.h"

#include "../../src/processing/raw_processing.h"

#include <QString>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <sstream>
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

void assert_known_gpu_amaze_skip_reason(const QString & reason)
{
    ASSERT_TRUE(!reason.isEmpty());
    ASSERT_TRUE(reason.contains(QStringLiteral("GPU AMaZE debayer backend"))
             || reason.contains(QStringLiteral("GPU AMaZE debayer fixture")));
    ASSERT_TRUE(reason.contains(QStringLiteral("do not route AMaZE to bilinear"))
             || reason.contains(QStringLiteral("DLL load failed"))
             || reason.contains(QStringLiteral("missing ABI symbol"))
             || reason.contains(QStringLiteral("create('cuda') failed"))
             || reason.contains(QStringLiteral("ABI mismatch")));
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

std::vector<uint16_t> apply_p_pre_processing_frame(
    BackendParametricFixture & fixture,
    BackendParametricFixture::Backend backend,
    const GpuPreviewProcessingConfig & config,
    const std::vector<uint16_t> & debayered,
    QString * error_message = nullptr)
{
    if (debayered.empty())
    {
        if (error_message) *error_message = QStringLiteral("debayered frame is empty");
        return std::vector<uint16_t>();
    }

    std::vector<uint16_t> output(debayered.size(), 0);
    QString local_error;
    switch (backend)
    {
    case BackendParametricFixture::Backend::Cpu:
        gpuPreviewProcessingApplyCpuReference(config,
                                              debayered.data(),
                                              output.data(),
                                              fixture.width(),
                                              fixture.height());
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

double mismatch_fraction(const frame_compare_result_t & result,
                         std::size_t total_samples)
{
    return total_samples == 0
        ? 0.0
        : static_cast<double>(result.pixels_exceeding_tolerance) /
          static_cast<double>(total_samples);
}

std::string format_compare_detail(const char * label,
                                  const frame_compare_result_t & result,
                                  std::size_t total_samples)
{
    std::ostringstream stream;
    stream.setf(std::ios::fixed);
    stream.precision(6);
    stream << label
           << "{max_abs_diff=" << result.max_abs_diff
           << ";mismatch_fraction=" << mismatch_fraction(result, total_samples)
           << ";pixels_exceeding=" << result.pixels_exceeding_tolerance
           << ";mean_abs=" << result.mean_abs_diff
           << "}";
    return stream.str();
}

std::string format_processing_settings_fingerprint(
    const char * label,
    const char * debayer_name,
    const processingObject_t * processing,
    const GpuPreviewProcessingConfig & config)
{
    std::ostringstream stream;
    stream.setf(std::ios::fixed);
    stream.precision(6);
    const int dual_iso_value = processing && processing->dual_iso
        ? *processing->dual_iso
        : -1;
    stream << "label=" << label
           << ";debayer=" << debayer_name
           << ";config_signature=" << config.signature
           << ";enabled=" << (config.enabled ? 1 : 0)
           << ";source_exposure_stops=" << config.sourceExposureStops
           << ";processing_exposure_stops=" << (processing ? processing->exposure_stops : 0.0)
           << ";kelvin=" << (processing ? processing->kelvin : 0.0)
           << ";wb_tint=" << (processing ? processing->wb_tint : 0.0)
           << ";wb_multipliers="
           << (processing ? processing->wb_multipliers[0] : 0.0) << ","
           << (processing ? processing->wb_multipliers[1] : 0.0) << ","
           << (processing ? processing->wb_multipliers[2] : 0.0)
           << ";dual_iso=" << dual_iso_value
           << ";exr_mode=" << (processing ? processing->exr_mode : 0)
           << ";use_camera_matrix=" << (config.useCameraMatrix ? 1 : 0)
           << ";processing_use_cam_matrix="
           << (processing ? static_cast<int>(processing->use_cam_matrix) : 0)
           << ";colour_gamut="
           << (processing ? static_cast<int>(processing->colour_gamut) : 0)
           << ";apply_gamut_compression="
           << (config.applyGamutCompression ? 1 : 0)
           << ";levels_lut_bytes=" << config.levelsLut.size()
           << ";matrix_lut_bytes="
           << config.matrixLutR.size() << ","
           << config.matrixLutG.size() << ","
           << config.matrixLutB.size()
           << ";gamma_lut_bytes=" << config.gammaLut.size();
    return stream.str();
}

std::string format_worst_debayer_samples(const std::vector<uint16_t> & cpu_debayer,
                                         const std::vector<uint16_t> & gpu_debayer,
                                         int width,
                                         int height,
                                         std::size_t max_samples)
{
    struct Sample
    {
        uint16_t abs_delta;
        std::size_t index;
    };

    std::vector<Sample> samples;
    const std::size_t sample_count = std::min(cpu_debayer.size(), gpu_debayer.size());
    samples.reserve(std::min(sample_count, max_samples));
    for (std::size_t index = 0; index < sample_count; ++index)
    {
        const uint16_t abs_delta = static_cast<uint16_t>(
            std::abs(static_cast<int>(gpu_debayer[index]) -
                     static_cast<int>(cpu_debayer[index])));
        if (samples.size() < max_samples)
        {
            samples.push_back({abs_delta, index});
            std::sort(samples.begin(), samples.end(),
                      [](const Sample & a, const Sample & b) {
                          return a.abs_delta > b.abs_delta;
                      });
        }
        else if (abs_delta > samples.back().abs_delta)
        {
            samples.back() = {abs_delta, index};
            std::sort(samples.begin(), samples.end(),
                      [](const Sample & a, const Sample & b) {
                          return a.abs_delta > b.abs_delta;
                      });
        }
    }

    std::ostringstream stream;
    const int channels = 3;
    for (std::size_t sample_index = 0; sample_index < samples.size(); ++sample_index)
    {
        const std::size_t index = samples[sample_index].index;
        const std::size_t pixel = index / channels;
        const int channel = static_cast<int>(index % channels);
        const int x = width > 0 ? static_cast<int>(pixel % static_cast<std::size_t>(width)) : 0;
        const int y = width > 0 ? static_cast<int>(pixel / static_cast<std::size_t>(width)) : 0;
        if (sample_index > 0) stream << "|";
        stream << "x=" << x
               << ",y=" << y
               << ",c=" << channel
               << ",cpu=" << cpu_debayer[index]
               << ",gpu=" << gpu_debayer[index]
               << ",abs=" << samples[sample_index].abs_delta;
    }
    if (samples.empty())
    {
        stream << "none";
    }
    stream << ";frame=" << width << "x" << height;
    return stream.str();
}

std::string format_worst_x1_samples(const std::vector<uint16_t> & cpu_x1,
                                    const std::vector<uint16_t> & gpu_x1,
                                    const std::vector<uint16_t> & expected_from_gpu_debayer,
                                    const std::vector<uint16_t> & cpu_debayer,
                                    const std::vector<uint16_t> & gpu_debayer,
                                    int width,
                                    int height,
                                    std::size_t max_samples)
{
    struct Sample
    {
        uint16_t x1_abs;
        std::size_t index;
    };

    std::vector<Sample> samples;
    const std::size_t sample_count = std::min(cpu_x1.size(), gpu_x1.size());
    samples.reserve(std::min(sample_count, max_samples));
    for (std::size_t index = 0; index < sample_count; ++index)
    {
        const uint16_t abs_delta = static_cast<uint16_t>(
            std::abs(static_cast<int>(gpu_x1[index]) - static_cast<int>(cpu_x1[index])));
        if (samples.size() < max_samples)
        {
            samples.push_back({abs_delta, index});
            std::sort(samples.begin(), samples.end(),
                      [](const Sample & a, const Sample & b) {
                          return a.x1_abs > b.x1_abs;
                      });
        }
        else if (abs_delta > samples.back().x1_abs)
        {
            samples.back() = {abs_delta, index};
            std::sort(samples.begin(), samples.end(),
                      [](const Sample & a, const Sample & b) {
                          return a.x1_abs > b.x1_abs;
                      });
        }
    }

    std::ostringstream stream;
    const int channels = 3;
    for (std::size_t sample_index = 0; sample_index < samples.size(); ++sample_index)
    {
        const std::size_t index = samples[sample_index].index;
        const std::size_t pixel = index / channels;
        const int channel = static_cast<int>(index % channels);
        const int x = width > 0 ? static_cast<int>(pixel % static_cast<std::size_t>(width)) : 0;
        const int y = width > 0 ? static_cast<int>(pixel / static_cast<std::size_t>(width)) : 0;
        if (sample_index > 0) stream << "|";
        stream << "x=" << x
               << ",y=" << y
               << ",c=" << channel
               << ",cpu_x1=" << cpu_x1[index]
               << ",gpu_x1=" << gpu_x1[index]
               << ",cpu_on_gpu_debayer=" << expected_from_gpu_debayer[index]
               << ",cpu_debayer=" << cpu_debayer[index]
               << ",gpu_debayer=" << gpu_debayer[index]
               << ",x1_abs=" << samples[sample_index].x1_abs
               << ",debayer_abs="
               << std::abs(static_cast<int>(gpu_debayer[index]) -
                           static_cast<int>(cpu_debayer[index]));
    }
    if (samples.empty())
    {
        stream << "none";
    }
    stream << ";frame=" << width << "x" << height;
    return stream.str();
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

TEST(BackendParametricDebayerShell, GpuRenderDebayerAmazeMatchesCpuWithinToleranceOrSkips)
{
    BackendParametricFixture fixture;
    assert_debayer_fixture_ready(fixture);

    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeDebayerBackend(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Amaze);
    if (!availability.available)
    {
        assert_known_gpu_amaze_skip_reason(availability.reason);
        SKIP_TEST(availability.reason.toStdString());
    }

    const uint64_t frame_indices[] = {0, 1};
    const uint16_t per_pixel_tolerance = 2;
    /* Production CPU AMaZE is built through its SSE2 path; the CUDA backend
     * reuses the reviewed scalar-transcribed kernels. The RTX 4090 DLL-present
     * gate measured frame 0 at max_abs=62 / mismatch=0.003558 and frame 1 at
     * max_abs=117 / mismatch=0.003534, so keep only a small deterministic
     * margin over the observed SSE-vs-scalar edge-halo residual. */
    const uint16_t max_abs_diff_threshold = 120;
    const double max_mismatch_fraction = 0.004;
    std::string failure_detail;

    for (uint64_t frame_index : frame_indices)
    {
        const std::vector<uint16_t> cpu_frame = fixture.renderDebayeredFrame(
            BackendParametricFixture::Backend::Cpu,
            BackendParametricFixture::DebayerMode::Amaze,
            frame_index);
        ASSERT_TRUE(!cpu_frame.empty());

        QString error_message;
        const std::vector<uint16_t> gpu_frame = fixture.renderDebayeredFrame(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Amaze,
            frame_index,
            &error_message);
        ASSERT_TRUE(error_message.isEmpty());
        ASSERT_TRUE(!gpu_frame.empty());
        ASSERT_EQ(cpu_frame.size(), gpu_frame.size());

        const std::string gpu_hash = sha256_bytes(gpu_frame.data(),
                                                  gpu_frame.size() * sizeof(uint16_t));
        test_artifacts::record(
            format_debayer_artifact_key(
                BackendParametricFixture::debayerModeName(
                    BackendParametricFixture::DebayerMode::Amaze),
                BackendParametricFixture::backendName(
                    BackendParametricFixture::Backend::Gpu),
                frame_index),
            gpu_hash);

        const frame_compare_result_t result = compare_frames_u16(
            cpu_frame.data(), gpu_frame.data(),
            fixture.width(), fixture.height(), 3,
            per_pixel_tolerance);
        const frame_tolerance_verdict_t verdict = evaluate_frame_tolerance(
            result, cpu_frame.size(),
            max_abs_diff_threshold,
            max_mismatch_fraction);
        const std::string diagnostic =
            std::string("frame=") + std::to_string(frame_index)
            + ";per_pixel_tolerance=" + std::to_string(per_pixel_tolerance)
            + ";max_abs_diff_threshold=" + std::to_string(max_abs_diff_threshold)
            + ";max_mismatch_fraction=" + std::to_string(max_mismatch_fraction)
            + ";"
            + format_compare_detail("debayer", result, cpu_frame.size())
            + ";worst_samples="
            + format_worst_debayer_samples(cpu_frame,
                                           gpu_frame,
                                           fixture.width(),
                                           fixture.height(),
                                           16);
        std::printf("AmazeDebayerParity: %s\n", diagnostic.c_str());

        if (!verdict.passed)
        {
            if (!failure_detail.empty()) failure_detail += " | ";
            failure_detail += verdict.detail + ";" + diagnostic;
        }
    }

    if (!failure_detail.empty())
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "GpuRenderDebayerAmazeMatchesCpuWithinToleranceOrSkips",
                         failure_detail);
    }
}

TEST(BackendParametricDebayerShell, GpuRenderDebayerAmazeDoesNotFallbackToBilinearOrCpu)
{
    BackendParametricFixture fixture;
    assert_debayer_fixture_ready(fixture);

    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeDebayerBackend(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Amaze);

    QString error_message;
    const std::vector<uint16_t> gpu_frame = fixture.renderDebayeredFrame(
        BackendParametricFixture::Backend::Gpu,
        BackendParametricFixture::DebayerMode::Amaze,
        0,
        &error_message);

    if (!availability.available)
    {
        ASSERT_TRUE(gpu_frame.empty());
        assert_known_gpu_amaze_skip_reason(error_message);
        SKIP_TEST(error_message.toStdString());
    }

    ASSERT_TRUE(error_message.isEmpty());
    ASSERT_TRUE(!gpu_frame.empty());

    const std::vector<uint16_t> cpu_bilinear = fixture.renderDebayeredFrame(
        BackendParametricFixture::Backend::Cpu,
        BackendParametricFixture::DebayerMode::Bilinear,
        0);
    ASSERT_TRUE(!cpu_bilinear.empty());
    ASSERT_EQ(cpu_bilinear.size(), gpu_frame.size());

    const frame_compare_result_t bilinear_result = compare_frames_u16(
        cpu_bilinear.data(), gpu_frame.data(),
        fixture.width(), fixture.height(), 3,
        /*per_pixel_tolerance=*/0);
    ASSERT_TRUE(bilinear_result.pixels_exceeding_tolerance > 0);
}

TEST(BackendParametricDebayerShell, PPreX1BilinearProcessingMatchesCpuReferenceOnGpuDebayerInputOrSkips)
{
    BackendParametricFixture fixture;
    assert_debayer_fixture_ready(fixture);
    const GpuPreviewProcessingConfig config =
        build_p_pre_supported_processing_config(fixture);

    QString error_message;
    const std::vector<uint16_t> cpu_debayer_frame = fixture.renderDebayeredFrame(
        BackendParametricFixture::Backend::Cpu,
        BackendParametricFixture::DebayerMode::Bilinear,
        0,
        &error_message);
    ASSERT_TRUE(error_message.isEmpty());
    ASSERT_TRUE(!cpu_debayer_frame.empty());

    const std::vector<uint16_t> cpu_frame =
        apply_p_pre_processing_frame(
            fixture,
            BackendParametricFixture::Backend::Cpu,
            config,
            cpu_debayer_frame);
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

    error_message.clear();
    const std::vector<uint16_t> gpu_debayer_frame = fixture.renderDebayeredFrame(
        BackendParametricFixture::Backend::Gpu,
        BackendParametricFixture::DebayerMode::Bilinear,
        0,
        &error_message);
    if (gpu_debayer_frame.empty())
    {
        assert_known_gpu_x1_pipeline_skip_reason(error_message);
        SKIP_TEST(error_message.toStdString());
    }

    ASSERT_TRUE(error_message.isEmpty());
    ASSERT_EQ(cpu_debayer_frame.size(), gpu_debayer_frame.size());

    const std::vector<uint16_t> expected_from_gpu_debayer =
        apply_p_pre_processing_frame(
            fixture,
            BackendParametricFixture::Backend::Cpu,
            config,
            gpu_debayer_frame);
    ASSERT_TRUE(!expected_from_gpu_debayer.empty());

    error_message.clear();
    const std::vector<uint16_t> gpu_frame =
        apply_p_pre_processing_frame(
            fixture,
            BackendParametricFixture::Backend::Gpu,
            config,
            gpu_debayer_frame,
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
    /*
     * Real-GL validation of this fixture on an RTX 4090 measured isolated
     * GPU-vs-CPU processing-chain variance at max_abs=5 on 0.0126% of samples
     * (mean_abs ~= 0.0003). Keep the fraction tight, but allow headroom above
     * that float-rounding bound because this stage chains matrix, gamut, and
     * gamma LUT operations rather than a single debayer step.
     */
    const uint16_t processing_max_abs_slack = 8;
    const double processing_mismatch_fraction_slack = 0.001;
    const std::size_t total_samples = cpu_frame.size();

    /*
     * This x1 check composes two contracts. The raw GPU debayer must stay
     * within its own tolerance, and GPU processing must match CPU processing
     * when both consume that same GPU-debayered frame. The end-to-end CPU-vs-GPU
     * delta is then allowed only inside the measured debayer-amplification
     * envelope plus the isolated processing slack.
     */
    const frame_compare_result_t debayer_result = compare_frames_u16(
        cpu_debayer_frame.data(), gpu_debayer_frame.data(),
        fixture.width(), fixture.height(), 3,
        per_pixel_tolerance);
    const frame_tolerance_verdict_t debayer_verdict = evaluate_frame_tolerance(
        debayer_result, cpu_debayer_frame.size(),
        /*max_abs_diff_threshold=*/3,
        /*max_mismatch_fraction=*/0.001);
    if (!debayer_verdict.passed)
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "PPreX1Bilinear debayer precondition",
                         debayer_verdict.detail);
    }

    const frame_compare_result_t expected_amplification = compare_frames_u16(
        cpu_frame.data(), expected_from_gpu_debayer.data(),
        fixture.width(), fixture.height(), 3,
        per_pixel_tolerance);
    const frame_compare_result_t isolated_processing = compare_frames_u16(
        expected_from_gpu_debayer.data(), gpu_frame.data(),
        fixture.width(), fixture.height(), 3,
        per_pixel_tolerance);
    const frame_tolerance_verdict_t processing_verdict = evaluate_frame_tolerance(
        isolated_processing, total_samples,
        processing_max_abs_slack,
        processing_mismatch_fraction_slack);

    const frame_compare_result_t end_to_end = compare_frames_u16(
        cpu_frame.data(), gpu_frame.data(),
        fixture.width(), fixture.height(), 3,
        per_pixel_tolerance);
    const uint16_t derived_max_abs_threshold =
        expected_amplification.max_abs_diff >
            static_cast<uint16_t>(65535u - processing_max_abs_slack)
            ? static_cast<uint16_t>(65535u)
            : static_cast<uint16_t>(expected_amplification.max_abs_diff +
                                    processing_max_abs_slack);
    const double derived_mismatch_fraction_threshold = std::min(
        1.0,
        mismatch_fraction(expected_amplification, total_samples) +
            processing_mismatch_fraction_slack);
    const frame_tolerance_verdict_t end_to_end_verdict = evaluate_frame_tolerance(
        end_to_end, total_samples,
        derived_max_abs_threshold,
        derived_mismatch_fraction_threshold);

    const std::string diagnostic =
        format_compare_detail("debayer", debayer_result, cpu_debayer_frame.size())
        + ";"
        + format_compare_detail("expected_amplification", expected_amplification, total_samples)
        + ";"
        + format_compare_detail("isolated_processing", isolated_processing, total_samples)
        + ";"
        + format_compare_detail("end_to_end", end_to_end, total_samples)
        + ";derived_max_abs_threshold=" + std::to_string(derived_max_abs_threshold)
        + ";derived_mismatch_fraction_threshold=" +
            std::to_string(derived_mismatch_fraction_threshold)
        + ";worst_samples="
        + format_worst_x1_samples(cpu_frame,
                                  gpu_frame,
                                  expected_from_gpu_debayer,
                                  cpu_debayer_frame,
                                  gpu_debayer_frame,
                                  fixture.width(),
                                  fixture.height(),
                                  16);
    std::printf("PPreX1BilinearDerivedTolerance: %s\n", diagnostic.c_str());

    if (!processing_verdict.passed)
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "PPreX1Bilinear isolated GPU processing parity",
                         processing_verdict.detail + ";" + diagnostic);
    }

    if (!end_to_end_verdict.passed)
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "PPreX1Bilinear derived debayer-amplification envelope",
                         end_to_end_verdict.detail + ";" + diagnostic);
    }
}

TEST(BackendParametricDebayerShell, PPreX1AmazeDebayerProcessingFrameDiffMatchesCpuReferenceOrSkips)
{
    BackendParametricFixture fixture;
    assert_debayer_fixture_ready(fixture);
    const GpuPreviewProcessingConfig config =
        build_p_pre_supported_processing_config(fixture);
    const std::string settings_fingerprint =
        format_processing_settings_fingerprint(
            "p_pre_x1_amaze",
            BackendParametricFixture::debayerModeName(
                BackendParametricFixture::DebayerMode::Amaze),
            fixture.processing(),
            config);
    std::printf("PPreX1ProcessingSettings: %s\n",
                settings_fingerprint.c_str());

    const BackendParametricFixture::BackendAvailability availability =
        BackendParametricFixture::probeDebayerBackend(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Amaze);
    if (!availability.available)
    {
        assert_known_gpu_amaze_skip_reason(availability.reason);
        SKIP_TEST(availability.reason.toStdString());
    }

    const BackendParametricFixture::BackendAvailability processing_availability =
        BackendParametricFixture::probeBackend(BackendParametricFixture::Backend::Gpu);
    if (!processing_availability.available)
    {
        assert_known_gpu_x1_pipeline_skip_reason(processing_availability.reason);
        SKIP_TEST(processing_availability.reason.toStdString());
    }

    const uint16_t debayer_per_pixel_tolerance = 2;
    /* Same measured AMaZE SSE-vs-scalar debayer envelope as the standalone
     * GPU-vs-CPU test; the isolated GPU processing comparison below stays much
     * tighter. */
    const uint16_t debayer_max_abs_slack = 120;
    const double debayer_mismatch_fraction_slack = 0.004;
    const uint16_t processing_per_pixel_tolerance = 1;
    const uint16_t processing_max_abs_slack = 8;
    const double processing_mismatch_fraction_slack = 0.001;
    const uint64_t frame_indices[] = {0, 1};
    std::string failure_detail;

    for (uint64_t frame_index : frame_indices)
    {
        QString error_message;
        const std::vector<uint16_t> cpu_debayer_frame = fixture.renderDebayeredFrame(
            BackendParametricFixture::Backend::Cpu,
            BackendParametricFixture::DebayerMode::Amaze,
            frame_index,
            &error_message);
        ASSERT_TRUE(error_message.isEmpty());
        ASSERT_TRUE(!cpu_debayer_frame.empty());

        const std::vector<uint16_t> cpu_frame =
            apply_p_pre_processing_frame(
                fixture,
                BackendParametricFixture::Backend::Cpu,
                config,
                cpu_debayer_frame);
        ASSERT_TRUE(!cpu_frame.empty());

        error_message.clear();
        const std::vector<uint16_t> gpu_debayer_frame = fixture.renderDebayeredFrame(
            BackendParametricFixture::Backend::Gpu,
            BackendParametricFixture::DebayerMode::Amaze,
            frame_index,
            &error_message);
        ASSERT_TRUE(error_message.isEmpty());
        ASSERT_TRUE(!gpu_debayer_frame.empty());
        ASSERT_EQ(cpu_debayer_frame.size(), gpu_debayer_frame.size());

        const std::vector<uint16_t> expected_from_gpu_debayer =
            apply_p_pre_processing_frame(
                fixture,
                BackendParametricFixture::Backend::Cpu,
                config,
                gpu_debayer_frame);
        ASSERT_TRUE(!expected_from_gpu_debayer.empty());

        error_message.clear();
        const std::vector<uint16_t> gpu_frame =
            apply_p_pre_processing_frame(
                fixture,
                BackendParametricFixture::Backend::Gpu,
                config,
                gpu_debayer_frame,
                &error_message);
        ASSERT_TRUE(error_message.isEmpty());
        ASSERT_TRUE(!gpu_frame.empty());
        ASSERT_EQ(cpu_frame.size(), gpu_frame.size());

        const std::string cpu_hash = sha256_bytes(cpu_frame.data(),
                                                  cpu_frame.size() * sizeof(uint16_t));
        const std::string gpu_hash = sha256_bytes(gpu_frame.data(),
                                                  gpu_frame.size() * sizeof(uint16_t));
        test_artifacts::record(
            format_x1_pipeline_artifact_key(
                BackendParametricFixture::debayerModeName(
                    BackendParametricFixture::DebayerMode::Amaze),
                BackendParametricFixture::backendName(
                    BackendParametricFixture::Backend::Cpu),
                frame_index),
            cpu_hash);
        test_artifacts::record(
            format_x1_pipeline_artifact_key(
                BackendParametricFixture::debayerModeName(
                    BackendParametricFixture::DebayerMode::Amaze),
                BackendParametricFixture::backendName(
                    BackendParametricFixture::Backend::Gpu),
                frame_index),
            gpu_hash);

        const std::size_t total_samples = cpu_frame.size();
        const frame_compare_result_t debayer_result = compare_frames_u16(
            cpu_debayer_frame.data(), gpu_debayer_frame.data(),
            fixture.width(), fixture.height(), 3,
            debayer_per_pixel_tolerance);
        const frame_tolerance_verdict_t debayer_verdict = evaluate_frame_tolerance(
            debayer_result, cpu_debayer_frame.size(),
            debayer_max_abs_slack,
            debayer_mismatch_fraction_slack);

        const frame_compare_result_t expected_amplification = compare_frames_u16(
            cpu_frame.data(), expected_from_gpu_debayer.data(),
            fixture.width(), fixture.height(), 3,
            processing_per_pixel_tolerance);
        const frame_compare_result_t isolated_processing = compare_frames_u16(
            expected_from_gpu_debayer.data(), gpu_frame.data(),
            fixture.width(), fixture.height(), 3,
            processing_per_pixel_tolerance);
        const frame_tolerance_verdict_t processing_verdict = evaluate_frame_tolerance(
            isolated_processing, total_samples,
            processing_max_abs_slack,
            processing_mismatch_fraction_slack);

        const frame_compare_result_t end_to_end = compare_frames_u16(
            cpu_frame.data(), gpu_frame.data(),
            fixture.width(), fixture.height(), 3,
            processing_per_pixel_tolerance);
        const uint16_t derived_max_abs_threshold =
            expected_amplification.max_abs_diff >
                static_cast<uint16_t>(65535u - processing_max_abs_slack)
                ? static_cast<uint16_t>(65535u)
                : static_cast<uint16_t>(expected_amplification.max_abs_diff +
                                        processing_max_abs_slack);
        const double derived_mismatch_fraction_threshold = std::min(
            1.0,
            mismatch_fraction(expected_amplification, total_samples) +
                processing_mismatch_fraction_slack);
        const frame_tolerance_verdict_t end_to_end_verdict = evaluate_frame_tolerance(
            end_to_end, total_samples,
            derived_max_abs_threshold,
            derived_mismatch_fraction_threshold);

        const std::string diagnostic =
            std::string("frame=") + std::to_string(frame_index)
            + ";"
            + format_compare_detail("debayer", debayer_result, cpu_debayer_frame.size())
            + ";"
            + format_compare_detail("expected_amplification", expected_amplification, total_samples)
            + ";"
            + format_compare_detail("isolated_processing", isolated_processing, total_samples)
            + ";"
            + format_compare_detail("end_to_end", end_to_end, total_samples)
            + ";derived_max_abs_threshold=" + std::to_string(derived_max_abs_threshold)
            + ";derived_mismatch_fraction_threshold=" +
                std::to_string(derived_mismatch_fraction_threshold)
            + ";worst_samples="
            + format_worst_x1_samples(cpu_frame,
                                      gpu_frame,
                                      expected_from_gpu_debayer,
                                      cpu_debayer_frame,
                                      gpu_debayer_frame,
                                      fixture.width(),
                                      fixture.height(),
                                      16);
        std::printf("PPreX1AmazeDerivedTolerance: %s\n", diagnostic.c_str());

        if (!debayer_verdict.passed)
        {
            if (!failure_detail.empty()) failure_detail += " | ";
            failure_detail += "debayer parity;" + debayer_verdict.detail + ";" + diagnostic;
        }

        if (!processing_verdict.passed)
        {
            if (!failure_detail.empty()) failure_detail += " | ";
            failure_detail += "isolated GPU processing parity;" +
                processing_verdict.detail + ";" + diagnostic;
        }

        if (!end_to_end_verdict.passed)
        {
            if (!failure_detail.empty()) failure_detail += " | ";
            failure_detail += "derived debayer-amplification envelope;" +
                end_to_end_verdict.detail + ";" + diagnostic;
        }
    }

    if (!failure_detail.empty())
    {
        ::minitest::fail(__FILE__, __LINE__,
                         "PPreX1AmazeDebayerProcessingFrameDiffMatchesCpuReferenceOrSkips",
                         failure_detail);
    }
}
