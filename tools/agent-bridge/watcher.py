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
import os
import subprocess
import sys
import threading
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
    quarantine: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
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


def run_command_for_session(cmd: str, agent: str, session_id: str, messages: List[Dict[str, Any]], inbox_path: Path) -> bool:
    import subprocess

    if not messages:
        return False
    first = messages[0]
    env = {**__import__("os").environ}
    env["BRIDGE_AGENT"] = agent
    env["BRIDGE_SESSION"] = session_id
    env["BRIDGE_MESSAGE_ID"] = str(first.get("id", ""))
    env["BRIDGE_MESSAGE_FROM"] = str(first.get("from", ""))
    env["BRIDGE_MESSAGE_TYPE"] = str(first.get("control_type", ""))
    env["BRIDGE_MARKER_VARIANT"] = str(first.get("marker_variant", ""))
    env["BRIDGE_INBOX"] = str(inbox_path)
    env["BRIDGE_BODY"] = str(first.get("body", ""))
    env["BRIDGE_MESSAGE_COUNT"] = str(len(messages))
    env["BRIDGE_MESSAGE_IDS"] = json.dumps([msg.get("id") for msg in messages])
    try:
        proc = subprocess.Popen(cmd, shell=True, env=env)
        return proc.pid is not None
    except Exception as exc:
        print(f"[agent-bridge] on_message_command failed: {exc}", flush=True)
        return False


def watch(config_path: Path, stop_event: Optional[threading.Event] = None) -> None:
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
        while not (stop_event and stop_event.is_set()):
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
                    # 'log' mode: emit a log line, do NOT run on_message_command
                    # (the consumer would mark messages read silently — destructive).
                    # The receiving agent is expected to be polled via Monitor or
                    # equivalent and call check_inbox itself.
                    if on_message == "log":
                        for m in new_msgs:
                            print(
                                f"[agent-bridge] {utc_now()} -- new {agent} message id={m.get('id')} session=...{(session_id or '')[-8:]}",
                                flush=True,
                            )
                        for m in new_msgs:
                            seen_ids.add(m["id"])
                        save_seen(state_path, {"seen_ids": list(seen_ids)[-500:]})
                        continue

                    # Notify
                    if on_message == "toast":
                        notify_windows_toast(agent, session_id, new_msgs)
                    else:
                        notify_terminal(agent, session_id, new_msgs)

                    # Optional command hook (e.g. wake Codex automation)
                    command_ok = False
                    if on_message_command:
                        command_ok = run_command_for_session(on_message_command, agent, session_id, new_msgs, inbox_path)

                    if not on_message_command or command_ok:
                        for m in new_msgs:
                            seen_ids.add(m["id"])
                        # Persist seen IDs (keep last 500 to avoid unbounded growth)
                        save_seen(state_path, {"seen_ids": list(seen_ids)[-500:]})

            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        print("\n[agent-bridge] Watcher stopped.", flush=True)

    print("[agent-bridge] Watcher exiting.", flush=True)


def _write_pid(pid_path: Path) -> None:
    pid_path.write_text(str(os.getpid()), encoding="utf-8")


def _kill_stale(pid_path: Path) -> None:
    """Kill any existing watcher process recorded in pid_path."""
    if not pid_path.exists():
        return
    try:
        old_pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pid_path.unlink(missing_ok=True)
        return
    if old_pid == os.getpid():
        return
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, old_pid)
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 0)
                ctypes.windll.kernel32.CloseHandle(handle)
                print(f"[agent-bridge] Killed stale watcher PID {old_pid}.", flush=True)
        else:
            import signal as _signal
            os.kill(old_pid, _signal.SIGTERM)
            print(f"[agent-bridge] Sent SIGTERM to stale watcher PID {old_pid}.", flush=True)
    except (ProcessLookupError, OSError):
        pass  # already gone
    pid_path.unlink(missing_ok=True)


def main() -> None:
    import signal
    import atexit

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

    # Single-instance enforcement via PID file next to the config
    pid_path = config_path.parent / "watcher.pid"
    _kill_stale(pid_path)
    _write_pid(pid_path)

    def _cleanup() -> None:
        try:
            if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(_cleanup)

    _stop = threading.Event()

    def _handle_signal(signum, frame):  # noqa: ANN001
        print(f"\n[agent-bridge] Watcher received signal {signum}, shutting down.", flush=True)
        _stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    watch(config_path, stop_event=_stop)
    _cleanup()


if __name__ == "__main__":
    main()
