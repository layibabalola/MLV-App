#!/usr/bin/env python3
"""Fail-closed checks for the zero-authority MLV supervisor integration."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "docs/universal-token-control-supervisor-integration-candidate-2026-08-21.json"
FIRST = "a4bb0d7a2833e760ba1d0d6cfb53133d69f09c24"
FIRST_TREE = "fd6de9b74a0a2ed48fa751048ad5e215849bf9c8"
SECOND = "50db64d71bf62da041b9a6872c893382cd7cc79b"
SECOND_TREE = "ab58ec53414ff889eaa41a6689dde1141cc687ed"
BASE = "30889f77e2000190b94d59f80f6a03b12ce3e0d3"
ARTIFACT_PATH = ARTIFACT.relative_to(ROOT).as_posix()
TEST_PATH = Path(__file__).relative_to(ROOT).as_posix()
PREFIXES = (
    ".github/requirements/provider-control",
    ".github/workflows/provider-control-candidate.yml",
    "tools/provider_control/",
)
EXPECTED_AUTHORITY = {
    "projectDisposition", "fleetAdoption", "installation", "runtime",
    "providerExecution", "authentication", "taskOrGateMutation", "pushOrMerge",
}


class IntegrationError(ValueError):
    pass


def git(*args: str, check: bool = True) -> bytes:
    clean_env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    run = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=False,
        env=clean_env,
    )
    if check and run.returncode:
        raise IntegrationError("GIT_COMMAND_FAILED")
    return run.stdout


def commit_tuple(commit: str) -> tuple[str, list[str]]:
    lines = git("show", "-s", "--format=%T%n%P", commit).decode().splitlines()
    return lines[0], lines[1].split() if len(lines) > 1 else []


def oid(treeish: str, path: str) -> str:
    spec = f":{path}" if treeish == ":" else f"{treeish}:{path}"
    return git("rev-parse", spec).decode().strip()


def blob(treeish: str, path: str) -> bytes:
    spec = f":{path}" if treeish == ":" else f"{treeish}:{path}"
    return git("show", spec)


def source_paths() -> set[str]:
    rows = git("ls-tree", "-r", "--name-only", SECOND).decode().splitlines()
    return {path for path in rows if any(path.startswith(prefix) for prefix in PREFIXES)}


def candidate_treeish() -> str:
    return ":" if (ROOT / ".git").is_file() and git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False) else "HEAD"


def verify_document(document: dict) -> None:
    if type(document) is not dict or list(document) != [
        "schema", "status", "integration", "integratedSubjects", "preservedEvidence",
        "verifiedControls", "acceptedBoundary", "remainingUncreditedProofs", "authority",
    ]:
        raise IntegrationError("DOCUMENT_SHAPE_INVALID")
    if document["schema"] != "mlv-universal-control-supervisor-integration/v1" or document["status"] != "CANDIDATE_ZERO_AUTHORITY":
        raise IntegrationError("DOCUMENT_HEADER_INVALID")
    integration = document["integration"]
    if integration != {
        "firstParent": FIRST, "firstParentTree": FIRST_TREE,
        "secondParent": SECOND, "secondParentTree": SECOND_TREE,
        "mergeBase": BASE, "sourceProofTransferred": False,
        "sourceCodeRevalidatedOnIntegratedTree": True,
    }:
        raise IntegrationError("INTEGRATION_BINDING_INVALID")
    authority = document["authority"]
    if type(authority) is not dict or set(authority) != EXPECTED_AUTHORITY or any(type(value) is not bool or value for value in authority.values()):
        raise IntegrationError("AUTHORITY_INVALID")
    controls = document["verifiedControls"]
    if controls.get("integratedSupervisorTests") != "PASS 45/45" or controls.get("productionTickGate") != "CLOSED":
        raise IntegrationError("CONTROL_EVIDENCE_INVALID")
    for key in ("productionProviderCalls", "productionProviderProcesses", "productionTokens"):
        if type(controls.get(key)) is not int or controls[key] != 0:
            raise IntegrationError("CONTROL_EVIDENCE_INVALID")
    if controls.get("proofTransferCredited") is not False:
        raise IntegrationError("PROOF_TRANSFER_INVALID")
    boundary = document["acceptedBoundary"]
    true_keys = {
        "functionalClosedSupervisorSourceIntegrated", "universalRequestEnvelopeIntegrated",
        "providerFreeControlSuiteIntegrated",
    }
    if type(boundary) is not dict or {key for key, value in boundary.items() if value is True} != true_keys:
        raise IntegrationError("BOUNDARY_INVALID")
    if any(type(value) is not bool for value in boundary.values()):
        raise IntegrationError("BOUNDARY_TYPE_INVALID")


def verify_git_and_subjects(document: dict) -> None:
    if commit_tuple(FIRST) != (FIRST_TREE, ["2ef9690649519d91197199d401d46fe3bb6d8dbb"]):
        raise IntegrationError("FIRST_PARENT_INVALID")
    if commit_tuple(SECOND) != (SECOND_TREE, ["709bf99307271ac7b6bb0f202495723373892871"]):
        raise IntegrationError("SECOND_PARENT_INVALID")
    if git("merge-base", FIRST, SECOND).decode().strip() != BASE:
        raise IntegrationError("MERGE_BASE_INVALID")
    treeish = candidate_treeish()
    if treeish == ":":
        merge_head = git("rev-parse", "MERGE_HEAD").decode().strip()
        if git("rev-parse", "HEAD").decode().strip() != FIRST or merge_head != SECOND:
            raise IntegrationError("STAGED_PARENT_INVALID")
    else:
        if commit_tuple("HEAD")[1] != [FIRST, SECOND]:
            raise IntegrationError("COMMITTED_PARENT_INVALID")
    for path in source_paths():
        if oid(treeish, path) != oid(SECOND, path):
            raise IntegrationError("SOURCE_BLOB_DRIFT")
    rows = document["integratedSubjects"]
    if type(rows) is not list or len(rows) != 7:
        raise IntegrationError("SUBJECT_ROWS_INVALID")
    for row in rows:
        if type(row) is not dict or list(row) != ["path", "gitBlobOid", "bytes", "sha256"]:
            raise IntegrationError("SUBJECT_ROW_SHAPE_INVALID")
        data = blob(treeish, row["path"])
        if oid(treeish, row["path"]) != row["gitBlobOid"] or len(data) != row["bytes"] or hashlib.sha256(data).hexdigest().upper() != row["sha256"]:
            raise IntegrationError("SUBJECT_TUPLE_INVALID")
    for path in (
        "docs/universal-token-control-launcher-action-graph-census-candidate-2026-08-20.json",
        "docs/universal-token-control-machine-observation-candidate-2026-08-20.json",
        "docs/universal-token-control-task-definition-enumeration-candidate-2026-08-21.json",
        "docs/universal-token-control-task-definition-enumeration-review-receipt-2026-08-21.json",
    ):
        if oid(treeish, path) != oid(FIRST, path):
            raise IntegrationError("PRESERVED_EVIDENCE_DRIFT")


class SupervisorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    def test_01_document_and_git_envelope_pass(self) -> None:
        verify_document(self.document)
        verify_git_and_subjects(self.document)

    def test_02_duplicate_json_keys_refuse(self) -> None:
        raw = ARTIFACT.read_text(encoding="utf-8").replace('"schema": ', '"schema": "forged", "schema": ', 1)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw, object_pairs_hook=lambda pairs: (_ for _ in ()).throw(json.JSONDecodeError("duplicate", raw, 0)) if len({k.casefold() for k, _ in pairs}) != len(pairs) else dict(pairs))

    def test_03_native_boolean_and_integer_aliases_refuse(self) -> None:
        for mutate in (
            lambda d: d["authority"].update(runtime=0),
            lambda d: d["verifiedControls"].update(productionProviderCalls=False),
            lambda d: d["acceptedBoundary"].update(runtimeActivation=0),
        ):
            hostile = copy.deepcopy(self.document)
            mutate(hostile)
            with self.assertRaises(IntegrationError):
                verify_document(hostile)

    def test_04_authority_and_boundary_overclaims_refuse(self) -> None:
        for section, key in (
            ("authority", "installation"),
            ("authority", "providerExecution"),
            ("acceptedBoundary", "productionLauncherWired"),
            ("acceptedBoundary", "adoption"),
        ):
            hostile = copy.deepcopy(self.document)
            hostile[section][key] = True
            with self.assertRaises(IntegrationError):
                verify_document(hostile)

    def test_05_proof_transfer_refuses(self) -> None:
        hostile = copy.deepcopy(self.document)
        hostile["integration"]["sourceProofTransferred"] = True
        hostile["verifiedControls"]["proofTransferCredited"] = True
        with self.assertRaises(IntegrationError):
            verify_document(hostile)

    def test_06_source_blob_drift_refuses(self) -> None:
        original = oid
        globals()["oid"] = lambda treeish, path: "0" * 40 if treeish == candidate_treeish() and path.endswith("mlv_lane_supervisor.py") else original(treeish, path)
        try:
            with self.assertRaisesRegex(IntegrationError, "SOURCE_BLOB_DRIFT"):
                verify_git_and_subjects(self.document)
        finally:
            globals()["oid"] = original

    def test_07_subject_tuple_drift_refuses(self) -> None:
        hostile = copy.deepcopy(self.document)
        hostile["integratedSubjects"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(IntegrationError, "SUBJECT_TUPLE_INVALID"):
            verify_git_and_subjects(hostile)

    def test_08_preserved_evidence_drift_refuses(self) -> None:
        original = oid
        globals()["oid"] = lambda treeish, path: "0" * 40 if treeish == candidate_treeish() and path.startswith("docs/universal-token-control-machine") else original(treeish, path)
        try:
            with self.assertRaisesRegex(IntegrationError, "PRESERVED_EVIDENCE_DRIFT"):
                verify_git_and_subjects(self.document)
        finally:
            globals()["oid"] = original


if __name__ == "__main__":
    unittest.main()
