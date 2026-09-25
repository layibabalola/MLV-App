"""Structural census over every key playback_smoke.render_manifest and
playback_smoke.timing_validity emit, plus every requestContext scale/
quality/smoke field CUDA-ATTRIBUTION-BASELINE-1 added -- round 12 (sol
MAJOR, fable minor 1: "adding pins by hand each round just produces the
next round's unpinned list").

Rounds 9-11 each closed the specific gaps the previous review named, and
each next review named MORE unpinned writers, because the census itself was
a hand-written list that only grew when a reviewer happened to notice a
missing row. This module instead MECHANICALLY ENUMERATES the keys/fields
straight from the live source (regex over the actual emission blocks and
struct declaration, not a copied-by-hand list -- see `_extract_keys` /
`_extract_context_fields` below) and requires every single one to have an
entry in the CENSUS table: either `Pinned(...)`, independently re-verified
here against the live source, or `Exempt(reason)`, a one-line reason it
needs no wiring pin. A newly emitted key with no entry FAILS THIS TEST
OUTRIGHT (`test_every_extracted_key_has_a_census_entry`) -- a producer must
add a Pinned or Exempt row before merging, instead of the gap surviving
until the next review round notices it. A CENSUS entry for a key that no
longer exists in the source also fails
(`test_no_stale_census_entries_survive`), so a rename cannot leave a dead
row masquerading as coverage.

`Pinned` entries are not merely claims: each names the exact production
statement(s) the key depends on and this module re-asserts every one is
present at its exact text inside its required enclosing function, exactly
like tools/repo_hygiene/test_playback_manifest_timing_writers_wiring.py
does (duplicated, not imported -- same established precedent that file's
own docstring names). A key whose CENSUS entry is `Pinned` but whose
statement has drifted or been deleted fails here independently of whether
the sibling wiring-test file still has (or lost) its own copy of the same
check.
"""
from pathlib import Path
import re
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW_CPP = REPO_ROOT / "platform" / "qt" / "MainWindow.cpp"
GPU_DISPLAY_VIEWPORT_CPP = REPO_ROOT / "platform" / "qt" / "GpuDisplayViewport.cpp"
GPU_DISPLAY_WINDOW_CPP = REPO_ROOT / "platform" / "qt" / "GpuDisplayWindow.cpp"
RENDER_FRAME_THREAD_H = REPO_ROOT / "platform" / "qt" / "RenderFrameThread.h"

NOTE_PRESENTED_FRAME_SIGNATURE = "void MainWindow::notePlaybackSmokePresentedFrame("
DRAW_FRAME_SIGNATURE = "void MainWindow::drawFrame( bool updateTimecodeLabel )"
PRESENT_PREPARED_FRAME_SIGNATURE = (
    "void MainWindow::presentPlaybackPreparedFrame( const PlaybackPrepResult &result )"
)
RESET_METADATA_SIGNATURE = "void resetMetadata( void )"
GPU_VIEWPORT_WRITER_SIGNATURE = (
    "GpuDisplayViewport::setPresentedGpuPlaybackReconAmazePostWbTexture("
)
GPU_WINDOW_WRITER_SIGNATURE = (
    "GpuDisplayWindow::setPresentedGpuPlaybackReconAmazePostWbTexture("
)

MANIFEST_BLOCK_START = "QStringList manifestFields;"
MANIFEST_BLOCK_END = 'QStringLiteral("playback_smoke.render_manifest ")'
TIMING_BLOCK_START = "QStringList timingValidityFields;"
TIMING_BLOCK_END = 'QStringLiteral("playback_smoke.timing_validity ")'

KEY_RE = re.compile(r'QStringLiteral\("([A-Za-z0-9_]+)=')
CONTEXT_FIELD_RE = re.compile(
    r'(?:int|bool|uint32_t|uint64_t|double|QString)\s+'
    r'(playback(?:Scale|Quality|Smoke)\w*)\s*='
)


