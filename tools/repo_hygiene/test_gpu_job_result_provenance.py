from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.repo_hygiene.gpu_job_result_provenance import (
    DEFAULT_LLRAWPROC_PATH,
    ProvenanceValidationError,
    SubprocessGitWitness,
    validate,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "gpu_job_result_provenance"
BOUND_FIXTURE = FIXTURES / "bound.json"
UNBOUND_FIXTURE = FIXTURES / "unbound.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class GpuJobResultProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.git = SubprocessGitWitness(ROOT)

    def test_bound_fixture_validates(self) -> None:
        manifest = _read_json(BOUND_FIXTURE)
        validate(manifest, git=self.git)

    def test_unbound_fixture_is_rejected(self) -> None:
        manifest = _read_json(UNBOUND_FIXTURE)
        with self.assertRaises(ProvenanceValidationError) as ctx:
            validate(manifest, git=self.git)
        self.assertIn("llrawprocBlobId", str(ctx.exception))

    def test_rejects_range_head_from_unknown_commit(self) -> None:
        manifest = _read_json(BOUND_FIXTURE)
        manifest["rangeHeadSha"] = "f" * 40
        with self.assertRaises(ProvenanceValidationError) as ctx:
            validate(manifest, git=self.git)
        self.assertIn("rangeHeadSha", str(ctx.exception))

    def test_rejects_malformed_range_head(self) -> None:
        manifest = _read_json(BOUND_FIXTURE)
        manifest["rangeHeadSha"] = "not-a-sha"
        with self.assertRaises(ProvenanceValidationError):
            validate(manifest, git=self.git)

    def test_rejects_missing_llrawproc_blob_id(self) -> None:
        manifest = _read_json(BOUND_FIXTURE)
        del manifest["llrawprocBlobId"]
        with self.assertRaises(ProvenanceValidationError):
            validate(manifest, git=self.git)

    def test_rejects_malformed_dll_sha256(self) -> None:
        manifest = _read_json(BOUND_FIXTURE)
        manifest["dllSha256"] = "not-a-hex-digest"
        with self.assertRaises(ProvenanceValidationError) as ctx:
            validate(manifest, git=self.git)
        self.assertIn("dllSha256", str(ctx.exception))

    def test_rejects_null_pending_symbol_presence(self) -> None:
        # An inconclusive export check (dumpbin missing, DLL missing) must still
        # be recorded as an explicit boolean - never silently dropped to null.
        manifest = _read_json(BOUND_FIXTURE)
        manifest["pendingSymbolPresence"] = None
        with self.assertRaises(ProvenanceValidationError) as ctx:
            validate(manifest, git=self.git)
        self.assertIn("pendingSymbolPresence", str(ctx.exception))

    def test_rejects_non_boolean_pending_symbol_presence(self) -> None:
        manifest = _read_json(BOUND_FIXTURE)
        manifest["pendingSymbolPresence"] = "true"
        with self.assertRaises(ProvenanceValidationError):
            validate(manifest, git=self.git)

    def test_rejects_non_object_manifest(self) -> None:
        with self.assertRaises(ProvenanceValidationError):
            validate(["not", "an", "object"], git=self.git)

    def test_llrawproc_path_is_the_expected_repo_relative_source(self) -> None:
        self.assertEqual(DEFAULT_LLRAWPROC_PATH, "src/mlv/llrawproc/llrawproc.c")
        self.assertTrue((ROOT / DEFAULT_LLRAWPROC_PATH).is_file())


if __name__ == "__main__":
    unittest.main()
