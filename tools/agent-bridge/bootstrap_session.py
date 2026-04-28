import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_bridge import AgentBridge
from configure_watcher import configure_watcher
from core.paths import ensure_bridge_root_manifest, resolve_bridge_paths
from core.processes import acquire_singleton_lease, build_lease, command_line_hash, is_process_alive, lease_status, write_lease
from project_identity import derive_project_identity


def _state_dir_from_watcher_config(watcher_config: Path) -> Path:
    try:
        data = json.loads(watcher_config.read_text(encoding="utf-8"))
        sessions = data.get("sessions", [])
        if sessions:
            return Path(sessions[0]["inbox"]).parent
    except Exception:
        pass
    return watcher_config.parent / "state"


def ensure_watcher(watcher_config: Path, state_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Start the watcher daemon if it is not already running.

    Uses a role lease under state/locks plus watcher.pid compatibility marker.
    Returns a dict with status and PID for inclusion in bootstrap output.
    """
    pid_path = watcher_config.parent / "watcher.pid"
    watcher_script = Path(__file__).with_name("watcher.py")
    resolved_state_dir = state_dir or _state_dir_from_watcher_config(watcher_config)
    command = [sys.executable, str(watcher_script), "--config", str(watcher_config)]
    lease_path = resolved_state_dir / "locks" / "watcher.lock"

    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
            if is_process_alive(existing_pid):
                acquired = acquire_singleton_lease(
                    lease_path,
                    role="watcher",
                    command=command,
                    state_dir=resolved_state_dir,
                    pid=existing_pid,
                )
                return {
                    "status": "already_running",
                    "pid": existing_pid,
                    "lease": acquired.get("lease"),
                    "command_line_hash": command_line_hash(command),
                }
        except (ValueError, OSError):
            pass

    # Stale or missing PID — spawn a fresh watcher
    current_lease = lease_status(lease_path, expected_command=command)
    if current_lease.get("status") == "running":
        return {
            "status": "already_running",
            "pid": current_lease.get("pid"),
            "lease": current_lease.get("lease"),
            "command_line_hash": command_line_hash(command),
        }

    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    lease_record = build_lease(
        role="watcher",
        command=command,
        state_dir=resolved_state_dir,
        pid=proc.pid,
    )
    write_lease(lease_path, lease_record)
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    return {
        "status": "started",
        "pid": proc.pid,
        "lease": lease_record,
        "command_line_hash": command_line_hash(command),
    }


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
    start_watcher: bool = True,
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
        if start_watcher:
            watcher_process = ensure_watcher(watcher_config, state_dir=state_dir)
        else:
            watcher_process = {
                "status": "not_started",
                "reason": "start_watcher_false",
            }

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
    parser.add_argument("--bridge-root", help="Bridge root directory; preferred over --state-dir")
    parser.add_argument("--state-dir", help="Legacy bridge state directory")
    parser.add_argument("--agent", required=True, choices=("claude", "codex"))
    parser.add_argument("--cwd", help="Workspace path used for project identity derivation")
    parser.add_argument("--previous-session-id", help="Old same-agent session GUID to drain before takeover")
    parser.add_argument("--session-id", help="Optional new session GUID; default generates one")
    parser.add_argument("--project", help="Optional explicit rendezvous/project name")
    parser.add_argument("--handshake-retries", type=int, default=3)
    parser.add_argument("--watcher-config", help="Optional watcher-config.json to update for this active session")
    parser.add_argument(
        "--no-start-watcher",
        action="store_true",
        help="Update watcher config without spawning the watcher daemon",
    )
    args = parser.parse_args()
    paths = resolve_bridge_paths(
        bridge_root=Path(args.bridge_root) if args.bridge_root else None,
        state_dir=Path(args.state_dir) if args.state_dir else None,
    )
    if args.bridge_root:
        ensure_bridge_root_manifest(paths, reason="bootstrap")

    result = bootstrap(
        state_dir=paths.state_dir,
        agent=args.agent,
        cwd=args.cwd,
        previous_session_id=args.previous_session_id,
        session_id=args.session_id,
        project=args.project,
        handshake_retries=args.handshake_retries,
        watcher_config=Path(args.watcher_config) if args.watcher_config else (paths.watcher_config if args.bridge_root else None),
        start_watcher=not args.no_start_watcher,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
