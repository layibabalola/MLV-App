"""
agent-bridge state recovery

Validates bridge state files and, when explicitly requested, performs the
smallest safe repair:
  - back up every touched file before mutation
  - replace corrupt JSON object files with minimal defaults
  - rewrite JSONL files with valid rows only and append bad lines to
    <file>.quarantine.jsonl

Default mode is dry-run/validate only.
"""
import argparse
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.paths import ROOT_MANIFEST_FILENAME, resolve_moved_root_chain


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def backup_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_state() -> Dict[str, Any]:
    return {"paused": False, "sessions": {}, "updated_at": utc_now()}


def default_session_registry() -> Dict[str, Any]:
    return {"projects": {}, "updated_at": utc_now()}


def read_json_object(path: Path) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    if not path.exists():
        return True, None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except Exception as exc:
        return False, None, str(exc)
    if not isinstance(parsed, dict):
        return False, None, "file must contain a JSON object"
    return True, parsed, None


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path.absolute()).lower()


def _moved_target(manifest: Dict[str, Any]) -> Optional[Path]:
    value = manifest.get("active_root") or manifest.get("target_root") or manifest.get("moved_to")
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def _add_historical_issue(report: Dict[str, Any], code: str, message: str, **details: Any) -> None:
    report["ok"] = False
    issue = {"code": code, "message": message}
    issue.update(details)
    report["historical"]["issues"].append(issue)


def scan_historical_state(state_dir: Path) -> Dict[str, Any]:
    state_dir = Path(state_dir)
    root = state_dir.parent
    manifest_path = root / ROOT_MANIFEST_FILENAME
    historical: Dict[str, Any] = {
        "root": str(root),
        "manifest_path": str(manifest_path),
        "manifest_exists": manifest_path.exists(),
        "redirect": None,
        "migration_sources": [],
        "issues": [],
    }
    report: Dict[str, Any] = {"ok": True, "historical": historical}

    try:
        moved = resolve_moved_root_chain(root)
    except Exception as exc:
        _add_historical_issue(report, "redirect_error", "MOVED_TO.json chain could not be resolved", error=str(exc))
        moved = None

    if moved:
        target = Path(moved["target"])
        chain = [str(path) for path in moved.get("chain", [])]
        historical["redirect"] = {"target": str(target), "chain": chain}
        _add_historical_issue(
            report,
            "root_is_stale",
            "This bridge root has MOVED_TO.json and should not accept new writes",
            active_root=str(target),
            chain=chain,
        )
        target_manifest = target / ROOT_MANIFEST_FILENAME
        ok, target_data, error = read_json_object(target_manifest)
        if not ok or target_data is None:
            _add_historical_issue(
                report,
                "target_manifest_unreadable",
                "The redirected active root is missing or has an unreadable bridge-root.json",
                path=str(target_manifest),
                error=error,
            )
        elif _path_key(Path(str(target_data.get("active_root", target)))) != _path_key(target):
            _add_historical_issue(
                report,
                "target_manifest_mismatch",
                "The redirected active root manifest does not point at its own root",
                path=str(target_manifest),
                active_root=target_data.get("active_root"),
            )

    ok, manifest, error = read_json_object(manifest_path)
    if not ok:
        _add_historical_issue(
            report,
            "manifest_unreadable",
            "bridge-root.json exists but is not a valid JSON object",
            path=str(manifest_path),
            error=error,
        )
        return report
    if manifest is None:
        return report

    history = manifest.get("migration_history", [])
    if history is not None and not isinstance(history, list):
        _add_historical_issue(
            report,
            "migration_history_invalid",
            "bridge-root.json migration_history must be a list when present",
            path=str(manifest_path),
        )
        return report

    for event in history or []:
        if not isinstance(event, dict):
            continue
        source_value = event.get("source")
        target_value = event.get("target")
        if not isinstance(source_value, str) or not source_value:
            continue
        if isinstance(target_value, str) and _path_key(Path(target_value)) != _path_key(root):
            continue
        source = Path(source_value)
        moved_path = source / "MOVED_TO.json"
        source_entry: Dict[str, Any] = {
            "source": str(source),
            "exists": source.exists(),
            "moved_to": str(moved_path),
            "moved_to_exists": moved_path.exists(),
        }
        historical["migration_sources"].append(source_entry)
        if not source.exists():
            continue
        ok, moved_manifest, moved_error = read_json_object(moved_path)
        if moved_manifest is None and ok:
            _add_historical_issue(
                report,
                "source_missing_moved_to",
                "Migration history names a source root that lacks MOVED_TO.json",
                source_root=str(source),
                expected_active_root=str(root),
            )
            continue
        if not ok:
            _add_historical_issue(
                report,
                "source_moved_to_unreadable",
                "Migration source MOVED_TO.json is not readable",
                source_root=str(source),
                path=str(moved_path),
                error=moved_error,
            )
            continue
        moved_target = _moved_target(moved_manifest or {})
        if moved_target is None or _path_key(moved_target) != _path_key(root):
            _add_historical_issue(
                report,
                "source_redirect_mismatch",
                "Migration source MOVED_TO.json does not point at this active root",
                source_root=str(source),
                expected_active_root=str(root),
                actual_active_root=str(moved_target) if moved_target else None,
            )

    return report