# --------------------------------------------------------------------- helpers
# Duplicated (not imported) from test_playback_manifest_timing_writers_wiring.py
# -- established precedent for this family of source-level wiring checks.

def _strip_comments(source):
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


def _braced_span(source, start_marker):
    start = source.index(start_marker)
    brace_open = source.index("{", start)
    depth = 1
    i = brace_open + 1
    while depth > 0:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    return brace_open + 1, i - 1


# --------------------------------------------------------------- mechanical extraction

def _extract_keys(source, start_marker, end_marker):
    """Every `QStringLiteral("key=...")` key inside [start_marker, end_marker),
    in first-seen order, deduplicated. This is the census's SOURCE OF TRUTH
    for "every key the emitters write" -- never a hand-written list."""
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    block = source[start:end]
    seen = []
    for m in KEY_RE.finditer(block):
        key = m.group(1)
        if key not in seen:
            seen.append(key)
    return seen


def _extract_context_fields(header_source):
    """Every requestContext scale/quality/smoke field declared inside
    ReadyFrame::PresentationContext, in declaration order, deduplicated."""
    lo, hi = _braced_span(header_source, "struct PresentationContext")
    span = header_source[lo:hi]
    seen = []
    for m in CONTEXT_FIELD_RE.finditer(span):
        field = m.group(1)
        if field not in seen:
            seen.append(field)
    return seen


# --------------------------------------------------------------------- census entries

class Pinned:
    """`checks` is a sequence of (source_attr, function_signature, statement)
    triples; every one must be present verbatim inside that function of the
    named source (source_attr indexes SOURCES below)."""

    def __init__(self, *checks):
        self.checks = checks


class Exempt:
    def __init__(self, reason):
        assert reason, "an Exempt entry must carry a one-line reason"
        self.reason = reason


_PHASE4_REASON = (
    "pre-dates CUDA-ATTRIBUTION-BASELINE-1 (Phase 4B render_manifest); unrelated to "
    "this baseline's scale/quality/phase3 attribution mechanisms and unchanged by "
    "this PR's mechanism set"
)
_JOIN_KEY_REASON = (
    "join key (session+index) echoed identically across every playback_smoke.* "
    "record for this frame; not a measurement, used only to correlate rows"
)

