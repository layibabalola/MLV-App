import argparse
import asyncio
import atexit
import dataclasses
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode
import urllib.error
import urllib.request

from agent_bridge import AgentBridge, add_common_args
from compact import reap_stale_server_pids
from core.processes import is_process_alive
from core.runtime import build_runtime_breadcrumb, write_runtime_breadcrumb
from dashboard_server import DEFAULT_DASHBOARD_PORT, DashboardServerHandle, start_dashboard_server


_WINDOWS_PIPE_CHUNK_BYTES = 4096


def _install_windows_pipe_safety(chunk_size: int = _WINDOWS_PIPE_CHUNK_BYTES) -> None:
    """Re-wrap sys.stdout so each OS write is at most chunk_size bytes.

    Why: MCP's stdio transport flushes a full JSON-RPC response in one write.
    On Windows anonymous pipes, large writes intermittently raise
    OSError [Errno 22] inside anyio's `to_thread.run_sync(self._fp.flush)`,
    which kills the server before Claude Desktop can finish initialization.
    With ~41 registered tools the tools/list response is ~25 KB and lands
    inside the failure envelope.

    The fix replaces sys.stdout's underlying raw writer with a chunked
    RawIOBase that splits every os-level write into chunk_size-byte slices,
    keeping each write inside the Windows pipe envelope.
    """
    if sys.platform != "win32":
        return

    raw = sys.stdout.buffer.raw if hasattr(sys.stdout.buffer, "raw") else sys.stdout.buffer
    fd = raw.fileno()
    encoding = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
    errors = getattr(sys.stdout, "errors", "strict") or "strict"

    class _ChunkedStdout(io.TextIOBase):
        """Direct-write stdout proxy that avoids secondary buffering layers."""

        def __init__(self, fileno: int, *, text_encoding: str, text_errors: str, max_chunk_size: int) -> None:
            self._fileno = fileno
            self._encoding = text_encoding
            self._errors = text_errors
            self._chunk_size = max_chunk_size
            self.buffer = self

        @property
        def encoding(self) -> str:
            return self._encoding

        @property
        def errors(self) -> str:
            return self._errors

        def writable(self) -> bool:  # noqa: D401
            return True

        def fileno(self) -> int:
            return self._fileno

        def isatty(self) -> bool:
            return False

        def write(self, data) -> int:
            if isinstance(data, str):
                payload = data.encode(self._encoding, errors=self._errors)
                text_length = len(data)
            else:
                payload = bytes(data)
                text_length = len(payload)

            view = memoryview(payload).cast("B")
            total = len(view)
            offset = 0
            while offset < total:
                end = min(offset + self._chunk_size, total)
                written = os.write(self._fileno, bytes(view[offset:end]))
                if not written:
                    raise OSError("short write to stdout pipe")
                offset += written
            return text_length

        def flush(self) -> None:
            return

    sys.stdout = _ChunkedStdout(fd, text_encoding=encoding, text_errors=errors, max_chunk_size=chunk_size)


if sys.version_info < (3, 10):
    raise SystemExit("agent-bridge requires Python 3.10 or newer")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit(
        "Missing MCP Python SDK. Install it with: py -3 -m pip install -r tools\\agent-bridge\\requirements.txt"
    ) from exc


READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

NON_DESTRUCTIVE_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}

IDEMPOTENT_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

DESTRUCTIVE_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
}

TOOL_MANIFEST_SCHEMA_VERSION = 1
_DASHBOARD_RUNTIME_FILENAME = "dashboard-launcher.runtime.json"
_dashboard_handle: Optional[DashboardServerHandle] = None
_dashboard_handle_lock = threading.Lock()


