"""Ratchets the tracked .closeout-evidence/ workBlockId directory count.

.closeout-evidence/ is archive-only (NA-2); untracking any of it is
Phase-3-gated and out of scope here. This test only stops further growth.

Derivation of RATCHET (run from the repo root):
    git ls-files -- .closeout-evidence | cut -d/ -f2 | sort -u | wc -l
Lowering RATCHET as directories are cleaned up is allowed; raising it is not
-- if this test starts failing, that means stop tracking new evidence
directories, not raise the ratchet.
"""

import json
import subprocess
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
RATCHET = 617


def _tracked_closeout_evidence_dirs():
    result = subprocess.run(
        ["git", "ls-files", "--", ".closeout-evidence"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    dirs = set()
    for line in result.stdout.splitlines():
        parts = line.strip().replace("\\", "/").split("/")
        if len(parts) >= 2 and parts[1]:
            dirs.add(parts[1])
    return dirs


def _load_json(relative_path):
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


class CloseoutEvidenceRatchetTest(unittest.TestCase):
    def test_tracked_workblock_dir_count_does_not_exceed_ratchet(self):
        dirs = _tracked_closeout_evidence_dirs()
        self.assertLessEqual(
            len(dirs),
            RATCHET,
            "Tracked .closeout-evidence/ workBlockId directory count "
            f"({len(dirs)}) exceeds the ratchet ({RATCHET}).",
        )


class CloseoutCapabilityLedgerSchemaTest(unittest.TestCase):
    def setUp(self):
        self.schema = _load_json("CLOSEOUT-CAPABILITY-LEDGER.schema.json")
        self.ledger = _load_json("CLOSEOUT-CAPABILITY-LEDGER.json")
        self.validator = Draft202012Validator(self.schema)

    def test_ledger_validates_against_schema(self):
        self.validator.validate(self.ledger)

    def test_ledger_declares_stale_status(self):
        self.assertEqual(self.ledger["status"], "STALE-2026-05-08")

    def test_schema_still_rejects_unknown_top_level_keys(self):
        hostile = dict(self.ledger)
        hostile["unexpectedTopLevelKey"] = True
        self.assertTrue(list(self.validator.iter_errors(hostile)))


if __name__ == "__main__":
    unittest.main()
