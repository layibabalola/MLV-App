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

from vendor.universal_provider_control import (
    ControlError, UniversalProviderBroker, canonical_json, digest_json,
    route_demand_tick, strict_json_file, validate_project_profile,
)

DOCTRINE_COMMIT = "488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d"
DOCTRINE_ENGINE_GIT_BLOB = "0e26b15f249f89972e2fc7807ccd0d98a0bd4954"
DEFAULT_STATE_ROOT = Path(r"C:\ProgramData\MLV-App\provider-control-v1")
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "mlv-project-profile.candidate.json"
BINDINGS = ROOT / "lane-bindings.candidate.json"
FAKE_ENV = "MLV_PROVIDER_CONTROL_TEST_ONLY"
DEMAND_KEYS = {"schema", "project", "hasWork", "lane", "priority", "estimateFraction",
               "availableFraction", "turns", "contextTokens", "capsuleSha256",
               "checkpointSha256", "cacheAffinitySha256"}
PRIORITIES = {"OWNER_FOREGROUND", "REQUIRED_REVIEW", "PRODUCT_WORK",
              "ADJUDICATION", "MAINTENANCE"}


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
    return value


def load_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    profile, bindings = strict_json_file(PROFILE), strict_json_file(BINDINGS)
    validate_project_profile(profile)
    if (set(bindings) != {"schema", "doctrineCommit", "doctrineEngineGitBlob", "provider",
                          "adapter", "quotaDomain", "lanes"} or
            bindings["schema"] != "mlv-provider-lane-bindings/v1" or
            bindings["doctrineCommit"] != DOCTRINE_COMMIT or
            bindings["doctrineEngineGitBlob"] != DOCTRINE_ENGINE_GIT_BLOB or
            bindings["provider"] != "claude" or bindings["adapter"] != "claude-code/1.0" or
            bindings["quotaDomain"] != "claude-shared-account" or
            not isinstance(bindings["lanes"], dict)):
        raise ControlError("LANE_BINDINGS_INVALID")
    keys = {"model", "effort", "role", "subject"}
    for lane, binding in bindings["lanes"].items():
        if not isinstance(lane, str) or not isinstance(binding, dict) or set(binding) != keys:
            raise ControlError("LANE_BINDINGS_INVALID")
        subject = (ROOT / binding["subject"]).resolve(strict=True)
        if ROOT not in subject.parents or subject.is_symlink() or not subject.is_file():
            raise ControlError("FROZEN_SUBJECT_INVALID")
        if binding["effort"] != "high" or not all(isinstance(binding[k], str) and binding[k] for k in keys):
            raise ControlError("LANE_BINDINGS_INVALID")
    return profile, bindings


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


def read_prior_idle(state_root: Path) -> str | None:
    path = state_root / "idle-fingerprint.json"
    if not path.exists():
        return None
    value = strict_json_file(path)
    if (not isinstance(value, dict) or set(value) != {"schema", "demandFingerprint"} or
            value["schema"] != "mlv-provider-idle-state/v1"):
        raise ControlError("IDLE_STATE_INVALID")
    return require_digest(value["demandFingerprint"])


