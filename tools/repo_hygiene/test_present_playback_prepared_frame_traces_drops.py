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

Round 6 (sol r5 minor, structural): rounds 3-5 each found a precision hole in
the same two-pass shape -- `_mask_all_literals` started from the
comment-masked copy but drove its own quote-state machine over the ORIGINAL
unmasked text, so an unmatched quote or apostrophe sitting inside a comment
could desync that second scan and let a later string literal's contents
leak into `direct_code` unblanked (sol's concrete repro: a comment
containing an unmatched `"`, followed by a QStringLiteral holding only the
trace/counter/marker tokens, was classified as a real trace). Two separate
passes over two differently-masked texts is the root mechanism, so both
`_mask_comments_and_string_braces` and `_mask_all_literals` are replaced by
one `_lex(text)` function: a SINGLE state machine walks the ORIGINAL text
exactly once and produces both views together. A quote or apostrophe
encountered while already inside a comment can never change literal state,
and a comment opener encountered while already inside a literal can never
start a comment, because both are driven off the same one pass rather than
two passes racing over different inputs. `_lex` also understands raw string
literals (`R"delim( ... )delim"`, including a custom delimiter), which
neither predecessor did.
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

_CODE, _LINE_COMMENT, _BLOCK_COMMENT, _STRING, _CHAR, _RAW_STRING = range(6)
_RAW_DELIM_STOP = "()\\\t\n "
_RAW_DELIM_MAX_LEN = 16


def _is_ident_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _lex(text: str):
    """Single pass over *text* producing two same-length views.

    Returns ``(comment_masked, code_only)``:
      - ``comment_masked``: comment bodies blanked; string/char/raw-string
        CONTENTS are left intact, except braces inside them are blanked so
        they can never distort brace-depth tracking. Quote and comment
        delimiters stay visible.
      - ``code_only``: comment bodies blanked AND string/char/raw-string
        contents also blanked, leaving only the delimiting punctuation --
        so a token that exists only inside a literal cannot match against
        it.

    Handles line comments, block comments, string literals, char literals
    and raw string literals (`R"delim( ... )delim"`, including a custom
    delimiter), with backslash escapes honoured inside normal string/char
    literals (an escaped closing quote does not end the literal). Both
    views are driven off ONE state machine over the ORIGINAL text -- never
    a previously masked copy -- so a quote or apostrophe encountered while
    already inside a comment can never flip literal state, and a `//` or
    `/*` encountered while already inside a literal can never start a
    comment. Newlines are preserved verbatim in both views so line numbers
    stay accurate, and both views are exactly ``len(text)`` long.
    """
    length = len(text)
    comment_out = list(text)
    code_out = list(text)

    state = _CODE
    raw_terminator = ""
    index = 0
    while index < length:
        char = text[index]

        if state == _CODE:
            if char == "/" and index + 1 < length and text[index + 1] == "/":
                comment_out[index] = comment_out[index + 1] = " "
                code_out[index] = code_out[index + 1] = " "
                state = _LINE_COMMENT
                index += 2
                continue
            if char == "/" and index + 1 < length and text[index + 1] == "*":
                comment_out[index] = comment_out[index + 1] = " "
                code_out[index] = code_out[index + 1] = " "
                state = _BLOCK_COMMENT
                index += 2
                continue
            if (
                char == "R"
                and index + 1 < length
                and text[index + 1] == '"'
                and (index == 0 or not _is_ident_char(text[index - 1]))
            ):
                delim_start = index + 2
                delim_end = delim_start
                while (
                    delim_end < length
                    and delim_end - delim_start < _RAW_DELIM_MAX_LEN
                    and text[delim_end] not in _RAW_DELIM_STOP
                ):
                    delim_end += 1
                if delim_end < length and text[delim_end] == "(":
                    raw_terminator = ")" + text[delim_start:delim_end] + '"'
                    state = _RAW_STRING
                    index = delim_end + 1
                    continue
                # 'R"' that never reaches a delimiter-closing '(' is not a
                # raw string literal -- fall through and re-scan from here
                # as ordinary code, one character at a time.
                index += 1
                continue
            if char == '"':
                state = _STRING
                index += 1
                continue
            if char == "'":
                state = _CHAR
                index += 1
                continue
            index += 1
            continue

        if state == _LINE_COMMENT:
            # A quote or apostrophe here NEVER changes state -- only a
            # newline ends a line comment.
            if char == "\n":
                state = _CODE
            else:
                comment_out[index] = " "
                code_out[index] = " "
            index += 1
            continue

        if state == _BLOCK_COMMENT:
            # A quote or apostrophe here NEVER changes state, and comments
            # do not nest -- the first `*/` ends it even if `/*` appeared
            # again inside.
            if char == "*" and index + 1 < length and text[index + 1] == "/":
                comment_out[index] = comment_out[index + 1] = " "
                code_out[index] = code_out[index + 1] = " "
                state = _CODE
                index += 2
                continue
            if char != "\n":
                comment_out[index] = " "
                code_out[index] = " "
            index += 1
            continue

        if state == _RAW_STRING:
            # Raw strings do not process escapes or nested quoting -- only
            # the exact `)delim"` terminator ends them, so a `)"`, `//` or
            # `/*` that doesn't match the terminator is just content.
            if text[index : index + len(raw_terminator)] == raw_terminator:
                index += len(raw_terminator)
                state = _CODE
                continue
            if char != "\n":
                code_out[index] = " "
                if char in "{}":
                    comment_out[index] = " "
            index += 1
            continue

        # state in (_STRING, _CHAR): a comment opener here is just content,
        # it can never start a comment.
        closing = '"' if state == _STRING else "'"
        if char == "\\" and index + 1 < length:
            code_out[index] = " "
            escaped = text[index + 1]
            if escaped != "\n":
                code_out[index + 1] = " "
                if escaped in "{}":
                    comment_out[index + 1] = " "
            index += 2
            continue
        if char == closing:
            state = _CODE
            index += 1
            continue
        if char != "\n":
            code_out[index] = " "
            if char in "{}":
                comment_out[index] = " "
        index += 1

    return "".join(comment_out), "".join(code_out)


