import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Sequence, Set

from compact import process_runtime_identity_status, reap_stale_server_pids
from core.processes import is_process_alive
from core.paths import BridgeRootMovedError, ensure_bridge_root_manifest, expand_path_arg, resolve_bridge_paths
from core.runtime import build_runtime_breadcrumb
from core.storage import append_jsonl, atomic_replace, file_lock, read_json, write_json
from powershell_runtime import powershell_cim_command


DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_DEBOUNCE_SECONDS = 1.0
DEFAULT_IDLE_SECONDS = 0.5
DEFAULT_TERMINATE_TIMEOUT_SECONDS = 5.0
DEFAULT_RESTART_WINDOW_SECONDS = 30.0
DEFAULT_MAX_RESTARTS_PER_WINDOW = 4
DEFAULT_CHUNK_SIZE = 65536
DEFAULT_LOOP_SLEEP_SECONDS = 0.05
DEFAULT_HOST_EXIT_CHECK_INTERVAL_SECONDS = 2.0
DEFAULT_GRACEFUL_RESTART_TIMEOUT_SECONDS = 5.0
DEFAULT_STALE_MARKER_REAP_INTERVAL_SECONDS = 10 * 60.0
DEFAULT_STALE_MARKER_MAX_AGE_HOURS = 0
DEFAULT_MAX_LIVE_SERVER_PROCESSES = 0
DEFAULT_MAX_LIVE_SERVER_PROCESSES_PER_HOST = 1
TOOL_MANIFEST_FILENAME = "tool-manifest.json"
TOOL_REFRESH_STATUS_FILENAME = "tool-refresh-status.json"
TOOL_REFRESH_STATUS_SCHEMA_VERSION = 1
SERVER_STATUS_FILENAME = "server-status.json"
CODE_WATCHER_SNAPSHOT_FILENAME = "code-watcher-snapshot.json"
CODE_WATCHER_SNAPSHOT_SCHEMA_VERSION = 1
MCP_SESSION_REPLAY_FILENAME = "mcp-session-replay.json"
MCP_SESSION_REPLAY_SCHEMA_VERSION = 1
SERVER_WRAPPER_FILENAME = "server_wrapper.py"
SERVER_WRAPPER_SELF_RESTART_EXIT_CODE = 77
SERVER_WRAPPER_SELF_RESTART_REASON = "server_wrapper_code_changed_during_wrapper_session"
HOST_SLOT_SCHEMA_VERSION = 1


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_max_live_server_processes() -> int:
    return _int_env("AGENT_BRIDGE_MAX_LIVE_MCP_SERVERS", DEFAULT_MAX_LIVE_SERVER_PROCESSES)


def _default_max_live_server_processes_per_host() -> int:
    return _int_env(
        "AGENT_BRIDGE_MAX_LIVE_MCP_SERVERS_PER_HOST",
        DEFAULT_MAX_LIVE_SERVER_PROCESSES_PER_HOST,
    )


@dataclass(frozen=True)
class SupervisorConfig:
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS
    idle_seconds: float = DEFAULT_IDLE_SECONDS
    terminate_timeout_seconds: float = DEFAULT_TERMINATE_TIMEOUT_SECONDS
    restart_window_seconds: float = DEFAULT_RESTART_WINDOW_SECONDS
    max_restarts_per_window: int = DEFAULT_MAX_RESTARTS_PER_WINDOW
    chunk_size: int = DEFAULT_CHUNK_SIZE
    loop_sleep_seconds: float = DEFAULT_LOOP_SLEEP_SECONDS
    host_exit_check_interval_seconds: float = DEFAULT_HOST_EXIT_CHECK_INTERVAL_SECONDS
    graceful_restart_timeout_seconds: float = DEFAULT_GRACEFUL_RESTART_TIMEOUT_SECONDS
    stale_marker_reap_interval_seconds: float = DEFAULT_STALE_MARKER_REAP_INTERVAL_SECONDS
    stale_marker_max_age_hours: int = DEFAULT_STALE_MARKER_MAX_AGE_HOURS


def build_server_command(*, server_path: Path, state_dir: Path, max_hops: int, passthrough: List[str]) -> List[str]:
    return [
        sys.executable,
        str(server_path),
        "--state-dir",
        str(state_dir),
        "--max-hops",
        str(max_hops),
        *passthrough,
    ]


def audit_wrapper_launch(*, state_dir: Path, command: List[str]) -> None:
    event = build_runtime_breadcrumb(state_dir=state_dir, role="mcp_server_wrapper", command=command)
    event.update({"action": "mcp_server_wrapper_launch", "accepted": True})
    append_jsonl(Path(state_dir) / "messages.jsonl", event)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolver-aware launcher for agent-bridge server.py. Keeps MCP stdin/stdout byte-transparent."
    )
    parser.add_argument("--bridge-root", help="Bridge root directory; preferred for Desktop MCP configs")
    parser.add_argument("--state-dir", help="Legacy bridge state directory")
    parser.add_argument("--watch-code-dir", help=argparse.SUPPRESS)
    parser.add_argument("--max-hops", type=int, default=8, help="Maximum accepted relays per session")
    parser.add_argument(
        "--max-live-server-processes",
        type=int,
        default=_default_max_live_server_processes(),
        help=(
            "Optional total maximum live agent-bridge server.py processes allowed for this state dir before "
            "this wrapper exits without launching another child. Defaults to 0 (disabled)."
        ),
    )
    parser.add_argument(
        "--max-live-server-processes-per-host",
        type=int,
        default=_default_max_live_server_processes_per_host(),
        help=(
            "Maximum live agent-bridge server.py processes allowed from the same MCP host process for this "
            "state dir before this wrapper exits without launching another child. Defaults to 1; use 0 to disable."
        ),
    )
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="Print the resolved server.py command and exit. Do not use in Desktop MCP config.",
    )
    args, passthrough = parser.parse_known_args(argv)
    args.passthrough = passthrough
    return args


_BRIDGE_LAUNCHER_SCRIPT_NAMES = {SERVER_WRAPPER_FILENAME, "server_wrapper_trampoline.py"}
_BRIDGE_LAUNCHER_SCRIPT_PATHS = {
    str(Path(__file__).with_name(script_name)).casefold().replace("/", "\\")
    for script_name in _BRIDGE_LAUNCHER_SCRIPT_NAMES
}


def _process_table_from_system() -> Dict[int, Dict[str, Any]]:
    """Best-effort process table used only to attribute bridge children to a host."""
    if sys.platform == "win32":
        command = powershell_cim_command(
            (
                "Get-CimInstance Win32_Process | "
                "Select-Object ProcessId,ParentProcessId,Name,CommandLine,ExecutablePath,CreationDate | "
                "ConvertTo-Json -Compress"
            )
        )
        kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "timeout": 5,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(command, **kwargs)
            if proc.returncode != 0 or not proc.stdout.strip():
                return {}
            rows = json.loads(proc.stdout)
            if isinstance(rows, dict):
                rows = [rows]
            result: Dict[int, Dict[str, Any]] = {}
            iterable_rows = rows if isinstance(rows, list) else []
            for row in iterable_rows:
                try:
                    pid = int(row.get("ProcessId") or 0)
                except (TypeError, ValueError):
                    continue
                result[pid] = {
                    "pid": pid,
                    "parent_pid": int(row.get("ParentProcessId") or 0),
                    "name": row.get("Name") or "",
                    "command_line": row.get("CommandLine") or "",
                    "executable_path": row.get("ExecutablePath") or "",
                    "creation_date": str(row.get("CreationDate") or ""),
                }
            return result
        except Exception:
            return {}

    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,comm=,args="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    result = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            parent_pid = int(parts[1])
        except ValueError:
            continue
        result[pid] = {
            "pid": pid,
            "parent_pid": parent_pid,
            "name": parts[2],
            "command_line": parts[3] if len(parts) > 3 else parts[2],
            "executable_path": "",
            "creation_date": "",
        }
    return result


def _process_entry_from_system(pid: int) -> Optional[Dict[str, Any]]:
    """Return one process table entry without enumerating every process on Windows."""
    pid = int(pid)
    if pid <= 0:
        return None
    if sys.platform == "win32":
        command = powershell_cim_command(
            (
                "Get-CimInstance Win32_Process -Filter \"ProcessId = %s\" | "
                "Select-Object ProcessId,ParentProcessId,Name,CommandLine,ExecutablePath,CreationDate | "
                "ConvertTo-Json -Compress"
            )
            % pid,
        )
        kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "timeout": 5,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(command, **kwargs)
            if proc.returncode != 0 or not proc.stdout.strip():
                return None
            row = json.loads(proc.stdout)
            if not isinstance(row, dict):
                return None
            return {
                "pid": int(row.get("ProcessId") or 0),
                "parent_pid": int(row.get("ParentProcessId") or 0),
                "name": row.get("Name") or "",
                "command_line": row.get("CommandLine") or "",
                "executable_path": row.get("ExecutablePath") or "",
                "creation_date": str(row.get("CreationDate") or ""),
            }
        except Exception:
            return None
    return _process_table_from_system().get(pid)


def _is_bridge_launcher_process(process: Dict[str, Any]) -> bool:
    command_line = str(process.get("command_line") or "").casefold().replace("/", "\\")
    return any(script_path in command_line for script_path in _BRIDGE_LAUNCHER_SCRIPT_PATHS)


