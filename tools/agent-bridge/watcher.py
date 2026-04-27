"""
agent-bridge watcher daemon

Watches bridge inbox JSONL files directly and triggers the consumer when an
unread message arrives. No model tokens spent on empty checks. Headless --
no toasts, no manual acknowledgement.

Usage:
    py -3 tools/agent-bridge/watcher.py --config tools/agent-bridge/watcher-config.json

The config file lists sessions to watch. See watcher-config.example.json.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Compaction support (same directory -- import directly)
try:
    from compact import compact_inbox, rotate_audit_log, should_compact
    _COMPACT_AVAILABLE = True
except ImportError:
    _COMPACT_AVAILABLE = False


POLL_INTERVAL_S = 2        # file-stat poll interval; cheap
COMPACT_INTERVAL_S = 6 * 3600  # run compaction every 6 hours
COMPACT_SIZE_MB = 1.0          # also compact if inbox exceeds this size


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def unread_for_session(inbox_path: Path, session_id: str) -> List[Dict[str, Any]]:
    rows = read_jsonl(inbox_path)
    return [
        r for r in rows
        if r.get("session_id") == session_id and not r.get("read_at")
    ]


def load_seen(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {"seen_ids": []}
    with state_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(state_path: Path, seen: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(seen, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(state_path)


def run_compaction(state_dir: Path) -> None:
    if not _COMPACT_AVAILABLE:
        return
    try:
        from compact import AGENTS as _AGENTS
        for agent in _AGENTS:
            result = compact_inbox(state_dir, agent)
            if result["read_dropped"] > 0:
                print(
                    f"[agent-bridge] compact inbox-{agent}.jsonl: "
                    f"dropped {result['read_dropped']} read rows, "
                    f"{result['unread_preserved']} unread preserved",
                    flush=True,
                )
        rotate_audit_log(state_dir)
    except Exception as exc:
        print(f"[agent-bridge] compaction error: {exc}", flush=True)


def notify_terminal(agent: str, session_id: str, messages: List[Dict[str, Any]]) -> None:
    count = len(messages)
    summary = messages[0].get("body", "")[:80].replace("\n", " ")
    print(
        f"[agent-bridge] {utc_now()} -- {count} unread for {agent} "
        f"(session ...{session_id[-8:]}): {summary!r}",
        flush=True,
    )


def notify_windows_toast(agent: str, session_id: str, messages: List[Dict[str, Any]]) -> None:
    try:
        from win10toast import ToastNotifier  # type: ignore
        toaster = ToastNotifier()
        summary = messages[0].get("body", "")[:60].replace("\n", " ")
        toaster.show_toast(
            f"agent-bridge: message for {agent}",
            summary,
            duration=5,
            threaded=True,
        )
    except ImportError:
        notify_terminal(agent, session_id, messages)


def run_command(cmd: str, agent: str, session_id: str) -> None:
    import subprocess
    try:
        subprocess.Popen(
            cmd,
            shell=True,
            env={**__import__("os").environ, "BRIDGE_AGENT": agent, "BRIDGE_SESSION": session_id},
        )
    except Exception as exc:
        print(f"[agent-bridge] on_message_command failed: {exc}", flush=True)


def watch(config_path: Path) -> None:
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    sessions = config.get("sessions", [])
    if not sessions:
        print("[agent-bridge] No sessions configured. Exiting.", flush=True)
        return

    # Per-session seen-ID tracking stored alongside watcher config
    state_path = config_path.parent / "watcher-state.json"
    seen_data = load_seen(state_path)
    seen_ids: set = set(seen_data.get("seen_ids", []))

    # Derive state_dir from first inbox path (parent directory)
    state_dir = Path(sessions[0]["inbox"]).parent if sessions else None

    print(f"[agent-bridge] Watcher started. Watching {len(sessions)} session(s). Poll every {POLL_INTERVAL_S}s.", flush=True)
    for s in sessions:
        print(f"  agent={s['agent']}  session=...{s['session_id'][-8:]}  inbox={s['inbox']}", flush=True)

    # Compact on startup
    if state_dir:
        run_compaction(state_dir)

    last_compact = time.monotonic()

    try:
        while True:
            # Periodic and size-triggered compaction
            if state_dir:
                now = time.monotonic()
                time_due = (now - last_compact) >= COMPACT_INTERVAL_S
                size_due = _COMPACT_AVAILABLE and any(
                    should_compact(state_dir, s["agent"], COMPACT_SIZE_MB)
                    for s in sessions
                )
                if time_due or size_due:
                    run_compaction(state_dir)
                    last_compact = now

            for s in sessions:
                agent = s["agent"]
                session_id = s["session_id"]
                inbox_path = Path(s["inbox"])
                on_message = s.get("on_message", "notify")
                on_message_command: Optional[str] = s.get("on_message_command")

                unread = unread_for_session(inbox_path, session_id)
                new_msgs = [m for m in unread if m.get("id") not in seen_ids]

                if new_msgs:
                    for m in new_msgs:
                        seen_ids.add(m["id"])

                    # Notify
                    if on_message == "toast":
                        notify_windows_toast(agent, session_id, new_msgs)
                    else:
                        notify_terminal(agent, session_id, new_msgs)

                    # Optional command hook (e.g. wake Codex automation)
                    if on_message_command:
                        run_command(on_message_command, agent, session_id)

                    # Persist seen IDs (keep last 500 to avoid unbounded growth)
                    save_seen(state_path, {"seen_ids": list(seen_ids)[-500:]})

            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        print("\n[agent-bridge] Watcher stopped.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="agent-bridge inbox watcher daemon")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to watcher config JSON (see watcher-config.example.json)",
    )
    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[agent-bridge] Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    watch(config_path)


if __name__ == "__main__":
    main()
