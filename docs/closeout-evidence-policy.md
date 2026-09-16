# Closeout evidence tracking policy

Brokered closeout keeps tracking `.closeout-evidence/` in Git. No growth ceiling
or ratchet test applies to it. Decided on 2026-09-16 by a three-seat adjudication
swarm for card HYG-EVIDENCE-GROWTH-POLICY-1; board-local record
`.claude-state/fleet-runs/swarm-evidence-policy-20260916T1325Z/SYNTHESIS.md`.

## Why tracking stays

- `closeout.config.json` `evidenceRepair` is enabled for `publish_missing_upstream`,
  `publish_ahead_only` and `final_push`. `tools/repo_hygiene/brokered_closeout.py`
  treats an evidence artifact as missing unless `git cat-file -e HEAD:<path>`
  succeeds, then writes, stages and commits the current work block's four
  `requiredArtifacts`. Untracking is undone by the next closeout. Ignoring the
  root makes `git add` fail and blocks the push with `evidenceRepairFailed`.
- `docs/never-authorized.json` NA-2 denies deleting or moving content under
  `.closeout-evidence` with no exception. No archive route exists, so rolling
  windows and compressed archives cannot be executed.
- The definitive fix plan leaves `closeout.config.json` and the finalize path
  untouched. Every alternative needs that file changed.
- The cost is small. At fork/master `18a9c60d` the root holds 617 work-block
  directories, each with exactly the four required artifacts (2,468 files,
  about 1.8 MB of blobs), against a pack of about 650 MB. History is never
  rewritten, so untracking would reclaim no pack bytes.

## Why there is no ratchet test

Closeout adds one directory per finalized work block, so a ceiling turns a
required check red on normal operation (the ratchet was removed from PR #124
for that reason). A floor can only fail after a delete or move that NA-2
already denies at tool-call time and that
`tools/repo_hygiene/test_mlv_never_authorized.py` already covers. Finalize
validates the current block's artifacts itself.

Growth is bursty, not steady. Roughly 500 directories a week were added in late
May and June 2026, 2 on 2026-09-07, and none from then through 2026-09-16.
Weekly directory counts are therefore not a usable trigger.

## Re-open condition

Re-open only when both hold:

1. Tracked evidence exceeds 20 MB on `fork/master`:

   ```bash
   git ls-tree -r -l fork/master -- .closeout-evidence | awk '{s+=$4} END {print s}'
   ```

2. A concrete cost is measured and named, such as clone time, review noise or
   upstream-sync conflicts.

The remedy is not a card alone. It needs a two-key plan amendment covering the
untouched `closeout.config.json` clause, and an NA-2 amendment that creates a
move route for evidence. Only then can a tree-counted test
(`git ls-tree -r <sha>`, never `git ls-files`, with a lower bound) encode the
new policy.
