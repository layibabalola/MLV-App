---
name: dual-lane
description: Run one lane of a Codex⇄Claude file-based dual-lane collaboration on a single goal. Use to start/resume a dual-lane round as the REVIEWER (independently verify the other lane's candidate) or IMPLEMENTER (build→gate→eyeball→commit→handoff→address CHANGES_REQUESTED), or to SET UP a new collaboration (questionnaire → writes collab-config.md + scaffolds the channel + generates the paste-ready Codex onboarding prompt). Replaces the older /dual-lane-review (reviewer-only) skill.
---

# Dual-lane collaboration (role-parameterized)

Two agents (Codex ⇄ Claude) collaborate on one goal, file-based, **no human relay**. This skill
runs YOUR lane. Two contracts govern every round; read BOTH first, every session:
- **Invariant protocol** (how the channel works): [docs/dual-lane-collaboration-protocol.md](../../../docs/dual-lane-collaboration-protocol.md)
- **Per-collaboration config** (this goal's rules/gates/acceptance): `.claude-state/coordination/dual-lane/collab-config.md`

## Argument → mode
`/dual-lane <mode>` where `<mode>` ∈ `reviewer` | `implementer` | `setup` | `watch`.
- If `<mode>` is omitted: read the config's `role assignment`; YOUR agent (Claude) runs the role
  it is assigned. If no config exists yet, drop into **setup**.
- `watch` = just (re)start the idle heartbeat Monitor and resume the assigned-role loop.

Confirm at the start: which agent you are (Claude → writes `claude.md`, reads `codex.md`) and which
ROLE the config assigns you. The lane FILE is fixed by agent; the ROLE comes from the config.

---

## On invocation (all modes)
1. Read the protocol doc and the config (above). If the config is missing → **setup mode**.
2. Ensure the channel exists: `.claude-state/coordination/dual-lane/{claude.md,codex.md,archive/}`
   and `collab-config.md`. If `claude.md` is missing, create it with header
   `LAST_READ_CODEX_SEQ: 0` and a SEQ 1 STATUS announcing your lane+role is live.
3. **Start the idle heartbeat Monitor** (event-driven; wakes you only on a real new Codex SEQ):
   ```
   Monitor(persistent=True, command="pwsh -NoProfile -ExecutionPolicy Bypass -File .claude/skills/dual-lane/heartbeat-check.ps1 -OtherLaneFile .claude-state/coordination/dual-lane/codex.md -MarkerFile .claude-state/coordination/dual-lane/.claude-marker -SelfLedgerFile .claude-state/coordination/dual-lane/claude.md -CursorKey LAST_READ_CODEX_SEQ -LoopSeconds <config interval, default 180>")
   ```
   Use RELATIVE paths (the repo path has a space; an unquoted ABSOLUTE path splits -> pwsh prints help and
   the watch never runs). The heartbeat is CURSOR-BASED + self-healing: it wakes when codex.md's max SEQ
   exceeds claude.md's `LAST_READ_CODEX_SEQ` and RE-FIRES until you advance that cursor, so a dropped wake
   self-heals. (Pre-2026-06-27 it advanced a script-owned marker on EMIT, decoupled from delivery -> a dead
   Monitor consumed wakes 181-190 and the lane sat idle ~4h until a human nudge. Do NOT regress to a marker
   the script self-advances.) Confirm the Monitor is active before saying you are "watching." Do NOT use the agent-bridge.
   **Liveness + teardown (2026-06-27 hardening):** the helper ALSO emits a STALENESS ALERT when the
   other lane goes dark (its latest entry older than ~2x the ~30-min liveness cadence, default 60 min)
   and a DEADLOCK WATCH when a live peer has not read one of your still-OPEN items -- both gated on the
   ABSENCE of a COLLABORATION_END entry (the collab-active gate). On a STALENESS ALERT, FIRST
   DISTINGUISH DARK FROM HEADS-DOWN before escalating (ledger-age alone CANNOT tell a dead lane from a
   peer mid-build): run `-Status` (authoritative), confirm the peer's watcher PID is alive, AND check
   for active peer WORK — build/capture/analysis processes (cc1plus/g++/mingw32-make/qmake/MLVApp/python)
   and recent writes in the peer's worktree (e.g. `C:\mlvtmp\wt-*`). If the peer is demonstrably WORKING
   (mid-build/iterate during an implementation phase), it is a **FALSE staleness alarm** — note it
   self-dated and KEEP WAITING; do NOT post a RESUME-REQUEST, do NOT ask the human to restart, do NOT cry
   wolf (a long (b) build legitimately posts no ledger entry for >60 min). ONLY if the peer is genuinely
   INERT (watcher dead, OR no active build/capture process AND no recent worktree writes) self-heal:
   surface it, post a RESUME-REQUEST in claude.md, and ask the human to restart the other agent (you
   cannot restart its process). A watcher is torn down ONLY by an explicit COLLABORATION_END control
   entry; a work-block-done STATUS is NOT teardown and MUST say "collaboration continues; keep your
   watcher alive."
   **A dead heartbeat is SILENT** (it stops waking you with no error), so do NOT assume it is
   running just because you started it once:
   - **Re-verify it is ALIVE at the start of every turn that touches the collaboration** (and
     immediately after any compaction notice). If the Monitor task is gone, RESTART it before
     anything else — a Monitor does not survive compaction.
   - **Belt-and-suspenders: directly re-read `codex.md` from your cursor** whenever you resume
     after substantial unrelated work. Never treat "no notification" as proof there is nothing new.
   - **Process surfaced entries PROMPTLY** — do not get absorbed in side-work and starve the channel.

---

## Shared working tree — commit ONLY your lane (mechanical guardrails)

Both lanes work in the SAME local working tree on the SAME branch, so at any moment it may hold
BOTH lanes' uncommitted work. A blanket `git add -A` / `git commit -a` by either lane sweeps the
OTHER lane's WIP into your commit (lane-mixing); a `git reset --hard` / `git checkout -- .` destroys
it (the branch is local-only — no remote backup). **Never use `-A`/`-a` or destructive git here.**

Guardrails make this fail-closed instead of relying on memory (`tools/dual-lane/`,
`.dual-lane/ownership.json` — the machine-readable `path → lane` map):
1. **Declare your lane once per session:** `$env:GIT_DUAL_LANE='claude'` (or `'codex'`).
2. **Commit via the helper, never raw:**
   ```
   pwsh -File tools/dual-lane/lane-commit.ps1 -Lane claude -DryRun          # preview owned vs foreign
   pwsh -File tools/dual-lane/lane-commit.ps1 -Lane claude -Message "..."   # stages owned paths ONLY
   ```
   It stages only paths `owner-of.ps1` maps to your lane (explicit pathspec), refuses unmapped
   (`unknown`) paths, leaves the other lane's WIP untouched, prints the `parent..HEAD` range for your
   handoff, and stamps a `Dual-Lane:` trailer for attribution.
3. **Backstop (both-lane opt-in):** `install-hooks.ps1 -Activate` installs a pre-commit lane-guard
   that blocks a cross-lane staged set even on a raw `git commit`. Loud override:
   `GIT_DUAL_LANE_OVERRIDE=1`. Do not activate until both lanes set `GIT_DUAL_LANE`. See
   `tools/dual-lane/README.md`.

If you spot an `unknown` path, ADD it to `.dual-lane/ownership.json` rather than bypassing the guard.

---

## REVIEWER loop (when the config assigns you REVIEWER)
Process per the protocol's read/write order. For a Codex **HANDOFF**, run the **independent**
review — never trust the implementer's verdict:
1. **Provenance:** the handoff must name a **committed** candidate (real `range:` + stamped exe).
   `Get-MlvAppBuildStamp` (or the config's stamp reader) on the candidate; record embedded SHA.
   If the tree is "dirty," confirm it is content-free (`git diff --numstat` = 0/0; LF→CRLF only)
   so the committed code == what you build. No quality claim on an unpinnable/stale binary.
2. **Independent rebuild:** build the candidate yourself (config's build command); verify the
   embedded stamp == the handoff sha. Review a binary YOU built.
3. **Gate:** run the config's gate command against YOUR exe, your own OutputRoot. Honor the
   "no simultaneous interactive/GUI gates" rule — you already posted "review in progress, hold gates."
4. **INSPECT — mandatory, the artifact is the verdict.** Open and LOOK at exactly what the config's
   "what to inspect" says (e.g. scale=1 AND scale=2 grabs). Check specifically for the config's
   **known gate blind spots** (e.g. uniform color cast). Write what you SAW.
5. **Absolute + anti-bypass:** cite the absolute acceptance metric vs target (not just the ratio);
   confirm the config's anti-bypass invariants. If the gate is noisy, use median-of-N on one binary.
6. Append a **REVIEW** to `claude.md` with explicit `Range:` and `Verdict:` lines:
   `Verdict: APPROVE` only if rebuild+gate+inspect + ALL config acceptance criteria pass; else
   `Verdict: CHANGES_REQUESTED` (or `BLOCKER`) with exact evidence (grab paths, absolute numbers,
   what you saw, which criterion failed). `ack:` the Codex SEQ; bump `LAST_READ_CODEX_SEQ`.
- Between handoffs you are correctly idle on review — but keep the Monitor live and process
  STATUS/ACK/QUESTION entries (a Codex QUESTION/BLOCKER is your work even with no new candidate).
- **When YOU resolve a detour/question and the next work is the peer's, POST the explicit forward
  HANDOFF** (TYPE HANDOFF, `YOUR ACTION:` + precise self-contained scope) — do NOT leave the direction
  implied in a REVIEW/STATUS and then idle in gate-prep/liveness while the peer waits. "Forward on X"
  buried in REVIEW prose is NOT a handoff; the peer holds for the ceremony. (Lesson 2026-06-29: reviewer
  resolved the NoLA detour + built the gate but never posted the resume-HANDOFF → implementer held
  passive for many heartbeats.)

## IMPLEMENTER loop (when the config assigns you IMPLEMENTER)
1. Implement a change toward the objective (config's owned areas).
2. **Build** a provenance-stamped artifact (config's build command).
3. **Gate** it (config's gate command); read the verdict.
4. **INSPECT — open and LOOK** at the outputs the config names, including its known blind spots.
   Self-review honestly: write "I looked: <what I saw>", including residuals — do not over-claim.
5. **Decide:** if it does NOT meet the config's acceptance criteria, it is NOT a handoff — log a
   STATUS (reject, why) and iterate. If it DOES, go to 6.
6. **COMMIT the candidate** (so the stamp is a unique sha with a real `range:` the reviewer can
   pin), then append a **HANDOFF** to `codex.md`: committed `range:`, stamped exe path + SHA, gate
   verdict, "I looked: <what I saw>", and the config's acceptance checks. ACK any open reviewer entry.
7. On a reviewer **CHANGES_REQUESTED**: that is your **work queue** until a superseding APPROVE —
   `ack:` it, address the named findings, and re-handoff. **Do not go idle on an open
   CHANGES_REQUESTED.** "No inbound message" ≠ "no work." If you are genuinely out of approaches,
   post a **BLOCKER/QUESTION** to the reviewer — never silently idle.
8. **Resume ASSIGNED work when its hold clears — no fresh handoff needed.** A task assigned to you
   that a detour or dependency interrupted RESUMES automatically once that detour/dependency resolves;
   you do NOT need to be re-handed the same task. A clear forward direction (the reviewer naming the
   next work, or the human's explicit instruction) IS authorization. **Liveness/ledger heartbeats are
   not a holding pattern:** if you are emitting consecutive heartbeats with open assigned work or a
   clear direction, you are stuck passive — RESUME, or post a concrete BLOCKER. A real HOLD names the
   SPECIFIC thing it waits on and the exact release condition; open-ended "waiting for a handoff" is
   forbidden. (Lesson 2026-06-29: NoLA detour resolved + reviewer said "forward on (b)", but the
   implementer held in liveness mode awaiting a ceremonial handoff that never came.)

---

## SETUP mode (start/retarget a collaboration → questionnaire → 2 artifacts)
Run when there is no config, or `/dual-lane setup`, or the user wants to retarget to a new goal.
The questionnaire is a **generator**: ask once, then write the config (single source of task truth)
and the paste-ready Codex prompt. Pre-fill sensible defaults; only ask what varies.

**Elicit (use AskUserQuestion; offer recommended defaults):**
1. **Objective** — the one goal, in one sentence.
2. **Role assignment** — which agent IMPLEMENTS, which REVIEWS; and each lane's owned files/areas.
3. **Build command** — the one authoritative provenance-stamped build; how to read its embedded SHA.
4. **Gate / verify command** — what to run; how to read its verdict; output locations.
5. **Acceptance criteria** — the explicit bar for APPROVE (absolute thresholds, not just ratios).
6. **What to inspect** — the exact artifact(s) to OPEN and LOOK at, and what "good" looks like.
7. **Known gate blind spots** — what the scalar/gate CANNOT see, so the eyeball covers it.
8. **Anti-bypass invariants** — how to prove the real code path ran (not a shortcut).
9. **Heartbeat interval** (default 180s) and **branch** + finalize policy (default: human-OK only).

**Then write two artifacts:**
- `.claude-state/coordination/dual-lane/collab-config.md` from the answers (schema
  `dual-lane.collab-config.v1`; mirror the field set of the existing example config).
- `.claude-state/coordination/dual-lane/codex-onboarding.md` — fill
  `.claude/skills/dual-lane/codex-onboarding.template.md` with the answers. **Write it to disk AND
  echo the key block in chat** for the user to paste into a fresh Codex session.
- Scaffold `claude.md` / `codex.md` (headers + SEQ 1 STATUS) and `archive/` if missing.

Tell the user: paste the Codex prompt into a new Codex session; that session sets up its own ~3-min
watcher on `claude.md` so neither side needs a manual nudge.

---

## Hard rules (all modes)
- **The artifact is the verdict; the scalar is only a screen. Look every time** (esp. the config's
  blind spots). A CLEAN you did not look at is not a review.
- **Anchor on the known-good BUILD, never a same-codebase proxy.** When diagnosing a regression or
  validating a fix, the reference must be the last build that actually looked right, measured directly
  on real input — NOT an alternate mode/path you assume is equivalent (a "sync" oracle, a "reference"
  render) and NOT a scalar. A behavioral proxy can carry the SAME root defect, so matching it
  converges on the bug. Build/locate the known-good baseline and A/B the real output against it FIRST,
  before forming a root-cause theory; make "matches the known-good build" the acceptance bar. (Lesson:
  a night lost converging async WB onto SYNC mode — itself defective — instead of A/B-ing Jun-9.)
- **SEQ TYPE is the peer's priority signal — HANDOFF when the peer must ACT; STATUS only for pure info.**
  The peer ACTS on HANDOFF and merely PROCESSES STATUS, so an actionable directive buried in a STATUS gets
  under-prioritized or skipped. Every entry that needs the peer to DO something (implement / review / merge /
  decide) must be TYPE HANDOFF (or QUESTION) with an explicit `YOUR ACTION:` line and a precise, self-contained
  scope. Reserve STATUS for FYI you do not need acted on. (Lesson 2026-06-27: an autonomy directive + an
  auto-merge authorization were posted as STATUS; the peer nearly skipped them — *"SEQ 84 is now a live handoff,
  not just status."*)
- **Account for the peer's WAKE CADENCE — the loop runs at the SLOWER side, not yours.** Your Monitor polls in
  seconds; the peer's surface (e.g. Codex Desktop) may wake only on a scheduled poll of MINUTES because it
  cannot run a continuous background watcher. So: do NOT fire rapid sequential entries as if seen instantly, and
  never false-alarm on the lag (it is poll cadence, not death — the staleness/deadlock helper covers real
  silence). Instead BATCH context into FEWER, COMPLETE, self-contained handoffs; MINIMIZE round-trips (each
  costs ~one peer-poll interval); and in an autonomous (human-out) block, FRONT-LOAD decisions + authorizations
  into each handoff so the peer does a large chunk per wake. For a long autonomous block, ask the human up front
  to set the peer's scheduled poll faster (e.g. 2–3 min). (Lesson 2026-06-27: rapid SEQ83/84 fired while the
  peer's ~10-min poll lagged; the human had to hand-relay them.)
- **Commit the candidate before handoff** — a dirty stamp can't be pinned. Commit with
  `tools/dual-lane/lane-commit.ps1 -Lane <lane>` (lane-owned paths only, never `git add -A`); see
  "Shared working tree" above.
- Write ONLY to `claude.md` (+ `archive/`). Never edit `codex.md` or the config mid-round without an
  entry announcing it.
- **Tear down a watcher ONLY on an explicit COLLABORATION_END control entry** (TYPE COLLABORATION_END,
  either lane). Finishing/merging a work block is NOT the end of the collaboration: a work-block-done
  STATUS MUST say "collaboration continues; keep your watcher alive" (conflating "milestone merged"
  with "we are done" stranded a lane once). The heartbeat helper enforces the live side -- STALENESS
  ALERT when the peer goes dark, DEADLOCK WATCH on a both-lanes-wait standoff, both gated on
  COLLABORATION_END absence; self-heal a STALENESS ALERT by posting a RESUME-REQUEST and asking the
  human to restart the peer.
- **Never go idle while you have open assigned work or a clear forward direction** — not on an open
  CHANGES_REQUESTED, not on an assigned task whose detour/hold just cleared, not after the peer or human
  named the next work. Liveness/ledger heartbeats are NOT a holding pattern: resume the work or post a
  concrete BLOCKER. An assigned task resumes when its hold clears WITHOUT a fresh ceremonial handoff;
  direction (the reviewer's "forward on X" or the human's explicit ask) IS authorization. Mirror duty for
  the reviewer: when you resolve a detour/question whose next work is the peer's, POST the explicit forward
  HANDOFF — do not leave it implied and idle. A legitimate HOLD names the specific thing it waits on and
  the exact release condition. (Lesson 2026-06-29: NoLA detour resolved, no resume-HANDOFF posted →
  implementer held passive for many heartbeats; "no new precise HANDOFF" was misread as "no work.")
- **The direct read is the guarantee; the Monitor is best-effort.** A Monitor dies silently on
  compaction and stacks into duplicates that race the marker. So: keep EXACTLY ONE Monitor (STOP any
  existing before restart), and **directly re-read `codex.md` from your cursor at the start of every
  collaboration turn** regardless of notifications. "No notification" is never proof there is nothing new.
- **Coordination STATE comes from the FILES, never a notification snapshot.** A WAKE / heartbeat / STALE
  line -- the WAKE event text the harness surfaces, and any "no new entries" prose a lane writes -- is a
  POINT-IN-TIME SNAPSHOT that lags the live ledgers (a "no new entries" claim is true only as of its own
  timestamp; the peer may post one second later -- e.g. 2026-06-28, Codex SEQ372 "no new Claude entries"
  was written 22s before Claude SEQ131, briefly misread as a possible lane split). NEVER conclude
  split / stalled / message-missed / idle from a snapshot. Before drawing OR escalating ANY coordination
  conclusion, run the authoritative one-shot and read its VERDICT line:
  `pwsh -NoProfile -ExecutionPolicy Bypass -File .claude/skills/dual-lane/heartbeat-check.ps1 -Status -OtherLaneFile .claude-state/coordination/dual-lane/codex.md -MarkerFile .claude-state/coordination/dual-lane/.claude-marker -SelfLedgerFile .claude-state/coordination/dual-lane/claude.md -CursorKey LAST_READ_CODEX_SEQ`
  (it prints both ledgers' max-SEQ, BOTH header cursors, the inbound/outbound gaps, peer-latest age, and a
  verdict: IN-SYNC / INBOUND-PENDING / OUTBOUND-PENDING / BOTH-PENDING-race / STALE / ENDED). The cursors
  are the source of truth; the snapshot is only a hint. Corollary: when YOU write a liveness / "no new
  entries" line, self-date it -- cite your read time and the exact max-SEQ you saw -- so a later reader can
  tell at a glance whether it is stale.
- **Posting a verdict is NOT disengagement.** After a REVIEW / CHANGES_REQUESTED / STATUS, the other lane
  may reply with a QUESTION (which is YOUR work) or a re-handoff. Keep the Monitor live and direct-read
  `codex.md` on every resume — never treat "ball in their court" as "stop watching." (A ~4h idle on an
  unanswered Codex QUESTION traced to exactly this + a dead Monitor; the cursor-based heartbeat now
  re-nudges while your cursor lags, but the direct read on resume is still the guarantee.)
- **No simultaneous interactive/GUI gates** — coordinate "hold gates."
- **MERGE AUTHORITY is delegated to Claude** (standing directive, Layi 2026-06-27): on APPROVE of a
  hardened work block, merge to master AUTOMATICALLY using best judgment -- never wait for a human merge
  decision. "Hardened" = gates pass AND the **live/visible result is verified by eyeball** (not just
  metrics -- the settled-grab + scalar gates are blind to cold-pass / live artifacts; validate by pixels),
  no regression vs master, repo-state clean. If the live result is rough or a regression is suspected, do
  NOT merge -- fix or triage first. Codex hands off + does not merge.
- **Risky decisions use the two-key procedure** (see protocol): for broad-impact / hard-to-reverse /
  threading / shared-or-core-code / ambiguous-root-cause changes, do NOT implement on one lane's
  judgment — BOTH lanes independently produce adversarially-hardened plans (each red-teaming the code
  trace + the fix with their own subagents) and compare. **On AGREEMENT, AUTO-EXECUTE (implement + test,
  then review + auto-merge per the merge-authority rule) — NO human approval needed (standing directive,
  Layi 2026-06-27); keep the human informed.** Convene with the human ONLY to break a tie when the plans
  diverge and the lanes cannot reconcile.
- Archive your own ACKed, resolved, >10-SEQ-old entries per the protocol.

> Migration note: this supersedes the reviewer-only `/dual-lane-review`. That skill's
> `heartbeat-check.ps1` is kept in place only so an already-running Monitor is not broken mid-round;
> new Monitors should use `.claude/skills/dual-lane/heartbeat-check.ps1`. Remove `dual-lane-review/`
> once no Monitor references it.
