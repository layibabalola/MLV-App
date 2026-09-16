# PLAYBACK-ATTR-3-CUDA runbook

Within-run refresh-period + prep-region attribution for the CUDA playback path on Bachelor.
Bachelor cannot resolve a timing A/B below about 2x (A/A noise), so this measurement is judged
**within one run**: the share of presented frames landing on 1, 2, or 3+ display refreshes,
plus per-sub-region prep timing (`prep_region_setup/gpu/image/present/finish/total/unattributed`).

**The build is SPLIT across three hosts** (swarm ruling 2026-09-16,
`.claude-state/fleet-runs/swarm-attr3-buildhost-20260916T2150Z/SYNTHESIS.md`). Bachelor -- the
measurement host -- has Visual Studio without the VC tools component (no `cl.exe`) and no CUDA
toolkit, so nothing compiles there; Ultra-Magnus has CUDA 12.6 nvcc and an RTX 4090 but no qmake
or MinGW. So: CUDA DLL pair on Ultra-Magnus, Qt exe on the board host, package staged into the
Bachelor cache, measurement on Bachelor. `playback-attr-3-cuda-compile-job.ps1` (compile on
Bachelor) is **retired and refuses to emit**; installing a toolchain on Bachelor was rejected as
an unnecessary system change to the owner's laptop.

> **TRAP: a precedent script is not a precedent result.** Before generating a job for any remote
> host, cite the precedent's `result.json` `exitCode` 0, and cite a fresh toolchain probe receipt
> from that host. The PR #131 lane prompt called the failed precedent a job that "ran"; nobody
> read its `result.json` (exit 5, `msvcDiscovery`). `safe_to_submit_to_bachelor` certified script
> safety, not toolchain existence.

**Footage (NA-4).** `docs/never-authorized.json` NA-4 lets a lane open exactly one real clip:
the single full canonical path on the `CLIP_OR_NONE:` line of that lane's own prompt
(`MLV_LANE_PROMPT`). Agent sessions cannot write that line, so the **owner types it by hand**.
Only step 5 touches footage; steps 1-4 compile, hash and move build artifacts and never name a
media file. Adjudication: `.claude-state/fleet-runs/swarm-footage-route-20260916T2020Z/SYNTHESIS.md`.

**Submission (NA-7).** Direct hooked writes to `\\bachelor\...` are refused. The tracked
submitter `tools/profiling/um-run.ps1` is the route, and the agents execute only `inbox\*.job.ps1`
while leaving other inbox files alone. Do not route around NA-7 by hiding paths in variables.

## 0. Toolchain probe first

Probe the host you are about to target, read-only, through `tools/profiling/um-run.ps1`, and keep
the receipt: nvcc version and `nvidia-smi` on Ultra-Magnus; `qmake -version` and
`mingw32-make --version` on the board host. A probe older than the current session proves nothing.

## 1. CUDA DLL pair (Ultra-Magnus)

```powershell
pwsh -NoProfile -File tools\profiling\ultramagnus\playback-attr-3-cuda-dll-job.ps1 `
    -SourceCommit <40-hex-sha> -OutDir <staging-dir> [-CudaArchitectures sm_86]
