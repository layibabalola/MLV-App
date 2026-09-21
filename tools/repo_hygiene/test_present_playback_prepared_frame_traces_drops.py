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

Round 4 (sol r3 minor): round 3's "same enclosing block" check actually
scanned body[block_start:return_start] -- the ENTIRE text from the block's
opening brace to the return, which still includes any nested sibling block
that opened and closed earlier in that same span. A trace call sitting in an
earlier `if( x ) { logInteractionEvent(...); }` sibling therefore satisfied a
later, unrelated `return;` in the outer block, even though that trace does
not cover the return's actual code path. The scan is now restricted to text
written DIRECTLY at the return's own brace depth -- text inside any nested
`{...}` that closes before the return is excluded, whether that nested block
sits before or after other direct-level statements. Comments and (for brace
counting only) braces inside string/char literals are masked out first so
they cannot distort depth tracking or produce phantom matches; string
contents are otherwise left intact so the `draw_frame_ready.present_nothing`
marker can still be identified inside its QStringLiteral. The
`draw_frame_ready.present_nothing` exit specifically must also have
`m_presentNothingDropCount.fetch_add(` directly in its own block -- a trace
without the counter is no longer sufficient for that exit.

Round 5 (sol r4 minor): round 4's `direct_text` was built from the UNMASKED
`body`, so a `logInteractionEvent(` sitting only inside a `//`/`/* */`
comment, or a `m_presentNothingDropCount.fetch_add(` sitting only inside a
string/char literal, still satisfied the check. Each return now gets two
views of its own direct block: `direct_text` (comments blanked, string/char
*contents* left intact) and `direct_code` (comments blanked AND string/char
contents also blanked -- the CODE-ONLY view). `traced` and `counted` are
matched against `direct_code`, so a token that exists only inside a comment
or a string literal no longer counts. `is_present_nothing` is matched
against `direct_text`, since the `draw_frame_ready.present_nothing` marker
is itself a string literal and must stay visible for that match to work.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW_CPP = ROOT / "platform/qt/MainWindow.cpp"

FUNCTION_NAME = "presentPlaybackPreparedFrame"

RETURN_PATTERN = re.compile(r"\breturn\s*;")
TRACE_CALL = "logInteractionEvent("
DROP_COUNTER_CALL = "m_presentNothingDropCount.fetch_add("
PRESENT_NOTHING_MARKER = "draw_frame_ready.present_nothing"


def _mask_comments_and_string_braces(text: str) -> str:
    """Same-length copy of *text* with comment bodies blanked and braces
    inside string/char literals blanked, so neither can distort brace-depth
    tracking or produce phantom `return;` / `logInteractionEvent(` matches.
    Newlines are preserved everywhere so line numbers stay accurate. All
    other string-literal content (e.g. the present_nothing marker text) is
    left intact.
    """
    out = list(text)
    length = len(text)
    index = 0
    state = None  # None | "line_comment" | "block_comment" | "string" | "char"
    while index < length:
        char = text[index]
        if state is None:
            if char == "/" and index + 1 < length and text[index + 1] == "/":
                out[index] = " "
                out[index + 1] = " "
                state = "line_comment"
                index += 2
                continue
            if char == "/" and index + 1 < length and text[index + 1] == "*":
                out[index] = " "
                out[index + 1] = " "
                state = "block_comment"
                index += 2
                continue
            if char == '"':
                state = "string"
                index += 1
                continue
            if char == "'":
                state = "char"
                index += 1
                continue
            index += 1
            continue
        if state == "line_comment":
            if char == "\n":
                state = None
            else:
                out[index] = " "
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and index + 1 < length and text[index + 1] == "/":
                out[index] = " "
                out[index + 1] = " "
                state = None
                index += 2
                continue
            if char != "\n":
                out[index] = " "
            index += 1
            continue
        # state in ("string", "char")
        closing = '"' if state == "string" else "'"
        if char == "\\" and index + 1 < length:
            if text[index + 1] in "{}":
                out[index + 1] = " "
            index += 2
            continue
        if char in "{}":
            out[index] = " "
            index += 1
            continue
        if char == closing:
            state = None
        index += 1
    return "".join(out)


def _mask_all_literals(text: str) -> str:
    """Same-length CODE-ONLY view of *text*: comment bodies blanked (via
    `_mask_comments_and_string_braces`) AND the full contents of string/char
    literals also blanked, leaving only the quote characters and newlines
    behind. Unlike `_mask_comments_and_string_braces`, which leaves string
    contents intact so the `draw_frame_ready.present_nothing` marker stays
    visible, this view exists so a token (e.g. `logInteractionEvent(`) that
    appears only inside a string or char literal cannot match against it.
    """
    out = list(_mask_comments_and_string_braces(text))
    length = len(text)
    index = 0
    state = None  # None | "string" | "char"
    while index < length:
        char = text[index]
        if state is None:
            if char == '"':
                state = "string"
            elif char == "'":
                state = "char"
            index += 1
            continue
        closing = '"' if state == "string" else "'"
        if char == "\\" and index + 1 < length:
            if text[index + 1] != "\n":
                out[index + 1] = " "
            index += 2
            continue
        if char == closing:
            state = None
            index += 1
            continue
        if char != "\n":
            out[index] = " "
        index += 1
    return "".join(out)


