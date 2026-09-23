"""ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (fable/sol major 2): the tracked
tools/profiling/ultra-magnus-agent.ps1 must fall back to its own -JobTimeoutSec default when a
job's inbox\\<id>.meta.json is either absent or unparseable -- the same fall-back rule the deployed,
untracked \\\\bachelor\\mlv-agent copy already implements. The "metadata present and honoured" case
is covered end to end (real agent + real um-run.ps1) by tools/testing/test-ultra-magnus-agent-
timeout.ps1; this file covers the two fallback paths against the same REAL, locally-run agent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_SCRIPT = ROOT / "tools" / "profiling" / "ultra-magnus-agent.ps1"
UM_RUN = ROOT / "tools" / "profiling" / "um-run.ps1"
PWSH = shutil.which("pwsh")


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "targets a Windows agent share")
class UltraMagnusAgentMetadataFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="umagent-meta-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.share = self.tmp / "agent"
        self.share.mkdir()
        # A small, fixed -JobTimeoutSec default that is easy to distinguish in wall-clock time from
        # both "no timeout at all" and from a job that finishes normally.
        self.agent_proc = subprocess.Popen(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(AGENT_SCRIPT),
             "-Root", str(self.share), "-PollSeconds", "1", "-JobTimeoutSec", "2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self._stop_agent)
        heartbeat = self.share / "heartbeat.txt"
        deadline = time.time() + 20
        while time.time() < deadline and not heartbeat.exists():
            time.sleep(0.2)
        if not heartbeat.exists():
            self.skipTest("local ultra-magnus-agent.ps1 double did not start in time")

        self.local = self.tmp / "local"
        self.local.mkdir()
        # Sleeps far longer than the agent's 2s default, so a kill at ~2s proves the default was
        # used (as opposed to running to completion, which would prove nothing about timeouts).
        self.job = self.local / "sleepy.job.ps1"
        self.job.write_text("Start-Sleep -Seconds 30\n", encoding="utf-8")

    def _stop_agent(self) -> None:
        try:
            self.agent_proc.terminate()
            self.agent_proc.wait(timeout=10)
        except Exception:
            try:
                self.agent_proc.kill()
            except Exception:
                pass

    def _submit(self, *extra: str, timeout_sec: str = "1") -> subprocess.CompletedProcess:
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(self.job), "-AgentShare", str(self.share),
             "-TimeoutSec", timeout_sec, "-PollSeconds", "1", "-MaxQueueWaitSec", "30", *extra],
            capture_output=True, text=True,
        )

    def test_missing_metadata_falls_back_to_the_agents_own_default(self) -> None:
        # sol major 3's decided contract: -TimeoutSec 0 means "no metadata" at the module level, and
        # the public client's own zero-rejection (um-run.ps1) does not apply to internal module
        # callers -- but um-run.ps1 itself always forwards a positive -TimeoutSec. To exercise the
        # AGENT's missing-metadata fallback specifically (not the client's own zero-rejection), drop
        # the job directly through UmRunDrop.psm1 with JobTimeoutSec 0, bypassing um-run.ps1 -- the
        # same "no budget requested" shape a caller with no opinion produces today.
        drop = self.tmp / "drop.ps1"
        drop.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{ROOT / 'tools' / 'profiling' / 'UmRunDrop.psm1'}' -Force\n"
            f"Invoke-UmRunDrop -Inbox '{self.share / 'inbox'}' -Outbox '{self.share / 'outbox'}' "
            f"-ScriptPath '{self.job}' -JobId 'demo' -JobTimeoutSec 0 | Out-Null\n",
            encoding="utf-8",
        )
        proc = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(drop)],
                               capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse((self.share / "inbox" / "demo.meta.json").exists(),
                          "this scenario is only meaningful with no metadata present")

        result_path = self.share / "outbox" / "demo.result.json"
        deadline = time.time() + 20
        while time.time() < deadline and not result_path.exists():
            time.sleep(0.2)
        self.assertTrue(result_path.exists(), "the agent never published a result")
        result = json.loads(result_path.read_text(encoding="ascii"))
        self.assertEqual(result["timeoutSec"], 2, "must fall back to the agent's own -JobTimeoutSec")
        self.assertTrue(result["timedOut"])
        self.assertEqual(result["exitCode"], 124)

    def test_unparseable_metadata_falls_back_to_the_agents_own_default(self) -> None:
        # fable minor 2 territory: a torn/corrupt meta.json must degrade to the agent's default, not
        # abort the job or silently hang. Written directly (bypassing UmRunDrop.psm1's own write-time
        # verification, which this test intentionally defeats) to exercise the AGENT's read-side
        # tolerance in isolation.
        inbox = self.share / "inbox"
        (inbox / "demo.meta.json").write_text("{not valid json", encoding="ascii")
        (inbox / "demo.job.ps1").write_bytes(self.job.read_bytes())

        result_path = self.share / "outbox" / "demo.result.json"
        deadline = time.time() + 20
        while time.time() < deadline and not result_path.exists():
            time.sleep(0.2)
        self.assertTrue(result_path.exists(), "the agent never published a result for a job with unparseable metadata")
        result = json.loads(result_path.read_text(encoding="ascii"))
        self.assertEqual(result["timeoutSec"], 2, "must fall back to the agent's own -JobTimeoutSec")
        self.assertTrue(result["timedOut"])

        # The agent publishes outbox\demo.result.json (the poll above) BEFORE it archives
        # inbox\demo.job.ps1 and inbox\demo.meta.json to processed\ (ultra-magnus-agent.ps1,
        # deliberately: the result publish is what retires the JobId in UmRunDrop.psm1's
        # outbox-result check, so it must land first, before either file moves) -- so the archival
        # is a few more of the agent's own instructions past the instant result.json becomes
        # visible, not simultaneous with it. Give it the same bounded settle time every other
        # file-visibility check in this module gets, rather than asserting the exact instant
        # result.json appears.
        meta_processed = self.share / "processed" / "demo.meta.json"
        deadline = time.time() + 5
        while time.time() < deadline and (inbox / "demo.meta.json").exists() and not meta_processed.exists():
            time.sleep(0.1)
        self.assertFalse((inbox / "demo.meta.json").exists(), "the unparseable metadata must still be moved out of inbox")
        self.assertTrue(meta_processed.exists())


if __name__ == "__main__":
    unittest.main()
