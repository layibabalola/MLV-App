from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.repo_hygiene.brokered_closeout import simulate_clean_integration
from tools.repo_hygiene.candidate_acceptance import (
    _hash,
    _live_github_provider_payload,
    acceptance_root,
    CONTENT_REVIEWERS,
    candidate_identity,
    evaluate,
    final_integration_mismatches,
    latest_summary,
    provider_surface_record,
    surface_record,
    validation_plan,
    validate_for_finalize,
    verify_live_provider,
    write_ledger,
)
from tools.repo_hygiene.core import HygieneError


TARGET = "1" * 40
FEATURE = "2" * 40
REQUIRED = ["content-self", "content-stranger-1", "content-stranger-2", "hosted-tests", "hosted-codeql"]
ROOT = Path(__file__).resolve().parents[2]


class CandidateAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider_temp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.provider_temp.name).resolve()
        self.config = {
            "policyHash": "policy-hash",
            "toolingBaseline": {"enabled": True},
            "candidateAcceptance": {
                "enabled": True,
                "requireReadyForFinalize": True,
                "providerRepository": "layibabalola/MLV-App",
                "stateRoot": ".claude-state/closeout/acceptance",
                "requiredSurfaces": REQUIRED,
            },
            "validation": {"commands": []},
        }
        provider_dir = self.repo_root / ".claude-state/closeout/acceptance/provider" / FEATURE
        provider_dir.mkdir(parents=True)
        names = [
            ("Repo Hygiene Python (windows-latest)", 15368),
            ("Repo Hygiene Python (ubuntu-latest)", 15368),
            ("Factory Bridge Regressions", 15368),
            ("Windows GUI Pilot", 15368),
            ("Windows Product Oracles", 15368),
            ("Protected Check Route", 15368),
            ("Analyze (actions)", 15368),
            ("Analyze (c-cpp)", 15368),
            ("Analyze (python)", 15368),
            ("CodeQL", 57789),
        ]
        runs = [
            {
                "name": name,
                "id": index,
                "head_sha": FEATURE,
                "started_at": f"2026-08-20T00:00:{index:02d}Z",
                "status": "completed",
                "conclusion": "success",
                "app": {"id": app_id},
                "output": {"annotations_count": 0},
                "details_url": f"https://example.invalid/check/{index}",
            }
            for index, (name, app_id) in enumerate(names, 1)
        ]
        self.write_provider(
            {
                "schema": "candidate-acceptance-github-checks.v1",
                "capturedAt": "2026-08-20T00:01:00Z",
                "repository": "layibabalola/MLV-App",
                "headSha": FEATURE,
                "response": {"total_count": len(runs), "check_runs": runs},
            }
        )

    def tearDown(self) -> None:
        self.provider_temp.cleanup()

    def write_provider(self, payload: dict) -> Path:
        provider_dir = self.repo_root / ".claude-state/closeout/acceptance/provider" / FEATURE
        provider_dir.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        path = provider_dir / f"check-runs-{digest}.json"
        path.write_bytes(encoded)
        self.provider_path = path
        return path

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
            plan_hash=_hash(validation_plan(self.config)),
            rehearsal=rehearsal,
        )
        return candidate, rehearsal

    def records(self, candidate: dict, verdicts: dict[str, str] | None = None) -> list[dict]:
        verdicts = verdicts or {}
        records = []
        for index, surface in enumerate(REQUIRED):
            if surface in {"hosted-tests", "hosted-codeql"}:
                records.append(
                    provider_surface_record(
                        self.repo_root,
                        self.config,
                        candidate,
                        surface=surface,
                        evidence_path=self.provider_path,
                    )
                )
                continue
            records.append(
                surface_record(
                    candidate,
                    surface=surface,
                    verdict=verdicts.get(surface, "APPROVE"),
                    reviewer=CONTENT_REVIEWERS[surface],
                    session_id=f"session-{index}",
                findings=(
                    [{"id": f"finding-{surface}", "invariant": "exact tuple remains valid", "falsifier": "mutate tuple"}]
                    if verdicts.get(surface) == "CHANGES_REQUESTED"
                    else []
                ),
                created_at=f"2026-08-20T00:00:0{index}Z",
                )
            )
        return records

    def run_evaluate(self, *, candidate: dict, rehearsal: dict, records: list[dict], required: list[str] = REQUIRED) -> dict:
        return evaluate(
            candidate=candidate,
            rehearsal=rehearsal,
            required=required,
            records=records,
            repo_root=self.repo_root,
            config=self.config,
        )

    def test_all_terminal_approvals_and_clean_rehearsal_are_ready(self) -> None:
        candidate, rehearsal = self.candidate()
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=self.records(candidate))
        self.assertEqual("ready", result["state"])
        self.assertEqual([], result["missingSurfaces"])
        self.assertEqual([], result["blockers"])
        self.assertNotIn("fixBatch", result)
        self.assertFalse(result["authorityBoundaries"]["agentApprovalsGrantHumanAuthority"])

    def test_blocker_waits_for_all_surfaces_then_emits_one_fix_batch(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate, {"content-stranger-1": "CHANGES_REQUESTED"})
        collecting = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records[:-1])
        self.assertEqual("collecting", collecting["state"])
        self.assertNotIn("fixBatch", collecting)
        blocked = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
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
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
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
            plan_hash=_hash(validation_plan(self.config)),
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
            reviewer=CONTENT_REVIEWERS["content-stranger-1"],
            session_id=records[0]["sessionId"],
            created_at="2026-08-20T00:01:00Z",
        )
        records[1] = replacement
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
        self.assertEqual("changes_required", result["state"])
        self.assertIn("duplicate-review-session", [item["id"] for item in result["blockers"]])

    def test_agent_record_cannot_escalate_human_authority(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        records[0]["authority"]["mayReblessGolden"] = True
        clean = dict(records[0])
        clean.pop("recordHash")
        records[0]["recordHash"] = __import__("hashlib").sha256(
            json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
        self.assertEqual("collecting", result["state"])
        self.assertEqual("human_authority_escalation", result["staleSurfaceRecords"][0]["reason"])

    def test_same_tuple_later_approval_cannot_mute_blocking_verdict(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        blocking = surface_record(
            candidate,
            surface="content-stranger-1",
            verdict="CHANGES_REQUESTED",
            reviewer=CONTENT_REVIEWERS["content-stranger-1"],
            session_id="blocking-session",
            findings=[{"id": "persistent-blocker", "invariant": "a fix requires a new tuple"}],
            created_at="2026-08-20T00:00:01Z",
        )
        later_approval = surface_record(
            candidate,
            surface="content-stranger-1",
            verdict="APPROVE",
            reviewer=CONTENT_REVIEWERS["content-stranger-1"],
            session_id="later-session",
            created_at="2026-08-20T00:10:00Z",
        )
        records.extend([blocking, later_approval])
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
        self.assertEqual("changes_required", result["state"])
        self.assertIn("persistent-blocker", [item["id"] for item in result["blockers"]])

    def test_content_reviewers_must_be_distinct_even_with_different_sessions(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        records[1] = surface_record(
            candidate,
            surface="content-stranger-1",
            verdict="APPROVE",
            reviewer=records[0]["reviewer"],
            session_id="different-session",
        )
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
        self.assertEqual("collecting", result["state"])
        self.assertEqual("content_reviewer_identity_mismatch", result["staleSurfaceRecords"][0]["reason"])

    def test_all_same_surface_blocking_findings_survive_consolidation(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        for finding_id, created_at in (("blocker-a", "2026-08-20T00:10:00Z"), ("blocker-b", "2026-08-20T00:11:00Z")):
            records.append(
                surface_record(
                    candidate,
                    surface="content-stranger-1",
                    verdict="CHANGES_REQUESTED",
                    reviewer=CONTENT_REVIEWERS["content-stranger-1"],
                    session_id=f"session-{finding_id}",
                    findings=[{"id": finding_id}],
                    created_at=created_at,
                )
            )
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
        self.assertEqual("changes_required", result["state"])
        ids = [item["id"] for item in result["fixBatch"]["findings"]]
        self.assertIn("blocker-a", ids)
        self.assertIn("blocker-b", ids)

    def test_configured_quorum_cannot_be_replaced_by_one_surface_ledger(self) -> None:
        candidate, rehearsal = self.candidate()
        one_surface = evaluate(
            candidate=candidate,
            rehearsal=rehearsal,
            required=["content-self"],
            records=[self.records(candidate)[0]],
            repo_root=self.repo_root,
            config=None,
        )
        self.assertEqual("ready", one_surface["state"])
        with self.assertRaisesRegex(HygieneError, "required surface policy mismatch"):
            write_ledger(self.repo_root, self.config, one_surface)

    def test_rehashed_ready_state_with_blockers_is_incoherent_and_finalize_blocks(self) -> None:
        candidate, rehearsal = self.candidate()
        blocked = self.run_evaluate(
            candidate=candidate,
            rehearsal=rehearsal,
            records=self.records(candidate, {"content-stranger-1": "CHANGES_REQUESTED"}),
        )
        forged = json.loads(json.dumps(blocked))
        forged["state"] = "ready"
        basis = {key: value for key, value in forged.items() if key not in {"capturedAt", "ledgerHash", "outputPaths"}}
        forged["ledgerHash"] = _hash(basis)
        root = acceptance_root(self.repo_root, self.config)
        history = root / "history" / f"{candidate['acceptanceTupleHash']}-{forged['ledgerHash']}.json"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(json.dumps(forged), encoding="utf-8")
        (root / "latest.json").write_text(json.dumps(forged), encoding="utf-8")
        summary = latest_summary(self.repo_root, self.config)
        self.assertEqual("invalid", summary["state"])
        self.assertIn("coherence mismatch", summary["error"])
        guard = validate_for_finalize(
            self.repo_root,
            self.config,
            {"targetHead": TARGET, "featureHead": FEATURE, "targetBranch": "master", "branch": "feature"},
        )
        self.assertEqual("candidate_acceptance_not_ready", guard["reason"])

    def test_generic_records_cannot_impersonate_hosted_surfaces(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        records[3] = surface_record(
            candidate,
            surface="hosted-tests",
            verdict="APPROVE",
            reviewer="forged-provider",
            session_id="forged-provider-session",
        )
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
        self.assertEqual("collecting", result["state"])
        self.assertIn("hosted-tests", result["missingSurfaces"])
        self.assertIn("schema_mismatch", [item["reason"] for item in result["staleSurfaceRecords"]])

    def test_provider_repository_and_annotation_shape_are_fail_closed(self) -> None:
        candidate, _ = self.candidate()
        original = json.loads(self.provider_path.read_text(encoding="utf-8"))
        wrong_repo = json.loads(json.dumps(original))
        wrong_repo["repository"] = "someone-else/MLV-App"
        self.write_provider(wrong_repo)
        with self.assertRaisesRegex(HygieneError, "repository"):
            provider_surface_record(
                self.repo_root,
                self.config,
                candidate,
                surface="hosted-tests",
                evidence_path=self.provider_path,
            )
        missing_annotations = json.loads(json.dumps(original))
        codeql = next(run for run in missing_annotations["response"]["check_runs"] if run["name"] == "CodeQL")
        codeql["output"].pop("annotations_count")
        self.write_provider(missing_annotations)
        with self.assertRaisesRegex(HygieneError, "annotation count"):
            provider_surface_record(
                self.repo_root,
                self.config,
                candidate,
                surface="hosted-codeql",
                evidence_path=self.provider_path,
            )

    def test_provider_record_rejects_mutable_non_addressed_evidence_path(self) -> None:
        candidate, _ = self.candidate()
        mutable = self.provider_path.with_name("check-runs.json")
        mutable.write_bytes(self.provider_path.read_bytes())
        with self.assertRaisesRegex(HygieneError, "SHA-addressed"):
            provider_surface_record(
                self.repo_root,
                self.config,
                candidate,
                surface="hosted-tests",
                evidence_path=mutable,
            )

    def test_hosted_verdict_cannot_be_rehashed_against_failing_provider_evidence(self) -> None:
        candidate, rehearsal = self.candidate()
        payload = json.loads(self.provider_path.read_text(encoding="utf-8"))
        codeql = next(run for run in payload["response"]["check_runs"] if run["name"] == "CodeQL")
        codeql["conclusion"] = "failure"
        self.write_provider(payload)
        record = provider_surface_record(
            self.repo_root,
            self.config,
            candidate,
            surface="hosted-codeql",
            evidence_path=self.provider_path,
        )
        self.assertEqual("CHANGES_REQUESTED", record["verdict"])
        record["verdict"] = "APPROVE"
        record["recordHash"] = _hash({key: value for key, value in record.items() if key != "recordHash"})
        ledger = evaluate(
            candidate=candidate,
            rehearsal=rehearsal,
            required=["hosted-codeql"],
            records=[record],
            repo_root=self.repo_root,
            config=self.config,
        )
        self.assertEqual("collecting", ledger["state"])
        self.assertEqual("provider_verdict_mismatch", ledger["staleSurfaceRecords"][0]["reason"])

    def test_nonterminal_or_fabricated_provider_state_cannot_finalize(self) -> None:
        candidate, rehearsal = self.candidate()
        ledger = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=self.records(candidate))
        live = json.loads(self.provider_path.read_text(encoding="utf-8"))
        product = next(run for run in live["response"]["check_runs"] if run["name"] == "Windows Product Oracles")
        product["status"] = "in_progress"
        product["conclusion"] = None
        with self.assertRaisesRegex(HygieneError, "not terminal"):
            verify_live_provider(self.repo_root, ledger, self.config, payload=live)

        live = json.loads(self.provider_path.read_text(encoding="utf-8"))
        codeql = next(run for run in live["response"]["check_runs"] if run["name"] == "CodeQL")
        codeql["id"] = 999999
        codeql["details_url"] = "https://example.invalid/forged"
        with self.assertRaisesRegex(HygieneError, "drifted"):
            verify_live_provider(self.repo_root, ledger, self.config, payload=live)

    def test_live_provider_query_is_github_pinned_and_bounded(self) -> None:
        provider = json.loads(self.provider_path.read_text(encoding="utf-8"))
        completed = {
            "returncode": 0,
            "timedOut": False,
            "outputCapped": False,
            "cpuStalled": False,
            "stdout": json.dumps(provider["response"]),
            "stderr": "",
        }
        with mock.patch.dict(os.environ, {"GH_HOST": "attacker.invalid"}), mock.patch(
            "tools.repo_hygiene.candidate_acceptance.resolve_repo_root",
            return_value=self.repo_root,
        ), mock.patch(
            "tools.repo_hygiene.brokered_closeout.run_bounded_closeout_process",
            return_value=completed,
        ) as bounded:
            payload = _live_github_provider_payload(
                self.repo_root,
                self.config,
                "layibabalola/MLV-App",
                FEATURE,
            )
        command = bounded.call_args.args[2]
        self.assertEqual(["--hostname", "github.com"], command[2:4])
        self.assertNotIn("GH_HOST", bounded.call_args.kwargs["env"])
        self.assertEqual(60000, bounded.call_args.kwargs["timeout_ms"])
        self.assertEqual("candidate-acceptance-github-checks.v1", payload["schema"])

    def test_mandatory_policy_cannot_be_disabled_when_tooling_baseline_is_enforced(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["toolingBaseline"]["enabled"] = False
        config["candidateAcceptance"]["requireReadyForFinalize"] = False
        guard = validate_for_finalize(self.repo_root, config, {"targetHead": TARGET, "featureHead": FEATURE})
        self.assertEqual("candidate_acceptance_policy_disabled", guard["reason"])

        config = json.loads(json.dumps(self.config))
        config["candidateAcceptance"]["requiredSurfaces"] = ["content-self"]
        guard = validate_for_finalize(self.repo_root, config, {"targetHead": TARGET, "featureHead": FEATURE})
        self.assertEqual("candidate_acceptance_policy_weakened", guard["reason"])

        config = json.loads(json.dumps(self.config))
        config["candidateAcceptance"]["providerRepository"] = "someone-else/MLV-App"
        guard = validate_for_finalize(self.repo_root, config, {"targetHead": TARGET, "featureHead": FEATURE})
        self.assertEqual("candidate_acceptance_policy_weakened", guard["reason"])

    def test_final_integration_tree_or_diff_drift_is_blocking(self) -> None:
        accepted = {"integrationTree": "a" * 40, "diffSha256": "b" * 64}
        self.assertEqual({}, final_integration_mismatches(accepted, dict(accepted)))
        mismatches = final_integration_mismatches(
            accepted,
            {"integrationTree": "c" * 40, "diffSha256": "d" * 64},
        )
        self.assertEqual({"integrationTree", "diffSha256"}, set(mismatches))

    def test_rehearsal_failure_is_batched_after_all_surfaces_terminal(self) -> None:
        candidate, rehearsal = self.candidate(clean=False)
        result = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=self.records(candidate))
        self.assertEqual("changes_required", result["state"])
        self.assertEqual("integration-rehearsal-failed", result["fixBatch"]["findings"][0]["id"])

    def test_ledger_hash_is_stable_across_capture_time_and_history_is_immutable(self) -> None:
        candidate, rehearsal = self.candidate()
        records = self.records(candidate)
        with mock.patch("tools.repo_hygiene.candidate_acceptance.utc_now", side_effect=["2026-08-20T00:00:00Z", "2026-08-20T01:00:00Z"]):
            first = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
            second = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=records)
        self.assertEqual(first["ledgerHash"], second["ledgerHash"])
        first_paths = write_ledger(self.repo_root, self.config, first)
        second_paths = write_ledger(self.repo_root, self.config, second)
        self.assertEqual(first_paths["history"], second_paths["history"])
        history = self.repo_root / first_paths["history"]
        self.assertEqual("2026-08-20T00:00:00Z", json.loads(history.read_text(encoding="utf-8"))["capturedAt"])
        self.assertEqual(
            "2026-08-20T01:00:00Z",
            json.loads((self.repo_root / second_paths["latest"]).read_text(encoding="utf-8"))["capturedAt"],
        )

    def test_provider_recapture_preserves_prior_content_addressed_ledger(self) -> None:
        candidate, rehearsal = self.candidate()
        first_provider = self.provider_path
        first = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=self.records(candidate))
        write_ledger(self.repo_root, self.config, first)
        recaptured = json.loads(first_provider.read_text(encoding="utf-8"))
        recaptured["capturedAt"] = "2026-08-20T02:00:00Z"
        second_provider = self.write_provider(recaptured)
        self.assertNotEqual(first_provider, second_provider)
        self.assertTrue(first_provider.exists())
        self.assertTrue(second_provider.exists())
        self.assertTrue(latest_summary(self.repo_root, self.config)["ready"])

    def test_monotonic_chain_detects_deleted_same_tuple_blocker_history(self) -> None:
        candidate, rehearsal = self.candidate()
        blocked = self.run_evaluate(
            candidate=candidate,
            rehearsal=rehearsal,
            records=self.records(candidate, {"content-stranger-1": "CHANGES_REQUESTED"}),
        )
        paths = write_ledger(self.repo_root, self.config, blocked)
        (self.repo_root / paths["history"]).unlink()
        summary = latest_summary(self.repo_root, self.config)
        self.assertEqual("invalid", summary["state"])
        self.assertIn("history is unreadable", summary["error"])

    def test_same_tuple_blocker_cannot_be_muted_by_direct_writer_or_rehashed_chain(self) -> None:
        candidate, rehearsal = self.candidate()
        blocked = self.run_evaluate(
            candidate=candidate,
            rehearsal=rehearsal,
            records=self.records(candidate, {"content-stranger-1": "CHANGES_REQUESTED"}),
        )
        ready = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=self.records(candidate))
        write_ledger(self.repo_root, self.config, blocked)
        with self.assertRaisesRegex(HygieneError, "same-tuple blocker is monotonic"):
            write_ledger(self.repo_root, self.config, ready)

        root = acceptance_root(self.repo_root, self.config)
        tuple_hash = candidate["acceptanceTupleHash"]
        ready_history = root / "history" / f"{tuple_hash}-{ready['ledgerHash']}.json"
        ready_history.write_text(json.dumps(ready), encoding="utf-8")
        chain_path = root / "ledger-chain.json"
        chain = json.loads(chain_path.read_text(encoding="utf-8"))
        chain["tuples"][tuple_hash].append(ready["ledgerHash"])
        chain.pop("chainHash")
        chain["chainHash"] = _hash(chain)
        chain_path.write_text(json.dumps(chain), encoding="utf-8")
        (root / "latest.json").write_text(json.dumps(ready), encoding="utf-8")
        summary = latest_summary(self.repo_root, self.config)
        self.assertEqual("invalid", summary["state"])
        self.assertIn("same-tuple blocker is monotonic", summary["error"])

    def test_state_root_escape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(HygieneError, "must stay under"):
                acceptance_root(Path(temp), {"candidateAcceptance": {"stateRoot": "outside"}})

    def test_latest_summary_is_read_only_and_hash_verified(self) -> None:
        candidate, rehearsal = self.candidate()
        ledger = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=self.records(candidate))
        self.assertEqual("missing", latest_summary(self.repo_root, self.config)["state"])
        paths = write_ledger(self.repo_root, self.config, ledger)
        summary = latest_summary(self.repo_root, self.config)
        self.assertTrue(summary["ready"])
        self.assertEqual(candidate["acceptanceTupleHash"], summary["acceptanceTupleHash"])
        latest = self.repo_root / paths["latest"]
        payload = json.loads(latest.read_text(encoding="utf-8"))
        payload["state"] = "changes_required"
        latest.write_text(json.dumps(payload), encoding="utf-8")
        invalid = latest_summary(self.repo_root, self.config)
        self.assertEqual("invalid", invalid["state"])
        self.assertEqual("ledger hash mismatch", invalid["error"])

    def test_finalize_requires_same_tuple_ready_before_content_review(self) -> None:
        candidate, rehearsal = self.candidate()
        ledger = self.run_evaluate(candidate=candidate, rehearsal=rehearsal, records=self.records(candidate))
        detection = {"targetHead": TARGET, "featureHead": FEATURE, "targetBranch": "master", "branch": "feature"}
        self.assertEqual(
            "candidate_acceptance_not_ready",
            validate_for_finalize(self.repo_root, self.config, detection)["reason"],
        )
        write_ledger(self.repo_root, self.config, ledger)
        with mock.patch("tools.repo_hygiene.candidate_acceptance._live_github_provider_payload", return_value=json.loads(self.provider_path.read_text(encoding="utf-8"))), mock.patch(
            "tools.repo_hygiene.brokered_closeout.simulate_clean_integration", return_value=rehearsal
        ):
            self.assertIsNone(validate_for_finalize(self.repo_root, self.config, detection))
            drifted = {**detection, "featureHead": "9" * 40}
            guard = validate_for_finalize(self.repo_root, self.config, drifted)
        self.assertEqual(FEATURE, guard["mismatches"]["featureHead"]["actual"])

        source = (ROOT / "tools/repo_hygiene/brokered_closeout.py").read_text(encoding="utf-8")
        finalize = source.split("def _finalize_work_block_once(", 1)[1].split("def finalize_work_block(", 1)[0]
        self.assertLess(
            finalize.index("repair = repair_eligibility"),
            finalize.index("validate_candidate_acceptance_for_finalize"),
        )
        self.assertLess(
            finalize.index("validate_candidate_acceptance_for_finalize"),
            finalize.index("validate_content_review_approval_for_finalize"),
        )
        self.assertIn("candidate_acceptance_final_tree_mismatch", finalize)
        self.assertIn("integration_range_evidence(integration_path, target[\"head\"], merged_head)", finalize)

    def test_tracked_policy_enables_finalize_barrier_without_human_authority(self) -> None:
        config = json.loads((ROOT / "closeout.config.json").read_text(encoding="utf-8"))
        policy = config["candidateAcceptance"]
        self.assertTrue(policy["requireReadyForFinalize"])
        self.assertEqual("layibabalola/MLV-App", policy["providerRepository"])
        self.assertTrue(policy["batchUntilAllSurfacesTerminal"])
        self.assertFalse(policy["carryApprovalsAcrossCandidateTuples"])
        self.assertFalse(policy["agentApprovalsGrantHumanAuthority"])

        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        invocation = "python -m unittest tools.repo_hygiene.test_candidate_acceptance -v"
        self.assertEqual(1, workflow.count(invocation))

    def test_github_capture_is_exact_head_terminal_and_fail_closed(self) -> None:
        source = (ROOT / "tools/factory/capture-github-acceptance.ps1").read_text(encoding="utf-8")
        for required in (
            "Ledger feature head does not match HeadSha",
            "Check-run response exceeds the fail-closed single-page bound",
            "Check-run response contains a different head SHA",
            "candidate-acceptance-github-checks.v1",
            "check-runs-$providerHash.json",
            "'record-hosted'",
            "[string]$_.status -cne 'completed'",
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

        python_source = (ROOT / "tools/repo_hygiene/candidate_acceptance.py").read_text(encoding="utf-8")
        self.assertIn('(\"CodeQL\", 57789, True)', python_source)
        self.assertIn('("Windows Product Oracles", 15368, False)', python_source)
        self.assertIn("providerRepository", python_source)
        self.assertIn('"annotations_count" not in output', python_source)
        self.assertIn("hosted surfaces require record-hosted", python_source)
        self.assertIn("def verify_live_provider", python_source)
        self.assertIn('"--hostname"', python_source)
        self.assertIn('"github.com"', python_source)
        self.assertIn("run_bounded_closeout_process", python_source)
        self.assertIn('process_env.pop("GH_HOST", None)', python_source)

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

    def test_diff_identity_is_independent_of_git_color_configuration(self) -> None:
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
            (repo / "file.txt").write_text("base\nfeature\n", encoding="utf-8")
            self.git(repo, "add", "file.txt")
            self.git(repo, "commit", "-m", "feature")
            feature = self.git(repo, "rev-parse", "HEAD").strip()
            self.git(repo, "checkout", "--detach", target)
            config = {"stateRoot": ".claude-state/closeout", "validation": {"commands": []}}
            normal = simulate_clean_integration(
                repo,
                config,
                target_head=target,
                branch_head=feature,
                branch_name="feature",
                target_branch="master",
            )
            self.git(repo, "config", "color.ui", "always")
            self.git(repo, "config", "diff.context", "10")
            self.git(repo, "config", "diff.indentHeuristic", "true")
            colored = simulate_clean_integration(
                repo,
                config,
                target_head=target,
                branch_head=feature,
                branch_name="feature",
                target_branch="master",
            )
            self.assertTrue(normal["clean"])
            self.assertTrue(colored["clean"])
            self.assertEqual(normal["diffSha256"], colored["diffSha256"])
            self.assertEqual(normal["integrationTree"], colored["integrationTree"])

    @staticmethod
    def git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=repo, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
