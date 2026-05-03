import argparse
import copy
import contextlib
import dataclasses
import hashlib
import html
import json
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import uuid
from json import JSONDecodeError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.addressing import AgentInbox, MessageKind, ProjectInbox, SenderContext, SessionInbox
from core.auth import LOCAL_DEFAULT_MACHINE_ID, LOCAL_DEFAULT_TENANT_ID, Identity, LocalUserAuth
from core.paths import routing_rules_path_for_state_dir, session_registry_path_for_state_dir, watcher_pid_path_for_state_dir
from core.runtime import (
    MONITOR_RUNTIME_MIN_TTL_S,
    build_peer_runtime_breadcrumb,
    monitor_runtime_path_for_state_dir,
    peer_runtime_path_for_state_dir,
    read_runtime_breadcrumb,
    write_runtime_breadcrumb,
)
from core.routing import resolve_route
from core.settings import load_settings
from core.storage import STATE_SCHEMA_VERSION
from core.transport import LocalFilesystemTransport
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
STALE_UNREAD_DEFAULT_AGE_S = 300
WAKE_PREFIRE_LIMIT = 2
WAKE_PREFIRE_WINDOW_S = 10
EXECUTION_STATE_SCHEMA_VERSION = 1
EXECUTION_PROOF_TIMEOUT_S = 120
NON_ACTIONABLE_PENDING_EXECUTION_STATES = {"blocked", "parked", "displaced", "completed"}
IMPLEMENTATION_JOURNAL_SCHEMA_VERSION = 1
IMPLEMENTATION_JOURNAL_MAX_EVENTS = 500
IMPLEMENTATION_JOURNAL_DIGEST_MAX_ITEMS = 20
IMPLEMENTATION_JOURNAL_MESSAGE_TYPES = {"IMPLEMENTATION_UPDATE", "IMPLEMENTATION_SUMMARY", "PHASE_DONE"}
REVIEW_LOOP_SCHEMA_VERSION = 1
REVIEWER_WAIT_SCHEMA_VERSION = 1
REVIEW_REQUEST_TYPES = {"AUDIT_REQUEST", "REVIEW_REQUEST"}
REVIEW_RESULT_TYPES = {"AUDIT_RESULT", "REVIEW_RESULT"}
REVIEW_CLOSEOUT_TYPES = {
    "AUDIT_RESULT",
    "CLOSEOUT",
    "IMPLEMENTATION_ACK",
    "READINESS_ASSESSMENT",
    "REVIEW_ACK",
    "STATUS_ACK",
}
CROSS_PROJECT_PAIR_SCHEMA_VERSION = 1
CROSS_PROJECT_PENDING_SCHEMA_VERSION = 1
CROSS_PROJECT_NONCE_WINDOW_S = 60
CROSS_PROJECT_DEFAULT_TTL_MINUTES = 120
CROSS_PROJECT_MAX_TTL_MINUTES = 24 * 60
CROSS_PROJECT_READ_AND_ADVISE = "read_and_advise"
CROSS_PROJECT_WRITE_WITH_CONFIRMATION = "write_with_confirmation"
CROSS_PROJECT_PERMISSION_TIERS = {CROSS_PROJECT_READ_AND_ADVISE, CROSS_PROJECT_WRITE_WITH_CONFIRMATION}
CROSS_PROJECT_ROLES = {"advisor", "executor"}
DASHBOARD_CONTRACT_EXPIRING_SOON_S = 15 * 60
CROSS_PROJECT_WARNING = "You are about to pair threads from different projects. Are you sure?"
REMOTE_REQUEST_CLASSES = {
    "informational",
    "proposal",
    "contract_action_request",
    "local_confirmation_required",
    "forbidden_remote_authority",
    "access_reducing_request",
}
PROTECTED_DOC_TOKENS = {
    "agents.md",
    "claude.md",
    "bridge_trigger_heuristics.md",
    "policy_authority_spec.md",
    "knowledge_sharing_contract_spec.md",
    "security_review.md",
}
INBOX_LEVEL_SESSION = "session"
INBOX_LEVEL_PROJECT = "project"
INBOX_LEVEL_AGENT = "agent"
PAIRING_INTENTS = {"ask_first", "active_primary", "background"}
NON_PRIMARY_PAIRING_STATUSES = {"pending_pair", "background"}
GUIDED_PAIRING_ROLES = {"active_primary", "background", "observer", "advisor", "auditor"}
NON_PRIMARY_SESSION_CAP_PER_AGENT_PROJECT = 3
EPHEMERAL_RELAY_CAP_PER_SESSION = 5
EPHEMERAL_RELAY_MODE = "ephemeral"
PENDING_PAIR_FIELDS = {
    "pending_pair_started_at",
    "pending_pair_timeout_seconds",
    "pending_pair_target_active_session",
    "pending_pair_peer_session",
    "pending_pair_fallback_at",
    "pending_pair_fallback_reason",
}
SESSION_BACKPRESSURE_LIMIT = 5
PROJECT_BACKPRESSURE_LIMIT = 10
# Per-pair rate limit: at most RATE_LIMIT_N accepted send_to_peer calls per
# (from_agent, to_agent) pair within a rolling RATE_LIMIT_WINDOW_S window.
# Replaces the old hop-count rejection (agreed via bridge HEURISTIC_SYNC
# 2026-04-27) — runaway loops trip in seconds while normal conversation
# never approaches the limit.
RATE_LIMIT_N = 30
RATE_LIMIT_WINDOW_S = 60
SESSION_REGISTRY_SCHEMA_VERSION = 3
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


def normalize_classifier_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    cleaned = []
    for char in normalized:
        category = unicodedata.category(char)
        if category == "Cf":
            continue
        if category == "Cc":
            cleaned.append(" ")
            continue
        cleaned.append(char)
    return re.sub(r"\s+", " ", "".join(cleaned)).strip().casefold()


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    with _file_lock(path):
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def _file_lock(path: Path, timeout_seconds: float = 30.0, stale_seconds: float = 120.0):
    @contextlib.contextmanager
    def _manager():
        lock_path = path.with_name(path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        while True:
            try:
                lock_path.mkdir()
                break
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    if age > stale_seconds:
                        lock_path.rmdir()
                        continue
                except OSError:
                    pass
                if time.time() - start > timeout_seconds:
                    raise TimeoutError("timed out waiting for storage lock %s" % lock_path)
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                lock_path.rmdir()
            except OSError:
                pass
    return _manager()


def _temp_path_for(path: Path) -> Path:
    return path.with_name(
        "%s.%s.%s.%s.tmp" % (path.name, os.getpid(), threading.get_ident(), uuid.uuid4().hex)
    )


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
    with _file_lock(path):
        tmp = _temp_path_for(path)
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            _atomic_replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    quarantine: List[str] = []
    with _file_lock(path):
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
    with _file_lock(path):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        tmp = _temp_path_for(path)
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True))
                    handle.write("\n")
            _atomic_replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


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


def bridge_header_value(body: str, header: str) -> str:
    pattern = re.compile(r"^\s*%s\s*:\s*(?P<value>.+?)\s*$" % re.escape(header), re.IGNORECASE)
    for line in (body or "").splitlines():
        match = pattern.match(line)
        if match:
            return match.group("value").strip()
    return ""


def bridge_message_type(body: str) -> str:
    return bridge_header_value(body, "TYPE").upper()


def bridge_message_subject(body: str) -> str:
    return bridge_header_value(body, "SUBJECT")


def bridge_message_in_reply_to(body: str) -> str:
    return bridge_header_value(body, "IN_REPLY_TO")


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


PROCESS_MARKER_START_TOLERANCE_SECONDS = 300


def process_start_time_utc(pid: int) -> Optional[datetime]:
    """Return a process creation timestamp when the platform exposes one.

    Windows reuses PIDs aggressively enough that "PID exists" is not a safe
    identity check for long-lived marker files. Creation time lets us
    distinguish the original MCP server from an unrelated later process.
    """
    if pid <= 0 or sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = ctypes.windll.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        ctypes.windll.kernel32.CloseHandle(handle)
        if not ok:
            return None
        ticks = (creation.dwHighDateTime << 32) + creation.dwLowDateTime
        unix_seconds = ticks / 10_000_000 - 11_644_473_600
        return datetime.fromtimestamp(unix_seconds, timezone.utc)
    except Exception:
        return None


def process_runtime_identity_status(
    pid: int,
    runtime: Optional[Dict[str, Any]],
    *,
    expected_role: Optional[str] = None,
    max_start_delta_seconds: int = PROCESS_MARKER_START_TOLERANCE_SECONDS,
) -> Dict[str, Any]:
    running = is_process_alive(pid)
    status: Dict[str, Any] = {
        "running": running,
        "identity_verified": False,
        "identity_mismatch": False,
        "identity_mismatch_reason": None,
        "process_started_at": None,
        "runtime_timestamp_delta_seconds": None,
    }
    if not running:
        return status

    runtime_data = runtime if isinstance(runtime, dict) else {}
    role = runtime_data.get("role")
    if expected_role and role and role != expected_role:
        status["identity_mismatch"] = True
        status["identity_mismatch_reason"] = "role_mismatch"
        return status

    runtime_started_at = parse_iso_datetime(runtime_data.get("timestamp"))
    process_started_at = process_start_time_utc(pid)
    if process_started_at is not None:
        status["process_started_at"] = process_started_at.isoformat(timespec="seconds")
    if runtime_started_at is None or process_started_at is None:
        return status

    delta = abs((process_started_at - runtime_started_at).total_seconds())
    status["identity_verified"] = True
    status["runtime_timestamp_delta_seconds"] = round(delta, 3)
    if delta > max_start_delta_seconds:
        status["identity_mismatch"] = True
        status["identity_mismatch_reason"] = "pid_reuse_start_time_mismatch"
    return status


