# 2026-06-18 - GPU Lane P3 no-readback validation belongs on UltraMagnus

### Verified locally

- The current VM host is `Virtual-Ten`, with `VMware SVGA 3D`; `nvidia-smi` is unavailable. This environment can validate build correctness, Qt/runtime wrapper behavior, policy/UI wiring, telemetry shape, fail-closed CPU fallback, and handoff automation, but it cannot authoritatively validate CUDA/GL interop or the real P3 no-readback win.
- For Lane B P3, do not claim `texture_no_readback_active=1`, x1 realtime improvement, CUDA-to-GL interop success, or pixel-validated no-readback presentation from VM-local runs.
- The real P3 validation host is UltraMagnus with the NVIDIA/4090 stack and local MLV files. MLV clip paths used by the standard smoke set should be resolved on UltraMagnus, not assumed to exist on `Virtual-Ten`.
- Canonical LAN access from this VM is the hyphenated SMB endpoint `\\ultra-magnus\G\Temp`. Do not treat the bare/no-hyphen `UltraMagnus` alias as authoritative; if that spelling fails DNS, probe `ultra-magnus` and the UNC share before reporting the host unreachable. On 2026-06-18, `ultra-magnus` resolved to `10.0.0.194`, `\\ultra-magnus\G\Temp` was readable, and the live file-drop agent heartbeat was `\\ultra-magnus\G\Temp\mlv-gpu-profile\agent\heartbeat.txt` with `host=ULTRA-MAGNUS`.
- WinRM is not the required automation path for this lane. From `Virtual-Ten`, SMB and ping worked while `Test-WSMan -ComputerName ultra-magnus` failed with a WinRM/firewall fault. Use the SMB file-drop mailbox `\\ultra-magnus\G\Temp\mlv-gpu-profile\agent\{inbox,outbox,processed,logs}` for unattended UltraMagnus work unless a newer repo-local note proves WinRM/SSH is healthier.
- As of 2026-06-18, the verified remote M16 clip path is `\\ultra-magnus\G\Temp\mlv-gpu-profile\clips\M16-1327.MLV`, which maps to `G:\Temp\mlv-gpu-profile\clips\M16-1327.MLV` for jobs running on UltraMagnus. Do not use the VM-local default `C:\temp\MLV` for the SMB-agent path unless a fresh remote check proves that path exists on UltraMagnus.
- Look Assist must be disabled through GUI/receipt smoke controls for P3 validation. Do not use pre-window `MLVAPP_NO_LOOK_ASSIST`; that path caused early GUI startup access violations during VM smoke investigation.
- 2026-06-18 13:59 CDT pause/resume snapshot: active branch `codex/work-block/wb-80ccdd666b074de2` had uncommitted F1/F2/F3 validation work in `platform/qt/GpuDisplayViewport.*`, `platform/qt/MainWindow.cpp`, `src/mlv/llrawproc/llrawproc.*`, and `tools/profiling/{detect-playback-artifacts.ps1,run-release-gui-smoke.ps1,run-ultramagnus-p3-validation.ps1}`. The release build was attempted and failed in `src/mlv/llrawproc/llrawproc.c` because `llrpGpuPlaybackReconGetBackendInfo` was inserted before the Windows backend struct/mutex declarations (`llrawprocGpuExportBackend_t`, `g_llrawproc_gpu_export_backend`, `g_llrawproc_gpu_recon_backend_mutex`). Move that function below the backend declarations or add correct forward declarations before resuming validation.

### Cross-checked from prior analysis

- P1/P2 CPU-readback proof can be reviewed on the VM if the backend/test harness is present, but P3's intended gain is specifically no-readback CUDA-to-GL texture presentation, so it needs the real NVIDIA/OpenGL stack.
- VM-local P3 smoke may still be useful when it proves `texture_requested=1`, `texture_candidate=1`, fail-closed fallback, and telemetry parsing, but that remains a plumbing check rather than P3 success.

### Needs runtime profiling

- Run unattended UltraMagnus validation against the user-facing release executable with:
  - `MLVAPP_GPU_PLAYBACK_RECON=1`
  - `MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1`
  - Phase 3 quality enabled for unattended smoke when needed
  - Per-frame telemetry showing `texture_requested=1`, `texture_candidate=1`, `texture_no_readback_active=1`, and `texture_source=cuda_gl_r16_texture`
  - Screenshot/pixel validation when claiming visual correctness
- Use `tools\profiling\run-ultramagnus-p3-validation.ps1` for the no-human-loop UltraMagnus run once this branch lands there. The runner is expected to fail closed off UltraMagnus or without the required NVIDIA GPU unless explicitly dry-run/override flags are supplied for plumbing checks.

# 2026-06-09 - real "flicker to first frame" found + fixed (forward-only present guard); built a headless temporal-artifact detector

### Verified locally

