"""Endpoint tests for POST /api/draw/live-stroke."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber.runtime_topics import MODE_DRAW, PEN_MODE_AUTO


class _FakeNode:
    def __init__(self) -> None:
        self.publish_count = 0

    def carriage_safe_writable_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

    def publish_execution_plan(self, *_args, **_kwargs) -> dict:
        self.publish_count += 1
        return {'published': 'primitive_path_plan', 'preferred_transport': 'primitive_path_plan'}

    def ensure_ready(self) -> dict:
        return self.runtime_snapshot()

    def runtime_snapshot(self) -> dict:
        return {
            'ready': True,
            'active_mode': MODE_DRAW,
            'manual_pen_mode': PEN_MODE_AUTO,
            'statuses': {'cable_executor_status': 'idle', 'cable_supervisor_status': 'idle'},
            'observed_statuses': {
                'cable_executor_status': True,
                'cable_supervisor_status': True,
            },
        }


class _FakeRuntime:
    def __init__(self) -> None:
        self.node = _FakeNode()
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'


def _client() -> TestClient:
    return TestClient(web_server.create_app(_FakeRuntime()))


def test_live_stroke_rejects_preview_id_while_active() -> None:
    client = _client()
    response = client.post(
        '/api/draw/live-stroke',
        json={
            'preview_id': 'abc123',
            'strokes': [{'draw': True, 'type': 'polyline', 'points': [[1.0, 1.0], [2.0, 2.0]]}],
        },
    )
    assert response.status_code == 409, response.text
    assert 'preview is active' in response.json()['detail']


def test_live_stroke_rejects_empty_drawable_strokes() -> None:
    client = _client()
    response = client.post(
        '/api/draw/live-stroke',
        json={'strokes': [{'draw': False, 'type': 'polyline', 'points': [[1.0, 1.0], [2.0, 2.0]]}]},
    )
    assert response.status_code == 422, response.text
    assert 'no drawable strokes' in response.json()['detail']


def test_live_stroke_rejects_unknown_fields() -> None:
    client = _client()
    response = client.post(
        '/api/draw/live-stroke',
        json={
            'strokes': [{'draw': True, 'type': 'polyline', 'points': [[1.0, 1.0], [2.0, 2.0]]}],
            'unexpected': True,
        },
    )
    assert response.status_code == 422, response.text
    assert 'unsupported fields' in response.json()['detail'].lower()


def test_live_stroke_publishes_valid_stroke() -> None:
    runtime = _FakeRuntime()
    client = TestClient(web_server.create_app(runtime))
    response = client.post(
        '/api/draw/live-stroke',
        json={
            'strokes': [{'draw': True, 'type': 'polyline', 'points': [[1.0, 1.0], [2.0, 2.0], [3.0, 2.5]]}],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['ok'] is True
    assert payload['transport']['published'] == 'primitive_path_plan'
    assert runtime.node.publish_count == 1
