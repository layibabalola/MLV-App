import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from server_wrapper import SERVER_WRAPPER_SELF_RESTART_EXIT_CODE


DEFAULT_SELF_RESTART_WINDOW_SECONDS = 10.0
DEFAULT_MAX_SELF_RESTARTS_PER_WINDOW = 3


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
        rc = call_fn([sys.executable, str(wrapper), *argv])
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
