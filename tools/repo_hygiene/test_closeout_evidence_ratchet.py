"""Validates the closeout capability ledger against its schema.

.closeout-evidence/ stays tracked and has no growth ratchet; see
docs/closeout-evidence-policy.md. NA-2 denies deleting or moving it.
"""

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(relative_path):
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


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
        errors = list(self.validator.iter_errors(hostile))
        self.assertTrue(errors)
        additional_properties_errors = [
            error for error in errors if error.validator == "additionalProperties"
        ]
        self.assertTrue(
            additional_properties_errors,
            "Expected an additionalProperties violation, got: "
            f"{[error.validator for error in errors]}",
        )
        self.assertTrue(
            any(
                "unexpectedTopLevelKey" in error.message
                for error in additional_properties_errors
            ),
            "Expected the additionalProperties error to name "
            f"'unexpectedTopLevelKey', got messages: "
            f"{[error.message for error in additional_properties_errors]}",
        )


if __name__ == "__main__":
    unittest.main()
