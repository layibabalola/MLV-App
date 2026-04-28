import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_bridge import AgentBridge
from configure_watcher import configure_watcher
from project_identity import derive_project_identity


def _is_process_alive(pid: int) -> bool:
    """Return True if a process with the given PID is currently running."""
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == 259  # STILL_ACTIVE
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def ensure_watcher(watcher_config: Path) -> Dict[str, Any]:
    """Start the watcher daemon if it is not already running.

    Uses watcher.pid next to the config file as a single-instance lock.
    Returns a dict with status and PID for inclusion in bootstrap output.
    """
    pid_path = watcher_config.parent / "watcher.pid"
    watcher_script = Path(__file__).with_name("watcher.py")

    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
            if _is_process_alive(existing_pid):
                return {"status": "already_running", "pid": existing_pid}
        except (ValueError, OSError):
            pass

    # Stale or missing PID — spawn a fresh watcher
    proc = subprocess.Popen(
        [sys.executable, str(watcher_script), "--config", str(watcher_config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"status": "started", "pid": proc.pid}


def bootstrap(
    *,
    state_dir: Path,
    agent: str,
    cwd: Optional[str],
    previous_session_id: Optional[str],
    session_id: Optional[str],
    project: Optional[str],
    handshake_retries: int,
    watcher_config: Optional[Path] = None,
) -> Dict[str, Any]:
    bridge = AgentBridge(state_dir)
    identity = derive_project_identity(cwd)
    project_name = project or identity["rendezvous"]
    new_session = session_id or str(uuid.uuid4())
    peer_agent = "claude" if agent == "codex" else "codex"

    # activate_session auto-detects the previous same-agent session from the
    # registry, drains its unread messages BEFORE stamping superseded_at, and
    # returns them in data["drained_messages"].  This is atomic under the bridge
    # lock so there is no TOCTOU window between reading the registry and retiring.
    activation = bridge.activate_session(agent=agent, session_id=new_session, project=project_name)
    drained: List[Dict[str, Any]] = activation.data.get("drained_messages", []) if activation.ok else []
    peer_session = activation.data.get("active_peer_session") if activation.ok else None

    # Mark every drained message read immediately.  activate_session may promote
    # an old private-session message into the project bucket, so mark by id
    # without a session filter instead of guessing which bucket now owns it.
    for msg in drained:
        msg_id = msg.get("id")
        if msg_id:
            bridge.mark_read(agent=agent, message_id=msg_id, session_id=None)

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

    watcher = None
    watcher_process = None
    if watcher_config is not None:
        watcher = configure_watcher(
            config_path=watcher_config,
            state_dir=state_dir,
            agent=agent,
            project=project_name,
            cwd=cwd,
            python_executable=sys.executable,
        )
        watcher_process = ensure_watcher(watcher_config)

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
        "watcher": watcher,
        "watcher_process": watcher_process,
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
    parser.add_argument("--watcher-config", help="Optional watcher-config.json to update for this active session")
    args = parser.parse_args()

    result = bootstrap(
        state_dir=Path(args.state_dir),
        agent=args.agent,
        cwd=args.cwd,
        previous_session_id=args.previous_session_id,
        session_id=args.session_id,
        project=args.project,
        handshake_retries=args.handshake_retries,
        watcher_config=Path(args.watcher_config) if args.watcher_config else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
