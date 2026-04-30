import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_bridge import AgentBridge
from bootstrap_session import bootstrap, detect_bootstrap_origin, restart_watcher_for_code_change, sweep_orphan_watchers, watcher_code_signature
from compact import prune_audit_logs, reap_stale_server_pids
from configure_watcher import configure_watcher
from consume_inbox import consume
from core.addressing import AgentInbox, MessageKind, SenderContext, SessionInbox
from core.paths import BridgeRootMovedError, ensure_bridge_root_manifest, resolve_bridge_paths
from core.processes import acquire_singleton_lease, heartbeat_lease, release_lease
from core.runtime import peer_runtime_path_for_state_dir, read_runtime_breadcrumb
from core.routing import RoutingStatus, resolve_route
from core.settings import load_settings, settings_path_for_state_dir
from core.storage import append_jsonl, read_jsonl, with_schema_version, write_json, write_jsonl
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

    def test_wake_codex_builds_quoted_inner_command_for_space_paths(self) -> None:
        script = Path(__file__).resolve().parent / "wake_codex.ps1"
        lock_dir = self.tempdir / "path with space"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / "wake codex lock.txt"

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Message",
                "check bridge inbox from test",
                "-ThreadId",
                "",
                "-IdleThresholdSeconds",
                "0",
                "-MaxWaitSeconds",
                "1",
                "-TotalRuntimeTimeoutSeconds",
                "5",
                "-PrintInnerCommand",
                "-LockFile",
                str(lock_file),
                "-ProcessName",
                "__codex_missing_process__",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"& '{script}'", result.stdout)
        self.assertIn(f"-LockFile '{lock_file}'", result.stdout)
        self.assertIn("-Message 'check bridge inbox from test'", result.stdout)

    def test_wake_codex_foreground_path_has_focus_retry_hardening(self) -> None:
        script = Path(__file__).resolve().parent / "wake_codex.ps1"
        text = script.read_text(encoding="utf-8")

        self.assertIn("public static extern bool BringWindowToTop", text)
        self.assertIn("public static extern uint SendInput", text)
        self.assertIn("public static extern void SwitchToThisWindow", text)
        self.assertIn("function Send-AltTap", text)
        self.assertIn("function Invoke-CodexComposerUiaFallback", text)
        self.assertIn("Add-Type -AssemblyName UIAutomationClient", text)
        self.assertIn("*ProseMirror*", text)
        self.assertIn("Send-BridgeMessageKeys -Value $Message", text)
        self.assertIn("SendInput ALT-tap fallback", text)
        self.assertIn("SwitchToThisWindow fallback", text)
        self.assertIn("exit 13", text)

        helper = text.split("function Invoke-CodexForegroundAttempt", 1)[1].split("if ($PrintInnerCommand", 1)[0]
        self.assertLess(helper.index("BringWindowToTop"), helper.index("SetForegroundWindow"))

    def test_wake_codex_input_size_smoke_runs_under_powershell(self) -> None:
        script = Path(__file__).resolve().parent / "wake_codex.ps1"

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-TestInputSize",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Win32 INPUT size=", result.stdout)

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
        self.assertIn("receiver bucket", result.message)

    def test_sender_session_id_is_rejected_as_receiver_bucket(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.activate_session("codex", "codex-live", project="mlv-app")

        result = bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] do not route to sender bucket",
            session_id="claude-live",
        )

        self.assertFalse(result.ok)
        self.assertIn("belongs to claude session", result.message)
        self.assertIn("receiver bucket for codex", result.message)

    def test_send_to_peer_writes_v2_route_metadata(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app", bootstrap_origin="parent")
        bridge.activate_session("codex", "codex-live", project="mlv-app")

        result = bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] route metadata",
            target_session_id="codex-live",
        )

        self.assertTrue(result.ok)
        inbox = bridge.peek_inbox("codex", session_id="codex-live")
        row = inbox.data["messages"][0]
        self.assertEqual(row["schema_version"], 2)
        self.assertEqual(row["from_session_id"], "claude-live")
        self.assertEqual(row["to_session_id"], "codex-live")
        self.assertEqual(row["from_session_id_kind"], "parent")

    def test_target_session_id_alias_rejects_conflict(self) -> None:
        bridge = AgentBridge(self.state_dir)
        result = bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] conflict",
            session_id="codex-live",
            target_session_id="codex-other",
        )

        self.assertFalse(result.ok)
        self.assertIn("target_session_id", result.message)

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

    def _activate_cross_project_fixture(self, bridge: AgentBridge) -> None:
        bridge.activate_session("claude", "claude-source", project="source-app")
        bridge.activate_session("codex", "codex-source", project="source-app")
        bridge.activate_session("claude", "claude-target", project="target-app")
        bridge.activate_session("codex", "codex-target", project="target-app")

    def test_cross_pair_init_requires_manual_confirmation(self) -> None:
        bridge = AgentBridge(self.state_dir)
        self._activate_cross_project_fixture(bridge)

        result = bridge.cross_pair_init(
            agent="claude",
            project="source-app",
            peer_project="target-app",
            role="advisor",
            nonce="nonce-12345",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "confirmation_required")
        self.assertIn("different projects", result.message)

    def test_cross_pair_nonce_match_activates_read_and_advise(self) -> None:
        bridge = AgentBridge(self.state_dir)
        self._activate_cross_project_fixture(bridge)

        first = bridge.cross_pair_init(
            agent="claude",
            project="source-app",
            peer_project="target-app",
            role="advisor",
            nonce="nonce-activate-1",
            session_id="claude-source",
            confirm_different_projects=True,
            requested_permission_tier="write_with_confirmation",
        )
        second = bridge.cross_pair_init(
            agent="codex",
            project="target-app",
            peer_project="source-app",
            role="executor",
            nonce="nonce-activate-1",
            session_id="codex-target",
            confirm_different_projects=True,
        )

        self.assertTrue(first.ok)
        self.assertEqual(first.status, "pending")
        self.assertTrue(second.ok)
        self.assertEqual(second.status, "active")
        link = second.data["link"]
        self.assertEqual(link["permission_tier"], "read_and_advise")
        self.assertEqual(link["advisor"]["project"], "source-app")
        self.assertEqual(link["executor"]["project"], "target-app")
        self.assertTrue((bridge.cross_project_pairs_dir / ("%s.json" % link["link_id"])).exists())

        replay = bridge.cross_pair_init(
            agent="claude",
            project="source-app",
            peer_project="target-app",
            role="advisor",
            nonce="nonce-activate-1",
            confirm_different_projects=True,
        )
        self.assertFalse(replay.ok)
        self.assertIn("already been used", replay.message)

    def test_cross_pair_nonce_window_expiry_keeps_second_side_pending(self) -> None:
        bridge = AgentBridge(self.state_dir)
        self._activate_cross_project_fixture(bridge)

        first = bridge.cross_pair_init(
            agent="claude",
            project="source-app",
            peer_project="target-app",
            role="advisor",
            nonce="nonce-expire-1",
            confirm_different_projects=True,
        )
        self.assertEqual(first.status, "pending")
        pending = json.loads(bridge.cross_project_pending_path.read_text(encoding="utf-8"))
        pending["observations"][0]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat(timespec="seconds")
        write_json(bridge.cross_project_pending_path, pending)

        second = bridge.cross_pair_init(
            agent="codex",
            project="target-app",
            peer_project="source-app",
            role="executor",
            nonce="nonce-expire-1",
            confirm_different_projects=True,
        )
        self.assertTrue(second.ok)
        self.assertEqual(second.status, "pending")
        self.assertEqual(bridge.list_cross_project_links().data["count"], 0)

    def test_cross_pair_promotion_is_executor_only_and_explicit(self) -> None:
        bridge = AgentBridge(self.state_dir)
        self._activate_cross_project_fixture(bridge)
        bridge.cross_pair_init(
            agent="claude",
            project="source-app",
            peer_project="target-app",
            role="advisor",
            nonce="nonce-promote-1",
            confirm_different_projects=True,
        )
        active = bridge.cross_pair_init(
            agent="codex",
            project="target-app",
            peer_project="source-app",
            role="executor",
            nonce="nonce-promote-1",
            confirm_different_projects=True,
        )
        link_id = active.data["link"]["link_id"]

        advisor = bridge.cross_pair_promote(
            link_id=link_id,
            project="source-app",
            permission_tier="write_with_confirmation",
            agent="claude",
            confirm_write_override=True,
        )
        self.assertFalse(advisor.ok)
        self.assertIn("only the executor", advisor.message)

        missing_confirm = bridge.cross_pair_promote(
            link_id=link_id,
            project="target-app",
            permission_tier="write_with_confirmation",
            agent="codex",
        )
        self.assertFalse(missing_confirm.ok)
        self.assertEqual(missing_confirm.status, "confirmation_required")

        promoted = bridge.cross_pair_promote(
            link_id=link_id,
            project="target-app",
            permission_tier="write_with_confirmation",
            agent="codex",
            session_id="codex-target",
            confirm_write_override=True,
        )
        self.assertTrue(promoted.ok)
        self.assertEqual(promoted.data["link"]["permission_tier"], "write_with_confirmation")

    def test_cross_project_message_routes_with_policy_and_revoke_blocks(self) -> None:
        bridge = AgentBridge(self.state_dir)
        self._activate_cross_project_fixture(bridge)
        bridge.cross_pair_init(
            agent="claude",
            project="source-app",
            peer_project="target-app",
            role="advisor",
            nonce="nonce-message-1",
            confirm_different_projects=True,
        )
        active = bridge.cross_pair_init(
            agent="codex",
            project="target-app",
            peer_project="source-app",
            role="executor",
            nonce="nonce-message-1",
            confirm_different_projects=True,
        )
        link_id = active.data["link"]["link_id"]

        sent = bridge.send_cross_project_message(
            link_id=link_id,
            from_project="source-app",
            from_agent="claude",
            to_agent="codex",
            message="Advise on porting this parser; do not write directly.",
        )
        self.assertTrue(sent.ok)
        self.assertEqual(sent.data["to_project"], "target-app")
        inbox = bridge.peek_inbox("codex", session_id="target-app")
        cross_rows = [row for row in inbox.data["messages"] if "TYPE: CROSS_PROJECT_MESSAGE" in row["body"]]
        self.assertEqual(len(cross_rows), 1)
        self.assertIn("ROLE_POLICY: communication-only", cross_rows[0]["body"])
        self.assertIn("PERMISSION_TIER: read_and_advise", cross_rows[0]["body"])

        revoked = bridge.cross_pair_revoke(
            link_id=link_id,
            project="target-app",
            agent="codex",
            reason="test complete",
        )
        self.assertTrue(revoked.ok)
        blocked = bridge.send_cross_project_message(
            link_id=link_id,
            from_project="source-app",
            from_agent="claude",
            to_agent="codex",
            message="This should not send.",
        )
        self.assertFalse(blocked.ok)
        self.assertIn("revoked", blocked.message)

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
        actions = [item.get("action") for item in read_jsonl(bridge.audit_path)]
        self.assertIn("bootstrap_rotation_routed_messages", actions)

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

    def test_truedup_session_routing_dry_run_and_rekey(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        append_jsonl(
            bridge.inbox_path("codex"),
            {
                "id": "orphan-1",
                "created_at": "2026-04-29T00:00:00+00:00",
                "session_id": "claude-live",
                "from": "claude",
                "to": "codex",
                "body": "misrouted",
                "read_at": None,
                "seen_at": None,
            },
        )

        dry_run = bridge.truedup_session_routing("codex", dry_run=True)
        self.assertTrue(dry_run.ok)
        self.assertEqual(dry_run.data["count"], 1)
        self.assertEqual(dry_run.data["orphans"][0]["session_id"], "claude-live")

        applied = bridge.truedup_session_routing("codex", dry_run=False, mode="rekey")
        self.assertTrue(applied.ok)
        self.assertEqual(applied.data["count"], 1)
        row = read_jsonl(bridge.inbox_path("codex"))[0]
        self.assertEqual(row["session_id"], "codex-live")
        self.assertEqual(row["to_session_id"], "codex-live")
        self.assertEqual(row["session_truedup_from"], "claude-live")

        second = bridge.truedup_session_routing("codex", dry_run=True)
        self.assertEqual(second.data["count"], 0)

    def test_truedup_session_routing_quarantines_orphans(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        append_jsonl(
            bridge.inbox_path("codex"),
            {
                "id": "orphan-2",
                "created_at": "2026-04-29T00:00:00+00:00",
                "session_id": "claude-live",
                "from": "claude",
                "to": "codex",
                "body": "misrouted",
                "read_at": None,
                "seen_at": None,
            },
        )

        applied = bridge.truedup_session_routing("codex", dry_run=False, mode="quarantine")

        self.assertTrue(applied.ok)
        self.assertEqual(read_jsonl(bridge.inbox_path("codex")), [])
        orphan_path = bridge.inbox_path("codex").with_suffix(".orphan.jsonl")
        quarantined = read_jsonl(orphan_path)
        self.assertEqual(quarantined[0]["id"], "orphan-2")
        actions = [item.get("action") for item in read_jsonl(bridge.audit_path)]
        self.assertIn("session_truedup_quarantined", actions)

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

    def test_mark_read_backfills_seen_at_when_missing(self) -> None:
        bridge = AgentBridge(self.state_dir)
        result = bridge.send_to_peer("codex", "claude", "[[handoff:claude]] receipt hello", session_id="mlv-app")
        self.assertTrue(result.ok)
        message_id = result.data["id"]

        inbox_path = bridge.inbox_path("claude")
        rows = read_jsonl(inbox_path)
        self.assertEqual(len(rows), 1)
        rows[0]["read_at"] = "2026-04-29T01:02:03+00:00"
        rows[0]["seen_at"] = None
        rows[0]["seen_by_session"] = None
        rows[0]["seen_via"] = None
        write_jsonl(inbox_path, rows)

        marked = bridge.mark_read("claude", message_id)
        self.assertTrue(marked.ok)
        self.assertEqual(marked.status, "marked_read")

        updated = read_jsonl(inbox_path)[0]
        self.assertEqual(updated["read_at"], "2026-04-29T01:02:03+00:00")
        self.assertIsNotNone(updated["seen_at"])
        self.assertEqual(updated["seen_by_session"], "mlv-app")
        self.assertEqual(updated["seen_via"], "implicit_via_mark_read")

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

    def test_pending_bridge_actions_record_list_and_resolve(self) -> None:
        bridge = AgentBridge(self.state_dir)

        recorded = bridge.record_pending_bridge_action(
            "codex",
            "Reply to Claude about follow-up scope",
            message_id="msg-123",
            related_session_id="mlv-app",
            priority="high",
            details="Need to send SPEC_REVIEW_RESULT after current patch.",
        )
        self.assertTrue(recorded.ok)
        action_id = recorded.data["action"]["id"]

        pending = bridge.list_pending_bridge_actions(owner_agent="codex")
        self.assertTrue(pending.ok)
        self.assertEqual(pending.data["count"], 1)
        self.assertEqual(pending.data["actions"][0]["id"], action_id)
        self.assertEqual(pending.data["actions"][0]["status"], "pending")
        self.assertEqual(pending.data["actions"][0]["priority"], "high")

        resolved = bridge.resolve_pending_bridge_action(
            action_id,
            resolved_by="codex",
            resolution="Sent the reply after finishing the patch.",
        )
        self.assertTrue(resolved.ok)
        self.assertEqual(resolved.data["action"]["status"], "resolved")
        self.assertEqual(resolved.data["action"]["resolved_by"], "codex")

        still_pending = bridge.list_pending_bridge_actions(owner_agent="codex")
        self.assertEqual(still_pending.data["count"], 0)
        all_actions = bridge.list_pending_bridge_actions(owner_agent="codex", status="all")
        self.assertEqual(all_actions.data["count"], 1)
        self.assertEqual(all_actions.data["actions"][0]["status"], "resolved")

    def test_pending_bridge_actions_are_bounded_and_filterable(self) -> None:
        bridge = AgentBridge(self.state_dir)
        for index in range(3):
            recorded = bridge.record_pending_bridge_action(
                "codex" if index < 2 else "claude",
                "Action %d" % index,
                priority="normal",
            )
            self.assertTrue(recorded.ok)

        rejected = bridge.list_pending_bridge_actions(status="later")
        self.assertFalse(rejected.ok)
        self.assertIn("status", rejected.message)

        page = bridge.list_pending_bridge_actions(owner_agent="codex", limit=1, offset=0)
        self.assertTrue(page.ok)
        self.assertEqual(page.data["count"], 1)
        self.assertEqual(page.data["total_count"], 2)
        self.assertTrue(page.data["has_more"])

    def test_next_pending_bridge_action_uses_priority_due_date_and_age(self) -> None:
        bridge = AgentBridge(self.state_dir)
        low = bridge.record_pending_bridge_action(
            "codex",
            "Low item",
            priority="low",
        )
        self.assertTrue(low.ok)
        urgent = bridge.record_pending_bridge_action(
            "codex",
            "Urgent item",
            priority="urgent",
        )
        self.assertTrue(urgent.ok)
        due_soon = bridge.record_pending_bridge_action(
            "codex",
            "High due soon",
            priority="high",
            due_at="2026-04-29T10:00:00+00:00",
        )
        self.assertTrue(due_soon.ok)

        next_action = bridge.next_pending_bridge_action("codex")
        self.assertTrue(next_action.ok)
        self.assertEqual(next_action.data["count"], 3)
        self.assertEqual(next_action.data["action"]["summary"], "Urgent item")

        bridge.resolve_pending_bridge_action(urgent.data["action"]["id"], resolved_by="codex")
        next_after_urgent = bridge.next_pending_bridge_action("codex")
        self.assertEqual(next_after_urgent.data["action"]["summary"], "High due soon")

        bridge.resolve_pending_bridge_action(due_soon.data["action"]["id"], resolved_by="codex")
        next_after_due = bridge.next_pending_bridge_action("codex")
        self.assertEqual(next_after_due.data["action"]["summary"], "Low item")

    def test_next_pending_bridge_action_empty_when_no_pending(self) -> None:
        bridge = AgentBridge(self.state_dir)
        result = bridge.next_pending_bridge_action("codex")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "empty")
        self.assertIsNone(result.data["action"])
        self.assertEqual(result.data["count"], 0)

    def test_next_pending_bridge_action_skips_parked_and_blocked_items(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.record_pending_bridge_action("codex", "Parked item", priority="urgent")
        bridge.record_pending_bridge_action("codex", "Blocked item", priority="high")
        bridge.record_pending_bridge_action("codex", "Actionable item", priority="normal")
        pending = bridge._load_pending_actions()
        actions = pending["actions"]
        actions[0]["execution_state"] = "parked"
        actions[1]["execution_state"] = "blocked"
        bridge._save_pending_actions(pending)

        result = bridge.next_pending_bridge_action("codex")

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "next_pending_bridge_action")
        self.assertEqual(result.data["action"]["summary"], "Actionable item")

    def test_execution_task_requires_explicit_displacement_and_records_progress(self) -> None:
        bridge = AgentBridge(self.state_dir)
        action = bridge.record_pending_bridge_action(
            "codex",
            "Fix routing footgun",
            priority="high",
        )
        self.assertTrue(action.ok)
        action_id = action.data["action"]["id"]

        started = bridge.start_execution_task(
            "codex",
            "Fix routing footgun",
            source="ledger",
            related_action_id=action_id,
            checkpoint="open file",
            allowed_interrupts=["urgent", "confirm_before"],
            prior_action_id=action_id,
            prior_disposition="continue",
        )
        self.assertTrue(started.ok)
        task_id = started.data["task"]["id"]
        self.assertEqual(started.data["task"]["status"], "starting")
        self.assertEqual(started.data["task"]["proof_status"], "awaiting")

        active = bridge.execution_status("codex")
        self.assertEqual(active.data["active_task"]["id"], task_id)
        self.assertEqual(active.data["active_task"]["related_action_id"], action_id)

        rejected = bridge.start_execution_task(
            "codex",
            "Something else",
            source="user",
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.status, "active_task_exists")

        progress = bridge.record_execution_progress(
            "codex",
            task_id,
            "patch_applied",
            details="Edited routing validation.",
            checkpoint="tests next",
        )
        self.assertTrue(progress.ok)
        self.assertEqual(progress.data["task"]["status"], "active")
        self.assertEqual(progress.data["task"]["proof_status"], "proved")
        self.assertEqual(progress.data["task"]["checkpoint"], "tests next")

    def test_execution_task_displacement_and_completion_reconcile_with_ledger(self) -> None:
        bridge = AgentBridge(self.state_dir)
        first = bridge.record_pending_bridge_action("codex", "First task", priority="normal")
        second = bridge.record_pending_bridge_action("codex", "Urgent task", priority="urgent")
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        first_id = first.data["action"]["id"]
        second_id = second.data["action"]["id"]

        started = bridge.start_execution_task(
            "codex",
            "First task",
            source="ledger",
            related_action_id=first_id,
            checkpoint="open file",
            prior_action_id=first_id,
            prior_disposition="continue",
        )
        self.assertTrue(started.ok)
        first_task_id = started.data["task"]["id"]

        displaced = bridge.start_execution_task(
            "codex",
            "Urgent task",
            source="interrupt",
            related_action_id=second_id,
            displaced_by="claude_action_request",
            displacement_reason="Higher-priority wake breakage",
            prior_action_id=first_id,
            prior_disposition="displaced",
        )
        self.assertTrue(displaced.ok)
        second_task_id = displaced.data["task"]["id"]
        status = bridge.execution_status("codex")
        self.assertEqual(status.data["active_task"]["id"], second_task_id)
        self.assertEqual(status.data["recent_tasks"][-1]["id"], first_task_id)
        self.assertEqual(status.data["recent_tasks"][-1]["status"], "displaced")

        completed = bridge.complete_execution_task(
            "codex",
            second_task_id,
            outcome="completed",
            resolution="Shipped urgent task.",
        )
        self.assertTrue(completed.ok)
        after = bridge.execution_status("codex")
        self.assertIsNone(after.data["active_task"])

        pending = bridge.list_pending_bridge_actions(owner_agent="codex", status="all")
        actions = {item["id"]: item for item in pending.data["actions"]}
        self.assertEqual(actions[second_id]["status"], "resolved")
        self.assertEqual(actions[first_id]["execution_state"], "displaced")

    def test_execution_status_derives_not_started_when_no_proof_arrives(self) -> None:
        bridge = AgentBridge(self.state_dir)
        started = bridge.start_execution_task(
            "codex",
            "Needs proof",
            source="manual",
        )
        self.assertTrue(started.ok)
        payload = json.loads(bridge.execution_state_path.read_text(encoding="utf-8"))
        payload["owners"]["codex"]["active_task"]["proof_deadline_at"] = "2026-04-29T00:00:00+00:00"
        payload["owners"]["codex"]["active_task"]["status"] = "starting"
        write_json(bridge.execution_state_path, payload)

        status = bridge.execution_status("codex")
        self.assertEqual(status.data["active_task"]["derived_status"], "not_started")
        self.assertIn("resume active task", status.data["resume_hint"])

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

    def test_bridge_process_status_surfaces_tool_refresh_required(self) -> None:
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "tool-refresh-status.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "refresh_required": True,
                    "reason": "tool_manifest_changed_during_wrapper_session",
                    "previous_signature": "old",
                    "current_signature": "new",
                    "changed_files": ["server.py"],
                }
            ),
            encoding="utf-8",
        )

        status = AgentBridge(self.state_dir).bridge_process_status()

        self.assertEqual(status.status, "attention")
        self.assertTrue(status.data["tool_refresh"]["refresh_required"])
        self.assertEqual(status.data["tool_refresh"]["status"], "refresh_required")

    def test_resume_wake_for_session_clears_breaker_and_status(self) -> None:
        bridge = AgentBridge(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        bridge.wake_breaker_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": "2026-04-29T00:00:00+00:00",
                    "sessions": {
                        "codex-live": {
                            "breaker_state": "open",
                            "opened_at": "2026-04-29T00:00:00+00:00",
                            "last_failure_at": "2026-04-29T00:01:00+00:00",
                            "failures": [{"at": "2026-04-29T00:01:00+00:00", "code": "1"}],
                            "exit_code_distribution": {"1": 1},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        status = bridge.bridge_process_status()
        self.assertEqual(status.status, "attention")
        self.assertEqual(status.data["wake_breakers"]["open_session_count"], 1)

        breaker_status = bridge.wake_breaker_status("codex-live")
        self.assertEqual(breaker_status.data["session"]["breaker_state"], "open")

        resumed = bridge.resume_wake_for_session("codex-live")
        self.assertTrue(resumed.ok)
        self.assertEqual(resumed.status, "cleared")

        after = bridge.bridge_process_status()
        self.assertEqual(after.data["wake_breakers"]["open_session_count"], 0)
        self.assertEqual(bridge.wake_breaker_status("codex-live").data["session"], None)

    def test_nudge_peer_grants_bypass_and_watcher_consumes_seen_backlog(self) -> None:
        bridge = AgentBridge(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        inbox = bridge.inbox_path("codex")
        append_jsonl(
            inbox,
            {
                "id": "msg-1",
                "created_at": "2026-04-29T00:00:00+00:00",
                "session_id": "codex-live",
                "from": "claude",
                "to": "codex",
                "body": "wake me",
                "marker_variant": None,
                "read_at": None,
                "seen_at": None,
            },
        )
        write_json(
            bridge.watcher_state_path,
            {
                "seen_ids": ["msg-1"],
                "toasted_ids": [],
                "pending_wake_verifications": [],
                "paused_wake_messages": [],
                "unknown_origin_warnings": [],
                "wake_fire_history": [],
            },
        )
        recent_failure = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_json(
            bridge.wake_breaker_path,
            {
                "schema_version": 1,
                "sessions": {
                    "codex-live": {
                        "breaker_state": "open",
                        "opened_at": recent_failure,
                        "last_failure_at": recent_failure,
                        "failures": [{"at": recent_failure, "code": "1"}],
                    }
                },
            },
        )

        grant = bridge.nudge_peer("codex", "codex-live")
        self.assertTrue(grant.ok)
        self.assertEqual(grant.status, "granted")

        with patch("watcher.run_command_for_session", return_value={"ok": True, "returncode": 0, "retryable": False}):
            watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "inbox": str(inbox),
                    "on_message": "notify",
                    "on_message_command": "py",
                },
                seen_ids={"msg-1"},
                state_path=bridge.watcher_state_path,
                toasts_enabled=False,
            )

        self.assertEqual(bridge.wake_breaker_status("codex-live").data["session"], None)
        actions = [row.get("action") for row in read_jsonl(bridge.audit_path)]
        self.assertIn("wake_breaker_bypass_granted", actions)
        self.assertIn("wake_recovery_backlog_selected", actions)
        self.assertIn("wake_breaker_bypass_consumed", actions)

    def test_watcher_autoclose_retries_one_seen_backlog_message(self) -> None:
        bridge = AgentBridge(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        inbox = bridge.inbox_path("codex")
        append_jsonl(
            inbox,
            {
                "id": "msg-2",
                "created_at": "2026-04-29T00:00:00+00:00",
                "session_id": "codex-live",
                "from": "claude",
                "to": "codex",
                "body": "retry me",
                "marker_variant": None,
                "read_at": None,
                "seen_at": None,
            },
        )
        write_json(
            bridge.watcher_state_path,
            {
                "seen_ids": ["msg-2"],
                "toasted_ids": [],
                "pending_wake_verifications": [],
                "paused_wake_messages": [],
                "unknown_origin_warnings": [],
                "wake_fire_history": [],
            },
        )
        old_failure = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(timespec="seconds")
        write_json(
            bridge.wake_breaker_path,
            {
                "schema_version": 1,
                "sessions": {
                    "codex-live": {
                        "breaker_state": "open",
                        "opened_at": old_failure,
                        "last_failure_at": old_failure,
                        "failures": [{"at": old_failure, "code": "1"}],
                    }
                },
            },
        )

        with patch("watcher.run_command_for_session", return_value={"ok": True, "returncode": 0, "retryable": False}):
            watcher.process_session_once(
                {
                    "agent": "codex",
                    "session_id": "codex-live",
                    "inbox": str(inbox),
                    "on_message": "notify",
                    "on_message_command": "py",
                },
                seen_ids={"msg-2"},
                state_path=bridge.watcher_state_path,
                toasts_enabled=False,
            )

        self.assertEqual(bridge.wake_breaker_status("codex-live").data["session"], None)
        actions = [row.get("action") for row in read_jsonl(bridge.audit_path)]
        self.assertIn("wake_recovery_backlog_selected", actions)
        self.assertIn("wake_breaker_autoclose_retry", actions)

    def test_backpressure_rejection_rearms_existing_unread_for_nudge(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.activate_session("codex", "codex-live", project="mlv-app")
        first = bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] first unread",
            session_id="codex-live",
        )
        self.assertTrue(first.ok)
        write_json(
            bridge.watcher_state_path,
            {
                "seen_ids": [first.data["id"]],
                "toasted_ids": [],
                "pending_wake_verifications": [],
                "paused_wake_messages": [],
                "unknown_origin_warnings": [],
                "wake_fire_history": [],
            },
        )

        second = bridge.send_to_peer(
            "claude",
            "codex",
            "[[handoff:codex]] second unread",
            session_id="codex-live",
        )

        self.assertFalse(second.ok)
        watcher_state = json.loads(bridge.watcher_state_path.read_text(encoding="utf-8"))
        self.assertNotIn(first.data["id"], watcher_state["seen_ids"])
        actions = [row.get("action") for row in read_jsonl(bridge.audit_path)]
        self.assertIn("backpressure_rejected_nudge_attempted", actions)

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
        self.assertEqual(result["bootstrap_origin"], "parent")
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
        self.assertEqual(breadcrumb["schema_version"], 2)
        self.assertEqual(breadcrumb["session_id"], "codex-new")
        self.assertEqual(breadcrumb["desktop_thread_id"], "019dcfe4-bd5d-7841-a7c1-2e8969a777c5")
        self.assertEqual(breadcrumb["bootstrap_origin"], "parent")

    def test_detect_bootstrap_origin_subagent_via_thread_mismatch(self) -> None:
        origin, signals = detect_bootstrap_origin(
            agent="codex",
            env={
                "CODEX_THREAD_ID": "child-thread",
                "CODEX_PARENT_THREAD_ID": "parent-thread",
            },
        )
        self.assertEqual(origin, "subagent")
        self.assertTrue(signals["parent_thread_id_mismatch"])

    def test_detect_bootstrap_origin_unknown_when_thread_missing(self) -> None:
        origin, signals = detect_bootstrap_origin(agent="codex", env={})
        self.assertEqual(origin, "unknown")
        self.assertIsNone(signals["env_marker"])

    def test_bootstrap_refuses_subagent_origin(self) -> None:
        with patch.dict("os.environ", {"CODEX_SUBAGENT": "1", "CODEX_THREAD_ID": "child-thread"}):
            result = bootstrap(
                state_dir=self.state_dir,
                agent="codex",
                cwd=str(ROOT),
                previous_session_id=None,
                session_id="codex-subagent",
                project=None,
                handshake_retries=1,
            )
        self.assertTrue(result["refused"])
        self.assertEqual(result["exit_code"], 3)
        status = AgentBridge(self.state_dir).session_status("mlv-app")
        self.assertEqual(status.data["active"].get("codex"), None)

    def test_bootstrap_subagent_retargets_to_explicit_parent_thread(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        with patch.dict(
            "os.environ",
            {
                "CODEX_SUBAGENT": "1",
                "CODEX_THREAD_ID": "child-thread",
                "CODEX_PARENT_THREAD_ID": "parent-thread",
            },
            clear=True,
        ):
            result = bootstrap(
                state_dir=self.state_dir,
                agent="codex",
                cwd=str(ROOT),
                previous_session_id=None,
                session_id="codex-retarget",
                project=None,
                handshake_retries=1,
                watcher_config=config_path,
                start_watcher=False,
            )

        self.assertFalse(result.get("refused", False))
        self.assertEqual(result["detected_bootstrap_origin"], "subagent")
        self.assertEqual(result["bootstrap_origin"], "parent")
        self.assertTrue(result["retargeted_to_parent"])
        status = AgentBridge(self.state_dir).session_status(result["project"])
        self.assertEqual(status.data["active"]["codex"], "codex-retarget")
        self.assertEqual(status.data["trusted_parent"]["codex"]["session_id"], "codex-retarget")
        breadcrumb = read_runtime_breadcrumb(peer_runtime_path_for_state_dir(self.state_dir, "codex"))
        self.assertEqual(breadcrumb["bootstrap_origin"], "parent")
        self.assertEqual(breadcrumb["desktop_thread_id"], "parent-thread")
        self.assertEqual(breadcrumb["bootstrap_thread_id"], "child-thread")
        self.assertEqual(breadcrumb["bootstrap_parent_thread_id"], "parent-thread")
        audit_rows = read_jsonl(self.state_dir / "messages.jsonl")
        self.assertTrue(any(row.get("action") == "bootstrap_subagent_retargeted_to_parent" for row in audit_rows))

    def test_bootstrap_refuses_codex_parent_thread_drift_without_override(self) -> None:
        bridge = AgentBridge(self.state_dir)
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

        with patch.dict("os.environ", {"CODEX_THREAD_ID": "child-thread"}, clear=True):
            result = bootstrap(
                state_dir=self.state_dir,
                agent="codex",
                cwd=str(ROOT),
                previous_session_id=None,
                session_id="codex-child",
                project="mlv-app",
                handshake_retries=1,
                start_watcher=False,
            )

        self.assertTrue(result["refused"])
        self.assertEqual(result["trusted_parent_drift"]["trusted_thread_id"], "parent-thread")
        self.assertEqual(result["trusted_parent_drift"]["incoming_thread_id"], "child-thread")
        status = bridge.session_status("mlv-app")
        self.assertEqual(status.data["active"]["codex"], "codex-parent")
        self.assertEqual(status.data["trusted_parent"]["codex"]["session_id"], "codex-parent")
        self.assertNotIn("codex-child", status.data["sessions"])
        audit_rows = read_jsonl(self.state_dir / "messages.jsonl")
        self.assertTrue(any(row.get("action") == "bootstrap_trusted_parent_drift_refused" for row in audit_rows))

    def test_bootstrap_allows_codex_parent_thread_drift_with_override(self) -> None:
        bridge = AgentBridge(self.state_dir)
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

        with patch.dict("os.environ", {"CODEX_THREAD_ID": "new-parent-thread"}, clear=True):
            result = bootstrap(
                state_dir=self.state_dir,
                agent="codex",
                cwd=str(ROOT),
                previous_session_id=None,
                session_id="codex-new-parent",
                project="mlv-app",
                handshake_retries=1,
                start_watcher=False,
                replace_trusted_parent=True,
            )

        self.assertFalse(result.get("refused", False))
        status = bridge.session_status("mlv-app")
        self.assertEqual(status.data["active"]["codex"], "codex-new-parent")
        self.assertEqual(status.data["trusted_parent"]["codex"]["session_id"], "codex-new-parent")
        breadcrumb = read_runtime_breadcrumb(peer_runtime_path_for_state_dir(self.state_dir, "codex"))
        self.assertEqual(breadcrumb["desktop_thread_id"], "new-parent-thread")

    def test_restart_watcher_for_code_change_clears_missing_signature_lease(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "locks").mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"sessions": [{"inbox": str(self.state_dir / "inbox-codex.jsonl")}]}),
            encoding="utf-8",
        )
        (self.tempdir / "watcher.pid").write_text("424242", encoding="utf-8")
        write_json(
            self.state_dir / "locks" / "watcher.lock",
            {
                "pid": 424242,
                "role": "watcher",
                "command": ["py", "watcher.py"],
                "heartbeat_at": "2026-04-29T00:00:00+00:00",
            },
        )

        with patch("bootstrap_session.is_process_alive", return_value=True), patch(
            "bootstrap_session._terminate_process", return_value=True
        ) as terminate:
            result = restart_watcher_for_code_change(config_path, state_dir=self.state_dir)

        self.assertEqual(result["status"], "restart_required")
        self.assertEqual(result["reason"], "missing_signature")
        self.assertTrue(result["stopped"])
        terminate.assert_called_once_with(424242)
        self.assertFalse((self.tempdir / "watcher.pid").exists())
        self.assertFalse((self.state_dir / "locks" / "watcher.lock").exists())

    def test_sweep_orphan_watchers_kills_non_lease_processes(self) -> None:
        bridge = AgentBridge(self.state_dir)
        config_path = self.tempdir / "watcher-config.json"
        other_config = self.tempdir / "other-config.json"
        watcher_script = Path(__file__).resolve().parent / "watcher.py"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "locks").mkdir(parents=True, exist_ok=True)
        write_json(
            self.state_dir / "locks" / "watcher.lock",
            {
                "pid": 111,
                "role": "watcher",
                "command": ["py", str(watcher_script), "--config", str(config_path)],
                "heartbeat_at": "2026-04-29T00:00:00+00:00",
            },
        )
        processes = [
            {
                "pid": 111,
                "parent_pid": 1,
                "started_at": "lease-start",
                "command_line": f'py "{watcher_script}" --config "{config_path}"',
            },
            {
                "pid": 222,
                "parent_pid": 1,
                "started_at": "orphan-start",
                "command_line": f'py "{watcher_script}" --config "{config_path}"',
            },
            {
                "pid": 333,
                "parent_pid": 1,
                "started_at": "other-config",
                "command_line": f'py "{watcher_script}" --config "{other_config}"',
            },
        ]

        with patch("bootstrap_session._enumerate_watcher_processes", return_value=processes), patch(
            "bootstrap_session._terminate_process", return_value=True
        ) as terminate:
            result = sweep_orphan_watchers(config_path, state_dir=self.state_dir, bridge=bridge)

        self.assertEqual(result["status"], "swept")
        self.assertEqual(result["lease_pid"], 111)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["orphan_count"], 1)
        self.assertEqual(result["killed"][0]["pid"], 222)
        terminate.assert_called_once_with(222)
        audit_rows = read_jsonl(self.state_dir / "messages.jsonl")
        self.assertTrue(
            any(
                row.get("action") == "orphan_watcher_killed" and row.get("pid") == 222
                for row in audit_rows
            )
        )

    def test_sweep_orphan_watchers_preserves_lease_holder(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        watcher_script = Path(__file__).resolve().parent / "watcher.py"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "locks").mkdir(parents=True, exist_ok=True)
        write_json(
            self.state_dir / "locks" / "watcher.lock",
            {
                "pid": 111,
                "role": "watcher",
                "command": ["py", str(watcher_script), "--config", str(config_path)],
                "heartbeat_at": "2026-04-29T00:00:00+00:00",
            },
        )

        with patch(
            "bootstrap_session._enumerate_watcher_processes",
            return_value=[
                {
                    "pid": 111,
                    "parent_pid": 1,
                    "started_at": "lease-start",
                    "command_line": f'py "{watcher_script}" --config "{config_path}"',
                }
            ],
        ), patch("bootstrap_session._terminate_process") as terminate:
            result = sweep_orphan_watchers(config_path, state_dir=self.state_dir)

        self.assertEqual(result["status"], "no_orphans")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["orphan_count"], 0)
        terminate.assert_not_called()

    def test_bootstrap_restarts_watcher_when_code_signature_missing_by_default(self) -> None:
        class FakeProcess:
            pid = 525252

        config_path = self.tempdir / "watcher-config.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "locks").mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"sessions": [{"inbox": str(self.state_dir / "inbox-codex.jsonl")}]}),
            encoding="utf-8",
        )
        (self.tempdir / "watcher.pid").write_text("424242", encoding="utf-8")
        write_json(
            self.state_dir / "locks" / "watcher.lock",
            {
                "pid": 424242,
                "role": "watcher",
                "command": ["py", "watcher.py"],
                "heartbeat_at": "2026-04-29T00:00:00+00:00",
            },
        )

        with patch.dict("os.environ", {"CODEX_THREAD_ID": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"}, clear=True), patch(
            "bootstrap_session.derive_project_identity",
            return_value={"canonical_root": str(ROOT), "rendezvous": "mlv-app", "source": "unit-test"},
        ), patch(
            "configure_watcher.derive_project_identity",
            return_value={"canonical_root": str(ROOT), "rendezvous": "mlv-app", "source": "unit-test"},
        ), patch("bootstrap_session.is_process_alive", return_value=True), patch(
            "bootstrap_session._terminate_process", return_value=True
        ), patch("bootstrap_session.subprocess.Popen", return_value=FakeProcess()), patch(
            "bootstrap_session.sweep_orphan_watchers", return_value={"status": "no_orphans"}
        ):
            result = bootstrap(
                state_dir=self.state_dir,
                agent="codex",
                cwd=str(ROOT),
                previous_session_id=None,
                session_id="codex-restart",
                project="mlv-app",
                handshake_retries=1,
                watcher_config=config_path,
                start_watcher=True,
            )

        self.assertEqual(result["watcher_process"]["status"], "restarted_code_changed")
        self.assertEqual(result["watcher_process"]["code_restart_check"]["reason"], "missing_signature")
        self.assertEqual(result["watcher_process"]["pid"], 525252)
        lease = json.loads((self.state_dir / "locks" / "watcher.lock").read_text(encoding="utf-8"))
        self.assertEqual(lease["pid"], 525252)
        self.assertEqual(lease["watcher_code_signature"]["signature"], watcher_code_signature()["signature"])

    def test_bootstrap_runs_orphan_sweep_by_default(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        with patch.dict("os.environ", {"CODEX_THREAD_ID": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"}, clear=True), patch(
            "bootstrap_session.derive_project_identity",
            return_value={"canonical_root": str(ROOT), "rendezvous": "mlv-app", "source": "unit-test"},
        ), patch(
            "configure_watcher.derive_project_identity",
            return_value={"canonical_root": str(ROOT), "rendezvous": "mlv-app", "source": "unit-test"},
        ), patch("bootstrap_session.sweep_orphan_watchers", return_value={"status": "no_orphans"}) as sweep:
            result = bootstrap(
                state_dir=self.state_dir,
                agent="codex",
                cwd=str(ROOT),
                previous_session_id=None,
                session_id="codex-sweep",
                project="mlv-app",
                handshake_retries=1,
                watcher_config=config_path,
                start_watcher=False,
            )

        sweep.assert_called_once()
        self.assertEqual(result["watcher_orphan_sweep"]["status"], "no_orphans")

    def test_bootstrap_can_skip_watcher_code_restart_for_debugging(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "locks").mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"sessions": [{"inbox": str(self.state_dir / "inbox-codex.jsonl")}]}),
            encoding="utf-8",
        )
        (self.tempdir / "watcher.pid").write_text("424242", encoding="utf-8")
        write_json(
            self.state_dir / "locks" / "watcher.lock",
            {
                "pid": 424242,
                "role": "watcher",
                "command": ["py", "watcher.py"],
                "heartbeat_at": "2026-04-29T00:00:00+00:00",
            },
        )

        with patch.dict("os.environ", {"CODEX_THREAD_ID": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"}, clear=True), patch(
            "bootstrap_session.derive_project_identity",
            return_value={"canonical_root": str(ROOT), "rendezvous": "mlv-app", "source": "unit-test"},
        ), patch(
            "configure_watcher.derive_project_identity",
            return_value={"canonical_root": str(ROOT), "rendezvous": "mlv-app", "source": "unit-test"},
        ), patch("bootstrap_session.is_process_alive", return_value=True), patch(
            "bootstrap_session._terminate_process", return_value=True
        ) as terminate:
            result = bootstrap(
                state_dir=self.state_dir,
                agent="codex",
                cwd=str(ROOT),
                previous_session_id=None,
                session_id="codex-debug-no-restart",
                project="mlv-app",
                handshake_retries=1,
                watcher_config=config_path,
                start_watcher=True,
                restart_watcher_if_code_changed=False,
            )

        self.assertEqual(result["watcher_process"]["status"], "already_running")
        self.assertIsNone(result["watcher_restart_check"])
        self.assertEqual(result["watcher_process"]["pid"], 424242)
        terminate.assert_not_called()

    def test_unknown_origin_session_does_not_supersede_parent(self) -> None:
        bridge = AgentBridge(self.state_dir)
        first = bridge.activate_session(
            "codex",
            "codex-parent",
            project="mlv-app",
            bootstrap_origin="parent",
            allow_supersede=True,
            trusted_parent_eligible=True,
        )
        self.assertEqual(first.status, "active")

        second = bridge.activate_session(
            "codex",
            "codex-unknown",
            project="mlv-app",
            bootstrap_origin="unknown",
            allow_supersede=False,
            trusted_parent_eligible=False,
        )
        self.assertEqual(second.status, "registered_secondary")
        status = bridge.session_status("mlv-app")
        self.assertEqual(status.data["active"]["codex"], "codex-parent")
        self.assertEqual(status.data["sessions"]["codex-unknown"]["status"], "secondary")
        self.assertEqual(status.data["trusted_parent"]["codex"]["session_id"], "codex-parent")

    def test_unknown_origin_session_reclassifies_to_parent_on_rebootstrap(self) -> None:
        config_path = self.tempdir / "watcher-config.json"
        with patch.dict("os.environ", {}, clear=True):
            first = bootstrap(
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
        self.assertEqual(first["bootstrap_origin"], "unknown")
        project_name = first["project"]
        initial_status = AgentBridge(self.state_dir).session_status(project_name)
        self.assertEqual(initial_status.data["sessions"]["codex-live"]["bootstrap_origin"], "unknown")

        with patch.dict("os.environ", {"CODEX_THREAD_ID": "019dcfe4-bd5d-7841-a7c1-2e8969a777c5"}, clear=True):
            second = bootstrap(
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
        self.assertEqual(second["bootstrap_origin"], "parent")
        status = AgentBridge(self.state_dir).session_status(project_name)
        self.assertEqual(status.data["active"]["codex"], "codex-live")
        self.assertEqual(status.data["sessions"]["codex-live"]["bootstrap_origin"], "parent")
        self.assertEqual(status.data["trusted_parent"]["codex"]["session_id"], "codex-live")
        breadcrumb = read_runtime_breadcrumb(peer_runtime_path_for_state_dir(self.state_dir, "codex"))
        self.assertEqual(breadcrumb["bootstrap_origin"], "parent")
        self.assertEqual(breadcrumb["desktop_thread_id"], "019dcfe4-bd5d-7841-a7c1-2e8969a777c5")

    def test_repair_bootstrap_provenance_rolls_back_to_trusted_parent(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        parent = bridge.activate_session(
            "codex",
            "codex-parent",
            project="mlv-app",
            bootstrap_origin="parent",
            trusted_parent_eligible=True,
        )
        self.assertEqual(parent.status, "active")
        bridge.record_session_runtime_metadata(
            agent="codex",
            session_id="codex-parent",
            project="mlv-app",
            desktop_thread_id="parent-thread",
            bootstrap_thread_id="parent-thread",
        )
        child = bridge.activate_session(
            "codex",
            "codex-child",
            project="mlv-app",
            bootstrap_origin="subagent",
            allow_supersede=True,
            trusted_parent_eligible=False,
        )
        self.assertEqual(child.status, "active")

        result = bridge.repair_bootstrap_provenance(
            agent="codex",
            project="mlv-app",
            bad_session_id="codex-child",
            trusted_parent_session_id="codex-parent",
            fallback_parent_thread_id="parent-thread",
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "rolled_back")
        status = bridge.session_status("mlv-app")
        self.assertEqual(status.data["active"]["codex"], "codex-parent")
        self.assertEqual(status.data["sessions"]["codex-child"]["status"], "superseded")
        breadcrumb = read_runtime_breadcrumb(peer_runtime_path_for_state_dir(self.state_dir, "codex"))
        self.assertEqual(breadcrumb["session_id"], "codex-parent")
        self.assertEqual(breadcrumb["desktop_thread_id"], "parent-thread")
        peer_inbox = bridge.peek_inbox("claude", "claude-live")
        self.assertIn("ROUTE_REPAIR", peer_inbox.message)
        audit_rows = read_jsonl(self.state_dir / "messages.jsonl")
        self.assertTrue(any(row.get("action") == "bootstrap_subagent_auto_rollback_succeeded" for row in audit_rows))

    def test_repair_bootstrap_provenance_freezes_without_trusted_parent(self) -> None:
        bridge = AgentBridge(self.state_dir)
        bridge.activate_session("claude", "claude-live", project="mlv-app")
        bridge.activate_session(
            "codex",
            "codex-child",
            project="mlv-app",
            bootstrap_origin="subagent",
            allow_supersede=True,
            trusted_parent_eligible=False,
        )

        result = bridge.repair_bootstrap_provenance(
            agent="codex",
            project="mlv-app",
            bad_session_id="codex-child",
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "frozen")
        self.assertEqual(bridge.bridge_status("mlv-app").message.split(".")[0], "Bridge is paused")
        peer_inbox = bridge.peek_inbox("claude", "claude-live")
        self.assertIn("BRIDGE_FROZEN", peer_inbox.message)
        audit_rows = read_jsonl(self.state_dir / "messages.jsonl")
        self.assertTrue(any(row.get("action") == "bootstrap_subagent_auto_rollback_failed" for row in audit_rows))

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

    def test_codex_bridge_reminder_final_guard_when_ledger_pending_and_execution_idle(self) -> None:
        script = Path(__file__).resolve().parent / "codex_bridge_reminder.ps1"
        bridge_root = self.tempdir / "bridge-root"
        state_dir = bridge_root / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "pending-actions.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "actions": [
                        {
                            "id": "action-1",
                            "owner_agent": "codex",
                            "summary": "Drain this item",
                            "priority": "normal",
                            "status": "pending",
                            "created_at": "2026-04-29T00:00:00+00:00",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
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
                str(bridge_root / "session.json"),
                "-WatcherConfigPath",
                str(bridge_root / "watcher-config.json"),
                "-WatcherPidPath",
                str(bridge_root / "watcher.pid"),
                "-BridgeWatchFlagPath",
                str(bridge_root / "missing-watch.flag"),
                "-SettingsPath",
                str(bridge_root / "settings.json"),
                "-LogPath",
                str(log_path),
                "-HookPhase",
                "final",
                "-NoToast",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("ledger_top=normal action-1 Drain this item", result.stdout)
        self.assertIn("FINAL-GUARD: execution is idle but the Codex ledger is not empty", result.stdout)

    def test_codex_bridge_reminder_final_guard_ignores_parked_ledger_items(self) -> None:
        script = Path(__file__).resolve().parent / "codex_bridge_reminder.ps1"
        bridge_root = self.tempdir / "parked-bridge-root"
        state_dir = bridge_root / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "pending-actions.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "actions": [
                        {
                            "id": "action-1",
                            "owner_agent": "codex",
                            "summary": "Parked item",
                            "priority": "urgent",
                            "status": "pending",
                            "execution_state": "parked",
                            "created_at": "2026-04-29T00:00:00+00:00",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
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
                str(bridge_root / "session.json"),
                "-WatcherConfigPath",
                str(bridge_root / "watcher-config.json"),
                "-WatcherPidPath",
                str(bridge_root / "watcher.pid"),
                "-BridgeWatchFlagPath",
                str(bridge_root / "missing-watch.flag"),
                "-SettingsPath",
                str(bridge_root / "settings.json"),
                "-LogPath",
                str(self.tempdir / "parked-codex-bridge-reminder.log"),
                "-HookPhase",
                "final",
                "-NoToast",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("ledger=empty", result.stdout)
        self.assertNotIn("FINAL-GUARD", result.stdout)


if __name__ == "__main__":
    unittest.main()
