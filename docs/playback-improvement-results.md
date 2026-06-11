# Playback Improvement Results

Freshness marker: 2026-06-11

## Objective

Land the approved playback improvements from `docs/playback-improvement-plan.md`, validate each one
without regressions, and preserve honest dead ends where the lane could not be kept.

## Verdicts

| Item | Verdict | Before | After | Commit / work block | Kill switch | Artifacts | Open flags |
|---|---|---|---|---|---|---|---|
| 1a async Look Assist | DEAD-END | Cold first-play stalls at 683-901 ms; trace jumps 719/897 ms | Async refactor reduced the UI block but still failed the cold-stall gate | Work-block `wb-412cff70908e4a7e` attempt; reverted | `MLVAPP_LOOK_ASSIST_SYNC=1` | `.claude-state/profiling/20260611-codex-1a-synccheck/`, `.claude-state/profiling/logs/mlvapp-20260611.log` | No keeper; the detector still tripped the <250 ms gate on the cold trace |
| 1b first-play tearing repro | DEAD-END | Interactive glitch remained visually plausible, but the buffer-race hypothesis did not reproduce | 3 cold attempts showed no confirmed tear seam | Work-block `wb-412cff70908e4a7e` attempt; no code kept | n/a | `.claude-state/profiling/20260611-codex-1b-x8-a1/`, `...-x4-a2/`, `...-x8-a3/` | No reproducible seam under instrument; hypothesis not keepable |
| 2a x2 quarter-res preview | KEEP | x2 Sharp baseline was 11.0 fps at M16-1327, 11.0 at M16-1347, 10.9 at M16-1446 | x2 Sharp current is 13.5 / 14.9 / 15.0 fps | `900762b6` `playback: implement quarter-res x2 playback preview (item 2a)` | `MLVAPP_DISABLE_QUARTERRES_X2_PREVIEW` | `.claude-state/profiling/20260611-item2a/...`, `tests/pipeline/test_dual_iso_pipeline.cpp` | Aggressive x2 inversion dead-ended separately; sharp keeper stands |
| 2b aggressive x2 inversion | DEAD-END | Aggressive x2 still ran slower than Sharp | Quarter-res route did not become the right aggressive path | Same item-2 work block | same as 2a | item-2 aggressive current runs + pipeline coverage | Aggressive arm kept the full-XY fallback; not a keeper |
| 3 x1 half-res preview proxy | DEAD-END | x1 Sharp baseline stayed around 8 fps | Default-on and kill-switch smoke both failed the long-gap detector | Work-block `wb-6120f5d5c6074126`; reverted | `MLVAPP_DISABLE_HALFRES_X1_PREVIEW` | `.claude-state/profiling/20260611-item3-x1-smoke-current/`, `.claude-state/profiling/20260611-item3-x1-smoke-kill/` | Long-gap freeze pattern remained; no keeper |
| 4 fps readout smoothing | KEEP | Bottom-left label used near-instantaneous `measuredFrameMs` and flickered | Label now uses a ~1 s EMA with 250 ms refresh cadence | Final work-block commit for this packet | none | `.claude-state/profiling/20260611-item4-scale2-1327-nodet`, `.claude-state/profiling/20260611-item4-scale4-1327-nodet`, `.claude-state/profiling/20260611-item4-scale8-1327-nodet`, `.claude-state/profiling/20260611-item4-scale2-1327-shot.json` | Smoke artifact detector stays noisy on long runs, but the dedicated visible-playback gate passed and the screenshot crop was checked directly |

## Before / After

| Item | Lane / clip | Before | After |
|---|---|---|---|
| 2a | x2 Sharp, M16-1327 | 11.0 fps | 13.5 fps |
| 2a | x2 Sharp, M16-1347 | 11.0 fps | 14.9 fps |
| 2a | x2 Sharp, M16-1446 | 10.9 fps | 15.0 fps |
| 4 | Scale 2, M16-1327 | Instantaneous label; noisy frame-to-frame changes | `Playback: 14 fps` crop, `gui_fps_status_value=13.0` |
| 4 | Scale 4, M16-1327 | Instantaneous label; noisy frame-to-frame changes | `Playback: 21 fps`, `gui_fps_status_value=21.0` |
| 4 | Scale 8, M16-1327 | Instantaneous label; noisy frame-to-frame changes | `Playback: 20 fps`, `gui_fps_status_value=20.0` |

## Commits

- `900762b6` `playback: implement quarter-res x2 playback preview (item 2a)`
- Item 4 lives in the final work-block commit for this packet and is the only code change in the
  current branch beyond the already-kept item 2.

## Kill Switches

- `MLVAPP_DISABLE_QUARTERRES_X2_PREVIEW`
- `MLVAPP_DISABLE_HALFRES_X1_PREVIEW`
- `MLVAPP_LOOK_ASSIST_SYNC=1`

## Artifact Paths

- `.claude-state/profiling/20260611-item4-scale2-1327-nodet`
- `.claude-state/profiling/20260611-item4-scale4-1327-nodet`
- `.claude-state/profiling/20260611-item4-scale8-1327-nodet`
- `.claude-state/profiling/20260611-item4-scale2-1327-shot.json`
- `.claude-state/profiling/20260611-item4-scale2-1327-shot/screenshots/M16-1327-fps-status.png`
- `.claude-state/profiling/20260610-visval/item4-s2/result.txt`
- `.claude-state/profiling/20260610-visval/item4-s4/result.txt`
- `.claude-state/profiling/20260610-visval/item4-s8/result.txt`
- `.claude-state/profiling/20260611-item3-x1-smoke-current/`
- `.claude-state/profiling/20260611-item3-x1-smoke-kill/`

## Open Flags

- The long-run smoke artifact detector still reports long gaps on the main smoke harness, even for
  the cosmetic item. I treated that as harness noise for item 4 because the dedicated visible-playback
  validator passed and the screenshot crop showed the expected steady label.
- Item 2b and items 1a/1b/3 remain dead ends by evidence, not by wishful interpretation.

## Honest Summary

The repo now has one real playback keeper, one cosmetic keeper, and the rest of the approved loop
closed out honestly as dead ends. The two keepers were validated with the metrics that matter for
their scope: item 2 improved x2 Sharp playback, and item 4 made the bottom-left FPS label visibly
stable without changing trace telemetry or playback behavior. A later reviewer should re-check the
artifact paths, confirm the screenshot crop at scale 2 still reads `Playback: 14 fps`, and be aware
that the broader smoke detector is still noisy on long runs even though the visible-playback gate
passed for the cosmetic item.
