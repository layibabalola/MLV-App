"""
agent-bridge routing rule helper

Stores user-taught bridge routing preferences in:
  %USERPROFILE%\\.agent-bridge\\routing-rules.json

Commands:
  learn     Add a positive trigger rule
  suppress  Add a negative/suppressed trigger rule
  feedback  Infer learn/suppress/status from natural language
  prune     Remove stale learned/suppressed rules
  status    Print current learned/suppressed rules

Examples:
  py -3 tools/agent-bridge/routing_rules.py learn --source codex --direction codex->claude --pattern "Bridge tooling changes need Claude review" --type AUDIT_REQUEST --reason "User manually pasted it"
  py -3 tools/agent-bridge/routing_rules.py suppress --source claude --direction claude->codex --pattern "Routine ACK with no state change" --rule "Do not bridge routine ACKs unless they change state"
  py -3 tools/agent-bridge/routing_rules.py feedback --message "you should have sent that automatically" --source codex --direction codex->claude --pattern "Bridge tooling changes need Claude review"
  py -3 tools/agent-bridge/routing_rules.py status
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import default_bridge_root


DEFAULT_RULES = {
    "learned_triggers": [],
    "suppressed_triggers": [],
    "updated_at": None,
}

VALID_TYPES = {
    "IMPLEMENTATION_SUMMARY",
    "PHASE_DONE",
    "DOGFOOD_REPORT",
    "TEST_RESULT",
    "BLOCKER",
    "SCOPE_QUERY",
    "AUDIT_REQUEST",
    "SESSION_UPDATE",
    "PHASE_APPROVED",
    "PHASE_PASSED",
    "AUDIT_RESULT",
    "SCOPE_CHANGE",
    "ARCH_DECISION",
    "UNBLOCK",
    "ACTION_REQUEST",
    "USER_PREFERENCE",
}

LEARN_PHRASES = (
    "bridge learn",
    "should have sent",
    "should have been sent",
    "should have gone over the bridge",
    "send this automatically",
    "send it automatically",
    "relay this automatically",
    "you should have bridged",
    "next time send this",
)

SUPPRESS_PHRASES = (
    "bridge suppress",
    "stop bridging this",
    "stop sending messages like this",
    "don't bridge these",
    "dont bridge these",
    "don't send messages like this",
    "dont send messages like this",
    "keep this local",
    "this kind of message is noise",
)

STATUS_PHRASES = (
    "bridge rule status",
    "what bridge rules",
    "show bridge rules",
    "show me current bridge preferences",
    "what are we auto-sending",
)


def utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def default_rules_path() -> Path:
    return default_bridge_root() / "routing-rules.json"


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


def entry_identity(entry: Dict[str, Any], kind: str) -> tuple:
    if kind == "learned":
        return (
            entry.get("source"),
            entry.get("direction"),
            entry.get("pattern"),
            entry.get("suggested_type"),
        )
    return (
        entry.get("source"),
        entry.get("direction"),
        entry.get("pattern"),
        entry.get("rule"),
    )


def upsert_entry(entries: List[Dict[str, Any]], entry: Dict[str, Any], kind: str) -> str:
    identity = entry_identity(entry, kind)
    for index, existing in enumerate(entries):
        if entry_identity(existing, kind) == identity:
            merged = dict(existing)
            merged.update(entry)
            entries[index] = merged
            return "updated"
    entries.append(entry)
    return "added"


def normalize_spaces(text: str) -> str:
    return " ".join(text.strip().split())


def infer_intent(message: str) -> Optional[str]:
    lowered = message.casefold()
    if any(phrase in lowered for phrase in STATUS_PHRASES):
        return "status"
    if any(phrase in lowered for phrase in SUPPRESS_PHRASES):
        return "suppress"
    if any(phrase in lowered for phrase in LEARN_PHRASES):
        return "learn"
    return None


def infer_type(message: str) -> Optional[str]:
    match = re.search(r"\b([A-Z][A-Z_]{2,})\b", message)
    if match and match.group(1) in VALID_TYPES:
        return match.group(1)

    lowered = message.casefold()
    for value in VALID_TYPES:
        if value.casefold() in lowered:
            return value

    match = re.search(r"\bas\s+([A-Za-z_]+)\b", message, flags=re.IGNORECASE)
    if match:
        candidate = match.group(1).upper()
        if candidate in VALID_TYPES:
            return candidate
    return None


def inferred_reason(intent: str, message: str) -> str:
    normalized = normalize_spaces(message)
    if intent == "learn":
        return "Natural-language bridge learn: %s" % normalized
    if intent == "suppress":
        return "Natural-language bridge suppress: %s" % normalized
    return normalized


def inferred_rule(pattern: str) -> str:
    return "Do not auto-bridge messages matching this pattern: %s" % pattern


def resolve_pattern(args: argparse.Namespace) -> str:
    if getattr(args, "pattern", None):
        return normalize_spaces(args.pattern)
    if getattr(args, "message", None):
        return normalize_spaces(args.message)
    raise ValueError("A pattern or message is required")


def add_learned(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.rules_path) if args.rules_path else default_rules_path()
    data = load_rules(path)
    entry = make_base_rule(args)
    if args.type:
        entry["suggested_type"] = args.type
    result = upsert_entry(data["learned_triggers"], entry, "learned")
    save_rules(path, data)
    return {"path": str(path), "entry": entry, "result": result}


def add_suppressed(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.rules_path) if args.rules_path else default_rules_path()
    data = load_rules(path)
    entry = make_base_rule(args)
    if args.rule:
        entry["rule"] = args.rule
    result = upsert_entry(data["suppressed_triggers"], entry, "suppressed")
    save_rules(path, data)
    return {"path": str(path), "entry": entry, "result": result}


def handle_feedback(args: argparse.Namespace) -> None:
    intent = infer_intent(args.message)
    if intent == "status":
        status_args = argparse.Namespace(rules_path=args.rules_path)
        print_status(status_args)
        return
    if intent is None:
        raise SystemExit(
            "Could not infer feedback intent from message. Use learn/suppress/status explicitly "
            "or include a phrase like 'you should have sent that automatically' or 'stop bridging this'."
        )

    args.pattern = resolve_pattern(args)
    args.reason = args.reason or inferred_reason(intent, args.message)
    if intent == "learn":
        args.type = args.type or infer_type(args.message)
        result = add_learned(args)
        print("learned trigger %s:" % result["result"], result["path"])
        print(json.dumps(result["entry"], indent=2, sort_keys=True))
        return

    args.rule = args.rule or inferred_rule(args.pattern)
    result = add_suppressed(args)
    print("suppressed trigger %s:" % result["result"], result["path"])
    print(json.dumps(result["entry"], indent=2, sort_keys=True))


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


def prune_rules(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.rules_path) if args.rules_path else default_rules_path()
    data = load_rules(path)
    cutoff = utc_date()
    days = args.days

    def keep(entry: Dict[str, Any]) -> bool:
        stamp = entry.get("last_updated")
        if not stamp:
            return True
        try:
            dt = datetime.fromisoformat(stamp).date()
        except ValueError:
            return True
        return (datetime.now(timezone.utc).date() - dt).days <= days

    before_learned = len(data["learned_triggers"])
    before_suppressed = len(data["suppressed_triggers"])
    data["learned_triggers"] = [entry for entry in data["learned_triggers"] if keep(entry)]
    data["suppressed_triggers"] = [entry for entry in data["suppressed_triggers"] if keep(entry)]
    save_rules(path, data)
    return {
        "path": str(path),
        "learned_removed": before_learned - len(data["learned_triggers"]),
        "suppressed_removed": before_suppressed - len(data["suppressed_triggers"]),
        "pruned_before": cutoff,
    }


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

    feedback = sub.add_parser("feedback", help="Infer routing feedback from natural language")
    feedback.add_argument("--rules-path", help="Override routing-rules.json path")
    feedback.add_argument("--message", required=True, help="Natural-language feedback, e.g. 'you should have sent that automatically'")
    feedback.add_argument("--source", required=True, choices=("claude", "codex", "user", "both"))
    feedback.add_argument("--direction", required=True, help="Example: codex->claude, claude->codex, both")
    feedback.add_argument("--pattern", help="Optional explicit pattern if the natural-language message is too generic")
    feedback.add_argument("--reason", help="Optional explicit reason override")
    feedback.add_argument("--session-id", help="Session GUID where this was learned")
    feedback.add_argument("--type", help="Optional suggested bridge TYPE, e.g. AUDIT_REQUEST")
    feedback.add_argument("--rule", help="Optional explicit do-not-send rule for suppress feedback")

    prune = sub.add_parser("prune", help="Remove stale routing rules")
    prune.add_argument("--rules-path", help="Override routing-rules.json path")
    prune.add_argument("--days", type=int, default=90, help="Keep rules updated within the last N days")

    status = sub.add_parser("status", help="Print active routing rules")
    status.add_argument("--rules-path", help="Override routing-rules.json path")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "learn":
        result = add_learned(args)
        print("learned trigger %s:" % result["result"], result["path"])
        print(json.dumps(result["entry"], indent=2, sort_keys=True))
    elif args.command == "suppress":
        result = add_suppressed(args)
        print("suppressed trigger %s:" % result["result"], result["path"])
        print(json.dumps(result["entry"], indent=2, sort_keys=True))
    elif args.command == "feedback":
        handle_feedback(args)
    elif args.command == "prune":
        result = prune_rules(args)
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "status":
        print_status(args)


if __name__ == "__main__":
    main()
