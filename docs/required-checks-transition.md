# Required-check transition

Phase 0.4b uses `tools/coordination/set-required-checks.ps1` (PowerShell 7.2+).
Running it without `-Apply` validates local prerequisites and prints the exact
proposed request. It performs no network call or write.

The transition replaces Factory Bridge Regressions with Batch Compile and keeps
the other four checks, `strict=true`, and GitHub Actions app ID 15368. It uses
only GitHub's [required-status-check PATCH endpoint](https://docs.github.com/en/rest/branches/branch-protection#update-status-check-protection).
It does not submit the broader branch-protection object.

Apply only after the actor PR is reviewed, merged and its
`execution-control-0.4b-i.json` is recorded. The script requires the matching actor
hash and exact-head Sol approval, the pinned Batch Compile falsifier, a guardrail
head bound to its own `execution-control-0.4c-i.json` receipt, successful
nonempty Windows guardrail coverage, and the existing protection snapshot.

The guardrail-head binding compares `0.4c-guardrail-move.json.headSha` against
`execution-control-0.4c-i.json.reviewedHeadSha` -- the PR HEAD the hosted
guardrail workflow actually ran against and Sol reviewed -- never against that
receipt's `mergeSha`. On a valid chain the merge commit is legitimately a
different SHA than the reviewed/tested head; comparing against `mergeSha`
rejects real, valid evidence. Both `reviewedHeadSha` and `mergeSha` are
still validated as full 40-hex commit SHAs.

### Deterministic control-plane checkout bytes

The fixed-set receipts (and this actor) hash Git *blob* bytes, which Git
always LF-normalizes. Other consumers (the coordination wrapper, the 0.2
gate) hash raw *disk* bytes after checkout. Under `core.autocrlf=true` those
only agree for paths `.gitattributes` pins to `text eol=lf`. `.gitattributes`
now pins the eleven control-plane paths this actor and the coordination
loop depend on (hooks, their tests, and the `tools/coordination/*.ps1`/`*.py`
scripts) to `text eol=lf`, so a fresh checkout on any `core.autocrlf=true`
machine byte-matches the Git blob. `tools/repo_hygiene/test_control_plane_line_endings.py`
proves this with a disposable Git fixture (no real hook files touched, no
global Git config written) and proves the guarantee breaks -- and is
restored -- when a pinned rule is removed.

**Migration constraint:** after the reviewed merge, and with editing workers
stopped, the HUB reconciles the canonical checkout and updates the raw
`hookSha256`/`hookTestSha256` fields in the existing enforcement receipt,
preserving all prior values and adding a fresh, harmless hook-wiring probe.
This actor and its tests do not edit any live receipt or registry. Old
worktrees checked out before this `.gitattributes` change must be refreshed
from the reviewed commit or are ineligible for hash-sensitive work. A fresh
checkout applies the rules, but switching an existing checkout or using
`checkout-index --force` may leave unchanged CRLF files untouched. Verify raw
hashes rather than assuming a switch refreshed them. Preserve the original
bytes, verify their normalized content equals the reviewed blobs, materialize
those exact blobs, and refresh only the affected index entries. Require zero
staged diff and a clean checkout afterward; retain all prior evidence.

Run the complete hook-registration suite in a fresh isolated checkout. Its
venue-away fixture assumes the checkout differs from `BoardRoot`; running that
fixture at the canonical root does not test that condition. The actual harmless
CLI wiring probe must observe a denied tool invocation. On Windows, pass its
multiline prompt through stdin: the `claude.cmd` argument path can lose lines.

Before PATCH, an immutable `0.4b-transition-intent.json` preserves the old check
set. A fresh GET must prove all five resulting checks and their app bindings.
Only then does the actor append a snapshot row and create the completion receipt.
All receipt writes use exclusive creation, never replacement; existing evidence
is retained. A process lock serializes cooperating invocations.

If PATCH succeeds but its subsequent verification fails, rerun the same reviewed
actor. It recognizes the saved intent and an already-correct live set, re-verifies
it, then finishes recording evidence. A malformed/partial receipt, an unexpected
live check, or a changed actor fails closed for explicit evidence reconciliation.
A completed transition is one-shot. No automatic rollback or blind retry occurs.

The fixtures in `tools/repo_hygiene/test_set_required_checks.py` run against a fake
GitHub executable and temporary receipts. They never contact GitHub or alter the
machine's protection policy.

### Separating bridge diagnostics

After the guardrail move and required-check transition receipts exist,
`demote-factory-bridge.ps1` previews a local workflow move. Pass the isolated
checkout as `-RepoRoot` and the receipt directory as `-ReceiptsDir`; use
`-Apply` to materialize it there. The actor does not change branch protection
or write receipts. The reviewed PR must preserve the bridge job's steps and
pinned actions, move them to the visible `Factory Bridge` workflow on every PR,
and update the contributor list and workflow inventory together. Bridge failures
remain visible follow-up work; the five product and hygiene checks above remain
required. The hub records `0.4c-demoted.json` after the reviewed PR lands.
