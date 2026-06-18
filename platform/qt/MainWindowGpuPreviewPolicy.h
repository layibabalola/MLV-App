/*!
 * \file MainWindowGpuPreviewPolicy.h
 * \author Codex
 * \copyright 2026
 * \brief Shared GPU preview policy helpers for MainWindow and GUI tests.
 */

#ifndef MAINWINDOWGPUPREVIEWPOLICY_H
#define MAINWINDOWGPUPREVIEWPOLICY_H

#include "GpuDisplayViewport.h"
#include <Qt>

enum class GpuPreviewProcessingBackendRequest
{
    Auto = 0,
    Cpu,
    Gpu
};

enum class GpuBilinearDebayerBackendRequest
{
    Auto = 0,
    Cpu,
    Gpu
};

enum class GpuAmazeDebayerBackendRequest
{
    Auto = 0,
    Cpu,
    Gpu
};

enum class GpuPlaybackPipelineStatus
{
    Cpu = 0,
    GpuPreview,
    GpuReconReadback,
    GpuTextureReadback,
    GpuTextureNoReadback
};

struct MainWindowGpuPreviewPolicyState
{
    bool gpuViewportInstalled = false;
    GpuPreviewProcessingBackendRequest gpuPreviewProcessingBackendRequest =
        GpuPreviewProcessingBackendRequest::Auto;
    bool gpuPreviewProcessingEnvironmentRequested = false;
    bool gpuPreviewProcessingCompatible = false;
    GpuBilinearDebayerBackendRequest gpuBilinearDebayerBackendRequest =
        GpuBilinearDebayerBackendRequest::Auto;
    bool gpuBilinearDebayerEnvironmentRequested = false;
    bool gpuBilinearDebayerCompatible = false;
    GpuAmazeDebayerBackendRequest gpuAmazeDebayerBackendRequest =
        GpuAmazeDebayerBackendRequest::Auto;
    bool gpuAmazeDebayerEnvironmentRequested = false;
    bool gpuAmazeDebayerCompatible = false;
    bool gpuAmazeTexturePresentationEnvironmentRequested = false;
    bool gpuPlaybackReconEnvironmentRequested = false;
    bool gpuPlaybackReconTexturePresentationEnvironmentRequested = false;
    bool gpuPlaybackReconTexturePresentationCompatible = false;
    bool histogramEnabled = false;
    bool waveformEnabled = false;
    bool paradeEnabled = false;
    bool vectorScopeEnabled = false;
    bool renderThreadUsing16BitPreview = false;
    bool renderThreadUsingGpuProcessingPreview = false;
    bool renderThreadUsingGpuBilinearDebayer = false;
    bool renderThreadUsingGpuAmazeDebayer = false;
    bool renderThreadUsingGpuAmazeTexturePresentation = false;
    bool renderThreadUsingGpuPlaybackReconTexturePresentation = false;
    int playbackScaleFactorActive = 1;
    bool betterResizerEnabled = false;
    bool zebrasEnabled = false;
    Qt::TransformationMode transformationMode = Qt::FastTransformation;
};

inline bool mainWindowHasScopeVisualization(
    const MainWindowGpuPreviewPolicyState &state)
{
    return state.histogramEnabled
        || state.waveformEnabled
        || state.paradeEnabled
        || state.vectorScopeEnabled;
}

inline bool mainWindowAllowsGpu16PreviewRender(
    const MainWindowGpuPreviewPolicyState &state)
{
    return state.gpuViewportInstalled && !mainWindowHasScopeVisualization(state);
}

inline bool mainWindowUsesGpu16PreviewPresentation(
    const MainWindowGpuPreviewPolicyState &state)
{
    return mainWindowAllowsGpu16PreviewRender(state)
        && state.renderThreadUsing16BitPreview;
}

