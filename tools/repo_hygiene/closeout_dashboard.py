from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .brokered_closeout import (
    HygieneError,
    closeout_script_command,
    effective_closeout_script_command,
    load_closeout_config,
    repo_closed_postcondition_state,
    repo_state_dashboard_spec,
    repo_state_ledger_config,
    repo_state_path,
    repo_state_snapshot,
    repo_sweep_reports_root,
    rollback_policy,
)
from .core import normalize_rel, resolve_repo_root, sha256_text


DASHBOARD_ACTIONS_SCHEMA = "closeout-dashboard-actions.v1"
DASHBOARD_ACTION_PREVIEW_SCHEMA = "closeout-dashboard-action-preview.v1"
DASHBOARD_ACTION_REQUEST_SCHEMA = "closeout-dashboard-action-request.v1"
DASHBOARD_ENDPOINTS_SCHEMA = "closeout-dashboard-endpoints.v1"
SAFE_HISTORY_ID = re.compile(r"^[A-Za-z0-9_.-]+(?:\.json)?$")
SAFE_ACTION_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
HELPER_FUTURE_SKEW_MS = 1000
DASHBOARD_MIN_INTERVAL_MS = 1000
DASHBOARD_MAX_INTERVAL_MS = 60000
MAX_PROCESS_ID = 2147483647


def _configured_value(mapping: Dict[str, Any], key: str, default: Any) -> Any:
    return mapping[key] if key in mapping else default


def dashboard_endpoints(config: Dict[str, Any]) -> Dict[str, str]:
    dashboard = repo_state_dashboard_spec(config)
    configured = dashboard.get("endpoints")
    if not isinstance(configured, dict):
        configured = {}
    return {
        "page": str(configured.get("page") or "/closeout"),
        "latest": str(configured.get("latest") or "/api/closeout/repo-state/latest"),
        "historyIndex": str(configured.get("historyIndex") or "/api/closeout/repo-state/history-index"),
        "historySnapshot": str(configured.get("historySnapshot") or "/api/closeout/repo-state/history/{snapshotId}"),
        "actions": str(configured.get("actions") or "/api/closeout/actions"),
        "actionsPreview": str(configured.get("actionsPreview") or "/api/closeout/actions/preview"),
        "actionsRequest": str(configured.get("actionsRequest") or "/api/closeout/actions/request"),
        "events": str(configured.get("events") or "/api/closeout/events"),
    }


def dashboard_actions_payload(
    repo_root_arg: Path,
    *,
    server_process_id: Optional[int] = None,
) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    dashboard = repo_state_dashboard_spec(config)
    ledger = repo_state_ledger_config(config)
    rollback = rollback_policy(config)
    endpoints = dashboard_endpoints(config)
    refresh_command = effective_closeout_script_command(
        config,
        "write-repo-state.ps1",
        ["-Write", "-LatestOnly"],
        str(ledger.get("liveRefreshCommand") or dashboard.get("refreshCommand") or ""),
    )
    refresh_command_policy = str(
        ledger.get("refreshCommandPolicy")
        or dashboard.get("refreshCommandPolicy")
        or "repo-owned-write-repo-state-latest-only"
    )
    helper = dashboard.get("helper") if isinstance(dashboard.get("helper"), dict) else {}
    disallowed = list(rollback.get("disallowedDefaultActions") or [])
    for extra in dashboard.get("rollbackForbiddenActions") or []:
        if extra not in disallowed:
            disallowed.append(str(extra))
    helper_pid_source = str(helper.get("serverProcessIdSource") or "")
    if not helper_pid_source or helper_pid_source == "/api/closeout/actions":
        helper_pid_source = endpoints["actions"]
    helper_readiness_endpoint = str(helper.get("readinessEndpoint") or "")
    if not helper_readiness_endpoint or helper_readiness_endpoint == "/api/closeout/actions":
        helper_readiness_endpoint = endpoints["actions"]

    return {
        "schema": DASHBOARD_ACTIONS_SCHEMA,
        "status": "ready",
        "serverProcessId": _request_int(
            server_process_id if server_process_id is not None else os.getpid(),
            "serverProcessId",
            min_value=1,
            max_value=MAX_PROCESS_ID,
        ),
        "repoRoot": str(repo_root),
        "repoRootHash": sha256_text(str(repo_root).casefold())[:16],
        "endpointsSchema": DASHBOARD_ENDPOINTS_SCHEMA,
        "endpoints": endpoints,
        "helper": {
            "scriptPath": str(helper.get("scriptPath") or "tools\\closeout\\start-closeout-dashboard.ps1"),
            "module": str(helper.get("module") or "tools.repo_hygiene.closeout_dashboard"),
            "host": str(helper.get("host") or "127.0.0.1"),
            "port": _request_int(_configured_value(helper, "port", 8765), "helper.port", min_value=1, max_value=65535),
            "reuseExistingForSameRepo": bool(helper.get("reuseExistingForSameRepo", True)),
            "serverProcessIdSource": helper_pid_source,
            "readinessEndpoint": helper_readiness_endpoint,
            "staleAfterMs": _request_int(
                _configured_value(helper, "staleAfterMs", 15000),
                "helper.staleAfterMs",
                min_value=DASHBOARD_MIN_INTERVAL_MS,
                max_value=DASHBOARD_MAX_INTERVAL_MS,
            ),
        },
        "dashboard": {
            "localUrl": str(dashboard.get("localUrl") or "http://127.0.0.1:8765/closeout"),
            "stickyUrlPath": str(dashboard.get("stickyUrlPath") or "/closeout"),
            "autoRefreshMs": _request_int(
                _configured_value(dashboard, "autoRefreshMs", 5000),
                "dashboard.autoRefreshMs",
                min_value=DASHBOARD_MIN_INTERVAL_MS,
                max_value=DASHBOARD_MAX_INTERVAL_MS,
            ),
            "refreshCommandPolicy": refresh_command_policy,
            "mutationModel": str(dashboard.get("mutationModel") or "symbolic-action-request-only"),
            "feedAuthority": str(dashboard.get("feedAuthority") or "latest-json-is-display-feed-only"),
            "duplicateLaunchPolicy": str(dashboard.get("duplicateLaunchPolicy") or "reuse-same-repo-fail-foreign-owner"),
            "preservedClientStateKeys": list(dashboard.get("preservedClientStateKeys") or []),
        },
        "symbolicActions": [
            {
                "id": "refresh_repo_state",
                "label": "Refresh repo state feed",
                "actionability": "generated-feed-only",
                "previewAvailable": True,
                "previewEndpoint": endpoints["actionsPreview"],
                "command": refresh_command,
                "commandPolicy": refresh_command_policy,
                "writesHistory": bool(ledger.get("liveRefreshWritesHistory", False)),
            },
            {
                "id": "request_rollback",
                "label": "Request rollback plan",
                "actionability": str(rollback.get("readinessDefaultActionability") or "read-only-no-actor"),
                "previewAvailable": True,
                "previewEndpoint": endpoints["actionsPreview"],
                "readinessReason": "rollback actor has not validated an immutable source snapshot and closeout-rollback-manifest.v1",
                "requiredManifestSchema": str(rollback.get("requiredManifestSchema") or "closeout-rollback-manifest.v1"),
                "requiredManifestFields": list(rollback.get("requiredManifestFields") or []),
                "validatorCommand": str(rollback.get("validatorCommand") or ""),
                "validatorActionability": str(rollback.get("validatorActionability") or "read-only-validator"),
                "actionActorAvailable": False,
                "mutationReady": False,
                "requiresUserApproval": bool(rollback.get("requireUserApprovalForRollback", True)),
                "requiresImmutableSourceSnapshot": bool(rollback.get("requireImmutableSourceSnapshotForRollback", True)),
                "exactTupleRequired": [
                    "targetHead",
                    "sourceSnapshotPath",
                    "sourceSnapshotHash",
                    "sourceSnapshotAuditHash",
                    "repoClosedAuditHash",
                    "policyHash",
                    "plannedStrategy",
                    "userApproval",
                    "recoveryCommand",
                ],
            },
            {
                "id": "request_retained_remediation",
                "label": "Request retained-candidate remediation",
                "actionability": "symbolic-request-only",
                "previewAvailable": True,
                "previewEndpoint": endpoints["actionsPreview"],
                "command": closeout_script_command("remediate-retained-closeout.ps1", ["-Apply"], config),
                "exactTupleRequired": ["candidateId", "actionId", "evidenceHash", "policyHash", "pinnedRefs"],
                "requestOnlyReason": "repo-owned retained-remediation actor must revalidate the tuple before mutation",
            },
        ],
        "forbiddenActions": disallowed,
    }


