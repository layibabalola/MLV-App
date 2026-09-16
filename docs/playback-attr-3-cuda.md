# PLAYBACK-ATTR-3-CUDA runbook

Within-run refresh-period + prep-region attribution for the CUDA playback path on Bachelor.
Bachelor cannot resolve a timing A/B below about 2x (A/A noise), so this measurement is judged
**within one run**: the share of presented frames landing on 1, 2, or 3+ display refreshes,
plus per-sub-region prep timing (`prep_region_setup/gpu/image/present/finish/total/unattributed`).

**Footage (NA-4).** `docs/never-authorized.json` NA-4 lets a lane open exactly one real clip:
the single full canonical path on the `CLIP_OR_NONE:` line of that lane's own prompt
(`MLV_LANE_PROMPT`). Agent sessions cannot write that line, so the **owner types it by hand**.
The attribution job opens only the path passed as `-ClipPath`, which must equal that line.
Nothing resolves a clip id to a file, scans the cache, or builds an extension. A two-part
clip's continuation part is opened by the application itself. Adjudication:
`.claude-state/fleet-runs/swarm-footage-route-20260916T2020Z/SYNTHESIS.md`.

## 1. Build job

Generate the Bachelor build job for the commit under test:

```powershell
pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-compile-job.ps1 `
    -SourceCommit <40-hex-sha> -OutDir <staging-dir>
```

This writes `<jobId>-source.zip` (a `git archive` of the commit -- tracked files only) and
`<jobId>.job.ps1` (`jobId = playback-attr-3-cuda-build-<sha12>`) into `<staging-dir>`. Drop both
into `C:\mlvtmp\mlv-agent\inbox` on Bachelor. The job, once run there:

1. expands the source zip;
2. builds the CUDA backend DLL for `sm_86` with nvcc;
3. builds the Release MLV-App exe (qmake + mingw32-make against
   `C:\Qt\6.10.2\mingw_64` / `C:\Qt\Tools\mingw1310_64`);
4. deploys the Qt runtime next to the exe with `windeployqt`, plus the backend DLL and the
   matching `cudart64_*.dll`;
5. stages the result into `C:\mlvtmp\mlv-agent\cache`:
   - `MLVApp-playback-attr-3-cuda-<sha12>.exe`
   - `igpu_recon_cuda-playback-attr-3-cuda-<sha12>.dll`
   - `MLVApp-playback-attr-3-cuda-<sha12>-pkg.zip` (the whole deployed release dir, zipped as-is
     -- the exe inside keeps its unrenamed build name, `MLVApp.exe`)
6. writes `result.json` to `outbox\<jobId>.artifacts` with lowercase sha256 for the exe, the
   DLL, and the package zip, the source commit, tool versions (nvcc/qmake/gcc), and every
   build step's exit code.

## 2. Wait for the result

Poll `C:\mlvtmp\mlv-agent\outbox\<jobId>.artifacts\result.json` on Bachelor. A run that fails
any step exits with a step-specific nonzero code and still writes a partial `result.json`
recording which step failed. Do not submit the attribution job (step 3) until `result.json`
reports `exitCode = 0` and the three staged cache artifacts are present.

## 3. Attribution job (clip M16-1243, inside the owner-granted lane)

Run this **only inside a lane whose prompt carries the owner-typed `CLIP_OR_NONE:` path** for
M16-1243. `-ClipPath` must be exactly that path.

```powershell
pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-job.ps1 `
    -SourceCommit <same-40-hex-sha> -ClipId M16-1243 `
    -ClipPath '<the lane prompt CLIP_OR_NONE path>' -OutFile <staging-dir>\<jobId>.job.ps1
```

On Bachelor the emitted job refuses to run unless that path sits directly in the agent cache,
names clip `M16-1243`, and exists. PresentMon runs as a direct child of the job, so it inherits
the job-owned TEMP/TMP. If the agent account lacks ETW trace rights, the job fails
`PRESENTMON_FAILED rc=6`; it never falls back to the elevated scheduled task.

`-BasePackageZip` / `-BasePackageExeName` default to the package the build job just staged
(`MLVApp-playback-attr-3-cuda-<sha12>-pkg.zip` / `MLVApp.exe`), so no override is needed as
long as that exact commit's build job already succeeded. Drop the emitted job into the inbox
the same way. It runs the CUDA playback path against clip `M16-1243`, captures a PresentMon
sidecar series and the raw MLVApp log, and publishes:

- `presentmon-series.csv` (`msBetweenDisplayChange` per sample)
- `logs\mlvapp.log` (raw log with `playback_smoke.frame` probes)
- `evidence-manifest.json`, `provenance.json`, `artifact-index.json`

The owner's consent for this clip is recorded at
`.claude-state/coordination/dual-lane/receipts/owner-footage-consent-20260916.json`, with its
`-correction.json`. That receipt is evidence of consent, never an authorization. The
authorization is the owner-typed path line. The attribution job cites the receipt's file name
in `evidence-manifest.json`.

## 4. Extract the histogram

Pull `presentmon-series.csv` and `logs\mlvapp.log` from the attribution job's artifacts
directory, then run:

```powershell
py -3 tools\profiling\refresh_period_histogram.py `
    --presentmon-csv <artifacts-dir>\presentmon-series.csv `
    --frame-log <artifacts-dir>\logs\mlvapp.log `
    --refresh-period-ms <Bachelor's panel refresh period in ms> `
    --out <artifacts-dir>\refresh-period-histogram.json
```

`--refresh-period-ms` is Bachelor's actual panel refresh period and must be supplied.
Without it, the tool estimates the refresh period from the interval distribution's
near-minimum cluster -- an estimate that cannot distinguish a healthy all-1-refresh
capture from a uniformly-stalled all-2-refresh one, so it fails closed (`"ambiguous"`)
whenever too little of the distribution falls outside that cluster to cross-check the
assumption. `refreshPeriodSource` in the output records which path was taken:
`"nominal"` when supplied, `"estimated-min-interval"` when not.

## 5. Reading the within-run verdict

The output JSON (`schema: mlvapp.refresh-period-histogram.v1`) has two sections:

- **`presentMon.buckets`** -- the share of presented frames landing on `"1"`, `"2"`, or `"3+"`
  display refreshes (`refreshPeriodMs` is measured from the sample cluster near the observed
  minimum interval; `refreshPeriodMeasurement` says whether that came from the cluster's mode
  or its median). A healthy CUDA playback run should show `"1"` dominating `share`; a
  meaningful `"2"` or `"3+"` share is evidence of missed-refresh stalls.
- **`frameLog.regions`** -- `p50Ms`/`p95Ms` per `prep_region_*` for every high-resolution frame
  row. Compare the regions' `p50Ms` against each other: whichever of
  `prep_region_setup/gpu/image/present/finish` has the largest share of
  `prep_region_total_ms` is the dominant cost; a large `prep_region_unattributed_ms` means the
  probes don't yet cover where the time is going.

Run the test suite before trusting a new extraction:

```powershell
py -3 -m pytest -q tools\profiling\test_refresh_period_histogram.py
```
