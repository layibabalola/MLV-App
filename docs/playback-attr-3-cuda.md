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

0. **verifies the source archive before expanding it.** The generator prints
   `sourceArchiveSha256` and bakes it in; the job refuses at **exit 8** unless the arriving bytes
   hash to it *and* the commit id `git archive` stamps into the zip's EOCD comment equals
   `-SourceCommit`. Everything below is a claim about the extracted source, so the claim is
   pinned first. The verified sha is recorded as `dll-pair-manifest.json`'s
   `sourceArchiveSha256`. `pwsh -File <jobId>.job.ps1 -VerifyOnly` runs exactly this prefix and
   exits, writing nothing -- use it to confirm a drop landed intact before the agent picks it up;
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

Per-step exit codes: 3 source archive missing, **8 source archive does not bind**, 4 backend
sources, 5 MSVC, 6 CUDA toolkit, 7 CUDA runtime,
11 recon build, 12 AMaZE build/exports, 13 parity harness (only with `-RequireParityHarness`),
14 architecture proof, 15 export inspection, 16 recon exports absent, 20-22 publish.

The recon backend script ends by running its LoadLibrary parity harness against oracle vectors
and exits with *its* code; without those vectors on the host that code is nonzero even when the
DLL is fine. It is therefore **recorded** (`reconBackendScript.exitCode`, `parityHarnessPassed`)
and not fatal unless the generator was run with `-RequireParityHarness`. The DLL itself is gated
on artifact presence, architecture and exports instead.

A second reason that harness fails on a default run: `-gencode=arch=compute_86,code=sm_86` emits
SASS for `sm_86` and **no PTX**, so the DLL cannot load on Ultra-Magnus's own `sm_89` 4090 at
all. To exercise the harness on the building host, request PTX as well --
`-CudaArchitectures sm_86,compute_86` (or add `sm_89`) -- and only then is
`-RequireParityHarness` meaningful. `sm_86` stays mandatory either way.

## 2. Exe and package (board host)

Fetch the pair job's artifacts directory, then:

```powershell
pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-assemble.ps1 `
    -SourceCommit <same-40-hex-sha> -DllPairDir <pair-artifacts-dir> -OutDir <build-dir>
```

It verifies the pair manifest (`sourceCommit` equal to `-SourceCommit`, every file's sha256
matching the bytes on disk, a boolean `pendingSymbolPresence`) **before** deploying anything,
then builds the Release exe from a clean `git archive` -- verified through the same
`Assert-AttrCudaSourceArchive` the Ultra-Magnus job uses, so the zip's EOCD comment has to
declare `-SourceCommit` and the verified sha lands in `build.json` as `sourceArchiveSha256`.
Then: `build_buildinfo.h` injected into the
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
  `pendingSymbolPresence`, `dllPairManifestSha256`, `sourceArchiveSha256` and `exeBuiltOnHost`.

**Keep the `MANIFEST_SHA256=` value off the `RESULT=ASSEMBLE_OK` line.** It is the lowercase
sha256 of the published `build.json`, and step 4 will not run without it. The staging generator
in step 3 echoes the same value as `buildManifestSha256`, so either receipt will do -- but it
must come from a step that ran, never from re-hashing a file someone dropped into the cache.

## 3. Stage into the Bachelor cache

```powershell
pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-stage-job.ps1 `
    -SourceCommit <same-40-hex-sha> -BuildDir <build-dir> -OutDir <staging-dir>
```

Drop the emitted job into `C:\mlvtmp\mlv-agent\inbox` together with the four published files, by
their own names, as side-files. The job re-hashes each one against values baked in at generation
time, cross-checks the arriving `build.json`, publishes into `C:\mlvtmp\mlv-agent\cache`
transactionally with `build.json` **LAST**, and only then removes the side-files. Exit codes:
3 side-file missing, 4 hash mismatch, 5 manifest binding, **6 artifact name safety**,
20-22 publish.

**The file names are never taken from `build.json`.** Both the generator and the job on the host
derive the canonical basenames from `sourceCommit` through the same convention the assembler and
the attribution job use, and require an exact match; `build.json` supplies hashes only. Every
name must be a plain basename (no separator, no `..`, no colon) and every resolved cache and
inbox path must be a direct child of its root -- checked before any `Copy-Item`, `Move-Item` or
`Remove-Item`, because those two directories are written and deleted in on an unattended host.

`pwsh -File <jobId>.job.ps1 -VerifyOnly` runs that whole read-only prefix -- names, containment,
side-file presence, hashes, manifest binding -- and exits without touching anything. An
interrupted run leaves `build.json` unpublished by construction (it is copied last, after every
artifact has been renamed into place), so the attribution job's requirement that it be present is
what makes a partial stage safe: re-drop the side-files and re-run.

## 4. Attribution job (inside the owner-granted lane)

Run this **only inside a lane whose prompt carries the owner-typed `CLIP_OR_NONE:` path**.
`-ClipPath` must be exactly that path.

```powershell
pwsh -NoProfile -File tools\profiling\bachelor\playback-attr-3-cuda-job.ps1 `
    -SourceCommit <same-40-hex-sha> -BuildManifestSha256 <64-lowercase-hex> -ClipId <id> `
    -ClipPath '<the lane prompt CLIP_OR_NONE path>' -OutFile <staging-dir>\<jobId>.job.ps1
```

