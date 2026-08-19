# MLV-App fail-closed provider supervisor candidate

This directory is a zero-authority Phase-0/1 candidate reconciled with canonical universal
token-saving subject `e70a044f31dd2f43ab7c716d63a4eb89318c61b6`, published by doctrine merge
`909f769d02e8412e51e28e242cfa8d00dadc9a3d`. Exact R14 subject
`874605e43531c9aa230ee16851f8107a8e0d9cec` remains a mechanics-only dependency for the vendored
engine and lane bindings. Start with `DISTINGUISH.md`,
`mlv-supervisor-profile.candidate.json`, and `mlv-observed-inventory.candidate.json`.

Local proof:

    py -3 -m unittest tools.provider_control.tests.test_mlv_lane_supervisor -v
    py -3 tools/provider_control/vendor/universal_provider_control.py validate profile tools/provider_control/mlv-project-profile.candidate.json

The unit suite validates the strict MLV profile and observed-inventory v2 schemas, hostile
inventory/profile inputs, 1,000 unchanged
ticks, production-root alias/reparse refusal, nonzero demand bindings, separate process-image/script
receipts, the SHADOW/CONTAINMENT fake-provider harness, and exact fail-closed bindings for canonical
doctrine, the author-attested attended rotation, and the numeric token-saving policy. The imported
receipt and policy are motivation only and grant no provider, installation, canary, or adoption
authority. Inventory v2 records the observed task, Codex automation, dual Agent Bridge MCP,
watcher, persistence, and process surfaces as partial evidence. It binds the stable two-export MLV
task XML convention while keeping the action graph incomplete and every mutable, dynamic, or
unreadable closure fail-closed. The imported universal profile is a
CLOSED-only template: its null/all-zero identities are intentional activation blockers, not
placeholders that may be treated as evidence.

CI uses immutable full-commit pins for `actions/checkout` and `actions/setup-python`. Its
`jsonschema` dependency and complete transitive set are installed from
`.github/requirements/provider-control.txt` with `--require-hashes` and binary-only, noninteractive
resolution; the source intent and explicit Windows/Ubuntu Python 3.13/3.14 wheel evidence live
beside the lock. Checkout credentials are not persisted, concurrency is event-safe and
non-cancelling, `queue: max` preserves up to GitHub's documented pending-run limit, and each job has
a bounded timeout. Local dry resolution proves the Windows legs only. R10 passed all four hosted
legs; R11 and R12 still need their own exact-tree hosted matrices and do not inherit authority from
that predecessor result.

The disposition remains
`DISTINGUISH(PENDING_INSTALLED_CHOKE_POINT_COMPLETE_ACTION_GRAPH_SHADOW_CONTAINMENT)`. The installer
is audit-only and refuses `-Apply`. Do not enable/start the task, invoke a provider, open a gate, run
a canary, or claim adoption from this candidate.
