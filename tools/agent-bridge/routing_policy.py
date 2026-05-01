import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import default_bridge_root


def default_rules_path() -> Path:
    return default_bridge_root() / "routing-rules.json"


def load_rules(path: Optional[str] = None) -> Dict[str, Any]:
    rules_path = Path(path) if path else default_rules_path()
    if not rules_path.exists():
        return {"path": str(rules_path), "learned_triggers": [], "suppressed_triggers": []}
    with rules_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("learned_triggers", [])
    data.setdefault("suppressed_triggers", [])
    data["path"] = str(rules_path)
    return data


def _matches(entry: Dict[str, Any], source: str, direction: str, text: str) -> bool:
    if entry.get("source") not in {None, "", "both", source}:
        return False
    if entry.get("direction") not in {None, "", "both", direction}:
        return False
    pattern = (entry.get("pattern") or "").strip()
    if not pattern:
        return False
    haystack = text.casefold()
    if pattern.casefold() in haystack:
        return True
    tokens = [token for token in re.findall(r"[a-z0-9]+", pattern.casefold()) if len(token) >= 4]
    if not tokens:
        return False
    matched = sum(1 for token in tokens if token in haystack)
    return matched >= min(2, len(tokens))


def evaluate_message(
    *,
    source: str,
    direction: str,
    text: str,
    rules_path: Optional[str] = None,
) -> Dict[str, Any]:
    rules = load_rules(rules_path)

    for entry in rules.get("suppressed_triggers", []):
        if _matches(entry, source, direction, text):
            return {
                "decision": "suppress",
                "matched_rule": entry,
                "rules_path": rules["path"],
            }

    for entry in rules.get("learned_triggers", []):
        if _matches(entry, source, direction, text):
            return {
                "decision": "learned_send",
                "matched_rule": entry,
                "suggested_type": entry.get("suggested_type"),
                "rules_path": rules["path"],
            }

    return {
        "decision": "no_match",
        "rules_path": rules["path"],
    }


def print_status(rules_path: Optional[str] = None) -> None:
    rules = load_rules(rules_path)
    print("routing rules:", rules["path"])
    print("learned:", len(rules.get("learned_triggers", [])))
    print("suppressed:", len(rules.get("suppressed_triggers", [])))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate learned/suppressed bridge routing rules")
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate = sub.add_parser("evaluate", help="Evaluate a message against routing rules")
    evaluate.add_argument("--rules-path", help="Override routing-rules.json path")
    evaluate.add_argument("--source", required=True, choices=("claude", "codex", "user", "both"))
    evaluate.add_argument("--direction", required=True, help="Example: codex->claude, claude->codex, both")
    evaluate.add_argument("--text", required=True, help="Message text to evaluate")

    status = sub.add_parser("status", help="Print routing-rule counts")
    status.add_argument("--rules-path", help="Override routing-rules.json path")

    args = parser.parse_args()
    if args.command == "evaluate":
        result = evaluate_message(
            source=args.source,
            direction=args.direction,
            text=args.text,
            rules_path=args.rules_path,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_status(args.rules_path)


if __name__ == "__main__":
    main()
