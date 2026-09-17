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

    def drop(self, copier: str, *, side: list[Path] | None = None, job_id: str = "demo") -> subprocess.CompletedProcess:
        sides = ",".join(_q(p) for p in (side if side is not None else [self.side]))
        script = self.tmp / "drop.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module {_q(MODULE)} -Force\n"
            f"$log = {_q(self.log)}\n"
            f"$copier = {copier}\n"
            "try {\n"
            f"  Invoke-UmRunDrop -Inbox {_q(self.inbox)} -Outbox {_q(self.outbox)} -ScriptPath {_q(self.job)} "
            f"-JobId '{job_id}' -SideFile @({sides}) -Copier $copier\n"
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


if __name__ == "__main__":
    unittest.main()
