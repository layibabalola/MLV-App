# GPU Lane — Playback & Export Roadmap + UX (plan of record)

Status: 2026-06-19. Lane A E0-E2 export, P-pre GPU AMaZE/processing
parity, and Lane B P1-P3 are now through their scoped proof gates. P3 is
honest-scoped, not universal: the RTX 4090 FastProxy proof validates the
raw-fixes-enabled HQ Dual ISO no-readback CUDA-to-GL R16 texture path with
GL/backend/oracle parity; unsupported states still fail closed to CPU readback
or CPU presentation. The remaining priority order is P4 adaptive-quality polish,
then Lane A E3/E4 export pipeline/rendered export, then Lane C portable GPU
backends.

Update 2026-06-16: P-pre **processing parity** is being extended from the
supported levels / matrix / camera-matrix WB / gamut / gamma subset to the
`allow_creative_adjustments` family, staged as curve-first parity slices
(see §8.1). Slice 1 (the creative contrast-curve LUT) is designed and next to
implement; each slice is bit-aligned CPU-vs-GPU and validated by an RTX 4090
frame diff before the `gpuPreviewProcessingIsSupported` gate relaxes. Note also
that Lane A **E1** is currently realized as an off-by-default *shadow validator*
(`MLVAPP_GPU_EXPORT`): `llrawproc` runs the CUDA recon into a scratch buffer and
copies it over the CPU output only when byte-identical, so the CPU path stays
authoritative until the E2 parity gate promotes it.

Update 2026-06-18: Lane B **P1/P2** has an experimental readback bridge behind
`MLVAPP_GPU_PLAYBACK_RECON=1`. Playback render/recon threads opt in explicitly,
then `llrawproc` prepares only the CPU-side Dual-ISO match/LUT state and runs
`igpu_recon` to `IGPU_OUT_CPU16` in a temporary output buffer that is copied
back only after success; on missing DLL, unsupported config, invalid state, or
backend error it falls back to the existing CPU `diso_get_full20bit`.
The bridge is limited to the already-proven v1 recon shape
(`MEAN23 + alias_map ON + fullres ON + chroma OFF`) and is telemetry-only: it
does not add a GUI quality claim, no-readback present path, or adaptive mode.
Unlike export's CPU-authoritative shadow replacement, playback P2 deliberately
trusts a successful backend `rc==0` and does not run a per-frame CPU memcmp;
that trust boundary is accepted only for this experimental, env-gated bridge
after the 4090 parity pass, with any future shadow-verify mode tracked as a
canary/follow-up rather than the normal playback path.

Update 2026-06-18 P3 surface: `MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1`
is now an explicit request surface. In the narrow experimental x1 GPU preview
processing + `Decode/Reconstruct/Process` playback shape, the Qt GL viewport can
present the P2 reconstructed Bayer frame through a `GL_R16`/Bayer16 texture and
shader-side bilinear debayer/display processing. Telemetry labels this as
`source=cpu16_readback_reconstructed_bayer` and records
`gpu_playback_recon_texture_present_no_readback_active=false`; it is a presenter
surface, not the final P3 win. Activating true P3 still requires a GUI-thread-safe
CUDA `IGPU_OUT_GL_TEXTURE` producer (or final RGB CUDA-to-GL backend) so the
recon output reaches the display without the CPU16 readback.

Update 2026-06-18 P4 status/telemetry slice: the existing Playback Quality
UI/status surface now classifies the presented pipeline as `CPU`, `GPU Preview`,
`GPU RB`, `GPU Tex RB`, or `GPU Tex NR`. Playback smoke logs emit matching
machine-readable tokens (`cpu`, `gpu_preview`, `gpu_recon_readback`,
`gpu_texture_readback`, `gpu_texture_no_readback`) on per-frame GPU telemetry
and a session summary. The 2026-06-19 scoped P3 producer may report `GPU Tex NR`
/ `gpu_texture_no_readback` only when the validated CUDA-to-GL no-readback path
actually presents the frame; readback or unsupported fallback remains reported
as `GPU Tex RB`, `GPU RB`, or `CPU`.

