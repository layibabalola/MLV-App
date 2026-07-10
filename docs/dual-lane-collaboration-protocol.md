# Dual-Lane Collaboration Protocol (Codex ⇄ Claude)

Two autonomous agents collaborate on a **single goal** with **no human relay**. Communication
is **file-based only** (NOT the agent-bridge / MCP). This file is the **invariant contract** —
it is task-agnostic and does not change per collaboration.

Everything specific to *the current goal* — the objective, who is in which lane, the build and
gate commands, the acceptance criteria, what to inspect, known gate blind spots, the definition
of done — lives in the **per-collaboration config**:

> `.claude-state/coordination/dual-lane/collab-config.md`

Both lanes read THIS file (the how) and the config (the what) at the start of every session and
treat them together as the source of truth. A new collaboration = a new config; this protocol
does not change.

---

## Lanes & roles (roles are assigned per collaboration, not hardcoded)

There are exactly two lanes, keyed by **agent identity** (which never changes):
- **Codex lane** → writes `codex.md`.
- **Claude lane** → writes `claude.md`.

Each lane is assigned exactly one **role** for the collaboration, in the config:
- **IMPLEMENTER** — owns the change. Implements, builds a provenance-stamped artifact, runs the
  gate, **opens and LOOKS at the output**, self-reviews, **commits the candidate**, then HANDOFFs.
  On CHANGES_REQUESTED, addresses the findings and re-handoffs.
- **REVIEWER** — independent second check. **Never trusts the implementer's verdict**:
  independently rebuilds, runs the gate, **inspects the output (the eyeball)**, checks provenance
  + anti-bypass + acceptance criteria, returns **APPROVE / CHANGES_REQUESTED** with evidence.

The pairing is symmetric: either agent can be implementer or reviewer — the config's
`role assignment` decides. (For the current goal see the config; historically Codex=implementer,
Claude=reviewer.)

---

## Channel (conflict-free, no locks)
Folder: `.claude-state/coordination/dual-lane/`
- `codex.md`  — **only Codex appends.**
- `claude.md` — **only Claude appends.**
- `collab-config.md` — the per-collaboration config (read-only to both; changed only by an
  explicit setup/retarget step, never mid-round without an entry announcing it).
- `archive/`  — archived old entries.
- `.codex-marker` / `.claude-marker` — heartbeat cursors (managed by the helper).

Each agent writes **only its own lane file** → zero write conflicts, no locks, no turn token.
Each agent **reads the other lane's file** every heartbeat. Never edit the other lane's file.

## Cursors (in your OWN file's header line)
- `claude.md`: `LAST_READ_CODEX_SEQ: <n>` — highest Codex SEQ Claude has processed.
- `codex.md`:  `LAST_READ_CLAUDE_SEQ: <n>`.
Update your cursor (in your own file) when you process the other lane's entries. The advancing
cursor is itself proof-of-liveness to the other lane.

## Entry format (append to YOUR file; never rewrite the other lane's lines)
```
## SEQ <n> | <TYPE> | <UTC ISO8601>
ack: <other-lane SEQ this responds to, or ->
range: <40hex startHead..head, or ->
re: <one-line subject>
body:
  <details, evidence paths, what you SAW in the output, gate verdict, the acceptance checks>
status: OPEN | ACKED | RESOLVED
---
```
SEQ is per-lane, monotonic. TYPE ∈ {HANDOFF, REVIEW, ACK, STATUS, BLOCKER, QUESTION, HEARTBEAT, COLLABORATION_END}.
Append at the physical end of the file (EOF) so the newest block is unambiguous.
**COLLABORATION_END** is a control entry that authorizes tearing down a lane's idle watcher (see
"Collaboration teardown" under the idle-heartbeat section) — it is the ONLY entry that does.

Status discipline:
- `OPEN` means the block still requires action or acknowledgement and may pin the live ledger.
- Routine informational `STATUS`, `ACK`, and `HEARTBEAT` entries should be written as
  `RESOLVED` unless they explicitly request action from the other lane.
