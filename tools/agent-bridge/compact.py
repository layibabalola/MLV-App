"""
agent-bridge inbox compaction

Prunes read messages from inbox JSONL files while preserving:
  - All unread messages (read_at is null) -- NEVER deleted
  - Read messages newer than max_age_days
  - The newest keep_last_read read messages even if older

Rotates messages.jsonl when it exceeds audit_max_mb or its oldest event is more
than one day old. Prunes rotated audit logs older than audit_retention_days.

Usage:
    py -3 tools/agent-bridge/compact.py --state-dir <path> [options]

Options:
    --max-age-days N        Drop read messages older than N days (default: 7)
    --keep-last-read N      Always keep the N newest read messages (default: 200)
    --audit-max-mb N        Rotate audit log when it exceeds N MB (default: 5)
    --audit-retention-days N
                            Drop rotated daily/legacy-monthly audit logs older than N days (default: 90)
    --server-pid-max-age-hours N
                            Reap dead MCP server PID/runtime markers older than N hours (default: 24)
    --audit-permissions-only
                            Audit storage confidentiality and exit nonzero unless verified
    --dry-run               Print what would be done without writing

Safe to run at any time. Uses the same .lock directory as the MCP server,
so compaction cannot race with send_to_peer / mark_read.
"""
import argparse
import os
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import resolve_bridge_paths
from core.storage import StorageCapability


AGENTS = ("claude", "codex")
AUDIT_ACTIVE_MAX_AGE_DAYS = 1


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def read_json(path: Path, default: Any, *, storage: StorageCapability) -> Any:
    storage.ensure_private_file(path)
    if not path.exists():
        return default
    with storage.open_private_read_text(path) as handle:
        return json.load(handle)


def locked(state_dir: Path, *, storage: StorageCapability, timeout: float = 30.0):
    storage.ensure_private_directory(state_dir)
    return storage.file_lock(state_dir / ".compact", timeout_seconds=timeout, stale_seconds=60)


# ---------------------------------------------------------------------------
# Compaction logic
# ---------------------------------------------------------------------------

def compact_inbox(
    state_dir: Path,
    agent: str,
    max_age_days: int = 7,
    keep_last_read: int = 200,
    dry_run: bool = False,
    *,
    storage: StorageCapability,
) -> Dict[str, Any]:
    """
    Compact inbox-{agent}.jsonl:
      - Keep all rows where read_at is None (unread -- never dropped)
      - Keep read rows newer than max_age_days
      - Keep the newest keep_last_read read rows regardless of age
      - Drop everything else
      - Write a compaction event to messages.jsonl
    """
    inbox_path = state_dir / f"inbox-{agent}.jsonl"
    audit_path = state_dir / "messages.jsonl"
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    with locked(state_dir, storage=storage):
        rows = storage.read_jsonl(inbox_path)
        total = len(rows)

        unread = [r for r in rows if not r.get("read_at")]
        read = [r for r in rows if r.get("read_at")]

        # Sort read by read_at descending (newest first)
        read.sort(key=lambda r: r.get("read_at") or "", reverse=True)

        # Keep: recent reads OR within keep_last_read window
        kept_read: List[Dict[str, Any]] = []
        dropped_read: List[Dict[str, Any]] = []
        for i, r in enumerate(read):
            dt = parse_dt(r.get("read_at"))
            within_age = dt is not None and dt.replace(tzinfo=timezone.utc) > cutoff if dt and dt.tzinfo is None else (dt is not None and dt > cutoff)
            within_count = i < keep_last_read
            if within_age or within_count:
                kept_read.append(r)
            else:
                dropped_read.append(r)

        kept = unread + kept_read
        dropped_count = len(dropped_read)

        event = {
            "id": str(uuid.uuid4()),
            "timestamp": utc_now(),
            "action": "compact_inbox",
            "agent": agent,
            "total_before": total,
            "unread_preserved": len(unread),
            "read_kept": len(kept_read),
            "read_dropped": dropped_count,
            "total_after": len(kept),
            "dry_run": dry_run,
        }

        if not dry_run and dropped_count > 0:
            storage.write_jsonl(inbox_path, kept)
            storage.append_jsonl(audit_path, event)

        return event


def _oldest_audit_timestamp(path: Path, *, storage: StorageCapability) -> Optional[datetime]:
    oldest: Optional[datetime] = None
    try:
        storage.ensure_private_file(path)
        with storage.open_private_read_text(path) as source:
            for line in source:
                try:
                    row = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                timestamp = parse_dt(row.get("timestamp"))
                if timestamp is None:
                    continue
                normalized = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
                if oldest is None or normalized < oldest:
                    oldest = normalized
    except OSError:
        return None
    return oldest