- The user-reported "playback flickering to first frame / frame not matching the seek bar" is a REAL, pre-existing bug (reproduced on the clean master build, not any experimental change). It is a TEMPORAL artifact that single-frame screenshot smoke is blind to.
- Built a headless detector, now a tracked tool: [`tools\profiling\detect-playback-artifacts.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/profiling/detect-playback-artifacts.ps1). Run any smoke with `-ExtraEnvironment 'MLVAPP_INTERACTIVE_TRACE=1'`, then run the detector on `logs/mlvapp-*.log`. It parses `draw_frame_ready.begin display_frame=N position=M` per present and flags (a) the displayed frame lagging the seek bar (stall) and (b) the presented frame jumping BACKWARD while the seek bar is still ahead (flicker). It is position-aware, so legitimate loop-wrap / scrub rewinds are NOT flagged. On a master x2 M16-1327 run it returns `FAIL max_lag=33 (~1.4 s stall) max_back_jump=30 flicker_back_jumps=11`.
- ROOT CAUSE: the render-present pipeline presents frames out of order. `findLatestReadySlotLocked` ([`platform/qt/RenderFrameThread.cpp`](C:/!Layi%20Wkspc/MLV-App/platform/qt/RenderFrameThread.cpp) line 1400) correctly picks the highest `requestSerial`, so a backward-jumping `display_frame` means a higher-serial render REQUEST carried a LOWER frame number - the playback position briefly moved backward (DropFrameMode/audio-sync/re-request during a stall) and the pipeline faithfully presented the older frame.
- FIX: forward-only present guard in `drawFrameReady` ([`platform/qt/MainWindow.cpp`](C:/!Layi%20Wkspc/MLV-App/platform/qt/MainWindow.cpp)): while actively playing, drop any acquired frame whose number is behind the last one shown WHEN the seek bar is still at/ahead of it (a stale out-of-order render); loop-wrap and scrub move the position itself backward so they pass; the guard resets on display-change generation bumps and is paused/scrub-safe. New member `m_lastPresentedPlaybackFrame` in [`platform/qt/MainWindow.h`](C:/!Layi%20Wkspc/MLV-App/platform/qt/MainWindow.h).
- Rebuilt the release tree:
  - `LastWriteTime=6/8/2026 11:43:08 PM`, `Length=9115648`, `SHA256=0F63950952A0D6B8CB132E6933A5170DB676C71F38DE8F474C10240340E36E3A`.
- Validation: the guard build is clean across 10+ traced runs on x1/x2/x8 (`PASS`, `flicker_back_jumps=0`, no stalls), the x8 canary presented frame is visually clean, and the detector still catches the master flicker after the position-aware update. The guard is pixel-neutral.

### Cross-checked from prior analysis

- The flicker is RARE / transient (it correlated with a stalled run; it did not reproduce on the guard build even under screenshot/disk load, so the guard's drop path was not directly observed firing). The guard is correct by construction and harmless, with the detector as the standing headless gate; interactive playback is the final confirmation.
- METHODOLOGY: temporal playback artifacts (flicker, stutter, out-of-order frames) require the trace + `detect-playback-artifacts.ps1`, not single-frame screenshots. Run it on every GUI-affecting playback change.

### Needs runtime profiling

- The ~1.4 s stall (`max_lag` spike) is the remaining half: a transient decode/cache/disk hitch (also seen as `max_present_interval` ~830-1100 ms). Investigate next; the detector already flags it as `STALL`.
- Consider integrating the trace + detector directly into `run-release-gui-smoke.ps1` so the temporal-artifact gate runs automatically.

# 2026-06-09 - conclusive playback ceiling: x2/x4/x8 are already real-time, x1 is compute-bound with no safe lever, and the "present bottleneck" was a measurement artifact

### Verified locally

- Measurement methodology correction (this changes how every prior FPS number should be read):
  - Screenshot-backed smoke distorts FPS. Same-session A/B on `M16-1347` (default threads): x2 ~24.6 fps no-screenshot vs ~25.0 fps with-screenshot (render 7.7 -> 14.8 ms, but x2 has budget headroom so FPS holds); x1 8.30 fps no-screenshot vs 6.71 fps with-screenshot (-19%, no headroom). Use screenshots only for visual/canary; measure FPS with `-FrameTelemetry` and NO `-CaptureScreenshot`.
  - Single-run/single-clip A/B is unreliable. A one-clip M16-1327 "+12.6% playback thread-cap win" was a transient anomaly (that t16 run had 27 skips + elevated render); the full trio debunked it (M16-1347/1446 x2 t16 >= t12, 0 skips). No thread-cap change made; `mlvappDefaultPlaybackWorkerThreadCapFor()` left as-is.
- In real (no-screenshot) playback, **x2/x4/x8 already run at the clip native rate (~24-26 fps = real-time)**. They are not bottlenecked. The earlier "x2 = ~15 fps / ~29 ms present overhead" was a high-load profiling session (x2 render was 34 ms that day vs ~8 ms on a quiet session) plus screenshot overhead. `present_pacing` is largely real-time pacing residual (derived as `interval - render - measured-present-components` at `MainWindow.cpp:16752`), not a fixable defect; the render->UI->prep-worker->UI pipeline is a deliberately race-engineered concurrency system, not the cause of the apparent gap.
- **x1 (full resolution) is the only sub-real-time lane** (~8.6 fps, `render_total` ~88-104 ms). It has no safe FPS lever, proven multiple ways:
  - The half-res preview-recon machinery (`phase4bv2/v3/v4`) excludes `scaleFactor <= 1` by design at [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) line 3695 - x1 output is full-res, so there is nothing to downsample-then-upsample.
  - The debayer already uses the fast basic AVX2 path: `avg_debayer_exclusive_ms ~6.5`, `debayer_engine_mode_last=0`, 185/185 frames AVX2 (not AMaZE).
  - OpenMP threads are already maxed (16, `openmp_thread_cap_active_last=0`); capping does not help.
  - No usable GPU in this environment (backend tests skip on llvmpipe software raster).
  - The dominant x1 costs - dual-ISO recon ~24 ms (`llrawproc_dual_iso`) and colour ~21 ms (`processing_core`) - are full-res per-pixel and irreducible without an algorithm/quality change.
- "Glitchy / not rendering properly" at 1x = this choppiness (~2/3 of frames dropped to hold timeline sync), plus a one-time ~0.85 s startup stall (only a single present interval >=500 ms per run). It is NOT pixel corruption: captured 1x/2x frames are clean.
- The landed x1 prefetch-drop change is exonerated as a cause of any glitch: the foreground render syncs black/white levels unconditionally at `video_mlv.c:5056` (the sync I bypassed at 5195 was on the now-disabled prefetch path x1 never renders from), and the cache key is provably identical (`mlv_processed_frame_signature_with_scale` is defined as `from_state(state_signature_with_scale(...), frameIndex)`).

### Cross-checked from prior analysis

- The only remaining x1 speedup is a deliberate faster/lower-quality PREVIEW recon (softer while playing, sharp when paused) - a speed/quality tradeoff in the fragile, contested Phase4Bv2/v3 dual-ISO code whose guardrail tests already fail. "Faster x1" and "no quality regression" are mutually exclusive; this needs explicit user buy-in and is not a smallest-safe change.
- For smooth playback today, 2x is already real-time. The autonomous smallest-safe-change loop has reached its boundary on the priority lanes.

### Needs runtime profiling

- If the x1 preview-quality tradeoff is approved, validate it no-screenshot (FPS) plus a separate screenshot pass (visual/canary), compare before/after 1x frames for softness, and keep the x8 canary in the loop.
- Re-confirm the landed x1 prefetch-drop magnitude with averaged no-screenshot A/B (it was measured with screenshots, which inflate the contention the change relieves; the win is real but the +55-66% figure is screenshot-measured).

# 2026-06-09 - dropping the inert x1 processed8 prefetch worker is a large keeper-safe x1 win (+55-66%)

### Verified locally

- Next-bottleneck decision (processing tail vs disk I/O): chose the **shared processing tail**. Per-stage telemetry shows `render_total` dominated by `processed16`; the no-screenshot disk trace held `AvgDiskQueueLength < 1` (disk keeps up, not gating cadence); playback reads are largely intrinsic and the screenshot harness only inflates the write side. Disk is a separate, riskier track the data does not yet justify prioritizing.
- Confirmed from on-disk telemetry (`.claude-state/profiling/20260608-x1x2-quarterres-validation`) that standard-preview **x1 lands `processed8_prefetch_hits=0` on all three clips** (M16-1327/1347/1446) while x2 lands 148-250. The processed8 prefetch worker was enabled at x1, running the full background RGB/dual-ISO/shadows-highlights pipeline at full resolution, so it can never get ahead of the equally-expensive x1 foreground frame and only steals cores/IO from the slowest priority lane.
- Narrowed the standard-preview branch of `mlv_processed8_prefetch_enabled()` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) from `scale<=2 || scale>=4` to `scale==2 || scale>=4`, excluding x1 only. The aggressive-preview branch is unchanged. Updated the pinning regression to `DualIsoPipeline.Processed8PrefetchEnablesStandardScaleTwoFourAndEightButNotOne` in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp) (x1=0; x2/x4/x8=1).
- Rebuilt the user-facing release tree:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=6/8/2026 7:52:08 PM`
  - `Length=9113600`
  - `SHA256=BC888E7560900D0B4772357120134EA161EBF9E8279C235695C70ACF44A9208E`
- Qt-linked focused pipeline regression green via [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1) (`Processed8PrefetchEnables*`, `RawUint16Prefetch*`, `Phase4Bv4_*`, `StandardPreviewScale*`).
- The full pipeline suite still reports the same 17 pre-existing failures (direct8 byte-identity, AVX2 parity, foreground processed8/16 cache, aggressive recon). None invoke the x1 prefetch gate: those tests use the foreground `renderFrame8` path, not `mlv_processed8_prefetch_enabled` (consulted only on the live playback path at `video_mlv.c:5181`), and the cache tests' own in-code comments attribute their changed behavior to the pre-existing direct8 (Phase E7) work. Confirmed unrelated.
- Screenshot-backed same-build smoke (`.claude-state/profiling/20260609-x1prefetch-drop`):
  - x1 (changed lane): `M16-1327 9.103 fps` (baseline 5.86, +55%), `M16-1347 8.92 fps` (baseline 5.366, +66%), `M16-1446 8.871 fps` (baseline 5.342, +66%); all `validation.ok=true`, `colorArtifactScan.verdict=clear-heuristic`, `raw_prefetch_hits` intact (108-129), x1 `avg_render_total_ms` ~88 (vs ~140 baseline).
  - x8 canary: `M16-1327 26.068 fps`, `M16-1347 26.595 fps`, `M16-1446 32.142 fps`; `processed8_prefetch_hits` still 783-883, so the x8 worker is untouched.
- Manually inspected presented frames: all three x1 frames are clean, sharp, natural color; all three x8 canary frames are coherent (M16-1327 aquarium and M16-1347 lobby both clean this run; M16-1446 very dark but coherent with no new bar/block artifact).

### Cross-checked from prior analysis

- This is the largest single x1 gain found so far and it is zero-pixel-risk: the win comes from removing a background pipeline that never served a hit, not from altering the presented frame.
- x2/x4/x8 standard-preview prefetch behavior is byte-identical (only the x1 branch changed), so the other lanes are unaffected by construction; the x8 canary was still inspected per the mandatory loop.
- The longstanding x8 corruption pattern did not appear on this run, consistent with it being intermittent / IO-pressure-dependent rather than a deterministic gate failure.

### Needs runtime profiling

- The next bottleneck on the priority lanes is now squarely `processed16` (x1 `render_total` ~88 ms is ~82 ms `processed16`); the next candidate should target the processed16 / shadows-highlights tail.
- Keep the x8 canary screenshot review in every future candidate; keep x4 quarter-res off by default.

# 2026-06-08 - x1/x2 quarter-res default-on remains the keeper-safe gain; x8 canary corruption and SSD pressure are still live

### Verified locally

- I rebuilt the user-facing release tree at [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe) after the current x1/x2 quarter-res change and captured the fingerprint:
  - `LastWriteTime=6/8/2026 6:48:46 PM`
  - `Length=9113600`
  - `SHA256=57798E288862C2F18AB37CA0A2FA70EB0ED11141E0DFC2742B3015B8EBCAE0DC`
- I reran the Qt-linked pipeline regression wrapper through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1). The new x2 default-on assertion now passes, but the suite still reports unrelated pre-existing failures in the aggressive x2 processing-filter path and several direct8/cache guardrail tests.
- I reran screenshot-backed same-build smoke on the standard trio at `1x`, `2x`, `4x`, and `8x` in the current profiling folders.
- The lower lanes remain visually clean:
  - `M16-1327`: `1x` `5.860 fps`, `2x` `14.846 fps`
  - `M16-1347`: `1x` `5.366 fps`, `2x` `14.085 fps`
  - `M16-1446`: `1x` `5.342 fps`, `2x` `12.611 fps`
- The x8 canary still shows the longstanding block / color-corruption pattern on `M16-1327` and `M16-1347`.
- `M16-1446` at `x8` is still very dark, but I did not see a new magenta/pink/green bar artifact in the presented frame.
- The latest disk investigation on `M16-1446` at `x2` still shows heavy playback reads, with screenshot capture adding enough write pressure to make Resource Monitor look much worse than the no-screenshot path.
- A probe-enabled `M16-1327` x2 sample still ranks the broader processing tail ahead of the quarter-res helper, with `processing_shadows_highlights_prep` and `processing_core_other` leading the visible cost.

### Cross-checked from prior analysis

- The x1/x2 quarter-res default-on change remains the best keeper-safe FPS improvement found so far for the standard trio.
- The x4 quarter-res default-on probe remains off by default because it hurt cadence on the current baseline.
- The x8 canary corruption appears to be the same longstanding failure mode, not a new regression from the current x1/x2 candidate.
- The next bottleneck is still the broader processing tail, not the quarter-res helper itself.

### Needs runtime profiling

- Keep the x8 canary review loop in every candidate.
- Compare playback disk pressure with and without screenshot capture if we want to quantify write amplification separately from the real playback reads.
- If the next tweak touches playback cadence or throughput again, rerun the same-build trio sweep before keeping it.
- Treat the current pipeline-suite failures as open triage items until we know whether any of them are newly introduced by the current quarter-res candidate.
# 2026-06-08 - current regression sweep is clean in x1/x2/x4, while the x8 canary still shows corruption on M16-1327 and M16-1347

### Verified locally

- I rebuilt the user-facing release tree at [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe) and captured the current fingerprint:
  - `LastWriteTime=6/8/2026 3:14:53 PM`
  - `Length=9113600`
  - `SHA256=D4C7417784178FB421EDDBD19C26C1A9B2AB6A275394B2D27B79E0891F3399A6`
- I rebuilt the Qt-linked pipeline test tree and ran the regression wrapper through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1). The wrapper completed successfully after the rebuild.
- I reran screenshot-backed same-build GUI smoke across the standard trio at `1x`, `2x`, `4x`, and `8x` into [`.claude-state/profiling/20260608-regression-sweep`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-regression-sweep).
- The fresh sweep is visually clean in the lower lanes:
  - `M16-1327`: `1x` `5.73 fps`, `2x` `14.74 fps`, `4x` `24.60 fps`
  - `M16-1347`: `1x` `5.73 fps`, `2x` `17.63 fps`, `4x` `20.77 fps`
  - `M16-1446`: `1x` `5.52 fps`, `2x` `20.32 fps`, `4x` `20.27 fps`
- The x8 canary still shows real corruption on the presented frames for `M16-1327` and `M16-1347`.
- `M16-1446` at `x8` is very dark, but the color-artifact heuristic stayed clear and I did not see a new magenta/pink/green bar artifact in the presented frame.
- The smoke runs all reported `validation.ok=true` and `colorArtifactScanVerdict=clear-heuristic`.
- The fresh sweep still shows heavy read-side playback pressure, which keeps the SSD investigation live.
- I also ran a probe-enabled `M16-1327` x1/x2 sample with `MLVAPP_SHADOWS_HIGHLIGHTS_PROBE=1` to split the current processing tail:
  - `x1`: `presented_fps=5.924`, `avg_render_total_ms=140.047 ms` (`7.14 fps-equiv`), `avg_processing_ms=58.964 ms` (`16.96 fps-equiv`), `avg_processing_shadows_highlights_prep_ms=14.526 ms` (`68.84 fps-equiv`)
  - `x2`: `presented_fps=8.753`, `avg_render_total_ms=80.600 ms` (`12.41 fps-equiv`), `avg_processing_ms=33.218 ms` (`30.11 fps-equiv`), `avg_processing_shadows_highlights_prep_ms=14.526 ms` (`68.84 fps-equiv`), `avg_processing_core_other_ms=10.298 ms` (`97.10 fps-equiv`)
  - `x2` also recorded `raw_prefetch_hits=168` and `processed8_prefetch_hits=42`, so the prefetch path is active but not the dominant limiter
- The probe split says the next bottleneck is the broader processing tail, with `processing_shadows_highlights_prep` and `processing_core_other` leading the visible cost rather than the quarter-res helper itself.

### Cross-checked from prior analysis

- The clean lower lanes mean the current code path is not introducing a new visible regression at `1x`, `2x`, or `4x` in the standard trio.
- The x8 canary corruption is persistent and appears to be the same longstanding failure mode rather than a new artifact from this sweep.
- The live bottleneck hunt should keep `1x` and `2x` first in priority, but the canary review loop still has to gate any future widening.
- The probe-enabled x2 sample makes the processing tail a better next target than the raw-decode or quarter-res prep helpers.

