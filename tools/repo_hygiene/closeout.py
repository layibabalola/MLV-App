from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .core import (
    IMPLEMENTED_CLOSEOUT_ACTION_IDS,
    IMPLEMENTED_CLOSEOUT_PUBLISH_MODES,
    IMPLEMENTED_CLOSEOUT_STATES,
    IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS,
    POLICY_VERSION,
    HygieneError,
    apply_mutex,
    build_plan,
    canonical_json,
    load_config,
    normalize_rel,
    path_matches,
    resolve_repo_root,
    run_git,
    sha256_text,
    stable_id,
    state_root,
    transient_git_state,
    utc_now,
    write_json,
)


CLOSEOUT_SCHEMA_VERSION = "1.0"
TX_STATES = IMPLEMENTED_CLOSEOUT_STATES
PUBLISH_MODES = IMPLEMENTED_CLOSEOUT_PUBLISH_MODES
CLOSEOUT_ACTION_IDS = IMPLEMENTED_CLOSEOUT_ACTION_IDS
TRIGGER_SIGNAL_IDS = IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS
HYGIENE_CLEANUP_ACTION_IDS = {"retain", "ask", "stash_promote", "orphan_quarantine", "branch_archive_delete", "worktree_remove"}
FORBIDDEN_RECOMMENDATION_KEYS = {
    "cmd",
    "command",
    "commands",
    "argv",
    "executable",
    "instructions",
    "shell",
    "script",
    "powershell",
    "bash",
    "subprocess",
}


def closeout_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("closeout", {})


def transactions_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return state_root(repo_root, config) / "transactions"


def transaction_dir(repo_root: Path, config: Dict[str, Any], tx_id: str) -> Path:
    safe = tx_id.strip()
    if not safe or any(ch in safe for ch in "\\/.."):
        raise HygieneError("invalid closeout transaction id")
    return transactions_root(repo_root, config) / safe


def tx_hash(data: Any) -> str:
    return sha256_text(canonical_json(data), 32)


def stored_artifact_hash(data: Dict[str, Any], hash_field: str) -> str:
    clean = dict(data)
    clean.pop(hash_field, None)
    return tx_hash(clean)


def require_keys(name: str, data: Dict[str, Any], required: Iterable[str], allowed: Iterable[str]) -> None:
    required_set = set(required)
    allowed_set = set(allowed)
    missing = sorted(key for key in required_set if key not in data)
    unknown = sorted(key for key in data if key not in allowed_set)
    if missing:
        raise HygieneError("%s missing required key(s): %s" % (name, ", ".join(missing)))
    if unknown:
        raise HygieneError("%s contains unknown key(s): %s" % (name, ", ".join(unknown)))


def require_type(name: str, value: Any, expected: type) -> None:
    if expected is bool:
        ok = isinstance(value, bool)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, expected)
    if not ok:
        raise HygieneError("%s must be %s" % (name, expected.__name__))


def unsigned_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    clean = dict(data)
    provenance = dict(clean.get("provenance", {})) if isinstance(clean.get("provenance"), dict) else {}
    provenance.pop("signature", None)
    clean["provenance"] = provenance
    return clean


def keyed_signature(tx_id: str, artifact_type: str, payload: Dict[str, Any], provenance_key: str) -> str:
    body = {
        "tx_id": tx_id,
        "artifact_type": artifact_type,
        "payload": payload,
    }
    return hmac.new(provenance_key.encode("utf-8"), canonical_json(body).encode("utf-8"), hashlib.sha256).hexdigest()


def sign_closeout_payload(tx_id: str, artifact_type: str, payload: Dict[str, Any], provenance_key: str) -> str:
    return keyed_signature(tx_id, artifact_type, unsigned_payload(payload), provenance_key)


def validate_signed_provenance(
    state: Dict[str, Any],
    artifact_type: str,
    payload: Dict[str, Any],
    *,
    provenance_key: str,
) -> None:
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise HygieneError("%s provenance is required" % artifact_type)
    require_keys(
        "%s provenance" % artifact_type,
        provenance,
        ["artifact_type", "actor_id", "session_id", "key_hash", "signature"],
        ["artifact_type", "actor_id", "session_id", "adapter_id", "tool_capabilities_hash", "key_hash", "signature"],
    )
    if provenance.get("artifact_type") != artifact_type:
        raise HygieneError("%s provenance artifact_type mismatch" % artifact_type)
    key_hashes = state.get("provenance_key_hashes", {})
    expected_key_hash = key_hashes.get(artifact_type)
    if provenance.get("key_hash") != expected_key_hash:
        raise HygieneError("%s provenance key hash mismatch" % artifact_type)
    if tx_hash({"tx_id": state.get("tx_id"), "artifact_type": artifact_type, "provenance_key": provenance_key}) != expected_key_hash:
        raise HygieneError("%s provenance key does not match transaction" % artifact_type)
    expected = sign_closeout_payload(str(state.get("tx_id")), artifact_type, payload, provenance_key)
    if not hmac.compare_digest(str(provenance.get("signature")), expected):
        raise HygieneError("%s provenance signature mismatch" % artifact_type)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True))
        handle.write("\n")


def artifact_token(value: Any) -> str:
    return sha256_text(str(value), 16)


@contextlib.contextmanager
def transaction_mutex(tx_dir: Path) -> Iterable[Path]:
    lock_path = tx_dir / "transaction.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "started_at": utc_now()}, handle, sort_keys=True)
        yield lock_path
    except Exception:
        raise
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def load_state(tx_dir: Path) -> Dict[str, Any]:
    state = read_json(tx_dir / "state.json", None)
    if not isinstance(state, dict):
        raise HygieneError("closeout transaction state is missing")
    return state


def write_state(tx_dir: Path, state: Dict[str, Any]) -> None:
    write_json(tx_dir / "state.json", state)


def record_event(tx_dir: Path, event: Dict[str, Any]) -> None:
    previous_event_hash: Optional[str] = None
    events_path = tx_dir / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    previous_event_hash = json.loads(line).get("event_hash") or sha256_text(line, 32)
                except json.JSONDecodeError:
                    previous_event_hash = sha256_text(line, 32)
    row = {
        "schema_version": CLOSEOUT_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "created_at": utc_now(),
        "previous_event_hash": previous_event_hash,
        **event,
    }
    row["state_hash"] = tx_hash(read_json(tx_dir / "state.json", {}))
    row["event_hash"] = tx_hash(row)
    append_jsonl(tx_dir / "events.jsonl", row)


def verify_event_chain(tx_dir: Path) -> Dict[str, Any]:
    events_path = tx_dir / "events.jsonl"
    previous_hash: Optional[str] = None
    count = 0
    if not events_path.exists():
        return {"ok": True, "event_count": 0}
    for line_number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HygieneError("event log line %s is not valid JSON: %s" % (line_number, exc))
        event_hash = row.get("event_hash")
        clean = dict(row)
        clean.pop("event_hash", None)
        if tx_hash(clean) != event_hash:
            raise HygieneError("event log hash mismatch at line %s" % line_number)
        if row.get("previous_event_hash") != previous_hash:
            raise HygieneError("event log previous hash mismatch at line %s" % line_number)
        previous_hash = event_hash
        count += 1
    return {"ok": True, "event_count": count, "last_event_hash": previous_hash}


def approved_event_anchor(tx_dir: Path) -> Dict[str, Any]:
    anchor: Optional[Dict[str, Any]] = None
    events_path = tx_dir / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event") == "approved":
                anchor = row
    if not anchor:
        raise HygieneError("approved event anchor is missing")
    return anchor


def event_by_hash(tx_dir: Path, event_hash: str) -> Dict[str, Any]:
    events_path = tx_dir / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event_hash") == event_hash:
                return row
    raise HygieneError("approved event hash is missing from event log")