Update 2026-06-19 P3 proof: `MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1`
can now present the proven raw-fixes-enabled HQ Dual ISO shape through
`source=cuda_gl_r16_texture` with
`gpu_playback_recon_texture_present_no_readback_active=true`. The accepted
UltraMagnus RTX 4090 run (`20260619T002209`, release executable SHA256
`F70CE56F8418E4107D1AF502F31A3B94399E92253016EC4950E145AB59922CAE`) reported
`correctnessValidated=true`, `gpu_texture_no_readback_frames=94`,
`glParityMatchCount=10`, `glMismatchTotal=0`, and advancing GL texture hashes.
The raw-fixes-off control receipt remains non-proof by design and must not arm
no-readback.

Update 2026-06-19 P4 default slice: clean playback settings now default to
`Auto` instead of `Fast`, matching the user-facing mode plan below while still
round-tripping explicit `Fast` selections. This is only the first adaptive
quality polish step. The Auto sampler also keeps headroom-based sharpening at
HQ x4 until the caller has observed a validated no-readback presentation path;
capability-aware promotion/demotion remains scoped by the P3 proof gate and
must keep unsupported states on readback/CPU paths.

Evidence (detail): `.claude-state/profiling/20260614-tier2-cuda/` (SUMMARY, tier2-findings,
recon-algorithm-map, recon-exact-constants, parity / parity-breadth / amaze-parity /
glinterop / optimization / full-pipeline results, integration-blueprint) and
`.claude-state/profiling/20260613-gpu-lane-x1/findings.md` (Tier 1 + x1 CPU breakdown).
Code: `tools/gpu/` (probes, parity, oracle, `igpu_recon.h`, `igpu_recon_cuda.dll`).

Proven so far: recon 0-LSB (mean23 + AMaZE dual-ISO logic), bilinear debayer 0-LSB,
CUDA AMaZE debayer parity through the DLL-gated/non-default production seam,
zero-readback CUDA->GL present (~0.1 ms), deployable ABI-validated `igpu_recon_cuda.dll`,
full pipeline ~1 ms/frame @ 4.1 MP (~988 fps) / ~9 ms @ 8K, parity across 8K/clipped/ISO.

---

## 1. Goals and non-goals

