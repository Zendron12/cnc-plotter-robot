"""Integration tests for AI sketch preview via /api/preview."""

from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore
import numpy
import pytest
from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber.image_pipeline.ai_preprocess.swinir import SwinirRunResult

from conftest import fake_autotrace_plan


def _encode_png(image: numpy.ndarray) -> bytes:
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    return bytes(encoded.tobytes())


def _simple_sketch_png() -> bytes:
    image = numpy.full((100, 180, 3), 255, dtype=numpy.uint8)
    cv2.line(image, (20, 50), (160, 50), (0, 0, 0), 5, lineType=cv2.LINE_AA)
    return _encode_png(image)


class _FakeNode:
    def carriage_safe_writable_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

    def carriage_safe_safe_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}

    def runtime_snapshot(self) -> dict[str, object]:
        return {'ready': True, 'active_mode': 'draw', 'observed_statuses': {}}

    def publish_execution_plan(self, *_args, **_kwargs):
        raise AssertionError('preview endpoint must not publish robot commands')


class _FakeRuntime:
    def __init__(self) -> None:
        self.node = _FakeNode()
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'


@pytest.fixture(autouse=True)
def _mock_autotrace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_server, 'is_autotrace_available', lambda: True)
    monkeypatch.setattr(
        web_server,
        'vectorize_autotrace_image_to_plan',
        lambda content, **kwargs: fake_autotrace_plan(content, **kwargs),
    )


@pytest.fixture(autouse=True)
def _stub_swinir(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(image_bgr: numpy.ndarray, *, target_resolution: int, **kwargs) -> SwinirRunResult:
        del target_resolution, kwargs
        return SwinirRunResult(
            image_bgr=image_bgr,
            scale_applied=1.0,
            model_key='swinir_stub',
            used_cuda=False,
            elapsed_ms=0.0,
            backend='stub',
            info='test stub',
        )

    monkeypatch.setattr(
        'wall_climber.image_pipeline.ai_preprocess.router.run_swinir_tiled',
        fake_run,
    )


@pytest.fixture(autouse=True)
def _stub_informative(monkeypatch: pytest.MonkeyPatch) -> None:
    from wall_climber.image_pipeline.ai_preprocess.informative_model import InformativeRunResult

    def fake_run(image_bgr: numpy.ndarray, *, target_size: int) -> InformativeRunResult:
        del target_size
        gray = numpy.full(image_bgr.shape[:2], 255, dtype=numpy.uint8)
        cv2.line(gray, (10, 10), (gray.shape[1] - 10, gray.shape[0] - 10), 0, 2)
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


def test_ai_photo_preview_returns_preprocess_preview() -> None:
    client = TestClient(web_server.create_app(_FakeRuntime()))
    settings = {
        'vectorization_method': 'autotrace',
        'image_preprocess_mode': 'photo',
        'image_target_resolution': 512,
        'image_force_solid_black_lines': False,
        'max_image_dim': 700,
    }
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', _simple_sketch_png(), 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': __import__('json').dumps(settings),
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['pipeline_mode'] == 'sketch_ai_photo_autotrace'
    assert payload['preview']['strokes']
    preprocess = payload.get('preprocess_preview') or {}
    assert preprocess.get('mode') == 'photo'
    assert preprocess.get('skipped_preprocess') is False
    assert isinstance(preprocess.get('pipeline_stages'), list)
    stage_ids = [stage['stage_id'] for stage in preprocess['pipeline_stages']]
    assert 'informative' in stage_ids
    assert 'pyra_canny' not in stage_ids
    assert len(preprocess['pipeline_stages']) >= 4
    assert preprocess['lineart_data_url'].startswith('data:image/png;base64,')


def test_coloring_book_raw_print_skips_preprocess_preview_stages() -> None:
    client = TestClient(web_server.create_app(_FakeRuntime()))
    settings = {
        'vectorization_method': 'autotrace',
        'image_preprocess_mode': 'coloring_book',
        'image_raw_print': True,
    }
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', _simple_sketch_png(), 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': __import__('json').dumps(settings),
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['pipeline_mode'] == 'sketch_raw_print_autotrace'
    preprocess = payload.get('preprocess_preview') or {}
    assert preprocess.get('skipped_preprocess') is True
    stage_ids = [stage['stage_id'] for stage in preprocess.get('pipeline_stages', [])]
    assert stage_ids == ['original', 'vectorization']


def test_coloring_book_raw_print_autotrace_uses_direct_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    original_png = _simple_sketch_png()

    def capture_vectorize(content, **kwargs):
        captured['content'] = content
        captured['kwargs'] = dict(kwargs)
        return fake_autotrace_plan(content, **kwargs)

    monkeypatch.setattr(web_server, 'vectorize_autotrace_image_to_plan', capture_vectorize)

    client = TestClient(web_server.create_app(_FakeRuntime()))
    settings = {
        'vectorization_method': 'autotrace',
        'image_preprocess_mode': 'coloring_book',
        'image_raw_print': True,
    }
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', original_png, 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': __import__('json').dumps(settings),
        },
    )

    assert response.status_code == 200, response.text
    kwargs = captured.get('kwargs') or {}
    assert kwargs.get('preprocessed_bitmap') is None
    assert captured.get('content') == original_png
    metadata = response.json().get('metadata') or {}
    assert metadata.get('autotrace_direct_upload') is True


