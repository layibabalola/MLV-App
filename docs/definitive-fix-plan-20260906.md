# DEFINITIVE FIX PLAN v28 — factory and product work streams (2026-09-06, rev 28 after ratification round 26)

**Executor:** Sonnet — or Haiku — as the HUB for Phase 0 and the ratification loop, following `prompts/v2/hub-procedure.md` (in this
manifest; a fresh session starts from it with nothing pasted beyond "resume our work", because `RESUME.md` and the checkpoint point at it — the pointer is the
literal path `C:\!Layi Wkspc\MLV-App\.claude-state\coordination\dual-lane\prompts\v2\hub-procedure.md`, verified after every write of RESUME.md by
`check-resume-derived-values.ps1`, which asserts BOTH that the file carries no C0 control byte other than TAB and LF and that every backticked
`$D` path it names resolves — one instance repaired is not the class repaired: a `\v` and then a `\r` in a shell heredoc each became a control
byte (O143/O149) — and RESUME.md itself carries the repair instruction, so a hub that cannot open this file still reads how to fix the pointer
(O150), and
Sonnet (every card a lane may run). Sol and Luna run ONLY as
`codex exec` processes started by `Invoke-Lane.ps1`, **always read-only**. Opus runs as a background Claude agent or lane.
**THE OWNER IS NOT IN THE LOOP** (owner directive 2026-09-06). §1 is the contract that makes that safe.

**Two bases, deliberately different.** `DIAGNOSIS_BASE = fork/master@55c486d450188e57d3e4c2b7e036fc8c85005b34` (equal to
remote `master` at 2026-09-06T05:33Z) is the frozen SUBJECT of every number in §0 and of this ratification; it is a line in
the manifest. **It does not freeze the tree.** Phase 0's own merges advance `fork/master` and do NOT trigger re-ratification;
every lane worktree branches from `fork/master` **at dispatch time**, and that `baseSha` is recorded in the dispatch receipt
and substituted into the card as `{{BASE_SHA}}`. Re-ratification is required only when the plan, the register, the wrapper,
or a `prompts/v2/*.md` file changes. **Anchors are symbols**; line numbers are illustrations at DIAGNOSIS_BASE.

**Ratification scope (bounded).** The two keys decide (a) the plan's decisions, (b) Phase 0's ordering and executability —
every precondition is a file the next step reads, and every actor can perform its act — and (c) the prompts' fidelity to
(a). A defect INSIDE a card that its own acceptance test or its `sol` review would catch is a `major` card amendment, not a
plan blocker, unless it makes a Phase 0 step unexecutable.

**What the manifest covers, stated plainly (O60).** The plan, the register, the wrapper, and every `prompts/v2/*.md` — the
bytes a lane is TOLD. It does NOT cover the bytes that RUN a lane: `Invoke-Lane.ps1`, the dispatcher/composer, the project
hook, `set-required-checks.ps1`, `demote-factory-bridge.ps1`. Each of those is bound by its own step's receipt (sha256 at
the merge sha), required CI, and the per-PR `sol` review (§1.3 step 5).