- When an `OPEN` item is superseded, acknowledged, merged, blocked by a newer item, or moved into
  a sidecar handoff, mark YOUR old block `RESOLVED` in the same turn. Do not leave stale
  informational entries `OPEN`.

A **REVIEW** entry additionally carries two dedicated lines so any downstream finalize/closeout
gate can parse it unambiguously:
- `Range:` — value EXACTLY equals the canonical full-40-char `startHead..head` range token.
- `Verdict:` — a bare token EXACTLY equal to `APPROVE`, or `CHANGES_REQUESTED` / `BLOCKER`.

## Read/write order (per heartbeat) — no ambiguity
1. **READ** the other lane file.
2. Process entries with `SEQ > your cursor`, oldest → newest.
3. **Budget gate before work:** run the archive helper on YOUR lane file when it is over the
   live budget, before implementation/review/build/profiling work. If your lane remains over
   budget after safe archiving because stale actionable blocks are still `OPEN`, write one
   compact `BLOCKER` / `STATUS` explaining the pinned blocks and do not add verbose evidence
   to the live ledger; put details in a sidecar under `.claude-state/`.
4. Do the work (implement / review). **No inbound is NOT no work:** if you have an open assigned
   task or a clear forward direction, that IS your work — proceed (see *Anti-passivity* below);
   never sit in liveness/ledger mode while assigned work is open.
5. **WRITE** your response entries to **your** file; bump **your** cursor. Mark your own prior
   entries RESOLVED by editing their `status:` line (your file only). Do not rewrite history
   beyond your own status lines.
6. Archive again if the write pushed your lane over budget.

## Anti-passivity — liveness/ledger mode is NOT a holding pattern (2026-06-29)
Keep the work MOVING; do not emit liveness heartbeats while assigned work is open. The failure this
prevents: after a detour (the NoLA historical matrix) resolved and the reviewer named the next work
("forward on Block 2 speckle") but did NOT post a fresh ceremonial HANDOFF, the implementer held in
liveness/ledger mode for many heartbeats instead of resuming the task it was already assigned —
"no new precise HANDOFF" was misread as "no work."

- **An assigned task RESUMES when its hold/detour/dependency clears — no fresh ceremonial HANDOFF is
  required to continue work already assigned.** If a HANDOFF assigned you a task and a detour
  interrupted it, finishing the detour means RESUME it; do not wait to be re-handed the same task.
- **Direction is authorization.** The reviewer naming the next valuable work (in a REVIEW/STATUS) or
  the human giving an explicit-enough instruction IS the go-ahead — proceed; do not wait for ceremony.
- **"No new message" ≠ "no work"** — applies to any open assigned task or clear forward direction, not
  just an open CHANGES_REQUESTED.
- **Liveness heartbeats are NOT progress and NOT a holding pattern.** If you emit consecutive
  liveness/ledger entries while you have open assigned work or a clear direction, you are stuck
  passive: RESUME the work, or post a concrete BLOCKER. A legitimate HOLD must name the SPECIFIC thing
  it waits on AND the exact release condition (e.g. "holding the GUI gate until peer posts merge-done");
  an open-ended "waiting for a handoff" hold is forbidden.
- **Reviewer mirror duty: when YOU resolve a detour/question whose next work is the peer's, POST the
  explicit forward HANDOFF** (TYPE HANDOFF, `YOUR ACTION:` + precise scope) — do NOT leave the
  direction implied in REVIEW/STATUS prose and then idle in gate-prep/liveness while the peer waits.
  Naming "forward on X" in a REVIEW is NOT a handoff; the peer will hold for the ceremony.

## ACK discipline
Every HANDOFF and every REVIEW must be ACKed by the other lane within one heartbeat of being
read (an ACK entry, or a REVIEW/HANDOFF carrying `ack: <seq>`). Unacked OPEN HANDOFF/REVIEW
entries are the work queue. Routine non-actionable STATUS/ACK entries need only a cursor bump.

