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
    bool histogramEnabled = false;
    bool waveformEnabled = false;
    bool paradeEnabled = false;
    bool vectorScopeEnabled = false;
    bool renderThreadUsing16BitPreview = false;
    bool renderThreadUsingGpuProcessingPreview = false;
    bool renderThreadUsingGpuBilinearDebayer = false;
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

inline bool mainWindowUsesGpuImagePresentation(
    const MainWindowGpuPreviewPolicyState &state)
{
    return state.gpuViewportInstalled
        && !mainWindowUsesGpu16PreviewPresentation(state);
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
        options.samplingMode = GpuDisplayViewport::SamplingNearest;
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
