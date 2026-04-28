"""
agent-bridge inbox compaction

Prunes read messages from inbox JSONL files while preserving:
  - All unread messages (read_at is null) -- NEVER deleted
  - Read messages newer than max_age_days
  - The newest keep_last_read read messages even if older

Rotates messages.jsonl by month when it exceeds audit_max_mb.
Prunes rotated audit logs older than audit_retention_days.

Usage:
    py -3 tools/agent-bridge/compact.py --state-dir <path> [options]

Options:
    --max-age-days N        Drop read messages older than N days (default: 7)
    --keep-last-read N      Always keep the N newest read messages (default: 200)
    --audit-max-mb N        Rotate audit log when it exceeds N MB (default: 5)
    --audit-retention-days N
                            Drop rotated messages.YYYY-MM.jsonl files older than N days (default: 90)
    --dry-run               Print what would be done without writing

Safe to run at any time. Uses the same .lock directory as the MCP server,
so compaction cannot race with send_to_peer / mark_read.
"""
import argparse
import contextlib
import os
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


AGENTS = ("claude", "codex")


# ---------------------------------------------------------------------------
# I/O helpers (same as agent_bridge.py -- no import to keep compact standalone)
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    quarantine: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                quarantine.append(line)
    if quarantine:
        qpath = path.with_suffix(".quarantine.jsonl")
        with qpath.open("a", encoding="utf-8", newline="\n") as f:
            for bad in quarantine:
                f.write(bad + "\n")
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True))
            f.write("\n")
    tmp.replace(path)


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, sort_keys=True))
        f.write("\n")


@contextlib.contextmanager
def locked(state_dir: Path, timeout: float = 30.0):
    lock_path = state_dir / ".lock"
    state_dir.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    while True:
        try:
            lock_path.mkdir()
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 60:
                    lock_path.rmdir()
                    continue
            except OSError:
                pass
            if time.monotonic() - start > timeout:
                raise TimeoutError("timed out waiting for bridge state lock")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_path.rmdir()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Compaction logic
# ---------------------------------------------------------------------------

def compact_inbox(
    state_dir: Path,
    agent: str,
    max_age_days: int = 7,
    keep_last_read: int = 200,
    dry_run: bool = False,
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

    with locked(state_dir):
        rows = read_jsonl(inbox_path)
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
            write_jsonl(inbox_path, kept)
            append_jsonl(audit_path, event)

        return event


def rotate_audit_log(
    state_dir: Path,
    max_mb: float = 5.0,
    dry_run: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Rotate messages.jsonl by month when it exceeds max_mb.
    Rotated file is named messages.YYYY-MM.jsonl.
    """
    audit_path = state_dir / "messages.jsonl"
    if not audit_path.exists():
        return None

    size_mb = audit_path.stat().st_size / (1024 * 1024)
    if size_mb < max_mb:
        return None

    month_tag = datetime.now(timezone.utc).strftime("%Y-%m")
    rotated_path = state_dir / f"messages.{month_tag}.jsonl"

    # If the target month file already exists, append to it
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": utc_now(),
        "action": "rotate_audit_log",
        "size_mb": round(size_mb, 2),
        "rotated_to": str(rotated_path),
        "dry_run": dry_run,
    }

    if not dry_run:
        with locked(state_dir):
            with audit_path.open("r", encoding="utf-8") as src, \
                 rotated_path.open("a", encoding="utf-8", newline="\n") as dst:
                for line in src:
                    dst.write(line)
            # Write rotation marker to fresh audit log
            write_jsonl(audit_path, [event])

    return event


def prune_audit_logs(
    state_dir: Path,
    retention_days: int = 90,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Remove rotated audit logs older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed: List[str] = []
    kept = 0
    errors: List[Dict[str, str]] = []

    for path in sorted(state_dir.glob("messages.*.jsonl")):
        if not re.fullmatch(r"messages\.\d{4}-\d{2}\.jsonl", path.name):
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        if mtime >= cutoff:
            kept += 1
            continue
        removed.append(str(path))
        if not dry_run:
            try:
                path.unlink()
            except OSError as exc:
                errors.append({"path": str(path), "error": str(exc)})

    event = {
        "id": str(uuid.uuid4()),
        "timestamp": utc_now(),
        "action": "prune_audit_logs",
        "retention_days": retention_days,
        "removed": len(removed),
        "removed_paths": removed,
        "kept": kept,
        "dry_run": dry_run,
        "errors": errors,
    }
    if not dry_run and removed:
        append_jsonl(state_dir / "messages.jsonl", event)
    return event


def should_compact(state_dir: Path, agent: str, threshold_mb: float = 1.0) -> bool:
    path = state_dir / f"inbox-{agent}.jsonl"
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


def reap_stale_server_pids(
    state_dir: Path,
    max_age_hours: int = 24,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Remove stale MCP server markers without enforcing singleton semantics."""
    server_dir = state_dir / "server-pids"
    cutoff_seconds = max_age_hours * 60 * 60
    checked = 0
    removed: List[str] = []
    kept_running = 0
    kept_fresh = 0
    errors: List[Dict[str, str]] = []

    if server_dir.exists():
        for marker in sorted(server_dir.glob("server-*.pid")):
            checked += 1
            try:
                raw = marker.read_text(encoding="utf-8").strip()
                pid = int(raw)
            except (OSError, ValueError) as exc:
                pid = 0
                errors.append({"path": str(marker), "error": str(exc)})
            running = is_process_alive(pid)
            age_seconds = time.time() - marker.stat().st_mtime
            if running:
                kept_running += 1
                continue
            if age_seconds < cutoff_seconds:
                kept_fresh += 1
                continue
            removed.append(str(marker))
            if not dry_run:
                try:
                    marker.unlink()
                except OSError as exc:
                    errors.append({"path": str(marker), "error": str(exc)})

    event = {
        "id": str(uuid.uuid4()),
        "timestamp": utc_now(),
        "action": "reap_stale_server_pids",
        "checked": checked,
        "removed": len(removed),
        "removed_paths": removed,
        "kept_running": kept_running,
        "kept_fresh": kept_fresh,
        "max_age_hours": max_age_hours,
        "dry_run": dry_run,
        "errors": errors,
    }
    if not dry_run and removed:
        append_jsonl(state_dir / "messages.jsonl", event)
    return event


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="agent-bridge inbox compaction")
    parser.add_argument("--state-dir", required=True, help="Bridge state directory")
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--keep-last-read", type=int, default=200)
    parser.add_argument("--audit-max-mb", type=float, default=5.0)
    parser.add_argument("--audit-retention-days", type=int, default=90)
    parser.add_argument("--server-pid-max-age-hours", type=int, default=24)
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)

    any_work = False
    for agent in AGENTS:
        result = compact_inbox(
            state_dir,
            agent,
            max_age_days=args.max_age_days,
            keep_last_read=args.keep_last_read,
            dry_run=args.dry_run,
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

    rotate_result = rotate_audit_log(state_dir, max_mb=args.audit_max_mb, dry_run=args.dry_run)
    if rotate_result:
        any_work = True
        tag = "[DRY RUN] " if args.dry_run else ""
        print(f"{tag}rotated messages.jsonl ({rotate_result['size_mb']} MB) -> {rotate_result['rotated_to']}")

    prune_result = prune_audit_logs(
        state_dir,
        retention_days=args.audit_retention_days,
        dry_run=args.dry_run,
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


if __name__ == "__main__":
    main()
