# Orchestration tiering (owner ruling 2026-09-08, adjudicated, veto open)

Standing owner instruction, September 8, 2026: Fable spends tokens only on important complex
reviews; Opus is the hub-lane traffic cop; Haiku reports status in chat sessions; adjudication
swarms are Opus; when Fable is exhausted, fall back to Opus at high effort. This document is the
tracked pointer; the decision record with measurements and the packet order lives in
`.claude-state/continuity/ORCHESTRATION-TIERING-20260908.md` (gitignored, canonical checkout only).
It composes with [error-remediation.md](error-remediation.md) and the fleet ruling that the
inference tier follows the highest-stakes act a surface performs.

## Tiers

| tier | model | effort | does | never does |
|---|---|---|---|---|
| judgement | Fable (`claude-fable-5` lane, or a Fable chat session) | low by default; high only for a Tier 1 decision | consequential reviews, design, doctrine-fold decisions, one hard adjudication per packet | implement, ratification loops, routine cards, the hub loop |
| judgement, cross-family | Codex Astra (`gpt-6-astra`) | xhigh | the same class as Fable when a cross-family seat is wanted: guard, merge, commit or ratification-path changes; contested Sol verdicts | anything routine; not reachable from codex-cli 0.147.0 (probe before adding a lane) |
| hub | Opus | high | derive board, pick ONE packet, dispatch ONE editing lane in an isolated worktree, verify the receipt, refresh the checkpoint, reconcile queue rows through the verified writer | edit product source, build, run probes itself |
| adjudication swarm | Opus, three briefs (against the default; what outranks it; post-mortem and evidence binding) | low | any blocker or "what next"; a new class of owner grant or a trust-boundary design | ruling on procedure alone (Haiku with script-first pre-filtering is admitted for that) |
| reviewer | Sol (`gpt-5.6-sol`) | low for PR review, high for design ratification | final PR review bound to the exact head; adversarial verification | editing; sole authority on a contract claim |
| implementer | Sonnet | default | one packet, one worktree, one review subject | the canonical checkout; widening scope |
| recon | Luna (`gpt-5.6-luna`) | low | read-only shards, doctrine folds, evidence audits | editing |
| status | Haiku (Desktop chat session from `.claude-state/continuity/HAIKU-STATUS-PASTE.md`) | n/a | read heartbeat, receipts, PR list; report | adjudicate, mutate, dispatch, answer "what next" |

## Rules that carry

- The hub dispatches; it does not do. A coordinator's own mutations are receipted like a lane's.
- Fallback: a Fable usage-limit refusal routes the same prompt to Opus high. The runner's refusal
  classifier detects the limit; the automatic re-route is packet LANE-FALLBACK-1 (add `max` to the
  effort set and a fallback map beside the lane table in `tools/coordination/Invoke-Lane.ps1`).
- Cross-family review is preferred, and required only for guard, merge, commit and
  ratification-path changes. Independence is four properties: author is not reviewer, adversarial
  instruction, independent access to ground truth, decorrelation by assignment. Same-family
  adversarial swarms are eligible reviews. Degraded mode when the cross-family key is dark: three
  same-family adversaries with distinct attack surfaces, a stamped `cross_family: UNAVAILABLE`
  token, and automatic re-review when the key returns. The `solVerdictPath` key in the control
  receipts stays frozen.
- Fan-out cap: total agent processes at most two per physical core; a three-agent frontier swarm
  does not run concurrently with a high-effort Sol lane.
- Before scaling Sonnet dispatch, land HOOK-FALSE-POSITIVE-1: the project hook's shell-text rule
  denied ordinary diagnostic commands and cost 14 of 27 editing runs on 2026-09-07.
