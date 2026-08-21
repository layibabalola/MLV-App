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
legs. The R11 technical head `9e3ee4d8` was not hosted; its later evidence/closeout head `152f1e5f`
passed all four legs in run `32228841343`. That result must not be relabeled as a run of the earlier
technical head. R12 and R13 still need their own exact-tree hosted matrices and inherit no authority
from either predecessor result.

Project R14 head `13707bd203340e5c0336d31212e996d6880aeb72` passed 42 tests in each of
four Windows/Ubuntu × Python 3.13/3.14 legs in run `32238122126`. That run uploaded no artifacts.
The additive R15 evidence workflow therefore runs the CLOSED suite through a standard-library
runner that emits fail-closed JUnit plus a strict JSON result binding the checked-out Git head/tree,
runtime, GitHub run identity, every test outcome and duration, profile validation, zero provider
calls/processes/tokens as suite-contract assertions rather than independent provider telemetry, and
the CLOSED gate. Subtest failures, fixture errors, profile failure, skips, and every other non-pass
result fail the job and appear in JUnit. JSON and JUnit are accepted only with a last-written
manifest that binds their exact SHA-256, byte count, head, and tree; partial bundles are not uploaded.
The verifier also parses both files, recomputes counters and the CLOSED verdict, requires the profile
and repository-cleanliness JUnit cases, refuses authority claims, and rejects any sibling fatal
diagnostic while preserving the original fatal cause. Every JSON test ID, status, and rounded
duration must map to exactly one JUnit testcase with the matching outcome type and detail. The JUnit
suite timestamp/duration/hostname and both synthetic-case durations/details are also cross-checked
against the JSON and live process. Raw JUnit child cardinality must be exact: duplicate testcase
identities and non-testcase root children are refused before identity mapping. The committed
45-test inventory is bound by its sorted-ID digest, so zero, partial, or renamed discovery cannot
become a green run. Environment fields, canonical UTC start time, and finite nonnegative duration
are type-checked; JSON non-finite constants are forbidden; and the environment/GitHub matrix claims
must equal the live verifier process. The pinned profile validator is rerun during verification and
its exact exit/stdout/stderr must match the result. The verifier also rechecks the live index/worktree immediately
before artifact routing instead of trusting only the runner's earlier cleanliness statement.
Complete bundles are uploaded after a gate failure with a full-commit action pin, the same verdict is
written to the hosted step summary, and runner exceptions after argument parsing produce a separate
non-authoritative fatal diagnostic rather than a partial bundle. That diagnostic has its own strict
schema, live environment and head/tree verifier; a forged preexisting fatal file is replaced by a
zero-authority invalid-predecessor receipt and can never pass the fatal upload route merely by
existing. A self-declared Git error is not evidence: verified fatal uploads require the live
checkout's exact head/tree. The narrowly scoped dot-directory files are explicitly included by the pinned upload
action; missing files are an error. Retention is
30 days and is not a permanent evidence archive. R15 itself
remains unhosted until its own four legs produce those artifacts; the R14 console-only run cannot be
relabeled as R15 evidence. The workflow now runs for matching pull requests and matching pushes to
`master`. This does not repair the separate repository protected-check topology.

The smallest next choke-point slice is deliberately reachable only through the explicit test-fake
seam. It serializes one fake Claude slot in fixed lane order, constructs the child executable and
argv exclusively from broker-owned identities, returns deterministic no-work before identity,
reservation, lock, or child creation, and reserves a conservative per-attempt consumptive envelope
containing the fresh estimate, full cache-read estimate, and full cache-create estimate while
preserving the larger of the 20 percent completion reserve and named priority floor. A failed
attempt is conservatively charged before the one permitted retry is admitted. Full argv and its
digest must match on every reload. The broker executes a private content-addressed copy of the bound
fake source, so a source-path byte race cannot change the executed bytes. Slot completion publishes
the next lane/generation before deleting active-slot and reservation fences; a cursor-write failure
therefore blocks restart replay. The caller's fake-script path is an exact identity witness, never
launch authority.
These are local fake-provider reference tests only: the slice is not installed or reachable from a
production command and does not establish SHADOW PASS, CONTAINMENT PASS, canary, adoption, or
activation.

The forward `prelaunch` command is a separate stop-only interception primitive. It runs the same
strict production decision as `tick`, accepts only the exact CLOSED refusal or exact no-work result
shapes, requires native integer zero for every provider/token counter, and emits
`mlv-provider-prelaunch-stop/v1`. It has deliberately no authorization result: an unknown,
authorization-shaped, type-coerced, or future OPEN result is `PRELAUNCH_RESULT_INVALID`. The
tracked PowerShell wrapper exposes this command, but no scheduled task or legacy launcher points at
it. The historically observed `MLV-LaneIgnitionWatchdog` project root and
`ignite-dead-lanes.ps1` are absent on this machine, so this source improvement is not installation,
runtime interception, or functionality credit.

The disposition remains
`DISTINGUISH(PENDING_INSTALLED_CHOKE_POINT_COMPLETE_ACTION_GRAPH_SHADOW_CONTAINMENT)`. The installer
is audit-only and refuses `-Apply`. Do not enable/start the task, invoke a provider, open a gate, run
a canary, or claim adoption from this candidate.
