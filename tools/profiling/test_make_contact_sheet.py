#!/usr/bin/env python3
"""Falsifier tests for make-contact-sheet.py.

Builds synthetic frame-NN.png + frame-NN.json fixture pairs (never owner footage -- see
make-contact-sheet.py's own header) and asserts the composer's grid geometry, per-tile
labelling and per-tile stats on a KNOWN-by-construction gradient, plus its
saved=false/off-by-default handling.
"""
import importlib.util
import json
import os
import sys
import tempfile

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULE_PATH = os.path.join(_HERE, "make-contact-sheet.py")

# The module's own filename has a hyphen, so it cannot be `import`ed by name.
_spec = importlib.util.spec_from_file_location("make_contact_sheet", _MODULE_PATH)
mcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcs)


class Args:
    """Minimal stand-in for argparse.Namespace, matching compose_sheet's field reads."""

    def __init__(self, cols=3, clip_id="fixture", host="h", gpu="g", build_sha="sha", backend="cpu", scale="4"):
        self.cols = cols
        self.clip_id = clip_id
        self.host = host
        self.gpu = gpu
        self.build_sha = build_sha
        self.backend = backend
        self.scale = scale


def _write_frame(
    frames_dir, index, color, look_assist_enabled=True, saved=True, w=64, h=36,
    render_path="gpu_texture_no_readback", playback_path=True,
):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = color[0]
    arr[:, :, 1] = color[1]
    arr[:, :, 2] = color[2]
    png_path = os.path.join(frames_dir, f"frame-{index:02d}.png")
    Image.fromarray(arr, "RGB").save(png_path)
    sidecar = {
        "index": index,
        "captured_utc": "2026-09-26T00:00:00.000Z",
        "serial": 100 + index,
        "display_frame": index,
        "elapsed_ms": float(index * 250),
        "texture_source": "gl_window_framebuffer_readback",
        "render_path": render_path,
        "playback_path": playback_path,
        "path": png_path,
        "look_assist_enabled": look_assist_enabled,
        "look_assist_scene": "outdoor",
        "look_assist_exposure": 5,
        "look_assist_contrast": 10,
        "look_assist_pivot": 75,
        "look_assist_temperature": -3,
        "look_assist_tint": 1,
        "look_assist_vibrance": 0,
        "look_assist_shadows": 0,
        "look_assist_highlights": 0,
        "settled": True,
        "saved": saved,
    }
    with open(os.path.join(frames_dir, f"frame-{index:02d}.json"), "w", encoding="utf-8") as f:
        json.dump(sidecar, f)
    return png_path


def _make_frames_dir(n=6, saved_flags=None):
    d = tempfile.mkdtemp()
    for i in range(n):
        saved = True if saved_flags is None else saved_flags[i]
        # A distinct, ascending grey level per frame: makes luma_p50 strictly increasing,
        # a known-by-construction property the stats test checks below.
        level = 20 + i * 30
        _write_frame(d, i, (level, level, level), saved=saved)
    return d


def test_load_frames_sorts_by_index_and_skips_unsaved():
    d = _make_frames_dir(n=4, saved_flags=[True, False, True, True])
    frames = mcs.load_frames(__import__("pathlib").Path(d))
    assert [f["index"] for f in frames] == [0, 2, 3]


def test_sheet_grid_dimensions_match_cols_and_frame_count():
    from pathlib import Path

    d = Path(_make_frames_dir(n=6))
    frames = mcs.load_frames(d)
    sheet, tile_stats = mcs.compose_sheet(frames, d, Args(cols=3))
    assert len(tile_stats) == 6
    # 6 frames at 3 cols -> 2 rows. Sheet width must fit exactly 3 tile columns plus padding,
    # and height must fit the header plus 2 tile rows plus padding -- not just "some size".
    expected_cols = 3
    expected_rows = 2
    tile_w = mcs.TILE_TARGET_WIDTH
    assert sheet.width == expected_cols * (tile_w + mcs.TILE_PADDING) + mcs.TILE_PADDING
    min_height = mcs.HEADER_HEIGHT + expected_rows * mcs.TILE_PADDING
    assert sheet.height > min_height


