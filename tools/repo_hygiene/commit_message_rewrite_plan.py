from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


VAGUE_SUBJECT_PATTERNS = [
    ("generic brokered checkpoint", re.compile(r"^brokered closeout checkpoint$")),
    ("generic brokered evidence repair", re.compile(r"^brokered closeout evidence repair$")),
    ("work-block checkpoint", re.compile(r"^chore\(closeout\): checkpoint (?P<paths>.+) for (?P<workBlockId>wb-[^ ]+)$")),
    (
        "work-block evidence repair",
        re.compile(r"^chore\(closeout\): repair (?P<paths>.+) for (?P<workBlockId>wb-[^ ]+) before (?P<reason>.+)$"),
    ),
    (
        "work-block merge",
        re.compile(r"^merge\(closeout\): integrate (?P<workBlockId>wb-[^ ]+) closeout hardening into (?P<target>.+)$"),
    ),
]


WORK_BLOCK_RE = re.compile(r"\b(wb-[0-9a-fA-F]+)\b")


@dataclass(frozen=True)
class CommitRow:
    hash: str
    parents: List[str]
    date: str
    subject: str


def run_git(repo_root: Path, args: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def classify_vague_subject(subject: str) -> Optional[Dict[str, str]]:
    for family, pattern in VAGUE_SUBJECT_PATTERNS:
        match = pattern.match(subject)
        if match:
            return {"family": family, **{key: value for key, value in match.groupdict().items() if value}}
    return None


def is_vague_subject(subject: str) -> bool:
    return classify_vague_subject(subject) is not None


def extract_work_block_id(subject: str) -> Optional[str]:
    classified = classify_vague_subject(subject)
    if classified and classified.get("workBlockId"):
        return classified["workBlockId"]
    match = WORK_BLOCK_RE.search(subject)
    return match.group(1) if match else None


def closeout_detail_for_subject(subject: str) -> str:
    classified = classify_vague_subject(subject) or {}
    family = classified.get("family")
    if family == "work-block checkpoint":
        return "Closeout: checkpoint %s." % classified.get("paths", "repo-managed updates")
    if family == "work-block evidence repair":
        return "Closeout: repair %s before %s." % (
            classified.get("paths", "closeout evidence"),
            classified.get("reason", "final push"),
        )
    if family == "work-block merge":
        return "Closeout: integrate work block %s into %s after clean validation." % (
            classified.get("workBlockId", "unknown"),
            classified.get("target", "target"),
        )
    if family == "generic brokered checkpoint":
        return "Closeout: checkpoint owned work."
    if family == "generic brokered evidence repair":
        return "Closeout: repair closeout evidence."
    return "Closeout: original generated subject was `%s`." % subject


def parse_log_rows(raw: str) -> List[CommitRow]:
    rows: List[CommitRow] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f", 3)
        if len(parts) != 4:
            continue
        rows.append(
            CommitRow(
                hash=parts[0],
                parents=[parent for parent in parts[1].split(" ") if parent],
                date=parts[2],
                subject=parts[3],
            )
        )
    return rows


def commit_log(repo_root: Path, ref: str) -> List[CommitRow]:
    raw = run_git(repo_root, ["log", ref, "--date=iso-strict", "--format=%H%x1f%P%x1f%cI%x1f%s"])
    return parse_log_rows(raw)


def side_branch_subjects(repo_root: Path, first_parent: str, second_parent: str) -> List[Dict[str, str]]:
    raw = run_git(repo_root, ["log", "--reverse", "--format=%H%x1f%s", "%s..%s" % (first_parent, second_parent)])
    subjects: List[Dict[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\x1f", 1)
        if len(parts) != 2:
            continue
        subjects.append({"hash": parts[0], "subject": parts[1]})
    return subjects


def changed_paths(repo_root: Path, commit_hash: str, *, limit: int = 20) -> List[str]:
    raw = run_git(repo_root, ["diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash])
    return [line for line in raw.splitlines() if line][:limit]


def merge_context_by_work_block(repo_root: Path, rows: Iterable[CommitRow]) -> Dict[str, Dict[str, Any]]:
    context: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        classified = classify_vague_subject(row.subject)
        if not classified or classified.get("family") != "work-block merge" or len(row.parents) < 2:
            continue
        work_block_id = str(classified["workBlockId"])
        branch_subjects = side_branch_subjects(repo_root, row.parents[0], row.parents[1])
        human_subjects = [
            item for item in branch_subjects if not is_vague_subject(item["subject"])
        ]
        context[work_block_id] = {
            "mergeHash": row.hash,
            "targetHead": row.parents[0],
            "featureHead": row.parents[1],
            "branchSubjects": branch_subjects,
            "humanSubjects": human_subjects,
        }
    return context


def proposed_subject_for_commit(row: CommitRow, merge_context: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    work_block_id = extract_work_block_id(row.subject)
    if work_block_id and work_block_id in merge_context:
        human_subjects = merge_context[work_block_id].get("humanSubjects", [])
        if len(human_subjects) == 1:
            return {
                "status": "candidate",
                "confidence": "medium",
                "subject": human_subjects[0]["subject"],
                "reason": "single non-generated subject found on the same work-block side branch",
                "sourceCommit": human_subjects[0]["hash"],
            }
        if len(human_subjects) > 1:
            return {
                "status": "needs_review",
                "confidence": "low",
                "subject": human_subjects[-1]["subject"],
                "reason": "multiple non-generated subjects found on the same work-block side branch; latest was selected only as a draft",
                "sourceCommit": human_subjects[-1]["hash"],
                "alternates": human_subjects,
            }
    return {
        "status": "needs_subject",
        "confidence": "none",
        "subject": None,
        "reason": "no reliable non-generated subject was found in the associated work-block history",
    }


def build_rewrite_plan(repo_root: Path, ref: str) -> Dict[str, Any]:
    rows = commit_log(repo_root, ref)
    vague_rows = [row for row in rows if is_vague_subject(row.subject)]
    merge_context = merge_context_by_work_block(repo_root, vague_rows)
    entries: List[Dict[str, Any]] = []
    for row in sorted(vague_rows, key=lambda item: item.date):
        classified = classify_vague_subject(row.subject) or {}
        proposal = proposed_subject_for_commit(row, merge_context)
        work_block_id = extract_work_block_id(row.subject)
        body = [closeout_detail_for_subject(row.subject)]
        if work_block_id:
            body.append("Work block: %s." % work_block_id)
        entries.append(
            {
                "hash": row.hash,
                "date": row.date,
                "parents": row.parents,
                "family": classified.get("family"),
                "workBlockId": work_block_id,
                "currentSubject": row.subject,
                "changedPaths": changed_paths(repo_root, row.hash),
                "proposal": proposal,
                "proposedBodyLines": body,
            }
        )
    family_counts: Dict[str, int] = {}
    status_counts: Dict[str, int] = {}
    for entry in entries:
        family = str(entry.get("family") or "unknown")
        status = str(entry.get("proposal", {}).get("status") or "unknown")
        family_counts[family] = family_counts.get(family, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schemaVersion": "commit-message-rewrite-plan.v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "ref": ref,
        "vagueCommitCount": len(entries),
        "earliestVagueCommit": entries[0] if entries else None,
        "latestVagueCommit": entries[-1] if entries else None,
        "familyCounts": family_counts,
        "proposalStatusCounts": status_counts,
        "rewriteSafety": {
            "historyRewriteRequired": True,
            "forcePushRequired": True,
            "requiresReviewedMapping": True,
            "readyForRewrite": bool(entries) and all(entry["proposal"]["status"] == "candidate" for entry in entries),
        },
        "entries": entries,
    }


def markdown_summary(plan: Dict[str, Any]) -> str:
    lines = [
        "# Commit Message Rewrite Plan",
        "",
        "- Ref: `%s`" % plan["ref"],
        "- Vague commits: %s" % plan["vagueCommitCount"],
        "- Earliest vague commit: `%s` on %s" % (
            (plan.get("earliestVagueCommit") or {}).get("hash", "")[:12],
            (plan.get("earliestVagueCommit") or {}).get("date", ""),
        ),
        "- Ready for rewrite: %s" % str(plan["rewriteSafety"]["readyForRewrite"]).lower(),
        "",
        "## Proposal Status",
        "",
    ]
    for status, count in sorted(plan["proposalStatusCounts"].items()):
        lines.append("- `%s`: %s" % (status, count))
    lines.extend(["", "## Family Counts", ""])
    for family, count in sorted(plan["familyCounts"].items()):
        lines.append("- `%s`: %s" % (family, count))
    lines.extend(
        [
            "",
            "## Review Queue",
            "",
            "Fill `proposal.subject` for entries with `needs_subject`, then rerun the planner or feed the reviewed JSON to a rewrite actor.",
            "Do not rewrite or force-push `master` until every entry has a reviewed human subject.",
            "",
        ]
    )
    for entry in plan["entries"]:
        proposal = entry["proposal"]
        if proposal["status"] == "candidate":
            continue
        lines.extend(
            [
                "### `%s`" % entry["hash"][:12],
                "",
                "- Date: %s" % entry["date"],
                "- Current: %s" % entry["currentSubject"],
                "- Status: `%s`" % proposal["status"],
                "- Reason: %s" % proposal["reason"],
                "- Changed paths: %s" % (", ".join(entry["changedPaths"][:8]) if entry["changedPaths"] else "(none listed)"),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_plan(plan: Dict[str, Any], output_root: Path) -> Dict[str, str]:
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "rewrite-plan.json"
    markdown_path = output_root / "rewrite-plan.md"
    json_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown_summary(plan), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def default_output_root(repo_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return repo_root / ".claude-state" / "commit-message-rewrite" / stamp


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a reviewed map for retroactive vague closeout commit messages.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--ref", default="master")
    parser.add_argument("--output-root")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    output_root = Path(args.output_root).resolve() if args.output_root else default_output_root(repo_root)
    plan = build_rewrite_plan(repo_root, args.ref)
    outputs = write_plan(plan, output_root)
    print(json.dumps({"status": "success", "outputs": outputs, "summary": {k: plan[k] for k in ("vagueCommitCount", "familyCounts", "proposalStatusCounts", "rewriteSafety")}}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
