import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import read_json, write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def command_line_hash(command: List[str]) -> str:
    return hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest()


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
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def lease_is_fresh(lease: Dict[str, Any], max_age_seconds: int = 120) -> bool:
    stamp = lease.get("heartbeat_at") or lease.get("started_at")
    if not stamp:
        return False
    try:
        dt = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() <= max_age_seconds


def build_lease(
    *,
    role: str,
    command: List[str],
    state_dir: Path,
    pid: Optional[int] = None,
    agent: Optional[str] = None,
    project: Optional[str] = None,
    session_id: Optional[str] = None,
    generation: Optional[str] = None,
) -> Dict[str, Any]:
    now = utc_now()
    return {
        "pid": pid if pid is not None else os.getpid(),
        "parent_pid": os.getppid() if hasattr(os, "getppid") else None,
        "process_name": Path(command[0]).name if command else "unknown",
        "command": command,
        "command_line_hash": command_line_hash(command),
        "state_dir": str(state_dir),
        "agent": agent,
        "project": project,
        "session_id": session_id,
        "role": role,
        "started_at": now,
        "heartbeat_at": now,
        "generation": generation or str(uuid.uuid4()),
    }


def read_lease(lock_path: Path) -> Dict[str, Any]:
    return read_json(lock_path, {})


def write_lease(lock_path: Path, lease: Dict[str, Any]) -> None:
    write_json(lock_path, lease)


def lease_status(lock_path: Path, expected_command: Optional[List[str]] = None) -> Dict[str, Any]:
    if not lock_path.exists():
        return {"status": "missing", "running": False, "stale": False, "pid": None}
    try:
        lease = read_lease(lock_path)
    except Exception:
        return {"status": "corrupt", "running": False, "stale": True, "pid": None}
    pid = int(lease.get("pid") or 0)
    running = is_process_alive(pid)
    stale = not running or not lease_is_fresh(lease)
    hash_matches = True
    if expected_command is not None:
        hash_matches = lease.get("command_line_hash") == command_line_hash(expected_command)
    status = "running" if running and not stale and hash_matches else "stale"
    if running and not hash_matches:
        status = "hash_mismatch"
    return {
        "status": status,
        "running": running,
        "stale": stale,
        "pid": pid or None,
        "hash_matches": hash_matches,
        "lease": lease,
    }


def acquire_singleton_lease(
    lock_path: Path,
    *,
    role: str,
    command: List[str],
    state_dir: Path,
    pid: Optional[int] = None,
    agent: Optional[str] = None,
    project: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    current = lease_status(lock_path, expected_command=command)
    if current["status"] == "running":
        return {"acquired": False, "status": "already_running", "pid": current["pid"], "lease": current["lease"]}
    if current["status"] in {"corrupt", "hash_mismatch"} and lock_path.exists():
        corrupt = lock_path.with_suffix(lock_path.suffix + ".corrupt.%s" % uuid.uuid4().hex[:8])
        try:
            lock_path.replace(corrupt)
        except OSError:
            pass
    lease = build_lease(
        role=role,
        command=command,
        state_dir=state_dir,
        pid=pid,
        agent=agent,
        project=project,
        session_id=session_id,
    )
    write_lease(lock_path, lease)
    return {"acquired": True, "status": "acquired", "pid": lease["pid"], "lease": lease}


def heartbeat_lease(lock_path: Path, pid: int, generation: str) -> bool:
    try:
        lease = read_lease(lock_path)
    except Exception:
        return False
    if int(lease.get("pid") or 0) != pid or lease.get("generation") != generation:
        return False
    lease["heartbeat_at"] = utc_now()
    write_lease(lock_path, lease)
    return True


def release_lease(lock_path: Path, pid: int, generation: str) -> bool:
    try:
        lease = read_lease(lock_path)
    except Exception:
        return False
    if int(lease.get("pid") or 0) != pid or lease.get("generation") != generation:
        return False
    lock_path.unlink(missing_ok=True)
    return True
