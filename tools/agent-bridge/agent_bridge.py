import argparse
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

from project_identity import derive_project_identity
from routing_policy import evaluate_message

AGENTS = {"claude", "codex"}
DEFAULT_SESSION_ID = "default"
DEFAULT_PROJECT = "default"
DEFAULT_MAX_HOPS = 8
MAX_MESSAGE_BYTES = 64 * 1024
SESSION_RETENTION_DAYS = 30
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
    def session_registry_path(self) -> Path:
        return self.state_dir.parent / "session.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / ".lock"

    def inbox_path(self, agent: str) -> Path:
        return self.state_dir / ("inbox-%s.jsonl" % agent)

    def ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

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
            "projects": {},
            "updated_at": utc_now(),
        }

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
        registry.setdefault("projects", {})
        return registry

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
                "updated_at": utc_now(),
            },
        )
        project_entry.setdefault("active", {})
        project_entry.setdefault("sessions", {})
        return project_entry

    def _session_record(self, project_entry: Dict[str, Any], session_id: str, agent: str) -> Dict[str, Any]:
        sessions = project_entry.setdefault("sessions", {})
        record = sessions.setdefault(
            session_id,
            {
                "agent": agent,
                "created_at": utc_now(),
                "activated_at": utc_now(),
                "status": "active",
            },
        )
        record.setdefault("agent", agent)
        record.setdefault("created_at", utc_now())
        record.setdefault("activated_at", utc_now())
        return record

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
    ) -> Optional[str]:
        path = self.inbox_path(target_agent)
        rows = read_jsonl(path)
        unread = [
            row for row in rows if row.get("session_id") == session_id and not row.get("read_at")
        ]
        if unread and not replace_existing_control:
            return None
        if unread and replace_existing_control:
            kept = []
            for row in rows:
                if (
                    row.get("session_id") == session_id
                    and not row.get("read_at")
                    and row.get("marker_variant") == "control"
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
                "from": sender,
                "to": target_agent,
                "body": body,
                "delivered_message": delivered,
                "hash": sha256_text("%s\n%s\n%s\n%s\n%s" % (sender, target_agent, session_id, control_type, body)),
                "marker_variant": "control",
                "control_type": control_type,
                "hop_count": 0,
                "read_at": None,
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

    def _unread_for(self, agent: str, session_id: str) -> List[Dict[str, Any]]:
        return [
            row
            for row in read_jsonl(self.inbox_path(agent))
            if row.get("session_id") == session_id
            and not row.get("read_at")
            and not row.get("superseded_at")
        ]

    def _retire_superseded_inbox(self, agent: str, superseded_session: str) -> int:
        """Mark all unread inbox entries for a superseded session as retired.

        Sets superseded_at on each matching row so backpressure checks ignore
        them, while preserving them for audit history.  Returns the count of
        rows retired.
        """
        inbox = self.inbox_path(agent)
        rows = read_jsonl(inbox)
        now = utc_now()
        count = 0
        for row in rows:
            if row.get("session_id") == superseded_session and not row.get("read_at") and not row.get("superseded_at"):
                row["superseded_at"] = now
                count += 1
        if count:
            write_jsonl(inbox, rows)
        return count

    def activate_session(
        self,
        agent: str,
        session_id: str,
        project: Optional[str] = None,
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

            project_entry["updated_at"] = utc_now()

            if previous_local and previous_local != session:
                old_record = self._session_record(project_entry, previous_local, owner)
                old_record["status"] = "superseded"
                old_record["superseded_by"] = session
                old_record["superseded_at"] = utc_now()
                old_record["last_seen_at"] = utc_now()
                # Retire stale unread inbox entries for the superseded session so
                # they no longer count toward backpressure.  The rows are kept
                # for audit history (superseded_at is set, not deleted).
                self._retire_superseded_inbox(owner, previous_local)
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
            record["status"] = "active"
            record["project"] = project_name
            record["activated_at"] = utc_now()
            record["last_seen_at"] = utc_now()
            if previous_peer:
                record["paired_with"] = previous_peer

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
                    % (owner, project_name, session),
                )

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
                }
            )
            return BridgeResult(
                True,
                "active",
                "Activated %s session %s for project %s." % (owner, session, project_name),
                {
                    "agent": owner,
                    "session_id": session,
                    "project": project_name,
                    "previous_local_session": previous_local,
                    "active_peer_session": previous_peer,
                    "registry_path": str(self.session_registry_path),
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
                    "active": dict(project_entry.get("active", {})),
                    "sessions": dict(project_entry.get("sessions", {})),
                    "registry_path": str(self.session_registry_path),
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
            if drained_messages:
                rows = read_jsonl(self.inbox_path(owner))
                now = utc_now()
                drained_ids = {row["id"] for row in drained_messages}
                for row in rows:
                    if row.get("id") in drained_ids:
                        row["read_at"] = now
                write_jsonl(self.inbox_path(owner), rows)

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
        return self.state_dir.parent / "routing-rules.json"

    def evaluate_routing(self, source: str, direction: str, text: str) -> BridgeResult:
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

    def send_to_peer(self, from_agent: str, to_agent: str, message: str, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            now = utc_now()
            event = {
                "id": str(uuid.uuid4()),
                "timestamp": now,
                "action": "send_to_peer",
                "from": (from_agent or "").strip().lower(),
                "to": (to_agent or "").strip().lower(),
                "session_id": session_id or DEFAULT_SESSION_ID,
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
                session = normalize_session(session_id)
            except ValueError as exc:
                return reject(str(exc))

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
            sender_record = self._find_session_record(registry, session, agent=sender)
            if sender_record:
                record = sender_record["record"]
                if record.get("status") != "active":
                    return reject(
                        "session %s for %s is %s and may not send bridge traffic"
                        % (session, sender, record.get("status"))
                    )
                if sender_record.get("active_session") != session:
                    return reject(
                        "session %s for %s is no longer the active session for project %s"
                        % (session, sender, sender_record["project"])
                    )

            session_state = self._session_state(state, session)
            hop_count = int(session_state.get("hop_count", 0))
            event["hop_count_before"] = hop_count
            if hop_count >= self.max_hops:
                return reject("max hop count reached for session %s" % session)

            if body_hash in session_state.get("seen_hashes", []):
                return reject("duplicate message hash for session %s" % session)

            unread = self._unread_for(target, session)
            if unread:
                return reject("target %s already has one unread message for session %s" % (target, session))

            delivered = "From %s:\n%s" % (sender.capitalize(), body)
            inbox_row = {
                "id": event["id"],
                "created_at": now,
                "session_id": session,
                "from": sender,
                "to": target,
                "body": body,
                "delivered_message": delivered,
                "hash": body_hash,
                "marker_variant": marker["marker_variant"],
                "hop_count": hop_count + 1,
                "read_at": None,
            }
            append_jsonl(self.inbox_path(target), inbox_row)

            session_state["hop_count"] = hop_count + 1
            seen = list(session_state.get("seen_hashes", []))
            seen.append(body_hash)
            session_state["seen_hashes"] = seen[-50:]
            session_state["last_message_at"] = now
            self._save_state(state)

            event["accepted"] = True
            event["reason"] = "delivered"
            event["hop_count_after"] = hop_count + 1
            self._audit(event)

            note = "Queued message for %s in session %s." % (target, session)
            if marker["marker_variant"] == "human-review":
                note += " Human review requested."
            return BridgeResult(True, "queued", note, {"id": event["id"], "hop_count": hop_count + 1})

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
            try:
                sender = normalize_agent(from_agent)
                target = normalize_agent(to_agent)
                session = normalize_session(session_id)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))
            control_name = (control_type or "").strip().upper()
            if not control_name:
                return BridgeResult(False, "rejected", "control_type is required")
            if not summary.strip():
                return BridgeResult(False, "rejected", "summary is required")
            if not body.strip():
                return BridgeResult(False, "rejected", "body is required")
            return self._enqueue_control_message(
                from_agent=sender,
                to_agent=target,
                session_id=session,
                control_type=control_name,
                summary=summary.strip(),
                body=body.strip(),
                status=status.strip() or "info",
                replace_existing_control=replace_existing_control,
            )

    def check_inbox(self, agent: str, session_id: Optional[str] = None, mark_read: bool = True) -> BridgeResult:
        with self._locked():
            try:
                target = normalize_agent(agent)
                session = normalize_session(session_id)
            except ValueError as exc:
                return BridgeResult(False, "rejected", str(exc))

            path = self.inbox_path(target)
            rows = read_jsonl(path)
            unread = [
                row
                for row in rows
                if row.get("session_id") == session and not row.get("read_at")
            ]
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
                        "accepted": True,
                        "reason": "marked_read",
                        "message_count": len(unread),
                    }
                )

            if not unread:
                return BridgeResult(True, "empty", "No unread bridge messages for %s in session %s." % (target, session))

            delivered = "\n\n".join(row["delivered_message"] for row in unread)
            return BridgeResult(
                True,
                "messages",
                delivered,
                {
                    "count": len(unread),
                    "messages": unread,
                },
            )

    def peek_inbox(self, agent: str, session_id: Optional[str] = None) -> BridgeResult:
        return self.check_inbox(agent, session_id=session_id, mark_read=False)

    def mark_read(self, agent: str, message_id: str, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            try:
                target = normalize_agent(agent)
                session = normalize_session(session_id)
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
                if row.get("id") == target_id and row.get("session_id") == session:
                    matched = True
                    if not row.get("read_at"):
                        row["read_at"] = now
                        changed = True
                    break

            if not matched:
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
            rules_path = self.state_dir.parent / "routing-rules.json"
            routing_rules = {
                "path": str(rules_path),
                "status": "missing",
                "learned": 0,
                "suppressed": 0,
            }
            if rules_path.exists():
                try:
                    rules = read_json(rules_path, {})
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

    def reset_session(self, session_id: Optional[str] = None) -> BridgeResult:
        with self._locked():
            session = normalize_session(session_id)
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


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", required=True, help="Directory for bridge runtime state.")
    parser.add_argument("--max-hops", type=int, default=DEFAULT_MAX_HOPS, help="Maximum accepted relays per session.")
