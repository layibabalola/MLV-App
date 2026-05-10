from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .brokered_closeout import (
    audit_summary,
    agent_remediation_queue_consumer_plan,
    bootstrap_response_broker_manifest,
    broker_contract,
    checkpoint_owned_work,
    collect_agent_remediation_results,
    complete_work_block,
    detect_work_block,
    finalize_action_id,
    finalize_candidate_id,
    finalize_evidence,
    finalize_work_block,
    guard_closeout_hook,
    load_closeout_config,
    preserve_owned_dirty_split,
    quarantine_orphans,
    record_review_approval,
    remediate_retained_candidates,
    remediation_freeze_status,
    remediation_packet_template,
    repair_eligibility,
    repo_sweep,
    repo_sweep_tuple,
    repo_state_snapshot,
    review_tuple_hash,
    start_work_block,
    write_review_surface_unavailable_report,
)
from .core import HygieneError, resolve_repo_root, stable_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Brokered repo-owned work block closeout.")
    parser.add_argument("--repo-root", default=".", help="Path inside the target Git repo.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Open a broker-owned work block.")
    start.add_argument("--work-block-id")
    start.add_argument("--actor", default="codex")
    start.add_argument("--claim", action="append", default=[], help="Repo-relative path claim. May be repeated.")

    bootstrap = sub.add_parser("bootstrap-response", help="Create or refresh the response-hook broker manifest.")
    bootstrap.add_argument("--hook-phase", default="response")
    bootstrap.add_argument("--actor", default="codex-response-hook")
    bootstrap.add_argument("--claim", action="append", default=[], help="Repo-relative path claim. May be repeated.")

    complete = sub.add_parser("complete", help="Mark a work block complete; optionally finalize.")
    complete.add_argument("--work-block-id")
    complete.add_argument("--finalize", action="store_true")
    complete.add_argument("--auto-approve", action="store_true")
    complete.add_argument("--require-repo-closed", action="store_true")

    detect = sub.add_parser("detect", help="Classify dirty state against the completed branch delta.")
    detect.add_argument("--work-block-id")

    repair = sub.add_parser("repair", help="Run safe closeout eligibility repair.")
    repair.add_argument("--work-block-id")

    checkpoint = sub.add_parser("checkpoint", help="Checkpoint owned dirty work only.")
    checkpoint.add_argument("--work-block-id")
    checkpoint.add_argument("--message", default="brokered closeout checkpoint")

    dirty_split = sub.add_parser("dirty-split", help="Preserve owned dirty split candidates on broker-owned branches.")
    dirty_split.add_argument("--work-block-id")

    finalize = sub.add_parser("finalize", help="Run pinned clean integration finalize.")
    finalize.add_argument("--work-block-id")
    finalize.add_argument("--expected-pinned-refs-file")

    review = sub.add_parser("review-quorum", help="Record or inspect exact-tuple review quorum.")
    review.add_argument("--work-block-id")
    review.add_argument("--candidate-id")
    review.add_argument("--action-id")
    review.add_argument("--evidence-hash")
    review.add_argument("--pinned-refs-file")
    review.add_argument("--reviewer", default="codex")
    review.add_argument("--approve", action="store_true")
    review.add_argument("--print-tuple", action="store_true")
    review.add_argument("--surface")
    review.add_argument("--mark-surface-unavailable", action="store_true")
    review.add_argument("--unavailable-reason", default="surface could not perform required review")
    review.add_argument("--recovery-command")

    quarantine = sub.add_parser("orphan-quarantine", help="Audit or quarantine orphaned work blocks.")
    quarantine.add_argument("--apply", action="store_true")

    audit = sub.add_parser("audit", help="Print recent durable closeout audits.")
    audit.add_argument("--limit", type=int, default=20)

    repo_state = sub.add_parser("repo-state", help="Print or persist a dashboard-ready repo state snapshot.")
    repo_state.add_argument("--write", action="store_true", help="Write latest/history snapshots and a repo_state_snapshot audit.")
    repo_state.add_argument("--work-block-id")

    sweep = sub.add_parser("sweep", help="Plan or apply whole-repo branch/worktree/stash cleanup.")
    sweep.add_argument("--apply", action="store_true")
    sweep.add_argument("--print-tuple", action="store_true")
    sweep.add_argument("--candidate-id")
    sweep.add_argument("--bulk-override-file")

    remediate = sub.add_parser("remediate-retained", help="Run the bounded retained-candidate remediation queue.")
    remediate.add_argument("--apply", action="store_true")
    remediate.add_argument("--candidate-id")

    agent_queue = sub.add_parser("agent-queue", help="Plan or mark unavailable Codex agent-remediation queue shards.")
    agent_queue.add_argument("--surface", default="codex-desktop")
    agent_queue.add_argument("--mark-unavailable", action="store_true")

    sub.add_parser("agent-results", help="Collect and validate agent-remediation result packets.")

    freeze = sub.add_parser("remediation-freeze-status", help="Inspect the closeout remediation freeze guard.")
    freeze.add_argument("--action", default="status")
    freeze.add_argument("--audit", action="store_true")

    hook_guard = sub.add_parser("hook-guard", help="Fail closed when a lifecycle hook runs during remediation freeze.")
    hook_guard.add_argument("--hook-name", required=True)

    packet = sub.add_parser("remediation-packet-template", help="Print the remediation packet template for the current worktree.")
    packet.add_argument("--reason", default="manual_remediation_packet")

    sub.add_parser("contract", help="Print broker/config/script parity information.")
    return parser


