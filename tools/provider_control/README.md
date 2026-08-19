# MLV-App fail-closed provider supervisor candidate

This directory is a zero-authority Phase-0/1 candidate against R14 technical subject
`874605e43531c9aa230ee16851f8107a8e0d9cec`, ratified by merge
`488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d`. Start with `DISTINGUISH.md`,
`mlv-supervisor-profile.candidate.json`, and `mlv-observed-inventory.candidate.json`.

Local proof:

    py -3 -m unittest tools.provider_control.tests.test_mlv_lane_supervisor -v
    py -3 tools/provider_control/vendor/universal_provider_control.py validate profile tools/provider_control/mlv-project-profile.candidate.json

The unit suite validates both strict MLV schemas, hostile inventory/profile inputs, 1,000 unchanged
ticks, production-root alias/reparse refusal, nonzero demand bindings, separate process-image/script
receipts, and the SHADOW/CONTAINMENT fake-provider harness. The imported universal profile is a
CLOSED-only template: its null/all-zero identities are intentional activation blockers, not
placeholders that may be treated as evidence.

CI uses immutable full-commit pins for `actions/checkout` and `actions/setup-python`. Its
`jsonschema` dependency and complete transitive set are installed from
`.github/requirements/provider-control.txt` with `--require-hashes` and binary-only, noninteractive
resolution; the source intent and explicit Windows/Ubuntu Python 3.13/3.14 wheel evidence live
beside the lock. Checkout credentials are not persisted, concurrency is event-safe and
non-cancelling, `queue: max` preserves up to GitHub's documented pending-run limit, and each job has
a bounded timeout. Local dry resolution proves the Windows legs only. The hosted matrix remains
required evidence for the Ubuntu legs and is not claimed by this candidate.

The installer is audit-only and refuses `-Apply`. Do not enable/start the task, invoke a provider,
open a gate, run a canary, or claim adoption from this candidate.
