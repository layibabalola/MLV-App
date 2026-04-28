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
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_bridge import AgentBridge


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
    @unittest.skip(
        "Phase 7 infrastructure not yet present: requires watcher to use "
        "subprocess.run with timeout + exit-code check + delivery markers. "
        "Will be enabled when Phase 7 lands."
    )
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

    # ------------------------------------------------------------------
    # Test 8 & 9: configure_watcher race + sub-agent cannot mutate parent_thread_id
    # Invariant: #10. Phase 7.X infrastructure.
    # ------------------------------------------------------------------
    @unittest.skip(
        "Phase 7.X infrastructure not yet present: requires parent_thread_id "
        "as a typed/protected config field with provenance allowlist. "
        "Will be enabled when Phase 7.X lands."
    )
    def test_08_concurrent_configure_watcher_preserves_parent_target(self) -> None:
        """Concurrent parent + sub-agent configure_watcher calls must
        preserve the parent's parent_thread_id setting. Sub-agent's
        ambient CODEX_THREAD_ID must not overwrite parent's."""

    @unittest.skip(
        "Phase 7.X infrastructure not yet present: parent_thread_id "
        "provenance allowlist must reject non-parent writers."
    )
    def test_09_sub_agent_cannot_mutate_parent_thread_id(self) -> None:
        """A configure_watcher call from a sub-agent context must be
        rejected when attempting to write parent_thread_id. Only a
        process proven to be the controlling parent may write it."""

    # ------------------------------------------------------------------
    # Test 10: Bootstrap watcher-config test does not leak watcher subprocess
    # Invariant: cleanup hygiene
    # ------------------------------------------------------------------
    @unittest.skip(
        "Requires bootstrap to expose a 'start_watcher=False' parameter or "
        "for ensure_watcher to return a tear-downable handle. Will be "
        "enabled when Phase 1+ lands."
    )
    def test_10_bootstrap_watcher_test_does_not_leak_subprocess(self) -> None:
        """The existing test_bootstrap_updates_watcher_config in
        test_agent_bridge.py emits a ResourceWarning because it spawns a
        real watcher subprocess that survives the test. Bootstrap must
        expose a no-watcher seam."""

    # ------------------------------------------------------------------
    # Test 11: wait_inbox rejects non-string session_ids at MCP boundary
    # Invariant: schema strictness
    # ------------------------------------------------------------------
    @unittest.skip(
        "Requires MCP boundary validation that doesn't yet exist as a "
        "testable seam. Phase 8 will tighten wait_inbox session_ids "
        "schema to list[str] explicitly."
    )
    def test_11_wait_inbox_rejects_non_string_session_ids(self) -> None:
        """wait_inbox(session_ids=[123, None, 'ok']) must reject at the
        MCP boundary, not silently coerce or fail mid-loop."""

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
    @unittest.skip(
        "Requires probe_server.py to be reworked with argparse + --mutate "
        "gate. Will be enabled when Phase 8 lands."
    )
    def test_13_probe_server_default_does_not_mutate_live_state(self) -> None:
        """probe_server.py must default to read-only / temp-state mode.
        Mutating live bridge state must require explicit --mutate."""


if __name__ == "__main__":
    unittest.main()
