"""Local-only queue-arm check for the PHASE 1 product backlog (plan step 0.18).

Validates every `queue.json` item whose `kind` is `product` or `playback`: it must
carry `track == kind` (the dispatcher's `Get-Track` reads only `track`, so a card
missing it is invisible to `-Track product`/`-Track playback` regardless of what its
`kind` says — S82), a non-empty `scope`, and a `procedure` path that exists on disk and
still matches the recorded `procedureSha256` (a currency check: the ratified card
changed out from under a queue entry). Prints one line per card and a final summary to
stdout; the hub records the stdout sha256 in `0.18-roadmap-parity.json` as
`queueArmResultSha256`.

LOCAL ONLY. `queue.json` lives under `.claude-state/`, which is gitignored and absent
from every hosted checkout (O97/O106) — this script is never collected by hosted CI's
`unittest discover`, and `tools/repo_hygiene/test_roadmap_queue_parity.py` is the
hosted-safe half that checks `docs/roadmap.md` against the tracked card mirror instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUEUE_PATH = (
    REPO_ROOT / ".claude-state" / "coordination" / "dual-lane" / "queue.json"
)
KINDS = ("product", "playback")


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_queue(queue_path: Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    data = json.loads(queue_path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    cards = [it for it in items if it.get("kind") in KINDS]

    for card in sorted(cards, key=lambda c: (c.get("priority", 0), c.get("id", ""))):
        card_id = card.get("id", "<no id>")
        problems: list[str] = []

        kind = card.get("kind")
        track = card.get("track")
        if track != kind:
            problems.append(f"track={track!r} != kind={kind!r}")

        scope = card.get("scope")
        if not scope or not isinstance(scope, str):
            problems.append("scope missing or empty")

        procedure = card.get("procedure")
        if not procedure:
            problems.append("procedure missing")
        else:
            proc_path = REPO_ROOT / procedure
            if not proc_path.is_file():
                problems.append(f"procedure file does not exist: {procedure}")
            else:
                recorded = card.get("procedureSha256")
                actual = _sha256_of(proc_path)
                if recorded and recorded != actual:
                    problems.append(
                        f"procedureSha256 stale: recorded={recorded} actual={actual}"
                    )

        if problems:
            ok = False
            lines.append(f"FAIL {card_id}: {'; '.join(problems)}")
        else:
            lines.append(f"OK   {card_id} kind={kind} priority={card.get('priority')}")

    lines.append(f"{'OK' if ok else 'FAIL'}: {len(cards)} product/playback card(s) checked")
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue",
        type=Path,
        default=DEFAULT_QUEUE_PATH,
        help="Path to queue.json (default: the board's own .claude-state queue)",
    )
    args = parser.parse_args(argv)

    ok, lines = check_queue(args.queue)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
