import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from agent_bridge import AgentBridge


def consume(state_dir: Path, agent: str, session_id: str, mark_read: bool = True) -> Dict[str, Any]:
    bridge = AgentBridge(state_dir)
    result = bridge.peek_inbox(agent, session_id=session_id)
    if not result.ok or result.status == "empty":
        return {
            "ok": result.ok,
            "status": result.status,
            "message": result.message,
            "should_halt": False,
            "control_events": [],
            "acked_message_ids": [],
            "messages": [],
        }

    messages: List[Dict[str, Any]] = result.data.get("messages", [])
    should_halt = False
    halt_reason = None
    control_events: List[Dict[str, Any]] = []
    acked_message_ids: List[str] = []
    for msg in messages:
        if msg.get("marker_variant") != "control":
            continue
        control_type = (msg.get("control_type") or "").upper()
        body = (msg.get("body") or "")
        summary = (msg.get("delivered_message") or "")
        event = {
            "id": msg.get("id"),
            "type": control_type,
            "body": body,
            "summary": summary,
        }
        control_events.append(event)
        lowered = (body + "\n" + summary).casefold()
        if control_type == "SESSION_UPDATE":
            if "superseded" in lowered:
                should_halt = True
                halt_reason = "superseded"
            elif "session ending" in lowered or "teardown" in lowered or "has ended" in lowered:
                should_halt = True
                halt_reason = "ended"

    if mark_read:
        for msg in messages:
            message_id = msg.get("id")
            if not message_id:
                continue
            mark = bridge.mark_read(agent, message_id, session_id=session_id)
            if mark.ok:
                acked_message_ids.append(message_id)

    return {
        "ok": result.ok,
        "status": result.status,
        "message": result.message,
        "should_halt": should_halt,
        "halt_reason": halt_reason,
        "control_events": control_events,
        "acked_message_ids": acked_message_ids,
        "messages": messages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Consume one bridge inbox session and detect halt conditions")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--agent", required=True, choices=("claude", "codex"))
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--peek", action="store_true", help="Read without marking messages read")
    args = parser.parse_args()

    result = consume(
        state_dir=Path(args.state_dir),
        agent=args.agent,
        session_id=args.session_id,
        mark_read=not args.peek,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
