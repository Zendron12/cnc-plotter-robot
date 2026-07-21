from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore
import numpy
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from wall_climber import web_server

from conftest import fake_autotrace_plan
from wall_climber.runtime_topics import MODE_DRAW, MODE_TEXT, PEN_MODE_AUTO


class _FakeBoardPoint:
    def __init__(self, *, x: float = 0.0, y: float = 0.0) -> None:
        self.x = float(x)
        self.y = float(y)


class _FakePathPrimitive:
    PEN_UP = 1
    PEN_DOWN = 2
    TRAVEL_MOVE = 3
    LINE_SEGMENT = 4
    ARC_SEGMENT = 5
    QUADRATIC_BEZIER = 6
    CUBIC_BEZIER = 7

    def __init__(self) -> None:
        self.type = 0
        self.start = _FakeBoardPoint()
        self.end = _FakeBoardPoint()
        self.control1 = _FakeBoardPoint()
        self.control2 = _FakeBoardPoint()
        self.center = _FakeBoardPoint()
        self.radius = 0.0
        self.start_angle_rad = 0.0
        self.sweep_angle_rad = 0.0
        self.clockwise = False
        self.pen_down = False


class _FakePrimitivePathPlan:
    def __init__(self) -> None:
        self.frame = ''
        self.theta_ref = 0.0
        self.primitives: list[_FakePathPrimitive] = []


class _FakeNode:
    def __init__(self) -> None:
        self.active_mode = MODE_DRAW
        self.manual_pen_mode = PEN_MODE_AUTO
        self.publish_count = 0
        self.published_plans: list[_FakePrimitivePathPlan] = []

    def carriage_safe_writable_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

    def carriage_safe_safe_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}

    def publish_execution_plan(self, primitive_plan, *, allowed_modes):
        if self.active_mode not in allowed_modes:
            raise HTTPException(status_code=409, detail='active mode must be draw')
        if self.manual_pen_mode != PEN_MODE_AUTO:
            raise HTTPException(status_code=409, detail='manual arm test must be auto')
        self.publish_count += 1
        self.published_plans.append(primitive_plan)
        return {
            'published': 'primitive_path_plan',
            'preferred_transport': 'primitive_path_plan',
            'primitive_transport_published': True,
            'topics': {'primitive_path_plan': '/wall_climber/primitive_path_plan'},
        }


