"""
Phase 0 - Contract Freeze Tests

These tests are the contract gate for the agent-bridge refactor (REFACTOR_PLAN.md).
Most should FAIL TODAY against current behavior; the refactor's job is to make them
pass without regressing the existing 18 tests in test_agent_bridge.py.

Tests are grouped by area. Each test:
- States the invariant from REFACTOR_PLAN.md frozen-protocol-invariants
- Documents expected vs current behavior
- Either runs and fails (preferred), or skips with a reason if infrastructure
  doesn't exist yet (e.g. parent_thread_id field hasn't been added)

Run:
    py -3 -m unittest tools.agent-bridge.test_phase0_contract -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_bridge import AgentBridge
from bootstrap_session import bootstrap
from configure_watcher import configure_watcher
import probe_server
import watcher


ROOT = Path(__file__).resolve().parents[2]


class Phase0ContractTests(unittest.TestCase):
    """Phase 0: Contract Freeze. Every test maps to one Phase 0 test in REFACTOR_PLAN.md."""

    def setUp(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="agent-bridge-phase0-"))
        self.state_dir = self.tempdir / "state"

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Test 1: check_inbox(None) scans all buckets, never silently == default
    # Invariant: REFACTOR_PLAN.md #5
    # ------------------------------------------------------------------
    def test_01_check_inbox_none_does_not_silently_mean_default(self) -> None:
        """check_inbox(None) must scan all buckets for the agent OR reject explicitly.
        It must NEVER silently resolve to the deprecated 'default' bucket.

        Current behavior: session_id=None normalizes to 'default', which then
        misses real session/project bucket messages. Reproduced by Codex.
        """
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.send_to_peer(
            "codex", "claude",
            "[[handoff:claude]] private bucket message",
            session_id="claude-live",
        )

        result = bridge.check_inbox("claude", session_id=None, mark_read=False)

        # Invariant: must not return empty just because the private bucket holds
        # the message. Either it scans all buckets (canonical), or it rejects
        # session_id=None explicitly. Silent default-fallback is the failure mode.
        self.assertNotEqual(
            result.message, "default",
            "check_inbox(None) silently resolved to deprecated 'default' bucket",
        )
        # Canonical: scan all buckets and find the private message.
        self.assertEqual(
            result.status, "messages",
            "check_inbox(None) should find messages across all buckets",
        )

    # ------------------------------------------------------------------
    # Test 2: Superseded sender cannot send work to active target bucket
    # Invariant: #1
    # ------------------------------------------------------------------
    def test_02_superseded_sender_cannot_send_to_active_target_bucket(self) -> None:
        """A superseded sender should be rejected when sending normal work to
        the target's active bucket — not just to its own session bucket.

        Current behavior: send_to_peer only verifies sender liveness when the
        addressed session_id matches the sender's own. Normal work addressing
        the target bucket bypasses this check. Reproduced by Codex.
        """
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-old", project="mlv-app")
        bridge.activate_session("codex", "codex-active", project="mlv-app")
        bridge.activate_session("claude", "claude-new", project="mlv-app")
        # claude-old is now superseded by claude-new.

        # claude-old sends normal work addressed at codex-active's bucket
        # (NOT addressed at its own claude-old bucket).
        result = bridge.send_to_peer(
            "claude", "codex",
            "[[handoff:codex]] should be rejected - sender is superseded",
            session_id="codex-active",
        )

        self.assertFalse(
            result.ok,
            "Superseded sender must not be able to send work to active target",
        )
        self.assertIn("superseded", result.message.lower())

    # ------------------------------------------------------------------
    # Test 3: Agent-level bucket rejects normal work; accepts control/recovery
    # Invariants: #2, #3
    # ------------------------------------------------------------------
    def test_03_agent_level_bucket_rejects_normal_work(self) -> None:
        """Agent-level inbox (e.g. session_id='codex') is reserved for
        control/recovery traffic. Normal work must be rejected there.
        """
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-1", project="mlv-app")
        bridge.activate_session("codex", "codex-1", project="mlv-app")

        result = bridge.send_to_peer(
            "claude", "codex",
            "[[handoff:codex]] regular work to agent-level bucket",
            session_id="codex",  # bare agent name = agent-level bucket
        )

        self.assertFalse(
            result.ok,
            "Agent-level bucket must reject normal work traffic",
        )

    def test_03b_agent_level_bucket_accepts_control(self) -> None:
        """Counterpart: agent-level bucket SHOULD accept control messages.
        This is the recovery channel that must remain available even under
        session/project backpressure.
        """
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-1", project="mlv-app")

        result = bridge.send_control_message(
            "claude", "codex",
            "ROUTE_REPAIR",
            "control to agent-level recovery bucket",
            "body",
            session_id="codex",
        )

        self.assertTrue(
            result.ok,
            "Agent-level bucket must accept control/recovery messages",
        )

    # ------------------------------------------------------------------
    # Test 4: 'default' rejected consistently across destructive ops
    # Invariant: #6
    # ------------------------------------------------------------------
    def test_04_default_rejected_by_send_clear_reset(self) -> None:
        """'default' is dead. Send already rejects it. Other destructive
        operations must not silently normalize to 'default' either.
        """
        bridge = AgentBridge(self.state_dir)

        # send_to_peer — already rejects (existing behavior)
        send_result = bridge.send_to_peer(
            "claude", "codex",
            "[[handoff:codex]] x",
            session_id="default",
        )
        self.assertFalse(send_result.ok, "send_to_peer should reject default")

        # clear_inbox — must reject default (currently may default through)
        clear_result = bridge.clear_inbox("claude", session_id="default")
        self.assertFalse(
            clear_result.ok,
            "clear_inbox must reject session_id='default'",
        )

        # reset_session — must reject default
        reset_result = bridge.reset_session("claude", session_id="default")
        self.assertFalse(
            reset_result.ok,
            "reset_session must reject session_id='default'",
        )

    # ------------------------------------------------------------------
    # Test 5: send_control_message replacement scoped to (bucket, type, source)
    # Invariant: control replacement must not delete unrelated controls
    # ------------------------------------------------------------------
    def test_05_control_replacement_scoped_by_type_and_source(self) -> None:
        """replace_existing_control=True should only replace the SAME control
        (same bucket, same control_type, same source). It must not delete
        unrelated control messages.

        Current behavior: replace removes ALL unread controls in the bucket.
        """
        bridge = AgentBridge(self.state_dir)

        # First control: HANDSHAKE from claude
        bridge.send_control_message(
            "claude", "codex", "HANDSHAKE",
            "first handshake", "body1",
            session_id="mlv-app",
        )

        # Second control: SESSION_UPDATE from claude (different type)
        bridge.send_control_message(
            "claude", "codex", "SESSION_UPDATE",
            "session update", "body2",
            session_id="mlv-app",
        )

        # Replace HANDSHAKE — should leave SESSION_UPDATE alone
        bridge.send_control_message(
            "claude", "codex", "HANDSHAKE",
            "second handshake", "body3",
            session_id="mlv-app",
            replace_existing_control=True,
        )

        inbox = bridge.peek_inbox("codex", session_id="mlv-app")
        self.assertEqual(inbox.status, "messages")
        types = {m.get("control_type") for m in inbox.data.get("messages", [])}
        self.assertIn(
            "SESSION_UPDATE", types,
            "replace_existing_control=True for HANDSHAKE deleted SESSION_UPDATE",
        )
        self.assertIn(
            "HANDSHAKE", types,
            "Replacement HANDSHAKE missing",
        )

    # ------------------------------------------------------------------
    # Test 6: end_session does not silently mark unread work read
    # Invariant: #4
    # ------------------------------------------------------------------
    def test_06_end_session_does_not_silently_mark_unread_read(self) -> None:
        """end_session must NOT mark unread work messages read just because
        the session is ending. Unread work should be promoted to project
        bucket (Phase 5) or quarantined with audit metadata, never silently
        consumed.
        """
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.activate_session("codex", "codex-live", project="mlv-app")

        send = bridge.send_to_peer(
            "codex", "claude",
            "[[handoff:claude]] unread work message",
            session_id="claude-live",
        )
        self.assertTrue(send.ok)
        msg_id = send.data.get("id")

        # End the claude session — without surfacing the message first.
        bridge.end_session("claude", "claude-live", project="mlv-app")

        # The message must still be findable as unread (either in claude-live
        # bucket if not yet promoted, or in mlv-app project bucket).
        # It must NOT have read_at set just because the session ended.
        inbox_path = bridge.inbox_path("claude")
        rows = []
        if inbox_path.exists():
            with inbox_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))

        target = next((r for r in rows if r.get("id") == msg_id), None)
        self.assertIsNotNone(target, "Message vanished entirely after end_session")
        self.assertIsNone(
            target.get("read_at"),
            "end_session silently marked unread work message read",
        )

    # ------------------------------------------------------------------
    # Test 7: Wake command failure does NOT add to watcher seen_ids
    # Invariant: #7. THIS IS THE BUG WE HIT LIVE.
    # ------------------------------------------------------------------
    def test_07_wake_command_failure_does_not_mark_seen(self) -> None:
        """When wake_codex.ps1 (or any on_message_command) exits non-zero,
        the watcher must NOT add the message id to seen_ids. The message
        must remain available for retry.

        Current behavior (live-demonstrated): subprocess.Popen(...) returns
        a PID; watcher.run_command_for_session returns True if PID exists;
        seen_ids gets updated regardless of exit code. The em-dash mojibake
        regression demonstrated this in production.

        Implementation: import watcher; mock subprocess.Popen to return a
        process object whose returncode is non-zero; trigger one poll cycle;
        assert seen_ids unchanged for the failed message.
        """
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-failed-wake",
            "session_id": "codex-live",
            "from": "claude",
            "to": "codex",
            "body": "wake me",
            "delivered_message": "wake me",
            "read_at": None,
        }
        with inbox_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(message) + "\n")

        seen_ids: set[str] = set()
        state_path = self.tempdir / "watcher-state.json"
        failed = Mock(returncode=1, stdout="", stderr="boom")

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run", return_value=failed):
            processed = watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command": "fake-wake",
                },
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(processed, [])
        self.assertNotIn("msg-failed-wake", seen_ids)
        self.assertFalse(state_path.exists(), "failed wake must not persist seen_ids")

    def test_07b_wake_exit_zero_requires_seen_receipt_before_seen_ids(self) -> None:
        """A successful wake command is not delivery until check_inbox sets seen_at.

        This covers the live failure where wake_codex.ps1 typed text into the
        Codex composer but Enter was lost. The wake helper exited 0, but Codex
        never ran check_inbox, so the watcher must keep the message pending and
        retry instead of marking it seen.
        """
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-lost-input",
            "session_id": "codex-live",
            "from": "claude",
            "to": "codex",
            "body": "wake me",
            "delivered_message": "wake me",
            "seen_at": None,
            "read_at": None,
        }
        with inbox_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(message) + "\n")

        seen_ids: set[str] = set()
        state_path = self.tempdir / "watcher-state.json"
        succeeded = Mock(returncode=0, stdout="", stderr="")
        session_config = {
            "agent": "codex",
            "session_id": "codex-live",
            "inbox": str(inbox_path),
            "on_message": "notify",
            "on_message_command": "fake-wake",
        }

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run", return_value=succeeded) as run:
            processed = watcher.process_session_once(
                session_config,
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
                grace_period_seconds=30,
            )
            self.assertEqual(processed, [])
            self.assertNotIn("msg-lost-input", seen_ids)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["pending_wake_verifications"][0]["message_id"], "msg-lost-input")

            watcher.process_session_once(
                session_config,
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
                grace_period_seconds=0,
            )
            self.assertEqual(run.call_count, 2)
            self.assertNotIn("msg-lost-input", seen_ids)

        message["seen_at"] = "2026-04-28T17:00:00+00:00"
        with inbox_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(message) + "\n")

        watcher.process_session_once(
            session_config,
            seen_ids=seen_ids,
            state_path=state_path,
            toasts_enabled=True,
            grace_period_seconds=0,
        )
        self.assertIn("msg-lost-input", seen_ids)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["pending_wake_verifications"], [])

    # ------------------------------------------------------------------
    # Test 8 & 9: configure_watcher race + sub-agent cannot mutate parent_thread_id
    # Invariant: #10. Phase 7.X infrastructure.
    # ------------------------------------------------------------------
    def test_08_concurrent_configure_watcher_preserves_parent_target(self) -> None:
        """Concurrent parent + sub-agent configure_watcher calls must
        preserve the parent's parent_thread_id setting. Sub-agent's
        ambient CODEX_THREAD_ID must not overwrite parent's."""
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        config_path = self.tempdir / "watcher-config.json"
        parent_thread = "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"
        subagent_thread = "11111111-1111-4111-8111-111111111111"

        configure_watcher(
            config_path=config_path,
            state_dir=self.state_dir,
            agent="codex",
            project="mlv-app",
            cwd=str(ROOT),
            python_executable=sys.executable,
            parent_thread_id=parent_thread,
            parent_thread_provenance="parent",
        )
        with patch.dict(os.environ, {"CODEX_THREAD_ID": subagent_thread}):
            configure_watcher(
                config_path=config_path,
                state_dir=self.state_dir,
                agent="codex",
                project="mlv-app",
                cwd=str(ROOT),
                python_executable=sys.executable,
            )

        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertEqual(config["codex_parent_thread_id"], parent_thread)
        commands = [
            entry.get("on_message_command", "")
            for entry in config["sessions"]
            if entry.get("agent") == "codex"
        ]
        self.assertTrue(any(parent_thread in command for command in commands))
        self.assertFalse(any(subagent_thread in command for command in commands))

    def test_09_sub_agent_cannot_mutate_parent_thread_id(self) -> None:
        """A configure_watcher call from a sub-agent context must be
        rejected when attempting to write parent_thread_id. Only a
        process proven to be the controlling parent may write it."""
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("codex", "codex-live", project="mlv-app")

        with self.assertRaises(ValueError):
            configure_watcher(
                config_path=self.tempdir / "watcher-config.json",
                state_dir=self.state_dir,
                agent="codex",
                project="mlv-app",
                cwd=str(ROOT),
                python_executable=sys.executable,
                parent_thread_id="11111111-1111-4111-8111-111111111111",
                parent_thread_provenance="sub-agent",
            )

    # ------------------------------------------------------------------
    # Test 10: Bootstrap watcher-config test does not leak watcher subprocess
    # Invariant: cleanup hygiene
    # ------------------------------------------------------------------
    def test_10_bootstrap_watcher_test_does_not_leak_subprocess(self) -> None:
        """The existing test_bootstrap_updates_watcher_config in
        test_agent_bridge.py emits a ResourceWarning because it spawns a
        real watcher subprocess that survives the test. Bootstrap must
        expose a no-watcher seam."""
        config_path = self.tempdir / "watcher-config.json"

        result = bootstrap(
            state_dir=self.state_dir,
            agent="codex",
            cwd=str(ROOT),
            previous_session_id=None,
            session_id="codex-live",
            project=None,
            handshake_retries=1,
            watcher_config=config_path,
            start_watcher=False,
        )

        self.assertTrue(config_path.exists())
        self.assertIsNotNone(result["watcher"])
        self.assertEqual(
            result["watcher_process"],
            {"status": "not_started", "reason": "start_watcher_false"},
        )

    # ------------------------------------------------------------------
    # Test 11: wait_inbox rejects non-string session_ids at MCP boundary
    # Invariant: schema strictness
    # ------------------------------------------------------------------
    def test_11_wait_inbox_rejects_non_string_session_ids(self) -> None:
        """wait_inbox(session_ids=[123, None, 'ok']) must reject at the
        MCP boundary, not silently coerce or fail mid-loop."""
        bridge = AgentBridge(self.state_dir)
        result = bridge.wait_inbox("codex", session_ids=[123, None, "ok"], timeout_seconds=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "rejected")
        self.assertIn("list of strings", result.message)

    # ------------------------------------------------------------------
    # Test 12: clear_bucket clears associated dedupe / seen_hash state
    # Invariant: state consistency
    # ------------------------------------------------------------------
    def test_12_clear_inbox_also_clears_seen_hash_state(self) -> None:
        """clear_inbox (eventually clear_bucket) must clear matching
        seen_hash dedupe state. Otherwise a re-sent identical message
        gets blocked by stale dedupe after a clear.

        Current behavior: clear_inbox resets some session state but may
        leave seen_hashes intact, causing dedup false-positives.
        """
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")

        # Send a message
        body = "[[handoff:claude]] dedupe test message body"
        first = bridge.send_to_peer("codex", "claude", body, session_id="claude-live")
        self.assertTrue(first.ok)

        # Clear claude's inbox for this session
        bridge.clear_inbox("claude", session_id="claude-live")

        # Re-send the same body — should succeed because clear cleared dedupe
        second = bridge.send_to_peer("codex", "claude", body, session_id="claude-live")
        self.assertTrue(
            second.ok,
            "After clear_inbox, identical message blocked by stale dedupe state",
        )

    # ------------------------------------------------------------------
    # Test 13: Probe tooling cannot mutate live state without --mutate
    # Invariant: probe safety
    # ------------------------------------------------------------------
    def test_13_probe_server_default_does_not_mutate_live_state(self) -> None:
        """probe_server.py must default to read-only / temp-state mode.
        Mutating live bridge state must require explicit --mutate."""
        parser = probe_server.build_parser()
        args = parser.parse_args([])
        self.assertFalse(args.mutate)
        self.assertIsNone(args.state_dir)


if __name__ == "__main__":
    unittest.main()
