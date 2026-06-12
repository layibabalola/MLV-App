"""Round-2 item 4: stall-tail stage attribution from playback_smoke.frame lines.

Parses per-frame telemetry from a trace log (latest session only is the
caller's responsibility - pass per-run logs), computes p50/p90/p99 per field,
and attributes the slowest-decile frames' render time to stages.
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
RX = re.compile(r"playback_smoke\.frame .*")


def percentile(sv, p):
    if not sv:
        return 0.0
    k = (len(sv) - 1) * p
    f = int(k)
    c = min(f + 1, len(sv) - 1)
    return sv[f] if f == c else sv[f] + (sv[c] - sv[f]) * (k - f)


def analyze(path):
    frames = []
    with open(path, errors="replace") as fh:
        for line in fh:
            m = RX.search(line)
            if not m:
                continue
            row = {}
            for kv in m.group(0).split():
                if "=" in kv:
                    k, _, v = kv.partition("=")
                    try:
                        row[k] = float(v)
                    except ValueError:
                        pass
            if "render_total_ms" in row:
                frames.append(row)

    print(f"\n=== {path}  (frames={len(frames)}) ===")
    print(f"{'field':<36}{'p50':>8}{'p90':>8}{'p99':>8}{'max':>8}")
    for f in FIELDS:
        vals = sorted(r.get(f, 0.0) for r in frames)
        if not vals or vals[-1] == 0.0:
            continue
        print(f"{f:<36}{percentile(vals, .5):>8.1f}{percentile(vals, .9):>8.1f}"
              f"{percentile(vals, .99):>8.1f}{vals[-1]:>8.1f}")

    # slowest decile by render_total: average stage split inside those frames
    frames.sort(key=lambda r: r.get("render_total_ms", 0.0))
    slow = frames[int(len(frames) * 0.9):]
    if slow:
        n = len(slow)
        avg = lambda f: sum(r.get(f, 0.0) for r in slow) / n
        rt = avg("render_total_ms") or 1.0
        # decode is the remainder of render work after llrawproc+processed8
        decode = avg("render_work_ms") - avg("llrawproc_ms") - avg("processed8_ms")
        print(f"slowest-decile (n={n}) render_total avg {rt:.1f} ms split:")
        print(f"  decode+debayer+scale (residual) {max(decode, 0.0):6.1f} ms  {100 * max(decode, 0.0) / rt:5.1f}%")
        print(f"  llrawproc (recon etc.)          {avg('llrawproc_ms'):6.1f} ms  {100 * avg('llrawproc_ms') / rt:5.1f}%")
        print(f"  processed8 (processing+pack)    {avg('processed8_ms'):6.1f} ms  {100 * avg('processed8_ms') / rt:5.1f}%")
        print(f"  queue_wait                      {avg('queue_wait_ms'):6.1f} ms")
        print(f"  draw_total (present side)       {avg('draw_total_ms'):6.1f} ms")
        print(f"  worst interval in decile        {max(r.get('interval_ms', 0.0) for r in slow):6.1f} ms")


for p in sys.argv[1:]:
    analyze(p)
