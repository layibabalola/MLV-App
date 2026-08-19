#!/usr/bin/env python3
"""Run the CLOSED provider-control suite and emit durable hosted evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import xml.etree.ElementTree as ET


REPO = Path(__file__).resolve().parents[3]
TEST_MODULE = "tools.provider_control.tests.test_mlv_lane_supervisor"
PROFILE = REPO / "tools/provider_control/mlv-project-profile.candidate.json"
VALIDATOR = REPO / "tools/provider_control/vendor/universal_provider_control.py"
EXPECTED_TEST_COUNT = 45
EXPECTED_TEST_IDS_SHA256 = "38c03bf4b8f5dbd82ae366e7a55221a1c85a456d9e1edf3f9858677fa1203de1"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: dict[str, dict[str, object]] = {}
        self._started: dict[str, float] = {}

    def _ensure_record(self, test) -> dict[str, object]:
        test_id = test.id()
        return self.records.setdefault(
            test_id, {"id": test_id, "status": "running", "durationSeconds": 0.0}
        )

    def _set_outcome(self, test, status: str, detail: str | None = None) -> None:
        record = self._ensure_record(test)
        record["status"] = status
        if detail:
            existing = record.get("detail")
            record["detail"] = f"{existing}\n\n{detail}" if existing else detail

    def startTest(self, test):  # noqa: N802 - unittest API
        self._started[test.id()] = time.perf_counter()
        self.records[test.id()] = {
            "id": test.id(), "status": "running", "durationSeconds": 0.0
        }
        super().startTest(test)

    def stopTest(self, test):  # noqa: N802 - unittest API
        record = self._ensure_record(test)
        started = self._started.pop(test.id(), None)
        if started is not None:
            record["durationSeconds"] = round(time.perf_counter() - started, 6)
        if record["status"] == "running":
            record.update(
                status="error",
                detail="test stopped without a terminal unittest outcome",
            )
        super().stopTest(test)

    def addSuccess(self, test):  # noqa: N802 - unittest API
        self._set_outcome(test, "passed")
        super().addSuccess(test)

    def addFailure(self, test, err):  # noqa: N802 - unittest API
        self._set_outcome(test, "failed", self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):  # noqa: N802 - unittest API
        self._set_outcome(test, "error", self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSkip(self, test, reason):  # noqa: N802 - unittest API
        self._set_outcome(test, "skipped", reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):  # noqa: N802 - unittest API
        self._set_outcome(
            test, "expected-failure", self._exc_info_to_string(err, test)
        )
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):  # noqa: N802 - unittest API
        self._set_outcome(test, "unexpected-success")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):  # noqa: N802 - unittest API
        if err is not None:
            status = "failed" if issubclass(err[0], test.failureException) else "error"
            self._set_outcome(
                test,
                status,
                f"subtest {subtest.id()}\n{self._exc_info_to_string(err, test)}",
            )
        super().addSubTest(test, subtest, err)


def _junit_bytes(
    records: list[dict[str, object]],
    started: str,
    elapsed: float,
    profile_exit_code: int = 0,
    profile_detail: str = "",
    repository_clean: bool = True,
    repository_detail: str = "",
) -> bytes:
    records = [
        *records,
        {
            "id": "provider_control.profile.validation",
            "status": "passed" if profile_exit_code == 0 else "error",
            "durationSeconds": 0.0,
            "detail": profile_detail,
        },
        {
            "id": "provider_control.repository.clean_after",
            "status": "passed" if repository_clean else "error",
            "durationSeconds": 0.0,
            "detail": repository_detail,
        },
    ]
    counts = {name: sum(item["status"] == name for item in records) for name in {
        "failed", "error", "skipped", "expected-failure", "unexpected-success"
    }}
    suite = ET.Element("testsuite", {
        "name": "mlv-provider-control-closed-gate",
        "tests": str(len(records)),
        "failures": str(counts["failed"] + counts["unexpected-success"]),
        "errors": str(counts["error"]),
        "skipped": str(counts["skipped"] + counts["expected-failure"]),
        "time": f"{elapsed:.6f}",
        "timestamp": started,
        "hostname": socket.gethostname(),
    })
    for record in records:
        test_id = str(record["id"])
        class_name, _, test_name = test_id.rpartition(".")
        case = ET.SubElement(suite, "testcase", {
            "classname": class_name,
            "name": test_name,
            "time": f"{float(record.get('durationSeconds', 0.0)):.6f}",
        })
        status = record["status"]
        if status in {"failed", "unexpected-success"}:
            child = ET.SubElement(case, "failure", {"type": str(status)})
            child.text = str(record.get("detail", status))
        elif status == "error":
            child = ET.SubElement(case, "error", {"type": "error"})
            child.text = str(record.get("detail", status))
        elif status in {"skipped", "expected-failure"}:
            child = ET.SubElement(case, "skipped", {"type": str(status)})
            child.text = str(record.get("detail", status))
    document = ET.ElementTree(suite)
    ET.indent(document, space="  ")
    return ET.tostring(suite, encoding="utf-8", xml_declaration=True)


def _closed_gate_passed(
    result: EvidenceResult, profile_exit_code: int, repository_clean: bool = True
) -> bool:
    statuses = [record["status"] for record in result.records.values()]
    return bool(
        result.wasSuccessful()
        and statuses
        and all(status == "passed" for status in statuses)
        and profile_exit_code == 0
        and repository_clean
    )


def _append_github_summary(result: dict[str, object]) -> None:
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        return
    tests = result["tests"]
    git = result["git"]
    lines = [
        "## Provider control CLOSED gate",
        "",
        f"- Result: **{'PASS' if result['closedGatePassed'] else 'FAIL'}**",
        f"- Head: `{git['head']}`",
        f"- Tree: `{git['tree']}`",
        f"- Tests: {tests['passed']}/{tests['total']} passed; "
        f"{tests['failed']} failed; {tests['error']} errors; {tests['skipped']} skipped",
        f"- Profile validation exit: {result['profileValidation']['exitCode']}",
        "- Provider calls/processes/tokens: 0/0/0",
        "- Automatic launch gate: `CLOSED`",
        "- Authority scope: zero-authority evidence only",
        "",
    ]
    with Path(destination).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _test_ids_sha256(test_ids: list[str]) -> str:
    return _sha256(
        json.dumps(sorted(test_ids), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _loaded_test_ids(item) -> list[str]:
    if isinstance(item, unittest.TestSuite):
        result = []
        for child in item:
            result.extend(_loaded_test_ids(child))
        return result
    return [item.id()]


def _file_binding(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(REPO).as_posix(),
        "bytes": len(data),
        "sha256": _sha256(data),
    }


def _runtime_environment() -> dict[str, object]:
    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "githubActions": os.environ.get("GITHUB_ACTIONS") == "true",
        "githubRepository": os.environ.get("GITHUB_REPOSITORY"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "githubRunAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "githubJob": os.environ.get("GITHUB_JOB"),
        "githubRef": os.environ.get("GITHUB_REF"),
    }


def _run_profile_validation() -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR), "validate", "profile", str(PROFILE)],
        cwd=REPO, text=True, encoding="utf-8", stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    return {
        "exitCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _strict_json(path: Path) -> dict[str, object]:
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def no_non_finite(constant):
        raise ValueError(f"non-finite JSON constant is forbidden: {constant}")

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=no_duplicates,
        parse_constant=no_non_finite,
    )
    if not isinstance(value, dict):
        raise ValueError("evidence manifest must be an object")
    return value


def _write_evidence_bundle(
    result_path: Path,
    junit_path: Path,
    manifest_path: Path,
    result_bytes: bytes,
    junit_bytes: bytes,
    head: str,
    tree: str,
) -> None:
    parents = {path.parent.resolve() for path in (result_path, junit_path, manifest_path)}
    if len(parents) != 1 or len({path.name for path in (result_path, junit_path, manifest_path)}) != 3:
        raise ValueError("evidence bundle paths must be distinct files in one directory")
    _atomic_write(result_path, result_bytes)
    _atomic_write(junit_path, junit_bytes)
    manifest = {
        "schema": "mlv-provider-control-hosted-evidence-manifest/v1",
        "complete": True,
        "authority": False,
        "git": {"head": head, "tree": tree},
        "files": [
            {"name": result_path.name, "bytes": len(result_bytes), "sha256": _sha256(result_bytes)},
            {"name": junit_path.name, "bytes": len(junit_bytes), "sha256": _sha256(junit_bytes)},
        ],
    }
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _verify_evidence_bundle(
    manifest_path: Path, *, require_live_clean: bool = True
) -> None:
    if (manifest_path.parent / "fatal-diagnostic.json").exists():
        raise ValueError("fatal diagnostic exists beside evidence bundle")
    manifest = _strict_json(manifest_path)
    if set(manifest) != {"schema", "complete", "authority", "git", "files"}:
        raise ValueError("evidence manifest keys are not exact")
    if manifest["schema"] != "mlv-provider-control-hosted-evidence-manifest/v1":
        raise ValueError("evidence manifest schema mismatch")
    if manifest["complete"] is not True or manifest["authority"] is not False:
        raise ValueError("evidence manifest is incomplete or claims authority")
    git = manifest["git"]
    if not isinstance(git, dict) or set(git) != {"head", "tree"}:
        raise ValueError("evidence manifest Git binding is invalid")
    if git["head"] != _git("rev-parse", "HEAD") or git["tree"] != _git("rev-parse", "HEAD^{tree}"):
        raise ValueError("evidence manifest does not bind the checked-out tree")
    files = manifest["files"]
    if not isinstance(files, list) or len(files) != 2:
        raise ValueError("evidence manifest must bind exactly two evidence files")
    if {item.get("name") for item in files if isinstance(item, dict)} != {
        "result.json", "junit.xml"
    }:
        raise ValueError("evidence manifest filenames are not exact")
    names = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"name", "bytes", "sha256"}:
            raise ValueError("evidence file entry is invalid")
        name = item["name"]
        if not isinstance(name, str) or Path(name).name != name or name in names:
            raise ValueError("evidence file name is unsafe or duplicated")
        names.add(name)
        path = manifest_path.parent / name
        data = path.read_bytes()
        if type(item["bytes"]) is not int or item["bytes"] != len(data):
            raise ValueError(f"evidence byte count mismatch: {name}")
        if item["sha256"] != _sha256(data):
            raise ValueError(f"evidence digest mismatch: {name}")

    result = _strict_json(manifest_path.parent / "result.json")
    required_result_keys = {
        "schema", "closedGatePassed", "authority", "authorityScope", "git",
        "repository", "inputs", "environment", "startedAtUtc", "durationSeconds",
        "testInventory",
        "tests", "profileValidation", "declaredZeroActivityInvariant",
        "automaticLaunchGate",
    }
    if set(result) != required_result_keys:
        raise ValueError("hosted result keys are not exact")
    if result["schema"] != "mlv-provider-control-hosted-result/v1":
        raise ValueError("hosted result schema mismatch")
    if type(result["closedGatePassed"]) is not bool or result["authority"] is not False:
        raise ValueError("hosted result gate/authority fields are invalid")
    if result["authorityScope"] != "ZERO_AUTHORITY_CLOSED_GATE_EVIDENCE_ONLY":
        raise ValueError("hosted result authority scope mismatch")
    if result["automaticLaunchGate"] != "CLOSED" or result["git"] != manifest["git"]:
        raise ValueError("hosted result gate or Git binding mismatch")
    inventory = result["testInventory"]
    if inventory != {
        "count": EXPECTED_TEST_COUNT,
        "idsSha256": EXPECTED_TEST_IDS_SHA256,
    }:
        raise ValueError("hosted result expected test inventory mismatch")
    repository = result["repository"]
    if not isinstance(repository, dict) or set(repository) != {
        "cleanBefore", "cleanAfter", "statusAfter"
    }:
        raise ValueError("hosted result repository evidence is invalid")
    if repository["cleanBefore"] is not True or type(repository["cleanAfter"]) is not bool:
        raise ValueError("hosted result repository cleanliness types are invalid")
    if not isinstance(repository["statusAfter"], str):
        raise ValueError("hosted result repository status is invalid")
    if repository["cleanAfter"] != (repository["statusAfter"] == ""):
        raise ValueError("hosted result repository cleanliness disagrees with status")
    expected_inputs = [
        _file_binding(REPO / "tools/provider_control/AUTHOR-PACKET.json"),
        _file_binding(REPO / ".github/workflows/provider-control-candidate.yml"),
        _file_binding(REPO / ".github/requirements/provider-control.txt"),
    ]
    if result["inputs"] != expected_inputs:
        raise ValueError("hosted result input bindings mismatch")
    environment = result["environment"]
    if environment != _runtime_environment():
        raise ValueError("hosted result environment does not match the live verifier process")
    started = result["startedAtUtc"]
    if not isinstance(started, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", started
    ):
        raise ValueError("hosted result timestamp is not canonical UTC")
    try:
        dt.datetime.fromisoformat(started[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("hosted result timestamp is invalid") from error
    duration = result["durationSeconds"]
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(
        duration
    ) or duration < 0:
        raise ValueError("hosted result duration is invalid")
    zero = result["declaredZeroActivityInvariant"]
    if zero != {
        "basis": "CLOSED_TEST_SUITE_CONTRACT_ASSERTIONS_NOT_PROVIDER_TELEMETRY",
        "providerCalls": 0,
        "providerProcesses": 0,
        "tokens": 0,
        "observedByIndependentProviderTelemetry": False,
    }:
        raise ValueError("hosted result zero-activity provenance is invalid")
    profile = result["profileValidation"]
    if not isinstance(profile, dict) or set(profile) != {"exitCode", "stdout", "stderr"}:
        raise ValueError("hosted result profile validation is invalid")
    if type(profile["exitCode"]) is not int or not all(
        isinstance(profile[key], str) for key in ("stdout", "stderr")
    ):
        raise ValueError("hosted result profile validation types are invalid")
    if profile != _run_profile_validation():
        raise ValueError("hosted result profile validation disagrees with the live verifier")
    tests = result["tests"]
    status_names = {
        "passed", "failed", "error", "skipped", "expected-failure", "unexpected-success"
    }
    if not isinstance(tests, dict) or set(tests) != {"total", "records", *status_names}:
        raise ValueError("hosted result test keys are invalid")
    records = tests["records"]
    if type(tests["total"]) is not int or not isinstance(records, list) or tests["total"] != len(records):
        raise ValueError("hosted result test total is invalid")
    actual_counts = {name: 0 for name in status_names}
    record_ids = set()
    for record in records:
        if not isinstance(record, dict) or not {"id", "status", "durationSeconds"} <= set(record):
            raise ValueError("hosted result test record is invalid")
        if set(record) - {"id", "status", "durationSeconds", "detail"}:
            raise ValueError("hosted result test record has unknown keys")
        if not isinstance(record["id"], str) or record["id"] in record_ids:
            raise ValueError("hosted result test record id is invalid")
        record_ids.add(record["id"])
        if record["status"] not in status_names:
            raise ValueError("hosted result test status is invalid")
        if not isinstance(record["durationSeconds"], (int, float)) or isinstance(
            record["durationSeconds"], bool
        ) or not math.isfinite(record["durationSeconds"]) or record["durationSeconds"] < 0:
            raise ValueError("hosted result test duration is invalid")
        if "detail" in record and not isinstance(record["detail"], str):
            raise ValueError("hosted result test detail is invalid")
        actual_counts[record["status"]] += 1
    if any(type(tests[name]) is not int or tests[name] != actual_counts[name] for name in status_names):
        raise ValueError("hosted result test counters mismatch")
    if tests["total"] != EXPECTED_TEST_COUNT or _test_ids_sha256(
        [record["id"] for record in records]
    ) != EXPECTED_TEST_IDS_SHA256:
        raise ValueError("hosted result test discovery is truncated or changed")
    expected_pass = (
        tests["total"] > 0
        and tests["passed"] == tests["total"]
        and profile["exitCode"] == 0
        and repository["cleanAfter"] is True
    )
    if result["closedGatePassed"] != expected_pass:
        raise ValueError("hosted result CLOSED-gate verdict mismatch")

    junit_root = ET.parse(manifest_path.parent / "junit.xml").getroot()
    if junit_root.tag != "testsuite":
        raise ValueError("JUnit root is not testsuite")
    expected_root_attributes = {
        "name", "tests", "failures", "errors", "skipped", "time", "timestamp", "hostname"
    }
    if set(junit_root.attrib) != expected_root_attributes:
        raise ValueError("JUnit root attributes are not exact")
    if junit_root.attrib["name"] != "mlv-provider-control-closed-gate":
        raise ValueError("JUnit suite name is invalid")
    if junit_root.attrib["timestamp"] != started:
        raise ValueError("JUnit timestamp disagrees with hosted result")
    if junit_root.attrib["hostname"] != socket.gethostname():
        raise ValueError("JUnit hostname disagrees with the live verifier process")
    try:
        junit_suite_duration = float(junit_root.attrib["time"])
    except ValueError as error:
        raise ValueError("JUnit suite duration is invalid") from error
    if not math.isfinite(junit_suite_duration) or junit_suite_duration != round(duration, 6):
        raise ValueError("JUnit suite duration disagrees with hosted result")
    try:
        junit_counts = {
            key: int(junit_root.attrib[key]) for key in ("tests", "failures", "errors", "skipped")
        }
    except (KeyError, ValueError) as error:
        raise ValueError("JUnit counters are invalid") from error
    expected_junit = {
        "tests": tests["total"] + 2,
        "failures": tests["failed"] + tests["unexpected-success"],
        "errors": tests["error"] + (profile["exitCode"] != 0) + (not repository["cleanAfter"]),
        "skipped": tests["skipped"] + tests["expected-failure"],
    }
    if junit_counts != expected_junit:
        raise ValueError("JUnit counters disagree with hosted result")
    raw_cases = list(junit_root)
    if any(case.tag != "testcase" for case in raw_cases):
        raise ValueError("JUnit root contains a non-testcase child")
    if len(raw_cases) != expected_junit["tests"]:
        raise ValueError("JUnit raw testcase cardinality disagrees with hosted result")
    cases = {
        (case.attrib.get("classname"), case.attrib.get("name")): case
        for case in raw_cases
    }
    if len(cases) != len(raw_cases):
        raise ValueError("JUnit testcase identities are duplicated")
    profile_case = cases.get(("provider_control.profile", "validation"))
    repository_case = cases.get(("provider_control.repository", "clean_after"))
    if profile_case is None or repository_case is None:
        raise ValueError("JUnit synthetic checks are missing")
    for name, case in (("profile", profile_case), ("repository", repository_case)):
        if set(case.attrib) != {"classname", "name", "time"} or case.attrib["time"] != "0.000000":
            raise ValueError(f"JUnit {name} synthetic testcase metadata is invalid")
    if bool(profile_case.find("error") is not None) != (profile["exitCode"] != 0):
        raise ValueError("JUnit profile outcome mismatch")
    if bool(repository_case.find("error") is not None) != (not repository["cleanAfter"]):
        raise ValueError("JUnit repository outcome mismatch")
    if profile["exitCode"] != 0:
        profile_error = profile_case.find("error")
        if profile_error.attrib != {"type": "error"} or (profile_error.text or "") != (
            profile["stdout"] + profile["stderr"]
        ):
            raise ValueError("JUnit profile detail mismatch")
    if not repository["cleanAfter"]:
        repository_error = repository_case.find("error")
        if repository_error.attrib != {"type": "error"} or (
            repository_error.text or ""
        ) != repository["statusAfter"]:
            raise ValueError("JUnit repository detail mismatch")
    expected_case_keys = {
        tuple(str(record["id"]).rpartition(".")[::2]) for record in records
    } | {
        ("provider_control.profile", "validation"),
        ("provider_control.repository", "clean_after"),
    }
    if set(cases) != expected_case_keys:
        raise ValueError("JUnit testcase identities disagree with hosted result")

    outcome_tag = {
        "passed": None,
        "failed": "failure",
        "unexpected-success": "failure",
        "error": "error",
        "skipped": "skipped",
        "expected-failure": "skipped",
    }
    actual_child_counts = {"failure": 0, "error": 0, "skipped": 0}
    for case in cases.values():
        children = list(case)
        if any(child.tag not in actual_child_counts for child in children) or len(children) > 1:
            raise ValueError("JUnit testcase has invalid outcome children")
        if children:
            actual_child_counts[children[0].tag] += 1
    if actual_child_counts != {
        "failure": junit_counts["failures"],
        "error": junit_counts["errors"],
        "skipped": junit_counts["skipped"],
    }:
        raise ValueError("JUnit outcome children disagree with root counters")
    for record in records:
        class_name, _, test_name = str(record["id"]).rpartition(".")
        case = cases[(class_name, test_name)]
        expected_tag = outcome_tag[record["status"]]
        children = list(case)
        actual_tag = children[0].tag if children else None
        if actual_tag != expected_tag:
            raise ValueError(f"JUnit outcome disagrees for {record['id']}")
        if set(case.attrib) != {"classname", "name", "time"}:
            raise ValueError(f"JUnit testcase metadata is invalid for {record['id']}")
        if expected_tag is not None:
            child = children[0]
            expected_type = "error" if expected_tag == "error" else record["status"]
            if child.attrib != {"type": expected_type}:
                raise ValueError(f"JUnit outcome type disagrees for {record['id']}")
            expected_detail = str(record.get("detail", record["status"]))
            if (child.text or "") != expected_detail:
                raise ValueError(f"JUnit outcome detail disagrees for {record['id']}")
        elif "detail" in record:
            raise ValueError(f"passed test record must not claim detail: {record['id']}")
        try:
            junit_duration = float(case.attrib["time"])
        except (KeyError, ValueError) as error:
            raise ValueError(f"JUnit duration is invalid for {record['id']}") from error
        if not math.isfinite(junit_duration):
            raise ValueError(f"JUnit duration is non-finite for {record['id']}")
        if junit_duration != round(float(record["durationSeconds"]), 6):
            raise ValueError(f"JUnit duration disagrees for {record['id']}")
    if require_live_clean:
        live_status = _git("status", "--porcelain=v1", "--untracked-files=all")
        if live_status:
            raise ValueError(f"repository became dirty before evidence routing:\n{live_status}")


def _verify_fatal_diagnostic(path: Path) -> None:
    diagnostic = _strict_json(path)
    base_keys = {
        "schema", "completeEvidenceBundle", "authority", "errorType", "error",
        "traceback", "environment", "git",
    }
    optional = {"invalidExistingDiagnostic"}
    if not base_keys <= set(diagnostic) or set(diagnostic) - base_keys - optional:
        raise ValueError("fatal diagnostic keys are invalid")
    if diagnostic["schema"] != "mlv-provider-control-hosted-fatal-diagnostic/v1":
        raise ValueError("fatal diagnostic schema mismatch")
    if diagnostic["completeEvidenceBundle"] is not False or diagnostic["authority"] is not False:
        raise ValueError("fatal diagnostic completeness or authority is invalid")
    if not all(
        isinstance(diagnostic[key], str) and diagnostic[key]
        for key in ("errorType", "error")
    ) or not isinstance(diagnostic["traceback"], str):
        raise ValueError("fatal diagnostic error fields are invalid")
    if diagnostic["environment"] != _runtime_environment():
        raise ValueError("fatal diagnostic environment does not match the live verifier process")
    expected_git = {
        "head": _git("rev-parse", "HEAD"),
        "tree": _git("rev-parse", "HEAD^{tree}"),
    }
    if diagnostic["git"] != expected_git:
        raise ValueError("fatal diagnostic Git binding mismatch")
    if "invalidExistingDiagnostic" in diagnostic:
        invalid = diagnostic["invalidExistingDiagnostic"]
        if not isinstance(invalid, dict) or set(invalid) != {
            "bytes", "sha256", "validationError"
        }:
            raise ValueError("fatal diagnostic invalid-predecessor receipt is malformed")
        if type(invalid["bytes"]) is not int or invalid["bytes"] < 0:
            raise ValueError("fatal diagnostic invalid-predecessor byte count is invalid")
        if not isinstance(invalid["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", invalid["sha256"]
        ) or not isinstance(invalid["validationError"], str) or not invalid["validationError"]:
            raise ValueError("fatal diagnostic invalid-predecessor binding is invalid")


def _write_fatal_diagnostic(args, error: Exception) -> None:
    anchor = (
        getattr(args, "manifest", None)
        or getattr(args, "result", None)
        or getattr(args, "verify_manifest", None)
        or getattr(args, "verify_fatal", None)
    )
    if anchor is None:
        return
    target = anchor.parent / "fatal-diagnostic.json"
    invalid_existing = None
    if target.exists():
        try:
            _verify_fatal_diagnostic(target)
            return
        except Exception as existing_error:
            existing = target.read_bytes()
            invalid_existing = {
                "bytes": len(existing),
                "sha256": _sha256(existing),
                "validationError": str(existing_error),
            }
    diagnostic = {
        "schema": "mlv-provider-control-hosted-fatal-diagnostic/v1",
        "completeEvidenceBundle": False,
        "authority": False,
        "errorType": type(error).__name__,
        "error": str(error),
        "traceback": traceback.format_exc(),
        "environment": _runtime_environment(),
    }
    if invalid_existing is not None:
        diagnostic["invalidExistingDiagnostic"] = invalid_existing
    try:
        diagnostic["git"] = {
            "head": _git("rev-parse", "HEAD"),
            "tree": _git("rev-parse", "HEAD^{tree}"),
        }
    except Exception as git_error:  # preserve the original failure
        diagnostic["gitError"] = str(git_error)
    _atomic_write(
        target,
        (json.dumps(diagnostic, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _execute(args, parser: argparse.ArgumentParser) -> int:
    if args.verify_manifest:
        if any((args.junit, args.result, args.manifest, args.verify_fatal)):
            parser.error("--verify-manifest cannot be combined with write options")
        _verify_evidence_bundle(args.verify_manifest)
        return 0
    if args.verify_fatal:
        if any((args.junit, args.result, args.manifest, args.verify_manifest)):
            parser.error("--verify-fatal cannot be combined with other options")
        _verify_fatal_diagnostic(args.verify_fatal)
        return 0
    if not all((args.junit, args.result, args.manifest)):
        parser.error("--junit, --result, and --manifest are all required")

    status_before = _git("status", "--porcelain=v1", "--untracked-files=all")
    if status_before:
        raise RuntimeError(f"repository is dirty before hosted execution:\n{status_before}")
    head = _git("rev-parse", "HEAD")
    expected = os.environ.get("EXPECTED_HEAD_SHA", head)
    if expected != head:
        raise RuntimeError(f"checked-out HEAD {head} != EXPECTED_HEAD_SHA {expected}")
    tree = _git("rev-parse", "HEAD^{tree}")
    started_at = dt.datetime.now(dt.timezone.utc)
    started_text = started_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    started_clock = time.perf_counter()

    suite = unittest.defaultTestLoader.loadTestsFromName(TEST_MODULE)
    discovered_ids = sorted(_loaded_test_ids(suite))
    if len(discovered_ids) != EXPECTED_TEST_COUNT or _test_ids_sha256(
        discovered_ids
    ) != EXPECTED_TEST_IDS_SHA256:
        raise RuntimeError("hosted test discovery does not match the committed inventory")
    runner = unittest.TextTestRunner(
        stream=sys.stdout, verbosity=2, resultclass=EvidenceResult
    )
    test_result = runner.run(suite)
    records = [test_result.records[key] for key in sorted(test_result.records)]

    profile = _run_profile_validation()
    elapsed = time.perf_counter() - started_clock
    status_after = _git("status", "--porcelain=v1", "--untracked-files=all")
    repository_clean = not status_after
    statuses = {name: sum(item["status"] == name for item in records) for name in {
        "passed", "failed", "error", "skipped", "expected-failure", "unexpected-success"
    }}
    closed_gate_passed = _closed_gate_passed(
        test_result, profile["exitCode"], repository_clean
    )
    result = {
        "schema": "mlv-provider-control-hosted-result/v1",
        "closedGatePassed": closed_gate_passed,
        "authority": False,
        "authorityScope": "ZERO_AUTHORITY_CLOSED_GATE_EVIDENCE_ONLY",
        "git": {"head": head, "tree": tree},
        "testInventory": {
            "count": EXPECTED_TEST_COUNT,
            "idsSha256": EXPECTED_TEST_IDS_SHA256,
        },
        "repository": {
            "cleanBefore": True,
            "cleanAfter": repository_clean,
            "statusAfter": status_after,
        },
        "inputs": [
            _file_binding(REPO / "tools/provider_control/AUTHOR-PACKET.json"),
            _file_binding(REPO / ".github/workflows/provider-control-candidate.yml"),
            _file_binding(REPO / ".github/requirements/provider-control.txt"),
        ],
        "environment": _runtime_environment(),
        "startedAtUtc": started_text,
        "durationSeconds": round(elapsed, 6),
        "tests": {"total": len(records), **statuses, "records": records},
        "profileValidation": profile,
        "declaredZeroActivityInvariant": {
            "basis": "CLOSED_TEST_SUITE_CONTRACT_ASSERTIONS_NOT_PROVIDER_TELEMETRY",
            "providerCalls": 0,
            "providerProcesses": 0,
            "tokens": 0,
            "observedByIndependentProviderTelemetry": False,
        },
        "automaticLaunchGate": "CLOSED",
    }
    result_bytes = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
    junit_bytes = _junit_bytes(
        records,
        started_text,
        elapsed,
        profile["exitCode"],
        profile["stdout"] + profile["stderr"],
        repository_clean,
        status_after,
    )
    _write_evidence_bundle(
        args.result, args.junit, args.manifest, result_bytes, junit_bytes, head, tree
    )
    _append_github_summary(result)
    return 0 if closed_gate_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--verify-manifest", type=Path)
    parser.add_argument("--verify-fatal", type=Path)
    args = parser.parse_args()
    try:
        return _execute(args, parser)
    except Exception as error:
        _write_fatal_diagnostic(args, error)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