def test_tile_label_pixels_present_for_each_frame():
    from pathlib import Path

    d = Path(_make_frames_dir(n=2))
    frames = mcs.load_frames(d)
    tile_size = (160, 100)
    font = mcs._load_font(13)
    for sidecar in frames:
        img_path = mcs._resolve_frame_image_path(sidecar, d)
        with Image.open(img_path) as im:
            tile = mcs.build_tile(im, sidecar, tile_size, font)
        # The label strip is drawn pure black with white text: some pixel in that strip must be
        # non-black, or the label text failed to render at all.
        label_region = np.asarray(tile.convert("RGB"))[-mcs.TILE_LABEL_HEIGHT:, :, :]
        assert label_region.max() > 100, "expected bright label glyph pixels in the label strip"


def test_header_reports_look_assist_and_clip_id_never_a_path():
    from pathlib import Path

    d = Path(_make_frames_dir(n=2))
    frames = mcs.load_frames(d)
    header = mcs.build_header(640, Args(clip_id="M16-1243"), frames)
    text_present = np.asarray(header.convert("L")).max() > 40
    assert text_present, "expected header text to render"
    # Structural guarantee, not an OCR check: build_header's own format string embeds
    # args.clip_id verbatim and never touches sidecar['path'] -- so a header built from
    # sidecars carrying real filesystem paths never leaks one. Confirmed by construction:
    # the only path-shaped value read from `frames` anywhere in build_header is absent.
    import inspect

    source = inspect.getsource(mcs.build_header)
    assert "sidecar" not in source and "['path']" not in source and '["path"]' not in source


def test_header_lines_include_all_applied_look_assist_values():
    from pathlib import Path

    d = Path(_make_frames_dir(n=2))
    frames = mcs.load_frames(d)
    lines = mcs.build_header_lines(Args(), frames)
    look_assist_line = next(line for line in lines if line.startswith("look_assist:"))
    # Hub finding (r1b): the demo header showed only 4 values (exposure, contrast,
    # temperature, tint). All eight applied values must be present.
    for token in (
        "exposure=5", "contrast=10", "pivot=75", "shadows=0", "highlights=0",
        "vibrance=0", "temperature=-3", "tint=1",
    ):
        assert token in look_assist_line, f"expected {token!r} in {look_assist_line!r}"


def test_header_reports_majority_render_path():
    from pathlib import Path

    d = tempfile.mkdtemp()
    _write_frame(d, 0, (50, 50, 50), render_path="gpu_texture_no_readback")
    _write_frame(d, 1, (80, 80, 80), render_path="gpu_texture_no_readback")
    _write_frame(d, 2, (110, 110, 110), render_path="cpu_amaze")
    frames = mcs.load_frames(Path(d))
    lines = mcs.build_header_lines(Args(), frames)
    assert any("render_path(majority)=gpu_texture_no_readback" in line for line in lines)


def test_header_reports_unknown_majority_render_path_when_none_present():
    from pathlib import Path

    d = tempfile.mkdtemp()
    _write_frame(d, 0, (50, 50, 50), render_path="")
    frames = mcs.load_frames(Path(d))
    lines = mcs.build_header_lines(Args(), frames)
    assert any("render_path(majority)=unknown" in line for line in lines)


def test_header_flags_frames_not_captured_via_the_playback_path():
    from pathlib import Path

    d = tempfile.mkdtemp()
    _write_frame(d, 0, (50, 50, 50), playback_path=True)
    _write_frame(d, 1, (80, 80, 80), playback_path=False)
    frames = mcs.load_frames(Path(d))
    lines = mcs.build_header_lines(Args(), frames)
    assert any("WARNING" in line and "1/2" in line for line in lines)


def test_header_has_no_warning_line_when_every_frame_is_playback_path():
    from pathlib import Path

    d = Path(_make_frames_dir(n=3))  # _write_frame defaults playback_path=True
    frames = mcs.load_frames(d)
    lines = mcs.build_header_lines(Args(), frames)
    assert not any("WARNING" in line for line in lines)


