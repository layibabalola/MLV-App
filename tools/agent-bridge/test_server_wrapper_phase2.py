import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import json
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.storage import read_jsonl
from server_wrapper import SupervisorConfig, run_supervisor


SERVER_SCRIPT = """\
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--state-dir")
parser.add_argument("--max-hops")
parser.add_argument("--mode", default="echo")
parser.add_argument("--launch-log", required=True)
parser.add_argument("--tool-signature-file")
args, _ = parser.parse_known_args()

with open(args.launch_log, "a", encoding="utf-8", newline="\\n") as handle:
    handle.write(str(os.getpid()) + "\\n")
    handle.flush()

if args.tool_signature_file:
    tool_signature_path = args.tool_signature_file
    with open(tool_signature_path, "r", encoding="utf-8") as handle:
        signature = handle.read().strip()
    manifest_path = os.path.join(args.state_dir, "tool-manifest.json")
    with open(manifest_path, "w", encoding="utf-8", newline="\\n") as handle:
        json.dump(
            {
                "schema_version": 1,
                "generated_at": "2026-04-29T00:00:00+00:00",
                "server_pid": os.getpid(),
                "tool_count": 1,
                "tool_names": ["demo_" + signature],
                "signature": signature,
                "tools": [{"name": "demo_" + signature}],
            },
            handle,
            sort_keys=True,
        )
        handle.write("\\n")

sys.stdout.buffer.write(("READY %s\\n" % os.getpid()).encode("ascii"))
sys.stdout.buffer.flush()

if args.mode == "crash":
    sys.exit(7)

while True:
    chunk = os.read(sys.stdin.fileno(), 65536)
    if not chunk:
        break
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
"""


class QueueInputStream:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._buffer = bytearray()
        self._closed = False

    def push(self, data: bytes) -> None:
        with self._condition:
            self._buffer.extend(data)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def read(self, size: int) -> bytes:
        with self._condition:
            while not self._buffer and not self._closed:
                self._condition.wait(timeout=0.1)
            if not self._buffer and self._closed:
                return b""
            chunk = bytes(self._buffer[:size])
            del self._buffer[:size]
            return chunk


class BufferOutputStream:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._buffer = bytearray()

    def write(self, data: bytes) -> int:
        with self._condition:
            self._buffer.extend(data)
            self._condition.notify_all()
        return len(data)

    def flush(self) -> None:
        return

    def snapshot(self) -> bytes:
        with self._condition:
            return bytes(self._buffer)

    def wait_for(self, needle: bytes, timeout: float = 2.0) -> bytes:
        deadline = time.time() + timeout
        with self._condition:
            while needle not in self._buffer:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise AssertionError("timed out waiting for %r in %r" % (needle, bytes(self._buffer)))
                self._condition.wait(timeout=remaining)
            return bytes(self._buffer)


