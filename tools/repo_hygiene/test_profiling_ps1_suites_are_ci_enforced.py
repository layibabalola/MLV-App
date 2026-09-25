"""Ensures every tools/profiling/test-*.ps1 pin suite is actually executed by CI.

Round 11: nothing under .github/workflows/ or tools/testing/ ran these suites
(the round-8/9/10 census, log-parsing, and validation-block-wiring pins were
enforced only by a manual hub checklist). This test globs the family and
executes each one via the real interpreter, failing on a non-zero exit code
or a printed "[SUMMARY] ... failed=N" line with N > 0 -- covering both a
suite that throws (the committed convention) and one that swallows a failure
into a counter without a nonzero exit.

Round 12 (sol MINOR, "the wrapper reads only the first SUMMARY line"): a
suite that prints more than one "[SUMMARY] ... failed=N" line (e.g. a
per-phase counter that later reports a nonzero count after an earlier
phase's line already reported 0) only had its FIRST match checked via
`.search()`. Now every match is checked and the test fails if any is
nonzero.

Round 12 (fable MINOR, "a renamed suite silently leaves CI"): the glob only
guarded against collecting zero files, so renaming (or deleting) any single
suite shrank the family with every test staying green. The expected name set
is now pinned explicitly; a rename, addition, or removal must update it.
"""

import platform
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE_DIR = REPO_ROOT / "tools" / "profiling"

# Declared, not silently skipped: these suites use Windows-only facilities --
# literal backslash-separated relative paths that do not resolve on a
# non-Windows filesystem (e.g. Join-Path $root "tools\profiling\...", used by
# test-playback-quality-contract.ps1, test-playback-smoke-ab-authoritative-label.ps1,
# test-playback-smoke-log-parsing.ps1, and test-playback-smoke-validation-block-wiring.ps1),
# System.Drawing.Common, which is Windows-only outside an explicit AppContext
# opt-in (test-gui-smoke-color-artifact-scan.ps1:9), and/or the GUI smoke
# subject they exercise, whose real binary is
# platform\qt\build-release\release\MLVApp.exe, a Windows PE
# (run-release-gui-smoke.ps1:610). The whole family is gated together rather
# than split by mechanism so an off-Windows run never silently "passes" a
# suite it did not actually execute.
WINDOWS_ONLY_REASON = (
    "tools/profiling/test-*.ps1 suites require Windows facilities (backslash-relative "
    "Join-Path targets, System.Drawing.Common, and/or the MLVApp.exe GUI smoke subject "
    "binary) -- skipped off Windows rather than silently passed"
)

SUMMARY_FAILED_RE = re.compile(r"\[SUMMARY\][^\n]*\bfailed=(\d+)", re.IGNORECASE)

# Round 12 (fable MINOR): the exact suite-name census. A rename, addition, or
# removal must touch this set -- that is the point. Re-derive with:
#   Get-ChildItem tools/profiling/test-*.ps1 | Select-Object -Expand Name
EXPECTED_SUITE_NAMES = frozenset({
    "test-gui-smoke-color-artifact-scan.ps1",
    "test-gui-smoke-gpu-texture-route-validation.ps1",
    "test-gui-smoke-screenshot-provenance.ps1",
    "test-playback-quality-contract.ps1",
    "test-playback-smoke-ab-authoritative-label.ps1",
    "test-playback-smoke-log-parsing.ps1",
    "test-playback-smoke-validation-block-wiring.ps1",
})


def _suite_paths():
    return sorted(SUITE_DIR.glob("test-*.ps1"))


def _all_summary_lines_report_zero_failed(combined_output):
    """True iff every "[SUMMARY] ... failed=N" line in combined_output has
    N == 0 (or there are no such lines at all). Split out so the regression
    this guards against -- only the FIRST match being checked -- has a
    platform-independent unit test, not just the pwsh-gated end-to-end one
    below."""
    matches = SUMMARY_FAILED_RE.findall(combined_output)
    return all(int(m) == 0 for m in matches)


class ProfilingPs1SuitesAreCiEnforcedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pwsh = shutil.which("pwsh")
        cls.suites = _suite_paths()

    def test_suite_family_is_non_empty(self):
        # If this ever collects zero files, the glob or directory moved and
        # the whole mechanism silently stopped protecting anything.
        self.assertTrue(
            self.suites,
            f"No tools/profiling/test-*.ps1 suites found under {SUITE_DIR}",
        )

    def test_suite_family_matches_the_pinned_name_set(self):
        actual = {p.name for p in self.suites}
        self.assertEqual(
            actual,
            set(EXPECTED_SUITE_NAMES),
            "tools/profiling/test-*.ps1 no longer matches the pinned CI-enforced "
            "name set -- a rename, addition, or removal silently changes what CI "
            "actually runs. Update EXPECTED_SUITE_NAMES once the change is "
            "intentional.",
        )

    def test_each_profiling_ps1_suite_passes(self):
        if self.pwsh is None:
            self.skipTest("pwsh not found on PATH -- cannot execute PS1 pin suites")
        if platform.system() != "Windows":
            self.skipTest(WINDOWS_ONLY_REASON)

        for suite in self.suites:
            with self.subTest(suite=suite.name):
                result = subprocess.run(
                    [self.pwsh, "-NoProfile", "-NonInteractive", "-File", str(suite)],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                )
                combined = result.stdout + result.stderr

                self.assertEqual(
                    result.returncode,
                    0,
                    f"{suite.name} exited {result.returncode}:\n{combined}",
                )

                self.assertTrue(
                    _all_summary_lines_report_zero_failed(combined),
                    f"{suite.name} printed a nonzero failed=N among "
                    f"{SUMMARY_FAILED_RE.findall(combined)} despite exit 0:\n{combined}",
                )


class SummaryFailedRegressionTest(unittest.TestCase):
    """Platform-independent unit tests for _all_summary_lines_report_zero_failed
    -- these run everywhere (no pwsh, no Windows) unlike the end-to-end test
    above, so the round-12 fix itself is proven on every CI leg."""

    def test_single_zero_summary_passes(self):
        self.assertTrue(_all_summary_lines_report_zero_failed("[SUMMARY] failed=0"))

    def test_single_nonzero_summary_fails(self):
        self.assertFalse(_all_summary_lines_report_zero_failed("[SUMMARY] failed=1"))

    def test_no_summary_line_passes_vacuously(self):
        self.assertTrue(_all_summary_lines_report_zero_failed("no summary line here"))

    def test_second_nonzero_summary_line_is_not_ignored(self):
        # sol's exact repro: a first zero SUMMARY line followed by a later
        # nonzero one -- `.search()` would only ever see the first.
        combined = "[SUMMARY] first failed=0\n[SUMMARY] later failed=1"
        self.assertFalse(_all_summary_lines_report_zero_failed(combined))

    def test_first_nonzero_summary_line_is_still_caught(self):
        combined = "[SUMMARY] first failed=1\n[SUMMARY] later failed=0"
        self.assertFalse(_all_summary_lines_report_zero_failed(combined))


if __name__ == "__main__":
    unittest.main()
