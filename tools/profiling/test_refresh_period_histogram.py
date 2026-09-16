"""Falsifier tests for refresh_period_histogram.

Each test is written to FAIL if the matching rule quietly degrades to something
weaker -- treating a missing column as zeros, silently dropping an empty series to an
empty-but-successful report, or mis-rounding a refresh multiple at the bucket boundary.
"""
from __future__ import annotations

import csv

import pytest

from refresh_period_histogram import (
    RefreshHistogramError,
    ROUNDING_RULE,
    bucket_label,
    build_report,
    compute_buckets,
    compute_refresh_period,
    compute_region_stats,
    parse_frame_log_rows,
    parse_presentmon_intervals,
    percentile,
    refresh_multiple,
)


def _write_presentmon_csv(path, rows, column="msBetweenDisplayChange"):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ordinal", "timeInMs", column, "displayFpsEquivalent", "presentMode"])
        for i, value in enumerate(rows):
            writer.writerow([i, i * 16.67, value, 1000.0 / value if value else "", "Hardware: Independent Flip"])


def _frame_log_line(index: int, total_ms: float, **overrides) -> str:
    fields = {
        "prep_region_setup_ms": total_ms,
        "prep_region_gpu_ms": total_ms,
        "prep_region_image_ms": total_ms,
        "prep_region_present_ms": total_ms,
        "prep_region_finish_ms": total_ms,
        "prep_region_total_ms": total_ms,
        "prep_region_unattributed_ms": total_ms,
    }
    fields.update(overrides)
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    return (
        f"2026-09-16 00:00:00 qInfo() playback_smoke.frame session=1 index={index} "
        f"elapsed_ms=1.0 interval_ms=16.67 display_frame={index} serial={index} {kv}"
    )


def _write_frame_log(path, count=10, start=1):
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(count):
            fh.write(_frame_log_line(i, float(start + i)))
            fh.write("\n")


# --- refresh multiple / bucket rounding -------------------------------------------

def test_rounding_rule_round_half_up_at_boundary():
    # exactly 1.5x the refresh period must round UP to 2 (round-half-up), not down to 1
    assert refresh_multiple(24.0, 16.0) == 2
    assert refresh_multiple(23.9, 16.0) == 1


def test_refresh_multiple_never_reports_zero():
    assert refresh_multiple(0.1, 16.6667) == 1


def test_bucket_label_groups_three_and_above():
    assert bucket_label(1) == "1"
    assert bucket_label(2) == "2"
    assert bucket_label(3) == "3+"
    assert bucket_label(7) == "3+"


def test_rounding_rule_is_stated_in_the_output():
    assert "round-half-up" in ROUNDING_RULE
    assert "3+" in ROUNDING_RULE


# --- known 1/2/3 mix ----------------------------------------------------------------

def test_known_1_2_3_mix_buckets_correctly(tmp_path):
    csv_path = tmp_path / "presentmon-series.csv"
    values = [16.67] * 12 + [33.34] * 5 + [50.01] * 3
    _write_presentmon_csv(csv_path, values)

    intervals = parse_presentmon_intervals(str(csv_path))
    assert len(intervals) == 20

    refresh = compute_refresh_period(intervals)
    assert refresh["measurement"] == "mode"
    assert refresh["refreshPeriodMs"] == pytest.approx(16.67, abs=0.01)

    buckets = compute_buckets(intervals, refresh["refreshPeriodMs"])
    assert buckets["1"]["count"] == 12
    assert buckets["2"]["count"] == 5
    assert buckets["3+"]["count"] == 3
    assert buckets["1"]["share"] == pytest.approx(0.6)
    assert buckets["2"]["share"] == pytest.approx(0.25)
    assert buckets["3+"]["share"] == pytest.approx(0.15)
    # shares must sum to 1 -- every sample lands in exactly one bucket
    total_share = buckets["1"]["share"] + buckets["2"]["share"] + buckets["3+"]["share"]
    assert total_share == pytest.approx(1.0)


def test_refresh_period_falls_back_to_median_without_a_clear_mode(tmp_path):
    # every value in the 1-refresh cluster is distinct -- no mode has a plurality
    intervals = [16.60, 16.65, 16.70, 16.75, 33.4, 33.4]
    refresh = compute_refresh_period(intervals)
    assert refresh["measurement"] == "median"


# --- histogram ambiguity (all-2-refresh) --------------------------------------------

def test_all_2_refresh_with_nominal_buckets_as_2(tmp_path):
    # every presented frame actually took 2 refreshes; with the nominal (true) refresh
    # period supplied, the estimator is bypassed entirely and every sample buckets "2".
    csv_path = tmp_path / "presentmon-series.csv"
    values = [33.34] * 20
    _write_presentmon_csv(csv_path, values)

    log_path = tmp_path / "mlvapp.log"
    _write_frame_log(log_path, count=10, start=1)

    report = build_report(str(csv_path), str(log_path), refresh_period_ms=16.67)
    assert report["presentMon"]["refreshPeriodSource"] == "nominal"
    assert report["presentMon"]["refreshPeriodMeasurement"] == "nominal"
    assert report["presentMon"]["refreshPeriodMs"] == pytest.approx(16.67)
    assert report["presentMon"]["buckets"]["2"]["count"] == 20
    assert report["presentMon"]["buckets"]["1"]["count"] == 0


