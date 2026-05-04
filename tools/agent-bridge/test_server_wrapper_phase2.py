import io
import queue
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

import server_wrapper as _sw
import server_wrapper_trampoline as _trampoline
from core.storage import read_jsonl
from server_wrapper import SERVER_WRAPPER_SELF_RESTART_EXIT_CODE, SupervisorConfig, _is_restart_trigger_file, run_supervisor


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

with open(args.launch_log, "a+", encoding="utf-8", newline="\\n") as handle:
    handle.write(str(os.getpid()) + "\\n")
    handle.flush()
    handle.seek(0)
    launch_count = len([line for line in handle.read().splitlines() if line.strip()])

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
if args.mode == "crash-once" and launch_count == 1:
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


def write_and_wait_new_mtime(path: Path, content: str, timeout: float = 2.0) -> None:
    before_mtime = path.stat().st_mtime_ns if path.exists() else None
    path.write_text(content, encoding="utf-8")
    deadline = time.time() + timeout
    while time.time() < deadline:
        after_mtime = path.stat().st_mtime_ns if path.exists() else None
        if after_mtime != before_mtime:
            return
        time.sleep(0.01)
        path.write_text(content, encoding="utf-8")


class SupervisorHarness:
    def __init__(self, tempdir: Path, *, mode: str = "echo", watch_count: int = 1, tool_signature: Optional[str] = None, watch_paths: Optional[List[Path]] = None, config: Optional[SupervisorConfig] = None) -> None:
        self.root = tempdir
        self.state_dir = self.root / "bridge-root" / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.server_script = self.root / "fake_server.py"
        self.server_script.write_text(SERVER_SCRIPT, encoding="utf-8")
        self.launch_log = self.root / "launches.log"
        self.tool_signature_file: Optional[Path] = None
        if tool_signature is not None:
            self.tool_signature_file = self.root / "tool-signature.txt"
            self.tool_signature_file.write_text(tool_signature, encoding="utf-8")
        if watch_paths is not None:
            self.watch_files = list(watch_paths)
            for path in self.watch_files:
                if not path.exists():
                    path.write_text("# watch\n", encoding="utf-8")
        else:
            core_dir = self.root / "core"
            core_dir.mkdir(exist_ok=True)
            self.watch_files = [core_dir / ("watch_%s.py" % index) for index in range(watch_count)]
            for index, path in enumerate(self.watch_files):
                path.write_text("# %s\\n" % index, encoding="utf-8")

        self._config = config
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
                config=self._config or SupervisorConfig(
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

    def wait_for_audit_events(self, action: str, expected: int = 1, timeout: float = 3.0) -> List[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = [event for event in self.audit_events() if event.get("action") == action]
            if len(events) >= expected:
                return events
            time.sleep(0.02)
        raise AssertionError("timed out waiting for %s audit event(s): %s" % (action, self.audit_events()))

    def set_tool_signature(self, signature: str) -> None:
        if self.tool_signature_file is None:
            raise AssertionError("tool signature file not configured")
        self.tool_signature_file.write_text(signature, encoding="utf-8")

    def wait_for_snapshot(self, timeout: float = 3.0) -> Path:
        """Wait until the code-watcher-snapshot.json file appears in state_dir."""
        snapshot_path = self.state_dir / "code-watcher-snapshot.json"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if snapshot_path.exists():
                return snapshot_path
            time.sleep(0.02)
        raise AssertionError("timed out waiting for code-watcher-snapshot.json to appear")

    def wait_for_snapshot_ready(self, old_file_mtime_ns: Optional[int] = None, timeout: float = 3.0) -> dict:
        """Wait until a valid (schema_version=1) snapshot exists that has been written or
        rewritten this session.

        When old_file_mtime_ns is given, polls until the snapshot file's mtime_ns differs from
        that value AND the file contains valid schema_version=1 JSON.  This reliably detects
        when the watch thread has completed its _load_and_apply_persisted_snapshot startup pass:
        the last action in every startup code-path is a write_json call that advances the mtime.

        When old_file_mtime_ns is None, just waits for any valid snapshot to appear.

        NOTE: wrapper_pid is NOT used here because all harnesses within one test run share the
        same OS process (run_supervisor is called from a daemon thread), so os.getpid() returns
        the same value for every session and cannot distinguish "session 1 wrote this" from
        "session 2 wrote this".  File mtime is process-independent and uniquely changes on
        every write_json call.
        """
        snapshot_path = self.state_dir / "code-watcher-snapshot.json"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                mtime_ns = snapshot_path.stat().st_mtime_ns
                data = json.loads(snapshot_path.read_text(encoding="utf-8"))
                if data.get("schema_version") == 1:
                    if old_file_mtime_ns is None or mtime_ns != old_file_mtime_ns:
                        return data
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            time.sleep(0.02)
        raise AssertionError(
            "timed out waiting for valid snapshot to be written (old_file_mtime_ns=%r)" % old_file_mtime_ns
        )

    def snapshot_data(self) -> dict:
        """Read and return the current code-watcher-snapshot.json contents."""
        path = self.state_dir / "code-watcher-snapshot.json"
        return json.loads(path.read_text(encoding="utf-8"))


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

    def test_wrapper_phase2_restarts_in_place_on_mtime_change(self) -> None:
        harness = self._start_harness()

        first_pid = harness.launch_pids()[0]
        harness.touch_watch_files([0])
        launches = harness.wait_for_launch_count(2)
        second_pid = launches[1]

        self.assertEqual(launches[0], first_pid)
        self.assertNotEqual(second_pid, first_pid)
        harness.stdout_stream.wait_for(("READY %s" % second_pid).encode("ascii"))

        payload = b"post-restart\\n"
        harness.stdin_stream.push(payload)
        output = harness.stdout_stream.wait_for(payload)

        self.assertIn(payload, output)
        refresh_events = harness.wait_for_audit_events("mcp_server_refresh_required")
        self.assertEqual(len(refresh_events), 1)
        self.assertEqual(refresh_events[0]["child_pid"], first_pid)
        self.assertEqual(refresh_events[0]["changed_files"], [str(harness.watch_files[0])])
        self_restarts = harness.wait_for_audit_events("mcp_server_self_restarted")
        self.assertEqual(len(self_restarts), 1)
        self.assertEqual(self_restarts[0]["old_child_pid"], first_pid)
        self.assertEqual(self_restarts[0]["new_child_pid"], second_pid)
        self.assertEqual(self_restarts[0]["reason"], "bridge_code_changed_during_wrapper_session")

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_debounces_burst(self) -> None:
        harness = self._start_harness(watch_count=3)

        harness.touch_watch_files([0, 1, 2])
        launches = harness.wait_for_launch_count(2)

        self.assertEqual(len(launches), 2)
        refresh_events = harness.wait_for_audit_events("mcp_server_refresh_required")
        self.assertEqual(len(refresh_events), 1)
        self.assertEqual(sorted(refresh_events[0]["changed_files"]), sorted(str(path) for path in harness.watch_files))
        self.assertEqual(len(harness.wait_for_audit_events("mcp_server_self_restarted")), 1)

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
        launches = harness.wait_for_launch_count(2)
        refresh_events = harness.wait_for_audit_events("mcp_server_refresh_required")
        self.assertEqual(len(launches), 2)
        self.assertEqual(len(refresh_events), 1)

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_repeated_changes_do_not_abort_wrapper(self) -> None:
        harness = self._start_harness()

        expected_launches = 1
        for _ in range(4):
            harness.touch_watch_files([0])
            expected_launches += 1
            harness.wait_for_launch_count(expected_launches)

        self.assertEqual(len(harness.launch_pids()), 5)
        self.assertIsNone(harness.result.get("exit_code"))

        refresh_events = harness.wait_for_audit_events("mcp_server_refresh_required", expected=4)
        aborted = [event for event in harness.audit_events() if event.get("action") == "mcp_server_self_restart_aborted_loop"]
        self.assertEqual(len(refresh_events), 4)
        self.assertEqual(len(aborted), 0)

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_respawns_after_unexpected_exit(self) -> None:
        harness = self._start_harness(mode="crash-once")

        launches = harness.wait_for_launch_count(2)
        second_pid = launches[1]
        harness.stdout_stream.wait_for(("READY %s" % second_pid).encode("ascii"))

        payload = b"after-crash\\n"
        harness.stdin_stream.push(payload)
        output = harness.stdout_stream.wait_for(payload)

        self.assertIn(payload, output)
        self_restarts = harness.wait_for_audit_events("mcp_server_self_restarted")
        self.assertEqual(len(self_restarts), 1)
        self.assertEqual(self_restarts[0]["reason"], "unexpected_child_exit")
        self.assertEqual(self_restarts[0]["previous_exit_code"], 7)
        self.assertEqual(
            [event for event in harness.audit_events() if event.get("action") == "mcp_server_refresh_required"],
            [],
        )

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrapper_phase2_exits_after_restart_limit_on_repeated_crash(self) -> None:
        harness = self._start_harness(mode="crash")

        self.assertEqual(harness.wait_for_exit(), 7)
        self.assertGreaterEqual(len(harness.launch_pids()), 5)
        self.assertGreaterEqual(len(harness.wait_for_audit_events("mcp_server_self_restarted", expected=4)), 4)
        aborted = [event for event in harness.audit_events() if event.get("action") == "mcp_server_self_restart_aborted_loop"]
        self.assertEqual(len(aborted), 1)
        self.assertEqual(aborted[0]["reason"], "unexpected_child_exit")

    def test_wrapper_phase2_marks_tool_refresh_required_when_manifest_changes(self) -> None:
        harness = self._start_harness(tool_signature="sig-a")

        harness.set_tool_signature("sig-b")
        harness.touch_watch_files([0])
        harness.wait_for_launch_count(2)
        refresh_events = harness.wait_for_audit_events("mcp_tools_refresh_required")

        self.assertEqual(len(refresh_events), 1)
        self.assertEqual(refresh_events[0]["previous_signature"], "sig-a")
        self.assertEqual(refresh_events[0]["current_signature"], "sig-b")
        self.assertEqual(refresh_events[0]["reason"], "bridge_code_changed_during_wrapper_session")

        status = json.loads((harness.state_dir / "tool-refresh-status.json").read_text(encoding="utf-8"))
        self.assertTrue(status["refresh_required"])
        self.assertEqual(status["previous_signature"], "sig-a")
        self.assertEqual(status["current_signature"], "sig-b")

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)


    def test_restart_trigger_file_predicate(self) -> None:
        self.assertTrue(_is_restart_trigger_file(Path("server.py")))
        self.assertTrue(_is_restart_trigger_file(Path("agent_bridge.py")))
        self.assertTrue(_is_restart_trigger_file(Path("core/storage.py")))
        self.assertTrue(_is_restart_trigger_file(Path("core/routing.py")))
        self.assertTrue(_is_restart_trigger_file(Path("server_wrapper.py")))
        self.assertFalse(_is_restart_trigger_file(Path("dashboard_server.py")))
        self.assertFalse(_is_restart_trigger_file(Path("watcher.py")))
        self.assertFalse(_is_restart_trigger_file(Path("test_foo.py")))
        self.assertFalse(_is_restart_trigger_file(Path("foo_test.py")))

    def test_wrapper_file_change_exits_for_trampoline_self_restart(self) -> None:
        wrapper_file = self.tempdir / "server_wrapper.py"
        wrapper_file.write_text("# wrapper v1\n", encoding="utf-8")

        harness = SupervisorHarness(
            self.tempdir / ("case-%s" % len(self._harnesses)),
            watch_paths=[wrapper_file],
        )
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")

        write_and_wait_new_mtime(wrapper_file, "# wrapper v2\n")

        self.assertEqual(harness.wait_for_exit(timeout=5.0), SERVER_WRAPPER_SELF_RESTART_EXIT_CODE)
        self.assertEqual(len(harness.launch_pids()), 1, "wrapper self-restart must not spawn a second child")
        events = harness.wait_for_audit_events("mcp_server_wrapper_self_restart_requested")
        self.assertEqual(events[0]["changed_files"], [str(wrapper_file)])
        self.assertEqual(events[0]["exit_code"], SERVER_WRAPPER_SELF_RESTART_EXIT_CODE)

        snapshot = harness.snapshot_data()
        self.assertEqual(snapshot["mtimes"][str(wrapper_file)], wrapper_file.stat().st_mtime_ns)

    def test_wrapper_self_restart_takes_precedence_for_mixed_changes(self) -> None:
        wrapper_file = self.tempdir / "server_wrapper.py"
        server_file = self.tempdir / "server.py"
        wrapper_file.write_text("# wrapper v1\n", encoding="utf-8")
        server_file.write_text("# server v1\n", encoding="utf-8")

        harness = SupervisorHarness(
            self.tempdir / ("case-%s" % len(self._harnesses)),
            watch_paths=[wrapper_file, server_file],
        )
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")

        write_and_wait_new_mtime(wrapper_file, "# wrapper v2\n")
        write_and_wait_new_mtime(server_file, "# server v2\n")

        self.assertEqual(harness.wait_for_exit(timeout=5.0), SERVER_WRAPPER_SELF_RESTART_EXIT_CODE)
        self.assertEqual(len(harness.launch_pids()), 1)
        events = harness.wait_for_audit_events("mcp_server_wrapper_self_restart_requested")
        self.assertEqual(events[0]["changed_files"], sorted([str(server_file), str(wrapper_file)]))
        self.assertEqual(
            [event for event in harness.audit_events() if event.get("action") == "mcp_server_self_restarted"],
            [],
        )

    def test_partial_stdin_frame_delays_wrapper_self_restart_until_complete(self) -> None:
        wrapper_file = self.tempdir / "server_wrapper.py"
        wrapper_file.write_text("# wrapper v1\n", encoding="utf-8")
        config = SupervisorConfig(
            poll_interval_seconds=0.05,
            debounce_seconds=0.05,
            idle_seconds=0.0,
            terminate_timeout_seconds=0.5,
            restart_window_seconds=5.0,
            max_restarts_per_window=4,
            chunk_size=4096,
            loop_sleep_seconds=0.01,
            graceful_restart_timeout_seconds=1.0,
        )

        harness = SupervisorHarness(
            self.tempdir / ("case-%s" % len(self._harnesses)),
            watch_paths=[wrapper_file],
            config=config,
        )
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")

        first_half = b'{"jsonrpc":"2.0","id":"partial-1","method":"tools/call"'
        second_half = b',"params":{"name":"t"}}\n'
        harness.stdin_stream.push(first_half)
        harness.stdout_stream.wait_for(first_half)

        write_and_wait_new_mtime(wrapper_file, "# wrapper v2\n")
        time.sleep(0.25)
        self.assertIsNone(harness.result.get("exit_code"), "partial JSON-RPC frame should delay self-restart")

        harness.stdin_stream.push(second_half)
        harness.stdout_stream.wait_for(second_half)
        self.assertEqual(harness.wait_for_exit(timeout=5.0), SERVER_WRAPPER_SELF_RESTART_EXIT_CODE)

    def test_no_restart_file_change_does_not_trigger_restart(self) -> None:
        case_dir = self.tempdir / "no-restart-case"
        case_dir.mkdir()
        real_file = case_dir / "server.py"
        real_file.write_text("# server\n", encoding="utf-8")
        no_restart_file = case_dir / "dashboard_server.py"
        no_restart_file.write_text("# dashboard\n", encoding="utf-8")

        harness = SupervisorHarness(
            self.tempdir / ("case-%s" % len(self._harnesses)),
            watch_paths=[real_file, no_restart_file],
        )
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")

        no_restart_file.write_text("# touched\n", encoding="utf-8")
        time.sleep(0.25)
        self.assertEqual(len(harness.launch_pids()), 1, "no-restart file change must not trigger restart")
        skipped = harness.wait_for_audit_events("mcp_server_restart_skipped_no_restart_files")
        self.assertEqual(len(skipped), 1)
        self.assertIn(str(no_restart_file), skipped[0]["skipped_files"])

        real_file.write_text("# updated\n", encoding="utf-8")
        harness.wait_for_launch_count(2)

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_mixed_file_change_restarts_on_restart_file_only(self) -> None:
        case_dir = self.tempdir / "mixed-case"
        case_dir.mkdir()
        real_file = case_dir / "server.py"
        real_file.write_text("# server\n", encoding="utf-8")
        no_restart_file = case_dir / "dashboard_server.py"
        no_restart_file.write_text("# dashboard\n", encoding="utf-8")

        harness = SupervisorHarness(
            self.tempdir / ("case-%s" % len(self._harnesses)),
            watch_paths=[real_file, no_restart_file],
        )
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")

        real_file.write_text("# updated\n", encoding="utf-8")
        no_restart_file.write_text("# also updated\n", encoding="utf-8")
        harness.wait_for_launch_count(2)

        refresh_events = harness.wait_for_audit_events("mcp_server_refresh_required")
        self.assertEqual(refresh_events[0]["changed_files"], [str(real_file)])

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)


