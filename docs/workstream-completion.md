# Reviewed implementation evidence

A successful provider process does not prove delivery. After a committed change,
independent review and appropriate tests, the hub invokes the explicit completion
mode with the evidence selected for that card:

```powershell
& tools/coordination/Invoke-Workstream.ps1 -RecordCompletion -CardId CARD-1 `
  -CompletionLaneReceipt C:/evidence/lane.json `
  -CompletionReviewVerdictPath C:/evidence/review.json `
  -CompletionWorktree C:/work/card-1 `
  -CompletionAllowedPath @('src/example.cpp','tests/example.cpp') `
  -CompletionTestReceiptPath @('C:/evidence/tests.json') `
  -CompletionArtifactPath @('C:/evidence/required-output.bin') `
  -CompletionOutputReceipt C:/evidence/completion.receipt.json
```

This parameter set cannot dispatch a model. It returns the sibling adapter's
status before reading the board or creating reservations, prompts or dispatch
records. Dispatch-only options cannot be combined with it. Python 3 must be
available as `python`; the adapter itself uses only the standard library.

The lane input uses `mlv-app/fleet-lane-receipt/v1`: matching `card`, `workDir`,
`allowEdits=true`, terminal success, no refusal, failure or timeout, full
`baseSha`, and a bound `outputPath`, integer `outputBytes` and `outputSha256`.
The current worktree must be clean, descend from that base and contain actual
file changes entirely within the exact, case-sensitive repository paths supplied.
Deletions count; a different commit with an unchanged tree is refused.

Review inputs use `mlv-app/workstream-review/v1` with `verdict=APPROVE`,
`cardId`, full `subject_sha` and `laneOutputSha256`. Each required test uses
`mlv-app/workstream-test/v1`, matching `cardId` and `subject_sha`, integer
`exitCode=0`, `commandPath`, `commandSha256`, `outputPath`, integer `outputBytes`
and `outputSha256`. At least one test receipt is required. The command file is
evidence, never executed by this adapter. Required artifacts are explicit paths.

The result follows `workstream-completion.schema.json` and can only say
`reviewed-ready`. Its files, hashes, source range and canonical semantic digest
bind what was verified. Input JSON is parsed from the bytes that were hashed;
duplicate keys, boolean numeric substitutes and non-object inputs are refused.
Git reads have a bounded timeout. Inputs, HEAD, cleanliness and the published
receipt are rechecked at publication or replay. Concurrent change causes a named
refusal; existing or concurrently written evidence is preserved.

Publication uses a temporary file and an exclusive hard link in an existing
output directory. Unsupported filesystems fail; there is no overwrite fallback.
Replaying the same semantic input returns the existing bytes and timestamp.
Conflicting input refuses, including JSON type changes in existing receipts.
Any retained receipt from a failed final check remains unaccepted evidence.

These are local consistency checks. The hub remains responsible for reviewer
independence, test sufficiency, selecting every required artifact and retaining
the files. The input receipts are not signed attestations, and the adapter cannot
prevent changes after its last check. A review approval without merge and target
verification never becomes `landed`; the existing hub reconciliation owns that
later decision and queue update. The workstream loop remains disabled until its
separate execution-control and activation prerequisites pass.

Run `py -3 -m pytest -q tools/coordination/test_record_workstream_completion.py`.
Fixtures use disposable repositories and synthetic evidence. They prove
validation, replay, concurrency refusal and the real PowerShell call boundary;
they do not claim a real editing lane delivered a product change.
