#include "../common/minitest.h"
#include "../common/frame_compare.h"
#include "../common/hash_helpers.h"
#include "../common/test_artifacts.h"

#include "backend_parametric_fixture.h"

#include <QString>
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
