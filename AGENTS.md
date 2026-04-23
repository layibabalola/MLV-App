# Workspace Notes For Codex

## Investigation Discipline
- Record actionable findings in `.claude/analysis/<topic>.md`.
- Use `.claude/ANALYSIS_LOG.md` only as the append-only historical log for already-tracked major investigations.
- Update existing topic notes on follow-up work instead of scattering findings across multiple files.
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