### Needs runtime profiling

- Investigate the heavy disk activity during playback so we can tell how much of the remaining pacing gap is I/O versus processing.
- Keep the x8 canary review in every future candidate loop.
- If the next tweak touches playback cadence or throughput again, rerun the same-build trio sweep before keeping it.
- Before another code change, decide whether the next safe lever is a tighter processing-tail optimization or a disk-path mitigation; the data now favors the processing tail.

# 2026-06-08 - processed8 prefetch widened for x1/x2; matrix is cleaner in x1/x2/x4 but the x8 canary still fails on M16-1327

### Verified locally

- I widened `mlv_processed8_prefetch_enabled()` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) so standard preview now enables processed8 prefetch for scale `1` and `2` as well as the existing `4` and `8` lanes.
- I updated the matching regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp) and rebuilt the user-facing release tree:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=6/8/2026 2:15:02 PM`
  - `Length=9114112`
  - `SHA256=3C877116F29F7C1AC77188348AFAACFF3347D378B04F990CBB053B4B1B8C2EB5`
- The Qt-linked pipeline regression wrapper passed after rebuilding the pipeline test tree:
  - [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1)
  - `DualIsoPipeline.Processed8PrefetchEnablesAggressiveScaleOneTwoAndFour`
  - `DualIsoPipeline.Processed8PrefetchEnablesStandardScaleOneTwoFourAndEight`
- I reran the standard trio screenshot-backed same-build matrix at `1x`, `2x`, `4x`, and `8x` into [`C:\!Layi Wkspc\MLV-App\.claude-state\profiling\20260608-processed8-prefetch-x1x2-sweep`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-processed8-prefetch-x1x2-sweep).
- The matrix is cleaner than the previous raw-prefetch baseline:
  - `scale-1` is visually clean across `M16-1327`, `M16-1347`, and `M16-1446`
  - `scale-2` is visually clean across the trio
  - `scale-4` is visually clean across the trio
  - `scale-8` still fails on `M16-1327` with a visible corruption artifact in the presented frame
- The smoke telemetry shows the new gate is taking effect where it matters most:
  - `scale-2` has `processed8_prefetch_hits=299` on `M16-1347`
  - `scale-4` has `processed8_prefetch_hits=543` on `M16-1446`
  - `scale-8` has `processed8_prefetch_hits=593` on `M16-1347` and `processed8_prefetch_hits=660` on `M16-1446`
  - `scale-1` still reports `processed8_prefetch_hits=0`, so `x1` still needs a different bottleneck pass
- The most important FPS wins so far are at `x2` and `x4`:
  - `M16-1347` at `x2` presented `15.329 fps`
  - `M16-1446` at `x2` presented `18.300 fps`
  - `M16-1446` at `x4` presented `21.179 fps`
- `M16-1327` at `x8` remains the live canary for visual corruption and should stay in every future candidate loop.

### Cross-checked from prior analysis

- The change is an improvement over the previous raw-prefetch baseline, but it is not yet a finish line because `x1` still does not show processed8-prefetch hits and the `x8` canary still fails on `M16-1327`.
- The disk-pressure concern is still live, but the latest evidence now suggests the priority-lane throughput win is coming from the processed8 cache/prefetch overlap rather than from another raw-prefetch widen.

### Needs runtime profiling

- Investigate why `x1` still has `processed8_prefetch_hits=0` even after the gate widen.
- Keep the `x8` canary review loop in place and decide whether the next bottleneck is a separate `x1` render-path gate, a direct8 path adjustment, or a raw/cache mitigation.

# 2026-06-08 - x1 rollback did not restore a fully clean matrix; the remaining corruption looks clip-specific and disk pressure is still live

### Verified locally

- I rolled the raw-prefetch `x1` lookahead in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) back from `6` to `4` and restored the matching regression expectations in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- I rebuilt the user-facing release tree after the rollback and verified the current fingerprint:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=6/8/2026 12:53:18 PM`
  - `Length=9114112`
  - `SHA256=97D2871EDB38276FD5AD637B1C6844368AB055D2730C0CB686C2FAA888D4C30B`
- I reran the Qt-linked regression coverage through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1) for:
  - `PlaybackQualityAutoSampler.*`
  - `DualIsoPipeline.RawUint16Prefetch*`
- I reran the full screenshot-backed same-build matrix across `M16-1327`, `M16-1347`, and `M16-1446` at `1x`, `2x`, `4x`, and `8x` into [`C:\!Layi Wkspc\MLV-App\.claude-state\profiling\20260608-x1-rollback-cleancheck`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x1-rollback-cleancheck).
- The rollback did not make the full matrix universally clean:
  - `M16-1327` at `x4` still showed a severe presented-frame corruption pattern
  - `M16-1347` at `x8` still showed a magenta top-band artifact
  - `M16-1327` at `x8` still showed a dark / black-frame artifact
- I then ran a 60-second no-screenshot GUI smoke on `M16-1446` at `x2` and sampled disk counters for the whole run:
  - matrix folder: [`C:\!Layi Wkspc\MLV-App\.claude-state\profiling\20260608-disk-job-smoke-noscreenshot-2x`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-disk-job-smoke-noscreenshot-2x)
  - `smokePresentedFps=11.787`
  - `smokeTimelineFps=23.920`
  - `avg_llrawproc_ms=12.891`
  - `avg_processed16_ms=55.639`
  - `PercentDiskTime` peaked at `125` during sampling, with `AvgDiskQueueLength` staying under `1`
  - sampled disk throughput peaked at `DiskReadBytesPerSec=245828908` and `DiskWriteBytesPerSec=68179791`
  - This is stronger evidence than the earlier one-sample burst because it covers the full playback interval and still shows a sustained disk-heavy workload even without screenshot capture.

### Cross-checked from prior analysis

- The recent regression pattern is not explained away by the `x1=6` rollback alone.
- The clip-specific failures are still useful canaries because they keep surfacing real visual corruption rather than just heuristic noise.
- The SSD pressure report is still credible because it now has both the user’s Resource Monitor capture and two live playback samples pointing in the same direction.

### Needs runtime profiling

- Investigate the disk-heavy playback path before trying another broad lookahead tweak.
- Keep the failing `x4` / `x8` canaries in the validation loop until the corruption pattern is understood.
- Treat the current raw-prefetch baseline as provisional, not as a keeper.

# 2026-06-08 - the reverted x1=6 baseline is not fully clean; x4/x8 canaries show visible corruption and SSD pressure is still live

### Verified locally

- I re-ran the Qt-linked regression coverage for the current raw-prefetch baseline through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1):
  - `PlaybackQualityAutoSampler.*`
  - `DualIsoPipeline.RawUint16Prefetch*`
- I then ran a fresh screenshot-backed same-build smoke sweep across the standard trio on `M16-1327`, `M16-1347`, and `M16-1446` at `1x`, `2x`, `4x`, and `8x` using the current release tree:
  - matrix folder: [`C:\!Layi Wkspc\MLV-App\.claude-state\profiling\20260608-no-regression-sweep`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-no-regression-sweep)
- The sweep kept `x1` and `x2` visually clean on the sampled presented-frame screenshots, but it did not stay clean across the whole matrix:
  - `M16-1327` at `x8` showed severe RGB separation / block corruption in the presented frame
  - `M16-1446` at `x8` showed an almost black / very dark presented frame
  - `M16-1347` at `x4` showed a magenta top bar in the presented frame and failed the color-artifact check
- The current release fingerprint after the latest rebuild is:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=6/8/2026 12:29:16 PM`
  - `Length=9114112`
  - `SHA256=99A14E92A85B8D2CA18E3FACA97A69843EAE7A09097FE05BF27E07B26CE63390`

### Cross-checked from prior analysis

- The user-provided Resource Monitor capture showed the SSD pinned at 100% with `System` and `MLVApp.exe` as the largest disk contributors, so the I/O path is still part of the live investigation.
- The reverted `x1=6` baseline is still the current code path, but the fresh sweep says it is not yet a universally clean keeper across all four scales.
- The x8 canary review loop is still necessary on every candidate, and the current sweep shows it is catching real visual regressions rather than just telemetry noise.

### Needs runtime profiling

- Investigate the disk bottleneck before trying another widening tweak; the current user-visible problem is not just cadence, it is also I/O pressure.
- Re-review the failing `x4` / `x8` presented-frame captures before deciding whether the next step is a narrower cache/prefetch change, a disk-path mitigation, or a rollback to an even safer baseline.
- Keep `1x` and `2x` prioritized, but do not relax the `x8` canary gate.

# 2026-06-08 - raw-prefetch lookahead x1=6/x2=10 is the current live candidate, with mixed lane results but clean canary review

### Verified locally

- I bumped the standard-preview raw uint16 prefetch lookahead in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) to `x1=6`, `x2=10`, `x4=8`, and `x8=8`.
- I updated the matching regression expectations in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- I rebuilt the user-facing release tree and verified the current fingerprint:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=2026-06-08 11:47:35 AM`
  - `Length=9114112`
  - `SHA256=CABDE97AD9C9539C18CD362CFFCF7B6BF487CFF8018FABC561181C4B968A879E`
- I ran the Qt-linked regression checks through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1) for the raw-prefetch lookahead coverage, then reran the full standard M16 same-build matrix on `M16-1327`, `M16-1347`, and `M16-1446` across `1x`, `2x`, `4x`, and `8x`.
- The smoke matrix completed with screenshot-backed validation, and I manually reviewed the `x8` canary frames for all three clips. They stayed baseline-consistent without a new magenta/pink/green bar artifact.

### Cross-checked from prior analysis

- `x1` improved a little, `x2` improved less cleanly, and `x4` traded some cadence for a mixed lane result compared with the previous keeper baseline.
- The shared processing tail is still the likely long-term bottleneck, but this candidate is the current one under decision rather than a settled keeper.

### Needs runtime profiling

- Keep the current `x1=6 / x2=10` raw-prefetch matrix as the baseline for the next search step.
- The next bottleneck still looks like the shared processing tail, especially `processed16` and the shadows/highlights prep path at `x1/x2`.
- Keep the x8 canary review loop in place on every future candidate.

# 2026-06-08 - standard-preview processed8 prefetch widened to x2, then reverted after matrix regression

### Verified locally

- I widened `mlv_processed8_prefetch_enabled()` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) so standard preview would try the processed8 prefetch worker at scale `2` as well as `4` and `8`, and I updated the matching regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- The focused Qt-linked console and pipeline regressions passed, but the full same-build standard M16 matrix regressed versus the prior keeper baseline.
- I reverted the x2 prefetch widen back to the standard-preview `x4/x8` gate and restored the matching regression expectation.

### Cross-checked from prior analysis

- The x2 prefetch widen increased cache activity, but it did not improve end-to-end playback cadence on the standard trio.
- The shared processing tail remains the next likely bottleneck, not processed8 prefetch gating.

### Needs runtime profiling

- Keep the x4/x8 prefetch behavior as the current baseline and continue looking for a smaller safe shared-tail change rather than widening standard preview prefetch further.

# 2026-06-08 - alias-map default-on was inert on the standard trio; x2/x4 quarter-res is the live candidate

