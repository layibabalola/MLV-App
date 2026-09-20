# ATTR3-ADMIT-CONTENT-PIN-1 -- check/use window table (round 2g)

Round 2g responds to round 2f's two independent keys (`sol`, `fable`), both CHANGES_REQUESTED,
both converging on the same structural finding: the table itself carried the defect it exists to
prevent -- a row marked CLOSED that only narrowed, and a window missing entirely. This is the
first time the table is committed to the tracked tree rather than living only in round-scoped
review evidence, specifically so it survives independently of any single round's turn budget.

**Rule this table now follows (round-2g ruling, binding going forward): a row is CLOSED only when
the window is CLOSED, not narrowed. A mechanism is named as what it is (a handle, an allowlist, a
literal-spelling match) plus its enumerated escape hatches. Enlarging a list and re-claiming
closure is not accepted as a fix.**

Path covered: `Get-UmRunFixtureAdmission` -> `Test-UmRunFixtureContentPin` ->
`Assert-AttrCudaFixtureCommittedBytes` (admission) -> `Invoke-UmRunDrop`'s pre-copy re-hash,
share-side `.sidepart` hash-then-rename, and job-file hash-then-rename (placement) ->
`attr3-stage-fixture-job.ps1`'s generator-time bake AND the emitted job's own arrival-hash and
cache-`.partial` hash-then-rename (generator + emitted-job execution).

## Row A -- admission's internal read: git-blob drift compare + SHA256 pin derivation

- **Mechanism:** `Assert-AttrCudaFixtureCommittedBytes` opens the fixture ONCE via
  `[IO.File]::Open($full, FileMode.Open, FileAccess.Read, FileShare.Read)` -- no Write, no Delete
  share -- reads it into one buffer, and derives both the git-blob drift compare and the returned
  SHA256 pin from that same buffer.
- **Escape hatches:** none identified. `FileShare.Read` alone (no Delete) denies both a concurrent
  write AND a concurrent rename/delete of this exact path for the handle's whole lifetime.
- **Status: CLOSED.** Round 2d closed this structurally (one buffer, one handle). Round 2e added
  `test_the_source_derives_both_hashes_from_one_read_buffer` and
  `test_a_deny_write_handle_blocks_a_concurrent_writer_for_its_whole_lifetime`, proving the
  mechanism rather than merely asserting it.
- **Residual (coverage gap, not a defect):** `Get-AttrCudaGitBlobHashFromBytes`'s
  `Process.Start`/stream-I/O catch (guards a git-binary-vanishes-mid-launch failure) has no
  dedicated test for that specific catch path.

## Row B -- admission's returned pin -> local re-hash immediately before the share copy

- **Mechanism:** `Invoke-UmRunDrop` re-hashes `$SourcePath` (SHA256, independent read) immediately
  before calling `$Copier`, and binds the result back to admission's pinned SHA256; mismatch
  throws `UMRUN_FIXTURE_CONTENT_PIN_RACE`.
- **Escape hatches:** none within this window itself -- a swap strictly BETWEEN admission
  returning and this re-hash is exactly what the bind-back catches (fail-closed, not fail-silent).
- **Status: CLOSED**, closed in round 2 (`fe6be215`), unchanged in logic since.

## Row C -- share-side `.sidepart`: round-trip hash verification -> rename

- **Mechanism:** `$Copier` copies the source to `$part`. A handle is then opened
  `[IO.File]::Open($part, FileMode.Open, FileAccess.Read, FileShare.Read -bor FileShare.Delete)`
  -- denies Write share; PERMITS Delete share (so the rename below can still succeed while the
  handle stays open). SHA256 is computed over that handle and compared to `$localSha`; on match,
  `Move-Item -LiteralPath $part -Destination $destination` is called while the SAME handle is
  still open (disposed only in the outer `finally`).
- **Escape hatch (round-2g finding, sol/fable, both keys independently): `FileShare.Delete` grants
  exactly what its name says.** Another process can delete `$part`'s directory entry (freeing the
  name) and a third process can create a brand-new file at the identical pathname, all while this
  handle is open -- the handle's Read/Write denial says nothing about the Delete/rename right it
  explicitly grants. `Move-Item` below resolves `$part` by PATHNAME at rename time, not by this
  handle's identity, so it would publish whatever now occupies that path. The new
  `test_a_write_attempt_between_the_share_hash_and_the_rename_is_denied` test proves only the
  narrower "an in-place WriteAllBytes is denied" property (confirmed by inspection: the hook does
  `[IO.File]::WriteAllBytes($p, ...)`, not a delete-then-recreate) -- it does not and cannot
  discriminate this pathname-identity race, because the deny-write handle does not defend against
  it.
