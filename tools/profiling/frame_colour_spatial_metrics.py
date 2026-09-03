#!/usr/bin/env python3
"""Colour + spatial DESCRIPTION of an MLVApp presented frame. NOT A GATE.

WHY THIS EXISTS
    detect-playback-artifacts.ps1 checks FLICKER, STALL, JITTER and FROZEN CONTENT - all
    TEMPORAL. Measured 2026-09-03: it returned PASS (67 presents, 59 distinct content hashes,
    frozen_content_runs=0) on a frame that looked, to the eye, to have a heavy blue cast and
    vertical striping. Colour and spatial correctness are simply not in its scope, so a cast or
    a per-column artifact would report PASS forever.

WHAT THIS IS NOT
    It is NOT a second gate. tools/gates/output_budget.py (PR #4,
    codex/shipping-output-guard-20260818) is the fail-closed A/B gate with full-frame comparison
    and it stays the authority. Two tools for one job is how pointers go stale. This one only
    DESCRIBES a frame in numbers, so that when a difference exists someone can say HOW it differs
    instead of arguing about a screenshot.

THE RULE IT ENCODES
    A CAST IS ONLY A CAST RELATIVE TO SOMETHING. Run it on a reference arm and a subject arm from
    the SAME run series and compare. On 2026-09-03 a single-frame reading of B/R=2.38 looked like
    a strong blue cast; the pre-PR#18 reference build on the same clip read B/R=2.407 - MARGINALLY
    BLUER - so the cast was scene content (a swimming pool) plus no-WB raw preview, not a
    regression. One frame with no reference is an opinion, and an agent judging blinded image
    pairs has been measured on this fleet at p=0.36 with confidence running backwards.

THE ROW ARM IS A CONTROL, NOT DECORATION
    Striping claimed to be VERTICAL must show in the column statistics and NOT in the row ones.
    In that same comparison the column odd/even delta rose 0.0442 -> 0.0700, which reads as
    "striping got worse" until you notice the ROW control rose by the same factor and the
    column:row ratio was 8.5 in both arms. Report the ratio, never the column figure alone.

USAGE
    python frame_colour_spatial_metrics.py REFERENCE.png SUBJECT.png [...]
    Requires Pillow + numpy. Exits 0 always: this DESCRIBES, it does not judge.
"""
import sys, numpy as np
from PIL import Image

def metrics(path):
    im = Image.open(path).convert('RGB')
    a = np.asarray(im).astype(np.float64)
    h, w, _ = a.shape
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    out = {'path': path, 'w': w, 'h': h}
    out['meanR'], out['meanG'], out['meanB'] = R.mean(), G.mean(), B.mean()
    # Cast indicators. A neutral-ish daylight frame sits near 1.0; >1 means blue-dominant.
    out['B_over_R'] = B.mean() / max(R.mean(), 1e-9)
    out['B_over_G'] = B.mean() / max(G.mean(), 1e-9)
    # Grey-world residual: how far the channel means are from equal, as a fraction.
    m = np.array([R.mean(), G.mean(), B.mean()])
    out['greyworld_spread'] = float((m.max() - m.min()) / max(m.mean(), 1e-9))

    # VERTICAL STRIPING: collapse to a column profile on luma, then look for high-frequency
    # column-to-column alternation. Real scene content is smooth column-to-column; per-column
    # artifacts are not. Reported as the fraction of column-profile energy above Nyquist/2.
    luma = 0.2126 * R + 0.7152 * G + 0.0722 * B
    col = luma.mean(axis=0)
    col = col - col.mean()
    spec = np.abs(np.fft.rfft(col)) ** 2
    n = len(spec)
    out['col_hf_energy_frac'] = float(spec[n // 2:].sum() / max(spec[1:].sum(), 1e-9))
    # Odd-even column mean difference: a direct read of 1-pixel-period striping.
    out['col_odd_even_delta'] = float(abs(luma[:, 0::2].mean() - luma[:, 1::2].mean()))
    # Same for rows, as a CONTROL. Striping claimed to be vertical should not show in rows.
    row = luma.mean(axis=1); row = row - row.mean()
    rspec = np.abs(np.fft.rfft(row)) ** 2; rn = len(rspec)
    out['row_hf_energy_frac'] = float(rspec[rn // 2:].sum() / max(rspec[1:].sum(), 1e-9))
    out['row_odd_even_delta'] = float(abs(luma[0::2, :].mean() - luma[1::2, :].mean()))
    return out

def main(argv):
    for p in argv:
        try:
            d = metrics(p)
        except Exception as e:
            print(f"{p}: ERROR {e}"); continue
        print(f"\n== {d['path'].split(chr(92))[-1]}  {d['w']}x{d['h']} ==")
        print(f"  mean RGB           {d['meanR']:7.2f} {d['meanG']:7.2f} {d['meanB']:7.2f}")
        print(f"  B/R  B/G           {d['B_over_R']:7.3f} {d['B_over_G']:7.3f}   (1.0 = neutral)")
        print(f"  greyworld spread   {d['greyworld_spread']:7.3f}")
        print(f"  COLUMN hf frac     {d['col_hf_energy_frac']:7.4f}   odd/even delta {d['col_odd_even_delta']:7.4f}")
        print(f"  ROW    hf frac     {d['row_hf_energy_frac']:7.4f}   odd/even delta {d['row_odd_even_delta']:7.4f}  <- control")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
