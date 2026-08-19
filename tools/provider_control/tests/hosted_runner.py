#!/usr/bin/env python3
"""Run the CLOSED provider-control suite and emit durable hosted evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET


REPO = Path(__file__).resolve().parents[3]
TEST_MODULE = "tools.provider_control.tests.test_mlv_lane_supervisor"
PROFILE = REPO / "tools/provider_control/mlv-project-profile.candidate.json"
VALIDATOR = REPO / "tools/provider_control/vendor/universal_provider_control.py"
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

    def startTest(self, test):  # noqa: N802 - unittest API
        self._started[test.id()] = time.perf_counter()
        self.records[test.id()] = {"id": test.id(), "status": "running"}
        super().startTest(test)

    def stopTest(self, test):  # noqa: N802 - unittest API
        record = self.records[test.id()]
        record["durationSeconds"] = round(
            time.perf_counter() - self._started.pop(test.id()), 6
        )
        super().stopTest(test)

    def addSuccess(self, test):  # noqa: N802 - unittest API
        self.records[test.id()]["status"] = "passed"
        super().addSuccess(test)

    def addFailure(self, test, err):  # noqa: N802 - unittest API
        self.records[test.id()].update(status="failed", detail=self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):  # noqa: N802 - unittest API
        self.records[test.id()].update(status="error", detail=self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSkip(self, test, reason):  # noqa: N802 - unittest API
        self.records[test.id()].update(status="skipped", detail=reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):  # noqa: N802 - unittest API
        self.records[test.id()].update(
            status="expected-failure", detail=self._exc_info_to_string(err, test)
        )
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):  # noqa: N802 - unittest API
        self.records[test.id()]["status"] = "unexpected-success"
        super().addUnexpectedSuccess(test)


def _junit_bytes(records: list[dict[str, object]], started: str, elapsed: float) -> bytes:
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


def _closed_gate_passed(result: EvidenceResult, profile_exit_code: int) -> bool:
    statuses = [record["status"] for record in result.records.values()]
    return bool(
        result.wasSuccessful()
        and statuses
        and all(status == "passed" for status in statuses)
        and profile_exit_code == 0
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()

    head = _git("rev-parse", "HEAD")
    expected = os.environ.get("EXPECTED_HEAD_SHA", head)
    if expected != head:
        raise RuntimeError(f"checked-out HEAD {head} != EXPECTED_HEAD_SHA {expected}")
    tree = _git("rev-parse", "HEAD^{tree}")
    started_at = dt.datetime.now(dt.timezone.utc)
    started_text = started_at.isoformat().replace("+00:00", "Z")
    started_clock = time.perf_counter()

    suite = unittest.defaultTestLoader.loadTestsFromName(TEST_MODULE)
    runner = unittest.TextTestRunner(
        stream=sys.stdout, verbosity=2, resultclass=EvidenceResult
    )
    test_result = runner.run(suite)
    records = [test_result.records[key] for key in sorted(test_result.records)]

    profile = subprocess.run(
        [sys.executable, str(VALIDATOR), "validate", "profile", str(PROFILE)],
        cwd=REPO, text=True, encoding="utf-8", stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    elapsed = time.perf_counter() - started_clock
    statuses = {name: sum(item["status"] == name for item in records) for name in {
        "passed", "failed", "error", "skipped", "expected-failure", "unexpected-success"
    }}
    closed_gate_passed = _closed_gate_passed(test_result, profile.returncode)
    result = {
        "schema": "mlv-provider-control-hosted-result/v1",
        "closedGatePassed": closed_gate_passed,
        "authority": False,
        "authorityScope": "ZERO_AUTHORITY_CLOSED_GATE_EVIDENCE_ONLY",
        "git": {"head": head, "tree": tree},
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "githubRepository": os.environ.get("GITHUB_REPOSITORY"),
            "githubRunId": os.environ.get("GITHUB_RUN_ID"),
            "githubRunAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "githubJob": os.environ.get("GITHUB_JOB"),
            "githubRef": os.environ.get("GITHUB_REF"),
        },
        "startedAtUtc": started_text,
        "durationSeconds": round(elapsed, 6),
        "tests": {"total": len(records), **statuses, "records": records},
        "profileValidation": {
            "exitCode": profile.returncode,
            "stdout": profile.stdout,
            "stderr": profile.stderr,
        },
        "providerCalls": 0,
        "providerProcesses": 0,
        "tokens": 0,
        "automaticLaunchGate": "CLOSED",
    }
    _atomic_write(
        args.result,
        (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _atomic_write(args.junit, _junit_bytes(records, started_text, elapsed))
    _append_github_summary(result)
    return 0 if closed_gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
