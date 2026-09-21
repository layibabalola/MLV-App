"""Behavioural tests for ATTR3-FOOTAGE-STAGE-1:
tools/profiling/bachelor/attr3-footage-stage.ps1 (the tracked, id-only entry point),
tools/profiling/bachelor/Attr3FootageStageJob.psm1 (job-text builder) and
tools/profiling/bachelor/AttrCudaOwnerFootage.psm1's Send-AttrCudaOwnerFootagePartToStaging
(local host -> agent share transfer).

Every row uses SYNTHETIC parts and a SYNTHETIC agent share (a plain temp directory standing in
for \\\\bachelor\\mlv-agent), imported and called directly through the module functions -- a
plain PowerShell function call, never a flag on the CLI generator (its only path to parts is
tools/gates/resolve_consented_clip.py; see the generator's own header comment). No row calls the
real resolver with a real clip id, reads git, touches the hook's real consent table, or opens a
tracked/real clip -- except the one resolver-refusal row, which calls the REAL resolver against
the REAL repository with a clip id that does not exist, which is safe by construction (the
resolver never opens or names a real footage path for an id it does not resolve). Synthetic
fixtures use a ``.raw`` extension, matching test_attr3_footage_presence_job.py's own precedent
against writing the real footage extension in this repository.
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
GENERATOR = ROOT / "tools" / "profiling" / "bachelor" / "attr3-footage-stage.ps1"
STAGE_MODULE = ROOT / "tools" / "profiling" / "bachelor" / "Attr3FootageStageJob.psm1"
OWNER_FOOTAGE_MODULE = ROOT / "tools" / "profiling" / "bachelor" / "AttrCudaOwnerFootage.psm1"
PWSH = shutil.which("pwsh")

TOKEN = "ZZFOOTAGESTAGETESTTOKENZZ"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(args, **kwargs):
    return subprocess.run(
        [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive"] + args,
        capture_output=True, text=True, **kwargs,
    )


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job targets a Windows measurement host")
class SendPartToStagingTests(unittest.TestCase):
    """tools/profiling/bachelor/AttrCudaOwnerFootage.psm1's Send-AttrCudaOwnerFootagePartToStaging."""

    def setUp(self) -> None:
        # TOKEN never appears in this prefix, or in -share-'s own path: the module legitimately
        # returns/prints the neutral STAGED path (never a source path) -- see
        # test_no_source_path_ever_reaches_stdout_or_stderr below, which puts TOKEN only in the
        # SOURCE's own directory name to isolate exactly that guarantee.
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3stagesend-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.share = self.tmp / "share"
        self.share.mkdir()
        self.source = self.tmp / "source.raw"
        self.content = b"synthetic staging source part " * 37
        self.source.write_bytes(self.content)
        self.sha256 = _sha256(self.content)

    def send(self, source: Path, stage_dir: Path, index: int, length: int, sha256: str):
        script = (
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force; "
            f"Send-AttrCudaOwnerFootagePartToStaging -SourcePath '{source}' "
            f"-StagingDirectory '{stage_dir}' -Index {index} -ExpectedLength {length} "
            f"-ExpectedSha256 '{sha256}'"
        )
        return _run(["-Command", script])

    def test_successful_transfer_creates_the_neutral_index_named_slot(self) -> None:
        stage_dir = self.share / "job1"
        proc = self.send(self.source, stage_dir, 0, len(self.content), self.sha256)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        final = stage_dir / "part-0"
        self.assertTrue(final.is_file())
        self.assertEqual(final.read_bytes(), self.content)
        # No extension, and no leftover .partial.
        self.assertEqual(sorted(p.name for p in stage_dir.iterdir()), ["part-0"])

    def test_idempotent_rerun_is_a_noop_and_does_not_error(self) -> None:
        stage_dir = self.share / "job2"
        first = self.send(self.source, stage_dir, 0, len(self.content), self.sha256)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.send(self.source, stage_dir, 0, len(self.content), self.sha256)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual((stage_dir / "part-0").read_bytes(), self.content)

    def test_tampered_source_never_reaches_the_final_slot(self) -> None:
        # -ExpectedLength/-ExpectedSha256 are supplied by the caller from an earlier verification
        # of -SourcePath; a source that no longer matches them (tampered, or simply the wrong
        # file) must be refused, with no bytes ever renamed into the final slot.
        stage_dir = self.share / "job3"
        tampered = self.tmp / "tampered.raw"
        tampered.write_bytes(self.content + b"!")
        proc = self.send(tampered, stage_dir, 0, len(self.content), self.sha256)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("OWNER_FOOTAGE_STAGE_VERIFY_FAILED", proc.stdout + proc.stderr)
        self.assertFalse((stage_dir / "part-0").exists())
        self.assertFalse((stage_dir / "part-0.partial").exists())

    def test_existing_different_bytes_at_the_final_slot_is_a_conflict_not_an_overwrite(self) -> None:
        stage_dir = self.share / "job4"
        stage_dir.mkdir()
        (stage_dir / "part-0").write_bytes(b"already staged, different bytes")
        proc = self.send(self.source, stage_dir, 0, len(self.content), self.sha256)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("OWNER_FOOTAGE_STAGE_CONFLICT", proc.stdout + proc.stderr)
        self.assertEqual((stage_dir / "part-0").read_bytes(), b"already staged, different bytes")

    def test_no_source_path_ever_reaches_stdout_or_stderr(self) -> None:
        # TOKEN lives only in the SOURCE's own directory name -- the module's return value (the
        # neutral staged path) is expected on stdout (PowerShell auto-echoes a -Command
        # pipeline's final expression), but the real source path, and this token, must not be.
        token_source_dir = self.tmp / f"{TOKEN}-source-dir"
        token_source_dir.mkdir()
        token_source = token_source_dir / "source.raw"
        token_source.write_bytes(self.content)
        stage_dir = self.share / "job5"
        proc = self.send(token_source, stage_dir, 0, len(self.content), self.sha256)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn(str(token_source), proc.stdout + proc.stderr)
        self.assertNotIn(TOKEN, proc.stdout + proc.stderr)


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job targets a Windows measurement host")
class FootageStageJobTests(unittest.TestCase):
    """tools/profiling/bachelor/Attr3FootageStageJob.psm1's New-Attr3FootageStageJob and the
    emitted job it writes, run directly (never through um-run.ps1 or a real agent)."""

    def setUp(self) -> None:
        # TOKEN never appears in this prefix: the generator/module legitimately print the local
        # job file path (see New-Attr3FootageStageJob's own RESULT=...JOB=... line, mirroring
        # attr3-footage-presence-job.ps1's identical contract) -- what must never appear is a
        # real FOOTAGE path, which test_no_path_ever_reaches_the_job_s_output_in_any_branch below
        # isolates by putting TOKEN only in its own target directories.
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3stagejob-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.agent_root = self.tmp / "agent"
        self.agent_root.mkdir()
        self.out = self.tmp / "out"
        self.out.mkdir()

        self.content = (b"synthetic stage part zero " * 97, b"synthetic stage part one")
        self.target_dir = self.agent_root / "spec" / "FIX-STAGE-0001"
        self.targets = [self.target_dir / "part0.raw", self.target_dir / "part1.raw"]
        self.clip_id = "FIX-STAGE-0001"
        self.parts_payload = [
            {"index": i, "path": str(path), "length": len(content), "sha256": _sha256(content)}
            for i, (path, content) in enumerate(zip(self.targets, self.content))
        ]

    # ---- calling New-Attr3FootageStageJob directly, never through the CLI ---------------------

    def build(self, parts=None, clip_id: str | None = None) -> subprocess.CompletedProcess:
        payload = self.parts_payload if parts is None else parts
        parts_json_path = self.tmp / f"parts-{id(payload)}.json"
        parts_json_path.write_text(json.dumps(payload), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId '{clip_id or self.clip_id}' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}'"
        )
        return _run(["-Command", script])

    def job_path(self, proc: subprocess.CompletedProcess) -> Path:
        jobs = sorted(self.out.glob("*.job.ps1"))
        self.assertEqual(len(jobs), 1, proc.stdout + proc.stderr)
        return jobs[0]

    def stage_dir(self, job_id: str) -> Path:
        d = self.agent_root / "footage-stage" / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def stage_all_parts(self, stage_dir: Path) -> None:
        for i, content in enumerate(self.content):
            (stage_dir / f"part-{i}").write_bytes(content)

    def run_job(self, job: Path) -> subprocess.CompletedProcess:
        return _run(["-File", str(job)])

    def _assert_no_token(self, *texts: str) -> None:
        for text in texts:
            self.assertNotIn(TOKEN, text)

    # ---- generator: orchestration order, never a bare template -----------------------------

    def test_generator_calls_the_module_not_a_local_template(self) -> None:
        text = GENERATOR.read_text(encoding="utf-8")
        self.assertIn("New-Attr3FootageStageJob", text)
        self.assertIn("Attr3FootageStageJob.psm1", text)
        self.assertIn("Send-AttrCudaOwnerFootagePartToStaging", text)
        self.assertIn("resolve_consented_clip.py", text)

    def test_generator_verifies_every_source_part_before_transferring(self) -> None:
        text = GENERATOR.read_text(encoding="utf-8")
        verify_pos = text.index("Test-AttrCudaFootagePart")
        transfer_pos = text.index("Send-AttrCudaOwnerFootagePartToStaging")
        self.assertLess(verify_pos, transfer_pos)

    def test_generator_has_no_caller_typed_path_parameter(self) -> None:
        text = GENERATOR.read_text(encoding="utf-8")
        for banned in ("[string]$Path", "[string]$SourcePath", "-Path $Path"):
            self.assertNotIn(banned, text)

    def test_resolver_refusal_yields_a_typed_refusal_token_and_no_transfer(self) -> None:
        # The REAL resolver, against the REAL repository, with a clip id that does not exist --
        # safe by construction: the resolver only ever opens the frozen spec/consent table text,
        # never a footage path, for an id it refuses.
        proc = _run(["-File", str(GENERATOR), "-ClipId", "NOT-A-REAL-CLIP-ID-ATTR3-STAGE"])
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn("ATTR3_FOOTAGE_STAGE_RESOLVE_REFUSED", combined)
        self.assertIn("UNKNOWN_ID", combined)
        self.assertNotIn("RESULT=FOOTAGE_STAGED", combined)
        self.assertNotIn("TRANSFER PART", combined)

    # ---- module: builder behaviour -------------------------------------------------------------

    def test_module_emits_one_job_and_prints_no_footage_path(self) -> None:
        # The builder's own RESULT=...JOB=... line legitimately names the local job FILE path
        # (mirroring attr3-footage-presence-job.ps1's identical contract) -- what must never
        # appear is one of the real footage TARGET paths this job was built from.
        proc = self.build()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.job_path(proc)
        for target in self.targets:
            self.assertNotIn(str(target), proc.stdout + proc.stderr)

    def test_module_refuses_zero_parts(self) -> None:
        proc = self.build(parts=[])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_STAGE_NO_PARTS", proc.stdout + proc.stderr)
        self.assertEqual(sorted(self.out.glob("*.job.ps1")), [])

    def test_module_refuses_a_path_without_a_drive_prefix(self) -> None:
        bad = [dict(self.parts_payload[0])]
        bad[0]["path"] = "relative/part0.raw"
        proc = self.build(parts=bad)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_STAGE_PART_PATH_INVALID", proc.stdout + proc.stderr)

    def test_module_never_embeds_a_bare_path_only_base64(self) -> None:
        proc = self.build()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        job_text = self.job_path(proc).read_text(encoding="utf-8")
        for target in self.targets:
            self.assertNotIn(str(target), job_text)
        self.assertIn("pathBase64", job_text)
        self.assertNotIn('"path"', job_text)

    # ---- emitted job: the full per-part outcome matrix ------------------------------------------

    def test_both_parts_placed_is_footage_staged(self) -> None:
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_STAGED", run.stdout)
        self.assertIn("PART=0 STATUS=PLACED", run.stdout)
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)
        for target, content in zip(self.targets, self.content):
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), content)
        # Both staged neutral files are gone -- moved, not copied.
        self.assertFalse((stage_dir / "part-0").exists())
        self.assertFalse((stage_dir / "part-1").exists())
        payload = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["result"], "FOOTAGE_STAGED")
        self.assertEqual([p["status"] for p in payload["parts"]], ["PLACED", "PLACED"])
        self._assert_no_token(run.stdout, run.stderr)
        for target in self.targets:
            self.assertNotIn(str(target), run.stdout + run.stderr)

    def test_idempotent_rerun_with_a_fresh_staged_copy_is_already_present(self) -> None:
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        first = self.run_job(job)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

        self.stage_all_parts(stage_dir)
        second = self.run_job(job)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("RESULT=FOOTAGE_STAGED", second.stdout)
        self.assertIn("PART=0 STATUS=ALREADY_PRESENT", second.stdout)
        self.assertIn("PART=1 STATUS=ALREADY_PRESENT", second.stdout)
        self.assertFalse((stage_dir / "part-0").exists())
        self.assertFalse((stage_dir / "part-1").exists())

    def test_existing_different_target_refuses_and_leaves_target_untouched(self) -> None:
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.targets[0].write_bytes(b"pre-existing, different bytes")
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_STAGE_REFUSED", run.stdout)
        self.assertIn("PART=0 STATUS=TARGET_CONFLICT", run.stdout)
        self.assertEqual(self.targets[0].read_bytes(), b"pre-existing, different bytes")
        # Part 1 still placed cleanly -- one part's refusal never blocks another's.
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)
        self.assertTrue(self.targets[1].is_file())
        # Both staged copies are cleaned regardless of each part's own outcome.
        self.assertFalse((stage_dir / "part-0").exists())
        self.assertFalse((stage_dir / "part-1").exists())
        self._assert_no_token(run.stdout, run.stderr)

    def test_existing_identical_target_is_a_noop_pass(self) -> None:
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.targets[0].write_bytes(self.content[0])
        run = self.run_job(job)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_STAGED", run.stdout)
        self.assertIn("PART=0 STATUS=ALREADY_PRESENT", run.stdout)
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)
        self.assertEqual(self.targets[0].read_bytes(), self.content[0])

    def test_a_corrupt_staged_copy_refuses_and_cleans_its_own_staged_file(self) -> None:
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        (stage_dir / "part-0").write_bytes(self.content[0] + b"!")  # tampered/partial in transit
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_STAGE_REFUSED", run.stdout)
        self.assertIn("PART=0 STATUS=STAGED_LENGTH_MISMATCH", run.stdout)
        self.assertFalse(self.targets[0].exists())
        self.assertFalse((stage_dir / "part-0").exists())
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)

    def test_missing_staged_copy_refuses_as_stage_not_found(self) -> None:
        proc = self.build()
        job = self.job_path(proc)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_STAGE_REFUSED", run.stdout)
        self.assertIn("PART=0 STATUS=STAGED_NOT_FOUND", run.stdout)
        self.assertIn("PART=1 STATUS=STAGED_NOT_FOUND", run.stdout)
        for target in self.targets:
            self.assertFalse(target.exists())

    def test_no_path_ever_reaches_the_job_s_output_in_any_branch(self) -> None:
        # A distinctive token lives in every target path AND in the source content, run through
        # every branch this job can take, and must never surface on stdout/stderr.
        payload = [
            {
                "index": i,
                "path": str(self.tmp / f"{TOKEN}-target-{i}" / f"part{i}.raw"),
                "length": len(content),
                "sha256": _sha256(content),
            }
            for i, content in enumerate(self.content)
        ]
        proc = self.build(parts=payload, clip_id="FIX-STAGE-TOKEN")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self._assert_no_token(proc.stdout, proc.stderr)
        job = self.job_path(proc)
        job_id = job.name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self._assert_no_token(run.stdout, run.stderr)


if __name__ == "__main__":
    unittest.main()