def _audit_rotation_plan(
    audit_path: Path,
    *,
    max_mb: float,
    max_age_days: int,
    current: datetime,
    storage: StorageCapability,
) -> Optional[Dict[str, Any]]:
    storage.reject_link_components(audit_path)
    if not audit_path.exists():
        return None
    size_mb = audit_path.stat().st_size / (1024 * 1024)
    oldest = _oldest_audit_timestamp(audit_path, storage=storage)
    if oldest is None:
        oldest = datetime.fromtimestamp(audit_path.stat().st_mtime, timezone.utc)
    age_days = max(0.0, (current - oldest).total_seconds() / 86400.0)
    size_triggered = size_mb >= max_mb
    age_triggered = age_days >= max_age_days
    if not size_triggered and not age_triggered:
        return None
    return {
        "size_mb": size_mb,
        "oldest": oldest,
        "age_days": age_days,
        "size_triggered": size_triggered,
        "age_triggered": age_triggered,
    }


def rotate_audit_log(
    state_dir: Path,
    max_mb: float = 5.0,
    max_age_days: int = AUDIT_ACTIVE_MAX_AGE_DAYS,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    *,
    storage: StorageCapability,
) -> Optional[Dict[str, Any]]:
    """
    Rotate messages.jsonl when it exceeds max_mb or its oldest event exceeds
    max_age_days. Rotated files use a daily name so retention has a bounded
    one-day active-log overhang instead of an unbounded low-volume exception.
    """
    audit_path = state_dir / "messages.jsonl"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    def _rotate_locked(plan: Dict[str, Any]) -> Dict[str, Any]:
        day_tag = plan["oldest"].astimezone(timezone.utc).strftime("%Y-%m-%d")
        rotated_path = state_dir / f"messages.{day_tag}.jsonl"
        event = {
            "id": str(uuid.uuid4()),
            "timestamp": current.isoformat(timespec="seconds"),
            "action": "rotate_audit_log",
            "size_mb": round(plan["size_mb"], 2),
            "age_days": round(plan["age_days"], 3),
            "max_age_days": max_age_days,
            "triggers": [
                name
                for name, active in (("size", plan["size_triggered"]), ("age", plan["age_triggered"]))
                if active
            ],
            "rotated_to": str(rotated_path),
            "dry_run": dry_run,
        }
        if dry_run:
            return event

        with storage.open_private_read_text(audit_path) as source, storage.open_private_text(rotated_path, "a") as target:
            for line in source:
                target.write(line)
        temp = audit_path.with_name("%s.%s.tmp" % (audit_path.name, uuid.uuid4().hex))
        try:
            with storage.open_private_text(temp, "w") as target:
                target.write(json.dumps(event, sort_keys=True))
                target.write("\n")
            storage.atomic_replace(temp, audit_path)
            storage.ensure_private_file(audit_path)
        finally:
            storage.unlink(temp, missing_ok=True)
        return event

    if dry_run:
        plan = _audit_rotation_plan(
            audit_path,
            max_mb=max_mb,
            max_age_days=max_age_days,
            current=current,
            storage=storage,
        )
        return None if plan is None else _rotate_locked(plan)

    with locked(state_dir, storage=storage):
        with storage.file_lock(audit_path):
            plan = _audit_rotation_plan(
                audit_path,
                max_mb=max_mb,
                max_age_days=max_age_days,
                current=current,
                storage=storage,
            )
            return None if plan is None else _rotate_locked(plan)


