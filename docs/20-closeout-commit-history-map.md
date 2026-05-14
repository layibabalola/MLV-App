# Recent Closeout Commit History Map

This note keeps the published Git history intact while making the recent
closeout-driven commits understandable to a human developer reading `git log`.

## Why This Exists

Several recent commits were created by the brokered closeout workflow with
generic subjects such as `brokered closeout checkpoint` or
`brokered closeout evidence repair`. The commits themselves are valid, but the
subjects do not explain what changed or why.

This map is also a compare-result landing zone for historical closeout rounds:
the history entry should be readable alongside a `closeout-compare-result.v1`
artifact that says whether the round is `current`, `stale`, `divergent`, or
`blocked`. That keeps the commit history narrative and the comparison outcome
in sync when another repo is doing a side-by-side workflow review.

## Recent Commit Map

| Commit | Original subject | What actually changed |
|---|---|---|
| `1a65275f` | `Merge commit '43f2fde5...' into HEAD` | Folded the preserved split branch back into `master` after repo sweep validated that the retained closeout config additions were safe to integrate. |
| `d20110c3` | `Merge commit '9b1db5df...' into HEAD` | Integrated the closeout hardening branch into `master`, including docs freshness updates, dashboard contract coverage, retry-policy changes, and closeout evidence artifacts for `wb-6d3be943aaf2481e`. |
| `9b1db5df` | `fix(closeout): renew stale validation closeout loops` | Expanded `finalizeLoop` repair handling so `stale_review` and `validation_failed` could renew/retry instead of dying on the first blocked tuple, and raised retry budget to give the workflow room to recover. |
| `f372e6c6` | `brokered closeout checkpoint` | Updated the finalize retry policy in the broker so the additional validation retry budget matched the config instead of drifting. |
| `e75c1d11` | `brokered closeout checkpoint` | Adjusted the broker default retry limit so the closeout loop could keep working through transient validation failures after the config change. |
| `281b46b9` | `brokered closeout checkpoint` | Added the `validation_failed -> rerun_validation_smoke` second-order repair plus regression coverage proving the finalize loop treats that blocker as retryable. |
| `f93d774e` | `brokered closeout checkpoint` | Added the stale-review renewal rule to the closeout standard and covered the new retry behavior in broker tests so policy, code, and tests described the same behavior. |
| `43f2fde5` | `preserve dirty split for wb-6d3be943aaf2481e` | Preserved the `same work block` and `freshness` dashboard-spec contract lines on a split branch while the main closeout branch continued to move. |
| `755e3ec2` | `brokered closeout evidence repair` | Generated the required closeout evidence bundle for `wb-6d3be943aaf2481e`: `closeout.json`, `handoff.json`, `metrics.json`, and `session.json`. |
| `c90e5efa` | `brokered closeout checkpoint` | Landed the docs-and-contract hardening pass: compare-ready reporting, dashboard freshness language, round-delta durability, and baseline coverage for the updated closeout workflow. |

## Forward Rule

Future repo-owned closeout commits should name:

- the work block or candidate being acted on
- the purpose of the action
- the main surface being changed when the commit is narrow

Examples:

- `chore(closeout): repair closeout.json, metrics.json, and session.json for wb-demo before final push`
- `chore(closeout): checkpoint brokered_closeout.py and closeout.config.json for wb-demo`
- `merge(closeout): integrate wb-demo closeout hardening into master`
