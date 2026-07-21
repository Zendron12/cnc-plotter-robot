"""Bezier smoothing for pixel-space polylines (shared by vector backends)."""

from __future__ import annotations

import math
from typing import Iterable

from wall_climber.image_pipeline._bezier_fit import fit_cubic_beziers

Point = tuple[float, float]
Polyline = tuple[Point, ...]

_EPS = 1.0e-9
_DEFAULT_SAMPLES_PER_SEGMENT = 8
_STRAIGHT_MAX_DEVIATION_PX = 0.45


def _dedupe(points: Iterable[Point]) -> Polyline:
    deduped: list[Point] = []
    for point in points:
        current = (float(point[0]), float(point[1]))
        if deduped:
            last = deduped[-1]
            if abs(last[0] - current[0]) <= _EPS and abs(last[1] - current[1]) <= _EPS:
                continue
        deduped.append(current)
    return tuple(deduped)


def _evaluate_cubic_bezier(
    p0: Point,
    p1: Point,
    p2: Point,
    p3: Point,
    t: float,
) -> Point:
    u = 1.0 - t
    uu = u * u
    tt = t * t
    x = (uu * u * p0[0]) + (3.0 * uu * t * p1[0]) + (3.0 * u * tt * p2[0]) + (tt * t * p3[0])
    y = (uu * u * p0[1]) + (3.0 * uu * t * p1[1]) + (3.0 * u * tt * p2[1]) + (tt * t * p3[1])
    return (x, y)


def _sample_cubic_beziers(
    cubics: list[tuple[Point, ...]],
    *,
    samples_per_segment: int,
) -> Polyline:
    if not cubics:
        return ()
    samples = max(2, int(samples_per_segment))
    points: list[Point] = []
    for index, cubic in enumerate(cubics):
        p0, p1, p2, p3 = cubic
        start_t = 0 if index == 0 else 1
        for step in range(start_t, samples + 1):
            t = float(step) / float(samples)
            points.append(_evaluate_cubic_bezier(p0, p1, p2, p3, t))
    return _dedupe(points)


def _stroke_max_line_deviation_px(stroke: Polyline) -> float:
    if len(stroke) < 3:
        return 0.0
    start_x, start_y = float(stroke[0][0]), float(stroke[0][1])
    end_x, end_y = float(stroke[-1][0]), float(stroke[-1][1])
    dx = end_x - start_x
    dy = end_y - start_y
    span = math.hypot(dx, dy)
    if span <= _EPS:
        return 0.0
    max_deviation = 0.0
    for x, y in stroke[1:-1]:
        px = float(x) - start_x
        py = float(y) - start_y
        deviation = abs((dx * py) - (dy * px)) / span
        if deviation > max_deviation:
            max_deviation = deviation
    return max_deviation


def smooth_polyline_with_bezier(
    stroke: Polyline,
    *,
    tolerance_px: float,
    straight_max_deviation_px: float = _STRAIGHT_MAX_DEVIATION_PX,
    samples_per_segment: int = _DEFAULT_SAMPLES_PER_SEGMENT,
    skip_straight: bool = True,
) -> tuple[Polyline, bool]:
    """Return a smoothed polyline; bool is True when nearly straight (Bezier skipped)."""
    if tolerance_px <= 0.0 or len(stroke) < 3:
        return tuple((float(x), float(y)) for x, y in stroke), False
    float_stroke = tuple((float(x), float(y)) for x, y in stroke)
    if skip_straight and _stroke_max_line_deviation_px(float_stroke) <= max(0.0, float(straight_max_deviation_px)):
        return (float_stroke[0], float_stroke[-1]), True
    cubics = fit_cubic_beziers(float_stroke, max(0.05, float(tolerance_px)))
    if not cubics:
        return float_stroke, False
    smoothed = _sample_cubic_beziers(cubics, samples_per_segment=samples_per_segment)
    if len(smoothed) < 2:
        return float_stroke, False
    return smoothed, False


def smooth_polylines_with_bezier(
    strokes: tuple[Polyline, ...],
    *,
    tolerance_px: float,
    straight_max_deviation_px: float = _STRAIGHT_MAX_DEVIATION_PX,
    samples_per_segment: int = _DEFAULT_SAMPLES_PER_SEGMENT,
    min_stroke_length_px: float = 0.0,
) -> tuple[tuple[Polyline, ...], dict[str, int | float | bool]]:
    """Apply selective Bezier smoothing to each polyline stroke."""
    if tolerance_px <= 0.0 or not strokes:
        return strokes, {
            'bezier_enabled': False,
            'bezier_tolerance_px': float(tolerance_px),
            'bezier_skipped_straight_count': 0,
            'bezier_skipped_short_count': 0,
        }
    smoothed: list[Polyline] = []
    skipped_straight = 0
    skipped_short = 0
    min_length = max(0.0, float(min_stroke_length_px))
    for stroke in strokes:
        stroke_len = sum(
            math.hypot(float(end[0] - start[0]), float(end[1] - start[1]))
            for start, end in zip(stroke[:-1], stroke[1:])
        )
        if min_length > 0.0 and stroke_len < min_length:
            smoothed.append(tuple((float(x), float(y)) for x, y in stroke))
            skipped_short += 1
            continue
        next_stroke, skipped = smooth_polyline_with_bezier(
            stroke,
            tolerance_px=tolerance_px,
            straight_max_deviation_px=straight_max_deviation_px,
            samples_per_segment=samples_per_segment,
        )
        if skipped:
            skipped_straight += 1
        smoothed.append(next_stroke)
    return tuple(smoothed), {
        'bezier_enabled': True,
        'bezier_tolerance_px': float(tolerance_px),
        'bezier_skipped_straight_count': int(skipped_straight),
        'bezier_skipped_short_count': int(skipped_short),
    }


__all__ = [
    'Polyline',
    'smooth_polyline_with_bezier',
    'smooth_polylines_with_bezier',
]
