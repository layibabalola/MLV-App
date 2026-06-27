#!/usr/bin/env python3
"""Structural metrics for the dual-ISO full-res recon review gate.

Emits JSON (one object per image path argument) with:
  hline         mean|diff(luma, axis=0)|  -- row-to-row energy (interlace/row-alternation)
  vline         mean|diff(luma, axis=1)|  -- col-to-col energy
  chromaSpeckle mean|diff(gm,0)|+mean|diff(gm,1)|, gm = g-0.5(r+b)  -- green/magenta speckle

These are CONTENT-DEPENDENT in absolute terms (the documented trap: a busy clean
frame can read high). The review gate therefore compares scale=1 against scale=2 of
the SAME frame, so identical scene content cancels and only the full-res recon
artifact remains in the ratio. Laplacian variance is deliberately avoided -- it reads
grids/interlace as sharpness and inverts the verdict.
"""
import sys
import json
import numpy as np
from PIL import Image


def metrics(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float64)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    gm = g - 0.5 * (r + b)
    return {
        "path": path,
        "width": int(a.shape[1]),
        "height": int(a.shape[0]),
        "hline": float(np.mean(np.abs(np.diff(luma, axis=0)))),
        "vline": float(np.mean(np.abs(np.diff(luma, axis=1)))),
        "chromaSpeckle": float(
            np.mean(np.abs(np.diff(gm, axis=0))) + np.mean(np.abs(np.diff(gm, axis=1)))
        ),
    }


def main():
    out = []
    for p in sys.argv[1:]:
        try:
            out.append(metrics(p))
        except Exception as e:  # noqa: BLE001
            out.append({"path": p, "error": str(e)})
    print(json.dumps(out))


if __name__ == "__main__":
    main()