- **Status: OPEN, corrected from round 2f's table (which recorded this CLOSED).** What remains:
  publishing by handle identity (e.g. a rename issued against the open handle itself, which
  requires native interop -- .NET has no managed API for a rename-by-handle) rather than by
  reopening the pathname at `Move-Item` time. Not fixed in round 2g: the correct fix is a native
  `FILE_RENAME_INFO`-via-handle call, which is a materially larger, harder-to-verify change than
  this round's scope; recording the honest OPEN state was judged the safer choice per this round's
  own ruling than shipping an unverified native-interop rename under time pressure.
- **Round 2g change:** applied the IDENTICAL mechanism (with the identical residual, documented
  above) to the job file's own temporary copy, which previously had NO verification at all --
  see the new Row G below.

## Row D -- generator bake: admission pin reused, no second independent read

- **Mechanism:** `attr3-stage-fixture-job.ps1` reuses `Get-UmRunFixtureAdmission`'s pinned SHA256
  instead of an independent second read; `-PostAdmissionHook` fires in the admission-return gap;
  mismatch throws `ATTR3_FIXTURE_CONTENT_PIN_RACE`.
- **Status: CLOSED**, closed in round 2 (`fe6be215`), unchanged in logic since. Round 2e only
  relocated the two tests exercising this race into a disposable throwaway repository (test
  hygiene, not a coverage or logic change).

## Row E -- emitted job: inbox arrival hash vs. baked pin

- **Mechanism:** the job the generator emits (runs on the measurement host, no repo checkout) reads
  the side-file that arrived in the inbox once (`$arrivedSha = Get-ShaLower $side`,
  `attr3-stage-fixture-job.ps1:211`) and compares it to `$FixtureSha256`, the value the generator
  baked into the template at generation time. Mismatch calls `Complete-Failed` (exit 4), fail-closed.
- **Escape hatches:** none within this window -- this is a single read compared to a value fixed
  before the job was ever written to disk; nothing after this read but before publication reuses
  the arrival read itself (see Row F for what happens to the bytes next).
- **Status: CLOSED.** Newly given its own row in round 2g; previously undocumented in the table
  (round-2f's table stopped at the generator bake, Row D).

## Row F -- emitted job: cache `.partial` hash-then-rename (round-2g finding, both keys)

- **Mechanism:** `Publish-AttrCudaFileCopy -Source $side -Destination $partialPath`
  (`attr3-stage-fixture-job.ps1:246`) copies the arrived side-file into the cache as `<name>.partial`
  using a plain `Copy-Item`, opening and closing its own handle. The very next statement,
  `(Get-ShaLower $partialPath) -ne $FixtureSha256` (`:247`), opens ANOTHER independent handle to
  hash it. On match, `Publish-AttrCudaFileMoveNonOverwriting -Source $partialPath -Destination
  $cachePath` (`:255`) calls `[IO.File]::Move($partialPath, $cachePath, $false)`, a THIRD,
  completely independent handle. Three separate handles on the same mutable path -- this is
  precisely the pre-round-2e shape Row C used to have (hash, close, rename, on an unheld path),
  not the "handle spans hash-to-rename" shape Row C (and now Row G) apply, so it does not even
  reach Row C's residual level of protection; nothing here denies a concurrent writer at any point.
- **This is the window sol's and fable's round-2f reports both named as missing from the table**
  (the round-2f prompt explicitly asked for "the job arrival hash" leg, which the table's declared
  path never reached).
- **Mitigation, not closure (both keys agree):** the downstream consumer,
  `playback-attr-3-cuda-job.ps1`, re-authenticates the cached clip against its own `-FixtureSha256`
  parameter and fails closed at `FIXTURE_CONTENT_MISMATCH` before attribution runs -- so a swap
  landing in this window is caught before it can influence an attribution result. That bounds
  practical impact; it does not close this window, which is this table's job to enumerate.
- **Status: OPEN.** Not fixed in round 2g: closing it to Row C/G's level (a held handle spanning
  hash-to-rename) requires a new shared helper in `AttrCudaArtifacts.psm1` (there is no existing
  "hash-then-move-under-one-handle" helper; `Publish-AttrCudaFileMoveNonOverwriting` is a generic,
  multiply-used helper whose contract would need to change for every caller, or a new sibling
  function added and re-embedded into the job template) -- judged out of this round's scope under
  its own scope-discipline directive. Recorded OPEN rather than silently narrowed.

