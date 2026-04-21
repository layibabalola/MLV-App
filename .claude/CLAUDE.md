# Claude operating rules - MLV-App worktree

These rules apply to every Claude session that opens this worktree. They exist so investigation work is not redone when context rolls over, and so changes do not silently regress pipeline output.

Rules here are harmonized with `/AGENTS.md` (Codex's equivalent). Both LLM agents follow the same conventions so the docs stay coherent.

---

## Rule 1 - Persist analysis findings to `.claude/analysis/<topic>.md`

Whenever you produce a non-trivial investigation, research report, code audit, performance analysis, or architectural recommendation, write the findings to a topic note under `.claude/analysis/` before summarizing in chat.

Conventions (matched to `AGENTS.md`):

- One file per topic: `.claude/analysis/<topic>.md`. Update the existing note on follow-up work. Do not scatter findings.
- Separate claims by confidence level:
  - `Verified locally` - you opened the file and read the code.
  - `Cross-checked from prior analysis` - came from an earlier Claude / Codex / peer report and you did not re-verify this session.
  - `Needs runtime profiling` - architectural reasoning but not measured.
- Use `path:line` references - they are the anchors that make the doc re-actionable.
- Rank next-step recommendations by impact x effort.
- Preserve raw numbers and quotes from tools / peer projects. Do not paraphrase away specificity.

A secondary file, `.claude/ANALYSIS_LOG.md`, exists as an append-only dated history of the 2026-04-20 playback investigation. New investigations go into `.claude/analysis/<topic>.md`, not into `ANALYSIS_LOG.md`. The log file is historical only.

Active investigation notes (keep in sync with `AGENTS.md`):

- `.claude/analysis/mlv-playback-investigation.md`
- `.claude/analysis/testing-strategy.md`
- `.claude/analysis/testing-scaffold-implementation.md`

## Rule 2 - Verify claims before building on them

If another tool (another LLM, a teammate, a forum post) makes a specific `file:line` claim, open the file and confirm before citing it in your own analysis. Record the verification in the topic note so future sessions do not re-verify.

## Rule 3 - Every change must ship with a test or documented test gap

Update (2026-04-20): a seed automated scaffold now exists under `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/` and `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd/.github/workflows/tests.yml`, but broad clip/golden/perf coverage is still incomplete.

This project has essentially no automated test coverage today from the perspective of risky playback-pipeline changes (see `.claude/analysis/testing-strategy.md`). The in-progress optimization work (per-frame allocator rewrite, cache refactor, Dual ISO preview path, GPU pipeline) is high-regression-risk. Therefore:

- Code changes to pipeline stages (anything in `src/mlv/`, `src/debayer/`, `src/processing/`, `src/mlv/llrawproc/`) require at least one golden-frame regression test that would fail if the stage's output changed unexpectedly. Add the test in the same PR.
- If a test is impractical (for example first-time bootstrap of the harness or GPU-only code before parity tests exist), explicitly call out the test gap in the PR description and add a follow-up item to `testing-strategy.md` section 9 ("Known test gaps").
- Qt UI changes require at least a QTest smoke test that exercises the code path, unless a test gap is documented.
- Performance optimizations require a before/after measurement recorded in the topic note. "Feels faster" is not acceptable evidence.

See `.claude/analysis/testing-strategy.md` for the harness design, fixture layout, and how to add a test of each type.

## Rule 4 - Absolute file paths in md files here

Use the `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd/...` prefix when citing files in md documents. This worktree has an unusual path and relative references break when the docs are read outside the worktree root.

## Rule 5 - Never delete analysis history

When updating `.claude/analysis/*.md`, only append new sections, mark prior findings as superseded in place, or revise sections with a change note. Never remove old sections - the historical reasoning is part of the audit trail.

## Rule 6 - Do not modify Codex-owned files without clear reason

`AGENTS.md` and the portions of `.claude/analysis/*.md` authored by Codex are the counterpart agent's working notes. When our analysis extends or contradicts Codex's, add a new dated section rather than rewriting Codex's sections in place. Cross-reference by section heading.

## Rule 7 - Parallelize analysis and implementation with background agents

When analyzing a Codex work dump, reviewing a large change set, or implementing anything that spans more than one file or subsystem, spawn as many concurrent Explore/general-purpose agents (via the `Agent` tool) as are useful, dispatched in a single tool-use block.

- Carve the work along independent axes (e.g. concurrency audit of module A, fixture-size / CI audit, test-coverage audit, perf-number verification) and give each agent a self-contained prompt with absolute file paths and the specific questions it must answer. No "based on your findings, fix it" language - each agent returns evidence; synthesis stays in the main session.
- Prefer the `Explore` subagent for read-only verification, the `general-purpose` agent for multi-step searches, and `Plan` for design questions.
- Verify agent output before citing it. Agents occasionally report confidently wrong claims (e.g. "function X is before stage Y" when in fact X *calls* Y internally). When two agents disagree, resolve the conflict by reading the code yourself.
- When the work is single-file and localized, do not spawn agents - direct tool use is faster. The rule is "parallelize when it helps," not "always spawn agents."
- Cap total concurrent agents in a single batch at about 4-5; beyond that, returns diminish and context stitching gets expensive.

This rule is harmonized with `AGENTS.md`; Codex is expected to follow the equivalent pattern when doing the reverse-direction work.
