import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from .brokered_closeout import (
    bootstrap_response_broker_manifest,
    broker_contract,
    checkpoint_owned_work,
    complete_work_block,
    detect_work_block,
    finalize_action_id,
    finalize_candidate_id,
    finalize_evidence,
    finalize_work_block,
    load_closeout_config,
    plan_dirty_split_candidates,
    apply_dirty_split_candidate,
    preserve_owned_dirty_split,
    record_review_approval,
    repair_eligibility,
    repo_sweep,
    repo_sweep_tuple,
    stable_hash,
    start_work_block,
)


ROOT = Path(__file__).resolve().parents[2]


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def deep_update(base: dict, updates: dict) -> dict:
    result = json.loads(json.dumps(base))
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_update(result[key], value)
        else:
            result[key] = value
    return result


class BrokeredCloseoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="brokered-closeout-test-"))
        self.repo_counter = 0

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def write_config(self, repo: Path, updates: dict | None = None) -> None:
        config = {
            "stateRoot": ".claude-state/closeout",
            "git": {
                "targetBranch": "master",
                "remote": "origin",
                "allowLocalOnly": True,
                "protectedBranches": ["master", "main"],
                "featureBranchPatterns": ["codex/*", "feature/*"],
                "fetchBeforeEvidence": True,
            },
            "validation": {"commands": []},
            "dirty": {"unclaimedOutsideDelta": "foreign", "sensitiveUnownedBlocks": True, "autoClaimCleanAtStart": True},
            "dirtySplit": {
                "enabled": True,
                "autoRepairOwnedDirty": True,
                "autoCheckpointOwnedDirty": True,
                "branchPrefix": "closeout/split",
                "worktreeRoot": ".claude-state/closeout/dirty-splits/worktrees",
                "maxCandidatesPerRun": 1,
                "registerBrokerOwnership": True,
            },
            "toolingBaseline": {"enabled": False},
            "evidenceRepair": {
                "enabled": False,
                "evidenceRoot": ".closeout-evidence",
                "requiredArtifacts": ["metrics.json", "handoff.json", "session.json", "closeout.json"],
                "requiredFor": ["publish_missing_upstream", "publish_ahead_only", "final_push"],
                "commitMessage": "brokered closeout evidence repair",
            },
            "reviewQuorum": {
                "requiredApprovals": 1,
                "allowedReviewers": ["local-test"],
                "highImpactActions": ["clean_integrate", "checkpoint-owned-dirty", "delete_local_branch", "repo_sweep_prune_merged", "split"],
                "tupleFields": ["candidateId", "actionId", "evidenceHash", "policyHash", "pinnedRefs"],
            },
            "autoQuorum": {
                "enabled": True,
                "requiredScore": 10,
                "allowStaleReviewRenewal": True,
                "reviewers": ["codex-self", "ancestry-safety-reviewer", "mutation-scope-reviewer"],
                "autonomousActionClasses": [
                    "integrated_branch_prune",
                    "repo_sweep_clean_integrate",
                    "owned_dirty_checkpoint",
                    "stale_locked_worktree_cleanup",
                    "redundant_backup_prune",
                    "dirty_split",
                    "foreign_dirty_integrated_branch_prune",
                    "detached_dirty_preserve",
                    "redundant_branch_prune",
                    "explicit_protected_worktree_cleanup",
                ],
                "manualOnlyActionClasses": ["protected_branch", "dirty_worktree", "locked_worktree", "ambiguous_merge_required", "active_locked_worktree", "unowned_dirty_triage"],
            },
            "repoSweep": {
                "enabled": True,
                "mergeMode": "auto_clean",
                "pruneMergedLocalBranches": True,
                "pruneWorktrees": "clean_detached_only",
                "stashMode": "retain",
                "agentDispatchMode": "deterministic",
                "investigateRetainedCandidates": True,
                "retainedBlockerAutoRemediation": {"enabled": True},
                "recoveryBranchPrefix": "closeout/recovery/detached",
                "allowForeignDirtyIntegratedBranchPrune": True,
                "allowPatchEquivalentPrune": True,
                "protectedLockedWorktreeExactPolicy": [],
                "allowCleanCheckedOutIntegration": True,
                "allowStaleLockedWorktreeCleanup": True,
                "lockedWorktreeStaleHours": 24,
                "backupBranchPatterns": ["*backup*", "backup/*", "*-backup", "*-backup-*"],
            },
            "blockerAutoRemediation": {
                "enabled": True,
                "allowForeignDirtyIntegratedBranchSwitch": True,
                "allowDetachedDirtyPreservation": True,
                "allowSensitiveDetachedDirtyPreservation": False,
                "maxDetachedDirtyPaths": 25,
                "prunePatchEquivalentBranches": True,
                "maxConflictFilesForAgent": 8,
                "explicitProtectedWorktreeActions": [],
            },
        }
        if updates:
            config = deep_update(config, updates)
        (repo / "closeout.config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    def init_repo(self, *, remote: bool = False, config_updates: dict | None = None) -> Path:
        self.repo_counter += 1
        repo = self.tempdir / ("repo" if self.repo_counter == 1 else f"repo-{self.repo_counter}")
        repo.mkdir()
        git(repo, "init", "-b", "master")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Test User")
        (repo / ".gitignore").write_text(".claude-state/\n", encoding="utf-8")
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        (repo / "foreign.txt").write_text("base foreign\n", encoding="utf-8")
        git(repo, "add", ".gitignore", "README.md", "foreign.txt")
        git(repo, "commit", "-m", "initial")
        self.write_config(repo, config_updates)
        git(repo, "add", "closeout.config.json")
        git(repo, "commit", "-m", "add closeout policy")
        if remote:
            bare = self.tempdir / f"origin-{self.repo_counter}.git"
            git(self.tempdir, "init", "--bare", str(bare))
            git(repo, "remote", "add", "origin", str(bare))
            git(repo, "push", "-u", "origin", "master")
        return repo

    def make_feature(self, repo: Path, work_block_id: str, *, filename: str = "work.txt") -> dict:
        git(repo, "checkout", "-b", "codex/test-work")
        block = start_work_block(repo, work_block_id=work_block_id, actor="local-test", path_claims=[filename])
        (repo / filename).write_text("feature work\n", encoding="utf-8")
        git(repo, "add", filename)
        git(repo, "commit", "-m", "feature work")
        return block

    def approve_current_tuple(self, repo: Path, work_block_id: str) -> dict:
        config = load_closeout_config(repo)
        detection = detect_work_block(repo, work_block_id=work_block_id)
        evidence = finalize_evidence(config, detection)
        evidence_hash = stable_hash(evidence)
        return record_review_approval(
            repo,
            candidate_id=finalize_candidate_id(work_block_id),
            action_id=finalize_action_id(),
            evidence_hash=evidence_hash,
            pinned_refs=detection["pinnedRefs"],
            reviewer="local-test",
            approved=True,
        )

    def audit_types(self, repo: Path) -> list[str]:
        audit_log = repo / ".claude-state" / "closeout" / "audits" / "audits.jsonl"
        if not audit_log.exists():
            return []
        return [json.loads(line)["auditType"] for line in audit_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_contract_parity_for_config_scripts_and_cli_surface(self) -> None:
        contract = broker_contract(ROOT)
        self.assertFalse(contract["missingConfigKeys"], contract)
        self.assertFalse(contract["missingScripts"], contract)
        self.assertIn("clean_integrate", contract["highImpactActions"])
        config = load_closeout_config(ROOT)
        self.assertEqual(config["git"]["targetBranch"], "master")
        self.assertEqual(config["git"]["remote"], "fork")
        self.assertFalse(config["stashPolicy"]["allowForeignDirtyStash"])
        self.assertIn("pinnedRefs", config["reviewQuorum"]["tupleFields"])
        self.assertIn("repo_sweep_prune_merged", config["reviewQuorum"]["highImpactActions"])
        self.assertIn("split", config["reviewQuorum"]["highImpactActions"])
        self.assertIn("checkpoint-owned-dirty", config["reviewQuorum"]["highImpactActions"])
        self.assertIn("dirtySplit", contract["requiredConfigKeys"])
        self.assertIn("toolingBaseline", contract["requiredConfigKeys"])
        self.assertIn("evidenceRepair", contract["requiredConfigKeys"])
        self.assertIn("responseHookLifecycle", contract["requiredConfigKeys"])
        self.assertIn("blockerAutoRemediation", contract["requiredConfigKeys"])
        self.assertIn("foreign_dirty_integrated_branch_prune", config["autoQuorum"]["autonomousActionClasses"])
        self.assertIn("detached_dirty_preserve", config["autoQuorum"]["autonomousActionClasses"])
        self.assertIn("owned_dirty_checkpoint", config["autoQuorum"]["autonomousActionClasses"])
        self.assertTrue(config["dirty"]["autoClaimCleanAtStart"])
        self.assertTrue(config["repoSweep"]["retainedBlockerAutoRemediation"]["enabled"])
        self.assertEqual(config["repoSweep"]["recoveryBranchPrefix"], "closeout/recovery/detached")
        self.assertTrue(config["repoSweep"]["allowForeignDirtyIntegratedBranchPrune"])
        self.assertTrue(config["repoSweep"]["allowPatchEquivalentPrune"])
        self.assertEqual(config["repoSweep"]["protectedLockedWorktreeExactPolicy"], [])
        self.assertEqual(config["responseHookLifecycle"]["skipSessionWorktreeSignal"], "SkipSessionWorktree")
        self.assertTrue(config["responseHookLifecycle"]["bootstrapAllowedOnlyByExplicitStart"])

    def test_clean_integration_worktree_add_uses_longpaths_config(self) -> None:
        text = (ROOT / "tools" / "repo_hygiene" / "brokered_closeout.py").read_text(encoding="utf-8")
        self.assertIn("def run_git_longpaths", text)
        self.assertIn('"core.longpaths=true"', text)
        self.assertIn('run_git_longpaths(repo_root, ["worktree", "add", "--detach"', text)
        self.assertIn("add_worktree = run_git_longpaths(repo_root, add_args)", text)
        self.assertIn("add = run_git_longpaths(repo_root, add_args)", text)
        self.assertIn('run_git_longpaths(repo_root, ["worktree", "remove"', text)
        self.assertIn('run_git_longpaths(repo_root, ["worktree", "prune"', text)
        self.assertNotIn('run_git(repo_root, ["worktree", "add"', text)
        self.assertNotIn('run_git(repo_root, ["worktree", "remove"', text)

    def test_completion_without_explicit_work_block_id_reports_deterministic_selection_reason(self) -> None:
        repo = self.init_repo(remote=False)
        git(repo, "checkout", "-b", "codex/select-latest")
        old = start_work_block(repo, work_block_id="wb-a-old", actor="local-test")
        old_manifest_path = repo / ".claude-state" / "closeout" / "work-blocks" / old["workBlockId"] / "manifest.json"
        old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
        old_manifest.update(
            {
                "state": "blocked",
                "blockedReason": "review_quorum_missing",
                "updatedAt": "2026-01-01T00:00:00+00:00",
            }
        )
        old_manifest_path.write_text(json.dumps(old_manifest, indent=2), encoding="utf-8")
        start_work_block(repo, work_block_id="wb-z-new", actor="local-test")

        result = complete_work_block(repo, finalize=False)

        self.assertEqual(result["workBlockId"], "wb-z-new")
        self.assertEqual(result["workBlockSelection"]["reason"], "selected_by_branch_state_updated_workBlockId")
        self.assertEqual(result["workBlockSelection"]["candidateCount"], 2)

    def test_pre_response_broker_bootstrap_records_dirty_baseline_without_worktree(self) -> None:
        repo = self.init_repo(remote=False)
        git(repo, "checkout", "-b", "codex/pre-response")
        (repo / "baseline-dirty.txt").write_text("dirty before hook\n", encoding="utf-8")
        worktrees_before = git(repo, "worktree", "list", "--porcelain").stdout

        result = bootstrap_response_broker_manifest(repo, hook_phase="response", actor="local-test-hook")

        worktrees_after = git(repo, "worktree", "list", "--porcelain").stdout
        manifest = result["manifest"]
        self.assertIn(result["status"], {"created", "refreshed"})
        self.assertEqual(manifest["branch"], "codex/pre-response")
        self.assertEqual(Path(manifest["worktree"]).resolve(), repo.resolve())
        self.assertEqual(manifest["startHead"], git(repo, "rev-parse", "HEAD").stdout.strip())
        self.assertIn("baseline-dirty.txt", manifest["dirtyBaseline"]["paths"])
        self.assertIn("lease", manifest)
        self.assertEqual(manifest["pathClaims"], [])
        self.assertEqual(worktrees_after, worktrees_before)

    def test_clean_at_start_new_dirty_paths_auto_claimed_and_checkpointed_through_quorum(self) -> None:
        repo = self.init_repo(remote=False)
        git(repo, "checkout", "-b", "codex/clean-at-start")
        start_work_block(repo, work_block_id="wb-clean-start", actor="local-test")
        (repo / "new-owned.txt").write_text("owned after baseline\n", encoding="utf-8")

        detection = detect_work_block(repo, work_block_id="wb-clean-start")
        self.assertEqual([item["path"] for item in detection["ownedDirty"]], ["new-owned.txt"])
        self.assertTrue(detection["ownedDirty"][0]["autoClaimedByDirtyBaseline"])

        result = finalize_work_block(repo, work_block_id="wb-clean-start")

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(git(repo, "show", "master:new-owned.txt").stdout, "owned after baseline\n")
        self.assertIn("checkpoint_owned_dirty", self.audit_types(repo))
        self.assertIn("auto_quorum", self.audit_types(repo))

    def test_baseline_dirty_claimed_path_blocks_as_mixed_and_not_checkpointed(self) -> None:
        repo = self.init_repo(remote=False)
        git(repo, "checkout", "-b", "codex/mixed-baseline")
        (repo / "mixed.txt").write_text("dirty before broker\n", encoding="utf-8")
        start_work_block(repo, work_block_id="wb-mixed", actor="local-test", path_claims=["mixed.txt"])
        head_before = git(repo, "rev-parse", "HEAD").stdout.strip()

        detection = detect_work_block(repo, work_block_id="wb-mixed")
        self.assertFalse(detection["ownedDirty"], detection)
        self.assertEqual([item["path"] for item in detection["mixedDirty"]], ["mixed.txt"])
        self.assertEqual(detection["mixedDirty"][0]["blocker"], "baseline-dirty-overlaps-candidate")

        result = checkpoint_owned_work(repo, work_block_id="wb-mixed")

        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(result["reason"], "baseline-dirty-overlaps-candidate")
        self.assertEqual(git(repo, "diff", "--cached", "--name-only").stdout, "")
        self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), head_before)

    def test_owned_dirty_checkpoint_stages_only_exact_owned_paths(self) -> None:
        repo = self.init_repo(remote=False)
        git(repo, "checkout", "-b", "codex/exact-checkpoint")
        start_work_block(repo, work_block_id="wb-exact", actor="local-test")
        start_work_block(repo, work_block_id="wb-other-foreign", actor="local-test", path_claims=["foreign.txt"])
        (repo / "owned.txt").write_text("owned checkpoint\n", encoding="utf-8")
        (repo / "foreign.txt").write_text("foreign stays dirty\n", encoding="utf-8")

        result = checkpoint_owned_work(repo, work_block_id="wb-exact")

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["paths"], ["owned.txt"])
        self.assertEqual(result["stagedPaths"], ["owned.txt"])
        self.assertEqual(git(repo, "show", "HEAD:owned.txt").stdout, "owned checkpoint\n")
        self.assertEqual(git(repo, "show", "HEAD:foreign.txt").stdout, "base foreign\n")
        self.assertEqual((repo / "foreign.txt").read_text(encoding="utf-8"), "foreign stays dirty\n")
        self.assertEqual(git(repo, "diff", "--cached", "--name-only").stdout, "")

    def test_stale_refs_block_finalize_before_mutation(self) -> None:
        repo = self.init_repo()
        self.make_feature(repo, "wb-stale-refs")
        detection = detect_work_block(repo, work_block_id="wb-stale-refs")
        (repo / "work.txt").write_text("feature work\nchanged\n", encoding="utf-8")
        git(repo, "add", "work.txt")
        git(repo, "commit", "-m", "move feature head")
        result = finalize_work_block(repo, work_block_id="wb-stale-refs", expected_pinned_refs=detection["pinnedRefs"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "stale_refs")
        self.assertIn("stale_refs", self.audit_types(repo))
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "codex/test-work")

    def test_stale_review_tuple_blocks_when_target_moves(self) -> None:
        repo = self.init_repo(config_updates={"autoQuorum": {"allowStaleReviewRenewal": False}})
        self.make_feature(repo, "wb-stale-review")
        self.approve_current_tuple(repo, "wb-stale-review")
        git(repo, "checkout", "master")
        (repo / "target.txt").write_text("target moved\n", encoding="utf-8")
        git(repo, "add", "target.txt")
        git(repo, "commit", "-m", "move target")
        git(repo, "checkout", "codex/test-work")
        result = finalize_work_block(repo, work_block_id="wb-stale-review")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "stale_review")
        self.assertIn("stale_review", self.audit_types(repo))
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "codex/test-work")

    def test_finalize_auto_quorum_renews_stale_review_when_policy_allows(self) -> None:
        repo = self.init_repo()
        self.make_feature(repo, "wb-renew-stale-review")
        self.approve_current_tuple(repo, "wb-renew-stale-review")
        (repo / "feature-followup.txt").write_text("follow-up after review\n", encoding="utf-8")
        git(repo, "add", "feature-followup.txt")
        git(repo, "commit", "-m", "feature follow-up")

        result = finalize_work_block(repo, work_block_id="wb-renew-stale-review")

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["quorum"]["matchingApprovals"], 3)
        self.assertIn("auto_quorum", self.audit_types(repo))
        self.assertEqual(git(repo, "show", "master:feature-followup.txt").stdout, "follow-up after review\n")

    def test_validation_failure_blocks_after_clean_merge(self) -> None:
        repo = self.init_repo(
            config_updates={
                "validation": {
                    "commands": [
                        {"name": "intentional-failure", "argv": [sys.executable, "-c", "import sys; sys.exit(7)"]}
                    ]
                }
            }
        )
        self.make_feature(repo, "wb-validation")
        self.approve_current_tuple(repo, "wb-validation")
        result = finalize_work_block(repo, work_block_id="wb-validation")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "validation_failed")
        self.assertIn("validation_failure", self.audit_types(repo))
        self.assertEqual(git(repo, "rev-parse", "--verify", "codex/test-work").returncode, 0)

    def test_closeout_tooling_stale_blocks_before_hygiene_blocker(self) -> None:
        repo = self.init_repo(
            config_updates={
                "toolingBaseline": {
                    "enabled": True,
                    "autoUpdate": False,
                    "requiredTests": ["test_missing_future_closeout_actor"],
                    "requiredSymbols": [
                        {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def missing_future_actor"}
                    ],
                }
            }
        )
        self.make_feature(repo, "wb-tooling-stale")
        (repo / "work.txt").write_text("dirty blocker should not be authoritative\n", encoding="utf-8")

        result = finalize_work_block(repo, work_block_id="wb-tooling-stale")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "closeout_tooling_stale")
        missing_kinds = {item["kind"] for item in result["tooling"]["missing"]}
        self.assertIn("test", missing_kinds)
        self.assertIn("symbol", missing_kinds)
        self.assertIn("closeout_tooling_stale", self.audit_types(repo))

    def test_closeout_tooling_stale_auto_updates_only_safe_paths(self) -> None:
        repo = self.init_repo(
            config_updates={
                "toolingBaseline": {
                    "enabled": True,
                    "baselineRef": "master",
                    "autoUpdate": True,
                    "paths": ["tools/repo_hygiene/brokered_closeout.py"],
                    "requiredTests": [],
                    "requiredSymbols": [
                        {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def baseline_actor"}
                    ],
                }
            }
        )
        tool_path = repo / "tools" / "repo_hygiene" / "brokered_closeout.py"
        tool_path.parent.mkdir(parents=True, exist_ok=True)
        tool_path.write_text("def baseline_actor():\n    return True\n", encoding="utf-8")
        git(repo, "add", "tools/repo_hygiene/brokered_closeout.py")
        git(repo, "commit", "-m", "baseline tooling")
        git(repo, "checkout", "-b", "codex/stale-tooling")
        tool_path.write_text("def old_actor():\n    return False\n", encoding="utf-8")
        git(repo, "add", "tools/repo_hygiene/brokered_closeout.py")
        git(repo, "commit", "-m", "stale tooling")
        start_work_block(repo, work_block_id="wb-tooling-update", actor="local-test", path_claims=["work.txt"])

        result = repair_eligibility(repo, work_block_id="wb-tooling-update")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "closeout_tooling_stale")
        self.assertIn("def baseline_actor", tool_path.read_text(encoding="utf-8"))
        self.assertTrue(result["tooling"]["updated"])

    def test_foreign_dirty_remains_retained_audited_and_does_not_block_independent_closeout(self) -> None:
        repo = self.init_repo()
        self.make_feature(repo, "wb-foreign")
        start_work_block(repo, work_block_id="wb-foreign-owner", actor="local-test", path_claims=["foreign.txt"])
        (repo / "foreign.txt").write_text("dirty but unrelated\n", encoding="utf-8")
        detection = detect_work_block(repo, work_block_id="wb-foreign")
        self.assertFalse(detection["ownedDirty"])
        self.assertFalse(detection["unownedDirty"])
        self.assertEqual([item["path"] for item in detection["foreignDirty"]], ["foreign.txt"])
        self.approve_current_tuple(repo, "wb-foreign")
        result = finalize_work_block(repo, work_block_id="wb-foreign")
        self.assertEqual(result["status"], "success")
        self.assertEqual([item["path"] for item in result["foreignDirtyRetained"]], ["foreign.txt"])
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "master")
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/test-work", check=False).returncode, 0)
        self.assertEqual((repo / "foreign.txt").read_text(encoding="utf-8"), "dirty but unrelated\n")
        self.assertIn("success", self.audit_types(repo))
        self.assertIn("branch_deletion", self.audit_types(repo))

    def test_other_work_block_claim_takes_precedence_over_branch_delta(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/claimed-delta")
        start_work_block(repo, work_block_id="wb-current", actor="local-test", path_claims=["owned.txt"])
        (repo / "owned.txt").write_text("committed\n", encoding="utf-8")
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "owned committed")
        start_work_block(repo, work_block_id="wb-z-other", actor="local-test", path_claims=["owned.txt"])
        (repo / "owned.txt").write_text("other dirty\n", encoding="utf-8")

        detection = detect_work_block(repo, work_block_id="wb-current")

        self.assertFalse(detection["ownedDirty"], detection)
        self.assertEqual([item["path"] for item in detection["foreignDirty"]], ["owned.txt"])
        self.assertEqual(detection["foreignDirty"][0]["ownerWorkBlockId"], "wb-z-other")

    def test_no_origin_local_only_closeout_updates_target_and_prunes_branch(self) -> None:
        repo = self.init_repo(remote=False)
        self.make_feature(repo, "wb-local-only")
        self.approve_current_tuple(repo, "wb-local-only")
        result = finalize_work_block(repo, work_block_id="wb-local-only")
        self.assertEqual(result["status"], "success")
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "master")
        self.assertEqual((repo / "work.txt").read_text(encoding="utf-8"), "feature work\n")
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/test-work", check=False).returncode, 0)

    def test_finalize_auto_quorum_resolves_missing_review(self) -> None:
        repo = self.init_repo(remote=False)
        self.make_feature(repo, "wb-auto-finalize")

        result = finalize_work_block(repo, work_block_id="wb-auto-finalize")

        self.assertEqual(result["status"], "success", result)
        self.assertIn("auto_quorum", self.audit_types(repo))
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "master")

    def test_partial_push_recovery_updates_local_target_and_cleans_branch(self) -> None:
        repo = self.init_repo(remote=True)
        self.make_feature(repo, "wb-partial-push")
        feature_head = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "push", "origin", "HEAD:master")
        local_master_before = git(repo, "rev-parse", "master").stdout.strip()
        self.assertNotEqual(local_master_before, feature_head)
        self.approve_current_tuple(repo, "wb-partial-push")
        result = finalize_work_block(repo, work_block_id="wb-partial-push")
        self.assertEqual(result["status"], "success")
        self.assertEqual(git(repo, "rev-parse", "master").stdout.strip(), feature_head)
        self.assertIn("partial_push_recovery", self.audit_types(repo))
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/test-work", check=False).returncode, 0)

    def test_safe_local_branch_pruning_retains_foreign_dirty_files(self) -> None:
        repo = self.init_repo()
        self.make_feature(repo, "wb-prune")
        start_work_block(repo, work_block_id="wb-prune-foreign-owner", actor="local-test", path_claims=["foreign.txt"])
        (repo / "foreign.txt").write_text("keep my local edit\n", encoding="utf-8")
        self.approve_current_tuple(repo, "wb-prune")
        result = finalize_work_block(repo, work_block_id="wb-prune")
        self.assertEqual(result["status"], "success")
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "master")
        self.assertEqual((repo / "foreign.txt").read_text(encoding="utf-8"), "keep my local edit\n")
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/test-work", check=False).returncode, 0)
        self.assertIn("branch_deletion", self.audit_types(repo))

    def test_repo_sweep_plans_branches_worktrees_and_stashes_without_mutation(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/merged")
        (repo / "merged.txt").write_text("merged\n", encoding="utf-8")
        git(repo, "add", "merged.txt")
        git(repo, "commit", "-m", "merged branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/merged", "-m", "merge codex merged")
        git(repo, "checkout", "-b", "codex/unmerged")
        (repo / "unmerged.txt").write_text("unmerged\n", encoding="utf-8")
        git(repo, "add", "unmerged.txt")
        git(repo, "commit", "-m", "unmerged branch")
        git(repo, "checkout", "master")
        (repo / "README.md").write_text("stashed change\n", encoding="utf-8")
        git(repo, "stash", "push", "-m", "sweep test stash")
        detached = self.tempdir / "detached-worktree"
        git(repo, "worktree", "add", "--detach", str(detached), "HEAD")
        (detached / "detached.txt").write_text("dirty detached\n", encoding="utf-8")

        result = repo_sweep(repo)
        self.assertEqual(result["status"], "planned")
        branch_dispositions = {item["branch"]: item["disposition"] for item in result["plan"]["branchPlans"]}
        self.assertEqual(branch_dispositions["codex/merged"], "prune_merged_branch")
        self.assertEqual(branch_dispositions["codex/unmerged"], "merge_required")
        self.assertEqual(branch_dispositions["master"], "retain_protected_branch")
        self.assertEqual(result["plan"]["stashPlans"][0]["disposition"], "retain_stash")
        detached_plan = next(item for item in result["plan"]["worktreePlans"] if Path(item.get("path", "")).resolve() == detached.resolve())
        self.assertEqual(detached_plan["disposition"], "retain_dirty_detached_worktree")
        self.assertEqual(git(repo, "rev-parse", "--verify", "codex/merged").returncode, 0)

    def test_repo_sweep_apply_auto_quorums_and_prunes_only_merged_branch(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/merged")
        (repo / "merged.txt").write_text("merged\n", encoding="utf-8")
        git(repo, "add", "merged.txt")
        git(repo, "commit", "-m", "merged branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/merged", "-m", "merge codex merged")
        applied = repo_sweep(repo, apply=True)
        self.assertEqual(applied["status"], "success")
        self.assertTrue(applied["quorumResults"][0]["autoGenerated"])
        self.assertTrue(applied["quorumResults"][0]["quorum"]["ok"])
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/merged", check=False).returncode, 0)
        self.assertIn("branch_deletion", self.audit_types(repo))
        packet_path = Path(applied["quorumResults"][0]["unblockDetail"]["reviewPacketPath"])
        self.assertTrue(packet_path.exists())
        self.assertTrue((packet_path.parent / "accepted-review-manifest.json").exists())

    def test_repo_sweep_prune_worktrees_clean_detached_only_removes_clean_detached(self) -> None:
        repo = self.init_repo()
        detached = self.tempdir / "clean-detached-worktree"
        git(repo, "worktree", "add", "--detach", str(detached), "HEAD")

        applied = repo_sweep(repo, apply=True)

        self.assertEqual(applied["status"], "success")
        self.assertFalse(detached.exists())
        self.assertTrue(any(item.get("action") == "remove_clean_detached_worktree" for item in applied["actions"]))
        self.assertIn("snapshot_pruning", self.audit_types(repo))

    def test_repo_sweep_manual_only_candidate_reports_recoverable_unblock_detail(self) -> None:
        repo = self.init_repo(config_updates={"autoQuorum": {"autonomousActionClasses": [], "manualOnlyActionClasses": ["integrated_branch_prune"]}})
        git(repo, "checkout", "-b", "codex/merged")
        (repo / "merged.txt").write_text("merged\n", encoding="utf-8")
        git(repo, "add", "merged.txt")
        git(repo, "commit", "-m", "merged branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/merged", "-m", "merge codex merged")
        result = repo_sweep(repo, apply=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(git(repo, "rev-parse", "--verify", "codex/merged").returncode, 0)
        quorum_result = result["quorumResults"][0]
        self.assertFalse(quorum_result["autoGenerated"])
        self.assertFalse(quorum_result["quorum"]["ok"])
        detail = quorum_result["unblockDetail"]
        self.assertEqual(detail["candidateId"], result["branchCandidates"][0]["candidateId"])
        self.assertEqual(detail["actionId"], "delete_local_branch")
        self.assertIn("policyHash", detail)
        self.assertIn("pinnedRefs", detail)
        self.assertIn("reviewPacketPath", detail)
        self.assertFalse(detail["autoUnblockAllowed"])

    def test_repo_sweep_merge_required_branch_writes_investigation_packet(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/unmerged")
        (repo / "unmerged.txt").write_text("unmerged\n", encoding="utf-8")
        git(repo, "add", "unmerged.txt")
        git(repo, "commit", "-m", "unmerged branch")
        git(repo, "checkout", "master")

        result = repo_sweep(repo)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/unmerged")
        self.assertEqual(report["sourceDisposition"], "merge_required")
        self.assertEqual(report["recommendedAction"], "clean_integrate_now")
        self.assertTrue(Path(report["reportPath"]).exists())
        self.assertTrue(report["scope"]["mergeProbe"]["clean"])
        self.assertEqual(git(repo, "rev-parse", "--verify", "codex/unmerged").returncode, 0)
        self.assertNotEqual(git(repo, "rev-parse", "master").stdout.strip(), git(repo, "rev-parse", "codex/unmerged").stdout.strip())

    def test_repo_sweep_clean_checked_out_branch_can_still_integrate(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/checked")
        (repo / "checked.txt").write_text("checked work\n", encoding="utf-8")
        git(repo, "add", "checked.txt")
        git(repo, "commit", "-m", "checked branch")
        git(repo, "checkout", "master")
        checked_worktree = self.tempdir / "checked-worktree"
        git(repo, "worktree", "add", str(checked_worktree), "codex/checked")

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/checked")
        self.assertEqual(report["recommendedAction"], "clean_integrate_now")
        self.assertEqual(git(repo, "show", "master:checked.txt").stdout, "checked work\n")
        self.assertFalse(checked_worktree.exists())
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/checked", check=False).returncode, 0)

    def test_repo_sweep_stale_locked_clean_worktree_can_be_cleaned(self) -> None:
        repo = self.init_repo(config_updates={"repoSweep": {"lockedWorktreeStaleHours": 0}})
        git(repo, "checkout", "-b", "codex/stale-lock")
        (repo / "stale.txt").write_text("stale lock work\n", encoding="utf-8")
        git(repo, "add", "stale.txt")
        git(repo, "commit", "-m", "stale lock branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/stale-lock", "-m", "merge stale lock")
        locked_worktree = self.tempdir / "locked-worktree"
        git(repo, "worktree", "add", str(locked_worktree), "codex/stale-lock")
        git(repo, "worktree", "lock", "--reason", "pid=999999 stale test lock", str(locked_worktree))
        lock_file = Path(git(locked_worktree, "rev-parse", "--git-dir").stdout.strip()) / "locked"
        if not lock_file.is_absolute():
            lock_file = locked_worktree / lock_file
        old_time = 1_600_000_000
        os.utime(lock_file, (old_time, old_time))

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/stale-lock")
        self.assertEqual(report["recommendedAction"], "cleanup_worktree_and_prune")
        self.assertEqual(report["actionClass"], "stale_locked_worktree_cleanup")
        self.assertFalse(report["lockInspection"]["pidAlive"])
        self.assertFalse(locked_worktree.exists())
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/stale-lock", check=False).returncode, 0)

    def test_repo_sweep_backup_branch_is_analyzed_before_prune(self) -> None:
        repo = self.init_repo()
        git(repo, "branch", "codex/master-backup", "master")

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/master-backup")
        self.assertTrue(report["backupAnalysis"]["isBackupBranch"])
        self.assertEqual(report["backupAnalysis"]["redundantWith"]["kind"], "target")
        self.assertEqual(report["recommendedAction"], "prune_now")
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/master-backup", check=False).returncode, 0)

    def test_repo_sweep_patch_equivalent_backup_branch_is_pruned(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/topic-backup")
        (repo / "dup.txt").write_text("same patch\n", encoding="utf-8")
        git(repo, "add", "dup.txt")
        git(repo, "commit", "-m", "backup duplicate work")
        git(repo, "checkout", "master")
        (repo / "dup.txt").write_text("same patch\n", encoding="utf-8")
        git(repo, "add", "dup.txt")
        git(repo, "commit", "-m", "integrated duplicate work")
        (repo / "target-only.txt").write_text("target-only\n", encoding="utf-8")
        git(repo, "add", "target-only.txt")
        git(repo, "commit", "-m", "target-only follow-up")

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/topic-backup")
        self.assertEqual(report["backupAnalysis"]["redundantWith"]["kind"], "target_patch_equivalent")
        self.assertEqual(report["recommendedAction"], "prune_now")
        deletion = next(item for item in result["actions"] if item.get("branch") == "codex/topic-backup" and item.get("action") == "delete_local_branch")
        self.assertTrue(deletion["forced"])
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/topic-backup", check=False).returncode, 0)

    def test_repo_sweep_patch_equivalent_non_backup_branch_auto_quorum_prunes(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/topic-copy")
        (repo / "dup.txt").write_text("same patch\n", encoding="utf-8")
        git(repo, "add", "dup.txt")
        git(repo, "commit", "-m", "copy duplicate work")
        git(repo, "checkout", "master")
        (repo / "dup.txt").write_text("same patch\n", encoding="utf-8")
        git(repo, "add", "dup.txt")
        git(repo, "commit", "-m", "integrated duplicate work")
        (repo / "target-only.txt").write_text("target-only\n", encoding="utf-8")
        git(repo, "add", "target-only.txt")
        git(repo, "commit", "-m", "target-only follow-up")

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/topic-copy")
        self.assertFalse(report["backupAnalysis"]["isBackupBranch"])
        self.assertEqual(report["backupAnalysis"]["redundantWith"]["kind"], "target_patch_equivalent")
        self.assertEqual(report["actionClass"], "redundant_branch_prune")
        quorum = next(item for item in result["quorumResults"] if item["report"]["branch"] == "codex/topic-copy")
        self.assertTrue(quorum["autoGenerated"])
        self.assertTrue(quorum["quorum"]["ok"])
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/topic-copy", check=False).returncode, 0)

    def test_repo_sweep_apply_candidate_id_mutates_only_that_candidate(self) -> None:
        repo = self.init_repo()
        git(repo, "branch", "codex/first-backup", "master")
        git(repo, "branch", "codex/second-backup", "master")
        plan = repo_sweep(repo)
        candidate = next(
            item
            for item in plan["promotedCandidates"]
            if item["pinnedRefs"]["branch"]["branch"] == "codex/first-backup"
        )

        result = repo_sweep(repo, apply=True, candidate_id=candidate["candidateId"])

        self.assertEqual(result["status"], "success", result)
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/first-backup", check=False).returncode, 0)
        self.assertEqual(git(repo, "rev-parse", "--verify", "codex/second-backup", check=False).returncode, 0)

    def test_repo_sweep_dirty_current_worktree_gets_ownership_classification(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/dirty-owned")
        start_work_block(repo, work_block_id="wb-dirty-owned", actor="local-test", path_claims=["owned.txt"])
        (repo / "owned.txt").write_text("owned base\n", encoding="utf-8")
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "owned branch")
        (repo / "owned.txt").write_text("owned dirty\n", encoding="utf-8")
        (repo / "foreign-dirty.txt").write_text("foreign dirty\n", encoding="utf-8")

        result = repo_sweep(repo)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/dirty-owned")
        self.assertEqual(report["sourceDisposition"], "retain_dirty_worktree")
        self.assertEqual(report["recommendedAction"], "split_now")
        self.assertEqual(report["actionClass"], "dirty_split")
        classification = report["dirtyClassification"]
        self.assertEqual(classification["workBlockId"], "wb-dirty-owned")
        self.assertEqual([item["path"] for item in classification["ownedDirty"]], ["owned.txt"])
        self.assertEqual([item["path"] for item in classification["foreignDirty"]], ["foreign-dirty.txt"])

    def test_repo_sweep_foreign_dirty_integrated_branch_switches_and_prunes(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/foreign-integrated")
        (repo / "integrated.txt").write_text("integrated\n", encoding="utf-8")
        git(repo, "add", "integrated.txt")
        git(repo, "commit", "-m", "integrated branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/foreign-integrated", "-m", "merge integrated branch")
        git(repo, "checkout", "codex/foreign-integrated")
        (repo / "foreign-local.txt").write_text("foreign dirty stays\n", encoding="utf-8")

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/foreign-integrated")
        self.assertEqual(report["recommendedAction"], "switch_target_and_prune")
        self.assertEqual(report["actionClass"], "foreign_dirty_integrated_branch_prune")
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "master")
        self.assertEqual((repo / "foreign-local.txt").read_text(encoding="utf-8"), "foreign dirty stays\n")
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/foreign-integrated", check=False).returncode, 0)

    def test_repo_sweep_foreign_dirty_integrated_branch_detaches_linked_worktree_and_prunes(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/foreign-detached")
        (repo / "integrated.txt").write_text("integrated\n", encoding="utf-8")
        git(repo, "add", "integrated.txt")
        git(repo, "commit", "-m", "integrated branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/foreign-detached", "-m", "merge integrated branch")
        linked = self.tempdir / "foreign-detached-worktree"
        git(repo, "worktree", "add", str(linked), "codex/foreign-detached")
        (linked / "foreign-local.txt").write_text("foreign dirty stays\n", encoding="utf-8")

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/foreign-detached")
        self.assertEqual(report["recommendedAction"], "switch_target_and_prune")
        action = next(item for item in result["actions"] if item.get("action") == "foreign_dirty_integrated_branch_prune")
        self.assertEqual(action["status"], "success", action)
        self.assertEqual(action["switch"]["action"], "detach_foreign_dirty_worktree_at_target")
        self.assertEqual(git(linked, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "HEAD")
        self.assertEqual((linked / "foreign-local.txt").read_text(encoding="utf-8"), "foreign dirty stays\n")
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/foreign-detached", check=False).returncode, 0)

    def test_repo_sweep_foreign_dirty_target_overlap_blocks_with_exact_path_evidence(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/foreign-overlap")
        (repo / "integrated.txt").write_text("integrated\n", encoding="utf-8")
        git(repo, "add", "integrated.txt")
        git(repo, "commit", "-m", "integrated branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/foreign-overlap", "-m", "merge integrated branch")
        (repo / "target-overlap.txt").write_text("target-side change\n", encoding="utf-8")
        git(repo, "add", "target-overlap.txt")
        git(repo, "commit", "-m", "target overlap path")
        linked = self.tempdir / "foreign-overlap-worktree"
        git(repo, "worktree", "add", str(linked), "codex/foreign-overlap")
        (linked / "target-overlap.txt").write_text("foreign dirty overlap\n", encoding="utf-8")

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/foreign-overlap")
        self.assertEqual(report["recommendedAction"], "retain_with_proven_blocker")
        self.assertEqual(report["blockers"], ["foreign_dirty_target_overlap"])
        self.assertEqual(report["scope"]["dirtyTargetDeltaOverlap"], ["target-overlap.txt"])
        self.assertEqual(report["dirtyClassification"]["dirtyPaths"], ["target-overlap.txt"])
        self.assertEqual(report["remediationProof"]["excludedByExactPolicy"], ["foreign_dirty_target_overlap"])
        self.assertEqual(git(repo, "rev-parse", "--verify", "codex/foreign-overlap").returncode, 0)

    def test_repo_sweep_detached_dirty_worktree_is_preserved_before_cleanup(self) -> None:
        repo = self.init_repo()
        detached = self.tempdir / "dirty-detached-preserve"
        git(repo, "worktree", "add", "--detach", str(detached), "HEAD")
        (detached / "README.md").write_text("detached dirty readme\n", encoding="utf-8")
        (detached / "detached-only.txt").write_text("another exact dirty path\n", encoding="utf-8")

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["sourceDisposition"] == "retain_dirty_detached_worktree")
        self.assertEqual(report["recommendedAction"], "preserve_detached_dirty_now")
        self.assertEqual(report["preservationBranch"].split("/")[:3], ["closeout", "recovery", "detached"])
        action = next(item for item in result["actions"] if item.get("action") == "detached_dirty_preserve")
        self.assertEqual(action["status"], "success", action)
        self.assertFalse(detached.exists())
        self.assertEqual(sorted(item["path"] for item in action["copied"]), ["README.md", "detached-only.txt"])
        self.assertEqual(git(repo, "show", f"{action['preservationBranch']}:README.md").stdout, "detached dirty readme\n")
        self.assertEqual(git(repo, "show", f"{action['preservationBranch']}:detached-only.txt").stdout, "another exact dirty path\n")
        self.assertIn("orphan_quarantine", self.audit_types(repo))

    def test_repo_sweep_detached_dirty_preservation_refuses_stale_or_missing_commit_before_cleanup(self) -> None:
        repo = self.init_repo()
        detached = self.tempdir / "dirty-detached-stale-preserve"
        git(repo, "worktree", "add", "--detach", str(detached), "HEAD")
        (detached / "README.md").write_text("detached dirty readme\n", encoding="utf-8")
        plan = repo_sweep(repo)
        report = next(item for item in plan["retainedCandidateReports"] if item["sourceDisposition"] == "retain_dirty_detached_worktree")
        git(repo, "branch", report["preservationBranch"], "HEAD")
        (repo / "stale-preservation.txt").write_text("stale preservation\n", encoding="utf-8")
        git(repo, "add", "stale-preservation.txt")
        git(repo, "commit", "-m", "stale preservation branch input")
        git(repo, "branch", "-f", report["preservationBranch"], "HEAD")

        result = repo_sweep(repo, apply=True)

        action = next(item for item in result["actions"] if item.get("reason") == "preservation_branch_exists")
        self.assertEqual(action["status"], "blocked")
        self.assertTrue(detached.exists())
        self.assertEqual((detached / "README.md").read_text(encoding="utf-8"), "detached dirty readme\n")

        missing_commit_repo = self.init_repo()
        missing_detached = self.tempdir / "dirty-detached-missing-preserve"
        git(missing_commit_repo, "worktree", "add", "--detach", str(missing_detached), "HEAD")
        (missing_detached / "README.md").write_text("detached dirty readme\n", encoding="utf-8")

        real_run_git = __import__("tools.repo_hygiene.brokered_closeout", fromlist=["run_git"]).run_git

        def fail_preservation_commit(cwd: Path, args: list[str], *pargs: object, **kwargs: object):
            if args[:2] == ["commit", "-m"] and "detached-preserve" in str(cwd):
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="simulated missing preservation commit")
            return real_run_git(cwd, args, *pargs, **kwargs)

        with mock.patch("tools.repo_hygiene.brokered_closeout.run_git", side_effect=fail_preservation_commit):
            missing_result = repo_sweep(missing_commit_repo, apply=True)

        missing_action = next(item for item in missing_result["actions"] if item.get("reason") == "preservation_commit_failed")
        self.assertEqual(missing_action["status"], "blocked")
        self.assertTrue(missing_detached.exists())

    def test_repo_sweep_explicit_protected_stale_worktree_cleanup_requires_exact_policy(self) -> None:
        repo = self.init_repo(
            config_updates={
                "repoSweep": {"lockedWorktreeStaleHours": 0},
                "cleanupPolicy": {"protectedWorktreeRoots": [".protected-worktrees/**"]},
            }
        )
        git(repo, "checkout", "-b", "codex/protected-lock")
        (repo / "protected.txt").write_text("protected lock work\n", encoding="utf-8")
        git(repo, "add", "protected.txt")
        git(repo, "commit", "-m", "protected lock branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/protected-lock", "-m", "merge protected lock")
        protected_root = repo / ".protected-worktrees"
        protected_root.mkdir(exist_ok=True)
        locked_worktree = protected_root / "locked"
        git(repo, "worktree", "add", str(locked_worktree), "codex/protected-lock")
        lock_reason = "pid=999999 protected stale test"
        git(repo, "worktree", "lock", "--reason", lock_reason, str(locked_worktree))

        plan = repo_sweep(repo)
        retained = next(item for item in plan["retainedCandidateReports"] if item["branch"] == "codex/protected-lock")
        self.assertEqual(retained["actionClass"], "active_locked_worktree")
        self.assertEqual(retained["recommendedAction"], "retain_with_proven_blocker")
        self.assertIn("repoSweep.protectedLockedWorktreeExactPolicy.missing_or_mismatched", retained["remediationProof"]["excludedByExactPolicy"])
        blocked_apply = repo_sweep(repo, apply=True)
        blocked_report = next(item for item in blocked_apply["retainedCandidateReports"] if item["branch"] == "codex/protected-lock")
        self.assertEqual(blocked_report["actionClass"], "active_locked_worktree")
        self.assertTrue(locked_worktree.exists())
        self.assertEqual(git(repo, "rev-parse", "--verify", "codex/protected-lock").returncode, 0)
        evidence = retained["scope"]["protectedWorktreeCleanupEvidence"]
        self.write_config(
            repo,
            {
                "repoSweep": {"lockedWorktreeStaleHours": 0},
                "cleanupPolicy": {"protectedWorktreeRoots": [".protected-worktrees/**"]},
                "repoSweep": {
                    "lockedWorktreeStaleHours": 0,
                    "protectedLockedWorktreeExactPolicy": [
                        {
                            "branch": "codex/protected-lock",
                            "path": evidence["path"],
                            "lockReason": lock_reason,
                            "action": "cleanup_worktree_and_prune",
                            "evidenceHash": evidence["evidenceHash"],
                            "recoveryCommand": "restore branch from reflog if cleanup was wrong",
                        }
                    ],
                },
            },
        )

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/protected-lock")
        self.assertEqual(report["actionClass"], "explicit_protected_worktree_cleanup")
        self.assertFalse(locked_worktree.exists())
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "codex/protected-lock", check=False).returncode, 0)

    def test_repo_sweep_protected_locked_worktree_without_exact_policy_is_inspect_only(self) -> None:
        repo = self.init_repo(
            config_updates={
                "repoSweep": {"lockedWorktreeStaleHours": 0},
                "cleanupPolicy": {"protectedWorktreeRoots": [".protected-worktrees/**"]},
            }
        )
        git(repo, "checkout", "-b", "codex/protected-inspect")
        (repo / "protected.txt").write_text("protected lock work\n", encoding="utf-8")
        git(repo, "add", "protected.txt")
        git(repo, "commit", "-m", "protected lock branch")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "codex/protected-inspect", "-m", "merge protected lock")
        protected_root = repo / ".protected-worktrees"
        protected_root.mkdir(exist_ok=True)
        locked_worktree = protected_root / "locked-inspect"
        git(repo, "worktree", "add", str(locked_worktree), "codex/protected-inspect")
        git(repo, "worktree", "lock", "--reason", "pid=999999 protected inspect test", str(locked_worktree))

        result = repo_sweep(repo, apply=True)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/protected-inspect")
        self.assertEqual(report["actionClass"], "active_locked_worktree")
        self.assertEqual(report["recommendedAction"], "retain_with_proven_blocker")
        self.assertTrue(locked_worktree.exists())
        self.assertEqual(git(repo, "rev-parse", "--verify", "codex/protected-inspect").returncode, 0)
        self.assertFalse(any(item.get("branch") == "codex/protected-inspect" for item in result["actions"]))

    def test_repo_sweep_merge_failed_report_has_agent_resolution_packet(self) -> None:
        repo = self.init_repo()
        (repo / "conflict.txt").write_text("base\n", encoding="utf-8")
        git(repo, "add", "conflict.txt")
        git(repo, "commit", "-m", "conflict base")
        git(repo, "checkout", "-b", "codex/conflict")
        (repo / "conflict.txt").write_text("branch\n", encoding="utf-8")
        git(repo, "add", "conflict.txt")
        git(repo, "commit", "-m", "branch conflict")
        git(repo, "checkout", "master")
        (repo / "conflict.txt").write_text("target\n", encoding="utf-8")
        git(repo, "add", "conflict.txt")
        git(repo, "commit", "-m", "target conflict")

        result = repo_sweep(repo)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/conflict")
        self.assertEqual(report["recommendedAction"], "retain_with_proven_blocker")
        self.assertEqual(report["scope"]["mergeProbe"]["reason"], "merge_failed")
        self.assertEqual(report["scope"]["mergeProbe"]["conflicts"], ["conflict.txt"])
        self.assertEqual(report["scope"]["agentResolutionPacket"]["symbolicAction"], "resolve-conflicts-with-agent")

    def test_repo_sweep_retained_terminal_outcomes_prove_remediation_attempted_or_excluded(self) -> None:
        repo = self.init_repo()
        (repo / "conflict.txt").write_text("base\n", encoding="utf-8")
        git(repo, "add", "conflict.txt")
        git(repo, "commit", "-m", "conflict base")
        git(repo, "checkout", "-b", "codex/conflict-proof")
        (repo / "conflict.txt").write_text("branch\n", encoding="utf-8")
        git(repo, "add", "conflict.txt")
        git(repo, "commit", "-m", "branch conflict")
        git(repo, "checkout", "master")
        (repo / "conflict.txt").write_text("target\n", encoding="utf-8")
        git(repo, "add", "conflict.txt")
        git(repo, "commit", "-m", "target conflict")

        result = repo_sweep(repo)

        report = next(item for item in result["retainedCandidateReports"] if item["branch"] == "codex/conflict-proof")
        proof = report["remediationProof"]
        self.assertFalse(proof["symbolicRemediationAttempted"])
        self.assertTrue(proof["policyEligible"])
        self.assertEqual(proof["excludedByExactPolicy"], ["merge_failed"])

    def test_dirty_split_auto_remediates_owned_dirty_and_retains_foreign(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/split-owned")
        start_work_block(repo, work_block_id="wb-split-owned", actor="local-test", path_claims=["owned.txt"])
        (repo / "owned.txt").write_text("owned committed\n", encoding="utf-8")
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "owned committed")
        start_work_block(repo, work_block_id="wb-split-foreign-owner", actor="local-test", path_claims=["foreign.txt"])
        (repo / "owned.txt").write_text("owned dirty preserved\n", encoding="utf-8")
        (repo / "foreign.txt").write_text("foreign dirty retained\n", encoding="utf-8")

        result = preserve_owned_dirty_split(repo, work_block_id="wb-split-owned")

        self.assertEqual(result["status"], "success", result)
        preservation = result["results"][0]
        self.assertEqual((repo / "owned.txt").read_text(encoding="utf-8"), "owned committed\n")
        self.assertEqual((repo / "foreign.txt").read_text(encoding="utf-8"), "foreign dirty retained\n")
        self.assertEqual(git(repo, "show", f"{preservation['preservationBranch']}:owned.txt").stdout, "owned dirty preserved\n")
        self.assertIn("dirty_split_success", self.audit_types(repo))
        self.assertIn("auto_quorum", self.audit_types(repo))
        detection_after = detect_work_block(repo, work_block_id="wb-split-owned")
        self.assertFalse(detection_after["ownedDirty"])
        self.assertEqual([item["path"] for item in detection_after["foreignDirty"]], ["foreign.txt"])

    def test_dirty_split_stale_tuple_is_rejected_before_mutation(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/split-stale")
        start_work_block(repo, work_block_id="wb-split-stale", actor="local-test", path_claims=["owned.txt"])
        (repo / "owned.txt").write_text("owned committed\n", encoding="utf-8")
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "owned committed")
        (repo / "owned.txt").write_text("first dirty\n", encoding="utf-8")
        detection = detect_work_block(repo, work_block_id="wb-split-stale")
        candidate = plan_dirty_split_candidates(repo, load_closeout_config(repo), detection)[0]
        (repo / "owned.txt").write_text("second dirty drift\n", encoding="utf-8")

        result = apply_dirty_split_candidate(repo, candidate)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "stale_tuple")
        self.assertIn("dirty_split_stale_tuple", self.audit_types(repo))
        self.assertEqual((repo / "owned.txt").read_text(encoding="utf-8"), "second dirty drift\n")

    def test_dirty_split_reuses_feature_head_branch_with_sparse_worktree(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/split-retry")
        start_work_block(repo, work_block_id="wb-split-retry", actor="local-test", path_claims=["owned.txt"])
        (repo / "owned.txt").write_text("owned committed\n", encoding="utf-8")
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "owned committed")
        (repo / "owned.txt").write_text("owned dirty retry\n", encoding="utf-8")
        detection = detect_work_block(repo, work_block_id="wb-split-retry")
        candidate = plan_dirty_split_candidates(repo, load_closeout_config(repo), detection)[0]
        git(repo, "branch", candidate["preservationBranch"], detection["featureHead"])

        result = apply_dirty_split_candidate(repo, candidate)

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(git(repo, "show", f"{candidate['preservationBranch']}:owned.txt").stdout, "owned dirty retry\n")
        self.assertEqual((repo / "owned.txt").read_text(encoding="utf-8"), "owned committed\n")
        sparse = git(Path(result["preservationWorktree"]), "config", "--get", "core.sparseCheckout").stdout.strip()
        self.assertEqual(sparse, "true")

    def test_dirty_split_mutates_only_one_candidate_per_run_and_audits(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/split-once")
        start_work_block(repo, work_block_id="wb-split-once", actor="local-test", path_claims=["one.txt", "two.txt"])
        (repo / "one.txt").write_text("one committed\n", encoding="utf-8")
        (repo / "two.txt").write_text("two committed\n", encoding="utf-8")
        git(repo, "add", "one.txt", "two.txt")
        git(repo, "commit", "-m", "two owned files")
        (repo / "one.txt").write_text("one dirty\n", encoding="utf-8")
        (repo / "two.txt").write_text("two dirty\n", encoding="utf-8")

        result = preserve_owned_dirty_split(repo, work_block_id="wb-split-once")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(sorted(result["candidates"][0]["paths"]), ["one.txt", "two.txt"])
        split_branches = git(repo, "for-each-ref", "refs/heads/closeout/split", "--format=%(refname:short)").stdout.splitlines()
        self.assertEqual(len(split_branches), 1)
        self.assertIn("dirty_split_success", self.audit_types(repo))

    def test_repair_runs_dirty_split_before_owned_dirty_blocks(self) -> None:
        repo = self.init_repo(config_updates={"stashPolicy": {"allowOwnedDirtyCheckpoint": False}})
        git(repo, "checkout", "-b", "codex/split-repair")
        start_work_block(repo, work_block_id="wb-split-repair", actor="local-test", path_claims=["owned.txt"])
        (repo / "owned.txt").write_text("owned committed\n", encoding="utf-8")
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "owned committed")
        (repo / "owned.txt").write_text("owned dirty repair\n", encoding="utf-8")

        from .brokered_closeout import repair_eligibility

        result = repair_eligibility(repo, work_block_id="wb-split-repair")

        self.assertEqual(result["status"], "repaired", result)
        self.assertEqual(result["blockers"], [])
        self.assertEqual((repo / "owned.txt").read_text(encoding="utf-8"), "owned committed\n")
        self.assertIn("dirty_split_success", self.audit_types(repo))

    def test_repair_checkpoints_owned_dirty_while_retaining_foreign_dirty(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "codex/checkpoint-owned")
        start_work_block(repo, work_block_id="wb-checkpoint-owned", actor="local-test", path_claims=["owned.txt"])
        (repo / "owned.txt").write_text("owned committed\n", encoding="utf-8")
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "owned committed")
        start_work_block(repo, work_block_id="wb-checkpoint-foreign-owner", actor="local-test", path_claims=["foreign.txt"])
        (repo / "owned.txt").write_text("owned checkpointed\n", encoding="utf-8")
        (repo / "foreign.txt").write_text("foreign retained\n", encoding="utf-8")

        from .brokered_closeout import repair_eligibility

        result = repair_eligibility(repo, work_block_id="wb-checkpoint-owned")

        self.assertEqual(result["status"], "repaired", result)
        self.assertEqual(git(repo, "show", "HEAD:owned.txt").stdout, "owned checkpointed\n")
        self.assertEqual((repo / "foreign.txt").read_text(encoding="utf-8"), "foreign retained\n")
        self.assertIn("checkpoint_owned_dirty", self.audit_types(repo))
        self.assertIn("retained_foreign_dirty", self.audit_types(repo))

    def test_missing_evidence_is_generated_and_committed_before_publish(self) -> None:
        repo = self.init_repo(
            remote=True,
            config_updates={
                "evidenceRepair": {
                    "enabled": True,
                    "evidenceRoot": ".closeout-evidence",
                    "requiredArtifacts": ["metrics.json", "handoff.json", "session.json", "closeout.json"],
                    "requiredFor": ["publish_missing_upstream"],
                    "commitMessage": "test evidence repair",
                }
            },
        )
        self.make_feature(repo, "wb-evidence-repair")

        result = repair_eligibility(repo, work_block_id="wb-evidence-repair")

        self.assertEqual(result["status"], "repaired", result)
        actions = [item["action"] for item in result["actions"]]
        self.assertLess(actions.index("evidence_repair"), actions.index("publish_missing_upstream"))
        for artifact in ["metrics.json", "handoff.json", "session.json", "closeout.json"]:
            path = f".closeout-evidence/wb-evidence-repair/{artifact}"
            self.assertEqual(git(repo, "cat-file", "-e", f"HEAD:{path}", check=False).returncode, 0)
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "codex/test-work@{upstream}").stdout.strip(), "origin/codex/test-work")
        self.assertIn("evidence_repair", self.audit_types(repo))

    def test_evidence_repair_refuses_claimed_evidence_path(self) -> None:
        repo = self.init_repo(
            remote=True,
            config_updates={
                "evidenceRepair": {
                    "enabled": True,
                    "requiredFor": ["publish_missing_upstream"],
                }
            },
        )
        self.make_feature(repo, "wb-evidence-blocked")
        start_work_block(
            repo,
            work_block_id="wb-other-evidence-owner",
            actor="local-test",
            path_claims=[".closeout-evidence/wb-evidence-blocked/metrics.json"],
        )

        result = repair_eligibility(repo, work_block_id="wb-evidence-blocked")

        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any(str(item).startswith("evidenceRepairFailed") for item in result["blockers"]))
        self.assertIn("evidence_repair_blocked", self.audit_types(repo))


if __name__ == "__main__":
    unittest.main()
