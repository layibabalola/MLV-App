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
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tools" / "profiling" / "bachelor" / "attr3-footage-stage.ps1"
STAGE_MODULE = ROOT / "tools" / "profiling" / "bachelor" / "Attr3FootageStageJob.psm1"
OWNER_FOOTAGE_MODULE = ROOT / "tools" / "profiling" / "bachelor" / "AttrCudaOwnerFootage.psm1"
PRESENCE_MODULE = ROOT / "tools" / "profiling" / "bachelor" / "Attr3FootagePresenceJob.psm1"
ARTIFACTS_MODULE = ROOT / "tools" / "profiling" / "bachelor" / "AttrCudaArtifacts.psm1"
UM_RUN = ROOT / "tools" / "profiling" / "um-run.ps1"
AGENT_SCRIPT = ROOT / "tools" / "profiling" / "ultra-magnus-agent.ps1"
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

    def test_a_junction_at_the_staging_directory_is_refused_and_the_junction_target_is_untouched(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 3 (astra PR #148 MAJOR, containment): a junction planted AT
        # the staging directory itself must be refused before anything under it is touched --
        # otherwise the copy (and a later stale-partial cleanup) would silently follow the
        # junction outside the owned staging directory.
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "part-0.partial").write_bytes(b"unrelated bytes that must survive untouched")
        stage_dir = self.share / "job6"
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", f"New-Item -ItemType Junction -Path '{stage_dir}' -Target '{elsewhere}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not stage_dir.exists():
            self.skipTest(f"cannot create a junction here: {made.stderr}")
        proc = self.send(self.source, stage_dir, 0, len(self.content), self.sha256)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("OWNER_FOOTAGE_STAGE_COPY_FAILED", proc.stdout + proc.stderr)
        self.assertEqual((elsewhere / "part-0.partial").read_bytes(), b"unrelated bytes that must survive untouched")
        self.assertEqual(sorted(p.name for p in elsewhere.iterdir()), ["part-0.partial"])


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job targets a Windows measurement host")
class FootageStageJobTests(unittest.TestCase):
    """tools/profiling/bachelor/Attr3FootageStageJob.psm1's New-Attr3FootageStageJob and the
    emitted job it writes, run directly (never through um-run.ps1 or a real agent)."""

    def setUp(self) -> None:
        # TOKEN never appears in this prefix: since round 3, New-Attr3FootageStageJob's own
        # RESULT=...JOB=... line prints the opaque job id, never the local job FILE path (see
        # test_job_emission_prints_the_job_id_never_the_local_job_file_path below) -- what must
        # never appear is a real FOOTAGE path, which
        # test_no_path_ever_reaches_the_job_s_output_in_any_branch below isolates by putting
        # TOKEN only in its own target directories.
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

    def build(
        self, parts=None, clip_id: str | None = None, agent_root: Path | None = None
    ) -> subprocess.CompletedProcess:
        payload = self.parts_payload if parts is None else parts
        parts_json_path = self.tmp / f"parts-{id(payload)}.json"
        parts_json_path.write_text(json.dumps(payload), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId '{clip_id or self.clip_id}' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{agent_root or self.agent_root}'"
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

    def test_generator_parameter_surface_is_exactly_id_only(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 3 (sol BLOCKER, astra MAJOR): a ban list only catches
        # parameter spellings someone thought to ban -- -RepoRoot (a caller-selectable resolver
        # and submitter tree) shipped right past the old version of this test. Asserting the
        # FULL parameter set instead means any new caller-controlled parameter fails this test
        # by construction, whatever it is named.
        script = (
            f"(Get-Command -CommandType ExternalScript '{GENERATOR}').Parameters.Keys | "
            "Where-Object { @('Verbose','Debug','ErrorAction','WarningAction','InformationAction',"
            "'ErrorVariable','WarningVariable','InformationVariable','OutVariable','OutBuffer',"
            "'PipelineVariable','Confirm','WhatIf','ProgressAction') -notcontains $_ } | ConvertTo-Json -Compress"
        )
        proc = _run(["-Command", script])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        names = json.loads(proc.stdout.strip())
        if isinstance(names, str):
            names = [names]
        self.assertEqual(set(names), {"ClipId", "AgentShare", "AgentRootOnHost", "TimeoutSec"})
        self.assertNotIn("RepoRoot", names)

    def test_agent_share_rejects_a_deeper_caller_chosen_subpath(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 3: -AgentShare must be exactly `\\host\share` (the default's
        # own shape) -- a caller-chosen deeper subpath is refused by parameter validation before
        # this script ever runs.
        proc = _run(["-File", str(GENERATOR), "-ClipId", "NOT-A-REAL-CLIP-ID-ATTR3-STAGE",
                     "-AgentShare", r"\\bachelor\mlv-agent\deeper\subpath"])
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertNotIn("RESULT=FOOTAGE_STAGED", combined)

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

    def test_agent_root_with_an_8_3_short_name_segment_is_accepted(self) -> None:
        # Windows temp roots carry 8.3 short names (RUNNER~1, OBABAL~1) -- this repo's own
        # behavioural tests build -AgentRoot from tempfile.TemporaryDirectory, which lands under
        # whatever TEMP the host has, short-named or not. -AgentRoot's own ValidatePattern must
        # admit `~` or this whole suite is silently host-dependent on TEMP's shape (round 2).
        short_name_root = self.tmp / "OBABAL~1" / "AppData" / "agent"
        short_name_root.mkdir(parents=True)
        target_dir = short_name_root / "spec" / "FIX-STAGE-SHORTNAME"
        targets = [target_dir / "part0.raw", target_dir / "part1.raw"]
        payload = [
            {"index": i, "path": str(path), "length": len(content), "sha256": _sha256(content)}
            for i, (path, content) in enumerate(zip(targets, self.content))
        ]
        proc = self.build(parts=payload, clip_id="FIX-STAGE-SHORTNAME", agent_root=short_name_root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.job_path(proc)

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

    # ---- round 3: containment, cross-volume-safe placement, job-id uniqueness -----------------

    def test_a_junction_at_the_stage_dir_is_refused_before_touching_anything(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 3 (astra PR #148 MAJOR, containment): a junction planted at
        # the per-job staging directory itself must refuse the WHOLE job before a single part is
        # read, copied or cleaned -- proven with a real NTFS junction pointing away from the owned
        # staging tree.
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.agent_root / "footage-stage" / job_id
        stage_dir.parent.mkdir(parents=True, exist_ok=True)
        elsewhere = self.tmp / "elsewhere-stage"
        elsewhere.mkdir()
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path '{stage_dir}' -Target '{elsewhere}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not stage_dir.exists():
            self.skipTest(f"cannot create a junction here: {made.stderr}")
        run = self.run_job(job)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("RESULT=FOOTAGE_STAGE_REFUSED", run.stdout)
        self.assertIn("STATUS=STAGE_SLOT_INVALID", run.stdout)
        self.assertEqual(list(elsewhere.iterdir()), [])
        for target in self.targets:
            self.assertFalse(target.exists())

    def test_placement_leaves_no_stray_partial_slot_in_the_target_directory(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 3 (sol BLOCKER, astra MAJOR x2): placement now goes through
        # an owned, per-attempt partial slot ON THE TARGET'S OWN VOLUME before the final rename --
        # this proves that slot never survives a successful run.
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        remaining = sorted(p.name for p in self.target_dir.iterdir())
        self.assertEqual(remaining, sorted(t.name for t in self.targets))

    def test_job_emission_prints_the_job_id_never_the_local_job_file_path(self) -> None:
        proc = self.build()
        job = self.job_path(proc)
        job_id = job.name[: -len(".job.ps1")]
        self.assertIn(f"JOB={job_id}", proc.stdout)
        self.assertNotIn(str(job), proc.stdout + proc.stderr)

    def test_repeated_builds_for_identical_content_get_different_job_ids(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 3 (astra PR #148 MAJOR): a purely content-derived job id
        # meant a retried submission for the SAME clip and parts always collided with an earlier
        # attempt's own retained result receipt (UMRUN_JOBID_IN_USE). The id must differ per
        # call; the reported SOURCE_SHA256 audit key must not.
        first_proc = self.build()
        first_job = self.job_path(first_proc)
        first_job_id = first_job.name[: -len(".job.ps1")]
        first_job.unlink()
        second_proc = self.build()
        second_job = self.job_path(second_proc)
        second_job_id = second_job.name[: -len(".job.ps1")]
        self.assertNotEqual(first_job_id, second_job_id)
        first_sha = first_proc.stdout.split("SOURCE_SHA256=")[1].split()[0]
        second_sha = second_proc.stdout.split("SOURCE_SHA256=")[1].split()[0]
        self.assertEqual(first_sha, second_sha)


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job and agent target a Windows host")
class EndToEndTransferIdempotenceTests(unittest.TestCase):
    """ATTR3-FOOTAGE-STAGE-1 round 3 (sol/astra PR #148 MAJOR): drives the SAME presence-preflight
    + transfer + placement pipeline attr3-footage-stage.ps1 itself uses -- New-Attr3FootagePresence-
    Job, Send-AttrCudaOwnerFootagePartToStaging, New-Attr3FootageStageJob -- through a REAL,
    locally-run agent (tools/profiling/ultra-magnus-agent.ps1) and the REAL tools/profiling/um-
    run.ps1 submitter, never a hand-rolled double of either. The CLI itself cannot be driven this
    way (its only path to parts is the real resolver against real footage, which this repository's
    tests must never touch), so this calls the underlying functions directly with SYNTHETIC parts
    -- the same split every other test in this file relies on -- submitted through the real
    submit-and-wait mechanism twice, proving the SECOND attempt's presence preflight alone answers
    ALREADY_PRESENT without retransferring a byte, and that neither submission collides with the
    other's own retained result receipt."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3e2e-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.share = self.tmp / "share"
        self.share.mkdir()
        self.agent_proc = subprocess.Popen(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(AGENT_SCRIPT),
             "-Root", str(self.share), "-PollSeconds", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self._stop_agent)
        heartbeat = self.share / "heartbeat.txt"
        deadline = time.time() + 20
        while time.time() < deadline and not heartbeat.exists():
            time.sleep(0.2)
        if not heartbeat.exists():
            self.skipTest("local ultra-magnus-agent.ps1 double did not start in time")

        self.target_dir = self.tmp / "spec" / "FIX-E2E-0001"
        self.target_dir.mkdir(parents=True)
        self.content = (b"synthetic e2e part zero " * 211, b"synthetic e2e part one " * 5)
        self.source = [self.tmp / "src0.raw", self.tmp / "src1.raw"]
        for path, content in zip(self.source, self.content):
            path.write_bytes(content)
        self.targets = [self.target_dir / "part0.raw", self.target_dir / "part1.raw"]
        self.parts = [
            {"index": i, "path": str(target), "length": len(content), "sha256": _sha256(content)}
            for i, (target, content) in enumerate(zip(self.targets, self.content))
        ]

    def _stop_agent(self) -> None:
        try:
            self.agent_proc.terminate()
            self.agent_proc.wait(timeout=10)
        except Exception:
            try:
                self.agent_proc.kill()
            except Exception:
                pass

    def _run_ps1(self, text: str) -> subprocess.CompletedProcess:
        script = self.tmp / f"e2e-{time.monotonic_ns()}.ps1"
        script.write_text(text, encoding="utf-8")
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True, text=True,
        )

    def _presence_check(self) -> subprocess.CompletedProcess:
        out_dir = self.tmp / f"presence-out-{time.monotonic_ns()}"
        parts_json_path = self.tmp / f"presence-parts-{time.monotonic_ns()}.json"
        parts_json_path.write_text(json.dumps(self.parts), encoding="utf-8")
        return self._run_ps1(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{PRESENCE_MODULE}' -Force\n"
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json)\n"
            f"$job = New-Attr3FootagePresenceJob -ClipId 'FIX-E2E-0001' -Parts $parts -OutDir '{out_dir}'\n"
            f"$r = & '{UM_RUN}' -ScriptPath $job.jobFile -JobId $job.jobId -AgentShare '{self.share}' "
            "-TimeoutSec 60 -PollSeconds 1\n"
            "Write-Output ('E2E_PRESENCE_EXIT=' + $r.exitCode)\n"
        )

    def _transfer_and_place(self) -> subprocess.CompletedProcess:
        stage_out_dir = self.tmp / f"stage-out-{time.monotonic_ns()}"
        parts_json_path = self.tmp / f"stage-parts-{time.monotonic_ns()}.json"
        parts_json_path.write_text(json.dumps(self.parts), encoding="utf-8")
        transfers = "\n".join(
            f"Send-AttrCudaOwnerFootagePartToStaging -SourcePath '{src}' -StagingDirectory $shareStageDir "
            f"-Index {part['index']} -ExpectedLength {part['length']} -ExpectedSha256 '{part['sha256']}' | Out-Null"
            for src, part in zip(self.source, self.parts)
        )
        return self._run_ps1(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{ARTIFACTS_MODULE}' -Force\n"
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force\n"
            f"Import-Module '{STAGE_MODULE}' -Force\n"
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json)\n"
            f"$job = New-Attr3FootageStageJob -ClipId 'FIX-E2E-0001' -Parts $parts -OutDir '{stage_out_dir}' -AgentRoot '{self.share}'\n"
            f"$shareStageDir = Join-Path '{self.share}' ('footage-stage\\' + $job.jobId)\n"
            f"{transfers}\n"
            f"$r = & '{UM_RUN}' -ScriptPath $job.jobFile -JobId $job.jobId -AgentShare '{self.share}' "
            "-TimeoutSec 60 -PollSeconds 1\n"
            "Write-Output ('E2E_STAGE_EXIT=' + $r.exitCode)\n"
            "Write-Output $r.stdout\n"
        )

    def test_second_attempt_skips_transfer_via_presence_preflight(self) -> None:
        first_presence = self._presence_check()
        self.assertEqual(first_presence.returncode, 0, first_presence.stdout + first_presence.stderr)
        # FOOTAGE_ABSENT (exit 1): nothing placed yet.
        self.assertIn("E2E_PRESENCE_EXIT=1", first_presence.stdout, first_presence.stdout + first_presence.stderr)

        first_stage = self._transfer_and_place()
        self.assertEqual(first_stage.returncode, 0, first_stage.stdout + first_stage.stderr)
        self.assertIn("E2E_STAGE_EXIT=0", first_stage.stdout, first_stage.stdout + first_stage.stderr)
        for target, content in zip(self.targets, self.content):
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), content)

        # A second, INDEPENDENT presence check -- a fresh job id, submitted through the real
        # agent again -- must report PRESENT without this test ever calling the transfer step
        # again. Before the round-3 fix a repeated presence check with identical content reused
        # the SAME job id and could be refused outright (UMRUN_JOBID_IN_USE) by the first check's
        # own retained receipt; this proves that no longer happens.
        second_presence = self._presence_check()
        self.assertEqual(second_presence.returncode, 0, second_presence.stdout + second_presence.stderr)
        self.assertIn("E2E_PRESENCE_EXIT=0", second_presence.stdout, second_presence.stdout + second_presence.stderr)
        for target, content in zip(self.targets, self.content):
            self.assertEqual(target.read_bytes(), content)

    def test_two_full_stage_submissions_for_the_same_content_both_succeed(self) -> None:
        # astra's own repro: "Run the complete synthetic CLI workflow twice through an agent or
        # faithful um-run double, retaining the first result receipt." With a unique job id per
        # attempt, the SECOND submission never sees UMRUN_JOBID_IN_USE from the first; both must
        # place cleanly, and the retained bytes must never re-transfer or re-place incorrectly.
        first = self._transfer_and_place()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("E2E_STAGE_EXIT=0", first.stdout, first.stdout + first.stderr)

        second = self._transfer_and_place()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("E2E_STAGE_EXIT=0", second.stdout, second.stdout + second.stderr)
        self.assertIn("PART=0 STATUS=ALREADY_PRESENT", second.stdout)
        self.assertIn("PART=1 STATUS=ALREADY_PRESENT", second.stdout)
        for target, content in zip(self.targets, self.content):
            self.assertEqual(target.read_bytes(), content)


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job targets a Windows measurement host")
class NoPathInAnyBranchTests(unittest.TestCase):
    """ATTR3-FOOTAGE-STAGE-1 round 3 (sol/astra PR #148 MAJOR): the specific branches the
    round-2 reviews found still leaking a path -- resolver-missing, a submitter exception forwarded
    unwrapped, and a cleanup warning."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3nopath-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_resolver_missing_message_is_a_fixed_token_never_the_resolver_path(self) -> None:
        text = GENERATOR.read_text(encoding="utf-8")
        self.assertIn(
            "ATTR3_FOOTAGE_STAGE_RESOLVER_MISSING the tracked resolver script is missing from this checkout",
            text,
        )
        self.assertNotIn('resolver not found at $ResolverPath', text)

    def test_submit_failure_is_converted_to_a_fixed_token_never_the_wrapped_exception_text(self) -> None:
        # um-run.ps1 itself throws a message naming a real path (its own heartbeat file) when no
        # agent is listening -- proven here directly -- and this repository's own CLI wraps every
        # call to it in exactly the try/catch pattern below (attr3-footage-stage.ps1's own step 6).
        dead_share = self.tmp / "dead-share"
        dead_share.mkdir()
        job = self.tmp / "noop.job.ps1"
        job.write_text("Write-Output 'unreachable'\n", encoding="utf-8")

        unwrapped = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(UM_RUN),
             "-ScriptPath", str(job), "-AgentShare", str(dead_share), "-TimeoutSec", "1"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(unwrapped.returncode, 0)
        self.assertIn(str(dead_share), unwrapped.stdout + unwrapped.stderr)

        script = self.tmp / "wrapped.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"$umRun = '{UM_RUN}'\n"
            f"$job = '{job}'\n"
            f"$dead = '{dead_share}'\n"
            "try {\n"
            "    $result = & $umRun -ScriptPath $job -AgentShare $dead -TimeoutSec 1\n"
            "} catch {\n"
            "    throw 'ATTR3_FOOTAGE_STAGE_SUBMIT_FAILED job could not be submitted or its result could not be retrieved'\n"
            "}\n",
            encoding="utf-8",
        )
        wrapped = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(wrapped.returncode, 0)
        combined = wrapped.stdout + wrapped.stderr
        self.assertIn("ATTR3_FOOTAGE_STAGE_SUBMIT_FAILED", combined)
        self.assertNotIn(str(dead_share), combined)

    def test_cleanup_warning_is_suppressed_and_never_reaches_output(self) -> None:
        # Remove-AttrCudaPartialFile (AttrCudaArtifacts.psm1) writes a path-bearing Write-Warning
        # diagnostic on a refused cleanup; the emitted stage job's Record-PartResult (round 3)
        # calls it with -WarningAction SilentlyContinue specifically so that never reaches this
        # job's own output. Proven directly against the shared helper: a directory occupying a
        # would-be partial slot is refused (ATTRCUDA_PARTIAL_NOT_A_FILE) and normally warns with
        # the path; suppressed, nothing about it reaches stdout or stderr.
        root = self.tmp / "root"
        occupied = root / "occupied-slot"
        occupied.mkdir(parents=True)
        script = self.tmp / "cleanup.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{ARTIFACTS_MODULE}' -Force\n"
            f"[void](Remove-AttrCudaPartialFile -TrustedRoot '{root}' -Path '{occupied}' -WarningAction SilentlyContinue)\n"
            "Write-Output 'DONE'\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn(str(occupied), proc.stdout + proc.stderr)
        self.assertTrue(occupied.is_dir(), "a directory occupying the slot must be left in place, not deleted")


if __name__ == "__main__":
    unittest.main()
