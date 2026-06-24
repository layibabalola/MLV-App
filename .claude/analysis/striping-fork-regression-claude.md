# Period-4 Playback Striping — Fork-vs-Upstream Comparative Analysis (CLAUDE)

_Author: Claude (GPU-lane reviewer/GUI-side implementer). Date: 2026-06-23 ~12:05 CDT.
Method: our own git history + `origin/master` (upstream ilia3101/MLV-App, present locally)
+ a 4-agent comparative workflow (3 read-only mappers + adversarial synthesis) + the A1/A2/A3
telemetry hardening already landed and **empirically verified** on the current build (exe sha
`2adb8e6a`). This doc is written to be compared adversarially against Codex's analysis doc._

---

## ADDENDUM — MEASURED, DECISIVE (2026-06-23 ~13:05 CDT): it is a period-3 → period-4 downsample MOIRÉ

The §5 decisive test was run (no rebuild — used the existing `MLVAPP_PIPELINE_CAPTURE_DIR` stage
capture on `large.mlv`, scale=1, **dual-ISO**, frame 3, then per-column-mean autocorr at NATIVE
pitch). Results supersede the H1/H2 framing below:

| Stage (native 1808-wide unless noted) | lag-2 | lag-3 | lag-4 | lag-6 | reading |
|---|---|---|---|---|---|
| S0_raw_uint16 | +0.99 | **−0.91** | +0.98 | +0.97 | **period-3 already present in RAW** |
| S1_pre_dualiso | +1.00 | **−0.99** | +1.00 | +1.00 | period-3 (amplified) |
| S2_post_dualiso (recon) | +1.00 | **−0.99** | +1.00 | +1.00 | period-3 (amplified) |
| S3_debayer (amaze) | +1.00 | **−0.97** | +0.99 | +0.99 | period-3 persists |
| S6_displaySource (input to scale) | +1.00 | **−0.97** | +0.99 | +0.99 | period-3 persists |
| **S6_displayImage (OUTPUT, 1197, scaler=smooth)** | **−0.94** | −0.06 | **+0.91** | −0.87 | **period-4** (the visible stripe) |

**Causal chain (empirical):** a native **period-3 vertical-column structure is present from the
RAW decode (S0) onward** — *not* introduced by the dual-ISO recon/debayer/processing (it only
*amplifies* it, S0 −0.91 → S2 −0.99) — and the fork's anamorphic ~1.51× horizontal display
downscale (1808→1197) **beats period-3 into the visible period-4** (6/1.51 ≈ 4 → period-4 moiré).
The output strip matches the baseline signature exactly (L2=−0.94/L4=+0.91 at the canonical harness
windows). It reproduces with the **smooth** scaler (capture had `scaler=smooth`), not just the NN
fast path — which is why "Fast vs avir identical" held: every resampler moirés the same period-3.

