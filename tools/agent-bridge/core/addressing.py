from dataclasses import dataclass
from enum import Enum
from typing import Union


class MessageKind(str, Enum):
    WORK = "work"
    CONTROL = "control"
    RECOVERY = "recovery"
    RECEIPT = "receipt"


@dataclass(frozen=True)
class AgentInbox:
    agent: str


@dataclass(frozen=True)
class ProjectInbox:
    project: str
    agent: str


@dataclass(frozen=True)
class SessionInbox:
    project: str
    agent: str
    session_id: str


Address = Union[AgentInbox, ProjectInbox, SessionInbox]


@dataclass(frozen=True)
class SenderContext:
    from_agent: str
    sender_session_id: str
    project: str


def inbox_level(address: Address) -> str:
    if isinstance(address, AgentInbox):
        return "agent"
    if isinstance(address, ProjectInbox):
        return "project"
    return "session"


def bucket_id(address: Address) -> str:
    if isinstance(address, AgentInbox):
        return address.agent
    if isinstance(address, ProjectInbox):
        return address.project
    return address.session_id
