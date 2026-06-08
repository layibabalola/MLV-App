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
