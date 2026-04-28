import argparse
import asyncio
import atexit
import dataclasses
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from agent_bridge import AgentBridge, add_common_args


if sys.version_info < (3, 10):
    raise SystemExit("agent-bridge requires Python 3.10 or newer")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit(
        "Missing MCP Python SDK. Install it with: py -3 -m pip install -r tools\\agent-bridge\\requirements.txt"
    ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP bridge for Claude Desktop and Codex Desktop handoffs.")
    add_common_args(parser)
    return parser.parse_args()


args = parse_args()
bridge = AgentBridge(Path(args.state_dir), max_hops=args.max_hops)
mcp = FastMCP("agent-bridge")

# --- PID hygiene: per-process marker, graceful shutdown ---

# Claude Desktop, Codex Desktop, and direct probes may all spawn their own stdio
# MCP server instance against the same bridge state dir.  A shared singleton PID
# file lets one client kill another client's live transport, so each process owns
# only its own marker.
_pid_dir = Path(args.state_dir) / "server-pids"
_pid_path = _pid_dir / f"server-{os.getpid()}.pid"


def _cleanup_pid() -> None:
    try:
        _pid_path.unlink(missing_ok=True)
    except OSError:
        pass


def _handle_sigterm(signum, frame):  # noqa: ANN001
    _cleanup_pid()
    sys.exit(0)


_pid_dir.mkdir(parents=True, exist_ok=True)
_pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
atexit.register(_cleanup_pid)
signal.signal(signal.SIGTERM, _handle_sigterm)


def as_dict(result):
    return dataclasses.asdict(result)


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


@mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
def send_to_peer(from_agent: str, to_agent: str, message: str, session_id: Optional[str] = None) -> dict:
    """Queue a handoff message for the peer agent.

    The message must contain [[handoff:claude]] or [[handoff:codex]] matching
    to_agent. The marker is stripped before delivery.
    """
    return as_dict(bridge.send_to_peer(from_agent, to_agent, message, session_id=session_id))


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
) -> dict:
    """Return unread bridge messages for an agent, optionally including parent buckets and marking them read."""
    return as_dict(
        bridge.check_inbox(
            agent,
            session_id=session_id,
            mark_read=mark_read,
            include_parents=include_parents,
        )
    )


@mcp.tool(annotations=NON_DESTRUCTIVE_WRITE)
async def wait_inbox(
    agent: str,
    session_ids: Optional[list] = None,
    timeout_seconds: int = 600,
    mark_read: bool = False,
) -> dict:
    """Block until a new message arrives for the agent, or timeout elapses.

    Async wrapper that runs the blocking bridge.wait_inbox in a worker thread
    via asyncio.to_thread().  This is critical: it keeps the MCP server's
    asyncio event loop responsive so heartbeats and other tool calls aren't
    starved during the wait.  Without this, an MCP host that polices stdio
    activity will close the transport when the server appears silent.

    Use this for the "blocking-tool-call" wake pattern: the model is suspended
    at the tool boundary while we wait, so idle time costs zero tokens.  Loop:
    call wait_inbox -> handle returned messages -> call wait_inbox again.  On
    timeout (data.timed_out=True), re-invoke immediately.

    session_ids is an optional list of buckets to watch (e.g., ["mlv-app",
    "default", "<your-GUID>"]).  Omit or pass None to watch every session.
    Default mark_read=False so the caller decides when to mark consumed —
    avoid silent-eat failures.

    Note: requires the host's MCP tool_timeout to be at least timeout_seconds.
    Codex: set tool_timeout_sec under [mcp_servers.agent_bridge] in
    ~/.codex/config.toml.
    """
    result = await asyncio.to_thread(
        bridge.wait_inbox,
        agent,
        session_ids=session_ids,
        timeout_seconds=timeout_seconds,
        mark_read=mark_read,
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
def bridge_status(session_id: Optional[str] = None) -> dict:
    """Return bridge pause state, hop count, state directory, and unread counts."""
    return as_dict(bridge.bridge_status(session_id=session_id))


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


@mcp.tool(annotations=DESTRUCTIVE_WRITE)
def clear_inbox(agent: Optional[str] = None, session_id: Optional[str] = None) -> dict:
    """Clear queued inbox messages and reset hop/dedup state for a session."""
    return as_dict(bridge.clear_inbox(agent=agent, session_id=session_id))


@mcp.tool(annotations=DESTRUCTIVE_WRITE)
def reset_session(session_id: Optional[str] = None) -> dict:
    """Reset hop and duplicate-tracking state for a session."""
    return as_dict(bridge.reset_session(session_id=session_id))


if __name__ == "__main__":
    mcp.run()