## Archiving — keep ledgers SMALL (enforced, not aspirational)
The lane files are re-read every heartbeat and pulled into context on demand, so an unbounded ledger
is a real cost. Keep YOUR lane file small.

- **Budget:** your live lane file holds at most the **~15 most-recent SEQ blocks** PLUS any still-OPEN
  entries PLUS the header. That is the target size, not a 40-entry ceiling.
- **Cadence:** check the budget **at the start of every session** and **on every turn that touches the
  collaboration** (do it alongside the heartbeat-liveness re-verify). This is a **pre-work hard gate**:
  if you are over budget, prune immediately before implementation/review/build/profiling work — do not
  let it drift to hundreds of entries (it has before).
- **Operator (the teeth):** run the shared helper against YOUR OWN file; it is conservative and
  dry-run by default:
  ```
  pwsh -NoProfile -ExecutionPolicy Bypass -File <skill>/archive-ledger.ps1 \
    -LaneFile <your lane .md> -OtherLaneFile <other lane .md> [-KeepRecent 15] [-Apply]
  ```
  It moves a block to `archive/<lane>-<UTCdate>.md` only when it is safe: its SEQ ≤ the other
  lane's cursor (they have read it), it is older than the keep-recent window, and it is either
  `RESOLVED` or a non-actionable informational type the helper recognizes (`STATUS`, `ACK`,
  `HEARTBEAT`, `OBSERVATION`). It NEVER archives actionable `OPEN` blocks (`HANDOFF`, `REVIEW`,
  `QUESTION`, `BLOCKER`), the recent window, or anything the other lane has not yet read; it backs
  up the original and leaves a pointer line. Run dry-run first, then `-Apply`.
- **Safety:** only archive entries the OTHER lane has already ACKed (its cursor proves it). Never touch
  the other lane's file or archive. Archived history stays in `archive/` (recoverable), so pruning is
  loss-free.
- This is per-lane: each lane prunes only its own file. A bloated other-lane file is a nudge to send
  (the other lane must prune its own); you cannot prune it for them.
- **Sidecar rule:** large evidence tables, screenshot lists, build matrices, long root-cause notes,
  and session handoffs belong in `.claude-state/coordination/dual-lane/*.md` or
  `.claude-state/profiling/**`, with one pointer line in the live ledger. Do not paste long evidence
  dumps into `codex.md` / `claude.md`.

---

## Idle heartbeat — BOTH lanes, every ~3 min (no human nudge)
The whole point is **no human relay**. Each lane runs its OWN automation heartbeat so neither
agent waits on the user to paste the other's reply:

> After every completed turn, while you are idle, check the OTHER lane's file every ~180s.
> Wake (resume your loop) only when the other lane's max SEQ exceeds your marker.

Use the shared helper (it prints the new SEQ blocks ONLY when there is real news, so silence ==
nothing-new and you stay cheap):
```
pwsh -NoProfile -ExecutionPolicy Bypass -File <skill>/heartbeat-check.ps1 \
  -OtherLaneFile <other lane .md> -MarkerFile <your marker> -LoopSeconds 180
```
- **Claude** runs this as a persistent Monitor (event-driven wake). Restart it after any context
  compaction — a Monitor does not survive compaction.
- **Codex** runs the SAME helper against `claude.md` on its own watcher/schedule (marker
  `.codex-marker`). This is the automation that removes the manual-nudge gap; if your surface
  cannot run a true background watcher, re-check `claude.md` at the start of every idle turn.

**Heartbeat liveness — CRITICAL, a dead heartbeat is SILENT.** A Monitor/watcher can die (context
compaction, process kill, host restart) and you get NO error — it simply stops waking you, so
"no notification" silently becomes indistinguishable from "nothing new," and the channel stalls
with each side thinking the other is idle. Both lanes MUST therefore:
- **Re-verify the heartbeat is ALIVE** at the start of every turn that touches the collaboration,
  and immediately after any compaction/restart notice. If it is dead (Claude: the Monitor task is
  gone; Codex: the watcher PID is gone), RESTART it before doing anything else. Do not assume it is
  running just because you started it once.
