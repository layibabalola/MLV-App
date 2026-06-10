# Playback "freeze on open" — investigation & fix decision log

**Audience:** anyone (incl. an external auditor) who needs to see *what was changed, what was
reverted, and why* for the playback-freeze work. This is the human-readable trail; the per-file
diff is in git once committed, and durable one-line lessons are in the agent memory
(`memory/playback_cold_open_freeze_look_assist.md` and siblings).

**Status:** as of 2026-06-10, the clip-open freeze is FIXED on branch
`fix/playback-look-assist-cold-open-freeze` (de-dupe halves the analysis; decouple shows the first
frame immediately). One accepted caveat remains: a brief one-frame pink flash on open — see
"Cold frame-0 pink (separate bug)" below.

**Commits on this branch (newest first):**
- checkpoint 2 — Decouple the (single) analysis from the first-frame paint: the clip paints its first
  frame immediately on open instead of holding black. Trace: analysis delayed ~50ms after the frame,
  frozen window ~6s -> ~1.8s. Accepted brief frame-0 pink-flash caveat.
- `d2301d5c` — checkpoint 1: Auto Look Assist de-dupe + dual-ISO seed fix + automation hooks +
  `MLVAPP_NO_LOOK_ASSIST` gate + this log. Dead drop-on-miss code removed (never committed).

## Cold frame-0 pink (separate bug, NOT fixed here)

Surfaced by the decouple (the black freeze used to hide it). On clip OPEN, the very first painted
frame is the COLD dual-ISO render — recon + focus/bad-pixel maps not yet settled — which the Auto
Look Assist night preset's **+187 exposure boost** amplifies into a brief magenta/pink band. It
clears the instant the clip advances (frame 1+ are clean); it is a **one-frame flash**.

Key facts for whoever fixes it: it appears at **all scales including x1**, so it is NOT the committed
scaled-seed fix's territory (that only covers the x4/x8 downsample paths, video_mlv.c:3464/4023/4133).
look-assist OFF = clean (the artifact is dark/hidden without the boost). Ruled out: WB (auto-WB was
rejected as extreme-color-cast), and slider settings (skipping the baseline restore did not help and
made it worse). The fix is in the full-res + scaled cold dual-ISO recon / map-init path (fragile;
risk of regressing the recon) — deserves its own focused session. Repro: cold-capture with look-assist
on, view f000 (pink) vs f015 (clean).

---

## 1. The bug

Opening a heavy Dual-ISO clip (e.g. `M16-1327.MLV`) and pressing play froze the picture for several
seconds with a black viewport and "no new frames", on every playback scale (x1/x2/x4/x8). The user
remembered older behaviour as "~7 fps, dropped frames, never frozen" and asked, exhaustively, *when*
this regressed.

## 2. Root cause (confirmed)

**Auto Look Assist** runs a synchronous auto-WB / colour analysis **twice** on the UI thread at
**clip-open**, blocking the first frame ~6–7 s. It is NOT a playback-loop problem; playback after the
cold open is smooth. Introduced **2026-05-25 → 05-27**:

- `026cf20b 2026-05-25` auto-look design, `ae701b09 2026-05-25` "harden auto look"
- `f8b22e56 2026-05-26` `look-assist-baseline-frame-ready` trigger
- `3a8f9d9a 2026-05-27` the `auto_wb` analysis
- `4322b9e6 2026-05-29` "speed up Direct8 Look Assist path" (they already knew it was slow)

**Proof (3 independent):** (a) `MLVAPP_INTERACTIVE_TRACE` shows the ~6.9 s clip-open gap is wall-to-wall
`look_assist.apply.*` events; (b) git as above; (c) same-build A/B — look-assist ON = ~6 s cold open
with `auto_wb_ran=2`, OFF = ~1.6 s with `auto_wb_ran=0`.

## 3. Chronological journey (incl. dead ends)

