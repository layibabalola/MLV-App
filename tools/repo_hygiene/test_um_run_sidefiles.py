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
             job_timeout_sec: int | None = None) -> subprocess.CompletedProcess:
        sides = ",".join(_q(p) for p in (side if side is not None else [self.side]))
        timeout_arg = "" if job_timeout_sec is None else f" -JobTimeoutSec {job_timeout_sec}"
        script = self.tmp / "drop.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module {_q(MODULE)} -Force\n"
            f"$log = {_q(self.log)}\n"
            f"$copier = {copier}\n"
            "try {\n"
            f"  Invoke-UmRunDrop -Inbox {_q(self.inbox)} -Outbox {_q(self.outbox)} -ScriptPath {_q(self.job)} "
            f"-JobId '{job_id}' -SideFile @({sides}){timeout_arg} -Copier $copier\n"
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
# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1. The metadata is written with Set-Content, not through the
# copier, so ordering cannot be read off the copy log alone: this copier records, at the moment the
# JOB's temporary is written, whether the metadata is already in place. That is the property the
# agent depends on -- it may claim the job the instant the job file appears.
META_ORDER = ("{ param($s, $d) if ($d -like '*.job.tmp') { "
              "Add-Content -LiteralPath $log -Value ('meta-present-at-job-copy=' + "
              "(Test-Path -LiteralPath (Join-Path (Split-Path -Parent $d) 'demo.meta.json'))) }; "
              "Add-Content -LiteralPath $log -Value $d; Copy-Item -LiteralPath $s -Destination $d }")


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
        proc = self.drop(META_ORDER, side=[], job_timeout_sec=3600)
        self.assertIn("UMRUN_JOBID=demo", proc.stdout, proc.stdout + proc.stderr)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertIn("meta-present-at-job-copy=True", lines, lines)
        self.assertEqual(self.names(), ["demo.job.ps1", "demo.meta.json"])
        meta = json.loads((self.inbox / "demo.meta.json").read_text(encoding="ascii"))
        self.assertEqual(meta["timeoutSec"], 3600)
        self.assertEqual(meta["jobId"], "demo")

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
    def submit(self, *extra: str, timeout_sec: str = "1") -> subprocess.CompletedProcess:
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(self.job), "-AgentShare", str(self.share),
             "-TimeoutSec", timeout_sec, "-PollSeconds", "1", *extra],
            capture_output=True, text=True,
        )

    def test_side_file_and_job_land_with_identical_bytes(self) -> None:
        proc = self.submit("-SideFile", str(self.side), "-JobId", "demo")
        self.assertIn("side-file placed: demo-source.zip", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("Timed out", proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo-source.zip", "demo.job.ps1", "demo.meta.json"])
        self.assertEqual(hashlib.sha256((self.inbox / "demo-source.zip").read_bytes()).hexdigest(),
                         hashlib.sha256(self.side.read_bytes()).hexdigest())

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

    def test_the_client_poll_outlasts_the_agent_budget_so_a_receipt_can_be_read(self) -> None:
        # Equal deadlines race: the client would throw its own generic timeout at the same instant
        # the agent writes the receipt that says WHY the job ended.
        proc = self.submit("-JobId", "demo")
        self.assertIn("agent budget 1s + 5s grace", proc.stdout + proc.stderr)

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
