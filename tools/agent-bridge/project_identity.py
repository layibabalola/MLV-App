import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


def normalize_rendezvous(name: str) -> str:
    lowered = (name or "").strip().lower().replace(" ", "-")
    normalized = "".join(ch for ch in lowered if ch.isascii() and (ch.isalnum() or ch in "-_"))
    normalized = normalized.strip("-_")
    if normalized:
        return normalized
    digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:8]
    return "project-%s" % digest


def _git_toplevel(start_path: Path) -> Optional[Path]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start_path),
            capture_output=True,
            text=True,
            check=True,
        )
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(start_path),
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    value = (result.stdout or "").strip()
    if not value:
        return None
    common_dir = (common.stdout or "").strip()
    if common_dir:
        common_path = Path(common_dir)
        if common_path.name.lower() == ".git":
            return common_path.parent
    return Path(value)


def _submodule_warning(start_path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-superproject-working-tree"],
            cwd=str(start_path),
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    value = (result.stdout or "").strip()
    if value:
        return "git submodule detected; using the submodule root rather than the parent repository"
    return None


def _load_override(root: Path) -> Optional[Dict[str, Any]]:
    path = root / ".agent-bridge.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(".agent-bridge.json must contain a JSON object")
    return data


def derive_project_identity(start_path: Optional[str] = None) -> Dict[str, Any]:
    base = Path(start_path).resolve() if start_path else Path.cwd().resolve()
    root = _git_toplevel(base)
    source = "git"
    warning = _submodule_warning(base)
    if root is None:
        root = base
        source = "cwd"
        warning = "git rev-parse --show-toplevel failed; falling back to current working directory"

    override = _load_override(root)
    if override and override.get("rendezvous"):
        rendezvous = str(override["rendezvous"]).strip()
        if not rendezvous:
            raise ValueError(".agent-bridge.json rendezvous override cannot be empty")
        return {
            "canonical_root": str(root),
            "rendezvous": rendezvous,
            "source": "override",
            "warning": warning,
        }

    basename = root.name or str(root)
    normalized = normalize_rendezvous(basename)
    if normalized.startswith("project-") and warning is None:
        warning = "project name normalized to a hash fallback because no ASCII rendezvous name could be derived"
    return {
        "canonical_root": str(root),
        "rendezvous": normalized,
        "source": source,
        "warning": warning,
    }