def current_finalize_tuple(repo_root: Path, work_block_id: str | None) -> dict:
    config = load_closeout_config(repo_root)
    detection = detect_work_block(repo_root, work_block_id=work_block_id)
    evidence = finalize_evidence(config, detection)
    evidence_hash = json_hash(evidence)
    candidate_id = finalize_candidate_id(detection["workBlockId"])
    action_id = finalize_action_id()
    tuple_hash = review_tuple_hash(candidate_id, action_id, evidence_hash, str(config.get("policyHash")), detection["pinnedRefs"])
    return {
        "candidateId": candidate_id,
        "actionId": action_id,
        "evidence": evidence,
        "evidenceHash": evidence_hash,
        "pinnedRefs": detection["pinnedRefs"],
        "policyHash": config.get("policyHash"),
        "tupleHash": tuple_hash,
    }


def json_hash(data: object) -> str:
    # Keep the CLI independent from the review implementation's private helper.
    import hashlib

    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        repo_root = resolve_repo_root(Path(args.repo_root))
        if args.command == "start":
            result = start_work_block(repo_root, work_block_id=args.work_block_id, actor=args.actor, path_claims=args.claim)
        elif args.command == "bootstrap-response":
            result = bootstrap_response_broker_manifest(repo_root, hook_phase=args.hook_phase, actor=args.actor, path_claims=args.claim)
        elif args.command == "complete":
            result = complete_work_block(
                repo_root,
                work_block_id=args.work_block_id,
                finalize=args.finalize,
                auto_approve=args.auto_approve,
                require_repo_closed=args.require_repo_closed,
            )
        elif args.command == "detect":
            result = detect_work_block(repo_root, work_block_id=args.work_block_id)
        elif args.command == "repair":
            result = repair_eligibility(repo_root, work_block_id=args.work_block_id)
        elif args.command == "checkpoint":
            result = checkpoint_owned_work(repo_root, work_block_id=args.work_block_id, message=args.message)
        elif args.command == "dirty-split":
            result = preserve_owned_dirty_split(repo_root, work_block_id=args.work_block_id)
        elif args.command == "finalize":
            expected = None
            if args.expected_pinned_refs_file:
                expected = json.loads(Path(args.expected_pinned_refs_file).read_text(encoding="utf-8"))
            result = finalize_work_block(repo_root, work_block_id=args.work_block_id, expected_pinned_refs=expected)
        elif args.command == "review-quorum":
            if args.print_tuple:
                result = current_finalize_tuple(repo_root, args.work_block_id)
            elif args.mark_surface_unavailable:
                pinned_refs = json.loads(Path(args.pinned_refs_file).read_text(encoding="utf-8"))
                result = write_review_surface_unavailable_report(
                    repo_root,
                    surface=args.surface,
                    candidate_id=args.candidate_id,
                    action_id=args.action_id,
                    evidence_hash=args.evidence_hash,
                    pinned_refs=pinned_refs,
                    unavailable_reason=args.unavailable_reason,
                    recovery_command=args.recovery_command,
                )
            else:
                pinned_refs = json.loads(Path(args.pinned_refs_file).read_text(encoding="utf-8"))
                result = record_review_approval(
                    repo_root,
                    candidate_id=args.candidate_id,
                    action_id=args.action_id,
                    evidence_hash=args.evidence_hash,
                    pinned_refs=pinned_refs,
                    reviewer=args.reviewer,
                    approved=args.approve,
                )
        elif args.command == "orphan-quarantine":
            result = quarantine_orphans(repo_root, apply=args.apply)
        elif args.command == "audit":
            result = audit_summary(repo_root, limit=args.limit)
        elif args.command == "repo-state":
            result = repo_state_snapshot(repo_root, write=args.write, work_block_id=args.work_block_id)
        elif args.command == "sweep":
            if args.print_tuple:
                result = repo_sweep_tuple(repo_root)
            else:
                bulk_override = None
                if args.bulk_override_file:
                    bulk_override = json.loads(Path(args.bulk_override_file).read_text(encoding="utf-8"))
                result = repo_sweep(repo_root, apply=args.apply, candidate_id=args.candidate_id, bulk_override=bulk_override)
        elif args.command == "remediate-retained":
            result = remediate_retained_candidates(repo_root, apply=args.apply, candidate_id=args.candidate_id)
        elif args.command == "agent-queue":
            result = agent_remediation_queue_consumer_plan(
                repo_root,
                surface=args.surface,
                mark_unavailable=args.mark_unavailable,
            )
        elif args.command == "agent-results":
            result = collect_agent_remediation_results(repo_root)
        elif args.command == "remediation-freeze-status":
            config = load_closeout_config(repo_root)
            result = remediation_freeze_status(repo_root, config, action=args.action, write_audit_packet=args.audit)
        elif args.command == "hook-guard":
            result = guard_closeout_hook(repo_root, hook_name=args.hook_name)
        elif args.command == "remediation-packet-template":
            config = load_closeout_config(repo_root)
            result = remediation_packet_template(repo_root, config, reason=args.reason)
        elif args.command == "contract":
            result = broker_contract(repo_root)
        else:
            return 4
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except HygieneError as exc:
        print("brokered closeout error: %s" % exc, file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