**Goals**
- Full-quality **x1 realtime** playback (the original moonshot) — no forced scale/quality compromises on capable hardware.
- **Faster CDNG export** (the repo's primary mission) with byte-exact output.
- One **backend contract** (`igpu_recon` C-ABI) so CUDA never leaks into the app; portable backends slot behind it.
- **Trustworthy UX**: quality labels mean exactly what they say; the app always shows its true active path.
- A permanent **CPU floor** — the app is fully usable with no GPU.

**Non-goals**
- Replacing the CPU path (it is the floor, not deprecated).
- Requiring a GPU.
- OpenGL-compute as the strategic portable target (it's frozen/deprecated on macOS).
- Rendered-video GPU export before processing parity lands.
- Bit-exactness *beyond* the engine's own float tolerance (its SSE2-vs-scalar variance is the reference, not a stricter bar).

## 2. Backend ladder and fallback rules

Ladder behind the single `igpu_recon` ABI:
```
CUDA      NVIDIA fast path (proven reference)
Vulkan    Win/Linux all-vendors + macOS via MoltenVK   (later)
Metal     macOS / Apple Silicon native                  (later)
CPU       universal fallback (permanent floor)
```
**Capability query** gates GPU use: *can this device do full recon + texture present with no per-frame readback?* — not merely "is a GPU present" (a weak iGPU can pass the latter and still not be worth it; the Tier 1 GL seam had a GPU and lost on readback).

**Fallback rules (all non-fatal):** missing DLL → CPU · no NVIDIA → CPU · GPU init failure → CPU · device/parity mismatch → CPU · **user selects Software → never touch the GPU** · **export always has a CPU correctness path**. No alarming modal unless the user explicitly requested GPU-only.

## 3. Lane A — GPU CDNG export (first; lowest risk, on-mission)

CDNG stores **post-recon Bayer** (debayer/processing happen later in the user's NLE), so only the **recon** stage needs the GPU — exactly the proven, ABI-wrapped, 0-LSB stage. Offline + readback-tolerant + file-diff-verifiable ⇒ the safest first production integration.
- **E0** export-stage profiler: decode / Dual-ISO recon / DNG pack / DNG compress / disk write / queue idle. Also includes an intentional CPU-export focal-plane resolution stability guard for same-process multi-frame DNG exports: frame 1 remains legacy byte-identical, while affected frames 2+ stop compounding the EXIF focal-plane denominator.
- **E1** GPU CDNG recon: CPU decode/unpack → CUDA recon (`IGPU_OUT_CPU16`) → read back Bayer16 → existing DNG writer unchanged. Behind `MLVAPP_GPU_EXPORT` / setting.
- **E2** export parity gate: CPU vs GPU exported DNGs match image payload + metadata (Look Assist defaults, resume, Dual-ISO pattern mapping, compressed + uncompressed). CPU fallback always.
- **E2 batch telemetry:** when a batch export actually attempts the CUDA shadow
  path and the backend exposes its optional VRAM query, stdout emits
  `[BATCH] GPU ... vramAllocatedMB=...` once per clip/resolution. The value is a
  backend working-set budget (tracked CUDA buffers plus the measured context
  reserve), not a WDDM per-PID reading; CPU-only and old-DLL runs stay silent.
- **E3** pipelined export: CPU decode workers → one GPU recon queue → CPU compress/write workers (never N processes fighting one GPU).
- **E4** rendered-video export: later, only after processing parity; hardware encoders (NVENC/AMF/QSV) a separate lane.

## 4. Lane B — CUDA playback

- **P-pre (quality completion):** GPU **AMaZE debayer** parity (landed behind the
  DLL gate) + GPU **processing** parity + clean x1 CPU-vs-GPU frame diff. Required
  before the GUI may claim "GPU Full Quality AMaZE" (see §8).
- **P1** loader/fallback: load `igpu_recon_cuda.dll` if present + capable, else CPU. No hard dependency. Experimental playback bridge present behind `MLVAPP_GPU_PLAYBACK_RECON=1`.
- **P2** GPU recon + CPU readback: CUDA recon → Bayer16 readback → existing CPU debayer/process/present. Integration bridge, not final UX. Implemented for the v1 proven config only; missing/unsupported backend falls back to CPU.
- **P3** no-readback playback: CPU decode/prefetch -> CUDA recon -> CUDA-to-GL R16 texture present (no per-frame CPU readback for the displayed Bayer frame) is implemented and RTX 4090-validated for the scoped raw-fixes-enabled HQ Dual ISO shape. Readback-backed Bayer16 GL presentation remains the fallback presenter for P2 output; unsupported or non-proof states stay CPU/readback.
- **P4** adaptive quality + polish: hardware-capability-driven auto quality/scale, visible A/B + frame diffs, status UI, telemetry. Status/telemetry now distinguishes CPU, GPU preview, GPU recon readback, readback-backed texture present, and true no-readback texture present; the remaining adaptive work is to promote capability-aware defaults and quality decisions without exceeding the scoped P3 gate.
- Decode (LJ92, CPU, overlapped via prefetch ~7-9 ms @ 4.1 MP) is the steady-state gate once recon is on GPU — tune the overlap.

## 5. Lane C — portable GPU (later)

CUDA stays the reference. Add backends behind the same ABI: **Vulkan** (strategic Win/Linux all-vendors + Mac via MoltenVK), **Metal** (strategic macOS). **OpenGL** = presentation (the viewport is GL; CUDA→GL present proven) + optional *tactical* Win/Linux compute bridge — not the strategic compute target. Sequenced after Lanes A/B so effort isn't fragmented; the CPU oracle validates every new backend identically (0-LSB).

## 6. Quality modes and Expert controls

**Default UI — three modes (no jargon):**
```
Playback Mode:  Auto  |  Prioritize Quality  |  Prioritize Smoothness
```
- **Auto** (default): CUDA full-quality when validated + available; CPU full-quality when paused/scrubbing/exporting if needed; reduced-scale preview only when necessary to hold cadence; shows a small status (`GPU` / `CPU` / `Preview`).
- **Prioritize Quality:** true x1, selected/Advanced debayer, **no substitution**; if CUDA can't satisfy it, fall back to **software and say so** — never silently drop to bilinear or x4.
- **Prioritize Smoothness:** reduced-scale / faster debayer allowed to keep editing responsive; clearly preview-only — paused inspection and export stay full quality unless explicitly opted out.

**Expert Playback Settings (advanced, opt-in):**
```
Playback Engine:    Auto / GPU / Software
Preview Resolution: Auto / Full 1x / 1/2 / 1/4 / 1/8
Debayer Quality:    Auto / AMaZE / RCD / Bilinear / Basic   (labeled by intent — see §7)
Dual ISO Preview:   Auto / Full HQ / Fast Preview
Fallback Behavior:  Allow automatic fallback · Warn when quality is reduced
```
Advanced users can force `Full 1x · AMaZE` and accept dropped frames (the status shows the honest fps). The main UI never forces anyone to understand CUDA, recon, readback, or x4/x8.

## 7. Debayer / scale policy

Rank by **quality tier**, not a fake exact speed ladder (the advanced demosaics are quality *tradeoffs*). Label by intent:
```
Advanced  — AMaZE (Maximum detail) · RCD/LMMSE/AHD (High quality)
Fast      — Bilinear (Fast preview)
Minimal   — Basic / None (Fastest, last-resort cadence rescue)
```
- **Auto** moves between *tiers* by sustained cadence; it does **not** micromanage AMaZE-vs-RCD (no evidence to; revisit only if that changes).
- **Prioritize Quality / Expert-forced algorithm:** use it, or fall back to **CPU** for that algorithm — never silent-substitute bilinear under a "Full Quality" label.
- **Dual-ISO interpolation** folds into the same intent: Full Quality → AMaZE/HQ dual-ISO; Performance → mean23 / reduced. Don't expose "mean23" to normal users.
- **Scale** is **graceful auto-degradation** (invisible rungs Auto uses to hold cadence on weak HW), not a chore — though Expert can pin it. On capable GPUs the cost gap nearly vanishes (even AMaZE debayer ~1-2 ms), so the tradeoff usually disappears and full quality just plays.

## 8. Validation gates

- **Recon:** 0 LSB vs CPU oracle (mean23 + AMaZE dual-ISO logic) — done. AMaZE dual-ISO's shared float demosaic core is ±1-2 LSB = the engine's own SSE2-vs-scalar variance (policy: keep that subpath on CPU for "legacy-exact," or require explicit tolerance opt-in).
- **Debayer:** bilinear 0 LSB — done; **AMaZE debayer parity** landed as a
  DLL-gated/non-default production seam and remains non-GUI until the rest of
  P-pre is reviewed.
- **Processing:** **parity = P-pre**. The current gate covers the supported
  preview-processing subset (levels / matrix / camera matrix / gamut compression
  / gamma LUT path) through CPU-vs-GPU frame diffs; broader unsupported features
  still fail closed instead of silently using GPU.
- **Export (Lane A E2):** per-frame DNG image-payload + metadata byte-diff, compressed
  + uncompressed — **implemented + RTX-4090-validated** (CUDA `igpu_recon`). The GPU
  export shadow path engages *only* for the base HQ dual-ISO config (MEAN23 + alias-map
  ON + full-res ON + chroma OFF): there it replaces the CPU output byte-identically
  (`replaced==1`, SHA256 match) across {tiny,large} × {Look-Assist off/on} ×
  {uncompressed,compressed}. Every other config (alias/full-res OFF, AMAZE, chroma-smooth
  ON) is GPU-ineligible and falls back to CPU cleanly (`run_attempted==0`, `replaced==0`,
  CPU authoritative, DNG still byte-equal). Resume/partial export is byte-identical to a
  full run (per-frame export carries no cross-frame state). Tests: `DualIsoPipeline.
  GpuExport*` in `tests/pipeline/test_dual_iso_pipeline.cpp` — eligible matrix, ineligible
  fallback, resume proxy, and missing-DLL byte-inert; the GPU-engaging cases are gated on
  `MLVAPP_GPU_EXPORT_TEST_DLL` (skip on llvmpipe, run on the 4090).
- **Playback truth:** validate by *pixels* (PrintWindow / frame diff), never FPS alone — cadence can read perfect over a frozen frame.

**Parity-coupling (the honesty linchpin):** a quality option appears in the GUI **only after its parity gate passes** — so the UX rollout is staged with the engineering:

| Stage | Engineering done | GUI may honestly offer |
|---|---|---|
| 1 | P2 / E1 (recon + bilinear, 0-LSB) | `GPU` engine; Full-Quality-AMaZE routes to **CPU**; GPU bilinear only under a labeled *Performance* mode |
| 2 | P-pre passes (AMaZE debayer + processing parity) | `GPU · Full Quality · AMaZE` becomes a true explicit path, with CPU AMaZE fallback reported instead of silent bilinear substitution |

P-pre is therefore both the engineering gate and the GUI-claim gate — it's what prevents a "Full Quality" toggle that silently isn't.

### 8.1 Processing-parity slices (extending P-pre to `allow_creative_adjustments`)

The GPU preview-processing shader today reproduces only the levels / matrix /
camera-matrix WB / gamut / gamma subset and fails closed on
`allow_creative_adjustments` (the creative grade). Extending it to full parity —
so a normally-graded clip can use the GPU path instead of falling back to CPU —
is staged as curve-first slices, because at the default image profile the
creative **contrast curve** (`pre_calc_curve_r`, built from the non-zero base
contrast params) is the only creative-family stage that is both active and
non-identity; gradation and toning execute but are identity, and
shadows/highlights and clarity are inert by default.

The post-gamma creative pipeline is ported as bit-aligned slices, in the exact
order `raw_processing.c` applies them (gamma → hue-vs → vibrance → saturation →
toning → contrast curve → gradation):

- **Slice 1 (DONE, `d77a26c6`):** creative contrast-curve LUT (`pre_calc_curve_r`)
  + gradation curves (`gcurve_*`) — 1D 16-bit LUT lookups, no spatial pass.
- **Slice 2 (DONE, `7c59d699`):** toning (per-channel `toning_dry + toning_wet`).
- **Slice 3 (DONE, `2e04516b`):** saturation (`Y1 + trunc((pix-Y1)*sat)`).
- **Slice 4 (DONE, `6ee4b4f3`):** vibrance (saturation-weighted blend).
- **Slice 5 (DONE):** hue-vs / luma-vs curves — RGB→HSV, four signed-`float[36000]`
  curve adjustments indexed by hue (`H*100`) and luma (`V*36000`), HSV→RGB. The
  curves are carried as **R32F** textures (units 11-14) so the GPU, the CPU
  reference and the production `float` curves stay bit-aligned (the uint16 LUT
  path would quantize the [-1,1] curve to ~3e-5 and break parity).
  - **Parity caveat (OOB clamp):** `hue_vs_luma` can push `V` to ≥ 1.0, after
    which `(uint16)(V*36000)` indexes `luma_vs_saturation[]` (exactly 36000
    entries) out of bounds — `V == 1.0` alone already yields index 36000. That
    read is undefined on the production CPU path, so both the GPU shader and the
    CPU reference **clamp the luma index to 35999** (the last valid sample)
    instead of reproducing undefined behaviour. This diverges from production
    only for boosted highlights (`V ≥ 1.0`) when a non-neutral
    `luma_vs_saturation` curve is set; clamping is the correct, deterministic
    behaviour and the production CPU path should adopt the same clamp (tracked
    separately — out of GPU-lane scope).
- **Slice 6 (DONE):** in-loop **simple-contrast factor** (`processing->contrast`,
  a per-pixel luma-dependent exposure multiply via `contrast_curve[cval]`,
  `raw_processing.c:2941-2954`). `cval` is the integer luma `(4R+11G+B)>>4` of the
  matrix-applied (pre camera-WB) pixel; the value is multiplied by
  `contrast_curve[cval]`. Because the factor is luma-dependent it cannot be folded
  into the per-channel matrix/gamma LUTs, so it is applied in-shader after the
  matrix sample and before the camera matrix/gamma (the scalar commutes with the
  linear WB). `contrast_curve` (`double[65536]`) is narrowed to `float` and carried
  as an R32F texture (unit 15).
- **Later slices (non-creative features, gated independently of the creative flag):**
  shadows/highlights + clarity (spatial RBF blur-mask pre-pass), then 1D/3D LUT,
  creative filter, AgX, median/RBF denoise, grain, CA correction, sharpen,
  vignette, non-Rec709 gamut.

**After slices 1-6 the creative-adjustments family is fully ported.**
`gpuPreviewProcessingIsSupported` no longer rejects `allow_creative_adjustments`
on its own — it accepts any grade (default or hand-graded: in-loop contrast +
hue-vs/luma-vs + vibrance + saturation + toning + contrast curve + gradation) and
fails closed only on the non-creative features listed above, which are gated
independently of the creative flag. The P-pre creative-parity goal (a normally
graded clip uses the GPU path instead of the CPU fallback) is met for the
creative-grade family, pending the RTX 4090 GL frame-diff that validates the
shader against this CPU reference.

Each slice keeps the CPU reference (`applyPreviewProcessingPixel`) and the GPU
subset shader bit-aligned, adds unit parity tests (the CPU reference is the local
bit-exact oracle), and is validated by a CPU-vs-GPU frame diff on the RTX 4090
before the support gate relaxes for that stage. P-pre — and the honest GUI
"GPU · Full Quality · AMaZE" claim per §8 stage 2 — completes when every creative
stage reaches parity.

### 8.2 Spatial-stages phase (completing P-pre beyond the creative family)

Update 2026-06-17: the creative family + the tractable non-creative per-pixel
stages (gamut / AgX / vignette / 1D-3D LUT) are ported and 4090-validated. The
remaining gate rejects were recon'd stage-by-stage and scoped in
[`gpu-lane-spatial-stages-scoping.md`](gpu-lane-spatial-stages-scoping.md). Key
correction to the earlier "the rest all need a blur pre-pass" assumption: most
remaining rejects are actually **per-pixel** (highlight reconstruction, gradient,
grain, creative filter) and only **chroma blur / sharpen / median** genuinely
need a neighborhood pass; **shadows/highlights, clarity, RBF denoise and CA** are
inherently **sequential** (a recursive bilateral filter / edge-window scan) and
are recommended to stay **CPU-fallback by design** (roadmap §9 honesty), not
bit-exact GPU ports. Two hard constraints drive the ranking: the shader is
**GLSL 110** (no bitwise ops/`uint`/`%` — all integer math float-emulated, which
blocks bit-exact grain and the NN sigmoid) and the **strict parity gate**
(≤16 LSB). The box blur (`blur_image`) is a separable integer box, bit-exactly
reproducible and validated in isolation before any consumer is wired. Order:
A) per-pixel bit-exact (highlight-recon → gradient); B) box-blur FBO infra +
chroma/sharpen/median; C) explicit CPU-fallback decision for the recursive
stages. See the scoping doc for the per-stage primitive table, apply-order map,
and the box-blur bit-exactness analysis.

