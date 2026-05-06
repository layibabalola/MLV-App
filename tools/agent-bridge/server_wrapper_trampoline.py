import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from server_wrapper import SERVER_WRAPPER_SELF_RESTART_EXIT_CODE


DEFAULT_SELF_RESTART_WINDOW_SECONDS = 10.0
DEFAULT_MAX_SELF_RESTARTS_PER_WINDOW = 3


def _clear_inherited_host_env(env: dict[str, str]) -> None:
    for key in list(env):
        if (
            key == "AGENT_BRIDGE_TRAMPOLINE_PARENT_PID"
            or key.startswith("AGENT_BRIDGE_MCP_HOST_")
            or key.startswith("AGENT_BRIDGE_HOST_")
        ):
            env.pop(key, None)


def _short_hash(value: object) -> str:
    return hashlib.sha256(str(value or "").casefold().encode("utf-8", errors="replace")).hexdigest()[:16]


def _host_env_for_parent(parent_pid: int) -> dict[str, str]:
    env = {"AGENT_BRIDGE_TRAMPOLINE_PARENT_PID": str(parent_pid), "AGENT_BRIDGE_MCP_HOST_PID": str(parent_pid)}
    if sys.platform != "win32":
        return env
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process -Filter \"ProcessId = %s\" | "
            "Select-Object ProcessId,Name,CommandLine,ExecutablePath,CreationDate | "
            "ConvertTo-Json -Compress"
        )
        % int(parent_pid),
    ]
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "timeout": 5,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(command, **kwargs)
        if proc.returncode != 0 or not proc.stdout.strip():
            return env
        row = json.loads(proc.stdout)
        if not isinstance(row, dict):
            return env
    except Exception:
        return env
    if row.get("Name"):
        env["AGENT_BRIDGE_MCP_HOST_PROCESS_NAME"] = str(row.get("Name"))
    if row.get("ExecutablePath"):
        env["AGENT_BRIDGE_MCP_HOST_EXECUTABLE_PATH"] = str(row.get("ExecutablePath"))
    if row.get("CreationDate"):
        env["AGENT_BRIDGE_MCP_HOST_CREATION_DATE"] = str(row.get("CreationDate"))
    if row.get("CommandLine"):
        env["AGENT_BRIDGE_MCP_HOST_COMMAND_HASH"] = _short_hash(row.get("CommandLine"))
    return env


def run_trampoline(
    argv: list[str],
    *,
    wrapper_path: Optional[Path] = None,
    call_fn: Callable[[list[str]], int] = subprocess.call,
    now_fn: Callable[[], float] = time.monotonic,
) -> int:
    wrapper = wrapper_path or Path(__file__).with_name("server_wrapper.py")
    restart_times: list[float] = []
    while True:
        command = [sys.executable, str(wrapper), *argv]
        child_env = os.environ.copy()
        _clear_inherited_host_env(child_env)
        child_env.update(_host_env_for_parent(os.getppid()))
        try:
            rc = call_fn(command, env=child_env)  # type: ignore[misc]
        except TypeError:
            rc = call_fn(command)
        if rc != SERVER_WRAPPER_SELF_RESTART_EXIT_CODE:
            return rc

        now = now_fn()
        cutoff = now - DEFAULT_SELF_RESTART_WINDOW_SECONDS
        restart_times = [stamp for stamp in restart_times if stamp >= cutoff]
        restart_times.append(now)
        if len(restart_times) > DEFAULT_MAX_SELF_RESTARTS_PER_WINDOW:
            print(
                "agent-bridge server wrapper trampoline aborted restart loop "
                "(%s restarts within %.1fs)"
                % (len(restart_times), DEFAULT_SELF_RESTART_WINDOW_SECONDS),
                file=sys.stderr,
                flush=True,
            )
            return 1


def main() -> None:
    raise SystemExit(run_trampoline(sys.argv[1:]))


if __name__ == "__main__":
    main()
