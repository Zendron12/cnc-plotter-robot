from __future__ import annotations

import cv2  # type: ignore
import numpy
import pytest

from wall_climber.image_pipeline.types import (
    DrawingPathPlan,
    PipelineMetrics,
    PipelineMode,
    Point2D,
    Stroke,
)


def fake_autotrace_plan(content: bytes, **kwargs) -> DrawingPathPlan:
    decoded = cv2.imdecode(numpy.frombuffer(content, dtype=numpy.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError('Failed to decode PNG/JPG image payload.')

    center_x = float(3.0 if kwargs.get('center_x_m') is None else kwargs['center_x_m'])
    center_y = float(1.5 if kwargs.get('center_y_m') is None else kwargs['center_y_m'])
    validation_bounds = kwargs.get('validation_bounds_m') or kwargs.get('fit_bounds_m') or {}
    stroke_points = (
        Point2D(center_x - 1.0, center_y),
        Point2D(center_x - 0.5, center_y + 0.35),
        Point2D(center_x, center_y + 0.45),
        Point2D(center_x + 0.5, center_y + 0.35),
        Point2D(center_x + 1.0, center_y),
    )
    min_board_x = min(point.x for point in stroke_points)
    max_board_x = max(point.x for point in stroke_points)
    min_board_y = min(point.y for point in stroke_points)
    max_board_y = max(point.y for point in stroke_points)
    if validation_bounds:
        if (
            min_board_x < float(validation_bounds.get('x_min', 0.0)) - 1.0e-7
            or min_board_y < float(validation_bounds.get('y_min', 0.0)) - 1.0e-7
            or max_board_x > float(validation_bounds.get('x_max', 0.0)) + 1.0e-7
            or max_board_y > float(validation_bounds.get('y_max', 0.0)) + 1.0e-7
        ):
            raise ValueError(
                'Sketch placement is outside the robot-safe drawable bounds; reduce scale_percent '
                'or choose a center_x_m/center_y_m inside the safe drawable area.'
            )

    return DrawingPathPlan(
        mode=PipelineMode.SKETCH_CENTERLINE,
        strokes=(Stroke(points=stroke_points),),
        metrics=PipelineMetrics(
            stroke_count=1,
            points_before_simplification=5,
            points_after_simplification=5,
            total_drawing_length_m=2.5,
        ),
        metadata={
            'vectorization_method': 'autotrace',
            'pipeline_mode': 'sketch_autotrace',
            'scale_percent': float(kwargs.get('scale_percent') or 100.0),
            'center_x_m': center_x,
            'center_y_m': center_y,
            'scale_m_per_px': 0.01,
            'fit_to_safe_area': True,
            'safe_x_min': float(validation_bounds.get('x_min', 0.348)),
            'safe_x_max': float(validation_bounds.get('x_max', 6.14)),
            'safe_y_min': float(validation_bounds.get('y_min', 0.22)),
            'safe_y_max': float(validation_bounds.get('y_max', 2.82)),
            'timing': {
                'decode_time_ms': 1.0,
                'autotrace_time_ms': 2.0,
                'scale_time_ms': 1.0,
            },
        },
    )


@pytest.fixture()
def mock_autotrace(monkeypatch: pytest.MonkeyPatch):
    from wall_climber import web_server

    monkeypatch.setattr(web_server, 'is_autotrace_available', lambda: True)
    monkeypatch.setattr(web_server, 'vectorize_autotrace_image_to_plan', fake_autotrace_plan)
