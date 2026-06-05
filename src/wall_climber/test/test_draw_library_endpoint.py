"""Endpoint tests for /api/draw_library/{identifier}.

Self-contained fake runtime/node (mirrors the sketch draw endpoint fixtures).
Requires httpx/TestClient, so this module is part of the httpx-dependent set
that is excluded in environments without httpx.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber.runtime_topics import MODE_DRAW, PEN_MODE_AUTO


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
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.active_mode = MODE_DRAW
        self.manual_pen_mode = PEN_MODE_AUTO
        self.publish_count = 0

    def runtime_snapshot(self) -> dict:
        return {
            'ready': self.ready,
            'active_mode': self.active_mode,
            'manual_pen_mode': self.manual_pen_mode,
            'statuses': {'cable_executor_status': 'idle'},
            'observed_statuses': {
                'cable_executor_status': True,
                'cable_supervisor_status': True,
            },
        }

    def writable_bounds(self) -> dict[str, float]:
        return {'x_min': 0.0, 'x_max': 6.3, 'y_min': 0.0, 'y_max': 3.0}

    def carriage_safe_writable_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

    def carriage_safe_safe_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}

    def publish_execution_plan(self, primitive_plan, *, allowed_modes):
        if not self.ready:
            raise HTTPException(status_code=503, detail='runtime is not ready')
        if self.active_mode not in allowed_modes:
            raise HTTPException(status_code=409, detail='active mode must be draw')
        self.publish_count += 1
        return {
            'published': 'primitive_path_plan',
            'preferred_transport': 'primitive_path_plan',
            'primitive_transport_published': True,
            'topics': {'primitive_path_plan': '/wall_climber/primitive_path_plan'},
        }


class _FakeRuntime:
    def __init__(self, *, ready: bool = True) -> None:
        self.node = _FakeNode(ready=ready)
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'

    def record_last_plan_debug(self, payload: dict) -> None:
        pass

    def record_last_execution_debug(self, payload: dict) -> None:
        pass

    def record_last_curve_fit_debug(self, _payload: dict) -> None:
        pass

    def set_text_cursor(self, *_args, **_kwargs) -> None:
        pass


@pytest.fixture(autouse=True)
def _fake_ros_messages(monkeypatch):
    monkeypatch.setattr(web_server, 'BoardPoint', _FakeBoardPoint)
    monkeypatch.setattr(web_server, 'PathPrimitive', _FakePathPrimitive)
    monkeypatch.setattr(web_server, 'PrimitivePathPlan', _FakePrimitivePathPlan)


def _client_and_runtime(*, ready: bool = True) -> tuple[TestClient, _FakeRuntime]:
    runtime = _FakeRuntime(ready=ready)
    return TestClient(web_server.create_app(runtime)), runtime


def _library_has_entry_one() -> bool:
    manifest = (
        Path(__file__).resolve().parents[1] / 'assets' / 'draw_library' / 'manifest.json'
    )
    if not manifest.is_file():
        return False
    data = json.loads(manifest.read_text(encoding='utf-8'))
    return any(int(e.get('id', -1)) == 1 for e in data.get('entries', []))


def test_existing_id_draws() -> None:
    if not _library_has_entry_one():
        pytest.skip('draw library entry id=1 not present')
    client, runtime = _client_and_runtime()
    response = client.post('/api/draw_library/1')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['published'] is True
    assert payload['draw_library']['id'] == 1
    assert runtime.node.publish_count == 1


def test_missing_id_returns_404() -> None:
    client, _runtime = _client_and_runtime()
    response = client.post('/api/draw_library/9999')
    assert response.status_code == 404
    assert 'was not found' in response.json()['detail']


def test_busy_runtime_returns_409() -> None:
    if not _library_has_entry_one():
        pytest.skip('draw library entry id=1 not present')
    client, runtime = _client_and_runtime()

    def _busy(*_args, **_kwargs):
        raise HTTPException(status_code=409, detail='cable executor is busy')

    runtime.node.publish_execution_plan = _busy  # type: ignore
    response = client.post('/api/draw_library/1')
    assert response.status_code == 409
