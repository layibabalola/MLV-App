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
WAKE_BREAKER_BYPASS_COOLDOWN_S = 60
BREAKER_BYPASS_STALE_MIN = 15
WAKE_PREFIRE_LIMIT = 2
WAKE_PREFIRE_WINDOW_S = 10
EXECUTION_STATE_SCHEMA_VERSION = 1
EXECUTION_PROOF_TIMEOUT_S = 120
NON_ACTIONABLE_PENDING_EXECUTION_STATES = {"blocked", "parked", "displaced", "completed"}
CROSS_PROJECT_PAIR_SCHEMA_VERSION = 1
CROSS_PROJECT_PENDING_SCHEMA_VERSION = 1
CROSS_PROJECT_NONCE_WINDOW_S = 60
CROSS_PROJECT_DEFAULT_TTL_MINUTES = 120
CROSS_PROJECT_MAX_TTL_MINUTES = 24 * 60
CROSS_PROJECT_READ_AND_ADVISE = "read_and_advise"
CROSS_PROJECT_WRITE_WITH_CONFIRMATION = "write_with_confirmation"
CROSS_PROJECT_PERMISSION_TIERS = {CROSS_PROJECT_READ_AND_ADVISE, CROSS_PROJECT_WRITE_WITH_CONFIRMATION}
CROSS_PROJECT_ROLES = {"advisor", "executor"}
CROSS_PROJECT_WARNING = "You are about to pair threads from different projects. Are you sure?"
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


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
    def execution_state_path(self) -> Path:
        return self.state_dir / "execution-state.json"

    @property
    def cross_project_pairs_dir(self) -> Path:
        return self.state_dir / "cross-project-pairs"

    @property
    def cross_project_pending_path(self) -> Path:
        return self.cross_project_pairs_dir / "_pending.json"

    @property
    def session_registry_path(self) -> Path:
        return session_registry_path_for_state_dir(self.state_dir)

    @property
    def wake_breaker_path(self) -> Path:
        return self.state_dir / "wake-failure-windows.json"

    @property
    def watcher_state_path(self) -> Path:
        return self.state_dir.parent / "watcher-state.json"

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

    def _default_execution_state(self) -> Dict[str, Any]:
        return {
            "schema_version": EXECUTION_STATE_SCHEMA_VERSION,
            "owners": {},
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
                action.setdefault("active_execution_task_id", None)
                action.setdefault("execution_state", None)
                action.setdefault("execution_updated_at", None)
                normalized.append(action)
            pending["actions"] = normalized
        return pending

    def _save_pending_actions(self, pending: Dict[str, Any]) -> None:
        pending["updated_at"] = utc_now()
        write_json(self.pending_actions_path, pending)

    def _load_execution_state(self) -> Dict[str, Any]:
        try:
            payload = read_json(self.execution_state_path, self._default_execution_state())
        except (JSONDecodeError, OSError):
            corrupt_path = self.execution_state_path.with_name(
                "execution-state.corrupt.%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            if self.execution_state_path.exists():
                self.execution_state_path.replace(corrupt_path)
            payload = self._default_execution_state()
        payload["schema_version"] = max(
            int(payload.get("schema_version") or 1),
            EXECUTION_STATE_SCHEMA_VERSION,
        )
        owners = payload.get("owners")
        if not isinstance(owners, dict):
            owners = {}
            payload["owners"] = owners
        normalized: Dict[str, Any] = {}
        for owner, record in owners.items():
            if not isinstance(record, dict):
                continue
            entry = dict(record)
            entry["active_task"] = dict(entry.get("active_task") or {}) or None
            recent = entry.get("recent_tasks")
            if not isinstance(recent, list):
                recent = []
            entry["recent_tasks"] = [dict(task) for task in recent if isinstance(task, dict)][-20:]
            entry.setdefault("updated_at", utc_now())
            normalized[str(owner)] = entry
        payload["owners"] = normalized
        return payload

    def _save_execution_state(self, payload: Dict[str, Any]) -> None:
        payload["updated_at"] = utc_now()
        write_json(self.execution_state_path, payload)

    def _default_cross_project_pending(self) -> Dict[str, Any]:
        return {
            "schema_version": CROSS_PROJECT_PENDING_SCHEMA_VERSION,
            "observations": [],
            "used_nonce_hashes": [],
            "updated_at": utc_now(),
        }

    def _load_cross_project_pending(self) -> Dict[str, Any]:
        try:
            pending = read_json(self.cross_project_pending_path, self._default_cross_project_pending())
        except (JSONDecodeError, OSError):
            corrupt_path = self.cross_project_pending_path.with_name(
                "_pending.corrupt.%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            if self.cross_project_pending_path.exists():
                self.cross_project_pending_path.replace(corrupt_path)
            pending = self._default_cross_project_pending()
        pending["schema_version"] = max(
            int(pending.get("schema_version") or 1),
            CROSS_PROJECT_PENDING_SCHEMA_VERSION,
        )
        if not isinstance(pending.get("observations"), list):
            pending["observations"] = []
        if not isinstance(pending.get("used_nonce_hashes"), list):
            pending["used_nonce_hashes"] = []
        return pending

    def _save_cross_project_pending(self, pending: Dict[str, Any]) -> None:
        pending["updated_at"] = utc_now()
        self.cross_project_pairs_dir.mkdir(parents=True, exist_ok=True)
        write_json(self.cross_project_pending_path, pending)

    def _hash_cross_project_nonce(self, nonce: str) -> str:
        return sha256_text("cross-project-pair\n%s" % nonce)

    def _normalize_cross_project_role(self, role: str) -> str:
        value = (role or "").strip().lower()
        if value not in CROSS_PROJECT_ROLES:
            raise ValueError("role must be one of: advisor, executor")
        return value

    def _normalize_cross_project_permission(self, permission_tier: Optional[str]) -> str:
        value = (permission_tier or CROSS_PROJECT_READ_AND_ADVISE).strip().lower()
        if value not in CROSS_PROJECT_PERMISSION_TIERS:
            raise ValueError(
                "permission_tier must be one of: %s"
                % ", ".join(sorted(CROSS_PROJECT_PERMISSION_TIERS))
            )
        return value

    def _normalize_cross_project_link_id(self, link_id: str) -> str:
        value = (link_id or "").strip()
        if not value:
            raise ValueError("link_id is required")
        if len(value) > 96:
            raise ValueError("link_id must be 96 characters or fewer")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError("link_id may only contain letters, numbers, dot, colon, dash, and underscore")
        return value

    def _bounded_cross_project_ttl(self, ttl_minutes: int) -> int:
        if isinstance(ttl_minutes, bool) or not isinstance(ttl_minutes, int):
            raise ValueError("ttl_minutes must be an integer")
        if ttl_minutes < 1 or ttl_minutes > CROSS_PROJECT_MAX_TTL_MINUTES:
            raise ValueError("ttl_minutes must be between 1 and %d" % CROSS_PROJECT_MAX_TTL_MINUTES)
        return ttl_minutes

    def _cross_project_link_path(self, link_id: str) -> Path:
        return self.cross_project_pairs_dir / ("%s.json" % self._normalize_cross_project_link_id(link_id))

    def _load_cross_project_link(self, link_id: str) -> Optional[Dict[str, Any]]:
        path = self._cross_project_link_path(link_id)
        if not path.exists():
            return None
        try:
            payload = read_json(path, {})
        except (JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _save_cross_project_link(self, link: Dict[str, Any]) -> None:
        link_id = self._normalize_cross_project_link_id(str(link.get("link_id") or ""))
        link["updated_at"] = utc_now()
        self.cross_project_pairs_dir.mkdir(parents=True, exist_ok=True)
        write_json(self._cross_project_link_path(link_id), link)

    def _cross_project_link_status(self, link: Dict[str, Any], now_dt: Optional[datetime] = None) -> str:
        status = str(link.get("status") or "unknown")
        if status != "active":
            return status
        expires_at = parse_iso_datetime(link.get("expires_at"))
        if expires_at and expires_at <= (now_dt or datetime.now(timezone.utc)):
            return "expired"
        return "active"

    def _cross_project_side_for_project(self, link: Dict[str, Any], project: str) -> Optional[str]:
        advisor_project = (link.get("advisor") or {}).get("project")
        executor_project = (link.get("executor") or {}).get("project")
        if project == advisor_project:
            return "advisor"
        if project == executor_project:
            return "executor"
        return None

    def _cross_project_link_records(self) -> List[Dict[str, Any]]:
        if not self.cross_project_pairs_dir.exists():
            return []
        records: List[Dict[str, Any]] = []
        for path in sorted(self.cross_project_pairs_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            try:
                payload = read_json(path, {})
            except (JSONDecodeError, OSError):
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def _active_cross_project_link_between(
        self,
        from_project: str,
        to_project: str,
        now_dt: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        for link in self._cross_project_link_records():
            if self._cross_project_link_status(link, now_dt=now_dt) != "active":
                continue
            advisor_project = (link.get("advisor") or {}).get("project")
            executor_project = (link.get("executor") or {}).get("project")
            projects = {advisor_project, executor_project}
            if from_project in projects and to_project in projects and from_project != to_project:
                return link
        return None

    def _prune_cross_project_pending(self, pending: Dict[str, Any], now_dt: datetime) -> None:
        kept: List[Dict[str, Any]] = []
        for observation in pending.get("observations", []):
            if not isinstance(observation, dict):
                continue
            expires_at = parse_iso_datetime(observation.get("expires_at"))
            if expires_at and expires_at <= now_dt:
                continue
            kept.append(observation)
        pending["observations"] = kept
        pending["used_nonce_hashes"] = list(dict.fromkeys(str(item) for item in pending.get("used_nonce_hashes", [])))[-200:]

    def _owner_execution_record(self, payload: Dict[str, Any], owner: str) -> Dict[str, Any]:
        owners = payload.setdefault("owners", {})
        record = owners.setdefault(
            owner,
            {
                "active_task": None,
                "recent_tasks": [],
                "updated_at": utc_now(),
            },
        )
        record.setdefault("active_task", None)
        recent = record.setdefault("recent_tasks", [])
        if not isinstance(recent, list):
            record["recent_tasks"] = []
        record.setdefault("updated_at", utc_now())
        return record

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
                "schema_version": 2,
                "id": message_id,
                "created_at": now,
                "session_id": session_id,
                "from_session_id": None,
                "to_session_id": session_id,
                "from_session_id_kind": "unknown",
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
                row["schema_version"] = max(2, int(row.get("schema_version") or 1))
                row["session_id"] = project_name
                row["to_session_id"] = project_name
                row.setdefault("from_session_id", None)
                row.setdefault("from_session_id_kind", "unknown")
                row["inbox_level"] = INBOX_LEVEL_PROJECT
                row["parent_project"] = project_name
                row["promoted_from"] = superseded_session
                row["promoted_at"] = now
                row["superseded_bucket_at"] = now
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
                if drained_messages:
                    self._audit(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": utc_now(),
                            "action": "bootstrap_rotation_routed_messages",
                            "accepted": True,
                            "agent": owner,
                            "from_session_id": previous_local,
                            "to_session_id": project_name,
                            "project": project_name,
                            "count": len(drained_messages),
                            "mode": "promote_to_project_bucket",
                        }
                    )
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

    def _routing_buckets_for_agent(self, registry: Dict[str, Any], agent: str) -> Dict[str, Any]:
        valid = {agent}
        active_targets: List[Dict[str, str]] = []
        project_targets: List[str] = []
        for project_name, project_entry in (registry.get("projects") or {}).items():
            valid.add(project_name)
            project_targets.append(project_name)
            active_session = (project_entry.get("active") or {}).get(agent)
            if active_session:
                active_targets.append({"project": project_name, "session_id": str(active_session)})
            for session_id, record in (project_entry.get("sessions") or {}).items():
                if not isinstance(record, dict) or record.get("agent") != agent:
                    continue
                if record.get("status") in {"active", "secondary", "superseded"}:
                    valid.add(str(session_id))
        if active_targets:
            target = active_targets[0]
            return {
                "valid": valid,
                "target_session_id": target["session_id"],
                "target_inbox_level": INBOX_LEVEL_SESSION,
                "target_parent_project": target["project"],
            }
        fallback_project = project_targets[0] if project_targets else DEFAULT_PROJECT
        valid.add(fallback_project)
        return {
            "valid": valid,
            "target_session_id": fallback_project,
            "target_inbox_level": INBOX_LEVEL_PROJECT,
            "target_parent_project": fallback_project,
        }

    def truedup_session_routing(self, agent: str, dry_run: bool = True, mode: str = "rekey") -> BridgeResult:
        with self._locked():
            try:
                target = normalize_agent(agent)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            action_mode = (mode or "rekey").strip().lower()
            if action_mode not in {"rekey", "quarantine"}:
                return BridgeResult(False, "rejected", "mode must be 'rekey' or 'quarantine'")

            registry = self._load_session_registry()
            routing = self._routing_buckets_for_agent(registry, target)
            valid_buckets = routing["valid"]
            target_bucket = routing["target_session_id"]
            inbox = self.inbox_path(target)
            rows = read_jsonl(inbox)
            now = utc_now()
            orphans = []
            for index, row in enumerate(rows):
                bucket = str(row.get("session_id") or "")
                if bucket in valid_buckets:
                    continue
                orphans.append(
                    {
                        "index": index,
                        "id": row.get("id"),
                        "session_id": bucket,
                        "from": row.get("from"),
                        "to": row.get("to"),
                        "control_type": row.get("control_type"),
                        "target_session_id": target_bucket,
                    }
                )

            if dry_run:
                return BridgeResult(
                    True,
                    "dry_run",
                    "Found %d orphaned routing row(s) for %s." % (len(orphans), target),
                    {"agent": target, "mode": action_mode, "dry_run": True, "orphans": orphans, "count": len(orphans)},
                )

            touched = []
            if action_mode == "rekey":
                for item in orphans:
                    row = rows[item["index"]]
                    old_bucket = str(row.get("session_id") or "")
                    row["schema_version"] = 2
                    row["session_id"] = target_bucket
                    row["to_session_id"] = target_bucket
                    row.setdefault("from_session_id", None)
                    row.setdefault("from_session_id_kind", "unknown")
                    row["inbox_level"] = routing["target_inbox_level"]
                    row["parent_project"] = routing["target_parent_project"]
                    row["session_truedup_from"] = old_bucket
                    row["session_truedup_at"] = now
                    row["escalated_from"] = old_bucket
                    row["escalation_reason"] = "session_truedup_rekey"
                    touched.append({"id": row.get("id"), "from_session_id": old_bucket, "to_session_id": target_bucket})
                    self._audit(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": now,
                            "action": "session_truedup_rekeyed",
                            "accepted": True,
                            "agent": target,
                            "message_id": row.get("id"),
                            "from_session_id": old_bucket,
                            "to_session_id": target_bucket,
                        }
                    )
                if orphans:
                    write_jsonl(inbox, rows)
            else:
                orphan_path = inbox.with_suffix(".orphan.jsonl")
                orphan_indexes = {item["index"] for item in orphans}
                kept = []
                for index, row in enumerate(rows):
                    if index not in orphan_indexes:
                        kept.append(row)
                        continue
                    old_bucket = str(row.get("session_id") or "")
                    moved = dict(row)
                    moved["orphaned_at"] = now
                    moved["session_truedup_from"] = old_bucket
                    moved["session_truedup_at"] = now
                    append_jsonl(orphan_path, moved)
                    touched.append({"id": row.get("id"), "from_session_id": old_bucket, "orphan_path": str(orphan_path)})
                    self._audit(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": now,
                            "action": "session_truedup_quarantined",
                            "accepted": True,
                            "agent": target,
                            "message_id": row.get("id"),
                            "from_session_id": old_bucket,
                            "orphan_path": str(orphan_path),
                        }
                    )
                if orphans:
                    write_jsonl(inbox, kept)

            return BridgeResult(
                True,
                "applied",
                "Applied %s truedup to %d row(s) for %s." % (action_mode, len(touched), target),
                {"agent": target, "mode": action_mode, "dry_run": False, "count": len(touched), "touched": touched},
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

    def cross_pair_init(
        self,
        agent: str,
        project: str,
        peer_project: str,
        role: str,
        nonce: str,
        session_id: Optional[str] = None,
        confirm_different_projects: bool = False,
        ttl_minutes: int = CROSS_PROJECT_DEFAULT_TTL_MINUTES,
        requested_permission_tier: str = CROSS_PROJECT_READ_AND_ADVISE,
    ) -> BridgeResult:
        """Manually establish a nonce-matched cross-project advisory link.

        This is intentionally not automatic. Both project chats must call this
        with the same nonce, inverse projects, and opposite roles within the
        short nonce window. The active link always starts as read_and_advise.
        """
        now = utc_now()
        now_dt = parse_iso_datetime(now) or datetime.now(timezone.utc)
        try:
            owner = normalize_agent(agent)
            project_name = normalize_project(project)
            peer_name = normalize_project(peer_project)
            side = self._normalize_cross_project_role(role)
            ttl = self._bounded_cross_project_ttl(ttl_minutes)
            requested_permission = self._normalize_cross_project_permission(requested_permission_tier)
            session = normalize_session(session_id) if session_id is not None else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))

        nonce_value = (nonce or "").strip()
        if len(nonce_value) < 8:
            return BridgeResult(False, "rejected", "nonce must be at least 8 characters")
        if project_name == peer_name:
            return BridgeResult(False, "rejected", "cross-project pairing requires two different projects")
        if not confirm_different_projects:
            return BridgeResult(
                False,
                "confirmation_required",
                CROSS_PROJECT_WARNING,
                {
                    "warning": CROSS_PROJECT_WARNING,
                    "required_parameter": "confirm_different_projects=true",
                    "project": project_name,
                    "peer_project": peer_name,
                    "default_permission_tier": CROSS_PROJECT_READ_AND_ADVISE,
                    "mode": "manual_only",
                },
            )

        nonce_hash = self._hash_cross_project_nonce(nonce_value)
        with self._locked():
            registry = self._load_session_registry()
            projects = registry.get("projects") or {}
            if project_name not in projects or peer_name not in projects:
                return BridgeResult(
                    False,
                    "rejected",
                    "both projects must be bootstrapped before cross-project pairing",
                    {"project": project_name, "peer_project": peer_name},
                )

            pending = self._load_cross_project_pending()
            self._prune_cross_project_pending(pending, now_dt)
            if nonce_hash in set(str(item) for item in pending.get("used_nonce_hashes", [])):
                self._save_cross_project_pending(pending)
                return BridgeResult(False, "rejected", "nonce has already been used for cross-project pairing")

            observation = {
                "id": str(uuid.uuid4()),
                "created_at": now,
                "expires_at": (now_dt + timedelta(seconds=CROSS_PROJECT_NONCE_WINDOW_S)).isoformat(timespec="seconds"),
                "nonce_hash": nonce_hash,
                "agent": owner,
                "session_id": session,
                "project": project_name,
                "peer_project": peer_name,
                "role": side,
                "ttl_minutes": ttl,
                "requested_permission_tier": requested_permission,
                "requested_permission_ignored": requested_permission != CROSS_PROJECT_READ_AND_ADVISE,
                "bridge_root": str(self.state_dir.parent),
            }

            observations = [
                item for item in pending.get("observations", [])
                if not (
                    isinstance(item, dict)
                    and item.get("nonce_hash") == nonce_hash
                    and item.get("project") == project_name
                    and item.get("role") == side
                )
            ]
            opposite = [
                item for item in observations
                if isinstance(item, dict)
                and item.get("nonce_hash") == nonce_hash
                and item.get("project") == peer_name
                and item.get("peer_project") == project_name
            ]
            same_role = [item for item in opposite if item.get("role") == side]
            if same_role:
                pending["observations"] = observations
                self._save_cross_project_pending(pending)
                return BridgeResult(False, "rejected", "matching nonce was observed with the same role; use advisor/executor")

            match = next((item for item in opposite if item.get("role") != side), None)
            if not match:
                observations.append(observation)
                pending["observations"] = observations
                self._save_cross_project_pending(pending)
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": now,
                        "action": "cross_project_pair_pending",
                        "agent": owner,
                        "session_id": session,
                        "project": project_name,
                        "peer_project": peer_name,
                        "role": side,
                        "nonce_hash": nonce_hash,
                        "accepted": True,
                    }
                )
                return BridgeResult(
                    True,
                    "pending",
                    "Recorded one side of cross-project pairing; waiting for peer project to confirm with matching nonce.",
                    {
                        "project": project_name,
                        "peer_project": peer_name,
                        "role": side,
                        "nonce_window_seconds": CROSS_PROJECT_NONCE_WINDOW_S,
                        "warning": CROSS_PROJECT_WARNING,
                    },
                )

            pair = [match, observation]
            advisor = next(item for item in pair if item.get("role") == "advisor")
            executor = next(item for item in pair if item.get("role") == "executor")
            ttl_final = min(int(advisor.get("ttl_minutes") or ttl), int(executor.get("ttl_minutes") or ttl), ttl)
            link_id = "xpair-%s" % sha256_text(
                "%s\n%s\n%s" % (advisor.get("project"), executor.get("project"), nonce_hash)
            )[:20]
            link = {
                "schema_version": CROSS_PROJECT_PAIR_SCHEMA_VERSION,
                "link_id": link_id,
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "expires_at": (now_dt + timedelta(minutes=ttl_final)).isoformat(timespec="seconds"),
                "nonce_hash": nonce_hash,
                "permission_tier": CROSS_PROJECT_READ_AND_ADVISE,
                "mode": "manual_cross_project",
                "warning_confirmed": True,
                "advisor": {
                    "project": advisor.get("project"),
                    "agent": advisor.get("agent"),
                    "session_id": advisor.get("session_id"),
                },
                "executor": {
                    "project": executor.get("project"),
                    "agent": executor.get("agent"),
                    "session_id": executor.get("session_id"),
                },
                "policy": {
                    "communication_only": True,
                    "advisor_default": "read-only advice; executor owns writes",
                    "write_override_requires_executor_confirmation": True,
                },
            }
            pending["observations"] = [
                item for item in observations
                if not (isinstance(item, dict) and item.get("nonce_hash") == nonce_hash)
            ]
            used = list(pending.get("used_nonce_hashes", []))
            used.append(nonce_hash)
            pending["used_nonce_hashes"] = used[-200:]
            self._save_cross_project_pending(pending)
            self._save_cross_project_link(link)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "cross_project_pair_activated",
                    "link_id": link_id,
                    "advisor_project": advisor.get("project"),
                    "executor_project": executor.get("project"),
                    "permission_tier": CROSS_PROJECT_READ_AND_ADVISE,
                    "expires_at": link["expires_at"],
                    "accepted": True,
                    "projects": [advisor.get("project"), executor.get("project")],
                }
            )
            return BridgeResult(
                True,
                "active",
                "Activated cross-project pairing %s in read_and_advise mode." % link_id,
                {"link": link},
            )

    def list_cross_project_links(
        self,
        project: Optional[str] = None,
        include_inactive: bool = False,
    ) -> BridgeResult:
        try:
            project_name = normalize_project(project) if project else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        now_dt = datetime.now(timezone.utc)
        with self._locked():
            links: List[Dict[str, Any]] = []
            for link in self._cross_project_link_records():
                derived_status = self._cross_project_link_status(link, now_dt=now_dt)
                if project_name and self._cross_project_side_for_project(link, project_name) is None:
                    continue
                if not include_inactive and derived_status != "active":
                    continue
                item = copy.deepcopy(link)
                item["derived_status"] = derived_status
                if project_name:
                    item["local_role"] = self._cross_project_side_for_project(link, project_name)
                links.append(item)
        return BridgeResult(
            True,
            "cross_project_links",
            "Found %d cross-project link(s)." % len(links),
            {"count": len(links), "links": links},
        )

    def cross_pair_promote(
        self,
        link_id: str,
        project: str,
        permission_tier: str,
        agent: str,
        session_id: Optional[str] = None,
        confirm_write_override: bool = False,
    ) -> BridgeResult:
        try:
            link = self._normalize_cross_project_link_id(link_id)
            project_name = normalize_project(project)
            owner = normalize_agent(agent)
            permission = self._normalize_cross_project_permission(permission_tier)
            session = normalize_session(session_id) if session_id is not None else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        with self._locked():
            payload = self._load_cross_project_link(link)
            if not payload:
                return BridgeResult(False, "not_found", "cross-project link %s was not found" % link)
            if self._cross_project_link_status(payload) != "active":
                return BridgeResult(False, "rejected", "cross-project link %s is not active" % link)
            side = self._cross_project_side_for_project(payload, project_name)
            if side != "executor":
                return BridgeResult(False, "rejected", "only the executor project can promote cross-project permissions")
            if permission == CROSS_PROJECT_WRITE_WITH_CONFIRMATION and not confirm_write_override:
                return BridgeResult(
                    False,
                    "confirmation_required",
                    "Write override requires explicit executor confirmation.",
                    {
                        "required_parameter": "confirm_write_override=true",
                        "permission_tier": permission,
                        "link_id": link,
                    },
                )
            payload["permission_tier"] = permission
            payload["permission_updated_at"] = utc_now()
            payload["permission_updated_by_project"] = project_name
            payload["permission_updated_by_agent"] = owner
            payload["permission_updated_by_session"] = session
            self._save_cross_project_link(payload)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": payload["permission_updated_at"],
                    "action": "cross_project_pair_permission_updated",
                    "link_id": link,
                    "project": project_name,
                    "agent": owner,
                    "session_id": session,
                    "permission_tier": permission,
                    "accepted": True,
                }
            )
            return BridgeResult(
                True,
                "updated",
                "Updated cross-project link %s permission to %s." % (link, permission),
                {"link": payload},
            )

    def cross_pair_revoke(
        self,
        link_id: str,
        project: str,
        agent: str,
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> BridgeResult:
        try:
            link = self._normalize_cross_project_link_id(link_id)
            project_name = normalize_project(project)
            owner = normalize_agent(agent)
            session = normalize_session(session_id) if session_id is not None else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        with self._locked():
            payload = self._load_cross_project_link(link)
            if not payload:
                return BridgeResult(False, "not_found", "cross-project link %s was not found" % link)
            side = self._cross_project_side_for_project(payload, project_name)
            if side is None:
                return BridgeResult(False, "rejected", "project is not part of cross-project link %s" % link)
            if payload.get("status") == "revoked":
                return BridgeResult(True, "already_revoked", "cross-project link %s is already revoked" % link, {"link": payload})
            revoked_at = utc_now()
            payload["status"] = "revoked"
            payload["revoked_at"] = revoked_at
            payload["revoked_by_project"] = project_name
            payload["revoked_by_agent"] = owner
            payload["revoked_by_session"] = session
            payload["revocation_reason"] = (reason or "").strip() or None
            self._save_cross_project_link(payload)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": revoked_at,
                    "action": "cross_project_pair_revoked",
                    "link_id": link,
                    "project": project_name,
                    "agent": owner,
                    "session_id": session,
                    "reason": payload["revocation_reason"],
                    "accepted": True,
                }
            )
            return BridgeResult(True, "revoked", "Revoked cross-project link %s." % link, {"link": payload})

    def send_cross_project_message(
        self,
        link_id: str,
        from_project: str,
        from_agent: str,
        to_agent: str,
        message: str,
    ) -> BridgeResult:
        try:
            link = self._normalize_cross_project_link_id(link_id)
            source_project = normalize_project(from_project)
            sender = normalize_agent(from_agent)
            target = normalize_agent(to_agent)
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        clean_body = strip_markers(message).get("body", "").strip()
        if not clean_body:
            return BridgeResult(False, "rejected", "message is empty after stripping bridge markers")

        with self._locked():
            payload = self._load_cross_project_link(link)
            if not payload:
                return BridgeResult(False, "not_found", "cross-project link %s was not found" % link)
            derived_status = self._cross_project_link_status(payload)
            if derived_status != "active":
                return BridgeResult(False, "rejected", "cross-project link %s is %s" % (link, derived_status))
            side = self._cross_project_side_for_project(payload, source_project)
            if side is None:
                return BridgeResult(False, "rejected", "from_project is not part of cross-project link %s" % link)
            target_project = (
                (payload.get("executor") or {}).get("project")
                if side == "advisor"
                else (payload.get("advisor") or {}).get("project")
            )
            permission = str(payload.get("permission_tier") or CROSS_PROJECT_READ_AND_ADVISE)
            policy = copy.deepcopy(payload.get("policy") or {})

        role_policy = (
            "communication-only; advisor is read-only/read-and-advise by default; "
            "executor owns writes unless it explicitly grants a write override"
        )
        wrapped = (
            "[[handoff:%s]] TYPE: CROSS_PROJECT_MESSAGE\n"
            "LINK_ID: %s\n"
            "FROM_PROJECT: %s\n"
            "TO_PROJECT: %s\n"
            "FROM_ROLE: %s\n"
            "PERMISSION_TIER: %s\n"
            "ROLE_POLICY: %s\n\n"
            "%s"
        ) % (target, link, source_project, target_project, side, permission, role_policy, clean_body)
        queued = self.send_to_peer(sender, target, wrapped, target_session_id=target_project)
        with self._locked():
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "cross_project_message_sent",
                    "link_id": link,
                    "from_project": source_project,
                    "to_project": target_project,
                    "from_agent": sender,
                    "to_agent": target,
                    "permission_tier": permission,
                    "policy": policy,
                    "accepted": queued.ok,
                    "reason": queued.status if queued.ok else queued.message,
                    "queued_message_id": queued.data.get("id") if queued.ok else None,
                }
            )
        if not queued.ok:
            return queued
        data = dict(queued.data)
        data.update(
            {
                "link_id": link,
                "from_project": source_project,
                "to_project": target_project,
                "permission_tier": permission,
            }
        )
        return BridgeResult(
            True,
            "queued",
            "Queued cross-project message for %s via link %s." % (target_project, link),
            data,
        )

    def send_to_peer(
        self,
        from_agent: str,
        to_agent: str,
        message: str,
        session_id: Optional[str] = None,
        target_session_id: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            now = utc_now()
            # If no session specified, route through the from_agent's active
            # project session so backpressure is per-project rather than the
            # shared global "default" bucket.
            if session_id and target_session_id:
                try:
                    old_session = normalize_session(session_id)
                    new_session = normalize_session(target_session_id)
                except ValueError as exc:
                    return BridgeResult(False, "rejected", str(exc))
                if old_session != new_session:
                    return BridgeResult(
                        False,
                        "rejected",
                        "session_id and target_session_id disagree; use target_session_id only",
                    )
            deprecated_session_param_used = bool(session_id and not target_session_id)
            resolved_session = target_session_id or session_id or self._resolve_default_session(from_agent)
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
                "deprecated_session_id_param_used": deprecated_session_param_used,
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
            requested_bucket_info = self._bucket_info(registry, session)
            requested_record = requested_bucket_info.get("record")
            if (
                requested_bucket_info.get("inbox_level") == INBOX_LEVEL_SESSION
                and isinstance(requested_record, dict)
                and requested_record.get("agent") != target
            ):
                owner = str(requested_record.get("agent") or "unknown")
                status = str(requested_record.get("status") or "unknown")
                return reject(
                    "session_id %s belongs to %s session (%s); send_to_peer session_id selects the receiver bucket for %s"
                    % (session, owner, status, target)
                )
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
            sender_session_id = sender_context.sender_session_id if sender_context else None
            sender_origin = None
            if sender_session_id:
                sender_record_info = self._find_session_record(registry, sender_session_id, agent=sender)
                if sender_record_info:
                    sender_origin = sender_record_info["record"].get("bootstrap_origin")

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
                reason = "target %s already has one unread work message for session %s" % (target, delivery_bucket)
                event["backpressure_nudge"] = self._request_backpressure_nudge(
                    agent=target,
                    session=delivery_bucket,
                    reason="session_backpressure",
                )
                return reject(reason)
            if delivery_level == INBOX_LEVEL_PROJECT and len(unread_work) >= PROJECT_BACKPRESSURE_LIMIT:
                reason = (
                    "target %s project inbox %s is full (%d unread >= %d)"
                    % (target, delivery_bucket, len(unread_work), PROJECT_BACKPRESSURE_LIMIT)
                )
                event["backpressure_nudge"] = self._request_backpressure_nudge(
                    agent=target,
                    session=delivery_bucket,
                    reason="project_backpressure",
                )
                return reject(
                    reason
                )

            delivered = "From %s:\n%s" % (sender.capitalize(), body)
            inbox_row = {
                "schema_version": 2,
                "id": event["id"],
                "created_at": now,
                "session_id": delivery_bucket,
                "from_session_id": sender_session_id,
                "to_session_id": delivery_bucket,
                "from_session_id_kind": sender_origin or "unknown",
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
            matched_row: Optional[Dict[str, Any]] = None
            for row in rows:
                if row.get("id") == target_id and (session is None or row.get("session_id") == session):
                    matched = True
                    matched_row = row
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

            stale_bypass = None
            if matched_row is not None:
                created_at = parse_iso_datetime(matched_row.get("created_at"))
                row_session = str(matched_row.get("session_id") or "")
                if (
                    row_session
                    and created_at is not None
                    and (datetime.now(timezone.utc) - created_at) >= timedelta(minutes=BREAKER_BYPASS_STALE_MIN)
                ):
                    stale_bypass = self._grant_wake_breaker_bypass(
                        row_session,
                        reason="stale_mark_read",
                        granted_by=target,
                    )

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
                {"message_id": target_id, "changed": changed, "stale_bypass": stale_bypass},
            )

    def _normalize_wake_breaker_session(self, record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        data = dict(record or {})
        data.setdefault("failures", [])
        data.setdefault("breaker_state", "closed")
        data.setdefault("opened_at", None)
        data.setdefault("last_failure_at", None)
        data.setdefault("last_success_at", None)
        data.setdefault("exit_code_distribution", {})
        data.setdefault("notified_open_at", None)
        try:
            data["bypass_grants"] = max(0, int(data.get("bypass_grants") or 0))
        except (TypeError, ValueError):
            data["bypass_grants"] = 0
        data.setdefault("last_bypass_granted_at", None)
        data.setdefault("last_bypass_consumed_at", None)
        data.setdefault("last_bypass_reason", None)
        return data

    def _grant_wake_breaker_bypass(
        self,
        session: str,
        *,
        reason: str,
        granted_by: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        payload = read_json(self.wake_breaker_path, {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}})
        sessions = payload.setdefault("sessions", {})
        record = self._normalize_wake_breaker_session(sessions.get(session))
        if record.get("breaker_state") != "open":
            return {"granted": False, "status": "breaker_closed", "session_id": session}
        now_dt = datetime.now(timezone.utc)
        last_grant = parse_iso_datetime(record.get("last_bypass_granted_at"))
        if (
            not force
            and last_grant is not None
            and (now_dt - last_grant).total_seconds() < WAKE_BREAKER_BYPASS_COOLDOWN_S
        ):
            return {
                "granted": False,
                "status": "cooldown",
                "session_id": session,
                "cooldown_seconds": WAKE_BREAKER_BYPASS_COOLDOWN_S,
            }
        record["bypass_grants"] = int(record.get("bypass_grants") or 0) + 1
        record["last_bypass_granted_at"] = utc_now()
        record["last_bypass_reason"] = reason
        sessions[session] = record
        payload["schema_version"] = WAKE_BREAKER_SCHEMA_VERSION
        payload["updated_at"] = utc_now()
        write_json(self.wake_breaker_path, payload)
        self._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": utc_now(),
                "action": "wake_breaker_bypass_granted",
                "accepted": True,
                "session_id": session,
                "reason": reason,
                "granted_by": granted_by,
                "bypass_grants": record["bypass_grants"],
            }
        )
        return {
            "granted": True,
            "status": "granted",
            "session_id": session,
            "bypass_grants": record["bypass_grants"],
        }

    def _wake_breaker_open_for_session(self, session: str) -> bool:
        payload = read_json(self.wake_breaker_path, {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}})
        record = self._normalize_wake_breaker_session((payload.get("sessions") or {}).get(session))
        return record.get("breaker_state") == "open"

    def _wake_prefire_limited_for_session(self, session: str) -> bool:
        state = read_json(
            self.watcher_state_path,
            {"seen_ids": [], "pending_wake_verifications": [], "wake_fire_history": []},
        )
        now_dt = datetime.now(timezone.utc)
        recent = []
        for item in state.get("wake_fire_history", []):
            if str(item.get("session_id") or "") != session:
                continue
            fired_at = parse_iso_datetime(item.get("at"))
            if fired_at is not None and (now_dt - fired_at).total_seconds() <= WAKE_PREFIRE_WINDOW_S:
                recent.append(item)
        return len(recent) >= WAKE_PREFIRE_LIMIT

    def _request_backpressure_nudge(self, *, agent: str, session: str, reason: str) -> Dict[str, Any]:
        if self._wake_breaker_open_for_session(session):
            status = "backpressure_rejected_no_nudge_breaker_open"
            result = {"status": status, "session_id": session, "reason": "wake_breaker_open"}
        elif self._wake_prefire_limited_for_session(session):
            status = "backpressure_rejected_no_nudge_rate_limited"
            result = {"status": status, "session_id": session, "reason": "wake_rate_limited"}
        else:
            unread_work = [
                row for row in self._unread_for(agent, session)
                if row.get("marker_variant") != "control"
            ]
            if not unread_work:
                status = "backpressure_rejected_no_nudge_no_unread"
                result = {"status": status, "session_id": session, "reason": "no_unread_work"}
            else:
                message_id = str(unread_work[0].get("id") or "")
                watcher_state = read_json(
                    self.watcher_state_path,
                    {"seen_ids": [], "toasted_ids": [], "pending_wake_verifications": [], "paused_wake_messages": [], "unknown_origin_warnings": [], "wake_fire_history": []},
                )
                seen_ids = [str(item) for item in watcher_state.get("seen_ids", [])]
                changed = message_id in seen_ids
                if changed:
                    watcher_state["seen_ids"] = [item for item in seen_ids if item != message_id]
                    write_json(self.watcher_state_path, watcher_state)
                status = "backpressure_rejected_nudge_attempted"
                result = {
                    "status": status,
                    "session_id": session,
                    "message_id": message_id,
                    "wake_rearmed": changed,
                }
        self._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": utc_now(),
                "action": result["status"],
                "accepted": True,
                "agent": agent,
                "session_id": session,
                "trigger_reason": reason,
                **{k: v for k, v in result.items() if k not in {"status"}},
            }
        )
        return result

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

    def nudge_peer(self, agent: str, session_id: str) -> BridgeResult:
        with self._locked():
            try:
                target = normalize_agent(agent)
                session = normalize_session(session_id)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            if self._wake_breaker_open_for_session(session):
                grant = self._grant_wake_breaker_bypass(
                    session,
                    reason="nudge_peer",
                    granted_by=target,
                    force=False,
                )
                return BridgeResult(
                    True,
                    grant["status"],
                    "Wake bypass %s for %s." % (grant["status"], session),
                    {"agent": target, "session_id": session, "wake_bypass": grant},
                )
            nudge = self._request_backpressure_nudge(
                agent=target,
                session=session,
                reason="nudge_peer",
            )
            return BridgeResult(
                True,
                nudge["status"],
                "Wake nudge %s for %s." % (nudge["status"], session),
                {"agent": target, "session_id": session, "nudge": nudge},
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
                and str(action.get("execution_state") or "").strip().lower()
                not in NON_ACTIONABLE_PENDING_EXECUTION_STATES
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

    def _set_pending_action_execution_state(
        self,
        pending: Dict[str, Any],
        *,
        action_id: Optional[str],
        execution_state: Optional[str],
        task_id: Optional[str],
    ) -> None:
        if not action_id:
            return
        for action in pending.get("actions", []):
            if action.get("id") != action_id:
                continue
            action["active_execution_task_id"] = task_id
            action["execution_state"] = execution_state
            action["execution_updated_at"] = utc_now()
            break

    def _derive_execution_task(self, task: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not task:
            return None
        derived = copy.deepcopy(task)
        now = datetime.now(timezone.utc)
        status = str(derived.get("status") or "starting").strip().lower()
        proof_signals = derived.get("proof_signals") or []
        proof_deadline = str(derived.get("proof_deadline_at") or "").strip()
        eta_at = str(derived.get("eta_at") or "").strip()
        derived_status = status
        if status in {"starting", "active"} and not proof_signals and proof_deadline:
            try:
                if datetime.fromisoformat(proof_deadline) < now:
                    derived_status = "not_started"
            except ValueError:
                pass
        if status in {"starting", "active"} and eta_at:
            try:
                eta = datetime.fromisoformat(eta_at)
                if eta.tzinfo is None:
                    eta = eta.replace(tzinfo=timezone.utc)
                if eta < now:
                    derived_status = "timed_out"
            except ValueError:
                pass
        derived["derived_status"] = derived_status
        derived["resume_hint"] = "resume active task: %s %s" % (
            derived.get("id"),
            derived.get("summary"),
        )
        return derived

    def start_execution_task(
        self,
        owner_agent: str,
        summary: str,
        *,
        source: str,
        related_action_id: Optional[str] = None,
        message_id: Optional[str] = None,
        checkpoint: Optional[str] = None,
        eta_at: Optional[str] = None,
        allowed_interrupts: Optional[List[str]] = None,
        interrupt_mode: str = "task_switch",
        priority: Optional[str] = None,
        displaced_by: Optional[str] = None,
        displacement_reason: Optional[str] = None,
        prior_action_id: Optional[str] = None,
        prior_disposition: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            text = (summary or "").strip()
            if not text:
                return BridgeResult(False, "rejected", "summary is required")
            source_value = (source or "").strip().lower()
            if not source_value:
                return BridgeResult(False, "rejected", "source is required")
            interrupt_value = (interrupt_mode or "task_switch").strip().lower()
            if interrupt_value not in {"task_switch", "answer_only_interrupt"}:
                return BridgeResult(False, "rejected", "interrupt_mode must be task_switch or answer_only_interrupt")

            pending = self._load_pending_actions()
            payload = self._load_execution_state()
            record = self._owner_execution_record(payload, owner)
            active = record.get("active_task")
            now = utc_now()
            task_id = str(uuid.uuid4())

            if active:
                active_id = str(active.get("id") or "")
                if not displaced_by or not displacement_reason:
                    return BridgeResult(
                        False,
                        "active_task_exists",
                        "Active execution task %s exists for %s; displacement must be explicit." % (active_id, owner),
                        {"active_task": self._derive_execution_task(active)},
                    )
                displaced = copy.deepcopy(active)
                displaced["status"] = "displaced"
                displaced["displaced_at"] = now
                displaced["displaced_by"] = (displaced_by or "").strip() or None
                displaced["displacement_reason"] = (displacement_reason or "").strip() or None
                displaced["new_active_task_id"] = task_id
                record.setdefault("recent_tasks", []).append(displaced)
                self._set_pending_action_execution_state(
                    pending,
                    action_id=displaced.get("related_action_id"),
                    execution_state="displaced",
                    task_id=None,
                )

            task = {
                "id": task_id,
                "owner_agent": owner,
                "summary": text,
                "source": source_value,
                "related_action_id": (related_action_id or "").strip() or None,
                "message_id": (message_id or "").strip() or None,
                "priority": (priority or "").strip().lower() or None,
                "checkpoint": (checkpoint or "").strip() or None,
                "eta_at": (eta_at or "").strip() or None,
                "allowed_interrupts": list(allowed_interrupts or []),
                "interrupt_mode": interrupt_value,
                "status": "starting",
                "started_at": now,
                "updated_at": now,
                "proof_status": "awaiting",
                "proof_deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=EXECUTION_PROOF_TIMEOUT_S)).isoformat(timespec="seconds"),
                "proof_signals": [],
                "prior_action_id": (prior_action_id or "").strip() or None,
                "prior_disposition": (prior_disposition or "").strip() or None,
            }
            record["active_task"] = task
            record["updated_at"] = now
            self._set_pending_action_execution_state(
                pending,
                action_id=task.get("related_action_id"),
                execution_state="active",
                task_id=task_id,
            )
            self._save_execution_state(payload)
            self._save_pending_actions(pending)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "start_execution_task",
                    "owner_agent": owner,
                    "task_id": task_id,
                    "source": source_value,
                    "related_action_id": task.get("related_action_id"),
                    "accepted": True,
                }
            )
            return BridgeResult(True, "started", "Execution task %s started for %s." % (task_id, owner), {"task": self._derive_execution_task(task)})

    def record_execution_progress(
        self,
        owner_agent: str,
        task_id: str,
        signal_type: str,
        *,
        details: Optional[str] = None,
        checkpoint: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            signal = (signal_type or "").strip().lower()
            if not signal:
                return BridgeResult(False, "rejected", "signal_type is required")
            payload = self._load_execution_state()
            record = self._owner_execution_record(payload, owner)
            active = record.get("active_task")
            if not active or str(active.get("id") or "") != (task_id or "").strip():
                return BridgeResult(False, "not_found", "Active execution task %s not found for %s." % (task_id, owner))
            active.setdefault("proof_signals", []).append(
                {
                    "type": signal,
                    "details": (details or "").strip() or None,
                    "at": utc_now(),
                }
            )
            active["proof_status"] = "proved"
            active["status"] = "active"
            if checkpoint is not None:
                active["checkpoint"] = (checkpoint or "").strip() or None
            active["updated_at"] = utc_now()
            record["updated_at"] = utc_now()
            self._save_execution_state(payload)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "record_execution_progress",
                    "owner_agent": owner,
                    "task_id": task_id,
                    "signal_type": signal,
                    "accepted": True,
                }
            )
            return BridgeResult(True, "progress_recorded", "Execution progress recorded for %s." % task_id, {"task": self._derive_execution_task(active)})

    def complete_execution_task(
        self,
        owner_agent: str,
        task_id: str,
        *,
        outcome: str,
        resolution: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            outcome_value = (outcome or "").strip().lower()
            if outcome_value not in {"completed", "blocked", "parked", "timed_out", "displaced"}:
                return BridgeResult(False, "rejected", "outcome must be completed, blocked, parked, timed_out, or displaced")
            pending = self._load_pending_actions()
            payload = self._load_execution_state()
            record = self._owner_execution_record(payload, owner)
            active = record.get("active_task")
            if not active or str(active.get("id") or "") != (task_id or "").strip():
                return BridgeResult(False, "not_found", "Active execution task %s not found for %s." % (task_id, owner))
            closed = copy.deepcopy(active)
            closed["status"] = outcome_value
            closed["completed_at"] = utc_now()
            closed["resolution"] = (resolution or "").strip() or None
            record.setdefault("recent_tasks", []).append(closed)
            record["recent_tasks"] = record["recent_tasks"][-20:]
            record["active_task"] = None
            record["updated_at"] = utc_now()
            if outcome_value == "completed":
                self._set_pending_action_execution_state(
                    pending,
                    action_id=closed.get("related_action_id"),
                    execution_state="completed",
                    task_id=None,
                )
                if closed.get("related_action_id"):
                    for action in pending.get("actions", []):
                        if action.get("id") == closed.get("related_action_id") and action.get("status") != "resolved":
                            action["status"] = "resolved"
                            action["resolved_at"] = utc_now()
                            action["resolved_by"] = owner
                            action["resolution"] = closed.get("resolution")
                            action["updated_at"] = utc_now()
                            break
            else:
                self._set_pending_action_execution_state(
                    pending,
                    action_id=closed.get("related_action_id"),
                    execution_state=outcome_value,
                    task_id=None,
                )
            self._save_execution_state(payload)
            self._save_pending_actions(pending)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "complete_execution_task",
                    "owner_agent": owner,
                    "task_id": task_id,
                    "outcome": outcome_value,
                    "accepted": True,
                }
            )
            return BridgeResult(True, outcome_value, "Execution task %s closed as %s." % (task_id, outcome_value), {"task": self._derive_execution_task(closed)})

    def execution_status(self, owner_agent: str) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            payload = self._load_execution_state()
            record = self._owner_execution_record(payload, owner)
            active = self._derive_execution_task(record.get("active_task"))
            recent = [self._derive_execution_task(task) for task in record.get("recent_tasks", [])]
            return BridgeResult(
                True,
                "execution_status",
                "Execution status for %s." % owner,
                {
                    "owner_agent": owner,
                    "active_task": active,
                    "recent_tasks": recent,
                    "resume_hint": active.get("resume_hint") if active else None,
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

    def _health_read_json(self, path: Path, default: Dict[str, Any], metric: str) -> Dict[str, Any]:
        if not path.exists():
            return {"status": "missing", "path": str(path), "data": copy.deepcopy(default), "error": None}
        try:
            return {"status": "ok", "path": str(path), "data": read_json(path, default), "error": None}
        except Exception as exc:
            return {"status": "error", "path": str(path), "data": copy.deepcopy(default), "error": "%s" % exc}

    def _health_read_jsonl(self, path: Path, metric: str) -> Dict[str, Any]:
        if not path.exists():
            return {"status": "missing", "path": str(path), "rows": [], "error": None, "bad_lines": 0}
        rows: List[Dict[str, Any]] = []
        bad_lines = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except JSONDecodeError:
                        bad_lines += 1
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
            status = "partial" if bad_lines else "ok"
            return {"status": status, "path": str(path), "rows": rows, "error": None, "bad_lines": bad_lines}
        except Exception as exc:
            return {"status": "error", "path": str(path), "rows": rows, "error": "%s" % exc, "bad_lines": bad_lines}

    def _health_age_seconds(self, stamp: Any, now_dt: datetime) -> Optional[int]:
        parsed = parse_iso_datetime(stamp)
        if not parsed:
            return None
        return max(0, int((now_dt - parsed).total_seconds()))

    def _health_active_sessions(self, registry_read: Dict[str, Any], now_dt: datetime) -> Dict[str, Any]:
        if registry_read["status"] == "error":
            return {
                "status": "error",
                "path": registry_read["path"],
                "error": registry_read["error"],
                "projects": {},
                "active": {},
                "recent_superseded": [],
            }
        registry = registry_read["data"]
        projects: Dict[str, Any] = {}
        active_flat: Dict[str, Any] = {}
        recent_superseded: List[Dict[str, Any]] = []
        cutoff = now_dt - timedelta(hours=1)
        for project_name, project_entry in (registry.get("projects") or {}).items():
            project_active = dict((project_entry or {}).get("active") or {})
            project_sessions = {}
            for session_id, record in ((project_entry or {}).get("sessions") or {}).items():
                if not isinstance(record, dict):
                    continue
                session_summary = {
                    "agent": record.get("agent"),
                    "status": record.get("status"),
                    "bootstrap_origin": record.get("bootstrap_origin", "unknown"),
                    "started_at": record.get("started_at"),
                    "updated_at": record.get("updated_at"),
                    "age_seconds": self._health_age_seconds(record.get("started_at"), now_dt),
                }
                project_sessions[session_id] = session_summary
                if project_active.get(record.get("agent")) == session_id:
                    active_flat["%s:%s" % (project_name, record.get("agent"))] = {
                        "project": project_name,
                        "agent": record.get("agent"),
                        "session_id": session_id,
                        **session_summary,
                    }
                elif record.get("status") == "superseded":
                    updated_at = parse_iso_datetime(record.get("updated_at"))
                    if updated_at and updated_at >= cutoff:
                        recent_superseded.append(
                            {
                                "project": project_name,
                                "session_id": session_id,
                                **session_summary,
                            }
                        )
            projects[project_name] = {
                "active": project_active,
                "sessions": project_sessions,
                "trusted_parent": dict((project_entry or {}).get("trusted_parent") or {}),
            }
        return {
            "status": "ok" if registry_read["status"] in {"ok", "missing"} else registry_read["status"],
            "path": registry_read["path"],
            "project_count": len(projects),
            "projects": projects,
            "active": active_flat,
            "recent_superseded": recent_superseded,
        }

    def _health_inboxes(self, now_dt: datetime) -> Dict[str, Any]:
        buckets: List[Dict[str, Any]] = []
        totals = {
            "unread_count": 0,
            "handled_not_seen_count": 0,
            "bad_lines": 0,
        }
        errors: List[Dict[str, Any]] = []
        for agent in sorted(AGENTS):
            read = self._health_read_jsonl(self.inbox_path(agent), "inboxes")
            if read["status"] == "error":
                errors.append({"metric": "inboxes", "agent": agent, "error": read["error"]})
            if read.get("bad_lines"):
                errors.append({"metric": "inboxes", "agent": agent, "error": "bad_jsonl_lines=%d" % read["bad_lines"]})
            grouped: Dict[str, Dict[str, Any]] = {}
            for row in read["rows"]:
                session = str(row.get("session_id") or DEFAULT_SESSION_ID)
                entry = grouped.setdefault(
                    session,
                    {
                        "agent": agent,
                        "session_id": session,
                        "unread_count": 0,
                        "oldest_unread_age_seconds": None,
                        "handled_not_seen_count": 0,
                        "handled_not_seen_oldest_age_seconds": None,
                        "row_count": 0,
                    },
                )
                entry["row_count"] += 1
                created_age = self._health_age_seconds(row.get("created_at"), now_dt)
                if not row.get("read_at"):
                    entry["unread_count"] += 1
                    totals["unread_count"] += 1
                    if created_age is not None and (
                        entry["oldest_unread_age_seconds"] is None
                        or created_age > entry["oldest_unread_age_seconds"]
                    ):
                        entry["oldest_unread_age_seconds"] = created_age
                if row.get("read_at") and not row.get("seen_at"):
                    entry["handled_not_seen_count"] += 1
                    totals["handled_not_seen_count"] += 1
                    if created_age is not None and (
                        entry["handled_not_seen_oldest_age_seconds"] is None
                        or created_age > entry["handled_not_seen_oldest_age_seconds"]
                    ):
                        entry["handled_not_seen_oldest_age_seconds"] = created_age
            buckets.extend(sorted(grouped.values(), key=lambda item: (item["agent"], item["session_id"])))
            totals["bad_lines"] += int(read.get("bad_lines") or 0)
        return {
            "status": "error" if errors else "ok",
            "bucket_count": len(buckets),
            "totals": totals,
            "buckets": buckets,
            "errors": errors,
        }

    def _health_in_flight_wakes(self, now_dt: datetime, threshold_seconds: int) -> Dict[str, Any]:
        try:
            import psutil  # type: ignore
        except Exception as exc:
            unavailable = {
                "status": "unavailable",
                "error": "psutil unavailable: %s" % exc,
                "count": 0,
                "processes": [],
            }
            return {
                "in_flight": unavailable,
                "stuck": {
                    "status": "unavailable",
                    "error": unavailable["error"],
                    "count": 0,
                    "threshold_seconds": threshold_seconds,
                    "processes": [],
                },
            }
        processes: List[Dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                info = proc.info
                cmdline = " ".join(str(part) for part in (info.get("cmdline") or []))
                name = str(info.get("name") or "")
                if "wake_codex.ps1" not in cmdline:
                    continue
                created = datetime.fromtimestamp(float(info.get("create_time") or 0), timezone.utc)
                age = max(0, int((now_dt - created).total_seconds()))
                target_session = None
                match = re.search(r"-(?:ThreadId|SessionId)\s+['\"]?([A-Za-z0-9_.:-]+)", cmdline)
                if match:
                    target_session = match.group(1)
                processes.append(
                    {
                        "pid": info.get("pid"),
                        "name": name,
                        "target_agent": "codex",
                        "target_session": target_session,
                        "started_at": created.isoformat(timespec="seconds"),
                        "age_seconds": age,
                        "cmdline_preview": cmdline[:240],
                    }
                )
            except Exception:
                continue
        stuck = [item for item in processes if int(item.get("age_seconds") or 0) > threshold_seconds]
        return {
            "in_flight": {"status": "ok", "count": len(processes), "processes": processes},
            "stuck": {
                "status": "ok",
                "count": len(stuck),
                "threshold_seconds": threshold_seconds,
                "processes": stuck,
            },
        }

    def _health_pending_actions(self, now_dt: datetime) -> Dict[str, Any]:
        read = self._health_read_json(self.pending_actions_path, self._default_pending_actions(), "pending_actions")
        if read["status"] == "error":
            return {"status": "error", "path": read["path"], "error": read["error"]}
        actions = [item for item in (read["data"].get("actions") or []) if isinstance(item, dict)]
        counts: Dict[str, int] = {}
        execution_counts: Dict[str, int] = {}
        unresolved: List[Dict[str, Any]] = []
        for action in actions:
            status = str(action.get("status") or "pending")
            counts[status] = counts.get(status, 0) + 1
            execution_state = str(action.get("execution_state") or "unclassified")
            execution_counts[execution_state] = execution_counts.get(execution_state, 0) + 1
            if status == "pending" and execution_state not in NON_ACTIONABLE_PENDING_EXECUTION_STATES:
                unresolved.append(
                    {
                        "id": action.get("id"),
                        "summary": action.get("summary"),
                        "priority": action.get("priority"),
                        "created_at": action.get("created_at"),
                        "age_seconds": self._health_age_seconds(action.get("created_at"), now_dt),
                        "execution_state": action.get("execution_state"),
                    }
                )
        unresolved.sort(key=lambda item: item.get("created_at") or "")
        return {
            "status": "ok" if read["status"] in {"ok", "missing"} else read["status"],
            "path": read["path"],
            "counts_by_status": counts,
            "counts_by_execution_state": execution_counts,
            "unresolved_actionable_count": len(unresolved),
            "oldest_unresolved": unresolved[:5],
        }

    def _health_recent_failures(self, now_dt: datetime) -> Dict[str, Any]:
        read = self._health_read_jsonl(self.audit_path, "recent_failures")
        failures: List[Dict[str, Any]] = []
        cutoff = now_dt - timedelta(minutes=5)
        for row in read["rows"][-200:]:
            action = str(row.get("action") or "")
            accepted = row.get("accepted")
            timestamp = parse_iso_datetime(row.get("timestamp"))
            if timestamp and timestamp < cutoff:
                continue
            is_failure = (
                "fail" in action
                or "rejected" in action
                or "breaker_open" in action
                or accepted is False
                or bool(row.get("error"))
            )
            if is_failure:
                failures.append(
                    {
                        "event_type": action,
                        "event_ts": row.get("timestamp"),
                        "agent": row.get("agent") or row.get("from") or row.get("to"),
                        "session_id": row.get("session_id") or row.get("resolved_session_id"),
                        "summary": str(row.get("reason") or row.get("error") or row.get("status") or "")[:120],
                    }
                )
        return {
            "status": "error" if read["status"] == "error" else "ok",
            "path": read["path"],
            "count": len(failures),
            "failures": failures[-50:],
            "error": read["error"],
        }

    def _health_provenance(self, active_sessions: Dict[str, Any]) -> Dict[str, Any]:
        counts = {"parent": 0, "subagent": 0, "unknown": 0}
        active_subagents: List[Dict[str, Any]] = []
        for active in active_sessions.get("active", {}).values():
            origin = str(active.get("bootstrap_origin") or "unknown")
            counts[origin] = counts.get(origin, 0) + 1
            if origin == "subagent":
                active_subagents.append(active)
        return {
            "status": "ok",
            "counts": counts,
            "subagent_owns_active": bool(active_subagents),
            "active_subagents": active_subagents,
        }

    def _health_wake_breaker(self) -> Dict[str, Any]:
        read = self._health_read_json(
            self.wake_breaker_path,
            {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}},
            "wake_breaker",
        )
        if read["status"] == "error":
            return {"status": "error", "path": read["path"], "error": read["error"], "sessions": {}}
        sessions = read["data"].get("sessions") or {}
        open_sessions = {
            session_id: record
            for session_id, record in sessions.items()
            if isinstance(record, dict) and record.get("breaker_state") == "open"
        }
        return {
            "status": "ok" if read["status"] in {"ok", "missing"} else read["status"],
            "path": read["path"],
            "session_count": len(sessions),
            "open_session_count": len(open_sessions),
            "open_sessions": open_sessions,
            "sessions": sessions,
        }

    def _health_last_wake_per_peer(self) -> Dict[str, Any]:
        read = self._health_read_jsonl(self.audit_path, "last_wake_per_peer")
        last: Dict[str, Any] = {}
        for row in read["rows"]:
            action = str(row.get("action") or "")
            if action not in {"wake_succeeded", "wake_receipt_verified", "wake_delivered"}:
                continue
            agent = row.get("agent") or row.get("to")
            session = row.get("session_id") or row.get("resolved_session_id")
            if not agent or not session:
                continue
            key = "%s:%s" % (agent, session)
            last[key] = {
                "agent": agent,
                "session_id": session,
                "timestamp": row.get("timestamp"),
                "event_type": action,
            }
        return {"status": "ok" if read["status"] != "error" else "error", "peers": last, "error": read["error"]}

    def _health_schema_versions(self) -> Dict[str, Any]:
        files = {
            "state": self.state_path,
            "session_registry": self.session_registry_path,
            "pending_actions": self.pending_actions_path,
            "execution_state": self.execution_state_path,
            "wake_breaker": self.wake_breaker_path,
            "tool_manifest": self.state_dir / "tool-manifest.json",
            "cross_project_pending": self.cross_project_pending_path,
        }
        versions: Dict[str, Any] = {}
        for name, path in files.items():
            read = self._health_read_json(path, {}, "schema_versions")
            if read["status"] == "error":
                versions[name] = {"status": "error", "path": str(path), "error": read["error"]}
                continue
            data = read["data"]
            versions[name] = {
                "status": read["status"],
                "path": str(path),
                "schema_version": data.get("schema_version", "legacy") if isinstance(data, dict) else "legacy",
            }
        return {"status": "ok", "files": versions}

    def _health_cross_project(self) -> Dict[str, Any]:
        now_dt = datetime.now(timezone.utc)
        links = []
        for link in self._cross_project_link_records():
            links.append(
                {
                    "link_id": link.get("link_id"),
                    "status": link.get("status"),
                    "derived_status": self._cross_project_link_status(link, now_dt=now_dt),
                    "permission_tier": link.get("permission_tier"),
                    "advisor_project": (link.get("advisor") or {}).get("project"),
                    "executor_project": (link.get("executor") or {}).get("project"),
                    "expires_at": link.get("expires_at"),
                }
            )
        active = [link for link in links if link.get("derived_status") == "active"]
        return {
            "status": "ok",
            "path": str(self.cross_project_pairs_dir),
            "active_count": len(active),
            "link_count": len(links),
            "links": links,
        }

    def _derive_health_status(self, snapshot: Dict[str, Any]) -> str:
        core = snapshot.get("core") or {}
        bridge_state = core.get("bridge_state") or {}
        watcher = core.get("watcher") or {}
        server = core.get("server") or {}
        stuck_wakes = core.get("stuck_wakes") or {}
        inboxes = core.get("inboxes") or {}
        extended = snapshot.get("extended") or {}
        wake_breaker = extended.get("wake_breaker") or {}
        recent_failures = extended.get("recent_failures") or {}
        pending_actions = extended.get("pending_actions") or {}
        provenance = extended.get("provenance") or {}

        if bridge_state.get("status") == "error" or (core.get("active_sessions") or {}).get("status") == "error":
            return "broken"
        if int(stuck_wakes.get("count") or 0) > 0:
            return "broken"
        if watcher.get("stale") and not bridge_state.get("paused"):
            return "broken"
        if server.get("stale_server_marker_count", 0):
            return "broken"
        if bridge_state.get("paused"):
            return "paused"
        if (wake_breaker.get("open_session_count") or 0) > 0:
            return "degraded"
        if (inboxes.get("totals") or {}).get("handled_not_seen_count", 0) > 0:
            return "degraded"
        if recent_failures.get("count", 0) > 0:
            return "degraded"
        if pending_actions.get("unresolved_actionable_count", 0) > 0:
            return "degraded"
        if provenance.get("subagent_owns_active"):
            return "degraded"
        return "healthy"

    def _render_health_markdown(self, snapshot: Dict[str, Any]) -> str:
        core = snapshot.get("core") or {}
        extended = snapshot.get("extended") or {}
        lines = [
            "# Bridge Health",
            "",
            "## Overview",
            "",
            "| Field | Value |",
            "|---|---|",
            "| Overall | %s |" % snapshot.get("overall_status"),
            "| Snapshot | %s |" % snapshot.get("snapshot_ts"),
            "| Bridge root | `%s` |" % snapshot.get("bridge_root"),
            "| Duration | %sms |" % snapshot.get("snapshot_duration_ms"),
            "",
            "## Core",
            "",
            "| Metric | Status | Detail |",
            "|---|---|---|",
        ]
        bridge_state = core.get("bridge_state") or {}
        watcher = core.get("watcher") or {}
        server = core.get("server") or {}
        in_flight = core.get("in_flight_wakes") or {}
        stuck = core.get("stuck_wakes") or {}
        inboxes = core.get("inboxes") or {}
        lines.extend(
            [
                "| Bridge state | %s | paused=%s |" % (bridge_state.get("status"), bridge_state.get("paused")),
                "| Watcher | %s | pid=%s running=%s stale=%s |"
                % (watcher.get("status", "ok"), watcher.get("pid"), watcher.get("running"), watcher.get("stale")),
                "| MCP servers | ok | markers=%s stale=%s |"
                % (server.get("mcp_server_marker_count", 0), server.get("stale_server_marker_count", 0)),
                "| In-flight wakes | %s | count=%s |" % (in_flight.get("status"), in_flight.get("count", 0)),
                "| Stuck wakes | %s | count=%s |" % (stuck.get("status"), stuck.get("count", 0)),
                "| Inboxes | %s | unread=%s handled-not-seen=%s |"
                % (
                    inboxes.get("status"),
                    (inboxes.get("totals") or {}).get("unread_count", 0),
                    (inboxes.get("totals") or {}).get("handled_not_seen_count", 0),
                ),
            ]
        )
        sessions = (core.get("active_sessions") or {}).get("active") or {}
        lines.extend(["", "## Active Sessions", "", "| Project/Agent | Session | Origin |", "|---|---|---|"])
        if sessions:
            for key, session in sorted(sessions.items()):
                lines.append(
                    "| %s | `%s` | %s |"
                    % (key, session.get("session_id"), session.get("bootstrap_origin", "unknown"))
                )
        else:
            lines.append("| none |  |  |")
        if extended:
            pending = extended.get("pending_actions") or {}
            breaker = extended.get("wake_breaker") or {}
            cross = extended.get("cross_project") or {}
            lines.extend(
                [
                    "",
                    "## Extended",
                    "",
                    "| Metric | Detail |",
                    "|---|---|",
                    "| Pending actions | actionable=%s |" % pending.get("unresolved_actionable_count", 0),
                    "| Wake breakers | open=%s |" % breaker.get("open_session_count", 0),
                    "| Cross-project links | active=%s total=%s |"
                    % (cross.get("active_count", 0), cross.get("link_count", 0)),
                ]
            )
        if snapshot.get("errors"):
            lines.extend(["", "## Errors", "", "| Metric | Error |", "|---|---|"])
            for error in snapshot["errors"]:
                lines.append("| %s | %s |" % (error.get("metric"), error.get("error")))
        return "\n".join(lines)

    def bridge_health_panel(
        self,
        agent: str,
        session_id: Optional[str] = None,
        include_extended: bool = False,
        format: str = "json",
        stuck_wake_threshold_seconds: int = 30,
    ) -> BridgeResult:
        start = time.monotonic()
        now_dt = datetime.now(timezone.utc)
        try:
            caller = normalize_agent(agent)
            session = normalize_session(session_id) if session_id is not None else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        output_format = (format or "json").strip().lower()
        if output_format not in {"json", "markdown"}:
            return BridgeResult(False, "rejected", "format must be json or markdown")
        if (
            isinstance(stuck_wake_threshold_seconds, bool)
            or not isinstance(stuck_wake_threshold_seconds, int)
            or stuck_wake_threshold_seconds < 1
            or stuck_wake_threshold_seconds > 3600
        ):
            return BridgeResult(False, "rejected", "stuck_wake_threshold_seconds must be between 1 and 3600")

        errors: List[Dict[str, Any]] = []
        state_read = self._health_read_json(self.state_path, self._default_state(), "bridge_state")
        registry_read = self._health_read_json(self.session_registry_path, self._default_session_registry(), "active_sessions")
        state_data = state_read["data"] if isinstance(state_read["data"], dict) else {}
        bridge_state = {
            "status": state_read["status"],
            "path": state_read["path"],
            "paused": bool(state_data.get("paused")),
            "paused_reason": state_data.get("paused_reason"),
            "paused_since": state_data.get("paused_since"),
            "error": state_read["error"],
        }
        if state_read["status"] == "error":
            errors.append({"metric": "bridge_state", "error": state_read["error"]})

        active_sessions = self._health_active_sessions(registry_read, now_dt)
        if active_sessions.get("status") == "error":
            errors.append({"metric": "active_sessions", "error": active_sessions.get("error")})

        try:
            process_result = self.bridge_process_status()
            process_data = process_result.data
        except Exception as exc:
            process_data = {
                "watcher": {"status": "error", "error": "%s" % exc, "stale": True, "running": False},
                "mcp_server_marker_count": 0,
                "stale_server_marker_count": 0,
                "mcp_server_markers": [],
            }
            errors.append({"metric": "process_status", "error": "%s" % exc})
        wake_processes = self._health_in_flight_wakes(now_dt, stuck_wake_threshold_seconds)
        for key in ("in_flight", "stuck"):
            if wake_processes[key].get("status") == "unavailable":
                errors.append({"metric": "%s_wakes" % key, "error": wake_processes[key].get("error")})
        inboxes = self._health_inboxes(now_dt)
        errors.extend(inboxes.get("errors") or [])

        extended: Dict[str, Any] = {}
        if include_extended:
            extended["pending_actions"] = self._health_pending_actions(now_dt)
            extended["recent_failures"] = self._health_recent_failures(now_dt)
            extended["provenance"] = self._health_provenance(active_sessions)
            extended["wake_breaker"] = self._health_wake_breaker()
            extended["last_wake_per_peer"] = self._health_last_wake_per_peer()
            extended["schema_versions"] = self._health_schema_versions()
            extended["cross_project"] = self._health_cross_project()
            for metric, payload in extended.items():
                if isinstance(payload, dict) and payload.get("status") == "error":
                    errors.append({"metric": metric, "error": payload.get("error")})

        snapshot: Dict[str, Any] = {
            "schema_version": 1,
            "snapshot_ts": now_dt.isoformat(timespec="seconds"),
            "snapshot_duration_ms": 0,
            "bridge_root": str(self.state_dir.parent),
            "state_dir": str(self.state_dir),
            "tenant_id": "local-default",
            "machine_id": "local-machine",
            "caller": {"agent": caller, "session_id": session},
            "overall_status": "healthy",
            "core": {
                "bridge_state": bridge_state,
                "active_sessions": active_sessions,
                "watcher": process_data.get("watcher", {}),
                "server": {
                    "mcp_server_marker_count": process_data.get("mcp_server_marker_count", 0),
                    "stale_server_marker_count": process_data.get("stale_server_marker_count", 0),
                    "markers": process_data.get("mcp_server_markers", []),
                },
                "in_flight_wakes": wake_processes["in_flight"],
                "stuck_wakes": wake_processes["stuck"],
                "inboxes": inboxes,
            },
            "extended": extended if include_extended else {},
            "errors": errors,
        }
        snapshot["overall_status"] = self._derive_health_status(snapshot)
        snapshot["snapshot_duration_ms"] = int((time.monotonic() - start) * 1000)
        if output_format == "markdown":
            markdown = self._render_health_markdown(snapshot)
            return BridgeResult(
                True,
                snapshot["overall_status"],
                markdown,
                {"snapshot": snapshot, "markdown": markdown},
            )
        return BridgeResult(
            True,
            snapshot["overall_status"],
            "Bridge health is %s." % snapshot["overall_status"],
            {"snapshot": snapshot},
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
            payload = read_json(self.wake_breaker_path, {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}})
            open_sessions = [
                session_id
                for session_id, record in (payload.get("sessions") or {}).items()
                if self._normalize_wake_breaker_session(record).get("breaker_state") == "open"
            ]
            grants = [
                self._grant_wake_breaker_bypass(
                    str(session_id),
                    reason="resume_bridge",
                    granted_by="system",
                    force=True,
                )
                for session_id in open_sessions
            ]
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "resume_bridge",
                    "accepted": True,
                    "wake_bypass_grants": grants,
                }
            )
            return BridgeResult(True, "active", "Bridge resumed.", {"wake_bypass_grants": grants})

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