### Verified locally

- I exercised the standard-preview alias-map default-on branch in [`platform/qt/DualIsoPlaybackPolicy.h`](C:/!Layi%20Wkspc/MLV-App/platform/qt/DualIsoPlaybackPolicy.h) and the matching console regression in [`tests/console/test_dual_iso_playback_policy.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/console/test_dual_iso_playback_policy.cpp).
- The standard M16 smoke matrix completed cleanly, but the alias-map path was effectively inert on the standard trio because the receipts already carried `dual_iso_alias_map=0` and the runtime telemetry stayed at `use_alias_map_any=0` with `mix_alias_map_ms=0.0`.
- I then started the next live candidate in [`src/processing/raw_processing.c`](C:/!Layi%20Wkspc/MLV-App/src/processing/raw_processing.c): make standard-preview `x2` and `x4` quarter-res shadows/highlights default-on, keeping the existing disable overrides.

### Cross-checked from prior analysis

- The alias-map idea is not the next useful lever for the standard M16 trio.
- The shared processing tail is still the likely bottleneck.
- The x2/x4 quarter-res change is the next candidate worth validating with the same-build `1x/2x/4x/8x` matrix and x8 canary screenshots.

### Needs runtime profiling

- Rebuild after the x2/x4 quarter-res change, rerun the Qt-linked regressions through `tools\testing\run-windows-test.ps1`, and then smoke the standard M16 trio again before deciding keep/revert.
- Keep the x8 canary review loop in place on every candidate.

# 2026-06-08 - direct8 is already active at scale 8; the bottleneck is llrawproc HQ dual-ISO work

### Verified locally

- I added a thread-local direct8 reason helper in [`src/processing/raw_processing.c`](C:/!Layi%20Wkspc/MLV-App/src/processing/raw_processing.c) and surfaced it in GUI stage telemetry via [`platform/qt/RenderFrameThread.cpp`](C:/!Layi%20Wkspc/MLV-App/platform/qt/RenderFrameThread.cpp).
- The new helper reports `direct8-active` on the standard M16 scale-8 direct-playback path.
- I corrected the console golden in [`tests/console/test_clip_golden.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/console/test_clip_golden.cpp) so it now asserts the real runtime state:
  - `LocalM16StandardPreviewScaleEightKeepsProcessed8DirectPathActive`
- I also captured the latest direct-playback telemetry in `.claude-state/profiling/direct8-reason-m16-1327-scale8.json`.

### Cross-checked from prior analysis

- The direct8 path is already active for the standard M16 scale-8 playback profile.
- The 621 ms `processed16_for_8bit` span is not a dead direct8 gate problem; the JSON shows `llrawproc_dual_iso_ms` around 507 ms, with `dual_iso_full20_interp_mean23_ms` around 418 ms and `dual_iso_full20_mix_ms` around 87 ms.
- `processed8_prefetch_hit` remains false on the one-frame probe because that probe only proves the direct8 path is active, not that the prefetch cache is being hit.

### Needs runtime profiling

- The next bottleneck is the HQ dual-ISO llrawproc interpolation/mix path, not the direct8 gate or the later RGB processing core.
- A future perf change should target that raw-domain work, while keeping the x8 canary review loop in place.

# 2026-06-08 - standard preview processed8 prefetch narrowed to x4/x8 restores x2 and keeps the mid/high lanes

### Verified locally

- I narrowed the standard-preview processed8 prefetch gate in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) so playback preview only enables processed8 prefetch at scale `4` and `8`, while aggressive preview still uses the existing lower-scale behavior.
- I kept the processed8 prefetch worker independent of the conservative foreground direct8 path, so the prefetch worker can warm the cache without changing the presented pixels.
- I updated the matching pipeline regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp):
  - `DualIsoPipeline.Processed8PrefetchEnablesStandardScaleFourAndEight`
- I rebuilt the pipeline tests and the user-facing release tree after the code/test edits:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=2026-06-08 8:22:48 AM`
  - `Length=9110528`
  - `SHA256=CCA21CBBCA5CD90F6D0187A96AD71789F62EE1FA4F473F913047033D43316FAA`
- Qt-linked regression checks passed through the Windows wrapper:
  - `PlaybackQualityAutoSampler.*`
  - `DualIsoPipeline.RawUint16PrefetchLookaheadExpandsForAggressiveScaleOneTwoAndFour`
  - `DualIsoPipeline.RawUint16PrefetchLookaheadExpandsForStandardScaleOneTwoFourAndEight`
  - `DualIsoPipeline.Processed8PrefetchEnablesStandardScaleFourAndEight`
  - `DualIsoPipeline.StandardPreviewScaleOneUsesQuarterResShadowsHighlightsByDefault`
  - `DualIsoPipeline.StandardPreviewScaleTwoCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleFourCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.Phase4Bv4_*`
- I reran the full same-build standard M16 smoke matrix across `x1`, `x2`, `x4`, and `x8` on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- All 12 screenshot-backed runs completed with `validation.ok=true` and `colorArtifactScan.verdict=clear-heuristic`.
- I manually inspected the x8 canary presented frames for `M16-1327` and `M16-1446`. They matched the older canary look and did not show a new magenta/pink/green bar or block artifact.

### Cross-checked from prior analysis

- New matrix averages after the narrowed processed8 gate:
  - `x1`: presented `6.145 fps`, timeline `23.392 fps`, GUI `1.000 fps`, render `133.093 ms` (`7.51 fps-equiv`), `llrawproc 37.631 ms` (`26.57 fps-equiv`), `processed8 131.418 ms` (`7.61 fps-equiv`), `processed16 123.914 ms` (`8.07 fps-equiv`), shadows/highlights prep `17.844 ms` (`56.04 fps-equiv`), `raw_prefetch_hits 84.7`, `processed8_prefetch_hits 0.0`
  - `x2`: presented `11.123 fps`, timeline `23.293 fps`, GUI `0.833 fps`, render `59.560 ms` (`16.79 fps-equiv`), `llrawproc 13.741 ms` (`72.78 fps-equiv`), `processed8 56.287 ms` (`17.77 fps-equiv`), `processed16 52.836 ms` (`18.92 fps-equiv`), shadows/highlights prep `9.728 ms` (`102.79 fps-equiv`), `raw_prefetch_hits 239.0`, `processed8_prefetch_hits 0.0`
  - `x4`: presented `12.514 fps`, timeline `23.382 fps`, GUI `0.967 fps`, render `42.472 ms` (`23.55 fps-equiv`), `llrawproc 8.203 ms` (`121.91 fps-equiv`), `processed8 37.810 ms` (`26.45 fps-equiv`), `processed16 33.602 ms` (`29.76 fps-equiv`), shadows/highlights prep `11.234 ms` (`89.01 fps-equiv`), `raw_prefetch_hits 235.7`, `processed8_prefetch_hits 117.3`
  - `x8`: presented `15.315 fps`, timeline `23.443 fps`, GUI `1.533 fps`, render `27.108 ms` (`36.89 fps-equiv`), `llrawproc 4.847 ms` (`206.31 fps-equiv`), `processed8 22.567 ms` (`44.31 fps-equiv`), `processed16 18.930 ms` (`52.82 fps-equiv`), shadows/highlights prep `6.720 ms` (`148.81 fps-equiv`), `raw_prefetch_hits 215.0`, `processed8_prefetch_hits 237.7`
- Compared with the broader standard-preview processed8 attempt, this narrowed version fixes the `x2` regression while keeping the `x4`/`x8` gains.
- The x8 canary stayed visually consistent with the older acceptable pattern.

### Needs runtime profiling

- The current keeper baseline is now `x1=4`, `x2=10`, `x4=8`, `x8=8`, with standard-preview processed8 prefetch only active at `x4` and `x8`.
- The next bottleneck remains the shared processing tail, especially `processed16` and shadows/highlights prep at `x1/x2/x4`.
- If we keep iterating, the next change should preserve the x8 canary review loop and avoid reintroducing the broader x2 processed8 prefetch activation.
- I also added a console-golden regression that profiles the three local M16 clips at scale `8` and asserts `processed8_direct_path_active` stays false, so the current direct8 blocker is now durably guarded without changing playback behavior.

# 2026-06-08 - x2 raw-prefetch lookahead bump lifts the whole standard matrix without a new visual artifact

### Verified locally

- I bumped the standard-preview raw uint16 prefetch lookahead for `x2` from `8` to `10` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) and updated the matching regression expectations in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- I rebuilt the user-facing release tree after the code/test edits:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=2026-06-08 6:34:02 AM`
  - `Length=9110528`
  - `SHA256=B6CDC6314E50DAF73394D4242879F679720D6020DED0746031D65BBAC6FA508D`
- Qt-linked regression checks passed through the Windows wrapper:
  - `PlaybackQualityAutoSampler.*`
  - `DualIsoPipeline.RawUint16PrefetchLookaheadExpandsForAggressiveScaleOneTwoAndFour`
  - `DualIsoPipeline.RawUint16PrefetchLookaheadExpandsForStandardScaleOneTwoFourAndEight`
  - `DualIsoPipeline.StandardPreviewScaleOneUsesQuarterResShadowsHighlightsByDefault`
  - `DualIsoPipeline.StandardPreviewScaleTwoCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleFourCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.Processed8PrefetchEnablesStandardScaleTwoFourAndEight`
  - `DualIsoPipeline.Phase4Bv4_*`
- I reran the full same-build standard M16 smoke matrix across `x1`, `x2`, `x4`, and `x8` on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- All 12 screenshot-backed runs completed with `validation.ok=true` and `colorArtifactScan.verdict=clear-heuristic`.
- I manually inspected the x8 canary presented frames for `M16-1327` and `M16-1446`. They matched the older canary look and did not show a new magenta/pink/green bar or block artifact.

### Cross-checked from prior analysis

- New matrix averages after the `x2` lookahead bump:
  - `x1`: presented `6.75 fps`, timeline `23.40 fps`, GUI `1.07 fps`, render `120.97 ms` (`8.27 fps-equiv`), `llrawproc 33.47 ms` (`29.88 fps-equiv`), `processed8 119.18 ms` (`8.39 fps-equiv`), `processed16 111.97 ms` (`8.93 fps-equiv`), shadows/highlights prep `16.81 ms` (`59.50 fps-equiv`), `raw_prefetch_hits 106.0`
  - `x2`: presented `11.00 fps`, timeline `23.29 fps`, GUI `0.93 fps`, render `62.50 ms` (`16.00 fps-equiv`), `llrawproc 14.72 ms` (`67.93 fps-equiv`), `processed8 59.14 ms` (`16.91 fps-equiv`), `processed16 55.67 ms` (`17.96 fps-equiv`), shadows/highlights prep `9.90 ms` (`101.01 fps-equiv`), `raw_prefetch_hits 229.7`
  - `x4`: presented `13.37 fps`, timeline `23.41 fps`, GUI `1.10 fps`, render `42.49 ms` (`23.54 fps-equiv`), `llrawproc 8.16 ms` (`122.55 fps-equiv`), `processed8 39.18 ms` (`25.53 fps-equiv`), `processed16 36.60 ms` (`27.32 fps-equiv`), shadows/highlights prep `7.80 ms` (`128.21 fps-equiv`), `raw_prefetch_hits 273.0`
  - `x8`: presented `14.06 fps`, timeline `23.49 fps`, GUI `1.20 fps`, render `38.38 ms` (`26.06 fps-equiv`), `llrawproc 6.99 ms` (`143.06 fps-equiv`), `processed8 34.68 ms` (`28.84 fps-equiv`), `processed16 32.05 ms` (`31.20 fps-equiv`), shadows/highlights prep `5.64 ms` (`177.30 fps-equiv`), `raw_prefetch_hits 278.3`
