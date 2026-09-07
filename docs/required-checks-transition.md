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
