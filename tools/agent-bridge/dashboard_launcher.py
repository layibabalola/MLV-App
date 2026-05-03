#!/usr/bin/env python3
"""
Standalone launcher for the Agent Bridge dashboard.

Default mode runs the dashboard in the foreground for tests and CLI use.
Background mode is a singleton supervisor: if a healthy dashboard is already
running it opens that instance; otherwise it starts one, writes a runtime file,
and health-checks/restarts the local server until stopped.

Usage:
  python dashboard_launcher.py [--bridge-root PATH] [--project PROJECT] [--port PORT]
  python dashboard_launcher.py --background [--bridge-root PATH]
"""

import argparse
import json
import os
import signal
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).parent))

from agent_bridge import AgentBridge  # noqa: E402
from dashboard_server import DashboardServerHandle, start_dashboard_server  # noqa: E402


RUNTIME_FILENAME = "dashboard-launcher.runtime.json"
RUNTIME_SCHEMA_VERSION = 1
DEFAULT_HEALTH_INTERVAL_SECONDS = 30.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _runtime_path(bridge_root: Path) -> Path:
    return bridge_root / "state" / RUNTIME_FILENAME


def _read_runtime(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_runtime(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%s.tmp" % (path.name, os.getpid()))
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _url_with_query(base_url: str, *, token: str, project: str) -> str:
    return "%s/?%s" % (base_url.rstrip("/"), urlencode({"token": token, "project": project}))


def _open_browser(url: str, *, no_browser: bool) -> None:
    if no_browser:
        return
    webbrowser.open(url)


def _runtime_browser_url(runtime: Dict[str, Any], *, project: str) -> Optional[str]:
    url = str(runtime.get("url") or "").strip()
    token = str(runtime.get("token") or "").strip()
    if not url or not token:
        return None
    return _url_with_query(url, token=token, project=project)


def _dashboard_healthy(runtime: Dict[str, Any], *, timeout_seconds: float = 3.0) -> bool:
    url = str(runtime.get("url") or "").rstrip("/")
    token = str(runtime.get("token") or "")
    if not url or not token:
        return False
    health_url = "%s/api/overview?%s" % (url, urlencode({"token": token}))
    try:
        with urllib.request.urlopen(health_url, timeout=timeout_seconds) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def _runtime_payload(
    *,
    bridge_root: Path,
    handle: DashboardServerHandle,
    project: str,
    health_interval_seconds: float,
    status: str = "running",
) -> Dict[str, Any]:
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "status": status,
        "pid": os.getpid(),
        "bridge_root": str(bridge_root),
        "state_dir": str(bridge_root / "state"),
        "url": handle.url,
        "token": handle.token,
        "csrf_token": handle.csrf_token,
        "project": project,
        "health_interval_seconds": health_interval_seconds,
        "updated_at": _utc_now(),
    }


def _start_dashboard(
    *,
    bridge_root: Path,
    project: str,
    port: int,
    health_interval_seconds: float,
    runtime_path: Path,
) -> DashboardServerHandle:
    bridge = AgentBridge(bridge_root / "state")
    handle = start_dashboard_server(
        bridge,
        port=port,
        default_agent="codex",
        default_project=project,
    )
    _write_runtime(
        runtime_path,
        _runtime_payload(
            bridge_root=bridge_root,
            handle=handle,
            project=project,
            health_interval_seconds=health_interval_seconds,
        ),
    )
    return handle


def _serve_with_health_loop(args: argparse.Namespace, bridge_root: Path) -> int:
    runtime_path = _runtime_path(bridge_root)
    existing = _read_runtime(runtime_path)
    if existing and _dashboard_healthy(existing):
        url = _runtime_browser_url(existing, project=args.project)
        if url:
            print("Agent Bridge Dashboard: %s" % url, flush=True)
            print("Reusing existing dashboard supervisor.", flush=True)
            _open_browser(url, no_browser=args.no_browser)
            return 0

    handle = _start_dashboard(
        bridge_root=bridge_root,
        project=args.project,
        port=args.port,
        health_interval_seconds=args.health_interval_seconds,
        runtime_path=runtime_path,
    )
    url = _url_with_query(handle.url, token=handle.token, project=args.project)
    print("Agent Bridge Dashboard: %s" % url, flush=True)
    print("Running in background supervisor mode.", flush=True)
    _open_browser(url, no_browser=args.no_browser)

    stop_requested = False
    current_ref: Dict[str, DashboardServerHandle] = {"handle": handle}

    def _shutdown(signum, frame) -> None:  # noqa: ANN001
        nonlocal stop_requested
        stop_requested = True
        current_ref["handle"].stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    current = current_ref["handle"]
    while not stop_requested:
        time.sleep(max(float(args.health_interval_seconds), 1.0))
        if current.shutdown_requested:
            break
        if current.thread.is_alive() and _dashboard_healthy(
            _runtime_payload(
                bridge_root=bridge_root,
                handle=current,
                project=args.project,
                health_interval_seconds=args.health_interval_seconds,
            )
        ):
            payload = _runtime_payload(
                bridge_root=bridge_root,
                handle=current,
                project=args.project,
                health_interval_seconds=args.health_interval_seconds,
            )
            payload["last_health_check_at"] = _utc_now()
            payload["last_health_status"] = "ok"
            _write_runtime(runtime_path, payload)
            continue

        try:
            current.stop()
        except OSError:
            pass
        current = _start_dashboard(
            bridge_root=bridge_root,
            project=args.project,
            port=args.port,
            health_interval_seconds=args.health_interval_seconds,
            runtime_path=runtime_path,
        )
        current_ref["handle"] = current

    try:
        current.stop()
    except OSError:
        pass
    stopped_payload = _runtime_payload(
        bridge_root=bridge_root,
        handle=current,
        project=args.project,
        health_interval_seconds=args.health_interval_seconds,
        status="stopped",
    )
    stopped_payload["stopped_at"] = _utc_now()
    _write_runtime(runtime_path, stopped_payload)
    return 0


def _serve_foreground(args: argparse.Namespace, bridge_root: Path) -> int:
    handle = _start_dashboard(
        bridge_root=bridge_root,
        project=args.project,
        port=args.port,
        health_interval_seconds=args.health_interval_seconds,
        runtime_path=_runtime_path(bridge_root),
    )
    url = _url_with_query(handle.url, token=handle.token, project=args.project)
    print("Agent Bridge Dashboard: %s" % url, flush=True)
    print("Press Ctrl+C, use the dashboard Stop button, or terminate the process to stop.", flush=True)

    _open_browser(url, no_browser=args.no_browser)

    def _shutdown(signum, frame) -> None:  # noqa: ANN001
        print("\nShutting down dashboard server...", flush=True)
        handle.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while handle.thread.is_alive() and not handle.shutdown_requested:
        time.sleep(1)
    handle.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Bridge Dashboard Launcher")
    parser.add_argument(
        "--bridge-root",
        default=None,
        help="Bridge root directory (default: %%USERPROFILE%%\\.agent-bridge)",
    )
    parser.add_argument(
        "--project",
        default="mlv-app",
        help="Default project filter (default: mlv-app)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind (default: 0 = OS-assigned)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start server but do not open a browser",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Run as a singleton background supervisor or reuse an existing healthy instance",
    )
    parser.add_argument(
        "--health-interval-seconds",
        type=float,
        default=DEFAULT_HEALTH_INTERVAL_SECONDS,
        help="Background health check interval (default: 30 seconds)",
    )
    args = parser.parse_args()

    bridge_root = Path(args.bridge_root) if args.bridge_root else Path.home() / ".agent-bridge"
    if args.background:
        return _serve_with_health_loop(args, bridge_root)
    return _serve_foreground(args, bridge_root)


if __name__ == "__main__":
    raise SystemExit(main())
