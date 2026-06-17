# GPU Lane — Spatial-stages phase scoping (P-pre completion)

Status: 2026-06-17. Scopes the stages still rejected by
`gpuPreviewProcessingIsSupported` after the creative-parity + non-creative
per-pixel extension landed (branch `codex/work-block/wb-2d67a6eb72414fb9`,
range `08c33e31..f5a5744e`: gamut + AgX + vignette + 1D/3D LUT on top of the
creative-grade family). This is the design-of-record for finishing P-pre
processing parity; it supersedes the loose "the remaining stages all need a
blur pre-pass" framing with a stage-by-stage primitive analysis.

Source of truth for the per-stage engine code is the recon performed
2026-06-17 (8-agent workflow over `src/processing`), quoted inline below with
`file:line`.

---

## 0. Two constraints that reshape the difficulty ranking

**(C1) The shader is GLSL 110 (no `#version`).** GLSL 110 has **no bitwise
operators, no `uint`, no `%`, no `<<`/`>>`**. The existing subset shader already
emulates all integer semantics with floats — `floor((m16.r*4.0 + m16.g*11.0 +
m16.b)/16.0)` stands in for `(4R+11G+B)>>4`, `mod(index,256.0)` for the LUT
address split, `truncToZero(v)=sign(v)*floor(abs(v))` for C truncation. Any
stage whose engine math is *intrinsically* bitwise (a uint32 XOR hash) or
double-precision-table-driven cannot be made bit-exact under GLSL 110 without
either a float decomposition that is itself lossy, or bumping the GLSL version
(rejected — the production viewport context targets broad/legacy GL; see
roadmap §5). This is why **grain** and the **creative-filter NN** are *not* the
easy per-pixel wins they first appear to be.

**(C2) The parity gate is strict.** `assert_gpu_offscreen_matches_cpu_reference`
requires max-abs-diff ≤ 16 LSB and mismatch-fraction ≤ 0.03 vs the CPU
reference (per-pixel tolerance 2). Every ported slice so far lands ≤ 8 LSB on
the 4090. A stage that can only be *approximated* on the GPU (recursive
bilateral filter, NN sigmoid) will not clear this gate, and the roadmap's
honesty principle (§9: "fallback is a feature") says the correct outcome for
such a stage is a **labelled CPU fallback**, not a silent approximation.

---

## 1. The biggest finding: most "spatial" rejects are actually per-pixel

The gate rejects 13 features. Recon shows they split into three classes, and
only **three** genuinely require a neighborhood blur:

- **Class A — per-pixel, no pre-pass, bit-exact in GLSL 110** (extend the
  existing single-pass shader exactly like the prior 10 slices):
  - **highlight reconstruction** — per-pixel clipped-green replace; needs a
    per-frame scalar (`highest_green` / `highest_green_diso`) uploaded as a
    uniform (computed CPU-side by `analyse_frame_highest_green`,
    `raw_processing.c:4999`). No neighbor reads. Apply at
    `raw_processing.c:3088-3119`.
  - **gradient** — per-pixel alpha-lerp against a precomputed full-frame mask
    (`gradient_mask[y*w+x]`, vignette-class) between the base pixel and a
    "gradient layer" that re-runs the colour pipeline with gradient-specific
    LUTs. Heavy (a second set of matrix/gamma/contrast LUT textures) but still
    one per-pixel pass. Apply/blend at `raw_processing.c:3021-3078` /
    `:3486-3491`.

- **Class A′ — per-pixel but NOT cleanly bit-exact in GLSL 110** (defer or
  accept a tolerance opt-in):
  - **grain** — deterministic per-pixel hash `randomval = seed1 ^
    ((i*seed2)*(seed3-i)*(i+seed4))`, `grain = (randomval % strength) -
    (strength>>2)` (`raw_processing.c:1979-1996`). The `^` and uint32 overflow
    are not expressible in GLSL 110. Seeds are already CPU-derived and can be
    uploaded, but the *hash itself* cannot be reproduced bit-exactly without
    bitwise ops. **Blocked on (C1).**
  - **creative filter** — per-pixel 3→7→3 MLP (`genann`) with a 4096-entry
    **double** sigmoid LUT (`filter.c:97-106`, `genann.c:213-219`). The matmul
    ports as uniforms, but the double sigmoid LUT gives only approximate parity.
    **Blocked on (C2)** unless the sigmoid LUT is texture-encoded and a wider
    tolerance is accepted.

