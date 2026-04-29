import argparse
import copy
import contextlib
import dataclasses
import hashlib
import json
import re
import shutil
import sys
import threading
import time
import uuid
from json import JSONDecodeError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.addressing import AgentInbox, MessageKind, ProjectInbox, SenderContext, SessionInbox
from core.paths import routing_rules_path_for_state_dir, session_registry_path_for_state_dir, watcher_pid_path_for_state_dir
from core.runtime import (
    build_peer_runtime_breadcrumb,
    peer_runtime_path_for_state_dir,
    read_runtime_breadcrumb,
    write_runtime_breadcrumb,
)
from core.routing import resolve_route
from core.settings import load_settings
from project_identity import derive_project_identity
from routing_policy import evaluate_message

AGENTS = {"claude", "codex"}
DEFAULT_SESSION_ID = "default"
DEFAULT_PROJECT = "default"
DEFAULT_MAX_HOPS = 8  # retained as audit-only metadata; no longer enforced
MAX_MESSAGE_BYTES = 64 * 1024
SESSION_RETENTION_DAYS = 30
PENDING_BRIDGE_ACTIONS_SCHEMA_VERSION = 1
WAKE_BREAKER_SCHEMA_VERSION = 1
INBOX_LEVEL_SESSION = "session"
INBOX_LEVEL_PROJECT = "project"
INBOX_LEVEL_AGENT = "agent"
SESSION_BACKPRESSURE_LIMIT = 1
PROJECT_BACKPRESSURE_LIMIT = 5
# Per-pair rate limit: at most RATE_LIMIT_N accepted send_to_peer calls per
# (from_agent, to_agent) pair within a rolling RATE_LIMIT_WINDOW_S window.
# Replaces the old hop-count rejection (agreed via bridge HEURISTIC_SYNC
# 2026-04-27) — runaway loops trip in seconds while normal conversation
# never approaches the limit.
RATE_LIMIT_N = 30
RATE_LIMIT_WINDOW_S = 60
SESSION_REGISTRY_SCHEMA_VERSION = 2
HANDOFF_RE = re.compile(r"\[\[handoff:(claude|codex)(?:\s+([a-z0-9_-]+))?\]\]", re.IGNORECASE)
STOP_RE = re.compile(r"\[\[(done|handoff-to-user|pause-relay)\]\]", re.IGNORECASE)


@dataclasses.dataclass
class BridgeResult:
    ok: bool
    status: str
    message: str
    data: Dict[str, Any] = dataclasses.field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_agent(agent: str) -> str:
    value = (agent or "").strip().lower()
    if value not in AGENTS:
        raise ValueError("agent must be one of: claude, codex")
    return value


def normalize_session(session_id: Optional[str]) -> str:
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("session_id must be a string")
    value = (session_id or DEFAULT_SESSION_ID).strip()
    if not value:
        return DEFAULT_SESSION_ID
    if len(value) > 80:
        raise ValueError("session_id must be 80 characters or fewer")
    if not re.match(r"^[A-Za-z0-9_.:-]+$", value):
        raise ValueError("session_id may only contain letters, numbers, dot, colon, dash, and underscore")
    return value


def normalize_project(project: Optional[str]) -> str:
    value = (project or DEFAULT_PROJECT).strip()
    if not value:
        return DEFAULT_PROJECT
    if len(value) > 80:
        raise ValueError("project must be 80 characters or fewer")
    if not re.match(r"^[A-Za-z0-9_.:-]+$", value):
        raise ValueError("project may only contain letters, numbers, dot, colon, dash, and underscore")
    return value


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_replace(src: Path, dst: Path) -> None:
    """Replace dst with src atomically.

    Path.replace() / os.replace() can fail on Windows with ERROR_ACCESS_DENIED
    when another process holds dst open without FILE_SHARE_DELETE (Python's
    default open() does not request that sharing mode).  shutil.move() falls
    back to a copy-then-delete strategy that works regardless.
    """
    if sys.platform == "win32":
        shutil.move(str(src), str(dst))
    else:
        src.replace(dst)


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _atomic_replace(tmp, path)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    quarantine: List[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    quarantine.append(line)
    if quarantine:
        qpath = path.with_suffix(".quarantine.jsonl")
        with qpath.open("a", encoding="utf-8", newline="\n") as handle:
            for bad in quarantine:
                handle.write(bad)
                handle.write("\n")
    return rows


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True))
        handle.write("\n")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")
    _atomic_replace(tmp, path)


def strip_markers(message: str) -> Dict[str, Any]:
    marker_match = HANDOFF_RE.search(message or "")
    marker_target = marker_match.group(1).lower() if marker_match else None
    marker_variant = marker_match.group(2).lower() if marker_match and marker_match.group(2) else None
    stop_match = STOP_RE.search(message or "")
    stripped = HANDOFF_RE.sub("", message or "")
    stripped = STOP_RE.sub("", stripped)
    stripped = stripped.strip()
    return {
        "marker_target": marker_target,
        "marker_variant": marker_variant,
        "stop": stop_match.group(1).lower() if stop_match else None,
        "body": stripped,
    }


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == 259
    try:
        import os
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


