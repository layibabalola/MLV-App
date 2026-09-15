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
Neither source proves complete accounting across all dispatch venues, so current
runtime output always reports `PARTIAL` coverage and `RED`.

At both actual launch points, `Invoke-Workstream.ps1` checks the guard after the
kill switch and before recording a reservation or launching a provider. Valid
`RED` permits only explicitly typed `product` and `playback` cards. Other or
missing kinds return 6. Unresolved Git history, malformed output, inconsistent
counts or invalid JSON types return 3 and refuse every kind. An empty valid
window is `RED` with an unavailable product share. Dry runs prepare evidence but
do not reserve or launch a provider. The loop records refused cards as skipped
and continues to the next track.

`GREEN` requires product share at least 0.50, dispatch rate at most 4.0, complete
coverage, available observations, complete landing provenance and no malformed
rows. Tests use synthetic complete evidence to exercise that decision. The
runtime observation reader cannot issue complete coverage, and this change adds
no factory-unfreeze authority. Future unfreezing still requires seven days of
version-enforced accounting across every venue and an authoritative green gate.

Validation uses disposable Git histories, controlled timestamp boundaries,
actual PowerShell guard and dispatcher executions with fake providers, and the
actual loop body extracted by PowerShell's parser. Fixture providers never edit
the live queue or call an external model. Provider exit zero continues to mean
process completion only; reviewed delivery and loop activation require their
separate receipts.