def _read_dashboard_runtime(state_dir: Path) -> Optional[dict]:
    try:
        path = Path(state_dir) / _DASHBOARD_RUNTIME_FILENAME
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_dashboard_runtime(state_dir: Path, *, handle: DashboardServerHandle, project: str) -> None:
    path = Path(state_dir) / _DASHBOARD_RUNTIME_FILENAME
    payload = {
        "schema_version": 1,
        "status": "running",
        "pid": os.getpid(),
        "state_dir": str(state_dir),
        "url": handle.url,
        "token": handle.token,
        "csrf_token": handle.csrf_token,
        "project": project,
        "updated_at": build_runtime_breadcrumb(state_dir=Path(state_dir), role="mcp_dashboard", pid=os.getpid())[
            "timestamp"
        ],
        "source": "open_dashboard_mcp_tool",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%s.tmp" % (path.name, os.getpid()))
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _dashboard_runtime_healthy(runtime: dict, *, timeout_seconds: float = 3.0) -> bool:
    url = str(runtime.get("url") or "").rstrip("/")
    token = str(runtime.get("token") or "")
    if not url or not token:
        return False
    try:
        with urllib.request.urlopen("%s/api/healthz?%s" % (url, urlencode({"token": token})), timeout=timeout_seconds) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def _dashboard_safe_url(base_url: str, *, project: str) -> str:
    return "%s/?%s" % (base_url.rstrip("/"), urlencode({"project": project}))


def _dashboard_token_url(base_url: str, *, token: str, project: str) -> str:
    return "%s/?%s" % (base_url.rstrip("/"), urlencode({"token": token, "project": project}))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP bridge for Claude Desktop and Codex Desktop handoffs.")
    add_common_args(parser)
    return parser.parse_args(argv)


def create_bridge(args: argparse.Namespace) -> AgentBridge:
    return AgentBridge(Path(args.state_dir), max_hops=args.max_hops)


def as_dict(result):
    return dataclasses.asdict(result)


def register_server_pid(state_dir: Path):
    """Create a per-process MCP server marker and return its cleanup callback."""
    try:
        reap_stale_server_pids(Path(state_dir), max_age_hours=0, dry_run=False)
    except Exception as exc:
        print("agent-bridge MCP server stale marker cleanup failed (non-fatal): %s" % exc, file=sys.stderr, flush=True)
    pid_dir = Path(state_dir) / "server-pids"
    pid_path = pid_dir / f"server-{os.getpid()}.pid"
    runtime_path = pid_dir / f"server-{os.getpid()}.json"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    breadcrumb = build_runtime_breadcrumb(state_dir=Path(state_dir), role="mcp_server", pid=os.getpid())
    for env_key, field in (
        ("AGENT_BRIDGE_WRAPPER_PID", "wrapper_pid"),
        ("AGENT_BRIDGE_WRAPPER_CREATION_DATE", "wrapper_creation_date"),
        ("AGENT_BRIDGE_WRAPPER_EXECUTABLE_PATH", "wrapper_executable_path"),
        ("AGENT_BRIDGE_WRAPPER_COMMAND_HASH", "wrapper_command_hash"),
        ("AGENT_BRIDGE_HOST_PID", "host_pid"),
        ("AGENT_BRIDGE_HOST_KEY", "host_key"),
        ("AGENT_BRIDGE_HOST_PROCESS_NAME", "host_process_name"),
        ("AGENT_BRIDGE_HOST_CREATION_DATE", "host_creation_date"),
        ("AGENT_BRIDGE_HOST_EXECUTABLE_PATH", "host_executable_path"),
        ("AGENT_BRIDGE_HOST_COMMAND_HASH", "host_command_hash"),
    ):
        value = os.environ.get(env_key)
        if value:
            if field.endswith("_pid"):
                try:
                    breadcrumb[field] = int(value)
                    continue
                except ValueError:
                    pass
            breadcrumb[field] = value
    write_runtime_breadcrumb(runtime_path, breadcrumb)

    def cleanup() -> None:
        try:
            pid_path.unlink(missing_ok=True)
            runtime_path.unlink(missing_ok=True)
        except OSError:
            pass

    return cleanup


def _short_hash(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.casefold().encode("utf-8", errors="replace")).hexdigest()[:16]


def _wrapper_pid_from_env() -> Optional[int]:
    raw = os.environ.get("AGENT_BRIDGE_WRAPPER_PID")
    try:
        pid = int(raw or 0)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _wrapper_process_entry(pid: int) -> Optional[dict]:
    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process -Filter \"ProcessId = %s\" | "
                "Select-Object ProcessId,ParentProcessId,Name,CommandLine,ExecutablePath,CreationDate | "
                "ConvertTo-Json -Compress"
            )
            % int(pid),
        ]
        kwargs = {
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
                "name": row.get("Name") or "",
                "command_line": row.get("CommandLine") or "",
                "executable_path": row.get("ExecutablePath") or "",
                "creation_date": str(row.get("CreationDate") or ""),
            }
        except Exception:
            return None
    return None


def _wrapper_process_matches_env(wrapper_pid: int) -> bool:
    expected_creation_date = os.environ.get("AGENT_BRIDGE_WRAPPER_CREATION_DATE") or ""
    expected_executable_path = os.environ.get("AGENT_BRIDGE_WRAPPER_EXECUTABLE_PATH") or ""
    expected_command_hash = os.environ.get("AGENT_BRIDGE_WRAPPER_COMMAND_HASH") or ""
    if not expected_creation_date and not expected_executable_path and not expected_command_hash:
        return True
    entry = _wrapper_process_entry(wrapper_pid)
    if entry is None:
        return True
    if expected_creation_date and str(entry.get("creation_date") or "") != expected_creation_date:
        return False
    current_executable_path = str(entry.get("executable_path") or "")
    if (
        expected_executable_path
        and current_executable_path
        and current_executable_path.casefold() != expected_executable_path.casefold()
    ):
        return False
    current_command_hash = _short_hash(entry.get("command_line"))
    if expected_command_hash and current_command_hash and current_command_hash != expected_command_hash:
        return False
    return True


