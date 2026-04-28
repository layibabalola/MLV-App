import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_bridge import AgentBridge
from bootstrap_session import bootstrap, ensure_watcher
from configure_watcher import configure_watcher
from core.paths import ensure_bridge_root_manifest, resolve_bridge_paths, watcher_config_path_for_state_dir
from project_identity import derive_project_identity


def _peek_count(bridge: AgentBridge, agent: str, session_id: Optional[str]) -> int:
    if not session_id:
        return 0
    result = bridge.peek_inbox(agent, session_id=session_id)
    if not result.ok:
        return 0
    if result.status == "empty":
        return 0
    return int((result.data or {}).get("count", 0))


def _load_watcher_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def inspect_bridge_runtime(
    *,
    state_dir: Path,
    agent: str,
    cwd: Optional[str],
    project: Optional[str] = None,
    watcher_config: Optional[Path] = None,
) -> Dict[str, Any]:
    bridge = AgentBridge(state_dir)
    identity = derive_project_identity(cwd)
    project_name = project or identity["rendezvous"]
    registry = bridge.session_registry_view()
    project_entry = {}
    projects = registry.get("projects", {})
    if isinstance(projects, dict):
        project_entry = projects.get(project_name, {}) or {}
    active = project_entry.get("active", {}) if isinstance(project_entry, dict) else {}
    sessions = project_entry.get("sessions", {}) if isinstance(project_entry, dict) else {}
    active_session_id = active.get(agent) if isinstance(active, dict) else None
    active_session_record = sessions.get(active_session_id, {}) if isinstance(sessions, dict) and active_session_id else {}
    active_session_status = active_session_record.get("status") if isinstance(active_session_record, dict) else None

    process_status = bridge.bridge_process_status()
    process_data = process_status.data if process_status.ok else {}
    watcher_status = process_data.get("watcher", {}) if isinstance(process_data, dict) else {}

    watcher_config_path = watcher_config or watcher_config_path_for_state_dir(state_dir)
    watcher_data = _load_watcher_config(watcher_config_path)
    watcher_sessions = watcher_data.get("sessions", []) if isinstance(watcher_data.get("sessions", []), list) else []
    private_entry_present = False
    rendezvous_entry_present = False
    for entry in watcher_sessions:
        if not isinstance(entry, dict):
            continue
        if entry.get("agent") != agent:
            continue
        kind = entry.get("kind")
        session_id = entry.get("session_id")
        if kind == "private" and active_session_id and session_id == active_session_id:
            private_entry_present = True
        if kind == "rendezvous" and session_id == project_name:
            rendezvous_entry_present = True

    private_unread = _peek_count(bridge, agent, active_session_id)
    project_unread = _peek_count(bridge, agent, project_name)

    if not active_session_id:
        bridge_state = "UNBOOTSTRAPPED"
    elif active_session_status and active_session_status != "active":
        bridge_state = "SUPERSEDED"
    elif watcher_status.get("running") and private_entry_present and rendezvous_entry_present:
        bridge_state = "WATCHING"
    else:
        bridge_state = "BOOTSTRAPPED_NOT_WATCHING"

    return {
        "bridge_state": bridge_state,
        "identity": identity,
        "project": project_name,
        "agent": agent,
        "active_session_id": active_session_id,
        "active_session_status": active_session_status,
        "watcher": {
            "running": bool(watcher_status.get("running")),
            "pid": watcher_status.get("pid"),
            "stale": bool(watcher_status.get("stale")),
            "config_path": str(watcher_config_path),
            "private_entry_present": private_entry_present,
            "rendezvous_entry_present": rendezvous_entry_present,
        },
        "unread": {
            "private": private_unread,
            "project": project_unread,
            "total": private_unread + project_unread,
        },
    }


