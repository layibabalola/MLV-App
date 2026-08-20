#!/usr/bin/env python3
"""Hostile verifier for MLV-App's inert launcher/action-graph census candidate."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_REPO_PATH = "docs/universal-token-control-launcher-action-graph-census-candidate-2026-08-20.json"
TEST_REPO_PATH = "tools/universal-token-control-launcher-action-graph-census-candidate.tests.py"
ARTIFACT_PATH = ROOT / ARTIFACT_REPO_PATH
SCHEMA = "mlv-app-launcher-action-graph-census-candidate/v1"
STATUS = "CANDIDATE_ZERO_AUTHORITY"
BASE = "30889f77e2000190b94d59f80f6a03b12ce3e0d3"
BASE_TREE = "d82ca4fdb9c2f45eb4bf169b4f5edfcc9a14100a"
BASE_PARENT = "45b63b1eb1841ab216a0f5c2ff78b3938fb82f85"
ORIGIN = "https://github.com/layibabalola/MLV-App.git"
ACQUISITION_ORIGIN = (
    r"C:\code\softwarefactory-fleet-doctrine-worktrees\mlv-app-r26-disposition-candidate-20260819-01"
)
SIBLING_COMMIT = "50db64d71bf62da041b9a6872c893382cd7cc79b"
SIBLING_TREE = "ab58ec53414ff889eaa41a6689dde1141cc687ed"
SIBLING_PARENT = "709bf99307271ac7b6bb0f202495723373892871"
SIBLING_PACKET_PATH = "tools/provider_control/AUTHOR-PACKET.json"
SIBLING_PACKET_BLOB = "82d133dd954702d44d8eb414cc43632fd6c27416"
SIBLING_PACKET_SHA256 = "498015856ebbdf29be45eb9ba5f5b8a8b429abc9585b55062af9afada052407c"
SIBLING_PACKET_BYTES = 9936
R2_COMMIT = "d9aa0d0062e1aa6ec3911bf6e0ce6e203f55aab9"
R2_TREE = "8a03372c0fcacd8385e36a1a5f7ac29c964b3304"
R2_ADJUDICATION = "bc62eb0b14e1d23b95a46dc1c56ab8da2a500a63"
R2_ADJUDICATION_TREE = "7e7cd706c572e6da260a03062dcbad4cbc4c1a4b"
SHA1 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")

ROOT_KEYS = {
    "schema",
    "status",
    "projectBase",
    "candidateBoundary",
    "baseTrackedEvidence",
    "knownSurfaceFamilies",
    "knownActionEdges",
    "preservationRequirementsOnly",
    "explicitNoTransfer",
    "missingSurfaces",
    "uncreditedAdoptionProofs",
    "reviewBoundary",
    "authority",
    "nextLawfulStep",
}
CANDIDATE_DIFF = [("A", ARTIFACT_REPO_PATH), ("A", TEST_REPO_PATH)]

PROJECT_BASE = {
    "qualifiedRef": "refs/remotes/origin/master",
    "commit": BASE,
    "tree": BASE_TREE,
    "parent": BASE_PARENT,
    "canonicalOrigin": ORIGIN,
    "localAcquisitionOrigin": ACQUISITION_ORIGIN,
    "masterQualified": True,
}
CANDIDATE_BOUNDARY = {
    "kind": "PROJECT_OWNED_TRACKED_STATIC_CENSUS",
    "semanticCompletenessClaimed": False,
    "inventoryComplete": False,
    "actionGraphComplete": False,
    "installed": False,
    "dispositionPublishedByCandidate": False,
    "adoptionCredit": False,
    "automaticGateState": "CLOSED",
    "currentClosedGateProofCredited": False,
    "gateObservedByCandidate": False,
    "currentMachineStateObserved": False,
    "providerOrAuthInvoked": False,
    "processOrTaskActionPerformed": False,
}

BASE_EVIDENCE = [
    (
        "AGENTS.md",
        "4fda88533706544f6d3d2ddb8b9e4da4533b4324",
        "sha256:05f7d1293ff01b8b5ea86fbc71847c7b4229a2362cbacfea6ce8a7fb7316328d",
        61002,
        [
            "output-equivalence **proven, not asserted**",
            "A failed baseline is not a comparator.",
            "Product-card completion requires a dedicated independent reviewer",
            "handoffActor=CODEX",
            "reviewActor=CLAUDE",
        ],
    ),
    (
        "tools/agent-bridge/bootstrap_session.py",
        "9bc712a535d5e67b59a1f89d4ef9ec2bf24f5541",
        "sha256:577e2aa779f3ccf4b953a4a4475f03a715127fb3a4b69604bb574ed32fa3e705",
        43843,
        [
            'watcher_script = Path(__file__).with_name("watcher.py")',
            'command = [sys.executable, str(watcher_script), "--config", str(watcher_config)]',
            "start_new_session=True",
        ],
    ),
    (
        "tools/agent-bridge/configure_watcher.py",
        "57ecb3666c2401f57536e306f0f1e2838e81fc2d",
        "sha256:436e6a111749e0265f7de38db21b478c160e735364aa921184d94b842a4dd9f3",
        16204,
        [
            'settings.wake_provider in {"sendkeys", "targeted_sendkeys"}',
            'settings.wake_provider in {"app_server", "app_server_then_redraw"}',
            "codex_app_server_wake.py",
        ],
    ),
    (
        "tools/agent-bridge/watcher.py",
        "3a6478d31fc4e0e9d8bea6e0572b6b90a7132381",
        "sha256:4535e9cd70a7f73f38bda61457a0266135e38bc6c5774e3451d515b23ab5897d",
        115700,
        ["on_message_command_template", "proc = subprocess.run(", "list(cmd)"],
    ),
    (
        "tools/agent-bridge/codex_app_server_wake.py",
        "8cfc204ca63f0e54801a41947279f46fd50c29b8",
        "sha256:6961d853835865c23f18c94069658447fce20ab7c68ab0edf4ef34516a47be28",
        16712,
        [
            'command = [codex_exe, "app-server", "--listen", listen_url]',
            'await client.request("thread/resume"',
            '"turn/start"',
        ],
    ),
    (
        "tools/agent-bridge/wake_codex.ps1",
        "aed289dd0c740d7e4e324e52543f170b5141757c",
        "sha256:2cd1a07390939701d78fffd9920f2e65a005ed8fa5c7169df3439b1e67b60274",
        90884,
        ['$uri = "codex://threads/$Value"', "Start-Process $uri", 'Ctrl+Enter is the "Steer"'],
    ),
    (
        "tools/agent-bridge/wake_claude.ps1",
        "bfa60ee949e29ab346c5070b70334e9d779e9ecd",
        "sha256:cb204d8acde97ab07dd86f78376c9ee6794bd0f5b02e97c1429865e9073ee77b",
        2648,
        ["refuses UI injection by default", "unsupported_thread_addressable_wake", "$UnsupportedExitCode = 20"],
    ),
]

SURFACE_FAMILIES = [
    (
        "WINDOWS_TASK_MLV_LANE_IGNITION_WATCHDOG",
        "WINDOWS_SCHEDULED_TASK",
        "MLV-LaneIgnitionWatchdog",
        20,
        "Disabled",
    ),
    (
        "CODEX_DESKTOP_AUTOMATION_LIVENESS",
        "CODEX_AUTOMATION",
        "mlv-app-dual-lane-codex-liveness",
        5,
        "KNOWN_DEFINITION_UNVERIFIED_CURRENT_STATE",
    ),
    (
        "RETIRED_APP_STORE_WAKE",
        "RETIRED_APP_STORE_TASK_FAMILY",
        "APP_STORE_WAKE_FAMILY",
        0,
        "RETIRED_BY_RECEIPT_CONTEXT_RECEIPTS_NOT_IMPORTED",
    ),
    (
        "RETIRED_APP_STORE_MIRROR",
        "RETIRED_APP_STORE_TASK_FAMILY",
        "APP_STORE_MIRROR_FAMILY",
        0,
        "RETIRED_BY_RECEIPT_CONTEXT_RECEIPTS_NOT_IMPORTED",
    ),
]

EDGE_KEYS = {
    "edgeId",
    "family",
    "from",
    "to",
    "evidenceKind",
    "basePath",
    "executionObserved",
    "authorityCredited",
    "errorClass",
    "outcome",
}
EDGES = [
    ("EDGE-01", "WINDOWS_TASK", "MLV-LaneIgnitionWatchdog", "pwsh.exe", "KNOWN_EXTERNAL_UNVERIFIED", None, "CURRENT_MACHINE_SURFACE_UNREAD", "KNOWN_EDGE_ENUMERATED_NOT_CURRENT_PROOF"),
    ("EDGE-02", "WINDOWS_TASK", "pwsh.exe", "ignite-dead-lanes.ps1", "KNOWN_EXTERNAL_UNVERIFIED", None, "CURRENT_MACHINE_SURFACE_UNREAD", "KNOWN_EDGE_ENUMERATED_NOT_CURRENT_PROOF"),
    ("EDGE-03", "DIRECT_PROVIDER_LAUNCH", "ignite-dead-lanes.ps1", "claude.cmd -p", "KNOWN_EXTERNAL_UNVERIFIED", None, "PROVIDER_LAUNCH_FORBIDDEN_STATIC_CENSUS", "KNOWN_EDGE_ENUMERATED_NOT_CURRENT_PROOF"),
    ("EDGE-04", "DIRECT_PROVIDER_LAUNCH", "claude.cmd", "claude-code-executable", "KNOWN_EXTERNAL_UNVERIFIED", None, "PROVIDER_LAUNCH_FORBIDDEN_STATIC_CENSUS", "KNOWN_EDGE_ENUMERATED_NOT_CURRENT_PROOF"),
    ("EDGE-05", "CODEX_AUTOMATION", "mlv-app-dual-lane-codex-liveness", "Codex Desktop automation executor", "KNOWN_EXTERNAL_UNVERIFIED", None, "AUTOMATION_EXECUTOR_INTERNAL_CHAIN_UNREAD", "KNOWN_EDGE_ENUMERATED_NOT_CURRENT_PROOF"),
    ("EDGE-06", "CODEX_AUTOMATION", "Codex Desktop automation executor", "codex exec revival", "KNOWN_EXTERNAL_UNVERIFIED", None, "AUTOMATION_EXECUTOR_INTERNAL_CHAIN_UNREAD", "KNOWN_EDGE_ENUMERATED_NOT_CURRENT_PROOF"),
    ("EDGE-07", "RETIRED_APP_STORE", "app scheduler registry", "app-store wake family", "DOCTRINE_CONTEXT_UNCREDITED", None, "RETIRED_FAMILY_RECEIPTS_NOT_IMPORTED", "KNOWN_RETIRED_FAMILY_ENUMERATED_UNCREDITED"),
    ("EDGE-08", "RETIRED_APP_STORE", "app scheduler registry", "app-store mirror family", "DOCTRINE_CONTEXT_UNCREDITED", None, "RETIRED_FAMILY_RECEIPTS_NOT_IMPORTED", "KNOWN_RETIRED_FAMILY_ENUMERATED_UNCREDITED"),
    ("EDGE-09", "TRACKED_WATCHER", "tools/agent-bridge/bootstrap_session.py", "tools/agent-bridge/watcher.py", "TRACKED_BASE_EXACT", "tools/agent-bridge/bootstrap_session.py", "NOT_EXECUTED_STATIC_ONLY", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-10", "TRACKED_WATCHER", "tools/agent-bridge/configure_watcher.py", "watcher on_message_command_template", "TRACKED_BASE_EXACT", "tools/agent-bridge/configure_watcher.py", "NOT_EXECUTED_STATIC_ONLY", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-11", "TRACKED_WATCHER", "tools/agent-bridge/watcher.py", "configured argv", "TRACKED_BASE_EXACT", "tools/agent-bridge/watcher.py", "NOT_EXECUTED_STATIC_ONLY", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-12", "DIRECT_TURN_START", "configured argv", "tools/agent-bridge/codex_app_server_wake.py", "TRACKED_BASE_EXACT", "tools/agent-bridge/configure_watcher.py", "MODEL_EFFORT_BINDING_MISSING", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-13", "DIRECT_TURN_START", "tools/agent-bridge/codex_app_server_wake.py", "codex app-server", "TRACKED_BASE_EXACT", "tools/agent-bridge/codex_app_server_wake.py", "MODEL_EFFORT_BINDING_MISSING", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-14", "DIRECT_TURN_START", "codex app-server", "thread/resume", "TRACKED_BASE_EXACT", "tools/agent-bridge/codex_app_server_wake.py", "MODEL_EFFORT_BINDING_MISSING", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-15", "DIRECT_TURN_START", "thread/resume", "turn/start", "TRACKED_BASE_EXACT", "tools/agent-bridge/codex_app_server_wake.py", "MODEL_EFFORT_BINDING_MISSING", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-16", "DIRECT_UI_STEER", "tools/agent-bridge/configure_watcher.py", "tools/agent-bridge/wake_codex.ps1", "TRACKED_BASE_EXACT", "tools/agent-bridge/configure_watcher.py", "NOT_EXECUTED_STATIC_ONLY", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-17", "DIRECT_UI_STEER", "tools/agent-bridge/wake_codex.ps1", "codex://threads/<id> plus Ctrl+Enter Steer", "TRACKED_BASE_EXACT", "tools/agent-bridge/wake_codex.ps1", "NOT_EXECUTED_STATIC_ONLY", "TRACKED_EDGE_ENUMERATED_UNCREDITED"),
    ("EDGE-18", "FAIL_CLOSED_DIAGNOSTIC", "tools/agent-bridge/wake_claude.ps1", "unsupported_thread_addressable_wake exit 20", "TRACKED_BASE_EXACT", "tools/agent-bridge/wake_claude.ps1", "UNSUPPORTED_THREAD_ADDRESSABLE_WAKE", "FAIL_CLOSED_DIAGNOSTIC_EDGE_ENUMERATED"),
]

PRESERVATION = [
    ("model", "EXACT_MODEL_MUST_BE_PRESERVED_WITHOUT_SUBSTITUTION", "BASE_DIRECT_TURN_START_HAS_NO_EXACT_MODEL_BINDING"),
    ("effort", "EXACT_EFFORT_MUST_BE_PRESERVED_WITHOUT_SUBSTITUTION", "BASE_DIRECT_TURN_START_HAS_NO_EXACT_EFFORT_BINDING"),
    ("role", "EXACT_ROLE_MUST_BE_BOUND_TO_EACH_REQUEST_AND_LANE", "REQUEST_ROLE_BINDING_NOT_PRODUCED"),
    ("review", "FRESH_NON_AUTHOR_REVIEW_AND_DISTINCT_ADJUDICATION_MUST_FOLLOW_BYTE_OFFSET_ORDERED_TWO_KEY_POLICY_WITH_BLOCKING_VERDICTS_FAIL_CLOSED", "FRESH_CANDIDATE_REVIEW_AND_ADJUDICATION_NOT_PRODUCED"),
    ("quality", "KNOWN_GOOD_BUILD_OUTPUT_EQUIVALENCE_MUST_BE_PROVEN_NOT_ASSERTED", "QUALITY_EQUIVALENCE_NOT_PRODUCED"),
    ("functionality", "FUNCTIONALITY_EQUIVALENCE_MUST_BE_PROVEN_BY_BASELINE_AB", "FUNCTIONALITY_EQUIVALENCE_NOT_PRODUCED"),
]

NO_TRANSFER = [
    ("R2_NO_WORK_UNCHANGED_TICKS_1000", R2_COMMIT, R2_TREE, 1000, False),
    ("R2_CONTROLS_PYTHON_3_13", R2_COMMIT, R2_TREE, "18/18 PASS", False),
    ("R2_CONTROLS_PYTHON_3_14", R2_COMMIT, R2_TREE, "18/18 PASS", False),
    ("R2_INDEPENDENT_REVIEW_MARKER", R2_COMMIT, R2_TREE, "[MSG 20260819-005623-CODEX-MLV-R2-CLOSED-REVIEW]", False),
    ("R2_DISTINCT_ADJUDICATION_MARKER", R2_ADJUDICATION, R2_ADJUDICATION_TREE, "[MSG 20260819-MLV-UPC-R2-DISTINGUISH-ADJUDICATION-ACCEPT]", False),
    ("SIBLING_R15_NO_WORK_UNCHANGED_TICKS_1000", SIBLING_COMMIT, SIBLING_TREE, 1000, True),
    ("SIBLING_R15_LOCAL_CONTROLS_PYTHON_3_13", SIBLING_COMMIT, SIBLING_TREE, "45/45 PASS", True),
    ("SIBLING_R15_LOCAL_CONTROLS_PYTHON_3_14", SIBLING_COMMIT, SIBLING_TREE, "45/45 PASS", True),
    ("SIBLING_R15_ANY_REVIEW_ADJUDICATION_OR_HOSTED_MARKER", SIBLING_COMMIT, SIBLING_TREE, "NO_MARKER_TRANSFERS_TO_THIS_CANDIDATE", True),
]

MISSING_SURFACES = [
    "CURRENT_WINDOWS_TASK_DEFINITION_STATE_AND_HISTORY",
    "CURRENT_CODEX_AUTOMATION_DEFINITIONS_AND_EXECUTOR_INTERNAL_CHAIN",
    "RETIRED_APP_STORE_WAKE_AND_MIRROR_RECEIPTS",
    "CURRENT_SERVICES_STARTUP_FOLDERS_RUN_KEYS_AND_ALTERNATE_USER_PROFILES",
    "CURRENT_MUTABLE_WATCHER_CONFIG_AND_SHARED_AGENT_BRIDGE_STATE",
    "UNTRACKED_IGNORED_AND_DIRECT_CLI_LAUNCHERS",
    "PROVIDER_BACKEND_ACCOUNT_AND_TOKEN_TELEMETRY",
    "SUPERVISOR_SUSPENDED_CHILD_RESUME_AND_FULL_CHILD_FENCING",
    "INDEPENDENT_OBSERVER_TO_PROVIDER_RECEIPT_EDGE",
]
UNCREDITED_PROOFS = [
    "PINNED_LOCAL_SUPERVISOR_ADAPTER",
    "COMPLETE_LAUNCHER_INVENTORY",
    "COMPLETE_ACTION_GRAPH",
    "FAKE_PROVIDER_CONTROLS",
    "CONCURRENCY_CONTROLS",
    "REQUEST_LEVEL_TOKEN_ACCOUNTING",
    "UNCHANGED_ZERO_INFERENCE_TICKS_1000",
    "FULL_CHILD_FENCING",
    "ROLLBACK_PROOF",
    "SIGNED_INSTALLATION",
    "POSITIVE_DIRECT_LAUNCH_ENFORCEMENT",
    "EXACT_MODEL_EFFORT_ROLE_BINDING",
    "FRESH_INDEPENDENT_REVIEW_AND_DISTINCT_ADJUDICATION",
    "QUALITY_EQUIVALENCE",
    "FUNCTIONALITY_EQUIVALENCE",
    "CURRENT_CLOSED_GATE_PROOF",
    "ONE_USE_CANARY_AUTHORIZATION_AND_RECEIPT",
    "HOSTED_EXACT_TREE_GREEN",
]
REVIEW_BOUNDARY = {
    "authorConflicted": True,
    "authorMaySelfReview": False,
    "freshIndependentReviewReceived": False,
    "distinctAdjudicationReceived": False,
    "priorReviewTransferAllowed": False,
}
AUTHORITY_KEYS = {
    "projectAdoption",
    "fleetAdoption",
    "runtimeActivation",
    "providerInvocation",
    "authenticationAction",
    "processSpawnResumeKill",
    "schedulerOrTaskMutation",
    "automationMutation",
    "launcherMutation",
    "automaticGateMutation",
    "installation",
    "canaryCredit",
    "mergeLandingPushRelease",
    "currentRuntimeGateCredit",
    "siblingProofTransfer",
    "dispositionCredit",
}
NEXT_STEP = {
    "kind": "READ_ONLY_CURRENT_MACHINE_LAUNCHER_AND_ACTION_GRAPH_CLOSURE",
    "scope": "From a separately authorized read-only observer, bind current scheduled-task definitions and history, Codex automation definitions and executor chain, retired-family receipts, services, startup, run keys, mutable watcher configuration, untracked launchers, and every direct provider or turn-start edge without executing any edge.",
    "requiredOutput": "A new exact subject with explicit unreadable surfaces, typed edge outcomes, a complete-or-incomplete declaration, and zero authority; any later installation remains a separate reviewed transaction.",
    "forbiddenActions": [
        "PROVIDER_OR_AUTH_INVOCATION",
        "PROCESS_SPAWN_RESUME_OR_KILL",
        "TASK_AUTOMATION_LAUNCHER_OR_GATE_MUTATION",
        "LEASE_PACKET_QUEUE_OR_RUNTIME_MUTATION",
        "ADOPTION_INSTALLATION_CANARY_OR_DISPOSITION_CREDIT",
    ],
}


class CandidateError(ValueError):
    pass


def exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CandidateError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def exact_keys(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise CandidateError(code)
    return value


def exact_equal(actual: Any, expected: Any, code: str) -> None:
    if type(actual) is not type(expected):
        raise CandidateError(code)
    if type(expected) is dict:
        if set(actual) != set(expected):
            raise CandidateError(code)
        for key in expected:
            exact_equal(actual[key], expected[key], code)
        return
    if type(expected) is list:
        if len(actual) != len(expected):
            raise CandidateError(code)
        for left, right in zip(actual, expected):
            exact_equal(left, right, code)
        return
    if actual != expected:
        raise CandidateError(code)


def clean_git_env() -> dict[str, str]:
    env = os.environ.copy()
    exact = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
        "GIT_CONFIG",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_COUNT",
    }
    for key in list(env):
        if key in exact or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    return env


def git(*args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=ROOT,
        env=clean_git_env(),
        check=False,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
    )
    if result.returncode != 0:
        raise CandidateError(f"GIT_OBJECT_UNAVAILABLE:{' '.join(args)}")
    return result.stdout


def commit_tuple(commit: str) -> tuple[str, list[str]]:
    lines = str(git("show", "-s", "--format=%T%n%P", commit)).splitlines()
    if len(lines) != 2 or not SHA1.fullmatch(lines[0]):
        raise CandidateError("COMMIT_TUPLE_INVALID")
    parents = lines[1].split()
    if any(not SHA1.fullmatch(parent) for parent in parents):
        raise CandidateError("COMMIT_PARENT_INVALID")
    return lines[0], parents


def commit_diff(commit: str) -> list[tuple[str, str]]:
    output = str(git("diff-tree", "--no-commit-id", "--name-status", "-r", commit))
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise CandidateError("COMMIT_DIFF_INVALID")
        entries.append((fields[0], fields[1]))
    return entries


def object_bytes(commit: str, path: str) -> bytes:
    return bytes(git("show", f"{commit}:{path}", binary=True))


def repository_facts() -> dict[str, Any]:
    head = str(git("rev-parse", "HEAD")).strip()
    tree, parents = commit_tuple(head)
    return {
        "head": head,
        "tree": tree,
        "parents": parents,
        "diff": commit_diff(head),
        "status": str(git("status", "--porcelain=v1", "--untracked-files=all")),
        "originMaster": str(git("rev-parse", "refs/remotes/origin/master")).strip(),
        "originUrl": str(git("config", "--local", "--get", "remote.origin.url")).strip(),
    }


def verify_repository_facts(facts: Any) -> None:
    facts = exact_keys(facts, {"head", "tree", "parents", "diff", "status", "originMaster", "originUrl"}, "REPOSITORY_FACT_FIELDS_INVALID")
    if type(facts["head"]) is not str or not SHA1.fullmatch(facts["head"]) or facts["head"] == BASE:
        raise CandidateError("CANDIDATE_HEAD_INVALID")
    actual_tree, actual_parents = commit_tuple(facts["head"])
    if type(facts["tree"]) is not str or not SHA1.fullmatch(facts["tree"]) or facts["tree"] != actual_tree:
        raise CandidateError("CANDIDATE_TREE_INVALID")
    exact_equal(facts["parents"], actual_parents, "CANDIDATE_PARENT_INVALID")
    exact_equal(facts["parents"], [BASE], "CANDIDATE_PARENT_INVALID")
    exact_equal(facts["diff"], commit_diff(facts["head"]), "CANDIDATE_DIFF_INVALID")
    exact_equal(facts["diff"], CANDIDATE_DIFF, "CANDIDATE_DIFF_INVALID")
    if facts["status"] != "":
        raise CandidateError("CANDIDATE_CHECKOUT_NOT_CLEAN")
    if facts["originMaster"] != BASE:
        raise CandidateError("ORIGIN_MASTER_INVALID")
    if facts["originUrl"] != ORIGIN:
        raise CandidateError("ORIGIN_URL_INVALID")
    if commit_tuple(BASE) != (BASE_TREE, [BASE_PARENT]):
        raise CandidateError("BASE_OBJECT_INVALID")
    if commit_tuple(SIBLING_COMMIT) != (SIBLING_TREE, [SIBLING_PARENT]):
        raise CandidateError("SIBLING_OBJECT_INVALID")


def verify_base_evidence(items: Any) -> None:
    if type(items) is not list or len(items) != len(BASE_EVIDENCE):
        raise CandidateError("BASE_EVIDENCE_INVALID")
    for item, expected in zip(items, BASE_EVIDENCE):
        item = exact_keys(item, {"path", "gitBlobOid", "sha256", "bytes", "requiredText"}, "BASE_EVIDENCE_FIELDS_INVALID")
        path, oid, digest, size, required = expected
        exact_equal(item, {"path": path, "gitBlobOid": oid, "sha256": digest, "bytes": size, "requiredText": required}, "BASE_EVIDENCE_INVALID")
        if not SHA1.fullmatch(item["gitBlobOid"]) or not SHA256.fullmatch(item["sha256"]):
            raise CandidateError("BASE_EVIDENCE_DIGEST_TYPE_INVALID")
        data = object_bytes(BASE, path)
        if str(git("rev-parse", f"{BASE}:{path}")).strip() != oid:
            raise CandidateError("BASE_EVIDENCE_BLOB_MISMATCH")
        if len(data) != size or "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise CandidateError("BASE_EVIDENCE_BYTE_MISMATCH")
        text = data.decode("utf-8")
        if any(anchor not in text for anchor in required):
            raise CandidateError("BASE_EVIDENCE_ANCHOR_MISSING")


def verify_surface_families(items: Any) -> None:
    if type(items) is not list or len(items) != len(SURFACE_FAMILIES):
        raise CandidateError("SURFACE_FAMILIES_INVALID")
    keys = {"familyId", "kind", "reportedName", "reportedCadenceMinutes", "reportedState", "currentObservationCredited", "inventoryClosureCredited"}
    for item, expected in zip(items, SURFACE_FAMILIES):
        item = exact_keys(item, keys, "SURFACE_FAMILY_FIELDS_INVALID")
        family_id, kind, name, minutes, state = expected
        exact_equal(
            item,
            {
                "familyId": family_id,
                "kind": kind,
                "reportedName": name,
                "reportedCadenceMinutes": minutes,
                "reportedState": state,
                "currentObservationCredited": False,
                "inventoryClosureCredited": False,
            },
            "SURFACE_FAMILIES_INVALID",
        )


def verify_edges(items: Any) -> None:
    if type(items) is not list or len(items) != len(EDGES):
        raise CandidateError("ACTION_EDGES_INVALID")
    for item, expected in zip(items, EDGES):
        item = exact_keys(item, EDGE_KEYS, "ACTION_EDGE_FIELDS_INVALID")
        edge_id, family, source, target, evidence, base_path, error_class, outcome = expected
        exact_equal(
            item,
            {
                "edgeId": edge_id,
                "family": family,
                "from": source,
                "to": target,
                "evidenceKind": evidence,
                "basePath": base_path,
                "executionObserved": False,
                "authorityCredited": False,
                "errorClass": error_class,
                "outcome": outcome,
            },
            "ACTION_EDGES_INVALID",
        )


def verify_preservation(items: Any) -> None:
    if type(items) is not list or len(items) != len(PRESERVATION):
        raise CandidateError("PRESERVATION_REQUIREMENTS_INVALID")
    keys = {"dimension", "requirement", "required", "proven", "credited", "gap"}
    for item, expected in zip(items, PRESERVATION):
        item = exact_keys(item, keys, "PRESERVATION_REQUIREMENT_FIELDS_INVALID")
        dimension, requirement, gap = expected
        exact_equal(
            item,
            {"dimension": dimension, "requirement": requirement, "required": True, "proven": False, "credited": False, "gap": gap},
            "PRESERVATION_REQUIREMENTS_INVALID",
        )


def verify_no_transfer(items: Any) -> None:
    if type(items) is not list or len(items) != len(NO_TRANSFER):
        raise CandidateError("NO_TRANSFER_INVALID")
    keys = {"claimId", "sourceCommit", "sourceTree", "value", "sourceCommitTupleVerifiedHere", "transferAllowed", "credited"}
    for item, expected in zip(items, NO_TRANSFER):
        item = exact_keys(item, keys, "NO_TRANSFER_FIELDS_INVALID")
        claim_id, commit, tree, value, verified = expected
        exact_equal(
            item,
            {
                "claimId": claim_id,
                "sourceCommit": commit,
                "sourceTree": tree,
                "value": value,
                "sourceCommitTupleVerifiedHere": verified,
                "transferAllowed": False,
                "credited": False,
            },
            "NO_TRANSFER_INVALID",
        )
        if verified and commit != SIBLING_COMMIT:
            raise CandidateError("NO_TRANSFER_OBJECT_SCOPE_INVALID")
        if not verified and commit not in {R2_COMMIT, R2_ADJUDICATION}:
            raise CandidateError("NO_TRANSFER_UNVERIFIED_SCOPE_INVALID")


def verify_sibling_no_transfer_source() -> None:
    data = object_bytes(SIBLING_COMMIT, SIBLING_PACKET_PATH)
    if str(git("rev-parse", f"{SIBLING_COMMIT}:{SIBLING_PACKET_PATH}")).strip() != SIBLING_PACKET_BLOB:
        raise CandidateError("SIBLING_PACKET_BLOB_MISMATCH")
    if len(data) != SIBLING_PACKET_BYTES or hashlib.sha256(data).hexdigest() != SIBLING_PACKET_SHA256:
        raise CandidateError("SIBLING_PACKET_BYTE_MISMATCH")
    try:
        packet = json.loads(data.decode("utf-8"), object_pairs_hook=exact_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CandidateError("SIBLING_PACKET_JSON_INVALID") from exc
    local = packet.get("localEvidence")
    if type(local) is not dict:
        raise CandidateError("SIBLING_PACKET_LOCAL_EVIDENCE_INVALID")
    exact_equal(local.get("unchangedNoWorkTicks"), 1000, "SIBLING_PACKET_TICKS_INVALID")
    exact_equal(local.get("python313"), "45/45 PASS", "SIBLING_PACKET_CONTROLS_INVALID")
    exact_equal(local.get("python314"), "45/45 PASS", "SIBLING_PACKET_CONTROLS_INVALID")


def verify_candidate(candidate: Any, verify_objects: bool) -> None:
    candidate = exact_keys(candidate, ROOT_KEYS, "ROOT_FIELDS_INVALID")
    if candidate["schema"] != SCHEMA or type(candidate["schema"]) is not str or candidate["status"] != STATUS:
        raise CandidateError("CANDIDATE_IDENTITY_INVALID")
    exact_equal(candidate["projectBase"], PROJECT_BASE, "PROJECT_BASE_INVALID")
    exact_equal(candidate["candidateBoundary"], CANDIDATE_BOUNDARY, "CANDIDATE_BOUNDARY_INVALID")
    if verify_objects:
        verify_base_evidence(candidate["baseTrackedEvidence"])
        verify_sibling_no_transfer_source()
    else:
        expected = [
            {"path": p, "gitBlobOid": oid, "sha256": digest, "bytes": size, "requiredText": anchors}
            for p, oid, digest, size, anchors in BASE_EVIDENCE
        ]
        exact_equal(candidate["baseTrackedEvidence"], expected, "BASE_EVIDENCE_INVALID")
    verify_surface_families(candidate["knownSurfaceFamilies"])
    verify_edges(candidate["knownActionEdges"])
    verify_preservation(candidate["preservationRequirementsOnly"])
    verify_no_transfer(candidate["explicitNoTransfer"])
    exact_equal(candidate["missingSurfaces"], MISSING_SURFACES, "MISSING_SURFACES_INVALID")
    exact_equal(candidate["uncreditedAdoptionProofs"], UNCREDITED_PROOFS, "UNCREDITED_PROOFS_INVALID")
    exact_equal(candidate["reviewBoundary"], REVIEW_BOUNDARY, "REVIEW_BOUNDARY_INVALID")
    authority = exact_keys(candidate["authority"], AUTHORITY_KEYS, "AUTHORITY_FIELDS_INVALID")
    if any(type(value) is not bool or value for value in authority.values()):
        raise CandidateError("ZERO_AUTHORITY_OVERCLAIM")
    exact_equal(candidate["nextLawfulStep"], NEXT_STEP, "NEXT_STEP_INVALID")


def expect_refusal(candidate: dict[str, Any], mutate: Callable[[dict[str, Any]], None], expected: str) -> None:
    changed = copy.deepcopy(candidate)
    mutate(changed)
    try:
        verify_candidate(changed, verify_objects=False)
    except CandidateError as exc:
        if expected not in str(exc):
            raise CandidateError(f"WRONG_MUTATION_REFUSAL:{expected}:{exc}") from exc
        return
    raise CandidateError(f"MUTATION_SURVIVED:{expected}")


def expect_repository_refusal(facts: dict[str, Any], mutate: Callable[[dict[str, Any]], None], expected: str) -> None:
    changed = copy.deepcopy(facts)
    mutate(changed)
    try:
        verify_repository_facts(changed)
    except CandidateError as exc:
        if expected not in str(exc):
            raise CandidateError(f"WRONG_REPOSITORY_MUTATION_REFUSAL:{expected}:{exc}") from exc
        return
    raise CandidateError(f"REPOSITORY_MUTATION_SURVIVED:{expected}")


def run_hostile_tests(candidate: dict[str, Any], facts: dict[str, Any]) -> int:
    tests: list[tuple[Callable[[dict[str, Any]], None], str]] = [
        (lambda v: v["candidateBoundary"].update(semanticCompletenessClaimed=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(inventoryComplete=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(actionGraphComplete=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(installed=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(adoptionCredit=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(automaticGateState="OPEN"), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(currentClosedGateProofCredited=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(gateObservedByCandidate=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(currentMachineStateObserved=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(providerOrAuthInvoked=True), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["candidateBoundary"].update(processOrTaskActionPerformed=1), "CANDIDATE_BOUNDARY_INVALID"),
        (lambda v: v["projectBase"].update(commit="81bc1ad472daaf1cad2609a80fa86495a7684367"), "PROJECT_BASE_INVALID"),
        (lambda v: v["projectBase"].update(tree="0" * 40), "PROJECT_BASE_INVALID"),
        (lambda v: v["projectBase"].update(canonicalOrigin="C:\\forged\\MLV-App"), "PROJECT_BASE_INVALID"),
        (lambda v: v["projectBase"].update(masterQualified="true"), "PROJECT_BASE_INVALID"),
        (lambda v: v["knownSurfaceFamilies"].__setitem__(0, copy.deepcopy(v["knownSurfaceFamilies"][1])), "SURFACE_FAMILIES_INVALID"),
        (lambda v: v["knownSurfaceFamilies"][0].update(reportedState="Ready"), "SURFACE_FAMILIES_INVALID"),
        (lambda v: v["knownSurfaceFamilies"][2].update(currentObservationCredited=True), "SURFACE_FAMILIES_INVALID"),
        (lambda v: v["knownActionEdges"].pop(7), "ACTION_EDGES_INVALID"),
        (lambda v: v["knownActionEdges"][0].update(errorClass=None), "ACTION_EDGES_INVALID"),
        (lambda v: v["knownActionEdges"][2].update(outcome="PROVIDER_STARTED"), "ACTION_EDGES_INVALID"),
        (lambda v: v["knownActionEdges"][12].update(executionObserved=True), "ACTION_EDGES_INVALID"),
        (lambda v: v["knownActionEdges"][14].update(authorityCredited=True), "ACTION_EDGES_INVALID"),
        (lambda v: v["knownActionEdges"][8].update(basePath="tools/provider_control/mlv_lane_supervisor.py"), "ACTION_EDGES_INVALID"),
        (lambda v: v["preservationRequirementsOnly"][0].update(proven=True), "PRESERVATION_REQUIREMENTS_INVALID"),
        (lambda v: v["preservationRequirementsOnly"][1].update(credited=True), "PRESERVATION_REQUIREMENTS_INVALID"),
        (lambda v: v["preservationRequirementsOnly"][2].update(requirement="ROLE_MAY_CHANGE"), "PRESERVATION_REQUIREMENTS_INVALID"),
        (lambda v: v["explicitNoTransfer"].pop(0), "NO_TRANSFER_INVALID"),
        (lambda v: v["explicitNoTransfer"][0].update(value="1000"), "NO_TRANSFER_INVALID"),
        (lambda v: v["explicitNoTransfer"][1].update(value="18/17 PASS"), "NO_TRANSFER_INVALID"),
        (lambda v: v["explicitNoTransfer"][3].update(value="[FORGED REVIEW]"), "NO_TRANSFER_INVALID"),
        (lambda v: v["explicitNoTransfer"][4].update(transferAllowed=True), "NO_TRANSFER_INVALID"),
        (lambda v: v["explicitNoTransfer"][5].update(credited=True), "NO_TRANSFER_INVALID"),
        (lambda v: v["explicitNoTransfer"][5].update(sourceCommitTupleVerifiedHere=False), "NO_TRANSFER_INVALID"),
        (lambda v: v["missingSurfaces"].pop(0), "MISSING_SURFACES_INVALID"),
        (lambda v: v["uncreditedAdoptionProofs"].remove("CURRENT_CLOSED_GATE_PROOF"), "UNCREDITED_PROOFS_INVALID"),
        (lambda v: v["reviewBoundary"].update(authorConflicted=False), "REVIEW_BOUNDARY_INVALID"),
        (lambda v: v["reviewBoundary"].update(freshIndependentReviewReceived=True), "REVIEW_BOUNDARY_INVALID"),
        (lambda v: v["reviewBoundary"].update(priorReviewTransferAllowed=True), "REVIEW_BOUNDARY_INVALID"),
        (lambda v: v["authority"].update(projectAdoption=True), "ZERO_AUTHORITY_OVERCLAIM"),
        (lambda v: v["authority"].update(siblingProofTransfer=1), "ZERO_AUTHORITY_OVERCLAIM"),
        (lambda v: v["authority"].update(dispositionCredit=True), "ZERO_AUTHORITY_OVERCLAIM"),
        (lambda v: v["nextLawfulStep"].update(kind="INSTALL_SUPERVISOR"), "NEXT_STEP_INVALID"),
        (lambda v: v["nextLawfulStep"]["forbiddenActions"].pop(0), "NEXT_STEP_INVALID"),
        (lambda v: v.update(extraAuthority=True), "ROOT_FIELDS_INVALID"),
    ]
    for mutate, expected in tests:
        expect_refusal(candidate, mutate, expected)

    repo_tests: list[tuple[Callable[[dict[str, Any]], None], str]] = [
        (lambda v: v.update(head=BASE), "CANDIDATE_HEAD_INVALID"),
        (lambda v: v.update(tree="0" * 40), "CANDIDATE_TREE_INVALID"),
        (lambda v: v.update(parents=[BASE, SIBLING_COMMIT]), "CANDIDATE_PARENT_INVALID"),
        (lambda v: v["diff"].append(("M", "tools/agent-bridge/watcher.py")), "CANDIDATE_DIFF_INVALID"),
        (lambda v: v.update(status=" M tools/agent-bridge/watcher.py\n"), "CANDIDATE_CHECKOUT_NOT_CLEAN"),
        (lambda v: v.update(originMaster="81bc1ad472daaf1cad2609a80fa86495a7684367"), "ORIGIN_MASTER_INVALID"),
        (lambda v: v.update(originUrl=ACQUISITION_ORIGIN), "ORIGIN_URL_INVALID"),
    ]
    for mutate, expected in repo_tests:
        expect_repository_refusal(facts, mutate, expected)

    try:
        json.loads('{"schema":"one","schema":"two"}', object_pairs_hook=exact_object)
    except CandidateError as exc:
        if "DUPLICATE_JSON_KEY" not in str(exc):
            raise
    else:
        raise CandidateError("DUPLICATE_JSON_KEY_SURVIVED")
    return len(tests) + len(repo_tests) + 1


def run() -> int:
    try:
        candidate = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"), object_pairs_hook=exact_object)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CandidateError("CANDIDATE_JSON_INVALID") from exc
    facts = repository_facts()
    verify_repository_facts(facts)
    verify_candidate(candidate, verify_objects=True)
    return run_hostile_tests(candidate, facts)


def main() -> int:
    try:
        count = run()
    except CandidateError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: MLV-App launcher/action-graph census candidate is master-bound, "
        f"incomplete, zero-authority, and hostile-closed ({count}/{count})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
