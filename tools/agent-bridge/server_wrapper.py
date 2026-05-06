import argparse
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Sequence, Set

from compact import process_runtime_identity_status, reap_stale_server_pids
from core.paths import BridgeRootMovedError, ensure_bridge_root_manifest, expand_path_arg, resolve_bridge_paths
from core.runtime import build_runtime_breadcrumb
from core.storage import append_jsonl, read_json, write_json


DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_DEBOUNCE_SECONDS = 1.0
DEFAULT_IDLE_SECONDS = 0.5
DEFAULT_TERMINATE_TIMEOUT_SECONDS = 5.0
DEFAULT_RESTART_WINDOW_SECONDS = 30.0
DEFAULT_MAX_RESTARTS_PER_WINDOW = 4
DEFAULT_CHUNK_SIZE = 65536
DEFAULT_LOOP_SLEEP_SECONDS = 0.05
DEFAULT_GRACEFUL_RESTART_TIMEOUT_SECONDS = 5.0
DEFAULT_STALE_MARKER_REAP_INTERVAL_SECONDS = 10 * 60.0
DEFAULT_STALE_MARKER_MAX_AGE_HOURS = 0
DEFAULT_MAX_LIVE_SERVER_PROCESSES = 16
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


def _default_max_live_server_processes() -> int:
    raw = os.environ.get("AGENT_BRIDGE_MAX_LIVE_MCP_SERVERS")
    if raw is None or raw == "":
        return DEFAULT_MAX_LIVE_SERVER_PROCESSES
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_LIVE_SERVER_PROCESSES


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
            "Maximum live agent-bridge server.py processes allowed for this state dir before this wrapper "
            "exits without launching another child. Use 0 to disable."
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


def _live_mcp_server_markers(
    state_dir: Path,
    *,
    identity_fn=process_runtime_identity_status,
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
        live.append(
            {
                "pid": pid,
                "parent_pid": (runtime or {}).get("parent_pid"),
                "timestamp": (runtime or {}).get("timestamp"),
                "path": str(marker),
                "runtime_path": str(runtime_path),
            }
        )
    return live


def enforce_live_server_process_limit(
    *,
    state_dir: Path,
    max_live_server_processes: int,
    audit_command: Sequence[str],
) -> Dict[str, Any]:
    """Bound accidental MCP process fan-out while preserving normal multi-client use.

    MCP stdio is intentionally per-client, so this is not a singleton lock. It is
    a circuit breaker for pathological host relaunch loops: once enough live
    server.py children already exist for the same state dir, the newest wrapper
    exits before spawning another child process.
    """
    max_live = int(max_live_server_processes)
    if max_live <= 0:
        return {"accepted": True, "disabled": True, "live_server_count": 0, "max_live_server_processes": max_live}

    try:
        reap_stale_server_pids(Path(state_dir), max_age_hours=0, dry_run=False)
    except Exception:
        # Marker cleanup is best-effort. The guard below still uses identity
        # checks, so stale markers should not produce false rejections.
        pass

    live = _live_mcp_server_markers(Path(state_dir))
    result: Dict[str, Any] = {
        "accepted": len(live) < max_live,
        "live_server_count": len(live),
        "max_live_server_processes": max_live,
        "live_server_pids": [entry["pid"] for entry in live],
    }
    if result["accepted"]:
        return result

    try:
        event = build_runtime_breadcrumb(state_dir=Path(state_dir), role="mcp_server_wrapper", command=list(audit_command))
        event.update(
            {
                "action": "mcp_server_wrapper_launch_rejected_live_server_limit",
                "accepted": False,
                "live_server_count": len(live),
                "max_live_server_processes": max_live,
                "live_server_pids": [entry["pid"] for entry in live],
            }
        )
        append_jsonl(Path(state_dir) / "messages.jsonl", event)
    except Exception:
        pass
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
        now_fn=time.monotonic,
    ) -> None:
        self.command = list(command)
        self.state_dir = Path(state_dir)
        self.watch_paths = [Path(path) for path in watch_paths]
        self.config = config or SupervisorConfig()
        self.stdin_stream = stdin_stream or _binary_reader(sys.stdin)
        self.stdout_stream = stdout_stream or _binary_writer(sys.stdout)
        self.stderr_target = stderr_target
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
        child = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_target,
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

    def _read_json_file(self, path: Path) -> Optional[dict]:
        try:
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _write_json_file(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # On Windows, replace() fails with WinError 32 if another process briefly holds
        # the destination open. Retry a few times before propagating.
        for attempt in range(4):
            try:
                tmp.replace(path)
                return
            except OSError:
                if attempt < 3:
                    time.sleep(0.05)
                else:
                    raise

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
) -> int:
    return ServerSupervisor(
        command=command,
        state_dir=Path(state_dir),
        watch_paths=watch_paths,
        config=config,
        stdin_stream=stdin_stream,
        stdout_stream=stdout_stream,
        stderr_target=stderr_target,
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
        audit_command=sys.argv,
    )
    if not guard.get("accepted"):
        print(
            "agent-bridge server wrapper refused to launch another child: "
            "%s live server.py process(es) already exist for %s "
            "(limit %s; pass --max-live-server-processes 0 to disable)"
            % (
                guard.get("live_server_count"),
                paths.state_dir,
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
    exit_code = run_supervisor(
        command=command,
        state_dir=paths.state_dir,
        watch_paths=_watch_bridge_code_files(Path(watch_code_dir)),
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
