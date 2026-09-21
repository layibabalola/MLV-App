"""Static tripwire: presentPlaybackPreparedFrame must never return silently.

CUDA-SCALE4-ZERO-PRESENT-1: at scale 4 in GL-window mode, this function used to
hit `!framePresentedByViewport && displayImage.isNull()`, release the render
slot, and `return;` with no trace, no counter and no draw_frame_ready.end --
the GUI thread simply dropped the frame. This is a SUPPLEMENT to the C++
policy tests in tests/gui/test_gui_smoke.cpp, not a replacement: it only
checks source shape (every bare `return;` inside the function is preceded by
a logInteractionEvent(...) call within a few lines), not runtime behavior.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW_CPP = ROOT / "platform/qt/MainWindow.cpp"

FUNCTION_NAME = "presentPlaybackPreparedFrame"
# How many lines above a bare `return;` we accept a tracing call within.
TRACE_LOOKBACK_LINES = 20


def _extract_function_body(source: str, function_name: str) -> str:
    start_match = re.search(
        r"void MainWindow::" + re.escape(function_name) + r"\s*\([^)]*\)\s*\n\{\n",
        source,
    )
    assert start_match is not None, f"could not locate {function_name} definition"

    body_start = start_match.end()
    depth = 1
    index = body_start
    while depth > 0:
        next_open = source.find("{", index)
        next_close = source.find("}", index)
        assert next_close != -1, f"unterminated body for {function_name}"
        if next_open != -1 and next_open < next_close:
            depth += 1
            index = next_open + 1
        else:
            depth -= 1
            index = next_close + 1
    body_end = index - 1
    return source[body_start:body_end]


class PresentPlaybackPreparedFrameTracesDropsTests(unittest.TestCase):
    def test_every_bare_return_is_preceded_by_a_traced_event(self):
        source = MAIN_WINDOW_CPP.read_text(encoding="utf-8")
        body = _extract_function_body(source, FUNCTION_NAME)
        body_lines = body.split("\n")

        bare_return_pattern = re.compile(r"^\s*return\s*;\s*$")
        untraced_returns = []
        for line_index, line in enumerate(body_lines):
            if not bare_return_pattern.match(line):
                continue
            lookback_start = max(0, line_index - TRACE_LOOKBACK_LINES)
            preceding = "\n".join(body_lines[lookback_start:line_index])
            if "logInteractionEvent(" not in preceding:
                # 1-indexed within the function body, for a readable message.
                untraced_returns.append(line_index + 1)

        self.assertEqual(
            [],
            untraced_returns,
            f"{FUNCTION_NAME} has bare `return;` statement(s) at body line(s) "
            f"{untraced_returns} with no logInteractionEvent(...) call in the "
            f"preceding {TRACE_LOOKBACK_LINES} lines -- a dropped frame must "
            "never be silent.",
        )

    def test_function_has_at_least_one_bare_return_to_guard(self):
        # Guards against the extraction regex silently matching nothing (e.g.
        # after an unrelated refactor renames or removes the early-out).
        source = MAIN_WINDOW_CPP.read_text(encoding="utf-8")
        body = _extract_function_body(source, FUNCTION_NAME)
        bare_return_pattern = re.compile(r"^\s*return\s*;\s*$", re.MULTILINE)
        self.assertTrue(
            bare_return_pattern.search(body),
            f"{FUNCTION_NAME} no longer contains a bare `return;` -- "
            "re-check whether this tripwire is still needed.",
        )


if __name__ == "__main__":
    unittest.main()
