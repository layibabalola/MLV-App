---
name: dual-lane-review
description: Run the CLAUDE reviewer lane of the Codex⇄Claude dual-lane playback-quality collaboration. Use when asked to start/resume the dual-lane review, act as second reviewer for Codex's recon fixes, or watch the coordination doc. Sets up the 180s heartbeat, independently re-verifies Codex's candidate (rebuild + gate + EYEBALL the screenshots), and writes APPROVE / CHANGES_REQUESTED to claude.md.
---

# Dual-lane review (Claude = independent second reviewer)

You are the **CLAUDE reviewer lane**. Codex implements; you independently verify. The full
contract is [docs/dual-lane-collaboration-protocol.md](../../../docs/dual-lane-collaboration-protocol.md) —
read it first; it is the source of truth. This skill is your operating loop.

## On invocation
1. Read `docs/dual-lane-collaboration-protocol.md`.
2. Ensure the channel exists: `.claude-state/coordination/dual-lane/{claude.md,codex.md,archive/}`.
   If `claude.md` is missing, create it with header `LAST_READ_CODEX_SEQ: 0` and a SEQ 1 STATUS
   entry announcing the reviewer lane is live.
3. **Start the heartbeat Monitor** (event-driven; wakes you only on a real new Codex SEQ):
   ```
   Monitor(persistent=True, command="pwsh -NoProfile -ExecutionPolicy Bypass -File .claude/skills/dual-lane-review/heartbeat-check.ps1 -OtherLaneFile .claude-state/coordination/dual-lane/codex.md -MarkerFile .claude-state/coordination/dual-lane/.claude-marker -LoopSeconds 180")
   ```
   Confirm the Monitor is active before saying you are "watching." Restart it after any
   compaction. Do NOT use the agent-bridge for Codex.

## When a Codex entry arrives (heartbeat fires)
Process per the protocol's read/write order. For a Codex **HANDOFF**, run the **independent**
review — never trust Codex's verdict:
1. **Provenance:** `Get-MlvAppBuildStamp` on the candidate exe; record embedded SHA / dirty.
   No quality claim on an unstamped/stale binary.
2. **Independent rebuild:** build the candidate tree yourself (`tools/build-release.ps1`,
   `-AllowDirty` if uncommitted) so you review a binary YOU built, not Codex's.
3. **Gate:** `tools/profiling/review-dualiso-fullres-recon.ps1 -RepoRoot . -ExePath <your build>`.
4. **EYEBALL (mandatory):** open the scale=1 AND scale=2 PNG grabs and look. Write what you
   SAW. A `CLEAN` verdict you did not look at is not a review.
5. **Absolute + anti-bypass:** cite absolute HLine vs the ~1.5 clean baseline (not just the
   ratio); confirm scale=1 still ran `dual_iso_fullres=1`.
6. Append a **REVIEW** entry to `claude.md`: `APPROVE` only if rebuild+gate+eyeball+5 checks
   all pass; else `CHANGES_REQUESTED` with the exact evidence (grab paths, absolute numbers,
   what you saw). `ack:` the Codex SEQ; bump `LAST_READ_CODEX_SEQ`.

## Hard rules
- The screenshot is the verdict; the scalar is only a screen. Look every time.
- Write ONLY to `claude.md` (+ `archive/`). Never edit `codex.md`.
- Review-only branch: never finalize/merge without the human's explicit OK.
- Archive your own ACKed, resolved, >10-SEQ-old entries per the protocol.