## Row G -- job-file placement: `.job.tmp` hash-then-rename (round-2g fix, item 5)

- **Mechanism, before this round:** `Invoke-UmRunDrop` copied `$ScriptPath` to `$tmp` and called
  `Move-Item -LiteralPath $tmp -Destination $final` with **no verification of any kind** --
  contradicting the module's own header invariant (which claimed every share-side temporary is
  re-read and verified before its rename). This was the single executable artifact placed into the
  inbox, placed unverified.
- **Mechanism, after this round:** the identical mechanism Row C uses -- one handle, opened
  `FileShare.Read -bor FileShare.Delete` (deny-write, permit-delete), spans the round-trip SHA256
  hash and the `Move-Item` rename. Mismatch throws the new token `UMRUN_JOB_VERIFY_FAILED`.
- **Escape hatch:** identical to Row C's -- `FileShare.Delete` permits a delete-and-recreate at
  `$tmp`'s pathname while the handle is held, and `Move-Item` resolves that pathname, not the
  handle's identity.
- **Status: OPEN, but materially improved from round 2f's "no verification at all."** Closing the
  residual requires the same native-interop rename-by-handle work named as out of scope for Row C;
  applying it here inherits, rather than introduces, that limitation.

## Round-2e/2g classification theme (cross-cutting, not a single check/use window)

Splitting "could not determine admissibility" (environmental/operational failure) from "definite
refusal" (an evidenced verdict about the fixture's bytes):

| Token | Where | Status |
|---|---|---|
| `ATTR3_FIXTURE_HEAD_LOOKUP_UNAVAILABLE` | `git rev-parse HEAD:<path>` (`AttrCudaArtifacts.psm1:518-527`) fails for a reason other than genuine non-commitment | CLOSED, tested (round 2e) |
| `UMRUN_FIXTURE_CONTENT_PIN_UNAVAILABLE` | `Import-Module` of a present-but-broken bachelor module | CLOSED, tested (round 2e) |
| `UMRUN_FIXTURE_ADMISSION_PATH_RESOLUTION_UNAVAILABLE` | `Get-Item`/`ResolveLinkTarget` raw throw in `Get-UmRunFixtureAdmission` | **OPEN -- coverage gap** (catch exists, no test drives the specific TOCTOU throw path) |
| `UMRUN_SIDEFILE_ADMISSION_INDETERMINATE` / `ATTR3_FIXTURE_ADMISSION_INDETERMINATE` | side-file and generator admission-refusal callers branch on `.Indeterminate` | CLOSED, tested |
| `ATTR3_FIXTURE_GIT_UNAVAILABLE` (repository-discovery leg) | **round-2g fix:** `git rev-parse --show-toplevel` (`AttrCudaArtifacts.psm1:465`) used to discard stderr (`2>$null`) and fold EVERY failure -- dubious ownership, corrupted config, I/O error -- into the definite `ATTR3_FIXTURE_NOT_IN_A_REPO`. Now inspects stderr the same way the HEAD:<path> lookup already did: only git's own "not a git repository" text is the definite refusal; everything else is `ATTR3_FIXTURE_GIT_UNAVAILABLE` (already a registered indeterminate token) | **CLOSED, round 2g. No dedicated new test added this round -- recorded as a coverage gap, not re-verified by a race/injection test.** |
| the classification mechanism itself | `UmRunDrop.psm1`'s `$UmRunIndeterminateAdmissionTokens` | **Named honestly, round 2g: this is a TOKEN ALLOWLIST over a closed, documented set of throw sites, not an exhaustive classification.** It is only as complete as every throw site actually using one of its tokens. This round found and fixed one gap where a throw site used a definite token it should not have (the show-toplevel leg above). Two narrower, prose-only escape hatches remain and are named in `Assert-AttrCudaFixtureCommittedBytes`'s docstring rather than fixed: an unanchored `invalid object name` stderr match, and a large-fixture OOM that surfaces as a raw untokened exception instead of `ATTR3_FIXTURE_CONTENT_PIN_UNBINDABLE`. |

