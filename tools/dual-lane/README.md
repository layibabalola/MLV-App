# Dual-lane mechanical guardrails

Fail-closed safeguards so the file-based Codex<->Claude collaboration (two agents, one shared working
tree, one local-only branch) cannot hit the shared-tree hazards by accident instead of by discipline.

## The model
- One repo, one branch (`build/provenance-p0`), one shared working tree. Both lanes commit to it.
- Codex = implementer (`src/mlv/**`, `src/processing/**`). Claude = reviewer (`platform/qt/**`, `tools/**`,
  `tests/**`, `docs/**`, `.claude/**`, `src/batch/**`).
- Coordination is append-only files under `.claude-state/coordination/dual-lane/`.

## Pieces
| File | Role |
|------|------|
| `.dual-lane/ownership.json` | THE machine-readable `path -> codex\|claude\|shared\|unknown` map. Single source of truth. |
| `owner-of.ps1 <path>` | The one resolver every guardrail calls. `unknown` = fail-closed block. |
| `lane-commit.ps1 -Lane <lane> [-Message <m>] [-DryRun]` | The ONLY sanctioned commit path: stages only the lane's owned dirty paths by explicit pathspec, never `git add -A`, so the other lane's WIP can't be swept in. Adds a `Dual-Lane:` trailer for attribution. |
| `lane-guard.ps1` | Pre-commit backstop for when someone types raw `git commit -a`. Reads the active lane from env `GIT_DUAL_LANE`. |
| `hooks/pre-commit` + `install-hooks.ps1` | Version-controlled hook + installer (`-Activate` sets `core.hooksPath`). |

## Usage
```pwsh
# Per session, declare your lane (env, NOT a shared file -- a shared tree would clobber a file):
$env:GIT_DUAL_LANE = 'claude'   # or 'codex'

# Preview what your lane would stage (nothing committed):
pwsh -File tools/dual-lane/owner-of.ps1 -Path platform/qt/MainWindow.cpp     # -> claude
pwsh -File tools/dual-lane/lane-commit.ps1 -Lane claude -DryRun

# Commit only your lane's files:
pwsh -File tools/dual-lane/lane-commit.ps1 -Lane claude -Message "subject line"
```

## Activation status / rollout
- `owner-of.ps1` + `lane-commit.ps1` are usable immediately by either lane (no global state).
- The `pre-commit` hook is NOT activated by default: it fail-closes when `GIT_DUAL_LANE` is unset, so it
  must not be turned on until BOTH lanes set the env (or use `lane-commit.ps1`). Coordinate via the ledger,
  then `pwsh -File tools/dual-lane/install-hooks.ps1 -Activate`. Escape hatch: `GIT_DUAL_LANE_OVERRIDE=1`.

## Deferred (need a quiesced tree or cross-lane agreement)
- A root `.gitattributes` EOL pin (kill the phantom-CRLF churn) -- run `git add --renormalize .` on a clean
  tree only, so it does not touch the other lane's dirty WIP.
- Build-stamp v2: add a working-tree content hash so a dirty candidate is still SHA-pinnable (closes the
  "every dirty candidate stamps the same sha-dirty" gap).
- Promote cross-lane interface headers to `shared` (Two-Key trailer required).