def test_coloring_book_raw_print_potrace_uses_preprocessed_lineart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    original_png = _simple_sketch_png()

    def capture_vectorize(content, **kwargs):
        captured['content'] = content
        captured['kwargs'] = dict(kwargs)
        return fake_autotrace_plan(content, **kwargs)

    monkeypatch.setattr(web_server, 'is_potrace_available', lambda: True)
    monkeypatch.setattr(web_server, 'vectorize_potrace_image_to_plan', capture_vectorize)

    client = TestClient(web_server.create_app(_FakeRuntime()))
    settings = {
        'vectorization_method': 'potrace',
        'image_preprocess_mode': 'coloring_book',
        'image_raw_print': True,
    }
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', original_png, 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': __import__('json').dumps(settings),
        },
    )

    assert response.status_code == 200, response.text
    kwargs = captured.get('kwargs') or {}
    assert kwargs.get('preprocessed_bitmap') is not None
    assert captured.get('content') != original_png
    metadata = response.json().get('metadata') or {}
    assert metadata.get('autotrace_direct_upload') is not True


def test_health_reports_ai_preprocess_fields() -> None:
    client = TestClient(web_server.create_app(_FakeRuntime()))
    response = client.get('/api/health')
    assert response.status_code == 200
    payload = response.json()
    assert 'cuda_available' in payload
    assert 'swinir_weights_cached' in payload
    assert 'informative_weights_cached' in payload
    assert 'anilines_weights_cached' in payload
    assert payload['ai_preprocess_available'] is True


def test_vectorizer_switch_reuses_lineart_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    preprocess_calls = {'count': 0}
    original_preprocess = web_server.preprocess_image_to_lineart

    def counting_preprocess(*args, **kwargs):
        preprocess_calls['count'] += 1
        return original_preprocess(*args, **kwargs)

    monkeypatch.setattr(web_server, 'preprocess_image_to_lineart', counting_preprocess)
    monkeypatch.setattr(web_server, 'is_potrace_available', lambda: True)
    monkeypatch.setattr(
        web_server,
        'vectorize_potrace_image_to_plan',
        lambda content, **kwargs: fake_autotrace_plan(content, **kwargs),
    )

    client = TestClient(web_server.create_app(_FakeRuntime()))
    png = _simple_sketch_png()
    base_settings = {
        'image_preprocess_mode': 'photo',
        'image_target_resolution': 512,
        'image_force_solid_black_lines': False,
    }

    first = client.post(
        '/api/preview',
        files={'file': ('line.png', png, 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': __import__('json').dumps(
                {**base_settings, 'vectorization_method': 'autotrace'}
            ),
        },
    )
    assert first.status_code == 200, first.text
    assert preprocess_calls['count'] == 1
    assert not (first.json().get('preprocess_preview') or {}).get('reused_from_cache')

    second = client.post(
        '/api/preview',
        files={'file': ('line.png', png, 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': __import__('json').dumps(
                {**base_settings, 'vectorization_method': 'potrace'}
            ),
        },
    )
    assert second.status_code == 200, second.text
    assert preprocess_calls['count'] == 1
    preprocess = second.json().get('preprocess_preview') or {}
    assert preprocess.get('reused_from_cache') is True
    assert second.json()['pipeline_mode'] == 'sketch_ai_photo_potrace'