inline bool mainWindowAllowsGpuPreviewProcessing(
    const MainWindowGpuPreviewPolicyState &state)
{
    bool gpuPreviewProcessingRequested = false;
    switch (state.gpuPreviewProcessingBackendRequest)
    {
    case GpuPreviewProcessingBackendRequest::Auto:
        gpuPreviewProcessingRequested = state.gpuPreviewProcessingEnvironmentRequested;
        break;
    case GpuPreviewProcessingBackendRequest::Cpu:
        gpuPreviewProcessingRequested = false;
        break;
    case GpuPreviewProcessingBackendRequest::Gpu:
        gpuPreviewProcessingRequested = true;
        break;
    }

    return mainWindowAllowsGpu16PreviewRender(state)
        && gpuPreviewProcessingRequested
        && state.gpuPreviewProcessingCompatible;
}

inline bool mainWindowUsesGpuPreviewProcessing(
    const MainWindowGpuPreviewPolicyState &state)
{
    return mainWindowAllowsGpuPreviewProcessing(state)
        && mainWindowUsesGpu16PreviewPresentation(state)
        && state.renderThreadUsingGpuProcessingPreview;
}

inline bool mainWindowAllowsGpuBilinearDebayer(
    const MainWindowGpuPreviewPolicyState &state)
{
    bool gpuBilinearDebayerRequested = false;
    switch (state.gpuBilinearDebayerBackendRequest)
    {
    case GpuBilinearDebayerBackendRequest::Auto:
        gpuBilinearDebayerRequested = state.gpuBilinearDebayerEnvironmentRequested;
        break;
    case GpuBilinearDebayerBackendRequest::Cpu:
        gpuBilinearDebayerRequested = false;
        break;
    case GpuBilinearDebayerBackendRequest::Gpu:
        gpuBilinearDebayerRequested = true;
        break;
    }

    return mainWindowAllowsGpuPreviewProcessing(state)
        && gpuBilinearDebayerRequested
        && state.gpuBilinearDebayerCompatible;
}

inline bool mainWindowUsesGpuBilinearDebayer(
    const MainWindowGpuPreviewPolicyState &state)
{
    return mainWindowUsesGpuPreviewProcessing(state)
        && mainWindowAllowsGpuBilinearDebayer(state)
        && state.renderThreadUsingGpuBilinearDebayer;
}

inline bool mainWindowAllowsGpuAmazeDebayer(
    const MainWindowGpuPreviewPolicyState &state)
{
    bool gpuAmazeDebayerRequested = false;
    switch (state.gpuAmazeDebayerBackendRequest)
    {
    case GpuAmazeDebayerBackendRequest::Auto:
        gpuAmazeDebayerRequested = state.gpuAmazeDebayerEnvironmentRequested;
        break;
    case GpuAmazeDebayerBackendRequest::Cpu:
        gpuAmazeDebayerRequested = false;
        break;
    case GpuAmazeDebayerBackendRequest::Gpu:
        gpuAmazeDebayerRequested = true;
        break;
    }

    return mainWindowAllowsGpuPreviewProcessing(state)
        && gpuAmazeDebayerRequested
        && state.gpuAmazeDebayerCompatible;
}

inline bool mainWindowUsesGpuAmazeDebayer(
    const MainWindowGpuPreviewPolicyState &state)
{
    return mainWindowUsesGpuPreviewProcessing(state)
        && mainWindowAllowsGpuAmazeDebayer(state)
        && state.renderThreadUsingGpuAmazeDebayer;
}

inline bool mainWindowAllowsGpuAmazeTexturePresentation(
    const MainWindowGpuPreviewPolicyState &state)
{
    return mainWindowUsesGpuAmazeDebayer(state)
        && state.gpuAmazeTexturePresentationEnvironmentRequested;
}

inline bool mainWindowUsesGpuAmazeTexturePresentation(
    const MainWindowGpuPreviewPolicyState &state)
{
    return mainWindowAllowsGpuAmazeTexturePresentation(state)
        && state.renderThreadUsingGpuAmazeTexturePresentation;
}

inline bool mainWindowAllowsGpuPlaybackReconTexturePresentation(
    const MainWindowGpuPreviewPolicyState &state)
{
    return mainWindowAllowsGpu16PreviewRender(state)
        && state.gpuPlaybackReconEnvironmentRequested
        && state.gpuPlaybackReconTexturePresentationEnvironmentRequested
        && state.gpuPlaybackReconTexturePresentationCompatible;
}