- `processed8_prefetch_hits` stayed `0` and `processed8_direct_path_frames` stayed `0`, so the new improvement is still a raw-prefetch/cadence win rather than a direct processed8-path activation.
- Compared with the previous keeper baseline, this round improved all four scales on the smoke telemetry and kept the x8 visual canary in the older acceptable pattern.

### Needs runtime profiling

- The next bottleneck is still the shared processing tail, especially `processed16` and the shadows/highlights prep path at `x1/x2/x4`.
- The x8 canary should keep getting screenshot review, but this candidate is keeper-safe enough to keep building from.

# 2026-06-08 - restored baseline matrix is clean, and the remaining bottleneck is still in the shared processing tail

### Verified locally

- I restored the validated playback baseline in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) and the matching regression expectations in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- I rebuilt the user-facing release tree after the restore:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=2026-06-08 12:16:57 AM`
  - `Length=9110016`
  - `SHA256=41904F13B00A4781C67F0490967DA925D4118B536E66E0F46BE690CCF5FB3FAD`
- Qt-linked regression checks passed through the Windows wrapper:
  - `PlaybackQualityAutoSampler.*`
  - `DualIsoPipeline.RawUint16Prefetch*`
  - `DualIsoPipeline.Phase4Bv4_*`
- I reran the full same-build standard M16 smoke matrix across `x1`, `x2`, `x4`, and `x8` on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- Every one of the 12 screenshot-backed runs stayed `validation.ok=true` and `colorArtifactScan.verdict=clear-heuristic`.
- I manually inspected the presented-frame screenshots for the `x8` canaries and one `x4` control frame. I did not see new magenta, pink, or green bar artifacts.

### Cross-checked from prior analysis

- The restored baseline is the same direction we had already converged on before the truncated note: it preserves the safer x8-standard behavior and keeps the standard M16 trio visually clean.
- The new matrix keeps the lane split stable:
  - `x1` is still the slow cadence lane
  - `x2` and `x4` are the mid lanes
  - `x8` is the best throughput lane and remains the visual canary
- The biggest remaining cost is still in the shared processing tail, not in draw/present:
  - `x1`: `avg_render_total_ms=577.987 ms` (`1.73 fps-equiv`), `avg_llrawproc_ms=40.894 ms` (`24.45 fps-equiv`), `avg_processed16_ms=569.012 ms` (`1.76 fps-equiv`), `avg_processing_shadows_highlights_prep_ms=456.887 ms` (`2.19 fps-equiv`)
  - `x2`: `avg_render_total_ms=177.318 ms` (`5.64 fps-equiv`), `avg_llrawproc_ms=15.010 ms` (`66.62 fps-equiv`), `avg_processed16_ms=170.710 ms` (`5.86 fps-equiv`), `avg_processing_shadows_highlights_prep_ms=119.891 ms` (`8.34 fps-equiv`)
  - `x4`: `avg_render_total_ms=160.831 ms` (`6.22 fps-equiv`), `avg_llrawproc_ms=10.288 ms` (`97.20 fps-equiv`), `avg_processed16_ms=155.193 ms` (`6.44 fps-equiv`), `avg_processing_shadows_highlights_prep_ms=119.568 ms` (`8.36 fps-equiv`)
  - `x8`: `avg_render_total_ms=73.416 ms` (`13.62 fps-equiv`), `avg_llrawproc_ms=8.303 ms` (`120.44 fps-equiv`), `avg_processed16_ms=67.535 ms` (`14.81 fps-equiv`), `avg_processing_shadows_highlights_prep_ms=36.305 ms` (`27.54 fps-equiv`)
- Raw-prefetch participation is present but still modest at the slower scales and strongest at `x8`:
  - `x1`: `18.333` average hits
  - `x2`: `79.333`
  - `x4`: `90.667`
  - `x8`: `207.000`

### Needs runtime profiling

- The next bottleneck to attack is still the shared processing tail, especially the shadows/highlights prep and the `processed16` path at `x1/x2/x4`.
- Any next candidate should stay smaller than the reverted quarter-res shadows/highlights attempt and must keep screenshot-backed validation in place.

# 2026-06-08 - standard x2 quarter-res path improves throughput, with x8 still matching the old visual canary pattern

### Verified locally

- I kept the standard `x2` quarter-res shadows/highlights gate in [`src/processing/raw_processing.c`](C:/!Layi%20Wkspc/MLV-App/src/processing/raw_processing.c) and the matching pipeline coverage in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- I rebuilt the pipeline test binary and re-ran the targeted Qt-linked checks through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1):
  - `PlaybackQualityAutoSampler.*`
  - `DualIsoPipeline.RawUint16Prefetch*`
  - `DualIsoPipeline.Phase4Bv4_*`
- I refreshed the release fingerprint after the code/test edits:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=2026-06-08 12:56:51 AM`
  - `Length=9110016`
  - `SHA256=04A0C8A52579AFAE2EE4FC9D655FE9B5A5F252FD9C5D0C702F3A093EF4116F32`
- I reran the full same-build standard M16 smoke matrix across `x1`, `x2`, `x4`, and `x8` on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- The screenshot-backed runs were visually clean on `x1`, `x2`, and `x4`. `M16-1327` at `x8` still tripped the heuristic color scan, but manual review matched the earlier restored-baseline x8 visual pattern and did not show a new bar/block artifact.

### Cross-checked from prior analysis

- The new matrix is materially faster on the mid lanes:
  - `x1`: presented `4.670 fps`, timeline `23.384 fps`, GUI `4.867 fps`, render `183.109 ms` (`5.46 fps-equiv`), `llrawproc 53.075 ms` (`18.84 fps-equiv`), `processed16 172.518 ms` (`5.80 fps-equiv`), shadows/highlights prep `33.272 ms` (`30.06 fps-equiv`)
  - `x2`: presented `9.929 fps`, timeline `23.365 fps`, GUI `9.933 fps`, render `71.608 ms` (`13.96 fps-equiv`), `llrawproc 18.074 ms` (`55.33 fps-equiv`), `processed16 63.999 ms` (`15.63 fps-equiv`), shadows/highlights prep `10.391 ms` (`96.23 fps-equiv`)
  - `x4`: presented `10.037 fps`, timeline `23.429 fps`, GUI `10.100 fps`, render `70.082 ms` (`14.27 fps-equiv`), `llrawproc 15.812 ms` (`63.24 fps-equiv`), `processed16 61.962 ms` (`16.14 fps-equiv`), shadows/highlights prep `13.728 ms` (`72.84 fps-equiv`)
  - `x8`: presented `11.149 fps`, timeline `23.478 fps`, GUI `9.133 fps`, render `56.936 ms` (`17.56 fps-equiv`), `llrawproc 12.643 ms` (`79.09 fps-equiv`), `processed16 49.222 ms` (`20.32 fps-equiv`), shadows/highlights prep `9.377 ms` (`106.65 fps-equiv`)
- Raw-prefetch participation is still strongest at `x8`, but the new `x2` quarter-res path is what improved the mid-lane balance without changing the visual canary pattern on the standard clips.

### Needs runtime profiling

- The next bottleneck to attack is still the shared processing tail, especially `processed16` and the shadows/highlights prep path on `x1/x2/x4`.
- The `x8` visual canary should keep being inspected on every candidate, even when the heuristic scan is suspicious on the green-heavy `M16-1327` clip.

# 2026-06-08 - scale-4 quarter-res shadows/highlights gate is a keeper, and the full matrix improved again

### Verified locally

- I added a second explicit quarter-res gate in [`src/processing/raw_processing.c`](C:/!Layi%20Wkspc/MLV-App/src/processing/raw_processing.c) for standard preview scale 4, guarded by `MLVAPP_ENABLE_STANDARD_X4_SH_QUARTERRES`.
- I added a matching pipeline regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp):
  - `DualIsoPipeline.StandardPreviewScaleFourCanUseQuarterResShadowsHighlightsWhenEnabled`
- I rebuilt the pipeline tests and re-ran the targeted Qt-linked checks through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1):
  - `DualIsoPipeline.StandardPreviewScaleFourCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.RawUint16Prefetch*`
  - `DualIsoPipeline.Phase4Bv4_*`
- I rebuilt the user-facing release tree after the code change:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=2026-06-08 2:10:34 AM`
  - `Length=9110016`
  - `SHA256=9B8F8583E2BE9AA15B9C95E0EB0C8CC0B991581E67AE50A7BCF7DE2A90F558A8`
- I reran the full same-build standard M16 smoke matrix across `x1`, `x2`, `x4`, and `x8` on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- All 12 screenshot-backed runs stayed `colorArtifactScan.verdict=clear-heuristic`.
- I manually inspected the presented-frame screenshots for the new `x4` control and the `x8` canary:
  - `x4` control: visually clean, no new magenta/pink/green bars or blocks
  - `x8` canary: matched the earlier restored-baseline pattern visually, again with no new artifact

### Cross-checked from prior analysis

- The new scale-4 gate is a real keeper:
  - `x4` improved materially, and the other lanes did not show a new visual regression.
- New matrix averages:
  - `x1`: presented `4.611 fps`, timeline `23.448 fps`, GUI `4.267 fps`, render `174.461 ms` (`5.73 fps-equiv`), `llrawproc 47.906 ms` (`20.87 fps-equiv`), `processed16 163.942 ms` (`6.10 fps-equiv`), shadows/highlights prep `32.228 ms` (`31.03 fps-equiv`)
  - `x2`: presented `9.818 fps`, timeline `23.359 fps`, GUI `8.700 fps`, render `73.524 ms` (`13.60 fps-equiv`), `llrawproc 17.585 ms` (`56.86 fps-equiv`), `processed16 66.270 ms` (`15.09 fps-equiv`), shadows/highlights prep `10.299 ms` (`97.09 fps-equiv`)
  - `x4`: presented `11.216 fps`, timeline `23.447 fps`, GUI `10.600 fps`, render `55.268 ms` (`18.09 fps-equiv`), `llrawproc 12.793 ms` (`78.17 fps-equiv`), `processed16 48.328 ms` (`20.69 fps-equiv`), shadows/highlights prep `8.790 ms` (`113.77 fps-equiv`)
  - `x8`: presented `11.636 fps`, timeline `23.530 fps`, GUI `12.367 fps`, render `52.149 ms` (`19.18 fps-equiv`), `llrawproc 12.467 ms` (`80.21 fps-equiv`), `processed16 44.919 ms` (`22.26 fps-equiv`), shadows/highlights prep `8.003 ms` (`124.95 fps-equiv`)
- Relative to the prior matrix, `x4` is the biggest win from this round; `x2` also improved slightly, and `x8` stayed visually acceptable despite the green-heavy canary clip still being a heuristic outlier.

### Needs runtime profiling

- The next bottleneck remains the same shared processing tail, but `x4` is now clearly the best balanced lane to keep optimizing from.
- The `x8` canary still needs manual inspection on each future candidate, because the heuristic scan can overfire on `M16-1327` even when the presented frame looks consistent with baseline.

# 2026-06-08 - corrected lane-mapped x1/x2/x4 matrix keeps the throughput gains, and x8 still matches the old canary look

### Verified locally

- I rebuilt the user-facing release tree after the x4 timing-probe test adjustment:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=2026-06-08 2:42:04 AM`
  - `Length=9110528`
  - `SHA256=9B545A67CD1937F35616F5AB5694157CDDD5642109E0F7C4D401BC417D91A747`
