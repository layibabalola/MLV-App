import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_bridge import AgentBridge
from bootstrap_session import bootstrap
from compact import prune_audit_logs, reap_stale_server_pids
from configure_watcher import configure_watcher
from consume_inbox import consume
from core.addressing import AgentInbox, MessageKind, SenderContext, SessionInbox
from core.paths import BridgeRootMovedError, ensure_bridge_root_manifest, resolve_bridge_paths
from core.processes import acquire_singleton_lease, heartbeat_lease, release_lease
from core.runtime import peer_runtime_path_for_state_dir, read_runtime_breadcrumb
from core.routing import RoutingStatus, resolve_route
from core.settings import load_settings, settings_path_for_state_dir
from core.storage import append_jsonl, read_jsonl, with_schema_version, write_json
from migrate_root import migrate_root
from project_identity import derive_project_identity, normalize_rendezvous
from recover_bridge_session import inspect_bridge_runtime, recover_bridge_session
from recover_state import recover_state
from routing_policy import evaluate_message
import watcher


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

    def test_bridge_paths_resolve_explicit_root_env_and_state_dir(self) -> None:
        explicit_root = self.tempdir / "explicit-root"
        env_root = self.tempdir / "env-root"
        legacy_state = self.tempdir / "legacy-root" / "state"

        explicit = resolve_bridge_paths(
            bridge_root=explicit_root,
            env={"AGENT_BRIDGE_ROOT": str(env_root), "USERPROFILE": str(self.tempdir)},
        )
        self.assertEqual(explicit.root, explicit_root)
        self.assertEqual(explicit.state_dir, explicit_root / "state")
        self.assertEqual(explicit.watcher_config, explicit_root / "watcher-config.json")

        from_env = resolve_bridge_paths(env={"AGENT_BRIDGE_ROOT": str(env_root), "USERPROFILE": str(self.tempdir)})
        self.assertEqual(from_env.root, env_root)

        legacy = resolve_bridge_paths(state_dir=legacy_state, env={"USERPROFILE": str(self.tempdir)})
        self.assertEqual(legacy.root, self.tempdir / "legacy-root")

    def test_bridge_paths_reject_moved_root(self) -> None:
        source = self.tempdir / "old-root"
        target = self.tempdir / "new-root"
        source.mkdir()
        (source / "MOVED_TO.json").write_text(
            json.dumps({"active_root": str(target), "migration_history": [{"source": str(source), "target": str(target)}]}),
            encoding="utf-8",
        )

        with self.assertRaises(BridgeRootMovedError) as raised:
            resolve_bridge_paths(bridge_root=source)
        self.assertEqual(raised.exception.target, target)

    def test_bridge_paths_follow_moved_root_chain_and_reject_cycles(self) -> None:
        root_a = self.tempdir / "root-a"
        root_b = self.tempdir / "root-b"
        root_c = self.tempdir / "root-c"
        for root in (root_a, root_b, root_c):
            root.mkdir()
        (root_a / "MOVED_TO.json").write_text(json.dumps({"active_root": str(root_b)}), encoding="utf-8")
        (root_b / "MOVED_TO.json").write_text(json.dumps({"active_root": str(root_c)}), encoding="utf-8")

        with self.assertRaises(BridgeRootMovedError) as raised:
            resolve_bridge_paths(bridge_root=root_a)
        self.assertEqual(raised.exception.target, root_c)
        self.assertEqual(raised.exception.chain, [root_a, root_b, root_c])

        (root_c / "MOVED_TO.json").write_text(json.dumps({"active_root": str(root_a)}), encoding="utf-8")
        with self.assertRaises(ValueError):
            resolve_bridge_paths(bridge_root=root_a)

    def test_bridge_root_manifest_is_created_once(self) -> None:
        paths = resolve_bridge_paths(bridge_root=self.tempdir / "manifest-root")
        first = ensure_bridge_root_manifest(paths, reason="unit-test")
        second = ensure_bridge_root_manifest(paths, reason="ignored")
        self.assertEqual(first["root_id"], second["root_id"])
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["active_root"], str(paths.root))
        self.assertEqual(first["migration_history"][0]["reason"], "unit-test")

    def test_bridge_root_manifest_rejects_mismatched_active_root(self) -> None:
        paths = resolve_bridge_paths(bridge_root=self.tempdir / "manifest-root")
        paths.root.mkdir()
        (paths.manifest).write_text(
            json.dumps({"schema_version": 1, "root_id": "root-id", "active_root": str(self.tempdir / "other-root")}),
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            ensure_bridge_root_manifest(paths, reason="unit-test")

    def test_bootstrap_cli_accepts_bridge_root_and_writes_manifest(self) -> None:
        bridge_root = self.tempdir / "cli-root"
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "bootstrap_session.py"),
                "--bridge-root",
                str(bridge_root),
                "--agent",
                "codex",
                "--cwd",
                str(ROOT),
                "--session-id",
                "codex-cli",
                "--handshake-retries",
                "1",
                "--no-start-watcher",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        self.assertEqual(data["session_id"], "codex-cli")
        self.assertTrue((bridge_root / "bridge-root.json").exists())
        self.assertTrue((bridge_root / "watcher-config.json").exists())

    def test_server_wrapper_print_command_resolves_bridge_root(self) -> None:
        bridge_root = self.tempdir / "wrapper-root"
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "server_wrapper.py"),
                "--bridge-root",
                str(bridge_root),
                "--print-command",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("server.py", result.stdout)
        self.assertIn(str(bridge_root / "state"), result.stdout)
        self.assertTrue((bridge_root / "bridge-root.json").exists())

    def test_server_wrapper_rejects_moved_root_before_mcp_startup(self) -> None:
        old_root = self.tempdir / "old-root"
        new_root = self.tempdir / "new-root"
        old_root.mkdir()
        (old_root / "MOVED_TO.json").write_text(json.dumps({"active_root": str(new_root)}), encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "server_wrapper.py"),
                "--bridge-root",
                str(old_root),
                "--print-command",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("agent-bridge root moved", result.stderr)

    def test_server_wrapper_launches_server_with_space_in_bridge_root(self) -> None:
        # Regression for the os.execv-on-Windows argv-quoting bug fixed at
        # server_wrapper.py:84. Pre-fix, os.execv joined argv elements with
        # bare spaces, so a server.py path containing a space (e.g. under
        # "C:\!Layi Wkspc\...") was re-tokenized in the child and Python
        # exited with "can't find '__main__' module in 'C:\\!Layi'".
        # Forces the failure surface by deliberately putting a space in the
        # bridge-root path; asserts the wrapper reached past the audit
        # breadcrumb (proving exec/spawn was attempted) and the child did
        # not see a mangled argv. See memory/windows_execv_quoting.md.
        bridge_root = self.tempdir / "path with space" / "root"
        bridge_root.parent.mkdir(parents=True, exist_ok=True)
        wrapper = Path(__file__).resolve().parent / "server_wrapper.py"

        try:
            proc = subprocess.run(
                [sys.executable, str(wrapper), "--bridge-root", str(bridge_root)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
            )
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            stderr_raw = exc.stderr
            if isinstance(stderr_raw, bytes):
                stderr = stderr_raw.decode("utf-8", errors="replace")
            else:
                stderr = stderr_raw or ""

        self.assertNotIn("can't find '__main__' module", stderr)
        audit_log = bridge_root / "state" / "messages.jsonl"
        self.assertTrue(audit_log.exists(), f"wrapper did not reach audit; stderr={stderr!r}")
        self.assertIn("mcp_server_wrapper_launch", audit_log.read_text(encoding="utf-8"))

    def test_migrate_root_dry_run_does_not_create_target(self) -> None:
        source = self.tempdir / "source-root"
        target = self.tempdir / "target-root"
        (source / "state").mkdir(parents=True)
        (source / "session.json").write_text("{}", encoding="utf-8")

        result = migrate_root(source_root=source, target_root=target)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(target.exists())
        self.assertIn("server_wrapper.py", " ".join(result["plan"]["mcp_config_snippets"]["codex_toml"]["mcp_servers.agent_bridge"]["args"]))

    def test_migrate_root_refuses_live_markers_without_force(self) -> None:
        source = self.tempdir / "source-root"
        target = self.tempdir / "target-root"
        (source / "state").mkdir(parents=True)
        (source / "watcher.pid").write_text(str(os.getpid()), encoding="utf-8")

        result = migrate_root(source_root=source, target_root=target)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "live_processes")
        self.assertEqual(result["live_processes"][0]["kind"], "watcher")

    def test_migrate_root_apply_rewrites_watcher_and_writes_redirect(self) -> None:
        source = self.tempdir / "source-root"
        target = self.tempdir / "target-root"
        source_state = source / "state"
        source_state.mkdir(parents=True)
        (source / "session.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
        (source / "watcher-config.json").write_text(
            json.dumps(
                {
                    "sessions": [
                        {
                            "agent": "codex",
                            "kind": "private",
                            "session_id": "codex-live",
                            "inbox": str(source_state / "inbox-codex.jsonl"),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = migrate_root(source_root=source, target_root=target, apply=True, reason="unit-test")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "migrated")
        moved = json.loads((source / "MOVED_TO.json").read_text(encoding="utf-8"))
        self.assertEqual(moved["active_root"], str(target))
        config = json.loads((target / "watcher-config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["sessions"][0]["inbox"], str(target / "state" / "inbox-codex.jsonl"))
        manifest = json.loads((target / "bridge-root.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["active_root"], str(target))
        self.assertEqual(manifest["migration_history"][-1]["reason"], "unit-test")
        self.assertTrue(result["validation"]["ok"])
        self.assertIn("claude_desktop_config", result["mcp_config_snippets"])
        with self.assertRaises(BridgeRootMovedError) as raised:
            resolve_bridge_paths(bridge_root=source)
        self.assertEqual(raised.exception.target, target)

    def test_migrate_root_refuses_existing_migration_lease(self) -> None:
        source = self.tempdir / "source-root"
        target = self.tempdir / "target-root"
        (source / "state" / "locks").mkdir(parents=True)
        command = ["migrate_root.py", str(source), str(target)]
        lease_path = source / "state" / "locks" / "migration.lock"
        acquired = acquire_singleton_lease(
            lease_path,
            role="migration",
            command=command,
            state_dir=source / "state",
            pid=os.getpid(),
        )
        self.assertTrue(acquired["acquired"])
        try:
            result = migrate_root(source_root=source, target_root=target, apply=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "migration_in_progress")
        finally:
            lease = acquired["lease"]
            release_lease(lease_path, int(lease["pid"]), str(lease["generation"]))

    def test_migrate_root_skip_redirect_leaves_no_moved_to(self) -> None:
        source = self.tempdir / "source-root"
        target = self.tempdir / "target-root"
        (source / "state").mkdir(parents=True)
        (source / "session.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")

        result = migrate_root(source_root=source, target_root=target, apply=True, skip_redirect=True)

        self.assertTrue(result["ok"])
        self.assertIsNone(result["moved_to"])
        self.assertFalse((source / "MOVED_TO.json").exists())

    def test_unicode_normalization_falls_back_to_hash(self) -> None:
        value = normalize_rendezvous("Проект")
        self.assertTrue(value.startswith("project-"))
        self.assertEqual(len(value), len("project-") + 8)

    def test_core_storage_quarantines_bad_jsonl_and_versions_state(self) -> None:
        state_path = self.tempdir / "state.json"
        write_json(state_path, with_schema_version({"paused": False}))
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema_version"], 1)

        inbox = self.tempdir / "inbox.jsonl"
        append_jsonl(inbox, {"id": "ok"})
        with inbox.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("{bad json\n")
        rows = read_jsonl(inbox)
        self.assertEqual(rows, [{"id": "ok"}])
        self.assertTrue(inbox.with_suffix(".quarantine.jsonl").exists())

    def test_core_routing_rejects_inactive_work_and_escalates_dead_session(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        bridge.activate_session("claude", "claude-old", project="mlv-app")
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        registry = bridge._load_session_registry()

        inactive_sender = SenderContext("claude", "claude-old", "mlv-app")
        rejected = resolve_route(
            sender=inactive_sender,
            target=SessionInbox("mlv-app", "codex", "codex-live"),
            kind=MessageKind.WORK,
            registry=registry,
        )
        self.assertEqual(rejected.status, RoutingStatus.REJECTED)

        active_sender = SenderContext("codex", "codex-live", "mlv-app")
        escalated = resolve_route(
            sender=active_sender,
            target=SessionInbox("mlv-app", "claude", "claude-old"),
            kind=MessageKind.WORK,
            registry=registry,
        )
        self.assertEqual(escalated.status, RoutingStatus.ESCALATED)
        self.assertEqual(escalated.bucket, "mlv-app")

        control = resolve_route(
            sender=active_sender,
            target=AgentInbox("claude"),
            kind=MessageKind.CONTROL,
            registry=registry,
        )
        self.assertTrue(control.ok)
        self.assertEqual(control.inbox_level, "agent")

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

    def test_superseded_target_session_escalates_to_project_bucket(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-old", project="mlv-app")
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        bridge.activate_session("claude", "claude-new", project="mlv-app")

        result = bridge.send_to_peer(
            "codex",
            "claude",
            "[[handoff:claude]] hello to stale target",
            session_id="claude-old",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["resolved_session_id"], "mlv-app")
        inbox = bridge.peek_inbox("claude", session_id="mlv-app")
        self.assertEqual(inbox.status, "messages")
        self.assertEqual(inbox.data["messages"][0]["inbox_level"], "project")
        self.assertEqual(inbox.data["messages"][0]["escalated_from"], "claude-old")

    def test_active_sender_can_send_to_active_target_even_with_superseded_history(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-old-1", project="mlv-app")
        bridge.activate_session("claude", "claude-old-2", project="mlv-app")
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        bridge.activate_session("claude", "claude-live", project="mlv-app")

        result = bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] active sender to active target still works",
            session_id="codex-live",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["resolved_session_id"], "codex-live")
        inbox = bridge.peek_inbox("codex", session_id="codex-live")
        self.assertEqual(inbox.status, "messages")
        self.assertIn("active sender to active target still works", inbox.message)

    def test_activate_session_promotes_unread_messages_to_project(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.activate_session("codex", "codex-old", project="mlv-app")
        bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] old session message",
            session_id="codex-old",
        )

        bridge.activate_session("codex", "codex-new", project="mlv-app")
        project_inbox = bridge.peek_inbox("codex", session_id="mlv-app")
        self.assertEqual(project_inbox.status, "messages")
        row = project_inbox.data["messages"][0]
        self.assertEqual(row["session_id"], "mlv-app")
        self.assertEqual(row["inbox_level"], "project")
        self.assertEqual(row["promoted_from"], "codex-old")

    def test_check_inbox_include_parents_reads_project_bucket(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.activate_session("codex", "codex-old", project="mlv-app")
        bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] old session message",
            session_id="codex-old",
        )
        bridge.activate_session("codex", "codex-new", project="mlv-app")

        result = bridge.check_inbox("codex", session_id="codex-new", include_parents=True, mark_read=False)
        self.assertEqual(result.status, "messages")
        self.assertEqual(result.data["messages"][0]["session_id"], "mlv-app")
        self.assertEqual(result.data["buckets"], ["codex-new", "mlv-app"])

    def test_default_bucket_is_rejected(self) -> None:
        bridge = AgentBridge(self.state_dir)
        result = bridge.send_to_peer(
            "codex",
            "claude",
            "[[handoff:claude]] should fail",
            session_id="default",
        )
        self.assertFalse(result.ok)
        self.assertIn("deprecated", result.message)

    def test_unknown_explicit_session_bucket_is_preserved(self) -> None:
        bridge = AgentBridge(self.state_dir)
        result = bridge.send_to_peer(
            "codex",
            "claude",
            "[[handoff:claude]] hello to unknown bucket",
            session_id="future-session",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["resolved_session_id"], "future-session")
        self.assertEqual(result.data["inbox_level"], "session")
        inbox = bridge.peek_inbox("claude", session_id="future-session")
        self.assertEqual(inbox.status, "messages")
        self.assertEqual(inbox.data["messages"][0]["session_id"], "future-session")

    def test_mark_read_can_target_message_without_session_id(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        bridge.activate_session("claude", "claude-old", project="mlv-app")
        bridge.send_to_peer(
            "codex",
            "claude",
            "[[handoff:claude]] old session message",
            session_id="claude-old",
        )
        bridge.activate_session("claude", "claude-new", project="mlv-app")
        inbox = bridge.peek_inbox("claude", session_id="mlv-app")
        self.assertEqual(inbox.status, "messages")
        message_id = inbox.data["messages"][0]["id"]

        result = bridge.mark_read("claude", session_id=None, message_id=message_id)
        self.assertTrue(result.ok)
        after = bridge.peek_inbox("claude", session_id="mlv-app")
        self.assertEqual(after.status, "empty")

    def test_message_receipts_track_seen_read_and_handled(self) -> None:
        bridge = AgentBridge(self.state_dir)
        result = bridge.send_to_peer("codex", "claude", "[[handoff:claude]] receipt hello", session_id="mlv-app")
        self.assertTrue(result.ok)
        message_id = result.data["id"]

        self.assertEqual(bridge.message_status(message_id).status, "queued")
        seen = bridge.mark_seen("claude", message_id, via="unit-test")
        self.assertTrue(seen.ok)
        self.assertEqual(bridge.message_status(message_id).status, "seen")
        read = bridge.mark_read("claude", message_id)
        self.assertTrue(read.ok)
        self.assertEqual(bridge.message_status(message_id).status, "read")
        handled = bridge.mark_handled("claude", message_id, status="handled")
        self.assertTrue(handled.ok)
        self.assertEqual(bridge.message_status(message_id).status, "handled")
        self.assertEqual(bridge.list_pending_receipts("claude").data["count"], 0)

    def test_list_pending_receipts_is_bounded_and_paginated(self) -> None:
        bridge = AgentBridge(self.state_dir)
        long_body = "x" * 1000
        message_ids = []
        for index in range(3):
            result = bridge.send_to_peer(
                "codex",
                "claude",
                "[[handoff:claude]] pending %d %s" % (index, long_body),
                session_id="receipt-%d" % index,
            )
            self.assertTrue(result.ok)
            message_ids.append(result.data["id"])

        page = bridge.list_pending_receipts("claude", limit=2, offset=0, body_preview_chars=10)
        self.assertTrue(page.ok)
        self.assertEqual(page.data["total_count"], 3)
        self.assertEqual(page.data["count"], 2)
        self.assertTrue(page.data["has_more"])
        first = page.data["messages"][0]
        self.assertLessEqual(len(first["body_preview"]), 10)
        self.assertTrue(first["body_truncated"])
        self.assertLessEqual(len(first["delivered_preview"]), 10)
        self.assertTrue(first["delivered_truncated"])

        second_page = bridge.list_pending_receipts("claude", limit=2, offset=2, body_preview_chars=10)
        self.assertEqual(second_page.data["count"], 1)
        self.assertFalse(second_page.data["has_more"])
        self.assertEqual(bridge.message_status(message_ids[0]).data["message"]["body"], "pending 0 " + long_body)

        rejected = bridge.list_pending_receipts("claude", limit=0)
        self.assertFalse(rejected.ok)
        self.assertIn("limit", rejected.message)

    def test_check_inbox_records_seen_but_peek_stays_pure(self) -> None:
        bridge = AgentBridge(self.state_dir)
        result = bridge.send_to_peer("codex", "claude", "[[handoff:claude]] visible hello", session_id="mlv-app")
        self.assertTrue(result.ok)
        message_id = result.data["id"]

        peeked = bridge.peek_inbox("claude", "mlv-app")
        self.assertEqual(peeked.status, "messages")
        self.assertEqual(bridge.message_status(message_id).status, "queued")

        checked = bridge.check_inbox("claude", "mlv-app", mark_read=False)
        self.assertEqual(checked.status, "messages")
        status = bridge.message_status(message_id)
        self.assertEqual(status.status, "seen")
        self.assertEqual(status.data["message"]["seen_via"], "check_inbox")

    def test_bridge_process_status_reports_without_mutation(self) -> None:
        bridge = AgentBridge(self.state_dir)
        status = bridge.bridge_process_status()
        self.assertTrue(status.ok)
        self.assertIn("watcher", status.data)
        self.assertIn("mcp_server_marker_count", status.data)

    def test_bridge_process_status_includes_runtime_breadcrumbs(self) -> None:
        self.state_dir.mkdir(parents=True)
        bridge_root = self.state_dir.parent
        (bridge_root / "watcher.pid").write_text(str(os.getpid()), encoding="utf-8")
        (bridge_root / "watcher.runtime.json").write_text(
            json.dumps({"schema_version": 1, "role": "watcher", "pid": os.getpid(), "bridge_root": str(bridge_root)}),
            encoding="utf-8",
        )
        server_dir = self.state_dir / "server-pids"
        server_dir.mkdir(parents=True)
        (server_dir / ("server-%s.pid" % os.getpid())).write_text(str(os.getpid()), encoding="utf-8")
        (server_dir / ("server-%s.json" % os.getpid())).write_text(
            json.dumps({"schema_version": 1, "role": "mcp_server", "pid": os.getpid(), "bridge_root": str(bridge_root)}),
            encoding="utf-8",
        )

        status = AgentBridge(self.state_dir).bridge_process_status()

        self.assertEqual(status.data["watcher"]["runtime"]["role"], "watcher")
        self.assertEqual(status.data["mcp_server_markers"][0]["runtime"]["role"], "mcp_server")

    def test_bridge_process_status_flags_runtime_root_mismatch(self) -> None:
        self.state_dir.mkdir(parents=True)
        bridge_root = self.state_dir.parent
        other_root = self.tempdir / "other-root"
        (bridge_root / "watcher.pid").write_text(str(os.getpid()), encoding="utf-8")
        (bridge_root / "watcher.runtime.json").write_text(
            json.dumps({"schema_version": 1, "role": "watcher", "pid": os.getpid(), "bridge_root": str(other_root)}),
            encoding="utf-8",
        )

        status = AgentBridge(self.state_dir).bridge_process_status()

        self.assertEqual(status.status, "attention")
        self.assertTrue(status.data["watcher"]["root_mismatch"])

    def test_process_lease_heartbeat_and_release(self) -> None:
        lock_path = self.state_dir / "locks" / "watcher.lock"
        acquired = acquire_singleton_lease(
            lock_path,
            role="watcher",
            command=[sys.executable, "watcher.py"],
            state_dir=self.state_dir,
        )
        self.assertTrue(acquired["acquired"])
        lease = acquired["lease"]
        self.assertTrue(heartbeat_lease(lock_path, lease["pid"], lease["generation"]))

        bridge = AgentBridge(self.state_dir)
        status = bridge.bridge_process_status()
        self.assertEqual(status.data["watcher"]["lease"]["generation"], lease["generation"])

        self.assertTrue(release_lease(lock_path, lease["pid"], lease["generation"]))
        self.assertFalse(lock_path.exists())

    def test_settings_defaults_and_validation(self) -> None:
        settings = load_settings(self.state_dir)
        self.assertEqual(settings.toast_expiry_minutes, 5)
        self.assertTrue(settings.toasts_enabled)

        settings_path = settings_path_for_state_dir(self.state_dir)
        settings_path.write_text(
            json.dumps(
                {
                    "toast_expiry_minutes": 7,
                    "toasts_enabled": False,
                    "routing_rules_enabled": False,
                }
            ),
            encoding="utf-8",
        )
        loaded = load_settings(self.state_dir)
        self.assertEqual(loaded.toast_expiry_minutes, 7)
        self.assertFalse(loaded.toasts_enabled)
        self.assertFalse(loaded.codex_bridge_reminder_toasts_enabled)
        self.assertFalse(loaded.routing_rules_enabled)

        settings_path.write_text(json.dumps({"unsupported_knob": True}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_settings(self.state_dir)

        settings_path.write_text(json.dumps({"toasts_enabled": "yes"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_settings(self.state_dir)

    def test_codex_bridge_reminder_toasts_are_opt_in(self) -> None:
        settings = load_settings(self.state_dir)
        self.assertFalse(settings.codex_bridge_reminder_toasts_enabled)

        settings_path_for_state_dir(self.state_dir).write_text(
            json.dumps({"codex_bridge_reminder_toasts_enabled": True}),
            encoding="utf-8",
        )
        self.assertTrue(load_settings(self.state_dir).codex_bridge_reminder_toasts_enabled)

    def test_codex_startup_hooks_avoid_personal_paths(self) -> None:
        paths = [
            ROOT / "AGENTS.md",
            Path(__file__).resolve().parent / "codex_bridge_reminder.ps1",
            Path(__file__).resolve().parent / "codex_bridge_watch_mode.ps1",
            Path(__file__).resolve().parent / "codex_pre_response.ps1",
            Path(__file__).resolve().parent / "codex_pre_final.ps1",
        ]
        forbidden = ["C:\\Users\\obabalola", "C:\\!Layi Wkspc"]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, text, "%s contains personal path %s" % (path.name, value))

    def test_evaluate_routing_respects_settings_gate(self) -> None:
        settings_path_for_state_dir(self.state_dir).write_text(
            json.dumps({"routing_rules_enabled": False}),
            encoding="utf-8",
        )
        bridge = AgentBridge(self.state_dir)
        result = bridge.evaluate_routing("codex", "codex->claude", "bridge tooling")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "disabled")

    def test_recover_state_dry_run_and_repair(self) -> None:
        self.state_dir.mkdir(parents=True)
        state_path = self.state_dir / "state.json"
        inbox_path = self.state_dir / "inbox-codex.jsonl"
        state_path.write_text("{bad json", encoding="utf-8")
        inbox_path.write_text('{"id":"ok"}\nnot-json\n', encoding="utf-8")

        dry_run = recover_state(self.state_dir, repair=False)
        self.assertFalse(dry_run["ok"])
        self.assertFalse(dry_run["json"]["state"]["ok"])
        self.assertEqual(dry_run["jsonl"]["inbox_codex"]["invalid_rows"], 1)
        self.assertEqual(state_path.read_text(encoding="utf-8"), "{bad json")

        repaired = recover_state(self.state_dir, repair=True)
        self.assertFalse(repaired["ok"])
        self.assertTrue(repaired["backups"])
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["sessions"], {})
        self.assertEqual(read_jsonl(inbox_path), [{"id": "ok"}])
        self.assertTrue(inbox_path.with_suffix(".quarantine.jsonl").exists())

    def test_recover_state_scan_historical_reports_stale_root(self) -> None:
        old_root = self.tempdir / "old-root"
        new_root = self.tempdir / "new-root"
        (old_root / "state").mkdir(parents=True)
        (new_root / "state").mkdir(parents=True)
        ensure_bridge_root_manifest(resolve_bridge_paths(bridge_root=new_root), reason="unit-test")
        (old_root / "MOVED_TO.json").write_text(json.dumps({"active_root": str(new_root)}), encoding="utf-8")

        report = recover_state(old_root / "state", scan_historical=True)

        self.assertFalse(report["ok"])
        self.assertEqual(report["historical"]["redirect"]["target"], str(new_root))
        self.assertEqual(report["historical"]["issues"][0]["code"], "root_is_stale")

    def test_recover_state_scan_historical_reports_missing_source_redirect(self) -> None:
        old_root = self.tempdir / "old-root"
        new_root = self.tempdir / "new-root"
        old_root.mkdir()
        (new_root / "state").mkdir(parents=True)
        paths = resolve_bridge_paths(bridge_root=new_root)
        manifest = ensure_bridge_root_manifest(paths, reason="unit-test")
        manifest["migration_history"].append(
            {"source": str(old_root), "target": str(new_root), "tool": "unit-test", "reason": "partial"}
        )
        write_json(paths.manifest, manifest)

        report = recover_state(new_root / "state", scan_historical=True)

        self.assertFalse(report["ok"])
        codes = [issue["code"] for issue in report["historical"]["issues"]]
        self.assertIn("source_missing_moved_to", codes)

    def test_compact_reaps_only_stale_server_pid_markers(self) -> None:
        server_dir = self.state_dir / "server-pids"
        server_dir.mkdir(parents=True)
        stale = server_dir / "server-999999.pid"
        fresh = server_dir / "server-999998.pid"
        stale.write_text("999999\n", encoding="utf-8")
        fresh.write_text("999998\n", encoding="utf-8")
        os.utime(stale, (0, 0))

        result = reap_stale_server_pids(self.state_dir, max_age_hours=24)
        self.assertEqual(result["removed"], 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_compact_default_retains_fresh_dead_server_pid_markers(self) -> None:
        server_dir = self.state_dir / "server-pids"
        server_dir.mkdir(parents=True)
        fresh_dead = server_dir / "server-0.pid"
        fresh_dead.write_text("0\n", encoding="utf-8")

        default_result = reap_stale_server_pids(self.state_dir)
        self.assertEqual(default_result["removed"], 0)
        self.assertTrue(fresh_dead.exists())
        self.assertEqual(default_result["kept_fresh"], 1)

        immediate_result = reap_stale_server_pids(self.state_dir, max_age_hours=0)
        self.assertEqual(immediate_result["removed"], 1)
        self.assertFalse(fresh_dead.exists())

    def test_compact_prunes_old_rotated_audit_logs(self) -> None:
        self.state_dir.mkdir(parents=True)
        old_log = self.state_dir / "messages.2025-01.jsonl"
        fresh_log = self.state_dir / "messages.2026-04.jsonl"
        quarantine = self.state_dir / "messages.quarantine.jsonl"
        old_log.write_text("{}\n", encoding="utf-8")
        fresh_log.write_text("{}\n", encoding="utf-8")
        quarantine.write_text("not-json\n", encoding="utf-8")
        os.utime(old_log, (0, 0))
        os.utime(quarantine, (0, 0))

        result = prune_audit_logs(self.state_dir, retention_days=1)
        self.assertEqual(result["removed"], 1)
        self.assertFalse(old_log.exists())
        self.assertTrue(fresh_log.exists())
        self.assertTrue(quarantine.exists())

    def test_windows_toast_uses_expiry_and_tray_cap_settings(self) -> None:
        with patch("watcher.subprocess.Popen") as popen:
            watcher.notify_windows_toast(
                "codex",
                "mlv-app",
                [{"id": "msg-1", "body": "TYPE: SMOKE\nSUMMARY: toast expiry"}],
                toast_expiry_minutes=7,
                toast_max_in_tray=3,
            )
        args = popen.call_args.args[0]
        encoded = args[args.index("-EncodedCommand") + 1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn("$toast.ExpirationTime = [DateTimeOffset]::Now.AddMinutes(7)", script)
        self.assertIn("$toastMaxInTray = 3", script)
        self.assertIn("$toast.Group = 'agent-bridge-codex'", script)

    def test_windows_toast_tag_sanitizes_ps_line_breaks(self) -> None:
        with patch("watcher.subprocess.Popen") as popen:
            watcher.notify_windows_toast(
                "codex",
                "mlv-app",
                [{"id": "bad'\r\nWrite-Host pwned", "body": "TYPE: SMOKE\nSUMMARY: toast tag"}],
            )
        args = popen.call_args.args[0]
        encoded = args[args.index("-EncodedCommand") + 1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        tag_line = next(line for line in script.splitlines() if line.startswith("$toast.Tag = "))
        self.assertEqual(tag_line, "$toast.Tag = 'badWrite-Hostpwned'")

    def test_clear_bucket_and_reset_bucket_are_explicit_aliases(self) -> None:
        bridge = AgentBridge(self.state_dir)
        result = bridge.send_to_peer("codex", "claude", "[[handoff:claude]] bucket hello", session_id="mlv-app")
        self.assertTrue(result.ok)

        cleared = bridge.clear_bucket("mlv-app", agent="claude")
        self.assertTrue(cleared.ok)
        self.assertEqual(bridge.peek_inbox("claude", "mlv-app").status, "empty")
        self.assertFalse(bridge.clear_bucket("default").ok)

        reset = bridge.reset_bucket("mlv-app")
        self.assertTrue(reset.ok)
        self.assertFalse(bridge.reset_bucket("default").ok)

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
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        bridge.activate_session("claude", "claude-old", project="mlv-app")
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
        self.assertEqual(bridge.peek_inbox("claude", session_id="mlv-app").status, "empty")

    def test_bootstrap_updates_watcher_config(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        with patch.dict("os.environ", {"CODEX_THREAD_ID": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"}):
            result = bootstrap(
                state_dir=self.state_dir,
                agent="codex",
                cwd=str(ROOT),
                previous_session_id=None,
                session_id="codex-new",
                project=None,
                handshake_retries=1,
                watcher_config=config_path,
                start_watcher=False,
            )
        self.assertIsNotNone(result["watcher"])
        self.assertEqual(result["watcher_process"]["status"], "not_started")
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        session_ids = {entry["session_id"] for entry in config["sessions"]}
        self.assertIn("codex-new", session_ids)
        self.assertIn("mlv-app", session_ids)
        codex_entries = [entry for entry in config["sessions"] if entry.get("agent") == "codex"]
        self.assertTrue(
            any(
                "{desktop_thread_id}" in " ".join(entry.get("on_message_command_template", []))
                for entry in codex_entries
            )
        )
        breadcrumb = read_runtime_breadcrumb(peer_runtime_path_for_state_dir(self.state_dir, "codex"))
        self.assertEqual(breadcrumb["session_id"], "codex-new")
        self.assertEqual(breadcrumb["desktop_thread_id"], "019dcfe4-bd5d-7841-a7c1-2e8969a777c5")

    def test_recover_bridge_session_bootstraps_when_unbootstrapped(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        result = recover_bridge_session(
            state_dir=self.state_dir,
            agent="codex",
            cwd=str(ROOT),
            watcher_config=config_path,
            start_watcher=False,
        )
        self.assertEqual(result["status"], "bootstrapped")
        self.assertEqual(result["before"]["bridge_state"], "UNBOOTSTRAPPED")
        self.assertEqual(result["after"]["bridge_state"], "BOOTSTRAPPED_NOT_WATCHING")
        self.assertIsNotNone(result["bootstrap"])
        self.assertTrue(config_path.exists())

    def test_recover_bridge_session_reports_healthy_existing_runtime(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        bootstrap(
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
        lock_path = self.state_dir / "locks" / "watcher.lock"
        acquired = acquire_singleton_lease(
            lock_path,
            role="watcher",
            command=[sys.executable, "watcher.py"],
            state_dir=self.state_dir,
            pid=os.getpid(),
        )
        self.assertTrue(acquired["acquired"])
        (self.tempdir / "watcher.pid").write_text(str(os.getpid()), encoding="utf-8")

        result = recover_bridge_session(
            state_dir=self.state_dir,
            agent="codex",
            cwd=str(ROOT),
            watcher_config=config_path,
            start_watcher=False,
        )
        self.assertEqual(result["status"], "already_healthy")
        self.assertEqual(result["after"]["bridge_state"], "WATCHING")

    def test_inspect_bridge_runtime_reports_bootstrapped_not_watching(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        bootstrap(
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
        summary = inspect_bridge_runtime(
            state_dir=self.state_dir,
            agent="codex",
            cwd=str(ROOT),
            watcher_config=config_path,
        )
        self.assertEqual(summary["bridge_state"], "BOOTSTRAPPED_NOT_WATCHING")
        self.assertEqual(summary["active_session_id"], "codex-live")

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

    def test_consume_detects_supersede_halt(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.send_control_message(
            "codex",
            "claude",
            "SESSION_UPDATE",
            "Session superseded by newer claude session",
            "A newer claude session is now active. Stop bridge communication.",
            session_id="claude-old",
        )
        result = consume(self.state_dir, "claude", "claude-old", mark_read=True)
        self.assertTrue(result["should_halt"])
        self.assertEqual(result["halt_reason"], "superseded")
        self.assertEqual(result["control_events"][0]["type"], "SESSION_UPDATE")

    def test_consume_detects_ending_and_peek_leaves_unread(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.send_control_message(
            "codex",
            "claude",
            "SESSION_UPDATE",
            "Session ending for project mlv-app",
            "The claude session has ended. Stop sending bridge traffic there.",
            session_id="claude-live",
        )
        peek = consume(self.state_dir, "claude", "claude-live", mark_read=False)
        self.assertTrue(peek["should_halt"])
        self.assertEqual(peek["halt_reason"], "ended")
        inbox = bridge.peek_inbox("claude", "claude-live")
        self.assertEqual(inbox.status, "messages")

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

    def test_configure_watcher_replaces_same_agent_entries(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        session_registry = self.tempdir / "session.json"
        session_registry.write_text(
            json.dumps(
                {
                    "projects": {
                        "mlv-app": {
                            "active": {"codex": "codex-fresh"},
                            "sessions": {
                                "codex-fresh": {"agent": "codex", "status": "active"},
                                "old-session": {"agent": "codex", "status": "superseded"},
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        config_path.write_text(
            json.dumps(
                {
                    "sessions": [
                        {"agent": "codex", "session_id": "old-session", "inbox": str(self.state_dir / "inbox-codex.jsonl")},
                        {"agent": "claude", "session_id": "claude-live", "inbox": str(self.state_dir / "inbox-claude.jsonl")},
                    ]
                }
            ),
            encoding="utf-8",
        )
        settings_path_for_state_dir(self.state_dir).write_text(
            json.dumps(
                {
                    "wake_idle_threshold_seconds": 9,
                    "wake_max_wait_seconds": 77,
                }
            ),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"CODEX_THREAD_ID": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"}):
            result = configure_watcher(
                config_path=config_path,
                state_dir=self.state_dir,
                agent="codex",
                project=None,
                cwd=str(ROOT),
                python_executable="py",
            )
        session_ids = {entry["session_id"] for entry in result["sessions"]}
        self.assertIn("codex-fresh", session_ids)
        self.assertIn("mlv-app", session_ids)
        self.assertIn("claude-live", session_ids)
        self.assertNotIn("old-session", session_ids)
        codex_commands = [
            entry.get("on_message_command_template", "")
            for entry in result["sessions"]
            if entry.get("agent") == "codex"
        ]
        self.assertTrue(any("-ThreadId {desktop_thread_id}" in " ".join(command) for command in codex_commands))
        self.assertTrue(any("-IdleThresholdSeconds 9" in " ".join(command) for command in codex_commands))
        self.assertTrue(any("-MaxWaitSeconds 77" in " ".join(command) for command in codex_commands))
        self.assertFalse(any("-ExpectedTitleMarker" in " ".join(command) for command in codex_commands))

    def test_codex_bridge_reminder_reports_unbootstrapped_and_recovery_hint(self) -> None:
        script = Path(__file__).resolve().parent / "codex_bridge_reminder.ps1"
        log_path = self.tempdir / "codex-bridge-reminder.log"
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-WorkspaceRoot",
                str(ROOT),
                "-ProjectBucket",
                "mlv-app",
                "-SessionRegistryPath",
                str(self.tempdir / "missing-session.json"),
                "-WatcherConfigPath",
                str(self.tempdir / "missing-watcher-config.json"),
                "-WatcherPidPath",
                str(self.tempdir / "missing-watcher.pid"),
                "-BridgeWatchFlagPath",
                str(self.tempdir / "missing-watch.flag"),
                "-SettingsPath",
                str(self.tempdir / "missing-settings.json"),
                "-LogPath",
                str(log_path),
                "-NoToast",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Bridge state: UNBOOTSTRAPPED", result.stdout)
        self.assertIn("recover_bridge_session.py", result.stdout)


if __name__ == "__main__":
    unittest.main()
