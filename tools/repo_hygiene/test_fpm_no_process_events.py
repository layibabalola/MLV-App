"""Static-analysis guard for FocusPixelMapManager's bounded-wait refactor.

This is a SUPPLEMENT to the C++ behavior tests in
tests/console/test_sync_download_waiter.cpp and
tests/console/test_download_manager.cpp, not a replacement for them: this
script only checks source text shape (no processEvents(), guard acquired at
each entry point). It cannot prove the waiter's ordering/timeout/reentrancy
semantics -- that's what the C++ tests are for.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
FPM_CPP = ROOT / "platform/qt/FocusPixelMapManager.cpp"

GUARDED_ENTRY_POINTS = (
    "isMapAvailable",
    "downloadMap",
    "downloadAllMaps",
    "updateAllMaps",
)


class FocusPixelMapManagerNoProcessEventsTests(unittest.TestCase):
    def test_no_process_events_or_nested_event_loop_polling(self):
        source = FPM_CPP.read_text(encoding="utf-8")
        self.assertNotIn("processEvents", source)
        self.assertNotIn("QEventLoop", source)

    def test_each_public_entry_point_acquires_the_operation_guard_before_work(self):
        source = FPM_CPP.read_text(encoding="utf-8")

        for name in GUARDED_ENTRY_POINTS:
            match = re.search(
                r"FocusPixelMapManager::" + re.escape(name) + r"\s*\([^)]*\)\s*\{(.*?)\n\}",
                source,
                re.DOTALL,
            )
            self.assertIsNotNone(match, f"could not locate function body for {name}")
            body = match.group(1)

            guard_match = re.search(
                r"ScopedOperationGuard\s+(\w+)\s*\(\s*&m_operationInProgress\s*\)\s*;", body
            )
            self.assertIsNotNone(
                guard_match, f"{name} does not construct a ScopedOperationGuard over m_operationInProgress"
            )

            guard_name = guard_match.group(1)
            acquired_check = re.search(
                r"!\s*" + re.escape(guard_name) + r"\.acquired\s*\(\s*\)", body
            )
            self.assertIsNotNone(
                acquired_check,
                f"{name} constructs a guard named {guard_name} but never checks acquired() before doing work",
            )

            # The guard construction must be the first statement in the
            # body (i.e. no earlier statement has run). Since the match
            # starts at "ScopedOperationGuard" itself (after an optional
            # "SyncDownloadWaiter::" qualifier), check for a prior completed
            # statement instead of requiring literally blank leading text.
            guard_pos = guard_match.start()
            leading = body[:guard_pos]
            self.assertEqual(
                0,
                leading.count(";"),
                f"{name} does work before acquiring its operation guard",
            )

    def test_waiter_header_is_used_instead_of_manual_polling(self):
        source = FPM_CPP.read_text(encoding="utf-8")
        self.assertIn("SyncDownloadWaiter", source)


if __name__ == "__main__":
    unittest.main()