class _FakeRuntime:
    def __init__(self) -> None:
        self.node = _FakeNode()
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'
        self.last_plan_debug = None
        self.last_execution_debug = None
        self.last_curve_fit_debug = None
        self.uploads: dict[str, tuple[dict, bytes]] = {}
        self._text_cursors: dict[str, tuple[float | None, float | None]] = {}
        self._text_full_width_bottom_y: float | None = None
        self._text_column_bottom_y: dict[str, float] = {}
        self._text_global_bottom_y: float | None = None
        self._last_text_draw_column: str | None = None

    def get_last_text_draw_column(self) -> str | None:
        return self._last_text_draw_column

    def set_last_text_draw_column(self, column: str | None) -> None:
        self._last_text_draw_column = (column or 'full').strip().lower()

    def get_text_global_bottom_y(self) -> float | None:
        return self._text_global_bottom_y

    def note_text_global_bottom_y(self, bottom_y: float) -> None:
        value = float(bottom_y)
        if self._text_global_bottom_y is None or value > self._text_global_bottom_y:
            self._text_global_bottom_y = value

    def get_text_column_bottom_y(self, column: str | None) -> float | None:
        key = (column or 'full').strip().lower()
        if key not in {'left', 'center', 'right'}:
            return None
        return self._text_column_bottom_y.get(key)

    def note_text_column_bottom_y(self, column: str | None, bottom_y: float) -> None:
        key = (column or 'full').strip().lower()
        if key not in {'left', 'center', 'right'}:
            return
        value = float(bottom_y)
        existing = self._text_column_bottom_y.get(key)
        if existing is None or value > existing:
            self._text_column_bottom_y[key] = value

    def get_text_cursor(self, column: str | None = None) -> tuple[float | None, float | None]:
        key = (column or 'full').strip().lower()
        return self._text_cursors.get(key, (None, None))

    def set_text_cursor(
        self,
        x: float | None,
        y: float | None,
        column: str | None = None,
    ) -> None:
        key = (column or 'full').strip().lower()
        if x is None or y is None:
            self._text_cursors.pop(key, None)
        else:
            self._text_cursors[key] = (float(x), float(y))

    def clear_text_cursor_position(self, column: str | None = None) -> None:
        key = (column or 'full').strip().lower()
        self._text_cursors.pop(key, None)

    def reset_text_cursors(self, column: str | None = None, *, clear_ink: bool = True) -> None:
        if column is None:
            self._text_cursors.clear()
            if clear_ink:
                self._text_full_width_bottom_y = None
                self._text_column_bottom_y.clear()
                self._text_global_bottom_y = None
                self._last_text_draw_column = None
            return
        key = str(column).strip().lower()
        self._text_cursors.pop(key, None)

    def get_text_full_width_bottom_y(self) -> float | None:
        return self._text_full_width_bottom_y

    def note_text_full_width_bottom_y(self, bottom_y: float) -> None:
        value = float(bottom_y)
        if (
            self._text_full_width_bottom_y is None
            or value > self._text_full_width_bottom_y
        ):
            self._text_full_width_bottom_y = value

    def record_last_plan_debug(self, payload: dict) -> None:
        self.last_plan_debug = dict(payload)

    def record_last_execution_debug(self, payload: dict) -> None:
        self.last_execution_debug = dict(payload)

    def record_last_curve_fit_debug(self, payload: dict) -> None:
        self.last_curve_fit_debug = dict(payload)

    def load_upload(self, upload_id: str) -> tuple[dict, bytes]:
        try:
            metadata, payload = self.uploads[upload_id]
        except KeyError:
            raise HTTPException(status_code=404, detail='upload_id was not found')
        return dict(metadata), payload

    def upload_processing_snapshot(self, upload_id: str, *, metadata=None, payload=None) -> dict:
        metadata = dict(metadata or self.uploads[upload_id][0])
        return {
            'upload_id': upload_id,
            'source_type': metadata.get('source_type', 'image'),
            'state': 'ready',
            'stage': 'ready',
            'progress': 1.0,
            'message': 'Vector preview is ready.',
            'image_size': metadata.get('image_size'),
            'route': None,
            'timings_ms': {},
            'curve_fit_summary': {},
        }

    def prepared_image_artifact(self, upload_id: str, *, metadata=None, payload=None):
        return None


@pytest.fixture(autouse=True)
def _fake_ros_messages(monkeypatch):
    monkeypatch.setattr(web_server, 'BoardPoint', _FakeBoardPoint)
    monkeypatch.setattr(web_server, 'PathPrimitive', _FakePathPrimitive)
    monkeypatch.setattr(web_server, 'PrimitivePathPlan', _FakePrimitivePathPlan)


@pytest.fixture(autouse=True)
def _mock_autotrace_preview(monkeypatch):
    monkeypatch.setattr(web_server, 'is_autotrace_available', lambda: True)
    monkeypatch.setattr(web_server, 'vectorize_autotrace_image_to_plan', fake_autotrace_plan)


def _client_and_runtime() -> tuple[TestClient, _FakeRuntime]:
    runtime = _FakeRuntime()
    return TestClient(web_server.create_app(runtime)), runtime


def _encode_png(image: numpy.ndarray) -> bytes:
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    return bytes(encoded.tobytes())


def _simple_line_art_png() -> bytes:
    image = numpy.full((120, 160, 3), 255, dtype=numpy.uint8)
    cv2.line(image, (20, 60), (140, 60), (0, 0, 0), 4, lineType=cv2.LINE_AA)
    cv2.circle(image, (80, 60), 22, (0, 0, 0), 2, lineType=cv2.LINE_AA)
    return _encode_png(image)