MANIFEST_CENSUS = {
    "session": Exempt(_JOIN_KEY_REASON),
    "index": Exempt(_JOIN_KEY_REASON),
    "path_code": Exempt(_PHASE4_REASON),
    "path_label": Exempt(_PHASE4_REASON),
    "path_source": Exempt(_PHASE4_REASON),
    "path_fallback_reason": Exempt(_PHASE4_REASON),
    "proxy_halvings": Exempt(_PHASE4_REASON),
    "aggressive_preview": Exempt(_PHASE4_REASON),
    "direct8": Exempt(_PHASE4_REASON),
    "processed8_cache_hit": Exempt(_PHASE4_REASON),
    "raw_prefetch": Exempt(_PHASE4_REASON),
    "y_crop_rows": Exempt(_PHASE4_REASON),
    "dual_iso_valid": Exempt(_PHASE4_REASON),
    "dual_iso_use_fullres": Exempt(_PHASE4_REASON),
    "dual_iso_interp": Exempt(_PHASE4_REASON),
    "src_w": Exempt(_PHASE4_REASON),
    "src_h": Exempt(_PHASE4_REASON),
    "rendered_w": Exempt(_PHASE4_REASON),
    "rendered_h": Exempt(_PHASE4_REASON),
    "scale_target_w": Exempt(_PHASE4_REASON),
    "scale_target_h": Exempt(_PHASE4_REASON),
    "reduced": Exempt(_PHASE4_REASON),
    "stretch_x": Exempt(_PHASE4_REASON),
    "stretched_w": Exempt(_PHASE4_REASON),
    "stretched_h": Exempt(_PHASE4_REASON),
    "requested_scale": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("requested_scale=%1").arg(\n'
         '                   requestContext.playbackScaleFactorRequestedBeforeGpuTextureRouteClamp )'),
    ),
    "effective_scale": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("effective_scale=%1").arg( requestContext.playbackScaleFactor )'),
    ),
    "achieved_scale": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("achieved_scale=%1").arg( readyFrame.playbackScaleFactorActive )'),
    ),
    "scale_clamped_for_gpu_texture_route": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("scale_clamped_for_gpu_texture_route=%1").arg(\n'
         "                   bool01( requestContext.playbackScaleFactorRequestedBeforeGpuTextureRouteClamp\n"
         "                           != requestContext.playbackScaleFactor ) )"),
    ),
    "quality_mode": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("quality_mode=%1").arg(\n'
         "                   requestContext.playbackQualityMode )"),
    ),
    "phase3_mode": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("phase3_mode=%1").arg(\n'
         "                   static_cast<int>( readyFrame.phase3Mode ) )"),
        # The emitted field is only trustworthy if a reused slot cannot carry
        # a stale value forward -- fold resetMetadata's clear into this key's
        # coverage (sol round-12: "never opens RenderFrameThread.h to verify
        # resetMetadata clears stale Phase3 state").
        ("render_thread_h", RESET_METADATA_SIGNATURE, "phase3Mode = Phase3Mode::Disabled;"),
    ),
    "phase3_mode_configured": Exempt(
        "explicitly-labelled policy snapshot recomputed live for readability (see "
        "the field's own doc comment); the per-frame gate value is phase3_mode, "
        "pinned separately above, and the '_configured' suffix makes this field's "
        "different semantics visible in the log line itself"
    ),
}

TIMING_CENSUS = {
    "session": Exempt(_JOIN_KEY_REASON),
    "index": Exempt(_JOIN_KEY_REASON),
    "processed16_threading_overhead_basis": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("processed16_threading_overhead_basis=derived_subtraction")'),
    ),
    "processed16_threading_overhead_inputs": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("processed16_threading_overhead_inputs=render_work_ms,llrawproc_ms,processed16_ms")'),
    ),
    "processed8_threading_overhead_basis": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("processed8_threading_overhead_basis=derived_subtraction")'),
    ),
    "processed8_threading_overhead_inputs": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("processed8_threading_overhead_inputs=render_work_ms,llrawproc_ms,processed8_ms")'),
    ),
    "texture_present_host_gap_ms_basis": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("texture_present_host_gap_ms_basis=derived_subtraction")'),
    ),
    "texture_present_host_gap_ms_inputs": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("texture_present_host_gap_ms_inputs=texture_present_wall_ms,texture_present_total_ms")'),
    ),
    "texture_present_available": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("texture_present_available=%1").arg(\n'
         "                   bool01( texturePresentAvailable ) )"),
        # The combined flag this field reports is only correct if both
        # presentation paths actually OR their two components (sol round-12:
        # "the combined availability writer" -- GpuDisplayWindow.cpp).
        ("gpu_viewport", GPU_VIEWPORT_WRITER_SIGNATURE,
         "timing->available = reconTiming.available || amazeTiming.available;"),
        ("gpu_window", GPU_WINDOW_WRITER_SIGNATURE,
         "timing->available = reconTiming.available || amazeTiming.available;"),
    ),
    "texture_present_recon_component_available": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("texture_present_recon_component_available=%1").arg(\n'
         "                   bool01( texturePresentReconAvailable ) )"),
    ),
    "texture_present_amaze_component_available": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("texture_present_amaze_component_available=%1").arg(\n'
         "                   bool01( texturePresentAmazeAvailable ) )"),
    ),
    "texture_present_upload_ms_basis": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("texture_present_upload_ms_basis=%1").arg(\n'
         "                   texturePresentUploadMsBasis )"),
    ),
    "cpu_amaze_debayer_skipped_for_gpu_tex_nr": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("cpu_amaze_debayer_skipped_for_gpu_tex_nr=%1").arg(\n'
         "                   bool01( telemetryBoolValue(\n"
         '                       timing, "render_thread_cpu_amaze_debayer_skipped_for_gpu_tex_nr" ) ) )'),
    ),
    "gpu_pipeline_status": Pinned(
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("gpu_pipeline_status=%1").arg(\n'
         "                   QString::fromLatin1(\n"
         "                       mainWindowGpuPlaybackPipelineStatusToken( gpuPlaybackPipelineStatus ) ) )"),
    ),
    "prep_region_clock_resolution_ns": Pinned(
        ("main_window", PRESENT_PREPARED_FRAME_SIGNATURE,
         "prepRegionClockResolutionNs = nowNs - prepRegionClockLastNs;"),
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("prep_region_clock_resolution_ns=%1").arg(\n'
         "                   telemetryDoubleValue(\n"
         '                       timing, "playback_prep_region_clock_resolution_ns" ), 0, \'f\', 0 )'),
    ),
    "prep_region_clock_monotonic": Pinned(
        ("main_window", PRESENT_PREPARED_FRAME_SIGNATURE,
         'readyFrame.stageTimingTelemetry.insert(\n'
         '        QStringLiteral("playback_prep_region_clock_monotonic"),\n'
         '        prepRegionClock.isMonotonic() );'),
        ("main_window", NOTE_PRESENTED_FRAME_SIGNATURE,
         '<< QStringLiteral("prep_region_clock_monotonic=%1").arg(\n'
         "                   bool01( telemetryBoolValue(\n"
         '                       timing, "playback_prep_region_clock_monotonic" ) ) )'),
    ),
}

