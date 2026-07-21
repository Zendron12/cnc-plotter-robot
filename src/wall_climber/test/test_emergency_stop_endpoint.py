"""Endpoint tests for /api/emergency/stop."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber.runtime_topics import MODE_DRAW, MODE_OFF, MODE_TEXT, PEN_MODE_AUTO


class _FakeNode:
    def __init__(self) -> None:
        self._active_mode = MODE_DRAW
        self.cancel_messages: list[str] = []
        self.mode_messages: list[str] = []

    def runtime_snapshot(self) -> dict:
        return {
            'ready': True,
            'active_mode': self._active_mode,
            'manual_pen_mode': PEN_MODE_AUTO,
            'statuses': {'cable_executor_status': 'running', 'cable_supervisor_status': 'idle'},
            'observed_statuses': {
                'cable_executor_status': True,
                'cable_supervisor_status': True,
            },
        }

    def emergency_stop(self) -> dict:
        self.cancel_messages.append('stop')
        self._active_mode = MODE_OFF
        self.mode_messages.append(MODE_OFF)
        return self.runtime_snapshot()

    def switch_mode(self, mode: str) -> dict:
        if self.runtime_snapshot()['statuses']['cable_executor_status'] == 'running':
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail='runtime is busy; mode switch rejected')
        self._active_mode = mode
        self.mode_messages.append(mode)
        return self.runtime_snapshot()


class _FakeRuntime:
    def __init__(self) -> None:
        self.node = _FakeNode()
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'
        self._text_cursors: dict[str, tuple[float | None, float | None]] = {
            'full': (1.0, 2.0),
        }
        self._text_full_width_bottom_y: float | None = None

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

    def reset_text_cursors(self, column: str | None = None) -> None:
        if column is None:
            self._text_cursors.clear()
            self._text_full_width_bottom_y = None
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

    @property
    def text_cursor(self) -> tuple[float | None, float | None]:
        return self.get_text_cursor('full')


def _client() -> TestClient:
    return TestClient(web_server.create_app(_FakeRuntime()))


def test_emergency_stop_cancels_executor_and_forces_mode_off() -> None:
    runtime = _FakeRuntime()
    runtime.node._active_mode = MODE_DRAW
    client = TestClient(web_server.create_app(runtime))

    response = client.post('/api/emergency/stop')

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['ok'] is True
    assert payload['active_mode'] == MODE_OFF
    assert payload['executor_cancelled'] is True
    assert payload['runtime']['active_mode'] == MODE_OFF
    assert runtime.node.cancel_messages == ['stop']
    assert runtime.text_cursor == (None, None)


def test_emergency_stop_works_while_executor_running_even_if_switch_mode_rejects() -> None:
    runtime = _FakeRuntime()
    runtime.node._active_mode = MODE_TEXT
    client = TestClient(web_server.create_app(runtime))

    busy_response = client.post('/api/mode', json={'mode': MODE_OFF})
    assert busy_response.status_code == 409

    response = client.post('/api/emergency/stop')
    assert response.status_code == 200, response.text
    assert response.json()['active_mode'] == MODE_OFF
