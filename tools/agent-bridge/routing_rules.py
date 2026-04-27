"""
agent-bridge routing rule helper

Stores user-taught bridge routing preferences in:
  %USERPROFILE%\\.agent-bridge\\routing-rules.json

Commands:
  learn     Add a positive trigger rule
  suppress  Add a negative/suppressed trigger rule
  status    Print current learned/suppressed rules

Examples:
  py -3 tools/agent-bridge/routing_rules.py learn --source codex --direction codex->claude --pattern "Bridge tooling changes need Claude review" --type AUDIT_REQUEST --reason "User manually pasted it"
  py -3 tools/agent-bridge/routing_rules.py suppress --source claude --direction claude->codex --pattern "Routine ACK with no state change" --rule "Do not bridge routine ACKs unless they change state"
  py -3 tools/agent-bridge/routing_rules.py status
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_RULES = {
    "learned_triggers": [],
    "suppressed_triggers": [],
    "updated_at": None,
}


def utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def default_rules_path() -> Path:
    return Path(os.path.expanduser("~")) / ".agent-bridge" / "routing-rules.json"


def load_rules(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "learned_triggers": [],
            "suppressed_triggers": [],
            "updated_at": None,
        }
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("learned_triggers", [])
    data.setdefault("suppressed_triggers", [])
    data.setdefault("updated_at", None)
    return data


def save_rules(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = utc_date()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def make_base_rule(args: argparse.Namespace) -> Dict[str, Any]:
    rule = {
        "source": args.source,
        "direction": args.direction,
        "pattern": args.pattern,
        "reason": args.reason,
        "learned_from_session": args.session_id,
        "last_updated": utc_date(),
    }
    return {key: value for key, value in rule.items() if value is not None}


def add_learned(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.rules_path) if args.rules_path else default_rules_path()
    data = load_rules(path)
    entry = make_base_rule(args)
    if args.type:
        entry["suggested_type"] = args.type
    data["learned_triggers"].append(entry)
    save_rules(path, data)
    return {"path": str(path), "entry": entry}


def add_suppressed(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.rules_path) if args.rules_path else default_rules_path()
    data = load_rules(path)
    entry = make_base_rule(args)
    if args.rule:
        entry["rule"] = args.rule
    data["suppressed_triggers"].append(entry)
    save_rules(path, data)
    return {"path": str(path), "entry": entry}


def print_status(args: argparse.Namespace) -> None:
    path = Path(args.rules_path) if args.rules_path else default_rules_path()
    data = load_rules(path)
    print("routing rules:", path)
    print("updated_at:", data.get("updated_at"))
    print()
    print("learned_triggers:")
    print_entries(data.get("learned_triggers", []))
    print()
    print("suppressed_triggers:")
    print_entries(data.get("suppressed_triggers", []))


def print_entries(entries: List[Dict[str, Any]]) -> None:
    if not entries:
        print("  (none)")
        return
    for index, entry in enumerate(entries, start=1):
        print("  %d. %s" % (index, entry.get("pattern", "(no pattern)")))
        for key in ("direction", "source", "suggested_type", "rule", "reason", "learned_from_session", "last_updated"):
            if entry.get(key):
                print("     %s: %s" % (key, entry[key]))


def add_common_rule_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rules-path", help="Override routing-rules.json path")
    parser.add_argument("--source", required=True, choices=("claude", "codex", "user", "both"))
    parser.add_argument("--direction", required=True, help="Example: codex->claude, claude->codex, both")
    parser.add_argument("--pattern", required=True, help="Natural-language pattern to learn or suppress")
    parser.add_argument("--reason", required=True, help="Why this rule was learned")
    parser.add_argument("--session-id", help="Session GUID where this was learned")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage agent-bridge routing rules")
    sub = parser.add_subparsers(dest="command", required=True)

    learn = sub.add_parser("learn", help="Add a learned positive bridge trigger")
    add_common_rule_args(learn)
    learn.add_argument("--type", help="Optional suggested bridge TYPE, e.g. AUDIT_REQUEST")

    suppress = sub.add_parser("suppress", help="Add a suppressed/non-trigger rule")
    add_common_rule_args(suppress)
    suppress.add_argument("--rule", help="Optional explicit do-not-send rule")

    status = sub.add_parser("status", help="Print active routing rules")
    status.add_argument("--rules-path", help="Override routing-rules.json path")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "learn":
        result = add_learned(args)
        print("learned trigger added:", result["path"])
        print(json.dumps(result["entry"], indent=2, sort_keys=True))
    elif args.command == "suppress":
        result = add_suppressed(args)
        print("suppressed trigger added:", result["path"])
        print(json.dumps(result["entry"], indent=2, sort_keys=True))
    elif args.command == "status":
        print_status(args)


if __name__ == "__main__":
    main()
