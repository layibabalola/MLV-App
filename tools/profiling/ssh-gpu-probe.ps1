# ssh-gpu-probe.ps1
# Runs ON Ultra-Magnus (invoked over SSH). Tests whether this session can reach
# the RTX 4090's OpenGL for MLVApp's GPU path, or falls back to software. Runs a
# tiny GPU-flagged profile and reports the detected renderer + GPU activation.

$ErrorActionPreference = 'Continue'
$bundle = 'G:\Temp\mlv-gpu-profile'
$env:MLVAPP_PLAYBACK_SCALE_FACTOR = '1'
$env:QT_OPENGL = 'desktop'
$exe  = "$bundle\release\MLVApp.exe"
$clip = "$bundle\clips\large_dual_iso.mlv"
$rcpt = "$bundle\receipts\large_dual_iso_hq.marxml"
$json = "$bundle\results\ssh-gpu-probe.json"
New-Item -ItemType Directory -Force -Path "$bundle\results" | Out-Null

# Force MLVApp onto the high-performance GPU (the 4090) for OpenGL. On hybrid
# Intel+NVIDIA systems the GL context defaults to the iGPU; GpuPreference=2 =
# "High performance" in Windows graphics settings, which also steers GL/Vulkan.
$prefKey = 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
New-Item -Path $prefKey -Force | Out-Null
New-ItemProperty -Path $prefKey -Name $exe -Value 'GpuPreference=2;' -PropertyType String -Force | Out-Null
"set GpuPreference=2 (high-perf) for $exe"

"host: $env:COMPUTERNAME   session GPU check:"
& nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1 | ForEach-Object { "  $_" }

"`nrunning MLVApp profiler with GPU flags (6 frames) ..."
& $exe --profile-playback -i $clip -r $rcpt --frames 6 --threads auto --raw-cache-mb 0 `
      --gpu-bilinear-debayer gpu --gpu-preview-processing gpu -o $json 2>&1 | Select-Object -Last 4
"exit=$LASTEXITCODE"

if (Test-Path $json) {
    $j = Get-Content $json -Raw | ConvertFrom-Json
    $m = $j.metadata
    "`n===== RESULT ====="
    "renderer                = " + $m.gpu_bilinear_debayer_probe_renderer
    "gpu_bilinear_probe_avail= " + $m.gpu_bilinear_debayer_probe_available
    $g = ($j.frames | Where-Object { $_.requested_frame -ge 2 } | Select-Object -First 1)
    "gpu_debayer_active      = " + $g.gpu_bilinear_debayer_active
    "gpu_preview_proc_active = " + $g.gpu_preview_processing_active
    "rendered                = $($g.render_thread_rendered_width)x$($g.render_thread_rendered_height)"
    if ($m.gpu_bilinear_debayer_probe_renderer -match 'llvmpipe|software|GDI') {
        "VERDICT: SOFTWARE GL in this session - GPU GL not reachable over SSH"
    } else {
        "VERDICT: HARDWARE GL - 4090 reachable in this session"
    }
} else {
    "NO JSON produced - MLVApp likely failed to start (Qt platform plugin needs a desktop?)."
}