- I reran the full same-build standard M16 smoke matrix across `x1`, `x2`, `x4`, and `x8` on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- I corrected the lane mapping after the first pass exposed an env-binding mistake, then reran the matrix with explicit lane config so each scale got the right quarter-res gate.
- All 12 screenshot-backed runs finished `validation.ok=true`.
- The screenshot color scan stayed `clear-heuristic` on `x1`, `x2`, and `x4`. `M16-1327` at `x8` still tripped the heuristic scan, but manual review of the presented frame matched the older x8 canary look and did not show a new magenta/pink/green bar or block artifact.

### Cross-checked from prior analysis

- The corrected matrix is the strongest keeper so far:
  - `x1`: presented `5.82 fps`, timeline `23.38 fps`, GUI `0.93 fps`, render `142.67 ms` (`7.01 fps-equiv`), `llrawproc 40.14 ms` (`24.91 fps-equiv`), `processed16 132.68 ms` (`7.54 fps-equiv`), shadows/highlights prep `20.13 ms` (`49.68 fps-equiv`)
  - `x2`: presented `10.02 fps`, timeline `23.27 fps`, GUI `0.90 fps`, render `69.75 ms` (`14.34 fps-equiv`), `llrawproc 17.23 ms` (`58.03 fps-equiv`), `processed16 62.15 ms` (`16.09 fps-equiv`), shadows/highlights prep `10.14 ms` (`98.62 fps-equiv`)
  - `x4`: presented `11.30 fps`, timeline `23.39 fps`, GUI `1.03 fps`, render `55.44 ms` (`18.04 fps-equiv`), `llrawproc 12.09 ms` (`82.72 fps-equiv`), `processed16 48.47 ms` (`20.63 fps-equiv`), shadows/highlights prep `9.02 ms` (`110.86 fps-equiv`)
  - `x8`: presented `12.88 fps`, timeline `23.44 fps`, GUI `1.17 fps`, render `42.09 ms` (`23.76 fps-equiv`), `llrawproc 8.34 ms` (`119.90 fps-equiv`), `processed16 35.59 ms` (`28.10 fps-equiv`), shadows/highlights prep `6.48 ms` (`154.32 fps-equiv`)
- Relative to the previous restored baseline, every lane improved on presentation cadence and the shared processing tail moved in the right direction:
  - `x1` picked up the most from its own quarter-res gate
  - `x2` stayed balanced and still looks like the safest throughput lane
  - `x4` kept the biggest mid-lane win
  - `x8` is still the visual canary, but the inspected presented frames matched the existing canary pattern rather than introducing a new artifact

### Needs runtime profiling

- The next bottleneck is still the shared processing tail, but the corrected matrix now keeps the x1/x2/x4 gains without giving back x8 throughput.
- A future candidate should keep the x8 canary screenshot review in the loop, because the heuristic scan can flag the green-heavy clip even when the frame looks consistent with the older baseline.

# 2026-06-08 - raw-prefetch standard preview expansion keeps x4/x8 strong, x1 improves, and x2 is the only mixed lane

### Verified locally

- I extended the raw uint16 prefetch lookahead into standard preview playback for `x1`, `x2`, `x4`, and `x8` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c), with the lookahead now mapping to `4`, `6`, `8`, and `8` respectively when standard preview mode is active.
- I added the matching standard-preview regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp):
  - `DualIsoPipeline.RawUint16PrefetchLookaheadExpandsForStandardScaleOneTwoFourAndEight`
- I rebuilt and revalidated the pipeline test suite through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1):
  - `DualIsoPipeline.RawUint16PrefetchLookaheadExpandsForStandardScaleOneTwoFourAndEight`
  - `DualIsoPipeline.StandardPreviewScaleOneCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleTwoCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleFourCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.Phase4Bv4_*`
- I reran the full same-build standard M16 smoke matrix on the standard trio:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- All 12 screenshot-backed runs completed and produced telemetry JSON under [`20260608-raw-prefetch-standard-matrix-v2`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-raw-prefetch-standard-matrix-v2).
- I manually inspected the x8 presented screenshots and they matched the older canary look rather than introducing a new magenta/pink/green artifact pattern.

### Cross-checked from prior analysis

- New matrix averages from the standard-preview prefetch expansion:
  - `x1`: presented `5.961 fps`, timeline `23.380 fps`, GUI `7.367 fps`, render `138.872 ms` (`7.20 fps-equiv`), `llrawproc 39.186 ms` (`25.52 fps-equiv`), `processed16 129.028 ms` (`7.75 fps-equiv`), shadows/highlights prep `18.773 ms` (`53.27 fps-equiv`), `raw_prefetch_hits 74.667`
  - `x2`: presented `9.271 fps`, timeline `23.277 fps`, GUI `12.333 fps`, render `77.902 ms` (`12.84 fps-equiv`), `llrawproc 21.212 ms` (`47.14 fps-equiv`), `processed16 70.475 ms` (`14.19 fps-equiv`), shadows/highlights prep `11.099 ms` (`90.10 fps-equiv`), `raw_prefetch_hits 160.333`
  - `x4`: presented `12.582 fps`, timeline `23.393 fps`, GUI `10.267 fps`, render `45.288 ms` (`22.08 fps-equiv`), `llrawproc 9.611 ms` (`104.04 fps-equiv`), `processed16 38.939 ms` (`25.68 fps-equiv`), shadows/highlights prep `6.875 ms` (`145.45 fps-equiv`), `raw_prefetch_hits 259.667`
  - `x8`: presented `12.984 fps`, timeline `23.486 fps`, GUI `12.667 fps`, render `42.333 ms` (`23.62 fps-equiv`), `llrawproc 8.336 ms` (`119.96 fps-equiv`), `processed16 35.855 ms` (`27.89 fps-equiv`), shadows/highlights prep `6.408 ms` (`156.05 fps-equiv`), `raw_prefetch_hits 252.667`
- Relative to the prior corrected matrix, `x1` improved a bit, `x4` and `x8` stayed strong, and `x2` was the only lane that moved less cleanly.
- The x8 `M16-1327` canary still trips the heuristic scan, but manual review matched the older canary look and did not show a new artifact.

### Needs runtime profiling

- The shared processing tail remains the bottleneck, especially `processed16` and shadows/highlights prep.
- The next change should either recover the x2 drag or target a different shared-path improvement that preserves the stronger x4/x8 lane behavior.

# 2026-06-08 - x2 raw-prefetch bump lifts the whole matrix and keeps the x8 canary visually consistent

### Verified locally

- I bumped the standard-preview raw uint16 prefetch lookahead for scale 2 from `6` to `8` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c), while keeping the scale 1, 4, and 8 schedule unchanged.
- I updated the matching Qt-linked regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp) so the aggressive and standard prefetch tests now expect scale 2 to return `8`.
- I rebuilt both the pipeline tests and the user-facing release tree, then re-ran the focused Qt-linked regressions:
  - `DualIsoPipeline.RawUint16Prefetch*`
  - `DualIsoPipeline.StandardPreviewScaleTwoCanUseQuarterResShadowsHighlightsWhenEnabled`
- I reran the full same-build standard M16 matrix on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- All 12 smoke runs completed under [`20260608-x2-prefetch-bump-matrix`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x2-prefetch-bump-matrix).
- I manually inspected the x8 canary presented frames for `M16-1327`, `M16-1347`, and `M16-1446`. They matched the older canary look and did not show a new magenta/pink/green bar or block artifact.

### Cross-checked from prior analysis

- New matrix averages after the x2 lookahead bump:
  - `x1`: presented `5.66 fps`, timeline `23.36 fps`, GUI `5.30 fps`, render `146.39 ms` (`6.83 fps-equiv`), `llrawproc 40.86 ms` (`24.48 fps-equiv`), `processed16 136.19 ms` (`7.34 fps-equiv`), shadows/highlights prep `19.91 ms` (`50.23 fps-equiv`), `raw_prefetch_hits 70.33`
  - `x2`: presented `10.98 fps`, timeline `23.27 fps`, GUI `11.33 fps`, render `60.46 ms` (`16.54 fps-equiv`), `llrawproc 13.99 ms` (`71.48 fps-equiv`), `processed16 53.65 ms` (`18.64 fps-equiv`), shadows/highlights prep `7.95 ms` (`125.78 fps-equiv`), `raw_prefetch_hits 232.00`
  - `x4`: presented `11.64 fps`, timeline `23.36 fps`, GUI `10.77 fps`, render `51.78 ms` (`19.31 fps-equiv`), `llrawproc 11.11 ms` (`89.98 fps-equiv`), `processed16 44.73 ms` (`22.36 fps-equiv`), shadows/highlights prep `6.74 ms` (`148.37 fps-equiv`), `raw_prefetch_hits 236.00`
  - `x8`: presented `13.53 fps`, timeline `23.49 fps`, GUI `10.97 fps`, render `38.55 ms` (`25.94 fps-equiv`), `llrawproc 7.13 ms` (`140.25 fps-equiv`), `processed16 32.37 ms` (`30.89 fps-equiv`), shadows/highlights prep `6.12 ms` (`163.40 fps-equiv`), `raw_prefetch_hits 270.33`
- Relative to the prior standard-preview matrix, every lane improved in presentation cadence and throughput, with `x2` showing the biggest recovery from the mixed lane.
- The x8 `M16-1327` canary still trips the heuristic scan, but the manual presented-frame review matched the older baseline rather than revealing a new artifact.

### Needs runtime profiling

- The shared processing tail remains the bottleneck, but the x2 recovery means the current standard-preview prefetch schedule is now the strongest keeper matrix so far.
- Future changes should keep the x8 canary review in the loop, because the heuristic scan remains sensitive on the green-heavy clip even when the visual result stays consistent with baseline.

# 2026-06-08 - Standard processed8 prefetch gate remains inert because direct8 still does not activate in playback preview

### Verified locally

- I widened the standard-preview processed8 prefetch gate in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) so `getMlvProcessed8PrefetchEnabledForTesting()` returns true for scale `2`, `4`, and `8` when preview mode is already active.
- I added the matching pipeline regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp):
  - `DualIsoPipeline.Processed8PrefetchEnablesStandardScaleTwoFourAndEight`
- I rebuilt the pipeline tests and the release tree, then re-ran the focused Qt-linked regressions:
  - `DualIsoPipeline.RawUint16Prefetch*`
  - `DualIsoPipeline.Processed8PrefetchEnablesAggressiveScaleOneTwoAndFour`
  - `DualIsoPipeline.Processed8PrefetchEnablesStandardScaleTwoFourAndEight`
