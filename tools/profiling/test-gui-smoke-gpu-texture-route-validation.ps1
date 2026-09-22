$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'gui-smoke-gpu-texture-route-validation.ps1')

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function New-GpuSummary([long]$GpuReconReadbackFrames) {
    [pscustomobject]@{
        session = 1L
        cpu_frames = 0L
        gpu_preview_frames = 0L
        gpu_recon_readback_frames = $GpuReconReadbackFrames
        gpu_texture_readback_frames = 0L
        gpu_texture_no_readback_frames = 0L
    }
}

function New-LaunchEnv([hashtable]$Overrides = @{}) {
    $env = [ordered]@{}
    foreach ($key in $Overrides.Keys) { $env[$key] = $Overrides[$key] }
    $env
}

# --- Test-GuiSmokeEnvironmentPairTruthy -------------------------------------

Assert-True (-not (Test-GuiSmokeEnvironmentPairTruthy -Env (New-LaunchEnv) -Key 'MLVAPP_GPU_PLAYBACK_RECON')) `
    'An unset key must not be truthy.'
Assert-True (-not (Test-GuiSmokeEnvironmentPairTruthy -Env (New-LaunchEnv @{ MLVAPP_GPU_PLAYBACK_RECON = '0' }) -Key 'MLVAPP_GPU_PLAYBACK_RECON')) `
    '"0" must not be truthy.'
Assert-True (-not (Test-GuiSmokeEnvironmentPairTruthy -Env (New-LaunchEnv @{ MLVAPP_GPU_PLAYBACK_RECON = 'FALSE' }) -Key 'MLVAPP_GPU_PLAYBACK_RECON')) `
    '"FALSE" (any case) must not be truthy.'
Assert-True (-not (Test-GuiSmokeEnvironmentPairTruthy -Env (New-LaunchEnv @{ MLVAPP_GPU_PLAYBACK_RECON = '  ' }) -Key 'MLVAPP_GPU_PLAYBACK_RECON')) `
    'Whitespace-only must not be truthy.'
Assert-True (Test-GuiSmokeEnvironmentPairTruthy -Env (New-LaunchEnv @{ MLVAPP_GPU_PLAYBACK_RECON = '1' }) -Key 'MLVAPP_GPU_PLAYBACK_RECON') `
    '"1" must be truthy.'
Assert-True (Test-GuiSmokeEnvironmentPairTruthy -Env (New-LaunchEnv @{ MLVAPP_GPU_PLAYBACK_RECON = 'cuda' }) -Key 'MLVAPP_GPU_PLAYBACK_RECON') `
    'A non-empty, non-"0"/"false" value must be truthy.'

# --- Test-GuiSmokeGpuReconTexturePresentRouteRequested ----------------------

Assert-True (-not (Test-GuiSmokeGpuReconTexturePresentRouteRequested -LaunchEnv (New-LaunchEnv))) `
    'Neither flag set: route not requested.'
Assert-True (-not (Test-GuiSmokeGpuReconTexturePresentRouteRequested -LaunchEnv (New-LaunchEnv @{
    MLVAPP_GPU_PLAYBACK_RECON = '1'
}))) 'Recon alone (no texture-present flag): route not requested.'
Assert-True (-not (Test-GuiSmokeGpuReconTexturePresentRouteRequested -LaunchEnv (New-LaunchEnv @{
    MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT = '1'
}))) 'Texture-present alone (no recon flag): route not requested.'
Assert-True (Test-GuiSmokeGpuReconTexturePresentRouteRequested -LaunchEnv (New-LaunchEnv @{
    MLVAPP_GPU_PLAYBACK_RECON = '1'
    MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT = '1'
})) 'Both flags set: route requested.'

# --- Get-GuiSmokeGpuTextureRouteReadbackRegressionFailure -------------------
# This is the GPU_S4 bachelor-job shape: MLVAPP_GPU_PLAYBACK_RECON=1,
# MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1, -ScaleFactor 4.

$routeRequestedEnv = New-LaunchEnv @{
    MLVAPP_GPU_PLAYBACK_RECON = '1'
    MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT = '1'
}

# Route not requested at all: never fails, regardless of what the (irrelevant)
# summary says.
Assert-True ($null -eq (Get-GuiSmokeGpuTextureRouteReadbackRegressionFailure `
    -LaunchEnv (New-LaunchEnv) -GpuSummary (New-GpuSummary 5))) `
    'Route not requested: must not fail even with recon-readback frames present.'
Assert-True ($null -eq (Get-GuiSmokeGpuTextureRouteReadbackRegressionFailure `
    -LaunchEnv (New-LaunchEnv) -GpuSummary $null)) `
    'Route not requested: must not fail even with no gpu_summary telemetry.'

# Route requested, every frame reached the no-readback texture path: passes.
Assert-True ($null -eq (Get-GuiSmokeGpuTextureRouteReadbackRegressionFailure `
    -LaunchEnv $routeRequestedEnv -GpuSummary (New-GpuSummary 0))) `
    'Route requested with zero recon-readback frames must pass.'

# Route requested, this is the exact regression the clamp exists to prevent:
# scale 4 silently rerouted every frame through gpu_recon_readback.
$regressed = Get-GuiSmokeGpuTextureRouteReadbackRegressionFailure `
    -LaunchEnv $routeRequestedEnv -GpuSummary (New-GpuSummary 16)
Assert-True ($null -ne $regressed) `
    'Route requested with 16 recon-readback frames must fail.'
Assert-True ($regressed -match '^gpu_texture_route_readback_regression:') `
    "Failure must carry the named token; observed: $regressed"
Assert-True ($regressed -match '16 frame\(s\)') `
    "Failure must report the offending frame count; observed: $regressed"

# Route requested, even a single fallback frame must fail -- "every frame".
$oneFrameRegressed = Get-GuiSmokeGpuTextureRouteReadbackRegressionFailure `
    -LaunchEnv $routeRequestedEnv -GpuSummary (New-GpuSummary 1)
Assert-True ($null -ne $oneFrameRegressed) `
    'Route requested with a single recon-readback frame must fail.'

# Route requested but no gpu_summary telemetry was logged at all: loud, named failure.
$missingSummary = Get-GuiSmokeGpuTextureRouteReadbackRegressionFailure `
    -LaunchEnv $routeRequestedEnv -GpuSummary $null
Assert-True ($null -ne $missingSummary) `
    'Route requested with no gpu_summary telemetry must fail.'
Assert-True ($missingSummary -match '^gpu_texture_route_readback_regression:') `
    "Missing-telemetry failure must carry the named token; observed: $missingSummary"

Write-Host 'PASS: GUI smoke GPU texture-route scale-clamp validation tests'
