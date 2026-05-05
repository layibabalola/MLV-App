from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .core import (
    HygieneError,
    canonical_json,
    deep_merge,
    normalize_rel,
    resolve_repo_root,
    run_command,
    run_git,
    sha256_text,
    utc_now,
    write_json,
)


BROKER_SCHEMA_VERSION = "1.0"
CONFIG_PATH = Path("closeout.config.json")
HIGH_IMPACT_ACTIONS = [
    "clean_integrate",
    "checkpoint-owned-dirty",
    "push_target",
    "delete_local_branch",
    "delete_remote_branch",
    "worktree_cleanup",
    "snapshot_prune",
    "orphan_quarantine",
    "stash_promote",
    "repo_sweep_prune_merged",
    "split",
]
REQUIRED_SCRIPT_NAMES = [
    "start-work-block.ps1",
    "work-block-complete.ps1",
    "detect-closeout.ps1",
    "repair-closeout.ps1",
    "publish-checkpoint.ps1",
    "finalize-closeout.ps1",
    "review-quorum.ps1",
    "orphan-quarantine.ps1",
    "audit-closeout.ps1",
    "repo-sweep-closeout.ps1",
    "remediate-retained-closeout.ps1",
]


DEFAULT_CLOSEOUT_CONFIG: Dict[str, Any] = {
    "schemaVersion": BROKER_SCHEMA_VERSION,
    "policyVersion": "2026-05-05.brokered-closeout",
    "stateRoot": ".claude-state/closeout",
    "git": {
        "targetBranch": "master",
        "remote": "fork",
        "allowLocalOnly": True,
        "protectedBranches": ["master", "main", "develop", "release/*"],
        "featureBranchPatterns": ["codex/*", "claude/*", "hygiene/*", "work/*", "feature/*"],
        "fetchBeforeEvidence": True,
    },
    "validation": {
        "commands": [
            {
                "name": "brokered-closeout-tests",
                "argv": ["py", "-3", "-m", "unittest", "tools.repo_hygiene.test_brokered_closeout", "-v"],
            },
            {
                "name": "repo-hygiene-policy",
                "argv": ["py", "-3", "tools/repo-hygiene/hygiene.py", "--repo-root", ".", "verify-policy"],
            },
        ]
    },
    "paths": {
        "generated": [
            ".claude-state/**",
            "**/__pycache__/**",
            "**/*.pyc",
            "**/build*/**",
            "platform/**/build*/**",
        ],
        "sensitive": [".claude/**", ".claude", ".git/**", ".git"],
        "state": ".claude-state/closeout",
    },
    "dirty": {
        "unclaimedOutsideDelta": "foreign",
        "sensitiveUnownedBlocks": True,
        "autoClaimCleanAtStart": True,
    },
    "dirtySplit": {
        "enabled": True,
        "autoRepairOwnedDirty": True,
        "autoCheckpointOwnedDirty": True,
        "branchPrefix": "closeout/split",
        "worktreeRoot": ".claude-state/closeout/dirty-splits/worktrees",
        "maxCandidatesPerRun": 1,
        "registerBrokerOwnership": True,
    },
    "stashPolicy": {
        "allowAutoStash": False,
        "allowForeignDirtyStash": False,
        "allowOwnedDirtyCheckpoint": True,
    },
    "cleanupPolicy": {
        "deleteLocalBranchAfterSuccess": True,
        "deleteRemoteBranchAfterSuccess": False,
        "pruneSnapshotsAfterSuccess": True,
        "removeIntegrationWorktreeAfterFailure": True,
        "retainOriginalWorktreeOnTreeMismatch": True,
        "removeCleanDetachedWorktreesInSweep": False,
        "dropStashesInSweep": False,
        "protectedWorktreeRoots": [".claude/worktrees/**"],
    },
    "responseHookLifecycle": {
        "skipSessionWorktreeSignal": "SkipSessionWorktree",
        "skipAuditField": "session_worktree_bootstrap",
        "readOnlyHookPhases": ["response", "final"],
        "managedSessionWorktreeRoots": [".codex-worktrees/**"],
        "bootstrapAllowedOnlyByExplicitStart": True,
    },
    "repair": {
        "autoPublishFeatureBranch": True,
        "allowedPublishRemotes": ["fork", "origin"],
        "setUpstreamOnPublish": True,
    },
    "toolingBaseline": {
        "enabled": False,
        "baselineRef": None,
        "autoUpdate": True,
        "paths": [
            "closeout.config.json",
            "tools/repo_hygiene/brokered_closeout.py",
            "tools/repo_hygiene/work_block_cli.py",
            "tools/repo_hygiene/test_brokered_closeout.py",
            "tools/agent-bridge/codex_pre_response.ps1",
            "tools/agent-bridge/codex_pre_final.ps1",
            "tools/agent-bridge/codex_bridge_reminder.ps1",
            "tools/repo-hygiene/closeout.contract.json",
            "tools/repo-hygiene/hygiene.config.json",
        ],
        "requiredConfigKeys": ["git", "validation", "paths", "dirtySplit", "toolingBaseline", "evidenceRepair", "stashPolicy", "cleanupPolicy", "reviewQuorum", "responseHookLifecycle", "blockerAutoRemediation"],
        "requiredHighImpactActions": ["clean_integrate", "checkpoint-owned-dirty", "delete_local_branch", "delete_remote_branch", "repo_sweep_prune_merged", "split"],
        "requiredAutoQuorumActions": [
            "integrated_branch_prune",
            "integrated_remote_feature_prune",
            "patch_equivalent_remote_feature_prune",
            "remote_feature_clean_integrate",
            "repo_sweep_clean_integrate",
            "owned_dirty_checkpoint",
            "dirty_split",
            "foreign_dirty_integrated_branch_prune",
            "detached_dirty_preserve",
            "redundant_branch_prune",
            "explicit_protected_worktree_cleanup",
        ],
        "requiredTests": [
            "test_dirty_split_auto_remediates_owned_dirty_and_retains_foreign",
            "test_dirty_split_stale_tuple_is_rejected_before_mutation",
            "test_repo_sweep_foreign_dirty_integrated_branch_switches_and_prunes",
            "test_repo_sweep_foreign_dirty_integrated_branch_detaches_linked_worktree_and_prunes",
            "test_repo_sweep_foreign_dirty_target_overlap_blocks_with_exact_path_evidence",
            "test_repo_sweep_detached_dirty_worktree_is_preserved_before_cleanup",
            "test_repo_sweep_detached_dirty_preservation_refuses_stale_or_missing_commit_before_cleanup",
            "test_repo_sweep_explicit_protected_stale_worktree_cleanup_requires_exact_policy",
            "test_repo_sweep_protected_locked_worktree_without_exact_policy_is_inspect_only",
            "test_repo_sweep_merge_failed_report_has_agent_resolution_packet",
            "test_finalize_auto_quorum_renews_stale_review_when_policy_allows",
            "test_stale_review_tuple_blocks_when_target_moves",
            "test_repo_sweep_patch_equivalent_non_backup_branch_auto_quorum_prunes",
            "test_repo_sweep_retained_terminal_outcomes_prove_remediation_attempted_or_excluded",
            "test_pre_response_broker_bootstrap_records_dirty_baseline_without_worktree",
            "test_clean_at_start_new_dirty_paths_auto_claimed_and_checkpointed_through_quorum",
            "test_baseline_dirty_claimed_path_blocks_as_mixed_and_not_checkpointed",
            "test_owned_dirty_checkpoint_stages_only_exact_owned_paths",
            "test_foreign_dirty_remains_retained_audited_and_does_not_block_independent_closeout",
            "test_completion_without_explicit_work_block_id_reports_deterministic_selection_reason",
            "test_clean_integration_worktree_add_uses_longpaths_config",
            "test_closeout_tooling_stale_blocks_before_hygiene_blocker",
            "test_missing_evidence_is_generated_and_committed_before_publish",
            "test_target_push_non_fast_forward_fetches_updates_local_target_and_reports_rerun",
            "test_remediate_retained_actor_applies_one_candidate_per_run",
            "test_repo_sweep_remote_integrated_feature_branch_is_pruned",
            "test_repo_sweep_remote_patch_equivalent_feature_branch_is_pruned",
            "test_repo_sweep_remote_unique_feature_branch_clean_integrates_and_prunes",
            "test_repo_sweep_remote_conflicting_feature_branch_writes_investigation_packet",
        ],
        "requiredSymbols": [
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def bootstrap_response_broker_manifest"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def checkpoint_owned_dirty_action_id"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def verify_detached_preservation_commit"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def repair_target_push_failure"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def verify_closeout_tooling_current"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def repair_missing_evidence"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def preserve_owned_dirty_split"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def apply_detached_dirty_preserve"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def cleanup_foreign_dirty_integrated_branch"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def remote_feature_rows"},
            {"path": "tools/repo_hygiene/brokered_closeout.py", "contains": "def remediate_retained_candidates"},
            {"path": "tools/agent-bridge/codex_pre_response.ps1", "contains": "bootstrap-response"},
            {"path": "tools/agent-bridge/codex_pre_response.ps1", "contains": "-SkipSessionWorktree"},
            {"path": "tools/agent-bridge/codex_pre_final.ps1", "contains": "-SkipSessionWorktree"},
            {"path": "tools/agent-bridge/codex_bridge_reminder.ps1", "contains": "session_worktree_bootstrap=skipped"},
        ],
    },
    "evidenceRepair": {
        "enabled": True,
        "evidenceRoot": ".closeout-evidence",
        "requiredArtifacts": ["metrics.json", "handoff.json", "session.json", "closeout.json"],
        "requiredFor": ["publish_missing_upstream", "publish_ahead_only", "final_push"],
        "commitMessage": "brokered closeout evidence repair",
    },
    "reviewQuorum": {
        "requiredApprovals": 1,
        "allowedReviewers": ["codex", "claude", "human", "local-test"],
        "highImpactActions": HIGH_IMPACT_ACTIONS,
        "tupleFields": ["candidateId", "actionId", "evidenceHash", "policyHash", "pinnedRefs"],
    },
    "autoQuorum": {
        "enabled": True,
        "requiredScore": 10,
        "allowStaleReviewRenewal": True,
        "reviewers": ["codex-self", "ancestry-safety-reviewer", "mutation-scope-reviewer"],
        "autonomousActionClasses": [
            "integrated_branch_prune",
            "integrated_remote_feature_prune",
            "patch_equivalent_remote_feature_prune",
            "remote_feature_clean_integrate",
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
        "manualOnlyActionClasses": [
            "protected_branch",
            "dirty_worktree",
            "locked_worktree",
            "ambiguous_merge_required",
            "active_locked_worktree",
            "unowned_dirty_triage",
        ],
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
        "fetchBeforeRemoteSweep": True,
        "remoteFeaturePatterns": [],
        "pruneRemoteFeatureBranches": True,
        "cleanIntegrateRemoteFeatureBranches": True,
        "deleteRemoteFeatureAfterCleanIntegrate": True,
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
    "scripts": REQUIRED_SCRIPT_NAMES,
}


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True))
        handle.write("\n")


def stable_hash(data: Any, length: int = 32) -> str:
    return sha256_text(canonical_json(data), length)