- I reran the full same-build standard M16 smoke matrix on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- All 12 smoke runs completed under [`20260608-processed8-standard-matrix`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-processed8-standard-matrix).
- I manually inspected the x8 presented screenshots for all three clips. They still matched the older canary look and did not show a new magenta/pink/green bar or block artifact.

### Cross-checked from prior analysis

- New matrix averages from the standard-preview processed8 gate:
  - `x1`: presented `5.840 fps`, timeline `23.379 fps`, GUI `5.167 fps`, render `143.552 ms` (`6.97 fps-equiv`), `llrawproc 37.210 ms` (`26.87 fps-equiv`), `processed8 141.662 ms` (`7.06 fps-equiv`), `processed16 134.207 ms` (`7.45 fps-equiv`), shadows/highlights prep `27.233 ms` (`36.72 fps-equiv`), `raw_prefetch_hits 80.00`, `processed8_prefetch_hits 0.00`
  - `x2`: presented `10.699 fps`, timeline `23.269 fps`, GUI `10.000 fps`, render `63.786 ms` (`15.68 fps-equiv`), `llrawproc 15.260 ms` (`65.53 fps-equiv`), `processed8 60.387 ms` (`16.56 fps-equiv`), `processed16 56.949 ms` (`17.56 fps-equiv`), shadows/highlights prep `10.014 ms` (`99.86 fps-equiv`), `raw_prefetch_hits 220.00`, `processed8_prefetch_hits 0.00`
  - `x4`: presented `11.968 fps`, timeline `23.369 fps`, GUI `11.667 fps`, render `50.673 ms` (`19.73 fps-equiv`), `llrawproc 10.126 ms` (`98.76 fps-equiv`), `processed8 46.799 ms` (`21.37 fps-equiv`), `processed16 43.946 ms` (`22.76 fps-equiv`), shadows/highlights prep `9.619 ms` (`103.96 fps-equiv`), `raw_prefetch_hits 253.33`, `processed8_prefetch_hits 0.00`
  - `x8`: presented `13.549 fps`, timeline `23.490 fps`, GUI `10.667 fps`, render `38.684 ms` (`25.85 fps-equiv`), `llrawproc 7.781 ms` (`128.52 fps-equiv`), `processed8 35.156 ms` (`28.44 fps-equiv`), `processed16 32.544 ms` (`30.73 fps-equiv`), shadows/highlights prep `5.493 ms` (`182.05 fps-equiv`), `raw_prefetch_hits 278.67`, `processed8_prefetch_hits 0.00`
- Relative to the prior standard-preview matrix, the new processed8 gate did not activate in runtime playback. The direct8 path is still blocked by the preview local-tone guard in [`src/processing/raw_processing.c`](C:/!Layi%20Wkspc/MLV-App/src/processing/raw_processing.c), so the processed8 prefetch worker never records a hit in the standard smoke path.
- The runtime matrix is mixed rather than a clear keeper: `x1` and `x4` improved a bit, `x2` slipped, and `x8` stayed visually acceptable.

### Needs runtime profiling

- The processed8 gate is currently latent until direct8 eligibility is widened safely for playback preview.
- The next bottleneck is still the shared processing tail, but the actionable blocker is now the direct8 local-tone gate rather than the processed8 prefetch gate itself.
- Future changes should keep the x8 canary review in the loop, because the heuristic scan can still flag the green-heavy clip even when the visual result stays consistent with baseline.

# 2026-06-08 - Standard preview x1 quarter-res gate is a keeper-safe default-on improvement

### Verified locally

- I changed the standard-preview quarter-res shadows/highlights gate so scale `1` is now default-on in playback preview unless `MLVAPP_DISABLE_STANDARD_X1_SH_QUARTERRES` is set.
- I added the matching regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp):
  - `DualIsoPipeline.StandardPreviewScaleOneUsesQuarterResShadowsHighlightsByDefault`
- I rebuilt the user-facing release tree and re-ran the focused Qt-linked regressions:
  - `DualIsoPipeline.StandardPreviewScaleOneUsesQuarterResShadowsHighlightsByDefault`
  - `DualIsoPipeline.StandardPreviewScaleOneCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleTwoCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleFourCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.RawUint16Prefetch*`
  - `DualIsoPipeline.Phase4Bv4_*`
- I reran the full same-build standard M16 smoke matrix on:
  - `M16-1327`
  - `M16-1347`
  - `M16-1446`
- All 12 smoke runs completed under [`20260608-x1-default-quarterres-matrix-30s`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x1-default-quarterres-matrix-30s).
- I manually inspected representative presented frames from the x1, x4, and x8 lanes. The frames looked consistent with baseline and I did not see a new magenta/pink/green bar or block artifact.

### Cross-checked from prior analysis

- New matrix averages from the x1 default quarter-res change:
  - `x1`: presented `3.76 fps`, timeline `23.36 fps`, GUI `4.10 fps`, render `234.74 ms` (`4.26 fps-equiv`), `llrawproc 38.14 ms` (`26.22 fps-equiv`), `processed8 233.07 ms` (`4.29 fps-equiv`), `processed16 226.37 ms` (`4.42 fps-equiv`), shadows/highlights prep `119.57 ms` (`8.37 fps-equiv`)
  - `x2`: presented `5.26 fps`, timeline `23.29 fps`, GUI `4.80 fps`, render `161.82 ms` (`6.18 fps-equiv`), `llrawproc 12.65 ms` (`79.05 fps-equiv`), `processed8 158.54 ms` (`6.31 fps-equiv`), `processed16 155.52 ms` (`6.43 fps-equiv`), shadows/highlights prep `111.97 ms` (`8.93 fps-equiv`)
  - `x4`: presented `5.78 fps`, timeline `23.38 fps`, GUI `6.27 fps`, render `146.49 ms` (`6.83 fps-equiv`), `llrawproc 6.46 ms` (`154.80 fps-equiv`), `processed8 143.26 ms` (`6.98 fps-equiv`), `processed16 140.93 ms` (`7.10 fps-equiv`), shadows/highlights prep `111.39 ms` (`8.98 fps-equiv`)
  - `x8`: presented `11.42 fps`, timeline `23.49 fps`, GUI `8.50 fps`, render `61.70 ms` (`16.21 fps-equiv`), `llrawproc 5.22 ms` (`191.57 fps-equiv`), `processed8 57.98 ms` (`17.25 fps-equiv`), `processed16 55.58 ms` (`17.99 fps-equiv`), shadows/highlights prep `32.08 ms` (`31.17 fps-equiv`)
- `raw_prefetch_hits` averaged `52` at `x1`, `78` at `x2`, `69` at `x4`, and `226` at `x8`.
- `processed8_direct_path_frames` stayed `0` and `processed8_prefetch_hits` stayed `0`, so this branch is still not opening the direct8 preview path. The win is coming from a safer quarter-res prep split.

### Needs runtime profiling

- This change is keeper-safe because it improved the slow lane without introducing a visual regression in the 30-second matrix.
- The next bottleneck is still the shared processing tail, especially `processed16` and shadows/highlights prep, but the x1 lane now has a better default profile for standard preview.
# 2026-06-08 - x4 default quarter-res attempt was rejected because the x8 canary corrupted, and the reverted baseline is clean again

### Verified locally

- I tried making the standard-preview scale 4 quarter-res shadows/highlights gate default-on in [`src/processing/raw_processing.c`](C:/!Layi%20Wkspc/MLV-App/src/processing/raw_processing.c), and I added the matching regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp):
  - `DualIsoPipeline.StandardPreviewScaleFourUsesQuarterResShadowsHighlightsByDefault`
- I rebuilt the pipeline tests and the user-facing release tree, then reran the targeted Qt-linked checks through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1):
  - `DualIsoPipeline.StandardPreviewScaleOneUsesQuarterResShadowsHighlightsByDefault`
  - `DualIsoPipeline.StandardPreviewScaleOneCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleTwoCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleFourCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.RawUint16Prefetch*`
  - `DualIsoPipeline.Processed8PrefetchEnablesStandardScaleTwoFourAndEight`
  - `DualIsoPipeline.Phase4Bv4_*`
- I reran the full same-build standard M16 smoke matrix under [`20260608-x4-default-quarterres-matrix`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x4-default-quarterres-matrix).
- The `M16-1327` x8 canary presented frame was visually corrupted with the same blocky/broken look we rejected in the earlier x2-default attempt, so this x4-default branch is not keeper-safe.
- I reverted the x4 default-on change back to the safer opt-in gate, rebuilt again, and ran an x8 revert-check smoke under [`20260608-x4-default-quarterres-revert-check`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x4-default-quarterres-revert-check).
- The revert-check is clean:
  - `validation.ok=true`
  - `colorArtifactScan.verdict=clear-heuristic`
  - `smokePresentedFps=13.884`
  - `timeline_fps=23.391`
  - `visibleBottomLeftGuiFps=13.0`
  - `avg_render_total_ms=39.211 ms` (`25.50 fps-equiv`)
  - `avg_llrawproc_ms=7.551 ms` (`132.43 fps-equiv`)
  - `avg_processed8_ms=35.680 ms` (`28.03 fps-equiv`)
  - `avg_processed16_ms=32.998 ms` (`30.31 fps-equiv`)
  - `avg_processing_shadows_highlights_prep_ms=6.134 ms` (`163.03 fps-equiv`)
  - `processed8_direct_path_frames=0`
  - `processed8_prefetch_hits=0`
  - `raw_prefetch_hits=270`
- I manually reviewed the revert-check presented frame and it no longer showed the rejected blocky corruption.
- The current keeper baseline remains the x1-default quarter-res change, not the rejected x4-default branch.

### Cross-checked from prior analysis

- The x4-default attempt did not survive the x8 visual risk check, so it should not be used as the new baseline.
- The reverted baseline is still the safer operating point while the next shared-processing bottleneck is investigated.

# 2026-06-08 - x2 default quarter-res attempt was rejected because the x8 canary corrupted, and the restored x1-default baseline stays clean

### Verified locally

- I tried making the standard-preview scale 2 quarter-res shadows/highlights gate default-on in [`src/processing/raw_processing.c`](C:/!Layi%20Wkspc/MLV-App/src/processing/raw_processing.c), and I added the matching regression in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp):
  - `DualIsoPipeline.StandardPreviewScaleTwoUsesQuarterResShadowsHighlightsByDefault`
- I rebuilt the pipeline tests and the user-facing release tree, then reran the targeted Qt-linked checks through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1):
  - `DualIsoPipeline.StandardPreviewScaleTwoUsesQuarterResShadowsHighlightsByDefault`
  - `DualIsoPipeline.StandardPreviewScaleOneUsesQuarterResShadowsHighlightsByDefault`
  - `DualIsoPipeline.StandardPreviewScaleOneCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleTwoCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.StandardPreviewScaleFourCanUseQuarterResShadowsHighlightsWhenEnabled`
  - `DualIsoPipeline.RawUint16Prefetch*`
  - `DualIsoPipeline.Phase4Bv4_*`
