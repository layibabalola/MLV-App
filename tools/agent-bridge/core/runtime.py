import os
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import ROOT_MANIFEST_FILENAME, bridge_root_for_state_dir
from .storage import read_json, write_json

PEER_RUNTIME_SCHEMA_VERSION = 2
MONITOR_RUNTIME_SCHEMA_VERSION = 1
MONITOR_RUNTIME_MIN_TTL_S = 30


def _manifest_identity(bridge_root: Path) -> Dict[str, Any]:
    manifest_path = Path(bridge_root) / ROOT_MANIFEST_FILENAME
    if not manifest_path.exists():
        return {"manifest_path": str(manifest_path), "manifest_exists": False}
    try:
        manifest = read_json(manifest_path, {})
    except Exception as exc:
        return {"manifest_path": str(manifest_path), "manifest_exists": True, "manifest_error": str(exc)}
    return {
        "manifest_path": str(manifest_path),
        "manifest_exists": True,
        "root_id": manifest.get("root_id"),
        "active_root": manifest.get("active_root"),
        "manifest_schema_version": manifest.get("schema_version"),
    }


def build_runtime_breadcrumb(
    *,
    state_dir: Path,
    role: str,
    command: Optional[List[str]] = None,
    pid: Optional[int] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    bridge_root = bridge_root_for_state_dir(Path(state_dir))
    breadcrumb: Dict[str, Any] = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "role": role,
        "pid": pid if pid is not None else os.getpid(),
        "parent_pid": os.getppid() if hasattr(os, "getppid") else None,
        "python": sys.executable,
        "command": command or sys.argv,
        "bridge_root": str(bridge_root),
        "state_dir": str(state_dir),
    }
    if config_path is not None:
        breadcrumb["config_path"] = str(config_path)
    breadcrumb.update(_manifest_identity(bridge_root))
    return breadcrumb


def write_runtime_breadcrumb(path: Path, breadcrumb: Dict[str, Any]) -> None:
    write_json(path, breadcrumb)


def read_runtime_breadcrumb(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = read_json(path, {})
    except Exception as exc:
        return {"path": str(path), "error": str(exc), "unreadable": True}
    return data if isinstance(data, dict) else {"path": str(path), "error": "not a JSON object", "unreadable": True}


def _file_hash(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def monitor_runtime_path_for_state_dir(state_dir: Path, agent: str, session_id: str) -> Path:
    safe_session = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(session_id))
    return bridge_root_for_state_dir(Path(state_dir)) / f"monitor-{agent}-{safe_session}.runtime.json"


def build_monitor_runtime_breadcrumb(
    *,
    state_dir: Path,
    agent: str,
    session_id: str,
    project: str,
    script_path: Path,
    argv: Optional[List[str]] = None,
    watched_buckets: Optional[List[str]] = None,
    poll_interval_seconds: Optional[float] = None,
    preexisting_target_unread: Optional[int] = None,
    last_emit_at: Optional[str] = None,
    context_generation: Optional[str] = None,
    compacted_after_start: bool = False,
    started_at: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    script = Path(script_path)
    bridge_root = bridge_root_for_state_dir(Path(state_dir))
    payload: Dict[str, Any] = {
        "schema_version": MONITOR_RUNTIME_SCHEMA_VERSION,
        "agent": agent,
        "session_id": session_id,
        "project": project,
        "monitor_pid": os.getpid(),
        "started_at": started_at or now,
        "heartbeat_at": now,
        "context_generation": context_generation,
        "compacted_after_start": bool(compacted_after_start),
        "script_path": str(script),
        "script_name": script.name,
        "argv": argv or sys.argv,
        "watched_buckets": list(watched_buckets or []),
        "helper_hash": _file_hash(script),
        "poll_interval_seconds": poll_interval_seconds,
        "preexisting_target_unread": preexisting_target_unread,
        "last_emit_at": last_emit_at,
        "bridge_root": str(bridge_root),
        "state_dir": str(state_dir),
    }
    payload.update(_manifest_identity(bridge_root))
    return payload


def peer_runtime_path_for_state_dir(state_dir: Path, agent: str) -> Path:
    return bridge_root_for_state_dir(Path(state_dir)) / f"peer-{agent}.runtime.json"


def build_peer_runtime_breadcrumb(
    *,
    state_dir: Path,
    agent: str,
    session_id: str,
    project: str,
    desktop_thread_id: Optional[str] = None,
    bootstrap_command: Optional[List[str]] = None,
    bootstrap_origin: str = "unknown",
    bootstrap_thread_id: Optional[str] = None,
    bootstrap_parent_thread_id: Optional[str] = None,
    trusted_parent_session_id: Optional[str] = None,
    subagent_signals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    bridge_root = bridge_root_for_state_dir(Path(state_dir))
    breadcrumb: Dict[str, Any] = {
        "schema_version": PEER_RUNTIME_SCHEMA_VERSION,
        "agent": agent,
        "session_id": session_id,
        "project": project,
        "desktop_app": "codex-desktop" if agent == "codex" else "claude-desktop",
        "bootstrap_origin": bootstrap_origin,
        "bootstrap_pid": os.getpid(),
        "bootstrap_parent_pid": os.getppid() if hasattr(os, "getppid") else None,
        "subagent_signals": dict(subagent_signals or {}),
        "written_by_pid": os.getpid(),
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bootstrap_command": bootstrap_command or sys.argv,
    }
    if desktop_thread_id:
        breadcrumb["desktop_thread_id"] = desktop_thread_id
    if bootstrap_thread_id:
        breadcrumb["bootstrap_thread_id"] = bootstrap_thread_id
    if bootstrap_parent_thread_id:
        breadcrumb["bootstrap_parent_thread_id"] = bootstrap_parent_thread_id
    if trusted_parent_session_id:
        breadcrumb["trusted_parent_session_id"] = trusted_parent_session_id
    if agent == "codex":
        breadcrumb["deeplink_template"] = "codex://threads/{thread_id}"
    breadcrumb.update(_manifest_identity(bridge_root))
    return breadcrumb


def normalize_peer_runtime_breadcrumb(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not data or data.get("unreadable"):
        return data
    normalized = dict(data)
    schema_version = int(normalized.get("schema_version") or 1)
    normalized["schema_version"] = schema_version
    normalized.setdefault("bootstrap_origin", "unknown")
    normalized.setdefault(
        "subagent_signals",
        {
            "env_marker": None,
            "process_depth": None,
            "parent_thread_id_mismatch": False,
            "mcp_tag": None,
        },
    )
    return normalized