- **Class B — genuinely needs a blur / multi-pass pre-pass**:
  - **chroma separation + chroma blur** — YCbCr round-trip + **separable box
    blur** on Cb/Cr only (`raw_processing.c:1832-1845` → `blur_image`,
    `processing.c:589`). The box blur IS bit-reproducible. This is the FIRST
    place to build + validate the box-blur FBO infrastructure.
  - **sharpen** — fixed 5-tap cross (`a*center - y*up - y*down - x*left -
    x*right`, `raw_processing.c:1911-1915`) over the prior stage's output;
    needs an intermediate texture (read the rendered result at 5 taps), plus an
    optional sobel edge-mask pass when `sh_masking>0`.
  - **median denoise** — per-pixel window sort (`denoiser_2d_median.c:191-217`);
    samples its `window×window` neighborhood from the source texture directly
    (no blurred pre-pass), feasible as one per-pixel pass, O(window²) fetches.

- **Class C — sequential / recursive, bit-exact ~infeasible under the gate**:
  - **shadows/highlights**, **clarity**, **RBF denoise** — all three are the
    **same recursive bilateral filter** (`CRBFilterPlain::filter`,
    `RBFilterPlain.cpp:226`): four strictly sequential 1-pixel IIR recursions
    (L→R, R→L, top→down, bottom→up) with data-dependent edge weights, wrapped in
    a full/half/quarter-res box-downsample + bilinear-upsample policy. A fragment
    shader cannot read its own prior-pixel output; a faithful port is a
    multi-pass row/column-serial scan whose float accumulation order would have
    to match the CPU to the LSB — "extremely fragile / possibly infeasible"
    (recon verdict, rank 5).
  - **CA correction** — sequential 1D edge-window scan applied twice with a
    transpose, variable-length windows, in-place writes, pointer-skip
    (`ColorAberrationCorrection.c:8,68`). Bit-exact GPU port ~infeasible.

---

## 2. Box-blur pre-pass design (the Class-B keystone)

`blur_image` (`processing.c:589`) is a **separable uniform box blur**, not a
Gaussian: `blur_diameter = radius*2+1`; horizontal pass into a temp buffer,
then vertical pass back, each using an integer sliding-sum with **truncating
integer divide** `sum / blur_diameter` and **clamp-to-edge** addressing
(`MAX(idx,0)` low / `MIN(idx,rl-3)` high). Single iteration per axis (not
3×-iterated to fake a Gaussian).

GPU design (validate in isolation BEFORE wiring any consumer):
1. **Two separable full-screen passes** into a ping-pong FBO: pass H = box of
   radius r along x, pass V = box of radius r along y. Each fragment sums
   `2r+1` clamped texels and applies the **truncating** divide.
2. **Bit-exactness requirements** (these are the parity risks to test first):
   - The sum is an **integer** sum of uint16 values, then a **truncating
     integer divide**. Reproduce with `floor(sum / diameter)` where `sum` is
     computed as a float of exact integer texel values (`floor(tex*65535 +
     0.5)`), NOT a normalized float average. Float32 has a 24-bit mantissa, so
     `sum` stays exact only while `(2r+1)*65535 < 2^24` ⇒ **r ≤ 127**. For
     larger radii the running sum loses exactness; document a radius ceiling for
     the GPU path and fall back to CPU above it (chroma_blur_radius is typically
     small, so this is acceptable).
   - **GL_CLAMP_TO_EDGE** sampling matches the engine's `MAX/MIN` edge clamp —
     the existing textures already set `ClampToEdge`.
   - Each separable pass truncates to uint16 *before* the next pass consumes it
     (the engine writes uint16 to `temp` after the H pass). The intermediate FBO
     must therefore be an integer-exact 16-bit target (RGBA16) and the shader
     must `floor` at the end of the H pass too, so the V pass reads the same
     truncated values the CPU did.
