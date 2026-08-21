#!/usr/bin/env python3
"""Sole CLOSED-by-default candidate launch boundary for MLV-App Claude lanes."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterator

try:
    import jsonschema
except Exception:  # pragma: no cover - normalized to a fail-closed reason
    jsonschema = None

from vendor.universal_provider_control import (
    ControlError, SafeArgumentParser, UniversalProviderBroker, canonical_json, digest_json,
    route_demand_tick, strict_json_file, validate_project_profile,
)

CANONICAL_TECHNICAL_SUBJECT = "e70a044f31dd2f43ab7c716d63a4eb89318c61b6"
CANONICAL_DOCTRINE_MERGE = "909f769d02e8412e51e28e242cfa8d00dadc9a3d"
MECHANICS_TECHNICAL_SUBJECT = "874605e43531c9aa230ee16851f8107a8e0d9cec"
MECHANICS_RATIFICATION_MERGE = "488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d"
# Compatibility aliases: lane bindings and the vendored engine still depend on exact R14 mechanics.
TECHNICAL_SUBJECT = MECHANICS_TECHNICAL_SUBJECT
RATIFICATION_MERGE = MECHANICS_RATIFICATION_MERGE
DOCTRINE_ENGINE_GIT_BLOB = "0e26b15f249f89972e2fc7807ccd0d98a0bd4954"
ATTENDED_RECEIPT_GIT_BLOB = "41d7b4c6ae56f8efb880a1f36f4c3225f3112251"
ATTENDED_RECEIPT_SHA256 = "sha256:b3c69cdd972b694bd37e914b6c8f11ec452ac3c8c14ed230c2a23215e1e01307"
TOKEN_POLICY_GIT_BLOB = "33b75cfb61b8c8934009ff987f2ea90ef5f74eb5"
TOKEN_POLICY_SHA256 = "sha256:b5eca2ef60a86d87e2ca96d34569035aef510387de7ea204ab7cbf98ce5f5192"
EXPECTED_PROFILE_SHA256 = "sha256:2767dbc8e48e41bfcca6101913dcb157b7ec05b6ef3ba1a8d4405b45f2010f2d"
EXPECTED_BINDINGS_SHA256 = "sha256:8f328778da046d8348a7161cf95d31b843cd58aaf89ee7989d6f60c5360e18b6"
EXPECTED_INVENTORY_SHA256 = "sha256:ce43663977e10d262632405078a3cb9e86dce8c123b064406e7b83e130bc760c"
EXPECTED_SUPERVISOR_PROFILE_SHA256 = "sha256:58f2c4a249067af356a9035bfb21babca5e7c6c087e378ece5ad442acd6dccd3"
PRODUCTION_STATE_ROOT = Path(r"C:\ProgramData\MLV-App\provider-control-v1")
DEFAULT_STATE_ROOT = PRODUCTION_STATE_ROOT
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "mlv-project-profile.candidate.json"
BINDINGS = ROOT / "lane-bindings.candidate.json"
INVENTORY = ROOT / "mlv-observed-inventory.candidate.json"
SUPERVISOR_PROFILE = ROOT / "mlv-supervisor-profile.candidate.json"
SCHEMAS = ROOT / "schemas"
FAKE_ENV = "MLV_PROVIDER_CONTROL_TEST_ONLY"
TEST_STATE_ROOT_ENV = "MLV_PROVIDER_CONTROL_TEST_STATE_ROOT"
DEMAND_KEYS = {"schema", "project", "hasWork", "lane", "priority", "estimateFraction",
               "availableFraction", "turns", "contextTokens", "capsuleSha256",
               "checkpointSha256", "cacheAffinitySha256"}
PRIORITIES = {"OWNER_FOREGROUND", "REQUIRED_REVIEW", "PRODUCT_WORK",
              "ADJUDICATION", "MAINTENANCE"}
TEST_MODES = {"SHADOW", "CONTAINMENT"}
TEST_SLOT_ORDER = ("fable", "claude-review", "opus", "sonnet-impl")
ZERO_SHA256 = "sha256:" + "0" * 64
ZERO_HMAC_SHA256 = "hmac-sha256:" + "0" * 64


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def require_digest(value: Any) -> str:
    if (not isinstance(value, str) or len(value) != 71 or
            not value.startswith("sha256:") or
            any(ch not in "0123456789abcdef" for ch in value[7:])):
        raise ControlError("DEMAND_BINDING_INVALID")
    if value == ZERO_SHA256:
        raise ControlError("DEMAND_BINDING_PLACEHOLDER")
    return value


def canonical_path_key(path: Path) -> str:
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))
    except (OSError, ValueError) as exc:
        raise ControlError("STATE_ROOT_IDENTITY_UNEVALUABLE") from exc


def path_has_reparse_component(path: Path) -> bool:
    candidate = Path(os.path.abspath(str(path)))
    for component in (candidate, *candidate.parents):
        try:
            is_junction = getattr(component, "is_junction", lambda: False)()
            if component.is_symlink() or is_junction:
                return True
        except OSError as exc:
            raise ControlError("STATE_ROOT_IDENTITY_UNEVALUABLE") from exc
    return False


def validate_local_contract(value: Any, schema_name: str) -> None:
    if jsonschema is None:
        raise ControlError("SCHEMA_VALIDATOR_UNAVAILABLE")
    schema = strict_json_file(SCHEMAS / schema_name)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        )
        if next(iter(validator.iter_errors(value)), None) is not None:
            raise ControlError("MLV_CONTRACT_SCHEMA_INVALID")
    except ControlError:
        raise
    except Exception as exc:
        raise ControlError("MLV_CONTRACT_SCHEMA_UNEVALUABLE") from exc


def _contains_placeholder_digest(value: Any) -> bool:
    if isinstance(value, str) and value in {ZERO_SHA256, ZERO_HMAC_SHA256}:
        return True
    if isinstance(value, dict):
        return any(_contains_placeholder_digest(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder_digest(item) for item in value)
    return False


def activation_blockers(
    profile: dict[str, Any], bindings: dict[str, Any],
    supervisor_profile: dict[str, Any], inventory: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    coordination = profile["coordination"]
    if profile["independenceClass"] == "SHARED_QUOTA_DOMAIN" and \
            coordination["sharedBrokerIdentitySha256"] is None:
        blockers.append("SHARED_BROKER_IDENTITY_UNKNOWN")
    if _contains_placeholder_digest(profile) or _contains_placeholder_digest(bindings):
        blockers.append("PLACEHOLDER_IDENTITY_BLOCKED")
    if inventory["complete"] is not True or inventory["status"] != "READY":
        blockers.append("INVENTORY_INCOMPLETE")
    if inventory["actionGraph"]["complete"] is not True:
        blockers.append("ACTION_GRAPH_INCOMPLETE")
    if inventory["ignitionLauncher"]["brokerRouted"] is not True or \
            inventory["ignitionLauncher"]["directProviderInvocation"] is not False:
        blockers.append("DIRECT_LAUNCHER_OBSERVED")
    if any(item["identity"]["status"] != "EXACT"
           for item in inventory["livePromptObservations"] + inventory["claudeCliObservations"]):
        blockers.append("OBSERVED_IDENTITY_UNKNOWN")
    if inventory["scheduledTasks"]["primary"]["exportedXml"]["status"] != "EXACT":
        blockers.append("TASK_XML_CANONICALIZATION_UNKNOWN")
    if inventory["agentBridge"]["complete"] is not True:
        blockers.append("AGENT_BRIDGE_SOURCE_DIVERGENCE")
    if inventory["surfaceClosure"]["status"] != "READY":
        blockers.append("SURFACE_CLOSURE_BLOCKED")
    if supervisor_profile["pending"]:
        blockers.append("SUPERVISOR_PROFILE_PENDING")
    return blockers


def load_contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    if sha256_file(PROFILE) != EXPECTED_PROFILE_SHA256:
        raise ControlError("PROJECT_PROFILE_DIGEST_MISMATCH")
    if sha256_file(BINDINGS) != EXPECTED_BINDINGS_SHA256:
        raise ControlError("LANE_BINDINGS_DIGEST_MISMATCH")
    if sha256_file(INVENTORY) != EXPECTED_INVENTORY_SHA256:
        raise ControlError("OBSERVED_INVENTORY_DIGEST_MISMATCH")
    if sha256_file(SUPERVISOR_PROFILE) != EXPECTED_SUPERVISOR_PROFILE_SHA256:
        raise ControlError("SUPERVISOR_PROFILE_DIGEST_MISMATCH")
    profile, bindings = strict_json_file(PROFILE), strict_json_file(BINDINGS)
    inventory = strict_json_file(INVENTORY)
    supervisor_profile = strict_json_file(SUPERVISOR_PROFILE)
    validate_project_profile(profile)
    validate_local_contract(inventory, "mlv-provider-observed-inventory-v2.schema.json")
    validate_local_contract(supervisor_profile, "mlv-provider-supervisor-profile-v1.schema.json")
    if (set(bindings) != {"schema", "technicalSubject", "ratificationMerge",
                          "doctrineEngineGitBlob", "provider",
                          "adapter", "quotaDomain", "canonicalStateRoot", "profileSha256",
                          "stateRootIdentity", "lanes"} or
            bindings["schema"] != "mlv-provider-lane-bindings/v1" or
            bindings["technicalSubject"] != TECHNICAL_SUBJECT or
            bindings["ratificationMerge"] != RATIFICATION_MERGE or
            bindings["doctrineEngineGitBlob"] != DOCTRINE_ENGINE_GIT_BLOB or
            bindings["provider"] != "claude" or bindings["adapter"] != "claude-code/1.0" or
            bindings["quotaDomain"] != "claude-shared-account" or
            bindings["canonicalStateRoot"] != str(PRODUCTION_STATE_ROOT) or
            bindings["profileSha256"] != EXPECTED_PROFILE_SHA256 or
            bindings["stateRootIdentity"] != profile["coordination"]["stateRootIdentity"] or
            not isinstance(bindings["lanes"], dict)):
        raise ControlError("LANE_BINDINGS_INVALID")
    keys = {"model", "effort", "role", "subject", "subjectSha256"}
    for lane, binding in bindings["lanes"].items():
        if not isinstance(lane, str) or not isinstance(binding, dict) or set(binding) != keys:
            raise ControlError("LANE_BINDINGS_INVALID")
        subject_input = ROOT / binding["subject"]
        if subject_input.is_symlink():
            raise ControlError("FROZEN_SUBJECT_INVALID")
        subject = subject_input.resolve(strict=True)
        if ROOT not in subject.parents or not subject.is_file():
            raise ControlError("FROZEN_SUBJECT_INVALID")
        if (binding["effort"] != "high" or
                not all(isinstance(binding[k], str) and binding[k] for k in keys) or
                sha256_file(subject) != binding["subjectSha256"]):
            raise ControlError("LANE_BINDINGS_INVALID")
    digests = supervisor_profile["contractDigests"]
    if (supervisor_profile["doctrine"] != {
            "canonicalUniversal": {
                "generation": "R26",
                "technicalSubject": CANONICAL_TECHNICAL_SUBJECT,
                "doctrineMerge": CANONICAL_DOCTRINE_MERGE,
                "automaticLaunchGate": "CLOSED",
                "authority": False,
            },
            "mechanicsDependency": {
                "generation": "R14",
                "technicalSubject": MECHANICS_TECHNICAL_SUBJECT,
                "ratificationMerge": MECHANICS_RATIFICATION_MERGE,
                "scope": "EXACT_VENDOR_ENGINE_AND_LANE_BINDINGS_ONLY",
                "authority": False,
            },
        } or supervisor_profile["tokenSavingEvidence"] != {
            "treatment": "MOTIVATION_ONLY",
            "authority": False,
            "attendedRotation": {
                "path": "receipts/attended-provider-rotation-20260819.json",
                "gitBlobOid": ATTENDED_RECEIPT_GIT_BLOB,
                "sha256": ATTENDED_RECEIPT_SHA256,
                "provenance": "AUTHOR_ATTESTED_LOCAL_CLI_MEASUREMENT",
                "credit": "MOTIVATION_AND_MEASUREMENT_ONLY",
                "providerAuthenticated": False,
                "independentObserver": False,
                "rawProviderReceiptCommitted": False,
                "inputTokens": 7,
                "cacheCreateTokens": 59319,
                "cacheReadTokens": 10723,
                "outputTokens": 7540,
            },
            "policy": {
                "path": "policy/universal-provider-token-control-r22.json",
                "gitBlobOid": TOKEN_POLICY_GIT_BLOB,
                "sha256": TOKEN_POLICY_SHA256,
                "policyId": "universal-provider-token-control-r22",
                "providerAuthority": False,
                "noWorkDecisionPoint": "BEFORE_PROCESS_OR_SESSION_CREATION",
                "requestAccountingScope": "PROVIDER_REQUEST",
                "cacheReadEnvelopeWeight": 1.0,
                "maxAssembledPrefixTokens": 32768,
                "maxAddressedWorkCapsuleTokens": 8192,
                "cacheAffinityTtlSeconds": 300,
                "maxProviderRetries": 1,
                "completionReserveFloor": 0.20,
                "directLaunchSeparateCertificationRequired": True,
            },
        } or digests["universalProfileSha256"] != EXPECTED_PROFILE_SHA256 or
            digests["laneBindingsSha256"] != EXPECTED_BINDINGS_SHA256 or
            digests["observedInventorySha256"] != EXPECTED_INVENTORY_SHA256):
        raise ControlError("SUPERVISOR_PROFILE_BINDING_INVALID")
    return profile, bindings, supervisor_profile, inventory


def enforce_state_root(
    state_root: Path,
    profile: dict[str, Any],
    bindings: dict[str, Any],
    *,
    test_only: bool = False,
) -> Path:
    expected = DEFAULT_STATE_ROOT
    if test_only:
        override = os.environ.get(TEST_STATE_ROOT_ENV)
        if not override:
            raise ControlError("TEST_STATE_ROOT_UNBOUND")
        expected = Path(override)
        if path_has_reparse_component(state_root) or path_has_reparse_component(expected):
            raise ControlError("TEST_STATE_ROOT_REPARSE_BLOCKED")
        production_key = canonical_path_key(PRODUCTION_STATE_ROOT)
        if (canonical_path_key(state_root) == production_key or
                canonical_path_key(expected) == production_key):
            raise ControlError("TEST_STATE_ROOT_PRODUCTION_ALIAS")
    if (not state_root.is_absolute() or not expected.is_absolute() or
            canonical_path_key(state_root) != canonical_path_key(expected)):
        raise ControlError("STATE_ROOT_IDENTITY_MISMATCH")
    if (sha256_file(PROFILE) != bindings["profileSha256"] or
            profile["coordination"]["stateRootIdentity"] != bindings["stateRootIdentity"]):
        raise ControlError("STATE_ROOT_PROFILE_IDENTITY_MISMATCH")
    return state_root


def validate_demand(value: Any, profile: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != DEMAND_KEYS:
        raise ControlError("DEMAND_INVALID")
    if (value["schema"] != "mlv-provider-demand/v1" or value["project"] != "mlv-app" or
            type(value["hasWork"]) is not bool or value["lane"] not in bindings["lanes"] or
            value["priority"] not in PRIORITIES):
        raise ControlError("DEMAND_INVALID")
    for field in ("estimateFraction", "availableFraction"):
        if type(value[field]) not in {int, float} or not 0 <= float(value[field]) <= 1:
            raise ControlError("DEMAND_INVALID")
    if type(value["turns"]) is not int or not 0 <= value["turns"] <= profile["efficiency"]["maxTurns"]:
        raise ControlError("TURN_BUDGET_REFUSED")
    if (type(value["contextTokens"]) is not int or
            not 0 <= value["contextTokens"] <= profile["efficiency"]["maxContextTokens"]):
        raise ControlError("CONTEXT_BUDGET_REFUSED")
    for field in ("capsuleSha256", "checkpointSha256", "cacheAffinitySha256"):
        require_digest(value[field])
    return value


def read_prior_idle(state_root: Path) -> dict[str, str] | None:
    path = state_root / "idle-fingerprint.json"
    if not path.exists():
        return None
    value = strict_json_file(path)
    if (not isinstance(value, dict) or
            set(value) != {"schema", "fingerprintType", "demandType", "demandFingerprint"} or
            value["schema"] != "mlv-provider-idle-state/v1"):
        raise ControlError("IDLE_STATE_INVALID")
    if value["fingerprintType"] != "CANONICAL_DEMAND_V1" or value["demandType"] != "NO_WORK":
        raise ControlError("IDLE_STATE_INVALID")
    require_digest(value["demandFingerprint"])
    return value


def write_idle(state_root: Path, fingerprint: str) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    target, temp = state_root / "idle-fingerprint.json", state_root / "idle-fingerprint.json.tmp-owned"
    payload = (canonical_json({"schema": "mlv-provider-idle-state/v1",
                              "fingerprintType": "CANONICAL_DEMAND_V1",
                              "demandType": "NO_WORK",
                              "demandFingerprint": fingerprint}) + "\n").encode()
    try:
        with temp.open("xb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, target)
    except FileExistsError as exc:
        raise ControlError("IDLE_STATE_BUSY") from exc
    except OSError as exc:
        raise ControlError("IDLE_STATE_UNEVALUABLE") from exc


def closed_result(broker: UniversalProviderBroker, fingerprint: str) -> dict[str, Any]:
    gate = broker.gate_state()
    if gate != "CLOSED":
        # Author is recused from adding the separately adjudicated suspended-child resume seam.
        raise ControlError("PRODUCTION_RESUME_BOUNDARY_NOT_ADJUDICATED")
    return {"status": "REFUSED", "reason": "AUTOMATIC_LAUNCH_GATE_CLOSED",
            "automaticLaunchGate": "CLOSED", "demandFingerprint": fingerprint,
            "providerCalls": 0, "providerProcesses": 0, "inputTokens": 0,
            "cachedInputTokens": 0, "reasoningTokens": 0, "outputTokens": 0}


def blocked_result(fingerprint: str, blockers: list[str]) -> dict[str, Any]:
    return {"status": "REFUSED", "reason": "ACTIVATION_EVIDENCE_BLOCKED",
            "automaticLaunchGate": "CLOSED", "disposition": "DISTINGUISH",
            "blockers": blockers, "demandFingerprint": fingerprint,
            "providerCalls": 0, "providerProcesses": 0, "inputTokens": 0,
            "cachedInputTokens": 0, "reasoningTokens": 0, "outputTokens": 0}


def _tick(state_root: Path, demand_path: Path, *, shadow: bool) -> dict[str, Any]:
    profile, bindings, supervisor_profile, inventory = load_contracts()
    enforce_state_root(state_root, profile, bindings, test_only=shadow)
    demand = validate_demand(strict_json_file(demand_path), profile, bindings)
    fingerprint = digest_json(demand)
    blockers = activation_blockers(profile, bindings, supervisor_profile, inventory)
    if blockers and not shadow:
        return blocked_result(fingerprint, blockers)
    if not demand["hasWork"]:
        prior = read_prior_idle(state_root)
        if prior is None:
            status = "IDLE_RECORDED"
        elif prior["demandFingerprint"] == fingerprint:
            return route_demand_tick(fingerprint, prior["demandFingerprint"], lambda: None)
        else:
            status = "IDLE_CHANGED"
        if prior is None or prior["demandFingerprint"] != fingerprint:
            write_idle(state_root, fingerprint)
        return {"status": status, "demandFingerprint": fingerprint, "providerCalls": 0,
                "providerProcesses": 0, "inputTokens": 0, "cachedInputTokens": 0,
                "reasoningTokens": 0, "outputTokens": 0}
    broker = UniversalProviderBroker(state_root)
    prior = read_prior_idle(state_root)
    prior_fingerprint = None if prior is None else prior["demandFingerprint"]
    return route_demand_tick(fingerprint, prior_fingerprint,
                             lambda: closed_result(broker, fingerprint))


def tick(state_root: Path, demand_path: Path) -> dict[str, Any]:
    return _tick(state_root, demand_path, shadow=False)


_ZERO_FIELDS = (
    "providerCalls", "providerProcesses", "inputTokens", "cachedInputTokens",
    "reasoningTokens", "outputTokens",
)


def enforce_prelaunch_stop(result: Any) -> dict[str, Any]:
    """Translate only an exact non-launch decision into a production stop receipt.

    This is deliberately one-way: it has no authorization result and cannot become a
    provider-resume seam.  Any new/unknown supervisor result must be reviewed before a
    launcher can consume it.
    """
    if type(result) is not dict:
        raise ControlError("PRELAUNCH_RESULT_INVALID")
    common = {
        "status", "reason", "automaticLaunchGate", "demandFingerprint", *_ZERO_FIELDS,
    }
    blocked = common | {"disposition", "blockers"}
    idle = {"status", "demandFingerprint", *_ZERO_FIELDS}
    keys = set(result)
    source_status = result.get("status")
    source_reason: str
    blockers: list[str] | None = None
    if keys == blocked:
        if (
            source_status != "REFUSED"
            or result.get("reason") != "ACTIVATION_EVIDENCE_BLOCKED"
            or result.get("automaticLaunchGate") != "CLOSED"
            or result.get("disposition") != "DISTINGUISH"
            or type(result.get("blockers")) is not list
            or not result["blockers"]
            or any(type(item) is not str or not item for item in result["blockers"])
        ):
            raise ControlError("PRELAUNCH_RESULT_INVALID")
        source_reason = "ACTIVATION_EVIDENCE_BLOCKED"
        blockers = list(result["blockers"])
    elif keys == common:
        if (
            source_status != "REFUSED"
            or result.get("reason") != "AUTOMATIC_LAUNCH_GATE_CLOSED"
            or result.get("automaticLaunchGate") != "CLOSED"
        ):
            raise ControlError("PRELAUNCH_RESULT_INVALID")
        source_reason = "AUTOMATIC_LAUNCH_GATE_CLOSED"
    elif keys == idle:
        if source_status not in {"IDLE_RECORDED", "IDLE_CHANGED", "IDLE_SKIPPED"}:
            raise ControlError("PRELAUNCH_RESULT_INVALID")
        source_reason = "NO_WORK"
    else:
        raise ControlError("PRELAUNCH_RESULT_INVALID")
    fingerprint = result.get("demandFingerprint")
    if (
        type(fingerprint) is not str
        or len(fingerprint) != 71
        or not fingerprint.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in fingerprint[7:])
        or any(type(result.get(field)) is not int or result[field] != 0 for field in _ZERO_FIELDS)
    ):
        raise ControlError("PRELAUNCH_RESULT_INVALID")
    stop = {
        "schema": "mlv-provider-prelaunch-stop/v1",
        "status": "STOPPED",
        "reason": source_reason,
        "sourceStatus": source_status,
        "automaticLaunchGate": "CLOSED",
        "demandFingerprint": fingerprint,
        **{field: 0 for field in _ZERO_FIELDS},
    }
    if blockers is not None:
        stop["blockers"] = blockers
    return stop


def prelaunch_boundary(state_root: Path, demand_path: Path) -> dict[str, Any]:
    return enforce_prelaunch_stop(tick(state_root, demand_path))


def shadow_tick(state_root: Path, demand_path: Path) -> dict[str, Any]:
    if os.environ.get(FAKE_ENV) != "1":
        raise ControlError("TEST_SEAM_DISABLED")
    return _tick(state_root, demand_path, shadow=True)


@contextmanager
def quota_lock(state_root: Path) -> Iterator[None]:
    state_root.mkdir(parents=True, exist_ok=True)
    handle = (state_root / "claude-shared-account.quota.lock").open("a+b")
    if handle.seek(0, 2) == 0:
        handle.write(b"\0"); handle.flush()
    handle.seek(0)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1); locked = True
            except OSError as exc:
                raise ControlError("QUOTA_DOMAIN_BUSY") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); locked = True
            except OSError as exc:
                raise ControlError("QUOTA_DOMAIN_BUSY") from exc
        yield
    finally:
        try:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def conservative_test_envelope(
    demand: dict[str, Any], profile: dict[str, Any], supervisor_profile: dict[str, Any],
) -> dict[str, Any]:
    """Return a test-only, deliberately conservative per-request capacity reservation."""
    policy = supervisor_profile["tokenSavingEvidence"]["policy"]
    if (policy["cacheReadEnvelopeWeight"] != 1.0 or
            policy["completionReserveFloor"] != 0.20 or
            policy["maxProviderRetries"] != 1):
        raise ControlError("TEST_ENVELOPE_POLICY_DRIFT")
    estimate = float(demand["estimateFraction"])
    fresh_input = estimate
    cache_read = round(estimate * float(policy["cacheReadEnvelopeWeight"]), 12)
    cache_create = estimate
    completion = float(policy["completionReserveFloor"])
    envelope_total = round(fresh_input + cache_read + cache_create + completion, 12)
    consumptive_total = round(fresh_input + cache_read + cache_create, 12)
    priority_floor = float(profile["policy"]["reserveFloorByPriority"][demand["priority"]])
    protected_reserve = max(completion, priority_floor)
    required = round(consumptive_total + protected_reserve, 12)
    maximum_attempts = int(policy["maxProviderRetries"]) + 1
    maximum_required = round(consumptive_total * maximum_attempts + protected_reserve, 12)
    result = {
        "schema": "mlv-test-request-envelope/v1",
        "freshInputFraction": fresh_input,
        "cacheReadFraction": cache_read,
        "cacheCreateFraction": cache_create,
        "completionReserveFraction": completion,
        "priorityReserveFloorFraction": priority_floor,
        "envelopeTotalFraction": envelope_total,
        "consumptiveFractionPerAttempt": consumptive_total,
        "protectedReserveFraction": protected_reserve,
        "requiredAvailableFraction": required,
        "maximumAttemptsRequiredAvailableFraction": maximum_required,
        "availableFraction": float(demand["availableFraction"]),
        "maxRetries": int(policy["maxProviderRetries"]),
        "authority": "TEST_ONLY_ZERO_PRODUCTION_AUTHORITY",
    }
    if result["availableFraction"] < result["requiredAvailableFraction"]:
        raise ControlError("CAPACITY_RESERVE_REFUSED")
    return result


def _publish_test_attempt_reservation(
    state_root: Path, envelope: dict[str, Any], attempt: int,
    available_before: float, consumed_before: float,
) -> dict[str, Any]:
    active = state_root / "test-claude-slot-active.json"
    reservation = state_root / "test-request-envelope-active.json"
    if not active.is_file() or not reservation.is_file():
        raise ControlError("TEST_TERMINAL_CLEANUP_INCOMPLETE")
    required = float(envelope["requiredAvailableFraction"])
    if available_before < required:
        raise ControlError(
            "CAPACITY_RETRY_RESERVE_REFUSED" if attempt > 1 else "CAPACITY_RESERVE_REFUSED")
    value = {
        **envelope,
        "attempt": attempt,
        "availableBeforeAttemptFraction": available_before,
        "consumedBeforeAttemptFraction": consumed_before,
        "conservativeChargeFraction": envelope["consumptiveFractionPerAttempt"],
        "availableAfterConservativeChargeFraction": round(
            available_before - float(envelope["consumptiveFractionPerAttempt"]), 12),
    }
    _write_owned_json(reservation, value)
    return value


def _write_owned_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(path.name + ".tmp-owned")
    payload = (canonical_json(value) + "\n").encode("utf-8")
    try:
        with temp.open("xb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    except FileExistsError as exc:
        raise ControlError("TEST_SLOT_STATE_BUSY") from exc
    except OSError as exc:
        raise ControlError("TEST_SLOT_STATE_UNEVALUABLE") from exc


def _read_test_slot_cursor(state_root: Path) -> dict[str, Any]:
    path = state_root / "test-claude-slot-cursor.json"
    if not path.exists():
        return {"schema": "mlv-test-claude-slot-cursor/v1",
                "nextLane": TEST_SLOT_ORDER[0], "generation": 0}
    value = strict_json_file(path)
    if (not isinstance(value, dict) or
            set(value) != {"schema", "nextLane", "generation"} or
            value["schema"] != "mlv-test-claude-slot-cursor/v1" or
            value["nextLane"] not in TEST_SLOT_ORDER or
            type(value["generation"]) is not int or value["generation"] < 0):
        raise ControlError("TEST_SLOT_STATE_INVALID")
    return value


def _begin_test_slot(
    state_root: Path, lane: str, envelope: dict[str, Any], binding_digest: str,
) -> dict[str, Any]:
    cursor = _read_test_slot_cursor(state_root)
    if lane != cursor["nextLane"]:
        raise ControlError("ROTATING_SLOT_NOT_CURRENT")
    active = state_root / "test-claude-slot-active.json"
    reservation = state_root / "test-request-envelope-active.json"
    if active.exists() or reservation.exists():
        raise ControlError("TEST_TERMINAL_CLEANUP_INCOMPLETE")
    active_value = {
        "schema": "mlv-test-claude-slot-active/v1", "lane": lane,
        "generation": cursor["generation"], "bindingSha256": binding_digest,
        "authority": "TEST_ONLY_ZERO_PRODUCTION_AUTHORITY",
    }
    try:
        _write_owned_json(active, active_value)
        _write_owned_json(reservation, envelope)
    except BaseException:
        active.unlink(missing_ok=True); reservation.unlink(missing_ok=True)
        raise
    return cursor


def _finish_test_slot(state_root: Path, cursor: dict[str, Any]) -> None:
    active = state_root / "test-claude-slot-active.json"
    reservation = state_root / "test-request-envelope-active.json"
    current = TEST_SLOT_ORDER.index(cursor["nextLane"])
    next_value = {
        "schema": "mlv-test-claude-slot-cursor/v1",
        "nextLane": TEST_SLOT_ORDER[(current + 1) % len(TEST_SLOT_ORDER)],
        "generation": cursor["generation"] + 1,
    }
    # Publish rotation before removing either replay fence. A failed cursor write must leave
    # both records behind so restart cannot claim the same lane/generation again.
    _write_owned_json(state_root / "test-claude-slot-cursor.json", next_value)
    try:
        active.unlink(missing_ok=True)
        reservation.unlink(missing_ok=True)
    except OSError as exc:
        raise ControlError("TEST_TERMINAL_CLEANUP_FAILED") from exc


def _materialize_test_launch_artifact(
    state_root: Path, source: Path, expected_sha256: str,
) -> Path:
    try:
        source_bytes = source.read_bytes()
    except OSError as exc:
        raise ControlError("TEST_PROVIDER_IDENTITY_INVALID") from exc
    actual = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    if actual != expected_sha256:
        raise ControlError("LAUNCH_BINDING_CHANGED")
    directory = state_root / "test-launch-artifacts"
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise ControlError("TEST_LAUNCH_ARTIFACT_UNEVALUABLE") from exc
    artifact = directory / (actual[7:] + ".py")
    if artifact.exists():
        if artifact.is_symlink() or not artifact.is_file() or sha256_file(artifact) != actual:
            raise ControlError("TEST_LAUNCH_ARTIFACT_INVALID")
        return artifact
    try:
        with artifact.open("xb") as handle:
            handle.write(source_bytes); handle.flush(); os.fsync(handle.fileno())
        artifact.chmod(0o400)
    except FileExistsError:
        if artifact.is_symlink() or not artifact.is_file() or sha256_file(artifact) != actual:
            raise ControlError("TEST_LAUNCH_ARTIFACT_INVALID")
    except OSError as exc:
        artifact.unlink(missing_ok=True)
        raise ControlError("TEST_LAUNCH_ARTIFACT_UNEVALUABLE") from exc
    if sha256_file(artifact) != actual:
        raise ControlError("TEST_LAUNCH_ARTIFACT_INVALID")
    return artifact


def _broker_owned_test_plan(
    binding: dict[str, str], subject: Path, delay: float, mode: str, attempt: int,
    launch_script: Path | None = None,
) -> dict[str, Any]:
    source = (ROOT / "tests" / "fake_provider.py").resolve(strict=True)
    fake = (launch_script or source).resolve(strict=True)
    process_image = Path(sys.executable).resolve(strict=True)
    if (not process_image.is_file() or source.is_symlink() or not source.is_file() or
            fake.is_symlink() or not fake.is_file()):
        raise ControlError("TEST_PROVIDER_IDENTITY_INVALID")
    argv = [str(process_image), str(fake), "--model", binding["model"],
            "--effort", binding["effort"], "--role", binding["role"],
            "--subject", str(subject), "--sleep", str(delay),
            "--harness-mode", mode, "--attempt", str(attempt)]
    return {
        "ownership": "BROKER_OWNED_TEST_REFERENCE",
        "processImagePath": str(process_image),
        "processImageSha256": sha256_file(process_image),
        "scriptPath": str(fake), "scriptSha256": sha256_file(fake),
        "sourceScriptPath": str(source), "sourceScriptSha256": sha256_file(source),
        "subjectPath": str(subject), "subjectSha256": sha256_file(subject),
        "argv": argv,
        "argvSha256": "sha256:" + hashlib.sha256(canonical_json(argv).encode()).hexdigest(),
        "attempt": attempt,
    }


def run_test_fake(
    state_root: Path, demand_path: Path, fake_path: Path, delay: float, mode: str = "SHADOW",
) -> dict[str, Any]:
    if os.environ.get(FAKE_ENV) != "1":
        raise ControlError("TEST_SEAM_DISABLED")
    if mode not in TEST_MODES:
        raise ControlError("TEST_MODE_INVALID")
    profile, bindings, supervisor_profile, inventory = load_contracts()
    enforce_state_root(state_root, profile, bindings, test_only=True)
    if not activation_blockers(profile, bindings, supervisor_profile, inventory):
        raise ControlError("TEST_REQUIRES_BLOCKED_ACTIVATION")
    demand = validate_demand(strict_json_file(demand_path), profile, bindings)
    if not demand["hasWork"]:
        return {
            "status": "TEST_NO_WORK_SKIPPED", "harnessMode": mode,
            "authority": "TEST_ONLY_ZERO_PRODUCTION_AUTHORITY",
            "automaticLaunchGate": "CLOSED",
            "providerCalls": 0, "providerProcesses": 0,
            "fakeProviderCalls": 0, "fakeProviderProcesses": 0,
            "inputTokens": 0, "cachedInputTokens": 0,
            "reasoningTokens": 0, "outputTokens": 0,
            "reservationCreated": False, "slotClaimed": False,
            "demandFingerprint": digest_json(demand),
        }
    binding = bindings["lanes"][demand["lane"]]
    envelope = conservative_test_envelope(demand, profile, supervisor_profile)
    if fake_path.is_symlink():
        raise ControlError("TEST_PROVIDER_IDENTITY_INVALID")
    caller_fake = fake_path.resolve(strict=True)
    broker_fake = (ROOT / "tests" / "fake_provider.py").resolve(strict=True)
    if caller_fake != broker_fake:
        raise ControlError("TEST_PROVIDER_IDENTITY_INVALID")
    subject = (ROOT / binding["subject"]).resolve(strict=True)
    initial_plan = _broker_owned_test_plan(binding, subject, delay, mode, 1)
    if initial_plan["subjectSha256"] != binding["subjectSha256"]:
        raise ControlError("LAUNCH_BINDING_CHANGED")
    with quota_lock(state_root):
        fresh_profile, fresh_bindings, fresh_supervisor_profile, fresh_inventory = load_contracts()
        enforce_state_root(state_root, fresh_profile, fresh_bindings, test_only=True)
        if not activation_blockers(
                fresh_profile, fresh_bindings, fresh_supervisor_profile, fresh_inventory):
            raise ControlError("ACTIVATION_EVIDENCE_CHANGED")
        fresh_binding = fresh_bindings["lanes"].get(demand["lane"])
        if fresh_binding != binding:
            raise ControlError("LAUNCH_BINDING_CHANGED")
        fresh_subject = (ROOT / fresh_binding["subject"]).resolve(strict=True)
        fresh_envelope = conservative_test_envelope(
            demand, fresh_profile, fresh_supervisor_profile)
        fresh_plan = _broker_owned_test_plan(fresh_binding, fresh_subject, delay, mode, 1)
        if (fresh_subject != subject or fresh_envelope != envelope or
                fake_path.is_symlink() or fake_path.resolve(strict=True) != caller_fake or
                fresh_plan != initial_plan or
                fresh_plan["subjectSha256"] != fresh_binding["subjectSha256"]):
            raise ControlError("LAUNCH_BINDING_CHANGED")

        launch_artifact = _materialize_test_launch_artifact(
            state_root, broker_fake, initial_plan["sourceScriptSha256"])
        expected_plans = [
            _broker_owned_test_plan(binding, subject, delay, mode, attempt, launch_artifact)
            for attempt in range(1, envelope["maxRetries"] + 2)
        ]

        binding_digest = "sha256:" + hashlib.sha256(canonical_json({
            "binding": binding,
            "sourcePlan": initial_plan,
            "launchPlans": expected_plans,
            "requestEnvelope": envelope,
        }).encode()).hexdigest()
        cursor = _begin_test_slot(state_root, demand["lane"], envelope, binding_digest)
        try:
            attempt_bindings = []
            max_attempts = envelope["maxRetries"] + 1
            consumed_fraction = 0.0
            for attempt in range(1, max_attempts + 1):
                available_before = round(
                    float(envelope["availableFraction"]) - consumed_fraction, 12)
                attempt_reservation = _publish_test_attempt_reservation(
                    state_root, envelope, attempt, available_before, consumed_fraction)
                plan = _broker_owned_test_plan(
                    binding, subject, delay, mode, attempt, launch_artifact)
                computed_argv_digest = "sha256:" + hashlib.sha256(
                    canonical_json(plan["argv"]).encode()).hexdigest()
                if (plan != expected_plans[attempt - 1] or
                        plan["argvSha256"] != computed_argv_digest or
                        fake_path.is_symlink() or fake_path.resolve(strict=True) != caller_fake):
                    raise ControlError("LAUNCH_BINDING_CHANGED")
                attempt_bindings.append({
                    "attempt": attempt, "argvSha256": plan["argvSha256"],
                    "processImageSha256": plan["processImageSha256"],
                    "scriptSha256": plan["scriptSha256"],
                    "subjectSha256": plan["subjectSha256"],
                })
                process = subprocess.Popen(
                    plan["argv"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.communicate()
                    consumed_fraction = round(
                        consumed_fraction + float(envelope["consumptiveFractionPerAttempt"]), 12)
                    if attempt < max_attempts:
                        continue
                    raise ControlError("FAKE_PROVIDER_TIMEOUT_CONTAINED") from exc
                consumed_fraction = round(
                    consumed_fraction + float(envelope["consumptiveFractionPerAttempt"]), 12)
                if process.returncode != 0 or stderr:
                    if attempt < max_attempts:
                        continue
                    raise ControlError("TEST_PROVIDER_FAILED")
                try:
                    receipt = json.loads(stdout)
                except (TypeError, ValueError) as exc:
                    raise ControlError("TEST_PROVIDER_RECEIPT_INVALID") from exc
                expected_receipt = {
                    "model": binding["model"], "effort": binding["effort"],
                    "role": binding["role"], "subject": str(subject),
                    "harnessMode": mode, "attempt": attempt,
                    "provider": "FAKE_ONLY",
                    "processImagePath": plan["processImagePath"],
                    "scriptPath": plan["scriptPath"],
                    "requestedAuthority": (
                        "OPEN_GATE_AND_ADOPT" if mode == "CONTAINMENT" else "NONE"),
                }
                if receipt != expected_receipt:
                    raise ControlError("TEST_PROVIDER_RECEIPT_INVALID")
                if UniversalProviderBroker(state_root).gate_state() != "CLOSED":
                    raise ControlError("TEST_CHANGED_AUTOMATIC_GATE")
                return {
                    "status": "TEST_FAKE_COMPLETED", "harnessMode": mode,
                    "authority": "TEST_ONLY_ZERO_PRODUCTION_AUTHORITY",
                    "automaticLaunchGate": "CLOSED",
                    "providerCalls": 0, "providerProcesses": 0,
                    "fakeProviderCalls": attempt, "fakeProviderProcesses": attempt,
                    "inputTokens": 0, "cachedInputTokens": 0,
                    "reasoningTokens": 0, "outputTokens": 0,
                    "containedHostileAuthorityClaim": mode == "CONTAINMENT",
                    "exitCode": process.returncode,
                    "requestEnvelope": envelope,
                    "attemptReservation": attempt_reservation,
                    "conservativeChargedFraction": consumed_fraction,
                    "availableAfterConservativeChargeFraction": round(
                        float(envelope["availableFraction"]) - consumed_fraction, 12),
                    "retryCount": attempt - 1, "maxRetries": envelope["maxRetries"],
                    "slot": {"lane": demand["lane"], "generation": cursor["generation"],
                             "rotationOrder": list(TEST_SLOT_ORDER)},
                    "callerProviderPathUsedForLaunch": False,
                    "binding": {
                        "ownership": plan["ownership"],
                        "model": binding["model"], "effort": binding["effort"],
                        "role": binding["role"], "subjectSha256": plan["subjectSha256"],
                        "processImagePath": plan["processImagePath"],
                        "processImageSha256": plan["processImageSha256"],
                        "scriptPath": plan["scriptPath"],
                        "scriptSha256": plan["scriptSha256"],
                        "sourceScriptPath": plan["sourceScriptPath"],
                        "sourceScriptSha256": plan["sourceScriptSha256"],
                        "argvSha256": plan["argvSha256"],
                        "bindingSha256": binding_digest,
                    },
                    "attemptBindings": attempt_bindings, "fakeReceipt": receipt,
                }
            raise ControlError("TEST_PROVIDER_FAILED")
        finally:
            _finish_test_slot(state_root, cursor)


def observe_signal(state_root: Path, event: str) -> dict[str, Any]:
    if event not in {"AUTH_SUCCESS", "RESET_OBSERVED", "CAPACITY_RETURNED", "QUOTA_REFUSAL"}:
        raise ControlError("SIGNAL_INVALID")
    profile, bindings, supervisor_profile, inventory = load_contracts()
    enforce_state_root(state_root, profile, bindings)
    blockers = activation_blockers(profile, bindings, supervisor_profile, inventory)
    if blockers:
        return {"status": "REFUSED", "reason": "ACTIVATION_EVIDENCE_BLOCKED",
                "event": event, "blockers": blockers, "automaticLaunchGate": "CLOSED",
                "providerCalls": 0, "providerProcesses": 0}
    broker = UniversalProviderBroker(state_root)
    before = broker.gate_state()
    path = state_root / "provider-signals.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json({"schema": "mlv-provider-signal/v1", "event": event}) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    after = broker.gate_state()
    if before != "CLOSED" or after != "CLOSED":
        raise ControlError("SIGNAL_CHANGED_GATE")
    return {"status": "RECORDED", "event": event, "automaticLaunchGate": "CLOSED"}


def parser() -> argparse.ArgumentParser:
    result = SafeArgumentParser()
    result.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    commands = result.add_subparsers(dest="command", required=True)
    tick_cmd = commands.add_parser("tick"); tick_cmd.add_argument("--demand", type=Path, required=True)
    prelaunch_cmd = commands.add_parser("prelaunch")
    prelaunch_cmd.add_argument("--demand", type=Path, required=True)
    commands.add_parser("status")
    signal = commands.add_parser("observe-signal"); signal.add_argument("--event", required=True)
    fake = commands.add_parser("test-fake-provider")
    fake.add_argument("--demand", type=Path, required=True)
    fake.add_argument("--fake-provider", type=Path, required=True)
    fake.add_argument("--sleep", type=float, default=0)
    fake.add_argument("--mode", choices=sorted(TEST_MODES), default="SHADOW")
    shadow = commands.add_parser("test-shadow-tick")
    shadow.add_argument("--demand", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command == "tick": result = tick(args.state_root, args.demand)
        elif args.command == "prelaunch": result = prelaunch_boundary(args.state_root, args.demand)
        elif args.command == "status":
            profile, bindings, supervisor_profile, inventory = load_contracts()
            enforce_state_root(args.state_root, profile, bindings)
            blockers = activation_blockers(profile, bindings, supervisor_profile, inventory)
            if not blockers and UniversalProviderBroker(args.state_root).gate_state() != "CLOSED":
                raise ControlError("AUTOMATIC_GATE_NOT_CLOSED")
            result = {"status": "BLOCKED", "disposition": "DISTINGUISH",
                      "automaticLaunchGate": "CLOSED", "blockers": blockers,
                      "canonicalTechnicalSubject": CANONICAL_TECHNICAL_SUBJECT,
                      "canonicalDoctrineMerge": CANONICAL_DOCTRINE_MERGE,
                      "technicalSubject": TECHNICAL_SUBJECT,
                      "ratificationMerge": RATIFICATION_MERGE,
                      "doctrineEngineGitBlob": DOCTRINE_ENGINE_GIT_BLOB,
                      "profileSha256": sha256_file(PROFILE),
                      "bindingsSha256": sha256_file(BINDINGS),
                      "inventorySha256": sha256_file(INVENTORY),
                      "supervisorProfileSha256": sha256_file(SUPERVISOR_PROFILE)}
        elif args.command == "observe-signal": result = observe_signal(args.state_root, args.event)
        elif args.command == "test-fake-provider":
            result = run_test_fake(
                args.state_root, args.demand, args.fake_provider, args.sleep, args.mode
            )
        elif args.command == "test-shadow-tick":
            result = shadow_tick(args.state_root, args.demand)
        else: raise ControlError("ARGUMENT_ERROR")
        sys.stdout.write(canonical_json(result) + "\n"); return 0
    except ControlError as exc:
        sys.stdout.write(canonical_json({"status": "UNEVALUABLE", "reason": exc.reason}) + "\n"); return 2
    except BaseException:
        sys.stdout.write(canonical_json({"status": "UNEVALUABLE", "reason": "INTERNAL_FAILURE"}) + "\n"); return 2


if __name__ == "__main__":
    raise SystemExit(main())