def write_approval_anchor(tx_dir: Path, state: Dict[str, Any], approval_hash: str, approval_key: str) -> None:
    anchor_event = approved_event_anchor(tx_dir)
    anchor = {
        "schema_version": CLOSEOUT_SCHEMA_VERSION,
        "tx_id": state["tx_id"],
        "approved_event_hash": anchor_event.get("event_hash"),
        "approval_hash": approval_hash,
        "state_hash": anchor_event.get("state_hash"),
        "created_at": utc_now(),
    }
    anchor["signature"] = keyed_signature(state["tx_id"], "approval_anchor", anchor, approval_key)
    write_json(tx_dir / "approval-anchor.json", anchor)


def verify_approval_anchor(tx_dir: Path, state: Dict[str, Any], approval_key: str) -> Dict[str, Any]:
    anchor = read_json(tx_dir / "approval-anchor.json", {})
    require_keys(
        "approval anchor",
        anchor,
        ["schema_version", "tx_id", "approved_event_hash", "approval_hash", "state_hash", "created_at", "signature"],
        ["schema_version", "tx_id", "approved_event_hash", "approval_hash", "state_hash", "created_at", "signature"],
    )
    clean = dict(anchor)
    signature = clean.pop("signature")
    if not hmac.compare_digest(str(signature), keyed_signature(str(state.get("tx_id")), "approval_anchor", clean, approval_key)):
        raise HygieneError("approval anchor signature mismatch")
    if tx_hash({"tx_id": state.get("tx_id"), "artifact_type": "approval", "provenance_key": approval_key}) != state.get(
        "provenance_key_hashes", {}
    ).get("approval"):
        raise HygieneError("approval anchor key does not match transaction")
    event = event_by_hash(tx_dir, str(anchor["approved_event_hash"]))
    if event.get("approval_hash") != state.get("approval_hash"):
        raise HygieneError("approved event approval hash does not match current state")
    if event.get("state_hash") != tx_hash(state):
        raise HygieneError("approved event state hash does not match current state")
    if anchor.get("approval_hash") != state.get("approval_hash") or anchor.get("state_hash") != event.get("state_hash"):
        raise HygieneError("approval anchor drifted")
    return anchor


def nonterminal_transaction_states(config: Dict[str, Any]) -> List[str]:
    return list(
        config.get("closeout", {}).get(
            "nonterminal_states",
            [
                "awaiting_codex_review",
                "codex_reviewing",
                "reviewed",
                "awaiting_user_approval",
                "approved",
                "applying",
                "blocked",
                "parked",
            ],
        )
    )