- **EXACTLY ONE Monitor/watcher.** Before (re)starting one, STOP any existing one. Stacked Monitors
  race the shared marker: one consumes the new SEQ and advances the marker, the rest go silent —
  which silently strands you (this happened: two live Monitors both eating events). Verify the old
  one is actually stopped, then start one. The shared `heartbeat-check.ps1` now ALSO enforces this
  MECHANICALLY: in persistent mode it takes a PID lock keyed to your marker, so a second instance
  exits cleanly and a careless restart cannot stack duplicates (stale locks self-heal via PID check).
- **The direct read is the GUARANTEE; the Monitor is best-effort.** A Monitor wake dies silently on
  compaction and can be duplicated, so do NOT treat it as the source of truth. **Directly re-read the
  other lane file from your cursor at the START of every turn that touches the collaboration**, even
  when no notification arrived. "No notification" is never proof there is nothing new.
- **A notification is a SNAPSHOT; the cursors are the TRUTH.** A WAKE / heartbeat / STALE line (and the
  harness-surfaced WAKE event text, and any "no new entries" prose a lane writes) is point-in-time and
  LAGS the live files — a "no new entries" claim is true only as of its own timestamp; the peer may post a
  second later (2026-06-28: Codex SEQ372 "no new Claude entries" was written 22s before Claude SEQ131, and
  was briefly misread as a possible lane split). So NEVER conclude split / stall / missed-message / idle
  from a snapshot. Diagnose coordination state ONLY from the authoritative one-shot
  `heartbeat-check.ps1 -Status` (prints both ledgers' max-SEQ, BOTH header cursors, the inbound/outbound
  gaps, peer age, and a verdict) or an equivalent direct read of both `## SEQ` headers + both header
  cursors. When you WRITE a liveness / "no new entries" line, self-date it (your read time + the exact
  max-SEQ you saw) so a later reader can tell at a glance whether it is stale.