def _extract_function_body(source: str, function_name: str) -> str:
    start_match = re.search(
        r"void MainWindow::" + re.escape(function_name) + r"\s*\([^)]*\)\s*\n\{\n",
        source,
    )
    assert start_match is not None, f"could not locate {function_name} definition"

    masked_source = _mask_comments_and_string_braces(source)
    body_start = start_match.end()
    depth = 1
    index = body_start
    while depth > 0:
        next_open = masked_source.find("{", index)
        next_close = masked_source.find("}", index)
        assert next_close != -1, f"unterminated body for {function_name}"
        if next_open != -1 and next_open < next_close:
            depth += 1
            index = next_open + 1
        else:
            depth -= 1
            index = next_close + 1
    body_end = index - 1
    return source[body_start:body_end]


def _find_returns_with_context(body: str):
    """Return a list of {"line", "direct_text", "direct_code"} for each
    `return;` in *body*.

    Both views cover text written DIRECTLY in the return's innermost
    enclosing block -- i.e. they exclude any nested `{...}` block that opens
    and closes before the return is reached, no matter where that nested
    block sits relative to other direct-level statements in the same
    enclosing block. `direct_text` has comments blanked but string/char
    contents left intact (so the present_nothing marker stays visible).
    `direct_code` additionally has string/char contents blanked -- the
    CODE-ONLY view, so a token sitting only inside a literal cannot match.
    """
    masked = _mask_comments_and_string_braces(body)
    code_masked = _mask_all_literals(body)
    frame_starts = [0]
    frame_segments = [[]]
    results = []
    index = 0
    length = len(masked)
    while index < length:
        char = masked[index]
        if char == "{":
            frame_segments[-1].append((frame_starts[-1], index))
            frame_starts.append(index + 1)
            frame_segments.append([])
            index += 1
            continue
        if char == "}":
            if len(frame_starts) > 1:
                frame_segments[-1].append((frame_starts[-1], index))
                frame_starts.pop()
                frame_segments.pop()
                frame_starts[-1] = index + 1
            index += 1
            continue
        match = RETURN_PATTERN.match(masked, index)
        if match:
            segments = frame_segments[-1] + [(frame_starts[-1], match.start())]
            direct_text = "".join(masked[s:e] for s, e in segments)
            direct_code = "".join(code_masked[s:e] for s, e in segments)
            line_number = body.count("\n", 0, match.start()) + 1
            results.append(
                {
                    "line": line_number,
                    "direct_text": direct_text,
                    "direct_code": direct_code,
                }
            )
            index = match.end()
            continue
        index += 1
    return results


def _classify_return(entry):
    direct_text = entry["direct_text"]
    direct_code = entry["direct_code"]
    traced = TRACE_CALL in direct_code
    is_present_nothing = PRESENT_NOTHING_MARKER in direct_text
    counted = DROP_COUNTER_CALL in direct_code
    ok = traced and (not is_present_nothing or counted)
    return ok, traced, is_present_nothing, counted


def _make_sample_body(
    *,
    trace_in_sibling: bool = False,
    trace_in_own_block: bool = True,
    include_counter: bool = True,
    extra_untraced_return: bool = False,
    trace_only_in_comment: bool = False,
    counter_only_in_string_literal: bool = False,
) -> str:
    """Synthetic stand-in for presentPlaybackPreparedFrame's present_nothing
    exit, shaped to exercise the tripwire in isolation from the rest of the
    real function. Used only in-memory by the mutation tests below -- never
    written to disk.
    """
    if trace_only_in_comment:
        own_log = (
            "        // logInteractionEvent( "
            'QStringLiteral("draw_frame_ready.present_nothing") );\n'
        )
    elif trace_in_own_block:
        own_log = (
            "        logInteractionEvent(\n"
            '            QStringLiteral("draw_frame_ready.present_nothing"),\n'
            '            QStringLiteral("serial=%1").arg( task.requestSerial ) );\n'
        )
    else:
        own_log = ""

    sibling_block = (
        "        if( diagnosticsEnabled )\n"
        "        {\n"
        "            logInteractionEvent(\n"
        '                QStringLiteral("draw_frame_ready.present_nothing"),\n'
        '                QStringLiteral("serial=%1").arg( task.requestSerial ) );\n'
        "        }\n"
        if trace_in_sibling
        else ""
    )

    if counter_only_in_string_literal:
        counter = (
            "        QStringLiteral( \"debug note: would call "
            'm_presentNothingDropCount.fetch_add( 1 )" );\n'
        )
    else:
        counter = (
            "        m_presentNothingDropCount.fetch_add( 1, std::memory_order_acq_rel );\n"
            if include_counter
            else ""
        )

    body = (
        "    if( !framePresentedByViewport && displayImage.isNull() )\n"
        "    {\n"
        + counter
        + sibling_block
        + own_log
        + "        if( m_pRenderThread )\n"
        "            m_pRenderThread->releasePresentedFrameForRequestSerial( task.requestSerial );\n"
        "        return;\n"
        "    }\n\n"
    )

    if extra_untraced_return:
        body += "    if( someOtherEarlyOutCondition )\n        return;\n\n"

    body += "    presentActualFrame( displayImage );\n"
    return body