class AgentBridge:
    def __init__(self, state_dir: Path, max_hops: int = DEFAULT_MAX_HOPS) -> None:
        self.state_dir = Path(state_dir)
        self.max_hops = max_hops
        self._lock = threading.Lock()
        self.auth = LocalUserAuth(machine_id=LOCAL_DEFAULT_MACHINE_ID)
        self.transport = LocalFilesystemTransport()

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
    def implementation_journal_path(self) -> Path:
        return self.state_dir / "implementation-journal.json"

    @property
    def review_loop_state_path(self) -> Path:
        return self.state_dir / "review-loop-state.jsonl"

    @property
    def reviewer_wait_state_path(self) -> Path:
        return self.state_dir / "reviewer-wait-state.jsonl"

    @property
    def guardrail_debt_path(self) -> Path:
        return self.state_dir / "guardrail-debt.jsonl"

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
            "schema_version": STATE_SCHEMA_VERSION,
            "paused": False,
            "sessions": {},
            "updated_at": utc_now(),
        }

    def _load_state(self) -> Dict[str, Any]:
        state = read_json(self.state_path, self._default_state())
        state["schema_version"] = max(int(state.get("schema_version") or 1), STATE_SCHEMA_VERSION)
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

    def _backpressure_key(self, agent: str, session_id: str) -> str:
        return "%s:%s" % (agent, session_id)

    def _backpressure_warning_threshold(self, limit: int) -> int:
        return max(1, (int(limit) * 3 + 4) // 5)

    def _backpressure_limit_for_level(self, inbox_level: str) -> Optional[int]:
        if inbox_level == INBOX_LEVEL_PROJECT:
            return PROJECT_BACKPRESSURE_LIMIT
        if inbox_level == INBOX_LEVEL_SESSION:
            return SESSION_BACKPRESSURE_LIMIT
        return None

    def _backpressure_limit_for_bucket(self, registry: Dict[str, Any], session_id: str) -> Optional[int]:
        info = self._bucket_info(registry, session_id)
        return self._backpressure_limit_for_level(info["inbox_level"])

    def _bucket_accepts_work_backpressure(
        self,
        registry: Dict[str, Any],
        receiver_agent: str,
        session_id: str,
    ) -> bool:
        info = self._bucket_info(registry, session_id)
        if info["inbox_level"] != INBOX_LEVEL_SESSION:
            return True
        record = info.get("record")
        if not isinstance(record, dict):
            return True
        return (
            record.get("agent") == receiver_agent
            and record.get("status") == "active"
            and info.get("active_session") == session_id
        )

    def _row_counts_for_backpressure(
        self,
        registry: Dict[str, Any],
        receiver_agent: str,
        row: Dict[str, Any],
    ) -> bool:
        if row.get("read_at") or row.get("superseded_at") or row.get("marker_variant") == "control":
            return False
        bucket = str(row.get("session_id") or DEFAULT_SESSION_ID)
        return self._bucket_accepts_work_backpressure(registry, receiver_agent, bucket)

    def _unread_work_counts_by_bucket(
        self,
        rows: List[Dict[str, Any]],
        registry: Optional[Dict[str, Any]] = None,
        receiver_agent: Optional[str] = None,
    ) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        receiver = (receiver_agent or "").strip().lower()
        for row in rows:
            if registry is not None and receiver in AGENTS:
                if not self._row_counts_for_backpressure(registry, receiver, row):
                    continue
            elif row.get("read_at") or row.get("superseded_at") or row.get("marker_variant") == "control":
                continue
            bucket = str(row.get("session_id") or DEFAULT_SESSION_ID)
            counts[bucket] = counts.get(bucket, 0) + 1
        return counts

    def _record_backpressure_pending(
        self,
        state: Dict[str, Any],
        *,
        receiver_agent: str,
        session_id: str,
        inbox_level: str,
        sender_agent: str,
        sender_session_id: Optional[str],
        reason: str,
        unread_count: int,
        limit: int,
    ) -> Dict[str, Any]:
        now = utc_now()
        pending = state.setdefault("backpressure_pending", {})
        key = self._backpressure_key(receiver_agent, session_id)
        record = pending.setdefault(
            key,
            {
                "receiver_agent": receiver_agent,
                "session_id": session_id,
                "inbox_level": inbox_level,
                "first_rejected_at": now,
                "last_rejected_at": now,
                "unread_count_at_rejection": unread_count,
                "limit": limit,
                "senders": [],
            },
        )
        record["receiver_agent"] = receiver_agent
        record["session_id"] = session_id
        record["inbox_level"] = inbox_level
        record["last_rejected_at"] = now
        record["unread_count_at_rejection"] = unread_count
        record["limit"] = limit
        sender_session = (sender_session_id or "").strip() or None
        sender_entry = {
            "sender_agent": sender_agent,
            "sender_session_id": sender_session,
            "reason": reason,
            "last_rejected_at": now,
        }
        senders = [
            item
            for item in record.get("senders", [])
            if not (
                isinstance(item, dict)
                and item.get("sender_agent") == sender_agent
                and item.get("sender_session_id") == sender_session
            )
        ]
        senders.append(sender_entry)
        record["senders"] = senders[-20:]
        return copy.deepcopy(record)

    def _backpressure_blocked_status(
        self,
        state: Dict[str, Any],
        registry: Dict[str, Any],
        *,
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        pending = state.get("backpressure_pending", {}) if isinstance(state, dict) else {}
        if not isinstance(pending, dict):
            pending = {}
        blocked_items: List[Dict[str, Any]] = []
        warning_items: List[Dict[str, Any]] = []
        seen_keys = set()

        def _oldest_created_at(rows: List[Dict[str, Any]]) -> Optional[datetime]:
            oldest = None
            for row in rows:
                parsed = parse_iso_datetime(row.get("created_at"))
                if parsed is None:
                    continue
                if oldest is None or parsed < oldest:
                    oldest = parsed
            return oldest

        def _append_pressure_item(
            *,
            status: str,
            receiver: str,
            session_id: str,
            info: Dict[str, Any],
            unread_count: int,
            limit: int,
            unread_work: List[Dict[str, Any]],
            senders: Optional[List[Dict[str, Any]]] = None,
            record: Optional[Dict[str, Any]] = None,
        ) -> None:
            senders = [item for item in (senders or []) if isinstance(item, dict)]
            primary_sender = senders[-1] if senders else {}
            blocked_sender_agent = primary_sender.get("sender_agent")
            blocked_sender_session = primary_sender.get("sender_session_id")
            oldest_created_at = _oldest_created_at(unread_work)
            item = {
                "status": status,
                "receiver_agent": receiver,
                "receiver_session_id": session_id,
                "inbox_level": (record or {}).get("inbox_level") or info.get("inbox_level"),
                "project": info.get("project"),
                "blocked_sender_agent": blocked_sender_agent,
                "blocked_sender_session": blocked_sender_session,
                "blocked_senders": senders,
                "unread_work_count": unread_count,
                "limit": int(limit),
                "warning_threshold": self._backpressure_warning_threshold(int(limit)),
                "oldest_unread_created_at": oldest_created_at.isoformat(timespec="seconds") if oldest_created_at else None,
                "blocked_since": (record or {}).get("first_rejected_at"),
                "last_rejected_at": (record or {}).get("last_rejected_at"),
                "recommendation": (
                    "Run dry-run receipt cleanup for %s bucket %s, then read and disposition the blocking work to unblock %s."
                    % (receiver, session_id, blocked_sender_agent or "sender")
                    if status == "BACKPRESSURE_BLOCKED"
                    else "Read and disposition %s bucket %s soon; it is approaching the backpressure limit."
                    % (receiver, session_id)
                ),
            }
            if status == "BACKPRESSURE_BLOCKED":
                item.update(
                    {
                        "remediation_command": 'receipt_debt_cleanup(agent="%s", apply=false, rearm_stale_unread=true)'
                        % receiver,
                        "remediation_safe_to_run": True,
                        "remediation_mutates_state": False,
                    }
                )
            if status == "BACKPRESSURE_BLOCKED":
                blocked_items.append(item)
            else:
                warning_items.append(item)

        inbox_cache: Dict[str, List[Dict[str, Any]]] = {}

        def _unread_for_bucket(receiver: str, session_id: str) -> List[Dict[str, Any]]:
            if receiver not in inbox_cache:
                read = self._health_read_jsonl(self.inbox_path(receiver), "backpressure_inbox")
                inbox_cache[receiver] = read.get("rows", [])
            return [
                row
                for row in inbox_cache.get(receiver, [])
                if str(row.get("session_id") or DEFAULT_SESSION_ID) == session_id
                and self._row_counts_for_backpressure(registry, receiver, row)
            ]

        for record in pending.values():
            if not isinstance(record, dict):
                continue
            receiver = str(record.get("receiver_agent") or "").strip().lower()
            if receiver not in AGENTS:
                continue
            session_id = str(record.get("session_id") or DEFAULT_SESSION_ID)
            info = self._bucket_info(registry, session_id)
            if project_name and info.get("project") != project_name and session_id != project_name:
                continue
            limit = record.get("limit")
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = self._backpressure_limit_for_bucket(registry, session_id)
            if limit is None:
                continue
            unread_work = _unread_for_bucket(receiver, session_id)
            unread_count = len(unread_work)
            if unread_count < int(limit):
                continue
            senders = [item for item in record.get("senders", []) if isinstance(item, dict)]
            seen_keys.add((receiver, session_id))
            _append_pressure_item(
                status="BACKPRESSURE_BLOCKED",
                receiver=receiver,
                session_id=session_id,
                info=info,
                unread_count=unread_count,
                limit=int(limit),
                unread_work=unread_work,
                senders=senders,
                record=record,
            )

        for receiver in sorted(AGENTS):
            _ = _unread_for_bucket(receiver, "")
            buckets = self._unread_work_counts_by_bucket(
                inbox_cache.get(receiver, []),
                registry,
                receiver,
            )
            for session_id, unread_count in sorted(buckets.items()):
                if (receiver, session_id) in seen_keys:
                    continue
                info = self._bucket_info(registry, session_id)
                if project_name and info.get("project") != project_name and session_id != project_name:
                    continue
                limit = self._backpressure_limit_for_bucket(registry, session_id)
                if limit is None:
                    continue
                threshold = self._backpressure_warning_threshold(limit)
                unread_work = _unread_for_bucket(receiver, session_id)
                if unread_count >= limit:
                    _append_pressure_item(
                        status="BACKPRESSURE_BLOCKED",
                        receiver=receiver,
                        session_id=session_id,
                        info=info,
                        unread_count=unread_count,
                        limit=limit,
                        unread_work=unread_work,
                    )
                elif unread_count >= threshold:
                    _append_pressure_item(
                        status="BACKPRESSURE_WARN",
                        receiver=receiver,
                        session_id=session_id,
                        info=info,
                        unread_count=unread_count,
                        limit=limit,
                        unread_work=unread_work,
                    )
        items = blocked_items + warning_items
        return {
            "status": "blocked" if blocked_items else ("warning" if warning_items else "ok"),
            "blocked_count": len(blocked_items),
            "warning_count": len(warning_items),
            "blocked_sender_count": sum(len(item.get("blocked_senders") or []) for item in blocked_items),
            "items": items,
        }

    def _resolve_backpressure_after_read(
        self,
        state: Dict[str, Any],
        registry: Dict[str, Any],
        *,
        receiver_agent: str,
        before_counts: Dict[str, int],
        after_counts: Dict[str, int],
        read_rows: List[Dict[str, Any]],
        via: str,
    ) -> List[Dict[str, Any]]:
        pending = state.setdefault("backpressure_pending", {})
        if not isinstance(pending, dict):
            state["backpressure_pending"] = {}
            pending = state["backpressure_pending"]
        read_buckets = {str(row.get("session_id") or DEFAULT_SESSION_ID) for row in read_rows}
        resolutions: List[Dict[str, Any]] = []
        for bucket in sorted(read_buckets):
            key = self._backpressure_key(receiver_agent, bucket)
            record = pending.get(key)
            if not isinstance(record, dict):
                continue
            limit = self._backpressure_limit_for_bucket(registry, bucket)
            if limit is None:
                continue
            before = before_counts.get(bucket, 0)
            after = after_counts.get(bucket, 0)
            if before < limit or after >= limit:
                continue
            notified: List[Dict[str, Any]] = []
            for sender_record in record.get("senders", []):
                if not isinstance(sender_record, dict):
                    continue
                try:
                    sender = normalize_agent(sender_record.get("sender_agent"))
                except ValueError:
                    continue
                reply_session = (sender_record.get("sender_session_id") or "").strip()
                if not reply_session:
                    reply_session = self._resolve_default_session(sender)
                try:
                    reply_session = normalize_session(reply_session)
                except ValueError:
                    continue
                if reply_session == DEFAULT_SESSION_ID:
                    continue
                reply_info = self._bucket_info(registry, reply_session)
                body = (
                    "Backpressure has cleared for %s bucket %s.\n\n"
                    "RECEIVER_AGENT: %s\n"
                    "BUCKET: %s\n"
                    "READ_VIA: %s\n"
                    "UNREAD_WORK_BEFORE: %d\n"
                    "UNREAD_WORK_AFTER: %d\n"
                    "LIMIT: %d\n"
                    "FIRST_REJECTED_AT: %s\n"
                    "LAST_REJECTED_AT: %s\n"
                ) % (
                    receiver_agent,
                    bucket,
                    receiver_agent,
                    bucket,
                    via,
                    before,
                    after,
                    limit,
                    record.get("first_rejected_at"),
                    record.get("last_rejected_at"),
                )
                sent = self._enqueue_control_message(
                    from_agent=receiver_agent,
                    to_agent=sender,
                    session_id=reply_session,
                    control_type="BACKPRESSURE_RESOLVED",
                    summary="Backpressure cleared for %s bucket %s" % (receiver_agent, bucket),
                    body=body,
                    status="info",
                    replace_existing_control=True,
                    inbox_level=reply_info.get("inbox_level"),
                    parent_project=reply_info.get("parent_project"),
                )
                notified.append(
                    {
                        "sender_agent": sender,
                        "sender_session_id": reply_session,
                        "control_status": sent.status,
                        "message_id": sent.data.get("id") if sent.ok else None,
                        "catchup_digest": self._send_catchup_digest_unlocked(
                            from_agent=sender,
                            to_agent=receiver_agent,
                            target_session_id=bucket,
                            reason="backpressure_resolved",
                        ),
                    }
                )
            pending.pop(key, None)
            resolution = {
                "receiver_agent": receiver_agent,
                "session_id": bucket,
                "via": via,
                "unread_work_before": before,
                "unread_work_after": after,
                "limit": limit,
                "notified": notified,
            }
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "backpressure_resolved",
                    "accepted": True,
                    **resolution,
                }
            )
            resolutions.append(resolution)
        return resolutions

    def _self_heal_stale_backpressure_pending(
        self,
        state: Dict[str, Any],
        registry: Dict[str, Any],
        *,
        receiver_agent: str,
        via: str,
    ) -> List[Dict[str, Any]]:
        pending = state.setdefault("backpressure_pending", {})
        if not isinstance(pending, dict):
            state["backpressure_pending"] = {}
            return []
        receiver = normalize_agent(receiver_agent)
        inbox_rows = self.transport.read_inbox(
            self._identity(receiver, None),
            self.inbox_path(receiver),
            unread_only=False,
        )
        healed: List[Dict[str, Any]] = []
        for key, record in list(pending.items()):
            if not isinstance(record, dict):
                continue
            if str(record.get("receiver_agent") or "").strip().lower() != receiver:
                continue
            bucket = str(record.get("session_id") or DEFAULT_SESSION_ID)
            info = self._bucket_info(registry, bucket)
            limit = record.get("limit")
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = self._backpressure_limit_for_bucket(registry, bucket)
            if limit is None:
                continue
            unread_work = [
                row
                for row in inbox_rows
                if str(row.get("session_id") or DEFAULT_SESSION_ID) == bucket
                and self._row_counts_for_backpressure(registry, receiver, row)
            ]
            unread_count = len(unread_work)
            session_record = info.get("record") if info.get("inbox_level") == INBOX_LEVEL_SESSION else None
            session_inactive = bool(
                session_record
                and (
                    session_record.get("status") != "active"
                    or info.get("active_session") != bucket
                )
            )
            if not session_inactive and unread_count >= int(limit):
                continue
            reason = "session_not_active" if session_inactive else "unread_below_limit"
            pending.pop(key, None)
            resolution = {
                "receiver_agent": receiver,
                "session_id": bucket,
                "via": via,
                "reason": reason,
                "unread_work_after": unread_count,
                "limit": int(limit),
                "first_rejected_at": record.get("first_rejected_at"),
                "last_rejected_at": record.get("last_rejected_at"),
            }
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "backpressure_self_healed",
                    "accepted": True,
                    **resolution,
                }
            )
            healed.append(resolution)
        return healed

    def _audit(self, event: Dict[str, Any]) -> None:
        agent = str(event.get("from") or event.get("agent") or "codex").strip().lower()
        if agent not in AGENTS:
            agent = "codex"
        identity = self._identity(agent, None)
        self.transport.append_audit(identity, self.audit_path, event)

    def _review_loop_request_for_peer_result(self, peer_result_message_id: str) -> Optional[str]:
        target_id = (peer_result_message_id or "").strip()
        if not target_id:
            return None
        for row in reversed(read_jsonl(self.review_loop_state_path)):
            if (
                row.get("event_type") == "peer_replied"
                and row.get("peer_result_message_id") == target_id
                and row.get("request_message_id")
            ):
                return str(row.get("request_message_id"))
        return None

    def _append_review_loop_event(
        self,
        event_type: str,
        *,
        owner_agent: str,
        peer_agent: str,
        request_message_id: Optional[str] = None,
        peer_result_message_id: Optional[str] = None,
        closeout_message_id: Optional[str] = None,
        session_id: Optional[str] = None,
        owner_session_id: Optional[str] = None,
        subject: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        request_id = (request_message_id or "").strip()
        peer_result_id = (peer_result_message_id or "").strip()
        if not request_id and peer_result_id:
            request_id = self._review_loop_request_for_peer_result(peer_result_id) or ""
        if not request_id:
            return
        row = {
            "schema_version": REVIEW_LOOP_SCHEMA_VERSION,
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "review_loop_id": request_id,
            "request_message_id": request_id,
            "peer_result_message_id": peer_result_id or None,
            "closeout_message_id": (closeout_message_id or "").strip() or None,
            "owner_agent": owner_agent,
            "owner_session_id": (owner_session_id or "").strip() or None,
            "peer_agent": peer_agent,
            "session_id": session_id,
            "subject": (subject or "").strip() or None,
            "status": status or event_type,
            "created_at": utc_now(),
        }
        append_jsonl(self.review_loop_state_path, row)

    def _record_review_loop_send(
        self,
        *,
        sender: str,
        target: str,
        body: str,
        message_id: str,
        session_id: str,
        sender_session_id: Optional[str] = None,
    ) -> None:
        message_type = bridge_message_type(body)
        in_reply_to = bridge_message_in_reply_to(body)
        subject = bridge_message_subject(body)
        if message_type in REVIEW_REQUEST_TYPES:
            self._append_review_loop_event(
                "review_requested",
                owner_agent=sender,
                peer_agent=target,
                request_message_id=message_id,
                session_id=session_id,
                owner_session_id=sender_session_id,
                subject=subject,
                status="requested",
            )
            return
        if message_type in REVIEW_RESULT_TYPES and in_reply_to:
            self._append_review_loop_event(
                "peer_replied",
                owner_agent=target,
                peer_agent=sender,
                request_message_id=in_reply_to,
                peer_result_message_id=message_id,
                session_id=session_id,
                owner_session_id=session_id,
                subject=subject,
                status="peer_replied",
            )
            return
        if message_type in REVIEW_CLOSEOUT_TYPES and in_reply_to:
            self._append_review_loop_event(
                "closeout_sent",
                owner_agent=sender,
                peer_agent=target,
                request_message_id=self._review_loop_request_for_peer_result(in_reply_to),
                peer_result_message_id=in_reply_to,
                closeout_message_id=message_id,
                session_id=session_id,
                owner_session_id=sender_session_id,
                subject=subject,
                status="closed",
            )

    def _record_review_result_handled(
        self,
        *,
        handler_agent: str,
        row: Dict[str, Any],
        handled_status: str,
    ) -> None:
        body = str(row.get("body") or "")
        message_type = bridge_message_type(body)
        in_reply_to = bridge_message_in_reply_to(body)
        if message_type not in REVIEW_RESULT_TYPES or not in_reply_to:
            return
        self._append_review_loop_event(
            "peer_result_handled",
            owner_agent=handler_agent,
            peer_agent=str(row.get("from") or ""),
            request_message_id=in_reply_to,
            peer_result_message_id=str(row.get("id") or ""),
            session_id=str(row.get("session_id") or ""),
            owner_session_id=str(row.get("session_id") or ""),
            subject=bridge_message_subject(body),
            status=handled_status,
        )

    def _append_reviewer_wait_event(
        self,
        event_type: str,
        *,
        owner_agent: str,
        reviewer_id: str,
        wait_id: Optional[str] = None,
        request_id: Optional[str] = None,
        owner_session_id: Optional[str] = None,
        subject: Optional[str] = None,
        eta_at: Optional[str] = None,
        checkback_due_at: Optional[str] = None,
        result: Optional[str] = None,
        status: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now()
        resolved_wait_id = (wait_id or "").strip() or str(uuid.uuid4())
        row = {
            "schema_version": REVIEWER_WAIT_SCHEMA_VERSION,
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "wait_id": resolved_wait_id,
            "owner_agent": owner_agent,
            "owner_session_id": (owner_session_id or "").strip() or None,
            "reviewer_id": (reviewer_id or "").strip(),
            "request_id": (request_id or "").strip() or None,
            "subject": (subject or "").strip() or None,
            "eta_at": (eta_at or "").strip() or None,
            "checkback_due_at": (checkback_due_at or "").strip() or None,
            "result": (result or "").strip() or None,
            "status": (status or event_type).strip().lower(),
            "note": (note or "").strip() or None,
            "created_at": now,
        }
        append_jsonl(self.reviewer_wait_state_path, row)
        return row

    def _reviewer_wait_eta_stamp(self, eta_minutes: Optional[int]) -> Optional[str]:
        if eta_minutes is None:
            return None
        if isinstance(eta_minutes, bool) or not isinstance(eta_minutes, int):
            raise ValueError("eta_minutes must be an integer")
        if eta_minutes < 0 or eta_minutes > 24 * 60:
            raise ValueError("eta_minutes must be between 0 and 1440")
        return (datetime.now(timezone.utc) + timedelta(minutes=eta_minutes)).isoformat(timespec="seconds")

    def record_reviewer_wait(
        self,
        owner_agent: str,
        reviewer_id: str,
        *,
        request_id: Optional[str] = None,
        owner_session_id: Optional[str] = None,
        subject: Optional[str] = None,
        eta_minutes: Optional[int] = None,
        note: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
                eta_at = self._reviewer_wait_eta_stamp(eta_minutes)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            reviewer = (reviewer_id or "").strip()
            if not reviewer:
                return BridgeResult(False, "rejected", "reviewer_id is required")
            event = self._append_reviewer_wait_event(
                "wait_started",
                owner_agent=owner,
                owner_session_id=owner_session_id,
                reviewer_id=reviewer,
                request_id=request_id,
                subject=subject,
                eta_at=eta_at,
                checkback_due_at=eta_at,
                status="eta_recorded" if eta_at else "waiting_for_eta",
                note=note,
            )
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": event["created_at"],
                    "action": "record_reviewer_wait",
                    "owner_agent": owner,
                    "reviewer_id": reviewer,
                    "wait_id": event["wait_id"],
                    "status": event["status"],
                    "accepted": True,
                }
            )
            return BridgeResult(True, "recorded", "Recorded reviewer wait %s." % event["wait_id"], {"wait": event})

    def update_reviewer_wait(
        self,
        wait_id: str,
        event_type: str,
        *,
        owner_agent: str = "codex",
        reviewer_id: Optional[str] = None,
        eta_minutes: Optional[int] = None,
        result: Optional[str] = None,
        note: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
                eta_at = self._reviewer_wait_eta_stamp(eta_minutes)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            resolved_wait_id = (wait_id or "").strip()
            if not resolved_wait_id:
                return BridgeResult(False, "rejected", "wait_id is required")
            kind = (event_type or "").strip().lower()
            status_map = {
                "eta_requested": "waiting_for_eta",
                "eta_recorded": "eta_recorded",
                "checkback_requested": "checkback_requested",
                "verdict_received": "verdict_received",
                "parked": "parked",
                "blocked": "blocked",
                "cancelled": "cancelled",
            }
            if kind not in status_map:
                return BridgeResult(False, "rejected", "event_type must be eta_requested, eta_recorded, checkback_requested, verdict_received, parked, blocked, or cancelled")
            prior: Optional[Dict[str, Any]] = None
            for row in read_jsonl(self.reviewer_wait_state_path):
                if row.get("owner_agent") == owner and row.get("wait_id") == resolved_wait_id:
                    if prior is None:
                        prior = {}
                    prior["reviewer_id"] = row.get("reviewer_id") or prior.get("reviewer_id")
                    prior["request_id"] = row.get("request_id") or prior.get("request_id")
                    prior["subject"] = row.get("subject") or prior.get("subject")
                    prior["owner_session_id"] = row.get("owner_session_id") or prior.get("owner_session_id")
            reviewer = (reviewer_id or "").strip() or (prior or {}).get("reviewer_id") or "unknown"
            if kind in {"eta_recorded", "checkback_requested"} and eta_at is None:
                return BridgeResult(False, "rejected", "eta_minutes is required for eta_recorded/checkback_requested")
            if kind == "verdict_received" and not (result or "").strip():
                return BridgeResult(False, "rejected", "result is required for verdict_received")
            event = self._append_reviewer_wait_event(
                kind,
                owner_agent=owner,
                owner_session_id=(prior or {}).get("owner_session_id"),
                reviewer_id=reviewer,
                wait_id=resolved_wait_id,
                request_id=(prior or {}).get("request_id"),
                subject=(prior or {}).get("subject"),
                eta_at=eta_at,
                checkback_due_at=eta_at,
                result=result,
                status=status_map[kind],
                note=note,
            )
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": event["created_at"],
                    "action": "update_reviewer_wait",
                    "owner_agent": owner,
                    "reviewer_id": reviewer,
                    "wait_id": resolved_wait_id,
                    "status": event["status"],
                    "accepted": True,
                }
            )
            return BridgeResult(True, event["status"], "Updated reviewer wait %s." % resolved_wait_id, {"wait": event})

    def reviewer_wait_status(
        self,
        owner_agent: str = "codex",
        *,
        include_all: bool = False,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            waits: Dict[str, Dict[str, Any]] = {}
            for row in read_jsonl(self.reviewer_wait_state_path):
                if row.get("owner_agent") != owner:
                    continue
                wait_id = str(row.get("wait_id") or "")
                if not wait_id:
                    continue
                current = waits.setdefault(
                    wait_id,
                    {
                        "wait_id": wait_id,
                        "owner_agent": owner,
                        "reviewer_id": row.get("reviewer_id"),
                        "request_id": row.get("request_id"),
                        "owner_session_id": row.get("owner_session_id"),
                        "subject": row.get("subject"),
                        "created_at": row.get("created_at"),
                        "status": row.get("status") or row.get("event_type"),
                        "eta_at": row.get("eta_at"),
                        "checkback_due_at": row.get("checkback_due_at"),
                        "result": row.get("result"),
                        "latest_event_at": row.get("created_at"),
                        "latest_event_type": row.get("event_type"),
                    },
                )
                current["reviewer_id"] = row.get("reviewer_id") or current.get("reviewer_id")
                current["request_id"] = row.get("request_id") or current.get("request_id")
                current["owner_session_id"] = row.get("owner_session_id") or current.get("owner_session_id")
                current["subject"] = row.get("subject") or current.get("subject")
                current["status"] = row.get("status") or row.get("event_type") or current.get("status")
                current["eta_at"] = row.get("eta_at") or current.get("eta_at")
                current["checkback_due_at"] = row.get("checkback_due_at") or current.get("checkback_due_at")
                current["result"] = row.get("result") or current.get("result")
                current["latest_event_at"] = row.get("created_at") or current.get("latest_event_at")
                current["latest_event_type"] = row.get("event_type") or current.get("latest_event_type")
            now_dt = datetime.now(timezone.utc)
            terminal = {"verdict_received", "parked", "blocked", "cancelled"}
            debts: List[Dict[str, Any]] = []
            active: List[Dict[str, Any]] = []
            for wait in waits.values():
                status = str(wait.get("status") or "").strip().lower()
                if status in terminal:
                    continue
                due_raw = wait.get("checkback_due_at") or wait.get("eta_at")
                due_at = parse_iso_datetime(due_raw)
                if not due_at:
                    debt_reason = "missing_eta_or_checkback"
                    wait["debt_reason"] = debt_reason
                    debts.append(copy.deepcopy(wait))
                elif due_at <= now_dt:
                    debt_reason = "checkback_due"
                    wait["debt_reason"] = debt_reason
                    debts.append(copy.deepcopy(wait))
                else:
                    wait["next_check_at"] = due_at.isoformat(timespec="seconds")
                    active.append(copy.deepcopy(wait))
            returned = list(waits.values()) if include_all else debts
            return BridgeResult(
                True,
                "reviewer_wait_debt" if debts else "ok",
                "%d reviewer wait debt item(s)." % len(debts),
                {
                    "owner_agent": owner,
                    "debt_count": len(debts),
                    "active_scheduled_count": len(active),
                    "debts": debts,
                    "active_scheduled": active,
                    "waits": returned,
                },
            )

    def _identity(self, agent: str, session_id: Optional[str]) -> Identity:
        identity = self.auth.authenticate({"agent": agent, "session_id": session_id})
        self.auth.authorize(identity, "bridge_op", {"tenant_id": identity.tenant_id})
        return identity

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

    def _default_implementation_journal(self) -> Dict[str, Any]:
        return {
            "schema_version": IMPLEMENTATION_JOURNAL_SCHEMA_VERSION,
            "next_sequence": 1,
            "events": [],
            "peer_states": {},
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

    def _load_implementation_journal(self) -> Dict[str, Any]:
        try:
            payload = read_json(self.implementation_journal_path, self._default_implementation_journal())
        except (JSONDecodeError, OSError):
            corrupt_path = self.implementation_journal_path.with_name(
                "implementation-journal.corrupt.%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            if self.implementation_journal_path.exists():
                self.implementation_journal_path.replace(corrupt_path)
            payload = self._default_implementation_journal()
        payload["schema_version"] = max(
            int(payload.get("schema_version") or 1),
            IMPLEMENTATION_JOURNAL_SCHEMA_VERSION,
        )
        if not isinstance(payload.get("events"), list):
            payload["events"] = []
        if not isinstance(payload.get("peer_states"), dict):
            payload["peer_states"] = {}
        try:
            payload["next_sequence"] = max(1, int(payload.get("next_sequence") or 1))
        except (TypeError, ValueError):
            payload["next_sequence"] = 1
        return payload

    def _save_implementation_journal(self, payload: Dict[str, Any]) -> None:
        payload["updated_at"] = utc_now()
        payload["events"] = list(payload.get("events") or [])[-IMPLEMENTATION_JOURNAL_MAX_EVENTS:]
        write_json(self.implementation_journal_path, payload)

    def _journal_pair_key(self, owner_agent: str, peer_agent: str) -> str:
        return "%s->%s" % (owner_agent, peer_agent)

    def _journal_peer_state(self, journal: Dict[str, Any], owner_agent: str, peer_agent: str) -> Dict[str, Any]:
        states = journal.setdefault("peer_states", {})
        return states.setdefault(
            self._journal_pair_key(owner_agent, peer_agent),
            {
                "owner_agent": owner_agent,
                "peer_agent": peer_agent,
                "last_ack_sequence": 0,
                "last_digest_sequence": 0,
                "last_digest_at": None,
            },
        )

    def _extract_bridge_field(self, body: str, field: str) -> Optional[str]:
        match = re.search(r"(?im)^%s:\s*(.+?)\s*$" % re.escape(field), body or "")
        return match.group(1).strip() if match else None

    def _implementation_message_type(self, body: str) -> Optional[str]:
        value = self._extract_bridge_field(body, "TYPE")
        if not value:
            return None
        message_type = value.split()[0].strip().upper()
        return message_type if message_type in IMPLEMENTATION_JOURNAL_MESSAGE_TYPES else None

    def _implementation_event_summary(self, body: str) -> str:
        summary = self._extract_bridge_field(body, "SUMMARY")
        if summary:
            return summary[:240]
        for line in (body or "").splitlines():
            stripped = line.strip()
            if stripped and not stripped.upper().startswith(("TYPE:", "STATUS:", "ACTION_REQUESTED:")):
                return stripped[:240]
        return "Implementation progress update"

    def _implementation_event_commit(self, body: str) -> Optional[str]:
        explicit = self._extract_bridge_field(body, "COMMIT") or self._extract_bridge_field(body, "Commit")
        if explicit:
            token = explicit.split()[0].strip()
            if re.fullmatch(r"[0-9a-fA-F]{7,40}", token):
                return token
        match = re.search(r"(?i)\bcommit[:\s]+([0-9a-f]{7,40})\b", body or "")
        return match.group(1) if match else None

    def _record_implementation_event_unlocked(
        self,
        journal: Dict[str, Any],
        *,
        owner_agent: str,
        peer_agent: str,
        message_type: str,
        summary: str,
        commit: Optional[str] = None,
        tests: Optional[List[str]] = None,
        details: Optional[str] = None,
        related_session_id: Optional[str] = None,
        body: Optional[str] = None,
        delivery_status: str = "manual",
        delivery_session_id: Optional[str] = None,
        delivery_message_id: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        sequence = int(journal.get("next_sequence") or 1)
        journal["next_sequence"] = sequence + 1
        event = {
            "id": str(uuid.uuid4()),
            "sequence": sequence,
            "created_at": utc_now(),
            "owner_agent": owner_agent,
            "peer_agent": peer_agent,
            "message_type": message_type,
            "summary": summary.strip()[:240] or "Implementation progress update",
            "commit": (commit or "").strip() or None,
            "tests": tests or [],
            "details": (details or "").strip() or None,
            "related_session_id": related_session_id,
            "body_hash": sha256_text(body or "") if body is not None else None,
            "body_preview": (body or "")[:1000] if body is not None else None,
            "delivery_status": delivery_status,
            "delivery_session_id": delivery_session_id,
            "delivery_message_id": delivery_message_id,
            "rejection_reason": rejection_reason,
            "updated_at": utc_now(),
        }
        journal.setdefault("events", []).append(event)
        self._journal_peer_state(journal, owner_agent, peer_agent)
        return event

    def _update_implementation_event_delivery_unlocked(
        self,
        journal: Dict[str, Any],
        sequence: Optional[int],
        *,
        delivery_status: str,
        delivery_session_id: Optional[str] = None,
        delivery_message_id: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ) -> None:
        if sequence is None:
            return
        for event in journal.get("events", []):
            if isinstance(event, dict) and event.get("sequence") == sequence:
                event["delivery_status"] = delivery_status
                if delivery_session_id is not None:
                    event["delivery_session_id"] = delivery_session_id
                if delivery_message_id is not None:
                    event["delivery_message_id"] = delivery_message_id
                if rejection_reason is not None:
                    event["rejection_reason"] = rejection_reason
                event["updated_at"] = utc_now()
                return

    def _ack_implementation_sequences_unlocked(
        self,
        journal: Dict[str, Any],
        *,
        rows: List[Dict[str, Any]],
        reader_agent: str,
    ) -> bool:
        changed = False
        for row in rows:
            try:
                sender = normalize_agent(row.get("from"))
            except ValueError:
                continue
            sequence = row.get("implementation_journal_sequence") or row.get("catchup_to_sequence")
            try:
                sequence_int = int(sequence)
            except (TypeError, ValueError):
                continue
            state = self._journal_peer_state(journal, sender, reader_agent)
            if sequence_int > int(state.get("last_ack_sequence") or 0):
                state["last_ack_sequence"] = sequence_int
                state["last_ack_at"] = utc_now()
                changed = True
        return changed

    def _ack_implementation_rows_unlocked(
        self,
        *,
        rows: List[Dict[str, Any]],
        reader_agent: str,
    ) -> Dict[str, Any]:
        journal = self._load_implementation_journal()
        changed = self._ack_implementation_sequences_unlocked(
            journal,
            rows=rows,
            reader_agent=reader_agent,
        )
        if changed:
            self._save_implementation_journal(journal)
        return {"changed": changed}

    def _render_catchup_digest(
        self,
        *,
        owner_agent: str,
        peer_agent: str,
        events: List[Dict[str, Any]],
        since_sequence: int,
        omitted_count: int,
        reason: str,
    ) -> str:
        highest = max([int(event.get("sequence") or 0) for event in events] or [since_sequence])
        lines = [
            "CATCHUP_FOR: %s" % peer_agent,
            "FROM_AGENT: %s" % owner_agent,
            "SINCE_SEQUENCE: %d" % since_sequence,
            "TO_SEQUENCE: %d" % highest,
            "EVENT_COUNT: %d" % len(events),
            "OMITTED_OLDER_COUNT: %d" % omitted_count,
            "REASON: %s" % reason,
            "",
            "Implementation catch-up digest:",
        ]
        if omitted_count:
            lines.append("- %d older undigested event(s) omitted from this bounded digest; inspect implementation_journal for full history." % omitted_count)
        for event in events:
            parts = [
                "- #%s %s" % (event.get("sequence"), event.get("summary")),
            ]
            if event.get("commit"):
                parts.append("commit=%s" % event.get("commit"))
            if event.get("delivery_status"):
                parts.append("delivery=%s" % event.get("delivery_status"))
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def _send_catchup_digest_unlocked(
        self,
        *,
        from_agent: str,
        to_agent: str,
        target_session_id: str,
        reason: str,
        max_items: int = IMPLEMENTATION_JOURNAL_DIGEST_MAX_ITEMS,
    ) -> Dict[str, Any]:
        journal = self._load_implementation_journal()
        state = self._journal_peer_state(journal, from_agent, to_agent)
        since_sequence = int(state.get("last_ack_sequence") or 0)
        events = [
            event
            for event in journal.get("events", [])
            if isinstance(event, dict)
            and event.get("owner_agent") == from_agent
            and event.get("peer_agent") == to_agent
            and int(event.get("sequence") or 0) > since_sequence
        ]
        if not events:
            return {
                "status": "skipped",
                "reason": "no_unacked_implementation_events",
                "from_agent": from_agent,
                "to_agent": to_agent,
                "target_session_id": target_session_id,
                "since_sequence": since_sequence,
            }
        events = sorted(events, key=lambda item: int(item.get("sequence") or 0))
        omitted = max(0, len(events) - max_items)
        selected = events[-max_items:]
        to_sequence = max(int(event.get("sequence") or 0) for event in selected)
        target_info = self._bucket_info(self._load_session_registry(), target_session_id)
        sent = self._enqueue_control_message(
            from_agent=from_agent,
            to_agent=to_agent,
            session_id=target_session_id,
            control_type="CATCHUP_DIGEST",
            summary="Implementation catch-up digest from %s" % from_agent,
            body=self._render_catchup_digest(
                owner_agent=from_agent,
                peer_agent=to_agent,
                events=selected,
                since_sequence=since_sequence,
                omitted_count=omitted,
                reason=reason,
            ),
            status="info",
            replace_existing_control=True,
            inbox_level=target_info.get("inbox_level"),
            parent_project=target_info.get("parent_project"),
            extra_fields={
                "catchup_from_sequence": since_sequence + 1,
                "catchup_to_sequence": to_sequence,
                "catchup_event_count": len(selected),
                "catchup_omitted_count": omitted,
                "catchup_reason": reason,
            },
        )
        if sent.ok:
            state["last_digest_sequence"] = to_sequence
            state["last_digest_at"] = utc_now()
            self._save_implementation_journal(journal)
        return {
            "status": sent.status,
            "ok": sent.ok,
            "message_id": sent.data.get("id") if sent.ok else None,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "target_session_id": target_session_id,
            "since_sequence": since_sequence,
            "to_sequence": to_sequence,
            "event_count": len(selected),
            "omitted_count": omitted,
            "reason": sent.message,
        }

    def _session_hint_from_handshake_row(self, row: Dict[str, Any], registry: Dict[str, Any]) -> Optional[str]:
        body = row.get("body")
        if isinstance(body, str):
            try:
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get("session_id"):
                    return normalize_session(str(payload["session_id"]))
            except Exception:
                pass
        sender = row.get("from")
        project = row.get("parent_project") or row.get("session_id")
        if sender in AGENTS and project:
            project_entry = (registry.get("projects") or {}).get(str(project), {})
            active = (project_entry.get("active") or {}).get(sender)
            if active:
                return normalize_session(str(active))
        return None

    def _send_catchup_for_handshakes_unlocked(
        self,
        *,
        receiver_agent: str,
        rows: List[Dict[str, Any]],
        registry: Dict[str, Any],
        via: str,
    ) -> List[Dict[str, Any]]:
        digests: List[Dict[str, Any]] = []
        for row in rows:
            if row.get("marker_variant") != "control" or row.get("control_type") != "HANDSHAKE":
                continue
            try:
                sender = normalize_agent(row.get("from"))
            except ValueError:
                continue
            target_session = self._session_hint_from_handshake_row(row, registry)
            if not target_session:
                continue
            digest = self._send_catchup_digest_unlocked(
                from_agent=receiver_agent,
                to_agent=sender,
                target_session_id=target_session,
                reason="handshake_%s" % via,
            )
            digests.append(digest)
        return digests

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
            project_entry.setdefault("pairs", {})
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
                record.setdefault("tenant_id", LOCAL_DEFAULT_TENANT_ID)
        for project_name, project_entry in registry.get("projects", {}).items():
            if isinstance(project_entry, dict):
                self._ensure_primary_pair_record(project_entry, str(project_name))
        return registry

    def _dashboard_session_registry(self) -> Dict[str, Any]:
        read = self._health_read_json(self.session_registry_path, self._default_session_registry(), "session_registry")
        if read["status"] == "error":
            return {
                "status": "error",
                "registry": self._default_session_registry(),
                "error": read["error"],
                "path": read["path"],
            }
        registry = copy.deepcopy(read["data"]) if isinstance(read["data"], dict) else self._default_session_registry()
        try:
            registry["schema_version"] = max(int(registry.get("schema_version") or 1), SESSION_REGISTRY_SCHEMA_VERSION)
        except (TypeError, ValueError):
            registry["schema_version"] = SESSION_REGISTRY_SCHEMA_VERSION
        if not isinstance(registry.get("projects"), dict):
            registry["projects"] = {}
        for project_name, project_entry in registry.get("projects", {}).items():
            if not isinstance(project_entry, dict):
                continue
            project_entry.setdefault("active", {})
            project_entry.setdefault("sessions", {})
            project_entry.setdefault("pairs", {})
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
                record.setdefault("tenant_id", LOCAL_DEFAULT_TENANT_ID)
            self._ensure_primary_pair_record(project_entry, str(project_name))
        return {
            "status": read["status"],
            "registry": registry,
            "error": read["error"],
            "path": read["path"],
        }

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
                "pairs": {},
                "trusted_parent": {
                    "claude": {"session_id": None, "promoted_at": None},
                    "codex": {"session_id": None, "promoted_at": None},
                },
                "updated_at": utc_now(),
            },
        )
        project_entry.setdefault("active", {})
        project_entry.setdefault("sessions", {})
        project_entry.setdefault("pairs", {})
        trusted = project_entry.setdefault("trusted_parent", {})
        for agent in sorted(AGENTS):
            trusted.setdefault(agent, {"session_id": None, "promoted_at": None})
        self._ensure_primary_pair_record(project_entry, project)
        return project_entry

    def _pair_id(self, project: str, claude_session_id: str, codex_session_id: str) -> str:
        return "pair-%s" % sha256_text(
            "pair\n%s\n%s\n%s" % (project, claude_session_id, codex_session_id)
        )[:16]

    def _ensure_pair_record(
        self,
        project_entry: Dict[str, Any],
        project: str,
        *,
        claude_session_id: str,
        codex_session_id: str,
        status: str = "active",
        source: str = "primary_active",
    ) -> Dict[str, Any]:
        pairs = project_entry.setdefault("pairs", {})
        pair_id = self._pair_id(project, claude_session_id, codex_session_id)
        sessions = project_entry.setdefault("sessions", {})
        codex_record = sessions.get(codex_session_id) if isinstance(sessions, dict) else {}
        record = pairs.setdefault(
            pair_id,
            {
                "pair_id": pair_id,
                "project": project,
                "created_at": utc_now(),
            },
        )
        record.update(
            {
                "pair_id": pair_id,
                "project": project,
                "status": status,
                "claude_session_id": claude_session_id,
                "codex_session_id": codex_session_id,
                "codex_desktop_thread_id": (
                    codex_record.get("desktop_thread_id") if isinstance(codex_record, dict) else None
                ),
                "source": source,
                "updated_at": utc_now(),
            }
        )
        return record

    def _ensure_primary_pair_record(self, project_entry: Dict[str, Any], project: str) -> Optional[Dict[str, Any]]:
        active = project_entry.setdefault("active", {})
        claude_session_id = active.get("claude")
        codex_session_id = active.get("codex")
        if not claude_session_id or not codex_session_id:
            return None
        return self._ensure_pair_record(
            project_entry,
            project,
            claude_session_id=str(claude_session_id),
            codex_session_id=str(codex_session_id),
            status="active",
            source="primary_active",
        )

    def _find_pair_record(self, registry: Dict[str, Any], pair_id: str) -> Optional[Dict[str, Any]]:
        for project_name, project_entry in (registry.get("projects") or {}).items():
            pairs = (project_entry or {}).get("pairs") or {}
            pair = pairs.get(pair_id)
            if isinstance(pair, dict):
                return {"project": project_name, "pair": pair}
        return None

    def _active_pairs_for_sender(
        self,
        registry: Dict[str, Any],
        *,
        sender: str,
        target: str,
        sender_session_id: str,
    ) -> List[Dict[str, Any]]:
        key = "%s_session_id" % sender
        target_key = "%s_session_id" % target
        matches: List[Dict[str, Any]] = []
        for project_name, project_entry in (registry.get("projects") or {}).items():
            for pair_id, pair in ((project_entry or {}).get("pairs") or {}).items():
                if not isinstance(pair, dict) or pair.get("status") != "active":
                    continue
                if pair.get(key) != sender_session_id or not pair.get(target_key):
                    continue
                matches.append({"project": project_name, "pair_id": pair_id, "pair": pair})
        return matches

    def _active_pair_for_sessions(
        self,
        registry: Dict[str, Any],
        *,
        sender: str,
        target: str,
        sender_session_id: str,
        target_session_id: str,
    ) -> Optional[Dict[str, Any]]:
        sender_key = "%s_session_id" % sender
        target_key = "%s_session_id" % target
        matches: List[Dict[str, Any]] = []
        for project_name, project_entry in (registry.get("projects") or {}).items():
            for pair_id, pair in ((project_entry or {}).get("pairs") or {}).items():
                if not isinstance(pair, dict) or pair.get("status") != "active":
                    continue
                if pair.get(sender_key) != sender_session_id:
                    continue
                if pair.get(target_key) != target_session_id:
                    continue
                matches.append({"project": project_name, "pair_id": pair_id, "pair": pair})
        if len(matches) == 1:
            return matches[0]
        return None

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
                "tenant_id": LOCAL_DEFAULT_TENANT_ID,
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
        record.setdefault("tenant_id", LOCAL_DEFAULT_TENANT_ID)
        return record

    def _clear_pending_pair_fields(self, record: Dict[str, Any]) -> None:
        for key in PENDING_PAIR_FIELDS:
            record.pop(key, None)

    def _clear_non_primary_fields(self, record: Dict[str, Any]) -> None:
        record["non_primary"] = False
        record.pop("non_primary_role", None)
        self._clear_pending_pair_fields(record)

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
            for pair in (project_entry.get("pairs") or {}).values():
                if isinstance(pair, dict) and pair.get("codex_session_id") == session:
                    pair["codex_desktop_thread_id"] = record.get("desktop_thread_id")
                    pair["updated_at"] = utc_now()
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
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        path = self.inbox_path(target_agent)
        identity = self._identity(target_agent, session_id)
        rows = self.transport.read_inbox(identity, path, unread_only=False)
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
            self.transport.write_inbox_rows(identity, path, rows)
        now = utc_now()
        message_id = str(uuid.uuid4())
        action_requested = (
            "Follow the control message instructions."
            if (status or "").strip().lower() in {"action_required", "requires_action"}
            else "none"
        )
        delivered = "From %s:\nTYPE: %s\nSTATUS: %s\nSUMMARY: %s\nACTION_REQUESTED: %s\n\n%s" % (
            sender.capitalize(),
            control_type,
            status,
            summary,
            action_requested,
            body,
        )
        row = {
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
                "tenant_id": LOCAL_DEFAULT_TENANT_ID,
                "originator_machine_id": LOCAL_DEFAULT_MACHINE_ID,
            }
        if extra_fields:
            row.update(extra_fields)
        self.transport.append_inbox(identity, self.inbox_path(target_agent), row)
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
        extra_fields: Optional[Dict[str, Any]] = None,
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
            extra_fields=extra_fields,
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
        identity = self._identity(agent, session_id)
        return [
            row
            for row in self.transport.read_inbox(
                identity,
                self.inbox_path(agent),
                session_ids=[session_id],
                unread_only=False,
            )
            if row.get("session_id") == session_id
            and not row.get("read_at")
            and not row.get("superseded_at")
        ]

    def _promote_superseded_inbox(self, agent: str, superseded_session: str, project_name: str) -> List[Dict[str, Any]]:
        """Promote unread inbox entries from a superseded session to the project bucket."""
        inbox = self.inbox_path(agent)
        identity = self._identity(agent, superseded_session)
        rows = self.transport.read_inbox(
            identity,
            inbox,
            session_ids=[superseded_session],
            unread_only=False,
        )
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
            self.transport.write_inbox_rows(
                identity,
                inbox,
                rows,
                replace_session_ids=[superseded_session],
            )
        return promoted

    def register_non_primary_session(
        self,
        agent: str,
        session_id: str,
        project: str,
        *,
        pairing_intent: str,
        bootstrap_origin: str = "unknown",
        consent_timeout_seconds: Optional[int] = None,
        desktop_thread_id: Optional[str] = None,
        bootstrap_thread_id: Optional[str] = None,
        bootstrap_parent_thread_id: Optional[str] = None,
    ) -> BridgeResult:
        """Register a same-project chat without mutating the active primary pair.

        This is the safe sidecar path used for ask-first and background chats:
        it gives the session a durable identity for audit/ephemeral relay, but
        does not supersede the active session, rewrite watcher private routing,
        or send a peer HANDSHAKE.
        """
        with self._locked():
            try:
                owner = normalize_agent(agent)
                session = normalize_session(session_id)
                project_name = normalize_project(project)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            intent = (pairing_intent or "").strip().lower().replace("-", "_")
            if intent not in {"ask_first", "background"}:
                return BridgeResult(False, "rejected", "pairing_intent must be ask_first or background")

            status = "pending_pair" if intent == "ask_first" else "background"
            registry = self._load_session_registry()
            project_entry = self._project_registry(registry, project_name)
            active = project_entry.setdefault("active", {})
            existing_non_primary = [
                item
                for item in (project_entry.get("sessions") or {}).values()
                if isinstance(item, dict)
                and item.get("agent") == owner
                and item.get("status") in NON_PRIMARY_PAIRING_STATUSES
            ]
            if session not in (project_entry.get("sessions") or {}) and len(existing_non_primary) >= NON_PRIMARY_SESSION_CAP_PER_AGENT_PROJECT:
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": utc_now(),
                        "action": "pairing_intent_cardinality_rejected",
                        "accepted": False,
                        "agent": owner,
                        "session_id": session,
                        "project": project_name,
                        "pairing_intent": intent,
                        "cap": NON_PRIMARY_SESSION_CAP_PER_AGENT_PROJECT,
                    }
                )
                return BridgeResult(
                    False,
                    "cardinality_rejected",
                    "project %s already has %d non-primary %s session(s); cap is %d"
                    % (project_name, len(existing_non_primary), owner, NON_PRIMARY_SESSION_CAP_PER_AGENT_PROJECT),
                    {
                        "agent": owner,
                        "project": project_name,
                        "cap": NON_PRIMARY_SESSION_CAP_PER_AGENT_PROJECT,
                        "existing_count": len(existing_non_primary),
                    },
                )
            record = self._session_record(project_entry, session, owner)
            now = utc_now()
            peer_agent = "claude" if owner == "codex" else "codex"
            record.update(
                {
                    "status": status,
                    "project": project_name,
                    "registered_at": record.get("registered_at") or now,
                    "last_seen_at": now,
                    "bootstrap_origin": bootstrap_origin,
                    "pairing_intent": intent,
                    "non_primary": True,
                    "non_primary_role": "background" if status == "background" else None,
                    "paired_with": None,
                    "desktop_thread_id": (desktop_thread_id or "").strip() or record.get("desktop_thread_id"),
                    "bootstrap_thread_id": (bootstrap_thread_id or "").strip() or record.get("bootstrap_thread_id"),
                    "bootstrap_parent_thread_id": (bootstrap_parent_thread_id or "").strip()
                    or record.get("bootstrap_parent_thread_id"),
                }
            )
            if status == "pending_pair":
                record["pending_pair_started_at"] = now
                record["pending_pair_timeout_seconds"] = consent_timeout_seconds
                record["pending_pair_target_active_session"] = active.get(owner)
                record["pending_pair_peer_session"] = active.get(peer_agent)
            project_entry["updated_at"] = now
            self._save_session_registry(registry)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "pairing_intent_registered",
                    "accepted": True,
                    "agent": owner,
                    "session_id": session,
                    "project": project_name,
                    "pairing_intent": intent,
                    "session_status": status,
                    "active_session_preserved": active.get(owner),
                    "active_peer_session": active.get(peer_agent),
                    "bootstrap_origin": bootstrap_origin,
                }
            )
            return BridgeResult(
                True,
                status,
                "Registered %s %s session %s for project %s without superseding the active pair."
                % (intent, owner, session, project_name),
                {
                    "agent": owner,
                    "session_id": session,
                    "project": project_name,
                    "pairing_intent": intent,
                    "session_status": status,
                    "active_session_preserved": active.get(owner),
                    "active_peer_session": active.get(peer_agent),
                    "consent_timeout_seconds": consent_timeout_seconds,
                },
            )

    def activate_session(
        self,
        agent: str,
        session_id: str,
        project: Optional[str] = None,
        bootstrap_origin: str = "unknown",
        allow_supersede: bool = True,
        trusted_parent_eligible: bool = False,
        pairing_intent: Optional[str] = None,
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
                old_pair_key = "%s_session_id" % owner
                for pair in (project_entry.get("pairs") or {}).values():
                    if isinstance(pair, dict) and pair.get(old_pair_key) == previous_local:
                        pair["status"] = "superseded"
                        pair["superseded_by_session_id"] = session
                        pair["superseded_at"] = utc_now()
                        pair["updated_at"] = utc_now()
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
            if pairing_intent:
                record["pairing_intent"] = str(pairing_intent).strip().lower().replace("-", "_")
            if activation_status == "active":
                self._clear_non_primary_fields(record)
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

            pair_record = None
            if active.get("claude") and active.get("codex"):
                pair_record = self._ensure_primary_pair_record(project_entry, project_name)

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
                    "pair_id": pair_record.get("pair_id") if isinstance(pair_record, dict) else None,
                    "bootstrap_origin": bootstrap_origin,
                    "activation_status": activation_status,
                    "pairing_intent": pairing_intent,
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
                    "pair_id": pair_record.get("pair_id") if isinstance(pair_record, dict) else None,
                    "bootstrap_origin": bootstrap_origin,
                    "pairing_intent": pairing_intent,
                    "trusted_parent_session": trusted_info.get("session_id"),
                    "trusted_parent_promoted_at": trusted_info.get("promoted_at"),
                    "registry_path": str(self.session_registry_path),
                    "drained_messages": drained_messages,
                },
            )

    def reap_expired_pending_pairs(self, project: Optional[str] = None, now: Optional[str] = None) -> BridgeResult:
        now_stamp = now or utc_now()
        now_dt = parse_iso_datetime(now_stamp) or datetime.now(timezone.utc)
        with self._locked():
            try:
                project_filter = normalize_project(project) if project else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            registry = self._load_session_registry()
            changed: List[Dict[str, Any]] = []
            for project_name, project_entry in (registry.get("projects") or {}).items():
                if project_filter and project_name != project_filter:
                    continue
                active = project_entry.get("active") or {}
                for session_id, record in (project_entry.get("sessions") or {}).items():
                    if not isinstance(record, dict) or record.get("status") != "pending_pair":
                        continue
                    agent = record.get("agent")
                    reason = None
                    target_active = record.get("pending_pair_target_active_session")
                    if target_active and active.get(agent) != target_active:
                        reason = "active_session_changed"
                    started = parse_iso_datetime(record.get("pending_pair_started_at") or record.get("registered_at"))
                    timeout_seconds = int(record.get("pending_pair_timeout_seconds") or 120)
                    if reason is None and started and (now_dt - started).total_seconds() >= timeout_seconds:
                        reason = "timeout"
                    if reason is None:
                        continue
                    record["status"] = "background"
                    record["pairing_intent"] = "background"
                    record["pending_pair_fallback_at"] = now_stamp
                    record["pending_pair_fallback_reason"] = reason
                    record["last_seen_at"] = now_stamp
                    changed.append(
                        {
                            "project": project_name,
                            "agent": agent,
                            "session_id": session_id,
                            "reason": reason,
                        }
                    )
                    self._audit(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": now_stamp,
                            "action": "pairing_intent_fallback_to_background",
                            "accepted": True,
                            "project": project_name,
                            "agent": agent,
                            "session_id": session_id,
                            "reason": reason,
                        }
                    )
            if changed:
                self._save_session_registry(registry)
        return BridgeResult(
            True,
            "reaped",
            "Reaped %d pending_pair session(s)." % len(changed),
            {"count": len(changed), "sessions": changed},
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
                    "pairs": dict(project_entry.get("pairs", {})),
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
                if record.get("status") in {"active", "secondary", "superseded"} | NON_PRIMARY_PAIRING_STATUSES:
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
            identity = self._identity(target, None)
            rows = self.transport.read_inbox(identity, inbox, unread_only=False)
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
                    self.transport.write_inbox_rows(identity, inbox, rows)
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
                    moved["tenant_id"] = identity.tenant_id
                    moved["originator_machine_id"] = identity.machine_id
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
                    self.transport.write_inbox_rows(identity, inbox, kept)

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

    def _ephemeral_relays(self, state: Dict[str, Any]) -> Dict[str, Any]:
        relays = state.setdefault("ephemeral_relays", {})
        if not isinstance(relays, dict):
            relays = {}
            state["ephemeral_relays"] = relays
        return relays

    def _open_ephemeral_relay_count(self, state: Dict[str, Any], session_id: str) -> int:
        count = 0
        for relay in self._ephemeral_relays(state).values():
            if not isinstance(relay, dict):
                continue
            if relay.get("request_session_id") == session_id and relay.get("status") == "open":
                count += 1
        return count

    def _append_ephemeral_relay_row(
        self,
        *,
        state: Dict[str, Any],
        sender: str,
        target: str,
        body: str,
        body_hash: str,
        marker_variant: str,
        delivery_bucket: str,
        delivery_level: str,
        parent_project: Optional[str],
        sender_session_id: str,
        sender_origin: str,
        relay_id: str,
        relay_role: str,
        reply_to_session_id: str,
        primary_session_id_at_send: Optional[str],
        contract_id_at_send: Optional[str],
        relay_status: str = "open",
    ) -> Dict[str, Any]:
        now = utc_now()
        session_state = self._session_state(state, delivery_bucket)
        hop_count = int(session_state.get("hop_count", 0))
        delivered = "From %s:\n%s" % (sender.capitalize(), body)
        row = {
            "schema_version": 2,
            "id": str(uuid.uuid4()),
            "created_at": now,
            "session_id": delivery_bucket,
            "from_session_id": sender_session_id,
            "to_session_id": delivery_bucket,
            "from_session_id_kind": sender_origin,
            "inbox_level": delivery_level,
            "parent_project": parent_project,
            "promoted_from": None,
            "promoted_at": None,
            "orphaned_at": None,
            "escalated_from": None,
            "escalation_reason": None,
            "from": sender,
            "to": target,
            "body": body,
            "delivered_message": delivered,
            "hash": body_hash,
            "marker_variant": marker_variant,
            "hop_count": hop_count + 1,
            "seen_at": None,
            "seen_by_session": None,
            "seen_via": None,
            "read_at": None,
            "handled_at": None,
            "handled_by_session": None,
            "handled_status": None,
            "failure_reason": None,
            "tenant_id": LOCAL_DEFAULT_TENANT_ID,
            "originator_machine_id": LOCAL_DEFAULT_MACHINE_ID,
            "relay_mode": EPHEMERAL_RELAY_MODE,
            "ephemeral_relay_id": relay_id,
            "ephemeral_relay_role": relay_role,
            "relay_status": relay_status,
            "reply_to_session_id": reply_to_session_id,
            "primary_session_id_at_send": primary_session_id_at_send,
            "contract_id_at_send": contract_id_at_send,
        }
        identity = self._identity(target, delivery_bucket)
        self.transport.append_inbox(identity, self.inbox_path(target), row)
        session_state["hop_count"] = hop_count + 1
        seen = list(session_state.get("seen_hashes", []))
        seen.append(body_hash)
        session_state["seen_hashes"] = seen[-50:]
        session_state["last_message_at"] = now
        self._record_rate_limit_send(state, sender, target)
        return row

    def _send_ephemeral_relay_unlocked(
        self,
        *,
        sender: str,
        target: str,
        body: str,
        body_hash: str,
        marker_variant: str,
        state: Dict[str, Any],
        registry: Dict[str, Any],
        sender_session_id: Optional[str],
        reply_to_session_id: Optional[str],
        ephemeral_relay_id: Optional[str],
    ) -> BridgeResult:
        session_hint = (sender_session_id or "").strip()
        reply_hint = (reply_to_session_id or "").strip()
        relay_hint = (ephemeral_relay_id or "").strip()
        relays = self._ephemeral_relays(state)

        if reply_hint:
            if not relay_hint:
                return BridgeResult(False, "rejected", "ephemeral replies require ephemeral_relay_id")
            relay = relays.get(relay_hint)
            if not isinstance(relay, dict):
                return BridgeResult(False, "not_found", "ephemeral relay %s was not found" % relay_hint)
            if relay.get("request_agent") != target or relay.get("response_agent") != sender:
                return BridgeResult(False, "rejected", "ephemeral relay participants do not match this reply")
            request_session = normalize_session(reply_hint)
            if request_session != relay.get("request_session_id"):
                return BridgeResult(False, "rejected", "reply_to_session_id does not match the relay request")
            project_name = normalize_project(str(relay.get("project") or ""))
            sender_active = ((registry.get("projects") or {}).get(project_name, {}).get("active") or {}).get(sender)
            sender_context = SenderContext(sender, str(sender_active), project_name) if sender_active else None
            if not sender_context or not resolve_route(
                sender=sender_context,
                target=ProjectInbox(project_name, target),
                kind=MessageKind.WORK,
                registry=registry,
            ).ok:
                return BridgeResult(False, "rejected", "ephemeral relay reply sender must be the active peer session")
            found = self._find_session_record(registry, request_session, agent=target)
            target_record = found.get("record") if found else None
            deliver_to_session = bool(
                target_record
                and target_record.get("status") in NON_PRIMARY_PAIRING_STATUSES | {"secondary", "active"}
            )
            delivery_bucket = request_session if deliver_to_session else project_name
            delivery_level = INBOX_LEVEL_SESSION if deliver_to_session else INBOX_LEVEL_PROJECT
            relay_status = "replied" if deliver_to_session else "orphaned"
            row = self._append_ephemeral_relay_row(
                state=state,
                sender=sender,
                target=target,
                body=body,
                body_hash=body_hash,
                marker_variant=marker_variant,
                delivery_bucket=delivery_bucket,
                delivery_level=delivery_level,
                parent_project=project_name,
                sender_session_id=str(sender_active),
                sender_origin="parent",
                relay_id=relay_hint,
                relay_role="reply",
                reply_to_session_id=request_session,
                primary_session_id_at_send=sender_active,
                contract_id_at_send=relay.get("contract_id_at_send"),
                relay_status=relay_status,
            )
            relay["status"] = relay_status
            relay["replied_at"] = utc_now()
            relay["reply_message_id"] = row["id"]
            relay["reply_delivery_bucket"] = delivery_bucket
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": relay["replied_at"],
                    "action": "ephemeral_relay_replied" if deliver_to_session else "ephemeral_relay_orphaned",
                    "accepted": True,
                    "ephemeral_relay_id": relay_hint,
                    "from": sender,
                    "to": target,
                    "project": project_name,
                    "request_session_id": request_session,
                    "delivery_bucket": delivery_bucket,
                    "message_id": row["id"],
                }
            )
            return BridgeResult(
                True,
                relay_status,
                "Queued ephemeral relay reply %s for %s in %s." % (relay_hint, target, delivery_bucket),
                {
                    "id": row["id"],
                    "ephemeral_relay_id": relay_hint,
                    "reply_to_session_id": request_session,
                    "resolved_session_id": delivery_bucket,
                    "inbox_level": delivery_level,
                    "relay_status": relay_status,
                },
            )

        if not session_hint:
            return BridgeResult(False, "rejected", "ephemeral relay requests require sender_session_id")
        request_session = normalize_session(session_hint)
        found = self._find_session_record(registry, request_session, agent=sender)
        if not found:
            return BridgeResult(False, "rejected", "sender_session_id is not registered for %s" % sender)
        record = found["record"]
        if record.get("status") not in NON_PRIMARY_PAIRING_STATUSES:
            return BridgeResult(False, "rejected", "ephemeral relay requests must originate from a background or pending_pair session")
        if record.get("bootstrap_origin") == "subagent":
            return BridgeResult(False, "rejected", "subagent sessions cannot initiate ephemeral relays")
        if self._open_ephemeral_relay_count(state, request_session) >= EPHEMERAL_RELAY_CAP_PER_SESSION:
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "ephemeral_relay_capped",
                    "accepted": False,
                    "from": sender,
                    "to": target,
                    "request_session_id": request_session,
                    "cap": EPHEMERAL_RELAY_CAP_PER_SESSION,
                }
            )
            return BridgeResult(False, "rejected", "ephemeral relay cap reached for session %s" % request_session)

        project_name = found["project"]
        project_entry = (registry.get("projects") or {}).get(project_name, {})
        relay_id = relay_hint or str(uuid.uuid4())
        primary_session = (project_entry.get("active") or {}).get(sender)
        row = self._append_ephemeral_relay_row(
            state=state,
            sender=sender,
            target=target,
            body=body,
            body_hash=body_hash,
            marker_variant=marker_variant,
            delivery_bucket=project_name,
            delivery_level=INBOX_LEVEL_PROJECT,
            parent_project=project_name,
            sender_session_id=request_session,
            sender_origin=record.get("bootstrap_origin") or "unknown",
            relay_id=relay_id,
            relay_role="request",
            reply_to_session_id=request_session,
            primary_session_id_at_send=primary_session,
            contract_id_at_send=None,
        )
        relays[relay_id] = {
            "id": relay_id,
            "status": "open",
            "created_at": row["created_at"],
            "project": project_name,
            "request_agent": sender,
            "response_agent": target,
            "request_session_id": request_session,
            "request_message_id": row["id"],
            "primary_session_id_at_send": primary_session,
            "pairing_intent_at_send": record.get("pairing_intent"),
            "contract_id_at_send": None,
            "relay_target_bucket": project_name,
        }
        self._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": row["created_at"],
                "action": "ephemeral_relay_initiated",
                "accepted": True,
                "ephemeral_relay_id": relay_id,
                "from": sender,
                "to": target,
                "project": project_name,
                "request_session_id": request_session,
                "message_id": row["id"],
            }
        )
        return BridgeResult(
            True,
            "queued",
            "Queued ephemeral relay request %s for %s in project bucket %s." % (relay_id, target, project_name),
            {
                "id": row["id"],
                "ephemeral_relay_id": relay_id,
                "reply_to_session_id": request_session,
                "resolved_session_id": project_name,
                "inbox_level": INBOX_LEVEL_PROJECT,
                "relay_status": "open",
            },
        )

    def send_to_peer(
        self,
        from_agent: str,
        to_agent: str,
        message: str,
        session_id: Optional[str] = None,
        target_session_id: Optional[str] = None,
        relay_mode: Optional[str] = None,
        sender_session_id: Optional[str] = None,
        reply_to_session_id: Optional[str] = None,
        ephemeral_relay_id: Optional[str] = None,
        pair_id: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            now = utc_now()
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
            resolved_session = target_session_id or session_id
            event = {
                "id": str(uuid.uuid4()),
                "timestamp": now,
                "action": "send_to_peer",
                "from": (from_agent or "").strip().lower(),
                "to": (to_agent or "").strip().lower(),
                "session_id": resolved_session,
                "pair_id": pair_id,
                "marker_target": None,
                "marker_variant": None,
                "hash": None,
                "accepted": False,
                "reason": None,
                "deprecated_session_id_param_used": deprecated_session_param_used,
            }
            journal: Optional[Dict[str, Any]] = None
            journal_sequence: Optional[int] = None

            def reject(reason: str) -> BridgeResult:
                event["reason"] = reason
                if journal is not None and journal_sequence is not None:
                    self._update_implementation_event_delivery_unlocked(
                        journal,
                        journal_sequence,
                        delivery_status="rejected",
                        rejection_reason=reason,
                    )
                    self._save_implementation_journal(journal)
                self._audit(event)
                return BridgeResult(False, "rejected", reason, {"audit_id": event["id"]})

            try:
                sender = normalize_agent(from_agent)
                target = normalize_agent(to_agent)
                sender_session_hint = (
                    normalize_session(sender_session_id) if sender_session_id is not None else None
                )
                pair_hint = normalize_session(pair_id) if pair_id is not None else None
            except ValueError as exc:
                return reject(str(exc))

            marker = strip_markers(message)
            body = marker["body"]
            body_hash = sha256_text("%s\n%s\n%s" % (sender, target, body))
            event["from"] = sender
            event["to"] = target
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
            resolved_relay_mode = (relay_mode or "").strip().lower()
            if resolved_relay_mode:
                if resolved_relay_mode != EPHEMERAL_RELAY_MODE:
                    return reject("relay_mode must be ephemeral")
                result = self._send_ephemeral_relay_unlocked(
                    sender=sender,
                    target=target,
                    body=body,
                    body_hash=body_hash,
                    marker_variant=marker["marker_variant"],
                    state=state,
                    registry=registry,
                    sender_session_id=sender_session_id,
                    reply_to_session_id=reply_to_session_id,
                    ephemeral_relay_id=ephemeral_relay_id,
                )
                event["relay_mode"] = EPHEMERAL_RELAY_MODE
                event["accepted"] = result.ok
                event["reason"] = result.status if result.ok else result.message
                event["ephemeral_relay_id"] = result.data.get("ephemeral_relay_id") if result.data else None
                self._save_state(state)
                self._audit(event)
                return result
            route_pair = None
            if pair_hint:
                found_pair = self._find_pair_record(registry, pair_hint)
                if not found_pair:
                    return reject("pair_id %s is not registered" % pair_hint)
                pair_record = found_pair["pair"]
                if pair_record.get("status") != "active":
                    return reject("pair_id %s is %s" % (pair_hint, pair_record.get("status") or "unknown"))
                sender_key = "%s_session_id" % sender
                target_key = "%s_session_id" % target
                if not pair_record.get(sender_key) or not pair_record.get(target_key):
                    return reject("pair_id %s does not connect %s to %s" % (pair_hint, sender, target))
                if sender_session_hint and pair_record.get(sender_key) != sender_session_hint:
                    return reject("sender_session_id does not belong to pair_id %s" % pair_hint)
                pair_target_session = str(pair_record[target_key])
                if resolved_session and normalize_session(resolved_session) != pair_target_session:
                    return reject("target_session_id/session_id does not match pair_id %s target" % pair_hint)
                resolved_session = pair_target_session
                sender_session_hint = str(pair_record[sender_key])
                route_pair = dict(pair_record)
            elif not resolved_session and sender_session_hint:
                matches = self._active_pairs_for_sender(
                    registry,
                    sender=sender,
                    target=target,
                    sender_session_id=sender_session_hint,
                )
                if len(matches) != 1:
                    return reject(
                        "sender_session_id %s matched %d active pair(s); specify pair_id or target_session_id"
                        % (sender_session_hint, len(matches))
                    )
                route_pair = dict(matches[0]["pair"])
                pair_hint = str(matches[0]["pair_id"])
                resolved_session = str(route_pair["%s_session_id" % target])
            elif not resolved_session:
                return reject(
                    "ambiguous route: send_to_peer requires target_session_id, pair_id, or sender_session_id; "
                    "implicit project-bucket fallback is disabled"
                )

            session = normalize_session(resolved_session)
            event["session_id"] = session
            event["pair_id"] = pair_hint
            if session == DEFAULT_SESSION_ID:
                return reject("routing error: 'default' is deprecated; use a named project bucket or the agent inbox")
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
            if sender_session_hint:
                found_sender = self._find_session_record(registry, sender_session_hint, agent=sender)
                if not found_sender:
                    return reject("sender_session_id %s is not registered for %s" % (sender_session_hint, sender))
                sender_context = SenderContext(sender, sender_session_hint, found_sender["project"])
            else:
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
            sender_identity = self._identity(sender, sender_session_id or session)
            sender_origin = None
            if sender_session_id:
                sender_record_info = self._find_session_record(registry, sender_session_id, agent=sender)
                if sender_record_info:
                    sender_origin = sender_record_info["record"].get("bootstrap_origin")
                if pair_hint is None and delivery_level == INBOX_LEVEL_SESSION:
                    active_pair = self._active_pair_for_sessions(
                        registry,
                        sender=sender,
                        target=target,
                        sender_session_id=sender_session_id,
                        target_session_id=delivery_bucket,
                    )
                    if active_pair:
                        pair_hint = str(active_pair["pair_id"])
                        event["pair_id"] = pair_hint

            implementation_message_type = self._implementation_message_type(body)
            if implementation_message_type:
                journal = self._load_implementation_journal()
                journal_event = self._record_implementation_event_unlocked(
                    journal,
                    owner_agent=sender,
                    peer_agent=target,
                    message_type=implementation_message_type,
                    summary=self._implementation_event_summary(body),
                    commit=self._implementation_event_commit(body),
                    related_session_id=sender_session_id or delivery_bucket,
                    body=body,
                    delivery_status="attempted",
                    delivery_session_id=delivery_bucket,
                )
                journal_sequence = int(journal_event["sequence"])
                event["implementation_journal_sequence"] = journal_sequence
                self._save_implementation_journal(journal)

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
                if self._row_counts_for_backpressure(registry, target, row)
            ]
            delivery_backpressure_limit = self._backpressure_limit_for_level(delivery_level)
            is_control_message = marker["marker_variant"] == "control"
            if (
                delivery_level == INBOX_LEVEL_SESSION
                and not is_control_message
                and delivery_backpressure_limit is not None
                and len(unread_work) >= delivery_backpressure_limit
            ):
                reason = (
                    "target %s session inbox %s is full (%d unread >= %d)"
                    % (target, delivery_bucket, len(unread_work), delivery_backpressure_limit)
                )
                event["backpressure_pending"] = self._record_backpressure_pending(
                    state,
                    receiver_agent=target,
                    session_id=delivery_bucket,
                    inbox_level=delivery_level,
                    sender_agent=sender,
                    sender_session_id=sender_session_id,
                    reason="session_backpressure",
                    unread_count=len(unread_work),
                    limit=delivery_backpressure_limit,
                )
                self._save_state(state)
                event["backpressure_nudge"] = self._request_backpressure_nudge(
                    agent=target,
                    session=delivery_bucket,
                    reason="session_backpressure",
                )
                return reject(reason)
            if (
                delivery_level == INBOX_LEVEL_PROJECT
                and not is_control_message
                and delivery_backpressure_limit is not None
                and len(unread_work) >= delivery_backpressure_limit
            ):
                reason = (
                    "target %s project inbox %s is full (%d unread >= %d)"
                    % (target, delivery_bucket, len(unread_work), delivery_backpressure_limit)
                )
                event["backpressure_pending"] = self._record_backpressure_pending(
                    state,
                    receiver_agent=target,
                    session_id=delivery_bucket,
                    inbox_level=delivery_level,
                    sender_agent=sender,
                    sender_session_id=sender_session_id,
                    reason="project_backpressure",
                    unread_count=len(unread_work),
                    limit=delivery_backpressure_limit,
                )
                self._save_state(state)
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
                "pair_id": pair_hint,
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
                "tenant_id": sender_identity.tenant_id,
                "originator_machine_id": sender_identity.machine_id,
            }
            if journal_sequence is not None:
                inbox_row["implementation_journal_sequence"] = journal_sequence
            target_identity = self._identity(target, delivery_bucket)
            self.transport.append_inbox(target_identity, self.inbox_path(target), inbox_row)

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
            if journal is not None and journal_sequence is not None:
                self._update_implementation_event_delivery_unlocked(
                    journal,
                    journal_sequence,
                    delivery_status="queued",
                    delivery_session_id=delivery_bucket,
                    delivery_message_id=event["id"],
                )
                self._save_implementation_journal(journal)
            self._record_review_loop_send(
                sender=sender,
                target=target,
                body=body,
                message_id=event["id"],
                session_id=delivery_bucket,
                sender_session_id=sender_session_hint,
            )
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
                    "pair_id": pair_hint,
                    "escalated_from": delivery.get("escalated_from"),
                    "escalation_reason": delivery.get("escalation_reason"),
                    "implementation_journal_sequence": journal_sequence,
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

    def record_implementation_event(
        self,
        owner_agent: str,
        peer_agent: str,
        summary: str,
        *,
        message_type: str = "IMPLEMENTATION_UPDATE",
        commit: Optional[str] = None,
        tests: Optional[List[str]] = None,
        details: Optional[str] = None,
        related_session_id: Optional[str] = None,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
                peer = normalize_agent(peer_agent)
                related_session = normalize_session(related_session_id) if related_session_id else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            event_type = (message_type or "").strip().upper()
            if event_type not in IMPLEMENTATION_JOURNAL_MESSAGE_TYPES:
                return BridgeResult(
                    False,
                    "rejected",
                    "message_type must be one of: %s" % ", ".join(sorted(IMPLEMENTATION_JOURNAL_MESSAGE_TYPES)),
                )
            if not summary.strip():
                return BridgeResult(False, "rejected", "summary is required")
            journal = self._load_implementation_journal()
            event = self._record_implementation_event_unlocked(
                journal,
                owner_agent=owner,
                peer_agent=peer,
                message_type=event_type,
                summary=summary,
                commit=commit,
                tests=tests or [],
                details=details,
                related_session_id=related_session,
                delivery_status="recorded",
            )
            self._save_implementation_journal(journal)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "record_implementation_event",
                    "accepted": True,
                    "owner_agent": owner,
                    "peer_agent": peer,
                    "sequence": event["sequence"],
                }
            )
            return BridgeResult(
                True,
                "recorded",
                "Recorded implementation event #%s for %s -> %s." % (event["sequence"], owner, peer),
                {"event": event},
            )

    def list_implementation_journal(
        self,
        owner_agent: Optional[str] = None,
        peer_agent: Optional[str] = None,
        since_sequence: int = 0,
        limit: int = 50,
    ) -> BridgeResult:
        with self._locked():
            try:
                owner = normalize_agent(owner_agent) if owner_agent else None
                peer = normalize_agent(peer_agent) if peer_agent else None
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            page_limit = self._bounded_int("limit", int(limit), 1, 200)
            try:
                since = max(0, int(since_sequence or 0))
            except (TypeError, ValueError):
                return BridgeResult(False, "rejected", "since_sequence must be an integer")
            journal = self._load_implementation_journal()
            events = [
                event
                for event in journal.get("events", [])
                if isinstance(event, dict)
                and int(event.get("sequence") or 0) > since
                and (owner is None or event.get("owner_agent") == owner)
                and (peer is None or event.get("peer_agent") == peer)
            ]
            events = sorted(events, key=lambda item: int(item.get("sequence") or 0))
            return BridgeResult(
                True,
                "implementation_journal",
                "Found %d implementation journal event(s)." % len(events),
                {
                    "count": min(len(events), page_limit),
                    "total_count": len(events),
                    "events": events[-page_limit:],
                    "peer_states": copy.deepcopy(journal.get("peer_states") or {}),
                    "path": str(self.implementation_journal_path),
                },
            )

    def send_catchup_digest(
        self,
        from_agent: str,
        to_agent: str,
        target_session_id: str,
        reason: str = "manual",
        max_items: int = IMPLEMENTATION_JOURNAL_DIGEST_MAX_ITEMS,
    ) -> BridgeResult:
        with self._locked():
            try:
                sender = normalize_agent(from_agent)
                target = normalize_agent(to_agent)
                session = normalize_session(target_session_id)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            page_limit = self._bounded_int("max_items", int(max_items), 1, 100)
            digest = self._send_catchup_digest_unlocked(
                from_agent=sender,
                to_agent=target,
                target_session_id=session,
                reason=(reason or "manual").strip() or "manual",
                max_items=page_limit,
            )
            return BridgeResult(
                bool(digest.get("ok", digest.get("status") == "skipped")),
                str(digest.get("status")),
                "Catch-up digest %s for %s -> %s." % (digest.get("status"), sender, target),
                {"digest": digest},
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
            registry = self._load_session_registry()
            buckets: Optional[List[str]] = None
            if session is not None:
                buckets = [session]
            if include_parents and session is not None and buckets is not None:
                for parent in self._parent_buckets_for(registry, session):
                    if parent not in buckets:
                        buckets.append(parent)
            target_identity = self._identity(target, session)
            rows = self.transport.read_inbox(
                target_identity,
                path,
                session_ids=buckets,
                unread_only=False,
            )
            unread = [row for row in rows if not row.get("read_at")]
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
                    self.transport.write_inbox_rows(
                        target_identity,
                        path,
                        rows,
                        replace_session_ids=buckets,
                    )
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
                before_counts = self._unread_work_counts_by_bucket(rows, registry, target)
                read_rows = [dict(row) for row in rows if row.get("id") in unread_ids]
                for row in rows:
                    if row.get("id") in unread_ids:
                        row["read_at"] = now
                self.transport.write_inbox_rows(
                    target_identity,
                    path,
                    rows,
                    replace_session_ids=buckets,
                )
                state = self._load_state()
                backpressure_resolutions = self._resolve_backpressure_after_read(
                    state,
                    registry,
                    receiver_agent=target,
                    before_counts=before_counts,
                    after_counts=self._unread_work_counts_by_bucket(rows, registry, target),
                    read_rows=read_rows,
                    via="check_inbox",
                )
                if backpressure_resolutions:
                    self._save_state(state)
                implementation_ack = self._ack_implementation_rows_unlocked(
                    rows=read_rows,
                    reader_agent=target,
                )
                catchup_digests = self._send_catchup_for_handshakes_unlocked(
                    receiver_agent=target,
                    rows=read_rows,
                    registry=registry,
                    via="check_inbox",
                )
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
                        "backpressure_resolutions": backpressure_resolutions,
                        "implementation_ack": implementation_ack,
                        "catchup_digests": catchup_digests,
                    }
                )
            else:
                backpressure_resolutions = []
                implementation_ack = {"changed": False}
                catchup_digests = []

            backpressure_self_healed: List[Dict[str, Any]] = []
            if record_seen:
                state = self._load_state()
                backpressure_self_healed = self._self_heal_stale_backpressure_pending(
                    state,
                    registry,
                    receiver_agent=target,
                    via="check_inbox",
                )
                if backpressure_self_healed:
                    self._save_state(state)

            if not unread:
                if session is None:
                    scope = "all buckets"
                else:
                    scope = "session %s" % session if not include_parents else "session %s (+parents)" % session
                data: Dict[str, Any] = {}
                if backpressure_self_healed:
                    data["backpressure_self_healed"] = backpressure_self_healed
                return BridgeResult(
                    True,
                    "empty",
                    "No unread bridge messages for %s in %s." % (target, scope),
                    data,
                )

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
                    "backpressure_resolutions": backpressure_resolutions,
                    "backpressure_self_healed": backpressure_self_healed,
                    "implementation_ack": implementation_ack,
                    "catchup_digests": catchup_digests,
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
                target_identity = self._identity(target, None)
                rows = self.transport.read_inbox(
                    target_identity,
                    path,
                    session_ids=valid_sessions,
                    unread_only=False,
                )
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
                            self.transport.write_inbox_rows(
                                target_identity,
                                path,
                                rows,
                                replace_session_ids=valid_sessions,
                            )
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
                        registry = self._load_session_registry()
                        before_counts = self._unread_work_counts_by_bucket(rows, registry, target)
                        read_rows = [dict(row) for row in rows if row.get("id") in unread_ids]
                        for row in rows:
                            if row.get("id") in unread_ids:
                                row["read_at"] = now
                        self.transport.write_inbox_rows(
                            target_identity,
                            path,
                            rows,
                            replace_session_ids=valid_sessions,
                        )
                        state = self._load_state()
                        backpressure_resolutions = self._resolve_backpressure_after_read(
                            state,
                            registry,
                            receiver_agent=target,
                            before_counts=before_counts,
                            after_counts=self._unread_work_counts_by_bucket(rows, registry, target),
                            read_rows=read_rows,
                            via="wait_inbox",
                        )
                        if backpressure_resolutions:
                            self._save_state(state)
                        implementation_ack = self._ack_implementation_rows_unlocked(
                            rows=read_rows,
                            reader_agent=target,
                        )
                        catchup_digests = self._send_catchup_for_handshakes_unlocked(
                            receiver_agent=target,
                            rows=read_rows,
                            registry=registry,
                            via="wait_inbox",
                        )
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
                                "backpressure_resolutions": backpressure_resolutions,
                                "implementation_ack": implementation_ack,
                                "catchup_digests": catchup_digests,
                            }
                        )
                    else:
                        backpressure_resolutions = []
                        implementation_ack = {"changed": False}
                        catchup_digests = []
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
                            "backpressure_resolutions": backpressure_resolutions,
                            "implementation_ack": implementation_ack,
                            "catchup_digests": catchup_digests,
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
            target_identity = self._identity(target, session)
            rows = self.transport.read_inbox(target_identity, path, unread_only=False)
            registry = self._load_session_registry()
            before_counts = self._unread_work_counts_by_bucket(rows, registry, target)
            now = utc_now()
            matched = False
            changed = False
            matched_row: Optional[Dict[str, Any]] = None
            read_rows: List[Dict[str, Any]] = []
            for row in rows:
                if row.get("id") == target_id and (session is None or row.get("session_id") == session):
                    matched = True
                    matched_row = row
                    if not row.get("read_at"):
                        read_rows = [dict(row)]
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
                self.transport.write_inbox_rows(target_identity, path, rows)

            backpressure_resolutions: List[Dict[str, Any]] = []
            if changed and read_rows:
                state = self._load_state()
                backpressure_resolutions = self._resolve_backpressure_after_read(
                    state,
                    registry,
                    receiver_agent=target,
                    before_counts=before_counts,
                    after_counts=self._unread_work_counts_by_bucket(rows, registry, target),
                    read_rows=read_rows,
                    via="mark_read",
                )
                if backpressure_resolutions:
                    self._save_state(state)
                implementation_ack = self._ack_implementation_rows_unlocked(
                    rows=read_rows,
                    reader_agent=target,
                )
                catchup_digests = self._send_catchup_for_handshakes_unlocked(
                    receiver_agent=target,
                    rows=read_rows,
                    registry=registry,
                    via="mark_read",
                )
            else:
                implementation_ack = {"changed": False}
                catchup_digests = []

            state = self._load_state()
            registry = self._load_session_registry()
            backpressure_self_healed = self._self_heal_stale_backpressure_pending(
                state,
                registry,
                receiver_agent=target,
                via="mark_read",
            )
            if backpressure_self_healed:
                self._save_state(state)

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
                    "backpressure_resolutions": backpressure_resolutions,
                    "backpressure_self_healed": backpressure_self_healed,
                    "implementation_ack": implementation_ack,
                    "catchup_digests": catchup_digests,
                }
            )
            return BridgeResult(
                True,
                "marked_read" if changed else "already_read",
                "Message %s is marked read." % target_id,
                {
                    "message_id": target_id,
                    "changed": changed,
                    "stale_bypass": stale_bypass,
                    "backpressure_resolutions": backpressure_resolutions,
                    "backpressure_self_healed": backpressure_self_healed,
                    "implementation_ack": implementation_ack,
                    "catchup_digests": catchup_digests,
                },
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

    def _set_next_override_wake_message(self, message: str) -> None:
        """Write an initiator label into watcher state for the next wake fire.

        The watcher consumes this one-shot field immediately after using it so
        subsequent fires (retries, unrelated messages) revert to the default
        "Watcher says check bridge inbox" template value.
        """
        watcher_state = read_json(
            self.watcher_state_path,
            {"seen_ids": [], "pending_wake_verifications": []},
        )
        watcher_state["next_override_wake_message"] = message
        write_json(self.watcher_state_path, watcher_state)

    def _request_backpressure_nudge(self, *, agent: str, session: str, reason: str, nudge_wake_message: Optional[str] = None) -> Dict[str, Any]:
        state = self._load_state()
        registry = self._load_session_registry()
        backpressure_self_healed = self._self_heal_stale_backpressure_pending(
            state,
            registry,
            receiver_agent=agent,
            via="nudge_peer",
        )
        nudgeable_bucket = self._bucket_accepts_work_backpressure(registry, agent, session)
        if not nudgeable_bucket:
            status = "backpressure_rejected_no_nudge_no_unread"
            result = {"status": status, "session_id": session, "reason": "session_not_active"}
        elif self._wake_breaker_open_for_session(session):
            status = "backpressure_rejected_no_nudge_breaker_open"
            result = {"status": status, "session_id": session, "reason": "wake_breaker_open"}
        elif self._wake_prefire_limited_for_session(session):
            status = "backpressure_rejected_no_nudge_rate_limited"
            result = {"status": status, "session_id": session, "reason": "wake_rate_limited"}
        else:
            unread_work = [
                row for row in self._unread_for(agent, session)
                if self._row_counts_for_backpressure(registry, agent, row)
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
                    if nudge_wake_message:
                        watcher_state["next_override_wake_message"] = nudge_wake_message
                    write_json(self.watcher_state_path, watcher_state)
                status = "backpressure_rejected_nudge_attempted"
                result = {
                    "status": status,
                    "session_id": session,
                    "message_id": message_id,
                    "wake_rearmed": changed,
                }
        if backpressure_self_healed:
            self._save_state(state)
            result["backpressure_self_healed"] = backpressure_self_healed
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

    def wake_fire_history(self, session_id: Optional[str] = None, limit: int = 20) -> BridgeResult:
        with self._locked():
            target_session = normalize_session(session_id) if session_id is not None else None
            try:
                bounded_limit = max(1, min(200, int(limit)))
            except (TypeError, ValueError):
                bounded_limit = 20
            state = read_json(
                self.watcher_state_path,
                {
                    "seen_ids": [],
                    "toasted_ids": [],
                    "pending_wake_verifications": [],
                    "paused_wake_messages": [],
                    "unknown_origin_warnings": [],
                    "wake_fire_history": [],
                },
            )
            history = state.get("wake_fire_history", [])
            if not isinstance(history, list):
                history = []
            if target_session is not None:
                history = [
                    item for item in history
                    if isinstance(item, dict) and str(item.get("session_id") or "") == target_session
                ]
            else:
                history = [item for item in history if isinstance(item, dict)]
            entries = copy.deepcopy(history[-bounded_limit:])
            scope = target_session if target_session is not None else "all sessions"
            return BridgeResult(
                True,
                "history",
                "Wake fire history for %s." % scope,
                {
                    "session_id": target_session,
                    "limit": bounded_limit,
                    "count": len(entries),
                    "entries": entries,
                    "path": str(self.watcher_state_path),
                },
            )

    def stale_unread_watchdog(
        self,
        agent: Optional[str] = None,
        stale_after_seconds: int = STALE_UNREAD_DEFAULT_AGE_S,
        rearm: bool = False,
        limit: int = 50,
    ) -> BridgeResult:
        """Find wake-delivered messages that are still unread.

        The watcher records ids in ``seen_ids`` after a wake command succeeds.
        If such a row remains unread past the threshold, the bridge has a
        delivered-but-not-consumed gap. Optional rearm removes those ids from
        ``seen_ids`` so the watcher can nudge again without rewriting inbox rows.
        """
        with self._locked():
            try:
                agents = [normalize_agent(agent)] if agent else sorted(AGENTS)
                if (
                    isinstance(stale_after_seconds, bool)
                    or not isinstance(stale_after_seconds, int)
                    or stale_after_seconds < 1
                    or stale_after_seconds > 86400
                ):
                    raise ValueError("stale_after_seconds must be between 1 and 86400")
                bounded_limit = max(1, min(200, int(limit)))
            except (TypeError, ValueError) as exc:
                return BridgeResult(False, "rejected", str(exc))

            watcher_state = read_json(
                self.watcher_state_path,
                {
                    "seen_ids": [],
                    "toasted_ids": [],
                    "pending_wake_verifications": [],
                    "paused_wake_messages": [],
                    "unknown_origin_warnings": [],
                    "wake_fire_history": [],
                },
            )
            seen_ids = {str(item) for item in watcher_state.get("seen_ids", []) if str(item)}
            now_dt = datetime.now(timezone.utc)
            stale_rows: List[Dict[str, Any]] = []
            for target in agents:
                identity = self._identity(target, None)
                rows = self.transport.read_inbox(identity, self.inbox_path(target), unread_only=False)
                for row in rows:
                    message_id = str(row.get("id") or "")
                    if not message_id or message_id not in seen_ids:
                        continue
                    if row.get("read_at") or row.get("superseded_at"):
                        continue
                    age = self._health_age_seconds(row.get("created_at"), now_dt)
                    if age is None or age < stale_after_seconds:
                        continue
                    stale_rows.append(
                        {
                            "agent": target,
                            "session_id": row.get("session_id") or DEFAULT_SESSION_ID,
                            "message_id": message_id,
                            "from": row.get("from"),
                            "created_at": row.get("created_at"),
                            "age_seconds": age,
                            "seen_at": row.get("seen_at"),
                            "read_at": row.get("read_at"),
                            "body_preview": str(row.get("body") or row.get("delivered_message") or "")[:240],
                        }
                    )
            stale_rows.sort(key=lambda item: int(item.get("age_seconds") or 0), reverse=True)
            stale_rows = stale_rows[:bounded_limit]

            rearmed_ids: List[str] = []
            if rearm and stale_rows:
                target_ids = {str(item["message_id"]) for item in stale_rows}
                original_seen = [str(item) for item in watcher_state.get("seen_ids", []) if str(item)]
                watcher_state["seen_ids"] = [item for item in original_seen if item not in target_ids]
                rearmed_ids = [item for item in original_seen if item in target_ids]
                if rearmed_ids:
                    write_json(self.watcher_state_path, watcher_state)

            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "stale_unread_watchdog",
                    "accepted": True,
                    "agent": agent,
                    "stale_after_seconds": stale_after_seconds,
                    "stale_count": len(stale_rows),
                    "rearm": bool(rearm),
                    "rearmed_count": len(rearmed_ids),
                    "message_ids": [item["message_id"] for item in stale_rows],
                }
            )
            status = "stale_unread" if stale_rows else "empty"
            return BridgeResult(
                True,
                status,
                "%d stale unread message(s) found; %d rearmed." % (len(stale_rows), len(rearmed_ids)),
                {
                    "stale_after_seconds": stale_after_seconds,
                    "count": len(stale_rows),
                    "messages": stale_rows,
                    "rearmed": rearmed_ids,
                    "watcher_state_path": str(self.watcher_state_path),
                },
            )

    def receipt_debt_cleanup(
        self,
        agent: Optional[str] = None,
        *,
        old_after_seconds: Optional[int] = None,
        stale_after_seconds: int = STALE_UNREAD_DEFAULT_AGE_S,
        apply: bool = False,
        rearm_stale_unread: bool = False,
        limit: int = 50,
        body_preview_chars: int = 240,
    ) -> BridgeResult:
        """Report and optionally clean safe receipt debt.

        This is intentionally conservative. It never marks unread rows read.
        Apply mode only backfills ``seen_at`` for rows that already have
        ``read_at`` and optionally removes stale wake ids from watcher
        ``seen_ids`` so normal receipt-verified wake retry can fire again.
        """
        with self._locked():
            try:
                agents = [normalize_agent(agent)] if agent else sorted(AGENTS)
                if old_after_seconds is None:
                    try:
                        old_threshold = max(1, int(self._load_settings().poll_interval_seconds * 10))
                    except Exception:
                        old_threshold = 20
                else:
                    old_threshold = self._bounded_int("old_after_seconds", old_after_seconds, 1, 86400)
                stale_threshold = self._bounded_int("stale_after_seconds", stale_after_seconds, 1, 86400)
                bounded_limit = self._bounded_int("limit", limit, 1, 200)
                preview_chars = self._bounded_int("body_preview_chars", body_preview_chars, 0, 4000)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            watcher_state = read_json(
                self.watcher_state_path,
                {
                    "seen_ids": [],
                    "toasted_ids": [],
                    "pending_wake_verifications": [],
                    "paused_wake_messages": [],
                    "unknown_origin_warnings": [],
                    "wake_fire_history": [],
                },
            )
            watcher_seen_ids = {str(item) for item in watcher_state.get("seen_ids", []) if str(item)}
            now_dt = datetime.now(timezone.utc)
            now = utc_now()
            totals = {
                "read_without_seen": 0,
                "old_unread": 0,
                "stale_unread": 0,
                "seen_backfilled": 0,
                "stale_rearmed": 0,
            }
            categories: Dict[str, List[Dict[str, Any]]] = {
                "read_without_seen": [],
                "old_unread": [],
                "stale_unread": [],
            }
            stale_unread_ids: set = set()
            rearmed_ids: List[str] = []
            changed_agents: List[str] = []

            def _sample(category: str, row_agent: str, row: Dict[str, Any], age_seconds: Optional[int]) -> None:
                if len(categories[category]) >= bounded_limit:
                    return
                body = self._truncate_preview(row.get("body") or row.get("delivered_message") or "", preview_chars)
                categories[category].append(
                    {
                        "agent": row_agent,
                        "message_id": row.get("id"),
                        "session_id": row.get("session_id") or DEFAULT_SESSION_ID,
                        "from": row.get("from"),
                        "created_at": row.get("created_at"),
                        "age_seconds": age_seconds,
                        "seen_at": row.get("seen_at"),
                        "seen_via": row.get("seen_via"),
                        "read_at": row.get("read_at"),
                        "handled_at": row.get("handled_at"),
                        "lifecycle_status": self._message_lifecycle_status(row),
                        "body_preview": body["preview"],
                        "body_truncated": body["truncated"],
                        "body_length": body["length"],
                    }
                )

            for target in agents:
                identity = self._identity(target, None)
                path = self.inbox_path(target)
                rows = self.transport.read_inbox(identity, path, unread_only=False)
                changed = False
                for row in rows:
                    message_id = str(row.get("id") or "")
                    if not message_id or row.get("superseded_at"):
                        continue
                    age = self._health_age_seconds(row.get("created_at"), now_dt)
                    if row.get("read_at") and not row.get("seen_at"):
                        totals["read_without_seen"] += 1
                        _sample("read_without_seen", target, row, age)
                        if apply:
                            row["seen_at"] = row.get("read_at") or now
                            row["seen_by_session"] = row.get("session_id")
                            row["seen_via"] = "receipt_debt_cleanup:read_backfill"
                            totals["seen_backfilled"] += 1
                            changed = True
                    if not row.get("read_at"):
                        if age is not None and age >= old_threshold:
                            totals["old_unread"] += 1
                            _sample("old_unread", target, row, age)
                        if (
                            message_id in watcher_seen_ids
                            and age is not None
                            and age >= stale_threshold
                        ):
                            totals["stale_unread"] += 1
                            stale_unread_ids.add(message_id)
                            _sample("stale_unread", target, row, age)
                if changed:
                    self.transport.write_inbox_rows(identity, path, rows)
                    changed_agents.append(target)

            if apply and rearm_stale_unread and totals["stale_unread"]:
                original_seen = [str(item) for item in watcher_state.get("seen_ids", []) if str(item)]
                watcher_state["seen_ids"] = [item for item in original_seen if item not in stale_unread_ids]
                rearmed_ids = [item for item in original_seen if item in stale_unread_ids]
                if rearmed_ids:
                    totals["stale_rearmed"] = len(rearmed_ids)
                    write_json(self.watcher_state_path, watcher_state)

            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "receipt_debt_cleanup",
                    "accepted": True,
                    "agent": agent,
                    "apply": bool(apply),
                    "old_after_seconds": old_threshold,
                    "stale_after_seconds": stale_threshold,
                    "rearm_stale_unread": bool(rearm_stale_unread),
                    "totals": totals,
                    "changed_agents": changed_agents,
                    "rearmed_ids": rearmed_ids,
                }
            )
            status = "cleaned" if apply and (totals["seen_backfilled"] or totals["stale_rearmed"]) else "debt_found"
            if not (totals["read_without_seen"] or totals["old_unread"] or totals["stale_unread"]):
                status = "empty"
            return BridgeResult(
                True,
                status,
                (
                    "Receipt debt report: read_without_seen=%d old_unread=%d "
                    "stale_unread=%d; backfilled=%d rearmed=%d."
                )
                % (
                    totals["read_without_seen"],
                    totals["old_unread"],
                    totals["stale_unread"],
                    totals["seen_backfilled"],
                    totals["stale_rearmed"],
                ),
                {
                    "apply": bool(apply),
                    "old_after_seconds": old_threshold,
                    "stale_after_seconds": stale_threshold,
                    "rearm_stale_unread": bool(rearm_stale_unread),
                    "limit": bounded_limit,
                    "totals": totals,
                    "categories": categories,
                    "changed_agents": changed_agents,
                    "rearmed": rearmed_ids,
                    "watcher_state_path": str(self.watcher_state_path),
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
                self._set_next_override_wake_message("User says check bridge inbox")
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
            # Derive the initiator label from the target: whoever is nudging
            # Codex must be Claude, and vice versa.
            override_msg = (
                "Claude says check bridge inbox" if target == "codex"
                else "Codex says check bridge inbox"
            )
            if self._wake_breaker_open_for_session(session):
                grant = self._grant_wake_breaker_bypass(
                    session,
                    reason="nudge_peer",
                    granted_by=target,
                    force=False,
                )
                # Bypass path: watcher fires next on its own; plant the override now.
                self._set_next_override_wake_message(override_msg)
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
                nudge_wake_message=override_msg,
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

    def classify_execution_interrupt(
        self,
        owner_agent: str,
        task_id: str,
        disposition: str,
        *,
        reason: Optional[str] = None,
        interrupt_kind: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> BridgeResult:
        """Record the mandatory WGI-10 disposition for an interrupt during active work."""
        with self._locked():
            try:
                owner = normalize_agent(owner_agent)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            disposition_value = (disposition or "").strip().lower()
            allowed = {"resume", "complete", "blocked", "parked", "displaced"}
            if disposition_value not in allowed:
                return BridgeResult(False, "rejected", "disposition must be resume, complete, blocked, parked, or displaced")
            payload = self._load_execution_state()
            pending = self._load_pending_actions()
            record = self._owner_execution_record(payload, owner)
            active = record.get("active_task")
            if not active or str(active.get("id") or "") != (task_id or "").strip():
                return BridgeResult(False, "not_found", "Active execution task %s not found for %s." % (task_id, owner))

            now = utc_now()
            classification = {
                "disposition": disposition_value,
                "reason": (reason or "").strip() or None,
                "interrupt_kind": (interrupt_kind or "").strip() or None,
                "message_id": (message_id or "").strip() or None,
                "classified_at": now,
            }
            active.setdefault("interrupt_classifications", []).append(classification)
            active["interrupt_classifications"] = active["interrupt_classifications"][-20:]
            active["latest_interrupt_classification"] = classification
            active["updated_at"] = now
            record["updated_at"] = now

            if disposition_value == "resume":
                active["status"] = "active"
                active["proof_status"] = active.get("proof_status") or "proved"
                self._save_execution_state(payload)
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": now,
                        "action": "classify_execution_interrupt",
                        "owner_agent": owner,
                        "task_id": task_id,
                        "disposition": disposition_value,
                        "accepted": True,
                    }
                )
                return BridgeResult(
                    True,
                    "interrupt_classified",
                    "Execution interrupt for %s classified as resume." % task_id,
                    {"task": self._derive_execution_task(active), "classification": classification},
                )

            outcome_value = "completed" if disposition_value == "complete" else disposition_value
            closed = copy.deepcopy(active)
            closed["status"] = outcome_value
            closed["completed_at"] = now
            closed["resolution"] = classification["reason"]
            record.setdefault("recent_tasks", []).append(closed)
            record["recent_tasks"] = record["recent_tasks"][-20:]
            record["active_task"] = None
            self._set_pending_action_execution_state(
                pending,
                action_id=closed.get("related_action_id"),
                execution_state=outcome_value,
                task_id=None,
            )
            if outcome_value == "completed" and closed.get("related_action_id"):
                for action in pending.get("actions", []):
                    if action.get("id") == closed.get("related_action_id") and action.get("status") != "resolved":
                        action["status"] = "resolved"
                        action["resolved_at"] = now
                        action["resolved_by"] = owner
                        action["resolution"] = closed.get("resolution")
                        action["updated_at"] = now
                        break
            self._save_execution_state(payload)
            self._save_pending_actions(pending)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "classify_execution_interrupt",
                    "owner_agent": owner,
                    "task_id": task_id,
                    "disposition": disposition_value,
                    "outcome": outcome_value,
                    "accepted": True,
                }
            )
            return BridgeResult(
                True,
                "interrupt_classified",
                "Execution interrupt for %s classified as %s." % (task_id, disposition_value),
                {"task": self._derive_execution_task(closed), "classification": classification},
            )

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
                identity = self._identity(agent, None)
                for row in self.transport.read_inbox(identity, self.inbox_path(agent), unread_only=False):
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
            target_identity = self._identity(target, session)
            rows = self.transport.read_inbox(target_identity, path, unread_only=False)
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
                self.transport.write_inbox_rows(target_identity, path, rows)
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
            target_identity = self._identity(target, session)
            rows = self.transport.read_inbox(target_identity, path, unread_only=False)
            now = utc_now()
            matched = False
            matched_row: Optional[Dict[str, Any]] = None
            for row in rows:
                if row.get("id") == target_id and (session is None or row.get("session_id") == session):
                    matched = True
                    matched_row = dict(row)
                    row["handled_at"] = now
                    row["handled_by_session"] = row.get("session_id")
                    row["handled_status"] = handled_status
                    row["failure_reason"] = (reason or None) if handled_status == "failed" else None
                    break
            if not matched:
                return BridgeResult(False, "not_found", "No message %s for %s." % (target_id, target))
            self.transport.write_inbox_rows(target_identity, path, rows)
            if matched_row is not None:
                self._record_review_result_handled(
                    handler_agent=target,
                    row=matched_row,
                    handled_status=handled_status,
                )
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
                identity = self._identity(target, None)
                for row in self.transport.read_inbox(identity, self.inbox_path(target), unread_only=False):
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
        now_dt = datetime.now(timezone.utc)
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
                    identity = process_runtime_identity_status(pid, entry.get("runtime"), expected_role="mcp_server")
                    entry.update(identity)
                    entry["stale"] = bool(entry.get("stale")) or not entry["running"] or bool(entry["identity_mismatch"])
                except (OSError, ValueError):
                    entry["stale"] = True
                server_markers.append(entry)

        mcp_reconnect = self._mcp_reconnect_summary(now_dt, server_markers)

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
            or mcp_reconnect.get("impact_class") in {"tool_access_risk", "client_reconnect_likely_required"}
            or mcp_reconnect.get("status") == "error"
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
                "mcp_reconnect": mcp_reconnect,
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

    def _mcp_reconnect_summary(self, now_dt: datetime, server_markers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Classify MCP wrapper relaunch and code-refresh impact.

        The wrapper owns the stdio pipe the MCP host connected to. MCP stdio
        initialization is stateful, so bridge code changes require a host
        reconnect instead of swapping `server.py` under an initialized client.
        """
        read = self._health_read_jsonl(self.audit_path, "mcp_reconnect")
        if read["status"] == "error":
            return {
                "status": "error",
                "path": read["path"],
                "error": read["error"],
                "impact_class": "unknown",
                "mcp_host_likely_reconnected": False,
            }

        rows = read["rows"]

        def row_time(row: Dict[str, Any]) -> Optional[datetime]:
            return parse_iso_datetime(row.get("timestamp"))

        wrapper_launches = [row for row in rows if row.get("action") == "mcp_server_wrapper_launch"]
        self_restarts = [row for row in rows if row.get("action") == "mcp_server_self_restarted"]
        refresh_required_events = [
            row
            for row in rows
            if row.get("action") in {"mcp_server_refresh_required", "mcp_tools_refresh_required"}
        ]
        today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        recent_cutoff = now_dt - timedelta(minutes=5)
        wrapper_launches_today = [row for row in wrapper_launches if (row_time(row) or datetime.min.replace(tzinfo=timezone.utc)) >= today_start]
        self_restarts_today = [row for row in self_restarts if (row_time(row) or datetime.min.replace(tzinfo=timezone.utc)) >= today_start]
        wrapper_launches_last_5m = [row for row in wrapper_launches if (row_time(row) or datetime.min.replace(tzinfo=timezone.utc)) >= recent_cutoff]

        latest_launch = None
        latest_launch_time = None
        if wrapper_launches:
            latest_launch = max(wrapper_launches, key=lambda row: row_time(row) or datetime.min.replace(tzinfo=timezone.utc))
            latest_launch_time = row_time(latest_launch)

        wrapper_pid: Optional[int] = None
        wrapper_running: Optional[bool] = None
        if latest_launch is not None:
            try:
                wrapper_pid = int(latest_launch.get("pid") or 0) or None
            except (TypeError, ValueError):
                wrapper_pid = None
            if wrapper_pid is not None:
                wrapper_running = is_process_alive(wrapper_pid)

        restarts_after_launch: List[Dict[str, Any]] = []
        if latest_launch_time is not None:
            restarts_after_launch = [
                row
                for row in self_restarts
                if (row_time(row) or datetime.min.replace(tzinfo=timezone.utc)) >= latest_launch_time
            ]
        latest_restart = None
        if restarts_after_launch:
            latest_restart = max(
                restarts_after_launch,
                key=lambda row: row_time(row) or datetime.min.replace(tzinfo=timezone.utc),
            )
        refresh_after_launch: List[Dict[str, Any]] = []
        if latest_launch_time is not None:
            refresh_after_launch = [
                row
                for row in refresh_required_events
                if (row_time(row) or datetime.min.replace(tzinfo=timezone.utc)) >= latest_launch_time
            ]
        latest_refresh = None
        latest_refresh_time = None
        if refresh_after_launch:
            latest_refresh = max(
                refresh_after_launch,
                key=lambda row: row_time(row) or datetime.min.replace(tzinfo=timezone.utc),
            )
            latest_refresh_time = row_time(latest_refresh)
        reconnect_required_since = latest_refresh_time or latest_launch_time

        tool_activity_actions = {"check_inbox", "wait_inbox", "mark_seen", "mark_read", "mark_handled", "send_to_peer"}
        post_launch_tool_events: List[Dict[str, Any]] = []
        if reconnect_required_since is not None:
            post_launch_tool_events = [
                row
                for row in rows
                if row.get("action") in tool_activity_actions
                and (row_time(row) or datetime.min.replace(tzinfo=timezone.utc)) > reconnect_required_since
            ]
        latest_tool_event = None
        if post_launch_tool_events:
            latest_tool_event = max(
                post_launch_tool_events,
                key=lambda row: row_time(row) or datetime.min.replace(tzinfo=timezone.utc),
            )

        inner_pid = None
        inner_running = None
        if wrapper_pid is not None:
            running_children = [
                entry
                for entry in server_markers
                if entry.get("running") and (entry.get("runtime") or {}).get("parent_pid") == wrapper_pid
            ]
            if running_children:
                inner_pid = running_children[-1].get("pid")
                inner_running = True
        if inner_pid is None and latest_restart is not None:
            inner_pid = latest_restart.get("new_child_pid")
            try:
                inner_running = is_process_alive(int(inner_pid)) if inner_pid is not None else None
            except (TypeError, ValueError):
                inner_running = None

        mcp_host_likely_reconnected = bool(post_launch_tool_events)
        impact_class = "unknown_no_wrapper_audit"
        if latest_launch is not None:
            recent_launch = latest_launch_time is not None and latest_launch_time >= recent_cutoff
            refresh_pending = latest_refresh is not None and not mcp_host_likely_reconnected
            if wrapper_running is False:
                impact_class = "client_reconnect_likely_required"
            elif refresh_pending:
                impact_class = "tool_access_risk"
            elif recent_launch and not mcp_host_likely_reconnected:
                impact_class = "tool_access_risk"
            elif restarts_after_launch:
                impact_class = "benign_hot_reload"
            else:
                impact_class = "connected_or_idle"

        return {
            "status": "ok" if read["status"] != "error" else "error",
            "path": read["path"],
            "bad_lines": read.get("bad_lines", 0),
            "latest_wrapper_launch": latest_launch,
            "wrapper_pid": wrapper_pid,
            "wrapper_running": wrapper_running,
            "wrapper_started_at": latest_launch.get("timestamp") if latest_launch else None,
            "wrapper_launch_count_today": len(wrapper_launches_today),
            "wrapper_launches_last_5m": len(wrapper_launches_last_5m),
            "inner_pid": inner_pid,
            "inner_running": inner_running,
            "inner_last_restart_at": latest_restart.get("timestamp") if latest_restart else None,
            "inner_restart_count_today": len(self_restarts_today),
            "latest_refresh_required": latest_refresh,
            "latest_refresh_required_at": latest_refresh.get("timestamp") if latest_refresh else None,
            "mcp_host_likely_reconnected": mcp_host_likely_reconnected,
            "last_tool_activity_at": latest_tool_event.get("timestamp") if latest_tool_event else None,
            "last_tool_activity": latest_tool_event,
            "impact_class": impact_class,
            "reconnect_required": impact_class in {"tool_access_risk", "client_reconnect_likely_required"},
            "error": read["error"],
        }

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
                runtime_display = self._runtime_display_for_session(
                    record.get("agent"),
                    session_id=session_id,
                    project=project_name,
                )
                session_summary = {
                    "agent": record.get("agent"),
                    "status": record.get("status"),
                    "bootstrap_origin": record.get("bootstrap_origin", "unknown"),
                    "started_at": record.get("started_at"),
                    "updated_at": record.get("updated_at"),
                    "age_seconds": self._health_age_seconds(record.get("started_at"), now_dt),
                    "display_label": self._session_display_label(session_id, runtime_display),
                    **runtime_display,
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
        try:
            unread_threshold_seconds = max(1, int(self._load_settings().poll_interval_seconds * 10))
        except Exception:
            unread_threshold_seconds = 20
        default_watcher_state = {
            "seen_ids": [],
            "toasted_ids": [],
            "pending_wake_verifications": [],
            "paused_wake_messages": [],
            "unknown_origin_warnings": [],
            "wake_fire_history": [],
        }
        watcher_read = self._health_read_json(self.watcher_state_path, default_watcher_state, "watcher_state")
        watcher_state = watcher_read["data"] if isinstance(watcher_read["data"], dict) else default_watcher_state
        watcher_seen_ids = {str(item) for item in watcher_state.get("seen_ids", []) if str(item)}
        buckets: List[Dict[str, Any]] = []
        totals = {
            "unread_count": 0,
            "unread_work_count": 0,
            "old_unread_over_threshold_count": 0,
            "old_unread_threshold_seconds": unread_threshold_seconds,
            "stale_unread_count": 0,
            "stale_unread_oldest_age_seconds": None,
            "handled_not_seen_count": 0,
            "bad_lines": 0,
        }
        errors: List[Dict[str, Any]] = []
        if watcher_read["status"] == "error":
            errors.append({"metric": "watcher_state", "error": watcher_read["error"]})
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
                        "unread_work_count": 0,
                        "oldest_unread_age_seconds": None,
                        "old_unread_over_threshold_count": 0,
                        "stale_unread_count": 0,
                        "stale_unread_oldest_age_seconds": None,
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
                    if row.get("marker_variant") != "control":
                        entry["unread_work_count"] += 1
                        totals["unread_work_count"] += 1
                    if created_age is not None and (
                        entry["oldest_unread_age_seconds"] is None
                        or created_age > entry["oldest_unread_age_seconds"]
                    ):
                        entry["oldest_unread_age_seconds"] = created_age
                    if created_age is not None and created_age >= unread_threshold_seconds:
                        entry["old_unread_over_threshold_count"] += 1
                        totals["old_unread_over_threshold_count"] += 1
                    if (
                        str(row.get("id") or "") in watcher_seen_ids
                        and created_age is not None
                        and created_age >= STALE_UNREAD_DEFAULT_AGE_S
                    ):
                        entry["stale_unread_count"] += 1
                        totals["stale_unread_count"] += 1
                        if (
                            entry["stale_unread_oldest_age_seconds"] is None
                            or created_age > entry["stale_unread_oldest_age_seconds"]
                        ):
                            entry["stale_unread_oldest_age_seconds"] = created_age
                        if (
                            totals["stale_unread_oldest_age_seconds"] is None
                            or created_age > totals["stale_unread_oldest_age_seconds"]
                        ):
                            totals["stale_unread_oldest_age_seconds"] = created_age
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

    def _health_stale_unread_watchdog(self, now_dt: datetime) -> Dict[str, Any]:
        read = self._health_read_jsonl(self.audit_path, "stale_unread_watchdog")
        events: List[Dict[str, Any]] = []
        cutoff = now_dt - timedelta(hours=24)
        for row in read["rows"][-500:]:
            if row.get("action") != "stale_unread_watchdog_rearmed":
                continue
            timestamp = parse_iso_datetime(row.get("timestamp"))
            if timestamp and timestamp < cutoff:
                continue
            events.append(
                {
                    "status": "STALE_UNREAD_WATCHDOG_REARMED",
                    "event_ts": row.get("timestamp"),
                    "agent": row.get("agent"),
                    "session_id": row.get("session_id"),
                    "project": row.get("project"),
                    "runtime_status": row.get("runtime_status"),
                    "rearmed_count": int(row.get("rearmed_count") or len(row.get("message_ids") or [])),
                    "message_ids": list(row.get("message_ids") or [])[:20],
                    "remediation_command": 'receipt_debt_cleanup(agent="%s", apply=false, rearm_stale_unread=true)'
                    % (row.get("agent") or "claude"),
                }
            )
        return {
            "status": "rearmed" if events else ("error" if read["status"] == "error" else "ok"),
            "path": read["path"],
            "rearm_count": len(events),
            "items": events[-20:],
            "error": read["error"],
        }

    def _claude_monitor_runtime_command(self, *, session_id: str, project: str) -> str:
        script = Path(__file__).resolve().parent / "bridge_monitor_poll.py"
        return (
            'Monitor(persistent=True, command="%s -u \\"%s\\" --state-dir \\"%s\\" '
            '--agent claude --session-id %s --project %s --poll-interval-seconds 2")'
            % (sys.executable, script, self.state_dir, session_id, project)
        )

    def _claude_monitor_runtime_status(self, *, session_id: str, project: str, now_dt: datetime) -> Dict[str, Any]:
        runtime_path = monitor_runtime_path_for_state_dir(self.state_dir, "claude", session_id)
        data = read_runtime_breadcrumb(runtime_path)
        result: Dict[str, Any] = {
            "path": str(runtime_path),
            "status": "missing",
            "fresh": False,
            "expected_buckets": [session_id, project],
            "remediation_command": self._claude_monitor_runtime_command(session_id=session_id, project=project),
        }
        if not data:
            return result
        result["data"] = data
        if data.get("unreadable"):
            result.update({"status": "unreadable", "reason": data.get("error") or "runtime_unreadable"})
            return result
        mismatches: List[str] = []
        if data.get("agent") != "claude":
            mismatches.append("agent")
        if data.get("session_id") != session_id:
            mismatches.append("session_id")
        if data.get("project") != project:
            mismatches.append("project")
        watched = {str(item) for item in data.get("watched_buckets") or [] if str(item)}
        if session_id not in watched or project not in watched:
            mismatches.append("watched_buckets")
        if str(data.get("script_name") or Path(str(data.get("script_path") or "")).name) != "bridge_monitor_poll.py":
            mismatches.append("script_path")
        pid = int(data.get("monitor_pid") or 0)
        if pid and not is_process_alive(pid):
            mismatches.append("monitor_pid")
        heartbeat = parse_iso_datetime(data.get("heartbeat_at"))
        try:
            ttl_seconds = max(MONITOR_RUNTIME_MIN_TTL_S, int(float(data.get("poll_interval_seconds") or 0) * 3))
        except (TypeError, ValueError):
            ttl_seconds = MONITOR_RUNTIME_MIN_TTL_S
        age_seconds = None
        if heartbeat:
            age_seconds = max(0, int((now_dt - heartbeat).total_seconds()))
        else:
            mismatches.append("heartbeat_at")
        result["age_seconds"] = age_seconds
        result["freshness_ttl_seconds"] = ttl_seconds
        if mismatches:
            result.update({"status": "misbound", "reason": ",".join(mismatches), "mismatches": mismatches})
            return result
        if age_seconds is None or age_seconds > ttl_seconds:
            result.update({"status": "stale", "reason": "heartbeat_expired"})
            return result
        result.update({"status": "current", "fresh": True})
        return result

    def _health_claude_monitor(self, active_sessions: Dict[str, Any], now_dt: datetime) -> Dict[str, Any]:
        read = self._health_read_jsonl(self.inbox_path("claude"), "claude_monitor_inbox")
        rows = read.get("rows") or []
        active_claude = [
            dict(item)
            for item in (active_sessions.get("active") or {}).values()
            if isinstance(item, dict) and item.get("agent") == "claude"
        ]
        items: List[Dict[str, Any]] = []
        sessions: List[Dict[str, Any]] = []
        for entry in active_claude:
            session_id = str(entry.get("session_id") or "")
            project = str(entry.get("project") or DEFAULT_PROJECT)
            if not session_id:
                continue
            runtime = self._claude_monitor_runtime_status(session_id=session_id, project=project, now_dt=now_dt)
            target_buckets = {session_id, project}
            unread: List[Dict[str, Any]] = []
            for row in rows:
                if row.get("read_at") or row.get("to") != "claude":
                    continue
                if str(row.get("session_id") or DEFAULT_SESSION_ID) not in target_buckets:
                    continue
                unread.append(row)
            oldest_age = None
            for row in unread:
                age = self._health_age_seconds(row.get("created_at"), now_dt)
                if age is not None and (oldest_age is None or age > oldest_age):
                    oldest_age = age
            status = "ok"
            if unread and runtime.get("status") in {"missing", "unreadable"}:
                status = "CLAUDE_UNREAD_WITHOUT_MONITOR"
            elif unread and runtime.get("status") != "current":
                status = "CLAUDE_MONITOR_STALE"
            session_report = {
                "status": status,
                "session_id": session_id,
                "project": project,
                "runtime": runtime,
                "unread_count": len(unread),
                "stuck_message_ids": [str(row.get("id") or "") for row in unread if row.get("id")][:10],
                "oldest_unread_age_seconds": oldest_age,
                "remediation_command": runtime.get("remediation_command"),
            }
            sessions.append(session_report)
            if status != "ok":
                items.append(session_report)
        return {
            "status": "degraded" if items else ("error" if read["status"] == "error" else "ok"),
            "session_count": len(sessions),
            "problem_count": len(items),
            "sessions": sessions,
            "items": items,
            "errors": [{"metric": "claude_monitor_inbox", "error": read.get("error")}] if read["status"] == "error" else [],
        }

    def _health_in_flight_wakes(self, now_dt: datetime, threshold_seconds: int) -> Dict[str, Any]:
        processes: List[Dict[str, Any]] = []
        default_watcher_state = {"pending_wake_verifications": []}
        read = self._health_read_json(self.watcher_state_path, default_watcher_state, "watcher_state")
        watcher_state = read["data"] if isinstance(read["data"], dict) else default_watcher_state
        if read["status"] == "error":
            unavailable = {"status": "unavailable", "count": 0, "processes": [], "error": read["error"]}
            return {
                "in_flight": unavailable,
                "stuck": {
                    "status": "unavailable",
                    "count": 0,
                    "threshold_seconds": threshold_seconds,
                    "processes": [],
                    "error": read["error"],
                },
            }
        for entry in watcher_state.get("pending_wake_verifications", []) or []:
            if not isinstance(entry, dict):
                continue
            sent_at = parse_iso_datetime(entry.get("sent_at"))
            age = max(0, int((now_dt - sent_at).total_seconds())) if sent_at else None
            processes.append(
                {
                    "pid": None,
                    "name": "watcher_pending_wake",
                    "target_agent": entry.get("agent"),
                    "target_session": entry.get("session_id"),
                    "message_id": entry.get("message_id"),
                    "started_at": sent_at.isoformat(timespec="seconds") if sent_at else entry.get("sent_at"),
                    "age_seconds": age,
                    "retry_count": entry.get("retry_count"),
                    "source": "watcher_state",
                }
            )
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
            "implementation_journal": self.implementation_journal_path,
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
        stale_unread_watchdog = core.get("stale_unread_watchdog") or {}
        claude_monitor = core.get("claude_monitor") or {}
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
        if int((core.get("backpressure") or {}).get("blocked_count") or 0) > 0:
            return "degraded"
        if int((core.get("backpressure") or {}).get("warning_count") or 0) > 0:
            return "degraded"
        if stale_unread_watchdog.get("status") == "error":
            return "degraded"
        if (server.get("mcp_reconnect") or {}).get("impact_class") in {
            "tool_access_risk",
            "client_reconnect_likely_required",
        }:
            return "degraded"
        if int(claude_monitor.get("problem_count") or 0) > 0:
            return "degraded"
        if (wake_breaker.get("open_session_count") or 0) > 0:
            return "degraded"
        if (inboxes.get("totals") or {}).get("old_unread_over_threshold_count", 0) > 0:
            return "degraded"
        if (inboxes.get("totals") or {}).get("stale_unread_count", 0) > 0:
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

    def _health_recommended_actions(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        core = snapshot.get("core") or {}
        extended = snapshot.get("extended") or {}
        caller = (snapshot.get("caller") or {}).get("agent") or "codex"
        bridge_state = core.get("bridge_state") or {}
        watcher = core.get("watcher") or {}
        server = core.get("server") or {}
        stuck_wakes = core.get("stuck_wakes") or {}
        inboxes = core.get("inboxes") or {}
        stale_unread_watchdog = core.get("stale_unread_watchdog") or {}
        backpressure = core.get("backpressure") or {}
        claude_monitor = core.get("claude_monitor") or {}
        totals = inboxes.get("totals") or {}
        actions: List[Dict[str, Any]] = []

        def add(
            action_id: str,
            severity: str,
            reason: str,
            command: str,
            *,
            safe_to_run: bool = True,
            mutates_state: bool = False,
        ) -> None:
            actions.append(
                {
                    "id": action_id,
                    "severity": severity,
                    "reason": reason,
                    "command": command,
                    "safe_to_run": safe_to_run,
                    "mutates_state": mutates_state,
                }
            )

        if bridge_state.get("paused"):
            add(
                "resume_bridge",
                "info",
                "Bridge is paused; messages will not route normally until resumed.",
                "resume_bridge()",
                mutates_state=True,
            )
        if watcher.get("stale") or (watcher and watcher.get("running") is False and not bridge_state.get("paused")):
            add(
                "restart_watcher",
                "high",
                "Watcher is stale or not running, so wake/toast delivery may not fire.",
                'ensure_watcher(reason="signature_changed")',
                mutates_state=True,
            )
        if int(server.get("stale_server_marker_count") or 0) > 0:
            add(
                "compact_stale_server_markers",
                "high",
                "Stale MCP server PID markers make process health look broken.",
                'py -3 tools/agent-bridge/compact.py --state-dir "%s" --server-pid-max-age-hours 0'
                % self.state_dir,
                mutates_state=True,
            )
        mcp_reconnect = server.get("mcp_reconnect") or {}
        if mcp_reconnect.get("impact_class") in {"tool_access_risk", "client_reconnect_likely_required"}:
            add(
                "reconnect_mcp_host",
                "high",
                "MCP wrapper relaunched and the host has not proven tool access after that launch.",
                "Restart/reconnect the MCP host, then rerun bridge_health_panel(agent=\"%s\", include_extended=true)."
                % caller,
                safe_to_run=False,
            )
        if int(backpressure.get("blocked_count") or 0) > 0:
            first = (backpressure.get("items") or [{}])[0]
            add(
                "clear_backpressure_blocker",
                "high",
                "Backpressure is blocking %s from sending to %s bucket %s; run a dry-run receipt diagnostic before mutating state."
                % (
                    first.get("blocked_sender_agent") or "a sender",
                    first.get("receiver_agent") or "receiver",
                    first.get("receiver_session_id") or "<unknown>",
                ),
                first.get("remediation_command")
                or 'receipt_debt_cleanup(agent="%s", apply=false, rearm_stale_unread=true)'
                % (first.get("receiver_agent") or caller),
                safe_to_run=bool(first.get("remediation_safe_to_run", True)),
                mutates_state=bool(first.get("remediation_mutates_state", False)),
            )
        elif int(backpressure.get("warning_count") or 0) > 0:
            first = (backpressure.get("items") or [{}])[0]
            add(
                "drain_backpressure_warning",
                "normal",
                "%s bucket %s is approaching the unread work backpressure limit (%s/%s)."
                % (
                    first.get("receiver_agent") or "receiver",
                    first.get("receiver_session_id") or "<bucket>",
                    first.get("unread_work_count") or 0,
                    first.get("limit") or "?",
                ),
                'check_inbox(agent="%s", session_id="%s", mark_read=false)'
                % (first.get("receiver_agent") or caller, first.get("receiver_session_id") or "<bucket>"),
                safe_to_run=False,
            )
        if int(claude_monitor.get("problem_count") or 0) > 0:
            first_monitor = (claude_monitor.get("items") or [{}])[0]
            add(
                "arm_claude_monitor",
                "high",
                "%s for Claude session %s; unread work will not be surfaced reliably until the Monitor is armed."
                % (
                    first_monitor.get("status") or "CLAUDE_MONITOR_STALE",
                    first_monitor.get("session_id") or "<unknown>",
                ),
                first_monitor.get("remediation_command") or "Start bridge_monitor_poll.py for the active Claude session.",
                safe_to_run=False,
            )
        if int(stuck_wakes.get("count") or 0) > 0:
            add(
                "inspect_stuck_wakes",
                "high",
                "One or more wake attempts are past the stuck threshold.",
                "wake_fire_history(limit=20); wake_breaker_status()",
            )
        if int(totals.get("stale_unread_count") or 0) > 0:
            add(
                "rearm_stale_unread",
                "high",
                "Wake succeeded but messages remain unread; rearm only stale ids for normal wake retry.",
                'receipt_debt_cleanup(agent="%s", apply=true, rearm_stale_unread=true)' % caller,
                mutates_state=True,
            )
        if int(stale_unread_watchdog.get("rearm_count") or 0) > 0:
            first_rearm = (stale_unread_watchdog.get("items") or [{}])[-1]
            add(
                "inspect_stale_unread_watchdog",
                "normal",
                "The stale-unread watchdog re-armed %s message(s) for %s session %s."
                % (
                    first_rearm.get("rearmed_count") or 0,
                    first_rearm.get("agent") or "agent",
                    first_rearm.get("session_id") or "<unknown>",
                ),
                first_rearm.get("remediation_command")
                or 'receipt_debt_cleanup(agent="%s", apply=false, rearm_stale_unread=true)' % caller,
            )
        if int(totals.get("handled_not_seen_count") or 0) > 0:
            add(
                "backfill_read_receipts",
                "normal",
                "Some rows were marked read before seen_at existed; safe migration can backfill seen_at.",
                'receipt_debt_cleanup(agent="%s", apply=true)' % caller,
                mutates_state=True,
            )
        if int(totals.get("old_unread_over_threshold_count") or 0) > 0:
            add(
                "read_old_inbox",
                "normal",
                "Old unread rows require an actual receiver inbox read; cleanup will not mark them read for you.",
                'check_inbox(agent="%s", session_id="<active-or-project-bucket>")' % caller,
                safe_to_run=False,
            )
        wake_breaker = extended.get("wake_breaker") or {}
        if int(wake_breaker.get("open_session_count") or 0) > 0:
            add(
                "inspect_wake_breaker",
                "high",
                "At least one session wake breaker is open.",
                "wake_breaker_status()",
            )
        pending = extended.get("pending_actions") or {}
        if int(pending.get("unresolved_actionable_count") or 0) > 0:
            add(
                "drain_pending_ledger",
                "normal",
                "Codex-owned actionable ledger work remains.",
                'next_pending_bridge_action(owner_agent="%s")' % caller,
            )
        recent_failures = extended.get("recent_failures") or {}
        if int(recent_failures.get("count") or 0) > 0:
            add(
                "inspect_recent_failures",
                "normal",
                "Recent bridge failures or rejections were observed.",
                'audit_timeline(agent="%s", max_count=50)' % caller,
            )
        provenance = extended.get("provenance") or {}
        if provenance.get("subagent_owns_active"):
            add(
                "repair_parent_pairing",
                "high",
                "A subagent appears to own an active bridge slot.",
                'Pair the intended parent thread explicitly, e.g. "pair this chat".',
                safe_to_run=False,
                mutates_state=True,
            )
        if not actions:
            add(
                "no_action_needed",
                "info",
                "No bridge remediation is currently recommended.",
                "No action.",
                safe_to_run=True,
            )
        return actions

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
        claude_monitor = core.get("claude_monitor") or {}
        lines.extend(
            [
                "| Bridge state | %s | paused=%s |" % (bridge_state.get("status"), bridge_state.get("paused")),
                "| Watcher | %s | pid=%s running=%s stale=%s |"
                % (watcher.get("status", "ok"), watcher.get("pid"), watcher.get("running"), watcher.get("stale")),
                "| MCP servers | ok | markers=%s stale=%s |"
                % (server.get("mcp_server_marker_count", 0), server.get("stale_server_marker_count", 0)),
                "| MCP reconnect | %s | wrapper=%s running=%s host_reconnected=%s |"
                % (
                    (server.get("mcp_reconnect") or {}).get("impact_class", "unknown"),
                    (server.get("mcp_reconnect") or {}).get("wrapper_pid"),
                    (server.get("mcp_reconnect") or {}).get("wrapper_running"),
                    (server.get("mcp_reconnect") or {}).get("mcp_host_likely_reconnected"),
                ),
                "| In-flight wakes | %s | count=%s |" % (in_flight.get("status"), in_flight.get("count", 0)),
                "| Stuck wakes | %s | count=%s |" % (stuck.get("status"), stuck.get("count", 0)),
                "| Inboxes | %s | unread=%s handled-not-seen=%s |"
                % (
                    inboxes.get("status"),
                    (inboxes.get("totals") or {}).get("unread_count", 0),
                    (inboxes.get("totals") or {}).get("handled_not_seen_count", 0),
                ),
                "| Backpressure | %s | blocked=%s |"
                % (
                    (core.get("backpressure") or {}).get("status", "ok"),
                    (core.get("backpressure") or {}).get("blocked_count", 0),
                ),
                "| Claude Monitor | %s | problems=%s |"
                % (
                    claude_monitor.get("status", "ok"),
                    claude_monitor.get("problem_count", 0),
                ),
                "| Old unread | %s | threshold=%ss |"
                % (
                    (inboxes.get("totals") or {}).get("old_unread_over_threshold_count", 0),
                    (inboxes.get("totals") or {}).get("old_unread_threshold_seconds"),
                ),
                "| Stale unread | %s | oldest=%ss |"
                % (
                    (inboxes.get("totals") or {}).get("stale_unread_count", 0),
                    (inboxes.get("totals") or {}).get("stale_unread_oldest_age_seconds"),
                ),
            ]
        )
        recommendations = snapshot.get("recommended_actions") or []
        if recommendations:
            lines.extend(["", "## Recommended Actions", "", "| Severity | Action | Command |", "|---|---|---|"])
            for item in recommendations[:8]:
                lines.append(
                    "| %s | %s | `%s` |"
                    % (
                        self._markdown_cell(item.get("severity")),
                        self._markdown_cell(item.get("reason")),
                        self._markdown_cell(item.get("command")),
                    )
                )
        sessions = (core.get("active_sessions") or {}).get("active") or {}
        lines.extend(["", "## Active Sessions", "", "| Project/Agent | Session | Title | Origin |", "|---|---|---|---|"])
        if sessions:
            for key, session in sorted(sessions.items()):
                lines.append(
                    "| %s | `%s` | %s | %s |"
                    % (
                        key,
                        session.get("session_id"),
                        self._markdown_cell(session.get("desktop_thread_title") or ""),
                        session.get("bootstrap_origin", "unknown"),
                    )
                )
        else:
            lines.append("| none |  |  |  |")
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
        stale_unread_watchdog = self._health_stale_unread_watchdog(now_dt)
        if stale_unread_watchdog.get("status") == "error":
            errors.append({"metric": "stale_unread_watchdog", "error": stale_unread_watchdog.get("error")})
        claude_monitor = self._health_claude_monitor(active_sessions, now_dt)
        errors.extend(claude_monitor.get("errors") or [])
        registry_data = registry_read["data"] if isinstance(registry_read["data"], dict) else self._default_session_registry()
        backpressure = self._backpressure_blocked_status(state_data, registry_data)

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
                    "mcp_reconnect": process_data.get("mcp_reconnect", {}),
                },
                "in_flight_wakes": wake_processes["in_flight"],
                "stuck_wakes": wake_processes["stuck"],
                "inboxes": inboxes,
                "stale_unread_watchdog": stale_unread_watchdog,
                "claude_monitor": claude_monitor,
                "backpressure": backpressure,
            },
            "extended": extended if include_extended else {},
            "errors": errors,
        }
        snapshot["overall_status"] = self._derive_health_status(snapshot)
        snapshot["recommended_actions"] = self._health_recommended_actions(snapshot)
        snapshot["recovery_hint"] = (
            snapshot["recommended_actions"][0].get("command")
            if snapshot.get("recommended_actions")
            and snapshot["recommended_actions"][0].get("id") != "no_action_needed"
            else None
        )
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

    def _short_id(self, value: Optional[Any]) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        return text[:8]

    def _runtime_display_for_session(
        self,
        agent: Optional[Any],
        *,
        session_id: Optional[Any] = None,
        project: Optional[Any] = None,
    ) -> Dict[str, Any]:
        agent_text = str(agent or "").strip().lower()
        if agent_text not in AGENTS:
            return {}
        breadcrumb = read_runtime_breadcrumb(peer_runtime_path_for_state_dir(self.state_dir, agent_text))
        if not isinstance(breadcrumb, dict) or breadcrumb.get("unreadable"):
            return {}
        if str(breadcrumb.get("agent") or "").strip().lower() not in {"", agent_text}:
            return {}
        session_text = str(session_id or "").strip()
        if session_text and str(breadcrumb.get("session_id") or "").strip() != session_text:
            return {}
        project_text = str(project or "").strip()
        if project_text and str(breadcrumb.get("project") or "").strip() != project_text:
            return {}
        title_project_match = breadcrumb.get("desktop_thread_title_project_match")
        title = "" if title_project_match is False else str(breadcrumb.get("desktop_thread_title") or "").strip()
        return {
            "desktop_thread_title": title or None,
            "desktop_thread_title_source": breadcrumb.get("desktop_thread_title_source"),
            "desktop_thread_title_observed_at": breadcrumb.get("desktop_thread_title_observed_at"),
            "desktop_thread_title_project_match": title_project_match,
            "desktop_window_title": breadcrumb.get("desktop_window_title"),
            "desktop_thread_id": breadcrumb.get("desktop_thread_id"),
            "last_wake_postflight_action": breadcrumb.get("last_wake_postflight_action"),
            "last_wake_postflight_reason": breadcrumb.get("last_wake_postflight_reason"),
            "last_wake_postflight_at": breadcrumb.get("last_wake_postflight_at"),
            "last_wake_delivery_priority_action": breadcrumb.get("last_wake_delivery_priority_action"),
            "last_wake_delivery_priority_at": breadcrumb.get("last_wake_delivery_priority_at"),
            "last_wake_delivery_priority_target_thread_id": breadcrumb.get("last_wake_delivery_priority_target_thread_id"),
            "last_wake_delivery_priority_previous_thread_title": breadcrumb.get(
                "last_wake_delivery_priority_previous_thread_title"
            ),
            "last_wake_delivery_priority_expected_thread_title": breadcrumb.get(
                "last_wake_delivery_priority_expected_thread_title"
            ),
        }

    def _session_display_label(self, session_id: Optional[Any], runtime_display: Dict[str, Any]) -> Optional[str]:
        short = self._short_id(session_id)
        title = str((runtime_display or {}).get("desktop_thread_title") or "").strip()
        if title and short:
            return "%s (%s)" % (title, short)
        return title or short

    def _markdown_cell(self, value: Any) -> str:
        text = str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
        return html.escape(text, quote=True).replace("`", "&#96;")

    def _matching_pairing_sessions(
        self,
        registry: Dict[str, Any],
        *,
        agent: str,
        project: Optional[str],
        session_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        matches: List[Dict[str, Any]] = []
        for current_project, project_entry in (registry.get("projects") or {}).items():
            if project and current_project != project:
                continue
            if not isinstance(project_entry, dict):
                continue
            active = project_entry.get("active") or {}
            sessions = project_entry.get("sessions") or {}
            target_session = session_id or active.get(agent)
            if not target_session:
                continue
            record = sessions.get(target_session)
            if not isinstance(record, dict) or record.get("agent") != agent:
                continue
            matches.append(
                {
                    "project": current_project,
                    "project_entry": project_entry,
                    "session_id": str(target_session),
                    "record": record,
                }
            )
        return matches

    def _guided_pairing_actions(
        self,
        project_entry: Dict[str, Any],
        *,
        agent: str,
        session_id: str,
        record: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        active = project_entry.get("active") or {}
        status = str(record.get("status") or "unknown")
        bootstrap_origin = str(record.get("bootstrap_origin") or "unknown")
        is_active_primary = active.get(agent) == session_id and status == "active"
        is_subagent = bootstrap_origin == "subagent"
        current_active = active.get(agent)
        actions: List[Dict[str, Any]] = [
            {
                "decision": "active_primary",
                "label": "Make active primary",
                "enabled": not is_subagent,
                "confirmation_required": not is_active_primary,
                "effect": (
                    "already_active"
                    if is_active_primary
                    else ("supersede_current_primary" if current_active else "activate_primary")
                ),
                "disabled_reason": (
                    "subagent sessions cannot become active primary without an explicit repair path"
                    if is_subagent
                    else None
                ),
                "supersedes_session_id": current_active if current_active != session_id else None,
            }
        ]
        non_primary_enabled = not is_active_primary
        for role in ("background", "observer", "advisor", "auditor"):
            actions.append(
                {
                    "decision": role,
                    "label": "Keep as %s" % role,
                    "enabled": non_primary_enabled,
                    "confirmation_required": False,
                    "effect": "keep_non_primary",
                    "disabled_reason": (
                        "active primary sessions cannot be demoted by this guided action"
                        if not non_primary_enabled
                        else None
                    ),
                }
            )
        return actions

    def _pairing_details_payload(
        self,
        project_entry: Dict[str, Any],
        *,
        project: str,
        agent: str,
        session_id: str,
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        active = project_entry.get("active") or {}
        peer_agent = "claude" if agent == "codex" else "codex"
        active_pair = self._ensure_primary_pair_record(project_entry, project)
        actions = self._guided_pairing_actions(project_entry, agent=agent, session_id=session_id, record=record)
        runtime_display = self._runtime_display_for_session(agent, session_id=session_id, project=project)
        peer_runtime_display = self._runtime_display_for_session(
            peer_agent,
            session_id=active.get(peer_agent),
            project=project,
        )
        return {
            "schema_version": 1,
            "project": project,
            "agent": agent,
            "session_id": session_id,
            "short_session_id": self._short_id(session_id),
            "session_display": self._session_display_label(session_id, runtime_display),
            **runtime_display,
            "status": record.get("status"),
            "role": record.get("non_primary_role") or record.get("status"),
            "pairing_intent": record.get("pairing_intent"),
            "bootstrap_origin": record.get("bootstrap_origin", "unknown"),
            "is_active_primary": active.get(agent) == session_id and record.get("status") == "active",
            "active_session_id": active.get(agent),
            "peer_agent": peer_agent,
            "peer_session_id": active.get(peer_agent),
            "peer_session_display": self._session_display_label(active.get(peer_agent), peer_runtime_display),
            "peer_desktop_thread_title": peer_runtime_display.get("desktop_thread_title"),
            "pair_id": active_pair.get("pair_id") if isinstance(active_pair, dict) else None,
            "session": copy.deepcopy(record),
            "available_actions": actions,
        }

    def _dashboard_pairings(self, registry: Dict[str, Any], project_name: Optional[str]) -> List[Dict[str, Any]]:
        pairings: List[Dict[str, Any]] = []
        projects = registry.get("projects") or {}
        for current_project, project_entry in sorted(projects.items()):
            if project_name and current_project != project_name:
                continue
            if not isinstance(project_entry, dict):
                continue
            active = project_entry.get("active") or {}
            sessions = project_entry.get("sessions") or {}
            active_pair = self._ensure_primary_pair_record(project_entry, current_project)
            active_pair_id = active_pair.get("pair_id") if isinstance(active_pair, dict) else None
            for agent in sorted(AGENTS):
                session_id = active.get(agent)
                if not session_id:
                    continue
                peer_agent = "claude" if agent == "codex" else "codex"
                record = sessions.get(session_id) if isinstance(sessions, dict) else {}
                runtime_display = self._runtime_display_for_session(
                    agent,
                    session_id=session_id,
                    project=current_project,
                )
                peer_runtime_display = self._runtime_display_for_session(
                    peer_agent,
                    session_id=active.get(peer_agent),
                    project=current_project,
                )
                session_display = self._session_display_label(session_id, runtime_display)
                peer_session_display = self._session_display_label(active.get(peer_agent), peer_runtime_display)
                pairings.append(
                    {
                        "project": current_project,
                        "scope": "same_project",
                        "role": "primary",
                        "relationship": "bidirectional" if active.get(peer_agent) else "directed",
                        "agent": agent,
                        "session_id": session_id,
                        "short_session_id": self._short_id(session_id),
                        "session_display": session_display,
                        **runtime_display,
                        "pair_id": active_pair_id,
                        "peer_agent": peer_agent,
                        "peer_session_id": active.get(peer_agent),
                        "peer_short_session_id": self._short_id(active.get(peer_agent)),
                        "peer_session_display": peer_session_display,
                        "peer_desktop_thread_title": peer_runtime_display.get("desktop_thread_title"),
                        "status": record.get("status", "active") if isinstance(record, dict) else "active",
                        "bootstrap_origin": record.get("bootstrap_origin", "unknown") if isinstance(record, dict) else "unknown",
                        "available_actions": self._guided_pairing_actions(
                            project_entry,
                            agent=agent,
                            session_id=session_id,
                            record=record if isinstance(record, dict) else {"status": "active"},
                        ),
                        "friendly_name": "%s / Primary / %s %s"
                        % (current_project, agent.capitalize(), session_display or ""),
                        "peer_claimed_label": None,
                        "peer_label_trust": "untrusted",
                    }
                )
            for session_id, record in sorted((sessions or {}).items()):
                if not isinstance(record, dict) or record.get("status") not in NON_PRIMARY_PAIRING_STATUSES:
                    continue
                agent = str(record.get("agent") or "")
                peer_agent = "claude" if agent == "codex" else "codex"
                role = record.get("non_primary_role") or record.get("status")
                runtime_display = self._runtime_display_for_session(
                    agent,
                    session_id=session_id,
                    project=current_project,
                )
                peer_runtime_display = self._runtime_display_for_session(
                    peer_agent,
                    session_id=active.get(peer_agent),
                    project=current_project,
                )
                session_display = self._session_display_label(session_id, runtime_display)
                peer_session_display = self._session_display_label(active.get(peer_agent), peer_runtime_display)
                pairings.append(
                    {
                        "project": current_project,
                        "scope": "same_project",
                        "role": role,
                        "relationship": "observer",
                        "agent": agent,
                        "session_id": session_id,
                        "short_session_id": self._short_id(session_id),
                        "session_display": session_display,
                        **runtime_display,
                        "peer_agent": peer_agent,
                        "peer_session_id": active.get(peer_agent),
                        "peer_short_session_id": self._short_id(active.get(peer_agent)),
                        "peer_session_display": peer_session_display,
                        "peer_desktop_thread_title": peer_runtime_display.get("desktop_thread_title"),
                        "status": record.get("status"),
                        "bootstrap_origin": record.get("bootstrap_origin", "unknown"),
                        "pairing_intent": record.get("pairing_intent"),
                        "available_actions": self._guided_pairing_actions(
                            project_entry,
                            agent=agent,
                            session_id=session_id,
                            record=record,
                        ),
                        "friendly_name": "%s / %s / %s %s"
                        % (current_project, str(role).replace("_", "-"), agent.capitalize(), session_display or ""),
                        "peer_claimed_label": None,
                        "peer_label_trust": "untrusted",
                    }
                )
        return pairings

    def _dashboard_cross_project_contracts(self, project_name: Optional[str]) -> List[Dict[str, Any]]:
        now_dt = datetime.now(timezone.utc)
        contracts: List[Dict[str, Any]] = []
        for link in self._cross_project_link_records():
            if project_name and self._cross_project_side_for_project(link, project_name) is None:
                continue
            item = copy.deepcopy(link)
            item["derived_status"] = self._cross_project_link_status(link, now_dt=now_dt)
            item["short_link_id"] = self._short_id(item.get("link_id"))
            item["local_role"] = self._cross_project_side_for_project(link, project_name) if project_name else None
            created_at = parse_iso_datetime(item.get("created_at"))
            expires_at = parse_iso_datetime(item.get("expires_at"))
            item["seconds_until_expiration"] = (
                int((expires_at - now_dt).total_seconds()) if expires_at is not None else None
            )
            item["original_duration_seconds"] = (
                int((expires_at - created_at).total_seconds())
                if created_at is not None and expires_at is not None
                else None
            )
            aliases = item.get("local_aliases") if isinstance(item.get("local_aliases"), dict) else {}
            alias = aliases.get(project_name) if project_name else None
            item["local_alias"] = alias
            item["friendly_name"] = alias or "%s / %s / %s" % (
                item.get("advisor", {}).get("project"),
                item.get("permission_tier"),
                item["short_link_id"],
            )
            contracts.append(item)
        return contracts

    def _dashboard_pending_actions(self) -> Dict[str, Any]:
        read = self._health_read_json(self.pending_actions_path, self._default_pending_actions(), "pending_actions")
        if read["status"] == "error":
            return {"status": "error", "actions": [], "error": read["error"], "path": read["path"]}
        actions: List[Dict[str, Any]] = []
        raw_actions = read["data"].get("actions") if isinstance(read["data"], dict) else []
        if isinstance(raw_actions, list):
            for action in raw_actions:
                if not isinstance(action, dict):
                    continue
                item = copy.deepcopy(action)
                item["status"] = str(item.get("status") or "pending")
                if item["status"] == "pending":
                    actions.append(item)
        return {"status": "ok" if read["status"] in {"ok", "missing"} else read["status"], "actions": actions}

    def _dashboard_implementation_journal(self) -> Dict[str, Any]:
        read = self._health_read_json(
            self.implementation_journal_path,
            self._default_implementation_journal(),
            "implementation_journal",
        )
        if read["status"] == "error":
            return {
                "status": "error",
                "journal": self._default_implementation_journal(),
                "error": read["error"],
                "path": read["path"],
            }
        journal = copy.deepcopy(read["data"]) if isinstance(read["data"], dict) else self._default_implementation_journal()
        if not isinstance(journal.get("events"), list):
            journal["events"] = []
        if not isinstance(journal.get("peer_states"), dict):
            journal["peer_states"] = {}
        return {"status": "ok" if read["status"] in {"ok", "missing"} else read["status"], "journal": journal}

    def _dashboard_event_matches_project(
        self,
        event: Dict[str, Any],
        registry: Dict[str, Any],
        project_name: Optional[str],
    ) -> bool:
        if not project_name:
            return True
        related_session_id = str(event.get("related_session_id") or "").strip()
        if not related_session_id:
            return False
        if related_session_id == project_name:
            return True
        return self._bucket_info(registry, related_session_id).get("project") == project_name

    def _dashboard_catchup_status(
        self,
        journal: Dict[str, Any],
        *,
        registry: Dict[str, Any],
        project_name: Optional[str],
        registry_status: str = "ok",
        registry_error: Optional[str] = None,
        journal_status: str = "ok",
        journal_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        if journal_status == "error":
            return {
                "status": "unknown",
                "scope": "project" if project_name else "global",
                "project": project_name,
                "pending_event_count": 0,
                "pending_pair_count": 0,
                "pairs": [],
                "error": journal_error,
            }
        if project_name and registry_status != "ok":
            return {
                "status": "unknown",
                "scope": "project",
                "project": project_name,
                "pending_event_count": 0,
                "pending_pair_count": 0,
                "pairs": [],
                "error": "session registry unavailable for project-scoped catch-up filtering: %s"
                % (registry_error or registry_status or "unknown error"),
            }

        def sequence_of(event: Dict[str, Any]) -> int:
            try:
                return int(event.get("sequence") or 0)
            except (TypeError, ValueError):
                return 0

        events_by_pair: Dict[str, List[Dict[str, Any]]] = {}
        for event in journal.get("events") or []:
            if not isinstance(event, dict):
                continue
            owner = event.get("owner_agent")
            peer = event.get("peer_agent")
            if owner not in AGENTS or peer not in AGENTS:
                continue
            if not self._dashboard_event_matches_project(event, registry, project_name):
                continue
            key = self._journal_pair_key(str(owner), str(peer))
            events_by_pair.setdefault(key, []).append(event)

        states = journal.get("peer_states") if isinstance(journal.get("peer_states"), dict) else {}
        keys = sorted(set(events_by_pair))
        pairs: List[Dict[str, Any]] = []
        pending_total = 0
        for key in keys:
            state = states.get(key) if isinstance(states.get(key), dict) else {}
            key_parts = key.split("->", 1)
            owner_agent = state.get("owner_agent") or (key_parts[0] if key_parts else None)
            peer_agent = state.get("peer_agent") or (key_parts[1] if len(key_parts) == 2 else None)
            try:
                last_ack_sequence = int(state.get("last_ack_sequence") or 0)
            except (TypeError, ValueError):
                last_ack_sequence = 0
            events = sorted(events_by_pair.get(key, []), key=sequence_of)
            latest_sequence = max([sequence_of(event) for event in events] or [0])
            pending_events = [event for event in events if sequence_of(event) > last_ack_sequence]
            pending_total += len(pending_events)
            if pending_events:
                pairs.append(
                    {
                        "owner_agent": owner_agent,
                        "peer_agent": peer_agent,
                        "last_ack_sequence": last_ack_sequence,
                        "latest_sequence": latest_sequence,
                        "pending_count": len(pending_events),
                        "preview_events": [
                            {
                                "sequence": sequence_of(event),
                                "message_type": event.get("message_type"),
                                "summary": event.get("summary"),
                                "delivery_status": event.get("delivery_status"),
                            }
                            for event in pending_events[-3:]
                        ],
                    }
                )
        return {
            "status": "attention_required" if pending_total else "ok",
            "scope": "project" if project_name else "global",
            "project": project_name,
            "pending_event_count": pending_total,
            "pending_pair_count": len(pairs),
            "pairs": pairs[:10],
        }

    def _dashboard_backpressure_status(
        self,
        health_snapshot: Dict[str, Any],
        registry: Dict[str, Any],
        project_name: Optional[str],
    ) -> Dict[str, Any]:
        inboxes = ((health_snapshot.get("core") or {}).get("inboxes") or {}) if isinstance(health_snapshot, dict) else {}
        explicit = ((health_snapshot.get("core") or {}).get("backpressure") or {}) if isinstance(health_snapshot, dict) else {}
        if inboxes.get("status") == "error":
            return {
                "status": "unknown",
                "scope": "project" if project_name else "global",
                "project": project_name,
                "blocked_bucket_count": 0,
                "blocked_sender_count": 0,
                "unread_work_count": 0,
                "buckets": [],
                "items": [],
                "error": inboxes.get("errors"),
            }
        explicit_items = [copy.deepcopy(item) for item in explicit.get("items", []) if isinstance(item, dict)]
        blocked: List[Dict[str, Any]] = [item for item in explicit_items if item.get("status") == "BACKPRESSURE_BLOCKED"]
        warnings: List[Dict[str, Any]] = [item for item in explicit_items if item.get("status") == "BACKPRESSURE_WARN"]
        blocked_keys = {
            (item.get("receiver_agent"), item.get("receiver_session_id"))
            for item in blocked + warnings
        }
        unread_total = 0
        for bucket in inboxes.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            session_id = str(bucket.get("session_id") or DEFAULT_SESSION_ID)
            receiver = str(bucket.get("agent") or "").strip().lower()
            if receiver in AGENTS and not self._bucket_accepts_work_backpressure(registry, receiver, session_id):
                continue
            info = self._bucket_info(registry, session_id)
            if project_name and info.get("project") != project_name:
                continue
            unread_count = int(bucket.get("unread_work_count", bucket.get("unread_count") or 0) or 0)
            unread_total += unread_count
            limit = self._backpressure_limit_for_bucket(registry, session_id)
            if limit is None:
                continue
            key = (receiver, session_id)
            if key in blocked_keys:
                continue
            threshold = self._backpressure_warning_threshold(limit)
            if unread_count < threshold:
                continue
            item = {
                "status": "BACKPRESSURE_BLOCKED" if unread_count >= limit else "BACKPRESSURE_WARN",
                "receiver_agent": receiver,
                "receiver_session_id": session_id,
                "inbox_level": info.get("inbox_level"),
                "project": info.get("project"),
                "blocked_sender_agent": None,
                "blocked_sender_session": None,
                "blocked_senders": [],
                "unread_work_count": unread_count,
                "limit": limit,
                "warning_threshold": threshold,
                "oldest_unread_created_at": None,
                "blocked_since": None,
                "last_rejected_at": None,
                "recommendation": "Read and disposition the unread work item(s) in bucket %s." % session_id,
            }
            if unread_count >= limit:
                item.update(
                    {
                        "remediation_command": 'receipt_debt_cleanup(agent="%s", apply=false, rearm_stale_unread=true)'
                        % (bucket.get("agent") or "codex"),
                        "remediation_safe_to_run": True,
                        "remediation_mutates_state": False,
                    }
                )
            if unread_count >= limit:
                blocked.append(item)
            else:
                warnings.append(item)
        items = blocked + warnings
        return {
            "status": "blocked" if blocked else ("warning" if warnings else "ok"),
            "scope": "project" if project_name else "global",
            "project": project_name,
            "blocked_bucket_count": len(blocked),
            "warning_bucket_count": len(warnings),
            "blocked_sender_count": sum(len(item.get("blocked_senders") or []) for item in blocked),
            "unread_work_count": unread_total,
            "buckets": items[:10],
            "items": items[:10],
        }

    def _dashboard_contract_status(self, contracts: List[Dict[str, Any]]) -> Dict[str, Any]:
        reauth_required = [
            contract
            for contract in contracts
            if contract.get("derived_status") == "expired"
        ]
        revoked = [
            contract
            for contract in contracts
            if contract.get("derived_status") == "revoked"
        ]
        other_non_active = [
            contract
            for contract in contracts
            if contract.get("derived_status") not in {"active", "expired", "revoked"}
        ]
        expiring_soon = [
            contract
            for contract in contracts
            if contract.get("derived_status") == "active"
            and isinstance(contract.get("seconds_until_expiration"), int)
            and contract["seconds_until_expiration"] <= DASHBOARD_CONTRACT_EXPIRING_SOON_S
        ]
        if reauth_required:
            status = "action_required"
        elif other_non_active:
            status = "attention_required"
        elif expiring_soon:
            status = "warning"
        else:
            status = "ok"
        return {
            "status": status,
            "total_count": len(contracts),
            "active_count": len([contract for contract in contracts if contract.get("derived_status") == "active"]),
            "reauthorization_required_count": len(reauth_required),
            "revoked_count": len(revoked),
            "other_non_active_count": len(other_non_active),
            "expiring_soon_count": len(expiring_soon),
            "expiring_soon_threshold_seconds": DASHBOARD_CONTRACT_EXPIRING_SOON_S,
            "items": [
                {
                    "link_id": contract.get("link_id"),
                    "short_link_id": contract.get("short_link_id"),
                    "friendly_name": contract.get("friendly_name"),
                    "derived_status": contract.get("derived_status"),
                    "seconds_until_expiration": contract.get("seconds_until_expiration"),
                }
                for contract in (reauth_required + other_non_active + expiring_soon + revoked)[:10]
            ],
        }

    def _dashboard_guardrail_debt_status(
        self,
        registry: Dict[str, Any],
        project_name: Optional[str],
    ) -> Dict[str, Any]:
        read = self._health_read_jsonl(self.guardrail_debt_path, "guardrail_debt")
        if read["status"] == "error":
            return {
                "status": "unknown",
                "scope": "project" if project_name else "global",
                "project": project_name,
                "active_debt_count": 0,
                "by_severity": {},
                "by_enforcement_tier": {},
                "items": [],
                "error": read.get("error"),
            }
        terminal = {"clean", "closed", "resolved", "cancelled", "canceled", "superseded"}
        active: List[Dict[str, Any]] = []
        for row in read.get("rows") or []:
            if not isinstance(row, dict):
                continue
            debt_status = str(row.get("debt_status") or row.get("status") or "open").strip().lower()
            if debt_status in terminal:
                continue
            session_id = str(row.get("session_id") or "").strip()
            if project_name:
                if session_id == project_name:
                    pass
                elif not session_id or self._bucket_info(registry, session_id).get("project") != project_name:
                    continue
            item = {
                "debt_id": row.get("debt_id") or row.get("id"),
                "guard_id": row.get("guard_id"),
                "severity": str(row.get("severity") or "warning").strip().lower(),
                "enforcement_tier": str(row.get("enforcement_tier") or "unknown").strip().lower(),
                "owner_agent": row.get("owner_agent"),
                "session_id": session_id or None,
                "source_message_id": row.get("source_message_id"),
                "debt_status": debt_status,
                "detected_at": row.get("detected_at") or row.get("created_at"),
                "remediation": row.get("remediation"),
            }
            active.append(item)

        by_severity: Dict[str, int] = {}
        by_tier: Dict[str, int] = {}
        for item in active:
            severity = str(item.get("severity") or "warning")
            tier = str(item.get("enforcement_tier") or "unknown")
            by_severity[severity] = by_severity.get(severity, 0) + 1
            by_tier[tier] = by_tier.get(tier, 0) + 1
        return {
            "status": "action_required" if active else "ok",
            "scope": "project" if project_name else "global",
            "project": project_name,
            "active_debt_count": len(active),
            "by_severity": by_severity,
            "by_enforcement_tier": by_tier,
            "items": active[:20],
        }

    def _dashboard_policy_drift_status(self, validation: BridgeResult) -> Dict[str, Any]:
        if not validation.ok:
            return {
                "status": "unknown",
                "missing_doc_count": 0,
                "missing_docs": [],
                "doc_drift_count": 0,
                "doc_drift": [],
                "protected_doc_count": 0,
                "message": validation.message,
            }
        policy = validation.data.get("policy") if isinstance(validation.data, dict) else {}
        protected_docs = policy.get("protected_docs") if isinstance(policy, dict) else []
        missing_docs = validation.data.get("missing_docs") if isinstance(validation.data, dict) else []
        missing_docs = list(missing_docs or [])
        doc_drift = validation.data.get("doc_drift") if isinstance(validation.data, dict) else []
        doc_drift = list(doc_drift or [])
        return {
            "status": "warning" if missing_docs or doc_drift else "ok",
            "missing_doc_count": len(missing_docs),
            "missing_docs": missing_docs,
            "doc_drift_count": len(doc_drift),
            "doc_drift": doc_drift,
            "protected_doc_count": len(protected_docs or []),
            "source": policy.get("source") if isinstance(policy, dict) else None,
        }

    def _dashboard_read_status(
        self,
        *,
        registry_read: Dict[str, Any],
        pending_read: Dict[str, Any],
        journal_read: Dict[str, Any],
        audit_read: Dict[str, Any],
    ) -> Dict[str, Any]:
        components = {
            "session_registry": {
                "status": registry_read.get("status"),
                "path": registry_read.get("path"),
                "error": registry_read.get("error"),
            },
            "pending_actions": {
                "status": pending_read.get("status"),
                "path": pending_read.get("path"),
                "error": pending_read.get("error"),
            },
            "implementation_journal": {
                "status": journal_read.get("status"),
                "path": journal_read.get("path"),
                "error": journal_read.get("error"),
            },
            "audit": {
                "status": audit_read.get("status"),
                "path": audit_read.get("path"),
                "error": audit_read.get("error"),
                "bad_lines": int(audit_read.get("bad_lines") or 0),
            },
        }
        degraded = [
            name
            for name, component in components.items()
            if component.get("status") not in {"ok", "missing"}
            or int(component.get("bad_lines") or 0) > 0
        ]
        return {
            "status": "degraded" if degraded else "ok",
            "degraded_component_count": len(degraded),
            "degraded_components": degraded,
            "components": components,
        }

    def _render_dashboard_markdown(self, overview: Dict[str, Any]) -> str:
        health = overview.get("health") or {}
        recommended_actions = overview.get("recommended_actions") or []
        status_surfaces = overview.get("status_surfaces") or {}
        read_status = status_surfaces.get("dashboard_reads") or {}
        backpressure = status_surfaces.get("backpressure") or {}
        catchup = status_surfaces.get("catchup") or {}
        contracts = status_surfaces.get("contracts") or {}
        policy_drift = status_surfaces.get("policy_drift") or {}
        guardrail_debt = status_surfaces.get("guardrail_debt") or {}
        lines = [
            "# Bridge Admin Dashboard",
            "",
            "## Health",
            "",
            "| Field | Value |",
            "|---|---|",
            "| Overall | %s |" % self._markdown_cell(health.get("overall_status")),
            "| Recovery hint | `%s` |" % self._markdown_cell(health.get("recovery_hint") or "none"),
            "",
            "## Recommended Actions",
            "",
            "| Severity | Reason | Command |",
            "|---|---|---|",
        ]
        for item in recommended_actions[:8]:
            lines.append(
                "| %s | %s | %s |"
                % (
                    self._markdown_cell(item.get("severity")),
                    self._markdown_cell(item.get("reason")),
                    self._markdown_cell(item.get("command")),
                )
            )
        if not recommended_actions:
            lines.append("| info | No bridge remediation is currently recommended. | `No action.` |")
        lines.extend(
            [
                "",
                "## Status Surfaces",
                "",
                "| Area | Status | Detail |",
                "|---|---|---|",
                "| Dashboard reads | %s | %s degraded component(s). |"
                % (
                    self._markdown_cell(read_status.get("status")),
                    self._markdown_cell(read_status.get("degraded_component_count", 0)),
                ),
                "| Backpressure | %s | %s blocked bucket(s), %s unread work item(s). |"
                % (
                    self._markdown_cell(backpressure.get("status")),
                    self._markdown_cell(backpressure.get("blocked_bucket_count", 0)),
                    self._markdown_cell(backpressure.get("unread_work_count", 0)),
                ),
                "| Catch-up | %s | %s pending event(s) across %s pair(s). |"
                % (
                    self._markdown_cell(catchup.get("status")),
                    self._markdown_cell(catchup.get("pending_event_count", 0)),
                    self._markdown_cell(catchup.get("pending_pair_count", 0)),
                ),
                "| Contracts | %s | %s reauthorization-required, %s expiring soon, %s revoked. |"
                % (
                    self._markdown_cell(contracts.get("status")),
                    self._markdown_cell(contracts.get("reauthorization_required_count", 0)),
                    self._markdown_cell(contracts.get("expiring_soon_count", 0)),
                    self._markdown_cell(contracts.get("revoked_count", 0)),
                ),
                "| Policy/doc drift | %s | %s missing protected doc(s), %s contradictory doc claim(s). |"
                % (
                    self._markdown_cell(policy_drift.get("status")),
                    self._markdown_cell(policy_drift.get("missing_doc_count", 0)),
                    self._markdown_cell(policy_drift.get("doc_drift_count", 0)),
                ),
                "| Guardrail debt | %s | %s active debt item(s) across %s enforcement tier(s). |"
                % (
                    self._markdown_cell(guardrail_debt.get("status")),
                    self._markdown_cell(guardrail_debt.get("active_debt_count", 0)),
                    self._markdown_cell(len(guardrail_debt.get("by_enforcement_tier") or {})),
                ),
            ]
        )
        guardrail_items = guardrail_debt.get("items") or []
        if guardrail_items:
            lines.extend(
                [
                    "",
                    "## Guardrail Debt",
                    "",
                    "| Guard | Severity | Tier | Status | Session | Remediation |",
                    "|---|---|---|---|---|---|",
                ]
            )
            for item in guardrail_items[:8]:
                lines.append(
                    "| %s | %s | %s | %s | %s | %s |"
                    % (
                        self._markdown_cell(item.get("guard_id") or item.get("debt_id")),
                        self._markdown_cell(item.get("severity")),
                        self._markdown_cell(item.get("enforcement_tier")),
                        self._markdown_cell(item.get("debt_status")),
                        self._markdown_cell(item.get("session_id") or item.get("owner_agent")),
                        self._markdown_cell(item.get("remediation")),
                    )
                )
        lines.extend(
            [
                "",
                "## Counts",
                "",
                "| Area | Count |",
                "|---|---:|",
                "| Pairings | %d |" % len(overview.get("pairings") or []),
                "| Cross-project contracts | %d |" % len(overview.get("contracts") or []),
                "| Pending actions | %d |" % len(overview.get("pending_actions") or []),
                "| Remote authority rejections | %d |" % len(overview.get("remote_authority_rejections") or []),
                "",
                "## Pairings",
                "",
                "| Project | Role | Agent | Session | Title | Peer | Status |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for row in overview.get("pairings") or []:
            status = row.get("status")
            if row.get("last_wake_delivery_priority_action"):
                status = "%s (delivery-priority wake)" % (status or "unknown")
            lines.append(
                "| %s | %s | %s | %s | %s | %s %s | %s |"
                % (
                    self._markdown_cell(row.get("project")),
                    self._markdown_cell(row.get("role")),
                    self._markdown_cell(row.get("agent")),
                    self._markdown_cell(row.get("session_display") or row.get("short_session_id")),
                    self._markdown_cell(row.get("desktop_thread_title") or ""),
                    self._markdown_cell(row.get("peer_agent")),
                    self._markdown_cell(row.get("peer_session_display") or row.get("peer_short_session_id")),
                    self._markdown_cell(status),
                )
            )
        if not overview.get("pairings"):
            lines.append("| none |  |  |  |  |  |  |")
        return "\n".join(lines)

    def dashboard_overview(
        self,
        agent: str,
        project: Optional[str] = None,
        format: str = "json",
    ) -> BridgeResult:
        try:
            caller = normalize_agent(agent)
            project_name = normalize_project(project) if project else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        output_format = (format or "json").strip().lower()
        if output_format not in {"json", "markdown"}:
            return BridgeResult(False, "rejected", "format must be json or markdown")

        health = self.bridge_health_panel(caller, include_extended=True, format="json")
        health_snapshot = health.data.get("snapshot") if health.ok else {"status": health.status, "message": health.message}
        recommended_actions = (
            health_snapshot.get("recommended_actions", [])
            if isinstance(health_snapshot, dict)
            else []
        )
        policy_validation = self.validate_policy_dashboard(caller, project=project_name)
        policy_drift = self._dashboard_policy_drift_status(policy_validation)
        with self._locked():
            registry_read = self._dashboard_session_registry()
            registry = registry_read["registry"]
            caller_identity = self._identity(caller, None)
            pending_read = self._dashboard_pending_actions()
            pending_actions = pending_read["actions"]
            audit_read = self._health_read_jsonl(self.audit_path, "remote_authority_rejections")
            remote_rejections = [
                copy.deepcopy(row)
                for row in audit_read["rows"]
                if (row.get("tenant_id") or caller_identity.tenant_id) == caller_identity.tenant_id
                and row.get("action") == "remote_authority_request_rejected"
            ][-100:]
            contracts = self._dashboard_cross_project_contracts(project_name)
            journal_read = self._dashboard_implementation_journal()
            read_status = self._dashboard_read_status(
                registry_read=registry_read,
                pending_read=pending_read,
                journal_read=journal_read,
                audit_read=audit_read,
            )
            backpressure_status = self._dashboard_backpressure_status(health_snapshot, registry, project_name)
            catchup_status = self._dashboard_catchup_status(
                journal_read["journal"],
                registry=registry,
                project_name=project_name,
                registry_status=registry_read["status"],
                registry_error=registry_read.get("error"),
                journal_status=journal_read["status"],
                journal_error=journal_read.get("error"),
            )
            contract_status = self._dashboard_contract_status(contracts)
            guardrail_debt_status = self._dashboard_guardrail_debt_status(registry, project_name)
            overview = {
                "schema_version": 1,
                "generated_at": utc_now(),
                "caller": {"agent": caller, "project": project_name},
                "bridge_root": str(self.state_dir.parent),
                "session_registry_status": {
                    "status": registry_read["status"],
                    "path": registry_read["path"],
                    "error": registry_read.get("error"),
                },
                "read_status": read_status,
                "health": health_snapshot,
                "recommended_actions": recommended_actions,
                "pairings": self._dashboard_pairings(registry, project_name),
                "contracts": contracts,
                "pending_actions": pending_actions,
                "status_surfaces": {
                    "dashboard_reads": read_status,
                    "backpressure": backpressure_status,
                    "claude_monitor": (health_snapshot.get("core") or {}).get("claude_monitor", {})
                    if isinstance(health_snapshot, dict)
                    else {},
                    "stale_unread_watchdog": (health_snapshot.get("core") or {}).get("stale_unread_watchdog", {})
                    if isinstance(health_snapshot, dict)
                    else {},
                    "catchup": catchup_status,
                    "contracts": contract_status,
                    "policy_drift": policy_drift,
                    "guardrail_debt": guardrail_debt_status,
                },
                "policy": {
                    "source": "runtime",
                    "remote_labels_trusted": False,
                    "mutations_require_local_confirmation": True,
                },
                "remote_authority_rejections": remote_rejections,
            }
        if output_format == "markdown":
            markdown = self._render_dashboard_markdown(overview)
            return BridgeResult(True, "dashboard_overview", markdown, {"overview": overview, "markdown": markdown})
        return BridgeResult(
            True,
            "dashboard_overview",
            "Dashboard overview has %d pairing(s), %d contract(s), and %d pending action(s)."
            % (len(overview["pairings"]), len(overview["contracts"]), len(overview["pending_actions"])),
            {"overview": overview},
        )

    def pairing_details(
        self,
        agent: str,
        project: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> BridgeResult:
        try:
            caller = normalize_agent(agent)
            project_name = normalize_project(project) if project else None
            session = normalize_session(session_id) if session_id else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        if not project_name and not session:
            return BridgeResult(False, "rejected", "pairing_details requires project or session_id")
        with self._locked():
            registry = self._load_session_registry()
            matches = self._matching_pairing_sessions(
                registry,
                agent=caller,
                project=project_name,
                session_id=session,
            )
            if not matches:
                return BridgeResult(
                    False,
                    "not_found",
                    "No %s pairing session matched project=%s session_id=%s."
                    % (caller, project_name, session),
                )
            if len(matches) > 1:
                return BridgeResult(
                    False,
                    "ambiguous",
                    "session_id %s matched %d projects; pass project explicitly."
                    % (session, len(matches)),
                    {"matches": [{"project": item["project"], "session_id": item["session_id"]} for item in matches]},
                )
            match = matches[0]
            payload = self._pairing_details_payload(
                match["project_entry"],
                project=match["project"],
                agent=caller,
                session_id=match["session_id"],
                record=match["record"],
            )
        return BridgeResult(
            True,
            "pairing_details",
            "Pairing details for %s session %s in project %s."
            % (caller, payload["session_id"], payload["project"]),
            {"pairing": payload},
        )

    def start_guided_pairing(
        self,
        agent: str,
        project: Optional[str] = None,
        session_id: Optional[str] = None,
        desired_role: str = "active_primary",
        source: str = "dashboard",
    ) -> BridgeResult:
        decision = (desired_role or "").strip().lower().replace("-", "_")
        if decision not in GUIDED_PAIRING_ROLES:
            return BridgeResult(False, "rejected", "desired_role must be one of %s" % sorted(GUIDED_PAIRING_ROLES))
        details = self.pairing_details(agent=agent, project=project, session_id=session_id)
        if not details.ok:
            return details
        pairing = details.data.get("pairing", {})
        options = {item.get("decision"): item for item in pairing.get("available_actions", [])}
        option = options.get(decision, {})
        confirmation_id = str(uuid.uuid4())
        now = utc_now()
        self._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": now,
                "action": "guided_pairing_started",
                "accepted": True,
                "agent": pairing.get("agent"),
                "session_id": pairing.get("session_id"),
                "project": pairing.get("project"),
                "source": source,
                "requested_decision": decision,
                "confirmation_id": confirmation_id,
                "enabled": option.get("enabled"),
                "confirmation_required": option.get("confirmation_required"),
            }
        )
        return BridgeResult(
            True,
            "guided_pairing_started",
            "Guided pairing started for %s session %s as %s."
            % (pairing.get("agent"), pairing.get("session_id"), decision),
            {
                "confirmation_id": confirmation_id,
                "requested_decision": decision,
                "source": source,
                "option": option,
                "pairing": pairing,
                "confirmation_required": bool(option.get("confirmation_required")),
            },
        )

    def confirm_guided_pairing(
        self,
        agent: str,
        project: Optional[str] = None,
        session_id: Optional[str] = None,
        decision: str = "active_primary",
        source: str = "dashboard",
        confirm: bool = False,
    ) -> BridgeResult:
        try:
            caller = normalize_agent(agent)
            project_name = normalize_project(project) if project else None
            session = normalize_session(session_id) if session_id else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        resolved_decision = (decision or "").strip().lower().replace("-", "_")
        if resolved_decision not in GUIDED_PAIRING_ROLES:
            return BridgeResult(False, "rejected", "decision must be one of %s" % sorted(GUIDED_PAIRING_ROLES))
        details = self.pairing_details(agent=caller, project=project_name, session_id=session)
        if not details.ok:
            return details
        pairing = details.data.get("pairing", {})
        option = {
            item.get("decision"): item
            for item in pairing.get("available_actions", [])
        }.get(resolved_decision, {})
        if not option.get("enabled", False):
            return BridgeResult(
                False,
                "rejected",
                option.get("disabled_reason") or "guided pairing decision is not enabled for this session",
                {"pairing": pairing, "decision": resolved_decision, "option": option},
            )
        if resolved_decision == "active_primary":
            if pairing.get("is_active_primary"):
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": utc_now(),
                        "action": "guided_pairing_confirmed",
                        "accepted": True,
                        "agent": caller,
                        "session_id": pairing.get("session_id"),
                        "project": pairing.get("project"),
                        "source": source,
                        "decision": resolved_decision,
                        "already_active": True,
                    }
                )
                return BridgeResult(
                    True,
                    "already_active",
                    "Session %s is already the active primary %s session."
                    % (pairing.get("session_id"), caller),
                    {"pairing": pairing, "decision": resolved_decision},
                )
            if option.get("confirmation_required") and not confirm:
                return BridgeResult(
                    True,
                    "confirmation_required",
                    "Confirm active_primary to supersede %s session %s."
                    % (caller, option.get("supersedes_session_id")),
                    {"pairing": pairing, "decision": resolved_decision, "option": option},
                )
            activated = self.activate_session(
                caller,
                str(pairing.get("session_id")),
                project=str(pairing.get("project")),
                bootstrap_origin=str(pairing.get("bootstrap_origin") or "unknown"),
                allow_supersede=True,
                pairing_intent="active_primary",
            )
            if not activated.ok:
                return activated
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "guided_pairing_confirmed",
                    "accepted": True,
                    "agent": caller,
                    "session_id": pairing.get("session_id"),
                    "project": pairing.get("project"),
                    "source": source,
                    "decision": resolved_decision,
                    "activation_status": activated.status,
                    "superseded_session_id": option.get("supersedes_session_id"),
                }
            )
            refreshed = self.pairing_details(agent=caller, project=str(pairing.get("project")), session_id=str(pairing.get("session_id")))
            return BridgeResult(
                True,
                "guided_pairing_confirmed",
                "Promoted %s session %s to active primary for project %s."
                % (caller, pairing.get("session_id"), pairing.get("project")),
                {
                    "decision": resolved_decision,
                    "activation": activated.data,
                    "pairing": refreshed.data.get("pairing") if refreshed.ok else pairing,
                },
            )

        with self._locked():
            registry = self._load_session_registry()
            matches = self._matching_pairing_sessions(
                registry,
                agent=caller,
                project=project_name,
                session_id=session,
            )
            if not matches:
                return BridgeResult(False, "not_found", "No pairing session matched the requested target.")
            if len(matches) > 1:
                return BridgeResult(
                    False,
                    "ambiguous",
                    "session_id matched %d projects; pass project explicitly." % len(matches),
                    {"matches": [{"project": item["project"], "session_id": item["session_id"]} for item in matches]},
                )
            match = matches[0]
            project_entry = match["project_entry"]
            active = project_entry.get("active") or {}
            if active.get(caller) == match["session_id"] and match["record"].get("status") == "active":
                return BridgeResult(
                    False,
                    "rejected",
                    "active primary sessions cannot be demoted through guided background actions",
                    {"session_id": match["session_id"], "project": match["project"], "decision": resolved_decision},
                )
            now = utc_now()
            record = match["record"]
            record.update(
                {
                    "status": "background",
                    "pairing_intent": "background",
                    "non_primary": True,
                    "non_primary_role": resolved_decision,
                    "paired_with": None,
                    "guided_pairing_confirmed_at": now,
                    "last_seen_at": now,
                }
            )
            self._clear_pending_pair_fields(record)
            project_entry["updated_at"] = now
            self._save_session_registry(registry)
            payload = self._pairing_details_payload(
                project_entry,
                project=match["project"],
                agent=caller,
                session_id=match["session_id"],
                record=record,
            )
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "guided_pairing_confirmed",
                    "accepted": True,
                    "agent": caller,
                    "session_id": match["session_id"],
                    "project": match["project"],
                    "source": source,
                    "decision": resolved_decision,
                }
            )
        return BridgeResult(
            True,
            "guided_pairing_confirmed",
            "Kept %s session %s as %s for project %s."
            % (caller, payload["session_id"], resolved_decision, payload["project"]),
            {"decision": resolved_decision, "pairing": payload},
        )

    def list_pairings(self, agent: str, project: Optional[str] = None) -> BridgeResult:
        try:
            caller = normalize_agent(agent)
            project_name = normalize_project(project) if project else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        with self._locked():
            pairings = self._dashboard_pairings(self._load_session_registry(), project_name)
        return BridgeResult(
            True,
            "pairings",
            "Found %d pairing(s)." % len(pairings),
            {"agent": caller, "project": project_name, "pairings": pairings, "count": len(pairings)},
        )

    def list_contracts(self, agent: str, project: Optional[str] = None, include_inactive: bool = True) -> BridgeResult:
        try:
            caller = normalize_agent(agent)
            project_name = normalize_project(project) if project else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        with self._locked():
            contracts = self._dashboard_cross_project_contracts(project_name)
        if not include_inactive:
            contracts = [item for item in contracts if item.get("derived_status") == "active"]
        return BridgeResult(
            True,
            "contracts",
            "Found %d contract(s)." % len(contracts),
            {"agent": caller, "project": project_name, "contracts": contracts, "count": len(contracts)},
        )

    def audit_timeline(
        self,
        agent: str,
        action: Optional[str] = None,
        project: Optional[str] = None,
        max_count: int = 100,
    ) -> BridgeResult:
        try:
            caller = normalize_agent(agent)
            project_name = normalize_project(project) if project else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        limit = self._bounded_int("max_count", int(max_count), 1, 500)
        with self._locked():
            identity = self._identity(caller, None)
            rows = self.transport.read_audit(identity, self.audit_path, action=(action or None))
        if project_name:
            def _row_projects(row: Dict[str, Any]) -> set[str]:
                value = row.get("projects")
                if isinstance(value, list):
                    return {str(item) for item in value if item is not None}
                if value is not None:
                    return {str(value)}
                return set()

            rows = [
                row
                for row in rows
                if row.get("project") == project_name
                or project_name in _row_projects(row)
                or row.get("advisor_project") == project_name
                or row.get("executor_project") == project_name
            ]
        rows = rows[-limit:]
        return BridgeResult(
            True,
            "audit_timeline",
            "Found %d audit event(s)." % len(rows),
            {"agent": caller, "project": project_name, "events": rows, "count": len(rows)},
        )

    def list_remote_authority_requests(
        self,
        agent: str,
        project: Optional[str] = None,
        max_count: int = 100,
    ) -> BridgeResult:
        timeline = self.audit_timeline(
            agent=agent,
            action="remote_authority_request_rejected",
            project=project,
            max_count=max_count,
        )
        if not timeline.ok:
            return timeline
        return BridgeResult(
            True,
            "remote_authority_requests",
            "Found %d rejected remote-authority request(s)." % timeline.data["count"],
            {
                "agent": timeline.data["agent"],
                "project": timeline.data["project"],
                "requests": timeline.data["events"],
                "count": timeline.data["count"],
            },
        )

    def list_policy_dashboard(self, agent: str, project: Optional[str] = None) -> BridgeResult:
        try:
            caller = normalize_agent(agent)
            project_name = normalize_project(project) if project else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        policy = {
            "source": "runtime",
            "remote_messages_are_requests": True,
            "remote_labels_trusted": False,
            "mutations_require_local_confirmation": True,
            "protected_docs": sorted(PROTECTED_DOC_TOKENS),
            "remote_request_classes": sorted(REMOTE_REQUEST_CLASSES),
            "project": project_name,
        }
        return BridgeResult(
            True,
            "policy_dashboard",
            "Runtime policy dashboard is available for %s." % caller,
            {"agent": caller, "policy": policy},
        )

    def _policy_protected_doc_roots(self) -> List[Path]:
        root = Path(__file__).resolve().parent
        return [root, root.parents[1]]

    def _policy_doc_drift_findings(self, doc_paths: Dict[str, Path]) -> List[Dict[str, Any]]:
        checks = [
            (
                "remote_labels_trusted",
                False,
                re.compile(r"(?i)\bremote[_\s-]*labels?[_\s-]*trusted\b\s*[:=]\s*true\b"),
            ),
            (
                "mutations_require_local_confirmation",
                True,
                re.compile(r"(?i)\bmutations?[_\s-]*require[_\s-]*local[_\s-]*confirmation\b\s*[:=]\s*false\b"),
            ),
            (
                "remote_messages_are_requests",
                True,
                re.compile(r"(?i)\bremote[_\s-]*messages?[_\s-]*are[_\s-]*requests\b\s*[:=]\s*false\b"),
            ),
        ]
        findings: List[Dict[str, Any]] = []
        for doc_name, path in sorted(doc_paths.items()):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    {
                        "doc": doc_name,
                        "policy_key": "read_error",
                        "expected": "readable protected doc",
                        "observed": "%s" % exc,
                    }
                )
                continue
            for policy_key, expected, pattern in checks:
                if pattern.search(text):
                    findings.append(
                        {
                            "doc": doc_name,
                            "policy_key": policy_key,
                            "expected": expected,
                            "observed": not expected,
                        }
                    )
        return findings

    def validate_policy_dashboard(self, agent: str, project: Optional[str] = None) -> BridgeResult:
        result = self.list_policy_dashboard(agent=agent, project=project)
        if not result.ok:
            return result
        missing_docs: List[str] = []
        doc_paths: Dict[str, Path] = {}
        available = set()
        for search_root in self._policy_protected_doc_roots():
            if search_root.exists():
                for path in search_root.iterdir():
                    available.add(path.name.casefold())
                    doc_paths.setdefault(path.name.casefold(), path)
        for name in result.data["policy"]["protected_docs"]:
            if name.casefold() not in available:
                missing_docs.append(name)
        present_doc_paths = {
            name: doc_paths[name.casefold()]
            for name in result.data["policy"]["protected_docs"]
            if name.casefold() in doc_paths
        }
        doc_drift = self._policy_doc_drift_findings(present_doc_paths)
        status = "valid" if not missing_docs and not doc_drift else "warning"
        return BridgeResult(
            True,
            status,
            "Policy dashboard validation %s." % status,
            {**result.data, "missing_docs": missing_docs, "doc_drift": doc_drift},
        )

    def revoke_contract(
        self,
        link_id: str,
        project: str,
        agent: str,
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
        source: str = "dashboard",
        confirm_revoke: bool = False,
    ) -> BridgeResult:
        try:
            link = self._normalize_cross_project_link_id(link_id)
            project_name = normalize_project(project)
            owner = normalize_agent(agent)
            session = normalize_session(session_id) if session_id is not None else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        action_source = (source or "dashboard").strip().lower()
        if action_source not in {"dashboard", "local_chat"}:
            return BridgeResult(False, "rejected", "source must be dashboard or local_chat")

        with self._locked():
            payload = self._load_cross_project_link(link)
            if not payload:
                return BridgeResult(False, "not_found", "contract %s was not found" % link)
            side = self._cross_project_side_for_project(payload, project_name)
            if side is None:
                return BridgeResult(False, "rejected", "project is not part of contract %s" % link)
            if payload.get("status") == "revoked":
                return BridgeResult(True, "already_revoked", "contract %s is already revoked" % link, {"link": payload})
            summary = {
                "link_id": link,
                "project": project_name,
                "local_role": side,
                "status": payload.get("status"),
                "permission_tier": payload.get("permission_tier"),
                "expires_at": payload.get("expires_at"),
                "reason": (reason or "").strip() or None,
                "source": action_source,
                "consequence": "future cross-project sends and body catch-up for this contract are blocked",
            }
            if not confirm_revoke:
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": utc_now(),
                        "action": "contract_revoke_requested",
                        "link_id": link,
                        "project": project_name,
                        "agent": owner,
                        "session_id": session,
                        "source": action_source,
                        "accepted": True,
                    }
                )
                return BridgeResult(
                    False,
                    "confirmation_required",
                    "Revoking contract %s requires local confirmation." % link,
                    {"required_parameter": "confirm_revoke=true", "confirmation": summary},
                )

        result = self.cross_pair_revoke(
            link_id=link,
            project=project_name,
            agent=owner,
            session_id=session,
            reason=reason,
        )
        if result.ok:
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "contract_revoked",
                    "link_id": link,
                    "project": project_name,
                    "agent": owner,
                    "session_id": session,
                    "source": action_source,
                    "accepted": True,
                }
            )
        return result

    def renew_contract(
        self,
        link_id: str,
        project: str,
        agent: str,
        ttl_minutes: int = CROSS_PROJECT_DEFAULT_TTL_MINUTES,
        session_id: Optional[str] = None,
        source: str = "dashboard",
        confirm_renew: bool = False,
    ) -> BridgeResult:
        try:
            link = self._normalize_cross_project_link_id(link_id)
            project_name = normalize_project(project)
            owner = normalize_agent(agent)
            ttl = self._bounded_cross_project_ttl(ttl_minutes)
            session = normalize_session(session_id) if session_id is not None else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        action_source = (source or "dashboard").strip().lower()
        if action_source not in {"dashboard", "local_chat"}:
            return BridgeResult(False, "rejected", "source must be dashboard or local_chat")
        now = utc_now()
        now_dt = parse_iso_datetime(now) or datetime.now(timezone.utc)
        with self._locked():
            payload = self._load_cross_project_link(link)
            if not payload:
                return BridgeResult(False, "not_found", "contract %s was not found" % link)
            side = self._cross_project_side_for_project(payload, project_name)
            if side is None:
                return BridgeResult(False, "rejected", "project is not part of contract %s" % link)
            if payload.get("status") == "revoked":
                return BridgeResult(False, "rejected", "revoked contract %s cannot be renewed" % link)
            confirmation = {
                "link_id": link,
                "project": project_name,
                "local_role": side,
                "current_expires_at": payload.get("expires_at"),
                "new_expires_at": (now_dt + timedelta(minutes=ttl)).isoformat(timespec="seconds"),
                "ttl_minutes": ttl,
                "source": action_source,
            }
            if not confirm_renew:
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": now,
                        "action": "contract_renew_requested",
                        "link_id": link,
                        "project": project_name,
                        "agent": owner,
                        "session_id": session,
                        "ttl_minutes": ttl,
                        "source": action_source,
                        "accepted": True,
                    }
                )
                return BridgeResult(
                    False,
                    "confirmation_required",
                    "Renewing contract %s requires local confirmation." % link,
                    {"required_parameter": "confirm_renew=true", "confirmation": confirmation},
                )
            payload["status"] = "active"
            payload["updated_at"] = now
            payload["renewed_at"] = now
            payload["renewed_by_project"] = project_name
            payload["renewed_by_agent"] = owner
            payload["renewed_by_session"] = session
            payload["renewal_ttl_minutes"] = ttl
            payload["expires_at"] = confirmation["new_expires_at"]
            self._save_cross_project_link(payload)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": now,
                    "action": "contract_renewed",
                    "link_id": link,
                    "project": project_name,
                    "agent": owner,
                    "session_id": session,
                    "ttl_minutes": ttl,
                    "source": action_source,
                    "accepted": True,
                }
            )
        return BridgeResult(True, "renewed", "Renewed contract %s." % link, {"link": payload})

    def rename_local_alias(
        self,
        link_id: str,
        project: str,
        agent: str,
        alias: str,
        source: str = "dashboard",
    ) -> BridgeResult:
        try:
            link = self._normalize_cross_project_link_id(link_id)
            project_name = normalize_project(project)
            owner = normalize_agent(agent)
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        clean_alias = (alias or "").strip()
        if not clean_alias:
            return BridgeResult(False, "rejected", "alias is required")
        if len(clean_alias) > 80:
            return BridgeResult(False, "rejected", "alias must be 80 characters or fewer")
        action_source = (source or "dashboard").strip().lower()
        if action_source not in {"dashboard", "local_chat"}:
            return BridgeResult(False, "rejected", "source must be dashboard or local_chat")
        with self._locked():
            payload = self._load_cross_project_link(link)
            if not payload:
                return BridgeResult(False, "not_found", "contract %s was not found" % link)
            if self._cross_project_side_for_project(payload, project_name) is None:
                return BridgeResult(False, "rejected", "project is not part of contract %s" % link)
            aliases = payload.setdefault("local_aliases", {})
            aliases[project_name] = clean_alias
            payload["updated_at"] = utc_now()
            self._save_cross_project_link(payload)
            self._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": payload["updated_at"],
                    "action": "local_alias_updated",
                    "link_id": link,
                    "project": project_name,
                    "agent": owner,
                    "source": action_source,
                    "accepted": True,
                }
            )
        return BridgeResult(True, "renamed", "Updated local alias for contract %s." % link, {"link": payload})

    def classify_remote_authority_request(
        self,
        from_agent: str,
        text: str,
        project: Optional[str] = None,
        audit: bool = True,
    ) -> BridgeResult:
        try:
            sender = normalize_agent(from_agent)
            project_name = normalize_project(project) if project else None
        except ValueError as exc:
            return BridgeResult(False, "rejected", str(exc))
        body = (text or "").strip()
        if not body:
            return BridgeResult(False, "rejected", "text is required")
        body_bytes = len(body.encode("utf-8"))
        if body_bytes > MAX_MESSAGE_BYTES:
            reason = "remote authority classifier text exceeds %d bytes" % MAX_MESSAGE_BYTES
            if audit:
                with self._locked():
                    self._audit(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": utc_now(),
                            "action": "remote_authority_request_rejected",
                            "from_agent": sender,
                            "project": project_name,
                            "classification": "message_too_large",
                            "reason": reason,
                            "text_hash": sha256_text(body),
                            "body_bytes": body_bytes,
                            "max_body_bytes": MAX_MESSAGE_BYTES,
                            "accepted": True,
                        }
                    )
            return BridgeResult(
                False,
                "message_too_large",
                reason,
                {
                    "classification": "message_too_large",
                    "reason": reason,
                    "body_bytes": body_bytes,
                    "max_body_bytes": MAX_MESSAGE_BYTES,
                    "project": project_name,
                    "from_agent": sender,
                    "safe_next_step": "reject and request a smaller proposal",
                },
            )
        lowered = normalize_classifier_text(body)

        classification = "informational"
        reason = "remote message is informational"
        requires_confirmation = False
        allowed_without_confirmation = True

        # This is a conservative heuristic classifier, not a semantic proof.
        # Ambiguous authority-tinged requests fail closed to local confirmation.
        forbidden_terms = [
            "the user approved",
            "user approved",
            "user has approved",
            "user said yes",
            "approved by user",
            "use this confirmation token",
            "disable audit",
            "change dashboard auth",
            "modify dashboard auth",
            "change csrf",
            "modify csrf",
            "edit agents.md",
            "remote peers can modify policy",
            "change your wake target",
            "change watcher wake target",
            "change bootstrap provenance",
            "read secrets",
            "send secrets",
            "extend my contract",
        ]
        authority_ambiguity_terms = [
            "approval",
            "approved",
            "confirmation",
            "confirmation token",
            "permission",
            "policy",
            "dashboard auth",
            "csrf",
            "wake target",
            "bootstrap provenance",
            "protected doc",
            "protected-doc",
            "contract",
            "secret",
            "token",
            "raw audit",
            "health internals",
        ]
        protected_doc_requested = any(token in lowered for token in PROTECTED_DOC_TOKENS)
        if any(term in lowered for term in forbidden_terms):
            classification = "forbidden_remote_authority"
            reason = "remote peer attempted to broaden or bypass local authority"
            allowed_without_confirmation = False
        elif any(term in lowered for term in ("disconnect me", "revoke my side", "metadata only", "do not send catch-up bodies")):
            classification = "access_reducing_request"
            reason = "request reduces remote access and may be honored with audit"
        elif any(term in lowered for term in ("renew", "catch-up", "catchup", "write-with-confirmation", "promote me")):
            classification = "contract_action_request"
            reason = "request affects contract or catch-up policy"
            requires_confirmation = True
            allowed_without_confirmation = False
        elif protected_doc_requested or any(
            term in lowered
            for term in ("protected doc", "protected-doc", "dashboard auth", "confirmation token")
        ):
            classification = "local_confirmation_required"
            reason = "request touches protected local authority"
            requires_confirmation = True
            allowed_without_confirmation = False
        elif any(term in lowered for term in ("please consider", "i propose", "here is a patch", "suggest")):
            classification = "proposal"
            reason = "remote peer proposed local work"
        elif any(term in lowered for term in authority_ambiguity_terms):
            classification = "local_confirmation_required"
            reason = "authority-related request is ambiguous and requires local confirmation"
            requires_confirmation = True
            allowed_without_confirmation = False

        result = {
            "classification": classification,
            "reason": reason,
            "classifier_mode": "heuristic_soft_fail_closed",
            "body_bytes": body_bytes,
            "requires_local_confirmation": requires_confirmation,
            "allowed_without_confirmation": allowed_without_confirmation,
            "project": project_name,
            "from_agent": sender,
            "safe_next_step": self._remote_request_safe_next_step(classification),
        }
        if audit and classification == "forbidden_remote_authority":
            with self._locked():
                self._audit(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": utc_now(),
                        "action": "remote_authority_request_rejected",
                        "from_agent": sender,
                        "project": project_name,
                        "classification": classification,
                        "reason": reason,
                        "text_hash": sha256_text(body),
                        "accepted": True,
                    }
                )
        return BridgeResult(
            True,
            classification,
            "Remote request classified as %s." % classification,
            result,
        )

    def _remote_request_safe_next_step(self, classification: str) -> str:
        if classification == "forbidden_remote_authority":
            return "reject and audit; local user may initiate a separate safe action"
        if classification == "local_confirmation_required":
            return "create a local confirmation or protected-doc proposal"
        if classification == "contract_action_request":
            return "evaluate active contract policy before sharing or broadening access"
        if classification == "access_reducing_request":
            return "honor if it only reduces remote access, then audit"
        if classification == "proposal":
            return "review as a proposal under current local priorities"
        return "summarize or acknowledge without privileged action"

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
            if open_sessions:
                # User explicitly asked to resume — label the next wake accordingly.
                self._set_next_override_wake_message("User says check bridge inbox")
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
                identity = self._identity(target, session)
                rows = self.transport.read_inbox(identity, path, unread_only=False)
                kept = [row for row in rows if row.get("session_id") != session]
                cleared += len(rows) - len(kept)
                self.transport.write_inbox_rows(identity, path, kept)
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


def _default_state_dir() -> Path:
    return Path.home() / ".agent-bridge" / "state"


def _print_cli_result(result: BridgeResult, *, output_format: str) -> None:
    if output_format == "json":
        _safe_cli_print(json.dumps(dataclasses.asdict(result), indent=2, sort_keys=True, ensure_ascii=True))
        return
    _safe_cli_print(result.message)


def _safe_cli_print(text: str) -> None:
    """Print bridge CLI output without crashing on legacy Windows code pages."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe = text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
        print(safe)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Local Agent Bridge utility commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check-inbox",
        help=(
            "Check one agent inbox bucket. Reads local bridge state files only; "
            "cannot reach inboxes on remote machines. Use the MCP tool for cross-machine access."
        ),
        description=(
            "Check one agent inbox bucket. Reads local bridge state files only; "
            "cannot reach inboxes on remote machines. Use the MCP tool for cross-machine access."
        ),
    )
    check.add_argument("--state-dir", default=str(_default_state_dir()), help="Bridge state directory.")
    check.add_argument("--agent", required=True, choices=sorted(AGENTS), help="Receiving agent.")
    check.add_argument("--session-id", default=None, help="Optional session/project bucket. Omit to scan all buckets.")
    check.add_argument("--include-parent", "--include-parents", action="store_true", dest="include_parents")
    check.add_argument("--mark-read", action="store_true", help="Mark returned unread rows read.")
    check.add_argument("--no-record-seen", action="store_true", help="Do not stamp seen_at while checking.")
    check.add_argument("--format", choices=("json", "text"), default="text")

    reviewer_start = subparsers.add_parser("reviewer-wait-start", help="Record a pending background reviewer wait.")
    reviewer_start.add_argument("--state-dir", default=str(_default_state_dir()), help="Bridge state directory.")
    reviewer_start.add_argument("--owner-agent", default="codex", choices=sorted(AGENTS))
    reviewer_start.add_argument("--reviewer-id", required=True)
    reviewer_start.add_argument("--request-id")
    reviewer_start.add_argument("--owner-session-id")
    reviewer_start.add_argument("--subject")
    reviewer_start.add_argument("--eta-minutes", type=int)
    reviewer_start.add_argument("--note")
    reviewer_start.add_argument("--format", choices=("json", "text"), default="text")

    reviewer_update = subparsers.add_parser("reviewer-wait-update", help="Update reviewer wait ETA/checkback/verdict/debt state.")
    reviewer_update.add_argument("--state-dir", default=str(_default_state_dir()), help="Bridge state directory.")
    reviewer_update.add_argument("--owner-agent", default="codex", choices=sorted(AGENTS))
    reviewer_update.add_argument("--wait-id", required=True)
    reviewer_update.add_argument("--event-type", required=True)
    reviewer_update.add_argument("--reviewer-id")
    reviewer_update.add_argument("--eta-minutes", type=int)
    reviewer_update.add_argument("--result")
    reviewer_update.add_argument("--note")
    reviewer_update.add_argument("--format", choices=("json", "text"), default="text")

    reviewer_status = subparsers.add_parser("reviewer-wait-status", help="Show reviewer wait debt and scheduled checkbacks.")
    reviewer_status.add_argument("--state-dir", default=str(_default_state_dir()), help="Bridge state directory.")
    reviewer_status.add_argument("--owner-agent", default="codex", choices=sorted(AGENTS))
    reviewer_status.add_argument("--include-all", action="store_true")
    reviewer_status.add_argument("--format", choices=("json", "text"), default="text")

    args = parser.parse_args(argv)
    bridge = AgentBridge(Path(args.state_dir))

    if args.command == "check-inbox":
        result = bridge.check_inbox(
            args.agent,
            session_id=args.session_id,
            mark_read=args.mark_read,
            include_parents=args.include_parents,
            record_seen=not args.no_record_seen,
        )
        _print_cli_result(result, output_format=args.format)
        return 0 if result.ok else 2
    if args.command == "reviewer-wait-start":
        result = bridge.record_reviewer_wait(
            args.owner_agent,
            args.reviewer_id,
            request_id=args.request_id,
            owner_session_id=args.owner_session_id,
            subject=args.subject,
            eta_minutes=args.eta_minutes,
            note=args.note,
        )
        _print_cli_result(result, output_format=args.format)
        return 0 if result.ok else 2
    if args.command == "reviewer-wait-update":
        result = bridge.update_reviewer_wait(
            args.wait_id,
            args.event_type,
            owner_agent=args.owner_agent,
            reviewer_id=args.reviewer_id,
            eta_minutes=args.eta_minutes,
            result=args.result,
            note=args.note,
        )
        _print_cli_result(result, output_format=args.format)
        return 0 if result.ok else 2
    if args.command == "reviewer-wait-status":
        result = bridge.reviewer_wait_status(args.owner_agent, include_all=args.include_all)
        _print_cli_result(result, output_format=args.format)
        return 0 if result.ok else 2

    parser.error("unsupported command: %s" % args.command)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
