from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.repo_hygiene.brokered_closeout import simulate_clean_integration
from tools.repo_hygiene.candidate_acceptance import (
    acceptance_root,
    candidate_identity,
    evaluate,
    latest_summary,
    surface_record,
    validate_for_finalize,
    write_ledger,
)
from tools.repo_hygiene.core import HygieneError


TARGET = "1" * 40
FEATURE = "2" * 40
REQUIRED = ["content-self", "content-stranger-1", "content-stranger-2", "hosted-tests", "hosted-codeql"]
ROOT = Path(__file__).resolve().parents[2]


class CandidateAcceptanceTests(unittest.TestCase):
    def candidate(self, *, clean: bool = True) -> tuple[dict, dict]:
        rehearsal = {
            "clean": clean,
            "reason": "clean_merge_and_validation_passed" if clean else "diff_check_failed",
            "integrationHead": "3" * 40,
            "integrationTree": "4" * 40,
            "diffSha256": "5" * 64,
            "changedPaths": ["src/a.c"],
            "validationStatus": "passed" if clean else "diff_check_failed",
            "validations": [],
        }
        candidate = candidate_identity(
            target_head=TARGET,
            feature_head=FEATURE,
            policy_hash="policy-hash",
            plan_hash="plan-hash",
            rehearsal=rehearsal,
        )
        return candidate, rehearsal

    def records(self, candidate: dict, verdicts: dict[str, str] | None = None) -> list[dict]:
        verdicts = verdicts or {}
        return [
            surface_record(
                candidate,
                surface=surface,
                verdict=verdicts.get(surface, "APPROVE"),
                reviewer=f"reviewer-{index}",
                session_id=f"session-{index}",
                findings=(
                    [{"id": f"finding-{surface}", "invariant": "exact tuple remains valid", "falsifier": "mutate tuple"}]
                    if verdicts.get(surface) == "CHANGES_REQUESTED"
                    else []
                ),
                created_at=f"2026-08-20T00:00:0{index}Z",
            )
            for index, surface in enumerate(REQUIRED)
        ]

    def test_all_terminal_approvals_and_clean_rehearsal_are_ready(self) -> None:
        candidate, rehearsal = self.candidate()
        result = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=self.records(candidate))
        self.assertEqual("ready", result["state"])
        self.assertEqual([], result["missingSurfaces"])
        self.assertEqual([], result["blockers"])
        self.assertNotIn("fixBatch", result)
        self.assertFalse(result["authorityBoundaries"]["agentApprovalsGrantHumanAuthority"])

    def test_blocker_waits_for_all_surfaces_then_emits_one_fix_batch(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate, {"content-stranger-1": "CHANGES_REQUESTED"})
        collecting = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=records[:-1])
        self.assertEqual("collecting", collecting["state"])
        self.assertNotIn("fixBatch", collecting)
        blocked = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=records)
        self.assertEqual("changes_required", blocked["state"])
        self.assertEqual("acceptance-fix-batch.v1", blocked["fixBatch"]["schema"])
        self.assertEqual(["finding-content-stranger-1"], [item["id"] for item in blocked["fixBatch"]["findings"]])

    def test_tuple_drift_never_carries_approval(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        records[0]["featureHead"] = "9" * 40
        clean = dict(records[0])
        clean.pop("recordHash")
        records[0]["recordHash"] = __import__("hashlib").sha256(
            json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        result = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=records)
        self.assertEqual("collecting", result["state"])
        self.assertIn("content-self", result["missingSurfaces"])
        self.assertEqual("candidate_tuple_mismatch", result["staleSurfaceRecords"][0]["reason"])

    def test_ephemeral_merge_commit_drift_does_not_invalidate_identical_content_tuple(self) -> None:
        candidate, rehearsal = self.candidate()
        repeated = dict(rehearsal)
        repeated["integrationHead"] = "8" * 40
        same_content = candidate_identity(
            target_head=TARGET,
            feature_head=FEATURE,
            policy_hash="policy-hash",
            plan_hash="plan-hash",
            rehearsal=repeated,
        )
        self.assertNotEqual(candidate["integrationHead"], same_content["integrationHead"])
        self.assertEqual(candidate["acceptanceTupleHash"], same_content["acceptanceTupleHash"])

    def test_duplicate_reviewer_session_is_blocking(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        replacement = surface_record(
            candidate,
            surface="content-stranger-1",
            verdict="APPROVE",
            reviewer=records[0]["reviewer"],
            session_id=records[0]["sessionId"],
            created_at="2026-08-20T00:01:00Z",
        )
        records[1] = replacement
        result = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=records)
        self.assertEqual("changes_required", result["state"])
        self.assertIn("duplicate-review-identity", [item["id"] for item in result["blockers"]])

    def test_agent_record_cannot_escalate_human_authority(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        records[0]["authority"]["mayReblessGolden"] = True
        clean = dict(records[0])
        clean.pop("recordHash")
        records[0]["recordHash"] = __import__("hashlib").sha256(
            json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        result = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=records)
        self.assertEqual("collecting", result["state"])
        self.assertEqual("human_authority_escalation", result["staleSurfaceRecords"][0]["reason"])

    def test_same_tuple_later_approval_cannot_mute_blocking_verdict(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        blocking = surface_record(
            candidate,
            surface="content-stranger-1",
            verdict="CHANGES_REQUESTED",
            reviewer="blocking-reviewer",
            session_id="blocking-session",
            findings=[{"id": "persistent-blocker", "invariant": "a fix requires a new tuple"}],
            created_at="2026-08-20T00:00:01Z",
        )
        later_approval = surface_record(
            candidate,
            surface="content-stranger-1",
            verdict="APPROVE",
            reviewer="later-reviewer",
            session_id="later-session",
            created_at="2026-08-20T00:10:00Z",
        )
        records.extend([blocking, later_approval])
        result = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=records)
        self.assertEqual("changes_required", result["state"])
        self.assertIn("persistent-blocker", [item["id"] for item in result["blockers"]])

    def test_rehearsal_failure_is_batched_after_all_surfaces_terminal(self) -> None:
        candidate, rehearsal = self.candidate(clean=False)
        result = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=self.records(candidate))
        self.assertEqual("changes_required", result["state"])
        self.assertEqual("integration-rehearsal-failed", result["fixBatch"]["findings"][0]["id"])

    def test_ledger_hash_is_stable_across_capture_time_and_history_is_immutable(self) -> None:
        candidate, rehearsal = self.candidate()
        with mock.patch("tools.repo_hygiene.candidate_acceptance.utc_now", side_effect=["2026-08-20T00:00:00Z", "2026-08-20T01:00:00Z"]):
            first = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=self.records(candidate))
            second = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=self.records(candidate))
        self.assertEqual(first["ledgerHash"], second["ledgerHash"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = {"candidateAcceptance": {"stateRoot": ".claude-state/closeout/acceptance"}}
            first_paths = write_ledger(root, config, first)
            second_paths = write_ledger(root, config, second)
            self.assertEqual(first_paths["history"], second_paths["history"])
            history = root / first_paths["history"]
            self.assertEqual("2026-08-20T00:00:00Z", json.loads(history.read_text(encoding="utf-8"))["capturedAt"])
            self.assertEqual("2026-08-20T01:00:00Z", json.loads((root / second_paths["latest"]).read_text(encoding="utf-8"))["capturedAt"])

    def test_state_root_escape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(HygieneError, "must stay under"):
                acceptance_root(Path(temp), {"candidateAcceptance": {"stateRoot": "outside"}})

    def test_latest_summary_is_read_only_and_hash_verified(self) -> None:
        candidate, rehearsal = self.candidate()
        ledger = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=self.records(candidate))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = {"candidateAcceptance": {"stateRoot": ".claude-state/closeout/acceptance"}}
            self.assertEqual("missing", latest_summary(root, config)["state"])
            paths = write_ledger(root, config, ledger)
            summary = latest_summary(root, config)
            self.assertTrue(summary["ready"])
            self.assertEqual(candidate["acceptanceTupleHash"], summary["acceptanceTupleHash"])
            latest = root / paths["latest"]
            payload = json.loads(latest.read_text(encoding="utf-8"))
            payload["state"] = "changes_required"
            latest.write_text(json.dumps(payload), encoding="utf-8")
            invalid = latest_summary(root, config)
            self.assertEqual("invalid", invalid["state"])
            self.assertEqual("ledger hash mismatch", invalid["error"])

    def test_finalize_requires_same_tuple_ready_before_content_review(self) -> None:
        candidate, rehearsal = self.candidate()
        ledger = evaluate(candidate=candidate, rehearsal=rehearsal, required=REQUIRED, records=self.records(candidate))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = {
                "policyHash": "policy-hash",
                "candidateAcceptance": {
                    "enabled": True,
                    "requireReadyForFinalize": True,
                    "stateRoot": ".claude-state/closeout/acceptance",
                },
            }
            detection = {"targetHead": TARGET, "featureHead": FEATURE}
            self.assertEqual("candidate_acceptance_not_ready", validate_for_finalize(root, config, detection)["reason"])
            write_ledger(root, config, ledger)
            self.assertIsNone(validate_for_finalize(root, config, detection))
            drifted = {"targetHead": TARGET, "featureHead": "9" * 40}
            guard = validate_for_finalize(root, config, drifted)
            self.assertEqual(FEATURE, guard["mismatches"]["featureHead"]["actual"])

        source = (ROOT / "tools/repo_hygiene/brokered_closeout.py").read_text(encoding="utf-8")
        finalize = source.split("def _finalize_work_block_once(", 1)[1].split("def finalize_work_block(", 1)[0]
        self.assertLess(
            finalize.index("validate_candidate_acceptance_for_finalize"),
            finalize.index("validate_content_review_approval_for_finalize"),
        )

    def test_tracked_policy_enables_finalize_barrier_without_human_authority(self) -> None:
        config = json.loads((ROOT / "closeout.config.json").read_text(encoding="utf-8"))
        policy = config["candidateAcceptance"]
        self.assertTrue(policy["requireReadyForFinalize"])
        self.assertTrue(policy["batchUntilAllSurfacesTerminal"])
        self.assertFalse(policy["carryApprovalsAcrossCandidateTuples"])
        self.assertFalse(policy["agentApprovalsGrantHumanAuthority"])

    def test_github_capture_is_exact_head_terminal_and_fail_closed(self) -> None:
        source = (ROOT / "tools/factory/capture-github-acceptance.ps1").read_text(encoding="utf-8")
        for required in (
            "Ledger feature head does not match HeadSha",
            "Check-run response exceeds the fail-closed single-page bound",
            "Check-run response contains a different head SHA",
            "function Get-ExpectedCheckAppId",
            "if ($Name -ceq 'CodeQL') { return [long]57789 }",
            "return [long]15368",
            "[long]$_.app.id -ne $expectedAppId",
            "[string]$_.status -cne 'completed'",
            "[string]$_.conclusion -cne 'success'",
            "[int]$_.output.annotations_count -ne 0",
            "Repo Hygiene Python (windows-latest)",
            "Windows Product Oracles",
            "Protected Check Route",
            "Analyze (actions)",
            "Analyze (c-cpp)",
            "Analyze (python)",
            "'CodeQL'",
        ):
            self.assertIn(required, source)
        self.assertNotIn("SilentlyContinue", source)
        self.assertNotIn("$env:GH_TOKEN", source)

        command = (
            "$errors=$null; $tokens=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "'tools/factory/capture-github-acceptance.ps1',[ref]$tokens,[ref]$errors) | Out-Null; "
            "if($errors.Count){$errors | ForEach-Object {$_.Message}; exit 1}"
        )
        subprocess.run(
            ["pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=ROOT,
            check=True,
        )

    def test_range_diff_check_catches_whitespace_already_committed_in_feature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            self.git(repo, "init")
            self.git(repo, "config", "user.email", "candidate-acceptance@example.invalid")
            self.git(repo, "config", "user.name", "Candidate Acceptance Test")
            (repo / "file.txt").write_text("base\n", encoding="utf-8")
            self.git(repo, "add", "file.txt")
            self.git(repo, "commit", "-m", "base")
            target = self.git(repo, "rev-parse", "HEAD").strip()
            self.git(repo, "checkout", "-b", "feature")
            (repo / "file.txt").write_text("base\ntrailing whitespace   \n", encoding="utf-8")
            self.git(repo, "add", "file.txt")
            self.git(repo, "commit", "-m", "feature")
            feature = self.git(repo, "rev-parse", "HEAD").strip()
            self.git(repo, "checkout", "--detach", target)
            config = {"stateRoot": ".claude-state/closeout", "validation": {"commands": []}}
            result = simulate_clean_integration(
                repo,
                config,
                target_head=target,
                branch_head=feature,
                branch_name="feature",
                target_branch="master",
            )
            self.assertFalse(result["clean"])
            self.assertEqual("diff_check_failed", result["reason"])
            self.assertIn("file.txt", result["stdout"])

    @staticmethod
    def git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=repo, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