## 9. UI truth / status language

Always surface the active path, quietly (no fake green lights, no alarms unless the user asked for GPU-only):
```
Playback: Full Quality · GPU · 1x · AMaZE
Playback: Full Quality · Software · 1x · AMaZE
Playback: Performance Preview · GPU · 1/4 · Bilinear
GPU unavailable: using software
```
Principles: **(1)** quality labels mean what they say; **(2)** preview compromises are allowed but *named* (reduced-scale / bilinear / mean23 = preview/performance); **(3)** Auto is smart but not mysterious — the status reveals the chosen path; **(4)** **export is sacred** — default deterministic + legacy-equivalent, any tolerance-based GPU path opt-in until proven; **(5)** fallback is a feature, not a failure (accelerator, not requirement); **(6)** Advanced controls exist without cluttering the main workflow.

North star: *"I can trust what I'm seeing and exporting,"* while the app quietly uses every bit of GPU speed it can safely use.

---

## Execution order (recommended)
1. **Lane A E0–E2** (GPU CDNG export, byte-exact) — lowest-risk first production use; serves the batch-export mission.
2. **P-pre** quality completion (AMaZE debayer + processing parity) — unlocks honest "GPU Full Quality" routing.
3. **Lane B P1–P4** (CUDA playback) — first explicit AMaZE playback routing, then the no-readback/user-facing realtime wins.
4. **Lane A E3–E4** (export pipeline + rendered/NVENC) and **Lane C** (Vulkan/Metal) as parallel/later tracks.

Supervised items (touch shipping `src/` or protected branch): all `src/` wiring in §3-4, and merging the work-block branch. The backend, parity harness, and this plan are ready.
