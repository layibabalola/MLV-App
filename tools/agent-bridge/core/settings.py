import dataclasses
import json
from pathlib import Path
from typing import Any, Dict, Optional


@dataclasses.dataclass(frozen=True)
class BridgeSettings:
    toast_expiry_minutes: int = 5
    toast_max_in_tray: int = 10
    wake_idle_threshold_seconds: int = 5
    wake_max_wait_seconds: int = 60
    poll_interval_seconds: int = 2
    compact_interval_hours: int = 6
    audit_log_retention_days: int = 90
    inbox_read_retention_days: int = 7
    toasts_enabled: bool = True
    routing_rules_enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


DEFAULT_SETTINGS = BridgeSettings()

_BOUNDS = {
    "toast_expiry_minutes": (1, 60),
    "toast_max_in_tray": (1, 50),
    "wake_idle_threshold_seconds": (0, 60),
    "wake_max_wait_seconds": (1, 3600),
    "poll_interval_seconds": (1, 60),
    "compact_interval_hours": (1, 168),
    "audit_log_retention_days": (1, 3650),
    "inbox_read_retention_days": (1, 3650),
}

_BOOL_FIELDS = {"toasts_enabled", "routing_rules_enabled"}
_KNOWN_FIELDS = set(DEFAULT_SETTINGS.to_dict())


def settings_path_for_state_dir(state_dir: Path) -> Path:
    return Path(state_dir).parent / "settings.json"


def _validate_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer" % name)
    low, high = _BOUNDS[name]
    if value < low or value > high:
        raise ValueError("%s must be between %d and %d" % (name, low, high))
    return value


def _validate_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("%s must be true or false" % name)
    return value


def load_settings(state_dir: Path, settings_path: Optional[Path] = None) -> BridgeSettings:
    path = Path(settings_path) if settings_path else settings_path_for_state_dir(Path(state_dir))
    values = DEFAULT_SETTINGS.to_dict()
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError("%s must contain a JSON object" % path)
        unknown = sorted(set(loaded) - _KNOWN_FIELDS)
        if unknown:
            raise ValueError("unsupported bridge setting(s): %s" % ", ".join(unknown))
        values.update(loaded)

    for name in _BOUNDS:
        values[name] = _validate_int(name, values[name])
    for name in _BOOL_FIELDS:
        values[name] = _validate_bool(name, values[name])
    return BridgeSettings(**values)