## Known-item accounting (round-2g completion of item 4)

- **`.gitattributes` clean-filter preimage -- proper mechanism row (was tally-only before this round).**
  `Get-AttrCudaGitBlobHashFromBytes` reproduces git's blob hash by piping the in-memory buffer to
  `git hash-object --stdin --path <RelativePath>`, which selects clean filters (`.gitattributes`
  `filter=`/`clean=` drivers, `core.autocrlf`) by the PATH given to `--path`, not by re-reading the
  file itself. **Known limitation, pre-existing, not investigated further this round:** an external
  filter driver whose behaviour depends on something other than piped byte content (e.g. reading
  the actual on-disk file path itself, or environment/working-directory state a real checkout
  provides that stdin does not) could disagree with what `git hash-object <path>` would compute by
  reading the file directly. No fixture in this repository currently exercises a custom external
  clean filter, so this is undefended and untested, not merely undocumented. **Status: OPEN,
  pre-existing, out of this round's scope.**
- **`core.autocrlf` in `_make_fixture_repo` -- accounted for, not a fix to the item above.**
  `test_playback_attr_3_cuda_behaviour.py`'s `_make_fixture_repo` (and the two
  `test_um_run_sidefiles.py` setUps plus `StageFixtureJobRaceTests`) pin `core.autocrlf=true`
  locally in the throwaway fixture repo. This is TEST DETERMINISM, not a code fix: it guarantees
  the suite's CRLF-normalisation fixture actually exercises git's clean-filter path on every host,
  regardless of the host's own global `core.autocrlf`, so the round-2d self-caught defect (a naive
  raw-byte SHA1 that ignored git's filters) stays caught by CI on any machine. It does not touch,
  and is not a disposition on, the clean-filter preimage gap named above -- that gap is about an
  UNTESTED kind of filter (external driver), not about whether autocrlf itself is exercised.
  **Status: test-hygiene item, CLOSED as test hygiene; does not change the disposition of the
  clean-filter preimage row above.**

## Tally

- **CLOSED:** Row A, Row B, Row D, Row E, Row G (materially improved from "no verification"), 4 of
  the classification tokens (`ATTR3_FIXTURE_HEAD_LOOKUP_UNAVAILABLE`,
  `UMRUN_FIXTURE_CONTENT_PIN_UNAVAILABLE`, the indeterminate-branching callers, and the round-2g
  `ATTR3_FIXTURE_GIT_UNAVAILABLE` show-toplevel fix) = **9 CLOSED**.
- **OPEN:** Row C (corrected from a false CLOSED -- pathname-identity race past a
  `FileShare.Delete` handle), Row F (missing window, newly enumerated -- three independent handles
  on the emitted job's `.partial`, mitigated but not closed by downstream re-verification), Row G's
  own inherited residual (same pathname-identity race as Row C, on the job file), the
  classification allowlist's two named prose-only edges, `UMRUN_FIXTURE_ADMISSION_PATH_RESOLUTION_UNAVAILABLE`'s
  coverage gap, Row A's git-binary-vanishes coverage gap, the `ATTR3_FIXTURE_GIT_UNAVAILABLE`
  show-toplevel fix's own missing dedicated test, and the pre-existing clean-filter preimage gap =
  **8 OPEN** (counting Row C and Row G's shared residual once each, since they are the same defect
  shape in two places).

## Verification, round 2g

- `tools/repo_hygiene/test_um_run_sidefiles.py`: 31 passed, 10 subtests passed. Exit code 0 taken
  directly from the pytest invocation (not through a pipe).
- `tools/repo_hygiene/test_playback_attr_3_cuda_behaviour.py`: 75 passed, 103 subtests passed. Exit
  code 0 taken directly.
- `tools/repo_hygiene/test_attr3_stage_fixture_job.py`: 12 passed. Exit code 0 taken directly.
- Total: 118 passed (31 + 75 + 12), matching the round-2f hub's reported total; HEAD unchanged
  (`bbfb2943`) and `git status --porcelain` clean immediately before this round's first edit.
- **No new test was added this round** for either code fix (item 3's show-toplevel classification
  fix, or item 5's job round-trip verification) -- both are covered only by the existing suites
  continuing to pass, not by a dedicated new race/injection test. Recorded here rather than
  implied: this is a real coverage gap on both fixes, consistent with this round's own rule against
  claiming more than what was actually done.
