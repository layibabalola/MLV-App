#!/usr/bin/env python3
"""Validate MLV-App's inert, zero-authority R26 DISTINGUISH candidate."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "docs" / "universal-token-control-r26-disposition-candidate-2026-08-19.json"
EVIDENCE_REPO_PATH = "docs/universal-token-control-r26-disposition-candidate-2026-08-19.json"
VALIDATOR_REPO_PATH = "tools/universal-token-control-r26-disposition-candidate.tests.py"
SCHEMA = "mlv-app-universal-token-control-r26-disposition-candidate/v1"
STATUS = "CANDIDATE_ZERO_AUTHORITY"
BASE = "30889f77e2000190b94d59f80f6a03b12ce3e0d3"
BASE_TREE = "d82ca4fdb9c2f45eb4bf169b4f5edfcc9a14100a"
BASE_PARENT = "45b63b1eb1841ab216a0f5c2ff78b3938fb82f85"
CANDIDATE_SUBJECT = "13eacc900662f4ba5df0659b0c4ff493abe9f0c5"
CANDIDATE_SUBJECT_TREE = "94c2f4e6e52e4f9ee0483b0abeb13a7595c58a6b"
R26_TECHNICAL = "e70a044f31dd2f43ab7c716d63a4eb89318c61b6"
R26_MERGE = "909f769d02e8412e51e28e242cfa8d00dadc9a3d"
SIBLING_PROPOSAL = "50db64d71bf62da041b9a6872c893382cd7cc79b"
SIBLING_TREE = "ab58ec53414ff889eaa41a6689dde1141cc687ed"
SIBLING_PARENT = "709bf99307271ac7b6bb0f202495723373892871"
REMOTE = "https://github.com/layibabalola/MLV-App.git"
SHA1 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")

CANDIDATE_DIFF = [
    ("A", EVIDENCE_REPO_PATH),
    ("A", VALIDATOR_REPO_PATH),
]
HARDENING_DIFF = [("M", VALIDATOR_REPO_PATH)]
DISPOSITION = {
    "kind": "DISTINGUISH",
    "statement": (
        "DISTINGUISH(909f769d02e8412e51e28e242cfa8d00dadc9a3d, "
        "MLV_APP_R26_CANDIDATE_ZERO_AUTHORITY_CURRENT_MASTER_HAS_NO_INSTALLED_TOKEN_CONTROL_"
        "SUPERVISOR_COMPLETE_LAUNCHER_CENSUS_REQUEST_LEVEL_ACCOUNTING_1000_IDLE_TICKS_FULL_"
        "CHILD_FENCING_ROLLBACK_QUALITY_EQUIVALENCE_OR_CURRENT_CLOSED_GATE_PROOF, "
        "MLV_APP_BASE_30889f77e2000190b94d59f80f6a03b12ce3e0d3)"
    ),
    "canonicalTechnicalSubject": R26_TECHNICAL,
    "canonicalMerge": R26_MERGE,
    "adoptionCredit": False,
}
SUBJECT_BINDING = {
    "mode": "CLEAN_HEAD_SOLE_PARENT_BASE_EXACT_TWO_ADDITIONS",
    "baseCommit": BASE,
    "expectedAddedPaths": [EVIDENCE_REPO_PATH, VALIDATOR_REPO_PATH],
}
DOCTRINE_EVIDENCE = {
    "technicalSubjectTree": "e9283a1c297103dd53f0bc7a1310fb1dc86b591e",
    "technicalSubjectParent": "387b4e13c4a8eeccf414d527b2d6a04dcd4e3ed8",
    "mergeTree": "e9283a1c297103dd53f0bc7a1310fb1dc86b591e",
    "mergeParents": ["c1529bc3030c6663e0be63c4789b07530b9b2ecc", R26_TECHNICAL],
    "manifest": {
        "path": "manifests/universal-provider-control-reconciliation-r26.json",
        "gitBlobOid": "898385fb82fbbe9946f937f0486142f4733d03fe",
        "rawGitBlobSha256": "sha256:5347db090e37a44ae440e90133e13b563b9f2a1c0545706e74a0f1249f9763d5",
        "canonicalZeroSelfSha256": "sha256:83a615d4de713993e873be416c523f04bf08da9e638c44d9a93a29d14721436e",
        "bytes": 34413,
        "status": STATUS,
        "automaticGateState": "CLOSED",
        "referenceExecutionBoundary": "NOT_INSTALLED",
        "directInvocationImpossible": False,
        "hostedGreenClaimed": False,
    },
    "tokenPolicy": {
        "path": "policy/universal-provider-token-control-r22.json",
        "gitBlobOid": "33b75cfb61b8c8934009ff987f2ea90ef5f74eb5",
        "sha256": "sha256:b5eca2ef60a86d87e2ca96d34569035aef510387de7ea204ab7cbf98ce5f5192",
        "bytes": 1397,
        "providerAuthority": False,
        "directLaunchPositiveEnforcementRequired": True,
    },
    "projectSpec": {
        "path": "specs/mlv-app.md",
        "gitBlobOid": "858b513e4e9e17eef1cd6864a351ee4905940f8b",
        "sha256": "sha256:8dfd9dbb3cf31bfae1f036f444c0ce59494522de373276efc6f4d86167f97f40",
        "bytes": 19624,
        "priorDisposition": "DISTINGUISH_R14_HARD_CLOSED_ZERO_AUTHORITY",
    },
    "rulingLines1027To1034": (
        "This ruling is doctrine authority only after the exact subject and this ruling reach canonical "
        "master; it is never project adoption or runtime authority. Every fleet project must issue ADOPT, "
        "DISTINGUISH, or REJECT. Adoption additionally requires its pinned local supervisor/adapter, "
        "complete launcher inventory, fake-provider and concurrency controls, 1,000 unchanged "
        "zero-inference ticks, full-child fencing, rollback proof, and a current CLOSED gate. A portable "
        "doctrine merge alone is not fleet-wide runtime adoption."
    ),
}
PROJECT_BASE = {
    "commit": BASE,
    "tree": BASE_TREE,
    "parent": BASE_PARENT,
    "remote": REMOTE,
    "remoteHeadObserved": BASE,
}
PORTABLE_INVARIANTS = [
    "MODEL_FREE_NO_WORK_BEFORE_SESSION_OR_PROVIDER_REQUEST",
    "ONE_QUOTA_DOMAIN_OWNER_FOR_FULL_CHILD_LIFETIME",
    "REQUEST_LEVEL_TOKEN_RESERVATION_AND_RECONCILIATION",
    "FULL_WEIGHT_CACHE_READ_AND_SEPARATE_CACHE_WRITE_INPUT_REASONING_OUTPUT_ACCOUNTING",
    "FAILED_REFUSED_TIMED_OUT_AND_RETRIED_ATTEMPTS_ARE_CHARGED",
    "MINIMUM_TWENTY_PERCENT_COMPLETION_AND_TERMINAL_RESERVE",
    "BOUNDED_PREFIX_CAPSULE_CONTEXT_TURNS_RETRIES_AND_CACHE_AFFINITY_TTL",
    "EXACT_MODEL_EFFORT_ROLE_SUBJECT_ARGV_AND_QUALITY_CELL_BINDING",
    "SEPARATELY_CERTIFIED_POSITIVE_DIRECT_LAUNCH_ENFORCEMENT",
    "CANARY_IS_SEPARATELY_AUTHORIZED_ONE_USE_AND_RETURNS_FAIL_CLOSED",
]
CENSUS = {
    "scope": "TRACKED_FILES_AT_PROJECT_BASE_ONLY",
    "complete": False,
    "externalMachineSurfacesInspected": False,
    "liveRuntimeInspected": False,
    "providerOrAuthInvoked": False,
    "findings": [
        "BOOTSTRAP_CAN_START_CONFIG_DRIVEN_WATCHER",
        "WATCHER_EXECUTES_CONFIGURED_WAKE_ARGV",
        "CODEX_WAKE_CAN_USE_UI_STEER_OR_APP_SERVER_TURN_START",
        "APP_SERVER_TURN_START_HAS_NO_EXACT_MODEL_OR_EFFORT_FIELD",
        "CLAUDE_WAKE_IS_DIAGNOSTIC_FAIL_CLOSED",
    ],
    "unresolved": [
        "WINDOWS_SCHEDULED_TASKS",
        "CODEX_AUTOMATIONS",
        "SERVICES_AND_STARTUP_SURFACES",
        "MUTABLE_AGENT_BRIDGE_CONFIG_AND_SHARED_STATE",
        "UNTRACKED_OR_IGNORED_LAUNCHERS",
        "PROVIDER_BACKEND_AND_ACCOUNT_TELEMETRY",
    ],
}
CONTROL_CLASSIFICATIONS = [
    ("tools/agent-bridge/bootstrap_session.py", "WATCHER_PROCESS_BOOTSTRAP_NOT_R26_REQUEST_SUPERVISOR"),
    ("tools/agent-bridge/configure_watcher.py", "WAKE_PROVIDER_COMMAND_SELECTOR_NOT_R26_REQUEST_SUPERVISOR"),
    ("tools/agent-bridge/watcher.py", "CONFIG_DRIVEN_WAKE_ARGV_EXECUTOR_NOT_R26_REQUEST_SUPERVISOR"),
    (
        "tools/agent-bridge/codex_app_server_wake.py",
        "DIRECT_CODEX_APP_SERVER_TURN_START_WITHOUT_EXACT_MODEL_EFFORT_BINDING",
    ),
    ("tools/agent-bridge/wake_codex.ps1", "DIRECT_CODEX_DESKTOP_UI_STEER_SURFACE_NOT_R26_REQUEST_SUPERVISOR"),
    ("tools/agent-bridge/wake_claude.ps1", "CLAUDE_WAKE_DIAGNOSTIC_FAIL_CLOSED"),
]
SIBLING_ARTIFACT_PATHS = [
    "tools/provider_control/AUTHOR-PACKET.json",
    "tools/provider_control/DISTINGUISH.md",
    "tools/provider_control/lane-bindings.candidate.json",
    "tools/provider_control/mlv-observed-inventory.candidate.json",
]
NON_REGRESSION = {
    "rule": "TOKEN_SAVINGS_MUST_NOT_REGRESS_EXACT_MODEL_EFFORT_ROLE_REVIEW_QUALITY_OR_FUNCTIONALITY",
    "adoptionCredit": False,
    "dimensions": [
        {
            "dimension": "model",
            "requiredClaim": "EXACT_MODEL_PRESERVED",
            "evidenceState": "MISSING_EXACT_BINDING_FROM_BASE_DIRECT_TURN_START",
            "anchorPath": "tools/agent-bridge/codex_app_server_wake.py",
        },
        {
            "dimension": "effort",
            "requiredClaim": "EXACT_EFFORT_PRESERVED",
            "evidenceState": "MISSING_EXACT_BINDING_FROM_BASE_DIRECT_TURN_START",
            "anchorPath": "tools/agent-bridge/codex_app_server_wake.py",
        },
        {
            "dimension": "role",
            "requiredClaim": "EXACT_ROLE_PRESERVED",
            "evidenceState": "PROJECT_REVIEW_ROLES_PINNED_PROVIDER_REQUEST_ROLE_BINDING_NOT_PRODUCED",
            "anchorPath": "AGENTS.md",
        },
        {
            "dimension": "review",
            "requiredClaim": "EXACT_REVIEW_PRESERVED",
            "evidenceState": "PROJECT_REVIEW_POLICY_PINNED_FRESH_R26_REVIEW_NOT_PRODUCED",
            "anchorPath": "AGENTS.md",
        },
        {
            "dimension": "quality",
            "requiredClaim": "QUALITY_NON_INFERIOR",
            "evidenceState": "OUTPUT_EQUIVALENCE_POLICY_PINNED_R26_EQUIVALENCE_NOT_PRODUCED",
            "anchorPath": "AGENTS.md",
        },
        {
            "dimension": "functionality",
            "requiredClaim": "FUNCTIONALITY_EQUIVALENT",
            "evidenceState": "BASELINE_AB_POLICY_PINNED_R26_FUNCTIONAL_EQUIVALENCE_NOT_PRODUCED",
            "anchorPath": "AGENTS.md",
        },
    ],
}
PROOF_GAPS = {
    "pinnedLocalSupervisorAdapter",
    "completeLauncherInventory",
    "completeActionGraph",
    "fakeProviderControls",
    "concurrencyControls",
    "unchangedZeroInferenceTicks1000",
    "fullChildFencing",
    "rollbackProof",
    "currentClosedGateProof",
    "requestLevelTokenAccounting",
    "qualityEquivalence",
    "functionalityEquivalence",
    "exactModelBinding",
    "exactEffortBinding",
    "exactRoleBinding",
    "freshIndependentReview",
    "signedInstallation",
    "positiveDirectLaunchEnforcement",
    "oneUseCanaryAuthorizationAndReceipt",
    "hostedExactTreeGreen",
}
AUTHORITY_FIELDS = {
    "projectAdoption",
    "fleetAdoption",
    "runtimeActivation",
    "providerInvocation",
    "authenticationAction",
    "processSpawnResumeKill",
    "schedulerOrTaskMutation",
    "launcherMutation",
    "automaticGateMutation",
    "installation",
    "canaryCredit",
    "mergeLandingPushRelease",
    "currentRuntimeGateCredit",
    "siblingProofTransfer",
}
NEXT_ACTION = {
    "kind": "READ_ONLY_COMPLETE_LAUNCHER_CENSUS",
    "scope": (
        "Enumerate and hash scheduled-task, Codex automation, service, startup, watcher-config, "
        "repository-wrapper, ignored-state, and direct CLI launch surfaces without invoking a provider, "
        "changing authentication, or mutating runtime state."
    ),
    "output": (
        "A bounded project-owned census with explicit unreadable surfaces, complete action-graph status, "
        "and zero authority."
    ),
    "whyFirst": (
        "The tracked base already exposes direct Codex wake paths, while the current machine and ignored-state "
        "launcher graph was deliberately not credited from a sibling proposal."
    ),
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
    if not isinstance(value, dict) or set(value) != keys:
        raise CandidateError(code)
    return value


def git(*args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
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


def verify_artifact(commit: str, artifact: Any, extra_keys: set[str] | None = None) -> bytes:
    keys = {"path", "gitBlobOid", "sha256", "bytes"} | (extra_keys or set())
    artifact = exact_keys(artifact, keys, "ARTIFACT_FIELDS_INVALID")
    path = artifact["path"]
    if not isinstance(path, str) or not path:
        raise CandidateError("ARTIFACT_PATH_INVALID")
    if not isinstance(artifact["gitBlobOid"], str) or not SHA1.fullmatch(artifact["gitBlobOid"]):
        raise CandidateError("ARTIFACT_BLOB_INVALID")
    if not isinstance(artifact["sha256"], str) or not SHA256.fullmatch(artifact["sha256"]):
        raise CandidateError("ARTIFACT_SHA256_INVALID")
    if type(artifact["bytes"]) is not int or artifact["bytes"] <= 0:
        raise CandidateError("ARTIFACT_SIZE_INVALID")
    data = object_bytes(commit, path)
    if str(git("rev-parse", f"{commit}:{path}")).strip() != artifact["gitBlobOid"]:
        raise CandidateError("ARTIFACT_BLOB_MISMATCH")
    if len(data) != artifact["bytes"]:
        raise CandidateError("ARTIFACT_SIZE_MISMATCH")
    if "sha256:" + hashlib.sha256(data).hexdigest() != artifact["sha256"]:
        raise CandidateError("ARTIFACT_SHA256_MISMATCH")
    text = data.decode("utf-8")
    if "requiredText" in keys:
        required = artifact["requiredText"]
        if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
            raise CandidateError("ARTIFACT_REQUIRED_TEXT_INVALID")
        if any(item not in text for item in required):
            raise CandidateError("ARTIFACT_REQUIRED_TEXT_MISSING")
    if "forbiddenText" in keys:
        forbidden = artifact["forbiddenText"]
        if not isinstance(forbidden, list) or any(not isinstance(item, str) or not item for item in forbidden):
            raise CandidateError("ARTIFACT_FORBIDDEN_TEXT_INVALID")
        if any(item in text for item in forbidden):
            raise CandidateError("ARTIFACT_FORBIDDEN_TEXT_PRESENT")
    return data


def repository_facts() -> dict[str, Any]:
    candidate_head = str(git("rev-parse", CANDIDATE_SUBJECT)).strip()
    candidate_tree, candidate_parents = commit_tuple(candidate_head)
    checkout_head = str(git("rev-parse", "HEAD")).strip()
    checkout_tree, checkout_parents = commit_tuple(checkout_head)
    return {
        "candidateHead": candidate_head,
        "candidateTree": candidate_tree,
        "candidateParents": candidate_parents,
        "candidateDiff": commit_diff(candidate_head),
        "checkoutHead": checkout_head,
        "checkoutTree": checkout_tree,
        "checkoutParents": checkout_parents,
        "checkoutDiff": commit_diff(checkout_head),
        "checkoutStatus": str(git("status", "--porcelain=v1", "--untracked-files=all")),
        "localMaster": str(git("rev-parse", "master")).strip(),
        "originMaster": str(git("rev-parse", "origin/master")).strip(),
        "originUrl": str(git("remote", "get-url", "origin")).strip(),
    }


def verify_repository_facts(facts: Any) -> None:
    facts = exact_keys(
        facts,
        {
            "candidateHead",
            "candidateTree",
            "candidateParents",
            "candidateDiff",
            "checkoutHead",
            "checkoutTree",
            "checkoutParents",
            "checkoutDiff",
            "checkoutStatus",
            "localMaster",
            "originMaster",
            "originUrl",
        },
        "REPOSITORY_FACT_FIELDS_INVALID",
    )
    if facts["candidateHead"] != CANDIDATE_SUBJECT:
        raise CandidateError("CANDIDATE_SUBJECT_INVALID")
    if facts["candidateTree"] != CANDIDATE_SUBJECT_TREE or facts["candidateParents"] != [BASE]:
        raise CandidateError("CANDIDATE_SUBJECT_TUPLE_INVALID")
    if facts["candidateDiff"] != CANDIDATE_DIFF:
        raise CandidateError("CANDIDATE_DIFF_INVALID")
    if (
        not isinstance(facts["checkoutHead"], str)
        or not SHA1.fullmatch(facts["checkoutHead"])
        or not isinstance(facts["checkoutTree"], str)
        or not SHA1.fullmatch(facts["checkoutTree"])
        or facts["checkoutHead"] == CANDIDATE_SUBJECT
        or facts["checkoutParents"] != [CANDIDATE_SUBJECT]
        or facts["checkoutDiff"] != HARDENING_DIFF
    ):
        raise CandidateError("CHECKOUT_NOT_EXACT_HARDENING_DESCENDANT")
    if facts["checkoutStatus"] != "":
        raise CandidateError("CHECKOUT_NOT_CLEAN")
    if facts["localMaster"] != BASE or facts["originMaster"] != BASE or facts["originUrl"] != REMOTE:
        raise CandidateError("PROJECT_BASE_REF_INVALID")


def verify_candidate(candidate: Any) -> None:
    candidate = exact_keys(
        candidate,
        {
            "schema",
            "status",
            "disposition",
            "subjectBinding",
            "doctrineEvidence",
            "projectBase",
            "portableInvariantsAcceptedAsTarget",
            "trackedRepositoryCensus",
            "currentProjectControls",
            "siblingProposalBoundary",
            "projectBoundaryArtifact",
            "nonRegressionBoundary",
            "missingAdoptionProofs",
            "authority",
            "smallestAutomatedNextAction",
        },
        "ROOT_FIELDS_INVALID",
    )
    if candidate["schema"] != SCHEMA or candidate["status"] != STATUS:
        raise CandidateError("CANDIDATE_IDENTITY_INVALID")
    if candidate["disposition"] != DISPOSITION:
        raise CandidateError("DISPOSITION_INVALID")
    if candidate["subjectBinding"] != SUBJECT_BINDING:
        raise CandidateError("SUBJECT_BINDING_INVALID")
    if candidate["doctrineEvidence"] != DOCTRINE_EVIDENCE:
        raise CandidateError("DOCTRINE_EVIDENCE_INVALID")
    if candidate["projectBase"] != PROJECT_BASE or commit_tuple(BASE) != (BASE_TREE, [BASE_PARENT]):
        raise CandidateError("PROJECT_BASE_INVALID")
    if candidate["portableInvariantsAcceptedAsTarget"] != PORTABLE_INVARIANTS:
        raise CandidateError("PORTABLE_INVARIANTS_INVALID")
    if candidate["trackedRepositoryCensus"] != CENSUS:
        raise CandidateError("TRACKED_CENSUS_INVALID")

    controls = candidate["currentProjectControls"]
    if not isinstance(controls, list) or [
        (item.get("path"), item.get("classification")) if isinstance(item, dict) else None for item in controls
    ] != CONTROL_CLASSIFICATIONS:
        raise CandidateError("CONTROL_SET_INVALID")
    for artifact in controls:
        verify_artifact(BASE, artifact, {"classification", "requiredText", "forbiddenText"})

    sibling = exact_keys(
        candidate["siblingProposalBoundary"],
        {"commit", "tree", "parent", "ancestorOfProjectBase", "status", "transferOfProof", "adoptionCredit", "artifacts"},
        "SIBLING_FIELDS_INVALID",
    )
    if {key: value for key, value in sibling.items() if key != "artifacts"} != {
        "commit": SIBLING_PROPOSAL,
        "tree": SIBLING_TREE,
        "parent": SIBLING_PARENT,
        "ancestorOfProjectBase": False,
        "status": "DISTINGUISH_PROJECT_R15_HOSTED_EVIDENCE_ZERO_AUTHORITY",
        "transferOfProof": False,
        "adoptionCredit": False,
    }:
        raise CandidateError("SIBLING_BOUNDARY_INVALID")
    if commit_tuple(SIBLING_PROPOSAL) != (SIBLING_TREE, [SIBLING_PARENT]):
        raise CandidateError("SIBLING_OBJECT_INVALID")
    if str(git("merge-base", SIBLING_PROPOSAL, BASE)).strip() != BASE:
        raise CandidateError("SIBLING_ANCESTRY_INVALID")
    sibling_artifacts = sibling["artifacts"]
    if not isinstance(sibling_artifacts, list) or [
        artifact.get("path") if isinstance(artifact, dict) else None for artifact in sibling_artifacts
    ] != SIBLING_ARTIFACT_PATHS:
        raise CandidateError("SIBLING_ARTIFACT_SET_INVALID")
    for artifact in sibling_artifacts:
        verify_artifact(SIBLING_PROPOSAL, artifact)

    boundary = candidate["projectBoundaryArtifact"]
    if not isinstance(boundary, dict) or boundary.get("path") != "AGENTS.md":
        raise CandidateError("PROJECT_BOUNDARY_INVALID")
    verify_artifact(BASE, boundary, {"requiredText"})
    if candidate["nonRegressionBoundary"] != NON_REGRESSION:
        raise CandidateError("NON_REGRESSION_INVALID")

    gaps = exact_keys(candidate["missingAdoptionProofs"], PROOF_GAPS, "PROOF_GAP_SET_INVALID")
    if any(value != "NOT_PRODUCED_OR_CREDITED_AT_THIS_CANDIDATE" for value in gaps.values()):
        raise CandidateError("FABRICATED_ADOPTION_PROOF")
    authority = exact_keys(candidate["authority"], AUTHORITY_FIELDS, "AUTHORITY_FIELDS_INVALID")
    if any(value is not False for value in authority.values()):
        raise CandidateError("ZERO_AUTHORITY_OVERCLAIM")
    if candidate["smallestAutomatedNextAction"] != NEXT_ACTION:
        raise CandidateError("NEXT_ACTION_INVALID")


def expect_refusal(candidate: dict[str, Any], mutate: Any, expected: str) -> None:
    changed = copy.deepcopy(candidate)
    mutate(changed)
    try:
        verify_candidate(changed)
    except CandidateError as exc:
        if expected not in str(exc):
            raise CandidateError(f"WRONG_MUTATION_REFUSAL:{exc}") from exc
        return
    raise CandidateError(f"MUTATION_SURVIVED:{expected}")


def expect_repository_refusal(facts: dict[str, Any], mutate: Any, expected: str) -> None:
    changed = copy.deepcopy(facts)
    mutate(changed)
    try:
        verify_repository_facts(changed)
    except CandidateError as exc:
        if expected not in str(exc):
            raise CandidateError(f"WRONG_REPOSITORY_MUTATION_REFUSAL:{exc}") from exc
        return
    raise CandidateError(f"REPOSITORY_MUTATION_SURVIVED:{expected}")


def run() -> None:
    try:
        candidate = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"), object_pairs_hook=exact_object)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CandidateError("CANDIDATE_JSON_INVALID") from exc
    facts = repository_facts()
    verify_repository_facts(facts)
    verify_candidate(candidate)

    expect_refusal(candidate, lambda value: value["disposition"].update(kind="ADOPT"), "DISPOSITION_INVALID")
    expect_refusal(
        candidate,
        lambda value: value["disposition"].update(
            statement=f"ADOPT({R26_MERGE}, MLV_APP_RUNTIME_ACTIVE, fabricated-review)"
        ),
        "DISPOSITION_INVALID",
    )
    expect_refusal(
        candidate,
        lambda value: value["portableInvariantsAcceptedAsTarget"].__setitem__(
            0, "MODEL_DOWNGRADE_AND_RUNTIME_ACTIVATION_ALLOWED"
        ),
        "PORTABLE_INVARIANTS_INVALID",
    )
    expect_refusal(
        candidate,
        lambda value: value["currentProjectControls"].__setitem__(
            1, copy.deepcopy(value["currentProjectControls"][0])
        ),
        "CONTROL_SET_INVALID",
    )
    expect_refusal(
        candidate,
        lambda value: value["siblingProposalBoundary"].update(transferOfProof=True),
        "SIBLING_BOUNDARY_INVALID",
    )
    expect_refusal(
        candidate,
        lambda value: value["nonRegressionBoundary"]["dimensions"][0].update(evidenceState="PROVEN"),
        "NON_REGRESSION_INVALID",
    )
    expect_refusal(
        candidate,
        lambda value: value["missingAdoptionProofs"].update(currentClosedGateProof="PROVEN"),
        "FABRICATED_ADOPTION_PROOF",
    )
    expect_refusal(
        candidate,
        lambda value: value["authority"].update(runtimeActivation=True),
        "ZERO_AUTHORITY_OVERCLAIM",
    )
    expect_refusal(
        candidate,
        lambda value: value["currentProjectControls"][0].update(sha256="sha256:" + "0" * 64),
        "ARTIFACT_SHA256_MISMATCH",
    )
    expect_repository_refusal(
        facts,
        lambda value: value.update(candidateHead="0" * 40),
        "CANDIDATE_SUBJECT_INVALID",
    )
    expect_repository_refusal(
        facts,
        lambda value: value.update(candidateTree="0" * 40),
        "CANDIDATE_SUBJECT_TUPLE_INVALID",
    )
    expect_repository_refusal(
        facts,
        lambda value: value["candidateDiff"].append(("M", "src/mlv/provider_runtime.c")),
        "CANDIDATE_DIFF_INVALID",
    )
    expect_repository_refusal(
        facts,
        lambda value: value["checkoutDiff"].append(("M", "tools/agent-bridge/watcher.py")),
        "CHECKOUT_NOT_EXACT_HARDENING_DESCENDANT",
    )
    expect_repository_refusal(
        facts,
        lambda value: value.update(checkoutStatus=" M tools/agent-bridge/watcher.py\n"),
        "CHECKOUT_NOT_CLEAN",
    )


def main() -> int:
    try:
        run()
    except CandidateError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: MLV-App R26 DISTINGUISH candidate is exact-bound and zero-authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
