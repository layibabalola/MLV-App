"""OWN-2 / GATE-RESIDUALS-1(b): the Dual-Lane trailer is a CLAIM, not an attestation.

lane-commit.ps1 takes -Lane as a caller-declared parameter validated against a two-value set.
Nothing identifies, challenges, or authenticates the caller, so the Dual-Lane: <lane> trailer it
writes records which lane OWNS THE PATHS, not who authored the change.

Proven in a landed range: 48e9cd2f and 90d49ee4 both carry "Dual-Lane: claude" on tools/** paths
after the codex lane ran -Lane claude. The ledger reads as though claude authored them.

These tests pin the honest label in place. A disclaimer that can be deleted without anything
failing is a comment, not a contract -- and this one exists precisely because the misreading is
durable: in a month nobody remembers the trailer was unverified.
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LANE_COMMIT = os.path.join(ROOT, "tools", "dual-lane", "lane-commit.ps1")


class LaneTrailerSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.text = open(LANE_COMMIT, encoding="utf-8").read()

    def test_the_trailer_is_documented_as_a_label_not_an_attestation(self):
        low = self.text.lower()
        self.assertIn("caller-declared", low)
        self.assertIn("path-ownership label", low)
        self.assertIn("not an authorship attestation", low)

    def test_the_disclaimer_names_the_landed_counter_example(self):
        """An abstract caveat is easy to dismiss; a commit id is not."""
        self.assertTrue(re.search(r"48e9cd2f", self.text), "landed counter-example not cited")

    def test_lane_really_is_caller_declared(self):
        """If -Lane ever DOES authenticate, this disclaimer becomes false and must be revisited."""
        self.assertRegex(self.text, r"\[ValidateSet\('codex',\s*'claude'\)\]\[string\]\$Lane")

    def test_the_cited_commits_still_show_the_mismatch(self):
        """The evidence is a claim about this repo's history; verify it rather than trust it."""
        for sha in ("48e9cd2f", "90d49ee4"):
            res = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%(trailers:key=Dual-Lane)", sha],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                self.skipTest("commit %s not present in this clone" % sha)
            self.assertIn("claude", res.stdout, "%s no longer carries Dual-Lane: claude" % sha)
            files = subprocess.run(["git", "-C", ROOT, "show", "--name-only", "--format=", sha],
                                   capture_output=True, text=True).stdout
            self.assertTrue(any(f.startswith("tools/") for f in files.splitlines() if f.strip()),
                            "%s no longer touches tools/**" % sha)


if __name__ == "__main__":
    unittest.main()
