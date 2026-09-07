"""Source-level wiring guard for PROD-DUALISO-GUARD-TEST.

`MainWindow.cpp` is not compiled by any test project, so the ordering at the
dual-ISO level-sync call site (wait for render-thread idle -> sync levels ->
invalidate the GPU preview config cache -> bake the config) can't be proven
by a unit test that links it. This test asserts, at the source level, that
`MainWindow.cpp` calls the extracted `DualIsoLevelSyncPolicy.h` decision and
that the four calls it guards still appear in that order.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW_CPP = REPO_ROOT / "platform" / "qt" / "MainWindow.cpp"
POLICY_HEADER = REPO_ROOT / "platform" / "qt" / "DualIsoLevelSyncPolicy.h"


class DualIsoPolicyWiringTest(unittest.TestCase):
    def setUp(self):
        self.source = MAIN_WINDOW_CPP.read_text(encoding="utf-8")

    def test_policy_header_exists(self):
        self.assertTrue(
            POLICY_HEADER.is_file(),
            "platform/qt/DualIsoLevelSyncPolicy.h must exist",
        )

    def test_main_window_includes_policy_header(self):
        self.assertIn(
            '#include "DualIsoLevelSyncPolicy.h"',
            self.source,
            "MainWindow.cpp must include DualIsoLevelSyncPolicy.h",
        )

    def test_main_window_references_the_policy_decision(self):
        self.assertIn(
            "dual_iso_level_sync_policy::shouldSync(",
            self.source,
            "MainWindow.cpp must call dual_iso_level_sync_policy::shouldSync(...)",
        )

    def _config_bake_block(self, source):
        start = source.index(
            "if( m_renderThreadUsingGpuPreviewProcessing || playbackProcessingSelected )"
        )
        end = source.index("\n    if( playbackProcessingSelected", start + 1)
        return source[start:end]

    def _assert_call_site_order(self, source):
        # Bind to the actual config-bake block, so unrelated calls or method
        # definitions elsewhere cannot satisfy this production wiring guard.
        block = self._config_bake_block(source)
        self.assertIn("dual_iso_level_sync_policy::shouldSync(", block)
        self.assertRegex(block, r"dualIsoLevelSyncSourceReady\s*&&\s*mlvProcessingDualIsoBlackWhiteLevelsOutOfSync\(")
        symbols = [
            "dual_iso_level_sync_policy::shouldSync",
            "waitForRenderThreadIdleBeforeCoreMutation",
            "mlvSyncProcessingDualIsoBlackWhiteLevels",
            "invalidateGpuPreviewProcessingConfigCache",
            "gpuPreviewProcessingConfigForCurrentSettings",
        ]
        positions = []
        for symbol in symbols:
            matches = list(re.finditer(re.escape(symbol) + r"\s*\(", block))
            self.assertEqual(len(matches), 1, "expected exactly one call to " + symbol)
            positions.append(matches[0].start())

        self.assertEqual(
            positions,
            sorted(positions),
            "wait -> sync -> invalidate -> config bake ordering must be preserved: %r"
            % (symbols,),
        )

    def test_call_site_preserves_wait_sync_invalidate_bake_order(self):
        self._assert_call_site_order(self.source)

    def test_ordered_decoy_elsewhere_cannot_hide_missing_production_sync(self):
        block = self._config_bake_block(self.source)
        broken = block.replace("mlvSyncProcessingDualIsoBlackWhiteLevels( m_pMlvObject );", "", 1)
        self.assertNotEqual(block, broken)
        mutated = self.source.replace(block, broken, 1) + "\n" + block
        with self.assertRaises(AssertionError):
            self._assert_call_site_order(mutated)

    def test_reordered_calls_at_production_site_are_rejected(self):
        block = self._config_bake_block(self.source)
        sync = "mlvSyncProcessingDualIsoBlackWhiteLevels( m_pMlvObject );"
        invalidate = "invalidateGpuPreviewProcessingConfigCache();"
        mutated_block = block.replace(sync, "SYNC_PLACEHOLDER", 1).replace(invalidate, sync, 1).replace("SYNC_PLACEHOLDER", invalidate, 1)
        self.assertNotEqual(block, mutated_block)
        with self.assertRaises(AssertionError):
            self._assert_call_site_order(self.source.replace(block, mutated_block, 1))


if __name__ == "__main__":
    unittest.main()