def prune_audit_logs(
    state_dir: Path,
    retention_days: int = 90,
    dry_run: bool = False,
    *,
    storage: StorageCapability,
) -> Dict[str, Any]:
    """Remove rotated audit logs older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed: List[str] = []
    would_remove: List[str] = []
    kept = 0
    errors: List[Dict[str, str]] = []

    def _prune_locked() -> Dict[str, Any]:
        nonlocal kept
        for path in sorted(state_dir.glob("messages.*.jsonl")):
            if not re.fullmatch(r"messages\.\d{4}-\d{2}(?:-\d{2})?\.jsonl", path.name):
                continue
            try:
                storage.reject_link_components(path)
                mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except OSError as exc:
                errors.append({"path": str(path), "error": str(exc)})
                continue
            if mtime >= cutoff:
                kept += 1
                continue
            would_remove.append(str(path))
            if dry_run:
                continue
            try:
                storage.unlink(path)
            except OSError as exc:
                errors.append({"path": str(path), "error": str(exc)})
                continue
            removed.append(str(path))

        event = {
            "id": str(uuid.uuid4()),
            "timestamp": utc_now(),
            "action": "prune_audit_logs",
            "retention_days": retention_days,
            "removed": len(removed),
            "removed_paths": removed,
            "would_remove": len(would_remove),
            "would_remove_paths": would_remove,
            "kept": kept,
            "dry_run": dry_run,
            "errors": errors,
        }
        if not dry_run and removed:
            storage.append_jsonl(state_dir / "messages.jsonl", event)
        return event

    if dry_run:
        storage.reject_link_components(state_dir)
        return _prune_locked()
    with locked(state_dir, storage=storage):
        return _prune_locked()


def should_compact(
    state_dir: Path,
    agent: str,
    threshold_mb: float = 1.0,
    *,
    storage: StorageCapability,
) -> bool:
    path = state_dir / f"inbox-{agent}.jsonl"
    storage.validate(path)
    if not path.exists():
        return False
    return path.stat().st_size / (1024 * 1024) >= threshold_mb


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


PROCESS_MARKER_START_TOLERANCE_SECONDS = 300


def process_start_time_utc(pid: int) -> Optional[datetime]:
    if pid <= 0 or sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = ctypes.windll.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        ctypes.windll.kernel32.CloseHandle(handle)
        if not ok:
            return None
        ticks = (creation.dwHighDateTime << 32) + creation.dwLowDateTime
        unix_seconds = ticks / 10_000_000 - 11_644_473_600
        return datetime.fromtimestamp(unix_seconds, timezone.utc)
    except Exception:
        return None


def process_runtime_identity_status(
    pid: int,
    runtime: Optional[Dict[str, Any]],
    *,
    expected_role: Optional[str] = None,
    max_start_delta_seconds: int = PROCESS_MARKER_START_TOLERANCE_SECONDS,
) -> Dict[str, Any]:
    running = is_process_alive(pid)
    status: Dict[str, Any] = {
        "running": running,
        "identity_verified": False,
        "identity_mismatch": False,
        "identity_mismatch_reason": None,
        "process_started_at": None,
        "runtime_timestamp_delta_seconds": None,
    }
    if not running:
        return status
    runtime_data = runtime if isinstance(runtime, dict) else {}
    role = runtime_data.get("role")
    if expected_role and role and role != expected_role:
        status["identity_mismatch"] = True
        status["identity_mismatch_reason"] = "role_mismatch"
        return status
    runtime_started_at = parse_dt(runtime_data.get("timestamp"))
    process_started_at = process_start_time_utc(pid)
    if process_started_at is not None:
        status["process_started_at"] = process_started_at.isoformat(timespec="seconds")
    if runtime_started_at is None or process_started_at is None:
        return status
    delta = abs((process_started_at - runtime_started_at).total_seconds())
    status["identity_verified"] = True
    status["runtime_timestamp_delta_seconds"] = round(delta, 3)
    if delta > max_start_delta_seconds:
        status["identity_mismatch"] = True
        status["identity_mismatch_reason"] = "pid_reuse_start_time_mismatch"
    return status


def reap_stale_server_pids(
    state_dir: Path,
    max_age_hours: int = 24,
    dry_run: bool = False,
    *,
    storage: StorageCapability,
) -> Dict[str, Any]:
    """Remove stale MCP server markers without enforcing singleton semantics."""
    server_dir = state_dir / "server-pids"
    cutoff_seconds = max_age_hours * 60 * 60
    checked = 0
    checked_runtime_orphans = 0
    removed: List[str] = []
    removed_runtime_paths: List[str] = []
    removed_runtime_orphans: List[str] = []
    removed_identity_mismatch = 0
    kept_running = 0
    kept_fresh = 0
    errors: List[Dict[str, str]] = []

    if server_dir.exists():
        for marker in sorted(server_dir.glob("server-*.pid")):
            checked += 1
            runtime_marker = marker.with_suffix(".json")
            try:
                with storage.open_private_read_text(marker) as handle:
                    raw = handle.read().strip()
                pid = int(raw)
            except (OSError, ValueError) as exc:
                pid = 0
                errors.append({"path": str(marker), "error": str(exc)})
            runtime: Optional[Dict[str, Any]] = None
            if runtime_marker.exists():
                try:
                    runtime = read_json(runtime_marker, {}, storage=storage)
                except Exception as exc:
                    errors.append({"path": str(runtime_marker), "error": str(exc)})
            identity = process_runtime_identity_status(pid, runtime, expected_role="mcp_server")
            running = bool(identity["running"])
            identity_mismatch = bool(identity["identity_mismatch"])
            age_seconds = time.time() - marker.stat().st_mtime
            if running and not identity_mismatch:
                kept_running += 1
                continue
            if running and identity_mismatch:
                removed_identity_mismatch += 1
            elif age_seconds < cutoff_seconds:
                kept_fresh += 1
                continue
            removed.append(str(marker))
            if runtime_marker.exists():
                removed_runtime_paths.append(str(runtime_marker))
            if not dry_run:
                try:
                    storage.unlink(marker)
                except OSError as exc:
                    errors.append({"path": str(marker), "error": str(exc)})
                try:
                    storage.unlink(runtime_marker, missing_ok=True)
                except OSError as exc:
                    errors.append({"path": str(runtime_marker), "error": str(exc)})

        for runtime_marker in sorted(server_dir.glob("server-*.json")):
            if runtime_marker.with_suffix(".pid").exists():
                continue
            checked_runtime_orphans += 1
            try:
                age_seconds = time.time() - runtime_marker.stat().st_mtime
            except OSError as exc:
                errors.append({"path": str(runtime_marker), "error": str(exc)})
                continue
            if age_seconds < cutoff_seconds:
                kept_fresh += 1
                continue
            removed_runtime_orphans.append(str(runtime_marker))
            if not dry_run:
                try:
                    storage.unlink(runtime_marker)
                except OSError as exc:
                    errors.append({"path": str(runtime_marker), "error": str(exc)})

    event = {
        "id": str(uuid.uuid4()),
        "timestamp": utc_now(),
        "action": "reap_stale_server_pids",
        "checked": checked,
        "checked_runtime_orphans": checked_runtime_orphans,
        "removed": len(removed),
        "removed_paths": removed,
        "removed_runtime_paths": removed_runtime_paths,
        "removed_runtime_orphans": removed_runtime_orphans,
        "removed_identity_mismatch": removed_identity_mismatch,
        "kept_running": kept_running,
        "kept_fresh": kept_fresh,
        "max_age_hours": max_age_hours,
        "dry_run": dry_run,
        "errors": errors,
    }
    if not dry_run and removed:
        storage.append_jsonl(state_dir / "messages.jsonl", event)
    return event


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="agent-bridge inbox compaction")
    parser.add_argument("--state-dir", required=True, help="Bridge state directory")
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--keep-last-read", type=int, default=200)
    parser.add_argument("--audit-max-mb", type=float, default=5.0)
    parser.add_argument("--audit-retention-days", type=int, default=90)
    parser.add_argument("--server-pid-max-age-hours", type=int, default=24)
    parser.add_argument("--audit-permissions-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    args = parser.parse_args()

    # CLI root selection is the trust boundary.  Resolve and authorize the
    # canonical bridge root before the audit or any compaction storage sink.
    paths = resolve_bridge_paths(state_dir=Path(args.state_dir))
    state_dir = paths.state_dir

    if args.audit_permissions_only:
        audit_root = paths.root
        report = paths.storage.audit_private_tree()
        report["scope"] = "bridge_root"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 2

    any_work = False
    for agent in AGENTS:
        result = compact_inbox(
            state_dir,
            agent,
            max_age_days=args.max_age_days,
            keep_last_read=args.keep_last_read,
            dry_run=args.dry_run,
            storage=paths.storage,
        )
        if result["read_dropped"] > 0 or args.dry_run:
            any_work = True
            tag = "[DRY RUN] " if args.dry_run else ""
            print(
                f"{tag}compact inbox-{agent}.jsonl: "
                f"{result['total_before']} rows -> {result['total_after']} "
                f"(dropped {result['read_dropped']} read, "
                f"preserved {result['unread_preserved']} unread)"
            )

    rotate_result = rotate_audit_log(
        state_dir,
        max_mb=args.audit_max_mb,
        dry_run=args.dry_run,
        storage=paths.storage,
    )
    if rotate_result:
        any_work = True
        tag = "[DRY RUN] " if args.dry_run else ""
        print(f"{tag}rotated messages.jsonl ({rotate_result['size_mb']} MB) -> {rotate_result['rotated_to']}")

    prune_result = prune_audit_logs(
        state_dir,
        retention_days=args.audit_retention_days,
        dry_run=args.dry_run,
        storage=paths.storage,
    )
    if prune_result["removed"] > 0 or args.dry_run:
        any_work = True
        tag = "[DRY RUN] " if args.dry_run else ""
        print(
            f"{tag}pruned audit logs: kept {prune_result['kept']}, "
            f"removed {prune_result['removed']}"
        )

    reaper_result = reap_stale_server_pids(
        state_dir,
        max_age_hours=args.server_pid_max_age_hours,
        dry_run=args.dry_run,
        storage=paths.storage,
    )
    if reaper_result["removed"] > 0 or args.dry_run:
        any_work = True
        tag = "[DRY RUN] " if args.dry_run else ""
        print(
            f"{tag}reaped server pid markers: checked {reaper_result['checked']}, "
            f"removed {reaper_result['removed']}"
        )

    if not any_work:
        print("Nothing to compact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
