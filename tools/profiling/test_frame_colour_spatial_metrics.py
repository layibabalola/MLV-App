#!/usr/bin/env python3
"""Falsifier tests for frame_colour_spatial_metrics.

A metric nobody has fed a KNOWN defect is not a measurement, it is a hope. Each test builds a
synthetic frame whose defect is known by construction and asserts the metric both FIRES on it and
STAYS QUIET on a neutral control - a check that only ever sees clean input proves nothing.
"""
import numpy as np
from PIL import Image
import tempfile, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frame_colour_spatial_metrics import metrics


def _write(a):
    f = os.path.join(tempfile.mkdtemp(), "f.png")
    Image.fromarray(a.astype(np.uint8), "RGB").save(f)
    return f


def _neutral(w=256, h=128, seed=0):
    rng = np.random.default_rng(seed)
    base = rng.normal(128, 12, (h, w))
    return np.stack([base, base, base], axis=2).clip(0, 255)


def test_neutral_control_is_quiet():
    m = metrics(_write(_neutral()))
    assert abs(m["B_over_R"] - 1.0) < 0.02, m["B_over_R"]
    assert m["greyworld_spread"] < 0.02, m["greyworld_spread"]
    assert m["col_odd_even_delta"] < 0.5, m["col_odd_even_delta"]


def test_blue_cast_is_detected_and_neutral_is_not():
    a = _neutral()
    a[:, :, 2] *= 1.6           # blue up, red untouched: a cast by construction
    a[:, :, 0] *= 0.8
    cast = metrics(_write(a.clip(0, 255)))
    ctrl = metrics(_write(_neutral()))
    assert cast["B_over_R"] > 1.8, cast["B_over_R"]
    assert ctrl["B_over_R"] < 1.05, ctrl["B_over_R"]
    assert cast["B_over_R"] > ctrl["B_over_R"] * 1.7


def test_vertical_striping_fires_on_columns_and_not_on_the_row_control():
    a = _neutral()
    a[:, 0::2, :] += 10          # every other COLUMN brighter: vertical striping by construction
    m = metrics(_write(a.clip(0, 255)))
    ctrl = metrics(_write(_neutral()))
    # The column arm must fire...
    assert m["col_odd_even_delta"] > 8.0, m["col_odd_even_delta"]
    # ...the row arm must NOT: that is what makes the finding DIRECTIONAL.
    assert m["row_odd_even_delta"] < 1.0, m["row_odd_even_delta"]
    assert m["col_odd_even_delta"] > m["row_odd_even_delta"] * 8
    assert ctrl["col_odd_even_delta"] < 0.5


def test_horizontal_striping_does_not_masquerade_as_vertical():
    """The trap this arm exists for: a defect in the OTHER axis must not read as vertical."""
    a = _neutral()
    a[0::2, :, :] += 10          # every other ROW brighter
    m = metrics(_write(a.clip(0, 255)))
    assert m["row_odd_even_delta"] > 8.0, m["row_odd_even_delta"]
    assert m["col_odd_even_delta"] < 1.0, m["col_odd_even_delta"]