def test_all_2_refresh_without_nominal_is_an_error(tmp_path):
    # without the true refresh period, an all-2-refresh capture is indistinguishable
    # from a healthy all-1-refresh one from the interval distribution alone -- this
    # must fail closed rather than silently report 100% "1".
    csv_path = tmp_path / "presentmon-series.csv"
    values = [33.34] * 20
    _write_presentmon_csv(csv_path, values)

    log_path = tmp_path / "mlvapp.log"
    _write_frame_log(log_path, count=10, start=1)

    with pytest.raises(RefreshHistogramError, match="ambiguous"):
        build_report(str(csv_path), str(log_path))

    with pytest.raises(RefreshHistogramError, match="ambiguous"):
        compute_refresh_period(values)


# --- missing column / empty series are errors, never zeros -------------------------

def test_missing_presentmon_column_is_an_error(tmp_path):
    csv_path = tmp_path / "bad.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ordinal", "timeInMs", "presentMode"])
        writer.writerow([0, 0.0, "Hardware: Independent Flip"])

    with pytest.raises(RefreshHistogramError, match="missing required column"):
        parse_presentmon_intervals(str(csv_path))


def test_empty_presentmon_series_is_an_error(tmp_path):
    csv_path = tmp_path / "empty.csv"
    _write_presentmon_csv(csv_path, [])

    with pytest.raises(RefreshHistogramError, match="no positive"):
        parse_presentmon_intervals(str(csv_path))


def test_all_zero_presentmon_series_is_an_error(tmp_path):
    # zero/negative values are capture gaps, not zero-cost frames -- must not be
    # silently coerced into a "successful" empty-looking report
    csv_path = tmp_path / "zeros.csv"
    _write_presentmon_csv(csv_path, [0.0, 0.0, -1.0])

    with pytest.raises(RefreshHistogramError, match="no positive"):
        parse_presentmon_intervals(str(csv_path))


def test_empty_frame_log_is_an_error(tmp_path):
    log_path = tmp_path / "empty.log"
    log_path.write_text("nothing to see here\nno frame lines at all\n", encoding="utf-8")

    with pytest.raises(RefreshHistogramError, match="no playback_smoke.frame rows"):
        parse_frame_log_rows(str(log_path))


def test_too_few_frame_rows_is_an_error(tmp_path):
    log_path = tmp_path / "sparse.log"
    _write_frame_log(log_path, count=3)

    with pytest.raises(RefreshHistogramError, match="require >= 10"):
        parse_frame_log_rows(str(log_path))


def test_frame_row_missing_total_is_excluded_not_zeroed(tmp_path):
    log_path = tmp_path / "partial.log"
    with open(log_path, "w", encoding="utf-8") as fh:
        for i in range(24):
            # omit prep_region_total_ms/unattributed_ms on odd rows
            if i % 2 == 1:
                fh.write(
                    f"playback_smoke.frame session=1 index={i} prep_region_setup_ms=1.0\n"
                )
            else:
                fh.write(_frame_log_line(i, float(i + 1)))
                fh.write("\n")

    rows = parse_frame_log_rows(str(log_path))
    assert len(rows) == 12


def test_missing_region_field_is_an_error():
    # a row set that never carries prep_region_gpu_ms must fail, not report a 0.0
    rows = [
        {
            "prep_region_setup_ms": 1.0,
            "prep_region_image_ms": 1.0,
            "prep_region_present_ms": 1.0,
            "prep_region_finish_ms": 1.0,
            "prep_region_total_ms": 1.0,
            "prep_region_unattributed_ms": 0.1,
        }
        for _ in range(10)
    ]
    with pytest.raises(RefreshHistogramError, match="prep_region_gpu_ms"):
        compute_region_stats(rows)


# --- percentiles ---------------------------------------------------------------------

def test_percentile_matches_known_p50_p95():
    values = sorted(float(i) for i in range(1, 11))  # 1..10
    assert percentile(values, 0.50) == pytest.approx(5.5)
    assert percentile(values, 0.95) == pytest.approx(9.55)


def test_percentile_requires_at_least_one_value():
    with pytest.raises(RefreshHistogramError):
        percentile([], 0.5)


# --- end-to-end build_report ----------------------------------------------------------

def test_build_report_end_to_end(tmp_path):
    csv_path = tmp_path / "presentmon-series.csv"
    values = [16.67] * 12 + [33.34] * 5 + [50.01] * 3
    _write_presentmon_csv(csv_path, values)

    log_path = tmp_path / "mlvapp.log"
    _write_frame_log(log_path, count=10, start=1)

    report = build_report(str(csv_path), str(log_path))
    assert report["schema"] == "mlvapp.refresh-period-histogram.v1"
    assert report["presentMon"]["sampleCount"] == 20
    assert report["presentMon"]["buckets"]["1"]["count"] == 12
    assert report["frameLog"]["presentedFrameCount"] == 10
    for region in (
        "prep_region_setup", "prep_region_gpu", "prep_region_image",
        "prep_region_present", "prep_region_finish", "prep_region_total",
        "prep_region_unattributed",
    ):
        assert region in report["frameLog"]["regions"]
        assert report["frameLog"]["regions"][region]["p50Ms"] == pytest.approx(5.5)
        assert report["frameLog"]["regions"][region]["p95Ms"] == pytest.approx(9.55)
