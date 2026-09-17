"""Behavioural tests for tools/profiling/um-run.ps1 side-file placement, against a fake agent share.

um-run.ps1 is the tracked route for putting a generated job AND its inputs into an agent inbox
(NA-7 refuses hooked writes to the share). These tests run it against a temporary directory laid
out like the agent share, with a fresh heartbeat and no agent, so every submission times out after
its drop -- which is exactly the moment the inbox contents are asserted.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UM_RUN = ROOT / "tools" / "profiling" / "um-run.ps1"
PWSH = shutil.which("pwsh")


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "um-run.ps1 targets Windows agent shares")
class UmRunSideFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="umrun-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.share = self.tmp / "agent"
        self.inbox = self.share / "inbox"
        (self.share / "outbox").mkdir(parents=True)
        self.inbox.mkdir()
        (self.share / "heartbeat.txt").write_text("alive", encoding="utf-8")
        self.local = self.tmp / "local"
        self.local.mkdir()
        self.job = self.local / "demo.job.ps1"
        self.job.write_text("Write-Output 'hi'\n", encoding="utf-8")
        self.side = self.local / "demo-source.zip"
        self.side.write_bytes(os.urandom(4096))

    def submit(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(self.job), "-AgentShare", str(self.share),
             "-TimeoutSec", "1", "-PollSeconds", "1", *extra],
            capture_output=True, text=True,
        )

    def names(self) -> list[str]:
        return sorted(p.name for p in self.inbox.iterdir())

    def test_side_file_is_placed_verified_and_before_the_job(self) -> None:
        proc = self.submit("-SideFile", str(self.side), "-JobId", "demo")
        self.assertIn("side-file placed: demo-source.zip", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("Timed out", proc.stderr + proc.stdout)  # no agent: the drop is what we assert
        self.assertEqual(self.names(), ["demo-source.zip", "demo.job.ps1"])
        self.assertEqual(
            hashlib.sha256((self.inbox / "demo-source.zip").read_bytes()).hexdigest(),
            hashlib.sha256(self.side.read_bytes()).hexdigest(),
        )
        self.assertFalse(any(n.endswith(".sidepart") for n in self.names()))

    def test_a_different_file_already_in_the_inbox_is_refused_and_no_job_is_dropped(self) -> None:
        (self.inbox / "demo-source.zip").write_bytes(b"someone else's bytes")
        proc = self.submit("-SideFile", str(self.side), "-JobId", "demo")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DIFFERENT content", proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo-source.zip"])
        self.assertEqual((self.inbox / "demo-source.zip").read_bytes(), b"someone else's bytes")

    def test_an_identical_file_already_present_is_accepted(self) -> None:
        shutil.copy2(self.side, self.inbox / "demo-source.zip")
        proc = self.submit("-SideFile", str(self.side), "-JobId", "demo")
        self.assertIn("already present with matching sha256", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(self.names(), ["demo-source.zip", "demo.job.ps1"])

    def test_a_job_named_side_file_is_refused(self) -> None:
        bad = self.local / "evil.job.ps1"
        bad.write_text("Write-Output 'x'\n", encoding="utf-8")
        proc = self.submit("-SideFile", str(bad))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a plain, non-job basename", proc.stdout + proc.stderr)
        self.assertEqual(self.names(), [])

    def test_a_pending_job_with_the_same_id_is_not_replaced(self) -> None:
        (self.inbox / "demo.job.ps1").write_text("Write-Output 'pending'\n", encoding="utf-8")
        proc = self.submit("-JobId", "demo")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("refusing to replace it", proc.stdout + proc.stderr)
        self.assertEqual((self.inbox / "demo.job.ps1").read_text(encoding="utf-8"), "Write-Output 'pending'\n")

    def test_a_list_separated_by_semicolons_places_every_file(self) -> None:
        second = self.local / "demo-build.json"
        second.write_text("{}", encoding="utf-8")
        proc = self.submit("-SideFile", f"{self.side};{second}", "-JobId", "demo")
        self.assertEqual(self.names(), ["demo-build.json", "demo-source.zip", "demo.job.ps1"], proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