class ServerWrapperTrampolineTests(unittest.TestCase):
    def test_trampoline_relaunches_on_exit_77_and_returns_final_code(self) -> None:
        calls: List[List[str]] = []
        return_codes = [
            SERVER_WRAPPER_SELF_RESTART_EXIT_CODE,
            SERVER_WRAPPER_SELF_RESTART_EXIT_CODE,
            0,
        ]

        def fake_call(command: List[str]) -> int:
            calls.append(command)
            return return_codes.pop(0)

        rc = _trampoline.run_trampoline(
            ["--bridge-root", "C:/bridge root"],
            wrapper_path=Path("server_wrapper.py"),
            call_fn=fake_call,
            now_fn=lambda: 1.0,
        )

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[0] == sys.executable for call in calls))
        self.assertTrue(all(call[1] == "server_wrapper.py" for call in calls))
        self.assertTrue(all(call[2:] == ["--bridge-root", "C:/bridge root"] for call in calls))

    def test_trampoline_aborts_exit_77_restart_loop(self) -> None:
        calls = 0

        def fake_call(command: List[str]) -> int:
            nonlocal calls
            calls += 1
            return SERVER_WRAPPER_SELF_RESTART_EXIT_CODE

        captured_stderr = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured_stderr
        try:
            rc = _trampoline.run_trampoline(
                [],
                wrapper_path=Path("server_wrapper.py"),
                call_fn=fake_call,
                now_fn=lambda: 5.0,
            )
        finally:
            sys.stderr = old_stderr

        self.assertEqual(rc, 1)
        self.assertEqual(calls, _trampoline.DEFAULT_MAX_SELF_RESTARTS_PER_WINDOW + 1)
        self.assertIn("aborted restart loop", captured_stderr.getvalue())


class ServerWrapperTrampolineMcpSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="server-wrapper-trampoline-mcp-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _send(self, proc: subprocess.Popen[bytes], message: dict) -> None:
        if proc.stdin is None:
            raise AssertionError("process stdin is closed")
        proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def _recv(
        self,
        proc: subprocess.Popen[bytes],
        stdout_queue: "queue.Queue[bytes]",
        stderr_lines: List[str],
        *,
        timeout: float = 10.0,
    ) -> dict:
        deadline = time.time() + timeout
        line = bytearray()
        while time.time() < deadline:
            try:
                chunk = stdout_queue.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                if proc.poll() is not None:
                    raise AssertionError("trampoline exited rc=%r stderr=%r" % (proc.returncode, "".join(stderr_lines)))
                continue
            if not chunk:
                raise AssertionError("stdout closed rc=%r stderr=%r" % (proc.poll(), "".join(stderr_lines)))
            line.extend(chunk)
            if chunk == b"\n":
                return json.loads(bytes(line).decode("utf-8"))
        raise AssertionError("timed out waiting for MCP response; stderr=%r" % "".join(stderr_lines))

    def _wait_for_audit_count(self, audit_path: Path, action: str, expected: int, timeout: float = 12.0) -> List[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            rows = read_jsonl(audit_path) if audit_path.exists() else []
            matches = [row for row in rows if row.get("action") == action]
            if len(matches) >= expected:
                return matches
            time.sleep(0.05)
        rows = read_jsonl(audit_path) if audit_path.exists() else []
        raise AssertionError("timed out waiting for %s x%s; rows=%r" % (action, expected, rows))

    def test_trampoline_preserves_mcp_tool_calls_across_wrapper_exit_77(self) -> None:
        bridge_root = self.tempdir / "bridge-root"
        watch_dir = self.tempdir / "watch"
        watch_dir.mkdir()
        watched_wrapper = watch_dir / "server_wrapper.py"
        watched_wrapper.write_text("# wrapper v1\n", encoding="utf-8")
        trampoline = Path(__file__).resolve().parent / "server_wrapper_trampoline.py"

        proc = subprocess.Popen(
            [
                sys.executable,
                str(trampoline),
                "--bridge-root",
                str(bridge_root),
                "--watch-code-dir",
                str(watch_dir),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        stdout_queue: "queue.Queue[bytes]" = queue.Queue()
        stderr_lines: List[str] = []

        def pump_stdout() -> None:
            if proc.stdout is None:
                return
            for raw in iter(lambda: proc.stdout.read(1), b""):
                stdout_queue.put(raw)
            stdout_queue.put(b"")

        def pump_stderr() -> None:
            if proc.stderr is None:
                return
            for raw in iter(proc.stderr.readline, b""):
                stderr_lines.append(raw.decode("utf-8", errors="replace"))

        threading.Thread(target=pump_stdout, daemon=True).start()
        threading.Thread(target=pump_stderr, daemon=True).start()
        try:
            self._send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "clientInfo": {"name": "trampoline-smoke", "version": "0.1"},
                        "capabilities": {},
                    },
                },
            )
            init_response = self._recv(proc, stdout_queue, stderr_lines, timeout=12.0)
            self.assertEqual(init_response["id"], "init-1")
            self.assertEqual(init_response["result"]["serverInfo"]["name"], "agent-bridge")

            self._send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": "call-before",
                    "method": "tools/call",
                    "params": {"name": "bridge_status", "arguments": {}},
                },
            )
            before_response = self._recv(proc, stdout_queue, stderr_lines)
            self.assertEqual(before_response["id"], "call-before")
            self.assertNotIn("error", before_response)

            audit_path = bridge_root / "state" / "messages.jsonl"
            self._wait_for_audit_count(audit_path, "mcp_server_wrapper_launch", 1)
            write_and_wait_new_mtime(watched_wrapper, "# wrapper v2\n")
            self._wait_for_audit_count(audit_path, "mcp_server_wrapper_self_restart_requested", 1)
            self._wait_for_audit_count(audit_path, "mcp_server_wrapper_launch", 2)
            self._wait_for_audit_count(audit_path, "mcp_server_session_replayed", 1)

            self._send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": "call-after",
                    "method": "tools/call",
                    "params": {"name": "bridge_status", "arguments": {}},
                },
            )
            after_response = self._recv(proc, stdout_queue, stderr_lines)
            self.assertEqual(after_response["id"], "call-after")
            self.assertNotIn("error", after_response)
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5.0)


