"""Source-level wiring guard for the playback-smoke source-frame identity
mechanism -- card PLAYBACK-IDENTITY-MAINWINDOW-WIRING-UNTESTABLE-1.

CUDA-ATTRIBUTION-BASELINE-1 round 6/7 (astra major 4, "the console tests
would still manually call the helpers and supply their own epochs"): every
behavioural test of PlaybackPresentedFrameIdentityTracker,
PlaybackFramePopulationPolicy and PlaybackDropFrameAdvancePolicy (in
tests/console/test_playback_frame_population_policy.cpp,
test_playback_drop_frame_advance_policy.cpp and
test_playback_lookahead_loop_position_policy.cpp) calls those GUI-free
types DIRECTLY and supplies its own (loop epoch, displayFrame) identities.
None of them link MainWindow.cpp, so none of them can prove that
MainWindow itself still calls these types, at the right place, with the
right live arguments -- deleting a production epoch capture or tracker
mutation call in MainWindow.cpp leaves every one of those tests green.
astra RULED (PR #155 round 6 review) that the round-6 proposal to extract
the call sites into a value-types free function does not fix this: the
tests would still call the free function directly with their own epochs.

This module is the required remedy's shape (b) from the round-8 brief: a
source-level census, parsing MainWindow.cpp and asserting each of the six
required call sites is present, in the correct function, in the correct
order relative to its neighbours, and supplied with the correct LIVE
argument expressions (not a hardcoded/wrong-boolean substitute) -- paired
with the existing behavioural console tests, which prove the callee types
compute the right thing GIVEN those arguments. Together they close the gap
astra identified: this module proves MainWindow supplies the arguments;
the console tests prove what happens to them.

Each checker below is unit-tested against an in-string fixture FIRST (so a
test failure means the checker caught a real defect, not that a regex
happened to match differently against the real file's incidental text),
then applied to the real MainWindow.cpp.

The six required call sites (round-8 brief, astra's enumeration plus round
7's two new ones):
  1. noteRequestedFrame(..., /*viaLookahead=*/true)  -- queuePlaybackLookaheadRequests()
  2. noteOfferedFrame(...)                            -- drawFrame(), before any reuse check
  3. noteRequestedFrame(..., /*viaLookahead=*/false) -- drawFrame(), after the reuse-branch return
  4. notePresentedFrame(...)                          -- notePlaybackSmokePresentedFrame()
  5. m_playbackSmokePresentedFrameIdentity.reset()    -- beginPlaybackSmokeTelemetry()
  6. PlaybackDropFrameAdvancePolicy::offeredAdvance(...) -- playbackHandling()
"""
from pathlib import Path
import re
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW_CPP = REPO_ROOT / "platform" / "qt" / "MainWindow.cpp"

TRACKER_MEMBER = "m_playbackSmokePresentedFrameIdentity"
NOTE_OFFERED_MARKER = TRACKER_MEMBER + ".noteOfferedFrame("
NOTE_REQUESTED_MARKER = TRACKER_MEMBER + ".noteRequestedFrame("
NOTE_PRESENTED_MARKER = TRACKER_MEMBER + ".notePresentedFrame("
RESET_MARKER = TRACKER_MEMBER + ".reset("
DROP_FRAME_POLICY_MARKER = "PlaybackDropFrameAdvancePolicy::offeredAdvance("
COMPUTE_SOURCE_POPULATION_MARKER = (
    "PlaybackFramePopulationPolicy::computeSourceFramePopulation("
)

QUEUE_LOOKAHEAD_SIGNATURE = "void MainWindow::queuePlaybackLookaheadRequests("
DRAW_FRAME_SIGNATURE = "void MainWindow::drawFrame( bool updateTimecodeLabel )"
NOTE_PRESENTED_FRAME_SIGNATURE = "void MainWindow::notePlaybackSmokePresentedFrame("
BEGIN_TELEMETRY_SIGNATURE = "void MainWindow::beginPlaybackSmokeTelemetry( void )"
PLAYBACK_HANDLING_SIGNATURE = "void MainWindow::playbackHandling(int timeDiff)"

