#!/usr/bin/env python3
"""Hostile mutation tests for the MLV-App machine-observation candidate."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Callable

from verify_universal_token_control_machine_observation_candidate import (
    ARTIFACT_PATH,
    AUTHORITY_KEYS,
    ValidationError,
    parse_document_bytes,
    validate_document,
)


class TestFailure(AssertionError):
    pass


def _at(document: Any, path: tuple[str | int, ...]) -> Any:
    current = document
    for component in path:
        current = current[component]
    return current


def _replace(
    baseline: dict[str, Any],
    path: tuple[str | int, ...],
    value: Any,
) -> dict[str, Any]:
    mutated = copy.deepcopy(baseline)
    parent = _at(mutated, path[:-1])
    parent[path[-1]] = value
    return mutated


def _delete(
    baseline: dict[str, Any],
    path: tuple[str | int, ...],
) -> dict[str, Any]:
    mutated = copy.deepcopy(baseline)
    parent = _at(mutated, path[:-1])
    del parent[path[-1]]
    return mutated


def _append(
    baseline: dict[str, Any],
    path: tuple[str | int, ...],
    value: Any,
) -> dict[str, Any]:
    mutated = copy.deepcopy(baseline)
    target = _at(mutated, path)
    target.append(value)
    return mutated


def _expect_document_reject(name: str, document: dict[str, Any]) -> None:
    try:
        validate_document(document)
    except ValidationError:
        return
    raise TestFailure(f"hostile document accepted: {name}")


def _expect_bytes_reject(name: str, raw: bytes) -> None:
    try:
        document = parse_document_bytes(raw)
        validate_document(document)
    except ValidationError:
        return
    raise TestFailure(f"hostile JSON bytes accepted: {name}")


def main() -> int:
    baseline_raw = ARTIFACT_PATH.read_bytes()
    baseline = parse_document_bytes(baseline_raw)
    baseline_checks = validate_document(baseline)
    cases: list[tuple[str, dict[str, Any]]] = []

    cases.extend(
        [
            ("root-extra", {**copy.deepcopy(baseline), "unexpected": False}),
            ("root-missing", _delete(baseline, ("creditDenials",))),
            ("root-list", [copy.deepcopy(baseline)]),
            ("schema-drift", _replace(baseline, ("schema",), "v2")),
            ("status-authority", _replace(baseline, ("status",), "ADOPT")),
            ("base-commit", _replace(baseline, ("baseCandidate", "commit"), "0" * 40)),
            ("base-tree", _replace(baseline, ("baseCandidate", "tree"), "0" * 40)),
            ("base-artifact-bytes-bool", _replace(baseline, ("baseCandidate", "artifactBytes"), True)),
            ("base-artifact-bytes-float", _replace(baseline, ("baseCandidate", "artifactBytes"), 21007.0)),
            ("base-adoption-transfer", _replace(baseline, ("baseCandidate", "adoptionTransferred"), True)),
            ("base-authority-transfer", _replace(baseline, ("baseCandidate", "authorityTransferred"), True)),
            ("boundary-extra", _replace(baseline, ("observationBoundary",), {**baseline["observationBoundary"], "extra": False})),
            ("cutoff-int-as-string", _replace(baseline, ("observationBoundary", "eventLogCutoffRecordId"), "1774790")),
            ("cutoff-int-as-bool", _replace(baseline, ("observationBoundary", "eventLogCutoffRecordId"), True)),
            ("definitions-closed", _replace(baseline, ("observationBoundary", "currentTaskDefinitionsClosed"), True)),
            ("inventory-complete", _replace(baseline, ("observationBoundary", "scheduledTaskInventoryComplete"), True)),
            ("launcher-complete", _replace(baseline, ("observationBoundary", "launcherInventoryComplete"), True)),
            ("graph-complete", _replace(baseline, ("observationBoundary", "actionGraphComplete"), True)),
            ("history-complete", _replace(baseline, ("observationBoundary", "eventLogWindowCompleteHistory"), True)),
            ("semantic-complete", _replace(baseline, ("observationBoundary", "semanticCompletenessClaimed"), True)),
            ("provider-invoked", _replace(baseline, ("observationBoundary", "providerOrAuthInvoked"), True)),
            ("task-action", _replace(baseline, ("observationBoundary", "processOrTaskActionPerformed"), True)),
            ("machine-mutated", _replace(baseline, ("observationBoundary", "machineFilesMutated"), True)),
            ("machine-input-remove", _delete(baseline, ("machineInputs", 1))),
            ("machine-input-add", _append(baseline, ("machineInputs",), copy.deepcopy(baseline["machineInputs"][0]))),
            ("machine-input-reorder", _replace(baseline, ("machineInputs",), list(reversed(baseline["machineInputs"])))),
            ("registry-size-bool", _replace(baseline, ("machineInputs", 0, "bytes"), True)),
            ("registry-size-one", _replace(baseline, ("machineInputs", 0, "bytes"), 1)),
            ("registry-sha", _replace(baseline, ("machineInputs", 0, "sha256"), "sha256:" + "0" * 64)),
            ("registry-mutated", _replace(baseline, ("machineInputs", 0, "mutated"), True)),
            ("auditor-size", _replace(baseline, ("machineInputs", 1, "bytes"), 22495)),
            ("auditor-sha", _replace(baseline, ("machineInputs", 1, "sha256"), "sha256:" + "f" * 64)),
            ("auditor-success", _replace(baseline, ("auditorObservation", "enumerationResult"), "SUCCESS")),
            ("auditor-population-41", _replace(baseline, ("auditorObservation", "reportedPopulation"), 41)),
            ("auditor-population-bool", _replace(baseline, ("auditorObservation", "reportedPopulation"), False)),
            ("auditor-population-usable", _replace(baseline, ("auditorObservation", "populationUsableAsCurrentTaskCountProof"), True)),
            ("auditor-zero-means-zero", _replace(baseline, ("auditorObservation", "zeroPopulationMeansZeroTasks"), True)),
            ("auditor-definitions-closed", _replace(baseline, ("auditorObservation", "currentTaskDefinitionsClosed"), True)),
            ("log-selection-widened", _replace(baseline, ("taskSchedulerOperationalSlice", "selection"), "RecordId >= 0")),
            ("log-first-id-bool", _replace(baseline, ("taskSchedulerOperationalSlice", "firstRecordId"), True)),
            ("log-last-id-plus-one", _replace(baseline, ("taskSchedulerOperationalSlice", "lastRecordId"), 1774791)),
            ("coverage-hours-int", _replace(baseline, ("taskSchedulerOperationalSlice", "coverageHours"), 56)),
            ("coverage-hours-rounded", _replace(baseline, ("taskSchedulerOperationalSlice", "coverageHours"), 56.8)),
            ("total-events-string", _replace(baseline, ("taskSchedulerOperationalSlice", "totalEvents"), "18967")),
            ("before-window-absence", _replace(baseline, ("taskSchedulerOperationalSlice", "beforeWindowAbsenceProven"), True)),
            ("after-cutoff-absence", _replace(baseline, ("taskSchedulerOperationalSlice", "afterCutoffAbsenceProven"), True)),
            ("task-definition-evidence", _replace(baseline, ("taskSchedulerOperationalSlice", "taskDefinitionEvidence"), True)),
            ("distinct-task-add", _append(baseline, ("mlvNamedTaskObservation", "distinctTaskNames"), "\\MLV-LaneIgnitionWatchdog")),
            ("mlv-event-count", _replace(baseline, ("mlvNamedTaskObservation", "eventCount"), 4098)),
            ("mlv-task-add", _append(baseline, ("mlvNamedTaskObservation", "tasks"), copy.deepcopy(baseline["mlvNamedTaskObservation"]["tasks"][0]))),
            ("event-id-count-reorder", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "eventIdCounts"), list(reversed(baseline["mlvNamedTaskObservation"]["tasks"][0]["eventIdCounts"])))),
            ("event-id-count-bool", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "eventIdCounts", 0, "count"), True)),
            ("event-id-count-683", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "eventIdCounts", 5, "count"), 683)),
            ("action-powershell", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "actionExecutables", 0), "pwsh.exe")),
            ("action-add", _append(baseline, ("mlvNamedTaskObservation", "tasks", 0, "actionExecutables"), "powershell.exe")),
            ("zero-completion-683", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "event201ResultCodeZeroCount"), 683)),
            ("nonzero-completion-one", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "event201NonzeroOrMissingResultCount"), 1)),
            ("definition-read", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "taskDefinitionRead"), True)),
            ("owner-inferred", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "definitionOwnerInferredFromLog"), True)),
            ("mlv-app-target", _replace(baseline, ("mlvNamedTaskObservation", "tasks", 0, "mlvAppTarget"), True)),
            ("patterns-remove", _delete(baseline, ("explicitMlvAppTargetPatternObservations", 3))),
            ("patterns-reorder", _replace(baseline, ("explicitMlvAppTargetPatternObservations",), list(reversed(baseline["explicitMlvAppTargetPatternObservations"])))),
            ("pattern-event-one", _replace(baseline, ("explicitMlvAppTargetPatternObservations", 0, "eventCount"), 1)),
            ("pattern-event-bool", _replace(baseline, ("explicitMlvAppTargetPatternObservations", 0, "eventCount"), False)),
            ("pattern-absence-proof", _replace(baseline, ("explicitMlvAppTargetPatternObservations", 0, "absenceProof"), True)),
            ("classification-current-proof", _replace(baseline, ("separateProjectClassification", "classificationIsCurrentDefinitionProof"), True)),
            ("classification-transfer", _replace(baseline, ("separateProjectClassification", "classificationTransfersToMlvApp"), True)),
            ("classification-owner", _replace(baseline, ("separateProjectClassification", "ownerProject"), "MLV_APP")),
            ("credit-denial-remove", _delete(baseline, ("creditDenials", 0))),
            ("credit-denial-reorder", _replace(baseline, ("creditDenials",), list(reversed(baseline["creditDenials"])))),
            ("credit-granted", _replace(baseline, ("creditDenials", 0, "credited"), True)),
            ("credit-bool-as-int", _replace(baseline, ("creditDenials", 0, "credited"), 0)),
            ("uncredited-remove", _delete(baseline, ("uncreditedProofs", 0))),
            ("uncredited-add", _append(baseline, ("uncreditedProofs",), "UNKNOWN")),
            ("self-review", _replace(baseline, ("reviewBoundary", "authorMaySelfReview"), True)),
            ("review-received", _replace(baseline, ("reviewBoundary", "freshIndependentReviewReceived"), True)),
            ("adjudication-received", _replace(baseline, ("reviewBoundary", "distinctAdjudicationReceived"), True)),
            ("review-transfer", _replace(baseline, ("reviewBoundary", "priorReviewTransferAllowed"), True)),
            ("next-step-kind", _replace(baseline, ("nextLawfulStep", "kind"), "INSTALL")),
            ("next-step-scope", _replace(baseline, ("nextLawfulStep", "scope"), "apply now")),
            ("forbidden-remove", _delete(baseline, ("nextLawfulStep", "forbiddenActions", 0))),
            ("forbidden-reorder", _replace(baseline, ("nextLawfulStep", "forbiddenActions"), list(reversed(baseline["nextLawfulStep"]["forbiddenActions"])))),
            ("opaque-canonical-drift", _replace(baseline, ("auditorObservation", "conclusion"), baseline["auditorObservation"]["conclusion"] + "_DRIFT")),
        ]
    )

    for key in sorted(AUTHORITY_KEYS):
        cases.append(
            (
                f"authority-true-{key}",
                _replace(baseline, ("authority", key), True),
            )
        )
        cases.append(
            (
                f"authority-int-{key}",
                _replace(baseline, ("authority", key), 0),
            )
        )
    cases.append(("authority-extra", _replace(baseline, ("authority",), {**baseline["authority"], "apply": False})))
    cases.append(("authority-missing", _delete(baseline, ("authority", "installation"))))

    for index in range(len(baseline["creditDenials"])):
        cases.append(
            (
                f"credit-denial-true-{index}",
                _replace(baseline, ("creditDenials", index, "credited"), True),
            )
        )

    for index in range(len(baseline["explicitMlvAppTargetPatternObservations"])):
        cases.append(
            (
                f"pattern-absence-true-{index}",
                _replace(
                    baseline,
                    ("explicitMlvAppTargetPatternObservations", index, "absenceProof"),
                    True,
                ),
            )
        )

    for name, document in cases:
        if type(document) is not dict:
            try:
                validate_document(document)
            except ValidationError:
                continue
            raise TestFailure(f"hostile document accepted: {name}")
        _expect_document_reject(name, document)

    byte_cases: list[tuple[str, bytes]] = [
        ("duplicate-root-key", baseline_raw.replace(b'"schema":', b'"schema": "duplicate", "schema":', 1)),
        ("nan", baseline_raw.replace(b"56.9", b"NaN", 1)),
        ("infinity", baseline_raw.replace(b"56.9", b"Infinity", 1)),
        ("invalid-utf8", b"\xff" + baseline_raw),
        ("trailing-garbage", baseline_raw + b"\n{}"),
        ("json-array-root", b"[]"),
    ]
    for name, raw in byte_cases:
        _expect_bytes_reject(name, raw)

    print(
        "PASS "
        f"baseline_checks={baseline_checks} hostile_document_cases={len(cases)} "
        f"hostile_byte_cases={len(byte_cases)} total_hostiles={len(cases) + len(byte_cases)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValidationError, TestFailure) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1)