def _short_hash(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.casefold().encode("utf-8", errors="replace")).hexdigest()[:16]


def _host_identity_from_process(
    process: Dict[str, Any],
    *,
    skipped: List[int],
    resolution: str,
) -> Dict[str, Any]:
    pid = int(process.get("pid") or 0)
    creation_date = str(process.get("creation_date") or "")
    executable_path = str(process.get("executable_path") or "")
    command_line = str(process.get("command_line") or "")
    key_parts = ["pid:%s" % pid]
    creation_hash = _short_hash(creation_date)
    executable_hash = _short_hash(executable_path)
    command_hash = _short_hash(command_line)
    if creation_hash:
        key_parts.append("start:%s" % creation_hash)
    if executable_hash:
        key_parts.append("exe:%s" % executable_hash)
    if command_hash and (creation_hash or executable_hash):
        key_parts.append("cmd:%s" % command_hash)
    return {
        "host_pid": pid,
        "host_key": "|".join(key_parts),
        "host_process_name": process.get("name"),
        "host_command_line": command_line or None,
        "host_executable_path": executable_path or None,
        "host_creation_date": creation_date or None,
        "host_command_hash": command_hash,
        "host_resolution": resolution,
        "bridge_launcher_pids": skipped,
    }


def _wrapper_identity_for_pid(
    pid: int,
    *,
    process_table: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    table = process_table if process_table is not None else _process_table_from_system()
    process = table.get(int(pid)) or {}
    command_line = str(process.get("command_line") or "")
    executable_path = str(process.get("executable_path") or "")
    creation_date = str(process.get("creation_date") or "")
    return {
        "wrapper_pid": int(pid),
        "wrapper_process_name": process.get("name"),
        "wrapper_creation_date": creation_date or None,
        "wrapper_executable_path": executable_path or None,
        "wrapper_command_hash": _short_hash(command_line),
    }


def _host_identity_from_env(*, skipped: List[int]) -> Optional[Dict[str, Any]]:
    host_pid = _int_or_none(
        os.environ.get("AGENT_BRIDGE_MCP_HOST_PID")
        or os.environ.get("AGENT_BRIDGE_TRAMPOLINE_PARENT_PID")
        or os.environ.get("AGENT_BRIDGE_HOST_PID")
    )
    if host_pid is None:
        return None
    host_process_name = os.environ.get("AGENT_BRIDGE_MCP_HOST_PROCESS_NAME") or os.environ.get(
        "AGENT_BRIDGE_HOST_PROCESS_NAME"
    )
    host_creation_date = os.environ.get("AGENT_BRIDGE_MCP_HOST_CREATION_DATE") or os.environ.get(
        "AGENT_BRIDGE_HOST_CREATION_DATE"
    )
    host_executable_path = os.environ.get("AGENT_BRIDGE_MCP_HOST_EXECUTABLE_PATH") or os.environ.get(
        "AGENT_BRIDGE_HOST_EXECUTABLE_PATH"
    )
    host_command_hash = os.environ.get("AGENT_BRIDGE_MCP_HOST_COMMAND_HASH") or os.environ.get(
        "AGENT_BRIDGE_HOST_COMMAND_HASH"
    )
    key_parts = ["pid:%s" % host_pid]
    creation_hash = _short_hash(host_creation_date)
    executable_hash = _short_hash(host_executable_path)
    if creation_hash:
        key_parts.append("start:%s" % creation_hash)
    if executable_hash:
        key_parts.append("exe:%s" % executable_hash)
    if host_command_hash and (creation_hash or executable_hash):
        key_parts.append("cmd:%s" % host_command_hash)
    return {
        "host_pid": host_pid,
        "host_key": "|".join(key_parts),
        "host_process_name": host_process_name,
        "host_command_line": None,
        "host_executable_path": host_executable_path or None,
        "host_creation_date": host_creation_date or None,
        "host_command_hash": host_command_hash,
        "host_resolution": "environment",
        "bridge_launcher_pids": skipped,
    }


def _mcp_host_identity_for_pid(
    pid: int,
    *,
    process_table: Optional[Dict[int, Dict[str, Any]]] = None,
    allow_env_fallback: bool = True,
) -> Dict[str, Any]:
    """Resolve the non-bridge process that owns a wrapper process."""
    table = process_table if process_table is not None else _process_table_from_system()
    current = table.get(int(pid))
    cursor = int((current or {}).get("parent_pid") or 0)
    skipped: List[int] = [int(pid)]
    if current is None and allow_env_fallback:
        env_host = _host_identity_from_env(skipped=skipped)
        if env_host is not None:
            return env_host
    for _ in range(12):
        if cursor <= 0:
            break
        process = table.get(cursor)
        if not process:
            if allow_env_fallback:
                env_host = _host_identity_from_env(skipped=skipped)
                if env_host is not None:
                    return env_host
            return {
                "host_pid": cursor,
                "host_key": "pid:%s" % cursor,
                "host_process_name": None,
                "host_command_line": None,
                "host_resolution": "missing_process_table_entry",
                "bridge_launcher_pids": skipped,
            }
        if allow_env_fallback and not str(process.get("command_line") or "").strip():
            env_host = _host_identity_from_env(skipped=skipped)
            if env_host is not None and _int_or_none(env_host.get("host_pid")) == _int_or_none(
                process.get("parent_pid")
            ):
                skipped.append(cursor)
                return env_host
        if _is_bridge_launcher_process(process):
            skipped.append(cursor)
            cursor = int(process.get("parent_pid") or 0)
            continue
        return _host_identity_from_process(process, skipped=skipped, resolution="process_tree")
    if allow_env_fallback:
        env_host = _host_identity_from_env(skipped=skipped)
        if env_host is not None:
            return env_host
    return {
        "host_pid": None,
        "host_key": "unknown:%s" % int(pid),
        "host_process_name": None,
        "host_command_line": None,
        "host_resolution": "unresolved",
        "bridge_launcher_pids": skipped,
    }


def _live_mcp_server_markers(
    state_dir: Path,
    *,
    identity_fn=process_runtime_identity_status,
    process_table: Optional[Dict[int, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return live, identity-verified server.py marker entries for one state dir."""
    server_dir = Path(state_dir) / "server-pids"
    live: List[Dict[str, Any]] = []
    if not server_dir.exists():
        return live
    for marker in sorted(server_dir.glob("server-*.pid")):
        runtime_path = marker.with_suffix(".json")
        try:
            pid = int(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        runtime: Dict[str, Any] = {}
        if runtime_path.exists():
            try:
                runtime = read_json(runtime_path, {})
            except Exception:
                runtime = {}
        identity = identity_fn(pid, runtime, expected_role="mcp_server")
        if not identity.get("running") or identity.get("identity_mismatch"):
            continue
        entry = {
            "pid": pid,
            "parent_pid": (runtime or {}).get("parent_pid"),
            "timestamp": (runtime or {}).get("timestamp"),
            "path": str(marker),
            "runtime_path": str(runtime_path),
            "recorded_host_key": (runtime or {}).get("host_key"),
            "host_key": (runtime or {}).get("host_key"),
            "host_pid": (runtime or {}).get("host_pid"),
            "host_process_name": (runtime or {}).get("host_process_name"),
            "host_creation_date": (runtime or {}).get("host_creation_date"),
            "host_executable_path": (runtime or {}).get("host_executable_path"),
            "host_command_hash": (runtime or {}).get("host_command_hash"),
        }
        if entry["parent_pid"]:
            host = _mcp_host_identity_for_pid(
                int(entry["parent_pid"]),
                process_table=process_table,
                allow_env_fallback=False,
            )
            if host.get("host_resolution") == "process_tree":
                entry.update({k: v for k, v in host.items() if k.startswith("host_")})
        live.append(entry)
    return live


def _host_slot_path(state_dir: Path, host_key: str) -> Path:
    digest = hashlib.sha256(host_key.encode("utf-8")).hexdigest()[:32]
    return Path(state_dir) / "server-pids" / "host-slots" / ("host-%s.json" % digest)


def _read_json_unlocked(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_unlocked(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%s.%s.tmp" % (path.name, os.getpid(), uuid.uuid4().hex))
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        atomic_replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _int_or_none(value: object) -> Optional[int]:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _host_pid_from_host_key(host_key: str) -> Optional[int]:
    prefix = str(host_key or "").split("|", 1)[0]
    if not prefix.startswith("pid:"):
        return None
    return _int_or_none(prefix[4:])


def _host_slot_key_for_identity(identity: Dict[str, Any]) -> str:
    host_pid = _int_or_none(identity.get("host_pid"))
    if host_pid is None:
        host_pid = _host_pid_from_host_key(str(identity.get("host_key") or ""))
    if host_pid is not None:
        return "host-pid:%s" % host_pid
    host_key = str(identity.get("host_key") or "")
    return "host-key:%s" % host_key if host_key else ""


def _host_slot_lease_is_live(
    lease: Dict[str, Any],
    *,
    process_table: Optional[Dict[int, Dict[str, Any]]] = None,
) -> bool:
    wrapper_pid = _int_or_none(lease.get("wrapper_pid"))
    if wrapper_pid is None or not is_process_alive(wrapper_pid):
        return False
    expected_wrapper_creation_date = str(lease.get("wrapper_creation_date") or "")
    if expected_wrapper_creation_date and process_table:
        wrapper_process = process_table.get(wrapper_pid)
        if wrapper_process and str(wrapper_process.get("creation_date") or "") != expected_wrapper_creation_date:
            return False
    if process_table:
        wrapper_process = process_table.get(wrapper_pid)
        if wrapper_process:
            expected_wrapper_executable_path = str(lease.get("wrapper_executable_path") or "")
            current_wrapper_executable_path = str(wrapper_process.get("executable_path") or "")
            if (
                expected_wrapper_executable_path
                and current_wrapper_executable_path
                and current_wrapper_executable_path.casefold() != expected_wrapper_executable_path.casefold()
            ):
                return False
            expected_wrapper_command_hash = str(lease.get("wrapper_command_hash") or "")
            current_wrapper_command_hash = _short_hash(wrapper_process.get("command_line"))
            if (
                expected_wrapper_command_hash
                and current_wrapper_command_hash
                and current_wrapper_command_hash != expected_wrapper_command_hash
            ):
                return False
    host_pid = _int_or_none(lease.get("host_pid"))
    if host_pid is not None and not is_process_alive(host_pid):
        return False
    expected_creation_date = str(lease.get("host_creation_date") or "")
    if host_pid is not None and expected_creation_date and process_table:
        current = process_table.get(host_pid)
        if current and str(current.get("creation_date") or "") != expected_creation_date:
            return False
    if host_pid is not None and process_table:
        current = process_table.get(host_pid)
        if current:
            expected_executable_path = str(lease.get("host_executable_path") or "")
            current_executable_path = str(current.get("executable_path") or "")
            if (
                expected_executable_path
                and current_executable_path
                and current_executable_path.casefold() != expected_executable_path.casefold()
            ):
                return False
            expected_command_hash = str(lease.get("host_command_hash") or "")
            current_command_hash = _short_hash(current.get("command_line"))
            if expected_command_hash and current_command_hash and current_command_hash != expected_command_hash:
                return False
    return True


def _audit_wrapper_guard_event(*, state_dir: Path, command: Sequence[str], action: str, **extra: object) -> None:
    try:
        event = build_runtime_breadcrumb(state_dir=Path(state_dir), role="mcp_server_wrapper", command=list(command))
        event.update({"action": action, **extra})
        append_jsonl(Path(state_dir) / "messages.jsonl", event)
    except Exception:
        pass


def acquire_mcp_host_slot(
    *,
    state_dir: Path,
    host_identity: Dict[str, Any],
    audit_command: Sequence[str],
    current_pid: Optional[int] = None,
    process_table: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Acquire the pre-launch slot for one MCP host before server.py markers exist."""
    host_key = str(host_identity.get("host_key") or "")
    host_slot_key = _host_slot_key_for_identity(host_identity)
    if not host_slot_key:
        return {"accepted": True, "host_slot_disabled": True}

    wrapper_pid = int(current_pid if current_pid is not None else os.getpid())
    wrapper_identity = _wrapper_identity_for_pid(wrapper_pid, process_table=process_table)
    generation = uuid.uuid4().hex
    slot_path = _host_slot_path(Path(state_dir), host_slot_key)
    host_pid = _int_or_none(host_identity.get("host_pid"))
    host_process_name = host_identity.get("host_process_name")
    host_creation_date = host_identity.get("host_creation_date")
    host_executable_path = host_identity.get("host_executable_path")
    host_command_hash = host_identity.get("host_command_hash")
    stale_slot_replaced = False
    stale_lease: Dict[str, Any] = {}

    with file_lock(slot_path):
        existing = _read_json_unlocked(slot_path)
        if existing and _host_slot_lease_is_live(existing, process_table=process_table):
            result = {
                "accepted": False,
                "host_slot_path": str(slot_path),
                "host_slot_key": host_slot_key,
                "host_slot_holder_wrapper_pid": existing.get("wrapper_pid"),
                "host_slot_holder_host_pid": existing.get("host_pid"),
                "host_slot_generation": existing.get("generation"),
                "host_key": host_key,
                "host_pid": host_pid,
                "host_process_name": host_process_name,
                "host_creation_date": host_creation_date,
                "host_executable_path": host_executable_path,
                "host_command_hash": host_command_hash,
                "wrapper_creation_date": existing.get("wrapper_creation_date"),
                "wrapper_command_hash": existing.get("wrapper_command_hash"),
            }
            _audit_wrapper_guard_event(
                state_dir=Path(state_dir),
                command=audit_command,
                action="mcp_server_wrapper_launch_rejected_duplicate_host_slot",
                **result,
            )
            return result

        if existing:
            stale_slot_replaced = True
            stale_lease = {
                "wrapper_pid": existing.get("wrapper_pid"),
                "host_pid": existing.get("host_pid"),
                "host_creation_date": existing.get("host_creation_date"),
                "wrapper_creation_date": existing.get("wrapper_creation_date"),
                "wrapper_command_hash": existing.get("wrapper_command_hash"),
                "generation": existing.get("generation"),
            }

        acquired_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lease = {
            "schema_version": HOST_SLOT_SCHEMA_VERSION,
            "role": "mcp_server_wrapper_host_slot",
            "generation": generation,
            "acquired_at": acquired_at,
            "state_dir": str(state_dir),
            "host_slot_key": host_slot_key,
            "host_key": host_key,
            "host_pid": host_pid,
            "host_process_name": host_process_name,
            "host_creation_date": host_creation_date,
            "host_executable_path": host_executable_path,
            "host_command_hash": host_command_hash,
            "wrapper_pid": wrapper_pid,
            "wrapper_process_name": wrapper_identity.get("wrapper_process_name"),
            "wrapper_creation_date": wrapper_identity.get("wrapper_creation_date"),
            "wrapper_executable_path": wrapper_identity.get("wrapper_executable_path"),
            "wrapper_command_hash": wrapper_identity.get("wrapper_command_hash"),
        }
        _write_json_unlocked(slot_path, lease)

    if stale_slot_replaced:
        _audit_wrapper_guard_event(
            state_dir=Path(state_dir),
            command=audit_command,
            action="mcp_server_wrapper_stale_host_slot_replaced",
            accepted=True,
            host_key=host_key,
            host_pid=host_pid,
            host_process_name=host_process_name,
            host_creation_date=host_creation_date,
            host_executable_path=host_executable_path,
            host_command_hash=host_command_hash,
            host_slot_path=str(slot_path),
            host_slot_key=host_slot_key,
            stale_lease=stale_lease,
            replacement_wrapper_pid=wrapper_pid,
            replacement_wrapper_creation_date=wrapper_identity.get("wrapper_creation_date"),
            replacement_wrapper_command_hash=wrapper_identity.get("wrapper_command_hash"),
        )

    return {
        "accepted": True,
        "host_slot_path": str(slot_path),
        "host_slot_key": host_slot_key,
        "host_slot_generation": generation,
        "host_key": host_key,
        "host_pid": host_pid,
        "host_process_name": host_process_name,
        "host_creation_date": host_creation_date,
        "host_executable_path": host_executable_path,
        "host_command_hash": host_command_hash,
        "wrapper_creation_date": wrapper_identity.get("wrapper_creation_date"),
        "wrapper_executable_path": wrapper_identity.get("wrapper_executable_path"),
        "wrapper_command_hash": wrapper_identity.get("wrapper_command_hash"),
        "stale_host_slot_replaced": stale_slot_replaced,
    }


def release_mcp_host_slot(
    *,
    state_dir: Path,
    host_key: Optional[str],
    generation: Optional[str],
    wrapper_pid: Optional[int] = None,
    host_pid: Optional[int] = None,
    host_slot_key: Optional[str] = None,
) -> bool:
    resolved_slot_key = str(host_slot_key or "") or _host_slot_key_for_identity(
        {"host_key": host_key, "host_pid": host_pid}
    )
    if not resolved_slot_key or not generation:
        return False
    slot_path = _host_slot_path(Path(state_dir), resolved_slot_key)
    expected_wrapper_pid = int(wrapper_pid if wrapper_pid is not None else os.getpid())
    with file_lock(slot_path):
        lease = _read_json_unlocked(slot_path)
        if (
            str(lease.get("generation") or "") != str(generation)
            or int(lease.get("wrapper_pid") or 0) != expected_wrapper_pid
        ):
            return False
        try:
            slot_path.unlink(missing_ok=True)
        except OSError:
            return False
    return True


def _host_key_aliases(identity: Dict[str, Any]) -> Set[str]:
    aliases: Set[str] = set()
    for key_name in ("host_key", "recorded_host_key"):
        value = identity.get(key_name)
        if value:
            aliases.add(str(value))
    return aliases


def _host_pid_from_identity(identity: Dict[str, Any]) -> Optional[int]:
    host_pid = _int_or_none(identity.get("host_pid"))
    if host_pid is not None:
        return host_pid
    for key in _host_key_aliases(identity):
        host_pid = _host_pid_from_host_key(key)
        if host_pid is not None:
            return host_pid
    return None


def _legacy_pid_key(identity: Dict[str, Any]) -> Optional[str]:
    host_pid = _host_pid_from_identity(identity)
    if host_pid is None:
        return None
    expected = "pid:%s" % host_pid
    keys = _host_key_aliases(identity)
    return expected if expected in keys else None


def _same_host_identity(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if _host_key_aliases(left) & _host_key_aliases(right):
        return True
    left_pid = _host_pid_from_identity(left)
    right_pid = _host_pid_from_identity(right)
    if left_pid is None or right_pid is None or left_pid != right_pid:
        return False
    if _legacy_pid_key(left) or _legacy_pid_key(right):
        return True
    left_creation = str(left.get("host_creation_date") or "")
    right_creation = str(right.get("host_creation_date") or "")
    return bool(left_creation and right_creation and left_creation == right_creation)


def enforce_live_server_process_limit(
    *,
    state_dir: Path,
    max_live_server_processes: int,
    max_live_server_processes_per_host: int,
    audit_command: Sequence[str],
    current_pid: Optional[int] = None,
    process_table: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Bound accidental Agent Bridge MCP fan-out while preserving normal multi-client use.

    MCP stdio is intentionally per-client, so this is not a global singleton. It
    is a host-scoped duplicate guard: once one live server.py child or pre-launch
    wrapper slot already exists for the same MCP host process and state dir, the
    newest wrapper exits before spawning another child process.
    """
    max_live = int(max_live_server_processes)
    max_per_host = int(max_live_server_processes_per_host)
    if max_live <= 0 and max_per_host <= 0:
        return {
            "accepted": True,
            "disabled": True,
            "live_server_count": 0,
            "max_live_server_processes": max_live,
            "max_live_server_processes_per_host": max_per_host,
        }

    try:
        reap_stale_server_pids(Path(state_dir), max_age_hours=0, dry_run=False)
    except Exception:
        # Marker cleanup is best-effort. The guard below still uses identity
        # checks, so stale markers should not produce false rejections.
        pass

    table = process_table if process_table is not None else _process_table_from_system()
    wrapper_pid = int(current_pid if current_pid is not None else os.getpid())
    current_host = _mcp_host_identity_for_pid(wrapper_pid, process_table=table)
    live = _live_mcp_server_markers(Path(state_dir), process_table=table)
    matching_host = [entry for entry in live if _same_host_identity(entry, current_host)]
    result: Dict[str, Any] = {
        "accepted": True,
        "live_server_count": len(live),
        "max_live_server_processes": max_live,
        "max_live_server_processes_per_host": max_per_host,
        "live_server_pids": [entry["pid"] for entry in live],
        "host_key": current_host.get("host_key"),
        "host_pid": current_host.get("host_pid"),
        "host_process_name": current_host.get("host_process_name"),
        "host_creation_date": current_host.get("host_creation_date"),
        "host_executable_path": current_host.get("host_executable_path"),
        "host_command_hash": current_host.get("host_command_hash"),
        "matching_host_live_server_count": len(matching_host),
        "matching_host_live_server_pids": [entry["pid"] for entry in matching_host],
    }
    reject_action: Optional[str] = None
    if max_per_host > 0 and len(matching_host) >= max_per_host:
        result["accepted"] = False
        reject_action = "mcp_server_wrapper_launch_rejected_duplicate_host"
    elif max_live > 0 and len(live) >= max_live:
        result["accepted"] = False
        reject_action = "mcp_server_wrapper_launch_rejected_live_server_limit"
    if result["accepted"]:
        if max_per_host > 0:
            slot = acquire_mcp_host_slot(
                state_dir=Path(state_dir),
                host_identity=current_host,
                audit_command=audit_command,
                current_pid=wrapper_pid,
                process_table=table,
            )
            result.update(slot)
            if not slot.get("accepted"):
                result["accepted"] = False
        return result

    _audit_wrapper_guard_event(
        state_dir=Path(state_dir),
        command=audit_command,
        action=str(reject_action),
        accepted=False,
        live_server_count=len(live),
        max_live_server_processes=max_live,
        max_live_server_processes_per_host=max_per_host,
        live_server_pids=[entry["pid"] for entry in live],
        host_key=current_host.get("host_key"),
        host_pid=current_host.get("host_pid"),
        host_process_name=current_host.get("host_process_name"),
        host_creation_date=current_host.get("host_creation_date"),
        host_executable_path=current_host.get("host_executable_path"),
        host_command_hash=current_host.get("host_command_hash"),
        matching_host_live_server_count=len(matching_host),
        matching_host_live_server_pids=[entry["pid"] for entry in matching_host],
    )
    return result


def _binary_reader(stream: object) -> BinaryIO:
    return getattr(stream, "buffer", stream)


def _binary_writer(stream: object) -> BinaryIO:
    return getattr(stream, "buffer", stream)


def _read_available(stream: object, size: int) -> bytes:
    fileno = getattr(stream, "fileno", None)
    if callable(fileno):
        try:
            return os.read(fileno(), size)
        except (OSError, ValueError):
            pass
    return stream.read(size)


_RESTART_TRIGGER_FILENAMES: frozenset = frozenset({
    # Tool definitions and the business logic they call into.
    "server.py",
    "agent_bridge.py",
    # The wrapper itself is handled by exiting with SERVER_WRAPPER_SELF_RESTART_EXIT_CODE;
    # server_wrapper_trampoline.py keeps the MCP host's stdio pipe open and relaunches it.
    SERVER_WRAPPER_FILENAME,
    # core/ modules are imported transitively by agent_bridge.py.
    # Any .py file under the core/ subdirectory also qualifies (see _is_restart_trigger_file).
})


def _is_restart_trigger_file(path: Path) -> bool:
    """Return True if changes to this file should trigger an MCP server restart.

    Everything else — dashboard HTML, standalone daemons, test files, utility
    scripts — is ignored by default.  New files only enter the restart set when
    they are explicitly added here or placed under core/.
    """
    return path.name in _RESTART_TRIGGER_FILENAMES or "core" in path.parts


def _watch_bridge_code_files(base_dir: Path) -> List[Path]:
    return sorted(path for path in Path(base_dir).rglob("*.py") if "__pycache__" not in path.parts)


def _snapshot_mtimes(paths: Sequence[Path]) -> Dict[Path, Optional[int]]:
    snapshot: Dict[Path, Optional[int]] = {}
    for path in paths:
        try:
            snapshot[path] = path.stat().st_mtime_ns
        except FileNotFoundError:
            snapshot[path] = None
    return snapshot


def _serialize_snapshot(snapshot: Dict[Path, Optional[int]]) -> Dict[str, Optional[int]]:
    """Convert Path-keyed mtime dict to string-keyed for JSON serialization."""
    return {str(k): v for k, v in snapshot.items()}


def _deserialize_snapshot(data: Dict[str, Any], paths: Sequence[Path]) -> Dict[Path, Optional[int]]:
    """Reconstruct a Path-keyed mtime dict from a JSON-deserialized string-keyed dict.
    Only returns entries for paths currently in the watch list; stale entries are ignored."""
    result: Dict[Path, Optional[int]] = {}
    for path in paths:
        key = str(path)
        if key in data:
            val = data[key]
            result[path] = int(val) if val is not None else None
    return result


def _close_pipe(pipe: Optional[BinaryIO]) -> None:
    if pipe is None:
        return
    try:
        pipe.close()
    except OSError:
        return


def _looks_like_partial_jsonrpc(buffer: bytes) -> bool:
    stripped = buffer.lstrip()
    return bool(stripped) and stripped[:1] in {b"{", b"["}


class ServerSupervisor:
    def __init__(
        self,
        *,
        command: Sequence[str],
        state_dir: Path,
        watch_paths: Sequence[Path],
        config: Optional[SupervisorConfig] = None,
        stdin_stream: Optional[BinaryIO] = None,
        stdout_stream: Optional[BinaryIO] = None,
        stderr_target: Optional[int] = None,
        host_identity: Optional[Dict[str, Any]] = None,
        now_fn=time.monotonic,
    ) -> None:
        self.command = list(command)
        self.state_dir = Path(state_dir)
        self.watch_paths = [Path(path) for path in watch_paths]
        self.config = config or SupervisorConfig()
        self.stdin_stream = stdin_stream or _binary_reader(sys.stdin)
        self.stdout_stream = stdout_stream or _binary_writer(sys.stdout)
        self.stderr_target = stderr_target
        self.host_identity = dict(host_identity or {})
        self.wrapper_identity = _wrapper_identity_for_pid(os.getpid())
        self.now_fn = now_fn

        self._state_lock = threading.Lock()
        self._stdout_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._child: Optional[subprocess.Popen[bytes]] = None
        self._stdout_threads: Dict[int, threading.Thread] = {}
        self._stdin_thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._restart_in_progress = False
        self._self_restart_pending = False
        self._pending_changed_files: Set[Path] = set()
        self._last_change_time: Optional[float] = None
        self._last_io_time = self.now_fn()
        self._restart_history: List[float] = []
        self._stdin_eof = False
        self._stdin_partial_buffer_size = 0
        self._thread_error: Optional[BaseException] = None
        self._tool_manifest_path = self.state_dir / TOOL_MANIFEST_FILENAME
        self._tool_refresh_status_path = self.state_dir / TOOL_REFRESH_STATUS_FILENAME
        self._server_status_path = self.state_dir / SERVER_STATUS_FILENAME
        self._snapshot_path = self.state_dir / CODE_WATCHER_SNAPSHOT_FILENAME
        self._session_replay_path = self.state_dir / MCP_SESSION_REPLAY_FILENAME
        self._last_initialize_message: Optional[dict] = None
        self._last_initialized_message: Optional[dict] = None
        self._last_stale_marker_reap = 0.0
        self._host_exit_reported = False
        self._last_host_identity_check: Optional[float] = None
        # In-flight JSON-RPC request IDs — cleared when the corresponding response
        # is observed on stdout. Restart is deferred until this set drains (or the
        # graceful-restart timeout expires) to avoid cutting off active requests.
        self._pending_request_ids: Set[str] = set()
        self._load_mcp_session_replay()
        self._clear_tool_refresh_status()

    def _save_watcher_snapshot(self, snapshot: Dict[Path, Optional[int]]) -> None:
        """Persist the mtime snapshot so the next wrapper startup can detect inter-session edits.

        Uses core.storage.write_json for cross-process atomicity (directory-based file lock
        + PID/UUID-keyed temp path + Windows-safe atomic rename). Non-fatal on any error.
        """
        payload: Dict[str, Any] = {
            "schema_version": CODE_WATCHER_SNAPSHOT_SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "wrapper_pid": os.getpid(),
            "mtimes": _serialize_snapshot(snapshot),
        }
        try:
            write_json(self._snapshot_path, payload)
        except Exception as exc:
            self._report_error(
                "agent-bridge server wrapper: code-watcher snapshot write failed (non-fatal): %s" % exc
            )
            # Record the failure in the audit log so tests and operators can observe it
            # without having to capture stderr.  _append_audit_event is itself non-fatal.
            self._append_audit_event(
                action="mcp_server_snapshot_write_failed",
                reason=str(exc),
            )

    def _load_and_apply_persisted_snapshot(self) -> Dict[Path, Optional[int]]:
        """Load the persisted mtime snapshot, detect any trigger-file changes that occurred
        while the wrapper was not running, and queue them for an immediate restart.

        Returns the current mtime dict to use as the in-memory polling baseline.

        Save policy (Option B — "snapshot = last successfully handled state"):
        - No snapshot / corrupt / wrong version → save current as new baseline, no restart queued.
        - Snapshot present, no changes → refresh baseline on disk, no restart queued.
        - Snapshot present, changes detected → do NOT save yet; _restart_child saves after success.
          This ensures a failed or aborted restart leaves the snapshot stale so the change is
          re-detected on the next startup.
        """
        current = _snapshot_mtimes(self.watch_paths)
        raw = read_json(self._snapshot_path, {})
        if not raw or raw.get("schema_version") != CODE_WATCHER_SNAPSHOT_SCHEMA_VERSION:
            # First run, corrupt file, or schema upgrade — establish a clean baseline.
            self._save_watcher_snapshot(current)
            return current

        persisted = _deserialize_snapshot(raw.get("mtimes") or {}, self.watch_paths)
        changed_since_last_run = [
            path for path in self.watch_paths
            if _is_restart_trigger_file(path) and current.get(path) != persisted.get(path)
        ]

        if changed_since_last_run:
            now = self.now_fn()
            with self._state_lock:
                self._pending_changed_files.update(changed_since_last_run)
                self._last_change_time = now
            self._append_audit_event(
                action="mcp_server_restart_queued_from_persisted_snapshot",
                changed_files=sorted(str(p) for p in changed_since_last_run),
            )
            # NOTE: we intentionally do NOT save the snapshot here.  See Option B comment
            # above — _restart_child saves after a successful _replace_child only.
            # If two supervisor instances start simultaneously and both detect the same
            # change, both will queue a restart; the rate-limiter absorbs any excess.
            # Last-writer-wins on the snapshot save is safe: they are all writing the
            # same current mtimes, and only the successful restart path writes.
        else:
            # No trigger-file changes since last snapshot: refresh the on-disk baseline so
            # future startups compare against recent state rather than a potentially ancient one.
            self._save_watcher_snapshot(current)

        return current

    def run(self) -> int:
        try:
            child = self._spawn_child()
            self._write_server_status(status="running", child_pid=child.pid)
            self._maybe_reap_stale_server_markers(force=True)
            self._stdin_thread = threading.Thread(target=self._pump_parent_stdin, name="agent-bridge-wrapper-stdin", daemon=True)
            self._stdin_thread.start()
            self._watch_thread = threading.Thread(target=self._watch_for_code_changes, name="agent-bridge-wrapper-watch", daemon=True)
            self._watch_thread.start()

            while True:
                if self._thread_error is not None:
                    self._report_error("agent-bridge server wrapper worker failed: %s" % self._thread_error)
                    return 1

                if self._owning_host_exited():
                    return 0

                if self._restart_is_due():
                    if not self._restart_child():
                        return 1
                    if self._should_exit_for_self_restart():
                        return SERVER_WRAPPER_SELF_RESTART_EXIT_CODE
                    continue

                self._maybe_reap_stale_server_markers()

                child = self._current_child()
                if child is None:
                    return 1

                exit_code = child.poll()
                if exit_code is not None and not self._is_restarting():
                    if self._stdin_eof:
                        self._join_stdout_thread(child.pid)
                        return exit_code
                    if not self._respawn_after_child_exit(child, exit_code):
                        self._join_stdout_thread(child.pid)
                        return exit_code or 1
                    continue

                if self._stop_event.wait(self.config.loop_sleep_seconds):
                    if self._thread_error is not None:
                        self._report_error("agent-bridge server wrapper worker failed: %s" % self._thread_error)
                        return 1
                    return 1
        finally:
            self._stop_event.set()
            self._shutdown_child()
            self._join_all_stdout_threads()

    def _spawn_child(self) -> subprocess.Popen[bytes]:
        child_env = os.environ.copy()
        child_env["AGENT_BRIDGE_WRAPPER_PID"] = str(os.getpid())
        for key in (
            "wrapper_creation_date",
            "wrapper_executable_path",
            "wrapper_command_hash",
        ):
            value = self.wrapper_identity.get(key)
            if value is not None:
                child_env["AGENT_BRIDGE_%s" % key.upper()] = str(value)
        for key in (
            "host_key",
            "host_pid",
            "host_process_name",
            "host_creation_date",
            "host_executable_path",
            "host_command_hash",
        ):
            value = self.host_identity.get(key)
            if value is not None:
                child_env["AGENT_BRIDGE_%s" % key.upper()] = str(value)
        child = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_target,
            env=child_env,
            bufsize=0,
        )
        try:
            self._replay_mcp_initialization(child)
        except BaseException:
            self._terminate_child(child)
            raise
        stdout_thread = threading.Thread(
            target=self._pump_child_stdout,
            args=(child,),
            name="agent-bridge-wrapper-stdout-%s" % child.pid,
            daemon=True,
        )
        with self._state_lock:
            self._child = child
            self._stdout_threads[child.pid] = stdout_thread
            stdin_eof = self._stdin_eof
        if stdin_eof and child.stdin is not None:
            _close_pipe(child.stdin)
        stdout_thread.start()
        return child

    def _owning_host_exited(self) -> bool:
        host_pid = _int_or_none(self.host_identity.get("host_pid"))
        if host_pid is None or host_pid == os.getpid():
            return False
        expected_creation_date = str(self.host_identity.get("host_creation_date") or "")
        reason = "host_pid_not_running"
        if is_process_alive(host_pid):
            if expected_creation_date:
                now = self.now_fn()
                interval = max(0.0, float(self.config.host_exit_check_interval_seconds))
                if (
                    self._last_host_identity_check is not None
                    and now - self._last_host_identity_check < interval
                ):
                    return False
                self._last_host_identity_check = now
                current = _process_entry_from_system(host_pid)
                if current and str(current.get("creation_date") or "") != expected_creation_date:
                    reason = "host_pid_reused"
                else:
                    return False
            else:
                return False
        if reason == "host_pid_not_running" and is_process_alive(host_pid):
            return False
        if not self._host_exit_reported:
            self._host_exit_reported = True
            self._append_audit_event(
                action="mcp_server_wrapper_host_exited",
                host_key=self.host_identity.get("host_key"),
                host_pid=host_pid,
                host_process_name=self.host_identity.get("host_process_name"),
                host_creation_date=self.host_identity.get("host_creation_date"),
                reason=reason,
            )
            self._report_error("agent-bridge server wrapper exiting because MCP host pid %s is gone" % host_pid)
        return True

    def _read_json_file(self, path: Path) -> Optional[dict]:
        try:
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _write_json_file(self, path: Path, payload: dict) -> None:
        write_json(path, payload)

    def _load_mcp_session_replay(self) -> None:
        raw = self._read_json_file(self._session_replay_path)
        if not raw or raw.get("schema_version") != MCP_SESSION_REPLAY_SCHEMA_VERSION:
            return
        initialize = raw.get("initialize")
        initialized = raw.get("initialized")
        with self._state_lock:
            if isinstance(initialize, dict):
                self._last_initialize_message = initialize
            if isinstance(initialized, dict):
                self._last_initialized_message = initialized

    def _persist_mcp_session_replay(self) -> None:
        with self._state_lock:
            initialize = self._last_initialize_message
            initialized = self._last_initialized_message
        if initialize is None:
            return
        payload = {
            "schema_version": MCP_SESSION_REPLAY_SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "wrapper_pid": os.getpid(),
            "initialize": initialize,
            "initialized": initialized,
        }
        try:
            self._write_json_file(self._session_replay_path, payload)
        except Exception as exc:
            self._report_error("agent-bridge server wrapper: MCP session replay write failed (non-fatal): %s" % exc)

    def _remember_mcp_session_message(self, message: dict) -> None:
        method = message.get("method")
        if method not in {"initialize", "notifications/initialized"}:
            return
        with self._state_lock:
            if method == "initialize":
                self._last_initialize_message = message
            else:
                self._last_initialized_message = message
        self._persist_mcp_session_replay()

    def _session_replay_messages(self) -> tuple[Optional[dict], Optional[dict]]:
        with self._state_lock:
            initialize = self._last_initialize_message
            initialized = self._last_initialized_message
        return initialize, initialized

    def _read_child_stdout_line_for_replay(self, child: subprocess.Popen[bytes], *, timeout_seconds: float = 5.0) -> bytes:
        if child.stdout is None:
            raise RuntimeError("child stdout is unavailable for MCP session replay")
        done = threading.Event()
        result: List[object] = []

        def reader() -> None:
            try:
                result.append(child.stdout.readline())
            except BaseException as exc:
                result.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=reader, name="agent-bridge-wrapper-replay-read", daemon=True)
        thread.start()
        if not done.wait(timeout=timeout_seconds):
            raise TimeoutError("timed out waiting for MCP initialize replay response")
        value = result[0] if result else b""
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, bytes) or not value:
            raise RuntimeError("child closed stdout during MCP initialize replay")
        return value

    def _replay_mcp_initialization(self, child: subprocess.Popen[bytes]) -> None:
        initialize, initialized = self._session_replay_messages()
        if initialize is None:
            return
        if child.stdin is None:
            raise RuntimeError("child stdin is unavailable for MCP session replay")
        initialize_id = initialize.get("id")
        child.stdin.write((json.dumps(initialize, separators=(",", ":")) + "\n").encode("utf-8"))
        child.stdin.flush()

        response_line = self._read_child_stdout_line_for_replay(child)
        try:
            response = json.loads(response_line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("invalid MCP initialize replay response: %r" % response_line) from exc
        if response.get("id") != initialize_id or "error" in response:
            raise RuntimeError("unexpected MCP initialize replay response: %r" % response)

        if initialized is not None:
            child.stdin.write((json.dumps(initialized, separators=(",", ":")) + "\n").encode("utf-8"))
            child.stdin.flush()

        self._append_audit_event(
            action="mcp_server_session_replayed",
            child_pid=child.pid,
            initialize_id=initialize_id,
            initialized=initialized is not None,
        )

    def _read_tool_manifest_snapshot(self) -> Optional[dict]:
        manifest = self._read_json_file(self._tool_manifest_path)
        if manifest is None:
            return None
        try:
            stat = self._tool_manifest_path.stat()
        except OSError:
            return None
        return {
            "path": str(self._tool_manifest_path),
            "mtime_ns": stat.st_mtime_ns,
            "signature": manifest.get("signature"),
            "tool_count": manifest.get("tool_count"),
            "tool_names": manifest.get("tool_names") or [],
            "generated_at": manifest.get("generated_at"),
        }

    def _wait_for_tool_manifest_snapshot(self, *, previous_mtime_ns: Optional[int], timeout_seconds: float = 1.0) -> Optional[dict]:
        deadline = time.monotonic() + timeout_seconds
        latest = self._read_tool_manifest_snapshot()
        while time.monotonic() < deadline:
            current = self._read_tool_manifest_snapshot()
            if current is not None:
                latest = current
                if previous_mtime_ns is None or current.get("mtime_ns") != previous_mtime_ns:
                    return current
            time.sleep(self.config.loop_sleep_seconds)
        return latest

    def _clear_tool_refresh_status(self) -> None:
        self._write_json_file(
            self._tool_refresh_status_path,
            {
                "schema_version": TOOL_REFRESH_STATUS_SCHEMA_VERSION,
                "refresh_required": False,
                "reason": None,
                "changed_at": None,
                "wrapper_pid": os.getpid(),
                "previous_signature": None,
                "current_signature": None,
                "changed_files": [],
                "previous_tool_names": [],
                "current_tool_names": [],
            },
        )

    def _mark_tool_refresh_required(
        self,
        *,
        previous_snapshot: dict,
        current_snapshot: dict,
        changed_files: List[str],
        reason: str = "tool_manifest_changed_during_wrapper_session",
    ) -> None:
        payload = {
            "schema_version": TOOL_REFRESH_STATUS_SCHEMA_VERSION,
            "refresh_required": True,
            "reason": reason,
            "changed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "wrapper_pid": os.getpid(),
            "previous_signature": previous_snapshot.get("signature"),
            "current_signature": current_snapshot.get("signature"),
            "changed_files": changed_files,
            "previous_tool_names": previous_snapshot.get("tool_names") or [],
            "current_tool_names": current_snapshot.get("tool_names") or [],
        }
        self._write_json_file(self._tool_refresh_status_path, payload)
        self._append_audit_event(
            action="mcp_tools_refresh_required",
            reason=payload["reason"],
            previous_signature=payload["previous_signature"],
            current_signature=payload["current_signature"],
            changed_files=changed_files,
            previous_tool_names=payload["previous_tool_names"],
            current_tool_names=payload["current_tool_names"],
        )

    def _write_server_status(
        self,
        *,
        status: str,
        child_pid: Optional[int] = None,
        last_restart_at: Optional[str] = None,
        last_restart_elapsed_ms: Optional[int] = None,
        needs_notification: bool = False,
    ) -> None:
        payload = {
            "status": status,
            "child_pid": child_pid,
            "wrapper_pid": os.getpid(),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_restart_at": last_restart_at,
            "last_restart_elapsed_ms": last_restart_elapsed_ms,
            "needs_notification": needs_notification,
        }
        try:
            self._write_json_file(self._server_status_path, payload)
        except Exception as exc:
            self._report_error("agent-bridge server wrapper: server-status write failed (non-fatal): %s" % exc)

    def _pump_parent_stdin(self) -> None:
        parse_buffer = b""
        try:
            while not self._stop_event.is_set():
                if self._should_exit_for_self_restart():
                    return
                chunk = _read_available(self.stdin_stream, self.config.chunk_size)
                if not chunk:
                    with self._state_lock:
                        self._stdin_eof = True
                        child = self._child
                    if child is not None and child.stdin is not None:
                        _close_pipe(child.stdin)
                    with self._state_lock:
                        self._stdin_partial_buffer_size = 0
                    return

                self._note_io()
                # Parse for in-flight request IDs before forwarding — best-effort,
                # never blocks or raises; parse errors are silently discarded.
                parse_buffer = self._parse_stdin_for_requests(chunk, parse_buffer)
                with self._state_lock:
                    self._stdin_partial_buffer_size = len(parse_buffer) if _looks_like_partial_jsonrpc(parse_buffer) else 0
                pending = bytes(chunk)
                while pending and not self._stop_event.is_set():
                    child_stdin = self._current_child_stdin()
                    if child_stdin is None:
                        self._stop_event.wait(self.config.loop_sleep_seconds)
                        continue
                    try:
                        child_stdin.write(pending)
                        child_stdin.flush()
                        pending = b""
                    except (BrokenPipeError, OSError, ValueError):
                        self._stop_event.wait(self.config.loop_sleep_seconds)
        except BaseException as exc:
            self._thread_error = exc
            self._stop_event.set()

    def _pump_child_stdout(self, child: subprocess.Popen[bytes]) -> None:
        parse_buffer = b""
        try:
            if child.stdout is None:
                return
            while not self._stop_event.is_set():
                chunk = _read_available(child.stdout, self.config.chunk_size)
                if not chunk:
                    return
                self._note_io()
                parse_buffer = self._parse_stdout_for_responses(chunk, parse_buffer)
                with self._stdout_lock:
                    self.stdout_stream.write(chunk)
                    self.stdout_stream.flush()
        except BaseException as exc:
            self._thread_error = exc
            self._stop_event.set()
        finally:
            _close_pipe(child.stdout)

    def _parse_stdin_for_requests(self, chunk: bytes, buffer: bytes) -> bytes:
        """Record JSON-RPC request IDs arriving from Claude Desktop."""
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line.strip():
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                    if isinstance(msg, dict) and msg.get("id") is not None and "method" in msg:
                        self._remember_mcp_session_message(msg)
                        with self._state_lock:
                            self._pending_request_ids.add(str(msg["id"]))
                        if msg.get("method") == "tools/call":
                            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                            self._record_mcp_tool_access_proof(
                                request_id=str(msg["id"]),
                                tool_name=str(params.get("name") or "unknown"),
                            )
                    elif isinstance(msg, dict) and msg.get("method") == "notifications/initialized":
                        self._remember_mcp_session_message(msg)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        # Safety: discard unparsed buffer if it grows implausibly large.
        return buffer if len(buffer) < 131072 else b""

    def _parse_stdout_for_responses(self, chunk: bytes, buffer: bytes) -> bytes:
        """Clear JSON-RPC request IDs when the child sends its response."""
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line.strip():
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                    if isinstance(msg, dict) and msg.get("id") is not None and (
                        "result" in msg or "error" in msg
                    ):
                        with self._state_lock:
                            self._pending_request_ids.discard(str(msg["id"]))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        return buffer if len(buffer) < 131072 else b""

    def _watch_for_code_changes(self) -> None:
        try:
            previous = self._load_and_apply_persisted_snapshot()
            while not self._stop_event.wait(self.config.poll_interval_seconds):
                current = _snapshot_mtimes(self.watch_paths)
                changed = [path for path in self.watch_paths if current.get(path) != previous.get(path)]
                previous = current
                if not changed:
                    continue
                restart_changed = [p for p in changed if _is_restart_trigger_file(p)]
                skipped_changed = [p for p in changed if not _is_restart_trigger_file(p)]
                if skipped_changed and not restart_changed:
                    self._append_audit_event(
                        action="mcp_server_restart_skipped_no_restart_files",
                        skipped_files=sorted(str(p) for p in skipped_changed),
                    )
                    continue
                now = self.now_fn()
                with self._state_lock:
                    self._pending_changed_files.update(restart_changed)
                    self._last_change_time = now
        except BaseException as exc:
            self._thread_error = exc
            self._stop_event.set()

    def _restart_is_due(self) -> bool:
        with self._state_lock:
            if self._restart_in_progress or not self._pending_changed_files or self._last_change_time is None:
                return False
            now = self.now_fn()
            if now - self._last_change_time < self.config.debounce_seconds:
                return False
            if now - self._last_io_time < self.config.idle_seconds:
                return False
            # Wait for in-flight JSON-RPC requests to drain before restarting so
            # Claude Desktop doesn't receive a truncated response and show the
            # "Server disconnected" banner. Force-restart after the graceful timeout
            # to avoid stalling indefinitely on a hung request.
            if self._pending_request_ids:
                elapsed_since_change = now - self._last_change_time
                if elapsed_since_change < self.config.graceful_restart_timeout_seconds:
                    return False
            # If stdin already consumed the beginning of a JSON-RPC line, give
            # the rest of that frame a chance to arrive before killing the old
            # child. Otherwise a split request can be stranded across restart.
            if self._stdin_partial_buffer_size:
                elapsed_since_change = now - self._last_change_time
                if elapsed_since_change < self.config.graceful_restart_timeout_seconds:
                    return False
            child = self._child
        return child is not None and child.poll() is None

    def _restart_child(self) -> bool:
        with self._state_lock:
            pending_changed_paths = set(self._pending_changed_files)
            changed_files = sorted(str(path) for path in pending_changed_paths)
            wrapper_self_restart = any(path.name == SERVER_WRAPPER_FILENAME for path in pending_changed_paths)
            # Capture the mtime snapshot HERE, under the lock, before clearing pending.
            # This prevents a subtle silent-drop race: if we instead called
            # _snapshot_mtimes() *after* the lock release (and after _replace_child),
            # the watch thread could detect a new change between the clear and the stat,
            # add it to _pending_changed_files, and the stat would read the newer mtime.
            # If that follow-up restart were then rate-limited, _pending_changed_files
            # would be cleared without a save, leaving the snapshot with the newer mtime —
            # making the change invisible to both in-session detection and the next startup.
            # Capturing here ensures the snapshot records the last state a restart was
            # *initiated against*, not any state reached during the restart itself.
            # Note: stat() calls while holding the lock are microsecond operations on local
            # NTFS; the lock hold is negligible.
            frozen_snapshot = _snapshot_mtimes(self.watch_paths)
            # _pending_changed_files is drained unconditionally, before we know whether the
            # restart will succeed or be rate-limited.  In-session recovery happens through
            # the watch thread re-detecting changes; cross-session recovery through the
            # NOT-saved snapshot (see below).
            self._pending_changed_files.clear()
            self._last_change_time = None
        if wrapper_self_restart:
            success = self._prepare_wrapper_self_restart(changed_files=changed_files)
        else:
            success = self._replace_child(
                changed_files=changed_files,
                reason="bridge_code_changed_during_wrapper_session",
            )
        if success:
            # Persist the snapshot captured at the start of this method (before the clear).
            # Any changes that arrived between the clear and now are already queued in
            # _pending_changed_files by the watch thread and will trigger the next restart;
            # we do NOT want to absorb them into the snapshot prematurely.
            # If the restart failed or was aborted by the rate-limiter, we intentionally do
            # NOT save — the next startup will re-detect the change and retry.
            self._save_watcher_snapshot(frozen_snapshot)
        return success

    def _prepare_wrapper_self_restart(self, *, changed_files: List[str]) -> bool:
        restart_at = self.now_fn()
        if self._restart_limit_reached(
            restart_at,
            changed_files,
            reason=SERVER_WRAPPER_SELF_RESTART_REASON,
        ):
            return False

        restart_started_at = self.now_fn()
        restart_at_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._state_lock:
            cutoff = restart_at - self.config.restart_window_seconds
            self._restart_history = [stamp for stamp in self._restart_history if stamp >= cutoff]
            self._restart_history.append(restart_at)
            self._restart_in_progress = True
            self._self_restart_pending = True
            child = self._child
            self._child = None

        old_child_pid = child.pid if child is not None else None
        self._write_server_status(
            status="wrapper_self_restarting",
            child_pid=old_child_pid,
            last_restart_at=restart_at_iso,
        )
        try:
            if child is not None:
                self._terminate_child(child)
                self._join_stdout_thread(child.pid)
            with self._state_lock:
                self._pending_request_ids.clear()
        except BaseException as exc:
            self._report_error("agent-bridge server wrapper self-restart failed: %s" % exc)
            with self._state_lock:
                self._self_restart_pending = False
            return False
        finally:
            with self._state_lock:
                self._restart_in_progress = False

        self._append_audit_event(
            action="mcp_server_wrapper_self_restart_requested",
            old_child_pid=old_child_pid,
            changed_files=changed_files,
            reason=SERVER_WRAPPER_SELF_RESTART_REASON,
            exit_code=SERVER_WRAPPER_SELF_RESTART_EXIT_CODE,
            elapsed_ms=int(round((self.now_fn() - restart_started_at) * 1000)),
        )
        return True

    def _respawn_after_child_exit(self, child: subprocess.Popen[bytes], exit_code: int) -> bool:
        # Intentionally does NOT call _save_watcher_snapshot after a successful respawn.
        # Rationale: this path handles an unexpected child crash, not a code change.
        # - If there are trigger-file changes already queued in _pending_changed_files
        #   (because a code change was detected just before the crash), those remain in
        #   the set and will be handled by the next _restart_child call, which WILL save
        #   the snapshot.
        # - If no code changes are queued, the current snapshot is still valid (it
        #   reflects the last successful restart state) and does not need refreshing.
        # In both cases the "snapshot = last successfully handled code-change state"
        # invariant (Option B) is preserved without an extra snapshot write here.
        return self._replace_child(
            changed_files=[],
            reason="unexpected_child_exit",
            previous_child=child,
            previous_exit_code=exit_code,
            refresh_required=False,
        )

    def _replace_child(
        self,
        *,
        changed_files: List[str],
        reason: str,
        previous_child: Optional[subprocess.Popen[bytes]] = None,
        previous_exit_code: Optional[int] = None,
        refresh_required: bool = True,
    ) -> bool:
        restart_at = self.now_fn()
        if self._restart_limit_reached(
            restart_at,
            changed_files,
            reason=reason,
            previous_exit_code=previous_exit_code,
        ):
            return False

        previous_snapshot = self._read_tool_manifest_snapshot() or {}
        restart_started_at = self.now_fn()
        restart_at_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._state_lock:
            cutoff = restart_at - self.config.restart_window_seconds
            self._restart_history = [stamp for stamp in self._restart_history if stamp >= cutoff]
            self._restart_history.append(restart_at)
            self._restart_in_progress = True
            child = previous_child or self._child
            if child is self._child:
                self._child = None

        if child is None:
            with self._state_lock:
                self._restart_in_progress = False
            return False

        old_child_pid = child.pid
        self._write_server_status(status="restarting", child_pid=old_child_pid)
        try:
            self._terminate_child(child)
            self._join_stdout_thread(old_child_pid)
            # Discard in-flight request IDs — the old child is gone and those
            # responses will never arrive; a clean slate prevents the new child
            # from being blocked waiting for IDs that can never be cleared.
            with self._state_lock:
                self._pending_request_ids.clear()
            new_child = self._spawn_child()
        except BaseException as exc:
            self._report_error("agent-bridge server wrapper restart failed: %s" % exc)
            return False
        finally:
            with self._state_lock:
                self._restart_in_progress = False

        # Child restarted successfully. Status-file writes below are non-fatal:
        # a failure here must not kill the wrapper or orphan the new child.
        elapsed_ms = int(round((self.now_fn() - restart_started_at) * 1000))
        self._write_server_status(
            status="running",
            child_pid=new_child.pid,
            last_restart_at=restart_at_iso,
            last_restart_elapsed_ms=elapsed_ms,
            needs_notification=refresh_required,
        )
        if refresh_required:
            try:
                current_snapshot = (
                    self._wait_for_tool_manifest_snapshot(previous_mtime_ns=previous_snapshot.get("mtime_ns"))
                    or self._read_tool_manifest_snapshot()
                    or {}
                )
                self._mark_tool_refresh_required(
                    previous_snapshot=previous_snapshot,
                    current_snapshot=current_snapshot,
                    changed_files=changed_files,
                    reason=reason,
                )
                self._append_audit_event(
                    action="mcp_server_refresh_required",
                    child_pid=old_child_pid,
                    changed_files=changed_files,
                    reason=reason,
                )
            except Exception as exc:
                self._report_error("agent-bridge server wrapper: tool-refresh status write failed (non-fatal): %s" % exc)

        try:
            self._append_audit_event(
                action="mcp_server_self_restarted",
                old_child_pid=old_child_pid,
                new_child_pid=new_child.pid,
                changed_files=changed_files,
                reason=reason,
                previous_exit_code=previous_exit_code,
                elapsed_ms=int(round((self.now_fn() - restart_started_at) * 1000)),
            )
        except Exception as exc:
            self._report_error("agent-bridge server wrapper: audit append failed (non-fatal): %s" % exc)

        return True

    def _restart_limit_reached(
        self,
        restart_at: float,
        changed_files: List[str],
        *,
        reason: str,
        previous_exit_code: Optional[int] = None,
    ) -> bool:
        with self._state_lock:
            cutoff = restart_at - self.config.restart_window_seconds
            recent = [stamp for stamp in self._restart_history if stamp >= cutoff]
        if len(recent) < self.config.max_restarts_per_window:
            return False
        self._append_audit_event(
            action="mcp_server_self_restart_aborted_loop",
            changed_files=changed_files,
            attempted_restart_count=len(recent) + 1,
            restart_window_seconds=self.config.restart_window_seconds,
            reason=reason,
            previous_exit_code=previous_exit_code,
        )
        self._report_error("agent-bridge server wrapper aborted restart loop protection (%s)" % reason)
        return True

    def _terminate_child(self, child: subprocess.Popen[bytes]) -> None:
        if child.stdin is not None:
            _close_pipe(child.stdin)
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=self.config.terminate_timeout_seconds)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=self.config.terminate_timeout_seconds)

    def _shutdown_child(self) -> None:
        child = self._current_child()
        if child is None:
            return
        if child.poll() is None:
            self._terminate_child(child)

    def _current_child(self) -> Optional[subprocess.Popen[bytes]]:
        with self._state_lock:
            return self._child

    def _current_child_stdin(self) -> Optional[BinaryIO]:
        with self._state_lock:
            child = self._child
        if child is None or child.poll() is not None or child.stdin is None:
            return None
        return child.stdin

    def _is_restarting(self) -> bool:
        with self._state_lock:
            return self._restart_in_progress

    def _should_exit_for_self_restart(self) -> bool:
        with self._state_lock:
            return self._self_restart_pending

    def _join_stdout_thread(self, child_pid: int) -> None:
        with self._state_lock:
            thread = self._stdout_threads.pop(child_pid, None)
        if thread is not None:
            thread.join(timeout=self.config.terminate_timeout_seconds)

    def _join_all_stdout_threads(self) -> None:
        with self._state_lock:
            threads = list(self._stdout_threads.items())
            self._stdout_threads.clear()
        for _, thread in threads:
            thread.join(timeout=self.config.terminate_timeout_seconds)

    def _note_io(self) -> None:
        with self._state_lock:
            self._last_io_time = self.now_fn()

    def _maybe_reap_stale_server_markers(self, *, force: bool = False) -> None:
        now = self.now_fn()
        interval = max(float(self.config.stale_marker_reap_interval_seconds), 1.0)
        if not force and now - self._last_stale_marker_reap < interval:
            return
        self._last_stale_marker_reap = now
        try:
            result = reap_stale_server_pids(
                self.state_dir,
                max_age_hours=self.config.stale_marker_max_age_hours,
                dry_run=False,
            )
        except Exception as exc:
            self._report_error("agent-bridge server wrapper stale marker cleanup failed (non-fatal): %s" % exc)
            return
        removed_runtime_orphans = result.get("removed_runtime_orphans") or []
        removed = int(result.get("removed") or 0) + len(removed_runtime_orphans)
        if removed:
            self._append_audit_event(
                action="mcp_server_stale_markers_self_healed",
                checked=result.get("checked"),
                checked_runtime_orphans=result.get("checked_runtime_orphans"),
                removed=result.get("removed"),
                removed_runtime_orphans=result.get("removed_runtime_orphans"),
                removed_identity_mismatch=result.get("removed_identity_mismatch"),
                max_age_hours=result.get("max_age_hours"),
            )

    def _record_mcp_tool_access_proof(self, *, request_id: str, tool_name: str) -> None:
        child = self._current_child()
        child_pid = child.pid if child is not None else None
        self._append_audit_event(
            action="mcp_tool_access_proof",
            proof_schema_version=1,
            request_id=request_id,
            tool_name=tool_name,
            wrapper_pid=os.getpid(),
            child_pid=child_pid,
            host_transport="stdio",
            host_scope="wrapper:%s" % os.getpid(),
            accepted=True,
        )

    def _append_audit_event(self, *, action: str, **extra: object) -> None:
        try:
            event = build_runtime_breadcrumb(state_dir=self.state_dir, role="mcp_server_wrapper", command=self.command)
            event["action"] = action
            event.update(extra)
            append_jsonl(self.state_dir / "messages.jsonl", event)
        except Exception as exc:
            self._report_error("agent-bridge server wrapper audit failed: %s" % exc)

    def _report_error(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)


def run_supervisor(
    *,
    command: Sequence[str],
    state_dir: Path,
    watch_paths: Sequence[Path],
    config: Optional[SupervisorConfig] = None,
    stdin_stream: Optional[BinaryIO] = None,
    stdout_stream: Optional[BinaryIO] = None,
    stderr_target: Optional[int] = None,
    host_identity: Optional[Dict[str, Any]] = None,
) -> int:
    return ServerSupervisor(
        command=command,
        state_dir=Path(state_dir),
        watch_paths=watch_paths,
        config=config,
        stdin_stream=stdin_stream,
        stdout_stream=stdout_stream,
        stderr_target=stderr_target,
        host_identity=host_identity,
    ).run()


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    try:
        paths = resolve_bridge_paths(
            bridge_root=expand_path_arg(args.bridge_root) if args.bridge_root else None,
            state_dir=expand_path_arg(args.state_dir) if args.state_dir else None,
        )
        if args.bridge_root:
            ensure_bridge_root_manifest(paths, reason="mcp_server_wrapper")
    except BridgeRootMovedError as exc:
        print(
            "agent-bridge root moved: %s -> %s. Update Desktop MCP config to --bridge-root %s"
            % (exc.root, exc.target, exc.target),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
    except Exception as exc:
        print("agent-bridge server wrapper failed before MCP startup: %s" % exc, file=sys.stderr, flush=True)
        raise SystemExit(2)

    server_path = Path(__file__).with_name("server.py")
    command = build_server_command(
        server_path=server_path,
        state_dir=paths.state_dir,
        max_hops=args.max_hops,
        passthrough=list(args.passthrough),
    )
    if args.print_command:
        print(" ".join(command))
        return

    guard = enforce_live_server_process_limit(
        state_dir=paths.state_dir,
        max_live_server_processes=args.max_live_server_processes,
        max_live_server_processes_per_host=args.max_live_server_processes_per_host,
        audit_command=sys.argv,
    )
    if not guard.get("accepted"):
        if guard.get("host_slot_holder_wrapper_pid"):
            reason = "same MCP host already has wrapper pid %s in its pre-launch slot" % (
                guard.get("host_slot_holder_wrapper_pid"),
            )
        else:
            reason = (
                "same MCP host already has %s live server.py process(es)"
                % guard.get("matching_host_live_server_count")
                if guard.get("matching_host_live_server_count")
                else "%s live server.py process(es) already exist" % guard.get("live_server_count")
            )
        print(
            "agent-bridge server wrapper refused to launch another child: "
            "%s for %s "
            "(per-host limit %s, total limit %s; pass --max-live-server-processes-per-host 0 only for diagnostics)"
            % (
                reason,
                paths.state_dir,
                guard.get("max_live_server_processes_per_host"),
                guard.get("max_live_server_processes"),
            ),
            file=sys.stderr,
            flush=True,
        )
        return

    try:
        audit_wrapper_launch(state_dir=paths.state_dir, command=command)
    except Exception as exc:
        print("agent-bridge server wrapper audit failed: %s" % exc, file=sys.stderr, flush=True)

    watch_code_dir = expand_path_arg(args.watch_code_dir) if args.watch_code_dir else server_path.parent
    try:
        exit_code = run_supervisor(
            command=command,
            state_dir=paths.state_dir,
            watch_paths=_watch_bridge_code_files(Path(watch_code_dir)),
            host_identity={
                "host_key": guard.get("host_key"),
                "host_pid": guard.get("host_pid"),
                "host_process_name": guard.get("host_process_name"),
                "host_creation_date": guard.get("host_creation_date"),
                "host_executable_path": guard.get("host_executable_path"),
                "host_command_hash": guard.get("host_command_hash"),
            },
        )
    finally:
        release_mcp_host_slot(
            state_dir=paths.state_dir,
            host_key=str(guard.get("host_key") or ""),
            generation=str(guard.get("host_slot_generation") or ""),
            wrapper_pid=os.getpid(),
            host_pid=_int_or_none(guard.get("host_pid")),
            host_slot_key=str(guard.get("host_slot_key") or ""),
        )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