def write_idle(state_root: Path, fingerprint: str) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    target, temp = state_root / "idle-fingerprint.json", state_root / "idle-fingerprint.json.tmp-owned"
    payload = (canonical_json({"schema": "mlv-provider-idle-state/v1",
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


def tick(state_root: Path, demand_path: Path) -> dict[str, Any]:
    profile, bindings = load_contracts()
    demand = validate_demand(strict_json_file(demand_path), profile, bindings)
    fingerprint = digest_json(demand)
    if not demand["hasWork"]:
        result = route_demand_tick(fingerprint, fingerprint, lambda: None)
        if read_prior_idle(state_root) != fingerprint:
            write_idle(state_root, fingerprint)
        return result
    broker = UniversalProviderBroker(state_root)
    return route_demand_tick(fingerprint, read_prior_idle(state_root),
                             lambda: closed_result(broker, fingerprint))


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


def run_test_fake(state_root: Path, demand_path: Path, fake_path: Path, delay: float) -> dict[str, Any]:
    if os.environ.get(FAKE_ENV) != "1":
        raise ControlError("TEST_SEAM_DISABLED")
    profile, bindings = load_contracts()
    demand = validate_demand(strict_json_file(demand_path), profile, bindings)
    if not demand["hasWork"]:
        raise ControlError("TEST_WORK_REQUIRED")
    binding = bindings["lanes"][demand["lane"]]
    floor = float(profile["policy"]["reserveFloorByPriority"][demand["priority"]])
    if float(demand["availableFraction"]) < float(demand["estimateFraction"]) + floor:
        raise ControlError("CAPACITY_RESERVE_REFUSED")
    fake = fake_path.resolve(strict=True)
    if fake != (ROOT / "tests" / "fake_provider.py").resolve(strict=True) or fake.is_symlink():
        raise ControlError("TEST_PROVIDER_IDENTITY_INVALID")
    subject = (ROOT / binding["subject"]).resolve(strict=True)
    argv = [sys.executable, str(fake), "--model", binding["model"], "--effort", binding["effort"],
            "--role", binding["role"], "--subject", str(subject), "--sleep", str(delay)]
    with quota_lock(state_root):
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(timeout=10)
        if process.returncode != 0 or stderr:
            raise ControlError("TEST_PROVIDER_FAILED")
        return {"status": "TEST_FAKE_COMPLETED", "automaticLaunchGate": "TEST_ONLY",
                "providerCalls": 1, "providerProcesses": 1, "exitCode": process.returncode,
                "binding": {"model": binding["model"], "effort": binding["effort"],
                            "role": binding["role"], "subjectSha256": sha256_file(subject),
                            "executableSha256": sha256_file(fake),
                            "argvSha256": "sha256:" + hashlib.sha256(canonical_json(argv).encode()).hexdigest()},
                "fakeReceipt": json.loads(stdout)}


def observe_signal(state_root: Path, event: str) -> dict[str, Any]:
    if event not in {"AUTH_SUCCESS", "RESET_OBSERVED", "CAPACITY_RETURNED", "QUOTA_REFUSAL"}:
        raise ControlError("SIGNAL_INVALID")
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
    result = argparse.ArgumentParser()
    result.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    commands = result.add_subparsers(dest="command", required=True)
    tick_cmd = commands.add_parser("tick"); tick_cmd.add_argument("--demand", type=Path, required=True)
    commands.add_parser("status")
    signal = commands.add_parser("observe-signal"); signal.add_argument("--event", required=True)
    fake = commands.add_parser("test-fake-provider")
    fake.add_argument("--demand", type=Path, required=True)
    fake.add_argument("--fake-provider", type=Path, required=True)
    fake.add_argument("--sleep", type=float, default=0)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command == "tick": result = tick(args.state_root, args.demand)
        elif args.command == "status":
            load_contracts()
            result = {"status": "PASS",
                      "automaticLaunchGate": UniversalProviderBroker(args.state_root).gate_state(),
                      "doctrineCommit": DOCTRINE_COMMIT,
                      "doctrineEngineGitBlob": DOCTRINE_ENGINE_GIT_BLOB,
                      "profileSha256": sha256_file(PROFILE), "bindingsSha256": sha256_file(BINDINGS)}
        elif args.command == "observe-signal": result = observe_signal(args.state_root, args.event)
        elif args.command == "test-fake-provider":
            result = run_test_fake(args.state_root, args.demand, args.fake_provider, args.sleep)
        else: raise ControlError("ARGUMENT_ERROR")
        sys.stdout.write(canonical_json(result) + "\n"); return 0
    except ControlError as exc:
        sys.stdout.write(canonical_json({"status": "UNEVALUABLE", "reason": exc.reason}) + "\n"); return 2
    except BaseException:
        sys.stdout.write(canonical_json({"status": "UNEVALUABLE", "reason": "INTERNAL_FAILURE"}) + "\n"); return 2


if __name__ == "__main__":
    raise SystemExit(main())
