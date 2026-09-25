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


# Ambiguity guard (histogram ambiguity fix): the minimum-interval estimate assumes the
# observed minimum IS one true refresh. That assumption is unverifiable -- and wrong --
# when the whole capture is uniformly N refreshes per frame (an all-2-refresh capture:
# every interval sits near the same value, so the "cluster" is the entire series and
# nothing in the interval distribution itself distinguishes that from a healthy
# all-1-refresh capture). The only interval-only signal available is whether a
# meaningfully-sized population of samples sits OUTSIDE the near-minimum cluster (i.e.
# genuinely different, higher-multiple frames) to cross-check the assumption against.
MIN_OUTSIDE_CLUSTER_SHARE = 0.05


def compute_refresh_period(intervals: Sequence[float]) -> dict:
    """Measure the true monitor refresh period from the interval distribution.

    Isolates the "1-refresh" cluster (samples within 50% of the observed minimum
    interval -- the cheapest, most common case on a healthy capture) and reports the
    MODE of that cluster (values rounded to 2 decimal places for grouping) when a
    clear plurality exists (the mode's count is strictly greater than 1 and strictly
    greater than the runner-up); otherwise falls back to the cluster's MEDIAN. Which
    method was actually used is reported in `measurement` so the number is never
    presented as more certain than it is.

    Raises when fewer than `MIN_OUTSIDE_CLUSTER_SHARE` of all intervals sit outside the
    near-minimum cluster: with too little of the distribution to contrast the cluster
    against, an all-N-refresh capture is indistinguishable from a healthy all-1-refresh
    one (the bug this guard exists to close -- see module docstring). Callers who can
    supply the display's actual nominal refresh period should bucket against that
    instead of calling this estimator at all.
    """
    if not intervals:
        raise RefreshHistogramError("compute_refresh_period requires at least one interval")
    min_interval = min(intervals)
    cluster = [v for v in intervals if v <= min_interval * 1.5]
    if not cluster:
        raise RefreshHistogramError("no samples found near the minimum observed interval")

    outside_cluster_share = 1.0 - (len(cluster) / len(intervals))
    if outside_cluster_share < MIN_OUTSIDE_CLUSTER_SHARE:
        raise RefreshHistogramError(
            f"refresh period is ambiguous: only {outside_cluster_share * 100:.1f}% of "
            f"{len(intervals)} intervals fall outside the near-minimum cluster (< "
            f"{MIN_OUTSIDE_CLUSTER_SHARE * 100:.0f}% required), so minObservedIntervalMs="
            f"{min_interval!r} cannot be confirmed as one true display refresh -- this "
            "capture may be uniformly N refreshes per frame (e.g. an all-2-refresh "
            "capture reads as 100% 1-refresh under the minimum-interval assumption). "
            "Pass --refresh-period-ms with the display's actual nominal refresh period "
            "to resolve the ambiguity."
        )

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
        "outsideClusterShare": outside_cluster_share,
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


def compute_deadline_evaluation(
    intervals: Sequence[float],
    target_fps: float,
    tolerance_multiplier: float = 1.5,
) -> dict:
    """Evaluate presented-frame intervals against the INTENDED playback cadence.

    CUDA-ATTRIBUTION-BASELINE-1 (arbiter finding, tools/profiling/refresh_period_histogram.py:33,:48
    in the pre-fix source): a refresh-multiple histogram alone establishes neither
    a missed deadline nor causality -- two refreshes per presented frame can be
    exactly correct at a target FPS below the display's refresh rate (e.g. a 30fps
    target on a 60Hz panel is SUPPOSED to land near 2 refreshes/frame; that is not
    lateness). This function evaluates each interval against the INTENDED
    per-frame period (1000/target_fps ms) instead, independent of the refresh
    multiple, and reports the expected refreshes/frame separately so a caller can
    tell "healthy N-refresh cadence" from "missed deadline" instead of conflating
    them into one bucket count.
    """
    if target_fps <= 0:
        raise RefreshHistogramError(f"targetFps must be positive, got {target_fps!r}")
    if math.isnan(target_fps) or math.isinf(target_fps):
        raise RefreshHistogramError(f"targetFps must be a finite positive number, got {target_fps!r}")
    # CUDA-ATTRIBUTION-BASELINE-1 round 2 (astra minor finding): NaN and
    # +/-infinity both satisfy `not (x <= 1.0)` being False -- i.e. they slip
    # past a `tolerance_multiplier <= 1.0` check silently, exactly like the
    # target_fps NaN/inf check two lines above this one exists to catch.
    # An infinite tolerance makes deadline_ms infinite, so every interval
    # below is "not missed" and this would silently report a false
    # missedDeadlineCount of 0 instead of rejecting the input.
    if math.isnan(tolerance_multiplier) or math.isinf(tolerance_multiplier):
        raise RefreshHistogramError(
            f"toleranceMultiplier must be a finite number, got {tolerance_multiplier!r}"
        )
    if tolerance_multiplier <= 1.0:
        raise RefreshHistogramError(
            f"toleranceMultiplier must be > 1.0 (it multiplies the intended period to "
            f"define a miss), got {tolerance_multiplier!r}"
        )
    if not intervals:
        raise RefreshHistogramError("compute_deadline_evaluation requires at least one interval")

    intended_period_ms = 1000.0 / target_fps
    deadline_ms = intended_period_ms * tolerance_multiplier
    missed = [v for v in intervals if v > deadline_ms]
    sorted_intervals = sorted(intervals)

    return {
        "targetFps": target_fps,
        "intendedPeriodMs": intended_period_ms,
        "toleranceMultiplier": tolerance_multiplier,
        "deadlineMs": deadline_ms,
        "sampleCount": len(intervals),
        "missedDeadlineCount": len(missed),
        "missedDeadlineShare": len(missed) / len(intervals),
        "p50IntervalMs": percentile(sorted_intervals, 0.50),
        "p95IntervalMs": percentile(sorted_intervals, 0.95),
        "p99IntervalMs": percentile(sorted_intervals, 0.99),
    }


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


