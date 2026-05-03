"""SendKeys-free Codex wake helper backed by a sibling codex app-server.

This is intentionally a small command-line helper because watcher.py already
owns wake retry, breaker, and receipt verification. The helper only starts a
local app-server, targets the known Codex Desktop thread id, and asks Codex to
check its bridge inbox through MCP.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


NPM_FALLBACK = (
    Path.home()
    / "AppData/Roaming/npm/node_modules/@openai/codex/node_modules"
    / "@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/codex/codex.exe"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def find_codex_exe(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    on_path = shutil.which("codex")
    if on_path and not on_path.lower().endswith((".cmd", ".ps1", ".bat")):
        return on_path
    if NPM_FALLBACK.exists():
        return str(NPM_FALLBACK)
    if on_path:
        return on_path
    raise RuntimeError("codex executable not found")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def resolve_listen_url(value: Optional[str]) -> str:
    url = (value or "").strip()
    if not url:
        return "ws://127.0.0.1:%d" % free_port()
    parsed = urlparse(url)
    if parsed.scheme != "ws":
        raise ValueError("listen URL must use ws://")
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("listen URL must bind loopback only")
    port = parsed.port
    if port is None:
        raise ValueError("listen URL must include a port")
    if port == 0:
        port = free_port()
    host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
    return "ws://%s:%d" % (host, port)


def build_wake_prompt(
    *,
    message_id: str,
    message_ids: str,
    message_count: str,
    project: str,
    session_id: str,
) -> str:
    msg = message_id or "unknown"
    ids = message_ids or "[]"
    count = message_count or "1"
    return (
        "Bridge wake [msg=%s]: use the Agent Bridge MCP server to check your "
        "Codex bridge inbox for unread messages.\n\n"
        "Call the bridge inbox/check tool with these exact parameters when the "
        "tool supports them:\n"
        "- agent: codex\n"
        "- session_id: %s\n"
        "- include_parents: true\n"
        "- mark_read: true\n\n"
        "Context:\n"
        "- project: %s\n"
        "- triggering_message_count: %s\n"
        "- triggering_message_ids: %s\n\n"
        "Surface any unread bridge messages in your final answer. Do not use "
        "shell commands for inbox access, and do not answer this wake sentence "
        "directly without checking the bridge inbox."
    ) % (msg, session_id, project, count, ids)


async def wait_tcp(port: int, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            await asyncio.sleep(0.2)
    raise TimeoutError("app-server TCP not ready on port %d" % port)


class AppServerClient:
    def __init__(self, ws: Any):
        self.ws = ws
        self.next_id = 1
        self.pending: Dict[str, asyncio.Future] = {}
        self.recv_task: Optional[asyncio.Task] = None
        self.turn_started = False
        self.turn_completed = False
        self.agent_messages: list[str] = []
        self.mcp_tool_events: list[Dict[str, Any]] = []
        self._buffers: Dict[str, list[str]] = {}

    async def start(self) -> None:
        self.recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                await self._dispatch(msg)
        except Exception as exc:
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("WebSocket receive failed: %s" % exc))

    async def _dispatch(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method")
        msg_id = msg.get("id")
        if method and msg_id is None:
            self._on_notification(str(method), msg.get("params") or {})
            return
        if method and msg_id is not None:
            await self.ws.send(
                json.dumps({"id": msg_id, "error": {"code": -32601, "message": "wake helper denies server request"}})
            )
            return
        if msg_id is not None:
            fut = self.pending.pop(str(msg_id), None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(json.dumps(msg["error"], ensure_ascii=False)))
                else:
                    fut.set_result(msg.get("result"))

    def _on_notification(self, method: str, params: Dict[str, Any]) -> None:
        if method == "turn/started":
            self.turn_started = True
            return
        if method == "turn/completed":
            self.turn_completed = True
            return
        if method == "item/started":
            item = params.get("item") or {}
            item_type = str(item.get("type") or "")
            if item_type == "agentMessage":
                self._buffers[str(item.get("id") or "")] = []
            elif "mcp" in item_type.lower() or "tool" in item_type.lower():
                self._record_tool("started", item, method)
            return
        if method == "item/agentMessage/delta":
            item_id = str(params.get("itemId") or "")
            delta = params.get("delta")
            if delta:
                self._buffers.setdefault(item_id, []).append(str(delta))
            return
        if method == "item/completed":
            item = params.get("item") or {}
            item_type = str(item.get("type") or "")
            if item_type == "agentMessage":
                text = "".join(
                    part.get("text", "")
                    for part in (item.get("content") or [])
                    if isinstance(part, dict) and part.get("type") == "text"
                )
                if not text:
                    text = "".join(self._buffers.get(str(item.get("id") or ""), []))
                if text:
                    self.agent_messages.append(text)
            elif "mcp" in item_type.lower() or "tool" in item_type.lower():
                self._record_tool("completed", item, method)
            return
        if method == "item/mcpToolCall/progress":
            self.mcp_tool_events.append({"phase": "progress", "method": method, "itemId": params.get("itemId")})

    def _record_tool(self, phase: str, item: Dict[str, Any], method: str) -> None:
        self.mcp_tool_events.append(
            {
                "phase": phase,
                "method": method,
                "itemId": item.get("id"),
                "itemType": item.get("type"),
                "server": item.get("server") or item.get("serverName"),
                "tool": item.get("tool") or item.get("toolName") or item.get("name"),
            }
        )

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Any:
        req_id = self.next_id
        self.next_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[str(req_id)] = fut
        await self.ws.send(json.dumps({"id": req_id, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        await self.ws.send(json.dumps({"method": method, "params": params or {}}))

    async def wait_turn_completed(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while not self.turn_completed:
            if time.time() >= deadline:
                raise TimeoutError("turn/completed not observed before timeout")
            await asyncio.sleep(0.25)
        await asyncio.sleep(0.5)


def stop_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
            return
        except Exception:
            pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def maybe_redraw_deeplink(thread_id: str) -> None:
    if os.name == "nt":
        os.startfile("codex://threads/%s" % thread_id)  # type: ignore[attr-defined]


async def run_wake(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("missing dependency: install websockets for app_server wake") from exc

    listen_url = resolve_listen_url(args.listen)
    port = int(urlparse(listen_url).port or 0)
    codex_exe = find_codex_exe(args.codex_exe)
    prompt = build_wake_prompt(
        message_id=args.message_id or os.environ.get("BRIDGE_MESSAGE_ID", ""),
        message_ids=args.message_ids or os.environ.get("BRIDGE_MESSAGE_IDS", ""),
        message_count=args.message_count or os.environ.get("BRIDGE_MESSAGE_COUNT", ""),
        project=args.project,
        session_id=args.session_id,
    )
    summary: Dict[str, Any] = {
        "ok": False,
        "started_at": utc_now(),
        "listen": listen_url,
        "thread_id": args.thread_id,
        "session_id": args.session_id,
        "project": args.project,
        "message_id": args.message_id or os.environ.get("BRIDGE_MESSAGE_ID", ""),
        "turn_started": False,
        "turn_completed": False,
        "mcp_tool_event_count": 0,
        "agent_message_count": 0,
    }

    command = [codex_exe, "app-server", "--listen", listen_url]
    if args.analytics == "enabled":
        command.append("--analytics-default-enabled")
    elif args.analytics == "disabled":
        command.append("--analytics-default-disabled")

    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    summary["app_server_pid"] = proc.pid
    try:
        await wait_tcp(port, timeout=min(20.0, float(args.timeout_seconds)))
        async with websockets.connect(listen_url, max_size=None, ping_interval=None) as ws:
            client = AppServerClient(ws)
            await client.start()
            await client.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "agent-bridge-app-server-wake",
                        "title": "Agent Bridge App Server Wake",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
                timeout=15.0,
            )
            await client.notify("initialized", {})
            if not args.skip_mcp_preflight:
                status = await client.request("mcpServerStatus/list", {"limit": 50}, timeout=15.0)
                if "agent_bridge" not in json.dumps(status, ensure_ascii=False):
                    raise RuntimeError("agent_bridge MCP server is not visible to sibling app-server")

            await client.request("thread/resume", {"threadId": args.thread_id}, timeout=20.0)
            turn = await client.request(
                "turn/start",
                {
                    "threadId": args.thread_id,
                    "cwd": str(Path(args.repo_root).resolve()),
                    "approvalPolicy": args.approval_policy,
                    "sandbox": args.sandbox,
                    "input": [{"type": "text", "text": prompt}],
                },
                timeout=30.0,
            )
            summary["turn_start_result"] = turn
            await client.wait_turn_completed(timeout=float(args.timeout_seconds))
            if args.redraw_deeplink:
                maybe_redraw_deeplink(args.thread_id)
            summary["turn_started"] = client.turn_started
            summary["turn_completed"] = client.turn_completed
            summary["mcp_tool_event_count"] = len(client.mcp_tool_events)
            summary["agent_message_count"] = len(client.agent_messages)
            summary["ok"] = bool(client.turn_completed and client.mcp_tool_events)
            if not summary["ok"]:
                summary["error"] = "turn completed without observed agent_bridge MCP tool progress"
            return summary
    finally:
        stop_process_tree(proc)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wake Codex through a sibling app-server without SendKeys.")
    parser.add_argument("--thread-id", required=True, help="Codex Desktop thread id to resume and turn/start.")
    parser.add_argument("--session-id", required=True, help="Codex bridge session id to check.")
    parser.add_argument("--project", required=True, help="Bridge rendezvous/project bucket.")
    parser.add_argument("--repo-root", default=str(Path.cwd()), help="Workspace root for the Codex turn.")
    parser.add_argument("--message-id", help="Triggering bridge inbox row id; defaults to BRIDGE_MESSAGE_ID.")
    parser.add_argument("--message-ids", help="Triggering bridge row ids JSON; defaults to BRIDGE_MESSAGE_IDS.")
    parser.add_argument("--message-count", help="Triggering bridge message count; defaults to BRIDGE_MESSAGE_COUNT.")
    parser.add_argument("--listen", help="Loopback ws:// listen URL. Port 0 is replaced with a free port.")
    parser.add_argument("--codex-exe", help="Explicit codex executable path.")
    parser.add_argument("--timeout-seconds", type=float, default=75.0, help="Turn completion timeout.")
    parser.add_argument("--approval-policy", default="never")
    parser.add_argument("--sandbox", default="danger-full-access")
    parser.add_argument("--analytics", choices=("omit", "enabled", "disabled"), default="omit")
    parser.add_argument("--skip-mcp-preflight", action="store_true")
    parser.add_argument("--redraw-deeplink", action="store_true", help="Open codex://threads/<id> after injection.")
    parser.add_argument("--dry-run", action="store_true", help="Print the wake envelope without starting app-server.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        prompt = build_wake_prompt(
            message_id=args.message_id or os.environ.get("BRIDGE_MESSAGE_ID", ""),
            message_ids=args.message_ids or os.environ.get("BRIDGE_MESSAGE_IDS", ""),
            message_count=args.message_count or os.environ.get("BRIDGE_MESSAGE_COUNT", ""),
            project=args.project,
            session_id=args.session_id,
        )
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "listen": resolve_listen_url(args.listen),
                        "thread_id": args.thread_id,
                        "session_id": args.session_id,
                        "project": args.project,
                        "prompt": prompt,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        summary = asyncio.run(run_wake(args))
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0 if summary.get("ok") else 1
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "classification": "config"}, ensure_ascii=False), file=sys.stderr)
        return 3
    except RuntimeError as exc:
        message = str(exc)
        permanent = "agent_bridge MCP server is not visible" in message or "missing dependency" in message
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
        return 3 if permanent else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
