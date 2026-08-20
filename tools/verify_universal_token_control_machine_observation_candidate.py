#!/usr/bin/env python3
"""Strict verifier for the inert MLV-App current-machine observation candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_REPO_PATH = "docs/universal-token-control-machine-observation-candidate-2026-08-20.json"
VERIFIER_REPO_PATH = "tools/verify_universal_token_control_machine_observation_candidate.py"
HOSTILES_REPO_PATH = "tools/universal_token_control_machine_observation_candidate_hostile_tests.py"
ARTIFACT_PATH = ROOT / ARTIFACT_REPO_PATH
BASE_COMMIT = "38ed4bbf9068cac80140297c74facdf522577704"
BASE_TREE = "e3a20b1227fe40a1550664ea92343f76c760baae"
BASE_PARENT = "30889f77e2000190b94d59f80f6a03b12ce3e0d3"
BASE_ARTIFACT_PATH = "docs/universal-token-control-launcher-action-graph-census-candidate-2026-08-20.json"
BASE_ARTIFACT_BLOB = "ba0e476429dbf0aad33ea2b96b76986f394673de"
BASE_ARTIFACT_BYTES = 21007
BASE_ARTIFACT_SHA256 = "52aef218ac9657f381f9166675722a2083fe525d1b09f4ffb03fdf96f5d4a506"
CANONICAL_DOCUMENT_SHA256 = "a1866eef758b12903cdc877cf20e21b97b31b107d65f60cf49cc96f08a3e4446"

ROOT_KEYS = {
    "schema",
    "status",
    "baseCandidate",
    "observationBoundary",
    "machineInputs",
    "auditorObservation",
    "taskSchedulerOperationalSlice",
    "mlvNamedTaskObservation",
    "explicitMlvAppTargetPatternObservations",
    "separateProjectClassification",
    "creditDenials",
    "uncreditedProofs",
    "reviewBoundary",
    "authority",
    "nextLawfulStep",
}

BASE_CANDIDATE = {
    "commit": BASE_COMMIT,
    "tree": BASE_TREE,
    "parent": BASE_PARENT,
    "artifactPath": BASE_ARTIFACT_PATH,
    "artifactGitBlobOid": BASE_ARTIFACT_BLOB,
    "artifactBytes": BASE_ARTIFACT_BYTES,
    "artifactSha256": f"sha256:{BASE_ARTIFACT_SHA256}",
    "acceptedCandidateOnly": True,
    "adoptionTransferred": False,
    "authorityTransferred": False,
}

OBSERVATION_BOUNDARY = {
    "kind": "READ_ONLY_POINT_IN_TIME_CURRENT_MACHINE_OBSERVATION",
    "machineScope": "CURRENT_MACHINE_ONLY",
    "eventLogCutoffRecordId": 1774790,
    "eventLogCutoffUtc": "2026-08-20T16:39:02.5555858Z",
    "currentMachineStateObserved": True,
    "currentTaskDefinitionsClosed": False,
    "scheduledTaskInventoryComplete": False,
    "launcherInventoryComplete": False,
    "actionGraphComplete": False,
    "eventLogWindowCompleteHistory": False,
    "semanticCompletenessClaimed": False,
    "providerOrAuthInvoked": False,
    "processOrTaskActionPerformed": False,
    "machineFilesMutated": False,
}

MACHINE_INPUTS = [
    {
        "inputId": "MACHINE_SCHEDULED_TASK_REGISTRY",
        "path": r"C:\Users\obabalola\.claude\machine\SCHEDULED-TASK-REGISTRY.md",
        "bytes": 26289,
        "sha256": "sha256:340534b7632cdc0245cd9ed63878b6fb5c91a942bd57183dbbe3321385c112a2",
        "lastWriteTimeUtc": "2026-08-19T16:31:33.1058137Z",
        "readSucceeded": True,
        "mutated": False,
    },
    {
        "inputId": "MACHINE_SCHEDULED_TASK_AUDITOR",
        "path": r"C:\Users\obabalola\.claude\machine\audit-scheduled-tasks.ps1",
        "bytes": 22496,
        "sha256": "sha256:0bb0ca5a7cb23b71bc309f8005e11c8acd692a7fea203dd75c57b2cf9b8979c6",
        "lastWriteTimeUtc": "2026-08-01T01:26:32.4186232Z",
        "readSucceeded": True,
        "mutated": False,
    },
]

AUDITOR_OBSERVATION = {
    "command": r"pwsh -NoProfile -File C:\Users\obabalola\.claude\machine\audit-scheduled-tasks.ps1",
    "enumerationCall": "Get-ScheduledTask",
    "enumerationScriptLine": 214,
    "enumerationResult": "ACCESS_DENIED",
    "reportEmittedAfterNonTerminatingError": True,
    "reportedPopulation": 0,
    "reportedRegistryEntries": 41,
    "reportedLogCoverageHoursRounded": 56.9,
    "populationUsableAsCurrentTaskCountProof": False,
    "zeroPopulationMeansZeroTasks": False,
    "currentTaskDefinitionsClosed": False,
    "conclusion": "ACCESS_DENIED_ZERO_POPULATION_IS_NOT_ZERO_TASKS_AND_CANNOT_CLOSE_CURRENT_DEFINITIONS",
}

LOG_SLICE = {
    "channel": "Microsoft-Windows-TaskScheduler/Operational",
    "selection": "RecordId >= 1755824 AND RecordId <= 1774790",
    "firstRecordId": 1755824,
    "lastRecordId": 1774790,
    "firstEventId": 129,
    "lastEventId": 102,
    "firstUtc": "2026-08-18T07:52:03.0714055Z",
    "lastUtc": "2026-08-20T16:39:02.5555858Z",
    "coverageSeconds": 204419.4841803,
    "coverageHours": 56.783190050083334,
    "totalEvents": 18967,
    "rollingBuffer": True,
    "beforeWindowAbsenceProven": False,
    "afterCutoffAbsenceProven": False,
    "taskDefinitionEvidence": False,
    "taskExistenceAtCutoffProvenOnlyForObservedTaskNames": True,
}

EVENT_ID_COUNTS = [
    {"eventId": 100, "count": 682},
    {"eventId": 102, "count": 682},
    {"eventId": 107, "count": 682},
    {"eventId": 129, "count": 682},
    {"eventId": 200, "count": 682},
    {"eventId": 201, "count": 682},
]

MLV_TASK = {
    "taskName": r"\MlvGpuProfileAgentWatchdogV2",
    "eventCount": 4092,
    "eventIdCounts": EVENT_ID_COUNTS,
    "actionExecutables": ["wscript.exe"],
    "event201ResultCodeZeroCount": 682,
    "event201NonzeroOrMissingResultCount": 0,
    "firstRecordId": 1755841,
    "lastRecordId": 1774790,
    "firstUtc": "2026-08-18T07:53:59.7067262Z",
    "lastUtc": "2026-08-20T16:39:02.5555858Z",
    "taskDefinitionRead": False,
    "definitionOwnerInferredFromLog": False,
    "mlvAppTarget": False,
    "separateProjectClassificationFromRegistry": True,
}

MLV_OBSERVATION = {
    "matchField": "TaskName",
    "matchMode": "CASE_INSENSITIVE_SUBSTRING",
    "matchLiteral": "mlv",
    "distinctTaskNames": [r"\MlvGpuProfileAgentWatchdogV2"],
    "eventCount": 4092,
    "tasks": [MLV_TASK],
}

TARGET_PATTERNS = [
    "MLV-LaneIgnitionWatchdog",
    "mlv-app-dual-lane-codex-liveness",
    "APP_STORE_WAKE_FAMILY",
    "APP_STORE_MIRROR_FAMILY",
]

PATTERN_OBSERVATIONS = [
    {
        "pattern": pattern,
        "matchField": "TaskName",
        "matchMode": "CASE_INSENSITIVE_SUBSTRING",
        "eventCount": 0,
        "absenceProof": False,
        "interpretation": "NO_MATCH_IN_BOUNDED_ROLLING_WINDOW_ONLY",
    }
    for pattern in TARGET_PATTERNS
]

SEPARATE_PROJECT_CLASSIFICATION = {
    "sourceInputId": "MACHINE_SCHEDULED_TASK_REGISTRY",
    "registrySectionHeading": "MLV GPU profile bench — owner: MLV bench work (separate project, `G:\\Temp\\mlv-gpu-profile`)",
    "registryTaskHeading": r"\MlvGpuProfileAgentWatchdogV2",
    "ownerProject": "MLV_GPU_PROFILE_BENCH_SEPARATE_PROJECT",
    "ownerPath": r"G:\Temp\mlv-gpu-profile",
    "classificationAppliedToObservedTask": True,
    "classificationIsCurrentDefinitionProof": False,
    "classificationTransfersToMlvApp": False,
}

CREDIT_DENIALS = [
    ("CURRENT_TASK_DEFINITIONS_CLOSED", "GET_SCHEDULED_TASK_ACCESS_DENIED"),
    ("AUDITOR_POPULATION_USABLE", "ZERO_REPORTED_AFTER_ACCESS_DENIED_WHILE_REGISTRY_HAS_41_ENTRIES"),
    ("SCHEDULED_TASK_INVENTORY_COMPLETE", "DEFINITIONS_UNREADABLE_AND_EVENT_LOG_IS_NOT_AN_INVENTORY"),
    ("MLV_APP_TARGET_ACTIVITY_ABSENT", "ZERO_PATTERN_EVENTS_IN_ROLLING_WINDOW_IS_NOT_ABSENCE_PROOF"),
    ("MLV_GPU_WATCHDOG_BELONGS_TO_MLV_APP_PROJECT", "MACHINE_REGISTRY_CLASSIFIES_IT_UNDER_SEPARATE_MLV_GPU_BENCH_PROJECT"),
    ("COMPLETE_LAUNCHER_OR_ACTION_GRAPH", "OBSERVATION_IS_ONE_CHANNEL_ONE_RECORD_RANGE"),
    ("QUALITY_OR_FUNCTIONALITY_PRESERVED", "NO_PRODUCT_EXECUTION_OR_BASELINE_AB_PERFORMED"),
    ("ADOPT_DISTINGUISH_OR_REJECT_DISPOSITION", "NO_PROJECT_OWNER_DISPOSITION_PUBLISHED"),
    ("INSTALLATION_OR_RUNTIME_ACTIVATION", "READ_ONLY_OBSERVATION_PERFORMED_NO_INSTALL_TRANSACTION"),
]
CREDIT_DENIAL_OBJECTS = [
    {"claim": claim, "credited": False, "reason": reason} for claim, reason in CREDIT_DENIALS
]

UNCREDITED_PROOFS = [
    "COMPLETE_CURRENT_TASK_DEFINITIONS",
    "COMPLETE_SCHEDULED_TASK_INVENTORY",
    "COMPLETE_LAUNCHER_INVENTORY",
    "COMPLETE_ACTION_GRAPH",
    "EVENT_LOG_ABSENCE_OUTSIDE_PINNED_RANGE",
    "MODEL_EFFORT_ROLE_PRESERVATION",
    "QUALITY_EQUIVALENCE",
    "FUNCTIONALITY_EQUIVALENCE",
    "CURRENT_CLOSED_GATE_PROOF",
    "PROJECT_OWNER_DISPOSITION",
    "INSTALLATION",
    "ADOPTION",
    "RUNTIME_ACTIVATION",
    "PROVIDER_RECEIPT",
]

REVIEW_BOUNDARY = {
    "authorConflicted": True,
    "authorMaySelfReview": False,
    "freshIndependentReviewReceived": False,
    "distinctAdjudicationReceived": False,
    "priorReviewTransferAllowed": False,
    "baseCandidateReviewTransferAllowed": False,
}

AUTHORITY_KEYS = {
    "projectAdoption",
    "fleetAdoption",
    "dispositionCredit",
    "installation",
    "runtimeActivation",
    "providerInvocation",
    "authenticationAction",
    "processSpawnResumeKill",
    "schedulerOrTaskMutation",
    "automationMutation",
    "launcherMutation",
    "automaticGateMutation",
    "canaryCredit",
    "mergeLandingPushRelease",
    "qualityCredit",
    "functionalityCredit",
    "completenessCredit",
    "absenceCredit",
}

FORBIDDEN_ACTIONS = [
    "PROVIDER_OR_AUTH_INVOCATION",
    "PROCESS_SPAWN_RESUME_OR_KILL",
    "TASK_AUTOMATION_LAUNCHER_OR_GATE_MUTATION",
    "LEASE_PACKET_QUEUE_OR_RUNTIME_MUTATION",
    "MACHINE_FILE_MUTATION",
    "ADOPTION_INSTALLATION_CANARY_OR_DISPOSITION_CREDIT",
]

NEXT_LAWFUL_STEP = {
    "kind": "AUTHORIZED_READ_ONLY_TASK_DEFINITION_ENUMERATION_THEN_OWNER_DISPOSITION",
    "scope": "A separately authorized observer with Task Scheduler definition-read access must bind exact current MLV-App task definitions and reconcile them against the R35 launcher/action graph; any project-owner disposition, installation, or runtime activation remains a later separately reviewed transaction.",
    "requiredOutput": "A new forward-only exact subject naming every readable and unreadable task-definition surface, typed definition and history outcomes, complete-or-incomplete inventory declaration, and zero inherited adoption authority.",
    "forbiddenActions": FORBIDDEN_ACTIONS,
}


class ValidationError(ValueError):
    """Raised when an observation candidate violates its closed contract."""


class Checks:
    def __init__(self) -> None:
        self.count = 0
        self.errors: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        self.count += 1
        if not condition:
            self.errors.append(message)

    def exact(self, actual: Any, expected: Any, path: str) -> None:
        self.count += 1
        if type(actual) is not type(expected):
            self.errors.append(
                f"{path}: native type {type(actual).__name__}, expected {type(expected).__name__}"
            )
            return
        if isinstance(expected, dict):
            self.require(set(actual) == set(expected), f"{path}: keys are not closed")
            for key in expected:
                if key in actual:
                    self.exact(actual[key], expected[key], f"{path}.{key}")
            return
        if isinstance(expected, list):
            self.require(len(actual) == len(expected), f"{path}: array length/order is not closed")
            for index, expected_item in enumerate(expected):
                if index < len(actual):
                    self.exact(actual[index], expected_item, f"{path}[{index}]")
            return
        if actual != expected:
            self.errors.append(f"{path}: {actual!r}, expected {expected!r}")

    def finish(self) -> int:
        if self.errors:
            raise ValidationError("; ".join(self.errors))
        return self.count


def _reject_constant(value: str) -> Any:
    raise ValidationError(f"non-finite JSON constant rejected: {value}")


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key rejected: {key}")
        result[key] = value
    return result


def parse_document_bytes(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"artifact is not UTF-8: {exc}") from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc
    if type(document) is not dict:
        raise ValidationError("root must be a JSON object")
    return document


def canonical_document_sha256(document: dict[str, Any]) -> str:
    raw = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_document(document: dict[str, Any]) -> int:
    checks = Checks()
    checks.require(type(document) is dict, "root: native type must be object")
    if type(document) is not dict:
        return checks.finish()
    checks.require(set(document) == ROOT_KEYS, "root: keys are not closed")
    if set(document) != ROOT_KEYS:
        return checks.finish()

    checks.exact(
        document["schema"],
        "mlv-app-universal-token-control-machine-observation-candidate/v1",
        "schema",
    )
    checks.exact(document["status"], "POINT_IN_TIME_OBSERVATION_ZERO_AUTHORITY", "status")
    checks.exact(document["baseCandidate"], BASE_CANDIDATE, "baseCandidate")
    checks.exact(document["observationBoundary"], OBSERVATION_BOUNDARY, "observationBoundary")
    checks.exact(document["machineInputs"], MACHINE_INPUTS, "machineInputs")
    checks.exact(document["auditorObservation"], AUDITOR_OBSERVATION, "auditorObservation")
    checks.exact(document["taskSchedulerOperationalSlice"], LOG_SLICE, "taskSchedulerOperationalSlice")
    checks.exact(document["mlvNamedTaskObservation"], MLV_OBSERVATION, "mlvNamedTaskObservation")
    checks.exact(
        document["explicitMlvAppTargetPatternObservations"],
        PATTERN_OBSERVATIONS,
        "explicitMlvAppTargetPatternObservations",
    )
    checks.exact(
        document["separateProjectClassification"],
        SEPARATE_PROJECT_CLASSIFICATION,
        "separateProjectClassification",
    )
    checks.exact(document["creditDenials"], CREDIT_DENIAL_OBJECTS, "creditDenials")
    checks.exact(document["uncreditedProofs"], UNCREDITED_PROOFS, "uncreditedProofs")
    checks.exact(document["reviewBoundary"], REVIEW_BOUNDARY, "reviewBoundary")

    authority = document["authority"]
    checks.require(type(authority) is dict, "authority: native type must be object")
    if type(authority) is dict:
        checks.require(set(authority) == AUTHORITY_KEYS, "authority: keys are not closed")
        for key in sorted(AUTHORITY_KEYS):
            if key in authority:
                checks.require(type(authority[key]) is bool, f"authority.{key}: native type must be bool")
                checks.require(authority[key] is False, f"authority.{key}: must remain false")

    checks.exact(document["nextLawfulStep"], NEXT_LAWFUL_STEP, "nextLawfulStep")

    task = document["mlvNamedTaskObservation"]["tasks"][0]
    checks.require(
        sum(item["count"] for item in task["eventIdCounts"]) == task["eventCount"],
        "mlvNamedTaskObservation: event-id counts must sum to 4092",
    )
    checks.require(
        task["event201ResultCodeZeroCount"]
        == next(item["count"] for item in task["eventIdCounts"] if item["eventId"] == 201),
        "mlvNamedTaskObservation: zero-result action completions must equal event-201 count",
    )
    checks.require(
        document["auditorObservation"]["reportedPopulation"] == 0
        and document["auditorObservation"]["reportedRegistryEntries"] == 41
        and document["auditorObservation"]["populationUsableAsCurrentTaskCountProof"] is False,
        "auditorObservation: access-denied 0-versus-41 mismatch may not become proof",
    )
    checks.require(
        all(item["eventCount"] == 0 and item["absenceProof"] is False for item in PATTERN_OBSERVATIONS),
        "explicit target patterns: zero bounded events may not become absence proof",
    )
    checks.require(
        canonical_document_sha256(document) == CANONICAL_DOCUMENT_SHA256,
        "document: canonical SHA-256 mismatch",
    )
    return checks.finish()


def _git(*args: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=not binary,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace") if binary else result.stderr
        raise ValidationError(f"git {' '.join(args)} failed ({result.returncode}): {stderr.strip()}")
    return result.stdout


def validate_git_envelope() -> int:
    checks = Checks()
    parents = str(_git("rev-list", "--parents", "-n", "1", "HEAD")).strip().split()
    checks.require(len(parents) == 2, "git envelope: candidate must have exactly one parent")
    if len(parents) == 2:
        checks.require(parents[1] == BASE_COMMIT, "git envelope: sole parent is not exact R35")
    checks.require(str(_git("rev-parse", f"{BASE_COMMIT}^{{tree}}")).strip() == BASE_TREE, "git envelope: R35 tree drifted")
    checks.require(str(_git("rev-parse", f"{BASE_COMMIT}^")).strip() == BASE_PARENT, "git envelope: R35 parent drifted")

    diff_lines = [
        line.split("\t", 1)
        for line in str(_git("diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD")).splitlines()
        if line.strip()
    ]
    expected_diff = [
        ["A", ARTIFACT_REPO_PATH],
        ["A", HOSTILES_REPO_PATH],
        ["A", VERIFIER_REPO_PATH],
    ]
    checks.exact(diff_lines, expected_diff, "git envelope.diff")
    checks.require(str(_git("status", "--porcelain=v1")) == "", "git envelope: worktree must be clean")

    base_blob_oid = str(_git("rev-parse", f"{BASE_COMMIT}:{BASE_ARTIFACT_PATH}")).strip()
    checks.require(base_blob_oid == BASE_ARTIFACT_BLOB, "git envelope: R35 artifact blob drifted")
    base_blob = _git("show", f"{BASE_COMMIT}:{BASE_ARTIFACT_PATH}", binary=True)
    assert isinstance(base_blob, bytes)
    checks.require(len(base_blob) == BASE_ARTIFACT_BYTES, "git envelope: R35 artifact bytes drifted")
    checks.require(
        hashlib.sha256(base_blob).hexdigest() == BASE_ARTIFACT_SHA256,
        "git envelope: R35 artifact SHA-256 drifted",
    )
    return checks.finish()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-envelope", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = parse_document_bytes(ARTIFACT_PATH.read_bytes())
        document_checks = validate_document(document)
        git_checks = validate_git_envelope() if args.git_envelope else 0
    except (OSError, ValidationError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print(
        "PASS "
        f"document_checks={document_checks} git_checks={git_checks} "
        "status=POINT_IN_TIME_OBSERVATION_ZERO_AUTHORITY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
