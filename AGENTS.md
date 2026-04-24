# Workspace Notes For Codex

## Sensitive Folders — Write Policy
- **DO NOT write new files into `.claude/`**. It is treated as a sensitive folder in this repo. Anything committed there was curated and should stay stable.
- Write all *new* agent scratch/state to `.claude-state/` instead. This includes profiling runs, temporary JSON, smoke-test logs, stashed artifacts, summary scripts, etc. `.claude-state/` is `.gitignore`d.
- Durable cross-session findings (the kind you want committed alongside a code change) still belong under `.claude/analysis/<topic>.md` — *editing* existing tracked notes there is fine, but do not create ad-hoc new files under `.claude/profiling/` or `.claude/` roots.
- Codex worktrees under `.claude/worktrees/` remain in place — that subtree is load-bearing.

## Investigation Discipline
- Scratch profiling / ephemeral measurements: `.claude-state/profiling/<date>-<topic>/`.
- Curated, cross-session findings: update existing `.claude/analysis/<topic>.md` rather than scattering new files.
- Use `.claude/ANALYSIS_LOG.md` only as the append-only historical log for already-tracked major investigations (do not create parallel logs elsewhere).
- Separate claims into:
  - `Verified locally`
  - `Cross-checked from prior analysis`
  - `Needs runtime profiling`
- Prefer code references in `path:line` form.
- Keep next-step recommendations ranked by impact and effort.

## Active Investigation Notes
- `.claude/analysis/mlv-playback-investigation.md`
- `.claude/analysis/testing-strategy.md`
- `.claude/analysis/testing-scaffold-implementation.md`

## Implemented Test Scaffold
- Seed automated coverage now lives under `tests/`.
- CI entrypoint for that scaffold is `.github/workflows/tests.yml`.
- Keep the docs above synchronized with what is implemented now versus still planned next.
