"""Round-2 item 4: stall-tail stage attribution from playback_smoke.frame lines.

Parses per-frame telemetry from a trace log (latest session only is the
caller's responsibility - pass per-run logs), computes p50/p90/p99 per field,
and attributes the slowest-decile frames' render time to stages.

CUDA-ATTRIBUTION-BASELINE-1 round 11 (sol MINOR, "the legacy analyzer reads
absent timing fields as zero"): a FIELDS key simply absent from a
playback_smoke.frame record means the probe did not run for that frame, not
that it measured 0.0 -- `row.get(f, 0.0)` used to silently zero-fill it,
corrupting every percentile/average that field feeds. Now only frames where
a field is actually PRESENT feed that field's stats; absent frames are
excluded and counted (the printed `n`/`unavail` columns), not zero-filled.
Also joins the companion playback_smoke.timing_validity record (by
session+index, MainWindow.cpp's playback_smoke.timing_validity emitter) so
the two derived-residual fields (processed16/8_threading_overhead_ms) are
labelled "(derived)" rather than read as an independent measurement.
"""
import re
import sys
from collections import defaultdict

FIELDS = [
    "interval_ms", "render_total_ms", "render_work_ms", "queue_wait_ms",
    "llrawproc_ms", "processed8_ms", "draw_total_ms",
    "present_ui_signal_latency_ms", "present_draw_present_ms",
    "present_overlays_scopes_ms", "present_render_slot_release_ms",
    "present_pacing_ms",
    "processed16_setup_ms", "processed16_core_math_ms",
    "processed16_local_tone_ms", "processed16_threading_overhead_ms",
    "processed8_setup_ms", "processed8_core_math_ms",
    "processed8_local_tone_ms", "processed8_threading_overhead_ms",
]
FRAME_RX = re.compile(r"playback_smoke\.frame (.*)")
TIMING_VALIDITY_RX = re.compile(r"playback_smoke\.timing_validity (.*)")

# The two FIELDS entries that playback_smoke.timing_validity documents as a
# derived_subtraction residual, and the basis key that names that -- see
# MainWindow.cpp's timing_validity emitter.
DERIVED_BASIS_FIELDS = {
    "processed16_threading_overhead_ms": "processed16_threading_overhead_basis",
    "processed8_threading_overhead_ms": "processed8_threading_overhead_basis",
}


def percentile(sv, p):
    if not sv:
        return 0.0
    k = (len(sv) - 1) * p
    f = int(k)
    c = min(f + 1, len(sv) - 1)
    return sv[f] if f == c else sv[f] + (sv[c] - sv[f]) * (k - f)


def parse_kv_line(text):
    """Parse a whitespace-separated key=value line. A value that parses as a
    float is stored as one; anything else (e.g. a basis label like
    "derived_subtraction") is kept as the raw string, so callers that need
    only the numeric fields must filter for that themselves."""
    row = {}
    for kv in text.split():
        if "=" not in kv:
            continue
        k, _, v = kv.partition("=")
        try:
            row[k] = float(v)
        except ValueError:
            row[k] = v
    return row


def frame_key(row):
    if "session" not in row or "index" not in row:
        return None
    return (row["session"], row["index"])


def parse_log(lines):
    """Parse an iterable of log lines into (frames, validity_by_key).

    frames: list of {field: float} dicts, numeric-only, one per
      playback_smoke.frame line that carries render_total_ms.
    validity_by_key: {(session, index): {field: float|str}} from the
      companion playback_smoke.timing_validity lines.
    """
    frames = []
    validity_by_key = {}
    for line in lines:
        vm = TIMING_VALIDITY_RX.search(line)
        if vm:
            vrow = parse_kv_line(vm.group(1))
            key = frame_key(vrow)
            if key is not None:
                validity_by_key[key] = vrow
            continue
        m = FRAME_RX.search(line)
        if not m:
            continue
        row = parse_kv_line(m.group(1))
        numeric_row = {k: v for k, v in row.items() if isinstance(v, float)}
        if "render_total_ms" in numeric_row:
            frames.append(numeric_row)
    return frames, validity_by_key


def analyze(path):
    with open(path, errors="replace") as fh:
        frames, validity_by_key = parse_log(fh)

    print(f"\n=== {path}  (frames={len(frames)}) ===")
    print(f"{'field':<40}{'p50':>8}{'p90':>8}{'p99':>8}{'max':>8}{'n':>6}{'unavail':>9}")
    for f in FIELDS:
        present = [r for r in frames if f in r]
        unavailable = len(frames) - len(present)
        vals = sorted(r[f] for r in present)
        if not vals or vals[-1] == 0.0:
            continue
        label = f
        basis_key = DERIVED_BASIS_FIELDS.get(f)
        if basis_key:
            bases = {
                validity_by_key.get(frame_key(r), {}).get(basis_key)
                for r in present
            }
            if bases == {"derived_subtraction"}:
                label = f + " (derived)"
        print(f"{label:<40}{percentile(vals, .5):>8.1f}{percentile(vals, .9):>8.1f}"
              f"{percentile(vals, .99):>8.1f}{vals[-1]:>8.1f}{len(present):>6}{unavailable:>9}")

    # slowest decile by render_total: average stage split inside those frames
    frames.sort(key=lambda r: r.get("render_total_ms", 0.0))
    slow = frames[int(len(frames) * 0.9):]
    if slow:
        n = len(slow)

        def avg(f):
            present = [r[f] for r in slow if f in r]
            return (sum(present) / len(present)) if present else None

        rt = avg("render_total_ms") or 1.0
        decode_render = avg("render_work_ms")
        decode = (
            decode_render - (avg("llrawproc_ms") or 0.0) - (avg("processed8_ms") or 0.0)
            if decode_render is not None else None
        )
        print(f"slowest-decile (n={n}) render_total avg {rt:.1f} ms split:")

        def report(label, value, as_percent=True):
            if value is None:
                print(f"  {label:<32} unavailable")
                return
            pct = f"  {100 * value / rt:5.1f}%" if as_percent else ""
            print(f"  {label:<32}{value:6.1f} ms{pct}")

        report("decode+debayer+scale (residual)", max(decode, 0.0) if decode is not None else None)
        report("llrawproc (recon etc.)", avg("llrawproc_ms"))
        report("processed8 (processing+pack)", avg("processed8_ms"))
        report("queue_wait", avg("queue_wait_ms"), as_percent=False)
        report("draw_total (present side)", avg("draw_total_ms"), as_percent=False)
        worst_intervals = [r["interval_ms"] for r in slow if "interval_ms" in r]
        if worst_intervals:
            print(f"  worst interval in decile        {max(worst_intervals):6.1f} ms")
        else:
            print("  worst interval in decile        unavailable")


def main(argv):
    for p in argv:
        analyze(p)


if __name__ == "__main__":
    main(sys.argv[1:])
