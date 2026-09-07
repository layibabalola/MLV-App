"""Guards against re-tracking large scratch media (e.g. profiling DNGs).

Golden fixtures under tests/fixtures/ are the sole authorized exception -
everything else tracked in the repository must not be a .dng file.
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_DNG_PREFIX = "tests/fixtures/"


def _tracked_paths():
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    raw = result.stdout.decode("utf-8", errors="surrogateescape")
    return [p for p in raw.split("\0") if p]


class NoTrackedScratchMediaTest(unittest.TestCase):
    def test_no_tracked_dng_outside_fixtures(self):
        offenders = [
            p
            for p in _tracked_paths()
            if p.lower().endswith(".dng") and not p.replace("\\", "/").startswith(ALLOWED_DNG_PREFIX)
        ]
        self.assertEqual(
            offenders,
            [],
            "Tracked .dng files found outside tests/fixtures/: "
            + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