def parse_utc(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_closeout_config(repo_root_arg: Path) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    override = read_json(repo_root / CONFIG_PATH, {})
    if not isinstance(override, dict):
        raise HygieneError("closeout.config.json must contain a JSON object")
    config = deep_merge(DEFAULT_CLOSEOUT_CONFIG, override)
    state_root_value = normalize_rel(str(config.get("stateRoot") or ""))
    if not state_root_value.startswith(".claude-state/"):
        raise HygieneError("closeout stateRoot must stay under .claude-state/")
    config["policyHash"] = stable_hash(config, 32)
    return config


def config_path_value(config: Dict[str, Any], dotted: str) -> Any:
    value: Any = config
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def closeout_state_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return (repo_root / str(config.get("stateRoot", ".claude-state/closeout"))).resolve()


def work_blocks_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return closeout_state_root(repo_root, config) / "work-blocks"


def work_block_dir(repo_root: Path, config: Dict[str, Any], work_block_id: str) -> Path:
    safe = str(work_block_id).strip()
    if not safe or any(ch in safe for ch in "\\/"):
        raise HygieneError("invalid workBlockId")
    if ".." in safe:
        raise HygieneError("invalid workBlockId")
    return work_blocks_root(repo_root, config) / safe


def audit_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return closeout_state_root(repo_root, config) / "audits"


def reviews_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return closeout_state_root(repo_root, config) / "reviews"


def locks_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return closeout_state_root(repo_root, config) / "locks"


def path_matches_any(path: str, patterns: Iterable[str]) -> bool:
    value = normalize_rel(path)
    return any(fnmatch.fnmatch(value, normalize_rel(pattern)) for pattern in patterns)


def current_branch(repo_root: Path) -> Optional[str]:
    result = run_git(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def rev_parse(repo_root: Path, rev: str, *, required: bool = True) -> Optional[str]:
    result = run_git(repo_root, ["rev-parse", "--verify", rev])
    if result.returncode != 0:
        if required:
            raise HygieneError("git ref is missing: %s" % rev)
        return None
    return result.stdout.strip()


def git_stdout(repo_root: Path, args: Sequence[str], *, required: bool = True) -> str:
    result = run_git(repo_root, args)
    if result.returncode != 0:
        if required:
            raise HygieneError("git command failed: git %s\n%s" % (" ".join(args), result.stderr.strip()))
        return ""
    return result.stdout.strip()


def run_git_longpaths(repo_root: Path, args: Sequence[str], *, check: bool = False):
    return run_git(repo_root, ["-c", "core.longpaths=true", *args], check=check)


def remote_exists(repo_root: Path, remote: str) -> bool:
    return run_git(repo_root, ["remote", "get-url", remote]).returncode == 0


def is_protected_branch(config: Dict[str, Any], branch: Optional[str]) -> bool:
    if not branch:
        return False
    return path_matches_any(branch, config.get("git", {}).get("protectedBranches", []))


def branch_allowed_by_policy(config: Dict[str, Any], branch: str) -> bool:
    return path_matches_any(branch, config.get("git", {}).get("featureBranchPatterns", []))


def target_ref_for(repo_root: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    git_config = config.get("git", {})
    target_branch = str(git_config.get("targetBranch", "master"))
    remote = str(git_config.get("remote", "origin"))
    has_remote = remote_exists(repo_root, remote)
    if has_remote and bool(git_config.get("fetchBeforeEvidence", True)):
        run_git(repo_root, ["fetch", "--prune", remote, target_branch])
    remote_ref = f"refs/remotes/{remote}/{target_branch}"
    local_ref = f"refs/heads/{target_branch}"
    remote_head = rev_parse(repo_root, remote_ref, required=False) if has_remote else None
    local_head = rev_parse(repo_root, local_ref, required=False)
    if remote_head:
        return {
            "targetBranch": target_branch,
            "remote": remote,
            "mode": "remote",
            "ref": remote_ref,
            "head": remote_head,
            "localHead": local_head,
        }
    if local_head:
        return {
            "targetBranch": target_branch,
            "remote": remote if has_remote else None,
            "mode": "local",
            "ref": local_ref,
            "head": local_head,
            "localHead": local_head,
        }
    raise HygieneError("target branch is missing: %s" % target_branch)


def parse_status_paths(repo_root: Path) -> List[Dict[str, Any]]:
    result = run_git(repo_root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], check=True)
    raw = result.stdout
    if not raw:
        return []
    parts = raw.split("\0")
    entries: List[Dict[str, Any]] = []
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if not item:
            continue
        code = item[:2]
        path = item[3:]
        original = None
        if code[:1] in {"R", "C"} or code[1:2] in {"R", "C"}:
            if i < len(parts):
                original = parts[i]
                i += 1
        path = normalize_rel(path)
        if path:
            entries.append({"status": code, "path": path, "originalPath": normalize_rel(original or "") or None})
    return entries


def dirty_baseline_snapshot(repo_root: Path) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for entry in parse_status_paths(repo_root):
        path = str(entry["path"])
        entries.append(
            {
                "status": entry.get("status"),
                "path": path,
                "originalPath": entry.get("originalPath"),
                "contentSha256": file_content_hash(repo_root / path),
            }
        )
    entries = sorted(entries, key=lambda item: str(item["path"]))
    return {
        "capturedAt": utc_now(),
        "paths": [str(item["path"]) for item in entries],
        "entries": entries,
    }


def manifest_dirty_baseline_paths(manifest: Dict[str, Any]) -> set[str]:
    baseline = manifest.get("dirtyBaseline")
    if not isinstance(baseline, dict):
        return set()
    paths = baseline.get("paths")
    if isinstance(paths, list):
        return {normalize_rel(str(path)) for path in paths if normalize_rel(str(path))}
    entries = baseline.get("entries")
    if isinstance(entries, list):
        return {normalize_rel(str(item.get("path") if isinstance(item, dict) else "")) for item in entries}
    return set()


def manifest_has_dirty_baseline(manifest: Dict[str, Any]) -> bool:
    return isinstance(manifest.get("dirtyBaseline"), dict)


def baseline_dirty_recovery_command(paths: Sequence[str]) -> str:
    joined = " ".join(sorted({normalize_rel(str(path)) for path in paths if normalize_rel(str(path))}))
    return "split or checkpoint pre-existing dirty content for %s, then rerun work-block-complete -Finalize" % joined


def changed_paths_between(repo_root: Path, target_ref: str, feature_head: str) -> List[str]:
    base = git_stdout(repo_root, ["merge-base", target_ref, feature_head])
    result = run_git(repo_root, ["diff", "--name-only", f"{base}..{feature_head}"], check=True)
    return sorted({normalize_rel(line) for line in result.stdout.splitlines() if normalize_rel(line)})


def load_manifest(repo_root: Path, config: Dict[str, Any], work_block_id: str) -> Dict[str, Any]:
    manifest = read_json(work_block_dir(repo_root, config, work_block_id) / "manifest.json", {})
    if not manifest:
        raise HygieneError("work block manifest is missing: %s" % work_block_id)
    return manifest


def manifest_path_claims(manifest: Dict[str, Any]) -> List[str]:
    return sorted({normalize_rel(path) for path in manifest.get("pathClaims", []) if normalize_rel(str(path))})


def active_path_claims(repo_root: Path, config: Dict[str, Any]) -> Dict[str, str]:
    claims: Dict[str, str] = {}
    root = work_blocks_root(repo_root, config)
    if not root.exists():
        return claims
    for manifest_file in root.glob("*/manifest.json"):
        manifest = read_json(manifest_file, {})
        if manifest.get("state") not in {"active", "completed", "finalizing", "blocked"}:
            continue
        block_id = str(manifest.get("workBlockId") or manifest_file.parent.name)
        for claim in manifest_path_claims(manifest):
            claims[claim] = block_id
    return claims


def work_block_selection_key(manifest: Dict[str, Any]) -> Tuple[int, datetime, str]:
    state_rank = {"active": 4, "completed": 3, "finalizing": 2, "blocked": 1}
    updated = parse_utc(str(manifest.get("updatedAt") or manifest.get("startedAt") or ""))
    if updated is None:
        updated = datetime.min.replace(tzinfo=timezone.utc)
    return (state_rank.get(str(manifest.get("state")), 0), updated, str(manifest.get("workBlockId") or ""))


def work_block_selection_summary(manifest: Dict[str, Any], reason: str, candidate_count: int) -> Dict[str, Any]:
    return {
        "workBlockId": str(manifest.get("workBlockId") or ""),
        "reason": reason,
        "candidateCount": candidate_count,
        "state": manifest.get("state"),
        "updatedAt": manifest.get("updatedAt"),
    }


def attach_work_block_selection(manifest: Dict[str, Any], reason: str, candidate_count: int) -> Dict[str, Any]:
    selected = dict(manifest)
    selected["workBlockSelection"] = work_block_selection_summary(selected, reason, candidate_count)
    return selected


def append_event(repo_root: Path, config: Dict[str, Any], work_block_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    block_dir = work_block_dir(repo_root, config, work_block_id)
    events_path = block_dir / "events.jsonl"
    previous_hash: Optional[str] = None
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                previous_hash = str(row.get("eventHash") or stable_hash(row))
    row = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "eventId": str(uuid.uuid4()),
        "createdAt": utc_now(),
        "workBlockId": work_block_id,
        "previousEventHash": previous_hash,
        **event,
    }
    clean = dict(row)
    row["eventHash"] = stable_hash(clean)
    append_jsonl(events_path, row)
    append_jsonl(closeout_state_root(repo_root, config) / "ledger" / "events.jsonl", row)
    return row


@contextlib.contextmanager
def broker_lease(repo_root: Path, config: Dict[str, Any], name: str, lease_seconds: Optional[int] = None) -> Iterator[Path]:
    root = locks_root(repo_root, config)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ("%s.lock" % name)
    ttl = int(lease_seconds or 300)
    now_dt = datetime.now(timezone.utc)
    payload = {"pid": os.getpid(), "createdAt": utc_now(), "leaseSeconds": ttl}
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            existing = read_json(lock_path, {})
            created = parse_utc(str(existing.get("createdAt") or ""))
            existing_ttl = int(existing.get("leaseSeconds") or ttl)
            if created and created + timedelta(seconds=existing_ttl) <= now_dt:
                try:
                    lock_path.unlink()
                    continue
                except FileNotFoundError:
                    continue
            raise HygieneError("closeout lease is already held: %s" % lock_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
        yield lock_path
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def write_audit(
    repo_root: Path,
    config: Dict[str, Any],
    audit_type: str,
    payload: Dict[str, Any],
    *,
    work_block_id: Optional[str] = None,
    outcome: str = "recorded",
) -> Dict[str, Any]:
    row = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "auditType": audit_type,
        "outcome": outcome,
        "createdAt": utc_now(),
        "workBlockId": work_block_id,
        "policyHash": config.get("policyHash"),
        "payload": payload,
    }
    row["auditHash"] = stable_hash(row)
    name = "%s-%s-%s.json" % (
        row["createdAt"].replace(":", "").replace("+", "Z"),
        audit_type,
        row["auditHash"][:12],
    )
    write_json(audit_root(repo_root, config) / name, row)
    append_jsonl(audit_root(repo_root, config) / "audits.jsonl", row)
    return row


def start_work_block(
    repo_root_arg: Path,
    *,
    work_block_id: Optional[str] = None,
    actor: str = "codex",
    path_claims: Optional[Sequence[str]] = None,
    lease_seconds: int = 3600,
) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    branch = current_branch(repo_root)
    if not branch:
        raise HygieneError("work block start requires a named branch")
    if is_protected_branch(config, branch):
        raise HygieneError("work block cannot start on protected branch %s" % branch)
    block_id = work_block_id or ("wb-%s" % uuid.uuid4().hex[:16])
    claims = sorted({normalize_rel(path) for path in path_claims or [] if normalize_rel(str(path))})
    with broker_lease(repo_root, config, "broker", lease_seconds=30):
        block_dir = work_block_dir(repo_root, config, block_id)
        if (block_dir / "manifest.json").exists():
            raise HygieneError("work block already exists: %s" % block_id)
        head = rev_parse(repo_root, "HEAD")
        dirty_baseline = dirty_baseline_snapshot(repo_root)
        manifest = {
            "schemaVersion": BROKER_SCHEMA_VERSION,
            "workBlockId": block_id,
            "state": "active",
            "actor": actor,
            "branch": branch,
            "worktree": str(repo_root),
            "targetBranch": config.get("git", {}).get("targetBranch", "master"),
            "targetRemote": config.get("git", {}).get("remote", "origin"),
            "pathClaims": claims,
            "startedAt": utc_now(),
            "updatedAt": utc_now(),
            "lease": {
                "holder": actor,
                "seconds": lease_seconds,
                "createdAt": utc_now(),
            },
            "startHead": head,
            "dirtyBaseline": dirty_baseline,
        }
        write_json(block_dir / "manifest.json", manifest)
        append_event(
            repo_root,
            config,
            block_id,
            {
                "event": "work_block_started",
                "branch": branch,
                "head": head,
                "pathClaims": claims,
                "dirtyBaselinePaths": dirty_baseline["paths"],
            },
        )
    return {"status": "started", "workBlockId": block_id, "manifest": manifest}


def update_manifest(repo_root: Path, config: Dict[str, Any], work_block_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    manifest = load_manifest(repo_root, config, work_block_id)
    manifest.update(updates)
    manifest["updatedAt"] = utc_now()
    write_json(work_block_dir(repo_root, config, work_block_id) / "manifest.json", manifest)
    return manifest


def closeout_tooling_recovery_command() -> str:
    return "git status --short && git checkout <tooling-baseline> -- closeout.config.json tools/closeout tools/repo_hygiene tools/repo-hygiene && rerun closeout"


def baseline_ref_for_tooling(repo_root: Path, config: Dict[str, Any]) -> Optional[str]:
    baseline = config.get("toolingBaseline", {})
    configured = baseline.get("baselineRef")
    if configured:
        return str(configured)
    try:
        target = target_ref_for(repo_root, config)
    except HygieneError:
        return None
    return str(target["ref"])


def git_show_text(repo_root: Path, ref: str, path: str) -> Optional[str]:
    result = run_git(repo_root, ["show", f"{ref}:{path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def dirty_path_set(repo_root: Path) -> set[str]:
    return {entry["path"] for entry in parse_status_paths(repo_root)}


def can_update_tooling_path(repo_root: Path, config: Dict[str, Any], path: str, dirty_paths: set[str], claims: Dict[str, str]) -> Tuple[bool, Optional[str]]:
    normalized = normalize_rel(path)
    if normalized in dirty_paths:
        return False, "path_has_dirty_work"
    if normalized in claims:
        return False, "path_is_claimed_by_work_block:%s" % claims[normalized]
    local = repo_root / normalized
    if local.exists() and not local.is_file():
        return False, "path_is_not_regular_file"
    return True, None


def verify_closeout_tooling_current(repo_root_arg: Path, config: Optional[Dict[str, Any]] = None, *, attempt_update: bool = True) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = config or load_closeout_config(repo_root)
    baseline = config.get("toolingBaseline", {})
    if not bool(baseline.get("enabled", False)):
        return {"ok": True, "status": "disabled", "missing": [], "updated": []}

    missing: List[Dict[str, Any]] = []
    for key in baseline.get("requiredConfigKeys", []):
        if config_path_value(config, str(key)) is None:
            missing.append({"kind": "config_key", "key": str(key)})
    high_impact = set(config.get("reviewQuorum", {}).get("highImpactActions", []))
    for action in baseline.get("requiredHighImpactActions", []):
        if action not in high_impact:
            missing.append({"kind": "high_impact_action", "action": str(action)})
    autonomous = set(config.get("autoQuorum", {}).get("autonomousActionClasses", []))
    for action in baseline.get("requiredAutoQuorumActions", []):
        if action not in autonomous:
            missing.append({"kind": "auto_quorum_action", "action": str(action)})
    test_path = repo_root / "tools" / "repo_hygiene" / "test_brokered_closeout.py"
    test_text = test_path.read_text(encoding="utf-8") if test_path.exists() else ""
    for test_name in baseline.get("requiredTests", []):
        if str(test_name) not in test_text:
            missing.append({"kind": "test", "test": str(test_name), "path": normalize_rel(str(test_path.relative_to(repo_root))) if test_path.exists() else "tools/repo_hygiene/test_brokered_closeout.py"})
    for required in baseline.get("requiredSymbols", []):
        path = normalize_rel(str(required.get("path") or ""))
        contains = str(required.get("contains") or "")
        text = (repo_root / path).read_text(encoding="utf-8") if path and (repo_root / path).exists() else ""
        if not path or contains not in text:
            missing.append({"kind": "symbol", "path": path, "contains": contains})
    for script in REQUIRED_SCRIPT_NAMES:
        if not (repo_root / "tools" / "closeout" / script).exists():
            missing.append({"kind": "actor", "path": normalize_rel(f"tools/closeout/{script}")})

    updated: List[Dict[str, Any]] = []
    blocked_updates: List[Dict[str, Any]] = []
    if missing and attempt_update and bool(baseline.get("autoUpdate", True)):
        ref = baseline_ref_for_tooling(repo_root, config)
        dirty_paths = dirty_path_set(repo_root)
        claims = active_path_claims(repo_root, config)
        candidate_paths = sorted({normalize_rel(str(item.get("path") or item.get("key") or "")) for item in missing if item.get("path")})
        candidate_paths.extend(normalize_rel(str(path)) for path in baseline.get("paths", []) if normalize_rel(str(path)))
        for path in sorted({path for path in candidate_paths if path}):
            allowed, reason = can_update_tooling_path(repo_root, config, path, dirty_paths, claims)
            if not allowed:
                blocked_updates.append({"path": path, "reason": reason})
                continue
            if not ref:
                blocked_updates.append({"path": path, "reason": "no_tooling_baseline_ref"})
                continue
            content = git_show_text(repo_root, ref, path)
            if content is None:
                blocked_updates.append({"path": path, "reason": "path_missing_from_baseline", "baselineRef": ref})
                continue
            target = repo_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="")
            updated.append({"path": path, "baselineRef": ref})

    result = {
        "ok": not missing,
        "status": "current" if not missing else "closeout_tooling_stale",
        "missing": missing,
        "updated": updated,
        "blockedUpdates": blocked_updates,
        "recoveryCommand": closeout_tooling_recovery_command(),
        "authoritative": not missing,
    }
    if missing:
        write_audit(repo_root, config, "closeout_tooling_stale", result, outcome="blocked")
    return result


def branch_work_block_candidates(repo_root: Path, config: Dict[str, Any], branch: str) -> List[Dict[str, Any]]:
    root = work_blocks_root(repo_root, config)
    candidates: List[Dict[str, Any]] = []
    for manifest_file in sorted(root.glob("*/manifest.json")) if root.exists() else []:
        manifest = read_json(manifest_file, {})
        if manifest.get("branch") == branch and manifest.get("state") in {"active", "completed", "finalizing", "blocked"}:
            candidates.append(manifest)
    return candidates


def bootstrap_response_broker_manifest(
    repo_root_arg: Path,
    *,
    hook_phase: str = "response",
    actor: str = "codex-response-hook",
    path_claims: Optional[Sequence[str]] = None,
    lease_seconds: int = 3600,
) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    branch = current_branch(repo_root)
    if not branch:
        return {"status": "skipped", "reason": "detached_head"}
    if is_protected_branch(config, branch):
        return {"status": "skipped", "reason": "protected_branch", "branch": branch}
    claims = sorted({normalize_rel(path) for path in path_claims or [] if normalize_rel(str(path))})
    with broker_lease(repo_root, config, "broker", lease_seconds=30):
        candidates = branch_work_block_candidates(repo_root, config, branch)
        if candidates:
            selected = max(candidates, key=work_block_selection_key)
            block_id = str(selected.get("workBlockId") or "")
            updates: Dict[str, Any] = {
                "actor": selected.get("actor") or actor,
                "branch": branch,
                "worktree": str(repo_root),
                "lease": {"holder": actor, "seconds": lease_seconds, "createdAt": utc_now()},
                "responseBroker": {"hookPhase": hook_phase, "refreshedAt": utc_now()},
            }
            if claims:
                updates["pathClaims"] = sorted(set(manifest_path_claims(selected)).union(claims))
            if not manifest_has_dirty_baseline(selected):
                updates["dirtyBaseline"] = dirty_baseline_snapshot(repo_root)
            manifest = update_manifest(repo_root, config, block_id, updates)
            append_event(
                repo_root,
                config,
                block_id,
                {
                    "event": "response_broker_refreshed",
                    "hookPhase": hook_phase,
                    "selection": work_block_selection_summary(manifest, "selected_by_branch_state_updated_workBlockId", len(candidates)),
                    "dirtyBaselinePaths": manifest.get("dirtyBaseline", {}).get("paths", []),
                },
            )
            return {
                "status": "refreshed",
                "workBlockId": block_id,
                "manifest": manifest,
                "workBlockSelection": work_block_selection_summary(manifest, "selected_by_branch_state_updated_workBlockId", len(candidates)),
            }
        block_id = "wb-%s" % uuid.uuid4().hex[:16]
        head = rev_parse(repo_root, "HEAD")
        dirty_baseline = dirty_baseline_snapshot(repo_root)
        now = utc_now()
        manifest = {
            "schemaVersion": BROKER_SCHEMA_VERSION,
            "workBlockId": block_id,
            "state": "active",
            "actor": actor,
            "branch": branch,
            "worktree": str(repo_root),
            "targetBranch": config.get("git", {}).get("targetBranch", "master"),
            "targetRemote": config.get("git", {}).get("remote", "origin"),
            "pathClaims": claims,
            "startedAt": now,
            "updatedAt": now,
            "lease": {"holder": actor, "seconds": lease_seconds, "createdAt": now},
            "startHead": head,
            "dirtyBaseline": dirty_baseline,
            "responseBroker": {"hookPhase": hook_phase, "createdAt": now},
        }
        write_json(work_block_dir(repo_root, config, block_id) / "manifest.json", manifest)
        append_event(
            repo_root,
            config,
            block_id,
            {
                "event": "response_broker_bootstrapped",
                "hookPhase": hook_phase,
                "branch": branch,
                "head": head,
                "pathClaims": claims,
                "dirtyBaselinePaths": dirty_baseline["paths"],
            },
        )
        return {
            "status": "created",
            "workBlockId": block_id,
            "manifest": manifest,
            "workBlockSelection": work_block_selection_summary(manifest, "created_response_broker_manifest", 0),
        }


def ensure_work_block_for_current_branch(repo_root: Path, config: Dict[str, Any], work_block_id: Optional[str]) -> Dict[str, Any]:
    if work_block_id:
        return attach_work_block_selection(load_manifest(repo_root, config, work_block_id), "explicit_workBlockId", 1)
    branch = current_branch(repo_root)
    if branch:
        candidates = branch_work_block_candidates(repo_root, config, branch)
        if candidates:
            return attach_work_block_selection(max(candidates, key=work_block_selection_key), "selected_by_branch_state_updated_workBlockId", len(candidates))
    implicit = start_work_block(repo_root, actor="codex-implicit", path_claims=[])
    return attach_work_block_selection(implicit["manifest"], "created_implicit_work_block", 0)


def detect_work_block(repo_root_arg: Path, *, work_block_id: Optional[str] = None) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    manifest = ensure_work_block_for_current_branch(repo_root, config, work_block_id)
    block_id = str(manifest["workBlockId"])
    branch = str(manifest.get("branch") or current_branch(repo_root) or "")
    feature_head = rev_parse(repo_root, branch)
    target = target_ref_for(repo_root, config)
    delta_paths = changed_paths_between(repo_root, target["ref"], feature_head)
    dirty_entries = parse_status_paths(repo_root)
    dirty_paths = sorted({entry["path"] for entry in dirty_entries})
    all_claims = active_path_claims(repo_root, config)
    own_claims = set(manifest_path_claims(manifest))
    baseline_paths = manifest_dirty_baseline_paths(manifest)
    baseline_available = manifest_has_dirty_baseline(manifest)
    generated_patterns = config.get("paths", {}).get("generated", [])
    sensitive_patterns = config.get("paths", {}).get("sensitive", [])
    unclaimed_default = str(config.get("dirty", {}).get("unclaimedOutsideDelta", "foreign"))
    auto_claim_clean = bool(config.get("dirty", {}).get("autoClaimCleanAtStart", True))
    owned_dirty: List[Dict[str, Any]] = []
    foreign_dirty: List[Dict[str, Any]] = []
    unowned_dirty: List[Dict[str, Any]] = []
    mixed_dirty: List[Dict[str, Any]] = []
    for entry in dirty_entries:
        path = entry["path"]
        owner = all_claims.get(path)
        in_delta = path in delta_paths
        claimed_by_self = path in own_claims or owner == block_id
        dirty_at_baseline = path in baseline_paths
        generated = path_matches_any(path, generated_patterns)
        sensitive = path_matches_any(path, sensitive_patterns)
        enriched = {
            **entry,
            "ownerWorkBlockId": owner,
            "inCompletedBranchDelta": in_delta,
            "dirtyAtBrokerStart": dirty_at_baseline,
            "dirtyBaselineAvailable": baseline_available,
            "generated": generated,
            "sensitive": sensitive,
        }
        if owner and owner != block_id:
            enriched["classificationReason"] = "path is claimed by another work block"
            foreign_dirty.append(enriched)
        elif dirty_at_baseline and (in_delta or claimed_by_self):
            enriched["classificationReason"] = "baseline dirty path overlaps candidate delta or claim"
            enriched["blocker"] = "baseline-dirty-overlaps-candidate"
            enriched["recoveryCommand"] = baseline_dirty_recovery_command([path])
            mixed_dirty.append(enriched)
        elif in_delta or claimed_by_self:
            enriched["classificationReason"] = "path overlaps completed branch delta or block claim"
            owned_dirty.append(enriched)
        elif sensitive and bool(config.get("dirty", {}).get("sensitiveUnownedBlocks", True)):
            enriched["classificationReason"] = "unclaimed sensitive path"
            unowned_dirty.append(enriched)
        elif auto_claim_clean and baseline_available and not dirty_at_baseline and not generated:
            enriched["classificationReason"] = "path was clean or absent at broker dirty baseline"
            enriched["autoClaimedByDirtyBaseline"] = True
            owned_dirty.append(enriched)
        elif unclaimed_default == "foreign":
            enriched["classificationReason"] = "path is outside completed branch delta"
            foreign_dirty.append(enriched)
        else:
            enriched["classificationReason"] = "path cannot be attributed"
            unowned_dirty.append(enriched)
    pinned_refs = {
        "feature": {"branch": branch, "head": feature_head},
        "target": {
            "branch": target["targetBranch"],
            "remote": target.get("remote"),
            "ref": target["ref"],
            "head": target["head"],
            "mode": target["mode"],
        },
    }
    result = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "workBlockId": block_id,
        "branch": branch,
        "targetBranch": target["targetBranch"],
        "targetRemote": target.get("remote"),
        "localOnly": target["mode"] == "local" and not target.get("remote"),
        "featureHead": feature_head,
        "targetHead": target["head"],
        "pinnedRefs": pinned_refs,
        "committedDeltaPaths": delta_paths,
        "dirtyPaths": dirty_paths,
        "dirtyBaselinePaths": sorted(baseline_paths),
        "ownedDirty": owned_dirty,
        "mixedDirty": mixed_dirty,
        "unownedDirty": unowned_dirty,
        "foreignDirty": foreign_dirty,
        "eligible": not owned_dirty and not unowned_dirty and not mixed_dirty,
        "workBlockSelection": manifest.get("workBlockSelection"),
    }
    result["detectorHash"] = stable_hash(result)
    write_json(work_block_dir(repo_root, config, block_id) / "detector.json", result)
    append_event(repo_root, config, block_id, {"event": "detector_ran", "detectorHash": result["detectorHash"], "eligible": result["eligible"]})
    write_audit(repo_root, config, "detector", result, work_block_id=block_id, outcome="success" if result["eligible"] else "blocked")
    return result


def upstream_for(repo_root: Path, branch: str) -> Optional[str]:
    result = run_git(repo_root, ["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def ahead_behind(repo_root: Path, upstream: str) -> Dict[str, int]:
    ahead = git_stdout(repo_root, ["rev-list", "--count", f"{upstream}..HEAD"], required=False)
    behind = git_stdout(repo_root, ["rev-list", "--count", f"HEAD..{upstream}"], required=False)
    return {"ahead": int(ahead or "0"), "behind": int(behind or "0")}


def evidence_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    root = normalize_rel(str(config.get("evidenceRepair", {}).get("evidenceRoot") or ".closeout-evidence"))
    if not root or root.startswith("../") or root == ".." or root.startswith(".claude/"):
        raise HygieneError("invalid evidenceRepair.evidenceRoot: %s" % root)
    return (repo_root / root).resolve()


def evidence_rel_paths(config: Dict[str, Any], work_block_id: str) -> List[str]:
    root = normalize_rel(str(config.get("evidenceRepair", {}).get("evidenceRoot") or ".closeout-evidence")).strip("/")
    artifacts = [normalize_rel(str(item)) for item in config.get("evidenceRepair", {}).get("requiredArtifacts", [])]
    return [normalize_rel("%s/%s/%s" % (root, safe_state_name(work_block_id), artifact)) for artifact in artifacts if artifact]


def git_path_tracked(repo_root: Path, rel_path: str, rev: str = "HEAD") -> bool:
    return run_git(repo_root, ["cat-file", "-e", f"{rev}:{rel_path}"]).returncode == 0


def evidence_missing_or_dirty(repo_root: Path, config: Dict[str, Any], work_block_id: str) -> List[str]:
    paths = evidence_rel_paths(config, work_block_id)
    missing: List[str] = []
    for path in paths:
        status = run_git(repo_root, ["status", "--porcelain=v1", "--", path])
        if status.stdout.strip() or not git_path_tracked(repo_root, path):
            missing.append(path)
    return missing


def evidence_payloads(config: Dict[str, Any], detection: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    base = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "createdAt": utc_now(),
        "workBlockId": detection["workBlockId"],
        "branch": detection["branch"],
        "featureHead": detection["featureHead"],
        "targetHead": detection["targetHead"],
        "detectorHash": detection["detectorHash"],
    }
    return {
        "metrics.json": {
            **base,
            "artifactKind": "metrics",
            "dirtyCounts": {
                "ownedDirty": len(detection.get("ownedDirty", [])),
                "mixedDirty": len(detection.get("mixedDirty", [])),
                "foreignDirty": len(detection.get("foreignDirty", [])),
                "unownedDirty": len(detection.get("unownedDirty", [])),
            },
            "committedDeltaPathCount": len(detection.get("committedDeltaPaths", [])),
        },
        "handoff.json": {
            **base,
            "artifactKind": "handoff",
            "summary": "Generated by brokered closeout evidence repair before publish/final push.",
            "foreignDirtyRetained": [item["path"] for item in detection.get("foreignDirty", [])],
        },
        "session.json": {
            **base,
            "artifactKind": "session",
            "actor": "brokered-closeout",
            "worktree": str(Path.cwd()),
        },
        "closeout.json": {
            **base,
            "artifactKind": "closeout",
            "pinnedRefs": detection.get("pinnedRefs"),
            "eligible": detection.get("eligible"),
        },
    }


def repair_missing_evidence(repo_root_arg: Path, config: Dict[str, Any], detection: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    if not bool(config.get("evidenceRepair", {}).get("enabled", True)):
        return {"status": "disabled", "reason": "evidenceRepair.enabled is false"}
    work_block_id = str(detection["workBlockId"])
    paths = evidence_rel_paths(config, work_block_id)
    missing = evidence_missing_or_dirty(repo_root, config, work_block_id)
    if not missing:
        return {"status": "noop", "reason": "required_evidence_present", "paths": paths}
    claims = active_path_claims(repo_root, config)
    manifest = load_manifest(repo_root, config, work_block_id)
    own_claims = set(manifest_path_claims(manifest))
    blocked: List[Dict[str, Any]] = []
    for path in paths:
        owner = claims.get(path)
        if owner and owner != work_block_id:
            blocked.append({"path": path, "reason": "claimed_by_other_work_block", "ownerWorkBlockId": owner})
        if path_matches_any(path, config.get("paths", {}).get("sensitive", [])):
            blocked.append({"path": path, "reason": "evidence_path_is_sensitive"})
    if blocked:
        payload = {"reason": reason, "blocked": blocked, "paths": paths}
        write_audit(repo_root, config, "evidence_repair_blocked", payload, work_block_id=work_block_id, outcome="blocked")
        return {"status": "blocked", **payload}
    payloads = evidence_payloads(config, detection)
    root = evidence_root(repo_root, config)
    root.mkdir(parents=True, exist_ok=True)
    for rel_path in paths:
        artifact = Path(rel_path).name
        target = safe_repo_path(repo_root, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, payloads.get(artifact, {"schemaVersion": BROKER_SCHEMA_VERSION, "workBlockId": work_block_id, "artifactKind": artifact}))
    updated_claims = sorted(own_claims.union(paths))
    update_manifest(repo_root, config, work_block_id, {"pathClaims": updated_claims})
    add = run_git(repo_root, ["add", "--", *paths])
    if add.returncode != 0:
        result = {"status": "blocked", "reason": "evidence_git_add_failed", "paths": paths, "stderr": add.stderr[-3000:]}
        write_audit(repo_root, config, "evidence_repair_blocked", result, work_block_id=work_block_id, outcome="blocked")
        return result
    commit = run_git(repo_root, ["commit", "-m", str(config.get("evidenceRepair", {}).get("commitMessage") or "brokered closeout evidence repair")])
    if commit.returncode != 0:
        result = {"status": "blocked", "reason": "evidence_commit_failed", "paths": paths, "stdout": commit.stdout[-3000:], "stderr": commit.stderr[-3000:]}
        write_audit(repo_root, config, "evidence_repair_blocked", result, work_block_id=work_block_id, outcome="blocked")
        return result
    validations: List[Dict[str, Any]] = []
    if bool(config.get("evidenceRepair", {}).get("validateAfterCommit", False)):
        validations = run_validations(repo_root, config, repo_root)
        failed = next((item for item in validations if item["returncode"] != 0), None)
        if failed:
            result = {"status": "blocked", "reason": "evidence_validation_failed", "paths": paths, "validations": validations}
            write_audit(repo_root, config, "evidence_repair_blocked", result, work_block_id=work_block_id, outcome="blocked")
            return result
    result = {"status": "success", "reason": reason, "paths": paths, "commit": git_stdout(repo_root, ["rev-parse", "HEAD"]), "validations": validations}
    write_audit(repo_root, config, "evidence_repair", result, work_block_id=work_block_id, outcome="success")
    append_event(repo_root, config, work_block_id, {"event": "evidence_repaired", "reason": reason, "paths": paths, "commit": result["commit"]})
    return result


def ensure_evidence_for_repair(repo_root: Path, config: Dict[str, Any], detection: Dict[str, Any], reason: str, actions: List[Dict[str, Any]], blockers: List[str]) -> Dict[str, Any]:
    if not bool(config.get("evidenceRepair", {}).get("enabled", True)):
        return detection
    required_for = set(config.get("evidenceRepair", {}).get("requiredFor", []))
    if reason not in required_for:
        return detection
    missing = evidence_missing_or_dirty(repo_root, config, str(detection["workBlockId"]))
    if not missing:
        return detection
    evidence = repair_missing_evidence(repo_root, config, detection, reason=reason)
    actions.append({"action": "evidence_repair", **evidence})
    if evidence["status"] != "success":
        blockers.append("evidenceRepairFailed:%s" % reason)
        return detection
    return detect_work_block(repo_root, work_block_id=str(detection["workBlockId"]))


def repair_eligibility(repo_root_arg: Path, *, work_block_id: Optional[str] = None) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    tooling = verify_closeout_tooling_current(repo_root, config)
    if not tooling["ok"]:
        return {"status": "blocked", "reason": "closeout_tooling_stale", "tooling": tooling}
    detection = detect_work_block(repo_root, work_block_id=work_block_id)
    block_id = detection["workBlockId"]
    branch = detection["branch"]
    actions: List[Dict[str, Any]] = []
    blockers: List[str] = []
    if detection.get("mixedDirty"):
        paths = [item["path"] for item in detection.get("mixedDirty", [])]
        blockers.append("baseline-dirty-overlaps-candidate:%s" % ",".join(paths))
    if detection["ownedDirty"] and not detection.get("mixedDirty"):
        if bool(config.get("stashPolicy", {}).get("allowOwnedDirtyCheckpoint", True)):
            checkpoint = checkpoint_owned_work(repo_root, work_block_id=block_id)
            actions.append({"action": "checkpoint_owned_dirty", **checkpoint})
            if checkpoint.get("status") == "success":
                detection = detect_work_block(repo_root, work_block_id=block_id)
            if detection["ownedDirty"] and bool(config.get("dirtySplit", {}).get("autoRepairOwnedDirty", True)):
                split = preserve_owned_dirty_split(repo_root, work_block_id=block_id)
                actions.append({"action": "dirty_split", **split})
                detection = detect_work_block(repo_root, work_block_id=block_id)
            if detection["ownedDirty"]:
                blockers.append("ownedDirty_checkpoint_incomplete")
        elif bool(config.get("dirtySplit", {}).get("autoRepairOwnedDirty", True)):
            split = preserve_owned_dirty_split(repo_root, work_block_id=block_id)
            actions.append({"action": "dirty_split", **split})
            detection = detect_work_block(repo_root, work_block_id=block_id)
            if detection["ownedDirty"]:
                blockers.append("ownedDirty_split_incomplete")
        else:
            blockers.append("ownedDirty")
    if detection["unownedDirty"]:
        blockers.append("unownedDirty")
    detection = ensure_evidence_for_repair(repo_root, config, detection, "final_push", actions, blockers)
    remote = str(config.get("git", {}).get("remote", "origin"))
    has_remote = remote_exists(repo_root, remote)
    if not has_remote:
        if bool(config.get("git", {}).get("allowLocalOnly", False)):
            actions.append({"action": "no_origin_local_only", "status": "repaired", "reason": "local-only closeout allowed"})
        else:
            blockers.append("missingRemote")
    if has_remote and not is_protected_branch(config, branch):
        upstream = upstream_for(repo_root, branch)
        allowed_remotes = set(config.get("repair", {}).get("allowedPublishRemotes", []))
        if not upstream:
            if bool(config.get("repair", {}).get("autoPublishFeatureBranch", False)) and remote in allowed_remotes:
                detection = ensure_evidence_for_repair(repo_root, config, detection, "publish_missing_upstream", actions, blockers)
                if not any(str(item).startswith("evidenceRepairFailed") for item in blockers):
                    push_args = ["push"]
                    if bool(config.get("repair", {}).get("setUpstreamOnPublish", True)):
                        push_args.append("-u")
                    push_args.extend([remote, f"HEAD:{branch}"])
                    push = run_git(repo_root, push_args)
                    actions.append({"action": "publish_missing_upstream", "remote": remote, "branch": branch, "returncode": push.returncode})
                    if push.returncode != 0:
                        blockers.append("publishMissingUpstreamFailed")
            else:
                blockers.append("missingUpstream")
        else:
            ab = ahead_behind(repo_root, upstream)
            if ab["behind"] > 0:
                blockers.append("featureBehindUpstream")
            elif ab["ahead"] > 0 and bool(config.get("repair", {}).get("autoPublishFeatureBranch", False)):
                detection = ensure_evidence_for_repair(repo_root, config, detection, "publish_ahead_only", actions, blockers)
                if not any(str(item).startswith("evidenceRepairFailed") for item in blockers):
                    ab = ahead_behind(repo_root, upstream)
                    push = run_git(repo_root, ["push", remote, f"HEAD:{branch}"])
                    actions.append({"action": "publish_ahead_only", "remote": remote, "branch": branch, "aheadBehind": ab, "returncode": push.returncode})
                    if push.returncode != 0:
                        blockers.append("publishAheadOnlyFailed")
    status = "repaired" if not blockers else "blocked"
    payload = {"detectionHash": detection["detectorHash"], "actions": actions, "blockers": blockers}
    write_json(work_block_dir(repo_root, config, block_id) / "repair.json", payload)
    append_event(repo_root, config, block_id, {"event": "repair_ran", "status": status, "blockers": blockers})
    if blockers:
        write_audit(repo_root, config, "blocked_repair", payload, work_block_id=block_id, outcome="blocked")
    else:
        write_audit(repo_root, config, "repair", payload, work_block_id=block_id, outcome="success")
    return {"status": status, **payload}


def finalize_candidate_id(work_block_id: str) -> str:
    return "candidate:%s" % work_block_id


def finalize_action_id() -> str:
    return "clean_integrate"


def finalize_evidence(config: Dict[str, Any], detection: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "workBlockId": detection["workBlockId"],
        "detectorHash": detection["detectorHash"],
        "ownedDirty": detection["ownedDirty"],
        "mixedDirty": detection.get("mixedDirty", []),
        "unownedDirty": detection["unownedDirty"],
        "foreignDirty": detection["foreignDirty"],
        "committedDeltaPaths": detection["committedDeltaPaths"],
        "validationCommands": config.get("validation", {}).get("commands", []),
        "pinnedRefs": detection["pinnedRefs"],
    }


def review_tuple(candidate_id: str, action_id: str, evidence_hash: str, policy_hash: str, pinned_refs: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidateId": candidate_id,
        "actionId": action_id,
        "evidenceHash": evidence_hash,
        "policyHash": policy_hash,
        "pinnedRefs": pinned_refs,
    }


def review_tuple_hash(candidate_id: str, action_id: str, evidence_hash: str, policy_hash: str, pinned_refs: Dict[str, Any]) -> str:
    return stable_hash(review_tuple(candidate_id, action_id, evidence_hash, policy_hash, pinned_refs))


def record_review_approval(
    repo_root_arg: Path,
    *,
    candidate_id: str,
    action_id: str,
    evidence_hash: str,
    pinned_refs: Dict[str, Any],
    reviewer: str,
    approved: bool = True,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    tuple_hash = review_tuple_hash(candidate_id, action_id, evidence_hash, str(config.get("policyHash")), pinned_refs)
    safe_reviewer = sha256_text(str(reviewer), 16)
    record = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "reviewer": reviewer,
        "approved": bool(approved),
        "createdAt": utc_now(),
        "tuple": review_tuple(candidate_id, action_id, evidence_hash, str(config.get("policyHash")), pinned_refs),
        "tupleHash": tuple_hash,
        "details": details or {},
    }
    record["reviewHash"] = stable_hash(record)
    write_json(reviews_root(repo_root, config) / tuple_hash / ("%s.json" % safe_reviewer), record)
    write_audit(repo_root, config, "review_recorded", record, outcome="success")
    return record


def review_records(repo_root: Path, config: Dict[str, Any], candidate_id: str, action_id: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    root = reviews_root(repo_root, config)
    if not root.exists():
        return records
    for review_file in root.glob("*/*.json"):
        record = read_json(review_file, {})
        tup = record.get("tuple", {})
        if tup.get("candidateId") == candidate_id and tup.get("actionId") == action_id:
            records.append(record)
    return records


def review_packets_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return closeout_state_root(repo_root, config) / "review-packets"


def review_packet_dir(repo_root: Path, config: Dict[str, Any], tuple_hash: str) -> Path:
    return review_packets_root(repo_root, config) / tuple_hash


def unblock_detail(
    repo_root: Path,
    config: Dict[str, Any],
    *,
    candidate_id: str,
    action_id: str,
    evidence_hash: str,
    pinned_refs: Dict[str, Any],
    action_class: str,
    auto_unblock_allowed: bool,
    blockers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    tuple_hash = review_tuple_hash(candidate_id, action_id, evidence_hash, str(config.get("policyHash")), pinned_refs)
    packet_dir = review_packet_dir(repo_root, config, tuple_hash)
    return {
        "candidateId": candidate_id,
        "actionId": action_id,
        "evidenceHash": evidence_hash,
        "policyHash": config.get("policyHash"),
        "pinnedRefs": pinned_refs,
        "tupleHash": tuple_hash,
        "reviewPacketPath": str(packet_dir / "review-packet.json"),
        "allowedNextCommand": "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\closeout\\repo-sweep-closeout.ps1 -RepoRoot . -Apply",
        "allowedPhrase": "unblock closeout",
        "actionClass": action_class,
        "autoUnblockAllowed": auto_unblock_allowed,
        "blockers": list(blockers or []),
    }


def write_review_packet(
    repo_root: Path,
    config: Dict[str, Any],
    *,
    candidate_id: str,
    action_id: str,
    evidence_hash: str,
    pinned_refs: Dict[str, Any],
    evidence: Dict[str, Any],
    action_class: str,
    auto_unblock_allowed: bool,
    blockers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    detail = unblock_detail(
        repo_root,
        config,
        candidate_id=candidate_id,
        action_id=action_id,
        evidence_hash=evidence_hash,
        pinned_refs=pinned_refs,
        action_class=action_class,
        auto_unblock_allowed=auto_unblock_allowed,
        blockers=blockers,
    )
    packet = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "createdAt": utc_now(),
        "candidateId": candidate_id,
        "actionId": action_id,
        "evidenceHash": evidence_hash,
        "policyHash": config.get("policyHash"),
        "pinnedRefs": pinned_refs,
        "actionClass": action_class,
        "autoUnblockAllowed": auto_unblock_allowed,
        "blockers": list(blockers or []),
        "evidence": evidence,
        "unblockDetail": detail,
    }
    packet["packetHash"] = stable_hash(packet)
    packet_dir = review_packet_dir(repo_root, config, detail["tupleHash"])
    write_json(packet_dir / "review-packet.json", packet)
    return packet


def auto_quorum_allowed(config: Dict[str, Any], action_class: str, blockers: Optional[Sequence[str]] = None) -> bool:
    auto = config.get("autoQuorum", {})
    if not bool(auto.get("enabled", False)):
        return False
    if blockers:
        return False
    if action_class in set(auto.get("manualOnlyActionClasses", [])):
        return False
    return action_class in set(auto.get("autonomousActionClasses", []))


def autonomous_review_payloads(action_class: str, evidence: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    score = int(config.get("autoQuorum", {}).get("requiredScore", 10))
    reviewers = list(config.get("autoQuorum", {}).get("reviewers", [])) or ["codex-self"]
    rationales = {
        "codex-self": "Exact tuple, policy hash, and symbolic action were generated by the repo actor.",
        "ancestry-safety-reviewer": "The branch head is proven to be an ancestor of the pinned target head.",
        "mutation-scope-reviewer": "The action mutates only the approved local branch ref and retains worktrees/stashes.",
    }
    if action_class == "dirty_split":
        rationales = {
            "codex-self": "The repo actor generated an exact split tuple for owned dirty paths only.",
            "ancestry-safety-reviewer": "The preservation branch is pinned to the feature head before dirty paths are copied.",
            "mutation-scope-reviewer": "The action preserves exact paths before removing those same paths from the original worktree.",
        }
    elif action_class == "owned_dirty_checkpoint":
        rationales = {
            "codex-self": "The repo actor generated an exact checkpoint tuple for owned dirty paths only.",
            "ancestry-safety-reviewer": "The checkpoint is pinned to the current feature and target refs before staging.",
            "mutation-scope-reviewer": "The commit stages only the exact owned dirty paths named in the tuple.",
        }
    payloads: List[Dict[str, Any]] = []
    for reviewer in reviewers:
        payloads.append(
            {
                "reviewer": reviewer,
                "score": score,
                "approved": True,
                "autonomous": True,
                "actionClass": action_class,
                "rationale": rationales.get(reviewer, "Autonomous policy reviewer approved the exact tuple."),
                "evidenceSummary": {
                    "candidate": evidence.get("candidate"),
                    "target": evidence.get("target"),
                },
            }
        )
    return payloads


def ensure_autonomous_quorum(
    repo_root: Path,
    config: Dict[str, Any],
    *,
    candidate_id: str,
    action_id: str,
    evidence_hash: str,
    pinned_refs: Dict[str, Any],
    evidence: Dict[str, Any],
    action_class: str,
    blockers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    allowed = auto_quorum_allowed(config, action_class, blockers)
    packet = write_review_packet(
        repo_root,
        config,
        candidate_id=candidate_id,
        action_id=action_id,
        evidence_hash=evidence_hash,
        pinned_refs=pinned_refs,
        evidence=evidence,
        action_class=action_class,
        auto_unblock_allowed=allowed,
        blockers=blockers,
    )
    quorum = check_review_quorum(
        repo_root,
        config,
        candidate_id=candidate_id,
        action_id=action_id,
        evidence_hash=evidence_hash,
        pinned_refs=pinned_refs,
    )
    if quorum["ok"] or not allowed:
        return {"quorum": quorum, "packet": packet, "autoGenerated": False, "unblockDetail": packet["unblockDetail"]}
    reviews: List[Dict[str, Any]] = []
    for payload in autonomous_review_payloads(action_class, evidence, config):
        reviews.append(
            record_review_approval(
                repo_root,
                candidate_id=candidate_id,
                action_id=action_id,
                evidence_hash=evidence_hash,
                pinned_refs=pinned_refs,
                reviewer=str(payload["reviewer"]),
                approved=True,
                details=payload,
            )
        )
    quorum = check_review_quorum(
        repo_root,
        config,
        candidate_id=candidate_id,
        action_id=action_id,
        evidence_hash=evidence_hash,
        pinned_refs=pinned_refs,
    )
    manifest = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "createdAt": utc_now(),
        "candidateId": candidate_id,
        "actionId": action_id,
        "evidenceHash": evidence_hash,
        "policyHash": config.get("policyHash"),
        "pinnedRefs": pinned_refs,
        "actionClass": action_class,
        "reviewHashes": [review["reviewHash"] for review in reviews],
        "quorum": quorum,
    }
    manifest["manifestHash"] = stable_hash(manifest)
    packet_dir = review_packet_dir(repo_root, config, packet["unblockDetail"]["tupleHash"])
    write_json(packet_dir / "accepted-review-manifest.json", manifest)
    write_audit(repo_root, config, "auto_quorum", {"packet": packet, "manifest": manifest}, outcome="success" if quorum["ok"] else "blocked")
    return {"quorum": quorum, "packet": packet, "autoGenerated": True, "reviews": reviews, "manifest": manifest, "unblockDetail": packet["unblockDetail"]}


def dirty_split_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return closeout_state_root(repo_root, config) / "dirty-splits"


def dirty_split_worktree_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    configured = str(config.get("dirtySplit", {}).get("worktreeRoot") or ".claude-state/closeout/dirty-splits/worktrees")
    return (repo_root / configured).resolve()


def dirty_split_candidate_id(work_block_id: str, paths: Sequence[str], detector_hash: str) -> str:
    return "candidate:dirty-split:%s" % stable_hash({"workBlockId": work_block_id, "paths": sorted(paths), "detectorHash": detector_hash}, 16)


def dirty_split_branch_name(config: Dict[str, Any], work_block_id: str, candidate_id: str) -> str:
    prefix = normalize_rel(str(config.get("dirtySplit", {}).get("branchPrefix") or "closeout/split")).strip("/")
    return "%s/%s-%s" % (prefix, safe_state_name(work_block_id), stable_hash(candidate_id, 8))


def file_content_hash(path: Path) -> Optional[str]:
    if not path.exists() or path.is_dir():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dirty_path_fingerprints(repo_root: Path, entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        path = str(entry["path"])
        rows.append(
            {
                "path": path,
                "status": entry.get("status"),
                "contentSha256": file_content_hash(safe_repo_path(repo_root, path)),
            }
        )
    return rows


def verify_detached_preservation_commit(repo_root: Path, *, branch: str, commit_head: str, entries_by_path: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    branch_head = rev_parse(repo_root, f"refs/heads/{branch}", required=False)
    missing: List[str] = []
    unexpected_present: List[str] = []
    for path, entry in sorted(entries_by_path.items()):
        exists = run_git(repo_root, ["cat-file", "-e", f"{commit_head}:{path}"]).returncode == 0
        if "D" in str(entry.get("status") or ""):
            if exists:
                unexpected_present.append(path)
        elif not exists:
            missing.append(path)
    ok = branch_head == commit_head and not missing and not unexpected_present
    return {
        "ok": ok,
        "branch": branch,
        "expectedHead": commit_head,
        "actualHead": branch_head,
        "missingPaths": missing,
        "unexpectedPresentDeletedPaths": unexpected_present,
    }


def dirty_split_evidence(repo_root: Path, config: Dict[str, Any], detection: Dict[str, Any], paths: Sequence[str]) -> Dict[str, Any]:
    path_set = set(paths)
    owned_entries = [item for item in detection["ownedDirty"] if item["path"] in path_set]
    return {
        "workBlockId": detection["workBlockId"],
        "branch": detection["branch"],
        "featureHead": detection["featureHead"],
        "targetHead": detection["targetHead"],
        "detectorHash": detection["detectorHash"],
        "paths": sorted(paths),
        "ownedDirty": owned_entries,
        "dirtyPathFingerprints": dirty_path_fingerprints(repo_root, owned_entries),
        "foreignDirtyCount": len(detection.get("foreignDirty", [])),
        "unownedDirtyCount": len(detection.get("unownedDirty", [])),
        "policy": {
            "branchPrefix": config.get("dirtySplit", {}).get("branchPrefix"),
            "registerBrokerOwnership": config.get("dirtySplit", {}).get("registerBrokerOwnership", True),
            "maxCandidatesPerRun": config.get("dirtySplit", {}).get("maxCandidatesPerRun", 1),
        },
    }


def plan_dirty_split_candidates(repo_root: Path, config: Dict[str, Any], detection: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not bool(config.get("dirtySplit", {}).get("enabled", True)):
        return []
    if detection.get("unownedDirty") or detection.get("mixedDirty"):
        return []
    paths = sorted({item["path"] for item in detection.get("ownedDirty", [])})
    if not paths:
        return []
    candidate_id = dirty_split_candidate_id(str(detection["workBlockId"]), paths, str(detection["detectorHash"]))
    branch = dirty_split_branch_name(config, str(detection["workBlockId"]), candidate_id)
    pinned_refs = {
        "feature": detection["pinnedRefs"]["feature"],
        "target": detection["pinnedRefs"]["target"],
        "dirtyPaths": paths,
    }
    evidence = dirty_split_evidence(repo_root, config, detection, paths)
    candidate = {
        "candidateId": candidate_id,
        "actionId": "split",
        "actionClass": "dirty_split",
        "workBlockId": detection["workBlockId"],
        "sourceBranch": detection["branch"],
        "preservationBranch": branch,
        "paths": paths,
        "evidence": evidence,
        "evidenceHash": stable_hash(evidence),
        "pinnedRefs": pinned_refs,
    }
    return [candidate]


def safe_repo_path(repo_root: Path, rel_path: str) -> Path:
    normalized = normalize_rel(rel_path)
    if not normalized or normalized.startswith("../") or normalized == "..":
        raise HygieneError("unsafe repo path: %s" % rel_path)
    resolved = (repo_root / normalized).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise HygieneError("path escapes repo root: %s" % rel_path) from exc
    return resolved


def copy_exact_path_for_split(source_root: Path, dest_root: Path, rel_path: str, status: str) -> Dict[str, Any]:
    source = safe_repo_path(source_root, rel_path)
    dest = safe_repo_path(dest_root, rel_path)
    if "D" in status:
        if dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists():
            dest.unlink()
        return {"path": rel_path, "operation": "delete_in_preservation"}
    if not source.exists():
        raise HygieneError("dirty split source path is missing: %s" % rel_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        return {"path": rel_path, "operation": "copy_directory"}
    shutil.copy2(source, dest)
    return {"path": rel_path, "operation": "copy_file"}


def remove_exact_path_from_original(repo_root: Path, rel_path: str) -> Dict[str, Any]:
    path = safe_repo_path(repo_root, rel_path)
    tracked = run_git(repo_root, ["ls-files", "--error-unmatch", "--", rel_path])
    if tracked.returncode == 0:
        restore = run_git(repo_root, ["restore", "--staged", "--worktree", "--", rel_path])
        return {"path": rel_path, "operation": "restore_tracked", "returncode": restore.returncode, "stderr": restore.stderr[-2000:]}
    if path.is_dir():
        shutil.rmtree(path)
        return {"path": rel_path, "operation": "remove_untracked_directory", "returncode": 0, "stderr": ""}
    if path.exists():
        path.unlink()
    return {"path": rel_path, "operation": "remove_untracked_file", "returncode": 0, "stderr": ""}


def remove_exact_path_from_worktree(worktree_path: Path, rel_path: str) -> Dict[str, Any]:
    path = safe_repo_path(worktree_path, rel_path)
    tracked = run_git(worktree_path, ["ls-files", "--error-unmatch", "--", rel_path])
    if tracked.returncode == 0:
        restore = run_git(worktree_path, ["restore", "--staged", "--worktree", "--", rel_path])
        return {"path": rel_path, "operation": "restore_tracked", "returncode": restore.returncode, "stderr": restore.stderr[-2000:]}
    if path.is_dir():
        shutil.rmtree(path)
        return {"path": rel_path, "operation": "remove_untracked_directory", "returncode": 0, "stderr": ""}
    if path.exists():
        path.unlink()
    return {"path": rel_path, "operation": "remove_untracked_file", "returncode": 0, "stderr": ""}


def register_dirty_split_manifest(
    repo_root: Path,
    config: Dict[str, Any],
    *,
    candidate: Dict[str, Any],
    worktree_path: Path,
    commit_head: str,
) -> Dict[str, Any]:
    split_id = "split-%s" % stable_hash({"candidateId": candidate["candidateId"], "head": commit_head}, 16)
    manifest = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "workBlockId": split_id,
        "actor": "dirty-split-actor",
        "branch": candidate["preservationBranch"],
        "baseBranch": candidate["sourceBranch"],
        "baseHead": candidate["pinnedRefs"]["feature"]["head"],
        "worktree": str(worktree_path),
        "pathClaims": sorted(candidate["paths"]),
        "state": "completed",
        "createdAt": utc_now(),
        "completedAt": utc_now(),
        "sourceCandidateId": candidate["candidateId"],
    }
    block_dir = work_block_dir(repo_root, config, split_id)
    block_dir.mkdir(parents=True, exist_ok=True)
    write_json(block_dir / "manifest.json", manifest)
    append_event(repo_root, config, split_id, {"event": "dirty_split_preserved", "candidateId": candidate["candidateId"], "paths": candidate["paths"], "head": commit_head})
    return manifest


def apply_dirty_split_candidate(repo_root_arg: Path, candidate: Dict[str, Any]) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    detection = detect_work_block(repo_root, work_block_id=str(candidate["workBlockId"]))
    matching = [
        item
        for item in plan_dirty_split_candidates(repo_root, config, detection)
        if item["candidateId"] == candidate["candidateId"]
    ]
    if not matching:
        payload = {"candidate": candidate, "actualDetectionHash": detection.get("detectorHash"), "detailReason": "candidate_no_longer_current"}
        write_audit(repo_root, config, "dirty_split_stale_tuple", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
        return {"status": "blocked", "reason": "stale_tuple", **payload}
    current = matching[0]
    if current["evidenceHash"] != candidate["evidenceHash"] or current["pinnedRefs"] != candidate["pinnedRefs"]:
        payload = {"expected": candidate, "actual": current, "detailReason": "dirty_split_tuple_drifted"}
        write_audit(repo_root, config, "dirty_split_stale_tuple", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
        return {"status": "blocked", "reason": "stale_tuple", **payload}
    blockers: List[str] = []
    if detection.get("unownedDirty"):
        blockers.append("unownedDirty")
    if detection.get("mixedDirty"):
        blockers.append("baseline-dirty-overlaps-candidate")
    missing_paths = [path for path in candidate["paths"] if not any(item["path"] == path for item in detection.get("ownedDirty", []))]
    if missing_paths:
        blockers.append("candidate_paths_not_owned_dirty")
    quorum_result = ensure_autonomous_quorum(
        repo_root,
        config,
        candidate_id=candidate["candidateId"],
        action_id=candidate["actionId"],
        evidence_hash=candidate["evidenceHash"],
        pinned_refs=candidate["pinnedRefs"],
        evidence=candidate["evidence"],
        action_class=candidate["actionClass"],
        blockers=blockers,
    )
    if not quorum_result["quorum"]["ok"]:
        payload = {"candidate": candidate, "quorum": quorum_result}
        write_audit(repo_root, config, "review_quorum_blocked", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
        return {"status": "blocked", "reason": quorum_result["quorum"]["reason"] or "review_quorum_blocked", **payload}
    latest_detection = detect_work_block(repo_root, work_block_id=str(candidate["workBlockId"]))
    latest_candidates = [
        item
        for item in plan_dirty_split_candidates(repo_root, config, latest_detection)
        if item["candidateId"] == candidate["candidateId"]
    ]
    if not latest_candidates or latest_candidates[0]["evidenceHash"] != candidate["evidenceHash"] or latest_candidates[0]["pinnedRefs"] != candidate["pinnedRefs"]:
        payload = {"candidate": candidate, "latestDetectionHash": latest_detection.get("detectorHash"), "detailReason": "tuple_drifted_after_quorum"}
        write_audit(repo_root, config, "dirty_split_stale_tuple", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
        return {"status": "blocked", "reason": "stale_tuple", **payload}
    worktree_path = dirty_split_worktree_root(repo_root, config) / safe_state_name(candidate["preservationBranch"])
    branch = str(candidate["preservationBranch"])
    existing_branch_head = rev_parse(repo_root, f"refs/heads/{branch}", required=False)
    if existing_branch_head and existing_branch_head != str(candidate["pinnedRefs"]["feature"]["head"]):
        payload = {"candidate": candidate, "branch": branch, "head": existing_branch_head, "reason": "preservation_branch_exists"}
        write_audit(repo_root, config, "dirty_split_blocked_preservation", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
        return {"status": "blocked", "reason": "preservation_branch_exists", **payload}
    if worktree_path.exists():
        payload = {"candidate": candidate, "worktreePath": str(worktree_path), "reason": "preservation_worktree_exists"}
        write_audit(repo_root, config, "dirty_split_blocked_preservation", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
        return {"status": "blocked", "reason": "preservation_worktree_exists", **payload}
    copied: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    add_args = ["worktree", "add", "--no-checkout"]
    if existing_branch_head:
        add_args.extend([str(worktree_path), branch])
    else:
        add_args.extend(["-b", branch, str(worktree_path), str(candidate["pinnedRefs"]["feature"]["head"])])
    add_worktree = run_git_longpaths(repo_root, add_args)
    if add_worktree.returncode != 0:
        payload = {"candidate": candidate, "operation": "worktree_add", "returncode": add_worktree.returncode, "stderr": add_worktree.stderr[-3000:]}
        write_audit(repo_root, config, "dirty_split_blocked_preservation", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
        return {"status": "blocked", "reason": "preservation_worktree_failed", **payload}
    for operation, args in [
        ("sparse_checkout_init", ["sparse-checkout", "init", "--no-cone"]),
        ("sparse_checkout_set", ["sparse-checkout", "set", "--no-cone", *candidate["paths"]]),
        ("sparse_checkout_checkout", ["checkout"]),
    ]:
        result = run_git(worktree_path, args)
        if result.returncode != 0:
            cleanup = remove_worktree(repo_root, worktree_path)
            payload = {
                "candidate": candidate,
                "operation": operation,
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-3000:],
                "cleanup": cleanup,
            }
            write_audit(repo_root, config, "dirty_split_blocked_preservation", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
            return {"status": "blocked", "reason": "preservation_sparse_checkout_failed", **payload}
    try:
        entries_by_path = {item["path"]: item for item in latest_detection.get("ownedDirty", [])}
        for path in candidate["paths"]:
            copied.append(copy_exact_path_for_split(repo_root, worktree_path, path, str(entries_by_path[path]["status"])))
        add = run_git(worktree_path, ["add", "--", *candidate["paths"]])
        if add.returncode != 0:
            payload = {"candidate": candidate, "operation": "git_add_preservation", "returncode": add.returncode, "stderr": add.stderr[-3000:], "copied": copied}
            write_audit(repo_root, config, "dirty_split_blocked_preservation", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
            return {"status": "blocked", "reason": "preservation_add_failed", **payload}
        commit = run_git(worktree_path, ["commit", "-m", "preserve dirty split for %s" % candidate["workBlockId"]])
        if commit.returncode != 0:
            payload = {"candidate": candidate, "operation": "git_commit_preservation", "returncode": commit.returncode, "stdout": commit.stdout[-3000:], "stderr": commit.stderr[-3000:], "copied": copied}
            write_audit(repo_root, config, "dirty_split_blocked_preservation", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
            return {"status": "blocked", "reason": "preservation_commit_failed", **payload}
        commit_head = git_stdout(worktree_path, ["rev-parse", "HEAD"])
        manifest = None
        if bool(config.get("dirtySplit", {}).get("registerBrokerOwnership", True)):
            manifest = register_dirty_split_manifest(repo_root, config, candidate=candidate, worktree_path=worktree_path, commit_head=commit_head)
        for path in candidate["paths"]:
            removal = remove_exact_path_from_original(repo_root, path)
            removed.append(removal)
            if removal.get("returncode") != 0:
                payload = {"candidate": candidate, "preservationHead": commit_head, "removed": removed, "manifest": manifest}
                write_audit(repo_root, config, "dirty_split_partial_recovery", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
                return {"status": "blocked", "reason": "original_cleanup_failed", **payload}
        payload = {
            "candidate": candidate,
            "preservationBranch": branch,
            "preservationWorktree": str(worktree_path),
            "preservationHead": commit_head,
            "copied": copied,
            "removedFromOriginal": removed,
            "manifest": manifest,
        }
        write_audit(repo_root, config, "dirty_split_success", payload, work_block_id=str(candidate["workBlockId"]), outcome="success")
        return {"status": "success", **payload}
    except Exception as exc:
        payload = {"candidate": candidate, "copied": copied, "removed": removed, "error": str(exc)}
        write_audit(repo_root, config, "dirty_split_partial_recovery", payload, work_block_id=str(candidate["workBlockId"]), outcome="blocked")
        return {"status": "blocked", "reason": "dirty_split_exception", **payload}


def preserve_owned_dirty_split(repo_root_arg: Path, *, work_block_id: Optional[str] = None) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    detection = detect_work_block(repo_root, work_block_id=work_block_id)
    candidates = plan_dirty_split_candidates(repo_root, config, detection)
    limit = max(1, int(config.get("dirtySplit", {}).get("maxCandidatesPerRun", 1)))
    candidates = candidates[:limit]
    if not candidates:
        if detection.get("mixedDirty"):
            payload = {"reason": "baseline-dirty-overlaps-candidate", "mixedDirty": detection["mixedDirty"]}
            write_audit(repo_root, config, "dirty_split_blocked_preservation", payload, work_block_id=detection["workBlockId"], outcome="blocked")
            return {"status": "blocked", **payload}
        if detection.get("unownedDirty"):
            payload = {"reason": "unowned_dirty_requires_triage", "unownedDirty": detection["unownedDirty"]}
            write_audit(repo_root, config, "dirty_split_blocked_preservation", payload, work_block_id=detection["workBlockId"], outcome="blocked")
            return {"status": "blocked", **payload}
        return {"status": "noop", "reason": "no_owned_dirty_split_candidate"}
    results = [apply_dirty_split_candidate(repo_root, candidate) for candidate in candidates]
    status = "success" if all(item["status"] == "success" for item in results) else "blocked"
    return {"status": status, "candidates": candidates, "results": results}


def check_review_quorum(
    repo_root: Path,
    config: Dict[str, Any],
    *,
    candidate_id: str,
    action_id: str,
    evidence_hash: str,
    pinned_refs: Dict[str, Any],
) -> Dict[str, Any]:
    required = int(config.get("reviewQuorum", {}).get("requiredApprovals", 1))
    high_impact = set(config.get("reviewQuorum", {}).get("highImpactActions", HIGH_IMPACT_ACTIONS))
    tuple_hash = review_tuple_hash(candidate_id, action_id, evidence_hash, str(config.get("policyHash")), pinned_refs)
    records = review_records(repo_root, config, candidate_id, action_id)
    matching = [record for record in records if record.get("tupleHash") == tuple_hash and record.get("approved") is True]
    stale = [record for record in records if record.get("tupleHash") != tuple_hash]
    ok = action_id not in high_impact or len(matching) >= required
    reason = None
    if not ok:
        reason = "stale_review" if stale else "review_quorum_missing"
    return {
        "ok": ok,
        "requiredApprovals": required,
        "matchingApprovals": len(matching),
        "tupleHash": tuple_hash,
        "staleReviewCount": len(stale),
        "reason": reason,
    }


def run_validations(repo_root: Path, config: Dict[str, Any], integration_path: Path) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for command in config.get("validation", {}).get("commands", []):
        argv = command.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
            raise HygieneError("validation command argv must be a non-empty string array")
        argv = list(argv)
        if argv[0] == "python":
            argv[0] = sys.executable
        completed = run_command(argv, cwd=integration_path)
        results.append(
            {
                "name": command.get("name") or argv[0],
                "argv": argv,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
        if completed.returncode != 0:
            break
    return results


def tree_hash(repo_root: Path, rev: str) -> str:
    return git_stdout(repo_root, ["rev-parse", f"{rev}^{{tree}}"])


def is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo_root, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def remove_worktree(repo_root: Path, path: Path) -> Dict[str, Any]:
    result = run_git_longpaths(repo_root, ["worktree", "remove", "--force", "--force", str(path)])
    if result.returncode != 0 and path.exists():
        shutil.rmtree(path, ignore_errors=True)
    prune = run_git_longpaths(repo_root, ["worktree", "prune"])
    return {"path": str(path), "returncode": result.returncode, "stderr": result.stderr[-2000:], "pruneReturncode": prune.returncode, "pruneStderr": prune.stderr[-2000:]}


def update_local_target(repo_root: Path, target_branch: str, new_head: str) -> Dict[str, Any]:
    result = run_git(repo_root, ["update-ref", f"refs/heads/{target_branch}", new_head])
    return {"targetBranch": target_branch, "newHead": new_head, "returncode": result.returncode, "stderr": result.stderr[-2000:]}


def push_failed_non_fast_forward(push_result: Dict[str, Any]) -> bool:
    text = "%s\n%s" % (push_result.get("stdout") or "", push_result.get("stderr") or "")
    lowered = text.lower()
    return "non-fast-forward" in lowered or "updates were rejected" in lowered or "fetch first" in lowered


def target_push_recovery_command(remote: str, target_branch: str) -> Dict[str, Any]:
    return {
        "fetch": "git fetch %s %s" % (remote, target_branch),
        "integrateLocalTarget": "switch to %s, then run git merge --ff-only %s/%s" % (target_branch, remote, target_branch),
        "rerunCloseout": "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\closeout\\work-block-complete.ps1 -RepoRoot . -Finalize",
        "raceAdvice": "If another closeout keeps moving %s/%s, wait for it to finish, fetch again, then rerun closeout." % (remote, target_branch),
    }


def repair_target_push_failure(
    repo_root: Path,
    config: Dict[str, Any],
    *,
    target_branch: str,
    remote: str,
    attempted_head: str,
    push_result: Dict[str, Any],
    work_block_id: Optional[str] = None,
) -> Dict[str, Any]:
    command = target_push_recovery_command(remote, target_branch)
    recovery: Dict[str, Any] = {
        "status": "blocked",
        "reason": "target_push_failed",
        "recoveryCommand": command,
        "safeRecovery": command,
        "push": push_result,
        "remote": remote,
        "targetBranch": target_branch,
        "attemptedHead": attempted_head,
    }
    if not push_failed_non_fast_forward(push_result):
        write_audit(repo_root, config, "target_push_recovery", recovery, work_block_id=work_block_id, outcome="blocked")
        return recovery
    fetch = run_git(repo_root, ["fetch", "--prune", remote, target_branch])
    remote_ref = f"refs/remotes/{remote}/{target_branch}"
    local_ref = f"refs/heads/{target_branch}"
    remote_head = rev_parse(repo_root, remote_ref, required=False)
    local_head = rev_parse(repo_root, local_ref, required=False)
    recovery.update(
        {
            "reason": "target_push_non_fast_forward",
            "fetch": {"returncode": fetch.returncode, "stdout": fetch.stdout[-2000:], "stderr": fetch.stderr[-2000:]},
            "remoteHeadAfterFetch": remote_head,
            "localHeadBeforeUpdate": local_head,
        }
    )
    if fetch.returncode != 0:
        recovery["reason"] = "target_push_recovery_fetch_failed"
        write_audit(repo_root, config, "target_push_recovery", recovery, work_block_id=work_block_id, outcome="blocked")
        return recovery
    if not remote_head:
        recovery["reason"] = "target_push_remote_ref_missing_after_fetch"
        write_audit(repo_root, config, "target_push_recovery", recovery, work_block_id=work_block_id, outcome="blocked")
        return recovery
    if local_head != remote_head and (not local_head or is_ancestor(repo_root, local_head, remote_head)):
        local_update = update_local_target(repo_root, target_branch, remote_head)
        recovery["localTargetUpdate"] = local_update
        recovery["localHeadAfterUpdate"] = rev_parse(repo_root, local_ref, required=False)
        if local_update["returncode"] != 0:
            recovery["reason"] = "target_push_local_update_failed"
            write_audit(repo_root, config, "target_push_recovery", recovery, work_block_id=work_block_id, outcome="blocked")
            return recovery
    elif local_head != remote_head:
        recovery["reason"] = "target_push_local_target_not_fast_forwardable"
        write_audit(repo_root, config, "target_push_recovery", recovery, work_block_id=work_block_id, outcome="blocked")
        return recovery
    else:
        recovery["localHeadAfterUpdate"] = local_head
    if remote_head == attempted_head or is_ancestor(repo_root, attempted_head, remote_head):
        recovery.update({"status": "success", "reason": "target_push_remote_already_contains_attempted_head", "targetHeadAfter": remote_head})
        write_audit(repo_root, config, "target_push_recovery", recovery, work_block_id=work_block_id, outcome="success")
        return recovery
    recovery.update(
        {
            "status": "blocked",
            "reason": "target_push_rerun_required",
            "targetHeadAfter": remote_head,
        }
    )
    write_audit(repo_root, config, "target_push_recovery", recovery, work_block_id=work_block_id, outcome="blocked")
    return recovery


def cleanup_after_success(
    repo_root: Path,
    config: Dict[str, Any],
    detection: Dict[str, Any],
    *,
    new_target_head: str,
    integration_path: Optional[Path],
) -> Dict[str, Any]:
    branch = detection["branch"]
    target_branch = detection["targetBranch"]
    cleanup: Dict[str, Any] = {"actions": [], "retained": []}
    tree_equal = tree_hash(repo_root, detection["featureHead"]) == tree_hash(repo_root, new_target_head)
    if integration_path is not None:
        cleanup["actions"].append({"action": "integration_worktree_remove", **remove_worktree(repo_root, integration_path)})
        write_audit(repo_root, config, "snapshot_pruning", cleanup["actions"][-1], work_block_id=detection["workBlockId"], outcome="success")
    if bool(config.get("cleanupPolicy", {}).get("deleteLocalBranchAfterSuccess", True)) and branch != target_branch:
        if current_branch(repo_root) == branch:
            if tree_equal:
                switch = run_git(repo_root, ["switch", target_branch])
                cleanup["actions"].append({"action": "switch_original_worktree_to_target", "returncode": switch.returncode, "stderr": switch.stderr[-2000:]})
                if switch.returncode != 0:
                    cleanup["retained"].append({"reason": "switch_to_target_failed", "branch": branch})
                    write_audit(repo_root, config, "cleanup_retention", cleanup, work_block_id=detection["workBlockId"], outcome="retained")
                    return cleanup
            else:
                cleanup["retained"].append({"reason": "target_tree_does_not_match_feature_tree", "branch": branch})
                write_audit(repo_root, config, "cleanup_retention", cleanup, work_block_id=detection["workBlockId"], outcome="retained")
                return cleanup
        delete = run_git(repo_root, ["branch", "-d", branch])
        cleanup["actions"].append({"action": "delete_local_branch", "branch": branch, "returncode": delete.returncode, "stderr": delete.stderr[-2000:]})
        if delete.returncode == 0:
            write_audit(repo_root, config, "branch_deletion", cleanup["actions"][-1], work_block_id=detection["workBlockId"], outcome="success")
        else:
            cleanup["retained"].append({"reason": "local_branch_delete_failed", "branch": branch})
            write_audit(repo_root, config, "cleanup_retention", cleanup, work_block_id=detection["workBlockId"], outcome="retained")
    return cleanup


def finalize_work_block(
    repo_root_arg: Path,
    *,
    work_block_id: Optional[str] = None,
    expected_pinned_refs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    tooling = verify_closeout_tooling_current(repo_root, config)
    if not tooling["ok"]:
        return {"status": "blocked", "reason": "closeout_tooling_stale", "tooling": tooling}
    detection = detect_work_block(repo_root, work_block_id=work_block_id)
    block_id = detection["workBlockId"]
    update_manifest(repo_root, config, block_id, {"state": "finalizing", "completedAt": utc_now()})
    if expected_pinned_refs is not None and expected_pinned_refs != detection["pinnedRefs"]:
        payload = {"expectedPinnedRefs": expected_pinned_refs, "actualPinnedRefs": detection["pinnedRefs"]}
        write_audit(repo_root, config, "stale_refs", payload, work_block_id=block_id, outcome="blocked")
        append_event(repo_root, config, block_id, {"event": "finalize_blocked", "reason": "stale_refs"})
        update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "stale_refs"})
        return {"status": "blocked", "reason": "stale_refs", **payload}
    repair = repair_eligibility(repo_root, work_block_id=block_id)
    if repair["status"] != "repaired":
        append_event(repo_root, config, block_id, {"event": "finalize_blocked", "reason": "repair_blocked"})
        update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "repair_blocked"})
        return {"status": "blocked", "reason": "repair_blocked", "repair": repair, "detection": detection}
    detection = detect_work_block(repo_root, work_block_id=block_id)
    evidence = finalize_evidence(config, detection)
    evidence_hash = stable_hash(evidence)
    candidate_id = finalize_candidate_id(block_id)
    action_id = finalize_action_id()
    quorum = check_review_quorum(
        repo_root,
        config,
        candidate_id=candidate_id,
        action_id=action_id,
        evidence_hash=evidence_hash,
        pinned_refs=detection["pinnedRefs"],
    )
    stale_review_renewal = quorum["reason"] == "stale_review" and bool(config.get("autoQuorum", {}).get("allowStaleReviewRenewal", True))
    if not quorum["ok"] and (quorum["reason"] == "review_quorum_missing" or stale_review_renewal):
        quorum_result = ensure_autonomous_quorum(
            repo_root,
            config,
            candidate_id=candidate_id,
            action_id=action_id,
            evidence_hash=evidence_hash,
            pinned_refs=detection["pinnedRefs"],
            evidence=evidence,
            action_class="repo_sweep_clean_integrate",
            blockers=[],
        )
        quorum = quorum_result["quorum"]
    if not quorum["ok"]:
        audit_type = "stale_review" if quorum["reason"] == "stale_review" else "review_quorum_blocked"
        payload = {"candidateId": candidate_id, "actionId": action_id, "evidenceHash": evidence_hash, "quorum": quorum}
        write_audit(repo_root, config, audit_type, payload, work_block_id=block_id, outcome="blocked")
        append_event(repo_root, config, block_id, {"event": "finalize_blocked", "reason": quorum["reason"]})
        update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": quorum["reason"]})
        return {"status": "blocked", "reason": quorum["reason"], "quorum": quorum, "evidenceHash": evidence_hash}
    target_branch = detection["targetBranch"]
    target = target_ref_for(repo_root, config)
    feature_head = detection["featureHead"]
    if target["head"] != detection["targetHead"]:
        payload = {"expectedTargetHead": detection["targetHead"], "actualTargetHead": target["head"]}
        write_audit(repo_root, config, "stale_refs", payload, work_block_id=block_id, outcome="blocked")
        append_event(repo_root, config, block_id, {"event": "finalize_blocked", "reason": "stale_refs"})
        update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "stale_refs"})
        return {"status": "blocked", "reason": "stale_refs", **payload}
    already_integrated = is_ancestor(repo_root, feature_head, target["head"])
    integration_path: Optional[Path] = None
    new_target_head = target["head"]
    push_result: Optional[Dict[str, Any]] = None
    recovery: Optional[Dict[str, Any]] = None
    local_update: Optional[Dict[str, Any]] = None
    if already_integrated:
        local_head = rev_parse(repo_root, f"refs/heads/{target_branch}", required=False)
        if local_head != target["head"]:
            recovery = update_local_target(repo_root, target_branch, target["head"])
            write_audit(repo_root, config, "partial_push_recovery", recovery, work_block_id=block_id, outcome="success" if recovery["returncode"] == 0 else "blocked")
            if recovery["returncode"] != 0:
                update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "partial_push_recovery_failed"})
                return {"status": "blocked", "reason": "partial_push_recovery_failed", "recovery": recovery}
    else:
        integration_path = closeout_state_root(repo_root, config) / "integration-worktrees" / ("%s-%s" % (block_id, uuid.uuid4().hex[:8]))
        integration_path.parent.mkdir(parents=True, exist_ok=True)
        add = run_git_longpaths(repo_root, ["worktree", "add", "--detach", str(integration_path), target["head"]])
        if add.returncode != 0:
            payload = {"operation": "worktree_add", "returncode": add.returncode, "stderr": add.stderr[-4000:]}
            write_audit(repo_root, config, "blocked_repair", payload, work_block_id=block_id, outcome="blocked")
            update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "integration_worktree_failed"})
            return {"status": "blocked", "reason": "integration_worktree_failed", "detail": payload}
        merge = run_git(integration_path, ["merge", "--no-ff", "--no-edit", feature_head])
        if merge.returncode != 0:
            payload = {"operation": "merge", "returncode": merge.returncode, "stdout": merge.stdout[-4000:], "stderr": merge.stderr[-4000:]}
            write_audit(repo_root, config, "blocked_repair", payload, work_block_id=block_id, outcome="blocked")
            remove_worktree(repo_root, integration_path)
            update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "merge_failed"})
            return {"status": "blocked", "reason": "merge_failed", "detail": payload}
        diff_check = run_git(integration_path, ["diff", "--check"])
        if diff_check.returncode != 0:
            payload = {"operation": "git_diff_check", "returncode": diff_check.returncode, "stdout": diff_check.stdout[-4000:], "stderr": diff_check.stderr[-4000:]}
            write_audit(repo_root, config, "validation_failure", payload, work_block_id=block_id, outcome="blocked")
            remove_worktree(repo_root, integration_path)
            update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "diff_check_failed"})
            return {"status": "blocked", "reason": "diff_check_failed", "detail": payload}
        validations = run_validations(repo_root, config, integration_path)
        failed_validation = next((item for item in validations if item["returncode"] != 0), None)
        if failed_validation:
            payload = {"validations": validations}
            write_audit(repo_root, config, "validation_failure", payload, work_block_id=block_id, outcome="blocked")
            if bool(config.get("cleanupPolicy", {}).get("removeIntegrationWorktreeAfterFailure", True)):
                remove_worktree(repo_root, integration_path)
            update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "validation_failed"})
            return {"status": "blocked", "reason": "validation_failed", "validations": validations}
        new_target_head = git_stdout(integration_path, ["rev-parse", "HEAD"])
        if target["mode"] == "remote":
            push = run_git(integration_path, ["push", str(target.get("remote")), f"HEAD:{target_branch}"])
            push_result = {"remote": target.get("remote"), "targetBranch": target_branch, "returncode": push.returncode, "stdout": push.stdout[-4000:], "stderr": push.stderr[-4000:]}
            if push.returncode != 0:
                recovery = repair_target_push_failure(
                    repo_root,
                    config,
                    target_branch=target_branch,
                    remote=str(target.get("remote")),
                    attempted_head=new_target_head,
                    push_result=push_result,
                    work_block_id=block_id,
                )
                if recovery["status"] != "success":
                    remove_worktree(repo_root, integration_path)
                    append_event(repo_root, config, block_id, {"event": "finalize_blocked", "reason": recovery["reason"]})
                    update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": recovery["reason"]})
                    return {"status": "blocked", "reason": recovery["reason"], "push": push_result, "recovery": recovery}
                new_target_head = str(recovery["targetHeadAfter"])
                local_update = recovery.get("localTargetUpdate")
        if local_update is None:
            local_update = update_local_target(repo_root, target_branch, new_target_head)
        if local_update["returncode"] != 0:
            write_audit(repo_root, config, "partial_push_recovery", local_update, work_block_id=block_id, outcome="blocked")
            remove_worktree(repo_root, integration_path)
            update_manifest(repo_root, config, block_id, {"state": "blocked", "blockedReason": "local_target_update_failed"})
            return {"status": "blocked", "reason": "local_target_update_failed", "localUpdate": local_update, "push": push_result}
    cleanup = cleanup_after_success(repo_root, config, detection, new_target_head=new_target_head, integration_path=integration_path)
    success_payload = {
        "candidateId": candidate_id,
        "actionId": action_id,
        "evidenceHash": evidence_hash,
        "quorum": quorum,
        "featureHead": feature_head,
        "targetHeadBefore": detection["targetHead"],
        "targetHeadAfter": new_target_head,
        "alreadyIntegrated": already_integrated,
        "push": push_result,
        "recovery": recovery,
        "cleanup": cleanup,
        "foreignDirtyRetained": detection["foreignDirty"],
        "workBlockSelection": detection.get("workBlockSelection"),
    }
    write_audit(repo_root, config, "success", success_payload, work_block_id=block_id, outcome="success")
    append_event(repo_root, config, block_id, {"event": "finalize_success", "targetHeadAfter": new_target_head})
    update_manifest(repo_root, config, block_id, {"state": "finalized", "finalizedAt": utc_now(), "targetHeadAfter": new_target_head})
    return {"status": "success", **success_payload}


def complete_work_block(repo_root_arg: Path, *, work_block_id: Optional[str] = None, finalize: bool = False) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    manifest = ensure_work_block_for_current_branch(repo_root, config, work_block_id)
    block_id = str(manifest["workBlockId"])
    update_manifest(repo_root, config, block_id, {"state": "completed", "completedAt": utc_now()})
    append_event(repo_root, config, block_id, {"event": "work_block_completed", "finalizeRequested": bool(finalize)})
    detection = detect_work_block(repo_root, work_block_id=block_id)
    if not finalize:
        return {"status": "completed", "workBlockId": block_id, "workBlockSelection": manifest.get("workBlockSelection"), "detector": detection}
    result = finalize_work_block(repo_root, work_block_id=block_id)
    result["workBlockSelection"] = manifest.get("workBlockSelection")
    return result


def checkpoint_owned_dirty_action_id() -> str:
    return "checkpoint-owned-dirty"


def checkpoint_owned_dirty_candidate_id(work_block_id: str, paths: Sequence[str], detector_hash: str) -> str:
    return "candidate:checkpoint-owned-dirty:%s" % stable_hash({"workBlockId": work_block_id, "paths": sorted(paths), "detectorHash": detector_hash}, 16)


def checkpoint_owned_dirty_evidence(repo_root: Path, config: Dict[str, Any], detection: Dict[str, Any], paths: Sequence[str]) -> Dict[str, Any]:
    path_set = set(paths)
    owned_entries = [item for item in detection.get("ownedDirty", []) if item["path"] in path_set]
    manifest = load_manifest(repo_root, config, str(detection["workBlockId"]))
    return {
        "workBlockId": detection["workBlockId"],
        "branch": detection["branch"],
        "featureHead": detection["featureHead"],
        "targetHead": detection["targetHead"],
        "detectorHash": detection["detectorHash"],
        "paths": sorted(paths),
        "ownedDirty": owned_entries,
        "dirtyPathFingerprints": dirty_path_fingerprints(repo_root, owned_entries),
        "foreignDirtyRetained": detection.get("foreignDirty", []),
        "mixedDirty": detection.get("mixedDirty", []),
        "unownedDirty": detection.get("unownedDirty", []),
        "dirtyBaseline": manifest.get("dirtyBaseline"),
        "policy": {
            "allowOwnedDirtyCheckpoint": config.get("stashPolicy", {}).get("allowOwnedDirtyCheckpoint", True),
            "autoClaimCleanAtStart": config.get("dirty", {}).get("autoClaimCleanAtStart", True),
        },
    }


def plan_checkpoint_owned_dirty_candidate(repo_root: Path, config: Dict[str, Any], detection: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    paths = sorted({item["path"] for item in detection.get("ownedDirty", [])})
    if not paths:
        return None
    candidate_id = checkpoint_owned_dirty_candidate_id(str(detection["workBlockId"]), paths, str(detection["detectorHash"]))
    pinned_refs = {
        "feature": detection["pinnedRefs"]["feature"],
        "target": detection["pinnedRefs"]["target"],
        "dirtyPaths": paths,
    }
    evidence = checkpoint_owned_dirty_evidence(repo_root, config, detection, paths)
    return {
        "candidateId": candidate_id,
        "actionId": checkpoint_owned_dirty_action_id(),
        "actionClass": "owned_dirty_checkpoint",
        "workBlockId": detection["workBlockId"],
        "paths": paths,
        "evidence": evidence,
        "evidenceHash": stable_hash(evidence),
        "pinnedRefs": pinned_refs,
    }


def staged_path_set(repo_root: Path) -> set[str]:
    result = run_git(repo_root, ["diff", "--cached", "--name-only", "--"], check=True)
    return {normalize_rel(line) for line in result.stdout.splitlines() if normalize_rel(line)}


def checkpoint_owned_work(repo_root_arg: Path, *, work_block_id: Optional[str] = None, message: str = "brokered closeout checkpoint") -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    detection = detect_work_block(repo_root, work_block_id=work_block_id)
    block_id = detection["workBlockId"]
    if detection.get("mixedDirty"):
        paths = [item["path"] for item in detection["mixedDirty"]]
        payload = {
            "reason": "baseline-dirty-overlaps-candidate",
            "mixedDirty": detection["mixedDirty"],
            "recoveryCommand": baseline_dirty_recovery_command(paths),
        }
        write_audit(repo_root, config, "blocked_repair", payload, work_block_id=block_id, outcome="blocked")
        return {"status": "blocked", **payload}
    if detection["unownedDirty"]:
        payload = {"reason": "checkpoint_refuses_non_owned_dirty", "foreignDirty": detection["foreignDirty"], "unownedDirty": detection["unownedDirty"]}
        write_audit(repo_root, config, "blocked_repair", payload, work_block_id=block_id, outcome="blocked")
        return {"status": "blocked", **payload}
    if detection["foreignDirty"]:
        write_audit(repo_root, config, "retained_foreign_dirty", {"foreignDirty": detection["foreignDirty"]}, work_block_id=block_id, outcome="retained")
    candidate = plan_checkpoint_owned_dirty_candidate(repo_root, config, detection)
    if not candidate:
        return {"status": "noop", "reason": "no_owned_dirty"}
    paths = candidate["paths"]
    blockers: List[str] = []
    staged_before = staged_path_set(repo_root)
    outside_staged_before = sorted(staged_before.difference(paths))
    if outside_staged_before:
        blockers.append("staged_paths_outside_owned_dirty")
    quorum_result = ensure_autonomous_quorum(
        repo_root,
        config,
        candidate_id=candidate["candidateId"],
        action_id=candidate["actionId"],
        evidence_hash=candidate["evidenceHash"],
        pinned_refs=candidate["pinnedRefs"],
        evidence=candidate["evidence"],
        action_class=candidate["actionClass"],
        blockers=blockers,
    )
    if not quorum_result["quorum"]["ok"]:
        payload = {"candidate": candidate, "quorum": quorum_result, "blockers": blockers, "outsideStagedPaths": outside_staged_before}
        write_audit(repo_root, config, "checkpoint_owned_dirty_blocked", payload, work_block_id=block_id, outcome="blocked")
        return {"status": "blocked", "reason": quorum_result["quorum"]["reason"] or "checkpoint_owned_dirty_blocked", **payload}
    latest_detection = detect_work_block(repo_root, work_block_id=block_id)
    latest_candidate = plan_checkpoint_owned_dirty_candidate(repo_root, config, latest_detection)
    if (
        not latest_candidate
        or latest_candidate["candidateId"] != candidate["candidateId"]
        or latest_candidate["evidenceHash"] != candidate["evidenceHash"]
        or latest_candidate["pinnedRefs"] != candidate["pinnedRefs"]
        or latest_detection.get("mixedDirty")
        or latest_detection.get("unownedDirty")
    ):
        payload = {"candidate": candidate, "latestCandidate": latest_candidate, "latestDetectionHash": latest_detection.get("detectorHash")}
        write_audit(repo_root, config, "checkpoint_owned_dirty_stale_tuple", payload, work_block_id=block_id, outcome="blocked")
        return {"status": "blocked", "reason": "stale_tuple", **payload}
    add = run_git(repo_root, ["add", "--", *paths])
    if add.returncode != 0:
        payload = {"candidate": candidate, "paths": paths, "stderr": add.stderr[-3000:]}
        write_audit(repo_root, config, "checkpoint_owned_dirty_blocked", payload, work_block_id=block_id, outcome="blocked")
        return {"status": "blocked", "reason": "git_add_failed", **payload}
    staged_after = staged_path_set(repo_root)
    outside_staged_after = sorted(staged_after.difference(paths))
    missing_staged = sorted(set(paths).difference(staged_after))
    if outside_staged_after or missing_staged:
        payload = {
            "candidate": candidate,
            "paths": paths,
            "stagedPaths": sorted(staged_after),
            "outsideStagedPaths": outside_staged_after,
            "missingStagedPaths": missing_staged,
        }
        write_audit(repo_root, config, "checkpoint_owned_dirty_blocked", payload, work_block_id=block_id, outcome="blocked")
        return {"status": "blocked", "reason": "checkpoint_stage_scope_mismatch", **payload}
    commit = run_git(repo_root, ["commit", "-m", message])
    payload = {
        "candidateId": candidate["candidateId"],
        "actionId": candidate["actionId"],
        "actionClass": candidate["actionClass"],
        "evidenceHash": candidate["evidenceHash"],
        "pinnedRefs": candidate["pinnedRefs"],
        "quorum": quorum_result["quorum"],
        "paths": paths,
        "stagedPaths": sorted(staged_after.intersection(paths)),
        "returncode": commit.returncode,
        "stdout": commit.stdout[-4000:],
        "stderr": commit.stderr[-4000:],
    }
    if commit.returncode == 0:
        payload["commit"] = git_stdout(repo_root, ["rev-parse", "HEAD"])
        append_event(repo_root, config, block_id, {"event": "owned_dirty_checkpointed", "paths": paths, "commit": payload["commit"]})
    write_audit(repo_root, config, "checkpoint_owned_dirty", payload, work_block_id=block_id, outcome="success" if commit.returncode == 0 else "blocked")
    return {"status": "success" if commit.returncode == 0 else "blocked", **payload}


def quarantine_orphans(repo_root_arg: Path, *, apply: bool = False) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    root = work_blocks_root(repo_root, config)
    orphans: List[Dict[str, Any]] = []
    if root.exists():
        for manifest_file in root.glob("*/manifest.json"):
            manifest = read_json(manifest_file, {})
            worktree = Path(str(manifest.get("worktree") or ""))
            branch = str(manifest.get("branch") or "")
            branch_exists = bool(branch and rev_parse(repo_root, f"refs/heads/{branch}", required=False))
            if not worktree.exists() or not branch_exists:
                orphans.append(
                    {
                        "workBlockId": manifest.get("workBlockId") or manifest_file.parent.name,
                        "branch": branch,
                        "worktree": str(worktree),
                        "branchExists": branch_exists,
                        "worktreeExists": worktree.exists(),
                    }
                )
    payload = {"orphans": orphans, "applied": False}
    if apply:
        payload["applied"] = True
        for orphan in orphans:
            block_id = str(orphan["workBlockId"])
            try:
                update_manifest(repo_root, config, block_id, {"state": "quarantined", "quarantinedAt": utc_now()})
                append_event(repo_root, config, block_id, {"event": "orphan_quarantined", "reason": orphan})
            except HygieneError:
                pass
    write_audit(repo_root, config, "orphan_quarantine", payload, outcome="success" if apply else "recorded")
    return {"status": "success", **payload}


REPO_SWEEP_CANDIDATE_ID = "candidate:repo-sweep"
REPO_SWEEP_ACTION_ID = "repo_sweep_prune_merged"


def parse_worktree_list(repo_root: Path) -> List[Dict[str, Any]]:
    result = run_git(repo_root, ["worktree", "list", "--porcelain"], check=True)
    worktrees: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branchRef"] = value
            prefix = "refs/heads/"
            current["branch"] = value[len(prefix) :] if value.startswith(prefix) else value
        elif key == "detached":
            current["detached"] = True
        elif key == "locked":
            current["locked"] = True
            current["lockReason"] = value
    if current:
        worktrees.append(current)
    return worktrees


def worktree_dirty_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "dirty": False, "paths": []}
    result = run_git(path, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if result.returncode != 0:
        return {"exists": True, "dirty": True, "paths": [], "statusError": result.stderr[-1000:]}
    entries = [part[3:] for part in result.stdout.split("\0") if part]
    return {"exists": True, "dirty": bool(entries), "paths": sorted(normalize_rel(path) for path in entries if normalize_rel(path))}


def safe_state_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return cleaned or stable_hash(value, 16)


def repo_sweep_reports_root(repo_root: Path, config: Dict[str, Any]) -> Path:
    return closeout_state_root(repo_root, config) / "repo-sweep" / "candidate-reports"


def repo_relative_or_absolute(repo_root: Path, path: Path) -> str:
    try:
        return normalize_rel(path.resolve().relative_to(repo_root.resolve()).as_posix())
    except ValueError:
        return str(path.resolve())


def is_protected_worktree_path(repo_root: Path, config: Dict[str, Any], path: Path) -> bool:
    rel = repo_relative_or_absolute(repo_root, path)
    return path_matches_any(rel, config.get("cleanupPolicy", {}).get("protectedWorktreeRoots", []))


def write_candidate_report(repo_root: Path, config: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    candidate_id = str(report["candidateId"])
    report_dir = repo_sweep_reports_root(repo_root, config) / safe_state_name(candidate_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "latest-report.json"
    enriched = {**report, "reportPath": str(report_path)}
    write_json(report_path, enriched)
    write_audit(repo_root, config, "cleanup_retention", enriched, outcome="recorded")
    return enriched


def work_block_for_branch(repo_root: Path, config: Dict[str, Any], branch: str) -> Optional[Dict[str, Any]]:
    root = work_blocks_root(repo_root, config)
    if not root.exists():
        return None
    matches: List[Dict[str, Any]] = []
    for manifest_file in root.glob("*/manifest.json"):
        manifest = read_json(manifest_file, {})
        if manifest.get("branch") == branch and manifest.get("state") in {"active", "completed", "finalizing", "blocked"}:
            matches.append(manifest)
    if not matches:
        return None
    matches.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    return matches[0]


def classify_dirty_entries_for_branch(
    repo_root: Path,
    config: Dict[str, Any],
    *,
    branch: str,
    branch_head: str,
    target_head: str,
    worktree_path: Path,
) -> Dict[str, Any]:
    claims = active_path_claims(repo_root, config)
    manifest = work_block_for_branch(repo_root, config, branch)
    block_id = str(manifest.get("workBlockId")) if manifest else None
    own_claims = set(manifest_path_claims(manifest)) if manifest else set()
    committed_delta = set(changed_paths_between(repo_root, target_head, branch_head))
    entries = parse_status_paths(worktree_path)
    owned_dirty: List[Dict[str, Any]] = []
    unowned_dirty: List[Dict[str, Any]] = []
    foreign_dirty: List[Dict[str, Any]] = []
    unclaimed_default = str(config.get("dirty", {}).get("unclaimedOutsideDelta", "foreign"))
    for entry in entries:
        path = str(entry["path"])
        owner = claims.get(path)
        sensitive = path_matches_any(path, config.get("paths", {}).get("sensitive", []))
        in_delta = path in committed_delta
        claimed_by_self = bool(block_id and (path in own_claims or owner == block_id))
        enriched = {
            **entry,
            "ownerWorkBlockId": owner,
            "inCompletedBranchDelta": in_delta,
            "sensitive": sensitive,
            "branch": branch,
        }
        if in_delta or claimed_by_self:
            enriched["classificationReason"] = "path overlaps branch delta or branch work-block claim"
            owned_dirty.append(enriched)
        elif sensitive and bool(config.get("dirty", {}).get("sensitiveUnownedBlocks", True)):
            enriched["classificationReason"] = "sensitive path is unowned"
            unowned_dirty.append(enriched)
        elif owner and owner != block_id:
            enriched["classificationReason"] = "path is claimed by another work block"
            foreign_dirty.append(enriched)
        elif unclaimed_default == "foreign":
            enriched["classificationReason"] = "path is outside completed branch delta"
            foreign_dirty.append(enriched)
        else:
            enriched["classificationReason"] = "path is unclaimed and outside branch delta"
            unowned_dirty.append(enriched)
    return {
        "workBlockId": block_id,
        "worktreePath": str(worktree_path),
        "committedDeltaPaths": sorted(committed_delta),
        "dirtyPaths": sorted({entry["path"] for entry in entries}),
        "ownedDirty": owned_dirty,
        "unownedDirty": unowned_dirty,
        "foreignDirty": foreign_dirty,
        "eligible": not owned_dirty and not unowned_dirty,
    }


def extract_pid(value: str) -> Optional[int]:
    match = re.search(r"(?:pid|process)\s*[:=]\s*(\d+)", value, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def process_is_alive(pid: Optional[int]) -> Optional[bool]:
    if pid is None:
        return None
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def worktree_git_dir(path: Path) -> Optional[Path]:
    result = run_git(path, ["rev-parse", "--git-dir"])
    if result.returncode != 0:
        return None
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = path / git_dir
    return git_dir.resolve()


def inspect_worktree_lock(path: Path, item: Dict[str, Any]) -> Dict[str, Any]:
    reason = str(item.get("lockReason") or "")
    pid = extract_pid(reason)
    git_dir = worktree_git_dir(path) if path.exists() else None
    lock_path = git_dir / "locked" if git_dir else None
    mtime: Optional[datetime] = None
    if lock_path and lock_path.exists():
        mtime = datetime.fromtimestamp(lock_path.stat().st_mtime, timezone.utc)
    age_hours = None
    if mtime:
        age_hours = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600.0
    return {
        "locked": bool(item.get("locked")),
        "lockReason": reason,
        "lockPath": str(lock_path) if lock_path else None,
        "lockMtime": mtime.isoformat() if mtime else None,
        "lockAgeHours": age_hours,
        "pid": pid,
        "pidAlive": process_is_alive(pid),
    }


def is_stale_lock(lock: Dict[str, Any], config: Dict[str, Any]) -> bool:
    if not lock.get("locked"):
        return False
    pid_alive = lock.get("pidAlive")
    if pid_alive is True:
        return False
    age = lock.get("lockAgeHours")
    threshold = float(config.get("repoSweep", {}).get("lockedWorktreeStaleHours", 24))
    if age is None:
        return pid_alive is False
    return float(age) >= threshold


def blocker_auto_remediation_config(config: Dict[str, Any]) -> Dict[str, Any]:
    repo_sweep = config.get("repoSweep", {})
    retained = repo_sweep.get("retainedBlockerAutoRemediation", {})
    if isinstance(retained, dict) and not bool(retained.get("enabled", True)):
        return {}
    legacy = config.get("blockerAutoRemediation", {})
    if not bool(legacy.get("enabled", True)):
        legacy = {}
    merged = dict(legacy)
    if "allowForeignDirtyIntegratedBranchPrune" in repo_sweep:
        merged["allowForeignDirtyIntegratedBranchSwitch"] = bool(repo_sweep.get("allowForeignDirtyIntegratedBranchPrune"))
    if "recoveryBranchPrefix" in repo_sweep:
        merged["detachedDirtyBranchPrefix"] = repo_sweep.get("recoveryBranchPrefix")
    if "allowPatchEquivalentPrune" in repo_sweep:
        merged["prunePatchEquivalentBranches"] = bool(repo_sweep.get("allowPatchEquivalentPrune"))
    if "protectedLockedWorktreeExactPolicy" in repo_sweep:
        merged["explicitProtectedWorktreeActions"] = repo_sweep.get("protectedLockedWorktreeExactPolicy") or []
    return merged


def remediation_proof(config: Dict[str, Any], *, recommended_action: str, action_class: str, blockers: Sequence[str]) -> Dict[str, Any]:
    auto = blocker_auto_remediation_config(config)
    attempted = recommended_action in {
        "clean_integrate_now",
        "clean_integrate_remote_now",
        "prune_now",
        "prune_remote_now",
        "cleanup_worktree_and_prune",
        "split_now",
        "switch_target_and_prune",
        "preserve_detached_dirty_now",
    }
    excluded: List[str] = []
    if not auto:
        excluded.append("repoSweep.retainedBlockerAutoRemediation.disabled")
    excluded.extend(str(blocker) for blocker in blockers)
    if action_class == "active_locked_worktree" and "protected_worktree_root" in blockers:
        excluded.append("repoSweep.protectedLockedWorktreeExactPolicy.missing_or_mismatched")
    if action_class == "dirty_worktree" and "foreign_dirty_target_overlap" in blockers:
        excluded.append("foreign_dirty_target_overlap")
    return {
        "symbolicRemediationAttempted": attempted,
        "policyEligible": attempted or bool(excluded),
        "excludedByExactPolicy": sorted({item for item in excluded if item}),
    }


def ref_delta_paths(repo_root: Path, left_ref: str, right_ref: str) -> List[str]:
    result = run_git(repo_root, ["diff", "--name-only", f"{left_ref}..{right_ref}"])
    if result.returncode != 0:
        return []
    return sorted({normalize_rel(line) for line in result.stdout.splitlines() if normalize_rel(line)})


def merge_conflict_paths(stdout: str, stderr: str) -> List[str]:
    paths: List[str] = []
    for line in (stdout + "\n" + stderr).splitlines():
        match = re.search(r"CONFLICT\s+\([^)]+\):\s+.*?\s+in\s+(.+)$", line.strip())
        if match:
            paths.append(normalize_rel(match.group(1)))
    return sorted({path for path in paths if path})


def agent_conflict_resolution_packet(config: Dict[str, Any], *, candidate_id: str, branch: str, merge_probe: Dict[str, Any], changed_paths: Sequence[str]) -> Optional[Dict[str, Any]]:
    auto = blocker_auto_remediation_config(config)
    conflicts = list(merge_probe.get("conflicts") or [])
    max_conflicts = int(auto.get("maxConflictFilesForAgent", 0) or 0)
    if merge_probe.get("reason") != "merge_failed" or not conflicts or not max_conflicts or len(conflicts) > max_conflicts:
        return None
    return {
        "symbolicAction": "resolve-conflicts-with-agent",
        "agentDispatch": investigation_agent_payload(config, candidate_id, "repo-sweep-conflict-remediator"),
        "branch": branch,
        "conflictPaths": conflicts,
        "changedPathCount": len(changed_paths),
        "recoveryCommand": "spawn one background agent for this report, resolve only listed conflict paths in a temp integration worktree, then rerun repo sweep for this candidate",
    }


def protected_worktree_cleanup_evidence(branch: str, head: str, path: Path, lock: Dict[str, Any], target: Dict[str, Any], action: str) -> Dict[str, Any]:
    return {
        "branch": branch,
        "head": head,
        "path": str(path),
        "lockReason": lock.get("lockReason"),
        "lockPath": lock.get("lockPath"),
        "action": action,
        "target": target,
    }


def policy_paths_equivalent(left: Any, right: Any) -> bool:
    if str(left) == str(right):
        return True
    try:
        return Path(str(left)).resolve() == Path(str(right)).resolve()
    except Exception:
        return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(os.path.normpath(str(right)))


def explicit_protected_worktree_action(config: Dict[str, Any], evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    evidence_hash = stable_hash(evidence)
    for raw in blocker_auto_remediation_config(config).get("explicitProtectedWorktreeActions", []):
        item = dict(raw or {})
        if str(item.get("branch")) != str(evidence.get("branch")):
            continue
        if not policy_paths_equivalent(item.get("path"), evidence.get("path")):
            continue
        if str(item.get("lockReason")) != str(evidence.get("lockReason")):
            continue
        if str(item.get("action")) != str(evidence.get("action")):
            continue
        if str(item.get("evidenceHash")) != evidence_hash:
            continue
        return {**item, "matchedEvidenceHash": evidence_hash}
    return None


def patch_id_for_range(repo_root: Path, left: str, right: str) -> Optional[str]:
    diff = run_git(repo_root, ["diff", "--binary", f"{left}...{right}"])
    diff_stdout = diff.stdout or ""
    if diff.returncode != 0 or not diff_stdout.strip():
        return None
    completed = subprocess.run(
        ["git", "patch-id", "--stable"],
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        errors="replace",
        input=diff_stdout,
        capture_output=True,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return stable_hash(diff.stdout)
    return completed.stdout.split()[0]


def branch_commit_subjects(repo_root: Path, left: str, right: str) -> List[str]:
    result = run_git(repo_root, ["log", "--format=%s", f"{left}..{right}"])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def branch_commit_date(repo_root: Path, rev: str) -> Optional[str]:
    result = run_git(repo_root, ["log", "-1", "--format=%cI", rev])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def ahead_behind_between(repo_root: Path, left: str, right: str) -> Dict[str, int]:
    result = run_git(repo_root, ["rev-list", "--left-right", "--count", f"{left}...{right}"])
    if result.returncode != 0:
        return {"behind": 0, "ahead": 0}
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return {"behind": 0, "ahead": 0}
    return {"behind": int(parts[0]), "ahead": int(parts[1])}


def backup_branch_analysis(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    branch = str(item["branch"])
    patterns = config.get("repoSweep", {}).get("backupBranchPatterns", [])
    is_backup = path_matches_any(branch, patterns)
    target_head = str(plan["pinnedRefs"]["target"]["head"])
    branch_head = str(item["head"])
    branch_tree = tree_hash(repo_root, branch_head)
    target_tree = tree_hash(repo_root, target_head)
    cherry = run_git(repo_root, ["cherry", "-v", target_head, branch_head])
    cherry_rows = [line.strip() for line in cherry.stdout.splitlines() if line.strip()] if cherry.returncode == 0 else []
    redundant_with: Optional[Dict[str, Any]] = None
    if branch_tree == target_tree or is_ancestor(repo_root, branch_head, target_head):
        redundant_with = {"kind": "target", "ref": plan["pinnedRefs"]["target"]["ref"], "head": target_head}
    elif cherry_rows and all(row.startswith("-") for row in cherry_rows):
        redundant_with = {
            "kind": "target_patch_equivalent",
            "ref": plan["pinnedRefs"]["target"]["ref"],
            "head": target_head,
            "cherry": cherry_rows,
        }
    else:
        for other in plan["branchPlans"]:
            if other["branch"] == branch:
                continue
            other_head = str(other["head"])
            if tree_hash(repo_root, other_head) == branch_tree:
                redundant_with = {"kind": "branch", "ref": other["branch"], "head": other_head}
                break
    return {
        "isBackupBranch": is_backup,
        "patterns": patterns,
        "branchTree": branch_tree,
        "targetTree": target_tree,
        "patchId": patch_id_for_range(repo_root, target_head, branch_head),
        "cherry": cherry_rows,
        "redundantWith": redundant_with,
    }


def simulate_clean_integration(repo_root: Path, config: Dict[str, Any], *, target_head: str, branch_head: str) -> Dict[str, Any]:
    integration_path = closeout_state_root(repo_root, config) / "repo-sweep" / "integration-probes" / uuid.uuid4().hex[:12]
    integration_path.parent.mkdir(parents=True, exist_ok=True)
    attempt = {
        "tempWorktreePath": str(integration_path),
        "targetHead": target_head,
        "branchHead": branch_head,
    }
    add = run_git_longpaths(repo_root, ["worktree", "add", "--detach", str(integration_path), target_head])
    if add.returncode != 0:
        return {"clean": False, "reason": "probe_worktree_failed", "attempt": attempt, "returncode": add.returncode, "stderr": add.stderr[-2000:], "validationStatus": "not_reached"}
    try:
        merge = run_git(integration_path, ["merge", "--no-ff", "--no-edit", branch_head])
        if merge.returncode != 0:
            conflicts = merge_conflict_paths(merge.stdout, merge.stderr)
            return {
                "clean": False,
                "reason": "merge_failed",
                "attempt": attempt,
                "returncode": merge.returncode,
                "stdout": merge.stdout[-2000:],
                "stderr": merge.stderr[-2000:],
                "conflicts": conflicts,
                "validationStatus": "not_reached",
            }
        diff_check = run_git(integration_path, ["diff", "--check"])
        if diff_check.returncode != 0:
            return {
                "clean": False,
                "reason": "diff_check_failed",
                "attempt": attempt,
                "returncode": diff_check.returncode,
                "stdout": diff_check.stdout[-2000:],
                "stderr": diff_check.stderr[-2000:],
                "validationStatus": "diff_check_failed",
            }
        validations = run_validations(repo_root, config, integration_path)
        failed = next((item for item in validations if item["returncode"] != 0), None)
        if failed:
            return {"clean": False, "reason": "validation_failed", "attempt": attempt, "validations": validations, "validationStatus": "failed"}
        return {
            "clean": True,
            "reason": "clean_merge_and_validation_passed",
            "attempt": attempt,
            "integrationHead": git_stdout(integration_path, ["rev-parse", "HEAD"]),
            "validations": validations,
            "validationStatus": "passed",
        }
    finally:
        remove_worktree(repo_root, integration_path)


def local_branch_rows(repo_root: Path) -> List[Dict[str, Any]]:
    result = run_git(repo_root, ["for-each-ref", "refs/heads", "--format=%(refname:short)%1f%(objectname)%1f%(upstream:short)%1f%(subject)"], check=True)
    rows: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        while len(parts) < 4:
            parts.append("")
        rows.append({"branch": parts[0], "head": parts[1], "upstream": parts[2] or None, "subject": parts[3]})
    return rows


def stash_rows(repo_root: Path) -> List[Dict[str, Any]]:
    result = run_git(repo_root, ["stash", "list", "--format=%gd%x1f%H%x1f%gs"])
    if result.returncode != 0:
        return []
    rows: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        while len(parts) < 3:
            parts.append("")
        rows.append({"ref": parts[0], "head": parts[1], "subject": parts[2]})
    return rows


def remote_feature_patterns(config: Dict[str, Any]) -> List[str]:
    configured = config.get("repoSweep", {}).get("remoteFeaturePatterns", [])
    if isinstance(configured, list) and configured:
        return [str(item) for item in configured]
    return [str(item) for item in config.get("git", {}).get("featureBranchPatterns", [])]


def remote_feature_rows(repo_root: Path, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    git_config = config.get("git", {})
    remote = str(git_config.get("remote", "origin"))
    if not remote_exists(repo_root, remote):
        return []
    if bool(config.get("repoSweep", {}).get("fetchBeforeRemoteSweep", True)):
        run_git(repo_root, ["fetch", "--prune", remote])
    target_branch = str(git_config.get("targetBranch", "master"))
    patterns = remote_feature_patterns(config)
    result = run_git(repo_root, ["for-each-ref", f"refs/remotes/{remote}", "--format=%(refname:short)%1f%(objectname)%1f%(subject)"])
    if result.returncode != 0:
        return []
    rows: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        while len(parts) < 3:
            parts.append("")
        ref_short = parts[0]
        prefix = f"{remote}/"
        if not ref_short.startswith(prefix):
            continue
        branch = ref_short[len(prefix) :]
        if branch == "HEAD" or branch == target_branch:
            continue
        if patterns and not path_matches_any(branch, patterns):
            continue
        rows.append({"remote": remote, "branch": branch, "ref": ref_short, "head": parts[1], "subject": parts[2]})
    return rows


def cherry_rows_between(repo_root: Path, target_head: str, branch_head: str) -> List[str]:
    cherry = run_git(repo_root, ["cherry", "-v", target_head, branch_head])
    if cherry.returncode != 0:
        return []
    return [line.strip() for line in cherry.stdout.splitlines() if line.strip()]


def remote_feature_plan_for(repo_root: Path, config: Dict[str, Any], target: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    branch = str(row["branch"])
    head = str(row["head"])
    target_head = str(target["head"])
    protected = is_protected_branch(config, branch)
    ancestor = is_ancestor(repo_root, head, target_head)
    branch_tree = tree_hash(repo_root, head)
    target_tree = tree_hash(repo_root, target_head)
    tree_equal = branch_tree == target_tree
    cherry = cherry_rows_between(repo_root, target_head, head)
    patch_equivalent = bool(cherry) and all(line.startswith("-") for line in cherry)
    prune_patch_equivalent = bool(blocker_auto_remediation_config(config).get("prunePatchEquivalentBranches", True))
    disposition = "retain_remote_feature"
    reason = "remote feature requires investigation"
    if protected:
        disposition = "retain_protected_remote_feature"
        reason = "remote branch is protected by policy"
    elif ancestor or tree_equal:
        disposition = "prune_integrated_remote_feature"
        reason = "remote feature head is already integrated into target"
    elif patch_equivalent and prune_patch_equivalent:
        disposition = "prune_patch_equivalent_remote_feature"
        reason = "remote feature patch is equivalent to target"
    else:
        disposition = "merge_required_remote_feature"
        reason = "remote feature has work not proven integrated"
    return {
        **row,
        "protected": protected,
        "ancestorOfTarget": ancestor,
        "treeEqualsTarget": tree_equal,
        "branchTree": branch_tree,
        "targetTree": target_tree,
        "cherry": cherry,
        "patchEquivalentToTarget": patch_equivalent,
        "disposition": disposition,
        "reason": reason,
    }


def repo_sweep_plan(repo_root: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    target = target_ref_for(repo_root, config)
    worktrees = parse_worktree_list(repo_root)
    worktree_by_branch = {item.get("branch"): item for item in worktrees if item.get("branch")}
    branch_plans: List[Dict[str, Any]] = []
    for row in local_branch_rows(repo_root):
        branch = str(row["branch"])
        head = str(row["head"])
        worktree = worktree_by_branch.get(branch)
        protected = is_protected_branch(config, branch)
        ancestor = is_ancestor(repo_root, head, target["head"])
        dirty = worktree_dirty_state(Path(str(worktree["path"]))) if worktree else {"exists": False, "dirty": False, "paths": []}
        checked_out = worktree is not None
        disposition = "retain"
        reason = "retained"
        if protected:
            disposition = "retain_protected_branch"
            reason = "protected branch"
        elif checked_out and bool(worktree.get("locked")):
            disposition = "retain_locked_worktree"
            reason = "branch is checked out in a locked worktree"
        elif checked_out and dirty.get("dirty"):
            disposition = "retain_dirty_worktree"
            reason = "branch has dirty worktree state"
        elif checked_out and ancestor:
            disposition = "retain_checked_out_merged_branch"
            reason = "branch is already integrated but checked out"
        elif ancestor:
            disposition = "prune_merged_branch"
            reason = "branch head is already ancestor of target"
        else:
            disposition = "merge_required"
            reason = "branch head is not ancestor of target"
        branch_plans.append(
            {
                **row,
                "protected": protected,
                "checkedOut": checked_out,
                "worktree": worktree,
                "worktreeDirty": dirty,
                "ancestorOfTarget": ancestor,
                "disposition": disposition,
                "reason": reason,
            }
        )
    worktree_plans: List[Dict[str, Any]] = []
    for item in worktrees:
        path = Path(str(item.get("path") or ""))
        dirty = worktree_dirty_state(path)
        detached = bool(item.get("detached") or not item.get("branch"))
        disposition = "retain_branch_worktree"
        reason = "branch worktree belongs to branch closeout"
        if bool(item.get("locked")):
            disposition = "retain_locked_worktree"
            reason = "worktree is locked"
        elif detached and dirty.get("dirty"):
            disposition = "retain_dirty_detached_worktree"
            reason = "detached worktree has dirty files"
        elif detached:
            disposition = "candidate_clean_detached_worktree_prune"
            reason = "detached worktree is clean"
        worktree_plans.append({**item, "dirtyState": dirty, "disposition": disposition, "reason": reason})
    stashes = stash_rows(repo_root)
    stash_plans = [
        {
            **stash,
            "disposition": "retain_stash" if config.get("repoSweep", {}).get("stashMode", "retain") == "retain" else "candidate_stash_drop",
            "reason": "stash mutation is retained unless exact tuple quorum approves a configured drop policy",
        }
        for stash in stashes
    ]
    remote_feature_plans = [remote_feature_plan_for(repo_root, config, target, row) for row in remote_feature_rows(repo_root, config)]
    pinned_refs = {
        "target": {
            "branch": target["targetBranch"],
            "remote": target.get("remote"),
            "ref": target["ref"],
            "head": target["head"],
            "mode": target["mode"],
        },
        "branches": [{"branch": item["branch"], "head": item["head"]} for item in branch_plans],
        "remoteFeatures": [{"remote": item["remote"], "branch": item["branch"], "ref": item["ref"], "head": item["head"]} for item in remote_feature_plans],
        "stashes": [{"ref": item["ref"], "head": item["head"]} for item in stash_plans],
    }
    plan = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "target": target,
        "branchPlans": branch_plans,
        "remoteFeaturePlans": remote_feature_plans,
        "worktreePlans": worktree_plans,
        "stashPlans": stash_plans,
        "pinnedRefs": pinned_refs,
    }
    plan["evidenceHash"] = stable_hash(plan)
    return plan


def repo_sweep_tuple(repo_root_arg: Path) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    plan = repo_sweep_plan(repo_root, config)
    tuple_hash = review_tuple_hash(
        REPO_SWEEP_CANDIDATE_ID,
        REPO_SWEEP_ACTION_ID,
        plan["evidenceHash"],
        str(config.get("policyHash")),
        plan["pinnedRefs"],
    )
    return {
        "candidateId": REPO_SWEEP_CANDIDATE_ID,
        "actionId": REPO_SWEEP_ACTION_ID,
        "evidenceHash": plan["evidenceHash"],
        "policyHash": config.get("policyHash"),
        "pinnedRefs": plan["pinnedRefs"],
        "tupleHash": tuple_hash,
        "plan": plan,
    }


def branch_prune_candidate(config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    pinned_refs = {
        "target": plan["pinnedRefs"]["target"],
        "branch": {"branch": item["branch"], "head": item["head"]},
    }
    evidence = {
        "candidate": {
            "branch": item["branch"],
            "head": item["head"],
            "disposition": item["disposition"],
            "reason": item["reason"],
            "ancestorOfTarget": item["ancestorOfTarget"],
            "checkedOut": item["checkedOut"],
            "protected": item["protected"],
            "worktreeDirty": item["worktreeDirty"],
        },
        "target": plan["pinnedRefs"]["target"],
        "policy": {
            "deleteLocalBranchAfterSuccess": config.get("cleanupPolicy", {}).get("deleteLocalBranchAfterSuccess", True),
            "pruneMergedLocalBranches": config.get("repoSweep", {}).get("pruneMergedLocalBranches", True),
        },
    }
    evidence_hash = stable_hash(evidence)
    return {
        "candidateId": "candidate:branch-prune:%s" % stable_hash({"branch": item["branch"], "head": item["head"], "target": pinned_refs["target"]}, 16),
        "actionId": "delete_local_branch",
        "actionClass": "integrated_branch_prune",
        "evidence": evidence,
        "evidenceHash": evidence_hash,
        "pinnedRefs": pinned_refs,
    }


def remote_feature_action_class(item: Dict[str, Any]) -> str:
    if item.get("disposition") == "prune_patch_equivalent_remote_feature":
        return "patch_equivalent_remote_feature_prune"
    return "integrated_remote_feature_prune"


def remote_feature_prune_candidate(config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    pinned_refs = {
        "target": plan["pinnedRefs"]["target"],
        "remoteFeature": {"remote": item["remote"], "branch": item["branch"], "ref": item["ref"], "head": item["head"]},
    }
    evidence = {
        "candidate": {
            "remote": item["remote"],
            "branch": item["branch"],
            "ref": item["ref"],
            "head": item["head"],
            "disposition": item["disposition"],
            "reason": item["reason"],
            "ancestorOfTarget": item["ancestorOfTarget"],
            "treeEqualsTarget": item["treeEqualsTarget"],
            "patchEquivalentToTarget": item["patchEquivalentToTarget"],
            "cherry": item["cherry"],
            "protected": item["protected"],
        },
        "target": plan["pinnedRefs"]["target"],
        "policy": {
            "pruneRemoteFeatureBranches": config.get("repoSweep", {}).get("pruneRemoteFeatureBranches", True),
            "allowPatchEquivalentPrune": config.get("repoSweep", {}).get("allowPatchEquivalentPrune", True),
            "remoteFeaturePatterns": remote_feature_patterns(config),
        },
    }
    return {
        "candidateId": "candidate:remote-feature-prune:%s" % stable_hash({"remote": item["remote"], "branch": item["branch"], "head": item["head"], "target": pinned_refs["target"]}, 16),
        "actionId": "delete_remote_branch",
        "actionClass": remote_feature_action_class(item),
        "evidence": evidence,
        "evidenceHash": stable_hash(evidence),
        "pinnedRefs": pinned_refs,
    }


def report_candidate_id(prefix: str, item: Dict[str, Any]) -> str:
    return "candidate:%s:%s" % (prefix, stable_hash({"branch": item.get("branch"), "head": item.get("head"), "path": item.get("path")}, 16))


def investigation_agent_payload(config: Dict[str, Any], candidate_id: str, kind: str) -> Dict[str, Any]:
    return {
        "agentId": "agent:%s" % stable_hash({"candidateId": candidate_id, "kind": kind}, 16),
        "agentKind": kind,
        "dispatchMode": config.get("repoSweep", {}).get("agentDispatchMode", "deterministic"),
        "surface": "repo-owned-sweep",
    }


def investigate_branch_candidate(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if item.get("protected"):
        return None
    disposition = str(item.get("disposition") or "")
    backup_probe = {
        "isBackupBranch": path_matches_any(str(item["branch"]), config.get("repoSweep", {}).get("backupBranchPatterns", []))
    }
    if disposition == "prune_merged_branch" and not backup_probe["isBackupBranch"]:
        return None
    branch = str(item["branch"])
    branch_head = str(item["head"])
    target = plan["pinnedRefs"]["target"]
    target_head = str(target["head"])
    candidate_id = report_candidate_id("repo-sweep-investigate", item)
    merge_base = git_stdout(repo_root, ["merge-base", target_head, branch_head], required=False)
    scope = {
        "mergeBase": merge_base,
        "aheadBehind": ahead_behind_between(repo_root, target_head, branch_head),
        "changedPaths": changed_paths_between(repo_root, target_head, branch_head) if merge_base else [],
        "commitSubjects": branch_commit_subjects(repo_root, target_head, branch_head),
        "lastCommitDate": branch_commit_date(repo_root, branch_head),
    }
    backup = backup_branch_analysis(repo_root, config, plan, item)
    dirty_classification = None
    lock = None
    recommended_action = "retain_with_proven_blocker"
    action_class = "ambiguous_merge_required"
    blockers: List[str] = []
    recovery_command = "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\closeout\\repo-sweep-closeout.ps1 -RepoRoot . -Apply"
    worktree = item.get("worktree") or {}
    worktree_path = Path(str(worktree.get("path") or repo_root))
    if item.get("checkedOut") and item.get("worktreeDirty", {}).get("dirty"):
        dirty_classification = classify_dirty_entries_for_branch(
            repo_root,
            config,
            branch=branch,
            branch_head=branch_head,
            target_head=target_head,
            worktree_path=worktree_path,
        )
        foreign_dirty_only = bool(dirty_classification["foreignDirty"]) and not dirty_classification["ownedDirty"] and not dirty_classification["unownedDirty"]
        target_delta_paths = ref_delta_paths(repo_root, branch_head, target_head) if item.get("ancestorOfTarget") else []
        dirty_target_overlap = sorted(set(dirty_classification["dirtyPaths"]) & set(target_delta_paths))
        scope["targetDeltaPaths"] = target_delta_paths
        scope["dirtyTargetDeltaOverlap"] = dirty_target_overlap
        if (
            item.get("ancestorOfTarget")
            and foreign_dirty_only
            and bool(blocker_auto_remediation_config(config).get("allowForeignDirtyIntegratedBranchSwitch", True))
            and not dirty_target_overlap
            and not is_protected_worktree_path(repo_root, config, worktree_path)
        ):
            recommended_action = "switch_target_and_prune"
            action_class = "foreign_dirty_integrated_branch_prune"
            recovery_command = "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\closeout\\repo-sweep-closeout.ps1 -RepoRoot . -Apply"
        elif dirty_classification["ownedDirty"] and dirty_classification.get("workBlockId"):
            recommended_action = "split_now"
            action_class = "dirty_split"
            recovery_command = "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\closeout\\publish-checkpoint.ps1 -RepoRoot . -WorkBlockId %s" % dirty_classification["workBlockId"]
        elif dirty_classification["unownedDirty"]:
            action_class = "unowned_dirty_triage"
            blockers.append("unowned_dirty")
            recovery_command = "claim or remove unowned dirty paths, then rerun repo sweep"
        elif item.get("ancestorOfTarget") and foreign_dirty_only and dirty_target_overlap:
            action_class = "dirty_worktree"
            blockers.append("foreign_dirty_target_overlap")
            recovery_command = "foreign dirty overlaps target delta; preserve or close owning work block, then rerun repo sweep"
        else:
            action_class = "dirty_worktree"
            blockers.append("foreign_dirty_retained")
            recovery_command = "foreign dirty paths retained; rerun repo sweep after owning session closes"
    elif item.get("checkedOut") and bool(worktree.get("locked")):
        lock = inspect_worktree_lock(worktree_path, worktree)
        if is_protected_worktree_path(repo_root, config, worktree_path):
            protected_evidence = protected_worktree_cleanup_evidence(branch, branch_head, worktree_path, lock, target, "cleanup_worktree_and_prune")
            protected_request = explicit_protected_worktree_action(config, protected_evidence)
            scope["protectedWorktreeCleanupEvidence"] = {**protected_evidence, "evidenceHash": stable_hash(protected_evidence)}
            if (
                protected_request
                and not item.get("worktreeDirty", {}).get("dirty")
                and is_stale_lock(lock, config)
                and (item.get("ancestorOfTarget") or backup.get("redundantWith"))
            ):
                recommended_action = "cleanup_worktree_and_prune"
                action_class = "explicit_protected_worktree_cleanup"
                scope["explicitProtectedWorktreeAction"] = protected_request
            else:
                action_class = "active_locked_worktree"
                blockers.append("protected_worktree_root")
                recovery_command = "protected worktree cleanup requires an exact blockerAutoRemediation.explicitProtectedWorktreeActions tuple"
        elif item.get("worktreeDirty", {}).get("dirty"):
            action_class = "locked_worktree"
            blockers.append("locked_dirty_worktree")
            recovery_command = "unlock and resolve dirty paths, then rerun repo sweep"
        elif is_stale_lock(lock, config) and bool(config.get("repoSweep", {}).get("allowStaleLockedWorktreeCleanup", True)):
            if item.get("ancestorOfTarget") or (backup["isBackupBranch"] and backup.get("redundantWith")):
                recommended_action = "cleanup_worktree_and_prune"
                action_class = "stale_locked_worktree_cleanup"
            else:
                merge_probe = simulate_clean_integration(repo_root, config, target_head=target_head, branch_head=branch_head)
                scope["mergeProbe"] = merge_probe
                if merge_probe.get("clean") and str(config.get("repoSweep", {}).get("mergeMode", "auto_clean")) == "auto_clean":
                    recommended_action = "clean_integrate_now"
                    action_class = "repo_sweep_clean_integrate"
                else:
                    action_class = "locked_worktree"
                    blockers.append(str(merge_probe.get("reason") or "locked_merge_required_ambiguous"))
                    recovery_command = "unlock worktree and resolve merge blockers, then rerun repo sweep"
        else:
            action_class = "active_locked_worktree"
            blockers.append("active_or_fresh_lock")
            recovery_command = "git worktree unlock %s" % worktree_path
    elif backup.get("redundantWith") and (
        backup["isBackupBranch"] or bool(blocker_auto_remediation_config(config).get("prunePatchEquivalentBranches", True))
    ):
        recommended_action = "prune_now"
        action_class = "redundant_backup_prune" if backup["isBackupBranch"] else "redundant_branch_prune"
    elif not item.get("ancestorOfTarget"):
        merge_probe = simulate_clean_integration(repo_root, config, target_head=target_head, branch_head=branch_head)
        if merge_probe.get("clean") and str(config.get("repoSweep", {}).get("mergeMode", "auto_clean")) == "auto_clean":
            recommended_action = "clean_integrate_now"
            action_class = "repo_sweep_clean_integrate"
        else:
            blockers.append(str(merge_probe.get("reason") or "merge_required_ambiguous"))
        scope["mergeProbe"] = merge_probe
        resolution_packet = agent_conflict_resolution_packet(config, candidate_id=candidate_id, branch=branch, merge_probe=merge_probe, changed_paths=scope["changedPaths"])
        if resolution_packet:
            scope["agentResolutionPacket"] = resolution_packet
    elif item.get("checkedOut"):
        recommended_action = "cleanup_worktree_and_prune"
        action_class = "integrated_branch_prune"
    report = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "reportType": "repo_sweep_candidate_investigation",
        "candidateId": candidate_id,
        "branch": branch,
        "head": branch_head,
        "sourceDisposition": disposition,
        "reason": item.get("reason"),
        "agentDispatch": investigation_agent_payload(config, candidate_id, "repo-sweep-candidate-investigator"),
        "target": target,
        "scope": scope,
        "backupAnalysis": backup,
        "lockInspection": lock,
        "dirtyClassification": dirty_classification,
        "recommendedAction": recommended_action,
        "actionClass": action_class,
        "blockers": blockers,
        "recoveryCommand": recovery_command,
        "worktree": item.get("worktree"),
        "worktreeDirty": item.get("worktreeDirty"),
    }
    report["remediationProof"] = remediation_proof(
        config,
        recommended_action=recommended_action,
        action_class=action_class,
        blockers=blockers,
    )
    report["evidenceHash"] = stable_hash({key: value for key, value in report.items() if key != "reportPath"})
    return write_candidate_report(repo_root, config, report)


def investigate_remote_feature_candidate(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    disposition = str(item.get("disposition") or "")
    if disposition in {"prune_integrated_remote_feature", "prune_patch_equivalent_remote_feature"}:
        return None
    branch = str(item["branch"])
    branch_head = str(item["head"])
    target = plan["pinnedRefs"]["target"]
    target_head = str(target["head"])
    candidate_id = report_candidate_id("repo-sweep-remote-feature-investigate", item)
    merge_base = git_stdout(repo_root, ["merge-base", target_head, branch_head], required=False)
    scope = {
        "mergeBase": merge_base,
        "aheadBehind": ahead_behind_between(repo_root, target_head, branch_head),
        "changedPaths": changed_paths_between(repo_root, target_head, branch_head) if merge_base else [],
        "commitSubjects": branch_commit_subjects(repo_root, target_head, branch_head),
        "lastCommitDate": branch_commit_date(repo_root, branch_head),
        "cherry": item.get("cherry") or [],
        "patchId": patch_id_for_range(repo_root, target_head, branch_head),
        "treeEqualsTarget": item.get("treeEqualsTarget"),
        "patchEquivalentToTarget": item.get("patchEquivalentToTarget"),
    }
    backup = backup_branch_analysis(repo_root, config, plan, {"branch": branch, "head": branch_head})
    recommended_action = "retain_with_proven_blocker"
    action_class = "ambiguous_merge_required"
    blockers: List[str] = []
    recovery_command = "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\closeout\\remediate-retained-closeout.ps1 -RepoRoot . -Apply"
    if item.get("protected"):
        action_class = "protected_branch"
        blockers.append("protected_remote_branch")
        recovery_command = "change closeout.config.json protectedBranches or manually inspect this remote branch"
    elif backup.get("redundantWith") and bool(blocker_auto_remediation_config(config).get("prunePatchEquivalentBranches", True)):
        recommended_action = "prune_remote_now"
        action_class = "patch_equivalent_remote_feature_prune"
    elif bool(config.get("repoSweep", {}).get("cleanIntegrateRemoteFeatureBranches", True)):
        merge_probe = simulate_clean_integration(repo_root, config, target_head=target_head, branch_head=branch_head)
        scope["mergeProbe"] = merge_probe
        if merge_probe.get("clean") and str(config.get("repoSweep", {}).get("mergeMode", "auto_clean")) == "auto_clean":
            recommended_action = "clean_integrate_remote_now"
            action_class = "remote_feature_clean_integrate"
        else:
            blockers.append(str(merge_probe.get("reason") or "remote_merge_required_ambiguous"))
            resolution_packet = agent_conflict_resolution_packet(config, candidate_id=candidate_id, branch=branch, merge_probe=merge_probe, changed_paths=scope["changedPaths"])
            if resolution_packet:
                scope["agentResolutionPacket"] = resolution_packet
    else:
        blockers.append("remote_feature_clean_integrate_disabled")
    report = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "reportType": "repo_sweep_remote_feature_investigation",
        "candidateId": candidate_id,
        "remote": item.get("remote"),
        "remoteRef": item.get("ref"),
        "branch": branch,
        "head": branch_head,
        "sourceDisposition": disposition,
        "reason": item.get("reason"),
        "agentDispatch": investigation_agent_payload(config, candidate_id, "repo-sweep-remote-feature-investigator"),
        "target": target,
        "scope": scope,
        "backupAnalysis": backup,
        "lockInspection": None,
        "dirtyClassification": None,
        "recommendedAction": recommended_action,
        "actionClass": action_class,
        "blockers": blockers,
        "recoveryCommand": recovery_command,
        "worktree": None,
        "worktreeDirty": {"exists": False, "dirty": False, "paths": []},
    }
    report["remediationProof"] = remediation_proof(
        config,
        recommended_action=recommended_action,
        action_class=action_class,
        blockers=blockers,
    )
    report["evidenceHash"] = stable_hash({key: value for key, value in report.items() if key != "reportPath"})
    return write_candidate_report(repo_root, config, report)


def detached_dirty_preservation_branch(config: Dict[str, Any], candidate_id: str) -> str:
    prefix = normalize_rel(str(blocker_auto_remediation_config(config).get("detachedDirtyBranchPrefix") or "closeout/recovery/detached")).strip("/")
    return "%s/%s" % (prefix, stable_hash(candidate_id, 12))


def is_recovery_branch(config: Dict[str, Any], branch: str) -> bool:
    prefix = normalize_rel(str(blocker_auto_remediation_config(config).get("detachedDirtyBranchPrefix") or "closeout/recovery/detached")).strip("/")
    return normalize_rel(branch).startswith(prefix + "/")


def investigate_worktree_candidate(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    disposition = str(item.get("disposition") or "")
    if disposition != "retain_dirty_detached_worktree":
        return None
    path = Path(str(item.get("path") or ""))
    dirty_state = item.get("dirtyState") or worktree_dirty_state(path)
    entries = parse_status_paths(path) if path.exists() else []
    paths = sorted({str(entry["path"]) for entry in entries})
    sensitive_paths = [candidate for candidate in paths if path_matches_any(candidate, config.get("paths", {}).get("sensitive", []))]
    auto = blocker_auto_remediation_config(config)
    candidate_id = report_candidate_id("repo-sweep-worktree-investigate", item)
    recommended_action = "retain_with_proven_blocker"
    action_class = "dirty_detached_worktree"
    blockers: List[str] = []
    recovery_command = "preserve or remove detached dirty paths, then rerun repo sweep"
    if len(paths) > int(auto.get("maxDetachedDirtyPaths", 25)):
        blockers.append("too_many_dirty_paths")
    if sensitive_paths and not bool(auto.get("allowSensitiveDetachedDirtyPreservation", False)):
        blockers.append("sensitive_dirty_paths")
    if not bool(auto.get("allowDetachedDirtyPreservation", True)):
        blockers.append("detached_dirty_preservation_disabled")
    if not blockers and paths:
        recommended_action = "preserve_detached_dirty_now"
        action_class = "detached_dirty_preserve"
        recovery_command = "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\closeout\\repo-sweep-closeout.ps1 -RepoRoot . -Apply"
    report = {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "reportType": "repo_sweep_worktree_investigation",
        "candidateId": candidate_id,
        "branch": None,
        "head": item.get("head"),
        "sourceDisposition": disposition,
        "reason": item.get("reason"),
        "agentDispatch": investigation_agent_payload(config, candidate_id, "repo-sweep-detached-dirty-investigator"),
        "target": plan["pinnedRefs"]["target"],
        "scope": {
            "dirtyPaths": paths,
            "sensitivePaths": sensitive_paths,
            "statusEntries": entries,
        },
        "backupAnalysis": None,
        "lockInspection": inspect_worktree_lock(path, item) if bool(item.get("locked")) else None,
        "dirtyClassification": {
            "worktreePath": str(path),
            "dirtyPaths": paths,
            "sensitivePaths": sensitive_paths,
            "entries": entries,
            "eligible": not blockers,
        },
        "recommendedAction": recommended_action,
        "actionClass": action_class,
        "blockers": blockers,
        "recoveryCommand": recovery_command,
        "worktree": item,
        "worktreeDirty": dirty_state,
        "preservationBranch": detached_dirty_preservation_branch(config, candidate_id),
    }
    report["remediationProof"] = remediation_proof(
        config,
        recommended_action=recommended_action,
        action_class=action_class,
        blockers=blockers,
    )
    report["evidenceHash"] = stable_hash({key: value for key, value in report.items() if key != "reportPath"})
    return write_candidate_report(repo_root, config, report)


def investigation_reports(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not bool(config.get("repoSweep", {}).get("investigateRetainedCandidates", True)):
        return []
    branch_reports = [
        report
        for report in (investigate_branch_candidate(repo_root, config, plan, item) for item in plan["branchPlans"])
        if report is not None
    ]
    remote_feature_reports = [
        report
        for report in (investigate_remote_feature_candidate(repo_root, config, plan, item) for item in plan.get("remoteFeaturePlans", []))
        if report is not None
    ]
    worktree_reports = [
        report
        for report in (investigate_worktree_candidate(repo_root, config, plan, item) for item in plan["worktreePlans"])
        if report is not None
    ]
    reports = branch_reports + remote_feature_reports + worktree_reports
    return sorted(reports, key=lambda item: (str(item.get("actionClass")), str(item.get("branch")), str(item.get("head"))))


def candidate_from_report(config: Dict[str, Any], plan: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    action = str(report["recommendedAction"])
    if action == "clean_integrate_now":
        action_id = "clean_integrate"
    elif action == "clean_integrate_remote_now":
        action_id = "clean_integrate"
    elif action == "cleanup_worktree_and_prune":
        action_id = "worktree_cleanup"
    elif action == "split_now":
        action_id = "split"
    elif action == "switch_target_and_prune":
        action_id = "worktree_cleanup"
    elif action == "preserve_detached_dirty_now":
        action_id = "orphan_quarantine"
    elif action == "prune_remote_now":
        action_id = "delete_remote_branch"
    else:
        action_id = "delete_local_branch"
    pinned_refs = {
        "target": plan["pinnedRefs"]["target"],
        "branch": {"branch": report.get("branch"), "head": report.get("head")},
        "remoteFeature": {"remote": report.get("remote"), "branch": report.get("branch"), "ref": report.get("remoteRef"), "head": report.get("head")} if report.get("remoteRef") else None,
        "worktree": report.get("worktree"),
    }
    evidence = {
        "candidateReport": {key: value for key, value in report.items() if key != "reportPath"},
        "policy": {
            "mergeMode": config.get("repoSweep", {}).get("mergeMode"),
            "allowCleanCheckedOutIntegration": config.get("repoSweep", {}).get("allowCleanCheckedOutIntegration", True),
            "allowStaleLockedWorktreeCleanup": config.get("repoSweep", {}).get("allowStaleLockedWorktreeCleanup", True),
            "retainedBlockerAutoRemediation": config.get("repoSweep", {}).get("retainedBlockerAutoRemediation"),
            "allowForeignDirtyIntegratedBranchPrune": config.get("repoSweep", {}).get("allowForeignDirtyIntegratedBranchPrune"),
            "allowPatchEquivalentPrune": config.get("repoSweep", {}).get("allowPatchEquivalentPrune"),
            "protectedLockedWorktreeExactPolicy": config.get("repoSweep", {}).get("protectedLockedWorktreeExactPolicy"),
            "pruneRemoteFeatureBranches": config.get("repoSweep", {}).get("pruneRemoteFeatureBranches"),
            "cleanIntegrateRemoteFeatureBranches": config.get("repoSweep", {}).get("cleanIntegrateRemoteFeatureBranches"),
            "deleteRemoteFeatureAfterCleanIntegrate": config.get("repoSweep", {}).get("deleteRemoteFeatureAfterCleanIntegrate"),
        },
    }
    return {
        "candidateId": "candidate:repo-sweep-action:%s" % stable_hash({"report": report["candidateId"], "action": action}, 16),
        "actionId": action_id,
        "actionClass": report["actionClass"],
        "evidence": evidence,
        "evidenceHash": stable_hash(evidence),
        "pinnedRefs": pinned_refs,
        "reportPath": report.get("reportPath"),
    }


def cleanup_branch_after_sweep_action(
    repo_root: Path,
    config: Dict[str, Any],
    plan: Dict[str, Any],
    item: Dict[str, Any],
    *,
    force_branch_delete: bool = False,
    allow_protected_worktree: bool = False,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    branch = str(item["branch"])
    worktree = item.get("worktree") or {}
    target_branch = str(plan["pinnedRefs"]["target"]["branch"])
    if item.get("checkedOut"):
        path = Path(str(worktree.get("path") or repo_root))
        dirty = worktree_dirty_state(path)
        if dirty.get("dirty"):
            action = {"action": "retain_checked_out_dirty_branch", "branch": branch, "dirty": dirty, "returncode": 1}
            actions.append(action)
            write_audit(repo_root, config, "cleanup_retention", action, outcome="retained")
            return actions
        if path.resolve() == repo_root.resolve():
            switch = run_git(repo_root, ["switch", target_branch])
            action = {"action": "switch_sweep_worktree_to_target", "branch": branch, "targetBranch": target_branch, "returncode": switch.returncode, "stderr": switch.stderr[-2000:]}
            actions.append(action)
            if switch.returncode != 0:
                write_audit(repo_root, config, "cleanup_retention", action, outcome="retained")
                return actions
        else:
            if is_protected_worktree_path(repo_root, config, path) and not allow_protected_worktree:
                action = {"action": "retain_protected_worktree", "branch": branch, "path": str(path), "returncode": 1, "reason": "protected_worktree_root"}
                actions.append(action)
                write_audit(repo_root, config, "cleanup_retention", action, outcome="retained")
                return actions
            action = {"action": "remove_branch_worktree", **remove_worktree(repo_root, path)}
            actions.append(action)
            write_audit(repo_root, config, "worktree_deletion", action, outcome="success" if action["returncode"] == 0 else "blocked")
            if action["returncode"] != 0:
                return actions
    force_reason = None
    if not force_branch_delete:
        branch_head = rev_parse(repo_root, f"refs/heads/{branch}", required=False)
        target_head = rev_parse(repo_root, f"refs/heads/{target_branch}", required=False) or str(plan["pinnedRefs"]["target"]["head"])
        if branch_head and target_head and is_ancestor(repo_root, branch_head, target_head):
            force_branch_delete = current_branch(repo_root) != target_branch
            force_reason = "branch_head_is_ancestor_of_target"
    delete_flag = "-D" if force_branch_delete else "-d"
    delete = run_git(repo_root, ["branch", delete_flag, branch])
    action = {"action": "delete_local_branch", "branch": branch, "forced": force_branch_delete, "forceReason": force_reason, "returncode": delete.returncode, "stderr": delete.stderr[-2000:]}
    actions.append(action)
    write_audit(repo_root, config, "branch_deletion", action, outcome="success" if delete.returncode == 0 else "blocked")
    return actions


def cleanup_foreign_dirty_integrated_branch(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    branch = str(item["branch"])
    branch_head = str(item["head"])
    target = plan["pinnedRefs"]["target"]
    target_head = str(target["head"])
    target_branch = str(target["branch"])
    worktree = item.get("worktree") or {}
    worktree_path = Path(str(worktree.get("path") or repo_root))
    if not is_ancestor(repo_root, branch_head, target_head):
        action = {"status": "blocked", "reason": "branch_not_ancestor_of_target", "branch": branch, "head": branch_head, "targetHead": target_head}
        write_audit(repo_root, config, "blocked_repair", action, outcome="blocked")
        return action
    current_head = rev_parse(repo_root, f"refs/heads/{branch}", required=False)
    if current_head != branch_head:
        action = {"status": "blocked", "reason": "branch_head_drifted", "branch": branch, "expected": branch_head, "actual": current_head}
        write_audit(repo_root, config, "stale_refs", action, outcome="blocked")
        return action
    classification = classify_dirty_entries_for_branch(
        repo_root,
        config,
        branch=branch,
        branch_head=branch_head,
        target_head=target_head,
        worktree_path=worktree_path,
    )
    if classification["ownedDirty"] or classification["unownedDirty"]:
        action = {"status": "blocked", "reason": "dirty_classification_changed", "classification": classification, "report": report}
        write_audit(repo_root, config, "blocked_repair", action, outcome="blocked")
        return action
    target_delta = ref_delta_paths(repo_root, branch_head, target_head)
    overlap = sorted(set(classification["dirtyPaths"]) & set(target_delta))
    if overlap:
        action = {"status": "blocked", "reason": "foreign_dirty_target_overlap", "overlap": overlap, "classification": classification}
        write_audit(repo_root, config, "cleanup_retention", action, outcome="retained")
        return action
    if worktree_path.resolve() == repo_root.resolve():
        switch = run_git(worktree_path, ["switch", target_branch])
        switch_action = {"action": "switch_foreign_dirty_worktree_to_target", "branch": branch, "targetBranch": target_branch, "returncode": switch.returncode, "stderr": switch.stderr[-2000:]}
    else:
        switch = run_git(worktree_path, ["switch", "--detach", target_head])
        switch_action = {"action": "detach_foreign_dirty_worktree_at_target", "branch": branch, "targetHead": target_head, "path": str(worktree_path), "returncode": switch.returncode, "stderr": switch.stderr[-2000:]}
    if switch.returncode != 0:
        payload = {"status": "blocked", "reason": "target_switch_failed", "switch": switch_action, "classification": classification}
        write_audit(repo_root, config, "cleanup_retention", payload, outcome="retained")
        return payload
    delete = run_git(repo_root, ["branch", "-D", branch])
    delete_action = {"action": "delete_local_branch", "branch": branch, "forced": True, "forceReason": "foreign_dirty_worktree_switched_to_target", "returncode": delete.returncode, "stderr": delete.stderr[-2000:]}
    write_audit(repo_root, config, "branch_deletion", delete_action, outcome="success" if delete.returncode == 0 else "blocked")
    result = {
        "status": "success" if delete.returncode == 0 else "blocked",
        "action": "foreign_dirty_integrated_branch_prune",
        "branch": branch,
        "switch": switch_action,
        "delete": delete_action,
        "retainedForeignDirty": classification["foreignDirty"],
    }
    write_audit(repo_root, config, "cleanup_retention", {"action": "retained_foreign_dirty_after_branch_prune", **result}, outcome="retained")
    return result


def apply_detached_dirty_preserve(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    worktree = report.get("worktree") or {}
    worktree_path = Path(str(worktree.get("path") or ""))
    if not worktree_path.exists():
        action = {"status": "blocked", "reason": "detached_worktree_missing", "report": report}
        write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
        return action
    current_entries = parse_status_paths(worktree_path)
    current_paths = sorted({str(item["path"]) for item in current_entries})
    expected_paths = sorted(report.get("scope", {}).get("dirtyPaths") or [])
    if current_paths != expected_paths:
        action = {"status": "blocked", "reason": "detached_dirty_tuple_drifted", "expectedPaths": expected_paths, "actualPaths": current_paths, "report": report}
        write_audit(repo_root, config, "stale_refs", action, outcome="blocked")
        return action
    sensitive_paths = [path for path in current_paths if path_matches_any(path, config.get("paths", {}).get("sensitive", []))]
    if sensitive_paths and not bool(blocker_auto_remediation_config(config).get("allowSensitiveDetachedDirtyPreservation", False)):
        action = {"status": "blocked", "reason": "sensitive_dirty_paths", "sensitivePaths": sensitive_paths, "report": report}
        write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
        return action
    branch = str(report.get("preservationBranch") or detached_dirty_preservation_branch(config, str(report["candidateId"])))
    base_head = str(report.get("head") or worktree.get("head") or "")
    existing_branch_head = rev_parse(repo_root, f"refs/heads/{branch}", required=False)
    if existing_branch_head and existing_branch_head != base_head:
        action = {"status": "blocked", "reason": "preservation_branch_exists", "branch": branch, "head": existing_branch_head, "expected": base_head}
        write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
        return action
    preservation_path = closeout_state_root(repo_root, config) / "repo-sweep" / "detached-preserve" / safe_state_name(branch)
    if preservation_path.exists():
        action = {"status": "blocked", "reason": "preservation_worktree_exists", "path": str(preservation_path), "report": report}
        write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
        return action
    add_args = ["worktree", "add", "--no-checkout"]
    if existing_branch_head:
        add_args.extend([str(preservation_path), branch])
    else:
        add_args.extend(["-b", branch, str(preservation_path), base_head])
    add = run_git_longpaths(repo_root, add_args)
    if add.returncode != 0:
        action = {"status": "blocked", "reason": "preservation_worktree_failed", "returncode": add.returncode, "stderr": add.stderr[-3000:], "report": report}
        write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
        return action
    copied: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    try:
        for operation, args in [
            ("sparse_checkout_init", ["sparse-checkout", "init", "--no-cone"]),
            ("sparse_checkout_set", ["sparse-checkout", "set", "--no-cone", *current_paths]),
            ("sparse_checkout_checkout", ["checkout"]),
        ]:
            result = run_git(preservation_path, args)
            if result.returncode != 0:
                cleanup = remove_worktree(repo_root, preservation_path)
                action = {"status": "blocked", "reason": "preservation_sparse_checkout_failed", "operation": operation, "returncode": result.returncode, "stderr": result.stderr[-3000:], "cleanup": cleanup}
                write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
                return action
        entries_by_path = {str(item["path"]): item for item in current_entries}
        for path in current_paths:
            copied.append(copy_exact_path_for_split(worktree_path, preservation_path, path, str(entries_by_path[path]["status"])))
        add_preserved = run_git(preservation_path, ["add", "--", *current_paths])
        if add_preserved.returncode != 0:
            action = {"status": "blocked", "reason": "preservation_add_failed", "returncode": add_preserved.returncode, "stderr": add_preserved.stderr[-3000:], "copied": copied}
            write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
            return action
        commit = run_git(preservation_path, ["commit", "-m", "preserve detached dirty worktree"])
        if commit.returncode != 0:
            action = {"status": "blocked", "reason": "preservation_commit_failed", "returncode": commit.returncode, "stdout": commit.stdout[-3000:], "stderr": commit.stderr[-3000:], "copied": copied}
            write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
            return action
        commit_head = git_stdout(preservation_path, ["rev-parse", "HEAD"])
        verification = verify_detached_preservation_commit(repo_root, branch=branch, commit_head=commit_head, entries_by_path=entries_by_path)
        if not verification["ok"]:
            action = {
                "status": "blocked",
                "reason": "preservation_commit_stale_or_incomplete",
                "verification": verification,
                "copied": copied,
                "report": report,
            }
            write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
            return action
        for path in current_paths:
            removal = remove_exact_path_from_worktree(worktree_path, path)
            removed.append(removal)
            if removal.get("returncode") != 0:
                action = {"status": "blocked", "reason": "original_cleanup_failed", "preservationBranch": branch, "preservationHead": commit_head, "removed": removed}
                write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
                return action
        remaining_dirty = worktree_dirty_state(worktree_path)
        remove_original = remove_worktree(repo_root, worktree_path) if not remaining_dirty.get("dirty") else {"returncode": 1, "stderr": "worktree still dirty", "path": str(worktree_path)}
        result = {
            "status": "success" if remove_original.get("returncode") == 0 else "blocked",
            "action": "detached_dirty_preserve",
            "preservationBranch": branch,
            "preservationHead": commit_head,
            "preservationWorktree": str(preservation_path),
            "copied": copied,
            "removedFromOriginal": removed,
            "originalWorktreeCleanup": remove_original,
        }
        write_audit(repo_root, config, "orphan_quarantine", result, outcome="success" if result["status"] == "success" else "blocked")
        return result
    except Exception as exc:
        action = {"status": "blocked", "reason": "detached_dirty_preserve_exception", "error": str(exc), "copied": copied, "removed": removed, "report": report}
        write_audit(repo_root, config, "orphan_quarantine", action, outcome="blocked")
        return action


def apply_repo_sweep_clean_integrate(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    target = target_ref_for(repo_root, config)
    branch = str(item["branch"])
    branch_head = str(item["head"])
    target_branch = str(plan["pinnedRefs"]["target"]["branch"])
    if target["head"] != plan["pinnedRefs"]["target"]["head"]:
        return {"status": "blocked", "reason": "target_head_drifted", "expected": plan["pinnedRefs"]["target"], "actual": target}
    current_head = rev_parse(repo_root, f"refs/heads/{branch}", required=False)
    if current_head != branch_head:
        return {"status": "blocked", "reason": "branch_head_drifted", "branch": branch, "expected": branch_head, "actual": current_head}
    integration_path = closeout_state_root(repo_root, config) / "rs-iw" / uuid.uuid4().hex[:12]
    integration_path.parent.mkdir(parents=True, exist_ok=True)
    add = run_git_longpaths(repo_root, ["worktree", "add", "--detach", str(integration_path), target["head"]])
    if add.returncode != 0:
        return {"status": "blocked", "reason": "integration_worktree_failed", "returncode": add.returncode, "stderr": add.stderr[-2000:]}
    try:
        merge = run_git(integration_path, ["merge", "--no-ff", "--no-edit", branch_head])
        if merge.returncode != 0:
            return {"status": "blocked", "reason": "merge_failed", "returncode": merge.returncode, "stdout": merge.stdout[-2000:], "stderr": merge.stderr[-2000:]}
        diff_check = run_git(integration_path, ["diff", "--check"])
        if diff_check.returncode != 0:
            return {"status": "blocked", "reason": "diff_check_failed", "returncode": diff_check.returncode, "stdout": diff_check.stdout[-2000:], "stderr": diff_check.stderr[-2000:]}
        validations = run_validations(repo_root, config, integration_path)
        failed = next((row for row in validations if row["returncode"] != 0), None)
        if failed:
            return {"status": "blocked", "reason": "validation_failed", "validations": validations}
        new_head = git_stdout(integration_path, ["rev-parse", "HEAD"])
        push_result: Optional[Dict[str, Any]] = None
        recovery: Optional[Dict[str, Any]] = None
        local_update: Optional[Dict[str, Any]] = None
        if target["mode"] == "remote":
            push = run_git(integration_path, ["push", str(target.get("remote")), f"HEAD:{target_branch}"])
            push_result = {"remote": target.get("remote"), "targetBranch": target_branch, "returncode": push.returncode, "stdout": push.stdout[-2000:], "stderr": push.stderr[-2000:]}
            if push.returncode != 0:
                recovery = repair_target_push_failure(
                    repo_root,
                    config,
                    target_branch=target_branch,
                    remote=str(target.get("remote")),
                    attempted_head=new_head,
                    push_result=push_result,
                )
                if recovery["status"] != "success":
                    return {"status": "blocked", "reason": recovery["reason"], "push": push_result, "recovery": recovery}
                new_head = str(recovery["targetHeadAfter"])
                local_update = recovery.get("localTargetUpdate")
        if local_update is None:
            local_update = update_local_target(repo_root, target_branch, new_head)
        if local_update["returncode"] != 0:
            return {"status": "blocked", "reason": "local_target_update_failed", "localUpdate": local_update, "push": push_result}
        cleanup = cleanup_branch_after_sweep_action(repo_root, config, plan, item)
        result = {"status": "success", "action": "clean_integrate", "branch": branch, "newTargetHead": new_head, "validations": validations, "push": push_result, "recovery": recovery, "localUpdate": local_update, "cleanup": cleanup}
        write_audit(repo_root, config, "success", result, outcome="success")
        return result
    finally:
        removal = remove_worktree(repo_root, integration_path)
        write_audit(repo_root, config, "snapshot_pruning", {"action": "integration_worktree_remove", **removal}, outcome="success" if removal["returncode"] == 0 else "blocked")


def delete_remote_feature_ref(repo_root: Path, config: Dict[str, Any], *, remote: str, branch: str, expected_head: str) -> Dict[str, Any]:
    ref = f"refs/remotes/{remote}/{branch}"
    current_head = rev_parse(repo_root, ref, required=False)
    if current_head is None:
        action = {"status": "success", "action": "delete_remote_branch", "remote": remote, "branch": branch, "alreadyMissing": True, "expectedHead": expected_head}
        write_audit(repo_root, config, "remote_branch_deletion", action, outcome="success")
        return action
    if current_head != expected_head:
        action = {"status": "blocked", "reason": "remote_feature_head_drifted", "remote": remote, "branch": branch, "expected": expected_head, "actual": current_head}
        write_audit(repo_root, config, "stale_refs", action, outcome="blocked")
        return action
    delete = run_git(repo_root, ["push", remote, "--delete", branch])
    run_git(repo_root, ["fetch", "--prune", remote])
    action = {
        "status": "success" if delete.returncode == 0 else "blocked",
        "action": "delete_remote_branch",
        "remote": remote,
        "branch": branch,
        "expectedHead": expected_head,
        "returncode": delete.returncode,
        "stdout": delete.stdout[-2000:],
        "stderr": delete.stderr[-2000:],
    }
    write_audit(repo_root, config, "remote_branch_deletion", action, outcome="success" if delete.returncode == 0 else "blocked")
    return action


def remote_feature_prune_still_eligible(repo_root: Path, item: Dict[str, Any], target_head: str) -> bool:
    head = str(item["head"])
    if is_ancestor(repo_root, head, target_head):
        return True
    if tree_hash(repo_root, head) == tree_hash(repo_root, target_head):
        return True
    if item.get("disposition") == "prune_patch_equivalent_remote_feature":
        cherry = cherry_rows_between(repo_root, target_head, head)
        return bool(cherry) and all(line.startswith("-") for line in cherry)
    return False


def apply_remote_feature_prune(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    target = target_ref_for(repo_root, config)
    if target["head"] != plan["pinnedRefs"]["target"]["head"]:
        action = {"status": "blocked", "reason": "target_head_drifted", "expected": plan["pinnedRefs"]["target"], "actual": target}
        write_audit(repo_root, config, "stale_refs", action, outcome="blocked")
        return action
    remote = str(item["remote"])
    branch = str(item["branch"])
    current_head = rev_parse(repo_root, str(item["ref"]), required=False)
    if current_head != item["head"]:
        action = {"status": "blocked", "reason": "remote_feature_head_drifted", "remote": remote, "branch": branch, "expected": item["head"], "actual": current_head}
        write_audit(repo_root, config, "stale_refs", action, outcome="blocked")
        return action
    if not remote_feature_prune_still_eligible(repo_root, item, str(target["head"])):
        action = {"status": "blocked", "reason": "remote_feature_no_longer_prunable", "remote": remote, "branch": branch, "head": item["head"], "target": target}
        write_audit(repo_root, config, "cleanup_retention", action, outcome="retained")
        return action
    return delete_remote_feature_ref(repo_root, config, remote=remote, branch=branch, expected_head=str(item["head"]))


def apply_remote_feature_clean_integrate(repo_root: Path, config: Dict[str, Any], plan: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    target = target_ref_for(repo_root, config)
    if target["head"] != plan["pinnedRefs"]["target"]["head"]:
        return {"status": "blocked", "reason": "target_head_drifted", "expected": plan["pinnedRefs"]["target"], "actual": target}
    remote = str(report.get("remote") or target.get("remote") or config.get("git", {}).get("remote", "origin"))
    branch = str(report["branch"])
    remote_ref = str(report.get("remoteRef") or f"{remote}/{branch}")
    branch_head = str(report["head"])
    current_head = rev_parse(repo_root, remote_ref, required=False)
    if current_head != branch_head:
        action = {"status": "blocked", "reason": "remote_feature_head_drifted", "remote": remote, "branch": branch, "expected": branch_head, "actual": current_head}
        write_audit(repo_root, config, "stale_refs", action, outcome="blocked")
        return action
    target_branch = str(plan["pinnedRefs"]["target"]["branch"])
    integration_path = closeout_state_root(repo_root, config) / "rs-remote-iw" / uuid.uuid4().hex[:12]
    integration_path.parent.mkdir(parents=True, exist_ok=True)
    add = run_git_longpaths(repo_root, ["worktree", "add", "--detach", str(integration_path), target["head"]])
    if add.returncode != 0:
        return {"status": "blocked", "reason": "integration_worktree_failed", "returncode": add.returncode, "stderr": add.stderr[-2000:]}
    try:
        merge = run_git(integration_path, ["merge", "--no-ff", "--no-edit", branch_head])
        if merge.returncode != 0:
            return {"status": "blocked", "reason": "merge_failed", "returncode": merge.returncode, "stdout": merge.stdout[-2000:], "stderr": merge.stderr[-2000:]}
        diff_check = run_git(integration_path, ["diff", "--check"])
        if diff_check.returncode != 0:
            return {"status": "blocked", "reason": "diff_check_failed", "returncode": diff_check.returncode, "stdout": diff_check.stdout[-2000:], "stderr": diff_check.stderr[-2000:]}
        validations = run_validations(repo_root, config, integration_path)
        failed = next((row for row in validations if row["returncode"] != 0), None)
        if failed:
            return {"status": "blocked", "reason": "validation_failed", "validations": validations}
        new_head = git_stdout(integration_path, ["rev-parse", "HEAD"])
        push_result: Optional[Dict[str, Any]] = None
        if target["mode"] == "remote":
            push = run_git(integration_path, ["push", str(target.get("remote")), f"HEAD:{target_branch}"])
            push_result = {"remote": target.get("remote"), "targetBranch": target_branch, "returncode": push.returncode, "stdout": push.stdout[-2000:], "stderr": push.stderr[-2000:]}
            if push.returncode != 0:
                return {"status": "blocked", "reason": "target_push_failed", "push": push_result}
        local_update = update_local_target(repo_root, target_branch, new_head)
        if local_update["returncode"] != 0:
            return {"status": "blocked", "reason": "local_target_update_failed", "localUpdate": local_update, "push": push_result}
        remote_cleanup: Optional[Dict[str, Any]] = None
        if bool(config.get("repoSweep", {}).get("deleteRemoteFeatureAfterCleanIntegrate", True)):
            remote_cleanup = delete_remote_feature_ref(repo_root, config, remote=remote, branch=branch, expected_head=branch_head)
        result = {
            "status": "success" if not remote_cleanup or remote_cleanup.get("status") == "success" else "blocked",
            "action": "clean_integrate_remote_feature",
            "remote": remote,
            "branch": branch,
            "newTargetHead": new_head,
            "validations": validations,
            "push": push_result,
            "localUpdate": local_update,
            "remoteCleanup": remote_cleanup,
        }
        write_audit(repo_root, config, "success" if result["status"] == "success" else "cleanup_retention", result, outcome="success" if result["status"] == "success" else "blocked")
        return result
    finally:
        removal = remove_worktree(repo_root, integration_path)
        write_audit(repo_root, config, "snapshot_pruning", {"action": "integration_worktree_remove", **removal}, outcome="success" if removal["returncode"] == 0 else "blocked")


def repo_sweep(repo_root_arg: Path, *, apply: bool = False, candidate_id: Optional[str] = None) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    tooling = verify_closeout_tooling_current(repo_root, config)
    if not tooling["ok"]:
        return {"status": "blocked", "reason": "closeout_tooling_stale", "tooling": tooling}
    if not bool(config.get("repoSweep", {}).get("enabled", True)):
        return {"status": "disabled", "reason": "repoSweep.enabled is false"}
    plan = repo_sweep_plan(repo_root, config)
    tuple_info = repo_sweep_tuple(repo_root)
    backup_patterns = config.get("repoSweep", {}).get("backupBranchPatterns", [])
    prunable_branches = [
        item
        for item in plan["branchPlans"]
        if item["disposition"] == "prune_merged_branch" and not path_matches_any(str(item["branch"]), backup_patterns)
        and not is_recovery_branch(config, str(item["branch"]))
    ]
    prunable_remote_features = [
        item
        for item in plan.get("remoteFeaturePlans", [])
        if item["disposition"] in {"prune_integrated_remote_feature", "prune_patch_equivalent_remote_feature"}
    ]
    candidate_worktrees = [item for item in plan["worktreePlans"] if item["disposition"] == "candidate_clean_detached_worktree_prune"]
    candidate_stashes = [item for item in plan["stashPlans"] if item["disposition"] == "candidate_stash_drop"]
    branch_candidates = [branch_prune_candidate(config, plan, item) for item in prunable_branches]
    remote_feature_candidates = [remote_feature_prune_candidate(config, plan, item) for item in prunable_remote_features]
    retained_reports = investigation_reports(repo_root, config, plan)
    promoted_reports = [
        report
        for report in retained_reports
        if report.get("recommendedAction")
        in {"clean_integrate_now", "clean_integrate_remote_now", "prune_now", "prune_remote_now", "cleanup_worktree_and_prune", "split_now", "switch_target_and_prune", "preserve_detached_dirty_now"}
    ]
    promoted_candidates = [candidate_from_report(config, plan, report) for report in promoted_reports]
    follow_up_candidates = retained_reports
    if not apply:
        payload = {
            "plan": plan,
            "tuple": tuple_info,
            "branchCandidates": branch_candidates,
            "remoteFeatureCandidates": remote_feature_candidates,
            "retainedCandidateReports": retained_reports,
            "promotedCandidates": promoted_candidates,
            "followUpCandidates": follow_up_candidates,
        }
        write_audit(repo_root, config, "cleanup_retention", payload, outcome="recorded")
        return {"status": "planned", **payload}
    actions: List[Dict[str, Any]] = []
    quorum_results: List[Dict[str, Any]] = []
    matched_candidate = candidate_id is None
    if bool(config.get("repoSweep", {}).get("pruneMergedLocalBranches", True)):
        for item, candidate in zip(prunable_branches, branch_candidates):
            if candidate_id and candidate["candidateId"] != candidate_id:
                continue
            matched_candidate = True
            current_head = rev_parse(repo_root, f"refs/heads/{item['branch']}", required=False)
            blockers: List[str] = []
            if current_head != item["head"]:
                blockers.append("branch_head_drifted")
            if not is_ancestor(repo_root, str(item["head"]), str(plan["pinnedRefs"]["target"]["head"])):
                blockers.append("branch_not_ancestor_of_target")
            quorum_result = ensure_autonomous_quorum(
                repo_root,
                config,
                candidate_id=candidate["candidateId"],
                action_id=candidate["actionId"],
                evidence_hash=candidate["evidenceHash"],
                pinned_refs=candidate["pinnedRefs"],
                evidence=candidate["evidence"],
                action_class=candidate["actionClass"],
                blockers=blockers,
            )
            quorum_results.append({"candidate": candidate, **quorum_result})
            if not quorum_result["quorum"]["ok"]:
                write_audit(repo_root, config, "review_quorum_blocked", {"candidate": candidate, **quorum_result}, outcome="blocked")
                continue
            actions.extend(cleanup_branch_after_sweep_action(repo_root, config, plan, item))
    if bool(config.get("repoSweep", {}).get("pruneRemoteFeatureBranches", True)):
        for item, candidate in zip(prunable_remote_features, remote_feature_candidates):
            if candidate_id and candidate["candidateId"] != candidate_id:
                continue
            matched_candidate = True
            blockers: List[str] = []
            current_head = rev_parse(repo_root, str(item["ref"]), required=False)
            current_target = target_ref_for(repo_root, config)
            if current_head != item["head"]:
                blockers.append("remote_feature_head_drifted")
            if current_target["head"] != plan["pinnedRefs"]["target"]["head"]:
                blockers.append("target_head_drifted")
            if not blockers and not remote_feature_prune_still_eligible(repo_root, item, str(plan["pinnedRefs"]["target"]["head"])):
                blockers.append("remote_feature_no_longer_prunable")
            quorum_result = ensure_autonomous_quorum(
                repo_root,
                config,
                candidate_id=candidate["candidateId"],
                action_id=candidate["actionId"],
                evidence_hash=candidate["evidenceHash"],
                pinned_refs=candidate["pinnedRefs"],
                evidence=candidate["evidence"],
                action_class=candidate["actionClass"],
                blockers=blockers,
            )
            quorum_results.append({"candidate": candidate, **quorum_result})
            if not quorum_result["quorum"]["ok"]:
                write_audit(repo_root, config, "review_quorum_blocked", {"candidate": candidate, **quorum_result}, outcome="blocked")
                continue
            action = apply_remote_feature_prune(repo_root, config, plan, item)
            actions.append(action)
    branch_by_name = {item["branch"]: item for item in plan["branchPlans"]}
    for report, candidate in zip(promoted_reports, promoted_candidates):
        if candidate_id and candidate["candidateId"] != candidate_id:
            continue
        matched_candidate = True
        if report["recommendedAction"] == "preserve_detached_dirty_now":
            current_paths = worktree_dirty_state(Path(str((report.get("worktree") or {}).get("path") or ""))).get("paths", [])
            blockers = list(report.get("blockers") or [])
            if sorted(current_paths) != sorted(report.get("scope", {}).get("dirtyPaths") or []):
                blockers.append("detached_dirty_tuple_drifted")
            quorum_result = ensure_autonomous_quorum(
                repo_root,
                config,
                candidate_id=candidate["candidateId"],
                action_id=candidate["actionId"],
                evidence_hash=candidate["evidenceHash"],
                pinned_refs=candidate["pinnedRefs"],
                evidence=candidate["evidence"],
                action_class=candidate["actionClass"],
                blockers=blockers,
            )
            quorum_results.append({"candidate": candidate, "report": report, **quorum_result})
            if not quorum_result["quorum"]["ok"]:
                write_audit(repo_root, config, "review_quorum_blocked", {"candidate": candidate, "report": report, **quorum_result}, outcome="blocked")
                continue
            action = apply_detached_dirty_preserve(repo_root, config, plan, report)
            actions.append(action)
            continue
        if report["recommendedAction"] in {"clean_integrate_remote_now", "prune_remote_now"}:
            blockers = list(report.get("blockers") or [])
            current_target = target_ref_for(repo_root, config)
            current_remote_head = rev_parse(repo_root, str(report.get("remoteRef") or ""), required=False)
            if current_remote_head != report["head"]:
                blockers.append("remote_feature_head_drifted")
            if current_target["head"] != plan["pinnedRefs"]["target"]["head"]:
                blockers.append("target_head_drifted")
            quorum_result = ensure_autonomous_quorum(
                repo_root,
                config,
                candidate_id=candidate["candidateId"],
                action_id=candidate["actionId"],
                evidence_hash=candidate["evidenceHash"],
                pinned_refs=candidate["pinnedRefs"],
                evidence=candidate["evidence"],
                action_class=candidate["actionClass"],
                blockers=blockers,
            )
            quorum_results.append({"candidate": candidate, "report": report, **quorum_result})
            if not quorum_result["quorum"]["ok"]:
                write_audit(repo_root, config, "review_quorum_blocked", {"candidate": candidate, "report": report, **quorum_result}, outcome="blocked")
                continue
            if report["recommendedAction"] == "clean_integrate_remote_now":
                action = apply_remote_feature_clean_integrate(repo_root, config, plan, report)
            else:
                remote_item = {
                    "remote": report.get("remote"),
                    "branch": report.get("branch"),
                    "ref": report.get("remoteRef"),
                    "head": report.get("head"),
                    "disposition": "prune_patch_equivalent_remote_feature",
                }
                action = apply_remote_feature_prune(repo_root, config, plan, remote_item)
            actions.append(action)
            continue
        item = branch_by_name.get(report["branch"])
        if not item:
            continue
        current_head = rev_parse(repo_root, f"refs/heads/{report['branch']}", required=False)
        blockers = list(report.get("blockers") or [])
        if current_head != report["head"]:
            blockers.append("branch_head_drifted")
        current_target = target_ref_for(repo_root, config)
        if current_target["head"] != plan["pinnedRefs"]["target"]["head"]:
            blockers.append("target_head_drifted")
        quorum_result = ensure_autonomous_quorum(
            repo_root,
            config,
            candidate_id=candidate["candidateId"],
            action_id=candidate["actionId"],
            evidence_hash=candidate["evidenceHash"],
            pinned_refs=candidate["pinnedRefs"],
            evidence=candidate["evidence"],
            action_class=candidate["actionClass"],
            blockers=blockers,
        )
        quorum_results.append({"candidate": candidate, "report": report, **quorum_result})
        if not quorum_result["quorum"]["ok"]:
            write_audit(repo_root, config, "review_quorum_blocked", {"candidate": candidate, "report": report, **quorum_result}, outcome="blocked")
            continue
        if report["recommendedAction"] == "clean_integrate_now":
            action = apply_repo_sweep_clean_integrate(repo_root, config, plan, item)
            actions.append(action)
            write_audit(repo_root, config, "success" if action.get("status") == "success" else "blocked_repair", {"candidate": candidate, "report": report, "action": action}, outcome="success" if action.get("status") == "success" else "blocked")
        elif report["recommendedAction"] == "switch_target_and_prune":
            action = cleanup_foreign_dirty_integrated_branch(repo_root, config, plan, item, report)
            actions.append(action)
        elif report["recommendedAction"] == "split_now":
            action = preserve_owned_dirty_split(repo_root, work_block_id=report.get("dirtyClassification", {}).get("workBlockId"))
            actions.append(action)
            write_audit(repo_root, config, "dirty_split_success" if action.get("status") == "success" else "dirty_split_blocked_preservation", {"candidate": candidate, "report": report, "action": action}, outcome="success" if action.get("status") == "success" else "blocked")
        elif report["recommendedAction"] in {"prune_now", "cleanup_worktree_and_prune"}:
            actions.extend(
                cleanup_branch_after_sweep_action(
                    repo_root,
                    config,
                    plan,
                    item,
                    force_branch_delete=report.get("actionClass") in {"redundant_backup_prune", "redundant_branch_prune"},
                    allow_protected_worktree=report.get("actionClass") == "explicit_protected_worktree_cleanup",
                )
            )
    remove_clean_detached = (
        bool(config.get("cleanupPolicy", {}).get("removeCleanDetachedWorktreesInSweep", False))
        or str(config.get("repoSweep", {}).get("pruneWorktrees") or "") == "clean_detached_only"
    )
    if remove_clean_detached:
        if candidate_id:
            candidate_worktrees = []
        for item in candidate_worktrees:
            action = {"action": "remove_clean_detached_worktree", **remove_worktree(repo_root, Path(str(item["path"])))}
            actions.append(action)
            write_audit(repo_root, config, "snapshot_pruning", action, outcome="success" if action["returncode"] == 0 else "blocked")
    if bool(config.get("cleanupPolicy", {}).get("dropStashesInSweep", False)):
        if candidate_id:
            candidate_stashes = []
        for item in candidate_stashes:
            drop = run_git(repo_root, ["stash", "drop", item["ref"]])
            action = {"action": "drop_stash", "stash": item["ref"], "returncode": drop.returncode, "stderr": drop.stderr[-2000:]}
            actions.append(action)
            write_audit(repo_root, config, "snapshot_pruning", action, outcome="success" if drop.returncode == 0 else "blocked")
    if candidate_id and not matched_candidate:
        result = {
            "status": "blocked",
            "reason": "candidate_not_found_or_not_promoted",
            "candidateId": candidate_id,
            "branchCandidates": branch_candidates,
            "remoteFeatureCandidates": remote_feature_candidates,
            "promotedCandidates": promoted_candidates,
        }
        write_audit(repo_root, config, "blocked_repair", result, outcome="blocked")
        return result
    result = {
        "status": "success",
        "candidateId": candidate_id,
        "plan": plan,
        "tuple": tuple_info,
        "branchCandidates": branch_candidates,
        "remoteFeatureCandidates": remote_feature_candidates,
        "retainedCandidateReports": retained_reports,
        "promotedCandidates": promoted_candidates,
        "followUpCandidates": follow_up_candidates,
        "quorumResults": quorum_results,
        "actions": actions,
    }
    write_audit(repo_root, config, "success", result, outcome="success")
    return result


def ordered_retained_remediation_candidates(planned: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    candidates.extend(planned.get("branchCandidates") or [])
    candidates.extend(planned.get("remoteFeatureCandidates") or [])
    candidates.extend(planned.get("promotedCandidates") or [])
    return [item for item in candidates if item.get("candidateId")]


def remediate_retained_candidates(repo_root_arg: Path, *, apply: bool = False, candidate_id: Optional[str] = None) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    if not apply:
        planned = repo_sweep(repo_root, apply=False, candidate_id=candidate_id)
        result = {"actor": "retained-remediation", **planned}
        write_audit(repo_root, config, "retained_remediation", {"apply": False, "candidateId": candidate_id, "resultStatus": planned.get("status")}, outcome="recorded")
        return result
    if candidate_id:
        applied = repo_sweep(repo_root, apply=True, candidate_id=candidate_id)
        result = {"actor": "retained-remediation", "selectedCandidateId": candidate_id, **applied}
        write_audit(repo_root, config, "retained_remediation", {"apply": True, "candidateId": candidate_id, "resultStatus": applied.get("status")}, outcome="success" if applied.get("status") == "success" else "blocked")
        return result
    planned = repo_sweep(repo_root, apply=False)
    candidates = ordered_retained_remediation_candidates(planned)
    if not candidates:
        result = {**planned, "actor": "retained-remediation", "status": "success", "reason": "no_promoted_candidates"}
        write_audit(repo_root, config, "retained_remediation", {"apply": True, "candidateId": None, "resultStatus": "no_promoted_candidates"}, outcome="success")
        return result
    selected = str(candidates[0]["candidateId"])
    applied = repo_sweep(repo_root, apply=True, candidate_id=selected)
    result = {"actor": "retained-remediation", "selectedCandidateId": selected, "plannedCandidateCount": len(candidates), **applied}
    write_audit(repo_root, config, "retained_remediation", {"apply": True, "candidateId": selected, "candidateCount": len(candidates), "resultStatus": applied.get("status")}, outcome="success" if applied.get("status") == "success" else "blocked")
    return result


def audit_summary(repo_root_arg: Path, *, limit: int = 20) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    rows: List[Dict[str, Any]] = []
    path = audit_root(repo_root, config) / "audits.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return {"status": "success", "auditCount": len(rows), "audits": rows[-limit:]}


def broker_contract(repo_root_arg: Path) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo_root_arg)
    config = load_closeout_config(repo_root)
    scripts_dir = repo_root / "tools" / "closeout"
    scripts = sorted(path.name for path in scripts_dir.glob("*.ps1")) if scripts_dir.exists() else []
    required_config_keys = ["git", "validation", "paths", "dirtySplit", "toolingBaseline", "evidenceRepair", "stashPolicy", "cleanupPolicy", "reviewQuorum", "responseHookLifecycle", "blockerAutoRemediation"]
    return {
        "schemaVersion": BROKER_SCHEMA_VERSION,
        "configPath": str(repo_root / CONFIG_PATH),
        "policyHash": config.get("policyHash"),
        "requiredConfigKeys": required_config_keys,
        "missingConfigKeys": [key for key in required_config_keys if key not in config],
        "requiredScripts": REQUIRED_SCRIPT_NAMES,
        "scripts": scripts,
        "missingScripts": [name for name in REQUIRED_SCRIPT_NAMES if name not in scripts],
        "highImpactActions": HIGH_IMPACT_ACTIONS,
    }