def recover_bridge_session(
    *,
    state_dir: Path,
    agent: str,
    cwd: Optional[str],
    project: Optional[str] = None,
    watcher_config: Optional[Path] = None,
    start_watcher: bool = True,
    force_takeover: bool = False,
) -> Dict[str, Any]:
    before = inspect_bridge_runtime(
        state_dir=state_dir,
        agent=agent,
        cwd=cwd,
        project=project,
        watcher_config=watcher_config,
    )
    project_name = before["project"]
    watcher_config_path = watcher_config or watcher_config_path_for_state_dir(state_dir)

    bootstrap_result: Optional[Dict[str, Any]] = None
    watcher_update: Optional[Dict[str, Any]] = None
    watcher_process: Optional[Dict[str, Any]] = None

    should_bootstrap = force_takeover or before["bridge_state"] in {"UNBOOTSTRAPPED", "SUPERSEDED"}
    if should_bootstrap:
        bootstrap_result = bootstrap(
            state_dir=state_dir,
            agent=agent,
            cwd=cwd,
            previous_session_id=before.get("active_session_id"),
            session_id=None,
            project=project_name,
            handshake_retries=3,
            watcher_config=watcher_config_path,
            start_watcher=start_watcher,
        )
    else:
        watcher_update = configure_watcher(
            config_path=watcher_config_path,
            state_dir=state_dir,
            agent=agent,
            project=project_name,
            cwd=cwd,
            python_executable="py",
        )
        if start_watcher:
            watcher_process = ensure_watcher(watcher_config_path, state_dir=state_dir)

    after = inspect_bridge_runtime(
        state_dir=state_dir,
        agent=agent,
        cwd=cwd,
        project=project_name,
        watcher_config=watcher_config_path,
    )

    if bootstrap_result is not None:
        status = "bootstrapped"
        message = "Bootstrapped %s bridge session for %s." % (agent, project_name)
    elif before["bridge_state"] != "WATCHING" and after["bridge_state"] == "WATCHING":
        status = "recovered"
        message = "Recovered watcher/config state for %s in %s." % (agent, project_name)
    elif after["bridge_state"] == "WATCHING":
        status = "already_healthy"
        message = "Bridge session for %s in %s is already healthy." % (agent, project_name)
    else:
        status = "needs_user_attention"
        message = "Bridge for %s in %s still needs user attention." % (agent, project_name)

    return {
        "ok": True,
        "status": status,
        "message": message,
        "before": before,
        "after": after,
        "bootstrap": bootstrap_result,
        "watcher_config_updated": watcher_update is not None,
        "watcher_config": watcher_update,
        "watcher_process": watcher_process,
        "suggested_next_actions": _suggested_next_actions(after, start_watcher=start_watcher),
    }


def _suggested_next_actions(summary: Dict[str, Any], *, start_watcher: bool) -> List[str]:
    actions: List[str] = []
    state = summary.get("bridge_state")
    watcher = summary.get("watcher", {})
    unread_total = ((summary.get("unread") or {}).get("total") or 0)
    if state == "UNBOOTSTRAPPED":
        actions.append("Run bootstrap_session.py for this project before normal bridge work.")
    elif state == "BOOTSTRAPPED_NOT_WATCHING":
        if not start_watcher:
            actions.append("Re-run without --no-start-watcher if you want the watcher daemon armed.")
        else:
            actions.append("Watcher/config is not fully armed yet; inspect watcher-config.json and watcher lease state.")
    elif state == "SUPERSEDED":
        actions.append("This session appears superseded; bootstrap a fresh session takeover before sending new bridge traffic.")
    if unread_total:
        actions.append("Unread bridge backlog detected: run a non-destructive inbox check next.")
    if watcher.get("running"):
        actions.append("Watcher is running, but continuous monitoring still requires a live wait_inbox loop.")
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover or verify Codex/Claude bridge session health")
    parser.add_argument("--bridge-root", help="Bridge root directory; preferred over --state-dir")
    parser.add_argument("--state-dir", help="Legacy bridge state directory")
    parser.add_argument("--agent", required=True, choices=("claude", "codex"))
    parser.add_argument("--cwd", help="Workspace path used for project identity derivation")
    parser.add_argument("--project", help="Optional explicit rendezvous/project name")
    parser.add_argument("--watcher-config", help="Path to watcher-config.json")
    parser.add_argument("--no-start-watcher", action="store_true", help="Inspect/update config without spawning watcher")
    parser.add_argument(
        "--force-takeover",
        action="store_true",
        help="Bootstrap a fresh session even if one already exists; this supersedes the current session for this agent.",
    )
    args = parser.parse_args()
    paths = resolve_bridge_paths(
        bridge_root=Path(args.bridge_root) if args.bridge_root else None,
        state_dir=Path(args.state_dir) if args.state_dir else None,
    )
    if args.bridge_root:
        ensure_bridge_root_manifest(paths, reason="recover_bridge_session")

    result = recover_bridge_session(
        state_dir=paths.state_dir,
        agent=args.agent,
        cwd=args.cwd,
        project=args.project,
        watcher_config=Path(args.watcher_config) if args.watcher_config else paths.watcher_config,
        start_watcher=not args.no_start_watcher,
        force_takeover=args.force_takeover,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