REUSE_BRANCH_MARKER = "playbackLookaheadCoversCurrent"

# CUDA-ATTRIBUTION-BASELINE-1 round 9 (sol + astra MAJOR, "the census
# protects call presence, not the values that flow into the tracker"):
# exact-text statements for every VALUE SOURCE feeding the six call sites
# above -- the epoch producer, both epoch assignments, the lap
# accumulation, the wrap increments/reset, and the EOF-policy's exact
# arguments. A one-token mutation to any of these (e.g. replacing the RHS
# with a constant, or deleting the statement) leaves every test above
# green, because those tests only check that SOME live-looking expression
# is passed -- not that the expression is fed by the right upstream
# statement. sol's repro: `requestContext.playbackSmokeLoopEpoch = 0;`
# passes all 13 pre-round-9 tests. astra's repro: deleting
# `lookaheadContext.playbackSmokeLoopEpoch = lookaheadLoopEpoch;`, deleting
# `lookaheadLoopEpoch += wrapped.lapsAhead;`, removing either wrap
# increment, or replacing offeredAdvance()'s `ui->actionLoop->isChecked()`
# argument with the constant `true` all pass too.
LOOKAHEAD_EPOCH_SEED_STATEMENT = (
    "uint64_t lookaheadLoopEpoch = baseContext.playbackSmokeLoopEpoch;"
)
LOOKAHEAD_EPOCH_WRAP_ACCUMULATE_STATEMENT = (
    "lookaheadLoopEpoch += wrapped.lapsAhead;"
)
LOOKAHEAD_CONTEXT_EPOCH_ASSIGN_STATEMENT = (
    "lookaheadContext.playbackSmokeLoopEpoch = lookaheadLoopEpoch;"
)
REQUEST_CONTEXT_EPOCH_ASSIGN_STATEMENT = (
    "requestContext.playbackSmokeLoopEpoch = m_playbackSmokeLoopWrapCount;"
)
WRAP_COUNT_INCREMENT_STATEMENT = (
    "if( m_playbackSmokeActive ) ++m_playbackSmokeLoopWrapCount;"
)
WRAP_COUNT_RESET_STATEMENT = "m_playbackSmokeLoopWrapCount = 0;"
DROP_FRAME_ADVANCE_EXACT_ARGS = [
    "m_newPosDropMode",
    "dropFrameSourceFramesAdvanced",
    "ui->actionLoop->isChecked()",
    "ui->spinBoxCutOut->value() - 1",
]

NUMERIC_LITERAL_RE = re.compile(r"^-?\d+(\.\d+)?[fFuUlL]*$")


# --------------------------------------------------------------------- helpers
# Same shape as tools/repo_hygiene/test_playback_gate_wiring.py's helpers
# (that module is the established precedent for this kind of check).

def _extract_balanced(source, open_index, open_char, close_char):
    assert source[open_index] == open_char
    depth = 1
    i = open_index + 1
    while depth > 0:
        if source[i] == open_char:
            depth += 1
        elif source[i] == close_char:
            depth -= 1
        i += 1
    return source[open_index + 1:i - 1]


def _split_top_level_args(text):
    args = []
    depth = 0
    current = []
    for ch in text:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


def _function_body_span(source, signature_marker):
    """Return (start, end) offsets spanning the function whose definition
    begins at `signature_marker`, `end` just past its closing brace."""
    start = source.index(signature_marker)
    brace_open = source.index("{", start)
    depth = 1
    i = brace_open + 1
    while depth > 0:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    return start, i


