"""Safe manual MCP probe for agent_bridge server.py.

By default this probe uses a temporary state directory and only calls
read/non-destructive tools. Live mutation requires an explicit --mutate flag.

Examples:
    py -3 probe_server.py
    py -3 probe_server.py --state-dir %USERPROFILE%\\.agent-bridge\\state
    py -3 probe_server.py --state-dir %USERPROFILE%\\.agent-bridge\\state --mutate
    py -3 probe_server.py --bisect-payload-max 8192
"""
import argparse
import json
import subprocess
import sys
import tempfile
import queue
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
    parser.add_argument(
        "--bisect-payload-max",
        type=int,
        default=0,
        help=(
            "If >0, bisect the largest MCP tool argument payload accepted by the "
            "client/server path using a non-mutating classifier call."
        ),
    )
    return parser


def _send(proc: subprocess.Popen, msg: Dict[str, Any]) -> None:
    frame = (json.dumps(msg) + "\n").encode("utf-8")
    preview = json.dumps(msg)
    if len(preview) > 500:
        preview = preview[:500] + "...<truncated>"
    print(f"[probe -> server] {preview}")
    proc.stdin.write(frame)
    proc.stdin.flush()


def _recv(proc: subprocess.Popen, stdout_queue: "queue.Queue[bytes]", timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout
    line = bytearray()
    while time.time() < deadline:
        try:
            ch = stdout_queue.get(timeout=max(0.05, deadline - time.time()))
        except queue.Empty:
            if proc.poll() is not None:
                print(f"[probe] server exited code={proc.returncode}")
                return None
            continue
        if not ch:
            if proc.poll() is not None:
                print(f"[probe] server exited code={proc.returncode}")
                return None
            continue
        line.extend(ch)
        if ch == b"\n":
            break
    if not line or line[-1:] != b"\n":
        print("[probe] timed out waiting for response line")
        return None
    try:
        obj = json.loads(bytes(line).decode("utf-8"))
        print(f"[probe <- server] {json.dumps(obj)[:300]}")
        return obj
    except Exception as exc:
        print(f"[probe] failed to parse: line={bytes(line)!r} err={exc}")
        return None


def _tool_call(
    proc: subprocess.Popen,
    stdout_queue: "queue.Queue[bytes]",
    *,
    request_id: int,
    name: str,
    arguments: Dict[str, Any],
    timeout: float = 10.0,
) -> Optional[Dict[str, Any]]:
    _send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    return _recv(proc, stdout_queue, timeout=timeout)


def _bisect_payload_size(
    proc: subprocess.Popen,
    stdout_queue: "queue.Queue[bytes]",
    *,
    max_payload_bytes: int,
    first_request_id: int,
) -> int:
    low = 0
    high = max(0, int(max_payload_bytes))
    best = 0
    request_id = first_request_id
    while low <= high:
        mid = (low + high) // 2
        payload = "x" * mid
        response = _tool_call(
            proc,
            stdout_queue,
            request_id=request_id,
            name="classify_remote_authority_request",
            arguments={"from_agent": "claude", "text": payload or "x", "audit": False},
            timeout=15.0,
        )
        request_id += 1
        ok = bool(response and "error" not in response)
        print(f"[probe] payload_size={mid} result={'OK' if ok else 'FAIL'}")
        if ok:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    print(f"[probe] largest accepted payload size <= {max_payload_bytes}: {best} bytes")
    return request_id


def run_probe(*, server_dir: Path, state_dir: Path, mutate: bool, bisect_payload_max: int = 0) -> int:
    print(f"[probe] spawning server.py with state-dir={state_dir}")
    proc = subprocess.Popen(
        ["py", "-3", str(server_dir / "server.py"), "--state-dir", str(state_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    stderr_lines: list[str] = []
    stdout_queue: "queue.Queue[bytes]" = queue.Queue()

    def _stderr_pump() -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                stderr_lines.append(line)
                print(f"[probe stderr] {line}")
        except Exception as exc:
            print(f"[probe] stderr pump error: {exc}")

    def _stdout_pump() -> None:
        try:
            for raw in iter(lambda: proc.stdout.read(1), b""):
                stdout_queue.put(raw)
        except Exception as exc:
            print(f"[probe] stdout pump error: {exc}")
        finally:
            stdout_queue.put(b"")

    threading.Thread(target=_stderr_pump, daemon=True).start()
    threading.Thread(target=_stdout_pump, daemon=True).start()

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
        if _recv(proc, stdout_queue, timeout=8.0) is None:
            print("[probe] FAIL - no initialize response")
            return 1

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        list_resp = _recv(proc, stdout_queue, timeout=5.0)
        if list_resp:
            tools = list_resp.get("result", {}).get("tools", [])
            names = [tool.get("name") for tool in tools]
            print(f"[probe] tools/list: {len(tools)} tools, includes wait_inbox? {'wait_inbox' in names}")
            if bisect_payload_max and "classify_remote_authority_request" not in names:
                print("[probe] FAIL - classify_remote_authority_request is unavailable for payload bisection")
                return 5

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
        if _recv(proc, stdout_queue, timeout=10.0) is None:
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
        if _recv(proc, stdout_queue, timeout=10.0) is None:
            print("[probe] FAIL - no response to wait_inbox")
            return 3

        next_request_id = 5
        if bisect_payload_max:
            next_request_id = _bisect_payload_size(
                proc,
                stdout_queue,
                max_payload_bytes=bisect_payload_max,
                first_request_id=next_request_id,
            )

        if mutate:
            response = _tool_call(
                proc,
                stdout_queue,
                request_id=next_request_id,
                name="send_to_peer",
                arguments={
                    "from_agent": "claude",
                    "to_agent": "codex",
                    "message": "[[handoff:codex]]\nTYPE: SMOKE_TEST\nSUMMARY: probe send_to_peer via direct MCP\nbody: probe test, ignore",
                    "session_id": "mlv-app",
                },
                timeout=10.0,
            )
            if response is None:
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
        return run_probe(
            server_dir=server_dir,
            state_dir=Path(args.state_dir),
            mutate=args.mutate,
            bisect_payload_max=args.bisect_payload_max,
        )
    with tempfile.TemporaryDirectory(prefix="agent-bridge-probe-") as tmp:
        return run_probe(
            server_dir=server_dir,
            state_dir=Path(tmp) / "state",
            mutate=args.mutate,
            bisect_payload_max=args.bisect_payload_max,
        )


if __name__ == "__main__":
    sys.exit(main())