**This corrects two earlier conclusions (mine and the workflow's):**
- NOT "purely the GUI fast-path NN scaler originates it" — the smooth scaler moirés too, and the
  period-3 precursor is real and upstream.
- NOT "the dual-ISO full20 recon originates it" — period-3 is already in S0 raw, before recon.
- The period-3 is consistent with the **3× anamorphic capture's horizontal sensor sampling**
  (the clip is `stretch_x=3.0`); the recon/llrawproc *sharpen* it (S0→S2), worth a look.

**Fix ownership (revised, honest):** two valid, non-exclusive fixes —
1. **GUI-side (Claude), most direct for the visible preview:** the anamorphic downscale must
   **anti-alias (low-pass / area-average) before decimating** so it does not moiré the period-3.
   Upstream avoids this by **upscaling** the source by stretch (1808→5424, no decimation aliasing)
   and letting the view fit — the fork instead CPU-**downscales** straight to the fit size. Matching
   upstream, or using a proper area resampler for >1 downscale ratios, removes the period-4.
2. **Core-side (Codex), root of the period-3 itself:** the period-3 is in S0 raw and *amplifies*
   through `llrawproc`/dual-ISO recon (S0 −0.91 → S2 −0.99). Worth confirming whether a llrawproc
   step (vertical-stripe correction? chroma smooth?) or the full20 recon sharpens it, and whether
   it should be suppressed (it would also affect full-res export, not just the downscaled preview).

The single highest-leverage, lowest-risk fix is **(1)** — it kills the *visible* preview striping
deterministically and is squarely GUI-side. **(2)** is a deeper question about whether the period-3
is a benign anamorphic-sampling structure or a defect that also harms full-res output.

---

## TL;DR verdict

1. **The striping is a FORK REGRESSION, not "pre-existing / inherent to MLV-App."** The entire
   prescaled-playback display path that the artifact is localized to is **fork-only** — it does
   not exist in upstream `origin/master`. Upstream's preview never does a second anamorphic CPU
   resample to a small fit-size.
2. **Introducing commit: `6303ddb3`** (2026-04-23, *"Add render-slot playback prescale path"*) —
   the first commit to add `platform/qt/PlaybackScaling.h` (custom fast/bilinear/cubic RGB8
   scalers) and the `computeDisplaySceneGeometry` → `.scaled(sceneW, sceneH, IgnoreAspectRatio)`
   architecture. `970bc389d` (2026-04-24) made it async; `1d532c14e` (2026-05-27) refactored. None
   of this is upstream.
3. **The exact mechanism (display-scaler vs carried-from-recon) is NOT yet proven** and is the one
   open question. Two competing hypotheses remain; a single cheap test splits them. **I have not
   run that test yet** — running it is the immediate next action.
4. **Honest caveat I am holding against myself:** my earlier "it's the GUI display resample"
   localization is *plausible but not yet decisively proven*. The resampler is demonstrably **not
   the discriminating factor** between Fast and avir (see §4), so "the fast path originates it" is
   not safe to assert until the input-vs-output test is run.

---

## 1. The fork divergence (what is fork-only)

**Upstream (`origin/master`) preview display** applies the anamorphic stretch by multiplying the
**source image dimensions** by the stretch factors and scaling to *that*, then lets the
`QGraphicsView`/scene fit it to the viewport:

- `origin/master:platform/qt/MainWindow.cpp` ~9518–9586: `.scaled(width*stretchX, height*stretchY,
  IgnoreAspectRatio, mode)` (or the avir `resizeImage(... sourceWidth*stretchX, sourceHeight*stretchY)`),
  then the scene rect is set to those dimensions. The pixmap **is** the native-res image scaled by
  stretch; there is **no** second prescale to a small fixed output size. On-screen downscaling to
  the viewport is the view transform (GPU/bilinear), not a CPU resample.

**Our fork (HEAD)** inserts a NEW intermediate step: it bakes stretch into
`sceneWidth/sceneHeight` and does a **single anamorphic CPU resample of the unsqueezed source
straight to the fit-size** (e.g. native 1808×2268 → 1197×500), `IgnoreAspectRatio`:

- `platform/qt/MainWindow.cpp:3913–3989` (`buildPlaybackPrepResult` display-image build): the
  fast path `build_fast_playback_scaled_image` (`:3933`), the Qt `.scaled(sceneWidth, sceneHeight,
  IgnoreAspectRatio, mode)` fallback (`:3984`), and the avir `CImageResizer` path (`:3959–3977`).
- The frame grab (`platform/qt/GpuDisplayViewport.cpp:662`, `maybeGrabDisplayFrame` at `:70`)
  captures the **output** of this resample — confirmed 1197×500 in every grab.

`git blame` confirms `MainWindow.cpp:3942–3990` is 100% authored by us (Layi) in `970bc389d` /
`1d532c14e`; the architecture originates in `6303ddb3`. **Conclusion: the resample stage the
artifact appears in is a fork addition.** That is the strongest, least-disputable result.

## 2. Localization chain (post-A1 trustworthy telemetry)

The A1/A2/A3 telemetry hardening is already landed + built + **verified emitting** (exe `2adb8e6a`).
On the striping config (gui-smoke, scale=1, dual-ISO clip `C:\mlvtmp\large.mlv`,
`MLVAPP_PLAYBACK_SMOKE_TELEMETRY=1`), the per-frame `playback_smoke.render_manifest` line reports,
steadily across frames:

```
path_code=0 path_label=full-recon-or-none path_source=render_thread reduced=0 proxy_halvings=0
dual_iso_valid=1 dual_iso_use_fullres=1 dual_iso_interp=1
src_w=1808 src_h=2268 rendered_w=1808 rendered_h=2268 stretch_x=3.0000 stretched_w=5424
```

This is **now trustworthy** because A1 reads `mlv_phase4bv2_last_path_taken()` unconditionally
(the prior scale≤1 ternary zeroed it) and `path_source=render_thread` (not a stale processed8
cache tag). It establishes: **rendered == src (no reduction)**, dual-ISO **full20** recon active
(`use_fullres=1`), and the 3× anamorphic stretch is applied at the display scale.

## 3. Two competing mechanisms (the open question)

### H1 — the fork display scaler ORIGINATES the period-4
- `build_fast_playback_scaled_image` → `playbackBuildFastScaledRgb8` in `PlaybackScaling.h:464–581`
  is **nearest-neighbor** with a 4-pixel unroll (`:538–561`). Its per-column offset cache
  `srcX = (x*sourceWidth)/targetWidth; off = srcX*3` (`:509–516`) yields a repeating **[+3,+6,+3]
  byte** source-sample spacing at the 1808→1197 ratio → a period-4 output cycle.
- The sibling bilinear (`:601–764`, Q0.8 fixed-point) and cubic (`:771–900`, 4-tap float) scalers
  use sub-pixel interpolation and would **not** produce this.
- Predicts: stripe present only when the **fast** path runs; replacing it with bilinear/cubic for
  anamorphic ratios fixes it. **GUI-side fix (Claude).**

### H2 — the period-4 is ALREADY in the input; the scaler only CARRIES it
- A simulation in the synthesis strand: feeding a synthetic period-4 source through NN vs box/avir
  downsample at 1808→1197 gives **near-identical** output autocorrelation (NN lag4 −0.72/lag8
  +0.90 vs box −0.81/+0.93), matching the measured L2=−0.95/L4=+0.89.
- This **explains the prior "Fast vs avir identical" observation** (project memory): if the input
  is already striped, every resampler reproduces it, so the resampler is **not the discriminating
  factor**. Under H2, H1's "fast-path-only" claim is wrong.
- Predicts: the period-4 is in `rgb8DisplaySource` (the processed/recon RGB) *before* the display
  scale. Origin would be upstream of the scale — debayer/processing or the dual-ISO recon.
  **Core-side fix (Codex)** if it sits in the recon; GUI/processing if earlier.

## 4. Adversarial tensions I am explicitly holding open

- **"Fast vs avir identical" vs "fast-path-only" directly conflict.** The scaler-internals strand
  asserts only the fast NN path stripes; the synthesis simulation + the prior observation assert
  all resamplers carry it. **Unresolved.** Caveat on the prior observation: it was never confirmed
  that the Fast↔avir toggle actually *reached* this window-mode call — if it did not switch paths,
  "identical" proves nothing. Must re-verify.
- **The synthesis's "source is half-res" sub-claim is stale for this config.** It reasons from the
  *pre-A1* tag-zeroing bug to argue the x1 half-res dual-ISO proxy secretly ran. But A1 is landed
  and my A3 manifest (post-A1) shows `reduced=0` + `dual_iso_use_fullres=1` + `path_source=
  render_thread` for these exact frames → the half-res proxy did **not** run here. I am **refuting**
  my own workflow's half-res-origin theory with direct measurement. (This is the repo's recurring
  lesson: empirical beats confident code-reasoning.)
