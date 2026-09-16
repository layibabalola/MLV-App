"""PLAYBACK-ATTR-3-CUDA: within-run refresh-period histogram + prep-region attribution.

Bachelor cannot resolve a timing A/B below about 2x (A/A noise), so this run is judged
WITHIN ONE RUN rather than against a baseline: the refresh-period histogram (the share
of presented frames landing on 1, 2, or 3+ display refreshes), plus the presented-frame
count and per-sub-region prep timing (prep_region_setup/gpu/image/present/finish/total/
unattributed).

Inputs:
  - a PresentMon series CSV with a `msBetweenDisplayChange` column (the derived
    presentmon-series.csv the bachelor attribution job exports, one row per PresentMon
    sample; see tools/profiling/bachelor/playback-attr-3-cuda-job.ps1).
  - the raw MLVApp log containing `playback_smoke.frame ...` lines with the
    `prep_region_*_ms` probes (always compiled in; see platform/qt/MainWindow.cpp
    near the `playback_smoke.frame` qInfo() call).

A missing required column/field is always an error, never treated as zero -- a silent
zero would misreport a broken capture as "no cost" instead of "not measured".
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from typing import Iterable, Sequence

SCHEMA = "mlvapp.refresh-period-histogram.v1"

PRESENTMON_INTERVAL_COLUMN = "msBetweenDisplayChange"

PREP_REGIONS = (
    "prep_region_setup",
    "prep_region_gpu",
    "prep_region_image",
    "prep_region_present",
    "prep_region_finish",
    "prep_region_total",
    "prep_region_unattributed",
)

FRAME_LINE_RX = re.compile(r"playback_smoke\.frame\s")
KV_RX = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>[^\s]+)")

# Refresh-multiple rounding rule (stated explicitly per the card's requirement):
#   multiple = floor(interval_ms / refresh_period_ms + 0.5)   (round-half-up)
#   clamped to a minimum of 1 (an interval can never be < 1 refresh, only noisy)
#   multiples >= 3 are grouped into the "3+" bucket.
ROUNDING_RULE = (
    "multiple = floor(intervalMs / refreshPeriodMs + 0.5) [round-half-up], "
    "clamped to a minimum of 1; multiples >= 3 are grouped into the '3+' bucket"
)


class RefreshHistogramError(ValueError):
    """Raised when an input is malformed, missing a required column, or empty."""


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def refresh_multiple(interval_ms: float, refresh_period_ms: float) -> int:
    if refresh_period_ms <= 0:
        raise RefreshHistogramError(f"refreshPeriodMs must be positive, got {refresh_period_ms!r}")
    multiple = _round_half_up(interval_ms / refresh_period_ms)
    return max(multiple, 1)


def bucket_label(multiple: int) -> str:
    if multiple <= 0:
        raise RefreshHistogramError(f"refresh multiple must be >= 1, got {multiple!r}")
    return "3+" if multiple >= 3 else str(multiple)


def percentile(sorted_values: Sequence[float], p: float) -> float:
    if not sorted_values:
        raise RefreshHistogramError("percentile requires at least one value")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * p
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(sorted_values[int(lo)])
    frac = rank - lo
    return float(sorted_values[int(lo)] + (sorted_values[int(hi)] - sorted_values[int(lo)]) * frac)


def parse_presentmon_intervals(path: str) -> list[float]:
    """Read msBetweenDisplayChange from a PresentMon series CSV. Positive values only
    (mirrors the bachelor job's own filter -- non-positive/unparseable rows are gaps
    in the capture, not zero-cost frames)."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or PRESENTMON_INTERVAL_COLUMN not in reader.fieldnames:
            raise RefreshHistogramError(
                f"PresentMon CSV {path!r} is missing required column "
                f"{PRESENTMON_INTERVAL_COLUMN!r}; found {reader.fieldnames!r}"
            )
        intervals: list[float] = []
        for row in reader:
            raw = row.get(PRESENTMON_INTERVAL_COLUMN)
            if raw is None or raw.strip() == "":
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if value > 0:
                intervals.append(value)
    if not intervals:
        raise RefreshHistogramError(
            f"PresentMon CSV {path!r} has no positive {PRESENTMON_INTERVAL_COLUMN} samples"
        )
    return intervals


