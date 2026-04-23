#include "backend_parametric_fixture.h"

#include "../../platform/qt/GpuDebayer.h"
#include "../../src/mlv/macros.h"

#include <cstddef>

namespace
{
int debayer_mode_override_value(BackendParametricFixture::DebayerMode mode)
{
    switch (mode)
    {
    case BackendParametricFixture::DebayerMode::Bilinear:
        return 0;
    case BackendParametricFixture::DebayerMode::Amaze:
        return 1;
    }
    return 0;
}

class ScopedDebayerModeOverride
{
public:
    ScopedDebayerModeOverride(mlvObject_t * video, int forced_mode)
        : m_video(video)
        , m_saved_mode(video ? video->use_amaze : 0)
    {
        if (!m_video) return;
        m_video->use_amaze = forced_mode;
        resetMlvCachedFrame(m_video);
    }

    ~ScopedDebayerModeOverride()
    {
        if (!m_video) return;
        m_video->use_amaze = m_saved_mode;
        resetMlvCachedFrame(m_video);
    }

private:
    mlvObject_t * m_video;
    int m_saved_mode;
};
}

const char * BackendParametricFixture::backendName(Backend backend)
{
    switch (backend)
    {
    case Backend::Cpu:
        return "cpu";
    case Backend::Gpu:
        return "gpu";
    }
    return "unknown";
}

const char * BackendParametricFixture::debayerModeName(DebayerMode mode)
{
    switch (mode)
    {
    case DebayerMode::Bilinear:
        return "bilinear";
    case DebayerMode::Amaze:
        return "amaze";
    }
    return "unknown";
}

BackendParametricFixture::BackendAvailability BackendParametricFixture::probeBackend(Backend backend)
{
    switch (backend)
    {
    case Backend::Cpu:
        return {true, QString(), QString()};
    case Backend::Gpu:
    {
        const GpuPreviewProcessingBackendAvailability availability =
            gpuPreviewProcessingProbeGpuBackend();
        return {availability.available,
                availability.reason,
                availability.rendererDescription};
    }
    }
    return {false, QStringLiteral("unknown backend enumerator"), QString()};
}

BackendParametricFixture::BackendAvailability BackendParametricFixture::probeDebayerBackend(
    Backend backend,
    DebayerMode mode)
{
    switch (backend)
    {
    case Backend::Cpu:
        return {true, QString(), QString()};
    case Backend::Gpu:
        if (mode != DebayerMode::Bilinear)
        {
            return {false,
                    QStringLiteral("not yet implemented: GPU debayer backend currently supports bilinear only"),
                    QString()};
        }
        {
            const GpuBilinearDebayerBackendAvailability availability =
                gpuBilinearDebayerProbeBackend();
            return {availability.available,
                    availability.reason,
                    availability.rendererDescription};
        }
    }
    return {false, QStringLiteral("unknown backend enumerator"), QString()};
}

std::vector<uint16_t> BackendParametricFixture::renderPreviewProcessingSubset(
    Backend backend,
    const GpuPreviewProcessingConfig & config,
    uint64_t frame_index,
    QString * error_message) const
{
    const BackendAvailability availability = probeBackend(backend);
    if (!availability.available)
    {
        if (error_message)
        {
            *error_message = availability.reason;
        }
        return std::vector<uint16_t>();
    }

    if (!config.enabled)
    {
        if (error_message)
        {
            *error_message = QStringLiteral(
                "GpuPreviewProcessingConfig is not enabled; caller must build a "
                "valid config via gpuPreviewProcessingBuildConfig before invoking "
                "renderPreviewProcessingSubset.");
        }
        return std::vector<uint16_t>();
    }

    const std::vector<uint16_t> debayered = renderDebayeredFrame16(frame_index);
    if (debayered.empty())
    {
        if (error_message)
        {
            *error_message = QStringLiteral(
                "Debayered frame is empty; check clip open / receipt apply.");
        }
        return std::vector<uint16_t>();
    }

    std::vector<uint16_t> output(debayered.size(), 0);
    const int pixel_count = width() * height();

    switch (backend)
    {
    case Backend::Cpu:
        gpuPreviewProcessingApplyCpuReference(config,
                                              debayered.data(),
                                              output.data(),
                                              pixel_count);
        return output;

    case Backend::Gpu:
        if (!gpuPreviewProcessingApplyGpuOffscreen(config,
                                                   debayered.data(),
                                                   output.data(),
                                                   width(),
                                                   height(),
                                                   error_message))
        {
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

std::vector<uint16_t> BackendParametricFixture::renderDebayeredFrame(
    Backend backend,
    DebayerMode mode,
    uint64_t frame_index,
    QString * error_message) const
{
    const BackendAvailability availability = probeDebayerBackend(backend, mode);
    if (!availability.available)
    {
        if (error_message)
        {
            *error_message = availability.reason;
        }
        return std::vector<uint16_t>();
    }

    switch (backend)
    {
    case Backend::Cpu:
    {
        ScopedDebayerModeOverride mode_override(
            video(),
            debayer_mode_override_value(mode));
        return renderDebayeredFrame16(frame_index);
    }
    case Backend::Gpu:
        if (mode != DebayerMode::Bilinear)
        {
            if (error_message)
            {
                *error_message = QStringLiteral(
                    "not yet implemented: GPU debayer backend currently supports bilinear only");
            }
            return std::vector<uint16_t>();
        }

        {
            const std::vector<float> raw = renderRawFrameFloat(frame_index);
            if (raw.empty())
            {
                if (error_message)
                {
                    *error_message = QStringLiteral(
                        "Raw frame is empty; check clip open / receipt apply.");
                }
                return std::vector<uint16_t>();
            }

            std::vector<uint16_t> output(
                static_cast<std::size_t>(width()) *
                static_cast<std::size_t>(height()) * 3u,
                0);
            if (!gpuBilinearDebayerApplyGpuOffscreen(raw.data(),
                                                     output.data(),
                                                     width(),
                                                     height(),
                                                     error_message))
            {
                return std::vector<uint16_t>();
            }
            return output;
        }
    }

    if (error_message)
    {
        *error_message = QStringLiteral("unknown backend enumerator");
    }
    return std::vector<uint16_t>();
}