- **"Pre-stretch the source dimensions" is NOT a safe fix** (divergence strand proposed it). The
  synthesis notes `computeDisplaySceneGeometry`'s zoom-fit math is already algebraically equal to
  upstream's `desWidth/desHeight`, so that theory is refuted; do not ship it blind.

## 5. THE decisive test (splits H1 from H2) — pending, next action

No full rebuild of logic needed beyond one env-gated buffer dump:

> Dump and measure **per-column-mean autocorrelation** of the **INPUT** to the suspect scale
> (`rgb8DisplaySource` at `sourceWidth×sourceHeight`, just before `MainWindow.cpp:3933`) vs the
> **OUTPUT** (the 1197×500 `displayImage` captured at `GpuDisplayViewport.cpp:662`) on the **same**
> dual-ISO frame, at matched normalized frequency.

- **INPUT already period-4 (strong L4)** ⟹ scaler exonerated; origin is upstream of the display
  scale (recon/processing) → **core-side (Codex)**, and Codex's `S2_post_dualiso` "smooth" result
  must be re-examined at the *native* column scale.
- **INPUT flat, period-4 only in OUTPUT** ⟹ the fork display scaler (NN fast path) → **GUI-side
  (Claude)**; fix = use bilinear/cubic (or avir) for anamorphic/large-ratio scales.

My prediction (weak prior, ~60/40): given "Fast vs avir identical" and the dual-ISO Bayer
horizontal correlation, I lean H2 (input carries it) — but I will **let the measurement decide**,
not this prior.

