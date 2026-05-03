import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.settings import load_settings
from core.paths import ensure_bridge_root_manifest, expand_path_arg, resolve_bridge_paths, session_registry_path_for_state_dir
from core.storage import STATE_SCHEMA_VERSION
from project_identity import derive_project_identity


PARENT_THREAD_ID_KEY = "codex_parent_thread_id"
PARENT_THREAD_PROVENANCE_KEY = "codex_parent_thread_provenance"
PARENT_THREAD_ALLOWED_PROVENANCE = {"parent", "bootstrap-parent"}


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("%s must contain a JSON object" % path)
    return data


def write_json(path: Path, value: Dict[str, Any]) -> None:
    if path.exists():
        try:
            current = read_json(path, {})
        except Exception:
            current = {}
        if (
            current.get(PARENT_THREAD_ID_KEY)
            and value.get(PARENT_THREAD_PROVENANCE_KEY) not in PARENT_THREAD_ALLOWED_PROVENANCE
        ):
            value[PARENT_THREAD_ID_KEY] = current.get(PARENT_THREAD_ID_KEY)
            value[PARENT_THREAD_PROVENANCE_KEY] = current.get(PARENT_THREAD_PROVENANCE_KEY, "parent")
    path.parent.mkdir(parents=True, exist_ok=True)
    value.setdefault("schema_version", STATE_SCHEMA_VERSION)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def load_registry(state_dir: Path) -> Dict[str, Any]:
    return read_json(
        session_registry_path_for_state_dir(state_dir),
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


def resolve_git_worktree_root(cwd: Optional[str], fallback: str) -> str:
    start = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, TypeError):
        return fallback
    root = (completed.stdout or "").strip()
    return root or fallback


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
    command_template: Optional[List[str]] = None,
) -> Dict[str, Any]:
    # Claude has an in-process Monitor that reads the inbox directly — the watcher
    # should only toast (alert the user) and never consume messages on Claude's behalf.
    # on_message_command is only useful for agents without an in-process monitor (e.g. codex).
    entry: Dict[str, Any] = {
        "_comment": "%s private inbox watch for %s (notification only)" % (agent.capitalize(), project),
        "agent": agent,
        "kind": "private",
        "project": project,
        "session_id_source": "active_session",
        "session_id": session_id,
        "inbox": str(inbox),
        "on_message": existing.get("on_message", "toast") if isinstance(existing, dict) else "toast",
    }
    if command is not None:
        entry["on_message_command"] = command
    if command_template is not None:
        entry["on_message_command_template"] = command_template
    merged = merge_entry(existing, entry)
    if command is None:
        merged.pop("on_message_command", None)
    if command_template is None:
        merged.pop("on_message_command_template", None)
    if command is not None or command_template is not None:
        merged.pop("wake_disabled_reason", None)
    elif agent == "claude":
        merged["wake_disabled_reason"] = (
            "Claude wake remains Monitor-owned: no verified thread-addressable "
            "Claude Desktop deeplink exists for safe SendKeys."
        )
    return merged


