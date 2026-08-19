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

TECHNICAL_SUBJECT = "874605e43531c9aa230ee16851f8107a8e0d9cec"
RATIFICATION_MERGE = "488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d"
DOCTRINE_ENGINE_GIT_BLOB = "0e26b15f249f89972e2fc7807ccd0d98a0bd4954"
EXPECTED_PROFILE_SHA256 = "sha256:e2993d90c520f5383eba8eab756bbc867ebc4fe0bfdafb8a287a05fe8d2f1cc9"
EXPECTED_BINDINGS_SHA256 = "sha256:f01383c5d5d38b2ec107bf76de90715d00c25c862493e8eebf1ca58c53f78531"
EXPECTED_INVENTORY_SHA256 = "sha256:7ac1d38bc190fc2deedbabd29d8a01d8c2a9de507e150c9e44312b9a2d8008e8"
EXPECTED_SUPERVISOR_PROFILE_SHA256 = "sha256:a4e97bd2f481e3b5f461ecbe8a131100ec0ba0ef1ab545ff0da2387cd3b41c90"
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
    if inventory["scheduledTask"]["exportedXml"]["status"] != "EXACT":
        blockers.append("TASK_XML_CANONICALIZATION_UNKNOWN")
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
    validate_local_contract(inventory, "mlv-provider-observed-inventory-v1.schema.json")
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
            "generation": "R14", "technicalSubject": TECHNICAL_SUBJECT,
            "ratificationMerge": RATIFICATION_MERGE,
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
        raise ControlError("TEST_WORK_REQUIRED")
    binding = bindings["lanes"][demand["lane"]]
    floor = float(profile["policy"]["reserveFloorByPriority"][demand["priority"]])
    if float(demand["availableFraction"]) < float(demand["estimateFraction"]) + floor:
        raise ControlError("CAPACITY_RESERVE_REFUSED")
    if fake_path.is_symlink():
        raise ControlError("TEST_PROVIDER_IDENTITY_INVALID")
    fake = fake_path.resolve(strict=True)
    if fake != (ROOT / "tests" / "fake_provider.py").resolve(strict=True):
        raise ControlError("TEST_PROVIDER_IDENTITY_INVALID")
    subject = (ROOT / binding["subject"]).resolve(strict=True)
    subject_digest = sha256_file(subject)
    fake_digest = sha256_file(fake)
    process_image_input = Path(sys.executable)
    process_image = process_image_input.resolve(strict=True)
    if not process_image.is_file():
        raise ControlError("TEST_PROCESS_IMAGE_IDENTITY_INVALID")
    process_image_digest = sha256_file(process_image)
    argv = [str(process_image), str(fake), "--model", binding["model"], "--effort", binding["effort"],
            "--role", binding["role"], "--subject", str(subject), "--sleep", str(delay),
            "--harness-mode", mode]
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
        if (fresh_subject != subject or
                sha256_file(fresh_subject) != fresh_binding["subjectSha256"] or
                sha256_file(fresh_subject) != subject_digest or
                fake_path.is_symlink() or fake_path.resolve(strict=True) != fake or
                sha256_file(fake) != fake_digest or
                Path(sys.executable).resolve(strict=True) != process_image or
                sha256_file(process_image) != process_image_digest):
            raise ControlError("LAUNCH_BINDING_CHANGED")
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise ControlError("FAKE_PROVIDER_TIMEOUT_CONTAINED") from exc
        if process.returncode != 0 or stderr:
            raise ControlError("TEST_PROVIDER_FAILED")
        try:
            receipt = json.loads(stdout)
        except (TypeError, ValueError) as exc:
            raise ControlError("TEST_PROVIDER_RECEIPT_INVALID") from exc
        expected_receipt = {
            "model": binding["model"], "effort": binding["effort"],
            "role": binding["role"], "subject": str(subject), "harnessMode": mode,
            "provider": "FAKE_ONLY",
            "processImagePath": str(process_image), "scriptPath": str(fake),
            "requestedAuthority": "OPEN_GATE_AND_ADOPT" if mode == "CONTAINMENT" else "NONE",
        }
        if receipt != expected_receipt:
            raise ControlError("TEST_PROVIDER_RECEIPT_INVALID")
        if UniversalProviderBroker(state_root).gate_state() != "CLOSED":
            raise ControlError("TEST_CHANGED_AUTOMATIC_GATE")
        return {"status": "TEST_FAKE_COMPLETED", "harnessMode": mode,
                "authority": "TEST_ONLY_ZERO_PRODUCTION_AUTHORITY",
                "automaticLaunchGate": "CLOSED",
                "providerCalls": 0, "providerProcesses": 0,
                "fakeProviderCalls": 1, "fakeProviderProcesses": 1,
                "inputTokens": 0, "cachedInputTokens": 0,
                "reasoningTokens": 0, "outputTokens": 0,
                "containedHostileAuthorityClaim": mode == "CONTAINMENT",
                "exitCode": process.returncode,
                "binding": {"model": binding["model"], "effort": binding["effort"],
                            "role": binding["role"], "subjectSha256": sha256_file(subject),
                            "processImagePath": str(process_image),
                            "processImageSha256": process_image_digest,
                            "scriptPath": str(fake), "scriptSha256": fake_digest,
                            "argvSha256": "sha256:" + hashlib.sha256(canonical_json(argv).encode()).hexdigest()},
                "fakeReceipt": receipt}


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
        elif args.command == "status":
            profile, bindings, supervisor_profile, inventory = load_contracts()
            enforce_state_root(args.state_root, profile, bindings)
            blockers = activation_blockers(profile, bindings, supervisor_profile, inventory)
            if not blockers and UniversalProviderBroker(args.state_root).gate_state() != "CLOSED":
                raise ControlError("AUTOMATIC_GATE_NOT_CLOSED")
            result = {"status": "BLOCKED", "disposition": "DISTINGUISH",
                      "automaticLaunchGate": "CLOSED", "blockers": blockers,
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
