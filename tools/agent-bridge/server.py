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
from core.runtime import build_runtime_breadcrumb, write_runtime_breadcrumb


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
    pid_dir = Path(state_dir) / "server-pids"
    pid_path = pid_dir / f"server-{os.getpid()}.pid"
    runtime_path = pid_dir / f"server-{os.getpid()}.json"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    write_runtime_breadcrumb(
        runtime_path,
        build_runtime_breadcrumb(state_dir=Path(state_dir), role="mcp_server", pid=os.getpid()),
    )

    def cleanup() -> None:
        try:
            pid_path.unlink(missing_ok=True)
            runtime_path.unlink(missing_ok=True)
        except OSError:
            pass

    return cleanup


def create_mcp(bridge: AgentBridge) -> FastMCP:
    mcp = FastMCP("agent-bridge")

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

    @mcp.tool(annotations=READ_ONLY)
    def bridge_status(session_id: Optional[str] = None) -> dict:
        """Return bridge pause state, hop count, state directory, and unread counts."""
        return as_dict(bridge.bridge_status(session_id=session_id))

    @mcp.tool(annotations=READ_ONLY)
    def bridge_process_status() -> dict:
        """Return watcher, MCP server marker, lock, and heartbeat process health."""
        return as_dict(bridge.bridge_process_status())

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
    bridge = create_bridge(args)
    cleanup_pid = register_server_pid(Path(args.state_dir))
    atexit.register(cleanup_pid)

    def handle_sigterm(signum, frame):  # noqa: ANN001
        cleanup_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    create_mcp(bridge).run()


if __name__ == "__main__":
    main()
