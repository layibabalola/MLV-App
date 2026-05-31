# MLV-App LLM Synthesis And Implementation Plan

Author: Codex
Date: 2026-05-31

## Objective

Turn five rounds of adversarial multi-LLM synthesis into one concrete, conservative implementation plan for the MLV-App Dual ISO playback performance investigation.

The current working constraints remain:

- Preserve the visible x1 Quality smoke gate.
- Preserve settled Auto Look Assist.
- Preserve `dual_iso_alias_map=0`.
- Preserve `processed8_direct_path_frames=0`.
- Stay byte-exact on the golden path.

## What Changed Across The Five Rounds

### Round 1

The first pass was broad and partly speculative. The useful signal was:

- Measure before coding.
- `final_blend_ms` is too opaque to optimize safely.
- Broad `mix_chroma -> final_blend` fusion is attractive in theory, but the source topology is not simple enough to trust yet.
- Source-grounded guidance began converging on the narrower `final_blend -> convert_20_to_16bit` handoff instead of a generic “fuse the hot stages” move.

The weak points in round 1 were the more generic ideas:

- U/V chroma fusion
- EV threshold early-exit
- AoS/SoA style layout rewrites

Those ideas did not match the code structure cleanly enough to trust.

### Round 2

The second pass sharpened the plan into a ranked hybrid:

1. Add `final_blend` sub-bucketing and probe variants.
2. Only then try the narrow `final_blend -> convert_20_to_16bit` fusion.
3. Consider EV-domain side planes only if gathers dominate.
4. Consider strip-mined Dual ISO only if the halo/dependency proof exists.
5. Keep creative-path LUT composition and RBF work as secondary.

This round introduced the important discipline that we should not optimize from bucket names alone. We need to know whether `final_blend` is gather-bound, store-bound, or ALU-bound before choosing the patch.

### Round 3

The third pass was the first real consensus document. It established:

- The best implementation plan is the GPT-5.5 Pro / ChatGPT phased sequence, with Claude’s proof discipline.
- Broad `mix_chroma -> final_blend` fusion is still too risky as the first patch.
- The soundest immediate action remains `final_blend` sub-bucketing.
- The practical implementation candidate is still `final_blend -> convert_20_to_16bit`.

This round also made the proof obligations explicit:

- The dither seed must remain `k = (y * 7) & 1023`.
- The memory-lifecycle proof matters: the retained path must not require `raw_buffer_32` between final blend and conversion.
- Any EV-plane reuse must preserve the exact LUT mapping, not approximate it.

### Round 4

The fourth pass turned consensus into a concrete decision tree:

- Do not write the fused kernel yet.
- Implement Phase 0 measurement first.
- If Phase 0 shows store/load pressure, prototype the narrow fusion.
- If Phase 0 shows raw-gather dominance, consider EV-domain side planes.
- If Phase 0 shows ALU saturation, stop trying to beat the keeper locally.

This round is the pivot from “possible ideas” to “gated implementation strategy.”

### Round 5

The fifth pass produced meta-convergence. Even the models that had earlier favored broader fusion or more generic image-pipeline ideas converged on the same operational next step:

- Add sub-bucket instrumentation to `final_blend`.
- Measure total frame time, not just isolated sub-buckets.
- Only then decide whether `final_blend -> convert_20_to_16bit` is worth coding.

The final important lesson from round 5 is that broad fusion, layout rewrites, polynomial EV approximations, and early-exit thresholds are now effectively closed. The remaining decision is between:

- narrow output-preserving fusion, or
- stopping local CPU work and moving to secondary buckets.

## Final Converged Implementation Plan

### Phase 0: Mandatory Measurement Patch

Add fine-grained `final_blend` sub-bucketing and probe modes before writing another optimization patch.

Instrument:

