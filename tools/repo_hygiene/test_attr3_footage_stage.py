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

    def test_a_pre_existing_partial_is_refused_and_left_untouched(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 4 (sol BLOCKER 2a): the share-side partial slot is opened
        # with exclusive creation -- a partial another concurrent attempt is actively writing (or
        # one already occupying the slot for any other reason) is refused, never pre-cleared and
        # overwritten.
        stage_dir = self.share / "job7"
        stage_dir.mkdir()
        partial = stage_dir / "part-0.partial"
        partial.write_bytes(b"bytes a concurrent attempt is still writing")
        proc = self.send(self.source, stage_dir, 0, len(self.content), self.sha256)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("OWNER_FOOTAGE_STAGE_PARTIAL_EXISTS", proc.stdout + proc.stderr)
        self.assertEqual(partial.read_bytes(), b"bytes a concurrent attempt is still writing")
        self.assertFalse((stage_dir / "part-0").exists())

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

    def build_with_corrupt_hook(self, corrupt_index: int) -> subprocess.CompletedProcess:
        # ATTR3-FOOTAGE-STAGE-1 round 4: -TestHookCorruptAfterVerifyPartIndex is a test-only
        # parameter on New-Attr3FootageStageJob, never reachable from the production CLI (see
        # that function's own header) -- calling it directly here is the same split every other
        # row in this file already relies on.
        parts_json_path = self.tmp / f"parts-hook-{id(self.parts_payload)}.json"
        parts_json_path.write_text(json.dumps(self.parts_payload), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId '{self.clip_id}' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}' "
            f"-TestHookCorruptAfterVerifyPartIndex {corrupt_index}"
        )
        return _run(["-Command", script])

    def build_with_corrupt_and_removal_failure_hooks(self, corrupt_index: int, removal_failure_index: int) -> subprocess.CompletedProcess:
        # ATTR3-FOOTAGE-STAGE-1 round 5: -TestHookForceRemovalFailurePartIndex is a second
        # test-only parameter, same non-reachability guarantee as the one above -- see
        # New-Attr3FootageStageJob's own header.
        parts_json_path = self.tmp / f"parts-hook2-{id(self.parts_payload)}.json"
        parts_json_path.write_text(json.dumps(self.parts_payload), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId '{self.clip_id}' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}' "
            f"-TestHookCorruptAfterVerifyPartIndex {corrupt_index} "
            f"-TestHookForceRemovalFailurePartIndex {removal_failure_index}"
        )
        return _run(["-Command", script])

    def build_with_marker_removal_failure_hook(self, marker_removal_failure_index: int) -> subprocess.CompletedProcess:
        # ATTR3-FOOTAGE-STAGE-1 round 6: -TestHookForceMarkerRemovalFailurePartIndex is a fourth
        # test-only parameter, same non-reachability guarantee as the three above -- see
        # New-Attr3FootageStageJob's own header.
        parts_json_path = self.tmp / f"parts-hook4-{id(self.parts_payload)}.json"
        parts_json_path.write_text(json.dumps(self.parts_payload), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId '{self.clip_id}' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}' "
            f"-TestHookForceMarkerRemovalFailurePartIndex {marker_removal_failure_index}"
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
        # ATTR3-FOOTAGE-STAGE-1 round 4 (sol BLOCKER 1): -AgentShare and -AgentRootOnHost are
        # gone too -- both are now fixed constants inside the script, not parameters at all.
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
        self.assertEqual(set(names), {"ClipId", "TimeoutSec"})
        self.assertNotIn("RepoRoot", names)
        self.assertNotIn("AgentShare", names)
        self.assertNotIn("AgentRootOnHost", names)

    def test_agent_share_is_no_longer_a_parameter_at_all(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 3 gave -AgentShare a ValidatePattern refusing a deeper
        # caller-chosen subpath; round 4 (sol BLOCKER 1) removes the parameter entirely -- a
        # caller cannot even NAME an alternate share any more, let alone a deeper subpath under
        # the real one. PowerShell itself refuses to bind an unknown parameter before this
        # script's own body ever runs.
        proc = _run(["-File", str(GENERATOR), "-ClipId", "NOT-A-REAL-CLIP-ID-ATTR3-STAGE",
                     "-AgentShare", r"\\bachelor\mlv-agent\deeper\subpath"])
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertNotIn("RESULT=FOOTAGE_STAGED", combined)
        self.assertIn("AgentShare", combined)

    def test_agent_root_on_host_is_no_longer_a_parameter_at_all(self) -> None:
        # Same closure as above (round 4, sol BLOCKER 1), for the other formerly-public parameter.
        proc = _run(["-File", str(GENERATOR), "-ClipId", "NOT-A-REAL-CLIP-ID-ATTR3-STAGE",
                     "-AgentRootOnHost", r"C:\caller-chosen-root"])
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertNotIn("RESULT=FOOTAGE_STAGED", combined)
        self.assertIn("AgentRootOnHost", combined)

    def test_resolver_refusal_yields_a_typed_refusal_token_and_no_transfer(self) -> None:
        # The REAL resolver, against the REAL repository, with a clip id that does not exist --
        # safe by construction: the resolver only ever opens the frozen spec/consent table text,
        # never a footage path, for an id it refuses.
        proc = _run(["-File", str(GENERATOR), "-ClipId", "NOT-A-REAL-CLIP-ID-ATTR3-STAGE"])
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn("ATTR3_FOOTAGE_STAGE_RESOLVE_REFUSED", combined)
        # ATTR3-FOOTAGE-STAGE-1 round 5 (sol/astra: resolver failure output mapped to a fixed
        # token, never interpolated): the resolver's own raw JSON summary text (which would have
        # carried its "UNKNOWN_ID" status word) is never folded into this script's own message
        # any more -- only the clip id (already ValidatePattern-shaped) and the exit code are.
        self.assertNotIn("UNKNOWN_ID", combined)
        self.assertNotIn('"status"', combined)
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

    def test_a_zero_or_negative_stale_residue_after_sec_is_rejected_by_validate_range(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 6 (sol blocker / astra major: sweep staleness threshold).
        # New-Attr3FootageStageJob has its own trusted-caller contract regardless of attr3-footage-
        # stage.ps1's own body-level TimeoutSec validation -- a caller passing zero or a negative
        # value must be rejected here too, at the parameter binder, never emitted into a job whose
        # own sweep would then treat everything as stale.
        for bad_value in (0, -100):
            script = (
                f"Import-Module '{STAGE_MODULE}' -Force; "
                f"$parts = @(Get-Content -LiteralPath '{self.tmp / 'parts-range.json'}' -Raw | ConvertFrom-Json); "
                f"New-Attr3FootageStageJob -ClipId '{self.clip_id}' -Parts $parts "
                f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}' -StaleResidueAfterSec {bad_value}"
            )
            (self.tmp / "parts-range.json").write_text(json.dumps(self.parts_payload), encoding="utf-8")
            proc = _run(["-Command", script])
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("Cannot validate argument", proc.stdout + proc.stderr)
            self.assertEqual(sorted(self.out.glob("*.job.ps1")), [])

    # ---- round 4: exclusive-creation partials, publish-verify race, link check on the staged leaf

    def test_a_pre_existing_target_volume_partial_is_refused_and_left_untouched(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 4 (sol BLOCKER 2a): the target-volume partial slot is opened
        # with exclusive creation -- a partial another concurrent placer for this exact part is
        # actively writing is refused, never pre-cleared and overwritten.
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        local_partial = self.target_dir / f".attr3-footage-stage-{job_id}-part0.partial"
        local_partial.write_bytes(b"bytes a concurrent placer is still writing")
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=TARGET_VOLUME_PARTIAL_EXISTS", run.stdout)
        self.assertEqual(local_partial.read_bytes(), b"bytes a concurrent placer is still writing")
        self.assertFalse(self.targets[0].exists())
        # Part 1's own placement is unaffected by part 0's refusal.
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)
        self.assertTrue(self.targets[1].is_file())

    def test_corruption_between_local_verify_and_publish_removes_the_target_and_a_rerun_is_not_blocked(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 4 (sol BLOCKER 2b). The test-only corruption hook flips a
        # byte in the LOCAL target-volume partial after it passed local verification but before
        # the same-volume publish rename -- modelling bytes changing between "verified" and
        # "published". The post-rename re-hash must catch it, this job must remove the target IT
        # just placed (never leaving a corrupt file at the spec path under a PLACED-shaped
        # status), and a later rerun must succeed rather than being blocked by a false
        # TARGET_CONFLICT against the bytes this job itself removed.
        proc = self.build_with_corrupt_hook(0)
        job = self.job_path(proc)
        stage_dir = self.stage_dir(job.name[: -len(".job.ps1")])
        self.stage_all_parts(stage_dir)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=PLACED_VERIFY_LENGTH_MISMATCH", run.stdout)
        self.assertFalse(self.targets[0].exists())
        # Part 1 (never corrupted) still places cleanly in the SAME run.
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)
        self.assertTrue(self.targets[1].is_file())
        job.unlink()

        second_proc = self.build()
        second_job = self.job_path(second_proc)
        second_stage_dir = self.stage_dir(second_job.name[: -len(".job.ps1")])
        self.stage_all_parts(second_stage_dir)
        second_run = self.run_job(second_job)
        self.assertEqual(second_run.returncode, 0, second_run.stdout + second_run.stderr)
        self.assertIn("PART=0 STATUS=PLACED", second_run.stdout)
        self.assertNotIn("TARGET_CONFLICT", second_run.stdout)
        self.assertIn("PART=1 STATUS=ALREADY_PRESENT", second_run.stdout)
        for target, content in zip(self.targets, self.content):
            self.assertEqual(target.read_bytes(), content)

    # ---- round 5: cleanup ownership (astra major) -- only delete what THIS attempt created -----

    def test_local_source_open_failure_never_deletes_an_unrelated_pre_existing_file_at_the_partial_slot(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 5 (astra major): before this round, "the share-side staged
        # copy could not be opened for reading" and "this attempt's own CreateNew of the
        # target-volume partial failed for some other reason" were BOTH folded into the same
        # $localCopyFailed flag, and the cleanup handler deleted $localPartialPath unconditionally
        # whenever that flag was set -- even though, in the open-failure branch, THIS ATTEMPT never
        # created (or even attempted to create) that file at all. -TestHookForceLocalSourceOpen-
        # FailurePartIndex models exactly that branch deterministically (see this hook's own
        # header on why the real race it stands in for cannot be won reliably from a test).
        parts_json_path = self.tmp / f"parts-hook3-{id(self.parts_payload)}.json"
        parts_json_path.write_text(json.dumps(self.parts_payload), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId '{self.clip_id}' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}' "
            f"-TestHookForceLocalSourceOpenFailurePartIndex 0"
        )
        proc = _run(["-Command", script])
        job = self.job_path(proc)
        job_id = job.name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        unrelated_partial = self.target_dir / f".attr3-footage-stage-{job_id}-part0.partial"
        unrelated_partial.write_bytes(b"bytes this attempt never created and must never delete")
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=TARGET_VOLUME_COPY_FAILED", run.stdout)
        self.assertEqual(unrelated_partial.read_bytes(), b"bytes this attempt never created and must never delete")
        self.assertFalse(self.targets[0].exists())
        # Part 1 (never hooked) still places cleanly in the SAME run.
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)
        self.assertTrue(self.targets[1].is_file())

    # ---- round 5: publish recovery (sol blocker) -- verified removal, residue recognition -------

    def test_removal_failure_after_publish_verify_fails_reports_a_distinct_token_and_retains_the_target(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 5 (sol blocker): when this job cannot verify that it
        # actually removed the corrupt bytes IT JUST PLACED, it must report a status DISTINCT
        # from the ordinary PLACED_VERIFY_<status> (removal succeeded) case, and must leave a
        # fixed-name residue marker beside the retained bytes.
        proc = self.build_with_corrupt_and_removal_failure_hooks(corrupt_index=0, removal_failure_index=0)
        job = self.job_path(proc)
        stage_dir = self.stage_dir(job.name[: -len(".job.ps1")])
        self.stage_all_parts(stage_dir)
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=PLACED_VERIFY_FAILED_TARGET_RETAINED", run.stdout)
        self.assertNotIn("PART=0 STATUS=PLACED_VERIFY_LENGTH_MISMATCH", run.stdout)
        # The corrupt bytes THIS job placed are still there -- removal was never actually skipped
        # silently; the retained-target token means exactly what it says.
        self.assertTrue(self.targets[0].is_file())
        self.assertNotEqual(self.targets[0].read_bytes(), self.content[0])
        marker = Path(str(self.targets[0]) + ".attr3-footage-stage-verify-failed")
        self.assertTrue(marker.is_file(), "a residue marker must be left beside the retained target")
        # Part 1 (never corrupted, never hooked) still places cleanly in the SAME run.
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)
        self.assertTrue(self.targets[1].is_file())

    def test_a_later_run_recognises_its_own_residue_marker_and_places_cleanly_instead_of_target_conflict(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 5 (sol blocker): a LATER, otherwise-ordinary run for the
        # SAME target must recognise the marker left by the round above as ITS OWN known-bad
        # residue -- clean it up and place fresh bytes -- rather than refusing forever with
        # TARGET_CONFLICT against bytes this tool itself left behind.
        first_proc = self.build_with_corrupt_and_removal_failure_hooks(corrupt_index=0, removal_failure_index=0)
        first_job = self.job_path(first_proc)
        first_stage_dir = self.stage_dir(first_job.name[: -len(".job.ps1")])
        self.stage_all_parts(first_stage_dir)
        first_run = self.run_job(first_job)
        self.assertEqual(first_run.returncode, 1, first_run.stdout + first_run.stderr)
        self.assertIn("PART=0 STATUS=PLACED_VERIFY_FAILED_TARGET_RETAINED", first_run.stdout)
        marker = Path(str(self.targets[0]) + ".attr3-footage-stage-verify-failed")
        self.assertTrue(marker.is_file())
        first_job.unlink()

        second_proc = self.build()
        second_job = self.job_path(second_proc)
        second_stage_dir = self.stage_dir(second_job.name[: -len(".job.ps1")])
        self.stage_all_parts(second_stage_dir)
        second_run = self.run_job(second_job)
        self.assertEqual(second_run.returncode, 0, second_run.stdout + second_run.stderr)
        self.assertNotIn("TARGET_CONFLICT", second_run.stdout)
        self.assertIn("PART=0 STATUS=PLACED", second_run.stdout)
        self.assertIn("PART=1 STATUS=ALREADY_PRESENT", second_run.stdout)
        for target, content in zip(self.targets, self.content):
            self.assertEqual(target.read_bytes(), content)
        self.assertFalse(marker.exists(), "the residue marker must be cleaned up once its target is recovered")

    def test_a_genuinely_foreign_conflicting_target_without_the_marker_still_refuses(self) -> None:
        # The residue-recognition path must never launder an UNRELATED stranger's file at the
        # spec path -- only a target with the fixed-name marker BESIDE it is ever touched.
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.targets[0].write_bytes(b"a stranger's file, no marker beside it")
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=TARGET_CONFLICT", run.stdout)
        self.assertEqual(self.targets[0].read_bytes(), b"a stranger's file, no marker beside it")
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)

    # ---- round 6: residue marker bound to the retained target's own identity --------------------

    def test_an_owner_replaced_target_beside_a_stale_marker_is_not_deleted(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 6 (sol blocker, astra blocker): the marker's mere PRESENCE
        # must never be enough to authorize deletion -- only a target whose CURRENT length, sha256
        # and last-write time still match what the marker recorded. An owner who replaces the
        # retained bytes with a file of their own, leaving the stale marker behind, must have that
        # replacement survive untouched, and the stale marker must be left in place too (never
        # silently cleaned up on a refusal).
        first_proc = self.build_with_corrupt_and_removal_failure_hooks(corrupt_index=0, removal_failure_index=0)
        first_job = self.job_path(first_proc)
        first_stage_dir = self.stage_dir(first_job.name[: -len(".job.ps1")])
        self.stage_all_parts(first_stage_dir)
        first_run = self.run_job(first_job)
        self.assertEqual(first_run.returncode, 1, first_run.stdout + first_run.stderr)
        self.assertIn("PART=0 STATUS=PLACED_VERIFY_FAILED_TARGET_RETAINED", first_run.stdout)
        marker = Path(str(self.targets[0]) + ".attr3-footage-stage-verify-failed")
        self.assertTrue(marker.is_file())
        marker_bytes_before = marker.read_bytes()
        first_job.unlink()

        # The owner replaces the retained (corrupt) target with an unrelated file of their own --
        # the stale marker (which recorded the OLD retained bytes' identity) is left beside it.
        replacement = b"the owner's own replacement file, unrelated to the retained residue"
        self.targets[0].write_bytes(replacement)

        second_proc = self.build()
        second_job = self.job_path(second_proc)
        second_stage_dir = self.stage_dir(second_job.name[: -len(".job.ps1")])
        self.stage_all_parts(second_stage_dir)
        second_run = self.run_job(second_job)
        self.assertEqual(second_run.returncode, 1, second_run.stdout + second_run.stderr)
        self.assertIn("PART=0 STATUS=TARGET_CONFLICT", second_run.stdout)
        self.assertEqual(self.targets[0].read_bytes(), replacement)
        self.assertTrue(marker.is_file(), "a stale marker that no longer matches the target must be left in place")
        self.assertEqual(marker.read_bytes(), marker_bytes_before)
        # Part 1 was already placed by the FIRST run (never hooked) -- the second run correctly
        # finds it ALREADY_PRESENT, not a fresh PLACED.
        self.assertIn("PART=1 STATUS=ALREADY_PRESENT", second_run.stdout)
        self.assertTrue(self.targets[1].is_file())

    def test_marker_removal_failure_after_a_successful_recovery_reports_a_distinct_token_and_retains_the_marker(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 6 (sol blocker: verified marker removal). The recovery that
        # deletes the retained target on the strength of a matching marker must also verify that
        # the marker itself was removed -- a failure there is reported with its own fixed,
        # distinct token rather than silently swallowed.
        first_proc = self.build_with_corrupt_and_removal_failure_hooks(corrupt_index=0, removal_failure_index=0)
        first_job = self.job_path(first_proc)
        first_stage_dir = self.stage_dir(first_job.name[: -len(".job.ps1")])
        self.stage_all_parts(first_stage_dir)
        first_run = self.run_job(first_job)
        self.assertEqual(first_run.returncode, 1, first_run.stdout + first_run.stderr)
        self.assertIn("PART=0 STATUS=PLACED_VERIFY_FAILED_TARGET_RETAINED", first_run.stdout)
        marker = Path(str(self.targets[0]) + ".attr3-footage-stage-verify-failed")
        self.assertTrue(marker.is_file())
        first_job.unlink()

        second_proc = self.build_with_marker_removal_failure_hook(marker_removal_failure_index=0)
        second_job = self.job_path(second_proc)
        second_stage_dir = self.stage_dir(second_job.name[: -len(".job.ps1")])
        self.stage_all_parts(second_stage_dir)
        second_run = self.run_job(second_job)
        self.assertEqual(second_run.returncode, 1, second_run.stdout + second_run.stderr)
        self.assertIn("PART=0 STATUS=RESIDUE_MARKER_REMOVAL_FAILED", second_run.stdout)
        self.assertNotIn("PART=0 STATUS=PLACED", second_run.stdout)
        # The retained (corrupt) target itself WAS recovered -- removed -- even though the marker
        # that authorized that recovery could not itself be removed.
        self.assertFalse(self.targets[0].exists())
        self.assertTrue(marker.is_file(), "a marker that failed to be removed must still be present")
        # Part 1 was already placed by the FIRST run (never hooked) -- the second run correctly
        # finds it ALREADY_PRESENT, not a fresh PLACED.
        self.assertIn("PART=1 STATUS=ALREADY_PRESENT", second_run.stdout)
        self.assertTrue(self.targets[1].is_file())

    def test_a_symlink_as_the_staged_file_leaf_is_refused_before_hashing(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 4 (astra 3, link checks on read paths): the per-job staging
        # directory's own chain check (round 3) only proves the DIRECTORY itself carries no
        # reparse point -- the individual leaf "part-<n>" was never checked on its own. A real
        # NTFS file symlink planted AT that leaf must be refused before a single byte of it is
        # ever hashed.
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        elsewhere = self.tmp / "elsewhere-leaf-target.raw"
        elsewhere.write_bytes(self.content[0])
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType SymbolicLink -Path '{stage_dir / 'part-0'}' -Target '{elsewhere}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not (stage_dir / "part-0").exists():
            self.skipTest(f"cannot create a file symlink here (needs elevation/Developer Mode): {made.stderr}")
        (stage_dir / "part-1").write_bytes(self.content[1])
        run = self.run_job(job)
        self.assertIn("PART=0 STATUS=STAGED_PATH_UNSAFE", run.stdout, run.stdout + run.stderr)
        self.assertFalse(self.targets[0].exists())
        self.assertEqual(elsewhere.read_bytes(), self.content[0])
        # Part 1 (an ordinary staged file) still places cleanly.
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)

    # ---- round 7 (item 2ii/2iii): root-inclusive link containment on the residue marker ---------

    def _identity(self, path: Path) -> dict:
        script = (
            f"$item = Get-Item -LiteralPath '{path}' -Force; "
            f"$sha = (Get-FileHash -LiteralPath '{path}' -Algorithm SHA256).Hash.ToLowerInvariant(); "
            "[pscustomobject]@{ length = $item.Length; sha256 = $sha; ticks = $item.LastWriteTimeUtc.Ticks } | ConvertTo-Json -Compress"
        )
        proc = _run(["-Command", script])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout.strip())

    def test_a_symlink_at_the_residue_marker_path_is_refused_before_reading_it(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 7 (item 2ii): the residue-marker READ had NO link check on
        # the marker's own leaf at all -- $targetPath's chain being link-free says nothing about
        # $residueMarkerPath, a DIFFERENT leaf in the same directory. A symlink planted at this
        # exact fixed name, pointing at a record that would OTHERWISE exactly authorize recovery
        # (correct length/sha256/ticks for the current mismatched target), must still be refused --
        # isolating the LINK check, not a content mismatch, as what gates this.
        proc = self.build()
        job = self.job_path(proc)
        job_id = self.job_path(proc).name[: -len(".job.ps1")]
        stage_dir = self.stage_dir(job_id)
        self.stage_all_parts(stage_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        mismatched = b"pre-existing, different bytes -- the mismatch this recovery reacts to"
        self.targets[0].write_bytes(mismatched)
        identity = self._identity(self.targets[0])

        marker = Path(str(self.targets[0]) + ".attr3-footage-stage-verify-failed")
        real_marker_item = self.tmp / "elsewhere-marker-target.json"
        real_marker_item.write_text(json.dumps({
            "length": identity["length"], "sha256": identity["sha256"],
            "lastWriteTimeUtcTicks": identity["ticks"], "jobId": "attacker-planted",
        }), encoding="utf-8")
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType SymbolicLink -Path '{marker}' -Target '{real_marker_item}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not marker.exists():
            self.skipTest(f"cannot create a file symlink here (needs elevation/Developer Mode): {made.stderr}")
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=TARGET_CONFLICT", run.stdout, run.stdout + run.stderr)
        self.assertEqual(self.targets[0].read_bytes(), mismatched)
        self.assertIn("PART=1 STATUS=PLACED", run.stdout)

    def test_a_pre_existing_file_at_the_residue_marker_path_is_never_overwritten(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 7 (item 2iii): the marker WRITE used [IO.File]::WriteAllText,
        # which overwrites whatever is already there. It now uses [IO.FileMode]::CreateNew --
        # exclusive creation -- so an existing file at this exact fixed name is left untouched and
        # no marker is written (the safe TARGET_CONFLICT default then applies to any later run).
        proc = self.build_with_corrupt_and_removal_failure_hooks(corrupt_index=0, removal_failure_index=0)
        job = self.job_path(proc)
        stage_dir = self.stage_dir(job.name[: -len(".job.ps1")])
        self.stage_all_parts(stage_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        marker = Path(str(self.targets[0]) + ".attr3-footage-stage-verify-failed")
        marker.write_bytes(b"pre-existing content that must never be overwritten by the marker write")
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=PLACED_VERIFY_FAILED_TARGET_RETAINED", run.stdout)
        self.assertEqual(marker.read_bytes(), b"pre-existing content that must never be overwritten by the marker write")

    def test_a_symlink_at_the_residue_marker_path_is_never_written_through(self) -> None:
        # Same refusal, for a reparse point specifically rather than an ordinary pre-existing file.
        proc = self.build_with_corrupt_and_removal_failure_hooks(corrupt_index=0, removal_failure_index=0)
        job = self.job_path(proc)
        stage_dir = self.stage_dir(job.name[: -len(".job.ps1")])
        self.stage_all_parts(stage_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        marker = Path(str(self.targets[0]) + ".attr3-footage-stage-verify-failed")
        real_elsewhere = self.tmp / "elsewhere-marker-write-target.raw"
        real_elsewhere.write_bytes(b"unrelated bytes that must survive untouched")
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType SymbolicLink -Path '{marker}' -Target '{real_elsewhere}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not marker.exists():
            self.skipTest(f"cannot create a file symlink here (needs elevation/Developer Mode): {made.stderr}")
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=PLACED_VERIFY_FAILED_TARGET_RETAINED", run.stdout)
        self.assertEqual(real_elsewhere.read_bytes(), b"unrelated bytes that must survive untouched")

    # ---- round 7 (item 1): whole-template outer boundary, sentinel leak proofs -------------------

    def build_with_dispose_throw_hook(self, index: int, parts, clip_id: str) -> subprocess.CompletedProcess:
        parts_json_path = self.tmp / f"parts-disposehook-{id(parts)}.json"
        parts_json_path.write_text(json.dumps(parts), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId '{clip_id}' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}' "
            f"-TestHookForceDisposeThrowPartIndex {index}"
        )
        return _run(["-Command", script])

    def test_a_dispose_failure_never_leaks_its_own_path_bearing_message(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 7 (item 1, dispose sentinel): -TestHookForceDisposeThrow-
        # PartIndex makes the target-volume destination stream's own Dispose() throw a message
        # naming the real local partial path (which lives under the real footage target
        # directory). The round-7 Dispose-in-finally fix must map that to the existing
        # TARGET_VOLUME_COPY_FAILED status -- never let the thrown .Message reach this job's own
        # output.
        payload = [{
            "index": 0, "path": str(self.tmp / f"{TOKEN}-dispose-target" / "part0.raw"),
            "length": len(self.content[0]), "sha256": _sha256(self.content[0]),
        }]
        proc = self.build_with_dispose_throw_hook(0, payload, "FIX-STAGE-DISPOSE-SENTINEL")
        job = self.job_path(proc)
        stage_dir = self.stage_dir(job.name[: -len(".job.ps1")])
        (stage_dir / "part-0").write_bytes(self.content[0])
        run = self.run_job(job)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("PART=0 STATUS=TARGET_VOLUME_COPY_FAILED", run.stdout, run.stdout + run.stderr)
        self._assert_no_token(run.stdout, run.stderr)
        self.assertNotIn("ATTR3_TEST_SENTINEL", run.stdout + run.stderr)

    def build_with_arbitrary_throw_hook(self, index: int, parts, clip_id: str) -> subprocess.CompletedProcess:
        parts_json_path = self.tmp / f"parts-arbthrow-{id(parts)}.json"
        parts_json_path.write_text(json.dumps(parts), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId '{clip_id}' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}' "
            f"-TestHookForceArbitraryThrowPartIndex {index}"
        )
        return _run(["-Command", script])

    def test_an_arbitrary_unwrapped_throw_is_caught_by_the_whole_template_boundary(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 7 (item 1, "at an arbitrary site" sentinel): -TestHookForce-
        # ArbitraryThrowPartIndex throws, untyped and unwrapped by any per-part try/catch, naming
        # the real target path -- modelling a genuinely unanticipated future failure. The
        # whole-template try/catch this round adds must be the backstop: only the fixed
        # RESULT=FOOTAGE_STAGE_JOB_ERROR token reaches output, never the thrown message, and no
        # per-part status is ever emitted for the throwing part (it never got that far).
        payload = [{
            "index": 0, "path": str(self.tmp / f"{TOKEN}-arbitrary-target" / "part0.raw"),
            "length": len(self.content[0]), "sha256": _sha256(self.content[0]),
        }]
        proc = self.build_with_arbitrary_throw_hook(0, payload, "FIX-STAGE-ARBITRARY-SENTINEL")
        job = self.job_path(proc)
        stage_dir = self.stage_dir(job.name[: -len(".job.ps1")])
        (stage_dir / "part-0").write_bytes(self.content[0])
        run = self.run_job(job)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("RESULT=FOOTAGE_STAGE_JOB_ERROR CLIP=FIX-STAGE-ARBITRARY-SENTINEL", run.stdout)
        self._assert_no_token(run.stdout, run.stderr)
        self.assertNotIn("ATTR3_TEST_SENTINEL", run.stdout + run.stderr)
        self.assertNotIn("PART=0 STATUS=", run.stdout)


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job and agent target a Windows host")
class StagingResidueCleanupTests(unittest.TestCase):
    """AttrCudaOwnerFootage.psm1's Remove-AttrCudaOwnerFootageStagingResidue (ATTR3-FOOTAGE-STAGE-1
    round 4, sol minor / astra 5: no stranded parts after a failed attr3-footage-stage.ps1
    attempt)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3residue-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.share_root = self.tmp / "footage-stage"
        self.share_root.mkdir()

    def remove(self, directory: Path) -> subprocess.CompletedProcess:
        script = (
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force; "
            f"Remove-AttrCudaOwnerFootageStagingResidue -TrustedRoot '{self.share_root}' -Directory '{directory}'"
        )
        return _run(["-Command", script])

    def test_removes_staged_part_slots_and_partials_and_the_now_empty_directory(self) -> None:
        attempt_dir = self.share_root / "attempt-1"
        attempt_dir.mkdir()
        (attempt_dir / "part-0").write_bytes(b"staged part zero")
        (attempt_dir / "part-1.partial").write_bytes(b"in-flight part one")
        proc = self.remove(attempt_dir)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(attempt_dir.exists())

    def test_leaves_an_unrecognized_file_in_place_and_does_not_remove_the_directory(self) -> None:
        attempt_dir = self.share_root / "attempt-2"
        attempt_dir.mkdir()
        (attempt_dir / "part-0").write_bytes(b"staged part zero")
        (attempt_dir / "unrelated.txt").write_bytes(b"not this cleanup's to touch")
        proc = self.remove(attempt_dir)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(attempt_dir.is_dir())
        self.assertFalse((attempt_dir / "part-0").exists())
        self.assertTrue((attempt_dir / "unrelated.txt").exists())

    def test_a_missing_directory_is_a_silent_noop(self) -> None:
        proc = self.remove(self.share_root / "never-existed")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_directory_outside_the_trusted_root_is_refused_and_left_in_place(self) -> None:
        outside = self.tmp / "outside-root"
        outside.mkdir()
        (outside / "part-0").write_bytes(b"not this attempt's to touch")
        proc = self.remove(outside)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((outside / "part-0").exists())


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
            "Write-Output $r.stdout\n"
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

    # ---- round 4: per-part resume and no stranded parts (sol minor / astra 5) -------------------

    def test_per_part_resume_only_transfers_the_part_still_missing_after_a_partial_failure(self) -> None:
        # Models exactly what attr3-footage-stage.ps1 itself does across two attempts: attempt 1
        # is BUILT for both parts (mirroring the CLI, which does not yet know part 1's transfer
        # will never happen) but only part 0 actually gets staged to the share before the attempt
        # is abandoned (simulating a failure after part 0 placed) -- so the job itself reports
        # part 0 PLACED and part 1 STAGED_NOT_FOUND, refusing overall. The presence preflight
        # before attempt 2 then reports part 0 PASS / part 1 NOT_FOUND,
        # Get-Attr3FootagePresentPartIndexes turns that into "needs work: part 1 only", and
        # attempt 2 transfers and places just that one part -- part 0's own SOURCE is never read
        # or transferred a second time.
        stage_out_dir_1 = self.tmp / "resume-stage-out-1"
        parts_json_path_1 = self.tmp / "resume-parts-1.json"
        parts_json_path_1.write_text(json.dumps(self.parts), encoding="utf-8")
        attempt1 = self._run_ps1(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{ARTIFACTS_MODULE}' -Force\n"
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force\n"
            f"Import-Module '{STAGE_MODULE}' -Force\n"
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path_1}' -Raw | ConvertFrom-Json)\n"
            f"$job = New-Attr3FootageStageJob -ClipId 'FIX-E2E-0001' -Parts $parts -OutDir '{stage_out_dir_1}' -AgentRoot '{self.share}'\n"
            f"$shareStageDir = Join-Path '{self.share}' ('footage-stage\\' + $job.jobId)\n"
            # Only part 0 is staged -- part 1's transfer never happens this attempt.
            f"Send-AttrCudaOwnerFootagePartToStaging -SourcePath '{self.source[0]}' -StagingDirectory $shareStageDir "
            f"-Index 0 -ExpectedLength {len(self.content[0])} -ExpectedSha256 '{_sha256(self.content[0])}' | Out-Null\n"
            f"$r = & '{UM_RUN}' -ScriptPath $job.jobFile -JobId $job.jobId -AgentShare '{self.share}' "
            "-TimeoutSec 60 -PollSeconds 1\n"
            "Write-Output ('E2E_ATTEMPT1_EXIT=' + $r.exitCode)\n"
            "Write-Output $r.stdout\n"
        )
        self.assertIn("E2E_ATTEMPT1_EXIT=1", attempt1.stdout, attempt1.stdout + attempt1.stderr)
        self.assertIn("PART=1 STATUS=STAGED_NOT_FOUND", attempt1.stdout)
        self.assertIn("PART=0 STATUS=PLACED", attempt1.stdout)
        self.assertTrue(self.targets[0].is_file())
        self.assertFalse(self.targets[1].exists())

        presence = self._presence_check()
        self.assertIn("E2E_PRESENCE_EXIT=3", presence.stdout, presence.stdout + presence.stderr)

        presence_stdout_file = self.tmp / "presence-stdout.txt"
        presence_stdout_file.write_text(presence.stdout, encoding="utf-8")
        resolve = self._run_ps1(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{PRESENCE_MODULE}' -Force\n"
            f"$stdout = Get-Content -LiteralPath '{presence_stdout_file}' -Raw\n"
            "(Get-Attr3FootagePresentPartIndexes -Stdout $stdout -ClipId 'FIX-E2E-0001') -join ','\n"
        )
        self.assertEqual(resolve.returncode, 0, resolve.stdout + resolve.stderr)
        self.assertEqual(resolve.stdout.strip(), "0")

        # Attempt 2: only part 1 is transferred.
        stage_out_dir_2 = self.tmp / "resume-stage-out-2"
        parts_json_path_2 = self.tmp / "resume-parts-2.json"
        parts_json_path_2.write_text(json.dumps([self.parts[1]]), encoding="utf-8")
        attempt2 = self._run_ps1(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{ARTIFACTS_MODULE}' -Force\n"
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force\n"
            f"Import-Module '{STAGE_MODULE}' -Force\n"
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path_2}' -Raw | ConvertFrom-Json)\n"
            f"$job = New-Attr3FootageStageJob -ClipId 'FIX-E2E-0001' -Parts $parts -OutDir '{stage_out_dir_2}' -AgentRoot '{self.share}'\n"
            f"$shareStageDir = Join-Path '{self.share}' ('footage-stage\\' + $job.jobId)\n"
            f"Send-AttrCudaOwnerFootagePartToStaging -SourcePath '{self.source[1]}' -StagingDirectory $shareStageDir "
            f"-Index 1 -ExpectedLength {len(self.content[1])} -ExpectedSha256 '{_sha256(self.content[1])}' | Out-Null\n"
            f"$r = & '{UM_RUN}' -ScriptPath $job.jobFile -JobId $job.jobId -AgentShare '{self.share}' "
            "-TimeoutSec 60 -PollSeconds 1\n"
            "Write-Output ('E2E_ATTEMPT2_EXIT=' + $r.exitCode)\n"
            "Write-Output $r.stdout\n"
        )
        self.assertIn("E2E_ATTEMPT2_EXIT=0", attempt2.stdout, attempt2.stdout + attempt2.stderr)
        self.assertIn("PART=1 STATUS=PLACED", attempt2.stdout)
        for target, content in zip(self.targets, self.content):
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), content)

    def test_a_transfer_failure_after_one_part_staged_leaves_no_residue_after_cleanup(self) -> None:
        # Models exactly what attr3-footage-stage.ps1's own step 5 does when a LATER part's
        # transfer throws after an EARLIER part already staged successfully: nothing is ever
        # submitted to Bachelor, so no job ever gets a chance to clean up the share-side copy
        # itself -- this script's own residue cleanup (Remove-AttrCudaOwnerFootageStagingResidue)
        # is what removes it.
        share_stage_root = self.share / "footage-stage"
        share_stage_dir = share_stage_root / "attempt-fail-1"
        proc0 = self._run_ps1(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{ARTIFACTS_MODULE}' -Force\n"
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force\n"
            f"Send-AttrCudaOwnerFootagePartToStaging -SourcePath '{self.source[0]}' "
            f"-StagingDirectory '{share_stage_dir}' -Index 0 -ExpectedLength {len(self.content[0])} "
            f"-ExpectedSha256 '{_sha256(self.content[0])}' | Out-Null\n"
            "Write-Output DONE\n"
        )
        self.assertEqual(proc0.returncode, 0, proc0.stdout + proc0.stderr)
        self.assertTrue((share_stage_dir / "part-0").is_file())

        # Part 1's transfer fails source verification (wrong expected hash) -- the CLI's own step
        # 5 throws ATTR3_FOOTAGE_STAGE_TRANSFER_FAILED here and never reaches the submit step.
        proc1 = self._run_ps1(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{ARTIFACTS_MODULE}' -Force\n"
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force\n"
            f"Send-AttrCudaOwnerFootagePartToStaging -SourcePath '{self.source[1]}' "
            f"-StagingDirectory '{share_stage_dir}' -Index 1 -ExpectedLength {len(self.content[1])} "
            f"-ExpectedSha256 '{'0' * 64}'\n"
        )
        self.assertNotEqual(proc1.returncode, 0)

        cleanup = self._run_ps1(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force\n"
            f"Remove-AttrCudaOwnerFootageStagingResidue -TrustedRoot '{share_stage_root}' -Directory '{share_stage_dir}'\n"
        )
        self.assertEqual(cleanup.returncode, 0, cleanup.stdout + cleanup.stderr)
        self.assertFalse(share_stage_dir.exists())


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

    def test_a_non_throwing_submission_result_carrying_a_sentinel_path_never_forwards_it(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 4 (sol MAJOR, astra 4: path-free output). The submitted
        # job's own stdout/stderr are never forwarded verbatim -- this drives
        # ConvertTo-Attr3FootageStageSafeOutput (attr3-footage-stage.ps1's own helper, extracted
        # here via the AST -- the CLI itself never exposes a way to fake a submission result)
        # directly against fabricated text carrying a sentinel path inside a line that does NOT
        # match either allowlisted shape, and inside a part status field that DOES look
        # PART=/STATUS=-shaped but fails the strict token pattern -- neither may survive.
        sentinel_dir = self.tmp / f"{TOKEN}-sentinel"
        sentinel_dir.mkdir()
        sentinel_path = str(sentinel_dir / "real-owner-footage.raw")
        raw_text = (
            "[job-1] START clip=FIX-SAFE-0001 parts=2\n"
            "PART=0 STATUS=PLACED\n"
            f"a stray diagnostic line naming {sentinel_path} that must never be forwarded\n"
            f"PART=1 STATUS={sentinel_path}\n"
            "PART=1 STATUS=STAGED_NOT_FOUND\n"
            "RESULT=FOOTAGE_STAGE_REFUSED CLIP=FIX-SAFE-0001 PARTS=2\n"
            + json.dumps({
                "schema": "mlvapp.attr3-footage-stage.v1", "jobId": "job-1", "clipId": "FIX-SAFE-0001",
                "result": "FOOTAGE_STAGE_REFUSED", "partCount": 2,
                "parts": [{"index": 0, "status": "PLACED"}, {"index": 1, "status": "STAGED_NOT_FOUND", "note": sentinel_path}],
            }) + "\n"
        )
        text_file = self.tmp / "raw_output.txt"
        text_file.write_text(raw_text, encoding="utf-8")
        script = (
            f"$genText = [IO.File]::ReadAllText('{GENERATOR}'); "
            "$t=$null; $e=$null; "
            "$ast = [System.Management.Automation.Language.Parser]::ParseInput($genText, [ref]$t, [ref]$e); "
            "$fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and "
            "$n.Name -eq 'ConvertTo-Attr3FootageStageSafeOutput' }, $true) | Select-Object -First 1; "
            "if (-not $fn) { throw 'FUNCTION_NOT_FOUND' }; "
            "Invoke-Expression $fn.Extent.Text; "
            f"$rawText = Get-Content -LiteralPath '{text_file}' -Raw; "
            "ConvertTo-Attr3FootageStageSafeOutput -Text $rawText -ClipId 'FIX-SAFE-0001'"
        )
        proc = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn(sentinel_path, proc.stdout)
        self.assertNotIn(TOKEN, proc.stdout)
        self.assertIn("PART=0 STATUS=PLACED", proc.stdout)
        self.assertIn("PART=1 STATUS=STAGED_NOT_FOUND", proc.stdout)
        self.assertIn("SUBMITTER RESULT=FOOTAGE_STAGE_REFUSED PARTS=2", proc.stdout)

    # ---- round 7 (item 1): the CLI's own whole-body outer boundary, sentinel leak proofs ---------

    def _run_generator_outer_catch(self, throw_statement: str, clip_id: str) -> subprocess.CompletedProcess:
        # Drives the REAL catch block, extracted via AST from the tracked generator -- never a
        # hand-copied duplicate -- exactly like ConvertTo-Attr3FootageStageSafeOutput's own
        # extraction above. -throw_statement supplies the PowerShell statement that raises the
        # exception the extracted catch then has to handle. Written to a temp .ps1 FILE and run
        # via -File (never -Command with the statement inlined): -throw_statement itself embeds a
        # real path in a quoted string, and process-argument quoting for a single -Command string
        # containing nested single AND double quotes is fragile on Windows.
        harness = self.tmp / f"outer-catch-harness-{id(throw_statement)}.ps1"
        harness.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"$genText = [IO.File]::ReadAllText('{GENERATOR}')\n"
            "$t=$null; $e=$null\n"
            "$ast = [System.Management.Automation.Language.Parser]::ParseInput($genText, [ref]$t, [ref]$e)\n"
            "$tryStatement = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.TryStatementAst] }, $true) | Select-Object -First 1\n"
            "if (-not $tryStatement) { throw 'TRY_NOT_FOUND' }\n"
            # .Body.Extent.Text includes the surrounding '{' '}' -- [scriptblock]::Create on THAT
            # text would parse the whole thing as a single bare script-block-literal STATEMENT
            # (producing the inner scriptblock as an output VALUE, never running its statements);
            # stripping the outer braces first makes the extracted STATEMENTS themselves the
            # created scriptblock's own body, so dot-sourcing it actually executes them.
            "$catchBodyText = $tryStatement.CatchClauses[0].Body.Extent.Text\n"
            "$catchBody = $catchBodyText.Substring(1, $catchBodyText.Length - 2)\n"
            f"$ClipId = '{clip_id}'\n"
            "try {\n"
            f"    {throw_statement}\n"
            "} catch {\n"
            "    . ([scriptblock]::Create($catchBody))\n"
            "}\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(harness)],
            capture_output=True, text=True,
        )

    def test_the_outer_catch_never_forwards_a_non_token_shaped_exception_message(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 7 (item 1: outer boundary). A synthetic exception shaped
        # like a raw, unanticipated .NET exception (not one of this script's own established
        # fixed tokens), whose own .Message carries a real-looking footage path. Only the
        # exception's TYPE NAME may reach output; the sentinel path text must not.
        sentinel_path = str(self.tmp / f"{TOKEN}-real-owner-footage.raw")
        proc = self._run_generator_outer_catch(
            f"throw [System.IO.IOException]::new(\"Could not access '{sentinel_path}'\")",
            "FIX-SENTINEL-EMISSION",
        )
        combined = proc.stdout + proc.stderr
        self.assertNotIn(sentinel_path, combined)
        self.assertNotIn(TOKEN, combined)
        self.assertIn("RESULT=FOOTAGE_STAGE_ERROR CLIP=FIX-SENTINEL-EMISSION CLASS=IOException", combined)

    def test_the_outer_catch_forwards_an_already_safe_fixed_token_message_verbatim(self) -> None:
        # The other half of the same rule: a message ALREADY shaped like this script's own
        # established ATTR3_*-style fixed token is forwarded verbatim, so every existing refusal
        # token the rest of this suite already asserts on keeps working unchanged.
        proc = self._run_generator_outer_catch(
            "throw 'ATTR3_FOOTAGE_STAGE_RESOLVE_REFUSED synthetic refusal for this test only'",
            "FIX-SENTINEL-SAFE-TOKEN",
        )
        combined = proc.stdout + proc.stderr
        self.assertIn("ATTR3_FOOTAGE_STAGE_RESOLVE_REFUSED synthetic refusal for this test only", combined)
        self.assertNotIn("CLASS=", combined)


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job and agent target a Windows host")
class StaleAttemptSweepTests(unittest.TestCase):
    """AttrCudaOwnerFootage.psm1's Remove-AttrCudaOwnerFootageStaleAttempts (ATTR3-FOOTAGE-STAGE-1
    round 5, sol minor / astra major: interrupted-attempt residue) -- the CLI-side sweep attr3-
    footage-stage.ps1 now runs at the start of every invocation, tested directly against the
    module function rather than through the CLI (whose AgentShare is a fixed constant this suite
    may not point at real infrastructure)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3stalesweep-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.share_root = self.tmp / "footage-stage"
        self.share_root.mkdir()

    def sweep(self, stale_after_sec: int) -> subprocess.CompletedProcess:
        script = (
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force; "
            f"Remove-AttrCudaOwnerFootageStaleAttempts -TrustedRoot '{self.share_root}' -StaleAfterSec {stale_after_sec}"
        )
        return _run(["-Command", script])

    def _age(self, path: Path, seconds_old: int) -> None:
        script = (
            f"(Get-Item -LiteralPath '{path}' -Force).LastWriteTimeUtc = "
            f"(Get-Date).ToUniversalTime().AddSeconds(-{seconds_old})"
        )
        proc = _run(["-Command", script])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_stale_owned_attempt_directory_is_removed(self) -> None:
        stale_dir = self.share_root / "attr3-footage-stage-FIX-SWEEP-0001-abcdef012345-0123456789"
        stale_dir.mkdir()
        part = stale_dir / "part-0"
        part.write_bytes(b"stale owned partial")
        self._age(part, 4000)
        self._age(stale_dir, 4000)
        proc = self.sweep(stale_after_sec=1800)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(stale_dir.exists(), "a stale owned attempt directory must be removed")

    def test_a_fresh_owned_attempt_directory_is_kept(self) -> None:
        fresh_dir = self.share_root / "attr3-footage-stage-FIX-SWEEP-0002-abcdef012345-9876543210"
        fresh_dir.mkdir()
        part = fresh_dir / "part-0"
        part.write_bytes(b"fresh in-flight partial")
        proc = self.sweep(stale_after_sec=1800)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(fresh_dir.is_dir(), "a fresh, still-within-timeout attempt must survive the sweep")
        self.assertEqual(part.read_bytes(), b"fresh in-flight partial")

    def test_a_non_matching_directory_name_is_never_touched_regardless_of_age(self) -> None:
        foreign_dir = self.share_root / "some-other-unrelated-directory"
        foreign_dir.mkdir()
        part = foreign_dir / "part-0"
        part.write_bytes(b"not this tool's naming convention at all")
        self._age(part, 4000)
        self._age(foreign_dir, 4000)
        proc = self.sweep(stale_after_sec=1800)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(foreign_dir.is_dir(), "a directory not matching this tool's own jobId shape must never be touched")
        self.assertEqual(part.read_bytes(), b"not this tool's naming convention at all")

    def test_a_zero_or_negative_stale_after_sec_is_rejected_by_validate_range(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 6 (sol blocker / astra major: sweep staleness threshold).
        # This function has its own trusted-caller contract regardless of attr3-footage-stage.ps1's
        # own body-level TimeoutSec validation -- a caller passing zero or a negative value must be
        # rejected here too, at the parameter binder, never silently treated as "everything is
        # stale".
        for bad_value in (0, -100):
            proc = self.sweep(stale_after_sec=bad_value)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("Cannot validate argument", proc.stdout + proc.stderr)

    # ---- round 7 (class a): root-inclusive link containment on the sweep itself -----------------

    def test_a_junction_at_the_trusted_root_itself_is_refused_and_nothing_under_it_is_touched(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 7 (item 2i): Assert-AttrCudaNoLinkBelowRoot never inspected
        # -TrustedRoot itself (only components strictly below it) -- a junction planted AT the
        # staging root would previously have been followed by this sweep's own Get-ChildItem
        # before any check could refuse it. The sweep must now skip entirely (Assert-Attr3NoLink-
        # FromBoundary refuses the root itself) with nothing under the junction's target touched.
        elsewhere = self.tmp / "elsewhere-root-target"
        elsewhere.mkdir()
        stale_dir = elsewhere / "attr3-footage-stage-FIX-SWEEP-ROOT-abcdef012345-0123456789"
        stale_dir.mkdir()
        part = stale_dir / "part-0"
        part.write_bytes(b"bytes behind the root junction, must never be touched")
        self._age(part, 4000)
        self._age(stale_dir, 4000)
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path '{self.share_root}' -Target '{elsewhere}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not self.share_root.exists():
            self.skipTest(f"cannot create a junction here: {made.stderr}")
        proc = self.sweep(stale_after_sec=1800)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(part.exists(), "a stale entry reachable only through a root junction must never be swept")
        self.assertEqual(part.read_bytes(), b"bytes behind the root junction, must never be touched")

    def test_a_junction_candidate_directly_under_the_trusted_root_is_skipped_before_enumeration(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 7 (item 2i): a candidate directory ITSELF may be a reparse
        # point whose NAME happens to match this tool's own stale-jobId shape -- skipped before its
        # own children are ever enumerated (Get-ChildItem), never after.
        elsewhere = self.tmp / "elsewhere-candidate-target"
        elsewhere.mkdir()
        (elsewhere / "part-0").write_bytes(b"bytes behind the candidate junction, must never be touched")
        candidate = self.share_root / "attr3-footage-stage-FIX-SWEEP-CAND-abcdef012345-0123456789"
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path '{candidate}' -Target '{elsewhere}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not candidate.exists():
            self.skipTest(f"cannot create a junction here: {made.stderr}")
        proc = self.sweep(stale_after_sec=1800)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(candidate.exists(), "the junction candidate itself must be left in place, never followed or removed")
        self.assertEqual((elsewhere / "part-0").read_bytes(), b"bytes behind the candidate junction, must never be touched")


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job targets a Windows measurement host")
class StageJobStaleResidueSweepTests(unittest.TestCase):
    """Attr3FootageStageJob.psm1's own emitted-job start-of-run sweep of a target-volume local
    partial left behind by an interrupted earlier attempt (ATTR3-FOOTAGE-STAGE-1 round 5, sol
    minor / astra major: interrupted-attempt residue)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3stagesweep-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.agent_root = self.tmp / "agent"
        self.agent_root.mkdir()
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.target_dir = self.agent_root / "spec" / "FIX-STAGE-SWEEP-0001"
        self.content = b"synthetic sweep-test part zero " * 41
        self.parts_payload = [
            {"index": 0, "path": str(self.target_dir / "part0.raw"), "length": len(self.content), "sha256": _sha256(self.content)}
        ]

    def build(self, stale_after_sec: int) -> Path:
        parts_json_path = self.tmp / "parts.json"
        parts_json_path.write_text(json.dumps(self.parts_payload), encoding="utf-8")
        script = (
            f"Import-Module '{STAGE_MODULE}' -Force; "
            f"$parts = @(Get-Content -LiteralPath '{parts_json_path}' -Raw | ConvertFrom-Json); "
            f"New-Attr3FootageStageJob -ClipId 'FIX-STAGE-SWEEP-0001' -Parts $parts "
            f"-OutDir '{self.out}' -AgentRoot '{self.agent_root}' -StaleResidueAfterSec {stale_after_sec}"
        )
        proc = _run(["-Command", script])
        jobs = sorted(self.out.glob("*.job.ps1"))
        self.assertEqual(len(jobs), 1, proc.stdout + proc.stderr)
        return jobs[0]

    def run_job(self, job: Path) -> subprocess.CompletedProcess:
        return _run(["-File", str(job)])

    def _age(self, path: Path, seconds_old: int) -> None:
        script = (
            f"(Get-Item -LiteralPath '{path}' -Force).LastWriteTimeUtc = "
            f"(Get-Date).ToUniversalTime().AddSeconds(-{seconds_old})"
        )
        proc = _run(["-Command", script])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_stale_owned_local_partial_from_an_interrupted_attempt_is_removed_and_a_fresh_placement_still_succeeds(self) -> None:
        job = self.build(stale_after_sec=1800)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        stale_partial = self.target_dir / ".attr3-footage-stage-attr3-footage-stage-FIX-STAGE-SWEEP-0001-deadbeef0000-aaaaaaaaaa-part0.partial"
        stale_partial.write_bytes(b"an earlier, interrupted attempt's own bytes")
        self._age(stale_partial, 4000)
        # No staged copy is placed for this run -- the point of this test is only the SWEEP, which
        # must run and remove the stale partial before this job's own STAGE_NOT_FOUND refusal.
        run = self.run_job(job)
        self.assertFalse(stale_partial.exists(), "a stale owned local partial must be swept at job start")
        self.assertIn("PART=0 STATUS=STAGED_NOT_FOUND", run.stdout, run.stdout + run.stderr)

    def test_a_fresh_local_partial_is_kept_and_a_non_matching_file_is_kept(self) -> None:
        job = self.build(stale_after_sec=1800)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        fresh_partial = self.target_dir / ".attr3-footage-stage-attr3-footage-stage-FIX-STAGE-SWEEP-0001-deadbeef0000-bbbbbbbbbb-part0.partial"
        fresh_partial.write_bytes(b"a concurrent placer's still-in-flight bytes")
        non_matching = self.target_dir / "some-unrelated-file.txt"
        non_matching.write_bytes(b"not this tool's naming convention at all")
        run = self.run_job(job)
        self.assertTrue(fresh_partial.is_file(), "a fresh, still-within-timeout local partial must survive the sweep")
        self.assertEqual(fresh_partial.read_bytes(), b"a concurrent placer's still-in-flight bytes")
        self.assertTrue(non_matching.is_file(), "a file not matching this tool's own partial-name pattern must never be touched")
        self.assertEqual(non_matching.read_bytes(), b"not this tool's naming convention at all")
        self.assertIn("PART=0 STATUS=STAGED_NOT_FOUND", run.stdout, run.stdout + run.stderr)

    # ---- round 6: sweep order (link check before enumeration) and exact name grammar -----------

    def test_a_lookalike_name_not_matching_the_exact_job_id_grammar_is_kept(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 6 (sol blocker, astra blocker: sweep grammar). The round 5
        # pattern matched ANY ".attr3-footage-stage-<anything>-part<n>.partial" -- broader than
        # this tool's own local-partial name, which always carries the DOUBLED
        # "attr3-footage-stage-" prefix (the fixed ".attr3-footage-stage-" lead-in, followed by
        # this job's own jobId, which itself starts with "attr3-footage-stage-"). A lookalike using
        # only the single-prefix shape -- which this tool's own generator could never produce --
        # must survive the sweep regardless of age.
        job = self.build(stale_after_sec=1800)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        lookalike = self.target_dir / ".attr3-footage-stage-not-this-tools-own-grammar-part0.partial"
        lookalike.write_bytes(b"a lookalike name this tool's own generator could never produce")
        self._age(lookalike, 4000)
        run = self.run_job(job)
        self.assertTrue(lookalike.is_file(), "a name not matching this tool's exact job-id grammar must never be swept")
        self.assertEqual(lookalike.read_bytes(), b"a lookalike name this tool's own generator could never produce")
        self.assertIn("PART=0 STATUS=STAGED_NOT_FOUND", run.stdout, run.stdout + run.stderr)

    def test_a_junction_in_the_target_directory_chain_is_never_enumerated_or_swept(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 6 (sol blocker, astra major: sweep order). The round 5 sweep
        # enumerated and deleted entries in the resolver's target directory before ever proving
        # that directory's own chain was link-free -- a junction planted there could redirect the
        # sweep's enumeration and deletion outside the intended directory. The sweep must now
        # refuse to enumerate at all when the target directory's own chain contains a link.
        job = self.build(stale_after_sec=1800)
        elsewhere = self.tmp / "elsewhere-sweep-target"
        elsewhere.mkdir()
        stale_partial_elsewhere = elsewhere / (
            ".attr3-footage-stage-attr3-footage-stage-FIX-STAGE-SWEEP-0001-deadbeef0000-cccccccccc-part0.partial"
        )
        stale_partial_elsewhere.write_bytes(b"bytes behind the junction, must never be touched")
        self._age(stale_partial_elsewhere, 4000)
        self.target_dir.parent.mkdir(parents=True, exist_ok=True)
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path '{self.target_dir}' -Target '{elsewhere}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not self.target_dir.exists():
            self.skipTest(f"cannot create a junction here: {made.stderr}")
        run = self.run_job(job)
        self.assertTrue(stale_partial_elsewhere.exists(), "a stale partial reachable only through a junction must never be swept")
        self.assertEqual(stale_partial_elsewhere.read_bytes(), b"bytes behind the junction, must never be touched")


@unittest.skipIf(PWSH is None, "pwsh is not on PATH")
@unittest.skipUnless(os.name == "nt", "the emitted job and agent target a Windows host")
class Attr3FootageStageCliEndToEndTests(unittest.TestCase):
    """ATTR3-FOOTAGE-STAGE-1 round 5 (hub-reproduced blocker): drives the REAL, tracked
    attr3-footage-stage.ps1 entry point end to end -- not the underlying functions directly --
    because the blocker this round closes lives in the CLI's OWN orchestration code (the
    presence-preflight array handling between um-run and the transfer/verify steps, at this
    script's own line ~183 before the round-5 fix), which no existing test exercised: every other
    test in this file calls Attr3FootagePresenceJob.psm1/Attr3FootageStageJob.psm1/AttrCudaOwner-
    Footage.psm1 functions directly, the exact split whose coverage gap let this blocker ship.

    THE ONE DELIBERATE SUBSTITUTION, twice over, both narrowly scoped and both already this
    repository's own sanctioned seams -- never a fake of the code path actually under test.

    (1) $AgentShare/$AgentRootOnHost are FIXED CONSTANTS baked into the tracked script's own text
    (round 4, sol BLOCKER 1) specifically so no caller -- test or production -- can redirect them.
    Testing the literal value (\\bachelor\\mlv-agent) would mean either touching real production
    infrastructure or fabricating an SMB server; neither is this suite's to do. A byte-for-byte
    copy of the tracked script has ONLY those two constant lines rewritten to point at a
    throwaway local directory -- verified by exact-single-occurrence substring replacement, so
    any future edit to those two lines fails this fixture loudly instead of silently no-opping.
    Every other line, including the exact orchestration code this round fixed, is untouched.

    (2) the resolver (tools/gates/resolve_consented_clip.py) always resolves against a real git
    ref and the hook's real frozen consent table, with no CLI-level seam for a test id -- so the
    copied CLI's own tools/gates/resolve_consented_clip.py is a thin wrapper that imports the
    REAL, tracked resolver module (unmodified, from its real path, never copied) and monkeypatches
    only `resolve()` -- the exact seam test_resolve_consented_clip.py already uses via its own
    `spec_bytes=`/`table=` test parameters -- never a fake resolver CLI contract, never fabricated
    consent data, never real footage.

    Everything else -- all four Import-Module'd tools/profiling/bachelor/*.psm1 files,
    tools/profiling/um-run.ps1 and tools/profiling/UmRunDrop.psm1 -- is copied byte-for-byte from
    the tracked checkout, unmodified. The agent is the REAL tools/profiling/ultra-magnus-agent.ps1,
    run locally against a throwaway share -- the same technique EndToEndTransferIdempotenceTests
    above already uses, just now submitting through the real CLI's own two um-run call sites
    instead of a test driving the underlying functions.
    """

    AGENT_SHARE_CONST = "\\\\bachelor\\mlv-agent"
    AGENT_ROOT_CONST = "C:\\mlvtmp\\mlv-agent"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3clie2e-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))

        fixture_bachelor = self.tmp / "fixture-repo" / "tools" / "profiling" / "bachelor"
        fixture_bachelor.mkdir(parents=True)
        fixture_gates = self.tmp / "fixture-repo" / "tools" / "gates"
        fixture_gates.mkdir(parents=True)
        fixture_profiling = self.tmp / "fixture-repo" / "tools" / "profiling"

        for name in ("Attr3FootagePresenceJob.psm1", "Attr3FootageStageJob.psm1", "AttrCudaArtifacts.psm1", "AttrCudaOwnerFootage.psm1"):
            shutil.copy2(ROOT / "tools" / "profiling" / "bachelor" / name, fixture_bachelor / name)
        shutil.copy2(UM_RUN, fixture_profiling / "um-run.ps1")
        shutil.copy2(ROOT / "tools" / "profiling" / "UmRunDrop.psm1", fixture_profiling / "UmRunDrop.psm1")

        self.share = self.tmp / "share"
        self.share.mkdir()
        self.agent_root_on_host = self.tmp / "agent-root-on-host"
        self.agent_root_on_host.mkdir()

        cli_text = GENERATOR.read_text(encoding="utf-8")
        old_share_line = "$AgentShare = '%s'" % self.AGENT_SHARE_CONST
        old_root_line = "$AgentRootOnHost = '%s'" % self.AGENT_ROOT_CONST
        self.assertEqual(cli_text.count(old_share_line), 1, "attr3-footage-stage.ps1's AgentShare constant line changed shape")
        self.assertEqual(cli_text.count(old_root_line), 1, "attr3-footage-stage.ps1's AgentRootOnHost constant line changed shape")
        cli_text = cli_text.replace(old_share_line, "$AgentShare = '%s'" % str(self.share), 1)
        cli_text = cli_text.replace(old_root_line, "$AgentRootOnHost = '%s'" % str(self.agent_root_on_host), 1)
        self.cli_path = fixture_bachelor / "attr3-footage-stage.ps1"
        self.cli_path.write_text(cli_text, encoding="utf-8")

        self.fixture_json = fixture_gates / "resolve_consented_clip.fixture.json"
        self.fixture_json.write_text("{}", encoding="utf-8")
        real_resolver = str(ROOT / "tools" / "gates" / "resolve_consented_clip.py")
        wrapper = (
            "import sys, json, importlib.util\n"
            f"REAL_RESOLVER = {real_resolver!r}\n"
            f"FIXTURE_JSON = {str(self.fixture_json)!r}\n"
            "spec = importlib.util.spec_from_file_location('attr3_e2e_real_resolver', REAL_RESOLVER)\n"
            "rcc = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(rcc)\n"
            "def _fake_resolve(clip_id, repo_root, ref=rcc.DEFAULT_REF, spec_bytes=None, table=None):\n"
            "    with open(FIXTURE_JSON, 'r', encoding='utf-8') as fh:\n"
            "        fixtures = json.load(fh)\n"
            "    if clip_id not in fixtures:\n"
            "        raise rcc.UnknownClipError(clip_id)\n"
            "    return [rcc.ResolvedPart(index=p['index'], path=p['path'], length=p['length'], sha256=p['sha256'], status=rcc.PASS) for p in fixtures[clip_id]]\n"
            "rcc.resolve = _fake_resolve\n"
            "sys.exit(rcc.main(sys.argv[1:]))\n"
        )
        (fixture_gates / "resolve_consented_clip.py").write_text(wrapper, encoding="utf-8")

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

    def _stop_agent(self) -> None:
        try:
            self.agent_proc.terminate()
            self.agent_proc.wait(timeout=10)
        except Exception:
            try:
                self.agent_proc.kill()
            except Exception:
                pass

    def set_fixture_parts(self, clip_id: str, parts: list) -> None:
        existing = json.loads(self.fixture_json.read_text(encoding="utf-8"))
        existing[clip_id] = parts
        self.fixture_json.write_text(json.dumps(existing), encoding="utf-8")

    def run_cli(self, clip_id: str, timeout_sec: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.cli_path),
             "-ClipId", clip_id, "-TimeoutSec", str(timeout_sec)],
            capture_output=True, text=True,
        )

    def _no_console_leak(self, combined: str) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 5 (sol/astra: output channels). Every scenario below submits
        # at least the presence preflight through um-run.ps1 -- its own (and UmRunDrop.psm1's)
        # Write-Host progress lines, including "submitted <id> -> <path>" naming a real share
        # path, must never reach this process's own console once `6>$null` is applied at the call
        # site.
        self.assertNotIn("submitted ", combined)
        self.assertNotIn("side-file placed", combined)
        self.assertNotIn(str(self.share), combined)

    # ---- the hub-reproduced blocker: 0, 1 and N PASS indexes through the REAL entry point -------

    def test_no_parts_present_reaches_a_clean_typed_refusal_not_a_crash(self) -> None:
        clip_id = "FIX-E2E-CLI-NONE-0001"
        target_dir = self.tmp / "spec" / clip_id
        content = (b"e2e cli none part zero " * 53, b"e2e cli none part one " * 5)
        parts = [
            {"index": i, "path": str(target_dir / f"part{i}.raw"), "length": len(c), "sha256": hashlib.sha256(c).hexdigest()}
            for i, c in enumerate(content)
        ]
        self.set_fixture_parts(clip_id, parts)
        # Neither part exists anywhere -- presence preflight must honestly report 0 PASS indexes
        # for a 2-part clip, the exact "zero" repro the hub found crashing every normal run.
        proc = self.run_cli(clip_id)
        combined = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("RESOLVED clip=%s parts=2" % clip_id, combined)
        self.assertNotIn("PRESENCE PREFLIGHT=INCONCLUSIVE", combined, "the real presence preflight must have run and returned, not fallen back")
        self.assertIn("ATTR3_FOOTAGE_STAGE_SOURCE_VERIFY_FAILED", combined)
        # The regression proof: no raw PowerShell type-conversion exception, anywhere.
        self.assertNotIn("Cannot convert", combined)
        self.assertNotIn("System.Object[]", combined)
        self.assertNotIn("TRANSFER PART", combined)
        self._no_console_leak(combined)

    def test_one_of_two_present_filters_needswork_via_the_real_presence_job(self) -> None:
        clip_id = "FIX-E2E-CLI-ONE-0001"
        target_dir = self.tmp / "spec" / clip_id
        target_dir.mkdir(parents=True)
        content = (b"e2e cli one part zero " * 53, b"e2e cli one part one " * 5)
        targets = [target_dir / "part0.raw", target_dir / "part1.raw"]
        parts = [
            {"index": i, "path": str(t), "length": len(c), "sha256": hashlib.sha256(c).hexdigest()}
            for i, (t, c) in enumerate(zip(targets, content))
        ]
        self.set_fixture_parts(clip_id, parts)
        # Part 0 already sits, byte-exact, at its resolver-designated spec path -- presence must
        # report exactly ONE PASS index (the "one of two" repro) and exclude it from needsWork.
        targets[0].write_bytes(content[0])
        proc = self.run_cli(clip_id)
        combined = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("PRESENCE PREFLIGHT=INCONCLUSIVE", combined)
        # Part 0 was correctly excluded from needsWork: never re-verified as a source part.
        self.assertNotIn("SOURCE PART=0", combined)
        self.assertIn("SOURCE PART=1 STATUS=", combined)
        self.assertIn("ATTR3_FOOTAGE_STAGE_SOURCE_VERIFY_FAILED", combined)
        self.assertIn("part 1", combined)
        self.assertNotIn("Cannot convert", combined)
        self.assertNotIn("System.Object[]", combined)
        self._no_console_leak(combined)

    def test_all_present_reports_footage_staged_without_transferring(self) -> None:
        clip_id = "FIX-E2E-CLI-ALL-0001"
        target_dir = self.tmp / "spec" / clip_id
        target_dir.mkdir(parents=True)
        content = (b"e2e cli all part zero " * 53, b"e2e cli all part one " * 5)
        targets = [target_dir / "part0.raw", target_dir / "part1.raw"]
        parts = [
            {"index": i, "path": str(t), "length": len(c), "sha256": hashlib.sha256(c).hexdigest()}
            for i, (t, c) in enumerate(zip(targets, content))
        ]
        self.set_fixture_parts(clip_id, parts)
        for t, c in zip(targets, content):
            t.write_bytes(c)
        # Both parts already correct at their spec paths -- presence must report 2 PASS indexes
        # for a 2-part clip (the "many" repro) and the run must exit clean without transferring.
        proc = self.run_cli(clip_id)
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        self.assertIn("RESULT=FOOTAGE_STAGED", combined)
        self.assertIn("ALREADY_PRESENT=true", combined)
        self.assertNotIn("SOURCE PART", combined)
        self.assertNotIn("TRANSFER PART", combined)
        self.assertNotIn("Cannot convert", combined)
        self.assertNotIn("System.Object[]", combined)
        self._no_console_leak(combined)
        for t, c in zip(targets, content):
            self.assertEqual(t.read_bytes(), c)

    # ---- round 5: ClipId validation failure prints a fixed token, never the raw value -----------

    def test_an_invalid_clip_id_shaped_like_a_path_never_reaches_output(self) -> None:
        hostile = r"C:\%s\real-owner-footage.raw" % TOKEN
        proc = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.cli_path), "-ClipId", hostile],
            capture_output=True, text=True,
        )
        combined = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_FOOTAGE_STAGE_CLIP_ID_INVALID", combined)
        self.assertNotIn(hostile, combined)
        self.assertNotIn(TOKEN, combined)

    # ---- round 6: TimeoutSec is [string]-bound and body-validated, never a raw binder echo ------

    def test_a_zero_or_negative_timeout_sec_is_rejected_with_a_fixed_token(self) -> None:
        for bad_value in ("0", "-100"):
            proc = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.cli_path),
                 "-ClipId", "NOT-A-REAL-CLIP-ID-ATTR3-STAGE-TIMEOUT", "-TimeoutSec", bad_value],
                capture_output=True, text=True,
            )
            combined = proc.stdout + proc.stderr
            self.assertNotEqual(proc.returncode, 0, combined)
            self.assertIn("ATTR3_FOOTAGE_STAGE_TIMEOUT_SEC_INVALID", combined)
            self.assertNotIn("RESULT=FOOTAGE_STAGED", combined)

    def test_a_path_shaped_timeout_sec_value_never_reaches_output(self) -> None:
        # ATTR3-FOOTAGE-STAGE-1 round 6 (sol major, astra major: binding-time output). -TimeoutSec
        # used to be [int]-typed, so PowerShell's OWN parameter binder attempted the conversion
        # BEFORE this script's body ever ran, and its auto-generated failure message echoed the
        # offending value verbatim -- exactly the leak -ClipId's own body-level validation already
        # existed to prevent for that parameter. -TimeoutSec is now [string]-typed and validated in
        # the body; a path-shaped sentinel value must reach only the fixed token, never itself,
        # never any raw PowerShell type-conversion exception text.
        hostile = r"C:\%s\real-owner-footage.raw" % TOKEN
        proc = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.cli_path),
             "-ClipId", "NOT-A-REAL-CLIP-ID-ATTR3-STAGE-TIMEOUT", "-TimeoutSec", hostile],
            capture_output=True, text=True,
        )
        combined = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_FOOTAGE_STAGE_TIMEOUT_SEC_INVALID", combined)
        self.assertNotIn(hostile, combined)
        self.assertNotIn(TOKEN, combined)
        self.assertNotIn("Cannot convert", combined)
        self.assertNotIn("ParameterBindingArgumentTransformationException", combined)

    # ---- round 5: source link check (astra major) -------------------------------------------

    def test_a_junction_above_a_source_part_is_refused_before_verification(self) -> None:
        clip_id = "FIX-E2E-CLI-LINK-0001"
        real_container = self.tmp / "link-real-container"
        real_container.mkdir()
        content = b"e2e cli link-check content " * 31
        real_file = real_container / "part0.raw"
        real_file.write_bytes(content)
        linked_container = self.tmp / "link-junction-container"
        made = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path '{linked_container}' -Target '{real_container}' | Out-Null"],
            capture_output=True, text=True,
        )
        if made.returncode != 0 or not linked_container.exists():
            self.skipTest(f"cannot create a junction here: {made.stderr}")
        linked_path = linked_container / "part0.raw"
        parts = [{"index": 0, "path": str(linked_path), "length": len(content), "sha256": hashlib.sha256(content).hexdigest()}]
        self.set_fixture_parts(clip_id, parts)
        proc = self.run_cli(clip_id)
        combined = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ATTR3_FOOTAGE_STAGE_SOURCE_PATH_UNSAFE", combined)
        self.assertIn("SOURCE PART=0 STATUS=SOURCE_PATH_UNSAFE", combined)
        self.assertNotIn("TRANSFER PART", combined)
        self.assertEqual(real_file.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
