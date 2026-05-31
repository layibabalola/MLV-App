## New Session Handoff - 2026-05-31

### Objective

Continue MLV App GUI playback performance work iteratively while preserving the x1 Quality visual state and avoiding regressions.

### Current State

- Branch: `codex/work-block/wb-d99f83c021f84327`
- Repo root: `C:\!Layi Wkspc\MLV-App`
- Worktree status: clean except for the durable investigation note
- Only dirty file: [`.claude/analysis/mlv-playback-investigation.md`](C:/!Layi%20Wkspc/MLV-App/.claude/analysis/mlv-playback-investigation.md)
- User-facing release exe: [`platform/qt/build-release/release/MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe)

### Most Recent Probe

- File touched: [`src/processing/raw_processing.c`](C:/!Layi%20Wkspc/MLV-App/src/processing/raw_processing.c)
- Probe idea: cache `processing->proper_wb_matrix[...]` outside the hot generic color loop and reuse it in the `use_cam_matrix` path
- Outcome: rejected on throughput and reverted
- Build result: release tree rebuilt successfully after revert
- Release exe metadata after the restore build:
  - `LastWriteTime`: `5/31/2026 1:46:10 PM`
  - `Length`: `8797184`
  - `SHA256`: `AADB800050C8DB5549FC0A9FB728C8FF0611675D5756D895A0B3C5A5FE76DC10`

### Smoke Gate Summary

The visible x1 Quality gate stayed intact on all three clips, with settled Auto Look Assist preserved and the direct8 guard intact.

Probe settled-frame averages:

- `M16-1327`: `average_cadence_ms=488.82`, `average_latency_ms=1005.46`, `render_thread_work_ms=339.9999`, `llrawproc_ms=150.0001`
- `M16-1347`: `average_cadence_ms=474.26`, `average_latency_ms=689.01`, `render_thread_work_ms=303.0000`, `llrawproc_ms=131.0000`
- `M16-1446`: `average_cadence_ms=369.40`, `average_latency_ms=648.30`, `render_thread_work_ms=227.9999`, `llrawproc_ms=58.0001`

### What To Know

- The current keeper for this region is still `ed2821e1`.
- The latest probe was a throughput reject, not a visual regression.
- The prior stale split worktree/transaction branch blocker was cleaned up by repo sweep.
- Closeout recovered the dirty note onto this branch, which is why the note remains intentionally dirty.

### Best Next Direction

1. Stay in `src/processing/raw_processing.c` only if the next probe is structurally different from the rejected branch-split and coefficient-hoist family.
2. If you want the next highest-signal hotspot, go back to the retained Dual ISO mix stack, especially `mix_chroma`.
3. Before making a new source edit, verify the visible clips still do not enter `processing_basic_matrix_fast_path`.

### Reference

- Detailed running investigation log: [`.claude/analysis/mlv-playback-investigation.md`](C:/!Layi%20Wkspc/MLV-App/.claude/analysis/mlv-playback-investigation.md)
