# RESUME HERE — MLV-App dual-lane work (git-TRACKED cross-account / cross-machine anchor)

This file is **tracked in git** (travels with the repo). The detailed, continuously-updated working anchor + the
per-step dual-lane ledgers live under `.claude-state/` (gitignored = machine-local). On the SAME machine, read
`.claude-state/SESSION-HANDOFF.md` + `.claude-state/coordination/dual-lane/{claude,codex}.md` for full detail and
the live cursor. This file is the self-sufficient fallback if `.claude-state/` isn't present.

## Who / how to resume
Dual-lane file collaboration: **Claude = reviewer / coordinator / packaging**, peer **Codex = GPU/recon implementer**.
Claude writes `.claude-state/coordination/dual-lane/claude.md`, reads `codex.md` (both auto-log every step as `## SEQ N`
entries). On resume: read SESSION-HANDOFF, restart the heartbeat Monitor (command in SESSION-HANDOFF), sync the cursor
(claude.md header `LAST_READ_CODEX_SEQ`), continue.
Layi directives: never `git add -A` (lane-commit.ps1 / `git apply --cached`); distributable packages = **7-Zip Ultra
.7z**; don't touch FOREIGN dirty WIP; Houston-time chat timestamps; 4K widgets.

## PRIORITY (Layi, 2026-06-30): DNG export + CUDA/GPU — fix, harden, polish.
(Rendered-video export + dev-tooling are deprioritized.)

## Working line `build/provenance-p0` — LOCAL/UNPUSHED. HEAD `ed1bffaf`.
`edba18ca` (x2 magenta-cliff) → `148445c7` (dual-lane infra) → `d34da2b1` (x2 too-dark, scale-gated) → `ed1bffaf` (GPU de-squeeze).

## In flight — two tracks
- **CODEX (GPU lane, on the 4090):** (1) **port shadows/highlights to GPU preview processing** — CRITICAL; this is the
  CUDA-on-Optimus root cause: Look Assist auto-applies shadows/highlights → `GpuPreviewProcessing` rejects them →
  `preview_compatible=0` → CPU, so CUDA never engages on Layi's Dell footage. (2) **M2** = no-readback CUDA
  texture-present into `GpuDisplayWindow` (realtime path; readback ~8fps too slow) — BUILT, held pending NVIDIA proof.
  Then GPU-export hardening (lossless +5.1% regression) + GPU default-promotion.
- **CLAUDE (me):** DNG-**export** quality — Dual-ISO x2 magenta on the EXPORT path + aspect de-squeeze on EXPORT
  (RAWC; export runs stretch=1.0 so DNGs come out squeezed) + export-path hardening.

## Banked wins
Black-screen fixed (M1 native QOpenGLWindow, validated on the Dell 3060). De-squeeze fixed (`ed1bffaf`). CUDA validated
on the RTX 4090. Layi's Dell = Optimus RTX 3060 Laptop + Iris Xe. Goal "A" = CUDA realtime HQ x1 on the Dell
(gated on the shadows/highlights GPU port + M2).

Full detail, the proven CUDA-engage env config, cursors, and the Monitor restart command:
**`.claude-state/SESSION-HANDOFF.md`** (and `.claude-state/project-memory/cuda-optimus-export-priority.md`).
