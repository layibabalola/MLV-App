import dataclasses
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


DEFAULT_BRIDGE_DIRNAME = ".agent-bridge"
MOVED_TO_FILENAME = "MOVED_TO.json"
ROOT_MANIFEST_FILENAME = "bridge-root.json"
ROOT_MANIFEST_SCHEMA_VERSION = 1
MAX_MOVED_ROOT_HOPS = 5


class BridgeRootMovedError(RuntimeError):
    def __init__(self, root: Path, target: Path, manifest: Dict[str, Any], chain: Optional[List[Path]] = None):
        self.root = root
        self.target = target
        self.manifest = manifest
        self.chain = chain or [root, target]
        super().__init__("bridge root %s moved to %s" % (root, target))


@dataclasses.dataclass(frozen=True)
class BridgePaths:
    root: Path
    state_dir: Path
    session_registry: Path
    settings: Path
    watcher_config: Path
    watcher_pid: Path
    routing_rules: Path
    watch_mode_flag: Path
    reminder_log: Path
    locks_dir: Path
    server_pids_dir: Path
    manifest: Path
    moved_to: Path


def default_bridge_root(env: Optional[Mapping[str, str]] = None) -> Path:
    values = env if env is not None else os.environ
    user_profile = values.get("USERPROFILE") or str(Path.home())
    return Path(user_profile) / DEFAULT_BRIDGE_DIRNAME


def bridge_root_for_state_dir(state_dir: Path) -> Path:
    return Path(state_dir).parent


def state_dir_for_bridge_root(bridge_root: Path) -> Path:
    return Path(bridge_root) / "state"


def session_registry_path_for_state_dir(state_dir: Path) -> Path:
    return bridge_root_for_state_dir(state_dir) / "session.json"


def watcher_config_path_for_state_dir(state_dir: Path) -> Path:
    return bridge_root_for_state_dir(state_dir) / "watcher-config.json"


def watcher_pid_path_for_state_dir(state_dir: Path) -> Path:
    return bridge_root_for_state_dir(state_dir) / "watcher.pid"


def routing_rules_path_for_state_dir(state_dir: Path) -> Path:
    return bridge_root_for_state_dir(state_dir) / "routing-rules.json"


def bridge_paths_for_root(bridge_root: Path) -> BridgePaths:
    root = Path(bridge_root)
    state_dir = state_dir_for_bridge_root(root)
    return BridgePaths(
        root=root,
        state_dir=state_dir,
        session_registry=root / "session.json",
        settings=root / "settings.json",
        watcher_config=root / "watcher-config.json",
        watcher_pid=root / "watcher.pid",
        routing_rules=root / "routing-rules.json",
        watch_mode_flag=root / "bridge_watch_mode.flag",
        reminder_log=state_dir / "codex-bridge-reminder.log",
        locks_dir=state_dir / "locks",
        server_pids_dir=state_dir / "server-pids",
        manifest=root / ROOT_MANIFEST_FILENAME,
        moved_to=root / MOVED_TO_FILENAME,
    )


def _read_json_object(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("%s must contain a JSON object" % path)
    return data


def detect_moved_root(bridge_root: Path) -> Optional[Dict[str, Any]]:
    moved_to = Path(bridge_root) / MOVED_TO_FILENAME
    if not moved_to.exists():
        return None
    return _read_json_object(moved_to)


def _target_from_moved_manifest(root: Path, manifest: Dict[str, Any]) -> Path:
    value = manifest.get("active_root") or manifest.get("target_root") or manifest.get("moved_to")
    if not value or not isinstance(value, str):
        raise ValueError("%s does not name active_root, target_root, or moved_to" % (root / MOVED_TO_FILENAME))
    return Path(value)


def resolve_moved_root_chain(bridge_root: Path, *, max_hops: int = MAX_MOVED_ROOT_HOPS) -> Optional[Dict[str, Any]]:
    root = Path(bridge_root)
    first_manifest = detect_moved_root(root)
    if not first_manifest:
        return None

    current = root
    manifest = first_manifest
    chain = [current]
    seen = {str(current.resolve()) if current.exists() else str(current.absolute())}
    for _ in range(max_hops):
        target = _target_from_moved_manifest(current, manifest)
        chain.append(target)
        key = str(target.resolve()) if target.exists() else str(target.absolute())
        if key in seen:
            raise ValueError("MOVED_TO.json cycle detected: %s" % " -> ".join(str(path) for path in chain))
        seen.add(key)

        next_manifest = detect_moved_root(target)
        if not next_manifest:
            return {"target": target, "manifest": first_manifest, "chain": chain}
        current = target
        manifest = next_manifest

    raise ValueError("MOVED_TO.json chain exceeds %d hop(s): %s" % (max_hops, " -> ".join(str(path) for path in chain)))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_bridge_root_manifest(paths: BridgePaths, *, reason: str = "initialize") -> Dict[str, Any]:
    paths.root.mkdir(parents=True, exist_ok=True)
    if paths.manifest.exists():
        manifest = _read_json_object(paths.manifest)
        if manifest.get("schema_version", 0) > ROOT_MANIFEST_SCHEMA_VERSION:
            raise ValueError("%s schema_version is newer than this bridge supports" % paths.manifest)
        return manifest

    now = utc_now()
    manifest = {
        "schema_version": ROOT_MANIFEST_SCHEMA_VERSION,
        "root_id": str(uuid.uuid4()),
        "active_root": str(paths.root),
        "created_at": now,
        "updated_at": now,
        "migration_history": [
            {
                "source": None,
                "target": str(paths.root),
                "tool": "agent-bridge",
                "reason": reason,
                "timestamp": now,
            }
        ],
    }
    with paths.manifest.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def resolve_bridge_paths(
    *,
    bridge_root: Optional[Path] = None,
    state_dir: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    reject_moved: bool = True,
) -> BridgePaths:
    values = env if env is not None else os.environ
    if bridge_root is not None:
        root = Path(bridge_root)
    elif values.get("AGENT_BRIDGE_ROOT"):
        root = Path(str(values["AGENT_BRIDGE_ROOT"]))
    elif state_dir is not None:
        root = bridge_root_for_state_dir(Path(state_dir))
    else:
        root = default_bridge_root(values)

    if state_dir is not None and bridge_root is not None:
        expected_state = state_dir_for_bridge_root(root)
        if Path(state_dir) != expected_state:
            raise ValueError("--state-dir must equal <bridge-root>\\state when --bridge-root is provided")

    moved = resolve_moved_root_chain(root)
    if reject_moved and moved:
        raise BridgeRootMovedError(root, moved["target"], moved["manifest"], moved["chain"])

    return bridge_paths_for_root(root)