CONTEXT_CENSUS = {
    "playbackScaleFactor": Pinned(
        ("main_window", DRAW_FRAME_SIGNATURE,
         "requestContext.playbackScaleFactor = effectivePlaybackScaleFactorForRequest();"),
    ),
    "playbackScaleFactorRequestedBeforeGpuTextureRouteClamp": Pinned(
        ("main_window", DRAW_FRAME_SIGNATURE,
         "requestContext.playbackScaleFactorRequestedBeforeGpuTextureRouteClamp =\n"
         "        m_playbackScaleClampedForGpuTextureRouteActive\n"
         "            ? m_playbackScaleClampedForGpuTextureRouteRequestedScale\n"
         "            : requestContext.playbackScaleFactor;"),
    ),
    "playbackQualityMode": Pinned(
        ("main_window", DRAW_FRAME_SIGNATURE,
         "requestContext.playbackQualityMode = m_playbackQualityMode;"),
    ),
    "playbackSmokeLoopEpoch": Pinned(
        ("main_window", DRAW_FRAME_SIGNATURE,
         "requestContext.playbackSmokeLoopEpoch = m_playbackSmokeLoopWrapCount;"),
    ),
}


class PlaybackManifestTimingCensusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {
            "main_window": _strip_comments(MAIN_WINDOW_CPP.read_text(encoding="utf-8")),
            "gpu_viewport": _strip_comments(GPU_DISPLAY_VIEWPORT_CPP.read_text(encoding="utf-8")),
            "gpu_window": _strip_comments(GPU_DISPLAY_WINDOW_CPP.read_text(encoding="utf-8")),
            "render_thread_h": _strip_comments(RENDER_FRAME_THREAD_H.read_text(encoding="utf-8")),
        }
        cls.actual_manifest_keys = _extract_keys(
            cls.sources["main_window"], MANIFEST_BLOCK_START, MANIFEST_BLOCK_END
        )
        cls.actual_timing_keys = _extract_keys(
            cls.sources["main_window"], TIMING_BLOCK_START, TIMING_BLOCK_END
        )
        cls.actual_context_fields = _extract_context_fields(cls.sources["render_thread_h"])

    # ---- the mechanical extraction itself is exercised, not just trusted ----

    def test_extraction_finds_the_documented_key_counts(self):
        # A canary on the extraction mechanism: if these counts move, either
        # a key was added/removed (expected -- go update the matching CENSUS
        # dict) or the extraction regex broke (fails loud either way).
        self.assertEqual(len(self.actual_manifest_keys), 32, self.actual_manifest_keys)
        self.assertEqual(len(self.actual_timing_keys), 16, self.actual_timing_keys)
        self.assertEqual(
            self.actual_context_fields,
            [
                "playbackScaleFactor",
                "playbackScaleFactorRequestedBeforeGpuTextureRouteClamp",
                "playbackQualityMode",
                "playbackSmokeLoopEpoch",
            ],
            self.actual_context_fields,
        )

    # ---- every mechanically-extracted key/field must have a census entry ----

    def test_every_extracted_manifest_key_has_a_census_entry(self):
        missing = [k for k in self.actual_manifest_keys if k not in MANIFEST_CENSUS]
        self.assertEqual(
            missing, [],
            f"render_manifest emits key(s) {missing} with no entry in MANIFEST_CENSUS -- "
            "add a Pinned(...) or Exempt(reason) row before merging.",
        )

    def test_every_extracted_timing_key_has_a_census_entry(self):
        missing = [k for k in self.actual_timing_keys if k not in TIMING_CENSUS]
        self.assertEqual(
            missing, [],
            f"timing_validity emits key(s) {missing} with no entry in TIMING_CENSUS -- "
            "add a Pinned(...) or Exempt(reason) row before merging.",
        )

    def test_every_extracted_context_field_has_a_census_entry(self):
        missing = [f for f in self.actual_context_fields if f not in CONTEXT_CENSUS]
        self.assertEqual(
            missing, [],
            f"PresentationContext declares scale/quality/smoke field(s) {missing} with no "
            "entry in CONTEXT_CENSUS -- add a Pinned(...) or Exempt(reason) row before "
            "merging.",
        )

    # ---- no stale census rows for keys/fields that no longer exist ----------

    def test_no_stale_census_entries_survive(self):
        stale_manifest = [k for k in MANIFEST_CENSUS if k not in self.actual_manifest_keys]
        stale_timing = [k for k in TIMING_CENSUS if k not in self.actual_timing_keys]
        stale_context = [f for f in CONTEXT_CENSUS if f not in self.actual_context_fields]
        self.assertEqual(stale_manifest, [], f"MANIFEST_CENSUS has stale entries: {stale_manifest}")
        self.assertEqual(stale_timing, [], f"TIMING_CENSUS has stale entries: {stale_timing}")
        self.assertEqual(stale_context, [], f"CONTEXT_CENSUS has stale entries: {stale_context}")

    # ---- every Pinned entry's statements are independently re-verified -----

    def test_every_pinned_statement_is_present_at_its_claimed_site(self):
        for table_name, table in (
            ("MANIFEST_CENSUS", MANIFEST_CENSUS),
            ("TIMING_CENSUS", TIMING_CENSUS),
            ("CONTEXT_CENSUS", CONTEXT_CENSUS),
        ):
            for key, entry in table.items():
                if not isinstance(entry, Pinned):
                    continue
                for source_attr, signature, statement in entry.checks:
                    with self.subTest(table=table_name, key=key, signature=signature):
                        source = self.sources[source_attr]
                        span = _function_body_span(source, signature)
                        body = source[span[0]:span[1]]
                        self.assertIn(
                            statement, body,
                            f"{table_name}[{key!r}] claims the statement below is present "
                            f"inside {signature!r} of {source_attr}, but it is missing or "
                            f"its text has drifted:\n{statement}",
                        )


if __name__ == "__main__":
    unittest.main()
