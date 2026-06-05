"""Unit tests for the thin-line filter (image_pipeline/_thin_line_filter.py).

Builds synthetic binary masks with numpy (no image files) and asserts the
filter keeps thick strokes, drops hairline strokes, is a no-op at threshold 0,
empties the mask when the threshold exceeds every stroke, and is monotonic.
"""

from __future__ import annotations

import numpy as np

from wall_climber.image_pipeline._thin_line_filter import filter_thin_lines


def _mask_with_thin_and_thick() -> np.ndarray:
    """A 1px-wide hairline (row 10) and an 8px-thick band (rows 40-47)."""
    mask = np.zeros((80, 200), dtype=np.uint8)
    mask[10, 20:180] = 255          # hairline, width ~1px
    mask[40:48, 20:180] = 255       # thick band, width 8px
    return mask


def _kept_rows(mask: np.ndarray) -> set[int]:
    return set(np.unique(np.nonzero(mask)[0]).tolist())


def test_keeps_thick_drops_thin() -> None:
    mask = _mask_with_thin_and_thick()
    filtered, meta = filter_thin_lines(mask, min_stroke_width_px=4.0)
    rows = _kept_rows(filtered)
    # The thick band (rows 40-47) survives; the hairline (row 10) is gone.
    assert rows and rows.issubset(set(range(40, 48)))
    assert 10 not in rows
    assert meta['filter_applied'] is True
    assert meta['components_total'] == 2
    assert meta['components_kept'] == 1
    assert meta['components_removed'] == 1


def test_threshold_zero_is_noop() -> None:
    mask = _mask_with_thin_and_thick()
    filtered, meta = filter_thin_lines(mask, min_stroke_width_px=0.0)
    assert np.array_equal((filtered > 0), (mask > 0))
    assert meta['filter_applied'] is False


def test_threshold_above_all_strokes_empties_mask() -> None:
    mask = _mask_with_thin_and_thick()
    filtered, meta = filter_thin_lines(mask, min_stroke_width_px=50.0)
    assert not np.any(filtered)
    assert meta['components_kept'] == 0
    assert meta['components_removed'] == meta['components_total']


def test_monotonic_non_increasing_kept_pixels() -> None:
    mask = _mask_with_thin_and_thick()
    counts = []
    for threshold in (0.0, 2.0, 4.0, 6.0, 10.0, 50.0):
        filtered, _meta = filter_thin_lines(mask, min_stroke_width_px=threshold)
        counts.append(int(np.count_nonzero(filtered)))
    # Raising the threshold must never increase the number of kept pixels.
    for earlier, later in zip(counts, counts[1:]):
        assert later <= earlier


def test_empty_mask_stays_empty() -> None:
    mask = np.zeros((40, 40), dtype=np.uint8)
    filtered, meta = filter_thin_lines(mask, min_stroke_width_px=4.0)
    assert not np.any(filtered)
    assert meta['components_total'] == 0
    assert meta['components_kept'] == 0