def parse_frame_log_rows(path: str, min_rows: int = 10) -> list[dict[str, float]]:
    """Read prep_region_* fields from playback_smoke.frame lines. A row is kept only
    when it carries prep_region_total_ms AND prep_region_unattributed_ms (mirrors the
    bachelor attribution job's own gate for a "high-resolution" frame row)."""
    rows: list[dict[str, float]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not FRAME_LINE_RX.search(line):
                continue
            values: dict[str, float] = {}
            for match in KV_RX.finditer(line):
                key = match.group("key")
                if key not in PREP_REGIONS and not key.startswith("prep_region_"):
                    continue
                try:
                    values[key] = float(match.group("value"))
                except ValueError:
                    continue
            if "prep_region_total_ms" in values and "prep_region_unattributed_ms" in values:
                rows.append(values)
    if not rows:
        raise RefreshHistogramError(
            f"frame log {path!r} has no playback_smoke.frame rows carrying prep_region_total_ms "
            "and prep_region_unattributed_ms"
        )
    if len(rows) < min_rows:
        raise RefreshHistogramError(
            f"frame log {path!r} has only {len(rows)} high-resolution frame rows; require >= {min_rows}"
        )
    return rows


def compute_refresh_period(intervals: Sequence[float]) -> dict:
    """Measure the true monitor refresh period from the interval distribution.

    Isolates the "1-refresh" cluster (samples within 50% of the observed minimum
    interval -- the cheapest, most common case on a healthy capture) and reports the
    MODE of that cluster (values rounded to 2 decimal places for grouping) when a
    clear plurality exists (the mode's count is strictly greater than 1 and strictly
    greater than the runner-up); otherwise falls back to the cluster's MEDIAN. Which
    method was actually used is reported in `measurement` so the number is never
    presented as more certain than it is.
    """
    if not intervals:
        raise RefreshHistogramError("compute_refresh_period requires at least one interval")
    min_interval = min(intervals)
    cluster = [v for v in intervals if v <= min_interval * 1.5]
    if not cluster:
        raise RefreshHistogramError("no samples found near the minimum observed interval")

    rounded = [round(v, 2) for v in cluster]
    counts = Counter(rounded)
    ranked = counts.most_common(2)
    top_value, top_count = ranked[0]
    runner_up_count = ranked[1][1] if len(ranked) > 1 else 0
    if top_count > 1 and top_count > runner_up_count:
        refresh_period_ms = float(top_value)
        measurement = "mode"
    else:
        sorted_cluster = sorted(cluster)
        refresh_period_ms = percentile(sorted_cluster, 0.5)
        measurement = "median"

    return {
        "refreshPeriodMs": refresh_period_ms,
        "measurement": measurement,
        "clusterSampleCount": len(cluster),
        "minObservedIntervalMs": min_interval,
    }


def compute_buckets(intervals: Sequence[float], refresh_period_ms: float) -> dict:
    counts: Counter[str] = Counter()
    for value in intervals:
        multiple = refresh_multiple(value, refresh_period_ms)
        counts[bucket_label(multiple)] += 1
    total = len(intervals)
    buckets = {}
    for label in ("1", "2", "3+"):
        count = counts.get(label, 0)
        buckets[label] = {"count": count, "share": count / total}
    return buckets


def compute_region_stats(frame_rows: Sequence[dict[str, float]]) -> dict:
    regions = {}
    for region in PREP_REGIONS:
        key = region + "_ms"
        values = sorted(row[key] for row in frame_rows if key in row)
        if not values:
            raise RefreshHistogramError(f"no frame rows carry {key!r}")
        regions[region] = {
            "p50Ms": percentile(values, 0.50),
            "p95Ms": percentile(values, 0.95),
            "count": len(values),
        }
    return regions


def build_report(presentmon_csv_path: str, frame_log_path: str, min_frame_rows: int = 10) -> dict:
    intervals = parse_presentmon_intervals(presentmon_csv_path)
    frame_rows = parse_frame_log_rows(frame_log_path, min_rows=min_frame_rows)

    refresh_period = compute_refresh_period(intervals)
    buckets = compute_buckets(intervals, refresh_period["refreshPeriodMs"])
    regions = compute_region_stats(frame_rows)

    return {
        "schema": SCHEMA,
        "presentMon": {
            "sourceFile": presentmon_csv_path,
            "sampleCount": len(intervals),
            "refreshPeriodMs": refresh_period["refreshPeriodMs"],
            "refreshPeriodMeasurement": refresh_period["measurement"],
            "refreshPeriodClusterSampleCount": refresh_period["clusterSampleCount"],
            "minObservedIntervalMs": refresh_period["minObservedIntervalMs"],
            "roundingRule": ROUNDING_RULE,
            "buckets": buckets,
        },
        "frameLog": {
            "sourceFile": frame_log_path,
            "presentedFrameCount": len(frame_rows),
            "regions": regions,
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presentmon-csv", required=True, help="PresentMon series CSV (msBetweenDisplayChange column)")
    parser.add_argument("--frame-log", required=True, help="Raw MLVApp log with playback_smoke.frame lines")
    parser.add_argument("--out", default="", help="Write JSON here instead of stdout")
    parser.add_argument("--min-frame-rows", type=int, default=10, help="Minimum high-resolution frame rows required")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = build_report(args.presentmon_csv, args.frame_log, min_frame_rows=args.min_frame_rows)
    except RefreshHistogramError as exc:
        print(f"refresh_period_histogram: FAIL: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.write("\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
