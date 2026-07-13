#!/usr/bin/env python3
"""Detect owed reviews, stalls, and repairable mirrored-ledger drift."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ENTRY_RE = re.compile(r"(?ms)^### \[(?P<ts>[^\]]+)\] (?P<actor>CODEX|CLAUDE) - (?P<kind>[^\r\n]+)\r?\n(?P<body>.*?)(?=^### \[|\Z)")
RANGE_RE = re.compile(r"(?m)^Range:\s*(?P<range>[0-9a-f]{40}\.[.][0-9a-f]{40})\s*$")
WORK_RE = re.compile(r"(?m)^WorkBlock:\s*(?P<work>[^\r\n]+)\s*$")
VERDICT_RE = re.compile(r"(?m)^Verdict:\s*(?P<verdict>APPROVE|CHANGES_REQUESTED|BLOCKER|HOLD|STATUS)\s*$")


def entries(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    result = []
    for match in ENTRY_RE.finditer(text):
        body = match.group("body")
        range_match = RANGE_RE.search(body)
        work_match = WORK_RE.search(body)
        verdict_match = VERDICT_RE.search(body)
        result.append(
            {
                "timestamp": match.group("ts"),
                "actor": match.group("actor"),
                "kind": match.group("kind").strip(),
                "body": body,
                "range": range_match.group("range") if range_match else "",
                "work": work_match.group("work").strip() if work_match else "",
                "verdict": verdict_match.group("verdict") if verdict_match else "",
                "raw": match.group(0),
            }
        )
    return result


def latest_handoff(items: list[dict[str, str]]) -> dict[str, str] | None:
    handoffs = [item for item in items if item["actor"] == "CODEX" and "HANDOFF" in item["kind"] and item["range"]]
    return handoffs[-1] if handoffs else None


def matching_review(items: list[dict[str, str]], handoff: dict[str, str]) -> dict[str, str] | None:
    for item in items:
        if item["actor"] != "CLAUDE" or "REVIEW" not in item["kind"]:
            continue
        if item["range"] == handoff["range"] and item["verdict"]:
            return item
    return None


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"missedHeartbeats": 0, "ackRequired": False}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def repair_missing_mirrors(paths: list[Path], parsed: list[list[dict[str, str]]], handoff: dict[str, str]) -> list[Path]:
    """Copy the authoritative latest handoff only into genuinely missing mirrors."""
    source = handoff["raw"]
    missing: list[Path] = []
    for path, items in zip(paths, parsed):
        same = [item for item in items if item["range"] == handoff["range"] and item["work"] == handoff["work"]]
        conflicting = [item for item in items if item["range"] == handoff["range"] and item["work"] != handoff["work"]]
        if conflicting:
            raise RuntimeError(f"refusing repair: conflicting handoff identity already exists in {path}")
        if not same:
            missing.append(path)
    if not missing:
        return []
    originals = {path: path.read_bytes() for path in missing}
    try:
        for path, original in originals.items():
            separator = b"" if not original or original.endswith((b"\n", b"\r")) else b"\n"
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.repair.", delete=False) as handle:
                temp_path = Path(handle.name)
                handle.write(original + separator + source.encode("utf-8"))
                handle.flush()
            temp_path.replace(path)
        return missing
    except Exception:
        for path, original in originals.items():
            path.write_bytes(original)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--ledger", action="append", type=Path, dest="ledgers")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--missed-heartbeats", type=int, default=None, help="test/monitor override")
    parser.add_argument("--ack", action="store_true", help="acknowledge the current repaired mirror fingerprint")
    parser.add_argument("--repair-mirrors", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo_root.resolve()
    paths = args.ledgers or [
        repo / ".claude-state/coordination/gpu-lane-impl-review-sync.md",
        repo / ".claude-state/coordination/dual-lane/claude.md",
        repo / ".claude-state/coordination/dual-lane/codex.md",
    ]
    paths = [path if path.is_absolute() else repo / path for path in paths]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("missing ledger(s): " + ", ".join(map(str, missing)))
    parsed = [entries(path) for path in paths]
    handoff = latest_handoff(parsed[0])
    if not handoff:
        print(json.dumps({"state": "IDLE", "reason": "no exact CODEX handoff"}, indent=2))
        return 0
    reviews = [matching_review(items, handoff) for items in parsed]
    review = next((item for item in reviews if item is not None), None)
    all_mirrored = all(any(item["range"] == handoff["range"] and item["work"] == handoff["work"] for item in items) for items in parsed)
    state_path = args.state_file or repo / ".claude-state/coordination/coordination-watchdog-state.json"
    state = load_state(state_path)
    repaired = []
    if args.repair_mirrors and not all_mirrored:
        repaired = repair_missing_mirrors(paths, parsed, handoff)
        state["ackRequired"] = bool(repaired)
        state["repairFingerprint"] = f"{handoff['range']}|{handoff['work']}"
        parsed = [entries(path) for path in paths]
        all_mirrored = all(any(item["range"] == handoff["range"] and item["work"] == handoff["work"] for item in items) for items in parsed)
    if args.ack:
        state["ackRequired"] = False
        state["ackFingerprint"] = f"{handoff['range']}|{handoff['work']}"
        state["missedHeartbeats"] = 0
        save_state(state_path, state)
    elif not review:
        state["missedHeartbeats"] = (args.missed_heartbeats if args.missed_heartbeats is not None else state.get("missedHeartbeats", 0) + 1)
        save_state(state_path, state)
    status = "APPROVED" if review and review["verdict"] == "APPROVE" else "OWED_REVIEW"
    if not all_mirrored:
        status = "MIRROR_DRIFT"
    if state.get("ackRequired"):
        status = "ACK_REQUIRED"
    if status == "OWED_REVIEW" and state.get("missedHeartbeats", 0) >= 2:
        status = "STALL"
    result = {
        "state": status,
        "range": handoff["range"],
        "workBlock": handoff["work"],
        "reviewVerdict": review["verdict"] if review else None,
        "mirrored": all_mirrored,
        "repairedMirrors": [str(path) for path in repaired],
        "ackRequired": bool(state.get("ackRequired")),
        "missedHeartbeats": state.get("missedHeartbeats", 0),
        "recoveryCommand": f"python tools/coordination/coordination_watchdog.py --repo-root {repo} --ack" if status == "STALL" else None,
        "finalizeEligible": status == "APPROVED" and not state.get("ackRequired", False),
    }
    print(json.dumps(result, indent=2))
    return 3 if status == "STALL" else (2 if status in {"OWED_REVIEW", "MIRROR_DRIFT", "ACK_REQUIRED"} else 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"WATCHDOG_BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
