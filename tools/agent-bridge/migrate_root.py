import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import (
    ROOT_MANIFEST_SCHEMA_VERSION,
    bridge_paths_for_root,
    ensure_bridge_root_manifest,
    utc_now,
)
from core.processes import is_process_alive
from core.storage import append_jsonl, read_json, write_json


def _backup_stamp() -> str:
    return utc_now().replace(":", "").replace("-", "")


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path.absolute()).lower()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _live_processes(source_root: Path) -> List[Dict[str, Any]]:
    paths = bridge_paths_for_root(source_root)
    live: List[Dict[str, Any]] = []

    if paths.watcher_pid.exists():
        try:
            pid = int(paths.watcher_pid.read_text(encoding="utf-8").strip())
            if is_process_alive(pid):
                live.append({"kind": "watcher", "pid": pid, "path": str(paths.watcher_pid)})
        except (OSError, ValueError):
            pass

    if paths.server_pids_dir.exists():
        for marker in sorted(paths.server_pids_dir.glob("server-*.pid")):
            try:
                pid = int(marker.read_text(encoding="utf-8").strip())
                if is_process_alive(pid):
                    live.append({"kind": "mcp_server", "pid": pid, "path": str(marker)})
            except (OSError, ValueError):
                continue
    return live


def _rewrite_watcher_config(target_root: Path, source_root: Path) -> Dict[str, Any]:
    paths = bridge_paths_for_root(target_root)
    if not paths.watcher_config.exists():
        return {"changed": False, "rewritten": 0}

    config = read_json(paths.watcher_config, {})
    sessions = config.get("sessions", [])
    if not isinstance(sessions, list):
        raise ValueError("%s sessions must be a list" % paths.watcher_config)

    rewritten = 0
    source_state = bridge_paths_for_root(source_root).state_dir
    target_state = paths.state_dir
    for entry in sessions:
        if not isinstance(entry, dict) or not entry.get("inbox"):
            continue
        inbox = Path(str(entry["inbox"]))
        if not _is_under(inbox, source_state):
            continue
        entry["inbox"] = str(target_state / inbox.resolve().relative_to(source_state.resolve()))
        rewritten += 1

    if rewritten:
        write_json(paths.watcher_config, config)
    return {"changed": bool(rewritten), "rewritten": rewritten}


def _write_target_manifest(target_root: Path, source_root: Path, *, reason: str) -> Dict[str, Any]:
    paths = bridge_paths_for_root(target_root)
    manifest = ensure_bridge_root_manifest(paths, reason="migration-target")
    history = list(manifest.get("migration_history") or [])
    event = {
        "source": str(source_root),
        "target": str(target_root),
        "tool": "migrate_root.py",
        "reason": reason,
        "timestamp": utc_now(),
    }
    history.append(event)
    manifest.update(
        {
            "schema_version": ROOT_MANIFEST_SCHEMA_VERSION,
            "active_root": str(target_root),
            "updated_at": event["timestamp"],
            "migration_history": history,
        }
    )
    write_json(paths.manifest, manifest)
    return manifest


def _write_moved_to(source_root: Path, target_root: Path, target_manifest: Dict[str, Any]) -> Dict[str, Any]:
    paths = bridge_paths_for_root(source_root)
    moved = {
        "schema_version": 1,
        "active_root": str(target_root),
        "created_at": utc_now(),
        "migration_history": target_manifest.get("migration_history", []),
    }
    write_json(paths.moved_to, moved)
    return moved


def migrate_root(
    *,
    source_root: Path,
    target_root: Path,
    apply: bool = False,
    force_while_running: bool = False,
    reason: str = "manual",
) -> Dict[str, Any]:
    source = Path(source_root)
    target = Path(target_root)
    if not source.exists():
        return {"ok": False, "status": "rejected", "reason": "source root does not exist", "source_root": str(source)}
    if _path_key(source) == _path_key(target):
        return {"ok": False, "status": "rejected", "reason": "source and target roots are the same"}
    if _is_under(target, source):
        return {"ok": False, "status": "rejected", "reason": "target root must not be inside source root"}
    if target.exists() and any(target.iterdir()):
        return {"ok": False, "status": "rejected", "reason": "target root exists and is not empty", "target_root": str(target)}

    live = _live_processes(source)
    if live and not force_while_running:
        return {
            "ok": False,
            "status": "live_processes",
            "reason": "live bridge processes detected; stop them or pass --force-while-running",
            "live_processes": live,
        }

    plan = {
        "source_root": str(source),
        "target_root": str(target),
        "apply": apply,
        "force_while_running": force_while_running,
        "live_processes": live,
        "steps": [
            "backup_source_root",
            "copy_root",
            "rewrite_watcher_config",
            "write_target_manifest",
            "write_source_moved_to",
            "write_migration_audit",
        ],
    }
    if not apply:
        return {"ok": True, "status": "dry_run", "plan": plan}

    backup_root = source.parent / ("%s.backup-%s" % (source.name, _backup_stamp()))
    shutil.copytree(source, backup_root)
    shutil.copytree(source, target)
    watcher_rewrite = _rewrite_watcher_config(target, source)
    manifest = _write_target_manifest(target, source, reason=reason)
    moved = _write_moved_to(source, target, manifest)
    audit = {
        "id": "migration-%s" % _backup_stamp(),
        "timestamp": utc_now(),
        "action": "migrate_root",
        "source_root": str(source),
        "target_root": str(target),
        "backup_root": str(backup_root),
        "watcher_config": watcher_rewrite,
        "force_while_running": force_while_running,
        "accepted": True,
    }
    append_jsonl(bridge_paths_for_root(target).state_dir / "messages.jsonl", audit)
    return {
        "ok": True,
        "status": "migrated",
        "plan": plan,
        "backup_root": str(backup_root),
        "watcher_config": watcher_rewrite,
        "manifest": manifest,
        "moved_to": moved,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run or apply an agent-bridge root migration")
    parser.add_argument("--source-root", required=True, help="Current bridge root")
    parser.add_argument("--target-root", required=True, help="New bridge root")
    parser.add_argument("--apply", action="store_true", help="Mutate state. Without this flag the command only plans.")
    parser.add_argument(
        "--force-while-running",
        action="store_true",
        help="Allow migration while live watcher/MCP markers are running. This can create split-brain until clients restart.",
    )
    parser.add_argument("--reason", default="manual", help="Migration reason recorded in bridge-root.json")
    args = parser.parse_args()

    result = migrate_root(
        source_root=Path(args.source_root),
        target_root=Path(args.target_root),
        apply=args.apply,
        force_while_running=args.force_while_running,
        reason=args.reason,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