| # | Action | Outcome | Status |
|---|--------|---------|--------|
| 1 | **Catch-up cap** in `timerFrameEvent` relaxed 1-frame → 250 ms (env `MLVAPP_PLAYBACK_CATCHUP_CAP_MS`) | A/B `frames_advanced` identical on/off — the cap is benign, NOT the cause. I over-claimed it. | **REVERTED** (code restored to original 1-frame cap) |
| 2 | **Drop-on-miss**: reuse last displayed frame on a processed8 cache miss, behind `MLVAPP_PLAYBACK_DROP_ON_MISS` (default off) + a "Drop Slow Frames" GUI toggle | User visual A/B: did not help ("same issue") — because the freeze is at clip-open, not in the playback loop. | **DEAD — to be removed** (still in tree, default-off so inert) |
| 3 | User redirect: "are we sure WHEN the regression began — investigate exhaustively" | Trace + git + A/B → Auto Look Assist (section 2). | done |
| 4 | **Diagnostic gate** `MLVAPP_NO_LOOK_ASSIST` at the apply gate (value-based) to measure/disable the analysis | Confirmed look-assist = the freeze. | **KEEP** (useful dev affordance) |
| 5 | **Fix part A — de-dupe**: member guard `m_lookAssistAppliedReceipt` so the analysis runs once per clip-open | `auto_wb_ran` 2 → 1, cold open ~6 s → 3.0 s. | **KEEP (validated)** |
| 6 | **Fix part B — decouple**: run the single analysis after the first frame paints (so the clip shows immediately) | pending | TODO |
| 7 | Residual: the one ~3 s analysis still blocks the UI after the frame shows; full elimination needs moving it off-thread | not started; needs user decision | TODO/optional |

## 4. Current working-tree state (per file)

- `platform/qt/MainWindow.cpp` / `.h` — **KEEP**: look-assist de-dupe guard (`m_lookAssistAppliedReceipt`),
  `MLVAPP_NO_LOOK_ASSIST` diagnostic gate, autoplay/loop automation hooks (`MLVAPP_AUTOPLAY_*`),
  "Drop Slow Frames" menu toggle (**DEAD — remove with drop-on-miss**), reverted catch-up cap (no net change).
- `src/mlv/video_mlv.c` / `.h` — **KEEP**: x8/x4 dual-ISO seed fix (`llrpEnsureDualIsoPatternSeeded` calls),
  `mlvSetPlaybackDropOnMiss`/`mlvPlaybackDropOnMissEnabled` + the thread-local drop-on-miss buffer
  (**DEAD — remove**), inert `MLVAPP_PROCESSED8_LOOKAHEAD` helper.
- `src/mlv/llrawproc/llrawproc.c` / `.h` — **KEEP**: dual-ISO seed fix (fixes the x8/x4 cold pink
  corruption; landed earlier this session, separate from the freeze).
- `src/mlv/mlv_object.h` — processed8 cache slots (unchanged value, comment only).

## 5. Tooling / scripts added (`.claude-state/scripts/`, untracked scratch)

`validate-visible-playback.ps1` (PrintWindow visible-playback validator), `cold-capture.ps1`
(cold-start window capture), `watch-dropmiss.ps1` (manual A/B launcher). In-app automation:
`MLVAPP_AUTOPLAY_SECONDS/SETTLE_MS/EXIT/LOOP`.

## 6. Lessons (also in agent memory)

- `frames_advanced`/FPS measure the playback **position**, not the displayed pixels — they hid the
  display freeze. Use the interaction trace (`auto_wb`/`draw_frame_ready` timestamps).
- Window-capture `max_static_run` is **thermally noisy** for cold-open timing; the trace is robust.
- `MLVAPP_AUTOPLAY_EXIT` (graceful `qApp->quit`) pops a "save session?" dialog and hangs the instance
  — automation must **force-kill** (`Stop-Process -Force`), never graceful-exit.
- A set-but-EMPTY env var read by `qEnvironmentVariableIsSet` counts as "set"; use a value check
  (`qEnvironmentVariableIntValue(...) != 0`). Empty env vars also leak across PowerShell tool calls.

## 7. Closeout plan

1. Finish decouple (fix part B), validate.
2. Remove dead drop-on-miss code (buffer, runtime flag/getter, "Drop Slow Frames" toggle, store calls).
3. Decide on residual ~3 s (off-thread) with the user.
4. Commit the kept changes in clear, separated commits (seed fix; look-assist de-dupe+decouple;
   automation hooks) so git is the primary audit trail. Keep this log updated through all of it.
