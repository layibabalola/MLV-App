#!/usr/bin/env python3
"""freeze-factory-cards.py -- deterministic one-way factory backlog freeze.

Phase0.5: freezes non-terminal factory-kind cards in a board queue document to
a fixed frozen state, preserving every product/playback card and all
historical fields untouched. See docs/factory-card-freeze.md for the full
contract this script implements.

Stdlib only. No subprocess, no git, no network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

FROZEN_STATE = "frozen-factory-20260906"

# Case-sensitive, exact match to tools/coordination/Invoke-Workstream.ps1's $Terminal.
TERMINAL_STATES = frozenset(
    [
        "closed-fixed",
        "closed-not-this-board",
        "closed-root-caused",
        "closed-superseded",
        "closed-transformed",
        "landed",
        "landed-evidence",
        "landed-local-proof",
        "CLEARED",
        "RETIRED",
        "withdrawn",
        "superseded",
        "retracted-and-fixed",
        "fixed",
        "answered-folded",
    ]
)

# Exact keep-live exception set: these IDs, when no recognized scope path is
# present, derive to playback (never factory) -- named explicitly by the root
# decision, not inferred from id/title/track/priority.
KEEP_LIVE_PLAYBACK_IDS = frozenset(
    [
        "USECASE-1",
        "MEASURE-STRATEGY-1",
        "VENUE-NOISE-1",
        "C2-SUBMIT-2",
        "C2-PROV-1",
        "C2-TELEM-2",
    ]
)

# Narrower playback roots checked first -- a match here beats a plain product
# root even though e.g. "platform/qt/" is also a product root.
PLAYBACK_PREFIXES = (
    "src/gpu/",
    "tools/gpu/",
    "platform/qt/GpuDisplay",
    "platform/qt/RenderThread",
    "platform/qt/OpenGLRenderThread",
)

PRODUCT_PREFIXES = (
    "src/",
    "platform/qt/",
    "tests/console/",
    "tests/gui/",
    "tests/pipeline/",
    "tests/fixtures/",
    "pixel_maps/",
    "data/",
)

KNOWN_KINDS = frozenset(["product", "playback", "factory"])

# Punctuation harmless enough to strip from either end of a raw scope token
# (quotes, brackets, trailing sentence punctuation) -- never characters that
# are legal inside a path (.-_~).
_STRIP_CHARS = "\"'`(){}[]<>,;:.!?*"


class QueueValidationError(ValueError):
    """Raised for a malformed or unsupported queue document. No writes happen."""


class ReceiptConflictError(RuntimeError):
    """Raised when an existing receipt disagrees with the freshly computed result."""


class ConcurrentModificationError(RuntimeError):
    """Raised when the queue file changed on disk between load and write."""


# --------------------------------------------------------------------- I/O


def load_queue_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def parse_queue(raw: bytes) -> dict:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QueueValidationError(f"queue is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise QueueValidationError(f"queue is not valid JSON: {exc}") from exc
    validate_queue(data)
    return data


def validate_queue(data) -> None:
    if not isinstance(data, dict):
        raise QueueValidationError("queue document must be a JSON object")
    if "items" not in data:
        raise QueueValidationError("queue document is missing required 'items' array")
    items = data["items"]
    if not isinstance(items, list):
        raise QueueValidationError("'items' must be a JSON array")
    seen_ids = set()
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise QueueValidationError(f"items[{idx}] is not a JSON object")
        card_id = item.get("id")
        if not isinstance(card_id, str) or card_id.strip() == "":
            raise QueueValidationError(f"items[{idx}] has a missing or empty string 'id'")
        if card_id in seen_ids:
            raise QueueValidationError(f"duplicate card id '{card_id}'")
        seen_ids.add(card_id)


# --------------------------------------------------------------- classification


def _normalize_token(raw_token: str) -> str | None:
    """Normalize one raw scope token; return a slash-path token or None.

    Bare filenames and prose never prove product/playback/factory -- only a
    token that contains a '/' after normalization is a path token.
    """
    tok = raw_token.replace("\\", "/").strip()
    tok = tok.strip(_STRIP_CHARS)
    # Strip a trailing line-suffix like "path/to/file.cpp:123".
    if ":" in tok:
        head, _, tail = tok.rpartition(":")
        if tail.isdigit() and head:
            tok = head
    tok = tok.strip(_STRIP_CHARS)
    if tok.startswith("./"):
        tok = tok[2:]
    if "/" not in tok:
        return None
    return tok


def _scope_tokens(scope_value) -> list[str]:
    if scope_value is None:
        return []
    raw_tokens: list[str] = []
    if isinstance(scope_value, str):
        raw_tokens = scope_value.replace(",", " ").split()
    elif isinstance(scope_value, list):
        for entry in scope_value:
            if isinstance(entry, str):
                raw_tokens.extend(entry.replace(",", " ").split())
    tokens = []
    for raw in raw_tokens:
        normalized = _normalize_token(raw)
        if normalized:
            tokens.append(normalized)
    return tokens


def _classify_token(token: str) -> str:
    """Every slash-path token classifies as playback, product, or factory."""
    if any(token.startswith(p) for p in PLAYBACK_PREFIXES):
        return "playback"
    if any(token.startswith(p) for p in PRODUCT_PREFIXES):
        return "product"
    return "factory"


def derive_kind(card_id: str, scope_value) -> tuple[str, bool]:
    """Derive (kind, is_scopeless) for a card with no existing 'kind'.

    is_scopeless is True whenever no recognized path token was found, even
    when the exact keep-live exception applies.
    """
    tokens = _scope_tokens(scope_value)
    classes = {_classify_token(t) for t in tokens}
    if "factory" in classes:
        return "factory", False
    if "playback" in classes:
        return "playback", False
    if "product" in classes:
        return "product", False
    # No recognized path token anywhere.
    if card_id in KEEP_LIVE_PLAYBACK_IDS:
        return "playback", True
    return "factory", True


# ---------------------------------------------------------------- plan build


def compute_plan(data: dict) -> dict:
    """Compute the deterministic proposed change set for `data['items']`.

    Returns a dict with new_items (new list, original items untouched unless
    changed), changes (ordered list of change records), scopeless_ids,
    unknown_kind_ids, and frozen_count (TOTAL frozen after applying, not just
    newly transitioned).
    """
    new_items = []
    changes = []
    scopeless_ids = []
    unknown_kind_ids = []
    frozen_count = 0

    for item in data["items"]:
        card = dict(item)
        card_id = card["id"]
        owner = card.get("owner")
        had_kind = "kind" in card and isinstance(card.get("kind"), str) and card["kind"] != ""
        original_kind = card.get("kind") if had_kind else None

        # Existing kind+owner=sonnet cards are deep-identical: never touched
        # in any way, regardless of kind value (product, playback, or even a
        # literal 'factory' string).
        protected_sonnet = had_kind and owner == "sonnet"

        if had_kind:
            kind = original_kind
            if kind not in KNOWN_KINDS:
                unknown_kind_ids.append(card_id)
            # track is never touched for a card that already carried a kind.
        else:
            kind, is_scopeless = derive_kind(card_id, card.get("scope"))
            if is_scopeless:
                scopeless_ids.append(card_id)
            card["kind"] = kind
            changes.append({"id": card_id, "field": "kind", "from": None, "to": kind})
            if not card.get("track"):
                card["track"] = kind
                changes.append({"id": card_id, "field": "track", "from": None, "to": kind})

        if not protected_sonnet and kind == "factory":
            state = card.get("state")
            if state == FROZEN_STATE:
                pass  # already frozen stays frozen
            elif state in TERMINAL_STATES:
                pass  # done work is left alone
            else:
                card["freezeProvenance"] = {
                    "previousState": state,
                    "frozenReason": "factory-card-freeze-phase0.5",
                }
                changes.append(
                    {"id": card_id, "field": "state", "from": state, "to": FROZEN_STATE}
                )
                card["state"] = FROZEN_STATE

        if card.get("state") == FROZEN_STATE:
            frozen_count += 1

        new_items.append(card)

    return {
        "new_items": new_items,
        "changes": changes,
        "scopeless_ids": scopeless_ids,
        "unknown_kind_ids": unknown_kind_ids,
        "frozen_count": frozen_count,
    }


def canonical_diff_bytes(changes: list) -> bytes:
    return json.dumps(changes, sort_keys=True, separators=(",", ":")).encode("utf-8")


def diff_sha256(changes: list) -> str:
    return hashlib.sha256(canonical_diff_bytes(changes)).hexdigest()


# ------------------------------------------------------------------ output


def render_queue(data: dict, new_items: list, original_len: int) -> bytes:
    out = dict(data)
    out["items"] = new_items
    text = json.dumps(out, indent=2, ensure_ascii=False)
    encoded = text.encode("utf-8") + b"\n"
    if len(encoded) < original_len:
        # Never knowingly shrink queue bytes: pad with trailing whitespace,
        # which json.loads happily ignores after the top-level value.
        encoded += b" " * (original_len - len(encoded))
    return encoded


def receipt_payload(recorded_utc: str, queue_sha256: str, frozen_count: int,
                     dry_run_diff_sha256: str, scopeless_ids: list) -> dict:
    return {
        "recordedUtc": recorded_utc,
        "queueSha256": queue_sha256,
        "frozenCount": frozen_count,
        "dryRunDiffSha256": dry_run_diff_sha256,
        "scopelessIds": list(scopeless_ids),
    }


def receipts_match(existing: dict, computed: dict) -> bool:
    # dryRunDiffSha256 is inherently run-relative (it is the diff a plan
    # computes against WHATEVER the queue currently looks like), so a second,
    # already-applied run legitimately computes an empty diff even though the
    # first run's diff was non-empty. "Identical result" is judged on the
    # artifacts a reader would actually rely on: the resulting queue content
    # and its derived totals, not the incidental diff of this particular run.
    keys = ("queueSha256", "frozenCount", "scopelessIds")
    return all(existing.get(k) == computed.get(k) for k in keys)


# --------------------------------------------------------------------- main


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic one-way factory backlog freeze (Phase0.5)."
    )
    parser.add_argument("--queue", required=True, help="Explicit path to the queue JSON document.")
    parser.add_argument(
        "--receipt",
        default=None,
        help="Explicit path to the freeze receipt JSON. Required with --apply.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Compute and print the plan; write nothing (default).")
    mode.add_argument("--apply", action="store_true", help="Write the frozen queue and receipt.")
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    apply_mode = bool(args.apply)

    if apply_mode and not args.receipt:
        print("freeze-factory-cards: --apply requires --receipt PATH", file=sys.stderr)
        return 2

    if not os.path.isfile(args.queue):
        print(f"freeze-factory-cards: no such queue file: {args.queue}", file=sys.stderr)
        return 2

    original_bytes = load_queue_bytes(args.queue)
    try:
        data = parse_queue(original_bytes)
    except QueueValidationError as exc:
        print(f"freeze-factory-cards: REFUSED (malformed queue): {exc}", file=sys.stderr)
        return 3

    plan = compute_plan(data)
    dd_sha = diff_sha256(plan["changes"])

    print(f"freeze-factory-cards: {len(plan['changes'])} field change(s) across "
          f"{len({c['id'] for c in plan['changes']})} card(s)")
    for c in plan["changes"]:
        print(f"  {c['id']}: {c['field']} {c['from']!r} -> {c['to']!r}")
    print(f"freeze-factory-cards: scopeless ids ({len(plan['scopeless_ids'])}): "
          f"{', '.join(plan['scopeless_ids']) if plan['scopeless_ids'] else '(none)'}")
    if plan["unknown_kind_ids"]:
        print(f"freeze-factory-cards: unknown existing kind, retained conservatively "
              f"({len(plan['unknown_kind_ids'])}): {', '.join(plan['unknown_kind_ids'])}")
    print(f"freeze-factory-cards: frozenCount(total)={plan['frozen_count']} dryRunDiffSha256={dd_sha}")

    if not apply_mode:
        return 0

    # --- apply mode ---
    computed_queue_bytes = render_queue(data, plan["new_items"], len(original_bytes))
    computed_queue_sha = hashlib.sha256(computed_queue_bytes).hexdigest()
    computed_receipt = receipt_payload(
        recorded_utc="<pending>",
        queue_sha256=computed_queue_sha,
        frozen_count=plan["frozen_count"],
        dry_run_diff_sha256=dd_sha,
        scopeless_ids=plan["scopeless_ids"],
    )

    if os.path.isfile(args.receipt):
        with open(args.receipt, "r", encoding="utf-8") as fh:
            try:
                existing_receipt = json.load(fh)
            except json.JSONDecodeError as exc:
                print(f"freeze-factory-cards: REFUSED (unreadable existing receipt): {exc}",
                      file=sys.stderr)
                return 4
        if receipts_match(existing_receipt, computed_receipt):
            print("freeze-factory-cards: existing receipt already matches computed result; "
                  "no-op (queue and receipt left byte-identical)")
            return 0
        print("freeze-factory-cards: REFUSED - existing receipt conflicts with the freshly "
              "computed result; queue was NOT touched. Move or delete the existing receipt at "
              f"{args.receipt} only after confirming it is stale evidence.", file=sys.stderr)
        return 5

    # Re-read the queue immediately before writing to detect a concurrent edit.
    current_bytes = load_queue_bytes(args.queue)
    if current_bytes != original_bytes:
        print("freeze-factory-cards: REFUSED - queue file changed on disk since it was read; "
              "re-run to compute a fresh plan. No writes performed.", file=sys.stderr)
        return 6

    if computed_queue_bytes == original_bytes:
        # No-op: nothing to freeze/derive. Preserve original queue bytes exactly.
        queue_written_bytes = original_bytes
    else:
        tmp_path = args.queue + ".freeze-factory-cards.tmp"
        with open(tmp_path, "wb") as fh:
            fh.write(computed_queue_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, args.queue)
        queue_written_bytes = computed_queue_bytes

    queue_sha = hashlib.sha256(queue_written_bytes).hexdigest()
    recorded_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    final_receipt = receipt_payload(
        recorded_utc=recorded_utc,
        queue_sha256=queue_sha,
        frozen_count=plan["frozen_count"],
        dry_run_diff_sha256=dd_sha,
        scopeless_ids=plan["scopeless_ids"],
    )

    try:
        # Exclusive creation: never overwrite existing evidence.
        with open(args.receipt, "x", encoding="utf-8") as fh:
            json.dump(final_receipt, fh, indent=2)
            fh.write("\n")
    except FileExistsError:
        # The queue write already landed (and is atomic/durable). Recovery
        # info: the queue's own sha is reproducible from its current bytes,
        # so re-running with the same --receipt path is the recovery path
        # once the racing receipt is inspected.
        print(
            "freeze-factory-cards: QUEUE WAS WRITTEN (queueSha256="
            f"{queue_sha}) but the receipt could not be created because "
            f"{args.receipt} now exists (created concurrently). This run did "
            "NOT overwrite it. Do not claim no mutation occurred: the queue "
            "at --queue reflects this run's result. Inspect the existing "
            "receipt and re-run once it is resolved.",
            file=sys.stderr,
        )
        return 7

    print(f"freeze-factory-cards: applied. queueSha256={queue_sha} frozenCount(total)="
          f"{plan['frozen_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
