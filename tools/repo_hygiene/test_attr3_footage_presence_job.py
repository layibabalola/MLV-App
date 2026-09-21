"""Behavioural tests for tools/profiling/bachelor/attr3-footage-presence-job.ps1 and its job-text
builder, tools/profiling/bachelor/Attr3FootagePresenceJob.psm1.

Every row uses SYNTHETIC parts, imported and called directly through the module's
New-Attr3FootagePresenceJob -- a plain PowerShell function call, never a flag on the CLI generator
(the real generator's only path to parts is tools/gates/resolve_consented_clip.py; see the
generator's own header comment). No row calls the real resolver, reads git, touches the hook's
real consent table, or opens a tracked/real clip. Synthetic paths live under a directory named for
a distinctive token so the path-free-output rows can assert the token never reaches stdout or
stderr, and every synthetic fixture uses a ``.raw`` extension per this card's hard rule against the
real footage extension.

ROUND 2 (ATTR3-FOOTAGE-BIND-1-prA): every test here that resolves parts through TEMP is built to
be environment-independent -- it does not matter whether the host's TEMP root is a plain path or
an 8.3 short name containing ``~``, because at least one part below always carries a path with
FORWARD slashes and at least one always carries a literal ``~``, exercising both real-data
properties directly rather than hoping the ambient TEMP shape happens to cover them.
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
MODULE = ROOT / "tools" / "profiling" / "bachelor" / "Attr3FootagePresenceJob.psm1"
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
        self.out = self.tmp / "out"
        self.out.mkdir()

        # Two real-data properties, forced explicitly rather than left to the ambient TEMP shape:
        # part 0 always carries a path with FORWARD slashes (every consented part in the real spec
        # uses them); part 1 always carries a literal `~` (a measurement host's own TEMP root can
        # be an 8.3 short name). Both are legal NTFS path components, so the files genuinely exist
        # at these exact strings.
        fwd_dir = self.tmp / f"{TOKEN}-fwd"
        fwd_dir.mkdir()
        tilde_dir = self.tmp / f"{TOKEN}~short"
        tilde_dir.mkdir()

        fwd_native = fwd_dir / "clip.raw"
        tilde_native = tilde_dir / "clip.raw.part1"
        self.contents = (b"synthetic presence part zero " * 131, b"synthetic presence part one")
        fwd_native.write_bytes(self.contents[0])
        tilde_native.write_bytes(self.contents[1])

        self.paths = [str(fwd_native).replace("\\", "/"), str(tilde_native)]
        self.assertIn("/", self.paths[0])
        self.assertNotIn("\\", self.paths[0])
        self.assertIn("~", self.paths[1])

        self.clip_id = "FIX-PRESENCE-0001"
        self.parts_payload = [
            {"index": i, "path": path, "length": len(content), "sha256": _sha256(content)}
            for i, (path, content) in enumerate(zip(self.paths, self.contents))
        ]

    # ---- calling New-Attr3FootagePresenceJob directly, never through the CLI ---------------------

    def call_module(self, parts, clip_id: str | None = None, out_dir: Path | None = None) -> subprocess.CompletedProcess:
        parts_json_path = self.tmp / f"parts-{id(parts)}.json"
        parts_json_path.write_text(json.dumps(parts), encoding="utf-8")
        script = (
            f"Import-Module '{MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootagePresenceJob -ClipId '{clip_id or self.clip_id}' -Parts $parts -OutDir '{out_dir or self.out}'"
        )
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True,
        )

    def generate(self, parts=None, clip_id: str | None = None) -> subprocess.CompletedProcess:
        return self.call_module(self.parts_payload if parts is None else parts, clip_id=clip_id)

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

    # ---- the injection seam is gone -------------------------------------------------------------

    def test_generator_no_longer_has_an_injection_parameter(self) -> None:
        # The removed parameter's name may still appear in prose comments explaining WHY it is
        # gone (see the generator's own header) -- what must be absent is any live code line
        # (declaring or referencing it as a parameter/variable), so comment lines are excluded.
        code_lines = [
            line for line in GENERATOR.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        ]
        for line in code_lines:
            self.assertNotIn("InjectResolvedPartsJsonPathForTests", line)

    def test_generator_calls_the_module_not_a_local_template(self) -> None:
        text = GENERATOR.read_text(encoding="utf-8")
        self.assertIn("New-Attr3FootagePresenceJob", text)
        self.assertIn("Attr3FootagePresenceJob.psm1", text)

    # ---- module: builder behaviour ------------------------------------------------------------

    def test_module_emits_one_job_and_prints_no_path(self) -> None:
        proc = self.generate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.job_path(proc)
        self._assert_no_token(proc.stdout, proc.stderr)

    def test_module_refuses_zero_parts(self) -> None:
        proc = self.generate(parts=[])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_PRESENCE_NO_PARTS", proc.stdout + proc.stderr)
        self.assertEqual(sorted(self.out.glob("*.job.ps1")), [])

    def test_module_refuses_a_path_without_a_drive_prefix(self) -> None:
        bad = [dict(self.parts_payload[0])]
        bad[0]["path"] = "relative/clip.raw"
        proc = self.generate(parts=bad)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_PRESENCE_PART_PATH_INVALID", proc.stdout + proc.stderr)
        self.assertEqual(sorted(self.out.glob("*.job.ps1")), [])

    def test_module_refuses_a_control_character_in_the_path(self) -> None:
        bad = [dict(self.parts_payload[0])]
        bad[0]["path"] = bad[0]["path"] + "\x01"
        proc = self.generate(parts=bad)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_PRESENCE_PART_PATH_INVALID", proc.stdout + proc.stderr)

    def test_module_accepts_forward_slashes_and_tilde_and_never_embeds_a_bare_path(self) -> None:
        # Requirement A's whole point: neither a forward slash nor a `~` is rejected, and the
        # emitted job's own text never contains the plain path -- only its base64 form.
        proc = self.generate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        job = self.job_path(proc)
        job_text = job.read_text(encoding="utf-8")
        for path in self.paths:
            self.assertNotIn(path, job_text)
        self.assertIn("pathBase64", job_text)
        self.assertNotIn('"path"', job_text)

    def test_a_quote_and_semicolon_in_the_path_does_not_break_out_of_the_template(self) -> None:
        # Round 2's actual injection-safety proof: a path containing a single quote (which JSON
        # encoding does NOT escape) used to be able to break out of the single-quoted PowerShell
        # literal the old template wrapped $PartsJson in. Base64 embedding removes the character
        # class entirely: the job must still emit and run cleanly, and the hostile-looking
        # substring must never appear verbatim in the emitted job text.
        hostile_dir = self.tmp / f"{TOKEN}-hostile"
        hostile_dir.mkdir()
        hostile_name = "clip'; $(Get-Date) .raw"
        hostile_path = hostile_dir / hostile_name
        hostile_path.write_bytes(b"synthetic hostile content")
        parts = [
            {
                "index": 0,
                "path": str(hostile_path),
                "length": len(b"synthetic hostile content"),
                "sha256": _sha256(b"synthetic hostile content"),
            }
        ]
        proc = self.generate(parts=parts, clip_id="FIX-PRESENCE-HOSTILE")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        job = self.job_path(proc)
        job_text = job.read_text(encoding="utf-8")
        self.assertNotIn("'; $(Get-Date)", job_text)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_PRESENT", run.stdout)
        self.assertIn("PART=0 STATUS=PASS", run.stdout)

    # ---- emitted job: the five required statuses, now decoding base64 first ----------------------

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
            Path(path).unlink()
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_ABSENT", run.stdout)
        self.assertIn("PART=0 STATUS=MISSING", run.stdout)
        self.assertIn("PART=1 STATUS=MISSING", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)

    def test_length_mismatch_is_footage_mismatch(self) -> None:
        job = self.job_path(self.generate())
        Path(self.paths[1]).write_bytes(self.contents[1] + b"!")
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
        Path(self.paths[1]).write_bytes(swapped)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_MISMATCH", run.stdout)
        self.assertIn("PART=1 STATUS=SHA256_MISMATCH", run.stdout)
        self.assertIn("PART=0 STATUS=PASS", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)

    def test_one_part_bad_one_good_is_footage_mismatch(self) -> None:
        job = self.job_path(self.generate())
        Path(self.paths[1]).unlink()
        run = self.run_job(job)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_MISMATCH", run.stdout)
        self.assertIn("PART=0 STATUS=PASS", run.stdout)
        self.assertIn("PART=1 STATUS=MISSING", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)


if __name__ == "__main__":
    unittest.main()
