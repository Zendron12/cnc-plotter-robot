"""Tests for AutoTrace centerline vectorization (POC)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from wall_climber.image_pipeline import autotrace_vector
from wall_climber.image_pipeline.types import PipelineMode


def _sample_autotrace_svg(width: int, height: int) -> str:
    return f'''<?xml version="1.0" standalone="yes"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">
<path style="stroke:#000000; fill:none;" d="M10 20 L{width - 10} 20"/>
</svg>'''


def _coloring_book_png() -> bytes:
    img = np.full((500, 500, 3), 255, dtype=np.uint8)
    cv2.rectangle(img, (80, 80), (420, 420), (0, 0, 0), 2, lineType=cv2.LINE_AA)
    cv2.circle(img, (250, 250), 60, (0, 0, 0), 2, lineType=cv2.LINE_AA)
    ok, buffer = cv2.imencode('.png', img)
    assert ok
    return bytes(buffer)


def test_is_autotrace_available_reflects_path(monkeypatch) -> None:
    monkeypatch.setattr(autotrace_vector.shutil, 'which', lambda _name: '/usr/local/bin/autotrace')
    assert autotrace_vector.is_autotrace_available() is True
    monkeypatch.setattr(autotrace_vector.shutil, 'which', lambda _name: None)
    assert autotrace_vector.is_autotrace_available() is False


def test_vectorize_autotrace_svg_keeps_image_space_coordinates() -> None:
    strokes = autotrace_vector._vectorize_autotrace_svg(_sample_autotrace_svg(200, 100))
    assert len(strokes) == 1
    ys = [point[1] for point in strokes[0]]
    assert min(ys) == pytest.approx(20.0, abs=0.5)
    assert max(ys) == pytest.approx(20.0, abs=0.5)


def test_vectorize_autotrace_image_to_plan_uses_cli(monkeypatch) -> None:
    monkeypatch.setattr(autotrace_vector.shutil, 'which', lambda _name: '/usr/local/bin/autotrace')

    def _fake_run(command, **_kwargs):
        output_path = command[command.index('-output-file') + 1]
        Path(output_path).write_text(_sample_autotrace_svg(280, 200), encoding='utf-8')
        return autotrace_vector.subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(autotrace_vector.subprocess, 'run', _fake_run)

    plan = autotrace_vector.vectorize_autotrace_image_to_plan(
        _coloring_book_png(),
        board_width_m=2.0,
        board_height_m=1.5,
    )
    assert plan.mode == PipelineMode.SKETCH_CENTERLINE
    assert plan.metrics.stroke_count >= 1
    assert plan.metadata['vectorization_method'] == 'autotrace'
    assert plan.metadata['pipeline_mode'] == 'sketch_autotrace'
    assert plan.metadata['autotrace_centerline'] is True
    assert plan.metadata['timing']['autotrace_time_ms'] >= 0.0


def test_vectorize_autotrace_image_to_plan_requires_binary(monkeypatch) -> None:
    monkeypatch.setattr(autotrace_vector.shutil, 'which', lambda _name: None)
    with pytest.raises(ValueError, match='autotrace is not installed'):
        autotrace_vector.vectorize_autotrace_image_to_plan(
            _coloring_book_png(),
            board_width_m=2.0,
            board_height_m=1.5,
        )


def test_remove_speckle_noise_strength_zero_is_noop() -> None:
    bitmap = np.array([[0, 255], [255, 255]], dtype=np.uint8)
    result = autotrace_vector._remove_speckle_noise(bitmap, strength=0)
    assert np.array_equal(result, bitmap)


def test_remove_speckle_noise_strength_removes_isolated_pixels() -> None:
    bitmap = np.full((5, 5), 255, dtype=np.uint8)
    bitmap[2, 2] = 0
    result = autotrace_vector._remove_speckle_noise(bitmap, strength=1)
    assert int(result[2, 2]) == 255


def test_preview_endpoint_autotrace_mode(monkeypatch) -> None:
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

    monkeypatch.setattr(web_server, 'is_autotrace_available', lambda: True)

    def _fake_plan(_content, **kwargs):
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
                'vectorization_method': 'autotrace',
                'pipeline_mode': 'sketch_autotrace',
                'scale_m_per_px': 0.01,
                'timing': {'autotrace_time_ms': 1.0},
            },
        )

    monkeypatch.setattr(web_server, 'vectorize_autotrace_image_to_plan', _fake_plan)
    client = TestClient(web_server.create_app(_FakeRuntime()))
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', _coloring_book_png(), 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': '{"vectorization_method":"autotrace","optimization_preset":"line_art","max_image_dim":600}',
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['pipeline_mode'] == 'sketch_autotrace'
    assert body['metadata']['vectorization_method'] == 'autotrace'


def test_vectorize_autotrace_preprocessed_lineart_real_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    if not autotrace_vector.is_autotrace_available():
        pytest.skip('autotrace not installed')
    from wall_climber.image_pipeline.ai_preprocess import (
        PreprocessSettings,
        preprocess_image_to_lineart,
    )
    from wall_climber.image_pipeline.ai_preprocess.informative_model import InformativeRunResult
    from wall_climber.image_pipeline.ai_preprocess.preview_encode import decode_lineart_png

    def fake_run(image_bgr: np.ndarray, *, target_size: int) -> InformativeRunResult:
        del target_size
        gray = np.full(image_bgr.shape[:2], 255, dtype=np.uint8)
        cv2.rectangle(gray, (10, 10), (gray.shape[1] - 10, gray.shape[0] - 10), 0, 2)
        return InformativeRunResult(
            lineart_gray=gray,
            model_key='informative_anime',
            used_cuda=False,
            elapsed_ms=0.0,
            backend='stub',
            info='test stub',
        )

    monkeypatch.setattr(
        'wall_climber.image_pipeline.ai_preprocess.router.run_informative_anime',
        fake_run,
    )

    result = preprocess_image_to_lineart(
        _coloring_book_png(),
        PreprocessSettings(mode='photo', target_resolution=512),
    )
    plan = autotrace_vector.vectorize_autotrace_image_to_plan(
        _coloring_book_png(),
        board_width_m=2.0,
        board_height_m=1.5,
        preprocessed_bitmap=decode_lineart_png(result.lineart_png),
    )
    assert plan.metrics.stroke_count >= 1
    assert plan.metadata['preprocessed_input'] is True


def test_preview_endpoint_autotrace_rejects_missing_binary(monkeypatch) -> None:
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

    monkeypatch.setattr(web_server, 'is_autotrace_available', lambda: False)
    client = TestClient(web_server.create_app(_FakeRuntime()))
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', _coloring_book_png(), 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': '{"vectorization_method":"autotrace"}',
        },
    )
    assert response.status_code == 503
    assert 'autotrace' in response.json()['detail'].lower()
