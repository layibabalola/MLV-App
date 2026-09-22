# CUDA-S4-TEXTURE-ROUTE-CLAMP-1: the GPU recon texture-present route (CUDA
# reconstruction straight to a GL texture, no per-frame CPU readback) is only
# wired for playbackScaleFactor == 1. MainWindow::effectivePlaybackScaleFactorForRequest()
# clamps the effective playback scale to 1 at that single policy source
# whenever the route would otherwise be armed, so a launch that requests the
# route (in GL-window mode -- this rig has no separate CPU-only-viewport
# switch, so requesting the route at all is the GL-window-mode signal) must
# never see a gpu_recon_readback fallback frame in playback_smoke.gpu_summary.

function Get-GuiSmokeGpuTextureRouteObjectPropertyValue {
    param(
        [object]$Object,
        [string]$Name
    )
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    $null
}

# Mirrors the C++ *_requested_by_environment() truthiness check in
# MainWindow.cpp: unset/empty, "0", and "false" (case-insensitive) are all
# "not requested".
function Test-GuiSmokeEnvironmentPairTruthy {
    param($Env, [string]$Key)
    if ($null -eq $Env -or -not $Env.Contains($Key)) { return $false }
    $value = [string]$Env[$Key]
    return -not [string]::IsNullOrWhiteSpace($value) -and
        $value -ne "0" -and
        $value.ToLowerInvariant() -ne "false"
}

function Test-GuiSmokeGpuReconTexturePresentRouteRequested {
    param($LaunchEnv)
    return (Test-GuiSmokeEnvironmentPairTruthy -Env $LaunchEnv -Key "MLVAPP_GPU_PLAYBACK_RECON") -and
        (Test-GuiSmokeEnvironmentPairTruthy -Env $LaunchEnv -Key "MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT")
}

# Returns a validation-failure string when the launch requested the GPU
# recon texture-present route but the run's playback_smoke.gpu_summary shows
# at least one frame that fell back to the CPU-readback gpu_recon_readback
# path (or the summary telemetry is missing outright); returns $null when
# the route was not requested, or was requested and honored cleanly.
function Get-GuiSmokeGpuTextureRouteReadbackRegressionFailure {
    param($LaunchEnv, $GpuSummary)

    if (-not (Test-GuiSmokeGpuReconTexturePresentRouteRequested -LaunchEnv $LaunchEnv)) {
        return $null
    }
    if ($null -eq $GpuSummary) {
        return "gpu_texture_route_readback_regression: the launch requested the GPU recon " +
               "texture-present route (MLVAPP_GPU_PLAYBACK_RECON + " +
               "MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT) but no " +
               "playback_smoke.gpu_summary telemetry was logged."
    }
    $frames = [long](Get-GuiSmokeGpuTextureRouteObjectPropertyValue $GpuSummary "gpu_recon_readback_frames")
    if ($frames -gt 0) {
        return "gpu_texture_route_readback_regression: $frames frame(s) fell back to " +
               "gpu_recon_readback (CPU-readback CUDA reconstruction) while the GPU recon " +
               "texture-present no-readback route was requested in GL-window mode; every " +
               "frame must reach gpu_texture_no_readback."
    }
    return $null
}