```

Writes `<jobId>-source.zip` (a `git archive` of the commit) and `<jobId>.job.ps1`
(`jobId = playback-attr-3-cuda-dllpair-<sha12>`). Drop both into the Ultra-Magnus agent inbox
(`G:\Temp\mlv-gpu-profile\agent\inbox`). On that host the job:

1. builds **both** DLLs with the tracked backend scripts from the extracted source --
   `tools/gpu/backend/build-backend-dll.ps1` (`igpu_recon_cuda.dll`) and
   `tools/gpu/backend/amaze-debayer-dll.ps1` (`igpu_amaze_debayer_cuda.dll`);
2. **targets `sm_86`** and re-proves it on the emitted bytes with `cuobjdump --list-elf`. The
   older Ultra-Magnus jobs target `sm_89`, which Bachelor's GPU cannot run at all; a requested
   flag is not an emitted cubin, so the flag alone is never accepted;
3. copies `cudart64_12.dll` from the same toolkit that compiled the DLLs;
4. records `dumpbin /exports` for both DLLs and computes **`pendingSymbolPresence`** (the
   `igpu_recon_` export test -- this is where it now lives; Bachelor has no tool to run it);
5. publishes into `outbox\<jobId>.artifacts`, transactionally (`.partial` -> verify -> rename),
   with `dll-pair-manifest.json` written **LAST**:
   `{sourceCommit, cudaArch, nvccVersion, files{name: lowercase sha256}, pendingSymbolPresence,
   builtOnHost}`.

Per-step exit codes: 3 source archive, 4 backend sources, 5 MSVC, 6 CUDA toolkit, 7 CUDA runtime,
11 recon build, 12 AMaZE build/exports, 13 parity harness (only with `-RequireParityHarness`),
14 architecture proof, 15 export inspection, 16 recon exports absent, 20-22 publish.

The recon backend script ends by running its LoadLibrary parity harness against oracle vectors
and exits with *its* code; without those vectors on the host that code is nonzero even when the
DLL is fine. It is therefore **recorded** (`reconBackendScript.exitCode`, `parityHarnessPassed`)
and not fatal unless the generator was run with `-RequireParityHarness`. The DLL itself is gated
on artifact presence, architecture and exports instead.

## 2. Exe and package (board host)

Fetch the pair job's artifacts directory, then:

```powershell
pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-assemble.ps1 `
    -SourceCommit <same-40-hex-sha> -DllPairDir <pair-artifacts-dir> -OutDir <build-dir>
```

It verifies the pair manifest (`sourceCommit` equal to `-SourceCommit`, every file's sha256
matching the bytes on disk, a boolean `pendingSymbolPresence`) **before** deploying anything,
then builds the Release exe from a clean `git archive`: `build_buildinfo.h` injected into the
build dir before `qmake` (an archive has no `.git`, so the qmake-time probe would stamp
"unknown"), `qmake`, `mingw32-make -B release`, `windeployqt --release --no-translations
--compiler-runtime`, plus the four MinGW runtime DLLs `--compiler-runtime` misses (`libgomp-1`
above all). A sanitized-PATH `--batch --help` launch probe proves the package is self-contained
before publication. `-RuntimeDonorDir` optionally mirrors the reviewed FFmpeg payload by exact
name; without it `ffmpegRuntimeDeployed=false` is recorded rather than implied.

Published into `<build-dir>`, `build.json` **LAST**:

- `MLVApp-playback-attr-3-cuda-<sha12>-pkg.zip` (the deployed release dir, zipped as-is -- the
  exe inside keeps its unrenamed build name, `MLVApp.exe`)
- `MLVApp-playback-attr-3-cuda-<sha12>.exe`
- `igpu_recon_cuda-playback-attr-3-cuda-<sha12>.dll`
- `playback-attr-3-cuda-<sha12>-build.json` -- the layout the attribution job verifies, plus
  `pendingSymbolPresence`, `dllPairManifestSha256` and `exeBuiltOnHost`.

## 3. Stage into the Bachelor cache

```powershell
pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-stage-job.ps1 `
    -SourceCommit <same-40-hex-sha> -BuildDir <build-dir> -OutDir <staging-dir>