inline bool mainWindowUsesGpuPlaybackReconTexturePresentation(
    const MainWindowGpuPreviewPolicyState &state)
{
    return mainWindowAllowsGpuPlaybackReconTexturePresentation(state)
        && state.renderThreadUsingGpuPlaybackReconTexturePresentation;
}

inline bool mainWindowUsesGpuImagePresentation(
    const MainWindowGpuPreviewPolicyState &state)
{
    return state.gpuViewportInstalled
        && !mainWindowUsesGpu16PreviewPresentation(state);
}

inline GpuPlaybackPipelineStatus mainWindowGpuPlaybackPipelineStatus(
    const MainWindowGpuPreviewPolicyState &state,
    bool gpuPlaybackReconUsed,
    bool gpuPlaybackReconTexturePresentActive,
    bool gpuPlaybackReconTexturePresentNoReadbackActive)
{
    if (gpuPlaybackReconTexturePresentActive)
    {
        return gpuPlaybackReconTexturePresentNoReadbackActive
            ? GpuPlaybackPipelineStatus::GpuTextureNoReadback
            : GpuPlaybackPipelineStatus::GpuTextureReadback;
    }
    if (gpuPlaybackReconUsed)
    {
        return GpuPlaybackPipelineStatus::GpuReconReadback;
    }
    if (mainWindowUsesGpuPreviewProcessing(state)
     || mainWindowUsesGpuBilinearDebayer(state)
     || mainWindowUsesGpuAmazeDebayer(state))
    {
        return GpuPlaybackPipelineStatus::GpuPreview;
    }
    return GpuPlaybackPipelineStatus::Cpu;
}

inline const char *mainWindowGpuPlaybackPipelineStatusToken(
    GpuPlaybackPipelineStatus status)
{
    switch (status)
    {
    case GpuPlaybackPipelineStatus::Cpu:
        return "cpu";
    case GpuPlaybackPipelineStatus::GpuPreview:
        return "gpu_preview";
    case GpuPlaybackPipelineStatus::GpuReconReadback:
        return "gpu_recon_readback";
    case GpuPlaybackPipelineStatus::GpuTextureReadback:
        return "gpu_texture_readback";
    case GpuPlaybackPipelineStatus::GpuTextureNoReadback:
        return "gpu_texture_no_readback";
    }
    return "cpu";
}

inline const char *mainWindowGpuPlaybackPipelineStatusLabel(
    GpuPlaybackPipelineStatus status)
{
    switch (status)
    {
    case GpuPlaybackPipelineStatus::Cpu:
        return "CPU";
    case GpuPlaybackPipelineStatus::GpuPreview:
        return "GPU Preview";
    case GpuPlaybackPipelineStatus::GpuReconReadback:
        return "GPU RB";
    case GpuPlaybackPipelineStatus::GpuTextureReadback:
        return "GPU Tex RB";
    case GpuPlaybackPipelineStatus::GpuTextureNoReadback:
        return "GPU Tex NR";
    }
    return "CPU";
}

inline bool mainWindowUsesGpuShaderZebraProcessing(
    const MainWindowGpuPreviewPolicyState &state)
{
    return state.gpuViewportInstalled && state.zebrasEnabled;
}

inline GpuDisplayViewport::PresentationOptions mainWindowBuildGpuPresentationOptions(
    const MainWindowGpuPreviewPolicyState &state)
{
    GpuDisplayViewport::PresentationOptions options;
    options.showZebras = state.zebrasEnabled;

    if (!state.gpuViewportInstalled)
    {
        return options;
    }

    if (state.transformationMode == Qt::FastTransformation)
    {
        options.samplingMode =
            (state.playbackScaleFactorActive >= 8)
                ? GpuDisplayViewport::SamplingBicubic
                : GpuDisplayViewport::SamplingNearest;
    }
    else if (state.betterResizerEnabled)
    {
        options.samplingMode = GpuDisplayViewport::SamplingBicubic;
    }
    else
    {
        options.samplingMode = GpuDisplayViewport::SamplingLinear;
    }

    return options;
}

#endif // MAINWINDOWGPUPREVIEWPOLICY_H