- I reran the full same-build standard M16 smoke matrix under [`20260608-x2-default-quarterres-matrix-30s`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x2-default-quarterres-matrix-30s).
- The matrix was numerically mixed, but the `M16-1327` x8 canary screenshot was visually unacceptable: it showed a severe blocky/corrupted presentation that did not match the older baseline canary look.
- I then reverted the x2 default-on change back to the safer opt-in gate, rebuilt again, and ran an x8 revert-check smoke under [`20260608-x2-default-revert-check`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x2-default-revert-check).
- The revert-check was clean:
  - `validation.ok=true`
  - `colorArtifactScan.verdict=clear-heuristic`
  - `smokePresentedFps=10.475`
  - `timeline_fps=23.335`
  - `visibleBottomLeftGuiFps=7.9`
  - `avg_render_total_ms=68.000 ms` (`14.71 fps-equiv`)
  - `avg_llrawproc_ms=6.970 ms` (`143.33 fps-equiv`)
  - `avg_processed8_ms=63.539 ms` (`15.74 fps-equiv`)
  - `avg_processed16_ms=60.985 ms` (`16.40 fps-equiv`)
  - `avg_processing_shadows_highlights_prep_ms=35.269 ms` (`28.35 fps-equiv`)
  - `processed8_direct_path_frames=0`
  - `processed8_prefetch_hits=0`
  - `raw_prefetch_hits=220`
- I manually reviewed the revert-check presented frame and it no longer showed the corrupted x8 block pattern.
- The current keeper baseline is still the x1-default quarter-res change, not the rejected x2-default branch.

### Cross-checked from prior analysis

- The x2-default attempt did not survive the x8 visual risk check, so it should not be used as the new baseline.
- The reverted x1-default baseline remains the safer operating point while the next shared-processing bottleneck is investigated.

# 2026-06-08 - x4/x8 raw-prefetch widen was rejected; x2 bump remains the keeper-safe baseline

### Verified locally

- I widened the standard-preview raw uint16 lookahead from `8` to `10` for `x4` and `x8` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c), and I updated the matching expectations in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- I rebuilt the pipeline tests and the user-facing release tree, then ran the focused Qt-linked regression filter through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1). The targeted pipeline checks passed.
- I ran the full same-build standard M16 matrix under [`20260608-x4x8-lookahead-matrix`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x4x8-lookahead-matrix) across `M16-1327`, `M16-1347`, and `M16-1446` at `1x`, `2x`, `4x`, and `8x`.
- The widened `x4/x8` branch was a regression versus the prior keeper baseline, especially on the mid lanes, so I reverted `x4` and `x8` back to `8` and left the earlier `x2=10` bump intact.
- I then ran a restore check under [`20260608-restore-check`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-restore-check) to make sure the rollback landed cleanly.
- The restore check was visually clean on `x4`, and the `x8` canary still matched the older baseline look even though the heuristic scan flagged it as `suspect-block-or-bar`.

### Cross-checked from prior analysis

- The `x2=10` raw-prefetch bump remains the keeper-safe improvement from this sequence.
- The `x4/x8=10` widen should not become the new baseline because it regressed the matrix and did not improve the overall playback envelope.
- The current keeper baseline stays at `x1=4`, `x2=10`, `x4=8`, `x8=8` in `mlv_raw_uint16_prefetch_lookahead_for_request()`.

### Needs runtime profiling

- The next bottleneck is still the shared processing tail rather than draw/present.
- Any future raw-prefetch tweak should be compared against the `x2=10` keeper baseline and must keep the x8 canary review in the loop.

# 2026-06-08 - x2 raw-prefetch 11 was rejected; x2=10 remains the keeper-safe baseline

### Verified locally

- I tried widening the standard-preview `x2` raw uint16 lookahead from `10` to `11` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c), and I updated the matching expectations in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- I rebuilt the pipeline tests and the user-facing release tree, then reran the focused Qt-linked regression filter through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1). The targeted prefetch checks passed.
- I ran a reduced same-build smoke set under [`20260608-x2-prefetch-bump-matrix-v3`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-x2-prefetch-bump-matrix-v3) covering the three standard M16 clips at `x2` plus the `x8` canary clips.
- The `x2=11` branch regressed versus the keeper `x2=10` baseline: `x2` lost cadence and the shared tail got slower, while `x8` stayed visually consistent with the older canary look but still triggered the heuristic on one canary.
- I reverted the `x2` lookahead back to `10`, rebuilt again, and reran the focused Qt-linked prefetch checks to confirm the rollback landed cleanly.

### Cross-checked from prior analysis

- The `x2=10` raw-prefetch setting remains the keeper-safe baseline.
- The `x2=11` branch should not become the new baseline because it regressed the `x2` lane relative to `x2=10` and did not improve the overall playback envelope.

### Needs runtime profiling

- The next bottleneck is still the shared processing tail rather than draw/present.
- Future raw-prefetch tweaks should treat `x2=10` as the floor/anchor for comparison, and x8 should remain the visual canary when new candidates are tested.

# 2026-06-08 - x2 raw-prefetch 11 recheck found a new M16-1347 artifact and was reverted

### Verified locally

- I temporarily widened the standard-preview processed8 prefetch lookahead from `2` to `3` in [`src/mlv/video_mlv.c`](C:/!Layi%20Wkspc/MLV-App/src/mlv/video_mlv.c) and added a matching unit test in [`tests/pipeline/test_dual_iso_pipeline.cpp`](C:/!Layi%20Wkspc/MLV-App/tests/pipeline/test_dual_iso_pipeline.cpp).
- The release tree was rebuilt and the new executable fingerprint was:
  - [`platform\qt\build-release\release\MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)
  - `LastWriteTime=6/8/2026 5:09:29 PM`
  - `Length=9113600`
  - `SHA256=FB8BEA76D1A8ECF3916CAB711A485285B99121A346518BFBB28119560C1662EA`
- Qt-linked regression checks through [`tools\testing\run-windows-test.ps1`](C:/!Layi%20Wkspc/MLV-App/tools/testing/run-windows-test.ps1) passed for the pipeline and console slices I reran.
- The screenshot-backed smoke matrix across `M16-1327`, `M16-1347`, and `M16-1446` showed the new `x2` `M16-1347` run tripping `suspect-block-or-bar`, while the same clip was clear in the nearby same-build baseline at `20260608-standard-x2-quarterres-matrix`.
- Reverting the lookahead bump back to `2` cleared the `M16-1347` x2 artifact on the rebuilt release smoke.

### Cross-checked from prior analysis

- The previous `x2=10` raw-prefetch bump remains a valid keeper-safe improvement.
- The temporary `x2=11` lookahead bump was not safe enough to keep because it introduced a clip-specific visual regression on `M16-1347` x2.
- The `x8` canary still needs to stay in every future candidate loop, but the new regression was the x2 clip-specific artifact, not the established canary behavior.

### Needs runtime profiling

- The next bottleneck is still the shared processing tail rather than draw/present.
- Any future throughput tweak should be validated against the exact `M16-1347` x2 baseline that was clean before the temporary lookahead change, with x8 still used as the visual guardrail.

# 2026-06-08 - disk pressure is real, but screenshots amplify an already busy playback path

### Verified locally

- I sampled physical and logical disk counters during live `M16-1446` x2 playback on the current build.
- In the no-screenshot playback trace under [`20260608-disk-job-smoke-noscreenshot-2x`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-disk-job-smoke-noscreenshot-2x), the clip still produced sustained read bursts, including `105,221,826` bytes/sec read and `75` disk-time on `disk-counters.csv`.
- In the screenshot-backed x2 trace under [`20260608-disk-investigation`](C:/!Layi%20Wkspc/MLV-App/.claude-state/profiling/20260608-disk-investigation), the same clip peaked at roughly `95.851` physical disk-time, `3` queue depth, `115,730,995` bytes/sec read, and `61,758,523` bytes/sec write.
- The screenshot-backed `M16-1446` x2 smoke still stayed visually clean with `presented_fps=11.519`, `timeline_fps=23.352`, `gui_fps=10`, `avg_render_total_ms=54.943`, and `avg_processing_ms=20.340` on the same run.
- I reran the `M16-1327` x8 canary after the rollback, and it still tripped `suspect-block-or-bar` with the same broad, blocky corruption look as the earlier canary run.
- I probed the x4 quarter-res candidate by setting `MLVAPP_ENABLE_STANDARD_X4_SH_QUARTERRES=1` on the current build. The `M16-1347` x4 run slowed from the current baseline (`presented_fps=30.483`) to `presented_fps=20.730`, while the x8 `M16-1327` canary stayed on the same `suspect-block-or-bar` pattern.
- I validated the current x1/x2 quarter-res default-on policy across the standard trio. The current build showed clean screenshot-backed smoke on all six x1/x2 runs, with the strongest gains on `M16-1327` and `M16-1446`:
  - `M16-1327` x1 `presented_fps=5.860` vs prior `5.223`
  - `M16-1327` x2 `presented_fps=14.846` vs prior `12.180`
  - `M16-1347` x1 `presented_fps=5.366` vs prior `5.338`
  - `M16-1347` x2 rerun `presented_fps=14.085` vs prior `13.328`
  - `M16-1446` x1 `presented_fps=5.342` vs prior `5.174`
  - `M16-1446` x2 `presented_fps=12.611` vs prior `11.519`
- A probe-enabled `M16-1327` x2 smoke on the same build showed the quarter-res stages actually contributing now: `render_total_ms=25.404`, `processing_ms=8.548`, `sh_prep_ms=9.148`, `sh_down_ms=1.975`, `sh_rbf_ms=5.680`, and `sh_up_ms=1.481`. That is a substantial improvement over the earlier probe baseline (`render_total_ms=80.600`, `processing_ms=33.218`).

### Cross-checked from prior analysis

- The SSD complaint is not just screenshot overhead: the playback path itself is issuing large reads.
- Screenshot capture and the surrounding smoke harness materially increase write pressure and can push the disk much closer to saturation, which explains the Resource Monitor view the user shared.
- The disk story still does not change the visual conclusion: the earlier x2 `M16-1347` regression was tied to the temporary lookahead bump, not to the disk behavior.
- The `M16-1327` x8 corruption is still the canary baseline after the rollback, so it should remain a guardrail rather than a new blocker against the current x2 work.
- The x4 quarter-res default-on idea is not a safe promotion candidate right now because it reduced x4 FPS instead of increasing it.
- The x1/x2 quarter-res default-on policy looks like a keeper-safe gain, while x4 should stay off by default.
- The remaining hotspot after the x1/x2 gain is now the broader processed16/processing tail, not the quarter-res shadows/highlights helper itself.

### Needs runtime profiling

- If the next throughput candidate targets I/O, it should be tested with and without screenshot capture so we can separate read-driven playback cost from harness write amplification.
- Otherwise, the next safe FPS gain should still prioritize the processing tail, with x8 retained as the visual canary.
- x4 quarter-res should stay off by default unless a different candidate path proves it can improve x4 without dragging x4 or x8.
- With x1/x2 now improved, the next bottleneck hunt should keep following the shared processing tail and related I/O pressure rather than reopening the rejected x4 lever.
- Future probes should compare against the new `M16-1327` x2 probe result (`25.404 ms` render total) instead of the older `80.600 ms` baseline when ranking the next bottleneck.
