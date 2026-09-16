# Product-priority dispatch admission

`tools/coordination/Test-ProductRatioGuard.ps1` resolves one source commit and
reports an inclusive seven-day window using Git committer timestamps. Its
product share counts all reachable non-merge commits once. A commit counts as
product work when its diff touches `src/` or `platform/`; a mixed commit still
counts only once. Future-dated and older commits are excluded.

The dispatch-rate denominator is separate: unique recognized GitHub PR landings
on first-parent history whose net diff from the first parent touches product
paths. Merge subjects and squash subjects identify PR numbers. A batch counts
once. Product landings without recognized PR provenance make the rate
unavailable instead of substituting a commit count.

Reservations are the preferred observation source. The legacy dispatch log is
used only when the reservations path is absent. An existing unreadable path
produces unavailable evidence; it cannot trigger fallback. Empty reservations
remain authoritative for observed rows. Malformed rows are counted separately.
Unavailable observations produce a null dispatch rate even when a recognized
PR denominator exists. The caller rejects a numeric rate in that case; a
readable empty observation file still produces a valid zero rate.
The legacy log never proves complete accounting.

## Version-enforced all-venue accounting

Every launch venue writes a ledger row with `schemaVersion` 2:
- `Invoke-Workstream.ps1` writes `reserved`, then `charged` or `refunded`.
- `Invoke-Lane.ps1` writes its row after taking its receipt slot and before any
  provider starts, naming the receipt in `receiptPath`. A direct launch writes
  `reserved`. When Invoke-Workstream passes its reservation id through
  `MLV_DISPATCH_RESERVATION_ID` (which also crosses `Start-EditingLane.ps1`),
  the lane writes `linked` instead.
- If a lane cannot write its row, it refuses to launch.

Only `reserved` rows count toward the dispatch rate, so a dispatcher launch counts
once. The rate numerator is unchanged: every reserved launch counts, whatever its
kind or `allowEdits`.

Coverage is `COMPLETE` only when every check below holds. Otherwise it is
`PARTIAL`, and each failed check adds its reason code after
`RED_DISPATCH_COVERAGE_PARTIAL`:

| Check | Reason code when it fails |
|---|---|
| The reservations ledger is the source | `COVERAGE_LEGACY_SOURCE` |
| A versioned row exists | `COVERAGE_NOT_ENFORCED` |
| The first versioned row is at or before the window start | `COVERAGE_WINDOW_PREDATES_ENFORCEMENT` |
| No unversioned row falls in the window after enforcement began | `COVERAGE_UNVERSIONED_ROW_AFTER_ENFORCEMENT` |
| Every `mlv-app/fleet-lane-receipt/v1` receipt under `.claude-state/fleet-runs` that started in the window is named by a ledger row | `COVERAGE_RECEIPT_UNRESERVED` |
| Every in-window versioned row naming a receipt under that tree still has that receipt | `COVERAGE_RESERVATION_RECEIPT_MISSING` |
| Every receipt can be read | `COVERAGE_RECEIPT_UNREADABLE` or `COVERAGE_RECEIPT_IN_FLIGHT` |
| The receipt tree can be read | `COVERAGE_RECEIPTS_UNAVAILABLE` |

An unreadable receipt, an empty receipt slot, or an unreadable tree makes
coverage cannot-determine, and cannot-determine is never `COMPLETE`. A receipt
whose failure is `dispatch-ledger-write-failed` records a refused launch, so it
does not count.

Coverage therefore cannot be `COMPLETE` until seven days after the first
versioned row. That wait is the ratified seven-day enforcement window, not a
defect.

A row outside the window is never counted as malformed. Rows with no
`schemaVersion` are legacy: they still count toward the rate, but they never
prove coverage.

Interactive sessions that author commits themselves are not a launch venue.
Their commits are counted by the product share, not by the ledger.

At both actual launch points, `Invoke-Workstream.ps1` checks the guard after the
kill switch and before recording a reservation or launching a provider. Valid
`RED` permits only explicitly typed `product` and `playback` cards. Other or
missing kinds return 6. Unresolved Git history, malformed output, inconsistent
counts or invalid JSON types return 3 and refuse every kind. An empty valid
window is `RED` with an unavailable product share. Dry runs prepare evidence but
do not reserve or launch a provider. The loop records refused cards as skipped
and continues to the next track.

`GREEN` requires all of the following:
- product share at least 0.50
- dispatch rate at most 4.0
- complete coverage
- available observations
- complete landing provenance
- no malformed rows

Tests exercise that decision through the real reader, over a disposable ledger
and receipt tree.

`GREEN` lifts the dispatcher's kind gate, as shipped. It does not thaw cards in
the one-way `frozen-factory-20260906` state. That unfreeze operation still does
not exist. Before it is implemented, seven days of `COMPLETE` version-enforced
accounting and an authoritative `GREEN` reading are required, and Phase 3 then
thaws one card per week. This change adds no factory-unfreeze authority. It
builds only the accounting that precondition needs. The adjudication record is
`fleet-runs/swarm-guard-unfreeze-20260916T1325Z/SYNTHESIS.md`.

Validation uses disposable Git histories, controlled timestamp boundaries,
actual PowerShell guard and dispatcher executions with fake providers, and the
actual loop body extracted by PowerShell's parser. Fixture providers never edit
the live queue or call an external model. Provider exit zero continues to mean
process completion only; reviewed delivery and loop activation require their
separate receipts.