def _simple_colored_diagram_png() -> bytes:
    image = numpy.full((160, 220, 3), (225, 245, 250), dtype=numpy.uint8)
    cv2.rectangle(image, (16, 104), (110, 150), (215, 190, 130), -1)
    cv2.rectangle(image, (16, 104), (110, 150), (0, 0, 0), 3, lineType=cv2.LINE_AA)
    cv2.circle(image, (44, 42), 24, (0, 220, 255), -1)
    cv2.circle(image, (44, 42), 24, (0, 0, 0), 3, lineType=cv2.LINE_AA)
    cv2.ellipse(image, (150, 55), (42, 20), 0, 0, 360, (245, 245, 245), -1, lineType=cv2.LINE_AA)
    cv2.ellipse(image, (150, 55), (42, 20), 0, 0, 360, (0, 0, 0), 3, lineType=cv2.LINE_AA)
    cv2.line(image, (134, 95), (194, 138), (0, 0, 0), 3, lineType=cv2.LINE_AA)
    return _encode_png(image)


def _preview_svg(client: TestClient) -> dict:
    response = client.post(
        '/api/preview',
        json={
            'input_type': 'svg',
            'svg': '<svg viewBox="0 0 100 60"><path d="M10 30 C30 5 70 55 90 30"/></svg>',
            'placement': {'x': 3.2, 'y': 1.5, 'scale': 0.7},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['preview_id']
    assert payload['canonical_hash']
    assert payload['preview']['canonical_hash'] == payload['canonical_hash']
    assert payload['primitive_hash']
    assert payload['execution_hash']
    return payload


def _preview_text(client: TestClient) -> dict:
    response = client.post(
        '/api/preview',
        json={
            'input_type': 'text',
            'text': 'HELLO',
            'placement': {'x': 0.55, 'y': 0.45, 'scale': 0.8},
            'settings': {'font_source': 'relief_singleline'},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['preview_id']
    assert payload['canonical_hash']
    assert payload['primitive_hash']
    assert payload['execution_hash']
    return payload


def _preview_image(client: TestClient, runtime: _FakeRuntime) -> dict:
    payload = _simple_line_art_png()
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', payload, 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': '{"preview_geometry_mode":"polyline","max_image_dim":600}',
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['preview_id']
    assert body['canonical_hash']
    assert body['pipeline_mode'] == 'sketch_autotrace'
    assert body['metadata']['vectorization_method'] == 'autotrace'
    assert body['primitive_hash']
    assert body['execution_hash']
    return body


def test_auto_colored_raster_uses_autotrace_pipeline() -> None:
    client, _runtime = _client_and_runtime()
    response = client.post(
        '/api/preview',
        files={'file': ('diagram.png', _simple_colored_diagram_png(), 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': '{"preview_geometry_mode":"polyline","max_image_dim":600}',
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['pipeline_mode'] == 'sketch_autotrace'
    assert body['input_type'] == 'sketch_image'
    assert body['preview_id']
    assert body['primitive_hash']
    assert body['execution_hash']


def test_forced_sketch_image_uses_autotrace_pipeline() -> None:
    client, _runtime = _client_and_runtime()
    response = client.post(
        '/api/preview',
        files={'file': ('diagram.png', _simple_colored_diagram_png(), 'image/png')},
        data={
            'input_type': 'sketch_image',
            'settings_json': '{"preview_geometry_mode":"polyline","max_image_dim":600}',
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['pipeline_mode'] == 'sketch_autotrace'
    assert body['input_type'] == 'sketch_image'
    assert 'converted_lineart_preview' not in body


def test_vpype_preview_falls_back_to_internal_when_missing(monkeypatch) -> None:
    client, _runtime = _client_and_runtime()

    def _missing_vpype(plan, *, timeout_sec=20.0):
        return None, {
            'available': False,
            'warnings': ('vpype is not installed or is not on PATH.',),
        }

    monkeypatch.setattr(web_server.vpype_optimizer, 'optimize_with_vpype', _missing_vpype)
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', _simple_line_art_png(), 'image/png')},
        data={
            'input_type': 'sketch_image',
            'settings_json': (
                '{"preview_geometry_mode":"polyline","max_image_dim":600,'
                '"optimize_stroke_order":true,"path_optimizer":"vpype"}'
            ),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    optimizer = body['metrics']['optimizer']
    assert optimizer['requested'] == 'vpype'
    assert optimizer['used'] == 'internal'
    assert optimizer['available'] is False
    assert optimizer['warnings']
    assert body['primitive_hash']
    assert body['execution_hash']


def test_vpype_optimizer_runs_only_during_preview_not_draw(monkeypatch) -> None:
    client, runtime = _client_and_runtime()
    calls = {'count': 0}

    def _fake_vpype(plan, *, timeout_sec=20.0):
        calls['count'] += 1
        if calls['count'] > 1:
            raise AssertionError('Draw must not rerun vpype')
        return plan, {'available': True, 'warnings': (), 'optimizer': 'vpype'}

    monkeypatch.setattr(web_server.vpype_optimizer, 'optimize_with_vpype', _fake_vpype)
    response = client.post(
        '/api/preview',
        json={
            'input_type': 'text',
            'text': 'VPYPE',
            'placement': {'x': 0.55, 'y': 0.45, 'scale': 0.8},
            'settings': {
                'font_source': 'relief_singleline',
                'path_optimizer': 'vpype',
                'optimize_stroke_order': True,
            },
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['metrics']['optimizer']['requested'] == 'vpype'
    assert preview['metrics']['optimizer']['used'] == 'vpype'
    assert calls['count'] == 1

    runtime.node.active_mode = MODE_TEXT
    draw_response = client.post('/api/draw', json={'preview_id': preview['preview_id']})

    assert draw_response.status_code == 200, draw_response.text
    draw = draw_response.json()
    assert calls['count'] == 1
    assert draw['primitive_hash'] == preview['primitive_hash']
    assert draw['execution_hash'] == preview['execution_hash']
    assert runtime.node.publish_count == 1


def _preview_sketch(client: TestClient) -> dict:
    response = client.post(
        '/api/preview',
        files={'file': ('line.png', _simple_line_art_png(), 'image/png')},
        data={
            'input_type': 'auto',
            'preview_geometry_mode': 'polyline',
            'optimization_preset': 'detail',
            'max_image_dim': '600',
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['preview_id']
    assert body['canonical_hash']
    assert body['pipeline_mode'] == 'sketch_autotrace'
    assert body['metadata']['vectorization_method'] == 'autotrace'
    assert body['primitive_hash']
    assert body['execution_hash']
    return body


def test_svg_preview_draw_uses_same_cached_canonical_hash(monkeypatch) -> None:
    client, runtime = _client_and_runtime()
    preview = _preview_svg(client)

    def _reject_rebuild(*_args, **_kwargs):
        raise AssertionError('draw must use the cached CanonicalPathPlan')

    monkeypatch.setattr(web_server, 'vectorize_svg', _reject_rebuild)

    response = client.post('/api/draw', json={'preview_id': preview['preview_id']})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['published'] is True
    assert body['used_cached_preview_plan'] is True
    assert body['canonical_hash'] == preview['canonical_hash']
    assert body['preview_draw_hash_match'] is True
    assert runtime.node.publish_count == 1


def test_text_preview_draw_uses_same_cached_canonical_hash(monkeypatch) -> None:
    client, runtime = _client_and_runtime()
    preview = _preview_text(client)
    runtime.node.active_mode = MODE_TEXT

    def _reject_rebuild(*_args, **_kwargs):
        raise AssertionError('draw must use the cached text CanonicalPathPlan')

    monkeypatch.setattr(web_server, 'vectorize_text_grouped', _reject_rebuild)

    response = client.post('/api/draw', json={'preview_id': preview['preview_id']})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['source_type'] == 'text'
    assert body['canonical_hash'] == preview['canonical_hash']
    assert body['preview_draw_hash_match'] is True
    assert runtime.node.publish_count == 1


def test_image_preview_draw_uses_same_cached_canonical_hash(monkeypatch) -> None:
    client, runtime = _client_and_runtime()
    preview = _preview_image(client, runtime)

    def _reject_rebuild(*_args, **_kwargs):
        raise AssertionError('draw must use the cached image CanonicalPathPlan')

    monkeypatch.setattr(web_server, 'vectorize_autotrace_image_to_plan', _reject_rebuild)

    response = client.post('/api/draw', json={'preview_id': preview['preview_id']})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['source_type'] == 'sketch_image'
    assert body['canonical_hash'] == preview['canonical_hash']
    assert body['preview_draw_hash_match'] is True
    assert runtime.node.publish_count == 1


def test_sketch_preview_draw_uses_same_cached_canonical_hash(monkeypatch) -> None:
    client, runtime = _client_and_runtime()
    preview = _preview_sketch(client)

    def _reject_rebuild(*_args, **_kwargs):
        raise AssertionError('draw must use the cached sketch CanonicalPathPlan')

    monkeypatch.setattr(web_server, 'vectorize_autotrace_image_to_plan', _reject_rebuild)

    response = client.post('/api/draw', json={'preview_id': preview['preview_id']})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['source_type'] == 'sketch_image'
    assert body['canonical_hash'] == preview['canonical_hash']
    assert body['preview_draw_hash_match'] is True
    assert runtime.node.publish_count == 1



def test_invalid_raster_upload_returns_clear_bad_request() -> None:
    client, _runtime = _client_and_runtime()
    response = client.post(
        '/api/preview',
        files={'file': ('bad.png', b'not-a-real-image', 'image/png')},
        data={
            'input_type': 'auto',
            'settings_json': '{"preview_geometry_mode":"polyline","max_image_dim":600}',
        },
    )

    assert response.status_code == 400
    assert 'Unable to decode uploaded image' in response.json()['detail']


def test_preview_metrics_split_canonical_and_executable_geometry() -> None:
    client, _runtime = _client_and_runtime()
    preview = _preview_sketch(client)
    metrics = preview['metrics']

    assert 'canonical_geometry' in metrics
    assert 'executable_geometry' in metrics
    assert set(metrics['canonical_geometry']).issuperset(
        {'line_count', 'quadratic_count', 'cubic_count', 'arc_count', 'total_curve_count'}
    )
    assert set(metrics['executable_geometry']).issuperset(
        {'draw_path_count', 'sampled_point_count', 'sampled_segment_count'}
    )
    assert metrics['executable_geometry']['sampled_point_count'] == metrics['draw_sample_count']



def test_draw_with_preview_id_uses_cached_svg_without_rebuilding(monkeypatch) -> None:
    client, runtime = _client_and_runtime()
    preview = _preview_svg(client)

    def _reject_rebuild(*_args, **_kwargs):
        raise AssertionError('draw with preview_id must use the cached plan')

    monkeypatch.setattr(web_server, 'vectorize_svg', _reject_rebuild)

    response = client.post('/api/draw', json={'preview_id': preview['preview_id']})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['canonical_hash'] == preview['canonical_hash']
    assert body['preview_draw_hash_match'] is True
    assert runtime.node.publish_count == 1


def test_cached_preview_draw_rejects_missing_and_expired_preview_id(monkeypatch) -> None:
    client, runtime = _client_and_runtime()

    missing = client.post('/api/draw', json={})
    assert missing.status_code == 400

    preview = _preview_svg(client)
    monkeypatch.setattr(web_server, '_PREVIEW_CACHE_TTL_SECONDS', -1)

    expired = client.post('/api/draw', json={'preview_id': preview['preview_id']})

    assert expired.status_code in {404, 410}
    assert 'preview_id' in expired.json()['detail']
    assert runtime.node.publish_count == 0
