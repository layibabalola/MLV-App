from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.repo_hygiene.pipeline_golden_provenance import (
    DEFAULT_MANIFEST,
    ProvenanceValidationError,
    SubprocessGitWitness,
    validate,
)


ROOT = Path(__file__).resolve().parents[2]


class OverlayGitWitness:
    """Add test-only tracked blobs while delegating real read-only history checks."""

    def __init__(self, delegate: SubprocessGitWitness, blobs: dict[tuple[str, str], bytes]) -> None:
        self.delegate = delegate
        self.blobs = blobs

    def commit_exists(self, commit: str) -> bool:
        return self.delegate.commit_exists(commit)

    def blob(self, commit: str, relative_path: str) -> bytes:
        key = (commit, relative_path)
        return self.blobs[key] if key in self.blobs else self.delegate.blob(commit, relative_path)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return self.delegate.is_ancestor(ancestor, descendant)

    def parents(self, commit: str) -> list[str]:
        return self.delegate.parents(commit)

    def paths_equal(self, left: str, right: str, paths: list[str]) -> bool:
        return self.delegate.paths_equal(left, right, paths)

    def last_change(self, commit: str, relative_path: str) -> str:
        return self.delegate.last_change(commit, relative_path)


class PipelineGoldenProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        self.git_witness = SubprocessGitWitness(ROOT)
        manifest = self._read_json(ROOT / DEFAULT_MANIFEST)
        paths = {
            manifest["artifact"]["path"],
            manifest["cross_golden"]["phase3"]["path"],
            manifest["generator"]["toolchain"]["evidence"]["path"],
        }
        paths.update(record["path"] for record in manifest["generator"]["sources"])
        paths.update(record["path"] for record in manifest["inputs"])
        approval_witness = manifest.get("review", {}).get("approval_witness")
        if isinstance(approval_witness, dict):
            paths.add(approval_witness["path"])
        for relative in paths:
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        manifest_destination = self.repo / DEFAULT_MANIFEST
        manifest_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / DEFAULT_MANIFEST, manifest_destination)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _sha256(path: Path, mode: str) -> str:
        content = path.read_bytes()
        if mode == "lf_normalized_sha256":
            content = content.replace(b"\r\n", b"\n")
        return hashlib.sha256(content).hexdigest()

    def _manifest(self) -> dict:
        return self._read_json(self.repo / DEFAULT_MANIFEST)

    def _write_manifest(self, manifest: dict) -> None:
        self._write_json(self.repo / DEFAULT_MANIFEST, manifest)

    def _validate(self) -> None:
        validate(self.repo, git_witness=self.git_witness)

    def _add_tracked_witness(self, relative_path: str, witness: dict) -> dict:
        content = (json.dumps(witness, indent=2) + "\n").encode("utf-8")
        destination = self.repo / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        source_state = self._manifest()["generator"]["source_state_commit"]
        self.git_witness = OverlayGitWitness(
            self.git_witness,
            {(source_state, relative_path): content},
        )
        return {
            "path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "hash_mode": "lf_normalized_sha256",
        }

    def test_committed_manifest_is_valid(self) -> None:
        self._validate()

    def test_ci_uses_full_history_for_ancestral_hosted_witness(self) -> None:
        manifest = self._manifest()
        external = manifest["artifact"]["external_observations"]
        self.assertEqual(1, len(external))
        witness = external[0]["head_sha"]
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertNotIn("Fetch historical provenance witness", workflow)
        self.assertTrue(self.git_witness.is_ancestor(witness, manifest["generator"]["source_state_commit"]))

    def test_pipeline_can_transition_to_ratified_with_tracked_approval(self) -> None:
        manifest = self._manifest()
        reviewed_range = (
            "3e02eaea7a39b1a86b22f61e43017af3cd4f14f6.."
            "cbae456144e2dc9092d78c87853de37d1bbe937a"
        )
        artifact = manifest["artifact"]
        witness = {
            "schema_version": 1,
            "kind": "tracked_whole_artifact_approval",
            "verdict": "APPROVE",
            "artifact_path": artifact["path"],
            "artifact_sha256": artifact["sha256"],
            "reviewed_range": reviewed_range,
        }
        manifest["review"] = {
            "status": "ratified",
            "scope": {
                "artifact": "complete",
                "keys": sorted(self._read_json(self.repo / artifact["path"])),
            },
            "reason": "test-only future transition",
            "approval_witness": self._add_tracked_witness(
                "tests/fixtures/golden/test-only-pipeline-approval.json", witness
            ),
        }
        manifest["unknowns"]["whole_artifact_review_range"] = reviewed_range
        self._write_manifest(manifest)
        self._validate()

    def test_phase3_can_transition_to_ratified_with_tracked_approval(self) -> None:
        manifest = self._manifest()
        phase3 = manifest["cross_golden"]["phase3"]
        review = phase3["review"]
        witness = {
            "schema_version": 1,
            "kind": "tracked_phase3_approval",
            "verdict": "APPROVE",
            "artifact_path": phase3["path"],
            "artifact_sha256": phase3["sha256"],
            "reviewed_range": review["reviewed_range"],
        }
        review["status"] = "ratified"
        review.pop("external_review_witness")
        review["approval_witness"] = self._add_tracked_witness(
            "tests/fixtures/golden/test-only-phase3-approval.json", witness
        )
        self._write_manifest(manifest)
        self._validate()

    def test_two_locally_ratified_artifacts_must_agree_on_full16_hashes(self) -> None:
        manifest = self._manifest()
        artifact = manifest["artifact"]
        phase3 = manifest["cross_golden"]["phase3"]
        reviewed_range = phase3["review"]["reviewed_range"]
        pipeline_witness = {
            "schema_version": 1,
            "kind": "tracked_whole_artifact_approval",
            "verdict": "APPROVE",
            "artifact_path": artifact["path"],
            "artifact_sha256": artifact["sha256"],
            "reviewed_range": reviewed_range,
        }
        manifest["review"] = {
            "status": "ratified",
            "scope": {
                "artifact": "complete",
                "keys": sorted(self._read_json(self.repo / artifact["path"])),
            },
            "reason": "test-only future transition",
            "approval_witness": self._add_tracked_witness(
                "tests/fixtures/golden/test-only-pipeline-approval.json", pipeline_witness
            ),
        }
        manifest["unknowns"]["whole_artifact_review_range"] = reviewed_range
        phase3_path = self.repo / phase3["path"]
        phase3_content = self._read_json(phase3_path)
        phase3_content["clips"][0]["frames"][0]["sha256"] = "0" * 64
        self._write_json(phase3_path, phase3_content)
        phase3["sha256"] = self._sha256(phase3_path, phase3["hash_mode"])
        manifest["cross_golden"]["pairs"][0]["status"] = "known_mismatch"
        source_state = manifest["generator"]["source_state_commit"]
        reviewed_head = reviewed_range.split("..", 1)[1]
        phase3_bytes = phase3_path.read_bytes()
        self.git_witness = OverlayGitWitness(
            self.git_witness,
            {
                (source_state, phase3["path"]): phase3_bytes,
                (reviewed_head, phase3["path"]): phase3_bytes,
            },
        )
        phase3_witness = {
            "schema_version": 1,
            "kind": "tracked_phase3_approval",
            "verdict": "APPROVE",
            "artifact_path": phase3["path"],
            "artifact_sha256": phase3["sha256"],
            "reviewed_range": reviewed_range,
        }
        phase3["review"]["status"] = "ratified"
        phase3["review"].pop("external_review_witness")
        phase3["review"]["approval_witness"] = self._add_tracked_witness(
            "tests/fixtures/golden/test-only-phase3-approval.json", phase3_witness
        )
        self._write_manifest(manifest)
        with self.assertRaisesRegex(
            ProvenanceValidationError, "ratified pipeline and Phase3 full16 hashes must agree"
        ):
            self._validate()

    def test_artifact_hash_drift_fails_closed(self) -> None:
        artifact = self.repo / self._manifest()["artifact"]["path"]
        artifact.write_bytes(artifact.read_bytes() + b"\n")
        with self.assertRaisesRegex(ProvenanceValidationError, "pipeline artifact sha256 mismatch"):
            self._validate()

    def test_non_sha_frame_value_fails_even_with_recomputed_artifact_hash(self) -> None:
        manifest = self._manifest()
        artifact_record = manifest["artifact"]
        artifact_path = self.repo / artifact_record["path"]
        artifact = self._read_json(artifact_path)
        artifact["tiny_dual_iso.debayer.amaze.cpu.frame0"] = "fabricated-not-a-sha"
        self._write_json(artifact_path, artifact)
        artifact_record["sha256"] = self._sha256(artifact_path, artifact_record["hash_mode"])
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "must be lowercase SHA-256"):
            self._validate()

    def test_missing_cross_golden_pair_is_partial_inconsistency(self) -> None:
        manifest = self._manifest()
        manifest["cross_golden"]["pairs"].pop()
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "must cover both and only pipeline full16"):
            self._validate()

    def test_partial_alignment_must_update_declared_pair_status(self) -> None:
        manifest = self._manifest()
        pipeline = self._read_json(self.repo / manifest["artifact"]["path"])
        phase3_record = manifest["cross_golden"]["phase3"]
        phase3_path = self.repo / phase3_record["path"]
        phase3 = self._read_json(phase3_path)
        phase3["clips"][0]["frames"][0]["sha256"] = "0" * 64
        self._write_json(phase3_path, phase3)
        phase3_record["sha256"] = self._sha256(phase3_path, phase3_record["hash_mode"])
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "pair status drift"):
            self._validate()

    def test_phase3_cannot_claim_ratified_without_tracked_approval_witness(self) -> None:
        manifest = self._manifest()
        manifest["cross_golden"]["phase3"]["review"]["status"] = "ratified"
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "must not retain an external-only review witness"):
            self._validate()

    def test_generator_source_hash_drift_fails_closed(self) -> None:
        manifest = self._manifest()
        source = self.repo / manifest["generator"]["sources"][0]["path"]
        source.write_bytes(source.read_bytes() + b"\n")
        with self.assertRaisesRegex(ProvenanceValidationError, r"generator source\[0\] sha256 mismatch"):
            self._validate()

    def test_source_state_commit_must_exist(self) -> None:
        manifest = self._manifest()
        manifest["generator"]["source_state_commit"] = "0" * 40
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "source_state_commit commit is absent"):
            self._validate()

    def test_dependency_snapshot_cannot_claim_complete_closure(self) -> None:
        manifest = self._manifest()
        manifest["generator"]["dependency_snapshot"]["complete"] = True
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "must not claim complete dependency closure"):
            self._validate()

    def test_fictional_producer_test_is_rejected(self) -> None:
        manifest = self._manifest()
        manifest["generator"]["producer_tests"][0] = "BackendParametricDebayerShell.FictionalGoldenProducer"
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "producer test is absent"):
            self._validate()

    def test_fictional_toolchain_version_is_rejected(self) -> None:
        manifest = self._manifest()
        manifest["generator"]["toolchain"]["qt"] = "9.9.9"
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "Qt version is not evidenced"):
            self._validate()

    def test_fabricated_introduced_by_existing_commit_is_rejected(self) -> None:
        manifest = self._manifest()
        manifest["artifact"]["introduced_by"] = "69c8c428fb18bba5a8b7eb67092daaaf0e2f47e2"
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "does not contain the declared artifact blob"):
            self._validate()

    def test_hosted_evidence_requires_full_sha(self) -> None:
        manifest = self._manifest()
        manifest["artifact"]["external_observations"][0]["head_sha"] = "91774963"
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "must be a lowercase full commit hash"):
            self._validate()

    def test_hosted_evidence_commit_must_exist(self) -> None:
        manifest = self._manifest()
        manifest["artifact"]["external_observations"][0]["head_sha"] = "0" * 40
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "head_sha commit is absent"):
            self._validate()

    def test_hosted_evidence_url_must_match_run_id(self) -> None:
        manifest = self._manifest()
        manifest["artifact"]["external_observations"][0]["url"] = (
            "https://github.com/layibabalola/MLV-App/actions/runs/999"
        )
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "URL must exactly match its hosted run_id"):
            self._validate()

    def test_hosted_evidence_artifact_blob_is_verified(self) -> None:
        manifest = self._manifest()
        manifest["artifact"]["external_observations"][0]["head_sha"] = (
            "73adf6eed3dd7443123d3801ab36c20f75526b4b"
        )
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "artifact blob does not match"):
            self._validate()

    def test_hosted_observation_cannot_claim_verified_without_local_run_witness(self) -> None:
        manifest = self._manifest()
        manifest["artifact"]["external_observations"][0]["verification"] = "verified"
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "externally_asserted_unvalidated"):
            self._validate()

    def test_legacy_validation_evidence_claim_is_rejected(self) -> None:
        manifest = self._manifest()
        manifest["artifact"]["validation_evidence"] = manifest["artifact"].pop("external_observations")
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "require a locally bound hosted-run witness"):
            self._validate()

    def test_unvalidated_external_run_observation_is_not_treated_as_proof(self) -> None:
        manifest = self._manifest()
        observation = manifest["artifact"]["external_observations"][0]
        observation["run_id"] = 1
        observation["url"] = "https://github.com/layibabalola/MLV-App/actions/runs/1"
        self._write_manifest(manifest)
        self._validate()

    def test_phase3_external_review_cannot_claim_verified_without_tracked_witness(self) -> None:
        manifest = self._manifest()
        witness = manifest["cross_golden"]["phase3"]["review"]["external_review_witness"]
        witness["verification"] = "verified"
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "externally_asserted_unvalidated"):
            self._validate()

    def test_phase3_reviewed_range_base_must_be_direct_parent(self) -> None:
        manifest = self._manifest()
        review = manifest["cross_golden"]["phase3"]["review"]
        review["reviewed_range"] = (
            "15eae08623efe3bdaf05f19c7efc4cfc639f9a9d.."
            "cbae456144e2dc9092d78c87853de37d1bbe937a"
        )
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "direct child of its declared base"):
            self._validate()

    def test_phase3_landed_commit_must_be_actual_integration_merge(self) -> None:
        manifest = self._manifest()
        manifest["cross_golden"]["phase3"]["review"]["landed_commit"] = (
            "6dc5aca7426ff6ee5a871efd1819c581d2521f1b"
        )
        self._write_manifest(manifest)
        with self.assertRaisesRegex(ProvenanceValidationError, "must be the merge that integrated reviewed history"):
            self._validate()


if __name__ == "__main__":
    unittest.main()
