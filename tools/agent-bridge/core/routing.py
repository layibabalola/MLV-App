from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from .addressing import Address, AgentInbox, MessageKind, ProjectInbox, SenderContext, SessionInbox, bucket_id, inbox_level


class RoutingStatus(str, Enum):
    DELIVERED = "delivered"
    REJECTED = "rejected"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class RoutingDecision:
    status: RoutingStatus
    bucket: Optional[str]
    inbox_level: Optional[str]
    reason: Optional[str] = None
    parent_project: Optional[str] = None
    escalated_from: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status in {RoutingStatus.DELIVERED, RoutingStatus.ESCALATED}


def _session_record(registry: Dict[str, Any], project: str, session_id: str) -> Optional[Dict[str, Any]]:
    return (
        (registry.get("projects") or {})
        .get(project, {})
        .get("sessions", {})
        .get(session_id)
    )


def _active_session(registry: Dict[str, Any], project: str, agent: str) -> Optional[str]:
    return (
        (registry.get("projects") or {})
        .get(project, {})
        .get("active", {})
        .get(agent)
    )


def sender_is_active(registry: Dict[str, Any], sender: SenderContext) -> bool:
    record = _session_record(registry, sender.project, sender.sender_session_id)
    if not record:
        return False
    return (
        record.get("agent") == sender.from_agent
        and record.get("status") == "active"
        and _active_session(registry, sender.project, sender.from_agent) == sender.sender_session_id
    )


def resolve_route(
    *,
    sender: SenderContext,
    target: Address,
    kind: MessageKind,
    registry: Dict[str, Any],
) -> RoutingDecision:
    """Pure routing resolver for the refactor target model."""
    level = inbox_level(target)
    bucket = bucket_id(target)

    if kind == MessageKind.WORK and level == "agent":
        return RoutingDecision(
            RoutingStatus.REJECTED,
            None,
            None,
            reason="agent-level inbox is reserved for control/recovery traffic",
        )

    if kind == MessageKind.WORK and not sender_is_active(registry, sender):
        record = _session_record(registry, sender.project, sender.sender_session_id)
        detail = "sender session is not active"
        if record and record.get("status"):
            detail = "sender session is %s" % record.get("status")
        return RoutingDecision(
            RoutingStatus.REJECTED,
            None,
            None,
            reason=detail,
        )

    if isinstance(target, AgentInbox):
        return RoutingDecision(RoutingStatus.DELIVERED, bucket, "agent")

    if isinstance(target, ProjectInbox):
        return RoutingDecision(RoutingStatus.DELIVERED, bucket, "project", parent_project=target.project)

    if isinstance(target, SessionInbox):
        active = _active_session(registry, target.project, target.agent)
        record = _session_record(registry, target.project, target.session_id)
        if record and record.get("status") == "active" and active == target.session_id:
            return RoutingDecision(
                RoutingStatus.DELIVERED,
                target.session_id,
                "session",
                parent_project=target.project,
            )
        return RoutingDecision(
            RoutingStatus.ESCALATED,
            target.project,
            "project",
            reason="session_unavailable",
            parent_project=target.project,
            escalated_from=target.session_id,
        )

    return RoutingDecision(RoutingStatus.REJECTED, None, None, reason="unknown address")
