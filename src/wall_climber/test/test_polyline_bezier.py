"""Tests for shared polyline Bezier smoothing."""

from __future__ import annotations

from wall_climber.image_pipeline._polyline_bezier import smooth_polylines_with_bezier


def test_bezier_smoothing_preserves_endpoints() -> None:
    stroke = (
        (0.0, 0.0),
        (5.0, 2.0),
        (10.0, 0.0),
        (15.0, 2.0),
        (20.0, 0.0),
    )
    smoothed, metadata = smooth_polylines_with_bezier((stroke,), tolerance_px=1.0)
    assert metadata['bezier_enabled'] is True
    assert len(smoothed) == 1
    assert smoothed[0][0] == (0.0, 0.0)
    assert smoothed[0][-1] == (20.0, 0.0)
    assert len(smoothed[0]) >= 2


def test_bezier_smoothing_skips_short_strokes() -> None:
    stroke = (
        (0.0, 0.0),
        (5.0, 2.0),
        (10.0, 0.0),
    )
    smoothed, metadata = smooth_polylines_with_bezier(
        (stroke,),
        tolerance_px=1.0,
        min_stroke_length_px=20.0,
    )
    assert metadata['bezier_skipped_short_count'] == 1
    assert smoothed[0] == stroke


def test_bezier_smoothing_skips_when_disabled() -> None:
    stroke = ((0.0, 0.0), (10.0, 10.0), (20.0, 0.0))
    smoothed, metadata = smooth_polylines_with_bezier((stroke,), tolerance_px=0.0)
    assert metadata['bezier_enabled'] is False
    assert smoothed[0] == stroke
