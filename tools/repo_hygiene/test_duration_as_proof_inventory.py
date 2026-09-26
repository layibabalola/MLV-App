"""PROD-TELEMETRY-DURATION-AS-PROOF-4 round 1: make the duration-as-proof inventory mechanical.

Two PRs in a row (#156, #169) were parked because a hand-made inventory of "stage ran"
inferred from elapsed ms > 0 was incomplete -- the producer's own claim that a given file held
no other such site was proven false by a cross-family reviewer both times. This test replaces
the hand inventory with a live, CI-enforced one: tools/repo_hygiene/duration_as_proof_scan.py
re-derives every candidate site (see that module's docstring for the four pattern shapes) from
src/, platform/qt/ and tests/, and this test diffs that live output against the checked-in,
hand-classified tools/repo_hygiene/duration_as_proof_inventory.json.

A site is identified by (path, anchor) -- anchor is NORMALIZED LINE TEXT (see
duration_as_proof_scan.normalize_anchor), never a line number, so a site that merely moves
(another line inserted above it) does not need reclassification, but ANY change to the line's
own text does: the anchor no longer matches, the old inventory row goes stale, and the new text
is unclassified. Both directions fail the gate:
  - a NEW or CHANGED site (test_every_live_site_is_pinned_and_classified)
  - a STALE row the live scan no longer finds (test_no_stale_inventory_rows)

Positive controls (test_matcher_flags_seeded_positive_control /
test_matcher_ignores_seeded_negative_control) pin the scanner's own behavior against two
seeded snippets that are never written to disk as real source, so the gate proves it actually
runs the four patterns rather than passing vacuously.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.repo_hygiene.duration_as_proof_scan import ROOT, normalize_anchor, scan_repo, scan_text

INVENTORY = Path(__file__).resolve().parent / "duration_as_proof_inventory.json"

KNOWN_CLASSES = {
    "division_guard", "display_or_format", "value_fallback_select",
    "not_run_proof_structural", "run_proof",
}
KNOWN_CLOCKS = {"qpc_stage_clock", "omp_get_wtime_direct", "other"}


def _row_key(row: dict) -> tuple:
    return (row["path"], row["anchor"])


class DurationAsProofInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live_candidates = scan_repo(ROOT)
        cls.live_by_key = {(c.path, c.anchor): c for c in cls.live_candidates}
        data = json.loads(INVENTORY.read_text(encoding="utf-8"))
        cls.meta = data["_meta"]
        cls.inventory = data["rows"]
        cls.inventory_by_key = {_row_key(r): r for r in cls.inventory}

    def test_scanner_finds_at_least_one_candidate(self) -> None:
        # A scanner that silently matched nothing would make every other test in this module
        # vacuously pass. src/, platform/qt/ and tests/ are known (from the #169 review) to
        # contain real duration-as-proof sites, so an empty live scan is itself a failure.
        self.assertGreater(len(self.live_candidates), 0)

    def test_inventory_has_no_duplicate_keys(self) -> None:
        keys = [_row_key(r) for r in self.inventory]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(duplicates, [])

    def test_every_inventory_row_carries_a_known_class_and_clock(self) -> None:
        bad = [
            {"key": _row_key(r), "class": r.get("class"), "clock": r.get("clock")}
            for r in self.inventory
            if r.get("class") not in KNOWN_CLASSES or r.get("clock") not in KNOWN_CLOCKS
        ]
        self.assertEqual(bad, [], json.dumps(bad, indent=2))

    def test_every_run_proof_row_names_a_flag_or_is_openly_tracked(self) -> None:
        bad = []
        for r in self.inventory:
            if r.get("class") != "run_proof":
                continue
            has_flag = bool(r.get("flag"))
            is_open = r.get("status") == "open" and bool(r.get("successor"))
            if not (has_flag or is_open):
                bad.append(_row_key(r))
        self.assertEqual(
            bad, [],
            "run_proof row(s) with neither a replacing 'flag' nor an open 'status'+'successor': "
            f"{bad}",
        )

    def test_every_inventory_row_carries_a_nonempty_justification(self) -> None:
        bad = [_row_key(r) for r in self.inventory if not r.get("justification", "").strip()]
        self.assertEqual(bad, [])

    def test_every_live_site_is_pinned_and_classified(self) -> None:
        missing = [
            {"path": c.path, "anchor": c.anchor, "lines": list(c.lines), "triggers": list(c.triggers)}
            for key, c in self.live_by_key.items()
            if key not in self.inventory_by_key
        ]
        self.assertEqual(
            missing, [],
            "New or changed duration-as-proof site(s) with no pinned classification -- add a "
            f"row to {INVENTORY.name} with an honest class/clock/justification (see this test "
            "module's docstring and the inventory file's own _meta block for the legend), or "
            "fix the site so it no longer needs one:\n" + json.dumps(missing, indent=2),
        )

    def test_no_stale_inventory_rows(self) -> None:
        stale = [
            _row_key(r) for r in self.inventory if _row_key(r) not in self.live_by_key
        ]
        self.assertEqual(
            stale, [],
            f"Inventory row(s) no longer found by the live scan -- remove from {INVENTORY.name} "
            "if the site was deleted/rewritten away, or fix the row's anchor if the line's text "
            f"changed:\n{json.dumps(stale, indent=2)}",
        )

    # -- positive controls: prove the scanner itself still recognizes each pattern shape ------

    def test_matcher_flags_seeded_positive_control(self) -> None:
        seeded = "\n".join([
            "void f() {",
            '    if (some_stage_duration_ms > 0.0) { markRan(); }',
            "}",
        ])
        found = scan_text(seeded, source="<seeded-positive>")
        self.assertEqual(len(found), 1, found)
        self.assertIn("ident_compare", found[0].triggers)
        self.assertEqual(
            found[0].anchor,
            normalize_anchor("    if (some_stage_duration_ms > 0.0) { markRan(); }"),
        )

    def test_matcher_flags_seeded_json_key_and_assert_macro_controls(self) -> None:
        seeded = "\n".join([
            'if (sample.value(QStringLiteral("seeded_probe_ms")).toDouble() > 0.0) { return; }',
            "ASSERT_EQ(0.0, getSeededProbeMilliseconds());",
        ])
        found = scan_text(seeded, source="<seeded-json-and-macro>")
        triggers_by_anchor = {c.anchor: set(c.triggers) for c in found}
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any("json_ms_key" in t for t in triggers_by_anchor.values()))
        self.assertTrue(any("assert_macro" in t for t in triggers_by_anchor.values()))

    def test_matcher_ignores_seeded_negative_control(self) -> None:
        # None of these compare a duration-suffixed value to a zero literal: a non-zero
        # comparison, a comparison between two non-zero-literal expressions, a duration-shaped
        # identifier used without any comparison, and an unrelated "ms" substring that is not a
        # duration suffix (lowercase, mid-word).
        seeded = "\n".join([
            "void f() {",
            "    if (some_stage_duration_ms > 5.0) { markRan(); }",
            "    if (stage_a_ms > stage_b_ms) { pickA(); }",
            "    logDuration(some_stage_duration_ms);",
            "    int items = countItems();",
            "}",
        ])
        found = scan_text(seeded, source="<seeded-negative>")
        self.assertEqual(found, [], found)

    def test_seeded_controls_do_not_leak_into_the_real_inventory(self) -> None:
        # The seeded snippets above are synthetic sources (source="<seeded-...>"), never written
        # under src/, platform/qt/ or tests/, so they must never appear as a live repo candidate
        # or a pinned inventory row -- this would only happen if a future edit accidentally wrote
        # the seed strings into a real tracked file.
        seeded_markers = ("some_stage_duration_ms", "seeded_probe_ms", "getSeededProbeMilliseconds")
        for c in self.live_candidates:
            for marker in seeded_markers:
                self.assertNotIn(marker, c.anchor, c)
        for row in self.inventory:
            for marker in seeded_markers:
                self.assertNotIn(marker, row["anchor"], row)


if __name__ == "__main__":
    unittest.main()