```

Drop the emitted job into `C:\mlvtmp\mlv-agent\inbox` together with the four published files, by
their own names, as side-files. The job re-hashes each one against values baked in at generation
time, cross-checks the arriving `build.json`, publishes into `C:\mlvtmp\mlv-agent\cache`
transactionally with `build.json` **LAST**, and only then removes the side-files. Exit codes:
3 side-file missing, 4 hash mismatch, 5 manifest binding, 20-22 publish.

## 4. Attribution job (inside the owner-granted lane)

Run this **only inside a lane whose prompt carries the owner-typed `CLIP_OR_NONE:` path**.
`-ClipPath` must be exactly that path.

```powershell
pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-job.ps1 `
    -SourceCommit <same-40-hex-sha> -ClipId <id> `
    -ClipPath '<the lane prompt CLIP_OR_NONE path>' -OutFile <staging-dir>\<jobId>.job.ps1
```

On Bachelor the emitted job refuses to run unless that path sits directly in the agent cache,
names that clip id, and exists. It requires the staged `build.json`, verifies all three cache
artifacts against it, and **reads `pendingSymbolPresence` from it** -- an absent or non-boolean
field is a refusal. PresentMon runs as a direct child, inheriting the job-owned TEMP; if the
agent account lacks ETW trace rights the job fails `PRESENTMON_FAILED rc=6` and never falls back
to the elevated scheduled task.

Before any verdict the job parses the run's own `gpu_playback_recon.eligibility` line and exits
**15 `BACKEND_NOT_AVAILABLE`** unless `cuda_backend_available=1` **and** `r16_available=1`,
recording both plus `r16_reason`. Other outcomes: 12 `VENUE_NOT_QUIESCENT`, 13
`GPU_RECON_FRAMES_ZERO`, 14 `CPU_FALLBACK_DETECTED`, 0 `MEASUREMENT_CAPTURED`. It publishes
`presentmon-series.csv`, `logs\mlvapp.log`, `evidence-manifest.json`, `provenance.json` and
`artifact-index.json`.

The owner's consent for the clip on this card is recorded at
`.claude-state/coordination/dual-lane/receipts/owner-footage-consent-20260916.json`, with its
`-correction.json`. That receipt is evidence of consent, never an authorization -- the
authorization is the owner-typed path line. The job cites the receipt's file name in
`evidence-manifest.json`.

## 5. Extract the histogram

Pull `presentmon-series.csv` and `logs\mlvapp.log` from the attribution artifacts, then:

```powershell
py -3 tools\profiling\refresh_period_histogram.py `
    --presentmon-csv <artifacts-dir>\presentmon-series.csv `
    --frame-log <artifacts-dir>\logs\mlvapp.log `
    --refresh-period-ms <Bachelor's panel refresh period in ms> `
    --out <artifacts-dir>\refresh-period-histogram.json
```

`--refresh-period-ms` is Bachelor's actual panel refresh period and must be supplied. Without it
the tool estimates the period from the interval distribution's near-minimum cluster -- an
estimate that cannot distinguish a healthy all-1-refresh capture from a uniformly-stalled
all-2-refresh one, so it fails closed (`"ambiguous"`) whenever too little of the distribution
falls outside that cluster to cross-check the assumption. `refreshPeriodSource` records which
path was taken: `"nominal"` when supplied, `"estimated-min-interval"` when not.

## 6. Reading the within-run verdict

The output JSON (`schema: mlvapp.refresh-period-histogram.v1`) has two sections:

- **`presentMon.buckets`** -- the share of presented frames landing on `"1"`, `"2"` or `"3+"`
  display refreshes. A healthy CUDA playback run should show `"1"` dominating `share`; a
  meaningful `"2"` or `"3+"` share is evidence of missed-refresh stalls.
- **`frameLog.regions`** -- `p50Ms`/`p95Ms` per `prep_region_*`. Whichever of
  `prep_region_setup/gpu/image/present/finish` has the largest share of `prep_region_total_ms`
  is the dominant cost; a large `prep_region_unattributed_ms` means the probes don't yet cover
  where the time is going.

Run the test suites before trusting a new extraction or a new job:

```powershell
py -3 -m pytest -q tools\profiling\test_refresh_period_histogram.py
py -3 -m pytest -q tools\repo_hygiene\test_playback_attr_3_cuda_split_build.py
```
