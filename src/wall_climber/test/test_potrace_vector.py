"""Tests for the Potrace raster vectorization pipeline."""

from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore
import numpy as np
import pytest

from wall_climber.image_pipeline import potrace_vector


def _encode_png(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode('.png', image)
    assert ok
    return bytes(buffer)


def _coloring_book_line_png() -> bytes:
    image = np.full((200, 280, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (40, 40), (240, 160), (0, 0, 0), 3, lineType=cv2.LINE_AA)
    cv2.circle(image, (140, 100), 36, (0, 0, 0), 2, lineType=cv2.LINE_AA)
    return _encode_png(image)


def _sample_potrace_svg(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<path d="M 20 100 L 260 100" fill="none" stroke="#000000" stroke-width="1"/>'
        '</svg>'
    )


def test_is_potrace_available_reflects_path(monkeypatch) -> None:
    monkeypatch.setattr(potrace_vector.shutil, 'which', lambda _name: '/usr/bin/potrace')
    assert potrace_vector.is_potrace_available() is True
    monkeypatch.setattr(potrace_vector.shutil, 'which', lambda _name: None)
    assert potrace_vector.is_potrace_available() is False


def _sample_potrace_svg(width: int, height: int) -> str:
    return (
        f'<?xml version="1.0" standalone="no"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" '
        f'viewBox="0 0 {width} {height}">'
        f'<g transform="translate(0.000000,{height:.6f}) scale(0.100000,-0.100000)" '
        f'fill="#000000" stroke="none">'
        f'<path d="M200 1000 L2600 1000 L2600 1600 L200 1600 z"/>'
        f'</g></svg>'
    )


def test_vectorize_potrace_svg_flattens_group_transform() -> None:
    strokes = potrace_vector._vectorize_potrace_svg(_sample_potrace_svg(280, 200))
    assert strokes
    xs = [x for stroke in strokes for x, _y in stroke]
    ys = [y for stroke in strokes for _x, y in stroke]
    assert min(xs) >= -1.0
    assert max(xs) <= 281.0
    assert min(ys) >= -1.0
    assert max(ys) <= 201.0


def test_vectorize_potrace_image_to_plan_uses_potrace_cli(monkeypatch) -> None:
    monkeypatch.setattr(potrace_vector.shutil, 'which', lambda _name: '/usr/bin/potrace')

    def _fake_run(command, **_kwargs):
        output_path = Path(command[command.index('-o') + 1])
        output_path.write_text(_sample_potrace_svg(280, 200), encoding='utf-8')
        return potrace_vector.subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(potrace_vector.subprocess, 'run', _fake_run)

    plan = potrace_vector.vectorize_potrace_image_to_plan(
        _coloring_book_line_png(),
        board_width_m=2.0,
        board_height_m=1.0,
        margin_m=0.05,
        max_image_dim=600,
    )

    assert plan.metadata['vectorization_method'] == 'potrace'
    assert plan.metadata['pipeline_mode'] == 'sketch_potrace'
    assert plan.metrics.stroke_count >= 1
    assert plan.metadata['timing']['potrace_time_ms'] >= 0.0


def test_vectorize_potrace_image_to_plan_requires_binary(monkeypatch) -> None:
    monkeypatch.setattr(potrace_vector.shutil, 'which', lambda _name: None)
    with pytest.raises(ValueError, match='potrace is not installed'):
        potrace_vector.vectorize_potrace_image_to_plan(
            _coloring_book_line_png(),
            board_width_m=2.0,
            board_height_m=1.0,
        )


def test_preview_endpoint_potrace_mode_returns_sketch_potrace_pipeline(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from wall_climber import web_server

    class _FakeNode:
        def carriage_safe_writable_bounds(self) -> dict[str, float]:
            return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

        def carriage_safe_safe_bounds(self) -> dict[str, float]:
            return {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}

        def publish_execution_plan(self, *_args, **_kwargs):
            raise AssertionError('preview must not publish')

    class _FakeRuntime:
        def __init__(self) -> None:
            self.node = _FakeNode()
            self.web_dir = Path(__file__).resolve().parents[1] / 'web'

    monkeypatch.setattr(web_server, 'is_potrace_available', lambda: True)

    def _fake_vectorize_potrace_image_to_plan(_content, **kwargs):
        from wall_climber.image_pipeline.types import (
            DrawingPathPlan,
            PipelineMetrics,
            PipelineMode,
            Point2D,
            Stroke,
        )

        return DrawingPathPlan(
            mode=PipelineMode.SKETCH_CENTERLINE,
            strokes=(
                Stroke(points=(Point2D(0.5, 0.5), Point2D(2.0, 0.5), Point2D(3.0, 1.0))),
            ),
            metrics=PipelineMetrics(stroke_count=1, points_before_simplification=3, points_after_simplification=3),
            metadata={
                'vectorization_method': 'potrace',
                'pipeline_mode': 'sketch_potrace',
                'scale_m_per_px': 0.01,
                'timing': {'potrace_time_ms': 1.0},
            },
        )

    monkeypatch.setattr(web_server, 'vectorize_potrace_image_to_plan', _fake_vectorize_potrace_image_to_plan)

    client = TestClient(web_server.create_app(_FakeRuntime()))
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', _coloring_book_line_png(), 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': '{"vectorization_method":"potrace","optimization_preset":"line_art","max_image_dim":600}',
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['pipeline_mode'] == 'sketch_potrace'
    assert body['metadata']['vectorization_method'] == 'potrace'
    assert body['preview_id']


def test_preview_endpoint_potrace_mode_rejects_missing_binary(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from wall_climber import web_server

    class _FakeNode:
        def carriage_safe_writable_bounds(self) -> dict[str, float]:
            return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

        def carriage_safe_safe_bounds(self) -> dict[str, float]:
            return {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}

    class _FakeRuntime:
        def __init__(self) -> None:
            self.node = _FakeNode()
            self.web_dir = Path(__file__).resolve().parents[1] / 'web'

    monkeypatch.setattr(web_server, 'is_potrace_available', lambda: False)
    client = TestClient(web_server.create_app(_FakeRuntime()))
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', _coloring_book_line_png(), 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': '{"vectorization_method":"potrace"}',
        },
    )

    assert response.status_code == 503
    assert 'potrace' in response.json()['detail'].lower()
