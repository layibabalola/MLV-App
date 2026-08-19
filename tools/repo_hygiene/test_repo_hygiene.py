import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from .closeout import (
    approve_transaction,
    evaluate_closeout_triggers,
    open_transaction,
    record_agent_review,
    record_codex_recommendation,
    sign_closeout_payload,
    transaction_status,
    tx_hash,
    validate_transaction_apply,
)
from .core import (
    IMPLEMENTED_CANDIDATE_KINDS,
    IMPLEMENTED_CLOSEOUT_ACTION_IDS,
    IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS,
    IMPLEMENTED_CLOSEOUT_PUBLISH_MODES,
    IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS,
    IMPLEMENTED_DASHBOARD_ACTION_IDS,
    IMPLEMENTED_RISK_TIERS,
    HygieneError,
    dirty_recommendation,
    is_reparse_point,
    load_config,
    run_apply,
    run_scan,
    stable_id,
    verify_policy,
)
from .protected_check_router import RouteError, build_receipt, classify_paths


ROOT = Path(__file__).resolve().parents[2]


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


class RepoHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="repo-hygiene-test-"))
        self.repo_counter = 0

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def init_repo(self) -> Path:
        self.repo_counter += 1
        repo = self.tempdir / ("repo" if self.repo_counter == 1 else f"repo-{self.repo_counter}")
        repo.mkdir()
        git(repo, "init", "-b", "master")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Test User")
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "\n".join(
                [
                    ".claude-state/",
                    ".claude/worktrees/",
                    ".hypothesis/",
                    "**/__pycache__/",
                    "*.pyc",
                    "monitor-probe.runtime.json",
                    "platform/qt/FFmpeg/ffmpeg.exe",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        git(repo, "add", "README.md", ".gitignore")
        git(repo, "commit", "-m", "initial")
        policy_dir = repo / "tools" / "repo-hygiene"
        policy_dir.mkdir(parents=True)
        shutil.copy(ROOT / "tools" / "repo-hygiene" / "hygiene.config.json", policy_dir / "hygiene.config.json")
        shutil.copy(ROOT / "tools" / "repo-hygiene" / "POLICY.md", policy_dir / "POLICY.md")
        shutil.copy(ROOT / "tools" / "repo-hygiene" / "closeout.contract.json", policy_dir / "closeout.contract.json")
        return repo

    def signed(self, tx: dict, artifact_type: str, payload: dict, actor_id: str = "codex-test") -> dict:
        payload = json.loads(json.dumps(payload))
        payload["provenance"] = {
            "artifact_type": artifact_type,
            "actor_id": actor_id,
            "session_id": "test-session",
            "adapter_id": "unit-test",
            "key_hash": tx["state"]["provenance_key_hashes"][artifact_type],
            "signature": "",
        }
        payload["provenance"]["signature"] = sign_closeout_payload(
            tx["tx_id"],
            artifact_type,
            payload,
            tx["trusted_provenance_keys"][artifact_type],
        )
        return payload

    def record_recommendation(self, repo: Path, tx: dict, payload: dict) -> dict:
        return record_codex_recommendation(
            repo,
            tx["tx_id"],
            self.signed(tx, "codex_recommendation", payload),
            provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
        )

    def record_review(self, repo: Path, tx: dict, payload: dict, actor_id: str = "reviewer-test") -> dict:
        return record_agent_review(
            repo,
            tx["tx_id"],
            self.signed(tx, "agent_review", payload, actor_id=actor_id),
            provenance_key=tx["trusted_provenance_keys"]["agent_review"],
        )

    def approve_closeout(self, repo: Path, tx: dict, payload: dict) -> dict:
        return approve_transaction(
            repo,
            tx["tx_id"],
            self.signed(tx, "approval", payload, actor_id="approver-test"),
            tx["trusted_approval_nonce"],
            provenance_key=tx["trusted_provenance_keys"]["approval"],
            recommendation_provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            review_provenance_key=tx["trusted_provenance_keys"]["agent_review"],
        )

    def validate_apply(self, repo: Path, tx: dict) -> dict:
        return validate_transaction_apply(
            repo,
            tx["tx_id"],
            approval_provenance_key=tx["trusted_provenance_keys"]["approval"],
        )

    def test_policy_verifier_passes_for_repo_config_docs_and_tests(self) -> None:
        result = verify_policy(ROOT)
        self.assertTrue(result["ok"], result["failures"])
        self.assertIn("policy_hash", result)
        for tier in IMPLEMENTED_RISK_TIERS:
            self.assertIn(tier, load_config(ROOT)["portability"]["risk_tiers"])
        for kind in IMPLEMENTED_CANDIDATE_KINDS:
            self.assertIn(kind, load_config(ROOT)["portability"]["candidate_kinds"])
        for kind in IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS:
            self.assertIn(kind, load_config(ROOT)["portability"]["closeout_candidate_kinds"])
        for action in IMPLEMENTED_CLOSEOUT_ACTION_IDS:
            self.assertIn(action, load_config(ROOT)["portability"]["closeout_action_ids"])
        for mode in IMPLEMENTED_CLOSEOUT_PUBLISH_MODES:
            self.assertIn(mode, load_config(ROOT)["portability"]["closeout_publish_modes"])
            self.assertIn(mode, load_config(ROOT)["closeout"]["publish_modes"])
        self.assertIn("local_merge_only", IMPLEMENTED_CLOSEOUT_PUBLISH_MODES)
        self.assertIn("no_publish", IMPLEMENTED_CLOSEOUT_PUBLISH_MODES)
        for signal in IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS:
            self.assertIn(signal, load_config(ROOT)["portability"]["closeout_trigger_signal_ids"])
        self.assertIn("repo-sweep-retained-blocker", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("detached-dirty-worktree", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("protected-worktree-cleanup", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("remote-feature-branch", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("foreign_dirty_integrated_branch_prune", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("detached_dirty_preserve", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("explicit_protected_worktree_cleanup", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("resolve_conflicts_with_agent", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("agent_remediation_surface_unavailable", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("protected-target-noop-closeout", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("evidence_preserving_prune_recovery", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("delete_remote_branch", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("remote_feature_clean_integrate", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("remote_feature_prune", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("retained_blocker_auto_remediation", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("agent_remediation_queue_consumer", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("protected_target_noop_closeout", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        for action in IMPLEMENTED_DASHBOARD_ACTION_IDS:
            self.assertIn(action, load_config(ROOT)["portability"]["dashboard_action_ids"])

        self.assertTrue(load_config(ROOT)["closeout"]["auto_trigger"]["enabled"])
        self.assertFalse(load_config(ROOT)["closeout"]["allow_review_waiver"])
        self.assertIn("codex_desktop", load_config(ROOT)["closeout"]["trusted_approval_sources"])
        self.assertIn("codex_background_agent", load_config(ROOT)["closeout"]["allowed_review_sources"])
        contract = json.loads((ROOT / "tools" / "repo-hygiene" / "closeout.contract.json").read_text(encoding="utf-8"))
        self.assertIn("executor-handoff.json", contract["artifact_names"])
        self.assertIn("closeout-compare-result.json", contract["artifact_names"])
        self.assertIn("closeout-compare-result.schema.json", contract["artifact_names"])
        self.assertIn("agent-remediation-queue/*.json", contract["artifact_names"])
        self.assertIn("agent-remediation-results/*.json", contract["artifact_names"])
        self.assertIn("manual-prune/*.json", contract["artifact_names"])
        self.assertIn("manual-prune/*.bundle", contract["artifact_names"])
        self.assertTrue(contract["requires_signed_provenance"])
        self.assertTrue(contract["role_specific_provenance_keys"])
        self.assertEqual(contract["cli_secret_transport"], "environment")
        registry = load_config(ROOT)["root_registry"]
        for required in [".claude", ".claude-state", ".claude/worktrees", "tools/repo_hygiene", "tools/repo-hygiene"]:
            self.assertIn(required, registry)
        self.assertIn("osx_installer/BuildInstaller.sh", load_config(ROOT)["tracked_ignored_allowlist"])
        self.assertIn("tools/gpu/build-cuda.ps1", load_config(ROOT)["tracked_ignored_allowlist"])
        self.assertIn(".claude-state/probe.tmp", load_config(ROOT)["required_ignore_samples"]["must_be_ignored"])

    def test_ci_product_oracles_are_isolated_from_factory_bridge_failures(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        bridge_start = workflow.index("\n  factory-bridge-regressions:")
        product_start = workflow.index("\n  windows-product-oracles:")
        gui_start = workflow.index("\n  windows-gui-pilot:")
        bridge_job = workflow[bridge_start:product_start]
        product_job = workflow[product_start:gui_start]

        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("Run agent bridge PowerShell launcher regressions", bridge_job)
        self.assertIn("Verify compatible MCP runtime", bridge_job)
        self.assertNotIn("Run agent bridge PowerShell launcher regressions", product_job)
        self.assertIn("    needs: protected-check-route", product_job)
        self.assertIn("Run console_tests --check-golden", product_job)
        self.assertIn("Run pipeline_tests --check-golden (bounded shards)", product_job)
        self.assertIn("Record explicit product-oracle N/A", product_job)
        self.assertIn("outputs.product == 'true'", product_job)
        self.assertIn("    if: ${{ always() }}", product_job)
        self.assertIn("needs.protected-check-route.result != 'success'", product_job)
        self.assertIn("outputs.product != 'true'", product_job)
        self.assertIn("outputs.product != 'false'", product_job)
        self.assertIn("outputs.product == 'false'", product_job)
        self.assertNotIn("outputs.product != 'true'\n        run:", product_job)

        requirements = (ROOT / "tools" / "agent-bridge" / "requirements.in").read_text(encoding="utf-8")
        self.assertIn("mcp>=1.27.0,<2.0.0", requirements.splitlines())
        lock = (ROOT / "tools" / "agent-bridge" / "requirements-test.txt").read_text(encoding="utf-8")
        self.assertRegex(lock, r"(?m)^mcp==1\.[0-9.]+ \\\r?$")
        self.assertNotIn("mcp==2.", lock)

    def test_ci_workflow_hardening_is_fail_closed_and_coordination_aware(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        trigger_section = workflow[: workflow.index("\npermissions:")]

        self.assertIn(
            "concurrency:\n"
            "  group: tests-${{ github.workflow }}-${{ github.event_name }}-"
            "${{ github.event_name == 'workflow_dispatch' && github.run_id || github.ref }}\n"
            "  cancel-in-progress: ${{ github.event_name != 'workflow_dispatch' }}",
            workflow,
        )
        self.assertIn("  pull_request:\n", trigger_section)
        self.assertIn("  push:\n    branches:\n      - master\n", trigger_section)
        self.assertNotRegex(
            trigger_section,
            r"(?m)^\s+paths(?:-ignore)?:",
            "required PR and master CI must not be suppressible by path filtering",
        )

        expected_timeouts = {
            "protected-check-route": 10,
            "repo-hygiene-python": 45,
            "factory-bridge-regressions": 45,
            "windows-product-oracles": 120,
            "windows-gui-pilot": 60,
        }
        jobs_text = workflow[workflow.index("\njobs:") :]
        job_matches = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\r?$", jobs_text))
        self.assertEqual({match.group(1) for match in job_matches}, set(expected_timeouts))
        for index, match in enumerate(job_matches):
            end = job_matches[index + 1].start() if index + 1 < len(job_matches) else len(jobs_text)
            job = jobs_text[match.start() : end]
            self.assertIn(
                f"    timeout-minutes: {expected_timeouts[match.group(1)]}",
                job,
                f"{match.group(1)} must have an explicit bounded deadline",
            )

        expected_remote_uses = [
            ("actions/checkout", "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09", "v5"),
            ("actions/setup-python", "ece7cb06caefa5fff74198d8649806c4678c61a1", "v6"),
        ] * 5 + [
            ("actions/upload-artifact", "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", "v7"),
        ] * 3
        uses_entries = []
        for line in workflow.splitlines():
            match = re.match(r"^\s*(?:-\s+)?uses\s*:\s*(.*?)\s*$", line)
            if not match:
                continue
            raw_target = match.group(1)
            target, comment_separator, version_comment = raw_target.partition(" #")
            uses_entries.append(
                (target.strip().strip("\"'"), version_comment.strip() if comment_separator else "")
            )
        remote_uses = []
        for target, version_comment in uses_entries:
            self.assertFalse(
                target.startswith("docker://"),
                "Docker actions are forbidden absent a separately reviewed digest policy",
            )
            if target.startswith("./"):
                continue
            action, separator, revision = target.partition("@")
            self.assertEqual(separator, "@", f"remote action lacks a revision: {target}")
            self.assertRegex(
                action,
                r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$",
                f"unrecognized non-local uses target: {target}",
            )
            self.assertRegex(revision, r"^[0-9a-f]{40}$", f"{action} must use a full immutable SHA")
            remote_uses.append((action, revision, version_comment.strip()))
        self.assertEqual(sorted(remote_uses), sorted(expected_remote_uses))

        checkout_revision = "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
        checkout_blocks = re.findall(
            rf"(?m)^\s*- uses: actions/checkout@{checkout_revision} # v5\r?\n"
            r"\s+with:\r?\n"
            r"\s+persist-credentials: false\s*$",
            workflow,
        )
        self.assertEqual(len(checkout_blocks), 5)

        bridge_start = workflow.index("\n  factory-bridge-regressions:")
        product_start = workflow.index("\n  windows-product-oracles:")
        bridge_job = workflow[bridge_start:product_start]
        self.assertIn("Run coordination and self-healing guardrails", bridge_job)
        self.assertIn("tools\\coordination\\test_coordination_guardrails.py", bridge_job)
        self.assertIn("tests\\coordination", bridge_job)
        self.assertIn("tools\\agent-bridge\\requirements-test.txt", bridge_job)
        self.assertNotRegex(bridge_job, r"pip install [\"']pytest")

        product_job = workflow[product_start : workflow.index("\n  windows-gui-pilot:")]
        self.assertIn("    needs: protected-check-route", product_job)
        self.assertIn("    if: ${{ always() }}", product_job)
        self.assertIn("needs.protected-check-route.result != 'success'", product_job)
        self.assertIn("if ($head -ne $env:EXPECTED_ROUTE_HEAD)", product_job)
        self.assertIn("EXPLICIT_NA_PROVIDER_CONTROL_ONLY", product_job)
        self.assertIn("MANUAL_DISPATCH_RUN_REAL_ORACLES", product_job)
        self.assertIn("outputs.product == 'false'", product_job)
        artifact_step_start = product_job.index("\n      - name: Upload pipeline crash forensics")
        next_step = product_job.find("\n      - ", artifact_step_start + 1)
        artifact_step = product_job[artifact_step_start : next_step if next_step != -1 else len(product_job)]
        self.assertIn(
            "\n        if: always() && needs.protected-check-route.outputs.product == 'true'",
            artifact_step,
        )
        self.assertIn("\n        uses: actions/upload-artifact@", artifact_step)
        self.assertIn("\n          path: |", artifact_step)
        self.assertIn("${{ runner.temp }}\\pipeline-golden-actual.json", artifact_step)

        route_start = workflow.index("\n  protected-check-route:")
        repo_start = workflow.index("\n  repo-hygiene-python:")
        route_job = workflow[route_start:repo_start]
        self.assertIn("tools/repo_hygiene/protected_check_router.py", route_job)
        self.assertIn("fetch-depth: 0", route_job)
        self.assertIn("if-no-files-found: error", route_job)
        self.assertIn("retention-days: 30", route_job)
        self.assertIn("if ('${{ github.event_name }}' -eq 'workflow_dispatch')", route_job)
        self.assertIn("$forceReal = @('--force-real')", route_job)

        gui_start = workflow.index("\n  windows-gui-pilot:")
        gui_job = workflow[gui_start:]
        self.assertIn("    needs: protected-check-route", gui_job)
        self.assertIn("    if: ${{ always() }}", gui_job)
        self.assertIn("needs.protected-check-route.result != 'success'", gui_job)
        self.assertIn("outputs.gui != 'true'", gui_job)
        self.assertIn("outputs.gui != 'false'", gui_job)
        self.assertIn("outputs.gui == 'false'", gui_job)
        self.assertIn("if ($head -ne $env:EXPECTED_ROUTE_HEAD)", gui_job)
        self.assertIn("EXPLICIT_NA_PROVIDER_CONTROL_ONLY", gui_job)
        self.assertIn("MANUAL_DISPATCH_RUN_REAL_ORACLES", gui_job)

        required_display_names = {
            "Repo Hygiene Python (${{ matrix.os }})",
            "Factory Bridge Regressions",
            "Windows Product Oracles",
            "Windows GUI Pilot",
        }
        observed_names = set(re.findall(r"(?m)^    name: (.+)$", jobs_text))
        self.assertTrue(required_display_names.issubset(observed_names))

    def test_protected_check_router_is_conservative_and_provider_specific(self) -> None:
        provider = classify_paths(
            [
                "tools/provider_control/mlv_lane_supervisor.py",
                ".github/workflows/provider-control-candidate.yml",
                ".github/requirements/provider-control.txt",
            ]
        )
        self.assertFalse(provider.product)
        self.assertFalse(provider.gui)
        self.assertEqual(provider.reason, "EXPLICIT_NA_PROVIDER_CONTROL_ONLY")

        for changed in (
            ["src/mlv/video_mlv.c"],
            ["platform/qt/MainWindow.cpp"],
            ["docs/unknown-surface.md"],
            [".github/workflows/tests.yml"],
        ):
            with self.subTest(changed=changed):
                route = classify_paths(changed)
                self.assertTrue(route.product)
                self.assertTrue(route.gui)

        with self.assertRaises(RouteError):
            classify_paths([])
        with self.assertRaises(RouteError):
            classify_paths(["tools/provider_control/../src/escape.c"])

    def test_pipeline_golden_sync_is_bound_to_frozen_known_good_output(self) -> None:
        golden = json.loads(
            (ROOT / "tests" / "fixtures" / "golden" / "pipeline_hashes.json").read_text(
                encoding="utf-8"
            )
        )
        phase3 = json.loads(
            (ROOT / "tests" / "fixtures" / "phase3_baselines" / "golden.json").read_text(
                encoding="utf-8"
            )
        )
        receipt = json.loads(
            (
                ROOT
                / "receipts"
                / "pipeline-golden-sync-deterministic-dither-20260819.json"
            ).read_text(encoding="utf-8")
        )

        canonical = json.dumps(
            golden,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        canonical_sha256 = hashlib.sha256(canonical).hexdigest()
        self.assertEqual(len(golden), 15)
        self.assertEqual(receipt["schema"], "mlv-app/pipeline-golden-sync-receipt/v1")
        self.assertEqual(receipt["disposition"], "STALE_ORACLE_SYNC")
        self.assertEqual(receipt["producerContract"]["keyCount"], len(golden))
        self.assertEqual(receipt["producerContract"]["testCount"], 7)
        self.assertEqual(receipt["producerContract"]["assertionCount"], 75)
        self.assertEqual(
            receipt["hostedCandidateArtifact"]["head"],
            "f5f8964c2d54df263fd325e80915c8cf799d18c6",
        )
        self.assertEqual(
            receipt["hostedCandidateArtifact"]["tree"],
            "5909be4d4c063a7da0c6b1147ac5b825e65db78b",
        )
        self.assertEqual(
            receipt["knownGoodArtifact"]["canonicalActualSha256"],
            canonical_sha256,
        )
        self.assertEqual(
            receipt["hostedCandidateArtifact"]["canonicalActualSha256"],
            canonical_sha256,
        )
        self.assertEqual(
            receipt["equivalence"]["updatedGoldenCanonicalSha256"],
            canonical_sha256,
        )
        self.assertEqual(
            receipt["knownGoodArtifact"]["actualJsonSha256"],
            receipt["hostedCandidateArtifact"]["actualJsonSha256"],
        )
        self.assertTrue(receipt["equivalence"]["knownGoodAndHostedActualByteIdentical"])
        self.assertEqual(receipt["equivalence"]["semanticDeltaCount"], 0)

        phase3_frames = {
            frame["frame"]: frame["sha256"]
            for frame in phase3["clips"][0]["frames"]
        }
        self.assertEqual(golden["tiny_dual_iso.full16.frame0"], phase3_frames[0])
        self.assertEqual(golden["tiny_dual_iso.full16.frame1"], phase3_frames[1])
        self.assertFalse(receipt["scope"]["productSourceChanged"])
        self.assertFalse(receipt["scope"]["runtimeBehaviorChanged"])
        self.assertFalse(receipt["scope"]["providerAuthority"])
        self.assertFalse(receipt["scope"]["reblessAuthority"])
        self.assertEqual(receipt["scope"]["automaticLaunchGate"], "CLOSED")
        self.assertTrue(receipt["scope"]["humanBeforeAfterReviewRequired"])

    def test_autonomous_golden_authority_is_fail_closed_and_recuses_proposer(self) -> None:
        policy_path = (
            ROOT / "tools" / "repo_hygiene" / "autonomous-golden-authority.json"
        )
        schema_path = (
            ROOT
            / "tools"
            / "repo_hygiene"
            / "autonomous-golden-authority.schema.json"
        )
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        validator.validate(policy)

        self.assertFalse(policy["ownerDelegation"]["routineHumanApprovalRequired"])
        self.assertEqual(
            policy["ownerDelegation"]["defaultOnAmbiguity"],
            "REJECT_PRESERVE_BASELINE",
        )
        self.assertTrue(policy["authorityBoundary"]["implementationProposerRecused"])
        self.assertFalse(policy["authorityBoundary"]["providerRuntimeAuthority"])
        self.assertEqual(policy["authorityBoundary"]["automaticLaunchGate"], "CLOSED")
        self.assertTrue(
            policy["independence"]["modelConsensusCannotOverrideOutputFailure"]
        )
        self.assertGreaterEqual(
            policy["decisionClasses"]["B_MECHANICALLY_PREDICTED"][
                "independentHostedRuns"
            ],
            2,
        )
        class_c = policy["decisionClasses"]["C_AESTHETIC_DEFAULT_OR_AMBIGUOUS"]
        self.assertEqual(class_c["minimumSignerClasses"], 5)
        self.assertTrue(class_c["unanimous"])
        self.assertTrue(class_c["requiresStandingBoundedPolicy"])
        self.assertEqual(class_c["whenPolicyMissing"], "REJECT_PRESERVE_BASELINE")
        self.assertTrue(
            policy["promotion"]["promotionCommitMayChangeOnlyGoldenAndReceipt"]
        )

        hostile_mutations = (
            (
                "routine human gate restored",
                ("ownerDelegation", "routineHumanApprovalRequired"),
                True,
            ),
            (
                "ambiguity auto-approved",
                ("ownerDelegation", "defaultOnAmbiguity"),
                "AUTO_APPROVE",
            ),
            (
                "proposer self-approval",
                ("authorityBoundary", "implementationProposerRecused"),
                False,
            ),
            (
                "provider authority smuggled",
                ("authorityBoundary", "providerRuntimeAuthority"),
                True,
            ),
            (
                "model vote overrides output",
                ("independence", "modelConsensusCannotOverrideOutputFailure"),
                False,
            ),
            (
                "class C skips standing policy",
                (
                    "decisionClasses",
                    "C_AESTHETIC_DEFAULT_OR_AMBIGUOUS",
                    "requiresStandingBoundedPolicy",
                ),
                False,
            ),
            (
                "promotion mixes product",
                ("promotion", "promotionCommitMayChangeOnlyGoldenAndReceipt"),
                False,
            ),
        )
        for label, path, value in hostile_mutations:
            with self.subTest(label=label):
                hostile = json.loads(json.dumps(policy))
                target = hostile
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.assertTrue(list(validator.iter_errors(hostile)))

        docs = (ROOT / "docs" / "autonomous-golden-authority.md").read_text(
            encoding="utf-8"
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("receipts, not approval requests", docs)
        self.assertRegex(docs, r"cannot\s+vote a failing output gate green")
        self.assertIn("docs/autonomous-golden-authority.md", agents)

    def test_protected_check_receipt_binds_exact_git_diff_and_refuses_divergence(self) -> None:
        repo = self.init_repo()
        base = git(repo, "rev-parse", "HEAD").stdout.strip()
        provider = repo / "tools" / "provider_control" / "candidate.py"
        provider.parent.mkdir(parents=True)
        provider.write_text("CLOSED = True\n", encoding="utf-8")
        git(repo, "add", "tools/provider_control/candidate.py")
        git(repo, "commit", "-m", "provider-only")
        head = git(repo, "rev-parse", "HEAD").stdout.strip()

        receipt = build_receipt(repo, base, head)
        self.assertEqual(receipt["schema"], "mlv-protected-check-route/v1")
        self.assertFalse(receipt["authority"])
        self.assertEqual(receipt["git"]["base"], base)
        self.assertEqual(receipt["git"]["baseTip"], base)
        self.assertEqual(receipt["git"]["head"], head)
        self.assertRegex(receipt["git"]["tree"], r"^[0-9a-f]{40}$")
        self.assertRegex(receipt["git"]["diffSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(receipt["changedPaths"], ["tools/provider_control/candidate.py"])
        self.assertFalse(receipt["route"]["productOraclesRequired"])
        self.assertFalse(receipt["route"]["forcedReal"])
        self.assertEqual(
            receipt["route"]["productOracleCredit"],
            "EXPLICIT_NA_NON_PRODUCT_DIFF",
        )

        forced = build_receipt(repo, base, head, force_real=True)
        self.assertTrue(forced["route"]["forcedReal"])
        self.assertTrue(forced["route"]["productOraclesRequired"])
        self.assertTrue(forced["route"]["guiPilotRequired"])
        self.assertEqual(forced["route"]["reason"], "MANUAL_DISPATCH_RUN_REAL_ORACLES")
        self.assertEqual(forced["route"]["productOracleCredit"], "RUN_REQUIRED")

        side = git(repo, "rev-parse", "HEAD^").stdout.strip()
        with self.assertRaises(RouteError):
            build_receipt(repo, head, side)

        divergent = repo / "divergent.txt"
        git(repo, "checkout", "-b", "divergent", base)
        divergent.write_text("base moved\n", encoding="utf-8")
        git(repo, "add", "divergent.txt")
        git(repo, "commit", "-m", "divergent base tip")
        base_tip = git(repo, "rev-parse", "HEAD").stdout.strip()
        diverged_receipt = build_receipt(repo, base_tip, head)
        self.assertEqual(diverged_receipt["git"]["baseTip"], base_tip)
        self.assertEqual(diverged_receipt["git"]["base"], base)
        self.assertEqual(
            diverged_receipt["changedPaths"],
            ["tools/provider_control/candidate.py"],
        )

    def test_release_playback_profile_wrapper_pins_windows_qpa(self) -> None:
        script = ROOT / "tools" / "profiling" / "run-release-playback-profile.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn('$env:QT_QPA_PLATFORM = "windows"', text)
        self.assertIn("QT_QPA_PLATFORM_PLUGIN_PATH", text)
        self.assertIn("qwindows.dll", text)
        self.assertIn("platform\\qt\\build-release\\release\\MLVApp.exe", text)
        self.assertIn('[Alias("Input")]', text)
        self.assertIn("[string]$ClipPath", text)
        self.assertNotIn("[string]$Input", text)
        self.assertNotIn('$env:QT_QPA_PLATFORM = "offscreen"', text)

    def test_policy_verifier_catches_runtime_auto_trigger_signal_drift(self) -> None:
        repo = self.init_repo()
        package_dir = repo / "tools" / "repo_hygiene"
        package_dir.mkdir(parents=True)
        (package_dir / "core.py").write_text("# verifier sample\n", encoding="utf-8")
        shutil.copy(ROOT / "tools" / "repo_hygiene" / "test_repo_hygiene.py", package_dir / "test_repo_hygiene.py")
        config_path = repo / "tools" / "repo-hygiene" / "hygiene.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["closeout"]["auto_trigger"]["signals"] = ["dirty_current_work"]
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        result = verify_policy(repo)
        self.assertFalse(result["ok"])
        self.assertTrue(any("closeout.auto_trigger.signals" in failure for failure in result["failures"]))
        config["closeout"]["auto_trigger"]["signals"] = load_config(ROOT)["closeout"]["auto_trigger"]["signals"]
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        contract_path = repo / "tools" / "repo-hygiene" / "closeout.contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["states"] = ["approved"]
        contract_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
        result = verify_policy(repo)
        self.assertFalse(result["ok"])
        self.assertTrue(any("contract.states" in failure for failure in result["failures"]))

    def test_candidate_ids_are_stable_and_do_not_require_raw_paths(self) -> None:
        first = stable_id("orphan-dir", {"path": "C:/repo/.claude/worktrees/tmp"})
        second = stable_id("orphan-dir", {"path": "C:/repo/.claude/worktrees/tmp"})
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("orphan-dir:"))
        self.assertNotIn("C:/repo", first)

    def test_reparse_helper_returns_boolean_on_platform_branch(self) -> None:
        self.assertIsInstance(is_reparse_point(ROOT), bool)

    def test_dirty_file_triage_recommends_generated_and_commit_groups(self) -> None:
        config = load_config(ROOT)
        generated = dirty_recommendation(".claude-state/profiling/run.json", config, [], "codex/hygiene")
        source = dirty_recommendation("tools/repo_hygiene/core.py", config, ["tools/repo_hygiene/core.py"], "codex/hygiene")
        self.assertEqual(generated[0], "ignore/generated")
        self.assertGreaterEqual(generated[1], 0.8)
        self.assertIn(source[0], {"commit", "split"})
        self.assertGreaterEqual(source[1], 0.65)

    def test_scan_emits_structured_artifacts_and_dirty_group_candidate(self) -> None:
        repo = self.init_repo()
        (repo / "tools" / "repo_hygiene").mkdir(parents=True)
        (repo / "tools" / "repo_hygiene" / "core.py").write_text("print('dirty')\n", encoding="utf-8")
        result = run_scan(repo)
        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "facts.json").exists())
        self.assertTrue((run_dir / "plan.json").exists())
        self.assertTrue((run_dir / "result.json").exists())
        self.assertTrue((run_dir / "summary.md").exists())
        plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["schema_version"], "1.0")
        self.assertIn("policy_hash", plan)
        dirty = [c for c in plan["candidates"] if c["kind"] == "dirty-group"]
        self.assertTrue(dirty)
        self.assertIn("evidence_hash", dirty[0])
        self.assertEqual(dirty[0]["decision"], "retain")

    def test_cli_scan_works_from_path_with_spaces_and_bang(self) -> None:
        repo = self.init_repo()
        spaced = self.tempdir / "path with space !"
        repo.rename(spaced)
        cli = ROOT / "tools" / "repo-hygiene" / "hygiene.py"
        result = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--repo-root",
                str(spaced),
                "scan",
                "--no-write-artifacts",
                "--trust-local-base",
                "--json",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertIn(result.returncode, {0, 1}, result.stderr)
        self.assertIn('"schema_version"', result.stdout)
        self.assertFalse((spaced / ".claude-state" / "repo-hygiene").exists())

    def test_orphan_source_like_directory_is_manual_only(self) -> None:
        repo = self.init_repo()
        orphan = repo / ".claude" / "worktrees" / "orphan-source"
        orphan.mkdir(parents=True)
        (orphan / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
        scan = run_scan(repo)
        candidate = next(
            c for c in scan["plan"]["candidates"] if c["kind"] == "orphan-dir" and c.get("path") == str(orphan.resolve())
        )
        self.assertEqual(candidate["risk_tier"], "R4")
        self.assertNotIn("orphan_quarantine", candidate["allowed_actions"])
        self.assertIn("source-like", candidate["never_allowed_reason"])

    def test_generated_report_apply_uses_candidate_id_and_revalidates_evidence(self) -> None:
        repo = self.init_repo()
        config = load_config(repo)
        config_path = repo / "tools" / "repo-hygiene" / "hygiene.config.json"
        config.pop("policy_hash", None)
        config["thresholds"]["generated_run_retention_days"] = 0
        config["thresholds"]["generated_run_keep_latest"] = 0
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        old_run = repo / ".claude-state" / "repo-hygiene" / "runs" / "old-run"
        old_run.mkdir(parents=True)
        (old_run / "summary.md").write_text("old\n", encoding="utf-8")
        os.utime(old_run, (time.time() - 3600, time.time() - 3600))
        scan = run_scan(repo)
        candidate = next(
            c for c in scan["plan"]["candidates"] if c["kind"] == "generated-report" and c.get("path") == str(old_run.resolve())
        )
        with self.assertRaises(HygieneError):
            run_apply(
                repo,
                candidate_id=candidate["id"],
                action_id="repo_hygiene_prune_old_runs",
            )
        (old_run / "extra.txt").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(HygieneError):
            run_apply(
                repo,
                candidate_id=candidate["id"],
                action_id="repo_hygiene_prune_old_runs",
                expected_evidence_hash=candidate["evidence_hash"],
            )
        scan = run_scan(repo)
        candidate = next(
            c for c in scan["plan"]["candidates"] if c["kind"] == "generated-report" and c.get("path") == str(old_run.resolve())
        )
        result = run_apply(
            repo,
            candidate_id=candidate["id"],
            action_id="repo_hygiene_prune_old_runs",
            expected_evidence_hash=candidate["evidence_hash"],
        )
        self.assertEqual(result["result"]["status"], "applied")
        self.assertFalse(old_run.exists())

    def test_apply_mutex_blocks_concurrent_mutation(self) -> None:
        repo = self.init_repo()
        lock = repo / ".claude-state" / "repo-hygiene" / "apply.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text("busy", encoding="utf-8")
        with self.assertRaises(HygieneError):
            run_apply(repo, candidate_id="generated-report:none", action_id="repo_hygiene_prune_old_runs")

    def test_branch_delete_archives_before_deleting(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "topic")
        (repo / "topic.txt").write_text("topic\n", encoding="utf-8")
        git(repo, "add", "topic.txt")
        git(repo, "commit", "-m", "topic")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "topic", "-m", "merge topic")
        scan = run_scan(repo, trust_local_base=True)
        candidate = next(c for c in scan["plan"]["candidates"] if c["kind"] == "branch" and c["evidence"]["branch"]["name"] == "topic")
        result = run_apply(
            repo,
            candidate_id=candidate["id"],
            action_id="branch_archive_delete",
            expected_evidence_hash=candidate["evidence_hash"],
            manual_override=True,
            trust_local_base=True,
        )
        self.assertEqual(result["result"]["status"], "applied")
        self.assertTrue(result["result"]["commands_invoked"])
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "topic", check=False).returncode, 0)
        archive_ref = result["result"]["outcomes"][0]["archive_ref"]
        self.assertEqual(git(repo, "rev-parse", "--verify", archive_ref).returncode, 0)

    def test_stash_promote_creates_recovery_branch_without_drop(self) -> None:
        repo = self.init_repo()
        (repo / "code.py").write_text("print('stash')\n", encoding="utf-8")
        git(repo, "add", "code.py")
        git(repo, "stash", "push", "-m", "codex-temp code")
        scan = run_scan(repo, trust_local_base=True)
        candidate = next(c for c in scan["plan"]["candidates"] if c["kind"] == "stash")
        result = run_apply(
            repo,
            candidate_id=candidate["id"],
            action_id="stash_promote",
            expected_evidence_hash=candidate["evidence_hash"],
            manual_override=True,
            trust_local_base=True,
        )
        self.assertEqual(result["result"]["status"], "applied")
        self.assertIn("hygiene/stash/", result["result"]["outcomes"][0]["branch"])
        self.assertIn("stash@{0}", git(repo, "stash", "list").stdout)

    def approved_closeout(self, repo: Path) -> tuple[dict, str]:
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        packet = json.loads((Path(tx["tx_dir"]) / "decision-packet.json").read_text(encoding="utf-8"))
        unit_id = packet["dirty_commit_units"][0]["id"]
        recommendation = {
            "tx_id": tx["tx_id"],
            "decision_packet_hash": tx["decision_packet_hash"],
            "summary": "Commit the selected commit unit, keep cleanup recommendations symbolic, then publish manually.",
            "actions": [{"action_id": "commit_unit_commit", "commit_unit_id": unit_id}],
            "cleanup_actions": [],
            "residual_risks": [],
        }
        recorded = self.record_recommendation(repo, tx, recommendation)
        for reviewer_id in ["stranger-a", "stranger-b"]:
            self.record_review(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": reviewer_id,
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                    "rationale": "Recommendation is data-only and references symbolic action IDs.",
                },
        )
        self.assertFalse((Path(tx["tx_dir"]) / "trusted-approval-nonce.json").exists())
        self.assertTrue((Path(tx["tx_dir"]) / "trusted-approval-nonce.public.json").exists())
        self.assertTrue((Path(tx["tx_dir"]) / "trusted-provenance-key.public.json").exists())
        persisted_state = json.loads((Path(tx["tx_dir"]) / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("trusted_provenance_key", persisted_state)
        self.assertNotIn("trusted_provenance_keys", persisted_state)
        self.approve_closeout(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "recommendation_hash": recorded["recommendation_hash"],
                "approval_source": "local_interactive_cli",
                "approved_action_ids": ["commit_unit_commit"],
                "approved_commit_unit_ids": [unit_id],
                "approved_candidate_ids": [],
                "risk_acceptance": "Local test approves the data-only closeout recommendation.",
            },
        )
        return tx, unit_id

    def test_closeout_transaction_requires_data_only_codex_review_and_readonly_strangers(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        packet = json.loads((Path(tx["tx_dir"]) / "decision-packet.json").read_text(encoding="utf-8"))
        self.assertEqual(tx["state"]["state"], "awaiting_codex_review")
        self.assertTrue(packet["dirty_commit_units"])
        self.assertIn("closeout-transaction", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("commit-unit", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("merge-readiness", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("publish-target", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("prune-after-publish", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("commit_unit_commit", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("publish_pr", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("publish_direct_branch", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("local_merge", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("prune_after_publish", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [{"action_id": "commit_unit_commit", "command": "git commit -am bad"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [{"action_id": "ask", "note": "unknown nested key"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [{"action_id": "commit_unit_commit", "commit_unit_id": "commit-unit:missing"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [],
                    "cleanup_actions": [{"action_id": "worktree_remove", "candidate_id": "worktree:missing"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [{"action_id": "publish_pr"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )

    def test_closeout_approval_and_apply_validation_are_revalidated(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        status = transaction_status(repo, tx["tx_id"])
        self.assertEqual(status["state"]["state"], "approved")
        self.assertEqual(status["review_count"], 2)
        validation = self.validate_apply(repo, tx)
        self.assertEqual(validation["status"], "validated")
        self.assertEqual(validation["preflight_results"]["candidate_ids_revalidated"], [])
        handoff = json.loads((Path(tx["tx_dir"]) / "executor-handoff.json").read_text(encoding="utf-8"))
        self.assertEqual(handoff["boundary"], "validation_only_not_completion")
        self.assertIn("raw shell", handoff["forbidden_inputs"])
        explained = transaction_status(repo, tx["tx_id"], explain=True)
        self.assertIn("validation_boundary", explained["explain"])
        events = [
            json.loads(line)
            for line in (Path(tx["tx_dir"]) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(all("event_hash" in event for event in events))
        for previous, current in zip(events, events[1:]):
            self.assertEqual(current["previous_event_hash"], previous["event_hash"])

    def test_closeout_apply_validation_blocks_when_dirty_file_changes(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        (repo / "work.py").write_text("print('changed after approval')\n", encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

    def test_closeout_apply_validation_blocks_when_event_chain_is_tampered(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        events_path = Path(tx["tx_dir"]) / "events.jsonl"
        lines = events_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["event"] = "tampered"
        lines[0] = json.dumps(first, sort_keys=True)
        events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

    def test_closeout_apply_validation_blocks_when_accepted_review_changes_or_disappears(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        review_file = sorted(Path(tx["tx_dir"]).glob("agent-review-*.json"))[0]
        review = json.loads(review_file.read_text(encoding="utf-8"))
        review["score"] = 1
        review_file.write_text(json.dumps(review, indent=2), encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

        shutil.rmtree(repo, ignore_errors=True)
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        review_file = sorted(Path(tx["tx_dir"]).glob("agent-review-*.json"))[0]
        review_file.unlink()
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

    def test_closeout_apply_validation_blocks_when_approval_manifest_is_rewritten(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        tx_dir = Path(tx["tx_dir"])
        for path in tx_dir.glob("agent-review-*.json"):
            path.unlink()
        approval_path = tx_dir / "approval.json"
        state_path = tx_dir / "state.json"
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        approval["accepted_review_hashes"] = []
        approval.pop("approval_hash", None)
        approval["approval_hash"] = tx_hash(approval)
        state["accepted_review_hashes"] = []
        state["approval_hash"] = approval["approval_hash"]
        approval_path.write_text(json.dumps(approval, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

    def test_closeout_readonly_review_and_trusted_approval_are_enforced(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        recorded = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "actions": [{"action_id": "ask"}],
            },
        )
        with self.assertRaises(HygieneError):
            record_agent_review(
                repo,
                tx["tx_id"],
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "unsigned",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                },
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        with self.assertRaises(HygieneError):
            record_agent_review(
                repo,
                tx["tx_id"],
                self.signed(tx, "agent_review", {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "write-capable",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": True},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                }),
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        with self.assertRaises(HygieneError):
            record_agent_review(
                repo,
                tx["tx_id"],
                self.signed(tx, "agent_review", {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "malformed",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": "not-an-int",
                    "score": 10,
                    "approve": True,
                }),
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        with self.assertRaises(HygieneError):
            record_agent_review(
                repo,
                tx["tx_id"],
                self.signed(tx, "agent_review", {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "malformed-approve",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": "not-bool",
                }),
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        recorded_review = self.record_review(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "reviewer_id": "../escape",
                "recommendation_hash_reviewed": recorded["recommendation_hash"],
                "review_source": "codex_background_agent",
                "reviewer_mode": "read_only",
                "tool_capabilities": {"write_tools_enabled": False},
                "write_attempts": 0,
                "score": 10,
                "approve": True,
            },
        )
        self.assertFalse((Path(tx["tx_dir"]).parent / "escape.json").exists())
        self.assertTrue(any(path.name.startswith("agent-review-") for path in Path(tx["tx_dir"]).glob("agent-review-*.json")))
        self.assertEqual(recorded_review["reviewer_id"], "../escape")
        nonce = tx["trusted_approval_nonce"]
        with self.assertRaises(HygieneError):
            approve_transaction(
                repo,
                tx["tx_id"],
                self.signed(tx, "approval", {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "untrusted_dashboard",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                    "review_waiver": {"risk_acceptance": "test waiver"},
                }),
                nonce,
                provenance_key=tx["trusted_provenance_keys"]["approval"],
                recommendation_provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
                review_provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        with self.assertRaises(HygieneError):
            approve_transaction(
                repo,
                tx["tx_id"],
                self.signed(tx, "approval", {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                }),
                nonce,
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
                recommendation_provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
                review_provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )

    def test_closeout_disabled_waiver_cannot_override_blocking_review(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        recorded = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "actions": [{"action_id": "ask"}],
            },
        )
        for reviewer_id, score, approve in [("stranger-a", 10, True), ("stranger-b", 2, False)]:
            self.record_review(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": reviewer_id,
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": score,
                    "approve": approve,
                },
            )
        with self.assertRaises(HygieneError):
            self.approve_closeout(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                    "review_waiver": {"risk_acceptance": "try to override blocker"},
                },
            )

    def test_closeout_required_review_count_is_config_driven(self) -> None:
        repo = self.init_repo()
        config_path = repo / "tools" / "repo-hygiene" / "hygiene.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["closeout"]["required_read_only_reviewers"] = 3
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        recorded = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "actions": [{"action_id": "ask"}],
            },
        )
        for reviewer_id in ["stranger-a", "stranger-b"]:
            self.record_review(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": reviewer_id,
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                },
            )
        with self.assertRaises(HygieneError):
            self.approve_closeout(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                },
            )

    def test_closeout_approval_counts_only_event_recorded_review_artifacts(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        recorded = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "actions": [{"action_id": "ask"}],
            },
        )
        self.record_review(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "reviewer_id": "stranger-a",
                "recommendation_hash_reviewed": recorded["recommendation_hash"],
                "review_source": "codex_background_agent",
                "reviewer_mode": "read_only",
                "tool_capabilities": {"write_tools_enabled": False},
                "write_attempts": 0,
                "score": 10,
                "approve": True,
            },
        )
        fake = {
            "schema_version": "1.0",
            "created_at": "2026-05-04T00:00:00+00:00",
            **self.signed(
                tx,
                "agent_review",
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "stranger-b",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                },
            ),
        }
        fake["review_hash"] = tx_hash(fake)
        (Path(tx["tx_dir"]) / "agent-review-fake.json").write_text(json.dumps(fake, indent=2), encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.approve_closeout(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                },
            )

    def test_closeout_reviews_must_match_current_recommendation_hash(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        first = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "summary": "First recommendation.",
                "actions": [{"action_id": "ask"}],
            },
        )
        for reviewer_id in ["stranger-a", "stranger-b"]:
            self.record_review(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": reviewer_id,
                    "recommendation_hash_reviewed": first["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                },
            )
        second = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "summary": "Replacement recommendation.",
                "actions": [{"action_id": "ask"}],
            },
        )
        with self.assertRaises(HygieneError):
            self.approve_closeout(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": second["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                },
            )

    def test_closeout_publish_policy_is_enforced_from_config(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "topic")
        (repo / "feature.py").write_text("print('feature')\n", encoding="utf-8")
        git(repo, "add", "feature.py")
        git(repo, "commit", "-m", "feature")
        with self.assertRaises(HygieneError):
            open_transaction(repo, publish_mode="pr_only", publish_remote="origin")
        with self.assertRaises(HygieneError):
            open_transaction(repo, publish_mode="direct_push_branch", publish_remote="fork")
        git(repo, "checkout", "-b", "codex/publish-ready")
        ok = open_transaction(repo, publish_mode="pr_only", publish_remote="fork")
        self.assertEqual(ok["state"]["publish_mode"], "pr_only")

    def test_closeout_auto_trigger_opens_transaction_for_clean_feature_branch(self) -> None:
        repo = self.init_repo()
        git(
            repo,
            "add",
            "tools/repo-hygiene/hygiene.config.json",
            "tools/repo-hygiene/POLICY.md",
            "tools/repo-hygiene/closeout.contract.json",
        )
        git(repo, "commit", "-m", "add hygiene policy")
        git(repo, "checkout", "-b", "codex/ready")
        (repo / "feature.py").write_text("print('ready')\n", encoding="utf-8")
        git(repo, "add", "feature.py")
        git(repo, "commit", "-m", "ready feature")
        result = evaluate_closeout_triggers(repo, open_if_triggered=True)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["opened_transaction"]["state"]["state"], "awaiting_codex_review")
        latest = json.loads((repo / ".claude-state" / "repo-hygiene" / "triggers" / "latest.json").read_text(encoding="utf-8"))
        self.assertNotIn("trusted_approval_nonce", latest["opened_transaction"])
        self.assertNotIn("trusted_provenance_keys", latest["opened_transaction"])
        self.assertIn("clean_feature_branch_ready_to_publish", [signal["id"] for signal in result["signals"]])
        self.assertIn("dirty_current_work", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("dirty_generated_only", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("clean_feature_branch_ready_to_publish", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("hygiene_cleanup_recommendations", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        second = evaluate_closeout_triggers(repo, open_if_triggered=True)
        self.assertFalse(second["triggered"])
        self.assertEqual(len(second["active_transactions"]), 1)

    def test_config_paths_must_stay_repo_relative_and_state_under_claude_state(self) -> None:
        repo = self.init_repo()
        config_path = repo / "tools" / "repo-hygiene" / "hygiene.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["state_root"] = "../outside"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        with self.assertRaises(HygieneError):
            load_config(repo)

    def test_load_bearing_registered_worktree_is_not_removal_candidate(self) -> None:
        repo = self.init_repo()
        wt = repo / ".claude" / "worktrees" / "registered"
        git(repo, "worktree", "add", str(wt), "-b", "registered-topic")
        scan = run_scan(repo, trust_local_base=True)
        candidate = next(c for c in scan["plan"]["candidates"] if c["kind"] == "worktree" and c.get("path") == str(wt.resolve()))
        self.assertEqual(candidate["risk_tier"], "R4")
        self.assertNotIn("worktree_remove", candidate["allowed_actions"])
        self.assertEqual(candidate["never_allowed_reason"], "load-bearing worktree root")

    def test_reparse_or_symlink_orphan_is_refused_before_inventory(self) -> None:
        repo = self.init_repo()
        link = repo / ".claude" / "worktrees" / "linked"
        link.mkdir(parents=True)
        (link / "would-be-skipped.cpp").write_text("int outside;\n", encoding="utf-8")

        def fake_reparse(path: Path) -> bool:
            return Path(path).name == "linked"

        with patch("tools.repo_hygiene.core.is_reparse_point", side_effect=fake_reparse):
            scan = run_scan(repo)
        candidate = next(c for c in scan["plan"]["candidates"] if c["kind"] == "orphan-dir" and c["title"].endswith(": linked"))
        self.assertEqual(candidate["risk_tier"], "R4")
        self.assertNotIn("orphan_quarantine", candidate["allowed_actions"])
        self.assertTrue(candidate["evidence"]["is_reparse_point"])
        self.assertTrue(candidate["evidence"]["inventory"]["refused_reparse_point"])


if __name__ == "__main__":
    unittest.main()
