"""Ensures every tools/profiling/test-*.ps1 pin suite is actually executed by CI.

Round 11: nothing under .github/workflows/ or tools/testing/ ran these suites
(the round-8/9/10 census, log-parsing, and validation-block-wiring pins were
enforced only by a manual hub checklist). This test globs the family and
executes each one via the real interpreter, failing on a non-zero exit code
or a printed "[SUMMARY] ... failed=N" line with N > 0 -- covering both a
suite that throws (the committed convention) and one that swallows a failure
into a counter without a nonzero exit.
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


def _suite_paths():
    return sorted(SUITE_DIR.glob("test-*.ps1"))


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

                match = SUMMARY_FAILED_RE.search(combined)
                if match:
                    failed = int(match.group(1))
                    self.assertEqual(
                        failed,
                        0,
                        f"{suite.name} printed failed={failed} despite exit 0:\n{combined}",
                    )


if __name__ == "__main__":
    unittest.main()
