import dataclasses
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import STATE_SCHEMA_VERSION

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
    codex_bridge_reminder_toasts_enabled: bool = False
    routing_rules_enabled: bool = True
    default_pairing_intent: str = "ask_first"
    pending_pair_timeout_seconds: int = 120
    wake_provider: str = "targeted_sendkeys"

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
    "pending_pair_timeout_seconds": (10, 3600),
}

_BOOL_FIELDS = {"toasts_enabled", "codex_bridge_reminder_toasts_enabled", "routing_rules_enabled"}
_ENUM_FIELDS = {
    "default_pairing_intent": {"ask_first", "active_primary", "background"},
    "wake_provider": {"disabled", "sendkeys", "targeted_sendkeys", "app_server", "app_server_then_redraw"},
}
_KNOWN_FIELDS = set(DEFAULT_SETTINGS.to_dict())
_KNOWN_META_FIELDS = {"schema_version"}


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


def _validate_enum(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % name)
    normalized = value.strip().lower()
    allowed = _ENUM_FIELDS[name]
    if normalized not in allowed:
        raise ValueError("%s must be one of: %s" % (name, ", ".join(sorted(allowed))))
    return normalized


def load_settings(state_dir: Path, settings_path: Optional[Path] = None) -> BridgeSettings:
    path = Path(settings_path) if settings_path else settings_path_for_state_dir(Path(state_dir))
    values = DEFAULT_SETTINGS.to_dict()
    if path.exists():
        with path.open("r", encoding="utf-8-sig") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError("%s must contain a JSON object" % path)
        unknown = sorted(set(loaded) - _KNOWN_FIELDS - _KNOWN_META_FIELDS)
        if unknown:
            raise ValueError("unsupported bridge setting(s): %s" % ", ".join(unknown))
        loaded.setdefault("schema_version", STATE_SCHEMA_VERSION)
        values.update(loaded)
    values.pop("schema_version", None)

    for name in _BOUNDS:
        values[name] = _validate_int(name, values[name])
    for name in _BOOL_FIELDS:
        values[name] = _validate_bool(name, values[name])
    for name in _ENUM_FIELDS:
        values[name] = _validate_enum(name, values[name])
    return BridgeSettings(**values)
