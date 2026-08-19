#!/usr/bin/env python3
"""Run the CLOSED provider-control suite and emit durable hosted evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
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


def _file_binding(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(REPO).as_posix(),
        "bytes": len(data),
        "sha256": _sha256(data),
    }


def _strict_json(path: Path) -> dict[str, object]:
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
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


def _verify_evidence_bundle(manifest_path: Path) -> None:
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


def _write_fatal_diagnostic(args, error: Exception) -> None:
    anchor = args.manifest or args.result or args.verify_manifest
    if anchor is None:
        return
    diagnostic = {
        "schema": "mlv-provider-control-hosted-fatal-diagnostic/v1",
        "completeEvidenceBundle": False,
        "authority": False,
        "errorType": type(error).__name__,
        "error": str(error),
        "traceback": traceback.format_exc(),
    }
    try:
        diagnostic["git"] = {
            "head": _git("rev-parse", "HEAD"),
            "tree": _git("rev-parse", "HEAD^{tree}"),
        }
    except Exception as git_error:  # preserve the original failure
        diagnostic["gitError"] = str(git_error)
    _atomic_write(
        anchor.parent / "fatal-diagnostic.json",
        (json.dumps(diagnostic, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _execute(args, parser: argparse.ArgumentParser) -> int:
    if args.verify_manifest:
        if any((args.junit, args.result, args.manifest)):
            parser.error("--verify-manifest cannot be combined with write options")
        _verify_evidence_bundle(args.verify_manifest)
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
    status_after = _git("status", "--porcelain=v1", "--untracked-files=all")
    repository_clean = not status_after
    statuses = {name: sum(item["status"] == name for item in records) for name in {
        "passed", "failed", "error", "skipped", "expected-failure", "unexpected-success"
    }}
    closed_gate_passed = _closed_gate_passed(
        test_result, profile.returncode, repository_clean
    )
    result = {
        "schema": "mlv-provider-control-hosted-result/v1",
        "closedGatePassed": closed_gate_passed,
        "authority": False,
        "authorityScope": "ZERO_AUTHORITY_CLOSED_GATE_EVIDENCE_ONLY",
        "git": {"head": head, "tree": tree},
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
        profile.returncode,
        profile.stdout + profile.stderr,
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
    args = parser.parse_args()
    try:
        return _execute(args, parser)
    except Exception as error:
        _write_fatal_diagnostic(args, error)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