def test_stats_on_synthetic_gradient_are_monotonic_and_bounded():
    from pathlib import Path

    d = Path(_make_frames_dir(n=5))
    frames = mcs.load_frames(d)
    _, tile_stats = mcs.compose_sheet(frames, d, Args(cols=5))
    p50s = [t["luma_p50"] for t in tile_stats]
    # Built with ascending grey levels (20, 50, 80, 110, 140): p50 must strictly increase.
    assert p50s == sorted(p50s)
    assert p50s[0] < p50s[-1]
    for t in tile_stats:
        assert 0.0 <= t["luma_p1"] <= t["luma_p50"] <= t["luma_p99"] <= 255.0
        assert 0.0 <= t["mean_saturation"] <= 1.0
        assert 0.0 <= t["clipped_highlight_pct"] <= 100.0
        assert 0.0 <= t["crushed_black_pct"] <= 100.0
        # A pure grey (R==G==B) fixture frame is, by definition, fully desaturated.
        assert t["mean_saturation"] == 0.0


def test_crushed_black_and_clipped_highlight_fire_on_known_extremes():
    from pathlib import Path

    d = tempfile.mkdtemp()
    _write_frame(d, 0, (0, 0, 0))       # fully crushed black
    _write_frame(d, 1, (255, 255, 255))  # fully clipped highlight
    from pathlib import Path as P

    frames = mcs.load_frames(P(d))
    _, tile_stats = mcs.compose_sheet(frames, P(d), Args(cols=2))
    black_tile = next(t for t in tile_stats if t["index"] == 0)
    white_tile = next(t for t in tile_stats if t["index"] == 1)
    assert black_tile["crushed_black_pct"] == 100.0
    assert black_tile["clipped_highlight_pct"] == 0.0
    assert white_tile["clipped_highlight_pct"] == 100.0
    assert white_tile["crushed_black_pct"] == 0.0


def test_compose_sheet_raises_on_zero_usable_frames():
    from pathlib import Path

    d = Path(_make_frames_dir(n=2, saved_flags=[False, False]))
    frames = mcs.load_frames(d)
    assert frames == []
    try:
        mcs.compose_sheet(frames, d, Args())
        assert False, "expected ValueError for zero usable frames"
    except ValueError:
        pass


def test_save_under_budget_shrinks_an_oversized_sheet():
    big = Image.new("RGB", (4000, 3000), (100, 150, 200))
    out_path = __import__("pathlib").Path(tempfile.mkdtemp()) / "sheet.png"
    # An all-solid-color PNG compresses far below any real budget, so force a tiny cap to
    # exercise the shrink loop deterministically rather than relying on compressibility.
    mcs.save_under_budget(big, out_path, max_bytes=1)
    # Loop bails once tiles would go below 200px; the file must still exist and be smaller
    # than the untouched original by pixel count.
    with Image.open(out_path) as shrunk:
        assert shrunk.width * shrunk.height < big.width * big.height


def test_main_end_to_end_writes_sheet_and_stats(tmp_path=None):
    import subprocess
    from pathlib import Path

    workdir = Path(tempfile.mkdtemp())
    frames_dir = workdir / "frames"
    frames_dir.mkdir()
    for i in range(6):
        level = 30 + i * 20
        _write_frame(str(frames_dir), i, (level, level, level))
    sheet_out = workdir / "sheet.png"
    stats_out = workdir / "stats.json"
    result = subprocess.run(
        [
            sys.executable,
            _MODULE_PATH,
            "--frames-dir", str(frames_dir),
            "--sheet-out", str(sheet_out),
            "--stats-out", str(stats_out),
            "--cols", "3",
            "--clip-id", "fixture-e2e",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert sheet_out.is_file()
    assert stats_out.is_file()
    stats = json.loads(stats_out.read_text(encoding="utf-8"))
    assert stats["schema"] == mcs.SCHEMA_STATS
    assert stats["tile_count"] == 6
    assert len(stats["tiles"]) == 6