def find_calls(source, marker):
    """Return a list of (call_start_offset, [args]) for every occurrence of
    `marker` (a call prefix ending in "(") in `source`."""
    results = []
    for m in re.finditer(re.escape(marker), source):
        call_start = m.start()
        open_paren = call_start + len(marker) - 1
        args_text = _extract_balanced(source, open_paren, "(", ")")
        results.append((call_start, _split_top_level_args(args_text)))
    return results


def call_within(span, call_start):
    return span[0] <= call_start < span[1]


def _strip_comments(source):
    """Replace // and /* */ comment bodies with spaces (newlines kept),
    so every marker/offset lookup below only ever matches real code.

    Several of the markers this module searches for (e.g.
    "noteRequestedFrame(" and "playbackLookaheadCoversCurrent") are also
    named in nearby doc comments explaining the mechanism -- see
    MainWindow.cpp's round-6/round-7 comment blocks, which literally say
    things like "m_playbackSmokePresentedFrameIdentity.noteRequestedFrame()
    below" and "the playbackLookaheadCoversCurrent branch further down".
    Without stripping, those comment mentions are indistinguishable from
    real call/declaration sites to a plain substring search. The output is
    the same length as `source` (comment characters become spaces, not
    removed) so every offset computed against it stays valid against the
    original text too.
    """
    out = []
    i = 0
    n = len(source)
    while i < n:
        two = source[i:i + 2]
        if two == "//":
            j = source.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = source.find("*/", i + 2)
            end = n if j == -1 else j + 2
            segment = source[i:end]
            out.append("".join(ch if ch == "\n" else " " for ch in segment))
            i = end
        else:
            out.append(source[i])
            i += 1
    return "".join(out)


# --------------------------------------------------------------- checker unit tests

class HelperCheckerFixtureTests(unittest.TestCase):
    """Prove the helpers themselves work, against small in-string fixtures,
    before trusting them against the real file (precedent:
    test_playback_gate_wiring.py's own fixture tests)."""

    def test_find_calls_locates_every_occurrence_and_splits_args(self):
        fixture = (
            "void MainWindow::a() { thing.reset(); }\n"
            "void MainWindow::b() { thing.noteRequestedFrame(x, y, true); "
            "thing.noteRequestedFrame(p, q, false); }\n"
        )
        calls = find_calls(fixture, "thing.noteRequestedFrame(")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], ["x", "y", "true"])
        self.assertEqual(calls[1][1], ["p", "q", "false"])

    def test_function_body_span_isolates_only_that_function(self):
        fixture = (
            "void MainWindow::a() { inside_a(); }\n"
            "void MainWindow::b() { inside_b(); }\n"
        )
        span_a = _function_body_span(fixture, "void MainWindow::a()")
        self.assertTrue(call_within(span_a, fixture.index("inside_a")))
        self.assertFalse(call_within(span_a, fixture.index("inside_b")))

    def test_strip_comments_blanks_out_a_marker_mentioned_only_in_prose(self):
        fixture = (
            "void f() {\n"
            "    /* calls thing.mark() below */\n"
            "    // thing.mark() again in a line comment\n"
            "    thing.mark(real, args);\n"
            "}\n"
        )
        stripped = _strip_comments(fixture)
        self.assertEqual(len(stripped), len(fixture))
        calls = find_calls(stripped, "thing.mark(")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], ["real", "args"])

    def test_numeric_literal_regex_flags_bare_constants_only(self):
        self.assertTrue(NUMERIC_LITERAL_RE.match("0"))
        self.assertTrue(NUMERIC_LITERAL_RE.match("60"))
        self.assertFalse(NUMERIC_LITERAL_RE.match("requestedFrame"))
        self.assertFalse(NUMERIC_LITERAL_RE.match("static_cast<uint64_t>( x )"))
        self.assertFalse(NUMERIC_LITERAL_RE.match("true"))
        self.assertFalse(NUMERIC_LITERAL_RE.match("false"))


# --------------------------------------------------------------- real-source tests

class MainWindowWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _strip_comments(MAIN_WINDOW_CPP.read_text(encoding="utf-8"))

    # ---- call site 1: lookahead noteRequestedFrame() -----------------------
    def test_lookahead_note_requested_frame_is_unique_inside_queue_lookahead_requests(self):
        calls = find_calls(self.source, NOTE_REQUESTED_MARKER)
        self.assertEqual(len(calls), 2, "expected exactly 2 noteRequestedFrame(...) call sites")
        span = _function_body_span(self.source, QUEUE_LOOKAHEAD_SIGNATURE)
        inside = [c for c in calls if call_within(span, c[0])]
        self.assertEqual(len(inside), 1,
                          "expected exactly 1 noteRequestedFrame(...) call inside "
                          "queuePlaybackLookaheadRequests()")
        args = inside[0][1]
        self.assertEqual(len(args), 3)
        self.assertNotRegex(args[0], NUMERIC_LITERAL_RE)
        self.assertNotRegex(args[1], NUMERIC_LITERAL_RE)
        # The `/*viaLookahead=*/` inline annotation comment is stripped along
        # with everything else _strip_comments() removes -- only the literal
        # boolean itself is semantically load-bearing here.
        self.assertEqual(args[2], "true",
                          "the lookahead request call must record viaLookahead=true")

    # ---- call site 2: noteOfferedFrame() ------------------------------------
    def test_note_offered_frame_is_unique_inside_draw_frame(self):
        calls = find_calls(self.source, NOTE_OFFERED_MARKER)
        self.assertEqual(len(calls), 1, "expected exactly 1 noteOfferedFrame(...) call site")
        span = _function_body_span(self.source, DRAW_FRAME_SIGNATURE)
        self.assertTrue(call_within(span, calls[0][0]),
                         "noteOfferedFrame(...) must be called from drawFrame()")
        args = calls[0][1]
        self.assertEqual(args, [
            "requestContext.playbackSmokeLoopEpoch",
            "static_cast<uint64_t>( requestedFrame )",
        ])

    def test_note_offered_frame_precedes_the_lookahead_reuse_check(self):
        # Round-7 requirement (PlaybackPresentedFrameIdentityTracker.h's
        # doc comment): the offered ceiling must be recorded BEFORE
        # drawFrame() decides whether this call reuses an existing
        # lookahead, so the ceiling reflects the position actually reached
        # regardless of how the request ends up satisfied.
        offered_calls = find_calls(self.source, NOTE_OFFERED_MARKER)
        self.assertEqual(len(offered_calls), 1)
        span = _function_body_span(self.source, DRAW_FRAME_SIGNATURE)
        reuse_offset = self.source.index(REUSE_BRANCH_MARKER, span[0], span[1])
        self.assertLess(
            offered_calls[0][0], reuse_offset,
            "noteOfferedFrame(...) must be called before the "
            "playbackLookaheadCoversCurrent reuse check in drawFrame()")

    # ---- call site 3: target noteRequestedFrame() ---------------------------
    def test_target_note_requested_frame_is_inside_draw_frame_after_reuse_branch(self):
        calls = find_calls(self.source, NOTE_REQUESTED_MARKER)
        self.assertEqual(len(calls), 2)
        span = _function_body_span(self.source, DRAW_FRAME_SIGNATURE)
        inside = [c for c in calls if call_within(span, c[0])]
        self.assertEqual(len(inside), 1,
                          "expected exactly 1 noteRequestedFrame(...) call inside drawFrame()")
        args = inside[0][1]
        self.assertEqual(len(args), 3)
        self.assertEqual(args[0], "requestContext.playbackSmokeLoopEpoch")
        self.assertEqual(args[1], "static_cast<uint64_t>( requestedFrame )")
        self.assertEqual(args[2], "false",
                          "the genuine target request call must record viaLookahead=false")

        # It must sit AFTER the reuse branch's own early return: a request
        # satisfied by an existing lookahead must never reach this call
        # (round 6 sol + astra major, "the call site that would record a
        # genuine target request is simply never reached for a reuse
        # attempt").
        reuse_offset = self.source.index(REUSE_BRANCH_MARKER, span[0], span[1])
        reuse_return_offset = self.source.index("return;", reuse_offset, span[1])
        self.assertGreater(
            inside[0][0], reuse_return_offset,
            "the genuine-target noteRequestedFrame(...) call must be positioned "
            "after the lookahead-reuse branch's early return")

    # ---- call site 4: notePresentedFrame() ----------------------------------
    def test_note_presented_frame_is_unique_inside_note_playback_smoke_presented_frame(self):
        calls = find_calls(self.source, NOTE_PRESENTED_MARKER)
        self.assertEqual(len(calls), 1, "expected exactly 1 notePresentedFrame(...) call site")
        span = _function_body_span(self.source, NOTE_PRESENTED_FRAME_SIGNATURE)
        self.assertTrue(call_within(span, calls[0][0]),
                         "notePresentedFrame(...) must be called from "
                         "notePlaybackSmokePresentedFrame()")
        args = calls[0][1]
        self.assertEqual(args, [
            "requestContext.playbackSmokeLoopEpoch",
            "displayFrame",
            "requestContext.playbackLookaheadRequest",
        ], "notePresentedFrame(...) must read the live origin flag, not a "
           "hardcoded true/false")

    # ---- call site 5: identity tracker reset() ------------------------------
    def test_identity_tracker_reset_is_unique_inside_begin_playback_smoke_telemetry(self):
        calls = find_calls(self.source, RESET_MARKER)
        self.assertEqual(len(calls), 1,
                          "expected exactly 1 %s call" % RESET_MARKER)
        span = _function_body_span(self.source, BEGIN_TELEMETRY_SIGNATURE)
        self.assertTrue(call_within(span, calls[0][0]),
                         "%s must be called from beginPlaybackSmokeTelemetry(), "
                         "otherwise a stale tracker leaks occurrences across "
                         "sessions" % RESET_MARKER)
        self.assertEqual(calls[0][1], [])

    # ---- call site 6: PlaybackDropFrameAdvancePolicy::offeredAdvance() -----
    def test_drop_frame_advance_policy_call_is_inside_playback_handling(self):
        calls = find_calls(self.source, DROP_FRAME_POLICY_MARKER)
        self.assertEqual(len(calls), 1,
                          "expected exactly 1 %s call" % DROP_FRAME_POLICY_MARKER)
        span = _function_body_span(self.source, PLAYBACK_HANDLING_SIGNATURE)
        self.assertTrue(call_within(span, calls[0][0]),
                         "%s must be called from playbackHandling()"
                         % DROP_FRAME_POLICY_MARKER)
        args = calls[0][1]
        self.assertEqual(len(args), 4)
        for arg in args:
            self.assertNotRegex(
                arg, NUMERIC_LITERAL_RE,
                "offeredAdvance(...) argument %r must be a live expression, "
                "not a hardcoded constant" % arg)
        # round 9 (sol + astra MAJOR): the per-arg numeric-literal check
        # above does not catch a live-looking BOOLEAN constant substituted
        # for the loop-checked argument -- replacing
        # ui->actionLoop->isChecked() with the bare token `true` passed the
        # round-8 census. Pin every argument to its exact required
        # expression so that mutation is caught too.
        self.assertEqual(
            args, DROP_FRAME_ADVANCE_EXACT_ARGS,
            "offeredAdvance(...) must be called with the exact required "
            "live arguments, not a constant substitute for any of them")

    def test_drop_frame_advance_policy_result_feeds_the_offered_accumulator(self):
        # The call's return value must be added into
        # m_playbackTimelineSourceFramesOffered -- the exact accumulator
        # every other offered-frame call site in this file feeds (round 3's
        # normal-mode +=1.0 above it, and the read side at
        # PlaybackFramePopulationPolicy::computeSourceFramePopulation()'s
        # first argument below).
        marker_offset = self.source.index(DROP_FRAME_POLICY_MARKER)
        preceding = self.source[max(0, marker_offset - 80):marker_offset]
        self.assertIn("m_playbackTimelineSourceFramesOffered +=", preceding)

    # ---- bonus: the read side is wired too ----------------------------------
    def test_compute_source_frame_population_reads_all_identity_accessors_live(self):
        # Closes the loop the other five checks open: MainWindow must also
        # READ the tracker through its own accessors (not a hardcoded
        # figure) when it finally computes the reported partition, and must
        # pass the live telemetry-enabled flag (round 7's "unmeasured"
        # third state depends on this exact argument being live, not a
        # hardcoded true).
        calls = find_calls(self.source, COMPUTE_SOURCE_POPULATION_MARKER)
        self.assertEqual(len(calls), 1)
        args = calls[0][1]
        self.assertEqual(len(args), 9)
        self.assertEqual(args[2], TRACKER_MEMBER + ".requestedOccurrenceUnionCount()")
        self.assertEqual(args[3], TRACKER_MEMBER + ".distinctTargetPresentedCount()")
        self.assertEqual(args[4], TRACKER_MEMBER + ".distinctLookaheadPresentedCount()")
        self.assertEqual(args[5], TRACKER_MEMBER + ".presentedOccurrenceUnionCount()")
        self.assertEqual(args[6], TRACKER_MEMBER + ".requestedThenSkippedTargetCount()")
        self.assertEqual(args[7], TRACKER_MEMBER + ".requestedThenDiscardedLookaheadCount()")
        self.assertEqual(args[8], "m_playbackSmokeFrameTelemetry",
                          "the telemetry-measured flag must be the live session "
                          "flag, not a hardcoded true -- see round 7's unmeasured "
                          "third-state fix")

    # ---- round 9: value sources feeding the epoch, not just its plumbing ---
    # sol + astra MAJOR: the round-8 census proved every call site receives
    # SOME live-looking expression, but never traced that expression back to
    # its producer. These tests pin the exact upstream statements a mutation
    # would have to survive; each one is independently mutation-tested in
    # the round-9 summary's mutation table.

    def test_request_context_epoch_is_assigned_from_the_live_wrap_counter(self):
        self.assertEqual(
            self.source.count(REQUEST_CONTEXT_EPOCH_ASSIGN_STATEMENT), 1,
            "expected exactly 1 occurrence of the exact statement assigning "
            "requestContext.playbackSmokeLoopEpoch from the live wrap "
            "counter -- replacing the right-hand side with a constant (e.g. "
            "0) must fail this test")
        span = _function_body_span(self.source, DRAW_FRAME_SIGNATURE)
        offset = self.source.index(REQUEST_CONTEXT_EPOCH_ASSIGN_STATEMENT)
        self.assertTrue(call_within(span, offset),
                         "the epoch assignment must be inside drawFrame()")

    def test_lookahead_epoch_is_seeded_from_the_live_base_context_epoch(self):
        self.assertEqual(
            self.source.count(LOOKAHEAD_EPOCH_SEED_STATEMENT), 1,
            "expected exactly 1 occurrence of the exact statement seeding "
            "lookaheadLoopEpoch from baseContext.playbackSmokeLoopEpoch")
        span = _function_body_span(self.source, QUEUE_LOOKAHEAD_SIGNATURE)
        offset = self.source.index(LOOKAHEAD_EPOCH_SEED_STATEMENT)
        self.assertTrue(call_within(span, offset),
                         "the lookahead epoch seed must be inside "
                         "queuePlaybackLookaheadRequests()")

    def test_lookahead_epoch_accumulates_laps_ahead_on_wrap(self):
        self.assertEqual(
            self.source.count(LOOKAHEAD_EPOCH_WRAP_ACCUMULATE_STATEMENT), 1,
            "expected exactly 1 occurrence of the exact statement adding "
            "wrapped.lapsAhead into lookaheadLoopEpoch -- deleting this "
            "statement silently drops the wrap credit for a lookahead that "
            "crosses cut-out into a later lap")
        span = _function_body_span(self.source, QUEUE_LOOKAHEAD_SIGNATURE)
        offset = self.source.index(LOOKAHEAD_EPOCH_WRAP_ACCUMULATE_STATEMENT)
        self.assertTrue(call_within(span, offset),
                         "the lap accumulation must be inside "
                         "queuePlaybackLookaheadRequests()")

    def test_lookahead_context_epoch_is_assigned_from_the_accumulated_local(self):
        self.assertEqual(
            self.source.count(LOOKAHEAD_CONTEXT_EPOCH_ASSIGN_STATEMENT), 1,
            "expected exactly 1 occurrence of the exact statement copying "
            "the accumulated lookaheadLoopEpoch into "
            "lookaheadContext.playbackSmokeLoopEpoch -- without it, "
            "lookaheadContext keeps baseContext's un-wrapped epoch (it is "
            "copy-constructed from baseContext just above), silently "
            "losing the wrap credit for every downstream reader of "
            "requestContext.playbackSmokeLoopEpoch on that request")
        span = _function_body_span(self.source, QUEUE_LOOKAHEAD_SIGNATURE)
        offset = self.source.index(LOOKAHEAD_CONTEXT_EPOCH_ASSIGN_STATEMENT)
        self.assertTrue(call_within(span, offset),
                         "the lookahead context epoch assignment must be "
                         "inside queuePlaybackLookaheadRequests()")
        # It must happen AFTER the accumulation, so it copies the wrapped
        # value rather than the pre-wrap seed.
        seed_offset = self.source.index(LOOKAHEAD_EPOCH_WRAP_ACCUMULATE_STATEMENT)
        self.assertGreater(
            offset, seed_offset,
            "lookaheadContext.playbackSmokeLoopEpoch must be assigned AFTER "
            "the lap accumulation, not before it")

    def test_wrap_counter_is_incremented_exactly_twice_guarded_inside_playback_handling(self):
        matches = [
            m.start() for m in re.finditer(
                re.escape(WRAP_COUNT_INCREMENT_STATEMENT), self.source)
        ]
        self.assertEqual(
            len(matches), 2,
            "expected exactly 2 occurrences of the guarded wrap-count "
            "increment (normal-mode loop wrap and drop-frame-mode loop "
            "wrap) -- deleting either one leaves that mode's laps "
            "collapsed onto a stale epoch")
        span = _function_body_span(self.source, PLAYBACK_HANDLING_SIGNATURE)
        for offset in matches:
            self.assertTrue(call_within(span, offset),
                             "each wrap-count increment must be inside "
                             "playbackHandling()")

    def test_wrap_counter_is_reset_inside_begin_playback_smoke_telemetry(self):
        self.assertEqual(
            self.source.count(WRAP_COUNT_RESET_STATEMENT), 1,
            "expected exactly 1 occurrence of the exact statement "
            "resetting m_playbackSmokeLoopWrapCount to 0 -- without it a "
            "stale wrap count from a previous session leaks into the next "
            "session's epochs, exactly the cross-session leak the sibling "
            "identity-tracker reset (tested above) already guards against")
        span = _function_body_span(self.source, BEGIN_TELEMETRY_SIGNATURE)
        offset = self.source.index(WRAP_COUNT_RESET_STATEMENT)
        self.assertTrue(call_within(span, offset),
                         "the wrap-count reset must be inside "
                         "beginPlaybackSmokeTelemetry()")


if __name__ == "__main__":
    unittest.main()
