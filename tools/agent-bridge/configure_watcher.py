import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from project_identity import derive_project_identity


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("%s must contain a JSON object" % path)
    return data


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def load_registry(state_dir: Path) -> Dict[str, Any]:
    return read_json(
        state_dir.parent / "session.json",
        {
            "projects": {},
        },
    )


def candidate_session_ids(project_entry: Dict[str, Any], agent: str) -> Set[str]:
    session_ids: Set[str] = set()
    sessions = project_entry.get("sessions", {})
    if isinstance(sessions, dict):
        for session_id, record in sessions.items():
            if isinstance(record, dict) and record.get("agent") == agent:
                session_ids.add(str(session_id))
    active = project_entry.get("active", {})
    if isinstance(active, dict) and active.get(agent):
        session_ids.add(str(active[agent]))
    return session_ids


def is_managed_entry(
    entry: Dict[str, Any],
    *,
    agent: str,
    inbox: Path,
    project: str,
    project_session_ids: Set[str],
) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("agent") != agent:
        return False
    entry_inbox = entry.get("inbox")
    if not entry_inbox or Path(entry_inbox) != inbox:
        return False
    if entry.get("project") == project:
        return True
    kind = entry.get("kind")
    if kind in {"private", "rendezvous"} and entry.get("session_id") in project_session_ids.union({project}):
        return True
    session_id = entry.get("session_id")
    return session_id == project or session_id in project_session_ids


def merge_entry(existing: Optional[Dict[str, Any]], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(existing, dict):
        merged.update(existing)
    merged.update(updates)
    return merged


def build_private_entry(
    *,
    existing: Optional[Dict[str, Any]],
    agent: str,
    session_id: str,
    project: str,
    inbox: Path,
    command: Optional[str],
) -> Dict[str, Any]:
    # Claude has an in-process Monitor that reads the inbox directly — the watcher
    # should only toast (alert the user) and never consume messages on Claude's behalf.
    # on_message_command is only useful for agents without an in-process monitor (e.g. codex).
    entry: Dict[str, Any] = {
        "_comment": "%s private inbox watch for %s (notification only)" % (agent.capitalize(), project),
        "agent": agent,
        "kind": "private",
        "project": project,
        "session_id": session_id,
        "inbox": str(inbox),
        "on_message": existing.get("on_message", "toast") if isinstance(existing, dict) else "toast",
    }
    if command is not None:
        entry["on_message_command"] = command
    merged = merge_entry(existing, entry)
    if command is None:
        merged.pop("on_message_command", None)
    return merged


def build_rendezvous_entry(
    *,
    existing: Optional[Dict[str, Any]],
    agent: str,
    project: str,
    inbox: Path,
    command: Optional[str],
) -> Dict[str, Any]:
    # Same rationale: Claude reads its own inbox via Monitor; no consume command needed.
    entry: Dict[str, Any] = {
        "_comment": "Rendezvous/control-plane watch for %s (notification only)" % project,
        "agent": agent,
        "kind": "rendezvous",
        "project": project,
        "session_id": project,
        "inbox": str(inbox),
        "on_message": existing.get("on_message", "toast") if isinstance(existing, dict) else "toast",
    }
    if command is not None:
        entry["on_message_command"] = command
    merged = merge_entry(existing, entry)
    if command is None:
        merged.pop("on_message_command", None)
    return merged


def configure_watcher(
    *,
    config_path: Path,
    state_dir: Path,
    agent: str,
    project: Optional[str],
    cwd: Optional[str],
    python_executable: str,
) -> Dict[str, Any]:
    identity = derive_project_identity(cwd)
    project_name = project or identity["rendezvous"]
    inbox = state_dir / ("inbox-%s.jsonl" % agent)

    registry = load_registry(state_dir)
    projects = registry.get("projects", {})
    project_entry = projects.get(project_name, {}) if isinstance(projects, dict) else {}
    active = project_entry.get("active", {}) if isinstance(project_entry, dict) else {}
    active_session_id = active.get(agent) if isinstance(active, dict) else None
    project_session_ids = candidate_session_ids(project_entry, agent) if isinstance(project_entry, dict) else set()

    config = read_json(
        config_path,
        {
            "_comment": "agent-bridge watcher config",
            "sessions": [],
        },
    )
    sessions = config.get("sessions", [])
    if not isinstance(sessions, list):
        raise ValueError("%s sessions must be a JSON array" % config_path)

    kept_sessions: List[Any] = []
    managed_entries: List[Dict[str, Any]] = []
    existing_private: Optional[Dict[str, Any]] = None
    existing_rendezvous: Optional[Dict[str, Any]] = None

    for raw_entry in sessions:
        if not isinstance(raw_entry, dict):
            kept_sessions.append(raw_entry)
            continue
        if not is_managed_entry(
            raw_entry,
            agent=agent,
            inbox=inbox,
            project=project_name,
            project_session_ids=project_session_ids,
        ):
            kept_sessions.append(raw_entry)
            continue
        if raw_entry.get("session_id") == project_name or raw_entry.get("kind") == "rendezvous":
            if existing_rendezvous is None:
                existing_rendezvous = raw_entry
            continue
        if active_session_id and raw_entry.get("session_id") == active_session_id:
            if existing_private is None:
                existing_private = raw_entry
            continue
        if existing_private is None:
            existing_private = raw_entry

    # The watcher's job is notification only (toast) plus, for Codex, an
    # automated wake into the running Codex Desktop session via SendKeys.
    # Consumption is each agent's own responsibility:
    #   Claude: persistent Monitor -> check_inbox -> mark_read by id
    #   Codex:  wake_codex.ps1 opens the active Codex Desktop thread deeplink
    #           when CODEX_THREAD_ID is available, then synthesizes "check
    #           bridge inbox". Codex does its own non-destructive check +
    #           handle + mark_read pattern.
    # NEVER use consume_inbox.py from the watcher — it races with both agent
    # read paths and silently eats messages.
    wake_command: Optional[str] = None
    if agent == "codex":
        wake_script = Path(__file__).with_name("wake_codex.ps1")
        wake_args = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(wake_script),
        ]
        codex_thread_id = os.environ.get("CODEX_THREAD_ID")
        if codex_thread_id:
            wake_args.extend(["-ThreadId", codex_thread_id])
        wake_command = subprocess.list2cmdline(wake_args)

    if active_session_id:
        managed_entries.append(
            build_private_entry(
                existing=existing_private,
                agent=agent,
                session_id=str(active_session_id),
                project=project_name,
                inbox=inbox,
                command=wake_command,
            )
        )

    managed_entries.append(
        build_rendezvous_entry(
            existing=existing_rendezvous,
            agent=agent,
            project=project_name,
            inbox=inbox,
            command=wake_command,
        )
    )

    config["sessions"] = kept_sessions + managed_entries
    write_json(config_path, config)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Update watcher-config.json from agent-bridge session state")
    parser.add_argument("--config", required=True, help="Path to watcher-config.json")
    parser.add_argument("--state-dir", required=True, help="Bridge state directory")
    parser.add_argument("--agent", required=True, choices=("claude", "codex"))
    parser.add_argument("--project", help="Explicit project/rendezvous name; defaults to derived identity")
    parser.add_argument("--cwd", help="Workspace path used for project identity derivation")
    parser.add_argument("--python", default=sys.executable, help="Python executable used in on_message_command")
    args = parser.parse_args()

    config = configure_watcher(
        config_path=Path(args.config),
        state_dir=Path(args.state_dir),
        agent=args.agent,
        project=args.project,
        cwd=args.cwd,
        python_executable=args.python,
    )
    print(json.dumps(config, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
