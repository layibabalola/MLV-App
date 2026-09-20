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
              module: Path | None = None, repo_root: Path | None = None) -> subprocess.CompletedProcess:
        sides = ",".join(_q(p) for p in (side if side is not None else [self.side]))
        script = self.tmp / "drop.ps1"
        repo_root_arg = f" -RepoRoot {_q(repo_root)}" if repo_root is not None else ""
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module {_q(module or MODULE)} -Force\n"
            f"$log = {_q(self.log)}\n"
            f"$copier = {copier}\n"
            "try {\n"
            f"  Invoke-UmRunDrop -Inbox {_q(self.inbox)} -Outbox {_q(self.outbox)} -ScriptPath {_q(self.job)} "
            f"-JobId '{job_id}' -SideFile @({sides}) -Copier $copier{repo_root_arg}\n"
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

    @requires_git
    def test_a_bachelor_less_host_refuses_a_tracked_fixture_rather_than_admitting_it_unpinned(self) -> None:
        # fable MINOR: nothing previously proved the module-absent path fails CLOSED end to end.
        # A "if present assert, else return" rewrite of Test-UmRunFixtureContentPin would make
        # Get-UmRunFixtureAdmission treat the tracked fixture below as admitted-but-unverified,
        # which -- because admission is what EXEMPTS a fixture from the extension allowlist --
        # would place these bytes anyway despite the media extension the allowlist refuses.
        # Round 2d: this used to RENAME the real, tracked bachelor module in the live working
        # tree and restore it in a finally -- a hard-killed run left the checkout without it and
        # dirty. A COPY of UmRunDrop.psm1 with no bachelor/ next to it produces the identical
        # module-absent condition instead ($script:AttrCudaArtifactsModulePath is derived from the
        # importing copy's own $PSScriptRoot), so the real tree is never touched; -RepoRoot pins
        # the copy back to the real repository so the real fixture is still what gets admitted.
        clip = self.repo_fixture()
        module_copy = self.tmp / "no-bachelor" / "UmRunDrop.psm1"
        module_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MODULE, module_copy)
        proc = self.drop(OBSERVING, side=[clip], module=module_copy, repo_root=ROOT)
        self.assertIn("THREW UMRUN_SIDEFILE_NAME_INVALID", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [], "a bachelor-less host must never place an unverified fixture")

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
    def submit(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(self.job), "-AgentShare", str(self.share),
             "-TimeoutSec", "1", "-PollSeconds", "1", *extra],
            capture_output=True, text=True,
        )

    def test_side_file_and_job_land_with_identical_bytes(self) -> None:
        proc = self.submit("-SideFile", str(self.side), "-JobId", "demo")
        self.assertIn("side-file placed: demo-source.zip", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("Timed out", proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo-source.zip", "demo.job.ps1"])
        self.assertEqual(hashlib.sha256((self.inbox / "demo-source.zip").read_bytes()).hexdigest(),
                         hashlib.sha256(self.side.read_bytes()).hexdigest())

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
        self.assertEqual(self.names(), ["demo-source.zip", "demo.job.ps1"])

    def test_semicolon_list_places_every_file(self) -> None:
        second = self.local / "demo-build.json"
        second.write_text("{}", encoding="utf-8")
        proc = self.submit("-SideFile", f"{self.side};{second}", "-JobId", "demo")
        self.assertEqual(self.names(), ["demo-build.json", "demo-source.zip", "demo.job.ps1"], proc.stdout + proc.stderr)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run([GIT, *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _env_without_git() -> dict[str, str] | None:
    """A copy of os.environ with every PATH entry that carries git.exe removed, or None if a probe
    subprocess still finds git afterwards (some hosts resolve git through a mechanism PATH-editing
    alone cannot defeat, e.g. an app-execution alias) -- callers skip rather than false-fail then."""
    env = dict(os.environ)
    kept = [part for part in env.get("PATH", "").split(os.pathsep)
            if part and not (Path(part) / "git.exe").exists()]
    env["PATH"] = os.pathsep.join(kept)
    probe = subprocess.run(
        [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
         "if (Get-Command git -ErrorAction SilentlyContinue) { 'FOUND' } else { 'GONE' }"],
        capture_output=True, text=True, env=env,
    )
    if "GONE" not in probe.stdout:
        return None
    return env


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipIf(GIT is None, "git is not on PATH")
@unittest.skipUnless(os.name == "nt", "um-run.ps1 targets Windows agent shares")
class UmRunFixtureContentPinTests(unittest.TestCase):
    """ATTR3-ADMIT-CONTENT-PIN-1 (fable key on PR #137): admission must pin the WORKING-TREE bytes
    to the committed blob, not merely a tracked name. Every repo here is a disposable, TEMPORARY git
    repository built under a scratch tempdir -- never the real tests/fixtures tree -- so a
    bytes-corrupting test can never touch a real fixture. The synthetic fixture uses the same
    ".umrunprobe" suffix the existing untracked-fixture test already uses (never a new
    media-extension literal), with a stem ("tiny_dual_iso") from the module's own admissible set.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="umrun-pin-")
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(os.path.realpath(self._tmp.name)) / "repo"
        self.repo.mkdir()
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "umrun-pin-test@example.invalid"], self.repo)
        _git(["config", "user.name", "UmRun Pin Test"], self.repo)
        self.clips = self.repo / "tests" / "fixtures" / "clips"
        self.clips.mkdir(parents=True)
        self.fixture = self.clips / "tiny_dual_iso.umrunprobe"
        self.fixture.write_bytes(b"committed fixture bytes")
        _git(["add", "tests/fixtures/clips/tiny_dual_iso.umrunprobe"], self.repo)
        _git(["commit", "-q", "-m", "fixture"], self.repo)

    def probe(self, path: Path, *, repo_root: Path | None = None, env: dict[str, str] | None = None,
              module: Path | None = None) -> str:
        # $VerbosePreference (not just -Verbose on the outer call) so Write-Verbose inside the
        # nested Get-UmRunFixtureAdmission catch block surfaces regardless of exactly how deep
        # the call chain runs -- the underlying ATTR3_FIXTURE_* / UMRUN_FIXTURE_CONTENT_PIN_*
        # token, which Test-UmRunTrackedFixtureSource's boolean return would otherwise discard.
        script = self.repo.parent / f"probe-{abs(hash(str(path))) % 10**8}.ps1"
        script.write_text(
            "$VerbosePreference = 'Continue'\n"
            "Import-Module " + _q(module or MODULE) + " -Force\n"
            "Write-Output ('RESULT=' + (Test-UmRunTrackedFixtureSource -SourcePath " + _q(path) +
            " -RepoRoot " + _q(repo_root if repo_root is not None else self.repo) + " -Verbose))\n",
            encoding="utf-8",
        )
        proc = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                              capture_output=True, text=True, env=env)
        return proc.stdout + proc.stderr

    def test_identical_working_tree_bytes_are_admitted(self) -> None:
        self.assertIn("RESULT=True", self.probe(self.fixture))

    def test_altered_working_tree_bytes_are_refused(self) -> None:
        # THE DEFECT: the old name-plus-tracked check admitted this unconditionally.
        self.fixture.write_bytes(b"foreign bytes staged over the fixture")
        output = self.probe(self.fixture)
        self.assertIn("RESULT=False", output)
        self.assertIn("ATTR3_FIXTURE_WORKING_TREE_DIRTY", output, output)

    def test_an_untracked_fixture_shaped_file_is_refused(self) -> None:
        untracked = self.clips / "large_dual_iso.umrunprobe"
        untracked.write_bytes(b"never committed")
        output = self.probe(untracked)
        self.assertIn("RESULT=False", output)
        self.assertIn("ATTR3_FIXTURE_NOT_COMMITTED", output, output)

    def test_a_fixture_shaped_file_outside_any_repo_is_refused(self) -> None:
        # No `git init` anywhere under orphan_root: the directory shape and stem are admissible,
        # but there is no repository at all to hold a committed blob.
        orphan_root = Path(os.path.realpath(self._tmp.name)) / "orphan"
        orphan_clips = orphan_root / "tests" / "fixtures" / "clips"
        orphan_clips.mkdir(parents=True)
        orphan = orphan_clips / "tiny_dual_iso.umrunprobe"
        shutil.copy2(self.fixture, orphan)
        output = self.probe(orphan, repo_root=orphan_root)
        self.assertIn("RESULT=False", output)
        self.assertIn("ATTR3_FIXTURE_NOT_IN_A_REPO", output, output)

    def test_no_git_on_path_is_refused(self) -> None:
        env = _env_without_git()
        if env is None:
            self.skipTest("could not remove git from PATH in this environment")
        output = self.probe(self.fixture, env=env)
        self.assertIn("RESULT=False", output)
        self.assertIn("ATTR3_FIXTURE_GIT_UNAVAILABLE", output, output)

    def test_a_nested_repository_beneath_the_trusted_root_cannot_authorize_the_fixture(self) -> None:
        # sol, PR #140 r2 BLOCKER: without -RepoRoot pinned to the caller's trusted root, the old
        # code discovered the repository from the FIXTURE'S OWN DIRECTORY -- so a nested
        # repository committed under tests/fixtures/clips could authorize bytes the outer
        # repository's HEAD never held. This repository is disposable and separate from the one
        # setUp already built at self.repo; the outer repo's committed fixture bytes are
        # untouched, and a nested repo underneath is the only thing that changes.
        _git(["init", "-q"], self.clips)
        _git(["config", "user.email", "umrun-pin-test@example.invalid"], self.clips)
        _git(["config", "user.name", "UmRun Pin Test"], self.clips)
        self.fixture.write_bytes(b"foreign bytes authorized only by the nested repo")
        _git(["add", "tiny_dual_iso.umrunprobe"], self.clips)
        _git(["commit", "-q", "-m", "foreign"], self.clips)
        output = self.probe(self.fixture)
        self.assertIn("RESULT=False", output)
        self.assertIn("ATTR3_FIXTURE_FOREIGN_REPO", output, output)

    def test_a_missing_bachelor_module_is_refused_as_content_pin_unavailable(self) -> None:
        # Test-UmRunFixtureContentPin's own fail-closed branch: the bachelor module (which carries
        # Assert-AttrCudaFixtureCommittedBytes) is the one thing this whole content-pin admission
        # surface depends on being importable ON DEMAND. Earlier coverage
        # (test_a_bachelor_less_host_refuses_a_tracked_fixture_rather_than_admitting_it_unpinned,
        # UmRunDropModuleTests above) proves the END-TO-END placement outcome when the module is
        # absent, but folds the reason into Test-UmRunTrackedFixtureSource's boolean return and
        # never asserts the UMRUN_FIXTURE_CONTENT_PIN_UNAVAILABLE token itself -- this is the
        # fail-open regression guard for the whole content pin, so a future change that quietly
        # turned "module missing" into "admit unpinned" would need to change this exact string, not
        # merely leave the end-to-end refusal (which a different bug could equally produce)
        # looking unchanged.
        # Round 2d: this used to RENAME the real, tracked bachelor module in the live working
        # tree and restore it in a finally -- a hard-killed run left the checkout without it and
        # dirty. A COPY of UmRunDrop.psm1 with no bachelor/ next to it produces the identical
        # module-absent condition without ever touching the real tree; -RepoRoot (via probe's
        # default of self.repo) still pins the disposable git repo this test built in setUp.
        module_copy = self.repo.parent / "no-bachelor" / "UmRunDrop.psm1"
        module_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MODULE, module_copy)
        output = self.probe(self.fixture, module=module_copy)
        self.assertIn("RESULT=False", output)
        self.assertIn("UMRUN_FIXTURE_CONTENT_PIN_UNAVAILABLE", output, output)


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipIf(GIT is None, "git is not on PATH")
@unittest.skipUnless(os.name == "nt", "um-run.ps1 targets Windows agent shares")
class UmRunFixtureContentPinRaceTests(unittest.TestCase):
    """sol, PR #140 r2 MAJOR: admission and placement used to be independent reads of the same
    source path -- Assert-UmRunSideFileName's content-pin check returned, then the source was
    re-read for its sha256 and for the copy, so bytes that changed in that gap were never bound
    to the blob admission actually verified. A disposable git repository (never the real
    tests/fixtures/clips tree) plus Invoke-UmRunDrop's -PostAdmissionHook test seam -- which
    fires in exactly that gap and is never set in production -- exercises the race directly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="umrun-race-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "umrun-race-test@example.invalid"], self.repo)
        _git(["config", "user.name", "UmRun Race Test"], self.repo)
        self.clips = self.repo / "tests" / "fixtures" / "clips"
        self.clips.mkdir(parents=True)
        self.fixture = self.clips / "tiny_dual_iso.umrunprobe"
        self.fixture.write_bytes(b"committed fixture bytes")
        _git(["add", "tests/fixtures/clips/tiny_dual_iso.umrunprobe"], self.repo)
        _git(["commit", "-q", "-m", "fixture"], self.repo)

        self.share = self.tmp / "agent"
        self.inbox = self.share / "inbox"
        self.outbox = self.share / "outbox"
        self.inbox.mkdir(parents=True)
        self.outbox.mkdir()
        self.job = self.tmp / "demo.job.ps1"
        self.job.write_text("Write-Output 'hi'\n", encoding="utf-8")

    def names(self) -> list[str]:
        return sorted(p.name for p in self.inbox.iterdir())

    def drop(self, hook: str) -> subprocess.CompletedProcess:
        script = self.tmp / "race-drop.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module {_q(MODULE)} -Force\n"
            "try {\n"
            f"  Invoke-UmRunDrop -Inbox {_q(self.inbox)} -Outbox {_q(self.outbox)} -ScriptPath {_q(self.job)} "
            f"-JobId 'demo' -SideFile @({_q(self.fixture)}) -RepoRoot {_q(self.repo)} "
            f"-PostAdmissionHook {hook}\n"
            "} catch { Write-Output ('THREW ' + $_.Exception.Message) }\n",
            encoding="utf-8",
        )
        return subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                              capture_output=True, text=True)

    def test_a_fixture_swapped_after_admission_but_before_placement_is_refused(self) -> None:
        hook = "{ param($p) [IO.File]::WriteAllBytes($p, [Text.Encoding]::UTF8.GetBytes('raced bytes')) }"
        proc = self.drop(hook)
        self.assertIn("THREW UMRUN_FIXTURE_CONTENT_PIN_RACE", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [], "bytes swapped after admission must never reach the inbox")

    def test_a_no_op_hook_still_admits_the_clean_fixture(self) -> None:
        # Positive control: the new check must not false-positive when nothing raced.
        proc = self.drop("{ param($p) }")
        self.assertIn("UMRUN_JOBID=demo", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), sorted(["demo.job.ps1", "tiny_dual_iso.umrunprobe"]))
        self.assertEqual(
            hashlib.sha256((self.inbox / "tiny_dual_iso.umrunprobe").read_bytes()).hexdigest(),
            hashlib.sha256(self.fixture.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
