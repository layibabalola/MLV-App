"""Falsifier tests for analyze-frame-telemetry.py.

CUDA-ATTRIBUTION-BASELINE-1 round 11 (sol MINOR, "the legacy analyzer reads
absent timing fields as zero"): each test is written to FAIL if a field
simply absent from a playback_smoke.frame record (the probe did not run
that frame) is silently treated as a measured 0.0 again.

The module under test has a hyphenated filename (analyze-frame-telemetry.py)
so it cannot be `import`ed normally -- load it by path, same pattern as
tools/repo_hygiene/test_mlv_never_authorized.py's `_load_hook_module`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().with_name("analyze-frame-telemetry.py")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_analyze_frame_telemetry_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyzer = _load_module()


def _frame_line(session, index, **fields):
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    return f"playback_smoke.frame session={session} index={index} {kv}"


def _timing_validity_line(session, index, **fields):
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    return f"playback_smoke.timing_validity session={session} index={index} {kv}"


def test_parse_log_excludes_a_field_absent_from_one_record_rather_than_zero_filling():
    lines = [
        _frame_line(1, 0, render_total_ms=10.0, llrawproc_ms=4.0),
        # index 1 OMITS llrawproc_ms entirely -- the probe did not run, not a true 0.0.
        _frame_line(1, 1, render_total_ms=12.0),
    ]
    frames, _ = analyzer.parse_log(lines)
    assert len(frames) == 2
    assert frames[0]["llrawproc_ms"] == 4.0
    assert "llrawproc_ms" not in frames[1]


def test_percentiles_only_use_frames_where_the_field_is_present(capsys):
    lines = [
        _frame_line(1, 0, render_total_ms=10.0, llrawproc_ms=100.0),
        # llrawproc_ms absent here: a naive get(f, 0.0) would drag the
        # median from 100.0 down toward 0.0.
        _frame_line(1, 1, render_total_ms=10.0),
    ]
    log_path_lines = lines
    frames, validity = analyzer.parse_log(log_path_lines)

    # Reimplement just the percentile-table body's field loop (avoids a
    # temp-file round trip) to check the printed n/unavail bookkeeping.
    present = [r for r in frames if "llrawproc_ms" in r]
    vals = sorted(r["llrawproc_ms"] for r in present)
    assert vals == [100.0]
    assert len(present) == 1
    assert len(frames) - len(present) == 1  # one frame correctly excluded, not zero-filled


def test_analyze_median_is_not_dragged_toward_zero_by_absent_frames(tmp_path, capsys):
    log_path = tmp_path / "trace.log"
    lines = [_frame_line(1, 0, render_total_ms=10.0, llrawproc_ms=100.0)]
    for i in range(1, 10):
        lines.append(_frame_line(1, i, render_total_ms=10.0))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    analyzer.analyze(str(log_path))
    out = capsys.readouterr().out

    llrawproc_line = next(l for l in out.splitlines() if l.startswith("llrawproc_ms"))
    fields = llrawproc_line.split()
    # field name, p50, p90, p99, max, n, unavail
    assert fields[0] == "llrawproc_ms"
    p50 = float(fields[1])
    n_present = int(fields[-2])
    n_unavailable = int(fields[-1])
    assert p50 == 100.0, (
        "the single present llrawproc_ms=100.0 sample must not be diluted by the 9 "
        f"frames where the field is absent -- got p50={p50}, line={llrawproc_line!r}"
    )
    assert n_present == 1
    assert n_unavailable == 9


def test_derived_residual_field_is_labelled_when_the_joined_validity_record_says_so(tmp_path, capsys):
    log_path = tmp_path / "trace.log"
    lines = [
        _frame_line(1, 0, render_total_ms=10.0, processed16_threading_overhead_ms=5.0),
        _timing_validity_line(
            1, 0, processed16_threading_overhead_basis="derived_subtraction"
        ),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    analyzer.analyze(str(log_path))
    out = capsys.readouterr().out
    assert "processed16_threading_overhead_ms (derived)" in out


def test_derived_residual_field_is_not_mislabelled_without_a_joined_validity_record(tmp_path, capsys):
    log_path = tmp_path / "trace.log"
    # No playback_smoke.timing_validity line at all -- nothing to join.
    lines = [_frame_line(1, 0, render_total_ms=10.0, processed16_threading_overhead_ms=5.0)]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    analyzer.analyze(str(log_path))
    out = capsys.readouterr().out
    assert "processed16_threading_overhead_ms (derived)" not in out
    assert "processed16_threading_overhead_ms" in out


def test_slowest_decile_average_excludes_frames_missing_the_field_rather_than_zero_filling(tmp_path, capsys):
    log_path = tmp_path / "trace.log"
    # 10 frames so the slowest decile is exactly the single sorted-last frame;
    # give it a real llrawproc_ms and make sure a hypothetical absent-field
    # frame elsewhere in the decile could not have dragged the average to 0.
    lines = []
    for i in range(9):
        lines.append(_frame_line(1, i, render_total_ms=float(i), llrawproc_ms=1.0))
    lines.append(_frame_line(1, 9, render_total_ms=100.0, llrawproc_ms=40.0))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    analyzer.analyze(str(log_path))
    out = capsys.readouterr().out
    assert "llrawproc (recon etc.)            40.0 ms" in out


def test_slowest_decile_reports_unavailable_instead_of_zero_when_no_slow_frame_has_the_field(tmp_path, capsys):
    log_path = tmp_path / "trace.log"
    lines = []
    for i in range(9):
        lines.append(_frame_line(1, i, render_total_ms=float(i), llrawproc_ms=1.0))
    # The slowest-decile frame never wrote llrawproc_ms at all.
    lines.append(_frame_line(1, 9, render_total_ms=100.0))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    analyzer.analyze(str(log_path))
    out = capsys.readouterr().out
    assert "llrawproc (recon etc.)           unavailable" in out


def test_decode_residual_is_unavailable_not_zero_filled_when_a_component_is_entirely_absent(tmp_path, capsys):
    # CUDA-ATTRIBUTION-BASELINE-1 round 12 (sol MINOR): the slowest-decile
    # residual used `avg("llrawproc_ms") or 0.0`, so an entirely-absent
    # llrawproc_ms population in the slow decile silently substituted 0.0
    # and reported a confident (wrong) residual instead of "unavailable".
    log_path = tmp_path / "trace.log"
    lines = []
    for i in range(9):
        lines.append(_frame_line(1, i, render_total_ms=float(i), render_work_ms=1.0,
                                  llrawproc_ms=1.0, processed8_ms=1.0))
    # The slowest-decile frame has render_work_ms and processed8_ms, but
    # never wrote llrawproc_ms at all -- one of the three residual inputs is
    # entirely unavailable for this decile.
    lines.append(_frame_line(1, 9, render_total_ms=100.0, render_work_ms=100.0,
                              processed8_ms=10.0))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    analyzer.analyze(str(log_path))
    out = capsys.readouterr().out
    assert "decode+debayer+scale (residual)  unavailable" in out, out
    # A pre-fix run would have printed a confident (and wrong) 90.0 ms here
    # (100.0 - 0.0 - 10.0) instead of admitting the component is missing.
    assert "decode+debayer+scale (residual)" in out and "90.0 ms" not in out