def build_rendezvous_entry(
    *,
    existing: Optional[Dict[str, Any]],
    agent: str,
    project: str,
    inbox: Path,
    command: Optional[str],
    command_template: Optional[List[str]] = None,
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
    if command_template is not None:
        entry["on_message_command_template"] = command_template
    merged = merge_entry(existing, entry)
    if command is None:
        merged.pop("on_message_command", None)
    if command_template is None:
        merged.pop("on_message_command_template", None)
    if command is not None or command_template is not None:
        merged.pop("wake_disabled_reason", None)
    elif agent == "claude":
        merged["wake_disabled_reason"] = (
            "Claude wake remains Monitor-owned: no verified thread-addressable "
            "Claude Desktop deeplink exists for safe SendKeys."
        )
    return merged


def configure_watcher(
    *,
    config_path: Path,
    state_dir: Path,
    agent: str,
    project: Optional[str],
    cwd: Optional[str],
    python_executable: str,
    parent_thread_id: Optional[str] = None,
    parent_thread_provenance: Optional[str] = None,
) -> Dict[str, Any]:
    identity = derive_project_identity(cwd)
    project_name = project or identity["rendezvous"]
    inbox = state_dir / ("inbox-%s.jsonl" % agent)
    settings = load_settings(state_dir)

    registry = load_registry(state_dir)
    projects = registry.get("projects", {})
    project_entry = projects.get(project_name, {}) if isinstance(projects, dict) else {}
    active = project_entry.get("active", {}) if isinstance(project_entry, dict) else {}
    active_session_id = active.get(agent) if isinstance(active, dict) else None
    project_session_ids = candidate_session_ids(project_entry, agent) if isinstance(project_entry, dict) else set()

    config = read_json(
        config_path,
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "_comment": "agent-bridge watcher config",
            "sessions": [],
        },
    )
    config.setdefault("schema_version", STATE_SCHEMA_VERSION)
    config["canonical_root"] = str(identity["canonical_root"])
    # Commit-change monitoring must follow the active checkout/worktree, while
    # canonical_root remains the stable rendezvous identity across worktrees.
    config["repo_root"] = resolve_git_worktree_root(cwd, str(identity["canonical_root"]))
    if agent == "codex" and parent_thread_id:
        if parent_thread_provenance not in PARENT_THREAD_ALLOWED_PROVENANCE:
            raise ValueError("parent_thread_id may only be written by an approved parent provenance")
        config[PARENT_THREAD_ID_KEY] = parent_thread_id
        config[PARENT_THREAD_PROVENANCE_KEY] = parent_thread_provenance
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

    # The watcher's default job is notification only (toast).  App Server wake
    # is the non-composer path; Codex SendKeys wake remains opt-in because it
    # can type into whichever Codex chat is active if target verification is
    # missing, deeplink navigation fails, or focus races with the user.
    # Consumption is each agent's own responsibility:
    #   Claude: persistent Monitor -> check_inbox -> mark_read by id
    #   Codex:  codex_app_server_wake.py starts a bridge-owned app-server,
    #           resumes the protected parent thread id, and starts a fixed
    #           bridge-inbox-check turn. The visible Desktop nudge is explicit
    #           only through targeted_sendkeys.
    # NEVER use consume_inbox.py from the watcher — it races with both agent
    # read paths and silently eats messages.
    wake_command: Optional[str] = None
    wake_command_template: Optional[List[str]] = None
    if agent == "codex" and settings.wake_provider in {"sendkeys", "targeted_sendkeys"}:
        wake_script = Path(__file__).with_name("wake_codex.ps1")
        wake_command_template = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(wake_script),
            "-IdleThresholdSeconds", str(settings.wake_idle_threshold_seconds),
            "-MaxWaitSeconds", str(settings.wake_max_wait_seconds),
            "-StateDir", str(state_dir),
            "-LockFile", str(state_dir.parent / "wake_codex.lock"),
            "-ThreadId", "{desktop_thread_id}",
            "-ExpectedProjectToken", "{project}",
        ]
        if settings.wake_provider == "targeted_sendkeys":
            wake_command_template.extend(
                [
                    # Run inner wake directly — avoids spawning a hidden child process
                    # which has no Win32 input queue and blocks AttachThreadInput.
                    "-RunInnerWake",
                    "-RequireThreadId",
                    "-RequireConstantMessage",
                    "-VerifyTargetTwice",
                    "-VerifyTargetGapMilliseconds", "50",
                    "-MaxPreSendRaceMilliseconds", "500",
                    "-PostTypingVerify",
                    "-WarnOnTitleMismatch",
                    "-ProtectForegroundCodexThread",
                ]
            )
    elif agent == "codex" and settings.wake_provider in {"app_server", "app_server_then_redraw"}:
        wake_script = Path(__file__).with_name("codex_app_server_wake.py")
        wake_command_template = [
            python_executable,
            str(wake_script),
            "--thread-id", "{desktop_thread_id}",
            "--session-id", "{session_id}",
            "--project", "{project}",
            "--repo-root", str(identity["canonical_root"]),
            "--timeout-seconds", str(max(30, min(settings.wake_max_wait_seconds, 85))),
        ]
        if settings.wake_provider == "app_server_then_redraw":
            wake_command_template.append("--redraw-deeplink")

    if active_session_id:
        managed_entries.append(
            build_private_entry(
                existing=existing_private,
                agent=agent,
                session_id=str(active_session_id),
                project=project_name,
                inbox=inbox,
                command=wake_command,
                command_template=wake_command_template,
            )
        )

    managed_entries.append(
        build_rendezvous_entry(
            existing=existing_rendezvous,
            agent=agent,
            project=project_name,
            inbox=inbox,
            command=wake_command,
            command_template=wake_command_template,
        )
    )

    config["sessions"] = kept_sessions + managed_entries
    write_json(config_path, config)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Update watcher-config.json from agent-bridge session state")
    parser.add_argument("--bridge-root", help="Bridge root directory; preferred over --state-dir")
    parser.add_argument("--config", help="Path to watcher-config.json")
    parser.add_argument("--state-dir", help="Legacy bridge state directory")
    parser.add_argument("--agent", required=True, choices=("claude", "codex"))
    parser.add_argument("--project", help="Explicit project/rendezvous name; defaults to derived identity")
    parser.add_argument("--cwd", help="Workspace path used for project identity derivation")
    parser.add_argument("--python", default=sys.executable, help="Python executable used in on_message_command")
    parser.add_argument("--parent-thread-id", help="Protected Codex parent thread id used for wake targeting")
    parser.add_argument(
        "--parent-thread-provenance",
        choices=sorted(PARENT_THREAD_ALLOWED_PROVENANCE),
        help="Proof that --parent-thread-id came from the controlling parent session",
    )
    args = parser.parse_args()
    paths = resolve_bridge_paths(
        bridge_root=expand_path_arg(args.bridge_root) if args.bridge_root else None,
        state_dir=expand_path_arg(args.state_dir) if args.state_dir else None,
    )
    if args.bridge_root:
        ensure_bridge_root_manifest(paths, reason="configure_watcher")

    config = configure_watcher(
        config_path=expand_path_arg(args.config) if args.config else paths.watcher_config,
        state_dir=paths.state_dir,
        agent=args.agent,
        project=args.project,
        cwd=args.cwd,
        python_executable=args.python,
        parent_thread_id=args.parent_thread_id,
        parent_thread_provenance=args.parent_thread_provenance,
    )
    print(json.dumps(config, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
