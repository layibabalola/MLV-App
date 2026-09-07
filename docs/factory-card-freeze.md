# Factory card freeze (Phase0.5)

`tools/coordination/freeze-factory-cards.py` is a deterministic, one-way
backlog freeze for the board queue. It transitions non-terminal
**factory**-kind cards to a fixed frozen state while leaving every
product/playback card, and all historical fields, byte-for-byte alone. Python
stdlib only — no subprocess, no git, no network.

There is no unfreeze option in this phase. A future Phase0.6 adds
ratio-guard behavior on top of this script.

## Usage

```
py -3 tools/coordination/freeze-factory-cards.py --queue PATH.json [--dry-run | --apply --receipt PATH.json]
```

- `--queue PATH` is **required** and always explicit — the script never
  guesses or writes the live queue implicitly.
- `--dry-run` is the default when neither flag is given. It computes and
  prints the proposed change set and writes nothing.
- `--apply` requires `--receipt PATH` and performs the write.

## Kind classification

A card with an existing string `kind` field is **never** re-derived or re-typed,
including an empty or unrecognized string. Unknown values are retained and
reported. A present non-string `kind` refuses the operation before any writes;
it is never silently replaced. Field presence, rather than truthiness, also
governs preservation of existing `track` values.

A card with no `kind` derives one from its `scope` field (a string or a list
of strings). Each whitespace/comma-separated token is normalized
(backslashes to forward slashes, harmless surrounding punctuation and a
trailing `:<line>` suffix stripped) and classified only if it contains a
`/` after normalization — bare filenames and prose never prove product or
playback.

Recognized roots, checked narrowest-first:

- **playback** (narrow): `src/gpu/`, `tools/gpu/`, `platform/qt/GpuDisplay*`,
  `platform/qt/RenderThread*`, `platform/qt/OpenGLRenderThread*`
- **product**: `src/`, `platform/qt/`, `tests/console/`, `tests/gui/`,
  `tests/pipeline/`, `tests/fixtures/`, `pixel_maps/`, `data/`
- **factory**: every other slash-path token, including `tools/`, `docs/`,
  `.github/`, `agents/`, `.dual-lane/`, `.claude*/`, and any unrecognized
  root.

If any token on a card classifies as factory, factory wins outright over
product/playback. Otherwise playback wins over product. A card with no
recognized path token anywhere is **scopeless**: it derives to `factory`
unless its id is in the exact keep-live exception set below, in which case
it derives to `playback`. A scopeless card's id is always recorded in the
receipt's `scopelessIds`, even when the exception applies. `kind`/`track`/
`state`/`priority`/`id`/`title` are never consulted for classification.

### Exact keep-live exceptions

`USECASE-1`, `MEASURE-STRATEGY-1`, `VENUE-NOISE-1`, `C2-SUBMIT-2`,
`C2-PROV-1`, `C2-TELEM-2` — when scopeless, these derive to `playback`, not
`factory`.

### track

When a kind is derived (not already present) and the card's `track` field is
absent, `track` is set equal to the derived `kind`. An existing `track`
value is never overwritten — the script does not rewrite old history.

## Freezing

The terminal-state set is byte-for-byte the same list
`tools/coordination/Invoke-Workstream.ps1` uses, case-sensitive:
`closed-fixed`, `closed-not-this-board`, `closed-root-caused`,
`closed-superseded`, `closed-transformed`, `landed`, `landed-evidence`,
`landed-local-proof`, `CLEARED`, `RETIRED`, `withdrawn`, `superseded`,
`retracted-and-fixed`, `fixed`, `answered-folded`.

A card whose resolved kind is `factory` and whose state is neither terminal
nor already the frozen state transitions to `frozen-factory-20260906`. Its
original state is preserved under a `freezeProvenance.previousState` field
that is never erased on subsequent runs. A card already in the frozen state
is left alone (idempotent), retaining its existing `freezeProvenance`
untouched. A terminal factory card is left alone (it is done, not live).

If a card about to transition (non-terminal, not already frozen) already
carries a `freezeProvenance` field -- e.g. from a hand edit or a replayed
change list -- the run refuses before any write, rather than silently
overwriting existing freeze evidence. Resolve manually (confirm or clear the
field deliberately) before re-running.

A card with no existing `kind` must have a `scope` that is absent, `null`, a
string, or a list of strings; any other shape (a number, an object, or a
list containing a non-string entry) refuses the entire run before any write.
This check never applies to a card that already carries a `kind` (including
sonnet-owned cards), since such a card's scope is never consulted.

**Exception:** a card that already carried an explicit `kind` *and* whose
`owner` is `sonnet` is deep-identical — untouched in every field, including
state, even if that `kind` literally reads `factory`. An existing `factory`
card owned by anyone else still freezes normally if non-terminal.

## Dry run and receipt

`--dry-run` prints every proposed field change, the full `scopelessIds`
list, and a `dryRunDiffSha256` — the sha256 of the canonical (sorted-key,
compact-separator) JSON change list for *this run*, excluding any
timestamp. The change list records **every** changed or added field per
card (`kind`, `track`, `freezeProvenance`, and `state`, as applicable) in
write order, so the hash binds the full mutation and replaying the list
onto the original input reproduces the proposed queue.

`--apply` writes the queue atomically (sibling temp file + `os.replace`),
after re-reading the queue file immediately before the write and refusing
if its bytes differ from what was originally loaded (concurrent
modification). The write never knowingly shrinks the queue's byte size:
formatting is preserved and, if the freshly serialized document would come
out shorter, trailing whitespace pads it back up (JSON parsers ignore
trailing whitespace after the top-level value).

The receipt (`--receipt PATH`) is created with **exclusive** creation — it
never overwrites existing evidence. Its required fields are `recordedUtc`,
`queueSha256`, `frozenCount` (the **total** currently-frozen count, not just
cards newly transitioned this run — so repeated runs don't reset it),
`dryRunDiffSha256`, and `scopelessIds`.

- An existing receipt is first validated against its full required schema
  (`recordedUtc` a valid UTC datetime, `queueSha256`/`dryRunDiffSha256` each
  a 64-char lowercase hex sha256 digest, `frozenCount` a non-negative int
  that is not a bool, `scopelessIds` a list of unique strings). A malformed
  existing receipt refuses the run **before** any queue mutation.
- If a valid receipt already exists at `--receipt` and its `queueSha256`,
  `frozenCount`, and `scopelessIds` match what this run computes, the queue
  is re-read immediately before declaring success and its hash is checked
  against the receipt's `queueSha256` one more time — closing the window
  where a concurrent writer changed the queue after the initial read but
  the (now stale) receipt still looked like a match. Only then is it a
  no-op: queue and receipt are left byte-identical, exit 0. (A matching
  receipt's own `dryRunDiffSha256` may legitimately differ from the diff
  this run computes — an already-applied queue computes an empty diff —
  so that field is never compared for the match.)
- If an existing (valid) receipt disagrees, the run refuses **before**
  touching the queue and exits non-zero — existing evidence is never
  overwritten or deleted.
- If the queue write succeeds but receipt creation then fails — for any
  I/O reason, not just a concurrent run racing this one and creating the
  receipt first — the script prints the queue's resulting `queueSha256`
  and states plainly that the queue **was** mutated — it never claims "no
  mutation" after a partial write — and exits non-zero so the caller can
  reconcile. Any partial/incomplete file such a failure may leave behind
  at `--receipt` is never treated as valid evidence by a later run: the
  schema validation above refuses it before any further mutation.

No locking beyond this narrow correctness is used; the script needs no
subprocess, git, or network access to do its job.
