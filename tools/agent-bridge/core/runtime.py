import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import ROOT_MANIFEST_FILENAME, bridge_root_for_state_dir
from .storage import read_json, write_json


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
        "schema_version": manifest.get("schema_version"),
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
) -> Dict[str, Any]:
    bridge_root = bridge_root_for_state_dir(Path(state_dir))
    breadcrumb: Dict[str, Any] = {
        "schema_version": 1,
        "agent": agent,
        "session_id": session_id,
        "project": project,
        "desktop_app": "codex-desktop" if agent == "codex" else "claude-desktop",
        "written_by_pid": os.getpid(),
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bootstrap_command": bootstrap_command or sys.argv,
    }
    if desktop_thread_id:
        breadcrumb["desktop_thread_id"] = desktop_thread_id
    if agent == "codex":
        breadcrumb["deeplink_template"] = "codex://threads/{thread_id}"
    breadcrumb.update(_manifest_identity(bridge_root))
    return breadcrumb
