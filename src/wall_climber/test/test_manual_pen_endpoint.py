"""Endpoint tests for POST /api/manual/pen."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber.runtime_topics import MODE_DRAW, PEN_MODE_AUTO, PEN_MODE_DOWN, PEN_MODE_UP


class _FakeNode:
    def __init__(self) -> None:
        self._manual_pen_mode = PEN_MODE_AUTO
        self.published_modes: list[str] = []

    def runtime_snapshot(self) -> dict:
        return {
            'ready': True,
            'active_mode': MODE_DRAW,
            'manual_pen_mode': self._manual_pen_mode,
            'statuses': {'cable_executor_status': 'idle', 'cable_supervisor_status': 'idle'},
            'observed_statuses': {
                'cable_executor_status': True,
                'cable_supervisor_status': True,
            },
        }

    def ensure_ready(self) -> dict:
        return self.runtime_snapshot()

    def set_manual_pen_mode(self, mode: str) -> dict:
        from fastapi import HTTPException

        if mode not in {PEN_MODE_AUTO, PEN_MODE_DOWN, PEN_MODE_UP}:
            raise HTTPException(status_code=400, detail='invalid manual pen mode')
        snapshot = self.ensure_ready()
        if snapshot['statuses']['cable_executor_status'] == 'running':
            raise HTTPException(status_code=409, detail='runtime is busy; manual pen control rejected')
        self._manual_pen_mode = mode
        self.published_modes.append(mode)
        return self.runtime_snapshot()


class _FakeRuntime:
    def __init__(self) -> None:
        self.node = _FakeNode()
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'


def _client() -> TestClient:
    return TestClient(web_server.create_app(_FakeRuntime()))


def test_manual_pen_sets_mode_down() -> None:
    runtime = _FakeRuntime()
    client = TestClient(web_server.create_app(runtime))

    response = client.post('/api/manual/pen', json={'mode': PEN_MODE_DOWN})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['ok'] is True
    assert payload['manual_pen_mode'] == PEN_MODE_DOWN
    assert payload['runtime']['manual_pen_mode'] == PEN_MODE_DOWN
    assert runtime.node.published_modes == [PEN_MODE_DOWN]


def test_manual_pen_rejects_invalid_mode() -> None:
    client = _client()
    response = client.post('/api/manual/pen', json={'mode': 'invalid'})
    assert response.status_code == 422, response.text


def test_manual_pen_rejects_extra_fields() -> None:
    client = _client()
    response = client.post('/api/manual/pen', json={'mode': PEN_MODE_UP, 'extra': True})
    assert response.status_code == 422, response.text
