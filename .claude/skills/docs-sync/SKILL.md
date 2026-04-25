---
name: docs-sync
description: |
  Bring the docs/ tree back into agreement with the working tree by
  analyzing commits since the last docs-touching commit and applying
  surgical edits to the affected sections. Routes via the ownership map
  at docs/.sync-ownership.yml. Avoids fabrications by re-grepping every
  cited symbol before quoting it, and commits the result as
  "docs: sync to <short-sha>".

  Trigger this skill BOTH when the user explicitly invokes /docs-sync
  AND when the user says any of the following in natural language:
  - "sync the docs"
  - "sync docs"
  - "sync documentation"
  - "update the docs"
  - "update docs to HEAD"
  - "update docs to current code"
  - "refresh the docs"
  - "refresh docs after recent commits"
  - "incremental docs update"
  - "incremental documentation update"
  - "docs sync"
  - "docs are stale, update them"
  - "documentation has drifted, fix it"
  - "bring docs up to date"
  - "bring documentation up to date"
  - "are the docs current?" (run --dry-run first to report drift)
  - "what changed since the last docs commit?" (--dry-run)
  - "sync docs after the merge"
  - "refresh the docs from HEAD"
  - "reconcile docs with code"
  - "patch docs for recent changes"

  Use this skill INSTEAD OF doing a full re-audit when changes are
  incremental. Use the four-stranger-reviewer audit (separate workflow)
  only if the user asks "are the docs still 10/10?" or "re-audit
  everything".
---

# /docs-sync — Incremental documentation sync

You are bringing `docs/` back into agreement with the working tree. **Do
not do a full audit** — that wastes hours. Walk the commit delta, route
each change through the ownership map, and apply surgical edits.

## Inputs

1. The current working tree at the repo root.
2. The ownership map: [`docs/.sync-ownership.yml`](../../../docs/.sync-ownership.yml).
3. The last full audit commit: `9ada94a7` (2026-04-24,
   "docs: introduce canonical docs/ tree …"). Everything earlier is
   already covered.

## Arguments (parse from the user's prompt)

- `--since <sha>` — start of the delta. Default: most recent
  `docs/`-touching commit (`git log -1 --format=%H -- docs/`).
- `--dry-run` — report the plan without editing files.
- `--include-wip` — also process uncommitted working-tree changes
  (`git diff HEAD`).
- `--strict` — fail loudly on any unowned path; default skips with a
  warning.

## Procedure

### 1. Bound the delta

```bash
SINCE=$(git log -1 --format=%H -- docs/)   # or --since arg
git log --no-merges --oneline ${SINCE}..HEAD
git diff --stat ${SINCE}..HEAD
git diff --name-only ${SINCE}..HEAD > /tmp/changed-files.txt
```

If `--include-wip`, also union in `git diff HEAD --name-only` (for the
codex peer's WIP) and `git status --short` (untracked).

If the changed-files list is empty, **report "docs are in sync" and
exit**. No-op is the right answer.

### 2. Route via ownership map

Read `docs/.sync-ownership.yml`. For each changed path:

1. Find the first `owners[].paths` glob it matches (using `fnmatch`
   semantics — `**` is any depth, `!pattern` is a negation).
2. Collect the union of `docs:` from all matching owners.
3. If no owner matches, log a warning. With `--strict`, abort; otherwise
   continue.

The result is a mapping `{ doc_path -> [list of changed source paths] }`.

### 3. For each affected doc, decide what to update

**Do not blanket-rewrite.** For each doc:

a. `git diff ${SINCE}..HEAD -- <changed-source-paths>` — read the
   actual diff hunks the agent must reflect.

b. Open the doc, find the section(s) that name the changed source
   (`Grep` for the file path, function name, struct, or env var).

c. Edit minimally:
   - If a function signature changed → update the signature in the doc.
   - If a struct field was added → add a row to the field table.
   - If a CLI flag was added/removed → update the flag table.
   - If a line number drifted → update the `path:line` citation.
   - If a section is now obsolete (the source it described was deleted)
     → mark the section deprecated or remove it.

d. **If the change is too large to mirror surgically** (e.g. a whole new
   subsystem like another debayer algorithm), spawn a focused agent
   for that doc with the diff and the section to extend. Don't try to
   write paragraphs of new prose inline.

### 4. Avoid the failure modes the original audit found

- **No fabricated symbols.** Every cited identifier must exist in the
  repo at the moment of the edit. Re-grep before quoting.
- **No fabricated cross-doc links.** Verify every `docs/NN-...md` you
  reference still exists.
- **No fabricated test names.** The minitest runner has no name filter;
  see `docs/02-developer-guide.md` §13 for the canonical phrasing.
- **No stale absolute paths.** Use repo-relative paths for source
  citations; absolute `C:/!Layi Wkspc/...` paths are forbidden.
- **No `.claude/profiling/`** in user-facing docs — the policy is
  `.claude-state/profiling/` (per `02-developer-guide.md` §15.4).

### 5. Run a narrow fabrication audit on touched sections only

After edits, for each modified doc section, spot-check 3-5 cited
identifiers/paths/line numbers. The full-tree audit isn't needed — only
the deltas you just introduced.

### 6. Update the version pin if applicable

If `platform/qt/MLVApp.pro:450-460` changed, search-and-replace the
old `1.15.0.0` (or whatever) in:

- `docs/00-overview.md`
- `docs/01-user-guide.md` (front matter)
- `docs/03-technical-specification.md` (footer)
- `docs/04-external-auditor-guide.md` (§4 abstract)

Don't forget the `### YAML frontmatter` style version pins.

### 7. Commit

```bash
git add docs/
git commit -m "docs: sync to <short-sha>

- <one bullet per significantly edited doc>
- <…>

Sync window: ${SINCE}..HEAD (N commits, M files changed)
Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

The `docs: sync to <short-sha>` subject is the convention — the next
sync uses this commit as its `${SINCE}`.

Do **not** stage engine source changes that the codex peer is working
on (`MainWindow.cpp`, `PlaybackScaling.h`, etc., when they appear in
the working tree but not in any commit you authored). Stage only what
your sync produced.

## Reporting

Report at the end:

```
Docs sync complete: ${SINCE}..HEAD (N commits)

Touched docs:
  - docs/03-technical-specification.md (3 edits, §4.1 + §7 + §12)
  - docs/03b-...-algorithms.md (1 edit, §5.4 dual-ISO)
  - docs/02-developer-guide.md (no changes — all citations still valid)

Skipped (no owner): tests/perf/scratch/foo.txt
Suspected fabrications introduced: 0
Version pin: unchanged (1.15.0.0)

Committed as <new-sha>.
```

## When NOT to use this skill

- The first time a brand-new doc is needed (e.g. a whole new subsystem
  like a Resolve plugin). That's a drafting task, not a sync.
- When the user asks for a full re-audit ("are the docs still good?")
  — that's a separate workflow that re-runs the four stranger reviewers.
- For commits that only touch `.claude-state/` or other ignored paths.

## See also

- `docs/.sync-ownership.yml` — the routing table.
- `.claude-state/docs-audit/` — the original audit notes (kept for
  reference; treated as historical, not a sync target).
- Memory: `track_persistent_work_product.md` — reminds us to commit the
  sync, not just edit and walk away.
