"""Safe manual MCP probe for agent_bridge server.py.

By default this probe uses a temporary state directory and only calls
read/non-destructive tools. Live mutation requires an explicit --mutate flag.

Examples:
    py -3 probe_server.py
    py -3 probe_server.py --state-dir %USERPROFILE%\\.agent-bridge\\state
    py -3 probe_server.py --state-dir %USERPROFILE%\\.agent-bridge\\state --mutate
"""
import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_SERVER_DIR = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe agent_bridge MCP server over stdio.")
    parser.add_argument("--server-dir", default=str(DEFAULT_SERVER_DIR), help="Directory containing server.py")
    parser.add_argument("--state-dir", help="Bridge state directory. Defaults to a temporary directory.")
    parser.add_argument(
        "--mutate",
        action="store_true",
        help="Allow mutating calls such as send_to_peer against the selected state-dir.",
    )
    return parser


def _send(proc: subprocess.Popen, msg: Dict[str, Any]) -> None:
    data = (json.dumps(msg) + "\n").encode("utf-8")
    print(f"[probe -> server] {msg}")
    proc.stdin.write(data)
    proc.stdin.flush()


def _recv(proc: subprocess.Popen, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    start = time.time()
    line = b""
    while time.time() - start < timeout:
        ch = proc.stdout.read(1)
        if not ch:
            if proc.poll() is not None:
                print(f"[probe] server exited code={proc.returncode}")
                return None
            time.sleep(0.05)
            continue
        line += ch
        if ch == b"\n":
            break
    if not line:
        return None
    try:
        obj = json.loads(line.decode("utf-8"))
        print(f"[probe <- server] {json.dumps(obj)[:300]}")
        return obj
    except Exception as exc:
        print(f"[probe] failed to parse: {line!r} err={exc}")
        return None


def run_probe(*, server_dir: Path, state_dir: Path, mutate: bool) -> int:
    print(f"[probe] spawning server.py with state-dir={state_dir}")
    proc = subprocess.Popen(
        ["py", "-3", str(server_dir / "server.py"), "--state-dir", str(state_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    stderr_lines: list[str] = []

    def _stderr_pump() -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                stderr_lines.append(line)
                print(f"[probe stderr] {line}")
        except Exception as exc:
            print(f"[probe] stderr pump error: {exc}")

    threading.Thread(target=_stderr_pump, daemon=True).start()

    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "clientInfo": {"name": "probe", "version": "0.1"},
                    "capabilities": {},
                },
            },
        )
        if _recv(proc, timeout=8.0) is None:
            print("[probe] FAIL - no initialize response")
            return 1

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        list_resp = _recv(proc, timeout=5.0)
        if list_resp:
            tools = list_resp.get("result", {}).get("tools", [])
            names = [tool.get("name") for tool in tools]
            print(f"[probe] tools/list: {len(tools)} tools, includes wait_inbox? {'wait_inbox' in names}")

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "check_inbox",
                    "arguments": {"agent": "claude", "session_id": "mlv-app", "mark_read": False},
                },
            },
        )
        if _recv(proc, timeout=10.0) is None:
            print("[probe] FAIL - no response to check_inbox")
            return 2

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "wait_inbox",
                    "arguments": {
                        "agent": "claude",
                        "session_ids": ["mlv-app"],
                        "timeout_seconds": 3,
                        "mark_read": False,
                    },
                },
            },
        )
        if _recv(proc, timeout=10.0) is None:
            print("[probe] FAIL - no response to wait_inbox")
            return 3

        if mutate:
            _send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "send_to_peer",
                        "arguments": {
                            "from_agent": "claude",
                            "to_agent": "codex",
                            "message": "[[handoff:codex]]\nTYPE: SMOKE_TEST\nSUMMARY: probe send_to_peer via direct MCP\nbody: probe test, ignore",
                            "session_id": "mlv-app",
                        },
                    },
                },
            )
            if _recv(proc, timeout=10.0) is None:
                print("[probe] FAIL - no response to send_to_peer")
                return 4
        else:
            print("[probe] skipping send_to_peer; pass --mutate to permit writes")

        print("\n[probe] === STDERR LINES ===")
        for line in stderr_lines:
            print(f"  {line}")
        return 0
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    server_dir = Path(args.server_dir)
    if args.state_dir:
        return run_probe(server_dir=server_dir, state_dir=Path(args.state_dir), mutate=args.mutate)
    with tempfile.TemporaryDirectory(prefix="agent-bridge-probe-") as tmp:
        return run_probe(server_dir=server_dir, state_dir=Path(tmp) / "state", mutate=args.mutate)


if __name__ == "__main__":
    sys.exit(main())
