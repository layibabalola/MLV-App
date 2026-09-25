"""Source-level wiring guard for the manifest/timing-writer mechanisms the
round-11 census gap named -- CUDA-ATTRIBUTION-BASELINE-1 round 11 (sol
MAJOR, fable minor 2): "Load-bearing mechanisms with only helper tests or
source review." Each row below pins the value-determining production
statement to its exact text AND to the function it must execute inside, so
a hardcoded constant, a swapped field, or a moved call site all fail this
module even though the unit-tested policy classes it wraps
(GpuTexturePresentAvailabilityPolicy, PlaybackAchievedScalePolicy) stay
green -- the same "helper vs. call site" gap
test_playback_identity_mainwindow_wiring.py already closes for the drop-
frame/identity mechanisms.

Mechanisms covered (sol round-11 finding, MainWindow.cpp:4680-4754,6531-
6546,25079-25197; GpuDisplayViewport.cpp:1713-1735; RenderFrameThread.cpp:
4764-4784):
  1. Clock-probe resolution sample write (the "disclosed" row 13 sol ruled
     load-bearing) and its two downstream emissions.
  2. requestContext.playbackScaleFactor / playbackQualityMode capture and
     the render_manifest fields sourced from them (requested/effective/
     achieved scale, quality_mode, phase3_mode).
  3. recon_available / amaze_available three-state writers on both
     presentation paths (GpuDisplayViewport.cpp, GpuDisplayWindow.cpp).
  4. The CPU-debayer non-execution marker
     (render_thread_cpu_amaze_debayer_skipped_for_gpu_tex_nr).
  5. Achieved-scale classification's production call and result field.
  6. phase3_mode reset (FrameSlot::resetMetadata) and replace (guarded by
     phase3Active, in both renderDecodedSlot and runSerial).

Same shape as test_playback_identity_mainwindow_wiring.py /
test_playback_gate_wiring.py (the established precedent for this kind of
check): parse the real source, assert exact statement text, and require it
to sit inside the correct enclosing function.
"""
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW_CPP = REPO_ROOT / "platform" / "qt" / "MainWindow.cpp"
GPU_DISPLAY_VIEWPORT_CPP = REPO_ROOT / "platform" / "qt" / "GpuDisplayViewport.cpp"
GPU_DISPLAY_WINDOW_CPP = REPO_ROOT / "platform" / "qt" / "GpuDisplayWindow.cpp"
RENDER_FRAME_THREAD_CPP = REPO_ROOT / "platform" / "qt" / "RenderFrameThread.cpp"
RENDER_FRAME_THREAD_H = REPO_ROOT / "platform" / "qt" / "RenderFrameThread.h"


# --------------------------------------------------------------------- helpers
# Duplicated (not imported) from test_playback_identity_mainwindow_wiring.py /
# test_playback_gate_wiring.py -- established precedent for this family of
# source-level wiring checks.

def _strip_comments(source):
    """Replace // and /* */ comment bodies with spaces (newlines kept), so a
    marker mentioned only in a doc comment can never satisfy a check meant
    to prove real code."""
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


def _assert_statement_in_function(test, source, signature_marker, statement, msg):
    span = _function_body_span(source, signature_marker)
    body = source[span[0]:span[1]]
    test.assertIn(statement, body, msg)


# --------------------------------------------------------------------- fixtures

class HelperFixtureTests(unittest.TestCase):
    def test_function_body_span_isolates_only_that_function(self):
        fixture = (
            "void A::a() { inside_a(); }\n"
            "void A::b() { inside_b(); }\n"
        )
        span_a = _function_body_span(fixture, "void A::a()")
        self.assertIn("inside_a", fixture[span_a[0]:span_a[1]])
        self.assertNotIn("inside_b", fixture[span_a[0]:span_a[1]])

    def test_strip_comments_blinds_doc_comment_mentions(self):
        fixture = "// thing.doIt(); is documented here\nthing.doIt();\n"
        stripped = _strip_comments(fixture)
        self.assertEqual(stripped.count("doIt()"), 1)


# --------------------------------------------------------------------- census

class ManifestTimingWritersWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main_window = _strip_comments(MAIN_WINDOW_CPP.read_text(encoding="utf-8"))
        cls.gpu_viewport = _strip_comments(GPU_DISPLAY_VIEWPORT_CPP.read_text(encoding="utf-8"))
        cls.gpu_window = _strip_comments(GPU_DISPLAY_WINDOW_CPP.read_text(encoding="utf-8"))
        cls.render_thread = _strip_comments(RENDER_FRAME_THREAD_CPP.read_text(encoding="utf-8"))
        cls.render_thread_h = _strip_comments(RENDER_FRAME_THREAD_H.read_text(encoding="utf-8"))

    # -- 1. clock-probe resolution sample (sol round-11: "the disclosed clock
    # probe has no disabling-mutation test") --------------------------------

    def test_clock_probe_resolution_sample_is_written_in_present_playback_prepared_frame(self):
        _assert_statement_in_function(
            self,
            self.main_window,
            "void MainWindow::presentPlaybackPreparedFrame( const PlaybackPrepResult &result )",
            "prepRegionClockResolutionNs = nowNs - prepRegionClockLastNs;",
            "The clock-probe granularity sample write is missing or was moved out of "
            "presentPlaybackPreparedFrame -- repro: replace the RHS with a constant "
            "(e.g. `= 1;`).",
        )

    def test_clock_probe_resolution_and_monotonic_are_emitted_to_stage_timing_telemetry(self):
        span = _function_body_span(
            self.main_window,
            "void MainWindow::presentPlaybackPreparedFrame( const PlaybackPrepResult &result )",
        )
        body = self.main_window[span[0]:span[1]]
        self.assertIn(
            'readyFrame.stageTimingTelemetry.insert(\n'
            '        QStringLiteral("playback_prep_region_clock_resolution_ns"),\n'
            '        prepRegionClockResolutionNs );',
            body,
            "prepRegionClockResolutionNs is no longer inserted into "
            "stageTimingTelemetry under its live-computed value.",
        )

    def test_clock_probe_resolution_is_re_emitted_in_timing_validity(self):
        _assert_statement_in_function(
            self,
            self.main_window,
            "void MainWindow::notePlaybackSmokePresentedFrame(",
            '<< QStringLiteral("prep_region_clock_resolution_ns=%1").arg(\n'
            '                   telemetryDoubleValue(\n'
            '                       timing, "playback_prep_region_clock_resolution_ns" ), 0, \'f\', 0 )',
            "playback_smoke.timing_validity no longer re-emits "
            "prep_region_clock_resolution_ns read back from the same key the probe wrote.",
        )

    # -- 2. scale/quality capture and render_manifest emission (sol round-11:
    # "changing requestContext.playbackQualityMode to a constant or emitting
    # effective scale as requested scale survives the committed tests") -----

    def test_playback_scale_factor_is_captured_from_the_live_effective_scale_helper(self):
        _assert_statement_in_function(
            self,
            self.main_window,
            "void MainWindow::drawFrame( bool updateTimecodeLabel )",
            "requestContext.playbackScaleFactor = effectivePlaybackScaleFactorForRequest();",
            "requestContext.playbackScaleFactor is no longer captured from "
            "effectivePlaybackScaleFactorForRequest() inside drawFrame.",
        )

    def test_playback_quality_mode_is_captured_from_the_live_member(self):
        _assert_statement_in_function(
            self,
            self.main_window,
            "void MainWindow::drawFrame( bool updateTimecodeLabel )",
            "requestContext.playbackQualityMode = m_playbackQualityMode;",
            "requestContext.playbackQualityMode is no longer captured from "
            "m_playbackQualityMode inside drawFrame -- repro: hardcode to a constant.",
        )

    def test_render_manifest_scale_and_mode_fields_read_the_captured_request_context(self):
        span = _function_body_span(
            self.main_window, "void MainWindow::notePlaybackSmokePresentedFrame("
        )
        body = self.main_window[span[0]:span[1]]
        for statement, label in (
            (
                '<< QStringLiteral("requested_scale=%1").arg(\n'
                '                   requestContext.playbackScaleFactorRequestedBeforeGpuTextureRouteClamp )',
                "requested_scale",
            ),
            (
                '<< QStringLiteral("effective_scale=%1").arg( requestContext.playbackScaleFactor )',
                "effective_scale",
            ),
            (
                '<< QStringLiteral("achieved_scale=%1").arg( readyFrame.playbackScaleFactorActive )',
                "achieved_scale",
            ),
            (
                '<< QStringLiteral("quality_mode=%1").arg(\n'
                '                   requestContext.playbackQualityMode )',
                "quality_mode",
            ),
            (
                '<< QStringLiteral("phase3_mode=%1").arg(\n'
                '                   static_cast<int>( readyFrame.phase3Mode ) )',
                "phase3_mode",
            ),
        ):
            with self.subTest(field=label):
                self.assertIn(
                    statement,
                    body,
                    f"render_manifest's {label} field no longer sources the live value "
                    "it is documented to -- repro: swap it for a different requestContext/"
                    "readyFrame field (e.g. emit effective scale as requested scale).",
                )

    # -- 3. recon/amaze three-state availability writers (fable round-10:
    # "no row for the question-3 three-state timing writers") ---------------

    def test_gpu_display_viewport_writes_recon_and_amaze_availability_from_live_timing(self):
        _assert_statement_in_function(
            self,
            self.gpu_viewport,
            "GpuDisplayViewport::setPresentedGpuPlaybackReconAmazePostWbTexture(",
            "timing->recon_available = reconTiming.available ? 1 : 0;",
            "GpuDisplayViewport's recon_available writer no longer reads reconTiming.available.",
        )
        _assert_statement_in_function(
            self,
            self.gpu_viewport,
            "GpuDisplayViewport::setPresentedGpuPlaybackReconAmazePostWbTexture(",
            "timing->amaze_available = amazeTiming.available ? 1 : 0;",
            "GpuDisplayViewport's amaze_available writer no longer reads amazeTiming.available.",
        )

    def test_gpu_display_window_writes_recon_and_amaze_availability_from_live_timing(self):
        _assert_statement_in_function(
            self,
            self.gpu_window,
            "GpuDisplayWindow::setPresentedGpuPlaybackReconAmazePostWbTexture(",
            "timing->recon_available = reconTiming.available ? 1 : 0;",
            "GpuDisplayWindow's recon_available writer no longer reads reconTiming.available.",
        )
        _assert_statement_in_function(
            self,
            self.gpu_window,
            "GpuDisplayWindow::setPresentedGpuPlaybackReconAmazePostWbTexture(",
            "timing->amaze_available = amazeTiming.available ? 1 : 0;",
            "GpuDisplayWindow's amaze_available writer no longer reads amazeTiming.available.",
        )

    # -- 4. CPU-debayer non-execution marking --------------------------------

    def test_cpu_amaze_debayer_skip_marker_is_written_true_on_the_no_readback_route(self):
        _assert_statement_in_function(
            self,
            self.render_thread,
            "void RenderFrameThread::drawFrame( int slotIndex,",
            'slot.stageTimingTelemetry.insert(\n'
            '            QStringLiteral("render_thread_cpu_amaze_debayer_skipped_for_gpu_tex_nr"),\n'
            '            true );',
            "The no-readback GPU AMaZE route no longer marks "
            "render_thread_cpu_amaze_debayer_skipped_for_gpu_tex_nr true -- a downstream "
            "consumer would then misread this frame's zeroed CPU-debayer fields as a true "
            "zero-cost measurement instead of non-execution.",
        )

    # -- 5. achieved-scale classification (sol round-11: timing writers "are
    # tested only through pure helper policies, not their production call
    # sites") ----------------------------------------------------------------

    def test_achieved_scale_policy_is_called_with_the_live_route_and_core_state(self):
        _assert_statement_in_function(
            self,
            self.render_thread,
            "void RenderFrameThread::drawFrame( int slotIndex,",
            "? PlaybackAchievedScalePolicy::achievedScaleFactor(\n"
            "                  achievedScaleRoute,\n"
            "                  playbackScaleFactor,\n"
            "                  coreActiveScale,\n"
            "                  coreActiveScaleValid )",
            "PlaybackAchievedScalePolicy::achievedScaleFactor is no longer called with "
            "the live route/scale/core-state quadruple.",
        )
        _assert_statement_in_function(
            self,
            self.render_thread,
            "void RenderFrameThread::drawFrame( int slotIndex,",
            "slot.playbackScaleFactorActive = playbackScaleFactorActive;",
            "slot.playbackScaleFactorActive is no longer fed by the policy's classification "
            "result -- repro: hardcode it to 1.",
        )

    # -- 6. phase3_mode reset/replace (fable round-10: "the achieved-side...
    # phase3_mode reset/replace") --------------------------------------------

    def test_phase3_mode_replace_after_render_decoded_slot(self):
        _assert_statement_in_function(
            self,
            self.render_thread,
            "void RenderFrameThread::renderDecodedSlot( int slotIndex,",
            "m_frameSlots[slotIndex].phase3Mode = activePhase3Mode;",
            "renderDecodedSlot no longer replaces the slot's reset phase3Mode with the "
            "per-frame activePhase3Mode result.",
        )

    def test_phase3_mode_replace_after_run_serial(self):
        _assert_statement_in_function(
            self,
            self.render_thread,
            "void RenderFrameThread::runSerial(void)",
            "m_frameSlots[slotIndex].phase3Mode = activePhase3Mode;",
            "runSerial no longer replaces the slot's reset phase3Mode with the per-frame "
            "activePhase3Mode result.",
        )

    # -- 7. round-12 named gaps (sol MAJOR: "the census still omits load-
    # bearing capture/reset/writer statements") --------------------------

    def test_pre_clamp_requested_scale_capture_predicate(self):
        # This is the statement render_manifest's requested_scale field
        # reads back (test 2 above pins the READ; this pins the WRITE). A
        # hardcoded predicate (e.g. always false) would make requested_scale
        # collapse to effective_scale on every clamped frame, and no
        # existing test would notice.
        _assert_statement_in_function(
            self,
            self.main_window,
            "void MainWindow::drawFrame( bool updateTimecodeLabel )",
            "requestContext.playbackScaleFactorRequestedBeforeGpuTextureRouteClamp =\n"
            "        m_playbackScaleClampedForGpuTextureRouteActive\n"
            "            ? m_playbackScaleClampedForGpuTextureRouteRequestedScale\n"
            "            : requestContext.playbackScaleFactor;",
            "The pre-clamp requested-scale capture predicate in drawFrame no longer "
            "distinguishes a clamped request from an unclamped one -- repro: force the "
            "predicate to a constant so requested_scale always equals effective_scale.",
        )

    def test_scale_clamped_for_gpu_texture_route_is_emitted_from_the_two_captured_scales(self):
        _assert_statement_in_function(
            self,
            self.main_window,
            "void MainWindow::notePlaybackSmokePresentedFrame(",
            '<< QStringLiteral("scale_clamped_for_gpu_texture_route=%1").arg(\n'
            "                   bool01( requestContext.playbackScaleFactorRequestedBeforeGpuTextureRouteClamp\n"
            "                           != requestContext.playbackScaleFactor ) )",
            "render_manifest's scale_clamped_for_gpu_texture_route field no longer "
            "compares the pre-clamp and post-clamp captured scales -- repro: hardcode "
            "its argument to 0.",
        )

    def test_frame_slot_reset_metadata_clears_stale_phase3_mode(self):
        _assert_statement_in_function(
            self,
            self.render_thread_h,
            "void resetMetadata( void )",
            "phase3Mode = Phase3Mode::Disabled;",
            "FrameSlot::resetMetadata no longer clears phase3Mode -- a reused slot could "
            "then carry a stale phase3_mode from a previous frame into "
            "renderDecodedSlot/runSerial's replace-only-if-guarded logic.",
        )

    def test_gpu_display_viewport_writes_combined_availability_from_both_components(self):
        _assert_statement_in_function(
            self,
            self.gpu_viewport,
            "GpuDisplayViewport::setPresentedGpuPlaybackReconAmazePostWbTexture(",
            "timing->available = reconTiming.available || amazeTiming.available;",
            "GpuDisplayViewport's combined availability writer no longer ORs the two "
            "component flags -- repro: hardcode it to false so texture_present_available "
            "reads 0 even when a component measured.",
        )

    def test_gpu_display_window_writes_combined_availability_from_both_components(self):
        _assert_statement_in_function(
            self,
            self.gpu_window,
            "GpuDisplayWindow::setPresentedGpuPlaybackReconAmazePostWbTexture(",
            "timing->available = reconTiming.available || amazeTiming.available;",
            "GpuDisplayWindow's combined availability writer no longer ORs the two "
            "component flags -- repro: hardcode it to false so texture_present_available "
            "reads 0 even when a component measured.",
        )

    def test_prep_region_clock_monotonic_is_written_and_re_emitted(self):
        _assert_statement_in_function(
            self,
            self.main_window,
            "void MainWindow::presentPlaybackPreparedFrame( const PlaybackPrepResult &result )",
            'readyFrame.stageTimingTelemetry.insert(\n'
            '        QStringLiteral("playback_prep_region_clock_monotonic"),\n'
            '        prepRegionClock.isMonotonic() );',
            "The clock-monotonic sample write is missing or was moved out of "
            "presentPlaybackPreparedFrame -- repro: replace the RHS with a constant "
            "(e.g. `= true;`).",
        )
        _assert_statement_in_function(
            self,
            self.main_window,
            "void MainWindow::notePlaybackSmokePresentedFrame(",
            '<< QStringLiteral("prep_region_clock_monotonic=%1").arg(\n'
            '                   bool01( telemetryBoolValue(\n'
            '                       timing, "playback_prep_region_clock_monotonic" ) ) )',
            "playback_smoke.timing_validity no longer re-emits prep_region_clock_monotonic "
            "read back from the same key the probe wrote.",
        )

    def test_derived_subtraction_basis_and_inputs_literals_are_emitted(self):
        span = _function_body_span(
            self.main_window, "void MainWindow::notePlaybackSmokePresentedFrame("
        )
        body = self.main_window[span[0]:span[1]]
        for statement, label in (
            (
                '<< QStringLiteral("processed16_threading_overhead_basis=derived_subtraction")',
                "processed16_threading_overhead_basis",
            ),
            (
                '<< QStringLiteral("processed16_threading_overhead_inputs=render_work_ms,llrawproc_ms,processed16_ms")',
                "processed16_threading_overhead_inputs",
            ),
            (
                '<< QStringLiteral("processed8_threading_overhead_basis=derived_subtraction")',
                "processed8_threading_overhead_basis",
            ),
            (
                '<< QStringLiteral("processed8_threading_overhead_inputs=render_work_ms,llrawproc_ms,processed8_ms")',
                "processed8_threading_overhead_inputs",
            ),
            (
                '<< QStringLiteral("texture_present_host_gap_ms_basis=derived_subtraction")',
                "texture_present_host_gap_ms_basis",
            ),
            (
                '<< QStringLiteral("texture_present_host_gap_ms_inputs=texture_present_wall_ms,texture_present_total_ms")',
                "texture_present_host_gap_ms_inputs",
            ),
        ):
            with self.subTest(field=label):
                self.assertIn(
                    statement,
                    body,
                    f"timing_validity's {label} literal is missing or its text changed -- "
                    "this literal is what tells a downstream reader (and the analyzer's "
                    "DERIVED_BASIS_FIELDS join) that the paired *_ms field is a subtraction "
                    "residual, not an independent measurement.",
                )

    def test_remaining_timing_validity_writers_are_emitted(self):
        span = _function_body_span(
            self.main_window, "void MainWindow::notePlaybackSmokePresentedFrame("
        )
        body = self.main_window[span[0]:span[1]]
        for statement, label in (
            (
                '<< QStringLiteral("texture_present_available=%1").arg(\n'
                '                   bool01( texturePresentAvailable ) )',
                "texture_present_available",
            ),
            (
                '<< QStringLiteral("texture_present_recon_component_available=%1").arg(\n'
                '                   bool01( texturePresentReconAvailable ) )',
                "texture_present_recon_component_available",
            ),
            (
                '<< QStringLiteral("texture_present_amaze_component_available=%1").arg(\n'
                '                   bool01( texturePresentAmazeAvailable ) )',
                "texture_present_amaze_component_available",
            ),
            (
                '<< QStringLiteral("texture_present_upload_ms_basis=%1").arg(\n'
                '                   texturePresentUploadMsBasis )',
                "texture_present_upload_ms_basis",
            ),
            (
                '<< QStringLiteral("cpu_amaze_debayer_skipped_for_gpu_tex_nr=%1").arg(\n'
                '                   bool01( telemetryBoolValue(\n'
                '                       timing, "render_thread_cpu_amaze_debayer_skipped_for_gpu_tex_nr" ) ) )',
                "cpu_amaze_debayer_skipped_for_gpu_tex_nr",
            ),
            (
                '<< QStringLiteral("gpu_pipeline_status=%1").arg(\n'
                '                   QString::fromLatin1(\n'
                '                       mainWindowGpuPlaybackPipelineStatusToken( gpuPlaybackPipelineStatus ) ) )',
                "gpu_pipeline_status",
            ),
        ):
            with self.subTest(field=label):
                self.assertIn(
                    statement,
                    body,
                    f"timing_validity's {label} field no longer sources the live value it "
                    "is documented to.",
                )


if __name__ == "__main__":
    unittest.main()
