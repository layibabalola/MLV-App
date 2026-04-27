import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_bridge import AgentBridge
from project_identity import derive_project_identity


def bootstrap(
    *,
    state_dir: Path,
    agent: str,
    cwd: Optional[str],
    previous_session_id: Optional[str],
    session_id: Optional[str],
    project: Optional[str],
    handshake_retries: int,
) -> Dict[str, Any]:
    bridge = AgentBridge(state_dir)
    identity = derive_project_identity(cwd)
    project_name = project or identity["rendezvous"]
    new_session = session_id or str(uuid.uuid4())
    peer_agent = "claude" if agent == "codex" else "codex"

    drained: List[Dict[str, Any]] = []
    if previous_session_id and previous_session_id != new_session:
        drained_result = bridge.check_inbox(agent, session_id=previous_session_id, mark_read=True)
        if drained_result.ok and drained_result.status == "messages":
            drained = drained_result.data.get("messages", [])

    activation = bridge.activate_session(agent=agent, session_id=new_session, project=project_name)
    peer_session = activation.data.get("active_peer_session") if activation.ok else None

    handshake = None
    delays = [2, 4, 8]
    for attempt in range(handshake_retries):
        handshake = bridge.send_control_message(
            from_agent=agent,
            to_agent=peer_agent,
            control_type="HANDSHAKE",
            summary="%s handshake for %s" % (agent, project_name),
            body=json.dumps(
                {
                    "agent": agent,
                    "session_id": new_session,
                    "project": project_name,
                    "peer_session_hint": peer_session,
                },
                sort_keys=True,
            ),
            session_id=project_name,
            replace_existing_control=True,
        )
        if handshake.ok:
            break
        if attempt < handshake_retries - 1:
            time.sleep(delays[min(attempt, len(delays) - 1)])

    return {
        "identity": identity,
        "project": project_name,
        "agent": agent,
        "session_id": new_session,
        "peer_agent": peer_agent,
        "peer_session_hint": peer_session,
        "previous_session_id": previous_session_id,
        "drained_previous_messages": drained,
        "activation": {
            "ok": activation.ok,
            "status": activation.status,
            "message": activation.message,
            "data": activation.data,
        },
        "handshake": None
        if handshake is None
        else {
            "ok": handshake.ok,
            "status": handshake.status,
            "message": handshake.message,
            "data": handshake.data,
            "attempts": attempt + 1,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an agent-bridge session takeover")
    parser.add_argument("--state-dir", required=True, help="Bridge state directory")
    parser.add_argument("--agent", required=True, choices=("claude", "codex"))
    parser.add_argument("--cwd", help="Workspace path used for project identity derivation")
    parser.add_argument("--previous-session-id", help="Old same-agent session GUID to drain before takeover")
    parser.add_argument("--session-id", help="Optional new session GUID; default generates one")
    parser.add_argument("--project", help="Optional explicit rendezvous/project name")
    parser.add_argument("--handshake-retries", type=int, default=3)
    args = parser.parse_args()

    result = bootstrap(
        state_dir=Path(args.state_dir),
        agent=args.agent,
        cwd=args.cwd,
        previous_session_id=args.previous_session_id,
        session_id=args.session_id,
        project=args.project,
        handshake_retries=args.handshake_retries,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