- `final_blend_setup_ms`
- `final_blend_row_kernel_ms`
- `final_blend_raw2ev_gather_probe_ms`
- `final_blend_fullres_curve_gather_probe_ms`
- `final_blend_ev2raw_store_probe_ms`
- `final_blend_arithmetic_probe_ms`
- `final_blend_overexposed_density`
- `final_blend_cap_clamp_pct`
- `final_blend_f_near_0_pct`
- `final_blend_f_near_1_pct`

Probe modes to add:

- raw2ev only
- fullres_curve only
- ev2raw_store only
- arithmetic only
- full kernel

Decision rule for Phase 0:

- If gather/store pressure is meaningful and `convert16_ms` is material, proceed to Phase 1.
- If raw gather dominates, consider EV-domain side planes next.
- If ALU saturation dominates, stop trying to improve the keeper with local CPU changes.

### Phase 1: Narrow Fusion Candidate

If Phase 0 justifies it, prototype a new retained-path function that fuses:

- `final_blend`
- `convert_20_to_16bit`

This fusion is the strongest concrete candidate because it removes a real full-frame `raw_buffer_32` round-trip while staying close to the existing math.

Requirements:

- Preserve the exact row dither seed `k = (y * 7) & 1023`.
- Preserve conversion ordering and rounding behavior.
- Keep the old path behind a switch until byte identity is proven.
- Check for register spills in assembly before trusting the win.

Validation:

- Byte-identical comparison against the current path.
- Three-clip playback smoke gate.
- Golden hash verification.
- Compare total playback time, not only `final_blend_ms`.

### Phase 2A: EV-Domain Side Planes, Only If Gather-Dominant

If Phase 0 shows the gathers dominate, consider EV-domain side planes for:

- `halfres_smooth`
- `fullres`
- `fullres_smooth`

Only do this if the exact EV mapping can be preserved and the memory traffic does not erase the win.

### Phase 2B: Strip-Mined Dual ISO Stack, Only With Halo Proof

Broad `mix_chroma -> final_blend` fusion remains too risky as a first patch. If we ever revisit the broader stack, it should be as a strip-mined or tiled pipeline, and only after the stencil / halo behavior of the chroma smoothing path is proven.

This is blocked on:

- `chroma_smooth.c` inspection
- bounded dependency radius proof
- byte-identity validation

### Phase 3: Secondary Buckets

Only move here if the new measurements show the retained Dual ISO path is no longer the best target.

Secondary candidates:

- Creative contrast + gradation LUT composition in `raw_processing.c`
- RBF work, but only with a written recurrence proof if the vertical passes are actually large enough to matter

## Explicit Non-Targets

Keep these closed for this work block:

- Dual ISO mix-curve clamp elision
- Dual ISO full-res final-blend float-curve caching
- Dual ISO no-alias `final_blend` dispatch
- Dual ISO chroma border-copy specialization
- Dual ISO mix-curve bandfill rewrite
- RBFilter row-stride hoist
- Unproven RBF vertical-pass parallelization
- Generic color-loop coefficient hoists
- `highlight_reconstruction` zero-case split
- Shadow/highlight curve-index toggle profiling
- Debayer store-side pointer cleanup
- Pixel saturation helper cleanup
- Polynomial EV-LUT replacement
- EV-threshold early-exit
- Broad `mix_chroma -> final_blend` as the first patch

## What We Learned

1. The keeper has likely exhausted easy output-preserving micro-optimizations.
2. `final_blend_ms` is the right place to spend the next measurement effort.
3. The narrow `final_blend -> convert_20_to_16bit` fusion is the best concrete patch candidate.
4. Broad Dual ISO fusion is not first-patch-safe because the dependency radius is not trivial.
5. If Phase 0 shows ALU saturation, the honest outcome is to stop CPU-local optimization and move to secondary work.

## Handoff Summary For The Next Session

Read this note first, then:

1. Confirm the current repo state and the visible smoke constraints.
2. Add `final_blend` sub-bucketing and probe modes.
3. Re-run the three-clip keeper gate.
4. Decide whether Phase 1 is justified.

Do not start with a new optimization patch until Phase 0 exists.
