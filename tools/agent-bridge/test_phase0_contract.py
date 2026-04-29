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
from core.runtime import peer_runtime_path_for_state_dir, write_runtime_breadcrumb
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
        bridge.activate_session("codex", "codex-live", project="mlv-app")
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
    # Test 2: Sender must be provably active to send work to active target bucket
    # Invariant: #1
    # ------------------------------------------------------------------
    def test_02_sender_must_have_an_active_session_to_send_to_active_target_bucket(self) -> None:
        """Normal work addressed at an active target bucket must be rejected
        if the sender agent cannot be proven active in that project.

        send_to_peer() carries `from_agent` plus a target bucket, but not the
        sender session id. That means the strongest sound contract here is:
        the bridge must prove the sender agent currently has an active session
        in the project before accepting active-target routing. It cannot
        distinguish which historical sender thread invoked the call once both
        active and superseded sender sessions exist.
        """
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-old", project="mlv-app")
        bridge.activate_session("codex", "codex-active", project="mlv-app")
        registry = bridge._load_session_registry()
        registry["projects"]["mlv-app"]["active"].pop("claude", None)
        bridge._save_session_registry(registry)

        result = bridge.send_to_peer(
            "claude", "codex",
            "[[handoff:codex]] should be rejected - sender is not proven active",
            session_id="codex-active",
        )

        self.assertFalse(
            result.ok,
            "Sender without an active session must not be able to send work to active target",
        )
        self.assertIn("not proven active", result.message.lower())

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
        self.assertTrue(state_path.exists(), "failed wake should persist watcher state for rate limiting/breaker tracking")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["seen_ids"], [])
        self.assertEqual(state["pending_wake_verifications"], [])

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

    def test_07c_wake_exit_3_marks_seen_without_retry(self) -> None:
        """Wrong-chat detection is a permanent wake failure, not a retry loop."""
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-wrong-chat",
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
        wrong_chat = Mock(
            returncode=3,
            stdout="[wake_codex] WARNING: foreground window title 'Other Chat' does not contain expected marker 'mlv-app'.",
            stderr="",
        )

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run", return_value=wrong_chat) as run:
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
        self.assertEqual(run.call_count, 1)
        self.assertIn("msg-wrong-chat", seen_ids)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["pending_wake_verifications"], [])
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        wrong_chat_events = [row for row in audit_rows if row.get("action") == "wake_skipped_wrong_chat"]
        self.assertEqual(len(wrong_chat_events), 1)
        self.assertEqual(wrong_chat_events[0]["message_id"], "msg-wrong-chat")

    def test_07d_watcher_resolves_command_template_from_peer_breadcrumb(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-template",
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

        write_runtime_breadcrumb(
            peer_runtime_path_for_state_dir(self.state_dir, "codex"),
            {
                "schema_version": 1,
                "agent": "codex",
                "session_id": "codex-live",
                "project": "mlv-app",
                "desktop_thread_id": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5",
                "deeplink_template": "codex://threads/{thread_id}",
            },
        )

        seen_ids: set[str] = set()
        state_path = self.tempdir / "watcher-state.json"
        succeeded = Mock(returncode=0, stdout="", stderr="")

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run", return_value=succeeded) as run:
            watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "project": "mlv-app",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command_template": ["fake-wake", "-ThreadId", "{desktop_thread_id}"],
                },
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(run.call_args.args[0], ["fake-wake", "-ThreadId", "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_07e_missing_peer_breadcrumb_marks_seen_without_retry(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-no-peer",
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

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run") as run:
            processed = watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "project": "mlv-app",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command_template": ["fake-wake", "-ThreadId", "{desktop_thread_id}"],
                },
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(processed, [])
        self.assertFalse(run.called)
        self.assertIn("msg-no-peer", seen_ids)
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        no_peer_events = [row for row in audit_rows if row.get("action") == "wake_skipped_no_peer"]
        self.assertEqual(len(no_peer_events), 1)
        self.assertEqual(no_peer_events[0]["message_id"], "msg-no-peer")

    def test_07ea_subagent_peer_breadcrumb_marks_seen_without_retry(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-subagent-peer",
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

        write_runtime_breadcrumb(
            peer_runtime_path_for_state_dir(self.state_dir, "codex"),
            {
                "schema_version": 2,
                "agent": "codex",
                "session_id": "codex-live",
                "project": "mlv-app",
                "desktop_thread_id": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5",
                "bootstrap_origin": "subagent",
                "subagent_signals": {"env_marker": "CODEX_SUBAGENT=1"},
            },
        )

        seen_ids: set[str] = set()
        state_path = self.tempdir / "watcher-state.json"

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run") as run:
            processed = watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "project": "mlv-app",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command_template": ["fake-wake", "-ThreadId", "{desktop_thread_id}"],
                },
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(processed, [])
        self.assertFalse(run.called)
        self.assertIn("msg-subagent-peer", seen_ids)
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        provenance_events = [row for row in audit_rows if row.get("action") == "wake_skipped_bad_provenance"]
        self.assertEqual(len(provenance_events), 1)
        self.assertEqual(provenance_events[0]["message_id"], "msg-subagent-peer")

    def test_07eaa_subagent_peer_breadcrumb_triggers_route_repair_when_parent_known(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.activate_session(
            "codex",
            "codex-parent",
            project="mlv-app",
            bootstrap_origin="parent",
            trusted_parent_eligible=True,
        )
        bridge.record_session_runtime_metadata(
            agent="codex",
            session_id="codex-parent",
            project="mlv-app",
            desktop_thread_id="parent-thread",
            bootstrap_thread_id="parent-thread",
        )
        bridge.activate_session(
            "codex",
            "codex-child",
            project="mlv-app",
            bootstrap_origin="subagent",
            allow_supersede=True,
            trusted_parent_eligible=False,
        )

        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-subagent-rollback",
            "session_id": "codex-child",
            "from": "claude",
            "to": "codex",
            "body": "wake me",
            "delivered_message": "wake me",
            "seen_at": None,
            "read_at": None,
        }
        with inbox_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(message) + "\n")

        write_runtime_breadcrumb(
            peer_runtime_path_for_state_dir(self.state_dir, "codex"),
            {
                "schema_version": 2,
                "agent": "codex",
                "session_id": "codex-child",
                "project": "mlv-app",
                "desktop_thread_id": "child-thread",
                "bootstrap_origin": "subagent",
                "bootstrap_parent_thread_id": "parent-thread",
                "trusted_parent_session_id": "codex-parent",
                "subagent_signals": {"env_marker": "CODEX_SUBAGENT=1"},
            },
        )

        seen_ids: set[str] = set()
        state_path = self.tempdir / "watcher-state.json"

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run") as run:
            processed = watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-child",
                    "project": "mlv-app",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command_template": ["fake-wake", "-ThreadId", "{desktop_thread_id}"],
                },
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(processed, [])
        self.assertFalse(run.called)
        self.assertIn("msg-subagent-rollback", seen_ids)
        status = bridge.session_status("mlv-app")
        self.assertEqual(status.data["active"]["codex"], "codex-parent")
        peer_inbox = bridge.peek_inbox("claude", "claude-live")
        self.assertIn("ROUTE_REPAIR", peer_inbox.message)
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(any(row.get("action") == "bootstrap_subagent_auto_rollback_succeeded" for row in audit_rows))

    def test_07eb_unknown_peer_breadcrumb_warns_once_and_proceeds(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-unknown-peer",
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

        write_runtime_breadcrumb(
            peer_runtime_path_for_state_dir(self.state_dir, "codex"),
            {
                "schema_version": 1,
                "agent": "codex",
                "session_id": "codex-live",
                "project": "mlv-app",
                "desktop_thread_id": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5",
            },
        )

        seen_ids: set[str] = set()
        state_path = self.tempdir / "watcher-state.json"
        succeeded = Mock(returncode=0, stdout="", stderr="")

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run", return_value=succeeded) as run:
            watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "project": "mlv-app",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command_template": ["fake-wake", "-ThreadId", "{desktop_thread_id}"],
                },
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(run.call_count, 1)
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        warning_events = [row for row in audit_rows if row.get("action") == "unknown_origin_warning"]
        self.assertEqual(len(warning_events), 1)

    def test_07f_watcher_skips_wake_command_when_bridge_paused(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-paused",
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
        (self.state_dir / "state.json").write_text(
            json.dumps({"paused": True, "sessions": {}, "updated_at": "2026-04-29T00:00:00+00:00"}) + "\n",
            encoding="utf-8",
        )

        seen_ids: set[str] = set()
        state_path = self.tempdir / "watcher-state.json"

        with patch("watcher.notify_terminal") as notify, patch("watcher.subprocess.run") as run:
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
        self.assertFalse(run.called)
        self.assertEqual(notify.call_count, 1)
        self.assertNotIn("msg-paused", seen_ids)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["pending_wake_verifications"], [])
        self.assertEqual(state["paused_wake_messages"][0]["message_id"], "msg-paused")
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        paused_events = [row for row in audit_rows if row.get("action") == "wake_skipped_paused"]
        self.assertEqual(len(paused_events), 1)
        self.assertEqual(paused_events[0]["message_id"], "msg-paused")

    def test_07g_paused_wake_message_retries_after_resume(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-resume",
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
        state_file = self.state_dir / "state.json"
        state_file.write_text(
            json.dumps({"paused": True, "sessions": {}, "updated_at": "2026-04-29T00:00:00+00:00"}) + "\n",
            encoding="utf-8",
        )

        seen_ids: set[str] = set()
        state_path = self.tempdir / "watcher-state.json"

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run") as run:
            watcher.process_session_once(
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
            self.assertFalse(run.called)

            state_file.write_text(
                json.dumps({"paused": False, "sessions": {}, "updated_at": "2026-04-29T00:01:00+00:00"}) + "\n",
                encoding="utf-8",
            )
            run.return_value = Mock(returncode=0, stdout="", stderr="")
            watcher.process_session_once(
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

        self.assertEqual(run.call_count, 1)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["paused_wake_messages"], [])
        self.assertEqual(state["pending_wake_verifications"][0]["message_id"], "msg-resume")

    def test_07h_legacy_inline_command_is_coerced_to_argv_without_shell(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-legacy-inline",
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

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run", return_value=succeeded) as run:
            watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command": "fake-wake -ThreadId 019dcfe4-bd5d-7841-a7c1-2e8969a777c5",
                },
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(run.call_args.args[0], ["fake-wake", "-ThreadId", "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_07i_legacy_inline_command_with_shell_metacharacters_is_rejected(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        message = {
            "id": "msg-legacy-bad",
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

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run") as run:
            processed = watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command": "fake-wake ; injected",
                },
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(processed, [])
        self.assertFalse(run.called)
        self.assertIn("msg-legacy-bad", seen_ids)
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        config_events = [row for row in audit_rows if row.get("action") == "wake_skipped_config_error"]
        self.assertEqual(len(config_events), 1)
        self.assertEqual(config_events[0]["message_id"], "msg-legacy-bad")

    def test_07j_wake_breaker_opens_after_repeated_failures_and_suppresses_new_wake(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        state_path = self.tempdir / "watcher-state.json"
        seen_ids: set[str] = set()
        session_config = {
            "agent": "codex",
            "session_id": "codex-live",
            "inbox": str(inbox_path),
            "on_message": "notify",
            "on_message_command": "fake-wake",
        }

        with patch("watcher.notify_terminal"):
            for code in ("1", "3", "1", "1", "3"):
                watcher._record_wake_failure(
                    state_path=state_path,
                    agent="codex",
                    session_id="codex-live",
                    code=code,
                    inbox_path=inbox_path,
                )

        breaker_state = json.loads((self.state_dir / "wake-failure-windows.json").read_text(encoding="utf-8"))
        self.assertEqual(breaker_state["sessions"]["codex-live"]["breaker_state"], "open")

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run") as run:
            inbox_path.write_text(
                json.dumps(
                    {
                        "id": "msg-breaker-suppressed",
                        "session_id": "codex-live",
                        "from": "claude",
                        "to": "codex",
                        "body": "wake me",
                        "delivered_message": "wake me",
                        "seen_at": None,
                        "read_at": None,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            watcher.process_session_once(
                session_config,
                seen_ids=seen_ids,
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertFalse(run.called, "open breaker must suppress new wake attempts")
        self.assertIn("msg-breaker-suppressed", seen_ids)
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(any(row.get("action") == "wake_breaker_open" for row in audit_rows))
        self.assertTrue(any(row.get("action") == "wake_skipped_breaker_open" and row.get("message_id") == "msg-breaker-suppressed" for row in audit_rows))

    def test_07k_resume_wake_for_session_clears_breaker(self) -> None:
        bridge = AgentBridge(self.state_dir)
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        state_path = self.tempdir / "watcher-state.json"

        for code in ("1", "3", "1", "3", "1"):
            watcher._record_wake_failure(
                state_path=state_path,
                agent="codex",
                session_id="codex-live",
                code=code,
                inbox_path=inbox_path,
            )

        self.assertEqual(bridge.wake_breaker_status("codex-live").data["session"]["breaker_state"], "open")
        resumed = bridge.resume_wake_for_session("codex-live")
        self.assertTrue(resumed.ok)
        self.assertIsNone(bridge.wake_breaker_status("codex-live").data["session"])

    def test_07l_watcher_rate_limits_rapid_wake_fires(self) -> None:
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        inbox_path.write_text(
            json.dumps(
                {
                    "id": "msg-rate-limited",
                    "session_id": "codex-live",
                    "from": "claude",
                    "to": "codex",
                    "body": "wake me",
                    "delivered_message": "wake me",
                    "seen_at": None,
                    "read_at": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state_path = self.tempdir / "watcher-state.json"
        now = watcher.utc_now()
        state_path.write_text(
            json.dumps(
                {
                    "seen_ids": [],
                    "pending_wake_verifications": [],
                    "paused_wake_messages": [],
                    "unknown_origin_warnings": [],
                    "wake_fire_history": [
                        {"session_id": "codex-live", "at": now},
                        {"session_id": "codex-live", "at": now},
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with patch("watcher.notify_terminal"), patch("watcher.subprocess.run") as run:
            processed = watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "inbox": str(inbox_path),
                    "on_message": "notify",
                    "on_message_command": "fake-wake",
                },
                seen_ids=set(),
                state_path=state_path,
                toasts_enabled=True,
            )

        self.assertEqual(processed, [])
        self.assertFalse(run.called)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["pending_wake_verifications"][0]["message_id"], "msg-rate-limited")
        self.assertIsNotNone(state["pending_wake_verifications"][0]["deferred_until"])
        audit_rows = [
            json.loads(line)
            for line in (self.state_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(any(row.get("action") == "wake_rate_limited" and row.get("message_id") == "msg-rate-limited" for row in audit_rows))

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
            entry.get("on_message_command_template", "")
            for entry in config["sessions"]
            if entry.get("agent") == "codex"
        ]
        self.assertTrue(any("{desktop_thread_id}" in " ".join(command) for command in commands))
        self.assertFalse(any(subagent_thread in " ".join(command) for command in commands))

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
        bridge.activate_session("codex", "codex-live", project="mlv-app")

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
