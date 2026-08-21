#!/usr/bin/env python3
"""Verify the exact R39 Sonnet review receipt without granting authority."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "docs/universal-token-control-supervisor-integration-review-receipt-2026-08-21.json"
R39 = "55900233427ad1b85782859a114141ee29462511"
R39_TREE = "5fdf3107dd3f22df8c732cde770fea009f9ea588"
PARENTS = [
    "a4bb0d7a2833e760ba1d0d6cfb53133d69f09c24",
    "50db64d71bf62da041b9a6872c893382cd7cc79b",
]
AUTHORITY_KEYS = {
    "projectDisposition", "fleetAdoption", "installation", "runtime",
    "providerExecution", "authentication", "taskOrGateMutation", "pushOrMerge",
}


class ReceiptError(ValueError):
    pass


def pairs(items):
    out, folded = {}, set()
    for key, value in items:
        if type(key) is not str or key.casefold() in folded:
            raise ReceiptError("DUPLICATE_OR_CASE_COLLIDING_KEY")
        folded.add(key.casefold())
        out[key] = value
    return out


def load(raw: bytes):
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("JSON_INVALID") from exc
    if type(value) is not dict:
        raise ReceiptError("JSON_ROOT_INVALID")
    return value


def git(*args: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    run = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False, env=env)
    if run.returncode:
        raise ReceiptError("GIT_COMMAND_FAILED")
    return run.stdout


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def verify_file(row: dict) -> bytes:
    if type(row) is not dict or set(row) != {"path", "bytes", "sha256"}:
        raise ReceiptError("FILE_ROW_INVALID")
    path = Path(row["path"])
    raw = path.read_bytes()
    if len(raw) != row["bytes"] or sha(raw) != row["sha256"]:
        raise ReceiptError("FILE_TUPLE_INVALID")
    return raw


def verify(document: dict) -> None:
    expected_keys = {
        "schema", "status", "reviewedSubject", "localExecutionEvidence",
        "independentReview", "findingDisposition", "acceptedBoundary", "authority",
    }
    if type(document) is not dict or set(document) != expected_keys:
        raise ReceiptError("DOCUMENT_SHAPE_INVALID")
    if document["schema"] != "mlv-universal-control-supervisor-integration-review/v1" or document["status"] != "ACCEPT_CANDIDATE_ONLY_ZERO_AUTHORITY":
        raise ReceiptError("DOCUMENT_HEADER_INVALID")
    subject = document["reviewedSubject"]
    lines = git("show", "-s", "--format=%T%n%P", R39).decode().splitlines()
    if subject["commit"] != R39 or subject["tree"] != R39_TREE or subject["orderedParents"] != PARENTS or lines != [R39_TREE, " ".join(PARENTS)]:
        raise ReceiptError("SUBJECT_IDENTITY_INVALID")
    for key in ("artifact", "tests"):
        row = subject[key]
        raw = git("show", f"{R39}:{row['path']}")
        oid = git("rev-parse", f"{R39}:{row['path']}").decode().strip()
        if oid != row["gitBlobOid"] or len(raw) != row["bytes"] or sha(raw) != row["sha256"]:
            raise ReceiptError("SUBJECT_TUPLE_INVALID")
    evidence = document["localExecutionEvidence"]
    if type(evidence) is not list or evidence != [
        {"suite": "tools.provider_control.tests.test_mlv_lane_supervisor", "result": "PASS", "tests": 45},
        {"suite": "tools/universal_token_control_supervisor_integration_candidate_tests.py", "result": "PASS", "tests": 8},
        {"suite": "tools/universal_token_control_machine_observation_candidate_hostile_tests.py", "result": "PASS", "tests": 140},
    ]:
        raise ReceiptError("EXECUTION_EVIDENCE_INVALID")
    review = document["independentReview"]
    packet = load(verify_file(review["packet"]))
    artifact_raw = verify_file(review["artifact"])
    consumption = load(verify_file(review["consumptionReceipt"]))
    if packet.get("route_id") != review["routeId"] or packet.get("lane") != review["lane"] or packet.get("role") != review["role"]:
        raise ReceiptError("PACKET_BINDING_INVALID")
    results = []
    result_lines = []
    for line in artifact_raw.splitlines():
        try:
            event = load(line)
        except ReceiptError:
            continue
        if event.get("type") == "result":
            results.append(event)
            result_lines.append(line)
    if len(results) != 1 or len(result_lines) != 1:
        raise ReceiptError("RESULT_CARDINALITY_INVALID")
    result, raw_line = results[0], result_lines[0]
    expected_result = review["resultLine"]
    structured = result.get("structured_output")
    if len(raw_line) != expected_result["bytes"] or sha(raw_line) != expected_result["sha256"]:
        raise ReceiptError("RESULT_TUPLE_INVALID")
    if result.get("subtype") != expected_result["subtype"] or result.get("is_error") is not expected_result["isError"] or result.get("session_id") != review["sessionId"]:
        raise ReceiptError("RESULT_ENVELOPE_INVALID")
    if type(structured) is not dict or structured.get("verdict") != expected_result["verdict"] or structured.get("route_id") != review["routeId"] or structured.get("lane") != review["lane"]:
        raise ReceiptError("STRUCTURED_RESULT_INVALID")
    if review["verdict"] != "ACCEPT_CANDIDATE_ONLY" or review["testExecutionPerformedByReviewer"] is not False:
        raise ReceiptError("REVIEW_SCOPE_INVALID")
    if consumption.get("route_id") != review["routeId"] or consumption.get("lane") != review["lane"]:
        raise ReceiptError("CONSUMPTION_BINDING_INVALID")
    boundary = document["acceptedBoundary"]
    if {key for key, value in boundary.items() if value is True} != {
        "functionalClosedSupervisorSourceIntegrated", "universalRequestEnvelopeIntegrated",
        "providerFreeControlSuiteIntegrated",
    } or any(type(value) is not bool for value in boundary.values()):
        raise ReceiptError("BOUNDARY_INVALID")
    authority = document["authority"]
    if type(authority) is not dict or set(authority) != AUTHORITY_KEYS or any(type(value) is not bool or value for value in authority.values()):
        raise ReceiptError("AUTHORITY_INVALID")


class ReviewReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = load(RECEIPT.read_bytes())

    def test_01_exact_receipt_passes(self):
        verify(self.document)

    def test_02_duplicate_keys_refuse(self):
        raw = RECEIPT.read_bytes().replace(b'"schema": ', b'"schema": "forged", "schema": ', 1)
        with self.assertRaisesRegex(ReceiptError, "DUPLICATE"):
            load(raw)

    def test_03_subject_mutations_refuse(self):
        for key, value in (("commit", "0" * 40), ("tree", "0" * 40), ("orderedParents", PARENTS[::-1])):
            hostile = copy.deepcopy(self.document)
            hostile["reviewedSubject"][key] = value
            with self.subTest(key=key), self.assertRaises(ReceiptError):
                verify(hostile)

    def test_04_test_count_and_native_aliases_refuse(self):
        for value in (44, True, "45"):
            hostile = copy.deepcopy(self.document)
            hostile["localExecutionEvidence"][0]["tests"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ReceiptError, "EXECUTION_EVIDENCE_INVALID"):
                verify(hostile)

    def test_05_review_file_and_result_mutations_refuse(self):
        for mutate in (
            lambda d: d["independentReview"]["packet"].update(sha256="0" * 64),
            lambda d: d["independentReview"]["artifact"].update(bytes=141774),
            lambda d: d["independentReview"]["resultLine"].update(sha256="0" * 64),
            lambda d: d["independentReview"].update(testExecutionPerformedByReviewer=True),
        ):
            hostile = copy.deepcopy(self.document)
            mutate(hostile)
            with self.assertRaises(ReceiptError):
                verify(hostile)

    def test_06_authority_and_boundary_overclaims_refuse(self):
        for section, key in (
            ("authority", "installation"),
            ("authority", "providerExecution"),
            ("acceptedBoundary", "productionLauncherWired"),
            ("acceptedBoundary", "adoption"),
        ):
            hostile = copy.deepcopy(self.document)
            hostile[section][key] = True
            with self.subTest(section=section, key=key), self.assertRaises(ReceiptError):
                verify(hostile)


if __name__ == "__main__":
    unittest.main()
