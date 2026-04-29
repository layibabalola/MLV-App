import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from core.paths import BridgeRootMovedError, ensure_bridge_root_manifest, resolve_bridge_paths
from core.storage import append_jsonl
from core.runtime import build_runtime_breadcrumb


def build_server_command(*, server_path: Path, state_dir: Path, max_hops: int, passthrough: List[str]) -> List[str]:
    return [
        sys.executable,
        str(server_path),
        "--state-dir",
        str(state_dir),
        "--max-hops",
        str(max_hops),
        *passthrough,
    ]


def audit_wrapper_launch(*, state_dir: Path, command: List[str]) -> None:
    event = build_runtime_breadcrumb(state_dir=state_dir, role="mcp_server_wrapper", command=command)
    event.update({"action": "mcp_server_wrapper_launch", "accepted": True})
    append_jsonl(Path(state_dir) / "messages.jsonl", event)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolver-aware launcher for agent-bridge server.py. Keeps MCP stdin/stdout byte-transparent."
    )
    parser.add_argument("--bridge-root", help="Bridge root directory; preferred for Desktop MCP configs")
    parser.add_argument("--state-dir", help="Legacy bridge state directory")
    parser.add_argument("--max-hops", type=int, default=8, help="Maximum accepted relays per session")
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="Print the resolved server.py command and exit. Do not use in Desktop MCP config.",
    )
    args, passthrough = parser.parse_known_args(argv)
    args.passthrough = passthrough
    return args


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    try:
        paths = resolve_bridge_paths(
            bridge_root=Path(args.bridge_root) if args.bridge_root else None,
            state_dir=Path(args.state_dir) if args.state_dir else None,
        )
        if args.bridge_root:
            ensure_bridge_root_manifest(paths, reason="mcp_server_wrapper")
    except BridgeRootMovedError as exc:
        print(
            "agent-bridge root moved: %s -> %s. Update Desktop MCP config to --bridge-root %s"
            % (exc.root, exc.target, exc.target),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
    except Exception as exc:
        print("agent-bridge server wrapper failed before MCP startup: %s" % exc, file=sys.stderr, flush=True)
        raise SystemExit(2)

    server_path = Path(__file__).with_name("server.py")
    command = build_server_command(
        server_path=server_path,
        state_dir=paths.state_dir,
        max_hops=args.max_hops,
        passthrough=list(args.passthrough),
    )
    if args.print_command:
        print(" ".join(command))
        return

    try:
        audit_wrapper_launch(state_dir=paths.state_dir, command=command)
    except Exception as exc:
        print("agent-bridge server wrapper audit failed: %s" % exc, file=sys.stderr, flush=True)

    # subprocess.run rather than os.execv: on Windows, os.execv does not quote
    # argv elements containing spaces, so a server.py path under "C:\!Layi Wkspc\..."
    # gets re-tokenized and Python fails with `can't find '__main__' module in '...'`.
    completed = subprocess.run(command)
    sys.exit(completed.returncode)


if __name__ == "__main__":
    main()