class SupervisorHarness:
    def __init__(self, tempdir: Path, *, mode: str = "echo", watch_count: int = 1, tool_signature: Optional[str] = None) -> None:
        self.root = tempdir
        self.state_dir = self.root / "bridge-root" / "state"
        self.state_dir.mkdir(parents=True)
        self.server_script = self.root / "fake_server.py"
        self.server_script.write_text(SERVER_SCRIPT, encoding="utf-8")
        self.launch_log = self.root / "launches.log"
        self.tool_signature_file: Optional[Path] = None
        if tool_signature is not None:
            self.tool_signature_file = self.root / "tool-signature.txt"
            self.tool_signature_file.write_text(tool_signature, encoding="utf-8")
        self.watch_files = [self.root / ("watch_%s.py" % index) for index in range(watch_count)]
        for index, path in enumerate(self.watch_files):
            path.write_text("# %s\\n" % index, encoding="utf-8")

        self.stdin_stream = QueueInputStream()
        self.stdout_stream = BufferOutputStream()
        self.result: Dict[str, object] = {}
        self.thread = threading.Thread(target=self._run, args=(mode,), daemon=True)
        self.thread.start()

    def _run(self, mode: str) -> None:
        try:
            command = [
                sys.executable,
                str(self.server_script),
                "--state-dir",
                str(self.state_dir),
                "--max-hops",
                "8",
                "--mode",
                mode,
                "--launch-log",
                str(self.launch_log),
            ]
            if self.tool_signature_file is not None:
                command.extend(["--tool-signature-file", str(self.tool_signature_file)])
            self.result["exit_code"] = run_supervisor(
                command=command,
                state_dir=self.state_dir,
                watch_paths=self.watch_files,
                config=SupervisorConfig(
                    poll_interval_seconds=0.05,
                    debounce_seconds=0.05,
                    idle_seconds=0.1,
                    terminate_timeout_seconds=0.5,
                    restart_window_seconds=0.8,
                    max_restarts_per_window=4,
                    chunk_size=4096,
                    loop_sleep_seconds=0.01,
                ),
                stdin_stream=self.stdin_stream,
                stdout_stream=self.stdout_stream,
                stderr_target=subprocess.DEVNULL,
            )
        except BaseException as exc:
            self.result["error"] = exc

    def cleanup(self) -> None:
        self.stdin_stream.close()
        self.thread.join(timeout=3.0)

    def wait_for_launch_count(self, expected: int, timeout: float = 3.0) -> List[int]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            launches = self.launch_pids()
            if len(launches) >= expected:
                return launches
            time.sleep(0.02)
        raise AssertionError("timed out waiting for %s launch(es); saw %s" % (expected, self.launch_pids()))

    def wait_for_exit(self, timeout: float = 3.0) -> int:
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            raise AssertionError("supervisor thread did not exit")
        error = self.result.get("error")
        if error is not None:
            raise AssertionError("supervisor raised %r" % (error,))
        exit_code = self.result.get("exit_code")
        if not isinstance(exit_code, int):
            raise AssertionError("missing exit code: %r" % (self.result,))
        return exit_code

    def launch_pids(self) -> List[int]:
        if not self.launch_log.exists():
            return []
        return [int(line.strip()) for line in self.launch_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def touch_watch_files(self, indexes: Optional[List[int]] = None) -> None:
        targets = indexes if indexes is not None else list(range(len(self.watch_files)))
        for index in targets:
            self.watch_files[index].write_text("# touched %s\\n" % time.time_ns(), encoding="utf-8")

    def audit_events(self) -> List[dict]:
        return read_jsonl(self.state_dir / "messages.jsonl")

    def set_tool_signature(self, signature: str) -> None:
        if self.tool_signature_file is None:
            raise AssertionError("tool signature file not configured")
        self.tool_signature_file.write_text(signature, encoding="utf-8")


class ServerWrapperPhase2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="server-wrapper-phase2-"))
        self._harnesses: List[SupervisorHarness] = []

    def tearDown(self) -> None:
        for harness in self._harnesses:
            harness.cleanup()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _start_harness(
        self,
        *,
        mode: str = "echo",
        watch_count: int = 1,
        tool_signature: Optional[str] = None,
    ) -> SupervisorHarness:
        harness = SupervisorHarness(
            self.tempdir / ("case-%s" % len(self._harnesses)),
            mode=mode,
            watch_count=watch_count,
            tool_signature=tool_signature,
        )
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")
        return harness

    def test_wrapper_phase2_pumps_stdio_bytes(self) -> None:
        harness = self._start_harness()

        payload = b"hello\\x00wrapper\\n"
        harness.stdin_stream.push(payload)
        output = harness.stdout_stream.wait_for(payload)

        self.assertIn(payload, output)
        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_marks_refresh_required_on_mtime_change(self) -> None:
        harness = self._start_harness()

        first_pid = harness.launch_pids()[0]
        harness.touch_watch_files([0])
        time.sleep(0.25)

        self.assertEqual(harness.launch_pids(), [first_pid])
        refresh_events = [event for event in harness.audit_events() if event.get("action") == "mcp_server_refresh_required"]
        self.assertEqual(len(refresh_events), 1)
        self.assertEqual(refresh_events[0]["child_pid"], first_pid)
        self.assertEqual(refresh_events[0]["changed_files"], [str(harness.watch_files[0])])

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_debounces_burst(self) -> None:
        harness = self._start_harness(watch_count=3)

        harness.touch_watch_files([0, 1, 2])
        time.sleep(0.2)

        self.assertEqual(len(harness.launch_pids()), 1)
        refresh_events = [event for event in harness.audit_events() if event.get("action") == "mcp_server_refresh_required"]
        self.assertEqual(len(refresh_events), 1)
        self.assertEqual(sorted(refresh_events[0]["changed_files"]), sorted(str(path) for path in harness.watch_files))

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_idle_gates_during_io(self) -> None:
        harness = self._start_harness()
        stop_writes = threading.Event()

        def spam_input() -> None:
            while not stop_writes.is_set():
                harness.stdin_stream.push(b"x")
                time.sleep(0.02)

        writer = threading.Thread(target=spam_input, daemon=True)
        writer.start()
        harness.stdout_stream.wait_for(b"x")

        harness.touch_watch_files([0])
        time.sleep(0.25)
        self.assertEqual(len(harness.launch_pids()), 1)
        self.assertEqual(
            [event for event in harness.audit_events() if event.get("action") == "mcp_server_refresh_required"],
            [],
        )

        stop_writes.set()
        writer.join(timeout=1.0)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            refresh_events = [event for event in harness.audit_events() if event.get("action") == "mcp_server_refresh_required"]
            if refresh_events:
                break
            time.sleep(0.02)
        self.assertEqual(len(harness.launch_pids()), 1)
        self.assertEqual(len(refresh_events), 1)

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_repeated_changes_do_not_abort_wrapper(self) -> None:
        harness = self._start_harness()

        for _ in range(4):
            harness.touch_watch_files([0])
            time.sleep(0.15)

        self.assertEqual(len(harness.launch_pids()), 1)
        self.assertIsNone(harness.result.get("exit_code"))

        refresh_events = [event for event in harness.audit_events() if event.get("action") == "mcp_server_refresh_required"]
        aborted = [event for event in harness.audit_events() if event.get("action") == "mcp_server_self_restart_aborted_loop"]
        self.assertGreaterEqual(len(refresh_events), 1)
        self.assertEqual(len(aborted), 0)

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_does_not_restart_on_crash_without_code_change(self) -> None:
        harness = self._start_harness(mode="crash")

        self.assertEqual(harness.wait_for_exit(), 7)
        self.assertEqual(len(harness.launch_pids()), 1)
        self.assertEqual(
            [event for event in harness.audit_events() if event.get("action") == "mcp_server_self_restarted"],
            [],
        )

    def test_wrapper_phase2_marks_tool_refresh_required_when_manifest_changes(self) -> None:
        harness = self._start_harness(tool_signature="sig-a")

        harness.touch_watch_files([0])
        deadline = time.time() + 3.0
        refresh_events = []
        while time.time() < deadline:
            refresh_events = [event for event in harness.audit_events() if event.get("action") == "mcp_tools_refresh_required"]
            if refresh_events:
                break
            time.sleep(0.02)

        self.assertEqual(len(refresh_events), 1)
        self.assertEqual(refresh_events[0]["previous_signature"], "sig-a")
        self.assertEqual(refresh_events[0]["current_signature"], "sig-a")
        self.assertEqual(refresh_events[0]["reason"], "bridge_code_changed_during_wrapper_session")

        status = json.loads((harness.state_dir / "tool-refresh-status.json").read_text(encoding="utf-8"))
        self.assertTrue(status["refresh_required"])
        self.assertEqual(status["previous_signature"], "sig-a")
        self.assertEqual(status["current_signature"], "sig-a")

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)


if __name__ == "__main__":
    unittest.main()