def build_report(
    presentmon_csv_path: str,
    frame_log_path: str,
    min_frame_rows: int = 10,
    refresh_period_ms: float | None = None,
    target_fps: float | None = None,
    source_fps: float | None = None,
    deadline_tolerance_multiplier: float = 1.5,
) -> dict:
    intervals = parse_presentmon_intervals(presentmon_csv_path)
    frame_rows = parse_frame_log_rows(frame_log_path, min_rows=min_frame_rows)

    if refresh_period_ms is not None:
        # Nominal supplied (e.g. Bachelor's panel refresh, per the runbook): bucket
        # against it directly -- no min-interval estimate, no ambiguity guard, because
        # the true refresh period is now known rather than inferred.
        # NaN/inf must be rejected explicitly: `nan <= 0` is False and `inf <= 0` is
        # False, so a bare positivity check silently lets both through to
        # compute_buckets, where they would corrupt every bucket's refresh multiple.
        if math.isnan(refresh_period_ms) or math.isinf(refresh_period_ms):
            raise RefreshHistogramError(
                f"refreshPeriodMs must be a finite positive number, got {refresh_period_ms!r}"
            )
        if refresh_period_ms <= 0:
            raise RefreshHistogramError(f"refreshPeriodMs must be positive, got {refresh_period_ms!r}")
        refresh_period = {
            "refreshPeriodMs": float(refresh_period_ms),
            "measurement": "nominal",
            "clusterSampleCount": None,
            "minObservedIntervalMs": None,
            "refreshPeriodSource": "nominal",
        }
    else:
        refresh_period = dict(compute_refresh_period(intervals))
        refresh_period["refreshPeriodSource"] = "estimated-min-interval"

    buckets = compute_buckets(intervals, refresh_period["refreshPeriodMs"])
    regions = compute_region_stats(frame_rows)

    # CUDA-ATTRIBUTION-BASELINE-1: deadlines, not buckets (arbiter finding).
    # Only computed when the caller supplies the intended playback cadence --
    # without it there is no "deadline" to evaluate against, only a refresh
    # histogram, which is exactly the ambiguity this section exists to resolve.
    deadline_evaluation = None
    if target_fps is not None:
        deadline_evaluation = compute_deadline_evaluation(
            intervals, target_fps, tolerance_multiplier=deadline_tolerance_multiplier
        )
        expected_refreshes_per_frame = deadline_evaluation["intendedPeriodMs"] / refresh_period["refreshPeriodMs"]
        deadline_evaluation["expectedRefreshesPerFrame"] = expected_refreshes_per_frame
        deadline_evaluation["sourceFps"] = source_fps

    return {
        "schema": SCHEMA,
        "presentMon": {
            "sourceFile": presentmon_csv_path,
            "sampleCount": len(intervals),
            "refreshPeriodMs": refresh_period["refreshPeriodMs"],
            "refreshPeriodMeasurement": refresh_period["measurement"],
            "refreshPeriodSource": refresh_period["refreshPeriodSource"],
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
        "deadlineEvaluation": deadline_evaluation,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presentmon-csv", required=True, help="PresentMon series CSV (msBetweenDisplayChange column)")
    parser.add_argument("--frame-log", required=True, help="Raw MLVApp log with playback_smoke.frame lines")
    parser.add_argument("--out", default="", help="Write JSON here instead of stdout")
    parser.add_argument("--min-frame-rows", type=int, default=10, help="Minimum high-resolution frame rows required")
    parser.add_argument(
        "--refresh-period-ms",
        type=float,
        default=None,
        help=(
            "Nominal display refresh period in ms (the runbook requires supplying "
            "Bachelor's panel refresh). When given, buckets are computed against this "
            "value directly. When omitted, the refresh period is estimated from the "
            "interval distribution's near-minimum cluster, which fails closed if the "
            "capture is too ambiguous to trust (see compute_refresh_period)."
        ),
    )
    parser.add_argument(
        "--target-fps",
        type=float,
        default=None,
        help=(
            "Intended playback cadence in frames/sec (CUDA-ATTRIBUTION-BASELINE-1: "
            "deadlines, not buckets). When given, evaluates every PresentMon interval "
            "against the INTENDED per-frame period (1000/target-fps ms) instead of only "
            "the refresh-multiple histogram -- a refresh multiple above 1 can be exactly "
            "correct at a target FPS below the display's refresh rate, so that alone "
            "does not establish a missed deadline."
        ),
    )
    parser.add_argument(
        "--source-fps",
        type=float,
        default=None,
        help="Clip's native frame rate, recorded alongside targetFps for context (not used in the deadline math).",
    )
    parser.add_argument(
        "--deadline-tolerance-multiplier",
        type=float,
        default=1.5,
        help="An interval counts as a missed deadline when it exceeds intendedPeriodMs * this multiplier (default 1.5).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = build_report(
            args.presentmon_csv,
            args.frame_log,
            min_frame_rows=args.min_frame_rows,
            refresh_period_ms=args.refresh_period_ms,
            target_fps=args.target_fps,
            source_fps=args.source_fps,
            deadline_tolerance_multiplier=args.deadline_tolerance_multiplier,
        )
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
