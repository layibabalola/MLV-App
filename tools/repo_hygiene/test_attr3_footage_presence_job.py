"""Behavioural tests for tools/profiling/bachelor/attr3-footage-presence-job.ps1.

Every row uses SYNTHETIC parts injected through ``-InjectResolvedPartsJsonPathForTests`` -- a
parameter the real CLI path never sets (the generator's normal invocation names only
-ClipId/-OutDir/-RepoRoot; see that parameter's own comment in the generator and its "TEST SEAM"
label). No row calls the real resolver, reads git, touches the hook's real consent table, or
opens a tracked/real clip. Synthetic paths live under a directory named for a distinctive token so
the path-free-output rows can assert the token never reaches stdout or stderr, and every synthetic
fixture uses a ``.raw`` extension per this card's hard rule against the real footage extension.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tools" / "profiling" / "bachelor" / "attr3-footage-presence-job.ps1"
PWSH = shutil.which("pwsh")

TOKEN = "ZZPRESENCEJOBTESTTOKENZZ"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(__import__("os").name == "nt", "the emitted job targets a Windows measurement host")
class FootagePresenceJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3presence-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.clips_dir = self.tmp / TOKEN
        self.clips_dir.mkdir()
        self.out = self.tmp / "out"
        self.out.mkdir()

        self.contents = (b"synthetic presence part zero " * 131, b"synthetic presence part one")
        self.paths = [self.clips_dir / "clip.raw", self.clips_dir / "clip.raw.part1"]
        for path, content in zip(self.paths, self.contents):
            path.write_bytes(content)

        self.parts_payload = {
            "clipId": "FIX-PRESENCE-0001",
            "parts": [
                {"index": i, "path": str(path), "length": len(content), "sha256": _sha256(content)}
                for i, (path, content) in enumerate(zip(self.paths, self.contents))
            ],
        }
        self.parts_json_path = self.tmp / "resolved-parts.json"
        self.parts_json_path.write_text(json.dumps(self.parts_payload), encoding="utf-8")

    def generate(self, parts_json: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(GENERATOR),
                "-ClipId", self.parts_payload["clipId"], "-OutDir", str(self.out),
                "-InjectResolvedPartsJsonPathForTests", str(parts_json or self.parts_json_path),
            ],
            capture_output=True, text=True,
        )

    def job_path(self, proc: subprocess.CompletedProcess) -> Path:
        jobs = sorted(self.out.glob("*.job.ps1"))
        self.assertEqual(len(jobs), 1, proc.stdout + proc.stderr)
        return jobs[0]

    def run_job(self, job: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(job)],
            capture_output=True, text=True,
        )

    def _assert_no_token(self, *texts: str) -> None:
        for text in texts:
            self.assertNotIn(TOKEN, text)

    # ---- generator ------------------------------------------------------------------------------

    def test_generator_emits_one_job_and_prints_no_path(self) -> None:
        proc = self.generate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.job_path(proc)
        self._assert_no_token(proc.stdout, proc.stderr)

    def test_generator_refuses_a_part_path_outside_the_allowlist(self) -> None:
        bad_payload = json.loads(json.dumps(self.parts_payload))
        bad_payload["parts"] = [bad_payload["parts"][0]]
        bad_payload["parts"][0]["path"] = str(self.paths[0]) + "'; rm -rf /"
        bad_path = self.tmp / "bad-parts.json"
        bad_path.write_text(json.dumps(bad_payload), encoding="utf-8")
        proc = self.generate(parts_json=bad_path)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_PRESENCE_PART_PATH_INVALID", proc.stdout + proc.stderr)
        self.assertEqual(sorted(self.out.glob("*.job.ps1")), [])

    def test_generator_refuses_zero_parts(self) -> None:
        empty_payload = {"clipId": self.parts_payload["clipId"], "parts": []}
        empty_path = self.tmp / "empty-parts.json"
        empty_path.write_text(json.dumps(empty_payload), encoding="utf-8")
        proc = self.generate(parts_json=empty_path)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_PRESENCE_NO_PARTS", proc.stdout + proc.stderr)

    # ---- emitted job: the five required statuses -------------------------------------------------

    def test_pass_when_both_parts_match(self) -> None:
        job = self.job_path(self.generate())
        run = self.run_job(job)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_PRESENT", run.stdout)
        self.assertIn("PART=0 STATUS=PASS", run.stdout)
        self.assertIn("PART=1 STATUS=PASS", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)
        payload = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["result"], "FOOTAGE_PRESENT")
        self.assertEqual([p["status"] for p in payload["parts"]], ["PASS", "PASS"])

    def test_missing_both_parts_is_footage_absent(self) -> None:
        job = self.job_path(self.generate())
        for path in self.paths:
            path.unlink()
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_ABSENT", run.stdout)
        self.assertIn("PART=0 STATUS=MISSING", run.stdout)
        self.assertIn("PART=1 STATUS=MISSING", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)

    def test_length_mismatch_is_footage_mismatch(self) -> None:
        job = self.job_path(self.generate())
        self.paths[1].write_bytes(self.contents[1] + b"!")
        run = self.run_job(job)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_MISMATCH", run.stdout)
        self.assertIn("PART=1 STATUS=LENGTH_MISMATCH", run.stdout)
        self.assertIn("PART=0 STATUS=PASS", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)

    def test_sha256_mismatch_at_the_same_length_is_footage_mismatch(self) -> None:
        job = self.job_path(self.generate())
        swapped = bytes(reversed(self.contents[1]))
        self.assertEqual(len(swapped), len(self.contents[1]))
        self.paths[1].write_bytes(swapped)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_MISMATCH", run.stdout)
        self.assertIn("PART=1 STATUS=SHA256_MISMATCH", run.stdout)
        self.assertIn("PART=0 STATUS=PASS", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)

    def test_one_part_bad_one_good_is_footage_mismatch(self) -> None:
        job = self.job_path(self.generate())
        self.paths[1].unlink()
        run = self.run_job(job)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_MISMATCH", run.stdout)
        self.assertIn("PART=0 STATUS=PASS", run.stdout)
        self.assertIn("PART=1 STATUS=MISSING", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)


if __name__ == "__main__":
    unittest.main()
