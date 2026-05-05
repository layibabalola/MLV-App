from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .closeout import (
    PUBLISH_MODES,
    approve_transaction,
    evaluate_closeout_triggers,
    open_transaction,
    record_agent_review,
    record_codex_recommendation,
    transaction_status,
    validate_transaction_apply,
)
from .core import HygieneError, run_apply, run_scan, verify_policy


def secret_from_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HygieneError(f"required secret environment variable is not set: {name}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repo hygiene scanner and safe apply tool.")
    parser.add_argument("--repo-root", default=".", help="Path inside the target Git repo.")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Run report-first hygiene scan.")
    scan.add_argument("--no-write-artifacts", action="store_true")
    scan.add_argument("--trust-local-base", action="store_true")
    scan.add_argument("--json", action="store_true", help="Print JSON instead of summary.")

    apply = sub.add_parser("apply", help="Apply one symbolic action to one candidate ID.")
    apply.add_argument("--candidate-id", required=True)
    apply.add_argument("--action-id", required=True)
    apply.add_argument("--expected-evidence-hash")
    apply.add_argument("--manual-override", action="store_true")
    apply.add_argument("--trust-local-base", action="store_true")

    verify = sub.add_parser("verify-policy", help="Verify config/docs/tests expose implemented behavior.")
    verify.add_argument("--json", action="store_true")

    closeout = sub.add_parser("closeout", help="Codex-in-the-loop closeout transaction workflow.")
    closeout_sub = closeout.add_subparsers(dest="closeout_command", required=True)

    closeout_open = closeout_sub.add_parser("open", help="Open a closeout transaction decision packet.")
    closeout_open.add_argument("--base")
    closeout_open.add_argument(
        "--publish-mode",
        choices=PUBLISH_MODES,
        default="no_publish",
    )
    closeout_open.add_argument("--publish-remote")

    closeout_trigger = closeout_sub.add_parser("trigger", help="Evaluate auto-trigger signals for closeout.")
    closeout_trigger.add_argument("--open-if-triggered", action="store_true")
    closeout_trigger.add_argument(
        "--publish-mode",
        choices=PUBLISH_MODES,
    )
    closeout_trigger.add_argument("--publish-remote")

    closeout_status = closeout_sub.add_parser("status", help="Read a closeout transaction state.")
    closeout_status.add_argument("--tx-id", required=True)
    closeout_status.add_argument("--explain", action="store_true")

    closeout_recommend = closeout_sub.add_parser("codex-review", help="Record Codex's data-only closeout recommendation.")
    closeout_recommend.add_argument("--tx-id", required=True)
    closeout_recommend.add_argument("--recommendation-file", required=True)
    closeout_recommend.add_argument("--provenance-key-env", required=True)

    closeout_agent_review = closeout_sub.add_parser("agent-review", help="Record a read-only stranger review artifact.")
    closeout_agent_review.add_argument("--tx-id", required=True)
    closeout_agent_review.add_argument("--review-file", required=True)
    closeout_agent_review.add_argument("--provenance-key-env", required=True)

    closeout_approve = closeout_sub.add_parser("approve", help="Record trusted approval for a closeout recommendation.")
    closeout_approve.add_argument("--tx-id", required=True)
    closeout_approve.add_argument("--approval-file", required=True)
    closeout_approve.add_argument("--approval-nonce-env", required=True)
    closeout_approve.add_argument("--approval-provenance-key-env", required=True)
    closeout_approve.add_argument("--recommendation-provenance-key-env", required=True)
    closeout_approve.add_argument("--review-provenance-key-env", required=True)

    closeout_validate = closeout_sub.add_parser("validate-apply", help="Validate closeout transaction can be applied.")
    closeout_validate.add_argument("--tx-id", required=True)
    closeout_validate.add_argument("--approval-provenance-key-env", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            result = run_scan(
                Path(args.repo_root),
                write_artifacts_flag=not args.no_write_artifacts,
                trust_local_base=args.trust_local_base,
                update_observation_state=not args.no_write_artifacts,
            )
            if args.json:
                print(json.dumps({"run_dir": result["run_dir"], "plan": result["plan"]}, indent=2, sort_keys=True))
            else:
                print(result["summary"], end="")
                if result["run_dir"]:
                    print(f"\nArtifacts: {result['run_dir']}")
            has_debt = any(c["decision"] in {"auto_apply", "recommend"} for c in result["plan"]["candidates"])
            return 1 if has_debt else 0
        if args.command == "apply":
            result = run_apply(
                Path(args.repo_root),
                candidate_id=args.candidate_id,
                action_id=args.action_id,
                expected_evidence_hash=args.expected_evidence_hash,
                manual_override=args.manual_override,
                trust_local_base=args.trust_local_base,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "verify-policy":
            result = verify_policy(Path(args.repo_root))
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print("policy verification: %s" % ("ok" if result["ok"] else "failed"))
                for failure in result["failures"]:
                    print(f"- {failure}")
            return 0 if result["ok"] else 5
        if args.command == "closeout":
            repo_root = Path(args.repo_root)
            if args.closeout_command == "open":
                result = open_transaction(
                    repo_root,
                    base=args.base,
                    publish_mode=args.publish_mode,
                    publish_remote=args.publish_remote,
                )
            elif args.closeout_command == "trigger":
                result = evaluate_closeout_triggers(
                    repo_root,
                    open_if_triggered=args.open_if_triggered,
                    publish_mode=args.publish_mode,
                    publish_remote=args.publish_remote,
                )
            elif args.closeout_command == "status":
                result = transaction_status(repo_root, args.tx_id, explain=args.explain)
            elif args.closeout_command == "codex-review":
                recommendation = json.loads(Path(args.recommendation_file).read_text(encoding="utf-8"))
                result = record_codex_recommendation(
                    repo_root,
                    args.tx_id,
                    recommendation,
                    provenance_key=secret_from_env(args.provenance_key_env),
                )
            elif args.closeout_command == "agent-review":
                review = json.loads(Path(args.review_file).read_text(encoding="utf-8"))
                result = record_agent_review(
                    repo_root,
                    args.tx_id,
                    review,
                    provenance_key=secret_from_env(args.provenance_key_env),
                )
            elif args.closeout_command == "approve":
                approval = json.loads(Path(args.approval_file).read_text(encoding="utf-8"))
                result = approve_transaction(
                    repo_root,
                    args.tx_id,
                    approval,
                    secret_from_env(args.approval_nonce_env),
                    provenance_key=secret_from_env(args.approval_provenance_key_env),
                    recommendation_provenance_key=secret_from_env(args.recommendation_provenance_key_env),
                    review_provenance_key=secret_from_env(args.review_provenance_key_env),
                )
            elif args.closeout_command == "validate-apply":
                result = validate_transaction_apply(
                    repo_root,
                    args.tx_id,
                    approval_provenance_key=secret_from_env(args.approval_provenance_key_env),
                )
            else:
                return 4
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
            return 0
    except HygieneError as exc:
        print(f"repo hygiene error: {exc}", file=sys.stderr)
        return 5
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