def start_wrapper_lifetime_watchdog(
    cleanup_pid,
    *,
    poll_seconds: float = 2.0,
    exit_fn: Callable[[int], object] = os._exit,
    stop_event: Optional[threading.Event] = None,
) -> Optional[threading.Thread]:
    wrapper_pid = _wrapper_pid_from_env()
    if wrapper_pid is None or wrapper_pid == os.getpid():
        return None
    stopper = stop_event or threading.Event()

    def _watch() -> None:
        while not stopper.is_set():
            if not is_process_alive(wrapper_pid) or not _wrapper_process_matches_env(wrapper_pid):
                cleanup_pid()
                exit_fn(0)
                return
            stopper.wait(poll_seconds)

    thread = threading.Thread(target=_watch, name="agent-bridge-wrapper-lifetime", daemon=True)
    thread.start()
    return thread


def _jsonable(obj):
    """Coerce MCP SDK pydantic models (ToolAnnotations, Icon, etc.) to plain dicts.

    Used as the `default=` callable for json.dump / json.dumps calls in this
    module so that tool-manifest serialization survives MCP SDK objects that
    aren't JSON-native.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, default=_jsonable)
        handle.write("\n")
    tmp.replace(path)


def write_tool_manifest(*, state_dir: Path, mcp: FastMCP) -> dict:
    tools = []
    for info in mcp._tool_manager.list_tools():
        tool_payload = {
            "name": info.name,
            "title": info.title,
            "description": info.description,
            "inputSchema": info.parameters,
            "outputSchema": info.output_schema,
            "annotations": info.annotations,
            "icons": info.icons,
            "meta": info.meta,
        }
        tools.append(tool_payload)
    tool_names = [tool["name"] for tool in tools]
    signature_payload = {"tool_names": tool_names, "tools": tools}
    signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, default=_jsonable).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": TOOL_MANIFEST_SCHEMA_VERSION,
        "generated_at": build_runtime_breadcrumb(state_dir=state_dir, role="mcp_server", pid=os.getpid())["timestamp"],
        "server_pid": os.getpid(),
        "tool_count": len(tools),
        "tool_names": tool_names,
        "signature": signature,
        "tools": tools,
    }
    _write_json(Path(state_dir) / "tool-manifest.json", manifest)
    return manifest


def create_mcp(bridge: AgentBridge) -> FastMCP:
    mcp = FastMCP("agent-bridge")

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def send_to_peer(
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
    ) -> dict:
        """Queue a handoff message for the peer agent.

        The message must contain [[handoff:claude]] or [[handoff:codex]] matching
        to_agent. The marker is stripped before delivery. relay_mode=ephemeral
        enables a scoped project-bucket relay for background chats.
        """
        return as_dict(
            bridge.send_to_peer(
                from_agent,
                to_agent,
                message,
                session_id=session_id,
                target_session_id=target_session_id,
                relay_mode=relay_mode,
                sender_session_id=sender_session_id,
                reply_to_session_id=reply_to_session_id,
                ephemeral_relay_id=ephemeral_relay_id,
                pair_id=pair_id,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def send_control_message(
        from_agent: str,
        to_agent: str,
        control_type: str,
        summary: str,
        body: str,
        session_id: Optional[str] = None,
        status: str = "info",
        replace_existing_control: bool = True,
    ) -> dict:
        """Queue a high-priority control-plane message such as HANDSHAKE, HANDSHAKE_ACK, or SESSION_UPDATE."""
        return as_dict(
            bridge.send_control_message(
                from_agent=from_agent,
                to_agent=to_agent,
                control_type=control_type,
                summary=summary,
                body=body,
                session_id=session_id,
                status=status,
                replace_existing_control=replace_existing_control,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def check_inbox(
        agent: str,
        session_id: Optional[str] = None,
        mark_read: bool = False,
        include_parents: bool = False,
        record_seen: bool = True,
    ) -> dict:
        """Return unread bridge messages for an agent, optionally including parent buckets and marking them read."""
        return as_dict(
            bridge.check_inbox(
                agent,
                session_id=session_id,
                mark_read=mark_read,
                include_parents=include_parents,
                record_seen=record_seen,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    async def wait_inbox(
        agent: str,
        session_ids: Optional[list[str]] = None,
        timeout_seconds: int = 600,
        mark_read: bool = False,
        record_seen: bool = True,
    ) -> dict:
        """Block until a new message arrives for the agent, or timeout elapses."""
        result = await asyncio.to_thread(
            bridge.wait_inbox,
            agent,
            session_ids=session_ids,
            timeout_seconds=timeout_seconds,
            mark_read=mark_read,
            record_seen=record_seen,
        )
        return as_dict(result)

    @mcp.tool(annotations=READ_ONLY)
    def peek_inbox(agent: str, session_id: Optional[str] = None, include_parents: bool = False) -> dict:
        """Return unread bridge messages for an agent without changing mailbox state."""
        return as_dict(bridge.peek_inbox(agent, session_id=session_id, include_parents=include_parents))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def mark_read(agent: str, message_id: str, session_id: Optional[str] = None) -> dict:
        """Mark one bridge inbox message read by id."""
        return as_dict(bridge.mark_read(agent, message_id, session_id=session_id))

    @mcp.tool(annotations=READ_ONLY)
    def message_status(message_id: str) -> dict:
        """Return the queued/seen/read/handled/failed lifecycle status for one message."""
        return as_dict(bridge.message_status(message_id))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def mark_seen(agent: str, message_id: str, via: str, session_id: Optional[str] = None) -> dict:
        """Mark one bridge inbox message seen without marking it read or handled."""
        return as_dict(bridge.mark_seen(agent, message_id, via=via, session_id=session_id))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def mark_handled(
        agent: str,
        message_id: str,
        status: str = "handled",
        reason: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """Mark one bridge inbox message handled, failed, or ignored."""
        return as_dict(
            bridge.mark_handled(
                agent,
                message_id,
                status=status,
                reason=reason,
                session_id=session_id,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def list_pending_receipts(
        agent: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        body_preview_chars: int = 240,
    ) -> dict:
        """List a bounded page of queued/seen/read receipt summaries."""
        return as_dict(
            bridge.list_pending_receipts(
                agent=agent,
                limit=limit,
                offset=offset,
                body_preview_chars=body_preview_chars,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def receipt_debt_cleanup(
        agent: Optional[str] = None,
        old_after_seconds: Optional[int] = None,
        stale_after_seconds: int = 300,
        apply: bool = False,
        rearm_stale_unread: bool = False,
        limit: int = 50,
        body_preview_chars: int = 240,
    ) -> dict:
        """Report receipt debt and optionally apply safe cleanup migrations."""
        return as_dict(
            bridge.receipt_debt_cleanup(
                agent=agent,
                old_after_seconds=old_after_seconds,
                stale_after_seconds=stale_after_seconds,
                apply=apply,
                rearm_stale_unread=rearm_stale_unread,
                limit=limit,
                body_preview_chars=body_preview_chars,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def record_pending_bridge_action(
        owner_agent: str,
        summary: str,
        message_id: Optional[str] = None,
        related_session_id: Optional[str] = None,
        priority: str = "normal",
        due_at: Optional[str] = None,
        details: Optional[str] = None,
    ) -> dict:
        """Record a durable bridge follow-up item after surfacing a message but before resuming other work."""
        return as_dict(
            bridge.record_pending_bridge_action(
                owner_agent=owner_agent,
                summary=summary,
                message_id=message_id,
                related_session_id=related_session_id,
                priority=priority,
                due_at=due_at,
                details=details,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def list_pending_bridge_actions(
        owner_agent: Optional[str] = None,
        status: str = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List pending or resolved bridge follow-up items from the durable action ledger."""
        return as_dict(
            bridge.list_pending_bridge_actions(
                owner_agent=owner_agent,
                status=status,
                limit=limit,
                offset=offset,
            )
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def resolve_pending_bridge_action(
        action_id: str,
        resolved_by: Optional[str] = None,
        resolution: Optional[str] = None,
    ) -> dict:
        """Resolve one pending bridge follow-up item by id."""
        return as_dict(
            bridge.resolve_pending_bridge_action(
                action_id=action_id,
                resolved_by=resolved_by,
                resolution=resolution,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def record_implementation_event(
        owner_agent: str,
        peer_agent: str,
        summary: str,
        message_type: str = "IMPLEMENTATION_UPDATE",
        commit: Optional[str] = None,
        tests: Optional[list[str]] = None,
        details: Optional[str] = None,
        related_session_id: Optional[str] = None,
    ) -> dict:
        """Record durable implementation progress for later peer catch-up digests."""
        return as_dict(
            bridge.record_implementation_event(
                owner_agent=owner_agent,
                peer_agent=peer_agent,
                summary=summary,
                message_type=message_type,
                commit=commit,
                tests=tests,
                details=details,
                related_session_id=related_session_id,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def list_implementation_journal(
        owner_agent: Optional[str] = None,
        peer_agent: Optional[str] = None,
        since_sequence: int = 0,
        limit: int = 50,
    ) -> dict:
        """List durable implementation progress events and peer acknowledgement state."""
        return as_dict(
            bridge.list_implementation_journal(
                owner_agent=owner_agent,
                peer_agent=peer_agent,
                since_sequence=since_sequence,
                limit=limit,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def send_catchup_digest(
        from_agent: str,
        to_agent: str,
        target_session_id: str,
        reason: str = "manual",
        max_items: int = 20,
    ) -> dict:
        """Queue one coalesced implementation catch-up digest for a peer."""
        return as_dict(
            bridge.send_catchup_digest(
                from_agent=from_agent,
                to_agent=to_agent,
                target_session_id=target_session_id,
                reason=reason,
                max_items=max_items,
            )
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def start_execution_task(
        owner_agent: str,
        summary: str,
        source: str,
        related_action_id: Optional[str] = None,
        message_id: Optional[str] = None,
        checkpoint: Optional[str] = None,
        eta_at: Optional[str] = None,
        allowed_interrupts: Optional[list[str]] = None,
        interrupt_mode: str = "task_switch",
        priority: Optional[str] = None,
        displaced_by: Optional[str] = None,
        displacement_reason: Optional[str] = None,
        prior_action_id: Optional[str] = None,
        prior_disposition: Optional[str] = None,
    ) -> dict:
        """Start an explicit execution-lane task; replacing an active task requires explicit displacement metadata."""
        return as_dict(
            bridge.start_execution_task(
                owner_agent=owner_agent,
                summary=summary,
                source=source,
                related_action_id=related_action_id,
                message_id=message_id,
                checkpoint=checkpoint,
                eta_at=eta_at,
                allowed_interrupts=allowed_interrupts,
                interrupt_mode=interrupt_mode,
                priority=priority,
                displaced_by=displaced_by,
                displacement_reason=displacement_reason,
                prior_action_id=prior_action_id,
                prior_disposition=prior_disposition,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def record_execution_progress(
        owner_agent: str,
        task_id: str,
        signal_type: str,
        details: Optional[str] = None,
        checkpoint: Optional[str] = None,
    ) -> dict:
        """Record near-immediate proof that execution actually started for the active task."""
        return as_dict(
            bridge.record_execution_progress(
                owner_agent=owner_agent,
                task_id=task_id,
                signal_type=signal_type,
                details=details,
                checkpoint=checkpoint,
            )
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def classify_execution_interrupt(
        owner_agent: str,
        task_id: str,
        disposition: str,
        reason: Optional[str] = None,
        interrupt_kind: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> dict:
        """Record the WGI-10 disposition for an interrupt while an execution task is active."""
        return as_dict(
            bridge.classify_execution_interrupt(
                owner_agent=owner_agent,
                task_id=task_id,
                disposition=disposition,
                reason=reason,
                interrupt_kind=interrupt_kind,
                message_id=message_id,
            )
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def complete_execution_task(
        owner_agent: str,
        task_id: str,
        outcome: str,
        resolution: Optional[str] = None,
    ) -> dict:
        """Close the active execution task with an explicit terminal state."""
        return as_dict(
            bridge.complete_execution_task(
                owner_agent=owner_agent,
                task_id=task_id,
                outcome=outcome,
                resolution=resolution,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def execution_status(owner_agent: str) -> dict:
        """Return the active execution-lane task, derived status, and recent closed tasks."""
        return as_dict(bridge.execution_status(owner_agent=owner_agent))

    @mcp.tool(annotations=READ_ONLY)
    def next_pending_bridge_action(owner_agent: str) -> dict:
        """Return the highest-priority actionable ledger item for one agent."""
        return as_dict(bridge.next_pending_bridge_action(owner_agent=owner_agent))

    @mcp.tool()
    def record_reviewer_wait(
        owner_agent: str,
        reviewer_id: str,
        request_id: Optional[str] = None,
        owner_session_id: Optional[str] = None,
        subject: Optional[str] = None,
        eta_minutes: Optional[int] = None,
        note: Optional[str] = None,
    ) -> dict:
        """Record that a background reviewer is pending and optionally schedule an ETA/checkback."""
        return as_dict(
            bridge.record_reviewer_wait(
                owner_agent=owner_agent,
                reviewer_id=reviewer_id,
                request_id=request_id,
                owner_session_id=owner_session_id,
                subject=subject,
                eta_minutes=eta_minutes,
                note=note,
            )
        )

    @mcp.tool()
    def update_reviewer_wait(
        wait_id: str,
        event_type: str,
        owner_agent: str = "codex",
        reviewer_id: Optional[str] = None,
        eta_minutes: Optional[int] = None,
        result: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        """Update reviewer wait state with ETA/checkback/verdict/parked/blocked status."""
        return as_dict(
            bridge.update_reviewer_wait(
                wait_id=wait_id,
                event_type=event_type,
                owner_agent=owner_agent,
                reviewer_id=reviewer_id,
                eta_minutes=eta_minutes,
                result=result,
                note=note,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def reviewer_wait_status(owner_agent: str = "codex", include_all: bool = False) -> dict:
        """Return reviewer wait debt and scheduled checkbacks for one owner."""
        return as_dict(bridge.reviewer_wait_status(owner_agent=owner_agent, include_all=include_all))

    @mcp.tool(annotations=READ_ONLY)
    def bridge_status(session_id: Optional[str] = None) -> dict:
        """Return bridge pause state, hop count, state directory, and unread counts."""
        return as_dict(bridge.bridge_status(session_id=session_id))

    @mcp.tool(annotations=READ_ONLY)
    def bridge_process_status() -> dict:
        """Return watcher, MCP server marker, lock, and heartbeat process health."""
        return as_dict(bridge.bridge_process_status())

    @mcp.tool(annotations=READ_ONLY)
    def bridge_health_panel(
        agent: str,
        session_id: Optional[str] = None,
        include_extended: bool = False,
        format: str = "json",
        stuck_wake_threshold_seconds: int = 30,
    ) -> dict:
        """Return a read-only bridge health snapshot as JSON or rendered markdown."""
        return as_dict(
            bridge.bridge_health_panel(
                agent=agent,
                session_id=session_id,
                include_extended=include_extended,
                format=format,
                stuck_wake_threshold_seconds=stuck_wake_threshold_seconds,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def dashboard_overview(agent: str, project: Optional[str] = None, format: str = "json") -> dict:
        """Return the local admin dashboard overview as JSON or escaped markdown."""
        return as_dict(bridge.dashboard_overview(agent=agent, project=project, format=format))

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def open_dashboard(project: Optional[str] = None) -> dict:
        """Start the local dashboard server if needed and open it in the default browser."""
        global _dashboard_handle

        selected_project = (project or "mlv-app").strip() or "mlv-app"
        runtime = _read_dashboard_runtime(bridge.state_dir)
        if runtime and _dashboard_runtime_healthy(runtime):
            runtime_url = str(runtime.get("url") or "")
            runtime_token = str(runtime.get("token") or "")
            url_with_token = _dashboard_token_url(runtime_url, token=runtime_token, project=selected_project)
            safe_url = _dashboard_safe_url(runtime_url, project=selected_project)
            try:
                if sys.platform == "win32":
                    subprocess.Popen(
                        ["cmd", "/c", "start", "", url_with_token],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    import webbrowser

                    webbrowser.open(url_with_token)
            except OSError as exc:
                return {
                    "ok": False,
                    "status": "open_failed",
                    "url": safe_url,
                    "message": "Dashboard is already running, but browser launch failed: %s" % exc,
                }
            return {
                "ok": True,
                "status": "opened",
                "url": safe_url,
                "message": "Dashboard opened in browser. Reused existing background server.",
            }

        with _dashboard_handle_lock:
            if _dashboard_handle is None or not _dashboard_handle.thread.is_alive():
                _dashboard_handle = start_dashboard_server(
                    bridge,
                    token=str((runtime or {}).get("token") or "") or None,
                    csrf_token=str((runtime or {}).get("csrf_token") or "") or None,
                    default_agent="codex",
                    default_project=selected_project,
                    live_app_dom_titles=True,
                    port=DEFAULT_DASHBOARD_PORT,
                    fallback_to_ephemeral=True,
                )
                _write_dashboard_runtime(bridge.state_dir, handle=_dashboard_handle, project=selected_project)
            handle = _dashboard_handle

        url_with_token = _dashboard_token_url(handle.url, token=handle.token, project=selected_project)
        safe_url = _dashboard_safe_url(handle.url, project=selected_project)
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    ["cmd", "/c", "start", "", url_with_token],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                import webbrowser

                webbrowser.open(url_with_token)
        except OSError as exc:
            return {
                "ok": False,
                "status": "open_failed",
                "url": safe_url,
                "message": "Dashboard server started, but browser launch failed: %s" % exc,
            }
        return {
            "ok": True,
            "status": "opened",
            "url": safe_url,
            "message": "Dashboard opened in browser. Auto-refreshes every 5s.",
        }

    @mcp.tool(annotations=READ_ONLY)
    def list_pairings(agent: str, project: Optional[str] = None) -> dict:
        """List dashboard-visible primary pairings."""
        return as_dict(bridge.list_pairings(agent=agent, project=project))

    @mcp.tool(annotations=READ_ONLY)
    def pairing_details(agent: str, project: Optional[str] = None, session_id: Optional[str] = None) -> dict:
        """Return guided pairing details and available actions for one session."""
        return as_dict(bridge.pairing_details(agent=agent, project=project, session_id=session_id))

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def start_guided_pairing(
        agent: str,
        project: Optional[str] = None,
        session_id: Optional[str] = None,
        desired_role: str = "active_primary",
        source: str = "dashboard",
    ) -> dict:
        """Start a guided pairing flow without mutating active sessions."""
        return as_dict(
            bridge.start_guided_pairing(
                agent=agent,
                project=project,
                session_id=session_id,
                desired_role=desired_role,
                source=source,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def confirm_guided_pairing(
        agent: str,
        project: Optional[str] = None,
        session_id: Optional[str] = None,
        decision: str = "active_primary",
        source: str = "dashboard",
        confirm: bool = False,
    ) -> dict:
        """Confirm a guided pairing decision after user-visible review."""
        return as_dict(
            bridge.confirm_guided_pairing(
                agent=agent,
                project=project,
                session_id=session_id,
                decision=decision,
                source=source,
                confirm=confirm,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def list_contracts(agent: str, project: Optional[str] = None, include_inactive: bool = True) -> dict:
        """List dashboard-visible knowledge/cross-project contracts."""
        return as_dict(bridge.list_contracts(agent=agent, project=project, include_inactive=include_inactive))

    @mcp.tool(annotations=READ_ONLY)
    def list_policy_dashboard(agent: str, project: Optional[str] = None) -> dict:
        """Return runtime policy facts used by the dashboard."""
        return as_dict(bridge.list_policy_dashboard(agent=agent, project=project))

    @mcp.tool(annotations=READ_ONLY)
    def validate_policy_dashboard(agent: str, project: Optional[str] = None) -> dict:
        """Validate that the runtime policy dashboard can resolve protected docs."""
        return as_dict(bridge.validate_policy_dashboard(agent=agent, project=project))

    @mcp.tool(annotations=READ_ONLY)
    def list_remote_authority_requests(
        agent: str,
        project: Optional[str] = None,
        max_count: int = 100,
    ) -> dict:
        """List rejected remote-authority requests for local dashboard review."""
        return as_dict(
            bridge.list_remote_authority_requests(agent=agent, project=project, max_count=max_count)
        )

    @mcp.tool(annotations=READ_ONLY)
    def audit_timeline(
        agent: str,
        action: Optional[str] = None,
        project: Optional[str] = None,
        max_count: int = 100,
    ) -> dict:
        """Return a tenant-filtered local audit timeline for dashboard display."""
        return as_dict(
            bridge.audit_timeline(agent=agent, action=action, project=project, max_count=max_count)
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def revoke_contract(
        link_id: str,
        project: str,
        agent: str,
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
        source: str = "dashboard",
        confirm_revoke: bool = False,
    ) -> dict:
        """Revoke a contract through the shared dashboard/local-chat confirmation path."""
        return as_dict(
            bridge.revoke_contract(
                link_id=link_id,
                project=project,
                agent=agent,
                session_id=session_id,
                reason=reason,
                source=source,
                confirm_revoke=confirm_revoke,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def renew_contract(
        link_id: str,
        project: str,
        agent: str,
        ttl_minutes: int = 120,
        session_id: Optional[str] = None,
        source: str = "dashboard",
        confirm_renew: bool = False,
    ) -> dict:
        """Renew a contract through the shared dashboard/local-chat confirmation path."""
        return as_dict(
            bridge.renew_contract(
                link_id=link_id,
                project=project,
                agent=agent,
                ttl_minutes=ttl_minutes,
                session_id=session_id,
                source=source,
                confirm_renew=confirm_renew,
            )
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def rename_local_alias(
        link_id: str,
        project: str,
        agent: str,
        alias: str,
        source: str = "dashboard",
    ) -> dict:
        """Update a trusted local display alias for a contract."""
        return as_dict(
            bridge.rename_local_alias(
                link_id=link_id,
                project=project,
                agent=agent,
                alias=alias,
                source=source,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def classify_remote_authority_request(
        from_agent: str,
        text: str,
        project: Optional[str] = None,
        audit: bool = True,
    ) -> dict:
        """Classify a remote peer request before any privileged local action is considered."""
        return as_dict(
            bridge.classify_remote_authority_request(
                from_agent=from_agent,
                text=text,
                project=project,
                audit=audit,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def wake_breaker_status(session_id: Optional[str] = None) -> dict:
        """Return persisted wake breaker state for one session or all sessions."""
        return as_dict(bridge.wake_breaker_status(session_id=session_id))

    @mcp.tool(annotations=READ_ONLY)
    def wake_fire_history(session_id: Optional[str] = None, limit: int = 20) -> dict:
        """Return recent watcher wake-fire events, optionally filtered by session."""
        return as_dict(bridge.wake_fire_history(session_id=session_id, limit=limit))

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def stale_unread_watchdog(
        agent: Optional[str] = None,
        stale_after_seconds: int = 300,
        rearm: bool = False,
        limit: int = 50,
    ) -> dict:
        """Find wake-delivered unread rows and optionally rearm their watcher ids."""
        return as_dict(
            bridge.stale_unread_watchdog(
                agent=agent,
                stale_after_seconds=stale_after_seconds,
                rearm=rearm,
                limit=limit,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def project_identity(cwd: Optional[str] = None) -> dict:
        """Derive the canonical project root and rendezvous name from a repo/worktree path."""
        return as_dict(bridge.project_identity(cwd=cwd))

    @mcp.tool(annotations=READ_ONLY)
    def evaluate_routing(source: str, direction: str, text: str) -> dict:
        """Evaluate learned and suppressed routing rules for a candidate message."""
        return as_dict(bridge.evaluate_routing(source=source, direction=direction, text=text))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def activate_session(agent: str, session_id: str, project: Optional[str] = None) -> dict:
        """Mark a session as the current active chat for an agent/project and supersede older same-agent sessions."""
        return as_dict(bridge.activate_session(agent=agent, session_id=session_id, project=project))

    @mcp.tool(annotations=READ_ONLY)
    def session_status(project: Optional[str] = None) -> dict:
        """Return active and historical session registry data for a project."""
        return as_dict(bridge.session_status(project=project))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def reap_expired_pending_pairs(project: Optional[str] = None, now: Optional[str] = None) -> dict:
        """Fallback stale pending_pair sessions to background mode."""
        return as_dict(bridge.reap_expired_pending_pairs(project=project, now=now))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def truedup_session_routing(agent: str, dry_run: bool = True, mode: str = "rekey") -> dict:
        """Find and optionally rekey/quarantine inbox rows in orphaned session buckets."""
        return as_dict(bridge.truedup_session_routing(agent=agent, dry_run=dry_run, mode=mode))

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def cross_pair_init(
        agent: str,
        project: str,
        peer_project: str,
        role: str,
        nonce: str,
        session_id: Optional[str] = None,
        confirm_different_projects: bool = False,
        ttl_minutes: int = 120,
        requested_permission_tier: str = "read_and_advise",
    ) -> dict:
        """Manually establish one side of a nonce-matched cross-project advisory link."""
        return as_dict(
            bridge.cross_pair_init(
                agent=agent,
                project=project,
                peer_project=peer_project,
                role=role,
                nonce=nonce,
                session_id=session_id,
                confirm_different_projects=confirm_different_projects,
                ttl_minutes=ttl_minutes,
                requested_permission_tier=requested_permission_tier,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    def list_cross_project_links(project: Optional[str] = None, include_inactive: bool = False) -> dict:
        """List active cross-project links, optionally scoped to one project."""
        return as_dict(bridge.list_cross_project_links(project=project, include_inactive=include_inactive))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def cross_pair_promote(
        link_id: str,
        project: str,
        permission_tier: str,
        agent: str,
        session_id: Optional[str] = None,
        confirm_write_override: bool = False,
    ) -> dict:
        """Promote or downgrade a cross-project link permission; write override requires executor confirmation."""
        return as_dict(
            bridge.cross_pair_promote(
                link_id=link_id,
                project=project,
                permission_tier=permission_tier,
                agent=agent,
                session_id=session_id,
                confirm_write_override=confirm_write_override,
            )
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def cross_pair_revoke(
        link_id: str,
        project: str,
        agent: str,
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict:
        """Revoke a cross-project link from either participating project."""
        return as_dict(
            bridge.cross_pair_revoke(
                link_id=link_id,
                project=project,
                agent=agent,
                session_id=session_id,
                reason=reason,
            )
        )

    @mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
    def send_cross_project_message(
        link_id: str,
        from_project: str,
        from_agent: str,
        to_agent: str,
        message: str,
    ) -> dict:
        """Send communication-only advice through an active cross-project link."""
        return as_dict(
            bridge.send_cross_project_message(
                link_id=link_id,
                from_project=from_project,
                from_agent=from_agent,
                to_agent=to_agent,
                message=message,
            )
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def end_session(agent: str, session_id: str, project: Optional[str] = None) -> dict:
        """Mark a session ended and notify the active peer session to stop sending there."""
        return as_dict(bridge.end_session(agent=agent, session_id=session_id, project=project))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def pause_bridge() -> dict:
        """Pause delivery of new bridge handoffs."""
        return as_dict(bridge.pause_bridge())

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def resume_bridge() -> dict:
        """Resume delivery of new bridge handoffs."""
        return as_dict(bridge.resume_bridge())

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def resume_wake_for_session(session_id: str) -> dict:
        """Clear the wake circuit breaker for one session."""
        return as_dict(bridge.resume_wake_for_session(session_id=session_id))

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def nudge_peer(agent: str, session_id: str) -> dict:
        """Request one receiver wake attempt; grants a one-shot breaker bypass when needed."""
        return as_dict(bridge.nudge_peer(agent=agent, session_id=session_id))

    @mcp.tool(annotations=DESTRUCTIVE_WRITE)
    def clear_inbox(agent: Optional[str] = None, session_id: Optional[str] = None) -> dict:
        """Clear queued inbox messages and reset hop/dedup state for a session."""
        return as_dict(bridge.clear_inbox(agent=agent, session_id=session_id))

    @mcp.tool(annotations=DESTRUCTIVE_WRITE)
    def clear_bucket(bucket: str, agent: Optional[str] = None) -> dict:
        """Clear queued inbox messages and reset dedupe state for an explicit bucket."""
        return as_dict(bridge.clear_bucket(bucket=bucket, agent=agent))

    @mcp.tool(annotations=DESTRUCTIVE_WRITE)
    def reset_session(session_id: Optional[str] = None) -> dict:
        """Reset hop and duplicate-tracking state for a session."""
        return as_dict(bridge.reset_session(session_id=session_id))

    @mcp.tool(annotations=DESTRUCTIVE_WRITE)
    def reset_bucket(bucket: str) -> dict:
        """Reset hop and duplicate-tracking state for an explicit bucket."""
        return as_dict(bridge.reset_bucket(bucket=bucket))

    return mcp


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    _install_windows_pipe_safety()
    bridge = create_bridge(args)
    mcp = create_mcp(bridge)
    write_tool_manifest(state_dir=Path(args.state_dir), mcp=mcp)
    cleanup_pid = register_server_pid(Path(args.state_dir))
    atexit.register(cleanup_pid)
    start_wrapper_lifetime_watchdog(cleanup_pid)

    def handle_sigterm(signum, frame):  # noqa: ANN001
        cleanup_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    mcp.run()


if __name__ == "__main__":
    main()