**`-BuildManifestSha256` is mandatory, and the hub passes the sha the assembler printed**
(`MANIFEST_SHA256=` on its `RESULT=ASSEMBLE_OK` line, echoed by the staging generator as
`buildManifestSha256`). The Bachelor cache is mutable and the job does not own it: without this
value the job would believe whichever same-named `build.json` is sitting there, and replacing it
together with matching artifacts forges `pendingSymbolPresence` and the whole DLL association in
one move. The job hashes the cached manifest and refuses **before `ConvertFrom-Json`** -- a
parsed field is already a trusted field -- and then requires its `sourceCommit` to equal
`-SourceCommit`, its `dllPairManifestSha256` to be present and well formed, and its
`pendingSymbolPresence` to be a real boolean. Both hashes land in `evidence-manifest.json` under
`buildManifest`.

On Bachelor the emitted job refuses to run unless the clip path sits directly in the agent cache,
names that clip id, and exists. It verifies all three cache artifacts against the authenticated
`build.json` and **reads `pendingSymbolPresence` from it**. PresentMon runs as a direct child,
inheriting the job-owned TEMP; if the agent account lacks ETW trace rights the job fails
`PRESENTMON_FAILED rc=6` and never falls back to the elevated scheduled task.

**Which log the gate reads.** `run-release-gui-smoke.ps1` does not write into
`<output dir>\logs`; it creates a GUID-nonced `logs-<stem>-<nonce>` directory and publishes the
per-run snapshot as `"$outputPath.run.log"`, exposed as `result.json` `log.path` and bound by
`evidence.runLogSnapshot.sha256`. The job reads **that** file -- resolved from the result of the
run it just executed, required to sit inside its own work tree, and required to hash to the
declared value -- and exits **16 `SMOKE_LOG_UNAVAILABLE`** if it is absent, unbound or
elsewhere. The aggregate rotating app log is recorded as `aggregateSourcePath` and never read:
the runner's own comment says it may grow after the run and carries no comparison authority.

Before any verdict the job takes the run's own `gpu_playback_recon.eligibility` line from that
snapshot and exits **15 `BACKEND_NOT_AVAILABLE`** unless `cuda_backend_available=1` **and**
`r16_available=1`, recording both plus `r16_reason`. A log with no eligibility line at all is
refused the same way: absence of the diagnostic is not evidence of eligibility. Other outcomes:
12 `VENUE_NOT_QUIESCENT`, 13 `GPU_RECON_FRAMES_ZERO`, 14 `CPU_FALLBACK_DETECTED`, 0
`MEASUREMENT_CAPTURED`. It publishes `presentmon-series.csv`, `logs\smoke-run.log`,
`evidence-manifest.json`, `provenance.json` and `artifact-index.json`.

The owner's consent for the clip on this card is recorded at
`.claude-state/coordination/dual-lane/receipts/owner-footage-consent-20260916.json`, with its
`-correction.json`. That receipt is evidence of consent, never an authorization -- the
authorization is the owner-typed path line. The job cites the receipt's file name in
`evidence-manifest.json`.

## 4b. Fixture rehearsal (no footage)

The chain above can be rehearsed end to end WITHOUT any owner footage, and should be, before a real
clip is ever opened. Pass `-ClipId tiny_dual_iso` or `-ClipId large_dual_iso`: the two clips tracked
in this repository under `tests/fixtures/clips`. NA-4 admits those already, with no `CLIP_OR_NONE`
line and no consent receipt, because they are repository fixtures rather than the owner's footage.

- **Staging.** `tools/profiling/um-run.ps1 -SideFile <repo>/tests/fixtures/clips/<name>` places the
  fixture in the agent cache by the same verified route as every other side-file. It is admitted by
  its SOURCE (a `tests/fixtures/clips` directory), not by its extension: see
  `Test-UmRunTrackedFixtureSource` in `tools/profiling/UmRunDrop.psm1`. A file with the same name from
  any other directory is refused.
- **Unchanged for a fixture run.** `-ClipPath` must still resolve into the agent cache with
  `BaseName -ceq $ClipId`; `-BuildManifestSha256` still authenticates the staged manifest; the
  eligibility gate still exits 15 unless `cuda_backend_available=1` and `r16_available=1`.
- **What it establishes.** That staging, backend load, the eligibility gate, PresentMon capture and
  artifact publication all work on the measurement host, against the exact package under test.
- **What it does NOT establish.** Anything about performance. Both fixtures are small dual-ISO files;
  their timing is a plumbing proof, never a measurement. A fixture run records
  `fixtureRehearsal: true` in `summary.json` and `evidence-manifest.json` so it cannot be read as an
  attribution result, and a fixture run's numbers never enter a verdict in section 6.

## 5. Extract the histogram

Pull `presentmon-series.csv` and `logs\smoke-run.log` from the attribution artifacts, then:

```powershell
py -3 tools\profiling\refresh_period_histogram.py `
    --presentmon-csv <artifacts-dir>\presentmon-series.csv `
    --frame-log <artifacts-dir>\logs\smoke-run.log `
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
py -3 -m pytest -q tools\repo_hygiene\test_playback_attr_3_cuda_split_route.py
```
