from pathlib import Path


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
