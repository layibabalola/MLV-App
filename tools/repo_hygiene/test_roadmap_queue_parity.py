"""Hosted-CI-safe half of plan step 0.18's parity check.

Asserts `docs/roadmap.md` (the tracked product-backlog mirror) agrees with the tracked
card/fields mirror at `docs/lane-prompts/v2/` — NEVER with `.claude-state/queue.json`,
which is gitignored and absent from every hosted checkout (O97/O106). A file there is
"dispatchable" iff it carries exactly one `^ALLOWED_PATHS:` line (O93) — that predicate
is derivable from the tracked tree alone, with no reference to the queue.

`tools/coordination/check_roadmap_queue_parity.py` is the other half: it validates
`queue.json` itself (`kind`, `track == kind`, `scope`) and is local-only by necessity.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROADMAP_PATH = REPO_ROOT / "docs" / "roadmap.md"
CARDS_DIR = REPO_ROOT / "docs" / "lane-prompts" / "v2"

ALLOWED_PATHS_RX = re.compile(r"^ALLOWED_PATHS:", re.MULTILINE)
ROADMAP_ROW_RX = re.compile(r"^\|\s*\S+\s*\|\s*(\S+)\s*\|\s*\S+\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)


def _dispatchable_card_files() -> set[str]:
    """Every file in the tracked mirror carrying exactly one ALLOWED_PATHS: line."""
    result = set()
    for path in CARDS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if len(ALLOWED_PATHS_RX.findall(text)) == 1:
            result.add(path.name)
    return result


def _roadmap_rows() -> list[tuple[str, str]]:
    """(card_id, procedure_filename) for every data row in the roadmap table."""
    text = ROADMAP_PATH.read_text(encoding="utf-8")
    return ROADMAP_ROW_RX.findall(text)


class RoadmapQueueParityTests(unittest.TestCase):
    def test_no_case_references_the_gitignored_queue(self) -> None:
        """Any mention of the gitignored queue must be disclaimed as non-hosted, somewhere in the file."""
        text = ROADMAP_PATH.read_text(encoding="utf-8")
        for forbidden in (".claude-state", "queue.json"):
            if forbidden in text:
                self.assertIn(
                    "gitignored",
                    text,
                    f"roadmap.md references {forbidden!r} without disclaiming it as gitignored/non-hosted",
                )

    def test_roadmap_size_budget(self) -> None:
        self.assertLessEqual(
            ROADMAP_PATH.stat().st_size, 4096, "docs/roadmap.md must stay <= 4 KB (plan step 0.18)"
        )

    def test_every_dispatchable_card_file_is_on_the_roadmap(self) -> None:
        dispatchable = _dispatchable_card_files()
        listed = {proc for _id, proc in _roadmap_rows()}
        missing = dispatchable - listed
        self.assertEqual(
            missing, set(), f"dispatchable card file(s) missing from docs/roadmap.md: {sorted(missing)}"
        )

    def test_every_roadmap_row_names_an_existing_dispatchable_file(self) -> None:
        dispatchable = _dispatchable_card_files()
        rows = _roadmap_rows()
        self.assertEqual(len(rows), 15, f"expected 15 roadmap rows, found {len(rows)}")
        for card_id, proc in rows:
            self.assertTrue((CARDS_DIR / proc).is_file(), f"{card_id}: {proc} does not exist in {CARDS_DIR}")
            self.assertIn(proc, dispatchable, f"{card_id}: {proc} exists but is not dispatchable (ALLOWED_PATHS count != 1)")

    def test_no_duplicate_card_ids_on_the_roadmap(self) -> None:
        ids = [card_id for card_id, _proc in _roadmap_rows()]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate card id(s) on the roadmap: {ids}")

    def test_usecase_1_is_never_listed(self) -> None:
        """USECASE-1 is a closure receipt only, never seeded as dispatchable (plan step 0.18)."""
        ids = {card_id for card_id, _proc in _roadmap_rows()}
        self.assertNotIn("USECASE-1", ids)


if __name__ == "__main__":
    unittest.main()