3. **First validation artifact:** a standalone `BlurImageBoxParity` test that
   runs the GPU box passes vs `blur_image` on a known buffer at several radii
   and asserts 0-LSB (it should be *exactly* reproducible — it is pure integer
   box math). Only after that passes do we wire chroma/sharpen on top.

`blur_image_threaded` is identical math (just pthread-sharded with a join
barrier between passes), so the single-thread `blur_image` is the oracle.

---

## 3. Recommended implementation order

Banking the bit-exact wins first, building the blur infra once, and making an
explicit decision on the recursive stages:

**Sub-phase A — per-pixel, bit-exact, no architecture change (proven slice
pattern):**
1. **highlight reconstruction** — cleanest first stage; one per-frame scalar
   uniform, no new textures, all-float-expressible. *(first stage to implement)*
2. **gradient** — vignette-class mask + a second LUT set; per-pixel but heavier.

**Sub-phase B — the box-blur FBO architecture lift (validate the blur in
isolation first, then stack):**
3. **box-blur pre-pass infra + `BlurImageBoxParity` 0-LSB test** (§2).
4. **chroma separation + chroma blur** — first consumer of the box blur.
5. **sharpen** — reuse the intermediate-texture infra (5-tap cross + optional
   sobel mask pass).
6. **median denoise** — single-pass neighbor read; implement if the cost is
   acceptable for preview.

**Sub-phase C — recursive bilateral filter + CA (explicit decision, NOT a port
by default):**
7. **shadows/highlights, clarity, RBF denoise, CA** — recommended outcome:
   **keep as labelled CPU fallback** (the gate keeps rejecting them, which is
   correct and honest per roadmap §9). Bit-exact GPU ports of a recursive
   bilateral filter and a sequential edge-window scan are fragile-to-infeasible
   under the strict gate, and the roadmap explicitly treats CPU fallback as a
   feature, not a failure. Revisit only as a future *tolerance-opt-in* preview
   path (approximate domain-transform / guided-filter), separate from the
   bit-exact parity gate.

**P-pre completion definition (updated):** the GPU preview path reaches parity
for every stage that is bit-reproducible under GLSL 110 + the strict gate
(Class A + Class B). Class A′ (grain, filter) are deferred pending a
texture-encoded-LUT + tolerance decision. Class C (RBF stages + CA) remain
CPU-fallback by design. At that point a normally-graded clip — including
chroma/sharpen/highlight-recon/gradient — runs on the GPU, and only the
inherently-sequential filters fall back to CPU (correctly labelled).

---

## 4. Per-stage apply-order map (where each inserts in the shader)

The current subset shader order is:
levels → matrix → vignette(expo) → in-loop-contrast → WB/gamut → AgX-fwd →
gamma → hue-vs → vibrance → saturation → toning → creative curves → AgX-inv →
LUT.

Engine insertion points for the remaining stages (from recon):
- **highlight reconstruction**: after WB+exposure, **before gamma** (early
  prelude, `raw_processing.c:3088`). Inserts in the shader right after the
  WB/gamut block, before the gamma LUT sample.
- **shadows/highlights + clarity**: earliest creative contributors — they
  multiply `expo_correction` at the very top of the creative block (before the
  contrast curve), driven by the RBF blur reference. (Class C — fallback.)
- **gradient**: blended after the base pixel's gamma, effectively the last
  per-pixel colour-loop step (`:3486`).
- **median / RBF denoise / CA / chroma / sharpen / grain**: the post-core
  spatial tail, in this order: median (`:1760`) → RBF denoise (`:1774`) → CA
  (`:1804`) → chroma sep/blur (`:1832`) → sharpen on Y (`:1849`) → leave-YCbCr
  (`:1971`) → grain (`:1976`). All AFTER the LUT in the colour core, so in the
  shader they append after the existing LUT stage.
- **creative filter**: the final pixel stage, after the .cube LUT (`:3772`).
