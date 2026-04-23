#ifndef MLV_APP_BACKEND_PARAMETRIC_FIXTURE_H
#define MLV_APP_BACKEND_PARAMETRIC_FIXTURE_H

#include "mlv_pipeline_fixture.h"

#include "../../platform/qt/GpuPreviewProcessing.h"

#include <QString>
#include <cstdint>
#include <vector>

/*
 * BackendParametricFixture - the thin harness used by the GPU-phase rollout.
 *
 * This class extends MlvPipelineFixture with a Backend enumeration and
 * associated skip-gate probes. Tests that want to run the same pipeline
 * stage through a CPU reference and a GPU offscreen path both go through
 * this fixture so the surrounding scaffolding (clip opening, receipt apply,
 * single-threaded runtime, teardown) is shared.
 *
 * Current slices:
 *   - Preview-processing subset:
 *     CPU reference + real offscreen GPU execution or runtime skip.
 *   - Debayer shell:
 *     CPU bilinear/AMaZE execution + real bilinear GPU execution or runtime
 *     skip. AMaZE remains an explicit skip-only tripwire until a second
 *     backend lands.
 *
 * Later slices will:
 *   - Replace the debayer GPU probe with a real backend check plus parity
 *     execution.
 *   - Extend the same pattern to later stages (for example Dual ISO full20bit)
 *     once the debayer backend is real.
 */

class BackendParametricFixture : public MlvPipelineFixture
{
public:
    enum class Backend
    {
        Cpu,
        Gpu
    };

    enum class DebayerMode
    {
        Bilinear,
        Amaze
    };

    struct BackendAvailability
    {
        bool available;
        QString reason;
        QString rendererDescription;
    };

    /*
     * Human-readable name used when formatting test_artifact keys such as
     *   tiny_dual_iso.preview_processing.{cpu|gpu}.frameN
     * Keep the return value in sync with the fixture hash-key schema in
     * .claude/analysis/gpu-phase-design.md.
     */
    static const char * backendName(Backend backend);
    static const char * debayerModeName(DebayerMode mode);

    /*
     * Stage 1 skip-gate. Returns {true, ""} when the backend can execute the
     * preview-processing subset on this build, and {false, reason} otherwise.
     *
     * CPU is always available. GPU now probes the offscreen OpenGL execution
     * path and reports a renderer string on success or a concrete skip reason
     * on unsupported/software-only environments.
     */
    static BackendAvailability probeBackend(Backend backend);
    static BackendAvailability probeDebayerBackend(Backend backend,
                                                   DebayerMode mode);

    /*
     * Runs the preview-processing subset for the specified backend. The
     * caller is responsible for having applied a receipt and built a
     * GpuPreviewProcessingConfig already. Returns an RGB uint16 frame with
     * 3 interleaved samples per pixel.
     *
     * If the backend is not available (see probeBackend), or if the runtime
     * execution fails, the returned vector is empty and error_message is
     * populated; callers should probe first so unsupported environments
     * surface as SKIP_TEST rather than as empty-vector mis-assertions.
     */
    std::vector<uint16_t> renderPreviewProcessingSubset(Backend backend,
                                                        const GpuPreviewProcessingConfig & config,
                                                        uint64_t frame_index,
                                                        QString * error_message = nullptr) const;

    /*
     * Runs the debayer stage for the specified backend and algorithm variant.
     * CPU executes through the existing video_mlv/frame_caching path with an
     * explicit debayer-mode override, so the returned frame is a real debayered
     * RGB16 output rather than a synthetic oracle.
     *
     * Bilinear GPU debayer now uses the same offscreen executor as the
     * experimental production preview path, while AMaZE intentionally remains
     * unavailable so the next backend flip is explicit in review.
     */
    std::vector<uint16_t> renderDebayeredFrame(Backend backend,
                                               DebayerMode mode,
                                               uint64_t frame_index,
                                               QString * error_message = nullptr) const;
};

#endif