def dashboard_action_request_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    dashboard = repo_state_dashboard_spec(config)
    rel = normalize_rel(str(dashboard.get("actionRequestRoot") or ".claude-state/closeout/dashboard-action-requests"))
    if not rel.startswith(".claude-state/"):
        raise HygieneError("dashboard action request root must stay under .claude-state/: %s" % rel)
    root = (repo_root / rel).resolve()
    claude_state_root = (repo_root / ".claude-state").resolve()
    if root != claude_state_root and claude_state_root not in root.parents:
        raise HygieneError("dashboard action request root must stay under .claude-state/: %s" % rel)
    return root


def _request_int(
    value: Any,
    field_name: str,
    *,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> int:
    if isinstance(value, bool):
        raise HygieneError("%s must be an integer" % field_name)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[0-9]+", text):
            parsed = int(text)
        else:
            raise HygieneError("%s must be an integer" % field_name)
    else:
        raise HygieneError("%s must be an integer" % field_name)
    if min_value is not None and parsed < min_value:
        raise HygieneError("%s must be >= %s" % (field_name, min_value))
    if max_value is not None and parsed > max_value:
        raise HygieneError("%s must be <= %s" % (field_name, max_value))
    return parsed


def _empty_exact_tuple_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return not value or any(_empty_exact_tuple_value(item_value) for item_value in value.values())
    if isinstance(value, (list, tuple, set)):
        return not value or any(_empty_exact_tuple_value(item_value) for item_value in value)
    return False


def _repo_state_hash(snapshot: Dict[str, Any]) -> str:
    return sha256_text(json.dumps(snapshot, sort_keys=True, default=str))


def _blocker_preview_rows(blockers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for blocker in blockers:
        kind = str(blocker.get("kind") or "unknown")
        row = {"kind": kind, "count": 0, "samplePaths": [], "detail": ""}
        if kind in {"non_exempt_dirty_files", "non_exempt_untracked_files"}:
            entries = blocker.get("entries") if isinstance(blocker.get("entries"), list) else []
            row["count"] = len(entries)
            row["samplePaths"] = [str(item.get("path")) for item in entries[:5] if isinstance(item, dict) and item.get("path")]
        elif kind == "disallowed_stashes":
            stashes = blocker.get("stashes") if isinstance(blocker.get("stashes"), list) else []
            row["count"] = len(stashes)
            row["samplePaths"] = [str(item.get("name") or item.get("ref") or "") for item in stashes[:5] if isinstance(item, dict)]
        elif kind == "stale_transaction_branches":
            branches = blocker.get("branches") if isinstance(blocker.get("branches"), list) else []
            row["count"] = len(branches)
            row["samplePaths"] = [str(item.get("branch") or "") for item in branches[:5] if isinstance(item, dict)]
        elif kind in {"linked_sibling_worktrees", "stale_managed_worktrees"}:
            worktrees = blocker.get("worktrees") if isinstance(blocker.get("worktrees"), list) else []
            row["count"] = len(worktrees)
            row["samplePaths"] = [str(item.get("path") or "") for item in worktrees[:5] if isinstance(item, dict)]
        elif kind == "orphaned_closeout_runtime_artifacts":
            artifacts = blocker.get("artifacts") if isinstance(blocker.get("artifacts"), list) else []
            row["count"] = len(artifacts)
            row["samplePaths"] = [str(item.get("path") or "") for item in artifacts[:5] if isinstance(item, dict)]
        elif kind == "retained_remote_feature_refs":
            refs = blocker.get("remoteFeaturePlans") if isinstance(blocker.get("remoteFeaturePlans"), list) else []
            row["count"] = len(refs)
            row["samplePaths"] = [str(item.get("ref") or item.get("branch") or "") for item in refs[:5] if isinstance(item, dict)]
        elif kind == "agent_remediation_queue_not_closed":
            state = blocker.get("agentRemediationState") if isinstance(blocker.get("agentRemediationState"), dict) else {}
            packets = state.get("packets") if isinstance(state.get("packets"), list) else []
            row["count"] = len(packets)
            row["samplePaths"] = [str(item.get("packetPath") or "") for item in packets[:5] if isinstance(item, dict)]
        elif kind == "worktree_inspection_failed":
            inspection = blocker.get("worktreeInspection") if isinstance(blocker.get("worktreeInspection"), dict) else {}
            failures = inspection.get("inspectionFailures") if isinstance(inspection.get("inspectionFailures"), list) else []
            row["count"] = len(failures)
            row["detail"] = "; ".join(str(item) for item in failures[:3])
        elif kind == "remediation_freeze_active":
            freeze = blocker.get("freeze") if isinstance(blocker.get("freeze"), dict) else {}
            row["detail"] = str(freeze.get("markerPath") or freeze.get("envVar") or "remediation freeze is active")
        else:
            row["detail"] = str(blocker.get("error") or blocker.get("reason") or "")
        rows.append(row)
    return rows


def _candidate_report_rows(repo_root: Path, config: Dict[str, Any], *, candidate_id: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
    report_root = repo_sweep_reports_root(repo_root, config)
    rows: List[Dict[str, Any]] = []
    if report_root.exists():
        report_paths = sorted(report_root.glob("*/latest-report.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in report_paths:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            current_candidate_id = str(payload.get("candidateId") or "")
            if candidate_id and current_candidate_id != candidate_id:
                continue
            rows.append(
                {
                    "candidateId": current_candidate_id,
                    "reportType": str(payload.get("reportType") or ""),
                    "recommendedAction": str(payload.get("recommendedAction") or ""),
                    "actionClass": str(payload.get("actionClass") or ""),
                    "blockers": [str(item) for item in (payload.get("blockers") or []) if str(item)],
                    "recoveryCommand": str(payload.get("recoveryCommand") or ""),
                    "reportPath": normalize_rel(str(path.relative_to(repo_root))),
                }
            )
            if len(rows) >= limit:
                break
    return {
        "reportRoot": normalize_rel(str(report_root.relative_to(repo_root))),
        "count": len(rows),
        "rows": rows,
    }


def dashboard_action_preview_payload(repo_root_arg: Path, request: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(request, dict):
        raise HygieneError("dashboard action preview request must be a JSON object")
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    actions = dashboard_actions_payload(repo_root)
    by_id = {str(item.get("id")): item for item in actions["symbolicActions"]}
    action_id = str(request.get("actionId") or request.get("id") or "").strip()
    if not SAFE_ACTION_ID.match(action_id) or action_id not in by_id:
        raise HygieneError("unsupported dashboard symbolic action preview: %s" % (action_id or "<missing>"))
    action = by_id[action_id]
    snapshot = repo_state_snapshot(repo_root, write=False)
    preview = {
        "schema": DASHBOARD_ACTION_PREVIEW_SCHEMA,
        "status": "ready",
        "actionId": action_id,
        "label": action.get("label"),
        "actionability": action.get("actionability"),
        "previewMode": "read-only-explain-and-dry-run",
        "mutationBoundary": "repo-owned symbolic actors only",
        "noDirectMutation": True,
        "wouldMutateNow": False,
        "repoRoot": str(repo_root),
        "repoRootHash": actions["repoRootHash"],
        "repoStateHash": _repo_state_hash(snapshot),
        "requestOnlyReason": action.get("requestOnlyReason") or action.get("readinessReason") or "",
        "exactTupleRequired": list(action.get("exactTupleRequired") or []),
        "requestTemplate": {
            "actionId": action_id,
            "exactTuple": {field: "<required>" for field in (action.get("exactTupleRequired") or [])},
        },
        "consequences": [],
        "safeguards": [],
        "nextSteps": [],
        "blockerSummary": [],
        "candidateReports": {"reportRoot": "", "count": 0, "rows": []},
    }
    if action_id == "refresh_repo_state":
        preview["explanation"] = "Refreshing the dashboard feed only rewrites the generated latest repo-state snapshot. It does not append closeout history, write audits, or mutate refs, worktrees, stashes, or tracked source."
        preview["consequences"] = [
            "Updates the stable latest dashboard feed under generated state.",
            "Keeps immutable history and audit rows unchanged during normal polling.",
        ]
        preview["safeguards"] = [
            "Refresh command must resolve to the repo-owned latest-only writer.",
            "Unsupported configured refresh commands fail closed.",
        ]
        preview["nextSteps"] = [
            "Use the top-level refresh button when the page looks stale.",
            "Use a normal closeout/finalize path when you need durable history or audit evidence.",
        ]
        preview["generatedFeed"] = {
            "command": action.get("command"),
            "commandPolicy": action.get("commandPolicy"),
            "writesHistory": bool(action.get("writesHistory")),
            "latestPath": snapshot.get("stateLedger", {}).get("latestPath"),
        }
        return preview
    if action_id == "request_rollback":
        readiness = snapshot.get("rollback", {}).get("readiness", {}) if isinstance(snapshot.get("rollback"), dict) else {}
        preview["explanation"] = "Rollback is ultimately a mutating action, but this dashboard surface can only preview readiness and queue symbolic intent until a repo-owned rollback actor validates immutable evidence and explicit user approval."
        preview["consequences"] = [
            "A future rollback actor would run in a new work block, not inline from the dashboard.",
            "The actor would prefer Git-safe strategies such as revert, recovery-branch restore, preservation-ref promotion, or path restore from immutable snapshots.",
        ]
        preview["safeguards"] = [
            "Latest/current dashboard feeds are never accepted as rollback evidence.",
            "Manifest validation requires target head, policy hash, immutable source snapshot hashes, and audited recovery commands.",
            "Reset-hard and force-push remain forbidden defaults without an explicit user request.",
        ]
        preview["nextSteps"] = [
            "Produce or select an immutable history snapshot plus matching repo-closed and repo-state audits.",
            "Validate a closeout-rollback-manifest.v1 before asking a future rollback actor to proceed.",
        ]
        preview["rollback"] = {
            "preferredStrategy": snapshot.get("rollback", {}).get("preferredStrategy"),
            "allowedStrategies": list(snapshot.get("rollback", {}).get("allowedStrategies") or []),
            "validatorCommand": snapshot.get("rollback", {}).get("validatorCommand"),
            "validatorActionability": snapshot.get("rollback", {}).get("validatorActionability"),
            "manifestRoot": snapshot.get("rollback", {}).get("manifestRoot"),
            "requiredManifestSchema": action.get("requiredManifestSchema"),
            "requiredManifestFields": list(action.get("requiredManifestFields") or []),
            "readiness": readiness,
            "userFacingFeasibility": snapshot.get("rollback", {}).get("userFacingFeasibility"),
        }
        return preview
    current_state = repo_closed_postcondition_state(repo_root, config, write_artifacts=False)
    candidate_reports = _candidate_report_rows(
        repo_root,
        config,
        candidate_id=str(request.get("candidateId") or "").strip() or None,
    )
    preview["explanation"] = "This preview explains why the repo is or is not pristine right now, using the same repo-closed postcondition logic the closeout flow trusts. A retained-remediation actor would still revalidate the exact tuple before any cleanup."
    preview["consequences"] = [
        "A repo-owned actor would remediate one retained candidate at a time rather than sweeping multiple risky changes together.",
        "Candidate cleanup still depends on exact tuple revalidation, recovery evidence, and review quorum where policy requires it.",
    ]
    preview["safeguards"] = [
        "Preview uses repo-closed truth without writing repo-closed artifacts or audits.",
        "The dashboard cannot prune, merge, switch worktrees, or delete stashes directly.",
        "Recorded requests remain evidence only until the repo-owned actor revalidates candidate id, action id, evidence hash, policy hash, and pinned refs.",
    ]
    preview["nextSteps"] = [
        "Inspect the blocker summary to understand why hard-clean is currently blocked or clean.",
        "Use the latest retained candidate report to populate the exact tuple when you want to queue a symbolic remediation request.",
    ]
    preview["repoClosedPostcondition"] = {
        "status": current_state.get("status"),
        "repoClosed": bool(current_state.get("ok")),
        "reason": current_state.get("reason"),
        "closeoutCleanTruth": current_state.get("closeoutCleanTruth"),
        "blockerCount": len(current_state.get("blockers") or []),
    }
    preview["blockerSummary"] = _blocker_preview_rows(current_state.get("blockers") if isinstance(current_state.get("blockers"), list) else [])
    preview["candidateReports"] = candidate_reports
    return preview


def dashboard_action_request_payload(
    repo_root_arg: Path,
    request: Dict[str, Any],
    *,
    server_process_id: Optional[int] = None,
) -> Dict[str, Any]:
    if not isinstance(request, dict):
        raise HygieneError("dashboard action request body must be a JSON object")
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    actions = dashboard_actions_payload(repo_root, server_process_id=server_process_id)
    by_id = {str(item.get("id")): item for item in actions["symbolicActions"]}
    action_id = str(request.get("actionId") or request.get("id") or "").strip()
    if not SAFE_ACTION_ID.match(action_id) or action_id not in by_id:
        raise HygieneError("unsupported dashboard symbolic action request: %s" % (action_id or "<missing>"))
    action = by_id[action_id]
    exact_tuple = request.get("exactTuple") if isinstance(request.get("exactTuple"), dict) else {}
    required_tuple_fields = list(action.get("exactTupleRequired") or [])
    missing_tuple_fields = [
        field
        for field in required_tuple_fields
        if field not in exact_tuple or _empty_exact_tuple_value(exact_tuple.get(field))
    ]
    if missing_tuple_fields:
        raise HygieneError("dashboard symbolic action request missing exact tuple fields: %s" % ", ".join(missing_tuple_fields))
    server_pid = _request_int(
        server_process_id if server_process_id is not None else os.getpid(),
        "serverProcessId",
        min_value=1,
        max_value=MAX_PROCESS_ID,
    )
    observed_pid_fields = ["serverProcessId", "observedServerProcessId"]
    supplied_pid_fields = [field for field in observed_pid_fields if field in request]
    if "serverProcessId" not in request:
        raise HygieneError("dashboard action request missing serverProcessId")
    for field in supplied_pid_fields:
        if _request_int(request.get(field), field, min_value=1, max_value=MAX_PROCESS_ID) != server_pid:
            raise HygieneError("stale dashboard helper process id")
    helper = actions["helper"]
    stale_after_ms = _request_int(
        _configured_value(helper, "staleAfterMs", 15000),
        "helper.staleAfterMs",
        min_value=DASHBOARD_MIN_INTERVAL_MS,
        max_value=DASHBOARD_MAX_INTERVAL_MS,
    )
    now_ms = int(time.time() * 1000)
    if "helperObservedAtMs" in request:
        observed_at_ms = request["helperObservedAtMs"]
    elif "observedAtMs" in request:
        observed_at_ms = request["observedAtMs"]
    else:
        raise HygieneError("dashboard action request missing helperObservedAtMs")
    observed_at_ms = _request_int(observed_at_ms, "helperObservedAtMs")
    if now_ms - observed_at_ms > stale_after_ms:
        raise HygieneError("stale dashboard helper state")
    if observed_at_ms - now_ms > HELPER_FUTURE_SKEW_MS:
        raise HygieneError("dashboard helper observation timestamp is in the future")
    snapshot = repo_state_snapshot(repo_root, write=False)
    snapshot_hash = sha256_text(json.dumps(snapshot, sort_keys=True, default=str))
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    packet = {
        "schema": DASHBOARD_ACTION_REQUEST_SCHEMA,
        "status": "recorded",
        "createdAt": created_at,
        "actionId": action_id,
        "actionability": action.get("actionability"),
        "requestOnlyReason": action.get("requestOnlyReason") or action.get("readinessReason"),
        "mutationBoundary": "repo-owned symbolic actors only",
        "noDirectMutation": True,
        "repoRoot": str(repo_root),
        "repoRootHash": actions["repoRootHash"],
        "serverProcessId": server_pid,
        "helperFreshness": {
            "observedAtMs": observed_at_ms,
            "receivedAtMs": now_ms,
            "staleAfterMs": stale_after_ms,
            "fresh": True,
        },
        "repoStateHash": snapshot_hash,
        "exactTupleRequired": required_tuple_fields,
        "exactTuple": exact_tuple,
        "rollbackManifest": {
            "requiredManifestSchema": action.get("requiredManifestSchema"),
            "requiredManifestFields": action.get("requiredManifestFields"),
            "requiresUserApproval": action.get("requiresUserApproval"),
            "requiresImmutableSourceSnapshot": action.get("requiresImmutableSourceSnapshot"),
        },
        "userIntent": str(request.get("userIntent") or ""),
    }
    request_root = dashboard_action_request_root(repo_root, config)
    request_root.mkdir(parents=True, exist_ok=True)
    safe_action = re.sub(r"[^A-Za-z0-9_.-]+", "-", action_id).strip("-") or "action"
    packet_hash = sha256_text(json.dumps(packet, sort_keys=True, default=str))[:12]
    packet_name = "%s-%s-%s.json" % (created_at.replace(":", "").replace("+", "Z"), safe_action, packet_hash)
    packet_path = request_root / packet_name
    packet["requestPath"] = normalize_rel(str(packet_path.relative_to(repo_root)))
    with packet_path.open("w", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return packet


def latest_repo_state_payload(repo_root_arg: Path) -> Dict[str, Any]:
    return repo_state_snapshot(repo_root_arg, write=True, latest_only=True)


def history_index_payload(repo_root_arg: Path) -> Dict[str, Any]:
    snapshot = repo_state_snapshot(repo_root_arg, write=False)
    history = snapshot.get("closeout", {}).get("history", {})
    state_ledger = snapshot.get("stateLedger", {})
    return {
        "schema": history.get("schema") or "closeout-history-index.v1",
        "status": "success",
        "historyRoot": state_ledger.get("historyRoot"),
        "entryCount": history.get("entryCount", 0),
        "workBlockCount": history.get("workBlockCount", 0),
        "skippedCount": history.get("skippedCount", 0),
        "errors": history.get("errors", []),
        "entries": history.get("entries", []),
        "recentWorkBlocks": history.get("recentWorkBlocks", []),
        "limit": history.get("limit"),
    }


def history_snapshot_payload(repo_root_arg: Path, snapshot_id: str) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    candidate = unquote(snapshot_id or "").strip()
    if not SAFE_HISTORY_ID.match(candidate):
        raise HygieneError("invalid history snapshot id")
    if not candidate.endswith(".json"):
        candidate = f"{candidate}.json"
    history_root = repo_state_path(repo_root, config, "historyRoot", ".claude-state/closeout/repo-state/history").resolve()
    path = (history_root / candidate).resolve()
    if history_root not in path.parents and path != history_root:
        raise HygieneError("history snapshot path escaped history root")
    if not path.exists():
        raise HygieneError("history snapshot not found: %s" % candidate)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload.setdefault("stateLedger", {})
    payload["stateLedger"]["servedHistorySnapshotId"] = candidate
    return payload


def dashboard_html(config: Dict[str, Any]) -> str:
    dashboard = repo_state_dashboard_spec(config)
    endpoints = dashboard_endpoints(config)
    title = "Closeout Dashboard"
    escaped_endpoints = html.escape(json.dumps(endpoints, sort_keys=True), quote=True)
    preserved_keys = list(dashboard.get("preservedClientStateKeys") or [])
    escaped_preserved_keys = html.escape(json.dumps(preserved_keys, sort_keys=True), quote=True)
    auto_refresh = _request_int(
        _configured_value(dashboard, "autoRefreshMs", 5000),
        "dashboard.autoRefreshMs",
        min_value=DASHBOARD_MIN_INTERVAL_MS,
        max_value=DASHBOARD_MAX_INTERVAL_MS,
    )
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5f6b7a;
      --line: #dce3ec;
      --accent: #146c94;
      --warn: #9a5a00;
      --ok: #177245;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #11151b;
        --panel: #181f27;
        --text: #edf3f8;
        --muted: #a6b3c0;
        --line: #2b3642;
        --accent: #65c7f7;
        --warn: #ffc15a;
        --ok: #7ee0a3;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 "Segoe UI", Arial, sans-serif;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid var(--line);
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      backdrop-filter: blur(12px);
      padding: 14px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1, h2 {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 20px; }}
    h2 {{ font-size: 16px; margin-bottom: 10px; }}
    main {{ width: min(1200px, 100%); margin: 0 auto; padding: 18px; display: grid; gap: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }}
    .full {{ grid-column: 1 / -1; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      color: var(--muted);
      background: transparent;
      white-space: nowrap;
    }}
    .chip.ok {{ color: var(--ok); }}
    .chip.warn {{ color: var(--warn); }}
    button {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 10px;
      color: var(--text);
      background: var(--panel);
      cursor: pointer;
    }}
    button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    code, pre {{ font-family: Consolas, "SFMono-Regular", monospace; }}
    pre {{ overflow: auto; max-height: 360px; margin: 0; color: var(--muted); }}
    .muted {{ color: var(--muted); }}
    .actions-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
    .action-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      display: grid;
      gap: 8px;
      background: color-mix(in srgb, var(--panel) 94%, transparent);
    }}
    .action-card h3 {{ margin: 0; font-size: 15px; }}
    .action-copy {{ margin: 0; color: var(--muted); }}
    .stack {{ display: grid; gap: 12px; }}
    .split {{ display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .list {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    .callout {{
      border-left: 3px solid var(--accent);
      padding-left: 12px;
      color: var(--muted);
    }}
    .tuple-grid {{ display: grid; gap: 8px; }}
    .tuple-grid .tuple-row {{ display: grid; gap: 6px; }}
    label.tuple-label {{ display: grid; gap: 4px; font-size: 12px; }}
    .tuple-grid input {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px;
      color: var(--text);
      background: var(--panel);
      min-width: 0;
    }}
    .tuple-grid input::placeholder {{ color: var(--muted); }}
    @media (max-width: 780px) {{ .grid {{ grid-template-columns: 1fr; }} header {{ align-items: flex-start; flex-direction: column; }} }}
    @media (max-width: 780px) {{ .split {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body data-endpoints=\"{escaped_endpoints}\" data-refresh-ms=\"{auto_refresh}\" data-preserved-client-state-keys=\"{escaped_preserved_keys}\">
  <header>
    <div>
      <h1>Closeout Dashboard</h1>
      <div class=\"muted\" id=\"subtitle\">Repo-owned state feed</div>
    </div>
    <div class=\"chips\">
      <span class=\"chip\" id=\"refresh-status\">Idle</span>
      <button id=\"refresh-button\" type=\"button\">Refresh</button>
    </div>
  </header>
  <main>
    <section class=\"grid\">
      <article class=\"panel\">
        <h2>Repo State</h2>
        <div class=\"chips\" id=\"repo-chips\"></div>
      </article>
      <article class=\"panel\">
        <h2>Rollback Readiness</h2>
        <div class=\"chips\" id=\"rollback-chips\"></div>
      </article>
      <article class=\"panel full\">
        <h2>Dirty Files</h2>
        <div id=\"dirty-table\" class=\"muted\">Loading...</div>
      </article>
      <article class=\"panel full\">
        <h2>Closeout History</h2>
        <div id=\"history-table\" class=\"muted\">Loading...</div>
      </article>
      <article class=\"panel full\">
        <h2>Action Controls</h2>
        <div id=\"action-cards\" class=\"actions-grid muted\">Loading...</div>
      </article>
      <article class=\"panel full\">
        <h2>Action Preview</h2>
        <div id=\"action-preview\" class=\"muted\">Choose an action to see a dry-run explanation.</div>
      </article>
      <article class=\"panel full\">
        <h2>Action Metadata</h2>
        <pre id=\"actions-json\">Loading...</pre>
      </article>
    </section>
  </main>
  <script>
    const endpoints = JSON.parse(document.body.dataset.endpoints);
    const refreshMs = Number(document.body.dataset.refreshMs || 5000);
    const preservedClientStateKeys = JSON.parse(document.body.dataset.preservedClientStateKeys || "[]");
    const stateKey = "mlv-closeout-dashboard-state";
    const cachedPreviews = {{}};
    let latestActions = null;
    function selectedActionIdFromUrl() {{
      const params = new URLSearchParams(window.location.search);
      return params.get("actionId") || params.get("action") || "";
    }}
    function syncActionInUrl(actionId) {{
      try {{
        const next = new URL(window.location.href);
        if(actionId) {{
          next.searchParams.set("actionId", actionId);
        }} else {{
          next.searchParams.delete("actionId");
          next.searchParams.delete("action");
        }}
        window.history.replaceState({{}}, "", next.toString());
      }} catch(_err) {{}}
    }}
    function setSelectedActionState(actionId) {{
      const stored = JSON.parse(localStorage.getItem(stateKey) || "{{}}");
      stored.selectedActionId = actionId || "";
      localStorage.setItem(stateKey, JSON.stringify(stored));
      syncActionInUrl(actionId);
    }}
    function byId(id) {{ return document.getElementById(id); }}
    function safeDomId(value) {{
      return String(value ?? "").replace(/[^A-Za-z0-9_-]+/g, "-");
    }}
    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>\"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[c]));
    }}
    function chip(label, value, tone) {{ return `<span class=\"chip ${{tone || ""}}\">${{escapeHtml(label)}}: ${{escapeHtml(value)}}</span>`; }}
    function table(rows, columns) {{
      if(!rows.length) return '<span class="muted">None</span>';
      return `<table><thead><tr>${{columns.map(c => `<th>${{escapeHtml(c.label)}}</th>`).join("")}}</tr></thead><tbody>${{rows.map(row => `<tr>${{columns.map(c => `<td>${{escapeHtml(row[c.key] ?? "")}}</td>`).join("")}}</tr>`).join("")}}</tbody></table>`;
    }}
    function listHtml(items, emptyText) {{
      if(!items || !items.length) return `<span class="muted">${{escapeHtml(emptyText || "None")}}</span>`;
      return `<ul class="list">${{items.map(item => `<li>${{escapeHtml(item)}}</li>`).join("")}}</ul>`;
    }}
    async function getJson(path) {{
      const response = await fetch(path, {{cache: "no-store"}});
      if(!response.ok) throw new Error(`${{response.status}} ${{response.statusText}}`);
      return response.json();
    }}
    function actionPreviewUrl(actionId) {{
      const url = new URL(endpoints.actionsPreview, window.location.origin);
      url.searchParams.set("actionId", actionId);
      return url.pathname + url.search;
    }}
    function actionCard(action) {{
      const tone = String(action.actionability || "").includes("read-only") ? "warn" : "ok";
      const reason = action.requestOnlyReason || action.readinessReason || action.commandPolicy || "";
      return `
        <article class="action-card">
          <h3>${{escapeHtml(action.label || action.id || "Action")}}</h3>
          <div class="chips">
            ${{chip("id", action.id || "")}}
            ${{chip("actionability", action.actionability || "unknown", tone)}}
          </div>
          <p class="action-copy">${{escapeHtml(reason || "Preview the exact safeguards and consequences before you queue a request.")}}</p>
          <div class="chips">
            <button type="button" class="preview-button" data-action-id="${{escapeHtml(action.id || "")}}">Preview</button>
          </div>
        </article>`;
    }}
    function renderActionCards(actions) {{
      const items = actions.symbolicActions || [];
      byId("action-cards").innerHTML = items.length ? items.map(actionCard).join("") : '<span class="muted">No actions available.</span>';
      for (const button of document.querySelectorAll(".preview-button")) {{
        button.addEventListener("click", () => {{
          const actionId = button.dataset.actionId || "";
          setSelectedActionState(actionId);
          void loadActionPreview(actionId);
        }});
      }}
    }}
    function setRequestFeedback(message) {{
      const feedback = byId("action-request-feedback");
      if(feedback) {{
        feedback.textContent = String(message ?? "");
      }}
    }}
    function tupleInputId(actionId, field) {{
      return `tuple-${{safeDomId(actionId || "action")}}-${{safeDomId(field)}}`;
    }}
    function tupleTemplateValue(template, field) {{
      if(!template || typeof template !== "object") return "";
      const raw = template[field];
      if(raw === undefined || raw === null) return "";
      if(typeof raw === "string") return raw;
      return JSON.stringify(raw);
    }}
    function parseTupleValue(rawValue) {{
      const text = String(rawValue ?? "").trim();
      if(!text) return null;
      if(text === "<required>") return "<required>";
      if(text.startsWith(String.fromCharCode(123)) || text.startsWith("[") || text === "true" || text === "false" || /^-?\\d+(?:\\.\\d+)?$/.test(text)) {{
        try {{ return JSON.parse(text); }}
        catch (_err) {{ return text; }}
      }}
      return text;
    }}
    function renderActionPreview(preview) {{
      if(preview && preview.actionId) {{
        cachedPreviews[preview.actionId] = preview;
      }}
      const current = preview.repoClosedPostcondition || {{}};
      const cleanTruth = current.closeoutCleanTruth || {{}};
      const blockerRows = (preview.blockerSummary || []).map(row => ({{
        kind: row.kind || "",
        count: row.count ?? "",
        sample: (row.samplePaths || []).join(", "),
        detail: row.detail || ""
      }}));
      const candidateRows = ((preview.candidateReports || {{}}).rows || []).map(row => ({{
        candidateId: row.candidateId || "",
        recommendedAction: row.recommendedAction || "",
        blockers: (row.blockers || []).join(", "),
        recoveryCommand: row.recoveryCommand || ""
      }}));
      const rollback = preview.rollback || {{}};
      const readiness = rollback.readiness || {{}};
      const requestTemplate = preview.requestTemplate && Object.keys(preview.requestTemplate).length
        ? JSON.stringify(preview.requestTemplate, null, 2)
        : "";
      const exactTupleFields = Array.isArray(preview.exactTupleRequired) ? preview.exactTupleRequired : [];
      const templateTuple = preview.requestTemplate && preview.requestTemplate.exactTuple && typeof preview.requestTemplate.exactTuple === "object"
        ? preview.requestTemplate.exactTuple
        : {{}};
      const canRequest = exactTupleFields.length > 0;
      const exactTupleInputs = exactTupleFields.map(field => `
        <div class="tuple-row">
          <label class="tuple-label">
            ${{escapeHtml(field)}}
            <input
              id="${{tupleInputId(preview.actionId || "action", field)}}"
              type="text"
              autocomplete="off"
              placeholder="${{escapeHtml(tupleTemplateValue(templateTuple, field) || "required")}}"
            />
          </label>
        </div>
      `).join("");
      const requestSection = canRequest ? `
        <div>
          <h3>Queue symbolic request</h3>
          <div class="muted">Populate required exact-tuple fields to queue a symbolic request packet.</div>
          <div class="tuple-grid">${{exactTupleInputs}}</div>
          <label class="tuple-label">
            User intent (optional)
            <input id="action-request-intent" type="text" placeholder="Optional operator/audit intent" />
          </label>
          <div class="stack">
            <button type="button" id="queue-action-button" data-action-id="${{escapeHtml(preview.actionId || "")}}">Queue request</button>
            <span id="action-request-feedback" class="muted">Ready to queue.</span>
          </div>
        </div>`
        : `<div><h3>Queue symbolic request</h3><div class="muted">No exact tuple required for this action.</div></div>`;
      byId("action-preview").innerHTML = `
        <div class="stack">
          <div class="chips">
            ${{chip("action", preview.label || preview.actionId || "")}}
            ${{chip("actionability", preview.actionability || "unknown", String(preview.actionability || "").includes("read-only") ? "warn" : "ok")}}
            ${{chip("mutates now", preview.wouldMutateNow ? "yes" : "no", preview.wouldMutateNow ? "warn" : "ok")}}
            ${{current.status ? chip("repo closed", current.repoClosed ? "clean" : current.status, current.repoClosed ? "ok" : "warn") : ""}}
            ${{readiness.actionability ? chip("rollback readiness", readiness.actionability, readiness.evidenceFresh ? "ok" : "warn") : ""}}
          </div>
          <div class="callout">${{escapeHtml(preview.explanation || preview.requestOnlyReason || "No preview details available.")}}</div>
          <div class="split">
            <div>
              <h3>Consequences</h3>
              ${{listHtml(preview.consequences || [], "No notable consequences recorded.")}}
            </div>
            <div>
              <h3>Safeguards</h3>
              ${{listHtml(preview.safeguards || [], "No safeguards recorded.")}}
            </div>
          </div>
          <div class="split">
            <div>
              <h3>Next Steps</h3>
              ${{listHtml(preview.nextSteps || [], "No next steps recorded.")}}
            </div>
            <div>
              <h3>Exact Tuple</h3>
              ${{listHtml(exactTupleFields, "No exact tuple required.")}}
            </div>
          </div>
          ${{requestSection}}
          <div>
            <h3>Current Blockers</h3>
            ${{table(blockerRows, [
              {{key:"kind", label:"Kind"}},
              {{key:"count", label:"Count"}},
              {{key:"sample", label:"Sample"}},
              {{key:"detail", label:"Detail"}}
            ])}}
          </div>
          <div>
            <h3>Retained Candidate Reports</h3>
            <div class="muted">Source: ${{escapeHtml((preview.candidateReports || {{}}).reportRoot || "n/a")}}</div>
            ${{table(candidateRows, [
              {{key:"candidateId", label:"Candidate"}},
              {{key:"recommendedAction", label:"Recommended"}},
              {{key:"blockers", label:"Blockers"}},
              {{key:"recoveryCommand", label:"Recovery"}}
            ])}}
          </div>
          <div>
            <h3>Request Template</h3>
            <pre>${{escapeHtml(requestTemplate || "This action does not need a request template preview.")}}</pre>
          </div>
          <div>
            <h3>Rollback Detail</h3>
            <div class="chips">
              ${{rollback.preferredStrategy ? chip("preferred", rollback.preferredStrategy) : ""}}
              ${{rollback.validatorActionability ? chip("validator", rollback.validatorActionability) : ""}}
              ${{readiness.evidenceStatus ? chip("evidence", readiness.evidenceStatus, readiness.evidenceFresh ? "ok" : "warn") : ""}}
            </div>
            ${{listHtml(rollback.allowedStrategies || [], "No rollback strategy detail for this action.")}}
          </div>
          <div>
            <h3>Closeout Truth</h3>
            <pre>${{escapeHtml(JSON.stringify(cleanTruth, null, 2) || "{{}}")}}</pre>
          </div>
        </div>`;
      if(canRequest) {{
        const requestButton = byId("queue-action-button");
        if(requestButton) {{
          requestButton.addEventListener("click", () => {{
            void requestSymbolicAction(preview.actionId || "");
          }});
        }}
      }}
    }}
    async function requestSymbolicAction(actionId) {{
      const actionPreview = cachedPreviews[actionId] || {{}};
      const required = Array.isArray(actionPreview.exactTupleRequired) ? actionPreview.exactTupleRequired : [];
      if(!actionId || required.length === 0) {{
        setRequestFeedback("No actionable exact tuple is defined for this action.");
        return;
      }}
      try {{
        const exactTuple = {{}};
        for (const field of required) {{
          const input = byId(tupleInputId(actionId || "action", field));
          const value = parseTupleValue(input ? input.value : "");
          if(value === null || value === undefined || value === "") {{
            setRequestFeedback(`Missing exact tuple value: ${{field}}`);
            return;
          }}
          if(value === "<required>") {{
            setRequestFeedback(`Replace placeholder tuple value for ${{field}}`);
            return;
          }}
          exactTuple[field] = value;
        }}
        if(!latestActions || !Number.isFinite(Number(latestActions.serverProcessId))) {{
          setRequestFeedback("Refresh the dashboard first to capture helper freshness.");
          return;
        }}
        const request = {{
          actionId,
          serverProcessId: Number(latestActions.serverProcessId),
          helperObservedAtMs: Date.now(),
          exactTuple,
          userIntent: String((byId("action-request-intent") && byId("action-request-intent").value) || "").trim(),
        }};
        setRequestFeedback("Submitting symbolic request...");
        const response = await fetch(endpoints.actionsRequest, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(request),
        }});
        if(!response.ok) {{
          const message = await response.text();
          throw new Error(`${{response.status}} ${{response.statusText}}: ${{message}}`);
        }}
        const payload = await response.json();
        if(!payload || typeof payload !== "object") {{
          throw new Error("Malformed response from action request endpoint.");
        }}
        setRequestFeedback(`Queued symbolic request to ${{payload.requestPath || "repo-owned packet"}}`);
      }} catch(error) {{
        setRequestFeedback(String(error.message || error));
      }}
    }}
    async function loadActionPreview(actionId) {{
      if(!actionId || !endpoints.actionsPreview) return;
      setSelectedActionState(actionId);
      setRequestFeedback("Loading...");
      byId("action-preview").textContent = "Loading preview...";
      try {{
        const preview = await getJson(actionPreviewUrl(actionId));
        renderActionPreview(preview);
      }} catch(error) {{
        setRequestFeedback(String(error.message || error));
        byId("action-preview").textContent = String(error.message || error);
      }}
    }}
    function configuredClientState() {{
      const stored = JSON.parse(localStorage.getItem(stateKey) || "{{}}");
      const state = {{}};
      for (const key of preservedClientStateKeys) {{
        if (key === "scrollPosition") state.scrollPosition = {{x: window.scrollX, y: window.scrollY}};
        else if (key === "focusedElement") state.focusedElement = document.activeElement && document.activeElement.id || "";
        else if (key === "selectedWorkBlockId") state.selectedWorkBlockId = stored.selectedWorkBlockId || "";
        else if (key === "expandedRows") state.expandedRows = Array.isArray(stored.expandedRows) ? stored.expandedRows : [];
        else if (key === "activeHistoryFilters") state.activeHistoryFilters = stored.activeHistoryFilters && typeof stored.activeHistoryFilters === "object" ? stored.activeHistoryFilters : {{}};
      }}
      state.scrollY = window.scrollY;
      state.focusedId = document.activeElement && document.activeElement.id || "";
      return state;
    }}
    function saveClientState() {{
      localStorage.setItem(stateKey, JSON.stringify(configuredClientState()));
    }}
    function restoreClientState() {{
      try {{
        const stored = JSON.parse(localStorage.getItem(stateKey) || "{{}}");
        const focused = stored.focusedElement || stored.focusedId;
        if(focused && byId(focused)) byId(focused).focus({{preventScroll: true}});
        const scroll = stored.scrollPosition && Number.isFinite(stored.scrollPosition.y) ? stored.scrollPosition.y : stored.scrollY;
        if(Number.isFinite(scroll)) window.scrollTo({{top: scroll, behavior: "instant"}});
      }} catch(_err) {{}}
    }}
    async function refresh() {{
      saveClientState();
      byId("refresh-status").textContent = "Refreshing";
      try {{
        const [latest, history, actions] = await Promise.all([
          getJson(endpoints.latest),
          getJson(endpoints.historyIndex),
          getJson(endpoints.actions)
        ]);
        byId("subtitle").textContent = latest.repo.root;
        const branch = latest.branch || {{}};
        byId("repo-chips").innerHTML = [
          chip("branch", branch.currentBranch || "detached"),
          chip("head", String(branch.head || "").slice(0, 12)),
          chip("dirty", latest.dirty.clean ? "clean" : latest.dirty.entryCount, latest.dirty.clean ? "ok" : "warn"),
          chip("worktrees", (latest.worktrees || []).length),
          chip("stashes", (latest.stashes || []).length)
        ].join("");
        const readiness = ((latest.rollback || {{}}).readiness || {{}});
        byId("rollback-chips").innerHTML = [
          chip("actionability", readiness.actionability || "unknown", readiness.evidenceFresh ? "ok" : "warn"),
          chip("evidence", readiness.evidenceStatus || "unknown"),
          chip("latest feed evidence", readiness.latestFeedIsRollbackEvidence ? "yes" : "no")
        ].join("");
        byId("dirty-table").innerHTML = table(latest.dirty.entries || [], [
          {{key:"xy", label:"Status"}},
          {{key:"path", label:"Path"}}
        ]);
        latestActions = actions;
        byId("history-table").innerHTML = table(history.entries || [], [
          {{key:"workBlockId", label:"Work block"}},
          {{key:"latestAuditType", label:"Latest audit"}},
          {{key:"latestOutcome", label:"Outcome"}},
          {{key:"latestSeenAt", label:"Seen"}}
        ]);
        renderActionCards(actions);
        byId("actions-json").textContent = JSON.stringify(actions, null, 2);
        const stored = JSON.parse(localStorage.getItem(stateKey) || "{{}}");
        const actionIds = new Set((actions.symbolicActions || []).map(item => String(item.id || "")));
        const selectedActionId = (
          selectedActionIdFromUrl() && actionIds.has(selectedActionIdFromUrl())
            ? selectedActionIdFromUrl()
            : stored.selectedActionId && actionIds.has(stored.selectedActionId)
              ? stored.selectedActionId
              : (((actions.symbolicActions || [])[0] || {{}}).id || "")
        );
        if(selectedActionId) {{
          setSelectedActionState(selectedActionId);
          syncActionInUrl(selectedActionId);
        }}
        if(selectedActionId) await loadActionPreview(selectedActionId);
        byId("refresh-status").textContent = "Updated " + new Date().toLocaleTimeString();
        restoreClientState();
      }} catch(error) {{
        byId("refresh-status").textContent = "Error";
        byId("actions-json").textContent = String(error);
        byId("action-preview").textContent = String(error);
      }}
    }}
    let pollingTimer = null;
    function startPolling() {{
      if(!pollingTimer) pollingTimer = window.setInterval(refresh, refreshMs);
    }}
    function startEventStream() {{
      if(!("EventSource" in window) || !endpoints.events) {{
        startPolling();
        return;
      }}
      try {{
        const source = new EventSource(endpoints.events);
        source.addEventListener("ready", refresh);
        source.addEventListener("repo-state", refresh);
        source.onerror = () => {{
          source.close();
          startPolling();
        }};
      }} catch(_err) {{
        startPolling();
      }}
    }}
    byId("refresh-button").addEventListener("click", refresh);
    window.addEventListener("beforeunload", saveClientState);
    refresh();
    startEventStream();
  </script>
</body>
</html>"""


class CloseoutDashboardHandler(BaseHTTPRequestHandler):
    server: "CloseoutDashboardServer"

    def _write_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_html(self, payload: str) -> None:
        data = payload.encode("utf-8")
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_error(self, status: HTTPStatus, message: str) -> None:
        self._write_json({"status": "error", "error": message}, status=status)

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query, keep_blank_values=True)
        endpoints = dashboard_endpoints(self.server.config)
        try:
            if path in {"/", endpoints["page"].rstrip("/")}:
                self._write_html(dashboard_html(self.server.config))
                return
            if path == endpoints["latest"]:
                self._write_json(latest_repo_state_payload(self.server.repo_root))
                return
            if path == endpoints["historyIndex"]:
                self._write_json(history_index_payload(self.server.repo_root))
                return
            if path == endpoints["actions"]:
                self._write_json(dashboard_actions_payload(self.server.repo_root))
                return
            if path == endpoints["actionsPreview"]:
                self._write_json(
                    dashboard_action_preview_payload(
                        self.server.repo_root,
                        {
                            "actionId": (query.get("actionId") or query.get("id") or [""])[0],
                            "candidateId": (query.get("candidateId") or [""])[0],
                        },
                    )
                )
                return
            prefix = endpoints["historySnapshot"].split("{snapshotId}", 1)[0].rstrip("/")
            if path.startswith(prefix + "/"):
                snapshot_id = path[len(prefix) + 1 :]
                self._write_json(history_snapshot_payload(self.server.repo_root, snapshot_id))
                return
            if path == endpoints["events"]:
                self.send_response(HTTPStatus.OK.value)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                interval = max(
                    1.0,
                    _request_int(
                        _configured_value(repo_state_dashboard_spec(self.server.config), "autoRefreshMs", 5000),
                        "dashboard.autoRefreshMs",
                        min_value=DASHBOARD_MIN_INTERVAL_MS,
                        max_value=DASHBOARD_MAX_INTERVAL_MS,
                    )
                    / 1000.0,
                )
                ready_payload = json.dumps({"status": "ready", "endpoint": endpoints["latest"]}, sort_keys=True)
                self.wfile.write(f"event: ready\ndata: {ready_payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                try:
                    while True:
                        time.sleep(interval)
                        payload = json.dumps(
                            {"status": "tick", "endpoint": endpoints["latest"], "serverProcessId": os.getpid()},
                            sort_keys=True,
                        )
                        self.wfile.write(f"event: repo-state\ndata: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                return
            self._write_error(HTTPStatus.NOT_FOUND, "unknown closeout dashboard route")
        except HygieneError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover - final fail-closed boundary
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        endpoints = dashboard_endpoints(self.server.config)
        try:
            if path != endpoints["actionsRequest"]:
                self._write_error(HTTPStatus.NOT_FOUND, "unknown closeout dashboard route")
                return
            length = _request_int(self.headers.get("Content-Length") or "0", "Content-Length", min_value=0, max_value=65536)
            if length <= 0 or length > 65536:
                raise HygieneError("dashboard action request body must be 1-65536 bytes")
            raw = self.rfile.read(length).decode("utf-8")
            request = json.loads(raw)
            self._write_json(
                dashboard_action_request_payload(
                    self.server.repo_root,
                    request,
                    server_process_id=os.getpid(),
                ),
                status=HTTPStatus.CREATED,
            )
        except json.JSONDecodeError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid JSON request body: %s" % exc)
        except HygieneError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover - final fail-closed boundary
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return


class CloseoutDashboardServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], repo_root: Path) -> None:
        super().__init__(server_address, handler_class)
        self.repo_root = resolve_repo_root(repo_root)
        self.config = load_closeout_config(self.repo_root)


def run_server(repo_root: Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise HygieneError("closeout dashboard may only bind a localhost address")
    server = CloseoutDashboardServer((host, _request_int(port, "port", min_value=1, max_value=65535)), CloseoutDashboardHandler, repo_root)
    print(json.dumps(dashboard_actions_payload(server.repo_root, server_process_id=os.getpid()), indent=2, sort_keys=True), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the read-only closeout dashboard.")
    parser.add_argument("--repo-root", default=".", help="Path inside the target Git repo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        run_server(Path(args.repo_root), host=args.host, port=args.port)
        return 0
    except HygieneError as exc:
        print("closeout dashboard error: %s" % exc, flush=True)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
