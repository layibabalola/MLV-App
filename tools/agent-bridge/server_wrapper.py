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
from typing import BinaryIO, Dict, List, Optional, Sequence, Set

from core.paths import BridgeRootMovedError, ensure_bridge_root_manifest, expand_path_arg, resolve_bridge_paths
from core.runtime import build_runtime_breadcrumb
from core.storage import append_jsonl


DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_DEBOUNCE_SECONDS = 1.0
DEFAULT_IDLE_SECONDS = 0.5
DEFAULT_TERMINATE_TIMEOUT_SECONDS = 5.0
DEFAULT_RESTART_WINDOW_SECONDS = 30.0
DEFAULT_MAX_RESTARTS_PER_WINDOW = 4
DEFAULT_CHUNK_SIZE = 65536
DEFAULT_LOOP_SLEEP_SECONDS = 0.05
TOOL_MANIFEST_FILENAME = "tool-manifest.json"
TOOL_REFRESH_STATUS_FILENAME = "tool-refresh-status.json"
TOOL_REFRESH_STATUS_SCHEMA_VERSION = 1


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
    parser.add_argument("--max-hops", type=int, default=8, help="Maximum accepted relays per session")
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="Print the resolved server.py command and exit. Do not use in Desktop MCP config.",
    )
    args, passthrough = parser.parse_known_args(argv)
    args.passthrough = passthrough
    return args


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


_NO_RESTART_FILENAMES: frozenset = frozenset({
    # HTML template imported by server.py — changes never affect tool schema or signatures.
    "dashboard_server.py",
    # Standalone daemons and utilities not imported by server.py.
    "watcher.py",
    "bootstrap_session.py",
    "bridge_monitor_poll.py",
    "probe_server.py",
    "configure_watcher.py",
    "compact.py",
    "consume_inbox.py",
    "dashboard_launcher.py",
    "codex_app_server_wake.py",
    "project_identity.py",
    "recover_bridge_session.py",
    "recover_state.py",
    "migrate_root.py",
    "routing_policy.py",
    "routing_rules.py",
    # The wrapper process cannot hot-reload itself.
    "server_wrapper.py",
})


def _is_no_restart_file(path: Path) -> bool:
    """Return True if changes to this file should never trigger an MCP server restart."""
    name = path.name
    return name in _NO_RESTART_FILENAMES or name.startswith("test_") or name.endswith("_test.py")


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


def _close_pipe(pipe: Optional[BinaryIO]) -> None:
    if pipe is None:
        return
    try:
        pipe.close()
    except OSError:
        return


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
        self._pending_changed_files: Set[Path] = set()
        self._last_change_time: Optional[float] = None
        self._last_io_time = self.now_fn()
        self._restart_history: List[float] = []
        self._stdin_eof = False
        self._thread_error: Optional[BaseException] = None
        self._tool_manifest_path = self.state_dir / TOOL_MANIFEST_FILENAME
        self._tool_refresh_status_path = self.state_dir / TOOL_REFRESH_STATUS_FILENAME
        self._clear_tool_refresh_status()

    def run(self) -> int:
        try:
            self._spawn_child()
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
                    continue

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

    def _pump_parent_stdin(self) -> None:
        try:
            while not self._stop_event.is_set():
                chunk = _read_available(self.stdin_stream, self.config.chunk_size)
                if not chunk:
                    with self._state_lock:
                        self._stdin_eof = True
                        child = self._child
                    if child is not None and child.stdin is not None:
                        _close_pipe(child.stdin)
                    return

                self._note_io()
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
        try:
            if child.stdout is None:
                return
            while not self._stop_event.is_set():
                chunk = _read_available(child.stdout, self.config.chunk_size)
                if not chunk:
                    return
                self._note_io()
                with self._stdout_lock:
                    self.stdout_stream.write(chunk)
                    self.stdout_stream.flush()
        except BaseException as exc:
            self._thread_error = exc
            self._stop_event.set()
        finally:
            _close_pipe(child.stdout)

    def _watch_for_code_changes(self) -> None:
        try:
            previous = _snapshot_mtimes(self.watch_paths)
            while not self._stop_event.wait(self.config.poll_interval_seconds):
                current = _snapshot_mtimes(self.watch_paths)
                changed = [path for path in self.watch_paths if current.get(path) != previous.get(path)]
                previous = current
                if not changed:
                    continue
                restart_changed = [p for p in changed if not _is_no_restart_file(p)]
                skipped_changed = [p for p in changed if _is_no_restart_file(p)]
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
            child = self._child
        return child is not None and child.poll() is None

    def _restart_child(self) -> bool:
        with self._state_lock:
            changed_files = sorted(str(path) for path in self._pending_changed_files)
            self._pending_changed_files.clear()
            self._last_change_time = None
        return self._replace_child(
            changed_files=changed_files,
            reason="bridge_code_changed_during_wrapper_session",
        )

    def _respawn_after_child_exit(self, child: subprocess.Popen[bytes], exit_code: int) -> bool:
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
        try:
            self._terminate_child(child)
            self._join_stdout_thread(old_child_pid)
            new_child = self._spawn_child()
        except BaseException as exc:
            self._report_error("agent-bridge server wrapper restart failed: %s" % exc)
            return False
        finally:
            with self._state_lock:
                self._restart_in_progress = False

        # Child restarted successfully. Status-file writes below are non-fatal:
        # a failure here must not kill the wrapper or orphan the new child.
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

    try:
        audit_wrapper_launch(state_dir=paths.state_dir, command=command)
    except Exception as exc:
        print("agent-bridge server wrapper audit failed: %s" % exc, file=sys.stderr, flush=True)

    exit_code = run_supervisor(
        command=command,
        state_dir=paths.state_dir,
        watch_paths=_watch_bridge_code_files(server_path.parent),
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