def validate_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    valid: List[Dict[str, Any]] = []
    invalid: List[str] = []
    if not path.exists():
        return valid, invalid
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                invalid.append(raw)
                continue
            if isinstance(parsed, dict):
                valid.append(parsed)
            else:
                invalid.append(raw)
    return valid, invalid


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")
    tmp.replace(path)


def append_quarantine(path: Path, lines: Iterable[str]) -> int:
    lines = list(lines)
    if not lines:
        return 0
    qpath = path.with_suffix(".quarantine.jsonl")
    with qpath.open("a", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line)
            handle.write("\n")
    return len(lines)


def backup_files(paths: Iterable[Path], backup_root: Path) -> List[str]:
    backup_root.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for path in paths:
        if not path.exists():
            continue
        target = backup_root / path.name
        if target.exists():
            target = backup_root / ("%s.%s" % (path.name, uuid.uuid4().hex[:8]))
        shutil.copy2(path, target)
        copied.append(str(target))
    return copied


def recover_state(state_dir: Path, repair: bool = False, scan_historical: bool = False) -> Dict[str, Any]:
    state_dir = Path(state_dir)
    session_path = state_dir.parent / "session.json"
    json_files = [
        ("state", state_dir / "state.json", default_state),
        ("session_registry", session_path, default_session_registry),
    ]
    jsonl_files = [
        ("audit", state_dir / "messages.jsonl"),
        ("inbox_claude", state_dir / "inbox-claude.jsonl"),
        ("inbox_codex", state_dir / "inbox-codex.jsonl"),
        ("orphaned_claude", state_dir / "orphaned-claude.jsonl"),
        ("orphaned_codex", state_dir / "orphaned-codex.jsonl"),
    ]

    report: Dict[str, Any] = {
        "ok": True,
        "repair": repair,
        "state_dir": str(state_dir),
        "checked_at": utc_now(),
        "backup_dir": None,
        "backups": [],
        "json": {},
        "jsonl": {},
        "repaired": [],
    }
    if scan_historical:
        historical = scan_historical_state(state_dir)
        report["historical"] = historical["historical"]
        if not historical["ok"]:
            report["ok"] = False

    touched: List[Path] = []
    json_repairs: List[Tuple[str, Path, Dict[str, Any]]] = []
    jsonl_repairs: List[Tuple[str, Path, List[Dict[str, Any]], List[str]]] = []

    for name, path, default_factory in json_files:
        ok, value, error = read_json_object(path)
        entry: Dict[str, Any] = {
            "path": str(path),
            "exists": path.exists(),
            "ok": ok,
            "error": error,
        }
        if ok and value is not None:
            entry["keys"] = sorted(value.keys())
        if not ok:
            report["ok"] = False
            touched.append(path)
            if repair:
                entry["repaired"] = True
                report["repaired"].append(name)
                json_repairs.append((name, path, default_factory()))
        report["json"][name] = entry

    for name, path in jsonl_files:
        valid, invalid = validate_jsonl(path)
        entry = {
            "path": str(path),
            "exists": path.exists(),
            "valid_rows": len(valid),
            "invalid_rows": len(invalid),
            "ok": not invalid,
        }
        if invalid:
            report["ok"] = False
            touched.append(path)
            if repair:
                entry["repaired"] = True
                report["repaired"].append(name)
                jsonl_repairs.append((name, path, valid, invalid))
        report["jsonl"][name] = entry

    if repair and touched:
        backup_dir = state_dir / "backups" / ("recovery-%s" % backup_stamp())
        report["backup_dir"] = str(backup_dir)
        report["backups"] = backup_files(touched, backup_dir)
        for _name, path, value in json_repairs:
            write_json(path, value)
        for name, path, valid, invalid in jsonl_repairs:
            write_jsonl(path, valid)
            quarantined = append_quarantine(path, invalid)
            report["jsonl"][name]["quarantined_rows"] = quarantined

    return report


def print_report(report: Dict[str, Any]) -> None:
    print(json.dumps(report, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and optionally repair agent-bridge state.")
    parser.add_argument("--state-dir", required=True, help="Bridge state directory")
    parser.add_argument("--repair", action="store_true", help="Mutate state after backing up touched files")
    parser.add_argument(
        "--scan-historical",
        action="store_true",
        help="Also inspect bridge-root manifests, MOVED_TO chains, and partial migration breadcrumbs",
    )
    args = parser.parse_args()

    report = recover_state(Path(args.state_dir), repair=args.repair, scan_historical=args.scan_historical)
    print_report(report)
    if not report["ok"] and not args.repair:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
