# GPU Lane — Playback & Export Roadmap + UX (plan of record)

Status: 2026-06-15. The CUDA recon lane is proven on an RTX 4090 (validated bit-exact
+ measured), and the first production CUDA AMaZE debayer seam has landed behind
a DLL gate with no GUI claim. This doc remains the plan of record for the
remaining playback/export rollout.

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
- **E3** pipelined export: CPU decode workers → one GPU recon queue → CPU compress/write workers (never N processes fighting one GPU).
- **E4** rendered-video export: later, only after processing parity; hardware encoders (NVENC/AMF/QSV) a separate lane.

## 4. Lane B — CUDA playback

- **P-pre (quality completion):** GPU **AMaZE debayer** parity (landed behind the
  DLL gate) + GPU **processing** parity + clean x1 CPU-vs-GPU frame diff. Required
  before the GUI may claim "GPU Full Quality AMaZE" (see §8).
- **P1** loader/fallback: load `igpu_recon_cuda.dll` if present + capable, else CPU. No hard dependency.
- **P2** GPU recon + CPU readback: CUDA recon → Bayer16 readback → existing CPU debayer/process/present. Integration bridge, not final UX.
- **P3** no-readback playback: CPU decode/prefetch → CUDA recon/debayer/process → CUDA→GL texture present (no `QImage`, no `glReadPixels`); `GpuDisplayViewport` gains a texture-in path.
- **P4** adaptive quality + polish: hardware-capability-driven auto quality/scale, visible A/B + frame diffs, status UI, telemetry.
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
- **Export:** per-frame DNG image-payload + metadata diff, compressed + uncompressed.
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
- **Slice 6 (REMAINING — gate still rejects):** in-loop **simple-contrast factor**
  (`processing->contrast`, a per-pixel luma-dependent exposure multiply via
  `contrast_curve[cval]`, `raw_processing.c:2954`). This is in the exposure/linear
  domain, not the post-gamma creative LUT section, so it needs its own port.
  Until then `gpuPreviewProcessingIsSupported` rejects `|contrast| >= 0.01`.
- **Later slices:** shadows/highlights + clarity (spatial RBF blur-mask pre-pass),
  then 1D/3D LUT, creative filter, AgX, median/RBF denoise, grain, CA correction,
  sharpen, vignette, non-Rec709 gamut.

After slices 1-5, `gpuPreviewProcessingIsSupported` accepts
`allow_creative_adjustments` for any default-graded clip whose only creative
controls are the ported stages — which covers the default image profile and the
common grade (hue-vs/vibrance/saturation/toning/curves), failing closed only on
the unported in-loop contrast factor and the spatial/LUT stages above.

Each slice keeps the CPU reference (`applyPreviewProcessingPixel`) and the GPU
subset shader bit-aligned, adds unit parity tests (the CPU reference is the local
bit-exact oracle), and is validated by a CPU-vs-GPU frame diff on the RTX 4090
before the support gate relaxes for that stage. P-pre — and the honest GUI
"GPU · Full Quality · AMaZE" claim per §8 stage 2 — completes when every creative
stage reaches parity.

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
