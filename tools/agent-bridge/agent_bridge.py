import argparse
import contextlib
import dataclasses
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


AGENTS = {"claude", "codex"}
DEFAULT_SESSION_ID = "default"
DEFAULT_MAX_HOPS = 8
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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
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
    tmp.replace(path)


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

    def _unread_for(self, agent: str, session_id: str) -> List[Dict[str, Any]]:
        return [
            row
            for row in read_jsonl(self.inbox_path(agent))
            if row.get("session_id") == session_id and not row.get("read_at")
        ]

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

            state = self._load_state()
            if state.get("paused"):
                return reject("bridge is paused")

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
            unread = {
                agent: len(self._unread_for(agent, session))
                for agent in sorted(AGENTS)
            }
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
