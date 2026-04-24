# Workspace Notes For Codex

## Sensitive Folders -- Write Policy
- **DO NOT write new files into `.claude/`**. It is treated as a sensitive folder in this repo. Anything committed there was curated and should stay stable.
- Write all *new* agent scratch/state to `.claude-state/` instead. This includes profiling runs, temporary JSON, smoke-test logs, stashed artifacts, summary scripts, etc. `.claude-state/` is `.gitignore`d.
- Durable cross-session findings (the kind you want committed alongside a code change) still belong under `.claude/analysis/<topic>.md` -- editing existing tracked notes there is fine, but do not create ad-hoc new files under `.claude/profiling/` or `.claude/` roots.
- Codex worktrees under `.claude/worktrees/` remain in place -- that subtree is load-bearing.

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

## Runtime Execution Rules (Windows)
- Before running any `MLVApp.exe` binary directly, always use a Qt runtime path that matches the binary and force it for that launch.
- Required shell pattern before launch:
  - set `QT_OPENGL=desktop`.
  - set `PATH` so the active Qt runtime comes first, then the active MinGW toolchain, then the exe folder:
    - `C:\Qt\6.10.2\mingw_64\bin`
    - `C:\Qt\Tools\mingw1310_64\bin`
    - `<directory containing MLVApp.exe>`
  - launch from the exe directory (or pass absolute paths).
- Do not mix `C:\Qt\6.10.2\mingw_64` runtime binaries with a different Qt runtime in the same launch session.
- For profile/test runs, prefer:
  - `Set-Location <build-root>\release`
  - `$env:QT_OPENGL='desktop'`
  - `$env:PATH='C:\Qt\6.10.2\mingw_64\bin;C:\Qt\Tools\mingw1310_64\bin;' + (Get-Location) + ';' + $env:PATH`
  - `.\MLVApp.exe ...`
- If the system reports missing `Qt6Core.dll` / `Qt6Network.dll` or entry-point lookup failures, rerun:
  - `C:\Qt\6.10.2\mingw_64\bin\windeployqt.exe <path-to-MLVApp.exe> --release --no-translations --no-compiler-runtime`
- For a repeatable launch with less chance of error, use:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File platform\qt\scripts\run-mlvapp.ps1 -ExePath <path-to-MLVApp.exe> -Arguments '--help'`
  - if you changed Qt paths, pass `-QtBin ...` and `-MingwBin ...`.

## Runtime helper
- Use `platform\qt\scripts\run-mlvapp.ps1` for deterministic launches:
  - prepends the correct Qt and toolchain bins,
  - sets `QT_OPENGL=desktop`,
  - optionally runs `windeployqt` in-place,
  - and launches `MLVApp.exe` with supplied arguments.
