import argparse
import dataclasses
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
def check_inbox(agent: str, session_id: Optional[str] = None, mark_read: bool = True) -> dict:
    """Return unread bridge messages for an agent, optionally marking them read."""
    return as_dict(bridge.check_inbox(agent, session_id=session_id, mark_read=mark_read))


@mcp.tool(annotations=READ_ONLY)
def peek_inbox(agent: str, session_id: Optional[str] = None) -> dict:
    """Return unread bridge messages for an agent without changing mailbox state."""
    return as_dict(bridge.peek_inbox(agent, session_id=session_id))


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
