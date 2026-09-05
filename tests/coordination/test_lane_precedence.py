"""Contract tests for inter-lane precedence in .dual-lane/ownership.json (OWN-1-PRECEDENCE).

The map deliberately overlaps: codex owns tools/gpu/** while claude owns tools/**, so the same
path matches two lanes and something must break the tie. Before lanePrecedence existed that
something was JSON property order -- codex resolved first only because it happens to be listed
first. The queue item calls that "undocumented luck, not policy", and it would flip silently if
the map were reserialised or a lane inserted above it.

The load-bearing test here is test_reordering_the_json_does_not_change_the_answer: it fails
against the pre-fix resolver and passes after, which is the only thing that distinguishes a
declared policy from a lucky one.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESOLVER = os.path.join(ROOT, "tools", "dual-lane", "owner-of.ps1")
MAP = os.path.join(ROOT, ".dual-lane", "ownership.json")


def owner_of(path, map_path=MAP):
    """Always pass -MapPath explicitly: the resolver otherwise derives the map from
    `git rev-parse --show-toplevel` of the CALLER cwd, which silently reads a different
    branch's map when invoked by absolute path from elsewhere."""
    res = subprocess.run(
        ["pwsh", "-NoProfile", "-File", RESOLVER, "-Path", path, "-MapPath", map_path],
        capture_output=True, text=True)
    return res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""


class LanePrecedenceTests(unittest.TestCase):
    def test_the_map_declares_precedence_at_all(self):
        d = json.load(open(MAP, encoding="utf-8"))
        self.assertIn("lanePrecedence", d, "inter-lane precedence is undeclared")
        for lane in d["lanes"]:
            self.assertIn(lane, d["lanePrecedence"], "lane %r missing from lanePrecedence" % lane)

    def test_the_overlap_this_item_names_still_resolves_the_same_way(self):
        """Behaviour preservation. Declaring policy must not silently reassign live paths."""
        self.assertEqual(owner_of("tools/gpu/backend/verify-exports.ps1"), "codex")
        self.assertEqual(owner_of("tools/coordination/owner-of.ps1"), "claude")

    def test_reordering_the_json_does_not_change_the_answer(self):
        """THE test. Pre-fix this flips to claude; post-fix precedence still decides."""
        d = json.load(open(MAP, encoding="utf-8"))
        reversed_lanes = {k: d["lanes"][k] for k in reversed(list(d["lanes"].keys()))}
        self.assertNotEqual(list(reversed_lanes.keys()), list(d["lanes"].keys()),
                            "map has <2 lanes; this test cannot discriminate")
        d["lanes"] = reversed_lanes
        with tempfile.TemporaryDirectory() as t:
            alt = os.path.join(t, "ownership.json")
            json.dump(d, open(alt, "w", encoding="utf-8"), indent=2)
            self.assertEqual(owner_of("tools/gpu/backend/verify-exports.ps1", alt), "codex",
                             "resolution followed JSON order, not declared precedence")

    def test_a_map_without_precedence_still_works(self):
        """Backwards compatibility: absent key falls back to JSON order, as before."""
        d = json.load(open(MAP, encoding="utf-8"))
        d.pop("lanePrecedence", None)
        with tempfile.TemporaryDirectory() as t:
            alt = os.path.join(t, "ownership.json")
            json.dump(d, open(alt, "w", encoding="utf-8"), indent=2)
            self.assertEqual(owner_of("tools/gpu/backend/verify-exports.ps1", alt), "codex")

    def test_unmapped_paths_still_fail_closed(self):
        """The resolver's whole contract: an unmapped path blocks, it does not guess."""
        self.assertEqual(owner_of("no/such/place/file.xyz"), "unknown")


if __name__ == "__main__":
    unittest.main()
