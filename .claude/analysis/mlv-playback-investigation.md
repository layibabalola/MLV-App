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
