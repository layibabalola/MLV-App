#!/usr/bin/env python3
"""Verify the exact R37 MLV task-definition reconciliation candidate."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT = ROOT / "docs" / "universal-token-control-task-definition-enumeration-candidate-2026-08-21.json"
ARTIFACT_BYTES = 15298
ARTIFACT_SHA256 = "F9E6F450461509BBF9F05200CFA73D499FF569C0CD8389261F0D4BE402D6E159"
ARTIFACT_SEMANTIC_BYTES = 12043
ARTIFACT_SEMANTIC_SHA256 = "BF6AB7D9D49FF9F0222FC375F4E5504407B9589E46821025C60F6BBD4EC7BD87"
BASE_COMMIT = "06ea3ea5eb1329f59d2e9fdf474485c30b0cc651"
BASE_TREE = "4b903e38de6d5572bcbc2387ef4031ecb4e0ad9a"
BASE_PARENT = "38ed4bbf9068cac80140297c74facdf522577704"
SHA_RE = re.compile(r"^[0-9A-F]{64}$")
OID_RE = re.compile(r"^[0-9a-f]{40}$")

TOP_KEYS = {
    "schema", "status", "projectId", "baseCandidate", "machineInputs",
    "observationBoundary", "operationalLogBoundary", "relevantDefinitions",
    "r35EdgeReconciliation", "closedFinding", "uncreditedProofs", "authority",
    "nextLawfulStep",
}
AUTH_KEYS = {
    "projectAdoption", "fleetAdoption", "dispositionCredit", "installation",
    "runtimeActivation", "providerInvocation", "authenticationAction",
    "processSpawnResumeKill", "schedulerOrTaskMutation", "automationMutation",
    "launcherMutation", "automaticGateMutation", "canaryCredit",
    "mergeLandingPushRelease", "qualityCredit", "functionalityCredit",
    "completenessCredit", "absenceCredit",
}
EXPECTED_TASK_IDS = [
    "\\MlvGpuParityAgent",
    "\\MlvGpuParityAgentOnLogon",
    "\\MlvGpuProfileAgent",
    "\\MlvGpuProfileAgentV2",
    "\\MlvGpuProfileAgentWatchdog",
    "\\MlvGpuProfileAgentWatchdogV2",
]
EXPECTED_DEFINITIONS = {
    "\\MlvGpuParityAgent": (1303, "91833005A0AFFF7A5614067FC8539762C2F5F7101908524DC71DA5F2FC03EAD2"),
    "\\MlvGpuParityAgentOnLogon": (1282, "47C18DE51283142281132A24DD04ABD2A249323361E8EFCCE79738557BFA67A5"),
    "\\MlvGpuProfileAgent": (1356, "F063CF17A657ABD9FF90E31CC8722F2C78FE02BFEA65E08A5FB0136BF2C27A40"),
    "\\MlvGpuProfileAgentV2": (1595, "4329E7BD0225D4FA0696D6A8409FD17863FD1CD771A4D08221758D56727F9E93"),
    "\\MlvGpuProfileAgentWatchdog": (1442, "04CBF66D76A308BB61AEA423D465266AD14997866149DD8BDF1C0749A45664C0"),
    "\\MlvGpuProfileAgentWatchdogV2": (1934, "139EEE446D29FFF021A96495F12B35F3ACB3906F7A4C96255AF8C49129710979"),
}


class VerificationError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    folded: set[str] = set()
    for key, value in pairs:
        if key in result or key.casefold() in folded:
            fail(f"duplicate or case-colliding JSON key: {key}")
        result[key] = value
        folded.add(key.casefold())
    return result


def load_strict(path: pathlib.Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        fail(f"artifact is not strict UTF-8: {exc}")
    try:
        value = json.loads(text, object_pairs_hook=strict_pairs)
    except (json.JSONDecodeError, VerificationError) as exc:
        fail(f"artifact JSON invalid: {exc}")
    if type(value) is not dict:
        fail("artifact root must be an object")
    return raw, value


def exact_keys(value: Any, keys: set[str], label: str) -> None:
    if type(value) is not dict or set(value) != keys:
        fail(f"{label} keys are not exact")


def native_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        fail(f"{label} must be a native boolean")
    return value


def native_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        fail(f"{label} must be a native integer >= {minimum}")
    return value


def native_string(value: Any, label: str, *, nonblank: bool = True) -> str:
    if type(value) is not str or (nonblank and not value.strip()):
        fail(f"{label} must be a native string")
    return value


def sha(value: Any, label: str) -> str:
    value = native_string(value, label)
    if not SHA_RE.fullmatch(value):
        fail(f"{label} must be uppercase SHA-256")
    return value


def oid(value: Any, label: str) -> str:
    value = native_string(value, label)
    if not OID_RE.fullmatch(value):
        fail(f"{label} must be a lowercase Git object id")
    return value


def clean_git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def git(*args: str, binary: bool = False) -> bytes | str:
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args],
        cwd=ROOT,
        env=clean_git_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        fail(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout if binary else proc.stdout.decode("ascii", errors="strict").strip()


def verify_base_artifact(row: Any, expected: dict[str, Any], label: str) -> None:
    exact_keys(row, {"path", "gitBlobOid", "bytes", "sha256"}, label)
    if row != expected:
        fail(f"{label} tuple drift")
    blob = git("cat-file", "blob", f"{BASE_COMMIT}:{row['path']}", binary=True)
    assert isinstance(blob, bytes)
    if len(blob) != row["bytes"] or hashlib.sha256(blob).hexdigest().upper() != row["sha256"]:
        fail(f"{label} Git bytes drift")
    if git("rev-parse", f"{BASE_COMMIT}:{row['path']}") != row["gitBlobOid"]:
        fail(f"{label} Git object drift")


def verify_document(raw: bytes, doc: dict[str, Any], enforce_raw_pin: bool) -> None:
    if enforce_raw_pin:
        if len(raw) != ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest().upper() != ARTIFACT_SHA256:
            fail("artifact raw bytes are not the frozen tuple")
    semantic = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(semantic) != ARTIFACT_SEMANTIC_BYTES or hashlib.sha256(semantic).hexdigest().upper() != ARTIFACT_SEMANTIC_SHA256:
        fail("artifact semantic tuple drift")
    exact_keys(doc, TOP_KEYS, "root")
    if doc["schema"] != "mlv-app-universal-token-control-task-definition-enumeration-candidate/v1":
        fail("schema mismatch")
    if doc["status"] != "POINT_IN_TIME_TASK_DEFINITION_RECONCILIATION_ZERO_AUTHORITY":
        fail("status mismatch")
    if doc["projectId"] != "mlv-app":
        fail("projectId mismatch")

    base = doc["baseCandidate"]
    exact_keys(base, {"commit", "tree", "parent", "r35Artifact", "r36Artifact"}, "baseCandidate")
    if (base["commit"], base["tree"], base["parent"]) != (BASE_COMMIT, BASE_TREE, BASE_PARENT):
        fail("base candidate tuple mismatch")
    verify_base_artifact(base["r35Artifact"], {
        "path": "docs/universal-token-control-launcher-action-graph-census-candidate-2026-08-20.json",
        "gitBlobOid": "ba0e476429dbf0aad33ea2b96b76986f394673de",
        "bytes": 21007,
        "sha256": "52AEF218AC9657F381F9166675722A2083FE525D1B09F4FFB03FDF96F5D4A506",
    }, "r35Artifact")
    verify_base_artifact(base["r36Artifact"], {
        "path": "docs/universal-token-control-machine-observation-candidate-2026-08-20.json",
        "gitBlobOid": "ce8fbe6260ef3e491e4916f6b2e8a50a4a0ccae5",
        "bytes": 10262,
        "sha256": "A597694E7121C72100654E5085DFEC789290C24DB76E797D7BEAA88F40BF45B3",
    }, "r36Artifact")
    if git("rev-parse", f"{BASE_COMMIT}^{{tree}}") != BASE_TREE or git("rev-parse", f"{BASE_COMMIT}^") != BASE_PARENT:
        fail("base Git lineage mismatch")

    inputs = doc["machineInputs"]
    if type(inputs) is not list or len(inputs) != 2:
        fail("machineInputs must contain exactly two rows")
    for index, row in enumerate(inputs):
        exact_keys(row, {"path", "bytes", "sha256", "role"}, f"machineInputs[{index}]")
        path = pathlib.Path(native_string(row["path"], f"machineInputs[{index}].path"))
        if not path.is_absolute() or not path.is_file():
            fail(f"machineInputs[{index}] path is not an existing absolute file")
        body = path.read_bytes()
        if len(body) != native_int(row["bytes"], f"machineInputs[{index}].bytes"):
            fail(f"machineInputs[{index}] byte length drift")
        if hashlib.sha256(body).hexdigest().upper() != sha(row["sha256"], f"machineInputs[{index}].sha256"):
            fail(f"machineInputs[{index}] hash drift")
        native_string(row["role"], f"machineInputs[{index}].role")

    boundary = doc["observationBoundary"]
    exact_keys(boundary, {
        "capturedUtc", "machine", "query", "relevantSelector", "nonMicrosoftTaskCount",
        "readableDefinitionCount", "unreadableDefinitionCount", "definitionTuplePreimage",
        "definitionTuplePreimageBytes", "definitionTupleDigest", "relevantDefinitionCount",
        "taskDefinitionInventoryCompleteForQuery", "launcherInventoryComplete",
        "actionGraphComplete", "semanticCompletenessClaimed", "pointInTimeOnly",
    }, "observationBoundary")
    for name in ("capturedUtc", "machine", "query", "relevantSelector", "definitionTuplePreimage"):
        native_string(boundary[name], f"observationBoundary.{name}")
    count_names = ("nonMicrosoftTaskCount", "readableDefinitionCount", "unreadableDefinitionCount", "definitionTuplePreimageBytes", "relevantDefinitionCount")
    count_values = [native_int(boundary[k], f"observationBoundary.{k}") for k in count_names]
    if count_values != [41, 41, 0, 4129, 6]:
        fail("observation counts drift")
    sha(boundary["definitionTupleDigest"], "observationBoundary.definitionTupleDigest")
    if boundary["definitionTupleDigest"] != "22A5615946247FC65FF031FE3463BD3195C91160DB074C2BCD779EDD5F4B7ECE":
        fail("definition tuple digest drift")
    if native_bool(boundary["taskDefinitionInventoryCompleteForQuery"], "taskDefinitionInventoryCompleteForQuery") is not True:
        fail("query completeness must be true")
    for name in ("launcherInventoryComplete", "actionGraphComplete", "semanticCompletenessClaimed"):
        if native_bool(boundary[name], f"observationBoundary.{name}") is not False:
            fail(f"{name} must remain false")
    if native_bool(boundary["pointInTimeOnly"], "observationBoundary.pointInTimeOnly") is not True:
        fail("pointInTimeOnly must remain true")

    log = doc["operationalLogBoundary"]
    exact_keys(log, {"channel", "totalEvents", "firstRecordId", "lastRecordId", "firstUtc", "lastUtc", "rollingBuffer", "outsideWindowAbsenceProven"}, "operationalLogBoundary")
    for name in ("totalEvents", "firstRecordId", "lastRecordId"):
        native_int(log[name], f"operationalLogBoundary.{name}")
    if native_bool(log["rollingBuffer"], "rollingBuffer") is not True or native_bool(log["outsideWindowAbsenceProven"], "outsideWindowAbsenceProven") is not False:
        fail("operational log boundary overclaim")

    rows = doc["relevantDefinitions"]
    if type(rows) is not list or [row.get("taskId") for row in rows if type(row) is dict] != EXPECTED_TASK_IDS:
        fail("relevantDefinitions exact ordered task set drift")
    for index, row in enumerate(rows):
        exact_keys(row, {"taskId", "definition", "state", "enabled", "lastTaskResult", "missedRuns", "actions", "triggers", "history", "reconciliation"}, f"relevantDefinitions[{index}]")
        task_id = native_string(row["taskId"], f"taskId[{index}]")
        exact_keys(row["definition"], {"bytes", "sha256", "readOutcome"}, f"definition[{index}]")
        expected_bytes, expected_sha = EXPECTED_DEFINITIONS[task_id]
        if row["definition"] != {"bytes": expected_bytes, "sha256": expected_sha, "readOutcome": "READ_EXPORTED_HASHED"}:
            fail(f"definition tuple drift for {task_id}")
        native_string(row["state"], f"state[{index}]")
        native_bool(row["enabled"], f"enabled[{index}]")
        native_int(row["lastTaskResult"], f"lastTaskResult[{index}]")
        native_int(row["missedRuns"], f"missedRuns[{index}]")
        if type(row["actions"]) is not list or not row["actions"] or type(row["triggers"]) is not list or not row["triggers"]:
            fail(f"actions/triggers missing for {task_id}")
        exact_keys(row["history"], {"eventCount", "eventIdCounts", "event201ResultCodeZeroCount", "event201NonzeroOrMissingResultCount", "firstRecordId", "lastRecordId", "firstUtc", "lastUtc"}, f"history[{index}]")
        native_int(row["history"]["eventCount"], f"history[{index}].eventCount")
        native_string(row["reconciliation"], f"reconciliation[{index}]")

    reconciliations = doc["r35EdgeReconciliation"]
    if type(reconciliations) is not list or len(reconciliations) != 4:
        fail("r35EdgeReconciliation must contain four exact classes")
    for index, row in enumerate(reconciliations):
        exact_keys(row, {"edgeIds", "reportedTask", "exactDefinitionMatchCount", "substringDefinitionMatchCount", "outcome", "absenceScope", "globalAbsenceCredit"}, f"r35EdgeReconciliation[{index}]")
        if native_bool(row["globalAbsenceCredit"], f"globalAbsenceCredit[{index}]") is not False:
            fail("global absence credit must remain false")

    finding = doc["closedFinding"]
    exact_keys(finding, {"finding", "currentWindowsTaskDefinitionGapClosed", "currentWindowsTaskHistoryGapClosedForRelevantDefinitions", "mlvAppUniversalControlInstalled", "currentClosedGateProofCredited", "projectOwnerDispositionPublished", "adoptionCredit"}, "closedFinding")
    for name in ("currentWindowsTaskDefinitionGapClosed", "currentWindowsTaskHistoryGapClosedForRelevantDefinitions"):
        if native_bool(finding[name], f"closedFinding.{name}") is not True:
            fail(f"{name} must be true")
    for name in ("mlvAppUniversalControlInstalled", "currentClosedGateProofCredited", "projectOwnerDispositionPublished", "adoptionCredit"):
        if native_bool(finding[name], f"closedFinding.{name}") is not False:
            fail(f"{name} must remain false")

    if type(doc["uncreditedProofs"]) is not list or len(doc["uncreditedProofs"]) != 15 or any(type(v) is not str for v in doc["uncreditedProofs"]):
        fail("uncreditedProofs exact native array drift")
    authority = doc["authority"]
    exact_keys(authority, AUTH_KEYS, "authority")
    for key, value in authority.items():
        if native_bool(value, f"authority.{key}") is not False:
            fail(f"authority.{key} must remain false")
    next_step = doc["nextLawfulStep"]
    exact_keys(next_step, {"kind", "scope", "requiredOutput", "forbiddenActions"}, "nextLawfulStep")
    for name in ("kind", "scope", "requiredOutput"):
        native_string(next_step[name], f"nextLawfulStep.{name}")
    if type(next_step["forbiddenActions"]) is not list or len(next_step["forbiddenActions"]) != 6 or any(type(v) is not str for v in next_step["forbiddenActions"]):
        fail("forbiddenActions exact array drift")


LIVE_SCRIPT = r'''$ErrorActionPreference='Stop'
$utf8=[Text.UTF8Encoding]::new($false)
$all=@()
foreach($t in Get-ScheduledTask | Where-Object {$_.TaskPath -notlike '\Microsoft\*'}){
  $xml=Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath
  $b=$utf8.GetBytes($xml)
  $sha=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($b))
  $actions=@($t.Actions | ForEach-Object {"$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)"}) -join "`n"
  $all += [pscustomobject]@{id="$($t.TaskPath)$($t.TaskName)";bytes=[int64]$b.Length;sha256=$sha;hay="$($t.TaskPath)$($t.TaskName)`n$actions`n$xml"}
}
$lines=[string[]]@($all|ForEach-Object{"$($_.id)`t$($_.bytes)`t$($_.sha256)"})
[Array]::Sort($lines,[StringComparer]::Ordinal)
$preimage=[string]::Join("`n",$lines)
$matched=@($all|Where-Object{$_.hay -match '(?i)(mlv|agent-bridge|G:\\Temp\\mlv-gpu-profile)'}|ForEach-Object{[ordered]@{id=$_.id;bytes=$_.bytes;sha256=$_.sha256}})
[ordered]@{count=[int64]$all.Count;preimageBytes=[int64]$utf8.GetByteCount($preimage);digest=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($utf8.GetBytes($preimage)));matched=$matched}|ConvertTo-Json -Compress -Depth 5
'''


def verify_live() -> None:
    runtime = pathlib.Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
    if not runtime.is_file():
        fail("pinned PowerShell runtime unavailable for live read-only verification")
    encoded = base64.b64encode(LIVE_SCRIPT.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run(
        [str(runtime), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        fail(f"live read-only task enumeration failed: {proc.stderr.decode(errors='replace').strip()}")
    try:
        live = json.loads(proc.stdout.decode("utf-8", errors="strict"), object_pairs_hook=strict_pairs)
    except Exception as exc:
        fail(f"live enumeration JSON invalid: {exc}")
    if live.get("count") != 41 or live.get("preimageBytes") != 4129 or live.get("digest") != "22A5615946247FC65FF031FE3463BD3195C91160DB074C2BCD779EDD5F4B7ECE":
        fail("live complete task-definition tuple set drift")
    expected = [{"id": task_id, "bytes": values[0], "sha256": values[1]} for task_id, values in EXPECTED_DEFINITIONS.items()]
    if live.get("matched") != expected:
        fail("live relevant task-definition set drift")


def verify_git_envelope() -> None:
    if git("rev-parse", "HEAD^") != BASE_COMMIT:
        fail("candidate HEAD sole parent is not the exact R36 base")
    parents = str(git("show", "-s", "--format=%P", "HEAD")).split()
    if parents != [BASE_COMMIT]:
        fail("candidate HEAD does not have exactly one parent")
    expected = [
        "A\tdocs/universal-token-control-task-definition-enumeration-candidate-2026-08-21.json",
        "A\ttools/universal_token_control_task_definition_enumeration_candidate_hostile_tests.py",
        "A\ttools/verify_universal_token_control_task_definition_enumeration_candidate.py",
    ]
    changed = str(git("diff", "--name-status", BASE_COMMIT, "HEAD")).splitlines()
    if changed != expected:
        fail(f"candidate Git delta is not the exact three additions: {changed}")
    if str(git("status", "--porcelain")):
        fail("candidate worktree is not clean")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=pathlib.Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--allow-mutated-fixture", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--git-envelope", action="store_true")
    args = parser.parse_args()
    try:
        raw, doc = load_strict(args.artifact.resolve())
        verify_document(raw, doc, not args.allow_mutated_fixture)
        if args.live:
            verify_live()
        if args.git_envelope:
            verify_git_envelope()
    except (OSError, VerificationError) as exc:
        print(f"TASK_DEFINITION_CANDIDATE_INVALID: {exc}", file=sys.stderr)
        return 1
    suffix = (" LIVE_EXACT" if args.live else "") + (" GIT_EXACT" if args.git_envelope else "")
    print(f"TASK_DEFINITION_CANDIDATE_VALID{suffix}: definitions=41 relevant=6 authority=ZERO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