## 6. Fix ownership & candidate fixes (per branch)

| Outcome of §5 test | Owner | Fix |
|---|---|---|
| Output-only stripe | Claude (GUI) | Route anamorphic/large-ratio playback scales through bilinear/cubic in `PlaybackScaling.h`, not NN fast path; or match upstream (scale source by stretch, let the view fit). |
| Input already striped | Codex (core) | Locate the period-4 in the dual-ISO full20 recon / processing producing `rgb8DisplaySource`. |

If GUI-side, `contentReviewGate` flips to CLAUDE-implements / CODEX-reviews.

## 7. Introducing commit

`6303ddb3` — *"Add render-slot playback prescale path"* (2026-04-23): first to add
`platform/qt/PlaybackScaling.h` and `computeDisplaySceneGeometry` + the unsqueezed-source
anamorphic `.scaled(sceneW, sceneH, IgnoreAspectRatio)` architecture. Confirmed post-fork
(after `e4c6d7de`), present in HEAD. (If the §5 test puts the origin in the recon, the *originating*
commit is instead whichever added the relevant dual-ISO/recon path in `video_mlv.c`/`dualiso.c`.)

---

## Appendix — workflow strand signals (for the adversarial merge)

| Strand | Signal | Core claim |
|---|---|---|
| upstream-path | contradicts_fork_regression* | Fork prescale block is new; frames it as architectural anamorphic-aliasing rather than a discrete bug (*signal label vs content are at odds — content supports fork-origin). |
| fork-divergence | supports_fork_regression | Introducing commit `6303ddb3`; upstream pre-stretches source dims. |
| scaler-internals | supports_fork_regression | NN fast-path `PlaybackScaling.h:464–581` [+3,+6,+3] originates period-4; fast-path-only. |
| synthesis | regression=True, fixOwner=unclear | Architecture is fork-only, but the EXACT period is likely carried from the input, not originated by the scaler; run the input-vs-output test; do not trust the stale half-res sub-claim. |

**Bottom line (Claude):** it IS a fork regression (architecture introduced by `6303ddb3`, absent
upstream), but whether the period-4 *originates* in the fork display scaler or is *carried* into it
from `rgb8DisplaySource` is unresolved and decided by the §5 input-vs-output measurement — which I
will run next. I am deliberately not over-claiming the GUI-scaler origin until that measurement lands.

---

## DEEPER CODE CROSS-CHECK (2026-06-23 ~13:25 CDT): the reduction-only HQ branch is the correct interception point

I re-read the current `MainWindow.cpp` geometry path against the upstream fork comparison, and one
important nuance is now clear:

1. The fork is still the architectural source of the problem surface. `origin/master` does not have
   the prescaled playback fit-size CPU-resample path at all; it stretches the source and lets the
   view fit. The fork does a genuine intermediate resample to the fit-size.
2. The new `hqPlaybackDownscale` branch is **not** the same thing as the regular fast/smooth
   presentation mode labels. The label in the capture metadata (`scaler=fast|smooth`) is only a
   coarse mode label, not a one-to-one statement about which resampler family ran.
3. The branch is scoped to the exact reduction case:
   - it only fires when playback is active,
   - the source is RGB8,
   - and the target fit-size is smaller than the source width.
   That makes it the right place to intercept a moiré caused by decimation.
4. That scoping matters for interpretation:
   - x2/x4 upscale-to-fit lanes are not touched by this mitigation,
   - the visible striping remains tied to the x1 reduction case,
   - and the fix is therefore a preview-side mitigation, not a blanket quality change.
5. The code now matches the empirical story better than the earlier fast-path-only theory:
   - a raw period-3 structure can exist upstream,
   - a reduction-only resampler can turn it into visible period-4,
   - and a wider-support low-pass such as avir is exactly the sort of filter that should collapse that aliasing.

The remaining open question is narrower than before:

- Is the native period-3 in the raw/recon chain a benign anamorphic sampling structure or a quality
  defect that also deserves a core-side fix?
- The preview-side fix is still valid either way, because it suppresses the visible moiré without
  changing the raw/recon chain.
- For root-cause ownership, the current evidence favors the combination of upstream periodic
  structure plus fork-only reduction-to-fit presentation, not a single fast-path bug.

---

## FIX LANDED + VERIFIED (2026-06-23 ~13:57 CDT)

