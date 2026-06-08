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