class AgentBridge:
    def __init__(self, state_dir: Path, max_hops: int = DEFAULT_MAX_HOPS) -> None:
        self.state_dir = Path(state_dir)
        self.max_hops = max_hops
        self._lock = threading.Lock()

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def audit_path(self) -> Path:
        return self.state_dir / "messages.jsonl"

    @property
    def pending_actions_path(self) -> Path:
        return self.state_dir / "pending-actions.json"

    @property
    def session_registry_path(self) -> Path:
        return session_registry_path_for_state_dir(self.state_dir)

    @property
    def wake_breaker_path(self) -> Path:
        return self.state_dir / "wake-failure-windows.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / ".lock"

    def inbox_path(self, agent: str) -> Path:
        return self.state_dir / ("inbox-%s.jsonl" % agent)

    def ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _load_settings(self):
        return load_settings(self.state_dir)

    @contextlib.contextmanager
    def _locked(self):
        self.ensure_state_dir()
        with self._lock:
            acquired = False
            start = time.monotonic()
            while not acquired:
                try:
                    self.lock_path.mkdir()
                    acquired = True
                except FileExistsError:
                    try:
                        age = time.time() - self.lock_path.stat().st_mtime
                        if age > 60:
                            self.lock_path.rmdir()
                            continue
                    except OSError:
                        pass
                    if time.monotonic() - start > 10:
                        raise TimeoutError("timed out waiting for bridge state lock")
                    time.sleep(0.05)
            try:
                yield
            finally:
                try:
                    self.lock_path.rmdir()
                except FileNotFoundError:
                    pass

    def _default_state(self) -> Dict[str, Any]:
        return {
            "paused": False,
            "sessions": {},
            "updated_at": utc_now(),
        }

    def _load_state(self) -> Dict[str, Any]:
        state = read_json(self.state_path, self._default_state())
        state.setdefault("paused", False)
        state.setdefault("sessions", {})
        return state

    def _save_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        write_json(self.state_path, state)

    def _session_state(self, state: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        sessions = state.setdefault("sessions", {})
        session = sessions.setdefault(
            session_id,
            {
                "hop_count": 0,
                "seen_hashes": [],
                "created_at": utc_now(),
            },
        )
        session.setdefault("hop_count", 0)
        session.setdefault("seen_hashes", [])
        return session

    def _audit(self, event: Dict[str, Any]) -> None:
        append_jsonl(self.audit_path, event)

    def _default_session_registry(self) -> Dict[str, Any]:
        return {
            "schema_version": SESSION_REGISTRY_SCHEMA_VERSION,
            "projects": {},
            "updated_at": utc_now(),
        }

    def _default_pending_actions(self) -> Dict[str, Any]:
        return {
            "schema_version": PENDING_BRIDGE_ACTIONS_SCHEMA_VERSION,
            "actions": [],
            "updated_at": utc_now(),
        }

    def _load_pending_actions(self) -> Dict[str, Any]:
        try:
            pending = read_json(self.pending_actions_path, self._default_pending_actions())
        except (JSONDecodeError, OSError):
            corrupt_path = self.pending_actions_path.with_name(
                "pending-actions.corrupt.%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            if self.pending_actions_path.exists():
                self.pending_actions_path.replace(corrupt_path)
            pending = self._default_pending_actions()
        pending["schema_version"] = max(
            int(pending.get("schema_version") or 1),
            PENDING_BRIDGE_ACTIONS_SCHEMA_VERSION,
        )
        actions = pending.get("actions")
        if not isinstance(actions, list):
            pending["actions"] = []
        else:
            normalized: List[Dict[str, Any]] = []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action.setdefault("status", "pending")
                action.setdefault("details", None)
                action.setdefault("due_at", None)
                action.setdefault("message_id", None)
                action.setdefault("related_session_id", None)
                action.setdefault("resolved_at", None)
                action.setdefault("resolved_by", None)
                action.setdefault("resolution", None)
                normalized.append(action)
            pending["actions"] = normalized
        return pending

    def _save_pending_actions(self, pending: Dict[str, Any]) -> None:
        pending["updated_at"] = utc_now()
        write_json(self.pending_actions_path, pending)

    def _load_session_registry(self) -> Dict[str, Any]:
        try:
            registry = read_json(self.session_registry_path, self._default_session_registry())
        except (JSONDecodeError, OSError):
            corrupt_path = self.session_registry_path.with_name(
                "session.corrupt.%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            if self.session_registry_path.exists():
                self.session_registry_path.replace(corrupt_path)
            registry = self._default_session_registry()
        registry["schema_version"] = max(int(registry.get("schema_version") or 1), SESSION_REGISTRY_SCHEMA_VERSION)
        registry.setdefault("projects", {})
        for project_entry in registry.get("projects", {}).values():
            if not isinstance(project_entry, dict):
                continue
            project_entry.setdefault("active", {})
            project_entry.setdefault("sessions", {})
            trusted = project_entry.setdefault("trusted_parent", {})
            for agent in sorted(AGENTS):
                trusted.setdefault(agent, {"session_id": None, "promoted_at": None})
            for session_id, record in project_entry.get("sessions", {}).items():
                if not isinstance(record, dict):
                    continue
                record.setdefault("session_id", session_id)
                record.setdefault("bootstrap_origin", "unknown")
                record.setdefault("bootstrap_promoted_to_trusted", False)
                record.setdefault("desktop_thread_id", None)
                record.setdefault("bootstrap_thread_id", None)
                record.setdefault("bootstrap_parent_thread_id", None)
        return registry

    def session_registry_view(self) -> Dict[str, Any]:
        with self._locked():
            return copy.deepcopy(self._load_session_registry())

    def _save_session_registry(self, registry: Dict[str, Any]) -> None:
        self._prune_session_registry(registry)
        registry["updated_at"] = utc_now()
        write_json(self.session_registry_path, registry)

    def _prune_session_registry(self, registry: Dict[str, Any]) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=SESSION_RETENTION_DAYS)
        for project_entry in registry.get("projects", {}).values():
            sessions = project_entry.get("sessions", {})
            stale: List[str] = []
            for session_id, record in sessions.items():
                if record.get("status") not in {"ended", "superseded"}:
                    continue
                stamp = (
                    record.get("ended_at")
                    or record.get("superseded_at")
                    or record.get("last_seen_at")
                    or record.get("created_at")
                )
                if not stamp:
                    continue
                try:
                    dt = datetime.fromisoformat(stamp)
                except ValueError:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    stale.append(session_id)
            for session_id in stale:
                sessions.pop(session_id, None)

    def _project_registry(self, registry: Dict[str, Any], project: str) -> Dict[str, Any]:
        projects = registry.setdefault("projects", {})
        project_entry = projects.setdefault(
            project,
            {
                "active": {},
                "sessions": {},
                "trusted_parent": {
                    "claude": {"session_id": None, "promoted_at": None},
                    "codex": {"session_id": None, "promoted_at": None},
                },
                "updated_at": utc_now(),
            },
        )
        project_entry.setdefault("active", {})
        project_entry.setdefault("sessions", {})
        trusted = project_entry.setdefault("trusted_parent", {})
        for agent in sorted(AGENTS):
            trusted.setdefault(agent, {"session_id": None, "promoted_at": None})
        return project_entry

    def _session_record(self, project_entry: Dict[str, Any], session_id: str, agent: str) -> Dict[str, Any]:
        sessions = project_entry.setdefault("sessions", {})
        record = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "agent": agent,
                "created_at": utc_now(),
                "activated_at": utc_now(),
                "status": "active",
                "bootstrap_origin": "unknown",
                "bootstrap_promoted_to_trusted": False,
                "desktop_thread_id": None,
                "bootstrap_thread_id": None,
                "bootstrap_parent_thread_id": None,
            },
        )
        record.setdefault("agent", agent)
        record.setdefault("session_id", session_id)
        record.setdefault("created_at", utc_now())
        record.setdefault("activated_at", utc_now())
        record.setdefault("bootstrap_origin", "unknown")
        record.setdefault("bootstrap_promoted_to_trusted", False)
        record.setdefault("desktop_thread_id", None)
        record.setdefault("bootstrap_thread_id", None)
        record.setdefault("bootstrap_parent_thread_id", None)
        return record

    def record_session_runtime_metadata(
        self,
        *,
        agent: str,
        session_id: str,
        project: str,
        desktop_thread_id: Optional[str] = None,
        bootstrap_thread_id: Optional[str] = None,
        bootstrap_parent_thread_id: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(agent)
                session = normalize_session(session_id)
                project_name = normalize_project(project)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            registry = self._load_session_registry()
            project_entry = self._project_registry(registry, project_name)
            record = self._session_record(project_entry, session, owner)
            record["last_seen_at"] = utc_now()
            record["desktop_thread_id"] = (desktop_thread_id or "").strip() or None
            record["bootstrap_thread_id"] = (bootstrap_thread_id or "").strip() or None
            record["bootstrap_parent_thread_id"] = (bootstrap_parent_thread_id or "").strip() or None
            self._save_session_registry(registry)
            return BridgeResult(
                True,
                "recorded",
                "Recorded runtime metadata for %s session %s." % (owner, session),
                {"project": project_name, "session_id": session},
            )

    def _trusted_parent_info(self, project_entry: Dict[str, Any], agent: str) -> Dict[str, Any]:
        trusted = project_entry.setdefault("trusted_parent", {})
        return trusted.setdefault(agent, {"session_id": None, "promoted_at": None})

    def _find_session_record(
        self,
        registry: Dict[str, Any],
        session_id: str,
        agent: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        for project_name, project_entry in registry.get("projects", {}).items():
            record = project_entry.get("sessions", {}).get(session_id)
            if not record:
                continue
            if agent and record.get("agent") != agent:
                continue
            active_session = project_entry.get("active", {}).get(record.get("agent"))
            return {
                "project": project_name,
                "record": record,
                "active_session": active_session,
            }
        return None

    def _bucket_info(self, registry: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        if session_id in AGENTS:
            return {
                "bucket": session_id,
                "inbox_level": INBOX_LEVEL_AGENT,
                "parent_project": None,
                "project": None,
                "record": None,
                "active_session": None,
            }

        projects = registry.get("projects", {}) or {}
        if session_id in projects:
            return {
                "bucket": session_id,
                "inbox_level": INBOX_LEVEL_PROJECT,
                "parent_project": session_id,
                "project": session_id,
                "record": None,
                "active_session": None,
            }

        found = self._find_session_record(registry, session_id)
        if found:
            return {
                "bucket": session_id,
                "inbox_level": INBOX_LEVEL_SESSION,
                "parent_project": found["project"],
                "project": found["project"],
                "record": found["record"],
                "active_session": found["active_session"],
            }

        return {
            "bucket": session_id,
            "inbox_level": INBOX_LEVEL_SESSION,
            "parent_project": None,
            "project": None,
            "record": None,
            "active_session": None,
        }

    def _parent_buckets_for(self, registry: Dict[str, Any], session_id: str) -> List[str]:
        info = self._bucket_info(registry, session_id)
        parent_project = info.get("parent_project")
        if info["inbox_level"] == INBOX_LEVEL_SESSION:
            buckets: List[str] = []
            if parent_project:
                buckets.append(parent_project)
            return buckets
        if info["inbox_level"] == INBOX_LEVEL_PROJECT:
            return []
        return []

    def _resolve_delivery_bucket(
        self,
        registry: Dict[str, Any],
        target_agent: str,
        session_id: str,
    ) -> Dict[str, Any]:
        info = self._bucket_info(registry, session_id)
        if info["bucket"] == DEFAULT_SESSION_ID:
            return {
                "ok": False,
                "reason": "routing error: 'default' is deprecated; use a named project bucket or the agent inbox",
            }

        if info["inbox_level"] == INBOX_LEVEL_SESSION:
            record = info.get("record")
            active_session = info.get("active_session")
            if record and record.get("status") == "active" and active_session == session_id:
                return {
                    "ok": True,
                    "bucket": session_id,
                    "inbox_level": INBOX_LEVEL_SESSION,
                    "parent_project": info.get("parent_project"),
                    "escalated_from": None,
                    "escalation_reason": None,
                }
            if info.get("project"):
                return {
                    "ok": True,
                    "bucket": info["project"],
                    "inbox_level": INBOX_LEVEL_PROJECT,
                    "parent_project": info["project"],
                    "escalated_from": session_id,
                    "escalation_reason": "session_unavailable",
                }
            return {
                "ok": True,
                "bucket": session_id,
                "inbox_level": INBOX_LEVEL_SESSION,
                "parent_project": None,
                "escalated_from": None,
                "escalation_reason": None,
            }

        return {
            "ok": True,
            "bucket": info["bucket"],
            "inbox_level": info["inbox_level"],
            "parent_project": info.get("parent_project"),
            "escalated_from": None,
            "escalation_reason": None,
        }

    def _append_control_message(
        self,
        *,
        target_agent: str,
        session_id: str,
        sender: str,
        control_type: str,
        summary: str,
        body: str,
        status: str = "info",
        replace_existing_control: bool = False,
        inbox_level: Optional[str] = None,
        parent_project: Optional[str] = None,
        escalated_from: Optional[str] = None,
        escalation_reason: Optional[str] = None,
    ) -> Optional[str]:
        path = self.inbox_path(target_agent)
        rows = read_jsonl(path)
        matching_unread = [
            row
            for row in rows
            if row.get("session_id") == session_id
            and not row.get("read_at")
            and row.get("marker_variant") == "control"
            and row.get("control_type") == control_type
            and row.get("from") == sender
        ]
        if matching_unread and not replace_existing_control:
            return None
        if matching_unread and replace_existing_control:
            kept = []
            for row in rows:
                if (
                    row.get("session_id") == session_id
                    and not row.get("read_at")
                    and row.get("marker_variant") == "control"
                    and row.get("control_type") == control_type
                    and row.get("from") == sender
                ):
                    continue
                kept.append(row)
            rows = kept
            write_jsonl(path, rows)
        now = utc_now()
        message_id = str(uuid.uuid4())
        delivered = "From %s:\nTYPE: %s\nSTATUS: %s\nSUMMARY: %s\nACTION_REQUESTED: none\n\n%s" % (
            sender.capitalize(),
            control_type,
            status,
            summary,
            body,
        )
        append_jsonl(
            self.inbox_path(target_agent),
            {
                "id": message_id,
                "created_at": now,
                "session_id": session_id,
                "inbox_level": inbox_level,
                "parent_project": parent_project,
                "promoted_from": None,
                "promoted_at": None,
                "orphaned_at": None,
                "escalated_from": escalated_from,
                "escalation_reason": escalation_reason,
                "from": sender,
                "to": target_agent,
                "body": body,
                "delivered_message": delivered,
                "hash": sha256_text("%s\n%s\n%s\n%s\n%s" % (sender, target_agent, session_id, control_type, body)),
                "marker_variant": "control",
                "control_type": control_type,
                "hop_count": 0,
                "seen_at": None,
                "seen_by_session": None,
                "seen_via": None,
                "read_at": None,
                "handled_at": None,
                "handled_by_session": None,
                "handled_status": None,
                "failure_reason": None,
            },
        )
        return message_id

    def _enqueue_control_message(
        self,
        *,
        from_agent: str,
        to_agent: str,
        session_id: str,
        control_type: str,
        summary: str,
        body: str,
        status: str = "info",
        replace_existing_control: bool = False,
        inbox_level: Optional[str] = None,
        parent_project: Optional[str] = None,
        escalated_from: Optional[str] = None,
        escalation_reason: Optional[str] = None,
    ) -> BridgeResult:
        message_id = self._append_control_message(
            target_agent=to_agent,
            session_id=session_id,
            sender=from_agent,
            control_type=control_type,
            summary=summary,
            body=body,
            status=status,
            replace_existing_control=replace_existing_control,
            inbox_level=inbox_level,
            parent_project=parent_project,
            escalated_from=escalated_from,
            escalation_reason=escalation_reason,
        )
        if message_id is None:
            return BridgeResult(
                False,
                "rejected",
                "Target %s already has unread non-control bridge mail for session %s." % (to_agent, session_id),
            )
        self._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": utc_now(),
                "action": "send_control",
                "accepted": True,
                "from": from_agent,
                "to": to_agent,
                "session_id": session_id,
                "control_type": control_type,
                "message_id": message_id,
            }
        )
        return BridgeResult(
            True,
            "queued",
            "Queued control message %s for %s in session %s." % (control_type, to_agent, session_id),
            {"id": message_id, "control_type": control_type},
        )

    def _row_inbox_level(self, registry: Dict[str, Any], row: Dict[str, Any]) -> str:
        level = row.get("inbox_level")
        if level in {INBOX_LEVEL_SESSION, INBOX_LEVEL_PROJECT, INBOX_LEVEL_AGENT}:
            return level
        return self._bucket_info(registry, row.get("session_id", DEFAULT_SESSION_ID))["inbox_level"]

    def _row_parent_project(self, registry: Dict[str, Any], row: Dict[str, Any]) -> Optional[str]:
        if row.get("parent_project"):
            return row.get("parent_project")
        info = self._bucket_info(registry, row.get("session_id", DEFAULT_SESSION_ID))
        return info.get("parent_project")

    def _unread_for(self, agent: str, session_id: str) -> List[Dict[str, Any]]:
        return [
            row
            for row in read_jsonl(self.inbox_path(agent))
            if row.get("session_id") == session_id
            and not row.get("read_at")
            and not row.get("superseded_at")
        ]

    def _promote_superseded_inbox(self, agent: str, superseded_session: str, project_name: str) -> List[Dict[str, Any]]:
        """Promote unread inbox entries from a superseded session to the project bucket."""
        inbox = self.inbox_path(agent)
        rows = read_jsonl(inbox)
        now = utc_now()
        promoted: List[Dict[str, Any]] = []
        changed = False
        for row in rows:
            if row.get("session_id") == superseded_session and not row.get("read_at") and not row.get("superseded_at"):
                promoted.append(dict(row))
                row["session_id"] = project_name
                row["inbox_level"] = INBOX_LEVEL_PROJECT
                row["parent_project"] = project_name
                row["promoted_from"] = superseded_session
                row["promoted_at"] = now
                row["escalated_from"] = superseded_session
                row["escalation_reason"] = "session_superseded"
                changed = True
        if changed:
            write_jsonl(inbox, rows)
        return promoted

    def activate_session(
        self,
        agent: str,
        session_id: str,
        project: Optional[str] = None,
        bootstrap_origin: str = "unknown",
        allow_supersede: bool = True,
        trusted_parent_eligible: bool = False,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(agent)
                session = normalize_session(session_id)
                project_name = normalize_project(project)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            registry = self._load_session_registry()
            project_entry = self._project_registry(registry, project_name)
            active = project_entry.setdefault("active", {})
            sessions = project_entry.setdefault("sessions", {})
            previous_local = active.get(owner)
            previous_peer = active.get("claude" if owner == "codex" else "codex")
            previous_local_record = None
            if previous_local:
                previous_local_record = self._session_record(project_entry, previous_local, owner)

            project_entry["updated_at"] = utc_now()
            activation_status = "active"

            # Promote unread messages from the previous same-agent session to the
            # durable project bucket instead of burying them on the superseded
            # session.  The snapshot is returned for bootstrap/user visibility.
            drained_messages: List[Dict[str, Any]] = []
            should_supersede = bool(previous_local and previous_local != session)
            if (
                should_supersede
                and not allow_supersede
                and previous_local_record
                and previous_local_record.get("status") == "active"
                and previous_local_record.get("bootstrap_origin") == "parent"
            ):
                should_supersede = False
                activation_status = "registered_secondary"
            if should_supersede:
                drained_messages = self._promote_superseded_inbox(owner, previous_local, project_name)
                old_record = self._session_record(project_entry, previous_local, owner)
                old_record["status"] = "superseded"
                old_record["superseded_by"] = session
                old_record["superseded_at"] = utc_now()
                old_record["last_seen_at"] = utc_now()
                self._append_control_message(
                    target_agent=owner,
                    session_id=previous_local,
                    sender="bridge",
                    control_type="SESSION_UPDATE",
                    summary="Session superseded by newer %s session" % owner,
                    body=(
                        "A newer %s session for project %s is now active.\n\n"
                        "New session id: %s\n"
                        "Old session id: %s\n\n"
                        "Stop bridge communication from this older session and let the newer chat take over."
                    )
                    % (owner, project_name, session, previous_local),
                    replace_existing_control=True,
                )

            record = self._session_record(project_entry, session, owner)
            record["status"] = "active" if activation_status == "active" else "secondary"
            record["project"] = project_name
            record["activated_at"] = utc_now()
            record["last_seen_at"] = utc_now()
            record["bootstrap_origin"] = bootstrap_origin
            record["bootstrap_promoted_to_trusted"] = False
            if previous_peer:
                record["paired_with"] = previous_peer

            if activation_status == "active":
                active[owner] = session

            if previous_peer:
                peer_agent = "claude" if owner == "codex" else "codex"
                peer_record = self._session_record(project_entry, previous_peer, peer_agent)
                peer_record["paired_with"] = session
                peer_record["last_seen_at"] = utc_now()
                self._append_control_message(
                    target_agent=peer_agent,
                    session_id=previous_peer,
                    sender="bridge",
                    control_type="SESSION_UPDATE",
                    summary="Peer session updated for project %s" % project_name,
                    body=(
                        "The active %s session for project %s is now %s.\n\n"
                        "When sending new bridge traffic, prefer the newest active peer session."
                    )
                    % (owner, project_name, active.get(owner) or session),
                )

            trusted_info = self._trusted_parent_info(project_entry, owner)
            if trusted_parent_eligible and activation_status == "active":
                trusted_info["session_id"] = session
                trusted_info["promoted_at"] = utc_now()
                record["bootstrap_promoted_to_trusted"] = True

            self._save_session_registry(registry)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "activate_session",
                    "accepted": True,
                    "agent": owner,
                    "session_id": session,
                    "project": project_name,
                    "previous_local_session": previous_local,
                    "active_peer_session": previous_peer,
                    "bootstrap_origin": bootstrap_origin,
                    "activation_status": activation_status,
                }
            )
            return BridgeResult(
                True,
                activation_status,
                "Activated %s session %s for project %s." % (owner, session, project_name),
                {
                    "agent": owner,
                    "session_id": session,
                    "project": project_name,
                    "previous_local_session": previous_local,
                    "active_peer_session": previous_peer,
                    "bootstrap_origin": bootstrap_origin,
                    "trusted_parent_session": trusted_info.get("session_id"),
                    "trusted_parent_promoted_at": trusted_info.get("promoted_at"),
                    "registry_path": str(self.session_registry_path),
                    "drained_messages": drained_messages,
                },
            )

    def session_status(self, project: Optional[str] = None) -> BridgeResult:
        with self._locked():
            try:
                project_name = normalize_project(project)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            registry = self._load_session_registry()
            project_entry = self._project_registry(registry, project_name)
            return BridgeResult(
                True,
                "status",
                "Project %s active sessions: claude=%s codex=%s"
                % (
                    project_name,
                    project_entry.get("active", {}).get("claude"),
                    project_entry.get("active", {}).get("codex"),
                ),
                {
                    "project": project_name,
                    "schema_version": registry.get("schema_version"),
                    "active": dict(project_entry.get("active", {})),
                    "sessions": dict(project_entry.get("sessions", {})),
                    "trusted_parent": dict(project_entry.get("trusted_parent", {})),
                    "registry_path": str(self.session_registry_path),
                },
            )

    def repair_bootstrap_provenance(
        self,
        *,
        agent: str,
        project: str,
        bad_session_id: str,
        trusted_parent_session_id: Optional[str] = None,
        fallback_thread_id: Optional[str] = None,
        fallback_parent_thread_id: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(agent)
                project_name = normalize_project(project)
                bad_session = normalize_session(bad_session_id)
                trusted_hint = normalize_session(trusted_parent_session_id) if trusted_parent_session_id else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            now = utc_now()
            registry = self._load_session_registry()
            project_entry = self._project_registry(registry, project_name)
            active = project_entry.setdefault("active", {})
            sessions = project_entry.setdefault("sessions", {})
            bad_record = self._session_record(project_entry, bad_session, owner)
            peer_agent = "claude" if owner == "codex" else "codex"
            peer_session = active.get(peer_agent)
            trusted_info = self._trusted_parent_info(project_entry, owner)
            trusted_session = trusted_hint or trusted_info.get("session_id")
            trusted_record = sessions.get(trusted_session) if trusted_session else None
            trusted_thread_id = None if not trusted_record else (
                trusted_record.get("desktop_thread_id") or fallback_parent_thread_id or fallback_thread_id
            )
            trusted_valid = bool(
                trusted_session
                and trusted_record
                and trusted_record.get("bootstrap_origin") == "parent"
                and trusted_record.get("status") != "ended"
                and (
                    trusted_record.get("status") != "superseded"
                    or trusted_record.get("superseded_by") == bad_session
                )
                and trusted_thread_id
                and trusted_session != bad_session
            )

            if trusted_valid:
                promoted = self._promote_superseded_inbox(owner, bad_session, project_name)
                bad_record["status"] = "superseded"
                bad_record["superseded_by"] = trusted_session
                bad_record["superseded_at"] = now
                bad_record["last_seen_at"] = now
                trusted_record["status"] = "active"
                trusted_record["activated_at"] = now
                trusted_record["last_seen_at"] = now
                active[owner] = trusted_session
                self._save_session_registry(registry)

                breadcrumb = build_peer_runtime_breadcrumb(
                    state_dir=self.state_dir,
                    agent=owner,
                    session_id=trusted_session,
                    project=project_name,
                    desktop_thread_id=trusted_thread_id,
                    bootstrap_origin="parent",
                    bootstrap_thread_id=trusted_record.get("bootstrap_thread_id"),
                    bootstrap_parent_thread_id=trusted_record.get("bootstrap_parent_thread_id"),
                    trusted_parent_session_id=trusted_session,
                    subagent_signals={},
                )
                write_runtime_breadcrumb(peer_runtime_path_for_state_dir(self.state_dir, owner), breadcrumb)
                self._append_control_message(
                    target_agent=peer_agent,
                    session_id=peer_session or project_name,
                    sender="bridge",
                    control_type="ROUTE_REPAIR",
                    summary="Recovered %s session routing for project %s" % (owner, project_name),
                    body=(
                        "Recovered from bad bootstrap provenance for %s.\n\n"
                        "Previous session id: %s\n"
                        "Restored trusted parent session id: %s\n"
                        "Reason: subagent bootstrap provenance detected at wake time."
                    )
                    % (owner, bad_session, trusted_session),
                    replace_existing_control=True,
                )
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": now,
                        "action": "bootstrap_subagent_auto_rollback_succeeded",
                        "accepted": True,
                        "agent": owner,
                        "project": project_name,
                        "original_subagent_session": bad_session,
                        "rolled_back_to_session": trusted_session,
                        "peer_session": peer_session,
                        "promoted_messages": len(promoted),
                    }
                )
                return BridgeResult(
                    True,
                    "rolled_back",
                    "Rolled back %s from %s to trusted parent %s." % (owner, bad_session, trusted_session),
                    {
                        "agent": owner,
                        "project": project_name,
                        "bad_session_id": bad_session,
                        "restored_session_id": trusted_session,
                        "peer_session": peer_session,
                    },
                )

            state = self._load_state()
            state["paused"] = True
            freeze = state.setdefault("freeze", {})
            freeze.update(
                {
                    "scope": "frozen_after_subagent_bootstrap",
                    "frozen_at": now,
                    "frozen_agent": owner,
                    "project": project_name,
                    "bad_session_id": bad_session,
                    "trusted_parent_session_id": trusted_session,
                    "reason": "no_valid_trusted_parent",
                }
            )
            self._save_state(state)
            self._save_session_registry(registry)
            self._append_control_message(
                target_agent=peer_agent,
                session_id=peer_session or project_name,
                sender="bridge",
                control_type="BRIDGE_FROZEN",
                summary="Bridge frozen for %s in project %s" % (owner, project_name),
                body=(
                    "Bridge traffic for %s is frozen after bad bootstrap provenance was detected and no valid trusted parent session could be restored.\n\n"
                    "Bad session id: %s\n"
                    "Manual recovery action: re-bootstrap the parent thread explicitly."
                )
                % (owner, bad_session),
                replace_existing_control=True,
            )
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "bootstrap_subagent_auto_rollback_failed",
                    "accepted": False,
                    "agent": owner,
                    "project": project_name,
                    "original_subagent_session": bad_session,
                    "frozen": True,
                    "peer_session": peer_session,
                }
            )
            return BridgeResult(
                True,
                "frozen",
                "Bridge frozen for %s after bad bootstrap provenance with no valid trusted parent." % owner,
                {
                    "agent": owner,
                    "project": project_name,
                    "bad_session_id": bad_session,
                    "peer_session": peer_session,
                },
            )

    def end_session(self, agent: str, session_id: str, project: Optional[str] = None) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(agent)
                session = normalize_session(session_id)
                project_name = normalize_project(project)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            registry = self._load_session_registry()
            project_entry = self._project_registry(registry, project_name)
            active = project_entry.setdefault("active", {})
            record = self._session_record(project_entry, session, owner)
            peer_agent = "claude" if owner == "codex" else "codex"
            peer_session = active.get(peer_agent)
            drained_messages = [
                row for row in read_jsonl(self.inbox_path(owner))
                if row.get("session_id") == session and not row.get("read_at")
            ]

            record["status"] = "ended"
            record["ended_at"] = utc_now()
            record["last_seen_at"] = utc_now()
            if active.get(owner) == session:
                active.pop(owner, None)

            control = None
            if peer_session:
                control = self._enqueue_control_message(
                    from_agent="bridge",
                    to_agent=peer_agent,
                    session_id=peer_session,
                    control_type="SESSION_UPDATE",
                    summary="Session ending for project %s" % project_name,
                    body=(
                        "The %s session %s for project %s has ended.\n\n"
                        "Stop sending bridge traffic to that session until a new handshake arrives."
                    )
                    % (owner, session, project_name),
                    replace_existing_control=False,
                )

            self._save_session_registry(registry)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "end_session",
                    "accepted": True,
                    "agent": owner,
                    "session_id": session,
                    "project": project_name,
                    "peer_session": peer_session,
                    "drained_messages": len(drained_messages),
                    "drained_marked_read": False,
                }
            )
            return BridgeResult(
                True,
                "ended",
                "Ended %s session %s for project %s." % (owner, session, project_name),
                {
                    "agent": owner,
                    "session_id": session,
                    "project": project_name,
                    "peer_session": peer_session,
                    "drained_messages": drained_messages,
                    "drained_marked_read": False,
                    "control_result": None if control is None else dataclasses.asdict(control),
                },
            )

    def project_identity(self, cwd: Optional[str] = None) -> BridgeResult:
        try:
            data = derive_project_identity(cwd)
        except (ValueError, OSError) as exc:
            return BridgeResult(False, "rejected", str(exc))
        return BridgeResult(
            True,
            "identity",
            "Derived rendezvous %s from %s." % (data["rendezvous"], data["canonical_root"]),
            data,
        )

    @property
    def routing_rules_path(self) -> Path:
        return routing_rules_path_for_state_dir(self.state_dir)

    def evaluate_routing(self, source: str, direction: str, text: str) -> BridgeResult:
        try:
            settings = self._load_settings()
        except Exception as exc:
            return BridgeResult(False, "rejected", str(exc))
        if not settings.routing_rules_enabled:
            return BridgeResult(
                True,
                "disabled",
                "Routing rules are disabled by settings.json.",
                {
                    "decision": "disabled",
                    "rules_path": str(self.routing_rules_path),
                },
            )
        try:
            result = evaluate_message(
                source=source,
                direction=direction,
                text=text,
                rules_path=str(self.routing_rules_path),
            )
        except Exception as exc:
            return BridgeResult(False, "rejected", str(exc))
        return BridgeResult(
            True,
            result["decision"],
            "Routing decision: %s" % result["decision"],
            result,
        )

    def _record_rate_limit_send(
        self,
        state: Dict[str, Any],
        sender: str,
        target: str,
    ) -> None:
        """Append the current timestamp to the per-pair rate-limit ring buffer.

        Trims entries older than RATE_LIMIT_WINDOW_S so the buffer stays bounded.
        Caller is responsible for persisting the state via _save_state.
        """
        rate_limits = state.setdefault("rate_limits", {})
        key = "%s->%s" % (sender, target)
        cutoff = time.time() - RATE_LIMIT_WINDOW_S
        recent = [t for t in rate_limits.get(key, []) if isinstance(t, (int, float)) and t >= cutoff]
        recent.append(time.time())
        rate_limits[key] = recent

    def _check_rate_limit(
        self,
        state: Dict[str, Any],
        sender: str,
        target: str,
    ) -> Optional[str]:
        """Return a rejection reason if the (sender, target) pair is over budget."""
        rate_limits = state.get("rate_limits", {})
        key = "%s->%s" % (sender, target)
        cutoff = time.time() - RATE_LIMIT_WINDOW_S
        recent = [t for t in rate_limits.get(key, []) if isinstance(t, (int, float)) and t >= cutoff]
        if len(recent) >= RATE_LIMIT_N:
            return (
                "rate limit exceeded for %s -> %s (%d sends in last %ds; max %d). "
                "Pause the bridge with pause_bridge or wait."
                % (sender, target, len(recent), RATE_LIMIT_WINDOW_S, RATE_LIMIT_N)
            )
        return None

    def _resolve_default_session(self, from_agent: str) -> str:
        """Pick a sensible session_id when the caller didn't specify one.

        Falls back to the rendezvous (project name) of the from_agent's
        currently active session, so MCP-driven traffic gets per-project
        backpressure instead of all sharing the global "default" bucket.
        """
        try:
            agent = normalize_agent(from_agent)
        except ValueError:
            return DEFAULT_SESSION_ID
        try:
            registry = self._load_session_registry()
        except Exception:
            return DEFAULT_SESSION_ID
        for project_name, project_entry in (registry.get("projects") or {}).items():
            active = (project_entry or {}).get("active") or {}
            if active.get(agent):
                return project_name
        return DEFAULT_SESSION_ID

    def _orphan_stats(self) -> Dict[str, Any]:
        total = 0
        oldest = None
        for agent in sorted(AGENTS):
            path = self.state_dir / ("orphaned-%s.jsonl" % agent)
            if not path.exists():
                continue
            rows = read_jsonl(path)
            total += len(rows)
            for row in rows:
                stamp = row.get("orphaned_at") or row.get("created_at")
                if stamp and (oldest is None or stamp < oldest):
                    oldest = stamp
        return {"count": total, "oldest": oldest}

    def _sender_context_for_route(
        self,
        registry: Dict[str, Any],
        sender: str,
        requested_session: str,
        project_hint: Optional[str],
    ) -> Optional[SenderContext]:
        found = self._find_session_record(registry, requested_session, agent=sender)
        if found:
            return SenderContext(sender, requested_session, found["project"])
        if project_hint:
            project_entry = (registry.get("projects") or {}).get(project_hint, {})
            active_session = (project_entry.get("active") or {}).get(sender)
            if active_session:
                return SenderContext(sender, str(active_session), project_hint)
        return None

    def _address_for_route(
        self,
        target: str,
        delivery_bucket: str,
        delivery_level: str,
        parent_project: Optional[str],
    ):
        if delivery_level == INBOX_LEVEL_AGENT:
            return AgentInbox(target)
        if delivery_level == INBOX_LEVEL_PROJECT:
            return ProjectInbox(delivery_bucket, target)
        if delivery_level == INBOX_LEVEL_SESSION and parent_project:
            return SessionInbox(parent_project, target, delivery_bucket)
        return None

    def send_to_peer(self, from_agent: str, to_agent: str, message: str, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            now = utc_now()
            # If no session specified, route through the from_agent's active
            # project session so backpressure is per-project rather than the
            # shared global "default" bucket.
            resolved_session = session_id or self._resolve_default_session(from_agent)
            event = {
                "id": str(uuid.uuid4()),
                "timestamp": now,
                "action": "send_to_peer",
                "from": (from_agent or "").strip().lower(),
                "to": (to_agent or "").strip().lower(),
                "session_id": resolved_session,
                "marker_target": None,
                "marker_variant": None,
                "hash": None,
                "accepted": False,
                "reason": None,
            }

            def reject(reason: str) -> BridgeResult:
                event["reason"] = reason
                self._audit(event)
                return BridgeResult(False, "rejected", reason, {"audit_id": event["id"]})

            try:
                sender = normalize_agent(from_agent)
                target = normalize_agent(to_agent)
                session = normalize_session(resolved_session)
            except ValueError as exc:
                return reject(str(exc))
            if session == DEFAULT_SESSION_ID:
                return reject("routing error: 'default' is deprecated; use a named project bucket or the agent inbox")

            marker = strip_markers(message)
            body = marker["body"]
            body_hash = sha256_text("%s\n%s\n%s" % (sender, target, body))
            event["from"] = sender
            event["to"] = target
            event["session_id"] = session
            event["marker_target"] = marker["marker_target"]
            event["marker_variant"] = marker["marker_variant"]
            event["hash"] = body_hash

            if sender == target:
                return reject("from and to must be different agents")
            if marker["stop"]:
                if marker["stop"] == "pause-relay":
                    state = self._load_state()
                    state["paused"] = True
                    self._save_state(state)
                    return reject("pause-relay sentinel present; bridge paused and relay skipped")
                return reject("stop sentinel %s present; relay skipped" % marker["stop"])
            if marker["marker_target"] is None:
                return reject("missing required [[handoff:<agent>]] marker")
            if marker["marker_target"] != target:
                return reject("handoff marker targets %s but to is %s" % (marker["marker_target"], target))
            if not body:
                return reject("message is empty after stripping markers")
            body_bytes = len(body.encode("utf-8"))
            if body_bytes > MAX_MESSAGE_BYTES:
                return reject(
                    "message too large (%dkb > 64kb). Link to the file instead of embedding raw output."
                    % max(1, round(body_bytes / 1024))
                )

            state = self._load_state()
            if state.get("paused"):
                return reject("bridge is paused")

            registry = self._load_session_registry()
            delivery = self._resolve_delivery_bucket(registry, target, session)
            if not delivery["ok"]:
                return reject(delivery["reason"])
            delivery_bucket = delivery["bucket"]
            delivery_level = delivery["inbox_level"]
            parent_project = delivery.get("parent_project")
            if delivery_level == INBOX_LEVEL_AGENT:
                return reject("agent-level inbox %s is reserved for control/recovery traffic" % delivery_bucket)
            event["resolved_session_id"] = delivery_bucket
            event["inbox_level"] = delivery_level
            event["escalated_from"] = delivery.get("escalated_from")
            event["escalation_reason"] = delivery.get("escalation_reason")
            requested_bucket_info = self._bucket_info(registry, session)
            if (
                delivery_level == INBOX_LEVEL_SESSION
                and requested_bucket_info.get("record")
                and requested_bucket_info["record"].get("agent") == target
                and requested_bucket_info["record"].get("status") == "active"
                and requested_bucket_info.get("active_session") == session
            ):
                project_for_target = requested_bucket_info.get("project")
                if project_for_target:
                    project_entry = (registry.get("projects") or {}).get(project_for_target, {})
                sender_active_session = (project_entry.get("active") or {}).get(sender) if project_for_target else None
                sender_record = (
                    (project_entry.get("sessions") or {}).get(sender_active_session)
                    if project_for_target and sender_active_session
                    else None
                )
                if not isinstance(sender_record, dict) or sender_record.get("status") != "active":
                    return reject(
                        "superseded sender session for %s is not proven active; cannot send to active target session %s"
                        % (sender, session)
                    )
            project_hint = parent_project or (delivery_bucket if delivery_level == INBOX_LEVEL_PROJECT else None)
            sender_context = self._sender_context_for_route(registry, sender, session, project_hint)
            target_address = self._address_for_route(target, delivery_bucket, delivery_level, parent_project)
            if sender_context and target_address:
                route = resolve_route(
                    sender=sender_context,
                    target=target_address,
                    kind=MessageKind.WORK,
                    registry=registry,
                )
                if not route.ok:
                    return reject(route.reason or "routing rejected")

            session_state = self._session_state(state, delivery_bucket)
            hop_count = int(session_state.get("hop_count", 0))
            event["hop_count_before"] = hop_count
            # NOTE: hop count is retained as audit metadata only — no longer
            # rejected when over self.max_hops.  Per-pair rate limiting
            # (below) replaces it as the loop-protection mechanism, agreed
            # via bridge HEURISTIC_SYNC 2026-04-27.

            rate_limit_reason = self._check_rate_limit(state, sender, target)
            if rate_limit_reason:
                return reject(rate_limit_reason)

            if body_hash in session_state.get("seen_hashes", []):
                return reject("duplicate message hash for session %s" % delivery_bucket)

            unread_work = [
                row for row in self._unread_for(target, delivery_bucket)
                if row.get("marker_variant") != "control"
            ]
            if delivery_level == INBOX_LEVEL_SESSION and len(unread_work) >= SESSION_BACKPRESSURE_LIMIT:
                return reject("target %s already has one unread work message for session %s" % (target, delivery_bucket))
            if delivery_level == INBOX_LEVEL_PROJECT and len(unread_work) >= PROJECT_BACKPRESSURE_LIMIT:
                return reject(
                    "target %s project inbox %s is full (%d unread >= %d)"
                    % (target, delivery_bucket, len(unread_work), PROJECT_BACKPRESSURE_LIMIT)
                )

            delivered = "From %s:\n%s" % (sender.capitalize(), body)
            inbox_row = {
                "id": event["id"],
                "created_at": now,
                "session_id": delivery_bucket,
                "inbox_level": delivery_level,
                "parent_project": parent_project,
                "promoted_from": None,
                "promoted_at": None,
                "orphaned_at": None,
                "escalated_from": delivery.get("escalated_from"),
                "escalation_reason": delivery.get("escalation_reason"),
                "from": sender,
                "to": target,
                "body": body,
                "delivered_message": delivered,
                "hash": body_hash,
                "marker_variant": marker["marker_variant"],
                "hop_count": hop_count + 1,
                "seen_at": None,
                "seen_by_session": None,
                "seen_via": None,
                "read_at": None,
                "handled_at": None,
                "handled_by_session": None,
                "handled_status": None,
                "failure_reason": None,
            }
            append_jsonl(self.inbox_path(target), inbox_row)

            session_state["hop_count"] = hop_count + 1
            seen = list(session_state.get("seen_hashes", []))
            seen.append(body_hash)
            session_state["seen_hashes"] = seen[-50:]
            session_state["last_message_at"] = now
            self._record_rate_limit_send(state, sender, target)
            self._save_state(state)

            event["accepted"] = True
            event["reason"] = "delivered"
            event["hop_count_after"] = hop_count + 1
            self._audit(event)

            note = "Queued message for %s in session %s." % (target, delivery_bucket)
            if marker["marker_variant"] == "human-review":
                note += " Human review requested."
            return BridgeResult(
                True,
                "queued",
                note,
                {
                    "id": event["id"],
                    "hop_count": hop_count + 1,
                    "resolved_session_id": delivery_bucket,
                    "inbox_level": delivery_level,
                    "escalated_from": delivery.get("escalated_from"),
                    "escalation_reason": delivery.get("escalation_reason"),
                },
            )

    def send_control_message(
        self,
        from_agent: str,
        to_agent: str,
        control_type: str,
        summary: str,
        body: str,
        session_id: Optional[str] = None,
        status: str = "info",
        replace_existing_control: bool = True,
    ) -> BridgeResult:
        with self._locked():
            # Same fallback policy as send_to_peer: route through the
            # from_agent's active project session when session_id is omitted.
            resolved_session = session_id or self._resolve_default_session(from_agent)
            try:
                sender = normalize_agent(from_agent)
                target = normalize_agent(to_agent)
                session = normalize_session(resolved_session)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            if session == DEFAULT_SESSION_ID:
                return BridgeResult(
                    False,
                    "rejected",
                    "routing error: 'default' is deprecated; use a named project bucket or the agent inbox",
                )
            control_name = (control_type or "").strip().upper()
            if not control_name:
                return BridgeResult(False, "rejected", "control_type is required")
            if not summary.strip():
                return BridgeResult(False, "rejected", "summary is required")
            if not body.strip():
                return BridgeResult(False, "rejected", "body is required")
            registry = self._load_session_registry()
            delivery = self._resolve_delivery_bucket(registry, target, session)
            if not delivery["ok"]:
                return BridgeResult(False, "rejected", delivery["reason"])
            return self._enqueue_control_message(
                from_agent=sender,
                to_agent=target,
                session_id=delivery["bucket"],
                control_type=control_name,
                summary=summary.strip(),
                body=body.strip(),
                status=status.strip() or "info",
                replace_existing_control=replace_existing_control,
                inbox_level=delivery["inbox_level"],
                parent_project=delivery.get("parent_project"),
                escalated_from=delivery.get("escalated_from"),
                escalation_reason=delivery.get("escalation_reason"),
            )

    def check_inbox(
        self,
        agent: str,
        session_id: Optional[str] = None,
        mark_read: bool = False,
        include_parents: bool = False,
        record_seen: bool = True,
    ) -> BridgeResult:
        with self._locked():
            try:
                target = normalize_agent(agent)
                session = None if session_id is None else normalize_session(session_id)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            path = self.inbox_path(target)
            rows = read_jsonl(path)
            registry = self._load_session_registry()
            buckets: Optional[List[str]] = None
            if session is not None:
                buckets = [session]
            if include_parents and session is not None and buckets is not None:
                for parent in self._parent_buckets_for(registry, session):
                    if parent not in buckets:
                        buckets.append(parent)
            unread = [
                row
                for row in rows
                if (buckets is None or row.get("session_id") in buckets) and not row.get("read_at")
            ]
            if unread and record_seen:
                now = utc_now()
                unread_ids = {row["id"] for row in unread}
                changed_seen = False
                for row in rows:
                    if row.get("id") in unread_ids and not row.get("seen_at"):
                        row["seen_at"] = now
                        row["seen_by_session"] = row.get("session_id")
                        row["seen_via"] = "check_inbox"
                        changed_seen = True
                if changed_seen:
                    write_jsonl(path, rows)
                    self._audit(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": now,
                            "action": "mark_seen",
                            "agent": target,
                            "session_id": session,
                            "via": "check_inbox",
                            "accepted": True,
                            "message_count": len(unread),
                        }
                    )

            if mark_read and unread:
                now = utc_now()
                unread_ids = {row["id"] for row in unread}
                for row in rows:
                    if row.get("id") in unread_ids:
                        row["read_at"] = now
                write_jsonl(path, rows)
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": now,
                        "action": "check_inbox",
                        "agent": target,
                        "session_id": session,
                        "include_parents": include_parents,
                        "accepted": True,
                        "reason": "marked_read",
                        "message_count": len(unread),
                    }
                )

            if not unread:
                if session is None:
                    scope = "all buckets"
                else:
                    scope = "session %s" % session if not include_parents else "session %s (+parents)" % session
                return BridgeResult(True, "empty", "No unread bridge messages for %s in %s." % (target, scope))

            delivered = "\n\n".join(row["delivered_message"] for row in unread)
            returned_buckets = sorted({str(row.get("session_id")) for row in unread})
            return BridgeResult(
                True,
                "messages",
                delivered,
                {
                    "count": len(unread),
                    "messages": unread,
                    "buckets": returned_buckets if buckets is None else buckets,
                },
            )

    def wait_inbox(
        self,
        agent: str,
        session_ids: Optional[List[str]] = None,
        timeout_seconds: int = 600,
        mark_read: bool = False,
        record_seen: bool = True,
    ) -> BridgeResult:
        """Block until a new message arrives for the agent or timeout elapses.

        Designed for the "blocking-tool-call" wake pattern: the model is
        suspended at the MCP tool boundary while we wait, so idle time costs
        zero tokens.  Lets a peer agent sit in a tight loop:

            while True:
                msgs = wait_inbox(agent='codex', timeout_seconds=600)
                if msgs.timed_out:
                    continue          # immediately re-invoke, no token cost
                handle(msgs)

        Args:
          agent:           Receiving agent ('claude' or 'codex').
          session_ids:     Optional list of session buckets to watch.  None
                           (or empty list) means "any session" — useful when
                           the receiver doesn't know which bucket the sender
                           used.  Typical value: [project_name, 'default',
                           own_GUID].
          timeout_seconds: Max wait (clamped 1..3600).  Caller should re-invoke
                           on timeout — re-invocation is cheap.
          mark_read:       Default False — return the message without marking,
                           let the caller decide when to mark_read after they
                           have actually surfaced it.  Avoids the silent-eat
                           failure mode of consume_inbox.py.

        Returns:
          BridgeResult with status='messages' (and data.messages populated) or
          status='timeout' (data.timed_out=True).  On rejection: status='rejected'.
        """
        try:
            target = normalize_agent(agent)
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))

        valid_sessions: Optional[set] = None
        if session_ids:
            if any(not isinstance(s, str) for s in session_ids):
                return BridgeResult(False, "rejected", "session_ids must be a list of strings")
            try:
                valid_sessions = {normalize_session(s) for s in session_ids}
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

        timeout_seconds = max(1, min(int(timeout_seconds or 600), 3600))
        deadline = time.time() + timeout_seconds
        poll_interval = 1.0

        def _matches(row: Dict[str, Any]) -> bool:
            if row.get("read_at") or row.get("superseded_at"):
                return False
            if valid_sessions is not None:
                return row.get("session_id") in valid_sessions
            return True

        while True:
            with self._locked():
                path = self.inbox_path(target)
                rows = read_jsonl(path)
                unread = [row for row in rows if _matches(row)]
                if unread:
                    if record_seen:
                        now = utc_now()
                        unread_ids = {row["id"] for row in unread}
                        changed_seen = False
                        for row in rows:
                            if row.get("id") in unread_ids and not row.get("seen_at"):
                                row["seen_at"] = now
                                row["seen_by_session"] = row.get("session_id")
                                row["seen_via"] = "wait_inbox"
                                changed_seen = True
                        if changed_seen:
                            write_jsonl(path, rows)
                            self._audit(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": now,
                                    "action": "mark_seen",
                                    "agent": target,
                                    "session_ids": sorted(valid_sessions) if valid_sessions else None,
                                    "via": "wait_inbox",
                                    "accepted": True,
                                    "message_count": len(unread),
                                }
                            )
                    if mark_read:
                        now = utc_now()
                        unread_ids = {row["id"] for row in unread}
                        for row in rows:
                            if row.get("id") in unread_ids:
                                row["read_at"] = now
                        write_jsonl(path, rows)
                        self._audit(
                            {
                                "id": str(uuid.uuid4()),
                                "timestamp": now,
                                "action": "wait_inbox",
                                "agent": target,
                                "session_ids": sorted(valid_sessions) if valid_sessions else None,
                                "accepted": True,
                                "reason": "delivered_and_marked",
                                "message_count": len(unread),
                            }
                        )
                    delivered = "\n\n".join(row["delivered_message"] for row in unread)
                    return BridgeResult(
                        True,
                        "messages",
                        delivered,
                        {
                            "count": len(unread),
                            "messages": unread,
                            "timed_out": False,
                            "marked_read": mark_read,
                        },
                    )

            # No matching unread.  Release lock, sleep, retry.
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))

        scope = ",".join(sorted(valid_sessions)) if valid_sessions else "any"
        return BridgeResult(
            True,
            "timeout",
            (
                "wait_inbox timed out after %ds with no new messages for %s (sessions=%s). "
                "Call again immediately to keep waiting — re-invocation is cheap."
            )
            % (timeout_seconds, target, scope),
            {
                "count": 0,
                "messages": [],
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
            },
        )

    def peek_inbox(self, agent: str, session_id: Optional[str] = None, include_parents: bool = False) -> BridgeResult:
        return self.check_inbox(
            agent,
            session_id=session_id,
            mark_read=False,
            include_parents=include_parents,
            record_seen=False,
        )

    def mark_read(self, agent: str, message_id: str, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            try:
                target = normalize_agent(agent)
                session = normalize_session(session_id) if session_id is not None else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            target_id = (message_id or "").strip()
            if not target_id:
                return BridgeResult(False, "rejected", "message_id is required")

            path = self.inbox_path(target)
            rows = read_jsonl(path)
            now = utc_now()
            matched = False
            changed = False
            for row in rows:
                if row.get("id") == target_id and (session is None or row.get("session_id") == session):
                    matched = True
                    if not row.get("read_at"):
                        row["read_at"] = now
                        changed = True
                    if not row.get("seen_at"):
                        row["seen_at"] = now
                        row["seen_by_session"] = row.get("session_id")
                        row["seen_via"] = "implicit_via_mark_read"
                        changed = True
                    break

            if not matched:
                if session is None:
                    return BridgeResult(False, "not_found", "No message %s for %s." % (target_id, target))
                return BridgeResult(False, "not_found", "No message %s for %s in session %s." % (target_id, target, session))

            if changed:
                write_jsonl(path, rows)

            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "mark_read",
                    "agent": target,
                    "session_id": session,
                    "message_id": target_id,
                    "accepted": True,
                    "changed": changed,
                }
            )
            return BridgeResult(
                True,
                "marked_read" if changed else "already_read",
                "Message %s is marked read." % target_id,
                {"message_id": target_id, "changed": changed},
            )

    def wake_breaker_status(self, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            target_session = normalize_session(session_id) if session_id is not None else None
            payload = read_json(self.wake_breaker_path, {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}})
            sessions = payload.get("sessions", {})
            if target_session is not None:
                return BridgeResult(
                    True,
                    "status",
                    "Wake breaker status for %s." % target_session,
                    {
                        "schema_version": payload.get("schema_version", WAKE_BREAKER_SCHEMA_VERSION),
                        "session_id": target_session,
                        "session": copy.deepcopy(sessions.get(target_session)),
                        "path": str(self.wake_breaker_path),
                    },
                )
            return BridgeResult(
                True,
                "status",
                "Wake breaker status for %d session(s)." % len(sessions),
                {
                    "schema_version": payload.get("schema_version", WAKE_BREAKER_SCHEMA_VERSION),
                    "sessions": copy.deepcopy(sessions),
                    "path": str(self.wake_breaker_path),
                },
            )

    def resume_wake_for_session(self, session_id: str) -> BridgeResult:
        with self._locked():
            try:
                session = normalize_session(session_id)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            payload = read_json(self.wake_breaker_path, {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}})
            sessions = payload.setdefault("sessions", {})
            changed = bool(sessions.pop(session, None) is not None)
            payload["schema_version"] = WAKE_BREAKER_SCHEMA_VERSION
            payload["updated_at"] = utc_now()
            if changed:
                write_json(self.wake_breaker_path, payload)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "wake_breaker_closed",
                    "accepted": True,
                    "session_id": session,
                    "reason": "resume_call",
                    "changed": changed,
                }
            )
            return BridgeResult(
                True,
                "cleared" if changed else "already_clear",
                "Wake breaker cleared for %s." % session,
                {"session_id": session, "changed": changed},
            )

    def _message_lifecycle_status(self, row: Dict[str, Any]) -> str:
        if row.get("handled_status") == "failed" or row.get("failure_reason"):
            return "failed"
        if row.get("handled_at"):
            return "handled"
        if row.get("read_at"):
            return "read"
        if row.get("seen_at"):
            return "seen"
        return "queued"

    def _truncate_preview(self, value: Any, max_chars: int) -> Dict[str, Any]:
        text = "" if value is None else str(value)
        if max_chars <= 0:
            return {"preview": "", "truncated": bool(text), "length": len(text)}
        return {
            "preview": text[:max_chars],
            "truncated": len(text) > max_chars,
            "length": len(text),
        }

    def _receipt_summary(self, agent: str, row: Dict[str, Any], lifecycle_status: str, body_preview_chars: int) -> Dict[str, Any]:
        body = self._truncate_preview(row.get("body", ""), body_preview_chars)
        delivered = self._truncate_preview(row.get("delivered_message", ""), body_preview_chars)
        return {
            "id": row.get("id"),
            "agent": agent,
            "from": row.get("from"),
            "to": row.get("to"),
            "session_id": row.get("session_id"),
            "inbox_level": row.get("inbox_level"),
            "parent_project": row.get("parent_project"),
            "created_at": row.get("created_at"),
            "seen_at": row.get("seen_at"),
            "seen_via": row.get("seen_via"),
            "read_at": row.get("read_at"),
            "handled_at": row.get("handled_at"),
            "handled_status": row.get("handled_status"),
            "failure_reason": row.get("failure_reason"),
            "lifecycle_status": lifecycle_status,
            "body_preview": body["preview"],
            "body_truncated": body["truncated"],
            "body_length": body["length"],
            "delivered_preview": delivered["preview"],
            "delivered_truncated": delivered["truncated"],
            "delivered_length": delivered["length"],
        }

    def _bounded_int(self, name: str, value: Any, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("%s must be an integer" % name)
        if value < minimum or value > maximum:
            raise ValueError("%s must be between %d and %d" % (name, minimum, maximum))
        return value

    def record_pending_bridge_action(
        self,
        owner_agent: str,
        summary: str,
        *,
        message_id: Optional[str] = None,
        related_session_id: Optional[str] = None,
        priority: str = "normal",
        due_at: Optional[str] = None,
        details: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
                related_session = normalize_session(related_session_id) if related_session_id is not None else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            text = (summary or "").strip()
            if not text:
                return BridgeResult(False, "rejected", "summary is required")
            if len(text) > 240:
                return BridgeResult(False, "rejected", "summary must be 240 characters or fewer")

            resolved_priority = (priority or "normal").strip().lower()
            if resolved_priority not in {"low", "normal", "high", "urgent"}:
                return BridgeResult(False, "rejected", "priority must be low, normal, high, or urgent")

            pending = self._load_pending_actions()
            now = utc_now()
            action_id = str(uuid.uuid4())
            action = {
                "id": action_id,
                "owner_agent": owner,
                "summary": text,
                "details": (details or "").strip() or None,
                "message_id": (message_id or "").strip() or None,
                "related_session_id": related_session,
                "priority": resolved_priority,
                "due_at": (due_at or "").strip() or None,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "resolved_at": None,
                "resolved_by": None,
                "resolution": None,
            }
            pending.setdefault("actions", []).append(action)
            self._save_pending_actions(pending)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "record_pending_bridge_action",
                    "owner_agent": owner,
                    "pending_action_id": action_id,
                    "message_id": action["message_id"],
                    "related_session_id": related_session,
                    "priority": resolved_priority,
                    "accepted": True,
                }
            )
            return BridgeResult(
                True,
                "recorded",
                "Recorded pending bridge action %s for %s." % (action_id, owner),
                {"action": action},
            )

    def list_pending_bridge_actions(
        self,
        owner_agent: Optional[str] = None,
        *,
        status: str = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent) if owner_agent else None
                page_limit = self._bounded_int("limit", limit, 1, 200)
                page_offset = self._bounded_int("offset", offset, 0, 1_000_000)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            status_filter = (status or "pending").strip().lower()
            if status_filter not in {"pending", "resolved", "all"}:
                return BridgeResult(False, "rejected", "status must be pending, resolved, or all")

            pending = self._load_pending_actions()
            filtered: List[Dict[str, Any]] = []
            for action in pending.get("actions", []):
                if owner and action.get("owner_agent") != owner:
                    continue
                if status_filter != "all" and action.get("status") != status_filter:
                    continue
                filtered.append(copy.deepcopy(action))
            total_count = len(filtered)
            page = filtered[page_offset:page_offset + page_limit]
            return BridgeResult(
                True,
                "pending_bridge_actions",
                "%d pending bridge action(s); returning %d starting at offset %d."
                % (total_count, len(page), page_offset),
                {
                    "count": len(page),
                    "total_count": total_count,
                    "limit": page_limit,
                    "offset": page_offset,
                    "has_more": page_offset + len(page) < total_count,
                    "actions": page,
                },
            )

    def next_pending_bridge_action(self, owner_agent: str) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}

            def _parse_iso(value: Any) -> Optional[str]:
                raw = str(value or "").strip()
                if not raw:
                    return None
                try:
                    parsed = datetime.fromisoformat(raw)
                except ValueError:
                    return None
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.isoformat(timespec="seconds")

            def _sort_key(action: Dict[str, Any]) -> tuple:
                due_at = _parse_iso(action.get("due_at"))
                created_at = _parse_iso(action.get("created_at"))
                due_bucket = 0 if due_at else 1
                due_value = due_at or "9999-12-31T23:59:59+00:00"
                created_value = created_at or "9999-12-31T23:59:59+00:00"
                return (
                    priority_order.get(str(action.get("priority") or "normal").strip().lower(), 2),
                    due_bucket,
                    due_value,
                    created_value,
                    str(action.get("id") or ""),
                )

            pending = self._load_pending_actions()
            candidates = [
                copy.deepcopy(action)
                for action in pending.get("actions", [])
                if action.get("owner_agent") == owner and action.get("status") == "pending"
            ]
            candidates.sort(key=_sort_key)
            next_action = candidates[0] if candidates else None
            return BridgeResult(
                True,
                "next_pending_bridge_action" if next_action else "empty",
                "Next pending bridge action selected for %s." % owner
                if next_action
                else "No pending bridge actions for %s." % owner,
                {
                    "owner_agent": owner,
                    "action": next_action,
                    "count": len(candidates),
                },
            )

    def resolve_pending_bridge_action(
        self,
        action_id: str,
        *,
        resolved_by: Optional[str] = None,
        resolution: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                resolver = normalize_agent(resolved_by) if resolved_by else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            target_id = (action_id or "").strip()
            if not target_id:
                return BridgeResult(False, "rejected", "action_id is required")

            pending = self._load_pending_actions()
            now = utc_now()
            matched: Optional[Dict[str, Any]] = None
            for action in pending.get("actions", []):
                if action.get("id") != target_id:
                    continue
                matched = action
                if action.get("status") != "resolved":
                    action["status"] = "resolved"
                    action["resolved_at"] = now
                if resolver:
                    action["resolved_by"] = resolver
                if resolution is not None:
                    action["resolution"] = (resolution or "").strip() or None
                action["updated_at"] = now
                break
            if not matched:
                return BridgeResult(False, "not_found", "No pending bridge action %s." % target_id)

            self._save_pending_actions(pending)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "resolve_pending_bridge_action",
                    "pending_action_id": target_id,
                    "resolved_by": resolver,
                    "accepted": True,
                }
            )
            return BridgeResult(
                True,
                "resolved",
                "Pending bridge action %s resolved." % target_id,
                {"action": copy.deepcopy(matched)},
            )

    def message_status(self, message_id: str) -> BridgeResult:
        with self._locked():
            target_id = (message_id or "").strip()
            if not target_id:
                return BridgeResult(False, "rejected", "message_id is required")

            for agent in sorted(AGENTS):
                for row in read_jsonl(self.inbox_path(agent)):
                    if row.get("id") == target_id:
                        status = self._message_lifecycle_status(row)
                        return BridgeResult(
                            True,
                            status,
                            "Message %s is %s." % (target_id, status),
                            {
                                "message": row,
                                "agent": agent,
                                "message_id": target_id,
                                "lifecycle_status": status,
                            },
                        )
            return BridgeResult(False, "not_found", "No message %s found." % target_id)

    def mark_seen(
        self,
        agent: str,
        message_id: str,
        via: str,
        session_id: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                target = normalize_agent(agent)
                session = normalize_session(session_id) if session_id is not None else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            target_id = (message_id or "").strip()
            seen_via = (via or "").strip()
            if not target_id:
                return BridgeResult(False, "rejected", "message_id is required")
            if not seen_via:
                return BridgeResult(False, "rejected", "via is required")

            path = self.inbox_path(target)
            rows = read_jsonl(path)
            now = utc_now()
            matched = False
            changed = False
            for row in rows:
                if row.get("id") == target_id and (session is None or row.get("session_id") == session):
                    matched = True
                    if not row.get("seen_at"):
                        row["seen_at"] = now
                        row["seen_by_session"] = row.get("session_id")
                        row["seen_via"] = seen_via
                        changed = True
                    break
            if not matched:
                return BridgeResult(False, "not_found", "No message %s for %s." % (target_id, target))
            if changed:
                write_jsonl(path, rows)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "mark_seen",
                    "agent": target,
                    "session_id": session,
                    "message_id": target_id,
                    "via": seen_via,
                    "accepted": True,
                    "changed": changed,
                }
            )
            return BridgeResult(
                True,
                "seen" if changed else "already_seen",
                "Message %s is marked seen." % target_id,
                {"message_id": target_id, "changed": changed},
            )

    def mark_handled(
        self,
        agent: str,
        message_id: str,
        status: str = "handled",
        reason: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                target = normalize_agent(agent)
                session = normalize_session(session_id) if session_id is not None else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            target_id = (message_id or "").strip()
            handled_status = (status or "handled").strip().lower()
            if not target_id:
                return BridgeResult(False, "rejected", "message_id is required")
            if handled_status not in {"handled", "failed", "ignored"}:
                return BridgeResult(False, "rejected", "status must be handled, failed, or ignored")

            path = self.inbox_path(target)
            rows = read_jsonl(path)
            now = utc_now()
            matched = False
            for row in rows:
                if row.get("id") == target_id and (session is None or row.get("session_id") == session):
                    matched = True
                    row["handled_at"] = now
                    row["handled_by_session"] = row.get("session_id")
                    row["handled_status"] = handled_status
                    row["failure_reason"] = (reason or None) if handled_status == "failed" else None
                    break
            if not matched:
                return BridgeResult(False, "not_found", "No message %s for %s." % (target_id, target))
            write_jsonl(path, rows)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "mark_handled",
                    "agent": target,
                    "session_id": session,
                    "message_id": target_id,
                    "handled_status": handled_status,
                    "failure_reason": reason,
                    "accepted": True,
                }
            )
            return BridgeResult(
                True,
                handled_status,
                "Message %s handled status is %s." % (target_id, handled_status),
                {"message_id": target_id, "handled_status": handled_status},
            )

    def list_pending_receipts(
        self,
        agent: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        body_preview_chars: int = 240,
    ) -> BridgeResult:
        with self._locked():
            try:
                agents = [normalize_agent(agent)] if agent else sorted(AGENTS)
                page_limit = self._bounded_int("limit", limit, 1, 200)
                page_offset = self._bounded_int("offset", offset, 0, 1_000_000)
                preview_chars = self._bounded_int("body_preview_chars", body_preview_chars, 0, 4000)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            pending: List[Dict[str, Any]] = []
            for target in agents:
                for row in read_jsonl(self.inbox_path(target)):
                    status = self._message_lifecycle_status(row)
                    if status in {"queued", "seen", "read"}:
                        pending.append(self._receipt_summary(target, row, status, preview_chars))
            total_count = len(pending)
            page = pending[page_offset:page_offset + page_limit]
            return BridgeResult(
                True,
                "pending_receipts",
                "%d pending receipt(s); returning %d starting at offset %d."
                % (total_count, len(page), page_offset),
                {
                    "count": len(page),
                    "total_count": total_count,
                    "limit": page_limit,
                    "offset": page_offset,
                    "has_more": page_offset + len(page) < total_count,
                    "messages": page,
                },
            )

    def bridge_status(self, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            state = self._load_state()
            session = normalize_session(session_id)
            session_state = self._session_state(state, session)
            registry = self._load_session_registry()
            unread = {
                agent: len(self._unread_for(agent, session))
                for agent in sorted(AGENTS)
            }
            rules_path = routing_rules_path_for_state_dir(self.state_dir)
            routing_rules = {
                "path": str(rules_path),
                "status": "missing",
                "learned": 0,
                "suppressed": 0,
                "enabled": True,
            }
            try:
                routing_rules["enabled"] = self._load_settings().routing_rules_enabled
            except Exception as exc:
                routing_rules["status"] = "settings_error: %s" % exc
            if rules_path.exists():
                try:
                    rules = read_json(rules_path, {})
                    if not str(routing_rules.get("status", "")).startswith("settings_error"):
                        routing_rules["status"] = "healthy"
                    routing_rules["learned"] = len(rules.get("learned_triggers", []))
                    routing_rules["suppressed"] = len(rules.get("suppressed_triggers", []))
                except Exception:
                    routing_rules["status"] = "unreadable"
            return BridgeResult(
                True,
                "status",
                "Bridge is %s. Session %s has %s hops." % (
                    "paused" if state.get("paused") else "active",
                    session,
                    session_state.get("hop_count", 0),
                ),
                {
                    "paused": bool(state.get("paused")),
                    "session_id": session,
                    "hop_count": session_state.get("hop_count", 0),
                    "max_hops": self.max_hops,
                    "unread": unread,
                    "state_dir": str(self.state_dir),
                    "session_registry_path": str(self.session_registry_path),
                    "session_registry_projects": sorted(registry.get("projects", {}).keys()),
                    "orphaned": self._orphan_stats(),
                    "routing_rules": routing_rules,
                },
            )

    def bridge_process_status(self) -> BridgeResult:
        """Report bridge background process health without mutating state."""
        watcher_pid_path = watcher_pid_path_for_state_dir(self.state_dir)
        bridge_root = self.state_dir.parent
        watcher_lease_path = self.state_dir / "locks" / "watcher.lock"
        watcher_runtime_path = watcher_pid_path.parent / "watcher.runtime.json"
        tool_refresh_status_path = self.state_dir / "tool-refresh-status.json"
        watcher: Dict[str, Any] = {
            "expected": watcher_pid_path.exists(),
            "pid_path": str(watcher_pid_path),
            "runtime_path": str(watcher_runtime_path),
            "lease_path": str(watcher_lease_path),
            "running": False,
            "pid": None,
            "stale": False,
            "lease": None,
            "runtime": read_runtime_breadcrumb(watcher_runtime_path),
        }
        if watcher["runtime"] and watcher["runtime"].get("bridge_root") and Path(str(watcher["runtime"]["bridge_root"])) != bridge_root:
            watcher["root_mismatch"] = True
            watcher["stale"] = True
        if watcher_lease_path.exists():
            try:
                watcher["lease"] = read_json(watcher_lease_path, {})
                lease_pid = int((watcher["lease"] or {}).get("pid") or 0)
                if lease_pid:
                    watcher["pid"] = lease_pid
                    watcher["running"] = is_process_alive(lease_pid)
                    watcher["stale"] = not watcher["running"]
            except Exception:
                watcher["stale"] = True
        if watcher_pid_path.exists():
            try:
                pid = int(watcher_pid_path.read_text(encoding="utf-8").strip())
                watcher["pid_marker"] = pid
                if watcher["pid"] is None:
                    watcher["pid"] = pid
                    watcher["running"] = is_process_alive(pid)
                    watcher["stale"] = bool(watcher.get("stale")) or not watcher["running"]
            except (OSError, ValueError):
                watcher["stale"] = True

        server_markers: List[Dict[str, Any]] = []
        server_dir = self.state_dir / "server-pids"
        if server_dir.exists():
            for marker in sorted(server_dir.glob("server-*.pid")):
                runtime_path = marker.with_suffix(".json")
                entry: Dict[str, Any] = {
                    "path": str(marker),
                    "runtime_path": str(runtime_path),
                    "runtime": read_runtime_breadcrumb(runtime_path),
                    "pid": None,
                    "running": False,
                    "stale": False,
                }
                if entry["runtime"] and entry["runtime"].get("bridge_root") and Path(str(entry["runtime"]["bridge_root"])) != bridge_root:
                    entry["root_mismatch"] = True
                    entry["stale"] = True
                try:
                    pid = int(marker.read_text(encoding="utf-8").strip())
                    entry["pid"] = pid
                    entry["running"] = is_process_alive(pid)
                    entry["stale"] = bool(entry.get("stale")) or not entry["running"]
                except (OSError, ValueError):
                    entry["stale"] = True
                server_markers.append(entry)

        lock_entries: List[Dict[str, Any]] = []
        locks_dir = self.state_dir / "locks"
        if locks_dir.exists():
            for lock in sorted(locks_dir.glob("*.lock")):
                lock_entries.append(
                    {
                        "path": str(lock),
                        "name": lock.name,
                        "updated_at": datetime.fromtimestamp(lock.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                    }
                )

        stale_servers = [entry for entry in server_markers if entry.get("stale")]
        wake_breakers = read_json(
            self.wake_breaker_path,
            {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}},
        )
        breaker_sessions = wake_breakers.get("sessions", {})
        open_breakers = {
            session_id: data
            for session_id, data in breaker_sessions.items()
            if (data or {}).get("breaker_state") == "open"
        }
        tool_refresh = {
            "path": str(tool_refresh_status_path),
            "status": "missing",
            "refresh_required": False,
            "data": None,
        }
        if tool_refresh_status_path.exists():
            try:
                tool_refresh["data"] = read_json(tool_refresh_status_path, {})
                tool_refresh["refresh_required"] = bool((tool_refresh["data"] or {}).get("refresh_required"))
                tool_refresh["status"] = "refresh_required" if tool_refresh["refresh_required"] else "current"
            except Exception:
                tool_refresh["status"] = "unreadable"
        status = "healthy"
        if (
            watcher.get("stale")
            or stale_servers
            or lock_entries
            or tool_refresh["refresh_required"]
            or tool_refresh["status"] == "unreadable"
            or open_breakers
        ):
            status = "attention"
        return BridgeResult(
            True,
            status,
            "Bridge process status: watcher=%s, server_markers=%d, stale_server_markers=%d."
            % ("running" if watcher.get("running") else "not_running", len(server_markers), len(stale_servers)),
            {
                "watcher": watcher,
                "mcp_server_markers": server_markers,
                "mcp_server_marker_count": len(server_markers),
                "stale_server_marker_count": len(stale_servers),
                "wake_breakers": {
                    "path": str(self.wake_breaker_path),
                    "session_count": len(breaker_sessions),
                    "open_session_count": len(open_breakers),
                    "sessions": open_breakers,
                },
                "tool_refresh": tool_refresh,
                "locks": lock_entries,
                "lock_count": len(lock_entries),
            },
        )

    def pause_bridge(self) -> BridgeResult:
        with self._locked():
            state = self._load_state()
            state["paused"] = True
            self._save_state(state)
            self._audit({"id": str(uuid.uuid4()), "timestamp": utc_now(), "action": "pause_bridge", "accepted": True})
            return BridgeResult(True, "paused", "Bridge paused.")

    def resume_bridge(self) -> BridgeResult:
        with self._locked():
            state = self._load_state()
            state["paused"] = False
            self._save_state(state)
            self._audit({"id": str(uuid.uuid4()), "timestamp": utc_now(), "action": "resume_bridge", "accepted": True})
            return BridgeResult(True, "active", "Bridge resumed.")

    def clear_inbox(self, agent: Optional[str] = None, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            session = normalize_session(session_id)
            if session == DEFAULT_SESSION_ID:
                return BridgeResult(False, "rejected", "routing error: 'default' is deprecated; use an explicit bucket")
            targets = [normalize_agent(agent)] if agent else sorted(AGENTS)
            cleared = 0
            for target in targets:
                path = self.inbox_path(target)
                rows = read_jsonl(path)
                kept = [row for row in rows if row.get("session_id") != session]
                cleared += len(rows) - len(kept)
                write_jsonl(path, kept)
            state = self._load_state()
            state.setdefault("sessions", {}).pop(session, None)
            self._save_state(state)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "clear_inbox",
                    "agent": agent or "all",
                    "session_id": session,
                    "accepted": True,
                    "cleared": cleared,
                    "reset_session": True,
                }
            )
            return BridgeResult(
                True,
                "cleared",
                "Cleared %d inbox message(s) and reset session %s." % (cleared, session),
                {"cleared": cleared, "reset_session": True},
            )

    def clear_bucket(self, bucket: str, agent: Optional[str] = None) -> BridgeResult:
        """Explicitly clear one named bucket; compatibility wrapper over clear_inbox."""
        return self.clear_inbox(agent=agent, session_id=bucket)

    def reset_session(self, agent_or_session_id: Optional[str] = None, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            session = normalize_session(session_id if session_id is not None else agent_or_session_id)
            if session == DEFAULT_SESSION_ID:
                return BridgeResult(False, "rejected", "routing error: 'default' is deprecated; use an explicit bucket")
            state = self._load_state()
            removed = state.setdefault("sessions", {}).pop(session, None) is not None
            self._save_state(state)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "reset_session",
                    "session_id": session,
                    "accepted": True,
                    "removed_existing": removed,
                }
            )
            return BridgeResult(True, "reset", "Reset bridge session %s." % session, {"removed_existing": removed})

    def reset_bucket(self, bucket: str) -> BridgeResult:
        """Explicitly reset one named bucket; compatibility wrapper over reset_session."""
        return self.reset_session(session_id=bucket)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", required=True, help="Directory for bridge runtime state.")
    parser.add_argument("--max-hops", type=int, default=DEFAULT_MAX_HOPS, help="Maximum accepted relays per session.")