Fix #1 implemented and committed (`a978c8e4` + stride fix `0d30f461`): a new
`hqPlaybackDownscale` branch in `buildPlaybackPrepResult` (`MainWindow.cpp:~3938`) routes the
playback DOWNSCALE through the in-tree avir `CImageResizer` (Lanczos-class wide-support low-pass)
instead of the fixed 2-tap NN/bilinear path. Scoped to playback reductions only
(`hqTargetWidth < sourceWidth`) so x2/x4 upscale-to-fit lanes are untouched. Env kill-switch
`MLVAPP_DISABLE_PLAYBACK_HQ_DOWNSCALE`.

**Resampler choice (empirical, on the captured native input):** LANCZOS-class kills the period-4
(L2 −0.95 → +0.68/+0.89); NN/box/bilinear do **not** (the period-3 sits at the downscale Nyquist
~0.333 vs cutoff ~0.331 — needs a sharp wide-support low-pass). avir is the in-tree equivalent.

**A stride bug caught by the eye, not the metric:** the first build (`a978c8e4`) built a
`Format_RGB888` QImage straight from avir's tightly-packed output, but the QImage 4-arg ctor
4-byte-aligns the scanline (1197×3=3591 → 3592) → per-row byte shift → **horizontal green/magenta
banding**. The per-column-mean autocorr (averaging over rows) is **structurally blind** to a
horizontal artifact, so it reported "clean" while the 2D crop showed banding. Fixed in `0d30f461`
(avir → packed temp → copy into a 4-byte-aligned backing → QImage with explicit aligned stride).

**Final verification (sha `227d89a5`, large.mlv scale=1 dual-ISO, S6_displayImage):**
- VERTICAL (col-mean autocorr): (600,230) L2=+0.59, (750,280) L2=+0.81 → period-4 moiré gone.
- HORIZONTAL (row-mean autocorr): L2/L3/L4 all positive → no banding.
- VISUAL: 6× crop is clean (real ripple detail preserved, no moiré, no banding).

**Lesson (recorded):** verify resampler/image changes with a 2D/visual check, not only a 1D
column metric — the metric lied here; the eye caught it. (Same theme as the earlier
direct-measurement-beats-confident-reasoning wins in this investigation.)

## RETIRED CORE PROBE (2026-06-23 ~15:46 CDT): the playback-scoped full-res skip was removed

Claude's later measurements closed the loop: the probe did not isolate a clean raw/recon defect, so
the code path itself was removed and only the historical learning remains below.

**Status:** the retired probe is no longer present in code. The native period-3 ROOT (present S0 →
amplified S2) remains a separate core-side question in the history, but the striping work is now
closed as a preview-side fix.

## RESOLVED / CLOSED (2026-06-23 ~15:38 CDT)

Consensus (Claude + Codex): the period-4 dual-ISO playback striping is a **preview-side** issue,
**not** a core-side bug.

- **Root mechanism:** a native **period-3** column structure (present in `S0_raw_uint16`, L3=−0.91,
  consistent with the clip's 3× anamorphic horizontal sensor sampling; amplified to −0.99 *before*
  the dual-ISO recon, in the pre-dualiso llrawproc fixes) is beaten into the visible **period-4** by
  the fork's anamorphic CPU display downscale (introduced by `6303ddb3`).
- **Fix (shipped, GUI-side):** `hqPlaybackDownscale` routes the playback downscale through avir
  (Lanczos-class low-pass) — commits `a978c8e4` + stride fix `0d30f461`. Verified: vertical period-4
  gone, no horizontal banding, image intact; confirmed on the combined build `DC31EA39`. Perf:
  avir ~40ms/−18% FPS at x1 ONLY (x2/x4/x8 untouched); env kill-switch
  `MLVAPP_DISABLE_PLAYBACK_HQ_DOWNSCALE`.
- **Why no core fix:** the period-3 is real raw data (anamorphic sampling), present before any
  processing. At full-res **export there is no downscale → no moiré**, so there is nothing to fix
  there; a core-side "removal" would alter legitimate raw data and risk export fidelity. The recon
  probe (`MLVAPP_EXPERIMENTAL_DUALISO_SKIP_FULLRES_RECON` / `diso_frblending=0`) was found to be the
  **wrong knob** (it disables the dual-ISO blend and breaks the image rather than isolating the
  stripe; S2 mean 9481→255) and is being **retired** (Claude recommended removal; Codex's call).

**Outcome:** preview striping **fixed**; investigation **closed**. Remaining: Codex's formal APPROVE
of `0d30f461` and final coordination closeout.