- **A STALENESS ALERT is ledger-age only — distinguish DARK from HEADS-DOWN before escalating.** The
  helper flags the peer "stale" when its newest entry is older than the threshold (~60 min), but a peer
  mid-build/iterate during an implementation phase legitimately posts nothing for that long. Before any
  escalation, run `-Status`, confirm the peer's watcher PID is alive, AND check for active peer WORK
  (build/capture/analysis processes — cc1plus/g++/mingw32-make/qmake/MLVApp/python — and recent writes
  in the peer's worktree, e.g. `C:\mlvtmp\wt-*`). If the peer is demonstrably WORKING it is a FALSE
  alarm: note it self-dated and keep waiting — do NOT post a RESUME-REQUEST, ask for a restart, or cry
  wolf. Escalate (RESUME-REQUEST + ask the human to restart) ONLY when the peer is genuinely INERT
  (watcher dead, or no active process AND no recent worktree writes). This is the mirror of *Anti-passivity*:
  ledger silence proves neither "dead" nor "idle" by itself.
- **Non-deferral:** when the heartbeat (or a direct read) surfaces a new entry, process it
  PROMPTLY. Do not get absorbed in an unrelated task and let the channel starve — coordination-doc
  processing is not deferrable behind long side-work.

Emit a HEARTBEAT/STATUS entry at least every ~30 min so the other lane sees you are alive (the
advancing cursor also signals liveness).

**Liveness detection — the helper watches the OTHER lane for going dark.** The cursor-based WAKE
self-heals a *dropped* wake, but it is BLIND to the peer simply going DARK: a dead/quit peer posts no
new SEQ, so a pure-WAKE watcher stays silent forever (this happened — Codex quit after a mis-signaled
"done" and the channel could have stalled indefinitely). So `heartbeat-check.ps1` ALSO emits, while
the collaboration is active (no COLLABORATION_END recorded):
- a **STALENESS ALERT** when the other lane's latest entry is older than ~2x the ~30-min liveness
  cadence (default 60 min) — i.e. it missed two liveness heartbeats; the peer likely quit/died.
- a **DEADLOCK WATCH** when the other lane is alive (posting heartbeats) but has not advanced its
  cursor past one of YOUR still-OPEN items for too long — a both-lanes-wait standoff.
Both re-alert on a throttled cadence (default 30 min) until resolved, and both are gated on the
ABSENCE of COLLABORATION_END so they cannot false-positive after a clean shutdown. **Self-heal on a
STALENESS ALERT:** surface it, post a RESUME-REQUEST in your lane, and ask the human to restart the
other agent (you cannot restart its process); do not silently keep waiting.

**Collaboration teardown — the ONLY authorization to stop a watcher.** A watcher/heartbeat may be
torn down ONLY when an explicit **COLLABORATION_END** control entry has been recorded (in either
lane). Finishing a work block is NOT the end of the collaboration: a work-block-done STATUS MUST say
"collaboration continues; keep your watcher alive" so neither lane reads "this milestone merged" as
"we are done" and kills its heartbeat (that exact conflation stranded a lane once). Only a deliberate
COLLABORATION_END — typically by mutual agreement and with the human informed — ends the
collaboration and authorizes standing the watchers down.

The heartbeat interval may be overridden per collaboration in the config.

---

## MANDATORY verification — universal quality principles (this is exactly why work regresses)
Before ANY "CLEAN" / "APPROVE" / "good" / "done" claim, the responsible lane MUST:

1. **Provenance.** Build/test a **provenance-stamped** artifact; record the embedded SHA. The
   IMPLEMENTER **commits the candidate first** so the handoff carries a UNIQUE sha and a real
   `range:` — a dirty stamp is identical across candidates and cannot be pinned, so the reviewer
   cannot prove it built the same code. No quality claim on an unstamped/stale/ambiguous binary.
   (If a dirty handoff is unavoidable, include a content hash of `git diff HEAD` + the exact patch.)
2. **Independent rebuild (reviewer).** Build the candidate yourself from the committed sha; verify
   the embedded stamp == that sha. Review a binary YOU built, not the other lane's.
3. **Run the gate** named in the config; record the verdict.
4. **INSPECT THE OUTPUT — the artifact is the verdict, the scalar is only a screen.** Open and
   LOOK at the actual output the config tells you to inspect (e.g. image grabs), every time.
   Write what you SAW, not just the scalar. A CLEAN verdict you did not look at is not a review.
   **Know the gate's blind spots** (listed in the config) and inspect specifically for them — a
   scalar can pass while a defect it cannot measure is plainly visible.
5. **Absolute, not just ratio.** Cite the **absolute** acceptance metric vs its target (per
   config), not only a ratio. A change that breaks BOTH compared legs keeps the ratio ~1.0; the
   absolute + the eyeball catch that "false-clean" (equal-but-both-damaged) case.
6. **Anti-bypass.** Confirm the result was produced by the real code path, not a shortcut/bypass
   (the config names the anti-bypass invariants to verify).
7. **Gate reproducibility.** If the gate has run-to-run variance, report an N-run spread on the
   SAME binary and use the median; never certify on a single noisy point.
8. **The artifact must actually RUN.** Before trusting "no output" / "empty logs" / "failed" as a
   gate result, confirm the binary launched and executed the path under test. A missing-runtime or
   load failure — e.g. Windows exit `-1073741515` (0xC0000135, STATUS_DLL_NOT_FOUND) from a bare
   provenance artifact that has no deployed Qt/runtime DLLs next to it — produces empty logs that
   masquerade as a gate failure. Run a DEPENDENCY-COMPLETE deployed binary, and treat an unexpected
   process exit code as "did not run," not "ran and failed clean."
9. **Lock non-deterministic per-leg auto-decisions before comparing.** If the gate's metric depends
   on a per-leg AUTO decision (auto-white-balance, auto-exposure, any auto-anything) that is
   non-deterministic or differs by leg, the A/B measures that decision's NOISE, not the thing under
   test — a "divergence" can be two samples of one unstable distribution, not a real defect. Pin it
   to one fixed value across both legs (a locked setting or a fixed receipt) so the comparison
   isolates the variable under test. Treat this as a known blind-spot class.

Operational guards:
- **When the DEFECT itself is non-determinism, the acceptance gate is a REPEATABILITY test** — run
  the path N times at fixed inputs and assert the output is stable within an explicit tolerance, not
  a single-run pass. A lone CLEAN run cannot certify a determinism fix; clamps/fallbacks can mask the
  instability on any given run, so check the raw pre-clamp signal, not only the final output.
- **No simultaneous interactive/GUI gates.** If the gate captures the screen / drives a GUI, the
  two lanes must not run it at the same time. The reviewer signals "review in progress, hold
  gates"; the implementer holds until the reviewer is done.
- A scalar NEVER certifies quality. If the gate says CLEAN but you did not look, you are not done.

---

## Risky-decision procedure (two-key adversarial planning)
Some changes are **RISKY**: broad-impact, hard-to-reverse, touch shared/core/threading code, have an
**ambiguous root cause**, or are security-sensitive. For these, do NOT let one lane implement on its
own judgment. BEFORE implementing:
1. **Independent adversarial plans.** EACH lane independently (a) verifies the code-trace /
   root-cause cleanliness and (b) produces a safe implementation plan — EACH using its **own
   adversarial subagents** to red-team the trace and the plan. Neither lane builds on the other's
   plan first; the value is two genuinely independent passes.
2. **Exchange + compare.** Each lane posts its plan to its lane file. Both compare: where the two
   plans AGREE is high-confidence; where they DIVERGE must be resolved (re-investigate the disputed
   point, don't paper over it).
3. **Auto-execute on agreement; convene with the human only to break ties (Layi, 2026-06-27).** When the
   two adversarial plans AGREE, that agreement IS the authorization — **AUTO-EXECUTE (implement + test);
   do NOT wait for human approval.** Keep the human INFORMED (surface the compared plans, recommended
   path, residual risks) but they do not gate. Convene with the human for a go/no-go ONLY when the plans
   DIVERGE and the lanes cannot reconcile — then the human breaks the tie. (Supersedes the prior "human
   makes the final go/no-go" gate.)
4. Implement only then, with the agreed safeguards + regression tests, and review the result through
   the normal handoff/review loop.

This "two-key" gate trades a little latency for catching exactly the broad-impact, confident-but-wrong
plan a single lane would otherwise ship. Either lane may invoke it; if one lane flags a decision as
risky, the procedure applies.

## Definition of done
IMPLEMENTER HANDOFF (committed candidate: real `range:` + stamped exe + gate verdict + "I looked:
<what I saw>" + the config's acceptance checks) → REVIEWER REVIEW (independent rebuild + gate +
inspect + checks → `Verdict: APPROVE`). Only then is a candidate shippable.

**Finalize/merge is auto-authorized by two-key agreement (Layi, 2026-06-27).** When both lanes agree —
a converged plan, or a REVIEWER `APPROVE` of a hardened candidate — the merging lane auto-merges using
the delegated merge authority; **no human OK required.** The human breaks ties on divergence and is kept
informed. "Hardened" still means: gates pass AND the live/visible result is eyeball-verified (validate by
pixels, not just metrics) AND no regression vs master AND a clean repo. Always test after auto-executing.

---

## Retargeting to a new goal
To start a different collaboration, run the dual-lane skill's **setup** mode. It runs the
questionnaire, writes a new `collab-config.md`, scaffolds the channel, and generates the
**Codex onboarding prompt** the user pastes into a fresh Codex session. This protocol file is not
edited per goal — only the config changes.
