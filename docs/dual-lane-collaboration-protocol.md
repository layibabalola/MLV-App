# Dual-Lane Collaboration Protocol (Codex ⇄ Claude)

Two autonomous agents collaborate on MLV-App playback quality with **no human relay**.
Communication is **file-based only** (NOT the agent-bridge / MCP). Both agents read this
file as the single source of truth for the contract.

## Lanes & roles
- **CODEX — implementer + self-review.** Owns core recon (`src/mlv/video_mlv.c`,
  `src/mlv/llrawproc/dualiso.c`, `src/mlv/llrawproc/llrawproc.*`). Implements, builds a
  provenance-stamped exe, runs the gate, **opens and LOOKS at the screenshots**,
  self-reviews, then hands off.
- **CLAUDE — independent second reviewer.** Owns GUI/tooling + review. **Never trusts
  Codex's verdict**: independently rebuilds, runs the gate, **eyeballs the grabs**, checks
  provenance + anti-bypass, returns APPROVE / CHANGES_REQUESTED with evidence.

## Channel (conflict-free, no locks)
Folder: `.claude-state/coordination/dual-lane/`
- `codex.md`  — **only Codex appends.**
- `claude.md` — **only Claude appends.**
- `archive/`  — archived old entries.
- `.codex-marker` / `.claude-marker` — heartbeat cursors (managed by the helper).

Each agent writes **only its own file** → zero write conflicts, no locks, no turn token.
Each agent **reads the other's file** every heartbeat. Never edit the other lane's file.

## Cursors (in your OWN file's header line)
- `claude.md`: `LAST_READ_CODEX_SEQ: <n>` — highest Codex SEQ Claude has processed.
- `codex.md`:  `LAST_READ_CLAUDE_SEQ: <n>`.
Update your cursor (in your own file) when you process the other lane's entries.

## Entry format (append to YOUR file; never rewrite the other lane's lines)
```
## SEQ <n> | <TYPE> | <UTC ISO8601>
ack: <other-lane SEQ this responds to, or ->
range: <40hex startHead..head, or ->
re: <one-line subject>
body:
  <details, evidence paths, what you SAW in the grabs, gate verdict, the 5 checks>
status: OPEN | ACKED | RESOLVED
---
```
SEQ is per-lane, monotonic. TYPE ∈ {HANDOFF, REVIEW, ACK, STATUS, BLOCKER, QUESTION, HEARTBEAT}.

## Read/write order (per heartbeat) — no ambiguity
1. **READ** the other lane file.
2. Process entries with `SEQ > your cursor`, oldest → newest.
3. Do the work (implement / review).
4. **WRITE** your response entries to **your** file; bump **your** cursor. Mark your own
   prior entries RESOLVED by editing their `status:` line (your file only). Do not rewrite
   history beyond your own status lines.
5. Archive (below).

## ACK discipline
Every HANDOFF and every REVIEW must be ACKed by the other lane within one heartbeat of
being read (an ACK entry, or a REVIEW/HANDOFF carrying `ack: <seq>`). Unacked OPEN entries
are the work queue.

## Archiving
When your file exceeds ~40 entries, OR an exchange is RESOLVED on both sides and older than
the last 10 SEQs, move those blocks from your file into `archive/<lane>-<UTCdate>.md`. Only
archive entries the OTHER lane has already ACKed (its cursor proves it). Never touch the
other lane's file or archive.

## Heartbeat — every 180s, both lanes
Run the helper against the OTHER lane file + your marker; it prints new SEQ blocks ONLY when
the other lane's max SEQ exceeds your marker (so you wake on real news, not every tick):
```
pwsh -NoProfile -File .claude/skills/dual-lane-review/heartbeat-check.ps1 \
  -OtherLaneFile <other lane .md> -MarkerFile <your marker> -LoopSeconds 180
```
Claude runs this as a persistent Monitor. Codex runs it on its own 180s schedule/watcher.
Emit a HEARTBEAT/STATUS entry at least every ~30 min so the other lane sees you are alive.

## MANDATORY verification — this is exactly why playback regressed
Before ANY "CLEAN" / "APPROVE" / "good" claim, BOTH lanes MUST:
1. Build/test a **provenance-stamped** exe; record the embedded SHA (==HEAD, or a dirty
   stamp for an uncommitted candidate). No quality claim on an unstamped/stale binary.
2. Run `tools/profiling/review-dualiso-fullres-recon.ps1` (the gate).
3. **OPEN AND LOOK AT the PNG grabs** (scale=1 AND scale=2). Write what you SAW, not just
   the verdict. The screenshot is the verdict; the scalar is only a screen.
4. Cite **absolute HLine vs the ~1.5 clean baseline** — not only the ratio. (A fix that
   breaks BOTH legs keeps the ratio ~1.0; the eyeball + absolute caught the 2026-06-24
   false-CLEAN where both legs read HLine ~26.)
5. **Anti-bypass:** confirm scale=1 still ran `dual_iso_fullres=1` (a real recon fix, not a
   full-res bypass).

A scalar NEVER certifies quality. If the gate says CLEAN but you did not look, you are not done.

## Definition of done
CODEX HANDOFF (range + stamped exe + gate verdict + "I looked: <what I saw>" + the 5 checks)
→ CLAUDE REVIEW (independent rebuild + gate + eyeball + 5 checks) → **APPROVE**.
Only then is a candidate shippable. Finalize/merge still requires the **human's** explicit OK.

## Current focus (2026-06-24)
Fix the dual-ISO full-res recon regression. Codex's first attempt (enum→kernel-size mapping
in `hdr_chroma_smooth`) was the right diagnosis but REGRESSED both scales to full-frame
green/magenta banding (HLine ~26 vs ~1.5 clean): the enum mismatch had been *masking* a
broken `chroma_smooth_2x2/3x3/5x5` invocation for full20-bit dual-ISO data. The real fix is
the chroma-smooth implementation/parameters (or correct application), not the routing.
