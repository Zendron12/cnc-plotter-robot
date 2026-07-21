from __future__ import annotations

import time

from wall_climber.image_pipeline._stroke_order import Stroke, optimise_stroke_order


def _grid_strokes(rows: int, cols: int) -> list[Stroke]:
    strokes: list[Stroke] = []
    for row in range(rows):
        y = float(row)
        for col in range(cols):
            x = float(col)
            strokes.append(Stroke(points=((x, y), (x + 0.4, y))))
    return strokes


def test_optimise_stroke_order_respects_time_budget() -> None:
    strokes = _grid_strokes(40, 40)
    started = time.perf_counter()
    result = optimise_stroke_order(strokes, max_time_ms=50.0, max_2opt_iterations=10_000)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert len(result.strokes) == len(strokes)
    assert elapsed_ms < 1000.0
