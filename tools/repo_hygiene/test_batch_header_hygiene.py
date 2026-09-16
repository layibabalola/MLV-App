"""Guard the BatchTypes/BatchRenderedVideoPlan split against regressing back to a
heavyweight, catch-all header (PROD-BATCHTYPES-SPLIT-1 acceptance criteria).
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
BATCH_TYPES_HEADER = ROOT / "src" / "batch" / "BatchTypes.h"
MAX_LINES = 1500


def assert_include_absent(test_case: unittest.TestCase, source: str, include: str) -> None:
    """Shared assertion helper: fail loudly if `include` reappears in `source`."""
    test_case.assertNotIn(include, source, f"forbidden include reintroduced: {include}")


class BatchTypesHeaderHygieneTests(unittest.TestCase):
    def test_no_qregularexpression_include(self) -> None:
        source = BATCH_TYPES_HEADER.read_text(encoding="utf-8")
        assert_include_absent(self, source, "#include <QRegularExpression>")

    def test_no_qdir_include(self) -> None:
        source = BATCH_TYPES_HEADER.read_text(encoding="utf-8")
        assert_include_absent(self, source, "#include <QDir>")

    def test_line_count_under_cap(self) -> None:
        line_count = len(BATCH_TYPES_HEADER.read_text(encoding="utf-8").splitlines())
        self.assertLess(line_count, MAX_LINES, f"BatchTypes.h grew to {line_count} lines")

    def test_helper_fails_when_forbidden_include_is_present(self) -> None:
        # The two include tests above are only meaningful if this shared helper
        # can actually fail. Feed it a fixture that reintroduces a forbidden
        # include and confirm it reports the violation instead of passing
        # silently.
        hostile_fixture = (
            "#pragma once\n"
            "#include <QRegularExpression>\n"
            "struct BatchSettings {};\n"
        )
        with self.assertRaises(AssertionError):
            assert_include_absent(self, hostile_fixture, "#include <QRegularExpression>")


if __name__ == "__main__":
    unittest.main()
