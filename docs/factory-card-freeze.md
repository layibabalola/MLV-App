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

A card that already carries a non-empty `kind` field is **never** re-derived
or re-typed, even if the value is unrecognized (an unknown existing kind is
retained conservatively and reported on stdout, never silently changed).

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
is left alone (idempotent). A terminal factory card is left alone (it is
done, not live).

**Exception:** a card that already carried an explicit `kind` *and* whose
`owner` is `sonnet` is deep-identical — untouched in every field, including
state, even if that `kind` literally reads `factory`. An existing `factory`
card owned by anyone else still freezes normally if non-terminal.

## Dry run and receipt

`--dry-run` prints every proposed field change, the full `scopelessIds`
list, and a `dryRunDiffSha256` — the sha256 of the canonical (sorted-key,
compact-separator) JSON change list for *this run*, excluding any
timestamp.

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

- If a receipt already exists at `--receipt` and its `queueSha256`,
  `frozenCount`, and `scopelessIds` match what this run computes, the run is
  a no-op: queue and receipt are left byte-identical, exit 0.
- If an existing receipt disagrees, the run refuses **before** touching the
  queue and exits non-zero — existing evidence is never overwritten or
  deleted.
- If the queue write succeeds but receipt creation then fails (e.g. a
  concurrent run raced this one and created the receipt first), the script
  prints the queue's resulting `queueSha256` and states plainly that the
  queue **was** mutated — it never claims "no mutation" after a partial
  write — and exits non-zero so the caller can reconcile.

No locking beyond this narrow correctness is used; the script needs no
subprocess, git, or network access to do its job.
