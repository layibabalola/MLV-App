"""Behavioural tests for um-run.ps1 side-file placement (tools/profiling/UmRunDrop.psm1).

The placement code is exercised two ways:
  - through the module with an OBSERVING or FAULTY copier, which is what proves the properties a
    final-state check cannot (sol, PR #135 r1): the share-side hash comparison actually rejects
    altered bytes, every side-file is renamed into place before the job's temporary copy is even
    written, and renames never overwrite a destination that appears concurrently;
  - end to end through um-run.ps1 against a fake agent share with a fresh heartbeat and no agent,
    so each submission times out right after its drop.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UM_RUN = ROOT / "tools" / "profiling" / "um-run.ps1"
MODULE = ROOT / "tools" / "profiling" / "UmRunDrop.psm1"
PWSH = shutil.which("pwsh")
GIT = shutil.which("git")

# Tracked-fixture admission asks git whether the file is tracked, so those cases need git.
requires_git = unittest.skipIf(GIT is None, "git is not on PATH")


def _q(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "um-run.ps1 targets Windows agent shares")
class _Share(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="umrun-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.share = self.tmp / "agent"
        self.inbox = self.share / "inbox"
        self.outbox = self.share / "outbox"
        self.inbox.mkdir(parents=True)
        self.outbox.mkdir()
        (self.share / "heartbeat.txt").write_text("alive", encoding="utf-8")
        self.local = self.tmp / "local"
        self.local.mkdir()
        self.job = self.local / "demo.job.ps1"
        self.job.write_text("Write-Output 'hi'\n", encoding="utf-8")
        self.side = self.local / "demo-source.zip"
        self.side.write_bytes(os.urandom(4096))
        self.log = self.tmp / "copies.log"

    def names(self) -> list[str]:
        return sorted(p.name for p in self.inbox.iterdir())

    def drop(self, copier: str, *, side: list[Path] | None = None, job_id: str = "demo",
             job_timeout_sec: int | None = None, orphan_grace_sec: int | None = None,
             before_job_visible: str | None = None,
             after_meta_tmp_written: str | None = None) -> subprocess.CompletedProcess:
        sides = ",".join(_q(p) for p in (side if side is not None else [self.side]))
        timeout_arg = "" if job_timeout_sec is None else f" -JobTimeoutSec {job_timeout_sec}"
        grace_arg = "" if orphan_grace_sec is None else f" -OrphanMetaGraceSec {orphan_grace_sec}"
        script = self.tmp / "drop.ps1"
        preamble = (
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module {_q(MODULE)} -Force\n"
            f"$log = {_q(self.log)}\n"
            f"$copier = {copier}\n"
        )
        hook_args = ""
        if before_job_visible is not None:
            preamble += f"$beforeJobVisible = {before_job_visible}\n"
            hook_args += " -TestHookBeforeJobVisible $beforeJobVisible"
        if after_meta_tmp_written is not None:
            preamble += f"$afterMetaTmpWritten = {after_meta_tmp_written}\n"
            hook_args += " -TestHookAfterMetaTmpWritten $afterMetaTmpWritten"
        script.write_text(
            preamble +
            "try {\n"
            f"  Invoke-UmRunDrop -Inbox {_q(self.inbox)} -Outbox {_q(self.outbox)} -ScriptPath {_q(self.job)} "
            f"-JobId '{job_id}' -SideFile @({sides}){timeout_arg}{grace_arg}{hook_args} -Copier $copier\n"
            "} catch { Write-Output ('THREW ' + $_.Exception.Message) }\n",
            encoding="utf-8",
        )
        return subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                              capture_output=True, text=True)


OBSERVING = "{ param($s, $d) Add-Content -LiteralPath $log -Value $d; Copy-Item -LiteralPath $s -Destination $d }"
CORRUPTING = ("{ param($s, $d) Copy-Item -LiteralPath $s -Destination $d; "
              "if ($d -like '*.sidepart') { [IO.File]::AppendAllText($d, 'x') } }")
RACING_SIDE = ("{ param($s, $d) Copy-Item -LiteralPath $s -Destination $d; "
               "if ($d -like '*.sidepart') { $final = $d -replace '\\.[0-9a-f]{32}\\.sidepart$', ''; "
               "[IO.File]::WriteAllText($final, 'another submitter') } }")
RACING_JOB = ("{ param($s, $d) Copy-Item -LiteralPath $s -Destination $d; "
              "if ($d -like '*.job.tmp') { $final = $d -replace '\\.[0-9a-f]{32}\\.job\\.tmp$', '.job.ps1'; "
              "[IO.File]::WriteAllText($final, 'Write-Output concurrent') } }")
# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (fable/sol major 1), round 4 (sol major: reordered so
# the job's own bytes are copied BEFORE metadata is written at all -- see UmRunDrop.psm1's own
# header). The job's own copy is made to fail, simulating the share hiccup / concurrent-rename
# class of failure fable's review used as its repro; because it fails before metadata is ever
# written, this now proves the weaker "nothing partial survives the very first copy" property --
# see test_a_racing_job_rename_with_a_budget_rolls_back_its_own_metadata below for the round-4
# rollback proof that actually exercises metadata-then-job-rename-fails.
JOB_COPY_FAILS = ("{ param($s, $d) if ($d -like '*.job.tmp') { throw 'INJECTED_JOB_COPY_FAILURE' }; "
                   "Copy-Item -LiteralPath $s -Destination $d }")


class UmRunDropModuleTests(_Share):
    def test_every_side_file_is_in_place_before_the_job_temp_is_written(self) -> None:
        second = self.local / "demo-build.json"
        second.write_text("{}", encoding="utf-8")
        proc = self.drop(OBSERVING, side=[self.side, second])
        self.assertIn("UMRUN_JOBID=demo", proc.stdout, proc.stdout + proc.stderr)
        copies = [Path(line).name for line in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(copies), 3, copies)
        self.assertTrue(copies[0].endswith(".sidepart") and copies[1].endswith(".sidepart"), copies)
        self.assertTrue(copies[2].endswith(".job.tmp"), copies)
        # unique per-submission temporary names, never the bare shared forms
        self.assertNotIn("demo-source.zip.sidepart", copies)
        self.assertNotIn("demo.job.tmp", copies)
        self.assertEqual(self.names(), ["demo-build.json", "demo-source.zip", "demo.job.ps1"])

    # ---- agent-side job budget (ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1) --------------------------
    # The agent reads inbox\<id>.meta.json when it claims a job and honours timeoutSec in 1..86400,
    # falling back to its own 1800 s default when the file is missing or unparseable. Before this,
    # nothing wrote that file: um-run's -TimeoutSec reached only the client poll, so a multi-GB
    # placement asked to take an hour was killed at 30 minutes, twice, discarding a ~50 min transfer.

    def test_the_job_budget_is_in_place_before_the_job_becomes_visible(self) -> None:
        # sol round 4 minor: this used to prove metadata-before-the-job's-OWN-temporary-copy (a
        # copier hook fired when demo.job.tmp was written) -- the wrong instant, since round 4
        # reordered the module to copy the job's bytes to that same temporary FIRST, before
        # metadata is written at all (see UmRunDrop.psm1's own header). The real contract the agent
        # depends on is metadata-before-the-job-becoming-VISIBLE (the rename to demo.job.ps1) --
        # -TestHookBeforeJobVisible fires at exactly that instant, whatever the module's internal
        # copy order is.
        hook = ("{ Add-Content -LiteralPath " + _q(self.log) + " -Value ('meta-present-before-visible=' + "
                "(Test-Path -LiteralPath (Join-Path " + _q(self.inbox) + " 'demo.meta.json'))) }")
        proc = self.drop(OBSERVING, side=[], job_timeout_sec=3600, before_job_visible=hook)
        self.assertIn("UMRUN_JOBID=demo", proc.stdout, proc.stdout + proc.stderr)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertIn("meta-present-before-visible=True", lines, lines)
        self.assertEqual(self.names(), ["demo.job.ps1", "demo.meta.json"])
        meta = json.loads((self.inbox / "demo.meta.json").read_text(encoding="ascii"))
        self.assertEqual(meta["timeoutSec"], 3600)
        self.assertEqual(meta["jobId"], "demo")

    def test_the_jobs_own_bytes_are_copied_before_metadata_is_written(self) -> None:
        # The other half of the round-4 reorder: at the moment the job's OWN temporary is copied,
        # metadata must NOT yet exist -- it is published only once those bytes already sit on the
        # share, narrowing the window in which metadata is visible with no job to the rename alone.
        proc = self.drop(OBSERVING, side=[], job_timeout_sec=3600)
        self.assertIn("UMRUN_JOBID=demo", proc.stdout, proc.stdout + proc.stderr)
        copies = [Path(line).name for line in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(copies), 1, copies)
        self.assertTrue(copies[0].endswith(".job.tmp"), copies)

    def test_no_metadata_is_written_when_no_budget_is_requested(self) -> None:
        proc = self.drop(OBSERVING, side=[])
        self.assertIn("UMRUN_JOBID=demo", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo.job.ps1"],
                         "a caller that names no budget must leave the agent on its own default")

    def test_a_budget_outside_the_agents_accepted_range_is_refused_and_nothing_is_placed(self) -> None:
        for bad in (86401, -1):
            with self.subTest(timeout=bad):
                for leftover in self.inbox.iterdir():
                    leftover.unlink()
                proc = self.drop(OBSERVING, side=[], job_timeout_sec=bad)
                self.assertIn("THREW UMRUN_JOB_TIMEOUT_INVALID", proc.stdout, proc.stdout + proc.stderr)
                self.assertEqual(self.names(), [],
                                 "a refused budget must leave neither metadata nor a job")

    def test_an_invalid_budget_is_refused_before_any_sidefile_is_copied(self) -> None:
        # fable/sol minor 1: the range check used to run AFTER the side-file loop, so an invalid
        # -JobTimeoutSec was only discovered after a possibly multi-GB transfer had already happened.
        proc = self.drop(OBSERVING, job_timeout_sec=86401)   # self.side is a real side-file by default
        self.assertIn("THREW UMRUN_JOB_TIMEOUT_INVALID", proc.stdout, proc.stdout + proc.stderr)
        self.assertFalse(self.log.exists(), "no copy may happen before the budget is validated")
        self.assertEqual(self.names(), [])

    def test_a_failed_job_copy_removes_the_metadata_so_a_retry_with_the_same_job_id_succeeds(self) -> None:
        # fable/sol major 1: metadata was placed and never rolled back when the job's OWN copy then
        # failed. Deterministic job ids (playback-attr-3-cuda-dll-job.ps1, ...-stage-job.ps1) have no
        # attempt nonce, so every retry of the same id was then refused (UMRUN_JOBID_IN_USE) against
        # a job/result that never actually existed -- this is the SUBMIT-RETRY-1 bug itself.
        first = self.drop(JOB_COPY_FAILS, side=[], job_timeout_sec=3600)
        self.assertIn("THREW", first.stdout, first.stdout + first.stderr)
        self.assertNotIn("UMRUN_JOBID=demo", first.stdout)
        self.assertEqual(self.names(), [],
                         "a failed job placement must leave neither its own metadata nor a job behind")

        retry = self.drop(OBSERVING, side=[], job_timeout_sec=3600)
        self.assertIn("UMRUN_JOBID=demo", retry.stdout, retry.stdout + retry.stderr)
        self.assertEqual(self.names(), ["demo.job.ps1", "demo.meta.json"],
                         "the retry must succeed exactly as if the first attempt had never happened")

    def test_a_racing_job_rename_with_a_budget_rolls_back_its_own_metadata(self) -> None:
        # sol round 4 major (item 4/5): the "budgeted final-job rename refusal" coverage sol named
        # as absent. With the round-4 reorder, metadata is published only just before the job's
        # final rename -- this proves that when that LAST rename loses a race (another submitter's
        # job.ps1 appears first), this call's own metadata is rolled back exactly like the
        # job-copy-failure case above, never left to brick a later retry.
        proc = self.drop(RACING_JOB, side=[], job_timeout_sec=3600)
        self.assertIn("THREW UMRUN_JOBID_IN_USE", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual((self.inbox / "demo.job.ps1").read_text(encoding="utf-8"), "Write-Output concurrent")
        self.assertEqual(self.names(), ["demo.job.ps1"],
                         "a lost job-rename race must roll back this call's own metadata, not leave it behind")

    def test_a_torn_metadata_write_is_rejected_by_readback_verification(self) -> None:
        # sol round 4 minor: "removing metadata readback verification would not fail any test" --
        # -TestHookAfterMetaTmpWritten corrupts the metadata temp file after it is written but
        # before the write-back comparison, proving that comparison actually rejects a torn write
        # rather than merely existing, untested, in the source.
        hook = "{ param($p) [IO.File]::AppendAllText($p, 'TORN') }"
        proc = self.drop(OBSERVING, side=[], job_timeout_sec=3600, after_meta_tmp_written=hook)
        self.assertIn("THREW UMRUN_JOB_METADATA_VERIFY_FAILED", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [], "a torn metadata write must leave neither metadata nor a job")

    def test_an_orphaned_metadata_from_a_hard_interruption_is_self_healed_after_its_grace_period(self) -> None:
        # sol round 4 major (item 2/4): metadata with no job and no result can only be left by a
        # submission hard-interrupted (killed, not thrown) between publishing metadata and exposing
        # the job -- neither agent ever consumes metadata without its paired job. -OrphanMetaGraceSec
        # 0 simulates that grace period having already elapsed: the retry must reclaim the JobId
        # instead of being bricked forever, which is the SUBMIT-RETRY-1 bug this round closes for
        # the interrupt path (round 2 already closed it for the thrown-exception path).
        (self.inbox / "demo.meta.json").write_text('{"jobId":"demo","timeoutSec":99}', encoding="ascii")
        proc = self.drop(OBSERVING, side=[], job_timeout_sec=3600, orphan_grace_sec=0)
        self.assertIn("removed orphaned metadata", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("UMRUN_JOBID=demo", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo.job.ps1", "demo.meta.json"])
        meta = json.loads((self.inbox / "demo.meta.json").read_text(encoding="ascii"))
        self.assertEqual(meta["timeoutSec"], 3600, "the retry's own fresh metadata, never the stale orphan's")

    def test_an_orphaned_metadata_still_inside_its_grace_period_is_treated_as_possibly_live(self) -> None:
        # The other half: immediately after it appears, orphaned metadata is indistinguishable from
        # a live submission mid-rename, so the default grace period still refuses the retry -- this
        # is the same outcome as test_metadata_that_appears_concurrently_is_not_overwritten below,
        # named here to make the grace-period boundary explicit.
        (self.inbox / "demo.meta.json").write_text('{"jobId":"demo","timeoutSec":99}', encoding="ascii")
        proc = self.drop(OBSERVING, side=[], job_timeout_sec=3600)   # default -OrphanMetaGraceSec
        self.assertIn("THREW UMRUN_JOBID_IN_USE", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(json.loads((self.inbox / "demo.meta.json").read_text(encoding="ascii"))["timeoutSec"], 99)

    def test_a_stale_orphan_that_cannot_be_removed_is_surfaced_not_silently_kept(self) -> None:
        # sol round 4 minor (item 2/4, "cleanup failure"): the round-2 rollback cleanup was
        # SilentlyContinue, so a share hiccup that broke a removal was invisible. The self-heal
        # removal above is not: a stale orphan that fails to delete (here, a non-empty directory
        # occupying the meta path -- Remove-Item without -Recurse refuses a non-empty directory)
        # surfaces a clear UMRUN_JOBID_IN_USE refusal instead of silently proceeding to place a
        # job the agent could claim against unremovable, unrelated metadata.
        meta_dir = self.inbox / "demo.meta.json"
        meta_dir.mkdir()
        (meta_dir / "unrelated.txt").write_bytes(b"bytes that must survive untouched")
        proc = self.drop(OBSERVING, side=[], job_timeout_sec=3600, orphan_grace_sec=0)
        self.assertIn("THREW UMRUN_JOBID_IN_USE", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("could not be removed", proc.stdout, proc.stdout + proc.stderr)
        self.assertTrue(meta_dir.is_dir(), "an unremovable orphan must be left in place, not partially cleared")
        self.assertEqual((meta_dir / "unrelated.txt").read_bytes(), b"bytes that must survive untouched")
        self.assertEqual(self.names(), ["demo.meta.json"])

    def test_metadata_that_appears_concurrently_is_not_overwritten(self) -> None:
        (self.inbox / "demo.meta.json").write_text('{"jobId":"demo","timeoutSec":42}', encoding="ascii")
        proc = self.drop(OBSERVING, side=[], job_timeout_sec=3600)
        self.assertIn("THREW UMRUN_JOBID_IN_USE", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(json.loads((self.inbox / "demo.meta.json").read_text(encoding="ascii"))["timeoutSec"], 42)
        self.assertEqual(self.names(), ["demo.meta.json"], "no job may be dropped after a metadata conflict")

    def test_bytes_altered_on_the_share_are_refused_and_nothing_is_placed(self) -> None:
        proc = self.drop(CORRUPTING)
        self.assertIn("THREW UMRUN_SIDEFILE_VERIFY_FAILED", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [], "neither the side-file, its temporary, nor the job may remain")

    def test_a_side_file_that_appears_concurrently_with_other_bytes_is_not_overwritten(self) -> None:
        proc = self.drop(RACING_SIDE)
        self.assertIn("THREW UMRUN_SIDEFILE_CONFLICT", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual((self.inbox / "demo-source.zip").read_text(encoding="utf-8"), "another submitter")
        self.assertEqual(self.names(), ["demo-source.zip"], "no job may be dropped after a conflict")

    def test_a_job_that_appears_concurrently_is_not_replaced(self) -> None:
        proc = self.drop(RACING_JOB, side=[])
        self.assertIn("THREW UMRUN_JOBID_IN_USE", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual((self.inbox / "demo.job.ps1").read_text(encoding="utf-8"), "Write-Output concurrent")
        self.assertEqual(self.names(), ["demo.job.ps1"])

    def test_a_trailing_dot_alias_of_a_job_file_is_refused(self) -> None:
        evil = self.local / "evil.job.ps1"
        evil.write_text("throw 'unintended'\n", encoding="utf-8")
        proc = self.drop(OBSERVING, side=[Path(str(evil) + ".")])
        self.assertIn("THREW UMRUN_SIDEFILE_NAME_INVALID", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [])

    # ---- tracked fixture admission (ATTR3-FIXTURE-REHEARSAL-1) -------------------------------
    # These use the REPOSITORY's own fixture clips, discovered by listing the directory rather than
    # by naming a file, and never assert on a media extension. Admission is anchored to this repo:
    # the source's real directory must BE <repo>/tests/fixtures/clips and the file must be tracked.

    # The admissible stems, mirrored from UmRunDrop.psm1. sol PR #137 r2 BLOCKER: the first version
    # of this helper took the SMALLEST tracked file in the directory, which is its 132-byte
    # README.md -- so the "fixture is admitted" test proved the bypass instead of the feature.
    FIXTURE_STEMS = ("tiny_dual_iso", "large_dual_iso")

    def repo_fixture(self) -> Path:
        fixtures = ROOT / "tests" / "fixtures" / "clips"
        if not fixtures.is_dir():
            self.skipTest("no repository fixture clips directory")
        clips = [f for f in fixtures.iterdir() if f.is_file() and f.stem in self.FIXTURE_STEMS]
        if not clips:
            self.skipTest("no repository fixture clips")
        return sorted(clips, key=lambda f: f.stat().st_size)[0]

    def repo_non_clip(self) -> Path:
        fixtures = ROOT / "tests" / "fixtures" / "clips"
        others = [f for f in fixtures.iterdir() if f.is_file() and f.stem not in self.FIXTURE_STEMS]
        if not others:
            self.skipTest("no tracked non-clip file in the fixtures directory")
        return others[0]

    def probe_source(self, path: Path) -> str:
        script = self.tmp / f"fixture-probe-{abs(hash(str(path))) % 10**8}.ps1"
        script.write_text(
            "Import-Module " + _q(MODULE) + " -Force\n"
            "Write-Output ('RESULT=' + (Test-UmRunTrackedFixtureSource -SourcePath " + _q(path) + "))\n",
            encoding="utf-8",
        )
        proc = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                              capture_output=True, text=True)
        return proc.stdout + proc.stderr

    @requires_git
    def test_a_tracked_repository_fixture_is_admitted_and_placed(self) -> None:
        clip = self.repo_fixture()
        proc = self.drop(OBSERVING, side=[clip])
        self.assertIn("UMRUN_JOBID=demo", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), sorted(["demo.job.ps1", clip.name]))
        self.assertEqual(
            hashlib.sha256((self.inbox / clip.name).read_bytes()).hexdigest(),
            hashlib.sha256(clip.read_bytes()).hexdigest(),
        )

    @requires_git
    def test_the_same_bytes_and_name_from_another_directory_are_refused(self) -> None:
        clip = self.repo_fixture()
        elsewhere = self.local / "downloads"
        elsewhere.mkdir()
        impostor = elsewhere / clip.name
        shutil.copy2(clip, impostor)
        proc = self.drop(OBSERVING, side=[impostor])
        self.assertIn("THREW UMRUN_SIDEFILE_NAME_INVALID", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [])

    @requires_git
    def test_a_lookalike_fixtures_tree_outside_the_repository_is_refused(self) -> None:
        # sol PR #137 r1 BLOCKER: a lexical segment match admitted any tests\fixtures\clips tree.
        clip = self.repo_fixture()
        lookalike = self.local / "tests" / "fixtures" / "clips"
        lookalike.mkdir(parents=True)
        impostor = lookalike / clip.name
        shutil.copy2(clip, impostor)
        self.assertIn("RESULT=False", self.probe_source(impostor))
        proc = self.drop(OBSERVING, side=[impostor])
        self.assertIn("THREW UMRUN_SIDEFILE_NAME_INVALID", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [])

    @requires_git
    def test_a_path_through_a_junction_to_a_lookalike_tree_is_refused(self) -> None:
        clip = self.repo_fixture()
        lookalike = self.local / "outside" / "tests" / "fixtures" / "clips"
        lookalike.mkdir(parents=True)
        shutil.copy2(clip, lookalike / clip.name)
        link = self.local / "link"
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path {_q(link)} -Target {_q(self.local / 'outside')} | Out-Null"],
            capture_output=True, text=True)
        if made.returncode != 0 or not link.exists():
            self.skipTest(f"cannot create a junction here: {made.stderr}")
        through = link / "tests" / "fixtures" / "clips" / clip.name
        self.assertIn("RESULT=False", self.probe_source(through))

    @requires_git
    def test_a_tracked_non_clip_file_in_the_fixtures_directory_is_refused(self) -> None:
        # sol PR #137 r2 BLOCKER: "tracked under tests/fixtures/clips" admitted that directory's
        # README, whose extension the allowlist would otherwise refuse.
        other = self.repo_non_clip()
        self.assertIn("RESULT=False", self.probe_source(other))
        proc = self.drop(OBSERVING, side=[other])
        self.assertIn("THREW UMRUN_SIDEFILE_NAME_INVALID", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [])

    @requires_git
    def test_an_untracked_file_inside_the_real_fixtures_directory_is_refused(self) -> None:
        # A file merely dropped into the repository's fixtures directory is not a fixture.
        fixtures = ROOT / "tests" / "fixtures" / "clips"
        intruder = fixtures / "tiny_dual_iso.umrunprobe"  # a fixture STEM, deliberately: this must fail the TRACKED test
        intruder.write_bytes(os.urandom(64))
        self.addCleanup(lambda: intruder.exists() and intruder.unlink())
        self.assertIn("RESULT=False", self.probe_source(intruder))

    def test_names_that_are_not_plain_allowlisted_basenames_are_refused(self) -> None:
        cases = ["x.job.ps1", "x.job.tmp", "x.ps1", "x.sidepart", "x.zip.", "x.zip ", "CON.zip", "noext", "x..zip", "x.exe.cmd"]
        body = "Import-Module " + _q(MODULE) + " -Force\n"
        for name in cases:
            body += ("try { [void](Assert-UmRunSideFileName -Name " + _q(name) + " -Inbox " + _q(self.inbox) + "); "
                     "Write-Output ('ACCEPTED ' + " + _q(name) + ") } catch { Write-Output ('REFUSED ' + " + _q(name) + ") }\n")
        script = self.tmp / "names.ps1"
        script.write_text(body, encoding="utf-8")
        proc = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)], capture_output=True, text=True)
        for name in cases:
            with self.subTest(name=name):
                self.assertIn(f"REFUSED {name}", proc.stdout, proc.stdout + proc.stderr)
        for good in ["playback-attr-3-cuda-dllpair-4b20b66f7401-source.zip", "playback-attr-3-cuda-4b20b66f7401-build.json"]:
            proc2 = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                 f"Import-Module {_q(MODULE)} -Force; [void](Assert-UmRunSideFileName -Name {_q(good)} -Inbox {_q(self.inbox)}); 'OK'"],
                capture_output=True, text=True)
            self.assertIn("OK", proc2.stdout, proc2.stdout + proc2.stderr)

    def test_an_existing_result_for_the_job_id_is_refused(self) -> None:
        (self.outbox / "demo.result.json").write_text("{}", encoding="utf-8")
        proc = self.drop(OBSERVING)
        self.assertIn("THREW UMRUN_JOBID_IN_USE", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [])


class UmRunEndToEndTests(_Share):
    def submit(self, *extra: str, timeout_sec: str = "1", max_queue_wait_sec: str = "5") -> subprocess.CompletedProcess:
        # max_queue_wait_sec is small here on purpose: these tests use a FAKE share with no agent,
        # so the job is NEVER claimed, and the production default (86400s -- see um-run.ps1's own
        # comment on -MaxQueueWaitSec) would make every one of them hang. A real caller that expects
        # queue contention passes a larger value explicitly; a caller that does not gets a fast,
        # honestly-worded "never claimed" failure instead of a false "agent is down" diagnosis.
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(self.job), "-AgentShare", str(self.share),
             "-TimeoutSec", timeout_sec, "-PollSeconds", "1", "-MaxQueueWaitSec", max_queue_wait_sec, *extra],
            capture_output=True, text=True,
        )

    def test_side_file_and_job_land_with_identical_bytes(self) -> None:
        proc = self.submit("-SideFile", str(self.side), "-JobId", "demo")
        self.assertIn("side-file placed: demo-source.zip", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("Timed out", proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo-source.zip", "demo.job.ps1", "demo.meta.json"])
        self.assertEqual(hashlib.sha256((self.inbox / "demo-source.zip").read_bytes()).hexdigest(),
                         hashlib.sha256(self.side.read_bytes()).hexdigest())

    def test_a_timeout_of_zero_is_rejected_by_the_public_client(self) -> None:
        # sol major 3: 0 is a valid MODULE-level sentinel ("write no metadata"), but the public
        # client forwarded it unchanged into a grace formula that gave the CALLER only 5s of
        # patience while a compatible agent quietly fell back to its own (possibly 1800s+) default.
        # The public contract closes that collision by refusing 0 outright, before anything is
        # submitted.
        proc = self.submit(timeout_sec="0")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("UMRUN_TIMEOUT_SEC_INVALID", proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [], "a rejected -TimeoutSec must submit nothing at all")

    def test_the_callers_timeout_reaches_the_agent_as_the_jobs_own_budget(self) -> None:
        # The bug this closes: -TimeoutSec bounded only the client poll, so the agent ran every job
        # on its 1800 s default and killed a placement the caller had given an hour.
        # A real hour-long budget now makes the CLIENT wait an hour too (that is the point of the
        # change), so this asserts on the artifact and kills the poll rather than sitting out the
        # deadline: the metadata is written during the drop, long before any result could appear.
        proc = subprocess.Popen(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(self.job), "-AgentShare", str(self.share),
             "-TimeoutSec", "3600", "-PollSeconds", "1", "-JobId", "demo"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            meta_path = self.inbox / "demo.meta.json"
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not meta_path.exists():
                time.sleep(0.2)
            self.assertTrue(meta_path.exists(), "the drop must write the agent's job budget")
            meta = json.loads(meta_path.read_text(encoding="ascii"))
            self.assertEqual(meta["timeoutSec"], 3600)
            self.assertEqual(meta["jobId"], "demo")
        finally:
            proc.kill()
            proc.communicate()

    def test_an_unclaimed_job_times_out_with_a_queued_diagnosis_never_a_down_diagnosis(self) -> None:
        # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (fable/sol major 3): the OLD message
        # ("the job never ran or the agent is down") is an affirmative diagnosis the client has not
        # earned -- it cannot distinguish "dead agent" from "queued behind other work". Against this
        # fake, agent-less share the job is genuinely never claimed, so the message must say exactly
        # that, and nothing stronger.
        proc = self.submit("-JobId", "demo", max_queue_wait_sec="2")
        combined = proc.stdout + proc.stderr
        self.assertIn("was never claimed", combined, combined)
        self.assertNotIn("the job never ran or the agent is down", combined, combined)

    def test_a_late_claim_extends_the_deadline_past_the_original_queue_wait(self) -> None:
        # fable/sol major 3: the agent's deadline starts at CLAIM, not submission, and jobs are
        # processed sequentially, so a job stuck behind another can be claimed well after this
        # client's naive submit-time deadline would have expired. Simulates a compatible agent's
        # claim marker (running\<id>.started.json) appearing late, and proves the client's patience
        # shifts to (claim + budget + grace) instead of giving up at the old, submission-anchored one.
        running_dir = self.share / "running"
        proc = subprocess.Popen(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(self.job), "-AgentShare", str(self.share),
             "-TimeoutSec", "1", "-PollSeconds", "1", "-MaxQueueWaitSec", "3", "-JobId", "demo"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(1.5)   # inside the 3s queue-wait ceiling
            running_dir.mkdir(parents=True, exist_ok=True)
            marker = {"jobId": "demo", "startedUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            (running_dir / "demo.started.json").write_text(json.dumps(marker), encoding="ascii")
            # By the OLD queue-wait ceiling (3s from submission) the client must still be alive,
            # because it saw the claim and switched to (claim + 1s budget + 5s grace) instead.
            time.sleep(2.0)   # ~3.5s since submission: past the 3s queue ceiling
            self.assertIsNone(proc.poll(), "the client gave up even though the agent had claimed the job")
            stdout, stderr = proc.communicate(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        combined = (stdout or "") + (stderr or "")
        self.assertIn("claimed by the agent", combined, combined)
        self.assertNotIn("was never claimed", combined, combined)

    def test_a_receipt_written_during_the_final_sleep_is_still_read(self) -> None:
        # fable/sol major 3 (second half): the old loop tested its deadline BEFORE sleeping, so a
        # receipt published during the final poll sleep was skipped -- the deadline had already
        # passed by the time the loop would have looked again. PollSeconds is deliberately larger
        # than MaxQueueWaitSec so the run's only sleep straddles the deadline.
        proc = subprocess.Popen(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(self.job), "-AgentShare", str(self.share),
             "-TimeoutSec", "1", "-PollSeconds", "3", "-MaxQueueWaitSec", "1", "-JobId", "demo"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(1.5)   # during the loop's single sleep, after the 1s queue-wait ceiling
            result = {"jobId": "demo", "exitCode": 0, "stdout": "late but real", "stderr": "",
                      "timeoutSec": 1, "timedOut": False}
            tmp = self.outbox / "demo.result.tmp"
            tmp.write_text(json.dumps(result), encoding="ascii")
            tmp.replace(self.outbox / "demo.result.json")
            stdout, stderr = proc.communicate(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        # Checked by exit code and absence of the timeout throw, not by scraping stdout for the
        # returned object's fields: PowerShell's default console formatting of a returned
        # PSCustomObject is not a stable text contract to assert against.
        combined = (stdout or "") + (stderr or "")
        self.assertEqual(proc.returncode, 0, combined)
        self.assertNotIn("Timed out", combined, combined)

    def test_a_different_same_named_file_is_refused_and_no_job_is_dropped(self) -> None:
        (self.inbox / "demo-source.zip").write_bytes(b"someone else's bytes")
        proc = self.submit("-SideFile", str(self.side), "-JobId", "demo")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("UMRUN_SIDEFILE_CONFLICT", proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo-source.zip"])

    def test_an_identical_file_already_present_is_accepted(self) -> None:
        shutil.copy2(self.side, self.inbox / "demo-source.zip")
        proc = self.submit("-SideFile", str(self.side), "-JobId", "demo")
        self.assertIn("already present with matching sha256", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo-source.zip", "demo.job.ps1", "demo.meta.json"])

    def test_semicolon_list_places_every_file(self) -> None:
        second = self.local / "demo-build.json"
        second.write_text("{}", encoding="utf-8")
        proc = self.submit("-SideFile", f"{self.side};{second}", "-JobId", "demo")
        self.assertEqual(self.names(), ["demo-build.json", "demo-source.zip", "demo.job.ps1", "demo.meta.json"], proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
