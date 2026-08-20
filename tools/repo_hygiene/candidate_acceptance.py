from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .core import HygieneError, canonical_json, normalize_rel, resolve_repo_root, utc_now


SCHEMA = "candidate-acceptance.v1"
SURFACE_SCHEMA = "candidate-acceptance-surface.v1"
FIX_BATCH_SCHEMA = "acceptance-fix-batch.v1"
DEFAULT_REQUIRED_SURFACES = (
    "content-self",
    "content-stranger-1",
    "content-stranger-2",
    "hosted-tests",
    "hosted-codeql",
)
TERMINAL_VERDICTS = {"APPROVE", "CHANGES_REQUESTED", "UNAVAILABLE"}
BLOCKING_VERDICTS = {"CHANGES_REQUESTED", "UNAVAILABLE"}


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_sha(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise HygieneError(f"{label} must be a full lowercase Git SHA")
    return text


def _acceptance_config(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("candidateAcceptance")
    return raw if isinstance(raw, dict) else {}


def acceptance_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    rel = normalize_rel(str(_acceptance_config(config).get("stateRoot") or ".claude-state/closeout/acceptance"))
    if not rel.startswith(".claude-state/"):
        raise HygieneError("candidate acceptance stateRoot must stay under .claude-state/")
    root = (repo_root / rel).resolve()
    state = (repo_root / ".claude-state").resolve()
    if root != state and state not in root.parents:
        raise HygieneError("candidate acceptance stateRoot escapes .claude-state/")
    return root


def required_surfaces(config: Dict[str, Any], override: Optional[Sequence[str]] = None) -> List[str]:
    raw: Iterable[Any]
    if override:
        raw = override
    else:
        raw = _acceptance_config(config).get("requiredSurfaces") or DEFAULT_REQUIRED_SURFACES
    result = [str(item).strip() for item in raw if str(item).strip()]
    if not result or len(result) != len(set(result)):
        raise HygieneError("candidate acceptance required surfaces must be a non-empty unique list")
    return result


def validation_plan(config: Dict[str, Any]) -> Dict[str, Any]:
    validation = config.get("validation") if isinstance(config.get("validation"), dict) else {}
    commands = validation.get("commands") if isinstance(validation.get("commands"), list) else []
    return {
        "commands": commands,
        "runFullSuiteByDefault": bool(validation.get("runFullSuiteByDefault", False)),
        "fullSuiteEnvVar": str(validation.get("fullSuiteEnvVar") or "CLOSEOUT_RUN_FULL_VALIDATION"),
        "boundedRunner": config.get("boundedRunner") if isinstance(config.get("boundedRunner"), dict) else {},
    }


def candidate_identity(
    *,
    target_head: str,
    feature_head: str,
    policy_hash: str,
    plan_hash: str,
    rehearsal: Dict[str, Any],
) -> Dict[str, Any]:
    target = _require_sha(target_head, "targetHead")
    feature = _require_sha(feature_head, "featureHead")
    if rehearsal.get("clean") is True:
        _require_sha(rehearsal.get("integrationHead"), "integrationHead")
        _require_sha(rehearsal.get("integrationTree"), "integrationTree")
        diff_sha = str(rehearsal.get("diffSha256") or "").lower()
        if len(diff_sha) != 64 or any(ch not in "0123456789abcdef" for ch in diff_sha):
            raise HygieneError("diffSha256 must be a lowercase SHA-256 for a clean rehearsal")
    identity = {
        "targetHead": target,
        "featureHead": feature,
        "integrationHead": rehearsal.get("integrationHead"),
        "integrationTree": rehearsal.get("integrationTree"),
        "diffSha256": rehearsal.get("diffSha256"),
        "changedPaths": sorted(
            {normalize_rel(str(path)) for path in rehearsal.get("changedPaths", []) if normalize_rel(str(path))}
        ),
        "policyHash": str(policy_hash),
        "validationPlanHash": str(plan_hash),
    }
    stable_identity = {key: value for key, value in identity.items() if key != "integrationHead"}
    identity["acceptanceTupleHash"] = _hash(stable_identity)
    return identity


def surface_record(
    candidate: Dict[str, Any],
    *,
    surface: str,
    verdict: str,
    reviewer: str,
    session_id: str,
    findings: Optional[Sequence[Dict[str, Any]]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    surface_name = str(surface).strip()
    reviewer_name = str(reviewer).strip()
    session = str(session_id).strip()
    normalized_verdict = str(verdict).upper().strip()
    if not surface_name or not reviewer_name or not session:
        raise HygieneError("surface, reviewer, and sessionId are required")
    if normalized_verdict not in TERMINAL_VERDICTS:
        raise HygieneError("surface verdict must be APPROVE, CHANGES_REQUESTED, or UNAVAILABLE")
    normalized_findings = []
    for item in findings or []:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            raise HygieneError("each surface finding must be an object with a non-empty id")
        normalized_findings.append(
            {
                "id": str(item["id"]),
                "path": normalize_rel(str(item.get("path") or "")),
                "line": item.get("line"),
                "invariant": str(item.get("invariant") or ""),
                "falsifier": str(item.get("falsifier") or ""),
                "detail": str(item.get("detail") or ""),
            }
        )
    record = {
        "schema": SURFACE_SCHEMA,
        "createdAt": created_at or utc_now(),
        "surface": surface_name,
        "verdict": normalized_verdict,
        "reviewer": reviewer_name,
        "sessionId": session,
        "acceptanceTupleHash": candidate["acceptanceTupleHash"],
        "targetHead": candidate["targetHead"],
        "featureHead": candidate["featureHead"],
        "integrationTree": candidate.get("integrationTree"),
        "policyHash": candidate["policyHash"],
        "findings": normalized_findings,
        "authority": {
            "kind": "agent_or_hosted_acceptance_evidence",
            "grantsHumanAuthority": False,
            "mayReblessGolden": False,
            "mayPublishRelease": False,
        },
    }
    record["recordHash"] = _hash(record)
    return record


def _validate_surface(record: Dict[str, Any], candidate: Dict[str, Any]) -> Optional[str]:
    if record.get("schema") != SURFACE_SCHEMA:
        return "schema_mismatch"
    clean = dict(record)
    supplied_hash = clean.pop("recordHash", None)
    if supplied_hash != _hash(clean):
        return "record_hash_mismatch"
    exact = {
        "acceptanceTupleHash": candidate["acceptanceTupleHash"],
        "targetHead": candidate["targetHead"],
        "featureHead": candidate["featureHead"],
        "integrationTree": candidate.get("integrationTree"),
        "policyHash": candidate["policyHash"],
    }
    if any(record.get(key) != value for key, value in exact.items()):
        return "candidate_tuple_mismatch"
    authority = record.get("authority") if isinstance(record.get("authority"), dict) else {}
    expected_authority = {
        "kind": "agent_or_hosted_acceptance_evidence",
        "grantsHumanAuthority": False,
        "mayReblessGolden": False,
        "mayPublishRelease": False,
    }
    if authority != expected_authority:
        return "human_authority_escalation"
    if record.get("verdict") not in TERMINAL_VERDICTS:
        return "nonterminal_verdict"
    return None


def evaluate(
    *,
    candidate: Dict[str, Any],
    rehearsal: Dict[str, Any],
    required: Sequence[str],
    records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    required_names = list(required)
    selected: Dict[str, Dict[str, Any]] = {}
    stale: List[Dict[str, Any]] = []
    for record in records:
        reason = _validate_surface(record, candidate)
        surface_name = str(record.get("surface") or "")
        if reason or surface_name not in required_names:
            stale.append({"surface": surface_name, "reason": reason or "surface_not_required", "recordHash": record.get("recordHash")})
            continue
        current = selected.get(surface_name)
        new_key = (record.get("verdict") in BLOCKING_VERDICTS, str(record.get("createdAt")), str(record.get("recordHash")))
        current_key = (
            current is not None and current.get("verdict") in BLOCKING_VERDICTS,
            str(current.get("createdAt")) if current else "",
            str(current.get("recordHash")) if current else "",
        )
        if current is None or new_key > current_key:
            selected[surface_name] = record

    missing = [surface for surface in required_names if surface not in selected]
    identities: Dict[tuple[str, str], str] = {}
    duplicate_identities: List[Dict[str, str]] = []
    for surface_name, record in selected.items():
        identity = (str(record.get("reviewer")), str(record.get("sessionId")))
        previous = identities.get(identity)
        if previous is not None:
            duplicate_identities.append({"surface": surface_name, "duplicates": previous})
        else:
            identities[identity] = surface_name

    blockers: List[Dict[str, Any]] = []
    if rehearsal.get("clean") is not True:
        blockers.append(
            {
                "id": "integration-rehearsal-failed",
                "surface": "integration-rehearsal",
                "reason": str(rehearsal.get("reason") or "unknown rehearsal failure"),
                "detail": rehearsal.get("stderr") or rehearsal.get("stdout") or "",
            }
        )
    for duplicate in duplicate_identities:
        blockers.append({"id": "duplicate-review-identity", "surface": duplicate["surface"], "reason": f"duplicates {duplicate['duplicates']}"})
    for surface_name in required_names:
        record = selected.get(surface_name)
        if record and record.get("verdict") in BLOCKING_VERDICTS:
            findings = record.get("findings") if isinstance(record.get("findings"), list) else []
            if findings:
                blockers.extend({**finding, "surface": surface_name} for finding in findings)
            else:
                blockers.append({"id": f"{surface_name}-{str(record.get('verdict')).lower()}", "surface": surface_name, "reason": record.get("verdict")})

    if missing:
        state = "collecting"
    elif blockers:
        state = "changes_required"
    else:
        state = "ready"
    selected_hashes = [selected[name]["recordHash"] for name in required_names if name in selected]
    evidence_hash = _hash(
        {
            "acceptanceTupleHash": candidate["acceptanceTupleHash"],
            "rehearsal": rehearsal,
            "surfaceRecordHashes": selected_hashes,
            "missingSurfaces": missing,
            "blockers": blockers,
        }
    )
    result = {
        "schema": SCHEMA,
        "capturedAt": utc_now(),
        "state": state,
        "candidate": candidate,
        "rehearsal": rehearsal,
        "requiredSurfaces": required_names,
        "surfaces": [selected[name] for name in required_names if name in selected],
        "missingSurfaces": missing,
        "staleSurfaceRecords": stale,
        "blockers": blockers,
        "authorityBoundaries": {
            "agentApprovalsGrantHumanAuthority": False,
            "humanOnlyActions": ["golden_rebless", "release_publication", "payload_redistribution", "provider_activation"],
            "providerActivation": "CLOSED",
        },
        "evidenceHash": evidence_hash,
    }
    if state == "changes_required":
        result["fixBatch"] = {
            "schema": FIX_BATCH_SCHEMA,
            "acceptanceTupleHash": candidate["acceptanceTupleHash"],
            "evidenceHash": evidence_hash,
            "findingCount": len(blockers),
            "findings": blockers,
        }
    ledger_basis = {key: value for key, value in result.items() if key not in {"capturedAt", "ledgerHash"}}
    result["ledgerHash"] = _hash(ledger_basis)
    return result


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def write_ledger(repo_root: Path, config: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, str]:
    repo_root = repo_root.resolve()
    root = acceptance_root(repo_root, config)
    history = root / "history" / f"{ledger['candidate']['acceptanceTupleHash']}-{ledger['ledgerHash']}.json"
    latest = root / "latest.json"
    if history.exists():
        existing = json.loads(history.read_text(encoding="utf-8"))
        if existing.get("ledgerHash") != ledger["ledgerHash"]:
            raise HygieneError("candidate acceptance history path collision")
    else:
        _atomic_json(history, ledger)
    _atomic_json(latest, ledger)
    return {
        "latest": normalize_rel(str(latest.relative_to(repo_root))),
        "history": normalize_rel(str(history.relative_to(repo_root))),
    }


def latest_summary(repo_root: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    path = acceptance_root(repo_root, config) / "latest.json"
    rel_path = normalize_rel(str(path.relative_to(repo_root.resolve())))
    if not path.exists():
        return {"schema": SCHEMA, "state": "missing", "path": rel_path, "ready": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"schema": SCHEMA, "state": "invalid", "path": rel_path, "ready": False, "error": str(exc)}
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "state": "invalid", "path": rel_path, "ready": False, "error": "schema mismatch"}
    if _ledger_error(payload):
        return {"schema": SCHEMA, "state": "invalid", "path": rel_path, "ready": False, "error": "ledger hash mismatch"}
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    fix_batch = payload.get("fixBatch") if isinstance(payload.get("fixBatch"), dict) else {}
    return {
        "schema": SCHEMA,
        "state": str(payload.get("state") or "invalid"),
        "ready": payload.get("state") == "ready",
        "path": rel_path,
        "capturedAt": payload.get("capturedAt"),
        "acceptanceTupleHash": candidate.get("acceptanceTupleHash"),
        "targetHead": candidate.get("targetHead"),
        "featureHead": candidate.get("featureHead"),
        "integrationTree": candidate.get("integrationTree"),
        "policyHash": candidate.get("policyHash"),
        "evidenceHash": payload.get("evidenceHash"),
        "ledgerHash": payload.get("ledgerHash"),
        "missingSurfaces": list(payload.get("missingSurfaces") or []),
        "blockerCount": len(payload.get("blockers") or []),
        "fixBatchFindingCount": int(fix_batch.get("findingCount") or 0),
        "authorityBoundaries": payload.get("authorityBoundaries") or {},
    }


def _ledger_error(payload: Dict[str, Any]) -> Optional[str]:
    if payload.get("schema") != SCHEMA:
        return "schema mismatch"
    basis = {key: value for key, value in payload.items() if key not in {"capturedAt", "ledgerHash", "outputPaths"}}
    if payload.get("ledgerHash") != _hash(basis):
        return "ledger hash mismatch"
    return None


def validate_for_finalize(repo_root: Path, config: Dict[str, Any], detection: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    policy = _acceptance_config(config)
    if not bool(policy.get("enabled", True)) or not bool(policy.get("requireReadyForFinalize", False)):
        return None
    summary = latest_summary(repo_root, config)
    expected = {
        "targetHead": detection.get("targetHead"),
        "featureHead": detection.get("featureHead"),
        "policyHash": config.get("policyHash"),
    }
    mismatches = {
        key: {"expected": value, "actual": summary.get(key)}
        for key, value in expected.items()
        if summary.get(key) != value
    }
    if summary.get("state") == "ready" and not mismatches:
        return None
    return {
        "status": "blocked",
        "reason": "candidate_acceptance_not_ready",
        "candidateAcceptance": summary,
        "mismatches": mismatches,
        "recoveryCommand": (
            "py -3 -m tools.repo_hygiene.candidate_acceptance evaluate --repo-root . "
            f"--target-head {detection.get('targetHead')} --feature-head {detection.get('featureHead')} --write"
        ),
    }


def _load_records(paths: Sequence[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for raw in paths:
        payload = json.loads(Path(raw).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise HygieneError(f"surface record must be a JSON object: {raw}")
        records.append(payload)
    return records


def _surface_record_paths(repo_root: Path, config: Dict[str, Any], tuple_hash: str) -> List[str]:
    root = acceptance_root(repo_root, config) / "surfaces" / tuple_hash
    return [str(path) for path in sorted(root.glob("*.json"))] if root.exists() else []


def _cmd_evaluate(args: argparse.Namespace) -> int:
    from .brokered_closeout import load_closeout_config, simulate_clean_integration

    repo_root = resolve_repo_root(Path(args.repo_root))
    config = load_closeout_config(repo_root)
    target_head = _require_sha(args.target_head, "targetHead")
    feature_head = _require_sha(args.feature_head, "featureHead")
    plan = validation_plan(config)
    rehearsal = simulate_clean_integration(
        repo_root,
        config,
        target_head=target_head,
        branch_head=feature_head,
        branch_name=args.branch_name or "candidate-acceptance-probe",
        target_branch=args.target_branch or "master",
    )
    candidate = candidate_identity(
        target_head=target_head,
        feature_head=feature_head,
        policy_hash=str(config.get("policyHash") or ""),
        plan_hash=_hash(plan),
        rehearsal=rehearsal,
    )
    record_paths = list(args.surface) + _surface_record_paths(repo_root, config, candidate["acceptanceTupleHash"])
    records = _load_records(record_paths)
    ledger = evaluate(
        candidate=candidate,
        rehearsal=rehearsal,
        required=required_surfaces(config, args.required_surface),
        records=records,
    )
    if args.write:
        ledger["outputPaths"] = write_ledger(repo_root, config, ledger)
    print(json.dumps(ledger, indent=2, sort_keys=True))
    return 0 if ledger["state"] == "ready" else 2


def _cmd_status(args: argparse.Namespace) -> int:
    from .brokered_closeout import load_closeout_config

    repo_root = resolve_repo_root(Path(args.repo_root))
    config = load_closeout_config(repo_root)
    path = Path(args.ledger) if args.ledger else acceptance_root(repo_root, config) / "latest.json"
    if not path.is_absolute():
        path = repo_root / path
    if not path.exists():
        print(json.dumps({"schema": SCHEMA, "state": "missing", "path": str(path)}, indent=2))
        return 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HygieneError("candidate acceptance ledger must be a JSON object")
    ledger_error = _ledger_error(payload)
    if ledger_error:
        raise HygieneError(ledger_error)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("state") == "ready" else 2


def _cmd_record(args: argparse.Namespace) -> int:
    from .brokered_closeout import load_closeout_config

    repo_root = resolve_repo_root(Path(args.repo_root))
    config = load_closeout_config(repo_root)
    ledger_path = Path(args.ledger) if args.ledger else acceptance_root(repo_root, config) / "latest.json"
    if not ledger_path.is_absolute():
        ledger_path = repo_root / ledger_path
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict):
        raise HygieneError("candidate acceptance ledger must be a JSON object")
    ledger_error = _ledger_error(ledger)
    if ledger_error:
        raise HygieneError(ledger_error)
    candidate = ledger.get("candidate") if isinstance(ledger.get("candidate"), dict) else None
    if not candidate or not candidate.get("acceptanceTupleHash"):
        raise HygieneError("candidate acceptance ledger lacks candidate identity")
    findings = []
    for raw in args.finding:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise HygieneError("--finding values must be JSON objects")
        findings.append(parsed)
    record = surface_record(
        candidate,
        surface=args.surface,
        verdict=args.verdict,
        reviewer=args.reviewer,
        session_id=args.session_id,
        findings=findings,
    )
    safe_surface = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in args.surface).strip("-")
    if not safe_surface:
        raise HygieneError("surface name does not contain a safe filename component")
    path = acceptance_root(repo_root, config) / "surfaces" / candidate["acceptanceTupleHash"] / f"{safe_surface}-{record['recordHash'][:12]}.json"
    _atomic_json(path, record)
    print(json.dumps({"schema": SURFACE_SCHEMA, "status": "recorded", "path": normalize_rel(str(path.relative_to(repo_root))), "record": record}, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rehearse and batch exact-candidate acceptance findings.")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--repo-root", default=".")
    evaluate_parser.add_argument("--target-head", required=True)
    evaluate_parser.add_argument("--feature-head", required=True)
    evaluate_parser.add_argument("--target-branch", default="master")
    evaluate_parser.add_argument("--branch-name")
    evaluate_parser.add_argument("--required-surface", action="append", default=[])
    evaluate_parser.add_argument("--surface", action="append", default=[])
    evaluate_parser.add_argument("--write", action="store_true")
    evaluate_parser.set_defaults(func=_cmd_evaluate)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--repo-root", default=".")
    status_parser.add_argument("--ledger")
    status_parser.set_defaults(func=_cmd_status)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--repo-root", default=".")
    record_parser.add_argument("--ledger")
    record_parser.add_argument("--surface", required=True)
    record_parser.add_argument("--verdict", required=True, choices=sorted(TERMINAL_VERDICTS))
    record_parser.add_argument("--reviewer", required=True)
    record_parser.add_argument("--session-id", required=True)
    record_parser.add_argument("--finding", action="append", default=[])
    record_parser.set_defaults(func=_cmd_record)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (HygieneError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": SCHEMA, "state": "invalid", "error": str(exc)}, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
