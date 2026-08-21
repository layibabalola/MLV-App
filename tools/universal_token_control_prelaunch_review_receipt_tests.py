#!/usr/bin/env python3
"""Verify the exact R40 Sonnet prelaunch review without granting authority."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "docs/universal-token-control-prelaunch-review-receipt-2026-08-21.json"
REVIEWED = "65bcc9918a0c2bf844607163242caa43577ea713"
REVIEWED_TREE = "fd2860d35095e86bc161c901036b9bb4d903c6e9"
REVIEWED_PARENT = "6974cfd868cea2fb60f688d6bacaf087075f55f7"
CLOSURE = "ad8d7f5f29882774c8eb5ce2df7c3c8689e73158"
CLOSURE_TREE = "b8b989b5abba708d58cd2287a7c965994e206e22"
ROUTE = "fleet-r64-mlv-r40-prelaunch-sonnet"
LANE = "sonnet"
SESSION = "223c9b6b-1bdb-4e35-8d3a-99884f371150"
AUTHORIZATION = "ee33f602-274a-4212-8b05-b4b618c08a0e"
PACKET_SHA = "F81EBD20A0CEEB058F364CFD0A2E2BB8646124BB15E1944D6ACC454C37B01594"
OUTPUT_SHA = "0B72B7FC4B1FDE6CD993BBC8616358180DE49765B90144AA1BA5C5764CD55F85"


class ReceiptError(ValueError):
    pass


def pairs(items):
    result, folded = {}, set()
    for key, value in items:
        if type(key) is not str or key.casefold() in folded:
            raise ReceiptError("DUPLICATE_OR_CASE_COLLIDING_KEY")
        folded.add(key.casefold())
        result[key] = value
    return result


def load(raw: bytes):
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("JSON_INVALID") from exc
    if type(value) is not dict:
        raise ReceiptError("JSON_ROOT_INVALID")
    return value


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def git(*args: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    run = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, env=env, check=False)
    if run.returncode:
        raise ReceiptError("GIT_COMMAND_FAILED")
    return run.stdout


def exact_file(row: dict, *, file_keys=frozenset({"path", "bytes", "sha256"})) -> bytes:
    if type(row) is not dict or set(row) != file_keys:
        raise ReceiptError("FILE_ROW_INVALID")
    path = Path(row["path"])
    if not path.is_absolute():
        path = ROOT / path
    raw = path.read_bytes()
    if len(raw) != row["bytes"] or sha(raw) != row["sha256"]:
        raise ReceiptError("FILE_TUPLE_INVALID")
    return raw


def git_subject(commit: str, row: dict) -> None:
    if type(row) is not dict or set(row) != {"path", "git_blob", "bytes", "sha256"}:
        raise ReceiptError("SUBJECT_ROW_INVALID")
    raw = git("cat-file", "blob", f"{commit}:{row['path']}")
    oid = git("rev-parse", f"{commit}:{row['path']}").decode().strip()
    if oid != row["git_blob"] or len(raw) != row["bytes"] or sha(raw) != row["sha256"]:
        raise ReceiptError("SUBJECT_TUPLE_INVALID")


def verify(document: dict) -> None:
    expected = {
        "schema", "status", "reviewed_subject", "local_execution_evidence",
        "independent_review", "finding_disposition", "accepted_boundary",
        "disposition", "authority",
    }
    if type(document) is not dict or set(document) != expected:
        raise ReceiptError("DOCUMENT_SHAPE_INVALID")
    if (
        document["schema"] != "mlv-universal-control-prelaunch-review.v1"
        or document["status"] != "ACCEPT_CANDIDATE_ONLY_ZERO_AUTHORITY"
        or document["disposition"] != "DISTINGUISH"
    ):
        raise ReceiptError("DOCUMENT_HEADER_INVALID")

    subject = document["reviewed_subject"]
    if type(subject) is not dict or set(subject) != {
        "commit", "tree", "sole_parent", "published_branch", "source", "wrapper", "tests"
    }:
        raise ReceiptError("SUBJECT_SHAPE_INVALID")
    identity = git("show", "-s", "--format=%T%n%P", REVIEWED).decode().splitlines()
    if (
        subject["commit"] != REVIEWED
        or subject["tree"] != REVIEWED_TREE
        or subject["sole_parent"] != REVIEWED_PARENT
        or subject["published_branch"] != "codex/mlv-r39-supervisor-integration"
        or identity != [REVIEWED_TREE, REVIEWED_PARENT]
    ):
        raise ReceiptError("SUBJECT_IDENTITY_INVALID")
    for key in ("source", "wrapper", "tests"):
        git_subject(REVIEWED, subject[key])

    if document["local_execution_evidence"] != [
        {"suite": "tools.provider_control.tests.test_mlv_prelaunch_boundary",
         "result": "PASS", "tests": 7},
        {"suite": "tools.provider_control.tests.test_mlv_lane_supervisor",
         "result": "PASS", "tests": 45},
    ]:
        raise ReceiptError("LOCAL_EVIDENCE_INVALID")

    review = document["independent_review"]
    if type(review) is not dict or set(review) != {
        "route_id", "lane", "model", "effort", "role", "session_id",
        "authorization_id", "packet_snapshot", "artifact", "result_snapshot",
        "consumption_snapshot", "provider_tools", "test_execution_performed_by_reviewer",
        "verdict", "done_consumed_released_at",
    }:
        raise ReceiptError("REVIEW_SHAPE_INVALID")
    if (
        review["route_id"] != ROUTE
        or review["lane"] != LANE
        or review["model"] != "claude-sonnet-5"
        or review["effort"] != "max"
        or review["role"] != "verifier"
        or review["session_id"] != SESSION
        or review["authorization_id"] != AUTHORIZATION
        or review["provider_tools"] != ["Read", "StructuredOutput"]
        or review["test_execution_performed_by_reviewer"] is not False
        or review["verdict"] != "ACCEPT_CANDIDATE_ONLY"
    ):
        raise ReceiptError("REVIEW_IDENTITY_INVALID")

    packet = load(exact_file(review["packet_snapshot"]))
    if (
        packet.get("route_id") != ROUTE
        or packet.get("lane") != LANE
        or packet.get("role") != "verifier"
        or sha((ROOT / review["packet_snapshot"]["path"]).read_bytes()) != PACKET_SHA
    ):
        raise ReceiptError("PACKET_BINDING_INVALID")

    artifact = exact_file(review["artifact"])
    results = []
    for line in artifact.splitlines():
        try:
            event = load(line)
        except ReceiptError:
            continue
        if event.get("type") == "result":
            results.append((line, event))
    if len(results) != 1:
        raise ReceiptError("RESULT_CARDINALITY_INVALID")

    result_row = review["result_snapshot"]
    if type(result_row) is not dict or set(result_row) != {
        "path", "file_bytes", "file_sha256", "raw_line_bytes", "raw_line_sha256",
        "subtype", "is_error", "verdict",
    }:
        raise ReceiptError("RESULT_ROW_INVALID")
    result_file = ROOT / result_row["path"]
    result_file_raw = result_file.read_bytes()
    raw_result = result_file_raw.removesuffix(b"\n")
    if (
        len(result_file_raw) != result_row["file_bytes"]
        or sha(result_file_raw) != result_row["file_sha256"]
        or len(raw_result) != result_row["raw_line_bytes"]
        or sha(raw_result) != result_row["raw_line_sha256"]
        or raw_result != results[0][0]
    ):
        raise ReceiptError("RESULT_TUPLE_INVALID")
    result = load(raw_result)
    structured = result.get("structured_output")
    if (
        result.get("subtype") != result_row["subtype"]
        or result_row["subtype"] != "success"
        or result.get("is_error") is not result_row["is_error"]
        or result_row["is_error"] is not False
        or result.get("session_id") != SESSION
        or type(structured) is not dict
        or structured.get("route_id") != ROUTE
        or structured.get("lane") != LANE
        or structured.get("verdict") != result_row["verdict"]
        or result_row["verdict"] != "ACCEPT"
    ):
        raise ReceiptError("RESULT_ENVELOPE_INVALID")

    consumption_row = review["consumption_snapshot"]
    if type(consumption_row) is not dict or set(consumption_row) != {
        "path", "file_bytes", "file_sha256", "raw_receipt_bytes", "raw_receipt_sha256"
    }:
        raise ReceiptError("CONSUMPTION_ROW_INVALID")
    consumption_file = ROOT / consumption_row["path"]
    consumption_file_raw = consumption_file.read_bytes()
    raw_consumption = consumption_file_raw.removesuffix(b"\n")
    if (
        len(consumption_file_raw) != consumption_row["file_bytes"]
        or sha(consumption_file_raw) != consumption_row["file_sha256"]
        or len(raw_consumption) != consumption_row["raw_receipt_bytes"]
        or sha(raw_consumption) != consumption_row["raw_receipt_sha256"]
    ):
        raise ReceiptError("CONSUMPTION_TUPLE_INVALID")
    consumption = load(raw_consumption)
    if (
        consumption.get("route_id") != ROUTE
        or consumption.get("lane") != LANE
        or consumption.get("packet_sha256") != PACKET_SHA
        or consumption.get("output_sha256") != OUTPUT_SHA
        or consumption.get("authorization_id") != AUTHORIZATION
        or consumption.get("artifact_path") != review["artifact"]["path"]
        or consumption.get("consumed_at_utc") != review["done_consumed_released_at"]
    ):
        raise ReceiptError("CONSUMPTION_BINDING_INVALID")

    finding = document["finding_disposition"]
    if type(finding) is not dict or set(finding) != {
        "open_gate_hostile_missing", "reviewer_did_not_execute_tests",
        "host_absence_not_independently_rederived",
    }:
        raise ReceiptError("FINDING_SHAPE_INVALID")
    closure = finding["open_gate_hostile_missing"]
    closure_identity = git("show", "-s", "--format=%T%n%P", CLOSURE).decode().splitlines()
    if (
        closure != {
            "status": "CLOSED_FORWARD_ONLY",
            "commit": CLOSURE,
            "tree": CLOSURE_TREE,
            "sole_parent": REVIEWED,
            "test_blob": "f9ceb4e83b83d43263730e3a088e963857f598cf",
            "test_bytes": 5552,
            "test_sha256": "004C4CA77152B5D0A8849406D1B4DB8485CFDFCDCF18640B2E9AFE5D2C7A5F6A",
            "result": "8/8 PASS",
        }
        or closure_identity != [CLOSURE_TREE, REVIEWED]
    ):
        raise ReceiptError("FINDING_CLOSURE_INVALID")
    git_subject(CLOSURE, {
        "path": "tools/provider_control/tests/test_mlv_prelaunch_boundary.py",
        "git_blob": closure["test_blob"],
        "bytes": closure["test_bytes"],
        "sha256": closure["test_sha256"],
    })
    if (
        finding["reviewer_did_not_execute_tests"] != "RETAINED_TRUTHFULLY"
        or finding["host_absence_not_independently_rederived"] != "RETAINED_TRUTHFULLY"
    ):
        raise ReceiptError("LIMITATION_INVALID")

    boundary = document["accepted_boundary"]
    boundary_keys = {
        "stop_only_prelaunch_source", "unknown_and_authorization_shapes_fail_closed",
        "native_zero_counters_required", "published_candidate_branch",
        "production_launcher_wired", "signed_installation", "runtime_interception",
        "functionality_equivalence", "quality_equivalence",
        "project_owner_disposition", "adoption",
    }
    expected_true = {
        "stop_only_prelaunch_source", "unknown_and_authorization_shapes_fail_closed",
        "native_zero_counters_required", "published_candidate_branch",
    }
    if (
        type(boundary) is not dict
        or set(boundary) != boundary_keys
        or any(type(value) is not bool for value in boundary.values())
        or {key for key, value in boundary.items() if value} != expected_true
    ):
        raise ReceiptError("BOUNDARY_INVALID")
    authority = document["authority"]
    authority_keys = {
        "project_disposition", "fleet_adoption", "installation", "runtime",
        "provider_execution", "authentication", "task_or_gate_mutation",
    }
    if (
        type(authority) is not dict
        or set(authority) != authority_keys
        or any(type(value) is not bool or value for value in authority.values())
    ):
        raise ReceiptError("AUTHORITY_INVALID")


class ReviewReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = load(RECEIPT.read_bytes())

    def test_01_exact_receipt_passes(self):
        verify(self.document)

    def test_02_duplicate_and_case_collision_refuse(self):
        raw = RECEIPT.read_bytes().replace(b'"schema": ', b'"Schema": "forged", "schema": ', 1)
        with self.assertRaisesRegex(ReceiptError, "COLLIDING"):
            load(raw)

    def test_03_subject_and_closure_substitutions_refuse(self):
        for mutate in (
            lambda d: d["reviewed_subject"].update(commit="0" * 40),
            lambda d: d["reviewed_subject"]["source"].update(sha256="0" * 64),
            lambda d: d["finding_disposition"]["open_gate_hostile_missing"].update(
                test_sha256="0" * 64
            ),
        ):
            hostile = copy.deepcopy(self.document)
            mutate(hostile)
            with self.assertRaises(ReceiptError):
                verify(hostile)

    def test_04_result_and_consumption_substitutions_refuse(self):
        for mutate in (
            lambda d: d["independent_review"]["result_snapshot"].update(verdict="REVISE"),
            lambda d: d["independent_review"]["result_snapshot"].update(is_error=0),
            lambda d: d["independent_review"]["consumption_snapshot"].update(
                raw_receipt_sha256="0" * 64
            ),
        ):
            hostile = copy.deepcopy(self.document)
            mutate(hostile)
            with self.assertRaises(ReceiptError):
                verify(hostile)

    def test_05_authority_and_boundary_overclaims_refuse(self):
        for section, key in (
            ("authority", "installation"),
            ("authority", "project_disposition"),
            ("accepted_boundary", "production_launcher_wired"),
            ("accepted_boundary", "adoption"),
        ):
            hostile = copy.deepcopy(self.document)
            hostile[section][key] = True
            with self.subTest(section=section, key=key), self.assertRaises(ReceiptError):
                verify(hostile)


if __name__ == "__main__":
    unittest.main()