Absolute repo root: `C:\!Layi Wkspc\MLV-App` (`$R`). Dual-lane dir: `$R\.claude-state\coordination\dual-lane` (`$D`).
Executable copies: `$D\prompts\v2\` (cards and fields), `$D\Start-EditingLane.ps1` (wrapper), `$D\never-authorized.json`.
`docs/` receives a MIRROR in 0.1 for humans; the dispatcher composes from `$D` into each run directory, so a lane never
needs `docs/` or `$D`. Receipts: `$D\receipts\` (created by 0.05) and `$R\.claude-state\fleet-runs\ratify-*\`.

---

## 0. DIAGNOSIS — why months of work landed nothing (values at DIAGNOSIS_BASE, 2026-09-06 05:30-06:45Z)

| finding | measured | command |
|---|---|---|
| Product share since the 08-29 topology change | **3 of 55** non-merge commits touch `src/` or `platform/` (**5.5%**). The seat-era 17.7% is QUOTED from the doctrine bus `C:\!Layi Wkspc\softwarefactory-fleet-doctrine\specs\mlv-app.md` (72/407; derivation in `$R\.claude-state\project-memory\orchestration-topology-stall-vs-throughput-20260830.md`) | `git log 55c486d4 --since=2026-08-29 --oneline --no-merges -- src platform \| wc -l` vs without the path |
| Where the other 52 went | `tools/` 88 file-touches, `tests/` 10, `src/`+`platform/` 5 | `git log 55c486d4 --since=2026-08-29 --no-merges --name-only --pretty=format: \| cut -d/ -f1 \| sort \| uniq -c` |
| What the unattended loop dispatched, all time | **49: factory 27, playback 14, UNSET 8, product 0. Lanes: luna 29, fable 20, sonnet 0** | Counter over `$D\workstream-dispatch-log.jsonl` |
| Why sonnet is 0 | `Invoke-Workstream.ps1`: `if (-not $Lane) { $Lane = if ($needsShell) { 'luna' } else { 'fable' } }`; schedulable states are exactly `queued, dispatched, in-review, waiting-evidence, dispatched-untracked-target` | `git show 55c486d4:tools/coordination/Invoke-Workstream.ps1 \| grep -n -E "needsShell\|Schedulable ="` |
| **The loop is LIVE** | `MLV-WorkstreamLoop` cycles every ~45 min with `dryRun:false`, default `-Tracks @('playback','factory','product','UNSET')`, dispatched 2/2/1 in the 00:01/00:46/01:31Z cycles; **no kill switch existed until the hub created `$D\WORKSTREAM-LOOP-DISABLED` at 06:36Z** (O49; hub-decidable, reversible) | newest `fleet-runs\loop-cycles\cycle-*.json`; `Test-Path $D\WORKSTREAM-LOOP-DISABLED` |
| Product track, every 45 min for days | `NO-LIVE-CARDS ... it is not idle, it is EMPTY`; **no step in rev 1-4 ever wrote a product card into `queue.json`** | the same receipts; `queue.json` product open = 0 |
| Open queue cards by track | 28 open: factory 12, UNSET 13, playback 3, **product 0** under the board's OPEN set `{booked, dispatched, dispatched-untracked-target, open-risk, booked-rule, surfaced-awaiting-ordering, consult-open, changes-requested-awaiting-acceptance-evidence, optional-validation, deferred-nonblocking}`; under the dispatcher's own `$Schedulable` set the figure is **5** (factory 3, playback 2) — two senses, both stated (O82); **55 of 117 carry no `scope`** | `$q.items` counts with the named state set |
| The document that "selects the next mutating work item" | `docs/roadmap.md` last changed **2026-08-20T11:38:33-05:00** at 06568c8c (its own status-snapshot line still reads 2026-07-11); it records completed work and supplies no live product-card producer (S127) | `git log -1 --format='%H %cI %s' 55c486d4 -- docs/roadmap.md` |
| The product purpose in CLAUDE.md | Batch CDNG CLI **Phases 0-6 ALL SHIPPED**. No next goal was ever written | `git grep -n '"receipt"' 55c486d4 -- platform/qt/main.cpp` |
| Required CI checks (live) | 5 contexts, `app_id 15368`, `strict`, `enforce_admins`, `allow_force_pushes: false`, `allow_deletions: false`, `required_approving_review_count: 0`; incl. **Factory Bridge Regressions** (compiles no C++; **the only required job running `tools/coordination/test_coordination_guardrails.py`**, `tests.yml:182`) | `ratify-4361880b-…\hub-live-github-state.json`; re-GET byte-identical in round 4 |
| **No required check compiles the batch runner** | `BatchRunner.cpp`, `BatchPrompts.cpp`, `MlvTrim.cpp` in no `tests/**/*.pro`; **no PR- or push-triggered job builds `MLVApp.pro`** (the four release workflows that do are `workflow_dispatch` only); `console_tests.pro` cannot take `BatchRunner.cpp` (`QT += core` vs `MainWindow : QMainWindow`) | `git grep -l BatchRunner.cpp 55c486d4 -- 'tests/**/*.pro'` → none |
| The two bridge flakes | **#74** and **#75**, both MERGED | `gh pr view 74 -R layibabalola/MLV-App --json state,mergedAt` |
| The registered loop task's action | its `-File` target is `C:\mlvtmp\ws-driver\tools\coordination\Invoke-WorkstreamLoop.ps1` — a scratch worktree, not `$R` — and it carries NO `-Tracks`, so the four-track default runs; `-Install` persists five values and drops `-Tracks` | `(Get-ScheduledTask MLV-WorkstreamLoop).Actions.Arguments`; `git show 55c486d4:tools/coordination/Invoke-WorkstreamLoop.ps1 \| sed -n '155,161p'` |
| Lane tooling | `Invoke-Lane.ps1` defaults `-WorkDir` to the canonical checkout, accepts `-Lane sol\|luna` with `-AllowEdits` (→ `codex exec -s workspace-write`), grants editing Claude lanes `allowedTools ALL`, raw prompt passthrough, reads no receipt; the dispatcher never forwards `-AllowEdits`/`-WorkDir`, never loads `procedure`; a direct `Invoke-Lane` call writes no dispatch-log row | `git show 55c486d4:tools/coordination/Invoke-Lane.ps1 \| sed -n '51,52p;124p;262p'` |
| **The canonical checkout is on a peer branch, and local `master` has diverged** | `HEAD` = `fix/ci-cover-profiling-tests` (PR #73), clean; `55c486d4` is NOT its ancestor. Local `master` is 2 ahead / 6 behind `fork/master`: its two local-only commits `44c04f08` (session-checkpoint hook at every turn end) and `e546ea11` (pin those hooks to the absolute interpreter) are on NO fork branch (`git cherry` `+`). **A `pull --ff-only` cannot succeed (S55) and those two commits must not be stranded** | `git -C $R rev-list --left-right --count master...fork/master` → `2 6`; `git cherry fork/master master` |
| The global hook (`~/.claude/hooks/check-continuity-boundaries.py`) | Claude Code PreToolUse gate for `Bash\|PowerShell\|Write\|Edit\|NotebookEdit`; DENIES `claude auth login|logout`, the three exact token names, `.factory/` via Write/Edit; ALLOWS 28 known falsifiers; **fails OPEN**; invisible to `codex exec`; matches tool-input TEXT only; **it changed at 06:00:09Z and now guards `~/.claude/hooks/` behind the agent-bridge FREEZE marker (present, to 2026-10-05) unless a payload carries `CARD:<id>`** (S45/O46). **It is shared by every project on this machine and is NOT modified by this plan.** GitHub itself enforces NA-1 on `master` | probe a byte-identical scratch copy; `Test-Path "C:\!Layi Wkspc\agent-bridge\.claude-state\coordination\FREEZE.md"` |
| `gh` inside a lane sandbox | fails with the keyring denial `Access is denied` (dormant CLAUDE pen SEQ 565); PR #66 moved `gh` to the dispatcher. **Expected 0.15 outcome: `gh-unavailable`; the dispatcher opens PRs.** Whether a lane's Bash can READ the board root at all is UNMEASURED (O61) and is the probe's second question | `claude.md` SEQ 565 [1] |
| Entry surface (bytes) | `55c486d4:CLAUDE.md` 9,695; `55c486d4:AGENTS.md` 14,183 (DIAGNOSIS_BASE-derived). RESUME.md, the orchestrator checkpoint, SESSION-HANDOFF.md and the project-memory README are **SESSION-LOCAL: re-measure immediately before Phase 4; not DIAGNOSIS_BASE-derived values** (they were 27,894 / 18,420 / 132,302 / 124,868 at 06:45Z and the checkpoint moves every turn by design) | `git cat-file -s`; `stat -c %s` at use |
| Coordination debris | 41 worktrees at the 06:20Z diagnosis snapshot (live count re-derived at use: `git -C $R worktree list --porcelain \| grep -c '^worktree '` — 42 during the rev-20 ratification, after the 0.05 branch worktree was added; S110); 117-card queue (tracks `UNSET, continuity, factory, fleet, gate, playback, product`); 615 `.closeout-evidence/` dirs tracked; ledger `lastVerifiedAt 2026-05-08`; 15 `git.protectedBranches` incl. `claude/sleepy-brahmagupta-3996c3`; `.claude-state` 116.99 GiB logical (sol 06:20Z; it grows) / 91 GiB on disk | shown |

**RETRACTIONS carried in place — ninety-three instances of ONE class.** (1) Rev 1 grepped the wrong directory for `--receipt`. (2) Rev 2
named #71/#73 as the flakes. (3) Rev 2 cited a line number true at local master only. (4) Rev 3's headline rows were derived at
local `master`. (5) Rev 4's priority-1 card claimed its hygiene test existed at the base. (6) Rev 5 tightened `CLAUDE.md`'s cap in
the plan table and not in the executable card. (7) Rev 6's CDNG-decouple fields required "a hygiene grep test" without
naming the file or allowing its path — the standing rule was written into the plan and not into the file. (8) Rev 6 guarded
the one OPERATION that mutates a ref (0.1's fast-forward) with a proxy (`status --porcelain`, the branch name) while every
NUMBER had been hardened against the same local-vs-remote confusion; the binding test is `merge-base --is-ancestor`, and it
fails today. (9) Rev 7 said cards read only their worktree and run directory while thirteen card files still ran `git`
against the absolute board root — the convention was written into the plan, not into the files; every Phase 1 verify-first
now runs `git -C .` in the worktree, whose object store holds `{{BASE_SHA}}`. (10) Rev 7 replaced 0.1's aborting fast-forward
in the plan and left the hook procedure's receipt heading saying "after the canonical fast-forward" — the same step, the third
consecutive round, the file not the table. (11) Rev 8 moved the GPU receipt into the run directory in the FIELDS file and left the
TEMPLATE it composes with saying the run directory is only readable and restating NA-7 without the board-root half — the file
that runs beside the file that was fixed. (12) Rev 8 raised the enable gate to five receipts in one sentence and left "four" in the
next; and one card kept two bare `git` commands after the rule said every verify-first runs `git -C .`. (13) Rev 6 made the composer
0.35's own deliverable and left §1.3's receipt table requiring EVERY receipt, 0.1's included, to hash it; the same table said a change to
a hashed file invalidates a receipt while 0.6 changes `Invoke-Workstream.ps1` and 0.2 still named 0.35's — the table kept after the
files it describes moved. (14) Rev 8's 0.4c-i moved the guardrail step's TEXT into `repo-hygiene-python` and never read that job's
install step — its hashed lock does not name `pytest` — the step moved, its job's dependency set unread. (15) Rev 9 seeded `kind` and called
the cards selectable while the dispatcher that selects reads `track` — the field the plan named beside the field the script reads. (16) Rev 10
named the newest receipt in the enable gate while the install still ran whatever script sat in a checkout last moved nine steps earlier —
the table current, the path stale. (17) Round 8's own fixes introduced two: the hub turned O85's "non-zero collected count" into an equality
against a grep nobody ran (the step's second target is a directory of TestCase files), and the O86 wording prescribed a subTest table the
runner counts as ONE test — amendments applied to the sentence, not re-derived against the job, directory or runner that executes it. (18) S67
put a provenance block on 0.35's receipt and S81 re-pointed the chain to 0.6's, and neither gave the block a writer. (19) O78's `scope`
derivation was written against the fields-file shape and never checked against the three full cards it also governs. (20) O92's snapshot
rewrite was adopted without re-deriving the write against NA-2's own shrink predicate — the post-transition object is shorter than the
pre-transition one, so the rewrite the plan prescribed was the act the register denies. (21) Rev 8's 0.4a-i added a job and never read the required suite that
pins the workflow's job set, timeouts, action count and check list — the sibling of (14): the job added, its own assertion set unread.
(22) S83 fixed the PATH half of the install line and left the ARGUMENT half, which drops `-Tracks`. (23) Two of rev 11's own amendments carried
values they did not re-derive: O92's host-independence sentence was not extended to the input it introduced, and the O87 note quoted a
magnitude that had already moved. (24) The O98 registration check was written as two substrings (`python.exe` and the script path)
instead of the one exact pinned command the rule names — the third hub-introduced instance. (25) Rev 12 transcribed O94's amendment TEXT
instead of re-deriving which assertions the required suite binds; 0.4c-i, whose PR gates NA-9's only exception, never named the file, and
the hub's own run of that suite against each mutation found two more failing tests for 0.4c-ii than any reading had named — the run, not
the list, is the derivation. (26) A card's verify-first was bound to `{{BASE_SHA}}` by inserting the placeholder into a `git ls-files`
command, which ignores a tree-ish and reads the moving index — the placeholder present, the derivation absent. (27) S87 moved 0.7 and 0.9 in front of 0.2 without checking the PR list in 0.7 against the
paths §1.3 step 5's schema table hashes; #71 touches two of them, so the cleanup step made the plan's own enable gate unreachable — the ordering
amended, the file set it collides with unread. (28) Rev 14 ordered two existing fork branches REBASED without re-deriving whether publishing a
rewritten head is authorized — NA-1, the plan's own first rule, forbids it; the hub wrote the ordering fix and never checked it against the
register it enforces. (29) Rev 14 made the hook select the newest receipt "by its `recordedUtc` field" and gave no receipt writer that field
— the fourth hub-introduced instance of a value named with no writer. (30) 0.18's parity test and 0.1's wrapper cases were written against
the board root and never against the hosted checkout that collects them, where `.claude-state\` does not exist — the O97 trap, closed for
one suite and inherited by none. (31) NA-6 forbade a step move that 0.4c-i performs; the rule and the step were never read together. (32) A Phase 1 card quoted an inline-function count the base contradicts and omitted the one
header its own change forces to change — the count typed instead of derived, the file set read from the change's intent instead of from the
includers; rev 16 inserts both counts by running the derivation, not by typing the sol key's number. (33) The hub measured 0.4c-ii on a git-less
extraction that could not show the inventory test and published "four" — the venue chosen could not reproduce the property it measured.
(34) Three specification gaps the keys had to name in the round that opened the gate: the third file in #71 unread, the falsifier run's
trigger unread, the review template's inputs for the three tool cards unread. (35) The always-allowed re-arm (O96) was added without replaying
the later delete authorization against the persistent receipt state it leaves behind — the six receipts outlive the step they authorize. (36) A card counted two env-flag helpers where the tree holds six and named its test as a
single-definition guarantee it does not give, and §0.5's G6 row said the helpers disagree on empty when they agree there and disagree on `off`
— the count and the property both quoted, neither derived; and the O113 sentence obliged 0.15 to a review of a PR it never lands. (37) The
S98 fix added the enable receipt as a state the delete requires ABSENT without requiring the delete to CREATE it — the guarded pair asserted in
the plan, unenforced in the hook. (38) The hub's "six helpers" came from a search that excluded the card's own target function. (39) The guarded enable was specified against
the hook's TEXT rule and never replayed against the hook's attribution of verbs to paths — resolved by making the enable a dedicated act. (40) 0.7's
#73 instruction read one of its three files. (41) 0.35 named two placeholders the card it composes does not carry. (42) The canonical compound was written without replaying PowerShell's
error semantics: a non-terminating `Set-Content` failure let the delete run. (43) "Extended, never rewritten" named an operation JSON cannot
express and no actor to perform it. (44) G6 adopted a key's claim that the helpers agree on empty, contradicting the card's own measured pair. (45) Rev 18 corrected two of the three surfaces that state the enable and left the register —
the file the hook implements — unchanged. (46) The plan's amendment loop and the hub's own checkpoint rotation both assumed an actor the hook,
once live, denies; the rotation had been passing only as an invisible launcher. (47) Rev 19 added an exact-input fixture pre-flight without deriving
how its fixture could be written or how the real input's paths could match a scratch root. (48) Rev 19 bound the amendment loop to the hub's venue
and left the enable act itself unbound, and appended a fourth exception under a heading that still said three. (49) Exception (iv) was keyed on
an environment variable measured absent from every recorded hook process on this machine, while three of this repo's own four hooks already pass
that value as an argument — the discriminator chosen without deriving whether the hook can read it. (50) S101's rationale named the wrong arm as
load-bearing and the hub's own reproduction used the one shape (an unresolvable drive) in which the form failed OPEN. (51) "Cannot change it" was
asserted for a lane's hook environment without considering a persistent-scope write, and no rule covered a lane editing its own gate. (52) The pre-0.35 reviews assumed the exporter that 0.35 itself delivers, and named no writer
for the PR-checks file the review binds to. (53) PROD-TOOLCHAIN-1 changed values a required suite pins while its allowed paths omitted that
suite — a card scoped without running the check its own CI would fail. (54) Rev 20 fixed the compound's FORM by measurement and then restated a
per-arm rationale the same measurement contradicts — the third consecutive round in which this compound's justification named the wrong arm.
(55) The hub added NA-10 and updated the register and the hook's rule block, leaving the plan's own §1.1 enumeration and five card surfaces —
including the template every product card composes with — saying NA-1..NA-9: the rule added, the surfaces that state it not re-derived. (56) PROD-TOOLCHAIN-1 required workflow-dispatch evidence from a lane that has no `gh` and named no
other actor. (57) The enable literal's fields were required to be PRESENT, never to be TRUE — the receipt name and hash were validated for shape
only, so any strings unlocked the one-shot. (58) S108 gave the pre-dispatch exporter a named actor and a named tool and never ran its command
in the venue that runs it: from `$R`, with two remotes and no default, `gh` addresses the upstream — loudly for `gh pr checks`, silently for
`gh workflow`; the plan's own probe card had pinned `-R` on both of its calls, and the convention was written into the card, not into the steps
the hub executes. (59) Rev 21 corrected the compound's rationale and mis-stated the retained arms in the same sentence — the fourth consecutive
round in which this compound's justification named the wrong arm; `-ErrorAction Stop` changes no outcome in any class. (60) The NA-10 sweep
keyed on the token `NA-1..NA-9`/`All nine`, so the one card that enumerates the rows individually and stops at NA-8 was invisible to it. (61) The hub procedure stated a lane rule the plan's own 0.15 and every card contradict — written from
memory of a constraint, not from the steps that run. (62) The pre-dispatch exporter was given the checks and not the PR body or head the review
template binds to — the artifact set derived from one command, not from the template's own items. (63) The toolchain card's evidence actor was
written in the same rev that pinned every hub-run `gh`, and the pin sweep covered the plan alone. (64) The procedure's hook-authoring venue was
carried from the pre-hook world into the post-hook one, where NA-7 and NA-10 both deny it. (65) "The five steps" counted a list the plan had
already outgrown. (66) S112 pinned a notation for the enable literal's stamps and left the receipts it orders unpinned and lexically compared,
so two stamps the hook itself accepts selected the wrong newest — and the amending key's own remedy, "lexical order equals chronological
order", measured false for optional fractions. (67) A backslash-v escape in a shell heredoc corrupted the one pointer the entry design rests
on, and no checker looks for a control character. (68) A card asserted "no other behaviour change" for an allowlist replacing two blocklists. (69) The gate's six receipts were required to EXIST and to "validate" with no schema anywhere; five
empty objects and a forged control name unlocked the enable on the sixth commit — "validate" written as a word, not as a rule the file that runs
could implement. (70) The O143 repair verified the one byte it had corrected and never scanned the file for the class; a second control byte
four lines below survived a round. (71) The pinned notation was written on three surfaces and not on the one the hub executes while writing
receipts. (72) The lane rule made `{{PR_STEP}}` the authorization to open a PR while no ratified surface stated its text. (73) The schema table was amended without re-deriving every field its own producer paragraph
requires — the second key's approval left out of the gate that exists to require it. (74) The exporter exported what one command returns, not
what the review needs: the required-context set was never exported. (75) A ratified literal was abbreviated with an ellipsis in the file the
composer implements, and the placeholder it nests was missing from the runtime values. (76) The PR_STEP literals were pasted into 0.35 without re-running the
composition against the one card 0.35 composes, whose branch is a literal in its own text (and which, O166 found, carried the token once as a
quoted word). (77) A recovery written for the chain deadlocked on the receipt
the chain binds. (78) A timestamp derivation was pinned without its culture, and two universals ("every receipt write uses the Write tool",
"every receipt carries recordedUtc") contradicted the plan's own receipt specs. (79) The reviewed PR head was relabelled as the merge sha without
re-deriving the before-merge review workflow, so the receipt could not conform to its own schema. (80) The procedure kept the universal Write-tool
instruction after the plan had scoped it to the hub's own tool calls. (81) The key set was stated three ways after the table had been declared
the one table. (82) The producer at 0.7 never received the keys its own schema requires. (83) The composition fix asserted a card carried no
placeholder while quoting the derivation that finds one, and reached the loop card's runtime list but not the template's. (84) The pre-flight
artifact was specified inside a rule that denied its own content, and the hook's actual decision was never measured against that sentence. (85) The
exporter was moved from the hub to 0.35 without a deliverable on the card 0.35 dispatches. (86) A diagnosis date was quoted from the file's own
snapshot line instead of derived from its history, and the register's version was bumped without the two surfaces that cite it. (87) A named
command was never run in the venue that runs it — the merge sha was asserted present with no fetch between the merge and the receipt. (88) A
stamp-preservation fix reached one surface of three, and the equality assertion was left with no artifact against the plan's own hookWiredProof
precedent. (89) A claim stood beside a derivation that does not produce it, and a producer asked for a key its authoritative table lacks. (90) The 0.7 producer carried a key from the ratio-guard receipt into a control receipt
whose row never defined it. (91) A proof field was pinned without its canonicalisation and made recomputable from the receipt it was meant to
prove. (92) A recovery was written that re-creates the state it clears, the stamp universal never carving it. (93) An artifact was made a one-shot
with no recovery, and an exporter deliverable was written with commands that diverge from the two consumers it feeds. Each is a value quoted instead of derived, or a table edited instead of the file
that runs. The mechanical fixes: the base is a manifest line; every command names it; both keys re-derive at it; every verify-first
block is bound to `{{BASE_SHA}}`; and **where a card or fields file and this table disagree, the FILE is the deliverable and the
table is corrected** (applies to full cards too — O52a).

**The one-sentence cause:** the 08-29 fix changed WHO acts but not WHAT they act on; the product track was empty, nothing ever wrote
a product card, the dispatcher could not pick an implementer, and 49 dispatches went to recon and review of factory cards. **Phase 0
builds the ability to run one product lane unattended and safely, seeds the backlog, and nothing else.**

---

## 0.5 WHAT THE GROK REVIEWS (2026-09-01) ADD — adopt / distinguish, verified at DIAGNOSIS_BASE

| # | Grok finding | disposition |
|---|---|---|
| G1 | Release CI builds Qt 5.15.2 / MinGW 8.1 (`Windows.yml`), Qt5 via apt (`Linux.yml`); oracles run 6.10.2 / 13.1 | **ADOPT** → PROD-TOOLCHAIN-1 |
| G2 | 32 commits behind upstream | **ADOPT** → PROD-UPSTREAM-SYNC-1 |
| G3 | 625 files / 177.0 MB (168.8 MiB) at BASE under `.claude/profiling`: 20 DNGs carry 164.0 MB (156.4 MiB); 605 non-DNG hold 13.0 MB | **ADOPT, narrowed** → HYG-PROFILING-MEDIA-1 |
| G4 | `VerifyNone` on pixel-map downloads | **ADOPT** → PROD-TLS-VERIFY-1 (all in `DownloadManager`) and 1b |
| G5 | Headless CDNG links `MainWindow` | **ADOPT** → PROD-CDNG-DECOUPLE-1 |
| G6 | Seven env-flag helpers in four incompatible families (the card's own target `mlvappEnvFlagEnabled` is the seventh); the six `envFlagEnabled` helpers return FALSE for a set-but-empty value while `mlvappEnvFlagEnabled` returns TRUE (the card's own pair), all return FALSE when unset, and they disagree on non-canonical values (`off` is FALSE under `main.cpp`, TRUE under `rbf_wrapper.cpp`) (S103/O122) — derive: `git grep -n -E 'bool (mlvappEnvFlagEnabled\|envFlagEnabled)\(' 55c486d4 -- platform src` (S100) | **ADOPT** → PROD-ENVFLAG-1, scoped to two of the seven (O117/O122) |
| G7 | 615 evidence dirs; ledger frozen May | **ADOPT, bounded** → HYG-EVIDENCE-RATCHET-1 |
| G8 | Skipped/paused CI counts as green | **ADOPT; rejected rev 1's `continue-on-error`** |
| G9 | Lanes in isolated worktrees | **ADOPT** → TOOL-LOOP-PLUMBING-1 |
| G10 | Handoffs per landed product commit; round cap; retry changes bytes | **ADOPT** → 0.6 + Phase 2 |
| G11 | Cross-family two-key; bots are eligibility only | **ADOPT** → one `sol` review per PR, fed by dispatcher exports (O55) |
| G12 | Machine-readable `never_authorized`; owner register | **ADOPT, inverted:** register EMPTY; NA-1..NA-10 with enforcement scope stated |
| G13 | Collapse four planes | **DISTINGUISH:** one landing bus (GitHub PRs); collapse Phase-3-gated |
| G14 | Activate lane-guard hook | **DISTINGUISH:** moot with one writer per worktree |
| G15 | Split `BatchTypes.h` | **ADOPT, late** → PROD-BATCHTYPES-SPLIT-1 |
| G16 | 440 KB bus logs boot-mandatory | **ADOPT:** delta only, ≤ 16 KB |
| G17 | CIAA ratification | **ADOPTED and exercised four times** |

---

## 1. AUTONOMY CONTRACT — decisions with the owner out of the loop

**1.1 `never_authorized`** = `never-authorized.json` v26 (NA-1..NA-10; mirrored to `docs/` in 0.1). **Enforcement, measured, with
its LIMITS and SCOPE:**
- **Machine-enforced today:** NA-1 on `master` by GitHub; NA-3 for `claude auth login|logout` and the three exact token names (global hook);
  NA-7 for `.factory/` via Write/Edit (global hook). The global hook fails OPEN on malformed input and is not ours to change.
- **The MLV-App PROJECT HOOK (0.05 + 0.1)** — `tools/hooks/mlv-never-authorized.py`, registered as a PreToolUse hook in the
  tracked `.claude/settings.json` **with the pinned absolute interpreter this repo already uses for every hook**
  (`"C:/Users/obabalola/AppData/Local/Python/bin/python.exe" "${CLAUDE_PROJECT_DIR}/tools/hooks/mlv-never-authorized.py" --project-dir "${CLAUDE_PROJECT_DIR}"`;
  the file records that `py -3` hooks silently never executed until 2026-08-09 — S54), so it applies in the board root AND in
  every lane worktree at or after 0.1's merge — fails CLOSED and enforces NA-1 (other refs/URLs), NA-2 (with the receipt carve-out below), NA-3 prefixes +
  `codex login`/`setx`, NA-4 (fixtures under `<worktree>/tests/fixtures/clips/` always allowed; any other clip must equal the
  card's `CLIP_OR_NONE` as ONE canonical absolute path), NA-6, NA-7 (Bash and foreign trees; the global hook's `.factory/` rule
  stays), NA-8, NA-9, NA-10 — one DENY test per row, run in CI. The global hook is never edited, so no freeze token is borrowed and no
  guard is routed around (S45/O46 both moot).
- **NA-2 carve-out (O47):** create-or-extend writes under `$D\receipts\**`, `$D\queue.json`, `$D\lane-gh-capability.json`,
  `$D\digest\**`, `$D\*-resume-CURRENT.md` and `.claude-state\fleet-runs\**` are ALLOWED; on those paths NA-2 is a SHRINK guard
  only (DENY on delete/move, or when the target exists and the new content is shorter).
  **The carve-out is a PATH permission, not a TOOL permission (O125):** a shell truncating write (`>`, `Set-Content`, `Out-File`, `tee`) cannot be
  proven non-shrinking and is DENIED on these paths even for a create (hub-measured: shell `Set-Content` create of `0.2-loop-enabled.json` exit 2,
  the `Write` create of the same path exit 0), so every Phase-0 receipt write after 0.1 THAT THE HUB PERFORMS AS A TOOL CALL uses the `Write`/`Edit` tool or an append
  (`Add-Content`, `>>`); the two receipts written from inside a tracked script — `0.4b-required-checks.json` by `set-required-checks.ps1` and
  `0.5-factory-frozen.json` by `freeze-factory-cards.py` — are outside the hook's view by the LIMIT below, and their non-shrinking discipline is
  the script's own, asserted by its unit tests (O161); 0.2's canonical compound is the ONE shell write these paths admit, and only because the dedicated enable act is evaluated before the
  generic rule — its `Set-Content` half is denied by that rule too, not merely its `Remove-Item` half.
- **Exception (iv) — the amendment loop's actor (O124):** once the hook is live in the hub's session every route to amending a manifest
  surface under `$D` is denied (hub-measured on the branch hook: `Write`, `Edit` and shell `Set-Content` of the plan all exit 2), yet re-ratification
  (§1.3) and 0.2's interruption recovery both require exactly that. So writes — create, extend or amend, any length — to the four ratified manifest
  surfaces `$D\DEFINITIVE-FIX-PLAN-*.md`, `$D\never-authorized.json`, `$D\Start-EditingLane.ps1` and `$D\prompts\v2\**` are ALLOWED only when the hook
  runs for a session ROOTED AT THE BOARD: the hook's own `--project-dir` argument — substituted by Claude Code from `${CLAUDE_PROJECT_DIR}` in the
  registered command, so no tool call can set it; the ENVIRONMENT variable of that name is measured ABSENT from every recorded hook process on
  this machine and three of this repo's five registered hook commands already pass the value as an argument — `--repo-root`, `--repo` and `--consumer`; a fourth uses
  it only in its script path (O126/O156) — normalised, equals `MLV_BOARD_ROOT` (the hub
  after 0.1); a lane's hook receives its `C:\mlvtmp\` worktree, which no tool call inside that session can change — a PERSISTENT user-scope write
  of `CLAUDE_PROJECT_DIR` or `MLV_BOARD_ROOT` would reach a LATER hook process, which is why NA-3 names both and the hook denies them, and a lane's
  edit of its own `.claude/settings.json` or of the hook script (re-read on every call) is NA-10 (O129); a missing or empty `--project-dir` → the
  exception does not apply. Delete/move of a manifest surface stays DENIED, the wrapper
  refuses `workdir-is-board-root`, and the guard is §1.3's own: the amended manifest is re-hashed and re-ratified before any step runs against it.
- **LIMIT:** a hook matches TOOL-INPUT TEXT. An interpreter one-liner or a script invocation performs any act without matching
  a pattern; the hook sees the launcher. It raises the cost of a never-authorized act. The other layers: the worktree boundary
  (`--add-dir` roots), the explicit tool allowlist (no `ALL`; no MCP tool reaches an editing lane), the receipt check inside
  `Invoke-Lane.ps1` and again in the wrapper, GitHub's own protection on `master`, and the cross-family `sol` review before every
  merge. NA-9 is bounded by `set-required-checks.ps1`'s internal receipt gate, not by any hook.
- **SCOPE:** no Claude hook is visible to `codex exec`. **No Codex lane is ever started with `-AllowEdits`**, refused at three
  layers: `Invoke-Lane.ps1` itself (0.1), the wrapper, and the dispatcher.

**1.2 Everything else is hub-decidable.** Reversible state executes without asking (the kill switch created at 06:36Z is an
instance). Branch protection: the hub may ADD a required context; removing or weakening is NA-9 except the single recorded
0.4b transition.

**1.3 Two-key adjudication.**
1. **Subject = the MANIFEST HASH** over sorted lines `"<sha256>  <basename>"` of the plan, `never-authorized.json`,
   `Start-EditingLane.ps1`, every `prompts/v2/*.md`, plus `"<DIAGNOSIS_BASE>  BASE"`. Canonical command (`python`):
   ```
   python -c "import hashlib,glob,os;D=r'C:\!Layi Wkspc\MLV-App\.claude-state\coordination\dual-lane';fs=sorted([D+r'\DEFINITIVE-FIX-PLAN-20260906.md',D+r'\never-authorized.json',D+r'\Start-EditingLane.ps1']+glob.glob(D+r'\prompts\v2\*.md'),key=os.path.basename);base='55c486d450188e57d3e4c2b7e036fc8c85005b34';m='\n'.join([hashlib.sha256(open(f,'rb').read()).hexdigest()+'  '+os.path.basename(f) for f in fs]+[base+'  BASE']);print(hashlib.sha256(m.encode()).hexdigest());print(base)"
   ```
2. **Two independent verdicts, different model families, same subject, neither sees the other:** `sol` (read-only `codex exec`;
   reviews hook SOURCE and test TABLE) and a background Claude Opus agent (Bash, read-only by instruction; probes a scratch copy).
3. **Agree** → apply the union. **Diverge on a FINDING** → third lane, `adjudicate-TEMPLATE.md` — which the hub composes by hand exactly as it composes the
   review template, filling `{{SUBJECT_SHA256}}`, `{{SUBJECT_PATHS}}`, `{{FAMILY_A}}`/`{{FAMILY_B}}`, `{{VERDICT_A}}`/`{{VERDICT_B}}`,
   `{{RECEIPT_A}}`/`{{RECEIPT_B}}` and `{{DISPUTED_FINDINGS}}` (one block per contested finding, carrying the raising key's own repro verbatim),
   enumerated with `grep -oE '\{\{[A-Za-z0-9_]+\}\}'` and asserting no `{{` remains before dispatch (O146); **or the hub rejects a premise
   both amendments share and records why** (round 4, S45/O46). **Diverge only on the LABEL** → union; re-ratification is the adjudication.
4. **Receipt** under `fleet-runs\ratify-<first8>-<ts>\`; one line in `orchestrator-resume-CURRENT.md`. **The hub's checkpoint ROTATION under the
   live hook takes exception (ii)'s shape — a byte-identical copy of the whole pre-rotation CURRENT under `.claude-state\continuity\archive\`, then
   the new CURRENT naming that copy's sha256 — because the hook's extend test is LENGTH-based (a longer `Write` passes, hub-measured on the branch
   hook) and a shorter one is denied; the former `py -3` rotation script passed only as an invisible launcher (the LIMIT), a route-around, not a
   permission (hub observation, round 17; practised from round 18).**
5. **Execution-control receipts (S43/O60; lifecycle S80/S81).** After each tooling merge (0.1, 0.35, 0.4c-i, 0.4b-i, **0.6**, and **0.7 when it lands a PR touching a hashed file**) the hub
   writes `$D\receipts\execution-control-<step>.json` = sha256 of the project hook, its rule test, `tools/hooks/test_registration_path_local.py`,
   `Invoke-Lane.ps1`, `Invoke-Workstream.ps1`, `Invoke-WorkstreamLoop.ps1` and the step's own additions per the RECEIPT SCHEMAS table below — the ONE authority for the key set (O164) — at the merge sha
   (`mergeSha`), plus the path of
   `sol`'s APPROVE on that PR, **and `recordedUtc` (ISO-8601 UTC, stamped by the writing hub step at the moment it writes the receipt, with TWO exceptions: the O159 cascade below, which preserves each rewritten chain receipt's original stamp (O171); and an O152 repair of a chain
   receipt other than the selected one, which carries a conforming stamp strictly earlier than the selected receipt's parsed stamp and strictly later
   than that of the chain receipt preceding it — and, for `execution-control-0.1.json`, which has no predecessor, strictly earlier than the parsed stamp of the earliest OTHER valid chain receipt on disk — never the moment of the write, which would re-create the undecidable state the repair exists to
   clear (O177; measured on the tenth commit: an order-preserving repair stamp ALLOWS, a write-time stamp DENIES) — in EXACTLY the notation `YYYY-MM-DDTHH:MM:SSZ`: whole seconds,
   uppercase `Z`, no fraction, no offset — the ONE notation every `recordedUtc` the hook reads or validates carries, receipts and enable literal
   alike; the hook validates each receipt's stamp against it and a receipt whose stamp fails is INVALID, which makes the newest undecidable and
   fails the exception closed; it orders by the PARSED value and treats an exact tie as undecidable. A chain receipt that is PRESENT but invalid — a missing or non-conforming
   stamp, a missing provenance key — is never silently excluded, because exclusion would re-open O141's hazard (an invalid 0.7 beside a valid 0.6
   would select the stale 0.6): it makes the newest undecidable, and O105's "no later step may treat it as the newest" now reads as this rule.
   Recovery from an undecidable set — a malformed receipt or an exact tie — is a NON-shrinking rewrite of the offending receipt with the `Write`
   tool carrying a conforming stamp and every required key, which the receipts carve-out allows, recorded in the checkpoint; never a delete (O152). When the offending receipt is `0.18-roadmap-parity.json` — whose sha256 every chain receipt
   from 0.35 on carries — the same non-shrinking `Write` rewrite is followed, IN THE SAME HUB STEP, by a non-shrinking `Write` rewrite of every
   chain receipt from 0.35 on, re-pointing `roadmapParityReceiptSha256` to the repaired file's new sha256 while preserving `queueSha256`,
   `productLiveCount` and each receipt's original `recordedUtc` VERBATIM (the chain's parsed order is unchanged, so no tie can be introduced — O169): the ONE amendment to "carried forward unchanged", recorded in the checkpoint, never a delete (O159). The hub measured that with fractional
   seconds optional the lexical order is NOT the chronological one — `…:00Z` sorts after `…:00.500000Z`, and the sixth hook commit selected the
   stale receipt on exactly that pair — so a fixed-width notation is pinned rather than a rule about text order (O141)) **RECEIPT SCHEMAS (S118) — what "validate" MEANS, the ONE table the hook implements; an empty object, a missing or empty key, a wrong
   type, a malformed value or a path that does not exist on disk is INVALID and the refusal names the file and the key.** Every gate receipt
   carries `recordedUtc` in the pinned notation. Value classes: `sha256` = 64 lowercase hex; `sha` = 40 lowercase hex; `path` = a file that exists,
   absolute or relative to `$R`; `url` = begins `https://github.com/layibabalola/MLV-App/`; `int` = a non-negative integer; `list` = a JSON array.
   - `0.18-roadmap-parity.json` = `{recordedUtc, queueArmResultSha256: sha256, composedPromptPath: path, prChecksPath: path, prReviewPath: path,
     solVerdictPath: path}`.
   - `0.4b-required-checks.json` = `{recordedUtc, headSha: sha, preContexts: list, postContexts: list (exactly the canonical five, containing
     `Batch Compile`), snapshotRowSha256: sha256 (of the row 0.4b-ii appended to `required-checks-live.jsonl`)}`.
   - `0.4c-demoted.json` = `{recordedUtc, headSha: sha, mergeSha: sha, runUrl: url, solVerdictPath: path}`.
   - `0.6-ratio-guard.json` = `{recordedUtc, mergeSha: sha, firstReading (present and non-empty), solVerdictPath: path}`.
   - `0.5-factory-frozen.json` = `{recordedUtc, queueSha256: sha256, frozenCount: int, dryRunDiffSha256: sha256, scopelessIds: list}`.
   - `execution-control-<step>.json`, `<step>` ∈ {`0.1`, `0.35`, `0.4c-i`, `0.4b-i`, `0.6`, `0.7`} — the SIX chain names and no other: `{recordedUtc, reviewedHeadSha: sha (the PR HEAD sol reviewed BEFORE the merge —
     the sha the verdict's subject_sha binds to; a GitHub merge lands a DIFFERENT commit, so the merge sha can never be the reviewed one), mergeSha: sha
     (the post-merge fork/master commit at which the receipt's hashes were taken), solVerdictPath: path whose terminal JSON block carries verdict
     APPROVE and subject_sha == reviewedHeadSha — the second key's approval of THIS head, without which no control receipt validates (S120); before the
     receipt is written the hub runs `git -C $R fetch fork` so BOTH shas are present in `$R` (a GitHub merge creates its commit server-side, and a
     squash or rebase merge leaves the reviewed head unreachable from fork/master — the pre-merge exporter fetch is what keeps it), asserts
     `git -C $R cat-file -e <reviewedHeadSha>^{commit}` and `git -C $R cat-file -e <mergeSha>^{commit}` both succeed (O170), and then hashes the step's
     fixed set at BOTH shas (`git -C $R show <sha>:<path>` piped to sha256, each path) and asserts
     equality — a mismatch means the merge carried something the review did not see, and no control receipt is written until sol has reviewed the
     merge commit itself; the hook checks shape and the subject binding only, never git (S123) —, fixedSetEqualityProof: the literal string
     `<reviewedHeadSha>=<mergeSha>:<digest>` where <digest> is produced by the canonical command
     `python -c "import hashlib,sys,json;h=json.load(open(sys.argv[1]))['hashes'];print(hashlib.sha256('\n'.join(sorted(h[k]+'  '+k for k in h)).encode()).hexdigest())" <receipt>`
     — one `<sha256>  <path>` line per `hashes` entry (two spaces), whole lines sorted as strings, joined by a single LF with NO trailing newline,
     UTF-8, the `hashes` keys verbatim (O175; hub-measured: five admissible readings of the former sentence gave five digests over the BASE set) —
     which the HUB computes over the step's fixed set AS HASHED AT `reviewedHeadSha` (the same lines built from `git -C $R show
     <reviewedHeadSha>:<path>` digests). The hub records those digests as a named artifact FIRST — `$D\receipts\<step>-reviewed-head-hashes.json` =
     `{recordedUtc, reviewedHeadSha, hashes}`, written with the `Write` tool by this step's hub act under the receipts carve-out, its `hashes` key
     set exactly the step's required set — and `fixedSetEqualityProof`'s digest is the canonical command run with THAT file as `sys.argv[1]`, never
     with the receipt (O180; a digest computed by running the pinned command against the receipt it is meant to prove is arithmetic, not evidence).
     The hook still recomputes it from the receipt's own `hashes`, which are taken at `mergeSha`: a match IS the
     equality, and a hub that skipped the run cannot produce a passing value (O176) — written only after the hub has run the assertion — the hook validates its SHAPE (two 40-hex shas, one 64-hex digest), that its first
     sha equals reviewedHeadSha and its second equals mergeSha, and that its digest equals the sha256 of the sorted `<sha256>  <path>` lines
     recomputed from the receipt's own `hashes` object (pure recomputation, no git — so a proof cannot be pasted from another receipt); a receipt
     lacking it is INVALID (O172; the ELEVENTH hook commit) —, hashes: object whose KEY SET is EXACTLY this step's
     required set and whose values are sha256 — BASE = {tools/hooks/mlv-never-authorized.py, tools/repo_hygiene/test_mlv_never_authorized.py,
     tools/hooks/test_registration_path_local.py, tools/coordination/Invoke-Lane.ps1, tools/coordination/Invoke-Workstream.ps1,
     tools/coordination/Invoke-WorkstreamLoop.ps1}; 0.1 = BASE; 0.35 = BASE + tools/coordination/Compose-LanePrompt.ps1; 0.4c-i = 0.35's set
     + tools/coordination/demote-factory-bridge.ps1; 0.4b-i = 0.4c-i's set + tools/coordination/set-required-checks.ps1; 0.6 and 0.7 = 0.4b-i's set +
     tools/coordination/Test-ProductRatioGuard.ps1 + tools/coordination/freeze-factory-cards.py (0.5's script, landed before 0.6); a missing or an
     extra key is INVALID, and 0.2 re-hashes that FIXED set in `$R`, never merely the keys present (O158) —, and additionally: 0.1 carries composerStatus ==
     "not-yet-created", composedPromptPath, prChecksPath and prReviewPath (paths); 0.35 carries composedPromptPath, prChecksPath and
     prReviewPath (paths); from 0.35 on roadmapParityReceiptSha256: sha256 EQUAL to the sha256 of `0.18-roadmap-parity.json` as it is on disk,
     queueSha256: sha256 (carried forward as a value), productLiveCount: exactly 15}`. The SELECTED
     control receipt is exactly `execution-control-0.7.json` when a valid one exists, else exactly `execution-control-0.6.json`; the parsed-newest
     valid chain receipt must be that selected one (0.6 stamped later than a valid 0.7 is a chain violation → undecidable); any other
     `execution-control-*.json` name is INVALID and fails the exception closed. Measured on the sixth hook commit: five `{}` receipts beside
     `execution-control-forged.json` carrying only the provenance keys ALLOWED the enable (S118). — the
   field the project hook and NA-2 exception (i) select the NEWEST receipt on; a receipt without `recordedUtc` is INVALID and no later step
   may treat it as the newest (O105)**. **The three Phase-0 tool cards carry no `CARD_ID:`/`DELIVERABLE:`/`ACCEPTANCE:`/`ALLOWED_PATHS:` labels,
   so for the three PRs reviewed BEFORE the exporter exists — the combined 0.05/0.1 PR, the 0.18 PR and the 0.35 PR — the HUB is the
   pre-dispatch EXPORTER (S108: at DIAGNOSIS_BASE nothing writes the fetched objects or the `pr-<n>-checks.json` the review prompt binds to —
   `git grep -E 'gh pr checks|pr-.*-checks\.json|retrievedUtc' 55c486d4 -- tools/coordination/Invoke-Workstream.ps1 tools/coordination/Invoke-Lane.ps1`
   → none — and 0.35 itself delivers that exporter): before starting `sol` it runs `git -C $R fetch fork`, asserts the full head and base objects
   exist (`git -C $R cat-file -e <sha40>^{commit}` for both), reads `gh pr view <n> -R layibabalola/MLV-App --json number,headRefOid,body,state` AND `gh api repos/layibabalola/MLV-App/branches/master/protection`
   (the LIVE required-context set) BEFORE and AFTER collecting the checks — refusing, or
   retrying from the fetch, if `headRefOid` moved between the two reads or differs from `{{HEAD_SHA_40}}` — writes
   `$R\.claude-state\fleet-runs\<review-run>\pr-<n>-checks.json` from `gh pr checks <n> -R layibabalola/MLV-App --json name,state,link` with a
   `retrievedUtc` AND `pr-<n>-review.json` carrying `headRefOidBefore` and `headRefOidAfter` (both equal to `{{HEAD_SHA_40}}` — the checks carry no sha, so the two reads are what bind
   them to the head), `requiredContextsBefore` and `requiredContextsAfter` (equal, from the two protection GETs — without them an absent required check is
   indistinguishable from a non-required one that never ran, S121), `body`, the checks and `retrievedUtc` — the artifact the review template's
   item 4 verifies, because a review cannot bind to a body and a head it was never given (S114) — and composes `sol-review-PR-TEMPLATE.md` by hand, filling `{{ALLOWED_PATHS}}` from the card's own
   Files/Deliverable section — for 0.1 exactly `tools/hooks/**`, `tools/repo_hygiene/test_mlv_never_authorized.py`, `.claude/settings.json`,
   `tools/coordination/Invoke-Lane.ps1`, `docs/**` and the paths of the two preserved local commits — recording `composedPromptPath`, `prChecksPath`, `prReviewPath` and
   `solVerdictPath` in the step's own receipt (`execution-control-0.1.json`, `0.18-roadmap-parity.json`, `execution-control-0.35.json`) — the permanent
   dispatcher assumes the export only after 0.35 merges (S108); **every `gh` invocation in this plan names the repository explicitly
   (`-R layibabalola/MLV-App`, or the `repos/layibabalola/MLV-App/…` API path): the board carries two remotes (`fork` = layibabalola,
   `origin` = ilia3101) and no default repo, so an unpinned `gh` resolves to the UPSTREAM — measured from `$R`: `gh repo view --json nameWithOwner`
   returns `ilia3101/MLV-App`, `gh pr checks 74` exits 1, and `gh workflow list` exits 0 against the wrong repository (O135)**; an uncomposable `{{ALLOWED_PATHS}}` is CHANGES_REQUESTED, never a blank (O113). **The hub fills EVERY placeholder the template carries —
   `{{PR_NUMBER}}`, `{{HEAD_SHA_40}}`, `{{BASE_SHA_40}}`, `{{RUNDIR}}`, `{{CARD_ID}}`, `{{DELIVERABLE}}`, `{{ACCEPTANCE}}`, `{{ALLOWED_PATHS}}` — enumerated with
   `grep -oE '\{\{[A-Za-z0-9_]+\}\}'` (the narrower `[A-Z_]` form cannot see the two sha placeholders, which contain digits and are the ones the
   verdict binds to), and asserts no `{{` remains in the composed prompt before dispatching it, exactly as 0.35 does for a composed card (O123).** 0.15 merges nothing and appears in no receipt chain — its probe PR is opened and closed by the lane in the same run
   — so it owes no composed review and no `solVerdictPath`; its evidence is `$D\lane-gh-capability.json` (O114).** **`Compose-LanePrompt.ps1` is 0.35's own deliverable and does not exist before it (absent at
   DIAGNOSIS_BASE — `git cat-file -e 55c486d4:tools/coordination/Compose-LanePrompt.ps1` fails): `execution-control-0.1.json` hashes no
   composer and records `composerStatus: "not-yet-created"`; 0.35's receipt and every later one require and hash it (S80).** Receipts form
   a CHAIN in which each supersedes the previous: 0.15 requires the post-0.1 receipt; 0.4b-ii and 0.4c-ii require theirs; **0.6 changes
   `Invoke-Workstream.ps1` (it becomes the ratio guard's caller), which by this rule invalidates 0.35's receipt, so 0.6 writes
   `execution-control-0.6.json` — hashing EXACTLY the eleven paths the RECEIPT SCHEMAS table gives for 0.6 (O164), and validating and carrying forward 0.35's `{roadmapParityReceiptSha256, queueSha256, productLiveCount: 15}`
   — and the loop's re-`-Install` at 0.2 requires THAT receipt, the newest (S81) — or `execution-control-0.7.json` when 0.7 landed a PR touching a hashed file (#71
   touches two of the hashed paths), in which case 0.7 writes it and 0.2 requires it (O102).** Ordinary `fork/master` movement does not invalidate a
   receipt; a change to any listed file does, and the step that changes one writes the next receipt in the chain. **0.35's receipt additionally
   carries the provenance block `{roadmapParityReceiptSha256, queueSha256, productLiveCount}` defined in 0.35 — its writer is 0.35's hub step
   (O91) — and every later receipt in the chain validates and carries it forward unchanged.**

**1.4 Roles by capability.** Falsifiers that need a commit/push are authored by the hub or a Claude `-AllowEdits` lane; `sol`
verifies immutable heads and dispatcher-exported run/check JSON (a read-only sandbox can neither fetch nor call `gh`; O55).
Codex Desktop is not in the topology. Never ask the owner to click.

**1.5 Spend.** `-DailyBudget` from charged reservations (0.35); kill switch `$D\WORKSTREAM-LOOP-DISABLED`. No card needs Fable.

---

## PHASE 0 — BUILD THE ABILITY TO RUN ONE PRODUCT LANE UNATTENDED AND SAFELY, AND SEED THE BACKLOG. Every precondition is a file; every file names its writer.

**0.0 Ratify this rev (§RATIFY).** Nothing below runs until both keys return PROCEED or PROCEED_WITH_AMENDMENTS with no blocker
standing. (Exception already taken: the kill switch, 1.2.)

**0.05 HUB PROCEDURE — author the project hook and its falsifier suite** (`prompts\v2\card-TOOL-HOOK-ENFORCE-1.md`). On branch
`plan/definitive-fix-v7` from `fork/master`, the hub writes `tools/hooks/mlv-never-authorized.py` (fail-closed PreToolUse gate,
rules NA-1..NA-10 per the register), `tools/repo_hygiene/test_mlv_never_authorized.py` (**every case inside a `unittest.TestCase` subclass — the repo-hygiene job runs
`unittest discover`, under which a module-level `def test_*` table collects ZERO and exits green, O79's failure mode in the one suite that
proves enforcement; acceptance asserts the runner's `Ran N tests` line with N ≥ register rows + controls and is RED at N = 0 — O86;**
one DENY per row, the 3 controls, the 28
known falsifiers, 4 fail-closed inputs, the NA-4 same-basename case, the receipt-create ALLOW and receipt-shrink DENY pair, the
canonical 0.4b body ALLOW/DENY pair, 6 benign ALLOWs; the suite fails if any row has zero DENY cases; collected by discovery), and
the hook entry in `.claude/settings.json` — `"C:/Users/obabalola/AppData/Local/Python/bin/python.exe" "${CLAUDE_PROJECT_DIR}/tools/hooks/mlv-never-authorized.py" --project-dir "${CLAUDE_PROJECT_DIR}"`,
matcher `Bash|PowerShell|Write|Edit|NotebookEdit`, the exact pattern the file already uses for its three existing hooks (S54). **The hub
also APPENDS the first row of `$D\receipts\required-checks-live.jsonl` — one complete normalised required-check snapshot per line; the
live required-context set from `gh api repos/layibabalola/MLV-App/branches/
master/protection` (the hub's session has `gh`; a lane's does not, and the hook never calls it) — which is NA-9's input: the hook reads its LAST non-empty row
via `MLV_REQUIRED_CHECKS_SNAPSHOT`, defaulting to that path, fails closed on any malformed row, and DENIES every protection mutation, add-only bodies included, while the file
is absent or unparseable; 0.4b-ii's `set-required-checks.ps1` APPENDS its validated post-PATCH row and never replaces an earlier one (O92/S84). NA-2's carve-out
allows both appends.**
Runs the test locally; **one acceptance case extracts the registered command string from `.claude/settings.json`, executes it
verbatim from a disposable worktree with one benign ALLOW and one harmless DENY payload, and asserts the exit codes** — the
registration is proven, not assumed. **That case lives in `tools/hooks/test_registration_path_local.py`, which the repo-hygiene
discovery pattern does not collect: it names a per-user absolute interpreter that exists on no hosted runner (O81). It is a LOCAL
gate the hub runs on the branch and again in 0.1 after the conflict resolution. Hosted CI proves the RULES: every case in
`test_mlv_never_authorized.py` uses `sys.executable` and parameterised, path-normalised fixtures (board root and the `bachelor`
UNC path are parameters, never literals), so the table is green on both matrix legs; the registration path itself is proven by
`hookWiredProof` in 0.1.** **No write touches `~/.claude/`.** The receipt is written in 0.1 after the merge.

**0.1 Land the tracked half: hook + mirror + Invoke-Lane guard; then move the canonical checkout.** Same branch: the 0.05 files;
`docs/definitive-fix-plan-20260906.md`, `docs/lane-prompts/v2/*`, `docs/never-authorized.json`, `docs/Start-EditingLane.ps1`
(mirror); and a hub-authored patch to `tools/coordination/Invoke-Lane.ps1`: (a) throw `codex-lane-never-edits` when `-AllowEdits`
is combined with `-Lane sol|luna`, before any process; (b) `-AllowedTools <list>`, **required** with `-AllowEdits` (`ALL` never
granted; else `allowlist-required`); (c) `-ExtraReadDir`; (d) receipt fields `allowEdits`, `lane`, `allowedTools`, `workDir`,
`baseSha`, `hookSha256`; (e) **with `-AllowEdits`, load `$D\receipts\0.05-hook-enforced.json`, hash `<WorkDir>\tools\hooks\
mlv-never-authorized.py`, and throw `hook-not-enforced` on a missing/drifted receipt BEFORE creating a run directory** (the
worktree's copy is what governs that lane). Branch `plan/definitive-fix-v7` was cut from `fork/master` in 0.05; the hub now cherry-picks onto it local master's two unpushed
commits `44c04f08` and `e546ea11` (session-checkpoint hooks), resolves the `.claude/settings.json` conflict once so the merged
file carries those two hooks AND the new PreToolUse entry, and RE-RUNS 0.05's registration-path acceptance case against the
resolved file before opening the PR (O76). PR via
`gh pr create -R layibabalola/MLV-App` (confirmed by `gh pr view <n> -R layibabalola/MLV-App` right after); `sol` reviews from the hub's pre-dispatch export (S108); CI green (the hook test runs there); merge. **Then the hub moves the
canonical checkout WITHOUT losing anything (S55):** `git -C $R status --porcelain` must be empty (it is); `git branch
preserve/local-master-20260906 master` (the two commits stay reachable until the merge proves they landed); `git fetch fork
master; git switch --detach fork/master`. **A Claude session loads project hooks at start, so the hub's CURRENT session does
not gain the hook mid-flight: the hub RESTARTS its session in `$R` before writing the receipt, attempts ONE known-DENY act in
the restarted session (the INERT payload `if ($false) { setx ANTHROPIC_PROBE_TOKEN x }` — it exercises the `ANTHROPIC_` prefix rule, names no real credential, and an UNWIRED hook merely executes the false branch without touching the environment; S73), and records the hook's stderr line as `hookWiredProof` (O62/O66) — this is
the registration-path proof, distinct from the CI subprocess test; a receipt without `hookWiredProof` does not satisfy 0.15.** **Measured in
round 8 (O87): project hooks load, fire and resolve `${CLAUDE_PROJECT_DIR}` PER WORKTREE, even in a directory with no `hasTrustDialogAccepted`
entry — derive it, never quote it: `Get-Content C:\mlvtmp\ws-driver\.claude-state\doc-size-runs.log` shows that worktree's own Stop-hook traces
with worktree-sized `governed=` counts (below the board root's own log — both move; derive them, never carry the numbers — O99), and
`~/.claude.json` `projects` holds no `mlvtmp` key. **And `--settings` MERGES with project settings rather than replacing them (hub-measured,
round 10): every `claude` lane receipt whose `workDir` is `C:\mlvtmp\ws-driver` carries `authority.bulkReads: DENIED` with five `denyRules`
— each was launched with the run-dir `--settings` file — and that worktree's Stop hook still fired minutes after each; derive: the receipts'
`startedUtc` against the log's stamps.** What `hookWiredProof` still has to establish is the NEW `PreToolUse` key, which no hook in this repo
has used before — the event-key class that failed silent on this machine on 2026-08-09 — and it establishes it in the hub's INTERACTIVE
session only; **the lane venue (headless `claude -p` with `--settings` and `acceptEdits` in a worktree) is proven by 0.15's `HOOK-FIRED:`
line, and the wrapper refuses `hook-unregistered` when the worktree's `.claude/settings.json` lacks the hook's `PreToolUse` entry — a hook
is (interpreter × script × REGISTRATION), and hashing the script proves only the middle term (O98).** The hub then writes
`$D\receipts\0.05-hook-enforced.json` = `{hookSha256, hookTestSha256, wrapperSha256, mergeSha, canonicalHeadSha, testRunUrl,
casesDeny, casesAllow, solVerdictPath, hookWiredProof, divergentLocalMaster}` (S68) and `execution-control-0.1.json`. **Acceptance (table-driven pytest cases in
`test_coordination_guardrails.py`, run with `python -m pytest`, with an EXISTING prompt file — each case sets `MLV_BOARD_ROOT` to a tmp-dir
board fixture carrying its own `receipts\0.05-hook-enforced.json`, so the table is host-independent and green on the hosted runner that
collects it, and no case references a real `.claude-state\` path — O97/O107):** `-Lane sol -AllowEdits` and `-Lane luna -AllowEdits` both exit
non-zero printing `codex-lane-never-edits`; `-Lane sonnet -AllowEdits -AllowedTools Read` with the receipt absent, and with its
`hookSha256` altered, both print `hook-not-enforced`; `-AllowEdits` without `-AllowedTools` prints `allowlist-required`; a worktree whose `Invoke-Lane.ps1` lacks
`-ExtraReadDir` is refused `invoke-lane-stale` by the wrapper (O83); a worktree whose `.claude/settings.json` lacks the hook's `PreToolUse` entry
(matcher `Bash|PowerShell|Write|Edit|NotebookEdit`, command naming `tools/hooks/mlv-never-authorized.py`) is refused `hook-unregistered` (O98); an otherwise-correct registration whose interpreter is `C:/definitely-missing/python.exe` is refused
`hook-unregistered` too — the check is an EXACT match on the one pinned command, never a substring (S89); an entry carrying that exact command under
`type: prompt` is refused too — registration is matcher, `type: command` AND the exact command (S92); a lowercase matcher with the exact type and
command is refused too — the matcher comparison is case-sensitive (S94); the
falsifier `-Lane sonnet -AllowEdits -AllowedTools Read` with a valid receipt proceeds to the run-directory step.

**0.15 TOOL-GH-PROBE-1 — can a lane push, open a PR, and read the board root?** First `-AllowEdits` lane: hub creates
`C:\mlvtmp\lane-TOOL-GH-PROBE-1-<ts>` from `fork/master` (post-0.1), runs `$D\Start-EditingLane.ps1 -Lane sonnet -Card
TOOL-GH-PROBE-1 -PromptFile $D\prompts\v2\card-TOOL-GH-PROBE-1.md -WorkDir <that> -TimeoutSec 300`. The wrapper resolves
`Invoke-Lane.ps1` and the hook FROM THAT WORKTREE and refuses `invoke-lane-stale` if `-AllowedTools` is absent there (no
exemption for the probe). The card carries no placeholders and prints THREE last lines: `HOOK-FIRED: <the project hook's stderr line>|none` — from ONE inert known-DENY payload
(`if ($false) { setx ANTHROPIC_PROBE_TOKEN x }`) attempted inside the lane, the venue that actually runs (O98) — then `BOARD-ROOT-READ: ok|denied` (O61) and `GH-CAPABILITY: …`, in exactly
that order: the card is the deliverable and the hub parses the three positionally (O52a/O115). Outcome → `$D\lane-gh-capability.json`, which records all three; **`HOOK-FIRED: none` blocks 0.35 until the
registration path is proven in the lane venue**; the dispatcher's `PR_STEP` and the verify-first convention follow from it
(if `denied`, the dispatcher exports `git show {{BASE_SHA}}:<path>` results into `{{RUNDIR}}` for the cards that need them).

**0.18 SEED THE PRODUCT BACKLOG (hub).** Precondition: `$D\WORKSTREAM-LOOP-DISABLED` exists (created 06:36Z) and the newest cycle
receipt carries a non-null `haltedReason` — **the loop stays halted until 0.2, the LAST step, re-installs it** (O49/S56). Write the
**15 lane-dispatchable cards of the PHASE 1 table** — every row that has a file in `$D\prompts\v2\` — into `queue.json` with
`kind` (explicit: `product` or `playback`), **`track` = `kind`** (the dispatcher SELECTS by `track`: `Get-Track` at
`Invoke-Workstream.ps1:136-140` reads only that property and maps an absent one to `UNSET`, and `-Track` filters the pool through it at
`:291-292`, so a card carrying `kind` alone is invisible to `-Track product` — S82), **`scope` = the card's column-0 `ALLOWED_PATHS:` line — present exactly once in all fifteen dispatchable prompt files: the twelve
`fields-*.md` always carried it and the three FULL cards (`card-PROD-TLS-VERIFY-1.md`, `card-PROD-CLAUDEMD-TRUTH.md`,
`card-HYG-PROFILING-DNGS-1.md`) gained one in rev 11, derived from each card's own Deliverable and STOP lines, the file being the
deliverable (O52a/O93)** (so 0.5's derivation has an input and
never defaults them — O78), `owner: sonnet`, **`state: queued`** (S41), `procedure` = the `$D\prompts\v2` path, `procedureSha256` from the manifest,
`dependsOn`. **`USECASE-1` is NOT seeded** (no procedure); it gets `state: closure-receipt-only` so no track selects it. Write
`docs/roadmap.md` (≤ 4 KB mirror), `tools/coordination/check_roadmap_queue_parity.py` and `tools/repo_hygiene/
test_roadmap_queue_parity.py` **on a fresh branch from `fork/master`; PR; `sol` review; green CI; merge; write (O174: the row has no merge-sha key and nothing consumes one)
`$D\receipts\0.18-roadmap-parity.json` = `{recordedUtc, queueArmResultSha256, composedPromptPath, prChecksPath, prReviewPath, solVerdictPath}` (S62; the four review fields for its own PR's
review, exported by the hub — S108; the schema the hook enforces — S118). `queue.json` stays the hub's separate machine-local write.** **`test_roadmap_queue_parity.py`
asserts ONLY over tracked artifacts — `docs/roadmap.md` against `docs/lane-prompts/v2/*`, the mirror 0.1 lands — because `.claude-state/` is
gitignored (`.gitignore:52`) and absent from every hosted checkout (O97/O106). The queue arm (15 cards, explicit `kind`, `track == kind`,
`scope`) is `tools/coordination/check_roadmap_queue_parity.py --queue <path>`, run by the hub LOCALLY on the branch; its stdout sha256 is
recorded in `0.18-roadmap-parity.json` as `queueArmResultSha256`. No case in any `tools/repo_hygiene/test_*.py` this plan creates may
reference a path under `.claude-state\`.** Acceptance: parity
test green in CI; the queue holds 15 `queued` product/playback cards, each carrying explicit `kind`, `track == kind` (asserted by the LOCAL queue arm, `check_roadmap_queue_parity.py` — S82/O106) and
`scope` (O78), and `grep -c '^ALLOWED_PATHS:'` is 1 in every one of the fifteen dispatchable prompt files (O93); the loop is still halted (a dry run shows them selectable;
`lane=sonnet` resolution arrives with 0.35).

**0.35 TOOL-LOOP-PLUMBING-1** (`prompts\v2\card-TOOL-LOOP-PLUMBING-1.md`; **discharges former step 0.3 — lane resolution by
`kind` + `owner` is one of its deliverables and tests, S63**). **Bootstrap composition (S66): the composer is this card's own
deliverable, so the hub composes THIS card by hand — replaces the placeholders that card actually carries, `{{WORKDIR}}`, `{{BASE_SHA}}` and
`{{PR_STEP}}` (from `lane-gh-capability.json` — PR_STEP has exactly TWO ratified values and the composer selects on `GH-CAPABILITY` alone:
for `lane-can-open-pr` it is `gh pr create -R layibabalola/MLV-App --head {{BRANCH}} --title "<card id>: <subject>" --body "<what, why, red run,
green run>"; then print PR-OPENED: <number> as your last line.`; for `gh-unavailable` or `push-unavailable` it is `Do NOT call gh. Print PUSHED:
{{BRANCH}} <head sha> as your last line; the dispatcher opens the PR.`; a third value is a ratified amendment, never a composer choice, and a card
composed with an empty PR_STEP is refused, never blanked — O113/O153. Both PR_STEP values carry a nested `{{BRANCH}}`, so composition is a
FIXED POINT, not one pass: after injecting PR_STEP the composer re-substitutes every field placeholder and only then asserts no `{{` remains.
`card-TOOL-LOOP-PLUMBING-1.md` names its branch as the literal `tool/TOOL-LOOP-PLUMBING-1` in its own text, and its own bytes now carry NO `{{BRANCH}}` at all
(O166 turned the last one, a quoted word in its composer spec, into prose — O173), so the token appears only inside the PR_STEP literal after
injection, which is why composition must be a fixed point rather than one pass — so for THIS
card the hub substitutes `{{BRANCH}}` = `tool/TOOL-LOOP-PLUMBING-1`, never the contract default `product/<CARD_ID>`, which would make the card
create one branch and name another (O157; hub-measured: the three named substitutions leave `{{BRANCH}}` on both values); derive: `grep -oE '\{\{[A-Z_]+\}\}' card-TOOL-LOOP-PLUMBING-1.md | sort -u` — the permanent
composer substitutes `{{RUNDIR}}` and `{{TS}}` for the cards that carry them, O120) in `card-TOOL-LOOP-PLUMBING-1.md`, writes `{{RUNDIR}}\TOOL-LOOP-PLUMBING-1-composed.md`,
asserts no `{{` remains, and passes THAT path to `Start-EditingLane.ps1`. The permanent composer is the tracked file
`tools/coordination/Compose-LanePrompt.ps1`, called by `Invoke-Workstream.ps1` and hashed in every execution-control receipt from 0.35
onward — it does not exist before 0.35 (S80).**
Deliverables: loop/dispatcher `-Lane`/`-AllowEdits`; every editing
dispatch goes through the wrapper; refusal vocabulary (emitter in brackets, O68): `codex-lane-never-edits` [Invoke-Lane, wrapper,
dispatcher]; `hook-not-enforced` / `allowlist-required` [Invoke-Lane]; `invoke-lane-stale` / `workdir-missing` /
`hook-receipt-missing` / `hook-missing` / `hook-drifted` / `hook-wire-unproven` / `hook-unregistered` / `prompt-missing` / `clip-line-missing`, `workdir-is-board-root` (a lane is never rooted at the board — O124) [wrapper];
`procedure-missing-or-drifted` / `unknown-field` [dispatcher]; worktree per dispatch from `fork/master` at dispatch time (`baseSha` in the receipt);
`-ExtraReadDir $RunDir`; deterministic composition of fields + TEMPLATE; **before any dispatch the hub/dispatcher refreshes
`origin` and `fork` and writes `{{RUNDIR}}\dependencies.json` (queue states, receipt paths the card names) and, for review lanes,
`{{RUNDIR}}\pr-<n>-checks.json` from `gh pr checks -R layibabalola/MLV-App` and `{{RUNDIR}}\pr-<n>-review.json` from `gh pr view -R layibabalola/MLV-App --json number,headRefOid,body,state` AND `gh api repos/layibabalola/MLV-App/branches/master/protection`, both read before and after the checks, carrying `headRefOidBefore`, `headRefOidAfter`, `requiredContextsBefore`, `requiredContextsAfter`, `body`, the checks and `retrievedUtc`, and refusing on head or required-context drift (O53/O54/O55/S114/S121)**; mutex, kill-switch re-check, reserve-before-start — **reservation events are APPENDED, never updated in place, to
`$D\receipts\dispatch-reservations.jsonl`: start appends `{reservationId, state:"reserved", card, kind, lane, recordedUtc}`,
completion appends a second row with the same `reservationId` and `state: charged|refunded`; `Invoke-WorkstreamLoop.ps1` reads
this exact file for `spentToday` (S76; under `$D\receipts\**`, so NA-2's carve-out allows the appends).** **Tests in
`test_coordination_guardrails.py` — a pytest suite (50 module-level `def test_*`, no `unittest.TestCase`; run by
`python -m pytest tools/coordination/test_coordination_guardrails.py -q`, never by the unittest runner, which collects nothing
and exits green — O79); it stays in the Windows-only bridge job until 0.4c-i moves it, and no `tools/repo_hygiene` bridge is
added.** Merge;
`execution-control-0.35.json`, **which ADDITIONALLY records the provenance block `{roadmapParityReceiptSha256` (sha256 of
`$D\receipts\0.18-roadmap-parity.json` as it stands at 0.35's merge)`, queueSha256` (sha256 of `$D\queue.json` at the same moment)`,
productLiveCount` (non-frozen `queue.json` cards with `kind ∈ {product, playback}` and `track == kind`, expected 15)`}` — the three fields
0.6 validates and carries forward and 0.2 and the hook's NA-2 exception require; its WRITER is this step's hub act, and a receipt without
them does not satisfy 0.6 (S67/S81/O91).** **The loop stays halted through 0.4 and 0.6; 0.2 is the LAST step of Phase 0 (S56).** Until
then the hub may start individual product lanes by hand through the wrapper, which needs no loop.

**0.4 CI: required checks compile the product; skip is never green. Order (O51): a-i → a-ii → c-i → b-i → b-ii → c-ii.**
- **0.4a-i land the job.** PR adding `Batch Compile` to `tests.yml` (builds `platform/qt/MLVApp.pro` on the oracle tuple) **and, IN THE SAME PR, updating
  `tools/repo_hygiene/test_repo_hygiene.py`, which runs in the REQUIRED `Repo Hygiene Python` contexts and pins this workflow four ways
  (`git show 55c486d4:tools/repo_hygiene/test_repo_hygiene.py | sed -n '961,983p;1013p'`): a SET EQUALITY over `tests.yml`'s job ids against
  `expected_timeouts`, a per-job `expected_timeouts[job]` lookup that raises `KeyError` on an unlisted job, and a POSITIONAL
  `expected_remote_uses` list whose `* 5` multiplier is the job count — so the PR adds `"batch-compile": <timeout>` to `expected_timeouts`
  and one `actions/checkout` + one `actions/setup-python` pair to `expected_remote_uses`, derived by running the suite on the branch, never
  by editing numbers to match (O94; the sibling of retraction #14: the job ADDED, its own assertion set unread). MEASURED by the hub on the
  scratch extraction (O100): adding a `batch-compile` job with a pinned checkout and setup-python turns RED both
  `test_ci_workflow_hardening_is_fail_closed_and_coordination_aware` and `test_python_ci_dependencies_are_exact_hash_locked_and_reproducible`
  (it pins `workflow.count('python-version-file: ".python-version"')`, the install-line count and the `pip check` count); and — MEASURED by the Claude key
  in a git-live scratch venue (round 12): it DOES break here and in 0.4c-ii — `test_all_tracked_workflow_actions_are_immutably_pinned_and_inventoried`'s
  repo-wide `expected_remote_inventory` Counter and its `credential_isolated_checkout_sites == checkout count` assertion: the new job's checkout
  carries `with: persist-credentials: false`, every remote action its `# vN` comment. Run the suite on the branch; fix every failure it names.** Merge sha
  = `WORKFLOW_BASE` → `$D\receipts\0.4a-workflow-base.json`.
- **0.4a-ii falsify from the merged head.** Hub cuts `falsify/batch-compile-<ts>` from `WORKFLOW_BASE` with a deliberate syntax
  error in `BatchRunner.cpp`, **pushes the branch and starts the run explicitly with `gh workflow run tests.yml -R layibabalola/MLV-App --ref falsify/batch-compile-<ts>`
  — `tests.yml` triggers on `workflow_dispatch`, `pull_request` and `push` to `master` ONLY (derive: `git show <WORKFLOW_BASE>:.github/workflows/
  tests.yml | sed -n '3,8p'`), so pushing a falsify branch alone fires nothing; that dispatched run is `runUrl` (O110)**; `Batch Compile` goes RED → `$D\receipts\0.4a-batch-compile-falsifier.json` = `{workflowBaseSha,
  headSha, runUrl, failingContext:"Batch Compile", conclusion:"failure", recordedUtc}`; `sol` verifies from the export; branch deleted.
- **0.4c-i move the guardrail step; create the demotion script.** PR moving the `test_coordination_guardrails.py` step into the
  `repo-hygiene-python` job **gated `if: runner.os == 'Windows'` with `shell: pwsh`** — the suite invokes the Windows-only
  `pwsh.exe` at two unguarded call sites and carries no platform guards, so the ubuntu leg of that two-OS matrix cannot go
  green without a port that is out of scope here (O71) — **AND, in the SAME PR, adding `pytest` to `.github/requirements/repo-hygiene.in`
  and regenerating the hashed `repo-hygiene.txt` with `tools/dependencies/update-python-locks.ps1` (its `-Check` mode at `tests.yml:113`
  verifies the lock in CI): `repo-hygiene-python` installs only `pip.txt` + `repo-hygiene.txt` with `--require-hashes` then `pip check`,
  `pytest` appears in none of the nine `.github/requirements/*` files and reaches CI today only through `tools/agent-bridge/
  requirements-test.txt` in the bridge job, so the moved step would fail at `python -m pytest` before collecting anything (O85) —**
  AND adding `tools/coordination/demote-factory-bridge.ps1` (parses and
  validates two receipts, refuses before touching any workflow); **AND, in the SAME PR, updating `tools/repo_hygiene/test_repo_hygiene.py`,
  which runs in the REQUIRED `Repo Hygiene Python` contexts and asserts the guardrail step's HOME: MEASURED by the hub on a scratch
  extraction of DIAGNOSIS_BASE (`git archive 55c486d4 | tar -x`, then `python -m pytest tools/repo_hygiene/test_repo_hygiene.py -q` before and
  after the move), moving the step alone turns `test_ci_workflow_hardening_is_fail_closed_and_coordination_aware` RED — its `assertIn`s at
  lines 1087-1089 require the step's name, `tools\\coordination\\test_coordination_guardrails.py` and `tests\\coordination` inside the
  `factory-bridge-regressions` job slice; re-point them at the `repo-hygiene-python` slice. The rule is the RUN, not the list: derive the full
  breakage set by running that suite on the branch and fixing every failure it names — line numbers here are illustrations, never the pin set
  (O100; without this the PR cannot merge, `0.4c-guardrail-move.json` is never written, and NA-9's single exception never opens)**; `sol` reviews;
  merge; the green run of the moved step on the
  Windows leg → `$D\receipts\0.4c-guardrail-move.json` = `{headSha, runUrls[1], requiredJobs:["Repo Hygiene Python
  (windows-latest)"], conclusion:"success", collectedTests}` (that context is already required, so 0.4b-ii's canonical body is unchanged; **`collectedTests`
  is the step's own `collected` count, DERIVED at `headSha` by `python -m pytest --collect-only -q <the step's exact target list> | tail -1`
  in a checkout at that sha — never by grepping `^def test_`: the step's second target is the DIRECTORY `tests\coordination`
  (`git ls-tree -r --name-only 55c486d4 tests/coordination` lists seven files, six of them `unittest.TestCase` subclasses whose indented
  methods pytest collects and a column-0 grep cannot see — O89). Acceptance: `collectedTests > 0`, equal to that `--collect-only` figure, and
  `passed + skipped == collectedTests` in the run log; O79's vacuous-green guard is the non-zero arm, never an equality against a grep**);
  `execution-control-0.4c-i.json`. If the hub later ports the suite, a separate PR proves the ubuntu leg green first.
- **0.4b-i create the gate script.** PR adding `tools/coordination/set-required-checks.ps1` + unit tests that it refuses on a
  missing/mismatched 0.4a-ii receipt AND on a missing 0.4c-i receipt (S44/O51); `sol` reviews; merge; `execution-control-0.4b-i.json`.
- **0.4b-ii branch protection, receipt-gated.** The script PATCHes the canonical five-context body (`--input -`, JSON boolean
  `strict`, every `app_id 15368`), re-GETs, asserts, writes `$D\receipts\0.4b-required-checks.json` = `{recordedUtc, headSha, preContexts, postContexts, snapshotRowSha256}` (the single NA-9 exception; the schema the
hook enforces — S118) **and APPENDS the validated post-PATCH row to
  `$D\receipts\required-checks-live.jsonl` — never a rewrite: the post-transition object is SHORTER (`Batch Compile` replaces `Factory Bridge
  Regressions`), so an in-place rewrite would be the shrinking overwrite NA-2 denies under `$D\receipts\**`; acceptance asserts the
  pre-transition row is byte-identical and the file grew (O92/S84)**. **The accompanying PR also updates the `required_checks` tuple in
  `tools/repo_hygiene/test_repo_hygiene.py` (line 744 at BASE) and the list under "The protected branch currently requires exactly these hosted
  checks:" in `CONTRIBUTING.md:17-23` to the canonical five — the two are asserted EQUAL as an ordered tuple, and each context must have a
  matching `name:` in `tests.yml` (O94).**
- **0.4c-ii demote the bridge job through the script:** requires `0.4c-guardrail-move.json` valid AND `0.4b-required-checks.json`
  showing no `Factory Bridge Regressions`; the script moves the bridge + profiling suites to `.github/workflows/factory-bridge.yml`
  (every PR, **not required, never `continue-on-error`**; red = weekly-digest debt) **on a fresh branch — the SAME PR removes
  `factory-bridge-regressions` from `test_repo_hygiene.py`'s `expected_timeouts` and one checkout/setup-python pair from `expected_remote_uses`,
  and sha-pins and inventories `factory-bridge.yml`'s actions, because `test_all_tracked_workflow_actions_are_immutably_pinned_and_inventoried`
  globs `.github/workflows/*.yml` (O94). MEASURED by the hub on the scratch extraction (O100): deleting the `factory-bridge-regressions` job
  turns RED five tests — `test_all_tracked_workflow_actions_are_immutably_pinned_and_inventoried` (visible only in a git-LIVE checkout: that test
  enumerates TRACKED workflows and is already red without `.git`, so a `git archive` extraction cannot show it as a delta — measure on
  `git clone --no-checkout` + `checkout <sha>`, never on an extraction; O112), `test_ci_product_oracles_are_isolated_from_factory_bridge_failures`,
  `test_ci_workflow_hardening_is_fail_closed_and_coordination_aware`, `test_contributor_governance_routes_stay_synchronized` and
  `test_python_ci_dependencies_are_exact_hash_locked_and_reproducible` — with `workflow.index("\n  factory-bridge-regressions:")` raising
  `ValueError` (an ERROR, not an assertion) at its site in `test_ci_product_oracles_are_isolated_from_factory_bridge_failures` — its second site,
  inside `test_ci_workflow_hardening_is_fail_closed_and_coordination_aware`, is not reached in the same run because that test dies earlier at the
  set-equality over `tests.yml`'s job ids, and the `ValueError` surfaces only after `expected_timeouts` is re-pointed: one more reason the rule
  is the RUN, iterated to green, never a one-pass list (O116); two of those five appeared in no key's reading, which is why the rule is the run:
  the same PR re-points every one of them and fixes every failure the suite names on the branch; PR; `sol` review; green
  CI; merge; `$D\receipts\0.4c-demoted.json` = `{recordedUtc, headSha, mergeSha, runUrl, solVerdictPath}` (S65).**

**0.5 Freeze factory-kind cards; archive nothing.** `tools/coordination/freeze-factory-cards.py` (created, tested, landed by
reviewed PR — S72) derives `kind` for every card that does NOT already carry one — **a card carrying `kind` and `owner: sonnet` (the 15 seeded by
0.18) is never re-derived and never frozen** (O78) — from the paths its `scope` names; **no `scope` or
no path → `kind: factory` (fail-closed), except the six keep-live cards — exactly `USECASE-1`, `MEASURE-STRATEGY-1`, `VENUE-NOISE-1`, `C2-SUBMIT-2`, `C2-PROV-1`,
`C2-TELEM-2`, asserted as that exact set by a deterministic test (S77) — → `kind: playback`; print every scope-less id.** Freeze
every non-terminal `factory`-kind card (`state: frozen-factory-20260906`). Idempotent, `--dry-run`. Writes
`$D\receipts\0.5-factory-frozen.json` = `{recordedUtc, queueSha256, frozenCount, dryRunDiffSha256, scopelessIds}`. Accept: 0 dispatchable
factory-kind cards and the receipt.

**0.6 Ratio guard with a named caller (S64).** `tools/coordination/Test-ProductRatioGuard.ps1` computes `product_share_7d` and
`dispatches_per_landed_product_pr_7d` over `fork/master`, `%ct`-bucketed; RED under 50% / above 4. **Caller:** `Invoke-Workstream.ps1`
invokes it immediately before every dispatch and refuses any dispatch whose `kind` is not `product`/`playback` while RED, and
`freeze-factory-cards.py` refuses to unfreeze while RED. Deterministic pytest cases in `test_coordination_guardrails.py` (synthetic 60%
→ GREEN; synthetic 5% → RED), run with `python -m pytest`. Landed by reviewed PR; `$D\receipts\0.6-ratio-guard.json` = `{recordedUtc, mergeSha, firstReading, solVerdictPath}`, **then
`$D\receipts\execution-control-0.6.json` — this PR changes `Invoke-Workstream.ps1`, a file every execution-control receipt hashes, so
0.35's receipt is invalidated by §1.3's own rule and 0.6's supersedes it (S81).**
The heartbeat prints the same two numbers FIRST for humans. Accept: the receipt's `firstReading` is RED (5.5% at DIAGNOSIS_BASE).

**0.7 Land or close the open fork PRs — a RECEIPT-WRITING step when it lands a hashed file (O102).** Derived live 2026-09-06 (`gh api
repos/layibabalola/MLV-App/pulls/<n>/files --jq '.[].filename'`; re-derive at execution): **#71** ("refund a dispatch that failed and spent
nothing") touches `tools/coordination/Invoke-Workstream.ps1` and `tools/coordination/Invoke-WorkstreamLoop.ps1` — two of the paths §1.3
step 5's schema table hashes, and the two scripts 0.35 rewrites — so merging it invalidates `execution-control-0.6.json` by §1.3's own rule while 0.2 re-hashes
every file that receipt lists: merge post-0.6 `fork/master` INTO #71's existing head with a normal merge commit (a fast-forward push of
that branch — NEVER a rebase: its four commits already sit on top of DIAGNOSIS_BASE, and publishing a rewritten head to an existing fork branch
is the history rewrite NA-1 forbids, S93), resolve the two SCRIPTS in favour of 0.35's rewrite, and resolve `tools/coordination/test_coordination_guardrails.py` — the THIRD file the
live command returns, which 0.1, 0.35 and 0.6 all write into and to which #71 adds test cases — as a UNION: every `def test_*` on either side
survives, because dropping #71's cases is the test deletion NA-6 denies and would leave CI green with fewer tests; assert on the branch that
`python -m pytest tools/coordination/test_coordination_guardrails.py --collect-only -q | tail -1` is at least the count of distinct case names
across both sides (O111); merge if green; if that head cannot be
updated without rewriting history, open a replacement PR from a fresh branch off post-0.6 `fork/master` that re-applies its intent (refund on
zero spend) and CLOSE #71 as superseded; never force-push, never force-merge. **#72** ("pass the async-H2D playback frame id explicitly") touches
`tools/repo_hygiene/test_repo_hygiene.py`, the file all three 0.4 steps rewrite: merge post-0.4 `fork/master` INTO #72's existing head with a normal merge commit (never a
rebase — S93), resolve that file against
the pins 0.4a-i/0.4c-i/0.4c-ii re-derived (run the suite on the branch; the run is the derivation), CI, `sol` review, merge; if the head cannot be
updated without rewriting history, a replacement PR from a fresh branch re-applies its intent and #72 is closed as superseded. **#73** ("cover tools/profiling
too") must be RE-TARGETED before merge: its only `tests.yml` change is one step at hunk `@@ -189` — inside the `factory-bridge-regressions` job,
lines 117-191 at BASE — which 0.4c-ii moved to `.github/workflows/factory-bridge.yml`; land the step there, sha-pinned and inventoried like the
rest of that file (O103). **Its other two files must be read, not assumed: `tools/session-checkpoint.py` is an ADD (223 lines) of a path 0.1
already added at 110 lines via the cherry-picked `44c04f08`, and neither preserved commit is an ancestor of #73's head, so git reports an add/add
conflict — resolve it in favour of #73's version (it supersedes rather than strands `44c04f08`'s intent; S55 is satisfied by that commit's
`.claude/settings.json` hunk, which 0.1 merged) and record the chosen blob in 0.7's receipt; `tools/agent-bridge/test_server_wrapper_phase2.py`
is shared with #76 and needs no rule because #73's branch already contains #76's commit (O119).** **#76** ("scale the agent-bridge wait budgets on CI"): merge if green. #5, #8: close unless green and < 50 lines.
**After the LAST merge in this step, if any landed PR touched a file §1.3 step 5 hashes, the hub writes `$D\receipts\execution-control-0.7.json`
over the same file set at the new `fork/master` head — carrying forward 0.6's `{roadmapParityReceiptSha256, queueSha256, productLiveCount: 15}`
after re-running 0.6's ratio-guard pytest cases against the merged scripts (`firstReading` stays in `0.6-ratio-guard.json` only — the control row
defines no such key, S130) — recording `reviewedHeadSha` = the head sha of the LAST PR this
step merged that touched a hashed path, `mergeSha` = the new `fork/master` head the hashes are taken at, and `solVerdictPath` = that PR's own sol
APPROVE; every PR this step merges gets a composed sol review before merge, #71, #73 and #76 included, and the step refuses to write the receipt
if the triggering PR has no APPROVE at its head (O165); if none did, no receipt is written and
0.6's stands (O102).**

**0.9 Retire seat-era checkpoints and prompts — COPY, verify, then shrink; never move (O65).** For each of
`$D\{product,fable,claude,codex,fleet,opus,claude-impl,sol}-resume-CURRENT.md` (eight — `codex` was unlisted, O109; `orchestrator` is
kept, §1.3 step 4 appends to it) and `$D\ignition\seat-*.md`: COPY verbatim to
`$R\.claude-state\continuity\archive\<name>-20260906.md` (a create, always allowed), verify the copy's sha256 equals the source,
record it in `$D\receipts\0.9-archive.json`, then replace the source with a 5-line stub naming the archive path and its sha256
(the shrinking overwrite NA-2 allows only when a byte-identical archive copy already exists).

**0.2 (LAST) Re-install the loop and lift the kill switch.** Preconditions, all files — each VALIDATED against its §1.3 step 5 RECEIPT SCHEMA, an
empty object or a forged execution-control name failing the gate closed (S118): `0.18-roadmap-parity.json`,
the NEWEST execution-control receipt — `execution-control-0.7.json` when 0.7 landed a PR touching a hashed file, else
`execution-control-0.6.json` (O102; 0.6 changed `Invoke-Workstream.ps1`, so 0.35's is invalid by §1.3's own rule, S81) — carrying forward `{roadmapParityReceiptSha256, queueSha256, productLiveCount: 15}` as PROVENANCE of the
queue at 0.35's merge — S67), `0.4b-required-checks.json`, `0.4c-demoted.json`, `0.6-ratio-guard.json`, `0.5-factory-frozen.json` (S78), **AND a LIVE derivation
at this step: `$D\queue.json` re-read now shows ≥ 15 non-frozen cards with `kind ∈ {product, playback}` AND `track == kind` (S82)** (0.5 rewrote the queue
after 0.35, so the receipt's hash cannot be re-matched and is not a precondition — O78). Then, **binding the install to the post-0.6 script (S83) — the canonical checkout was last moved in 0.1, whose loop script has neither
`-Lane` nor `-AllowEdits` (both arrive in 0.35), and `-Install` persists the invoking script's own `$PSCommandPath` into the scheduled task
(`Invoke-WorkstreamLoop.ps1:157` at DIAGNOSIS_BASE): the hub requires `$R` clean (`git -C $R status --porcelain` empty), runs
`git -C $R fetch fork master` and `git -C $R switch --detach fork/master`, asserts `git -C $R rev-parse HEAD` equals the fetched sha, and
re-hashes every file the NEWEST execution-control receipt (0.7's if written, else 0.6's — O102) lists in `$R` against that receipt; only then
it invokes the ABSOLUTE**
`$R\tools\coordination\Invoke-WorkstreamLoop.ps1 -Install -Tracks product,playback -Lane sonnet -AllowEdits` (a comma list: the persisted
action is a `pwsh -File` invocation whose arguments arrive as literal strings, and 0.35 makes the script serialise and re-parse `-Tracks` in
that form — O95); **ONE compound PowerShell tool input, of exactly the canonical shape `$ErrorActionPreference = 'Stop'; $r = '<json>'; Set-Content -LiteralPath '<receipt>' -Value $r -NoNewline -Encoding utf8 -ErrorAction Stop; if ((Get-Content -LiteralPath '<receipt>' -Raw -ErrorAction Stop) -cne $r) { throw 'enable-receipt-write-verification-failed' }; Remove-Item -LiteralPath '<marker>' -ErrorAction Stop` — CREATE FIRST, DELETE SECOND, FAIL-CLOSED: `$ErrorActionPreference = 'Stop'` as the first statement, `-ErrorAction Stop` on both acts and a
read-back verification between them — THREE arms. MEASURED on pwsh 7.6.5 over five cases, the leading `$ErrorActionPreference = 'Stop'` ALONE
fails closed in every class measured (read-only target, missing parent, directory target, unresolvable drive) — it is the arm the guarantee rests
on, and its absence is the one shape measured to fail OPEN (the drive case, which unbinds the FileSystem provider's dynamic parameters
`-NoNewline`/`-Encoding`/`-Raw` and `-ErrorAction` with them, so the delete runs; the hub's own S101 reproduction had used that shape and measured
the wrong property). `-ErrorAction Stop` on both acts and the read-back `throw` are RETAINED as defence in depth, not because either was measured load-bearing:
`-ErrorAction Stop` is MEASURED INERT in every class measured — in the rev-19 form, dropping it while keeping the read-back leaves all four outcomes
unchanged, and it alone leaves three of four open, including the access-denied case S101 added it for (hub re-measured before adopting: EA only →
marker deleted; read-back only → marker present). The read-back is the arm that carried the rev-19 form and the only arm that verifies the
receipt's CONTENT rather than the absence of an error. The contract is the pinned SHAPE, not this rationale: the hook denies a compound missing
any arm (S101/O128/O131/O136)** — writes
`$D\receipts\0.2-loop-enabled.json` = `{state:"enabling", enabledUtc, executionControlReceipt, executionControlSha256, recordedUtc}` — validated
SEMANTICALLY by the hook, not for presence: `executionControlReceipt` is the basename of the NEWEST valid execution-control receipt by `recordedUtc` — every
`recordedUtc` the hook reads or validates in the ONE pinned notation `YYYY-MM-DDTHH:MM:SSZ`, ordered by the parsed value, a non-conforming stamp or an
exact tie making the newest undecidable (O141) — `executionControlSha256` is the lowercase SHA-256 of that exact file (derived as `(Get-FileHash -Algorithm SHA256 -LiteralPath <receipt>).Hash.ToLowerInvariant()` — `Get-FileHash` returns uppercase and the
comparison is case-sensitive, the trap `Start-EditingLane.ps1` already lowercases both sides for (O145)),
`enabledUtc`/`recordedUtc` carry that same notation; any string in those fields unlocked the one-shot before this (S112) — and then
deletes `$D\WORKSTREAM-LOOP-DISABLED` — **the ONE delete NA-2 exempts, and only while those six receipt files exist and validate, the enable
receipt is ABSENT on disk, and the SAME tool input creates it with a JSON literal the hook extracts and validates BEFORE the delete; a delete-only
input, or a literal missing a field, is DENIED (O64/S74/S98/S99); `Invoke-WorkstreamLoop.ps1` tests presence with `Test-Path`, so a stub cannot
substitute for the delete.** **The hook recognises this canonical compound as ONE dedicated act — the enable — evaluated BEFORE its generic
NA-2 attribution runs: a text matcher that applied a command's verbs to every path it names would deny the compound itself, because `Remove-Item`
beside the receipt path reads as a delete under `$D\receipts\**` (O118, measured 8/8 against the pre-S99 hook). So the rule is: the exact
canonical shape → the enable act; A `Write` to exactly `$D\receipts\0.2-enable-preflight-input.json` is the PRE-FLIGHT ARTIFACT act — the SECOND dedicated act, evaluated
before generic content attribution: ALLOWED only at the board venue, only when the target is absent, and only when its complete content parses as
exactly one hook-stdin object whose `tool_name` is `PowerShell` and whose `command` is the semantically valid canonical enable compound for the
current six receipts; it writes evidence only and authorizes no delete; any other path, a worktree venue, an existing target, a malformed object or
a non-canonical command → DENY (S125; hub-measured on the tenth commit: the outer Write was ALLOWED by the receipts carve-out while this sentence
said DENY — the act pins the decision by rule, and the ELEVENTH hook commit implements it); any other input naming both paths → DENY; the delete verb naming the marker in any other input → DENY; and the
enable act is ALLOWED only when the hook's own `--project-dir` argument, normalised, equals `MLV_BOARD_ROOT` (O126) — a worktree value or a
missing/empty argument → DENY, because a
worktree lane's hook evaluates the same absolute board paths and would otherwise admit the compound once the six receipts exist, before the hub has
verified `$R` or installed the task (S105). The
compound's value is authorization-binding — the delete is authorized only inside the validated enable — not atomicity: two statements are as
interruptible as two tool calls, which is why the create comes first and interruption fails closed (S99 and O118 were CONTESTED; the hub
rejected the premise both shared, that the enable is decided by generic attribution at all, and recorded it in the round-16 receipt).** **PRE-FLIGHT (S104): before typing the enable the hub writes the EXACT hook-stdin JSON — `{"tool_name":"PowerShell","tool_input":{"command":"<the
final board-path compound>"}}` — to `$D\receipts\0.2-enable-preflight-input.json` with the `Write` tool (that Write IS the PRE-FLIGHT ARTIFACT act above — S125; if the file ALREADY EXISTS — a resumed hub step or a second attempt — the hub does NOT
rewrite it, since the act denies an existing target and NA-2 denies the delete: it READS the stored object, types the compound VERBATIM from it
including its `enabledUtc` and `recordedUtc`, and re-runs the DRYRUN probe on the stored file; if the six receipts have changed since it was written
the probe exits non-zero, and recovery is a hub-authored, ratified amendment naming the stuck artifact — never a delete — O178), records its sha256, and runs the LIVE board's
hook on it directly: `$env:MLV_HOOK_DRYRUN='1'; Get-Content -Raw -LiteralPath <that file> | & <pinned python> $R\tools\hooks\mlv-never-authorized.py --project-dir $R` under
the live/default `MLV_BOARD_ROOT` — NO fixture board: the hook derives the marker and receipt paths from `MLV_BOARD_ROOT`, so a scratch root can never
match the real input, and no step could write a fixture's nested tree under NA-7 anyway. The probe is safe because the hook is DECISION-ONLY — it
reads the receipts and the marker and writes, removes and creates nothing (the one-shot state lives on disk, written by the compound, never by the
hook; derive: `grep -nE 'open\([^)]*["'\''](w|a)|os\.remove|os\.unlink|mkdir' tools/hooks/mlv-never-authorized.py` → none), and `MLV_HOOK_DRYRUN=1`
prints the decision on stdout with the exit code unchanged. Proceed only on exit 0; record the input path, its sha256, the decision line and the exit
code in the post-enable rewrite, so the shape boundary the dedicated act draws is proven before the one-shot act rather than discovered by it
(round-17 Opus self-failure; S104).** If the input is interrupted after the create, the marker stays and the enable FAILS CLOSED; recovery is a hub-authored,
ratified amendment naming the stuck receipt — never a delete of it (NA-2), which NA-2 exception (iv) allows the hub to author (O124). After the first post-enable cycle the HUB reads and validates the enabling receipt, then rewrites it ONCE, WITH THE `Write` TOOL (never a shell cmdlet: a shell truncating write
to `$D\receipts\**` is denied even for a create and only the canonical compound is exempt — O125/O130), as a strictly LONGER complete JSON
object carrying the original fields plus `cycleReceiptPath` and `state:"enabled"`, and re-reads and validates the result — a non-shrinking overwrite,
which NA-2's receipt carve-out allows; appending to a complete JSON object cannot yield valid JSON, so for a JSON receipt "extend" means a longer
rewrite by a named actor (S102). **The enable is ONE-SHOT: once that receipt exists the delete exception is closed, and a re-armed marker cannot be
deleted again without a newly ratified authorization — the six receipts outlive the step they authorize, so they cannot be its only key (S98).**
Accept: the first post-enable cycle receipt (named by `cycleReceiptPath`) shows `lane=sonnet`, a `baseSha`, product live > 0,
no other track, and the ratio guard consulted; **and the scheduled task's action, re-read (`(Get-ScheduledTask MLV-WorkstreamLoop).Actions`),
names that verified `$R` path as its `-File` target AND its `Arguments` string contains `-Tracks product,playback`, `-Lane sonnet` and
`-AllowEdits` verbatim — the persisted VALUES, never the script's declared parameters: at DIAGNOSIS_BASE `-Install` builds its argument line
from five values and silently drops `-Tracks` (`Invoke-WorkstreamLoop.ps1:155-161`), and the live task's action today carries no `-Tracks` and
names a scratch worktree as its `-File` target (derive: `(Get-ScheduledTask MLV-WorkstreamLoop).Actions.Arguments`) — asserted BEFORE the
kill switch is deleted; and the first post-enable cycle receipt's `tracks` equals exactly `product, playback` (S83/O95)**. **0.7 (PR landings) and 0.9 (checkpoint retirement) are non-gating cleanup
steps: they are not members of 0.2's six-receipt enable gate (S78), but in this Phase 0 execution the hub completes both BEFORE 0.2 — 0.2 is
the final step, without exception (S87).**

---

## PHASE 1 — THE PRODUCT BACKLOG (Sonnet). Seeded by 0.18; never empty again.

**Standing rule:** a console-test acceptance names the `QT += core` header AND its `console_tests.pro` addition; a grep-style
acceptance names the `tools/repo_hygiene/test_*.py` file the card CREATES and lists it in `ALLOWED_PATHS`. **Every
lane-dispatchable card has a prompt or fields file in `$D\prompts\v2\`, in the manifest; the dispatcher refuses one that does
not. Where a card or fields file and this table disagree, the FILE is the deliverable.** Cards read nothing outside their
worktree and `{{RUNDIR}}`; live facts they need arrive as dispatcher exports; **every verify-first command runs `git -C .`**
(a worktree shares the repository's object store, so `{{BASE_SHA}}` resolves there) — S69.

| P | card | file | deliverable (short) | acceptance (hosted) |
|---|---|---|---|---|
| 1 | **PROD-TLS-VERIFY-1** | `card-PROD-TLS-VERIFY-1.md` | delete `VerifyNone`; `FpmNameValidator.h` in `saveFileName`; `AtomicFileReplace.h` in `saveToDisk` | NEW hygiene grep test; console tests in `.pro`; grep binds call sites |
| 1b | **PROD-TLS-VERIFY-1b** | `fields-PROD-TLS-VERIFY-1b.md` | 4 busy-waits → `SyncDownloadWaiter.h` | console test; `test_fpm_no_process_events.py` |
| 2 | **PROD-CLAUDEMD-TRUTH** | `card-PROD-CLAUDEMD-TRUTH.md` | CLAUDE.md truth + roadmap pointer; host `check_pinned_tokens.py` | that step; **CLAUDE.md ≤ 9,695 B** (card and table agree) |
| 3 | **HYG-PROFILING-MEDIA-1** | `card-HYG-PROFILING-DNGS-1.md` | untrack the 20 DNGs only | no tracked `*.dng` outside fixtures; 605 remain |
| 4 | **PROD-ENVFLAG-1** | `fields-PROD-ENVFLAG-1.md` | `EnvFlags.h`; both call sites | console test; `test_env_flag_single_definition.py` |
| 5 | **PROD-DUALISO-GUARD-TEST** | `fields-PROD-DUALISO-GUARD-TEST.md` | `DualIsoLevelSyncPolicy.h` | console test; `test_dual_iso_policy_wiring.py` |
| 6 | **PROD-TOOLCHAIN-1** | `fields-PROD-TOOLCHAIN-1.md` | per-platform tuples | `Print toolchain` step + receipt |
| 7a | **PLAY-COUNTERS-CPU** | `fields-PLAY-COUNTERS-CPU.md` | inventory, then `PlaybackGatePolicy.h`; source-contract test | console + `test_playback_gate_wiring.py` |
| 7b | **PLAY-COUNTERS-GPU** | `fields-PLAY-COUNTERS-GPU.md` | `AsyncH2dCounterContract.h` + fake backend; bachelor job | hosted contract test; receipt or `EXTERNAL_CAPABILITY_UNAVAILABLE(gpu-runner)` |
| 8 | **PLAY-C2-SUBMIT-2-ACCEPT** | `fields-PLAY-C2-SUBMIT-2-ACCEPT.md` | accept or revert using 7b's receipt (dispatcher-exported) | inherits 7b |
| 9 | **PROD-UPSTREAM-SYNC-1** | `fields-PROD-UPSTREAM-SYNC-1.md` | merge `origin/master` (32), no rewrite; the hub refreshes `origin` before dispatch | required checks + goldens |
| 10 | **PROD-CDNG-DECOUPLE-1** | `fields-PROD-CDNG-DECOUPLE-1.md` | `CdngSequenceExport.{h,cpp}`; drop `MainWindow.h` | pipeline `.pro`; goldens |
| 11 | **HYG-EVIDENCE-RATCHET-1** | `fields-HYG-EVIDENCE-RATCHET-1.md` | 615 ratchet; ledger `STALE-2026-05-08` | ratchet test |
| 12 | **PROD-BATCHTYPES-SPLIT-1** | `fields-PROD-BATCHTYPES-SPLIT-1.md` | split; no behaviour change | goldens; header-hygiene grep |
| 13 | **USECASE-1** | none — **closure receipt, never seeded as dispatchable** | (a) DONE; (b)/(c) `EXTERNAL_CAPABILITY_UNAVAILABLE(owner-machine-state)` | **the HUB writes** `$D\receipts\usecase-1-closure-<ts>.md` with the `Write` tool, carrying `recordedUtc` (O148) |
| 14 | **PROD-README-FORK-1** | `fields-PROD-README-FORK-1.md` | fork banner; CHANGELOG | hygiene test on README head |

**Removed:** PROD-BATCH-RECEIPT-1, PROD-BATCH-CDNG-PERFRAME-ERRORS (shipped); FLAKE-BRIDGE-1 (shipped as #74/#75); PROD-GUI-EXPORT-AB-1.

---

## PHASE 2 — EXECUTE (Sonnet lanes via the loop). Haiku adjudicates daily, writes nothing else.

- Loop as re-installed in 0.2. Every PR gets `sol-review-PR-TEMPLATE.md` before merge, fed by dispatcher exports (fetched objects,
  `pr-<n>-checks.json`, `pr-<n>-review.json`); APPROVE binds to the full-40 head sha.
- **Done = MERGED then VERIFIED (CI green on `fork/master` at the merge sha).**
- **Ladder, no owner:** sonnet → sonnet with the failure inlined → opus once → `sol` `DECLINE` → `parked-with-reason`. Round cap 4.
- **Haiku daily (≤ 15 min):** receipts → CI → merge if green + sol APPROVE → `landed` → one checkpoint line. Weekly digest.

## PHASE 3 — FACTORY CHANGES ARE EARNED, ONE PER WEEK

A factory-kind card is unfrozen only when a product card's receipt names it, never while RED; `kind: factory` + `unblocksProductPr`.

## PHASE 4 — ENTRY SURFACE ≤ 30,000 BYTES OVER ALL SIX MEMBERS (Haiku, week 2, one PR)

Budgets: `RESUME.md` ≤ 4,000; orchestrator checkpoint ≤ 5,000; newest `SESSION-HANDOFF.md` block ≤ 3,000; project-memory
`README.md` ≤ 2,000; `CLAUDE.md` ≤ 9,000; `AGENTS.md` ≤ 7,000. Sum 30,000. Bus reads = the SessionStart delta only, ≤ 16 KB.

---

## RATIFY — this rev, by CIAA, no owner

```
python -c "<manifest command from §1.3 step 1>"
pwsh -NoProfile -File "$R\tools\coordination\Invoke-Lane.ps1" -Lane sol -PromptFile "$D\prompts\v2\ratify-plan-SOL.md" -Card RATIFY-PLAN-V28 -TimeoutSec 1500
# in parallel: a background Claude Opus agent with prompts\v2\ratify-plan-OPUS.md
```
Rounds 1-26: no contested finding except S45/O46 and S99/O118 (premises rejected, recorded in their receipts); unions applied as revs 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28.

## EXIT CRITERIA (derivable; printed weekly)

1. `product_share_7d` over `fork/master` ≥ 50% for 3 consecutive weeks. 2. Product track never `NO-LIVE-CARDS`.
3. Required checks include `Batch Compile`; zero re-runs-to-green in 2 weeks; bridge debt listed, never hidden.
4. Every non-terminal card has `kind ∈ {product, playback}` or `unblocksProductPr`. 5. Entry surface (six members) ≤ 30,000 B.
6. `dispatches_per_landed_product_pr_7d` ≤ 4. 7. The only owner-shaped vocabulary is `EXTERNAL_CAPABILITY_UNAVAILABLE(<capability>)`.
8. The project-hook falsifier test passes in CI on every merge, AND every `Invoke-Lane` receipt with `allowEdits: true` has
`lane ∈ {sonnet, opus}` and a `hookSha256` equal to the 0.05 receipt's.

## WHAT THIS PLAN DELIBERATELY DOES NOT DO

No lane, seat, chip, heartbeat, pen, or doctrine entry is added. The global hook, the content-review gate, `closeout.config.json`
and the finalize path are untouched. The bus gets ONE `specs/mlv-app.md` update at week 3. Nothing asks Fable to work, and
nothing waits on the owner.
