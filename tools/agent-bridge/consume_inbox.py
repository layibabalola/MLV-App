import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from agent_bridge import AgentBridge


def consume(state_dir: Path, agent: str, session_id: str, mark_read: bool = True) -> Dict[str, Any]:
    bridge = AgentBridge(state_dir)
    result = bridge.check_inbox(agent, session_id=session_id, mark_read=mark_read)
    if not result.ok or result.status == "empty":
        return {
            "ok": result.ok,
            "status": result.status,
            "message": result.message,
            "should_halt": False,
            "messages": [],
        }

    messages: List[Dict[str, Any]] = result.data.get("messages", [])
    should_halt = False
    halt_reason = None
    for msg in messages:
        if msg.get("marker_variant") == "control" and msg.get("control_type") == "SESSION_UPDATE":
            body = (msg.get("body") or "").casefold()
            summary = (msg.get("delivered_message") or "").casefold()
            if "superseded" in body or "superseded" in summary:
                should_halt = True
                halt_reason = "superseded"
                break
            if "session ending" in body or "teardown" in body:
                should_halt = True
                halt_reason = "ended"
                break

    return {
        "ok": result.ok,
        "status": result.status,
        "message": result.message,
        "should_halt": should_halt,
        "halt_reason": halt_reason,
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