def _extract_function_body(source: str, function_name: str) -> str:
    start_match = re.search(
        r"void MainWindow::" + re.escape(function_name) + r"\s*\([^)]*\)\s*\n\{\n",
        source,
    )
    assert start_match is not None, f"could not locate {function_name} definition"

    masked_source, _ = _lex(source)
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
    masked, code_masked = _lex(body)
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
    comment_unmatched_quote_then_string_only_tokens: bool = False,
) -> str:
    """Synthetic stand-in for presentPlaybackPreparedFrame's present_nothing
    exit, shaped to exercise the tripwire in isolation from the rest of the
    real function. Used only in-memory by the mutation tests below -- never
    written to disk.
    """
    if comment_unmatched_quote_then_string_only_tokens:
        # sol r4/r5's exact repro shape: a comment holding an UNMATCHED
        # double quote, followed by a string literal whose CONTENTS are the
        # only place the trace/counter/marker tokens appear. Under the old
        # two-pass `_mask_all_literals`, the comment's stray quote desynced
        # the second scan's quote-state machine, so this string's contents
        # leaked into `direct_code` unblanked and looked like a real call.
        own_log = (
            '        // unmatched quote: "\n'
            '        QStringLiteral( "logInteractionEvent( '
            "draw_frame_ready.present_nothing ) "
            'm_presentNothingDropCount.fetch_add( 1 )" );\n'
        )
        sibling_block = ""
        counter = ""
    else:
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


class LexerDirectTests(unittest.TestCase):
    """Direct, table-driven tests of `_lex` in isolation from the tripwire
    logic above -- each case proves one precision requirement from the
    round 6 brief by asserting BOTH output views exactly.
    """

    def test_lexer_cases(self):
        cases = [
            (
                "quote inside a line comment",
                'int a; // say "hi"\nint b;\n',
                "int a; " + " " * 11 + "\nint b;\n",
                "int a; " + " " * 11 + "\nint b;\n",
            ),
            (
                "apostrophe inside a block comment",
                "int a; /* it's ok */ int b;\n",
                "int a; " + " " * 13 + " int b;\n",
                "int a; " + " " * 13 + " int b;\n",
            ),
            (
                "// inside a string",
                'x = "a//b";\n',
                'x = "a//b";\n',
                'x = "' + " " * 4 + '";\n',
            ),
            (
                "/* inside a string",
                'x = "a/*b*/c";\n',
                'x = "a/*b*/c";\n',
                'x = "' + " " * 7 + '";\n',
            ),
            (
                "escaped quote inside a string",
                'x = "a\\"b";\n',
                'x = "a\\"b";\n',
                'x = "' + " " * 4 + '";\n',
            ),
            (
                "char literal '\"'",
                "x = " + "'" + '"' + "'" + ";\n",
                "x = " + "'" + '"' + "'" + ";\n",
                "x = '" + " " + "';\n",
            ),
            (
                "char literal '\\''",
                "x = " + "'" + "\\" + "'" + "'" + ";\n",
                "x = " + "'" + "\\" + "'" + "'" + ";\n",
                "x = '" + " " * 2 + "';\n",
            ),
            (
                "raw string with )\", // and braces via a custom delimiter",
                'x = ' + 'R"XY(' + 'a)"b//c{d}e' + ')XY"' + ";\n",
                'x = ' + 'R"XY(' + 'a)"b//c d e' + ')XY"' + ";\n",
                'x = ' + 'R"XY(' + " " * 11 + ')XY"' + ";\n",
            ),
            (
                "brace inside a string",
                'x = "{}";\n',
                'x = "  ";\n',
                'x = "  ";\n',
            ),
            (
                "nested comment-looking text (comments do not nest)",
                "/* outer /* still comment */ after\n",
                " " * 28 + " after\n",
                " " * 28 + " after\n",
            ),
        ]
        for name, text, expected_comment_masked, expected_code_only in cases:
            with self.subTest(name=name):
                comment_masked, code_only = _lex(text)
                self.assertEqual(len(comment_masked), len(text))
                self.assertEqual(len(code_only), len(text))
                self.assertEqual(comment_masked, expected_comment_masked)
                self.assertEqual(code_only, expected_code_only)


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

    def test_unmatched_quote_in_earlier_comment_then_string_only_tokens_is_rejected(
        self,
    ):
        # sol r4/r5's exact repro: an unmatched quote inside a comment,
        # earlier in the block, followed by a string literal that is the
        # ONLY place the trace/counter/marker tokens appear. The old
        # two-pass `_mask_all_literals` desynced on the comment's stray
        # quote and let this string's contents leak into `direct_code`
        # unblanked, so it was wrongly accepted as a real trace+counter.
        body = _make_sample_body(
            comment_unmatched_quote_then_string_only_tokens=True
        )
        untraced, uncounted = self._untraced_and_uncounted(body)
        self.assertNotEqual(
            [],
            untraced,
            "logInteractionEvent(...) and m_presentNothingDropCount.fetch_add(...) "
            "tokens sitting only inside a string literal -- reached after an "
            "unmatched quote in an earlier comment -- must not satisfy the "
            "trace/counter requirement",
        )


if __name__ == "__main__":
    unittest.main()