class ServerWrapperSnapshotTests(unittest.TestCase):
    """Tests for the persisted mtime snapshot that closes the inter-session self-heal gap."""

    def setUp(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="server-wrapper-snapshot-"))
        self._harnesses: List[SupervisorHarness] = []
        self._case_counter = 0

    def tearDown(self) -> None:
        for harness in self._harnesses:
            harness.cleanup()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _make_case_dir(self) -> Path:
        case_dir = self.tempdir / ("case-%s" % self._case_counter)
        self._case_counter += 1
        case_dir.mkdir(parents=True, exist_ok=True)
        return case_dir

    @staticmethod
    def _write_and_wait_new_mtime(path: Path, content: str, timeout: float = 2.0) -> None:
        """Write content to path and spin until the filesystem reports a new mtime.

        A plain time.sleep() before writing is fragile on filesystems with coarse mtime
        granularity (FAT32, some network shares). This helper guarantees the write lands
        with a mtime that differs from whatever was there before, making inter-session
        change-detection tests reliable in CI.
        """
        before_mtime = path.stat().st_mtime_ns if path.exists() else None
        path.write_text(content, encoding="utf-8")
        deadline = time.time() + timeout
        while time.time() < deadline:
            after_mtime = path.stat().st_mtime_ns if path.exists() else None
            if after_mtime != before_mtime:
                return
            # Re-write to force mtime advance on filesystems that batch updates.
            time.sleep(0.01)
            path.write_text(content, encoding="utf-8")
        # Give up — filesystem uses very coarse granularity; test proceeds as-is.

    def test_snapshot_created_on_first_start(self) -> None:
        """Wrapper creates a baseline snapshot on the very first run (no pre-existing file)."""
        case_dir = self._make_case_dir()
        core_dir = case_dir / "core"
        core_dir.mkdir()
        watch_file = core_dir / "agent_bridge.py"
        watch_file.write_text("# v1\n", encoding="utf-8")

        harness = SupervisorHarness(
            self._make_case_dir(),
            watch_paths=[watch_file],
        )
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")

        snapshot_path = harness.wait_for_snapshot()
        self.assertTrue(snapshot_path.exists())
        data = harness.snapshot_data()
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("mtimes", data)
        self.assertIn(str(watch_file), data["mtimes"])
        # No spurious restart on first run
        self.assertEqual(len(harness.launch_pids()), 1)

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_startup_detects_edit_between_sessions(self) -> None:
        """Core regression test: file edited after wrapper stopped is caught on next startup."""
        # Session 1: create a harness to establish a snapshot baseline
        watch_dir = self.tempdir / "shared-watch"
        watch_dir.mkdir()
        core_dir = watch_dir / "core"
        core_dir.mkdir()
        trigger_file = core_dir / "agent_bridge.py"
        trigger_file.write_text("# version 1\n", encoding="utf-8")

        harness1_root = self.tempdir / "session1"
        harness1_root.mkdir()
        (harness1_root / "bridge-root" / "state").mkdir(parents=True)

        harness1 = SupervisorHarness(harness1_root, watch_paths=[trigger_file])
        self._harnesses.append(harness1)
        harness1.wait_for_launch_count(1)
        harness1.stdout_stream.wait_for(b"READY ")
        snapshot_path_h1 = harness1.wait_for_snapshot()

        # Stop session 1
        harness1.stdin_stream.close()
        harness1.wait_for_exit()

        # Simulate editing trigger_file BETWEEN sessions.
        # Use _write_and_wait_new_mtime rather than a plain sleep: on filesystems with
        # coarse mtime granularity the sleep may be insufficient for the mtime to advance.
        self._write_and_wait_new_mtime(trigger_file, "# version 2 - edited between sessions\n")

        # Session 2: new harness, same watch file, snapshot copied from session 1
        harness2_root = self.tempdir / "session2"
        harness2_root.mkdir()
        state2 = harness2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        # Copy the snapshot from session 1 into session 2's state dir
        shutil.copy2(str(snapshot_path_h1), str(state2 / "code-watcher-snapshot.json"))

        harness2 = SupervisorHarness(harness2_root, watch_paths=[trigger_file])
        self._harnesses.append(harness2)

        # The startup detection should queue the change and trigger a restart
        harness2.wait_for_launch_count(2)

        events = harness2.wait_for_audit_events("mcp_server_restart_queued_from_persisted_snapshot")
        self.assertEqual(len(events), 1)
        self.assertIn(str(trigger_file), events[0]["changed_files"])

        restart_events = harness2.wait_for_audit_events("mcp_server_self_restarted")
        self.assertGreaterEqual(len(restart_events), 1)

        harness2.stdin_stream.close()
        self.assertEqual(harness2.wait_for_exit(), 0)

    def test_no_spurious_restart_when_snapshot_is_current(self) -> None:
        """If snapshot already reflects current mtimes, no restart is queued at startup."""
        watch_dir = self.tempdir / "shared-watch2"
        watch_dir.mkdir()
        core_dir = watch_dir / "core"
        core_dir.mkdir()
        trigger_file = core_dir / "storage.py"
        trigger_file.write_text("# unchanged\n", encoding="utf-8")

        # Write a snapshot that matches current mtime
        harness1_root = self.tempdir / "s1"
        harness1_root.mkdir()
        (harness1_root / "bridge-root" / "state").mkdir(parents=True)
        harness1 = SupervisorHarness(harness1_root, watch_paths=[trigger_file])
        self._harnesses.append(harness1)
        harness1.wait_for_launch_count(1)
        harness1.stdout_stream.wait_for(b"READY ")
        snap_path = harness1.wait_for_snapshot()
        harness1.stdin_stream.close()
        harness1.wait_for_exit()

        # Start session 2 with the current snapshot — no file was edited
        old_snap_data = json.loads(snap_path.read_text(encoding="utf-8"))

        harness2_root = self.tempdir / "s2"
        harness2_root.mkdir()
        state2 = harness2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        snap2_path = state2 / "code-watcher-snapshot.json"
        shutil.copy2(str(snap_path), str(snap2_path))
        # shutil.copy2 preserves the source mtime; record it so we can detect the rewrite.
        old_file_mtime_ns = snap2_path.stat().st_mtime_ns

        harness2 = SupervisorHarness(harness2_root, watch_paths=[trigger_file])
        self._harnesses.append(harness2)
        harness2.wait_for_launch_count(1)
        harness2.stdout_stream.wait_for(b"READY ")
        # Wait for startup detection to complete: the no-change else-branch rewrites the
        # snapshot (updating its mtime).  Deterministic replacement for time.sleep(0.2).
        refreshed = harness2.wait_for_snapshot_ready(old_file_mtime_ns=old_file_mtime_ns)

        self.assertEqual(len(harness2.launch_pids()), 1, "no restart expected: snapshot is current")
        queued = [e for e in harness2.audit_events() if e.get("action") == "mcp_server_restart_queued_from_persisted_snapshot"]
        self.assertEqual(len(queued), 0)
        # The else-branch must have refreshed the snapshot on disk with the same mtimes
        # (proves _save_watcher_snapshot was called on the no-change path).
        self.assertEqual(refreshed["mtimes"], old_snap_data["mtimes"], "snapshot mtimes must be unchanged (no file edits)")

        harness2.stdin_stream.close()
        self.assertEqual(harness2.wait_for_exit(), 0)

    def test_no_change_startup_refreshes_snapshot_on_disk(self) -> None:
        """When no trigger files changed between sessions, the startup detection else-branch
        must rewrite the snapshot on disk with the current wrapper_pid (refreshing the baseline
        so future startups compare against recent state rather than an ancient one).

        This explicitly tests the else-branch of _load_and_apply_persisted_snapshot that was
        identified as untested in review — the branch is exercised by test_no_spurious_restart_*
        above but this test makes the assertion explicit and adds the mtimes-unchanged check.
        """
        watch_dir = self.tempdir / "shared-watch-refresh"
        watch_dir.mkdir()
        core_dir = watch_dir / "core"
        core_dir.mkdir()
        trigger_file = core_dir / "server.py"
        trigger_file.write_text("# unchanged\n", encoding="utf-8")

        # Session 1: establish baseline snapshot
        h1_root = self.tempdir / "refresh-s1"
        h1_root.mkdir()
        (h1_root / "bridge-root" / "state").mkdir(parents=True)
        h1 = SupervisorHarness(h1_root, watch_paths=[trigger_file])
        self._harnesses.append(h1)
        h1.wait_for_launch_count(1)
        h1.stdout_stream.wait_for(b"READY ")
        snap_path = h1.wait_for_snapshot()
        h1.stdin_stream.close()
        h1.wait_for_exit()

        # Record the session-1 snapshot contents for comparison
        s1_snap = json.loads(snap_path.read_text(encoding="utf-8"))

        # Session 2: start with the unchanged snapshot (no file edits)
        h2_root = self.tempdir / "refresh-s2"
        h2_root.mkdir()
        state2 = h2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        snap2_path = state2 / "code-watcher-snapshot.json"
        shutil.copy2(str(snap_path), str(snap2_path))
        # shutil.copy2 preserves the source mtime; record it so we can detect the rewrite.
        old_file_mtime_ns = snap2_path.stat().st_mtime_ns

        h2 = SupervisorHarness(h2_root, watch_paths=[trigger_file])
        self._harnesses.append(h2)
        h2.wait_for_launch_count(1)
        h2.stdout_stream.wait_for(b"READY ")
        # The else-branch must rewrite the snapshot (advancing its mtime) once startup
        # detection has confirmed no trigger files changed.
        refreshed = h2.wait_for_snapshot_ready(old_file_mtime_ns=old_file_mtime_ns)

        # No restart queued
        queued = [e for e in h2.audit_events() if e.get("action") == "mcp_server_restart_queued_from_persisted_snapshot"]
        self.assertEqual(len(queued), 0)
        self.assertEqual(len(h2.launch_pids()), 1)

        # Snapshot refreshed with the same mtimes (no file edits between sessions)
        self.assertEqual(
            refreshed["mtimes"],
            s1_snap["mtimes"],
            "snapshot mtimes must be unchanged (no file edits between sessions)",
        )
        self.assertEqual(refreshed["schema_version"], 1)

        h2.stdin_stream.close()
        self.assertEqual(h2.wait_for_exit(), 0)

    def test_corrupt_snapshot_treated_as_first_run(self) -> None:
        """A corrupt or unparseable snapshot file is treated as if no snapshot exists."""
        case_dir = self._make_case_dir()
        core_dir = case_dir / "core"
        core_dir.mkdir()
        trigger_file = core_dir / "server.py"
        trigger_file.write_text("# server\n", encoding="utf-8")

        harness_root = self._make_case_dir()
        harness_root.mkdir(exist_ok=True)
        state_dir = harness_root / "bridge-root" / "state"
        state_dir.mkdir(parents=True)
        # Write a corrupt snapshot
        corrupt_snap = state_dir / "code-watcher-snapshot.json"
        corrupt_snap.write_text("not valid json!!!", encoding="utf-8")
        corrupt_mtime_ns = corrupt_snap.stat().st_mtime_ns

        harness = SupervisorHarness(harness_root, watch_paths=[trigger_file])
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")
        # Wait for startup detection to complete: the first-run path overwrites the corrupt
        # file with a valid schema_version=1 snapshot (new mtime).  Deterministic.
        new_data = harness.wait_for_snapshot_ready(old_file_mtime_ns=corrupt_mtime_ns)

        self.assertEqual(len(harness.launch_pids()), 1, "corrupt snapshot must not trigger restart")
        queued = [e for e in harness.audit_events() if e.get("action") == "mcp_server_restart_queued_from_persisted_snapshot"]
        self.assertEqual(len(queued), 0)
        # A valid new snapshot should have been written
        self.assertTrue((state_dir / "code-watcher-snapshot.json").exists())
        self.assertEqual(new_data.get("schema_version"), 1)

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_wrong_schema_version_treated_as_first_run(self) -> None:
        """A snapshot with an unknown schema_version is treated as if no snapshot exists."""
        trigger_file = self.tempdir / "server.py"
        trigger_file.write_text("# server\n", encoding="utf-8")

        harness_root = self._make_case_dir()
        harness_root.mkdir(exist_ok=True)
        state_dir = harness_root / "bridge-root" / "state"
        state_dir.mkdir(parents=True)
        wrong_snap = state_dir / "code-watcher-snapshot.json"
        wrong_snap.write_text(
            json.dumps({"schema_version": 99, "mtimes": {str(trigger_file): 0}}),
            encoding="utf-8",
        )
        wrong_mtime_ns = wrong_snap.stat().st_mtime_ns

        harness = SupervisorHarness(harness_root, watch_paths=[trigger_file])
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")
        # Wait for the first-run path to overwrite the wrong-version snapshot with a valid one.
        harness.wait_for_snapshot_ready(old_file_mtime_ns=wrong_mtime_ns)

        self.assertEqual(len(harness.launch_pids()), 1)
        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)

    def test_non_trigger_file_change_at_startup_does_not_restart(self) -> None:
        """A non-restart-trigger file that changed between sessions must not queue a restart."""
        watch_dir = self.tempdir / "nt-watch"
        watch_dir.mkdir()
        non_trigger_file = watch_dir / "dashboard_server.py"
        non_trigger_file.write_text("# original\n", encoding="utf-8")
        trigger_file = watch_dir / "server.py"
        trigger_file.write_text("# server\n", encoding="utf-8")

        # Session 1: establish baseline
        h1_root = self.tempdir / "nt1"
        h1_root.mkdir()
        (h1_root / "bridge-root" / "state").mkdir(parents=True)
        h1 = SupervisorHarness(h1_root, watch_paths=[non_trigger_file, trigger_file])
        self._harnesses.append(h1)
        h1.wait_for_launch_count(1)
        h1.stdout_stream.wait_for(b"READY ")
        snap = h1.wait_for_snapshot()
        h1.stdin_stream.close()
        h1.wait_for_exit()

        # Edit only the non-trigger file between sessions.
        self._write_and_wait_new_mtime(non_trigger_file, "# changed between sessions\n")

        # Session 2
        h2_root = self.tempdir / "nt2"
        h2_root.mkdir()
        state2 = h2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        snap2_path = state2 / "code-watcher-snapshot.json"
        shutil.copy2(str(snap), str(snap2_path))
        # shutil.copy2 preserves the source mtime; record it so we can detect the rewrite.
        old_file_mtime_ns = snap2_path.stat().st_mtime_ns

        h2 = SupervisorHarness(h2_root, watch_paths=[non_trigger_file, trigger_file])
        self._harnesses.append(h2)
        h2.wait_for_launch_count(1)
        h2.stdout_stream.wait_for(b"READY ")
        # Wait for the no-change else-branch to finish: it rewrites the snapshot (advancing
        # its mtime), proving startup detection completed.  Deterministic replacement for
        # time.sleep(0.2) which can give false-negatives on a loaded CI machine.
        h2.wait_for_snapshot_ready(old_file_mtime_ns=old_file_mtime_ns)

        self.assertEqual(len(h2.launch_pids()), 1, "non-trigger file change must not trigger startup restart")
        queued = [e for e in h2.audit_events() if e.get("action") == "mcp_server_restart_queued_from_persisted_snapshot"]
        self.assertEqual(len(queued), 0)

        h2.stdin_stream.close()
        self.assertEqual(h2.wait_for_exit(), 0)

    def test_post_restart_snapshot_prevents_re_trigger(self) -> None:
        """After a successful in-session restart, the saved snapshot prevents re-triggering on next startup."""
        watch_dir = self.tempdir / "pr-watch"
        watch_dir.mkdir()
        trigger_file = watch_dir / "agent_bridge.py"
        trigger_file.write_text("# v1\n", encoding="utf-8")

        # Session 1: start, trigger in-session restart, let snapshot be saved
        h1_root = self.tempdir / "pr1"
        h1_root.mkdir()
        (h1_root / "bridge-root" / "state").mkdir(parents=True)
        h1 = SupervisorHarness(h1_root, watch_paths=[trigger_file])
        self._harnesses.append(h1)
        h1.wait_for_launch_count(1)
        h1.stdout_stream.wait_for(b"READY ")

        # Trigger in-session restart — use the robust mtime helper to guarantee
        # the filesystem reflects the write before the polling loop's next tick.
        self._write_and_wait_new_mtime(trigger_file, "# v2\n")
        h1.wait_for_launch_count(2)
        h1.wait_for_audit_events("mcp_server_self_restarted")

        # Give snapshot a moment to be written
        snap_path = h1.wait_for_snapshot()
        h1.stdin_stream.close()
        h1.wait_for_exit()

        # Session 2 uses the post-restart snapshot — no re-trigger expected
        h2_root = self.tempdir / "pr2"
        h2_root.mkdir()
        state2 = h2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        shutil.copy2(str(snap_path), str(state2 / "code-watcher-snapshot.json"))

        h2 = SupervisorHarness(h2_root, watch_paths=[trigger_file])
        self._harnesses.append(h2)
        h2.wait_for_launch_count(1)
        h2.stdout_stream.wait_for(b"READY ")
        time.sleep(0.2)

        self.assertEqual(len(h2.launch_pids()), 1, "post-restart snapshot must prevent re-trigger")
        queued = [e for e in h2.audit_events() if e.get("action") == "mcp_server_restart_queued_from_persisted_snapshot"]
        self.assertEqual(len(queued), 0)

        h2.stdin_stream.close()
        self.assertEqual(h2.wait_for_exit(), 0)

    def test_wrapper_self_restart_snapshot_prevents_exit_77_loop(self) -> None:
        """A handled server_wrapper.py edit must be saved before exit 77.

        Without this, the trampoline relaunch would load a stale snapshot, see
        the same server_wrapper.py mtime again, and immediately exit 77 forever.
        """
        wrapper_file = self.tempdir / "server_wrapper.py"
        wrapper_file.write_text("# wrapper v1\n", encoding="utf-8")

        h1_root = self._make_case_dir()
        h1 = SupervisorHarness(h1_root, watch_paths=[wrapper_file])
        self._harnesses.append(h1)
        h1.wait_for_launch_count(1)
        h1.stdout_stream.wait_for(b"READY ")
        snap1 = h1.wait_for_snapshot()
        h1.stdin_stream.close()
        h1.wait_for_exit()

        self._write_and_wait_new_mtime(wrapper_file, "# wrapper v2\n")
        mtime_v2 = wrapper_file.stat().st_mtime_ns

        h2_root = self._make_case_dir()
        state2 = h2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        shutil.copy2(str(snap1), str(state2 / "code-watcher-snapshot.json"))
        h2 = SupervisorHarness(h2_root, watch_paths=[wrapper_file])
        self._harnesses.append(h2)
        self.assertEqual(h2.wait_for_exit(timeout=5.0), SERVER_WRAPPER_SELF_RESTART_EXIT_CODE)
        h2.wait_for_audit_events("mcp_server_restart_queued_from_persisted_snapshot")
        h2.wait_for_audit_events("mcp_server_wrapper_self_restart_requested")

        snap2_data = json.loads((state2 / "code-watcher-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snap2_data["mtimes"][str(wrapper_file)], mtime_v2)

        h3_root = self._make_case_dir()
        state3 = h3_root / "bridge-root" / "state"
        state3.mkdir(parents=True)
        shutil.copy2(str(state2 / "code-watcher-snapshot.json"), str(state3 / "code-watcher-snapshot.json"))
        old_file_mtime_ns = (state3 / "code-watcher-snapshot.json").stat().st_mtime_ns
        h3 = SupervisorHarness(h3_root, watch_paths=[wrapper_file])
        self._harnesses.append(h3)
        h3.wait_for_launch_count(1)
        h3.stdout_stream.wait_for(b"READY ")
        h3.wait_for_snapshot_ready(old_file_mtime_ns=old_file_mtime_ns)

        self.assertEqual(len(h3.launch_pids()), 1)
        self.assertEqual(
            [event for event in h3.audit_events() if event.get("action") == "mcp_server_restart_queued_from_persisted_snapshot"],
            [],
        )
        h3.stdin_stream.close()
        self.assertEqual(h3.wait_for_exit(), 0)

    def test_new_trigger_file_between_sessions_restarts(self) -> None:
        """A brand-new trigger file that didn't exist in the old snapshot counts as changed."""
        original_file = self.tempdir / "server.py"
        original_file.write_text("# server\n", encoding="utf-8")

        # Session 1: snapshot captures only original_file
        h1_root = self.tempdir / "nf1"
        h1_root.mkdir()
        (h1_root / "bridge-root" / "state").mkdir(parents=True)
        h1 = SupervisorHarness(h1_root, watch_paths=[original_file])
        self._harnesses.append(h1)
        h1.wait_for_launch_count(1)
        h1.stdout_stream.wait_for(b"READY ")
        snap = h1.wait_for_snapshot()
        h1.stdin_stream.close()
        h1.wait_for_exit()

        # A new trigger file appears between sessions (not in old snapshot)
        new_file = self.tempdir / "agent_bridge.py"
        new_file.write_text("# new file\n", encoding="utf-8")

        # Session 2 watches both files; old snapshot has no entry for new_file
        h2_root = self.tempdir / "nf2"
        h2_root.mkdir()
        state2 = h2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        shutil.copy2(str(snap), str(state2 / "code-watcher-snapshot.json"))

        h2 = SupervisorHarness(h2_root, watch_paths=[original_file, new_file])
        self._harnesses.append(h2)
        h2.wait_for_launch_count(2)

        events = h2.wait_for_audit_events("mcp_server_restart_queued_from_persisted_snapshot")
        self.assertEqual(len(events), 1)
        self.assertIn(str(new_file), events[0]["changed_files"])

        h2.stdin_stream.close()
        self.assertEqual(h2.wait_for_exit(), 0)

    def test_aborted_restart_leaves_snapshot_stale_for_next_startup(self) -> None:
        """Option B invariant: when a restart is aborted by the rate-limiter the snapshot
        is NOT updated.  The next startup must therefore re-detect the unhandled change.

        Flow:
          Session 1 — establishes baseline snapshot.
          Between sessions — trigger file is edited (mtime advances).
          Session 2 — uses tight restart limit (max 1 per window).
            * First startup-detected restart succeeds; snapshot is updated to new mtime.
            * Second file edit triggers a second in-session restart.
            * Rate-limiter fires and aborts that second restart.
            * Snapshot is NOT updated for the second (aborted) change.
          Session 3 — started with session 2's snapshot.
            * session 2's snapshot reflects the first edit (handled) but not the second
              (aborted) edit.
            * Session 3 must detect the second change and restart.
        """
        trigger_file = self.tempdir / "agent_bridge.py"
        trigger_file.write_text("# v1\n", encoding="utf-8")

        # --- Session 1: establish baseline ---
        h1_root = self._make_case_dir()
        h1 = SupervisorHarness(h1_root, watch_paths=[trigger_file])
        self._harnesses.append(h1)
        h1.wait_for_launch_count(1)
        h1.stdout_stream.wait_for(b"READY ")
        snap1 = h1.wait_for_snapshot()
        h1.stdin_stream.close()
        h1.wait_for_exit()

        # --- Edit file once between sessions ---
        self._write_and_wait_new_mtime(trigger_file, "# v2 - edit 1\n")
        mtime_v2 = trigger_file.stat().st_mtime_ns

        # --- Session 2: tight restart limit (max 1 restart per 60 s window) ---
        tight_config = SupervisorConfig(
            poll_interval_seconds=0.05,
            debounce_seconds=0.05,
            idle_seconds=0.05,
            terminate_timeout_seconds=0.5,
            restart_window_seconds=60.0,   # wide window…
            max_restarts_per_window=1,     # …but only 1 restart allowed
            chunk_size=4096,
            loop_sleep_seconds=0.01,
        )
        h2_root = self._make_case_dir()
        state2 = h2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        shutil.copy2(str(snap1), str(state2 / "code-watcher-snapshot.json"))

        h2 = SupervisorHarness(h2_root, watch_paths=[trigger_file], config=tight_config)
        self._harnesses.append(h2)

        # First startup-detected restart: snapshot starts at v1, file is v2 -> restart fires.
        # The audit row is the reliable signal here; on a fast restart the first
        # fake child can be terminated before its launch-log append reaches disk.
        h2.wait_for_audit_events("mcp_server_restart_queued_from_persisted_snapshot")
        h2.wait_for_audit_events("mcp_server_self_restarted")

        # Give the post-restart snapshot write a moment to land.
        h2.wait_for_snapshot()

        # Record snapshot mtime after the first (successful) restart.
        snap2_data_after_restart1 = json.loads((state2 / "code-watcher-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(
            snap2_data_after_restart1["mtimes"][str(trigger_file)], mtime_v2,
            "snapshot must reflect v2 mtime after the first successful restart",
        )

        # Edit file a second time — this in-session change will be rate-limited.
        self._write_and_wait_new_mtime(trigger_file, "# v3 - edit 2 (will be rate-limited)\n")
        mtime_v3 = trigger_file.stat().st_mtime_ns

        # Wait for the rate-limiter to fire and abort the second restart.
        h2.wait_for_audit_events("mcp_server_self_restart_aborted_loop")

        # Verify the snapshot was NOT updated to v3.
        snap2_data_after_abort = json.loads((state2 / "code-watcher-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(
            snap2_data_after_abort["mtimes"][str(trigger_file)], mtime_v2,
            "snapshot must still reflect v2 (not v3) after the aborted restart",
        )

        h2.stdin_stream.close()
        # Wait for session 2 to fully exit before starting session 3. Without this,
        # h2's watch thread may still be polling trigger_file while h3 starts, producing
        # a spurious detection in h2 that could contaminate h3's snapshot on disk.
        h2.wait_for_exit()

        # --- Session 3: starts with session 2's snapshot (reflects v2, not v3) ---
        h3_root = self._make_case_dir()
        state3 = h3_root / "bridge-root" / "state"
        state3.mkdir(parents=True)
        shutil.copy2(str(state2 / "code-watcher-snapshot.json"), str(state3 / "code-watcher-snapshot.json"))

        h3 = SupervisorHarness(h3_root, watch_paths=[trigger_file])
        self._harnesses.append(h3)

        # Session 3 must detect the v3 change (unhandled in session 2) and restart.
        # Use audit rather than launch-log count: startup restarts can kill the
        # first fake child before its append reaches disk.
        events = h3.wait_for_audit_events("mcp_server_restart_queued_from_persisted_snapshot")
        self.assertEqual(len(events), 1)
        self.assertIn(str(trigger_file), events[0]["changed_files"])
        h3.wait_for_audit_events("mcp_server_self_restarted")

        h3.stdin_stream.close()
        self.assertEqual(h3.wait_for_exit(), 0)

    def test_snapshot_write_failure_does_not_crash_supervisor(self) -> None:
        """A snapshot write failure (e.g. permissions, disk full) must be non-fatal.

        Three required properties:
        1. Supervisor stays alive after the failed startup write.
        2. A stderr warning and an audit event are emitted so the failure is observable.
        3. In-session change detection still triggers a restart despite the blocked path.

        Failure injection: monkeypatch _sw.write_json to raise for the snapshot path.
        This is cross-platform (unlike a directory-at-path approach, which on Windows
        causes shutil.move to silently move the temp file into the directory — no exception).
        """
        trigger_file = self.tempdir / "agent_bridge.py"
        trigger_file.write_text("# v1\n", encoding="utf-8")

        harness_root = self._make_case_dir()

        # --- Inject write failure and capture stderr before starting the harness ---
        original_write_json = _sw.write_json
        write_failure_count = [0]

        def _write_json_snapshot_fail(path, value):
            if path.name == _sw.CODE_WATCHER_SNAPSHOT_FILENAME:
                write_failure_count[0] += 1
                raise OSError("simulated disk full for snapshot test")
            original_write_json(path, value)

        captured_stderr = io.StringIO()
        old_stderr = sys.stderr

        _sw.write_json = _write_json_snapshot_fail
        sys.stderr = captured_stderr
        try:
            harness = SupervisorHarness(harness_root, watch_paths=[trigger_file])
            self._harnesses.append(harness)
            harness.wait_for_launch_count(1)
            harness.stdout_stream.wait_for(b"READY ")

            # Wait for the write-failure audit event as the observable signal that
            # _save_watcher_snapshot was called AND failed (races the watch thread).
            harness.wait_for_audit_events("mcp_server_snapshot_write_failed", timeout=3.0)

            # Property 1: no crash from the failed write.
            self.assertIsNone(harness.result.get("exit_code"), "supervisor must not have exited")
            self.assertGreater(write_failure_count[0], 0, "expected at least one write failure")

            # Property 3: normal in-session change detection still works.
            self._write_and_wait_new_mtime(trigger_file, "# v2\n")
            harness.wait_for_launch_count(2)
            restart_events = harness.wait_for_audit_events("mcp_server_self_restarted")
            self.assertGreaterEqual(len(restart_events), 1)
            self.assertIsNone(
                harness.result.get("exit_code"),
                "supervisor must not have exited after write-failure restart",
            )

            harness.stdin_stream.close()
            self.assertEqual(harness.wait_for_exit(), 0)
        finally:
            _sw.write_json = original_write_json
            sys.stderr = old_stderr

        # Property 2a: stderr warning emitted (check after thread is done to avoid races).
        stderr_output = captured_stderr.getvalue()
        self.assertIn(
            "snapshot write failed",
            stderr_output,
            "expected stderr warning for snapshot write failure, got: %r" % stderr_output,
        )
        # Property 2b: audit event recorded (testable without stderr capture).
        fail_events = [e for e in harness.audit_events() if e.get("action") == "mcp_server_snapshot_write_failed"]
        self.assertGreaterEqual(
            len(fail_events),
            1,
            "expected at least one mcp_server_snapshot_write_failed audit event",
        )


    def test_deleted_trigger_file_between_sessions_restarts(self) -> None:
        """A trigger file present in session 1 but deleted before session 2 must trigger
        a restart on startup (mtime None vs stored int — the deletion is a change).
        This exercises the None-vs-int branch in _load_and_apply_persisted_snapshot."""
        watch_dir = self.tempdir / "del-watch"
        watch_dir.mkdir()
        trigger_file = watch_dir / "agent_bridge.py"
        trigger_file.write_text("# original\n", encoding="utf-8")

        # Session 1: establish baseline with the file present.
        h1_root = self._make_case_dir()
        (h1_root / "bridge-root" / "state").mkdir(parents=True)
        h1 = SupervisorHarness(h1_root, watch_paths=[trigger_file])
        self._harnesses.append(h1)
        h1.wait_for_launch_count(1)
        h1.stdout_stream.wait_for(b"READY ")
        snap1 = h1.wait_for_snapshot()
        snap1_data = json.loads(snap1.read_text(encoding="utf-8"))
        self.assertIsNotNone(snap1_data["mtimes"].get(str(trigger_file)), "snapshot must record mtime for existing file")

        h1.stdin_stream.close()
        h1.wait_for_exit()

        # Delete the file between sessions.
        trigger_file.unlink()
        self.assertFalse(trigger_file.exists())

        # Session 2: snapshot has int mtime, current is None (file absent) → change detected.
        h2_root = self._make_case_dir()
        state2 = h2_root / "bridge-root" / "state"
        state2.mkdir(parents=True)
        shutil.copy2(str(snap1), str(state2 / "code-watcher-snapshot.json"))

        h2 = SupervisorHarness(h2_root, watch_paths=[trigger_file])
        self._harnesses.append(h2)

        events = h2.wait_for_audit_events("mcp_server_restart_queued_from_persisted_snapshot")
        self.assertEqual(len(events), 1)
        self.assertIn(str(trigger_file), events[0]["changed_files"])
        h2.wait_for_audit_events("mcp_server_self_restarted")

        h2.stdin_stream.close()
        self.assertEqual(h2.wait_for_exit(), 0)

    def test_pending_request_ids_cleared_on_graceful_drain_timeout(self) -> None:
        """When a code change is pending while a JSON-RPC request is in-flight, the wrapper
        must defer the restart until _pending_request_ids drains — and force-restart after
        graceful_restart_timeout_seconds even if the drain never completes.

        The echo server reflects the request bytes verbatim.  Because the echo is a JSON
        object with 'id' and 'method' but no 'result'/'error', _parse_stdout_for_responses
        never clears the ID — permanently simulating a hung tool call.

        The test proves three properties:
        1. The restart eventually happens (graceful timeout fires; _pending_request_ids cleared).
        2. The wrapper survives and is still functional after the force-restart.
        3. A subsequent code change triggers a second restart, confirming the cleared IDs do
           not re-block the restart path.
        """
        trigger_file = self.tempdir / "agent_bridge.py"
        trigger_file.write_text("# v1\n", encoding="utf-8")

        # Use idle_seconds=0.0 to disable the idle gate so I/O activity doesn't delay
        # the restart check.  graceful_restart_timeout_seconds=0.25 keeps the test fast.
        config = SupervisorConfig(
            poll_interval_seconds=0.05,
            debounce_seconds=0.05,
            idle_seconds=0.0,
            terminate_timeout_seconds=0.5,
            restart_window_seconds=5.0,
            max_restarts_per_window=4,
            chunk_size=4096,
            loop_sleep_seconds=0.01,
            graceful_restart_timeout_seconds=0.25,
        )

        harness_root = self._make_case_dir()
        harness = SupervisorHarness(harness_root, watch_paths=[trigger_file], config=config)
        self._harnesses.append(harness)
        harness.wait_for_launch_count(1)
        harness.stdout_stream.wait_for(b"READY ")

        # Push a JSON-RPC request that the echo server will reflect as-is.
        # The reflected bytes have 'id' but no 'result'/'error', so
        # _parse_stdout_for_responses never clears the ID from _pending_request_ids.
        in_flight = b'{"jsonrpc":"2.0","id":"grace-1","method":"tools/call","params":{"name":"t"}}\n'
        harness.stdin_stream.push(in_flight)
        # Wait for the echo to confirm the ID has been parsed and added to pending.
        harness.stdout_stream.wait_for(b"grace-1", timeout=3.0)

        # Trigger a restart — the pending ID will block it until the graceful timeout.
        # Measure latency: it will include graceful_restart_timeout_seconds + constant overhead
        # (poll, debounce, tool-manifest wait of ~1 s because the fake server has no manifest).
        self._write_and_wait_new_mtime(trigger_file, "# v2\n")
        t_v2 = time.monotonic()

        # Property 1: restart happens despite the un-drained in-flight request.
        # Timeout is well above graceful_restart_timeout_seconds (0.25 s) to absorb CI jitter.
        harness.wait_for_audit_events("mcp_server_self_restarted", timeout=5.0)
        first_restart_latency = time.monotonic() - t_v2
        harness.wait_for_launch_count(2, timeout=5.0)

        # Property 2: supervisor still alive after force-restart.
        self.assertIsNone(harness.result.get("exit_code"), "supervisor must not have exited")

        # Property 3: _pending_request_ids was cleared — second restart is measurably faster.
        # Differential timing proof: both restarts share the same constant overhead
        # (poll + debounce + tool-manifest wait). The first restart additionally spent
        # graceful_restart_timeout_seconds waiting for IDs to drain. If _pending_request_ids
        # was NOT cleared, the second restart would also spend that same time, giving
        # approximately equal latencies (improvement ≈ 0). Asserting improvement >
        # graceful_restart_timeout_seconds / 2 confirms the IDs were cleared.
        self._write_and_wait_new_mtime(trigger_file, "# v3\n")
        t_v3 = time.monotonic()
        harness.wait_for_audit_events("mcp_server_self_restarted", expected=2, timeout=5.0)
        second_restart_latency = time.monotonic() - t_v3
        harness.wait_for_launch_count(3, timeout=5.0)

        improvement = first_restart_latency - second_restart_latency
        self.assertGreater(
            improvement,
            config.graceful_restart_timeout_seconds / 2,
            "second restart not sufficiently faster than first (%.3fs vs %.3fs, improvement %.3fs); "
            "_pending_request_ids may not have been cleared"
            % (first_restart_latency, second_restart_latency, improvement),
        )

        self.assertIsNone(harness.result.get("exit_code"), "supervisor must not have exited after second restart")

        harness.stdin_stream.close()
        self.assertEqual(harness.wait_for_exit(), 0)


if __name__ == "__main__":
    unittest.main()
