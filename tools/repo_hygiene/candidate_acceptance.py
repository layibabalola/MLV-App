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
PROVIDER_SCHEMA = "candidate-acceptance-github-checks.v1"
DEFAULT_REQUIRED_SURFACES = (
    "content-self",
    "content-stranger-1",
    "content-stranger-2",
    "hosted-tests",
    "hosted-codeql",
)
TERMINAL_VERDICTS = {"APPROVE", "CHANGES_REQUESTED", "UNAVAILABLE"}
BLOCKING_VERDICTS = {"CHANGES_REQUESTED", "UNAVAILABLE"}
HOSTED_SURFACES = {"hosted-tests", "hosted-codeql"}
HOSTED_CHECKS = {
    "hosted-tests": (
        ("Repo Hygiene Python (windows-latest)", 15368, False),
        ("Repo Hygiene Python (ubuntu-latest)", 15368, False),
        ("Factory Bridge Regressions", 15368, False),
        ("Windows GUI Pilot", 15368, False),
        ("Windows Product Oracles", 15368, False),
        ("Protected Check Route", 15368, False),
    ),
    "hosted-codeql": (
        ("Analyze (actions)", 15368, True),
        ("Analyze (c-cpp)", 15368, True),
        ("Analyze (python)", 15368, True),
        ("CodeQL", 57789, True),
    ),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        "diffSha256": candidate.get("diffSha256"),
        "validationPlanHash": candidate.get("validationPlanHash"),
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


def _provider_payload(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != PROVIDER_SCHEMA:
        raise HygieneError("GitHub provider evidence schema mismatch")
    response = payload.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("check_runs"), list):
        raise HygieneError("GitHub provider evidence response is malformed")
    total = response.get("total_count")
    if not isinstance(total, int) or total < 0 or total > 100 or total != len(response["check_runs"]):
        raise HygieneError("GitHub provider evidence is truncated or exceeds the single-page bound")
    return payload


def _selected_github_checks(payload: Dict[str, Any], surface: str, candidate: Dict[str, Any], repository: str) -> List[Dict[str, Any]]:
    if surface not in HOSTED_SURFACES:
        raise HygieneError("GitHub provider evidence may record only hosted surfaces")
    if payload.get("repository") != repository:
        raise HygieneError("GitHub provider repository does not match the configured repository")
    if payload.get("headSha") != candidate["featureHead"]:
        raise HygieneError("GitHub provider head does not match the candidate feature head")
    runs = payload["response"]["check_runs"]
    for run in runs:
        if not isinstance(run, dict) or run.get("head_sha") != candidate["featureHead"]:
            raise HygieneError("GitHub provider response contains a malformed or different-head check")
    selected: List[Dict[str, Any]] = []
    for name, expected_app, require_annotations in HOSTED_CHECKS[surface]:
        matches = [run for run in runs if run.get("name") == name]
        if not matches:
            raise HygieneError(f"GitHub provider evidence is missing check: {name}")
        matches.sort(key=lambda run: (str(run.get("started_at") or ""), int(run.get("id") or 0)), reverse=True)
        run = matches[0]
        output = run.get("output")
        if not isinstance(output, dict):
            raise HygieneError(f"GitHub check output is missing: {name}")
        if require_annotations and ("annotations_count" not in output or not isinstance(output["annotations_count"], int)):
            raise HygieneError(f"GitHub check annotation count is missing: {name}")
        app = run.get("app")
        app_id = app.get("id") if isinstance(app, dict) else None
        selected.append(
            {
                "name": name,
                "id": run.get("id"),
                "startedAt": run.get("started_at"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "appId": app_id,
                "annotationsCount": output.get("annotations_count"),
                "detailsUrl": run.get("details_url"),
                "expectedAppId": expected_app,
                "requiresZeroAnnotations": require_annotations,
            }
        )
    return selected


def _provider_failures(selected: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        item
        for item in selected
        if item["status"] == "completed"
        and (
            item["conclusion"] != "success"
            or item["appId"] != item["expectedAppId"]
            or (item["requiresZeroAnnotations"] and item["annotationsCount"] != 0)
        )
    ]


def _provider_findings(selected: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"github-check-{item['id']}",
            "path": "",
            "line": None,
            "invariant": f"{item['name']} must pass at the exact candidate head from its expected app",
            "falsifier": (
                f"status={item['status']}; conclusion={item['conclusion']}; app={item['appId']}; "
                f"annotations={item['annotationsCount']}"
            ),
            "detail": str(item.get("detailsUrl") or ""),
        }
        for item in _provider_failures(selected)
    ]


def provider_surface_record(
    repo_root: Path,
    config: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    surface: str,
    evidence_path: Path,
) -> Dict[str, Any]:
    repository = str(_acceptance_config(config).get("providerRepository") or "").strip()
    if not repository:
        raise HygieneError("candidate acceptance providerRepository is required")
    evidence_path = evidence_path.resolve()
    provider_root = (acceptance_root(repo_root, config) / "provider").resolve()
    if provider_root not in evidence_path.parents:
        raise HygieneError("GitHub provider evidence must stay under the candidate acceptance provider root")
    payload = _provider_payload(evidence_path)
    selected = _selected_github_checks(payload, surface, candidate, repository)
    failures = _provider_failures(selected)
    nonterminal = [item for item in selected if item["status"] != "completed"]
    if nonterminal:
        raise HygieneError(f"GitHub provider surface is not terminal: {surface}")
    findings = _provider_findings(selected)
    record = surface_record(
        candidate,
        surface=surface,
        verdict="APPROVE" if not failures else "CHANGES_REQUESTED",
        reviewer=f"github:{repository}",
        session_id="github-checks:" + candidate["featureHead"] + ":" + ",".join(str(item["id"]) for item in selected),
        findings=findings,
    )
    record["schema"] = "candidate-acceptance-provider-surface.v1"
    record["providerEvidence"] = {
        "schema": PROVIDER_SCHEMA,
        "repository": repository,
        "headSha": candidate["featureHead"],
        "path": normalize_rel(str(evidence_path.relative_to(repo_root.resolve()))),
        "sha256": _file_sha256(evidence_path),
        "selectedChecks": selected,
    }
    record.pop("recordHash", None)
    record["recordHash"] = _hash(record)
    return record


def _validate_surface(
    record: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    surface_name = str(record.get("surface") or "")
    expected_schema = "candidate-acceptance-provider-surface.v1" if surface_name in HOSTED_SURFACES else SURFACE_SCHEMA
    if record.get("schema") != expected_schema:
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
        "diffSha256": candidate.get("diffSha256"),
        "validationPlanHash": candidate.get("validationPlanHash"),
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
    if surface_name in HOSTED_SURFACES:
        evidence = record.get("providerEvidence")
        if not isinstance(evidence, dict) or evidence.get("schema") != PROVIDER_SCHEMA:
            return "provider_evidence_missing"
        if repo_root is None or config is None:
            return "provider_evidence_not_revalidated"
        try:
            evidence_path = (repo_root / normalize_rel(str(evidence.get("path") or ""))).resolve()
            provider_root = (acceptance_root(repo_root, config) / "provider").resolve()
            if provider_root not in evidence_path.parents:
                return "provider_evidence_path_escape"
            if _file_sha256(evidence_path) != evidence.get("sha256"):
                return "provider_evidence_hash_mismatch"
            payload = _provider_payload(evidence_path)
            repository = str(_acceptance_config(config).get("providerRepository") or "")
            selected = _selected_github_checks(payload, surface_name, candidate, repository)
        except (HygieneError, OSError, json.JSONDecodeError, ValueError):
            return "provider_evidence_invalid"
        if evidence.get("repository") != repository or evidence.get("headSha") != candidate["featureHead"]:
            return "provider_evidence_identity_mismatch"
        if evidence.get("selectedChecks") != selected:
            return "provider_selected_checks_mismatch"
        expected_verdict = "CHANGES_REQUESTED" if _provider_failures(selected) else "APPROVE"
        expected_reviewer = f"github:{repository}"
        expected_session = "github-checks:" + candidate["featureHead"] + ":" + ",".join(
            str(item["id"]) for item in selected
        )
        if record.get("verdict") != expected_verdict:
            return "provider_verdict_mismatch"
        if record.get("reviewer") != expected_reviewer or record.get("sessionId") != expected_session:
            return "provider_identity_mismatch"
        if record.get("findings") != _provider_findings(selected):
            return "provider_findings_mismatch"
    return None


def evaluate(
    *,
    candidate: Dict[str, Any],
    rehearsal: Dict[str, Any],
    required: Sequence[str],
    records: Sequence[Dict[str, Any]],
    repo_root: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    required_names = list(required)
    grouped: Dict[str, List[Dict[str, Any]]] = {name: [] for name in required_names}
    stale: List[Dict[str, Any]] = []
    for record in records:
        reason = _validate_surface(record, candidate, repo_root=repo_root, config=config)
        surface_name = str(record.get("surface") or "")
        if reason or surface_name not in required_names:
            stale.append({"surface": surface_name, "reason": reason or "surface_not_required", "recordHash": record.get("recordHash")})
            continue
        grouped[surface_name].append(record)

    selected: Dict[str, List[Dict[str, Any]]] = {}
    for surface_name, surface_records in grouped.items():
        if not surface_records:
            continue
        blocking = [record for record in surface_records if record.get("verdict") in BLOCKING_VERDICTS]
        if blocking:
            selected[surface_name] = sorted(blocking, key=lambda record: (str(record.get("createdAt")), str(record.get("recordHash"))))
        else:
            selected[surface_name] = [max(surface_records, key=lambda record: (str(record.get("createdAt")), str(record.get("recordHash"))))]

    missing = [surface for surface in required_names if surface not in selected]
    reviewer_identities: Dict[str, str] = {}
    session_identities: Dict[str, str] = {}
    duplicate_identities: List[Dict[str, str]] = []
    for surface_name, surface_records in selected.items():
        record = surface_records[-1]
        session = str(record.get("sessionId"))
        previous_session = session_identities.get(session)
        if previous_session is not None:
            duplicate_identities.append({"surface": surface_name, "duplicates": previous_session, "identity": "session"})
        else:
            session_identities[session] = surface_name
        if surface_name.startswith("content-"):
            reviewer = str(record.get("reviewer"))
            previous_reviewer = reviewer_identities.get(reviewer)
            if previous_reviewer is not None:
                duplicate_identities.append({"surface": surface_name, "duplicates": previous_reviewer, "identity": "reviewer"})
            else:
                reviewer_identities[reviewer] = surface_name

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
        blockers.append(
            {
                "id": f"duplicate-review-{duplicate['identity']}",
                "surface": duplicate["surface"],
                "reason": f"duplicates {duplicate['duplicates']}",
            }
        )
    for surface_name in required_names:
        for record in selected.get(surface_name, []):
            if record.get("verdict") in BLOCKING_VERDICTS:
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
    selected_records = [record for name in required_names for record in selected.get(name, [])]
    selected_hashes = [record["recordHash"] for record in selected_records]
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
        "surfaces": selected_records,
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
    ledger_error = _ledger_error(ledger, repo_root=repo_root, config=config)
    if ledger_error:
        raise HygieneError(f"candidate acceptance ledger is incoherent: {ledger_error}")
    root = acceptance_root(repo_root, config)
    history = root / "history" / f"{ledger['candidate']['acceptanceTupleHash']}-{ledger['ledgerHash']}.json"
    latest = root / "latest.json"
    if history.exists():
        existing = json.loads(history.read_text(encoding="utf-8"))
        existing_basis = {key: value for key, value in existing.items() if key not in {"capturedAt", "outputPaths"}}
        ledger_basis = {key: value for key, value in ledger.items() if key not in {"capturedAt", "outputPaths"}}
        if _ledger_error(existing, repo_root=repo_root, config=config) or existing_basis != ledger_basis:
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
    ledger_error = _ledger_error(payload, repo_root=repo_root, config=config)
    if ledger_error:
        return {"schema": SCHEMA, "state": "invalid", "path": rel_path, "ready": False, "error": ledger_error}
    history = acceptance_root(repo_root, config) / "history" / (
        f"{payload['candidate']['acceptanceTupleHash']}-{payload['ledgerHash']}.json"
    )
    try:
        history_payload = json.loads(history.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"schema": SCHEMA, "state": "invalid", "path": rel_path, "ready": False, "error": f"immutable history missing or invalid: {exc}"}
    history_basis = {key: value for key, value in history_payload.items() if key not in {"capturedAt", "outputPaths"}}
    latest_basis = {key: value for key, value in payload.items() if key not in {"capturedAt", "outputPaths"}}
    if history_basis != latest_basis:
        return {"schema": SCHEMA, "state": "invalid", "path": rel_path, "ready": False, "error": "latest ledger does not match immutable history"}
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
        "diffSha256": candidate.get("diffSha256"),
        "validationPlanHash": candidate.get("validationPlanHash"),
        "policyHash": candidate.get("policyHash"),
        "evidenceHash": payload.get("evidenceHash"),
        "ledgerHash": payload.get("ledgerHash"),
        "missingSurfaces": list(payload.get("missingSurfaces") or []),
        "blockerCount": len(payload.get("blockers") or []),
        "fixBatchFindingCount": int(fix_batch.get("findingCount") or 0),
        "authorityBoundaries": payload.get("authorityBoundaries") or {},
    }


def _ledger_error(
    payload: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if payload.get("schema") != SCHEMA:
        return "schema mismatch"
    basis = {key: value for key, value in payload.items() if key not in {"capturedAt", "ledgerHash", "outputPaths"}}
    if payload.get("ledgerHash") != _hash(basis):
        return "ledger hash mismatch"
    candidate = payload.get("candidate")
    rehearsal = payload.get("rehearsal")
    required = payload.get("requiredSurfaces")
    surfaces = payload.get("surfaces")
    if not isinstance(candidate, dict) or not isinstance(rehearsal, dict):
        return "candidate or rehearsal missing"
    if not isinstance(required, list) or not isinstance(surfaces, list):
        return "required surfaces or surface records missing"
    if config is not None and required != required_surfaces(config):
        return "required surface policy mismatch"
    try:
        recomputed_candidate = candidate_identity(
            target_head=str(candidate.get("targetHead") or ""),
            feature_head=str(candidate.get("featureHead") or ""),
            policy_hash=str(candidate.get("policyHash") or ""),
            plan_hash=str(candidate.get("validationPlanHash") or ""),
            rehearsal=rehearsal,
        )
    except HygieneError as exc:
        return f"candidate identity invalid: {exc}"
    if candidate != recomputed_candidate:
        return "candidate identity mismatch"
    recomputed = evaluate(
        candidate=candidate,
        rehearsal=rehearsal,
        required=required,
        records=surfaces,
        repo_root=repo_root,
        config=config,
    )
    for key in (
        "state",
        "requiredSurfaces",
        "surfaces",
        "missingSurfaces",
        "blockers",
        "authorityBoundaries",
        "evidenceHash",
        "fixBatch",
    ):
        if payload.get(key) != recomputed.get(key):
            return f"ledger coherence mismatch: {key}"
    return None


def validate_for_finalize(repo_root: Path, config: Dict[str, Any], detection: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    policy = _acceptance_config(config)
    enabled = bool(policy.get("enabled", True))
    required = bool(policy.get("requireReadyForFinalize", True))
    tooling_enforced = bool(config.get("toolingBaseline", {}).get("enabled", True))
    if tooling_enforced and (not enabled or not required):
        return {
            "status": "blocked",
            "reason": "candidate_acceptance_policy_disabled",
            "recoveryCommand": "restore the tracked mandatory candidateAcceptance policy and rerun acceptance",
        }
    if not enabled or not required:
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
        from .brokered_closeout import simulate_clean_integration

        rehearsal = simulate_clean_integration(
            repo_root,
            config,
            target_head=str(detection.get("targetHead")),
            branch_head=str(detection.get("featureHead")),
            branch_name=str(detection.get("branch") or "candidate-acceptance-finalize"),
            target_branch=str(detection.get("targetBranch") or "master"),
        )
        if rehearsal.get("clean") is True:
            current_candidate = candidate_identity(
                target_head=str(detection.get("targetHead")),
                feature_head=str(detection.get("featureHead")),
                policy_hash=str(config.get("policyHash") or ""),
                plan_hash=_hash(validation_plan(config)),
                rehearsal=rehearsal,
            )
            tuple_mismatches = {
                key: {"expected": current_candidate.get(key), "actual": summary.get(key)}
                for key in ("acceptanceTupleHash", "integrationTree", "diffSha256", "validationPlanHash")
                if summary.get(key) != current_candidate.get(key)
            }
            if not tuple_mismatches:
                return None
            mismatches.update(tuple_mismatches)
        else:
            mismatches["integrationRehearsal"] = {"expected": "clean", "actual": rehearsal.get("reason")}
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


def final_integration_mismatches(accepted: Dict[str, Any], final_range: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: {"expected": accepted.get(key), "actual": final_range.get(key)}
        for key in ("integrationTree", "diffSha256")
        if accepted.get(key) != final_range.get(key)
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


def _historical_surface_records(repo_root: Path, config: Dict[str, Any], tuple_hash: str) -> List[Dict[str, Any]]:
    history_root = acceptance_root(repo_root, config) / "history"
    records: Dict[str, Dict[str, Any]] = {}
    if not history_root.exists():
        return []
    for path in sorted(history_root.glob(f"{tuple_hash}-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise HygieneError(f"candidate acceptance history is unreadable: {path}")
        if not isinstance(payload, dict) or _ledger_error(payload, repo_root=repo_root, config=config):
            raise HygieneError(f"candidate acceptance history is invalid: {path}")
        for record in payload.get("surfaces") or []:
            if isinstance(record, dict) and record.get("recordHash"):
                records[str(record["recordHash"])] = record
    return list(records.values())


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
    known_hashes = {str(record.get("recordHash")) for record in records}
    for historical in _historical_surface_records(repo_root, config, candidate["acceptanceTupleHash"]):
        if str(historical.get("recordHash")) not in known_hashes:
            records.append(historical)
    ledger = evaluate(
        candidate=candidate,
        rehearsal=rehearsal,
        required=required_surfaces(config),
        records=records,
        repo_root=repo_root,
        config=config,
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
    ledger_error = _ledger_error(payload, repo_root=repo_root, config=config)
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
    ledger_error = _ledger_error(ledger, repo_root=repo_root, config=config)
    if ledger_error:
        raise HygieneError(ledger_error)
    candidate = ledger.get("candidate") if isinstance(ledger.get("candidate"), dict) else None
    if not candidate or not candidate.get("acceptanceTupleHash"):
        raise HygieneError("candidate acceptance ledger lacks candidate identity")
    if args.surface in HOSTED_SURFACES:
        raise HygieneError("hosted surfaces require record-hosted with content-addressed provider evidence")
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


def _cmd_record_hosted(args: argparse.Namespace) -> int:
    from .brokered_closeout import load_closeout_config

    repo_root = resolve_repo_root(Path(args.repo_root))
    config = load_closeout_config(repo_root)
    ledger_path = Path(args.ledger) if args.ledger else acceptance_root(repo_root, config) / "latest.json"
    if not ledger_path.is_absolute():
        ledger_path = repo_root / ledger_path
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict):
        raise HygieneError("candidate acceptance ledger must be a JSON object")
    ledger_error = _ledger_error(ledger, repo_root=repo_root, config=config)
    if ledger_error:
        raise HygieneError(ledger_error)
    candidate = ledger["candidate"]
    record = provider_surface_record(
        repo_root,
        config,
        candidate,
        surface=args.surface,
        evidence_path=(repo_root / args.evidence) if not Path(args.evidence).is_absolute() else Path(args.evidence),
    )
    safe_surface = args.surface.replace("/", "-")
    path = acceptance_root(repo_root, config) / "surfaces" / candidate["acceptanceTupleHash"] / f"{safe_surface}-{record['recordHash'][:12]}.json"
    _atomic_json(path, record)
    print(
        json.dumps(
            {
                "schema": record["schema"],
                "status": "recorded",
                "path": normalize_rel(str(path.relative_to(repo_root))),
                "record": record,
            },
            indent=2,
            sort_keys=True,
        )
    )
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
    provider_parser = sub.add_parser("record-hosted")
    provider_parser.add_argument("--repo-root", default=".")
    provider_parser.add_argument("--ledger")
    provider_parser.add_argument("--surface", required=True, choices=sorted(HOSTED_SURFACES))
    provider_parser.add_argument("--evidence", required=True)
    provider_parser.set_defaults(func=_cmd_record_hosted)
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