class PresentPlaybackPreparedFrameTracesDropsTests(unittest.TestCase):
    def test_every_return_is_traced_within_its_own_block(self):
        source = MAIN_WINDOW_CPP.read_text(encoding="utf-8")
        body = _extract_function_body(source, FUNCTION_NAME)

        untraced_returns = []
        uncounted_present_nothing_returns = []
        for entry in _find_returns_with_context(body):
            ok, traced, is_present_nothing, counted = _classify_return(entry)
            if not traced:
                untraced_returns.append(entry["line"])
            elif is_present_nothing and not counted:
                uncounted_present_nothing_returns.append(entry["line"])

        self.assertEqual(
            [],
            untraced_returns,
            f"{FUNCTION_NAME} has `return;` statement(s) at body line(s) "
            f"{untraced_returns} with no logInteractionEvent(...) call earlier "
            "directly in the same enclosing block (a call inside an earlier "
            "nested sibling block does not count) -- a dropped frame must "
            "never be silent.",
        )
        self.assertEqual(
            [],
            uncounted_present_nothing_returns,
            f"{FUNCTION_NAME} has draw_frame_ready.present_nothing `return;` "
            f"statement(s) at body line(s) {uncounted_present_nothing_returns} "
            "with no m_presentNothingDropCount.fetch_add(...) call directly in "
            "the same enclosing block -- the drop counter must stay in lockstep "
            "with the trace.",
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


class TripwireMutationTests(unittest.TestCase):
    """In-memory mutations of a synthetic present_nothing exit, proving the
    tripwire logic itself (not just the current MainWindow.cpp contents)
    rejects the sol r3 false-positive and the missing-counter gap.
    """

    def _untraced_and_uncounted(self, body):
        untraced = []
        uncounted = []
        for entry in _find_returns_with_context(body):
            ok, traced, is_present_nothing, counted = _classify_return(entry)
            if not traced:
                untraced.append(entry["line"])
            elif is_present_nothing and not counted:
                uncounted.append(entry["line"])
        return untraced, uncounted

    def test_real_shaped_sample_passes(self):
        body = _make_sample_body()
        untraced, uncounted = self._untraced_and_uncounted(body)
        self.assertEqual([], untraced)
        self.assertEqual([], uncounted)

    def test_trace_in_earlier_nested_sibling_block_is_rejected(self):
        body = _make_sample_body(trace_in_sibling=True, trace_in_own_block=False)
        untraced, uncounted = self._untraced_and_uncounted(body)
        self.assertNotEqual(
            [],
            untraced,
            "a logInteractionEvent(...) call sitting only in an earlier nested "
            "sibling block must not satisfy a return in the outer block",
        )

    def test_missing_drop_counter_is_rejected(self):
        body = _make_sample_body(include_counter=False)
        untraced, uncounted = self._untraced_and_uncounted(body)
        self.assertEqual([], untraced)
        self.assertNotEqual(
            [],
            uncounted,
            "a draw_frame_ready.present_nothing exit missing "
            "m_presentNothingDropCount.fetch_add(...) must be flagged even "
            "when it is traced",
        )

    def test_untraced_conditional_return_is_rejected(self):
        body = _make_sample_body(extra_untraced_return=True)
        untraced, uncounted = self._untraced_and_uncounted(body)
        self.assertNotEqual(
            [],
            untraced,
            "an inline `if( x ) return;` with no trace in its own block must "
            "be flagged",
        )

    def test_trace_only_in_comment_is_rejected(self):
        body = _make_sample_body(trace_only_in_comment=True)
        untraced, uncounted = self._untraced_and_uncounted(body)
        self.assertNotEqual(
            [],
            untraced,
            "a logInteractionEvent(...) token sitting only inside a `//` "
            "comment in the return's own block must not count as a trace",
        )

    def test_counter_only_in_string_literal_is_rejected(self):
        body = _make_sample_body(counter_only_in_string_literal=True)
        untraced, uncounted = self._untraced_and_uncounted(body)
        self.assertEqual([], untraced)
        self.assertNotEqual(
            [],
            uncounted,
            "an m_presentNothingDropCount.fetch_add(...) token sitting only "
            "inside a string literal must not satisfy the drop-counter "
            "requirement",
        )


if __name__ == "__main__":
    unittest.main()