def open_transactions(repo_root: Path, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    root = transactions_root(repo_root, config)
    if not root.exists():
        return []
    active_states = set(nonterminal_transaction_states(config))
    transactions: List[Dict[str, Any]] = []
    for state_file in sorted(root.glob("tx-*/state.json")):
        state = read_json(state_file, {})
        if state.get("state") in active_states:
            transactions.append(
                {
                    "tx_id": state.get("tx_id"),
                    "state": state.get("state"),
                    "state_version": state.get("state_version"),
                    "updated_at": state.get("updated_at"),
                    "tx_dir": str(state_file.parent),
                }
            )
    return transactions


def transition_state(
    tx_dir: Path,
    *,
    expected_version: int,
    allowed_from: Sequence[str],
    new_state: str,
    updates: Optional[Dict[str, Any]] = None,
    event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = load_state(tx_dir)
    if int(state.get("state_version", -1)) != expected_version:
        raise HygieneError("closeout transaction state version changed")
    if state.get("state") not in set(allowed_from):
        raise HygieneError("closeout transaction state %s cannot transition to %s" % (state.get("state"), new_state))
    if new_state not in TX_STATES:
        raise HygieneError("unknown closeout transaction state %s" % new_state)
    state = dict(state)
    state.update(updates or {})
    state["state"] = new_state
    state["state_version"] = expected_version + 1
    state["updated_at"] = utc_now()
    write_state(tx_dir, state)
    record_event(
        tx_dir,
        {
            "tx_id": state["tx_id"],
            "from_state": allowed_from,
            "to_state": new_state,
            "state_version": state["state_version"],
            **(event or {}),
        },
    )
    return state


def disallow_executable_recommendation(value: Any, path: str = "") -> List[str]:
    failures: List[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_str = str(key).lower()
            nested_path = f"{path}.{key}" if path else str(key)
            if key_str in FORBIDDEN_RECOMMENDATION_KEYS:
                failures.append(nested_path)
            failures.extend(disallow_executable_recommendation(nested, nested_path))
    elif isinstance(value, list):
        for idx, nested in enumerate(value):
            failures.extend(disallow_executable_recommendation(nested, f"{path}[{idx}]"))
    return failures


def head_blob(repo_root: Path, path: str) -> Optional[str]:
    result = run_git(repo_root, ["rev-parse", "--verify", f"HEAD:{path}"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def file_worktree_hash(repo_root: Path, path: str) -> Optional[str]:
    full = repo_root / path
    if not full.exists() or not full.is_file():
        return None
    return hashlib.sha256(full.read_bytes()).hexdigest()[:32]


def commit_units_from_plan(repo_root: Path, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    for candidate in plan.get("candidates", []):
        if candidate.get("kind") != "dirty-group":
            continue
        paths: List[str] = []
        for evidence in candidate.get("evidence", {}).get("path_evidence", []):
            path = normalize_rel(str(evidence.get("path") or ""))
            if path:
                paths.append(path)
        paths = sorted(set(paths))
        preimages = {
            path: {
                "head_blob": head_blob(repo_root, path),
                "worktree_hash": file_worktree_hash(repo_root, path),
            }
            for path in paths
        }
        key = {
            "source_candidate_id": candidate["id"],
            "paths": paths,
            "evidence_hash": candidate["evidence_hash"],
        }
        units.append(
            {
                "id": stable_id("commit-unit", key),
                "source_candidate_id": candidate["id"],
                "recommended_disposition": candidate.get("recommendation"),
                "confidence": candidate.get("confidence"),
                "paths": paths,
                "preimage_hashes": preimages,
                "patch_hash": tx_hash(preimages),
                "requires_hunk_approval": False,
                "evidence_hash": candidate["evidence_hash"],
                "status": "proposed",
            }
        )
    return units


def changed_paths_since_packet(repo_root: Path, commit_units: Iterable[Dict[str, Any]]) -> List[str]:
    changed: List[str] = []
    for unit in commit_units:
        for path, hashes in unit.get("preimage_hashes", {}).items():
            if file_worktree_hash(repo_root, path) != hashes.get("worktree_hash"):
                changed.append(path)
    return sorted(set(changed))


def parse_ahead_behind(value: str) -> Dict[str, int]:
    result = {"ahead": 0, "behind": 0}
    for part in str(value or "").split():
        if part.startswith("+"):
            result["ahead"] = int(part[1:] or "0")
        elif part.startswith("-"):
            result["behind"] = int(part[1:] or "0")
    return result


def branch_name_from_facts(facts: Dict[str, Any]) -> Optional[str]:
    branch = facts.get("status", {}).get("branch", {}).get("head")
    if not branch or branch == "(detached)":
        return None
    return str(branch)


def is_protected_branch(config: Dict[str, Any], branch: Optional[str]) -> bool:
    if not branch:
        return False
    patterns = config.get("integration_base", {}).get("protected_branch_patterns", [])
    return path_matches(branch, patterns)


def validate_publish_policy(
    config: Dict[str, Any],
    facts: Dict[str, Any],
    *,
    publish_mode: str,
    publish_remote: Optional[str],
) -> None:
    closeout = config.get("closeout", {})
    allowed_modes = closeout.get("publish_modes", PUBLISH_MODES)
    if publish_mode not in allowed_modes:
        raise HygieneError("publish_mode must be one of %s" % ", ".join(allowed_modes))
    if publish_mode == "no_publish":
        if publish_remote:
            raise HygieneError("publish_remote is not allowed when publish_mode is no_publish")
        return

    branch = branch_name_from_facts(facts)
    if not branch:
        raise HygieneError("publish modes require a named current branch")
    if is_protected_branch(config, branch):
        raise HygieneError("publish modes cannot run from protected branch %s" % branch)
    allowed_remotes = closeout.get("allowed_publish_remotes", [])
    if publish_mode in {"pr_only", "direct_push_branch"}:
        if not publish_remote:
            raise HygieneError("publish_remote is required for %s" % publish_mode)
        if publish_remote not in allowed_remotes:
            raise HygieneError("publish_remote %s is not allowed by closeout policy" % publish_remote)
    if publish_mode == "direct_push_branch":
        patterns = closeout.get("direct_push_branch_patterns", [])
        if not path_matches(branch, patterns):
            raise HygieneError("direct push is only allowed from configured branch patterns")
    freshness_required = bool(config.get("integration_base", {}).get("remote_freshness_required_for_apply", True))
    freshness = facts.get("integration_base", {}).get("freshness")
    if freshness_required and freshness == "unknown":
        raise HygieneError("integration base freshness is unknown for publish mode %s" % publish_mode)


def closeout_trigger_signals(facts: Dict[str, Any], plan: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    branch = branch_name_from_facts(facts)
    status = facts.get("status", {})
    dirty_entries = status.get("entries", [])
    dirty_candidates = [c for c in plan.get("candidates", []) if c.get("kind") == "dirty-group"]
    actionable_dirty = [
        c
        for c in dirty_candidates
        if c.get("recommendation") in {"commit", "split", "stash", "ask"} and c.get("recommendation") != "ignore/generated"
    ]
    generated_only = bool(dirty_candidates) and all(c.get("recommendation") == "ignore/generated" for c in dirty_candidates)
    if actionable_dirty:
        signals.append(
            {
                "id": "dirty_current_work",
                "score": 1.0,
                "reason": "dirty files have a commit/split/stash/ask recommendation",
                "candidate_ids": [c["id"] for c in actionable_dirty],
            }
        )
    if generated_only:
        signals.append(
            {
                "id": "dirty_generated_only",
                "score": 0.4,
                "reason": "dirty files look generated and can be retained or ignored intentionally",
                "candidate_ids": [c["id"] for c in dirty_candidates],
            }
        )
    ab = parse_ahead_behind(status.get("branch", {}).get("ahead_behind", ""))
    base_commit = facts.get("integration_base", {}).get("commit")
    head = facts.get("head")
    clean_feature_branch = (
        not dirty_entries
        and branch is not None
        and not is_protected_branch(config, branch)
        and head
        and base_commit
        and head != base_commit
        and (ab["ahead"] > 0 or not status.get("branch", {}).get("upstream"))
    )
    if clean_feature_branch:
        signals.append(
            {
                "id": "clean_feature_branch_ready_to_publish",
                "score": 1.0,
                "reason": "current feature branch is clean and appears to contain publishable commits",
                "branch": branch,
                "ahead_behind": ab,
            }
        )
    hygiene_recommendations = [
        c
        for c in plan.get("candidates", [])
        if c.get("kind") != "dirty-group" and c.get("decision") in {"auto_apply", "recommend"}
    ]
    if hygiene_recommendations:
        signals.append(
            {
                "id": "hygiene_cleanup_recommendations",
                "score": 0.6,
                "reason": "repo hygiene scan found generated-state or explicit-review cleanup candidates",
                "candidate_ids": [c["id"] for c in hygiene_recommendations],
            }
        )
    return signals


def evaluate_closeout_triggers(
    repo_root_arg: Path,
    *,
    open_if_triggered: bool = False,
    publish_mode: Optional[str] = None,
    publish_remote: Optional[str] = None,
) -> Dict[str, Any]:
    active_root = repo_root_arg.resolve()
    repo_root = resolve_repo_root(active_root)
    config = load_config(repo_root)
    closeout = config.get("closeout", {})
    trigger_config = closeout.get("auto_trigger", {})
    built = build_plan(repo_root, active_root, config, trust_local_base=False)
    facts = built["facts"]
    plan = built["plan"]
    signals = closeout_trigger_signals(facts, plan, config)
    enabled = bool(trigger_config.get("enabled", True))
    minimum_score = float(trigger_config.get("minimum_score", 1.0))
    configured_signals = set(trigger_config.get("signals", TRIGGER_SIGNAL_IDS))
    accepted_signals = [signal for signal in signals if signal["id"] in configured_signals]
    score = sum(float(signal.get("score", 0.0)) for signal in accepted_signals)
    active_transactions = open_transactions(repo_root, config)
    triggered = enabled and score >= minimum_score and not active_transactions
    opened: Optional[Dict[str, Any]] = None
    if open_if_triggered and triggered:
        opened = open_transaction(
            active_root,
            publish_mode=publish_mode or str(closeout.get("default_publish_mode", "no_publish")),
            publish_remote=publish_remote,
        )
    result = {
        "schema_version": CLOSEOUT_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "policy_hash": config["policy_hash"],
        "evaluated_at": utc_now(),
        "repo_root": str(repo_root),
        "active_root": str(active_root),
        "enabled": enabled,
        "triggered": triggered,
        "score": score,
        "minimum_score": minimum_score,
        "signals": accepted_signals,
        "ignored_signals": [signal for signal in signals if signal["id"] not in configured_signals],
        "active_transactions": active_transactions,
        "opened_transaction": opened,
        "message": "closeout transaction opened" if opened else "closeout trigger evaluated",
    }
    trigger_dir = state_root(repo_root, config) / "triggers"
    persisted = json.loads(json.dumps(result))
    if persisted.get("opened_transaction"):
        persisted["opened_transaction"].pop("trusted_approval_nonce", None)
        persisted["opened_transaction"].pop("trusted_provenance_keys", None)
    write_json(trigger_dir / "latest.json", persisted)
    return result


def open_transaction(
    repo_root_arg: Path,
    *,
    base: Optional[str] = None,
    publish_mode: str = "no_publish",
    publish_remote: Optional[str] = None,
) -> Dict[str, Any]:
    active_root = repo_root_arg.resolve()
    repo_root = resolve_repo_root(active_root)
    config = load_config(repo_root)

    built = build_plan(repo_root, active_root, config, trust_local_base=False)
    facts = built["facts"]
    plan = built["plan"]
    validate_publish_policy(config, facts, publish_mode=publish_mode, publish_remote=publish_remote)
    commit_units = commit_units_from_plan(repo_root, plan)
    tx_id = "tx-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    tx_dir = transaction_dir(repo_root, config, tx_id)
    tx_dir.mkdir(parents=True, exist_ok=False)
    approval_nonce = secrets.token_hex(12)
    provenance_keys = {
        "codex_recommendation": secrets.token_hex(24),
        "agent_review": secrets.token_hex(24),
        "approval": secrets.token_hex(24),
    }
    decision_packet = {
        "schema_version": CLOSEOUT_SCHEMA_VERSION,
        "tx_id": tx_id,
        "invocation_id": str(uuid.uuid4()),
        "created_at": utc_now(),
        "policy_version": POLICY_VERSION,
        "policy_hash": config["policy_hash"],
        "config_hash": config["policy_hash"],
        "repo_root": str(repo_root),
        "active_root": str(active_root),
        "head": facts.get("head"),
        "branch": facts.get("status", {}).get("branch"),
        "status": facts.get("status"),
        "remotes": run_git(repo_root, ["remote", "-v"]).stdout,
        "integration_base": base or facts.get("integration_base", {}).get("name"),
        "integration_base_facts": facts.get("integration_base"),
        "publish_mode": publish_mode,
        "publish_remote": publish_remote,
        "transient_git_state": facts.get("transient_git_state"),
        "dirty_commit_units": commit_units,
        "hygiene_candidates": plan.get("candidates", []),
        "worktrees": facts.get("worktrees", []),
        "stashes": facts.get("stashes", []),
        "questions_for_codex": [
            "Which commit units should be committed, split, stashed, ignored, or retained?",
            "Are unrelated dirty files excluded from the transaction?",
            "What tests/proofs are required before publish or merge?",
            "Which publish mode and target are appropriate?",
            "Which branch/worktree/stash/orphan cleanup actions are safe after publish proof?",
            "What residual risks require user approval?",
        ],
    }
    packet_hash = tx_hash(decision_packet)
    state = {
        "schema_version": CLOSEOUT_SCHEMA_VERSION,
        "tx_id": tx_id,
        "state": "awaiting_codex_review",
        "state_version": 1,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "decision_packet_hash": packet_hash,
        "recommendation_hash": None,
        "approval_hash": None,
        "approval_nonce_hash": tx_hash({"tx_id": tx_id, "approval_nonce": approval_nonce}),
        "provenance_key_hashes": {
            artifact_type: tx_hash({"tx_id": tx_id, "artifact_type": artifact_type, "provenance_key": key})
            for artifact_type, key in provenance_keys.items()
        },
        "publish_mode": publish_mode,
        "policy_hash": config["policy_hash"],
    }
    write_json(tx_dir / "decision-packet.json", decision_packet)
    write_json(tx_dir / "state.json", state)
    write_json(
        tx_dir / "trusted-approval-nonce.public.json",
        {
            "schema_version": CLOSEOUT_SCHEMA_VERSION,
            "tx_id": tx_id,
            "nonce_hash": state["approval_nonce_hash"],
            "created_at": utc_now(),
            "usage": "The plaintext nonce is returned once to the trusted caller and is not stored in transaction artifacts.",
        },
    )
    write_json(
        tx_dir / "trusted-provenance-key.public.json",
        {
            "schema_version": CLOSEOUT_SCHEMA_VERSION,
            "tx_id": tx_id,
            "key_hashes": state["provenance_key_hashes"],
            "created_at": utc_now(),
            "usage": "Role-specific plaintext provenance keys are returned once to the trusted caller and are not stored in transaction artifacts.",
        },
    )
    record_event(
        tx_dir,
        {
            "tx_id": tx_id,
            "event": "opened",
            "to_state": "awaiting_codex_review",
            "decision_packet_hash": packet_hash,
        },
    )
    return {
        "tx_id": tx_id,
        "tx_dir": str(tx_dir),
        "state": state,
        "decision_packet_hash": packet_hash,
        "trusted_approval_nonce": approval_nonce,
        "trusted_provenance_keys": provenance_keys,
    }


def load_transaction(repo_root_arg: Path, tx_id: str) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg.resolve())
    config = load_config(repo_root)
    tx_dir = transaction_dir(repo_root, config, tx_id)
    state = load_state(tx_dir)
    packet = read_json(tx_dir / "decision-packet.json", {})
    return {"repo_root": repo_root, "config": config, "tx_dir": tx_dir, "state": state, "decision_packet": packet}


def iter_symbolic_actions(recommendation: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for key in ["actions", "cleanup_actions"]:
        for action in recommendation.get(key, []):
            if isinstance(action, dict):
                yield action


def validate_symbolic_actions(recommendation: Dict[str, Any], packet: Dict[str, Any]) -> None:
    forbidden = disallow_executable_recommendation(recommendation)
    if forbidden:
        raise HygieneError("closeout recommendation contains executable command-like keys: %s" % ", ".join(forbidden))
    commit_unit_ids = {unit.get("id") for unit in packet.get("dirty_commit_units", [])}
    candidates = {candidate.get("id"): candidate for candidate in packet.get("hygiene_candidates", [])}
    publish_mode = packet.get("publish_mode")
    action_allowed_keys = {
        "commit_unit_commit": {"action_id", "commit_unit_id", "rationale"},
        "publish_pr": {"action_id", "rationale"},
        "publish_direct_branch": {"action_id", "rationale"},
        "local_merge": {"action_id", "rationale"},
        "prune_after_publish": {"action_id", "candidate_id", "rationale"},
        "retain": {"action_id", "candidate_id", "commit_unit_id", "rationale"},
        "ask": {"action_id", "candidate_id", "commit_unit_id", "question", "rationale"},
        "stash_promote": {"action_id", "candidate_id", "rationale"},
        "orphan_quarantine": {"action_id", "candidate_id", "rationale"},
        "branch_archive_delete": {"action_id", "candidate_id", "rationale"},
        "worktree_remove": {"action_id", "candidate_id", "rationale"},
    }
    for action in iter_symbolic_actions(recommendation):
        action_id = action.get("action_id")
        if action_id not in CLOSEOUT_ACTION_IDS and action_id not in HYGIENE_CLEANUP_ACTION_IDS:
            raise HygieneError("unsupported closeout symbolic action: %s" % action_id)
        unknown_action_keys = sorted(set(action) - action_allowed_keys.get(str(action_id), {"action_id"}))
        if unknown_action_keys:
            raise HygieneError("closeout action %s contains unknown key(s): %s" % (action_id, ", ".join(unknown_action_keys)))
        if action.get("path") or action.get("raw_path"):
            raise HygieneError("closeout actions must reference candidate_id or commit_unit_id, not raw paths")
        commit_unit_id = action.get("commit_unit_id")
        candidate_id = action.get("candidate_id")
        if action_id == "commit_unit_commit":
            if not commit_unit_id or commit_unit_id not in commit_unit_ids:
                raise HygieneError("commit_unit_commit requires a valid commit_unit_id")
        if action_id == "publish_pr" and publish_mode != "pr_only":
            raise HygieneError("publish_pr requires a pr_only closeout transaction")
        if action_id == "publish_direct_branch" and publish_mode != "direct_push_branch":
            raise HygieneError("publish_direct_branch requires a direct_push_branch closeout transaction")
        if action_id == "local_merge" and publish_mode != "local_merge_only":
            raise HygieneError("local_merge requires a local_merge_only closeout transaction")
        if action_id == "prune_after_publish" and publish_mode == "no_publish":
            raise HygieneError("prune_after_publish requires a publishing closeout transaction")
        if action_id in {"stash", "split"} and commit_unit_id and commit_unit_id not in commit_unit_ids:
            raise HygieneError("%s references an unknown commit_unit_id" % action_id)
        if action_id in {"stash_promote", "orphan_quarantine", "branch_archive_delete", "worktree_remove"}:
            candidate = candidates.get(candidate_id)
            if not candidate:
                raise HygieneError("%s requires a valid candidate_id" % action_id)
            if action_id not in candidate.get("allowed_actions", []):
                raise HygieneError("%s is not allowed for candidate %s" % (action_id, candidate_id))
        if candidate_id and candidate_id not in candidates:
            raise HygieneError("closeout action references unknown candidate_id %s" % candidate_id)
        if commit_unit_id and commit_unit_id not in commit_unit_ids:
            raise HygieneError("closeout action references unknown commit_unit_id %s" % commit_unit_id)


def validate_recommendation_schema(recommendation: Dict[str, Any]) -> None:
    require_keys(
        "codex recommendation",
        recommendation,
        ["tx_id", "decision_packet_hash", "actions", "provenance"],
        [
            "tx_id",
            "decision_packet_hash",
            "summary",
            "actions",
            "cleanup_actions",
            "residual_risks",
            "test_plan",
            "publish_mode",
            "provenance",
        ],
    )
    if not isinstance(recommendation.get("actions"), list):
        raise HygieneError("codex recommendation actions must be a list")
    if "cleanup_actions" in recommendation and not isinstance(recommendation.get("cleanup_actions"), list):
        raise HygieneError("codex recommendation cleanup_actions must be a list")
    require_type("codex recommendation tx_id", recommendation.get("tx_id"), str)
    require_type("codex recommendation decision_packet_hash", recommendation.get("decision_packet_hash"), str)


def record_codex_recommendation(
    repo_root_arg: Path,
    tx_id: str,
    recommendation: Dict[str, Any],
    *,
    provenance_key: str,
) -> Dict[str, Any]:
    loaded = load_transaction(repo_root_arg, tx_id)
    tx_dir = loaded["tx_dir"]
    with transaction_mutex(tx_dir):
        state = load_state(tx_dir)
        packet = loaded["decision_packet"]
        validate_recommendation_schema(recommendation)
        validate_signed_provenance(state, "codex_recommendation", recommendation, provenance_key=provenance_key)
        if recommendation.get("tx_id") != tx_id:
            raise HygieneError("recommendation tx_id does not match transaction")
        if recommendation.get("decision_packet_hash") != tx_hash(packet):
            raise HygieneError("recommendation does not match current decision packet")
        validate_symbolic_actions(recommendation, packet)
        recommendation = {
            "schema_version": CLOSEOUT_SCHEMA_VERSION,
            "created_at": utc_now(),
            **recommendation,
        }
        recommendation_hash = tx_hash(recommendation)
        recommendation["recommendation_hash"] = recommendation_hash
        write_json(tx_dir / "codex-closeout-recommendation.json", recommendation)
        new_state = transition_state(
            tx_dir,
            expected_version=int(state["state_version"]),
            allowed_from=["awaiting_codex_review", "codex_reviewing", "reviewed", "awaiting_user_approval"],
            new_state="awaiting_user_approval",
            updates={"recommendation_hash": recommendation_hash},
            event={"event": "codex_recommendation_recorded", "recommendation_hash": recommendation_hash},
        )
        return {"tx_id": tx_id, "recommendation_hash": recommendation_hash, "state": new_state}


def validate_review_schema(review: Dict[str, Any]) -> None:
    require_keys(
        "agent review",
        review,
        [
            "tx_id",
            "reviewer_id",
            "recommendation_hash_reviewed",
            "review_source",
            "reviewer_mode",
            "tool_capabilities",
            "write_attempts",
            "score",
            "approve",
            "provenance",
        ],
        [
            "tx_id",
            "reviewer_id",
            "recommendation_hash_reviewed",
            "review_source",
            "reviewer_mode",
            "tool_capabilities",
            "write_attempts",
            "score",
            "approve",
            "rationale",
            "findings",
            "provenance",
        ],
    )
    require_type("agent review tx_id", review.get("tx_id"), str)
    require_type("agent review reviewer_id", review.get("reviewer_id"), str)
    require_type("agent review recommendation_hash_reviewed", review.get("recommendation_hash_reviewed"), str)
    require_type("agent review review_source", review.get("review_source"), str)
    require_type("agent review reviewer_mode", review.get("reviewer_mode"), str)
    require_type("agent review tool_capabilities", review.get("tool_capabilities"), dict)
    require_type("agent review write_attempts", review.get("write_attempts"), int)
    require_type("agent review approve", review.get("approve"), bool)
    score = review.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise HygieneError("agent review score must be a number")
    if float(score) < 0.0 or float(score) > 10.0:
        raise HygieneError("agent review score must be between 0 and 10")
    capabilities = review.get("tool_capabilities", {})
    if "write_tools_enabled" not in capabilities:
        raise HygieneError("agent review tool_capabilities.write_tools_enabled is required")
    require_type("agent review tool_capabilities.write_tools_enabled", capabilities.get("write_tools_enabled"), bool)


def record_agent_review(
    repo_root_arg: Path,
    tx_id: str,
    review: Dict[str, Any],
    *,
    provenance_key: str,
) -> Dict[str, Any]:
    loaded = load_transaction(repo_root_arg, tx_id)
    tx_dir = loaded["tx_dir"]
    with transaction_mutex(tx_dir):
        state = load_state(tx_dir)
        config = loaded["config"]
        validate_review_schema(review)
        validate_signed_provenance(state, "agent_review", review, provenance_key=provenance_key)
        recommendation_hash = state.get("recommendation_hash")
        if review.get("tx_id") != tx_id:
            raise HygieneError("agent review tx_id does not match transaction")
        if review.get("recommendation_hash_reviewed") != recommendation_hash:
            raise HygieneError("agent review does not match current recommendation hash")
        allowed_sources = set(config.get("closeout", {}).get("allowed_review_sources", []))
        if review.get("review_source") not in allowed_sources:
            raise HygieneError("agent review source is not allowed by closeout policy")
        if review.get("reviewer_mode") != "read_only":
            raise HygieneError("agent review must prove read_only reviewer_mode")
        capabilities = review.get("tool_capabilities", {})
        if capabilities.get("write_tools_enabled"):
            raise HygieneError("agent review used write-capable tools")
        if int(review.get("write_attempts", 0)) != 0:
            raise HygieneError("agent review attempted writes")
        review = {
            "schema_version": CLOSEOUT_SCHEMA_VERSION,
            "created_at": utc_now(),
            **review,
        }
        reviewer_id = str(review.get("reviewer_id") or uuid.uuid4())
        review_hash = tx_hash(review)
        review["review_hash"] = review_hash
        write_json(tx_dir / f"agent-review-{artifact_token(reviewer_id)}.json", review)
        record_event(
            tx_dir,
            {
                "tx_id": tx_id,
                "event": "agent_review_recorded",
                "reviewer_id": reviewer_id,
                "review_hash": review_hash,
                "score": review.get("score"),
                "approve": review.get("approve"),
            },
        )
        return {"tx_id": tx_id, "reviewer_id": reviewer_id, "review_hash": review_hash}


def review_files(tx_dir: Path) -> List[Dict[str, Any]]:
    return [read_json(path, {}) for path in sorted(tx_dir.glob("agent-review-*.json"))]


def recorded_review_hashes(tx_dir: Path) -> set[str]:
    hashes: set[str] = set()
    events_path = tx_dir / "events.jsonl"
    if not events_path.exists():
        return hashes
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event") == "agent_review_recorded" and row.get("review_hash"):
            hashes.add(str(row["review_hash"]))
    return hashes


def validate_persisted_recommendation(
    state: Dict[str, Any],
    recommendation: Dict[str, Any],
    *,
    provenance_key: str,
) -> None:
    if stored_artifact_hash(recommendation, "recommendation_hash") != recommendation.get("recommendation_hash"):
        raise HygieneError("codex recommendation hash drifted")
    clean = dict(recommendation)
    clean.pop("schema_version", None)
    clean.pop("created_at", None)
    clean.pop("recommendation_hash", None)
    validate_recommendation_schema(clean)
    validate_signed_provenance(state, "codex_recommendation", clean, provenance_key=provenance_key)


def validate_persisted_review(
    state: Dict[str, Any],
    review: Dict[str, Any],
    *,
    provenance_key: str,
) -> None:
    if stored_artifact_hash(review, "review_hash") != review.get("review_hash"):
        raise HygieneError("agent review hash drifted")
    clean = dict(review)
    clean.pop("schema_version", None)
    clean.pop("created_at", None)
    clean.pop("review_hash", None)
    validate_review_schema(clean)
    validate_signed_provenance(state, "agent_review", clean, provenance_key=provenance_key)


def validate_reviews_or_waiver(
    tx_dir: Path,
    state: Dict[str, Any],
    approval: Dict[str, Any],
    recommendation_hash: str,
    config: Dict[str, Any],
    *,
    review_provenance_key: str,
) -> List[str]:
    all_reviews = review_files(tx_dir)
    event_hashes = recorded_review_hashes(tx_dir)
    reviews: List[Dict[str, Any]] = []
    for review in all_reviews:
        if review.get("recommendation_hash_reviewed") != recommendation_hash:
            continue
        validate_persisted_review(state, review, provenance_key=review_provenance_key)
        if review.get("review_hash") not in event_hashes:
            raise HygieneError("agent review was not recorded in the transaction event log")
        reviews.append(review)
    waiver = approval.get("review_waiver")
    closeout = config.get("closeout", {})
    allow_waiver = bool(closeout.get("allow_review_waiver", False))
    if waiver and not allow_waiver:
        raise HygieneError("review waivers are disabled by closeout policy")
    if waiver and not waiver.get("risk_acceptance"):
        raise HygieneError("review waiver must include risk_acceptance")
    required_reviews = int(closeout.get("required_read_only_reviewers", 2))
    reviewer_ids = {str(review.get("reviewer_id")) for review in reviews if review.get("reviewer_id")}
    if len(reviews) < required_reviews or len(reviewer_ids) < required_reviews:
        if not waiver:
            raise HygieneError(
                "at least %s read-only agent reviews are required or an explicit waiver must be approved"
                % required_reviews
            )
    blockers = []
    for review in reviews:
        try:
            score = float(review.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        if review.get("approve") is False or score < 8.0:
            blockers.append(review)
    if blockers and not waiver:
        raise HygieneError("material agent review disagreement blocks approval")
    return sorted(str(review["review_hash"]) for review in reviews)


def current_review_hash_integrity(tx_dir: Path) -> set[str]:
    hashes: set[str] = set()
    for review in review_files(tx_dir):
        review_hash = review.get("review_hash")
        if stored_artifact_hash(review, "review_hash") != review_hash:
            raise HygieneError("agent review hash drifted after approval")
        hashes.add(str(review_hash))
    return hashes


def status_fingerprint(status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "branch": status.get("branch", {}),
        "entries": sorted_records(
            {
                "status": str(entry.get("status", "")),
                "path": normalize_rel(str(entry.get("path", ""))),
            }
            for entry in status.get("entries", [])
        ),
    }


def sorted_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        records,
        key=lambda item: canonical_json(item),
    )


def integration_base_fingerprint(base: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": base.get("status"),
        "name": base.get("name"),
        "commit": base.get("commit"),
        "freshness": base.get("freshness"),
    }


def worktree_fingerprint(worktrees: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted_records(
        {
            "path": str(item.get("path", "")),
            "head": str(item.get("head", "")),
            "branch": str(item.get("branch", "")),
            "locked": bool(item.get("locked")),
            "detached": bool(item.get("detached")),
        }
        for item in worktrees
    )


def stash_fingerprint(stashes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted_records(
        {
            "name": str(item.get("name", "")),
            "sha": str(item.get("sha", "")),
            "message": str(item.get("message", "")),
        }
        for item in stashes
    )


def requested_candidate_ids(recommendation: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for action in iter_symbolic_actions(recommendation):
        candidate_id = action.get("candidate_id")
        if candidate_id:
            ids.append(str(candidate_id))
    return sorted(set(ids))


def validate_candidate_evidence_unchanged(
    original_candidates: Sequence[Dict[str, Any]],
    current_candidates: Sequence[Dict[str, Any]],
    candidate_ids: Sequence[str],
) -> None:
    original = {item.get("id"): item for item in original_candidates}
    current = {item.get("id"): item for item in current_candidates}
    for candidate_id in candidate_ids:
        if candidate_id not in original:
            raise HygieneError("recommended candidate was not in the decision packet: %s" % candidate_id)
        if candidate_id not in current:
            raise HygieneError("recommended candidate disappeared before apply: %s" % candidate_id)
        if current[candidate_id].get("evidence_hash") != original[candidate_id].get("evidence_hash"):
            raise HygieneError("candidate evidence changed before apply: %s" % candidate_id)


def validate_approval_schema(approval: Dict[str, Any]) -> None:
    require_keys(
        "approval",
        approval,
        ["tx_id", "recommendation_hash", "approval_source", "approved_action_ids", "provenance"],
        [
            "tx_id",
            "recommendation_hash",
            "approval_source",
            "approved_action_ids",
            "approved_commit_unit_ids",
            "approved_candidate_ids",
            "risk_acceptance",
            "review_waiver",
            "provenance",
        ],
    )
    require_type("approval tx_id", approval.get("tx_id"), str)
    require_type("approval recommendation_hash", approval.get("recommendation_hash"), str)
    require_type("approval approval_source", approval.get("approval_source"), str)
    require_type("approval approved_action_ids", approval.get("approved_action_ids"), list)
    if "approved_commit_unit_ids" in approval:
        require_type("approval approved_commit_unit_ids", approval.get("approved_commit_unit_ids"), list)
    if "approved_candidate_ids" in approval:
        require_type("approval approved_candidate_ids", approval.get("approved_candidate_ids"), list)


def validate_compare_result_schema(compare_result: Dict[str, Any]) -> None:
    require_keys(
        "closeout compare result",
        compare_result,
        [
            "artifactType",
            "schemaVersion",
            "schema",
            "status",
            "generatedAt",
            "freshnessMarkerOrTimestamp",
            "snapshotPointer",
            "reportEnvelope",
            "compareFindings",
        ],
        [
            "artifactType",
            "schemaVersion",
            "schema",
            "status",
            "generatedAt",
            "freshnessMarkerOrTimestamp",
            "snapshotPointer",
            "reportEnvelope",
            "compareFindings",
            "blockerReason",
        ],
    )
    require_type("closeout compare result artifactType", compare_result.get("artifactType"), str)
    if compare_result.get("artifactType") != "closeout-compare-result.v1":
        raise HygieneError("closeout compare result artifactType must be closeout-compare-result.v1")
    require_type("closeout compare result schemaVersion", compare_result.get("schemaVersion"), int)
    if compare_result.get("schemaVersion") != 1:
        raise HygieneError("closeout compare result schemaVersion must be 1")
    require_type("closeout compare result schema", compare_result.get("schema"), str)
    if compare_result.get("schema") != "closeout-compare-result.v1":
        raise HygieneError("closeout compare result schema must be closeout-compare-result.v1")
    if compare_result.get("schema") != compare_result.get("artifactType"):
        raise HygieneError("closeout compare result schema and artifactType must match")
    if compare_result.get("status") not in {"current", "stale", "divergent", "blocked"}:
        raise HygieneError("closeout compare result status must be current, stale, divergent, or blocked")
    require_type("closeout compare result generatedAt", compare_result.get("generatedAt"), str)
    require_type("closeout compare result freshnessMarkerOrTimestamp", compare_result.get("freshnessMarkerOrTimestamp"), str)
    snapshot_pointer = compare_result.get("snapshotPointer")
    if not isinstance(snapshot_pointer, dict):
        raise HygieneError("closeout compare result snapshotPointer must be a dict")
    require_keys(
        "closeout compare result snapshotPointer",
        snapshot_pointer,
        ["schema", "path", "hash"],
        ["schema", "path", "hash", "auditHash", "workBlockId"],
    )
    require_type("closeout compare result snapshotPointer.schema", snapshot_pointer.get("schema"), str)
    if snapshot_pointer.get("schema") != "repo-state-snapshot.v1":
        raise HygieneError("closeout compare result snapshotPointer.schema must be repo-state-snapshot.v1")
    require_type("closeout compare result snapshotPointer.path", snapshot_pointer.get("path"), str)
    require_type("closeout compare result snapshotPointer.hash", snapshot_pointer.get("hash"), str)
    if "auditHash" in snapshot_pointer:
        require_type("closeout compare result snapshotPointer.auditHash", snapshot_pointer.get("auditHash"), str)
    if "workBlockId" in snapshot_pointer:
        require_type("closeout compare result snapshotPointer.workBlockId", snapshot_pointer.get("workBlockId"), str)
    report_envelope = compare_result.get("reportEnvelope")
    if not isinstance(report_envelope, dict):
        raise HygieneError("closeout compare result reportEnvelope must be a dict")
    require_keys(
        "closeout compare result reportEnvelope",
        report_envelope,
        ["objective", "lastCompletedWork", "nextSteps", "blockers", "freshnessMarkerOrTimestamp", "compareFindings"],
        ["objective", "lastCompletedWork", "nextSteps", "blockers", "freshnessMarkerOrTimestamp", "compareFindings"],
    )
    require_type("closeout compare result reportEnvelope.objective", report_envelope.get("objective"), str)
    require_type("closeout compare result reportEnvelope.lastCompletedWork", report_envelope.get("lastCompletedWork"), str)
    require_type("closeout compare result reportEnvelope.nextSteps", report_envelope.get("nextSteps"), list)
    require_type("closeout compare result reportEnvelope.blockers", report_envelope.get("blockers"), list)
    require_type(
        "closeout compare result reportEnvelope.freshnessMarkerOrTimestamp",
        report_envelope.get("freshnessMarkerOrTimestamp"),
        str,
    )
    compare_findings = compare_result.get("compareFindings")
    if not isinstance(compare_findings, list) or not compare_findings:
        raise HygieneError("closeout compare result compareFindings must be a non-empty list")
    if report_envelope.get("compareFindings") != compare_findings:
        raise HygieneError("closeout compare result reportEnvelope.compareFindings must match compareFindings")
    for idx, finding in enumerate(compare_findings):
        if not isinstance(finding, dict):
            raise HygieneError("closeout compare result compareFindings[%s] must be a dict" % idx)
        require_keys(
            "closeout compare result compareFindings[%s]" % idx,
            finding,
            ["heading", "status", "summary"],
            ["heading", "status", "summary", "evidencePaths", "blockerReason"],
        )
        require_type("closeout compare result compareFindings[%s].heading" % idx, finding.get("heading"), str)
        require_type("closeout compare result compareFindings[%s].status" % idx, finding.get("status"), str)
        if finding.get("status") not in {"current", "stale", "divergent", "blocked"}:
            raise HygieneError("closeout compare result compareFindings[%s].status must be current, stale, divergent, or blocked" % idx)
        require_type("closeout compare result compareFindings[%s].summary" % idx, finding.get("summary"), str)
        if "evidencePaths" in finding:
            require_type("closeout compare result compareFindings[%s].evidencePaths" % idx, finding.get("evidencePaths"), list)
        if finding.get("status") != "current" and not finding.get("blockerReason"):
            raise HygieneError("closeout compare result compareFindings[%s] requires blockerReason when status is not current" % idx)
    if compare_result.get("status") != "current" and not compare_result.get("blockerReason"):
        raise HygieneError("closeout compare result requires blockerReason when status is not current")
    if compare_result.get("status") == "current" and "blockerReason" in compare_result:
        raise HygieneError("current closeout compare result must not include blockerReason")


def recommendation_reference_sets(recommendation: Dict[str, Any]) -> Dict[str, List[str]]:
    action_ids: List[str] = []
    commit_unit_ids: List[str] = []
    candidate_ids: List[str] = []
    for action in iter_symbolic_actions(recommendation):
        action_ids.append(str(action.get("action_id")))
        if action.get("commit_unit_id"):
            commit_unit_ids.append(str(action.get("commit_unit_id")))
        if action.get("candidate_id"):
            candidate_ids.append(str(action.get("candidate_id")))
    return {
        "action_ids": sorted(action_ids),
        "commit_unit_ids": sorted(set(commit_unit_ids)),
        "candidate_ids": sorted(set(candidate_ids)),
    }


def validate_approval_covers_recommendation(tx_dir: Path, approval: Dict[str, Any]) -> None:
    recommendation = read_json(tx_dir / "codex-closeout-recommendation.json", {})
    refs = recommendation_reference_sets(recommendation)
    approved_action_ids = sorted(str(item) for item in approval.get("approved_action_ids", []))
    approved_commit_unit_ids = sorted(str(item) for item in approval.get("approved_commit_unit_ids", []))
    approved_candidate_ids = sorted(str(item) for item in approval.get("approved_candidate_ids", []))
    if approved_action_ids != refs["action_ids"]:
        raise HygieneError("approval action IDs do not exactly cover current recommendation")
    if approved_commit_unit_ids != refs["commit_unit_ids"]:
        raise HygieneError("approval commit unit IDs do not exactly cover current recommendation")
    if approved_candidate_ids != refs["candidate_ids"]:
        raise HygieneError("approval candidate IDs do not exactly cover current recommendation")


def approve_transaction(
    repo_root_arg: Path,
    tx_id: str,
    approval: Dict[str, Any],
    approval_nonce: str,
    *,
    provenance_key: str,
    recommendation_provenance_key: str,
    review_provenance_key: str,
) -> Dict[str, Any]:
    loaded = load_transaction(repo_root_arg, tx_id)
    tx_dir = loaded["tx_dir"]
    with transaction_mutex(tx_dir):
        state = load_state(tx_dir)
        config = loaded["config"]
        validate_approval_schema(approval)
        validate_signed_provenance(state, "approval", approval, provenance_key=provenance_key)
        if approval.get("tx_id") != tx_id:
            raise HygieneError("approval tx_id does not match transaction")
        recommendation_hash = state.get("recommendation_hash")
        if not recommendation_hash:
            raise HygieneError("cannot approve before Codex recommendation exists")
        if approval.get("recommendation_hash") != recommendation_hash:
            raise HygieneError("approval recommendation hash does not match current recommendation")
        if tx_hash({"tx_id": tx_id, "approval_nonce": approval_nonce}) != state.get("approval_nonce_hash"):
            raise HygieneError("approval nonce does not match trusted transaction nonce")
        trusted_sources = set(config.get("closeout", {}).get("trusted_approval_sources", []))
        if approval.get("approval_source") not in trusted_sources:
            raise HygieneError("approval source is not trusted")
        recommendation = read_json(tx_dir / "codex-closeout-recommendation.json", {})
        validate_persisted_recommendation(state, recommendation, provenance_key=recommendation_provenance_key)
        validate_approval_covers_recommendation(tx_dir, approval)
        verify_event_chain(tx_dir)
        accepted_review_hashes = validate_reviews_or_waiver(
            tx_dir,
            state,
            approval,
            recommendation_hash,
            config,
            review_provenance_key=review_provenance_key,
        )
        approval = {
            "schema_version": CLOSEOUT_SCHEMA_VERSION,
            "created_at": utc_now(),
            "nonce_hash": state.get("approval_nonce_hash"),
            "accepted_review_hashes": accepted_review_hashes,
            **approval,
        }
        approval_hash = tx_hash(approval)
        approval["approval_hash"] = approval_hash
        write_json(tx_dir / "approval.json", approval)
        new_state = transition_state(
            tx_dir,
            expected_version=int(state["state_version"]),
            allowed_from=["awaiting_user_approval", "reviewed"],
            new_state="approved",
            updates={"approval_hash": approval_hash, "accepted_review_hashes": accepted_review_hashes},
            event={"event": "approved", "approval_hash": approval_hash, "approval_source": approval.get("approval_source")},
        )
        write_approval_anchor(tx_dir, new_state, approval_hash, provenance_key)
        return {"tx_id": tx_id, "approval_hash": approval_hash, "state": new_state}


def validate_transaction_apply(repo_root_arg: Path, tx_id: str, *, approval_provenance_key: str) -> Dict[str, Any]:
    loaded = load_transaction(repo_root_arg, tx_id)
    repo_root = loaded["repo_root"]
    config = loaded["config"]
    tx_dir = loaded["tx_dir"]
    state = loaded["state"]
    packet = loaded["decision_packet"]
    recommendation = read_json(tx_dir / "codex-closeout-recommendation.json", {})
    approval = read_json(tx_dir / "approval.json", {})
    with transaction_mutex(tx_dir), apply_mutex(repo_root, config):
        state = load_state(tx_dir)
        if state.get("state") != "approved":
            raise HygieneError("transaction must be approved before apply validation")
        if config.get("policy_hash") != state.get("policy_hash"):
            raise HygieneError("hygiene policy/config changed after transaction opened")
        if tx_hash(packet) != state.get("decision_packet_hash"):
            raise HygieneError("decision packet hash drifted")
        if stored_artifact_hash(recommendation, "recommendation_hash") != state.get("recommendation_hash"):
            raise HygieneError("recommendation hash drifted")
        if stored_artifact_hash(approval, "approval_hash") != state.get("approval_hash"):
            raise HygieneError("approval hash drifted")
        if approval.get("recommendation_hash") != state.get("recommendation_hash"):
            raise HygieneError("approval is stale for current recommendation")
        event_chain = verify_event_chain(tx_dir)
        approved_anchor = verify_approval_anchor(tx_dir, state, approval_provenance_key)
        accepted_review_hashes = set(str(item) for item in approval.get("accepted_review_hashes", []))
        if accepted_review_hashes != set(str(item) for item in state.get("accepted_review_hashes", [])):
            raise HygieneError("accepted review hash manifest drifted")
        current_review_hashes = current_review_hash_integrity(tx_dir)
        missing_review_hashes = sorted(accepted_review_hashes - current_review_hashes)
        if missing_review_hashes:
            raise HygieneError("accepted review artifact missing or changed after approval: %s" % ", ".join(missing_review_hashes))

        built = build_plan(repo_root, repo_root, config, trust_local_base=False)
        current_facts = built["facts"]
        current_plan = built["plan"]
        if current_facts.get("head") != packet.get("head"):
            raise HygieneError("HEAD changed after decision packet")
        if current_facts.get("status", {}).get("branch") != packet.get("branch"):
            raise HygieneError("current branch changed after decision packet")
        if integration_base_fingerprint(current_facts.get("integration_base", {})) != integration_base_fingerprint(
            packet.get("integration_base_facts", {})
        ):
            raise HygieneError("integration base changed after decision packet")
        if status_fingerprint(current_facts.get("status", {})) != status_fingerprint(packet.get("status", {})):
            raise HygieneError("worktree dirty status changed after decision packet")
        if worktree_fingerprint(current_facts.get("worktrees", [])) != worktree_fingerprint(packet.get("worktrees", [])):
            raise HygieneError("registered worktree state changed after decision packet")
        if stash_fingerprint(current_facts.get("stashes", [])) != stash_fingerprint(packet.get("stashes", [])):
            raise HygieneError("stash state changed after decision packet")
        changed = changed_paths_since_packet(repo_root, packet.get("dirty_commit_units", []))
        if changed:
            raise HygieneError("transaction file evidence changed after decision packet: %s" % ", ".join(changed))
        transient = transient_git_state(repo_root)
        if transient.get("blocked"):
            raise HygieneError("transient git state blocks closeout apply: %s" % ", ".join(transient.get("markers", [])))
        validate_candidate_evidence_unchanged(
            packet.get("hygiene_candidates", []),
            current_plan.get("candidates", []),
            requested_candidate_ids(recommendation),
        )
        publish_mode = packet.get("publish_mode")
        validate_publish_policy(
            config,
            current_facts,
            publish_mode=str(publish_mode),
            publish_remote=packet.get("publish_remote"),
        )
        result = {
            "schema_version": CLOSEOUT_SCHEMA_VERSION,
            "tx_id": tx_id,
            "status": "validated",
            "validated_at": utc_now(),
            "publish_mode": publish_mode,
            "recommendation_hash": state.get("recommendation_hash"),
            "approval_hash": state.get("approval_hash"),
            "preflight_results": {
                "decision_packet_hash": state.get("decision_packet_hash"),
                "policy_hash": state.get("policy_hash"),
                "head": current_facts.get("head"),
                "branch": current_facts.get("status", {}).get("branch"),
                "integration_base": integration_base_fingerprint(current_facts.get("integration_base", {})),
                "candidate_ids_revalidated": requested_candidate_ids(recommendation),
                "event_chain": event_chain,
                "approved_event_hash": approved_anchor.get("approved_event_hash"),
                "accepted_review_hashes": sorted(accepted_review_hashes),
            },
            "message": "transaction is approved and evidence is unchanged; symbolic executor may proceed",
        }
        handoff = {
            "schema_version": CLOSEOUT_SCHEMA_VERSION,
            "tx_id": tx_id,
            "created_at": utc_now(),
            "boundary": "validation_only_not_completion",
            "executor_contract": "consume symbolic actions only; revalidate hashes immediately before each mutation",
            "decision_packet_hash": state.get("decision_packet_hash"),
            "recommendation_hash": state.get("recommendation_hash"),
            "approval_hash": state.get("approval_hash"),
            "allowed_actions": recommendation.get("actions", []) + recommendation.get("cleanup_actions", []),
            "approved_action_ids": approval.get("approved_action_ids", []),
            "forbidden_inputs": ["raw shell", "raw paths", "generated command strings"],
            "required_preflights": result["preflight_results"],
        }
        write_json(tx_dir / "apply-validation.json", result)
        write_json(tx_dir / "executor-handoff.json", handoff)
        record_event(tx_dir, {"tx_id": tx_id, "event": "apply_validated"})
        return result


def closeout_next_steps(tx_dir: Path, state: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
    steps: List[str] = []
    recommendation = read_json(tx_dir / "codex-closeout-recommendation.json", {})
    reviews = review_files(tx_dir)
    approval = read_json(tx_dir / "approval.json", {})
    if not recommendation:
        steps.append("record a signed Codex recommendation for the decision packet")
    else:
        current_reviews = [r for r in reviews if r.get("recommendation_hash_reviewed") == state.get("recommendation_hash")]
        required = int(config.get("closeout", {}).get("required_read_only_reviewers", 2))
        if len({r.get("reviewer_id") for r in current_reviews}) < required:
            steps.append("record two signed read-only stranger reviews for the current recommendation hash")
    if recommendation and reviews and not approval:
        steps.append("record trusted approval with the one-time nonce and signed provenance")
    if state.get("state") == "approved":
        steps.append("run validate-apply to emit apply-validation.json and executor-handoff.json")
    if not steps:
        steps.append("no closeout action is currently pending")
    return steps


def transaction_status(repo_root_arg: Path, tx_id: str, *, explain: bool = False) -> Dict[str, Any]:
    loaded = load_transaction(repo_root_arg, tx_id)
    tx_dir = loaded["tx_dir"]
    result = {
        "tx_id": tx_id,
        "tx_dir": str(tx_dir),
        "state": loaded["state"],
        "has_decision_packet": (tx_dir / "decision-packet.json").exists(),
        "has_codex_recommendation": (tx_dir / "codex-closeout-recommendation.json").exists(),
        "review_count": len(list(tx_dir.glob("agent-review-*.json"))),
        "has_approval": (tx_dir / "approval.json").exists(),
    }
    if explain:
        result["explain"] = {
            "next_steps": closeout_next_steps(tx_dir, loaded["state"], loaded["config"]),
            "current_recommendation_hash": loaded["state"].get("recommendation_hash"),
            "current_review_count": len(
                [
                    review
                    for review in review_files(tx_dir)
                    if review.get("recommendation_hash_reviewed") == loaded["state"].get("recommendation_hash")
                ]
            ),
            "event_chain": verify_event_chain(tx_dir),
            "validation_boundary": "validate-apply emits a handoff; it does not mutate or complete closeout",
        }
    return result
