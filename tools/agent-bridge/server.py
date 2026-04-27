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
