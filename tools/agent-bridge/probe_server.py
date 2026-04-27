"""Manual MCP probe for agent_bridge server.py.

Spawns server.py as a child via stdio, sends initialize + tools/list +
tools/call (check_inbox), and captures all stdout/stderr.  Exposes any
exceptions/tracebacks the server emits when handling tool calls.

Run:
    py -3 probe_server.py
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

SERVER_DIR = Path(r"C:\!Layi Wkspc\MLV-App\.claude\worktrees\festive-boyd-integration\tools\agent-bridge")
STATE_DIR = Path(r"C:\Users\obabalola\.agent-bridge\state")


def main() -> int:
    print(f"[probe] spawning server.py with state-dir={STATE_DIR}")
    proc = subprocess.Popen(
        ["py", "-3", str(SERVER_DIR / "server.py"), "--state-dir", str(STATE_DIR)],
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
        except Exception as e:
            print(f"[probe] stderr pump error: {e}")

    threading.Thread(target=_stderr_pump, daemon=True).start()

    def send(msg: dict) -> None:
        data = (json.dumps(msg) + "\n").encode("utf-8")
        print(f"[probe -> server] {msg}")
        proc.stdin.write(data)
        proc.stdin.flush()

    def recv(timeout: float = 5.0) -> dict | None:
        # Naive newline-delimited reader with timeout
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
        except Exception as e:
            print(f"[probe] failed to parse: {line!r}  err={e}")
            return None

    # Step 1: initialize
    send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": "probe", "version": "0.1"},
            "capabilities": {},
        },
    })
    init_resp = recv(timeout=8.0)
    if init_resp is None:
        print("[probe] FAIL — no initialize response")
        return 1

    # Step 2: notifications/initialized (notification, no response expected)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # Step 3: tools/list
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    list_resp = recv(timeout=5.0)
    if list_resp:
        tools = list_resp.get("result", {}).get("tools", [])
        names = [t.get("name") for t in tools]
        print(f"[probe] tools/list: {len(tools)} tools, includes wait_inbox? {'wait_inbox' in names}")

    # Step 4: tools/call check_inbox (the one Codex says fails)
    send({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "check_inbox",
            "arguments": {"agent": "claude", "session_id": "mlv-app", "mark_read": False},
        },
    })
    call_resp = recv(timeout=10.0)
    if call_resp is None:
        print("[probe] FAIL — no response to check_inbox")
        time.sleep(1)
        if proc.poll() is None:
            proc.terminate()
        time.sleep(1)
        # Print collected stderr if any
        print("[probe] === STDERR DUMP ===")
        for line in stderr_lines[-30:]:
            print(f"  {line}")
        return 2

    # Step 5: tools/call wait_inbox (3s timeout, the new tool that triggered the crash)
    send({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "wait_inbox",
            "arguments": {"agent": "claude", "session_ids": ["mlv-app"], "timeout_seconds": 3, "mark_read": False},
        },
    })
    wait_resp = recv(timeout=10.0)
    if wait_resp is None:
        print("[probe] FAIL — no response to wait_inbox")

    # Step 6: tools/call send_to_peer (Codex reports this fails for them via MCP)
    send({
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
    })
    send_resp = recv(timeout=10.0)
    if send_resp is None:
        print("[probe] FAIL — no response to send_to_peer")
    else:
        print(f"[probe] send_to_peer response shape: {list(send_resp.get('result', {}).keys()) if 'result' in send_resp else send_resp}")

    print("\n[probe] === STDERR LINES ===")
    for line in stderr_lines:
        print(f"  {line}")

    # Cleanup
    proc.stdin.close()
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()

    return 0


if __name__ == "__main__":
    sys.exit(main())
