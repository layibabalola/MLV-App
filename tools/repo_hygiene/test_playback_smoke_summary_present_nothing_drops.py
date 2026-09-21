"""Static tripwire: the playback_smoke.summary line must export present_nothing_drops.

CUDA-SCALE4-ZERO-PRESENT-1: presentPlaybackPreparedFrame's silent-drop path
(see test_present_playback_prepared_frame_traces_drops.py) is now counted by
m_presentNothingDropCount and must surface in the same GUI-smoke summary line
that already reports prep_stale_drops/prep_generation_drops, so a scale-4
GL-window regression shows up as a nonzero counter instead of only a trace
line an operator has to go looking for.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW_CPP = ROOT / "platform/qt/MainWindow.cpp"


class PlaybackSmokeSummaryPresentNothingDropsTests(unittest.TestCase):
    def test_summary_format_string_declares_the_field(self):
        source = MAIN_WINDOW_CPP.read_text(encoding="utf-8")
        self.assertIn("playback_smoke.summary", source)
        self.assertIn("present_nothing_drops=%", source)

    def test_summary_format_string_is_followed_by_a_matching_arg_call(self):
        source = MAIN_WINDOW_CPP.read_text(encoding="utf-8")
        field_index = source.index("present_nothing_drops=%")
        placeholder_end = field_index + len("present_nothing_drops=%")
        digits = ""
        while placeholder_end < len(source) and source[placeholder_end].isdigit():
            digits += source[placeholder_end]
            placeholder_end += 1
        self.assertTrue(digits, "present_nothing_drops=% is not followed by a placeholder number")

        # The format string and its .arg() chain both live in
        # finishPlaybackSmokeTelemetry; the field must resolve to the drop
        # counter, not some unrelated value left behind by a future edit.
        chain_end = source.index(");", placeholder_end)
        arg_chain = source[placeholder_end:chain_end]
        self.assertIn("PresentNothingDrops", arg_chain)

        # The chain reads a local snapshot of the counter rather than the
        # atomic directly; confirm that local is actually sourced from
        # m_presentNothingDropCount somewhere in the same function.
        function_start = source.rindex(
            "void MainWindow::finishPlaybackSmokeTelemetry", 0, field_index
        )
        function_slice = source[function_start:chain_end]
        self.assertIn("m_presentNothingDropCount", function_slice)


if __name__ == "__main__":
    unittest.main()
