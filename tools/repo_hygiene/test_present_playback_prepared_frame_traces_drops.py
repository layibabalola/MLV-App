"""Static tripwire: presentPlaybackPreparedFrame must never return silently.

CUDA-SCALE4-ZERO-PRESENT-1: at scale 4 in GL-window mode, this function used to
hit `!framePresentedByViewport && displayImage.isNull()`, release the render
slot, and `return;` with no trace, no counter and no draw_frame_ready.end --
the GUI thread simply dropped the frame. This is a SUPPLEMENT to the C++
policy tests in tests/gui/test_gui_smoke.cpp, not a replacement: it only
checks source shape, not runtime behavior.

Round 3: the trace requirement is scoped to the nearest enclosing brace block
(not a fixed line lookback), so a trace call sitting in a sibling or outer
block can no longer satisfy a `return;` it doesn't actually cover. The
`return;` scan matches the token anywhere in the body -- including inline
`if( x ) return;` -- not just a whole line containing only `return;`.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW_CPP = ROOT / "platform/qt/MainWindow.cpp"

FUNCTION_NAME = "presentPlaybackPreparedFrame"

RETURN_PATTERN = re.compile(r"\breturn\s*;")


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


def _find_returns_with_enclosing_block(body: str):
    """Return a list of (return_start_index, enclosing_block_start_index).

    The enclosing block is the nearest unclosed `{` at the point the
    `return;` token is encountered -- i.e. the innermost brace scope the
    return statement actually executes in, whether it sits alone on its own
    line or inline after an `if( ... )`.
    """
    stack = [0]
    results = []
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char == "{":
            stack.append(index + 1)
            index += 1
        elif char == "}":
            if len(stack) > 1:
                stack.pop()
            index += 1
        else:
            match = RETURN_PATTERN.match(body, index)
            if match:
                results.append((match.start(), stack[-1]))
                index = match.end()
            else:
                index += 1
    return results


class PresentPlaybackPreparedFrameTracesDropsTests(unittest.TestCase):
    def test_every_return_is_traced_within_its_own_block(self):
        source = MAIN_WINDOW_CPP.read_text(encoding="utf-8")
        body = _extract_function_body(source, FUNCTION_NAME)

        untraced_returns = []
        for return_start, block_start in _find_returns_with_enclosing_block(body):
            preceding_in_block = body[block_start:return_start]
            if "logInteractionEvent(" not in preceding_in_block:
                # 1-indexed line within the function body, for a readable message.
                line_number = body.count("\n", 0, return_start) + 1
                untraced_returns.append(line_number)

        self.assertEqual(
            [],
            untraced_returns,
            f"{FUNCTION_NAME} has `return;` statement(s) at body line(s) "
            f"{untraced_returns} with no logInteractionEvent(...) call earlier "
            "in the same enclosing block -- a dropped frame must never be "
            "silent.",
        )

    def test_function_has_at_least_one_return_to_guard(self):
        # Guards against the extraction regex silently matching nothing (e.g.
        # after an unrelated refactor renames or removes the early-out).
        source = MAIN_WINDOW_CPP.read_text(encoding="utf-8")
        body = _extract_function_body(source, FUNCTION_NAME)
        self.assertTrue(
            RETURN_PATTERN.search(body),
            f"{FUNCTION_NAME} no longer contains a `return;` -- "
            "re-check whether this tripwire is still needed.",
        )


if __name__ == "__main__":
    unittest.main()
