import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_bridge import AgentBridge
from bootstrap_session import bootstrap
from project_identity import derive_project_identity, normalize_rendezvous
from routing_policy import evaluate_message


ROOT = Path(__file__).resolve().parents[2]


class AgentBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="agent-bridge-test-"))
        self.state_dir = self.tempdir / "state"

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_project_identity_uses_git_common_root(self) -> None:
        identity = derive_project_identity(str(ROOT))
        self.assertEqual(identity["rendezvous"], "mlv-app")
        self.assertTrue(identity["canonical_root"].endswith("MLV-App"))

    def test_unicode_normalization_falls_back_to_hash(self) -> None:
        value = normalize_rendezvous("Проект")
        self.assertTrue(value.startswith("project-"))
        self.assertEqual(len(value), len("project-") + 8)

    def test_superseded_session_cannot_send(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-old", project="mlv-app")
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        bridge.activate_session("claude", "claude-new", project="mlv-app")

        result = bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] stale send",
            session_id="claude-old",
        )
        self.assertFalse(result.ok)
        self.assertIn("superseded", result.message)

    def test_control_message_replaces_prior_control(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.send_control_message(
            "claude",
            "codex",
            "HANDSHAKE",
            "first",
            "body-one",
            session_id="mlv-app",
        )
        bridge.send_control_message(
            "claude",
            "codex",
            "HANDSHAKE",
            "second",
            "body-two",
            session_id="mlv-app",
            replace_existing_control=True,
        )
        inbox = bridge.peek_inbox("codex", session_id="mlv-app")
        self.assertEqual(inbox.status, "messages")
        self.assertIn("second", inbox.message)
        self.assertNotIn("first", inbox.message)

    def test_bootstrap_drains_previous_and_sends_handshake(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.send_to_peer(
            "codex",
            "claude",
            "[[handoff:claude]] hello from old window",
            session_id="claude-old",
        )
        result = bootstrap(
            state_dir=self.state_dir,
            agent="claude",
            cwd=str(ROOT),
            previous_session_id="claude-old",
            session_id="claude-new",
            project=None,
            handshake_retries=1,
        )
        self.assertEqual(result["project"], "mlv-app")
        self.assertEqual(len(result["drained_previous_messages"]), 1)
        self.assertTrue(result["activation"]["ok"])
        self.assertTrue(result["handshake"]["ok"])

    def test_end_session_marks_registry_and_notifies_peer(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        result = bridge.end_session("claude", "claude-live", project="mlv-app")
        self.assertTrue(result.ok)
        peer_inbox = bridge.peek_inbox("codex", "codex-live")
        self.assertIn("Session ending", peer_inbox.message)
        status = bridge.session_status("mlv-app")
        self.assertEqual(status.data["sessions"]["claude-live"]["status"], "ended")

    def test_routing_policy_prefers_suppression(self) -> None:
        rules_path = self.tempdir / "routing-rules.json"
        rules_path.write_text(
            json.dumps(
                {
                    "learned_triggers": [
                        {
                            "source": "codex",
                            "direction": "codex->claude",
                            "pattern": "bridge tooling",
                            "suggested_type": "AUDIT_REQUEST",
                        }
                    ],
                    "suppressed_triggers": [
                        {
                            "source": "codex",
                            "direction": "codex->claude",
                            "pattern": "bridge tooling",
                            "rule": "Do not send these",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = evaluate_message(
            source="codex",
            direction="codex->claude",
            text="This bridge tooling change is ready",
            rules_path=str(rules_path),
        )
        self.assertEqual(result["decision"], "suppress")

    def test_bridge_status_reports_rules_and_orphans(self) -> None:
        (self.tempdir / "routing-rules.json").write_text(
            json.dumps({"learned_triggers": [{"pattern": "x"}], "suppressed_triggers": []}),
            encoding="utf-8",
        )
        orphan_path = self.state_dir / "orphaned-claude.jsonl"
        orphan_path.parent.mkdir(parents=True, exist_ok=True)
        orphan_path.write_text(json.dumps({"orphaned_at": "2026-04-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
        bridge = AgentBridge(self.state_dir)
        status = bridge.bridge_status("mlv-app")
        self.assertEqual(status.data["routing_rules"]["learned"], 1)
        self.assertEqual(status.data["orphaned"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
