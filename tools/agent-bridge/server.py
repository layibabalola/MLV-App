import argparse
import asyncio
import atexit
import dataclasses
import hashlib
import json
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

TOOL_MANIFEST_SCHEMA_VERSION = 1


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
    ) -> dict:
        """Queue a handoff message for the peer agent.

        The message must contain [[handoff:claude]] or [[handoff:codex]] matching
        to_agent. The marker is stripped before delivery.
        """
        return as_dict(
            bridge.send_to_peer(
                from_agent,
                to_agent,
                message,
                session_id=session_id,
                target_session_id=target_session_id,
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
    def wake_breaker_status(session_id: Optional[str] = None) -> dict:
        """Return persisted wake breaker state for one session or all sessions."""
        return as_dict(bridge.wake_breaker_status(session_id=session_id))

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
    bridge = create_bridge(args)
    mcp = create_mcp(bridge)
    write_tool_manifest(state_dir=Path(args.state_dir), mcp=mcp)
    cleanup_pid = register_server_pid(Path(args.state_dir))
    atexit.register(cleanup_pid)

    def handle_sigterm(signum, frame):  # noqa: ANN001
        cleanup_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    mcp.run()


if __name__ == "__main__":
    main()
