"""Poll Claude's bridge inbox and print one line for new unread rows.

This helper is intended to be launched by Claude's in-app Monitor. It is not a
consumer: it never marks rows seen/read/handled. Its only job is to make a dead
Claude-side Monitor obvious by giving the Monitor a concrete, repeatable command
to run.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Set

from core.runtime import build_monitor_runtime_breadcrumb, monitor_runtime_path_for_state_dir, write_runtime_breadcrumb


def iter_jsonl(path: Path) -> Iterable[Dict[str, object]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    yield parsed
    except FileNotFoundError:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll an agent-bridge inbox for Claude Monitor wakeups.")
    parser.add_argument("--state-dir")
    parser.add_argument("--inbox", help="Legacy/diagnostic explicit inbox path. Prefer --state-dir.")
    parser.add_argument("--agent", default="claude", choices=("claude", "codex"))
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--interval", type=float, help="Legacy alias for --poll-interval-seconds.")
    parser.add_argument(
        "--suppress-existing",
        action="store_true",
        help="Do not emit targeted unread rows that already existed at startup.",
    )
    parser.add_argument("--once", action="store_true", help="Emit startup diagnostics and exit.")
    args = parser.parse_args()

    if args.inbox:
        inbox_path = Path(os.path.expandvars(os.path.expanduser(args.inbox)))
        state_dir = inbox_path.parent
    elif args.state_dir:
        state_dir = Path(os.path.expandvars(os.path.expanduser(args.state_dir)))
        inbox_path = state_dir / ("inbox-%s.jsonl" % args.agent)
    else:
        parser.error("one of --state-dir or --inbox is required")
    targets: Set[str] = {args.session_id, args.project}
    seen_ids: Set[str] = set()
    poll_interval = max(0.5, float(args.interval if args.interval is not None else (args.poll_interval_seconds or 2.0)))
    runtime_path = monitor_runtime_path_for_state_dir(state_dir, args.agent, args.session_id)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    last_emit_at = None
    preexisting_target_unread = 0

    def write_runtime() -> None:
        payload = build_monitor_runtime_breadcrumb(
            state_dir=state_dir,
            agent=args.agent,
            session_id=args.session_id,
            project=args.project,
            script_path=Path(__file__).resolve(),
            argv=[os.path.basename(__file__), *sys.argv[1:]],
            watched_buckets=sorted(targets),
            poll_interval_seconds=poll_interval,
            preexisting_target_unread=preexisting_target_unread,
            last_emit_at=last_emit_at,
            context_generation=os.environ.get("CLAUDE_CONTEXT_GENERATION") or os.environ.get("CODEX_CONTEXT_GENERATION"),
            started_at=started_at,
        )
        try:
            write_runtime_breadcrumb(runtime_path, payload)
        except OSError as exc:
            print("[BRIDGE-MONITOR] runtime heartbeat write failed: %s" % exc, flush=True)

    def emit_unread(row: Dict[str, object]) -> None:
        nonlocal last_emit_at
        bucket = str(row.get("session_id") or "").strip()
        message_id = str(row.get("id") or "").strip()
        body = str(row.get("body") or row.get("delivered_message") or "")[:240].replace("\n", " | ")
        last_emit_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(
            "[BRIDGE] unread agent=%s bucket=%s from=%s id=%s body=%r"
            % (args.agent, bucket, row.get("from", "?"), message_id, body),
            flush=True,
        )

    for row in iter_jsonl(inbox_path):
        message_id = str(row.get("id") or "").strip()
        if not message_id:
            continue
        seen_ids.add(message_id)
        if row.get("read_at") or row.get("to") != args.agent:
            continue
        bucket = str(row.get("session_id") or "").strip()
        if bucket not in targets:
            continue
        preexisting_target_unread += 1
        if not args.suppress_existing:
            emit_unread(row)

    print(
        "[BRIDGE-MONITOR] watching %s for buckets=%s; primed_ids=%d; preexisting_target_unread=%d"
        % (inbox_path, sorted(targets), len(seen_ids), preexisting_target_unread),
        flush=True,
    )
    write_runtime()
    if args.once:
        return 0

    while True:
        write_runtime()
        for row in iter_jsonl(inbox_path):
            message_id = str(row.get("id") or "").strip()
            if not message_id or message_id in seen_ids:
                continue
            seen_ids.add(message_id)
            if row.get("read_at") or row.get("to") != args.agent:
                continue
            bucket = str(row.get("session_id") or "").strip()
            if bucket not in targets:
                continue
            emit_unread(row)
            write_runtime()
        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
