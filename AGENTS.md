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

## Agent Bridge Startup
- On session open in this repository, initialize the agent bridge before normal relay work.
- For Codex-specific bridge heuristics, wake-loop behavior, and routing policy, also read `bridge_trigger_heuristics.md`.
- Before any substantive bridge-related response, run:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-bridge\codex_pre_response.ps1 -RepoRoot .`
- Before any final response after bridge-related work, run:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-bridge\codex_pre_final.ps1 -RepoRoot .`
- These pre-response/pre-final scripts are workflow reminders, not consumers. They must not inspect message bodies, mark messages read, or replace explicit inbox hygiene.
- Use:
  - `py -3 tools\agent-bridge\bootstrap_session.py --state-dir C:\Users\obabalola\.agent-bridge\state --agent codex --cwd C:\!Layi Wkspc\MLV-App --watcher-config C:\Users\obabalola\.agent-bridge\watcher-config.json`
- Bootstrap does four things:
  - derives the canonical project/rendezvous identity,
  - activates this Codex session and supersedes any older same-agent session,
  - drains any previous same-agent unread messages once,
  - sends the bridge `HANDSHAKE` and refreshes `watcher-config.json` with the active private GUID plus the rendezvous/control-plane entry.
- After bootstrap:
  - surface any drained previous-session messages in the chat,
  - use the returned active session GUID for bridge traffic,
  - if bridge consumption reports `SESSION_UPDATE: superseded`, stop bridge communication in this session.
  - start the Codex-side `wait_inbox` loop described in `bridge_trigger_heuristics.md`.

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
- After every release build intended for manual dogfood or Explorer double-click launch, make the release folder self-contained for the MinGW runtime. Copy these DLLs from `C:\Qt\Tools\mingw1310_64\bin` into the directory containing `MLVApp.exe`, then verify they exist there:
  - `libgcc_s_seh-1.dll`
  - `libstdc++-6.dll`
  - `libwinpthread-1.dll`
  - `libgomp-1.dll`
  - This is required even when command-line launches work, because Explorer does not inherit the Codex shell `PATH`.
- For a repeatable launch with less chance of error, use:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File .claude-state\\scripts\\run-mlvapp.ps1 -ExePath <path-to-MLVApp.exe> -Arguments '--help'`
  - if you changed Qt paths, pass `-QtBin ...` and `-MingwBin ...`.

## Runtime helper
- Use `.claude-state\\scripts\\run-mlvapp.ps1` for deterministic launches:
  - prepends the correct Qt and toolchain bins,
  - sets `QT_OPENGL=desktop`,
  - optionally runs `windeployqt` in-place,
  - and launches `MLVApp.exe` with supplied arguments.
